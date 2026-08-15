"""Unit tests for Command Record Store and terminal status cache."""

import pytest

from cleannav_interfaces.msg import TaskStatus

from cleannav_mission_manager.domain.command_processing import (
    CommandReason,
    CommandSource,
    DedupDecision,
    NormalizedTaskCommand,
)
from cleannav_mission_manager.domain.command_store import (
    CommandRecordStore,
    ExternalTaskState,
    StatusScope,
    StatusSnapshot,
    TerminalStatusCache,
)
from cleannav_mission_manager.domain.models import (
    CommandRecordState,
)


def _command(
    command_id='cmd-1',
    task_id=30,
    source=int(CommandSource.APP),
    valid_for_ns=1_000,
):
    return NormalizedTaskCommand(
        interface_version='1.0',
        command_id=command_id,
        source=source,
        task_id=task_id,
        stamp_ns=1_000,
        valid_for_ns=valid_for_ns,
        confidence=1.0,
    )


def _status(
    *,
    command_id='cmd-1',
    task_id=30,
    scope=StatusScope.COMMAND,
    state=ExternalTaskState.ACCEPTED,
    execution_id='',
    stamp_ns=2_000,
    reason_code=2,
):
    return StatusSnapshot(
        stamp_ns=stamp_ns,
        interface_version='1.0',
        execution_id=execution_id,
        command_id=command_id,
        task_id=task_id,
        status_scope=scope,
        state=state,
        progress=-1.0,
        active_target_id='',
        remaining_distance_m=-1.0,
        reason_code=reason_code,
        message='test',
    )


def test_new_reason_codes_match_generated_interface():
    assert int(CommandReason.COMMAND_RECEIVED) == (
        TaskStatus.REASON_COMMAND_RECEIVED
    )
    assert int(CommandReason.IDEMPOTENT_REPLAY) == (
        TaskStatus.REASON_IDEMPOTENT_REPLAY
    )
    assert int(
        CommandReason.TERMINAL_STATE_ALREADY_WRITTEN
    ) == TaskStatus.REASON_TERMINAL_STATE_ALREADY_WRITTEN
    assert int(CommandReason.TERMINAL_CACHE_MISSING) == (
        TaskStatus.REASON_TERMINAL_CACHE_MISSING
    )
    assert int(CommandReason.COMMAND_RECORD_CORRUPT) == (
        TaskStatus.REASON_COMMAND_RECORD_CORRUPT
    )


def test_scope_values_match_generated_interface():
    assert int(StatusScope.UNKNOWN) == TaskStatus.SCOPE_UNKNOWN
    assert int(StatusScope.COMMAND) == TaskStatus.SCOPE_COMMAND
    assert int(StatusScope.EXECUTION) == (
        TaskStatus.SCOPE_EXECUTION
    )


def test_external_state_values_match_generated_interface():
    expected = [
        TaskStatus.STATE_UNKNOWN,
        TaskStatus.STATE_IDLE,
        TaskStatus.STATE_ACCEPTED,
        TaskStatus.STATE_REJECTED,
        TaskStatus.STATE_QUEUED,
        TaskStatus.STATE_WAITING_TARGET,
        TaskStatus.STATE_PREPARING,
        TaskStatus.STATE_NAVIGATING,
        TaskStatus.STATE_PAUSING,
        TaskStatus.STATE_PAUSED,
        TaskStatus.STATE_CANCELING,
        TaskStatus.STATE_RETURNING_HOME,
        TaskStatus.STATE_SAFETY_BLOCKED,
        TaskStatus.STATE_SUCCEEDED,
        TaskStatus.STATE_CANCELED,
        TaskStatus.STATE_FAILED,
        TaskStatus.STATE_EMERGENCY_STOPPED,
    ]

    assert [
        int(item)
        for item in ExternalTaskState
    ] == expected


def test_execution_scope_requires_execution_id():
    with pytest.raises(ValueError):
        _status(
            scope=StatusScope.EXECUTION,
            state=ExternalTaskState.NAVIGATING,
        )


@pytest.mark.parametrize(
    'progress',
    [-2.0, 1.1],
)
def test_invalid_progress_is_rejected(progress):
    with pytest.raises(ValueError):
        StatusSnapshot(
            stamp_ns=1,
            interface_version='1.0',
            execution_id='',
            command_id='cmd',
            task_id=30,
            status_scope=StatusScope.COMMAND,
            state=ExternalTaskState.ACCEPTED,
            progress=progress,
        )


def test_terminal_cache_capacity_must_be_positive():
    with pytest.raises(ValueError):
        TerminalStatusCache(0)


