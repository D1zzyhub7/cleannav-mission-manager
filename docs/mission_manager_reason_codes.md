# CleanNav Mission Manager M0 Reason Code 设计

> **状态：** M0-4 Reason Code 冻结候选。
> **边界：** 本文冻结 `TaskStatus.reason_code` 的数值空间、命名、Scope 语义、默认处置策略和接口回填要求；尚未修改 `cleannav_interfaces`，尚未创建业务代码。
> **依赖基线：** `docs/mission_manager_architecture.md`、`docs/mission_manager_interface.md`、`docs/mission_manager_state_machine.md`。

---

## 1. 文档目的

`TaskStatus.state` 表示“当前处于什么生命周期状态”，`reason_code` 表示“为什么处于该状态，或为什么某条命令被接受、拒绝、取消、失败或安全阻断”。

冻结目标：

1. 为 APP、离线语音、Mock 测试和 Mission Manager 提供稳定、可编程判断的原因码；
2. 区分 Command Scope 与 Execution Scope；
3. 将底层 A*、Path Bridge、Navigation Adapter 和 Safety Adapter 的实现细节翻译为稳定的 Mission Manager 语义；
4. 冻结默认 `failure_disposition`，决定任务失败后是继续队列还是保持阻断；
5. 禁止 HMI 通过解析 `message` 文本驱动逻辑；
6. 为后续 `TaskStatus.msg`、测试矩阵和 M1 实现提供统一常量。

---

## 2. 核心原则

### 2.1 state 与 reason_code 分工

| 字段 | 职责 |
|---|---|
| `state` | 生命周期状态，例如 ACCEPTED、QUEUED、NAVIGATING、FAILED |
| `reason_code` | 状态原因，例如 COMMAND_VALID、QUEUE_FULL、NAV_GOAL_REJECTED、LEASE_RENEW_FAILED |
| `message` | 人类可读补充，不是稳定接口 |

示例：

```text
state=STATE_REJECTED
reason_code=REASON_COMMAND_EXPIRED
```

```text
state=STATE_SAFETY_BLOCKED
reason_code=REASON_LEASE_RENEW_FAILED
```

```text
state=STATE_SUCCEEDED
reason_code=REASON_EXECUTION_COMPLETED
```

### 2.2 稳定性规则

- 所有正式 reason code 使用非负 `int32`；
- `0` 永久保留为 `REASON_NONE`；
- 已发布的数值不得改变含义；
- 已废弃的数值不得复用；
- 新增原因码只在对应区间末尾追加；
- 数值名称是程序合同，`message` 文本可以调整；
- 底层原始 Action 状态码、异常字符串和日志文本不得直接作为 Mission Manager reason code；
- 适配器必须把底层结果翻译为本文定义的稳定语义；
- 未识别的底层错误映射为相应类别的通用错误，原始信息只写入诊断或 `message`。

### 2.3 Scope 规则

`reason_code` 必须结合 `TaskStatus.status_scope` 解释：

- `SCOPE_COMMAND`：解释一条 HMI 命令的受理和处理结果；
- `SCOPE_EXECUTION`：解释一个 mission execution 的生命周期状态。

同一数值可以在两个 Scope 中都合法，但本文明确推荐的 Scope 不得被随意改变。

---

## 3. 数值区间

| 区间 | 类别 |
|---:|---|
| 0 | 无附加原因 |
| 1–99 | 正常生命周期、控制命令成功与幂等结果 |
| 100–199 | 命令格式、版本、来源、状态与权限校验 |
| 200–299 | Task Catalog、队列和任务配置 |
| 300–399 | 感知健康、CleaningTarget 与目标选择 |
| 400–499 | Navigation Adapter、规划、执行与取消 |
| 500–599 | Safety、Lease 与 Emergency Stop |
| 600–699 | 超时、stale、时钟与运行看门狗 |
| 700–799 | 收口、状态机、不变量与 Adapter 合同 |
| 900–999 | 未分类内部错误 |
| 800–899 | 预留，当前不使用 |
| 1000 及以上 | 未来扩展，未经接口变更不得使用 |

---

## 4. 处置属性

Reason Code 在 Mission Manager 内部关联以下策略属性。这些属性当前不进入 `TaskStatus.msg`，由实现中的静态映射表维护。

### 4.1 severity

| 值 | 含义 |
|---|---|
| INFO | 正常生命周期或 no-op |
| WARNING | 可恢复异常、请求拒绝或局部任务失败 |
| ERROR | 当前 execution 失败，需要明确收口 |
| CRITICAL | Safety、状态一致性或系统可信度问题 |

### 4.2 failure_disposition

| 值 | 含义 |
|---|---|
| NOT_APPLICABLE | 非 execution 失败，或不涉及队列推进 |
| CONTINUE_QUEUE | 当前 execution 收口后允许自动激活下一条有效普通任务 |
| HOLD_QUEUE | 保留队列但禁止自动推进，等待人工或控制命令 |
| CLEAR_QUEUE | 清空普通队列 |

`CONTINUE_QUEUE` 与 `HOLD_QUEUE` 是状态机冻结的 execution 失败策略。`NOT_APPLICABLE` 和 `CLEAR_QUEUE` 仅用于完整表达命令拒绝、急停和控制命令行为，不改变状态机既有不变量。

### 4.3 retry_policy

| 值 | 含义 |
|---|---|
| NO_RETRY | 不自动重试 |
| NEW_COMMAND | 只能由新的 command_id 再次发起 |
| USER_RESUME | 阻断原因消失后允许 RESUME |
| INTERNAL_RETRY_NOT_ENABLED | 未来可以设计内部重试，但 M1 默认禁用 |

