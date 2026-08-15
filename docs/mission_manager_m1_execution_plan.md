# CleanNav Mission Manager M1 总体执行方案

> **状态：** M1 总体实现方案已冻结。
> **基线：** M0 最终总审查已 PASS；M1 已完成 Package Skeleton、FakeClock、Domain Models、Command Processing、Task Catalog Loader、FIFO Queue、Command Record Store。
> **目的：** 在继续实现 Execution Store、Generation Gate、State Machine 和 Mock Adapter 前，冻结 Mission Manager M1 的总体任务体系、ID 生命周期、执行语义和模块边界。
> **重要边界：** 本文冻结“框架和语义”，不冻结语音组尚未最终确定的具体任务数量、同义句数量和部署参数。

---

## 1. 总体原则

Mission Manager 是 CleanNav 的任务编排层，不是运动控制器，也不是感知模块。

总体链路冻结为：

```text
APP ──────────┐
离线语音 ─────┼──> Command Gateway
Mock ─────────┘
                    │
                    ▼
                 Validator
                    │
                    ▼
                Deduplicator
                    │
                    ▼
                Task Catalog
                    │
          ┌─────────┴─────────┐
          │                   │
       CONTROL             MISSION
          │                   │
     状态控制/抢占         Idle / Busy
          │                   │
          │             ┌─────┴─────┐
          │             │           │
          │          Activate     FIFO Queue
          │             │
          │             ▼
          │      Goal / Target Resolver
          │             │
          │             ▼
          │       Execution Record
          │             │
          │             ▼
          │        Generation
          │             │
          └─────────────┤
                        ▼
                Mission State Machine
                     │       │
                     ▼       ▼
            Navigation     Safety
             Adapter       Adapter
```

感知链路独立：

```text
Perception
   │
   ▼
CleaningTargetArray
   │
   ▼
Target Registry
   │
   ▼
Mission Goal Resolver
```

冻结原则：

1. APP、离线语音和 Mock 表达“用户意图”；
2. Perception 表达“环境观测”；
3. 视觉目标不得直接生成导航 Goal；
4. Mission Manager 不直接发布 `/cmd_vel`；
5. 最终运动输出继续由 Navigation Controller 和 Safety Supervisor 共同约束。

---

## 2. 任务体系

M1 只冻结两种一级任务类型：

```text
CONTROL
MISSION
```

具体语音任务数量当前不冻结。

### 2.1 CONTROL

CONTROL 不进入普通 Mission FIFO。

当前控制能力骨架：

```text
PAUSE
RESUME
STOP
RETURN_HOME
ESTOP
RESET_ESTOP
```

具体 Task ID 继续由 Task Catalog 管理。

CONTROL 命令：

- 有自己的 `command_id`；
- 不创建普通 mission execution；
- 不覆盖当前活动 execution 的原始 mission `command_id`；
- 可以作用于当前 execution 或 Manager Mode；
- CONTROL 的成功表示控制副作用完成，不等于原 mission 导航完成。

RETURN_HOME 是特殊 CONTROL：

- RETURN_HOME 命令本身属于控制命令；
- `home_pose` 校验成功后，可以创建一个独立的返航 execution；
- `home_pose` 校验失败时不得破坏当前任务或队列。

### 2.2 MISSION

MISSION 表示真正需要机器人执行的任务。

当前能力框架：

```text
MISSION
├── Default Mission
├── Fixed Goal Mission
├── Fixed Route Mission
└── Perception Target Mission
```

示例可以包括：

```text
默认清扫
前往预设点
执行预设路线
清扫最近落叶
清扫最近落叶堆
清扫最近积水
清扫最高优先级目标
```

上述示例不代表最终任务数量冻结。

---

## 3. Task Catalog 是任务扩展唯一权威入口

Mission Manager 核心逻辑禁止通过 `task_id` 数值区间直接推断行为。

例如禁止：

```python
if task_id < 10:
    ...
```

真正决定行为的权威字段必须来自 Task Catalog，例如：

```text
task_kind
enabled
allowed_sources
requires_confirmation
target_type
selection_rule
goal_pose
route_id
completion_radius_m
wait_timeout_sec
```

当前编号区间继续保留：

```text
1–9    system_command
10–19  fixed_goal
20–29  fixed_route
30–39  visual_target
40–99  reserved
```

编号区间只用于组织和兼容，不作为业务行为判断依据。

