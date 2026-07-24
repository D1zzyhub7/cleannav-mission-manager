# CleanNav Mission Manager M0 测试计划

> **状态：** M0-5 测试计划冻结候选。
> **边界：** 本文冻结 Mission Manager M1 Mock 阶段的测试分层、Mock 合同、测试矩阵、验收门槛和证据格式；尚未创建 `cleannav_mission_manager` 业务包，尚未接入真实 Navigation Adapter 或真实 Safety Adapter。
> **依赖基线：** `docs/mission_manager_architecture.md`、`docs/mission_manager_interface.md`、`docs/mission_manager_state_machine.md`、`docs/mission_manager_reason_codes.md`。

---

## 1. 文档目的

本文用于保证 Mission Manager 在接入真实导航前，先通过可重复、可审计、无真实运动风险的 Mock 测试。

测试目标：

1. 验证 Command Record、Execution Record 与 Manager Mode 三层状态模型；
2. 验证 `command_id` 幂等、队列、execution/generation 和旧回调隔离；
3. 验证 Navigation Adapter 与 Safety Lease Adapter 的抽象合同；
4. 验证 PAUSE、RESUME、STOP、RETURN_HOME、ESTOP 和 RESET_ESTOP；
5. 验证视觉目标等待、失效和超时；
6. 验证 `TaskStatus.status_scope/state/reason_code` 的一致性；
7. 验证所有安全收口必须等待必要的 cancel/release 确认；
8. 验证 M1 不直接依赖 A*、Path Bridge、TEB、Gazebo 或真实 Safety Supervisor；
9. 为后续真实 Adapter 集成建立不可回退的行为基线。

---

## 2. 测试范围与非范围

### 2.1 M1 当前测试范围

- v1.0 `TaskCommand`；
- v1.0 `TaskStatus`；
- v1.0 `RobotStatus`；
- v1.0 `CleaningTarget` / `CleaningTargetArray`；
- v1.0 `PerceptionHealth`；
- Task Catalog；
- Command Gateway、Validator、Deduplicator；
- Mission Queue；
- Mission State Machine；
- Target Registry；
- Goal Resolver；
- Mock Navigation Adapter；
- Mock Safety Lease Adapter；
- Status Aggregator；
- 注入式 ROS 语义时钟和 monotonic 看门狗时钟；
- reason policy 静态映射；
- Topic/QoS 的节点级验证（接口包和 M1 节点创建后）。

### 2.2 当前明确不测试

- 真实 A* 路径质量；
- Path Bridge 连续 Goal 修复；
- TEB 动态避障性能；
- Gazebo 车辆运动；
- 真实 `/goal_pose` 的 accepted/cancel/result 映射；
- J6M 部署；
- APP 手动遥控；
- `SpatialGoalRequest` 的正式 v1.1 实现；
- 清扫执行器 Action；
- 车端 LLM/VLA；
- 完整 Continuous Navigation V1。

M1 测试通过不得表述为“真实导航集成通过”。

---

## 3. 测试分层

| 层级 | 名称 | 依赖 | 目的 | M1 阻塞 |
|---|---|---|---|---|
| L0 | 文档与接口静态一致性 | 文件系统、Python | 检查 Topic、枚举、Reason Code、配置和构建元数据 | 是 |
| L1 | 纯领域逻辑单元测试 | Python、可注入时钟、Mock 对象 | 验证状态机、队列、幂等、ID、Reason Policy | 是 |
| L2 | 组件级 Mock 测试 | `rclpy`、生成后的 v1.0 接口 | 验证 Mission Manager 节点与 Mock Adapter/Topic | 是 |
| L3 | ROS 2 launch 测试 | `launch_testing_ros` | 验证节点启动、QoS、Topic、关闭和跨节点交互 | 是 |
| L4 | 真实 Adapter 集成测试 | A*、Path Bridge、Safety | 后续真实集成 | 否 |
| L5 | Gazebo/J6M 系统测试 | 完整系统 | 真实运动、性能和部署 | 否 |

ROS 2 节点级测试后续优先使用 `launch_testing_ros`；纯状态机逻辑优先使用 pytest，不应为了测试领域逻辑而启动完整 ROS 图。

---

## 4. 通用测试原则

