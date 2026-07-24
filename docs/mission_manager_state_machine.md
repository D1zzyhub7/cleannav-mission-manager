# CleanNav Mission Manager M0 状态机设计

> **状态：** M0-3 状态机冻结候选（修订版）。
> **边界：** 本文冻结 Command Record、Execution Record、Manager Mode、内部执行状态、外部 TaskStatus 状态、事件、队列、超时与不变量；尚未创建业务代码或 Mock Adapter。
> **后续依赖：** 精确 `reason_code` 数值和故障处置映射由 `mission_manager_reason_codes.md` 冻结。

---

## 1. 文档目的与当前事实

本文用于统一 Mission Manager 的命令处理、任务执行、导航代际、Safety Lease、暂停/恢复、停止、返航和急停语义。

当前事实：

- Navigation Execution Baseline Alpha 已完成；
- B1-4g 单 Path、单 FollowPath、停止 A* 后的动态障碍恢复隔离实验已通过；
- Continuous Navigation V1 尚未完成；
- 当前 `/goal_pose`、A* 状态和 Path Bridge 状态不具备完整的任务关联 accepted/cancel/result 合同；
- M0/M1 只使用 Mock Navigation Adapter 和 Mock Safety Lease Adapter；
- 真实 Adapter 必须等待 A* 与 Path Bridge 修复；
- Mission Manager 不直接调用 FollowPath，不直接发布 `/cmd_vel`，不周期性重发 Goal。

---

## 2. 三层状态模型

### 2.1 Command Record

每条 `TaskCommand` 或 `SpatialGoalRequest` 都建立 Command Record，以 `command_id` 为键。

| 内部状态 | 含义 |
|---|---|
| RECEIVED | 已接收，尚未完成基础处理 |
| ACCEPTED | 校验通过，已受理 |
| REJECTED | 校验失败或当前状态不允许 |
| QUEUED | 普通 mission 已进入等待队列 |
| APPLIED | 控制命令已对状态机生效 |
| TERMINAL | 命令处理已完成，不得再次执行；允许幂等重放已有状态 |

冻结规则：

- `TERMINAL` 的“不得再次执行”不等于拒绝幂等重放；
- 相同 `command_id` 且语义相同：重放已有 Command Scope TaskStatus；
- 相同 `command_id` 但语义不同：拒绝为重复 ID 冲突；
- Command Record 的 APPLIED/TERMINAL 是内部状态，不新增同名外部 TaskStatus 状态；
- 成功应用的控制命令对外最终使用 `STATE_SUCCEEDED`；
- 控制命令失败对外使用 `STATE_FAILED` 或 `STATE_REJECTED`。

### 2.2 Execution Record

Execution Record 表示一次实际 mission：

- 有唯一 `execution_id`；
- 来源于一个普通 mission 命令，或独立 RETURN_HOME mission；
- 可以经历多个 navigation `generation`；
- 同时最多一个活动 execution；
- 同时最多一个活动 generation；
- 终态为 SUCCEEDED、FAILED、CANCELED 或 EMERGENCY_STOPPED；
- 终态写入后不可恢复，只能创建新的 execution。

### 2.3 Manager Mode

| 模式 | 含义 |
|---|---|
| NORMAL | 正常受理和执行 mission、控制命令 |
| EMERGENCY_LATCHED | 全局急停锁闭；拒绝普通 mission 和除 RESET_ESTOP、重复 ESTOP 外的控制命令 |

EMERGENCY_LATCHED 解除后：

- 不恢复旧 execution；
- 不恢复旧 generation；
- 不恢复已清空队列；
- 返回无活动任务状态；
- 需要新的用户命令才能继续工作。

---

## 3. Internal Execution State

### 3.1 状态集合

| 状态 | 进入条件 | 主要退出条件 |
|---|---|---|
| IDLE | 初始化或无活动 execution | 激活新普通 mission 或返航 mission |
| WAITING_TARGET | 视觉 mission 暂无有效目标 | 目标出现、超时、控制命令 |
| PREPARING_GOAL | 已有任务上下文，正在生成/校验 InternalGoal | Goal 准备完成、失败、控制命令 |
| NAVIGATION_STARTING | 已单次 submit，等待目标接受 | accepted、rejected、超时、控制命令 |
| LEASE_ACQUIRING | 目标已接受，等待 Safety Lease | acquire 成功/失败、控制命令、ESTOP |
| EXECUTING | Goal 已接受且 Lease 有效 | 导航终态、暂停、停止、返航、安全阻断、ESTOP |
| CANCELING | 已发出取消/释放请求，等待所需收口条件 | cancel/release 收口成功或失败 |
| FINALIZING | 导航已产生成功/失败终态，等待 Lease release 收口 | release 成功或失败 |
| PAUSED | 暂停收口完成，保留 execution 上下文 | RESUME、STOP、RETURN_HOME、ESTOP |
| SAFETY_BLOCKED | Safety/Adapter 状态不可信或安全收口失败 | 明确原因解除后的 RESUME、STOP、RETURN_HOME、ESTOP |

### 3.2 不作为长期内部状态的概念

以下属于 Command Record、Execution Record 终态或外部状态，不作为 Manager 长期运行状态：

- VALIDATING；
- QUEUED；
- ACCEPTED；
- REJECTED；
- SUCCEEDED；
- FAILED；
- CANCELED；
- EMERGENCY_STOPPED；
- RETURN_HOME。

RETURN_HOME 是 mission 类型，不复制一套独立状态机。

### 3.3 cancel_intent

