"""Tests for the independent PC offline runtime composition."""

from types import SimpleNamespace
from pathlib import Path

import pytest
import rclpy
from geometry_msgs.msg import PoseStamped
from rclpy.parameter import Parameter

from cleannav_mission_manager.adapters.mock_safety import MockSafetyAdapter
from cleannav_mission_manager.adapters.real_navigation import (
    RealNavigationAdapter,
)
from cleannav_mission_manager.adapters.real_safety import RealSafetyAdapter
from cleannav_mission_manager.core import GoalResolutionError
from cleannav_mission_manager.demo_hil_navigation_runner import (
    HilNavigationAdapter,
    create_hil_runtime,
)
from cleannav_mission_manager.demo_pc_offline_runner import (
    create_pc_offline_runtime,
    make_pc_offline_demo_goal,
)
from cleannav_mission_manager.node import MissionManagerNode


@pytest.fixture(scope='module', autouse=True)
def ros_context():
    """Provide one local ROS context without starting external nodes."""
    if not rclpy.ok():
        rclpy.init(args=None)
    yield
    if rclpy.ok():
        rclpy.shutdown()


def _node(*, runtime_factory=None):
    return MissionManagerNode(
        parameter_overrides=[
            Parameter('runtime_mode', value='mock'),
        ],
        runtime_factory=runtime_factory,
    )


def _task(task_id):
    return SimpleNamespace(task_id=task_id)


def _command(command_id='pc-offline-test'):
    return SimpleNamespace(command_id=command_id)


def test_pc_offline_runtime_wires_real_navigation_and_safety():
    node = _node(runtime_factory=create_pc_offline_runtime)
    try:
        runtime = node.runtime
        assert runtime is not None
        assert isinstance(runtime.navigation, RealNavigationAdapter)
        assert isinstance(runtime.safety, RealSafetyAdapter)
        assert not isinstance(runtime.safety, MockSafetyAdapter)
        assert runtime.core._goal_resolver is make_pc_offline_demo_goal
    finally:
        node.destroy_node()


def test_pc_offline_goal_resolver_returns_frozen_map_pose():
    goal = make_pc_offline_demo_goal(_command(), _task(30))

    assert isinstance(goal, PoseStamped)
    assert goal.header.frame_id == 'map'
    assert goal.pose.position.x == pytest.approx(-4.0)
    assert goal.pose.position.y == pytest.approx(0.0)
    assert goal.pose.position.z == pytest.approx(0.0)
    assert goal.pose.orientation.w == pytest.approx(1.0)


def test_pc_offline_goal_resolver_rejects_unsupported_task_deterministically():
    for _ in range(2):
        with pytest.raises(
            GoalResolutionError,
            match='PC offline demo goal is unsupported for task_id=31',
        ):
            make_pc_offline_demo_goal(_command(), _task(31))


def test_default_node_remains_mock_runtime():
    node = _node()
    try:
        assert node.runtime is not None
        assert isinstance(node.runtime.safety, MockSafetyAdapter)
        assert not isinstance(node.runtime.safety, RealSafetyAdapter)
    finally:
        node.destroy_node()


def test_hil_runtime_keeps_hil_navigation_and_mock_safety():
    node = _node(runtime_factory=create_hil_runtime)
    try:
        runtime = node.runtime
        assert runtime is not None
        assert isinstance(runtime.navigation, HilNavigationAdapter)
        assert isinstance(runtime.safety, MockSafetyAdapter)
        assert not isinstance(runtime.safety, RealSafetyAdapter)
    finally:
        node.destroy_node()


def test_pc_demo_entrypoint_and_launch_reuse_task_command_runtime():
    package_root = Path(__file__).resolve().parents[2]
    setup_source = (package_root / 'setup.py').read_text(encoding='utf-8')
    launch_source = (
        package_root / 'launch' / 'pc_demo.launch.py'
    ).read_text(encoding='utf-8')

    assert 'cleannav_pc_demo =' in setup_source
    assert 'demo_pc_offline_runner:main' in setup_source
    assert "executable='cleannav_pc_demo'" in launch_source
    assert "use_sim_time" in launch_source


def test_pc_demo_lifecycle_tokens_are_emitted_by_ros_glue_source():
    package_root = Path(__file__).resolve().parents[2]
    source = (
        package_root / 'cleannav_mission_manager' / 'node.py'
    ).read_text(encoding='utf-8')
    for token in (
        'TASK_RECEIVED',
        'EXECUTION_CREATED',
        'SAFETY_LEASE_ACQUIRED',
        'NAVIGATION_STARTED',
        'NAVIGATION_SUCCEEDED',
        'NAVIGATION_FAILED',
        'SAFETY_LEASE_RELEASED',
        'EXECUTION_FINISHED',
    ):
        assert token in source