未来语音组增加任务时，优先只修改：

```text
task_catalog.yaml
voice_task_map*.yaml
```

原则上不得因此修改 Mission Manager 核心状态机。

---

## 4. 三类 ID 生命周期

### 4.1 command_id

`command_id` 表示“一次外部请求”。

来源：

```text
APP
Voice Bridge
Mock
```

作用：

- 请求幂等；
- 重试识别；
- Command Record 主键；
- Command Scope TaskStatus 关联。

规则：

```text
相同 command_id + 相同语义
→ 幂等重放已有状态
→ 不重复副作用

相同 command_id + 不同语义
→ DUPLICATE_COMMAND_CONFLICT
```

`header.stamp`、`confidence` 和 `raw_text` 不单独改变冻结的 command semantic fingerprint。

### 4.2 execution_id

`execution_id` 表示“一次实际 Mission Execution”。

只在 MISSION 真正激活时创建。

冻结：

```text
Command ACCEPTED
      │
      ├─ IDLE → 激活 → 创建 execution_id
      │
      └─ BUSY → QUEUED → execution_id 为空
                         │
                         ▼
                       激活
                         │
                         ▼
                   创建 execution_id
```

因此：

- REJECTED：通常没有 execution_id；
- QUEUED：execution_id 必须为空；
- 激活后：必须有 execution_id；
- 同一 mission execution 在 PAUSE / RESUME 中保持同一 execution_id。

### 4.3 generation

`generation` 表示同一个 execution 中第几次 Navigation Goal 提交。

冻结：

```text
第一次 Navigation submit
generation = 1
```

例如：

```text
execution_id = exec-001

generation 1
    │
    ├─ PAUSE → cancel generation 1
    │
    └─ RESUME
          │
          ▼
      generation 2
```

generation 用于：

- 隔离旧 Navigation callback；
- 禁止同 execution 同时存在多个有效 generation；
- 对应一次真实 Navigation Adapter Goal 生命周期。

---

## 5. 普通 MISSION 生命周期

冻结主流程：

```text
TaskCommand
    │
    ▼
Validate
    │
    ▼
Deduplicate
    │
    ▼
Task Catalog
    │
    ▼
ACCEPTED
    │
    ▼
机器人当前是否空闲？
    │
 ┌──┴───┐
 │      │
YES     NO
 │      │
 ▼      ▼
Activate QUEUED
 │      │
 │     FIFO
 │      │
 └──◄───┘
    │
    ▼
create execution_id
    │
    ▼
WAITING_TARGET
或
PREPARING_GOAL
    │
    ▼
NAVIGATION_STARTING
    │
    ▼
generation = 1
    │
    ▼
LEASE_ACQUIRING
    │
    ▼
EXECUTING
```

Navigation Goal 已提交不等于执行成功。

必须等待 Navigation Adapter 明确事件：

```text
GOAL_ACCEPTED
GOAL_REJECTED
RESULT
CANCEL_CONFIRMED
TIMEOUT
```

---

## 6. FIFO Queue

普通 MISSION 在已有活动 execution 时进入 FIFO。

冻结：

1. CONTROL 不进入 FIFO；
2. 相同 `task_id`、不同 `command_id` 可以同时排队；
3. QUEUED 时不得提前创建 execution；
4. 队列容量由参数注入，不在核心逻辑硬编码；
5. Queue 满时拒绝新 mission，不改变已有队列；
6. 已 ACCEPTED/QUEUED 的命令在激活前过期：
   - 从 Queue 移除；
   - Command Scope → CANCELED；
   - Reason=`QUEUED_COMMAND_EXPIRED`；
   - 不创建 execution。

---

## 7. PAUSE

PAUSE 不创建新 execution。

冻结流程：

```text
ACTIVE EXECUTION
      │
      ▼
PAUSE command ACCEPTED
      │
      ▼
release Safety Lease
      │
      ▼
cancel current generation
      │
      ▼
等待 cancel confirmed
      │
      ▼
PAUSED
```

保留：

```text
execution_id
原 mission command_id
task context
active target context
FIFO queue
```

重复 PAUSE：

```text
相同 command_id
→ 幂等重放

新 command_id 且已经 PAUSING / PAUSED
→ no-op SUCCEEDED
→ 不重复 cancel
```

---

## 8. RESUME

RESUME 恢复原 execution，不创建新 execution。

冻结：

