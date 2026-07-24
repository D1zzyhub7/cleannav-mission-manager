# CleanNav Mission Manager M0 总体架构

> **状态：** M0-1 总体架构冻结候选（修订版）。
> **边界：** 当前只冻结职责、模块关系、执行原则和版本边界；尚未创建 Mission Manager 业务代码，尚未接入真实导航与真实 Safety Lease。
> **事实优先级：** 以 `AGENTS.md`、`docs/PROJECT_STATUS.md`、当前源码和运行证据为准；历史总体架构文档仅作背景参考。

---

## 1. 文档目的与状态

本文用于冻结 Mission Manager 的系统位置、职责边界、内部模块和核心执行规则，为后续以下工作提供统一基线：

1. `mission_manager_interface.md`
2. `mission_manager_state_machine.md`
3. `mission_manager_reason_codes.md`
4. `mission_manager_test_plan.md`
5. `cleannav_interfaces` 候选包修订
6. M1 Mock 包骨架

当前阶段：

- Navigation Execution Baseline Alpha 已完成；
- B1-4g 单路径动态障碍恢复隔离实验已通过；
- Continuous Navigation V1 尚未完成；
- A* 和 Path Bridge 的连续导航问题仍待修复；
- M0/M1 只使用 Mock Navigation Adapter 和 Mock Safety Lease Adapter；
- 真实 Navigation Adapter 必须等待导航稳定性修复与完整回归。

最终部署平台为地平线征程 J6M。车端不部署大语言模型或 VLA；v1.0 采用预定义任务、轻量化规则、确定性状态机和可验证的任务编排。

---

## 2. 当前系统位置

### 2.1 外部输入与内部入口

```text
APP / 离线语音
    └─ TaskCommand ────────────────┐
                                   │
APP / 离线语音（v1.1 预留）        │
    └─ SpatialGoalRequest ─────────┤
                                   ▼
                           Command Gateway
                                   │
                         Validator / Deduplicator
                                   │
                                   ▼
                            Mission Manager
                                   │
视觉识别                           │
    └─ CleaningTargetArray ──→ Target Registry
                                   │
                                   ▼
                              Goal Resolver
                                   │
                              InternalGoal
```

`CleaningTargetArray` 是环境观测，不属于命令，因此不进入 Command Gateway，而是直接进入 Target Registry。

### 2.2 导航与安全执行链

```text
Mission Manager
    ├─ Navigation Adapter
    │      └─ 单次提交 /goal_pose
    │              ↓
    │             A*
    │              └─ /cleannav/global_path
    │                          ↓
    │                     Path Bridge
    │                          └─ /follow_path Action
    │                                      ↓
    │                           controller_server + TEB
    │                                      └─ /cleannav/cmd_vel_candidate
    │                                                       ↓
    └─ Safety Lease Manager ───────────────→ Safety Supervisor
                                                            └─ /cmd_vel
```

冻结边界：

- Mission Manager 不直接调用 FollowPath；
- Navigation Adapter 不直接调用 FollowPath；
- FollowPath 属于 Path Bridge 与 `controller_server` 之间的导航内部接口；
- Safety Supervisor 是最终 `/cmd_vel` 的唯一发布者；
- Mission Manager 只通过 Safety Lease Manager 管理 autonomous 授权，不直接控制速度。

### 2.3 状态输出

```text
Mission State + Navigation Snapshot + Safety Snapshot
                         ↓
                  Status Aggregator
                     ├─ /cleannav/task_status
                     └─ /cleannav/robot_status
```

---

## 3. 架构原则

