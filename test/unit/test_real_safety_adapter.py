"""Unit tests for the ROS safety transport adapter."""

from __future__ import annotations

from pathlib import Path

import pytest
from cleannav_interfaces.srv import SafetyLease
from rclpy.task import Future
from std_msgs.msg import Bool
from std_srvs.srv import Trigger

from cleannav_mission_manager.adapters.real_safety import (
    SAFETY_ACQUIRE_LEASE_SERVICE,
    SAFETY_ESTOP_TOPIC,
    SAFETY_RELEASE_LEASE_SERVICE,
    SAFETY_RESET_ESTOP_SERVICE,
    RealSafetyAdapter,
)
from cleannav_mission_manager.adapters.safety import SafetyEventType


class FakeServiceClient:
    def __init__(self, *, ready=True):
        self.ready = ready
        self.futures = []
        self.requests = []

    def service_is_ready(self):
        return self.ready

    def call_async(self, request):
        future = Future()
        self.requests.append(request)
        self.futures.append(future)
        return future


class FakePublisher:
    def __init__(self):
        self.messages = []

    def publish(self, message):
        self.messages.append(message)


class FakeNode:
    def __init__(self):
        self.clients = []
        self.publishers = []

    def create_client(self, service_type, name):
        client = FakeServiceClient()
        self.clients.append((service_type, name, client))
        return client

    def create_publisher(self, message_type, name, qos):
        publisher = FakePublisher()
        self.publishers.append((message_type, name, qos, publisher))
        return publisher


def _adapter(*, acquire=None, release=None, reset=None, publisher=None):
    received = []
    values = [acquire, release, reset]
    if all(value is not None for value in values) and publisher is not None:
        node = object()
        adapter = RealSafetyAdapter(
            node,
            received.append,
            acquire_client=acquire,
            release_client=release,
            reset_client=reset,
            estop_publisher=publisher,
        )
        return adapter, received

    node = FakeNode()
    adapter = RealSafetyAdapter(node, received.append)
    return adapter, received


def test_endpoint_constants_are_frozen():
    assert SAFETY_ACQUIRE_LEASE_SERVICE == (
        '/cleannav/safety/acquire_lease'
    )
    assert SAFETY_RELEASE_LEASE_SERVICE == (
        '/cleannav/safety/release_lease'
    )
    assert SAFETY_RESET_ESTOP_SERVICE == (
        '/cleannav/safety/reset_emergency_stop'
    )
    assert SAFETY_ESTOP_TOPIC == '/cleannav/safety/emergency_stop'


@pytest.mark.parametrize('invalid_sink', [None, object(), 1])
def test_constructor_rejects_non_callable_sink(invalid_sink):
    with pytest.raises(TypeError):
        RealSafetyAdapter(
            object(),
            invalid_sink,
            acquire_client=FakeServiceClient(),
            release_client=FakeServiceClient(),
            reset_client=FakeServiceClient(),
            estop_publisher=FakePublisher(),
        )


def test_constructor_creates_frozen_ros_endpoints():
    node = FakeNode()
    RealSafetyAdapter(node, lambda event: None)

    assert [entry[1] for entry in node.clients] == [
        SAFETY_ACQUIRE_LEASE_SERVICE,
        SAFETY_RELEASE_LEASE_SERVICE,
        SAFETY_RESET_ESTOP_SERVICE,
    ]
    assert node.publishers[0][1] == SAFETY_ESTOP_TOPIC
    assert node.publishers[0][0] is Bool


@pytest.mark.parametrize('invalid_id', [None, '', 1])
def test_lease_methods_validate_execution_id(invalid_id):
    adapter, _ = _adapter()
    with pytest.raises((TypeError, ValueError)):
        adapter.acquire_lease(invalid_id)
    with pytest.raises((TypeError, ValueError)):
        adapter.release_lease(invalid_id)


def test_acquire_unavailable_emits_failed():
    client = FakeServiceClient(ready=False)
    adapter, received = _adapter(
        acquire=client,
        release=FakeServiceClient(),
        reset=FakeServiceClient(),
        publisher=FakePublisher(),
    )
    adapter.acquire_lease('execution-a')

    assert [(event.event_type, event.execution_id) for event in received] == [
        (SafetyEventType.LEASE_ACQUIRE_FAILED, 'execution-a'),
    ]


