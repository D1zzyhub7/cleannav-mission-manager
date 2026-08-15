"""Pure FIFO Mission Queue for validated MISSION commands."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional

from cleannav_mission_manager.domain.command_processing import (
    CommandReason,
    NormalizedTaskCommand,
    TaskCatalogEntry,
    TaskKind,
)


@dataclass(frozen=True)
class QueuedMission:
    """One validated ordinary mission waiting for activation."""

    command: NormalizedTaskCommand
    task: TaskCatalogEntry

    @property
    def expires_at_ns(self) -> int:
        """Return ROS semantic expiry time."""
        return self.command.stamp_ns + self.command.valid_for_ns


@dataclass(frozen=True)
class EnqueueResult:
    """Result of attempting to append one mission."""

    accepted: bool
    reason_code: CommandReason
    item: Optional[QueuedMission] = None


@dataclass(frozen=True)
class QueueExpiry:
    """One queued command removed because it expired before activation."""

    item: QueuedMission
    reason_code: CommandReason = (
        CommandReason.QUEUED_COMMAND_EXPIRED
    )


@dataclass(frozen=True)
class QueuePopResult:
    """
    Result of requesting the next mission.

    Expired queue items are returned separately so the state-machine layer can
    later publish command-scope CANCELED statuses without creating executions.
    """

    item: Optional[QueuedMission]
    expired: tuple[QueueExpiry, ...]


class MissionQueue:
    """
    Bounded FIFO queue for ordinary MISSION commands.

    CONTROL commands are programmer-routing errors and must never enter this
    queue. Queue capacity is injected instead of hardcoded because M0 leaves
    the deployment-specific maximum as a parameter.
    """

    def __init__(self, max_size: int) -> None:
        if type(max_size) is not int:
            raise TypeError('max_size must be int')

        if max_size <= 0:
            raise ValueError('max_size must be > 0')

        self._max_size = max_size
        self._items: Deque[QueuedMission] = deque()

    @property
    def max_size(self) -> int:
        """Return configured queue capacity."""
        return self._max_size

    @property
    def is_full(self) -> bool:
        """Return whether no additional mission may be enqueued."""
        return len(self._items) >= self._max_size

    def enqueue(
        self,
        command: NormalizedTaskCommand,
        task: TaskCatalogEntry,
    ) -> EnqueueResult:
        """Append one validated MISSION command in FIFO order."""
        if not isinstance(command, NormalizedTaskCommand):
            raise TypeError(
                'command must be NormalizedTaskCommand'
            )

        if not isinstance(task, TaskCatalogEntry):
            raise TypeError(
                'task must be TaskCatalogEntry'
            )

        if task.task_kind is not TaskKind.MISSION:
            raise ValueError(
                'CONTROL commands must not enter MissionQueue'
            )

        if command.task_id != task.task_id:
            raise ValueError(
                'command task_id does not match catalog entry'
            )

        if self.is_full:
            return EnqueueResult(
                accepted=False,
                reason_code=CommandReason.QUEUE_FULL,
            )

        item = QueuedMission(
            command=command,
            task=task,
        )
        self._items.append(item)

        return EnqueueResult(
            accepted=True,
            reason_code=CommandReason.COMMAND_QUEUED,
            item=item,
        )

    def expire(
        self,
        *,
        now_ros_ns: int,
    ) -> tuple[QueueExpiry, ...]:
        """
        Remove all missions expired before activation.

        Exact expiry boundary remains valid. An item expires only when
        now_ros_ns is strictly greater than stamp_ns + valid_for_ns.
        """
        self._validate_now(now_ros_ns)

        if not self._items:
            return ()

        kept: Deque[QueuedMission] = deque()
        expired = []

        while self._items:
            item = self._items.popleft()

            if now_ros_ns > item.expires_at_ns:
                expired.append(QueueExpiry(item=item))
            else:
                kept.append(item)

        self._items = kept

        return tuple(expired)

    def pop_next(
        self,
        *,
        now_ros_ns: int,
    ) -> QueuePopResult:
        """
        Return the next valid FIFO mission and all expired queue items.

        Expiry cleanup is performed before activation so an expired command can
        never create an execution.
        """
        expired = self.expire(
            now_ros_ns=now_ros_ns,
        )

        item = (
            self._items.popleft()
            if self._items
            else None
        )

        return QueuePopResult(
            item=item,
            expired=expired,
        )

    def peek(self) -> Optional[QueuedMission]:
        """Return the FIFO head without removing it."""
        if not self._items:
            return None

        return self._items[0]

    def clear(self) -> tuple[QueuedMission, ...]:
        """Clear the queue and return removed items in FIFO order."""
        items = tuple(self._items)
        self._items.clear()
        return items

    def snapshot(self) -> tuple[QueuedMission, ...]:
        """Return an immutable FIFO snapshot."""
        return tuple(self._items)

    def __len__(self) -> int:
        return len(self._items)

    @staticmethod
    def _validate_now(now_ros_ns: int) -> None:
        if type(now_ros_ns) is not int:
            raise TypeError(
                'now_ros_ns must be int'
            )

        if now_ros_ns < 0:
            raise ValueError(
                'now_ros_ns must be non-negative'
            )
