"""Unit tests for the ROS transport navigation adapter."""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest
from action_msgs.msg import GoalStatus
from action_msgs.srv import CancelGoal
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose
from rclpy.task import Future

from cleannav_mission_manager.adapters.navigation import (
    NavigationEventType,
)
from cleannav_mission_manager.adapters.real_navigation import (
    NAVIGATION_ACTION_NAME,
    RealNavigationAdapter,
)
from cleannav_mission_manager.domain.generation_gate import (
    GenerationHandle,
)


class FakeClientGoalHandle:
    """Minimal rclpy ActionClient goal-handle double."""

    def __init__(self, *, accepted=True):
        self.accepted = accepted
        self.goal_id = SimpleNamespace(uuid=bytes(range(16)))
        self.result_future = Future()
        self.cancel_future = Future()
        self.cancel_calls = 0

    def get_result_async(self):
        return self.result_future

    def cancel_goal_async(self):
        self.cancel_calls += 1
        return self.cancel_future


class FakeActionClient:
    """Action client double whose futures are controlled by each test."""

    def __init__(self, *, ready=True):
        self.ready = ready
        self.send_futures = []
        self.sent_goals = []

    def server_is_ready(self):
        return self.ready

    def send_goal_async(self, goal):
        future = Future()
        self.sent_goals.append(goal)
        self.send_futures.append(future)
        return future


def _handle(execution_id='execution-1', generation=1):
    return GenerationHandle(
        execution_id=execution_id,
        generation=generation,
    )


def _pose(x=1.0, y=2.0):
    pose = PoseStamped()
    pose.header.frame_id = 'map'
    pose.pose.position.x = x
    pose.pose.position.y = y
    pose.pose.orientation.w = 1.0
    return pose


def _result(status):
    return SimpleNamespace(
        status=status,
        result=SimpleNamespace(),
    )


def _cancel_response(goal_handle, *, return_code=None, include_goal=True):
    if return_code is None:
        return_code = CancelGoal.Response.ERROR_NONE
    goals_canceling = []
    if include_goal:
        goals_canceling.append(
            SimpleNamespace(
                goal_id=SimpleNamespace(uuid=goal_handle.goal_id.uuid),
            )
        )
    return SimpleNamespace(
        return_code=return_code,
        goals_canceling=goals_canceling,
    )


def _adapter(*, client=None, received=None, **kwargs):
    if client is None:
        client = FakeActionClient()
    if received is None:
        received = []
    adapter = RealNavigationAdapter(
        object(),
        received.append,
        action_client=client,
        **kwargs,
    )
    return adapter, client, received


def _accept(client, goal_handle):
    client.send_futures[-1].set_result(goal_handle)


def test_action_endpoint_and_type_are_frozen():
    assert NAVIGATION_ACTION_NAME == '/cleannav/navigate_to_pose'
    assert hasattr(NavigateToPose.Goal(), 'pose')
    assert hasattr(NavigateToPose.Goal(), 'behavior_tree')


@pytest.mark.parametrize('invalid_sink', [None, object(), 1])
def test_constructor_rejects_non_callable_sink(invalid_sink):
    with pytest.raises(TypeError):
        RealNavigationAdapter(
            object(),
            invalid_sink,
            action_client=FakeActionClient(),
        )


def test_constructor_validates_action_name():
    with pytest.raises(TypeError):
        RealNavigationAdapter(
            object(),
            lambda event: None,
            action_name=1,
            action_client=FakeActionClient(),
        )
    with pytest.raises(ValueError):
        RealNavigationAdapter(
            object(),
            lambda event: None,
            action_name='',
            action_client=FakeActionClient(),
        )


@pytest.mark.parametrize('invalid_handle', [None, ('execution-1', 1)])
def test_public_methods_reject_invalid_generation_handle(invalid_handle):
    adapter, _, _ = _adapter()
    with pytest.raises(TypeError):
        adapter.submit_goal(invalid_handle, _pose())
    with pytest.raises(TypeError):
        adapter.cancel_goal(invalid_handle)


def test_submit_rejects_non_pose_payload():
    adapter, _, _ = _adapter()
    with pytest.raises(TypeError):
        adapter.submit_goal(_handle(), object())


def test_pose_is_copied_and_behavior_tree_stays_default():
    adapter, client, _ = _adapter()
    payload = _pose(3.0, 4.0)

    adapter.submit_goal(_handle(), payload)

    sent_goal = client.sent_goals[0]
    assert isinstance(sent_goal, NavigateToPose.Goal)
    assert sent_goal.pose is not payload
    assert sent_goal.pose.pose.position.x == 3.0
    assert sent_goal.pose.pose.position.y == 4.0
    assert sent_goal.behavior_tree == ''
    payload.pose.position.x = 99.0
    assert sent_goal.pose.pose.position.x == 3.0