| 原则 | 冻结说明 |
|---|---|
| 外部命令单一入口 | `TaskCommand` 和 `SpatialGoalRequest` 统一进入 Command Gateway；感知观测进入 Target Registry |
| 命令与观测分离 | HMI 表达“用户要做什么”，视觉表达“环境中检测到什么” |
| 单一活动任务 | 同时最多一个活动 mission；普通任务可以排队 |
| 普通任务 FIFO | 普通 mission 按到达顺序排队；v1.0 不自动抢占当前普通任务 |
| 控制命令不入普通队列 | PAUSE、RESUME、STOP、RETURN_HOME、ESTOP 作为状态机控制事件处理 |
| 单一活动 generation | 同一时刻最多一个活动 navigation generation |
| 每代目标只提交一次 | 不周期性重发 `/goal_pose`，不以重复提交掩盖 A* 或 Path Bridge 问题 |
| 取消确认后再提交 | 旧 generation 未确认取消前，不得提交新 generation |
| 旧回调隔离 | 旧 execution 或旧 generation 的反馈不得改变当前任务状态 |
| 同目标可再次执行 | 上一次任务结束后，相同目标可通过新的 `execution_id` 再次执行 |
| 最终速度安全隔离 | 无有效 Safety Lease 时不得产生最终自主运动；不得绕过 Safety Supervisor |
| 观测目标不等于导航目标 | CleaningTarget 只有经 Goal Resolver 选择和校验后才能形成 InternalGoal |
| J6M 轻量化 | 不部署车端 LLM/VLA，不使用任意自然语言在线规划 |
| 状态可审计 | 每次接收、拒绝、排队、提交、取消、完成和失败均应关联 `command_id`、`execution_id` 与 `generation` |

---

## 4. 外部输入分类

### 4.1 TaskCommand（v1.0）

`TaskCommand` 表达预定义任务或任务控制语义，不携带任意导航坐标。

#### A. 普通 mission 命令

由 `task_id` 映射到 Task Catalog，例如：

- 启动默认清扫任务；
- 前往预定义固定点；
- 执行预定义路线；
- 清扫最近落叶；
- 处理最近轻度积水目标；
- 返回预定义起点或停靠点。

普通 mission 命令经过校验、去重和排队后，形成新的 `execution_id`。

#### B. 控制命令

控制命令驱动当前状态机，不作为普通 mission 排队，也不要求全部映射到 Task Catalog，例如：

- PAUSE；
- RESUME；
- STOP；
- RETURN_HOME；
- ESTOP。

ESTOP 具有最高优先级；急停不得等待普通队列处理。

### 4.2 CleaningTargetArray（v1.0）

视觉模块发布 `CleaningTargetArray`，它是环境观测，不是已批准的导航任务。

Mission Manager 接收的目标应满足：

- 使用稳定 `target_id`；
- 包含目标类型、置信度、时间戳和有效期；
- 空间坐标统一使用 `map` frame；
- 感知侧负责将相机坐标或车体坐标转换为 `map` 坐标；
- 不直接发布 `/goal_pose`；
- 不直接修改 Costmap；
- 普通位置更新不自动触发 `/goal_pose` 重发。

Mission Manager 可以持续监测当前 `target_id` 是否仍然有效。若未来需要因目标失效或显著偏移重新规划，必须：

1. 释放当前 Safety Lease；
2. 取消旧 generation；
3. 等待 `cancel_confirmed`；
4. 创建新 generation；
5. 重新提交一次目标。

显著偏移阈值和目标失效规则留待后续文档冻结。

### 4.3 SpatialGoalRequest（v1.1 预留）

`SpatialGoalRequest` 与 `TaskCommand` 分离，避免预定义任务语义与空间参数混杂。

支持的候选场景：

- APP 地图点击：提交 `map` frame 下的绝对目标；
- 离线语音受限空间指令：例如“前方 5 米”，提交 `base_footprint` frame 下的相对目标。

约束：

- APP 和语音不得直接发布 `/goal_pose`；
- 相对目标使用带时间戳和 frame 的 `PoseStamped` 表达；
- Mission Manager 在接收时通过 TF 一次性转换并固化为 `map` 目标；
- 固化后目标不再跟随 `base_footprint` 移动；
- 必须校验命令有效期、定位、TF、地图范围、目标可达性、当前任务状态和急停状态；
- 该接口不属于手动速度控制；
- 最终仍经过 A*、Path Bridge、TEB 和 Safety Supervisor。

