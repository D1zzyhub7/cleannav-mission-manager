"""Deterministic pure-Python mock of the navigation adapter contract."""

from __future__ import annotations

from dataclasses import dataclass

from cleannav_mission_manager.adapters.navigation import (
    NavigationEvent,
    NavigationEventSink,
)
from cleannav_mission_manager.domain.generation_gate import (
    GenerationHandle,
)


@dataclass(frozen=True)
class NavigationSubmitCall:
    """Record one submitted opaque goal without interpreting it."""

    handle: GenerationHandle
    goal_payload: object


@dataclass(frozen=True)
class NavigationCancelCall:
    """Record one navigation cancellation request."""

    handle: GenerationHandle


class MockNavigationAdapter:
    """Record calls and synchronously forward explicitly injected events."""

    def __init__(self, event_sink: NavigationEventSink) -> None:
        """Create a mock with no background activity or automatic events."""
        if not callable(event_sink):
            raise TypeError('event_sink must be callable')

        self._event_sink = event_sink
        self._submitted_calls: list[NavigationSubmitCall] = []
        self._cancel_calls: list[NavigationCancelCall] = []

    @property
    def submitted_calls(self) -> tuple[NavigationSubmitCall, ...]:
        """Return an immutable snapshot of submitted goal calls."""
        return tuple(self._submitted_calls)

    @property
    def cancel_calls(self) -> tuple[NavigationCancelCall, ...]:
        """Return an immutable snapshot of cancellation calls."""
        return tuple(self._cancel_calls)

    def submit_goal(
        self,
        handle: GenerationHandle,
        goal_payload: object,
    ) -> None:
        """Record a goal call while preserving the opaque payload object."""
        self._validate_handle(handle)
        self._submitted_calls.append(
            NavigationSubmitCall(
                handle=handle,
                goal_payload=goal_payload,
            )
        )

    def cancel_goal(self, handle: GenerationHandle) -> None:
        """Record a cancellation call without applying business policy."""
        self._validate_handle(handle)
        self._cancel_calls.append(NavigationCancelCall(handle=handle))

    def inject_event(self, event: NavigationEvent) -> None:
        """Forward exactly one explicit navigation event synchronously."""
        if not isinstance(event, NavigationEvent):
            raise TypeError('event must be NavigationEvent')

        self._event_sink(event)

    @staticmethod
    def _validate_handle(handle: GenerationHandle) -> None:
        """Reject invalid generation identity types at the public boundary."""
        if not isinstance(handle, GenerationHandle):
            raise TypeError('handle must be GenerationHandle')
