"""Unit tests for TaskCommand validation and deduplication."""

import math

import pytest

from cleannav_interfaces.msg import TaskStatus

from cleannav_mission_manager.domain.command_processing import (
    CommandDeduplicator,
    CommandReason,
    CommandSource,
    CommandValidator,
    DedupDecision,
    NormalizedTaskCommand,
    TaskCatalogEntry,
    TaskKind,
    semantic_fingerprint,
)


def _catalog():
    return {
        2: TaskCatalogEntry(
            task_id=2,
            name='PAUSE_CURRENT_TASK',
            task_kind=TaskKind.CONTROL,
            enabled=True,
            allowed_sources=frozenset({1, 2, 3}),
        ),
        5: TaskCatalogEntry(
            task_id=5,
            name='RETURN_HOME',
            task_kind=TaskKind.CONTROL,
            enabled=False,
            allowed_sources=frozenset({1, 2, 3}),
        ),
        7: TaskCatalogEntry(
            task_id=7,
            name='RESET_SOFTWARE_EMERGENCY_STOP',
            task_kind=TaskKind.CONTROL,
            enabled=True,
            allowed_sources=frozenset({2, 3}),
            requires_confirmation=True,
        ),
        30: TaskCatalogEntry(
            task_id=30,
            name='CLEAN_NEAREST_LEAF',
            task_kind=TaskKind.MISSION,
            enabled=True,
            allowed_sources=frozenset({1, 2, 3}),
        ),
    }


def _command(**overrides):
    values = {
        'interface_version': '1.0',
        'command_id': 'app-001',
        'source': int(CommandSource.APP),
        'task_id': 30,
        'stamp_ns': 1_000_000_000,
        'valid_for_ns': 5_000_000_000,
        'confidence': 1.0,
        'raw_text': '',
    }
    values.update(overrides)
    return NormalizedTaskCommand(**values)


def _validator(**kwargs):
    return CommandValidator(_catalog(), **kwargs)


def test_reason_codes_match_generated_task_status():
    pairs = {
        CommandReason.COMMAND_VALID:
            TaskStatus.REASON_COMMAND_VALID,
        CommandReason.COMMAND_QUEUED:
            TaskStatus.REASON_COMMAND_QUEUED,
        CommandReason.QUEUED_COMMAND_EXPIRED:
            TaskStatus.REASON_QUEUED_COMMAND_EXPIRED,
        CommandReason.INTERFACE_VERSION_UNSUPPORTED:
            TaskStatus.REASON_INTERFACE_VERSION_UNSUPPORTED,
        CommandReason.COMMAND_ID_MISSING:
            TaskStatus.REASON_COMMAND_ID_MISSING,
        CommandReason.COMMAND_ID_INVALID:
            TaskStatus.REASON_COMMAND_ID_INVALID,
        CommandReason.DUPLICATE_COMMAND_CONFLICT:
            TaskStatus.REASON_DUPLICATE_COMMAND_CONFLICT,
        CommandReason.COMMAND_EXPIRED:
            TaskStatus.REASON_COMMAND_EXPIRED,
        CommandReason.VALID_FOR_INVALID:
            TaskStatus.REASON_VALID_FOR_INVALID,
        CommandReason.SOURCE_UNKNOWN:
            TaskStatus.REASON_SOURCE_UNKNOWN,
        CommandReason.SOURCE_NOT_ALLOWED:
            TaskStatus.REASON_SOURCE_NOT_ALLOWED,
        CommandReason.TASK_ID_UNKNOWN:
            TaskStatus.REASON_TASK_ID_UNKNOWN,
        CommandReason.TASK_DISABLED:
            TaskStatus.REASON_TASK_DISABLED,
        CommandReason.CONFIDENCE_TOO_LOW:
            TaskStatus.REASON_CONFIDENCE_TOO_LOW,
        CommandReason.FIELD_INVALID:
            TaskStatus.REASON_FIELD_INVALID,
        CommandReason.NONFINITE_VALUE:
            TaskStatus.REASON_NONFINITE_VALUE,
        CommandReason.INTERNAL_SOURCE_FORBIDDEN_ON_HMI_TOPIC:
            TaskStatus.REASON_INTERNAL_SOURCE_FORBIDDEN_ON_HMI_TOPIC,
        CommandReason.COMMAND_TOO_LARGE:
            TaskStatus.REASON_COMMAND_TOO_LARGE,
        CommandReason.TASK_CATALOG_UNAVAILABLE:
            TaskStatus.REASON_TASK_CATALOG_UNAVAILABLE,
        CommandReason.TASK_CATALOG_INVALID:
            TaskStatus.REASON_TASK_CATALOG_INVALID,
        CommandReason.TIMESTAMP_INVALID:
            TaskStatus.REASON_TIMESTAMP_INVALID,
    }

    for domain_reason, ros_reason in pairs.items():
        assert int(domain_reason) == ros_reason


