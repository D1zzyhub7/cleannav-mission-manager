"""Unit tests for the pure Mission Manager state machine."""

from dataclasses import FrozenInstanceError
import inspect

import pytest

from cleannav_mission_manager.domain.models import (
    CancelIntent,
    InternalExecutionState,
    ManagerMode,
)
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
    return transition(
        _lease_acquiring_context(),
        StateMachineEvent.LEASE_ACQUIRED,
    ).next_context


def _lease_acquiring_context():
    context = _context(InternalExecutionState.PREPARING_GOAL)
    context = transition(
        context,
        StateMachineEvent.GOAL_PREPARED,
    ).next_context
    context = transition(
        context,
        StateMachineEvent.NAV_GOAL_ACCEPTED,
    ).next_context
    return context


def _canceling_context(
    outcome=PendingOutcome.CANCELED,
    intent=CancelIntent.STOP,
):
    return StateMachineContext(
        state=InternalExecutionState.CANCELING,
        cancel_intent=intent,
        pending_outcome=outcome,
        cleanup=CleanupContext(
            navigation_submitted=True,
            navigation_cancel_required=True,
            lease_was_active=True,
            lease_release_required=True,
        ),
    )


def test_finalizing_state_is_part_of_internal_execution_state():
    assert InternalExecutionState.FINALIZING.value == 'FINALIZING'


def test_state_machine_context_defaults_to_normal_mode():
    context = _context(InternalExecutionState.IDLE)

    assert context.manager_mode is ManagerMode.NORMAL


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
    assert decision.next_context.cleanup.lease_acquire_pending is True
    assert decision.effects == (TransitionEffect.ACQUIRE_LEASE,)


def test_lease_acquired_enters_executing():
    context = StateMachineContext(
        state=InternalExecutionState.LEASE_ACQUIRING,
        cleanup=CleanupContext(
            navigation_submitted=True,
            lease_acquire_pending=True,
        ),
    )

    decision = transition(context, StateMachineEvent.LEASE_ACQUIRED)

    assert decision.accepted is True
    assert decision.next_context.state is InternalExecutionState.EXECUTING
    assert decision.next_context.cleanup.lease_acquire_pending is False
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


def test_lease_acquire_failed_cancels_navigation_for_safety_block():
    context = StateMachineContext(
        state=InternalExecutionState.LEASE_ACQUIRING,
        cleanup=CleanupContext(
            navigation_submitted=True,
            lease_acquire_pending=True,
        ),
    )

    decision = transition(
        context,
        StateMachineEvent.LEASE_ACQUIRE_FAILED,
    )

    assert decision.accepted is True
    assert decision.next_context.state is InternalExecutionState.CANCELING
    assert decision.next_context.cancel_intent is CancelIntent.SAFETY_BLOCK
    assert (
        decision.next_context.pending_outcome
        is PendingOutcome.SAFETY_BLOCKED
    )
    assert decision.next_context.cleanup.navigation_cancel_required is True
    assert decision.next_context.cleanup.lease_acquire_pending is False
    assert decision.next_context.cleanup.lease_was_active is False
    assert decision.next_context.cleanup.lease_release_required is False
    assert decision.effects == (TransitionEffect.CANCEL_NAVIGATION,)


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


@pytest.mark.parametrize(
    'state',
    [
        InternalExecutionState.WAITING_TARGET,
        InternalExecutionState.PREPARING_GOAL,
    ],
)
def test_pause_without_resources_enters_clean_paused(state):
    context = StateMachineContext(
        state=state,
        pending_outcome=PendingOutcome.FAILED,
        cleanup=CleanupContext(cleanup_failed=True),
    )

    decision = transition(context, StateMachineEvent.PAUSE_REQUESTED)

    assert decision.accepted is True
    assert decision.next_context == _context(InternalExecutionState.PAUSED)
    assert decision.effects == ()