Mission Manager 不得通过周期性重发 `/goal_pose` 实现重试。

---

## 5. 正常生命周期原因码（1–99）

| 数值 | 常量 | 推荐 Scope | 典型 state | 语义 |
|---:|---|---|---|---|
| 1 | `REASON_COMMAND_RECEIVED` | COMMAND | ACCEPTED | 命令已收到并进入处理 |
| 2 | `REASON_COMMAND_VALID` | COMMAND | ACCEPTED | 命令校验通过 |
| 3 | `REASON_COMMAND_QUEUED` | COMMAND | QUEUED | 普通 mission 已进入 FIFO |
| 4 | `REASON_COMMAND_APPLIED` | COMMAND | SUCCEEDED | 控制命令副作用已完成并收口 |
| 5 | `REASON_IDEMPOTENT_REPLAY` | COMMAND | 原缓存状态 | 相同 command_id 与相同语义的幂等重放 |
| 6 | `REASON_NO_OP_ALREADY_SATISFIED` | COMMAND | SUCCEEDED | STOP/PAUSE/ESTOP 等目标状态已经满足，无需重复副作用 |
| 7 | `REASON_EXECUTION_ACTIVATED` | EXECUTION | PREPARING | 队列任务已激活并创建 execution_id |
| 8 | `REASON_WAITING_FOR_TARGET` | EXECUTION | WAITING_TARGET | 视觉 mission 正在等待有效目标 |
| 9 | `REASON_GOAL_PREPARING` | EXECUTION | PREPARING | 正在解析和校验 InternalGoal |
| 10 | `REASON_NAVIGATION_STARTING` | EXECUTION | PREPARING | Goal 已单次提交，等待 Adapter 接受 |
| 11 | `REASON_LEASE_ACQUIRING` | EXECUTION | PREPARING | 导航已接受，正在获取 Safety Lease |
| 12 | `REASON_NAVIGATION_ACTIVE` | EXECUTION | NAVIGATING | 普通 mission 正在导航 |
| 13 | `REASON_RETURN_HOME_ACTIVE` | EXECUTION | RETURNING_HOME | 返航 execution 正在导航 |
| 14 | `REASON_PAUSE_REQUESTED` | EXECUTION | PAUSING | 正在释放 Lease 和取消导航 |
| 15 | `REASON_EXECUTION_PAUSED` | EXECUTION | PAUSED | 暂停收口完成 |
| 16 | `REASON_CANCEL_REQUESTED` | EXECUTION | CANCELING | 正在取消当前 generation |
| 17 | `REASON_SAFETY_BLOCK_ACTIVE` | EXECUTION | SAFETY_BLOCKED | 当前 execution 因 Safety/可信度问题阻断 |
| 18 | `REASON_EXECUTION_COMPLETED` | EXECUTION | SUCCEEDED | execution 成功并完成安全收口 |
| 19 | `REASON_EXECUTION_CANCELED_BY_STOP` | EXECUTION | CANCELED | STOP 导致 execution 取消 |
| 20 | `REASON_EXECUTION_CANCELED_BY_RETURN_HOME` | EXECUTION | CANCELED | RETURN_HOME 终止旧 execution |
| 21 | `REASON_EXECUTION_CANCELED_BY_ESTOP` | EXECUTION | EMERGENCY_STOPPED | ESTOP 终止旧 execution |
| 22 | `REASON_QUEUE_ADVANCED` | EXECUTION | PREPARING | 上一任务收口后自动激活下一有效队列任务 |
| 23 | `REASON_RESUME_STARTED` | COMMAND | SUCCEEDED | RESUME 已创建新 generation 并开始启动 |
| 24 | `REASON_STOP_COMPLETED` | COMMAND | SUCCEEDED | STOP 已清队列并完成必要收口 |
| 25 | `REASON_RETURN_HOME_STARTED` | COMMAND | SUCCEEDED | 返航 execution 已成功创建 |
| 26 | `REASON_ESTOP_ASSERTED` | COMMAND | SUCCEEDED | Safety 已确认急停激活 |
| 27 | `REASON_ESTOP_CLEARED` | COMMAND | SUCCEEDED | Safety 已确认急停解除 |
| 28 | `REASON_QUEUED_COMMAND_CANCELED` | COMMAND | CANCELED | 队列项被 STOP、RETURN_HOME 或 ESTOP 清除 |
| 29 | `REASON_QUEUED_COMMAND_EXPIRED` | COMMAND | CANCELED | 已接受的队列项在激活前过期 |
| 30 | `REASON_CLEANUP_IN_PROGRESS` | EXECUTION | 原非终态 | 导航终态已产生，正在等待安全收口 |

默认属性：

- severity：INFO；
- disposition：NOT_APPLICABLE；
- retry：NO_RETRY。

例外：

- 18 成功后按队列规则推进；
- 19、20、21、28、29 不自动重新执行原命令。

---

## 6. 命令校验原因码（100–199）

