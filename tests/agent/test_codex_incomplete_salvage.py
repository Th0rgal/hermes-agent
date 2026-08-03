"""Retry-exhaustion salvage: deliver the turn's real text, not the sentinel."""

from agent.conversation_loop import (
    _CODEX_INCOMPLETE_NUDGE,
    _salvage_visible_turn_answer,
)


def test_salvages_latest_assistant_text_within_turn():
    messages = [
        {"role": "user", "content": "comment ça avance ?"},
        {"role": "assistant", "content": "Voici le tableau récapitulatif…"},
        {"role": "user", "content": _CODEX_INCOMPLETE_NUDGE},
        {"role": "assistant", "content": ""},
    ]
    assert (
        _salvage_visible_turn_answer(messages)
        == "Voici le tableau récapitulatif…"
    )


def test_returns_empty_when_turn_produced_no_visible_text():
    messages = [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "", "reasoning_content": "hidden"},
        {"role": "user", "content": _CODEX_INCOMPLETE_NUDGE},
        {"role": "assistant", "content": ""},
    ]
    assert _salvage_visible_turn_answer(messages) == ""


def test_does_not_cross_the_previous_user_turn():
    messages = [
        {"role": "assistant", "content": "Old answer from a previous turn."},
        {"role": "user", "content": "nouvelle question"},
        {"role": "assistant", "content": ""},
    ]
    assert _salvage_visible_turn_answer(messages) == ""


def test_tolerates_non_dict_and_tool_messages():
    messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "Réponse utile."},
        {"role": "tool", "content": "tool output"},
        None,
        {"role": "assistant", "content": None},
    ]
    assert _salvage_visible_turn_answer(messages) == "Réponse utile."
