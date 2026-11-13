"""Offline preflight: every dataset row must pass with its canonical solution.

    python evals/scripts/validate_datasets.py

This checks the adapter (signature extraction, hidden test assembly, imports)
without spending any API budget.  A row that fails here is a harness bug, not a
model failure, and must be fixed before quoting benchmark numbers.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from castorcoder.evals.datasets import load_instances  # noqa: E402
from castorcoder.evals.runner import _run_script_test  # noqa: E402

DATASETS = {
    "humaneval": "evals/datasets/HumanEval.jsonl.gz",
    "mbpp": "evals/datasets/sanitized-mbpp.json",
}


def validate(name: str, path: Path, limit: int = 0) -> tuple[int, int, list[str]]:
    instances = load_instances(name, path, limit=limit)
    failures: list[str] = []
    for instance in instances:
        work = Path(tempfile.mkdtemp(prefix=f"validate-{name}-"))
        try:
            (work / "solution.py").write_text(
                str(instance.metadata.get("canonical", "")), encoding="utf-8"
            )
            (work / "test_solution.py").write_text(instance.tests, encoding="utf-8")
            ok, output = _run_script_test(work / "test_solution.py", work, timeout=30)
            if not ok:
                failures.append(f"{instance.id}: {output.strip().splitlines()[-1]}")
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return len(instances), len(instances) - len(failures), failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", choices=[*DATASETS, "all"], default="all")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)

    names = list(DATASETS) if args.dataset == "all" else [args.dataset]
    failed = False
    for name in names:
        total, passed, failures = validate(name, Path(DATASETS[name]), args.limit)
        print(f"{name}: {passed}/{total} canonical solutions pass")
        for failure in failures[:20]:
            print(f"  FAIL {failure}")
        failed |= bool(failures)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
