"""LLM provider layer - thin wrapper over OpenAI-compatible APIs.

Since most providers (DeepSeek, Qwen, Kimi, GLM, Ollama, etc.) expose an
OpenAI-compatible endpoint, we just use the openai SDK directly.  Switch
provider by changing OPENAI_BASE_URL + OPENAI_API_KEY. That's it.

For providers that are NOT OpenAI-compatible (AWS Bedrock, Google Vertex,
etc.), use the LiteLLM backend which routes to 100+ providers through a
single unified interface. Set CASTORCODER_PROVIDER=litellm.
"""

import json
import time
from dataclasses import dataclass, field

from openai import APIConnectionError, APIError, APITimeoutError, OpenAI, RateLimitError

from .reasoning import litellm_reasoning_params, normalise_effort, openai_reasoning_params

# Some OpenAI-compatible servers reject optional request fields with one of
# these statuses.  Retrying without the field is safer than failing the turn.
_OPTIONAL_PARAM_STATUSES = {None, 400, 404, 422}
_REASONING_HINTS = ("reasoning_effort", "reasoning effort", "thinking", "extra_body")


def _unsupported_parameter(exc: Exception, name: str) -> bool:
    message = str(exc).lower()
    return (
        getattr(exc, "status_code", None) in _OPTIONAL_PARAM_STATUSES
        and name in message
    )


def _mentions_reasoning(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        getattr(exc, "status_code", None) in _OPTIONAL_PARAM_STATUSES
        and any(hint in message for hint in _REASONING_HINTS)
    )


def _has_reasoning_params(params: dict) -> bool:
    extra = params.get("extra_body")
    return (
        "reasoning_effort" in params
        or "thinking" in params
        or (isinstance(extra, dict) and "thinking" in extra)
    )


def _drop_reasoning_params(params: dict) -> None:
    params.pop("reasoning_effort", None)
    params.pop("thinking", None)
    extra = params.get("extra_body")
    if isinstance(extra, dict):
        extra.pop("thinking", None)
        if not extra:
            params.pop("extra_body", None)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    arguments_error: str | None = None


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    # Provider finish reason: "stop", "length", "tool_calls", ...
    # "length" means the completion hit max_tokens and may be incomplete.
    finish_reason: str = ""
    reasoning_content: str = ""

    @property
    def message(self) -> dict:
        """Convert to OpenAI message format for appending to history."""
        msg: dict = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            msg["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.arguments),
                    },
                }
                for tc in self.tool_calls
            ]
        return msg


# pricing per million tokens: (input, output)
# sources: openai.com/api/pricing, api-docs.deepseek.com, platform.claude.com,
#          platform.moonshot.ai, alibabacloud.com/help/en/model-studio
_PRICING = {
    # OpenAI - current flagships
    "gpt-5.4": (2.5, 15),
    "gpt-5.4-mini": (0.75, 4.5),
    "gpt-5.4-nano": (0.2, 1.25),
    "o4-mini": (1.1, 4.4),
    # OpenAI - previous gen (still widely used)
    "gpt-4.1": (2, 8),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4.1-nano": (0.1, 0.4),
    "gpt-4o": (2.5, 10),
    "gpt-4o-mini": (0.15, 0.6),
    # DeepSeek
    "deepseek-chat": (0.27, 1.10),
    "deepseek-reasoner": (0.55, 2.19),
    "deepseek-flash": (0.15, 0.60),
    # Anthropic Claude
    "claude-opus-4-6": (5, 25),
    "claude-sonnet-4-6": (3, 15),
    "claude-haiku-4-5": (1, 5),
    # Alibaba Qwen
    "qwen3-max": (0.78, 3.9),
    "qwen3-plus": (0.26, 0.78),
    "qwen-max": (0.78, 3.9),
    # Moonshot Kimi
    "kimi-k2.5": (0.6, 3),
}


