# CleanNav Mission Manager M0 接口设计

> **状态：** M0-2 接口冻结候选（M0 总审查回填版）。
> **边界：** 本文冻结 Topic、QoS、消息职责、字段语义、时间和坐标系；`cleannav_interfaces` v1.0 候选包已落地并完成首次构建验证。
> **实现决定：** v1.0 Reason 常量直接定义在 `TaskStatus.msg`，不创建独立 `ReasonCodes.msg`；bounded string 上界以当前已构建消息定义为准。

---

## 1. 文档目的与状态

本文用于冻结 Mission Manager 与 APP、离线语音、感知模块之间的组间接口合同，并记录现有导航与 Safety 接口的上下文边界。

当前事实：

- `src/cleannav_interfaces` 已创建，当前 v1.0 候选包是接口实现的权威来源；旧 `cleannav_interfaces_v1_0_template.zip` 仅保留为历史参考；
- v1.0 当前只生成 TaskCommand、TaskStatus、RobotStatus、CleaningTarget、CleaningTargetArray、PerceptionHealth 共 6 个消息；
- 接口包已完成静态一致性检查和 `colcon build --packages-select cleannav_interfaces` 首次构建验证；6 个接口可由 `ros2 interface show` 识别并完成 Python 导入，3 个配置文件已安装；
- M1 尚未开始，只允许使用 v1.0 组间接口和 Mock Navigation/Safety Adapter；
- `SpatialGoalRequest` 仅作为 v1.1 文档预留，当前不生成、不订阅；
- `ManualDriveRequest` 不属于当前 v1.0，也不属于当前 v1.1，正式候选包不生成该消息；
- 本文不把 B1-4g 单路径隔离实验扩大为 Continuous Navigation V1 已完成，也不把当前 `/goal_pose` 写成具备完整 accepted/cancel/result 合同。

---

## 2. 接口分层与所有权

### 2.1 组间接口

| Topic | 消息类型 | 发布者 | 订阅者 | 版本 | 用途 | M1 验收 |
|---|---|---|---|---|---|---|
| `/cleannav/hmi/task_command` | `cleannav_interfaces/msg/TaskCommand` | APP Bridge、语音 Bridge、Mock HMI | Mission Manager | v1.0 | 预定义 mission 或控制命令 | 是 |
| `/cleannav/task_status` | `cleannav_interfaces/msg/TaskStatus` | Mission Manager | APP Bridge、语音 Bridge、Mock HMI | v1.0 | 对外任务生命周期状态 | 是 |
| `/cleannav/robot_status` | `cleannav_interfaces/msg/RobotStatus` | Mission Manager / Status Aggregator | HMI 模块 | v1.0 | 机器人综合状态 | 是 |
| `/cleannav/perception/cleaning_targets` | `cleannav_interfaces/msg/CleaningTargetArray` | 感知模块或感知桥接节点 | Mission Manager | v1.0 | `map` 坐标下的环境观测目标 | 是 |
| `/cleannav/perception/health` | `cleannav_interfaces/msg/PerceptionHealth` | 感知模块 | Mission Manager、HMI | v1.0 | 感知子系统健康状态 | 是 |
| `/cleannav/hmi/spatial_goal_request` | `cleannav_interfaces/msg/SpatialGoalRequest` | APP Bridge、语音 Bridge、Mock HMI | Mission Manager | v1.1 预留 | APP 地图点击或语音受限相对目标 | 否 |

冻结规则：

- APP 和语音原始数据必须先经过各自 Bridge，不直接调用导航接口；
- CleaningTargetArray 是环境观测，不进入 Command Gateway；
- HMI、感知和 Mission Manager 都不得直接发布最终 `/cmd_vel`；
- SpatialGoalRequest 不并入 TaskCommand。

### 2.2 现有导航与 Safety 上下文接口

以下接口不属于 `cleannav_interfaces` 组间消息合同，但决定真实 Adapter 的后续映射边界。

