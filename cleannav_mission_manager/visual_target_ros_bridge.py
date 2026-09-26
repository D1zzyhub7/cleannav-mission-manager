"""ROS bridge for visual target observations and localization pose."""

from __future__ import annotations

import math
from threading import Lock
from typing import Callable

from cleannav_interfaces.msg import CleaningTargetArray
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.qos import qos_profile_sensor_data

from cleannav_mission_manager.domain.target_models import (
    RobotPoseSnapshot,
)
from cleannav_mission_manager.domain.target_registry import TargetRegistry
from cleannav_mission_manager.target_ros_conversion import (
    TargetRosConversionError,
    ingest_cleaning_target_array,
)


LOCALIZATION_TOPIC = '/rtabmap/localization_pose'
MAP_FRAME = 'map'
DEFAULT_TIMER_PERIOD_SEC = 0.1


class PoseValidationError(ValueError):
    """Raised when a localization pose cannot be used for target selection."""


class LatestRobotPoseProvider:
    """Thread-safe cache of the latest valid map-frame localization pose."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._latest: RobotPoseSnapshot | None = None

    def update(self, message: PoseWithCovarianceStamped) -> None:
        """Validate and replace the latest pose, preserving the old one on error."""
        if not isinstance(message, PoseWithCovarianceStamped):
            raise TypeError('message must be PoseWithCovarianceStamped')
        if message.header.frame_id != MAP_FRAME:
            raise PoseValidationError(
                'PoseWithCovarianceStamped header.frame_id must be map'
            )

        position = message.pose.pose.position
        orientation = message.pose.pose.orientation
        values = (
            ('position.x', position.x),
            ('position.y', position.y),
            ('position.z', position.z),
            ('orientation.x', orientation.x),
            ('orientation.y', orientation.y),
            ('orientation.z', orientation.z),
            ('orientation.w', orientation.w),
        )
        for name, value in values:
            if not math.isfinite(float(value)):
                raise PoseValidationError(f'{name} must be finite')

        norm = sum(float(value) ** 2 for _, value in values[3:])
        if not math.isfinite(norm) or norm <= 0.0:
            raise PoseValidationError(
                'orientation quaternion must have a finite non-zero norm'
            )

        snapshot = RobotPoseSnapshot(
            x=position.x,
            y=position.y,
            z=position.z,
            orientation_x=orientation.x,
            orientation_y=orientation.y,
            orientation_z=orientation.z,
            orientation_w=orientation.w,
        )
        with self._lock:
            self._latest = snapshot

    def latest(self) -> RobotPoseSnapshot | None:
        """Return the latest valid snapshot, or None before first valid input."""
        with self._lock:
            return self._latest

    def __call__(self) -> RobotPoseSnapshot | None:
        """Provide the resolver's pose-provider callable contract."""
        return self.latest()


class VisualTargetRosBridge:
    """Connect ROS target/pose callbacks to existing Mission Manager Core APIs."""

    def __init__(
        self,
        *,
        node: object,
        core: object,
        registry: TargetRegistry,
        pose_provider: LatestRobotPoseProvider,
        cleaning_target_topic: str,
        localization_topic: str = LOCALIZATION_TOPIC,
        timer_period_sec: float = DEFAULT_TIMER_PERIOD_SEC,
        on_core_result: Callable[[object], None] | None = None,
    ) -> None:
        if not isinstance(registry, TargetRegistry):
            raise TypeError('registry must be TargetRegistry')
        if not isinstance(pose_provider, LatestRobotPoseProvider):
            raise TypeError(
                'pose_provider must be LatestRobotPoseProvider'
            )
        if not isinstance(cleaning_target_topic, str):
            raise TypeError('cleaning_target_topic must be str')
        if not cleaning_target_topic.strip():
            raise ValueError('cleaning_target_topic must be non-empty')
        if not isinstance(localization_topic, str):
            raise TypeError('localization_topic must be str')
        if not localization_topic.strip():
            raise ValueError('localization_topic must be non-empty')
        if (
            isinstance(timer_period_sec, bool)
            or not isinstance(timer_period_sec, (int, float))
            or not math.isfinite(float(timer_period_sec))
            or float(timer_period_sec) <= 0.0
        ):
            raise ValueError('timer_period_sec must be finite and > 0')
        if not callable(getattr(core, 'retry_waiting_goal_resolution', None)):
            raise TypeError(
                'core must provide retry_waiting_goal_resolution'
            )
        if not callable(getattr(core, 'check_target_wait_timeout', None)):
            raise TypeError('core must provide check_target_wait_timeout')

        self._node = node
        self._core = core
        self._registry = registry
        self._pose_provider = pose_provider
        self.cleaning_target_topic = cleaning_target_topic
        self.localization_topic = localization_topic
        self._on_core_result_callback = on_core_result

        create_subscription = getattr(node, 'create_subscription', None)
        create_timer = getattr(node, 'create_timer', None)
        if not callable(create_subscription) or not callable(create_timer):
            raise TypeError(
                'node must provide create_subscription and create_timer'
            )

        self._target_subscription = create_subscription(
            CleaningTargetArray,
            cleaning_target_topic,
            self._on_cleaning_target_array,
            qos_profile_sensor_data,
        )
        self._localization_subscription = create_subscription(
            PoseWithCovarianceStamped,
            localization_topic,
            self._on_localization_pose,
            qos_profile_sensor_data,
        )
        self._timer = create_timer(
            float(timer_period_sec),
            self._on_timer,
        )

    @property
    def registry(self) -> TargetRegistry:
        """Return the registry shared with the goal resolver."""
        return self._registry

    @property
    def pose_provider(self) -> LatestRobotPoseProvider:
        """Return the pose cache shared with the goal resolver."""
        return self._pose_provider

    def _warn(self, message: str) -> None:
        """Log one callback validation warning without holding the pose lock."""
        self._node.get_logger().warning(message)

    def _on_cleaning_target_array(
        self,
        message: CleaningTargetArray,
    ) -> None:
        """Ingest one target batch and retry a waiting goal."""
        try:
            ingest_cleaning_target_array(message, self._registry)
        except (TargetRosConversionError, TypeError, ValueError) as exc:
            self._warn(f'Ignoring invalid CleaningTargetArray: {exc}')
            return
        self._publish_core_result(
            self._core.retry_waiting_goal_resolution()
        )

    def _on_localization_pose(
        self,
        message: PoseWithCovarianceStamped,
    ) -> None:
        """Cache one valid map pose and retry a waiting goal."""
        try:
            self._pose_provider.update(message)
        except (PoseValidationError, TypeError, ValueError) as exc:
            self._warn(f'Ignoring invalid localization pose: {exc}')
            return
        self._publish_core_result(
            self._core.retry_waiting_goal_resolution()
        )

    def _on_timer(self) -> None:
        """Poll the Core's existing target-wait timeout boundary."""
        self._publish_core_result(
            self._core.check_target_wait_timeout()
        )

    def _publish_core_result(self, result: object) -> None:
        """Forward a Core result to the Node's existing status path."""
        if self._on_core_result_callback is not None:
            self._on_core_result_callback(result)


__all__ = [
    'DEFAULT_TIMER_PERIOD_SEC',
    'LOCALIZATION_TOPIC',
    'LatestRobotPoseProvider',
    'MAP_FRAME',
    'PoseValidationError',
    'VisualTargetRosBridge',
]