def test_source_values_match_task_command_contract():
    assert int(CommandSource.UNKNOWN) == 0
    assert int(CommandSource.VOICE) == 1
    assert int(CommandSource.APP) == 2
    assert int(CommandSource.MOCK) == 3


def test_valid_command_is_accepted():
    result = _validator().validate(
        _command(),
        now_ros_ns=2_000_000_000,
    )

    assert result.accepted
    assert result.reason_code is CommandReason.COMMAND_VALID
    assert result.task is not None
    assert result.task.task_id == 30


def test_unsupported_interface_version_is_rejected():
    result = _validator().validate(
        _command(interface_version='1.1'),
        now_ros_ns=2_000_000_000,
    )

    assert not result.accepted
    assert result.reason_code is (
        CommandReason.INTERFACE_VERSION_UNSUPPORTED
    )


def test_missing_command_id_is_rejected():
    result = _validator().validate(
        _command(command_id=''),
        now_ros_ns=2_000_000_000,
    )

    assert result.reason_code is CommandReason.COMMAND_ID_MISSING


@pytest.mark.parametrize(
    'command_id',
    [
        'contains space',
        'contains/slash',
        'x' * 129,
    ],
)
def test_invalid_command_id_is_rejected(command_id):
    result = _validator().validate(
        _command(command_id=command_id),
        now_ros_ns=2_000_000_000,
    )

    assert result.reason_code is CommandReason.COMMAND_ID_INVALID


def test_all_frozen_command_id_characters_are_allowed():
    result = _validator().validate(
        _command(command_id='Az09._:-'),
        now_ros_ns=2_000_000_000,
    )

    assert result.accepted


def test_source_unknown_is_rejected():
    result = _validator().validate(
        _command(source=0),
        now_ros_ns=2_000_000_000,
    )

    assert result.reason_code is CommandReason.SOURCE_UNKNOWN


def test_internal_source_value_is_explicitly_forbidden():
    result = _validator().validate(
        _command(source=4),
        now_ros_ns=2_000_000_000,
    )

    assert result.reason_code is (
        CommandReason.INTERNAL_SOURCE_FORBIDDEN_ON_HMI_TOPIC
    )


def test_unknown_nonzero_source_is_rejected():
    result = _validator().validate(
        _command(source=99),
        now_ros_ns=2_000_000_000,
    )

    assert result.reason_code is CommandReason.SOURCE_UNKNOWN


def test_catalog_unavailable_uses_dedicated_reason():
    validator = CommandValidator(None)

    result = validator.validate(
        _command(),
        now_ros_ns=2_000_000_000,
    )

    assert not result.accepted
    assert result.reason_code is (
        CommandReason.TASK_CATALOG_UNAVAILABLE
    )


def test_unknown_task_is_rejected():
    result = _validator().validate(
        _command(task_id=99),
        now_ros_ns=2_000_000_000,
    )

    assert result.reason_code is CommandReason.TASK_ID_UNKNOWN


def test_disabled_task_is_rejected():
    result = _validator().validate(
        _command(task_id=5),
        now_ros_ns=2_000_000_000,
    )

    assert result.reason_code is CommandReason.TASK_DISABLED


def test_source_not_allowed_by_catalog_is_rejected():
    result = _validator().validate(
        _command(
            source=int(CommandSource.VOICE),
            task_id=7,
        ),
        now_ros_ns=2_000_000_000,
    )

    assert result.reason_code is CommandReason.SOURCE_NOT_ALLOWED


@pytest.mark.parametrize('valid_for_ns', [0, -1])
def test_invalid_valid_for_is_rejected(valid_for_ns):
    result = _validator().validate(
        _command(valid_for_ns=valid_for_ns),
        now_ros_ns=2_000_000_000,
    )

    assert result.reason_code is CommandReason.VALID_FOR_INVALID


def test_command_at_exact_expiry_boundary_is_still_valid():
    result = _validator().validate(
        _command(
            stamp_ns=1_000,
            valid_for_ns=500,
        ),
        now_ros_ns=1_500,
    )

    assert result.accepted


def test_command_after_expiry_boundary_is_rejected():
    result = _validator().validate(
        _command(
            stamp_ns=1_000,
            valid_for_ns=500,
        ),
        now_ros_ns=1_501,
    )

    assert result.reason_code is CommandReason.COMMAND_EXPIRED


