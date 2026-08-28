# 参与贡献

感谢参与 Copala。项目处于 Alpha 阶段，优先接受可复现缺陷、兼容性修复、测试、文档和边界清晰的 Agent 能力改进。

## 开始前

1. 搜索已有 Issue，较大变更先创建 Feature Request 说明动机、兼容影响和验证方案。
2. 遵守 [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) 和 [SECURITY.md](SECURITY.md)。安全问题不要提交公开 Issue。
3. Fork 仓库并从最新默认分支创建单一主题分支。

## 开发环境

```bash
python -m venv .venv
python -m pip install -e ".[dev]"
ruff check src tests evaluation
pytest -q
pytest -q --cov=copala --cov-branch
python -m build
```

修改路由还应运行 `python -m evaluation.run --suite routing --output evaluation/results/routing_check.json`。不要使用留出集调优，也不要为提高分数修改冻结答案。

## 兼容与风格

- 保持 `copala` 导入、模块入口、CLI 命令/参数和配置字段兼容；必要的不兼容变更需先讨论。
- 使用 Ruff 约束的 Python 风格，公开行为需有测试。
- 文档和示例只使用占位凭证，不提交真实用户数据、数据库、日志或 Token。
- 更新 `CHANGELOG.md` 的 `Unreleased`，同时维护受影响的中英文 README 或专题文档。

## Pull Request

PR 应说明问题、实现边界、测试结果、兼容风险和文档变化。保持提交可审查，避免夹带格式化或无关文件。
