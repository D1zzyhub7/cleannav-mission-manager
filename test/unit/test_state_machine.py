"""Unit tests for the pure Mission Manager state machine."""

from dataclasses import FrozenInstanceError
import inspect

import pytest

from cleannav_mission_manager.domain.models import InternalExecutionState
from cleannav_mission_manager.domain.state_machine import (
    CleanupContext,
    PendingOutcome,
    StateMachineContext,
    StateMachineEvent,
    TransitionEffect,
    transition,
)


def _context(state):
    return StateMachineContext(state=state)


def _executing_context():
    context = _context(InternalExecutionState.PREPARING_GOAL)
    context = transition(
        context,
        StateMachineEvent.GOAL_PREPARED,
    ).next_context
    context = transition(
        context,
        StateMachineEvent.NAV_GOAL_ACCEPTED,
    ).next_context
    return transition(
        context,
        StateMachineEvent.LEASE_ACQUIRED,
    ).next_context


def test_finalizing_state_is_part_of_internal_execution_state():
    assert InternalExecutionState.FINALIZING.value == 'FINALIZING'


def test_context_and_decision_are_immutable():
    context = _context(InternalExecutionState.PREPARING_GOAL)
    decision = transition(context, StateMachineEvent.GOAL_PREPARED)

    with pytest.raises(FrozenInstanceError):
        context.state = InternalExecutionState.IDLE
    with pytest.raises(FrozenInstanceError):
        context.cleanup.cleanup_failed = True
    with pytest.raises(FrozenInstanceError):
        decision.accepted = False

    assert isinstance(decision.effects, tuple)


def test_goal_prepared_submits_navigation_without_generation_change():
    decision = transition(
        _context(InternalExecutionState.PREPARING_GOAL),
        StateMachineEvent.GOAL_PREPARED,
    )

    assert decision.accepted is True
    assert decision.previous_state is InternalExecutionState.PREPARING_GOAL
    assert (
        decision.next_context.state
        is InternalExecutionState.NAVIGATION_STARTING
    )
    assert decision.next_context.cleanup.navigation_submitted is True
    assert decision.effects == (TransitionEffect.SUBMIT_NAVIGATION,)
    assert not hasattr(decision.next_context, 'generation')


def test_navigation_goal_accepted_acquires_lease():
    context = StateMachineContext(
        state=InternalExecutionState.NAVIGATION_STARTING,
        cleanup=CleanupContext(navigation_submitted=True),
    )

    decision = transition(
        context,
        StateMachineEvent.NAV_GOAL_ACCEPTED,
    )

    assert decision.accepted is True
    assert (
        decision.next_context.state
        is InternalExecutionState.LEASE_ACQUIRING
    )
    assert decision.effects == (TransitionEffect.ACQUIRE_LEASE,)


def test_lease_acquired_enters_executing():
    context = StateMachineContext(
        state=InternalExecutionState.LEASE_ACQUIRING,
        cleanup=CleanupContext(navigation_submitted=True),
    )

    decision = transition(context, StateMachineEvent.LEASE_ACQUIRED)

    assert decision.accepted is True
    assert decision.next_context.state is InternalExecutionState.EXECUTING
    assert decision.next_context.cleanup.lease_was_active is True
    assert decision.effects == ()


@pytest.mark.parametrize(
    ('event', 'outcome'),
    [
        (StateMachineEvent.NAV_SUCCEEDED, PendingOutcome.SUCCEEDED),
        (StateMachineEvent.NAV_FAILED, PendingOutcome.FAILED),
    ],
)
def test_navigation_terminal_result_enters_finalizing(event, outcome):
    decision = transition(_executing_context(), event)

    assert decision.accepted is True
    assert decision.next_context.state is InternalExecutionState.FINALIZING
    assert decision.next_context.pending_outcome is outcome
    assert decision.next_context.cleanup.lease_release_required is True
    assert decision.effects == (TransitionEffect.RELEASE_LEASE,)


@pytest.mark.parametrize(
    ('outcome', 'effect'),
    [
        (
            PendingOutcome.SUCCEEDED,
            TransitionEffect.TERMINAL_SUCCEEDED,
        ),
        (PendingOutcome.FAILED, TransitionEffect.TERMINAL_FAILED),
    ],
)
def test_lease_released_requests_pending_terminal_outcome(
    outcome,
    effect,
):
    context = StateMachineContext(
        state=InternalExecutionState.FINALIZING,
        pending_outcome=outcome,
        cleanup=CleanupContext(
            navigation_submitted=True,
            lease_was_active=True,
            lease_release_required=True,
        ),
    )

    decision = transition(context, StateMachineEvent.LEASE_RELEASED)

    assert decision.accepted is True
    assert decision.next_context.state is InternalExecutionState.IDLE
    assert decision.next_context.cancel_intent is None
    assert decision.next_context.pending_outcome is PendingOutcome.NONE
    assert decision.next_context.cleanup == CleanupContext()
    assert decision.effects == (effect,)


def test_navigation_goal_rejected_fails_without_acquiring_lease():
    context = StateMachineContext(
        state=InternalExecutionState.NAVIGATION_STARTING,
        cleanup=CleanupContext(navigation_submitted=True),
    )

    decision = transition(
        context,
        StateMachineEvent.NAV_GOAL_REJECTED,
    )

    assert decision.accepted is True
    assert decision.next_context.state is InternalExecutionState.IDLE
    assert decision.next_context.cancel_intent is None
    assert decision.next_context.pending_outcome is PendingOutcome.NONE
    assert decision.next_context.cleanup == CleanupContext()
    assert decision.effects == (TransitionEffect.TERMINAL_FAILED,)


def test_lease_acquire_failed_is_deferred_without_partial_cleanup():
    context = StateMachineContext(
        state=InternalExecutionState.LEASE_ACQUIRING,
        cleanup=CleanupContext(navigation_submitted=True),
    )

    decision = transition(
        context,
        StateMachineEvent.LEASE_ACQUIRE_FAILED,
    )

    assert decision.accepted is False
    assert decision.next_context is context
    assert decision.effects == ()


def test_lease_release_failed_enters_safety_blocked():
    context = StateMachineContext(
        state=InternalExecutionState.FINALIZING,
        pending_outcome=PendingOutcome.SUCCEEDED,
        cleanup=CleanupContext(
            lease_was_active=True,
            lease_release_required=True,
        ),
    )

    decision = transition(
        context,
        StateMachineEvent.LEASE_RELEASE_FAILED,
    )

    assert decision.accepted is True
    assert (
        decision.next_context.state
        is InternalExecutionState.SAFETY_BLOCKED
    )
    assert decision.next_context.pending_outcome is PendingOutcome.SUCCEEDED
    assert decision.next_context.cleanup.cleanup_failed is True
    assert decision.effects == ()


def test_invalid_transition_has_no_side_effects():
    context = _context(InternalExecutionState.IDLE)

    decision = transition(context, StateMachineEvent.NAV_SUCCEEDED)

    assert decision.accepted is False
    assert decision.previous_state is InternalExecutionState.IDLE
    assert decision.next_context is context
    assert decision.effects == ()


def test_state_machine_has_no_store_generation_gate_or_ros_dependency():
    source = inspect.getsource(
        __import__(
            'cleannav_mission_manager.domain.state_machine',
            fromlist=['state_machine'],
        )
    )

    assert 'execution_store' not in source
    assert 'generation_gate' not in source
    assert 'rclpy' not in source
    assert 'rospy' not in source
