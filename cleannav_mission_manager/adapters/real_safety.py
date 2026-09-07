"""ROS service and topic transport adapter for Safety Supervisor."""

from __future__ import annotations

from typing import Any

from cleannav_interfaces.srv import SafetyLease
from rclpy.task import Future
from std_msgs.msg import Bool
from std_srvs.srv import Trigger

from cleannav_mission_manager.adapters.safety import (
    SafetyEvent,
    SafetyEventSink,
    SafetyEventType,
)


SAFETY_ACQUIRE_LEASE_SERVICE = '/cleannav/safety/acquire_lease'
SAFETY_RELEASE_LEASE_SERVICE = '/cleannav/safety/release_lease'
SAFETY_RESET_ESTOP_SERVICE = '/cleannav/safety/reset_emergency_stop'
SAFETY_ESTOP_TOPIC = '/cleannav/safety/emergency_stop'


class RealSafetyAdapter:
    """Bridge SafetyAdapter calls to the Safety Supervisor ROS API."""

    def __init__(
        self,
        node: Any,
        event_sink: SafetyEventSink,
        *,
        acquire_client: Any | None = None,
        release_client: Any | None = None,
        reset_client: Any | None = None,
        estop_publisher: Any | None = None,
    ) -> None:
        """Create service clients and the latched emergency-stop publisher."""
        if not callable(event_sink):
            raise TypeError('event_sink must be callable')

        self._event_sink = event_sink
        self._acquire_client = (
            acquire_client
            if acquire_client is not None
            else node.create_client(
                SafetyLease,
                SAFETY_ACQUIRE_LEASE_SERVICE,
            )
        )
        self._release_client = (
            release_client
            if release_client is not None
            else node.create_client(
                SafetyLease,
                SAFETY_RELEASE_LEASE_SERVICE,
            )
        )
        self._reset_client = (
            reset_client
            if reset_client is not None
            else node.create_client(
                Trigger,
                SAFETY_RESET_ESTOP_SERVICE,
            )
        )
        self._estop_publisher = (
            estop_publisher
            if estop_publisher is not None
            else node.create_publisher(Bool, SAFETY_ESTOP_TOPIC, 10)
        )

    def acquire_lease(self, execution_id: str) -> None:
        """Request a lease and preserve this call's execution identity."""
        self._validate_execution_id(execution_id)
        request = SafetyLease.Request()
        request.execution_id = execution_id
        self._call_lease_service(
            self._acquire_client,
            request,
            execution_id,
            SafetyEventType.LEASE_ACQUIRED,
            SafetyEventType.LEASE_ACQUIRE_FAILED,
        )

    def release_lease(self, execution_id: str) -> None:
        """Request lease release without applying ownership policy locally."""
        self._validate_execution_id(execution_id)
        request = SafetyLease.Request()
        request.execution_id = execution_id
        self._call_lease_service(
            self._release_client,
            request,
            execution_id,
            SafetyEventType.LEASE_RELEASED,
            SafetyEventType.LEASE_RELEASE_FAILED,
        )

    def request_emergency_stop(self) -> None:
        """Publish only the latching emergency-stop assertion."""
        message = Bool()
        message.data = True
        self._estop_publisher.publish(message)

    def reset_emergency_stop(self) -> None:
        """Request a reset and translate the Trigger response."""
        if not self._reset_client.service_is_ready():
            self._emit(SafetyEventType.RESET_EMERGENCY_STOP_FAILED)
            return

        try:
            future = self._reset_client.call_async(Trigger.Request())
        except Exception:
            self._emit(SafetyEventType.RESET_EMERGENCY_STOP_FAILED)
            return
        self._register_response_callback(
            future,
            None,
            SafetyEventType.RESET_EMERGENCY_STOP_SUCCEEDED,
            SafetyEventType.RESET_EMERGENCY_STOP_FAILED,
        )

    def _call_lease_service(
        self,
        client: Any,
        request: Any,
        execution_id: str,
        success_event: SafetyEventType,
        failure_event: SafetyEventType,
    ) -> None:
        if not client.service_is_ready():
            self._emit(failure_event, execution_id)
            return

        try:
            future = client.call_async(request)
        except Exception:
            self._emit(failure_event, execution_id)
            return
        self._register_response_callback(
            future,
            execution_id,
            success_event,
            failure_event,
        )

    def _register_response_callback(
        self,
        future: Future | None,
        execution_id: str | None,
        success_event: SafetyEventType,
        failure_event: SafetyEventType,
    ) -> None:
        if future is None:
            self._emit(failure_event, execution_id)
            return
        try:
            future.add_done_callback(
                lambda done_future: self._on_response(
                    done_future,
                    execution_id,
                    success_event,
                    failure_event,
                )
            )
        except Exception:
            self._emit(failure_event, execution_id)

    def _on_response(
        self,
        future: Any,
        execution_id: str | None,
        success_event: SafetyEventType,
        failure_event: SafetyEventType,
    ) -> None:
        try:
            response = future.result()
            success = bool(getattr(response, 'success', False))
        except Exception:
            success = False
        self._emit(
            success_event if success else failure_event,
            execution_id,
        )

    def _emit(
        self,
        event_type: SafetyEventType,
        execution_id: str | None = None,
    ) -> None:
        self._event_sink(
            SafetyEvent(
                event_type=event_type,
                execution_id=execution_id,
            )
        )

    @staticmethod
    def _validate_execution_id(execution_id: str) -> None:
        if not isinstance(execution_id, str):
            raise TypeError('execution_id must be str')
        if execution_id == '':
            raise ValueError('execution_id must not be empty')
