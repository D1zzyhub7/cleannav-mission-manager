"""Injectable clocks used by the Mission Manager domain layer."""

from __future__ import annotations

from dataclasses import dataclass
import math


def _validate_non_negative_finite(value: float, name: str) -> float:
    """Return value as float after validating clock input."""
    value = float(value)

    if not math.isfinite(value):
        raise ValueError(f'{name} must be finite')

    if value < 0.0:
        raise ValueError(f'{name} must be non-negative')

    return value


@dataclass
class FakeClock:
    """Deterministic clock for Mission Manager unit tests.

    ROS semantic time and monotonic watchdog time are intentionally
    independent.

    ROS time may be explicitly reset or moved backwards with ``set_ros()``
    so tests can model simulation clock resets.

    Monotonic time has no setter and can only advance.
    """

    _ros_time_s: float = 0.0
    _monotonic_time_s: float = 0.0

    def __post_init__(self) -> None:
        self._ros_time_s = _validate_non_negative_finite(
            self._ros_time_s,
            'ros_time_s',
        )
        self._monotonic_time_s = _validate_non_negative_finite(
            self._monotonic_time_s,
            'monotonic_time_s',
        )

    def now_ros(self) -> float:
        """Return current ROS semantic time in seconds."""
        return self._ros_time_s

    def now_monotonic(self) -> float:
        """Return current monotonic watchdog time in seconds."""
        return self._monotonic_time_s

    def advance_ros(self, seconds: float) -> None:
        """Advance ROS semantic time."""
        seconds = _validate_non_negative_finite(seconds, 'seconds')
        self._ros_time_s += seconds

    def advance_monotonic(self, seconds: float) -> None:
        """Advance monotonic watchdog time."""
        seconds = _validate_non_negative_finite(seconds, 'seconds')
        self._monotonic_time_s += seconds

    def set_ros(self, seconds: float) -> None:
        """Set ROS semantic time explicitly.

        Moving ROS time backwards is intentionally allowed for deterministic
        simulation/reset tests.
        """
        self._ros_time_s = _validate_non_negative_finite(
            seconds,
            'seconds',
        )