| 数值 | 常量 | 推荐 Scope | 典型 state | 默认处置 |
|---:|---|---|---|---|
| 100 | `REASON_INTERFACE_VERSION_UNSUPPORTED` | COMMAND | REJECTED | 拒绝，不改当前 execution/queue |
| 101 | `REASON_COMMAND_ID_MISSING` | COMMAND | REJECTED | 拒绝 |
| 102 | `REASON_COMMAND_ID_INVALID` | COMMAND | REJECTED | 拒绝 |
| 103 | `REASON_DUPLICATE_COMMAND_CONFLICT` | COMMAND | REJECTED | 拒绝；同 ID 语义冲突 |
| 104 | `REASON_COMMAND_EXPIRED` | COMMAND | REJECTED | 拒绝 |
| 105 | `REASON_VALID_FOR_INVALID` | COMMAND | REJECTED | 拒绝 |
| 106 | `REASON_SOURCE_UNKNOWN` | COMMAND | REJECTED | 拒绝 |
| 107 | `REASON_SOURCE_NOT_ALLOWED` | COMMAND | REJECTED | 拒绝 |
| 108 | `REASON_TASK_ID_UNKNOWN` | COMMAND | REJECTED | 拒绝 |
| 109 | `REASON_TASK_DISABLED` | COMMAND | REJECTED | 拒绝 |
| 110 | `REASON_CONFIDENCE_TOO_LOW` | COMMAND | REJECTED | 拒绝或未来确认流程 |
| 111 | `REASON_FIELD_INVALID` | COMMAND | REJECTED | 拒绝 |
| 112 | `REASON_NONFINITE_VALUE` | COMMAND | REJECTED | 拒绝 |
| 113 | `REASON_COMMAND_NOT_ALLOWED_IN_MODE` | COMMAND | REJECTED | 例如 LATCHED 下普通 mission |
| 114 | `REASON_COMMAND_NOT_ALLOWED_IN_STATE` | COMMAND | REJECTED | 例如 IDLE 下 RESUME |
| 115 | `REASON_CONFIRMATION_REQUIRED` | COMMAND | REJECTED | 当前确认协议未完成 |
| 116 | `REASON_CONFIRMATION_FAILED` | COMMAND | REJECTED | 拒绝 |
| 117 | `REASON_RESET_ESTOP_SOURCE_NOT_ALLOWED` | COMMAND | REJECTED | 语音不得解除急停 |
| 118 | `REASON_INTERNAL_SOURCE_FORBIDDEN_ON_HMI_TOPIC` | COMMAND | REJECTED | 公共 HMI Topic 不接受 INTERNAL |
| 119 | `REASON_SPATIAL_GOAL_NOT_SUPPORTED_IN_VERSION` | COMMAND | REJECTED | v1.0 不启用 SpatialGoalRequest |
| 120 | `REASON_SPATIAL_MODE_INVALID` | COMMAND | REJECTED | 模式非法 |
| 121 | `REASON_FRAME_ID_INVALID` | COMMAND | REJECTED | 请求 frame 不满足合同 |
| 122 | `REASON_POSE_ORIENTATION_INVALID` | COMMAND | REJECTED | 四元数非法或与 orientation_valid 冲突 |
| 123 | `REASON_RELATIVE_GOAL_REVERSE_FORBIDDEN` | COMMAND | REJECTED | 负 x 请求被拒绝 |
| 124 | `REASON_COMMAND_TOO_LARGE` | COMMAND | REJECTED | bounded string/字段长度超限 |

默认属性：

- severity：WARNING；
- disposition：NOT_APPLICABLE；
- retry：NEW_COMMAND；
- 当前 execution 和队列保持不变，除非命令本身是 ESTOP。

---

## 7. Task Catalog 与队列原因码（200–299）

| 数值 | 常量 | Scope | 典型 state | 默认处置 |
|---:|---|---|---|---|
| 200 | `REASON_QUEUE_FULL` | COMMAND | REJECTED | 不入队 |
| 201 | `REASON_TASK_CATALOG_UNAVAILABLE` | COMMAND/EXECUTION | REJECTED/FAILED | HOLD_QUEUE |
| 202 | `REASON_TASK_CATALOG_INVALID` | COMMAND/EXECUTION | REJECTED/FAILED | HOLD_QUEUE |
| 203 | `REASON_DEFAULT_TASK_UNCONFIGURED` | COMMAND | REJECTED | 当前任务不受影响 |
| 204 | `REASON_HOME_POSE_UNCONFIGURED` | COMMAND | REJECTED | RETURN_HOME 不破坏当前任务/队列 |
| 205 | `REASON_FIXED_GOAL_UNCONFIGURED` | COMMAND | REJECTED | 不执行 |
| 206 | `REASON_ROUTE_UNCONFIGURED` | COMMAND | REJECTED | 不执行 |
| 207 | `REASON_ROUTE_INVALID` | EXECUTION | FAILED | CONTINUE_QUEUE |
| 208 | `REASON_NO_ACTIVE_EXECUTION` | COMMAND | REJECTED | 例如无任务时 PAUSE/RESUME |
| 209 | `REASON_EXECUTION_ALREADY_PAUSED` | COMMAND | SUCCEEDED | no-op 成功 |
| 210 | `REASON_EXECUTION_NOT_PAUSED` | COMMAND | REJECTED | RESUME 非法 |
| 211 | `REASON_QUEUE_ITEM_INVALID` | COMMAND | CANCELED | 从队列删除 |
| 212 | `REASON_TASK_TIMEOUT` | EXECUTION | FAILED | CONTINUE_QUEUE |
| 213 | `REASON_COMPLETION_RULE_INVALID` | EXECUTION | FAILED | HOLD_QUEUE |
| 214 | `REASON_TASK_CONFIGURATION_MISSING` | COMMAND/EXECUTION | REJECTED/FAILED | HOLD_QUEUE |
| 215 | `REASON_QUEUE_HELD` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |

