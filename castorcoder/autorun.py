"""Self-documenting task workspace and auto-run test feedback loop.

Inspired by waku-agent's dated, traceable workspace and autorun mechanism:
- TaskWorkspace creates an isolated, dated directory with MANIFEST.md and run logs.
- AutoRunner discovers entry points (pytest, verify.py, test_*.py, main.py, app.py),
  runs them headless with a strict timeout, and provides automated feedback
  so the Agent can observe whether its code actually executed and self-correct.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

ENTRY_PREFERENCES = (
    "verify.py",
    "test_main.py",
    "main.py",
    "app.py",
    "run.py",
)


@dataclass
class AutoRunResult:
    command: list[str]
    exit_code: int | None
    output: str
    elapsed_s: float
    timed_out: bool = False

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def format_feedback(self) -> str:
        cmd_str = " ".join(self.command)
        if self.timed_out:
            return f"[AutoRun Feedback] Command '{cmd_str}' timed out after {self.elapsed_s:.1f}s."
        if self.passed:
            out_preview = f":\n{self.output[:400]}" if self.output.strip() else "."
            return f"[AutoRun Feedback] Verification command '{cmd_str}' passed (exit code 0){out_preview}"
        return (
            f"[AutoRun Feedback] Verification command '{cmd_str}' failed (exit code {self.exit_code}):\n"
            f"{self.output[:1500]}"
        )


class AutoRunner:
    """Discovers and executes runnable tests or entry points in a workspace."""

    def __init__(self, workspace_root: Path | str, *, default_timeout: int = 30) -> None:
        self.root = Path(workspace_root).resolve()
        self.default_timeout = default_timeout

    def discover_command(self) -> list[str] | None:
        """Find the best command to verify the workspace."""
        # 1. Custom verify.py
        if (self.root / "verify.py").is_file():
            return [sys.executable, "verify.py"]

        # 2. Existing pytest suite
        tests_dir = self.root / "tests"
        has_tests_dir = tests_dir.is_dir() and any(tests_dir.glob("test_*.py"))
        has_local_tests = any(self.root.glob("test_*.py"))
        if has_tests_dir or has_local_tests:
            return [sys.executable, "-m", "pytest", "-q"]

        # 3. Known entry scripts
        for name in ENTRY_PREFERENCES:
            candidate = self.root / name
            if candidate.is_file():
                return [sys.executable, name]

        # 4. Any python file containing '__main__'
        for py_file in self.root.glob("*.py"):
            try:
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                if '__name__ == "__main__"' in content or "__name__ == '__main__'" in content:
                    return [sys.executable, py_file.name]
            except OSError:
                continue

        return None

    def run(self, command: list[str] | None = None, *, timeout: int | None = None) -> AutoRunResult | None:
        """Execute the discovered or given command in the workspace directory."""
        cmd = command or self.discover_command()
        if not cmd:
            return None

        t0 = time.perf_counter()
        to = timeout or self.default_timeout
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(self.root),
                capture_output=True,
                text=True,
                timeout=to,
                check=False,
            )
            elapsed = time.perf_counter() - t0
            output = (proc.stdout + "\n" + proc.stderr).strip()
            return AutoRunResult(
                command=cmd,
                exit_code=proc.returncode,
                output=output,
                elapsed_s=round(elapsed, 2),
                timed_out=False,
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = time.perf_counter() - t0
            out = ""
            if exc.stdout:
                out += exc.stdout if isinstance(exc.stdout, str) else exc.stdout.decode("utf-8", errors="replace")
            if exc.stderr:
                out += exc.stderr if isinstance(exc.stderr, str) else exc.stderr.decode("utf-8", errors="replace")
            return AutoRunResult(
                command=cmd,
                exit_code=None,
                output=out.strip(),
                elapsed_s=round(elapsed, 2),
                timed_out=True,
            )
        except Exception as exc:
            elapsed = time.perf_counter() - t0
            return AutoRunResult(
                command=cmd,
                exit_code=-1,
                output=f"Failed to execute {cmd}: {type(exc).__name__}: {exc}",
                elapsed_s=round(elapsed, 2),
                timed_out=False,
            )


@dataclass
class TaskWorkspace:
    """Manages dated, self-documenting workspace directories for agent tasks."""

    root_dir: Path
    run_id: str
    task_prompt: str
    model_name: str
    created_at: datetime = field(default_factory=datetime.now)

    @classmethod
    def create(
        cls,
        base_dir: Path | str,
        run_id: str,
        task_prompt: str,
        model_name: str = "default",
    ) -> TaskWorkspace:
        now = datetime.now()
        date_str = now.strftime("%Y-%m-%d")
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", task_prompt[:24].strip()).strip("_") or "task"
        folder_name = f"{run_id}_{slug}"
        root = Path(base_dir).resolve() / ".castorcoder" / "runs" / date_str / folder_name
        root.mkdir(parents=True, exist_ok=True)
        return cls(root, run_id, task_prompt, model_name, now)

    def write_manifest(
        self,
        *,
        files_created: list[str] | None = None,
        auto_run: AutoRunResult | None = None,
        duration_s: float | None = None,
    ) -> Path:
        """Writes or updates MANIFEST.md in the task workspace."""
        lines = [
            f"# Run Manifest: {self.run_id}",
            "",
            f"- **Timestamp**: {self.created_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"- **Model**: `{self.model_name}`",
            f"- **Task Prompt**: {self.task_prompt}",
        ]
        if duration_s is not None:
            lines.append(f"- **Duration**: {duration_s:.2f}s")

        lines.extend(["", "## Output Deliverables", ""])
        files = files_created or []
        if files:
            for f in sorted(files):
                lines.append(f"- `{f}`")
        else:
            lines.append("*No files created.*")

        lines.extend(["", "## Auto-Run Verification", ""])
        if auto_run:
            status = "PASSED (exit 0)" if auto_run.passed else f"FAILED (exit {auto_run.exit_code})"
            if auto_run.timed_out:
                status = f"TIMED OUT after {auto_run.elapsed_s:.1f}s"
            lines.append(f"- **Command**: `{' '.join(auto_run.command)}`")
            lines.append(f"- **Status**: `{status}`")
            lines.append(f"- **Elapsed**: {auto_run.elapsed_s:.2f}s")
            if auto_run.output:
                lines.append("")
                lines.append("```text")
                lines.append(auto_run.output[:2000])
                lines.append("```")
        else:
            lines.append("*No automated verification was executed.*")

        lines.append("")
        manifest_path = self.root_dir / "MANIFEST.md"
        manifest_path.write_text("\n".join(lines), encoding="utf-8")
        return manifest_path
