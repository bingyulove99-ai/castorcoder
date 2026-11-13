"""CastorCoder terminal interface and operational commands."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import threading
import time
from pathlib import Path

from prompt_toolkit import prompt as pt_prompt
from prompt_toolkit.history import FileHistory, InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings

try:  # Windows-only failure mode, but the class imports everywhere.
    from prompt_toolkit.output.win32 import NoConsoleScreenBufferError
except Exception:  # pragma: no cover - depends on prompt_toolkit build
    class NoConsoleScreenBufferError(Exception):
        pass
from rich import box
from rich.console import Console
from rich.json import JSON
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from . import __version__
from .agent import Agent
from .config import Config
from .context import estimate_tokens
from .llm import LLM, LiteLLM
from .metrics import UsageLedger
from .picker import pick_effort, pick_session, short_time, truncate_text, workspace_name
from .reasoning import EFFORT_LEVELS, EFFORT_SUMMARY, canonical_effort
from .session import (
    latest_session,
    list_sessions,
    load_session_data,
    mark_last_session,
    new_session_id,
    save_session,
)
from .ui import LiveRenderer
from .workspace import Workspace

console = Console()

# The REPL can host the cockpit on a daemon thread so ``/dashboard`` works
# without leaving the conversation.  A second invocation reuses it.
_DASHBOARD: dict[str, object] = {"server": None, "thread": None, "url": ""}


def _start_dashboard_background(
    port: int, workspace: Path, *, open_browser: bool = True
) -> str:
    """Start the local cockpit on a background thread and return its URL."""

    from .dashboard.server import start_dashboard

    running = _DASHBOARD.get("url")
    if _DASHBOARD.get("server") is not None and running:
        return str(running)
    server = start_dashboard(
        port=port,
        workspace_root=workspace,
        server_root=workspace,
        open_browser=open_browser,
    )
    thread = threading.Thread(
        target=server.serve_forever,
        name="castorcoder-dashboard",
        daemon=True,
    )
    thread.start()
    url = f"http://127.0.0.1:{int(server.server_address[1])}"
    _DASHBOARD.update({"server": server, "thread": thread, "url": url})
    return url


class SafeFileHistory(FileHistory):
    """FileHistory that degrades gracefully on permission or filesystem errors."""

    def store_string(self, string: str) -> None:
        with contextlib.suppress(OSError):
            super().store_string(string)

    def load_history_strings(self):
        try:
            yield from super().load_history_strings()
        except OSError:
            return



def _tolerant_stdio() -> None:
    """Keep a legacy Windows console from corrupting formatted output.

    Terminals in a GBK/cp936 locale cannot encode many glyphs the renderer
    emits (Markdown bullets, box-drawing, the status spinner); the original
    code wrote them directly and died with UnicodeEncodeError in the middle of
    a run.  When the stream's codec cannot represent common typography, switch
    it to UTF-8; otherwise keep the codec and only replace the unencodable
    characters.
    """

    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError):
            encoding = getattr(stream, "encoding", None) or "utf-8"
            try:
                "•".encode(encoding)
            except (UnicodeEncodeError, LookupError):
                stream.reconfigure(encoding="utf-8", errors="replace")
            else:
                stream.reconfigure(errors="replace")


def _parse_args(argv: list[str] | None = None):
    parser = argparse.ArgumentParser(
        prog="castorcoder",
        description="Observable, workspace-safe AI coding agent inspired by Claude Code.",
    )
    parser.add_argument("-m", "--model", help="Model name")
    parser.add_argument("--base-url", help="OpenAI-compatible API base URL")
    parser.add_argument("--api-key", help="API key (environment variables are preferred)")
    parser.add_argument("-p", "--prompt", help="One-shot prompt")
    parser.add_argument(
        "-r",
        "--resume",
        nargs="?",
        const="__pick__",
        metavar="ID",
        help="Resume a saved session; no value opens the interactive picker",
    )
    parser.add_argument("-c", "--continue", dest="continue_session", action="store_true", help="Resume this workspace's most recent session")
    parser.add_argument("-w", "--workspace", help="Workspace root (default: current directory)")
    parser.add_argument("--reasoning", choices=["brief", "full", "off"], help="How much model reasoning to show (default: brief)")
    parser.add_argument("--reasoning-chars", type=int, help="Characters of reasoning shown in brief mode (default 220)")
    parser.add_argument(
        "--effort",
        choices=list(EFFORT_LEVELS),
        help="How hard the model should think: auto, off, low, medium, high, max (default: auto)",
    )
    parser.add_argument("--no-web", action="store_true", help="Disable web_search and web_fetch")
    parser.add_argument("--no-ask", action="store_true", help="Disable the ask_user tool")
    parser.add_argument("--no-trim", action="store_true", help="Do not shorten old tool results between rounds")
    parser.add_argument("--no-save", action="store_true", help="Disable automatic session saving")
    parser.add_argument("--no-status", action="store_true", help="Disable the live status line")
    parser.add_argument(
        "--permission-mode",
        choices=["plan", "workspace", "full_access"],
        help="Tool permission policy",
    )
    parser.add_argument("--max-cost", type=float, help="Stop before exceeding estimated USD cost")
    parser.add_argument(
        "--max-rounds",
        type=int,
        help="Maximum model/tool rounds per turn (default: 200, max 2000)",
    )
    parser.add_argument("--trace", action="store_true", help="Write structured JSONL runtime events (on by default)")
    parser.add_argument("--no-trace", action="store_true", help="Do not write JSONL runtime traces")
    parser.add_argument("--config", help="Path to config TOML")
    parser.add_argument("--doctor", action="store_true", help="Check configuration without calling a model")
    parser.add_argument("--no-index", action="store_true", help="Disable code index tools")
    parser.add_argument("--no-memory", action="store_true", help="Disable long-term memory")
    parser.add_argument("--rebuild-index", action="store_true", help="Force a full workspace index rebuild")
    parser.add_argument("--mcp", action="store_true", help="Start MCP servers from config")
    parser.add_argument("--approve", action="store_true", help="Ask interactively before blocked tools")
    parser.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="subcommand")

    # dashboard subcommand
    dash_p = subparsers.add_parser("dashboard", help="Start local developer cockpit web interface")
    dash_p.add_argument("--port", type=int, default=7777, help="Port to bind (default 7777)")
    dash_p.add_argument("--host", default="127.0.0.1", help="Host to bind (default 127.0.0.1)")
    dash_p.add_argument("-w", "--workspace", help="Workspace root (default: current directory)")
    dash_p.add_argument("--open", action="store_true", help="Open browser automatically")

    # gate subcommand
    gate_p = subparsers.add_parser("gate", help="Run multi-tier production release gate")
    gate_p.add_argument("-w", "--workspace", help="Workspace root (default: current directory)")
    gate_p.add_argument("--report", help="Path to write gate_report.json")
    gate_p.add_argument("--skip-tier2", action="store_true", help="Skip offline benchmark tier")

    # graph subcommand
    graph_p = subparsers.add_parser("graph", help="Run deterministic wave-based workflow")
    graph_p.add_argument("workflow", choices=["fix", "review"], help="Workflow type: fix or review")
    graph_p.add_argument("-p", "--prompt", required=True, help="Task prompt or problem description")
    graph_p.add_argument("-w", "--workspace", help="Workspace root (default: current directory)")
    graph_p.add_argument("-m", "--model", help="Model name")
    graph_p.add_argument("--base-url", help="API base URL")
    graph_p.add_argument("--api-key", help="API key")

    return parser.parse_args(argv)


def main(argv: list[str] | None = None):
    _tolerant_stdio()
    args = _parse_args(argv)
    if getattr(args, "subcommand", None) == "dashboard":
        return _run_dashboard(args)
    if getattr(args, "subcommand", None) == "gate":
        return _run_gate(args)
    if getattr(args, "subcommand", None) == "graph":
        return _run_graph(args)

    try:
        config = Config.from_env(args.config, workspace=getattr(args, "workspace", None))
        _apply_cli_overrides(config, args)
        workspace = Workspace(
            config.workspace,
            allow_outside=config.allow_outside_workspace or config.permission_mode == "full_access",
        )
    except (OSError, ValueError) as exc:
        console.print(f"[red bold]Configuration error:[/] {exc}")
        return 2

    if args.doctor:
        return _doctor(config, workspace)
    if not config.api_key and config.provider == "openai":
        console.print("[red bold]No API key found.[/]")
        console.print(
            "Set CASTORCODER_API_KEY or OPENAI_API_KEY. For a local compatible "
            "server, a placeholder such as 'local' is usually sufficient."
        )
        return 1

    llm_cls = LiteLLM if config.provider == "litellm" else LLM
    llm = llm_cls(
        model=config.model,
        api_key=config.api_key or None,
        base_url=config.base_url,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout=config.request_timeout,
        reasoning_effort=config.reasoning_effort,
    )

    from .index.embedder import make_embedder
    from .index.indexer import CodeIndex
    from .memory.store import MemoryStore

    try:
        embedder = make_embedder(
            config.embedding_model,
            api_key=config.api_key or None,
            base_url=config.base_url,
        )
        code_index = (
            CodeIndex(
                workspace,
                db_path=config.index_path or None,
                embedder=embedder,
                auto_build=config.auto_index,
            )
            if config.index
            else None
        )
        memory_store = (
            MemoryStore(
                config.memory_path or workspace.root / ".castorcoder" / "memory.db",
                workspace=str(workspace.root),
                embedder=embedder,
            )
            if config.memory
            else None
        )
    except Exception as exc:
        console.print(f"[yellow bold]Storage notice:[/] Could not initialize local cache/memory storage ({exc}). Proceeding without persistent index.")
        code_index = None
        memory_store = None

    if args.rebuild_index and code_index is not None:
        stats = code_index.update(force=True)
        console.print(f"[green]Index rebuilt:[/] {stats.as_dict()}")

    mcp_manager = None
    extra_tools = []
    if args.mcp and config.mcp_servers:
        from .mcp import MCPManager

        mcp_manager = MCPManager(config.mcp_servers)
        try:
            extra_tools = mcp_manager.start()
        except Exception as exc:
            console.print(f"[red bold]MCP error:[/] {exc}")
            mcp_manager.close()
            return 2
        console.print(f"[green]MCP tools:[/] {len(extra_tools)}")

    trace_path = None
    if config.trace:
        traces_dir = workspace.root / ".castorcoder" / "traces"
        if not traces_dir.exists() and (workspace.root / ".castorcoder" / "traces").exists():
            traces_dir = workspace.root / ".castorcoder" / "traces"
        trace_path = traces_dir / f"trace_{time.strftime('%Y%m%d_%H%M%S')}.jsonl"
    try:
        agent = Agent(
            llm=llm,
            workspace=workspace,
            max_context_tokens=config.max_context_tokens,
            max_rounds=config.max_rounds,
            max_tool_output=config.max_tool_output,
            max_parallel_tools=config.max_parallel_tools,
            max_cost_usd=config.max_cost_usd,
            permission_mode=config.permission_mode,
            trace_path=trace_path,
            memory_store=memory_store,
            memory_inject=config.memory,
            memory_gate=config.memory_gate,
            auto_remember=config.memory and config.auto_remember,
            code_index=code_index,
            extra_tools=extra_tools,
            approval_callback=_make_approval() if args.approve else None,
            session_id=new_session_id(),
            trim_tool_outputs=config.trim_tool_outputs,
            trim_long_chars=config.trim_long_chars,
            max_consecutive_failures=config.max_consecutive_failures,
            usage_ledger=UsageLedger(),
            ask_callback=_make_ask() if config.ask_user else None,
            web_tools=config.web_tools,
            enable_ask_user=config.ask_user,
        )
    except Exception as exc:
        if mcp_manager is not None:
            mcp_manager.close()
        console.print(f"[red bold]Agent initialization error:[/] {exc}")
        return 2

    resume_target = _resume_target(args, workspace.root)
    if resume_target is _RESUME_MISSING:
        return 1
    if resume_target is _RESUME_CANCELLED:
        console.print("[dim]No session selected.[/dim]")
        return 0
    if resume_target and not _restore_session(
        agent, config, str(resume_target), override_model=not args.model
    ):
        return 1

    # Attach the effective config so _run_once keeps its historical two-arg
    # call shape (library users and older tests patch it).
    agent.config = config

    try:
        if args.prompt:
            _run_once(agent, args.prompt)
            _save_agent_session(agent, config)
            return 0
        _repl(agent, config, trace_path)
        return 0
    finally:
        if mcp_manager is not None:
            mcp_manager.close()


def _run_dashboard(args) -> int:
    from .dashboard.server import start_dashboard

    ws = Path(args.workspace or Path.cwd()).resolve()
    server = start_dashboard(
        port=args.port,
        host=args.host,
        workspace_root=ws,
        server_root=ws,
        open_browser=args.open,
    )
    console.print(
        f"[bold green]CastorCoder Cockpit is running at http://{args.host}:{args.port}[/bold green] (Press Ctrl+C to stop)"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[yellow]Dashboard stopped.[/yellow]")
    finally:
        server.server_close()
    return 0


def _run_gate(args) -> int:
    from .evals.gate import ReleaseGate

    ws = Path(args.workspace or Path.cwd()).resolve()
    gate = ReleaseGate(repo_root=ws)
    console.print("[bold cyan]Running CastorCoder Production Release Gate...[/bold cyan]")
    verdict = gate.evaluate(save_report=True, skip_tier2=args.skip_tier2)
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(verdict.as_dict(), indent=2), encoding="utf-8")
        console.print(f"[dim]Report written to {report_path}[/dim]")
    color = "green" if verdict.passed else "red"
    status_text = "SHIP" if verdict.passed else "REJECT"
    console.print(f"[{color} bold]Gate Verdict: {status_text}[/{color} bold]")
    return 0 if verdict.passed else 1


def _workspace_changed_files(root: Path) -> list[str]:
    """Return tracked/untracked paths reported by ``git status`` safely.

    Used by ``castorcoder graph review`` so it can review changes that already
    exist in the workspace, not only edits made by a freshly created Agent.
    """

    import subprocess

    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    paths: list[str] = []
    for line in proc.stdout.splitlines():
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            paths.append(path)
    return paths


def _run_graph(args) -> int:
    from .config import Config
    from .graph.workflows import create_code_fix_workflow, create_review_workflow
    from .llm import LLM, LiteLLM

    config = Config.from_env()
    if args.model:
        config.model = args.model
    if args.base_url:
        config.base_url = args.base_url
    if args.api_key:
        config.api_key = args.api_key
    if not config.api_key and config.provider == "openai":
        console.print("[red bold]No API key found.[/] Set CASTORCODER_API_KEY or OPENAI_API_KEY.")
        return 1

    llm_cls = LiteLLM if config.provider == "litellm" else LLM
    llm = llm_cls(
        model=config.model,
        api_key=config.api_key or None,
        base_url=config.base_url,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        reasoning_effort=config.reasoning_effort,
    )
    ws = Workspace(args.workspace or Path.cwd())
    from .agent import Agent
    from .index.indexer import CodeIndex

    agent = Agent(
        llm=llm,
        workspace=ws,
        permission_mode=config.permission_mode,
        max_rounds=config.max_rounds,
        code_index=CodeIndex(ws),
    )

    if args.workflow == "fix":
        wf = create_code_fix_workflow(agent)
        console.print(f"[bold cyan]Running CodeFixWorkflow with task:[/] {args.prompt}")
        final_state = wf.run({"task": args.prompt})
        console.print(f"[green bold]Workflow complete.[/] Verified: {final_state.get('verified')}")
        report = final_state.get("final_report")
        if report:
            console.print(Panel(str(report)[:2000], title="Workflow Report"))
    elif args.workflow == "review":
        wf = create_review_workflow(agent)
        console.print(f"[bold cyan]Running CodeReviewWorkflow with task:[/] {args.prompt}")
        final_state = wf.run(
            {
                "objective": args.prompt,
                "changed_files": _workspace_changed_files(ws.root),
            }
        )
        console.print(f"[green bold]Workflow complete.[/] Verdict: {final_state.get('verdict')}")
        summary = final_state.get("summary")
        if summary:
            console.print(Panel(str(summary)[:2000], title="Review Summary"))
    return 0


def _apply_cli_overrides(config: Config, args) -> None:
    for name in ("model", "base_url", "api_key", "workspace", "permission_mode"):
        value = getattr(args, name, None)
        if value is not None:
            setattr(config, name, value)
    if args.max_cost is not None:
        config.max_cost_usd = args.max_cost
    if getattr(args, "max_rounds", None) is not None:
        config.max_rounds = args.max_rounds
    if args.trace:
        config.trace = True
    if getattr(args, "no_trace", False):
        config.trace = False
    if getattr(args, "no_index", False):
        config.index = False
    if getattr(args, "no_memory", False):
        config.memory = False
    if getattr(args, "no_web", False):
        config.web_tools = False
    if getattr(args, "no_ask", False):
        config.ask_user = False
    if getattr(args, "no_trim", False):
        config.trim_tool_outputs = False
    if getattr(args, "no_save", False):
        config.auto_save = False
    if getattr(args, "reasoning", None):
        config.reasoning_mode = args.reasoning
    if getattr(args, "reasoning_chars", None) is not None:
        config.reasoning_chars = args.reasoning_chars
    if getattr(args, "effort", None):
        config.reasoning_effort = args.effort
    config.validate()


_RESUME_MISSING = object()
_RESUME_CANCELLED = object()


def _workspace_sessions(
    agent: Agent, *, show_all: bool = False, limit: int = 20
) -> list[dict]:
    """Saved sessions for this workspace (or every workspace), newest first."""

    try:
        return list_sessions(
            limit=limit,
            workspace=None if show_all else agent.workspace.root,
        )
    except TypeError:  # test doubles with the old zero-argument signature
        return list_sessions()


def _resume_target(args, workspace: Path) -> str | None | object:
    """Resolve --continue / --resume to a session id (or report none)."""

    if getattr(args, "continue_session", False):
        recent = latest_session(workspace)
        if not recent:
            console.print("[red]No saved session for this workspace yet.[/red]")
            return _RESUME_MISSING
        return str(recent["id"])
    if not getattr(args, "resume", None):
        return None
    if args.resume == "__pick__":
        return _pick_resume_target(workspace)
    if args.resume != "latest":
        return str(args.resume)
    recent = latest_session(workspace)
    if not recent:
        # An explicit --resume latest may still mean "whatever exists".
        recent = latest_session(None)
    if not recent:
        console.print("[red]No saved sessions found to resume.[/red]")
        return _RESUME_MISSING
    return str(recent["id"])


def _pick_resume_target(workspace: Path) -> str | object:
    """Interactive picker for a bare ``--resume``; latest when not a terminal."""

    try:
        sessions = list_sessions(limit=50, workspace=workspace)
    except TypeError:  # test doubles with the old zero-argument signature
        sessions = list_sessions()
    show_all = False
    if not sessions:
        try:
            sessions = list_sessions(limit=50)
        except TypeError:
            sessions = list_sessions()
        show_all = True
    if not sessions:
        console.print("[red]No saved sessions found to resume.[/red]")
        return _RESUME_MISSING
    if not sys.stdin.isatty():
        return str(sessions[0]["id"])
    chosen = pick_session(
        sessions,
        console=console,
        title="Resume a conversation",
        show_workspace=show_all,
    )
    if chosen is None:
        return _RESUME_CANCELLED
    return str(chosen["id"])


def _restore_session(
    agent: Agent,
    config: Config,
    session_id: str,
    *,
    override_model: bool = True,
    recap: bool = True,
) -> bool:
    """Load a saved conversation into ``agent`` and show what was restored."""

    data = load_session_data(session_id)
    if not data:
        console.print(f"[red]Session '{session_id}' not found.[/red]")
        return False
    agent.messages = list(data.get("messages") or [])
    agent.session_id = str(data.get("id") or session_id)
    metadata = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    if isinstance(metadata.get("tasks"), list):
        agent.task_board.load(metadata["tasks"])
    loaded_model = data.get("model")
    if override_model and isinstance(loaded_model, str) and loaded_model:
        agent.llm.model = config.model = loaded_model
    turns = sum(1 for message in agent.messages if message.get("role") == "user")
    console.print(
        f"[green]Resumed[/green] [cyan]{agent.session_id}[/cyan] "
        f"[dim]({turns} turn(s), {len(agent.messages)} messages, "
        f"model {agent.llm.model})[/dim]"
    )
    origin = metadata.get("workspace")
    if isinstance(origin, str) and origin.strip():
        current = str(agent.workspace.root)
        if os.path.normcase(os.path.normpath(origin)) != os.path.normcase(
            os.path.normpath(current)
        ):
            console.print(
                f"[yellow]Note:[/] this conversation came from [dim]{origin}[/dim]; "
                f"tools still run in [dim]{current}[/dim]."
            )
    # The restored conversation is now this workspace's current one, so `-c`
    # continues it rather than the older pointer target.
    mark_last_session(agent.workspace.root, agent.session_id)
    if recap:
        _print_recap(agent)
    return True


def _print_recap(agent: Agent, limit: int = 4) -> None:
    """Show the tail of the restored conversation so the user knows the context."""

    messages = [
        message
        for message in agent.messages
        if message.get("role") in {"user", "assistant"}
        and isinstance(message.get("content"), str)
        and message["content"].strip()
    ][-limit:]
    if not messages:
        return
    table = Table.grid(padding=(0, 1))
    table.add_column(justify="right", style="cyan", no_wrap=True)
    table.add_column(overflow="fold")
    for message in messages:
        label = "You" if message["role"] == "user" else "Agent"
        table.add_row(label, Text(truncate_text(message["content"], 140)))
    console.print(Panel(table, title="Last messages", border_style="dim"))


def _save_agent_session(agent: Agent, config: Config, *, force: bool = False) -> str | None:
    """Persist the conversation after every turn, not only on /save."""

    if not config.auto_save and not force:
        return None
    if not any(message.get("role") == "user" for message in agent.messages):
        return None
    metadata = {
        "workspace": str(agent.workspace.root),
        "permission_mode": config.permission_mode,
        "tasks": agent.task_board.snapshot(),
        "receipt": agent.last_receipt.as_dict() if agent.last_receipt else None,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    session_id = save_session(
        agent.messages,
        agent.llm.model,
        session_id=agent.session_id or new_session_id(),
        metadata=metadata,
    )
    agent.session_id = session_id
    mark_last_session(agent.workspace.root, session_id)
    return session_id


def _doctor(config: Config, workspace: Workspace) -> int:
    from .skills import SkillCatalog
    from .tools import create_tools

    catalog = SkillCatalog(workspace)
    tools = create_tools(
        workspace,
        skill_catalog=catalog,
        web=config.web_tools,
        ask_user=config.ask_user,
    )
    extra = (1 if config.index else 0) + (2 if config.memory else 0)
    rows = [
        f"Version: {__version__}",
        f"Python: {sys.version.split()[0]}",
        f"Workspace: {workspace.root}",
        f"Provider/model: {config.provider} / {config.model}",
        f"API key: {'configured' if config.api_key else 'missing'}",
        f"Permission mode: {config.permission_mode}",
        f"Tools: {len(tools) + extra}",
        f"Skills: {len(catalog.list())}",
        f"Code index: {'enabled' if config.index else 'disabled'} "
        f"({config.embedding_model})",
        f"Memory: {'enabled' if config.memory else 'disabled'}",
        f"Web tools: {'enabled' if config.web_tools else 'disabled'}",
        f"Ask user: {'enabled' if config.ask_user else 'disabled'}",
        f"Reasoning display: {config.reasoning_mode}",
        f"Thinking effort: {config.reasoning_effort}",
        f"Auto-save: {'on' if config.auto_save else 'off'}",
        f"MCP servers: {len(config.mcp_servers)}",
    ]
    console.print(Panel("\n".join(rows), title="CastorCoder Doctor", border_style="green"))
    return 0 if config.api_key or config.provider == "litellm" else 1


_ACTIVE_RENDERER: list = []


def _normalise_command(text: str) -> str:
    """Accept ``／compact`` from a Chinese IME and ``/Compact`` as commands.

    Only the leading verb is lower-cased; the argument (a search query, a
    session id, a model name) keeps its original case.
    """

    command = text.strip()
    if command[:1] in {"／", "⁄"}:
        command = "/" + command[1:].lstrip()
    if command.startswith("/"):
        head, separator, rest = command.partition(" ")
        command = head.lower() + separator + rest
    return command


def _make_renderer(config: Config) -> LiveRenderer:
    return LiveRenderer(
        console,
        reasoning=config.reasoning_mode,
        reasoning_chars=config.reasoning_chars,
    )


def _run_once(agent: Agent, prompt: str, config: Config | None = None):
    """Run one prompt with the live renderer. Returns the AgentResult."""

    config = config or getattr(agent, "config", None) or Config(workspace=str(agent.workspace.root))
    renderer = _make_renderer(config)
    _ACTIVE_RENDERER.append(renderer)
    renderer.begin()
    result = None
    try:
        result = agent.run(
            prompt,
            on_token=renderer.on_token,
            on_event=renderer.on_event,
            on_reasoning=renderer.on_reasoning,
        )
    finally:
        streamed = renderer.finish(result)
        if _ACTIVE_RENDERER and _ACTIVE_RENDERER[-1] is renderer:
            _ACTIVE_RENDERER.pop()
    if result is not None and not streamed and result.output:
        console.print(Markdown(result.output))
    return result


def _repl(agent: Agent, config: Config, trace_path: Path | None) -> None:
    banner = Table.grid(padding=(0, 2))
    banner.add_column(style="dim", justify="right", no_wrap=True)
    banner.add_column(overflow="fold")
    banner.add_row("model", f"[cyan]{config.model}[/cyan]")
    banner.add_row("workspace", f"[cyan]{agent.workspace.root}[/cyan]")
    banner.add_row("session", f"[cyan]{agent.session_id}[/cyan]")
    banner.add_row("policy", f"[yellow]{config.permission_mode}[/yellow]")
    banner.add_row(
        "effort",
        f"[cyan]{config.reasoning_effort}[/cyan] [dim]thinking, /effort to change[/dim]",
    )
    banner.add_row("reasoning", f"[cyan]{config.reasoning_mode}[/cyan] [dim]display[/dim]")
    banner.add_row("rounds", f"[cyan]{config.max_rounds}[/cyan] [dim]per turn[/dim]")
    if trace_path:
        banner.add_row("trace", f"[dim]{trace_path}[/dim]")
    console.print(
        Panel(
            banner,
            title=f"[bold]CastorCoder[/bold] v{__version__}",
            subtitle="[dim]/help commands  ·  /resume restore a chat  ·  quit exit[/dim]",
            border_style="blue",
            padding=(0, 1),
        )
    )
    console.print(
        "[dim]Sessions save automatically; [bold]castorcoder -c[/bold] resumes the "
        "latest one and [bold]castorcoder -r[/bold] lets you pick. "
        "[bold]/dashboard[/bold] opens the trace cockpit.[/dim]"
    )

    history: FileHistory | InMemoryHistory
    from .paths import home as castorcoder_home

    history_path = castorcoder_home() / "history"
    try:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        with open(history_path, "ab"):
            pass
        history = SafeFileHistory(str(history_path))
    except OSError:
        try:
            fallback_history = agent.workspace.root / ".castorcoder" / "history"
            fallback_history.parent.mkdir(parents=True, exist_ok=True)
            with open(fallback_history, "ab"):
                pass
            history = SafeFileHistory(str(fallback_history))
        except OSError:
            history = InMemoryHistory()
    bindings = KeyBindings()

    @bindings.add("enter")
    def _submit(event):
        event.current_buffer.validate_and_handle()

    @bindings.add("escape", "enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    while True:
        try:
            user_input = pt_prompt(
                "You > ", history=history, multiline=True, key_bindings=bindings,
                prompt_continuation="...  ",
            ).strip()
        except NoConsoleScreenBufferError:
            console.print(
                "[red]This terminal cannot host the interactive prompt. "
                "Use [bold]-p \"prompt\"[/bold] for one-shot mode, or run in Windows Terminal.[/red]"
            )
            break
        except (EOFError, KeyboardInterrupt):
            console.print("\nBye!")
            break
        if not user_input:
            continue
        user_input = _normalise_command(user_input)
        if user_input.lower() in {"quit", "exit", "/quit", "/exit"}:
            break
        if user_input.startswith("/"):
            try:
                handled = _handle_command(user_input, agent, config, trace_path)
            except Exception as exc:  # a broken command must not kill the REPL
                console.print(f"[red]Command failed: {type(exc).__name__}: {exc}[/red]")
                continue
            if handled:
                continue

        # Keep the submitted prompt visually separate from the answer.
        console.print()
        renderer = _make_renderer(config)
        _ACTIVE_RENDERER.append(renderer)
        renderer.begin()
        result = None
        try:
            result = agent.run(
                user_input,
                on_token=renderer.on_token,
                on_event=renderer.on_event,
                on_reasoning=renderer.on_reasoning,
            )
        except KeyboardInterrupt:
            agent.cancel()
            console.print("\n[yellow]Interrupted.[/yellow]")
        except Exception as exc:
            console.print(f"\n[red]Error: {type(exc).__name__}: {exc}[/red]")
        finally:
            streamed = renderer.finish(result)
            if _ACTIVE_RENDERER and _ACTIVE_RENDERER[-1] is renderer:
                _ACTIVE_RENDERER.pop()
        if result is not None and not streamed and result.output:
            console.print(Markdown(result.output))
        if result is not None:
            _save_agent_session(agent, config)

    session_id = _save_agent_session(agent, config)
    if session_id:
        console.print(f"[dim]Resume this conversation: castorcoder -c  (session {session_id})[/dim]")


def _handle_command(command: str, agent: Agent, config: Config, trace_path: Path | None) -> bool:
    if command == "/help":
        _show_help()
    elif command == "/reset":
        agent.reset()
        console.print("[yellow]Conversation, tasks, and change journal reset.[/yellow]")
    elif command in {"/new", "/new-session"}:
        _save_agent_session(agent, config, force=True)
        agent.reset()
        agent.session_id = new_session_id()
        console.print(
            f"[green]Started a new session:[/] [cyan]{agent.session_id}[/cyan] "
            "(the previous one was saved)"
        )
    elif command == "/session":
        receipt = agent.last_receipt
        turns = sum(1 for message in agent.messages if message.get("role") == "user")
        line = (
            f"Session: [cyan]{agent.session_id}[/cyan] | {turns} user turn(s) | "
            f"{len(agent.messages)} messages | model [cyan]{agent.llm.model}[/cyan]"
        )
        if receipt:
            line += f"\nLast turn: {receipt.line()}"
        console.print(line)
    elif command == "/status":
        console.print(_status_table(agent))
    elif command in {"/cost", "/usage"}:
        ledger = UsageLedger()
        summary = ledger.summary()
        console.print(
            f"[bold]This process:[/] {agent.last_receipt.line() if agent.last_receipt else 'no runs yet'}"
        )
        console.print(
            f"[bold]Recent ledger:[/] {summary['runs']} run(s), "
            f"{summary['prompt_tokens']:,} in / {summary['completion_tokens']:,} out, "
            f"${summary['cost_usd']:.4f} est, {summary['tool_calls']} tool call(s)"
        )
        console.print(f"[dim]{ledger.path}[/dim]")
    elif command == "/tokens":
        receipt = agent.last_receipt
        if receipt is not None:
            console.print(receipt.line())
        else:
            prompt_tokens = agent.llm.total_prompt_tokens
            completion_tokens = agent.llm.total_completion_tokens
            line = f"Tokens: {prompt_tokens} prompt + {completion_tokens} completion"
            if agent.llm.estimated_cost is not None:
                line += f" (~${agent.llm.estimated_cost:.4f})"
            console.print(line)
    elif command == "/model" or command.startswith("/model "):
        name = command[7:].strip() if command.startswith("/model ") else ""
        if name:
            agent.llm.model = config.model = name
            console.print(f"Switched to [cyan]{name}[/cyan]")
        else:
            console.print(f"Current model: [cyan]{agent.llm.model}[/cyan]")
    elif command == "/reasoning" or command.startswith("/reasoning "):
        mode = command[11:].strip() if command.startswith("/reasoning ") else ""
        if mode in {"brief", "full", "off"}:
            config.reasoning_mode = mode
            console.print(f"Reasoning display: [cyan]{mode}[/cyan]")
        elif mode:
            console.print(Text("Usage: /reasoning [brief|full|off]", style="yellow"))
        else:
            console.print(
                f"Reasoning display: [cyan]{config.reasoning_mode}[/cyan] "
                f"({config.reasoning_chars} chars in brief mode)"
            )
    elif command == "/effort" or command.startswith("/effort "):
        _command_effort(command, agent, config)
    elif command == "/compact" or command.startswith("/compact "):
        argument = command[len("/compact") :].strip()
        keep_recent = 4
        if argument:
            if argument.isdigit() and 2 <= int(argument) <= 40:
                keep_recent = int(argument)
            else:
                console.print(Text("Usage: /compact [messages-to-keep, 2-40]", style="yellow"))
                return True
        if len(agent.messages) <= 2:
            console.print(
                f"[dim]Conversation is too short to compact "
                f"({len(agent.messages)} message(s)).[/dim]"
            )
            return True
        before_messages = len(agent.messages)
        before = estimate_tokens(agent.messages)
        # Summarisation streams a model response, so acknowledge the command
        # before the (possibly slow) call instead of looking frozen.
        console.print(
            f"[dim]Compacting {before_messages} messages (~{before:,} tokens, "
            f"keeping the last {keep_recent})...[/dim]"
        )
        try:
            compressed = agent.context.force_compress(
                agent.messages, agent.llm, keep_recent=keep_recent
            )
        except Exception as exc:
            console.print(f"[red]Compaction failed: {type(exc).__name__}: {exc}[/red]")
            return True
        after = estimate_tokens(agent.messages)
        after_messages = len(agent.messages)
        if compressed and after < before:
            saved = 100 * (before - after) // before
            console.print(
                f"[bold green]Compacted:[/bold green] {before_messages} -> "
                f"{after_messages} messages, ~{before:,} -> ~{after:,} tokens "
                f"([green]{saved}% smaller[/green])."
            )
            if agent.session_id:
                _save_agent_session(agent, config)
        elif compressed:
            console.print(
                f"[green]Compacted to {after_messages} messages "
                f"(~{after:,} estimated tokens).[/green]"
            )
        else:
            console.print(
                f"[dim]Nothing to compact: no summarisable history and no "
                f"oversized tool output (~{after:,} tokens).[/dim]"
            )
    elif command == "/save" or command.startswith("/save "):
        custom_id = command[6:].strip() if command.startswith("/save ") else agent.session_id
        metadata = {
            "workspace": str(agent.workspace.root),
            "permission_mode": config.permission_mode,
            "tasks": agent.task_board.snapshot(),
            "receipt": agent.last_receipt.as_dict() if agent.last_receipt else None,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        sid = save_session(
            agent.messages, agent.llm.model, session_id=custom_id, metadata=metadata
        )
        agent.session_id = sid
        mark_last_session(agent.workspace.root, sid)
        console.print(f"[green]Session saved: {sid}[/green]\nResume: castorcoder -r {sid}")
    elif command == "/resume" or command.startswith("/resume "):
        _command_resume(command, agent, config)
    elif command in {"/sessions", "/sessions --all"}:
        _command_sessions(command, agent)
    elif command == "/diff":
        paths = agent.change_tracker.paths()
        console.print("\n".join(paths) if paths else "[dim]No files changed this session.[/dim]")
    elif command == "/undo":
        restored = agent.undo()
        console.print(
            f"[green]Restored {len(restored)} file(s).[/green]"
            if restored else "[dim]Nothing to undo.[/dim]"
        )
    elif command == "/index" or command.startswith("/index "):
        if agent.code_index is None:
            console.print("[yellow]Code index is disabled (--no-index).[/yellow]")
        else:
            stats = agent.code_index.update(force="--force" in command)
            console.print(f"[green]Index updated:[/green] {stats.as_dict()}")
    elif command.startswith("/search "):
        query = command[8:].strip()
        if agent.code_index is None:
            console.print("[yellow]Code index is disabled (--no-index).[/yellow]")
        elif not query:
            console.print(Text("Usage: /search <query>", style="yellow"))
        else:
            from .index.retriever import render_hits

            console.print(Text(render_hits(agent.code_index.search(query, k=8))))
    elif command == "/memory" or command.startswith("/memory "):
        _handle_memory(command, agent)
    elif command == "/tools":
        table = Table(
            box=box.SIMPLE_HEAD, padding=(0, 1), pad_edge=False, show_edge=False
        )
        table.add_column("TOOL", style="cyan", no_wrap=True)
        table.add_column("RISK", no_wrap=True)
        table.add_column("DESCRIPTION", style="dim", overflow="fold", ratio=1)
        for tool in agent.tools:
            risk = str(getattr(tool, "risk", ""))
            table.add_row(
                Text(tool.name),
                Text(risk, style=_RISK_STYLES.get(risk, "")),
                Text(tool.description),
            )
        console.print(table)
    elif command == "/skills":
        console.print(Text(agent.skill_catalog.prompt_catalog()))
    elif command == "/tasks":
        console.print(agent.task_board.render())
    elif command == "/rounds" or command.startswith("/rounds "):
        argument = command[8:].strip() if command.startswith("/rounds ") else ""
        if argument:
            if argument.isdigit() and 1 <= int(argument) <= 2_000:
                config.max_rounds = agent.max_rounds = int(argument)
                console.print(
                    f"Round limit set to [cyan]{config.max_rounds}[/cyan] "
                    "(applies from the next turn)."
                )
            else:
                console.print(Text("Usage: /rounds [1-2000]", style="yellow"))
        else:
            used = agent.last_receipt.rounds if agent.last_receipt else 0
            console.print(
                f"Round limit: [cyan]{agent.max_rounds}[/cyan] | "
                f"last turn used {used} round(s)"
            )
    elif command == "/dashboard" or command.startswith("/dashboard "):
        argument = command[11:].strip() if command.startswith("/dashboard ") else ""
        port = 7777
        if argument:
            if argument.isdigit() and 1 <= int(argument) <= 65_535:
                port = int(argument)
            else:
                console.print(Text("Usage: /dashboard [port]", style="yellow"))
                return True
        try:
            url = _start_dashboard_background(
                port, agent.workspace.root, open_browser=console.is_terminal
            )
        except OSError as exc:
            console.print(f"[red]Dashboard could not bind port {port}:[/] {exc}")
            console.print("[dim]Try /dashboard <port> with a free port.[/dim]")
        else:
            console.print(
                f"[bold green]Cockpit:[/] {url} "
                "[dim](trace waterfall, memory, sessions, usage, release gate)[/dim]"
            )
    elif command == "/trace":
        if trace_path:
            console.print(str(trace_path))
            console.print("[dim]Inspect every run in the cockpit: /dashboard[/dim]")
        else:
            console.print("Trace is disabled (--no-trace).")
    elif command == "/gate":
        from .evals.gate import ReleaseGate

        gate = ReleaseGate(repo_root=agent.workspace.root)
        console.print("[cyan]Running production release gate...[/cyan]")
        verdict = gate.evaluate(save_report=True)
        color = "green" if verdict.passed else "red"
        status_text = "SHIP" if verdict.passed else "REJECT"
        console.print(f"Release gate verdict: [{color} bold]{status_text}[/{color} bold]")
    elif command == "/config":
        console.print(
            Panel(
                JSON(json.dumps(config.public_dict(), ensure_ascii=False, default=str)),
                title="Effective configuration",
                border_style="dim",
            )
        )
    else:
        return False
    return True


def _effort_table(config: Config) -> Table:
    table = Table(
        box=box.SIMPLE_HEAD, padding=(0, 1), pad_edge=False, show_edge=False
    )
    table.add_column("LEVEL", style="cyan", no_wrap=True)
    table.add_column("EFFECT", overflow="fold", ratio=1)
    for level in EFFORT_LEVELS:
        marker = "* " if level == config.reasoning_effort else "  "
        table.add_row(Text(marker + level), Text(EFFORT_SUMMARY[level]))
    return table


def _command_effort(command: str, agent: Agent, config: Config) -> None:
    """``/effort`` without a value opens the picker; a value is applied live."""

    argument = command[len("/effort") :].strip()
    if not argument:
        chosen = pick_effort(config.reasoning_effort, console=console) if sys.stdin.isatty() else None
        if not chosen:
            console.print(_effort_table(config))
            note = getattr(agent.llm, "reasoning_note", "")
            line = f"Current thinking effort: [cyan]{config.reasoning_effort}[/cyan]"
            if note:
                line += f" [yellow]({note})[/yellow]"
            console.print(line)
            console.print(
                Text("Usage: /effort [auto|off|low|medium|high|max]", style="dim")
            )
            console.print(
                Text(
                    "OpenAI-compatible APIs receive reasoning_effort; Claude "
                    "receives thinking.budget_tokens.",
                    style="dim",
                )
            )
            return
        argument = chosen
    level = canonical_effort(argument)
    if level is None:
        console.print(
            Text("Usage: /effort [auto|off|low|medium|high|max]", style="yellow")
        )
        return
    config.reasoning_effort = level
    setter = getattr(agent.llm, "set_reasoning_effort", None)
    if callable(setter):
        setter(level)
    console.print(
        f"Thinking effort: [cyan]{level}[/cyan] "
        f"[dim]({EFFORT_SUMMARY[level]}; applies from the next turn)[/dim]"
    )


_RISK_STYLES = {
    "read": "green",
    "write": "yellow",
    "execute": "red",
    "delegate": "magenta",
}


def _command_resume(command: str, agent: Agent, config: Config) -> None:
    """``/resume`` opens the picker; ``/resume <#>`` and ``/resume <id>`` also work."""

    argument = command[len("/resume") :].strip()
    show_all = argument in {"--all", "all"}
    if show_all:
        argument = ""
    sessions = _workspace_sessions(agent, show_all=show_all)
    if not argument:
        if not sessions:
            console.print("[dim]No saved sessions found.[/dim]")
            return
        chosen = pick_session(
            sessions,
            console=console,
            current_id=agent.session_id,
            show_workspace=show_all,
        )
        if chosen is None:
            console.print("[dim]Resume cancelled.[/dim]")
            return
        _restore_session(agent, config, str(chosen["id"]))
        return
    if argument.isdigit():
        index = int(argument)
        if not 1 <= index <= len(sessions):
            console.print(
                f"[yellow]No session #{index}.[/yellow] Run /sessions to list them."
            )
            return
        _restore_session(agent, config, str(sessions[index - 1]["id"]))
        return
    if argument == "latest":
        recent = latest_session(agent.workspace.root) or latest_session(None)
        if not recent:
            console.print("[dim]No saved sessions found.[/dim]")
            return
        _restore_session(agent, config, str(recent["id"]))
        return
    _restore_session(agent, config, argument)