def test_pause_navigation_starting_requests_only_navigation_cancel():
    context = StateMachineContext(
        state=InternalExecutionState.NAVIGATION_STARTING,
        cleanup=CleanupContext(navigation_submitted=True),
    )

    decision = transition(context, StateMachineEvent.PAUSE_REQUESTED)

    assert decision.next_context.state is InternalExecutionState.CANCELING
    assert decision.next_context.cancel_intent is CancelIntent.PAUSE
    assert decision.next_context.pending_outcome is PendingOutcome.PAUSED
    assert decision.next_context.cleanup.navigation_cancel_required is True
    assert decision.next_context.cleanup.lease_release_required is False
    assert decision.effects == (TransitionEffect.CANCEL_NAVIGATION,)


def test_pause_executing_requests_cancel_and_release():
    decision = transition(
        _executing_context(),
        StateMachineEvent.PAUSE_REQUESTED,
    )

    assert decision.next_context.state is InternalExecutionState.CANCELING
    assert decision.next_context.cancel_intent is CancelIntent.PAUSE
    assert decision.next_context.pending_outcome is PendingOutcome.PAUSED
    assert decision.effects == (
        TransitionEffect.CANCEL_NAVIGATION,
        TransitionEffect.RELEASE_LEASE,
    )


def test_pause_cleanup_confirmation_enters_clean_paused():
    context = transition(
        StateMachineContext(
            state=InternalExecutionState.NAVIGATION_STARTING,
            cleanup=CleanupContext(navigation_submitted=True),
        ),
        StateMachineEvent.PAUSE_REQUESTED,
    ).next_context

    decision = transition(context, StateMachineEvent.NAV_CANCEL_CONFIRMED)

    assert decision.next_context == _context(InternalExecutionState.PAUSED)
    assert decision.effects == ()


def test_duplicate_pause_in_paused_is_accepted_no_op():
    context = _context(InternalExecutionState.PAUSED)

    decision = transition(context, StateMachineEvent.PAUSE_REQUESTED)

    assert decision.accepted is True
    assert decision.next_context is context
    assert decision.effects == ()


def test_pause_in_dirty_safety_blocked_is_accepted_no_op():
    context = StateMachineContext(
        state=InternalExecutionState.SAFETY_BLOCKED,
        cancel_intent=CancelIntent.SAFETY_BLOCK,
        pending_outcome=PendingOutcome.SAFETY_BLOCKED,
        cleanup=CleanupContext(cleanup_failed=True),
    )

    decision = transition(context, StateMachineEvent.PAUSE_REQUESTED)

    assert decision.accepted is True
    assert decision.next_context is context
    assert decision.effects == ()


def test_stop_from_paused_terminalizes_canceled_execution():
    decision = transition(
        _context(InternalExecutionState.PAUSED),
        StateMachineEvent.STOP_REQUESTED,
    )

    assert decision.next_context == _context(InternalExecutionState.IDLE)
    assert decision.effects == (TransitionEffect.TERMINAL_CANCELED,)


def test_stop_active_execution_enters_canceling():
    decision = transition(
        _executing_context(),
        StateMachineEvent.STOP_REQUESTED,
    )

    assert decision.next_context.state is InternalExecutionState.CANCELING
    assert decision.next_context.cancel_intent is CancelIntent.STOP
    assert decision.next_context.pending_outcome is PendingOutcome.CANCELED
    assert decision.effects == (
        TransitionEffect.CANCEL_NAVIGATION,
        TransitionEffect.RELEASE_LEASE,
    )


def test_stop_cleanup_requires_both_confirmations():
    context = _canceling_context()

    cancel_only = transition(
        context,
        StateMachineEvent.NAV_CANCEL_CONFIRMED,
    )
    release_only = transition(
        context,
        StateMachineEvent.LEASE_RELEASED,
    )

    assert cancel_only.next_context.state is InternalExecutionState.CANCELING
    assert release_only.next_context.state is InternalExecutionState.CANCELING
    assert cancel_only.effects == ()
    assert release_only.effects == ()


def test_stop_cleanup_enters_clean_idle_and_terminalizes():
    context = transition(
        _canceling_context(),
        StateMachineEvent.NAV_CANCEL_CONFIRMED,
    ).next_context

    decision = transition(context, StateMachineEvent.LEASE_RELEASED)

    assert decision.next_context == _context(InternalExecutionState.IDLE)
    assert decision.effects == (TransitionEffect.TERMINAL_CANCELED,)


