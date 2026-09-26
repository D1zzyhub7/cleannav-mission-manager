#!/usr/bin/env python3
"""
Competition-only PC Showcase runtime using real ROS APIs.

This is not a production vehicle runtime. In the J6 HIL composition, J6
Mission Manager remains the external mission authority and this process is
only the simulation execution adapter. The physical leaf-completion latch is
Showcase-only semantics.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Callable
from uuid import uuid4

from action_msgs.msg import GoalStatus
from cleannav_interfaces.msg import TaskCommand
from gazebo_msgs.srv import GetEntityState
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
    qos_profile_sensor_data,
)

from cleannav_mission_manager.adapters.real_navigation import (
    RealNavigationAdapter,
)
from cleannav_mission_manager.adapters.navigation import (
    NavigationEvent,
    NavigationEventSink,
    NavigationEventType,
)
from cleannav_mission_manager.adapters.real_safety import RealSafetyAdapter
from cleannav_mission_manager.core import GoalResolutionError
from cleannav_mission_manager.domain.goal_resolution import (
    PendingGoal,
    ResolvedGoal,
)
from cleannav_mission_manager.demo_goals import (
    DEMO_TASK_GOALS,
)
from cleannav_mission_manager.domain.target_models import RobotPoseSnapshot
from cleannav_mission_manager.node import (
    MissionManagerNode,
    RuntimeComposition,
)
from cleannav_mission_manager.visual_target_ros_bridge import (
    LatestRobotPoseProvider,
)


DEMO_TASK_COMMAND_DELAY_SEC = 1.0
DEMO_TASK_COMMAND_VALID_FOR_SEC = 240
DEMO_LOCALIZATION_TOPIC = '/rtabmap/localization_pose'
DEMO_GOAL_WAIT_TIMEOUT_NS = 10_000_000_000
DEMO_ROBOT_ENTITY = 'cleannav_ackermann'
DEMO_LEAF_ENTITIES = tuple(
    f'cleannav_demo_leaf_pile_{index}' for index in range(5)
)
# Compatibility alias for callers that only need one representative entity.
DEMO_LEAF_ENTITY = DEMO_LEAF_ENTITIES[0]
DEMO_ENTITY_STATE_TOPIC = '/get_entity_state'
DEMO_ENTITY_POLL_PERIOD_SEC = 0.1
DEMO_WARNING_THROTTLE_SEC = 2.0
DEMO_LEAF_APPROACH_LOG_PERIOD_SEC = 1.0
DEMO_PASS_THROUGH_DISTANCE_M = 0.8
DEMO_PASS_THROUGH_LANE_ANCHOR_X = 11.2
DEMO_PASS_THROUGH_LANE_ANCHOR_Y = -5.0
DEMO_CLEANING_POINT_OFFSET_X = 0.37
DEMO_CLEANING_POINT_OFFSET_Y = 0.0
DEMO_LEFT_BRUSH_OFFSET_X = 0.37
DEMO_LEFT_BRUSH_OFFSET_Y = 0.16
DEMO_RIGHT_BRUSH_OFFSET_X = 0.37
DEMO_RIGHT_BRUSH_OFFSET_Y = -0.16
DEMO_CLEANING_DISTANCE_M = 0.35
DEMO_DISTANCE_COMPARISON_EPSILON_M = 1e-9


def make_demo_task_command(
    node: MissionManagerNode,
    *,
    command_id: str | None = None,
) -> TaskCommand:
    """
    Build the valid TaskCommand emitted by the PC showcase demo.

    The message is stamped with this node's ROS clock at publish time.  This
    is important when ``use_sim_time`` is enabled: the command timestamp and
    Mission Manager's validation clock then use the same ``/clock`` source.
    ``valid_for`` is a builtin_interfaces/Duration, so its two fields must be
    populated explicitly rather than using a non-existent ``valid_for_sec``
    field.
    """
    message = TaskCommand()
    message.header.stamp = node.get_clock().now().to_msg()
    message.header.frame_id = ''
    message.interface_version = '1.0'
    message.command_id = command_id or f'showcase-{uuid4()}'
    message.source = TaskCommand.SOURCE_MOCK
    message.task_id = 30
    message.confidence = 1.0
    message.raw_text = 'clean nearest leaf'
    message.valid_for.sec = DEMO_TASK_COMMAND_VALID_FOR_SEC
    message.valid_for.nanosec = 0
    message.user_confirmed = True
    return message


def schedule_demo_task_command(node: MissionManagerNode) -> None:
    """Publish one valid demo command from the existing PC demo process."""
    publisher = node.create_publisher(
        TaskCommand,
        node.task_command_topic,
        QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
        ),
    )

    def publish_once() -> None:
        message = make_demo_task_command(node)
        publisher.publish(message)
        node.get_logger().info(
            'DEMO_TASK_COMMAND_SENT '
            f'task_id={message.task_id} '
            f'command_id={message.command_id} '
            f'stamp={message.header.stamp.sec}.'
            f'{message.header.stamp.nanosec:09d} '
            f'valid_for={message.valid_for.sec}.'
            f'{message.valid_for.nanosec:09d}s'
        )
        timer.cancel()

    # Keep both entities alive for the lifetime of the existing Mission
    # Manager node.  No extra ROS node is created for the demo publisher.
    node._pc_demo_task_publisher = publisher
    timer = node.create_timer(DEMO_TASK_COMMAND_DELAY_SEC, publish_once)
    node._pc_demo_task_timer = timer


def start_pc_showcase_task30_execution(
    node: MissionManagerNode,
    *,
    command_id: str,
) -> object:
    """
    Start the frozen PC task30 path without publishing a ROS command.

    This demo-only entry exists for the HIL execution adapter. It deliberately
    reuses MissionManagerNode's existing TaskCommand processing callback while
    avoiding a PC-side TaskCommand publication that could flow back to J6.
    """
    return node._on_task_command(
        make_demo_task_command(node, command_id=command_id)
    )


def _yaw_from_components(x: float, y: float, z: float, w: float) -> float:
    """Return planar yaw from finite quaternion components."""
    values = (float(x), float(y), float(z), float(w))
    if not all(math.isfinite(value) for value in values):
        raise ValueError('orientation quaternion must be finite')
    norm = math.sqrt(sum(value * value for value in values))
    if norm <= 1e-12:
        raise ValueError('orientation quaternion must be non-zero')
    x, y, z, w = (value / norm for value in values)
    return math.atan2(
        2.0 * (
            w * z
            + x * y
        ),
        1.0
        - 2.0 * (
            y * y
            + z * z
        ),
    )


def _yaw_from_quaternion(orientation: object) -> float:
    """Return planar yaw from a finite quaternion-like object."""
    return _yaw_from_components(
        orientation.x,
        orientation.y,
        orientation.z,
        orientation.w,
    )


def cleaning_point_xy(
    robot_pose: object,
    offset_x: float = DEMO_CLEANING_POINT_OFFSET_X,
    offset_y: float = DEMO_CLEANING_POINT_OFFSET_Y,
) -> tuple[float, float]:
    """Transform the front-brush midpoint from base_link into world XY."""
    robot_x = float(robot_pose.position.x)
    robot_y = float(robot_pose.position.y)
    offset_x = float(offset_x)
    offset_y = float(offset_y)
    if not all(math.isfinite(value) for value in (
        robot_x, robot_y, offset_x, offset_y,
    )):
        raise ValueError('cleaning point inputs must be finite')
    yaw = _yaw_from_quaternion(robot_pose.orientation)
    clean_x = robot_x + offset_x * math.cos(yaw) - offset_y * math.sin(yaw)
    clean_y = robot_y + offset_x * math.sin(yaw) + offset_y * math.cos(yaw)
    if not all(math.isfinite(value) for value in (clean_x, clean_y)):
        raise ValueError('cleaning point must be finite')
    return clean_x, clean_y


def brush_centers_xy(
    robot_pose: object,
    left_offset_x: float = DEMO_LEFT_BRUSH_OFFSET_X,
    left_offset_y: float = DEMO_LEFT_BRUSH_OFFSET_Y,
    right_offset_x: float = DEMO_RIGHT_BRUSH_OFFSET_X,
    right_offset_y: float = DEMO_RIGHT_BRUSH_OFFSET_Y,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Return left and right brush centers in world XY."""
    return (
        cleaning_point_xy(robot_pose, left_offset_x, left_offset_y),
        cleaning_point_xy(robot_pose, right_offset_x, right_offset_y),
    )


