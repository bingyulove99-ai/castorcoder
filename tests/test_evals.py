"""Local evaluation harness, metrics and report tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from castorcoder.evals.__main__ import main as eval_main
from castorcoder.evals.datasets import load_instances
from castorcoder.evals.metrics import (
    evaluate_ranking,
    gold_matches_code,
    mean_metrics,
    percentile,
    wilson_interval,
)
from castorcoder.evals.models import (
    EvalSuite,
    EvalTask,
    ScriptedStep,
    TaskResult,
    ToolCallStep,
    load_suite,
)
from castorcoder.evals.report import render_markdown, summarize, write_report
from castorcoder.evals.runner import (
    EvalOptions,
    ScriptedLLM,
    _aggregate,
    _apply_verifier,
    _classify_failure,
    _load_checkpoint,
    _normalize_gold,
    check_tool_pairing,
    run_generation_task,
    run_live_task,
    run_suite,
)
from castorcoder.index.chunker import Chunk
from castorcoder.index.retriever import SearchHit
from castorcoder.ranking import rrf_fuse

ROOT = Path(__file__).resolve().parents[1]


def _hit(path: str, symbol: str, line: int = 1) -> SearchHit:
    chunk = Chunk(
        path=path,
        symbol=symbol,
        kind="function",
        start_line=line,
        end_line=line + 5,
        content=f"def {symbol}(): ...",
    )
    return SearchHit(chunk=chunk, score=1.0, sources=("bm25",))


def test_ranking_metrics_and_code_gold_matching():
    hits = [_hit("wrong.py", "other"), _hit("right.py", "target", line=10)]
    metrics = evaluate_ranking(
        ["right.py#target"],
        hits,
        k=2,
        matcher=gold_matches_code,
    )
    assert metrics["recall"] == 1.0
    assert metrics["mrr"] == 0.5
    assert 0.0 < metrics["ndcg"] < 1.0

    line_metrics = evaluate_ranking(
        ["right.py:12"],
        hits,
        k=2,
        matcher=gold_matches_code,
    )
    assert line_metrics["recall"] == 1.0


def test_tool_pairing_invariant():
    ok = [
        {"role": "assistant", "content": None, "tool_calls": [{"id": "a"}]},
        {"role": "tool", "tool_call_id": "a", "content": "result"},
    ]
    assert check_tool_pairing(ok) == (True, "")
    missing = [{"role": "assistant", "content": None, "tool_calls": [{"id": "a"}]}]
    assert not check_tool_pairing(missing)[0]
    orphan = [{"role": "tool", "tool_call_id": "x", "content": "result"}]
    assert not check_tool_pairing(orphan)[0]


def test_suite_loads_tasks(tmp_path):
    suite = load_suite(ROOT / "evals" / "suites" / "offline.toml")
    ids = {task.id for task in suite.tasks}
    assert {"edit-rename", "bugfix-average", "retrieval-sample"} <= ids
    retrieval = next(task for task in suite.tasks if task.id == "retrieval-sample")
    assert retrieval.fixture_path and retrieval.fixture_path.is_dir()
    assert retrieval.queries


def test_offline_suite_passes(tmp_path):
    suite = load_suite(ROOT / "evals" / "suites" / "offline.toml")
    results = run_suite(
        suite,
        EvalOptions(work_root=tmp_path / "work", repeats=1, verbose=False),
    )
    assert len(results) == len(suite.tasks)
    failed = [result for result in results if not result.passed and not result.skipped]
    assert failed == [], [result.as_dict() for result in failed]
    retrieval = next(result for result in results if result.task_id == "retrieval-sample")
    assert retrieval.metrics["recall"] >= retrieval.metrics["min_recall"]


def test_shared_queries_file_and_verifier_root():
    suite = load_suite(ROOT / "evals" / "suites" / "retrieval_repo.toml")
    file_task, symbol_task = suite.tasks
    assert suite.fixture_root == ROOT
    assert file_task.fixture_path == ROOT
    assert suite.verifier_root and suite.verifier_root.name == "verifiers"
    assert len(file_task.queries) == 35
    assert file_task.gold_level == "file"
    assert symbol_task.gold_level == "symbol"
    assert file_task.exclude and "evals/suites/" in file_task.exclude
    assert _normalize_gold("pkg/a.py#Thing.run", "file") == "pkg/a.py"
    assert _normalize_gold("pkg/a.py#Thing.run", "symbol") == "pkg/a.py#Thing.run"


def test_verifier_overwrites_graded_files(tmp_path):
    verifier = tmp_path / "verifier"
    (verifier / "tests").mkdir(parents=True)
    (verifier / "tests" / "test_hidden.py").write_text("def test_x():\n    assert True\n")
    destination = tmp_path / "repo"
    (destination / "tests").mkdir(parents=True)
    (destination / "tests" / "test_hidden.py").write_text("def test_x():\n    assert False\n")
    task = EvalTask(
        id="v",
        verifier_path=verifier,
    )
    _apply_verifier(task, destination)
    assert "assert True" in (destination / "tests" / "test_hidden.py").read_text()


def test_aggregate_reports_pass_rate_and_averages():
    rows = [
        {"passed": True, "rounds": 2, "cost_usd": None, "stop_reason": "completed"},
        {"passed": False, "rounds": 4, "cost_usd": None, "stop_reason": "completed"},
    ]
    result = _aggregate(rows)
    assert result["pass_rate"] == 0.5
    assert result["rounds"] == 3.0
    assert result["cost_usd"] is None
    assert result["repeats"] == 2
    assert result["stop_reasons"] == ["completed"]


def test_rrf_supports_channel_weights():
    equal = rrf_fuse([["a"], ["b"]])
    assert equal["a"] == equal["b"]
    weighted = rrf_fuse([["a"], ["b"]], weights=[1.0, 0.25])
    assert weighted["a"] > weighted["b"]


def test_report_writes_json_and_markdown(tmp_path):
    suite = load_suite(ROOT / "evals" / "suites" / "offline.toml")
    results = run_suite(
        suite,
        EvalOptions(
            work_root=tmp_path / "work",
            only={"edit-rename"},
        ),
    )
    json_path, markdown_path = write_report(results, suite.name, tmp_path / "report.json")
    assert json_path.is_file() and markdown_path.is_file()
    assert "edit-rename" in markdown_path.read_text(encoding="utf-8")


def test_empty_gold_and_unknown_kind():
    assert evaluate_ranking([], [_hit("a.py", "a")], k=1, matcher=gold_matches_code) == {
        "recall": 0.0,
        "mrr": 0.0,
        "ndcg": 0.0,
    }
    assert mean_metrics([]) == {"recall": 0.0, "mrr": 0.0, "ndcg": 0.0}

    suite = EvalSuite(
        name="bad",
        fixture_root=ROOT,
        tasks=[EvalTask(id="weird", kind="unknown")],
        path=ROOT / "evals" / "suites" / "offline.toml",
    )
    results = run_suite(suite, EvalOptions(work_root=ROOT / ".tmp" / "evals-test"))
    assert len(results) == 1 and not results[0].passed
    assert "unknown task kind" in results[0].reason


def test_summary_reports_attempt_level_pass_at_1():
    results = [
        TaskResult(
            task_id="a",
            kind="live",
            passed=False,
            repeats=3,
            metrics={"pass_rate": 2 / 3},
        ),
        TaskResult(
            task_id="b",
            kind="scripted",
            passed=True,
            repeats=1,
            metrics={"pass_rate": 1.0},
        ),
    ]
    summary = summarize(results)
    assert summary["attempts"] == 4
    assert summary["pass_at_1"] == 3 / 4
    assert summary["pass_rate"] == 0.5


def test_live_task_is_skipped_without_opt_in(tmp_path):
    task = EvalTask(id="live", kind="live", prompt="hello")
    result = run_live_task(task, EvalOptions(work_root=tmp_path), repeats=1)
    assert result.skipped
    assert "disabled" in result.reason


def test_eval_cli_lists_runs_and_reports(tmp_path, capsys):
    suite = str(ROOT / "evals" / "suites" / "offline.toml")
    assert eval_main(["--suite", suite, "--list"]) == 0
    assert "edit-rename" in capsys.readouterr().out

    assert eval_main(["--suite", str(tmp_path / "missing.toml")]) == 2
    assert "not found" in capsys.readouterr().out

    report = tmp_path / "report.json"
    code = eval_main([
        "--suite", suite,
        "--only", "edit-rename",
        "--work-root", str(tmp_path / "work"),
        "--report", str(report),
    ])
    assert code == 0
    assert report.is_file()
    assert capsys.readouterr().out


def test_markdown_report_escapes_errors():
    results = [
        TaskResult(
            task_id="broken",
            kind="scripted",
            passed=False,
            reason="line one|line two\nnext",
            error="boom",
        )
    ]
    markdown = render_markdown(
        results,
        "suite",
        {
            "tasks": 1,
            "executed": 1,
            "passed": 0,
            "failed": 1,
            "skipped": 0,
            "pass_rate": 0.0,
            "duration_ms": 10,
        },
    )
    assert "line one\\|line two next" in markdown
    assert "boom" in markdown


def _humaneval_file(tmp_path: Path, count: int = 2) -> Path:
    rows = [
        {
            "task_id": f"HumanEval/{index}",
            "entry_point": "answer",
            "prompt": 'def answer():\n    """Return the expected integer."""\n',
            "canonical_solution": "    return 1\n",
            "test": "def check(candidate):\n    assert candidate() == 1\n\n\ncheck(answer)\n",
        }
        for index in range(count)
    ]
    path = tmp_path / "HumanEval.jsonl"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def _mbpp_file(tmp_path: Path) -> Path:
    rows = [
        {
            "task_id": 1,
            "prompt": "Add two numbers.",
            "code": "def add(a, b):\n    return a + b\n",
            "test_imports": [],
            "test_list": ["assert add(1, 2) == 3"],
        },
        {
            "task_id": 2,
            "prompt": "Sum an array.",
            "code": "def _sum(items):\n    return sum(items)\n",
            "test_imports": [],
            "test_list": ["assert _sum([1, 2]) == 3"],
        },
    ]
    path = tmp_path / "mbpp.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    return path


def _scripted_factory(body: str):
    def factory(instance):
        content = instance.solution_stub.replace("    pass\n", f"    {body}\n")
        return ScriptedLLM(
            [
                ScriptedStep(
                    tool_calls=(
                        ToolCallStep(
                            "write_file",
                            {"file_path": "solution.py", "content": content},
                        ),
                    )
                ),
                ScriptedStep(content="done"),
            ]
        )

    return factory


def test_dataset_adapters_build_stub_and_hidden_tests(tmp_path):
    humaneval = load_instances("humaneval", _humaneval_file(tmp_path), limit=1)[0]
    assert humaneval.id == "HumanEval/0"
    assert humaneval.solution_stub.endswith("    pass\n")
    assert "from solution import answer" in humaneval.tests
    assert "check(answer)" in humaneval.tests

    mbpp = load_instances("mbpp", _mbpp_file(tmp_path))[0]
    assert mbpp.entry_point == "add"
    assert "def add(a, b):" in mbpp.solution_stub
    assert "assert add(1, 2) == 3" in mbpp.tests
    assert "assert" not in mbpp.solution_stub
    assert mbpp.metadata["canonical"].startswith("def add")

    private = load_instances("mbpp", _mbpp_file(tmp_path))[1]
    assert private.entry_point == "_sum"
    assert "from solution import _sum" in private.tests


def test_dataset_sampling_is_deterministic(tmp_path):
    path = _humaneval_file(tmp_path, count=5)
    first = [item.id for item in load_instances("humaneval", path, sample=3, seed=7)]
    second = [item.id for item in load_instances("humaneval", path, sample=3, seed=7)]
    assert first == second and len(first) == 3
    assert load_instances("humaneval", path, limit=2)[0].id == "HumanEval/0"


def test_generation_task_scores_pass_at_1(tmp_path, monkeypatch):
    monkeypatch.setenv("CASTORCODER_EVAL_LIVE", "1")
    task = EvalTask(
        id="gen",
        kind="generation",
        dataset="humaneval",
        dataset_file=_humaneval_file(tmp_path),
        max_rounds=4,
    )
    result = run_generation_task(
        task,
        EvalOptions(
            work_root=tmp_path / "work",
            live=True,
            llm_factory=_scripted_factory("return 1"),
        ),
        1,
    )
    assert result.benchmark and result.passed
    assert result.metrics["attempts"] == 2
    assert result.metrics["pass_at_1"] == 1.0
    assert result.metrics["by_failure"] == {}


def test_generation_task_reports_failure_taxonomy(tmp_path, monkeypatch):
    monkeypatch.setenv("CASTORCODER_EVAL_LIVE", "1")
    task = EvalTask(
        id="gen",
        kind="generation",
        dataset="humaneval",
        dataset_file=_humaneval_file(tmp_path, count=1),
        max_rounds=4,
    )
    result = run_generation_task(
        task,
        EvalOptions(
            work_root=tmp_path / "work",
            live=True,
            llm_factory=_scripted_factory("return 0"),
        ),
        1,
    )
    assert result.metrics["pass_at_1"] == 0.0
    assert result.metrics["by_failure"] == {"AssertionError": 1}
    assert result.metrics["pass_at_1_ci95"][0] == 0.0


def test_generation_task_parallel_and_missing_dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("CASTORCODER_EVAL_LIVE", "1")
    task = EvalTask(
        id="gen",
        kind="generation",
        dataset="humaneval",
        dataset_file=_humaneval_file(tmp_path),
        max_rounds=4,
    )
    result = run_generation_task(
        task,
        EvalOptions(
            work_root=tmp_path / "work",
            live=True,
            jobs=2,
            llm_factory=_scripted_factory("return 1"),
        ),
        1,
    )
    assert result.metrics["pass_at_1"] == 1.0

    missing = EvalTask(
        id="gen",
        kind="generation",
        dataset="humaneval",
        dataset_file=tmp_path / "absent.jsonl",
    )
    skipped = run_generation_task(
        missing,
        EvalOptions(work_root=tmp_path / "work", live=True),
        1,
    )
    assert skipped.skipped and "dataset not found" in skipped.reason


def test_generation_task_retries_infrastructure_errors(tmp_path, monkeypatch):
    monkeypatch.setenv("CASTORCODER_EVAL_LIVE", "1")
    task = EvalTask(
        id="gen",
        kind="generation",
        dataset="humaneval",
        dataset_file=_humaneval_file(tmp_path, count=1),
        max_rounds=4,
    )
    calls: dict[str, int] = {}

    def factory(instance):
        calls[instance.id] = calls.get(instance.id, 0) + 1
        if calls[instance.id] == 1:
            raise ConnectionError("temporary gateway failure")
        return _scripted_factory("return 1")(instance)

    result = run_generation_task(
        task,
        EvalOptions(work_root=tmp_path / "work", live=True, llm_factory=factory),
        1,
    )
    assert result.metrics["pass_at_1"] == 1.0
    assert result.metrics["infra_errors"] == 0
    assert result.metrics["infra_retried"] == 1


def _checkpoint_row(instance_id: str, *, passed: bool, failure: str = "") -> dict:
    return {
        "id": instance_id,
        "entry_point": "answer",
        "attempt": 0,
        "passed": passed,
        "rounds": 2,
        "tool_calls": 1,
        "tool_failures": 0,
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "cost_usd": 0.001,
        "elapsed_ms": 1000.0,
        "stop_reason": "completed",
        "failure": failure,
        "infra_retries": 0,
        "error": "",
    }


def test_generation_task_resumes_from_checkpoint(tmp_path, monkeypatch):
    monkeypatch.setenv("CASTORCODER_EVAL_LIVE", "1")
    task = EvalTask(
        id="gen",
        kind="generation",
        dataset="humaneval",
        dataset_file=_humaneval_file(tmp_path),
        max_rounds=4,
    )
    checkpoint_dir = tmp_path / "checkpoints"
    checkpoint_dir.mkdir()
    (checkpoint_dir / "gen.jsonl").write_text(
        json.dumps(_checkpoint_row("HumanEval/0", passed=True)) + "\n",
        encoding="utf-8",
    )
    seen: list[str] = []

    def factory(instance):
        seen.append(instance.id)
        return _scripted_factory("return 1")(instance)

    result = run_generation_task(
        task,
        EvalOptions(
            work_root=tmp_path / "work",
            live=True,
            resume=True,
            checkpoint_dir=checkpoint_dir,
            llm_factory=factory,
        ),
        1,
    )
    assert seen == ["HumanEval/1"]
    assert result.metrics["attempts"] == 2
    assert result.metrics["pass_at_1"] == 1.0
    lines = (checkpoint_dir / "gen.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2  # the final checkpoint is rewritten, not appended twice


def test_generation_task_resume_retries_transport_failures(tmp_path, monkeypatch):
    monkeypatch.setenv("CASTORCODER_EVAL_LIVE", "1")
    task = EvalTask(
        id="gen",
        kind="generation",
        dataset="humaneval",
        dataset_file=_humaneval_file(tmp_path, count=1),
        max_rounds=4,
    )
    checkpoint_dir = tmp_path / "checkpoints"
    checkpoint_dir.mkdir()
    (checkpoint_dir / "gen.jsonl").write_text(
        json.dumps(
            _checkpoint_row("HumanEval/0", passed=False, failure="runtime_error")
        )
        + "\n",
        encoding="utf-8",
    )
    seen: list[str] = []

    def factory(instance):
        seen.append(instance.id)
        return _scripted_factory("return 1")(instance)

    result = run_generation_task(
        task,
        EvalOptions(
            work_root=tmp_path / "work",
            live=True,
            resume=True,
            checkpoint_dir=checkpoint_dir,
            llm_factory=factory,
        ),
        1,
    )
    assert seen == ["HumanEval/0"]
    assert result.metrics["pass_at_1"] == 1.0
    assert result.metrics["infra_errors"] == 0


def test_generation_task_filters_instance_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("CASTORCODER_EVAL_LIVE", "1")
    task = EvalTask(
        id="gen",
        kind="generation",
        dataset="humaneval",
        dataset_file=_humaneval_file(tmp_path),
        max_rounds=4,
    )
    seen: list[str] = []

    def factory(instance):
        seen.append(instance.id)
        return _scripted_factory("return 1")(instance)

    result = run_generation_task(
        task,
        EvalOptions(
            work_root=tmp_path / "work",
            live=True,
            ids={"HumanEval/1"},
            llm_factory=factory,
        ),
        1,
    )
    assert seen == ["HumanEval/1"]
    assert result.metrics["attempts"] == 1


def test_checkpoint_loader_ignores_torn_lines(tmp_path):
    path = tmp_path / "gen.jsonl"
    good = json.dumps(_checkpoint_row("HumanEval/0", passed=True))
    path.write_text(good + "\n{\"id\": \"HumanEval/1\"", encoding="utf-8")
    rows = _load_checkpoint(path)
    assert set(rows) == {("HumanEval/0", 0)}


def test_classify_failure_and_statistics():
    assert _classify_failure(
        changed=False, verify_ok=False, output="", stop_reason="completed"
    ) == "no_edit"
    assert _classify_failure(
        changed=False,
        verify_ok=False,
        output="",
        stop_reason="completed",
        finish_reason="length",
    ) == "truncated"
    assert _classify_failure(
        changed=True,
        verify_ok=False,
        output="Traceback\nAssertionError",
        stop_reason="completed",
    ) == "AssertionError"
    assert _classify_failure(
        changed=True, verify_ok=False, output="TimeoutError after 60s", stop_reason="completed"
    ) == "timeout"
    low, high = wilson_interval(8, 10)
    assert low < 0.8 < high
    assert wilson_interval(0, 0) == (0.0, 0.0)
    assert percentile([10, 20, 30], 0.5) == 20
    assert percentile([], 0.9) == 0.0


def test_summary_separates_benchmark_attempts():
    results = [
        TaskResult(task_id="gate", kind="scripted", passed=True),
        TaskResult(
            task_id="bench",
            kind="generation",
            passed=True,
            benchmark=True,
            metrics={"attempts": 10, "passes": 7, "pass_at_1": 0.7},
        ),
    ]
    summary = summarize(results)
    assert summary["benchmark_tasks"] == 1
    assert summary["passed"] == 1 and summary["failed"] == 0
    assert summary["task_pass_rate"] == 1.0
    assert summary["attempts"] == 11
    assert summary["pass_at_1"] == pytest.approx(8 / 11)
    markdown = render_markdown(results, "suite", summary)
    assert "MEASURED" in markdown
    benchmark_only = summarize([results[1]])
    assert "n/a (benchmark only)" in render_markdown(
        [results[1]], "suite", benchmark_only
    )


def test_retrieval_k_sweep(tmp_path):
    suite = load_suite(ROOT / "evals" / "suites" / "retrieval_repo_depth.toml")
    results = run_suite(
        suite,
        EvalOptions(
            work_root=tmp_path / "work",
            only={"repo-retrieval-file-k-sweep"},
        ),
    )
    metrics = results[0].metrics
    assert list(metrics["by_k"]) == ["1", "3", "5", "10", "20"]
    recalls = [metrics["by_k"][key]["bm25"]["recall"] for key in metrics["by_k"]]
    assert recalls == sorted(recalls)
