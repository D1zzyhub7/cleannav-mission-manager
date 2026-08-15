"""Pure Task Catalog YAML loader for Mission Manager."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping, Union

import yaml

from cleannav_mission_manager.domain.command_processing import (
    CommandReason,
    CommandSource,
    CommandValidator,
    TaskCatalogEntry,
    TaskKind,
)


_EXPECTED_RANGES = {
    'system_command': (1, 9),
    'fixed_goal': (10, 19),
    'fixed_route': (20, 29),
    'visual_target': (30, 39),
    'reserved': (40, 99),
}

_SOURCE_NAME_TO_VALUE = {
    'VOICE': int(CommandSource.VOICE),
    'APP': int(CommandSource.APP),
    'MOCK': int(CommandSource.MOCK),
}

_REQUIRED_TASK_KEYS = {
    'id',
    'name',
    'task_kind',
    'enabled',
    'allowed_sources',
    'requires_confirmation',
    'description',
}


class TaskCatalogLoadError(RuntimeError):
    """Task Catalog load failure with a frozen Mission Manager reason."""

    def __init__(
        self,
        reason_code: CommandReason,
        message: str,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class LoadedTaskCatalog:
    """Validated v1.0 Task Catalog."""

    interface_version: str
    ranges: Mapping[str, tuple[int, int]]
    tasks: Mapping[int, TaskCatalogEntry]

    def make_validator(
        self,
        **kwargs,
    ) -> CommandValidator:
        """Create a CommandValidator using this validated catalog."""
        return CommandValidator(
            self.tasks,
            supported_interface_version=self.interface_version,
            **kwargs,
        )


def _raise_unavailable(message: str) -> None:
    raise TaskCatalogLoadError(
        CommandReason.TASK_CATALOG_UNAVAILABLE,
        message,
    )


def _raise_invalid(message: str) -> None:
    raise TaskCatalogLoadError(
        CommandReason.TASK_CATALOG_INVALID,
        message,
    )


def _validate_ranges(raw_ranges: object) -> Mapping[str, tuple[int, int]]:
    if not isinstance(raw_ranges, dict):
        _raise_invalid('ranges must be a mapping')

    if set(raw_ranges) != set(_EXPECTED_RANGES):
        _raise_invalid('ranges keys do not match v1.0 contract')

    parsed: dict[str, tuple[int, int]] = {}

    for name, expected in _EXPECTED_RANGES.items():
        value = raw_ranges[name]

        if not isinstance(value, list) or len(value) != 2:
            _raise_invalid(
                f'range {name} must contain exactly two integers'
            )

        start, end = value

        if (
            type(start) is not int
            or type(end) is not int
        ):
            _raise_invalid(
                f'range {name} bounds must be integers'
            )

        parsed_value = (start, end)

        if parsed_value != expected:
            _raise_invalid(
                f'range {name} must equal {expected}'
            )

        parsed[name] = parsed_value

    return MappingProxyType(parsed)


def _parse_task(raw_task: object) -> TaskCatalogEntry:
    if not isinstance(raw_task, dict):
        _raise_invalid('each task must be a mapping')

    missing = _REQUIRED_TASK_KEYS - set(raw_task)

    if missing:
        _raise_invalid(
            'task missing required keys: '
            + ', '.join(sorted(missing))
        )

    task_id = raw_task['id']

    if type(task_id) is not int:
        _raise_invalid('task id must be int')

    if not 1 <= task_id <= 99:
        _raise_invalid('task id must be in v1.0 range [1, 99]')

    name = raw_task['name']

    if not isinstance(name, str) or not name:
        _raise_invalid('task name must be a non-empty string')

    raw_kind = raw_task['task_kind']

    if not isinstance(raw_kind, str):
        _raise_invalid('task_kind must be a string')

    try:
        task_kind = TaskKind(raw_kind)
    except ValueError:
        _raise_invalid(
            f'unsupported task_kind: {raw_kind}'
        )

    enabled = raw_task['enabled']

    if type(enabled) is not bool:
        _raise_invalid('enabled must be bool')

    raw_sources = raw_task['allowed_sources']

    if not isinstance(raw_sources, list) or not raw_sources:
        _raise_invalid(
            'allowed_sources must be a non-empty list'
        )

    sources = []

    for source_name in raw_sources:
        if not isinstance(source_name, str):
            _raise_invalid(
                'allowed source names must be strings'
            )

        if source_name not in _SOURCE_NAME_TO_VALUE:
            _raise_invalid(
                f'unsupported allowed source: {source_name}'
            )

        sources.append(_SOURCE_NAME_TO_VALUE[source_name])

    if len(sources) != len(set(sources)):
        _raise_invalid(
            'allowed_sources must not contain duplicates'
        )

    requires_confirmation = raw_task['requires_confirmation']

    if type(requires_confirmation) is not bool:
        _raise_invalid(
            'requires_confirmation must be bool'
        )

    description = raw_task['description']

    if not isinstance(description, str):
        _raise_invalid('description must be a string')

    return TaskCatalogEntry(
        task_id=task_id,
        name=name,
        task_kind=task_kind,
        enabled=enabled,
        allowed_sources=frozenset(sources),
        requires_confirmation=requires_confirmation,
    )


def load_task_catalog(
    path: Union[str, Path],
    *,
    expected_interface_version: str = '1.0',
) -> LoadedTaskCatalog:
    """Load and validate one v1.0 Task Catalog YAML file."""
    catalog_path = Path(path)

    try:
        raw_text = catalog_path.read_text(encoding='utf-8')
    except OSError as exc:
        _raise_unavailable(
            f'cannot read Task Catalog: {exc}'
        )

    try:
        document = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        _raise_invalid(
            f'Task Catalog YAML parse failed: {exc}'
        )

    if not isinstance(document, dict):
        _raise_invalid(
            'Task Catalog root must be a mapping'
        )

    interface_version = document.get('interface_version')

    if interface_version != expected_interface_version:
        _raise_invalid(
            'Task Catalog interface_version mismatch'
        )

    ranges = _validate_ranges(document.get('ranges'))

    raw_tasks = document.get('tasks')

    if not isinstance(raw_tasks, list) or not raw_tasks:
        _raise_invalid(
            'tasks must be a non-empty list'
        )

    tasks: dict[int, TaskCatalogEntry] = {}

    for raw_task in raw_tasks:
        task = _parse_task(raw_task)

        if task.task_id in tasks:
            _raise_invalid(
                f'duplicate task id: {task.task_id}'
            )

        tasks[task.task_id] = task

    return LoadedTaskCatalog(
        interface_version=interface_version,
        ranges=ranges,
        tasks=MappingProxyType(tasks),
    )
