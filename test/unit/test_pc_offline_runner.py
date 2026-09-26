"""Tests for the independent PC offline runtime composition."""

import math
import threading
from types import SimpleNamespace
from pathlib import Path

import pytest
import rclpy
from action_msgs.msg import GoalStatus
from cleannav_interfaces.msg import TaskCommand
from geometry_msgs.msg import Pose, PoseWithCovarianceStamped
from rclpy.parameter import Parameter

import cleannav_mission_manager.demo_pc_offline_runner as pc_demo
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
from cleannav_mission_manager.demo_hil_pc_execution_bridge import (
    PC_PRIVATE_TASK_COMMAND_TOPIC,
    PC_PRIVATE_TASK_STATUS_TOPIC,
)
from cleannav_mission_manager.demo_pc_offline_runner import (
    DEMO_CLEANING_DISTANCE_M,
    DEMO_LEAF_ENTITY,
    DEMO_LEAF_ENTITIES,
    DEMO_PASS_THROUGH_DISTANCE_M,
    DemoPhysicalCompletionNavigationAdapter,
    DemoWorldLeafGoalResolver,
    brush_centers_xy,
    cleaning_point_xy,
    create_pc_hil_execution_runtime,
    create_pc_offline_runtime,
    make_demo_task_command,
    make_world_relative_demo_goal,
    pass_through_goal_world_xy,
    physical_cleaning_distance,
)
from cleannav_mission_manager.adapters.navigation import (
    NavigationEvent,
    NavigationEventType,
)
from cleannav_mission_manager.domain.generation_gate import GenerationHandle
from cleannav_mission_manager.domain.goal_resolution import PendingGoal
from cleannav_mission_manager.domain.target_models import RobotPoseSnapshot
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
        assert isinstance(
            runtime.navigation,
            DemoPhysicalCompletionNavigationAdapter,
        )
        assert isinstance(runtime.safety, RealSafetyAdapter)
        assert not isinstance(runtime.safety, MockSafetyAdapter)
        assert callable(runtime.core._goal_resolver)
    finally:
        node.destroy_node()


def _pose(x, y, yaw):
    pose = Pose()
    pose.position.x = x
    pose.position.y = y
    pose.orientation.z = math.sin(yaw / 2.0)
    pose.orientation.w = math.cos(yaw / 2.0)
    return pose


def _map_snapshot(x, y, yaw):
    return RobotPoseSnapshot(
        x=x,
        y=y,
        z=0.0,
        orientation_x=0.0,
        orientation_y=0.0,
        orientation_z=math.sin(yaw / 2.0),
        orientation_w=math.cos(yaw / 2.0),
    )


def _handle(execution_id='execution-1', generation=1):
    return GenerationHandle(
        execution_id=execution_id,
        generation=generation,
    )


def test_cleaning_point_transform_zero_and_ninety_degrees():
    robot = _pose(1.0, 2.0, 0.0)
    assert cleaning_point_xy(robot) == (
        pytest.approx(1.37),
        pytest.approx(2.0),
    )

    robot = _pose(1.0, 2.0, math.pi / 2.0)
    clean_x, clean_y = cleaning_point_xy(robot)
    assert clean_x == pytest.approx(1.0)
    assert clean_y == pytest.approx(2.37)


def test_cleaning_point_transform_translation_and_yaw_combination():
    yaw = math.radians(30.0)
    robot = _pose(4.0, -1.0, yaw)
    clean_x, clean_y = cleaning_point_xy(robot, 0.37, 0.16)
    assert clean_x == pytest.approx(
        4.0 + 0.37 * math.cos(yaw) - 0.16 * math.sin(yaw)
    )
    assert clean_y == pytest.approx(
        -1.0 + 0.37 * math.sin(yaw) + 0.16 * math.cos(yaw)
    )


