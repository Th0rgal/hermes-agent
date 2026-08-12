"""Tier-A last-resort tool-result elision fallback.

Covers ``agent.context_compressor.elide_bulky_tool_messages`` — the escape hatch
the overflow handler calls before surfacing "Cannot compress further" when the
normal summariser is exhausted but a huge tool_result still sits in a protected
turn it cannot touch.
"""

import agent.context_compressor as cc
from agent.context_compressor import elide_bulky_tool_messages, _TOOL_ELISION_MARKER


def _char_estimator(monkeypatch):
    # Deterministic, tokenizer-free estimate: ~4 chars/token. Patched on the
    # symbol the function imports lazily (agent.model_metadata).
    import agent.model_metadata as mm

    monkeypatch.setattr(
        mm,
        "estimate_messages_tokens_rough",
        lambda messages: sum(len(str(m.get("content", ""))) for m in messages) // 4,
    )


def test_elides_old_bulky_tool_and_keeps_recent_intact(monkeypatch):
    _char_estimator(monkeypatch)
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "tool_call_id": "1", "content": "X" * 40000},  # OLD, huge
        {"role": "assistant", "content": "thinking"},
        {"role": "tool", "tool_call_id": "2", "content": "Y" * 40000},  # RECENT, keep
    ]
    before = sum(len(str(m.get("content", ""))) for m in messages) // 4
    out, saved = elide_bulky_tool_messages(messages, target_tokens=8000)

    assert saved > 0
    assert _TOOL_ELISION_MARKER in out[3]["content"]  # old tool elided
    assert len(out[3]["content"]) < 40000
    assert out[5]["content"] == "Y" * 40000  # most-recent tool preserved verbatim
    after = sum(len(str(m.get("content", ""))) for m in out) // 4
    assert after < before


def test_converges_second_pass_is_noop(monkeypatch):
    _char_estimator(monkeypatch)
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "1"}]},
        {"role": "tool", "tool_call_id": "1", "content": "X" * 40000},
        {"role": "assistant", "content": "a"},
        {"role": "tool", "tool_call_id": "2", "content": "keep"},
    ]
    out, saved = elide_bulky_tool_messages(messages, target_tokens=2000)
    assert saved > 0
    out2, saved2 = elide_bulky_tool_messages(out, target_tokens=2000)
    assert saved2 == 0  # already-elided messages are skipped → converges


def test_under_budget_is_noop(monkeypatch):
    _char_estimator(monkeypatch)
    messages = [{"role": "tool", "content": "z" * 100}]
    out, saved = elide_bulky_tool_messages(messages, target_tokens=8000)
    assert saved == 0
    assert out is messages


def test_no_tool_messages_is_noop(monkeypatch):
    _char_estimator(monkeypatch)
    messages = [{"role": "user", "content": "u" * 80000}]  # over budget but no tool
    out, saved = elide_bulky_tool_messages(messages, target_tokens=1000)
    assert saved == 0
