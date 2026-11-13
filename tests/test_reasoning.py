"""Thinking-effort levels, provider mapping, and request fallback."""

from __future__ import annotations

import pytest

from castorcoder.config import Config
from castorcoder.llm import LLM, LiteLLM
from castorcoder.reasoning import (
    EFFORT_LEVELS,
    canonical_effort,
    litellm_reasoning_params,
    normalise_effort,
    openai_reasoning_params,
)


def _llm(call):
    llm = LLM.__new__(LLM)
    llm.model = "deepseek-flash"
    llm.extra = {}
    llm.total_prompt_tokens = 0
    llm.total_completion_tokens = 0
    llm.total_reported_cost = 0.0
    llm.last_finish_reason = ""
    llm._call_with_retry = call
    return llm


def test_effort_aliases_normalise_to_supported_levels():
    assert canonical_effort("HIGH") == "high"
    assert canonical_effort("xhigh") == "high"
    assert canonical_effort("minimal") == "low"
    assert canonical_effort("none") == "off"
    assert canonical_effort("default") == "auto"
    assert canonical_effort("turbo") is None
    assert normalise_effort("turbo") == "auto"
    assert normalise_effort(None) == "auto"


def test_openai_parameters_per_level():
    assert openai_reasoning_params("auto", "deepseek-flash") == {}
    assert openai_reasoning_params("off", "deepseek-flash") == {"reasoning_effort": "none"}
    assert openai_reasoning_params("low", "deepseek-flash") == {"reasoning_effort": "low"}
    assert openai_reasoning_params("max", "deepseek-flash") == {"reasoning_effort": "max"}
    # OpenAI's highest documented value is "high".
    assert openai_reasoning_params("max", "gpt-5.4") == {"reasoning_effort": "high"}


def test_anthropic_uses_a_thinking_budget():
    params = litellm_reasoning_params("high", "anthropic/claude-sonnet-4-6")
    assert params["thinking"] == {"type": "enabled", "budget_tokens": 8_192}
    assert litellm_reasoning_params("off", "claude-sonnet-4-6") == {}
    assert litellm_reasoning_params("max", "deepseek-flash") == {"reasoning_effort": "max"}


def test_litellm_raises_max_tokens_above_the_thinking_budget():
    llm = LiteLLM("anthropic/claude-sonnet-4-6", max_tokens=4_096, reasoning_effort="high")
    params = llm._reasoning_params()
    assert params["max_tokens"] == 8_192 + 1_024

    roomy = LiteLLM("anthropic/claude-sonnet-4-6", max_tokens=32_000, reasoning_effort="low")
    assert "max_tokens" not in roomy._reasoning_params()


def test_chat_sends_the_selected_effort():
    calls = []

    def call(params):
        calls.append(dict(params))
        return iter([])

    llm = _llm(call)
    llm.set_reasoning_effort("high")
    llm.chat([{"role": "user", "content": "hi"}])
    assert calls[0]["reasoning_effort"] == "high"

    # "auto" keeps the provider default: no extension fields are sent.
    llm.set_reasoning_effort("auto")
    llm.chat([{"role": "user", "content": "hi"}])
    assert "reasoning_effort" not in calls[1]


def test_rejected_effort_is_retried_without_the_parameter():
    calls = []

    def call(params):
        calls.append(dict(params))
        if "reasoning_effort" in params:
            raise TypeError("Unrecognized request argument supplied: reasoning_effort")
        return iter([])

    llm = _llm(call)
    llm.set_reasoning_effort("max")
    response = llm.chat([{"role": "user", "content": "hi"}])
    assert response.content == ""
    assert len(calls) == 2
    assert "reasoning_effort" not in calls[1]
    assert "rejected" in llm.reasoning_note


def test_unrelated_type_errors_are_not_swallowed():
    def call(params):
        raise TypeError("messages must be a list")

    llm = _llm(call)
    llm.set_reasoning_effort("high")
    with pytest.raises(TypeError, match="messages"):
        llm.chat([{"role": "user", "content": "hi"}])


def test_config_validates_and_normalises_effort():
    config = Config(reasoning_effort="xhigh")
    config.validate()
    assert config.reasoning_effort == "high"

    with pytest.raises(ValueError, match="reasoning_effort"):
        Config(reasoning_effort="turbo").validate()


def test_config_reads_effort_from_the_environment(monkeypatch):
    monkeypatch.setenv("CASTORCODER_REASONING_EFFORT", "low")
    assert Config.from_env().reasoning_effort == "low"


def test_every_level_is_documented():
    assert set(EFFORT_LEVELS) == {"auto", "off", "low", "medium", "high", "max"}
