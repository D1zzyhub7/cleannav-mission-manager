"""Pure Mission Manager domain records and enumerations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Optional


class CommandRecordState(Enum):
    """Internal lifecycle of one received command."""

    RECEIVED = 'RECEIVED'
    ACCEPTED = 'ACCEPTED'
    REJECTED = 'REJECTED'
    QUEUED = 'QUEUED'
    APPLIED = 'APPLIED'
    TERMINAL = 'TERMINAL'


class ManagerMode(Enum):
    """Top-level Mission Manager operating mode."""

    NORMAL = 'NORMAL'
    EMERGENCY_LATCHED = 'EMERGENCY_LATCHED'


class InternalExecutionState(Enum):
    """Internal execution states not exposed directly to HMI."""

    IDLE = 'IDLE'
    WAITING_TARGET = 'WAITING_TARGET'
    PREPARING_GOAL = 'PREPARING_GOAL'
    NAVIGATION_STARTING = 'NAVIGATION_STARTING'
    LEASE_ACQUIRING = 'LEASE_ACQUIRING'
    EXECUTING = 'EXECUTING'
    CANCELING = 'CANCELING'
    FINALIZING = 'FINALIZING'
    PAUSED = 'PAUSED'
    SAFETY_BLOCKED = 'SAFETY_BLOCKED'


class CancelIntent(Enum):
    """Reason for canceling the active navigation generation."""

    PAUSE = 'PAUSE'
    STOP = 'STOP'
    RETURN_HOME = 'RETURN_HOME'
    SAFETY_BLOCK = 'SAFETY_BLOCK'
    ESTOP = 'ESTOP'
    REPLAN = 'REPLAN'


def _require_non_empty(value: str, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f'{name} must be str')

    if not value:
        raise ValueError(f'{name} must not be empty')

    return value


def _validate_task_id(value: int) -> int:
    if type(value) is not int:
        raise TypeError('task_id must be int')

    if not 1 <= value <= 65535:
        raise ValueError('task_id must be in [1, 65535]')

    return value


def _validate_source(value: int) -> int:
    if type(value) is not int:
        raise TypeError('source must be int')

    if not 0 <= value <= 255:
        raise ValueError('source must be in [0, 255]')

    return value


def _validate_reason_code(value: int) -> int:
    if type(value) is not int:
        raise TypeError('reason_code must be int')

    if not 0 <= value <= 2147483647:
        raise ValueError('reason_code must be a non-negative int32')

    return value


def _validate_ros_time(value: float) -> float:
    value = float(value)

    if not math.isfinite(value):
        raise ValueError('created_ros_time_s must be finite')

    if value < 0.0:
        raise ValueError('created_ros_time_s must be non-negative')

    return value


@dataclass
class CommandRecord:
    """
    Internal record keyed by command_id.

    This record intentionally contains only M0-frozen identity and lifecycle
    data. Deduplication fingerprints and terminal-cache policy belong to later
    domain components rather than this base record.
    """

    command_id: str
    task_id: int
    source: int
    state: CommandRecordState = CommandRecordState.RECEIVED
    execution_id: str = ''
    reason_code: int = 0
    created_ros_time_s: float = 0.0

    def __post_init__(self) -> None:
        self.command_id = _require_non_empty(
            self.command_id,
            'command_id',
        )
        self.task_id = _validate_task_id(self.task_id)
        self.source = _validate_source(self.source)
        self.reason_code = _validate_reason_code(self.reason_code)
        self.created_ros_time_s = _validate_ros_time(
            self.created_ros_time_s,
        )

        if not isinstance(self.execution_id, str):
            raise TypeError('execution_id must be str')

        if not isinstance(self.state, CommandRecordState):
            raise TypeError('state must be CommandRecordState')


@dataclass
class ExecutionRecord:
    """Internal record for one mission execution instance."""

    execution_id: str
    command_id: str
    task_id: int
    state: InternalExecutionState
    generation: int = 1
    cancel_intent: Optional[CancelIntent] = None
    active_target_id: str = ''

    def __post_init__(self) -> None:
        self.execution_id = _require_non_empty(
            self.execution_id,
            'execution_id',
        )
        self.command_id = _require_non_empty(
            self.command_id,
            'command_id',
        )
        self.task_id = _validate_task_id(self.task_id)

        if not isinstance(self.state, InternalExecutionState):
            raise TypeError(
                'state must be InternalExecutionState'
            )

        if type(self.generation) is not int:
            raise TypeError('generation must be int')

        if self.generation < 1:
            raise ValueError('generation must be >= 1')

        if (
            self.cancel_intent is not None
            and not isinstance(self.cancel_intent, CancelIntent)
        ):
            raise TypeError(
                'cancel_intent must be CancelIntent or None'
            )

        if not isinstance(self.active_target_id, str):
            raise TypeError('active_target_id must be str')