| 值 | 语义 |
|---|---|
| NONE | 当前无取消流程 |
| PAUSE | 暂停当前 execution |
| STOP | 停止当前 execution |
| RETURN_HOME | 结束旧 execution 后启动返航 |
| SAFETY_BLOCK | 因 Safety/Adapter 不可信而阻断 |
| ESTOP | 急停后的后台导航清理 |
| REPLAN | 明确批准的重规划 |

### 3.4 pending_outcome

`FINALIZING` 或 `CANCELING` 必须保存待收口结果：

- SUCCEEDED；
- FAILED；
- CANCELED；
- PAUSED；
- SAFETY_BLOCKED；
- EMERGENCY_STOPPED；
- START_RETURN_HOME；
- START_REPLAN。

### 3.5 cleanup_flags

异步收口至少跟踪：

- `navigation_submitted`
- `navigation_cancel_required`
- `navigation_cancel_confirmed`
- `lease_was_active`
- `lease_release_required`
- `lease_release_confirmed`
- `cleanup_failed`

只有当前流程所需的全部确认完成后，才允许进入 PAUSED、IDLE、新 generation 或终态。

---

## 4. External TaskStatus State Enumeration

冻结以下对外简化状态：

```text
uint8 STATE_UNKNOWN=0
uint8 STATE_IDLE=1
uint8 STATE_ACCEPTED=2
uint8 STATE_REJECTED=3
uint8 STATE_QUEUED=4
uint8 STATE_WAITING_TARGET=5
uint8 STATE_PREPARING=6
uint8 STATE_NAVIGATING=7
uint8 STATE_PAUSING=8
uint8 STATE_PAUSED=9
uint8 STATE_CANCELING=10
uint8 STATE_RETURNING_HOME=11
uint8 STATE_SAFETY_BLOCKED=12
uint8 STATE_SUCCEEDED=13
uint8 STATE_CANCELED=14
uint8 STATE_FAILED=15
uint8 STATE_EMERGENCY_STOPPED=16
```

### 4.1 内部到外部映射

| 内部条件 | 外部状态 |
|---|---|
| PREPARING_GOAL、NAVIGATION_STARTING、LEASE_ACQUIRING | STATE_PREPARING |
| EXECUTING 且 mission_kind 不是 RETURN_HOME | STATE_NAVIGATING |
| 活动 mission_kind=RETURN_HOME | STATE_RETURNING_HOME |
| CANCELING 且 cancel_intent=PAUSE | STATE_PAUSING |
| CANCELING 且 cancel_intent=STOP/RETURN_HOME/REPLAN | STATE_CANCELING |
| CANCELING 且 cancel_intent=SAFETY_BLOCK | STATE_SAFETY_BLOCKED |
| FINALIZING | 保持最近的非终态，使用 reason_code 表示正在安全收口 |
| PAUSED | STATE_PAUSED |
| SAFETY_BLOCKED | STATE_SAFETY_BLOCKED |
| Manager Mode=EMERGENCY_LATCHED | STATE_EMERGENCY_STOPPED |
| Execution Record 终态 | 对应 SUCCEEDED/CANCELED/FAILED/EMERGENCY_STOPPED |

不得把全部内部状态原样暴露给 HMI。

---

## 5. TaskStatus Scope

候选增加：

```text
uint8 SCOPE_UNKNOWN=0
uint8 SCOPE_COMMAND=1
uint8 SCOPE_EXECUTION=2
uint8 status_scope
```

### 5.1 SCOPE_COMMAND

用于命令处理结果：

- ACCEPTED；
- REJECTED；
- QUEUED；
- SUCCEEDED；
- FAILED；
- CANCELED。

字段语义：

- `command_id`：当前被处理的命令；
- `task_id`：该命令的 task_id；
- `execution_id`：受影响的 execution；尚不存在时为空。

内部 Command Record 映射：

| Command Record | 外部状态 |
|---|---|
| ACCEPTED | STATE_ACCEPTED |
| REJECTED | STATE_REJECTED |
| QUEUED | STATE_QUEUED |
| APPLIED/TERMINAL 成功 | STATE_SUCCEEDED |
| TERMINAL 失败 | STATE_FAILED |
| 已排队后过期/被 STOP、RETURN_HOME、ESTOP 清除 | STATE_CANCELED |

### 5.2 SCOPE_EXECUTION

用于 mission 生命周期：

- WAITING_TARGET；
- PREPARING；
- NAVIGATING；
- PAUSING；
- PAUSED；
- CANCELING；
- RETURNING_HOME；
- SAFETY_BLOCKED；
- SUCCEEDED；
- CANCELED；
- FAILED；
- EMERGENCY_STOPPED。

字段语义：

- `command_id`：创建该 mission 的原始普通 mission command_id；
- `task_id`：该 mission 的原始 task_id；
- `execution_id`：当前 execution_id。

### 5.3 双重发布

控制命令成功后通常发布：

1. 一条 SCOPE_COMMAND 终态；
2. 一条受影响 execution 的 SCOPE_EXECUTION 状态。

例如 PAUSE 完成：

- PAUSE command → SCOPE_COMMAND STATE_SUCCEEDED；
- 原 mission → SCOPE_EXECUTION STATE_PAUSED。

---

## 6. ID 与 Generation 规则

### 6.1 command_id

- 由 APP/语音 Bridge 产生；
- 用于请求幂等；
- 不同 command_id 可以执行相同 task_id；
- 控制命令也必须有 command_id。

### 6.2 execution_id

