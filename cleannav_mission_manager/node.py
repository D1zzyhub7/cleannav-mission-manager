"""ROS 2 glue node for the ROS-independent Mission Manager Core."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ament_index_python.packages import get_package_share_directory
from cleannav_interfaces.msg import TaskCommand, TaskStatus
import rclpy
from rclpy.node import Node
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)

from cleannav_mission_manager.adapters.mock_navigation import (
    MockNavigationAdapter,
)
from cleannav_mission_manager.adapters.mock_safety import (
    MockSafetyAdapter,
)
from cleannav_mission_manager.adapters.navigation import (
    NavigationAdapter,
    NavigationEvent,
    NavigationEventType,
)
from cleannav_mission_manager.adapters.real_navigation import (
    RealNavigationAdapter,
)
from cleannav_mission_manager.adapters.real_safety import (
    RealSafetyAdapter,
)
from cleannav_mission_manager.adapters.safety import (
    SafetyAdapter,
    SafetyEvent,
    SafetyEventType,
)
from cleannav_mission_manager.core import (
    CoreResult,
    GoalResolver,
    MissionManagerCore,
)
from cleannav_mission_manager.domain.command_processing import (
    NormalizedTaskCommand,
)
from cleannav_mission_manager.domain.command_store import (
    CommandRecordStore,
    TerminalStatusCache,
)
from cleannav_mission_manager.domain.execution_store import (
    ExecutionRecordStore,
)
from cleannav_mission_manager.domain.generation_gate import GenerationGate
from cleannav_mission_manager.domain.mission_queue import MissionQueue
from cleannav_mission_manager.domain.runtime_policy import (
    MIN_CONFIDENCE_BY_SOURCE,
)
from cleannav_mission_manager.domain.task_catalog import load_task_catalog
from cleannav_mission_manager.ros_conversion import (
    RosConversionError,
    ros_task_command_to_normalized,
    status_snapshot_to_ros,
)
from cleannav_mission_manager.domain.target_registry import TargetRegistry
from cleannav_mission_manager.domain.target_selector import TargetSelector
from cleannav_mission_manager.visual_target_goal_resolver import (
    VisualTargetGoalResolver,
)
from cleannav_mission_manager.visual_target_ros_bridge import (
    LOCALIZATION_TOPIC,
    LatestRobotPoseProvider,
    VisualTargetRosBridge,
)


TASK_COMMAND_TOPIC = '/cleannav/hmi/task_command'
TASK_STATUS_TOPIC = '/cleannav/task_status'


class _RosClockAdapter:
    """Expose the Node ROS clock through the Core clock protocol."""

    def __init__(self, node: Node) -> None:
        self._node = node

    def now_ros(self) -> float:
        """Return the current ROS clock value in seconds."""
        return self._node.get_clock().now().nanoseconds / 1_000_000_000


@dataclass(frozen=True)
class RuntimeComposition:
    """Concrete dependencies assembled for one Mission Manager runtime."""

    core: MissionManagerCore
    command_store: CommandRecordStore
    execution_store: ExecutionRecordStore
    navigation: NavigationAdapter
    safety: SafetyAdapter


RuntimeFactory = Callable[['MissionManagerNode'], RuntimeComposition]


class MissionManagerNode(Node):
    """Connect ROS messages to an injected MissionManagerCore."""

    def __init__(
        self,
        *,
        core: object | None = None,
        runtime_factory: Optional[RuntimeFactory] = None,
        **kwargs,
    ) -> None:
        super().__init__('mission_manager_node', **kwargs)

        self.declare_parameter('runtime_mode', 'mock')
        self.declare_parameter('demo_goal_x', -4.0)
        self.declare_parameter('demo_goal_y', 0.0)
        self.declare_parameter('demo_goal_yaw', 0.0)
        runtime_mode = self.get_parameter('runtime_mode').value
        if runtime_mode != 'mock':
            raise RuntimeError(
                'production runtime is not available; '
                f'unsupported runtime_mode={runtime_mode!r}'
            )

        self._runtime_mode = runtime_mode
        self._command_store: CommandRecordStore | None = None
        self._execution_store: ExecutionRecordStore | None = None
        self._last_command_id: str | None = None
        self._visual_target_bridge: VisualTargetRosBridge | None = None
        self._visual_target_registry: TargetRegistry | None = None
        self._visual_target_pose_provider: (
            LatestRobotPoseProvider | None
        ) = None

        self._task_command_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.VOLATILE,
        )
        self._task_status_qos = QoSProfile(
            history=HistoryPolicy.KEEP_LAST,
            depth=10,
            reliability=ReliabilityPolicy.RELIABLE,
            durability=DurabilityPolicy.TRANSIENT_LOCAL,
        )
        self._task_status_publisher = self.create_publisher(
            TaskStatus,
            TASK_STATUS_TOPIC,
            self._task_status_qos,
        )

        if core is not None and runtime_factory is not None:
            raise TypeError('provide either core or runtime_factory')

        if core is not None:
            self._core = core
            self._runtime: RuntimeComposition | None = None
        else:
            runtime = (
                runtime_factory(self)
                if runtime_factory is not None
                else self._create_mock_runtime()
            )
            if not isinstance(runtime, RuntimeComposition):
                raise TypeError(
                    'runtime_factory must return RuntimeComposition'
                )
            self._runtime = runtime
            self._core = runtime.core
            self._command_store = runtime.command_store
            self._execution_store = runtime.execution_store

        self._task_command_subscription = self.create_subscription(
            TaskCommand,
            TASK_COMMAND_TOPIC,
            self._on_task_command,
            self._task_command_qos,
        )

        self.get_logger().info(
            'Mission Manager ROS glue started with runtime_mode=mock'
        )

    @property
    def runtime_mode(self) -> str:
        """Return the explicitly selected runtime mode."""
        return self._runtime_mode

    @property
    def task_command_topic(self) -> str:
        """Return the frozen TaskCommand topic name."""
        return TASK_COMMAND_TOPIC

    @property
    def task_status_topic(self) -> str:
        """Return the frozen TaskStatus topic name."""
        return TASK_STATUS_TOPIC

    @property
    def runtime(self) -> RuntimeComposition | None:
        """Return the assembled runtime for integration tests, if present."""
        return self._runtime

    def _create_mock_runtime(self) -> RuntimeComposition:
        """Build the default deterministic runtime with mock adapters."""
        navigation = MockNavigationAdapter(
            self._on_navigation_event
        )
        safety = MockSafetyAdapter(
            self._on_safety_event
        )

        def resolve_goal(
            command: NormalizedTaskCommand,
            task: object,
        ) -> object:
            """Return a deterministic opaque payload for mock navigation."""
            return ('mock-goal', command.command_id, task.task_id)

        return self._create_runtime(
            navigation=navigation,
            safety=safety,
            goal_resolver=resolve_goal,
        )

    def create_real_runtime(
        self,
        goal_resolver: GoalResolver,
    ) -> RuntimeComposition:
        """Build a real-adapter runtime with an injected goal resolver."""
        navigation = RealNavigationAdapter(
            self,
            self._on_navigation_event,
        )
        safety = RealSafetyAdapter(
            self,
            self._on_safety_event,
        )
        return self._create_runtime(
            navigation=navigation,
            safety=safety,
            goal_resolver=goal_resolver,
        )

    def create_visual_real_runtime(
        self,
        *,
        cleaning_target_topic: str,
        localization_topic: str = LOCALIZATION_TOPIC,
    ) -> RuntimeComposition:
        """Build the real runtime and explicit visual ROS input bridge."""
        if not isinstance(cleaning_target_topic, str):
            raise TypeError('cleaning_target_topic must be str')
        if not cleaning_target_topic.strip():
            raise ValueError('cleaning_target_topic must be non-empty')
        if not isinstance(localization_topic, str):
            raise TypeError('localization_topic must be str')
        if not localization_topic.strip():
            raise ValueError('localization_topic must be non-empty')

        registry = TargetRegistry()
        pose_provider = LatestRobotPoseProvider()
        selector = TargetSelector(registry)
        resolver = VisualTargetGoalResolver(
            registry=registry,
            selector=selector,
            robot_pose_provider=pose_provider,
            now_ros_ns=self._now_ros_ns,
        )
        runtime = self.create_real_runtime(resolver)
        self._visual_target_registry = registry
        self._visual_target_pose_provider = pose_provider
        self._visual_target_bridge = VisualTargetRosBridge(
            node=self,
            core=runtime.core,
            registry=registry,
            pose_provider=pose_provider,
            cleaning_target_topic=cleaning_target_topic,
            localization_topic=localization_topic,
            on_core_result=self._publish_active_runtime_status,
        )
        return runtime

    @property
    def visual_target_bridge(self) -> VisualTargetRosBridge | None:
        """Return the explicit visual runtime bridge, when configured."""
        return self._visual_target_bridge

    def _now_ros_ns(self) -> int:
        """Return current ROS time in integer nanoseconds."""
        return int(self.get_clock().now().nanoseconds)

    def _create_runtime(
        self,
        *,
        navigation: NavigationAdapter,
        safety: SafetyAdapter,
        goal_resolver: GoalResolver,
    ) -> RuntimeComposition:
        """Assemble shared stores, queue, gate and orchestration core."""
        catalog_share = Path(
            get_package_share_directory('cleannav_interfaces')
        )
        catalog = load_task_catalog(
            catalog_share / 'config' / 'task_catalog.yaml'
        )

        command_store = CommandRecordStore(32)

        execution_counter = 0

        def make_execution_id() -> str:
            nonlocal execution_counter
            execution_counter += 1
            return f'ros-execution-{execution_counter}'

        execution_store = ExecutionRecordStore(
            id_factory=make_execution_id,
            terminal_cache=TerminalStatusCache(32),
        )
        core = MissionManagerCore(
            validator=catalog.make_validator(
                min_confidence_by_source=MIN_CONFIDENCE_BY_SOURCE,
            ),
            command_store=command_store,
            mission_queue=MissionQueue(32),
            execution_store=execution_store,
            generation_gate=GenerationGate(),
            navigation=navigation,
            safety=safety,
            clock=_RosClockAdapter(self),
            goal_resolver=goal_resolver,
        )
        return RuntimeComposition(
            core=core,
            command_store=command_store,
            execution_store=execution_store,
            navigation=navigation,
            safety=safety,
        )

    def _on_task_command(
        self,
        message: TaskCommand,
    ) -> CoreResult | None:
        """Convert and submit one TaskCommand without duplicating policy."""
        self.get_logger().info(
            'TASK_RECEIVED '
            f'task_id={int(message.task_id)} '
            f'command_id={message.command_id}'
        )
        try:
            command = ros_task_command_to_normalized(message)
        except RosConversionError as exc:
            self.get_logger().warning(
                'COMMAND_REJECTED '
                'reason=ROS_CONVERSION_ERROR '
                f'task_id={int(message.task_id)} '
                f'command_id={message.command_id} '
                f'detail={exc}'
            )
            return None

        self.get_logger().info(
            'TASK_NORMALIZED '
            f'task_id={command.task_id} '
            f'command_id={command.command_id} '
            f'source={command.source}'
        )
        self._last_command_id = command.command_id
        try:
            result = self._core.submit_command(command)
        except Exception as exc:
            self.get_logger().error(
                'COMMAND_REJECTED '
                'reason=CORE_EXCEPTION '
                f'task_id={command.task_id} '
                f'command_id={command.command_id} '
                f'detail={exc}'
            )
            raise
        immediate_status = getattr(result, 'status_snapshot', None)
        if immediate_status is not None:
            self._publish_status_message(
                status_snapshot_to_ros(immediate_status)
            )
        else:
            self._publish_latest_status(
                command_id=command.command_id,
                execution_id=getattr(result, 'execution_id', None),
            )
        execution_id = getattr(result, 'execution_id', None)
        if getattr(result, 'accepted', False) and execution_id:
            self.get_logger().info(
                'EXECUTION_CREATED '
                f'execution_id={execution_id} '
                f'command_id={command.command_id}'
            )
        elif getattr(result, 'accepted', False):
            reason = getattr(result, 'reason_code', 'UNKNOWN')
            reason_name = getattr(reason, 'name', str(reason))
            self.get_logger().info(
                'COMMAND_ACCEPTED_NO_EXECUTION '
                f'reason={reason_name} '
                f'task_id={command.task_id} '
                f'command_id={command.command_id}'
            )
        elif not getattr(result, 'accepted', False):
            reason = getattr(result, 'reason_code', 'UNKNOWN')
            reason_name = getattr(reason, 'name', str(reason))
            self.get_logger().warning(
                'COMMAND_REJECTED '
                f'reason={reason_name} '
                f'task_id={command.task_id} '
                f'command_id={command.command_id}'
            )
        return result

    def _on_navigation_event(self, event: NavigationEvent) -> CoreResult:
        """Forward a navigation event, then publish current stored status."""
        result = self._core.handle_navigation_event(event)
        if event.event_type is NavigationEventType.GOAL_ACCEPTED:
            self.get_logger().info(
                'NAVIGATION_STARTED '
                f'execution_id={event.handle.execution_id}'
            )
        elif event.event_type is NavigationEventType.SUCCEEDED:
            self.get_logger().info(
                'NAVIGATION_SUCCEEDED '
                f'execution_id={event.handle.execution_id}'
            )
        elif event.event_type in (
            NavigationEventType.GOAL_REJECTED,
            NavigationEventType.FAILED,
        ):
            self.get_logger().warning(
                'NAVIGATION_FAILED '
                f'execution_id={event.handle.execution_id}'
            )
        execution_id = event.handle.execution_id
        command_id = self._command_id_for_execution(execution_id)
        self._publish_latest_status(
            command_id=command_id,
            execution_id=execution_id,
        )
        return result

    def _on_safety_event(self, event: SafetyEvent) -> CoreResult:
        """Forward a safety event, then publish current stored status."""
        result = self._core.handle_safety_event(event)
        if event.event_type is SafetyEventType.LEASE_ACQUIRED:
            self.get_logger().info(
                'SAFETY_LEASE_ACQUIRED '
                f'execution_id={event.execution_id}'
            )
        elif event.event_type is SafetyEventType.LEASE_RELEASED:
            self.get_logger().info(
                'SAFETY_LEASE_RELEASED '
                f'execution_id={event.execution_id}'
            )
        execution_id = event.execution_id
        command_id = self._command_id_for_execution(execution_id)
        self._publish_latest_status(
            command_id=command_id or self._last_command_id,
            execution_id=execution_id,
        )
        if (
            event.event_type is SafetyEventType.LEASE_RELEASED
            and self._core.active_execution is None
        ):
            self.get_logger().info(
                'EXECUTION_FINISHED '
                f'execution_id={execution_id}'
            )
        return result

    def _command_id_for_execution(
        self,
        execution_id: str | None,
    ) -> str | None:
        """Read the command identity without interpreting event semantics."""
        if execution_id is None or self._execution_store is None:
            return None
        record = self._execution_store.get(execution_id)
        return record.command_id if record is not None else None

    def _publish_active_runtime_status(self, result: object) -> None:
        """Publish status after a visual bridge Core entry point returns."""
        execution_id = getattr(result, 'execution_id', None)
        if execution_id is None:
            active_execution = getattr(self._core, 'active_execution', None)
            if active_execution is not None:
                execution_id = active_execution.execution_id
        if execution_id is None:
            return
        self._publish_latest_status(
            command_id=self._command_id_for_execution(execution_id),
            execution_id=execution_id,
        )

    def _publish_latest_status(
        self,
        *,
        command_id: str | None,
        execution_id: str | None,
    ) -> None:
        """Publish currently stored command and execution snapshots."""
        snapshots = []
        if command_id is not None and self._command_store is not None:
            snapshot = self._command_store.get_current_command_status(
                command_id
            )
            if snapshot is not None:
                snapshots.append(snapshot)

        if execution_id is not None and self._execution_store is not None:
            snapshot = self._execution_store.get_current_status(execution_id)
            if snapshot is not None:
                snapshots.append(snapshot)

        for snapshot in snapshots:
            self._publish_status_message(status_snapshot_to_ros(snapshot))

    def _publish_status_message(self, message: object) -> None:
        """Publish one already-converted TaskStatus message."""
        self._task_status_publisher.publish(message)


def main(args=None) -> None:
    """Run the Mission Manager ROS glue node."""
    rclpy.init(args=args)
    node: MissionManagerNode | None = None
    try:
        node = MissionManagerNode()
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


__all__ = [
    'MissionManagerNode',
    'RuntimeComposition',
    'main',
]
