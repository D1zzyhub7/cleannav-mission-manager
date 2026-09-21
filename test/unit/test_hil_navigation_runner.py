"""Focused tests for the demo-only J6M HIL HTTP bridge."""

from __future__ import annotations

import http.client
import json
import threading

import pytest
from builtin_interfaces.msg import Time
from cleannav_interfaces.msg import TaskCommand
from geometry_msgs.msg import PoseStamped
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    ReliabilityPolicy,
)

from cleannav_mission_manager.adapters.navigation import NavigationEventType
from cleannav_mission_manager.demo_hil_navigation_runner import (
    HIL_BIND_HOST_ENV,
    HIL_BIND_PORT_ENV,
    HilNavigationAdapter,
    HilNavigationHttpServer,
    HilTaskIngress,
    TASK_COMMAND_TOPIC,
    resolve_hil_bind_address,
)
from cleannav_mission_manager.domain.generation_gate import GenerationHandle


class _FakePublisher:
    def __init__(self) -> None:
        self.messages = []

    def publish(self, message) -> None:
        self.messages.append(message)


class _FakeLogger:
    def __init__(self) -> None:
        self.messages = []

    def info(self, message) -> None:
        self.messages.append(message)


class _FakeNow:
    def __init__(self, stamp: Time) -> None:
        self._stamp = stamp

    def to_msg(self) -> Time:
        return self._stamp


class _FakeClock:
    def __init__(self) -> None:
        self.calls = 0
        self.stamp = Time(sec=1234, nanosec=567890123)

    def now(self) -> _FakeNow:
        self.calls += 1
        return _FakeNow(self.stamp)


class _FakeNode:
    def __init__(self) -> None:
        self.publisher = _FakePublisher()
        self.logger = _FakeLogger()
        self.clock = _FakeClock()
        self.publisher_type = None
        self.publisher_topic = None
        self.publisher_qos = None
        self.mission_callback_calls = 0

    def create_publisher(self, message_type, topic, qos):
        self.publisher_type = message_type
        self.publisher_topic = topic
        self.publisher_qos = qos
        return self.publisher

    def get_clock(self) -> _FakeClock:
        return self.clock

    def get_logger(self) -> _FakeLogger:
        return self.logger

    def _on_task_command(self, _message) -> None:
        self.mission_callback_calls += 1
        raise AssertionError("HTTP ingress must publish, not invoke MM directly")


@pytest.fixture
def hil_http_server():
    node = _FakeNode()
    ingress = HilTaskIngress(node)
    navigation_events = []
    adapter = HilNavigationAdapter(navigation_events.append)
    server = HilNavigationHttpServer(
        ("127.0.0.1", 0),
        adapter,
        ingress,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, adapter, ingress, node, navigation_events
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def _valid_task(**updates):
    payload = {
        "task_id": 30,
        "source": 1,
        "command_id": "voice-hil-001",
        "confidence": 0.95,
        "raw_text": "清扫最近的落叶",
        "user_confirmed": False,
        "valid_for_sec": 60.0,
    }
    payload.update(updates)
    return payload


def test_hil_bind_address_keeps_default_and_supports_environment_override():
    assert resolve_hil_bind_address(environ={}) == ("0.0.0.0", 18081)
    assert resolve_hil_bind_address(
        environ={
            HIL_BIND_HOST_ENV: "127.0.0.1",
            HIL_BIND_PORT_ENV: "19081",
        }
    ) == ("127.0.0.1", 19081)


def _request(server, method, path, payload=None):
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        server.server_address[1],
        timeout=2.0,
    )
    body = None if payload is None else json.dumps(payload)
    headers = {} if body is None else {"Content-Type": "application/json"}
    connection.request(method, path, body=body, headers=headers)
    response = connection.getresponse()
    response_body = json.loads(response.read().decode("utf-8"))
    connection.close()
    return response.status, response_body


@pytest.mark.parametrize("source", [1, 2, 3])
def test_post_task_accepts_supported_sources(hil_http_server, source):
    server, _adapter, _ingress, node, _events = hil_http_server
    status, body = _request(
        server,
        "POST",
        "/task",
        _valid_task(source=source),
    )
    assert status == 202
    assert body == {
        "accepted_for_delivery": True,
        "task_id": 30,
        "source": source,
        "command_id": "voice-hil-001",
    }
    assert node.publisher.messages == []
    assert node.mission_callback_calls == 0


@pytest.mark.parametrize("source", [0, 4, -1, 256, "1", True])
def test_post_task_rejects_invalid_source(hil_http_server, source):
    server, _adapter, _ingress, _node, _events = hil_http_server
    status, body = _request(
        server,
        "POST",
        "/task",
        _valid_task(source=source),
    )
    assert status == 400
    assert body["error"] == "invalid_request"


