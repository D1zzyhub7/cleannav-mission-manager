"""Unit tests for the Mission Manager ROS glue node."""

import re
import inspect

import pytest
import rclpy
from geometry_msgs.msg import PoseStamped
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
from cleannav_mission_manager.adapters.mock_navigation import (
    MockNavigationAdapter,
)
from cleannav_mission_manager.adapters.mock_safety import MockSafetyAdapter
from cleannav_mission_manager.adapters.real_navigation import (
    RealNavigationAdapter,
)
from cleannav_mission_manager.adapters.real_safety import RealSafetyAdapter
from cleannav_mission_manager.core import CoreResult
from cleannav_mission_manager.domain.command_processing import (
    CommandReason,
    CommandSource,
    NormalizedTaskCommand,
)
from cleannav_mission_manager.domain.command_store import CommandRecordStore
from cleannav_mission_manager.domain.execution_store import (
    ExecutionRecordStore,
)
from cleannav_mission_manager.domain.generation_gate import GenerationGate
from cleannav_mission_manager.domain.mission_queue import MissionQueue
from cleannav_mission_manager.node import MissionManagerNode
from cleannav_mission_manager.domain.runtime_policy import (
    MIN_CONFIDENCE_BY_SOURCE,
    VOICE_MIN_CONFIDENCE,
)


@pytest.fixture(scope='module', autouse=True)
def ros_context():
    """Provide one local ROS context without starting any external node."""
    if not rclpy.ok():
        rclpy.init(args=None)
    yield
    if rclpy.ok():
        rclpy.shutdown()


def _command(
    node,
    *,
    task_id=30,
    frame_id='',
    source=TaskCommand.SOURCE_APP,
    confidence=1.0,
    command_id=None,
):
    message = TaskCommand()
    message.header.stamp = node.get_clock().now().to_msg()
    message.header.frame_id = frame_id
    message.interface_version = '1.0'
    message.command_id = command_id or f'command-{task_id}'
    message.source = source
    message.task_id = task_id
    message.confidence = confidence
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
        assert isinstance(node.runtime.navigation, MockNavigationAdapter)
        assert isinstance(node.runtime.safety, MockSafetyAdapter)
    finally:
        node.destroy_node()


def test_default_runtime_composition_contains_voice_threshold_policy():
    node = _node()
    try:
        assert MIN_CONFIDENCE_BY_SOURCE == {
            int(CommandSource.VOICE): VOICE_MIN_CONFIDENCE,
        }
        assert node.runtime is not None
        validator = node.runtime.core._validator
        assert validator._min_confidence_by_source == {
            int(CommandSource.VOICE): 0.80,
        }
    finally:
        node.destroy_node()


def test_explicit_real_runtime_wires_real_adapters_and_injected_resolver():
    def resolver(command, task):
        pose = PoseStamped()
        pose.header.frame_id = 'map'
        pose.pose.orientation.w = 1.0
        return pose

    node = _node(
        runtime_factory=(
            lambda current: current.create_real_runtime(resolver)
        ),
    )
    try:
        runtime = node.runtime
        assert runtime is not None
        assert isinstance(runtime.navigation, RealNavigationAdapter)
        assert isinstance(runtime.safety, RealSafetyAdapter)
        assert not isinstance(runtime.navigation, MockNavigationAdapter)
        assert not isinstance(runtime.safety, MockSafetyAdapter)
        assert runtime.core._goal_resolver is resolver
        assert runtime.core._navigation is runtime.navigation
        assert runtime.core._safety is runtime.safety
    finally:
        node.destroy_node()


def test_real_runtime_uses_common_stores_and_preserves_execution_id_factory():
    def resolver(command, task):
        return PoseStamped()

    node = _node(
        runtime_factory=(
            lambda current: current.create_real_runtime(resolver)
        ),
    )
    try:
        runtime = node.runtime
        assert runtime is not None
        assert isinstance(runtime.command_store, CommandRecordStore)
        assert isinstance(runtime.execution_store, ExecutionRecordStore)
        assert isinstance(runtime.core._mission_queue, MissionQueue)
        assert isinstance(runtime.core._generation_gate, GenerationGate)
        assert runtime.execution_store._id_factory() == 'ros-execution-1'
        assert runtime.core._validator._min_confidence_by_source == {
            int(CommandSource.VOICE): VOICE_MIN_CONFIDENCE,
        }
    finally:
        node.destroy_node()