def brush_distances(
    robot_pose: object,
    leaf_pose: object,
    left_offset_x: float = DEMO_LEFT_BRUSH_OFFSET_X,
    left_offset_y: float = DEMO_LEFT_BRUSH_OFFSET_Y,
    right_offset_x: float = DEMO_RIGHT_BRUSH_OFFSET_X,
    right_offset_y: float = DEMO_RIGHT_BRUSH_OFFSET_Y,
) -> tuple[float, float]:
    """Return left/right brush-center distances to the leaf."""
    leaf_x = float(leaf_pose.position.x)
    leaf_y = float(leaf_pose.position.y)
    if not all(math.isfinite(value) for value in (leaf_x, leaf_y)):
        raise ValueError('leaf position must be finite')
    left, right = brush_centers_xy(
        robot_pose,
        left_offset_x,
        left_offset_y,
        right_offset_x,
        right_offset_y,
    )
    distances = (
        math.hypot(left[0] - leaf_x, left[1] - leaf_y),
        math.hypot(right[0] - leaf_x, right[1] - leaf_y),
    )
    if not all(math.isfinite(value) for value in distances):
        raise ValueError('brush distances must be finite')
    return distances


def physical_cleaning_distance(
    robot_pose: object,
    leaf_pose: object,
    offset_x: float = DEMO_CLEANING_POINT_OFFSET_X,
    offset_y: float = DEMO_CLEANING_POINT_OFFSET_Y,
) -> tuple[float, tuple[float, float]]:
    """Return minimum brush distance and the left/right distances."""
    distances = brush_distances(
        robot_pose,
        leaf_pose,
        offset_x,
        offset_y + DEMO_LEFT_BRUSH_OFFSET_Y,
        offset_x,
        offset_y + DEMO_RIGHT_BRUSH_OFFSET_Y,
    )
    return min(distances), distances


