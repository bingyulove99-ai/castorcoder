"""Adapters for mainstream public code benchmarks.

Two execution-graded datasets are supported:

* ``humaneval`` -- OpenAI HumanEval (164 function-synthesis problems).  The
  agent sees the signature and docstring; grading tests stay hidden.
* ``mbpp`` -- MBPP sanitized (427 short programs).  The agent sees a docstring
  and the expected signature; grading assertions stay hidden.

Each dataset row is materialised as a throw-away workspace containing a
``solution.py`` stub.  Tests are copied in only after the agent stops, so a run
cannot pass by editing the grading file.
"""

from __future__ import annotations

import ast
import gzip
import json
import random
import re
import warnings
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DATASET_FILES = {
    "humaneval": "HumanEval.jsonl.gz",
    "mbpp": "sanitized-mbpp.json",
}

DATASET_NAMES = tuple(DATASET_FILES)


@dataclass(frozen=True)
class GenerationInstance:
    """One benchmark problem prepared for an agent run."""

    id: str
    entry_point: str
    user_prompt: str
    solution_stub: str
    tests: str
    metadata: dict[str, Any] = field(default_factory=dict)


def load_instances(
    dataset: str,
    path: str | Path,
    *,
    limit: int = 0,
    offset: int = 0,
    sample: int = 0,
    seed: int = 0,
) -> list[GenerationInstance]:
    """Load and deterministically slice a dataset."""

    name = dataset.lower()
    if name not in DATASET_FILES:
        raise ValueError(f"unknown dataset: {dataset}")
    dataset_path = Path(path)
    if not dataset_path.is_file():
        raise FileNotFoundError(f"dataset not found: {dataset_path}")
    if name == "humaneval":
        instances = list(_load_humaneval(dataset_path))
    else:
        instances = list(_load_mbpp(dataset_path))

    if sample and sample < len(instances):
        chosen = set(
            random.Random(seed).sample(range(len(instances)), max(0, sample))
        )
        instances = [item for index, item in enumerate(instances) if index in chosen]
    if offset:
        instances = instances[max(0, offset) :]
    if limit:
        instances = instances[: max(0, limit)]
    return instances


def _load_humaneval(path: Path) -> Iterable[GenerationInstance]:
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            prompt = str(row.get("prompt", ""))
            if not prompt.endswith("\n"):
                prompt += "\n"
            entry = str(row.get("entry_point", "solution"))
            stub = prompt + "    pass\n"
            tests = f"from solution import {entry}\n\n{row.get('test', '')}\n"
            yield GenerationInstance(
                id=str(row.get("task_id", "")),
                entry_point=entry,
                user_prompt=(
                    "Implement the function in `solution.py` so that it satisfies "
                    "the docstring and its examples. Keep the function name and "
                    "signature unchanged. Only edit `solution.py`; do not create "
                    "or modify test files. Hidden tests will grade the result."
                ),
                solution_stub=stub,
                tests=tests,
                metadata={
                    "dataset": "humaneval",
                    "canonical": prompt + str(row.get("canonical_solution", "")),
                },
            )


def _load_mbpp(path: Path) -> Iterable[GenerationInstance]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError("MBPP file must contain a JSON list")
    for row in rows:
        if not isinstance(row, dict):
            continue
        task_id = row.get("task_id")
        code = str(row.get("code", ""))
        entry, signature = _mbpp_signature(code)
        if not entry:
            entry = _entry_from_asserts(row.get("test_list", [])) or "solution"
        description = str(row.get("prompt", "")).strip()
        if signature:
            stub = f'"""{description}"""\n\n\n{signature}\n    pass\n'
        else:
            stub = (
                f'"""{description}"""\n\n\n'
                f"def {entry}(*args, **kwargs):\n    pass\n"
            )
        tests = _mbpp_tests(row, entry)
        yield GenerationInstance(
            id=f"mbpp/{task_id}",
            entry_point=entry,
            user_prompt=(
                f"Implement `{entry}` in `solution.py` so that it matches the "
                "description and signature. Only edit `solution.py`; do not "
                "create or modify test files. Hidden tests will grade the result."
            ),
            solution_stub=stub,
            tests=tests,
            metadata={
                "dataset": "mbpp",
                "task_id": task_id,
                "canonical": code,
            },
        )


def _mbpp_signature(code: str) -> tuple[str, str]:
    """Return the first top-level function name and a ``def`` header."""

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(code)
    except SyntaxError:
        return "", ""
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            try:
                arguments = ast.unparse(node.args)
            except Exception:  # pragma: no cover - extremely old Python
                return node.name, ""
            returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
            return node.name, f"def {node.name}({arguments}){returns}:"
    return "", ""


def _entry_from_asserts(test_list: object) -> str:
    if not isinstance(test_list, list):
        return ""
    for line in test_list:
        match = re.search(r"assert\s+(?:not\s+)?([A-Za-z_]\w*)\s*\(", str(line))
        if match:
            return match.group(1)
    return ""


def _mbpp_tests(row: dict[str, Any], entry: str) -> str:
    imports = row.get("test_imports") or []
    if isinstance(imports, str):
        imports = [imports]
    asserts = row.get("test_list") or []
    if isinstance(asserts, str):
        asserts = [asserts]
    header = "\n".join(str(item) for item in imports)
    body = "\n".join(str(item) for item in asserts)
    return (
        (header + "\n\n" if header else "")
        + "from solution import *  # noqa: F401,F403\n"
        + f"from solution import {entry}  # explicit: import * skips _private names\n\n"
        + f"{body}\n"
    )