def test_brush_centers_rotate_at_zero_and_ninety_degrees():
    robot = _pose(1.0, 2.0, 0.0)
    left, right = brush_centers_xy(robot)
    assert left == pytest.approx((1.37, 2.16))
    assert right == pytest.approx((1.37, 1.84))

    robot = _pose(1.0, 2.0, math.pi / 2.0)
    left, right = brush_centers_xy(robot)
    assert left == pytest.approx((0.84, 2.37))
    assert right == pytest.approx((1.16, 2.37))


def test_physical_cleaning_distance_uses_nearest_brush_and_strict_boundary():
    robot = _pose(0.0, 0.0, 0.0)
    leaf = _pose(0.37, 0.16 + DEMO_CLEANING_DISTANCE_M, 0.0)
    distance, distances = physical_cleaning_distance(robot, leaf)
    assert distance == pytest.approx(DEMO_CLEANING_DISTANCE_M)
    assert distances[0] == pytest.approx(DEMO_CLEANING_DISTANCE_M)
    assert distances[1] > DEMO_CLEANING_DISTANCE_M
    assert distance <= DEMO_CLEANING_DISTANCE_M

    leaf = _pose(0.37, 0.16 + DEMO_CLEANING_DISTANCE_M + 1e-6, 0.0)
    distance, _ = physical_cleaning_distance(robot, leaf)
    assert distance > DEMO_CLEANING_DISTANCE_M

    leaf = _pose(0.37, 0.16 + DEMO_CLEANING_DISTANCE_M - 1e-6, 0.0)
    distance, _ = physical_cleaning_distance(robot, leaf)
    assert distance < DEMO_CLEANING_DISTANCE_M


def test_leaf_near_one_brush_completes_even_when_midpoint_is_farther():
    robot = _pose(0.0, 0.0, 0.0)
    leaf = _pose(0.37, 0.45, 0.0)
    midpoint_distance = math.hypot(0.37 - 0.37, 0.45)
    distance, distances = physical_cleaning_distance(robot, leaf)
    assert midpoint_distance > DEMO_CLEANING_DISTANCE_M
    assert distance == pytest.approx(0.29)
    assert min(distances) <= DEMO_CLEANING_DISTANCE_M


def test_leaf_near_right_brush_completes_with_same_semantics():
    robot = _pose(0.0, 0.0, 0.0)
    leaf = _pose(0.37, -0.45, 0.0)
    distance, distances = physical_cleaning_distance(robot, leaf)
    assert distance == pytest.approx(0.29)
    assert distances[1] == pytest.approx(0.29)


def test_both_brushes_farther_than_radius_do_not_complete():
    robot = _pose(0.0, 0.0, 0.0)
    leaf = _pose(0.37, 0.52, 0.0)
    distance, distances = physical_cleaning_distance(robot, leaf)
    assert distances[0] > DEMO_CLEANING_DISTANCE_M
    assert distances[1] > DEMO_CLEANING_DISTANCE_M
    assert distance > DEMO_CLEANING_DISTANCE_M


def test_pass_through_goal_world_xy_for_showcase_leaf_at_zero_yaw():
    assert DEMO_PASS_THROUGH_DISTANCE_M == pytest.approx(0.8)
    assert pass_through_goal_world_xy(0.0) == pytest.approx(
        (11.2, -5.0)
    )


def test_pass_through_goal_world_xy_rotates_at_ninety_degrees():
    assert pass_through_goal_world_xy(math.pi / 2.0) == pytest.approx(
        (11.2, -5.0)
    )


