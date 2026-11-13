"""Policy-aware tool execution, concurrency scheduling, and audit events."""

from __future__ import annotations

import concurrent.futures
import difflib
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from .events import AgentEvent, EventBus
from .tools.base import Tool, ToolRegistry

ApprovalCallback = Callable[[str, dict, str], bool]


@dataclass(frozen=True)
class PermissionPolicy:
    """Simple, inspectable policy modes inspired by coding-agent sandboxes."""

    mode: str = "workspace"  # plan | workspace | full_access

    def __post_init__(self) -> None:
        if self.mode not in {"plan", "workspace", "full_access"}:
            raise ValueError(f"Unknown permission mode: {self.mode}")

    def allows(self, tool: Tool) -> bool:
        if self.mode == "plan":
            return tool.risk == "read"
        return True


@dataclass
class ToolExecution:
    tool_call_id: str
    name: str
    output: str
    success: bool
    duration_ms: float


_SENSITIVE_KEY = re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization|cookie)")
_SECRET_PATTERNS = [
    re.compile(
        r"(?i)((?:api[_-]?key|token|secret|password|authorization|cookie)"
        r"[\"']?\s*[:=]\s*[\"']?)([^\s,;\"'}]+)"
    ),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
]


def redact_secrets(text: str) -> str:
    result = text
    result = _SECRET_PATTERNS[0].sub(r"\1[REDACTED]", result)
    result = _SECRET_PATTERNS[1].sub("Bearer [REDACTED]", result)
    result = _SECRET_PATTERNS[2].sub("sk-[REDACTED]", result)
    return result


def redact_data(value):
    """Recursively redact sensitive argument keys before trace serialisation."""

    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else redact_data(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_data(item) for item in value]
    return value


