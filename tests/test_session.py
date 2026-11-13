"""Session persistence and migration tests."""

from __future__ import annotations

import json

from castorcoder import session as session_module
from castorcoder.session import (
    delete_session,
    latest_session,
    list_sessions,
    load_session,
    mark_last_session,
    save_session,
)


def _isolate_sessions(monkeypatch, tmp_path):
    current = tmp_path / "current"
    monkeypatch.setattr(session_module, "SESSIONS_DIR", current)
    return current


def test_default_session_ids_do_not_collide(tmp_path, monkeypatch):
    _isolate_sessions(monkeypatch, tmp_path)
    first = save_session([{"role": "user", "content": "first"}], "model-a")
    second = save_session([{"role": "user", "content": "second"}], "model-b")
    assert first != second
    assert load_session(first) == ([{"role": "user", "content": "first"}], "model-a")


def test_session_id_is_sanitized_and_write_is_atomic(tmp_path, monkeypatch):
    current = _isolate_sessions(monkeypatch, tmp_path)
    session_id = save_session([], "model", "../Research Notes!", metadata={"rounds": 2})
    assert session_id == "Research-Notes"
    data = json.loads((current / "Research-Notes.json").read_text(encoding="utf-8"))
    assert data["schema_version"] == 2
    assert data["metadata"] == {"rounds": 2}
    assert not list(current.glob("*.tmp"))


def test_schema_v1_session_can_be_loaded(tmp_path, monkeypatch):
    current = _isolate_sessions(monkeypatch, tmp_path)
    current.mkdir(parents=True)
    (current / "v1.json").write_text(json.dumps({
        "id": "v1",
        "model": "model-v1",
        "messages": [{"role": "user", "content": "hello"}],
        "saved_at": "2024-01-01 00:00:00",
    }), encoding="utf-8")
    assert load_session("v1")[1] == "model-v1"
    assert list_sessions()[0]["schema_version"] == 1


def test_delete_session(tmp_path, monkeypatch):
    _isolate_sessions(monkeypatch, tmp_path)
    session_id = save_session([], "model", "delete-me")
    assert delete_session(session_id)
    assert not delete_session(session_id)


def test_candidate_directories_discovery(tmp_path, monkeypatch):
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    primary.mkdir(parents=True)
    secondary.mkdir(parents=True)

    monkeypatch.setattr(session_module, "SESSIONS_DIR", primary)
    monkeypatch.setattr(session_module, "_get_candidate_dirs", lambda: [primary, secondary])

    save_session([{"role": "user", "content": "in-primary"}], "model-1", "s1")
    # Manually place s2 in secondary
    (secondary / "s2.json").write_text(json.dumps({
        "schema_version": 2,
        "id": "s2",
        "model": "model-2",
        "saved_at": "2024-01-02 00:00:00",
        "messages": [{"role": "user", "content": "in-secondary"}],
        "metadata": {},
    }), encoding="utf-8")

    assert load_session("s1")[0][0]["content"] == "in-primary"
    assert load_session("s2")[0][0]["content"] == "in-secondary"
    ids = [item["id"] for item in list_sessions()]
    assert "s1" in ids and "s2" in ids


def test_sessions_are_scoped_by_workspace(tmp_path, monkeypatch):
    current = _isolate_sessions(monkeypatch, tmp_path)
    current.mkdir(parents=True, exist_ok=True)
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"
    alpha.mkdir()
    beta.mkdir()

    first = save_session(
        [{"role": "user", "content": "alpha work"}], "model-a",
        "alpha-session", metadata={"workspace": str(alpha.resolve())},
    )
    second = save_session(
        [{"role": "user", "content": "beta work"}], "model-b",
        "beta-session", metadata={"workspace": str(beta.resolve())},
    )
    mark_last_session(alpha, first)

    assert [item["id"] for item in list_sessions(workspace=alpha)] == ["alpha-session"]
    assert [item["id"] for item in list_sessions(workspace=beta)] == ["beta-session"]
    assert latest_session(alpha)["id"] == first
    assert latest_session(beta)["id"] == second
    assert latest_session(tmp_path / "gamma") is None


def test_metadata_round_trips_for_resume(tmp_path, monkeypatch):
    current = _isolate_sessions(monkeypatch, tmp_path)
    current.mkdir(parents=True, exist_ok=True)
    tasks = [{"id": 1, "title": "Fix loop", "status": "in_progress", "note": ""}]
    session_id = save_session(
        [{"role": "user", "content": "fix it"}], "model",
        "with-tasks", metadata={"workspace": str(tmp_path), "tasks": tasks},
    )
    from castorcoder.session import load_session_data

    data = load_session_data(session_id)
    assert data["metadata"]["tasks"] == tasks