默认属性：

- 200、203～206、208、210：WARNING / NOT_APPLICABLE / NEW_COMMAND；
- 207、212：ERROR / CONTINUE_QUEUE / NEW_COMMAND；
- 201、202、213、214、215：ERROR 或 CRITICAL / HOLD_QUEUE / NO_RETRY；
- 209：INFO / NOT_APPLICABLE / NO_RETRY。

---

## 8. 感知与目标原因码（300–399）

| 数值 | 常量 | Scope | 典型 state | 默认处置 |
|---:|---|---|---|---|
| 300 | `REASON_PERCEPTION_UNAVAILABLE` | EXECUTION | SAFETY_BLOCKED/FAILED | HOLD_QUEUE |
| 301 | `REASON_PERCEPTION_HEALTH_DEGRADED` | EXECUTION | WAITING_TARGET/SAFETY_BLOCKED | 按任务要求 |
| 302 | `REASON_PERCEPTION_HEALTH_ERROR` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 303 | `REASON_PERCEPTION_HEALTH_STALE` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 304 | `REASON_TARGET_NOT_FOUND` | EXECUTION | FAILED | CONTINUE_QUEUE |
| 305 | `REASON_TARGET_WAIT_TIMEOUT` | EXECUTION | FAILED | CONTINUE_QUEUE |
| 306 | `REASON_TARGET_PROJECTION_INVALID` | EXECUTION | FAILED | CONTINUE_QUEUE |
| 307 | `REASON_TARGET_EXPIRED` | EXECUTION | FAILED | CONTINUE_QUEUE |
| 308 | `REASON_TARGET_INVALID` | EXECUTION | FAILED | CONTINUE_QUEUE |
| 309 | `REASON_TARGET_LOST` | EXECUTION | FAILED | CONTINUE_QUEUE |
| 310 | `REASON_TARGET_FRAME_INVALID` | EXECUTION | FAILED | HOLD_QUEUE |
| 311 | `REASON_TARGET_UNCERTAINTY_TOO_HIGH` | EXECUTION | FAILED | CONTINUE_QUEUE |
| 312 | `REASON_TARGET_TYPE_UNSUPPORTED` | COMMAND/EXECUTION | REJECTED/FAILED | CONTINUE_QUEUE |
| 313 | `REASON_TARGET_REGISTRY_STALE` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 314 | `REASON_TARGET_CHANGED_BEFORE_SUBMIT` | EXECUTION | PREPARING/WAITING_TARGET | 可重新选择 |
| 315 | `REASON_TARGET_INVALIDATED_DURING_EXECUTION` | EXECUTION | FAILED | CONTINUE_QUEUE |
| 316 | `REASON_NO_VALID_TARGET_AFTER_FILTERING` | EXECUTION | FAILED | CONTINUE_QUEUE |

默认：

- 304～309、311、312、315、316：局部目标失败，`CONTINUE_QUEUE`；
- 300、302、303、310、313：系统/坐标可信度问题，`HOLD_QUEUE`；
- M1 默认不自动重选执行中的失效目标；
- 任何目标更新都不得通过周期性重发 Goal 处理。

---

## 9. Navigation 原因码（400–499）

| 数值 | 常量 | Scope | 典型 state | 默认处置 |
|---:|---|---|---|---|
| 400 | `REASON_NAV_ADAPTER_UNAVAILABLE` | EXECUTION | FAILED/SAFETY_BLOCKED | HOLD_QUEUE |
| 401 | `REASON_NAV_GOAL_SUBMIT_FAILED` | EXECUTION | FAILED | HOLD_QUEUE |
| 402 | `REASON_NAV_GOAL_REJECTED` | EXECUTION | FAILED | CONTINUE_QUEUE |
| 403 | `REASON_NAV_ACCEPT_TIMEOUT` | EXECUTION | FAILED | HOLD_QUEUE |
| 404 | `REASON_GLOBAL_PLANNING_FAILED` | EXECUTION | FAILED | CONTINUE_QUEUE |
| 405 | `REASON_PATH_EXECUTION_FAILED` | EXECUTION | FAILED | CONTINUE_QUEUE |
| 406 | `REASON_NAVIGATION_FAILED` | EXECUTION | FAILED | CONTINUE_QUEUE |
| 407 | `REASON_NAVIGATION_ABORTED` | EXECUTION | FAILED | CONTINUE_QUEUE |
| 408 | `REASON_NAVIGATION_PROGRESS_FAILED` | EXECUTION | FAILED | CONTINUE_QUEUE |
| 409 | `REASON_NAV_SNAPSHOT_STALE` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 410 | `REASON_NAV_CANCEL_FAILED` | EXECUTION/COMMAND | SAFETY_BLOCKED/FAILED | HOLD_QUEUE |
| 411 | `REASON_NAV_CANCEL_TIMEOUT` | EXECUTION/COMMAND | SAFETY_BLOCKED/FAILED | HOLD_QUEUE |
| 412 | `REASON_NAV_RESULT_MISMATCH` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 413 | `REASON_NAV_GENERATION_MISMATCH` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 414 | `REASON_NAV_STALE_CALLBACK_IGNORED` | EXECUTION | 当前状态不变 | NOT_APPLICABLE |
| 415 | `REASON_NAV_PROGRESS_UNAVAILABLE` | EXECUTION | NAVIGATING | NOT_APPLICABLE |
| 416 | `REASON_NAV_GOAL_ALREADY_SUBMITTED` | EXECUTION | FAILED/SAFETY_BLOCKED | HOLD_QUEUE |
| 417 | `REASON_NAV_CONCURRENT_GENERATION_FORBIDDEN` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 418 | `REASON_NAV_REAL_ADAPTER_NOT_READY` | COMMAND/EXECUTION | REJECTED/FAILED | HOLD_QUEUE |
| 419 | `REASON_NAV_CANCELED_CONFIRMED` | EXECUTION | PAUSED/CANCELED | NOT_APPLICABLE |
| 420 | `REASON_NAV_REPLAN_NOT_ENABLED` | EXECUTION | FAILED | CONTINUE_QUEUE |