---

## 5. 内部模块划分

以下模块为设计概念，尚未实现。

| 模块 | 主要输入 | 主要输出 | 职责 |
|---|---|---|---|
| Command Gateway | TaskCommand、SpatialGoalRequest | 归一化命令 | 接收、来源适配、基础格式归一化和路由；不负责业务校验或去重 |
| Command Validator | 归一化命令、当前系统状态 | 校验结果 | 校验接口版本、字段合法性、有效期、来源权限、当前状态与命令适用性 |
| Command Deduplicator | 已通过基础校验的命令 | 幂等结果 | 按 `command_id` 处理幂等与冲突 |
| Task Catalog | `task_id` | 任务模板 | 保存预定义固定点、路线、目标选择规则、完成条件和超时策略 |
| Mission Queue | 普通 mission 命令 | FIFO 队列 | 接收普通任务、容量限制、过期回收；控制命令不进入该队列 |
| Mission State Machine | 当前任务、控制事件、Adapter 快照 | 状态跃迁 | 管理任务准备、执行、暂停、恢复、取消、返航、完成和失败 |
| Target Registry | CleaningTargetArray | 有效目标集合 | 按 `target_id` 注册、更新、去重、过期和失效 |
| Goal Resolver | 任务模板、目标集合、配置 | InternalGoal | 将固定点、路线或视觉目标解析为统一内部执行目标 |
| Navigation Adapter | InternalGoal、execution/generation | Navigation Snapshot | 单次提交目标、取消目标、隔离旧反馈、提供稳定导航快照 |
| Safety Lease Manager | execution_id、任务状态 | Safety Snapshot | 获取、续期、释放授权；急停覆盖 Lease |
| Status Aggregator | 状态机、Navigation/Safety 快照 | 聚合状态 | 统一异步状态顺序和对外状态内容 |
| TaskStatus Publisher | 聚合任务状态 | `/cleannav/task_status` | 发布任务生命周期和原因码 |
| RobotStatus Publisher | 聚合机器人状态 | `/cleannav/robot_status` | 发布定位、安全、感知和其他可用状态 |

### 5.1 command_id 幂等规则

- 相同 `command_id` 且内容完全相同：不重复执行，重放该命令已有的 TaskStatus；
- 相同 `command_id` 但内容不同：拒绝，判定为重复 ID 冲突；
- 不以 `task_id`、姿态、时间戳或来源作为主要幂等键；
- 相同 `task_id` 在不同 `command_id` 下允许重复执行。

---

## 6. InternalGoal

`InternalGoal` 是 Mission Manager 内部模型，不是组间冻结 ROS 消息。

| 字段 | 含义 |
|---|---|
| `execution_id` | 一次任务实例的稳定标识 |
| `generation` | 同一 execution 内导航提交的单调递增编号 |
| `source` | APP、VOICE、PERCEPTION_TRIGGER、MOCK 等来源 |
| `source_command_id` | 产生该目标的原始命令 ID |
| `goal_kind` | FIXED_POINT、ROUTE、VISUAL_TARGET、SPATIAL_ABSOLUTE、SPATIAL_RELATIVE |
| `map_pose` | 单点目标时使用的 `map` frame `PoseStamped`；路线任务可为空 |
| `route_id` | 路线任务使用的预定义路线 ID；非路线任务可为空 |
| `target_id` | 视觉目标任务关联的稳定目标 ID；其他任务可为空 |
| `created_time` | InternalGoal 创建时间 |
| `valid_until` | 目标失效时间 |
| `completion_rule` | 到点、路线完成、停留、人工结束等规则 |
| `metadata` | 不参与组间接口冻结的附加内部信息 |

冻结要求：

