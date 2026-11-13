"""Shrink tool results the model has already read.

The loop re-sends the whole conversation on every model call.  A large tool
result therefore rides along on every round after the one that produced it,
which is both slow and expensive.  Waku's loop solves this with a ``trim``
step that cuts *old* results only; CastorCoder adopts the same rule here.

The newest round's results are never touched: the model has not read them
yet.  Everything older than the last assistant message may be cut once, and a
cut result is short enough that it is never cut twice.
"""

from __future__ import annotations

# A result at or below this length is left whole: the replacement note would
# cost as much as the text it saves.
LONG_CHARS = 4_000
# What a cut result keeps: its opening, where search hits and file headers live.
KEEP_CHARS = 2_000
NOTE = (
    "\n\n[Trimmed from {total:,} characters after you read it. Results you have "
    "already read are shortened before each later model call; copy anything you "
    "still need into your next message.]"
)


def shrink_seen(
    messages: list[dict],
    *,
    long_chars: int = LONG_CHARS,
    keep_chars: int = KEEP_CHARS,
) -> int:
    """Cut old, long tool results in place. Returns how many were cut.

    Only messages before the most recent assistant message are eligible.  In
    OpenAI's message format a tool result is its own ``role="tool"`` message,
    and a round may contain several of them, so the boundary is the last
    assistant message rather than ``messages[-1]``.
    """

    boundary = -1
    for index, message in enumerate(messages):
        if isinstance(message, dict) and message.get("role") == "assistant":
            boundary = index
    if boundary <= 0:
        return 0

    trimmed = 0
    for message in messages[:boundary]:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        content = message.get("content")
        if isinstance(content, str) and len(content) > long_chars:
            message["content"] = content[:keep_chars] + NOTE.format(total=len(content))
            trimmed += 1
    return trimmed
