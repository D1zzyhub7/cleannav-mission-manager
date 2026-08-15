"""Unit tests for the pure FIFO Mission Queue."""

import pytest

from cleannav_interfaces.msg import TaskStatus

from cleannav_mission_manager.domain.command_processing import (
    CommandReason,
    CommandSource,
    NormalizedTaskCommand,
    TaskCatalogEntry,
    TaskKind,
)
from cleannav_mission_manager.domain.mission_queue import (
    MissionQueue,
)


def _mission_task(task_id=30):
    return TaskCatalogEntry(
        task_id=task_id,
        name=f'MISSION_{task_id}',
        task_kind=TaskKind.MISSION,
        enabled=True,
        allowed_sources=frozenset({
            int(CommandSource.VOICE),
            int(CommandSource.APP),
            int(CommandSource.MOCK),
        }),
    )


def _control_task(task_id=2):
    return TaskCatalogEntry(
        task_id=task_id,
        name=f'CONTROL_{task_id}',
        task_kind=TaskKind.CONTROL,
        enabled=True,
        allowed_sources=frozenset({
            int(CommandSource.VOICE),
            int(CommandSource.APP),
            int(CommandSource.MOCK),
        }),
    )


def _command(
    command_id='cmd-1',
    task_id=30,
    stamp_ns=1_000,
    valid_for_ns=1_000,
):
    return NormalizedTaskCommand(
        interface_version='1.0',
        command_id=command_id,
        source=int(CommandSource.APP),
        task_id=task_id,
        stamp_ns=stamp_ns,
        valid_for_ns=valid_for_ns,
        confidence=1.0,
    )


def test_queue_reason_codes_match_generated_interface():
    assert int(CommandReason.COMMAND_QUEUED) == (
        TaskStatus.REASON_COMMAND_QUEUED
    )
    assert int(CommandReason.QUEUED_COMMAND_EXPIRED) == (
        TaskStatus.REASON_QUEUED_COMMAND_EXPIRED
    )
    assert int(CommandReason.QUEUE_FULL) == (
        TaskStatus.REASON_QUEUE_FULL
    )


@pytest.mark.parametrize('max_size', [0, -1])
def test_queue_capacity_must_be_positive(max_size):
    with pytest.raises(ValueError):
        MissionQueue(max_size=max_size)


@pytest.mark.parametrize('max_size', [True, 1.5, '2'])
def test_queue_capacity_must_be_int(max_size):
    with pytest.raises(TypeError):
        MissionQueue(max_size=max_size)


def test_empty_queue_properties():
    queue = MissionQueue(max_size=2)

    assert len(queue) == 0
    assert not queue.is_full
    assert queue.peek() is None
    assert queue.snapshot() == ()


def test_enqueue_returns_command_queued():
    queue = MissionQueue(max_size=2)

    result = queue.enqueue(
        _command(),
        _mission_task(),
    )

    assert result.accepted
    assert result.reason_code is CommandReason.COMMAND_QUEUED
    assert result.item is not None
    assert len(queue) == 1


def test_fifo_order_is_preserved():
    queue = MissionQueue(max_size=3)

    queue.enqueue(
        _command(command_id='cmd-1'),
        _mission_task(),
    )
    queue.enqueue(
        _command(command_id='cmd-2'),
        _mission_task(),
    )
    queue.enqueue(
        _command(command_id='cmd-3'),
        _mission_task(),
    )

    first = queue.pop_next(
        now_ros_ns=1_500,
    )
    second = queue.pop_next(
        now_ros_ns=1_500,
    )
    third = queue.pop_next(
        now_ros_ns=1_500,
    )

    assert first.item.command.command_id == 'cmd-1'
    assert second.item.command.command_id == 'cmd-2'
    assert third.item.command.command_id == 'cmd-3'


def test_queue_full_rejects_without_mutation():
    queue = MissionQueue(max_size=1)

    first = queue.enqueue(
        _command(command_id='cmd-1'),
        _mission_task(),
    )
    second = queue.enqueue(
        _command(command_id='cmd-2'),
        _mission_task(),
    )

    assert first.accepted
    assert not second.accepted
    assert second.reason_code is CommandReason.QUEUE_FULL
    assert second.item is None

    assert len(queue) == 1
    assert queue.peek().command.command_id == 'cmd-1'


def test_control_command_cannot_enter_mission_queue():
    queue = MissionQueue(max_size=2)

    with pytest.raises(ValueError):
        queue.enqueue(
            _command(task_id=2),
            _control_task(task_id=2),
        )


def test_command_and_catalog_task_id_must_match():
    queue = MissionQueue(max_size=2)

    with pytest.raises(ValueError):
        queue.enqueue(
            _command(task_id=30),
            _mission_task(task_id=31),
        )


