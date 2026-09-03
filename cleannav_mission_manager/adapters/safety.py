"""Minimal framework contract for asynchronous safety adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol


class SafetyEventType(Enum):
    """Generic outcomes emitted by a safety implementation."""

    LEASE_ACQUIRED = 'LEASE_ACQUIRED'
    LEASE_ACQUIRE_FAILED = 'LEASE_ACQUIRE_FAILED'
    LEASE_RELEASED = 'LEASE_RELEASED'
    LEASE_RELEASE_FAILED = 'LEASE_RELEASE_FAILED'
    RESET_EMERGENCY_STOP_SUCCEEDED = 'RESET_EMERGENCY_STOP_SUCCEEDED'
    RESET_EMERGENCY_STOP_FAILED = 'RESET_EMERGENCY_STOP_FAILED'


@dataclass(frozen=True)
class SafetyEvent:
    """Immutable asynchronous result for one safety operation."""

    event_type: SafetyEventType
    execution_id: str | None = None

    def __post_init__(self) -> None:
        """Validate the event shape without applying business policy."""
        if not isinstance(self.event_type, SafetyEventType):
            raise TypeError('event_type must be SafetyEventType')

        lease_events = (
            SafetyEventType.LEASE_ACQUIRED,
            SafetyEventType.LEASE_ACQUIRE_FAILED,
            SafetyEventType.LEASE_RELEASED,
            SafetyEventType.LEASE_RELEASE_FAILED,
        )
        if self.event_type in lease_events:
            if not isinstance(self.execution_id, str):
                raise TypeError('lease event execution_id must be str')
            if self.execution_id == '':
                raise ValueError('lease event execution_id must not be empty')
        elif self.execution_id is not None:
            raise ValueError(
                'reset emergency stop event must not have execution_id'
            )


SafetyEventSink = Callable[[SafetyEvent], None]


class SafetyAdapter(Protocol):
    """Structural contract for a safety adapter implementation."""

    def acquire_lease(self, execution_id: str) -> None:
        """Request exclusive safety ownership for one execution."""
        ...

    def release_lease(self, execution_id: str) -> None:
        """Request release of safety ownership for one execution."""
        ...

    def request_emergency_stop(self) -> None:
        """Request the safety emergency-stop action."""
        ...

    def reset_emergency_stop(self) -> None:
        """Request clearing the software emergency-stop condition."""
        ...
