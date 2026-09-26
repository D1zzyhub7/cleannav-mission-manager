"""SW-PER-1 tests for target contracts, selection and waiting execution."""

from pathlib import Path

from ament_index_python.packages import get_package_share_directory
from geometry_msgs.msg import PoseStamped
import pytest
import yaml

from cleannav_interfaces.msg import CleaningTarget, CleaningTargetArray

from cleannav_mission_manager.adapters.mock_navigation import (
    MockNavigationAdapter,
)
from cleannav_mission_manager.adapters.mock_safety import MockSafetyAdapter
from cleannav_mission_manager.adapters.navigation import (
    NavigationEvent,
    NavigationEventType,
)
from cleannav_mission_manager.core import MissionManagerCore
from cleannav_mission_manager.domain.clock import FakeClock
from cleannav_mission_manager.domain.command_processing import (
    CommandReason,
    CommandSource,
    CommandValidator,
    NormalizedTaskCommand,
    TaskCatalogEntry,
    TaskKind,
)
from cleannav_mission_manager.domain.command_store import (
    CommandRecordStore,
    ExternalTaskState,
    TerminalStatusCache,
)
from cleannav_mission_manager.domain.execution_store import (
    ExecutionRecordStore,
)
from cleannav_mission_manager.domain.generation_gate import GenerationGate
from cleannav_mission_manager.domain.goal_resolution import (
    PendingGoal,
    ResolvedGoal,
)
from cleannav_mission_manager.domain.mission_queue import MissionQueue
from cleannav_mission_manager.domain.models import InternalExecutionState
from cleannav_mission_manager.domain.target_models import (
    ObservationState,
    RobotPoseSnapshot,
    TargetObservation,
    TargetSelectionRule,
    TargetType,
    VisualTargetPolicy,
)
from cleannav_mission_manager.domain.target_registry import TargetRegistry
from cleannav_mission_manager.domain.target_selector import (
    TargetSelector,
    UnsupportedTargetSelectionError,
)
from cleannav_mission_manager.domain.task_catalog import (
    TaskCatalogLoadError,
    load_task_catalog,
)
from cleannav_mission_manager.target_ros_conversion import (
    TargetRosConversionError,
    cleaning_target_to_observation,
    ingest_cleaning_target_array,
)
from cleannav_mission_manager.visual_target_goal_resolver import (
    VisualTargetGoalResolver,
)


REAL_CATALOG = (
    Path(get_package_share_directory('cleannav_interfaces'))
    / 'config'
    / 'task_catalog.yaml'
)


def _observation(
    target_id='target-a',
    *,
    target_type=TargetType.LEAF,
    stamp_ns=1_000,
    valid_for_ns=1_000,
    x=1.0,
    y=0.0,
    state=ObservationState.CONFIRMED,
    projection_valid=True,
):
    return TargetObservation(
        target_id=target_id,
        source='camera-1',
        target_type=target_type,
        stamp_ns=stamp_ns,
        valid_for_ns=valid_for_ns,
        confidence=0.8,
        projection_valid=projection_valid,
        centroid_x=x,
        centroid_y=y,
        centroid_z=0.0,
        position_uncertainty_m=0.1,
        observation_state=state,
    )


def _pose(x=0.0, y=0.0):
    return RobotPoseSnapshot(
        x=x,
        y=y,
        z=0.0,
        orientation_x=0.0,
        orientation_y=0.0,
        orientation_z=0.25,
        orientation_w=0.9682458,
    )


def _policy(target_type=TargetType.LEAF):
    return VisualTargetPolicy(
        target_type=target_type,
        selection_rule=TargetSelectionRule.NEAREST_VALID,
        wait_timeout_sec=10.0,
        completion_radius_m=0.35,
    )


def _command(command_id='visual-1', task_id=30):
    return NormalizedTaskCommand(
        interface_version='1.0',
        command_id=command_id,
        source=int(CommandSource.APP),
        task_id=task_id,
        stamp_ns=1_000_000_000,
        valid_for_ns=100_000_000_000,
        confidence=1.0,
    )