def test_leaf_lateral_pose_does_not_shift_fixed_pass_through_lane_goal():
    first_leaf = _pose(10.4, -5.0, 0.0)
    shifted_leaf = _pose(10.4, -4.75, 0.0)
    robot = _pose(0.0, -5.0, 0.0)
    map_pose = _map_snapshot(0.0, -5.0, 0.0)
    first_goal = make_world_relative_demo_goal(robot, first_leaf, map_pose)
    shifted_goal = make_world_relative_demo_goal(robot, shifted_leaf, map_pose)
    assert first_leaf.position.y != shifted_leaf.position.y
    assert shifted_goal.pose.position.x == pytest.approx(
        first_goal.pose.position.x
    )
    assert shifted_goal.pose.position.y == pytest.approx(
        first_goal.pose.position.y
    )
    first_goal_xy = (
        first_goal.pose.position.x,
        first_goal.pose.position.y,
    )
    assert first_goal_xy == pytest.approx((11.2, -5.0))


def test_pc_demo_navigation_goal_does_not_use_old_base_stopping_offset():
    assert not hasattr(pc_demo, 'base_goal_world_xy')
    assert (
        'pass_through_goal_world_xy'
        in make_world_relative_demo_goal.__code__.co_names
    )
    assert (
        'DEMO_CLEANING_POINT_OFFSET_X'
        not in make_world_relative_demo_goal.__code__.co_names
    )


def test_malformed_physical_pose_cannot_complete():
    robot = _pose(0.0, 0.0, 0.0)
    robot.position.x = float('nan')
    with pytest.raises(ValueError):
        physical_cleaning_distance(robot, _pose(0.37, 0.0, 0.0))


def test_pose_unavailable_cannot_latch_physical_completion():
    handle = _handle()
    wrapper, received = _wrapper_for_terminal_test(active=handle)
    wrapper._resolver = SimpleNamespace(physical_completion_enabled=True)

    wrapper._on_physical_poses(None, None)

    assert wrapper._physical_completion_handle is None
    assert received == []


def test_physical_completion_latches_when_only_one_brush_reaches_leaf():
    handle = _handle()
    wrapper = object.__new__(DemoPhysicalCompletionNavigationAdapter)
    wrapper._completion_lock = threading.Lock()
    wrapper._active_handle = handle
    wrapper._physical_completion_handle = None
    wrapper._physical_completion_distance = None
    wrapper._min_left_brush_distance = None
    wrapper._min_right_brush_distance = None
    wrapper._min_cleaning_distance = None
    wrapper._last_approach_log = -math.inf
    wrapper._resolver = SimpleNamespace(physical_completion_enabled=True)
    wrapper._logger = SimpleNamespace(info=lambda message: None)
    wrapper._records = {
        handle: SimpleNamespace(
            terminal_emitted=False,
            cancel_requested=False,
            cancel_started=False,
            goal_handle=None,
        )
    }
    wrapper._lock = threading.Lock()
    wrapper._start_cancel = lambda record: None
    robot = _pose(0.0, 0.0, 0.0)
    leaf = _pose(0.37, 0.45, 0.0)
    assert math.hypot(0.37, 0.45) > DEMO_CLEANING_DISTANCE_M

    wrapper._on_physical_poses(robot, leaf)

    assert wrapper._physical_completion_handle == handle
    assert wrapper._physical_completion_distance == pytest.approx(0.29)
    assert wrapper._records[handle].cancel_requested is True


def test_physical_monitor_uses_shifted_leaf_entity_pose():
    handle = _handle()
    wrapper = object.__new__(DemoPhysicalCompletionNavigationAdapter)
    wrapper._completion_lock = threading.Lock()
    wrapper._active_handle = handle
    wrapper._physical_completion_handle = None
    wrapper._physical_completion_distance = None
    wrapper._min_left_brush_distance = None
    wrapper._min_right_brush_distance = None
    wrapper._min_cleaning_distance = None
    wrapper._last_approach_log = -math.inf
    wrapper._resolver = SimpleNamespace(physical_completion_enabled=True)
    wrapper._logger = SimpleNamespace(info=lambda message: None)
    wrapper._records = {
        handle: SimpleNamespace(
            terminal_emitted=False,
            cancel_requested=False,
            cancel_started=False,
            goal_handle=None,
        )
    }
    wrapper._lock = threading.Lock()
    wrapper._start_cancel = lambda record: None

    # The right brush is exactly at the new entity pose (10.4, -4.75).
    wrapper._on_physical_poses(
        _pose(10.03, -4.59, 0.0),
        _pose(10.4, -4.75, 0.0),
    )

    assert wrapper._physical_completion_handle == handle
    assert wrapper._physical_completion_distance == pytest.approx(0.0)


