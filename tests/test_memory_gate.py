"""Tests for smart memory controls: gates, filters, and markdown export."""

from __future__ import annotations

from castorcoder.agent import Agent
from castorcoder.llm import LLM, LLMResponse
from castorcoder.memory.gate import (
    AntiPatternFilter,
    RetrievalGate,
    SlotGate,
    export_memory_markdown,
)
from castorcoder.memory.models import MemoryHit, MemoryRecord
from castorcoder.memory.store import MemoryStore


class FakeLLM(LLM):
    def __init__(self, response_text: str = "") -> None:
        super().__init__(model="test-fake", api_key="sk-fake")
        self.response_text = response_text
        self.last_prompt = ""

    def chat(self, messages, tools=None, on_token=None) -> LLMResponse:
        self.last_prompt = messages[-1]["content"] if messages else ""
        return LLMResponse(content=self.response_text)


def test_retrieval_gate_heuristic_and_llm(tmp_path):
    gate = RetrievalGate(enabled=True)

    # Heuristic self-contained action without memory need
    needed, q, reason = gate.should_retrieve(None, "rename foo to bar")
    assert not needed
    assert "heuristic" in reason

    # Keyword match forces retrieval
    needed, q, reason = gate.should_retrieve(None, "what was our team convention for logging?")
    assert needed
    assert "convention" in reason

    # Model says no
    llm_no = FakeLLM('{"retrieve": false, "query": "", "reason": "math problem"}')
    needed, q, reason = gate.should_retrieve(llm_no, "calculate 128 * 4")
    assert not needed
    assert "math problem" in reason

    # Model says yes
    llm_yes = FakeLLM('{"retrieve": true, "query": "auth jwt token", "reason": "needs auth rule"}')
    needed, q, reason = gate.should_retrieve(llm_yes, "how do we authenticate users?")
    assert needed
    assert q == "auth jwt token"
    assert "needs auth rule" in reason

    # Fail open on bad JSON or error
    llm_broken = FakeLLM("not valid json at all")
    needed, q, reason = gate.should_retrieve(llm_broken, "any task")
    assert needed
    assert "fail open" in reason


def test_slot_gate_filters_low_score_and_superseded():
    gate = SlotGate(threshold=0.5)

    rec_good = MemoryRecord("id1", "convention", "always use ruff", confidence=0.8)
    rec_low = MemoryRecord("id2", "fact", "yesterday was sunny", confidence=0.3)
    rec_superseded = MemoryRecord("id3", "decision", "old arch", superseded_by="id4", confidence=0.9)

    hits = [
        MemoryHit(rec_good, score=0.85, sources=("fts",)),
        MemoryHit(rec_low, score=0.2, sources=("vector",)),
        MemoryHit(rec_superseded, score=0.9, sources=("fts",)),
    ]

    selected = gate.filter_hits(hits)
    assert len(selected) == 1
    assert selected[0].record.id == "id1"


def test_anti_pattern_filter():
    # Transient operational errors
    assert AntiPatternFilter.is_ops_state("HTTP 429 rate limit reached on endpoint")
    assert AntiPatternFilter.is_ops_state("Token limit exceeded, connection timed out")
    assert AntiPatternFilter.is_ops_state("account balance is $0.00")
    assert not AntiPatternFilter.is_ops_state("Use pytest fixture for database testing")

    # Meta memory states
    assert AntiPatternFilter.is_meta_state("No memory found for logging convention")
    assert AntiPatternFilter.is_meta_state("Memory store lacks database credentials")
    assert not AntiPatternFilter.is_meta_state("Database credentials should be loaded from .env")

    # Restatements
    recalled = ["Use SQLAlchemy 2.0 style queries with select()"]
    assert AntiPatternFilter.is_restatement("We use SQLAlchemy 2.0 style with select()", recalled)
    assert not AntiPatternFilter.is_restatement("Prefer Alembic for schema migrations", recalled)

    # Combined check
    assert AntiPatternFilter.is_acceptable("Prefer Alembic for migrations", recalled)
    assert not AntiPatternFilter.is_acceptable("HTTP 500 internal server error", recalled)


def test_export_memory_markdown(tmp_path):
    store = MemoryStore(tmp_path / "memory.db", workspace=str(tmp_path))
    store.add("Use Pydantic v2 for data schemas", kind="convention", confidence=0.9)
    store.add("Chose SQLite with FTS5 for local search", kind="decision", confidence=0.85)

    md_path = export_memory_markdown(store, tmp_path / "MEMORY.md")
    assert md_path.is_file()
    content = md_path.read_text(encoding="utf-8")
    assert "# Workspace Long-Term Memory" in content
    assert "Conventions & Architectural Guidelines" in content
    assert "Pydantic v2" in content
    assert "Technical Decisions" in content
    assert "SQLite with FTS5" in content


def test_agent_uses_retrieval_gate_and_exports_memory(tmp_path):
    store = MemoryStore(tmp_path / "memory.db", workspace=str(tmp_path))
    store.add("Always use snake_case for functions", kind="convention", confidence=0.9)

    class ScriptedChatLLM(LLM):
        def __init__(self):
            super().__init__(model="test", api_key="sk-fake")

        def chat(self, messages, tools=None, on_token=None):
            content = messages[-1]["content"] if messages else ""
            if "retrieval gate" in content:
                if "rename" in content:
                    return LLMResponse(content='{"retrieve": false, "reason": "code edit"}')
                return LLMResponse(content='{"retrieve": true, "query": "convention naming", "reason": "needs rule"}')
            return LLMResponse(content="Done")

    agent = Agent(
        llm=ScriptedChatLLM(),
        workspace=tmp_path,
        memory_store=store,
        memory_inject=True,
        memory_gate=True,
        auto_remember=True,
    )

    # Run a self-contained command where gate intercepts
    result = agent.run("rename my_file to new_file")
    assert result.stats.memory_hits == 0

    # Run task asking for convention where gate allows
    result2 = agent.run("what is our convention for naming?")
    assert result2.stats.memory_hits >= 1