翻译规则：

- Adapter 不得把 Path Bridge String、A* String 或 Action 原始数值直接写入 reason_code；
- Goal rejection、规划失败、FollowPath abort 等必须转换为本文中最接近的稳定语义；
- 无法判断具体层级时使用 `REASON_NAVIGATION_FAILED`；
- 旧 generation 回调只使用 414 做诊断，不得改变 execution 终态。

默认 retry：

- 402、404～408、420：NEW_COMMAND；
- 409～413、416～418：NO_RETRY，等待系统修复或控制命令；
- 不自动周期性重试 Goal。

---

## 10. Safety 与 Lease 原因码（500–599）

| 数值 | 常量 | Scope | 典型 state | 默认处置 |
|---:|---|---|---|---|
| 500 | `REASON_EMERGENCY_STOP_ACTIVE` | COMMAND/EXECUTION | REJECTED/EMERGENCY_STOPPED | CLEAR_QUEUE |
| 501 | `REASON_SAFETY_ADAPTER_UNAVAILABLE` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 502 | `REASON_SAFETY_STATUS_STALE` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 503 | `REASON_LEASE_ACQUIRE_FAILED` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 504 | `REASON_LEASE_ACQUIRE_TIMEOUT` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 505 | `REASON_LEASE_RENEW_FAILED` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 506 | `REASON_LEASE_RENEW_TIMEOUT` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 507 | `REASON_LEASE_RELEASE_FAILED` | EXECUTION/COMMAND | SAFETY_BLOCKED/FAILED | HOLD_QUEUE |
| 508 | `REASON_LEASE_RELEASE_TIMEOUT` | EXECUTION/COMMAND | SAFETY_BLOCKED/FAILED | HOLD_QUEUE |
| 509 | `REASON_LEASE_OWNER_MISMATCH` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 510 | `REASON_AUTONOMOUS_ENABLE_REJECTED` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 511 | `REASON_AUTONOMOUS_NOT_ENABLED` | EXECUTION | PREPARING/SAFETY_BLOCKED | HOLD_QUEUE |
| 512 | `REASON_SAFETY_BLOCKED_OUTPUT` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 513 | `REASON_CANDIDATE_STALE` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 514 | `REASON_ESTOP_ASSERT_FAILED` | COMMAND | FAILED | CLEAR_QUEUE |
| 515 | `REASON_ESTOP_ASSERT_TIMEOUT` | COMMAND | FAILED | CLEAR_QUEUE |
| 516 | `REASON_ESTOP_CLEAR_CONDITIONS_NOT_MET` | COMMAND | REJECTED | CLEAR_QUEUE |
| 517 | `REASON_ESTOP_CLEAR_FAILED` | COMMAND | FAILED | CLEAR_QUEUE |
| 518 | `REASON_ESTOP_CLEAR_TIMEOUT` | COMMAND | FAILED | CLEAR_QUEUE |
| 519 | `REASON_EXTERNAL_ESTOP_STILL_ACTIVE` | COMMAND | REJECTED | CLEAR_QUEUE |
| 520 | `REASON_SAFETY_CLEANUP_INCOMPLETE` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 521 | `REASON_SAFETY_LEASE_EXPIRED` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 522 | `REASON_SAFETY_STATE_NOT_STRUCTURED` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |

默认：

- severity：CRITICAL；
- disposition：HOLD_QUEUE，ESTOP 类为 CLEAR_QUEUE；
- retry：USER_RESUME 或 NEW_COMMAND，前提是原因已明确解除；
- 任何 Safety 原因都不得通过直接发布 `/cmd_vel` 绕过。

---

## 11. 超时、stale 与时钟原因码（600–699）

| 数值 | 常量 | Scope | 典型 state | 默认处置 |
|---:|---|---|---|---|
| 600 | `REASON_OPERATION_TIMEOUT` | COMMAND/EXECUTION | FAILED | 按所属子系统 |
| 601 | `REASON_EXECUTION_TIMEOUT` | EXECUTION | FAILED | CONTINUE_QUEUE |
| 602 | `REASON_TARGET_WAIT_TIMEOUT_GENERIC` | EXECUTION | FAILED | CONTINUE_QUEUE |
| 603 | `REASON_ADAPTER_RESPONSE_TIMEOUT` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 604 | `REASON_STATUS_SNAPSHOT_STALE` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 605 | `REASON_ROS_CLOCK_UNAVAILABLE` | COMMAND/EXECUTION | REJECTED/SAFETY_BLOCKED | HOLD_QUEUE |
| 606 | `REASON_ROS_TIME_JUMP_INVALIDATED_REQUEST` | COMMAND | REJECTED | NOT_APPLICABLE |
| 607 | `REASON_WATCHDOG_CLOCK_ERROR` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 608 | `REASON_TIMESTAMP_IN_FUTURE` | COMMAND/EXECUTION | REJECTED/FAILED | NOT_APPLICABLE |
| 609 | `REASON_TIMESTAMP_INVALID` | COMMAND/EXECUTION | REJECTED/FAILED | NOT_APPLICABLE |
| 610 | `REASON_HEARTBEAT_STALE` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |

当存在更具体原因码时，优先使用具体代码：

- NAV_ACCEPT_TIMEOUT 使用 403，不使用 600；
- NAV_CANCEL_TIMEOUT 使用 411；
- LEASE_*_TIMEOUT 使用 504、506、508；
- PERCEPTION_HEALTH_STALE 使用 303；
- SAFETY_STATUS_STALE 使用 502。

本区间用于跨模块通用或无法进一步分类的超时/时钟问题。

---

## 12. 收口、状态机与合同原因码（700–799）

| 数值 | 常量 | Scope | 典型 state | 默认处置 |
|---:|---|---|---|---|
| 700 | `REASON_CLEANUP_PENDING` | EXECUTION | 原非终态/SAFETY_BLOCKED | HOLD_QUEUE |
| 701 | `REASON_CLEANUP_FAILED` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 702 | `REASON_CANCEL_CONFIRMATION_MISSING` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 703 | `REASON_RELEASE_CONFIRMATION_MISSING` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 704 | `REASON_INVALID_STATE_TRANSITION` | COMMAND/EXECUTION | REJECTED/SAFETY_BLOCKED | HOLD_QUEUE |
| 705 | `REASON_STATE_INVARIANT_VIOLATION` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 706 | `REASON_ACTIVE_EXECUTION_CONFLICT` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 707 | `REASON_ACTIVE_GENERATION_CONFLICT` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 708 | `REASON_LEASE_OWNER_CONFLICT` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 709 | `REASON_ADAPTER_CONTRACT_VIOLATION` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 710 | `REASON_TERMINAL_STATE_ALREADY_WRITTEN` | EXECUTION | 当前终态不变 | NOT_APPLICABLE |
| 711 | `REASON_TERMINAL_CACHE_MISSING` | COMMAND | FAILED | HOLD_QUEUE |
| 712 | `REASON_COMMAND_RECORD_CORRUPT` | COMMAND | FAILED | HOLD_QUEUE |
| 713 | `REASON_EXECUTION_RECORD_CORRUPT` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 714 | `REASON_CLEANUP_CONTEXT_CORRUPT` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 715 | `REASON_PENDING_OUTCOME_MISSING` | EXECUTION | SAFETY_BLOCKED | HOLD_QUEUE |
| 716 | `REASON_STATUS_SCOPE_INVALID` | COMMAND/EXECUTION | FAILED | HOLD_QUEUE |
| 717 | `REASON_REASON_CODE_MAPPING_MISSING` | COMMAND/EXECUTION | FAILED | HOLD_QUEUE |

这些原因码表示 Mission Manager 自身一致性或 Adapter 合同问题。默认：

- severity：CRITICAL；
- disposition：HOLD_QUEUE；
- retry：NO_RETRY；
- 不得自动推进普通队列。

---

## 13. 未分类内部错误（900–999）

| 数值 | 常量 | Scope | 典型 state | 默认处置 |
|---:|---|---|---|---|
| 900 | `REASON_INTERNAL_ERROR` | COMMAND/EXECUTION | FAILED/SAFETY_BLOCKED | HOLD_QUEUE |
| 901 | `REASON_INTERNAL_EXCEPTION` | COMMAND/EXECUTION | FAILED/SAFETY_BLOCKED | HOLD_QUEUE |
| 902 | `REASON_RESOURCE_UNAVAILABLE` | COMMAND/EXECUTION | FAILED/SAFETY_BLOCKED | HOLD_QUEUE |
| 903 | `REASON_SERIALIZATION_ERROR` | COMMAND/EXECUTION | REJECTED/FAILED | HOLD_QUEUE |
| 904 | `REASON_CONFIGURATION_ERROR` | COMMAND/EXECUTION | REJECTED/FAILED | HOLD_QUEUE |
| 905 | `REASON_UNSUPPORTED_OPERATION` | COMMAND | REJECTED | NOT_APPLICABLE |
| 999 | `REASON_UNKNOWN_ERROR` | COMMAND/EXECUTION | FAILED/SAFETY_BLOCKED | HOLD_QUEUE |

只有无法映射到 100–799 的情况才使用本区间。实现和测试应尽量避免使用 999。

---

## 14. 默认 failure_disposition 映射

### 14.1 CONTINUE_QUEUE

默认允许当前 execution 安全收口后继续队列：

- 207 ROUTE_INVALID；
- 212 TASK_TIMEOUT；
- 304 TARGET_NOT_FOUND；
- 305 TARGET_WAIT_TIMEOUT；
- 306 TARGET_PROJECTION_INVALID；
- 307 TARGET_EXPIRED；
- 308 TARGET_INVALID；
- 309 TARGET_LOST；
- 311 TARGET_UNCERTAINTY_TOO_HIGH；
- 312 TARGET_TYPE_UNSUPPORTED；
- 315 TARGET_INVALIDATED_DURING_EXECUTION；
- 316 NO_VALID_TARGET_AFTER_FILTERING；
- 402 NAV_GOAL_REJECTED；
- 404 GLOBAL_PLANNING_FAILED；
- 405 PATH_EXECUTION_FAILED；
- 406 NAVIGATION_FAILED；
- 407 NAVIGATION_ABORTED；
- 408 NAVIGATION_PROGRESS_FAILED；
- 420 NAV_REPLAN_NOT_ENABLED；
- 601 EXECUTION_TIMEOUT。