1. **确定性**：相同输入、相同时钟和 Mock 脚本必须得到相同状态序列；
2. **无真实 sleep**：L1/L2 测试通过推进 Fake Clock 触发超时；
3. **无真实运动**：M1 不发布最终 `/cmd_vel`；
4. **单事件推进**：每次测试明确注入一个事件并检查状态、输出和副作用；
5. **完整收口**：涉及导航或 Lease 的流程必须检查 cancel/release 所需确认；
6. **负路径优先**：每个成功路径至少配套一个拒绝、失败、超时或 stale 路径；
7. **双 Scope 检查**：控制命令同时验证 Command Scope 和 Execution Scope；
8. **旧回调隔离**：每个 generation 流程都至少注入一次旧回调；
9. **Reason Policy 校验**：每条 TaskStatus 必须满足 scope/state/reason 合法映射；
10. **不可扩大结论**：Mock PASS 只能证明 Mission Manager 合同，不证明真实导航链路。

---

## 5. 测试夹具与可注入依赖

### 5.1 FakeClock

至少提供两类独立时钟：

```text
ros_time
monotonic_time
```

概念方法：

```text
now_ros()
now_monotonic()
advance_ros(seconds)
advance_monotonic(seconds)
set_ros(seconds)
```

规则：

- `valid_for`、目标有效期、队列项过期使用 `ros_time`；
- accept/cancel/Lease/stale 看门狗使用 `monotonic_time`；
- 测试可以只推进一种时钟，验证两类超时互不混淆；
- 不调用真实 `time.sleep()`。

### 5.2 DeterministicIdFactory

提供可预测 ID：

```text
next_execution_id() -> "exec-001", "exec-002", ...
```

便于精确断言。

generation 在同一 execution 内从 1 开始单调递增。

### 5.3 FakeTaskCatalog

至少包含：

- 一个有效固定点 mission；
- 一个有效视觉目标 mission；
- 一个有效固定路线 mission（只用于 Mock）；
- PAUSE；
- RESUME；
- STOP；
- RETURN_HOME；
- ESTOP；
- RESET_ESTOP；
- 一个 disabled task；
- 一个缺失配置的 task；
- 一个来源受限 task。

### 5.4 Event Recorder

记录：

- 输入命令；
- 内部状态变化；
- Adapter 调用；
- TaskStatus；
- RobotStatus；
- 队列变化；
- terminal cache 写入；
- stale callback；
- Reason Policy 校验结果。

每条记录至少包含顺序号和测试时钟。

---

## 6. Mock Navigation Adapter 合同

### 6.1 概念方法

```text
submit_goal(execution_id, generation, internal_goal)
cancel_goal(execution_id, generation)
get_snapshot()
```

### 6.2 可编程行为

Mock 必须支持脚本化注入：

- submit 成功并延迟 accepted；
- submit 立即失败；
- goal accepted；
- goal rejected；
- accept timeout；
- navigation succeeded；
- navigation failed；
- progress 更新；
- progress 不可用；
- cancel confirmed；
- cancel failed；
- cancel timeout；
- snapshot stale；
- 旧 execution 回调；
- 旧 generation 回调；
- 重复 submit 同 generation；
- 并发 generation 请求。

### 6.3 Mock 断言

- 同一 generation 最多调用一次 submit；
- cancel 未 confirmed 前禁止新 submit；
- 回调必须携带 execution_id 与 generation；
- 旧回调不修改当前状态；
- Mock 不发布 `/goal_pose`；
- Mock 不调用 FollowPath；
- Mock 不依赖 A* 或 Path Bridge。

---

## 7. Mock Safety Lease Adapter 合同

### 7.1 概念方法

```text
acquire(execution_id)
renew(execution_id)
release(execution_id)
get_snapshot()
```

### 7.2 可编程行为

- acquire 成功；
- acquire 失败；
- acquire timeout；
- renew 成功；
- renew 失败；
- renew timeout；
- release 成功；
- release 失败；
- release timeout；
- Lease owner mismatch；
- Safety Snapshot stale；
- emergency_stop active；
- emergency_stop cleared；
- autonomous enable rejected。

### 7.3 Mock 断言

- goal accepted 前不得 acquire；
- execution_id 必须匹配 Lease owner；
- PAUSE/STOP/RETURN_HOME/ESTOP/失败收口时按需 release；
- release 未确认前不得发布正常 execution 终态；
- renew 失败后不得继续认为 `autonomous_enabled=true`；
- Mock 不发布 `/cmd_vel`。

