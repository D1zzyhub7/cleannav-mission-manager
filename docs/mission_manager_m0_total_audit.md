# CleanNav Mission Manager M0 最终总审查报告

> **状态：** M0 最终总审查完成。
> **结论：** **PASS — 允许冻结 M0，并进入 M1 Mock 实现阶段。**
> **审查基线：** `feature/mission-manager-m0`，HEAD `792d113`（`收口 Mission Manager M0 文档一致性`）。
> **边界：** 本结论仅覆盖 Mission Manager M0 文档、`cleannav_interfaces` v1.0 候选接口包及 M1 Mock 入口条件；不代表真实 Navigation Adapter、真实 Safety Lease、Continuous Navigation V1、APP/语音/视觉联合联调或 J6M 部署已经完成。

---

## 1. 审查目的

本报告用于对 CleanNav Mission Manager M0 阶段进行最终收口，确认以下制品在进入 M1 前是否已经达到一致、可实现、可测试的冻结条件：

1. `docs/mission_manager_architecture.md`
2. `docs/mission_manager_interface.md`
3. `docs/mission_manager_state_machine.md`
4. `docs/mission_manager_reason_codes.md`
5. `docs/mission_manager_test_plan.md`
6. `src/cleannav_interfaces/`

本轮重点不是重新设计，而是确认：

- 架构职责没有交叉或越权；
- 接口定义与实际 ROS 2 消息包一致；
- 状态机、Reason Code、队列和安全收口规则一致；
- 当前真实导航能力与未来 Adapter 抽象没有混淆；
- M1 能在不接真实导航、不接真实 Safety 的条件下通过 Mock 开始开发；
- 已解决的 M0 决策不再残留为“未决项”。

---

## 2. Git 与阶段基线

| 项目 | 当前基线 |
|---|---|
| 开发分支 | `feature/mission-manager-m0` |
| 当前 HEAD | `792d113` |
| 上一阶段状态提交 | `658ad00` |
| v1.0 接口包提交 | `f60a9d5` |
| M0 测试计划提交 | `dbb1586` |
| M1 业务代码 | 尚未创建 |
| 真实 Navigation Adapter | 尚未接入 |
| 真实 Safety Lease Adapter | 尚未接入 |
| Continuous Navigation V1 | 尚未完成 |

M0 总审查期间发现的接口文档和状态机陈旧表述，已在 `792d113` 中完成收口。

---

## 3. M0 最终验收结论

| # | 验收项 | 最终结论 |
|---|---|---|
| 1 | Mission Manager 职责边界 | **PASS** |
| 2 | Topic、消息职责、QoS、时间与坐标系 | **PASS** |
| 3 | v1.0 正式消息与版本边界 | **PASS** |
| 4 | Command / Execution / Manager 三层状态模型 | **PASS** |
| 5 | TaskStatus 外部状态与 Scope | **PASS** |
| 6 | Reason Code 数值、Scope、State 与 disposition | **PASS** |
| 7 | PAUSE / RESUME / STOP / RETURN_HOME / ESTOP / RESET_ESTOP | **PASS** |
| 8 | Safety Lease 抽象与安全收口 | **PASS** |
| 9 | Navigation Adapter generation / cancel / stale callback 规则 | **PASS** |
| 10 | M1 Mock 测试计划与 FakeClock | **PASS** |
| 11 | `cleannav_interfaces` 静态合同与 ROS 2 构建 | **PASS** |
| 12 | 跨文档一致性与 M0 未决项分类 | **PASS** |

**最终 M0 阻塞项数量：0。**

---

## 4. 架构一致性

### 4.1 Mission Manager 的系统职责

Mission Manager 只负责：

- 外部命令接收与归一化；
- 参数与权限校验；
- `command_id` 幂等；
- Task Catalog；
- 普通 Mission Queue；
- Command Record / Execution Record；
- Mission 生命周期状态机；
- Target Registry；
- Goal Resolver；
- Navigation Adapter 抽象；
- Safety Lease Manager 抽象；
- TaskStatus / RobotStatus 聚合发布。

Mission Manager **不负责**：

- A* 搜索；
- TEB 局部规划或动态避障；
- costmap 维护；
- `/follow_path` 的直接控制；
- `/cmd_vel` 输出；
- 用周期性重发 Goal 掩盖 Path Bridge / A* 连续导航问题。

### 4.2 速度安全边界

最终速度链保持：

```text
A* / Path Bridge / TEB
→ /cleannav/cmd_vel_candidate
→ Safety Supervisor
→ /cmd_vel
```

Safety Supervisor 仍是最终 `/cmd_vel` 的唯一发布者。

M1 Mission Manager 只使用 Mock Safety Lease Adapter，不接真实速度链。

### 4.3 J6M 与大模型边界

