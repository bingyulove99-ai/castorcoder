"""Tool interface, metadata, validation, and instance-level registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..workspace import Workspace


class Tool(ABC):
    """Stable tool protocol used by the LLM-facing executor.

    ``risk`` powers permission modes and ``parallel_safe`` prevents concurrent
    writes from racing.  Existing custom tools only need the original four
    attributes and remain compatible.
    """

    name: str
    description: str
    parameters: dict  # JSON Schema for the function args
    risk: str = "read"  # read | write | execute | delegate
    parallel_safe: bool = True
    timeout_s: float | None = None

    @abstractmethod
    def execute(self, **kwargs) -> str:
        """Run the tool and return a text result."""
        ...

    def schema(self) -> dict:
        """OpenAI function-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def validate(self, arguments: dict) -> str | None:
        """Perform cheap structural validation before calling Python code."""

        if not isinstance(arguments, dict):
            return "arguments must be an object"
        required = self.parameters.get("required", [])
        missing = [name for name in required if name not in arguments]
        if missing:
            return f"missing required arguments: {', '.join(missing)}"
        if self.parameters.get("additionalProperties") is False:
            allowed = set(self.parameters.get("properties", {}))
            extra = sorted(set(arguments) - allowed)
            if extra:
                return f"unexpected arguments: {', '.join(extra)}"
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "object": dict,
            "array": list,
        }
        for name, value in arguments.items():
            rule = self.parameters.get("properties", {}).get(name, {})
            expected = rule.get("type")
            python_type = type_map.get(expected)
            if python_type and (
                not isinstance(value, python_type)
                or expected in {"integer", "number"} and isinstance(value, bool)
            ):
                return f"argument '{name}' must be {expected}"
            if "enum" in rule and value not in rule["enum"]:
                choices = ", ".join(map(str, rule["enum"]))
                return f"argument '{name}' must be one of: {choices}"
        return None


class ToolRegistry:
    """Per-agent registry; avoids shared global tools and supports extensions."""

    def __init__(self, tools: Iterable[Tool]):
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            if not tool.name:
                raise ValueError("Tool name cannot be empty")
            if tool.name in self._tools:
                raise ValueError(f"Duplicate tool name: {tool.name}")
            self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[dict]:
        return [tool.schema() for tool in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)


class WorkspaceTool(Tool):
    """Base class for tools that resolve paths inside a workspace."""

    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