---

## 8. TaskStatus 与 Reason Policy Oracle

测试中维护单一权威映射：

```text
reason_code
canonical_name
allowed_scopes
allowed_states
severity
failure_disposition
retry_policy
```

每条对外 TaskStatus 自动检查：

1. `status_scope` 合法；
2. `state` 为 0～16 中已定义值；
3. `reason_code` 存在；
4. reason 允许当前 scope；
5. reason 允许当前 state；
6. `execution_id` 是否满足 Scope 规则；
7. progress 是否为 `-1.0` 或 `[0,1]`；
8. remaining distance 是否为 `-1.0` 或非负；
9. message 不参与测试判定；
10. terminal 状态是否只首次生成一次，幂等重放除外。

---

## 9. L0 静态一致性测试

### TP-L0-001 文档存在性

检查：

- `mission_manager_architecture.md`
- `mission_manager_interface.md`
- `mission_manager_state_machine.md`
- `mission_manager_reason_codes.md`
- `mission_manager_test_plan.md`

### TP-L0-002 Topic 一致性

接口文档与未来配置中 Topic 必须一致：

- `/cleannav/hmi/task_command`
- `/cleannav/task_status`
- `/cleannav/robot_status`
- `/cleannav/perception/cleaning_targets`
- `/cleannav/perception/health`

### TP-L0-003 TaskStatus 状态枚举

验证状态值恰为 0～16，名称与状态机文档一致，不存在 `STATE_APPLIED`。

### TP-L0-004 Reason Code 唯一性

检查：

- 数值唯一；
- 常量名称唯一；
- 无负值；
- 0 仅为 NONE；
- 800～899 未使用；
- 已定义数值位于对应区间。

### TP-L0-005 TaskStatus 字段

必须包含：

- `status_scope`
- `reason_code`

不得包含旧字段 `error_code`。

### TP-L0-006 CleaningTarget 默认状态

必须存在：

```text
OBSERVATION_UNKNOWN=0
```

不得以 NEW=0。

### TP-L0-007 CMake 生成边界

正式 v1.0：

- 生成 6 个 v1.0 消息；
- 不生成 ManualDriveRequest；
- 不生成 SpatialGoalRequest；
- 安装 config 目录。

### TP-L0-008 package.xml

检查：

- `rosidl_default_generators` 为 `buildtool_depend`；
- `rosidl_default_runtime` 为 `exec_depend`；
- `member_of_group` 存在；
- maintainer 不使用占位邮箱。

### TP-L0-009 Task Catalog 占位禁用

未配置时必须 disabled：

- START_DEFAULT_CLEANING；
- RETURN_HOME；
- GOTO_POINT_1；
- CLEAN_ROUTE_1。

### TP-L0-010 QoS 配置一致性

节点代码与接口文档 QoS 必须一致。

---

## 10. 命令校验与幂等测试

| ID | 场景 | 预期 |
|---|---|---|
| TP-CMD-001 | 有效普通 mission，IDLE | Command ACCEPTED，创建 execution |
| TP-CMD-002 | 有效普通 mission，忙碌 | Command QUEUED，不创建 execution_id |
| TP-CMD-003 | command_id 同语义重放 | 不重复副作用，Reason=IDEMPOTENT_REPLAY |
| TP-CMD-004 | command_id 不同语义冲突 | REJECTED，Reason=DUPLICATE_COMMAND_CONFLICT |
| TP-CMD-005 | interface_version 非 1.0 | REJECTED |
| TP-CMD-006 | command_id 为空 | REJECTED |
| TP-CMD-007 | valid_for<=0 | REJECTED |
| TP-CMD-008 | 接收时已过期 | REJECTED，不入队 |
| TP-CMD-009 | 来源 UNKNOWN | REJECTED |
| TP-CMD-010 | 来源不允许 | REJECTED |
| TP-CMD-011 | task_id 未知 | REJECTED |
| TP-CMD-012 | task disabled | REJECTED |
| TP-CMD-013 | 语音置信度过低 | REJECTED |
| TP-CMD-014 | HMI 伪造 SOURCE_INTERNAL | REJECTED |
| TP-CMD-015 | 字符串超上界 | REJECTED 或接口层无法构造 |
| TP-CMD-016 | LATCHED 下普通 mission | REJECTED，不入队 |
| TP-CMD-017 | v1.0 下 SpatialGoalRequest 功能关闭 | REJECTED，Reason=NOT_SUPPORTED_IN_VERSION |
| TP-CMD-018 | 相同 task_id 不同 command_id | 允许分别执行/排队 |

