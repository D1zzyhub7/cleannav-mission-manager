"""Unit tests for generation ownership and callback classification."""

from dataclasses import FrozenInstanceError, fields

import pytest

from cleannav_mission_manager.domain.command_processing import (
    CommandReason,
)
from cleannav_mission_manager.domain.generation_gate import (
    CallbackClassification,
    GenerationCallbackResult,
    GenerationGate,
    GenerationHandle,
    GenerationRegisterResult,
    GenerationRetireResult,
)


def _handle(execution_id='exec-1', generation=1):
    return GenerationHandle(
        execution_id=execution_id,
        generation=generation,
    )


def test_valid_generation_handle_has_only_contract_fields():
    handle = _handle()

    assert handle.execution_id == 'exec-1'
    assert handle.generation == 1
    assert [field.name for field in fields(handle)] == [
        'execution_id',
        'generation',
    ]


@pytest.mark.parametrize('execution_id', [None, 1, True])
def test_generation_handle_rejects_non_string_execution_id(execution_id):
    with pytest.raises(TypeError):
        _handle(execution_id=execution_id)


def test_generation_handle_rejects_empty_execution_id():
    with pytest.raises(ValueError):
        _handle(execution_id='')


@pytest.mark.parametrize('generation', [0, -1])
def test_generation_handle_rejects_non_positive_generation(generation):
    with pytest.raises(ValueError):
        _handle(generation=generation)


@pytest.mark.parametrize('generation', [True, 1.0, '1', None])
def test_generation_handle_rejects_non_int_generation(generation):
    with pytest.raises(TypeError):
        _handle(generation=generation)


def test_generation_handle_is_immutable():
    handle = _handle()

    with pytest.raises(FrozenInstanceError):
        handle.generation = 2


def test_first_register_is_accepted():
    gate = GenerationGate()
    handle = _handle()

    result = gate.register(handle)

    assert result == GenerationRegisterResult(
        accepted=True,
        reason_code=CommandReason.NONE,
    )
    assert gate.active_handle is handle


def test_duplicate_register_is_rejected_without_state_change():
    gate = GenerationGate()
    handle = _handle()
    gate.register(handle)

    result = gate.register(handle)

    assert not result.accepted
    assert result.reason_code is CommandReason.NAV_GOAL_ALREADY_SUBMITTED
    assert gate.active_handle is handle


def test_next_generation_before_retire_is_rejected():
    gate = GenerationGate()
    active = _handle()
    gate.register(active)

    result = gate.register(_handle(generation=2))

    assert not result.accepted
    assert result.reason_code is (
        CommandReason.NAV_CONCURRENT_GENERATION_FORBIDDEN
    )
    assert gate.active_handle is active


def test_different_execution_while_active_is_rejected():
    gate = GenerationGate()
    active = _handle()
    gate.register(active)

    result = gate.register(_handle(execution_id='exec-2'))

    assert not result.accepted
    assert result.reason_code is CommandReason.ACTIVE_GENERATION_CONFLICT
    assert gate.active_handle is active


def test_retire_current_generation_succeeds():
    gate = GenerationGate()
    handle = _handle()
    gate.register(handle)

    result = gate.retire(handle)

    assert result == GenerationRetireResult(
        retired=True,
        reason_code=CommandReason.NONE,
    )
    assert gate.active_handle is None


def test_retire_wrong_generation_does_not_clear_active():
    gate = GenerationGate()
    active = _handle(generation=2)
    gate.register(active)

    result = gate.retire(_handle(generation=1))

    assert not result.retired
    assert result.reason_code is CommandReason.ACTIVE_GENERATION_CONFLICT
    assert gate.active_handle is active


def test_retire_wrong_execution_does_not_clear_active():
    gate = GenerationGate()
    active = _handle(execution_id='exec-2')
    gate.register(active)

    result = gate.retire(_handle(execution_id='exec-1'))

    assert not result.retired
    assert result.reason_code is CommandReason.ACTIVE_GENERATION_CONFLICT
    assert gate.active_handle is active