def pass_through_goal_world_xy(
    desired_world_yaw: float,
    anchor_x: float = DEMO_PASS_THROUGH_LANE_ANCHOR_X,
    anchor_y: float = DEMO_PASS_THROUGH_LANE_ANCHOR_Y,
) -> tuple[float, float]:
    """Return the fixed world lane anchor used by the showcase pass-through."""
    desired_world_yaw = float(desired_world_yaw)
    anchor_x = float(anchor_x)
    anchor_y = float(anchor_y)
    if not all(math.isfinite(value) for value in (
        desired_world_yaw, anchor_x, anchor_y,
    )):
        raise ValueError('pass-through lane anchor inputs must be finite')
    goal_x = anchor_x
    goal_y = anchor_y
    if not all(math.isfinite(value) for value in (goal_x, goal_y)):
        raise ValueError('pass-through goal must be finite')
    return goal_x, goal_y


def make_world_relative_demo_goal(
    robot_world_pose: object,
    leaf_world_pose: object,
    robot_map_pose: RobotPoseSnapshot,
) -> PoseStamped:
    """
    Resolve the fixed showcase lane goal in the current map frame.

    ``leaf_world_pose`` remains an input because the same resolver owns the
    physical monitor, but the navigation target is deliberately independent
    of that visual/entity pose.
    """
    if not isinstance(robot_map_pose, RobotPoseSnapshot):
        raise TypeError('robot_map_pose must be RobotPoseSnapshot')
    if not robot_map_pose.valid:
        raise ValueError('robot_map_pose must be valid')

    robot_world_yaw = _yaw_from_quaternion(robot_world_pose.orientation)
    map_robot_yaw = _yaw_from_components(
        robot_map_pose.orientation_x,
        robot_map_pose.orientation_y,
        robot_map_pose.orientation_z,
        robot_map_pose.orientation_w,
    )

    pass_through_world_x, pass_through_world_y = pass_through_goal_world_xy(
        robot_world_yaw,
    )
    world_dx = pass_through_world_x - float(
        robot_world_pose.position.x
    )
    world_dy = pass_through_world_y - float(
        robot_world_pose.position.y
    )
    if not math.isfinite(world_dx) or not math.isfinite(world_dy):
        raise ValueError('world positions must be finite')

    # T_map_world = T_map_robot * inverse(T_world_robot).
    local_dx = (
        math.cos(robot_world_yaw) * world_dx
        + math.sin(robot_world_yaw) * world_dy
    )
    local_dy = (
        -math.sin(robot_world_yaw) * world_dx
        + math.cos(robot_world_yaw) * world_dy
    )
    map_dx = (
        math.cos(map_robot_yaw) * local_dx
        - math.sin(map_robot_yaw) * local_dy
    )
    map_dy = (
        math.sin(map_robot_yaw) * local_dx
        + math.cos(map_robot_yaw) * local_dy
    )

    goal = PoseStamped()
    goal.header.frame_id = 'map'
    goal.pose.position.x = robot_map_pose.x + map_dx
    goal.pose.position.y = robot_map_pose.y + map_dy
    # The desired approach yaw is the robot's current world yaw.  Mapping
    # that same local orientation through T_map_robot yields map_robot_yaw.
    goal_yaw = map_robot_yaw
    goal.pose.orientation.z = math.sin(goal_yaw / 2.0)
    goal.pose.orientation.w = math.cos(goal_yaw / 2.0)
    return goal