M0/M1 采用确定性 Task Catalog + 状态机设计，不依赖车端 LLM/VLA。

J6M 实机部署仍属于后续部署阶段，不是 M0/M1 验收内容。

---

## 5. 接口与版本边界

### 5.1 v1.0 正式消息

`cleannav_interfaces` v1.0 当前只生成以下 6 个消息：

```text
TaskCommand
TaskStatus
RobotStatus
CleaningTarget
CleaningTargetArray
PerceptionHealth
```

当前正式候选包中：

```text
不存在 ManualDriveRequest.msg
不存在 SpatialGoalRequest.msg
不存在 ReasonCodes.msg
不存在 srv/
不存在 action/
```

### 5.2 v1.1 与未来接口

`SpatialGoalRequest` 仅为 v1.1 预留，用于：

- APP 地图点击绝对目标；
- 受限语音相对空间目标。

其启用不阻塞 M1。

`ManualDriveRequest`、结构化 `SafetyStatus`、专用 Safety Lease Service 均属于未来候选，不属于当前 v1.0。

### 5.3 Reason 常量组织方式

M0 最终决定：

> **全部正式 `REASON_*` 常量直接定义在 `TaskStatus.msg`。**

不创建独立 `ReasonCodes.msg`。

该决定已经由当前接口包实现，并已从接口文档未决项中移除。

### 5.4 bounded string

v1.0 当前构建成功的字符串上界正式作为 M0 基线：

| 字段类别 | 上界 |
|---|---:|
| `interface_version` | 16 |
| `execution_id` | 64 |
| `command_id` | 128 |
| `target_id` / `active_target_id` | 128 |
| `source` | 64 |
| `class_name` | 64 |
| Robot/Perception `message` | 256 |
| TaskStatus `message` | 512 |
| TaskCommand `raw_text` | 512 |

后续若修改这些上界，视为接口变更，不在 M1 中静默修改。

---

## 6. TaskStatus 与状态机一致性

### 6.1 Scope

```text
SCOPE_UNKNOWN=0
SCOPE_COMMAND=1
SCOPE_EXECUTION=2
```

Command Scope 表示“某条输入命令的处理结果”。

Execution Scope 表示“某个 mission execution 的生命周期”。

控制命令本身的 `command_id` 不覆盖原 execution 的 mission `command_id`。

### 6.2 外部状态

冻结外部状态为 0～16：

```text
UNKNOWN
IDLE
ACCEPTED
REJECTED
QUEUED
WAITING_TARGET
PREPARING
NAVIGATING
PAUSING
PAUSED
CANCELING
RETURNING_HOME
SAFETY_BLOCKED
SUCCEEDED
CANCELED
FAILED
EMERGENCY_STOPPED
```

不存在 `STATE_APPLIED`。

内部 FINALIZING / cleanup 等实现态不直接扩散为额外 HMI 状态。

### 6.3 execution_id 与 generation

冻结规则：

- QUEUED 尚未创建 execution，因此 `execution_id` 为空；
- execution 激活后 `execution_id` 必须存在；
- 同时最多一个活动 execution；
- 同时最多一个活动 generation；
- RESUME 保持 execution_id，但创建新 generation；
- RETURN_HOME 创建新的返航 execution；
- cancel 未 confirmed 前不得提交新 generation；
- 旧 execution / generation 回调不得修改当前活动状态。

---

## 7. 队列与控制命令

### 7.1 普通任务

普通 mission 使用确定性等待队列。

M1 不引入复杂动态任务调度策略。

### 7.2 PAUSE

- 普通等待队列保留；
- 如有导航/Lease，则进入清理；
- PAUSED 后 RESUME 创建新 generation；
- 不恢复旧 generation。

### 7.3 STOP

- 不进入普通队列；
- 清空普通等待队列；
- release / cancel 后等待必要确认；
- 当前 execution 最终为 CANCELED；
- STOP 不等于 ESTOP。

### 7.4 RETURN_HOME

最终冻结规则：

> **必须先校验 `home_pose`。只有校验成功后，RETURN_HOME 才允许清空普通队列并破坏当前 mission。**

若 `home_pose` 无效：

- Command Scope REJECTED；
- 当前 execution 不变；
- 当前普通队列不变。

若校验成功：

- 清空普通队列；
- 安全收口旧 execution；
- 创建新的返航 execution；
- 新 generation 走正常 accepted → Lease → navigation 生命周期。

### 7.5 ESTOP

- 优先于全部普通事件；
- 立即进入 EMERGENCY_LATCHED；
- 清空普通队列；
- 请求急停；
- 后台继续完成必要 cancel/release 清理；
- 不等待导航 cancel 才进入急停锁闭。

### 7.6 RESET_ESTOP

- 语音不得解除急停；
- 只允许受信来源；
- 必须确认 Safety 新鲜、急停条件已解除、无活动 Lease、导航已收口；
- 清除后回到 NORMAL / IDLE；
- 不恢复急停前旧任务和旧队列。

