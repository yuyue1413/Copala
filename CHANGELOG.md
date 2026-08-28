# 更新日志

本项目按 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 记录重要变化。版本号保持 `0.1.4.post4`，当前重构暂记在 `Unreleased`。

## [Unreleased]

### Added

- 新增集中式 Agent 路由和输出安全模块，同时保留 `AgentLoop` 兼容包装。
- 新增中英文 GitHub 首页、专题文档、评测说明和社区协作文件。
- 新增 Linux/Windows CI、CodeQL、依赖审查和 Dependabot 配置。

### Changed

- Python 包迁移到现代 `src/copala/` 布局；示例、脚本、资源和展示文档归入独立目录。
- 构建元数据、pytest 路径与分支覆盖率门槛适配新布局。

### Fixed

- 修复相对日期边界、生活事件时间冲突和 Matrix `mxc://` 图片净化兼容问题。

## [0.1.4.post4]

- 基于 nanobot 的 Copala 初始版本。