- 普通任务从队列激活时创建；
- QUEUED 阶段可以为空；
- RETURN_HOME 创建独立 execution_id；
- PAUSE/RESUME 不更换 execution_id；
- STOP 后旧 execution_id 永久终止；
- ESTOP 后旧 execution_id 永久终止；
- 任务终态后相同目标需要新的 execution_id 才能再次执行。

### 6.3 generation

- 在同一 execution_id 内单调递增；
- 首次导航提交使用第一代；
- RESUME 创建新 generation；
- 经批准的 REPLAN 创建新 generation；
- 相同 generation 只允许 submit 一次；
- 新 execution 使用独立 generation 生命周期。

### 6.4 回调隔离

只有同时满足以下条件的 Navigation Snapshot 或异步回调才允许改变当前状态：

- `execution_id == active_execution_id`
- `generation == active_generation`

其他回调：

- 记录为 stale callback；
- 不修改当前 handle；
- 不发布当前任务终态；
- 不释放或续期当前任务 Lease；
- 不覆盖当前 failure_reason。

---

## 7. 普通任务队列

| 规则 | 冻结语义 |
|---|---|
| 活动 execution | 最多 1 个 |
| 调度 | FIFO |
| 默认容量 | 10，后续作为参数 |
| 控制命令 | 不进入普通队列 |
| 相同 task_id | 不同 command_id 可分别排队 |
| PAUSE/RESUME | 不清空、不重排队列 |
| STOP | 清空队列 |
| RETURN_HOME | 校验 home_pose 成功后清空队列 |
| ESTOP | 立即清空队列 |
| EMERGENCY_LATCHED | 拒绝普通 mission |
| SUCCEEDED | 可推进下一条有效任务 |
| 局部失败 | 由 failure_disposition 决定 |
| 系统/安全阻断 | HOLD_QUEUE |

### 7.1 队列项过期

队列项继续使用原始：

```text
header.stamp + valid_for
```

判断有效期。

已 ACCEPTED/QUEUED 的命令在激活前过期时：

- 不创建 execution_id；
- 不执行；
- 发布 SCOPE_COMMAND STATE_CANCELED；
- reason_code 表示 QUEUE_ITEM_EXPIRED。

不使用 STATE_REJECTED，因为该请求此前已经被接受。

### 7.2 failure_disposition

| 值 | 语义 |
|---|---|
| CONTINUE_QUEUE | 当前 execution 收口后继续下一任务 |
| HOLD_QUEUE | 保留队列但不自动推进 |

---

## 8. 普通 Mission 生命周期

```text
TaskCommand
→ Command Gateway
→ Validator
→ command_id Deduplicator
→ SCOPE_COMMAND ACCEPTED
→ 直接激活或 SCOPE_COMMAND QUEUED
→ 激活时创建 execution_id
→ WAITING_TARGET 或 PREPARING_GOAL
→ 创建 generation
→ submit_goal(execution_id, generation, InternalGoal)
→ NAVIGATION_STARTING
→ NAV_GOAL_ACCEPTED
→ LEASE_ACQUIRING
→ acquire(execution_id)
→ LEASE_ACQUIRED
→ EXECUTING
→ NAV_SUCCEEDED 或 NAV_FAILED
→ FINALIZING
→ release(execution_id)
→ LEASE_RELEASED
→ 写入 execution 终态
→ IDLE 或激活下一队列任务
```

冻结规则：

- 未 goal_accepted 前不得 acquire Lease；
- 未 Lease 成功前不得进入 EXECUTING；
- 导航终态后必须进入 FINALIZING；
- release 未确认前不得发布完全收口的 execution 终态；
- release 失败不得伪装成 SUCCEEDED/FAILED 已安全收口；
- 真实 `/goal_pose` 当前没有 accepted/cancel/result 语义，这只是 Navigation Adapter 抽象合同。

---

## 9. 视觉目标任务

### 9.1 无有效目标

- 进入 WAITING_TARGET；
- 不提交导航；
- 不获取 Lease；
- 等待 Target Registry；
- wait_timeout 到达后 execution FAILED；
- failure_disposition 由 Reason Code 文档决定。

### 9.2 目标可用

- 选择稳定 target_id；
- 固化 map 目标；
- 创建 InternalGoal；
- 进入普通导航流程。

### 9.3 执行中更新

- 普通位置更新不自动重发 `/goal_pose`；
- 单批数组缺失不等于立即删除目标；
- observation_state、valid_for 和 Registry timeout 共同决定失效。

### 9.4 v1.0 默认失效策略

#### accepted 前失效

- 若仍可选择有效目标：重新进入 PREPARING_GOAL，但不复用已 submit 的 generation；
- 若尚未 submit：可在当前 generation 创建前重新选择；
- 若已 submit：必须取消旧 generation 并等待确认后再决定；
- 无目标时回到 WAITING_TARGET。

#### EXECUTING 中 INVALID/EXPIRED

1. 设置 cancel_intent=REPLAN；
2. pending_outcome=FAILED；
3. 请求 Lease release；
4. 请求取消旧 generation；
5. 等待所需确认；
6. 默认将 execution 结束为目标失效 FAILED。

M1 默认不自动选择新目标。自动重选和新 generation 属于后续可配置策略。

---

## 10. Navigation 事件

