"""Unit tests for the framework-only mock navigation adapter."""

from dataclasses import FrozenInstanceError
import inspect

import pytest

from cleannav_mission_manager.adapters.mock_navigation import (
    MockNavigationAdapter,
    NavigationCancelCall,
    NavigationSubmitCall,
)
from cleannav_mission_manager.adapters.navigation import (
    NavigationEvent,
    NavigationEventType,
)
from cleannav_mission_manager.domain.generation_gate import (
    GenerationHandle,
)


def _handle(execution_id='execution-1', generation=1):
    return GenerationHandle(
        execution_id=execution_id,
        generation=generation,
    )


def _adapter(received=None):
    if received is None:
        received = []
    return MockNavigationAdapter(received.append), received


def test_navigation_event_types_match_minimal_contract():
    assert [event.value for event in NavigationEventType] == [
        'GOAL_ACCEPTED',
        'GOAL_REJECTED',
        'SUCCEEDED',
        'FAILED',
        'CANCEL_CONFIRMED',
        'CANCEL_FAILED',
    ]


def test_navigation_event_is_immutable_and_preserves_payload():
    payload = object()
    event = NavigationEvent(
        handle=_handle(),
        event_type=NavigationEventType.SUCCEEDED,
        payload=payload,
    )

    assert event.payload is payload
    with pytest.raises(FrozenInstanceError):
        event.payload = object()


def test_submit_goal_records_exact_handle_and_opaque_payload():
    adapter, received = _adapter()
    handle = _handle()
    payload = object()

    adapter.submit_goal(handle, payload)

    assert adapter.submitted_calls == (
        NavigationSubmitCall(handle=handle, goal_payload=payload),
    )
    assert adapter.submitted_calls[0].handle is handle
    assert adapter.submitted_calls[0].goal_payload is payload
    assert received == []


def test_cancel_goal_records_exact_handle_without_automatic_event():
    adapter, received = _adapter()
    handle = _handle()

    adapter.cancel_goal(handle)

    assert adapter.cancel_calls == (NavigationCancelCall(handle=handle),)
    assert adapter.cancel_calls[0].handle is handle
    assert received == []


def test_call_properties_are_immutable_tuple_snapshots():
    adapter, _ = _adapter()
    first_handle = _handle()
    adapter.submit_goal(first_handle, object())
    adapter.cancel_goal(first_handle)

    submitted_snapshot = adapter.submitted_calls
    cancel_snapshot = adapter.cancel_calls
    adapter.submit_goal(_handle(generation=2), object())
    adapter.cancel_goal(_handle(generation=2))

    assert isinstance(submitted_snapshot, tuple)
    assert isinstance(cancel_snapshot, tuple)
    assert len(submitted_snapshot) == 1
    assert len(cancel_snapshot) == 1
    assert len(adapter.submitted_calls) == 2
    assert len(adapter.cancel_calls) == 2


def test_duplicate_submit_is_recorded_without_generation_policy():
    adapter, _ = _adapter()
    handle = _handle()
    payload = object()

    adapter.submit_goal(handle, payload)
    adapter.submit_goal(handle, payload)

    assert len(adapter.submitted_calls) == 2


def test_duplicate_cancel_is_recorded_without_generation_policy():
    adapter, _ = _adapter()
    handle = _handle()

    adapter.cancel_goal(handle)
    adapter.cancel_goal(handle)

    assert len(adapter.cancel_calls) == 2


def test_injected_event_is_forwarded_once_without_copying():
    adapter, received = _adapter()
    event = NavigationEvent(
        handle=_handle(),
        event_type=NavigationEventType.GOAL_ACCEPTED,
    )

    adapter.inject_event(event)

    assert received == [event]
    assert received[0] is event


@pytest.mark.parametrize('event_type', list(NavigationEventType))
def test_every_navigation_event_type_can_be_injected(event_type):
    adapter, received = _adapter()
    event = NavigationEvent(
        handle=_handle(),
        event_type=event_type,
        payload=object(),
    )

    adapter.inject_event(event)

    assert received == [event]


def test_stale_generation_event_is_not_filtered():
    adapter, received = _adapter()
    adapter.submit_goal(_handle(generation=2), object())
    stale_event = NavigationEvent(
        handle=_handle(generation=1),
        event_type=NavigationEventType.SUCCEEDED,
    )

    adapter.inject_event(stale_event)

    assert received == [stale_event]
    assert received[0].handle.generation == 1


def test_never_submitted_handle_event_is_forwarded():
    adapter, received = _adapter()
    event = NavigationEvent(
        handle=_handle(execution_id='unknown', generation=9),
        event_type=NavigationEventType.FAILED,
    )

    adapter.inject_event(event)

    assert received == [event]


@pytest.mark.parametrize('invalid_handle', [None, ('execution-1', 1)])
@pytest.mark.parametrize('method_name', ['submit_goal', 'cancel_goal'])
def test_public_call_apis_reject_invalid_handle(
    invalid_handle,
    method_name,
):
    adapter, _ = _adapter()

    with pytest.raises(TypeError):
        if method_name == 'submit_goal':
            adapter.submit_goal(invalid_handle, object())
        else:
            adapter.cancel_goal(invalid_handle)


@pytest.mark.parametrize('invalid_sink', [None, object(), 1])
def test_constructor_rejects_non_callable_sink(invalid_sink):
    with pytest.raises(TypeError):
        MockNavigationAdapter(invalid_sink)


@pytest.mark.parametrize('invalid_event', [None, object(), 'event'])
def test_inject_event_rejects_invalid_type(invalid_event):
    adapter, _ = _adapter()

    with pytest.raises(TypeError):
        adapter.inject_event(invalid_event)


def test_navigation_event_rejects_invalid_handle():
    with pytest.raises(TypeError):
        NavigationEvent(
            handle=('execution-1', 1),
            event_type=NavigationEventType.SUCCEEDED,
        )


def test_navigation_event_rejects_invalid_event_type():
    with pytest.raises(TypeError):
        NavigationEvent(
            handle=_handle(),
            event_type='SUCCEEDED',
        )


def test_production_sources_have_only_framework_dependencies():
    modules = [
        __import__(
            'cleannav_mission_manager.adapters.navigation',
            fromlist=['navigation'],
        ),
        __import__(
            'cleannav_mission_manager.adapters.mock_navigation',
            fromlist=['mock_navigation'],
        ),
    ]
    source = '\n'.join(inspect.getsource(module) for module in modules)

    for forbidden in (
        'rclpy',
        'rospy',
        'nav2',
        'geometry_msgs',
        'nav_msgs',
        'cleannav_interfaces',
        'ExecutionStore',
        'StateMachineContext',
        'GenerationGate',
    ):
        assert forbidden not in source


def test_production_sources_have_no_business_hardcoding():
    modules = [
        __import__(
            'cleannav_mission_manager.adapters.navigation',
            fromlist=['navigation'],
        ),
        __import__(
            'cleannav_mission_manager.adapters.mock_navigation',
            fromlist=['mock_navigation'],
        ),
    ]
    source = '\n'.join(inspect.getsource(module) for module in modules)

    for forbidden in (
        'task_id',
        'CleaningTarget',
        'leaf',
        'puddle',
        'target_id',
    ):
        assert forbidden not in source
