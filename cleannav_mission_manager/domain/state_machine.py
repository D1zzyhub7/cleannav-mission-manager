"""Pure state transitions for Mission Manager execution lifecycles."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Optional

from cleannav_mission_manager.domain.models import (
    CancelIntent,
    InternalExecutionState,
)


class PendingOutcome(Enum):
    """Outcome retained while asynchronous cleanup is in progress."""

    NONE = 'NONE'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'
    CANCELED = 'CANCELED'
    PAUSED = 'PAUSED'
    SAFETY_BLOCKED = 'SAFETY_BLOCKED'
    EMERGENCY_STOPPED = 'EMERGENCY_STOPPED'
    START_RETURN_HOME = 'START_RETURN_HOME'
    START_REPLAN = 'START_REPLAN'


@dataclass(frozen=True)
class CleanupContext:
    """Immutable cleanup progress for one execution transition flow."""

    navigation_submitted: bool = False
    navigation_cancel_required: bool = False
    navigation_cancel_confirmed: bool = False
    lease_was_active: bool = False
    lease_release_required: bool = False
    lease_release_confirmed: bool = False
    cleanup_failed: bool = False


@dataclass(frozen=True)
class StateMachineContext:
    """Immutable input and output context for the pure state machine."""

    state: InternalExecutionState
    cancel_intent: Optional[CancelIntent] = None
    pending_outcome: PendingOutcome = PendingOutcome.NONE
    cleanup: CleanupContext = CleanupContext()


class StateMachineEvent(Enum):
    """Normal-lifecycle events supported by the M1-2iA gate."""

    GOAL_PREPARED = 'GOAL_PREPARED'
    NAV_GOAL_ACCEPTED = 'NAV_GOAL_ACCEPTED'
    NAV_GOAL_REJECTED = 'NAV_GOAL_REJECTED'
    LEASE_ACQUIRED = 'LEASE_ACQUIRED'
    LEASE_ACQUIRE_FAILED = 'LEASE_ACQUIRE_FAILED'
    NAV_SUCCEEDED = 'NAV_SUCCEEDED'
    NAV_FAILED = 'NAV_FAILED'
    LEASE_RELEASED = 'LEASE_RELEASED'
    LEASE_RELEASE_FAILED = 'LEASE_RELEASE_FAILED'


class TransitionEffect(Enum):
    """Side effects requested from, but never executed by, the machine."""

    SUBMIT_NAVIGATION = 'SUBMIT_NAVIGATION'
    ACQUIRE_LEASE = 'ACQUIRE_LEASE'
    RELEASE_LEASE = 'RELEASE_LEASE'
    TERMINAL_SUCCEEDED = 'TERMINAL_SUCCEEDED'
    TERMINAL_FAILED = 'TERMINAL_FAILED'


@dataclass(frozen=True)
class TransitionDecision:
    """Immutable decision produced for one state and event pair."""

    accepted: bool
    previous_state: InternalExecutionState
    next_context: StateMachineContext
    effects: tuple[TransitionEffect, ...]


def _decision(
    previous: StateMachineContext,
    next_context: StateMachineContext,
    *effects: TransitionEffect,
) -> TransitionDecision:
    """Build an accepted transition decision."""
    return TransitionDecision(
        accepted=True,
        previous_state=previous.state,
        next_context=next_context,
        effects=effects,
    )


def _rejected(context: StateMachineContext) -> TransitionDecision:
    """Build a side-effect-free rejected or deferred decision."""
    return TransitionDecision(
        accepted=False,
        previous_state=context.state,
        next_context=context,
        effects=(),
    )


def transition(
    context: StateMachineContext,
    event: StateMachineEvent,
) -> TransitionDecision:
    """Apply one M1-2iA event without mutating state or calling adapters."""
    if not isinstance(context, StateMachineContext):
        raise TypeError('context must be StateMachineContext')
    if not isinstance(event, StateMachineEvent):
        raise TypeError('event must be StateMachineEvent')

    state = context.state

    if (
        state is InternalExecutionState.PREPARING_GOAL
        and event is StateMachineEvent.GOAL_PREPARED
    ):
        cleanup = replace(
            context.cleanup,
            navigation_submitted=True,
        )
        return _decision(
            context,
            replace(
                context,
                state=InternalExecutionState.NAVIGATION_STARTING,
                cleanup=cleanup,
            ),
            TransitionEffect.SUBMIT_NAVIGATION,
        )

    if state is InternalExecutionState.NAVIGATION_STARTING:
        if event is StateMachineEvent.NAV_GOAL_ACCEPTED:
            return _decision(
                context,
                replace(
                    context,
                    state=InternalExecutionState.LEASE_ACQUIRING,
                ),
                TransitionEffect.ACQUIRE_LEASE,
            )
        if event is StateMachineEvent.NAV_GOAL_REJECTED:
            return _decision(
                context,
                replace(
                    context,
                    state=InternalExecutionState.IDLE,
                    cancel_intent=None,
                    pending_outcome=PendingOutcome.NONE,
                    cleanup=CleanupContext(),
                ),
                TransitionEffect.TERMINAL_FAILED,
            )

    if (
        state is InternalExecutionState.LEASE_ACQUIRING
        and event is StateMachineEvent.LEASE_ACQUIRED
    ):
        cleanup = replace(
            context.cleanup,
            lease_was_active=True,
        )
        return _decision(
            context,
            replace(
                context,
                state=InternalExecutionState.EXECUTING,
                cleanup=cleanup,
            ),
        )

    if state is InternalExecutionState.EXECUTING:
        if event is StateMachineEvent.NAV_SUCCEEDED:
            pending_outcome = PendingOutcome.SUCCEEDED
        elif event is StateMachineEvent.NAV_FAILED:
            pending_outcome = PendingOutcome.FAILED
        else:
            return _rejected(context)

        cleanup = replace(
            context.cleanup,
            lease_release_required=True,
        )
        return _decision(
            context,
            replace(
                context,
                state=InternalExecutionState.FINALIZING,
                pending_outcome=pending_outcome,
                cleanup=cleanup,
            ),
            TransitionEffect.RELEASE_LEASE,
        )

    if state is InternalExecutionState.FINALIZING:
        if event is StateMachineEvent.LEASE_RELEASE_FAILED:
            cleanup = replace(
                context.cleanup,
                cleanup_failed=True,
            )
            return _decision(
                context,
                replace(
                    context,
                    state=InternalExecutionState.SAFETY_BLOCKED,
                    cleanup=cleanup,
                ),
            )

        if (
            event is StateMachineEvent.LEASE_RELEASED
            and context.pending_outcome
            in (PendingOutcome.SUCCEEDED, PendingOutcome.FAILED)
        ):
            effect = (
                TransitionEffect.TERMINAL_SUCCEEDED
                if context.pending_outcome is PendingOutcome.SUCCEEDED
                else TransitionEffect.TERMINAL_FAILED
            )
            return _decision(
                context,
                replace(
                    context,
                    state=InternalExecutionState.IDLE,
                    cancel_intent=None,
                    pending_outcome=PendingOutcome.NONE,
                    cleanup=CleanupContext(),
                ),
                effect,
            )

    return _rejected(context)