def test_server_unavailable_emits_goal_rejected_without_waiting():
    client = FakeActionClient(ready=False)
    adapter, _, received = _adapter(client=client)

    adapter.submit_goal(_handle(), _pose())

    assert [event.event_type for event in received] == [
        NavigationEventType.GOAL_REJECTED,
    ]
    assert client.sent_goals == []


def test_send_future_exception_emits_goal_rejected():
    adapter, client, received = _adapter()
    adapter.submit_goal(_handle(), _pose())
    client.send_futures[0].set_exception(RuntimeError('send failed'))

    assert [event.event_type for event in received] == [
        NavigationEventType.GOAL_REJECTED,
    ]


def test_server_rejected_goal_emits_only_goal_rejected():
    adapter, client, received = _adapter()
    adapter.submit_goal(_handle(), _pose())
    _accept(client, FakeClientGoalHandle(accepted=False))

    assert [event.event_type for event in received] == [
        NavigationEventType.GOAL_REJECTED,
    ]


def test_accepted_goal_emits_original_client_goal_handle():
    adapter, client, received = _adapter()
    goal_handle = FakeClientGoalHandle()
    handle = _handle()
    adapter.submit_goal(handle, _pose())
    _accept(client, goal_handle)

    assert received[0].event_type is NavigationEventType.GOAL_ACCEPTED
    assert received[0].handle is handle
    assert received[0].payload is goal_handle


def test_succeeded_result_maps_to_succeeded():
    adapter, client, received = _adapter()
    goal_handle = FakeClientGoalHandle()
    adapter.submit_goal(_handle(), _pose())
    _accept(client, goal_handle)
    result = _result(GoalStatus.STATUS_SUCCEEDED)
    goal_handle.result_future.set_result(result)

    assert [event.event_type for event in received] == [
        NavigationEventType.GOAL_ACCEPTED,
        NavigationEventType.SUCCEEDED,
    ]
    assert received[-1].payload is result


def test_aborted_result_maps_to_failed():
    adapter, client, received = _adapter()
    goal_handle = FakeClientGoalHandle()
    adapter.submit_goal(_handle(), _pose())
    _accept(client, goal_handle)
    goal_handle.result_future.set_result(
        _result(GoalStatus.STATUS_ABORTED)
    )

    assert [event.event_type for event in received] == [
        NavigationEventType.GOAL_ACCEPTED,
        NavigationEventType.FAILED,
    ]


def test_unrequested_canceled_result_maps_to_failed():
    adapter, client, received = _adapter()
    goal_handle = FakeClientGoalHandle()
    adapter.submit_goal(_handle(), _pose())
    _accept(client, goal_handle)
    goal_handle.result_future.set_result(
        _result(GoalStatus.STATUS_CANCELED)
    )

    assert [event.event_type for event in received] == [
        NavigationEventType.GOAL_ACCEPTED,
        NavigationEventType.FAILED,
    ]


def test_cancel_before_goal_response_is_preserved():
    adapter, client, received = _adapter()
    goal_handle = FakeClientGoalHandle()
    handle = _handle()
    adapter.submit_goal(handle, _pose())
    adapter.cancel_goal(handle)

    assert goal_handle.cancel_calls == 0
    _accept(client, goal_handle)

    assert goal_handle.cancel_calls == 1
    assert [event.event_type for event in received] == [
        NavigationEventType.GOAL_ACCEPTED,
    ]


def test_accepted_pending_cancel_immediately_issues_cancel():
    adapter, client, received = _adapter()
    goal_handle = FakeClientGoalHandle()
    adapter.submit_goal(_handle(), _pose())
    _accept(client, goal_handle)
    adapter.cancel_goal(_handle(generation=2))

    assert goal_handle.cancel_calls == 0
    assert len(received) == 1

    # The cancel request must use the exact submitted GenerationHandle.
    adapter.cancel_goal(received[0].handle)
    assert goal_handle.cancel_calls == 1


def test_cancel_response_and_canceled_result_confirm_cancel():
    adapter, client, received = _adapter()
    goal_handle = FakeClientGoalHandle()
    handle = _handle()
    adapter.submit_goal(handle, _pose())
    _accept(client, goal_handle)
    adapter.cancel_goal(handle)
    goal_handle.cancel_future.set_result(_cancel_response(goal_handle))
    goal_handle.result_future.set_result(
        _result(GoalStatus.STATUS_CANCELED)
    )

    assert [event.event_type for event in received] == [
        NavigationEventType.GOAL_ACCEPTED,
        NavigationEventType.CANCEL_CONFIRMED,
    ]
    assert received[-1].handle is handle