def test_stop_while_finalizing_does_not_repeat_release():
    cleanup = CleanupContext(
        navigation_submitted=True,
        lease_was_active=True,
        lease_release_required=True,
    )
    context = StateMachineContext(
        state=InternalExecutionState.FINALIZING,
        pending_outcome=PendingOutcome.SUCCEEDED,
        cleanup=cleanup,
    )

    decision = transition(context, StateMachineEvent.STOP_REQUESTED)

    assert decision.next_context.state is InternalExecutionState.CANCELING
    assert decision.next_context.cancel_intent is CancelIntent.STOP
    assert decision.next_context.pending_outcome is PendingOutcome.CANCELED
    assert decision.next_context.cleanup is cleanup
    assert decision.effects == ()


@pytest.mark.parametrize(
    'state',
    [
        InternalExecutionState.PAUSED,
        InternalExecutionState.SAFETY_BLOCKED,
    ],
)
def test_resume_advances_generation_before_goal_preparation(state):
    context = _context(state)

    decision = transition(context, StateMachineEvent.RESUME_ALLOWED)

    assert decision.next_context == _context(
        InternalExecutionState.PREPARING_GOAL
    )
    assert decision.effects == (TransitionEffect.ADVANCE_GENERATION,)
    assert TransitionEffect.SUBMIT_NAVIGATION not in decision.effects


@pytest.mark.parametrize(
    'event',
    [
        StateMachineEvent.RESUME_ALLOWED,
        StateMachineEvent.STOP_REQUESTED,
        StateMachineEvent.RETURN_HOME_ALLOWED,
    ],
)
def test_dirty_safety_blocked_rejects_control_exit(event):
    context = StateMachineContext(
        state=InternalExecutionState.SAFETY_BLOCKED,
        cancel_intent=CancelIntent.SAFETY_BLOCK,
        pending_outcome=PendingOutcome.SAFETY_BLOCKED,
        cleanup=CleanupContext(
            navigation_submitted=True,
            navigation_cancel_required=True,
        ),
    )

    decision = transition(context, event)

    assert decision.accepted is False
    assert decision.next_context is context
    assert decision.effects == ()


def test_return_home_without_resources_terminalizes_old_execution():
    decision = transition(
        _context(InternalExecutionState.PAUSED),
        StateMachineEvent.RETURN_HOME_ALLOWED,
    )

    assert decision.next_context == _context(InternalExecutionState.IDLE)
    assert decision.effects == (
        TransitionEffect.TERMINAL_CANCELED,
        TransitionEffect.START_RETURN_HOME,
    )


def test_return_home_with_active_resources_enters_canceling():
    decision = transition(
        _executing_context(),
        StateMachineEvent.RETURN_HOME_ALLOWED,
    )

    assert decision.next_context.state is InternalExecutionState.CANCELING
    assert decision.next_context.cancel_intent is CancelIntent.RETURN_HOME
    assert (
        decision.next_context.pending_outcome
        is PendingOutcome.START_RETURN_HOME
    )
    assert decision.effects == (
        TransitionEffect.CANCEL_NAVIGATION,
        TransitionEffect.RELEASE_LEASE,
    )


def test_return_home_cleanup_returns_clean_old_context_and_both_effects():
    context = _canceling_context(
        PendingOutcome.START_RETURN_HOME,
        CancelIntent.RETURN_HOME,
    )
    context = transition(
        context,
        StateMachineEvent.NAV_CANCEL_CONFIRMED,
    ).next_context

    decision = transition(context, StateMachineEvent.LEASE_RELEASED)

    assert decision.next_context == _context(InternalExecutionState.IDLE)
    assert decision.effects == (
        TransitionEffect.TERMINAL_CANCELED,
        TransitionEffect.START_RETURN_HOME,
    )


