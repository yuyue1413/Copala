from pathlib import Path

from copala.agent.context import ContextBuilder
from copala.agent.memory_rag.schema import MemoryItem
from copala.agent.memory_rag.store import MemoryRAGStore


def test_memory_rag_retrieves_travel_preference(tmp_path: Path) -> None:
    store = MemoryRAGStore(tmp_path)
    store.upsert(
        MemoryItem(
            id="travel-window-seat",
            type="travel_preference",
            text="用户出行时偏好靠窗座位，不喜欢红眼航班。",
            source="email",
            tags=["travel"],
            confidence=0.9,
            priority=8,
        )
    )

    results = store.retrieve("帮我规划航班座位和出行时间", top_k=3)

    assert results
    assert results[0].item.id == "travel-window-seat"
    assert "dense" in results[0].retrievers or "bm25" in results[0].retrievers


def test_memory_rag_reindex_workspace_reads_kb_files(tmp_path: Path) -> None:
    kb = tmp_path / "memory" / "kb"
    kb.mkdir(parents=True)
    (kb / "preferences.md").write_text("用户购物时偏好性价比和售后。", encoding="utf-8")

    store = MemoryRAGStore(tmp_path)
    count = store.reindex_workspace()
    results = store.retrieve("购物售后", top_k=3)

    assert count >= 1
    assert any("购物" in r.item.text for r in results)


def test_context_builder_injects_retrieved_personal_memory(tmp_path: Path) -> None:
    store = MemoryRAGStore(tmp_path)
    store.upsert(
        MemoryItem(
            id="shopping-pref",
            type="shopping_preference",
            text="用户购买电子产品时优先考虑售后。",
            source="conversation",
            priority=8,
        )
    )
    builder = ContextBuilder(tmp_path)

    messages = builder.build_messages([], "推荐一个电子产品购物方案")

    assert "Retrieved Personal Memory" in messages[-1]["content"]
    assert "售后" in messages[-1]["content"]
