#!/usr/bin/env python3
"""
Competition-only HTTP HIL ingress for CleanNav Mission Manager.

This module is not a production vehicle runtime. It accepts J6-side HTTP
TaskCommand input and exposes navigation events/results for the PC simulation
execution adapter. J6 Mission Manager remains the mission authority.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import queue
import re
import sys
import threading
from collections import deque
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import rclpy
from cleannav_interfaces.msg import TaskCommand
from geometry_msgs.msg import PoseStamped
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)

from cleannav_mission_manager.adapters.mock_safety import MockSafetyAdapter
from cleannav_mission_manager.adapters.navigation import (
    NavigationEvent,
    NavigationEventSink,
    NavigationEventType,
)
from cleannav_mission_manager.demo_goals import (
    DEMO_TASK_GOALS,
    make_demo_goal,
)
from cleannav_mission_manager.domain.generation_gate import GenerationHandle
from cleannav_mission_manager.node import MissionManagerNode


__all__ = [
    'DEMO_TASK_GOALS',
    'HIL_BIND_HOST_ENV',
    'HIL_BIND_PORT_ENV',
    'HilNavigationAdapter',
    'HilNavigationHttpServer',
    'HilTaskIngress',
    'make_demo_goal',
    'create_hil_runtime',
    'resolve_hil_bind_address',
    'main',
]


HOST = "0.0.0.0"
PORT = 18081
HIL_BIND_HOST_ENV = "CLEANNAV_J6_HIL_BIND_HOST"
HIL_BIND_PORT_ENV = "CLEANNAV_J6_HIL_PORT"
MAX_BODY_BYTES = 8192
MAX_EVENT_HISTORY = 256
TASK_COMMAND_TOPIC = "/cleannav/hmi/task_command"
TASK_COMMAND_INTERFACE_VERSION = "1.0"
DEFAULT_TASK_VALID_FOR_SEC = 60.0
TASK_COMMAND_ID_MAX_LENGTH = 128
TASK_RAW_TEXT_MAX_LENGTH = 512
_COMMAND_ID_PATTERN = re.compile(r"[A-Za-z0-9._:-]+")
_ROS_DURATION_MAX_NS = (
    ((2**31 - 1) * 1_000_000_000) + 999_999_999
)

TERMINAL_EVENTS = {
    NavigationEventType.GOAL_REJECTED,
    NavigationEventType.SUCCEEDED,
    NavigationEventType.FAILED,
    NavigationEventType.CANCEL_CONFIRMED,
    NavigationEventType.CANCEL_FAILED,
}


def resolve_hil_bind_address(
    cli_host: str | None = None,
    cli_port: int | None = None,
    *,
    environ: dict[str, str] | None = None,
) -> tuple[str, int]:
    """Resolve the HIL server bind address without changing its defaults."""
    environment = os.environ if environ is None else environ
    host = cli_host or environment.get(HIL_BIND_HOST_ENV) or HOST
    raw_port = cli_port
    if raw_port is None:
        raw_port = environment.get(HIL_BIND_PORT_ENV, str(PORT))
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise ValueError("HIL bind port must be an integer") from exc
    if not host or not 1 <= port <= 65535:
        raise ValueError("HIL bind host/port is invalid")
    return str(host), port


def _parse_cli_args(args: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--hil-bind-host")
    parser.add_argument("--hil-port", type=int)
    return parser.parse_known_args(args)


@dataclass
class _NavigationRecord:
    handle: GenerationHandle
    nav_request_id: str
    cancel_requested: bool = False
    terminal_event: NavigationEventType | None = None
    queued_events: set[NavigationEventType] = field(default_factory=set)


@dataclass(frozen=True)
class _TaskSpec:
    task_id: int
    source: int
    command_id: str
    confidence: float
    raw_text: str
    user_confirmed: bool
    valid_for_sec: float


class HilTaskIngress:
    """Queue validated HTTP tasks and publish them from the ROS thread."""

    def __init__(self, node: MissionManagerNode) -> None:
        self._node = node
        self._queue: queue.Queue[_TaskSpec] = queue.Queue()
        self._qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
        )
        self._publisher = node.create_publisher(
            TaskCommand,
            TASK_COMMAND_TOPIC,
            self._qos,
        )

    @property
    def topic(self) -> str:
        """Return the formal Mission Manager TaskCommand ingress topic."""
        return TASK_COMMAND_TOPIC

    @property
    def qos(self) -> QoSProfile:
        """Return the publisher QoS for compatibility tests."""
        return self._qos

    def enqueue_task(self, payload: object) -> _TaskSpec:
        """Validate one HTTP JSON object and enqueue only plain task data."""
        spec = self._validate_task_spec(payload)
        self._queue.put(spec)
        return spec

    def drain_task_queue(self) -> None:
        """Build and publish TaskCommand messages on the ROS executor thread."""
        while True:
            try:
                spec = self._queue.get_nowait()
            except queue.Empty:
                return

            message = TaskCommand()
            message.header.stamp = self._node.get_clock().now().to_msg()
            message.header.frame_id = ""
            message.interface_version = TASK_COMMAND_INTERFACE_VERSION
            message.command_id = spec.command_id
            message.source = spec.source
            message.task_id = spec.task_id
            message.confidence = spec.confidence
            message.raw_text = spec.raw_text
            duration_sec, duration_nanosec = self._duration_parts(
                spec.valid_for_sec
            )
            message.valid_for.sec = duration_sec
            message.valid_for.nanosec = duration_nanosec
            message.user_confirmed = spec.user_confirmed
            self._publisher.publish(message)
            self._node.get_logger().info(
                "HIL_TASK_COMMAND_PUBLISHED "
                f"task_id={spec.task_id} "
                f"source={spec.source} "
                f"command_id={spec.command_id}"
            )

    @staticmethod
    def _validate_task_spec(payload: object) -> _TaskSpec:
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object")

        task_id = payload.get("task_id")
        if type(task_id) is not int or not 0 <= task_id <= 65535:
            raise ValueError("task_id must be an int in [0, 65535]")

        source = payload.get("source")
        if type(source) is not int or source not in (
            TaskCommand.SOURCE_VOICE,
            TaskCommand.SOURCE_APP,
            TaskCommand.SOURCE_MOCK,
        ):
            raise ValueError(
                "source must be one of 1 (VOICE), 2 (APP), 3 (MOCK)"
            )

        command_id = payload.get("command_id")
        if (
            not isinstance(command_id, str)
            or not command_id
            or len(command_id) > TASK_COMMAND_ID_MAX_LENGTH
            or _COMMAND_ID_PATTERN.fullmatch(command_id) is None
        ):
            raise ValueError(
                "command_id must match ^[A-Za-z0-9._:-]+$ and be at most "
                f"{TASK_COMMAND_ID_MAX_LENGTH} characters"
            )

        confidence_value = payload.get("confidence")
        if isinstance(confidence_value, bool) or not isinstance(
            confidence_value,
            (int, float),
        ):
            raise ValueError("confidence must be a number in [0.0, 1.0]")
        confidence = float(confidence_value)
        if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
            raise ValueError(
                "confidence must be a finite number in [0.0, 1.0]"
            )

        raw_text = payload.get("raw_text", "")
        if (
            not isinstance(raw_text, str)
            or len(raw_text) > TASK_RAW_TEXT_MAX_LENGTH
        ):
            raise ValueError(
                "raw_text must be a string of at most "
                f"{TASK_RAW_TEXT_MAX_LENGTH} characters"
            )

        user_confirmed = payload.get("user_confirmed", False)
        if type(user_confirmed) is not bool:
            raise ValueError("user_confirmed must be a boolean")

        valid_for_value = payload.get(
            "valid_for_sec",
            DEFAULT_TASK_VALID_FOR_SEC,
        )
        if isinstance(valid_for_value, bool) or not isinstance(
            valid_for_value,
            (int, float),
        ):
            raise ValueError("valid_for_sec must be a finite number > 0")
        valid_for_sec = float(valid_for_value)
        if not math.isfinite(valid_for_sec) or valid_for_sec <= 0.0:
            raise ValueError("valid_for_sec must be a finite number > 0")
        HilTaskIngress._duration_parts(valid_for_sec)

        return _TaskSpec(
            task_id=task_id,
            source=source,
            command_id=command_id,
            confidence=confidence,
            raw_text=raw_text,
            user_confirmed=user_confirmed,
            valid_for_sec=valid_for_sec,
        )

    @staticmethod
    def _duration_parts(valid_for_sec: float) -> tuple[int, int]:
        if (
            not math.isfinite(valid_for_sec)
            or valid_for_sec <= 0.0
            or valid_for_sec > _ROS_DURATION_MAX_NS / 1_000_000_000
        ):
            raise ValueError(
                "valid_for_sec cannot be represented as a positive ROS Duration"
            )
        total_nanoseconds = int(round(valid_for_sec * 1_000_000_000))
        if not 0 < total_nanoseconds <= _ROS_DURATION_MAX_NS:
            raise ValueError(
                "valid_for_sec cannot be represented as a positive ROS Duration"
            )
        return divmod(total_nanoseconds, 1_000_000_000)


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
        """
        Queue a PC navigation result.

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
        task_ingress: HilTaskIngress | None = None,
    ) -> None:
        self.adapter = adapter
        self.task_ingress = task_ingress

        super().__init__(
            server_address,
            HilNavigationHttpHandler,
        )


