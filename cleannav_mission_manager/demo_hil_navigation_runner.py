#!/usr/bin/env python3
"""Demo-only HTTP HIL navigation runtime for CleanNav Mission Manager."""

from __future__ import annotations

import json
import queue
import threading
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import rclpy
from geometry_msgs.msg import PoseStamped

from cleannav_mission_manager.adapters.mock_safety import MockSafetyAdapter
from cleannav_mission_manager.adapters.navigation import (
    NavigationEvent,
    NavigationEventSink,
    NavigationEventType,
)
from cleannav_mission_manager.domain.generation_gate import GenerationHandle
from cleannav_mission_manager.node import MissionManagerNode


HOST = "0.0.0.0"
PORT = 18081
MAX_BODY_BYTES = 8192
MAX_EVENT_HISTORY = 256

# Phase-1 Demo resolver.
# Gazebo world: robot starts near (0, -5), first truth leaf is at (12, -5).
# RTAB-Map map frame starts near the robot, so the corresponding goal is (12, 0).
DEMO_TASK_GOALS = {
    30: (12.0, 0.0, 0.0),
}

TERMINAL_EVENTS = {
    NavigationEventType.GOAL_REJECTED,
    NavigationEventType.SUCCEEDED,
    NavigationEventType.FAILED,
    NavigationEventType.CANCEL_CONFIRMED,
    NavigationEventType.CANCEL_FAILED,
}


@dataclass
class _NavigationRecord:
    handle: GenerationHandle
    nav_request_id: str
    cancel_requested: bool = False
    terminal_event: NavigationEventType | None = None
    queued_events: set[NavigationEventType] = field(default_factory=set)


