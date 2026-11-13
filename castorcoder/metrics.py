"""Per-turn receipts and a permanent usage ledger.

Waku writes every model call to ``usage.jsonl`` and shows one compact receipt
under each reply.  CastorCoder had run metrics in memory only, so a restart
lost the answer to "what did this cost?".  This module keeps the same small,
inspectable contract:

* :class:`TurnReceipt` summarises one run (tokens, dollars, tools, latency).
* :class:`UsageLedger` appends one JSON line per run under
  ``~/.castorcoder/usage.jsonl`` (never wiped automatically).
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def default_usage_path() -> Path:
    override = os.getenv("CASTORCODER_USAGE_LOG")
    if override:
        return Path(override).expanduser()
    from .paths import home

    return home() / "usage.jsonl"


@dataclass
class TurnReceipt:
    """What one turn did, in numbers only — never prompts or tool output."""

    run_id: str = ""
    session_id: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float | None = None
    rounds: int = 0
    tool_calls: int = 0
    tool_failures: int = 0
    cache_reuses: int = 0
    trimmed_results: int = 0
    elapsed_ms: float = 0.0
    stop_reason: str = "completed"
    workspace: str = ""
    timestamp: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def line(self) -> str:
        """One terminal line, in the spirit of Waku's turn receipt."""

        cost = f"${self.cost_usd:.4f} est" if self.cost_usd is not None else "cost n/a"
        seconds = self.elapsed_ms / 1000
        details = [
            f"{self.prompt_tokens:,} in / {self.completion_tokens:,} out",
            cost,
            f"{self.rounds} round{'s' if self.rounds != 1 else ''}",
            f"{self.tool_calls} tool{'s' if self.tool_calls != 1 else ''}",
            f"{seconds:.1f}s",
        ]
        if self.tool_failures:
            details.append(f"{self.tool_failures} failed")
        if self.cache_reuses:
            details.append(f"{self.cache_reuses} reused")
        return " | ".join(details)


class UsageLedger:
    """Append-only JSONL ledger of completed runs."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path).expanduser() if path else default_usage_path()
        self._lock = threading.Lock()

    def append(self, receipt: TurnReceipt) -> bool:
        record = receipt.as_dict()
        record["ts"] = record.pop("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%S")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
            with self._lock, self.path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            return True
        except OSError:
            return False

    def summary(self, limit: int = 500) -> dict[str, Any]:
        """Aggregate the most recent ``limit`` runs for /cost and the dashboard."""

        totals = {"runs": 0, "prompt_tokens": 0, "completion_tokens": 0,
                  "cost_usd": 0.0, "tool_calls": 0, "failures": 0}
        if not self.path.is_file():
            return totals
        try:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return totals
        for line in lines[-limit:]:
            try:
                row = json.loads(line)
            except (ValueError, TypeError):
                continue
            if not isinstance(row, dict):
                continue
            totals["runs"] += 1
            totals["prompt_tokens"] += int(row.get("prompt_tokens") or 0)
            totals["completion_tokens"] += int(row.get("completion_tokens") or 0)
            totals["tool_calls"] += int(row.get("tool_calls") or 0)
            totals["failures"] += int(row.get("tool_failures") or 0)
            cost = row.get("cost_usd")
            if isinstance(cost, (int, float)) and not isinstance(cost, bool):
                totals["cost_usd"] += float(cost)
        totals["cost_usd"] = round(totals["cost_usd"], 6)
        return totals
