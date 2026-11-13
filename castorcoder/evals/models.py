"""Evaluation task and result models."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .datasets import DATASET_FILES


@dataclass(frozen=True)
class ToolCallStep:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScriptedStep:
    content: str = ""
    tool_calls: tuple[ToolCallStep, ...] = ()


@dataclass
class EvalTask:
    id: str
    kind: str = "scripted"
    prompt: str = ""
    fixture: str = ""
    verify: str | None = None
    verifier: str = ""
    steps: list[ScriptedStep] = field(default_factory=list)
    queries: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    k: int = 5
    modes: list[str] = field(default_factory=lambda: ["hybrid"])
    ks: list[int] = field(default_factory=list)
    gold_level: str = "symbol"
    exclude: list[str] = field(default_factory=list)
    queries_file: str = ""
    min_recall: float = 0.0
    dataset: str = ""
    dataset_path: str = ""
    limit: int = 0
    offset: int = 0
    sample: int = 0
    seed: int = 0
    min_pass: float = 0.0
    repeats: int = 1
    max_rounds: int = 20
    max_cost_usd: float | None = None
    permission_mode: str = "workspace"
    expect_stop_reason: str | None = None
    use_memory: bool = False
    use_index: bool = False
    tags: list[str] = field(default_factory=list)
    fixture_path: Path | None = None
    verifier_path: Path | None = None
    dataset_file: Path | None = None


@dataclass
class EvalSuite:
    name: str
    fixture_root: Path
    tasks: list[EvalTask]
    path: Path
    verifier_root: Path | None = None


@dataclass
class TaskResult:
    task_id: str
    kind: str
    passed: bool
    skipped: bool = False
    benchmark: bool = False
    reason: str = ""
    repeats: int = 1
    metrics: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_suite(path: str | Path) -> EvalSuite:
    suite_path = Path(path).expanduser().resolve()
    if not suite_path.is_file():
        raise FileNotFoundError(f"evaluation suite not found: {suite_path}")
    if suite_path.suffix.lower() == ".json":
        document = json.loads(suite_path.read_text(encoding="utf-8"))
    else:
        document = _load_toml(suite_path)

    section = document.get("suite", {})
    if not isinstance(section, dict):
        section = {}
    name = str(section.get("name") or suite_path.stem)
    fixture_root = _resolve_root(section.get("fixture_root", "evals/fixtures"), suite_path)
    verifier_root = _resolve_root(section.get("verifier_root", "evals/verifiers"), suite_path)

    tasks: list[EvalTask] = []
    for raw in document.get("tasks", []):
        if not isinstance(raw, dict) or not raw.get("id"):
            continue
        task = _task_from_dict(raw)
        if task.queries_file:
            task.queries = _load_queries(task.queries_file, suite_path)
        if task.fixture:
            task.fixture_path = (fixture_root / task.fixture).resolve()
        if task.verifier:
            task.verifier_path = (verifier_root / task.verifier).resolve()
        if task.dataset:
            raw_dataset = task.dataset_path or DATASET_FILES.get(task.dataset, "")
            candidate = Path(raw_dataset).expanduser()
            if not candidate.is_absolute():
                candidate = (suite_path.parent / candidate).resolve()
            task.dataset_file = candidate
        tasks.append(task)
    return EvalSuite(
        name=name,
        fixture_root=fixture_root,
        tasks=tasks,
        path=suite_path,
        verifier_root=verifier_root,
    )


def _load_queries(value: str, suite_path: Path) -> list[dict[str, Any]]:
    """Load shared ``[[queries]]`` from a file next to the suite or repo root."""

    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        options = [suite_path.parent / candidate, Path.cwd() / candidate]
        candidate = next((item for item in options if item.is_file()), options[0])
    document = _load_toml(candidate)
    return [
        dict(item)
        for item in document.get("queries", []) or []
        if isinstance(item, dict)
    ]


def _resolve_root(value: object, suite_path: Path) -> Path:
    root = Path(str(value)).expanduser()
    if root.is_absolute():
        return root.resolve()
    if root == Path("."):
        # A suite that indexes "this repository" should work from any cwd.
        for parent in suite_path.parents:
            if (parent / "pyproject.toml").is_file():
                return parent.resolve()
    candidates = [Path.cwd() / root, *(parent / root for parent in suite_path.parents[:3])]
    for item in candidates:
        if item.is_dir():
            return item.resolve()
    return candidates[0].resolve()


def _task_from_dict(raw: dict[str, Any]) -> EvalTask:
    steps: list[ScriptedStep] = []
    for raw_step in raw.get("steps", []) or []:
        if not isinstance(raw_step, dict):
            continue
        calls = tuple(
            ToolCallStep(
                name=str(call.get("name", "")),
                arguments=dict(call.get("arguments", {})),
            )
            for call in raw_step.get("tool_calls", []) or []
            if isinstance(call, dict) and call.get("name")
        )
        steps.append(
            ScriptedStep(content=str(raw_step.get("content", "")), tool_calls=calls)
        )

    return EvalTask(
        id=str(raw["id"]),
        kind=str(raw.get("kind", "scripted")),
        prompt=str(raw.get("prompt", "")),
        fixture=str(raw.get("fixture", "")),
        verify=raw.get("verify"),
        verifier=str(raw.get("verifier", "") or ""),
        steps=steps,
        queries=[dict(item) for item in raw.get("queries", []) or [] if isinstance(item, dict)],
        facts=[dict(item) for item in raw.get("facts", []) or [] if isinstance(item, dict)],
        k=int(raw.get("k", 5)),
        modes=[str(mode) for mode in raw.get("modes", []) or []] or ["hybrid"],
        ks=[int(value) for value in raw.get("ks", []) or []],
        gold_level=str(raw.get("gold_level", "symbol")),
        exclude=[str(item) for item in raw.get("exclude", []) or []],
        queries_file=str(raw.get("queries_file", "") or ""),
        min_recall=float(raw.get("min_recall", 0.0)),
        dataset=str(raw.get("dataset", "") or ""),
        dataset_path=str(raw.get("dataset_path", "") or ""),
        limit=int(raw.get("limit", 0)),
        offset=int(raw.get("offset", 0)),
        sample=int(raw.get("sample", 0)),
        seed=int(raw.get("seed", 0)),
        min_pass=float(raw.get("min_pass", 0.0)),
        repeats=int(raw.get("repeats", 1)),
        max_rounds=int(raw.get("max_rounds", 20)),
        max_cost_usd=(float(raw["max_cost_usd"]) if raw.get("max_cost_usd") is not None else None),
        permission_mode=str(raw.get("permission_mode", "workspace")),
        expect_stop_reason=raw.get("expect_stop_reason"),
        use_memory=bool(raw.get("use_memory", False)),
        use_index=bool(raw.get("use_index", False)),
        tags=[str(tag) for tag in raw.get("tags", []) or []],
    )


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        import tomllib
    except ImportError:  # Python 3.10
        import tomli as tomllib
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    return data if isinstance(data, dict) else {}