---

## 8. Reason Code

M0 已冻结 Reason Code 数值空间、名称、Scope、State 和默认处置策略。

接口包静态审计确认：

- Reason 文档与 `TaskStatus.msg` 双向一致；
- 无缺失；
- 无额外常量；
- 无重复数值；
- `REASON_NONE=0`；
- `error_code` 已移除。

默认处置策略保持：

```text
CONTINUE_QUEUE
HOLD_QUEUE
CLEAR_QUEUE
```

程序控制只依赖结构化字段：

```text
status_scope
state
reason_code
command_id
execution_id
```

`message` 只用于人类诊断，不允许 HMI 或 Mission Manager 通过字符串解析驱动程序逻辑。

---

## 9. Safety Lease

### 9.1 M0 抽象合同

M0 只冻结抽象：

```text
acquire(execution_id)
renew(execution_id)
release(execution_id)
get_snapshot()
```

M1 使用 Mock Safety Lease Adapter。

### 9.2 安全不变量

- navigation goal accepted 前不 acquire Lease；
- Lease 未成功前不进入自主 EXECUTING；
- 没有有效 Lease 不得认为车辆处于自主执行；
- 需要 release 时，release 未确认前不得声明正常安全收口完成；
- Lease owner 与 execution_id 必须一致；
- Safety stale 不得继续假定授权有效；
- ESTOP 高于普通 Lease 生命周期。

### 9.3 当前真实 Safety Supervisor

当前真实 Safety Supervisor 的 `SetBool` 短时授权只是已有运行能力。

它**不是**已经冻结的正式 execution-aware Safety Lease 接口。

真实 Safety Adapter 的接口形式留到后续真实集成阶段。

---

## 10. Navigation Adapter

### 10.1 M0 抽象合同

Navigation Adapter 负责：

```text
submit_goal
cancel_goal
get_snapshot
```

并负责：

- execution_id；
- generation；
- accepted/rejected；
- cancel confirmed；
- terminal result；
- stale snapshot；
- old callback 隔离。

### 10.2 当前真实导航边界

当前真实链路仍是：

```text
/goal_pose
→ A*
→ /cleannav/global_path
→ Path Bridge
→ FollowPath
→ TEB
```

M0 **没有**把 `/goal_pose` Topic 描述成天然具有 accepted / cancel / result 语义。

这些语义是 Navigation Adapter 的抽象合同，真实映射必须在 Path Bridge / A* 连续导航问题修复后另行完成。

### 10.3 M1 禁止事项

M1 不得：

- 直接接真实 Path Bridge；
- 直接调用 FollowPath；
- 直接修复 A*；
- 通过重复 `/goal_pose` 模拟可靠导航；
- 以 Mock 通过宣称真实 Navigation Adapter 已通过。

---

## 11. 时间与 stale

### 11.1 ROS Clock

用于任务语义时间：

- TaskCommand 有效期；
- queued command expiry；
- CleaningTarget 有效期；
- target wait timeout；
- execution 的语义超时。

### 11.2 monotonic

用于运行看门狗：

- navigation accept timeout；
- navigation cancel timeout；
- Lease acquire / renew / release timeout；
- Navigation Snapshot stale；
- Safety Snapshot stale；
- PerceptionHealth 到达 stale；
- emergency clear confirmation timeout。

M1 测试不得依赖真实 sleep，必须使用可注入 FakeClock / monotonic clock。

---

## 12. CleaningTarget 与视觉任务

冻结规则：

- CleaningTarget 是环境观测，不是已批准导航 Goal；
- 只有 Goal Resolver 选中的目标才能形成 InternalGoal；
- `projection_valid=false` 不得导航；
- map frame 作为空间合同；
- 空数组不立即清空 Target Registry；
- 普通位置更新不自动重发 Goal；
- 执行中目标明确 INVALID / EXPIRED 时，M1 默认结束当前 execution；
- M1 默认不自动重选目标；
- 自动重选属于后续策略。

---

## 13. `cleannav_interfaces` 验证证据

当前 v1.0 候选接口包已经完成：

1. 文件边界检查；
2. CMake 生成边界检查；
3. JSON / YAML / XML 解析；
4. Task Catalog 语义检查；
5. Scope / State 检查；
6. Reason Code 双向一致性检查；
7. 静态回归审计 **19/19 PASS**；
8. `colcon build --packages-select cleannav_interfaces` 成功；
9. 6 个接口 `ros2 interface show` **6/6 PASS**；
10. Python 消息类型导入 **6/6 PASS**；
11. 3 个 config 文件进入安装空间。

首次构建曾因无效 maintainer 邮箱格式失败；当前 `package.xml` 已使用有效长期邮箱并成功构建。

