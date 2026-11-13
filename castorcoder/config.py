"""Layered, validated configuration for CastorCoder."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .paths import home as castorcoder_home
from .reasoning import canonical_effort


def _load_dotenv(
    workspace: str | Path | None = None,
    *,
    load_cwd: bool = True,
    load_home: bool = True,
    load_project: bool = True,
) -> None:
    try:
        from dotenv import load_dotenv

        if load_home:
            try:
                home_env = castorcoder_home() / ".env"
                if home_env.is_file():
                    load_dotenv(home_env, override=False)
            except OSError:
                pass
        if not load_project:
            return
        if workspace:
            try:
                ws_env = Path(workspace) / ".env"
                if ws_env.is_file():
                    load_dotenv(ws_env, override=False)
            except OSError:
                pass
        # Only read the process directory's .env when the workspace *is* the
        # process directory.  An explicit --workspace or --config must not
        # silently inherit a different project's keys or model name.
        if load_cwd:
            try:
                cwd = Path.cwd().resolve()
                explicit = Path(workspace).resolve() if workspace else cwd
                if explicit == cwd:
                    load_dotenv(cwd / ".env", override=False)
            except OSError:
                pass
    except ImportError:
        pass


def _read_toml_document(path: Path) -> dict[str, Any]:
    try:
        if not path.is_file():
            return {}
        try:
            import tomllib
        except ImportError:  # Python 3.10
            import tomli as tomllib
        with path.open("rb") as handle:
            data = tomllib.load(handle)
        return data if isinstance(data, dict) else {}
    except OSError:
        return {}


def _read_toml(path: Path) -> dict[str, Any]:
    data = _read_toml_document(path)
    section = data.get("castorcoder", data)
    return section if isinstance(section, dict) else {}


def _env(*names: str, default: str | None = None) -> str | None:
    for name in names:
        val = os.getenv(name)
        if val is not None:
            return val
    return default


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    model: str = "gpt-4o"
    api_key: str = ""
    base_url: str | None = None
    max_tokens: int = 4096
    temperature: float = 0.0
    max_context_tokens: int = 128_000
    provider: str = "openai"
    max_rounds: int = 200
    max_tool_output: int = 15_000
    max_parallel_tools: int = 8
    permission_mode: str = "workspace"
    max_cost_usd: float | None = None
    trace: bool = True
    reasoning_mode: str = "brief"
    reasoning_chars: int = 220
    reasoning_effort: str = "auto"
    auto_save: bool = True
    trim_tool_outputs: bool = True
    trim_long_chars: int = 4_000
    max_consecutive_failures: int = 6
    web_tools: bool = True
    ask_user: bool = True
    request_timeout: float = 300.0
    workspace: str = "."
    allow_outside_workspace: bool = False
    use_project_config: bool = True
    index: bool = True
    memory: bool = True
    auto_index: bool = True
    auto_remember: bool = True
    memory_gate: bool = False
    embedding_model: str = "hash"
    index_path: str | None = None
    memory_path: str | None = None
    mcp_servers: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(
        cls,
        config_path: str | Path | None = None,
        workspace: str | Path | None = None,
    ) -> Config:
        """Load defaults < user TOML < project TOML < environment variables.

        Set ``use_project_config = false`` in the user TOML (or
        ``CASTORCODER_USE_PROJECT_CONFIG=0``) to skip the project layer and
        keep one global configuration for every working directory.
        """

        # The user-level .env is always safe to read.  Project dotenv files are
        # loaded later, after the global config has decided whether project
        # settings are allowed at all.
        _load_dotenv(None, load_cwd=False, load_project=False)
        allowed = set(cls.__dataclass_fields__)
        merged_values: dict[str, Any] = {}
        merged_servers: dict[str, Any] = {}

        # 1. Global user config fallback: ~/.castorcoder/config.toml or ~/.castorcoder.toml
        if not config_path:
            for home_candidate in (
                castorcoder_home() / "config.toml",
                castorcoder_home().with_suffix(".toml"),
            ):
                try:
                    if home_candidate.is_file():
                        home_doc = _read_toml_document(home_candidate)
                        home_vals = home_doc.get("castorcoder", home_doc)
                        if isinstance(home_vals, dict):
                            merged_values.update({k: v for k, v in home_vals.items() if k in allowed})
                        mcp = home_doc.get("mcp", {})
                        servers = mcp.get("servers", {}) if isinstance(mcp, dict) else {}
                        if isinstance(servers, dict):
                            merged_servers.update({str(k): dict(v) for k, v in servers.items() if isinstance(v, dict)})
                        break
                except OSError:
                    pass

        # 2. Project local config: ./.castorcoder/config.toml or workspace/.castorcoder/config.toml
        # A global config can opt out with ``use_project_config = false`` so it
        # stays the single source of truth when the user runs from any repo.
        use_project_config = _as_bool(merged_values.get("use_project_config", True))
        env_use_project = _env("CASTORCODER_USE_PROJECT_CONFIG")
        if env_use_project is not None:
            use_project_config = _as_bool(env_use_project)
        _load_dotenv(
            workspace,
            load_cwd=config_path is None,
            load_home=False,
            load_project=use_project_config,
        )

        if config_path:
            path: Path | None = Path(config_path)
        elif not use_project_config:
            path = None
        elif workspace:
            # An explicit workspace owns its config.  Do not fall back to the
            # process directory's project config: that would silently apply a
            # different project's model and keys.
            path = Path(workspace) / ".castorcoder" / "config.toml"
        else:
            path = Path.cwd() / ".castorcoder" / "config.toml"
        document = _read_toml_document(path) if path else {}
        file_values = document.get("castorcoder", document)
        if isinstance(file_values, dict):
            merged_values.update({key: value for key, value in file_values.items() if key in allowed})
        config = cls(**merged_values)
        config.use_project_config = _as_bool(config.use_project_config)

        mcp = document.get("mcp", {})
        servers = mcp.get("servers", {}) if isinstance(mcp, dict) else {}
        if isinstance(servers, dict):
            merged_servers.update({
                str(name): dict(value)
                for name, value in servers.items()
                if isinstance(value, dict)
            })
        config.mcp_servers = merged_servers

        config.model = _env("CASTORCODER_MODEL", default=config.model) or config.model
        config.api_key = (
            _env("CASTORCODER_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("DEEPSEEK_API_KEY")
            or config.api_key
        )
        config.base_url = (
            _env("CASTORCODER_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or config.base_url
        )
        config.provider = _env("CASTORCODER_PROVIDER", default=config.provider) or config.provider
        config.max_tokens = int(_env("CASTORCODER_MAX_TOKENS", default=str(config.max_tokens)))
        config.temperature = float(_env("CASTORCODER_TEMPERATURE", default=str(config.temperature)))
        config.max_context_tokens = int(_env("CASTORCODER_MAX_CONTEXT", default=str(config.max_context_tokens)))
        config.max_rounds = int(_env("CASTORCODER_MAX_ROUNDS", default=str(config.max_rounds)))
        config.max_tool_output = int(_env("CASTORCODER_MAX_TOOL_OUTPUT", default=str(config.max_tool_output)))
        config.max_parallel_tools = int(_env("CASTORCODER_MAX_PARALLEL", default=str(config.max_parallel_tools)))
        config.permission_mode = _env("CASTORCODER_PERMISSION_MODE", default=config.permission_mode) or config.permission_mode
        max_cost = _env("CASTORCODER_MAX_COST_USD")
        if max_cost is not None:
            config.max_cost_usd = float(max_cost)
        trace = _env("CASTORCODER_TRACE")
        if trace is not None:
            config.trace = _as_bool(trace)
        workspace = _env("CASTORCODER_WORKSPACE")
        if workspace:
            config.workspace = workspace
        outside = _env("CASTORCODER_ALLOW_OUTSIDE_WORKSPACE")
        if outside is not None:
            config.allow_outside_workspace = _as_bool(outside)
        for name, env_name in (
            ("index", "CASTORCODER_INDEX"),
            ("memory", "CASTORCODER_MEMORY"),
            ("auto_index", "CASTORCODER_AUTO_INDEX"),
            ("auto_remember", "CASTORCODER_AUTO_REMEMBER"),
            ("auto_save", "CASTORCODER_AUTO_SAVE"),
            ("trim_tool_outputs", "CASTORCODER_TRIM_TOOL_OUTPUTS"),
            ("web_tools", "CASTORCODER_WEB_TOOLS"),
            ("ask_user", "CASTORCODER_ASK_USER"),
            ("use_project_config", "CASTORCODER_USE_PROJECT_CONFIG"),
        ):
            value = _env(env_name)
            if value is not None:
                setattr(config, name, _as_bool(value))
        config.reasoning_mode = (
            _env("CASTORCODER_REASONING", default=config.reasoning_mode)
            or config.reasoning_mode
        )
        config.reasoning_chars = int(
            _env("CASTORCODER_REASONING_CHARS", default=str(config.reasoning_chars))
        )
        config.reasoning_effort = (
            _env("CASTORCODER_REASONING_EFFORT", default=config.reasoning_effort)
            or config.reasoning_effort
        )
        config.trim_long_chars = int(
            _env("CASTORCODER_TRIM_LONG_CHARS", default=str(config.trim_long_chars))
        )
        config.max_consecutive_failures = int(
            _env(
                "CASTORCODER_MAX_CONSECUTIVE_FAILURES",
                default=str(config.max_consecutive_failures),
            )
        )
        config.request_timeout = float(
            _env("CASTORCODER_REQUEST_TIMEOUT", default=str(config.request_timeout))
        )
        config.embedding_model = _env(
            "CASTORCODER_EMBEDDING_MODEL", default=config.embedding_model
        ) or config.embedding_model
        index_path = _env("CASTORCODER_INDEX_PATH")
        if index_path:
            config.index_path = index_path
        memory_path = _env("CASTORCODER_MEMORY_PATH")
        if memory_path:
            config.memory_path = memory_path
        config.validate()
        return config

    def validate(self) -> None:
        if self.provider not in {"openai", "litellm"}:
            raise ValueError("provider must be 'openai' or 'litellm'")
        if self.permission_mode not in {"plan", "workspace", "full_access"}:
            raise ValueError("permission_mode must be plan, workspace, or full_access")
        if self.max_tokens <= 0 or self.max_context_tokens < 1_000:
            raise ValueError("token limits must be positive")
        if not 1 <= self.max_rounds <= 2_000:
            raise ValueError("max_rounds must be between 1 and 2000")
        if not 1 <= self.max_parallel_tools <= 64:
            raise ValueError("max_parallel_tools must be between 1 and 64")
        if self.max_tool_output < 1_000:
            raise ValueError("max_tool_output must be at least 1000")
        if self.max_cost_usd is not None and self.max_cost_usd <= 0:
            raise ValueError("max_cost_usd must be positive")
        if self.reasoning_mode not in {"brief", "full", "off"}:
            raise ValueError("reasoning_mode must be brief, full, or off")
        if self.reasoning_chars < 0:
            raise ValueError("reasoning_chars must not be negative")
        effort = canonical_effort(self.reasoning_effort)
        if effort is None:
            raise ValueError(
                "reasoning_effort must be auto, off, low, medium, high, or max"
            )
        self.reasoning_effort = effort
        if self.trim_long_chars < 500:
            raise ValueError("trim_long_chars must be at least 500")
        if not 1 <= self.max_consecutive_failures <= 100:
            raise ValueError("max_consecutive_failures must be between 1 and 100")
        if self.request_timeout <= 0:
            raise ValueError("request_timeout must be positive")
        if not self.embedding_model.strip():
            raise ValueError("embedding_model must not be empty")
        if not isinstance(self.mcp_servers, dict):
            raise ValueError("mcp_servers must be a table of server configurations")
        for name, server in self.mcp_servers.items():
            if not isinstance(server, dict) or not server.get("command"):
                raise ValueError(f"mcp server '{name}' must define command")

    def public_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["api_key"] = "[configured]" if self.api_key else "[missing]"
        data["mcp_servers"] = {
            name: {
                "command": server.get("command"),
                "args": server.get("args", []),
                "env": "[REDACTED]" if server.get("env") else {},
            }
            for name, server in self.mcp_servers.items()
        }
        return data