| 事件 | 主要处理 |
|---|---|
| NAV_GOAL_ACCEPTED | NAVIGATION_STARTING → LEASE_ACQUIRING，调用 acquire |
| NAV_GOAL_REJECTED | 不获取 Lease，execution 进入 FAILED 收口 |
| NAV_ACCEPT_TIMEOUT | 同 NAV_GOAL_REJECTED |
| NAV_SUCCEEDED | EXECUTING → FINALIZING，pending_outcome=SUCCEEDED，release Lease |
| NAV_FAILED | EXECUTING → FINALIZING，pending_outcome=FAILED，release Lease |
| NAV_CANCEL_CONFIRMED | 标记 navigation_cancel_confirmed，检查 CANCELING 收口条件 |
| NAV_CANCEL_FAILED | cleanup_failed=true，进入 SAFETY_BLOCKED，HOLD_QUEUE |
| NAV_CANCEL_TIMEOUT | 同 NAV_CANCEL_FAILED |
| NAV_SNAPSHOT_STALE | 活动导航期间按系统阻断处理：release、cancel_intent=SAFETY_BLOCK、HOLD_QUEUE |
| NAV_STALE_CALLBACK | 仅记录并忽略，不改变任何当前状态 |

### 10.1 CANCELING 收口条件

若当前流程需要 cancel 和 release，只有同时满足：

```text
navigation_cancel_confirmed == true
lease_release_confirmed == true
```

才允许迁移。

若当前没有导航或没有 Lease，对应确认标志初始化为 true。

---

## 11. Safety Lease 事件

| 事件 | 处理 |
|---|---|
| LEASE_ACQUIRED | LEASE_ACQUIRING → EXECUTING |
| LEASE_ACQUIRE_FAILED | 设置 cancel_intent=SAFETY_BLOCK，取消已接受导航，收口后进入 SAFETY_BLOCKED |
| LEASE_RENEWED | 保持 EXECUTING |
| LEASE_RENEW_FAILED | 立即按 Lease 无效处理；best-effort release；取消导航；进入 SAFETY_BLOCKED |
| LEASE_RELEASED | 标记 lease_release_confirmed，检查 FINALIZING/CANCELING 收口条件 |
| LEASE_RELEASE_FAILED | cleanup_failed=true；不得发布正常终态；进入 SAFETY_BLOCKED；是否自动 ESTOP 留给 Reason Code/安全策略 |
| SAFETY_ESTOP_ACTIVE | 进入 ESTOP 流程 |
| SAFETY_ESTOP_CLEARED | 只更新 Safety Snapshot，不自动恢复 |
| SAFETY_STATUS_STALE | 不继续假设授权有效；release + cancel_intent=SAFETY_BLOCK；HOLD_QUEUE |

### 11.1 SAFETY_BLOCKED 退出

SAFETY_BLOCKED 不通过 RESET_ESTOP 解除，除非 Manager Mode 同时为 EMERGENCY_LATCHED。

NORMAL 模式下离开 SAFETY_BLOCKED：

- STOP：结束当前 execution；
- RETURN_HOME：满足安全前置条件后结束旧任务并启动返航；
- ESTOP：进入 EMERGENCY_LATCHED；
- RESUME：仅在阻断原因已明确消失、状态快照新鲜、无旧 Lease、无未收口导航时允许。

---

## 12. 控制命令矩阵

| 命令 | 合法 Mode | 合法 Execution State | 普通队列 | Lease | Navigation | 新 execution | 新 generation | 命令终态 |
|---|---|---|---|---|---|---|---|---|
| START_DEFAULT_CLEANING | NORMAL | IDLE：直接激活；忙碌：排队 | 可入队 | 激活后按标准流程 | 激活后提交 | 激活时创建 | 激活时创建 | ACCEPTED/QUEUED，execution 后续独立发布 |
| PAUSE | NORMAL | WAITING_TARGET、PREPARING_GOAL、NAVIGATION_STARTING、LEASE_ACQUIRING、EXECUTING、SAFETY_BLOCKED | 保留 | 有则 release | 有则 cancel | 否 | 否 | 收口完成后 SUCCEEDED |
| RESUME | NORMAL | PAUSED；SAFETY_BLOCKED 仅在阻断已解除时 | 保留 | accepted 后重新 acquire | 新 generation 单次提交 | 否 | 是 | accepted 后最终 SUCCEEDED/FAILED |
| STOP | NORMAL | 任意非急停状态；IDLE+空队列视为幂等 no-op | 清空 | 有则 release | 有则 cancel | 否 | 否 | 收口完成后 SUCCEEDED |
| RETURN_HOME | NORMAL | 任意非急停状态 | home_pose 校验成功后清空 | 旧任务 release；新任务 accepted 后 acquire | 旧任务 cancel；新任务 submit | 是 | 是 | 返航 execution 创建后 SUCCEEDED |
| ESTOP | 任意 | 任意 | 立即清空 | best-effort release | best-effort cancel | 否 | 否 | Safety 确认急停后 SUCCEEDED |
| RESET_ESTOP | EMERGENCY_LATCHED | emergency cleanup 已完成 | 保持空 | 必须无 Lease | 必须无活动导航 | 否 | 否 | Safety 确认清除后 SUCCEEDED |

重复 ESTOP 在 EMERGENCY_LATCHED 下作为幂等安全命令处理。

---

## 13. PAUSE

### 13.1 可暂停状态

- WAITING_TARGET；
- PREPARING_GOAL；
- NAVIGATION_STARTING；
- LEASE_ACQUIRING；
- EXECUTING；
- SAFETY_BLOCKED。

### 13.2 无导航、无 Lease

例如 WAITING_TARGET 或尚未 submit 的 PREPARING_GOAL：