def test_terminal_cache_rejects_nonterminal_status():
    cache = TerminalStatusCache(2)

    with pytest.raises(ValueError):
        cache.write(_status())


def test_terminal_cache_preserves_first_terminal_write():
    cache = TerminalStatusCache(2)

    first = _status(
        state=ExternalTaskState.SUCCEEDED,
        reason_code=18,
    )
    second = _status(
        state=ExternalTaskState.FAILED,
        reason_code=406,
        stamp_ns=3_000,
    )

    first_result = cache.write(first)
    second_result = cache.write(second)

    assert first_result.written
    assert not second_result.written
    assert second_result.reason_code is (
        CommandReason.TERMINAL_STATE_ALREADY_WRITTEN
    )
    assert second_result.stored is first
    assert cache.get_command('cmd-1') is first


def test_terminal_cache_fifo_eviction_is_deterministic():
    cache = TerminalStatusCache(2)

    first = _status(
        command_id='cmd-1',
        state=ExternalTaskState.SUCCEEDED,
    )
    second = _status(
        command_id='cmd-2',
        state=ExternalTaskState.SUCCEEDED,
    )
    third = _status(
        command_id='cmd-3',
        state=ExternalTaskState.SUCCEEDED,
    )

    cache.write(first)
    cache.write(second)
    result = cache.write(third)

    assert result.evicted is first
    assert cache.get_command('cmd-1') is None
    assert cache.get_command('cmd-2') is second
    assert cache.get_command('cmd-3') is third
    assert cache.snapshot() == (second, third)


def test_command_and_execution_terminal_keys_are_separate():
    cache = TerminalStatusCache(2)

    command_status = _status(
        state=ExternalTaskState.SUCCEEDED,
    )
    execution_status = _status(
        scope=StatusScope.EXECUTION,
        state=ExternalTaskState.SUCCEEDED,
        execution_id='exec-1',
    )

    cache.write(command_status)
    cache.write(execution_status)

    assert cache.get_command('cmd-1') is command_status
    assert cache.get_execution('exec-1') is execution_status


def test_new_command_creates_received_record():
    store = CommandRecordStore(
        terminal_cache_max_entries=4
    )

    result = store.register_or_replay(
        _command(),
        created_ros_time_s=2.0,
        replay_stamp_ns=2_000,
    )

    assert result.decision is DedupDecision.NEW
    assert result.reason_code is CommandReason.COMMAND_RECEIVED
    assert result.record is not None
    assert result.record.state is CommandRecordState.RECEIVED
    assert result.record.reason_code == (
        TaskStatus.REASON_COMMAND_RECEIVED
    )
    assert len(store) == 1


def test_same_semantics_replays_current_command_status():
    store = CommandRecordStore(4)
    command = _command()

    store.register_or_replay(
        command,
        created_ros_time_s=2.0,
        replay_stamp_ns=2_000,
    )
    current = _status(
        state=ExternalTaskState.ACCEPTED,
        reason_code=TaskStatus.REASON_COMMAND_VALID,
    )
    store.write_command_status(current)

    replay = store.register_or_replay(
        _command(
            valid_for_ns=command.valid_for_ns
        ),
        created_ros_time_s=99.0,
        replay_stamp_ns=9_000,
    )

    assert replay.decision is DedupDecision.REPLAY
    assert replay.reason_code is CommandReason.IDEMPOTENT_REPLAY
    assert replay.replay_status is not None
    assert replay.replay_status.state is (
        ExternalTaskState.ACCEPTED
    )
    assert replay.replay_status.stamp_ns == 9_000
    assert replay.replay_status.reason_code == (
        TaskStatus.REASON_IDEMPOTENT_REPLAY
    )
    assert len(store) == 1


def test_replay_metadata_does_not_create_new_record():
    store = CommandRecordStore(4)

    original = _command()

    store.register_or_replay(
        original,
        created_ros_time_s=2.0,
        replay_stamp_ns=2_000,
    )
    store.write_command_status(_status())

    changed_metadata = NormalizedTaskCommand(
        interface_version='1.0',
        command_id='cmd-1',
        source=int(CommandSource.APP),
        task_id=30,
        stamp_ns=99_000,
        valid_for_ns=1_000,
        confidence=0.1,
        raw_text='retry',
    )

    result = store.register_or_replay(
        changed_metadata,
        created_ros_time_s=99.0,
        replay_stamp_ns=100_000,
    )

    assert result.decision is DedupDecision.REPLAY
    assert len(store) == 1


