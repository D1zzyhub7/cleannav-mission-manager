"""Pure domain contracts for visual cleaning targets."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
import math


class TargetType(IntEnum):
    """Cleaning target types matching the ROS message numeric contract."""

    UNKNOWN = 0
    LEAF = 1
    LEAF_PILE = 2
    PUDDLE = 3
    BOTTLE_CAN = 4
    PAPER_TRASH = 5
    ANY = 255


class ObservationState(IntEnum):
    """Cleaning target observation lifecycle states."""

    UNKNOWN = 0
    NEW = 1
    CONFIRMED = 2
    LOST = 3
    EXPIRED = 4
    INVALID = 5


class TargetSelectionRule(Enum):
    """Selection policies declared by the Task Catalog."""

    NEAREST_VALID = 'NEAREST_VALID'
    HIGHEST_PRIORITY_THEN_NEAREST = (
        'HIGHEST_PRIORITY_THEN_NEAREST'
    )


@dataclass(frozen=True)
class VisualTargetPolicy:
    """Validated visual target selection and waiting configuration."""

    target_type: TargetType
    selection_rule: TargetSelectionRule
    wait_timeout_sec: float
    completion_radius_m: float

    def __post_init__(self) -> None:
        target_type = self.target_type
        if not isinstance(target_type, TargetType):
            try:
                target_type = TargetType(target_type)
            except (TypeError, ValueError) as exc:
                raise ValueError('unsupported target_type') from exc
            object.__setattr__(self, 'target_type', target_type)

        selection_rule = self.selection_rule
        if not isinstance(selection_rule, TargetSelectionRule):
            try:
                selection_rule = TargetSelectionRule(selection_rule)
            except (TypeError, ValueError) as exc:
                raise ValueError('unsupported selection_rule') from exc
            object.__setattr__(
                self,
                'selection_rule',
                selection_rule,
            )

        for name, value in (
            ('wait_timeout_sec', self.wait_timeout_sec),
            ('completion_radius_m', self.completion_radius_m),
        ):
            if isinstance(value, bool) or not isinstance(
                value,
                (int, float),
            ):
                raise TypeError(f'{name} must be numeric')
            if not math.isfinite(float(value)) or float(value) <= 0.0:
                raise ValueError(f'{name} must be finite and > 0')

    @property
    def wait_timeout_ns(self) -> int:
        """Return the waiting timeout as integer nanoseconds."""
        return int(round(float(self.wait_timeout_sec) * 1_000_000_000))


@dataclass(frozen=True)
class TargetObservation:
    """Immutable, ROS-independent snapshot of one target observation."""

    target_id: str
    source: str
    target_type: TargetType
    stamp_ns: int
    valid_for_ns: int
    confidence: float
    projection_valid: bool
    centroid_x: float
    centroid_y: float
    centroid_z: float
    position_uncertainty_m: float
    observation_state: ObservationState

    def __post_init__(self) -> None:
        if not isinstance(self.target_id, str) or self.target_id == '':
            raise ValueError('target_id must be non-empty')
        if len(self.target_id) > 128:
            raise ValueError('target_id exceeds maximum length 128')
        if not isinstance(self.source, str) or self.source == '':
            raise ValueError('source must be non-empty')

        target_type = self.target_type
        if not isinstance(target_type, TargetType):
            try:
                target_type = TargetType(target_type)
            except (TypeError, ValueError) as exc:
                raise ValueError('unsupported target_type') from exc
            object.__setattr__(self, 'target_type', target_type)

        state = self.observation_state
        if not isinstance(state, ObservationState):
            try:
                state = ObservationState(state)
            except (TypeError, ValueError) as exc:
                raise ValueError('unsupported observation_state') from exc
            object.__setattr__(self, 'observation_state', state)

        if type(self.stamp_ns) is not int or self.stamp_ns < 0:
            raise ValueError('stamp_ns must be a non-negative int')
        if type(self.valid_for_ns) is not int or self.valid_for_ns <= 0:
            raise ValueError('valid_for_ns must be a positive int')
        if type(self.projection_valid) is not bool:
            raise TypeError('projection_valid must be bool')
        for name, value in (
            ('confidence', self.confidence),
            ('centroid_x', self.centroid_x),
            ('centroid_y', self.centroid_y),
            ('centroid_z', self.centroid_z),
            ('position_uncertainty_m', self.position_uncertainty_m),
        ):
            if isinstance(value, bool) or not isinstance(
                value,
                (int, float),
            ) or not math.isfinite(float(value)):
                raise ValueError(f'{name} must be finite')

        if not 0.0 <= float(self.confidence) <= 1.0:
            raise ValueError('confidence must be in [0, 1]')
        if float(self.position_uncertainty_m) < 0.0:
            raise ValueError('position_uncertainty_m must be >= 0')

    @property
    def expiration_ns(self) -> int:
        """Return the absolute expiration timestamp."""
        return self.stamp_ns + self.valid_for_ns

    def is_expired(self, now_ros_ns: int) -> bool:
        """Return whether the observation has reached its TTL."""
        if type(now_ros_ns) is not int or now_ros_ns < 0:
            raise ValueError('now_ros_ns must be a non-negative int')
        return now_ros_ns >= self.expiration_ns


@dataclass(frozen=True)
class RobotPoseSnapshot:
    """Current robot pose in the map frame for nearest-target selection."""

    x: float
    y: float
    z: float
    orientation_x: float
    orientation_y: float
    orientation_z: float
    orientation_w: float
    valid: bool = True

    def __post_init__(self) -> None:
        if type(self.valid) is not bool:
            raise TypeError('valid must be bool')
        for name in (
            'x',
            'y',
            'z',
            'orientation_x',
            'orientation_y',
            'orientation_z',
            'orientation_w',
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(
                value,
                (int, float),
            ) or not math.isfinite(float(value)):
                raise ValueError(f'{name} must be finite')

        norm = sum(
            float(getattr(self, name)) ** 2
            for name in (
                'orientation_x',
                'orientation_y',
                'orientation_z',
                'orientation_w',
            )
        )
        if self.valid and norm <= 0.0:
            raise ValueError('orientation quaternion must be non-zero')
