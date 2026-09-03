"""ROS message conversion at the Mission Manager boundary."""

from __future__ import annotations

from cleannav_interfaces.msg import TaskCommand, TaskStatus

from cleannav_mission_manager.domain.command_processing import (
    NormalizedTaskCommand,
)
from cleannav_mission_manager.domain.command_store import (
    StatusSnapshot,
)


_NANOSECONDS_PER_SECOND = 1_000_000_000
_ROS_INT32_MIN = -(2**31)
_ROS_INT32_MAX = 2**31 - 1
_ROS_NANOSECOND_MAX = _NANOSECONDS_PER_SECOND - 1


class RosConversionError(ValueError):
    """Raised when a ROS representation violates its message contract."""


def _ros_time_to_ns(value: object, field_name: str) -> int:
    """Convert ROS Time or Duration parts to integer nanoseconds."""
    try:
        sec = getattr(value, 'sec')
        nanosec = getattr(value, 'nanosec')
    except AttributeError as exc:
        raise RosConversionError(
            f'{field_name} must provide sec and nanosec'
        ) from exc

    if (
        type(sec) is not int
        or not _ROS_INT32_MIN <= sec <= _ROS_INT32_MAX
    ):
        raise RosConversionError(
            f'{field_name}.sec must be a signed 32-bit integer'
        )

    if (
        type(nanosec) is not int
        or not 0 <= nanosec <= _ROS_NANOSECOND_MAX
    ):
        raise RosConversionError(
            f'{field_name}.nanosec must be in [0, 999999999]'
        )

    return sec * _NANOSECONDS_PER_SECOND + nanosec


def _ns_to_ros_time_parts(stamp_ns: int) -> tuple[int, int]:
    """Split non-negative nanoseconds into ROS Time sec/nanosec fields."""
    if type(stamp_ns) is not int or stamp_ns < 0:
        raise RosConversionError('stamp_ns must be a non-negative integer')

    sec, nanosec = divmod(stamp_ns, _NANOSECONDS_PER_SECOND)
    if sec > _ROS_INT32_MAX:
        raise RosConversionError(
            'stamp_ns cannot be represented by ROS Time sec'
        )

    return sec, nanosec


def ros_task_command_to_normalized(
    message: TaskCommand,
) -> NormalizedTaskCommand:
    """Convert one ROS TaskCommand without applying domain business rules."""
    if not isinstance(message, TaskCommand):
        raise TypeError('message must be TaskCommand')

    if message.header.frame_id != '':
        raise RosConversionError(
            'TaskCommand.header.frame_id must be empty'
        )

    return NormalizedTaskCommand(
        interface_version=message.interface_version,
        command_id=message.command_id,
        source=int(message.source),
        task_id=int(message.task_id),
        stamp_ns=_ros_time_to_ns(
            message.header.stamp,
            'header.stamp',
        ),
        valid_for_ns=_ros_time_to_ns(message.valid_for, 'valid_for'),
        confidence=float(message.confidence),
        raw_text=message.raw_text,
    )


def status_snapshot_to_ros(snapshot: StatusSnapshot) -> TaskStatus:
    """Convert one ROS-independent StatusSnapshot to TaskStatus."""
    if not isinstance(snapshot, StatusSnapshot):
        raise TypeError('snapshot must be StatusSnapshot')

    sec, nanosec = _ns_to_ros_time_parts(snapshot.stamp_ns)
    message = TaskStatus()
    message.header.stamp.sec = sec
    message.header.stamp.nanosec = nanosec
    message.header.frame_id = ''
    message.interface_version = snapshot.interface_version
    message.execution_id = snapshot.execution_id
    message.command_id = snapshot.command_id
    message.task_id = snapshot.task_id
    message.status_scope = int(snapshot.status_scope)
    message.state = int(snapshot.state)
    message.progress = snapshot.progress
    message.active_target_id = snapshot.active_target_id
    message.remaining_distance_m = snapshot.remaining_distance_m
    message.reason_code = int(snapshot.reason_code)
    message.message = snapshot.message
    return message


__all__ = [
    'RosConversionError',
    'ros_task_command_to_normalized',
    'status_snapshot_to_ros',
]