def truncate_output(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = max(1, int(limit * 0.65))
    tail = max(1, int(limit * 0.25))
    removed = len(text) - head - tail
    return f"{text[:head]}\n\n... ({removed} chars truncated) ...\n\n{text[-tail:]}"


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        event_bus: EventBus | None = None,
        permission_policy: PermissionPolicy | None = None,
        approval_callback: ApprovalCallback | None = None,
        max_output_chars: int = 15_000,
        max_parallel: int = 8,
    ) -> None:
        self.registry = registry
        self.event_bus = event_bus or EventBus()
        self.policy = permission_policy or PermissionPolicy()
        self.approval_callback = approval_callback
        self.max_output_chars = max(1_000, max_output_chars)
        self.max_parallel = max(1, max_parallel)

    def execute(self, tool_call, run_id: str, cancel_event=None) -> ToolExecution:
        started = time.perf_counter()
        if cancel_event is not None and cancel_event.is_set():
            return self._finish(tool_call, run_id, started, "Error: run cancelled", False)
        tool = self.registry.get(tool_call.name)
        if tool is None:
            names = [item.name for item in self.registry.list()]
            close = difflib.get_close_matches(tool_call.name, names, n=3, cutoff=0.5)
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            return self._finish(
                tool_call,
                run_id,
                started,
                f"Error: unknown tool '{tool_call.name}'.{hint} "
                f"Available: {', '.join(names)}",
                False,
            )

        parse_error = getattr(tool_call, "arguments_error", None)
        if parse_error:
            return self._finish(
                tool_call,
                run_id,
                started,
                f"Error: invalid JSON arguments for {tool.name}: {parse_error}",
                False,
            )

        error = tool.validate(tool_call.arguments)
        if error:
            return self._finish(
                tool_call, run_id, started, f"Error: bad arguments for {tool.name}: {error}", False
            )

        if not self.policy.allows(tool):
            reason = f"permission mode '{self.policy.mode}' blocks {tool.risk} tools"
            approved = bool(
                self.approval_callback
                and self.approval_callback(tool.name, tool_call.arguments, reason)
            )
            if not approved:
                return self._finish(
                    tool_call, run_id, started, f"Blocked: {reason}", False
                )

        self.event_bus.emit(AgentEvent("tool.started", run_id, data={
            "tool_call_id": tool_call.id,
            "name": tool.name,
            "risk": tool.risk,
            "arguments": redact_data(tool_call.arguments),
        }))
        try:
            raw = self._invoke(tool, tool_call.arguments)
            output = str(raw)
            success = not output.lower().startswith(("error:", "blocked:"))
        except TimeoutError:
            limit = f" after {tool.timeout_s:g}s" if tool.timeout_s else ""
            output = f"Error: {tool.name} timed out{limit}"
            success = False
        except TypeError as exc:
            output, success = f"Error: bad arguments for {tool.name}: {exc}", False
        except Exception as exc:
            output, success = f"Error executing {tool.name}: {type(exc).__name__}: {exc}", False

        return self._finish(tool_call, run_id, started, output, success)

    def _invoke(self, tool: Tool, arguments: dict):
        """Run a tool with an optional cooperative wall-clock timeout."""

        if not tool.timeout_s:
            return tool.execute(**arguments)

        result: dict[str, object] = {}

        def target() -> None:
            try:
                result["value"] = tool.execute(**arguments)
            except Exception as exc:  # re-raised on the calling thread
                result["error"] = exc

        worker = threading.Thread(target=target, daemon=True)
        worker.start()
        worker.join(tool.timeout_s)
        if worker.is_alive():
            raise TimeoutError(tool.name)
        if "error" in result:
            error = result["error"]
            assert isinstance(error, BaseException)
            raise error
        return result.get("value", "")

    def execute_many(self, tool_calls: list, run_id: str, cancel_event=None) -> list[ToolExecution]:
        """Run consecutive parallel-safe calls together, ordered calls alone.

        A mixed batch such as ``read, read, bash, read`` previously ran every
        call sequentially because one tool was unsafe.  Grouping adjacent safe
        runs keeps mutations ordered while still overlapping independent reads,
        which is where most of a coding turn's wall-clock goes.
        """

        results: list[ToolExecution | None] = [None] * len(tool_calls)
        index = 0
        while index < len(tool_calls):
            tool = self.registry.get(tool_calls[index].name)
            if tool is not None and tool.parallel_safe:
                end = index + 1
                while end < len(tool_calls):
                    candidate = self.registry.get(tool_calls[end].name)
                    if candidate is None or not candidate.parallel_safe:
                        break
                    end += 1
                group = list(range(index, end))
                if len(group) > 1:
                    workers = min(self.max_parallel, len(group))
                    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                        futures = {
                            position: pool.submit(
                                self.execute, tool_calls[position], run_id, cancel_event
                            )
                            for position in group
                        }
                        for position, future in futures.items():
                            results[position] = future.result()
                else:
                    results[index] = self.execute(tool_calls[index], run_id, cancel_event)
                index = end
            else:
                results[index] = self.execute(tool_calls[index], run_id, cancel_event)
                index += 1
        return [item for item in results if item is not None]

    def blocked_execution(self, tool_call, message: str, run_id: str) -> ToolExecution:
        """Report a call the loop guard refused to run."""

        return self._finish(tool_call, run_id, time.perf_counter(), message, False)

    def cached_execution(self, tool_call, output: str, run_id: str) -> ToolExecution:
        """Build a result for an identical read-only call without re-running it."""

        result = ToolExecution(tool_call.id, tool_call.name, output, True, 0.0)
        self.event_bus.emit(AgentEvent("tool.completed", run_id, data={
            "tool_call_id": tool_call.id,
            "name": tool_call.name,
            "success": True,
            "duration_ms": 0.0,
            "output_chars": len(output),
            "cached": True,
            "status": "success",
        }))
        return result

    def _finish(self, tool_call, run_id: str, started: float, output: str, success: bool) -> ToolExecution:
        output = truncate_output(redact_secrets(output), self.max_output_chars)
        duration_ms = (time.perf_counter() - started) * 1000
        result = ToolExecution(tool_call.id, tool_call.name, output, success, duration_ms)
        self.event_bus.emit(AgentEvent("tool.completed", run_id, data={
            "tool_call_id": tool_call.id,
            "name": tool_call.name,
            "success": success,
            "status": "success" if success else "error",
            "duration_ms": round(duration_ms, 3),
            "output_chars": len(output),
            "output_preview": output[:200],
        }))
        return result