def _visual_catalog():
    return {
        2: TaskCatalogEntry(
            task_id=2,
            name='PAUSE_CURRENT_TASK',
            task_kind=TaskKind.CONTROL,
            enabled=True,
            allowed_sources=frozenset({int(CommandSource.APP)}),
        ),
        3: TaskCatalogEntry(
            task_id=3,
            name='RESUME_CURRENT_TASK',
            task_kind=TaskKind.CONTROL,
            enabled=True,
            allowed_sources=frozenset({int(CommandSource.APP)}),
        ),
        4: TaskCatalogEntry(
            task_id=4,
            name='STOP_CURRENT_TASK',
            task_kind=TaskKind.CONTROL,
            enabled=True,
            allowed_sources=frozenset({int(CommandSource.APP)}),
        ),
        6: TaskCatalogEntry(
            task_id=6,
            name='SOFTWARE_EMERGENCY_STOP',
            task_kind=TaskKind.CONTROL,
            enabled=True,
            allowed_sources=frozenset({int(CommandSource.APP)}),
        ),
        30: TaskCatalogEntry(
            task_id=30,
            name='CLEAN_NEAREST_LEAF',
            task_kind=TaskKind.MISSION,
            enabled=True,
            allowed_sources=frozenset({int(CommandSource.APP)}),
            visual_target_policy=_policy(),
        ),
    }


class _Ids:
    def __init__(self):
        self.value = 0

    def __call__(self):
        self.value += 1
        return f'execution-{self.value}'


def _build_core(resolver, clock=None):
    holder = {}
    navigation = MockNavigationAdapter(
        lambda event: holder['core'].handle_navigation_event(event)
    )
    safety = MockSafetyAdapter(
        lambda event: holder['core'].handle_safety_event(event)
    )
    core = MissionManagerCore(
        validator=CommandValidator(_visual_catalog()),
        command_store=CommandRecordStore(32),
        mission_queue=MissionQueue(4),
        execution_store=ExecutionRecordStore(
            id_factory=_Ids(),
            terminal_cache=TerminalStatusCache(32),
        ),
        generation_gate=GenerationGate(),
        navigation=navigation,
        safety=safety,
        clock=clock or FakeClock(_ros_time_s=2.0),
        goal_resolver=resolver,
    )
    holder['core'] = core
    return core, navigation, safety


def _target_message(
    *,
    target_id='target-a',
    stamp_sec=3,
    valid_for_sec=7,
    frame_id='map',
    interface_version='1.0',
):
    message = CleaningTarget()
    message.header.frame_id = frame_id
    message.header.stamp.sec = stamp_sec
    message.header.stamp.nanosec = 123
    message.interface_version = interface_version
    message.target_id = target_id
    message.source = 'camera-1'
    message.target_type = CleaningTarget.TYPE_LEAF
    message.confidence = 0.9
    message.projection_valid = True
    message.centroid.x = 1.0
    message.centroid.y = 2.0
    message.centroid.z = 0.0
    message.position_uncertainty_m = 0.1
    message.observation_state = CleaningTarget.OBSERVATION_CONFIRMED
    message.valid_for.sec = valid_for_sec
    return message


def test_real_catalog_visual_policy_and_disabled_priority_task():
    catalog = load_task_catalog(REAL_CATALOG)

    assert catalog.tasks[30].visual_target_policy.target_type is (
        TargetType.LEAF
    )
    assert catalog.tasks[30].visual_target_policy.selection_rule is (
        TargetSelectionRule.NEAREST_VALID
    )
    assert catalog.tasks[31].visual_target_policy.selection_rule is (
        TargetSelectionRule.NEAREST_VALID
    )
    assert catalog.tasks[32].visual_target_policy.target_type is (
        TargetType.PUDDLE
    )
    assert not catalog.tasks[33].enabled


def test_enabled_visual_task_with_missing_policy_is_rejected(tmp_path):
    document = {
        'interface_version': '1.0',
        'ranges': {
            'system_command': [1, 9],
            'fixed_goal': [10, 19],
            'fixed_route': [20, 29],
            'visual_target': [30, 39],
            'reserved': [40, 99],
        },
        'tasks': [{
            'id': 30,
            'name': 'VISUAL',
            'task_kind': 'MISSION',
            'enabled': True,
            'allowed_sources': ['APP'],
            'requires_confirmation': False,
            'description': 'missing policy',
        }],
    }
    path = tmp_path / 'catalog.yaml'
    path.write_text(yaml.safe_dump(document), encoding='utf-8')

    with pytest.raises(TaskCatalogLoadError):
        load_task_catalog(path)


def test_registry_stores_valid_target_and_empty_batch_does_not_clear():
    registry = TargetRegistry()
    observation = _observation()

    assert registry.update(observation)
    assert registry.get('target-a') == observation
    assert registry.update_many([]) == 0
    assert registry.get('target-a') == observation


def test_registry_expired_projection_invalid_and_lost_are_unavailable():
    registry = TargetRegistry()
    registry.update(_observation(stamp_ns=10, valid_for_ns=5))
    registry.update(
        _observation(
            'target-b',
            projection_valid=False,
        )
    )
    registry.update(
        _observation(
            'target-c',
            state=ObservationState.LOST,
        )
    )

    assert registry.available(15) == ()
    assert registry.get('target-c') is None


