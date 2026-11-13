"""OpenAI-compatible streaming adapter tests without network access."""

from __future__ import annotations

from types import SimpleNamespace

from castorcoder.llm import LLM, LLMResponse


def _chunk(content=None, tool_calls=None, usage=None):
    choices = []
    if content is not None or tool_calls is not None:
        delta = SimpleNamespace(content=content, tool_calls=tool_calls)
        choices = [SimpleNamespace(delta=delta)]
    return SimpleNamespace(choices=choices, usage=usage)


def _llm(call):
    llm = LLM.__new__(LLM)
    llm.model = "gpt-4o"
    llm.extra = {"temperature": 0}
    llm.total_prompt_tokens = 0
    llm.total_completion_tokens = 0
    llm.total_reported_cost = 0.0
    llm._call_with_retry = call
    return llm


def test_openai_stream_accumulates_text_usage_and_callbacks():
    usage = SimpleNamespace(prompt_tokens=12, completion_tokens=3)
    llm = _llm(lambda params: iter([_chunk(content="hello "), _chunk(content="world"), _chunk(usage=usage)]))
    tokens = []
    response = llm.chat([{"role": "user", "content": "hi"}], on_token=tokens.append)
    assert response.content == "hello world"
    assert tokens == ["hello ", "world"]
    assert (response.prompt_tokens, response.completion_tokens) == (12, 3)
    assert llm.total_prompt_tokens == 12


def test_provider_reported_cost_is_accumulated():
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, cost=0.00042)
    llm = _llm(lambda params: iter([_chunk(content="ok"), _chunk(usage=usage)]))
    llm.chat([{"role": "user", "content": "hi"}])
    assert llm.total_reported_cost == 0.00042
    assert llm.reported_cost == 0.00042


def test_openai_stream_reassembles_tool_arguments():
    function_one = SimpleNamespace(name="echo", arguments='{"value":')
    function_two = SimpleNamespace(name=None, arguments='"ok"}')
    deltas = [
        SimpleNamespace(index=0, id="call-1", function=function_one),
        SimpleNamespace(index=0, id=None, function=function_two),
    ]
    llm = _llm(lambda params: iter([_chunk(tool_calls=[deltas[0]]), _chunk(tool_calls=[deltas[1]])]))
    response = llm.chat([{"role": "user", "content": "use tool"}], tools=[{"type": "function"}])
    assert response.tool_calls[0].name == "echo"
    assert response.tool_calls[0].arguments == {"value": "ok"}
    assert response.message["tool_calls"][0]["function"]["name"] == "echo"


def test_invalid_tool_json_is_preserved_as_execution_error():
    function = SimpleNamespace(name="echo", arguments="{")
    delta = SimpleNamespace(index=0, id="bad", function=function)
    llm = _llm(lambda params: iter([_chunk(tool_calls=[delta])]))
    response = llm.chat([{"role": "user", "content": "bad tool"}], tools=[{}])
    assert response.tool_calls[0].arguments == {}
    assert response.tool_calls[0].arguments_error


def test_stream_options_falls_back_only_when_reported_unsupported():
    calls = []

    def call(params):
        calls.append(dict(params))
        if "stream_options" in params:
            raise TypeError("stream_options unsupported")
        return iter([_chunk(content="ok")])

    response = _llm(call).chat([{"role": "user", "content": "hi"}])
    assert isinstance(response, LLMResponse)
    assert response.content == "ok"
    assert len(calls) == 2
    assert "stream_options" not in calls[1]


def test_cost_estimate_known_and_unknown_model():
    llm = _llm(lambda params: iter([]))
    llm.total_prompt_tokens = 1_000_000
    llm.total_completion_tokens = 500_000
    assert llm.estimated_cost == 7.5
    llm.model = "custom-model"
    assert llm.estimated_cost is None
