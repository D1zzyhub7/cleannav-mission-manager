"""Unit tests for the Mission Manager Task Catalog loader."""

from pathlib import Path

import pytest
import yaml

from cleannav_interfaces.msg import TaskStatus

from cleannav_mission_manager.domain.command_processing import (
    CommandReason,
    CommandSource,
    NormalizedTaskCommand,
    TaskKind,
)
from cleannav_mission_manager.domain.task_catalog import (
    TaskCatalogLoadError,
    load_task_catalog,
)


REAL_CATALOG = (
    Path(__file__).resolve().parents[3]
    / 'cleannav_interfaces'
    / 'config'
    / 'task_catalog.yaml'
)


def _valid_document():
    return {
        'interface_version': '1.0',
        'ranges': {
            'system_command': [1, 9],
            'fixed_goal': [10, 19],
            'fixed_route': [20, 29],
            'visual_target': [30, 39],
            'reserved': [40, 99],
        },
        'tasks': [
            {
                'id': 30,
                'name': 'CLEAN_NEAREST_LEAF',
                'task_kind': 'MISSION',
                'enabled': True,
                'allowed_sources': [
                    'VOICE',
                    'APP',
                    'MOCK',
                ],
                'requires_confirmation': False,
                'description': 'test task',
            },
        ],
    }


def _write_document(tmp_path, document):
    path = tmp_path / 'task_catalog.yaml'
    path.write_text(
        yaml.safe_dump(
            document,
            sort_keys=False,
        ),
        encoding='utf-8',
    )
    return path


def _assert_invalid(path):
    with pytest.raises(TaskCatalogLoadError) as exc_info:
        load_task_catalog(path)

    assert exc_info.value.reason_code is (
        CommandReason.TASK_CATALOG_INVALID
    )


def test_catalog_reason_codes_match_generated_interface():
    assert int(CommandReason.TASK_CATALOG_UNAVAILABLE) == (
        TaskStatus.REASON_TASK_CATALOG_UNAVAILABLE
    )
    assert int(CommandReason.TASK_CATALOG_INVALID) == (
        TaskStatus.REASON_TASK_CATALOG_INVALID
    )


def test_real_catalog_loads():
    catalog = load_task_catalog(REAL_CATALOG)

    assert catalog.interface_version == '1.0'
    assert len(catalog.tasks) == 13

    leaf = catalog.tasks[30]

    assert leaf.task_kind is TaskKind.MISSION
    assert leaf.enabled
    assert leaf.allowed_sources == frozenset({
        int(CommandSource.VOICE),
        int(CommandSource.APP),
        int(CommandSource.MOCK),
    })


def test_real_catalog_reset_estop_contract():
    catalog = load_task_catalog(REAL_CATALOG)

    reset_estop = catalog.tasks[7]

    assert reset_estop.task_kind is TaskKind.CONTROL
    assert reset_estop.requires_confirmation
    assert int(CommandSource.VOICE) not in (
        reset_estop.allowed_sources
    )
    assert int(CommandSource.APP) in (
        reset_estop.allowed_sources
    )
    assert int(CommandSource.MOCK) in (
        reset_estop.allowed_sources
    )


def test_real_catalog_disabled_configuration_tasks_remain_disabled():
    catalog = load_task_catalog(REAL_CATALOG)

    assert not catalog.tasks[1].enabled
    assert not catalog.tasks[5].enabled
    assert not catalog.tasks[10].enabled
    assert not catalog.tasks[20].enabled


def test_loaded_catalog_can_create_validator():
    catalog = load_task_catalog(REAL_CATALOG)
    validator = catalog.make_validator()

    command = NormalizedTaskCommand(
        interface_version='1.0',
        command_id='app-001',
        source=int(CommandSource.APP),
        task_id=30,
        stamp_ns=1_000,
        valid_for_ns=1_000,
        confidence=1.0,
    )

    result = validator.validate(
        command,
        now_ros_ns=1_500,
    )

    assert result.accepted
    assert result.task is catalog.tasks[30]


def test_loaded_catalog_validator_rejects_disabled_task():
    catalog = load_task_catalog(REAL_CATALOG)
    validator = catalog.make_validator()

    command = NormalizedTaskCommand(
        interface_version='1.0',
        command_id='app-002',
        source=int(CommandSource.APP),
        task_id=5,
        stamp_ns=1_000,
        valid_for_ns=1_000,
    )

    result = validator.validate(
        command,
        now_ros_ns=1_500,
    )

    assert not result.accepted
    assert result.reason_code is CommandReason.TASK_DISABLED


