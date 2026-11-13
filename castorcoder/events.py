"""Structured runtime events and lightweight JSONL tracing.

The agent core deliberately does not depend on an observability framework.  It
emits stable, serialisable events instead, so a CLI, test suite, OpenTelemetry
adapter, or web UI can consume the same execution stream.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class AgentEvent:
    """A single event produced during an agent run."""

    type: str
    run_id: str
    timestamp: float = field(default_factory=time.time)
    data: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


EventHandler = Callable[[AgentEvent], None]


class EventBus:
    """Thread-safe in-process pub/sub used by the executor and agent loop."""

    def __init__(self) -> None:
        self._handlers: list[EventHandler] = []
        self._lock = threading.RLock()

    def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        with self._lock:
            self._handlers.append(handler)

        def unsubscribe() -> None:
            with self._lock:
                if handler in self._handlers:
                    self._handlers.remove(handler)

        return unsubscribe

    def emit(self, event: AgentEvent) -> None:
        with self._lock:
            handlers = tuple(self._handlers)
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                # Observers must never be able to break an agent run.
                continue


class JsonlTrace:
    """Append events as JSON Lines with one atomic write per event."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def __call__(self, event: AgentEvent) -> None:
        line = json.dumps(event.as_dict(), ensure_ascii=False, default=str) + "\n"
        with self._lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)


def new_run_id() -> str:
    """Create a compact, sortable-enough identifier for a single chat run."""

    return f"run_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
