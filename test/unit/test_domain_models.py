"""Unit tests for Mission Manager domain records."""

import math

import pytest

from cleannav_mission_manager.domain.models import (
    CancelIntent,
    CommandRecord,
    CommandRecordState,
    ExecutionRecord,
    InternalExecutionState,
    ManagerMode,
)


def test_command_record_states_match_frozen_contract():
    assert [item.value for item in CommandRecordState] == [
        'RECEIVED',
        'ACCEPTED',
        'REJECTED',
        'QUEUED',
        'APPLIED',
        'TERMINAL',
    ]


def test_manager_modes_match_frozen_contract():
    assert [item.value for item in ManagerMode] == [
        'NORMAL',
        'EMERGENCY_LATCHED',
    ]


def test_internal_execution_states_match_frozen_contract():
    assert [item.value for item in InternalExecutionState] == [
        'IDLE',
        'WAITING_TARGET',
        'PREPARING_GOAL',
        'NAVIGATION_STARTING',
        'LEASE_ACQUIRING',
        'EXECUTING',
        'CANCELING',
        'FINALIZING',
        'PAUSED',
        'SAFETY_BLOCKED',
    ]


def test_cancel_intents_match_frozen_contract():
    assert [item.value for item in CancelIntent] == [
        'PAUSE',
        'STOP',
        'RETURN_HOME',
        'SAFETY_BLOCK',
        'ESTOP',
        'REPLAN',
    ]


def test_command_record_defaults():
    record = CommandRecord(
        command_id='app-001',
        task_id=30,
        source=2,
    )

    assert record.state is CommandRecordState.RECEIVED
    assert record.execution_id == ''
    assert record.reason_code == 0
    assert record.created_ros_time_s == 0.0


def test_command_record_accepts_execution_association():
    record = CommandRecord(
        command_id='app-002',
        task_id=30,
        source=2,
        state=CommandRecordState.ACCEPTED,
        execution_id='exec-001',
        created_ros_time_s=12.5,
    )

    assert record.execution_id == 'exec-001'
    assert record.state is CommandRecordState.ACCEPTED


@pytest.mark.parametrize('command_id', ['', 123])
def test_invalid_command_id_is_rejected(command_id):
    with pytest.raises((TypeError, ValueError)):
        CommandRecord(
            command_id=command_id,
            task_id=30,
            source=2,
        )


@pytest.mark.parametrize('task_id', [0, -1, 65536, True])
def test_invalid_command_task_id_is_rejected(task_id):
    with pytest.raises((TypeError, ValueError)):
        CommandRecord(
            command_id='cmd',
            task_id=task_id,
            source=2,
        )


@pytest.mark.parametrize('source', [-1, 256, True])
def test_invalid_source_is_rejected(source):
    with pytest.raises((TypeError, ValueError)):
        CommandRecord(
            command_id='cmd',
            task_id=30,
            source=source,
        )


@pytest.mark.parametrize(
    'reason_code',
    [-1, 2147483648, True],
)
def test_invalid_reason_code_is_rejected(reason_code):
    with pytest.raises((TypeError, ValueError)):
        CommandRecord(
            command_id='cmd',
            task_id=30,
            source=2,
            reason_code=reason_code,
        )


@pytest.mark.parametrize(
    'created_ros_time_s',
    [-1.0, math.inf, -math.inf, math.nan],
)
def test_invalid_command_ros_time_is_rejected(
    created_ros_time_s,
):
    with pytest.raises(ValueError):
        CommandRecord(
            command_id='cmd',
            task_id=30,
            source=2,
            created_ros_time_s=created_ros_time_s,
        )


def test_execution_record_first_generation_is_one():
    record = ExecutionRecord(
        execution_id='exec-001',
        command_id='app-001',
        task_id=30,
        state=InternalExecutionState.PREPARING_GOAL,
    )

    assert record.generation == 1
    assert record.cancel_intent is None
    assert record.active_target_id == ''


def test_execution_record_can_hold_cancel_intent():
    record = ExecutionRecord(
        execution_id='exec-001',
        command_id='app-001',
        task_id=30,
        state=InternalExecutionState.CANCELING,
        generation=2,
        cancel_intent=CancelIntent.PAUSE,
        active_target_id='leaf-001',
    )

    assert record.generation == 2
    assert record.cancel_intent is CancelIntent.PAUSE
    assert record.active_target_id == 'leaf-001'


@pytest.mark.parametrize('execution_id', ['', 123])
def test_invalid_execution_id_is_rejected(execution_id):
    with pytest.raises((TypeError, ValueError)):
        ExecutionRecord(
            execution_id=execution_id,
            command_id='cmd',
            task_id=30,
            state=InternalExecutionState.IDLE,
        )


@pytest.mark.parametrize('command_id', ['', 123])
def test_invalid_execution_command_id_is_rejected(command_id):
    with pytest.raises((TypeError, ValueError)):
        ExecutionRecord(
            execution_id='exec',
            command_id=command_id,
            task_id=30,
            state=InternalExecutionState.IDLE,
        )


@pytest.mark.parametrize('task_id', [0, -1, 65536, True])
def test_invalid_execution_task_id_is_rejected(task_id):
    with pytest.raises((TypeError, ValueError)):
        ExecutionRecord(
            execution_id='exec',
            command_id='cmd',
            task_id=task_id,
            state=InternalExecutionState.IDLE,
        )


@pytest.mark.parametrize('generation', [0, -1, True])
def test_invalid_generation_is_rejected(generation):
    with pytest.raises((TypeError, ValueError)):
        ExecutionRecord(
            execution_id='exec',
            command_id='cmd',
            task_id=30,
            state=InternalExecutionState.IDLE,
            generation=generation,
        )


def test_invalid_execution_state_is_rejected():
    with pytest.raises(TypeError):
        ExecutionRecord(
            execution_id='exec',
            command_id='cmd',
            task_id=30,
            state='EXECUTING',
        )


def test_invalid_cancel_intent_is_rejected():
    with pytest.raises(TypeError):
        ExecutionRecord(
            execution_id='exec',
            command_id='cmd',
            task_id=30,
            state=InternalExecutionState.CANCELING,
            cancel_intent='PAUSE',
        )