---

## 11. 队列测试

| ID | 场景 | 预期 |
|---|---|---|
| TP-QUE-001 | IDLE 首任务 | 直接激活，不进入等待队列 |
| TP-QUE-002 | 忙碌时连续入队 | FIFO 顺序保持 |
| TP-QUE-003 | 队列容量 10，再提交第 11 个 | 第 11 个 REJECTED，QUEUE_FULL |
| TP-QUE-004 | 已 QUEUED 项激活前过期 | Command CANCELED，不创建 execution |
| TP-QUE-005 | PAUSE | 队列保留 |
| TP-QUE-006 | RESUME | 队列不变 |
| TP-QUE-007 | STOP | 队列清空，被清项发布 Command CANCELED |
| TP-QUE-008 | RETURN_HOME 配置合法 | 队列清空 |
| TP-QUE-009 | RETURN_HOME home_pose 无效 | 当前任务和队列不变 |
| TP-QUE-010 | ESTOP | 队列立即清空 |
| TP-QUE-011 | execution SUCCEEDED | 自动激活下一有效项 |
| TP-QUE-012 | CONTINUE_QUEUE 失败 | 收口后推进下一项 |
| TP-QUE-013 | HOLD_QUEUE 失败 | 队列保留但不推进 |
| TP-QUE-014 | 队首过期、第二项有效 | 删除首项并激活第二项 |
| TP-QUE-015 | 相同 task_id 不同 command_id | FIFO 中允许并存 |

---

## 12. Execution 与 Generation 测试

| ID | 场景 | 预期 |
|---|---|---|
| TP-EXE-001 | 普通 mission 激活 | 创建唯一 execution_id |
| TP-EXE-002 | QUEUED | execution_id 为空 |
| TP-EXE-003 | 首次 submit | generation=1 |
| TP-EXE-004 | PAUSE/RESUME | execution_id 不变，generation+1 |
| TP-EXE-005 | RETURN_HOME | 旧 execution 终止，新 execution_id、generation=1 |
| TP-EXE-006 | STOP 后再次同目标 | 新 command_id、新 execution_id |
| TP-EXE-007 | 同 generation 重复 submit | 拒绝/安全阻断 |
| TP-EXE-008 | 旧 generation NAV_SUCCEEDED | 忽略，不终止当前 execution |
| TP-EXE-009 | 旧 execution cancel callback | 忽略，不释放当前 Lease |
| TP-EXE-010 | 并发 generation | 禁止并进入合同错误 |
| TP-EXE-011 | terminal 状态重复写入 | 保持原终态，记录重复 |
| TP-EXE-012 | terminal cache 幂等重放 | 再发缓存状态，不执行副作用 |

---

## 13. 固定点与普通生命周期测试

### TP-LIFE-001 成功路径

```text
ACCEPTED
→ PREPARING
→ NAV_GOAL_ACCEPTED
→ LEASE_ACQUIRED
→ NAVIGATING
→ NAV_SUCCEEDED
→ FINALIZING
→ LEASE_RELEASED
→ SUCCEEDED
```

断言：

- accepted 前无 acquire；
- Lease 成功前不 NAVIGATING；
- release 前不发布 SUCCEEDED；
- terminal cache 已写入；
- 可推进队列。

### TP-LIFE-002 Goal rejected

- 不 acquire；
- execution FAILED；
- reason=NAV_GOAL_REJECTED；
- 按 disposition 处理队列。

### TP-LIFE-003 Accept timeout

- monotonic 超时；
- ROS Clock 不推进也必须超时；
- 不 acquire；
- HOLD_QUEUE。

### TP-LIFE-004 Lease acquire failed

- goal 已 accepted；
- 不进入 EXECUTING；
- cancel navigation；
- 等待 cancel confirmed；
- 进入 SAFETY_BLOCKED。

### TP-LIFE-005 Navigation failed