1. PAUSE command ACCEPTED；
2. 确认无 pending navigation；
3. 确认无 Lease；
4. 进入 PAUSED；
5. PAUSE command SCOPE_COMMAND SUCCEEDED；
6. 原 mission SCOPE_EXECUTION PAUSED。

### 13.3 有导航或 Lease

1. PAUSE command ACCEPTED；
2. `cancel_intent=PAUSE`；
3. `pending_outcome=PAUSED`；
4. 立即请求 release（如需）；
5. 请求 cancel（如需）；
6. 进入 CANCELING；
7. 等待所有必需确认；
8. 进入 PAUSED；
9. 发布 command SUCCEEDED 和 execution PAUSED。

PAUSE 保留：

- execution_id；
- 原 mission command_id；
- task_id；
- 目标上下文；
- 普通等待队列。

重复 PAUSE：

- 相同 command_id：幂等重放；
- 新 command_id 且已 PAUSING/PAUSED：作为 no-op 成功，不重复发 cancel。

---

## 14. RESUME

1. 只允许 PAUSED，或已明确解除原因的 SAFETY_BLOCKED；
2. EMERGENCY_LATCHED 时拒绝；
3. 检查定位、Safety Snapshot、目标有效性、任务上下文；
4. 保持 execution_id；
5. generation + 1；
6. 单次 submit；
7. 等待 NAV_GOAL_ACCEPTED；
8. acquire Lease；
9. LEASE_ACQUIRED 后进入 EXECUTING；
10. 不恢复旧 generation。

RESUME command 在新 Goal 被接受并进入 LEASE_ACQUIRING 后可发布 SCOPE_COMMAND SUCCEEDED；后续 execution 状态由 SCOPE_EXECUTION 表达。

---

## 15. STOP

### 15.1 有活动 execution

1. STOP command ACCEPTED；
2. 清空普通队列，并对被清除的 queued commands 发布 SCOPE_COMMAND CANCELED；
3. `cancel_intent=STOP`；
4. `pending_outcome=CANCELED`；
5. 请求 release（如需）；
6. 请求 cancel（如需）；
7. 进入 CANCELING；
8. 所有必需确认完成后：
   - execution → CANCELED；
   - Manager → IDLE；
   - STOP command → SUCCEEDED。

### 15.2 只有队列、无活动 execution

- 清空队列；
- queued commands → SCOPE_COMMAND CANCELED；
- STOP command → SUCCEEDED；
- 保持 IDLE。

### 15.3 无任务、无队列

- 作为幂等 no-op 成功；
- 不发布 emergency_stop。

cancel/release 失败时，STOP 不得宣称完成，进入 SAFETY_BLOCKED。

---

## 16. RETURN_HOME

### 16.1 前置校验

在修改当前任务或队列之前，必须先确认：

- home_pose 已配置且合法；
- Manager Mode=NORMAL；
- 基础定位和 Safety 条件满足。

校验失败：

- RETURN_HOME command REJECTED；
- 当前 execution 和队列保持不变。

### 16.2 有活动 execution

1. RETURN_HOME command ACCEPTED；
2. 校验成功后清空普通队列；
3. `cancel_intent=RETURN_HOME`；
4. `pending_outcome=START_RETURN_HOME`；
5. release 旧 Lease；
6. cancel 旧 generation；
7. 等待必需确认；
8. 旧 execution → CANCELED；
9. 创建新的返航 execution_id；
10. 创建新 generation；
11. submit home_pose；
12. accepted 后 acquire 新 Lease；
13. execution 对外状态为 RETURNING_HOME；
14. 返航 execution 成功创建后 RETURN_HOME command → SUCCEEDED。

### 16.3 无活动 execution

- 清空普通队列；
- 直接创建返航 execution；
- 进入标准导航流程。

RETURN_HOME 不复用旧 execution_id。

---

## 17. ESTOP

1. 任何状态均可触发；
2. 立即发布 `emergency_stop=true`；
3. Manager Mode → EMERGENCY_LATCHED；
4. 立即清空普通队列；
5. 当前 execution 对外立即发布 EMERGENCY_STOPPED；
6. 终止旧 execution 的恢复资格；
7. best-effort release Lease；
8. best-effort cancel navigation；
9. 后台保留 emergency cleanup context，直到 cancel/release 收口或明确失败；
10. 不等待 cancel 完成才执行急停；
11. 不接受新的普通 mission、PAUSE、RESUME、STOP、RETURN_HOME；
12. 只允许重复 ESTOP 和 RESET_ESTOP。

ESTOP command 在 Safety Snapshot 确认急停已激活后发布 SCOPE_COMMAND SUCCEEDED。若当前真实接口无法确认，该确认能力属于 Mock 合同和未来结构化 Safety Adapter 未决项。

---

## 18. RESET ESTOP

只允许：

- source=APP 或 MOCK；
- Manager Mode=EMERGENCY_LATCHED；
- 外部急停条件已解除；
- Safety Snapshot 新鲜；
- `autonomous_enabled=false`；
- 无活动 Lease；
- 无活动 navigation；
- emergency cleanup 已收口；
- 普通队列为空。

流程：

1. RESET command ACCEPTED；
2. 发布 `emergency_stop=false`；
3. 等待 Safety Snapshot 确认清除；
4. Manager Mode → NORMAL；
5. Execution State → IDLE；
6. 不恢复旧任务或旧队列；
7. RESET command → SUCCEEDED。

失败或超时：

