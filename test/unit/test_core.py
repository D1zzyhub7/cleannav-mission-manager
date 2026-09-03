"""Unit tests for the first Mission Manager Core vertical slice."""

import inspect
import re

import pytest

from cleannav_mission_manager.adapters.mock_navigation import (
    MockNavigationAdapter,
)
from cleannav_mission_manager.adapters.mock_safety import (
    MockSafetyAdapter,
)
from cleannav_mission_manager.adapters.navigation import (
    NavigationEvent,
    NavigationEventType,
)
from cleannav_mission_manager.adapters.safety import (
    SafetyEvent,
    SafetyEventType,
)
from cleannav_mission_manager.core import (
    GoalResolutionError,
    MissionManagerCore,
)
from cleannav_mission_manager.domain.clock import FakeClock
from cleannav_mission_manager.domain.command_processing import (
    CommandReason,
    CommandSource,
    CommandValidator,
    NormalizedTaskCommand,
    TaskCatalogEntry,
    TaskKind,
)
from cleannav_mission_manager.domain.command_store import (
    CommandRecordStore,
    ExternalTaskState,
    TerminalStatusCache,
)
from cleannav_mission_manager.domain.execution_store import (
    ExecutionRecordStore,
)
from cleannav_mission_manager.domain.generation_gate import (
    GenerationGate,
    GenerationHandle,
)
from cleannav_mission_manager.domain.mission_queue import (
    MissionQueue,
)
from cleannav_mission_manager.domain.models import (
    InternalExecutionState,
)


def _catalog():
    return {
        2: TaskCatalogEntry(
            task_id=2,
            name='PAUSE_CURRENT_TASK',
            task_kind=TaskKind.CONTROL,
            enabled=True,
            allowed_sources=frozenset({int(CommandSource.APP)}),
        ),
        3: TaskCatalogEntry(
            task_id=3,
            name='RESUME_CURRENT_TASK',
            task_kind=TaskKind.CONTROL,
            enabled=True,
            allowed_sources=frozenset({int(CommandSource.APP)}),
        ),
        4: TaskCatalogEntry(
            task_id=4,
            name='STOP_CURRENT_TASK',
            task_kind=TaskKind.CONTROL,
            enabled=True,
            allowed_sources=frozenset({int(CommandSource.APP)}),
        ),
        5: TaskCatalogEntry(
            task_id=5,
            name='RETURN_HOME',
            task_kind=TaskKind.CONTROL,
            enabled=False,
            allowed_sources=frozenset({int(CommandSource.APP)}),
        ),
        6: TaskCatalogEntry(
            task_id=6,
            name='SOFTWARE_EMERGENCY_STOP',
            task_kind=TaskKind.CONTROL,
            enabled=True,
            allowed_sources=frozenset({int(CommandSource.APP)}),
        ),
        7: TaskCatalogEntry(
            task_id=7,
            name='RESET_SOFTWARE_EMERGENCY_STOP',
            task_kind=TaskKind.CONTROL,
            enabled=True,
            allowed_sources=frozenset({int(CommandSource.APP)}),
            requires_confirmation=True,
        ),
        30: TaskCatalogEntry(
            task_id=30,
            name='CLEAN_NEAREST_LEAF',
            task_kind=TaskKind.MISSION,
            enabled=True,
            allowed_sources=frozenset({int(CommandSource.APP)}),
        ),
        31: TaskCatalogEntry(
            task_id=31,
            name='CLEAN_SECOND_LEAF',
            task_kind=TaskKind.MISSION,
            enabled=True,
            allowed_sources=frozenset({int(CommandSource.APP)}),
        ),
        32: TaskCatalogEntry(
            task_id=32,
            name='DISABLED_MISSION',
            task_kind=TaskKind.MISSION,
            enabled=False,
            allowed_sources=frozenset({int(CommandSource.APP)}),
        ),
    }


