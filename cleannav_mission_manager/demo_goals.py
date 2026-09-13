"""Deterministic demo task goals shared by demo runtime compositions."""

from __future__ import annotations

import math

from geometry_msgs.msg import PoseStamped


# task30 remains the current PC smoke-test goal.  The production far goal is
# intentionally not part of this demo composition.
DEFAULT_DEMO_GOAL = (-4.0, 0.0, 0.0)
DEMO_TASK_GOALS = {
    30: DEFAULT_DEMO_GOAL,
}


def validate_demo_goal(goal: object) -> tuple[float, float, float]:
    """Validate one demo-only ``(x, y, yaw)`` goal tuple."""
    if not isinstance(goal, (tuple, list)) or len(goal) != 3:
        raise ValueError("demo goal must contain x, y and yaw")
    values = tuple(float(value) for value in goal)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("demo goal values must be finite")
    return values


def demo_goal_from_node(node: object) -> tuple[float, float, float]:
    """Read the three demo-only parameters from a ROS node."""
    get_parameter = getattr(node, "get_parameter")
    return validate_demo_goal(
        (
            get_parameter("demo_goal_x").value,
            get_parameter("demo_goal_y").value,
            get_parameter("demo_goal_yaw").value,
        )
    )


def make_demo_goal(
    command: object,
    task: object,
    *,
    demo_goal: object | None = None,
) -> object:
    """Resolve task30 using a demo-only map-frame pose."""
    task_id = int(getattr(task, 'task_id'))
    target = DEMO_TASK_GOALS.get(task_id)

    if target is None:
        return (
            'hil-demo-goal',
            str(getattr(command, 'command_id')),
            task_id,
        )

    if demo_goal is not None:
        target = validate_demo_goal(demo_goal)

    x, y, yaw = target
    pose = PoseStamped()
    pose.header.frame_id = 'map'
    pose.pose.position.x = x
    pose.pose.position.y = y
    pose.pose.orientation.z = math.sin(yaw / 2.0)
    pose.pose.orientation.w = math.cos(yaw / 2.0)
    return pose


__all__ = [
    'DEFAULT_DEMO_GOAL',
    'DEMO_TASK_GOALS',
    'demo_goal_from_node',
    'make_demo_goal',
    'validate_demo_goal',
]