class HilNavigationAdapter:
    """NavigationAdapter implementation backed by an HTTP event queue."""

    def __init__(self, event_sink: NavigationEventSink) -> None:
        if not callable(event_sink):
            raise TypeError("event_sink must be callable")

        self._event_sink = event_sink
        self._lock = threading.Lock()

        self._next_event_id = 1
        self._event_history: deque[dict[str, Any]] = deque(
            maxlen=MAX_EVENT_HISTORY
        )

        self._records_by_handle: dict[
            GenerationHandle,
            _NavigationRecord,
        ] = {}

        self._records_by_request_id: dict[
            str,
            _NavigationRecord,
        ] = {}

        self._result_queue: queue.Queue[
            tuple[
                _NavigationRecord,
                NavigationEventType,
                object | None,
            ]
        ] = queue.Queue()

    def submit_goal(
        self,
        handle: GenerationHandle,
        goal_payload: object,
    ) -> None:
        self._validate_handle(handle)

        nav_request_id = self._request_id_for_handle(handle)

        with self._lock:
            if handle in self._records_by_handle:
                return

            record = _NavigationRecord(
                handle=handle,
                nav_request_id=nav_request_id,
            )

            self._records_by_handle[handle] = record
            self._records_by_request_id[nav_request_id] = record

            self._append_event_locked(
                {
                    "nav_request_id": nav_request_id,
                    "operation": "SUBMIT_GOAL",
                    "handle": self._serialize_handle(handle),
                    "goal": self._serialize_goal(goal_payload),
                }
            )

    def cancel_goal(
        self,
        handle: GenerationHandle,
    ) -> None:
        self._validate_handle(handle)

        with self._lock:
            record = self._records_by_handle.get(handle)

            if (
                record is None
                or record.cancel_requested
                or record.terminal_event is not None
            ):
                return

            record.cancel_requested = True

            self._append_event_locked(
                {
                    "nav_request_id": record.nav_request_id,
                    "operation": "CANCEL_GOAL",
                    "handle": self._serialize_handle(handle),
                }
            )

    def get_events_after(
        self,
        after_event_id: int,
    ) -> list[dict[str, Any]]:
        if type(after_event_id) is not int:
            raise TypeError("after_event_id must be int")

        if after_event_id < 0:
            raise ValueError("after_event_id must be >= 0")

        with self._lock:
            return [
                dict(event)
                for event in self._event_history
                if int(event["event_id"]) > after_event_id
            ]

    def queue_result(
        self,
        *,
        nav_request_id: str,
        event_name: str,
        payload: object | None,
    ) -> bool:
        """Queue a PC navigation result.

        Returns True when the exact event was already accepted.
        """
        if not isinstance(nav_request_id, str) or not nav_request_id:
            raise ValueError(
                "nav_request_id must be a non-empty string"
            )

        if not isinstance(event_name, str) or not event_name:
            raise ValueError(
                "event must be a non-empty string"
            )

        try:
            event_type = NavigationEventType(event_name)
        except ValueError as exc:
            allowed = ", ".join(
                item.value
                for item in NavigationEventType
            )
            raise ValueError(
                f"unsupported navigation event; allowed: {allowed}"
            ) from exc

        with self._lock:
            record = self._records_by_request_id.get(
                nav_request_id
            )

            if record is None:
                raise ValueError(
                    "unknown nav_request_id"
                )

            if event_type in record.queued_events:
                return True

            if record.terminal_event is not None:
                if event_type is record.terminal_event:
                    return True

                raise ValueError(
                    "navigation request is already terminal"
                )

            if (
                event_type
                in {
                    NavigationEventType.CANCEL_CONFIRMED,
                    NavigationEventType.CANCEL_FAILED,
                }
                and not record.cancel_requested
            ):
                raise ValueError(
                    "cancel result received before "
                    "CANCEL_GOAL was requested"
                )

            if event_type in TERMINAL_EVENTS:
                record.terminal_event = event_type

            record.queued_events.add(event_type)

            self._result_queue.put(
                (
                    record,
                    event_type,
                    payload,
                )
            )

            return False

    def drain_result_queue(self) -> None:
        """Deliver HTTP results to MM from the ROS executor thread."""
        while True:
            try:
                record, event_type, payload = (
                    self._result_queue.get_nowait()
                )
            except queue.Empty:
                return

            self._event_sink(
                NavigationEvent(
                    handle=record.handle,
                    event_type=event_type,
                    payload=payload,
                )
            )

    def health_snapshot(self) -> dict[str, Any]:
        with self._lock:
            active = sum(
                1
                for record
                in self._records_by_request_id.values()
                if record.terminal_event is None
            )

            latest_event_id = (
                int(self._event_history[-1]["event_id"])
                if self._event_history
                else 0
            )

        return {
            "ok": True,
            "bridge": "cleannav_hil_navigation_runner",
            "http_port": PORT,
            "active_navigation_requests": active,
            "latest_event_id": latest_event_id,
        }

    def _append_event_locked(
        self,
        event: dict[str, Any],
    ) -> None:
        event = dict(event)
        event["event_id"] = self._next_event_id

        self._next_event_id += 1
        self._event_history.append(event)

    @staticmethod
    def _request_id_for_handle(
        handle: GenerationHandle,
    ) -> str:
        return (
            f"{handle.execution_id}:g{handle.generation}"
        )

    @staticmethod
    def _serialize_handle(
        handle: GenerationHandle,
    ) -> dict[str, Any]:
        return {
            "execution_id": handle.execution_id,
            "generation": handle.generation,
        }

    @staticmethod
    def _serialize_goal(
        goal_payload: object,
    ) -> dict[str, Any]:
        if isinstance(goal_payload, PoseStamped):
            pose = goal_payload.pose

            return {
                "kind": "pose_stamped",
                "frame_id": goal_payload.header.frame_id,
                "position": {
                    "x": float(pose.position.x),
                    "y": float(pose.position.y),
                    "z": float(pose.position.z),
                },
                "orientation": {
                    "x": float(pose.orientation.x),
                    "y": float(pose.orientation.y),
                    "z": float(pose.orientation.z),
                    "w": float(pose.orientation.w),
                },
            }

        return {
            "kind": "opaque",
            "python_type": type(goal_payload).__name__,
            "repr": repr(goal_payload),
        }

    @staticmethod
    def _validate_handle(
        handle: GenerationHandle,
    ) -> None:
        if not isinstance(handle, GenerationHandle):
            raise TypeError(
                "handle must be GenerationHandle"
            )


class HilNavigationHttpServer(ThreadingHTTPServer):

    def __init__(
        self,
        server_address: tuple[str, int],
        adapter: HilNavigationAdapter,
    ) -> None:
        self.adapter = adapter

        super().__init__(
            server_address,
            HilNavigationHttpHandler,
        )


