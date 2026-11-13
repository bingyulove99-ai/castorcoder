"""JSON and Markdown reports for local evaluations."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from .models import TaskResult


def summarize(results: list[TaskResult]) -> dict[str, Any]:
    executed = [result for result in results if not result.skipped]
    gated = [result for result in executed if not result.benchmark]
    benchmarks = [result for result in executed if result.benchmark]
    passed = [result for result in gated if result.passed]
    skipped = [result for result in results if result.skipped]
    attempts = 0
    attempt_passes = 0.0
    for result in executed:
        metrics = result.metrics
        if metrics.get("attempts") is not None and metrics.get("passes") is not None:
            count = int(metrics["attempts"])
            successes = float(metrics["passes"])
        else:
            count = max(1, result.repeats)
            successes = (
                metrics.get("pass_rate", 1.0 if result.passed else 0.0) * count
            )
        attempts += count
        attempt_passes += successes
    task_pass_rate = (len(passed) / len(gated)) if gated else None
    return {
        "tasks": len(results),
        "executed": len(executed),
        "passed": len(passed),
        "failed": len(gated) - len(passed),
        "skipped": len(skipped),
        "benchmark_tasks": len(benchmarks),
        "pass_rate": task_pass_rate if task_pass_rate is not None else 0.0,
        "task_pass_rate": task_pass_rate,
        "attempts": attempts,
        "pass_at_1": (attempt_passes / attempts) if attempts else 0.0,
        "duration_ms": sum(result.duration_ms for result in results),
    }


def write_report(
    results: list[TaskResult],
    suite_name: str,
    path: str | Path,
) -> tuple[Path, Path]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    summary = summarize(results)
    payload = {
        "suite": suite_name,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "tasks": [result.as_dict() for result in results],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    markdown = render_markdown(results, suite_name, summary)
    markdown_path = target.with_suffix(".md")
    markdown_path.write_text(markdown, encoding="utf-8")
    return target, markdown_path


def render_markdown(
    results: list[TaskResult],
    suite_name: str,
    summary: dict[str, Any],
) -> str:
    task_rate = summary.get("task_pass_rate", summary["pass_rate"])
    rate_text = f"{task_rate:.1%}" if task_rate is not None else "n/a (benchmark only)"
    lines = [
        f"# CastorCoder evaluation: {suite_name}",
        "",
        f"- Tasks: {summary['tasks']}",
        f"- Executed: {summary['executed']}",
        f"- Passed (gated tasks): {summary['passed']}",
        f"- Failed (gated tasks): {summary['failed']}",
        f"- Skipped: {summary['skipped']}",
        f"- Benchmark tasks: {summary.get('benchmark_tasks', 0)}",
        f"- Task pass rate (all repeats): {rate_text}",
        f"- Attempts: {summary.get('attempts', 0)}",
        f"- pass@1: {summary.get('pass_at_1', summary['pass_rate']):.1%}",
        f"- Wall time: {summary['duration_ms'] / 1000:.2f}s",
        "",
        "| Task | Kind | Status | Repeats | Key metrics |",
        "|---|---|---|---:|---|",
    ]
    for result in results:
        if result.skipped:
            status = "SKIP"
        elif result.benchmark:
            status = "MEASURED"
        else:
            status = "PASS" if result.passed else "FAIL"
        metrics = _metric_summary(result.metrics)
        lines.append(
            f"| {result.task_id} | {result.kind} | {status} | {result.repeats} | {metrics} |"
        )
        if result.reason:
            lines.append(f"| | | {_escape(result.reason)} | | |")
        if result.error:
            lines.append(f"| | | {_escape(result.error)} | | |")
    lines.append("")
    return "\n".join(lines)


def _metric_summary(metrics: dict[str, Any]) -> str:
    keys = (
        "recall",
        "mrr",
        "ndcg",
        "pass_at_1",
        "pass_at_1_ci95",
        "passes",
        "attempts",
        "infra_errors",
        "pass_rate",
        "tool_calls",
        "rounds",
        "mean_rounds",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "elapsed_ms",
        "mean_elapsed_ms",
        "cost_usd",
        "total_cost_usd",
        "mean_cost_usd",
        "stop_reason",
    )
    parts = []
    for key in keys:
        value = metrics.get(key)
        if value is None:
            continue
        if key == "pass_at_1_ci95" and isinstance(value, (list, tuple)) and len(value) == 2:
            parts.append(f"ci95=[{value[0]:.3f}, {value[1]:.3f}]")
        elif isinstance(value, float):
            parts.append(f"{key}={value:.3f}")
        else:
            parts.append(f"{key}={value}")
    return ", ".join(parts) or "-"


def _escape(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")[:300]


def print_summary(results: list[TaskResult], suite_name: str) -> None:
    summary = summarize(results)
    print(f"\nSuite: {suite_name}")
    print(
        f"Tasks: {summary['tasks']} | executed: {summary['executed']} | "
        f"passed: {summary['passed']} | failed: {summary['failed']} | "
        f"measured: {summary.get('benchmark_tasks', 0)} | "
        f"skipped: {summary['skipped']} | pass@1: {summary['pass_at_1']:.1%}"
    )
    for result in results:
        if result.skipped:
            status = "SKIP"
        elif result.benchmark:
            status = "MEASURED"
        else:
            status = "PASS" if result.passed else "FAIL"
        detail = result.reason or result.error
        print(f"  [{status}] {result.task_id} ({result.kind}) {detail}")