class DemoWorldLeafGoalResolver:
    """Resolve the PC demo leaf from Gazebo world and map poses."""

    def __init__(
        self,
        node: MissionManagerNode,
        *,
        localization_topic: str = DEMO_LOCALIZATION_TOPIC,
        robot_entity: str = DEMO_ROBOT_ENTITY,
        leaf_entity: str | None = None,
        leaf_entities: tuple[str, ...] = DEMO_LEAF_ENTITIES,
    ) -> None:
        self._node = node
        self._robot_entity = str(robot_entity)
        configured_leaf_entities = tuple(str(entity) for entity in leaf_entities)
        if not configured_leaf_entities:
            configured_leaf_entities = (
                str(leaf_entity or DEMO_LEAF_ENTITY),
            )
        self._leaf_entities = configured_leaf_entities
        self._leaf_entity = self._leaf_entities[0]
        self._provider = LatestRobotPoseProvider()
        self._robot_world_pose = None
        self._leaf_world_pose = None
        self._leaf_world_poses: dict[str, object] = {}
        self._active_task_id = None
        self._retry_callback = None
        self._completion_callback: (
            Callable[[object, object], None] | None
        ) = None
        self._pending_robot = None
        self._pending_leaf: dict[str, object] = {}
        self._last_wait_log = -math.inf
        self._state_client = node.create_client(
            GetEntityState,
            DEMO_ENTITY_STATE_TOPIC,
        )
        self._subscription = node.create_subscription(
            PoseWithCovarianceStamped,
            localization_topic,
            self._on_localization_pose,
            qos_profile_sensor_data,
        )
        self._timer = node.create_timer(
            DEMO_ENTITY_POLL_PERIOD_SEC,
            self._poll_entity_states,
        )

    @property
    def robot_world_pose(self):
        """Return the latest valid Gazebo robot world pose."""
        return self._robot_world_pose

    @property
    def leaf_world_pose(self):
        """Return the latest valid Gazebo leaf world pose."""
        return self._leaf_world_pose

    @property
    def leaf_world_poses(self) -> dict[str, object]:
        """Return the latest valid world pose for every configured pile."""
        return dict(self._leaf_world_poses)

    def bind_retry_callback(self, callback) -> None:
        """Bind the Core retry hook without changing its public contract."""
        if not callable(callback):
            raise TypeError('callback must be callable')
        self._retry_callback = callback

    def bind_completion_callback(self, callback) -> None:
        """Bind the demo-only physical completion observer."""
        if not callable(callback):
            raise TypeError('callback must be callable')
        self._completion_callback = callback

    @property
    def physical_completion_enabled(self) -> bool:
        """Return whether the current resolver task is task 30."""
        return self._active_task_id == 30

    def _on_localization_pose(
        self,
        message: PoseWithCovarianceStamped,
    ) -> None:
        try:
            self._provider.update(message)
        except (TypeError, ValueError) as exc:
            self._node.get_logger().warning(
                f'DEMO_ONLY_GOAL_IGNORED_LOCALIZATION reason={exc}'
            )
            return

        self._retry_if_ready()

    def _poll_entity_states(self) -> None:
        """Poll the robot and every visual-only leaf pile."""
        if not self._state_client.service_is_ready():
            self._log_waiting('GetEntityState service unavailable')
            return
        if self._pending_robot is None:
            future = self._request_entity(self._robot_entity)
            if self._robot_world_pose is None and future is not None:
                self._pending_robot = future
        for leaf_entity in self._leaf_entities:
            if leaf_entity not in self._pending_leaf:
                future = self._request_entity(leaf_entity)
                if (
                    leaf_entity not in self._leaf_world_poses
                    and future is not None
                ):
                    self._pending_leaf[leaf_entity] = future

    def _request_entity(self, entity_name: str):
        if entity_name == self._robot_entity:
            self._robot_world_pose = None
        request = GetEntityState.Request()
        request.name = entity_name
        request.reference_frame = 'world'
        try:
            future = self._state_client.call_async(request)
        except Exception as exc:
            self._log_waiting(f'GetEntityState request unavailable: {exc}')
            return None
        future.add_done_callback(
            lambda done: self._on_entity_result(entity_name, done)
        )
        return future

    def _on_entity_result(self, entity_name: str, future) -> None:
        if entity_name == self._robot_entity:
            self._pending_robot = None
        else:
            self._pending_leaf.pop(entity_name, None)
        try:
            response = future.result()
        except Exception as exc:
            if entity_name == self._robot_entity:
                self._robot_world_pose = None
            self._log_waiting(f'GetEntityState call unavailable: {exc}')
            self._notify_physical_completion()
            return
        if not bool(response.success):
            if entity_name == self._robot_entity:
                self._robot_world_pose = None
            self._log_waiting(f'Waiting for entity {entity_name}')
            self._notify_physical_completion()
            return
        pose = response.state.pose
        try:
            _yaw_from_quaternion(pose.orientation)
            if not all(
                math.isfinite(float(value))
                for value in (
                    pose.position.x,
                    pose.position.y,
                    pose.position.z,
                )
            ):
                raise ValueError('entity position must be finite')
        except (AttributeError, TypeError, ValueError) as exc:
            if entity_name == self._robot_entity:
                self._robot_world_pose = None
            self._log_waiting(f'Ignoring invalid entity pose: {exc}')
            self._notify_physical_completion()
            return
        if entity_name == self._robot_entity:
            self._robot_world_pose = pose
        else:
            self._leaf_world_poses[entity_name] = pose
            if entity_name == self._leaf_entity:
                self._leaf_world_pose = pose
        self._notify_physical_completion()
        self._retry_if_ready()

    def _notify_physical_completion(self) -> None:
        """Forward current robot and last-known pile poses to the monitor."""
        if not (
            self._completion_callback is not None
            and self.physical_completion_enabled
            and self._robot_world_pose is not None
            and len(self._leaf_world_poses) == len(self._leaf_entities)
        ):
            return
        try:
            self._completion_callback(
                self._robot_world_pose,
                dict(self._leaf_world_poses),
            )
        except Exception as exc:  # pragma: no cover - defensive demo log
            self._log_waiting(
                f'Physical completion observer unavailable: {exc}'
            )

    def _retry_if_ready(self) -> None:
        if (
            self._retry_callback is not None
            and self._provider.latest() is not None
            and self._robot_world_pose is not None
            and len(self._leaf_world_poses) == len(self._leaf_entities)
        ):
            self._retry_callback()

    def _log_waiting(self, message: str) -> None:
        now = time.monotonic()
        if now - self._last_wait_log < DEMO_WARNING_THROTTLE_SEC:
            return
        self._node.get_logger().info(f'DEMO_ONLY_GOAL_WAITING {message}')
        self._last_wait_log = now

    def __call__(self, command: object, task: object) -> object:
        """Resolve task 30 once map and Gazebo world poses are available."""
        task_id = int(getattr(task, 'task_id'))
        self._active_task_id = task_id
        if task_id not in DEMO_TASK_GOALS:
            raise GoalResolutionError(
                'PC offline demo goal is unsupported for '
                f'task_id={task_id}'
            )
        robot_map_pose = self._provider.latest()
        if (
            robot_map_pose is None
            or self._robot_world_pose is None
            or len(self._leaf_world_poses) != len(self._leaf_entities)
        ):
            policy = getattr(task, 'visual_target_policy', None)
            timeout_ns = getattr(
                policy,
                'wait_timeout_ns',
                DEMO_GOAL_WAIT_TIMEOUT_NS,
            )
            return PendingGoal(int(timeout_ns))
        return ResolvedGoal(
            payload=make_world_relative_demo_goal(
                self._robot_world_pose,
                self._leaf_world_poses[self._leaf_entity],
                robot_map_pose,
            )
        )