前提：

- Lease 已确认释放；
- 导航已结束或取消确认；
- Manager 不处于 EMERGENCY_LATCHED；
- 没有其他 HOLD_QUEUE 原因。

### 14.2 HOLD_QUEUE

以下类别默认 HOLD_QUEUE：

- Task Catalog 不可用或配置损坏；
- PerceptionHealth/Safety/Navigation Snapshot stale；
- Navigation Adapter 不可用；
- cancel 失败或超时；
- Safety Lease 获取、续期、释放失败；
- Adapter 合同不一致；
- cleanup/invariant/internal error；
- 所有 CRITICAL 级原因。

### 14.3 CLEAR_QUEUE

以下事件清空普通队列：

- STOP；
- RETURN_HOME 在 home_pose 校验成功后；
- ESTOP；
- EMERGENCY_LATCHED；
- 对应原因码通常为 500、514～519，或控制命令自己的成功原因。

Command rejection 本身不会清空队列，除非它发生在急停流程中。

---

## 15. 控制命令 Reason Code 映射

### 15.1 PAUSE

| 阶段 | Scope | state | reason_code |
|---|---|---|---|
| 已受理 | COMMAND | ACCEPTED | REASON_COMMAND_VALID |
| execution 正在收口 | EXECUTION | PAUSING | REASON_PAUSE_REQUESTED |
| 暂停完成 | EXECUTION | PAUSED | REASON_EXECUTION_PAUSED |
| 命令完成 | COMMAND | SUCCEEDED | REASON_COMMAND_APPLIED |
| 已经暂停的新 command_id | COMMAND | SUCCEEDED | REASON_NO_OP_ALREADY_SATISFIED |
| 当前无活动 execution | COMMAND | REJECTED | REASON_NO_ACTIVE_EXECUTION |

### 15.2 RESUME

| 阶段 | Scope | state | reason_code |
|---|---|---|---|
| 非 PAUSED/可恢复 SAFETY_BLOCKED | COMMAND | REJECTED | REASON_COMMAND_NOT_ALLOWED_IN_STATE |
| 急停锁闭 | COMMAND | REJECTED | REASON_COMMAND_NOT_ALLOWED_IN_MODE |
| 新 generation 已开始 | COMMAND | SUCCEEDED | REASON_RESUME_STARTED |
| execution 启动中 | EXECUTION | PREPARING | REASON_NAVIGATION_STARTING |
| 阻断原因仍存在 | COMMAND | REJECTED | 对应 Safety/Navigation 原因 |

### 15.3 STOP

| 阶段 | Scope | state | reason_code |
|---|---|---|---|
| 已受理 | COMMAND | ACCEPTED | REASON_COMMAND_VALID |
| execution 取消完成 | EXECUTION | CANCELED | REASON_EXECUTION_CANCELED_BY_STOP |
| STOP 完成 | COMMAND | SUCCEEDED | REASON_STOP_COMPLETED |
| 无任务无队列 | COMMAND | SUCCEEDED | REASON_NO_OP_ALREADY_SATISFIED |
| cancel/release 失败 | COMMAND/EXECUTION | FAILED/SAFETY_BLOCKED | 对应 410/411/507/508 |

### 15.4 RETURN_HOME

| 阶段 | Scope | state | reason_code |
|---|---|---|---|
| home_pose 未配置 | COMMAND | REJECTED | REASON_HOME_POSE_UNCONFIGURED |
| 旧 execution 取消 | EXECUTION | CANCELED | REASON_EXECUTION_CANCELED_BY_RETURN_HOME |
| 返航 execution 创建 | COMMAND | SUCCEEDED | REASON_RETURN_HOME_STARTED |
| 返航执行中 | EXECUTION | RETURNING_HOME | REASON_RETURN_HOME_ACTIVE |
| 返航成功 | EXECUTION | SUCCEEDED | REASON_EXECUTION_COMPLETED |

### 15.5 ESTOP / RESET ESTOP

| 阶段 | Scope | state | reason_code |
|---|---|---|---|
| 急停已确认 | COMMAND | SUCCEEDED | REASON_ESTOP_ASSERTED |
| execution 被急停终止 | EXECUTION | EMERGENCY_STOPPED | REASON_EXECUTION_CANCELED_BY_ESTOP |
| 急停断言失败 | COMMAND | FAILED | REASON_ESTOP_ASSERT_FAILED |
| RESET 来源非法 | COMMAND | REJECTED | REASON_RESET_ESTOP_SOURCE_NOT_ALLOWED |
| RESET 条件不满足 | COMMAND | REJECTED | REASON_ESTOP_CLEAR_CONDITIONS_NOT_MET |
| 外部急停仍有效 | COMMAND | REJECTED | REASON_EXTERNAL_ESTOP_STILL_ACTIVE |
| 清除成功 | COMMAND | SUCCEEDED | REASON_ESTOP_CLEARED |
| 清除失败/超时 | COMMAND | FAILED | REASON_ESTOP_CLEAR_FAILED / TIMEOUT |

---

## 16. Command Scope 与 Execution Scope 规则

### 16.1 Command Scope

Command Scope reason code 只解释该命令本身：

- 是否被接受；
- 是否排队；
- 是否幂等重放；
- 是否拒绝；
- 控制命令是否已经完成副作用。

不得用 Command Scope 的 SUCCEEDED 表示 mission 已经导航成功。

例如：