- 保持 EMERGENCY_LATCHED；
- command → FAILED；
- 不启动任何 mission。

---

## 19. Navigation 终态与安全收口

### 19.1 NAV_SUCCEEDED

1. EXECUTING → FINALIZING；
2. `pending_outcome=SUCCEEDED`；
3. 请求 release；
4. LEASE_RELEASED 后：
   - execution → SUCCEEDED；
   - 写 terminal cache；
   - 根据 failure_disposition/队列规则推进下一任务。

### 19.2 NAV_FAILED

1. EXECUTING → FINALIZING；
2. `pending_outcome=FAILED`；
3. 请求 release；
4. LEASE_RELEASED 后：
   - execution → FAILED；
   - 根据 Reason Code 选择 CONTINUE_QUEUE 或 HOLD_QUEUE。

### 19.3 LEASE_RELEASE_FAILED

- execution 不得发布正常安全收口终态；
- 进入 SAFETY_BLOCKED；
- 保存原 pending_outcome；
- HOLD_QUEUE；
- 是否自动触发软件 ESTOP 由 Reason Code/安全策略冻结。

---

## 20. Stale 与旧回调

### 20.1 NAV_STALE_CALLBACK

旧 execution/generation 的回调：

- 仅记录；
- 不改变当前状态；
- 不释放当前 Lease；
- 不清空当前 handle；
- 不发布当前终态。

### 20.2 NAV_SNAPSHOT_STALE

活动状态下 Navigation Snapshot 超时属于执行状态不可信：

1. HOLD_QUEUE；
2. 请求 release Lease；
3. `cancel_intent=SAFETY_BLOCK`；
4. 请求 cancel；
5. 收口后进入 SAFETY_BLOCKED。

IDLE、WAITING_TARGET、PAUSED 且无活动导航时，仅记录诊断。

### 20.3 SAFETY_STATUS_STALE

任何依赖自主授权的状态下：

- 不继续假设 Lease 有效；
- HOLD_QUEUE；
- best-effort release；
- cancel_intent=SAFETY_BLOCK；
- 取消导航；
- 进入 SAFETY_BLOCKED。

---

## 21. 超时与时间源

时间分为两类。

### 21.1 语义时间：ROS Clock

用于与仿真任务语义一致的有效期：

| 项目 | 时间源 |
|---|---|
| TaskCommand valid_for | ROS Clock |
| queued command expiry | ROS Clock |
| CleaningTarget valid_for | ROS Clock |
| target wait timeout | ROS Clock |
| execution semantic timeout | ROS Clock |

### 21.2 运行看门狗：monotonic

用于确保 `/clock` 暂停时不会无限等待：

| 项目 | 时间源 |
|---|---|
| Navigation accept timeout | monotonic |
| Navigation cancel timeout | monotonic |
| Lease acquire timeout | monotonic |
| Lease renew deadline | monotonic |
| Lease release timeout | monotonic |
| PerceptionHealth arrival stale timeout | monotonic |
| Navigation Snapshot stale timeout | monotonic |
| Safety Snapshot stale timeout | monotonic |
| emergency clear confirmation timeout | monotonic |

冻结规则：

- M1 Mock 必须使用可注入时钟；
- 测试不得依赖真实 sleep；
- ROS `/clock` 暂停时，当前真实 Safety Lease 仍可能按墙钟到期；
- header 时间新鲜度与本地到达看门狗可同时检查；
- 默认数值作为参数，不在本文写死。

---

## 22. 状态发布与数值语义

| 字段 | 冻结规则 |
|---|---|
| `TaskStatus.progress` | 有效 0.0～1.0；不可用为 -1.0 |
| `TaskStatus.remaining_distance_m` | 有效 >=0；不可用为 -1.0 |
| `message` | 只供人读 |
| 程序控制 | 只使用 state、reason_code、status_scope 和 ID |
| REJECTED execution_id | 未创建 execution 时为空 |
| QUEUED execution_id | 为空 |
| 活动 execution | 必须携带 execution_id |
| terminal cache | 缓存 Command Scope 和 Execution Scope 终态，用于幂等重放 |

Terminal 状态首次生成时发布一次；后续相同 command_id 的幂等重放可以再次发布同一缓存状态，但不得重新执行副作用。

---

## 23. 完整状态转换表

### 23.1 命令接收与队列

| Mode | Current | Event | Guard | Actions | Next | External | Queue |
|---|---|---|---|---|---|---|---|
| NORMAL | 任意 | 新命令 | command_id 同内容已存在 | 重放缓存 | 不变 | 原 Command Scope 状态 | 不变 |
| NORMAL | 任意 | 新命令 | command_id 冲突 | 拒绝 | 不变 | COMMAND REJECTED | 不变 |
| NORMAL | 任意 | 新命令 | 已过期/版本非法/来源非法 | 拒绝 | 不变 | COMMAND REJECTED | 不变 |
| NORMAL | IDLE | 普通 mission | 校验通过 | 激活、创建 execution_id | WAITING_TARGET 或 PREPARING_GOAL | COMMAND ACCEPTED | 不变 |
| NORMAL | 非 IDLE | 普通 mission | 队列未满 | 入队 | 不变 | COMMAND QUEUED | 增加 |
| NORMAL | 非 IDLE | 普通 mission | 队列已满 | 拒绝 | 不变 | COMMAND REJECTED | 不变 |
| NORMAL | 任意 | 队列项过期 | 尚未激活 | 删除 | 不变 | COMMAND CANCELED | 减少 |
| EMERGENCY_LATCHED | 任意 | 普通 mission | — | 拒绝 | 不变 | COMMAND REJECTED | 保持空 |

