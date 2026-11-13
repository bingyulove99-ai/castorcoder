"""Tests for wave-based deterministic graph engine and workflows."""

from __future__ import annotations

from pathlib import Path

import pytest

from castorcoder.agent import Agent
from castorcoder.graph.engine import (
    END,
    START,
    Graph,
    GraphCycleLimit,
    GraphStateCollision,
    Node,
)
from castorcoder.graph.workflows import create_code_fix_workflow, create_review_workflow
from castorcoder.llm import LLM, LLMResponse


def test_graph_linear_execution():
    g = Graph("linear")
    g.add_node(Node("step1", lambda s: {"a": 1}))
    g.add_node(Node("step2", lambda s: {"b": s["a"] + 1}))

    g.add_edge(START, "step1")
    g.add_edge("step1", "step2")
    g.add_edge("step2", END)

    state = g.run({"init": 0})
    assert state["init"] == 0
    assert state["a"] == 1
    assert state["b"] == 2


def test_graph_parallel_wave_and_collision():
    g = Graph("parallel")
    g.add_node(Node("left", lambda s: {"left_res": 10}))
    g.add_node(Node("right", lambda s: {"right_res": 20}))

    g.add_edge(START, "left")
    g.add_edge(START, "right")
    g.add_edge("left", END)
    g.add_edge("right", END)

    state = g.run({})
    assert state["left_res"] == 10
    assert state["right_res"] == 20

    # Test collision: both nodes write to "conflict_key"
    g_bad = Graph("collision")
    g_bad.add_node(Node("n1", lambda s: {"conflict": 1}))
    g_bad.add_node(Node("n2", lambda s: {"conflict": 2}))
    g_bad.add_edge(START, "n1")
    g_bad.add_edge(START, "n2")

    with pytest.raises(GraphStateCollision):
        g_bad.run({})


def test_graph_code_router_and_cycles():
    g = Graph("loop")

    def worker_fn(s: dict) -> dict:
        cnt = s.get("count", 0) + 1
        return {"count": cnt}

    def route_fn(s: dict) -> str:
        return "continue" if s["count"] < 3 else "done"

    g.add_node(Node("worker", worker_fn, max_visits=5))
    g.add_edge(START, "worker")
    g.add_router("worker", route_fn, targets={"continue": "worker", "done": END})

    events = []
    state = g.run({}, observer=lambda k, d: events.append(k))
    assert state["count"] == 3
    assert "graph_start" in events
    assert "route" in events
    assert "graph_end" in events


def test_graph_max_visits_cycle_guard():
    g = Graph("infinite")
    g.add_node(Node("spin", lambda s: {"val": 1}, max_visits=2))
    g.add_edge(START, "spin")
    g.add_router("spin", lambda s: "loop", targets={"loop": "spin"})

    with pytest.raises(GraphCycleLimit):
        g.run({})


def test_code_fix_workflow_execution(tmp_path):
    # Setup test file in workspace
    (tmp_path / "verify.py").write_text("import sys\nsys.exit(0)\n", encoding="utf-8")

    class FakeAgentLLM(LLM):
        def __init__(self):
            super().__init__(model="test", api_key="sk-fake")

        def chat(self, messages, tools=None, on_token=None):
            return LLMResponse(content="Fixed bug")

    agent = Agent(llm=FakeAgentLLM(), workspace=tmp_path)
    workflow = create_code_fix_workflow(agent)

    state = workflow.run({"task": "Fix the division by zero bug"})
    assert "_last_error" not in state, state.get("_last_error")
    assert state["status"] == "completed"
    assert state["verified"] is True
    assert "VERIFIED" in state["final_report"]


def test_review_workflow_execution(tmp_path):
    py_file = tmp_path / "code.py"
    py_file.write_text("def valid_syntax():\n    return 42\n", encoding="utf-8")

    class FakeAgentLLM(LLM):
        def __init__(self):
            super().__init__(model="test", api_key="sk-fake")

        def chat(self, messages, tools=None, on_token=None):
            return LLMResponse(content="Reviewed")

    agent = Agent(llm=FakeAgentLLM(), workspace=tmp_path)
    # Simulate modified file
    agent.change_tracker.record(Path("code.py"), "", py_file.read_text(encoding="utf-8"))

    workflow = create_review_workflow(agent)
    state = workflow.run({})
    assert state["verdict"] == "APPROVED"
    assert state["syntax_ok"] is True


def test_review_workflow_accepts_seeded_changed_files(tmp_path):
    (tmp_path / "code.py").write_text("def valid_syntax():\n    return 42\n", encoding="utf-8")

    class FakeAgentLLM(LLM):
        def __init__(self):
            super().__init__(model="test", api_key="sk-fake")

        def chat(self, messages, tools=None, on_token=None):
            return LLMResponse(content="Reviewed")

    agent = Agent(llm=FakeAgentLLM(), workspace=tmp_path)
    workflow = create_review_workflow(agent)
    state = workflow.run({"changed_files": ["code.py"]})
    assert state["changed_files"] == ["code.py"]
    assert state["syntax_ok"] is True
    assert state["verdict"] == "APPROVED"