@pytest.mark.parametrize("confidence", [-0.01, 1.01, "0.9", True])
def test_post_task_rejects_invalid_confidence(
    hil_http_server,
    confidence,
):
    server, _adapter, _ingress, _node, _events = hil_http_server
    status, body = _request(
        server,
        "POST",
        "/task",
        _valid_task(confidence=confidence),
    )
    assert status == 400
    assert body["error"] == "invalid_request"


def test_post_task_requires_task_id(hil_http_server):
    server, _adapter, _ingress, _node, _events = hil_http_server
    payload = _valid_task()
    payload.pop("task_id")
    status, body = _request(server, "POST", "/task", payload)
    assert status == 400
    assert body["error"] == "invalid_request"


@pytest.mark.parametrize("valid_for_sec", [0, -0.1, 1e300])
def test_post_task_rejects_nonpositive_validity(
    hil_http_server,
    valid_for_sec,
):
    server, _adapter, _ingress, _node, _events = hil_http_server
    status, body = _request(
        server,
        "POST",
        "/task",
        _valid_task(valid_for_sec=valid_for_sec),
    )
    assert status == 400
    assert body["error"] == "invalid_request"


def test_ros_drain_publishes_standard_task_command_with_j6_stamp(
    hil_http_server,
):
    server, _adapter, ingress, node, _events = hil_http_server
    payload = _valid_task(
        source=2,
        command_id="app.hil:002",
        confidence=1.0,
        valid_for_sec=60.25,
        user_confirmed=True,
    )
    status, _body = _request(server, "POST", "/task", payload)

    assert status == 202
    assert node.clock.calls == 0
    assert node.publisher.messages == []
    assert node.mission_callback_calls == 0

    ingress.drain_task_queue()

    assert node.clock.calls == 1
    assert len(node.publisher.messages) == 1
    message = node.publisher.messages[0]
    assert isinstance(message, TaskCommand)
    assert message.header.stamp == node.clock.stamp
    assert message.header.frame_id == ""
    assert message.interface_version == "1.0"
    assert message.command_id == "app.hil:002"
    assert message.source == TaskCommand.SOURCE_APP
    assert message.task_id == 30
    assert message.confidence == pytest.approx(1.0)
    assert message.raw_text == "清扫最近的落叶"
    assert message.valid_for.sec == 60
    assert message.valid_for.nanosec == 250000000
    assert message.user_confirmed is True
    assert node.mission_callback_calls == 0
    assert "HIL_TASK_COMMAND_PUBLISHED" in node.logger.messages[-1]


def test_task_publisher_uses_formal_topic_and_matching_qos():
    node = _FakeNode()
    ingress = HilTaskIngress(node)
    qos = ingress.qos
    assert ingress.topic == TASK_COMMAND_TOPIC
    assert node.publisher_topic == "/cleannav/hmi/task_command"
    assert node.publisher_type is TaskCommand
    assert qos.history == HistoryPolicy.KEEP_LAST
    assert qos.depth == 10
    assert qos.reliability == ReliabilityPolicy.RELIABLE
    assert qos.durability == DurabilityPolicy.VOLATILE


def test_health_reports_task_ingress_without_breaking_existing_fields(
    hil_http_server,
):
    server, _adapter, _ingress, _node, _events = hil_http_server
    status, body = _request(server, "GET", "/health")
    assert status == 200
    assert body["ok"] is True
    assert body["bridge"] == "cleannav_hil_navigation_runner"
    assert body["task_ingress"] is True


def test_nav_events_and_result_endpoints_still_deliver_on_drain(
    hil_http_server,
):
    server, adapter, _ingress, _node, navigation_events = hil_http_server
    handle = GenerationHandle(execution_id="hil-execution", generation=1)
    adapter.submit_goal(handle, PoseStamped())

    status, event_body = _request(server, "GET", "/nav/events?after=0")
    assert status == 200
    assert event_body["count"] == 1
    nav_request_id = event_body["events"][0]["nav_request_id"]

    status, result_body = _request(
        server,
        "POST",
        "/nav/result",
        {
            "nav_request_id": nav_request_id,
            "event": "SUCCEEDED",
            "payload": {"from": "pc"},
        },
    )
    assert status == 202
    assert result_body["accepted_for_delivery"] is True
    assert navigation_events == []

    adapter.drain_result_queue()
    assert len(navigation_events) == 1
    assert navigation_events[0].handle == handle
    assert navigation_events[0].event_type is NavigationEventType.SUCCEEDED
    assert navigation_events[0].payload == {"from": "pc"}


def test_unknown_http_path_remains_404(hil_http_server):
    server, _adapter, _ingress, _node, _events = hil_http_server
    status, body = _request(server, "GET", "/unknown")
    assert status == 404
    assert body == {"error": "not_found"}