@pytest.mark.parametrize(
    'confidence',
    [math.inf, -math.inf, math.nan],
)
def test_nonfinite_confidence_is_rejected(confidence):
    result = _validator().validate(
        _command(confidence=confidence),
        now_ros_ns=2_000_000_000,
    )

    assert result.reason_code is CommandReason.NONFINITE_VALUE


@pytest.mark.parametrize('confidence', [-0.1, 1.1])
def test_out_of_range_confidence_is_rejected(confidence):
    result = _validator().validate(
        _command(confidence=confidence),
        now_ros_ns=2_000_000_000,
    )

    assert result.reason_code is CommandReason.FIELD_INVALID


def test_configured_voice_confidence_threshold_is_applied():
    validator = _validator(
        min_confidence_by_source={
            int(CommandSource.VOICE): 0.80,
        }
    )

    result = validator.validate(
        _command(
            source=int(CommandSource.VOICE),
            confidence=0.79,
        ),
        now_ros_ns=2_000_000_000,
    )

    assert result.reason_code is CommandReason.CONFIDENCE_TOO_LOW


def test_confidence_threshold_is_not_hardcoded_for_app():
    validator = _validator(
        min_confidence_by_source={
            int(CommandSource.VOICE): 0.80,
        }
    )

    result = validator.validate(
        _command(
            source=int(CommandSource.APP),
            confidence=0.10,
        ),
        now_ros_ns=2_000_000_000,
    )

    assert result.accepted


def test_raw_text_bound_is_enforced():
    result = _validator().validate(
        _command(raw_text='x' * 513),
        now_ros_ns=2_000_000_000,
    )

    assert result.reason_code is CommandReason.COMMAND_TOO_LARGE


def test_fingerprint_uses_only_frozen_semantic_fields():
    first = _command(
        stamp_ns=100,
        confidence=0.2,
        raw_text='first',
    )
    second = _command(
        stamp_ns=999,
        confidence=0.9,
        raw_text='retry metadata changed',
    )

    assert semantic_fingerprint(first) == semantic_fingerprint(second)


def test_first_command_id_is_new():
    dedup = CommandDeduplicator()

    result = dedup.check_and_record(_command())

    assert result.decision is DedupDecision.NEW
    assert result.reason_code is CommandReason.NONE
    assert len(dedup) == 1


def test_same_command_id_and_same_semantics_is_replay():
    dedup = CommandDeduplicator()
    dedup.check_and_record(_command())

    result = dedup.check_and_record(
        _command(
            stamp_ns=9_000_000_000,
            confidence=0.25,
            raw_text='metadata changed',
        )
    )

    assert result.decision is DedupDecision.REPLAY
    assert result.reason_code is CommandReason.NONE
    assert len(dedup) == 1


@pytest.mark.parametrize(
    'changed',
    [
        {'interface_version': '1.1'},
        {'source': int(CommandSource.VOICE)},
        {'task_id': 2},
        {'valid_for_ns': 6_000_000_000},
    ],
)
def test_same_command_id_with_changed_semantics_is_conflict(
    changed,
):
    dedup = CommandDeduplicator()
    dedup.check_and_record(_command())

    result = dedup.check_and_record(_command(**changed))

    assert result.decision is DedupDecision.CONFLICT
    assert result.reason_code is (
        CommandReason.DUPLICATE_COMMAND_CONFLICT
    )
    assert len(dedup) == 1


def test_conflict_does_not_replace_original_fingerprint():
    dedup = CommandDeduplicator()
    original = _command()

    dedup.check_and_record(original)
    dedup.check_and_record(
        _command(task_id=2)
    )

    result = dedup.check_and_record(original)

    assert result.decision is DedupDecision.REPLAY


def test_different_command_id_same_semantics_is_new():
    dedup = CommandDeduplicator()

    first = dedup.check_and_record(_command(command_id='cmd-1'))
    second = dedup.check_and_record(_command(command_id='cmd-2'))

    assert first.decision is DedupDecision.NEW
    assert second.decision is DedupDecision.NEW
    assert len(dedup) == 2


def test_invalid_now_ros_ns_is_programmer_error():
    with pytest.raises(ValueError):
        _validator().validate(
            _command(),
            now_ros_ns=-1,
        )


@pytest.mark.parametrize(
    'threshold',
    [-0.1, 1.1, math.inf, math.nan],
)
def test_invalid_configured_threshold_is_rejected(threshold):
    with pytest.raises(ValueError):
        _validator(
            min_confidence_by_source={
                int(CommandSource.VOICE): threshold,
            }
        )