def test_safety_block_active_execution_enters_canceling():
    decision = transition(
        _executing_context(),
        StateMachineEvent.SAFETY_BLOCK_REQUESTED,
    )

    assert decision.next_context.state is InternalExecutionState.CANCELING
    assert decision.next_context.cancel_intent is CancelIntent.SAFETY_BLOCK
    assert (
        decision.next_context.pending_outcome
        is PendingOutcome.SAFETY_BLOCKED
    )
    assert decision.effects == (
        TransitionEffect.CANCEL_NAVIGATION,
        TransitionEffect.RELEASE_LEASE,
    )


def test_safety_block_cleanup_enters_clean_blocked_without_terminal():
    context = _canceling_context(
        PendingOutcome.SAFETY_BLOCKED,
        CancelIntent.SAFETY_BLOCK,
    )
    context = transition(
        context,
        StateMachineEvent.NAV_CANCEL_CONFIRMED,
    ).next_context

    decision = transition(context, StateMachineEvent.LEASE_RELEASED)

    assert decision.next_context == _context(
        InternalExecutionState.SAFETY_BLOCKED
    )
    assert decision.effects == ()


@pytest.mark.parametrize(
    ('event', 'required_flag'),
    [
        (StateMachineEvent.NAV_CANCEL_FAILED, 'navigation_cancel_required'),
        (StateMachineEvent.LEASE_RELEASE_FAILED, 'lease_release_required'),
    ],
)
def test_canceling_cleanup_failure_enters_safety_blocked(
    event,
    required_flag,
):
    context = _canceling_context()

    decision = transition(context, event)

    assert decision.accepted is True
    assert (
        decision.next_context.state
        is InternalExecutionState.SAFETY_BLOCKED
    )
    assert decision.next_context.pending_outcome is PendingOutcome.CANCELED
    assert getattr(decision.next_context.cleanup, required_flag) is True
    assert decision.next_context.cleanup.cleanup_failed is True
    assert decision.effects == ()


def test_late_lease_acquired_while_canceling_requests_release_only():
    context = StateMachineContext(
        state=InternalExecutionState.CANCELING,
        cancel_intent=CancelIntent.PAUSE,
        pending_outcome=PendingOutcome.PAUSED,
        cleanup=CleanupContext(
            navigation_submitted=True,
            navigation_cancel_required=True,
            lease_acquire_pending=True,
        ),
    )

    decision = transition(context, StateMachineEvent.LEASE_ACQUIRED)

    assert decision.next_context.state is InternalExecutionState.CANCELING
    assert decision.next_context.cleanup.lease_acquire_pending is False
    assert decision.next_context.cleanup.lease_was_active is True
    assert decision.next_context.cleanup.lease_release_required is True
    assert decision.effects == (TransitionEffect.RELEASE_LEASE,)


def test_canceling_waits_after_cancel_confirmed_while_acquire_pending():
    context = transition(
        _lease_acquiring_context(),
        StateMachineEvent.PAUSE_REQUESTED,
    ).next_context

    decision = transition(
        context,
        StateMachineEvent.NAV_CANCEL_CONFIRMED,
    )

    assert decision.next_context.state is InternalExecutionState.CANCELING
    assert decision.next_context.cleanup.navigation_cancel_confirmed is True
    assert decision.next_context.cleanup.lease_acquire_pending is True
    assert decision.effects == ()


def test_late_lease_acquired_requires_release_before_pause_completes():
    context = transition(
        _lease_acquiring_context(),
        StateMachineEvent.PAUSE_REQUESTED,
    ).next_context
    context = transition(
        context,
        StateMachineEvent.NAV_CANCEL_CONFIRMED,
    ).next_context

    acquired = transition(context, StateMachineEvent.LEASE_ACQUIRED)

    assert acquired.next_context.state is InternalExecutionState.CANCELING
    assert acquired.next_context.cleanup.lease_acquire_pending is False
    assert acquired.next_context.cleanup.lease_was_active is True
    assert acquired.next_context.cleanup.lease_release_required is True
    assert acquired.effects == (TransitionEffect.RELEASE_LEASE,)

    released = transition(
        acquired.next_context,
        StateMachineEvent.LEASE_RELEASED,
    )
    assert released.next_context == _context(InternalExecutionState.PAUSED)
    assert released.effects == ()


