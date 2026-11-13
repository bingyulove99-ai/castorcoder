"""Shell execution with workspace scoping and destructive-command checks.

On Windows the original code sent almost every POSIX-looking command through
``powershell -Command``.  Starting PowerShell costs several hundred
milliseconds before the command itself runs, which made shell-heavy turns
noticeably slower than Claude Code.  When Git Bash is available we now run the
command through ``bash -c`` (a few tens of milliseconds), keep PowerShell for
cmdlet syntax, and keep cmd for Windows-native builtins.
"""

from __future__ import annotations

import locale
import os
import platform
import re
import shutil
import subprocess

from .base import WorkspaceTool

_DANGEROUS_PATTERNS = [
    (r"\brm\s+(-\w*)?-r\w*\s+(/|~|\$HOME)", "recursive delete on home/root"),
    (r"\brm\s+(-\w*)?-rf\s", "force recursive delete"),
    (r"\bmkfs\b", "format filesystem"),
    (r"\bdd\s+.*of=/dev/", "raw disk write"),
    (r">\s*/dev/sd[a-z]", "overwrite block device"),
    (r"\bchmod\s+(-R\s+)?777\s+/", "chmod 777 on root"),
    (r":\(\)\s*\{.*:\|:.*\}", "fork bomb"),
    (r"\bcurl\b.*\|\s*(sudo\s+)?bash", "pipe curl to bash"),
    (r"\bwget\b.*\|\s*(sudo\s+)?bash", "pipe wget to bash"),
    (r"(?i)\bRemove-Item\b.*\s-Recurse\b.*\s-Force\b", "forced recursive PowerShell delete"),
    (r"(?i)\bFormat-(Volume|Disk)\b", "format disk or volume"),
    (
        r"(?i)\b(Invoke-WebRequest|iwr)\b.*\|\s*(iex|Invoke-Expression)\b",
        "execute downloaded PowerShell",
    ),
]

_POWERSHELL_HINT = re.compile(
    r"(?i)(?:^|[;&|]\s*)("
    r"(?:Get|Set|New|Remove|Copy|Move|Test|Start|Stop|Select|Where|ForEach|"
    r"Invoke|Write|Out|Import|Export|ConvertTo|ConvertFrom|Measure|Sort|Group|"
    r"Join|Split|Rename|Resolve|Update|Install|Uninstall|Register|Unregister)-[A-Za-z]+"
    r"|\$env:|\$PSVersionTable|Get-ChildItem|Get-Content|Set-Location)"
)
_CMD_HINT = re.compile(
    r"(?i)^\s*(dir|type|copy|move|del|erase|rd|rmdir|md|mkdir|where|tasklist|"
    r"findstr|set|cls|ver|vol|attrib|xcopy|robocopy|fc|chkdsk)\b"
)
_WINDOWS_PATH = re.compile(r"(?i)[A-Za-z]:\\")
_UNIX_HINT = re.compile(
    r"(?i)^\s*(ls|cat|pwd|touch|cp|mv|rm|head|tail|which|sed|awk|grep|find|"
    r"chmod|chown|mkdir|rmdir|ln|du|df|wc|sort|uniq|diff|tar|curl|wget)\b"
)
_BASH_STATE: dict[str, bool] = {}


def _translate_for_cmd(command: str) -> str | None:
    """Translate a few POSIX reads to fast cmd builtins.

    ``powershell -Command ls`` costs well over a second on a cold start;
    ``cmd /c dir`` costs milliseconds.  Only unambiguous read-only one-liners
    are translated; anything else keeps the normal shell path.
    """

    stripped = command.strip()
    if not stripped or any(token in stripped for token in ("&&", "||", "|", ";", ">")):
        return None
    if stripped == "pwd":
        return "cd"
    if stripped == "ls" or re.fullmatch(r"ls\s+-\S+(?:\s+-\S+)*", stripped):
        return "dir /a"
    if stripped.startswith("ls "):
        return "dir " + stripped[3:].strip()
    if stripped.startswith("cat "):
        return "type " + stripped[4:].strip()
    if stripped.startswith("which "):
        return "where " + stripped[6:].strip()
    if stripped.startswith("type "):
        return stripped
    return None