- `generation` 在同一 `execution_id` 内单调递增；
- 新 execution 从独立 generation 生命周期开始；
- 进入 Navigation Adapter 前，单点目标必须已转换并固化为 `map` frame；
- `metadata` 不得成为绕过正式字段和校验规则的任意通道。

---

## 7. Navigation Adapter 边界

Navigation Adapter 是 Mission Manager 与导航执行链之间的稳定抽象。M1 只实现 Mock。

### 7.1 概念方法

```text
submit_goal(execution_id, generation, internal_goal)
cancel_goal(execution_id, generation)
get_snapshot()
```

### 7.2 Navigation Snapshot

至少提供稳定抽象字段：

- `execution_id`
- `generation`
- `state`
- `goal_accepted`
- `cancel_confirmed`
- `progress`
- `failure_reason`
- `updated_time`

不向 Mission Manager 暴露 Path 点数、TEB 内部状态或 FollowPath 原始回调等下游实现细节。

### 7.3 保护规则

1. 一个 generation 只提交一次；
2. `submit_goal` 的真实职责是向 A* 单次提交 `/goal_pose`；
3. A* 发布 `/cleannav/global_path`；
4. Path Bridge 负责向 `controller_server` 发送 FollowPath；
5. Mission Manager 和 Navigation Adapter 不直接调用 FollowPath；
6. `cancel_goal` 必须等待确认，不能把“已发送取消请求”视为“取消完成”；
7. 旧 execution/generation 的反馈一律忽略；
8. 同一目标在旧任务结束后允许以新的 `execution_id` 再次执行；
9. 多个待更新目标不得并发提交；只能在旧 generation 结束后选择最新有效目标；
10. 当前真实链路尚缺少任务关联的目标接受、取消和状态接口，因此真实 Adapter 的映射方式属于未决项；
11. Path Bridge 和 A* 修复完成前，不接真实 Navigation Adapter。

---

## 8. Safety Lease Manager 边界

### 8.1 概念方法

```text
acquire(execution_id)
renew(execution_id)
release(execution_id)
get_snapshot()
```

### 8.2 Safety Snapshot

至少表达：

- Lease 状态；
- 对应 `execution_id`；
- autonomous 是否有效；
- 剩余时间；
- emergency_stop 状态；
- 最近失败原因；
- 更新时间。

### 8.3 冻结规则

1. M1 使用 Mock Safety Lease Adapter；
2. 真实适配器后续可以兼容现有 `SetBool`，或升级为专用 Lease 接口；
3. 导航目标被 Navigation Adapter 接受后，才申请 Safety Lease；
4. 未获得有效 Lease 时不得进入自主执行阶段；
5. 导航执行期间按既定周期续期；续期策略留给接口文档；
6. 任务完成、暂停、取消或失败时先释放 Lease，再完成对应状态收口；
7. emergency_stop 优先级最高，覆盖任何 Lease；
8. Lease 获取或续期失败时，禁止绕过 Safety，任务转入安全阻断或失败处理；
9. Lease 必须关联当前 `execution_id`，旧任务不得续期或释放新任务的 Lease。

---

## 9. 任务队列与控制命令仲裁

### 9.1 普通 mission

普通 mission：

- 最多一个处于活动状态；
- 其余任务进入 FIFO 队列；
- 队列容量和过期策略留给接口/状态机文档；
- v1.0 不因为新普通任务到达而自动抢占当前任务。

### 9.2 控制命令

控制命令不进入普通 FIFO：

| 命令 | 处理原则 |
|---|---|
| PAUSE | 释放 Lease，取消当前 generation，等待确认后进入暂停 |
| RESUME | 对暂停任务创建新 generation，重新提交并重新获取 Lease |
| STOP | 停止当前任务并清空普通等待队列 |
| RETURN_HOME | 终止当前任务后创建独立返航任务 |
| ESTOP | 立即触发 Safety emergency_stop；优先于所有任务和队列 |

控制命令的精确合法状态和 Reason Code 留给后续状态机与原因码文档。

---

## 10. 任务执行总流程