def test_late_lease_acquire_failure_completes_stop_after_cancel():
    context = transition(
        _lease_acquiring_context(),
        StateMachineEvent.STOP_REQUESTED,
    ).next_context
    context = transition(
        context,
        StateMachineEvent.NAV_CANCEL_CONFIRMED,
    ).next_context

    decision = transition(
        context,
        StateMachineEvent.LEASE_ACQUIRE_FAILED,
    )

    assert decision.next_context == _context(InternalExecutionState.IDLE)
    assert decision.effects == (TransitionEffect.TERMINAL_CANCELED,)


@pytest.mark.parametrize(
    ('control_event', 'expected_state', 'expected_effects'),
    [
        (
            StateMachineEvent.PAUSE_REQUESTED,
            InternalExecutionState.PAUSED,
            (),
        ),
        (
            StateMachineEvent.STOP_REQUESTED,
            InternalExecutionState.IDLE,
            (TransitionEffect.TERMINAL_CANCELED,),
        ),
    ],
)
def test_goal_rejected_while_canceling_completes_pending_outcome(
    control_event,
    expected_state,
    expected_effects,
):
    context = StateMachineContext(
        state=InternalExecutionState.NAVIGATION_STARTING,
        cleanup=CleanupContext(navigation_submitted=True),
    )
    context = transition(context, control_event).next_context

    decision = transition(context, StateMachineEvent.NAV_GOAL_REJECTED)

    assert decision.next_context == _context(expected_state)
    assert decision.effects == expected_effects


def test_estop_from_idle_latches_without_terminal_execution_effect():
    decision = transition(
        _context(InternalExecutionState.IDLE),
        StateMachineEvent.ESTOP_REQUESTED,
    )

    assert decision.next_context.state is InternalExecutionState.IDLE
    assert (
        decision.next_context.manager_mode
        is ManagerMode.EMERGENCY_LATCHED
    )
    assert decision.next_context.cleanup == CleanupContext()
    assert decision.effects == (TransitionEffect.REQUEST_SAFETY_ESTOP,)


def test_estop_from_paused_terminalizes_active_execution():
    decision = transition(
        _context(InternalExecutionState.PAUSED),
        StateMachineEvent.ESTOP_REQUESTED,
    )

    assert decision.next_context == StateMachineContext(
        state=InternalExecutionState.IDLE,
        manager_mode=ManagerMode.EMERGENCY_LATCHED,
    )
    assert decision.effects == (
        TransitionEffect.REQUEST_SAFETY_ESTOP,
        TransitionEffect.TERMINAL_EMERGENCY_STOPPED,
    )


def test_estop_from_executing_latches_and_requests_all_cleanup():
    decision = transition(
        _executing_context(),
        StateMachineEvent.ESTOP_REQUESTED,
    )

    assert decision.next_context.state is InternalExecutionState.CANCELING
    assert (
        decision.next_context.manager_mode
        is ManagerMode.EMERGENCY_LATCHED
    )
    assert decision.next_context.cancel_intent is CancelIntent.ESTOP
    assert (
        decision.next_context.pending_outcome
        is PendingOutcome.EMERGENCY_STOPPED
    )
    assert decision.effects == (
        TransitionEffect.REQUEST_SAFETY_ESTOP,
        TransitionEffect.CANCEL_NAVIGATION,
        TransitionEffect.RELEASE_LEASE,
    )


def test_estop_from_finalizing_does_not_repeat_cleanup_requests():
    context = transition(
        _executing_context(),
        StateMachineEvent.NAV_SUCCEEDED,
    ).next_context
    cleanup = context.cleanup

    decision = transition(context, StateMachineEvent.ESTOP_REQUESTED)

    assert decision.next_context.state is InternalExecutionState.CANCELING
    assert decision.next_context.cancel_intent is CancelIntent.ESTOP
    assert decision.next_context.cleanup == cleanup
    assert decision.effects == (TransitionEffect.REQUEST_SAFETY_ESTOP,)