def test_registry_older_timestamp_cannot_overwrite_newer_snapshot():
    registry = TargetRegistry()
    newer = _observation(stamp_ns=20, x=2.0)
    older = _observation(stamp_ns=19, x=9.0)

    assert registry.update(newer)
    assert not registry.update(older)
    assert registry.get('target-a').centroid_x == 2.0


def test_nonfinite_target_geometry_is_rejected():
    with pytest.raises(ValueError):
        _observation(x=float('nan'))


def test_selector_picks_nearest_matching_target_and_filters_type():
    registry = TargetRegistry()
    registry.update(_observation('far', x=5.0))
    registry.update(_observation('near', x=1.0))
    registry.update(
        _observation(
            'puddle',
            target_type=TargetType.PUDDLE,
            x=0.1,
        )
    )
    selector = TargetSelector(registry)

    selected = selector.select(_policy(), _pose(), 1_100)

    assert selected.target_id == 'near'


def test_selector_puddle_and_deterministic_tie_break():
    registry = TargetRegistry()
    registry.update(
        _observation(
            'z-target',
            target_type=TargetType.PUDDLE,
            x=1.0,
        )
    )
    registry.update(
        _observation(
            'a-target',
            target_type=TargetType.PUDDLE,
            x=-1.0,
        )
    )
    selector = TargetSelector(registry)

    selected = selector.select(
        _policy(TargetType.PUDDLE),
        _pose(),
        1_100,
    )

    assert selected.target_id == 'a-target'


def test_selector_expiry_pose_failure_and_priority_gap():
    registry = TargetRegistry()
    registry.update(_observation(stamp_ns=10, valid_for_ns=5))
    selector = TargetSelector(registry)

    assert selector.select(_policy(), _pose(), 15) is None
    assert selector.select(_policy(), None, 11) is None
    with pytest.raises(UnsupportedTargetSelectionError):
        selector.select(
            VisualTargetPolicy(
                target_type=TargetType.ANY,
                selection_rule=(
                    TargetSelectionRule.HIGHEST_PRIORITY_THEN_NEAREST
                ),
                wait_timeout_sec=1.0,
                completion_radius_m=0.2,
            ),
            _pose(),
            11,
        )


def test_ros_conversion_preserves_exact_times_and_requires_map():
    message = _target_message()
    observation = cleaning_target_to_observation(message)

    assert observation.stamp_ns == 3_000_000_123
    assert observation.valid_for_ns == 7_000_000_000

    message.header.frame_id = 'odom'
    with pytest.raises(TargetRosConversionError):
        cleaning_target_to_observation(message)


def test_ros_conversion_rejects_bad_interface_and_ingests_empty_array():
    message = _target_message(interface_version='0.9')
    with pytest.raises(TargetRosConversionError):
        cleaning_target_to_observation(message)

    array = CleaningTargetArray()
    array.header.frame_id = 'map'
    array.interface_version = '1.0'
    registry = TargetRegistry()
    registry.update(_observation())
    assert ingest_cleaning_target_array(array, registry) == ()
    assert registry.get('target-a') is not None


def test_visual_resolver_returns_pending_then_pose_with_current_orientation():
    registry = TargetRegistry()
    selector = TargetSelector(registry)
    pose = _pose()
    resolver = VisualTargetGoalResolver(
        registry=registry,
        selector=selector,
        robot_pose_provider=lambda: pose,
        now_ros_ns=lambda: 1_100,
    )
    task = _visual_catalog()[30]

    pending = resolver(_command(), task)
    assert isinstance(pending, PendingGoal)
    assert pending.wait_timeout_ns == 10_000_000_000

    registry.update(_observation(x=2.0, y=3.0))
    resolved = resolver(_command(), task)
    assert isinstance(resolved, ResolvedGoal)
    assert resolved.active_target_id == 'target-a'
    assert isinstance(resolved.payload, PoseStamped)
    assert resolved.payload.header.frame_id == 'map'
    assert resolved.payload.pose.position.x == 2.0
    assert resolved.payload.pose.position.y == 3.0
    assert resolved.payload.pose.orientation.z == pose.orientation_z
    assert resolved.payload.pose.orientation.w == pose.orientation_w


def test_core_visual_mission_waits_without_navigation_or_safety():
    calls = []

    def resolver(command, task):
        calls.append(command.command_id)
        return PendingGoal(10_000_000_000)

    core, navigation, safety = _build_core(resolver)
    result = core.submit_command(_command())

    assert result.accepted
    assert core.active_execution.execution_id == 'execution-1'
    assert core.active_execution.state is InternalExecutionState.WAITING_TARGET
    assert core.active_context.state is InternalExecutionState.WAITING_TARGET
    assert core.active_target_id == ''
    assert navigation.submitted_calls == ()
    assert safety.calls == ()
    assert calls == ['visual-1']


