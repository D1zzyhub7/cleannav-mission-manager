# CleanNav Mission Manager Monorepo Migration Record

## 1. 目的

本文永久记录 `cleannav_mission_manager` 从原 CleanNav monorepo 拆分为独立 Git 仓库的历史映射、迁移边界和验证证据。

该文档属于迁移审计记录，后续日常开发不应删除。

## 2. 原始 Monorepo

原仓库本地路径：

`~/code/cleannav`

拆仓时分支：

`feature/mission-manager-m1`

拆仓前 Mission Manager 最后 HEAD：

`0830c301084df41fd2e39501c6d52e10c5465892`

对应主题：

`实现 Mission Manager M1 Execution Record Store`

拆仓过程中没有在原 monorepo 上运行 `git filter-repo`。

原仓库历史保持不变。

## 3. 拆仓前安全备份

拆仓前创建完整 Git bundle：

`cleannav-pre-modularization-20260825.bundle`

SHA256：

`ed19c0268b4ed995bd86d1123a3e56913b28cfe7230593e2963fcfbbd4444427`

该 bundle 已完成真实恢复测试，包括：

- clone 成功
- HEAD 一致
- tracked tree 一致
- commit count 一致
- branch 可恢复
- tag 可恢复

拆仓前安全 tag：

`pre-modularization-20260825`

## 4. Mission Manager 提取范围

保留源码：

`src/cleannav_mission_manager/`

在独立仓库中，该 ROS 2 package 被重定位至仓库根目录。

保留设计文档：

- `docs/mission_manager_architecture.md`
- `docs/mission_manager_interface.md`
- `docs/mission_manager_m0_total_audit.md`
- `docs/mission_manager_m1_execution_plan.md`
- `docs/mission_manager_reason_codes.md`
- `docs/mission_manager_state_machine.md`
- `docs/mission_manager_test_plan.md`

`cleannav_interfaces` 没有复制进入本仓库。

其保持独立 ROS 2 package / 独立 Git 仓库边界。

## 5. 历史过滤结果

原 monorepo 共分析：

`22 commits`

其中 Mission Manager 有效历史：

`17 commits`

过滤后为空或与 Mission Manager 无关的 commit 不进入本独立仓库。

由于 Git commit hash 包含 tree 和 parent 信息，路径过滤后 commit SHA 发生变化属于正常且必然的历史重写结果。

## 6. 完整 Commit 映射

| # | 原 monorepo commit | 独立仓库 commit | Commit subject |
|---:|---|---|---|
| 01 | `3b213d3d3a12d404a5cd7e28b7a12a28dbf272d0` | `7b60d02c9b2f1aeded58c50f0a5bfcfee0d6171e` | 冻结 Mission Manager M0 总体架构 |
| 02 | `7c7fc59d34b49822fc3a867c9fa7b681c0660cc8` | `405affadbf0593d8da001d60cbff927c3b30986a` | 冻结 Mission Manager M0 接口设计 |
| 03 | `71bfff2f803c9eb98c98914b8e60123e1a63588a` | `96337ef7ddf5ffebc8a2bedb4fd96f38428e1c60` | 冻结 Mission Manager M0 状态机设计 |
| 04 | `75857a8cf3ed52fdc1e2c6d2677c51b7c6600eca` | `0c00ffc8062db4d9a7825ddbbbda87330869fe3b` | 冻结 Mission Manager M0 Reason Code 设计 |
| 05 | `20a873e1564d2e9ec85229fc95946b056df5484d` | `82ac6a278ca9e264e159c97536843316bad2ca57` | 回填 Mission Manager 状态机与 Reason Code 接口 |
| 06 | `dbb1586aca40156b4ba4a0dc5c208ee94125ff40` | `878c0161134b2295540cf0c26dc1719a923aaa83` | 冻结 Mission Manager M0 测试计划 |
| 07 | `792d113a31874d5a683f13605b925373d311e63a` | `26a9d35586244816c0ec32e9045ac910e917e59b` | 收口 Mission Manager M0 文档一致性 |
| 08 | `f8c10109ab04dd1ddaa57241896d22daa4c5e454` | `a2d7baca48debc74bc4982fbbb4c83a33bd64aae` | 冻结 Mission Manager M0 最终总审查 |
| 09 | `c1aefba24287d5abcf295875acaadd198a86594a` | `0045805abd2d8c450c54bc6ce15ce500924b03db` | 建立 Mission Manager M1 最小包骨架 |
| 10 | `6894f3117e317b89ab2f4370101a70bbb968a088` | `925505ab2435768a4e712560d474006879a58b4c` | 实现 Mission Manager M1 FakeClock |
| 11 | `585cb07e5a81e57a4b39a80292b7f6909ed91674` | `5c4766f6c61791c78d6cf6f9a6624b37c3766755` | 实现 Mission Manager M1 领域数据模型 |
| 12 | `f90b2689356ea95810a16f5cc58591c8029e7e93` | `0ac4fd1946dbd86fa543fe9fdea701be1ab22498` | 实现 Mission Manager M1 命令校验与幂等处理 |
| 13 | `6a220dedb90c5e27717aa4e321f3961fb9becb82` | `e1b09f933ce9af3f7607bdb9adc1a61c11b992c1` | 实现 Mission Manager M1 Task Catalog Loader |
| 14 | `592491ed5781445d34ff6263637e6d55ea68df5d` | `c7723a4f18780fed8e26ec6f6b20ee088fc2f216` | 实现 Mission Manager M1 FIFO 任务队列 |
| 15 | `e3adc25d78335c4ef61d7cb81031b10de627f93b` | `98bc82b2441a892cd86a439a6d6a66bdd17f2de6` | 实现 Mission Manager M1 Command Record Store |
| 16 | `872e96f5c52b387079212e6fdd6c7cc836498774` | `34f507a419e52949c81eca3e2455467f2990c27c` | 冻结 Mission Manager M1 总体执行方案 |
| 17 | `0830c301084df41fd2e39501c6d52e10c5465892` | `1b487dddea9ab75d04b76b986ecfcfc46a61bf71` | 实现 Mission Manager M1 Execution Record Store |