```text
RETURN_HOME command:
SCOPE_COMMAND + STATE_SUCCEEDED + REASON_RETURN_HOME_STARTED
```

只表示返航 execution 已成功建立，不表示已经回到 home。

### 16.2 Execution Scope

Execution Scope reason code 解释任务实例：

- 等待目标；
- 启动导航；
- 执行中；
- 暂停；
- 安全阻断；
- 成功、失败、取消或急停终止。

Execution Scope 的 `command_id` 始终使用创建该 execution 的原始普通 mission command_id。控制命令自身的 command_id 只出现在对应 Command Scope 状态中。

---

## 17. message 与诊断信息

`TaskStatus.message`：

- 只用于人类阅读；
- 不作为程序逻辑合同；
- 建议使用简短中文；
- 不应包含密钥、完整堆栈或大段日志；
- 可以包含底层错误摘要，但必须同时给出稳定 reason_code。

推荐示例：

```text
state=STATE_FAILED
reason_code=REASON_GLOBAL_PLANNING_FAILED
message="A* 未生成有效路径"
```

底层详细证据应写入日志或诊断 Topic，不应无限扩张 TaskStatus.message。

---

## 18. TaskStatus.msg 回填候选

后续接口包修订时，`TaskStatus.msg` 至少回填：

```text
uint8 SCOPE_UNKNOWN=0
uint8 SCOPE_COMMAND=1
uint8 SCOPE_EXECUTION=2

# STATE_* 常量由状态机文档 0～16 冻结

# REASON_* 常量由本文冻结
# 实际 msg 中可以按本文区间完整定义，或拆分到独立 ReasonCodes.msg。
# M0 候选优先直接放入 TaskStatus.msg，减少额外接口类型。

std_msgs/Header header
string interface_version
string execution_id
string command_id
uint16 task_id
uint8 status_scope
uint8 state
float32 progress
string active_target_id
float32 remaining_distance_m
int32 reason_code
string message
```

冻结：

- `progress=-1.0` 表示不可用；
- `remaining_distance_m=-1.0` 表示不可用；
- `reason_code` 不再使用 `error_code` 命名；
- HMI 只能基于 `status_scope`、`state`、`reason_code` 和 ID 字段做程序判断；
- Terminal TaskStatus 必须缓存以支持 command_id 幂等重放。

---

## 19. 实现要求

M1 实现必须提供静态 Reason Policy 表，至少包含：

```text
reason_code
canonical_name
severity
failure_disposition
retry_policy
allowed_scopes
allowed_states
```

运行时要求：

1. 生成 TaskStatus 前校验 reason_code 是否允许当前 scope/state；
2. 未定义映射时使用 `REASON_REASON_CODE_MAPPING_MISSING`，并 HOLD_QUEUE；
3. 不允许把任意整数透传到 TaskStatus；
4. Adapter 只返回结构化结果，不让 Mission State Machine 解析日志字符串；
5. 测试覆盖每个对外 reason code 的 scope/state 合法性；
6. 测试覆盖 CONTINUE_QUEUE、HOLD_QUEUE 和 CLEAR_QUEUE；
7. reason policy 表应为单一权威来源，避免代码中散落多套映射。

---

## 20. 测试要求

后续 `mission_manager_test_plan.md` 至少覆盖：

- 相同 command_id 同语义：REASON_IDEMPOTENT_REPLAY；
- 相同 command_id 不同语义：REASON_DUPLICATE_COMMAND_CONFLICT；
- queue full；
- queued command expiry；
- home_pose 未配置时 RETURN_HOME 不破坏当前任务；
- target not found 与 target wait timeout；
- goal rejected、accept timeout；
- cancel failed、cancel timeout；
- Lease acquire/renew/release 失败；
- Navigation/Safety/Perception stale；
- STOP/RETURN_HOME/ESTOP 队列效果；
- PAUSE/RESUME 的 Command Scope 与 Execution Scope 双重发布；
- ESTOP 解除后不恢复旧任务；
- stale old generation callback ignored；
- terminal cache 幂等重放；
- reason code 与 scope/state 不匹配时安全失败；
- CONTINUE_QUEUE 与 HOLD_QUEUE 的分支行为。

---

## 21. 当前未决项

| 项目 | 状态 |
|---|---|
| Reason 常量直接放入 TaskStatus.msg，还是拆分为独立 ReasonCodes.msg | M0 总审查决定 |
| 自定义字符串 bounded 上界 | 接口包修订前冻结 |
| Lease release 失败是否自动触发软件 ESTOP | 待安全策略冻结 |
| PerceptionHealth DEGRADED 对不同任务的处置 | 与感知组确认 |
| NAV_GOAL_REJECTED 是否在所有任务中都 CONTINUE_QUEUE | 测试计划可按任务类型覆盖 |
| Progress Checker 正式参数与失败重试策略 | 导航修复后冻结 |
| 真实 Adapter 的底层状态到 400 区间映射 | 等待 A* 与 Path Bridge 修复 |
| RESET ESTOP 的真实确认来源 | 等待结构化 Safety Adapter |
| 900 区间错误是否自动上报独立诊断 Topic | M1 设计时决定 |

---

## 22. 后续顺序

1. 审查并冻结本 Reason Code 文档；
2. 回填 `docs/mission_manager_interface.md`；
3. 创建 `docs/mission_manager_test_plan.md`；
4. 修订 `cleannav_interfaces` 候选包；
5. 构建接口包并执行静态验证；
6. M0 总审查；
7. 进入 M1 Mock 包骨架。