以下状态名仅表达概念阶段，精确枚举由 `mission_manager_state_machine.md` 冻结。

### 10.1 固定点任务

```text
接收 TaskCommand
→ 基础校验
→ command_id 幂等检查
→ 普通任务入队
→ 任务成为队首并创建 execution_id
→ Task Catalog 解析固定点
→ Goal Resolver 创建 InternalGoal
→ Navigation Adapter 单次 submit_goal
→ 等待 goal_accepted
→ Safety Lease acquire
→ 授权成功后进入执行阶段
→ 监测 Navigation Snapshot
→ 到达完成条件
→ release Safety Lease
→ 发布终态并处理下一排队任务
```

### 10.2 视觉目标任务

```text
CleaningTargetArray 持续更新 Target Registry
→ 接收“清扫最近目标”等 TaskCommand
→ 校验和 command_id 幂等
→ 创建 execution_id
→ Goal Resolver 从有效目标集合中选择 target_id
→ 创建 map frame InternalGoal
→ Navigation Adapter 单次 submit_goal
→ 等待 goal_accepted
→ Safety Lease acquire
→ 授权成功后执行
→ 仅监测 target_id 有效性，普通位置更新不自动重发目标
→ 完成：release Lease → 发布终态
→ 需要重规划：release Lease → cancel → 等待确认 → 新 generation → 单次重提
```

### 10.3 APP 地图点击（v1.1）

```text
接收带 command_id 的 SpatialGoalRequest(map PoseStamped)
→ 校验版本、有效期、来源、地图范围和当前状态
→ 按 command_id 幂等处理
→ 固化 map_pose
→ 创建 execution_id 和 generation
→ submit_goal
→ 等待 goal_accepted
→ acquire Lease
→ 授权成功后执行
```

### 10.4 语音“前方 5 米”（v1.1）

```text
离线语音解析受限方向和距离
→ 生成带 command_id 的 base_footprint PoseStamped
→ Mission Manager 校验距离、有效期、定位和 TF
→ 接收时一次性转换并固化为 map_pose
→ 校验地图范围和目标可用性
→ 创建 execution_id 和 generation
→ submit_goal
→ 等待 goal_accepted
→ acquire Lease
→ 授权成功后执行
```

### 10.5 暂停与恢复

#### PAUSE

```text
接收 PAUSE
→ 立即 release(execution_id)
→ 进入暂停处理中
→ cancel_goal(execution_id, generation)
→ 等待 cancel_confirmed
→ 保留任务上下文和目标信息
→ 发布 PAUSED 对外状态
```

#### RESUME

```text
接收 RESUME
→ 重新校验定位、急停、目标和有效期
→ 创建 new_generation
→ 单次 submit_goal
→ 等待 goal_accepted
→ acquire(execution_id)
→ 授权成功后恢复执行
```

### 10.6 STOP

```text
接收 STOP
→ 立即 release 当前 Lease
→ cancel 当前 generation
→ 等待 cancel_confirmed
→ 当前任务记为取消/停止
→ 清空普通等待队列
→ 回到无活动任务状态
```

### 10.7 RETURN_HOME

```text
接收 RETURN_HOME
→ release 当前 Lease
→ cancel 当前 generation
→ 等待 cancel_confirmed
→ 收口当前任务
→ 创建新的返航 execution_id
→ 从 Task Catalog 读取 home_pose
→ 创建新 generation 并单次 submit_goal
→ 等待 goal_accepted
→ acquire 新 execution 的 Lease
→ 授权成功后执行返航
```

### 10.8 ESTOP

```text
接收 ESTOP 或安全侧急停事件
→ 立即触发 Safety emergency_stop
→ release 当前 Lease
→ 请求取消当前导航并等待确认或记录取消失败
→ 清空普通等待队列
→ 当前任务进入急停终止语义
→ 解除急停后不得自动恢复旧任务
```

---

## 11. 接口版本与开发阶段

### 11.1 接口版本

