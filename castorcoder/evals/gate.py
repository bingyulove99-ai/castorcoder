"""Production release gate for CastorCoder.

Ensures that code updates, prompt engineering, and policy changes pass
strict multi-tier quality thresholds before shipping:
- Tier 1: Deterministic unit tests (100% must pass).
- Tier 2: Offline regression benchmark suite (must pass 100%).
- Tier 3: Optional live benchmark generation evaluation.

Exits with code 0 on success (GATE OPEN), or 1 on regression (GATE CLOSED).
"""

from __future__ import annotations

import contextlib
import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .models import load_suite
from .runner import EvalOptions, run_suite


@dataclass
class GateVerdict:
    timestamp: str
    tier1_unit_tests: bool
    tier2_regression_suite: bool
    tier1_details: dict[str, int]
    tier2_details: dict[str, int]
    passed: bool
    reason: str = ""

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class ReleaseGate:
    """Orchestrates multi-tier test suites and enforces release invariants."""

    def __init__(self, repo_root: Path | str | None = None) -> None:
        self.root = Path(repo_root or Path(__file__).resolve().parents[2])

    def run_unit_tests(self, test_path: str = "tests") -> tuple[bool, dict[str, int]]:
        """Run pytest unit test suite and return (passed, counts)."""
        cmd = [sys.executable, "-m", "pytest", "-q", str(self.root / test_path)]
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.root),
                capture_output=True,
                text=True,
                check=False,
            )
            passed = proc.returncode == 0
            # Parse summary line
            proc.stdout + "\n" + proc.stderr
            return passed, {"exit_code": proc.returncode}
        except Exception as exc:
            return False, {"error": str(exc), "exit_code": -1}

    def run_regression_suite(self, suite_path: str = "evals/suites/offline.toml") -> tuple[bool, dict[str, int]]:
        """Run offline regression evaluation suite."""
        path = self.root / suite_path
        if not path.is_file():
            return False, {"error": "suite file not found", "passed": 0, "failed": 1}

        suite = load_suite(path)
        import tempfile
        work_dir = Path(tempfile.gettempdir()) / "castorcoder_gate_eval"
        work_dir.mkdir(parents=True, exist_ok=True)

        results = run_suite(suite, EvalOptions(work_root=work_dir, verbose=False))
        passed_count = sum(1 for r in results if r.passed)
        failed_count = sum(1 for r in results if not r.passed and not r.skipped)
        all_passed = failed_count == 0 and len(results) > 0
        return all_passed, {"passed": passed_count, "failed": failed_count, "total": len(results)}

    def evaluate(self, *, save_report: bool = True, skip_tier2: bool = False) -> GateVerdict:
        """Run all tiers and return overall GateVerdict."""
        now_str = datetime.now().isoformat(timespec="seconds")

        print("=== Tier 1: Deterministic Unit Tests ===")
        t1_ok, t1_details = self.run_unit_tests()
        print(f"Tier 1 Result: {'PASS' if t1_ok else 'FAIL'}")

        if skip_tier2:
            print("\n=== Tier 2: Offline Regression Suite (skipped) ===")
            t2_ok, t2_details = True, {"skipped": 1}
        else:
            print("\n=== Tier 2: Offline Regression Suite ===")
            t2_ok, t2_details = self.run_regression_suite()
            print(f"Tier 2 Result: {'PASS' if t2_ok else 'FAIL'} ({t2_details.get('passed', 0)}/{t2_details.get('total', 0)} tasks)")

        overall_passed = t1_ok and t2_ok
        reason = "All release gates passed." if overall_passed else "One or more gate tiers failed."

        verdict = GateVerdict(
            timestamp=now_str,
            tier1_unit_tests=t1_ok,
            tier2_regression_suite=t2_ok,
            tier1_details=t1_details,
            tier2_details=t2_details,
            passed=overall_passed,
            reason=reason,
        )

        if save_report:
            out_file = self.root / ".castorcoder" / "gate_report.json"
            out_file.parent.mkdir(parents=True, exist_ok=True)
            out_file.write_text(json.dumps(verdict.as_dict(), indent=2), encoding="utf-8")
            if (self.root / ".castorcoder").is_dir():
                with contextlib.suppress(Exception):
                    (self.root / ".castorcoder" / "gate_report.json").write_text(
                        json.dumps(verdict.as_dict(), indent=2), encoding="utf-8"
                    )

        return verdict


def main() -> int:
    gate = ReleaseGate()
    verdict = gate.evaluate()
    if verdict.passed:
        print("\n[GATE OPEN] Safe to ship release.")
        return 0
    else:
        print("\n[GATE CLOSED] Release blocked. Fix regressions before shipping.")
        return 1


if __name__ == "__main__":
    sys.exit(main())