```text
PAUSED / SAFETY_BLOCKED
       │
       ▼
RESUME ACCEPTED
       │
       ▼
generation += 1
       │
       ▼
重新确认 Goal / Target
       │
       ▼
submit 新 Navigation generation
       │
       ▼
重新 acquire Safety Lease
       │
       ▼
EXECUTING
```

因此：

```text
execution_id 不变
原 mission command_id 不变
generation 增加
```

---

## 9. STOP

STOP 语义：

```text
终止当前活动 execution
+
清空普通 FIFO
+
release Safety Lease
+
cancel 当前 generation
+
等待安全收口
```

STOP 本身不创建新的 mission execution。

完成后默认进入：

```text
IDLE
```

若本来已经：

```text
IDLE + queue empty
```

STOP 作为 no-op 成功。

---

## 10. RETURN_HOME

RETURN_HOME 必须先校验 `home_pose`。

### home_pose 无效

冻结：

```text
RETURN_HOME → REJECTED
当前 execution 不变
当前 generation 不变
FIFO 不清空
```

### home_pose 有效

冻结：

```text
RETURN_HOME ACCEPTED
      │
      ▼
清空 FIFO
      │
      ▼
收口旧 execution
      │
      ▼
创建独立 Return Home execution
      │
      ▼
generation = 1
      │
      ▼
导航到 home_pose
```

返航 execution 有独立：

```text
execution_id
generation
terminal lifecycle
```

---

## 11. ESTOP

ESTOP 是最高优先级全局安全行为。

冻结：

```text
ESTOP
  │
  ▼
ManagerMode = EMERGENCY_LATCHED
  │
  ├─ 清空普通 FIFO
  ├─ release Safety Lease
  ├─ cancel 当前 Navigation
  └─ 请求 Safety ESTOP
```

EMERGENCY_LATCHED 下：

- 普通 MISSION 禁止；
- 除 RESET_ESTOP 和重复 ESTOP 外的普通控制命令禁止；
- 重复 ESTOP 作为幂等安全成功处理。

---

## 12. RESET_ESTOP

RESET_ESTOP：

```text
检查解除急停条件
       │
       ▼
Safety clear
       │
       ▼
ManagerMode → NORMAL
```

冻结：

- 不恢复 ESTOP 前的旧 execution；
- 不恢复 ESTOP 前的 Queue；
- 不自动重新提交旧 generation；
- 解除后等待新的外部任务。

---

## 13. 视觉目标任务

Perception Target 是 Observation，不是 Command。

视觉组发布：

```text
CleaningTarget
CleaningTargetArray
PerceptionHealth
```

Mission Manager：

```text
Observation
   │
   ▼
Target Registry
   │
   ▼
selection_rule
   │
   ▼
active_target_id
```

M1 v1.0 默认策略：

> **目标一旦选中并进入当前 execution，不自动重选其他目标。**

若当前目标在执行过程中失效：

```text
当前 generation / execution 按 Reason Policy 失败或安全收口
```

不自动创建新 generation 去追逐另一个目标。

未来自动重选必须作为显式新策略，例如：

```text
reselect_policy
```

不得隐式加入 M1 v1.0。

---

## 14. Spatial Goal

v1.0 正式接口继续不生成：

```text
SpatialGoalRequest
```

预定义任务继续使用：

```text
TaskCommand(task_id)
```

未来 APP 地图点击、语音相对目标等动态空间目标使用独立：

```text
SpatialGoalRequest
```

不得通过无限增加 Task ID 模拟任意空间坐标。

Spatial Goal 属于后续接口版本能力，不进入当前 M1 核心实现。

---

## 15. Navigation Adapter 边界

Mission Manager 只表达导航意图：

```text
submit
cancel
observe result
```

每次 generation 映射到一次 Navigation Adapter Goal 生命周期。

冻结：

```text
一个 execution
同一时刻最多一个 active generation
```

旧 generation 的 callback：

```text
generation mismatch
→ stale callback
→ 忽略
→ 不允许改变当前 execution
```

同 generation 重复 submit：

```text
拒绝或安全阻断
```

不得产生并发 Navigation Goal。

---

## 16. Safety Adapter 边界

Mission Manager 不直接发布：

```text
/cmd_vel
```

Mission Manager 可通过 Safety Adapter 请求：

```text
acquire lease
renew lease
release lease
assert estop
clear estop
read structured safety state
```

实际运动输出链：