class DemoPhysicalCompletionNavigationAdapter(RealNavigationAdapter):
    """
    Demo-only adapter wrapper for physical leaf completion.

    The formal NavigationFacade contract remains unchanged.  This wrapper
    owns only the PC showcase's physical completion latch and translates the
    same-generation, post-latch cancel/near-goal-abort terminal into the
    normal ``SUCCEEDED`` navigation event consumed by Mission Manager.
    """

    def __init__(
        self,
        node: MissionManagerNode,
        resolver: DemoWorldLeafGoalResolver,
        event_sink: NavigationEventSink,
        *,
        action_client: object | None = None,
    ) -> None:
        if not callable(event_sink):
            raise TypeError('event_sink must be callable')
        self._demo_event_sink = event_sink
        self._resolver = resolver
        self._completion_lock = threading.Lock()
        self._active_handle = None
        self._physical_completion_handle = None
        self._physical_completion_distance = None
        self._min_left_brush_distance = None
        self._min_right_brush_distance = None
        self._min_cleaning_distance = None
        self._nearest_leaf_name = None
        self._last_approach_log = -math.inf
        super().__init__(
            node,
            self._on_inner_event,
            action_client=action_client,
        )
        resolver.bind_completion_callback(self._on_physical_poses)

    @property
    def physical_completion_handle(self):
        """Return the latched execution handle, if any."""
        with self._completion_lock:
            return self._physical_completion_handle

    def submit_goal(self, handle, goal_payload) -> None:
        """Track one current generation before delegating to ROS action."""
        with self._completion_lock:
            self._active_handle = handle
            self._physical_completion_handle = None
            self._physical_completion_distance = None
            self._min_left_brush_distance = None
            self._min_right_brush_distance = None
            self._min_cleaning_distance = None
            self._nearest_leaf_name = None
            self._last_approach_log = -math.inf
        super().submit_goal(handle, goal_payload)

    def cancel_goal(self, handle) -> None:
        """Delegate ordinary Mission Manager cancellation unchanged."""
        super().cancel_goal(handle)

    def _on_physical_poses(self, robot_pose, leaf_poses) -> None:
        """Latch and cancel once when either brush reaches any leaf pile."""
        if not self._resolver.physical_completion_enabled:
            return
        if hasattr(leaf_poses, 'position'):
            leaf_poses = {DEMO_LEAF_ENTITY: leaf_poses}
        try:
            leaf_items = tuple(leaf_poses.items())
        except AttributeError:
            return
        nearest = None
        for leaf_name, leaf_pose in leaf_items:
            try:
                pile_distance, brush_distance_values = (
                    physical_cleaning_distance(robot_pose, leaf_pose)
                )
            except (AttributeError, TypeError, ValueError):
                continue
            if nearest is None or pile_distance < nearest[0]:
                nearest = (
                    pile_distance,
                    brush_distance_values,
                    str(leaf_name),
                    leaf_pose,
                )
        if nearest is None:
            return
        distance, brush_distance_values, nearest_leaf_name, nearest_leaf_pose = (
            nearest
        )

        now = time.monotonic()
        should_log_approach = False
        with self._completion_lock:
            handle = self._active_handle
            if handle is None:
                return
            left_distance, right_distance = brush_distance_values
            self._min_left_brush_distance = self._minimum_distance(
                self._min_left_brush_distance,
                left_distance,
            )
            self._min_right_brush_distance = self._minimum_distance(
                self._min_right_brush_distance,
                right_distance,
            )
            self._min_cleaning_distance = self._minimum_distance(
                self._min_cleaning_distance,
                distance,
            )
            if (
                self._min_cleaning_distance == distance
                or self._nearest_leaf_name is None
            ):
                self._nearest_leaf_name = nearest_leaf_name
            if (
                now - self._last_approach_log
                >= DEMO_LEAF_APPROACH_LOG_PERIOD_SEC
            ):
                self._last_approach_log = now
                should_log_approach = True

        if should_log_approach:
            self._log_info(
                'LEAF_APPROACH '
                f'execution_id={handle.execution_id} '
                f'generation={handle.generation} '
                f'nearest_leaf={nearest_leaf_name} '
                f'left={brush_distance_values[0]:.3f} '
                f'right={brush_distance_values[1]:.3f} '
                f'min={distance:.3f}'
            )

        if (
            distance - DEMO_CLEANING_DISTANCE_M
            > DEMO_DISTANCE_COMPARISON_EPSILON_M
        ):
            return

        with self._completion_lock:
            handle = self._active_handle
            if handle is None or self._physical_completion_handle is not None:
                return
            self._physical_completion_handle = handle
            self._physical_completion_distance = distance

        self._log_info(
            'PHYSICAL_LEAF_COMPLETION_LATCHED '
            f'execution_id={handle.execution_id} '
            f'generation={handle.generation} '
            f'nearest_leaf={nearest_leaf_name} '
            f'LEFT_BRUSH_DISTANCE={brush_distance_values[0]:.3f} '
            f'RIGHT_BRUSH_DISTANCE={brush_distance_values[1]:.3f} '
            f'CLEANING_DISTANCE={distance:.3f} '
            f'robot=({robot_pose.position.x:.3f},'
            f'{robot_pose.position.y:.3f}) '
            f'leaf=({nearest_leaf_pose.position.x:.3f},'
            f'{nearest_leaf_pose.position.y:.3f})'
        )
        # This is a demo-only direct cancel request.  The terminal event is
        # still emitted by the real adapter and is translated below only for
        # this exact latched generation.
        super().cancel_goal(handle)

    def _on_inner_event(self, event: NavigationEvent) -> None:
        """Map only a latched same-generation cancel/ABORT to success."""
        with self._completion_lock:
            active_handle = self._active_handle
            latched_handle = self._physical_completion_handle

        is_current = active_handle == event.handle
        is_latched = latched_handle == event.handle
        mapped_event = event

        if is_current and event.event_type in (
            NavigationEventType.SUCCEEDED,
            NavigationEventType.FAILED,
            NavigationEventType.CANCEL_CONFIRMED,
            NavigationEventType.CANCEL_FAILED,
            NavigationEventType.GOAL_REJECTED,
        ):
            with self._completion_lock:
                minimum_distances = (
                    self._min_left_brush_distance,
                    self._min_right_brush_distance,
                    self._min_cleaning_distance,
                    getattr(self, '_nearest_leaf_name', None),
                )
                self._active_handle = None
            self._log_approach_summary(event.handle, minimum_distances)

        if (
            is_current
            and is_latched
            and event.event_type is NavigationEventType.CANCEL_CONFIRMED
        ):
            mapped_event = NavigationEvent(
                handle=event.handle,
                event_type=NavigationEventType.SUCCEEDED,
                payload=event.payload,
            )
        elif (
            is_current
            and is_latched
            and event.event_type is NavigationEventType.FAILED
            and getattr(event.payload, 'status', None)
            == GoalStatus.STATUS_ABORTED
        ):
            mapped_event = NavigationEvent(
                handle=event.handle,
                event_type=NavigationEventType.SUCCEEDED,
                payload=event.payload,
            )

        if mapped_event.event_type is NavigationEventType.SUCCEEDED:
            self._log_info(
                'DEMO_ONLY_PHYSICAL_COMPLETION_CONFIRMED '
                f'execution_id={event.handle.execution_id} '
                f'generation={event.handle.generation}'
            )
        self._demo_event_sink(mapped_event)

    @staticmethod
    def _minimum_distance(current, candidate: float) -> float:
        """Return a finite running minimum for one execution."""
        return candidate if current is None else min(current, candidate)

    def _log_approach_summary(self, handle, distances) -> None:
        """Emit one terminal summary for the current generation."""
        left, right, cleaning, nearest_leaf_name = distances

        def render(value) -> str:
            return 'unavailable' if value is None else f'{value:.3f}'

        self._log_info(
            'LEAF_APPROACH_SUMMARY '
            f'execution_id={handle.execution_id} '
            f'generation={handle.generation} '
            f'min_left={render(left)} '
            f'min_right={render(right)} '
            f'min_cleaning={render(cleaning)} '
            f'nearest_leaf_name={nearest_leaf_name or "unavailable"}'
        )

    def _log_info(self, message: str) -> None:
        """Log through the inherited adapter logger when available."""
        logger = getattr(self, '_logger', None)
        if logger is not None:
            logger.info(message)