def test_missing_catalog_is_unavailable(tmp_path):
    missing = tmp_path / 'missing.yaml'

    with pytest.raises(TaskCatalogLoadError) as exc_info:
        load_task_catalog(missing)

    assert exc_info.value.reason_code is (
        CommandReason.TASK_CATALOG_UNAVAILABLE
    )


def test_malformed_yaml_is_invalid(tmp_path):
    path = tmp_path / 'bad.yaml'
    path.write_text(
        'tasks: [\n',
        encoding='utf-8',
    )

    _assert_invalid(path)


@pytest.mark.parametrize(
    'document',
    [
        None,
        [],
        'not-a-mapping',
    ],
)
def test_root_must_be_mapping(tmp_path, document):
    path = _write_document(
        tmp_path,
        document,
    )

    _assert_invalid(path)


def test_interface_version_mismatch_is_invalid(tmp_path):
    document = _valid_document()
    document['interface_version'] = '1.1'

    _assert_invalid(
        _write_document(tmp_path, document)
    )


def test_missing_ranges_is_invalid(tmp_path):
    document = _valid_document()
    del document['ranges']

    _assert_invalid(
        _write_document(tmp_path, document)
    )


def test_changed_range_is_invalid(tmp_path):
    document = _valid_document()
    document['ranges']['visual_target'] = [30, 40]

    _assert_invalid(
        _write_document(tmp_path, document)
    )


def test_missing_tasks_is_invalid(tmp_path):
    document = _valid_document()
    del document['tasks']

    _assert_invalid(
        _write_document(tmp_path, document)
    )


def test_empty_tasks_is_invalid(tmp_path):
    document = _valid_document()
    document['tasks'] = []

    _assert_invalid(
        _write_document(tmp_path, document)
    )


def test_duplicate_task_id_is_invalid(tmp_path):
    document = _valid_document()
    document['tasks'].append(
        dict(document['tasks'][0])
    )

    _assert_invalid(
        _write_document(tmp_path, document)
    )


def test_missing_required_task_key_is_invalid(tmp_path):
    document = _valid_document()
    del document['tasks'][0]['allowed_sources']

    _assert_invalid(
        _write_document(tmp_path, document)
    )


@pytest.mark.parametrize(
    'task_id',
    [0, 100, True, '30'],
)
def test_invalid_task_id_is_rejected(tmp_path, task_id):
    document = _valid_document()
    document['tasks'][0]['id'] = task_id

    _assert_invalid(
        _write_document(tmp_path, document)
    )


@pytest.mark.parametrize(
    'task_kind',
    ['UNKNOWN', 1, None],
)
def test_invalid_task_kind_is_rejected(tmp_path, task_kind):
    document = _valid_document()
    document['tasks'][0]['task_kind'] = task_kind

    _assert_invalid(
        _write_document(tmp_path, document)
    )


@pytest.mark.parametrize(
    'enabled',
    [1, 0, 'true', None],
)
def test_enabled_must_be_bool(tmp_path, enabled):
    document = _valid_document()
    document['tasks'][0]['enabled'] = enabled

    _assert_invalid(
        _write_document(tmp_path, document)
    )


@pytest.mark.parametrize(
    'allowed_sources',
    [
        [],
        ['VOICE', 'VOICE'],
        ['INTERNAL'],
        ['UNKNOWN'],
        ['VOICE', 2],
        'VOICE',
    ],
)
def test_invalid_allowed_sources_are_rejected(
    tmp_path,
    allowed_sources,
):
    document = _valid_document()
    document['tasks'][0]['allowed_sources'] = allowed_sources

    _assert_invalid(
        _write_document(tmp_path, document)
    )


@pytest.mark.parametrize(
    'requires_confirmation',
    [0, 1, 'false', None],
)
def test_requires_confirmation_must_be_bool(
    tmp_path,
    requires_confirmation,
):
    document = _valid_document()
    document['tasks'][0][
        'requires_confirmation'
    ] = requires_confirmation

    _assert_invalid(
        _write_document(tmp_path, document)
    )


def test_task_name_must_not_be_empty(tmp_path):
    document = _valid_document()
    document['tasks'][0]['name'] = ''

    _assert_invalid(
        _write_document(tmp_path, document)
    )


def test_description_must_be_string(tmp_path):
    document = _valid_document()
    document['tasks'][0]['description'] = 123

    _assert_invalid(
        _write_document(tmp_path, document)
    )
