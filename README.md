# CleanNav Mission Manager

`cleannav_mission_manager` 是 CleanNav 无人清扫车系统的任务管理与执行编排模块，运行于 ROS 2 Humble。

它位于 HMI / Voice 与 Navigation / Safety 之间，负责把上层任务命令转换为可追踪、可取消、可恢复、受安全约束的 Mission Execution。

当前决赛阶段冻结基线：

~~~text
competition-hil-baseline-20260921
~~~

当前基线 commit：

~~~text
ed519714f2da4cb0e006cdc9fd27d0a80a769927
~~~

---

## 1. 模块定位

Mission Manager 位于：

~~~text
APP / Voice
    ↓
TaskCommand
    ↓
Mission Manager
    ↓
Task Catalog
    ↓
Execution / Queue / State Machine
    ↓
Navigation Adapter
    ↓
Safety Lease
    ↓
Navigation + Safety
~~~

Mission Manager 负责：

- TaskCommand 校验；
- command_id 幂等；
- Task Catalog 加载；
- Mission FIFO Queue；
- Command Record；
- Execution Record；
- generation 管理；
- stale callback 丢弃；
- Mission 状态机；
- PAUSE / RESUME / STOP；
- SOFTWARE ESTOP / RESET ESTOP；
- RETURN_HOME 框架；
- Visual Target 目标解析；
- Navigation Adapter；
- Safety Adapter；
- Safety Lease；
- PC Demo / HIL 执行桥；
- TaskStatus / RobotStatus 等运行状态编排。

Mission Manager 不负责：

- 全局路径规划；
- 局部路径跟踪；
- MPPI / Hybrid-A* 参数计算；
- 感知模型推理；
- APP 界面；
- Voice ASR；
- 直接控制底盘；
- 发布 `/cmd_vel`。

Mission Manager 绝不能成为第二条车辆控制链路。

---

## 2. 当前系统边界

系统上层任务链：

~~~text
Flutter APP
       │
       ├── HTTP Gateway
       │
Voice ─┘
       ↓
TaskCommand
       ↓
Mission Manager
~~~

Mission Manager 向下只通过受控 Adapter 与 Navigation / Safety 交互。

运动链：

~~~text
Mission Manager
    ↓
Navigation Request
    ↓
Navigation
    ↓
candidate control
    ↓
Safety Supervisor
    ↓
vehicle command
~~~

因此必须保持：

- APP 不发布 `/cmd_vel`；
- Voice 不发布 `/cmd_vel`；
- Mission Manager 不发布 `/cmd_vel`；
- Mission Manager 不修改 Ackermann 控制参数；
- Mission Manager 不绕过 Safety Supervisor。

---

## 3. 当前核心概念

### command_id

来自 APP / Voice / Mock 的外部命令幂等键。

同一个 `command_id` 的重复请求必须遵循幂等语义。

### execution_id

一个实际 Mission Execution 的身份。

它与 `command_id` 不等价。

### generation

一次 Navigation 提交实例的身份。

同一个 execution 在 PAUSE / RESUME 或重新提交时可以产生新的 generation。

旧 generation 的 callback 必须被识别为 stale callback 并丢弃。

三者关系：

~~~text
command_id
    ↓
execution_id
    ↓
generation 1
generation 2
generation 3
...
~~~

不得混用。

---

## 4. Task Catalog

Mission Manager 不根据 task_id 数字范围自行推断行为。

任务定义必须来自：

~~~text
cleannav_interfaces/config/task_catalog.yaml
~~~

运行时通过 ROS 2 package share 查找：

~~~python
ament_index_python.packages.get_package_share_directory(
    "cleannav_interfaces"
)
~~~

Task Catalog 是以下信息的权威来源：

- task_kind；
- enabled；
- allowed_sources；
- requires_confirmation；
- target type；
- route；
- fixed goal；
- completion rule。

---

## 5. 当前重点任务

决赛 PC / HIL Showcase 的核心任务：

~~~text
Task 30
CLEAN_NEAREST_LEAF
~~~

其语义是：

~~~text
TaskCommand
    ↓
Task 30
    ↓
查找有效 LEAF Target
    ↓
选择最近目标
    ↓
解析 Navigation Goal
    ↓
申请 Safety Lease
    ↓
提交 Navigation
    ↓
监控执行
    ↓
完成 / 取消 / 失败
~~~

Task 30 的目标选择属于 Mission Manager 业务层。

Perception 只提供 CleaningTarget Observation，不直接决定 Navigation Goal。

---

## 6. Visual Target

Visual Target 链路：

~~~text
Perception
    ↓
CleaningTargetArray
    ↓
Visual Target ROS Bridge
    ↓
Visual Target Goal Resolver
    ↓
Mission Manager
~~~

当前 V1 使用稳定目标选择与冻结策略。