def test_any_leaf_pile_can_latch_and_all_far_piles_do_not():
    handle = _handle()
    wrapper, _ = _wrapper_for_terminal_test(active=handle)
    wrapper._resolver = SimpleNamespace(physical_completion_enabled=True)
    wrapper._logger = SimpleNamespace(info=lambda message: None)
    wrapper._records = {
        handle: SimpleNamespace(
            terminal_emitted=False,
            cancel_requested=False,
            cancel_started=False,
            goal_handle=None,
        )
    }
    wrapper._lock = threading.Lock()
    wrapper._start_cancel = lambda record: None

    far_piles = {
        name: _pose(0.37, 0.52, 0.0)
        for name in DEMO_LEAF_ENTITIES
    }
    wrapper._on_physical_poses(_pose(0.0, 0.0, 0.0), far_piles)
    assert wrapper._physical_completion_handle is None

    near_piles = dict(far_piles)
    near_piles[DEMO_LEAF_ENTITIES[3]] = _pose(0.37, -0.45, 0.0)
    wrapper._on_physical_poses(_pose(0.0, 0.0, 0.0), near_piles)
    assert wrapper._physical_completion_handle == handle
    assert wrapper._physical_completion_distance == pytest.approx(0.29)


def _wrapper_for_terminal_test(*, active, latched=None):
    wrapper = object.__new__(DemoPhysicalCompletionNavigationAdapter)
    wrapper._completion_lock = threading.Lock()
    wrapper._active_handle = active
    wrapper._physical_completion_handle = latched
    wrapper._physical_completion_distance = (
        DEMO_CLEANING_DISTANCE_M if latched is not None else None
    )
    wrapper._min_left_brush_distance = None
    wrapper._min_right_brush_distance = None
    wrapper._min_cleaning_distance = None
    wrapper._last_approach_log = -math.inf
    wrapper._demo_event_sink = lambda event: received.append(event)
    wrapper._logger = None
    received = []
    return wrapper, received


def test_physical_completion_cancel_for_same_execution_becomes_success():
    handle = _handle()
    wrapper, received = _wrapper_for_terminal_test(
        active=handle,
        latched=handle,
    )
    wrapper._on_inner_event(
        NavigationEvent(
            handle,
            NavigationEventType.CANCEL_CONFIRMED,
        )
    )
    assert [event.event_type for event in received] == [
        NavigationEventType.SUCCEEDED,
    ]


def test_physical_completion_near_goal_abort_for_same_execution_succeeds():
    handle = _handle()
    wrapper, received = _wrapper_for_terminal_test(
        active=handle,
        latched=handle,
    )
    wrapper._on_inner_event(
        NavigationEvent(
            handle,
            NavigationEventType.FAILED,
            SimpleNamespace(status=GoalStatus.STATUS_ABORTED),
        )
    )
    assert [event.event_type for event in received] == [
        NavigationEventType.SUCCEEDED,
    ]


def test_far_abort_and_cancel_without_latch_remain_non_success():
    handle = _handle()
    wrapper, received = _wrapper_for_terminal_test(active=handle)
    wrapper._on_inner_event(
        NavigationEvent(
            handle,
            NavigationEventType.FAILED,
            SimpleNamespace(status=GoalStatus.STATUS_ABORTED),
        )
    )
    assert [event.event_type for event in received] == [
        NavigationEventType.FAILED,
    ]

    handle = _handle('execution-2')
    wrapper, received = _wrapper_for_terminal_test(active=handle)
    wrapper._on_inner_event(
        NavigationEvent(handle, NavigationEventType.CANCEL_CONFIRMED)
    )
    assert [event.event_type for event in received] == [
        NavigationEventType.CANCEL_CONFIRMED,
    ]


