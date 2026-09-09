"""DEMO-only Mission Manager mock runtime auto-ack harness."""

from __future__ import annotations

import rclpy
from rclpy.qos import (
    DurabilityPolicy,
    HistoryPolicy,
    QoSProfile,
    ReliabilityPolicy,
)

from cleannav_interfaces.msg import TaskStatus

from cleannav_mission_manager.adapters.navigation import (
    NavigationEvent,
    NavigationEventType,
)
from cleannav_mission_manager.node import MissionManagerNode


def state_name(value: int) -> str:
    for name in dir(TaskStatus):
        if name.startswith("STATE_") and getattr(TaskStatus, name) == value:
            return name
    return "UNKNOWN"


def scope_name(value: int) -> str:
    if value == 1:
        return "COMMAND"
    if value == 2:
        return "EXECUTION"
    return "UNKNOWN"


def main() -> None:
    rclpy.init()

    mm = MissionManagerNode()

    if mm.runtime is None:
        raise RuntimeError("mock runtime unavailable")

    navigation = mm.runtime.navigation
    safety = mm.runtime.safety

    indexes = {
        "safety": 0,
        "submit": 0,
        "cancel": 0,
        "status": 0,
    }

    qos = QoSProfile(
        history=HistoryPolicy.KEEP_LAST,
        depth=10,
        reliability=ReliabilityPolicy.RELIABLE,
        durability=DurabilityPolicy.TRANSIENT_LOCAL,
    )

    def on_status(msg: TaskStatus) -> None:
        indexes["status"] += 1

        print()
        print("=" * 72)
        print(f"TASK_STATUS_RECEIVED={indexes['status']}")
        print(f"command_id={msg.command_id!r}")
        print(f"execution_id={msg.execution_id!r}")
        print(f"task_id={msg.task_id}")
        print(
            f"status_scope={msg.status_scope} "
            f"({scope_name(msg.status_scope)})"
        )
        print(
            f"state={msg.state} "
            f"({state_name(msg.state)})"
        )
        print(f"reason_code={msg.reason_code}")
        print("=" * 72)

    status_subscription = mm.create_subscription(
        TaskStatus,
        "/cleannav/task_status",
        on_status,
        qos,
    )

    def auto_ack() -> None:
        safety_calls = getattr(safety, "calls", ())

        while indexes["safety"] < len(safety_calls):
            call = safety_calls[indexes["safety"]]
            indexes["safety"] += 1

            operation = call.operation.name
            execution_id = call.execution_id

            print(
                "[DEMO_AUTO_ACK] SAFETY "
                f"{operation} execution_id={execution_id!r}"
            )

            if operation == "ACQUIRE_LEASE":
                safety.emit_acquire_result(execution_id)
                print("[DEMO_AUTO_ACK] -> LEASE_ACQUIRED")

            elif operation == "RELEASE_LEASE":
                safety.emit_release_result(execution_id)
                print("[DEMO_AUTO_ACK] -> LEASE_RELEASED")

            elif operation == "REQUEST_EMERGENCY_STOP":
                print("[DEMO_AUTO_ACK] -> ESTOP REQUEST RECORDED")

            safety_calls = getattr(safety, "calls", ())

        submitted_calls = getattr(
            navigation,
            "submitted_calls",
            (),
        )

        while indexes["submit"] < len(submitted_calls):
            call = submitted_calls[indexes["submit"]]
            indexes["submit"] += 1

            print(
                "[DEMO_AUTO_ACK] NAV SUBMIT "
                f"{call.handle!r}"
            )

            navigation.inject_event(
                NavigationEvent(
                    handle=call.handle,
                    event_type=NavigationEventType.GOAL_ACCEPTED,
                )
            )

            print("[DEMO_AUTO_ACK] -> GOAL_ACCEPTED")

            submitted_calls = getattr(
                navigation,
                "submitted_calls",
                (),
            )

        cancel_calls = getattr(
            navigation,
            "cancel_calls",
            (),
        )

        while indexes["cancel"] < len(cancel_calls):
            call = cancel_calls[indexes["cancel"]]
            indexes["cancel"] += 1

            print(
                "[DEMO_AUTO_ACK] NAV CANCEL "
                f"{call.handle!r}"
            )

            navigation.inject_event(
                NavigationEvent(
                    handle=call.handle,
                    event_type=NavigationEventType.CANCEL_CONFIRMED,
                )
            )

            print("[DEMO_AUTO_ACK] -> CANCEL_CONFIRMED")

            cancel_calls = getattr(
                navigation,
                "cancel_calls",
                (),
            )

    auto_ack_timer = mm.create_timer(0.05, auto_ack)

    print()
    print("=" * 72)
    print("CLEANNAV VOICE + MM DEMO HARNESS")
    print("=" * 72)
    print("DEMO_ONLY=YES")
    print("MISSION_MANAGER=READY")
    print("RUNTIME_MODE=" + mm.runtime_mode)
    print("MOCK_NAV_AUTO_ACK=ENABLED")
    print("MOCK_SAFETY_AUTO_ACK=ENABLED")
    print()
    print("Demo sequence:")
    print("  清扫最近的落叶")
    print("  暂停")
    print("  继续")
    print("  紧急停止")
    print("=" * 72)

    try:
        rclpy.spin(mm)
    except KeyboardInterrupt:
        pass
    finally:
        del status_subscription
        del auto_ack_timer
        mm.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