def _command(
    *,
    command_id='command-1',
    task_id=30,
    source=CommandSource.APP,
):
    return NormalizedTaskCommand(
        interface_version='1.0',
        command_id=command_id,
        source=int(source),
        task_id=task_id,
        stamp_ns=1_000_000_000,
        valid_for_ns=10_000_000_000,
        confidence=1.0,
    )


class DeterministicIdFactory:
    """Generate deterministic execution IDs for Core tests."""

    def __init__(self):
        self._counter = 0

    def __call__(self):
        self._counter += 1
        return f'execution-{self._counter}'


def _build(
    *,
    acquire_success=True,
    release_success=True,
    reset_success=True,
):
    holder = {}
    goals = []

    navigation = MockNavigationAdapter(
        lambda event: holder['core'].handle_navigation_event(event)
    )
    safety = MockSafetyAdapter(
        lambda event: holder['core'].handle_safety_event(event),
        acquire_success=acquire_success,
        release_success=release_success,
        reset_success=reset_success,
    )

    def resolve(command, task):
        goals.append((command.command_id, task.task_id))
        return {'task_id': task.task_id}

    core = MissionManagerCore(
        validator=CommandValidator(_catalog()),
        command_store=CommandRecordStore(32),
        mission_queue=MissionQueue(4),
        execution_store=ExecutionRecordStore(
            id_factory=DeterministicIdFactory(),
            terminal_cache=TerminalStatusCache(32),
        ),
        generation_gate=GenerationGate(),
        navigation=navigation,
        safety=safety,
        clock=FakeClock(_ros_time_s=2.0),
        goal_resolver=resolve,
    )
    holder['core'] = core
    return core, navigation, safety, goals


def _start_executing(core, navigation, safety, command=None):
    if command is None:
        command = _command()

    submitted = core.submit_command(command)
    handle = core.active_generation_handle
    assert submitted.accepted
    assert handle is not None

    navigation.inject_event(
        NavigationEvent(
            handle=handle,
            event_type=NavigationEventType.GOAL_ACCEPTED,
        )
    )
    safety.emit_acquire_result(handle.execution_id)
    assert core.active_execution is not None
    assert core.active_execution.state is InternalExecutionState.EXECUTING
    return command, handle


def test_core_can_be_constructed_with_injected_primitives():
    core, _, _, _ = _build()

    assert isinstance(core, MissionManagerCore)
    assert core.active_execution is None
    assert core.queued_missions == ()


def test_valid_enabled_mission_is_accepted_and_activated():
    core, navigation, _, goals = _build()

    result = core.submit_command(_command())

    assert result.accepted
    assert result.reason_code is CommandReason.COMMAND_QUEUED
    assert result.execution_id == 'execution-1'
    assert core.active_execution is not None
    assert core.active_execution.generation == 1
    assert len(core.queued_missions) == 0
    assert goals == [('command-1', 30)]
    assert len(navigation.submitted_calls) == 1
    assert navigation.submitted_calls[0].handle == GenerationHandle(
        execution_id='execution-1',
        generation=1,
    )


@pytest.mark.parametrize(
    ('task_id', 'source', 'reason'),
    [
        (32, CommandSource.APP, CommandReason.TASK_DISABLED),
        (30, CommandSource.VOICE, CommandReason.SOURCE_NOT_ALLOWED),
    ],
)
def test_existing_validator_and_catalog_reject_invalid_mission(
    task_id,
    source,
    reason,
):
    core, navigation, _, goals = _build()

    result = core.submit_command(
        _command(task_id=task_id, source=source)
    )

    assert not result.accepted
    assert result.reason_code is reason
    assert core.active_execution is None
    assert core.queued_missions == ()
    assert navigation.submitted_calls == ()
    assert goals == []


def test_same_command_id_replay_does_not_create_execution_or_navigation():
    core, navigation, _, _ = _build()
    command = _command()

    first = core.submit_command(command)
    replay = core.submit_command(command)

    assert first.execution_id == replay.execution_id == 'execution-1'
    assert replay.replay_status is not None
    assert replay.message == 'idempotent replay'
    assert len(navigation.submitted_calls) == 1
    assert core.active_execution is not None
    assert core.active_execution.execution_id == 'execution-1'


