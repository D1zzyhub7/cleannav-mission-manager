"""Deterministic demo task goals shared by demo runtime compositions."""

from __future__ import annotations

from geometry_msgs.msg import PoseStamped


# task30 remains the current PC smoke-test goal.  The production far goal is
# intentionally not part of this demo composition.
DEMO_TASK_GOALS = {
    30: (-4.0, 0.0, 0.0),
}


def make_demo_goal(
    command: object,
    task: object,
) -> object:
    """Resolve task30 to the frozen map-frame smoke-test pose."""
    task_id = int(getattr(task, 'task_id'))
    target = DEMO_TASK_GOALS.get(task_id)

    if target is None:
        return (
            'hil-demo-goal',
            str(getattr(command, 'command_id')),
            task_id,
        )

    x, y, z = target
    pose = PoseStamped()
    pose.header.frame_id = 'map'
    pose.pose.position.x = x
    pose.pose.position.y = y
    pose.pose.position.z = z
    pose.pose.orientation.w = 1.0
    return pose


__all__ = [
    'DEMO_TASK_GOALS',
    'make_demo_goal',
]