def test_stale_physical_completion_terminal_cannot_affect_current_execution():
    old_handle = _handle('old-execution')
    current_handle = _handle('current-execution')
    wrapper, received = _wrapper_for_terminal_test(
        active=current_handle,
        latched=old_handle,
    )
    wrapper._on_inner_event(
        NavigationEvent(old_handle, NavigationEventType.CANCEL_CONFIRMED)
    )
    assert [event.event_type for event in received] == [
        NavigationEventType.CANCEL_CONFIRMED,
    ]
    assert wrapper._active_handle == current_handle


def test_natural_navigation_success_remains_success_without_physical_latch():
    handle = _handle()
    wrapper, received = _wrapper_for_terminal_test(active=handle)
    wrapper._on_inner_event(
        NavigationEvent(handle, NavigationEventType.SUCCEEDED)
    )
    assert [event.event_type for event in received] == [
        NavigationEventType.SUCCEEDED,
    ]


def test_minimum_distance_diagnostics_reset_per_execution(monkeypatch):
    wrapper, _ = _wrapper_for_terminal_test(active=_handle('old-execution'))
    wrapper._physical_completion_handle = _handle('old-execution')
    wrapper._physical_completion_distance = 0.2
    wrapper._min_left_brush_distance = 0.3
    wrapper._min_right_brush_distance = 0.4
    wrapper._min_cleaning_distance = 0.3
    wrapper._last_approach_log = 123.0
    delegated = []
    monkeypatch.setattr(
        RealNavigationAdapter,
        'submit_goal',
        lambda self, handle, goal: delegated.append((handle, goal)),
    )

    handle = _handle('new-execution', 2)
    goal = object()
    wrapper.submit_goal(handle, goal)

    assert delegated == [(handle, goal)]
    assert wrapper._active_handle == handle
    assert wrapper._physical_completion_handle is None
    assert wrapper._physical_completion_distance is None
    assert wrapper._min_left_brush_distance is None
    assert wrapper._min_right_brush_distance is None
    assert wrapper._min_cleaning_distance is None
    assert wrapper._last_approach_log == -math.inf


def test_leaf_approach_tracks_minima_and_logs_terminal_summary():
    handle = _handle()
    wrapper, received = _wrapper_for_terminal_test(active=handle)
    wrapper._resolver = SimpleNamespace(physical_completion_enabled=True)
    messages = []
    wrapper._logger = SimpleNamespace(info=messages.append)

    wrapper._on_physical_poses(
        _pose(0.0, 0.0, 0.0),
        _pose(2.0, 0.0, 0.0),
    )
    wrapper._last_approach_log = math.inf
    wrapper._on_physical_poses(
        _pose(0.5, 0.0, 0.0),
        _pose(2.0, 0.0, 0.0),
    )
    wrapper._on_inner_event(
        NavigationEvent(handle, NavigationEventType.FAILED)
    )

    assert wrapper._min_left_brush_distance == pytest.approx(
        math.hypot(1.13, 0.16)
    )
    assert wrapper._min_right_brush_distance == pytest.approx(
        math.hypot(1.13, 0.16)
    )
    assert wrapper._min_cleaning_distance == pytest.approx(
        math.hypot(1.13, 0.16)
    )
    assert sum(message.startswith('LEAF_APPROACH ') for message in messages) == 1
    summaries = [
        message for message in messages
        if message.startswith('LEAF_APPROACH_SUMMARY ')
    ]
    assert len(summaries) == 1
    assert 'min_left=1.141' in summaries[0]
    assert 'min_right=1.141' in summaries[0]
    assert 'min_cleaning=1.141' in summaries[0]
    assert 'nearest_leaf_name=cleannav_demo_leaf_pile' in summaries[0]
    assert [event.event_type for event in received] == [
        NavigationEventType.FAILED,
    ]


