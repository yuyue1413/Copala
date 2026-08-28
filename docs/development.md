# 开发指南

## 目录约定

- `src/copala/`：可发布源码和包内资源
- `tests/`：单元、集成边界与兼容性测试
- `evaluation/`：冻结数据和离线评测
- `examples/`：只允许脱敏示例
- `scripts/`：维护脚本
- `docs/`：稳定文档、展示材料和历史归档

源码运行以可编辑安装为标准；pytest 的 `pythonpath = ["src"]` 只用于简化本地测试。

## 本地质量门槛

```bash
python -m pip install -e ".[dev]"
ruff check src tests evaluation
pytest -q
pytest -q --cov=copala --cov-branch
python -m build
```

综合覆盖率不得低于 45%。修改路由时还需运行：

```bash
python -m evaluation.run --suite routing --output evaluation/results/routing_check.json
```

留出集结果、快路径零调用数量和 decision hash 必须保持稳定，除非变更经过明确评审并更新冻结数据版本。

## 兼容性

以下接口视为稳定兼容面：`import copala`、`from copala.agent import AgentLoop`、`python -m copala`、`copala.cli.commands:app`、既有 CLI 命令/参数、配置 JSON 字段及默认工作区。

## 提交前检查

- 不提交 `.env`、本地配置、OAuth Token、邮箱授权码、数据库或用户数据。
- 新增示例全部使用 `YOUR_*` 等占位值。
- 更新 `CHANGELOG.md` 的 `Unreleased`。
- 文档相对链接可解析，中英文首页的结构保持一致。
- 不生成或提交 `uv.lock`/`poetry.lock`，除非项目策略另行变更。

协作流程见仓库根目录的 [`CONTRIBUTING.md`](../CONTRIBUTING.md)。