def _bash_usable() -> bool:
    """Probe Git Bash once; some Windows sandboxes ship it but deny it."""

    if "ok" in _BASH_STATE:
        return _BASH_STATE["ok"]
    bash = shutil.which("bash")
    if not bash:
        _BASH_STATE["ok"] = False
        return False
    try:
        probe = subprocess.run(
            [bash, "-c", "echo __castor_ok__"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=8,
            shell=False,
        )
        ok = probe.returncode == 0 and "__castor_ok__" in (probe.stdout or "")
    except (OSError, subprocess.SubprocessError):
        ok = False
    _BASH_STATE["ok"] = ok
    return ok


def _check_dangerous(command: str) -> str | None:
    for pattern, reason in _DANGEROUS_PATTERNS:
        if re.search(pattern, command):
            return reason
    return None


class BashTool(WorkspaceTool):
    name = "bash"
    description = (
        "Execute a shell command inside the workspace. Returns stdout, stderr, "
        "and exit code. Use for tests, builds, and git inspection."
    )
    risk = "execute"
    parallel_safe = False
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run"},
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds (default 120, max 3600)",
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    def __init__(self, workspace):
        super().__init__(workspace)
        self.cwd = workspace.root

    def execute(self, command: str, timeout: int = 120) -> str:
        warning = _check_dangerous(command)
        if warning:
            return (
                f"Blocked: {warning}\nCommand: {command}\n"
                "Use a narrower, reviewable command."
            )

        timeout = max(1, min(int(timeout), 3600))
        argv, use_shell, encoding = self._shell_for(command)
        try:
            environment = dict(os.environ)
            if platform.system() == "Windows" and argv and "bash" in os.path.basename(argv[0]).lower():
                environment.setdefault("LANG", "C.UTF-8")
                environment.setdefault("LC_ALL", "C.UTF-8")
            proc = subprocess.run(
                argv if argv is not None else command,
                shell=use_shell,
                capture_output=True,
                text=True,
                encoding=encoding,
                errors="replace",
                timeout=timeout,
                cwd=str(self.cwd),
                env=environment,
            )
            if proc.returncode == 0:
                self._update_cwd(command)
            output = proc.stdout or ""
            if proc.stderr:
                output += f"\n[stderr]\n{proc.stderr}"
            if proc.returncode != 0:
                output += f"\n[exit code: {proc.returncode}]"
            if len(output) > 15_000:
                output = (
                    output[:9_000]
                    + f"\n\n... truncated ({len(output)} chars total) ...\n\n"
                    + output[-3_000:]
                )
            return output.strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return (
                f"Error: Command timed out after {timeout}s. If running a script, "
                "check whether it has an infinite loop or is waiting for interactive input."
            )
        except FileNotFoundError as exc:
            return f"Error: shell or command not found: {exc}"
        except Exception as exc:
            return f"Error running command: {exc}"

    @staticmethod
    def _shell_for(command: str) -> tuple[list[str] | None, bool, str | None]:
        """Choose an interpreter, a shell flag, and an output encoding.

        Returns ``(argv, use_shell, encoding)``.  ``argv`` is None when the
        command is handed to the platform shell through ``shell=True``.
        """

        if platform.system() != "Windows":
            return None, True, None

        if _POWERSHELL_HINT.search(command):
            return (
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                False,
                locale.getpreferredencoding(False) or None,
            )
        if _CMD_HINT.search(command) or _WINDOWS_PATH.search(command):
            return None, True, locale.getpreferredencoding(False) or None

        bash = shutil.which("bash")
        if bash and _bash_usable():
            return [bash, "-c", command], False, "utf-8"
        # Git Bash is present but unusable (common in restricted Windows
        # sandboxes).  Translate simple POSIX reads to cmd builtins first;
        # PowerShell is the slow last resort.
        translated = _translate_for_cmd(command)
        if translated is not None:
            return (
                [os.environ.get("COMSPEC", "cmd.exe"), "/c", translated],
                False,
                locale.getpreferredencoding(False) or None,
            )
        if _UNIX_HINT.search(command) or " | " in command:
            return (
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                False,
                locale.getpreferredencoding(False) or None,
            )
        return None, True, locale.getpreferredencoding(False) or None

    def _update_cwd(self, command: str) -> None:
        # A deliberately conservative heuristic. Compound command state is not
        # inherited from a child shell, so only explicit cd segments are kept.
        for part in command.split("&&"):
            part = part.strip()
            if not part.lower().startswith("cd "):
                continue
            target = part[3:].strip().strip("'\"")
            if not target:
                continue
            try:
                candidate = self.workspace.resolve(self.cwd / target)
            except Exception:
                continue
            if candidate.is_dir():
                self.cwd = candidate
