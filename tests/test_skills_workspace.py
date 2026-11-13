"""Workspace boundaries, project instructions, task board, and skill loading."""

from __future__ import annotations

import pytest

from castorcoder.planning import TaskBoard, TaskTool
from castorcoder.skills import SkillCatalog, SkillTool
from castorcoder.workspace import Workspace, WorkspaceViolation


def test_workspace_rejects_path_escape(tmp_path):
    workspace = Workspace(tmp_path)
    with pytest.raises(WorkspaceViolation):
        workspace.resolve(tmp_path.parent / "outside.txt")


def test_workspace_can_explicitly_allow_outside(tmp_path):
    workspace = Workspace(tmp_path, allow_outside=True)
    assert workspace.resolve(tmp_path.parent) == tmp_path.parent.resolve()


def test_project_instructions_are_loaded(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Always run unit tests.")
    workspace = Workspace(tmp_path)
    assert "Always run unit tests" in workspace.project_instructions()


def test_skill_progressive_disclosure(tmp_path):
    skill_dir = tmp_path / ".castorcoder" / "skills" / "review"
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        "---\nname: secure-review\ndescription: Review code for security risks.\n---\n"
        "# Workflow\nInspect trust boundaries before data flow.\n"
    )
    catalog = SkillCatalog(Workspace(tmp_path))
    assert "secure-review" in catalog.prompt_catalog()
    assert "Inspect trust boundaries" not in catalog.prompt_catalog()
    loaded = SkillTool(catalog).execute(action="load", name="secure-review")
    assert "Inspect trust boundaries" in loaded


def test_task_board_state_machine():
    board = TaskBoard()
    tool = TaskTool(board)
    assert "Added task 1" in tool.execute(action="add", title="Inspect repository")
    assert "in_progress" in tool.execute(action="update", task_id=1, status="in_progress")
    assert board.snapshot()[0]["status"] == "in_progress"
    with pytest.raises(ValueError):
        board.update(1, "invalid")