| Topic / Service | 当前发布/服务方 | 当前订阅/调用方 | 说明 |
|---|---|---|---|
| `/goal_pose` | 未来真实 Navigation Adapter | A* 全局规划器 | 单次目标入口；当前类型为 `PoseStamped`，不携带 execution/generation |
| `/cleannav/global_path` | A* 全局规划器 | Path Bridge | A* 路径输出 |
| `/cleannav/global_planner_status` | A* 全局规划器 | 诊断/未来 Adapter | 当前为字符串状态 |
| `/cleannav/path_executor_status` | Path Bridge | 诊断/未来 Adapter | 当前为字符串状态 |
| `/follow_path` | Path Bridge Action Client | `controller_server` Action Server | 导航内部 Action，Mission Manager 不直接调用 |
| `/cleannav/cmd_vel_candidate` | TEB `controller_server` | Safety Supervisor | 候选速度 |
| `/cleannav/safety/emergency_stop` | Mission Manager 的软件急停出口或未来安全输入桥 | Safety Supervisor | Bool 急停输入；最高优先级 |
| `/cleannav/safety/set_autonomous_enabled` | Safety Supervisor Service | 未来真实 Safety Adapter | 当前 `SetBool` 授权接口 |
| `/cleannav/safety_supervisor_status` | Safety Supervisor | 诊断/未来 Safety Adapter | 当前为 String，不作为最终结构化合同 |
| `/cmd_vel` | Safety Supervisor | 当前仿真差速驱动；未来实车底盘驱动 | 最终速度唯一出口 |

---

## 3. QoS 合同

### 3.1 v1.0

| Topic | Reliability | Durability | History | Depth |
|---|---|---|---|---:|
| `/cleannav/hmi/task_command` | RELIABLE | VOLATILE | KEEP_LAST | 10 |
| `/cleannav/task_status` | RELIABLE | TRANSIENT_LOCAL | KEEP_LAST | 10 |
| `/cleannav/robot_status` | RELIABLE | TRANSIENT_LOCAL | KEEP_LAST | 1 |
| `/cleannav/perception/cleaning_targets` | RELIABLE | VOLATILE | KEEP_LAST | 5 |
| `/cleannav/perception/health` | RELIABLE | TRANSIENT_LOCAL | KEEP_LAST | 1 |

### 3.2 v1.1 预留

| Topic | Reliability | Durability | History | Depth |
|---|---|---|---|---:|
| `/cleannav/hmi/spatial_goal_request` | RELIABLE | VOLATILE | KEEP_LAST | 10 |

### 3.3 QoS 解释

- 发布者和订阅者都必须显式配置兼容 QoS；
- 需要获取历史保留样本的订阅者必须请求 `TRANSIENT_LOCAL`；
- TaskCommand 和 SpatialGoalRequest 使用 `VOLATILE`，避免节点重启后自动重放旧命令；
- CleaningTargetArray 选择 RELIABLE 是因为它是低频语义目标列表，而不是高频原始传感器流；
- PerceptionHealth 即使使用 TRANSIENT_LOCAL，消费者仍必须基于时间戳检查新鲜度；
- TaskStatus depth=10 可能向晚加入订阅者交付多条历史状态。HMI 必须按 `header.stamp`、`execution_id` 和 `command_id` 排序与去重，不得把历史状态当作新任务请求。

---

## 4. TaskCommand

### 4.1 v1.0 已实现字段

```text
std_msgs/Header header
string<=16 interface_version
string<=128 command_id
uint8 source
uint16 task_id
float32 confidence
string<=512 raw_text
builtin_interfaces/Duration valid_for
```

### 4.2 字段语义

| 字段 | 冻结语义 |
|---|---|
| `header.stamp` | Bridge 使用当前 ROS Clock 生成 |
| `header.frame_id` | 必须为空 |
| `interface_version` | 当前为 `"1.0"` |
| `command_id` | HMI 请求幂等键 |
| `source` | VOICE、APP、MOCK；公共 HMI Topic 不接受伪造的 INTERNAL 来源 |
| `task_id` | Task Catalog 中的预定义 mission 或控制命令编号 |
| `confidence` | APP 固定为 1.0；语音填离线识别置信度 |
| `raw_text` | 审计信息，不参与任务执行语义；无内容时使用空字符串 |
| `valid_for` | 自 `header.stamp` 起计算的请求有效期 |

### 4.3 校验规则

- `now_ros > header.stamp + valid_for` 时拒绝为过期命令；
- `valid_for` 必须大于 0；
- `task_id` 必须存在、启用且允许当前来源；
- `raw_text` 不得作为 task_id 推断后的第二套执行语义；
- 内部状态机事件不通过 `/cleannav/hmi/task_command` 伪装成 `SOURCE_INTERNAL`；
- 控制命令不进入普通 Mission Queue；
- ESTOP 必须优先处理；
- RESET ESTOP 只允许 APP/MOCK，并执行额外安全确认。

### 4.4 command_id 幂等

相同 `command_id`：

- 语义内容相同：不重复执行，重放该请求已有的 TaskStatus；
- 语义内容不同：拒绝为重复 ID 冲突。

