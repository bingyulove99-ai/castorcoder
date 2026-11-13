"""Built-in tool factory and backward-compatible default registry."""

from __future__ import annotations

from ..workspace import Workspace
from .agent import AgentTool
from .ask import AskUserTool
from .base import Tool, ToolRegistry
from .bash import BashTool
from .edit import ChangeTracker, EditFileTool, MultiEditTool
from .git_tool import GitTool
from .glob_tool import GlobTool
from .grep import GrepTool
from .read import ReadFileTool
from .web import WebFetchTool, WebSearchTool
from .write import WriteFileTool


def create_tools(
    workspace: Workspace | None = None,
    *,
    tracker: ChangeTracker | None = None,
    task_board=None,
    skill_catalog=None,
    code_index=None,
    memory_store=None,
    git: bool = False,
    web: bool = True,
    ask_user: bool = True,
    ask_callback=None,
) -> list[Tool]:
    # Local imports keep ``castorcoder.tools.base`` importable from the planning
    # and skills modules without a package-initialisation cycle.
    from ..planning import TaskBoard, TaskTool
    from ..skills import SkillCatalog, SkillTool

    workspace = workspace or Workspace()
    tracker = tracker or ChangeTracker()
    task_board = task_board or TaskBoard()
    skill_catalog = skill_catalog or SkillCatalog(workspace)
    tools: list[Tool] = [
        BashTool(workspace),
        ReadFileTool(workspace),
        WriteFileTool(workspace, tracker),
        EditFileTool(workspace, tracker),
        MultiEditTool(workspace, tracker),
        GlobTool(workspace),
        GrepTool(workspace),
        TaskTool(task_board),
        SkillTool(skill_catalog),
        AgentTool(),
    ]
    if web:
        tools.extend([WebSearchTool(), WebFetchTool()])
    if ask_user:
        tools.append(AskUserTool(ask_callback))
    if git:
        tools.append(GitTool(workspace))
    if code_index is not None:
        from .search_code import SearchCodeTool

        tools.append(SearchCodeTool(code_index))
    if memory_store is not None:
        from .memory import SearchMemoryTool, UpdateMemoryTool

        tools.extend([SearchMemoryTool(memory_store), UpdateMemoryTool(memory_store)])
    return tools


def _compatibility_tools() -> list[Tool]:
    """Original seven-tool surface retained for downstream imports."""

    workspace = Workspace()
    tracker = ChangeTracker()
    return [
        BashTool(workspace),
        ReadFileTool(workspace),
        WriteFileTool(workspace, tracker),
        EditFileTool(workspace, tracker),
        GlobTool(workspace),
        GrepTool(workspace),
        AgentTool(),
    ]


# Agent() creates a richer fresh set; this list preserves the v0.3 public API.
ALL_TOOLS = _compatibility_tools()


def get_tool(name: str, tools: list[Tool] | None = None) -> Tool | None:
    """Look up a tool by name in the supplied collection or compatibility set."""

    return ToolRegistry(tools or ALL_TOOLS).get(name)


__all__ = [
    "ALL_TOOLS",
    "GitTool",
    "Tool",
    "ToolRegistry",
    "create_tools",
    "get_tool",
]