def test_real_runtime_adapter_event_sinks_return_to_node_callbacks():
    def resolver(command, task):
        return PoseStamped()

    node = _node(
        runtime_factory=(
            lambda current: current.create_real_runtime(resolver)
        ),
    )
    try:
        runtime = node.runtime
        assert runtime is not None

        navigation_sink = runtime.navigation._event_sink
        safety_sink = runtime.safety._event_sink
        assert navigation_sink.__self__ is node
        assert (
            navigation_sink.__func__
            is MissionManagerNode._on_navigation_event
        )
        assert safety_sink.__self__ is node
        assert safety_sink.__func__ is MissionManagerNode._on_safety_event
    finally:
        node.destroy_node()


def test_topics_and_qos_match_frozen_contract():
    node = _node()
    try:
        assert node.task_command_topic == '/cleannav/hmi/task_command'
        assert node.task_status_topic == '/cleannav/task_status'

        command_qos = node._task_command_subscription.qos_profile
        callback = node._task_command_subscription.callback
        assert callback.__self__ is node
        assert callback.__func__ is MissionManagerNode._on_task_command
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


def test_low_confidence_voice_is_rejected_without_runtime_side_effects():
    node = _node()
    published = []
    node._publish_status_message = published.append
    try:
        result = node._on_task_command(
            _command(
                node,
                source=TaskCommand.SOURCE_VOICE,
                confidence=0.79,
                command_id='voice-low-confidence',
            )
        )

        assert result is not None
        assert not result.accepted
        assert result.reason_code is CommandReason.CONFIDENCE_TOO_LOW
        assert len(published) == 1
        assert published[0].status_scope == TaskStatus.SCOPE_COMMAND
        assert published[0].state == TaskStatus.STATE_REJECTED
        assert published[0].reason_code == (
            TaskStatus.REASON_CONFIDENCE_TOO_LOW
        )
        assert published[0].command_id == 'voice-low-confidence'

        runtime = node.runtime
        assert runtime is not None
        assert runtime.command_store.get_record(
            'voice-low-confidence'
        ) is None
        assert runtime.execution_store.get_active() is None
        assert runtime.core.queued_missions == ()
        assert runtime.navigation.submitted_calls == ()
        assert runtime.safety.calls == ()
    finally:
        node.destroy_node()


@pytest.mark.parametrize('confidence', [0.80, 1.0])
def test_voice_confidence_boundary_is_admitted_by_default_runtime(
    confidence,
):
    node = _node()
    try:
        result = node._on_task_command(
            _command(
                node,
                source=TaskCommand.SOURCE_VOICE,
                confidence=confidence,
                command_id=f'voice-confidence-{confidence}',
            )
        )

        assert result is not None
        assert result.accepted
        assert result.reason_code is not CommandReason.CONFIDENCE_TOO_LOW
    finally:
        node.destroy_node()


def test_app_low_confidence_is_not_rejected_by_voice_threshold():
    node = _node()
    try:
        result = node._on_task_command(
            _command(
                node,
                source=TaskCommand.SOURCE_APP,
                confidence=0.10,
                command_id='app-low-confidence',
            )
        )

        assert result is not None
        assert result.accepted
        assert result.reason_code is not CommandReason.CONFIDENCE_TOO_LOW
    finally:
        node.destroy_node()


def test_mock_low_confidence_is_not_rejected_by_voice_threshold():
    node = _node()
    try:
        result = node._on_task_command(
            _command(
                node,
                source=TaskCommand.SOURCE_MOCK,
                confidence=0.10,
                command_id='mock-low-confidence',
            )
        )

        assert result is not None
        assert result.accepted
        assert result.reason_code is not CommandReason.CONFIDENCE_TOO_LOW
    finally:
        node.destroy_node()


def test_voice_reset_estop_remains_source_forbidden():
    node = _node()
    try:
        result = node._on_task_command(
            _command(
                node,
                task_id=7,
                source=TaskCommand.SOURCE_VOICE,
                confidence=1.0,
                command_id='voice-reset-estop',
            )
        )

        assert result is not None
        assert not result.accepted
        assert result.reason_code is CommandReason.SOURCE_NOT_ALLOWED
    finally:
        node.destroy_node()


def test_unconfirmed_reset_publishes_ephemeral_rejected_command_status():
    node = _node()
    published = []
    node._publish_status_message = published.append
    try:
        result = node._on_task_command(
            _command(node, task_id=7)
        )

        assert result is not None
        assert not result.accepted
        assert result.reason_code is CommandReason.CONFIRMATION_REQUIRED
        assert len(published) == 1
        status = published[0]
        assert status.status_scope == TaskStatus.SCOPE_COMMAND
        assert status.state == TaskStatus.STATE_REJECTED
        assert status.reason_code == 115
        assert status.command_id == 'command-7'
        assert status.task_id == 7
        assert status.execution_id == ''
        assert node.runtime is not None
        assert node.runtime.command_store.get_record('command-7') is None
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
