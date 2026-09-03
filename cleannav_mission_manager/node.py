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
from cleannav_mission_manager.adapters.navigation import NavigationEvent
from cleannav_mission_manager.adapters.safety import SafetyEvent
from cleannav_mission_manager.core import (
    CoreResult,
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
from cleannav_mission_manager.domain.task_catalog import load_task_catalog
from cleannav_mission_manager.ros_conversion import (
    RosConversionError,
    ros_task_command_to_normalized,
    status_snapshot_to_ros,
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
    """Concrete dependencies assembled for the explicit mock runtime."""

    core: MissionManagerCore
    command_store: CommandRecordStore
    execution_store: ExecutionRecordStore
    navigation: MockNavigationAdapter
    safety: MockSafetyAdapter


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
        """Return the mock composition for integration tests, if present."""
        return self._runtime

    def _create_mock_runtime(self) -> RuntimeComposition:
        """Build the only currently supported, explicitly named runtime."""
        catalog_share = Path(
            get_package_share_directory('cleannav_interfaces')
        )
        catalog = load_task_catalog(
            catalog_share / 'config' / 'task_catalog.yaml'
        )

        navigation = MockNavigationAdapter(
            lambda event: self._on_navigation_event(event)
        )
        safety = MockSafetyAdapter(
            lambda event: self._on_safety_event(event)
        )

        execution_counter = 0

        def make_execution_id() -> str:
            nonlocal execution_counter
            execution_counter += 1
            return f'ros-execution-{execution_counter}'

        def resolve_goal(
            command: NormalizedTaskCommand,
            task: object,
        ) -> object:
            """Return a deterministic opaque payload for mock navigation."""
            return ('mock-goal', command.command_id, task.task_id)

        command_store = CommandRecordStore(32)
        execution_store = ExecutionRecordStore(
            id_factory=make_execution_id,
            terminal_cache=TerminalStatusCache(32),
        )
        core = MissionManagerCore(
            validator=catalog.make_validator(),
            command_store=command_store,
            mission_queue=MissionQueue(32),
            execution_store=execution_store,
            generation_gate=GenerationGate(),
            navigation=navigation,
            safety=safety,
            clock=_RosClockAdapter(self),
            goal_resolver=resolve_goal,
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
        try:
            command = ros_task_command_to_normalized(message)
        except RosConversionError as exc:
            self.get_logger().warning(
                f'Ignoring invalid TaskCommand representation: {exc}'
            )
            return None

        self._last_command_id = command.command_id
        result = self._core.submit_command(command)
        self._publish_latest_status(
            command_id=command.command_id,
            execution_id=getattr(result, 'execution_id', None),
        )
        return result

    def _on_navigation_event(self, event: NavigationEvent) -> CoreResult:
        """Forward a navigation event, then publish current stored status."""
        result = self._core.handle_navigation_event(event)
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
        execution_id = event.execution_id
        command_id = self._command_id_for_execution(execution_id)
        self._publish_latest_status(
            command_id=command_id or self._last_command_id,
            execution_id=execution_id,
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