def _pricing_for(model: str) -> tuple[float, float] | None:
    """Match exact names and provider-prefixed names such as ``anthropic/...``."""

    if model in _PRICING:
        return _PRICING[model]
    normalized = model.rsplit("/", 1)[-1].lower()
    direct = {name.lower(): value for name, value in _PRICING.items()}
    if normalized in direct:
        return direct[normalized]
    matches = [
        (name, value) for name, value in direct.items() if normalized.endswith(name)
    ]
    if not matches:
        return None
    return max(matches, key=lambda item: len(item[0]))[1]


class LLM:
    # Defaults as class attributes so instances built via ``__new__`` (tests,
    # embeddings, adapters) still answer the effort API.
    reasoning_effort = "auto"
    reasoning_note = ""

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str | None = None,
        timeout: float | None = None,
        reasoning_effort: str = "auto",
        **kwargs,
    ):
        self.model = model
        if base_url:
            base_url = base_url.rstrip("/")
            if not base_url.endswith("/v1") and "/v1" not in base_url:
                base_url = f"{base_url}/v1"
        client_kwargs: dict = {"api_key": api_key, "base_url": base_url}
        if timeout is not None:
            client_kwargs["timeout"] = timeout
        self.client = OpenAI(**client_kwargs)
        self.extra = kwargs  # temperature, max_tokens, etc.
        self.reasoning_effort = normalise_effort(reasoning_effort)
        self.reasoning_note = ""
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_reported_cost = 0.0
        self.last_finish_reason = ""

    def set_reasoning_effort(self, effort: str) -> str:
        """Change how hard the model should think (applies to later calls)."""

        self.reasoning_effort = normalise_effort(effort)
        self.reasoning_note = ""
        return self.reasoning_effort

    def _reasoning_params(self) -> dict:
        return openai_reasoning_params(
            getattr(self, "reasoning_effort", "auto"), self.model
        )

    def _apply_reasoning(self, params: dict) -> None:
        for key, value in self._reasoning_params().items():
            if key == "extra_body" and isinstance(params.get("extra_body"), dict):
                merged = dict(params["extra_body"])
                merged.update(value)
                params["extra_body"] = merged
            elif key == "max_tokens" and key in params:
                params[key] = max(int(params[key]), int(value))
            else:
                params[key] = value

    def _open_stream(self, params: dict, *, include_usage: bool = True):
        """Create the stream, dropping optional fields the provider rejects.

        Both ``stream_options`` and the thinking-effort fields are extensions;
        a compatible server may answer 400 for either.  Each is retried once
        without the offending field, and the effort failure is recorded so
        ``/effort`` can explain that the provider ignored the setting.
        """

        if include_usage:
            params.setdefault("stream_options", {"include_usage": True})
        for _ in range(2):
            try:
                return self._call_with_retry(params)
            except (APIError, TypeError) as exc:
                if "stream_options" in params and _unsupported_parameter(
                    exc, "stream_options"
                ):
                    params.pop("stream_options", None)
                    continue
                if _has_reasoning_params(params) and _mentions_reasoning(exc):
                    _drop_reasoning_params(params)
                    self.reasoning_note = (
                        f"provider rejected reasoning_effort="
                        f"{getattr(self, 'reasoning_effort', 'auto')}; "
                        "using its default"
                    )
                    continue
                raise
        return self._call_with_retry(params)

    @property
    def reported_cost(self) -> float | None:
        """Cost reported by the provider, when its usage payload includes one."""

        return self.total_reported_cost or None

    @property
    def estimated_cost(self) -> float | None:
        """Rough cost estimate in USD. Returns None if model not in pricing table."""
        pricing = _pricing_for(self.model)
        if not pricing:
            return None
        input_rate, output_rate = pricing
        return (
            self.total_prompt_tokens * input_rate / 1_000_000
            + self.total_completion_tokens * output_rate / 1_000_000
        )

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token=None,
        on_reasoning=None,
        tool_choice: str | None = None,
    ) -> LLMResponse:
        """Send messages, stream back response, handle tool calls."""
        params: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            **self.extra,
        }
        if tools:
            params["tools"] = tools
            if tool_choice is not None:
                params["tool_choice"] = tool_choice
        self._apply_reasoning(params)

        # stream_options and thinking-effort fields are extensions; a
        # compatible server may reject either one.
        stream = self._open_stream(params)

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tc_map: dict[int, dict] = {}  # index -> {id, name, arguments_str}
        prompt_tok = 0
        completion_tok = 0
        reported_cost = 0.0
        finish_reason = ""

        for chunk in stream:
            # usage info comes in the final chunk
            usage = getattr(chunk, "usage", None)
            if usage:
                prompt_tok = usage.prompt_tokens
                completion_tok = usage.completion_tokens
                reported_cost = float(getattr(usage, "cost", 0.0) or 0.0)

            if not chunk.choices:
                continue
            if getattr(chunk.choices[0], "finish_reason", None):
                finish_reason = chunk.choices[0].finish_reason
            delta = chunk.choices[0].delta

            # accumulate reasoning content (DeepSeek, OpenAI o-series)
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning is None and hasattr(delta, "model_extra") and delta.model_extra:
                reasoning = delta.model_extra.get("reasoning_content")
            if reasoning:
                reasoning_parts.append(reasoning)
                if on_reasoning:
                    on_reasoning(reasoning)

            # accumulate text
            if delta.content:
                content_parts.append(delta.content)
                if on_token:
                    on_token(delta.content)

            # accumulate tool calls across chunks
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tc_map:
                        tc_map[idx] = {"id": "", "name": "", "args": ""}
                    if tc_delta.id:
                        tc_map[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc_map[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tc_map[idx]["args"] += tc_delta.function.arguments

        # parse accumulated tool calls
        parsed: list[ToolCall] = []
        for idx in sorted(tc_map):
            raw = tc_map[idx]
            try:
                args = json.loads(raw["args"])
                error = None
            except (json.JSONDecodeError, KeyError) as exc:
                args = {}
                error = str(exc)
            parsed.append(ToolCall(
                id=raw["id"], name=raw["name"], arguments=args, arguments_error=error
            ))

        self.total_prompt_tokens += prompt_tok
        self.total_completion_tokens += completion_tok
        self.total_reported_cost = getattr(self, "total_reported_cost", 0.0) + reported_cost
        self.last_finish_reason = finish_reason

        return LLMResponse(
            content="".join(content_parts),
            tool_calls=parsed,
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
            finish_reason=finish_reason,
            reasoning_content="".join(reasoning_parts),
        )

    def _call_with_retry(self, params: dict, max_retries: int = 3):
        """Retry on transient errors with exponential backoff."""
        for attempt in range(max_retries):
            try:
                return self.client.chat.completions.create(**params)
            except (RateLimitError, APITimeoutError, APIConnectionError):
                if attempt == max_retries - 1:
                    raise
                wait = 2 ** attempt
                time.sleep(wait)
            except APIError as e:
                # 5xx = server error, retry; 4xx = client error, don't
                if e.status_code and e.status_code >= 500 and attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise


class LiteLLM(LLM):
    """LLM backend via LiteLLM, supporting 100+ providers.

    Use this when your target provider is NOT OpenAI-compatible
    (AWS Bedrock, Google Vertex, Cohere, etc.) or when you want
    a single interface to switch between any provider by changing
    the model string.

    Set CASTORCODER_PROVIDER=litellm and use LiteLLM model strings
    like ``anthropic/claude-3-haiku``, ``bedrock/anthropic.claude-v2``,
    ``vertex_ai/gemini-pro``, etc.
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        reasoning_effort: str = "auto",
        **kwargs,
    ):
        # skip LLM.__init__ which creates an OpenAI client
        self.model = model
        self.api_key = api_key
        self.base_url = base_url
        self.extra = kwargs
        self.reasoning_effort = normalise_effort(reasoning_effort)
        self.reasoning_note = ""
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.total_reported_cost = 0.0
        self.last_finish_reason = ""

    def _reasoning_params(self) -> dict:
        params = litellm_reasoning_params(
            getattr(self, "reasoning_effort", "auto"), self.model
        )
        thinking = params.get("thinking")
        if isinstance(thinking, dict) and thinking.get("type") == "enabled":
            budget = int(thinking.get("budget_tokens", 0))
            configured = int(self.extra.get("max_tokens") or 0)
            if configured and configured <= budget:
                # Anthropic requires max_tokens to exceed the thinking budget.
                params["max_tokens"] = budget + 1_024
        return params

    @property
    def reported_cost(self) -> float | None:
        return self.total_reported_cost or None

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        on_token=None,
        on_reasoning=None,
        tool_choice: str | None = None,
    ) -> LLMResponse:
        """Send messages via litellm, stream back response, handle tool calls."""
        params: dict = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            **self.extra,
        }
        if tools:
            params["tools"] = tools
            if tool_choice is not None:
                params["tool_choice"] = tool_choice
        self._apply_reasoning(params)

        stream = self._open_stream(params, include_usage=False)

        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tc_map: dict[int, dict] = {}
        prompt_tok = 0
        completion_tok = 0
        reported_cost = 0.0
        finish_reason = ""

        for chunk in stream:
            usage = getattr(chunk, "usage", None)
            if usage:
                prompt_tok = getattr(usage, "prompt_tokens", 0) or 0
                completion_tok = getattr(usage, "completion_tokens", 0) or 0
                reported_cost = float(getattr(usage, "cost", 0.0) or 0.0)

            if not getattr(chunk, "choices", None):
                continue
            if getattr(chunk.choices[0], "finish_reason", None):
                finish_reason = chunk.choices[0].finish_reason
            delta = chunk.choices[0].delta

            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning is None and getattr(delta, "model_extra", None):
                reasoning = delta.model_extra.get("reasoning_content")
            if reasoning:
                reasoning_parts.append(reasoning)
                if on_reasoning:
                    on_reasoning(reasoning)

            if getattr(delta, "content", None):
                content_parts.append(delta.content)
                if on_token:
                    on_token(delta.content)

            if getattr(delta, "tool_calls", None):
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tc_map:
                        tc_map[idx] = {"id": "", "name": "", "args": ""}
                    if tc_delta.id:
                        tc_map[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tc_map[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tc_map[idx]["args"] += tc_delta.function.arguments

        parsed: list[ToolCall] = []
        for idx in sorted(tc_map):
            raw = tc_map[idx]
            try:
                args = json.loads(raw["args"])
                error = None
            except (json.JSONDecodeError, KeyError) as exc:
                args = {}
                error = str(exc)
            parsed.append(ToolCall(
                id=raw["id"], name=raw["name"], arguments=args, arguments_error=error
            ))

        self.total_prompt_tokens += prompt_tok
        self.total_completion_tokens += completion_tok
        self.total_reported_cost = getattr(self, "total_reported_cost", 0.0) + reported_cost
        self.last_finish_reason = finish_reason

        return LLMResponse(
            content="".join(content_parts),
            tool_calls=parsed,
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
            finish_reason=finish_reason,
            reasoning_content="".join(reasoning_parts),
        )

    def _call_with_retry(self, params: dict, max_retries: int = 3):
        """Retry on transient errors with exponential backoff via litellm."""
        import litellm

        params["drop_params"] = True
        if self.api_key:
            params["api_key"] = self.api_key
        if self.base_url:
            params["api_base"] = self.base_url

        for attempt in range(max_retries):
            try:
                return litellm.completion(**params)
            except Exception as e:
                err = str(e).lower()
                is_transient = any(
                    kw in err
                    for kw in ["rate_limit", "timeout", "connection", "502", "503", "529"]
                )
                is_server = any(kw in err for kw in ["500", "502", "503", "504"])
                if (is_transient or is_server) and attempt < max_retries - 1:
                    time.sleep(2 ** attempt)
                else:
                    raise
