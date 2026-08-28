"""Minimal framework contract for asynchronous navigation adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Protocol

from cleannav_mission_manager.domain.generation_gate import (
    GenerationHandle,
)


class NavigationEventType(Enum):
    """Generic outcomes emitted by a navigation implementation."""

    GOAL_ACCEPTED = 'GOAL_ACCEPTED'
    GOAL_REJECTED = 'GOAL_REJECTED'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'
    CANCEL_CONFIRMED = 'CANCEL_CONFIRMED'
    CANCEL_FAILED = 'CANCEL_FAILED'


@dataclass(frozen=True)
class NavigationEvent:
    """Immutable asynchronous event carrying an opaque payload."""

    handle: GenerationHandle
    event_type: NavigationEventType
    payload: object | None = None

    def __post_init__(self) -> None:
        """Validate only the framework-level event structure."""
        if not isinstance(self.handle, GenerationHandle):
            raise TypeError('handle must be GenerationHandle')
        if not isinstance(self.event_type, NavigationEventType):
            raise TypeError('event_type must be NavigationEventType')


NavigationEventSink = Callable[[NavigationEvent], None]


class NavigationAdapter(Protocol):
    """Structural contract for a navigation adapter implementation."""

    def submit_goal(
        self,
        handle: GenerationHandle,
        goal_payload: object,
    ) -> None:
        """Submit an opaque navigation goal for one generation."""
        ...

    def cancel_goal(self, handle: GenerationHandle) -> None:
        """Request cancellation for one generation."""
        ...