def test_conflicting_duplicate_is_rejected_without_second_execution():
    core, navigation, _, _ = _build()

    core.submit_command(_command())
    conflict = core.submit_command(
        _command(command_id='command-1', task_id=31)
    )

    assert not conflict.accepted
    assert conflict.reason_code is CommandReason.DUPLICATE_COMMAND_CONFLICT
    assert len(navigation.submitted_calls) == 1
    assert core.active_execution is not None
    assert core.active_execution.command_id == 'command-1'


def test_missions_use_fifo_queue_and_keep_one_active_execution():
    core, navigation, safety, goals = _build()

    first = core.submit_command(_command(command_id='command-1'))
    second = core.submit_command(
        _command(command_id='command-2', task_id=31)
    )

    assert first.execution_id == 'execution-1'
    assert second.execution_id is None
    assert [item.command.command_id for item in core.queued_missions] == [
        'command-2',
    ]

    handle = core.active_generation_handle
    assert handle is not None
    navigation.inject_event(
        NavigationEvent(handle, NavigationEventType.GOAL_ACCEPTED)
    )
    safety.emit_acquire_result(handle.execution_id)
    navigation.inject_event(
        NavigationEvent(handle, NavigationEventType.SUCCEEDED)
    )
    safety.emit_release_result(handle.execution_id)

    assert core.active_execution is not None
    assert core.active_execution.execution_id == 'execution-2'
    assert core.active_execution.command_id == 'command-2'
    assert core.active_execution.generation == 1
    assert [goal[0] for goal in goals] == ['command-1', 'command-2']
    assert len(navigation.submitted_calls) == 2


def test_goal_resolver_is_called_once_and_payload_is_opaque_to_core():
    core, navigation, _, goals = _build()

    core.submit_command(_command())

    assert goals == [('command-1', 30)]
    assert navigation.submitted_calls[0].goal_payload == {'task_id': 30}


def test_goal_resolver_none_is_explicitly_rejected_before_navigation_submit():
    core, navigation, _, _ = _build()
    core._goal_resolver = lambda command, task: None

    with pytest.raises(GoalResolutionError):
        core.submit_command(_command())

    assert core.active_execution is None
    assert navigation.submitted_calls == ()


def test_goal_accepted_requests_safety_lease():
    core, navigation, safety, _ = _build()
    _, handle = _start_executing(core, navigation, safety)

    assert safety.calls[0].operation.value == 'ACQUIRE_LEASE'
    assert safety.calls[0].execution_id == handle.execution_id
    assert core.active_execution.state is InternalExecutionState.EXECUTING


def test_navigation_success_releases_lease_before_terminal_success():
    core, navigation, safety, _ = _build()
    command, handle = _start_executing(core, navigation, safety)

    navigation.inject_event(
        NavigationEvent(handle, NavigationEventType.SUCCEEDED)
    )

    assert core.active_execution is not None
    assert core.active_execution.state is InternalExecutionState.FINALIZING
    assert safety.calls[-1].operation.value == 'RELEASE_LEASE'
    assert core.active_execution.execution_id == handle.execution_id

    safety.emit_release_result(handle.execution_id)

    assert core.active_execution is None
    assert core.active_generation_handle is None
    terminal = core._command_store.get_current_command_status(
        command.command_id
    )
    assert terminal is not None
    assert terminal.state is ExternalTaskState.SUCCEEDED


def test_navigation_failure_releases_lease_before_terminal_failure():
    core, navigation, safety, _ = _build()
    command, handle = _start_executing(core, navigation, safety)

    navigation.inject_event(
        NavigationEvent(handle, NavigationEventType.FAILED)
    )
    assert safety.calls[-1].operation.value == 'RELEASE_LEASE'

    safety.emit_release_result(handle.execution_id)

    assert core.active_execution is None
    terminal = core._command_store.get_current_command_status(
        command.command_id
    )
    assert terminal is not None
    assert terminal.state is ExternalTaskState.FAILED


