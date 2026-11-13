"""Turn receipts and the persistent usage ledger."""

from __future__ import annotations

import json

from castorcoder.metrics import TurnReceipt, UsageLedger


def test_receipt_line_has_tokens_cost_and_timing():
    receipt = TurnReceipt(
        run_id="run_1",
        prompt_tokens=12_000,
        completion_tokens=1_900,
        cost_usd=0.064,
        rounds=3,
        tool_calls=4,
        elapsed_ms=8_200,
    )
    line = receipt.line()
    assert "12,000 in / 1,900 out" in line
    assert "$0.0640" in line
    assert "3 rounds" in line
    assert "8.2s" in line


def test_ledger_appends_json_lines_and_summarises(tmp_path):
    path = tmp_path / "usage.jsonl"
    ledger = UsageLedger(path)
    assert ledger.append(TurnReceipt(prompt_tokens=100, completion_tokens=10, cost_usd=0.01,
                                     tool_calls=2, tool_failures=1))
    assert ledger.append(TurnReceipt(prompt_tokens=200, completion_tokens=20, cost_usd=0.02,
                                     tool_calls=3))

    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2
    assert rows[0]["prompt_tokens"] == 100
    assert "ts" in rows[0]

    summary = ledger.summary()
    assert summary["runs"] == 2
    assert summary["prompt_tokens"] == 300
    assert summary["completion_tokens"] == 30
    assert summary["cost_usd"] == 0.03
    assert summary["tool_calls"] == 5
    assert summary["failures"] == 1
