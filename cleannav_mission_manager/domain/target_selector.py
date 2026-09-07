"""Deterministic pure target selection policies."""

from __future__ import annotations

import math

from cleannav_mission_manager.domain.target_models import (
    RobotPoseSnapshot,
    TargetObservation,
    TargetSelectionRule,
    TargetType,
    VisualTargetPolicy,
)
from cleannav_mission_manager.domain.target_registry import TargetRegistry


class UnsupportedTargetSelectionError(RuntimeError):
    """Raised when a policy requires data not present in CleaningTarget V1."""


class TargetSelector:
    """Select one target using only explicit catalog policy and geometry."""

    def __init__(self, registry: TargetRegistry) -> None:
        if not isinstance(registry, TargetRegistry):
            raise TypeError('registry must be TargetRegistry')
        self._registry = registry

    def select(
        self,
        policy: VisualTargetPolicy,
        robot_pose: RobotPoseSnapshot | None,
        now_ros_ns: int,
    ) -> TargetObservation | None:
        """Return the nearest valid candidate or None when unresolved."""
        if not isinstance(policy, VisualTargetPolicy):
            raise TypeError('policy must be VisualTargetPolicy')
        if policy.selection_rule is not TargetSelectionRule.NEAREST_VALID:
            raise UnsupportedTargetSelectionError(
                'priority selection is unsupported without a priority field'
            )
        if robot_pose is None or not robot_pose.valid:
            return None

        candidates = []
        for observation in self._registry.available(now_ros_ns):
            if policy.target_type is TargetType.ANY:
                matches_type = observation.target_type is not TargetType.UNKNOWN
            else:
                matches_type = observation.target_type is policy.target_type
            if matches_type:
                candidates.append(observation)

        selected: TargetObservation | None = None
        selected_distance: float | None = None
        for observation in candidates:
            dx = float(observation.centroid_x) - float(robot_pose.x)
            dy = float(observation.centroid_y) - float(robot_pose.y)
            distance_squared = dx * dx + dy * dy
            if selected is None or selected_distance is None:
                selected = observation
                selected_distance = distance_squared
                continue

            equivalent = math.isclose(
                distance_squared,
                selected_distance,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            if (
                distance_squared < selected_distance
                and not equivalent
            ) or (
                equivalent
                and observation.target_id < selected.target_id
            ):
                selected = observation
                selected_distance = distance_squared

        return selected


__all__ = [
    'RobotPoseSnapshot',
    'TargetSelector',
    'TargetSelectionRule',
    'TargetType',
    'UnsupportedTargetSelectionError',
]