def _session_table(
    sessions: list[dict], current_id: str | None, *, show_workspace: bool = False
) -> Table:
    table = Table(
        box=box.SIMPLE_HEAD, padding=(0, 1), pad_edge=False, show_edge=False
    )
    table.add_column("#", justify="right", style="dim", no_wrap=True)
    table.add_column("WHEN", style="cyan", no_wrap=True)
    table.add_column("TURNS", justify="right", no_wrap=True)
    table.add_column("MODEL", no_wrap=True)
    if show_workspace:
        table.add_column("WORKSPACE", no_wrap=True)
    table.add_column("PREVIEW", style="dim", overflow="ellipsis", ratio=1)
    for index, session in enumerate(sessions, start=1):
        marker = f"{index} *" if session.get("id") == current_id else str(index)
        cells: list = [
            marker,
            short_time(session.get("saved_at")),
            str(session.get("turns", "?")),
            str(session.get("model") or "?"),
        ]
        if show_workspace:
            cells.append(workspace_name(session.get("workspace")) or "-")
        cells.append(Text(truncate_text(session.get("preview"), 72)))
        table.add_row(*cells)
    return table


def _command_sessions(command: str, agent: Agent) -> None:
    show_all = command.endswith("--all")
    sessions = _workspace_sessions(agent, show_all=show_all)
    if not sessions:
        console.print("[dim]No saved sessions for this workspace.[/dim]")
        return
    console.print(_session_table(sessions, agent.session_id, show_workspace=show_all))
    console.print(
        "[dim]* = current session   /resume opens the picker; "
        "/resume <#> or /resume <id> restores directly[/dim]"
    )


