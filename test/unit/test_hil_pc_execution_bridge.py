"""Focused tests for the J6-to-PC Gazebo HIL execution bridge."""

import json
from types import SimpleNamespace

from cleannav_interfaces.msg import TaskStatus

import cleannav_mission_manager.demo_hil_pc_execution_bridge as hil_pc
from cleannav_mission_manager.demo_hil_pc_execution_bridge import (
    HilPcExecutionBridge,
    J6NavigationHttpClient,
    resolve_j6_base_url,
)


class _Logger:
    def __init__(self):
        self.info_messages = []
        self.warning_messages = []
        self.error_messages = []

    def info(self, message):
        self.info_messages.append(message)

    def warning(self, message):
        self.warning_messages.append(message)

    def error(self, message):
        self.error_messages.append(message)


class _Node:
    def __init__(self):
        self.logger = _Logger()

    def get_logger(self):
        return self.logger


class _HttpClient:
    base_url = "http://j6.test:18081"

    def __init__(self, batches=None):
        self.batches = list(batches or [])
        self.get_calls = []
        self.posts = []
        self.get_failures = 0
        self.post_failures = 0
        self.post_status = 202
        self.task_posts = 0

    def get_navigation_events(self, after_event_id):
        self.get_calls.append(after_event_id)
        if self.get_failures:
            self.get_failures -= 1
            raise RuntimeError("offline")
        if self.batches:
            return {"events": self.batches.pop(0)}
        return {"events": []}

    def post_navigation_result(self, **payload):
        if self.post_failures:
            self.post_failures -= 1
            raise RuntimeError("offline")
        self.posts.append(payload)
        return self.post_status


class _ExecutionStarter:
    def __init__(self, accepted=True):
        self.calls = []
        self.accepted = accepted

    def __call__(self, node, *, command_id):
        self.calls.append({"node": node, "command_id": command_id})
        return SimpleNamespace(
            accepted=self.accepted,
            execution_id="pc-execution-1" if self.accepted else None,
        )


class _UrlopenResponse:
    def __init__(self, status, payload):
        self.status = status
        self._data = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self._data


def _submit_event(event_id=1, nav_request_id="j6-execution:g1"):
    return {
        "event_id": event_id,
        "operation": "SUBMIT_GOAL",
        "nav_request_id": nav_request_id,
        "goal": {
            "kind": "pose_stamped",
            "position": {"x": -4.0, "y": 0.0, "z": 0.0},
        },
    }


def _bridge(events, *, accepted=True):
    node = _Node()
    client = _HttpClient([events])
    starter = _ExecutionStarter(accepted=accepted)
    bridge = HilPcExecutionBridge(
        node,
        http_client=client,
        execution_starter=starter,
    )
    return bridge, node, client, starter


def _terminal_status(bridge, state):
    active = bridge.active_execution
    message = TaskStatus()
    message.status_scope = TaskStatus.SCOPE_EXECUTION
    message.execution_id = active.pc_execution_id
    message.command_id = active.command_id
    message.task_id = 30
    message.state = state
    return message


def test_event_polling_starts_frozen_task30_without_using_old_goal():
    bridge, node, client, starter = _bridge([_submit_event()])

    bridge.poll_once()

    assert client.get_calls == [0]
    assert bridge.last_event_id == 1
    assert starter.calls == []
    bridge.drain_execution_queue()
    assert starter.calls == [{"node": node, "command_id": "hil-pc-1"}]
    assert bridge.active_execution.pc_execution_id == "pc-execution-1"
    assert all("goal" not in call for call in starter.calls)
    assert any(
        "J6_NAV_EVENT_RECEIVED nav_request_id=j6-execution:g1"
        in message
        for message in node.logger.info_messages
    )
    assert any(
        "PC_TASK30_EXECUTION_STARTED" in message
        for message in node.logger.info_messages
    )


def test_standard_library_client_polls_expected_j6_endpoint(monkeypatch):
    calls = []

    def fake_urlopen(request, *, timeout):
        calls.append((request, timeout))
        return _UrlopenResponse(200, {"events": [], "count": 0})

    monkeypatch.setattr(hil_pc, "urlopen", fake_urlopen)
    client = J6NavigationHttpClient(
        "http://192.168.8.10:18081/",
        timeout_sec=2.5,
    )
    assert client.get_navigation_events(17) == {
        "events": [],
        "count": 0,
    }
    request, timeout = calls[0]
    assert request.full_url == (
        "http://192.168.8.10:18081/nav/events?after=17"
    )
    assert request.get_method() == "GET"
    assert timeout == 2.5


def test_j6_base_url_cli_precedes_environment_and_default(monkeypatch):
    monkeypatch.setenv(
        "CLEANNAV_J6_HIL_BASE_URL",
        "http://env-j6:19081/",
    )
    assert resolve_j6_base_url() == "http://env-j6:19081"
    assert resolve_j6_base_url("http://cli-j6:29081/") == (
        "http://cli-j6:29081"
    )
    monkeypatch.delenv("CLEANNAV_J6_HIL_BASE_URL")
    assert resolve_j6_base_url() == "http://192.168.8.10:18081"


