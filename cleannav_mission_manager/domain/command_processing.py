"""Pure TaskCommand validation and command_id deduplication."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum
import math
import re
from typing import Mapping, Optional


_COMMAND_ID_PATTERN = re.compile(r'[A-Za-z0-9._:-]+')


class CommandSource(IntEnum):
    """Public TaskCommand source values frozen by v1.0."""

    UNKNOWN = 0
    VOICE = 1
    APP = 2
    MOCK = 3


class TaskKind(Enum):
    """Task Catalog routing kind."""

    MISSION = 'MISSION'
    CONTROL = 'CONTROL'


class CommandReason(IntEnum):
    """M0-frozen reason codes used by command processing."""

    NONE = 0
    COMMAND_RECEIVED = 1
    COMMAND_VALID = 2
    COMMAND_QUEUED = 3
    IDEMPOTENT_REPLAY = 5
    EXECUTION_ACTIVATED = 7
    QUEUED_COMMAND_EXPIRED = 29

    INTERFACE_VERSION_UNSUPPORTED = 100
    COMMAND_ID_MISSING = 101
    COMMAND_ID_INVALID = 102
    DUPLICATE_COMMAND_CONFLICT = 103
    COMMAND_EXPIRED = 104
    VALID_FOR_INVALID = 105
    SOURCE_UNKNOWN = 106
    SOURCE_NOT_ALLOWED = 107
    TASK_ID_UNKNOWN = 108
    TASK_DISABLED = 109
    CONFIDENCE_TOO_LOW = 110
    FIELD_INVALID = 111
    NONFINITE_VALUE = 112
    INTERNAL_SOURCE_FORBIDDEN_ON_HMI_TOPIC = 118
    COMMAND_TOO_LARGE = 124

    QUEUE_FULL = 200
    TASK_CATALOG_UNAVAILABLE = 201
    TASK_CATALOG_INVALID = 202

    NAV_STALE_CALLBACK_IGNORED = 414
    NAV_GOAL_ALREADY_SUBMITTED = 416
    NAV_CONCURRENT_GENERATION_FORBIDDEN = 417

    TIMESTAMP_INVALID = 609

    ACTIVE_EXECUTION_CONFLICT = 706
    ACTIVE_GENERATION_CONFLICT = 707

    TERMINAL_STATE_ALREADY_WRITTEN = 710
    TERMINAL_CACHE_MISSING = 711
    COMMAND_RECORD_CORRUPT = 712
    EXECUTION_RECORD_CORRUPT = 713


@dataclass(frozen=True)
class NormalizedTaskCommand:
    """
    ROS-independent representation of one TaskCommand.

    Times are integer nanoseconds so command expiry and deduplication do not
    depend on floating-point equality.
    """

    interface_version: str
    command_id: str
    source: int
    task_id: int
    stamp_ns: int
    valid_for_ns: int
    confidence: float = 1.0
    raw_text: str = ''


@dataclass(frozen=True)
class TaskCatalogEntry:
    """Subset of Task Catalog data required by CommandValidator."""

    task_id: int
    name: str
    task_kind: TaskKind
    enabled: bool
    allowed_sources: frozenset[int]
    requires_confirmation: bool = False


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating one normalized TaskCommand."""

    accepted: bool
    reason_code: CommandReason
    task: Optional[TaskCatalogEntry] = None


@dataclass(frozen=True)
class CommandSemanticFingerprint:
    """M0-frozen semantic fields used for command_id idempotency."""

    interface_version: str
    source: int
    task_id: int
    valid_for_ns: int


class DedupDecision(Enum):
    """Outcome of command_id deduplication."""

    NEW = 'NEW'
    REPLAY = 'REPLAY'
    CONFLICT = 'CONFLICT'


@dataclass(frozen=True)
class DedupResult:
    """Command deduplication result."""

    decision: DedupDecision
    reason_code: CommandReason = CommandReason.NONE


def semantic_fingerprint(
    command: NormalizedTaskCommand,
) -> CommandSemanticFingerprint:
    """
    Build the M0-frozen command semantic fingerprint.

    header.stamp, confidence and raw_text are intentionally excluded.
    """
    return CommandSemanticFingerprint(
        interface_version=command.interface_version,
        source=command.source,
        task_id=command.task_id,
        valid_for_ns=command.valid_for_ns,
    )