def test_world_leaf_goal_has_no_drift():
    goal = make_world_relative_demo_goal(
        _pose(0.0, -5.0, 0.0),
        _pose(10.4, -4.75, 0.0),
        _map_snapshot(0.0, -5.0, 0.0),
    )
    assert goal.header.frame_id == 'map'
    assert goal.pose.position.x == pytest.approx(11.2)
    assert goal.pose.position.y == pytest.approx(-5.0)


def test_world_leaf_goal_handles_translation_drift():
    goal = make_world_relative_demo_goal(
        _pose(0.3, -4.8, 0.0),
        _pose(10.4, -4.75, 0.0),
        _map_snapshot(0.0, -5.0, 0.0),
    )
    assert goal.pose.position.x == pytest.approx(10.9)
    assert goal.pose.position.y == pytest.approx(-5.2)


def test_world_leaf_goal_handles_world_yaw_drift():
    world_yaw = math.radians(10.0)
    goal = make_world_relative_demo_goal(
        _pose(0.0, -5.0, world_yaw),
        _pose(10.4, -4.75, 0.0),
        _map_snapshot(0.0, -5.0, 0.0),
    )
    assert goal.pose.position.x == pytest.approx(
        11.2 * math.cos(world_yaw)
    )
    assert goal.pose.position.y == pytest.approx(
        -5.0 - 11.2 * math.sin(world_yaw)
    )


def test_world_leaf_goal_handles_different_map_and_world_yaw():
    world_yaw = math.radians(10.0)
    map_yaw = math.radians(40.0)
    leaf_yaw = math.radians(25.0)
    goal = make_world_relative_demo_goal(
        _pose(0.0, -5.0, world_yaw),
        _pose(10.4, -4.75, leaf_yaw),
        _map_snapshot(1.0, 2.0, map_yaw),
    )
    base_x, base_y = pass_through_goal_world_xy(world_yaw)
    local_x = math.cos(world_yaw) * base_x + math.sin(world_yaw) * (
        base_y + 5.0
    )
    local_y = -math.sin(world_yaw) * base_x + math.cos(world_yaw) * (
        base_y + 5.0
    )
    expected_x = 1.0 + (
        math.cos(map_yaw) * local_x - math.sin(map_yaw) * local_y
    )
    expected_y = 2.0 + (
        math.sin(map_yaw) * local_x + math.cos(map_yaw) * local_y
    )
    assert goal.pose.position.x == pytest.approx(expected_x)
    assert goal.pose.position.y == pytest.approx(expected_y)
    assert goal.pose.orientation.z == pytest.approx(math.sin(map_yaw / 2.0))
    assert goal.pose.orientation.w == pytest.approx(math.cos(map_yaw / 2.0))


def test_world_leaf_goal_handles_ninety_degree_approach():
    goal = make_world_relative_demo_goal(
        _pose(0.0, 0.0, math.pi / 2.0),
        _pose(10.4, -4.75, 0.0),
        _map_snapshot(0.0, 0.0, math.pi / 2.0),
    )
    assert goal.pose.position.x == pytest.approx(11.2)
    assert goal.pose.position.y == pytest.approx(-5.0)
    assert goal.pose.orientation.z == pytest.approx(math.sin(math.pi / 4.0))
    assert goal.pose.orientation.w == pytest.approx(math.cos(math.pi / 4.0))


def test_pc_offline_runtime_uses_world_leaf_resolver():
    node = _node(runtime_factory=create_pc_offline_runtime)
    try:
        resolver = node._pc_demo_goal_resolver
        assert isinstance(resolver, DemoWorldLeafGoalResolver)
        assert resolver.robot_world_pose is None
        assert resolver.leaf_world_pose is None
    finally:
        node.destroy_node()