def test_stale_navigation_callback_is_dropped_by_generation_gate():
    core, navigation, safety, _ = _build()
    core.submit_command(_command())
    current = core.active_generation_handle
    assert current is not None
    before = core.active_execution

    stale = NavigationEvent(
        handle=GenerationHandle(
            execution_id=current.execution_id,
            generation=current.generation + 1,
        ),
        event_type=NavigationEventType.SUCCEEDED,
    )
    result = core.handle_navigation_event(stale)

    assert not result.accepted
    assert result.stale
    assert result.reason_code is CommandReason.NAV_STALE_CALLBACK_IGNORED
    assert core.active_execution is before
    assert core.active_execution.state is (
        InternalExecutionState.NAVIGATION_STARTING
    )
    assert len(safety.calls) == 0
    assert len(navigation.submitted_calls) == 1


def test_safety_event_for_other_execution_does_not_pollute_active_execution():
    core, navigation, safety, _ = _build()
    core.submit_command(_command())
    handle = core.active_generation_handle
    assert handle is not None
    navigation.inject_event(
        NavigationEvent(handle, NavigationEventType.GOAL_ACCEPTED)
    )
    before = core.active_execution

    result = core.handle_safety_event(
        SafetyEvent(
            event_type=SafetyEventType.LEASE_ACQUIRED,
            execution_id='other-execution',
        )
    )

    assert not result.accepted
    assert result.reason_code is CommandReason.ACTIVE_EXECUTION_CONFLICT
    assert core.active_execution is before
    assert core.active_execution.state is (
        InternalExecutionState.LEASE_ACQUIRING
    )
    assert len(safety.calls) == 1


def test_pause_cleans_up_navigation_and_lease_then_preserves_execution():
    core, navigation, safety, _ = _build()
    command, handle = _start_executing(core, navigation, safety)
    generation = handle.generation

    result = core.submit_command(
        _command(command_id='pause-1', task_id=2)
    )

    assert result.accepted
    assert core.active_execution is not None
    assert core.active_execution.execution_id == handle.execution_id
    assert core.active_execution.generation == generation
    assert core.active_context.state is InternalExecutionState.CANCELING
    assert len(navigation.cancel_calls) == 1
    assert safety.calls[-1].operation.value == 'RELEASE_LEASE'

    navigation.inject_event(
        NavigationEvent(handle, NavigationEventType.CANCEL_CONFIRMED)
    )
    safety.emit_release_result(handle.execution_id)

    assert core.active_execution is not None
    assert core.active_execution.command_id == command.command_id
    assert core.active_execution.state is InternalExecutionState.PAUSED
    assert core.active_execution.generation == generation


def test_repeated_pause_while_paused_has_no_dangerous_side_effect():
    core, navigation, safety, _ = _build()
    _, handle = _start_executing(core, navigation, safety)
    core.submit_command(_command(command_id='pause-1', task_id=2))
    navigation.inject_event(
        NavigationEvent(handle, NavigationEventType.CANCEL_CONFIRMED)
    )
    safety.emit_release_result(handle.execution_id)
    before_cancel = len(navigation.cancel_calls)
    before_safety = len(safety.calls)

    result = core.submit_command(
        _command(command_id='pause-2', task_id=2)
    )

    assert result.accepted
    assert len(navigation.cancel_calls) == before_cancel
    assert len(safety.calls) == before_safety