def test_acquire_success_and_failure_and_future_exception():
    client = FakeServiceClient()
    adapter, received = _adapter(
        acquire=client,
        release=FakeServiceClient(),
        reset=FakeServiceClient(),
        publisher=FakePublisher(),
    )
    adapter.acquire_lease('execution-a')
    client.futures[0].set_result(SafetyLease.Response(success=True))
    adapter.acquire_lease('execution-b')
    client.futures[1].set_result(SafetyLease.Response(success=False))
    adapter.acquire_lease('execution-c')
    client.futures[2].set_exception(RuntimeError('service failure'))

    assert [(event.event_type, event.execution_id) for event in received] == [
        (SafetyEventType.LEASE_ACQUIRED, 'execution-a'),
        (SafetyEventType.LEASE_ACQUIRE_FAILED, 'execution-b'),
        (SafetyEventType.LEASE_ACQUIRE_FAILED, 'execution-c'),
    ]


def test_release_unavailable_success_and_failure():
    client = FakeServiceClient()
    adapter, received = _adapter(
        acquire=FakeServiceClient(),
        release=client,
        reset=FakeServiceClient(),
        publisher=FakePublisher(),
    )
    client.ready = False
    adapter.release_lease('execution-a')
    client.ready = True
    adapter.release_lease('execution-a')
    client.futures[0].set_result(SafetyLease.Response(success=True))
    adapter.release_lease('execution-b')
    client.futures[1].set_result(SafetyLease.Response(success=False))

    assert [(event.event_type, event.execution_id) for event in received] == [
        (SafetyEventType.LEASE_RELEASE_FAILED, 'execution-a'),
        (SafetyEventType.LEASE_RELEASED, 'execution-a'),
        (SafetyEventType.LEASE_RELEASE_FAILED, 'execution-b'),
    ]


def test_late_old_execution_callback_is_forwarded_with_original_id():
    client = FakeServiceClient()
    adapter, received = _adapter(
        acquire=client,
        release=FakeServiceClient(),
        reset=FakeServiceClient(),
        publisher=FakePublisher(),
    )
    adapter.acquire_lease('execution-old')
    adapter.acquire_lease('execution-new')
    client.futures[1].set_result(SafetyLease.Response(success=True))
    client.futures[0].set_result(SafetyLease.Response(success=False))

    assert [(event.event_type, event.execution_id) for event in received] == [
        (SafetyEventType.LEASE_ACQUIRED, 'execution-new'),
        (SafetyEventType.LEASE_ACQUIRE_FAILED, 'execution-old'),
    ]


def test_request_emergency_stop_publishes_true_without_event():
    publisher = FakePublisher()
    adapter, received = _adapter(
        acquire=FakeServiceClient(),
        release=FakeServiceClient(),
        reset=FakeServiceClient(),
        publisher=publisher,
    )
    adapter.request_emergency_stop()

    assert len(publisher.messages) == 1
    assert isinstance(publisher.messages[0], Bool)
    assert publisher.messages[0].data is True
    assert received == []


def test_reset_unavailable_success_failure_and_no_execution_id():
    client = FakeServiceClient(ready=False)
    adapter, received = _adapter(
        acquire=FakeServiceClient(),
        release=FakeServiceClient(),
        reset=client,
        publisher=FakePublisher(),
    )
    adapter.reset_emergency_stop()
    client.ready = True
    adapter.reset_emergency_stop()
    client.futures[0].set_result(Trigger.Response(success=True))
    adapter.reset_emergency_stop()
    client.futures[1].set_result(Trigger.Response(success=False))

    assert [(event.event_type, event.execution_id) for event in received] == [
        (SafetyEventType.RESET_EMERGENCY_STOP_FAILED, None),
        (SafetyEventType.RESET_EMERGENCY_STOP_SUCCEEDED, None),
        (SafetyEventType.RESET_EMERGENCY_STOP_FAILED, None),
    ]


def test_production_source_has_no_policy_or_control_dependencies():
    source_path = (
        Path(__file__).resolve().parents[2]
        / 'cleannav_mission_manager'
        / 'adapters'
        / 'real_safety.py'
    )
    source = source_path.read_text(encoding='utf-8')
    for forbidden in (
        'MissionManagerCore',
        'GenerationGate',
        'state_machine',
        'cmd_vel',
        'Navigation',
        'MPPI',
        'Ackermann',
    ):
        assert forbidden not in source
    for forbidden in ('spin_until_future_complete', 'time.sleep', 'asyncio'):
        assert forbidden not in source