def test_retire_inactive_gate_is_structured_rejection():
    result = GenerationGate().retire(_handle())

    assert not result.retired
    assert result.reason_code is CommandReason.ACTIVE_GENERATION_CONFLICT


def test_next_generation_can_register_after_retire():
    gate = GenerationGate()
    first = _handle()
    gate.register(first)
    gate.retire(first)

    result = gate.register(_handle(generation=2))

    assert result.accepted
    assert gate.active_handle == _handle(generation=2)


def test_different_execution_can_register_after_retire():
    gate = GenerationGate()
    first = _handle()
    gate.register(first)
    gate.retire(first)

    result = gate.register(_handle(execution_id='exec-2'))

    assert result.accepted
    assert gate.active_handle == _handle(execution_id='exec-2')


def test_current_callback_is_current():
    gate = GenerationGate()
    handle = _handle()
    gate.register(handle)

    result = gate.classify_callback(handle)

    assert result == GenerationCallbackResult(
        classification=CallbackClassification.CURRENT,
        reason_code=CommandReason.NONE,
    )


@pytest.mark.parametrize(
    'callback_handle',
    [
        _handle(generation=1),
        _handle(execution_id='exec-1', generation=3),
        _handle(execution_id='exec-2', generation=2),
    ],
)
def test_non_current_callback_is_stale(callback_handle):
    gate = GenerationGate()
    gate.register(_handle(generation=2))

    result = gate.classify_callback(callback_handle)

    assert result.classification is CallbackClassification.STALE
    assert result.reason_code is CommandReason.NAV_STALE_CALLBACK_IGNORED


def test_callback_after_retire_is_stale():
    gate = GenerationGate()
    handle = _handle()
    gate.register(handle)
    gate.retire(handle)

    result = gate.classify_callback(handle)

    assert result.classification is CallbackClassification.STALE
    assert result.reason_code is CommandReason.NAV_STALE_CALLBACK_IGNORED


def test_callback_when_inactive_is_stale():
    result = GenerationGate().classify_callback(_handle())

    assert result.classification is CallbackClassification.STALE
    assert result.reason_code is CommandReason.NAV_STALE_CALLBACK_IGNORED


def test_duplicate_current_callback_remains_current():
    gate = GenerationGate()
    handle = _handle()
    gate.register(handle)

    first = gate.classify_callback(handle)
    second = gate.classify_callback(handle)

    assert first.classification is CallbackClassification.CURRENT
    assert second.classification is CallbackClassification.CURRENT


def test_callback_classification_does_not_mutate_gate_state():
    gate = GenerationGate()
    active = _handle(generation=2)
    gate.register(active)

    gate.classify_callback(active)
    gate.classify_callback(_handle(generation=1))

    assert gate.active_handle is active


@pytest.mark.parametrize(
    'method_name',
    ['register', 'retire', 'classify_callback'],
)
@pytest.mark.parametrize('invalid_handle', [None, ('exec-1', 1), 'exec-1'])
def test_public_apis_reject_non_generation_handle(
    method_name,
    invalid_handle,
):
    gate = GenerationGate()

    with pytest.raises(TypeError):
        getattr(gate, method_name)(invalid_handle)


def test_generation_reason_values_are_exact():
    assert int(CommandReason.NAV_STALE_CALLBACK_IGNORED) == 414
    assert int(CommandReason.NAV_GOAL_ALREADY_SUBMITTED) == 416
    assert int(CommandReason.NAV_CONCURRENT_GENERATION_FORBIDDEN) == 417
    assert int(CommandReason.ACTIVE_GENERATION_CONFLICT) == 707


def test_result_types_are_immutable():
    result = GenerationRegisterResult(True, CommandReason.NONE)

    with pytest.raises(FrozenInstanceError):
        result.accepted = False


def test_generation_gate_has_no_generation_counter():
    gate = GenerationGate()

    assert gate.__dict__ == {'_active_handle': None}
    first = _handle(generation=7)
    gate.register(first)
    gate.retire(first)
    gate.register(_handle(generation=3))
    assert gate.__dict__ == {
        '_active_handle': _handle(generation=3),
    }
