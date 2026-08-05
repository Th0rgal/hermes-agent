"""The webhook lane must consult the skill guard before injecting a body.

This was the unidentified duplicate-injection path. `agent.skill_commands`
guards the slash path, spans continuation chains (#50), and warns when it
cannot run (#51) — and duplicates kept appearing with **no warning at all**:

    17:10  94 401 ch  →  20260805_162321_a1c9e2
    17:13  94 410 ch  →  20260805_162321_a1c9e2
    17:15  94 401 ch  →  20260805_162321_a1c9e2

Three 94 KB copies into one pinned conversation in five minutes: three Lido
repair missions completed back to back, and every `mission-complete` callback
re-injected the route's skill unconditionally. 19 injections between 16:00 and
18:23. The guard was silent because this lane never called it.

An unpinned delivery keeps the full body — its per-delivery session is brand
new and has no earlier copy to point at.
"""

import inspect

from gateway.platforms import webhook as webhook_module


class TestTheLaneIsWired:
    """Source-level pins: the handler is not unit-instantiable without a
    gateway, so assert the wiring the fix consists of."""

    def _handler_source(self):
        return inspect.getsource(webhook_module.WebhookPlatform._handle_webhook)

    def test_the_guard_is_consulted(self):
        assert "_skill_already_loaded" in self._handler_source()

    def test_the_reference_template_is_the_shared_one(self):
        # The template must stay skill_commands' own: the "already loaded"
        # reference form is what the elision projections and the guard itself
        # recognise. A locally spelled variant would drift.
        assert "_SKILL_ALREADY_LOADED_TEMPLATE" in self._handler_source()

    def test_the_pin_resolves_before_the_injection(self):
        # The guard needs the pinned session to search. If injection ran
        # first, the guard would have nothing to consult — which is exactly
        # the bug.
        source = self._handler_source()
        assert source.index("_resolve_pinned_session") < source.index(
            "_skill_already_loaded"
        )

    def test_the_guard_is_gated_on_a_pin(self):
        # An unpinned delivery goes to a fresh per-delivery session with no
        # earlier copy; the body must still be sent there.
        source = self._handler_source()
        assert "pinned_session_id and _skill_already_loaded" in source

    def test_the_webhook_prompt_survives_the_reference_path(self):
        # The reference replaces the BODY, never the delivery's own prompt —
        # dropping the prompt would deliver a bare "already loaded" note and
        # nothing else.
        source = self._handler_source()
        assert 'f"{reference}\\n\\n{prompt}"' in source


class TestTheGuardFindsTheWebhookBodies:
    """End-to-end against a real DB: the exact rows this lane writes are the
    rows the guard must find on the next callback."""

    def test_a_previous_callback_body_counts_as_loaded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))
        from hermes_state import SessionDB
        from agent.skill_commands import (
            _SINGLE_SKILL_MARKER,
            _SKILL_INVOCATION_PREFIX,
            _skill_already_loaded,
        )

        db = SessionDB(db_path=tmp_path / "state.db")
        try:
            db.create_session("pinned-1", source="webhook")
            db.append_message(
                "pinned-1",
                "user",
                f'{_SKILL_INVOCATION_PREFIX}"sandboxed-sh-missions" skill, '
                f"indicating they want you to follow its instructions. "
                f"{_SINGLE_SKILL_MARKER}\n\nthe 94 KB body",
            )
        finally:
            db.close()

        assert _skill_already_loaded("pinned-1", "sandboxed-sh-missions")

    def test_a_fresh_pinned_session_still_gets_the_body(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))
        from hermes_state import SessionDB
        from agent.skill_commands import _skill_already_loaded

        db = SessionDB(db_path=tmp_path / "state.db")
        try:
            db.create_session("pinned-2", source="webhook")
        finally:
            db.close()

        assert not _skill_already_loaded("pinned-2", "sandboxed-sh-missions")
