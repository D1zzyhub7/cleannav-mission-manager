"""Pure generation ownership and navigation callback classification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from cleannav_mission_manager.domain.command_processing import (
    CommandReason,
)


@dataclass(frozen=True)
class GenerationHandle:
    """Identify one execution generation."""

    execution_id: str
    generation: int

    def __post_init__(self) -> None:
        """Validate the immutable generation identity."""
        if type(self.execution_id) is not str:
            raise TypeError('execution_id must be str')
        if self.execution_id == '':
            raise ValueError('execution_id must not be empty')
        if type(self.generation) is not int:
            raise TypeError('generation must be int')
        if self.generation < 1:
            raise ValueError('generation must be at least 1')


@dataclass(frozen=True)
class GenerationRegisterResult:
    """Result of attempting to acquire generation ownership."""

    accepted: bool
    reason_code: CommandReason


@dataclass(frozen=True)
class GenerationRetireResult:
    """Result of attempting to release generation ownership."""

    retired: bool
    reason_code: CommandReason


class CallbackClassification(Enum):
    """Relationship between a callback and the active generation."""

    CURRENT = 'CURRENT'
    STALE = 'STALE'


@dataclass(frozen=True)
class GenerationCallbackResult:
    """Result of classifying a navigation callback."""

    classification: CallbackClassification
    reason_code: CommandReason


class GenerationGate:
    """Own at most one active generation and classify callbacks."""

    def __init__(self) -> None:
        """Create an inactive generation gate."""
        self._active_handle: Optional[GenerationHandle] = None

    @property
    def active_handle(self) -> Optional[GenerationHandle]:
        """Return the active generation handle, if any."""
        return self._active_handle

    def register(
        self,
        handle: GenerationHandle,
    ) -> GenerationRegisterResult:
        """Acquire ownership for ``handle`` when the gate is inactive."""
        self._validate_handle(handle)

        if self._active_handle is None:
            self._active_handle = handle
            return GenerationRegisterResult(
                accepted=True,
                reason_code=CommandReason.NONE,
            )

        if handle == self._active_handle:
            reason_code = CommandReason.NAV_GOAL_ALREADY_SUBMITTED
        elif handle.execution_id == self._active_handle.execution_id:
            reason_code = (
                CommandReason.NAV_CONCURRENT_GENERATION_FORBIDDEN
            )
        else:
            reason_code = CommandReason.ACTIVE_GENERATION_CONFLICT

        return GenerationRegisterResult(
            accepted=False,
            reason_code=reason_code,
        )

    def retire(
        self,
        handle: GenerationHandle,
    ) -> GenerationRetireResult:
        """Release ownership only when ``handle`` is exactly active."""
        self._validate_handle(handle)

        if handle == self._active_handle:
            self._active_handle = None
            return GenerationRetireResult(
                retired=True,
                reason_code=CommandReason.NONE,
            )

        return GenerationRetireResult(
            retired=False,
            reason_code=CommandReason.ACTIVE_GENERATION_CONFLICT,
        )

    def classify_callback(
        self,
        handle: GenerationHandle,
    ) -> GenerationCallbackResult:
        """Classify ``handle`` without changing generation ownership."""
        self._validate_handle(handle)

        if handle == self._active_handle:
            return GenerationCallbackResult(
                classification=CallbackClassification.CURRENT,
                reason_code=CommandReason.NONE,
            )

        return GenerationCallbackResult(
            classification=CallbackClassification.STALE,
            reason_code=CommandReason.NAV_STALE_CALLBACK_IGNORED,
        )

    @staticmethod
    def _validate_handle(handle: GenerationHandle) -> None:
        """Reject programmer errors at the public API boundary."""
        if not isinstance(handle, GenerationHandle):
            raise TypeError('handle must be GenerationHandle')