TaskCommand 的语义比较至少包括：

- `interface_version`
- `source`
- `task_id`
- `valid_for`

`header.stamp`、`confidence` 和 `raw_text` 属于请求元数据，不单独创造新任务语义。Bridge 重试时应尽量重发原始消息，而不是重新生成新时间戳。

不同 `command_id` 可以重复执行相同 `task_id`。

### 4.5 v1.0 已实现字符串边界

v1.0 已采用 bounded string：

- `interface_version`：`string<=16`；
- `command_id`：`string<=128`，APP JSON 同步限制为 1～128 字符；
- `raw_text`：`string<=512`。

`command_id` 的 APP JSON 格式冻结为 `^[A-Za-z0-9._:-]+$`。上述上界已经进入当前已构建接口；后续若改变，需要按接口版本与变更控制处理。

---

## 5. TaskStatus

### 5.1 v1.0 已实现字段

```text
std_msgs/Header header
string<=16 interface_version
string<=64 execution_id
string<=128 command_id
uint16 task_id
uint8 status_scope
uint8 state
float32 progress
string<=128 active_target_id
float32 remaining_distance_m
int32 reason_code
string<=512 message
```

v1.0 当前实现已经完成：

- `status_scope` 已进入 `TaskStatus.msg`，用于区分命令处理结果和 execution 生命周期；
- 已统一使用 `reason_code`，不存在旧 `error_code` 字段；
- 0～16 状态常量和正式 Reason 常量均直接定义在 `TaskStatus.msg`。

### 5.2 status_scope

冻结：

```
uint8 SCOPE_UNKNOWN   = 0
uint8 SCOPE_COMMAND   = 1
uint8 SCOPE_EXECUTION = 2
```

SCOPE_COMMAND：

- 表示一条 `TaskCommand` 或 `SpatialGoalRequest` 的处理结果；
- 用于 `ACCEPTED`、`REJECTED`、`QUEUED`、`SUCCEEDED`、`FAILED`、`CANCELED`；
- `command_id` 为当前命令；
- `execution_id` 尚不存在时为空；
- 控制命令的成功只表示其副作用已经应用和收口，不表示受影响 mission 已经导航完成。

SCOPE_EXECUTION：

- 表示一个 mission execution 的生命周期；
- `command_id` 使用创建该 execution 的原始普通 mission 的 command_id；
- execution 激活后必须携带 `execution_id`；
- PAUSE、STOP、RETURN_HOME、ESTOP 等控制命令自身的 `command_id` 不覆盖原 execution 的 `command_id`。

### 5.3 字段语义

| 字段 | 冻结语义 |
|---|---|
| `header.stamp` | 该状态生成时间，使用 ROS Clock |
| `header.frame_id` | 空 |
| `execution_id` | 一次普通 mission 或返航 mission 的实例标识；拒绝前无法创建 execution 时可为空 |
| `command_id` | `SCOPE_COMMAND` 时为当前命令 ID；`SCOPE_EXECUTION` 时为创建该 execution 的原始普通 mission command_id |
| `task_id` | 对应 Task Catalog 编号 |
| `state` | 对外简化状态；不暴露全部内部中间状态。冻结枚举见本节 state 对照表 |
| `progress` | 正常范围 0.0～1.0；不可用时为 -1.0 |
| `active_target_id` | 视觉任务关联目标；无目标时为空 |
| `remaining_distance_m` | 可计算时为非负值；不可用时为 -1.0 |
| `reason_code` | 正常、等待、拒绝、取消、失败和安全阻断原因。精确数值由 `mission_manager_reason_codes.md` 冻结（非负 int32） |
| `message` | 人类可读说明，不参与程序控制 |

### 5.4 对外状态枚举（冻结，共 17 个值）

引用自 `mission_manager_state_machine.md`：

