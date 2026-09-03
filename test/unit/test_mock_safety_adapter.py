"""Unit tests for the framework-only mock safety adapter."""

from dataclasses import FrozenInstanceError, fields
import inspect

import pytest

from cleannav_mission_manager.adapters.mock_safety import (
    MockSafetyAdapter,
    SafetyCall,
    SafetyCallType,
)
from cleannav_mission_manager.adapters.safety import (
    SafetyAdapter,
    SafetyEvent,
    SafetyEventType,
)


def _adapter(**kwargs):
    received = []
    return MockSafetyAdapter(received.append, **kwargs), received


def test_protocol_and_mock_can_be_constructed():
    adapter, _ = _adapter()

    assert all(
        hasattr(adapter, method_name)
        for method_name in (
            'acquire_lease',
            'release_lease',
            'request_emergency_stop',
            'reset_emergency_stop',
        )
    )
    assert all(hasattr(SafetyAdapter, method_name) for method_name in (
        'acquire_lease',
        'release_lease',
        'request_emergency_stop',
        'reset_emergency_stop',
    ))


def test_safety_event_types_match_minimal_contract():
    assert [event.value for event in SafetyEventType] == [
        'LEASE_ACQUIRED',
        'LEASE_ACQUIRE_FAILED',
        'LEASE_RELEASED',
        'LEASE_RELEASE_FAILED',
        'RESET_EMERGENCY_STOP_SUCCEEDED',
        'RESET_EMERGENCY_STOP_FAILED',
    ]


def test_lease_event_requires_execution_id_and_reset_event_does_not_use_one():
    lease_event = SafetyEvent(
        event_type=SafetyEventType.LEASE_ACQUIRED,
        execution_id='execution-1',
    )
    reset_event = SafetyEvent(
        event_type=SafetyEventType.RESET_EMERGENCY_STOP_SUCCEEDED,
    )

    assert lease_event.execution_id == 'execution-1'
    assert reset_event.execution_id is None

    with pytest.raises(TypeError):
        SafetyEvent(event_type=SafetyEventType.LEASE_ACQUIRED)
    with pytest.raises(ValueError):
        SafetyEvent(
            event_type=SafetyEventType.RESET_EMERGENCY_STOP_FAILED,
            execution_id='execution-1',
        )


def test_safety_event_is_immutable():
    event = SafetyEvent(
        event_type=SafetyEventType.LEASE_ACQUIRED,
        execution_id='execution-1',
    )

    with pytest.raises(FrozenInstanceError):
        event.execution_id = 'execution-2'


def test_acquire_success_is_recorded_and_explicitly_emitted():
    adapter, received = _adapter(acquire_success=True)

    adapter.acquire_lease('execution-1')

    assert adapter.calls == (
        SafetyCall(SafetyCallType.ACQUIRE_LEASE, 'execution-1'),
    )
    assert received == []

    adapter.emit_acquire_result('execution-1')

    assert received == [
        SafetyEvent(
            event_type=SafetyEventType.LEASE_ACQUIRED,
            execution_id='execution-1',
        ),
    ]


def test_acquire_failure_is_explicitly_emitted():
    adapter, received = _adapter(acquire_success=False)

    adapter.acquire_lease('execution-1')
    adapter.emit_acquire_result('execution-1')

    assert received == [
        SafetyEvent(
            event_type=SafetyEventType.LEASE_ACQUIRE_FAILED,
            execution_id='execution-1',
        ),
    ]


def test_release_success_preserves_execution_id():
    adapter, received = _adapter(release_success=True)

    adapter.release_lease('execution-7')
    adapter.emit_release_result('execution-7')

    assert adapter.calls == (
        SafetyCall(SafetyCallType.RELEASE_LEASE, 'execution-7'),
    )
    assert received == [
        SafetyEvent(
            event_type=SafetyEventType.LEASE_RELEASED,
            execution_id='execution-7',
        ),
    ]


