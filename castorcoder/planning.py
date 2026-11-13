"""A small run-scoped task board exposed to the model as a tool."""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass

from .tools.base import Tool

_VALID_STATUS = {"pending", "in_progress", "completed", "blocked"}


@dataclass
class TaskItem:
    id: int
    title: str
    status: str = "pending"
    note: str = ""


class TaskBoard:
    def __init__(self) -> None:
        self._items: list[TaskItem] = []
        self._lock = threading.RLock()
        self._next_id = 1

    def add(self, title: str, note: str = "") -> TaskItem:
        title = title.strip()
        if not title:
            raise ValueError("Task title cannot be empty")
        with self._lock:
            item = TaskItem(self._next_id, title, note=note.strip())
            self._next_id += 1
            self._items.append(item)
            return item

    def update(self, task_id: int, status: str, note: str | None = None) -> TaskItem:
        if status not in _VALID_STATUS:
            raise ValueError(f"Invalid status: {status}")
        with self._lock:
            item = next((item for item in self._items if item.id == task_id), None)
            if item is None:
                raise ValueError(f"Unknown task id: {task_id}")
            item.status = status
            if note is not None:
                item.note = note.strip()
            return item

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._next_id = 1

    def load(self, items: list[dict]) -> None:
        """Restore a saved board (used by session resume)."""

        with self._lock:
            self._items = []
            highest = 0
            for raw in items or []:
                if not isinstance(raw, dict):
                    continue
                try:
                    task_id = int(raw.get("id") or 0)
                except (TypeError, ValueError):
                    continue
                status = str(raw.get("status") or "pending")
                if status not in _VALID_STATUS:
                    status = "pending"
                title = str(raw.get("title") or "").strip()
                if not title:
                    continue
                self._items.append(
                    TaskItem(task_id or highest + 1, title, status, str(raw.get("note") or ""))
                )
                highest = max(highest, self._items[-1].id)
            self._next_id = highest + 1

    def snapshot(self) -> list[dict]:
        with self._lock:
            return [asdict(item) for item in self._items]

    def render(self) -> str:
        items = self.snapshot()
        if not items:
            return "Task board is empty."
        return "\n".join(
            f"{item['id']}. [{item['status']}] {item['title']}"
            + (f" — {item['note']}" if item["note"] else "")
            for item in items
        )


class TaskTool(Tool):
    name = "task_board"
    description = (
        "Create and maintain a concise task plan for multi-step work. "
        "Use list to inspect the current plan and update progress as work completes."
    )
    parallel_safe = False
    timeout_s = 15.0
    parameters = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "update", "list", "clear"]},
            "title": {"type": "string", "description": "Task title for action=add"},
            "task_id": {"type": "integer", "description": "Task id for action=update"},
            "status": {"type": "string", "enum": sorted(_VALID_STATUS)},
            "note": {"type": "string"},
        },
        "required": ["action"],
        "additionalProperties": False,
    }

    def __init__(self, board: TaskBoard | None = None) -> None:
        self.board = board or TaskBoard()

    def execute(
        self,
        action: str,
        title: str | None = None,
        task_id: int | None = None,
        status: str | None = None,
        note: str | None = None,
    ) -> str:
        if action == "list":
            return self.board.render()
        if action == "clear":
            self.board.clear()
            return "Task board cleared."
        if action == "add":
            item = self.board.add(title or "", note or "")
            return f"Added task {item.id}: {item.title}\n{self.board.render()}"
        if action == "update":
            if task_id is None or status is None:
                return "Error: action=update requires task_id and status"
            item = self.board.update(task_id, status, note)
            return f"Updated task {item.id} to {item.status}.\n{self.board.render()}"
        return f"Error: unknown action '{action}'"