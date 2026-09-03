"""Unit tests for the ROS message conversion boundary."""

from dataclasses import fields
import inspect
import re
from types import SimpleNamespace

import pytest

from cleannav_interfaces.msg import TaskCommand, TaskStatus

from cleannav_mission_manager.domain.command_processing import (
    NormalizedTaskCommand,
)
from cleannav_mission_manager.domain.command_store import (
    ExternalTaskState,
    StatusScope,
    StatusSnapshot,
)
from cleannav_mission_manager.ros_conversion import (
    RosConversionError,
    _ros_time_to_ns,
    ros_task_command_to_normalized,
    status_snapshot_to_ros,
)


def _command() -> TaskCommand:
    message = TaskCommand()
    message.header.stamp.sec = 12
    message.header.stamp.nanosec = 345
    message.header.frame_id = ''
    message.interface_version = '1.0'
    message.command_id = 'command-1'
    message.source = TaskCommand.SOURCE_APP
    message.task_id = 42
    message.confidence = 0.75
    message.raw_text = 'clean'
    message.valid_for.sec = 3
    message.valid_for.nanosec = 678
    return message


def _snapshot(scope: StatusScope) -> StatusSnapshot:
    return StatusSnapshot(
        stamp_ns=4 * 1_000_000_000 + 789,
        interface_version='1.0',
        execution_id=(
            'execution-1' if scope is StatusScope.EXECUTION else ''
        ),
        command_id='command-1',
        task_id=42,
        status_scope=scope,
        state=(
            ExternalTaskState.NAVIGATING
            if scope is StatusScope.EXECUTION
            else ExternalTaskState.ACCEPTED
        ),
        progress=0.25,
        active_target_id='target-1',
        remaining_distance_m=2.5,
        reason_code=TaskStatus.REASON_NAVIGATION_ACTIVE,
        message='running',
    )


def test_task_command_conversion_maps_all_fields_and_time_parts():
    result = ros_task_command_to_normalized(_command())

    assert isinstance(result, NormalizedTaskCommand)
    assert result.interface_version == '1.0'
    assert result.command_id == 'command-1'
    assert type(result.source) is int
    assert result.source == TaskCommand.SOURCE_APP
    assert type(result.task_id) is int
    assert result.task_id == 42
    assert result.stamp_ns == 12 * 1_000_000_000 + 345
    assert result.valid_for_ns == 3 * 1_000_000_000 + 678
    assert type(result.confidence) is float
    assert result.confidence == pytest.approx(0.75)
    assert result.raw_text == 'clean'


def test_task_command_zero_and_subsecond_times_are_preserved():
    message = _command()
    message.header.stamp.sec = 0
    message.header.stamp.nanosec = 1
    message.valid_for.sec = 0
    message.valid_for.nanosec = 999

    result = ros_task_command_to_normalized(message)

    assert result.stamp_ns == 1
    assert result.valid_for_ns == 999


def test_task_command_conversion_does_not_apply_domain_validation():
    message = _command()
    message.task_id = 65535
    message.valid_for.sec = 0
    message.valid_for.nanosec = 0

    result = ros_task_command_to_normalized(message)

    assert result.task_id == 65535
    assert result.valid_for_ns == 0


def test_non_empty_task_command_frame_id_is_rejected():
    message = _command()
    message.header.frame_id = 'map'

    with pytest.raises(RosConversionError):
        ros_task_command_to_normalized(message)


@pytest.mark.parametrize('field_name', ['stamp', 'valid_for'])
@pytest.mark.parametrize('nanosec', [-1, 1_000_000_000])
def test_invalid_ros_nanosec_is_rejected(field_name, nanosec):
    value = SimpleNamespace(sec=0, nanosec=nanosec)

    with pytest.raises((ValueError, AssertionError)):
        _ros_time_to_ns(value, field_name)


@pytest.mark.parametrize(
    'scope',
    [StatusScope.COMMAND, StatusScope.EXECUTION],
)
def test_status_snapshot_conversion_maps_scope_and_all_fields(scope):
    snapshot = _snapshot(scope)
    result = status_snapshot_to_ros(snapshot)

    assert isinstance(result, TaskStatus)
    assert result.header.frame_id == ''
    assert result.interface_version == snapshot.interface_version
    assert result.execution_id == snapshot.execution_id
    assert result.command_id == snapshot.command_id
    assert result.task_id == snapshot.task_id
    assert result.status_scope == int(snapshot.status_scope)
    assert result.state == int(snapshot.state)
    assert result.progress == pytest.approx(snapshot.progress)
    assert result.active_target_id == snapshot.active_target_id
    assert result.remaining_distance_m == pytest.approx(
        snapshot.remaining_distance_m
    )
    assert result.reason_code == snapshot.reason_code
    assert result.message == snapshot.message


