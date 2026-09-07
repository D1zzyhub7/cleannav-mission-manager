"""SW-PER-2 tests for visual target ROS wiring."""

import math

import pytest
import rclpy
from cleannav_interfaces.msg import CleaningTarget, CleaningTargetArray
from geometry_msgs.msg import PoseWithCovarianceStamped
from rclpy.qos import qos_profile_sensor_data

from cleannav_mission_manager.domain.target_registry import TargetRegistry
from cleannav_mission_manager.node import MissionManagerNode
from cleannav_mission_manager.visual_target_ros_bridge import (
    LOCALIZATION_TOPIC,
    LatestRobotPoseProvider,
    PoseValidationError,
    VisualTargetRosBridge,
)


@pytest.fixture(scope='module', autouse=True)
def ros_context():
    """Provide one local ROS context without starting any external node."""
    if not rclpy.ok():
        rclpy.init(args=None)
    yield
    if rclpy.ok():
        rclpy.shutdown()


class _Logger:
    def __init__(self):
        self.warnings = []

    def warning(self, message):
        self.warnings.append(message)


class _BridgeNode:
    def __init__(self):
        self.logger = _Logger()
        self.subscriptions = []
        self.timers = []

    def create_subscription(self, message_type, topic, callback, qos):
        subscription = {
            'message_type': message_type,
            'topic': topic,
            'callback': callback,
            'qos': qos,
        }
        self.subscriptions.append(subscription)
        return subscription

    def create_timer(self, period, callback):
        timer = {'period': period, 'callback': callback}
        self.timers.append(timer)
        return timer

    def get_logger(self):
        return self.logger


class _Core:
    def __init__(self):
        self.retry_count = 0
        self.timeout_count = 0
        self.results = []

    def retry_waiting_goal_resolution(self):
        self.retry_count += 1
        result = object()
        self.results.append(result)
        return result

    def check_target_wait_timeout(self):
        self.timeout_count += 1
        result = object()
        self.results.append(result)
        return result


def _pose(*, frame_id='map', x=1.0, orientation_w=1.0):
    message = PoseWithCovarianceStamped()
    message.header.frame_id = frame_id
    message.pose.pose.position.x = x
    message.pose.pose.orientation.w = orientation_w
    return message


def _target_array(*, frame_id='map', target_count=0):
    message = CleaningTargetArray()
    message.interface_version = '1.0'
    message.header.frame_id = frame_id
    message.header.stamp.sec = 1
    message.header.stamp.nanosec = 0
    for index in range(target_count):
        target = CleaningTarget()
        target.interface_version = '1.0'
        target.header.frame_id = 'map'
        target.header.stamp.sec = index + 1
        target.header.stamp.nanosec = 0
        target.valid_for.sec = 10
        target.target_id = f'target-{index}'
        target.source = 'test-camera'
        target.target_type = 1
        target.observation_state = 2
        target.confidence = 0.9
        target.projection_valid = True
        target.centroid.x = float(index)
        message.targets.append(target)
    return message


def _bridge(core=None, node=None):
    node = node or _BridgeNode()
    core = core or _Core()
    bridge_results = []
    bridge = VisualTargetRosBridge(
        node=node,
        core=core,
        registry=TargetRegistry(),
        pose_provider=LatestRobotPoseProvider(),
        cleaning_target_topic='/test/cleaning_targets',
        on_core_result=bridge_results.append,
    )
    return bridge, node, core, bridge_results


def test_pose_provider_starts_empty_and_returns_latest_valid_pose():
    provider = LatestRobotPoseProvider()

    assert provider() is None
    provider.update(_pose(x=2.5))

    assert provider().x == 2.5
    assert provider().orientation_w == 1.0


@pytest.mark.parametrize(
    'message',
    [
        _pose(frame_id='odom'),
        _pose(x=math.nan),
        _pose(orientation_w=0.0),
    ],
)
def test_invalid_pose_does_not_overwrite_last_valid_pose(message):
    provider = LatestRobotPoseProvider()
    provider.update(_pose(x=3.0))

    with pytest.raises((PoseValidationError, ValueError)):
        provider.update(message)

    assert provider().x == 3.0


def test_invalid_pose_before_first_valid_input_leaves_provider_empty():
    provider = LatestRobotPoseProvider()

    with pytest.raises(PoseValidationError):
        provider.update(_pose(frame_id='odom'))

    assert provider() is None


def test_bridge_uses_explicit_target_topic_and_sensor_qos():
    bridge, node, _, bridge_results = _bridge()

    assert bridge.cleaning_target_topic == '/test/cleaning_targets'
    assert bridge.localization_topic == LOCALIZATION_TOPIC
    assert len(node.subscriptions) == 2
    assert all(
        subscription['qos'] == qos_profile_sensor_data
        for subscription in node.subscriptions
    )
    assert node.timers[0]['period'] == 0.1
    assert bridge_results == []


@pytest.mark.parametrize('topic', ['', '   '])
def test_bridge_rejects_missing_target_topic(topic):
    with pytest.raises(ValueError, match='cleaning_target_topic'):
        VisualTargetRosBridge(
            node=_BridgeNode(),
            core=_Core(),
            registry=TargetRegistry(),
            pose_provider=LatestRobotPoseProvider(),
            cleaning_target_topic=topic,
        )


def test_target_and_pose_callbacks_retry_core_and_empty_array_preserves_registry():
    bridge, node, core, bridge_results = _bridge()
    target_callback = node.subscriptions[0]['callback']
    pose_callback = node.subscriptions[1]['callback']

    target_callback(_target_array(target_count=1))
    assert bridge.registry.get('target-0') is not None
    assert core.retry_count == 1

    target_callback(_target_array(target_count=0))
    assert bridge.registry.get('target-0') is not None
    assert core.retry_count == 2

    pose_callback(_pose(x=4.0))
    assert bridge.pose_provider().x == 4.0
    assert core.retry_count == 3
    assert bridge_results == core.results


def test_invalid_target_and_pose_callbacks_warn_without_core_retry():
    bridge, node, core, _ = _bridge()
    target_callback = node.subscriptions[0]['callback']
    pose_callback = node.subscriptions[1]['callback']

    target_callback(_target_array(frame_id='odom'))
    pose_callback(_pose(frame_id='odom'))

    assert core.retry_count == 0
    assert len(node.logger.warnings) == 2


def test_timer_uses_core_timeout_poll_and_forwards_result():
    bridge, node, core, bridge_results = _bridge()

    node.timers[0]['callback']()

    assert core.timeout_count == 1
    assert bridge_results == core.results


def test_node_visual_real_runtime_composes_real_adapters_and_bridge():
    node = MissionManagerNode(
        runtime_factory=(
            lambda current: current.create_visual_real_runtime(
                cleaning_target_topic='/test/cleaning_targets',
                localization_topic='/test/localization',
            )
        ),
    )
    try:
        runtime = node.runtime
        assert runtime is not None
        assert node.visual_target_bridge is not None
        assert node.visual_target_bridge.cleaning_target_topic == (
            '/test/cleaning_targets'
        )
        assert node.visual_target_bridge.localization_topic == (
            '/test/localization'
        )
        assert node._visual_target_registry is runtime.core._goal_resolver._registry
        assert (
            node._visual_target_pose_provider
            is runtime.core._goal_resolver._robot_pose_provider
        )
    finally:
        node.destroy_node()


def test_node_visual_real_runtime_requires_explicit_target_topic():
    node = MissionManagerNode()
    try:
        with pytest.raises(ValueError, match='cleaning_target_topic'):
            node.create_visual_real_runtime(cleaning_target_topic='')
    finally:
        node.destroy_node()
