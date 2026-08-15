"""Regression tests for autonomous-delivery hygiene (D1).

Covers the Telegram leaks where a controller tick that meant to stay silent
emitted a truncated ``[SILENT`` after a reasoning/tool-call blob, and where
machine-only trailers must be stripped from human-facing platform deliveries.
"""

from gateway.response_filters import (
    is_autonomous_silence_response,
    is_intentional_silence_response,
    sanitize_platform_delivery,
)


# ── Truncated / malformed bracket sentinel suppression (FUITE A/B) ────────────

def test_truncated_bracket_sentinel_last_line_is_silence():
    leaked = (
        "Confirmed: the mcp__sandboxed_assistant__* tool family is not directly "
        "invocable from this webhook turn.\n\n"
        "I have no way to verify PR #65 or the immutable head.\n\n"
        "[SILENT"
    )
    assert is_autonomous_silence_response(leaked) is True


def test_truncated_bracket_sentinel_with_tool_narration_is_silence():
    leaked = (
        '[tool call: terminal]\n'
        '{"name":"terminal","arguments":{"cmd":"gh pr view 64"}}\n\n'
        "[SILENT"
    )
    assert is_autonomous_silence_response(leaked) is True


def test_truncated_bracket_sentinel_first_line_is_silence():
    assert is_autonomous_silence_response("[SILENT\nsome trailing note") is True


def test_bare_word_silent_in_prose_still_delivers():
    # A leading-bracket requirement keeps ordinary prose safe.
    assert is_autonomous_silence_response("Silent retry succeeded, all good.") is False


def test_ctrl_trailer_is_not_mistaken_for_silence():
    assert (
        is_autonomous_silence_response("[CTRL: coldcard | mode=blocked | wait=1]")
        is False
    )


def test_interactive_matcher_unchanged_by_truncated_sentinel():
    # The strict interactive rule must NOT swallow a reasoning blob.
    leaked = "Here is a long report.\n\n[SILENT"
    assert is_intentional_silence_response(leaked) is False


# ── Platform-delivery trailer / tool-call stripping ──────────────────────────

def test_sanitize_strips_ctrl_and_state_signature():
    raw = (
        "SCANNER DEAD - checkpoint=2755198976\n\n"
        "[CTRL: coldcard-rng-cracker | mode=blocked | wait=1 | next=monitor] "
        "[STATE_SIGNATURE: coldcard-rng-cracker|scan|none|scanner-dead|monitor]"
    )
    out = sanitize_platform_delivery(raw)
    assert "CTRL" not in out
    assert "STATE_SIGNATURE" not in out
    assert "SCANNER DEAD - checkpoint=2755198976" in out


def test_sanitize_strips_tool_call_narration():
    raw = (
        "Checking PR status.\n"
        '[tool call: terminal]\n'
        '{"name":"terminal","arguments":{"cmd":"gh pr view 64"}}\n'
        "Done."
    )
    out = sanitize_platform_delivery(raw)
    assert "tool call" not in out
    assert '"name"' not in out
    assert "Checking PR status." in out
    assert "Done." in out


def test_sanitize_leaves_clean_report_untouched():
    raw = "Bloom built: 106M HASH160. Scan at 64%."
    assert sanitize_platform_delivery(raw) == raw


def test_sanitize_empty_when_only_scaffolding():
    raw = "[CTRL: x | mode=blocked] [STATE_SIGNATURE: x|y|z]"
    assert sanitize_platform_delivery(raw) == ""


def test_sanitize_strips_empty_tag_ctrl_line():
    raw = (
        "Action Thomas : aucune pour l’instant.\n\n"
        "[CTRL:] #2332 repair active; both workers recovered after server restart; "
        "waiting for successor head or concrete blocker.\n\n"
        "[STATE_SIGNATURE: verity|pr2332|none|repair-active|source|successor-head-or-blocker]"
    )
    out = sanitize_platform_delivery(raw)
    assert "CTRL" not in out
    assert "STATE_SIGNATURE" not in out
    assert "workers recovered" not in out
    assert "Action Thomas : aucune pour l’instant." in out