### 23.2 目标与准备

| Mode | Current | Event | Guard | Actions | Next | External | Queue |
|---|---|---|---|---|---|---|---|
| NORMAL | WAITING_TARGET | TARGET_AVAILABLE | 有效 | 选择并固化目标 | PREPARING_GOAL | EXECUTION PREPARING | 不变 |
| NORMAL | WAITING_TARGET | TARGET_WAIT_TIMEOUT | — | execution 失败收口 | IDLE | EXECUTION FAILED | 按 disposition |
| NORMAL | PREPARING_GOAL | GOAL_PREPARED | 有效 | generation++，单次 submit | NAVIGATION_STARTING | EXECUTION PREPARING | 不变 |
| NORMAL | PREPARING_GOAL | GOAL_PREPARE_FAILED | — | execution 失败 | IDLE | EXECUTION FAILED | 按 disposition |

### 23.3 Navigation 与 Lease

| Mode | Current | Event | Guard | Actions | Next | External | Queue |
|---|---|---|---|---|---|---|---|
| NORMAL | NAVIGATION_STARTING | NAV_GOAL_ACCEPTED | 当前 execution/generation | acquire Lease | LEASE_ACQUIRING | EXECUTION PREPARING | 不变 |
| NORMAL | NAVIGATION_STARTING | NAV_GOAL_REJECTED/ACCEPT_TIMEOUT | 当前 generation | execution 失败 | IDLE | EXECUTION FAILED | 按 disposition |
| NORMAL | LEASE_ACQUIRING | LEASE_ACQUIRED | 当前 owner | — | EXECUTING | EXECUTION NAVIGATING/RETURNING_HOME | 不变 |
| NORMAL | LEASE_ACQUIRING | LEASE_ACQUIRE_FAILED | — | cancel intent SAFETY_BLOCK | CANCELING | EXECUTION SAFETY_BLOCKED | HOLD |
| NORMAL | EXECUTING | NAV_SUCCEEDED | 当前 generation | release，pending SUCCEEDED | FINALIZING | 保持活动状态 | 不变 |
| NORMAL | EXECUTING | NAV_FAILED | 当前 generation | release，pending FAILED | FINALIZING | 保持活动状态 | 按 disposition 待定 |
| NORMAL | FINALIZING | LEASE_RELEASED | pending SUCCEEDED | 写终态 | IDLE | EXECUTION SUCCEEDED | 推进 |
| NORMAL | FINALIZING | LEASE_RELEASED | pending FAILED | 写终态 | IDLE | EXECUTION FAILED | 按 disposition |
| NORMAL | FINALIZING | LEASE_RELEASE_FAILED | — | HOLD | SAFETY_BLOCKED | EXECUTION SAFETY_BLOCKED | HOLD |
| NORMAL | 活动导航状态 | NAV_SNAPSHOT_STALE | 当前 generation | release+cancel | CANCELING | SAFETY_BLOCKED | HOLD |
| NORMAL | EXECUTING | LEASE_RENEW_FAILED/SAFETY_STATUS_STALE | — | release+cancel | CANCELING | SAFETY_BLOCKED | HOLD |
| 任意 | 任意 | 旧 generation 回调 | ID 不匹配 | 忽略 | 不变 | 无 | 不变 |

### 23.4 CANCELING

| Mode | Current | Event | Guard | Actions | Next | External | Queue |
|---|---|---|---|---|---|---|---|
| NORMAL | CANCELING | cancel/release 全部确认 | intent=PAUSE | 保留 execution | PAUSED | EXECUTION PAUSED | 保留 |
| NORMAL | CANCELING | 全部确认 | intent=STOP | execution CANCELED | IDLE | EXECUTION CANCELED | 清空 |
| NORMAL | CANCELING | 全部确认 | intent=RETURN_HOME | 旧 execution CANCELED，创建返航 execution | PREPARING_GOAL | 旧 CANCELED + 新 PREPARING | 清空 |
| NORMAL | CANCELING | 全部确认 | intent=SAFETY_BLOCK | 保存上下文 | SAFETY_BLOCKED | EXECUTION SAFETY_BLOCKED | HOLD |
| NORMAL | CANCELING | 全部确认 | intent=REPLAN | 按策略结束或创建新 generation | SAFETY_BLOCKED/ PREPARING_GOAL | FAILED 或 PREPARING | HOLD/不变 |
| EMERGENCY_LATCHED | CANCELING | 全部确认 | intent=ESTOP | cleanup complete | IDLE | 已发布 EMERGENCY_STOPPED | 空 |
| 任意 | CANCELING | CANCEL_FAILED/TIMEOUT | — | cleanup_failed | SAFETY_BLOCKED 或保持 LATCHED | FAILED/SAFETY_BLOCKED | HOLD/空 |
| 任意 | CANCELING | RELEASE_FAILED/TIMEOUT | — | cleanup_failed | SAFETY_BLOCKED 或保持 LATCHED | SAFETY_BLOCKED | HOLD/空 |

### 23.5 控制命令