def test_estop_overrides_canceling_intent_and_preserves_progress():
    context = transition(
        _executing_context(),
        StateMachineEvent.STOP_REQUESTED,
    ).next_context
    context = transition(
        context,
        StateMachineEvent.NAV_CANCEL_CONFIRMED,
    ).next_context
    cleanup = context.cleanup

    decision = transition(context, StateMachineEvent.ESTOP_REQUESTED)

    assert decision.next_context.cancel_intent is CancelIntent.ESTOP
    assert (
        decision.next_context.pending_outcome
        is PendingOutcome.EMERGENCY_STOPPED
    )
    assert decision.next_context.cleanup == cleanup
    assert decision.next_context.cleanup.navigation_cancel_confirmed is True
    assert decision.effects == (TransitionEffect.REQUEST_SAFETY_ESTOP,)


def test_repeated_estop_is_accepted_identity_no_op():
    context = StateMachineContext(
        state=InternalExecutionState.CANCELING,
        manager_mode=ManagerMode.EMERGENCY_LATCHED,
        cancel_intent=CancelIntent.ESTOP,
        pending_outcome=PendingOutcome.EMERGENCY_STOPPED,
        cleanup=CleanupContext(
            navigation_submitted=True,
            navigation_cancel_required=True,
        ),
    )

    decision = transition(context, StateMachineEvent.ESTOP_REQUESTED)

    assert decision.accepted is True
    assert decision.next_context is context
    assert decision.effects == ()


@pytest.mark.parametrize(
    ('state', 'event'),
    [
        (
            InternalExecutionState.PAUSED,
            StateMachineEvent.PAUSE_REQUESTED,
        ),
        (
            InternalExecutionState.PAUSED,
            StateMachineEvent.STOP_REQUESTED,
        ),
        (
            InternalExecutionState.PAUSED,
            StateMachineEvent.RESUME_ALLOWED,
        ),
        (
            InternalExecutionState.PAUSED,
            StateMachineEvent.RETURN_HOME_ALLOWED,
        ),
        (
            InternalExecutionState.PREPARING_GOAL,
            StateMachineEvent.GOAL_PREPARED,
        ),
    ],
)
def test_emergency_latched_mode_rejects_normal_events(state, event):
    context = StateMachineContext(
        state=state,
        manager_mode=ManagerMode.EMERGENCY_LATCHED,
    )

    decision = transition(context, event)

    assert decision.accepted is False
    assert decision.next_context is context
    assert decision.effects == ()


def test_latched_canceling_allows_navigation_cancel_confirmation():
    context = transition(
        _executing_context(),
        StateMachineEvent.ESTOP_REQUESTED,
    ).next_context

    decision = transition(
        context,
        StateMachineEvent.NAV_CANCEL_CONFIRMED,
    )

    assert decision.accepted is True
    assert decision.next_context.state is InternalExecutionState.CANCELING
    assert (
        decision.next_context.manager_mode
        is ManagerMode.EMERGENCY_LATCHED
    )
    assert decision.next_context.cleanup.navigation_cancel_confirmed is True
    assert decision.effects == ()


def test_estop_late_lease_acquired_requires_release_before_completion():
    context = transition(
        _lease_acquiring_context(),
        StateMachineEvent.ESTOP_REQUESTED,
    ).next_context
    assert context.cleanup.lease_acquire_pending is True

    context = transition(
        context,
        StateMachineEvent.NAV_CANCEL_CONFIRMED,
    ).next_context
    assert context.state is InternalExecutionState.CANCELING

    acquired = transition(context, StateMachineEvent.LEASE_ACQUIRED)
    assert acquired.next_context.state is InternalExecutionState.CANCELING
    assert (
        acquired.next_context.manager_mode
        is ManagerMode.EMERGENCY_LATCHED
    )
    assert acquired.effects == (TransitionEffect.RELEASE_LEASE,)

    released = transition(
        acquired.next_context,
        StateMachineEvent.LEASE_RELEASED,
    )
    assert released.next_context == StateMachineContext(
        state=InternalExecutionState.IDLE,
        manager_mode=ManagerMode.EMERGENCY_LATCHED,
    )
    assert released.effects == (
        TransitionEffect.TERMINAL_EMERGENCY_STOPPED,
    )


