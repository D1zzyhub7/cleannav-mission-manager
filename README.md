# CleanNav Mission Manager

`cleannav_mission_manager` 是 CleanNav 无人清扫车系统的任务管理模块，运行于 ROS 2 Humble。

本仓库由原 CleanNav monorepo 中的 Mission Manager 源码和设计文档提取而来，并保留从 M0 架构冻结到当前 M1 开发阶段的有效 Git 历史。

## 1. 当前状态

当前开发状态：

- M0：已完成并冻结
- M1：已完成 M1-2g Execution Record Store、M1-2h Generation / stale callback gate、Pure State Machine 基础、PAUSE / STOP / RESUME / RETURN_HOME 控制取消与恢复语义、ESTOP / RESET_ESTOP latched 状态机、framework-first 原则，以及 M1-3 Mock Navigation Adapter
- 当前稳定开发点：M1-3 CLOSED
- 当前最新代码 HEAD：`b8265eac8761bc350e2a8806621b6d85dfeccd3d`
- 独立仓库适配：已完成
- 下一计划组件：M1-4 Mock Safety Adapter

当前验证环境：

- WSL2 Ubuntu 22.04
- ROS 2 Humble
- Python 3.10

当前验证结果：

- Direct unit test：210 passed
- ROS 2 package test：213 tests
- Errors：0
- Failures：0
- Skipped：1
- flake8 / pep257：PASS
- git diff check：PASS

## 2. 模块职责

Mission Manager 负责 CleanNav 的任务生命周期管理和任务执行编排，包括：

- Command 校验
- Command 幂等处理
- Task Catalog 加载与验证
- Mission FIFO Queue
- Command Record Store
- Execution Record Store
- Generation / stale callback gate
- Pure State Machine 基础
- PAUSE / STOP / RESUME / RETURN_HOME 控制取消与恢复语义
- ESTOP / RESET_ESTOP latched 状态机
- M1-3 Mock Navigation Adapter

Mission Manager 不负责：

- 全局路径规划
- 局部规划与动态避障
- 直接发布 `/cmd_vel`
- 感知模型推理
- APP 本身
- 语音识别本身

Mission Manager 不替代导航系统。

最终运动命令仍由导航链路产生，并经过 Safety Supervisor 的最终安全门控。

## 3. Framework-first 原则与通用 Adapter 合同

Mission Manager 当前冻结通用框架，不提前冻结业务细节：

- 不冻结最终 Task 数量和名称。
- 不冻结最终工作模式集合。
- 不冻结最终 Perception 接入业务结构。
- 不冻结最终 Navigation Goal 业务结构。

Mock Navigation Adapter 只冻结通用 `submit`、`cancel`、callback/event 和 generation identity 合同，不冻结具体导航业务。当前通用事件包括：

`GOAL_ACCEPTED`、`GOAL_REJECTED`、`GOAL_SUCCEEDED`、`GOAL_FAILED`、`CANCEL_CONFIRMED`、`CANCEL_FAILED`

## 4. 当前核心依赖

运行依赖：

- `rclpy`
- `cleannav_interfaces`
- `python3-yaml`

主要测试依赖：

- `ament_copyright`
- `ament_index_python`
- `ament_flake8`
- `ament_pep257`
- `python3-pytest`

`cleannav_interfaces` 是独立的 ROS 2 接口模块，不复制进入本仓库。

真实 Task Catalog 测试通过 ROS 2 `ament_index` 定位已安装的：

`cleannav_interfaces/config/task_catalog.yaml`

因此 Mission Manager 不再依赖旧 monorepo 中两个源码 package 必须处于固定相邻目录的假设。

## 5. 仓库结构

当前仓库主要结构如下：

    cleannav-mission-manager/
    ├── cleannav_mission_manager/
    │   └── domain/
    ├── docs/
    ├── resource/
    ├── test/
    │   └── unit/
    ├── package.xml
    ├── setup.py
    ├── setup.cfg
    ├── LICENSE
    └── README.md

ROS 2 package 位于仓库根目录。

这使得本仓库后续可以直接作为 CleanNav 系统工作区中的一个独立组件使用。

## 6. 核心设计文档

当前保留的 Mission Manager 设计文档包括：

- `docs/mission_manager_architecture.md`
- `docs/mission_manager_interface.md`
- `docs/mission_manager_state_machine.md`
- `docs/mission_manager_reason_codes.md`
- `docs/mission_manager_test_plan.md`
- `docs/mission_manager_m0_total_audit.md`
- `docs/mission_manager_m1_execution_plan.md`

后续仓库治理文档包括：

- `CHANGELOG.md`
- `docs/PROJECT_STATUS.md`
- `docs/MONOREPO_MIGRATION.md`

## 7. M1 当前实现

当前已经实现：

1. ROS 2 Python package skeleton
2. FakeClock
3. Domain Models
4. Command Validation
5. Command Idempotency
6. Task Catalog Loader
7. FIFO Mission Queue
8. Command Record Store
9. Execution Record Store
10. Generation / stale callback gate
11. Pure State Machine 基础
12. 控制取消与恢复语义
13. ESTOP / RESET_ESTOP latched 状态机
14. M1-3 Mock Navigation Adapter

M1 总体执行方案已经冻结在：

`docs/mission_manager_m1_execution_plan.md`

下一计划组件为：

`M1-4 Mock Safety Adapter`

## 8. 已冻结的重要边界

`command_id`、`execution_id` 和 `generation` 必须保持不同职责。

Task Catalog 是任务展开的唯一权威来源。

不得根据 `task_id` 数字范围自行推断任务行为。

Mission Manager 不发布 `/cmd_vel`。

Safety Supervisor 保持最终安全门控职责。

M1 v1.0 中 Visual Target 采用目标冻结策略，不自动重新选择。

Spatial Goal 在 M1 v1.0 中保持关闭。

ESTOP 为 latched 模式。

RESET_ESTOP 不自动恢复旧 execution、queue 或 generation。

RETURN_HOME 必须先验证 home pose，并创建独立 execution，不复用被打断任务的旧 execution_id。

旧 generation callback 必须作为 stale callback 丢弃，不得修改当前有效 execution。

## 9. 构建与测试原则

本仓库可以独立作为 ROS 2 package 构建。

系统集成时由外部工作区提供：

- ROS 2 Humble
- `cleannav_interfaces`
- 其他 CleanNav 组件

建议将 colcon 的 build、install 和 log 输出放在工作区或独立验证目录，而不是源码 package 根目录中。

## 10. Git 历史说明

本仓库的早期 Git 历史来自原 CleanNav monorepo。

路径过滤后，Git commit 的 tree 发生变化，因此过滤后的 commit SHA 与原 monorepo SHA 不同，这是正常现象。

Mission Manager 的有效开发顺序和相关文件历史已经保留。

完整的：

原 monorepo SHA → 独立仓库 SHA

映射将在：

`docs/MONOREPO_MIGRATION.md`

中永久记录。

## 11. 当前关键 Git 基线

M0 最终冻结：

`a2d7baca48debc74bc4982fbbb4c83a33bd64aae`

M1-2g Execution Record Store：

`1b487dddea9ab75d04b76b986ecfcfc46a61bf71`

独立仓库适配：

`5f76fb749041fd288e15d1fde17194d4f7593d3f`