```text
Planner / Controller
       │
       ▼
candidate cmd_vel
       │
       ▼
Safety Supervisor
       │
       ▼
final cmd_vel
```

Safety Supervisor 继续拥有最终运动门控权。

---

## 17. Terminal Cache 与幂等

冻结：

1. Command Scope terminal status 进入 terminal cache；
2. Execution Scope terminal status 进入 terminal cache；
3. terminal status 第一次写入后不得覆盖；
4. 相同 command_id 同语义重试：
   - 不重复副作用；
   - 重放已有 Command Scope status；
   - Reason=`IDEMPOTENT_REPLAY`；
5. terminal cache 被容量淘汰后：
   - 原 command fingerprint / record 仍不得退化成 NEW；
   - 不允许重新执行旧命令；
   - 返回明确 cache missing / internal contract reason；
6. terminal cache 容量是部署参数，不在核心算法硬编码。

当前 M1 实现采用确定性有界 FIFO terminal cache。

---

## 18. 当前冻结与暂不冻结

### 18.1 当前冻结

- APP / Voice 为 Command，Perception 为 Observation；
- CONTROL / MISSION 两类任务；
- Task Catalog 驱动；
- CONTROL 不进入 FIFO；
- MISSION Busy 时进入 FIFO；
- command_id / execution_id / generation 三层 ID；
- execution 激活时创建 execution_id；
- 首 generation=1；
- RESUME 使用原 execution 且 generation+1；
- STOP 清当前 execution 和 FIFO；
- RETURN_HOME 校验成功后创建独立返航 execution；
- ESTOP 进入 EMERGENCY_LATCHED；
- RESET_ESTOP 不恢复旧任务；
- Visual M1 默认不自动重选；
- Mission Manager 不发布 `/cmd_vel`；
- SpatialGoal v1.0 不启用；
- 一个 execution 同时最多一个有效 generation。

### 18.2 当前不冻结

以下内容等待其他小组或部署阶段：

- 最终语音任务数量；
- 语音同义句数量；
- 最终 Task ID 使用数量；
- 固定点真实坐标；
- 固定路线真实路线；
- home_pose 真实位置；
- 语音 confidence 最终阈值；
- terminal cache 最终部署容量；
- FIFO 最终部署容量；
- J6M 最终性能参数；
- SpatialGoal v1.1 完整字段；
- Visual 自动重选策略。

---

## 19. M1 开发阶段划分

已完成：

```text
M1-2a  Package Skeleton
M1-2b  Domain Models
M1-2c  Command Processing
M1-2d  Task Catalog Loader
M1-2e  FIFO Mission Queue
M1-2f  Command Record Store + terminal cache
```

下一顺序冻结为：

```text
M1-2g  Execution Record Store
       │
       ▼
M1-2h  Generation / stale callback gate
       │
       ▼
M1-2i  Pure Mission State Machine
       │
       ▼
M1-3   Mock Navigation Adapter
       │
       ▼
M1-4   Mock Safety Lease Adapter
       │
       ▼
M1-5   Mission Manager Core orchestration
       │
       ▼
M1-6   ROS Node / Topic glue
       │
       ▼
M1 Full Mock Test
```

真实 Navigation Adapter 深度集成不属于当前 M1 Mock 阶段。

---

## 20. 变更控制

本文冻结的是 Mission Manager M1 总体执行架构。

后续以下变化不要求修改核心架构：

```text
增加语音同义句
增加 Task Catalog 普通任务
修改固定目标坐标
修改路线
修改部署容量
修改 Voice threshold
```

以下变化必须重新做架构审查：

```text
新增第三种一级 task_kind
改变 command_id 幂等语义
改变 execution_id 创建时机
改变 PAUSE/RESUME execution 关系
允许同 execution 并发 generation
允许 Mission Manager 绕过 Safety
启用视觉自动重选
启用 SpatialGoalRequest
改变 ESTOP / RESET_ESTOP 恢复语义
```

---

## 21. 下一步

本文确认后：

1. 不再等待语音组完整任务数量；
2. 不等待完整 Task Catalog；
3. 继续实现 `M1-2g Execution Record Store`；
4. Execution Store 必须遵循本文冻结的 command_id / execution_id / generation 生命周期；
5. 继续保持纯领域层；
6. 在 Pure State Machine 和 Mock Adapter 完成前，不创建真实 Navigation Adapter 集成。