class HilNavigationHttpHandler(BaseHTTPRequestHandler):

    server_version = "CleanNavHILNavigation/0.1"

    @property
    def adapter(self) -> HilNavigationAdapter:
        return self.server.adapter

    @property
    def task_ingress(self) -> HilTaskIngress | None:
        return self.server.task_ingress

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
            health = self.adapter.health_snapshot()
            health["task_ingress"] = self.task_ingress is not None
            self._json_response(
                200,
                health,
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

        if parsed.path not in ("/nav/result", "/task"):
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

            if parsed.path == "/task":
                if self.task_ingress is None:
                    self._json_response(
                        503,
                        {"error": "task_ingress_unavailable"},
                    )
                    return
                spec = self.task_ingress.enqueue_task(body)
                self.log_message(
                    "HIL_HTTP_TASK_ACCEPTED task_id=%d "
                    "source=%d command_id=%s",
                    spec.task_id,
                    spec.source,
                    spec.command_id,
                )
                self._json_response(
                    202,
                    {
                        "accepted_for_delivery": True,
                        "task_id": spec.task_id,
                        "source": spec.source,
                        "command_id": spec.command_id,
                    },
                )
                return

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


def create_hil_runtime(
    node: MissionManagerNode,
):
    """Inject HIL NavigationAdapter without changing frozen MM core."""
    navigation = HilNavigationAdapter(
        node._on_navigation_event
    )
    task_ingress = HilTaskIngress(node)

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
    node.create_timer(
        0.02,
        task_ingress.drain_task_queue,
    )
    node._hil_task_ingress = task_ingress

    return runtime


def main(args=None) -> None:
    cli_args = list(sys.argv[1:] if args is None else args)
    parsed_args, ros_args = _parse_cli_args(cli_args)
    hil_host, hil_port = resolve_hil_bind_address(
        parsed_args.hil_bind_host,
        parsed_args.hil_port,
    )
    rclpy.init(args=ros_args)

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

        task_ingress = getattr(node, "_hil_task_ingress", None)
        if not isinstance(task_ingress, HilTaskIngress):
            raise RuntimeError(
                "HIL task ingress was not assembled"
            )

        server = HilNavigationHttpServer(
            (hil_host, hil_port),
            runtime.navigation,
            task_ingress,
        )

        http_thread = threading.Thread(
            target=server.serve_forever,
            name="cleannav-hil-navigation-http",
            daemon=True,
        )

        http_thread.start()

        node.get_logger().info(
            "Demo HIL navigation runtime active; "
            f"HTTP listening on {hil_host}:{hil_port} "
            "mode=COMPETITION_HIL_ONLY"
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
