# Changelog

本文记录 `cleannav_mission_manager` 的主要开发里程碑。

细粒度开发历史以 Git commit 为准。

## Unreleased

### Repository Modularization

已完成：

- 从原 CleanNav monorepo 提取 Mission Manager
- 保留 17 个 Mission Manager 有效历史 commit
- 将 ROS 2 package 重定位到独立仓库根目录
- 保留 M0 与 M1 的历史关系
- 新增独立仓库 `.gitignore`
- 移除 Task Catalog 测试对 monorepo 固定相邻目录的依赖
- 改用 ROS 2 `ament_index` 定位 `cleannav_interfaces`
- 完成独立 ROS 2 build / test 验证

独立仓库适配 commit：

`5f76fb749041fd288e15d1fde17194d4f7593d3f`

验证结果：

- colcon build：PASS
- colcon test：PASS
- 213 tests
- 0 errors
- 0 failures
- 1 skipped
- direct unit：210 passed
- flake8 / pep257：PASS

## M1 — In Progress

当前已经完成：

1. ROS 2 Python package skeleton
2. FakeClock
3. Domain Models
4. Command Validation
5. Command Idempotency
6. Task Catalog Loader
7. FIFO Mission Queue
8. Command Record Store
9. M1 Execution Plan Freeze
10. Execution Record Store

M1 总体执行方案：

`docs/mission_manager_m1_execution_plan.md`

### 当前 M1 基线

原 monorepo M1-2g commit：

`0830c301084df41fd2e39501c6d52e10c5465892`

独立仓库对应 commit：

`1b487dddea9ab75d04b76b986ecfcfc46a61bf71`

当前下一阶段：

`M1-2h Generation / stale callback gate`

### 后续冻结执行顺序

M1-2h 完成后依次进入：

1. Pure State Machine
2. Mock Navigation Adapter
3. Mock Safety Adapter
4. Mission Manager Core
5. ROS glue
6. Full Mock Test

## M0 — Frozen

M0 已完成：

- 总体架构冻结
- ROS 2 接口设计冻结
- 状态机冻结
- Reason Code 冻结
- 测试计划冻结
- 文档一致性收口
- 最终总审查

原 monorepo M0 最终 commit：

`f8c10109ab04dd1ddaa57241896d22daa4c5e454`

独立仓库对应 commit：

`a2d7baca48debc74bc4982fbbb4c83a33bd64aae`

M0 当前视为冻结基线，不因拆仓重新定义其技术结论。

## Migration History

完整的原 monorepo commit 与独立仓库 commit 映射将在：

`docs/MONOREPO_MIGRATION.md`

中长期保存。

拆仓不会通过伪造 commit 的方式补写历史。

历史过滤导致 commit SHA 改变属于正常现象；相关 Mission Manager 的开发顺序、主题和文件历史得到保留。