def test_core_waiting_resolution_freezes_target_and_payload():
    payload = PoseStamped()
    payload.header.frame_id = 'map'
    payload.pose.position.x = 4.0
    calls = []

    def resolver(command, task):
        calls.append(command.command_id)
        if len(calls) == 1:
            return PendingGoal(10_000_000_000)
        return ResolvedGoal(payload, active_target_id='target-a')

    core, navigation, safety = _build_core(resolver)
    core.submit_command(_command())
    result = core.retry_waiting_goal_resolution()

    assert result.accepted
    assert core.active_execution.active_target_id == 'target-a'
    assert navigation.submitted_calls[0].goal_payload is payload
    assert safety.calls == ()
    assert calls == ['visual-1', 'visual-1']


def test_core_perception_update_does_not_reselect_frozen_goal():
    payloads = []

    def resolver(command, task):
        payload = PoseStamped()
        payload.pose.position.x = len(payloads) + 1.0
        payloads.append(payload)
        return ResolvedGoal(payload, active_target_id='target-a')

    core, navigation, _ = _build_core(resolver)
    core.submit_command(_command())

    assert core.active_target_id == 'target-a'
    assert len(payloads) == 1
    assert len(navigation.submitted_calls) == 1


def test_pause_resume_preserves_target_and_reuses_frozen_payload():
    payloads = []

    def resolver(command, task):
        payload = PoseStamped()
        payload.pose.position.x = 2.0
        payloads.append(payload)
        return ResolvedGoal(payload, active_target_id='target-a')

    core, navigation, safety = _build_core(resolver)
    core.submit_command(_command())
    old_handle = core.active_generation_handle
    navigation.inject_event(
        NavigationEvent(old_handle, NavigationEventType.GOAL_ACCEPTED)
    )
    safety.emit_acquire_result(old_handle.execution_id)
    frozen_payload = navigation.submitted_calls[0].goal_payload

    core.submit_command(_command('pause-1', task_id=2))
    navigation.inject_event(
        NavigationEvent(old_handle, NavigationEventType.CANCEL_CONFIRMED)
    )
    safety.emit_release_result(old_handle.execution_id)
    core.submit_command(_command('resume-1', task_id=3))

    assert core.active_execution.active_target_id == 'target-a'
    assert navigation.submitted_calls[-1].goal_payload is frozen_payload
    assert len(payloads) == 1


def test_waiting_timeout_fails_with_target_reason_and_no_lease():
    clock = FakeClock(_ros_time_s=2.0)

    def resolver(command, task):
        return PendingGoal(10_000_000_000)

    core, navigation, safety = _build_core(resolver, clock=clock)
    core.submit_command(_command())
    deadline = core.target_wait_deadline_ros_ns
    clock.advance_ros(10.0)
    result = core.tick()

    assert deadline == 12_000_000_000
    assert result.reason_code is CommandReason.TARGET_WAIT_TIMEOUT
    assert core.active_execution is None
    assert navigation.submitted_calls == ()
    assert safety.calls == ()
    terminal = core._execution_store.terminal_cache.get_execution(
        'execution-1'
    )
    assert terminal is not None
    assert terminal.state is ExternalTaskState.FAILED
    assert terminal.reason_code == int(CommandReason.TARGET_WAIT_TIMEOUT)


def test_waiting_retry_does_not_reset_original_deadline():
    clock = FakeClock(_ros_time_s=2.0)

    def resolver(command, task):
        return PendingGoal(10_000_000_000)

    core, _, _ = _build_core(resolver, clock=clock)
    core.submit_command(_command())
    original_deadline = core.target_wait_deadline_ros_ns
    clock.advance_ros(1.0)
    core.retry_waiting_goal_resolution()

    assert core.target_wait_deadline_ros_ns == original_deadline


def test_stop_and_estop_keep_waiting_execution_safety_semantics():
    def resolver(command, task):
        return PendingGoal(10_000_000_000)

    core, navigation, safety = _build_core(resolver)
    core.submit_command(_command())
    core.submit_command(_command('stop-1', task_id=4))
    assert core.active_execution is None
    assert navigation.submitted_calls == ()
    assert safety.calls == ()

    core, navigation, safety = _build_core(resolver)
    core.submit_command(_command())
    core.submit_command(_command('estop-1', task_id=6))
    assert core.active_execution is None
    assert safety.calls[0].operation.value == 'REQUEST_EMERGENCY_STOP'
    assert navigation.submitted_calls == ()
