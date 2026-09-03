"""ROS-independent orchestration for the first Mission Manager slice."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol

from cleannav_mission_manager.adapters.navigation import (
    NavigationAdapter,
    NavigationEvent,
    NavigationEventType,
)
from cleannav_mission_manager.adapters.safety import (
    SafetyAdapter,
    SafetyEvent,
    SafetyEventType,
)
from cleannav_mission_manager.domain.command_processing import (
    CommandReason,
    CommandValidator,
    DedupDecision,
    NormalizedTaskCommand,
    TaskCatalogEntry,
    TaskKind,
)
from cleannav_mission_manager.domain.command_store import (
    CommandRecordStore,
    ExternalTaskState,
    StatusScope,
    StatusSnapshot,
)
from cleannav_mission_manager.domain.execution_store import (
    ExecutionRecordStore,
)
from cleannav_mission_manager.domain.generation_gate import (
    CallbackClassification,
    GenerationGate,
    GenerationHandle,
)
from cleannav_mission_manager.domain.mission_queue import (
    MissionQueue,
    QueuedMission,
)
from cleannav_mission_manager.domain.models import (
    CommandRecordState,
    ExecutionRecord,
    InternalExecutionState,
    ManagerMode,
)
from cleannav_mission_manager.domain.state_machine import (
    StateMachineContext,
    StateMachineEvent,
    TransitionDecision,
    TransitionEffect,
    transition,
)


class GoalResolver(Protocol):
    """Resolve one validated mission into an opaque navigation payload."""

    def __call__(
        self,
        command: NormalizedTaskCommand,
        task: TaskCatalogEntry,
    ) -> object:
        """Return a navigation payload without exposing its structure."""
        ...


class GoalResolutionError(RuntimeError):
    """Raised when a resolver cannot provide a usable navigation payload."""


class UnsupportedEffectError(RuntimeError):
    """Raised when a State Machine effect is outside this slice."""


@dataclass(frozen=True)
class CoreResult:
    """Immutable result returned by command and adapter entry points."""

    accepted: bool
    reason_code: CommandReason
    execution_id: Optional[str] = None
    replay_status: Optional[StatusSnapshot] = None
    stale: bool = False
    supported: bool = True
    message: str = ''


class MissionManagerCore:
    """Orchestrate existing domain primitives and injected adapters."""

    def __init__(
        self,
        *,
        validator: CommandValidator,
        command_store: CommandRecordStore,
        mission_queue: MissionQueue,
        execution_store: ExecutionRecordStore,
        generation_gate: GenerationGate,
        navigation: NavigationAdapter,
        safety: SafetyAdapter,
        clock: object,
        goal_resolver: GoalResolver,
    ) -> None:
        if not isinstance(validator, CommandValidator):
            raise TypeError('validator must be CommandValidator')
        if not isinstance(command_store, CommandRecordStore):
            raise TypeError('command_store must be CommandRecordStore')
        if not isinstance(mission_queue, MissionQueue):
            raise TypeError('mission_queue must be MissionQueue')
        if not isinstance(execution_store, ExecutionRecordStore):
            raise TypeError(
                'execution_store must be ExecutionRecordStore'
            )
        if not isinstance(generation_gate, GenerationGate):
            raise TypeError('generation_gate must be GenerationGate')
        if not callable(getattr(clock, 'now_ros', None)):
            raise TypeError('clock must provide callable now_ros')
        if not callable(goal_resolver):
            raise TypeError('goal_resolver must be callable')

        self._validator = validator
        self._command_store = command_store
        self._mission_queue = mission_queue
        self._execution_store = execution_store
        self._generation_gate = generation_gate
        self._navigation = navigation
        self._safety = safety
        self._clock = clock
        self._goal_resolver = goal_resolver

        self._active_context: Optional[StateMachineContext] = None
        self._active_mission: Optional[QueuedMission] = None
        self._active_goal: object | None = None
        self._manager_mode = ManagerMode.NORMAL
        self._pending_reset_command: Optional[NormalizedTaskCommand] = None

    @property
    def active_execution(self) -> Optional[ExecutionRecord]:
        """Return the active execution record, if any."""
        return self._execution_store.get_active()

    @property
    def active_context(self) -> Optional[StateMachineContext]:
        """Return the active pure State Machine context, if any."""
        return self._active_context

    @property
    def queued_missions(self) -> tuple[QueuedMission, ...]:
        """Return an immutable queue snapshot."""
        return self._mission_queue.snapshot()

    @property
    def active_generation_handle(self) -> Optional[GenerationHandle]:
        """Return the currently registered navigation generation."""
        return self._generation_gate.active_handle

    @property
    def manager_mode(self) -> ManagerMode:
        """Return the current manager-level mode."""
        return self._manager_mode

    def submit_command(
        self,
        command: NormalizedTaskCommand,
    ) -> CoreResult:
        """Validate, deduplicate and route one normalized command."""
        if not isinstance(command, NormalizedTaskCommand):
            raise TypeError('command must be NormalizedTaskCommand')

        validation = self._validator.validate(
            command,
            now_ros_ns=self._now_ros_ns(),
        )
        if not validation.accepted:
            return CoreResult(
                accepted=False,
                reason_code=validation.reason_code,
            )

        registration = self._command_store.register_or_replay(
            command,
            created_ros_time_s=self._clock.now_ros(),
            replay_stamp_ns=self._now_ros_ns(),
        )
        if registration.decision is DedupDecision.REPLAY:
            return CoreResult(
                accepted=True,
                reason_code=registration.reason_code,
                execution_id=(
                    registration.record.execution_id
                    if registration.record is not None
                    else None
                ),
                replay_status=registration.replay_status,
                message='idempotent replay',
            )
        if registration.decision is DedupDecision.CONFLICT:
            return CoreResult(
                accepted=False,
                reason_code=registration.reason_code,
                message='command_id semantic conflict',
            )

        if registration.record is None:
            raise RuntimeError('new command registration returned no record')

        self._write_command_status(
            command,
            execution_id='',
            state=ExternalTaskState.ACCEPTED,
            reason_code=CommandReason.COMMAND_VALID,
        )

        if validation.task is None:
            raise RuntimeError('accepted validation returned no task')

        if validation.task.task_kind is TaskKind.CONTROL:
            return self._submit_control(
                command,
                validation.task,
            )

        enqueue = self._mission_queue.enqueue(command, validation.task)
        if not enqueue.accepted or enqueue.item is None:
            self._command_store.update_record(
                command.command_id,
                state=CommandRecordState.TERMINAL,
                reason_code=int(enqueue.reason_code),
            )
            self._write_command_status(
                command,
                execution_id='',
                state=ExternalTaskState.REJECTED,
                reason_code=enqueue.reason_code,
            )
            return CoreResult(
                accepted=False,
                reason_code=enqueue.reason_code,
            )

        self._command_store.update_record(
            command.command_id,
            state=CommandRecordState.QUEUED,
            reason_code=int(CommandReason.COMMAND_QUEUED),
        )
        self._write_command_status(
            command,
            execution_id='',
            state=ExternalTaskState.QUEUED,
            reason_code=CommandReason.COMMAND_QUEUED,
        )

        if self.active_execution is None:
            self._activate_next()

        record = self._execution_store.get_by_command_id(
            command.command_id
        )
        return CoreResult(
            accepted=True,
            reason_code=CommandReason.COMMAND_QUEUED,
            execution_id=(record.execution_id if record is not None else None),
        )

    def _submit_control(
        self,
        command: NormalizedTaskCommand,
        task: TaskCatalogEntry,
    ) -> CoreResult:
        """Route a validated CONTROL without entering MissionQueue."""
        control_events = {
            2: StateMachineEvent.PAUSE_REQUESTED,
            3: StateMachineEvent.RESUME_ALLOWED,
            4: StateMachineEvent.STOP_REQUESTED,
            6: StateMachineEvent.ESTOP_REQUESTED,
        }
        if task.task_id == 7:
            return self._submit_reset_control(command)

        event = control_events.get(task.task_id)
        if event is None:
            return self._reject_control(
                command,
                'CONTROL task is unsupported in this slice',
            )

        if event is StateMachineEvent.STOP_REQUESTED:
            self._clear_waiting_queue()

        if event is StateMachineEvent.ESTOP_REQUESTED:
            self._clear_waiting_queue()

        if (
            self._active_context is None
            and event in (
                StateMachineEvent.PAUSE_REQUESTED,
                StateMachineEvent.RESUME_ALLOWED,
            )
        ):
            return self._reject_control(
                command,
                'CONTROL requires an active execution',
            )

        result = self._apply_event(event)
        if not result.accepted:
            return self._reject_control(
                command,
                result.message or 'CONTROL event rejected by State Machine',
            )

        self._mark_control_applied(command)
        return CoreResult(
            accepted=True,
            reason_code=CommandReason.COMMAND_VALID,
            execution_id=(
                self.active_execution.execution_id
                if self.active_execution is not None
                else None
            ),
        )

    def _submit_reset_control(
        self,
        command: NormalizedTaskCommand,
    ) -> CoreResult:
        """Request reset and wait for the Safety adapter result event."""
        context = self._active_context
        if context is None:
            context = StateMachineContext(
                state=InternalExecutionState.IDLE,
                manager_mode=self._manager_mode,
            )
        decision = transition(context, StateMachineEvent.RESET_ESTOP_ALLOWED)
        if not decision.accepted:
            return self._reject_control(
                command,
                'RESET_ESTOP is not currently allowed',
            )

        self._pending_reset_command = command
        self._mark_control_applied(command)
        self._safety.reset_emergency_stop()
        return CoreResult(
            accepted=True,
            reason_code=CommandReason.COMMAND_VALID,
            message='waiting for safety reset result',
        )

    def _mark_control_applied(
        self,
        command: NormalizedTaskCommand,
    ) -> None:
        """Persist a nonterminal accepted CONTROL status for replay."""
        self._command_store.update_record(
            command.command_id,
            state=CommandRecordState.APPLIED,
            reason_code=int(CommandReason.COMMAND_VALID),
        )
        self._write_command_status(
            command,
            execution_id=(
                self.active_execution.execution_id
                if self.active_execution is not None
                else ''
            ),
            state=ExternalTaskState.ACCEPTED,
            reason_code=CommandReason.COMMAND_VALID,
        )

    def _reject_control(
        self,
        command: NormalizedTaskCommand,
        message: str,
    ) -> CoreResult:
        """Persist an explicit CONTROL rejection for future replay."""
        self._command_store.update_record(
            command.command_id,
            state=CommandRecordState.TERMINAL,
            reason_code=int(CommandReason.COMMAND_VALID),
        )
        self._write_command_status(
            command,
            execution_id='',
            state=ExternalTaskState.REJECTED,
            reason_code=CommandReason.COMMAND_VALID,
        )
        return CoreResult(
            accepted=False,
            reason_code=CommandReason.COMMAND_VALID,
            supported=False,
            message=message,
        )

    def _clear_waiting_queue(self) -> None:
        """Cancel queued missions through existing queue/store APIs."""
        for item in self._mission_queue.clear():
            self._command_store.update_record(
                item.command.command_id,
                state=CommandRecordState.TERMINAL,
                reason_code=int(CommandReason.NONE),
            )
            self._write_command_status(
                item.command,
                execution_id='',
                state=ExternalTaskState.CANCELED,
                reason_code=CommandReason.NONE,
            )

    def handle_navigation_event(
        self,
        event: NavigationEvent,
    ) -> CoreResult:
        """Classify one navigation callback before applying its event."""
        if not isinstance(event, NavigationEvent):
            raise TypeError('event must be NavigationEvent')

        classification = self._generation_gate.classify_callback(
            event.handle
        )
        if classification.classification is CallbackClassification.STALE:
            return CoreResult(
                accepted=False,
                reason_code=classification.reason_code,
                stale=True,
                message='stale navigation callback ignored',
            )

        event_map = {
            NavigationEventType.GOAL_ACCEPTED:
                StateMachineEvent.NAV_GOAL_ACCEPTED,
            NavigationEventType.GOAL_REJECTED:
                StateMachineEvent.NAV_GOAL_REJECTED,
            NavigationEventType.SUCCEEDED:
                StateMachineEvent.NAV_SUCCEEDED,
            NavigationEventType.FAILED:
                StateMachineEvent.NAV_FAILED,
            NavigationEventType.CANCEL_CONFIRMED:
                StateMachineEvent.NAV_CANCEL_CONFIRMED,
            NavigationEventType.CANCEL_FAILED:
                StateMachineEvent.NAV_CANCEL_FAILED,
        }
        return self._apply_event(event_map[event.event_type])

    def handle_safety_event(
        self,
        event: SafetyEvent,
    ) -> CoreResult:
        """Validate execution ownership and apply one safety callback."""
        if not isinstance(event, SafetyEvent):
            raise TypeError('event must be SafetyEvent')

        record = self.active_execution
        lease_events = {
            SafetyEventType.LEASE_ACQUIRED,
            SafetyEventType.LEASE_ACQUIRE_FAILED,
            SafetyEventType.LEASE_RELEASED,
            SafetyEventType.LEASE_RELEASE_FAILED,
        }
        if event.event_type in lease_events:
            if record is None or event.execution_id != record.execution_id:
                return CoreResult(
                    accepted=False,
                    reason_code=CommandReason.ACTIVE_EXECUTION_CONFLICT,
                    message=(
                        'safety event execution_id does not match '
                        'active execution'
                    ),
                )

            event_map = {
                SafetyEventType.LEASE_ACQUIRED:
                    StateMachineEvent.LEASE_ACQUIRED,
                SafetyEventType.LEASE_ACQUIRE_FAILED:
                    StateMachineEvent.LEASE_ACQUIRE_FAILED,
                SafetyEventType.LEASE_RELEASED:
                    StateMachineEvent.LEASE_RELEASED,
                SafetyEventType.LEASE_RELEASE_FAILED:
                    StateMachineEvent.LEASE_RELEASE_FAILED,
            }
            return self._apply_event(event_map[event.event_type])

        if event.event_type in (
            SafetyEventType.RESET_EMERGENCY_STOP_SUCCEEDED,
            SafetyEventType.RESET_EMERGENCY_STOP_FAILED,
        ):
            if self._pending_reset_command is None:
                return CoreResult(
                    accepted=False,
                    reason_code=CommandReason.ACTIVE_EXECUTION_CONFLICT,
                    message='unexpected safety reset result',
                )

            command = self._pending_reset_command
            self._pending_reset_command = None
            if event.event_type is SafetyEventType.RESET_EMERGENCY_STOP_FAILED:
                self._command_store.update_record(
                    command.command_id,
                    state=CommandRecordState.TERMINAL,
                    reason_code=int(CommandReason.NONE),
                )
                self._write_command_status(
                    command,
                    execution_id='',
                    state=ExternalTaskState.FAILED,
                    reason_code=CommandReason.NONE,
                )
                return CoreResult(
                    accepted=False,
                    reason_code=CommandReason.NONE,
                    supported=True,
                    message='safety emergency-stop reset failed',
                )

            result = self._apply_event(
                StateMachineEvent.RESET_ESTOP_ALLOWED
            )
            if not result.accepted:
                raise RuntimeError(
                    'Safety reset succeeded but State Machine rejected reset'
                )
            self._active_context = None
            self._command_store.update_record(
                command.command_id,
                state=CommandRecordState.TERMINAL,
                reason_code=int(CommandReason.NONE),
            )
            self._write_command_status(
                command,
                execution_id='',
                state=ExternalTaskState.SUCCEEDED,
                reason_code=CommandReason.NONE,
            )
            return CoreResult(
                accepted=True,
                reason_code=CommandReason.NONE,
                message='safety emergency-stop reset succeeded',
            )

        raise ValueError('unsupported SafetyEventType')

    def _prepare_and_submit_current_goal(self) -> None:
        """Resolve and submit the next goal for the current generation."""
        if self._active_mission is None:
            raise GoalResolutionError(
                'current execution has no mission context'
            )
        goal = self._goal_resolver(
            self._active_mission.command,
            self._active_mission.task,
        )
        if goal is None:
            raise GoalResolutionError(
                'goal resolver returned None for current mission'
            )
        self._active_goal = goal
        result = self._apply_event(StateMachineEvent.GOAL_PREPARED)
        if not result.accepted:
            raise RuntimeError(
                'State Machine rejected prepared resumed goal'
            )

    def _activate_next(self) -> None:
        """Activate at most one queued mission and submit its first goal."""
        if self.active_execution is not None:
            return

        now_ros_ns = self._now_ros_ns()
        queued = self._mission_queue.peek()
        if queued is None:
            return

        goal = self._goal_resolver(queued.command, queued.task)
        if goal is None:
            raise GoalResolutionError(
                'goal resolver returned None for queued mission'
            )

        popped = self._mission_queue.pop_next(now_ros_ns=now_ros_ns)
        if popped.item is None:
            return
        if popped.item.command.command_id != queued.command.command_id:
            raise RuntimeError('queue head changed during activation')

        activation = self._execution_store.activate(
            popped.item.command,
            initial_state=InternalExecutionState.PREPARING_GOAL,
        )
        if not activation.created or activation.record is None:
            raise RuntimeError(
                'Execution Store rejected activation: '
                f'{activation.reason_code.name}'
            )

        record = activation.record
        self._active_mission = popped.item
        self._active_goal = goal
        self._active_context = StateMachineContext(
            state=InternalExecutionState.PREPARING_GOAL
        )
        self._command_store.update_record(
            record.command_id,
            state=CommandRecordState.APPLIED,
            execution_id=record.execution_id,
            reason_code=int(CommandReason.EXECUTION_ACTIVATED),
        )
        self._write_command_status(
            popped.item.command,
            execution_id=record.execution_id,
            state=ExternalTaskState.PREPARING,
            reason_code=CommandReason.EXECUTION_ACTIVATED,
        )
        self._write_execution_status(
            record,
            state=ExternalTaskState.PREPARING,
            reason_code=CommandReason.EXECUTION_ACTIVATED,
        )

        self._apply_event(StateMachineEvent.GOAL_PREPARED)

    def _apply_event(self, event: StateMachineEvent) -> CoreResult:
        """Apply one domain event, then execute all emitted effects."""
        context = self._active_context
        if context is None:
            if event is not StateMachineEvent.ESTOP_REQUESTED:
                return CoreResult(
                    accepted=False,
                    reason_code=CommandReason.ACTIVE_EXECUTION_CONFLICT,
                    message='no active execution',
                )
            context = StateMachineContext(
                state=InternalExecutionState.IDLE,
                manager_mode=self._manager_mode,
            )

        decision = transition(context, event)
        if not decision.accepted:
            return CoreResult(
                accepted=False,
                reason_code=CommandReason.NONE,
                execution_id=(
                    self.active_execution.execution_id
                    if self.active_execution is not None
                    else None
                ),
                message='State Machine rejected event',
            )

        self._active_context = decision.next_context
        self._manager_mode = decision.next_context.manager_mode
        record = self.active_execution
        if record is not None:
            self._sync_execution_record(record, decision.next_context)
            self._write_running_status(record, decision.next_context)

        for effect in decision.effects:
            self._execute_effect(effect, decision)

        return CoreResult(
            accepted=True,
            reason_code=CommandReason.NONE,
            execution_id=(record.execution_id if record is not None else None),
        )

    def _execute_effect(
        self,
        effect: TransitionEffect,
        decision: TransitionDecision,
    ) -> None:
        """Execute one known effect or expose an unsupported one."""
        record = self.active_execution
        if (
            record is None
            and effect not in (
                TransitionEffect.REQUEST_SAFETY_ESTOP,
                TransitionEffect.TERMINAL_EMERGENCY_STOPPED,
            )
        ):
            raise RuntimeError(f'effect {effect.name} has no active execution')

        if effect is TransitionEffect.SUBMIT_NAVIGATION:
            handle = GenerationHandle(
                execution_id=record.execution_id,
                generation=record.generation,
            )
            registration = self._generation_gate.register(handle)
            if not registration.accepted:
                raise RuntimeError(
                    f'Generation Gate rejected navigation submit: '
                    f'{registration.reason_code.name}'
                )
            if self._active_goal is None:
                raise GoalResolutionError(
                    'navigation submit has no resolved goal payload'
                )
            self._navigation.submit_goal(handle, self._active_goal)
            return

        if effect is TransitionEffect.CANCEL_NAVIGATION:
            handle = self._generation_gate.active_handle
            if handle is None:
                raise RuntimeError(
                    'navigation cancel has no active generation'
                )
            self._navigation.cancel_goal(handle)
            return

        if effect is TransitionEffect.ACQUIRE_LEASE:
            self._safety.acquire_lease(record.execution_id)
            return

        if effect is TransitionEffect.RELEASE_LEASE:
            self._safety.release_lease(record.execution_id)
            return

        if effect is TransitionEffect.REQUEST_SAFETY_ESTOP:
            self._safety.request_emergency_stop()
            return

        if effect is TransitionEffect.ADVANCE_GENERATION:
            if record is None or self._active_mission is None:
                raise RuntimeError(
                    'generation advance has no active execution context'
                )
            advanced = self._execution_store.advance_generation(
                record.execution_id
            )
            if not advanced.advanced:
                raise RuntimeError(
                    'Execution Store rejected generation advance: '
                    f'{advanced.reason_code.name}'
                )
            old_handle = self._generation_gate.active_handle
            if old_handle is not None:
                retired = self._generation_gate.retire(old_handle)
                if not retired.retired:
                    raise RuntimeError(
                        'failed to retire paused navigation generation'
                    )
            self._prepare_and_submit_current_goal()
            return

        if effect is TransitionEffect.TERMINAL_SUCCEEDED:
            self._finish_execution(
                record,
                ExternalTaskState.SUCCEEDED,
            )
            return

        if effect is TransitionEffect.TERMINAL_FAILED:
            self._finish_execution(
                record,
                ExternalTaskState.FAILED,
            )
            return

        if effect is TransitionEffect.TERMINAL_CANCELED:
            self._finish_execution(
                record,
                ExternalTaskState.CANCELED,
            )
            return

        if effect is TransitionEffect.TERMINAL_EMERGENCY_STOPPED:
            if record is None:
                return
            self._finish_execution(
                record,
                ExternalTaskState.EMERGENCY_STOPPED,
            )
            return

        raise UnsupportedEffectError(
            f'unsupported State Machine effect: {effect.name}'
        )

    def _finish_execution(
        self,
        record: ExecutionRecord,
        state: ExternalTaskState,
    ) -> None:
        """Persist terminal status, retire navigation, activate FIFO next."""
        if (
            self._active_mission is None
            or self._active_mission.command.command_id != record.command_id
        ):
            raise RuntimeError(
                'active mission does not match terminal execution'
            )

        self._write_execution_status(
            record,
            state=state,
            reason_code=CommandReason.NONE,
        )
        command = self._active_mission.command
        self._command_store.update_record(
            record.command_id,
            state=CommandRecordState.TERMINAL,
            reason_code=int(CommandReason.NONE),
        )
        self._write_command_status(
            command,
            execution_id=record.execution_id,
            state=state,
            reason_code=CommandReason.NONE,
        )
        handle = self._generation_gate.active_handle
        if handle is not None and handle.execution_id == record.execution_id:
            retired = self._generation_gate.retire(handle)
            if not retired.retired:
                raise RuntimeError('failed to retire completed generation')

        self._active_context = None
        self._active_mission = None
        self._active_goal = None
        if self._manager_mode is ManagerMode.EMERGENCY_LATCHED:
            self._active_context = StateMachineContext(
                state=InternalExecutionState.IDLE,
                manager_mode=ManagerMode.EMERGENCY_LATCHED,
            )
        self._activate_next()

    def _sync_execution_record(
        self,
        record: ExecutionRecord,
        context: StateMachineContext,
    ) -> None:
        """Mirror State Machine output into the existing execution record."""
        kwargs = {'state': context.state}
        if context.cancel_intent is None:
            if record.cancel_intent is not None:
                kwargs['clear_cancel_intent'] = True
        else:
            kwargs['cancel_intent'] = context.cancel_intent
        result = self._execution_store.update(
            record.execution_id,
            **kwargs,
        )
        if not result.updated:
            raise RuntimeError(
                'Execution Store rejected state sync: '
                f'{result.reason_code.name}'
            )

    def _write_running_status(
        self,
        record: ExecutionRecord,
        context: StateMachineContext,
    ) -> None:
        """Publish the current state through existing store snapshots only."""
        state = self._external_state(context.state)
        if state is None:
            return
        self._write_execution_status(
            record,
            state=state,
            reason_code=CommandReason.NONE,
        )
        if self._active_mission is not None:
            self._write_command_status(
                self._active_mission.command,
                execution_id=record.execution_id,
                state=state,
                reason_code=CommandReason.NONE,
            )

    def _write_execution_status(
        self,
        record: ExecutionRecord,
        *,
        state: ExternalTaskState,
        reason_code: CommandReason,
    ) -> None:
        snapshot = StatusSnapshot(
            stamp_ns=self._now_ros_ns(),
            interface_version='1.0',
            execution_id=record.execution_id,
            command_id=record.command_id,
            task_id=record.task_id,
            status_scope=StatusScope.EXECUTION,
            state=state,
            reason_code=int(reason_code),
        )
        result = self._execution_store.write_status(snapshot)
        if not result.written:
            raise RuntimeError(
                f'Execution status write failed: {result.reason_code.name}'
            )

    def _write_command_status(
        self,
        command: NormalizedTaskCommand,
        *,
        execution_id: str,
        state: ExternalTaskState,
        reason_code: CommandReason,
    ) -> None:
        snapshot = StatusSnapshot(
            stamp_ns=self._now_ros_ns(),
            interface_version=command.interface_version,
            execution_id=execution_id,
            command_id=command.command_id,
            task_id=command.task_id,
            status_scope=StatusScope.COMMAND,
            state=state,
            reason_code=int(reason_code),
        )
        result = self._command_store.write_command_status(snapshot)
        if not result.written:
            raise RuntimeError(
                f'Command status write failed: {result.reason_code.name}'
            )

    @staticmethod
    def _external_state(
        state: InternalExecutionState,
    ) -> Optional[ExternalTaskState]:
        mapping = {
            InternalExecutionState.PREPARING_GOAL:
                ExternalTaskState.PREPARING,
            InternalExecutionState.NAVIGATION_STARTING:
                ExternalTaskState.NAVIGATING,
            InternalExecutionState.LEASE_ACQUIRING:
                ExternalTaskState.NAVIGATING,
            InternalExecutionState.EXECUTING:
                ExternalTaskState.NAVIGATING,
            InternalExecutionState.FINALIZING:
                ExternalTaskState.NAVIGATING,
            InternalExecutionState.CANCELING:
                ExternalTaskState.CANCELING,
            InternalExecutionState.PAUSED:
                ExternalTaskState.PAUSED,
            InternalExecutionState.SAFETY_BLOCKED:
                ExternalTaskState.SAFETY_BLOCKED,
        }
        return mapping.get(state)

    def _now_ros_ns(self) -> int:
        """Convert the injected semantic clock to validator/store units."""
        return int(self._clock.now_ros() * 1_000_000_000)
