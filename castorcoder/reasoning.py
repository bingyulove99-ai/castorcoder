"""Thinking-effort control for model providers.

Claude Code lets the user choose how hard the model should think before it
answers.  CastorCoder exposes the same idea as ``/effort`` and the
``--effort`` flag, then translates one neutral level into whichever parameter
the active provider understands:

* OpenAI-compatible servers (OpenAI, DeepSeek, Qwen, ...) use
  ``reasoning_effort``.  DeepSeek documents ``none``/``low``/``high``/``max``
  and maps ``medium`` to ``high``; OpenAI maps ``max`` to ``high``.
* Anthropic models reached through LiteLLM use
  ``thinking={"type": "enabled", "budget_tokens": ...}``.

``auto`` sends nothing at all and is the default, so an existing setup keeps
behaving exactly as before until the user asks for a level.  Providers that
reject the parameter are handled in :mod:`castorcoder.llm`: the request is
retried once without it instead of failing the turn.
"""

from __future__ import annotations

EFFORT_LEVELS = ("auto", "off", "low", "medium", "high", "max")
DEFAULT_EFFORT = "auto"

# Values accepted by other tools (and by Claude Code) but not worth showing in
# the picker.  They are normalised to the nearest level above.
_ALIASES = {
    "default": "auto",
    "provider": "auto",
    "none": "off",
    "disabled": "off",
    "minimal": "low",
    "xhigh": "high",
    "ultra": "max",
}

EFFORT_SUMMARY = {
    "auto": "provider default - send no effort hint",
    "off": "disable thinking where the provider supports it",
    "low": "fastest - minimal reasoning",
    "medium": "balanced reasoning",
    "high": "deeper reasoning for hard tasks",
    "max": "deepest reasoning the provider allows",
}

# Anthropic's thinking budget must leave room for the answer itself.
ANTHROPIC_BUDGETS = {
    "low": 1_024,
    "medium": 4_096,
    "high": 8_192,
    "max": 16_384,
}


def canonical_effort(value: object) -> str | None:
    """Return the canonical level for ``value``, or ``None`` when unknown."""

    text = str(value or "").strip().lower()
    text = _ALIASES.get(text, text)
    return text if text in EFFORT_LEVELS else None


def normalise_effort(value: object) -> str:
    """Best-effort normalisation used at boundaries that must not fail."""

    return canonical_effort(value) or DEFAULT_EFFORT


def _family(model: str) -> str:
    name = str(model or "").strip().lower()
    if "claude" in name or name.startswith("anthropic/"):
        return "anthropic"
    if name.startswith(("gpt-", "o1", "o3", "o4", "openai/")):
        return "openai"
    if "deepseek" in name:
        return "deepseek"
    return ""


def openai_reasoning_params(effort: object, model: str) -> dict:
    """Parameters for an OpenAI-compatible ``chat.completions`` request."""

    level = normalise_effort(effort)
    if level == "auto":
        return {}
    if level == "off":
        # DeepSeek treats "none" as "thinking off"; OpenAI rejects the value
        # and the request helper retries without the parameter.
        return {"reasoning_effort": "none"}
    if level == "max" and _family(model) == "openai":
        # OpenAI's highest documented value is "high".
        return {"reasoning_effort": "high"}
    return {"reasoning_effort": level}


def litellm_reasoning_params(effort: object, model: str) -> dict:
    """Parameters for the LiteLLM backend (Anthropic budgets where relevant)."""

    level = normalise_effort(effort)
    if level == "auto":
        return {}
    if _family(model) == "anthropic":
        if level == "off":
            # Omitting ``thinking`` is how Anthropic models stay non-thinking.
            return {}
        return {
            "thinking": {
                "type": "enabled",
                "budget_tokens": ANTHROPIC_BUDGETS[level],
            }
        }
    return openai_reasoning_params(level, model)