# Compatibility name for callers that imported the previous demo-only class.
DemoStartRelativeGoalResolver = DemoWorldLeafGoalResolver


def _create_pc_showcase_runtime(
    node: MissionManagerNode,
    *,
    auto_start_task: bool,
) -> RuntimeComposition:
    """Assemble the frozen PC Navigation/Safety/physical-completion path."""
    resolver = DemoWorldLeafGoalResolver(node)
    node.get_logger().info(
        "DEMO_ONLY_GOAL enabled: "
        f"world_leaf={DEMO_LEAF_ENTITY}; task30 resolver only"
    )

    def resolve_demo_goal(command: object, task: object) -> object:
        return resolver(command, task)

    navigation = DemoPhysicalCompletionNavigationAdapter(
        node,
        resolver,
        node._on_navigation_event,
    )
    safety = RealSafetyAdapter(node, node._on_safety_event)
    runtime = node._create_runtime(
        navigation=navigation,
        safety=safety,
        goal_resolver=resolve_demo_goal,
    )
    resolver.bind_retry_callback(runtime.core.retry_waiting_goal_resolution)
    node._pc_demo_goal_resolver = resolver
    node._pc_demo_navigation = navigation
    if auto_start_task:
        schedule_demo_task_command(node)
    return runtime


def create_pc_offline_runtime(
    node: MissionManagerNode,
) -> RuntimeComposition:
    """Inject the existing self-starting PC Showcase runtime unchanged."""
    return _create_pc_showcase_runtime(node, auto_start_task=True)