| 版本 | 内容 |
|---|---|
| v1.0 | TaskCommand、CleaningTargetArray、TaskStatus、RobotStatus；固定点、固定路线和视觉目标任务 |
| v1.1 预留 | SpatialGoalRequest、APP 地图点击、语音受限相对空间目标 |

### 11.2 开发阶段

| 阶段 | 内容 |
|---|---|
| M0 | 架构、接口、状态机、原因码、Mock 测试计划和候选接口修订 |
| M1 | Mission Manager 包骨架、Mock Navigation Adapter、Mock Safety Lease Adapter |
| 后续真实集成 | 修复 A*/Path Bridge 后接入真实 Navigation Adapter 和真实 Safety Adapter |
| J6M 迁移 | 在仿真与接口稳定后开展部署适配，不在当前 M0/M1 中进行 |

真实 Adapter 的接入不自动触发接口版本升级；接口版本和开发阶段分别管理。

---

## 12. 当前明确不做

- 车端 LLM 或 VLA；
- 任意自然语言在线规划；
- 语音或 APP 直接发布 `/goal_pose`；
- APP、语音、视觉或 Mission Manager 直接发布 `/cmd_vel`；
- Mission Manager 或 Navigation Adapter 直接调用 FollowPath；
- Mission Manager 周期性重发 Goal；
- 感知模块直接修改 Costmap；
- APP 手动遥控；
- 清扫执行器 Action；
- 真实 Navigation Adapter；
- 修改 A*、Path Bridge、TEB 或 Safety Supervisor；
- ROS/Gazebo 真实联调；
- J6M 部署。

---

## 13. ROS 2 接口类型原则

| 类型 | 当前用途 |
|---|---|
| Topic | 异步 HMI 命令、感知观测和状态数据流 |
| Service | 快速授权、释放或确认类请求 |
| Action / 状态化 Adapter | 长时间、需要反馈和取消的导航执行 |

在 CleanNav 当前架构中：

- `/goal_pose` 仍是 Mission Manager 到 A* 的单次目标入口；
- `/cleannav/global_path` 是 A* 的路径输出；
- FollowPath 是 Path Bridge 到 `controller_server` 的内部 Action；
- Mission Manager 通过状态化 Navigation Adapter 获得抽象的接受、执行、取消和失败状态；
- 本文不冻结所有 ROS 消息字段，字段细节留给 `mission_manager_interface.md`。

---

## 14. 未决项

| 项目 | 状态 |
|---|---|
| `home_pose` 和固定点坐标的配置格式与来源 | 待接口文档冻结 |
| 默认路线的格式、航点结构和存储方式 | 待接口文档冻结 |
| 普通任务队列容量和过期策略 | 待状态机/接口文档冻结 |
| 视觉目标失效与显著偏移阈值 | 待感知与状态机联合审查 |
| 正式 Progress Checker 参数 | 测试低速参数不得直接升级为正式值 |
| 真实 Navigation Adapter 如何获得任务关联的 accepted/cancel/result | 等待 A* 与 Path Bridge 修复方案 |
| Safety Lease 继续适配 `SetBool` 还是新增专用接口 | 待真实集成审查 |
| 是否新增结构化 SafetyStatus | 当前 `safety_supervisor_status` 为 String |
| SpatialGoalRequest 的最终字段与启用时点 | v1.1 预留 |
| 时间源规则 | ROS Clock、墙钟 Lease 和外部 APP 时间的转换需在接口文档冻结 |

不得把以上未决项写成已经实现或已经验证。

---

## 15. M0 后续顺序

1. 创建 `docs/mission_manager_interface.md`
2. 创建 `docs/mission_manager_state_machine.md`
3. 创建 `docs/mission_manager_reason_codes.md`
4. 创建 `docs/mission_manager_test_plan.md`
5. 修订 `cleannav_interfaces` 候选包
6. 执行 M0 总审查
7. 创建 M1 `cleannav_mission_manager` 包骨架
