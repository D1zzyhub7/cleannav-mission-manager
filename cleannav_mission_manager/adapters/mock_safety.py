"""Deterministic pure-Python mock of the safety adapter contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from cleannav_mission_manager.adapters.safety import (
    SafetyEvent,
    SafetyEventSink,
    SafetyEventType,
)


class SafetyCallType(Enum):
    """Names of operations recorded by the mock."""

    ACQUIRE_LEASE = 'ACQUIRE_LEASE'
    RELEASE_LEASE = 'RELEASE_LEASE'
    REQUEST_EMERGENCY_STOP = 'REQUEST_EMERGENCY_STOP'
    RESET_EMERGENCY_STOP = 'RESET_EMERGENCY_STOP'


@dataclass(frozen=True)
class SafetyCall:
    """Record one safety operation in call order."""

    operation: SafetyCallType
    execution_id: str | None = None


class MockSafetyAdapter:
    """Record calls and synchronously forward explicitly injected events."""

    def __init__(
        self,
        event_sink: SafetyEventSink,
        *,
        acquire_success: bool = True,
        release_success: bool = True,
        reset_success: bool = True,
    ) -> None:
        """Create a mock with no background activity or automatic events."""
        if not callable(event_sink):
            raise TypeError('event_sink must be callable')
        for name, value in (
            ('acquire_success', acquire_success),
            ('release_success', release_success),
            ('reset_success', reset_success),
        ):
            if type(value) is not bool:
                raise TypeError(f'{name} must be bool')

        self._event_sink = event_sink
        self._acquire_success = acquire_success
        self._release_success = release_success
        self._reset_success = reset_success
        self._calls: list[SafetyCall] = []

    @property
    def calls(self) -> tuple[SafetyCall, ...]:
        """Return an immutable snapshot of all calls in order."""
        return tuple(self._calls)

    def acquire_lease(self, execution_id: str) -> None:
        """Record a lease request without automatically producing an event."""
        self._validate_execution_id(execution_id)
        self._calls.append(
            SafetyCall(
                operation=SafetyCallType.ACQUIRE_LEASE,
                execution_id=execution_id,
            )
        )

    def release_lease(self, execution_id: str) -> None:
        """Record a lease release without automatically producing an event."""
        self._validate_execution_id(execution_id)
        self._calls.append(
            SafetyCall(
                operation=SafetyCallType.RELEASE_LEASE,
                execution_id=execution_id,
            )
        )

    def request_emergency_stop(self) -> None:
        """Record an emergency-stop request without inventing a result event."""
        self._calls.append(
            SafetyCall(operation=SafetyCallType.REQUEST_EMERGENCY_STOP)
        )

    def reset_emergency_stop(self) -> None:
        """Record a software emergency-stop reset request."""
        self._calls.append(
            SafetyCall(operation=SafetyCallType.RESET_EMERGENCY_STOP)
        )

    def inject_event(self, event: SafetyEvent) -> None:
        """Forward exactly one explicitly injected event synchronously."""
        if not isinstance(event, SafetyEvent):
            raise TypeError('event must be SafetyEvent')

        self._event_sink(event)

    def emit_acquire_result(self, execution_id: str) -> None:
        """Emit the configured result for one lease acquisition."""
        self._validate_execution_id(execution_id)
        event_type = (
            SafetyEventType.LEASE_ACQUIRED
            if self._acquire_success
            else SafetyEventType.LEASE_ACQUIRE_FAILED
        )
        self.inject_event(
            SafetyEvent(
                event_type=event_type,
                execution_id=execution_id,
            )
        )

    def emit_release_result(self, execution_id: str) -> None:
        """Emit the configured result for one lease release."""
        self._validate_execution_id(execution_id)
        event_type = (
            SafetyEventType.LEASE_RELEASED
            if self._release_success
            else SafetyEventType.LEASE_RELEASE_FAILED
        )
        self.inject_event(
            SafetyEvent(
                event_type=event_type,
                execution_id=execution_id,
            )
        )

    def emit_reset_result(self) -> None:
        """Emit the configured result for one emergency-stop reset."""
        event_type = (
            SafetyEventType.RESET_EMERGENCY_STOP_SUCCEEDED
            if self._reset_success
            else SafetyEventType.RESET_EMERGENCY_STOP_FAILED
        )
        self.inject_event(SafetyEvent(event_type=event_type))

    @staticmethod
    def _validate_execution_id(execution_id: str) -> None:
        """Reject invalid execution identity values at the public boundary."""
        if not isinstance(execution_id, str):
            raise TypeError('execution_id must be str')
        if execution_id == '':
            raise ValueError('execution_id must not be empty')