class CommandValidator:
    """
    Validate normalized public TaskCommand data.

    Validation here covers only the frozen command/interface/catalog contract.
    Manager mode/state and confirmation safety checks belong to the state
    machine layer.
    """

    def __init__(
        self,
        catalog: Optional[Mapping[int, TaskCatalogEntry]],
        *,
        supported_interface_version: str = '1.0',
        min_confidence_by_source: Optional[Mapping[int, float]] = None,
    ) -> None:
        self._catalog = catalog
        self._supported_interface_version = supported_interface_version
        self._min_confidence_by_source = dict(
            min_confidence_by_source or {}
        )

        for source, threshold in self._min_confidence_by_source.items():
            if type(source) is not int:
                raise TypeError(
                    'confidence threshold source must be int'
                )

            threshold = float(threshold)
            if not math.isfinite(threshold):
                raise ValueError(
                    'confidence threshold must be finite'
                )

            if not 0.0 <= threshold <= 1.0:
                raise ValueError(
                    'confidence threshold must be in [0, 1]'
                )

            self._min_confidence_by_source[source] = threshold

    def validate(
        self,
        command: NormalizedTaskCommand,
        *,
        now_ros_ns: int,
    ) -> ValidationResult:
        """
        Validate one command.

        The first failing check determines the returned reason code.
        """
        if type(now_ros_ns) is not int or now_ros_ns < 0:
            raise ValueError(
                'now_ros_ns must be a non-negative int'
            )

        if not isinstance(command.interface_version, str):
            return self._reject(CommandReason.FIELD_INVALID)

        if (
            command.interface_version
            != self._supported_interface_version
        ):
            return self._reject(
                CommandReason.INTERFACE_VERSION_UNSUPPORTED
            )

        if not isinstance(command.command_id, str):
            return self._reject(CommandReason.COMMAND_ID_INVALID)

        if command.command_id == '':
            return self._reject(CommandReason.COMMAND_ID_MISSING)

        if (
            len(command.command_id) > 128
            or _COMMAND_ID_PATTERN.fullmatch(
                command.command_id
            ) is None
        ):
            return self._reject(CommandReason.COMMAND_ID_INVALID)

        if type(command.source) is not int:
            return self._reject(CommandReason.FIELD_INVALID)

        if command.source == 4:
            return self._reject(
                CommandReason.INTERNAL_SOURCE_FORBIDDEN_ON_HMI_TOPIC
            )

        if command.source not in (
            CommandSource.VOICE,
            CommandSource.APP,
            CommandSource.MOCK,
        ):
            return self._reject(CommandReason.SOURCE_UNKNOWN)

        if type(command.task_id) is not int:
            return self._reject(CommandReason.FIELD_INVALID)

        if not 0 <= command.task_id <= 65535:
            return self._reject(CommandReason.FIELD_INVALID)

        if (
            type(command.stamp_ns) is not int
            or command.stamp_ns < 0
        ):
            return self._reject(CommandReason.TIMESTAMP_INVALID)

        if (
            type(command.valid_for_ns) is not int
            or command.valid_for_ns <= 0
        ):
            return self._reject(CommandReason.VALID_FOR_INVALID)

        if isinstance(command.confidence, bool) or not isinstance(
            command.confidence,
            (int, float),
        ):
            return self._reject(CommandReason.FIELD_INVALID)

        confidence = float(command.confidence)

        if not math.isfinite(confidence):
            return self._reject(CommandReason.NONFINITE_VALUE)

        if not 0.0 <= confidence <= 1.0:
            return self._reject(CommandReason.FIELD_INVALID)

        if not isinstance(command.raw_text, str):
            return self._reject(CommandReason.FIELD_INVALID)

        if len(command.raw_text) > 512:
            return self._reject(CommandReason.COMMAND_TOO_LARGE)

        if now_ros_ns > (
            command.stamp_ns + command.valid_for_ns
        ):
            return self._reject(CommandReason.COMMAND_EXPIRED)

        if self._catalog is None:
            return self._reject(
                CommandReason.TASK_CATALOG_UNAVAILABLE
            )

        task = self._catalog.get(command.task_id)

        if task is None:
            return self._reject(CommandReason.TASK_ID_UNKNOWN)

        if not task.enabled:
            return self._reject(CommandReason.TASK_DISABLED)

        if command.source not in task.allowed_sources:
            return self._reject(CommandReason.SOURCE_NOT_ALLOWED)

        threshold = self._min_confidence_by_source.get(
            command.source
        )

        if (
            threshold is not None
            and confidence < threshold
        ):
            return self._reject(CommandReason.CONFIDENCE_TOO_LOW)

        return ValidationResult(
            accepted=True,
            reason_code=CommandReason.COMMAND_VALID,
            task=task,
        )

    @staticmethod
    def _reject(
        reason_code: CommandReason,
    ) -> ValidationResult:
        return ValidationResult(
            accepted=False,
            reason_code=reason_code,
            task=None,
        )


class CommandDeduplicator:
    """
    Track command_id semantic fingerprints.

    This component deliberately does not cache TaskStatus yet. A later
    terminal/current-status cache will provide the payload used for REPLAY.
    """

    def __init__(self) -> None:
        self._fingerprints: dict[
            str,
            CommandSemanticFingerprint,
        ] = {}

    def check_and_record(
        self,
        command: NormalizedTaskCommand,
    ) -> DedupResult:
        """Return NEW, REPLAY or CONFLICT for a validated command."""
        fingerprint = semantic_fingerprint(command)
        existing = self._fingerprints.get(command.command_id)

        if existing is None:
            self._fingerprints[command.command_id] = fingerprint
            return DedupResult(DedupDecision.NEW)

        if existing == fingerprint:
            return DedupResult(DedupDecision.REPLAY)

        return DedupResult(
            DedupDecision.CONFLICT,
            CommandReason.DUPLICATE_COMMAND_CONFLICT,
        )

    def __len__(self) -> int:
        return len(self._fingerprints)
