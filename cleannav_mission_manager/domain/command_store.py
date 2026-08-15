"""Command records and bounded TaskStatus caches."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, replace
from enum import IntEnum
import math
from typing import Optional

from cleannav_mission_manager.domain.command_processing import (
    CommandDeduplicator,
    CommandReason,
    DedupDecision,
    NormalizedTaskCommand,
)
from cleannav_mission_manager.domain.models import (
    CommandRecord,
    CommandRecordState,
)


class StatusScope(IntEnum):
    """Frozen TaskStatus scope values."""

    UNKNOWN = 0
    COMMAND = 1
    EXECUTION = 2


class ExternalTaskState(IntEnum):
    """Frozen external TaskStatus states."""

    UNKNOWN = 0
    IDLE = 1
    ACCEPTED = 2
    REJECTED = 3
    QUEUED = 4
    WAITING_TARGET = 5
    PREPARING = 6
    NAVIGATING = 7
    PAUSING = 8
    PAUSED = 9
    CANCELING = 10
    RETURNING_HOME = 11
    SAFETY_BLOCKED = 12
    SUCCEEDED = 13
    CANCELED = 14
    FAILED = 15
    EMERGENCY_STOPPED = 16


_COMMAND_TERMINAL_STATES = frozenset({
    ExternalTaskState.REJECTED,
    ExternalTaskState.SUCCEEDED,
    ExternalTaskState.CANCELED,
    ExternalTaskState.FAILED,
})

_EXECUTION_TERMINAL_STATES = frozenset({
    ExternalTaskState.SUCCEEDED,
    ExternalTaskState.CANCELED,
    ExternalTaskState.FAILED,
    ExternalTaskState.EMERGENCY_STOPPED,
})


def _bounded_string(
    value: str,
    name: str,
    max_length: int,
    *,
    allow_empty: bool = True,
) -> str:
    if not isinstance(value, str):
        raise TypeError(f'{name} must be str')

    if not allow_empty and value == '':
        raise ValueError(f'{name} must not be empty')

    if len(value) > max_length:
        raise ValueError(
            f'{name} exceeds maximum length {max_length}'
        )

    return value


def _non_negative_int(
    value: int,
    name: str,
    maximum: int,
) -> int:
    if type(value) is not int:
        raise TypeError(f'{name} must be int')

    if not 0 <= value <= maximum:
        raise ValueError(
            f'{name} must be in [0, {maximum}]'
        )

    return value


def _finite_float(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(
        value,
        (int, float),
    ):
        raise TypeError(f'{name} must be numeric')

    value = float(value)

    if not math.isfinite(value):
        raise ValueError(f'{name} must be finite')

    return value


@dataclass(frozen=True)
class StatusSnapshot:
    """Immutable ROS-independent TaskStatus payload snapshot."""

    stamp_ns: int
    interface_version: str
    execution_id: str
    command_id: str
    task_id: int
    status_scope: StatusScope
    state: ExternalTaskState
    progress: float = -1.0
    active_target_id: str = ''
    remaining_distance_m: float = -1.0
    reason_code: int = 0
    message: str = ''
    frame_id: str = ''

    def __post_init__(self) -> None:
        _non_negative_int(
            self.stamp_ns,
            'stamp_ns',
            2**63 - 1,
        )

        _bounded_string(
            self.interface_version,
            'interface_version',
            16,
            allow_empty=False,
        )
        _bounded_string(
            self.execution_id,
            'execution_id',
            64,
        )
        _bounded_string(
            self.command_id,
            'command_id',
            128,
            allow_empty=False,
        )
        _bounded_string(
            self.active_target_id,
            'active_target_id',
            128,
        )
        _bounded_string(
            self.message,
            'message',
            512,
        )

        if self.frame_id != '':
            raise ValueError('frame_id must be empty')

        _non_negative_int(
            self.task_id,
            'task_id',
            65535,
        )
        _non_negative_int(
            self.reason_code,
            'reason_code',
            2147483647,
        )

        if not isinstance(self.status_scope, StatusScope):
            raise TypeError(
                'status_scope must be StatusScope'
            )

        if self.status_scope is StatusScope.UNKNOWN:
            raise ValueError(
                'status_scope must not be UNKNOWN'
            )

        if not isinstance(self.state, ExternalTaskState):
            raise TypeError(
                'state must be ExternalTaskState'
            )

        progress = _finite_float(
            self.progress,
            'progress',
        )

        if not (
            progress == -1.0
            or 0.0 <= progress <= 1.0
        ):
            raise ValueError(
                'progress must be -1 or in [0, 1]'
            )

        remaining = _finite_float(
            self.remaining_distance_m,
            'remaining_distance_m',
        )

        if not (
            remaining == -1.0
            or remaining >= 0.0
        ):
            raise ValueError(
                'remaining_distance_m must be -1 or >= 0'
            )

        if (
            self.status_scope is StatusScope.EXECUTION
            and self.execution_id == ''
        ):
            raise ValueError(
                'execution scope requires execution_id'
            )

    @property
    def is_terminal(self) -> bool:
        """Return whether this snapshot is terminal for its scope."""
        if self.status_scope is StatusScope.COMMAND:
            return self.state in _COMMAND_TERMINAL_STATES

        return self.state in _EXECUTION_TERMINAL_STATES


@dataclass(frozen=True)
class TerminalCacheKey:
    """Stable key for one terminal status."""

    status_scope: StatusScope
    identity: str


@dataclass(frozen=True)
class TerminalWriteResult:
    """Result of a terminal-cache write."""

    written: bool
    reason_code: CommandReason
    stored: StatusSnapshot
    evicted: Optional[StatusSnapshot] = None


class TerminalStatusCache:
    """Bounded FIFO cache for command and execution terminal statuses."""

    def __init__(self, max_entries: int) -> None:
        if type(max_entries) is not int:
            raise TypeError('max_entries must be int')

        if max_entries <= 0:
            raise ValueError('max_entries must be > 0')

        self._max_entries = max_entries
        self._items: OrderedDict[
            TerminalCacheKey,
            StatusSnapshot,
        ] = OrderedDict()

    @property
    def max_entries(self) -> int:
        """Return configured cache capacity."""
        return self._max_entries

    def write(
        self,
        snapshot: StatusSnapshot,
    ) -> TerminalWriteResult:
        """Write a terminal status without overwriting first terminal."""
        if not isinstance(snapshot, StatusSnapshot):
            raise TypeError(
                'snapshot must be StatusSnapshot'
            )

        if not snapshot.is_terminal:
            raise ValueError(
                'TerminalStatusCache accepts terminal status only'
            )

        key = self._key(snapshot)

        existing = self._items.get(key)

        if existing is not None:
            return TerminalWriteResult(
                written=False,
                reason_code=(
                    CommandReason.TERMINAL_STATE_ALREADY_WRITTEN
                ),
                stored=existing,
            )

        self._items[key] = snapshot

        evicted = None

        if len(self._items) > self._max_entries:
            _, evicted = self._items.popitem(
                last=False
            )

        return TerminalWriteResult(
            written=True,
            reason_code=CommandReason.NONE,
            stored=snapshot,
            evicted=evicted,
        )

    def get_command(
        self,
        command_id: str,
    ) -> Optional[StatusSnapshot]:
        """Return cached command terminal status."""
        return self._items.get(
            TerminalCacheKey(
                StatusScope.COMMAND,
                command_id,
            )
        )

    def get_execution(
        self,
        execution_id: str,
    ) -> Optional[StatusSnapshot]:
        """Return cached execution terminal status."""
        return self._items.get(
            TerminalCacheKey(
                StatusScope.EXECUTION,
                execution_id,
            )
        )

    def snapshot(self) -> tuple[StatusSnapshot, ...]:
        """Return terminal statuses in FIFO insertion order."""
        return tuple(self._items.values())

    def __len__(self) -> int:
        return len(self._items)

    @staticmethod
    def _key(
        snapshot: StatusSnapshot,
    ) -> TerminalCacheKey:
        if snapshot.status_scope is StatusScope.COMMAND:
            identity = snapshot.command_id
        else:
            identity = snapshot.execution_id

        return TerminalCacheKey(
            snapshot.status_scope,
            identity,
        )


@dataclass(frozen=True)
class CommandStoreResult:
    """Result of command registration or idempotent lookup."""

    decision: DedupDecision
    reason_code: CommandReason
    record: Optional[CommandRecord] = None
    replay_status: Optional[StatusSnapshot] = None


@dataclass(frozen=True)
class RecordUpdateResult:
    """Result of updating one internal Command Record."""

    updated: bool
    reason_code: CommandReason
    record: CommandRecord


@dataclass(frozen=True)
class CommandStatusWriteResult:
    """Result of storing current Command Scope TaskStatus."""

    written: bool
    reason_code: CommandReason
    stored: StatusSnapshot
    evicted: Optional[StatusSnapshot] = None


class CommandRecordStore:
    """Command records, deduplication and current/terminal status storage."""

    def __init__(
        self,
        terminal_cache_max_entries: int,
    ) -> None:
        self._deduplicator = CommandDeduplicator()
        self._records: dict[str, CommandRecord] = {}
        self._latest_command_status: dict[
            str,
            StatusSnapshot,
        ] = {}
        self._terminal_cache = TerminalStatusCache(
            terminal_cache_max_entries
        )

    @property
    def terminal_cache(self) -> TerminalStatusCache:
        """Return terminal cache."""
        return self._terminal_cache

    def register_or_replay(
        self,
        command: NormalizedTaskCommand,
        *,
        created_ros_time_s: float,
        replay_stamp_ns: int,
    ) -> CommandStoreResult:
        """Register a new command or return its idempotent replay."""
        if not isinstance(command, NormalizedTaskCommand):
            raise TypeError(
                'command must be NormalizedTaskCommand'
            )

        _non_negative_int(
            replay_stamp_ns,
            'replay_stamp_ns',
            2**63 - 1,
        )

        decision = self._deduplicator.check_and_record(
            command
        )

        if decision.decision is DedupDecision.NEW:
            record = CommandRecord(
                command_id=command.command_id,
                task_id=command.task_id,
                source=command.source,
                state=CommandRecordState.RECEIVED,
                reason_code=int(
                    CommandReason.COMMAND_RECEIVED
                ),
                created_ros_time_s=created_ros_time_s,
            )
            self._records[command.command_id] = record

            return CommandStoreResult(
                decision=DedupDecision.NEW,
                reason_code=CommandReason.COMMAND_RECEIVED,
                record=record,
            )

        if decision.decision is DedupDecision.CONFLICT:
            return CommandStoreResult(
                decision=DedupDecision.CONFLICT,
                reason_code=(
                    CommandReason.DUPLICATE_COMMAND_CONFLICT
                ),
            )

        record = self._records.get(command.command_id)

        if record is None:
            return CommandStoreResult(
                decision=DedupDecision.REPLAY,
                reason_code=(
                    CommandReason.COMMAND_RECORD_CORRUPT
                ),
            )

        latest = self._latest_command_status.get(
            command.command_id
        )

        if latest is None:
            return CommandStoreResult(
                decision=DedupDecision.REPLAY,
                reason_code=(
                    CommandReason.COMMAND_RECORD_CORRUPT
                ),
                record=record,
            )

        if latest.is_terminal:
            latest = self._terminal_cache.get_command(
                command.command_id
            )

            if latest is None:
                return CommandStoreResult(
                    decision=DedupDecision.REPLAY,
                    reason_code=(
                        CommandReason.TERMINAL_CACHE_MISSING
                    ),
                    record=record,
                )

        replay = replace(
            latest,
            stamp_ns=replay_stamp_ns,
            reason_code=int(
                CommandReason.IDEMPOTENT_REPLAY
            ),
        )

        return CommandStoreResult(
            decision=DedupDecision.REPLAY,
            reason_code=CommandReason.IDEMPOTENT_REPLAY,
            record=record,
            replay_status=replay,
        )

    def get_record(
        self,
        command_id: str,
    ) -> Optional[CommandRecord]:
        """Return Command Record by command_id."""
        return self._records.get(command_id)

    def update_record(
        self,
        command_id: str,
        *,
        state: Optional[CommandRecordState] = None,
        execution_id: Optional[str] = None,
        reason_code: Optional[int] = None,
    ) -> RecordUpdateResult:
        """Update stored record without implementing transition policy."""
        record = self._records.get(command_id)

        if record is None:
            raise KeyError(command_id)

        if record.state is CommandRecordState.TERMINAL:
            return RecordUpdateResult(
                updated=False,
                reason_code=(
                    CommandReason.TERMINAL_STATE_ALREADY_WRITTEN
                ),
                record=record,
            )

        if state is not None:
            if not isinstance(state, CommandRecordState):
                raise TypeError(
                    'state must be CommandRecordState'
                )

            record.state = state

        if execution_id is not None:
            if not isinstance(execution_id, str):
                raise TypeError(
                    'execution_id must be str'
                )

            record.execution_id = execution_id

        if reason_code is not None:
            _non_negative_int(
                reason_code,
                'reason_code',
                2147483647,
            )
            record.reason_code = reason_code

        return RecordUpdateResult(
            updated=True,
            reason_code=CommandReason.NONE,
            record=record,
        )

    def write_command_status(
        self,
        snapshot: StatusSnapshot,
    ) -> CommandStatusWriteResult:
        """Store latest Command Scope status and terminal snapshot."""
        if not isinstance(snapshot, StatusSnapshot):
            raise TypeError(
                'snapshot must be StatusSnapshot'
            )

        if snapshot.status_scope is not StatusScope.COMMAND:
            raise ValueError(
                'write_command_status requires COMMAND scope'
            )

        record = self._records.get(snapshot.command_id)

        if record is None:
            raise KeyError(snapshot.command_id)

        if record.task_id != snapshot.task_id:
            raise ValueError(
                'TaskStatus task_id does not match Command Record'
            )

        previous = self._latest_command_status.get(
            snapshot.command_id
        )

        if previous is not None and previous.is_terminal:
            return CommandStatusWriteResult(
                written=False,
                reason_code=(
                    CommandReason.TERMINAL_STATE_ALREADY_WRITTEN
                ),
                stored=previous,
            )

        if snapshot.is_terminal:
            cache_result = self._terminal_cache.write(
                snapshot
            )

            if not cache_result.written:
                self._latest_command_status[
                    snapshot.command_id
                ] = cache_result.stored

                return CommandStatusWriteResult(
                    written=False,
                    reason_code=cache_result.reason_code,
                    stored=cache_result.stored,
                    evicted=cache_result.evicted,
                )

            self._latest_command_status[
                snapshot.command_id
            ] = snapshot

            return CommandStatusWriteResult(
                written=True,
                reason_code=CommandReason.NONE,
                stored=snapshot,
                evicted=cache_result.evicted,
            )

        self._latest_command_status[
            snapshot.command_id
        ] = snapshot

        return CommandStatusWriteResult(
            written=True,
            reason_code=CommandReason.NONE,
            stored=snapshot,
        )

    def get_current_command_status(
        self,
        command_id: str,
    ) -> Optional[StatusSnapshot]:
        """Return latest Command Scope status."""
        return self._latest_command_status.get(command_id)

    def __len__(self) -> int:
        return len(self._records)
