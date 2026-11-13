"""Constructed-argv safe Git inspection tool without shell injection risks."""

from __future__ import annotations

import shutil
import subprocess
from typing import TYPE_CHECKING

from .base import WorkspaceTool

if TYPE_CHECKING:
    from ..workspace import Workspace

_ALLOWED_SUBCOMMANDS = {"status", "diff", "log", "branch", "show", "blame"}
_FORBIDDEN_FLAG_PREFIXES = ("--exec", "--upload-pack", "--receive-pack", "--ext-diff", "--pager", "-c", "--config")
_MAX_OUTPUT_CHARS = 50_000


class GitTool(WorkspaceTool):
    """Execute read-only git operations safely with constructed argv."""

    name = "git"
    description = (
        "Execute read-only Git operations (status, diff, log, branch, show, blame) "
        "inside the workspace. Safely constructs argv to prevent shell injection."
    )
    risk = "read"
    parallel_safe = True
    timeout_s = 20.0

    parameters = {
        "type": "object",
        "properties": {
            "subcommand": {
                "type": "string",
                "enum": ["status", "diff", "log", "branch", "show", "blame"],
                "description": "Git subcommand to execute.",
            },
            "file_path": {
                "type": "string",
                "description": "Optional file path within workspace to inspect.",
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional safe flags (e.g. ['--stat'], ['--staged'], ['HEAD~1']).",
            },
            "limit": {
                "type": "integer",
                "description": "Optional limit on number of commits for 'log'. Default 20.",
            },
        },
        "required": ["subcommand"],
        "additionalProperties": False,
    }

    def __init__(self, workspace: Workspace) -> None:
        super().__init__(workspace)

    def execute(
        self,
        subcommand: str,
        file_path: str | None = None,
        args: list[str] | None = None,
        limit: int | None = None,
    ) -> str:
        if subcommand not in _ALLOWED_SUBCOMMANDS:
            return f"Blocked: subcommand '{subcommand}' is not permitted. Allowed: {sorted(_ALLOWED_SUBCOMMANDS)}"

        git_bin = shutil.which("git")
        if not git_bin:
            return "Error: 'git' executable was not found in system PATH."

        cmd: list[str] = [git_bin, "--no-pager", subcommand]

        # Process flags
        clean_args: list[str] = []
        if args:
            for arg in args:
                if not isinstance(arg, str):
                    continue
                arg_clean = arg.strip()
                if not arg_clean:
                    continue
                # Security checks
                if any(arg_clean.startswith(prefix) for prefix in _FORBIDDEN_FLAG_PREFIXES):
                    return f"Blocked: flag '{arg_clean}' is forbidden for security reasons."
                if any(char in arg_clean for char in ("\x00", "\n", "\r")):
                    return "Blocked: argument contains illegal control characters."
                clean_args.append(arg_clean)

        cmd.extend(clean_args)

        if subcommand == "log":
            n = limit if (limit and limit > 0) else 20
            if not any(a.startswith("-n") or a.startswith("--max-count") for a in clean_args):
                cmd.extend(["-n", str(min(n, 100))])

        # Validate and append file_path
        if file_path:
            try:
                resolved = self.workspace.resolve(file_path)
                rel = resolved.relative_to(self.workspace.root)
                cmd.append(str(rel))
            except Exception as exc:
                return f"Path error: {exc}"

        try:
            res = subprocess.run(
                cmd,
                cwd=str(self.workspace.root),
                capture_output=True,
                text=True,
                errors="replace",
                timeout=self.timeout_s,
                shell=False,
            )
            out = res.stdout.strip()
            err = res.stderr.strip()

            if res.returncode != 0:
                msg = f"git {subcommand} exited with code {res.returncode}"
                if err:
                    msg += f"\n{err}"
                if out:
                    msg += f"\n{out}"
                return msg

            result = out or err or "(empty output)"
            if len(result) > _MAX_OUTPUT_CHARS:
                result = (
                    result[:_MAX_OUTPUT_CHARS]
                    + f"\n\n[Output truncated at {_MAX_OUTPUT_CHARS} characters. Specify a narrower file_path or limit.]"
                )
            return result
        except subprocess.TimeoutExpired:
            return f"Error: git {subcommand} timed out after {self.timeout_s}s."
        except Exception as exc:
            return f"Error executing git {subcommand}: {exc}"
