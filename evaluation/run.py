"""Credential-free Copala evaluation runner.

Run from the repository root:
    python -m evaluation.run --suite all --repeats 3 \
        --output evaluation/results/agent_eval.json
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from loguru import logger

# Keep the evaluator genuinely offline even when importing LiteLLM-backed modules.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "true")

from copala.agent.loop import AgentLoop
from copala.agent.memory_rag.schema import MemoryItem
from copala.agent.memory_rag.store import MemoryRAGStore
from copala.agent.skills import SkillsLoader
from copala.agent.tools.learned import LearnSkillTool
from copala.bus.events import InboundMessage
from copala.bus.queue import MessageBus
from copala.calendar.service import CalendarEvent
from copala.life_events.service import LifeEventExtractor, LifeEventManager, ProcessResult
from copala.planning.outing import OutingPlanningService
from copala.providers.base import LLMProvider, LLMResponse
from copala.smart_reminders.service import SmartReminderService
from evaluation.datasets import all_datasets

ROOT = Path(__file__).resolve().parents[1]
FAST_LABELS = {"calendar_query", "daily_plan", "outing_plan", "smart_reminder"}


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _round(value: float) -> float:
    return round(float(value), 6)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def freeze_datasets(base: Path, datasets: dict[str, Any]) -> dict[str, str]:
    base.mkdir(parents=True, exist_ok=True)
    hashes: dict[str, str] = {}
    for name, payload in datasets.items():
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        dataset_path = base / f"{name}.json"
        if dataset_path.exists() and dataset_path.read_text(encoding="utf-8") != text:
            raise RuntimeError(
                f"Frozen dataset changed: {dataset_path}. "
                "Review the labels and replace the file explicitly before rerunning."
            )
        if not dataset_path.exists():
            dataset_path.write_text(text, encoding="utf-8")
        hashes[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return hashes


def _quality_at_five(ranking: list[str], relevant: set[str]) -> tuple[float, float, float]:
    hits = [index for index, item_id in enumerate(ranking[:5], 1) if item_id in relevant]
    if not hits:
        return 0.0, 0.0, 0.0
    recall = len(hits) / len(relevant)
    reciprocal_rank = 1.0 / hits[0]
    dcg = sum(1.0 / math.log2(rank + 1) for rank in hits)
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(5, len(relevant)) + 1))
    return recall, reciprocal_rank, dcg / ideal


def _retrieval_scores(store: MemoryRAGStore, queries: list[dict[str, Any]]) -> dict[str, Any]:
    totals = {name: [0.0, 0.0, 0.0] for name in ("bm25", "dense", "rrf")}
    rankings: dict[str, dict[str, list[str]]] = {}
    for query in queries:
        text = query["query"]
        current = {
            "bm25": store._bm25(text, 5),
            "dense": store._dense(text, 5),
            "rrf": [item.item.id for item in store.retrieve(text, top_k=5, bm25_k=20, dense_k=20)],
        }
        rankings[query["id"]] = current
        relevant = set(query["relevant_ids"])
        for name, ranking in current.items():
            score = _quality_at_five(ranking, relevant)
            totals[name] = [totals[name][i] + score[i] for i in range(3)]
    result: dict[str, Any] = {}
    count = len(queries)
    for name, values in totals.items():
        result[name] = {
            "recall_at_5": _round(values[0] / count),
            "mrr_at_5": _round(values[1] / count),
            "ndcg_at_5": _round(values[2] / count),
            "recall_at_5_sum": _round(values[0]),
            "mrr_at_5_sum": _round(values[1]),
            "ndcg_at_5_sum": _round(values[2]),
            "query_count": count,
            "unit": "ratio",
        }
    result["ranking_hash"] = _hash(rankings)
    return result


def evaluate_memory(dataset: dict[str, Any], repeats: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="copala-memory-quality-") as raw:
        store = MemoryRAGStore(Path(raw))
        items = [MemoryItem(**item, confidence=0.9, priority=5) for item in dataset["memories"]]
        started = time.perf_counter()
        store.upsert_many(items)
        quality_index_seconds = time.perf_counter() - started
        quality = {
            split: _retrieval_scores(store, [q for q in dataset["queries"] if q["split"] == split])
            for split in ("dev", "test")
        }
        repeated = {
            split: _retrieval_scores(store, [q for q in dataset["queries"] if q["split"] == split])
            for split in ("dev", "test")
        }
        deterministic = quality == repeated

    performance: dict[str, Any] = {}
    performance_queries = [q["query"] for q in dataset["queries"][:20]]
    for size in (1_000, 10_000):
        with tempfile.TemporaryDirectory(prefix=f"copala-memory-{size}-") as raw:
            store = MemoryRAGStore(Path(raw))
            scale_items = [
                MemoryItem(
                    id=f"scale-{i:05d}",
                    type="benchmark",
                    text=(
                        f"编号SCALE-{i:05d}的用户记忆：偏好城市{['杭州', '苏州', '上海', '南京'][i % 4]}，"
                        f"任务类别{['旅行', '邮件', '日历', '购物'][i % 4]}，预算{i % 97 * 100 + 500}元。"
                    ),
                    summary=f"SCALE-{i:05d}场景记忆",
                    source="evaluation",
                    tags=["scale", str(i % 10)],
                    confidence=0.9,
                    priority=5,
                )
                for i in range(size)
            ]
            started = time.perf_counter()
            store.upsert_many(scale_items)
            index_seconds = time.perf_counter() - started
            for query in performance_queries[:5]:
                store.retrieve(query, top_k=5)
            rounds: list[dict[str, float]] = []
            all_latencies: list[float] = []
            for _ in range(repeats):
                latencies: list[float] = []
                for query in performance_queries:
                    tick = time.perf_counter()
                    store.retrieve(query, top_k=5)
                    latencies.append((time.perf_counter() - tick) * 1_000)
                all_latencies.extend(latencies)
                rounds.append({
                    "p50_ms": _round(statistics.median(latencies)),
                    "p95_ms": _round(_percentile(latencies, 0.95)),
                })
            round_p50s = [item["p50_ms"] for item in rounds]
            round_p95s = [item["p95_ms"] for item in rounds]
            performance[str(size)] = {
                "items": size,
                "queries_per_round": len(performance_queries),
                "repeats": repeats,
                "index_seconds": _round(index_seconds),
                "items_indexed_per_second": _round(size / index_seconds),
                "latency_unit": "ms",
                "index_time_unit": "s",
                "p50_ms": _round(statistics.median(round_p50s)),
                "p95_ms": _round(statistics.median(round_p95s)),
                "p50_range_ms": [_round(min(round_p50s)), _round(max(round_p50s))],
                "p95_range_ms": [_round(min(round_p95s)), _round(max(round_p95s))],
                "aggregate_p50_ms": _round(statistics.median(all_latencies)),
                "aggregate_p95_ms": _round(_percentile(all_latencies, 0.95)),
                "rounds": rounds,
            }

    test_metrics = quality["test"]
    best_single = max(test_metrics["bm25"]["mrr_at_5"], test_metrics["dense"]["mrr_at_5"])
    test_metrics["rrf_mrr_change_vs_best_single"] = _round(test_metrics["rrf"]["mrr_at_5"] - best_single)
    return {
        "dataset": {"memory_count": len(dataset["memories"]), "query_count": len(dataset["queries"])},
        "index_seconds_200_items": _round(quality_index_seconds),
        "quality": quality,
        "performance": performance,
        "deterministic": deterministic,
    }


def classify_route(text: str) -> str:
    if AgentLoop._looks_like_readonly_calendar_query(text) or AgentLoop._looks_like_calendar_status_query(text):
        return "calendar_query"
    if AgentLoop._looks_like_outing_planning_query(text):
        return "outing_plan"
    if AgentLoop._looks_like_smart_reminder_query(text):
        return "smart_reminder"
    if AgentLoop._looks_like_daily_planning_query(text):
        return "daily_plan"
    if LifeEventExtractor().extract(text, source="evaluation", source_ref="evaluation"):
        return "life_event"
    return "llm_slow"


def _classification_metrics(records: list[dict[str, Any]], predictions: list[str]) -> dict[str, Any]:
    labels = sorted({record["label"] for record in records})
    correct = sum(record["label"] == predicted for record, predicted in zip(records, predictions, strict=True))
    per_class: dict[str, Any] = {}
    f1_values: list[float] = []
    for label in labels:
        tp = sum(r["label"] == label and p == label for r, p in zip(records, predictions, strict=True))
        fp = sum(r["label"] != label and p == label for r, p in zip(records, predictions, strict=True))
        fn = sum(r["label"] == label and p != label for r, p in zip(records, predictions, strict=True))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        f1_values.append(f1)
        per_class[label] = {"precision": _round(precision), "recall": _round(recall), "f1": _round(f1), "support": tp + fn}
    general = [(r, p) for r, p in zip(records, predictions, strict=True) if r["label"] == "llm_slow"]
    false_fast = sum(predicted in FAST_LABELS for _, predicted in general)
    fast = [(r, p) for r, p in zip(records, predictions, strict=True) if r["label"] in FAST_LABELS]
    fast_correct = sum(r["label"] == p for r, p in fast)
    return {
        "accuracy": _round(correct / len(records)),
        "macro_f1": _round(statistics.mean(f1_values)),
        "correct": correct,
        "total": len(records),
        "fast_path_recall": _round(fast_correct / len(fast)),
        "general_false_intercept_rate": _round(false_fast / len(general)) if general else 0.0,
        "per_class": per_class,
    }


class CountingProvider(LLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7, reasoning_effort=None):
        self.calls += 1
        return LLMResponse(content="离线评测模型回复")

    def get_default_model(self) -> str:
        return "offline-counting-provider"


class _CalendarStub:
    def auth_status(self) -> dict[str, Any]:
        return {"enterprise_enabled": False, "providers": {}}

    async def list_events_async(self, **kwargs) -> list[Any]:
        return []


class _PlanStub:
    async def plan_from_request(self, request: str) -> str:
        return "离线出行规划"

    async def plan_day(self, request: str, mode: str = "plan_day") -> str:
        return "离线每日规划"


class _ReminderStub:
    async def check_risks(self, days: int = 2) -> list[Any]:
        return []

    def format_risks(self, risks: list[Any]) -> str:
        return "离线风险检查"


class _LifeStub:
    async def process_text(self, text: str, **kwargs) -> ProcessResult:
        events = LifeEventExtractor().extract(text, source="evaluation", source_ref="evaluation")
        return ProcessResult(events=events, messages=["离线生活事件处理"] if events else [])


async def _provider_bypass(records: list[dict[str, Any]]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="copala-routing-") as raw:
        provider = CountingProvider()
        agent = AgentLoop(MessageBus(), provider, Path(raw), memory_window=1_000)
        agent.calendar_service = _CalendarStub()
        agent.outing_planning = _PlanStub()
        agent.daily_planning = _PlanStub()
        agent.smart_reminders = _ReminderStub()
        agent.life_events = _LifeStub()
        decisions: list[dict[str, Any]] = []
        for index, record in enumerate(records):
            before = provider.calls
            await agent._process_message(InboundMessage(
                channel="qq", sender_id="evaluation", chat_id=f"case-{index}", content=record["text"]
            ))
            used_provider = provider.calls > before
            expected_provider = record["label"] == "llm_slow"
            decisions.append({"id": record["id"], "used_provider": used_provider, "expected_provider": expected_provider})
        correct = sum(row["used_provider"] == row["expected_provider"] for row in decisions)
        fast_domain = [row for row, record in zip(decisions, records, strict=True) if record["label"] in FAST_LABELS]
        avoided = sum(not row["used_provider"] for row in fast_domain)
        return {
            "decision_accuracy": _round(correct / len(decisions)),
            "correct": correct,
            "total": len(decisions),
            "fast_path_provider_calls_avoided": avoided,
            "fast_path_total": len(fast_domain),
            "fast_path_avoidance_rate": _round(avoided / len(fast_domain)),
            "decision_hash": _hash(decisions),
        }


def evaluate_routing(dataset: list[dict[str, Any]]) -> dict[str, Any]:
    results: dict[str, Any] = {"dataset_count": len(dataset)}
    first_predictions = [classify_route(row["text"]) for row in dataset]
    second_predictions = [classify_route(row["text"]) for row in dataset]
    for split in ("dev", "test"):
        rows = [row for row in dataset if row["split"] == split]
        predictions = [classify_route(row["text"]) for row in rows]
        results[split] = _classification_metrics(rows, predictions)
    test_rows = [row for row in dataset if row["split"] == "test"]
    results["provider_bypass_test"] = asyncio.run(_provider_bypass(test_rows))
    results["deterministic"] = first_predictions == second_predictions
    return results


def _binary_metrics(truth: list[bool], predicted: list[bool]) -> dict[str, Any]:
    tp = sum(t and p for t, p in zip(truth, predicted, strict=True))
    fp = sum(not t and p for t, p in zip(truth, predicted, strict=True))
    fn = sum(t and not p for t, p in zip(truth, predicted, strict=True))
    tn = sum(not t and not p for t, p in zip(truth, predicted, strict=True))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "precision": _round(precision), "recall": _round(recall), "f1": _round(f1),
        "tp": tp, "fp": fp, "fn": fn, "tn": tn, "total": len(truth),
    }


class _EventProvider:
    default_calendar_id = "primary"

    def __init__(self) -> None:
        self.created = 0

    async def create_event(self, calendar_id, draft):
        self.created += 1
        return CalendarEvent(
            id=f"event-{self.created}", title=draft.title, start=draft.start, end=draft.end,
            location=draft.location, source="feishu", platform="feishu", calendar_id=calendar_id,
            event_id=f"event-{self.created}",
        )


class _EventCalendar:
    def __init__(self) -> None:
        self.provider = _EventProvider()

    def _provider(self, platform: str) -> _EventProvider:
        return self.provider

    async def list_events_async(self, **kwargs) -> list[Any]:
        return []


async def _dedupe_rate(records: list[dict[str, Any]]) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="copala-life-dedupe-") as raw:
        calendar = _EventCalendar()
        manager = LifeEventManager(
            workspace=Path(raw),
            config=SimpleNamespace(
                enabled=True, min_calendar_confidence=0.82, auto_create_todo=True,
                auto_create_reminder=False, notify_channel="qq", notify_chat_id="evaluation",
            ),
            calendar_service=calendar,
        )
        suppressed = 0
        pairs = records[:20]
        for row in pairs:
            first = await manager.process_text(row["text"], source="evaluation", source_ref=row["id"], send_notifications=False)
            second = await manager.process_text(row["text"], source="evaluation", source_ref=row["id"], send_notifications=False)
            if first.created == 1 and second.skipped == 1 and second.created == 0:
                suppressed += 1
        result = {"suppressed": suppressed, "pairs": len(pairs), "suppression_rate": _round(suppressed / len(pairs))}
        del manager
        gc.collect()
        return result


def _life_metrics(records: list[dict[str, Any]]) -> dict[str, Any]:
    extractor = LifeEventExtractor()
    predicted_events = [extractor.extract(row["text"], source="evaluation", source_ref=row["id"]) for row in records]
    binary = _binary_metrics([row["is_event"] for row in records], [bool(events) for events in predicted_events])
    true_positives = [(row, events[0]) for row, events in zip(records, predicted_events, strict=True) if row["is_event"] and events]
    type_correct = sum(row["event_type"] == event.event_type for row, event in true_positives)
    time_cases = [(row, event) for row, event in true_positives if row["start"]]
    time_correct = sum(event.start and event.start.isoformat() == row["start"] for row, event in time_cases)
    location_cases = [(row, event) for row, event in true_positives if row["location"]]
    location_correct = sum(event.location == row["location"] for row, event in location_cases)
    binary.update({
        "event_type_accuracy": _round(type_correct / len(true_positives)) if true_positives else 0.0,
        "time_accuracy": _round(time_correct / len(time_cases)) if time_cases else 0.0,
        "location_accuracy": _round(location_correct / len(location_cases)) if location_cases else 0.0,
    })
    return binary


def evaluate_life_events(dataset: list[dict[str, Any]]) -> dict[str, Any]:
    result = {split: _life_metrics([row for row in dataset if row["split"] == split]) for split in ("dev", "test")}
    first = [bool(LifeEventExtractor().extract(row["text"], source="evaluation", source_ref=row["id"])) for row in dataset]
    second = [bool(LifeEventExtractor().extract(row["text"], source="evaluation", source_ref=row["id"])) for row in dataset]
    result["dedupe_test"] = asyncio.run(_dedupe_rate([row for row in dataset if row["is_event"]]))
    result["dataset_count"] = len(dataset)
    result["deterministic"] = first == second
    return result


class _MemoryStub:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.saved = 0

    def retrieve(self, query: str, top_k: int = 6) -> list[Any]:
        if self.fail:
            raise RuntimeError("memory unavailable")
        return [SimpleNamespace(item=SimpleNamespace(text="用户偏好轻松行程、少排队，并预留交通缓冲。"))]

    def upsert(self, item: Any) -> None:
        if self.fail:
            raise RuntimeError("memory unavailable")
        self.saved += 1


class _PlanningCalendar:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def list_events_async(self, **kwargs) -> list[CalendarEvent]:
        if self.fail:
            raise RuntimeError("calendar unavailable")
        start = datetime.combine(kwargs["start_date"], datetime.min.time()).replace(hour=14)
        return [CalendarEvent(id="plan-event", title="项目会议", start=start, end=start + timedelta(hours=1), location="飞书会议")]


class _AmapStub:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def geocode(self, address: str, city: str = "") -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("amap unavailable")
        suffix = sum(ord(char) for char in address) % 10_000
        return {"location": f"120.{suffix:04d},30.{suffix:04d}", "city": city or "杭州", "adcode": "330100"}

    async def driving(self, origin: str, destination: str) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("amap unavailable")
        return {"mode": "driving", "duration_seconds": 2_400, "distance_meters": 18_000}

    async def transit(self, origin: str, destination: str, city: str) -> dict[str, Any]:
        return await self.driving(origin, destination)

    async def weather(self, city: str, extensions: str = "base") -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("amap unavailable")
        return {"city": "杭州", "weather": "小雨", "temperature": "22"}


class _WebStub:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def execute(self, **kwargs) -> str:
        return "Error: web unavailable" if self.fail else "实时攻略：建议预约核心景点并避开周末高峰。"


async def _planning_run(dataset: list[dict[str, str]]) -> list[dict[str, Any]]:
    modes = ("normal", "calendar_failure", "amap_failure", "web_failure", "multi_failure")
    required = ("日程校验：", "路线与天气：", "个性化依据：", "外部信息检索：", "预算估算：", "风险与备用方案：")
    rows: list[dict[str, Any]] = []
    for case in dataset:
        for mode in modes:
            memory = _MemoryStub(fail=mode == "multi_failure")
            service = OutingPlanningService(
                config=SimpleNamespace(enabled=True, default_search_count=3, guide_sites=[], save_plan_to_memory=True),
                calendar_service=_PlanningCalendar(fail=mode in {"calendar_failure", "multi_failure"}),
                memory_store=memory,
                amap_config=SimpleNamespace(api_key="offline", api_base="offline", timeout_seconds=1, default_origin="滨江", default_city="杭州", route_mode="driving"),
            )
            service.amap = _AmapStub(fail=mode in {"amap_failure", "multi_failure"})
            service.web = _WebStub(fail=mode in {"web_failure", "multi_failure"})
            method = service.plan_trip if case["kind"] == "trip" else service.plan_outing
            try:
                text = await method(
                    destination=case["destination"], time_range=case["time_range"], origin="滨江",
                    budget=case["budget"], companions=case["companions"], preferences=case["preferences"],
                )
                sections = sum(marker in text for marker in required)
                success = bool(text.strip()) and sections == len(required)
            except Exception as exc:  # pragma: no cover - recorded as an evaluation failure
                text, sections, success = str(exc), 0, False
            rows.append({"case": case["id"], "mode": mode, "success": success, "sections": sections, "section_total": len(required), "output_hash": hashlib.sha256(text.encode()).hexdigest()})
    return rows


def evaluate_planning(dataset: list[dict[str, str]]) -> dict[str, Any]:
    first = asyncio.run(_planning_run(dataset))
    second = asyncio.run(_planning_run(dataset))
    success = sum(row["success"] for row in first)
    degraded = [row for row in first if row["mode"] != "normal"]
    coverage = sum(row["sections"] for row in first) / sum(row["section_total"] for row in first)
    return {
        "scenario_count": len(first),
        "success_count": success,
        "success_rate": _round(success / len(first)),
        "required_section_coverage": _round(coverage),
        "degraded_scenario_count": len(degraded),
        "degraded_success_rate": _round(sum(row["success"] for row in degraded) / len(degraded)),
        "deterministic": first == second,
    }


class _RiskLifeEvents:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def list_events(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.rows


class _RiskCalendar:
    def __init__(self, events: list[CalendarEvent]) -> None:
        self.events = events

    async def list_events_async(self, **kwargs) -> list[CalendarEvent]:
        return self.events


async def _risk_predictions() -> tuple[list[bool], list[bool], bool]:
    truth: list[bool] = []
    predicted: list[bool] = []
    now = datetime.now().replace(microsecond=0)
    with tempfile.TemporaryDirectory(prefix="copala-risk-") as raw:
        for group in ("weather", "traffic", "deadline"):
            for i in range(10):
                positive = i < 5
                life_rows: list[dict[str, Any]] = []
                events: list[CalendarEvent] = []
                amap = _AmapStub()
                if group == "weather":
                    life_rows = [{"id": f"w-{i}", "type": "personal_activity", "title": "户外活动", "start": (now + timedelta(hours=6)).isoformat(), "location": "西湖公园", "source_ref": f"w-{i}", "weather_sensitive": positive, "outdoor": positive}]
                    if not positive:
                        amap.weather = lambda *args, **kwargs: asyncio.sleep(0, result={"city": "杭州", "weather": "晴", "temperature": "25"})
                elif group == "traffic":
                    first = CalendarEvent(id="a", title="会议A", start=now + timedelta(hours=2), end=now + timedelta(hours=3), location="滨江A座")
                    gap = 10 if positive else 120
                    second = CalendarEvent(id="b", title="会议B", start=first.end + timedelta(minutes=gap), end=first.end + timedelta(minutes=gap + 60), location="西湖B座")
                    events = [first, second]
                else:
                    deadline = now + (timedelta(hours=12) if positive else timedelta(days=7))
                    life_rows = [{"id": f"b-{i}", "type": "bill", "title": "信用卡账单", "start": deadline.isoformat(), "deadline": deadline.isoformat(), "source_ref": f"b-{i}"}]
                service = SmartReminderService(
                    workspace=Path(raw) / f"{group}-{i}",
                    config=SimpleNamespace(
                        enabled=True, min_risk_confidence=0.75, lookahead_hours=240,
                        notify_channel="qq", notify_chat_id="evaluation",
                        amap=SimpleNamespace(enabled=True, api_key="offline", api_base="offline", timeout_seconds=1, default_city="杭州", route_mode="driving", default_travel_buffer_minutes=15, outdoor_weather_keywords=[]),
                    ),
                    calendar_service=_RiskCalendar(events), life_events=_RiskLifeEvents(life_rows),
                )
                service.amap = amap
                risks = await service.check_risks(days=10)
                truth.append(positive)
                predicted.append(any(risk.risk_type == group for risk in risks))

        sent: list[Any] = []
        service = SmartReminderService(
            workspace=Path(raw) / "dedupe",
            config=SimpleNamespace(
                enabled=True, min_risk_confidence=0.75, lookahead_hours=48,
                notify_channel="qq", notify_chat_id="evaluation",
                amap=SimpleNamespace(enabled=True, api_key="offline", api_base="offline", timeout_seconds=1, default_city="杭州", route_mode="driving", default_travel_buffer_minutes=15, outdoor_weather_keywords=[]),
            ),
            calendar_service=_RiskCalendar([]),
            life_events=_RiskLifeEvents([{"id": "dedupe", "type": "bill", "title": "待还账单", "start": (now + timedelta(hours=12)).isoformat(), "deadline": (now + timedelta(hours=12)).isoformat(), "source_ref": "dedupe"}]),
            send_callback=lambda msg: _capture(sent, msg),
        )
        await service.patrol()
        first_count = len(sent)
        await service.patrol()
        repeat_suppressed = first_count == 1 and len(sent) == 1
        del service
        gc.collect()
    return truth, predicted, repeat_suppressed


async def _capture(target: list[Any], value: Any) -> None:
    target.append(value)


def evaluate_risks() -> dict[str, Any]:
    first_truth, first_predicted, first_dedupe = asyncio.run(_risk_predictions())
    second_truth, second_predicted, second_dedupe = asyncio.run(_risk_predictions())
    result = _binary_metrics(first_truth, first_predicted)
    result["repeat_notification_suppressed"] = first_dedupe
    result["deterministic"] = first_truth == second_truth and first_predicted == second_predicted and first_dedupe == second_dedupe
    return result


async def _skill_run() -> tuple[int, int, list[dict[str, Any]]]:
    details: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="copala-skills-") as raw:
        workspace = Path(raw)
        tool = LearnSkillTool(workspace)
        for i in range(10):
            result = await tool.execute(
                action="save", skill_name=f"demo-{i}", description=f"复用场景{i}", category="evaluation",
                script_name=f"demo_{i}.py", script_content=f"print({i})", usage="运行脚本并检查输出。",
            )
            discovered = f"learned-demo-{i}" in {row["name"] for row in SkillsLoader(workspace).list_skills(False)}
            script_path = workspace / "learned" / "scripts" / "evaluation" / f"demo_{i}.py"
            executed = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            details.append({
                "case": f"save-discover-reuse-{i}",
                "passed": (
                    result.startswith("Saved learned skill")
                    and discovered
                    and executed.returncode == 0
                    and executed.stdout.strip() == str(i)
                ),
            })
        for i in range(5):
            result = await tool.execute(action="save", skill_name=f"demo-{i}", description="重复", category="evaluation", script_name=f"demo_{i}.py", script_content="print('duplicate')")
            details.append({"case": f"overwrite-block-{i}", "passed": result.startswith("Error:")})
        for i in range(5):
            result = await tool.execute(action="save", skill_name=f"demo-{i}", description="覆盖", category="evaluation", script_name=f"demo_{i}.py", script_content="print('updated')", overwrite=True)
            details.append({"case": f"overwrite-allow-{i}", "passed": result.startswith("Saved learned skill")})
        for i in range(5):
            result = await tool.execute(action="save", skill_name=f"secret-{i}", description="敏感检查", category="evaluation", script_name=f"secret_{i}.py", script_content=f"API_KEY = 'secret-{i}'")
            details.append({"case": f"secret-warning-{i}", "passed": "credential-like" in result})
        for i in range(5):
            result = await tool.execute(action="save", skill_name=f"../../escape-{i}", description="路径检查", category="../outside", script_name=f"../../escape_{i}.py", script_content="print('safe')")
            escaped = any(path.is_file() for path in workspace.parent.glob(f"escape_{i}.py"))
            details.append({"case": f"path-containment-{i}", "passed": result.startswith("Saved learned skill") and not escaped})
    return sum(row["passed"] for row in details), len(details), details


def evaluate_skills() -> dict[str, Any]:
    passed, total, details = asyncio.run(_skill_run())
    passed_again, total_again, details_again = asyncio.run(_skill_run())
    return {
        "passed": passed, "total": total, "pass_rate": _round(passed / total),
        "deterministic": (passed, total, details) == (passed_again, total_again, details_again),
        "cases": details,
    }


def evaluate_engineering(results_dir: Path) -> dict[str, Any]:
    results_dir.mkdir(parents=True, exist_ok=True)
    junit_path = results_dir / "pytest.xml"
    coverage_path = results_dir / "coverage.json"
    command = [
        sys.executable, "-m", "pytest", "-q", f"--junitxml={junit_path}",
        "--cov=copala.agent.memory_rag", "--cov=copala.life_events", "--cov=copala.planning",
        "--cov=copala.smart_reminders", "--cov=copala.agent.tools.learned", "--cov-branch",
        f"--cov-report=json:{coverage_path}", "--cov-report=term",
    ]
    completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    suites = None
    if junit_path.exists():
        junit_tree = ET.parse(junit_path)
        suites = junit_tree.getroot()
        for suite in suites.iter("testsuite"):
            if "hostname" in suite.attrib:
                suite.set("hostname", "local-machine")
        junit_tree.write(junit_path, encoding="utf-8", xml_declaration=True)
    if suites is not None and suites.tag == "testsuites":
        tests = sum(int(node.attrib.get("tests", 0)) for node in suites.findall("testsuite"))
        failures = sum(int(node.attrib.get("failures", 0)) for node in suites.findall("testsuite"))
        errors = sum(int(node.attrib.get("errors", 0)) for node in suites.findall("testsuite"))
        skipped = sum(int(node.attrib.get("skipped", 0)) for node in suites.findall("testsuite"))
    elif suites is not None:
        tests = int(suites.attrib.get("tests", 0))
        failures = int(suites.attrib.get("failures", 0))
        errors = int(suites.attrib.get("errors", 0))
        skipped = int(suites.attrib.get("skipped", 0))
    else:
        tests = failures = errors = skipped = 0
    coverage = json.loads(coverage_path.read_text(encoding="utf-8")) if coverage_path.exists() else {}
    totals = coverage.get("totals", {})
    covered_lines = totals.get("covered_lines", 0)
    num_statements = totals.get("num_statements", 0)
    covered_branches = totals.get("covered_branches", 0)
    num_branches = totals.get("num_branches", 0)
    display_command = ["python", *command[1:]]
    return {
        "command": display_command,
        "exit_code": completed.returncode,
        "tests": tests,
        "passed": tests - failures - errors - skipped,
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
        "coverage_line_percent": _round(100 * covered_lines / num_statements) if num_statements else 0.0,
        "coverage_branch_percent": _round(100 * covered_branches / num_branches) if num_branches else 0.0,
        "coverage_combined_percent": _round(totals.get("percent_covered", 0.0)),
        "covered_lines": covered_lines,
        "num_statements": num_statements,
        "covered_branches": covered_branches,
        "num_branches": num_branches,
        "stdout_tail": "\n".join(completed.stdout.splitlines()[-20:]),
    }


def environment() -> dict[str, Any]:
    def version(name: str) -> str:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            return "not-installed"

    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()
    return {
        "timestamp": datetime.now().astimezone().isoformat(),
        "commit": commit,
        "os": platform.platform(),
        "python": platform.python_version(),
        "processor": platform.processor(),
        "packages": {name: version(name) for name in ("copala-ai", "pytest", "pytest-cov", "matrix-nio", "nh3", "mistune")},
    }


def render_report(result: dict[str, Any]) -> str:
    lines = [
        "# Copala 离线评测报告", "",
        f"- Commit：`{result['environment']['commit']}`",
        f"- Python：`{result['environment']['python']}`",
        f"- 数据集总哈希：`{result['dataset_hash']}`", "",
    ]
    if memory := result.get("memory"):
        test = memory["quality"]["test"]
        lines.extend([
            "## 长期记忆", "",
            f"- 留出集：{test['rrf']['query_count']} 条查询；RRF Recall@5={test['rrf']['recall_at_5']:.3f}，MRR@5={test['rrf']['mrr_at_5']:.3f}，nDCG@5={test['rrf']['ndcg_at_5']:.3f}。",
            f"- RRF MRR 相对最佳单路变化：{test['rrf_mrr_change_vs_best_single']:+.3f}。",
        ])
        for size in ("1000", "10000"):
            perf = memory["performance"][size]
            p50_rounds = "/".join(f"{row['p50_ms']:.2f}" for row in perf["rounds"])
            p95_rounds = "/".join(f"{row['p95_ms']:.2f}" for row in perf["rounds"])
            lines.append(
                f"- {perf['items']:,} 条记忆：三轮 P50={p50_rounds}ms，"
                f"中位数 {perf['p50_ms']:.2f}ms、范围 {perf['p50_range_ms'][0]:.2f}–{perf['p50_range_ms'][1]:.2f}ms；"
                f"三轮 P95={p95_rounds}ms，中位数 {perf['p95_ms']:.2f}ms、"
                f"范围 {perf['p95_range_ms'][0]:.2f}–{perf['p95_range_ms'][1]:.2f}ms；"
                f"索引 {perf['index_seconds']:.2f}s。"
            )
        lines.append("")
    if routing := result.get("routing"):
        test = routing["test"]
        bypass = routing["provider_bypass_test"]
        lines.extend([
            "## Agent 路由", "",
            f"- 留出集：{test['total']} 条；准确率={test['accuracy']:.3f}，Macro-F1={test['macro_f1']:.3f}，通用请求误拦截率={test['general_false_intercept_rate']:.3f}。",
            f"- 快路径零模型调用：{bypass['fast_path_provider_calls_avoided']}/{bypass['fast_path_total']}，规避率={bypass['fast_path_avoidance_rate']:.3f}。", "",
        ])
    if life := result.get("life_events"):
        test = life["test"]
        lines.extend([
            "## 主动生活事件", "",
            f"- 留出集：{test['total']} 条；Precision={test['precision']:.3f}，Recall={test['recall']:.3f}，F1={test['f1']:.3f}。",
            f"- 类型/时间/地点准确率：{test['event_type_accuracy']:.3f}/{test['time_accuracy']:.3f}/{test['location_accuracy']:.3f}。",
            f"- 重复输入抑制：{life['dedupe_test']['suppressed']}/{life['dedupe_test']['pairs']}。", "",
        ])
    if planning := result.get("planning"):
        lines.extend([
            "## 规划与风险", "",
            f"- 多源规划：{planning['success_count']}/{planning['scenario_count']} 成功；降级成功率={planning['degraded_success_rate']:.3f}；必要章节覆盖率={planning['required_section_coverage']:.3f}。",
        ])
        if risks := result.get("risks"):
            lines.extend([
                f"- 风险识别：F1={risks['f1']:.3f}；重复提醒抑制={risks['repeat_notification_suppressed']}。",
                "",
            ])
    if skills := result.get("skills"):
        lines.extend(["## 技能与工程质量", "", f"- 技能沉淀：{skills['passed']}/{skills['total']} 场景通过。"])
    if engineering := result.get("engineering"):
        lines.extend([
            f"- 完整测试：{engineering['passed']}/{engineering['tests']} 通过，失败 {engineering['failures']}，错误 {engineering['errors']}。",
            f"- 新增核心模块行覆盖率：{engineering['coverage_line_percent']:.2f}%；分支覆盖率：{engineering['coverage_branch_percent']:.2f}%。", "",
        ])
    lines.extend(["## 口径边界", "", "本报告为离线标注集与可控故障注入结果，不代表生产数据，也不包含在线模型 Token、真实飞书/高德延迟。", ""])
    return "\n".join(lines)


def main() -> int:
    logger.remove()
    logger.add(sys.stderr, level="WARNING")
    parser = argparse.ArgumentParser(description="Run reproducible Copala offline evaluations.")
    parser.add_argument("--suite", choices=("all", "data", "memory", "routing", "life", "planning", "risk", "skills", "engineering"), default="all")
    parser.add_argument("--seed", type=int, default=20260824)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=ROOT / "evaluation" / "results" / "agent_eval.json")
    args = parser.parse_args()

    datasets = all_datasets()
    data_hashes = freeze_datasets(ROOT / "evaluation" / "data", datasets)
    result: dict[str, Any] = {
        "schema_version": 1,
        "seed": args.seed,
        "environment": environment(),
        "baseline": {"passed": 278, "failed": 3, "total": 281},
        "dataset_hashes": data_hashes,
        "dataset_hash": _hash(datasets),
    }
    if args.suite in {"all", "memory"}:
        result["memory"] = evaluate_memory(datasets["memory"], max(1, args.repeats))
    if args.suite in {"all", "routing"}:
        result["routing"] = evaluate_routing(datasets["routing"])
    if args.suite in {"all", "life"}:
        result["life_events"] = evaluate_life_events(datasets["life_events"])
    if args.suite in {"all", "planning"}:
        result["planning"] = evaluate_planning(datasets["planning"])
    if args.suite in {"all", "risk"}:
        result["risks"] = evaluate_risks()
    if args.suite in {"all", "skills"}:
        result["skills"] = evaluate_skills()
    if args.suite in {"all", "engineering"}:
        result["engineering"] = evaluate_engineering(args.output.parent)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report_path = args.output.with_suffix(".md")
    report_path.write_text(render_report(result), encoding="utf-8")
    print(json.dumps({"result": str(args.output), "report": str(report_path), "dataset_hash": result["dataset_hash"]}, ensure_ascii=False))
    engineering = result.get("engineering")
    return 1 if engineering and engineering["exit_code"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