def test_pc_hil_runtime_reuses_showcase_without_auto_task_command():
    node = _node(runtime_factory=create_pc_hil_execution_runtime)
    try:
        assert isinstance(node.runtime.navigation, RealNavigationAdapter)
        assert isinstance(node.runtime.safety, RealSafetyAdapter)
        assert isinstance(
            node._pc_demo_goal_resolver,
            DemoWorldLeafGoalResolver,
        )
        assert not hasattr(node, '_pc_demo_task_publisher')
        assert not hasattr(node, '_pc_demo_task_timer')
    finally:
        node.destroy_node()


def test_pc_hil_runtime_remaps_task_topics_away_from_j6_and_voice():
    node = MissionManagerNode(
        parameter_overrides=[Parameter('runtime_mode', value='mock')],
        runtime_factory=create_pc_hil_execution_runtime,
        cli_args=[
            '--ros-args',
            '-r',
            (
                '/cleannav/hmi/task_command:='
                f'{PC_PRIVATE_TASK_COMMAND_TOPIC}'
            ),
            '-r',
            f'/cleannav/task_status:={PC_PRIVATE_TASK_STATUS_TOPIC}',
        ],
    )
    try:
        assert node._task_command_subscription.topic_name == (
            PC_PRIVATE_TASK_COMMAND_TOPIC
        )
        assert node._task_status_publisher.topic_name == (
            PC_PRIVATE_TASK_STATUS_TOPIC
        )
    finally:
        node.destroy_node()


def test_world_leaf_resolver_waits_for_all_inputs_then_resolves():
    node = _node()
    try:
        resolver = DemoWorldLeafGoalResolver(
            node,
            leaf_entities=(DEMO_LEAF_ENTITY,),
        )
        task = SimpleNamespace(task_id=30)
        assert isinstance(resolver(_command(), task), PendingGoal)

        pose = PoseWithCovarianceStamped()
        pose.header.frame_id = 'map'
        pose.pose.pose.position.x = 3.0
        pose.pose.pose.position.y = -5.0
        pose.pose.pose.orientation.w = 1.0
        resolver._on_localization_pose(pose)

        assert isinstance(resolver(_command(), task), PendingGoal)
        resolver._robot_world_pose = _pose(0.0, -5.0, 0.0)
        resolver._leaf_world_pose = _pose(10.4, -4.75, 0.0)
        resolver._leaf_world_poses[DEMO_LEAF_ENTITY] = resolver._leaf_world_pose
        goal = resolver(_command(), task)
        # The fixed world anchor is (11.2, -5.0); map pose x=3.0 adds the
        # expected map-frame translation.
        assert goal.payload.pose.position.x == pytest.approx(14.2)
        assert goal.payload.pose.position.y == pytest.approx(-5.0)
    finally:
        node.destroy_node()


def test_pc_demo_task_command_populates_ros_stamp_and_duration():
    node = _node()
    try:
        message = make_demo_task_command(
            node,
            command_id='showcase-test',
        )

        assert isinstance(message, TaskCommand)
        assert message.header.frame_id == ''
        assert message.header.stamp.sec >= 0
        assert 0 <= message.header.stamp.nanosec < 1_000_000_000
        assert message.valid_for.sec == 240
        assert message.valid_for.nanosec == 0
        assert message.task_id == 30
        assert message.source == TaskCommand.SOURCE_MOCK
        assert message.user_confirmed is True
    finally:
        node.destroy_node()


def test_pc_offline_goal_resolver_rejects_unsupported_task_deterministically():
    node = _node()
    try:
        resolver = DemoWorldLeafGoalResolver(node)
        with pytest.raises(
            GoalResolutionError,
            match='PC offline demo goal is unsupported for task_id=31',
        ):
            resolver(_command(), _task(31))
    finally:
        node.destroy_node()


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
