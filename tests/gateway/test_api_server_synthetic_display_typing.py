"""``body["hermes"]`` display typing is accepted only on an authenticated
session continuation and only with a sane kind."""

from gateway.platforms.api_server import _synthetic_turn_display_typing


def test_typing_requires_authenticated_continuation():
    body = {"hermes": {"display_kind": "mission_callback_wake"}}
    assert _synthetic_turn_display_typing(body, False) is None
    assert _synthetic_turn_display_typing(body, True) == ("mission_callback_wake", None)


def test_typing_carries_metadata_and_rejects_junk_kinds():
    body = {
        "hermes": {
            "display_kind": "mission_callback_wake",
            "display_metadata": {"mission_id": "m1", "status": "completed"},
        }
    }
    assert _synthetic_turn_display_typing(body, True) == (
        "mission_callback_wake",
        {"mission_id": "m1", "status": "completed"},
    )
    for junk in ["", "Mission Callback", "x" * 80, "../etc", 42]:
        assert _synthetic_turn_display_typing({"hermes": {"display_kind": junk}}, True) is None
    assert _synthetic_turn_display_typing({"hermes": "nope"}, True) is None
    assert _synthetic_turn_display_typing({}, True) is None