普通位置更新不会自动重新选择另一目标。

CleaningTarget：

- 是 Observation；
- 不是 `/goal_pose`；
- 不直接进入 Costmap；
- 不直接调用 Navigation。

目标是否有效、是否可以成为 Mission Goal，由 Mission Manager 根据任务策略决定。

---

## 7. Navigation Adapter

当前仓库同时包含 Mock 和真实 Navigation Adapter 演进成果。

通用 Navigation 合同包括：

~~~text
submit
cancel
generation identity
callback/event
~~~

典型事件：

~~~text
GOAL_ACCEPTED
GOAL_REJECTED
GOAL_SUCCEEDED
GOAL_FAILED
CANCEL_CONFIRMED
CANCEL_FAILED
~~~

真实 Navigation Adapter 负责把 Mission Manager 的执行意图映射到实际导航系统。

Mission Manager 不关心 Hybrid-A*、MPPI 或底盘控制器内部实现。

---

## 8. Safety Adapter 与 Safety Lease

Mission Manager 通过 Safety Adapter 与 Safety Supervisor 协作。

运动授权使用 Safety Lease。

典型执行：

~~~text
Execution 创建
    ↓
Navigation 准备
    ↓
Acquire Safety Lease
    ↓
Navigation Active
    ↓
Mission 执行
    ↓
完成 / 暂停 / 停止 / 急停
    ↓
Release Safety Lease
~~~

Lease owner 必须对应当前有效 execution。

PAUSE、STOP、ESTOP 等路径必须正确处理：

- Navigation cancel；
- cancel confirmation；
- Safety Lease release；
- cleanup；
- terminal state。

---

## 9. PAUSE / RESUME / STOP

### PAUSE

PAUSE 不等于任务完成。

典型语义：

~~~text
PAUSE
→ release Safety Lease
→ cancel 当前 navigation generation
→ 等待 cancel confirmed
→ 保留 execution
→ 进入 PAUSED
~~~

### RESUME

~~~text
RESUME
→ 基于原 execution
→ 创建新 generation
→ 重新提交 Navigation
→ 重新申请 Safety Lease
~~~

### STOP

~~~text
STOP
→ 停止当前 execution
→ 清理普通等待队列
→ release Lease
→ cancel Navigation
→ 等待 confirmed
→ terminal cleanup
~~~

---

## 10. Emergency Stop

Software Emergency Stop 使用 latched 语义。

~~~text
SOFTWARE_EMERGENCY_STOP
→ EMERGENCY_LATCHED
→ release Safety Lease
→ cancel Navigation
→ 清空普通 Queue
~~~

RESET ESTOP：

- 不能自动恢复旧任务；
- 不能自动恢复旧 Queue；
- 不能自动恢复旧 generation；
- 必须满足 Safety 条件；
- Voice 不允许解除急停。

RESET 由 APP / 合法上层请求完成。

---

## 11. RETURN_HOME

RETURN_HOME 框架已经存在，但只有在 home pose 配置有效时才能执行。

原则：

- 先验证 home pose；
- 终止或清理当前执行；
- 创建独立 execution；
- 不复用被打断任务的旧 execution_id。

当前比赛基线不依赖 RETURN_HOME 作为核心 Showcase 功能。

---

## 12. PC Competition Showcase

当前仓库已经包含 PC competition showcase runtime。

主要入口包括：

~~~text
cleannav_mission_manager/demo_pc_offline_runner.py
cleannav_mission_manager/demo_hil_pc_execution_bridge.py
launch/pc_demo.launch.py
~~~

PC Showcase 主要用于：

- TaskCommand 接入；
- Task 30；
- Visual Target；
- Navigation；
- Safety Lease；
- TaskStatus；
- 物理完成条件；
- PC / J6 HIL 演示。

当前比赛 PC Showcase 已完成实际运行验证。

---

## 13. J6M HTTP HIL

当前 HIL 采用：

~~~text
J6M
 ↕ HTTP/TCP
PC
~~~

而不是跨 WSL / J6M 直接依赖 DDS。

J6M 端 runner 提供 HTTP 接口。

主要端点：

~~~text
GET  /health
GET  /nav/events
POST /nav/result
POST /task
~~~

Task 请求示例：

~~~json
{
  "task_id": 30,
  "source": 1,
  "command_id": "demo-command",
  "confidence": 0.95,
  "raw_text": "清扫最近的落叶",
  "user_confirmed": false,
  "valid_for_sec": 60.0
}
~~~

source：

~~~text
VOICE = 1
APP   = 2
MOCK  = 3
~~~

J6M HIL runner 运行时应避免形成 Task feedback loop。

当前比赛 HIL 使用：

~~~text
ROS_LOCALHOST_ONLY=1
~~~

作为 J6M 侧运行约束之一。

---