```
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

规则：

- 不增加 `STATE_APPLIED`；
- Command Record 的 APPLIED/TERMINAL 成功对外映射为 `SCOPE_COMMAND` + `STATE_SUCCEEDED`；
- 内部 FINALIZING 不直接暴露为外部状态；
- 不把全部内部状态逐一暴露给 HMI。

### 5.5 Reason Code

精确数值区间由 `docs/mission_manager_reason_codes.md` 冻结。核心原则：

| 区间 | 分类 |
|------|------|
| 0 | NONE |
| 1–99 | 正常生命周期 |
| 100–199 | 命令校验 |
| 200–299 | Task Catalog 与队列 |
| 300–399 | 感知与目标 |
| 400–499 | Navigation |
| 500–599 | Safety 与 Lease |
| 600–699 | 超时、stale 与时钟 |
| 700–799 | 收口、状态机与合同 |
| 800–899 | 预留 |
| 900–999 | 内部错误 |

冻结规则：

- `reason_code` 使用非负 int32；
- 已发布数值不得改变含义或复用；
- `message` 不替代 `reason_code`；
- Adapter 不得直接透传 Action 数值、String 状态或日志文本；
- 底层状态必须映射为稳定 Reason Code；
- 未映射错误使用对应内部映射缺失原因并 HOLD_QUEUE。

### 5.6 队列项过期

冻结语义：

- 新命令接收时已经过期：`SCOPE_COMMAND` + `STATE_REJECTED`；
- 已经 ACCEPTED/QUEUED、但激活前过期：`SCOPE_COMMAND` + `STATE_CANCELED`；
- `reason_code` 使用 `REASON_QUEUED_COMMAND_EXPIRED`；
- 不创建 execution_id，不执行该任务。

### 5.7 规则

- `state` 常量已由状态机文档冻结（17 个值，见 5.4）；
- `reason_code` 常量已由 Reason Code 文档冻结（区间见 5.5，完整数值见 `docs/mission_manager_reason_codes.md`）；
- `progress` 有效范围 0.0–1.0，不可用时为 -1.0；
- `remaining_distance_m` 有效时 >=0，不可用时为 -1.0；
- REJECTED 且尚未创建 execution 时 `execution_id` 为空；
- QUEUED 时 `execution_id` 为空；
- execution 激活后 `execution_id` 必须非空；
- `message` 只供人读，不用于程序控制；
- 程序只能依据 `status_scope`、`state`、`reason_code` 和 ID 字段；
- Terminal TaskStatus 必须缓存，用于 `command_id` 幂等重放；
- 相同 `command_id` 的幂等重放可以再次发布缓存状态，但不得重复执行任何副作用；
- HMI 不得根据 `message` 文本驱动逻辑；
- TRANSIENT_LOCAL 的历史状态只用于恢复显示，不得触发任务执行。

---

## 6. RobotStatus

### 6.1 v1.0 已实现字段

```text
std_msgs/Header header
string<=16 interface_version
uint8 system_state
bool localization_ok
geometry_msgs/Pose pose
bool navigation_active
bool emergency_stop_active
bool autonomous_enabled
float32 linear_velocity_mps
float32 angular_velocity_rps
string<=256 message
```

### 6.2 冻结语义

| 字段 | 冻结语义 |
|---|---|
| `header.stamp` | 综合状态采样时间，ROS Clock |
| `header.frame_id` | pose 有效时为 `map`；无效时为空 |
| `system_state` | 面向 HMI 的综合状态，不等同于 TaskStatus.state |
| `localization_ok` | false 时 pose 不得被消费者使用 |
| `pose` | localization_ok=true 时的 `map` 位姿 |
| `navigation_active` | 当前存在活动或清理中的 navigation generation；发出 cancel 请求不等于 navigation_active=false；只有导航确认结束后才变为 false |
| `emergency_stop_active` | 来自结构化 Safety Snapshot 或等价可信输入 |
| `autonomous_enabled` | Safety 当前是否实际授权。navigation_active 与 autonomous_enabled 不能混为同一字段——导航可以已接受但 Safety 尚未授权，Safety 可以回锁而导航取消仍在确认中 |
| `linear_velocity_mps` | 机器人测量线速度，优先来自 odometry，不是候选或最终命令速度 |
| `angular_velocity_rps` | 机器人测量角速度，优先来自 odometry |
| `message` | 人类可读摘要 |

`navigation_active` 与 `autonomous_enabled` 是不同概念：

- 导航可以已接受但 Safety 尚未授权；
- Safety 可以回锁而导航取消仍在确认中。

### 6.3 候选扩展

后续可增加：

```text
bool battery_valid
float32 battery_percentage
```

RobotStatus 不替代 TaskStatus，也不应把 `safety_supervisor_status` 字符串解析作为永久实现。

---

## 7. CleaningTarget

### 7.1 坐标与时间

- `header.frame_id` 必须为 `map`；
- `header.stamp` 是该目标位置估计时间；
- centroid 与 footprint 均使用相同的 `map` frame；
- `valid_for` 从 `header.stamp` 起计算；
- `projection_valid=false` 时不得生成导航目标。

### 7.2 v1.0 已实现目标类型

当前 `CleaningTarget.msg` 使用以下连续编号：

```text
TYPE_UNKNOWN=0
TYPE_LEAF=1
TYPE_LEAF_PILE=2
TYPE_PUDDLE=3
TYPE_BOTTLE_CAN=4
TYPE_PAPER_TRASH=5
```

上述 0～5 数值已经进入 v1.0 消息合同；BOTTLE_CAN 和 PAPER_TRASH 是否进入当前比赛 Demo 仍可由任务配置决定，但不得在 v1.0 内改变既有数值语义。新增目标类型应向后追加并经过接口变更审查。

### 7.3 observation_state 已实现定义

当前 `CleaningTarget.msg` 已采用安全默认值 `OBSERVATION_UNKNOWN=0`：

```text
OBSERVATION_UNKNOWN=0
OBSERVATION_NEW=1
OBSERVATION_CONFIRMED=2
OBSERVATION_LOST=3
OBSERVATION_EXPIRED=4
OBSERVATION_INVALID=5
```

冻结语义：

- NEW：首次有效观测；
- CONFIRMED：连续观测后确认；
- LOST：暂时未观测到，位置为最后已知值，不可自动重提目标；
- EXPIRED：已超过有效期；
- INVALID：投影或数据不合法；
- UNKNOWN：未初始化或未知。

### 7.4 其他字段

| 字段 | 语义 |
|---|---|
| `target_id` | 在目标生命周期内稳定 |
| `source` | 感知来源 |
| `class_name` | 可选文字标签；无内容时为空 |
| `confidence` | 感知置信度 |
| `centroid` | `map` frame 中心点 |
| `footprint` | `map` frame 平面轮廓 |
| `area_m2` | 估计面积 |
| `position_uncertainty_m` | 位置不确定度 |
| `valid_for` | 有效期 |

CleaningTarget：

- 不直接写 Costmap；
- 不直接成为 `/goal_pose`；
- 普通位置更新不自动触发新 Goal；
- 目标失效或显著偏移需要重规划时，必须先取消旧 generation 并等待确认；
- 执行中目标明确为 INVALID 或 EXPIRED 时，M1 默认将当前 execution 结束为 FAILED；
- M1 默认不自动重选目标；自动重选和创建新 generation 属于后续可配置策略。

---

## 8. CleaningTargetArray

冻结规则：

- `header.frame_id` 必须为 `map`；
- `header.stamp` 表示该批次生成时间；
- 同批目标的 frame 必须与数组 header 一致；
- 各目标可以保留各自的观测时间；
- `sequence` 只用于批次排序，不用于 command 幂等；
- 空数组表示“该批次没有当前观测目标”，不代表立即删除 Target Registry 中全部目标；
- 目标删除依赖 observation_state、valid_for 和 Registry 超时规则；
- 单纯从一批数组中缺失，不等于目标已永久删除。

---

## 9. PerceptionHealth

冻结规则：

- 表达感知子系统健康，不表达单个 CleaningTarget 状态；
- 使用 TRANSIENT_LOCAL 保留最新健康状态；
- `header.frame_id` 为空；
- `header.stamp` 为健康状态采样时间；
- Mission Manager 必须同时检查状态值和消息新鲜度；
- 超过配置的 stale timeout 后，即使最后一条消息是 STATE_OK，也不得继续视为健康；
- stale timeout 是节点参数，不写死在消息定义中；
- `message` 仅供诊断。

---

## 10. SpatialGoalRequest（v1.1 草案）

本阶段只冻结草案，不创建 `.msg`。

### 10.1 候选字段

```text
uint8 SOURCE_UNKNOWN=0
uint8 SOURCE_VOICE=1
uint8 SOURCE_APP=2
uint8 SOURCE_MOCK=3