该失败已经解决，不是当前阻塞项。

---

## 14. M1 测试入口

M1 可以在完全不接真实导航的条件下开始。

测试分层保持：

```text
L0 静态一致性
L1 纯领域逻辑单元测试
L2 Mock Navigation / Safety 组件测试
L3 ROS 2 launch / QoS 测试
```

核心夹具：

- FakeClock；
- DeterministicIdFactory；
- FakeTaskCatalog；
- Event Recorder；
- Mock Navigation Adapter；
- Mock Safety Lease Adapter；
- Reason Policy Oracle。

M1 必须优先验证：

- 命令校验；
- 幂等；
- FIFO/确定性队列行为；
- execution / generation；
- PAUSE / RESUME；
- STOP；
- RETURN_HOME；
- ESTOP / RESET_ESTOP；
- stale / timeout；
- reason policy；
- TaskStatus / RobotStatus；
- QoS。

---

## 15. 当前未决项分类

### 15.1 M0 阻塞项

**无。**

### 15.2 M1 实现阶段参数项

以下不阻塞开始 M1，但在具体测试实现时需要给出配置默认值：

- timeout 默认数值；
- terminal cache 容量与淘汰；
- RobotStatus 发布周期；
- Target Registry 的部分运行参数；
- 测试框架具体组织方式。

### 15.3 真实 Adapter 前必须决定

- `/goal_pose` → accepted/cancel/result 的真实映射；
- Path Bridge generation / cancel 串行语义；
- Safety Lease 最终 Service/Adapter 接口；
- Lease release 失败是否自动 ESTOP；
- RESET_ESTOP 的真实外部安全确认来源；
- Lease renew 定时器最终实现；
- 正式 Progress Checker 参数。

### 15.4 v1.1 / 后续

- SpatialGoalRequest 正式启用；
- APP 地图点击；
- 语音相对空间目标；
- ManualDriveRequest 是否重新立项；
- 结构化 SafetyStatus；
- 自动重选视觉目标；
- 电池字段；
- CleaningTarget 类型扩展。

### 15.5 部署阶段

- J6M 性能预算；
- 进程部署；
- DDS/QoS 车端调优；
- CPU/内存资源监控；
- 实车超时参数；
- APP/语音/视觉完整联调。

---

## 16. 已解决的 M0 总审查问题

本轮最终冻结前已完成以下修正：

1. `mission_manager_interface.md` 不再错误写“接口包尚未创建/构建”；
2. Reason 常量组织方式已明确为直接放入 `TaskStatus.msg`；
3. `ReasonCodes.msg` 不再作为 M0 未决方案；
4. bounded string 上界已按实际成功构建的 v1.0 消息冻结；
5. ManualDriveRequest 当前包处理方式已明确；
6. 状态机“待回填接口文档”章节已转为已完成回填；
7. 已冻结的 Reason Code / failure disposition 不再残留为未决项；
8. RETURN_HOME 队列不变量已修正为“home_pose 校验成功后才清空”；
9. 旧的、包含错误 maintainer 邮箱事实的 M0 审查报告已废弃。

---

## 17. 最终冻结结论

### 17.1 M0 结论

**PASS。**

当前 M0 制品已经形成一套相互一致的：

```text
Architecture
→ Interface Contract
→ State Machine
→ Reason Policy
→ Mock Test Contract
→ ROS 2 Interface Package
```

不存在阻塞 M1 Mock 开发的 M0 级问题。

### 17.2 允许进入的下一阶段

允许进入：

> **Mission Manager M1：包骨架 + Mock Navigation/Safety + L1 单元测试。**

M1 第一阶段仍不得接真实 Navigation Adapter。

### 17.3 不得扩大的结论

M0 PASS **不代表**：

- `cleannav_mission_manager` 已经实现；
- M1 已经通过；
- 真实 Navigation Adapter 已完成；
- A* / Path Bridge 连续导航问题已修复；
- Continuous Navigation V1 已完成；
- Safety SetBool 已经等于正式 Safety Lease；
- APP、语音和视觉完整联调已完成；
- J6M 已完成部署。

---

## 18. M0 冻结后的唯一推荐顺序

```text
1. 提交本最终总审查报告
2. 更新 PROJECT_STATUS：M0 PASS，M1 可开始
3. 创建 M1 cleannav_mission_manager 包骨架
4. 先实现纯领域模型和 FakeClock
5. 实现 Command Validator / Deduplicator / Queue
6. 实现 Mission State Machine
7. 实现 Reason Policy Oracle
8. 实现 Mock Navigation Adapter
9. 实现 Mock Safety Lease Adapter
10. 完成 L1 单元测试
11. 再进入 L2 ROS 2 组件 Mock 测试
```

在 M1 完成 Mock 验收前，不切入真实 Navigation Adapter。
