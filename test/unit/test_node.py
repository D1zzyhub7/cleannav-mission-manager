"""Unit tests for the Mission Manager ROS glue node."""

import re
import inspect

import pytest
import rclpy
from rclpy.parameter import Parameter
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    ReliabilityPolicy,
)

from cleannav_interfaces.msg import TaskCommand, TaskStatus

from cleannav_mission_manager.adapters.navigation import (
    NavigationEvent,
    NavigationEventType,
)
from cleannav_mission_manager.core import CoreResult
from cleannav_mission_manager.domain.command_processing import (
    CommandReason,
    NormalizedTaskCommand,
)
from cleannav_mission_manager.node import MissionManagerNode


@pytest.fixture(scope='module', autouse=True)
def ros_context():
    """Provide one local ROS context without starting any external node."""
    if not rclpy.ok():
        rclpy.init(args=None)
    yield
    if rclpy.ok():
        rclpy.shutdown()


def _command(node, *, task_id=30, frame_id=''):
    message = TaskCommand()
    message.header.stamp = node.get_clock().now().to_msg()
    message.header.frame_id = frame_id
    message.interface_version = '1.0'
    message.command_id = f'command-{task_id}'
    message.source = TaskCommand.SOURCE_APP
    message.task_id = task_id
    message.confidence = 1.0
    message.raw_text = 'mock command'
    message.valid_for.sec = 60
    return message


class StubCore:
    """Capture normalized command input for conversion boundary tests."""

    def __init__(self):
        self.commands = []

    def submit_command(self, command):
        self.commands.append(command)
        return CoreResult(
            accepted=True,
            reason_code=CommandReason.COMMAND_VALID,
        )

    def handle_navigation_event(self, event):
        return CoreResult(
            accepted=True,
            reason_code=CommandReason.NONE,
        )

    def handle_safety_event(self, event):
        return CoreResult(
            accepted=True,
            reason_code=CommandReason.NONE,
        )


def _node(**kwargs):
    return MissionManagerNode(
        parameter_overrides=[
            Parameter('runtime_mode', value='mock'),
        ],
        **kwargs,
    )


def test_node_name_and_explicit_mock_runtime():
    node = _node()
    try:
        assert node.get_name() == 'mission_manager_node'
        assert node.runtime_mode == 'mock'
        assert node.runtime is not None
    finally:
        node.destroy_node()


def test_topics_and_qos_match_frozen_contract():
    node = _node()
    try:
        assert node.task_command_topic == '/cleannav/hmi/task_command'
        assert node.task_status_topic == '/cleannav/task_status'

        command_qos = node._task_command_subscription.qos_profile
        assert command_qos.reliability == ReliabilityPolicy.RELIABLE
        assert command_qos.durability == DurabilityPolicy.VOLATILE
        assert command_qos.history == HistoryPolicy.KEEP_LAST
        assert command_qos.depth == 10

        status_qos = node._task_status_publisher.qos_profile
        assert status_qos.reliability == ReliabilityPolicy.RELIABLE
        assert status_qos.durability == DurabilityPolicy.TRANSIENT_LOCAL
        assert status_qos.history == HistoryPolicy.KEEP_LAST
        assert status_qos.depth == 10
    finally:
        node.destroy_node()


def test_task_command_callback_reuses_converter_and_calls_core():
    core = StubCore()
    node = _node(core=core)
    try:
        message = _command(node)
        result = node._on_task_command(message)

        assert result.accepted
        assert len(core.commands) == 1
        assert isinstance(core.commands[0], NormalizedTaskCommand)
        assert core.commands[0].task_id == 30
    finally:
        node.destroy_node()


def test_invalid_frame_id_does_not_call_core_or_crash():
    core = StubCore()
    node = _node(core=core)
    try:
        result = node._on_task_command(_command(node, frame_id='map'))

        assert result is None
        assert core.commands == []
    finally:
        node.destroy_node()


def test_disabled_task_rejection_is_returned_by_core():
    node = _node()
    try:
        result = node._on_task_command(_command(node, task_id=1))

        assert result is not None
        assert not result.accepted
        assert result.reason_code is CommandReason.TASK_DISABLED
    finally:
        node.destroy_node()


def test_task_status_is_published_from_current_store_snapshots():
    node = _node()
    published = []
    node._publish_status_message = published.append
    try:
        result = node._on_task_command(_command(node))

        assert result.accepted
        assert [message.status_scope for message in published] == [1, 2]
        assert published[0].execution_id != ''
        assert published[1].execution_id != ''
        assert all(
            message.header.stamp.sec >= 0
            for message in published
        )
    finally:
        node.destroy_node()


def test_adapter_events_flow_through_core_and_publish_status():
    node = _node()
    published = []
    node._publish_status_message = published.append
    try:
        result = node._on_task_command(_command(node))
        assert result.accepted
        published.clear()

        runtime = node.runtime
        handle = runtime.core.active_generation_handle
        assert handle is not None
        runtime.navigation.inject_event(
            NavigationEvent(
                handle=handle,
                event_type=NavigationEventType.GOAL_ACCEPTED,
            )
        )
        assert published
        assert any(
            message.state == TaskStatus.STATE_NAVIGATING
            for message in published
        )

        published.clear()
        runtime.safety.emit_acquire_result(handle.execution_id)
        assert published
        assert any(
            message.state == TaskStatus.STATE_NAVIGATING
            for message in published
        )
    finally:
        node.destroy_node()


def test_robot_status_topic_is_not_published():
    node = _node()
    try:
        topics = {
            topic
            for topic, _ in node.get_topic_names_and_types()
        }
        assert '/cleannav/robot_status' not in topics
    finally:
        node.destroy_node()


@pytest.mark.parametrize('runtime_mode', ['production', 'unsupported'])
def test_non_mock_runtime_fails_without_silent_fallback(runtime_mode):
    with pytest.raises(
        RuntimeError,
        match='production runtime is not available',
    ):
        MissionManagerNode(
            parameter_overrides=[
                Parameter('runtime_mode', value=runtime_mode),
            ],
        )


def test_node_source_has_no_forbidden_business_or_runtime_logic():
    from cleannav_mission_manager import node as node_module

    source = inspect.getsource(node_module)
    for forbidden in (
        'HTTP',
        'BLE',
        'Ackermann',
        'MPPI',
        'Hybrid-A*',
        'CAN',
        '/cmd_vel',
        'NavigateToPose',
        'FollowPath',
        'ActionClient',
    ):
        assert re.search(
            rf'(?<![A-Za-z0-9_]){re.escape(forbidden)}'
            rf'(?![A-Za-z0-9_])',
            source,
        ) is None

    assert 'if task_id ==' not in source