uint8 MODE_UNKNOWN=0
uint8 MODE_ABSOLUTE_MAP=1
uint8 MODE_RELATIVE_ROBOT=2

std_msgs/Header header
string interface_version
string command_id
uint8 source
uint8 mode
geometry_msgs/PoseStamped requested_pose
bool orientation_valid
float32 confidence
builtin_interfaces/Duration valid_for
```

本版暂不加入 `require_confirmation`，因为当前尚无完整的确认令牌和确认响应协议。需要确认的任务继续由 Task Catalog 的 `requires_confirmation` 和后续 HMI 协议设计处理。

### 10.2 双 Header 语义

| Header | 语义 |
|---|---|
| 外层 `header` | 请求生成时间；`frame_id` 为空；用于 valid_for 和 command 幂等审计 |
| `requested_pose.header` | 空间参考时间和坐标系；用于 TF 变换 |

### 10.3 模式语义

#### MODE_ABSOLUTE_MAP

- `requested_pose.header.frame_id=map`；
- position 必须有限；
- z 在二维导航中必须为 0 或由 Validator 归零；
- orientation_valid=true 时 quaternion 必须有限且归一化；
- orientation_valid=false 时由 Goal Resolver 按当前航向或路径策略生成目标朝向。

#### MODE_RELATIVE_ROBOT

- `requested_pose.header.frame_id=base_footprint`；
- requested_pose 表示接收时刻的相对位姿；
- Mission Manager 使用 requested_pose 的时间戳完成一次 TF 变换；
- 转换后立即固化为 map pose，不继续跟随机器人；
- 不允许通过负 x 请求绕过当前禁止倒车策略；是否支持后方目标留待 v1.1 审查。

### 10.4 通用规则

- command_id 幂等与 TaskCommand 一致；
- `now_ros > header.stamp + valid_for` 时拒绝；
- 不用于速度控制；
- 不直接发布 `/goal_pose`；
- 不属于 M1 当前验收；
- 它未来也使用相同 `status_scope`、`state` 和 `reason_code` 返回处理结果；
- 正式 v1.0 不生成也不订阅 `SpatialGoalRequest`；若未来已经生成该消息、但功能开关关闭或接口版本不支持，则以 `REASON_SPATIAL_GOAL_NOT_SUPPORTED_IN_VERSION` 拒绝；
- 相对目标负 x 默认拒绝，不能绕过当前禁止倒车策略。

---

## 11. ManualDriveRequest

旧模板曾包含 `msg/ManualDriveRequest.msg`，但当前正式候选包已经不包含、也不生成该消息。

冻结结论：

- 不纳入接口 v1.0；
- 不纳入当前 v1.1；
- 当前 `rosidl_generate_interfaces` 不生成该消息；
- `src/cleannav_interfaces` 中不存在该 `.msg`；
- README 不得把它写成 v1.1 已支持接口；
- 后续如需恢复 APP 手动遥控，必须单独立项并重新审查 Safety 仲裁、deadman 与权限边界。

---

## 12. Task Catalog

### 12.1 必需字段概念

每个任务至少包含：

```text
id
name
task_kind            # MISSION / CONTROL
enabled
allowed_sources
requires_confirmation
task-specific parameters
```

`task_kind` 是普通 Mission Queue 与控制事件分流的权威字段。

### 12.2 v1.0 当前配置状态

| task_id | 当前状态 | v1.0 处理 |
|---:|---|---|
| 1 START_DEFAULT_CLEANING | disabled | `task_kind=MISSION`；默认队列/路线未配置前不得启用 |
| 2 PAUSE_CURRENT_TASK | enabled | `task_kind=CONTROL`；release Lease、cancel 并等待 confirmed，保留 execution_id 与普通队列 |
| 3 RESUME_CURRENT_TASK | enabled | `task_kind=CONTROL`；从 PAUSED/可恢复 SAFETY_BLOCKED 创建新 generation |
| 4 STOP_CURRENT_TASK | enabled | `task_kind=CONTROL`；安全收口并清空普通等待队列 |
| 5 RETURN_HOME | disabled | `task_kind=CONTROL`；home_pose 未配置前拒绝且不得破坏当前任务/队列 |
| 6 SOFTWARE_EMERGENCY_STOP | enabled | `task_kind=CONTROL`；最高优先级，不进入普通队列 |
| 7 RESET_SOFTWARE_EMERGENCY_STOP | enabled | 仅 APP/MOCK，`requires_confirmation=true`，语音不得解除急停 |
| 10 GOTO_POINT_1 | disabled | `goal_pose: null`；配置真实固定点前不得启用 |
| 20 CLEAN_ROUTE_1 | disabled | `route_id: ""`；配置路线前不得启用 |
| 30～33 视觉目标任务 | enabled | M1 Mock 可用；参数仍是候选运行参数，不扩大为实车已验证值 |

`ranges.system_command` 已作为 1～9 的范围名称；`task_kind` 是普通 FIFO Mission 与 CONTROL 分流的权威字段。所有 task 都必须显式给出 `requires_confirmation`，当前仅 Task 7 为 true。

---

## 13. APP JSON 与语音映射

### 13.1 APP JSON

`app_task_schema.json` 是 APP 到 APP Bridge 的外部 JSON 合同，不是 ROS 消息。

规则：

- Bridge 校验 command_id、task_id、timestamp_ms 和 valid_for_ms；
- `timestamp_ms` 仅用于外部审计和 Bridge 侧请求检查；
- Bridge 使用当前 ROS Clock 生成 TaskCommand.header.stamp；
- Unix 时间不得直接与 Gazebo `/clock` 比较；
- Bridge 固定 `source=SOURCE_APP`；
- 重试时应保留相同 command_id 和相同任务语义。

### 13.2 语音映射

- 离线语音节点只输出映射表允许的 task_id；
- raw_text 用于审计；
- 低置信度命令按阈值拒绝或进入后续确认流程；
- Task 7 不映射语音，语音不得解除急停；
- “前方 5 米”等受限空间指令属于 SpatialGoalRequest，不加入固定 task_id 语音映射。

---

## 14. 时间与坐标系合同

### 14.1 语义时间（使用 ROS Clock）

| 接口 | 时间语义 | frame_id |
|------|----------|----------|
| TaskCommand | Bridge 生成请求时间；ROS Clock | 空 |
| TaskStatus | 状态生成时间；ROS Clock | 空 |
| RobotStatus | 综合状态采样时间；ROS Clock | pose 有效时 `map`，无效时空 |
| CleaningTarget | 目标位置估计时间 | `map` |
| CleaningTargetArray | 目标批次生成时间 | `map` |
| PerceptionHealth | 健康状态采样时间 | 空 |
| SpatialGoalRequest 外层 Header | 请求生成时间；ROS Clock | 空 |
| SpatialGoalRequest absolute pose | 空间参考时间 | `map` |
| SpatialGoalRequest relative pose | 相对指令参考时间 | `base_footprint` |

ROS Clock 用于：

- TaskCommand valid_for
- queued command expiry
- CleaningTarget valid_for
- target wait timeout
- execution 语义超时

过期公式：

```text
request_expired = now_ros > request_stamp + valid_for
target_expired  = now_ros > observation_stamp + valid_for
```

### 14.2 运行看门狗（使用可注入 monotonic）

- navigation accept timeout
- navigation cancel timeout
- Lease acquire / renew / release timeout
- Navigation Snapshot stale timeout
- Safety Snapshot stale timeout
- PerceptionHealth 到达 stale timeout
- emergency clear confirmation timeout

说明：

- `header.stamp` 的过期判断和本地消息到达 stale 看门狗可以同时存在；
- `/clock` 暂停时，运行看门狗仍需推进；
- M1 Mock 不得依赖真实 sleep，必须支持可注入时钟。

### 14.3 Safety Lease 时钟

当前 Safety Lease 使用 `time.monotonic()` 墙钟：

- 仿真 `/clock` 暂停时 Lease 仍可能到期；
- 这是当前安全保守行为，但真实 Adapter 必须显式处理；
- Lease 续期定时器的具体时间源留待真实 Safety Adapter 文档冻结。

---

## 15. 接口包当前实现与构建基线

### 15.1 CMakeLists.txt

当前 `rosidl_generate_interfaces` 已冻结为只生成 6 个 v1.0 消息：

- TaskCommand；
- TaskStatus；
- RobotStatus；
- CleaningTarget；
- CleaningTargetArray；
- PerceptionHealth。

同时冻结：

- Reason 常量直接定义在 `TaskStatus.msg`，不创建独立 `ReasonCodes.msg`；
- 不生成 ManualDriveRequest；
- 不生成 SpatialGoalRequest；
- `config/` 通过 `install(DIRECTORY config DESTINATION share/${PROJECT_NAME})` 安装；
- `ament_export_dependencies(rosidl_default_runtime)` 保留。

当前接口包已经通过首次 `colcon build --packages-select cleannav_interfaces`；6 个接口均可被 `ros2 interface show` 发现并完成 Python 导入。

### 15.2 package.xml

当前 manifest 已完成接口包所需元数据：

- package format=3；
- `ament_cmake` 构建类型；
- `rosidl_default_generators` 为 buildtool dependency；
- `builtin_interfaces`、`std_msgs`、`geometry_msgs` 为接口依赖；
- `rosidl_default_runtime` 为运行依赖；
- `member_of_group=rosidl_interface_packages`；
- 维护者邮箱已替换为可通过 package manifest 校验的有效地址。

具体维护者地址以 `src/cleannav_interfaces/package.xml` 为唯一权威来源，不在本设计文档重复维护。

### 15.3 README 与配置文件

当前候选包已明确：

- README 使用“v1.0 候选接口包”，不冒充最终 frozen contract；
- README 与自研消息注释已中文化；
- v1.0、v1.1 预留与未来未定边界明确；
- ManualDriveRequest 不属于 v1.1；
- `task_catalog.yaml`、`app_task_schema.json`、`voice_task_map_example.yaml` 均随包安装。

### 15.4 v1.0 bounded string 冻结值

ROS 2 消息中的字符串上界以当前已构建 `.msg` 为准：

| 消息 | 字段 | 上界 |
|---|---|---:|
| TaskCommand | interface_version | 16 |
| TaskCommand | command_id | 128 |
| TaskCommand | raw_text | 512 |
| TaskStatus | interface_version | 16 |
| TaskStatus | execution_id | 64 |
| TaskStatus | command_id | 128 |
| TaskStatus | active_target_id | 128 |
| TaskStatus | message | 512 |
| RobotStatus | interface_version | 16 |
| RobotStatus | message | 256 |
| CleaningTarget | interface_version | 16 |
| CleaningTarget | target_id | 128 |
| CleaningTarget | source | 64 |
| CleaningTarget | class_name | 64 |
| CleaningTargetArray | interface_version | 16 |
| CleaningTargetArray | source | 64 |
| PerceptionHealth | interface_version | 16 |
| PerceptionHealth | source | 64 |
| PerceptionHealth | message | 256 |

上述值不再属于 M0 未决项。未来若修改，应走接口版本/兼容性变更审查。

---

## 16. 版本与实施边界

| 接口版本 | 内容 |
|---|---|
| v1.0 | TaskCommand、TaskStatus、RobotStatus、CleaningTarget、CleaningTargetArray、PerceptionHealth |
| v1.1 预留 | SpatialGoalRequest、APP 地图点击、语音受限相对目标 |
| 未来未定 | ManualDriveRequest、SafetyStatus、专用 Safety Lease Service |

| 开发阶段 | 内容 |
|---|---|
| M0 | 接口、状态机、Reason Code、测试计划和候选包修订 |
| M1 | 使用 v1.0 接口和 Mock Navigation/Safety Adapter |
| 后续真实集成 | 接真实 Adapter；不自动等于接口 v1.1 |

---

## 17. 当前未决项

以下事项不阻塞 M0 接口合同冻结：

| 项目 | 状态 / 决策时点 |
|---|---|
| RobotStatus 电池字段 | 当前 v1.0 不增加；需要时以后续版本扩展 |
| CleaningTarget 后续新增目标类型 | 当前 0～5 数值保持不变；新增类型需接口变更审查 |
| 感知 stale timeout 默认数值 | M1 参数冻结，不写入消息 |
| 视觉目标显著偏移阈值 | M1/感知联合验证后决定 |
| SpatialGoalRequest 最终启用时点 | v1.1 预留 |
| APP/语音确认协议 | v1.1/HMI 协议；当前 SpatialGoalRequest 不含 require_confirmation |
| ManualDriveRequest 是否未来恢复 | 当前 v1.0/v1.1 均不支持；若恢复需单独立项 |
| 结构化 SafetyStatus | 真实 Safety Adapter 前决定 |
| 专用 Safety Lease Service | 真实 Safety Adapter 前决定 |
| home_pose、固定点、默认队列和路线配置 | M1/产品配置阶段完成 |
| 真实 Adapter 如何获得任务关联的 accepted/cancel/result | 等待 A* 与 Path Bridge 修复 |
| Lease release 失败是否自动触发软件 ESTOP | 真实 Safety Adapter/安全策略冻结 |
| Lease 续期定时器时间源 | 真实 Safety Adapter 冻结 |
| RESET ESTOP 的外部安全确认来源 | 真实 Safety Adapter 前确定 |

已从未决项移除：Reason 常量组织方式、bounded string 上界、ManualDriveRequest 在当前正式候选包中的处理方式；这些均已由 v1.0 当前实现确定。

---

## 18. 后续顺序

1. 用本 M0 总审查回填版替换旧接口文档；
2. 与 `mission_manager_state_machine.md` 的 M0 总审查回填版一起执行最终跨文档检查；
3. 修订并重新生成 `mission_manager_m0_total_audit.md`，以当前源码和文档为准；
4. 更新 `PROJECT_STATUS.md`，将 M0 从“候选基线”收口为最终冻结；
5. 提交 M0 冻结基线；
6. 进入 M1 `cleannav_mission_manager` Mock 包骨架，不接真实 Navigation/Safety Adapter。
