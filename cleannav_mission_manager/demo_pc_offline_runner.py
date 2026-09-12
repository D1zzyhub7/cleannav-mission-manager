#!/usr/bin/env python3
"""Demo-only PC offline runtime using real ROS navigation and Safety APIs."""

from __future__ import annotations

import rclpy
from rclpy.executors import ExternalShutdownException

from cleannav_mission_manager.adapters.real_navigation import (
    RealNavigationAdapter,
)
from cleannav_mission_manager.adapters.real_safety import RealSafetyAdapter
from cleannav_mission_manager.core import GoalResolutionError
from cleannav_mission_manager.demo_goals import (
    DEMO_TASK_GOALS,
    make_demo_goal,
)
from cleannav_mission_manager.node import (
    MissionManagerNode,
    RuntimeComposition,
)


def make_pc_offline_demo_goal(
    command: object,
    task: object,
) -> object:
    """
    Resolve only the supported PC offline smoke-test task.

    The shared HIL resolver keeps its legacy opaque fallback for compatibility.
    A real NavigationAdapter requires a PoseStamped, so this composition fails
    unsupported demo tasks deterministically instead of submitting an invalid
    payload.
    """
    task_id = int(getattr(task, 'task_id'))
    if task_id not in DEMO_TASK_GOALS:
        raise GoalResolutionError(
            'PC offline demo goal is unsupported for '
            f'task_id={task_id}'
        )
    return make_demo_goal(command, task)


def create_pc_offline_runtime(
    node: MissionManagerNode,
) -> RuntimeComposition:
    """Inject real Navigation and Safety adapters for PC-only execution."""
    return node.create_real_runtime(make_pc_offline_demo_goal)


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
    'create_pc_offline_runtime',
    'main',
    'make_pc_offline_demo_goal',
]