- 先进入 FINALIZING；
- release 成功后 FAILED；
- 不提前写终态。

### TP-LIFE-006 Release failed

- 不发布正常 SUCCEEDED/FAILED；
- 进入 SAFETY_BLOCKED；
- HOLD_QUEUE。

---

## 14. 视觉目标测试

| ID | 场景 | 预期 |
|---|---|---|
| TP-TGT-001 | 有有效 target | 选取稳定 target_id 并生成 map Goal |
| TP-TGT-002 | 无目标 | WAITING_TARGET，不 submit、不 acquire |
| TP-TGT-003 | wait_timeout | FAILED，TARGET_WAIT_TIMEOUT |
| TP-TGT-004 | projection_valid=false | 不选为目标 |
| TP-TGT-005 | frame 非 map | 拒绝/系统错误 |
| TP-TGT-006 | target expired | 不选或 execution FAILED |
| TP-TGT-007 | 空数组 | 不立即清空 Registry |
| TP-TGT-008 | 单批缺失 | 不立即删除目标 |
| TP-TGT-009 | 普通位置更新 | 不自动重发 Goal |
| TP-TGT-010 | accepted 前目标失效、尚未 submit | 可重新选择 |
| TP-TGT-011 | 已 submit 后目标失效 | 先 cancel 旧 generation |
| TP-TGT-012 | EXECUTING 中 INVALID | 默认 FAILED，不自动重选 |
| TP-TGT-013 | 旧 target_id 更新 | 不影响当前不同 target |
| TP-TGT-014 | PerceptionHealth stale | HOLD_QUEUE / SAFETY_BLOCKED |
| TP-TGT-015 | DEGRADED | 按任务策略，不默认为 OK |

---

## 15. PAUSE / RESUME 测试

| ID | 场景 | 预期 |
|---|---|---|
| TP-PAU-001 | WAITING_TARGET 中 PAUSE | 无 cancel/release，进入 PAUSED |
| TP-PAU-002 | PREPARING_GOAL 未 submit 时 PAUSE | 进入 PAUSED |
| TP-PAU-003 | NAVIGATION_STARTING 中 PAUSE | cancel pending goal，等待 confirmed |
| TP-PAU-004 | LEASE_ACQUIRING 中 PAUSE | cancel；若 Lease 已获得则 release |
| TP-PAU-005 | EXECUTING 中 PAUSE | release+cancel，二者收口后 PAUSED |
| TP-PAU-006 | cancel 先确认、release 后确认 | 最后一个确认到达后 PAUSED |
| TP-PAU-007 | release 先确认、cancel 后确认 | 最后一个确认到达后 PAUSED |
| TP-PAU-008 | cancel timeout | SAFETY_BLOCKED，不伪装 PAUSED |
| TP-PAU-009 | release timeout | SAFETY_BLOCKED |
| TP-PAU-010 | PAUSED 收新 command_id PAUSE | no-op SUCCEEDED |
| TP-RES-001 | PAUSED RESUME | 同 execution，新 generation |
| TP-RES-002 | RESUME goal accepted 前 | 不 acquire |
| TP-RES-003 | RESUME accepted 后 | acquire，成功后 NAVIGATING |
| TP-RES-004 | 恢复旧 generation 回调 | 忽略 |
| TP-RES-005 | IDLE 下 RESUME | REJECTED |
| TP-RES-006 | LATCHED 下 RESUME | REJECTED |
| TP-RES-007 | SAFETY_BLOCKED 原因未解除 | REJECTED |
| TP-RES-008 | SAFETY_BLOCKED 原因解除且清理完成 | 允许新 generation |

双 Scope：

- PAUSE/RESUME Command Scope 最终状态；
- 原 execution 的 PAUSED/PREPARING/NAVIGATING 状态。

---

## 16. STOP 测试

| ID | 场景 | 预期 |
|---|---|---|
| TP-STP-001 | EXECUTING STOP | 清队列，release+cancel，确认后 CANCELED |
| TP-STP-002 | WAITING_TARGET STOP | 无导航，execution CANCELED |
| TP-STP-003 | 仅队列无活动任务 | 清队列，STOP SUCCEEDED |
| TP-STP-004 | 无任务无队列 | no-op SUCCEEDED |
| TP-STP-005 | cancel failed | STOP 不完成，SAFETY_BLOCKED |
| TP-STP-006 | release failed | STOP 不完成，SAFETY_BLOCKED |
| TP-STP-007 | STOP | 不发布 emergency_stop=true |
| TP-STP-008 | 被清队列项 | 每项 Command CANCELED |
| TP-STP-009 | 重复相同 command_id | 幂等重放，不重复 cancel |