## 7. 关键历史节点

### M0 最终冻结

原 monorepo：

`f8c10109ab04dd1ddaa57241896d22daa4c5e454`

独立仓库：

`a2d7baca48debc74bc4982fbbb4c83a33bd64aae`

### M1-2g Execution Record Store

原 monorepo：

`0830c301084df41fd2e39501c6d52e10c5465892`

独立仓库：

`1b487dddea9ab75d04b76b986ecfcfc46a61bf71`

### 独立仓库适配

`5f76fb749041fd288e15d1fde17194d4f7593d3f`

该 commit 是拆仓完成后的第一笔新开发历史。

## 8. 作者身份

过滤得到的历史 commit 保留原作者：

`d1zzy <d1zzy@localhost>`

没有为了 GitHub 展示而重写历史作者信息。

独立仓库建立后的新 commit 使用：

`d1zzy <1339980053@qq.com>`

因此可以明确区分 monorepo 历史和独立仓库后续开发历史。

## 9. Task Catalog 独立仓库适配

拆仓后发现旧测试曾通过固定相邻源码目录定位：

`cleannav_interfaces/config/task_catalog.yaml`

这种方式依赖旧 monorepo 的目录结构。

独立仓库适配后改为：

ROS 2 `ament_index` → `cleannav_interfaces` package share → `config/task_catalog.yaml`

因此不再依赖两个 Git 仓库在文件系统中的固定相邻位置。

领域层 `task_catalog.py` 保持纯净，没有为了拆仓加入 ROS package 路径解析逻辑。

## 10. 独立构建验证

最终采用仓库外 build / install / log 目录进行验证。

验证结果：

- colcon build：PASS
- colcon test：PASS
- test-result：213 tests
- errors：0
- failures：0
- skipped：1
- direct unit：210 passed
- flake8：PASS
- pep257：PASS
- git diff check：PASS

第一次在 package 根目录直接构建时，flake8 扫描了 colcon 自动生成的 `build/.../prefix_override/sitecustomize.py`，产生一个 E501 假失败。

该问题不是 Mission Manager 源码错误。

改为仓库外构建后全部验证通过，因此没有修改业务逻辑或放宽 lint 规则。

## 11. 原 Monorepo 最终状态

独立仓库建立完成后，原 monorepo 仍保持：

branch：

`feature/mission-manager-m1`

HEAD：

`0830c301084df41fd2e39501c6d52e10c5465892`

原仓库没有发生历史重写。

## 12. 回退原则

需要查看拆仓前完整 CleanNav 系统历史时，应使用：

- 原 CleanNav monorepo
- 拆仓前安全 bundle

需要回退 Mission Manager 模块时，应优先使用本独立仓库自己的：

- commit
- branch
- tag

不应通过手工复制旧 monorepo 文件覆盖独立仓库来实现版本回退。

后续系统级版本组合最终由 `cleannav-system` 固定各组件的精确 commit。