def test_estop_late_lease_acquire_failure_completes_emergency_cleanup():
    context = transition(
        _lease_acquiring_context(),
        StateMachineEvent.ESTOP_REQUESTED,
    ).next_context
    context = transition(
        context,
        StateMachineEvent.NAV_CANCEL_CONFIRMED,
    ).next_context

    decision = transition(
        context,
        StateMachineEvent.LEASE_ACQUIRE_FAILED,
    )

    assert decision.next_context == StateMachineContext(
        state=InternalExecutionState.IDLE,
        manager_mode=ManagerMode.EMERGENCY_LATCHED,
    )
    assert decision.effects == (
        TransitionEffect.TERMINAL_EMERGENCY_STOPPED,
    )


def test_reset_estop_from_clean_latched_idle_returns_normal_only():
    context = StateMachineContext(
        state=InternalExecutionState.IDLE,
        manager_mode=ManagerMode.EMERGENCY_LATCHED,
    )

    decision = transition(context, StateMachineEvent.RESET_ESTOP_ALLOWED)

    assert decision.next_context == _context(InternalExecutionState.IDLE)
    assert decision.effects == ()
    assert TransitionEffect.ADVANCE_GENERATION not in decision.effects
    assert TransitionEffect.SUBMIT_NAVIGATION not in decision.effects
    assert TransitionEffect.START_RETURN_HOME not in decision.effects


def test_reset_estop_while_canceling_is_rejected():
    context = transition(
        _executing_context(),
        StateMachineEvent.ESTOP_REQUESTED,
    ).next_context

    decision = transition(context, StateMachineEvent.RESET_ESTOP_ALLOWED)

    assert decision.accepted is False
    assert decision.next_context is context
    assert decision.effects == ()


def test_reset_estop_with_unresolved_cleanup_is_rejected():
    context = StateMachineContext(
        state=InternalExecutionState.IDLE,
        manager_mode=ManagerMode.EMERGENCY_LATCHED,
        cancel_intent=CancelIntent.ESTOP,
        pending_outcome=PendingOutcome.EMERGENCY_STOPPED,
        cleanup=CleanupContext(
            navigation_submitted=True,
            navigation_cancel_required=True,
        ),
    )

    decision = transition(context, StateMachineEvent.RESET_ESTOP_ALLOWED)

    assert decision.accepted is False
    assert decision.next_context is context
    assert decision.effects == ()


def test_estop_from_dirty_safety_blocked_preserves_unresolved_cleanup():
    cleanup = CleanupContext(
        navigation_submitted=True,
        navigation_cancel_required=True,
        cleanup_failed=True,
    )
    context = StateMachineContext(
        state=InternalExecutionState.SAFETY_BLOCKED,
        cancel_intent=CancelIntent.SAFETY_BLOCK,
        pending_outcome=PendingOutcome.SAFETY_BLOCKED,
        cleanup=cleanup,
    )

    decision = transition(context, StateMachineEvent.ESTOP_REQUESTED)

    assert decision.next_context.state is InternalExecutionState.CANCELING
    assert (
        decision.next_context.manager_mode
        is ManagerMode.EMERGENCY_LATCHED
    )
    assert decision.next_context.cancel_intent is CancelIntent.ESTOP
    assert (
        decision.next_context.pending_outcome
        is PendingOutcome.EMERGENCY_STOPPED
    )
    assert decision.next_context.cleanup == cleanup
    assert decision.next_context.cleanup.cleanup_failed is True
    assert decision.effects == (TransitionEffect.REQUEST_SAFETY_ESTOP,)


def test_start_replan_cleanup_remains_deferred():
    context = _canceling_context(
        PendingOutcome.START_REPLAN,
        CancelIntent.REPLAN,
    )

    decision = transition(
        context,
        StateMachineEvent.NAV_CANCEL_CONFIRMED,
    )

    assert decision.accepted is False
    assert decision.next_context is context
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
