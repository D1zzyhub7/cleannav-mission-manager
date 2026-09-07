"""Goal resolver for catalog-driven visual cleaning missions."""

from __future__ import annotations

from typing import Callable

from geometry_msgs.msg import PoseStamped

from cleannav_mission_manager.domain.goal_resolution import (
    PendingGoal,
    ResolvedGoal,
)
from cleannav_mission_manager.domain.target_models import (
    RobotPoseSnapshot,
)
from cleannav_mission_manager.domain.target_registry import TargetRegistry
from cleannav_mission_manager.domain.target_selector import TargetSelector


RobotPoseProvider = Callable[[], RobotPoseSnapshot | None]
NowRosNs = Callable[[], int]


class TargetResolutionError(RuntimeError):
    """Raised when a non-visual fallback is missing or invalid."""


class VisualTargetGoalResolver:
    """Resolve visual tasks while preserving an explicit pending result."""

    def __init__(
        self,
        *,
        registry: TargetRegistry,
        selector: TargetSelector,
        robot_pose_provider: RobotPoseProvider,
        now_ros_ns: NowRosNs | None = None,
        fallback_resolver: Callable[[object, object], object] | None = None,
        clock: object | None = None,
    ) -> None:
        if not isinstance(registry, TargetRegistry):
            raise TypeError('registry must be TargetRegistry')
        if not isinstance(selector, TargetSelector):
            raise TypeError('selector must be TargetSelector')
        if not callable(robot_pose_provider):
            raise TypeError('robot_pose_provider must be callable')
        if now_ros_ns is None and clock is not None:
            now_ros_ns = getattr(clock, 'now_ros_ns', None)
            if now_ros_ns is None:
                now_ros = getattr(clock, 'now_ros', None)
                if callable(now_ros):
                    def clock_now_ros_ns():
                        return int(
                            round(float(now_ros()) * 1_000_000_000)
                        )

                    now_ros_ns = clock_now_ros_ns
        if not callable(now_ros_ns):
            raise TypeError('now_ros_ns or clock must be callable')
        if (
            fallback_resolver is not None
            and not callable(fallback_resolver)
        ):
            raise TypeError('fallback_resolver must be callable or None')

        self._registry = registry
        self._selector = selector
        self._robot_pose_provider = robot_pose_provider
        self._now_ros_ns = now_ros_ns
        self._fallback_resolver = fallback_resolver

    def __call__(self, command: object, task: object) -> object:
        """Return a frozen goal, a pending wait, or a delegated goal."""
        policy = getattr(task, 'visual_target_policy', None)
        if policy is None:
            if self._fallback_resolver is None:
                raise TargetResolutionError(
                    'non-visual task requires fallback_resolver'
                )
            return self._fallback_resolver(command, task)

        pose = self._robot_pose_provider()
        selected = self._selector.select(
            policy,
            pose,
            self._now_ros_ns(),
        )
        if selected is None:
            return PendingGoal(policy.wait_timeout_ns)

        if pose is None or not pose.valid:
            return PendingGoal(policy.wait_timeout_ns)

        goal = PoseStamped()
        goal.header.frame_id = 'map'
        goal.pose.position.x = selected.centroid_x
        goal.pose.position.y = selected.centroid_y
        goal.pose.position.z = selected.centroid_z
        goal.pose.orientation.x = pose.orientation_x
        goal.pose.orientation.y = pose.orientation_y
        goal.pose.orientation.z = pose.orientation_z
        goal.pose.orientation.w = pose.orientation_w
        return ResolvedGoal(
            payload=goal,
            active_target_id=selected.target_id,
        )


__all__ = [
    'NowRosNs',
    'RobotPoseProvider',
    'TargetResolutionError',
    'VisualTargetGoalResolver',
]