def test_exact_expiry_boundary_remains_valid():
    queue = MissionQueue(max_size=2)

    queue.enqueue(
        _command(
            stamp_ns=1_000,
            valid_for_ns=500,
        ),
        _mission_task(),
    )

    expired = queue.expire(
        now_ros_ns=1_500,
    )

    assert expired == ()
    assert len(queue) == 1


def test_item_after_expiry_boundary_is_removed():
    queue = MissionQueue(max_size=2)

    queue.enqueue(
        _command(
            stamp_ns=1_000,
            valid_for_ns=500,
        ),
        _mission_task(),
    )

    expired = queue.expire(
        now_ros_ns=1_501,
    )

    assert len(expired) == 1
    assert expired[0].reason_code is (
        CommandReason.QUEUED_COMMAND_EXPIRED
    )
    assert expired[0].item.command.command_id == 'cmd-1'
    assert len(queue) == 0


def test_expire_removes_middle_items_and_preserves_fifo():
    queue = MissionQueue(max_size=4)

    queue.enqueue(
        _command(
            command_id='valid-1',
            stamp_ns=1_000,
            valid_for_ns=10_000,
        ),
        _mission_task(),
    )
    queue.enqueue(
        _command(
            command_id='expired',
            stamp_ns=1_000,
            valid_for_ns=100,
        ),
        _mission_task(),
    )
    queue.enqueue(
        _command(
            command_id='valid-2',
            stamp_ns=1_000,
            valid_for_ns=10_000,
        ),
        _mission_task(),
    )

    expired = queue.expire(
        now_ros_ns=2_000,
    )

    assert [
        item.item.command.command_id
        for item in expired
    ] == ['expired']

    assert [
        item.command.command_id
        for item in queue.snapshot()
    ] == ['valid-1', 'valid-2']


def test_pop_next_never_activates_expired_command():
    queue = MissionQueue(max_size=3)

    queue.enqueue(
        _command(
            command_id='expired',
            stamp_ns=1_000,
            valid_for_ns=100,
        ),
        _mission_task(),
    )
    queue.enqueue(
        _command(
            command_id='valid',
            stamp_ns=1_000,
            valid_for_ns=10_000,
        ),
        _mission_task(),
    )

    result = queue.pop_next(
        now_ros_ns=2_000,
    )

    assert result.item is not None
    assert result.item.command.command_id == 'valid'

    assert len(result.expired) == 1
    assert result.expired[0].item.command.command_id == (
        'expired'
    )
    assert result.expired[0].reason_code is (
        CommandReason.QUEUED_COMMAND_EXPIRED
    )


def test_all_expired_queue_returns_no_activation():
    queue = MissionQueue(max_size=2)

    queue.enqueue(
        _command(
            command_id='expired-1',
            stamp_ns=1_000,
            valid_for_ns=100,
        ),
        _mission_task(),
    )
    queue.enqueue(
        _command(
            command_id='expired-2',
            stamp_ns=1_000,
            valid_for_ns=200,
        ),
        _mission_task(),
    )

    result = queue.pop_next(
        now_ros_ns=2_000,
    )

    assert result.item is None
    assert len(result.expired) == 2
    assert len(queue) == 0


def test_clear_returns_items_in_fifo_order():
    queue = MissionQueue(max_size=3)

    for command_id in ('cmd-1', 'cmd-2', 'cmd-3'):
        queue.enqueue(
            _command(command_id=command_id),
            _mission_task(),
        )

    removed = queue.clear()

    assert [
        item.command.command_id
        for item in removed
    ] == ['cmd-1', 'cmd-2', 'cmd-3']
    assert len(queue) == 0


def test_snapshot_is_immutable_tuple():
    queue = MissionQueue(max_size=2)
    queue.enqueue(
        _command(),
        _mission_task(),
    )

    snapshot = queue.snapshot()

    assert isinstance(snapshot, tuple)
    assert snapshot[0] is queue.peek()


@pytest.mark.parametrize('now_ros_ns', [-1])
def test_negative_ros_time_is_rejected(now_ros_ns):
    queue = MissionQueue(max_size=1)

    with pytest.raises(ValueError):
        queue.expire(
            now_ros_ns=now_ros_ns,
        )


@pytest.mark.parametrize(
    'now_ros_ns',
    [True, 1.5, '100'],
)
def test_non_integer_ros_time_is_rejected(now_ros_ns):
    queue = MissionQueue(max_size=1)

    with pytest.raises(TypeError):
        queue.expire(
            now_ros_ns=now_ros_ns,
        )