def test_changed_semantics_is_conflict_without_mutation():
    store = CommandRecordStore(4)

    store.register_or_replay(
        _command(),
        created_ros_time_s=2.0,
        replay_stamp_ns=2_000,
    )

    result = store.register_or_replay(
        _command(task_id=31),
        created_ros_time_s=3.0,
        replay_stamp_ns=3_000,
    )

    assert result.decision is DedupDecision.CONFLICT
    assert result.reason_code is (
        CommandReason.DUPLICATE_COMMAND_CONFLICT
    )
    assert len(store) == 1
    assert store.get_record('cmd-1').task_id == 30


def test_terminal_command_replay_uses_terminal_cache():
    store = CommandRecordStore(4)
    command = _command()

    store.register_or_replay(
        command,
        created_ros_time_s=2.0,
        replay_stamp_ns=2_000,
    )

    terminal = _status(
        state=ExternalTaskState.SUCCEEDED,
        reason_code=TaskStatus.REASON_COMMAND_APPLIED,
    )
    store.write_command_status(terminal)

    replay = store.register_or_replay(
        command,
        created_ros_time_s=9.0,
        replay_stamp_ns=9_000,
    )

    assert replay.decision is DedupDecision.REPLAY
    assert replay.replay_status is not None
    assert replay.replay_status.state is (
        ExternalTaskState.SUCCEEDED
    )
    assert replay.replay_status.reason_code == (
        TaskStatus.REASON_IDEMPOTENT_REPLAY
    )


def test_terminal_cache_eviction_never_reexecutes_old_command():
    store = CommandRecordStore(
        terminal_cache_max_entries=1
    )

    first = _command(command_id='cmd-1')
    second = _command(command_id='cmd-2')

    store.register_or_replay(
        first,
        created_ros_time_s=1.0,
        replay_stamp_ns=1_000,
    )
    store.write_command_status(
        _status(
            command_id='cmd-1',
            state=ExternalTaskState.SUCCEEDED,
        )
    )

    store.register_or_replay(
        second,
        created_ros_time_s=2.0,
        replay_stamp_ns=2_000,
    )
    store.write_command_status(
        _status(
            command_id='cmd-2',
            state=ExternalTaskState.SUCCEEDED,
        )
    )

    replay = store.register_or_replay(
        first,
        created_ros_time_s=10.0,
        replay_stamp_ns=10_000,
    )

    assert replay.decision is DedupDecision.REPLAY
    assert replay.reason_code is (
        CommandReason.TERMINAL_CACHE_MISSING
    )
    assert replay.replay_status is None
    assert len(store) == 2


def test_terminal_command_status_cannot_be_overwritten():
    store = CommandRecordStore(4)

    store.register_or_replay(
        _command(),
        created_ros_time_s=1.0,
        replay_stamp_ns=1_000,
    )

    first = _status(
        state=ExternalTaskState.SUCCEEDED,
        reason_code=18,
    )
    second = _status(
        state=ExternalTaskState.FAILED,
        reason_code=406,
        stamp_ns=3_000,
    )

    first_result = store.write_command_status(first)
    second_result = store.write_command_status(second)

    assert first_result.written
    assert not second_result.written
    assert second_result.reason_code is (
        CommandReason.TERMINAL_STATE_ALREADY_WRITTEN
    )
    assert store.get_current_command_status('cmd-1') is first


def test_update_record_refuses_changes_after_terminal():
    store = CommandRecordStore(4)

    store.register_or_replay(
        _command(),
        created_ros_time_s=1.0,
        replay_stamp_ns=1_000,
    )

    result = store.update_record(
        'cmd-1',
        state=CommandRecordState.TERMINAL,
        reason_code=18,
    )

    assert result.updated

    blocked = store.update_record(
        'cmd-1',
        state=CommandRecordState.RECEIVED,
    )

    assert not blocked.updated
    assert blocked.reason_code is (
        CommandReason.TERMINAL_STATE_ALREADY_WRITTEN
    )
    assert blocked.record.state is CommandRecordState.TERMINAL


def test_command_status_task_id_must_match_record():
    store = CommandRecordStore(4)

    store.register_or_replay(
        _command(task_id=30),
        created_ros_time_s=1.0,
        replay_stamp_ns=1_000,
    )

    with pytest.raises(ValueError):
        store.write_command_status(
            _status(task_id=31)
        )


def test_command_store_rejects_execution_scope_as_current_command_status():
    store = CommandRecordStore(4)

    store.register_or_replay(
        _command(),
        created_ros_time_s=1.0,
        replay_stamp_ns=1_000,
    )

    with pytest.raises(ValueError):
        store.write_command_status(
            _status(
                scope=StatusScope.EXECUTION,
                state=ExternalTaskState.SUCCEEDED,
                execution_id='exec-1',
            )
        )
