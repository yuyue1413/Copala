# 评测复现

评测直接运行 Copala 的检索、路由、事件、规划、提醒和技能代码，不需要模型、飞书、高德或搜索凭证。冻结数据位于 `evaluation/data/`，原始结果位于 `evaluation/results/`。

## 数据与边界

- 总数据集哈希：`c7bfab38d7d07c20d6537298e6da5e2dc2988eb5c9841a9e770a17e1fc672178`
- 报告时点：Commit `f4ecb42`
- 留出集不参与规则调优；简历仅引用留出集和故障注入结果。
- 结果不代表生产准确率，不测在线 Token，不测真实飞书/高德或搜索延迟。

## 运行

```bash
python -m pip install -e ".[dev]"
python -m evaluation.run --suite all --seed 20260520 --repeats 2 --output evaluation/results/agent_eval.json
```

入口支持 `--suite`、`--seed`、`--repeats` 和 `--output`。性能套件会预热并运行三轮，JSON 保存指标分子/分母、单位、哈希、环境和每轮数据。

只运行路由回归：

```bash
python -m evaluation.run --suite routing --output evaluation/results/routing_check.json
```

预期留出集 Accuracy 和 Macro-F1 均为 1.0，40/40 个快路径请求零模型调用；基线 decision hash 为 `c969f463da08d53162a3f6582dad1d346b9508947d91037e3e4edff0443167d3`。

## 已记录结果

完整报告见 [`evaluation/results/agent_eval.md`](../evaluation/results/agent_eval.md)，机器可读数据见 [`agent_eval.json`](../evaluation/results/agent_eval.json)。核心增量模块覆盖率保存在 [`coverage.json`](../evaluation/results/coverage.json)，重构后全包覆盖率明细保存在 [`coverage-current.json`](../evaluation/results/coverage-current.json)，JUnit 结果保存在 [`pytest.xml`](../evaluation/results/pytest.xml)。README 与简历中的所有数字必须能回溯到这些结果。

## 质量检查

```bash
ruff check src tests evaluation
pytest -q
pytest -q --cov=copala --cov-branch
python -m build
```

全包综合覆盖率门槛为 45%，不是历史报告中“新增核心模块覆盖率 79.02%/61.22%”的同一统计口径。
