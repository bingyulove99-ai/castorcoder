"""Tests for TurnAuditor and ReleaseGate."""

from __future__ import annotations

from castorcoder.evals.gate import GateVerdict
from castorcoder.evals.turn_checks import TurnAuditor
from castorcoder.events import AgentEvent


def test_turn_auditor_clean_run(tmp_path):
    events = [
        AgentEvent("tool.started", "r1", data={"tool_call_id": "c1", "name": "read_file", "arguments": {"file_path": "foo.py"}}),
        AgentEvent("tool.completed", "r1", data={"tool_call_id": "c1", "success": True, "output": "content"}),
        AgentEvent("llm.completed", "r1", data={"cost_usd": 0.005}),
    ]
    results = TurnAuditor.audit_run(events, workspace_root=tmp_path, max_budget_usd=0.1)
    assert results["pairing_integrity"][0] is True
    assert results["errors_handled"][0] is True
    assert results["under_budget"][0] is True
    assert results["grounded_paths"][0] is True


def test_turn_auditor_detects_unmatched_tool_and_budget_exceeded():
    events = [
        AgentEvent("tool.started", "r1", data={"tool_call_id": "orphan_call"}),
        AgentEvent("llm.completed", "r1", data={"cost_usd": 1.50}),
    ]
    results = TurnAuditor.audit_run(events, max_budget_usd=1.0)
    assert results["pairing_integrity"][0] is False
    assert "orphan_call" in results["pairing_integrity"][1]
    assert results["under_budget"][0] is False
    assert "exceeded" in results["under_budget"][1]


def test_turn_auditor_detects_path_escape_and_syntax_error(tmp_path):
    bad_py = tmp_path / "broken.py"
    bad_py.write_text("def broken_syntax(:\n", encoding="utf-8")

    events = [
        AgentEvent("tool.started", "r1", data={"tool_call_id": "c1", "name": "write_file", "arguments": {"file_path": "broken.py"}}),
        AgentEvent("tool.completed", "r1", data={"tool_call_id": "c1", "success": True}),
        AgentEvent("tool.started", "r1", data={"tool_call_id": "c2", "name": "write_file", "arguments": {"file_path": "../outside.py"}}),
        AgentEvent("tool.completed", "r1", data={"tool_call_id": "c2", "success": True}),
    ]

    results = TurnAuditor.audit_run(events, workspace_root=tmp_path)
    assert results["grounded_paths"][0] is False
    assert "outside.py" in results["grounded_paths"][1]
    assert results["syntax_valid"][0] is False
    assert "broken.py" in results["syntax_valid"][1]


def test_release_gate_verdict_serialization(tmp_path):
    verdict = GateVerdict(
        timestamp="2026-11-10T00:00:00",
        tier1_unit_tests=True,
        tier2_regression_suite=True,
        tier1_details={"exit_code": 0},
        tier2_details={"passed": 5, "failed": 0, "total": 5},
        passed=True,
        reason="All gates passed.",
    )
    d = verdict.as_dict()
    assert d["passed"] is True
    assert d["tier1_unit_tests"] is True
    assert d["tier2_regression_suite"] is True