## 14. Voice 链路

当前 Voice 链路：

~~~text
Mic / WAV
    ↓
SenseVoice
    ↓
RecognizedUtterance
    ↓
Voice Policy
    ↓
TaskCommand(source=VOICE)
    ↓
Mission Manager
~~~

Mission Manager 不负责 ASR。

Voice 的 confidence、intent 与 source 必须先经过上层 Voice Policy。

当前比赛语音任务已验证：

~~~text
清扫最近的落叶
→ Task 30
~~~

Mission Manager 仍会执行自己的合同校验，不能因为 Voice 已经校验就跳过 Task Catalog。

---

## 15. APP 链路

APP 当前典型链路：

~~~text
Flutter APP
    ↓
HMI Gateway
    ↓
J6M / ROS2 TaskCommand
    ↓
Mission Manager
~~~

APP 不直接向 Mission Manager 发送速度或任意 Ackermann 控制量。

Mission Manager 只接收符合接口合同的任务请求。

---

## 16. 当前主要源码

核心文件包括：

~~~text
cleannav_mission_manager/core.py
cleannav_mission_manager/node.py
cleannav_mission_manager/ros_conversion.py

cleannav_mission_manager/visual_target_goal_resolver.py
cleannav_mission_manager/visual_target_ros_bridge.py
cleannav_mission_manager/target_ros_conversion.py

cleannav_mission_manager/demo_pc_offline_runner.py
cleannav_mission_manager/demo_hil_navigation_runner.py
cleannav_mission_manager/demo_hil_pc_execution_bridge.py
cleannav_mission_manager/demo_mm_harness.py

launch/mission_manager.launch.py
launch/pc_demo.launch.py
~~~

Domain 层实现位于：

~~~text
cleannav_mission_manager/domain/
~~~

---

## 17. 设计文档

主要文档：

~~~text
docs/mission_manager_architecture.md
docs/mission_manager_interface.md
docs/mission_manager_state_machine.md
docs/mission_manager_reason_codes.md
docs/mission_manager_test_plan.md
docs/mission_manager_m0_total_audit.md
docs/mission_manager_m1_execution_plan.md
docs/PROJECT_STATUS.md
docs/MONOREPO_MIGRATION.md
~~~

---

## 18. ROS 2 依赖

运行环境：

~~~text
Ubuntu 22.04
ROS 2 Humble
Python 3.10
~~~

主要依赖：

~~~text
rclpy
cleannav_interfaces
python3-yaml
~~~

`cleannav_interfaces` 必须作为独立 ROS 2 package 提供。

Mission Manager 不复制接口定义。

---

## 19. 构建

示例：

~~~bash
source /opt/ros/humble/setup.bash

cd <workspace>

colcon build \
  --packages-select cleannav_interfaces cleannav_mission_manager

source install/setup.bash
~~~

---

## 20. 测试

比赛冻结阶段 Mission Manager 已完成完整单元测试与 ROS 2 package 回归。

当前冻结阶段记录：

~~~text
584 passed
1 skipped
0 errors
0 failures
~~~

同时要求：

~~~text
flake8 PASS
pep257 PASS
git diff --check PASS
~~~

如果接口、状态机、Safety Lease 或 Navigation Adapter 有修改，应重新执行完整回归。

---

## 21. 当前 Git 基线

当前比赛 HIL baseline：

~~~text
competition-hil-baseline-20260921
~~~

对应：

~~~text
ed519714f2da4cb0e006cdc9fd27d0a80a769927
~~~

最近关键提交：

~~~text
512185f  add navigation and safety runtime adapters
7327930  add PC competition showcase runtime
ed51971  add J6 HTTP HIL execution bridge
~~~

这些提交共同构成当前比赛 Mission Manager 基线。

---

## 22. GitHub 协作

当前交接分支：

~~~text
sync/competition-handoff-20260926
~~~

该分支包含当前比赛冻结代码。

完成 PR 后，组员应从最新 main 开始新开发：

~~~bash
git clone https://github.com/D1zzyhub7/cleannav-mission-manager.git
cd cleannav-mission-manager

git switch main
git pull --ff-only

git switch -c feature/<your-feature>
~~~

禁止重写比赛冻结历史。

禁止直接修改已有 baseline tag。

---

## 23. 下一阶段

决赛下一阶段重点包括：

- J6M 实车链路；
- Vehicle Adapter / Arbiter；
- 实车 Navigation / Safety 联调；
- Coverage Cleaning；
- 动态障碍行为优化；
- APP / Voice / Mission Manager 全链路；
- VLM Shadow Decision；
- 最终系统集成。

Mission Manager 下一阶段重点不是增加第二条控制链，而是继续稳定：

~~~text
Task
→ Execution
→ Navigation
→ Safety
→ Result
~~~

这条唯一任务执行链。
