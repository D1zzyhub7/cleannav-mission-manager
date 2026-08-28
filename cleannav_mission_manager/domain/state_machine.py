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
    lease_acquire_pending: bool = False
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
    """Execution lifecycle events supported by the pure state machine."""

    GOAL_PREPARED = 'GOAL_PREPARED'
    NAV_GOAL_ACCEPTED = 'NAV_GOAL_ACCEPTED'
    NAV_GOAL_REJECTED = 'NAV_GOAL_REJECTED'
    LEASE_ACQUIRED = 'LEASE_ACQUIRED'
    LEASE_ACQUIRE_FAILED = 'LEASE_ACQUIRE_FAILED'
    NAV_SUCCEEDED = 'NAV_SUCCEEDED'
    NAV_FAILED = 'NAV_FAILED'
    LEASE_RELEASED = 'LEASE_RELEASED'
    LEASE_RELEASE_FAILED = 'LEASE_RELEASE_FAILED'
    PAUSE_REQUESTED = 'PAUSE_REQUESTED'
    STOP_REQUESTED = 'STOP_REQUESTED'
    RESUME_ALLOWED = 'RESUME_ALLOWED'
    RETURN_HOME_ALLOWED = 'RETURN_HOME_ALLOWED'
    SAFETY_BLOCK_REQUESTED = 'SAFETY_BLOCK_REQUESTED'
    NAV_CANCEL_CONFIRMED = 'NAV_CANCEL_CONFIRMED'
    NAV_CANCEL_FAILED = 'NAV_CANCEL_FAILED'


class TransitionEffect(Enum):
    """Side effects requested from, but never executed by, the machine."""

    SUBMIT_NAVIGATION = 'SUBMIT_NAVIGATION'
    ACQUIRE_LEASE = 'ACQUIRE_LEASE'
    RELEASE_LEASE = 'RELEASE_LEASE'
    TERMINAL_SUCCEEDED = 'TERMINAL_SUCCEEDED'
    TERMINAL_FAILED = 'TERMINAL_FAILED'
    CANCEL_NAVIGATION = 'CANCEL_NAVIGATION'
    ADVANCE_GENERATION = 'ADVANCE_GENERATION'
    TERMINAL_CANCELED = 'TERMINAL_CANCELED'
    START_RETURN_HOME = 'START_RETURN_HOME'


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


def _cleanup_complete(cleanup: CleanupContext) -> bool:
    """Return whether every required cleanup has been confirmed."""
    return (
        (
            not cleanup.navigation_cancel_required
            or cleanup.navigation_cancel_confirmed
        )
        and (
            not cleanup.lease_release_required
            or cleanup.lease_release_confirmed
        )
        and not cleanup.lease_acquire_pending
        and not cleanup.cleanup_failed
    )


def _clean_context(state: InternalExecutionState) -> StateMachineContext:
    """Build a state context without transient execution cleanup data."""
    return StateMachineContext(state=state)


def _is_clean_safety_blocked(context: StateMachineContext) -> bool:
    """Return whether a safety-blocked execution has no pending cleanup."""
    return (
        context.state is InternalExecutionState.SAFETY_BLOCKED
        and context.cancel_intent is None
        and context.pending_outcome is PendingOutcome.NONE
        and context.cleanup == CleanupContext()
    )


def _start_canceling(
    context: StateMachineContext,
    intent: CancelIntent,
    outcome: PendingOutcome,
) -> TransitionDecision:
    """Enter cancellation and request cleanup for active resources."""
    cleanup = context.cleanup
    request_cancel = (
        cleanup.navigation_submitted
        and not cleanup.navigation_cancel_required
    )
    request_release = (
        cleanup.lease_was_active
        and not cleanup.lease_release_required
    )
    next_cleanup = replace(
        cleanup,
        navigation_cancel_required=(
            cleanup.navigation_cancel_required
            or cleanup.navigation_submitted
        ),
        lease_release_required=(
            cleanup.lease_release_required
            or cleanup.lease_was_active
        ),
    )
    effects = []
    if request_cancel:
        effects.append(TransitionEffect.CANCEL_NAVIGATION)
    if request_release:
        effects.append(TransitionEffect.RELEASE_LEASE)

    return _decision(
        context,
        replace(
            context,
            state=InternalExecutionState.CANCELING,
            cancel_intent=intent,
            pending_outcome=outcome,
            cleanup=next_cleanup,
        ),
        *effects,
    )