class HilNavigationHttpHandler(BaseHTTPRequestHandler):

    server_version = "CleanNavHILNavigation/0.1"

    @property
    def adapter(self) -> HilNavigationAdapter:
        return self.server.adapter

    def _json_response(
        self,
        status_code: int,
        payload: object,
    ) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")

        self.send_response(status_code)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )

        self.send_header(
            "Content-Length",
            str(len(body)),
        )

        self.send_header(
            "Cache-Control",
            "no-store",
        )

        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self._json_response(
                200,
                self.adapter.health_snapshot(),
            )
            return

        if parsed.path == "/nav/events":
            try:
                after = int(
                    parse_qs(parsed.query)
                    .get("after", ["0"])[0]
                )

                events = self.adapter.get_events_after(
                    after
                )

            except (TypeError, ValueError) as exc:
                self._json_response(
                    400,
                    {
                        "error": "invalid_request",
                        "detail": str(exc),
                    },
                )
                return

            self._json_response(
                200,
                {
                    "events": events,
                    "count": len(events),
                },
            )
            return

        self._json_response(
            404,
            {
                "error": "not_found",
            },
        )

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path != "/nav/result":
            self._json_response(
                404,
                {
                    "error": "not_found",
                },
            )
            return

        try:
            length = int(
                self.headers.get(
                    "Content-Length",
                    "0",
                )
            )

            if length <= 0:
                raise ValueError(
                    "empty request body"
                )

            if length > MAX_BODY_BYTES:
                self._json_response(
                    413,
                    {
                        "error": "request_too_large",
                    },
                )
                return

            body = json.loads(
                self.rfile.read(length)
                .decode("utf-8")
            )

            if not isinstance(body, dict):
                raise ValueError(
                    "JSON body must be an object"
                )

            nav_request_id = body.get(
                "nav_request_id"
            )

            event_name = body.get(
                "event"
            )

            payload = body.get(
                "payload"
            )

            duplicate = self.adapter.queue_result(
                nav_request_id=nav_request_id,
                event_name=event_name,
                payload=payload,
            )

            self._json_response(
                202,
                {
                    "accepted_for_delivery": True,
                    "duplicate": duplicate,
                    "nav_request_id": nav_request_id,
                    "event": event_name,
                },
            )

        except (
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            self._json_response(
                400,
                {
                    "error": "invalid_request",
                    "detail": str(exc),
                },
            )

    def log_message(
        self,
        fmt: str,
        *args: object,
    ) -> None:
        print(
            "[HIL-NAV-HTTP] "
            + self.address_string()
            + " "
            + (fmt % args),
            flush=True,
        )


def make_demo_goal(
    command: object,
    task: object,
) -> object:
    """Resolve Phase-1 task30 to the first frozen leaf truth pose."""
    task_id = int(
        getattr(task, "task_id")
    )

    target = DEMO_TASK_GOALS.get(
        task_id
    )

    if target is None:
        return (
            "hil-demo-goal",
            str(
                getattr(
                    command,
                    "command_id",
                )
            ),
            task_id,
        )

    x, y, z = target

    pose = PoseStamped()

    pose.header.frame_id = "map"

    pose.pose.position.x = x
    pose.pose.position.y = y
    pose.pose.position.z = z

    pose.pose.orientation.w = 1.0

    return pose


def create_hil_runtime(
    node: MissionManagerNode,
):
    """Inject HIL NavigationAdapter without changing frozen MM core."""
    navigation = HilNavigationAdapter(
        node._on_navigation_event
    )

    safety = MockSafetyAdapter(
        node._on_safety_event
    )

    runtime = node._create_runtime(
        navigation=navigation,
        safety=safety,
        goal_resolver=make_demo_goal,
    )

    # HTTP request threads only enqueue result events.
    # MM callbacks are delivered on the ROS executor thread.
    node.create_timer(
        0.02,
        navigation.drain_result_queue,
    )

    return runtime


def main(args=None) -> None:

    rclpy.init(args=args)

    node = None
    server = None

    try:
        node = MissionManagerNode(
            runtime_factory=create_hil_runtime
        )

        runtime = node.runtime

        if (
            runtime is None
            or not isinstance(
                runtime.navigation,
                HilNavigationAdapter,
            )
        ):
            raise RuntimeError(
                "HIL navigation runtime was not assembled"
            )

        server = HilNavigationHttpServer(
            (HOST, PORT),
            runtime.navigation,
        )

        http_thread = threading.Thread(
            target=server.serve_forever,
            name="cleannav-hil-navigation-http",
            daemon=True,
        )

        http_thread.start()

        node.get_logger().info(
            "Demo HIL navigation runtime active; "
            f"HTTP listening on {HOST}:{PORT}"
        )

        node.get_logger().info(
            "Frozen runtime_mode parameter remains "
            "'mock'; HIL navigation was injected "
            "by the Demo runner"
        )

        rclpy.spin(node)

    except KeyboardInterrupt:
        pass

    finally:
        if server is not None:
            server.shutdown()
            server.server_close()

        if node is not None:
            node.destroy_node()

        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
