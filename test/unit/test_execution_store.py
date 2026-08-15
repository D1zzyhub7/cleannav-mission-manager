"""Unit tests for the Mission Manager Execution Record Store."""

import pytest

from cleannav_interfaces.msg import TaskStatus

from cleannav_mission_manager.domain.command_processing import (
    CommandReason,
    CommandSource,
    NormalizedTaskCommand,
)
from cleannav_mission_manager.domain.command_store import (
    ExternalTaskState,
    StatusScope,
    StatusSnapshot,
    TerminalStatusCache,
)
from cleannav_mission_manager.domain.execution_store import (
    ExecutionRecordStore,
)
from cleannav_mission_manager.domain.models import (
    InternalExecutionState,
)


class DeterministicIdFactory:
    """Generate deterministic execution IDs for tests."""

    def __init__(self) -> None:
        self._counter = 0

    def __call__(self) -> str:
        self._counter += 1
        return f'exec-{self._counter:04d}'


def _command(
    command_id='cmd-1',
    task_id=30,
):
    return NormalizedTaskCommand(
        interface_version='1.0',
        command_id=command_id,
        source=int(CommandSource.APP),
        task_id=task_id,
        stamp_ns=1_000,
        valid_for_ns=10_000,
        confidence=1.0,
    )


def _store(
    *,
    cache_size=8,
    id_factory=None,
):
    return ExecutionRecordStore(
        id_factory=(
            id_factory
            if id_factory is not None
            else DeterministicIdFactory()
        ),
        terminal_cache=TerminalStatusCache(
            cache_size
        ),
    )


def _status(
    *,
    execution_id='exec-0001',
    command_id='cmd-1',
    task_id=30,
    state=ExternalTaskState.NAVIGATING,
    reason_code=12,
    stamp_ns=2_000,
):
    return StatusSnapshot(
        stamp_ns=stamp_ns,
        interface_version='1.0',
        execution_id=execution_id,
        command_id=command_id,
        task_id=task_id,
        status_scope=StatusScope.EXECUTION,
        state=state,
        progress=-1.0,
        remaining_distance_m=-1.0,
        reason_code=reason_code,
        message='test',
    )


def test_execution_reason_codes_match_generated_interface():
    assert int(CommandReason.EXECUTION_ACTIVATED) == (
        TaskStatus.REASON_EXECUTION_ACTIVATED
    )
    assert int(CommandReason.ACTIVE_EXECUTION_CONFLICT) == (
        TaskStatus.REASON_ACTIVE_EXECUTION_CONFLICT
    )
    assert int(CommandReason.EXECUTION_RECORD_CORRUPT) == (
        TaskStatus.REASON_EXECUTION_RECORD_CORRUPT
    )


