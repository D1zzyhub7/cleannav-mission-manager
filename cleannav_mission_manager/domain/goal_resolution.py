"""Goal resolver result contracts shared by Core and visual selection."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResolvedGoal:
    """A navigation payload frozen for one execution generation."""

    payload: object
    active_target_id: str = ''

    def __post_init__(self) -> None:
        if self.payload is None:
            raise ValueError('ResolvedGoal payload must not be None')
        if not isinstance(self.active_target_id, str):
            raise TypeError('active_target_id must be str')
        if len(self.active_target_id) > 128:
            raise ValueError('active_target_id exceeds maximum length 128')


@dataclass(frozen=True)
class PendingGoal:
    """A mission execution waiting for a usable target."""

    wait_timeout_ns: int

    def __post_init__(self) -> None:
        if type(self.wait_timeout_ns) is not int:
            raise TypeError('wait_timeout_ns must be int')
        if self.wait_timeout_ns <= 0:
            raise ValueError('wait_timeout_ns must be > 0')