def create_pc_hil_execution_runtime(
    node: MissionManagerNode,
) -> RuntimeComposition:
    """Inject the same PC Showcase runtime without its automatic task."""
    return _create_pc_showcase_runtime(node, auto_start_task=False)


def main(args=None) -> None:
    """Run the independent PC offline Mission Manager composition."""
    rclpy.init(args=args)
    node: MissionManagerNode | None = None
    try:
        node = MissionManagerNode(
            runtime_factory=create_pc_offline_runtime,
        )
        runtime = node.runtime
        if (
            runtime is None
            or not isinstance(runtime.navigation, RealNavigationAdapter)
            or not isinstance(runtime.safety, RealSafetyAdapter)
        ):
            raise RuntimeError(
                'PC offline runtime was not assembled with real adapters'
            )

        node.get_logger().info(
            'PC offline real runtime active; '
            'Navigation and Safety Lease APIs are connected'
        )
        node.get_logger().info(
            "Frozen runtime_mode parameter remains 'mock'; "
            'real adapters were injected by the PC offline demo runner'
        )
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()


__all__ = [
    'DEMO_LOCALIZATION_TOPIC',
    'DEMO_ROBOT_ENTITY',
    'DEMO_LEAF_ENTITIES',
    'DEMO_LEAF_ENTITY',
    'DEMO_LEFT_BRUSH_OFFSET_X',
    'DEMO_LEFT_BRUSH_OFFSET_Y',
    'DEMO_RIGHT_BRUSH_OFFSET_X',
    'DEMO_RIGHT_BRUSH_OFFSET_Y',
    'DEMO_PASS_THROUGH_DISTANCE_M',
    'DEMO_PASS_THROUGH_LANE_ANCHOR_X',
    'DEMO_PASS_THROUGH_LANE_ANCHOR_Y',
    'create_pc_offline_runtime',
    'create_pc_hil_execution_runtime',
    'main',
    'make_demo_task_command',
    'cleaning_point_xy',
    'brush_centers_xy',
    'brush_distances',
    'physical_cleaning_distance',
    'pass_through_goal_world_xy',
    'make_world_relative_demo_goal',
    'DemoWorldLeafGoalResolver',
    'DemoPhysicalCompletionNavigationAdapter',
    'schedule_demo_task_command',
    'start_pc_showcase_task30_execution',
]