def _finish_canceling(
    previous: StateMachineContext,
    next_context: StateMachineContext,
) -> TransitionDecision:
    """Finish a cancellation when all required cleanup is confirmed."""
    if not _cleanup_complete(next_context.cleanup):
        return _decision(previous, next_context)

    outcome = next_context.pending_outcome
    if outcome is PendingOutcome.PAUSED:
        return _decision(
            previous,
            _clean_context(InternalExecutionState.PAUSED),
        )
    if outcome is PendingOutcome.CANCELED:
        return _decision(
            previous,
            _clean_context(InternalExecutionState.IDLE),
            TransitionEffect.TERMINAL_CANCELED,
        )
    if outcome is PendingOutcome.SAFETY_BLOCKED:
        return _decision(
            previous,
            _clean_context(InternalExecutionState.SAFETY_BLOCKED),
        )
    if outcome is PendingOutcome.START_RETURN_HOME:
        return _decision(
            previous,
            _clean_context(InternalExecutionState.IDLE),
            TransitionEffect.TERMINAL_CANCELED,
            TransitionEffect.START_RETURN_HOME,
        )

    return _rejected(previous)


def transition(
    context: StateMachineContext,
    event: StateMachineEvent,
) -> TransitionDecision:
    """Apply one domain event without mutating state or calling adapters."""
    if not isinstance(context, StateMachineContext):
        raise TypeError('context must be StateMachineContext')
    if not isinstance(event, StateMachineEvent):
        raise TypeError('event must be StateMachineEvent')

    state = context.state

    if event is StateMachineEvent.PAUSE_REQUESTED:
        if state in (
            InternalExecutionState.WAITING_TARGET,
            InternalExecutionState.PREPARING_GOAL,
        ):
            return _decision(
                context,
                _clean_context(InternalExecutionState.PAUSED),
            )
        if state is InternalExecutionState.PAUSED:
            return _decision(context, context)
        if state is InternalExecutionState.SAFETY_BLOCKED:
            return _decision(context, context)
        if state in (
            InternalExecutionState.NAVIGATION_STARTING,
            InternalExecutionState.LEASE_ACQUIRING,
            InternalExecutionState.EXECUTING,
        ):
            return _start_canceling(
                context,
                CancelIntent.PAUSE,
                PendingOutcome.PAUSED,
            )
        return _rejected(context)

    if event is StateMachineEvent.STOP_REQUESTED:
        if state in (
            InternalExecutionState.WAITING_TARGET,
            InternalExecutionState.PREPARING_GOAL,
            InternalExecutionState.PAUSED,
        ):
            return _decision(
                context,
                _clean_context(InternalExecutionState.IDLE),
                TransitionEffect.TERMINAL_CANCELED,
            )
        if state is InternalExecutionState.SAFETY_BLOCKED:
            if not _is_clean_safety_blocked(context):
                return _rejected(context)
            return _decision(
                context,
                _clean_context(InternalExecutionState.IDLE),
                TransitionEffect.TERMINAL_CANCELED,
            )
        if state in (
            InternalExecutionState.NAVIGATION_STARTING,
            InternalExecutionState.LEASE_ACQUIRING,
            InternalExecutionState.EXECUTING,
        ):
            return _start_canceling(
                context,
                CancelIntent.STOP,
                PendingOutcome.CANCELED,
            )
        if state is InternalExecutionState.FINALIZING:
            return _decision(
                context,
                replace(
                    context,
                    state=InternalExecutionState.CANCELING,
                    cancel_intent=CancelIntent.STOP,
                    pending_outcome=PendingOutcome.CANCELED,
                ),
            )
        return _rejected(context)

    if event is StateMachineEvent.RETURN_HOME_ALLOWED:
        if state in (
            InternalExecutionState.WAITING_TARGET,
            InternalExecutionState.PREPARING_GOAL,
            InternalExecutionState.PAUSED,
        ):
            return _decision(
                context,
                _clean_context(InternalExecutionState.IDLE),
                TransitionEffect.TERMINAL_CANCELED,
                TransitionEffect.START_RETURN_HOME,
            )
        if state is InternalExecutionState.SAFETY_BLOCKED:
            if not _is_clean_safety_blocked(context):
                return _rejected(context)
            return _decision(
                context,
                _clean_context(InternalExecutionState.IDLE),
                TransitionEffect.TERMINAL_CANCELED,
                TransitionEffect.START_RETURN_HOME,
            )
        if state in (
            InternalExecutionState.NAVIGATION_STARTING,
            InternalExecutionState.LEASE_ACQUIRING,
            InternalExecutionState.EXECUTING,
        ):
            return _start_canceling(
                context,
                CancelIntent.RETURN_HOME,
                PendingOutcome.START_RETURN_HOME,
            )
        if state is InternalExecutionState.FINALIZING:
            return _decision(
                context,
                replace(
                    context,
                    state=InternalExecutionState.CANCELING,
                    cancel_intent=CancelIntent.RETURN_HOME,
                    pending_outcome=PendingOutcome.START_RETURN_HOME,
                ),
            )
        return _rejected(context)

    if event is StateMachineEvent.RESUME_ALLOWED:
        if state is InternalExecutionState.PAUSED:
            return _decision(
                context,
                _clean_context(InternalExecutionState.PREPARING_GOAL),
                TransitionEffect.ADVANCE_GENERATION,
            )
        if state is InternalExecutionState.SAFETY_BLOCKED:
            if not _is_clean_safety_blocked(context):
                return _rejected(context)
            return _decision(
                context,
                _clean_context(InternalExecutionState.PREPARING_GOAL),
                TransitionEffect.ADVANCE_GENERATION,
            )
        return _rejected(context)

    if event is StateMachineEvent.SAFETY_BLOCK_REQUESTED:
        if state in (
            InternalExecutionState.NAVIGATION_STARTING,
            InternalExecutionState.LEASE_ACQUIRING,
            InternalExecutionState.EXECUTING,
        ):
            return _start_canceling(
                context,
                CancelIntent.SAFETY_BLOCK,
                PendingOutcome.SAFETY_BLOCKED,
            )
        if state in (
            InternalExecutionState.WAITING_TARGET,
            InternalExecutionState.PREPARING_GOAL,
            InternalExecutionState.PAUSED,
        ):
            return _decision(
                context,
                _clean_context(InternalExecutionState.SAFETY_BLOCKED),
            )
        return _rejected(context)

    if state is InternalExecutionState.CANCELING:
        if event is StateMachineEvent.NAV_CANCEL_CONFIRMED:
            if context.pending_outcome is PendingOutcome.START_REPLAN:
                return _rejected(context)
            if (
                not context.cleanup.navigation_cancel_required
                or context.cleanup.navigation_cancel_confirmed
            ):
                return _rejected(context)
            cleanup = replace(
                context.cleanup,
                navigation_cancel_confirmed=True,
            )
            return _finish_canceling(
                context,
                replace(context, cleanup=cleanup),
            )

        if event is StateMachineEvent.NAV_GOAL_REJECTED:
            if (
                not context.cleanup.navigation_cancel_required
                or context.cleanup.navigation_cancel_confirmed
            ):
                return _rejected(context)
            cleanup = replace(
                context.cleanup,
                navigation_cancel_confirmed=True,
            )
            return _finish_canceling(
                context,
                replace(context, cleanup=cleanup),
            )

        if event is StateMachineEvent.LEASE_RELEASED:
            if context.pending_outcome is PendingOutcome.START_REPLAN:
                return _rejected(context)
            if (
                not context.cleanup.lease_release_required
                or context.cleanup.lease_release_confirmed
            ):
                return _rejected(context)
            cleanup = replace(
                context.cleanup,
                lease_release_confirmed=True,
            )
            return _finish_canceling(
                context,
                replace(context, cleanup=cleanup),
            )

        if event is StateMachineEvent.NAV_CANCEL_FAILED:
            if not context.cleanup.navigation_cancel_required:
                return _rejected(context)
            cleanup = replace(context.cleanup, cleanup_failed=True)
            return _decision(
                context,
                replace(
                    context,
                    state=InternalExecutionState.SAFETY_BLOCKED,
                    cleanup=cleanup,
                ),
            )

        if event is StateMachineEvent.LEASE_RELEASE_FAILED:
            if not context.cleanup.lease_release_required:
                return _rejected(context)
            cleanup = replace(context.cleanup, cleanup_failed=True)
            return _decision(
                context,
                replace(
                    context,
                    state=InternalExecutionState.SAFETY_BLOCKED,
                    cleanup=cleanup,
                ),
            )

        if event is StateMachineEvent.LEASE_ACQUIRED:
            if not context.cleanup.lease_acquire_pending:
                return _rejected(context)
            cleanup = replace(
                context.cleanup,
                lease_acquire_pending=False,
                lease_was_active=True,
                lease_release_required=True,
                lease_release_confirmed=False,
            )
            return _decision(
                context,
                replace(context, cleanup=cleanup),
                TransitionEffect.RELEASE_LEASE,
            )

        if event is StateMachineEvent.LEASE_ACQUIRE_FAILED:
            if not context.cleanup.lease_acquire_pending:
                return _rejected(context)
            cleanup = replace(
                context.cleanup,
                lease_acquire_pending=False,
            )
            return _finish_canceling(
                context,
                replace(context, cleanup=cleanup),
            )

        return _rejected(context)

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
            cleanup = replace(
                context.cleanup,
                lease_acquire_pending=True,
            )
            return _decision(
                context,
                replace(
                    context,
                    state=InternalExecutionState.LEASE_ACQUIRING,
                    cleanup=cleanup,
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
    ):
        if event is StateMachineEvent.LEASE_ACQUIRED:
            cleanup = replace(
                context.cleanup,
                lease_acquire_pending=False,
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
        if event is StateMachineEvent.LEASE_ACQUIRE_FAILED:
            cleanup = CleanupContext(
                navigation_submitted=True,
                navigation_cancel_required=True,
                lease_acquire_pending=False,
            )
            return _decision(
                context,
                replace(
                    context,
                    state=InternalExecutionState.CANCELING,
                    cancel_intent=CancelIntent.SAFETY_BLOCK,
                    pending_outcome=PendingOutcome.SAFETY_BLOCKED,
                    cleanup=cleanup,
                ),
                TransitionEffect.CANCEL_NAVIGATION,
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
