"""ROS Action transport adapter for the CleanNav navigation facade."""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass
from typing import Any

from action_msgs.msg import GoalStatus
from action_msgs.srv import CancelGoal
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.task import Future

from cleannav_mission_manager.adapters.navigation import (
    NavigationEvent,
    NavigationEventSink,
    NavigationEventType,
)
from cleannav_mission_manager.domain.generation_gate import (
    GenerationHandle,
)


NAVIGATION_ACTION_NAME = '/cleannav/navigate_to_pose'


@dataclass
class _NavigationGoalRecord:
    """Track one ROS goal lifecycle without applying manager policy."""

    handle: GenerationHandle
    send_future: Future | None = None
    goal_handle: Any | None = None
    result_future: Future | None = None
    cancel_requested: bool = False
    cancel_started: bool = False
    cancel_future: Future | None = None
    cancel_response_received: bool = False
    cancel_response_valid: bool = False
    cancel_response: Any | None = None
    result_response: Any | None = None
    result_status: int | None = None
    terminal_emitted: bool = False


class RealNavigationAdapter:
    """Bridge one generation to the Navigation Facade ROS action."""

    def __init__(
        self,
        node: Any,
        event_sink: NavigationEventSink,
        *,
        action_name: str = NAVIGATION_ACTION_NAME,
        action_client: Any | None = None,
    ) -> None:
        """Create a non-blocking adapter with an injectable ActionClient."""
        if not callable(event_sink):
            raise TypeError('event_sink must be callable')
        if not isinstance(action_name, str):
            raise TypeError('action_name must be str')
        if action_name == '':
            raise ValueError('action_name must not be empty')

        self._event_sink = event_sink
        self._action_client = (
            action_client
            if action_client is not None
            else ActionClient(node, NavigateToPose, action_name)
        )
        self._lock = threading.Lock()
        self._records: dict[GenerationHandle, _NavigationGoalRecord] = {}

    def submit_goal(
        self,
        handle: GenerationHandle,
        goal_payload: object,
    ) -> None:
        """Submit a copied PoseStamped through the ROS action client."""
        self._validate_handle(handle)
        if not isinstance(goal_payload, PoseStamped):
            raise TypeError('goal_payload must be PoseStamped')

        goal = NavigateToPose.Goal()
        goal.pose = copy.deepcopy(goal_payload)
        goal.behavior_tree = ''

        record = _NavigationGoalRecord(handle=handle)
        with self._lock:
            self._records[handle] = record

        try:
            if not self._action_client.server_is_ready():
                self._emit_terminal(record, NavigationEventType.GOAL_REJECTED)
                return
            send_future = self._action_client.send_goal_async(goal)
        except Exception:
            self._emit_terminal(record, NavigationEventType.GOAL_REJECTED)
            return

        with self._lock:
            record.send_future = send_future

        if send_future is None:
            self._emit_terminal(record, NavigationEventType.GOAL_REJECTED)
            return

        try:
            send_future.add_done_callback(
                lambda future: self._on_goal_response(record, future)
            )
        except Exception:
            self._emit_terminal(record, NavigationEventType.GOAL_REJECTED)

    def cancel_goal(self, handle: GenerationHandle) -> None:
        """Request cancellation while preserving a pre-acceptance race."""
        self._validate_handle(handle)
        with self._lock:
            record = self._records.get(handle)
            if record is None or record.terminal_emitted:
                return
            record.cancel_requested = True

        self._start_cancel(record)

    def _on_goal_response(
        self,
        record: _NavigationGoalRecord,
        future: Any,
    ) -> None:
        try:
            goal_handle = future.result()
        except Exception:
            self._emit_terminal(record, NavigationEventType.GOAL_REJECTED)
            return

        if goal_handle is None or not getattr(goal_handle, 'accepted', False):
            self._emit_terminal(record, NavigationEventType.GOAL_REJECTED)
            return

        with self._lock:
            if record.terminal_emitted:
                return
            record.goal_handle = goal_handle
            accepted_event = NavigationEvent(
                handle=record.handle,
                event_type=NavigationEventType.GOAL_ACCEPTED,
                payload=goal_handle,
            )

        # Do not call user code while holding the transport lock.
        self._event_sink(accepted_event)

        try:
            result_future = goal_handle.get_result_async()
        except Exception:
            self._emit_terminal(record, NavigationEventType.FAILED)
            return

        with self._lock:
            if record.terminal_emitted:
                return
            record.result_future = result_future

        try:
            result_future.add_done_callback(
                lambda result: self._on_result(record, result)
            )
        except Exception:
            self._emit_terminal(record, NavigationEventType.FAILED)
            return

        self._start_cancel(record)

    def _on_result(
        self,
        record: _NavigationGoalRecord,
        future: Any,
    ) -> None:
        try:
            result_response = future.result()
        except Exception:
            self._emit_terminal(record, NavigationEventType.FAILED)
            return

        status = getattr(result_response, 'status', None)
        with self._lock:
            record.result_response = result_response
            record.result_status = status
            if record.terminal_emitted:
                return

            if status == GoalStatus.STATUS_SUCCEEDED:
                event_type = NavigationEventType.SUCCEEDED
            elif status == GoalStatus.STATUS_CANCELED:
                if not record.cancel_requested:
                    event_type = NavigationEventType.FAILED
                elif record.cancel_response_received:
                    event_type = (
                        NavigationEventType.CANCEL_CONFIRMED
                        if record.cancel_response_valid
                        else NavigationEventType.CANCEL_FAILED
                    )
                else:
                    return
            else:
                event_type = NavigationEventType.FAILED

            event = self._take_terminal_locked(
                record,
                event_type,
                result_response,
            )

        if event is not None:
            self._event_sink(event)

    def _on_cancel_response(
        self,
        record: _NavigationGoalRecord,
        future: Any,
    ) -> None:
        try:
            response = future.result()
            response_valid = self._cancel_response_contains_goal(
                response,
                record.goal_handle,
            )
        except Exception:
            response = None
            response_valid = False

        with self._lock:
            record.cancel_response_received = True
            record.cancel_response_valid = response_valid
            record.cancel_response = response
            if record.terminal_emitted:
                return

            if not response_valid:
                event = self._take_terminal_locked(
                    record,
                    NavigationEventType.CANCEL_FAILED,
                    response,
                )
            elif record.result_status == GoalStatus.STATUS_CANCELED:
                event = self._take_terminal_locked(
                    record,
                    NavigationEventType.CANCEL_CONFIRMED,
                    record.result_response,
                )
            else:
                # Natural success/failure is emitted by _on_result.  If the
                # result is not complete yet, wait for it before deciding.
                event = None

        if event is not None:
            self._event_sink(event)

    def _start_cancel(self, record: _NavigationGoalRecord) -> None:
        with self._lock:
            if (
                record.terminal_emitted
                or not record.cancel_requested
                or record.cancel_started
                or record.goal_handle is None
            ):
                return
            record.cancel_started = True
            goal_handle = record.goal_handle

        try:
            cancel_future = goal_handle.cancel_goal_async()
        except Exception:
            self._mark_cancel_failed(record, None)
            return

        if cancel_future is None:
            self._mark_cancel_failed(record, None)
            return

        with self._lock:
            record.cancel_future = cancel_future

        try:
            cancel_future.add_done_callback(
                lambda future: self._on_cancel_response(record, future)
            )
        except Exception:
            self._mark_cancel_failed(record, None)

    def _mark_cancel_failed(
        self,
        record: _NavigationGoalRecord,
        response: Any,
    ) -> None:
        with self._lock:
            record.cancel_response_received = True
            record.cancel_response_valid = False
            record.cancel_response = response
            event = self._take_terminal_locked(
                record,
                NavigationEventType.CANCEL_FAILED,
                response,
            )
        if event is not None:
            self._event_sink(event)

    def _emit_terminal(
        self,
        record: _NavigationGoalRecord,
        event_type: NavigationEventType,
        payload: object | None = None,
    ) -> None:
        with self._lock:
            event = self._take_terminal_locked(record, event_type, payload)
        if event is not None:
            self._event_sink(event)

    def _take_terminal_locked(
        self,
        record: _NavigationGoalRecord,
        event_type: NavigationEventType,
        payload: object | None,
    ) -> NavigationEvent | None:
        if record.terminal_emitted:
            return None
        record.terminal_emitted = True
        if self._records.get(record.handle) is record:
            del self._records[record.handle]
        return NavigationEvent(
            handle=record.handle,
            event_type=event_type,
            payload=payload,
        )

    @staticmethod
    def _goal_id_bytes(goal_handle: Any) -> bytes:
        if goal_handle is None:
            return b''
        goal_id = getattr(goal_handle, 'goal_id', None)
        uuid = getattr(goal_id, 'uuid', None)
        if uuid is None:
            return b''
        return bytes(uuid)

    @classmethod
    def _cancel_response_contains_goal(
        cls,
        response: Any,
        goal_handle: Any,
    ) -> bool:
        if getattr(response, 'return_code', None) != CancelGoal.Response.ERROR_NONE:
            return False
        expected = cls._goal_id_bytes(goal_handle)
        if not expected:
            return False
        for goal_info in getattr(response, 'goals_canceling', ()):
            goal_id = getattr(goal_info, 'goal_id', None)
            if bytes(getattr(goal_id, 'uuid', ())) == expected:
                return True
        return False

    @staticmethod
    def _validate_handle(handle: GenerationHandle) -> None:
        if not isinstance(handle, GenerationHandle):
            raise TypeError('handle must be GenerationHandle')