def _format_status_value(value) -> Text:
    if value is None:
        return Text("-", style="dim")
    if isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False, default=str)
    elif isinstance(value, list):
        text = ", ".join(str(item) for item in value) if value else "-"
    else:
        text = str(value)
    if len(text) > 240:
        text = text[:237] + "..."
    return Text(text, style="dim")


def _status_table(agent: Agent) -> Table:
    table = Table(
        box=None, padding=(0, 1), pad_edge=False, show_header=False, show_edge=False
    )
    table.add_column("FIELD", style="cyan", no_wrap=True)
    table.add_column("VALUE", overflow="fold", ratio=1)
    for key, value in agent.status().items():
        table.add_row(key.replace("_", " "), _format_status_value(value))
    return table


def _memory_table(records) -> Table:
    table = Table(
        box=box.SIMPLE_HEAD, padding=(0, 1), pad_edge=False, show_edge=False
    )
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("KIND", no_wrap=True)
    table.add_column("CONF", justify="right", no_wrap=True)
    table.add_column("CONTENT", overflow="fold", ratio=1)
    for record in records:
        table.add_row(
            Text(str(record.id)),
            str(record.kind),
            f"{record.confidence:.2f}",
            Text(record.content),
        )
    return table


def _handle_memory(command: str, agent: Agent) -> None:
    store = agent.memory_store
    if store is None:
        console.print("[yellow]Long-term memory is disabled (--no-memory).[/yellow]")
        return
    _, _, tail = command.partition(" ")
    action, _, argument = tail.partition(" ")
    action = action or "list"
    argument = argument.strip()
    if action == "search":
        if not argument:
            console.print(Text("Usage: /memory search <query>", style="yellow"))
            return
        hits = store.search(argument, k=10)
        if not hits:
            console.print("[dim]No relevant memories.[/dim]")
            return
        console.print(_memory_table([hit.record for hit in hits]))
    elif action == "forget":
        if not argument:
            console.print(Text("Usage: /memory forget <id>", style="yellow"))
        else:
            console.print(
                "[green]Forgotten.[/green]"
                if store.forget(argument) else "[yellow]Memory not found.[/yellow]"
            )
    elif action == "gc":
        console.print(f"[green]Garbage-collected {store.gc()} memories.[/green]")
    elif action == "stats":
        console.print_json(json.dumps(store.stats(), ensure_ascii=False, default=str))
    elif action == "list":
        records = store.list(limit=50)
        if not records:
            console.print("[dim]Memory is empty.[/dim]")
            return
        console.print(_memory_table(records))
    else:
        console.print(Text("Usage: /memory [list|search <q>|stats|forget <id>|gc]", style="yellow"))