@pytest.mark.parametrize(
    'response',
    [
        lambda goal: _cancel_response(
            goal,
            return_code=CancelGoal.Response.ERROR_REJECTED,
        ),
        lambda goal: _cancel_response(goal, include_goal=False),
    ],
)
def test_cancel_response_failure_maps_to_cancel_failed(response):
    adapter, client, received = _adapter()
    goal_handle = FakeClientGoalHandle()
    handle = _handle()
    adapter.submit_goal(handle, _pose())
    _accept(client, goal_handle)
    adapter.cancel_goal(handle)
    goal_handle.cancel_future.set_result(response(goal_handle))

    assert [event.event_type for event in received] == [
        NavigationEventType.GOAL_ACCEPTED,
        NavigationEventType.CANCEL_FAILED,
    ]


def test_canceled_result_before_cancel_response_waits_for_both():
    adapter, client, received = _adapter()
    goal_handle = FakeClientGoalHandle()
    handle = _handle()
    adapter.submit_goal(handle, _pose())
    _accept(client, goal_handle)
    adapter.cancel_goal(handle)
    goal_handle.result_future.set_result(
        _result(GoalStatus.STATUS_CANCELED)
    )

    assert [event.event_type for event in received] == [
        NavigationEventType.GOAL_ACCEPTED,
    ]
    goal_handle.cancel_future.set_result(_cancel_response(goal_handle))
    assert [event.event_type for event in received] == [
        NavigationEventType.GOAL_ACCEPTED,
        NavigationEventType.CANCEL_CONFIRMED,
    ]


def test_natural_success_wins_cancel_race():
    adapter, client, received = _adapter()
    goal_handle = FakeClientGoalHandle()
    handle = _handle()
    adapter.submit_goal(handle, _pose())
    _accept(client, goal_handle)
    adapter.cancel_goal(handle)
    goal_handle.result_future.set_result(
        _result(GoalStatus.STATUS_SUCCEEDED)
    )
    goal_handle.cancel_future.set_result(_cancel_response(goal_handle))

    assert [event.event_type for event in received] == [
        NavigationEventType.GOAL_ACCEPTED,
        NavigationEventType.SUCCEEDED,
    ]


def test_natural_failure_wins_cancel_race():
    adapter, client, received = _adapter()
    goal_handle = FakeClientGoalHandle()
    handle = _handle()
    adapter.submit_goal(handle, _pose())
    _accept(client, goal_handle)
    adapter.cancel_goal(handle)
    goal_handle.result_future.set_result(
        _result(GoalStatus.STATUS_ABORTED)
    )
    goal_handle.cancel_future.set_result(_cancel_response(goal_handle))

    assert [event.event_type for event in received] == [
        NavigationEventType.GOAL_ACCEPTED,
        NavigationEventType.FAILED,
    ]


def test_terminal_event_is_emitted_exactly_once():
    adapter, client, received = _adapter()
    goal_handle = FakeClientGoalHandle()
    handle = _handle()
    adapter.submit_goal(handle, _pose())
    _accept(client, goal_handle)
    goal_handle.result_future.set_result(
        _result(GoalStatus.STATUS_SUCCEEDED)
    )
    adapter.cancel_goal(handle)

    assert [event.event_type for event in received] == [
        NavigationEventType.GOAL_ACCEPTED,
        NavigationEventType.SUCCEEDED,
    ]


def test_late_old_generation_callback_is_forwarded_unchanged():
    adapter, client, received = _adapter()
    first = _handle(generation=1)
    second = _handle(generation=2)
    first_goal = FakeClientGoalHandle()
    second_goal = FakeClientGoalHandle()

    adapter.submit_goal(first, _pose(1.0, 1.0))
    _accept(client, first_goal)
    adapter.submit_goal(second, _pose(2.0, 2.0))
    _accept(client, second_goal)
    first_goal.result_future.set_result(
        _result(GoalStatus.STATUS_SUCCEEDED)
    )

    assert received[-1].event_type is NavigationEventType.SUCCEEDED
    assert received[-1].handle is first


def test_production_source_has_no_manager_policy_or_control_logic():
    source_path = (
        Path(__file__).resolve().parents[2]
        / 'cleannav_mission_manager'
        / 'adapters'
        / 'real_navigation.py'
    )
    source = source_path.read_text(encoding='utf-8')
    for forbidden in (
        'MissionManagerCore',
        'GenerationGate(',
        'state_machine',
        'task_id',
        'CleaningTarget',
        'cmd_vel',
        'Safety',
        'MPPI',
        'Ackermann',
    ):
        assert forbidden not in source


def test_production_source_has_no_blocking_or_asyncio_runtime():
    source_path = (
        Path(__file__).resolve().parents[2]
        / 'cleannav_mission_manager'
        / 'adapters'
        / 'real_navigation.py'
    )
    source = source_path.read_text(encoding='utf-8')
    for forbidden in (
        'spin_until_future_complete',
        'time.sleep',
        'asyncio',
    ):
        assert forbidden not in source
    assert 'add_done_callback' in source
    assert inspect.isfunction(RealNavigationAdapter.submit_goal)