def test_standard_library_client_posts_only_navigation_result(monkeypatch):
    calls = []

    def fake_urlopen(request, *, timeout):
        calls.append((request, timeout))
        return _UrlopenResponse(202, {"accepted_for_delivery": True})

    monkeypatch.setattr(hil_pc, "urlopen", fake_urlopen)
    client = J6NavigationHttpClient()
    status = client.post_navigation_result(
        nav_request_id="j6-execution:g1",
        event="SUCCEEDED",
        payload={"detail": "PC Gazebo task30 showcase completed"},
    )
    assert status == 202
    request, _timeout = calls[0]
    assert request.full_url == "http://192.168.8.10:18081/nav/result"
    assert request.get_method() == "POST"
    assert json.loads(request.data.decode("utf-8")) == {
        "nav_request_id": "j6-execution:g1",
        "event": "SUCCEEDED",
        "payload": {"detail": "PC Gazebo task30 showcase completed"},
    }


def test_only_submit_goal_operations_trigger_pc_execution():
    events = [
        {
            "event_id": 1,
            "operation": "CANCEL_GOAL",
            "nav_request_id": "j6-execution:g1",
        },
        {
            "event_id": 2,
            "operation": "IGNORED",
            "nav_request_id": "j6-execution:g1",
        },
    ]
    bridge, _node, _client, starter = _bridge(events)
    bridge.poll_once()
    bridge.drain_execution_queue()
    assert starter.calls == []
    assert bridge.last_event_id == 2


def test_duplicate_event_id_never_retriggers_execution():
    event = _submit_event()
    node = _Node()
    client = _HttpClient([[event, event], [event]])
    starter = _ExecutionStarter()
    bridge = HilPcExecutionBridge(
        node,
        http_client=client,
        execution_starter=starter,
    )
    bridge.poll_once()
    bridge.poll_once()
    bridge.drain_execution_queue()
    assert len(starter.calls) == 1


def test_real_pc_terminal_success_posts_j6_success_with_original_request_id():
    bridge, node, client, _starter = _bridge([_submit_event()])
    bridge.poll_once()
    bridge.drain_execution_queue()

    bridge.on_task_status(
        _terminal_status(bridge, TaskStatus.STATE_SUCCEEDED)
    )

    assert client.posts == []
    assert bridge.post_one_result() is True
    assert client.posts == [
        {
            "nav_request_id": "j6-execution:g1",
            "event": "SUCCEEDED",
            "payload": {
                "detail": "PC Gazebo task30 showcase completed",
            },
        }
    ]
    assert any(
        "PC_TASK30_EXECUTION_SUCCEEDED" in message
        for message in node.logger.info_messages
    )
    assert any(
        "J6_NAV_RESULT_POSTED" in message and "status=202" in message
        for message in node.logger.info_messages
    )


def test_real_pc_terminal_failure_posts_j6_failed():
    bridge, node, client, _starter = _bridge([_submit_event()])
    bridge.poll_once()
    bridge.drain_execution_queue()

    bridge.on_task_status(
        _terminal_status(bridge, TaskStatus.STATE_FAILED)
    )
    assert bridge.post_one_result() is True
    assert client.posts[0]["nav_request_id"] == "j6-execution:g1"
    assert client.posts[0]["event"] == "FAILED"
    assert client.posts[0]["payload"]["pc_task_state"] == (
        TaskStatus.STATE_FAILED
    )
    assert any(
        "PC_TASK30_EXECUTION_FAILED" in message
        for message in node.logger.error_messages
    )


def test_pc_start_rejection_posts_failed_instead_of_faking_motion():
    bridge, _node, client, starter = _bridge(
        [_submit_event()],
        accepted=False,
    )
    bridge.poll_once()
    bridge.drain_execution_queue()
    assert len(starter.calls) == 1
    assert bridge.active_execution is None
    assert bridge.post_one_result() is True
    assert client.posts[0]["event"] == "FAILED"


def test_http_poll_failure_is_logged_and_does_not_start_execution():
    bridge, node, client, starter = _bridge([])
    client.get_failures = 1
    bridge.poll_once()
    assert starter.calls == []
    assert bridge.last_event_id == 0
    assert any(
        "HIL_PC_NETWORK_WARNING event polling failed" in message
        for message in node.logger.warning_messages
    )


def test_result_post_failure_is_retained_for_retry():
    bridge, _node, client, _starter = _bridge([_submit_event()])
    bridge.poll_once()
    bridge.drain_execution_queue()
    bridge.on_task_status(
        _terminal_status(bridge, TaskStatus.STATE_SUCCEEDED)
    )
    client.post_failures = 1
    assert bridge.post_one_result() is False
    assert client.posts == []
    assert bridge.post_one_result() is True
    assert len(client.posts) == 1


def test_bridge_never_posts_task_command_back_to_j6():
    bridge, _node, client, _starter = _bridge([_submit_event()])
    bridge.poll_once()
    bridge.drain_execution_queue()
    bridge.on_task_status(
        _terminal_status(bridge, TaskStatus.STATE_SUCCEEDED)
    )
    bridge.post_one_result()
    assert client.task_posts == 0
    assert all(
        set(post) == {"nav_request_id", "event", "payload"}
        for post in client.posts
    )


def test_unrelated_task_status_cannot_complete_active_j6_request():
    bridge, _node, client, _starter = _bridge([_submit_event()])
    bridge.poll_once()
    bridge.drain_execution_queue()
    unrelated = _terminal_status(bridge, TaskStatus.STATE_SUCCEEDED)
    unrelated.command_id = "some-other-command"
    bridge.on_task_status(unrelated)
    assert bridge.active_execution is not None
    assert bridge.post_one_result() is False
    assert client.posts == []
