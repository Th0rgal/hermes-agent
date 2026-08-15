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


def test_protect_last_tool_false_elides_recent_tool(monkeypatch):
    _char_estimator(monkeypatch)
    messages = [
        {"role": "user", "content": "q"},
        {"role": "tool", "content": "Y" * 40000},
    ]
    out, saved = elide_bulky_tool_messages(
        messages, target_tokens=1000, protect_last_tool=False
    )
    assert saved > 0
    assert _TOOL_ELISION_MARKER in out[1]["content"]


def test_elide_until_fit_cuts_last_tool_when_first_pass_saves_nothing(monkeypatch):
    from agent.context_compressor import elide_until_fit

    _char_estimator(monkeypatch)
    messages = [
        {"role": "user", "content": "q"},
        {"role": "tool", "content": "Z" * 80000},
    ]
    # First pass protects the only tool and would save 0; later passes must
    # cut it so a 277k overflow cannot dead-end.
    out, saved = elide_until_fit(messages, target_tokens=2000)
    assert saved > 0
    assert _TOOL_ELISION_MARKER in out[1]["content"]
    after = sum(len(str(m.get("content", ""))) for m in out) // 4
    assert after <= 2000


def test_elide_until_fit_elides_bulky_assistant_when_tools_are_already_cut(
    monkeypatch,
):
    from agent.context_compressor import elide_until_fit

    _char_estimator(monkeypatch)
    already = (
        "head\n\n… [tool output elided to fit context: 9,999 chars] …\n\ntail"
    )
    messages = [
        {"role": "user", "content": "q"},
        {"role": "tool", "content": already},
        {"role": "assistant", "content": "A" * 40000},
        {"role": "user", "content": "please continue"},
    ]
    out, saved = elide_until_fit(messages, target_tokens=2000)
    assert saved > 0
    assert _TOOL_ELISION_MARKER in out[2]["content"]
    assert out[3]["content"] == "please continue"
