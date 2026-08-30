# CleanNav Mission Manager 项目状态

## 1. 当前模块

模块名称：

`cleannav_mission_manager`

职责：

CleanNav 任务生命周期管理、任务编排和执行状态管理。

标准开发环境：

- WSL2 Ubuntu 22.04
- ROS 2 Humble
- Python 3.10

当前开发分支：

`feature/mission-manager-m1`

## 2. 当前开发阶段

Mission Manager M0 已完成并冻结。

Mission Manager M1 当前已经完成：

- `M1-2g Execution Record Store`
- `M1-2h Generation / stale callback gate`
- Pure State Machine 基础
- PAUSE / STOP / RESUME / RETURN_HOME 控制取消与恢复语义
- ESTOP / RESET_ESTOP latched 状态机
- framework-first 原则
- `M1-3 Mock Navigation Adapter`

当前稳定开发点：

`M1-3 CLOSED`

当前最新代码 HEAD：

`b8265eac8761bc350e2a8806621b6d85dfeccd3d`

当前独立仓库适配已经完成。

下一计划组件：

`M1-4 Mock Safety Adapter`

## 3. 当前 Git 基线

M0 最终冻结：

`a2d7baca48debc74bc4982fbbb4c83a33bd64aae`

M1-2g Execution Record Store：

`1b487dddea9ab75d04b76b986ecfcfc46a61bf71`

独立仓库适配：

`5f76fb749041fd288e15d1fde17194d4f7593d3f`

原 monorepo M1-2g：

`0830c301084df41fd2e39501c6d52e10c5465892`

最近实现链：

- `d4beb7a` Generation Gate
- `c05366e` Pure State Machine
- `5f6c756` control cancel/resume state machine
- `8264e45` ESTOP latch state machine
- `82b0322` framework-first principle
- `b8265ea` Mock Navigation Adapter

## 4. 当前验证状态

独立仓库已经完成真实 ROS 2 构建和测试验证。

验证结果：

- colcon build：PASS
- colcon test：PASS
- colcon test-result：PASS
- 213 tests
- 0 errors
- 0 failures
- 1 skipped
- direct unit test：210 passed
- flake8：PASS
- pep257：PASS
- git diff check：PASS

当前存在两个非阻断 warning：

`SelectableGroups dict interface is deprecated. Use select.`

该 warning 来自当前 ROS 2 Humble / Python 测试依赖，不属于 Mission Manager 业务逻辑失败。

## 5. 当前已经实现

M1 当前已经实现：

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

M1 总体执行方案已经冻结于：

`docs/mission_manager_m1_execution_plan.md`

## 6. 下一阶段执行顺序

下一阶段严格按照冻结方案继续：

1. M1-4 Mock Safety Adapter
2. Mission Manager Core
3. ROS glue
4. Full Mock Test

真实 Navigation Adapter 暂不提前接入。

真实 Safety Lease 暂不提前接入。

## 7. Command / Execution / Generation 边界

三个身份必须保持分离。

### command_id

`command_id` 表示外部请求身份，并承担幂等语义。

重复 command 不应创建新的业务执行。

### execution_id

`execution_id` 只在 MISSION 真正激活时创建。

queued mission 不拥有 execution_id。

### generation

`generation` 表示某一个 execution 内的导航尝试代次。

第一次导航：

`generation = 1`

RESUME：

- 保留原 `execution_id`
- 创建新的 generation
- `generation + 1`

任意旧 generation callback 必须被识别为 stale callback。

stale callback 不得修改当前有效 execution 状态。

任意 execution 同一时刻最多只能有一个 active generation。

## 8. Task Catalog 边界

Task Catalog 是任务展开的唯一权威来源。

不得根据 `task_id` 数字范围自行推断行为。

Mission Manager 当前依赖独立的：

`cleannav_interfaces`

真实 Task Catalog 位于：

`cleannav_interfaces/config/task_catalog.yaml`

独立仓库测试通过 ROS 2 `ament_index` 获取安装后的 package share。

不得重新引入对旧 monorepo 固定相邻目录的依赖。

## 9. Safety 边界

Mission Manager 不发布：

`/cmd_vel`

导航模块负责产生运动控制输出。

Safety Supervisor 保持最终安全门控和仲裁职责。

Mission Manager 不替代 Safety Supervisor。

## 10. Visual Target 边界

M1 v1.0 使用目标冻结策略。

MISSION 激活并选定视觉目标后，不自动重新选择目标。

视觉环境后续发生变化时，不应在当前 M1 v1.0 中静默切换任务目标。

## 11. Spatial Goal 边界

Spatial Goal 在 M1 v1.0 中保持关闭。

相关接口可以作为后续版本预留，但当前 M1 不实现其完整执行链。

## 12. ESTOP / RESET_ESTOP 边界

ESTOP 为 latched 模式。

ESTOP 生效后不得自动恢复旧任务执行。

RESET_ESTOP 只解除急停锁存状态。

RESET_ESTOP 不自动恢复：

- 旧 execution
- 旧 queue
- 旧 generation

## 13. RETURN_HOME 边界

RETURN_HOME 在打断当前任务之前必须先验证有效的 home pose。

如果 home pose 无效，不应破坏当前任务状态。

如果 home pose 有效：

- 当前旧 execution 正常关闭
- 创建独立的 Return Home execution
- 新 execution 从 generation 1 开始

RETURN_HOME 不复用旧 execution_id。

## 14. 当前尚未完成

以下内容当前不能宣称已经完成：

- Mock Safety Adapter
- Mission Manager Core
- ROS glue
- Full Mock Test
- 真实 Navigation Adapter
- 真实 Safety Lease 联调
- APP 联调
- Voice 联调
- Perception 联调
- APP / Voice / Perception 联合联调
- J6M 实车部署

## 15. 独立仓库说明

本仓库由原 CleanNav monorepo 提取。

过滤后 Mission Manager 的有效历史得到保留，但由于 Git tree 发生变化，commit SHA 与原 monorepo 不同。

完整原 SHA 与新 SHA 映射记录于：

`docs/MONOREPO_MIGRATION.md`

旧 monorepo 仍作为拆仓前完整系统历史基线保留。

后续 Mission Manager 开发应以本独立仓库为模块级 Git 历史来源。

系统级组件组合最终由：

`cleannav-system`

记录各组件的精确 commit。

<!-- M1_3_FRAMEWORK_FIRST_STATUS_START -->
## M1-3 Framework-First Status

M1-3 开始采用 framework-first 原则：Mock Navigation Adapter 当前只冻结通用 submit / cancel / callback、generation identity 和确定性测试机制，不冻结具体 Task、工作模式、Perception 接入方式或 Navigation Goal 的业务结构。
<!-- M1_3_FRAMEWORK_FIRST_STATUS_END -->
