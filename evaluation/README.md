# Copala 离线评测

本目录包含冻结数据、评测入口和原始结果。评测不会访问真实模型、飞书、高德或搜索服务。

```bash
python -m evaluation.run --suite all --seed 20260520 --repeats 2 --output evaluation/results/agent_eval.json
```

可用套件和参数通过 `python -m evaluation.run --help` 查看。不要为了提高留出集分数修改 `data/` 答案；变更数据时必须更新版本、哈希与报告口径。详细说明见 [`docs/evaluation.md`](../docs/evaluation.md)。
