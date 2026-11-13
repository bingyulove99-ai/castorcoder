"""Role-scoped sub-agent delegation with isolated conversation state."""

from __future__ import annotations

from .base import Tool


class AgentTool(Tool):
    name = "agent"
    description = (
        "Delegate a bounded sub-task to an isolated sub-agent. Use research mode "
        "for read-only codebase analysis and execute mode for implementation."
    )
    risk = "delegate"
    parallel_safe = False
    parameters = {
        "type": "object",
        "properties": {
            "task": {"type": "string", "description": "Concrete, bounded objective"},
            "role": {
                "type": "string",
                "enum": ["researcher", "implementer", "reviewer", "tester"],
                "description": "Specialist role (default: researcher)",
            },
            "mode": {
                "type": "string",
                "enum": ["research", "execute"],
                "description": "Whether the sub-agent may mutate files",
            },
        },
        "required": ["task"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self._parent_agent = None

    def execute(self, task: str, role: str = "researcher", mode: str = "research") -> str:
        if self._parent_agent is None:
            return "Error: agent tool not initialized (no parent agent)"

        from ..agent import Agent

        parent = self._parent_agent
        available = [tool for tool in parent.tools if tool.name != "agent"]
        if mode == "research":
            available = [tool for tool in available if tool.risk == "read"]

        role_prompt = (
            f"You are a specialist {role} sub-agent. Complete only the delegated "
            "objective, verify claims with tools, and return a concise evidence-backed "
            "report to the parent agent."
        )
        sub = Agent(
            llm=parent.llm,
            tools=available,
            workspace=parent.workspace,
            max_context_tokens=parent.context.max_tokens,
            max_rounds=min(50, parent.max_rounds),
            permission_mode="plan" if mode == "research" else parent.permission_mode,
            system_suffix=role_prompt,
        )
        try:
            result = sub.chat(task)
            if len(result) > 6_000:
                result = result[:5_500] + "\n... (sub-agent output truncated)"
            return f"[Sub-agent completed: {role}/{mode}]\n{result}"
        except Exception as exc:
            return f"Sub-agent error: {type(exc).__name__}: {exc}"