def test_status_stamp_ns_is_reconstructed_exactly():
    snapshot = _snapshot(StatusScope.EXECUTION)

    result = status_snapshot_to_ros(snapshot)

    assert result.header.stamp.sec == 4
    assert result.header.stamp.nanosec == 789
    assert (
        result.header.stamp.sec * 1_000_000_000
        + result.header.stamp.nanosec
    ) == snapshot.stamp_ns


def test_status_scope_and_state_values_match_ros_contract():
    assert int(StatusScope.UNKNOWN) == TaskStatus.SCOPE_UNKNOWN
    assert int(StatusScope.COMMAND) == TaskStatus.SCOPE_COMMAND
    assert int(StatusScope.EXECUTION) == TaskStatus.SCOPE_EXECUTION

    expected_states = {
        ExternalTaskState.UNKNOWN: TaskStatus.STATE_UNKNOWN,
        ExternalTaskState.IDLE: TaskStatus.STATE_IDLE,
        ExternalTaskState.ACCEPTED: TaskStatus.STATE_ACCEPTED,
        ExternalTaskState.REJECTED: TaskStatus.STATE_REJECTED,
        ExternalTaskState.QUEUED: TaskStatus.STATE_QUEUED,
        ExternalTaskState.WAITING_TARGET: TaskStatus.STATE_WAITING_TARGET,
        ExternalTaskState.PREPARING: TaskStatus.STATE_PREPARING,
        ExternalTaskState.NAVIGATING: TaskStatus.STATE_NAVIGATING,
        ExternalTaskState.PAUSING: TaskStatus.STATE_PAUSING,
        ExternalTaskState.PAUSED: TaskStatus.STATE_PAUSED,
        ExternalTaskState.CANCELING: TaskStatus.STATE_CANCELING,
        ExternalTaskState.RETURNING_HOME: TaskStatus.STATE_RETURNING_HOME,
        ExternalTaskState.SAFETY_BLOCKED: TaskStatus.STATE_SAFETY_BLOCKED,
        ExternalTaskState.SUCCEEDED: TaskStatus.STATE_SUCCEEDED,
        ExternalTaskState.CANCELED: TaskStatus.STATE_CANCELED,
        ExternalTaskState.FAILED: TaskStatus.STATE_FAILED,
        ExternalTaskState.EMERGENCY_STOPPED:
            TaskStatus.STATE_EMERGENCY_STOPPED,
    }
    assert all(
        int(state) == value
        for state, value in expected_states.items()
    )


def test_status_conversion_rejects_unrepresentable_ros_time():
    snapshot = StatusSnapshot(
        stamp_ns=(2**31) * 1_000_000_000,
        interface_version='1.0',
        execution_id='',
        command_id='command-1',
        task_id=42,
        status_scope=StatusScope.COMMAND,
        state=ExternalTaskState.ACCEPTED,
    )

    with pytest.raises(RosConversionError):
        status_snapshot_to_ros(snapshot)


def test_no_robot_status_converter_or_forbidden_runtime_dependency():
    source = inspect.getsource(
        __import__(
            'cleannav_mission_manager.ros_conversion',
            fromlist=['ros_conversion'],
        )
    )

    assert not any(
        name.startswith('robot_status')
        for name in dir(
            __import__(
                'cleannav_mission_manager.ros_conversion',
                fromlist=['ros_conversion'],
            )
        )
    )
    for forbidden in (
        'rclpy',
        'Node',
        'HTTP',
        'BLE',
        'Navigation',
        'Ackermann',
        'Hybrid-A*',
        'MPPI',
        'CAN',
        '/cmd_vel',
    ):
        assert re.search(
            rf'(?<![A-Za-z0-9_]){re.escape(forbidden)}'
            rf'(?![A-Za-z0-9_])',
            source,
        ) is None

    assert {field.name for field in fields(NormalizedTaskCommand)} == {
        'interface_version',
        'command_id',
        'source',
        'task_id',
        'stamp_ns',
        'valid_for_ns',
        'confidence',
        'raw_text',
    }
