"""Pure delivery-routing resolvers extracted from scheduler.py."""

from cron.delivery_routing import _normalize_deliver_value, _resolve_origin


def test_resolve_origin_accepts_a_well_formed_dict():
    origin = {"platform": "telegram", "chat_id": "123", "extra": "kept"}
    assert _resolve_origin({"origin": origin}) == origin


def test_resolve_origin_rejects_non_dict_origins():
    # The #18722 crash guard: a string/int/list origin is "missing", not a crash.
    for bad in ("combined-digest-replaces-x", 42, ["telegram"], None):
        assert _resolve_origin({"origin": bad}) is None
    assert _resolve_origin({}) is None


def test_resolve_origin_requires_both_platform_and_chat_id():
    assert _resolve_origin({"origin": {"platform": "telegram"}}) is None
    assert _resolve_origin({"origin": {"chat_id": "123"}}) is None


def test_normalize_deliver_value_passthrough_and_falsy():
    assert _normalize_deliver_value("telegram:-1001:17") == "telegram:-1001:17"
    assert _normalize_deliver_value("") == "local"
    assert _normalize_deliver_value(None) == "local"


def test_normalize_deliver_value_flattens_lists():
    assert _normalize_deliver_value(["telegram"]) == "telegram"
    assert _normalize_deliver_value(["telegram", "local"]) == "telegram,local"
    assert _normalize_deliver_value([]) == "local"
    assert _normalize_deliver_value([" ", ""]) == "local"


def test_reexport_from_scheduler_is_the_same_object():
    from cron.scheduler import _resolve_origin as via_scheduler

    assert via_scheduler is _resolve_origin
