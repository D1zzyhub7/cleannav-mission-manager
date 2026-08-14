"""Unit tests for the Mission Manager FakeClock."""

import math

import pytest

from cleannav_mission_manager.domain.clock import FakeClock


def test_default_clock_starts_at_zero():
    clock = FakeClock()

    assert clock.now_ros() == 0.0
    assert clock.now_monotonic() == 0.0


def test_initial_values_are_independent():
    clock = FakeClock(
        _ros_time_s=10.0,
        _monotonic_time_s=20.0,
    )

    assert clock.now_ros() == 10.0
    assert clock.now_monotonic() == 20.0


def test_advance_ros_does_not_advance_monotonic():
    clock = FakeClock()

    clock.advance_ros(2.5)

    assert clock.now_ros() == 2.5
    assert clock.now_monotonic() == 0.0


def test_advance_monotonic_does_not_advance_ros():
    clock = FakeClock()

    clock.advance_monotonic(3.0)

    assert clock.now_ros() == 0.0
    assert clock.now_monotonic() == 3.0


def test_ros_time_can_be_set_backwards():
    clock = FakeClock(_ros_time_s=10.0)

    clock.set_ros(4.0)

    assert clock.now_ros() == 4.0


@pytest.mark.parametrize('value', [-1.0, math.inf, -math.inf, math.nan])
def test_invalid_initial_ros_time_is_rejected(value):
    with pytest.raises(ValueError):
        FakeClock(_ros_time_s=value)


@pytest.mark.parametrize('value', [-1.0, math.inf, -math.inf, math.nan])
def test_invalid_initial_monotonic_time_is_rejected(value):
    with pytest.raises(ValueError):
        FakeClock(_monotonic_time_s=value)


@pytest.mark.parametrize('value', [-1.0, math.inf, -math.inf, math.nan])
def test_invalid_ros_advance_is_rejected(value):
    clock = FakeClock()

    with pytest.raises(ValueError):
        clock.advance_ros(value)


@pytest.mark.parametrize('value', [-1.0, math.inf, -math.inf, math.nan])
def test_invalid_monotonic_advance_is_rejected(value):
    clock = FakeClock()

    with pytest.raises(ValueError):
        clock.advance_monotonic(value)


@pytest.mark.parametrize('value', [-1.0, math.inf, -math.inf, math.nan])
def test_invalid_ros_set_is_rejected(value):
    clock = FakeClock()

    with pytest.raises(ValueError):
        clock.set_ros(value)
