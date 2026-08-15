"""Execution Record storage for Mission Manager M1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from cleannav_mission_manager.domain.command_processing import (
    CommandReason,
    NormalizedTaskCommand,
)
from cleannav_mission_manager.domain.command_store import (
    StatusScope,
    StatusSnapshot,
    TerminalStatusCache,
)
from cleannav_mission_manager.domain.models import (
    CancelIntent,
    ExecutionRecord,
    InternalExecutionState,
)


@dataclass(frozen=True)
class ExecutionCreateResult:
    """Result of activating one Mission execution."""

    created: bool
    reason_code: CommandReason
    record: Optional[ExecutionRecord] = None


@dataclass(frozen=True)
class ExecutionUpdateResult:
    """Result of updating one Execution Record."""

    updated: bool
    reason_code: CommandReason
    record: ExecutionRecord


@dataclass(frozen=True)
class GenerationAdvanceResult:
    """Result of advancing an execution generation."""

    advanced: bool
    reason_code: CommandReason
    record: ExecutionRecord
    previous_generation: int
    current_generation: int


@dataclass(frozen=True)
class ExecutionStatusWriteResult:
    """Result of storing one Execution Scope TaskStatus."""

    written: bool
    reason_code: CommandReason
    stored: Optional[StatusSnapshot] = None
    evicted: Optional[StatusSnapshot] = None


class ExecutionRecordStore:
    """
    Store Mission execution records and current execution status.

    Execution IDs are created only when a Mission is activated. The ID
    generator is injected so unit tests remain deterministic and the final
    production ID format is not frozen by this domain component.

    Generation submit ownership and stale callback filtering intentionally
    belong to the later Generation Gate.
    """

    def __init__(
        self,
        *,
        id_factory: Callable[[], str],
        terminal_cache: TerminalStatusCache,
    ) -> None:
        if not callable(id_factory):
            raise TypeError('id_factory must be callable')

        if not isinstance(terminal_cache, TerminalStatusCache):
            raise TypeError(
                'terminal_cache must be TerminalStatusCache'
            )

        self._id_factory = id_factory
        self._terminal_cache = terminal_cache

        self._records: dict[str, ExecutionRecord] = {}
        self._command_to_execution: dict[str, str] = {}
        self._latest_status: dict[str, StatusSnapshot] = {}
        self._terminal_ids: set[str] = set()

        self._active_execution_id: Optional[str] = None

    @property
    def active_execution_id(self) -> Optional[str]:
        """Return the active execution ID, if any."""
        return self._active_execution_id

    @property
    def terminal_cache(self) -> TerminalStatusCache:
        """Return the shared terminal status cache."""
        return self._terminal_cache

    def activate(
        self,
        command: NormalizedTaskCommand,
        *,
        initial_state: InternalExecutionState,
    ) -> ExecutionCreateResult:
        """
        Create one active Mission execution.

        The first generation is always one. A queued command must call this
        method only when it actually becomes active.
        """
        if not isinstance(command, NormalizedTaskCommand):
            raise TypeError(
                'command must be NormalizedTaskCommand'
            )

        if not isinstance(
            initial_state,
            InternalExecutionState,
        ):
            raise TypeError(
                'initial_state must be InternalExecutionState'
            )

        if self._active_execution_id is not None:
            active = self._records.get(
                self._active_execution_id
            )

            return ExecutionCreateResult(
                created=False,
                reason_code=(
                    CommandReason.ACTIVE_EXECUTION_CONFLICT
                ),
                record=active,
            )

        if command.command_id in self._command_to_execution:
            return ExecutionCreateResult(
                created=False,
                reason_code=(
                    CommandReason.EXECUTION_RECORD_CORRUPT
                ),
                record=self.get_by_command_id(
                    command.command_id
                ),
            )

        execution_id = self._id_factory()

        if not isinstance(execution_id, str):
            raise TypeError(
                'id_factory must return str'
            )

        if execution_id == '':
            raise ValueError(
                'id_factory must return non-empty execution_id'
            )

        if len(execution_id) > 64:
            raise ValueError(
                'execution_id exceeds v1.0 bound of 64'
            )

        if execution_id in self._records:
            return ExecutionCreateResult(
                created=False,
                reason_code=(
                    CommandReason.EXECUTION_RECORD_CORRUPT
                ),
                record=self._records[execution_id],
            )

        record = ExecutionRecord(
            execution_id=execution_id,
            command_id=command.command_id,
            task_id=command.task_id,
            state=initial_state,
            generation=1,
        )

        self._records[execution_id] = record
        self._command_to_execution[
            command.command_id
        ] = execution_id
        self._active_execution_id = execution_id

        return ExecutionCreateResult(
            created=True,
            reason_code=CommandReason.EXECUTION_ACTIVATED,
            record=record,
        )

    def get(
        self,
        execution_id: str,
    ) -> Optional[ExecutionRecord]:
        """Return an Execution Record by execution_id."""
        return self._records.get(execution_id)

    def get_by_command_id(
        self,
        command_id: str,
    ) -> Optional[ExecutionRecord]:
        """Return the execution created by one command."""
        execution_id = self._command_to_execution.get(
            command_id
        )

        if execution_id is None:
            return None

        return self._records.get(execution_id)

    def get_active(self) -> Optional[ExecutionRecord]:
        """Return the currently active execution."""
        if self._active_execution_id is None:
            return None

        return self._records.get(
            self._active_execution_id
        )

    def update(
        self,
        execution_id: str,
        *,
        state: Optional[InternalExecutionState] = None,
        cancel_intent: Optional[CancelIntent] = None,
        clear_cancel_intent: bool = False,
        active_target_id: Optional[str] = None,
    ) -> ExecutionUpdateResult:
        """
        Update an Execution Record without implementing transition policy.

        Legal state transitions are the responsibility of the later pure
        Mission State Machine.
        """
        record = self._require_record(execution_id)

        if execution_id in self._terminal_ids:
            return ExecutionUpdateResult(
                updated=False,
                reason_code=(
                    CommandReason.TERMINAL_STATE_ALREADY_WRITTEN
                ),
                record=record,
            )

        if state is not None:
            if not isinstance(
                state,
                InternalExecutionState,
            ):
                raise TypeError(
                    'state must be InternalExecutionState'
                )

            record.state = state

        if (
            cancel_intent is not None
            and clear_cancel_intent
        ):
            raise ValueError(
                'cannot set and clear cancel_intent together'
            )

        if cancel_intent is not None:
            if not isinstance(cancel_intent, CancelIntent):
                raise TypeError(
                    'cancel_intent must be CancelIntent'
                )

            record.cancel_intent = cancel_intent

        if clear_cancel_intent:
            record.cancel_intent = None

        if active_target_id is not None:
            if not isinstance(active_target_id, str):
                raise TypeError(
                    'active_target_id must be str'
                )

            if len(active_target_id) > 128:
                raise ValueError(
                    'active_target_id exceeds v1.0 bound of 128'
                )

            record.active_target_id = active_target_id

        return ExecutionUpdateResult(
            updated=True,
            reason_code=CommandReason.NONE,
            record=record,
        )

    def advance_generation(
        self,
        execution_id: str,
    ) -> GenerationAdvanceResult:
        """
        Increment generation while preserving execution identity.

        This operation models the generation change required by RESUME. It
        does not submit a Navigation Goal; submit gating belongs to M1-2h.
        """
        record = self._require_record(execution_id)
        previous = record.generation

        if execution_id in self._terminal_ids:
            return GenerationAdvanceResult(
                advanced=False,
                reason_code=(
                    CommandReason.TERMINAL_STATE_ALREADY_WRITTEN
                ),
                record=record,
                previous_generation=previous,
                current_generation=previous,
            )

        if self._active_execution_id != execution_id:
            return GenerationAdvanceResult(
                advanced=False,
                reason_code=(
                    CommandReason.ACTIVE_EXECUTION_CONFLICT
                ),
                record=record,
                previous_generation=previous,
                current_generation=previous,
            )

        record.generation += 1

        return GenerationAdvanceResult(
            advanced=True,
            reason_code=CommandReason.NONE,
            record=record,
            previous_generation=previous,
            current_generation=record.generation,
        )

    def write_status(
        self,
        snapshot: StatusSnapshot,
    ) -> ExecutionStatusWriteResult:
        """
        Store the latest Execution Scope status.

        A terminal status is atomically written to the shared terminal cache,
        marks the execution terminal and releases the store's active slot.
        """
        if not isinstance(snapshot, StatusSnapshot):
            raise TypeError(
                'snapshot must be StatusSnapshot'
            )

        if snapshot.status_scope is not StatusScope.EXECUTION:
            raise ValueError(
                'write_status requires EXECUTION scope'
            )

        record = self._require_record(
            snapshot.execution_id
        )

        self._validate_snapshot_identity(
            record,
            snapshot,
        )

        if snapshot.execution_id in self._terminal_ids:
            cached = self._terminal_cache.get_execution(
                snapshot.execution_id
            )

            if cached is None:
                return ExecutionStatusWriteResult(
                    written=False,
                    reason_code=(
                        CommandReason.TERMINAL_CACHE_MISSING
                    ),
                )

            return ExecutionStatusWriteResult(
                written=False,
                reason_code=(
                    CommandReason.TERMINAL_STATE_ALREADY_WRITTEN
                ),
                stored=cached,
            )

        if not snapshot.is_terminal:
            self._latest_status[
                snapshot.execution_id
            ] = snapshot

            return ExecutionStatusWriteResult(
                written=True,
                reason_code=CommandReason.NONE,
                stored=snapshot,
            )

        cache_result = self._terminal_cache.write(
            snapshot
        )

        if not cache_result.written:
            return ExecutionStatusWriteResult(
                written=False,
                reason_code=cache_result.reason_code,
                stored=cache_result.stored,
                evicted=cache_result.evicted,
            )

        self._latest_status[
            snapshot.execution_id
        ] = snapshot
        self._terminal_ids.add(
            snapshot.execution_id
        )

        if self._active_execution_id == snapshot.execution_id:
            self._active_execution_id = None

        return ExecutionStatusWriteResult(
            written=True,
            reason_code=CommandReason.NONE,
            stored=snapshot,
            evicted=cache_result.evicted,
        )

    def get_current_status(
        self,
        execution_id: str,
    ) -> Optional[StatusSnapshot]:
        """Return latest Execution Scope status."""
        return self._latest_status.get(execution_id)

    def is_terminal(
        self,
        execution_id: str,
    ) -> bool:
        """Return whether an execution has reached terminal state."""
        self._require_record(execution_id)
        return execution_id in self._terminal_ids

    def __len__(self) -> int:
        return len(self._records)

    def _require_record(
        self,
        execution_id: str,
    ) -> ExecutionRecord:
        if not isinstance(execution_id, str):
            raise TypeError('execution_id must be str')

        record = self._records.get(execution_id)

        if record is None:
            raise KeyError(execution_id)

        return record

    @staticmethod
    def _validate_snapshot_identity(
        record: ExecutionRecord,
        snapshot: StatusSnapshot,
    ) -> None:
        if record.command_id != snapshot.command_id:
            raise ValueError(
                'TaskStatus command_id does not match Execution Record'
            )

        if record.task_id != snapshot.task_id:
            raise ValueError(
                'TaskStatus task_id does not match Execution Record'
            )
