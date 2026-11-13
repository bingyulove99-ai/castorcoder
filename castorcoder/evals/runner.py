"""Local evaluation runner for scripted, retrieval and live tasks."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

from ..agent import Agent
from ..events import AgentEvent, EventBus
from ..index.embedder import HashingEmbedder
from ..index.indexer import CodeIndex
from ..llm import LLM, LLMResponse, ToolCall
from ..memory.store import MemoryStore
from ..tools import create_tools
from ..workspace import Workspace
from .datasets import GenerationInstance, load_instances
from .metrics import (
    evaluate_ranking,
    gold_matches_code,
    gold_matches_memory,
    mean_metrics,
    percentile,
    wilson_interval,
)
from .models import EvalSuite, EvalTask, ScriptedStep, TaskResult


class ScriptedLLM:
    """Deterministic stand-in for a model, used by offline runtime evaluations."""

    def __init__(self, steps: list[ScriptedStep]) -> None:
        self.steps = list(steps)
        self.model = "scripted"
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self._call_index = 0

    @property
    def estimated_cost(self) -> float | None:
        return None

    def chat(self, messages, tools=None, on_token=None):
        if not self.steps:
            return LLMResponse(content="(script exhausted)")
        step = self.steps.pop(0)
        if step.content and on_token:
            on_token(step.content)
        calls = []
        for call in step.tool_calls:
            self._call_index += 1
            calls.append(
                ToolCall(
                    id=f"call-{self._call_index}",
                    name=call.name,
                    arguments=dict(call.arguments),
                )
            )
        return LLMResponse(content=step.content, tool_calls=calls)


@dataclass
class EvalOptions:
    only: set[str] = field(default_factory=set)
    ids: set[str] = field(default_factory=set)
    tags: set[str] = field(default_factory=set)
    repeats: int | None = None
    live: bool = False
    work_root: Path = field(default_factory=lambda: Path(".tmp/evals"))
    verbose: bool = False
    jobs: int = 1
    infra_retries: int = 2
    resume: bool = False
    checkpoint_dir: Path = field(default_factory=lambda: Path(".tmp/evals/checkpoints"))
    # Injected by tests; production builds a real client from the environment.
    llm_factory: Callable[[GenerationInstance], Any] | None = None

    def __post_init__(self) -> None:
        if self.checkpoint_dir == Path(".tmp/evals/checkpoints") and self.work_root != Path(".tmp/evals"):
            self.checkpoint_dir = self.work_root / "checkpoints"


def run_suite(suite: EvalSuite, options: EvalOptions | None = None) -> list[TaskResult]:
    options = options or EvalOptions()
    options.work_root.mkdir(parents=True, exist_ok=True)
    results: list[TaskResult] = []
    for task in suite.tasks:
        if options.only and task.id not in options.only:
            continue
        if options.tags and not set(task.tags) & options.tags:
            continue
        repeats = options.repeats or task.repeats
        started = time.perf_counter()
        try:
            if task.kind == "scripted":
                result = run_scripted_task(task, options, repeats)
            elif task.kind == "retrieval":
                result = run_retrieval_task(task, options)
            elif task.kind == "memory_retrieval":
                result = run_memory_retrieval_task(task, options)
            elif task.kind == "live":
                result = run_live_task(task, options, repeats)
            elif task.kind == "generation":
                result = run_generation_task(task, options, repeats)
            else:
                result = TaskResult(
                    task_id=task.id,
                    kind=task.kind,
                    passed=False,
                    reason=f"unknown task kind: {task.kind}",
                )
        except Exception as exc:
            result = TaskResult(
                task_id=task.id,
                kind=task.kind,
                passed=False,
                error=f"{type(exc).__name__}: {exc}",
            )
        result.duration_ms = (time.perf_counter() - started) * 1000
        results.append(result)
        if options.verbose:
            status = "SKIP" if result.skipped else ("PASS" if result.passed else "FAIL")
            print(f"[{status}] {result.task_id} ({result.kind}) {result.reason}")
    return results


def run_scripted_task(task: EvalTask, options: EvalOptions, repeats: int) -> TaskResult:
    if task.fixture_path is None or not task.fixture_path.is_dir():
        return TaskResult(task.id, task.kind, False, reason="fixture is missing")
    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for _ in range(max(1, repeats)):
        work = _make_work_dir(options.work_root, task.id)
        try:
            destination = work / "repo"
            db_dir = work / "db"
            db_dir.mkdir()
            shutil.copytree(task.fixture_path, destination)
            workspace = Workspace(destination)
            index_store = None
            memory_store = None
            extra: dict[str, Any] = {}
            if task.use_index:
                index_store = CodeIndex(
                    workspace,
                    db_path=db_dir / "index.db",
                    embedder=HashingEmbedder(),
                )
                extra["code_index"] = index_store
            if task.use_memory:
                memory_store = MemoryStore(
                    db_dir / "memory.db",
                    workspace=str(workspace.root),
                    embedder=HashingEmbedder(),
                )
                extra["memory_store"] = memory_store

            tools = create_tools(workspace, **extra)
            event_bus = EventBus()
            events: list[AgentEvent] = []
            event_bus.subscribe(events.append)
            agent = Agent(
                llm=ScriptedLLM(task.steps),
                tools=tools,
                workspace=workspace,
                max_rounds=task.max_rounds,
                max_cost_usd=task.max_cost_usd,
                permission_mode=task.permission_mode,
                event_bus=event_bus,
                memory_store=memory_store,
                auto_remember=task.use_memory,
                memory_inject=task.use_memory,
                code_index=index_store,
            )
            result = agent.run(task.prompt or task.id)
            pairing_ok, pairing_reason = check_tool_pairing(agent.messages)
            verify_ok, verify_output = (True, "")
            if task.verify:
                _apply_verifier(task, destination)
                verify_ok, verify_output = _run_verify(task.verify, destination)
            stop_ok = (
                task.expect_stop_reason is None
                or result.stats.stop_reason == task.expect_stop_reason
            )
            passed = pairing_ok and verify_ok and stop_ok
            if not pairing_ok:
                failures.append(pairing_reason)
            if not verify_ok:
                failures.append(f"verify failed: {verify_output[-500:]}")
            if not stop_ok:
                failures.append(
                    f"stop_reason={result.stats.stop_reason}, expected={task.expect_stop_reason}"
                )
            rows.append(
                {
                    "rounds": result.stats.rounds,
                    "tool_calls": result.stats.tool_calls,
                    "tool_failures": result.stats.tool_failures,
                    "compressions": result.stats.compressions,
                    "memory_hits": result.stats.memory_hits,
                    "memories_written": result.stats.memories_written,
                    "elapsed_ms": result.stats.elapsed_ms,
                    "stop_reason": result.stats.stop_reason,
                    "verify_ok": verify_ok,
                    "pairing_ok": pairing_ok,
                    "passed": passed,
                    "output": result.output[:1_000],
                    "events": len(events),
                }
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return TaskResult(
        task_id=task.id,
        kind=task.kind,
        passed=bool(rows) and all(row["passed"] for row in rows),
        repeats=max(1, repeats),
        reason="; ".join(dict.fromkeys(failures)),
        metrics=_aggregate(rows),
    )


def run_retrieval_task(task: EvalTask, options: EvalOptions) -> TaskResult:
    if task.fixture_path is None or not task.fixture_path.is_dir():
        return TaskResult(task.id, task.kind, False, reason="fixture is missing")
    if not task.queries:
        return TaskResult(task.id, task.kind, False, reason="no queries configured")
    workspace = Workspace(task.fixture_path)
    db_dir = _make_work_dir(options.work_root, f"{task.id}-db")
    try:
        index = CodeIndex(
            workspace,
            db_path=db_dir / "index.db",
            embedder=HashingEmbedder(),
            auto_build=False,
        )
        stats = index.update(force=True)
        k_values = task.ks or [task.k]
        by_k: dict[int, dict[str, dict[str, Any]]] = {}
        for k_value in k_values:
            by_mode: dict[str, dict[str, Any]] = {}
            for mode in task.modes:
                rows: list[dict[str, float]] = []
                details: list[dict[str, Any]] = []
                for query in task.queries:
                    text = str(query.get("query", ""))
                    gold = query.get("gold", [])
                    if isinstance(gold, str):
                        gold = [gold]
                    labels = [_normalize_gold(str(item), task.gold_level) for item in gold]
                    hits = index.search(
                        text,
                        k=k_value,
                        mode=mode,
                        exclude_prefixes=task.exclude,
                    )
                    metrics = evaluate_ranking(
                        labels,
                        hits,
                        k=k_value,
                        matcher=gold_matches_code,
                    )
                    rows.append(metrics)
                    if k_value == task.k or not task.ks:
                        details.append(
                            {
                                "query": text,
                                "gold": labels,
                                "retrieved": [hit.location for hit in hits],
                                **metrics,
                            }
                        )
                by_mode[mode] = {
                    **mean_metrics(rows),
                    "queries": details,
                }
            by_k[k_value] = by_mode

        primary_k = task.k if task.k in by_k else k_values[0]
        primary_modes = by_k[primary_k]
        primary = primary_modes[task.modes[0]]
        aggregate = {key: value for key, value in primary.items() if key != "queries"}
        passed = aggregate["recall"] >= task.min_recall
        metrics: dict[str, Any] = {
            **aggregate,
            "mode": task.modes[0],
            "gold_level": task.gold_level,
            "k": primary_k,
            "min_recall": task.min_recall,
            "files": stats.files_indexed,
            "chunks": stats.chunks,
            "index_ms": stats.duration_ms,
            "by_mode": {
                mode: {key: value for key, value in data.items() if key != "queries"}
                for mode, data in primary_modes.items()
            },
        }
        if task.ks:
            metrics["by_k"] = {
                str(k_value): {
                    mode: {key: value for key, value in data.items() if key != "queries"}
                    for mode, data in modes.items()
                }
                for k_value, modes in by_k.items()
            }
        metrics["queries"] = primary["queries"]
        return TaskResult(
            task_id=task.id,
            kind=task.kind,
            passed=passed,
            reason="" if passed else (
                f"recall {aggregate['recall']:.3f} < min_recall {task.min_recall:.3f}"
            ),
            metrics=metrics,
        )
    finally:
        shutil.rmtree(db_dir, ignore_errors=True)


def run_memory_retrieval_task(task: EvalTask, options: EvalOptions) -> TaskResult:
    if not task.facts or not task.queries:
        return TaskResult(task.id, task.kind, False, reason="facts and queries are required")
    db_dir = _make_work_dir(options.work_root, f"{task.id}-db")
    store = MemoryStore(
        db_dir / "memory.db",
        workspace=f"eval:{task.id}",
        embedder=HashingEmbedder(),
    )
    try:
        for fact in task.facts:
            store.add(
                str(fact.get("content", "")),
                kind=str(fact.get("kind", "fact")),
                source=str(fact.get("source", "eval")),
                confidence=float(fact.get("confidence", 0.6)),
            )
        rows: list[dict[str, float]] = []
        details: list[dict[str, Any]] = []
        for query in task.queries:
            text = str(query.get("query", ""))
            gold = query.get("gold", [])
            if isinstance(gold, str):
                gold = [gold]
            hits = store.search(text, k=task.k)
            metrics = evaluate_ranking(
                [str(item) for item in gold],
                hits,
                k=task.k,
                matcher=gold_matches_memory,
            )
            rows.append(metrics)
            details.append(
                {
                    "query": text,
                    "gold": gold,
                    "retrieved": [hit.record.content for hit in hits],
                    **metrics,
                }
            )
        aggregate = mean_metrics(rows)
        return TaskResult(
            task_id=task.id,
            kind=task.kind,
            passed=aggregate["recall"] >= task.min_recall,
            reason="" if aggregate["recall"] >= task.min_recall else (
                f"recall {aggregate['recall']:.3f} < min_recall {task.min_recall:.3f}"
            ),
            metrics={
                **aggregate,
                "k": task.k,
                "min_recall": task.min_recall,
                "facts": len(task.facts),
                "queries": details,
            },
        )
    finally:
        store.clear()
        shutil.rmtree(db_dir, ignore_errors=True)


def run_live_task(task: EvalTask, options: EvalOptions, repeats: int) -> TaskResult:
    if not options.live or not os.getenv("CASTORCODER_EVAL_LIVE"):
        return TaskResult(
            task.id,
            task.kind,
            False,
            skipped=True,
            reason="live evaluation disabled (pass --live and set CASTORCODER_EVAL_LIVE=1)",
        )
    from ..config import Config

    config = Config.from_env()
    if not config.api_key and config.provider == "openai":
        return TaskResult(
            task.id,
            task.kind,
            False,
            skipped=True,
            reason="no API key configured",
        )
    if task.fixture_path is None or not task.fixture_path.is_dir():
        return TaskResult(task.id, task.kind, False, reason="fixture is missing")

    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for _ in range(max(1, repeats)):
        work = _make_work_dir(options.work_root, task.id)
        try:
            destination = work / "repo"
            shutil.copytree(task.fixture_path, destination)
            workspace = Workspace(destination)
            index_store = None
            if task.use_index:
                db_dir = work / "db"
                db_dir.mkdir()
                index_store = CodeIndex(
                    workspace,
                    db_path=db_dir / "index.db",
                    embedder=HashingEmbedder(),
                )
                index_store.update(force=True)
            llm = _configured_llm(config)
            agent = Agent(
                llm=llm,
                workspace=workspace,
                max_rounds=task.max_rounds,
                max_cost_usd=task.max_cost_usd,
                permission_mode=task.permission_mode,
                code_index=index_store,
            )
            result = agent.run(task.prompt)
            verify_ok, verify_output = (True, "")
            if task.verify:
                _apply_verifier(task, destination)
                verify_ok, verify_output = _run_verify(task.verify, destination)
            pairing_ok, pairing_reason = check_tool_pairing(agent.messages)
            passed = verify_ok and pairing_ok
            if not verify_ok:
                failures.append(f"verify failed: {verify_output[-500:]}")
            if not pairing_ok:
                failures.append(pairing_reason)
            rows.append(
                {
                    "passed": passed,
                    "rounds": result.stats.rounds,
                    "tool_calls": result.stats.tool_calls,
                    "tool_failures": result.stats.tool_failures,
                    "elapsed_ms": result.stats.elapsed_ms,
                    "prompt_tokens": llm.total_prompt_tokens,
                    "completion_tokens": llm.total_completion_tokens,
                    "cost_usd": llm.reported_cost or llm.estimated_cost,
                    "stop_reason": result.stats.stop_reason,
                    "verify_ok": verify_ok,
                    "pairing_ok": pairing_ok,
                    "output": result.output[:1_000],
                }
            )
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return TaskResult(
        task_id=task.id,
        kind=task.kind,
        passed=bool(rows) and all(row["passed"] for row in rows),
        repeats=max(1, repeats),
        reason="; ".join(dict.fromkeys(failures)),
        metrics=_aggregate(rows),
    )


_GENERATION_SYSTEM_PROMPT = """\
You are completing one self-contained programming benchmark problem.

