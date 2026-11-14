"""Versioned, atomic session persistence with legacy discovery."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .paths import home as _home

SESSIONS_DIR = _home() / "sessions"
LEGACY_SESSIONS_DIR = SESSIONS_DIR
LEGACY_SESSIONS_DIRS = (SESSIONS_DIR,)
SCHEMA_VERSION = 2
_SAFE_SESSION_RE = re.compile(r"[^A-Za-z0-9._-]+")
_LAST_POINTER = "last_session.json"


def new_session_id() -> str:
    return f"session_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


def _workspace_key(workspace: str | Path | None) -> str:
    if not workspace:
        return "default"
    try:
        resolved = str(Path(workspace).expanduser().resolve())
    except OSError:
        resolved = str(workspace)
    if os.name == "nt":
        resolved = resolved.lower()
    return hashlib.sha1(resolved.encode("utf-8")).hexdigest()[:16]


def _normalize_session_id(session_id: str | None) -> str:
    if not session_id:
        return new_session_id()
    name = session_id.strip().replace("\\", "/").split("/")[-1]
    name = _SAFE_SESSION_RE.sub("-", name).strip(".-_")
    return name or _normalize_session_id(None)


def _session_path(session_id: str, root: Path | None = None) -> Path:
    root = root or SESSIONS_DIR
    path = (root / f"{_normalize_session_id(session_id)}.json").resolve()
    if path.parent != root.resolve():
        raise ValueError("Invalid session id")
    return path


def _get_candidate_dirs() -> list[Path]:
    dirs = [SESSIONS_DIR]
    cwd_dir = Path.cwd() / ".castorcoder" / "sessions"
    if cwd_dir != SESSIONS_DIR:
        dirs.append(cwd_dir)
    import tempfile
    tmp_dir = Path(tempfile.gettempdir()) / "castorcoder" / "sessions"
    if tmp_dir not in dirs:
        dirs.append(tmp_dir)
    return dirs


def _get_sessions_dir() -> Path:
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        return SESSIONS_DIR
    except OSError:
        fallback = Path.cwd() / ".castorcoder" / "sessions"
        try:
            fallback.mkdir(parents=True, exist_ok=True)
            return fallback
        except OSError:
            import tempfile
            tmp = Path(tempfile.gettempdir()) / "castorcoder" / "sessions"
            with contextlib.suppress(OSError):
                tmp.mkdir(parents=True, exist_ok=True)
            return tmp


def _without_empty_assistant_tail(messages: list[dict]) -> list[dict]:
    """Drop trailing empty assistant messages from a saved conversation.

    Older builds treated a reasoning-only truncation as a final answer and
    persisted an assistant message with ``content: None``.  Such a message is
    useless when resumed and can confuse strict OpenAI-compatible servers.
    """

    cleaned = list(messages)
    while cleaned:
        last = cleaned[-1]
        if (
            last.get("role") == "assistant"
            and not last.get("tool_calls")
            and not str(last.get("content") or "").strip()
        ):
            cleaned.pop()
        else:
            break
    return cleaned


def save_session(
    messages: list[dict],
    model: str,
    session_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    """Atomically save a session and return its sanitized identifier."""

    sessions_dir = _get_sessions_dir()
    session_id = _normalize_session_id(session_id)
    data = {
        "schema_version": SCHEMA_VERSION,
        "id": session_id,
        "model": model,
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "saved_at_ts": time.time(),
        "messages": _without_empty_assistant_tail(messages),
        "metadata": metadata or {},
    }
    path = _session_path(session_id, root=sessions_dir)
    try:
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass
    return session_id


def load_session_data(session_id: str) -> dict[str, Any] | None:
    for candidate in _get_candidate_dirs():
        try:
            path = _session_path(session_id, candidate)
            if not path.is_file():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data.get("messages"), list) and isinstance(data.get("model"), str):
                return data
        except (ValueError, OSError, json.JSONDecodeError):
            continue
    return None


def load_session(session_id: str) -> tuple[list[dict], str] | None:
    data = load_session_data(session_id)
    if not data:
        return None
    return (_without_empty_assistant_tail(data["messages"]), data["model"])


def list_sessions(limit: int = 20, workspace: str | Path | None = None) -> list[dict]:
    """List saved sessions, newest first, optionally for one workspace."""

    sessions: dict[str, dict] = {}
    wanted = _workspace_key(workspace) if workspace else None
    for directory in _get_candidate_dirs():
        if not directory.is_dir():
            continue
        for path in directory.glob("*.json"):
            if path.name == _LAST_POINTER:
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                messages = data.get("messages") if isinstance(data.get("messages"), list) else []
                preview = next(
                    (
                        str(message.get("content", ""))[:80]
                        for message in messages
                        if message.get("role") == "user" and message.get("content")
                    ),
                    "",
                )
                metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
                item = {
                    "id": data.get("id", path.stem),
                    "model": data.get("model", "?"),
                    "saved_at": data.get("saved_at", "?"),
                    "preview": preview,
                    "schema_version": data.get("schema_version", 1),
                    "workspace": metadata.get("workspace", ""),
                    "turns": sum(1 for m in messages if m.get("role") == "user"),
                    "saved_at_ts": float(data.get("saved_at_ts") or path.stat().st_mtime),
                }
                item["_workspace_key"] = _workspace_key(item["workspace"]) if item["workspace"] else "legacy"
                if wanted is None or item["_workspace_key"] == wanted:
                    sessions[item["id"]] = item
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
    ordered = sorted(sessions.values(), key=lambda item: item["saved_at_ts"], reverse=True)
    for item in ordered:
        item.pop("_workspace_key", None)
    return ordered[:limit]


def _pointer_path() -> Path:
    return _get_sessions_dir() / _LAST_POINTER


def mark_last_session(workspace: str | Path | None, session_id: str) -> None:
    """Remember which session this workspace should resume by default."""

    path = _pointer_path()
    key = _workspace_key(workspace)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data[key] = {
        "id": _normalize_session_id(session_id),
        "saved_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "workspace": str(Path(workspace).expanduser().resolve()) if workspace else "",
    }
    try:
        temporary = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        pass


def latest_session(workspace: str | Path | None = None) -> dict | None:
    """The workspace's last session, falling back to its newest saved file."""

    if workspace:
        try:
            data = json.loads(_pointer_path().read_text(encoding="utf-8"))
            entry = data.get(_workspace_key(workspace)) if isinstance(data, dict) else None
            if isinstance(entry, dict) and entry.get("id"):
                loaded = load_session_data(entry["id"])
                if loaded:
                    metadata = loaded.get("metadata") if isinstance(loaded.get("metadata"), dict) else {}
                    return {
                        "id": entry["id"],
                        "model": loaded.get("model", "?"),
                        "saved_at": loaded.get("saved_at", "?"),
                        "preview": next(
                            (
                                str(m.get("content", ""))[:80]
                                for m in loaded.get("messages", [])
                                if m.get("role") == "user" and m.get("content")
                            ),
                            "",
                        ),
                        "workspace": metadata.get("workspace", ""),
                    }
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    scoped = list_sessions(limit=1, workspace=workspace)
    if scoped:
        return scoped[0]
    if workspace:
        # No session recorded for this directory: do not resume an unrelated
        # project's conversation.
        return None
    recent = list_sessions(limit=1)
    return recent[0] if recent else None


def delete_session(session_id: str) -> bool:
    deleted = False
    for candidate in _get_candidate_dirs():
        try:
            path = _session_path(session_id, candidate)
            if path.exists():
                path.unlink()
                deleted = True
        except (ValueError, OSError):
            continue
    return deleted
