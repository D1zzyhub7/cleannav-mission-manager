#!/usr/bin/env python3
"""
Competition-only J6 HIL event to PC Gazebo task30 adapter.

This module is not a production vehicle runtime. J6 Mission Manager remains
the external mission authority; the PC process is only the simulation
execution adapter. The physical leaf-completion latch belongs to the PC
Showcase semantics and is intentionally not part of the formal runtime.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import rclpy
from cleannav_interfaces.msg import TaskStatus
from rclpy.executors import ExternalShutdownException
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)

from cleannav_mission_manager.demo_pc_offline_runner import (
    create_pc_hil_execution_runtime,
    start_pc_showcase_task30_execution,
)
from cleannav_mission_manager.node import MissionManagerNode


DEFAULT_J6_BASE_URL = "http://192.168.8.10:18081"
J6_BASE_URL_ENV = "CLEANNAV_J6_HIL_BASE_URL"
DEFAULT_HTTP_TIMEOUT_SEC = 2.5
DEFAULT_POLL_INTERVAL_SEC = 0.2
NETWORK_WARNING_THROTTLE_SEC = 2.0
MAX_PROCESSED_EVENT_IDS = 512
PC_PRIVATE_TASK_COMMAND_TOPIC = "/cleannav/hil_pc/task_command"
PC_PRIVATE_TASK_STATUS_TOPIC = "/cleannav/hil_pc/task_status"


def resolve_j6_base_url(
    cli_value: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    """Resolve CLI, environment and backwards-compatible Demo URL values."""
    environment = os.environ if environ is None else environ
    value = cli_value or environment.get(J6_BASE_URL_ENV) or DEFAULT_J6_BASE_URL
    if not isinstance(value, str) or not value.startswith("http://"):
        raise ValueError("J6 HIL base URL must be a non-empty http:// URL")
    return value.rstrip("/")


def _parse_cli_args(args: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--j6-base-url")
    return parser.parse_known_args(args)


@dataclass(frozen=True)
class _ExecutionRequest:
    event_id: int
    nav_request_id: str


@dataclass(frozen=True)
class _ActiveExecution:
    request: _ExecutionRequest
    command_id: str
    pc_execution_id: str


@dataclass(frozen=True)
class _PendingResult:
    nav_request_id: str
    event: str
    payload: dict[str, Any]


class J6NavigationHttpClient:
    """Small standard-library client for the frozen J6 HIL endpoints."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_sec: float = DEFAULT_HTTP_TIMEOUT_SEC,
    ) -> None:
        base_url = resolve_j6_base_url(base_url)
        if not isinstance(base_url, str) or not base_url.startswith("http://"):
            raise ValueError("base_url must be a non-empty http:// URL")
        if not isinstance(timeout_sec, (int, float)) or timeout_sec <= 0.0:
            raise ValueError("timeout_sec must be > 0")
        self._base_url = base_url.rstrip("/")
        self._timeout_sec = float(timeout_sec)

    @property
    def base_url(self) -> str:
        return self._base_url

    def get_navigation_events(self, after_event_id: int) -> dict[str, Any]:
        query = urlencode({"after": int(after_event_id)})
        request = Request(
            f"{self._base_url}/nav/events?{query}",
            headers={"Accept": "application/json"},
            method="GET",
        )
        status, payload = self._request_json(request)
        if status != 200:
            raise RuntimeError(f"GET /nav/events returned HTTP {status}")
        if not isinstance(payload, dict):
            raise ValueError("GET /nav/events response must be an object")
        return payload

    def post_navigation_result(
        self,
        *,
        nav_request_id: str,
        event: str,
        payload: dict[str, Any],
    ) -> int:
        body = json.dumps(
            {
                "nav_request_id": nav_request_id,
                "event": event,
                "payload": payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(
            f"{self._base_url}/nav/result",
            data=body,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        status, _response = self._request_json(request)
        return status

    def _request_json(self, request: Request) -> tuple[int, object]:
        try:
            with urlopen(request, timeout=self._timeout_sec) as response:
                status = int(response.status)
                data = response.read()
        except HTTPError as exc:
            raise RuntimeError(
                f"J6 HTTP request returned {exc.code}"
            ) from exc
        except (OSError, URLError) as exc:
            raise RuntimeError(f"J6 HTTP request failed: {exc}") from exc
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("J6 HTTP response is not valid JSON") from exc
        return status, payload


class HilPcExecutionBridge:
    """
    Treat the frozen PC Showcase as J6's task30 execution adapter.

    J6 remains the external mission authority. The PC-side Mission Manager is
    reused only to execute its already-validated Navigation, Safety and
    physical leaf-completion path. The old goal contained in J6 SUBMIT_GOAL is
    intentionally ignored.
    """

    def __init__(
        self,
        node: MissionManagerNode,
        *,
        http_client: object | None = None,
        execution_starter: Callable[..., object] = (
            start_pc_showcase_task30_execution
        ),
        poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
    ) -> None:
        if not callable(execution_starter):
            raise TypeError("execution_starter must be callable")
        if (
            not isinstance(poll_interval_sec, (int, float))
            or poll_interval_sec <= 0.0
        ):
            raise ValueError("poll_interval_sec must be > 0")
        self._node = node
        self._http_client = http_client or J6NavigationHttpClient()
        self._execution_starter = execution_starter
        self._poll_interval_sec = float(poll_interval_sec)
        self._execution_queue: queue.Queue[_ExecutionRequest] = queue.Queue()
        self._result_queue: queue.Queue[_PendingResult] = queue.Queue()
        self._active_execution: _ActiveExecution | None = None
        self._last_event_id = 0
        self._processed_event_ids: set[int] = set()
        self._processed_event_order: deque[int] = deque()
        self._stop_event = threading.Event()
        self._network_thread: threading.Thread | None = None
        self._last_network_warning = -float("inf")

    @property
    def last_event_id(self) -> int:
        return self._last_event_id

    @property
    def active_execution(self) -> _ActiveExecution | None:
        return self._active_execution

    def start(self) -> None:
        """Start the only background thread, which performs J6 HTTP I/O."""
        if self._network_thread is not None:
            return
        self._network_thread = threading.Thread(
            target=self._network_loop,
            name="cleannav-hil-pc-http",
            daemon=True,
        )
        self._network_thread.start()
        self._log_info(
            "HIL_PC_BRIDGE_READY "
            f"j6_url={getattr(self._http_client, 'base_url', 'injected')} "
            f"poll_interval={self._poll_interval_sec:.3f}s"
        )

    def stop(self) -> None:
        """Stop and briefly join the HTTP thread."""
        self._stop_event.set()
        thread = self._network_thread
        if thread is not None:
            thread.join(timeout=DEFAULT_HTTP_TIMEOUT_SEC + 0.5)

    def poll_once(self) -> None:
        """Poll one J6 event batch and enqueue new SUBMIT_GOAL events."""
        try:
            response = self._http_client.get_navigation_events(
                self._last_event_id
            )
            events = response.get("events")
            if not isinstance(events, list):
                raise ValueError("GET /nav/events response.events must be a list")
            ordered_events = sorted(events, key=self._event_sort_key)
            for event in ordered_events:
                self._process_event(event)
        except Exception as exc:
            self._warn_network(f"event polling failed: {exc}")

    def drain_execution_queue(self) -> None:
        """Start at most one real PC Showcase execution on the ROS thread."""
        if self._active_execution is not None:
            return
        try:
            request = self._execution_queue.get_nowait()
        except queue.Empty:
            return

        command_id = f"hil-pc-{request.event_id}"
        try:
            result = self._execution_starter(
                self._node,
                command_id=command_id,
            )
            accepted = bool(getattr(result, "accepted", False))
            execution_id = getattr(result, "execution_id", None)
            if not accepted or not isinstance(execution_id, str) or not execution_id:
                raise RuntimeError("PC task30 execution was not accepted")
        except Exception as exc:
            self._log_error(
                "PC_TASK30_EXECUTION_FAILED "
                f"nav_request_id={request.nav_request_id} "
                f"detail={exc}"
            )
            self._queue_terminal_result(
                request.nav_request_id,
                "FAILED",
                {"detail": "PC Gazebo task30 showcase failed to start"},
            )
            return

        self._active_execution = _ActiveExecution(
            request=request,
            command_id=command_id,
            pc_execution_id=execution_id,
        )
        self._log_info(
            "PC_TASK30_EXECUTION_STARTED "
            f"nav_request_id={request.nav_request_id} "
            f"pc_execution_id={execution_id}"
        )

    def on_task_status(self, message: TaskStatus) -> None:
        """Translate only the active PC execution's real terminal status."""
        active = self._active_execution
        if active is None:
            return
        if (
            int(message.status_scope) != TaskStatus.SCOPE_EXECUTION
            or message.execution_id != active.pc_execution_id
            or message.command_id != active.command_id
            or int(message.task_id) != 30
        ):
            return

        state = int(message.state)
        if state == TaskStatus.STATE_SUCCEEDED:
            event = "SUCCEEDED"
            payload = {"detail": "PC Gazebo task30 showcase completed"}
            self._log_info(
                "PC_TASK30_EXECUTION_SUCCEEDED "
                f"nav_request_id={active.request.nav_request_id} "
                f"pc_execution_id={active.pc_execution_id}"
            )
        elif state in (
            TaskStatus.STATE_FAILED,
            TaskStatus.STATE_CANCELED,
            TaskStatus.STATE_EMERGENCY_STOPPED,
        ):
            event = "FAILED"
            payload = {
                "detail": "PC Gazebo task30 showcase failed",
                "pc_task_state": state,
            }
            self._log_error(
                "PC_TASK30_EXECUTION_FAILED "
                f"nav_request_id={active.request.nav_request_id} "
                f"pc_execution_id={active.pc_execution_id} "
                f"state={state}"
            )
        else:
            return

        self._active_execution = None
        self._queue_terminal_result(
            active.request.nav_request_id,
            event,
            payload,
        )

    def post_one_result(self) -> bool:
        """Post one queued terminal result, retaining it after failures."""
        try:
            result = self._result_queue.get_nowait()
        except queue.Empty:
            return False
        try:
            status = self._http_client.post_navigation_result(
                nav_request_id=result.nav_request_id,
                event=result.event,
                payload=result.payload,
            )
            if status != 202:
                raise RuntimeError(f"POST /nav/result returned HTTP {status}")
        except Exception as exc:
            self._result_queue.put(result)
            self._warn_network(f"result post failed: {exc}")
            return False

        self._log_info(
            "J6_NAV_RESULT_POSTED "
            f"nav_request_id={result.nav_request_id} "
            f"event={result.event} status={status}"
        )
        return True

    def _network_loop(self) -> None:
        while not self._stop_event.is_set():
            self.post_one_result()
            self.poll_once()
            self._stop_event.wait(self._poll_interval_sec)

    @staticmethod
    def _event_sort_key(event: object) -> int:
        if not isinstance(event, dict) or type(event.get("event_id")) is not int:
            raise ValueError("navigation event_id must be an int")
        return int(event["event_id"])

    def _process_event(self, event: object) -> None:
        if not isinstance(event, dict):
            raise ValueError("navigation event must be an object")
        event_id = event.get("event_id")
        if type(event_id) is not int or event_id < 0:
            raise ValueError("navigation event_id must be a non-negative int")
        if event_id in self._processed_event_ids:
            self._last_event_id = max(self._last_event_id, event_id)
            return

        self._remember_event_id(event_id)
        self._last_event_id = max(self._last_event_id, event_id)
        if event.get("operation") != "SUBMIT_GOAL":
            return

        nav_request_id = event.get("nav_request_id")
        if not isinstance(nav_request_id, str) or not nav_request_id:
            raise ValueError("SUBMIT_GOAL nav_request_id must be non-empty")
        request = _ExecutionRequest(
            event_id=event_id,
            nav_request_id=nav_request_id,
        )
        self._execution_queue.put(request)
        self._log_info(
            "J6_NAV_EVENT_RECEIVED "
            f"nav_request_id={nav_request_id} event_id={event_id}"
        )

    def _remember_event_id(self, event_id: int) -> None:
        if len(self._processed_event_order) >= MAX_PROCESSED_EVENT_IDS:
            oldest = self._processed_event_order.popleft()
            self._processed_event_ids.discard(oldest)
        self._processed_event_order.append(event_id)
        self._processed_event_ids.add(event_id)

    def _queue_terminal_result(
        self,
        nav_request_id: str,
        event: str,
        payload: dict[str, Any],
    ) -> None:
        self._result_queue.put(
            _PendingResult(
                nav_request_id=nav_request_id,
                event=event,
                payload=payload,
            )
        )

    def _warn_network(self, detail: str) -> None:
        now = time.monotonic()
        if now - self._last_network_warning < NETWORK_WARNING_THROTTLE_SEC:
            return
        self._last_network_warning = now
        self._node.get_logger().warning(
            f"HIL_PC_NETWORK_WARNING {detail}"
        )

    def _log_info(self, message: str) -> None:
        self._node.get_logger().info(message)

    def _log_error(self, message: str) -> None:
        self._node.get_logger().error(message)


def _task_status_qos() -> QoSProfile:
    return QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=10,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
    )


def main(args=None) -> None:
    """Run the PC Showcase as the execution side of the J6 HIL bridge."""
    cli_args = list(sys.argv[1:] if args is None else args)
    parsed_args, ros_args = _parse_cli_args(cli_args)
    j6_base_url = resolve_j6_base_url(parsed_args.j6_base_url)
    rclpy.init(args=ros_args)
    node = None
    bridge = None
    try:
        # Private remaps isolate PC TaskCommand/TaskStatus traffic from J6 and
        # the voice bridge. Navigation and Safety APIs remain unchanged.
        node = MissionManagerNode(
            runtime_factory=create_pc_hil_execution_runtime,
            cli_args=[
                "--ros-args",
                "-r",
                (
                    "/cleannav/hmi/task_command:="
                    f"{PC_PRIVATE_TASK_COMMAND_TOPIC}"
                ),
                "-r",
                f"/cleannav/task_status:={PC_PRIVATE_TASK_STATUS_TOPIC}",
            ],
        )
        bridge = HilPcExecutionBridge(
            node,
            http_client=J6NavigationHttpClient(j6_base_url),
        )
        node.create_subscription(
            TaskStatus,
            "/cleannav/task_status",
            bridge.on_task_status,
            _task_status_qos(),
        )
        node.create_timer(0.02, bridge.drain_execution_queue)
        bridge.start()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if bridge is not None:
            bridge.stop()
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()


__all__ = [
    "DEFAULT_J6_BASE_URL",
    "J6_BASE_URL_ENV",
    "HilPcExecutionBridge",
    "J6NavigationHttpClient",
    "resolve_j6_base_url",
    "main",
]
