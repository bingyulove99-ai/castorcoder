"""Command line entry point: ``python -m castorcoder.evals``."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from .models import load_suite
from .report import print_summary, write_report
from .runner import EvalOptions, run_suite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="castorcoder-eval",
        description="Run local, reproducible CastorCoder evaluations.",
    )
    parser.add_argument(
        "--suite",
        default="evals/suites/offline.toml",
        help="Path to an evaluation suite (TOML or JSON)",
    )
    parser.add_argument("--only", nargs="*", default=[], help="Only run these task ids")
    parser.add_argument("--tag", nargs="*", default=[], help="Only run tasks with these tags")
    parser.add_argument(
        "--ids",
        nargs="*",
        default=[],
        help="Only run these dataset instance ids (generation tasks)",
    )
    parser.add_argument("--repeats", type=int, default=None, help="Override repeats per task")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Enable live model tasks (also requires CASTORCODER_EVAL_LIVE=1)",
    )
    parser.add_argument(
        "--work-root",
        default=".tmp/evals",
        help="Directory for temporary fixture copies and databases",
    )
    parser.add_argument(
        "--jobs",
        type=int,
        default=1,
        help="Parallel workers for dataset-backed generation benchmarks",
    )
    parser.add_argument(
        "--infra-retries",
        type=int,
        default=2,
        help="Retries for connection/API failures in generation benchmarks",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume a generation benchmark from its per-attempt checkpoint",
    )
    parser.add_argument(
        "--checkpoint-dir",
        default=".tmp/evals/checkpoints",
        help="Per-attempt checkpoint directory for generation benchmarks",
    )
    parser.add_argument("--report", default=None, help="Report path (JSON; Markdown is written too)")
    parser.add_argument("--list", action="store_true", help="List tasks and exit")
    parser.add_argument("--verbose", action="store_true", help="Print per-task status")
    args = parser.parse_args(argv)

    try:
        suite = load_suite(args.suite)
    except FileNotFoundError as exc:
        print(f"{exc}\nRun from the CastorCoder repository or pass --suite explicitly.")
        return 2
    if args.list:
        for task in suite.tasks:
            print(f"{task.id}\t{task.kind}\t{','.join(task.tags)}")
        return 0

    options = EvalOptions(
        only=set(args.only),
        ids=set(args.ids),
        tags=set(args.tag),
        repeats=args.repeats,
        live=args.live,
        work_root=Path(args.work_root),
        verbose=args.verbose,
        jobs=max(1, args.jobs),
        infra_retries=max(0, args.infra_retries),
        resume=args.resume,
        checkpoint_dir=Path(args.checkpoint_dir),
    )
    results = run_suite(suite, options)
    print_summary(results, suite.name)

    report = args.report or (
        f"evals/results/eval_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    json_path, markdown_path = write_report(results, suite.name, report)
    print(f"\nReports: {json_path} and {markdown_path}")

    failed = [
        result
        for result in results
        if not result.passed and not result.skipped and not result.benchmark
    ]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
