"""Convert CleaningTarget ROS messages into pure domain observations."""

from __future__ import annotations

from cleannav_interfaces.msg import CleaningTarget, CleaningTargetArray

from cleannav_mission_manager.domain.target_models import (
    ObservationState,
    TargetObservation,
)
from cleannav_mission_manager.domain.target_registry import TargetRegistry


SUPPORTED_INTERFACE_VERSION = '1.0'
MAP_FRAME = 'map'


class TargetRosConversionError(ValueError):
    """Raised when a CleaningTarget message violates its ROS contract."""


def _time_to_ns(value: object, name: str) -> int:
    sec = getattr(value, 'sec', None)
    nanosec = getattr(value, 'nanosec', None)
    if type(sec) is not int or type(nanosec) is not int:
        raise TargetRosConversionError(f'{name} must contain integer time')
    if nanosec < 0 or nanosec >= 1_000_000_000:
        raise TargetRosConversionError(f'{name}.nanosec is invalid')
    total = sec * 1_000_000_000 + nanosec
    if total < 0:
        raise TargetRosConversionError(f'{name} must be non-negative')
    return total


def _duration_to_ns(value: object, name: str) -> int:
    return _time_to_ns(value, name)


def cleaning_target_to_observation(
    message: CleaningTarget,
) -> TargetObservation:
    """Convert and validate one CleaningTarget without storing it."""
    if not isinstance(message, CleaningTarget):
        raise TypeError('message must be CleaningTarget')
    if message.interface_version != SUPPORTED_INTERFACE_VERSION:
        raise TargetRosConversionError(
            'CleaningTarget interface_version must be 1.0'
        )
    if message.header.frame_id != MAP_FRAME:
        raise TargetRosConversionError(
            'CleaningTarget header.frame_id must be map'
        )

    try:
        state = ObservationState(message.observation_state)
    except ValueError as exc:
        raise TargetRosConversionError(
            'CleaningTarget observation_state is unsupported'
        ) from exc

    try:
        return TargetObservation(
            target_id=message.target_id,
            source=message.source,
            target_type=message.target_type,
            stamp_ns=_time_to_ns(message.header.stamp, 'header.stamp'),
            valid_for_ns=_duration_to_ns(
                message.valid_for,
                'valid_for',
            ),
            confidence=message.confidence,
            projection_valid=message.projection_valid,
            centroid_x=message.centroid.x,
            centroid_y=message.centroid.y,
            centroid_z=message.centroid.z,
            position_uncertainty_m=message.position_uncertainty_m,
            observation_state=state,
        )
    except (TypeError, ValueError) as exc:
        raise TargetRosConversionError(str(exc)) from exc


def ingest_cleaning_target_array(
    message: CleaningTargetArray,
    registry: TargetRegistry,
) -> tuple[TargetObservation, ...]:
    """Convert one batch and update a registry without defining a topic."""
    if not isinstance(message, CleaningTargetArray):
        raise TypeError('message must be CleaningTargetArray')
    if not isinstance(registry, TargetRegistry):
        raise TypeError('registry must be TargetRegistry')
    if message.interface_version != SUPPORTED_INTERFACE_VERSION:
        raise TargetRosConversionError(
            'CleaningTargetArray interface_version must be 1.0'
        )
    if message.header.frame_id != MAP_FRAME:
        raise TargetRosConversionError(
            'CleaningTargetArray header.frame_id must be map'
        )

    observations = tuple(
        cleaning_target_to_observation(target)
        for target in message.targets
    )
    registry.update_many(list(observations))
    return observations


__all__ = [
    'MAP_FRAME',
    'SUPPORTED_INTERFACE_VERSION',
    'TargetRosConversionError',
    'cleaning_target_to_observation',
    'ingest_cleaning_target_array',
]
