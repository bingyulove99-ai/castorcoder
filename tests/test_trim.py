"""Old tool results are shortened without touching the newest round."""

from __future__ import annotations

from castorcoder.trim import shrink_seen


def test_old_long_tool_results_are_trimmed():
    messages = [
        {"role": "user", "content": "go"},
        {"role": "assistant", "content": "first call"},
        {"role": "tool", "tool_call_id": "1", "content": "A" * 5_000},
        {"role": "assistant", "content": "second call"},
        {"role": "tool", "tool_call_id": "2", "content": "B" * 5_000},
    ]

    count = shrink_seen(messages, long_chars=1_000, keep_chars=100)

    assert count == 1
    assert len(messages[2]["content"]) < 400
    assert "Trimmed from 5,000 characters" in messages[2]["content"]
    # The newest round's result is what the model just read; leave it alone.
    assert messages[4]["content"] == "B" * 5_000


def test_short_results_and_no_assistant_message_are_untouched():
    messages = [
        {"role": "tool", "tool_call_id": "1", "content": "short"},
    ]
    assert shrink_seen(messages, long_chars=1_000) == 0
    assert messages[0]["content"] == "short"