The workspace contains `solution.py` with a function signature, docstring, and
a `pass` body. Implement the function correctly.

Rules:
1. Make a single `write_file` call containing the complete `solution.py`.
2. Preserve the existing imports, function name and signature exactly.
3. Do not read or modify any other file and do not run shell commands.
4. After writing the file, reply DONE and stop.
"""


def run_generation_task(task: EvalTask, options: EvalOptions, repeats: int) -> TaskResult:
    """Run a public generation benchmark such as HumanEval or MBPP.

    Every instance gets a fresh workspace, a hidden test file applied after the
    agent stops, and one independent attempt per repeat.  The task reports a
    measured pass@1 instead of gating on it (``benchmark=True``).
    """

    if not options.live or not os.getenv("CASTORCODER_EVAL_LIVE"):
        return TaskResult(
            task.id,
            task.kind,
            False,
            skipped=True,
            reason="live evaluation disabled (pass --live and set CASTORCODER_EVAL_LIVE=1)",
        )
    from ..config import Config

    if task.dataset_file is None or not task.dataset_file.is_file():
        return TaskResult(
            task.id,
            task.kind,
            False,
            skipped=True,
            reason=(
                f"dataset not found: {task.dataset_file}; "
                "run evals/scripts/fetch_datasets.py"
            ),
        )
    config = Config.from_env()
    if options.llm_factory is None and not config.api_key and config.provider == "openai":
        return TaskResult(
            task.id,
            task.kind,
            False,
            skipped=True,
            reason="no API key configured",
        )
    try:
        instances = load_instances(
            task.dataset,
            task.dataset_file,
            limit=task.limit,
            offset=task.offset,
            sample=task.sample,
            seed=task.seed,
        )
    except Exception as exc:
        return TaskResult(
            task.id,
            task.kind,
            False,
            error=f"{type(exc).__name__}: {exc}",
        )
    if options.ids:
        instances = [instance for instance in instances if instance.id in options.ids]
    if not instances:
        return TaskResult(task.id, task.kind, False, reason="dataset slice is empty")

    repeats = max(1, repeats)
    attempts = [
        (instance, repeat_index)
        for repeat_index in range(repeats)
        for instance in instances
    ]
    checkpoint = _checkpoint_path(options, task.id)
    attempt_keys = {(instance.id, repeat_index) for instance, repeat_index in attempts}
    completed = (
        {
            key: row
            for key, row in _load_checkpoint(checkpoint).items()
            # ignore rows from another dataset slice, and retry transport
            # failures, which are exactly what a resume should retry
            if key in attempt_keys and row.get("failure") != "runtime_error"
        }
        if options.resume
        else {}
    )
    if not completed and checkpoint.is_file():
        checkpoint.unlink()  # a fresh run must not inherit stale partial rows
    if options.resume and completed:
        print(
            f"[{task.id}] resuming: {len(completed)}/{len(attempts)} attempts already recorded",
            flush=True,
        )
    pending = [
        (instance, repeat_index)
        for instance, repeat_index in attempts
        if (instance.id, repeat_index) not in completed
    ]
    latest: dict[tuple[str, int], dict[str, Any]] = dict(completed)
    lock = threading.Lock()
    done_count = [len(completed)]

    def record(row: dict[str, Any]) -> None:
        with lock:
            latest[(row["id"], row["attempt"])] = row
            done_count[0] += 1
            _append_checkpoint(checkpoint, row)
            completed_now = done_count[0]
        if len(attempts) >= 20 and (
            completed_now % 10 == 0 or completed_now == len(attempts)
        ):
            print(
                f"[{task.id}] {completed_now}/{len(attempts)} attempts",
                flush=True,
            )

    _run_generation_batch(task, pending, config, options, on_row=record)
    for _ in range(max(0, options.infra_retries)):
        broken = {
            (row["id"], row["attempt"])
            for row in latest.values()
            if row.get("failure") == "runtime_error"
        }
        if not broken:
            break
        retry_items = [
            (instance, repeat_index)
            for instance, repeat_index in attempts
            if (instance.id, repeat_index) in broken
        ]
        replacements = {
            (row["id"], row["attempt"]): row
            for row in _run_generation_batch(
                task, retry_items, config, options, on_row=None
            )
        }
        for key, replacement in replacements.items():
            previous = latest.get(key)
            if previous is not None:
                replacement["infra_retries"] = previous.get("infra_retries", 0) + 1
            latest[key] = replacement
    rows = sorted(latest.values(), key=lambda row: (row["id"], row["attempt"]))
    _write_checkpoint(checkpoint, rows)

    metrics = _generation_metrics(rows, len(instances), repeats)
    passed = metrics["pass_at_1"] >= task.min_pass
    reason = "" if passed else (
        f"pass@1 {metrics['pass_at_1']:.3f} < min_pass {task.min_pass:.3f}"
    )
    return TaskResult(
        task_id=task.id,
        kind=task.kind,
        passed=passed,
        benchmark=True,
        repeats=repeats,
        reason=reason,
        metrics=metrics,
    )


def _run_generation_batch(
    task: EvalTask,
    items: list[tuple[GenerationInstance, int]],
    config: Any,
    options: EvalOptions,
    on_row: Callable[[dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    if not items:
        return []
    jobs = max(1, options.jobs)

    def one(item: tuple[GenerationInstance, int]) -> dict[str, Any]:
        instance, repeat_index = item
        row = _run_generation_attempt(task, instance, config, options, repeat_index)
        if on_row is not None:
            on_row(row)
        return row

    if jobs == 1:
        return [one(item) for item in items]
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        return list(pool.map(one, items))


def _checkpoint_path(options: EvalOptions, task_id: str) -> Path:
    return options.checkpoint_dir / f"{_safe_name(task_id)}.jsonl"


def _load_checkpoint(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    """Return the last recorded row per (instance id, attempt)."""
    rows: dict[tuple[str, int], dict[str, Any]] = {}
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            rows[(str(row["id"]), int(row["attempt"]))] = row
        except (ValueError, KeyError, TypeError):
            continue  # a torn final line after a hard kill is expected
    return rows


def _append_checkpoint(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        handle.flush()


def _write_checkpoint(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(json.dumps(row, ensure_ascii=False) for row in rows)
    path.write_text(body + ("\n" if body else ""), encoding="utf-8")


def _run_generation_attempt(
    task: EvalTask,
    instance: GenerationInstance,
    config: Any,
    options: EvalOptions,
    attempt_index: int,
) -> dict[str, Any]:
    started = time.perf_counter()
    row: dict[str, Any] = {
        "id": instance.id,
        "entry_point": instance.entry_point,
        "attempt": attempt_index,
        "passed": False,
        "rounds": 0,
        "tool_calls": 0,
        "tool_failures": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_usd": None,
        "elapsed_ms": 0.0,
        "stop_reason": "",
        "finish_reason": "",
        "failure": "runtime_error",
        "infra_retries": 0,
        "error": "",
    }
    work = _make_work_dir(options.work_root, _safe_name(instance.id))
    try:
        destination = work / "repo"
        destination.mkdir()
        solution = destination / "solution.py"
        solution.write_text(instance.solution_stub, encoding="utf-8")
        workspace = Workspace(destination)
        llm = (
            options.llm_factory(instance)
            if options.llm_factory is not None
            else _configured_llm(config)
        )
        tools = [
            tool
            for tool in create_tools(workspace)
            if tool.name in {"read_file", "write_file"}
        ]
        agent = Agent(
            llm=llm,
            tools=tools,
            workspace=workspace,
            max_rounds=task.max_rounds,
            max_cost_usd=task.max_cost_usd,
            permission_mode="workspace",
            system_prompt_override=_GENERATION_SYSTEM_PROMPT,
        )
        result = agent.run(
            f"{instance.user_prompt}\n\n"
            "Current `solution.py`:\n"
            f"```python\n{instance.solution_stub}```\n\n"
            "Return the complete corrected `solution.py` with `write_file`."
        )

        after = (
            solution.read_text(encoding="utf-8", errors="replace")
            if solution.is_file()
            else ""
        )
        changed = after.strip() != instance.solution_stub.strip()
        (destination / "test_solution.py").write_text(instance.tests, encoding="utf-8")
        verify_ok, output = _run_script_test(
            destination / "test_solution.py", destination
        )
        pairing_ok, pairing_reason = check_tool_pairing(agent.messages)
        passed = verify_ok and pairing_ok
        finish_reason = getattr(llm, "last_finish_reason", "") or ""
        failure = "" if passed else _classify_failure(
            changed=changed,
            verify_ok=verify_ok,
            output=output,
            stop_reason=result.stats.stop_reason,
            finish_reason=finish_reason,
        )
        row.update(
            {
                "passed": passed,
                "rounds": result.stats.rounds,
                "tool_calls": result.stats.tool_calls,
                "tool_failures": result.stats.tool_failures,
                "prompt_tokens": getattr(llm, "total_prompt_tokens", 0),
                "completion_tokens": getattr(llm, "total_completion_tokens", 0),
                "cost_usd": getattr(llm, "reported_cost", None)
                or getattr(llm, "estimated_cost", None),
                "stop_reason": result.stats.stop_reason,
                "finish_reason": finish_reason,
                "failure": failure,
                "error": "" if passed else (pairing_reason or output[-400:]),
            }
        )
    except Exception as exc:  # one bad instance must not abort the benchmark
        row["error"] = f"{type(exc).__name__}: {exc}"[:400]
        row["failure"] = "runtime_error"
    finally:
        row["elapsed_ms"] = (time.perf_counter() - started) * 1000
        shutil.rmtree(work, ignore_errors=True)
    return row


def _classify_failure(
    *,
    changed: bool,
    verify_ok: bool,
    output: str,
    stop_reason: str,
    finish_reason: str = "",
) -> str:
    if not changed:
        # A completion that hit max_tokens never got the chance to emit its
        # tool call; counting that as "model declined to edit" would blame the
        # model for a harness budget.
        return "truncated" if finish_reason == "length" else "no_edit"
    if verify_ok:
        return ""
    if "TimeoutError" in output:
        return "timeout"
    if stop_reason == "max_rounds":
        return "max_rounds"
    markers = (
        "SyntaxError",
        "IndentationError",
        "ModuleNotFoundError",
        "ImportError",
        "NameError",
        "AttributeError",
        "TypeError",
        "IndexError",
        "KeyError",
        "ValueError",
        "RecursionError",
        "AssertionError",
    )
    found = [(output.find(marker), marker) for marker in markers if marker in output]
    if found:
        return min(found)[1]
    return "test_failure"


def _generation_metrics(
    rows: list[dict[str, Any]],
    instance_count: int,
    repeats: int,
) -> dict[str, Any]:
    attempts = len(rows)
    passes = sum(1 for row in rows if row["passed"])
    pass_at_1 = (passes / attempts) if attempts else 0.0
    low, high = wilson_interval(passes, attempts)
    failures = Counter(
        row["failure"] for row in rows if row.get("failure")
    )
    elapsed = [row["elapsed_ms"] for row in rows]
    rounds = [row["rounds"] for row in rows]
    tool_calls = [row["tool_calls"] for row in rows]
    prompt_tokens = [row["prompt_tokens"] for row in rows]
    completion_tokens = [row["completion_tokens"] for row in rows]
    costs = [row["cost_usd"] for row in rows if row["cost_usd"] is not None]
    total_tokens = sum(prompt_tokens) + sum(completion_tokens)
    infra_errors = sum(1 for row in rows if row.get("failure") == "runtime_error")
    valid_attempts = attempts - infra_errors
    return {
        "attempts": attempts,
        "passes": passes,
        "pass_at_1": pass_at_1,
        "pass_at_1_ci95": [low, high],
        "pass_at_1_excluding_infra": (
            passes / valid_attempts if valid_attempts else 0.0
        ),
        "infra_errors": infra_errors,
        "infra_retried": sum(1 for row in rows if row.get("infra_retries")),
        "pass_rate": pass_at_1,
        "instances": instance_count,
        "repeats": repeats,
        "mean_rounds": _mean(rounds),
        "rounds_std": _std(rounds),
        "mean_tool_calls": _mean(tool_calls),
        "mean_prompt_tokens": _mean(prompt_tokens),
        "mean_completion_tokens": _mean(completion_tokens),
        "total_tokens": total_tokens,
        "total_cost_usd": sum(costs) if costs else None,
        "mean_cost_usd": _mean(costs) if costs else None,
        "mean_elapsed_ms": _mean(elapsed),
        "median_elapsed_ms": median(elapsed) if elapsed else 0.0,
        "p95_elapsed_ms": percentile(elapsed, 0.95),
        "by_failure": dict(sorted(failures.items())),
        "results": rows,
    }


def _configured_llm(config: Any) -> LLM:
    from ..llm import LiteLLM

    llm_cls = LiteLLM if config.provider == "litellm" else LLM
    return llm_cls(
        model=config.model,
        api_key=config.api_key or None,
        base_url=config.base_url,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout=120,
    )


def _run_script_test(script: Path, cwd: Path, timeout: float = 60.0) -> tuple[bool, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        process = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        if isinstance(output, bytes):  # pragma: no cover - text=True path
            output = output.decode("utf-8", "replace")
        return False, f"TimeoutError after {timeout:.0f}s\n{output[-2_000:]}"
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    output = (process.stdout or "") + (process.stderr or "")
    return process.returncode == 0, output[-3_000:]


def _safe_name(value: str) -> str:
    return "".join(character if character.isalnum() or character in "._-" else "-" for character in value)


def _mean(values: list[float]) -> float:
    return (sum(values) / len(values)) if values else 0.0


def _std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = _mean(values)
    return (sum((value - mean) ** 2 for value in values) / len(values)) ** 0.5


def check_tool_pairing(messages: list[dict]) -> tuple[bool, str]:
    pending: set[str] = set()
    orphans: list[str] = []
    for message in messages:
        role = message.get("role")
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                call_id = call.get("id")
                if call_id:
                    pending.add(str(call_id))
        elif role == "tool":
            call_id = str(message.get("tool_call_id", ""))
            if call_id in pending:
                pending.remove(call_id)
            else:
                orphans.append(call_id)
    if pending:
        return False, f"unanswered tool calls: {sorted(pending)}"
    if orphans:
        return False, f"orphan tool results: {orphans}"
    return True, ""


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Average numeric fields; expose booleans as rates.

    ``pass_rate`` over repeats is the standard pass@1 estimate for a live task:
    the fraction of independent attempts that satisfy the verifier.
    """

    if not rows:
        return {}
    result: dict[str, Any] = {"repeats": len(rows)}
    keys = list(dict.fromkeys(key for row in rows for key in row))
    for key in keys:
        values = [row.get(key) for row in rows]
        present = [value for value in values if value is not None]
        if present and all(isinstance(value, bool) for value in present):
            label = "pass_rate" if key == "passed" else f"{key}_rate"
            result[label] = sum(1 for value in present if value) / len(present)
        elif present and all(isinstance(value, (int, float)) for value in present):
            mean = sum(present) / len(present)
            result[key] = mean
            if len(present) > 1:
                result[f"{key}_std"] = (
                    sum((value - mean) ** 2 for value in present) / len(present)
                ) ** 0.5
        else:
            result[key] = present[0] if present else None
    reasons = sorted(
        {row["stop_reason"] for row in rows if row.get("stop_reason") is not None}
    )
    if reasons:
        result["stop_reasons"] = reasons
    return result