---

## 17. RETURN_HOME 测试

| ID | 场景 | 预期 |
|---|---|---|
| TP-RTH-001 | home_pose 未配置 | Command REJECTED，当前任务/队列不变 |
| TP-RTH-002 | home_pose 非法 | 同上 |
| TP-RTH-003 | 有活动 execution | 收口旧任务后创建新返航 execution |
| TP-RTH-004 | 无活动任务 | 直接创建返航 execution |
| TP-RTH-005 | 校验成功 | 清空队列 |
| TP-RTH-006 | 旧 cancel 未确认 | 不提交 home Goal |
| TP-RTH-007 | 旧 release 未确认 | 不提交 home Goal |
| TP-RTH-008 | 新返航 goal accepted | acquire 新 execution Lease |
| TP-RTH-009 | 对外状态 | Execution Scope RETURNING_HOME |
| TP-RTH-010 | Command Scope SUCCEEDED | 只表示返航 execution 已创建，不表示已到家 |
| TP-RTH-011 | 返航成功 | Execution Scope SUCCEEDED |
| TP-RTH-012 | 返航失败 | 按 reason policy 收口 |

---

## 18. ESTOP / RESET_ESTOP 测试

| ID | 场景 | 预期 |
|---|---|---|
| TP-EST-001 | 任意状态 ESTOP | 立即进入 EMERGENCY_LATCHED |
| TP-EST-002 | ESTOP | 立即清队列 |
| TP-EST-003 | ESTOP | 立即请求 emergency_stop=true |
| TP-EST-004 | ESTOP | 不等待 cancel 完成才锁闭 |
| TP-EST-005 | 当前 execution | 立即对外 EMERGENCY_STOPPED |
| TP-EST-006 | 后台 cancel/release | 继续清理但旧任务不可恢复 |
| TP-EST-007 | LATCHED 普通 mission | REJECTED |
| TP-EST-008 | LATCHED PAUSE/RESUME/STOP/RETURN_HOME | REJECTED |
| TP-EST-009 | 重复 ESTOP | 幂等安全成功 |
| TP-RST-001 | 语音 RESET | REJECTED |
| TP-RST-002 | 非 LATCHED RESET | REJECTED |
| TP-RST-003 | 外部急停仍有效 | REJECTED |
| TP-RST-004 | Safety stale | REJECTED |
| TP-RST-005 | Lease 仍活动 | REJECTED |
| TP-RST-006 | navigation 未收口 | REJECTED |
| TP-RST-007 | 条件全部满足 | emergency_stop=false，等待确认 |
| TP-RST-008 | Safety 确认清除 | NORMAL + IDLE |
| TP-RST-009 | clear timeout | 保持 LATCHED，Command FAILED |
| TP-RST-010 | 清除后 | 不恢复旧任务或队列 |

---

## 19. stale、超时与时钟测试

| ID | 场景 | 预期 |
|---|---|---|
| TP-TIME-001 | 只推进 ROS Clock | 命令/目标语义超时触发 |
| TP-TIME-002 | 只推进 monotonic | Adapter 看门狗触发 |
| TP-TIME-003 | ROS Clock 暂停 | accept/cancel/Lease 看门狗仍触发 |
| TP-TIME-004 | monotonic 不推进 | 语义时间不受影响 |
| TP-TIME-005 | timestamp in future | 拒绝或按配置容差 |
| TP-TIME-006 | PerceptionHealth 到达 stale | 最后一条 OK 也不得继续视为健康 |
| TP-TIME-007 | Navigation Snapshot stale 且活动导航 | release+cancel，HOLD_QUEUE |
| TP-TIME-008 | Navigation Snapshot stale 且 IDLE | 只记录诊断 |
| TP-TIME-009 | Safety Snapshot stale | 不继续假设 Lease 有效 |
| TP-TIME-010 | cancel timeout | 不当作 confirmed |
| TP-TIME-011 | release timeout | 不写正常终态 |
| TP-TIME-012 | FakeClock | 无真实 sleep，测试即时完成 |

