"""Pure target registry with timestamp and TTL semantics."""

from __future__ import annotations

from dataclasses import replace

from cleannav_mission_manager.domain.target_models import (
    ObservationState,
    TargetObservation,
    TargetType,
)


class TargetRegistry:
    """Store the newest observation for each target identity."""

    def __init__(self) -> None:
        self._targets: dict[str, TargetObservation] = {}
        self._latest_stamp_ns: dict[str, int] = {}

    def update(self, observation: TargetObservation) -> bool:
        """Insert a newer selectable or terminal observation."""
        if not isinstance(observation, TargetObservation):
            raise TypeError('observation must be TargetObservation')

        previous_stamp = self._latest_stamp_ns.get(observation.target_id)
        if previous_stamp is not None and observation.stamp_ns <= previous_stamp:
            return False

        self._latest_stamp_ns[observation.target_id] = observation.stamp_ns
        if observation.observation_state in (
            ObservationState.LOST,
            ObservationState.EXPIRED,
            ObservationState.INVALID,
        ):
            self._targets.pop(observation.target_id, None)
            return False

        self._targets[observation.target_id] = observation
        return True

    def update_many(
        self,
        observations: tuple[TargetObservation, ...] | list[TargetObservation],
    ) -> int:
        """Apply a batch without treating an empty batch as a clear."""
        if not isinstance(observations, (tuple, list)):
            raise TypeError('observations must be a tuple or list')
        return sum(self.update(observation) for observation in observations)

    def get(self, target_id: str) -> TargetObservation | None:
        """Return one stored snapshot, including a possibly expired one."""
        return self._targets.get(target_id)

    def snapshots(self) -> tuple[TargetObservation, ...]:
        """Return deterministic target snapshots ordered by target ID."""
        return tuple(
            self._targets[target_id]
            for target_id in sorted(self._targets)
        )

    def available(
        self,
        now_ros_ns: int,
    ) -> tuple[TargetObservation, ...]:
        """Return snapshots that have not reached their TTL."""
        self.expire(now_ros_ns)
        return tuple(
            observation
            for observation in self.snapshots()
            if observation.observation_state in (
                ObservationState.NEW,
                ObservationState.CONFIRMED,
            )
            and observation.projection_valid
            and observation.target_type not in (
                TargetType.UNKNOWN,
                TargetType.ANY,
            )
        )

    def expire(self, now_ros_ns: int) -> tuple[str, ...]:
        """Mark TTL-expired snapshots unavailable without clearing history."""
        if type(now_ros_ns) is not int or now_ros_ns < 0:
            raise ValueError('now_ros_ns must be a non-negative int')

        expired_ids = []
        for target_id, observation in tuple(self._targets.items()):
            if observation.is_expired(now_ros_ns):
                self._targets[target_id] = replace(
                    observation,
                    observation_state=ObservationState.EXPIRED,
                )
                expired_ids.append(target_id)
        return tuple(sorted(expired_ids))


__all__ = [
    'ObservationState',
    'TargetObservation',
    'TargetRegistry',
    'TargetType',
]