def test_activation_creates_execution_with_generation_one():
    store = _store()

    result = store.activate(
        _command(),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    assert result.created
    assert result.reason_code is (
        CommandReason.EXECUTION_ACTIVATED
    )
    assert result.record is not None
    assert result.record.execution_id == 'exec-0001'
    assert result.record.command_id == 'cmd-1'
    assert result.record.task_id == 30
    assert result.record.generation == 1
    assert store.active_execution_id == 'exec-0001'
    assert len(store) == 1


def test_execution_id_is_not_created_before_activate():
    factory = DeterministicIdFactory()
    store = _store(id_factory=factory)

    assert store.active_execution_id is None
    assert len(store) == 0

    result = store.activate(
        _command(),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    assert result.record.execution_id == 'exec-0001'


def test_second_active_execution_is_rejected():
    store = _store()

    first = store.activate(
        _command(command_id='cmd-1'),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )
    second = store.activate(
        _command(command_id='cmd-2'),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    assert first.created
    assert not second.created
    assert second.reason_code is (
        CommandReason.ACTIVE_EXECUTION_CONFLICT
    )
    assert second.record is first.record
    assert len(store) == 1


def test_same_command_cannot_create_second_execution():
    store = _store()

    first = store.activate(
        _command(),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    store.write_status(
        _status(
            execution_id=first.record.execution_id,
            state=ExternalTaskState.SUCCEEDED,
            reason_code=18,
        )
    )

    duplicate = store.activate(
        _command(),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    assert not duplicate.created
    assert duplicate.reason_code is (
        CommandReason.EXECUTION_RECORD_CORRUPT
    )
    assert len(store) == 1


def test_generation_advance_preserves_execution_id():
    store = _store()

    created = store.activate(
        _command(),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    execution_id = created.record.execution_id

    advanced = store.advance_generation(
        execution_id
    )

    assert advanced.advanced
    assert advanced.previous_generation == 1
    assert advanced.current_generation == 2
    assert advanced.record.execution_id == execution_id
    assert advanced.record.command_id == 'cmd-1'
    assert store.active_execution_id == execution_id


def test_multiple_resume_cycles_only_advance_generation():
    store = _store()

    created = store.activate(
        _command(),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    execution_id = created.record.execution_id

    second = store.advance_generation(execution_id)
    third = store.advance_generation(execution_id)

    assert second.current_generation == 2
    assert third.current_generation == 3
    assert third.record.execution_id == execution_id
    assert len(store) == 1


def test_nonterminal_status_does_not_release_active_execution():
    store = _store()

    created = store.activate(
        _command(),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    result = store.write_status(
        _status(
            execution_id=created.record.execution_id,
            state=ExternalTaskState.NAVIGATING,
        )
    )

    assert result.written
    assert not store.is_terminal(
        created.record.execution_id
    )
    assert store.active_execution_id == (
        created.record.execution_id
    )


def test_terminal_status_releases_active_slot():
    store = _store()

    created = store.activate(
        _command(),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    execution_id = created.record.execution_id

    result = store.write_status(
        _status(
            execution_id=execution_id,
            state=ExternalTaskState.SUCCEEDED,
            reason_code=18,
        )
    )

    assert result.written
    assert store.is_terminal(execution_id)
    assert store.active_execution_id is None
    assert store.terminal_cache.get_execution(
        execution_id
    ) is result.stored


def test_terminal_execution_cannot_advance_generation():
    store = _store()

    created = store.activate(
        _command(),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    execution_id = created.record.execution_id

    store.write_status(
        _status(
            execution_id=execution_id,
            state=ExternalTaskState.SUCCEEDED,
        )
    )

    result = store.advance_generation(
        execution_id
    )

    assert not result.advanced
    assert result.reason_code is (
        CommandReason.TERMINAL_STATE_ALREADY_WRITTEN
    )
    assert result.current_generation == 1


def test_terminal_execution_cannot_be_updated():
    store = _store()

    created = store.activate(
        _command(),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    execution_id = created.record.execution_id

    store.write_status(
        _status(
            execution_id=execution_id,
            state=ExternalTaskState.SUCCEEDED,
        )
    )

    result = store.update(
        execution_id,
        state=InternalExecutionState.EXECUTING,
    )

    assert not result.updated
    assert result.reason_code is (
        CommandReason.TERMINAL_STATE_ALREADY_WRITTEN
    )


def test_duplicate_terminal_write_preserves_first_terminal():
    store = _store()

    created = store.activate(
        _command(),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    execution_id = created.record.execution_id

    first = _status(
        execution_id=execution_id,
        state=ExternalTaskState.SUCCEEDED,
        reason_code=18,
    )
    second = _status(
        execution_id=execution_id,
        state=ExternalTaskState.FAILED,
        reason_code=406,
        stamp_ns=3_000,
    )

    first_result = store.write_status(first)
    second_result = store.write_status(second)

    assert first_result.written
    assert not second_result.written
    assert second_result.reason_code is (
        CommandReason.TERMINAL_STATE_ALREADY_WRITTEN
    )
    assert second_result.stored is first


def test_new_command_after_terminal_gets_new_execution_id():
    store = _store()

    first = store.activate(
        _command(command_id='cmd-1'),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    store.write_status(
        _status(
            execution_id=first.record.execution_id,
            command_id='cmd-1',
            state=ExternalTaskState.SUCCEEDED,
        )
    )

    second = store.activate(
        _command(command_id='cmd-2'),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    assert second.created
    assert second.record.execution_id == 'exec-0002'
    assert second.record.generation == 1
    assert len(store) == 2


def test_return_home_pattern_is_new_execution_generation_one():
    store = _store()

    mission = store.activate(
        _command(
            command_id='mission-command',
            task_id=30,
        ),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    store.write_status(
        _status(
            execution_id=mission.record.execution_id,
            command_id='mission-command',
            task_id=30,
            state=ExternalTaskState.CANCELED,
            reason_code=20,
        )
    )

    home = store.activate(
        _command(
            command_id='return-home-command',
            task_id=5,
        ),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    assert home.created
    assert home.record.execution_id != (
        mission.record.execution_id
    )
    assert home.record.generation == 1


def test_status_identity_must_match_execution_record():
    store = _store()

    created = store.activate(
        _command(),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    with pytest.raises(ValueError):
        store.write_status(
            _status(
                execution_id=created.record.execution_id,
                command_id='wrong-command',
            )
        )


def test_status_task_id_must_match_execution_record():
    store = _store()

    created = store.activate(
        _command(task_id=30),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    with pytest.raises(ValueError):
        store.write_status(
            _status(
                execution_id=created.record.execution_id,
                task_id=31,
            )
        )


def test_execution_store_rejects_command_scope_status():
    store = _store()

    created = store.activate(
        _command(),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    snapshot = StatusSnapshot(
        stamp_ns=2_000,
        interface_version='1.0',
        execution_id=created.record.execution_id,
        command_id='cmd-1',
        task_id=30,
        status_scope=StatusScope.COMMAND,
        state=ExternalTaskState.SUCCEEDED,
        reason_code=4,
    )

    with pytest.raises(ValueError):
        store.write_status(snapshot)


def test_duplicate_execution_id_from_factory_is_rejected():
    store = _store(
        id_factory=lambda: 'fixed-exec'
    )

    first = store.activate(
        _command(command_id='cmd-1'),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    store.write_status(
        _status(
            execution_id=first.record.execution_id,
            command_id='cmd-1',
            state=ExternalTaskState.SUCCEEDED,
        )
    )

    second = store.activate(
        _command(command_id='cmd-2'),
        initial_state=InternalExecutionState.PREPARING_GOAL,
    )

    assert not second.created
    assert second.reason_code is (
        CommandReason.EXECUTION_RECORD_CORRUPT
    )
    assert second.record is first.record


def test_execution_id_bound_is_enforced():
    store = _store(
        id_factory=lambda: 'x' * 65
    )

    with pytest.raises(ValueError):
        store.activate(
            _command(),
            initial_state=InternalExecutionState.PREPARING_GOAL,
        )


def test_update_can_store_and_clear_target_context():
    store = _store()

    created = store.activate(
        _command(),
        initial_state=InternalExecutionState.WAITING_TARGET,
    )

    execution_id = created.record.execution_id

    updated = store.update(
        execution_id,
        active_target_id='leaf-001',
    )

    assert updated.record.active_target_id == 'leaf-001'

    cleared = store.update(
        execution_id,
        active_target_id='',
    )

    assert cleared.record.active_target_id == ''


def test_unknown_execution_raises_key_error():
    store = _store()

    with pytest.raises(KeyError):
        store.advance_generation('missing-exec')