def test_resume_preserves_execution_advances_generation_and_resubmits():
    core, navigation, safety, goals = _build()
    _, old_handle = _start_executing(core, navigation, safety)
    core.submit_command(_command(command_id='pause-1', task_id=2))
    navigation.inject_event(
        NavigationEvent(old_handle, NavigationEventType.CANCEL_CONFIRMED)
    )
    safety.emit_release_result(old_handle.execution_id)

    result = core.submit_command(
        _command(command_id='resume-1', task_id=3)
    )

    new_handle = core.active_generation_handle
    assert result.accepted
    assert new_handle is not None
    assert new_handle.execution_id == old_handle.execution_id
    assert new_handle.generation == old_handle.generation + 1
    assert core.active_execution is not None
    assert core.active_execution.execution_id == old_handle.execution_id
    assert core.active_execution.generation == 2
    assert len(navigation.submitted_calls) == 2
    assert navigation.submitted_calls[-1].handle == new_handle
    assert goals == [('command-1', 30), ('command-1', 30)]

    for stale_type in (
        NavigationEventType.GOAL_ACCEPTED,
        NavigationEventType.SUCCEEDED,
        NavigationEventType.FAILED,
        NavigationEventType.CANCEL_CONFIRMED,
        NavigationEventType.CANCEL_FAILED,
    ):
        stale_result = core.handle_navigation_event(
            NavigationEvent(old_handle, stale_type)
        )
        assert not stale_result.accepted
        assert stale_result.stale
    assert core.active_execution.state is (
        InternalExecutionState.NAVIGATION_STARTING
    )
    assert len(safety.calls) == 2

    navigation.inject_event(
        NavigationEvent(new_handle, NavigationEventType.GOAL_ACCEPTED)
    )
    assert safety.calls[-1].operation.value == 'ACQUIRE_LEASE'
    safety.emit_acquire_result(new_handle.execution_id)
    assert core.active_execution.state is InternalExecutionState.EXECUTING


def test_stop_clears_waiting_queue_and_does_not_activate_next_mission():
    core, navigation, safety, _ = _build()
    _, handle = _start_executing(core, navigation, safety)
    core.submit_command(
        _command(command_id='command-2', task_id=31)
    )

    result = core.submit_command(
        _command(command_id='stop-1', task_id=4)
    )

    assert result.accepted
    assert core.queued_missions == ()
    assert len(navigation.cancel_calls) == 1
    assert safety.calls[-1].operation.value == 'RELEASE_LEASE'
    navigation.inject_event(
        NavigationEvent(handle, NavigationEventType.CANCEL_CONFIRMED)
    )
    safety.emit_release_result(handle.execution_id)

    assert core.active_execution is None
    assert len(navigation.submitted_calls) == 1
    terminal = core._command_store.get_current_command_status('command-1')
    assert terminal is not None
    assert terminal.state is ExternalTaskState.CANCELED


def test_estop_clears_queue_requests_safety_and_latches_after_cleanup():
    core, navigation, safety, _ = _build()
    _, handle = _start_executing(core, navigation, safety)
    core.submit_command(
        _command(command_id='command-2', task_id=31)
    )

    result = core.submit_command(
        _command(command_id='estop-1', task_id=6)
    )

    assert result.accepted
    assert core.queued_missions == ()
    assert core.manager_mode.value == 'EMERGENCY_LATCHED'
    assert safety.calls[-2].operation.value == 'REQUEST_EMERGENCY_STOP'
    assert len(navigation.cancel_calls) == 1
    assert safety.calls[-1].operation.value == 'RELEASE_LEASE'

    navigation.inject_event(
        NavigationEvent(handle, NavigationEventType.CANCEL_CONFIRMED)
    )
    safety.emit_release_result(handle.execution_id)

    assert core.active_execution is None
    assert core.manager_mode.value == 'EMERGENCY_LATCHED'
    assert core.active_context.state is InternalExecutionState.IDLE

    replay = core.submit_command(_command(command_id='estop-1', task_id=6))
    assert replay.accepted
    assert len([call for call in safety.calls
                if call.operation.value == 'REQUEST_EMERGENCY_STOP']) == 1


def test_idle_estop_requests_safety_and_latches_without_execution():
    core, _, safety, _ = _build()

    result = core.submit_command(
        _command(command_id='estop-idle', task_id=6)
    )

    assert result.accepted
    assert core.active_execution is None
    assert core.manager_mode.value == 'EMERGENCY_LATCHED'
    assert len(safety.calls) == 1
    assert safety.calls[0].operation.value == 'REQUEST_EMERGENCY_STOP'


