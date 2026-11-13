"""Ask the human a clarifying question instead of guessing forever.

Coding loops often stall because a requirement is genuinely ambiguous.  Claude
Code has a dedicated question tool; CastorCoder previously had no way to stop
and ask, so a model would try one interpretation after another.  The CLI binds
a callback to this tool; library users can supply their own, and a
non-interactive run fails fast with instructions instead of blocking.
"""

from __future__ import annotations

from collections.abc import Callable

from .base import Tool

AskCallback = Callable[[str, list[str]], str]


class AskUserTool(Tool):
    name = "ask_user"
    description = (
        "Ask the user a focused clarifying question when progress is blocked by "
        "a genuine ambiguity or missing credential. Do not use it for choices "
        "you can resolve from the repository."
    )
    risk = "read"
    parallel_safe = False
    timeout_s = None
    parameters = {
        "type": "object",
        "properties": {
            "question": {
                "type": "string",
                "description": "One concrete question, with the context needed to answer it",
            },
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional short suggested answers",
            },
        },
        "required": ["question"],
        "additionalProperties": False,
    }

    def __init__(self, callback: AskCallback | None = None) -> None:
        self.callback = callback

    def execute(self, question: str, options: list[str] | None = None) -> str:
        question = (question or "").strip()
        if not question:
            return "Error: question must not be empty"
        if self.callback is None:
            return (
                "Error: no interactive user is available in this run. "
                "Make the most reasonable assumption explicit, proceed, and note "
                "the assumption in your final answer."
            )
        try:
            answer = self.callback(question, list(options or []))
        except Exception as exc:
            return f"Error: could not ask the user: {type(exc).__name__}: {exc}"
        answer = str(answer or "").strip()
        return f"User answered: {answer}" if answer else "User gave no answer."
