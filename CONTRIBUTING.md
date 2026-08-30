# 贡献 CleanNav Mission Manager

- `main` 只保留稳定、通过 Gate 的代码；不要直接在 `main` 上开发。
- 功能使用 `feature/*`，bugfix 使用 `fix/*`；一个 PR 只处理一个明确 scope。
- 提交 PR 前执行相应的 build/test，合并前必须完成 review。
- 禁止对 `main` force push；禁止改写、reset 或 rebase 已共享的公共历史。
- 不要提交 `build/`、`install/`、`log/`、token、密码、私钥、数据库或 rosbag。
- 不同成员避免直接共同编辑同一 feature branch。
- Interfaces / Mission Manager / Navigation / Perception / HMI 的跨仓库接口变更必须显式协调；对 `cleannav_interfaces` 合同的依赖变更必须说明兼容性影响。