---

## 20. Reason Code 与状态合法性测试

### TP-RSN-001 全量常量唯一

遍历全部常量，名称和值唯一。

### TP-RSN-002 Scope 合法性

每个 reason 只允许权威映射中的 Scope。

### TP-RSN-003 State 合法性

每个 reason 只允许权威映射中的 state。

### TP-RSN-004 未映射 reason

输出：

```text
REASON_REASON_CODE_MAPPING_MISSING
```

并 HOLD_QUEUE。

### TP-RSN-005 Adapter 原始值

Action 数值、String 状态和日志文本不得直接进入 `reason_code`。

### TP-RSN-006 Command Scope 成功

控制命令成功使用 `STATE_SUCCEEDED`，不使用不存在的 `STATE_APPLIED`。

### TP-RSN-007 Execution Scope 原 command_id

控制命令不覆盖 execution 的原始 mission command_id。

### TP-RSN-008 progress/distance 边界

只允许：

- progress=-1 或 [0,1]；
- distance=-1 或 >=0。

---

## 21. RobotStatus 测试

| ID | 场景 | 预期 |
|---|---|---|
| TP-ROB-001 | localization_ok=true | frame_id=map，pose 可用 |
| TP-ROB-002 | localization_ok=false | pose 不得被消费者使用 |
| TP-ROB-003 | goal accepted、Lease 未得 | navigation_active=true，autonomous_enabled=false |
| TP-ROB-004 | cancel 已请求未确认 | navigation_active=true |
| TP-ROB-005 | cancel confirmed | navigation_active=false |
| TP-ROB-006 | Lease 回锁、cancel pending | autonomous_enabled=false，navigation_active=true |
| TP-ROB-007 | 速度 | 来自测量 odometry，不来自 candidate/cmd |
| TP-ROB-008 | TRANSIENT_LOCAL | 晚加入 HMI 获取最近状态 |
| TP-ROB-009 | Safety String | 不作为永久结构化解析合同 |

---

## 22. QoS 与 ROS 2 组件测试

接口包和 M1 节点创建后执行。

### TP-QOS-001 TaskCommand

- RELIABLE；
- VOLATILE；
- KEEP_LAST 10；
- 节点重启后旧命令不自动重放。

### TP-QOS-002 TaskStatus

- RELIABLE；
- TRANSIENT_LOCAL；
- KEEP_LAST 10；
- 晚加入订阅者收到保留状态；
- 保留状态不触发执行。

### TP-QOS-003 RobotStatus

- RELIABLE；
- TRANSIENT_LOCAL；
- KEEP_LAST 1。

### TP-QOS-004 CleaningTargetArray

- RELIABLE；
- VOLATILE；
- KEEP_LAST 5。

### TP-QOS-005 PerceptionHealth

- RELIABLE；
- TRANSIENT_LOCAL；
- KEEP_LAST 1；
- 即使收到保留 OK，也必须经过 stale 检查。

### TP-QOS-006 不兼容 QoS

使用故意不兼容订阅者验证无法通信，并确认诊断能够发现配置错误。

---

## 23. 启动与关闭测试

使用 `launch_testing_ros`，至少覆盖：

- Mission Manager 启动成功；
- Mock Navigation Adapter 启动成功；
- Mock Safety Adapter 启动成功；
- 必需 Topic 存在；
- QoS 与文档一致；
- 未出现 `/cmd_vel` 发布者；
- 未直接创建 `/follow_path` client；
- SIGINT 后所有节点正常退出；
- 不残留后台 timer；
- 测试失败时保存节点日志；
- 关闭过程中不重复发布任务终态。

---

## 24. 性能与资源测试候选

M1 只做基础守门，不声称 J6M 实时性能已通过。

候选指标：

- 单条命令纯逻辑处理时间；
- 1000 条幂等查询；
- 队列上限 10 下的内存稳定性；
- 1000 个 Target Registry 更新；
- 1000 个旧回调过滤；
- TaskStatus terminal cache 上限；
- bounded string 生效；
- 无无限增长列表；
- 无每周期 Goal 重发；
- 无繁忙轮询。

具体 J6M 性能预算留给部署阶段。

---

## 25. M1 阻塞级验收门槛

