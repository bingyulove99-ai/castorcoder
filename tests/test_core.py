"""Core API, configuration, context, and compatibility tests."""

from __future__ import annotations

from castorcoder import LLM, Agent, Config, Workspace, __version__
from castorcoder.context import ContextManager, estimate_tokens
from castorcoder.tools import ALL_TOOLS, create_tools


def test_version_and_public_api():
    assert __version__ in ("1.1.0", "1.2.0", "1.3.0")
    assert Agent and LLM and Config and Workspace
    assert len(ALL_TOOLS) == 7  # compatibility surface
    # production surface: core tools, multi_edit, tasks, skills, sub-agent,
    # web search/fetch and ask_user
    assert len(create_tools()) == 13

    from castorcoder import CodeIndex, MCPClient, MemoryStore

    assert CodeIndex and MemoryStore and MCPClient


def test_config_env_override(monkeypatch):
    monkeypatch.setenv("CASTORCODER_MODEL", "new-model")
    config = Config.from_env()
    assert config.model == "new-model"


def test_config_reads_project_toml(tmp_path, monkeypatch):
    monkeypatch.delenv("CASTORCODER_PERMISSION_MODE", raising=False)
    path = tmp_path / "config.toml"
    path.write_text('[castorcoder]\nmodel = "local-model"\npermission_mode = "plan"\n')
    config = Config.from_env(path)
    assert config.model == "local-model"
    assert config.permission_mode == "plan"


def test_global_config_can_ignore_project_toml(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        '[castorcoder]\nmodel = "global-model"\nuse_project_config = false\n',
        encoding="utf-8",
    )
    project = tmp_path / "project"
    (project / ".castorcoder").mkdir(parents=True)
    (project / ".castorcoder" / "config.toml").write_text(
        '[castorcoder]\nmodel = "project-model"\n',
        encoding="utf-8",
    )
    (project / ".env").write_text("CASTORCODER_MODEL=env-model\n", encoding="utf-8")
    monkeypatch.setenv("CASTORCODER_HOME", str(home))
    monkeypatch.chdir(project)

    config = Config.from_env()
    assert config.model == "global-model"
    assert config.use_project_config is False


def test_project_config_overrides_global_by_default(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        '[castorcoder]\nmodel = "global-model"\n',
        encoding="utf-8",
    )
    project = tmp_path / "project"
    (project / ".castorcoder").mkdir(parents=True)
    (project / ".castorcoder" / "config.toml").write_text(
        '[castorcoder]\nmodel = "project-model"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CASTORCODER_HOME", str(home))
    monkeypatch.chdir(project)

    assert Config.from_env().model == "project-model"


def test_env_can_disable_project_config(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        '[castorcoder]\nmodel = "global-model"\n',
        encoding="utf-8",
    )
    project = tmp_path / "project"
    (project / ".castorcoder").mkdir(parents=True)
    (project / ".castorcoder" / "config.toml").write_text(
        '[castorcoder]\nmodel = "project-model"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("CASTORCODER_HOME", str(home))
    monkeypatch.setenv("CASTORCODER_USE_PROJECT_CONFIG", "0")
    monkeypatch.chdir(project)

    config = Config.from_env()
    assert config.model == "global-model"
    assert config.use_project_config is False


def test_config_public_dict_redacts_key():
    config = Config(api_key="super-secret")
    assert config.public_dict()["api_key"] == "[configured]"


def test_config_validation_rejects_unknown_policy():
    config = Config(permission_mode="dangerous")
    try:
        config.validate()
    except ValueError as exc:
        assert "permission_mode" in str(exc)
    else:
        raise AssertionError("invalid policy was accepted")


def test_estimate_tokens_counts_content_and_tool_calls():
    messages = [{
        "role": "assistant",
        "content": "hello world",
        "tool_calls": [{"name": "read_file", "arguments": "{}"}],
    }]
    assert 0 < estimate_tokens(messages) < 100


def test_context_snips_verbose_tool_output():
    context = ContextManager(max_tokens=3_000)
    messages = [{"role": "tool", "tool_call_id": "t1", "content": "x\n" * 1_000}]
    before = estimate_tokens(messages)
    assert context._snip_tool_outputs(messages)
    assert estimate_tokens(messages) < before


def test_context_force_compress_summarizes_short_history():
    context = ContextManager(max_tokens=100_000)
    messages = [
        {"role": "user", "content": "please refactor the storage layer"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "continue"},
    ]
    assert context.force_compress(messages, llm=None)
    assert "[Context compressed" in str(messages[0]["content"])
    assert len(messages) == 4
    assert messages[-1]["content"] == "continue"


def test_context_force_compress_ignores_a_two_message_chat():
    context = ContextManager(max_tokens=100_000)
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    assert context.force_compress(messages) is False
    assert len(messages) == 2


def test_context_force_compresses_below_threshold():
    context = ContextManager(max_tokens=100_000)
    messages = []
    for index in range(12):
        messages.append({"role": "user", "content": f"message {index}"})
        messages.append({"role": "assistant", "content": f"answer {index}"})
    assert context.force_compress(messages, llm=None, keep_recent=6)
    assert len(messages) == 8


def test_context_compression_preserves_tool_call_group():
    context = ContextManager(max_tokens=100_000)
    messages = [
        {"role": "user", "content": "old"},
        {"role": "assistant", "content": "old answer"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "one"}]},
        {"role": "tool", "tool_call_id": "one", "content": "result one"},
        {"role": "tool", "tool_call_id": "two", "content": "result two"},
        {"role": "assistant", "content": "done"},
    ]
    assert context.force_compress(messages, keep_recent=3)
    tail = messages[2:]
    assert tail[0]["role"] == "assistant"
    assert tail[0].get("tool_calls")


def test_context_summarizes_with_model_and_falls_back():
    from castorcoder.llm import LLMResponse

    class SummaryLLM:
        def chat(self, messages, tools=None, on_token=None):
            return LLMResponse(content="compressed summary")

    messages = []
    for index in range(20):
        messages.append({"role": "user", "content": f"message {index}"})
        messages.append({"role": "assistant", "content": f"answer {index}"})
    context = ContextManager(max_tokens=100_000)
    assert context.force_compress(messages, SummaryLLM(), keep_recent=6)
    assert "compressed summary" in str(messages[0]["content"])

    fallback = []
    for index in range(20):
        fallback.append({"role": "user", "content": f"message {index}"})
        fallback.append({"role": "assistant", "content": f"answer {index}"})
    assert context.force_compress(fallback, llm=None, keep_recent=6)
    assert "[Context compressed" in str(fallback[0]["content"])


def test_context_hard_collapse_near_limit():
    context = ContextManager(max_tokens=300)
    messages = [
        {"role": "user", "content": "x" * 1_200},
        {"role": "assistant", "content": "y" * 1_200},
    ] * 10
    assert context.maybe_compress(messages, llm=None)
    assert "[Hard context reset]" in str(messages[0]["content"])