def _normalize_gold(gold: str, level: str) -> str:
    """Reduce symbol/line labels to a file path for file-level evaluation."""

    if level != "file":
        return gold
    return gold.split("#", 1)[0].rsplit(":", 1)[0]


def _apply_verifier(task: EvalTask, destination: Path) -> None:
    """Overwrite grading files with the hidden copy after the agent has run.

    The verifier lives outside the fixture, so editing tests inside the
    workspace cannot make a failing solution pass.
    """

    if task.verifier_path is None:
        return
    if not task.verifier_path.is_dir():
        raise FileNotFoundError(f"verifier directory missing: {task.verifier_path}")
    for source in task.verifier_path.rglob("*"):
        if not source.is_file():
            continue
        relative = source.relative_to(task.verifier_path)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def _make_work_dir(root: Path, task_id: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{task_id}-", dir=root))


def _run_verify(command: str, cwd: Path) -> tuple[bool, str]:
    try:
        parts = [part.strip("\"'") for part in shlex.split(command, posix=False)]
    except ValueError as exc:
        return False, f"invalid verify command: {exc}"
    if not parts:
        return False, "empty verify command"
    if parts[0].lower() in {"python", "python3"}:
        parts[0] = sys.executable
    elif parts[0].lower() in {"pytest"}:
        parts = [sys.executable, "-m", "pytest", *parts[1:]]
    try:
        process = subprocess.run(
            parts,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    output = (process.stdout or "") + (process.stderr or "")
    return process.returncode == 0, output[-3_000:]


def result_to_json(result: TaskResult) -> str:
    return json.dumps(result.as_dict(), ensure_ascii=False, indent=2)