def _make_ask():
    """Bind the ask_user tool to this terminal. None keeps it non-blocking."""

    if not sys.stdin.isatty():
        return None

    def ask(question: str, options: list[str]) -> str:
        renderer = _ACTIVE_RENDERER[-1] if _ACTIVE_RENDERER else None
        if renderer is not None:
            renderer.pause_for_input()
        try:
            console.print(Panel(question, title="Agent question", border_style="cyan"))
            for index, option in enumerate(options, 1):
                console.print(f"  [cyan]{index}[/cyan]. {option}")
            suffix = " or a number" if options else ""
            return pt_prompt(f"Your answer{suffix} > ").strip()
        except (EOFError, KeyboardInterrupt):
            return ""
        finally:
            if renderer is not None:
                renderer.resume_after_input()

    return ask


def _make_approval():
    """Interactive approval for policy-blocked tools."""

    always: set[str] = set()

    def approve(name: str, arguments: dict, reason: str) -> bool:
        if name in always:
            return True
        if not sys.stdin.isatty():
            console.print(f"[yellow]Blocked:[/] {reason} (non-interactive)")
            return False
        from .executor import redact_data

        console.print(f"\n[yellow]Approval required:[/] {reason}")
        console.print(f"[dim]{name}({_brief(redact_data(arguments))})[/dim]")
        try:
            answer = pt_prompt("Allow? [y]es/[n]o/[a]lways: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if answer in {"a", "always"}:
            always.add(name)
            return True
        return answer in {"y", "yes"}

    return approve


_HELP_ROWS = (
    ("/status", "Runtime, task, session, and last-run metrics"),
    ("/session", "Current session id and turn count"),
    ("/new", "Save this chat and start a fresh session"),
    ("/tools", "Available tools and risk classes"),
    ("/skills", "Discovered skill catalog"),
    ("/tasks", "Current task board"),
    ("/model [name]", "Show or change the model"),
    ("/effort [level]", "Thinking effort: auto | off | low | medium | high | max"),
    ("/reasoning [brief|full|off]", "How much reasoning text is shown"),
    ("/rounds [N]", "Show or change the per-turn round limit"),
    ("/dashboard [port]", "Open the local cockpit with trace waterfall"),
    ("/tokens", "Last-turn token usage and estimated cost"),
    ("/cost", "This turn plus the persistent usage ledger"),
    ("/compact [N]", "Summarise history now, keeping the last N messages"),
    ("/diff", "Files changed this session"),
    ("/undo", "Restore files changed this session"),
    ("/index", "Build or update the code index (--force rebuilds)"),
    ("/search <q>", "Hybrid code search over the index"),
    ("/memory ...", "list | search | stats | forget | gc long-term memory"),
    ("/save [name]", "Atomically save the session (optional custom ID)"),
    ("/sessions", "List saved sessions (--all for every workspace)"),
    ("/resume [id]", "Pick a saved conversation, or restore <#> / <id>"),
    ("/trace", "Trace file location"),
    ("/config", "Effective redacted configuration"),
    ("/reset", "Reset conversation state"),
    ("quit", "Exit"),
)


def _show_help() -> None:
    table = Table.grid(padding=(0, 2))
    table.add_column(style="cyan", no_wrap=True)
    table.add_column(style="dim", overflow="fold")
    for command, description in _HELP_ROWS:
        table.add_row(Text(command), Text(description))
    console.print(
        Panel(table, title="CastorCoder Help", border_style="dim", padding=(0, 1))
    )
    console.print(
        "[dim]Enter submits; Esc+Enter inserts a newline. Sessions save "
        "automatically; bare /resume opens the conversation picker.[/dim]"
    )


def _brief(arguments: dict, max_length: int = 100) -> str:
    text = ", ".join(f"{key}={repr(value)[:50]}" for key, value in arguments.items())
    return text[:max_length] + ("..." if len(text) > max_length else "")


if __name__ == "__main__":
    sys.exit(main())