def test_reset_waits_for_success_and_returns_to_normal_without_resuming():
    core, navigation, safety, _ = _build()
    _, handle = _start_executing(core, navigation, safety)
    core.submit_command(_command(command_id='pause-1', task_id=2))
    navigation.inject_event(
        NavigationEvent(handle, NavigationEventType.CANCEL_CONFIRMED)
    )
    safety.emit_release_result(handle.execution_id)
    core.submit_command(_command(command_id='estop-1', task_id=6))

    reset = core.submit_command(
        _command(command_id='reset-1', task_id=7)
    )
    assert reset.accepted
    assert core.manager_mode.value == 'EMERGENCY_LATCHED'
    assert core.active_execution is None
    assert safety.calls[-1].operation.value == 'RESET_EMERGENCY_STOP'

    safety.emit_reset_result()

    assert core.manager_mode.value == 'NORMAL'
    assert core.active_execution is None
    assert len(navigation.submitted_calls) == 1


def test_reset_failure_stays_latched_and_replay_does_not_repeat_reset():
    core, _, safety, _ = _build(reset_success=False)
    core.submit_command(_command(command_id='estop-1', task_id=6))
    reset = core.submit_command(
        _command(command_id='reset-1', task_id=7)
    )
    assert reset.accepted
    safety.emit_reset_result()

    assert core.manager_mode.value == 'EMERGENCY_LATCHED'
    assert safety.calls[-1].operation.value == 'RESET_EMERGENCY_STOP'
    terminal = core._command_store.get_current_command_status('reset-1')
    assert terminal is not None
    assert terminal.state is ExternalTaskState.FAILED
    before = len(safety.calls)
    replay = core.submit_command(
        _command(command_id='reset-1', task_id=7)
    )
    assert replay.accepted
    assert len(safety.calls) == before


def test_return_home_is_rejected_by_existing_catalog():
    core, navigation, safety, _ = _build()

    result = core.submit_command(
        _command(command_id='home-1', task_id=5)
    )

    assert not result.accepted
    assert result.reason_code is CommandReason.TASK_DISABLED
    assert navigation.submitted_calls == ()
    assert safety.calls == ()


def test_voice_reset_is_rejected_by_existing_catalog_source_rules():
    core, _, safety, _ = _build()

    result = core.submit_command(
        _command(
            command_id='reset-voice',
            task_id=7,
            source=CommandSource.VOICE,
        )
    )

    assert not result.accepted
    assert result.reason_code is CommandReason.SOURCE_NOT_ALLOWED
    assert safety.calls == ()


def test_control_is_not_queued_and_is_explicitly_unsupported():
    core, navigation, safety, goals = _build()

    result = core.submit_command(_command(task_id=2))

    assert not result.accepted
    assert not result.supported
    assert 'active execution' in result.message
    assert core.queued_missions == ()
    assert core.active_execution is None
    assert navigation.submitted_calls == ()
    assert safety.calls == ()
    assert goals == []


def test_unexpected_reset_safety_event_is_rejected():
    core, _, _, _ = _build()

    result = core.handle_safety_event(
        SafetyEvent(
            event_type=SafetyEventType.RESET_EMERGENCY_STOP_SUCCEEDED
        )
    )

    assert not result.accepted
    assert result.reason_code is CommandReason.ACTIVE_EXECUTION_CONFLICT
    assert 'unexpected' in result.message


def test_core_source_has_no_forbidden_dependencies_or_generation_safety():
    source = inspect.getsource(
        __import__('cleannav_mission_manager.core', fromlist=['core'])
    )

    for forbidden in (
        'rclpy',
        'geometry_msgs',
        'cleannav_interfaces.msg',
        'HTTP',
        'BLE',
        'Ackermann',
        'MPPI',
        'Hybrid',
        'cmd_vel',
        'CAN',
    ):
        assert re.search(
            rf'(?<![A-Za-z0-9_]){re.escape(forbidden)}'
            rf'(?![A-Za-z0-9_])',
            source,
        ) is None

    assert 'SafetyEvent' in source
    assert 'GenerationGate' in source
