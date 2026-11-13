"""Long-term memory store, writer, injection and agent integration tests."""

from __future__ import annotations

import time

from castorcoder.agent import Agent
from castorcoder.evals.models import ScriptedStep, ToolCallStep
from castorcoder.evals.runner import ScriptedLLM
from castorcoder.llm import LLMResponse
from castorcoder.memory import MemoryContext, MemoryStore, MemoryWriter, build_context
from castorcoder.memory.models import MemoryRecord
from castorcoder.tools.memory import SearchMemoryTool, UpdateMemoryTool


def _store(tmp_path, workspace="test"):
    return MemoryStore(tmp_path / "memory.db", workspace=workspace)


def test_memory_add_dedupes_and_searches(tmp_path):
    store = _store(tmp_path)
    record, created = store.add(
        "Always run pytest before committing.",
        kind="preference",
        source="user",
        confidence=0.8,
    )
    assert created
    duplicate, created_again = store.add(
        "Always run pytest before committing.",
        kind="preference",
        confidence=0.9,
    )
    assert not created_again
    assert duplicate.id == record.id
    assert duplicate.confidence == 0.9

    hits = store.search("commit tests", k=3)
    assert hits
    assert hits[0].record.id == record.id
    assert "bm25" in hits[0].sources and "vector" in hits[0].sources


def test_memory_ttl_forget_and_gc(tmp_path):
    store = _store(tmp_path)
    old, _ = store.add("This memory already expired.", ttl_days=-1)
    assert store.get(old.id) is not None
    assert store.search("expired memory", k=3) == []
    assert store.gc() == 1
    assert store.get(old.id) is None

    keep, _ = store.add("Keep this memory.", kind="fact")
    assert store.forget(keep.id)
    assert not store.forget(keep.id)
    assert not store.forget("missing")


def test_memory_supersede(tmp_path):
    store = _store(tmp_path)
    old, _ = store.add("Use the old retrieval backend.", kind="decision")
    new, _ = store.add("Use SQLite FTS5 for retrieval.", kind="decision")
    assert store.supersede(old.id, new.id)
    hits = store.search("retrieval backend", k=5)
    ids = {hit.record.id for hit in hits}
    assert old.id not in ids
    assert new.id in ids


def test_memory_writer_distills_durable_items(tmp_path):
    store = _store(tmp_path)
    messages = [
        {"role": "user", "content": "Always run ruff before committing."},
        {"role": "tool", "content": "Error: pytest failed with exit code 1"},
        {"role": "assistant", "content": "The root cause is a missing import."},
    ]
    from castorcoder.agent import AgentStats

    written = MemoryWriter(store).distill(
        messages,
        AgentStats(run_id="run-1", tool_calls=1, rounds=2),
        prompt="Always run ruff before committing.",
        changed_paths=["app.py"],
    )
    assert written
    kinds = {record.kind for record in store.list(limit=20)}
    assert {"episode", "error", "decision", "preference"} <= kinds


def test_memory_injector_respects_budget(tmp_path):
    store = _store(tmp_path)
    store.add("Workspace paths must stay inside the project root.", kind="convention")
    store.add("Always redact API keys before tracing.", kind="preference")
    context = build_context(store, "paths and keys", k=5, max_chars=300)
    assert context
    assert "Retrieved long-term memory" in context.text
    assert len(context.text) <= 320


def test_memory_tools(tmp_path):
    store = _store(tmp_path)
    update = UpdateMemoryTool(store)
    search = SearchMemoryTool(store)
    assert "Added memory" in update.execute(
        action="add", content="Use SQLite FTS5 for retrieval.", kind="decision"
    )
    output = search.execute(query="retrieval storage", k=3)
    assert "FTS5" in output
    assert "Memories:" in update.execute(action="stats")
    assert "Garbage-collected" in update.execute(action="gc")


def test_agent_injects_memory_and_auto_remembers(tmp_path):
    (tmp_path / "app.py").write_text("value = 1\n")
    store = _store(tmp_path, workspace=str(tmp_path.resolve()))
    store.add("Always run pytest before committing.", kind="preference", confidence=0.9)

    captured = {}

    class RecordingLLM:
        model = "recording"
        total_prompt_tokens = 0
        total_completion_tokens = 0

        @property
        def estimated_cost(self):
            return None

        def chat(self, messages, tools=None, on_token=None):
            captured["messages"] = messages
            return LLMResponse(content="done")

    agent = Agent(
        RecordingLLM(),
        workspace=tmp_path,
        memory_store=store,
        auto_remember=True,
        memory_inject=True,
    )
    result = agent.run("What should I run before committing?")
    assert result.stats.memory_hits >= 1
    assert any(
        "Retrieved long-term memory" in str(message.get("content", ""))
        for message in captured["messages"]
    )


def test_agent_undo_restores_deleted_and_edited_files(tmp_path):
    steps = [
        ScriptedStep(
            content="create",
            tool_calls=(ToolCallStep("write_file", {"file_path": "new.txt", "content": "x"}),),
        ),
        ScriptedStep(
            content="edit",
            tool_calls=(
                ToolCallStep(
                    "write_file",
                    {"file_path": "existing.txt", "content": "changed"},
                ),
            ),
        ),
        ScriptedStep(content="done"),
    ]
    (tmp_path / "existing.txt").write_text("original")
    agent = Agent(ScriptedLLM(steps), workspace=tmp_path)
    agent.run("mutate files")
    assert (tmp_path / "new.txt").exists()
    assert (tmp_path / "existing.txt").read_text() == "changed"

    restored = agent.undo()
    assert len(restored) == 2
    assert not (tmp_path / "new.txt").exists()
    assert (tmp_path / "existing.txt").read_text() == "original"


def test_memory_record_round_trip(tmp_path):
    store = _store(tmp_path)
    record, _ = store.add("Tagged memory", kind="fact", tags=("a", "b"))
    loaded = store.get(record.id)
    assert isinstance(loaded, MemoryRecord)
    assert loaded is not None and loaded.tags == ("a", "b")
    assert loaded.as_dict()["tags"] == ["a", "b"]
    assert store.stats()["active"] == 1
    assert time.time() >= loaded.created_at


def test_memory_list_filters_and_clear(tmp_path):
    store = _store(tmp_path)
    store.add("A fact.", kind="fact")
    store.add("A preference.", kind="preference")
    assert len(store.list()) == 2
    assert len(store.list(kind="fact")) == 1
    assert store.supersede("missing", "missing") is False
    assert build_context(store, "   ") == MemoryContext("", (), 0)
    store.clear()
    assert store.list() == []
    assert store.stats()["total"] == 0


def test_memory_tools_list_forget_and_errors(tmp_path):
    store = _store(tmp_path)
    update = UpdateMemoryTool(store)
    record, _ = store.add("Remember me.", kind="fact")
    assert "Remember me" in update.execute(action="list", limit=5)
    assert "Forgotten" in update.execute(action="forget", memory_id=record.id)
    assert "not found" in update.execute(action="forget", memory_id=record.id)
    assert "requires content" in update.execute(action="add")
    assert "requires memory_id" in update.execute(action="forget")
    assert "unknown action" in update.execute(action="nope")
    assert "Memory is empty" in update.execute(action="list")


def test_memory_writer_skips_trivial_runs(tmp_path):
    from castorcoder.agent import AgentStats

    store = _store(tmp_path)
    assert MemoryWriter(store).distill(
        [{"role": "assistant", "content": "hello"}],
        AgentStats(run_id="run", tool_calls=0),
    ) == []