| Mode | Current | Event | Guard | Actions | Next | External | Queue |
|---|---|---|---|---|---|---|---|
| NORMAL | 可暂停状态 | PAUSE | — | 无资源则直达；否则 release+cancel | PAUSED/CANCELING | COMMAND ACCEPTED，后续 SUCCEEDED | 保留 |
| NORMAL | PAUSED | RESUME | 校验通过 | new generation submit | NAVIGATION_STARTING | COMMAND SUCCEEDED | 保留 |
| NORMAL | SAFETY_BLOCKED | RESUME | 阻断已解除且 cleanup 完成 | new generation submit | NAVIGATION_STARTING | COMMAND SUCCEEDED | 保留 |
| NORMAL | 任意 | STOP | — | clear queue，release+cancel | CANCELING/IDLE | COMMAND ACCEPTED，后续 SUCCEEDED | 清空 |
| NORMAL | 任意 | RETURN_HOME | home_pose 无效 | 拒绝，不改当前任务 | 不变 | COMMAND REJECTED | 不变 |
| NORMAL | 任意 | RETURN_HOME | home_pose 有效 | clear queue，收口旧任务 | CANCELING/PREPARING_GOAL | COMMAND ACCEPTED，后续 SUCCEEDED | 清空 |
| 任意 | 任意 | ESTOP | — | emergency true，clear queue，cleanup | LATCHED + cleanup | COMMAND ACCEPTED，确认后 SUCCEEDED | 清空 |
| EMERGENCY_LATCHED | 任意 | RESET_ESTOP | 前置条件满足 | emergency false，等待确认 | EMERGENCY_LATCHED | COMMAND ACCEPTED | 空 |
| EMERGENCY_LATCHED | 任意 | SAFETY_ESTOP_CLEARED | reset pending 且 cleanup 完成 | mode NORMAL | IDLE | COMMAND SUCCEEDED + STATE_IDLE | 空 |
| EMERGENCY_LATCHED | 任意 | RESET_ESTOP | 条件不满足 | 拒绝 | 不变 | COMMAND REJECTED | 空 |
| EMERGENCY_LATCHED | 任意 | ESTOP | — | 幂等保持急停 | 不变 | COMMAND SUCCEEDED | 空 |

---

## 24. 状态机不变量

1. 同时最多一个活动 execution；
2. 同时最多一个活动 generation；
3. 同时最多一个有效 Safety Lease owner；
4. generation 未 accepted 前不 acquire Lease；
5. Lease 未成功前不进入 EXECUTING；
6. cancel 未 confirmed 前不提交新 generation；
7. 需要 release 时，release 未 confirmed 前不完成安全收口；
8. 旧 execution/generation 回调不修改当前状态；
9. 没有有效 Lease 时不得认为车辆处于自主执行；
10. ESTOP 优先于所有普通事件；
11. EMERGENCY_LATCHED 只允许 RESET_ESTOP 和重复 ESTOP；
12. ESTOP 解除后不恢复旧任务或队列；
13. STOP、RETURN_HOME、ESTOP 清空普通队列；
14. RETURN_HOME 必须先校验 home_pose，再破坏当前任务或队列；
15. PAUSE 保留普通队列；
16. Mission Manager 不周期性重发 Goal；
17. Mission Manager 和 Navigation Adapter 不直接调用 FollowPath；
18. Mission Manager 不发布 `/cmd_vel`；
19. Terminal execution 状态只首次生成一次，幂等重放除外；
20. Command Scope APPLIED/TERMINAL 成功对外映射为 STATE_SUCCEEDED，不使用未定义的外部 APPLIED 状态；
21. 活动导航 Snapshot stale 必须安全阻断，不能只记录；
22. 所有异步清理必须由 cleanup_flags 明确收口。

---

## 25. 需要回填接口文档的事项

后续回填 `docs/mission_manager_interface.md`：

1. TaskStatus 增加 `status_scope`；
2. TaskStatus.state 使用本文 0～16 枚举；
3. `progress` 不可用值=-1.0；
4. `remaining_distance_m` 不可用值=-1.0；
5. SCOPE_COMMAND 与 SCOPE_EXECUTION 字段语义；
6. command terminal cache 与幂等重放；
7. queued command 过期对外使用 STATE_CANCELED；
8. Command Record APPLIED/TERMINAL 成功对外使用 STATE_SUCCEEDED；
9. `navigation_active` 在 cleanup pending 期间仍应保持 true，直到导航确认结束；
10. operational watchdog 使用 monotonic，可注入时钟。

本阶段不直接修改接口文档。

---

## 26. 当前未决项

| 项目 | 状态 |
|---|---|
| 精确 reason_code 数值 | 留给 Reason Code 文档 |
| timeout 默认值 | 参数，待测试计划冻结候选 |
| failure_disposition 与 reason_code 映射 | 留给 Reason Code 文档 |
| 真实 Adapter 如何获得 accepted/cancel/result | 等待 A* 与 Path Bridge 修复 |
| Lease release 失败是否自动触发软件 ESTOP | 待安全策略冻结 |
| 视觉目标显著偏移阈值 | 未决 |
| 自动重选视觉目标策略 | M1 默认关闭 |
| RESET_ESTOP 的真实 Safety 确认来源 | 当前 String 状态不足，待结构化 Adapter |
| ESTOP 后 cleanup 失败的人工恢复流程 | 待安全运维策略 |
| RETURN_HOME 在 Safety 暂不可用时是拒绝还是建立 HOLD 任务 | 待 Reason Code/产品策略 |

---

## 27. 后续顺序

1. 审查并冻结本状态机文档；
2. 创建 `mission_manager_reason_codes.md`；
3. 回填 `mission_manager_interface.md`；
4. 创建 `mission_manager_test_plan.md`；
5. 修订 `cleannav_interfaces` 候选包；
6. M0 总审查；
7. M1 Mock 包骨架。