def test_release_failure_is_explicitly_emitted():
    adapter, received = _adapter(release_success=False)

    adapter.release_lease('execution-7')
    adapter.emit_release_result('execution-7')

    assert received == [
        SafetyEvent(
            event_type=SafetyEventType.LEASE_RELEASE_FAILED,
            execution_id='execution-7',
        ),
    ]


def test_emergency_stop_is_recorded_without_inventing_a_result_event():
    adapter, received = _adapter()

    adapter.request_emergency_stop()

    assert adapter.calls == (
        SafetyCall(SafetyCallType.REQUEST_EMERGENCY_STOP),
    )
    assert received == []
    assert 'generation' not in {field.name for field in fields(SafetyCall)}


def test_reset_success_is_recorded_and_explicitly_emitted():
    adapter, received = _adapter(reset_success=True)

    adapter.reset_emergency_stop()
    adapter.emit_reset_result()

    assert adapter.calls == (
        SafetyCall(SafetyCallType.RESET_EMERGENCY_STOP),
    )
    assert received == [
        SafetyEvent(
            event_type=SafetyEventType.RESET_EMERGENCY_STOP_SUCCEEDED,
        ),
    ]


def test_reset_failure_is_explicitly_emitted():
    adapter, received = _adapter(reset_success=False)

    adapter.reset_emergency_stop()
    adapter.emit_reset_result()

    assert received == [
        SafetyEvent(event_type=SafetyEventType.RESET_EMERGENCY_STOP_FAILED),
    ]


def test_call_order_is_deterministic():
    adapter, _ = _adapter()

    adapter.acquire_lease('execution-1')
    adapter.request_emergency_stop()
    adapter.release_lease('execution-1')
    adapter.reset_emergency_stop()

    assert adapter.calls == (
        SafetyCall(SafetyCallType.ACQUIRE_LEASE, 'execution-1'),
        SafetyCall(SafetyCallType.REQUEST_EMERGENCY_STOP),
        SafetyCall(SafetyCallType.RELEASE_LEASE, 'execution-1'),
        SafetyCall(SafetyCallType.RESET_EMERGENCY_STOP),
    )


def test_lease_events_for_two_execution_ids_do_not_cross_wire():
    adapter, received = _adapter()

    adapter.acquire_lease('execution-1')
    adapter.acquire_lease('execution-2')
    adapter.emit_acquire_result('execution-2')
    adapter.emit_acquire_result('execution-1')

    assert [event.execution_id for event in received] == [
        'execution-2',
        'execution-1',
    ]
    assert [call.execution_id for call in adapter.calls] == [
        'execution-1',
        'execution-2',
    ]


def test_injected_event_is_forwarded_once_without_copying():
    adapter, received = _adapter()
    event = SafetyEvent(
        event_type=SafetyEventType.LEASE_RELEASED,
        execution_id='execution-1',
    )

    adapter.inject_event(event)

    assert received == [event]
    assert received[0] is event


def test_safety_modules_have_no_generation_dependency_or_forbidden_runtime_imports():
    modules = [
        __import__(
            'cleannav_mission_manager.adapters.safety',
            fromlist=['safety'],
        ),
        __import__(
            'cleannav_mission_manager.adapters.mock_safety',
            fromlist=['mock_safety'],
        ),
    ]
    source = '\n'.join(inspect.getsource(module) for module in modules)

    assert 'GenerationHandle' not in source
    assert 'generation' not in source
    for forbidden in (
        'rclpy',
        'ROS Node',
        'HTTP',
        'BLE',
        'Ackermann',
        'MPPI',
        'Hybrid-A*',
        'CAN',
        '/cmd_vel',
    ):
        assert forbidden not in source


@pytest.mark.parametrize('invalid_id', [None, 1, ''])
def test_lease_methods_reject_invalid_execution_id(invalid_id):
    adapter, _ = _adapter()

    with pytest.raises((TypeError, ValueError)):
        adapter.acquire_lease(invalid_id)
    with pytest.raises((TypeError, ValueError)):
        adapter.release_lease(invalid_id)