M1 只有同时满足以下条件才可判定通过：

1. L0 静态一致性全部 PASS；
2. L1 单元测试全部 PASS；
3. L2 组件 Mock 测试全部 PASS；
4. L3 launch/QoS 测试全部 PASS；
5. 无跳过的阻塞级测试；
6. 无真实 A*、Path Bridge、TEB、Safety 依赖；
7. 所有超时测试不使用真实 sleep；
8. 所有旧 execution/generation 回调被隔离；
9. PAUSE/STOP/RETURN_HOME 的 cancel/release 收口顺序通过；
10. ESTOP/RESET_ESTOP 全矩阵通过；
11. reason policy 无缺失映射；
12. `git diff --check` 通过；
13. `colcon test-result --verbose` 无失败；
14. 测试证据已落盘；
15. `PROJECT_STATUS.md` 只记录实际通过范围。

---

## 26. 非阻塞候选项

以下不阻塞 M1：

- v1.1 SpatialGoalRequest 运行测试；
- RobotStatus 电池字段；
- 自动重选视觉目标；
- 真实 Navigation Adapter；
- 真实 Safety Adapter；
- Gazebo 运动；
- J6M 性能；
- 清扫执行器；
- APP 手动遥控。

非阻塞项不得被误写为已经实现。

---

## 27. 测试目录建议

M1 包创建后建议：

```text
src/cleannav_mission_manager/
├── cleannav_mission_manager/
│   ├── domain/
│   ├── adapters/
│   └── node.py
├── test/
│   ├── unit/
│   │   ├── test_command_validation.py
│   │   ├── test_deduplication.py
│   │   ├── test_queue.py
│   │   ├── test_state_machine.py
│   │   ├── test_reason_policy.py
│   │   ├── test_target_registry.py
│   │   └── test_fake_clock.py
│   ├── component/
│   │   ├── test_mock_navigation_adapter.py
│   │   ├── test_mock_safety_adapter.py
│   │   └── test_status_publishing.py
│   └── launch/
│       ├── test_mission_manager_mock_launch.py
│       └── test_qos_contract_launch.py
```

具体目录可在 M1 包骨架审查时调整，但测试分层不得丢失。

---

## 28. 测试证据格式

建议每次阶段验收保存：

```text
docs/evidence/mission_manager/
├── m1_unit_<date>.log
├── m1_component_<date>.log
├── m1_launch_<date>.log
├── m1_test_results_<date>.txt
└── m1_summary_<date>.md
```

`m1_summary` 至少包含：

- Git commit；
- ROS 2 版本；
- Python 版本；
- 测试命令；
- PASS/FAIL/SKIP 数量；
- 失败列表；
- 已知限制；
- 是否使用真实 Adapter；
- 是否运行 Gazebo；
- 结论边界。

---

## 29. 计划中的验证命令

接口包和 M1 包创建后，候选命令为：

```bash
cd ~/code/cleannav
source /opt/ros/humble/setup.bash
colcon build --packages-select cleannav_interfaces cleannav_mission_manager
source install/setup.bash
colcon test --packages-select cleannav_interfaces cleannav_mission_manager --event-handlers console_direct+
colcon test-result --verbose
```

在包创建前不得执行并声称通过。

---

## 30. 当前未决项

| 项目 | 状态 |
|---|---|
| pytest 与 unittest 的最终组织方式 | M1 包骨架决定 |
| launch 测试是否使用 launch_pytest | M1 工具链审查 |
| timeout 默认参数 | 测试实现前冻结候选 |
| terminal cache 容量与淘汰 | M1 设计 |
| RobotStatus 发布周期 | M1 设计 |
| Reason 常量组织方式 | M0 总审查 |
| 真实 Adapter 测试合同 | 导航修复后另建计划 |
| J6M 性能门槛 | 部署阶段确定 |

---

## 31. 后续顺序

1. 审查并冻结本测试计划；
2. 修订 `cleannav_interfaces` 候选包；
3. 构建并静态验证接口包；
4. 更新 `PROJECT_STATUS.md`；
5. 执行 M0 总审查；
6. 创建 M1 `cleannav_mission_manager` 包骨架；
7. 实现 L1 单元测试；
8. 实现 L2 组件 Mock 测试；
9. 实现 L3 launch/QoS 测试；
10. M1 总验收。
