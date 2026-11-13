"""Pre-built deterministic workflows for common software engineering tasks."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .engine import END, START, Graph, Node

if TYPE_CHECKING:
    from ..agent import Agent


def create_code_fix_workflow(agent: Agent, *, max_fix_attempts: int = 3) -> Graph:
    """Builds a deterministic bugfix workflow:

    START -> triage -> search -> implement -> verify -[router]-> review -> END
                                     ^          |
                                     +-- retry -+
    """
    g = Graph("code_fix_pipeline")

    def triage_fn(state: dict[str, Any]) -> dict[str, Any]:
        task = state.get("task", "")
        return {
            "triage_summary": f"Targeting issue: {task[:80]}",
            "status": "triaged",
        }

    def search_fn(state: dict[str, Any]) -> dict[str, Any]:
        task = state.get("task", "")
        hits_summary = []
        if agent.code_index:
            hits = agent.code_index.search(task, limit=3)
            hits_summary = [f"{h.chunk.path}:{h.chunk.start_line} ({h.chunk.symbol})" for h in hits]
        return {
            "search_hits": hits_summary,
            "status": "searched",
        }

    def implement_fn(state: dict[str, Any]) -> dict[str, Any]:
        prompt = state.get("task", "")
        # If retrying after a test failure, attach the verification error
        last_error = state.get("verify_error")
        if last_error:
            prompt += f"\n\nPrevious attempt failed automated verification:\n{last_error}\nPlease fix the issue."

        res = agent.run(prompt)
        return {
            "agent_output": res.output,
            "modified_files": agent.change_tracker.paths(),
            "status": "implemented",
        }

    def verify_fn(state: dict[str, Any]) -> dict[str, Any]:
        from ..autorun import AutoRunner

        runner = AutoRunner(agent.workspace.root)
        run_res = runner.run()
        if run_res is None:
            # No tests found, consider passed
            return {"verified": True, "verify_error": None, "verify_status": "no_tests_found"}
        return {
            "verified": run_res.passed,
            "verify_error": run_res.output if not run_res.passed else None,
            "verify_status": "passed" if run_res.passed else f"failed_{run_res.exit_code}",
        }

    def verify_router(state: dict[str, Any]) -> str:
        if state.get("verified", False):
            return "pass"
        return "retry"

    def review_fn(state: dict[str, Any]) -> dict[str, Any]:
        mod = state.get("modified_files", [])
        verified = state.get("verified", False)
        status_txt = "VERIFIED" if verified else "UNVERIFIED"
        return {
            "final_report": f"Pipeline completed ({status_txt}). Modified {len(mod)} file(s): {', '.join(mod)}",
            "status": "completed",
        }

    g.add_node(Node("triage", triage_fn, kind="fn"))
    g.add_node(Node("search", search_fn, kind="tool"))
    g.add_node(Node("implement", implement_fn, kind="agent", max_visits=max_fix_attempts))
    g.add_node(Node("verify", verify_fn, kind="eval", max_visits=max_fix_attempts))
    g.add_node(Node("review", review_fn, kind="fn"))

    g.add_edge(START, "triage")
    g.add_edge("triage", "search")
    g.add_edge("search", "implement")
    g.add_edge("implement", "verify")

    g.add_router(
        "verify",
        verify_router,
        targets={"pass": "review", "retry": "implement"},
    )
    g.add_edge("review", END)

    return g


def create_review_workflow(agent: Agent) -> Graph:
    """Builds a static review pipeline: START -> inspect_diff -> check_syntax -> synthesize -> END."""
    g = Graph("code_review_pipeline")

    def inspect_diff_fn(state: dict[str, Any]) -> dict[str, Any]:
        diffs = [item["path"] for item in agent.change_tracker.snapshot()]
        if not diffs:
            # The CLI can seed this list from ``git status`` when the workflow
            # reviews pre-existing workspace changes instead of agent edits.
            diffs = [str(path) for path in state.get("changed_files", [])]
        return {"changed_files": diffs}

    def check_syntax_fn(state: dict[str, Any]) -> dict[str, Any]:
        import ast

        errors = []
        for path_str in state.get("changed_files", []):
            if path_str.endswith(".py"):
                full_path = agent.workspace.root / path_str
                if full_path.is_file():
                    try:
                        ast.parse(full_path.read_text(encoding="utf-8", errors="ignore"))
                    except SyntaxError as exc:
                        errors.append(f"{path_str}: SyntaxError at line {exc.lineno}: {exc.msg}")
        return {"syntax_errors": errors, "syntax_ok": len(errors) == 0}

    def synthesize_fn(state: dict[str, Any]) -> dict[str, Any]:
        syntax_ok = state.get("syntax_ok", True)
        changed = state.get("changed_files", [])
        return {
            "verdict": "APPROVED" if syntax_ok else "REJECTED",
            "summary": f"Reviewed {len(changed)} file(s). Syntax OK: {syntax_ok}",
        }

    g.add_node(Node("inspect_diff", inspect_diff_fn, kind="tool"))
    g.add_node(Node("check_syntax", check_syntax_fn, kind="eval"))
    g.add_node(Node("synthesize", synthesize_fn, kind="fn"))

    g.add_edge(START, "inspect_diff")
    g.add_edge("inspect_diff", "check_syntax")
    g.add_edge("check_syntax", "synthesize")
    g.add_edge("synthesize", END)

    return g
