"""Unit tests for the structured controller-event vocabulary.

Covers: stable event_type strings, structured SILENT suppression defaults,
UI-role mapping, payload round-trips, privacy of rendered output, dedupe
keys, and batcher window/ordering/thread-safety semantics.
"""

import threading

import pytest

from gateway.controller_events import (
    CONTROLLER_WAKEUP,
    PROJECT_PROGRESS,
    RECONCILIATION_EVENT,
    UI_ROLES,
    USER_NOTIFICATION,
    ControllerEventBatcher,
    ControllerWakeup,
    ProjectProgress,
    ReconciliationEvent,
    UserNotification,
    dedupe_key,
    emit_controller_event,
    event_from_payload,
    event_to_payload,
    partition_suppressed,
    render_batch,
    render_event,
    set_controller_event_sink,
    should_suppress_delivery,
    ui_role_for,
)


class TestEventTypes:
    def test_stable_event_type_strings(self):
        assert ReconciliationEvent(kind="x").event_type == "reconciliation_event"
        assert ControllerWakeup(reason="cron_fire").event_type == "controller_wakeup"
        assert ProjectProgress(project="p", stage="s").event_type == "project_progress"
        assert UserNotification(text="hi").event_type == "user_notification"

    def test_module_constants_match(self):
        assert RECONCILIATION_EVENT == "reconciliation_event"
        assert CONTROLLER_WAKEUP == "controller_wakeup"
        assert PROJECT_PROGRESS == "project_progress"
        assert USER_NOTIFICATION == "user_notification"

    def test_events_are_frozen(self):
        event = UserNotification(text="hi")
        with pytest.raises(Exception):
            event.text = "rewritten"

    def test_events_carry_no_platform_routing_fields(self):
        """Privacy: no event type has chat/thread/platform address fields."""
        forbidden = {"chat_id", "thread_id", "platform", "token", "phone"}
        for cls in (ReconciliationEvent, ControllerWakeup, ProjectProgress, UserNotification):
            assert not (set(cls.__dataclass_fields__) & forbidden), cls.__name__


class TestStructuredSilence:
    def test_internal_beats_default_silent(self):
        assert should_suppress_delivery(ReconciliationEvent(kind="silent_suppression"))
        assert should_suppress_delivery(ControllerWakeup(reason="cron_fire"))

    def test_user_facing_beats_default_loud(self):
        assert not should_suppress_delivery(ProjectProgress(project="p", stage="delivered"))
        assert not should_suppress_delivery(UserNotification(text="done"))

    def test_silent_flag_is_explicit_not_marker_parsed(self):
        """Structured silence ignores marker strings in content — the typed
        flag is the only gate."""
        loud = UserNotification(text="[SILENT]")
        assert not should_suppress_delivery(loud)
        demoted = UserNotification(text="real content", silent=True)
        assert should_suppress_delivery(demoted)

    def test_partition_preserves_order(self):
        events = [
            ControllerWakeup(reason="cron_fire", job_id="j1"),
            UserNotification(text="a"),
            ReconciliationEvent(kind="route_migrated"),
            UserNotification(text="b"),
        ]
        deliverable, suppressed = partition_suppressed(events)
        assert [e.text for e in deliverable] == ["a", "b"]
        assert [e.event_type for e in suppressed] == [
            "controller_wakeup",
            "reconciliation_event",
        ]


class TestUiRoles:
    def test_roles_are_valid_dashboard_roles(self):
        for event in (
            ReconciliationEvent(kind="x"),
            ControllerWakeup(reason="r"),
            ProjectProgress(project="p", stage="s"),
            UserNotification(text="t"),
        ):
            assert ui_role_for(event) in UI_ROLES

    def test_specific_mapping(self):
        assert ui_role_for(ReconciliationEvent(kind="x")) == "system"
        assert ui_role_for(ControllerWakeup(reason="r")) == "system"
        assert ui_role_for(ProjectProgress(project="p", stage="s")) == "assistant"
        assert ui_role_for(UserNotification(text="t")) == "assistant"

    def test_controller_events_are_never_user_role(self):
        for event in (
            ReconciliationEvent(kind="x"),
            ControllerWakeup(reason="r"),
            ProjectProgress(project="p", stage="s"),
            UserNotification(text="t"),
        ):
            assert ui_role_for(event) != "user"


class TestPayloadRoundtrip:
    @pytest.mark.parametrize(
        "event",
        [
            ReconciliationEvent(kind="silent_suppression", subject="j1", detail="d", ts=5.0),
            ControllerWakeup(reason="cron_fire", job_id="j1", job_name="n", ts=6.0),
            ProjectProgress(project="alpha", stage="route_resolved", session_id="s1", summary="x", silent=True, ts=7.0),
            UserNotification(text="hi", title="T", priority="high", ts=8.0),
        ],
    )
    def test_roundtrip_identity(self, event):
        assert event_from_payload(event_to_payload(event)) == event

    def test_payload_has_discriminator(self):
        payload = event_to_payload(UserNotification(text="hi"))
        assert payload["event_type"] == "user_notification"

    def test_unknown_type_rejected(self):
        with pytest.raises(ValueError, match="unknown controller event_type"):
            event_from_payload({"event_type": "mystery"})

    def test_non_dict_rejected(self):
        with pytest.raises(ValueError):
            event_from_payload(["not", "a", "dict"])


class TestRendering:
    def test_single_event_renders_bare(self):
        assert render_batch([UserNotification(text="deploy done")]) == "deploy done"

    def test_multi_event_digest_one_line_each(self):
        text = render_batch([
            UserNotification(text="a"),
            ProjectProgress(project="alpha", stage="delivered"),
        ])
        lines = text.splitlines()
        assert len(lines) == 2
        assert lines[0] == "• a"
        assert "project alpha: delivered" in lines[1]

    def test_empty_batch_renders_empty(self):
        assert render_batch([]) == ""

    def test_render_uses_only_typed_fields(self):
        """Privacy: rendering is a pure function of the whitelisted typed
        fields — local ids appear, but there is no channel for anything else."""
        event = ProjectProgress(project="alpha", stage="route_resolved", session_id="sess-9")
        text = render_event(event)
        assert "alpha" in text and "route_resolved" in text

    def test_wakeup_render_prefers_job_name(self):
        assert "nightly" in render_event(
            ControllerWakeup(reason="cron_fire", job_id="j1", job_name="nightly")
        )


class TestDedupeKey:
    def test_same_fact_different_ts_collides(self):
        a = ControllerWakeup(reason="cron_fire", job_id="j1", ts=1.0)
        b = ControllerWakeup(reason="cron_fire", job_id="j1", ts=99.0)
        assert dedupe_key(a) == dedupe_key(b)

    def test_silent_flag_does_not_split_identity(self):
        a = UserNotification(text="x", silent=False)
        b = UserNotification(text="x", silent=True)
        assert dedupe_key(a) == dedupe_key(b)

    def test_different_jobs_do_not_collide(self):
        a = ControllerWakeup(reason="cron_fire", job_id="j1")
        b = ControllerWakeup(reason="cron_fire", job_id="j2")
        assert dedupe_key(a) != dedupe_key(b)

    def test_different_types_never_collide(self):
        a = ReconciliationEvent(kind="x", subject="s")
        b = ControllerWakeup(reason="x", job_id="s")
        assert dedupe_key(a) != dedupe_key(b)


class TestBatcher:
    def test_duplicate_within_window_dedupes(self):
        batcher = ControllerEventBatcher(window_seconds=300)
        event = UserNotification(text="x")
        assert batcher.add(event, now=100.0) is True
        assert batcher.add(event, now=150.0) is False
        assert batcher.flush() == [event]

    def test_duplicate_after_window_accepted(self):
        batcher = ControllerEventBatcher(window_seconds=60)
        event = UserNotification(text="x")
        assert batcher.add(event, now=100.0) is True
        assert batcher.add(event, now=161.0) is True

    def test_flush_preserves_arrival_order(self):
        batcher = ControllerEventBatcher()
        events = [UserNotification(text=f"n{i}") for i in range(5)]
        for e in events:
            batcher.add(e)
        assert batcher.flush() == events

    def test_max_batch_chunks_remainder_stays_queued(self):
        batcher = ControllerEventBatcher(max_batch=2)
        events = [UserNotification(text=f"n{i}") for i in range(5)]
        for e in events:
            batcher.add(e)
        assert batcher.flush() == events[:2]
        assert batcher.pending_count() == 3
        assert batcher.flush() == events[2:4]
        assert batcher.flush() == events[4:]
        assert batcher.flush() == []

    def test_dedupe_memory_survives_flush(self):
        batcher = ControllerEventBatcher(window_seconds=300)
        event = UserNotification(text="x")
        batcher.add(event, now=100.0)
        batcher.flush()
        assert batcher.add(event, now=101.0) is False

    def test_invalid_params_rejected(self):
        with pytest.raises(ValueError):
            ControllerEventBatcher(window_seconds=-1)
        with pytest.raises(ValueError):
            ControllerEventBatcher(max_batch=0)

    def test_concurrent_adds_dedupe_exactly_once(self):
        """Concurrency: N threads racing the same event → exactly one
        acceptance, and distinct events all land exactly once."""
        batcher = ControllerEventBatcher(window_seconds=300)
        same = ControllerWakeup(reason="cron_fire", job_id="race")
        accepted = []
        barrier = threading.Barrier(8)

        def worker(i):
            barrier.wait()
            if batcher.add(same, now=100.0):
                accepted.append("same")
            if batcher.add(UserNotification(text=f"unique-{i}"), now=100.0):
                accepted.append(f"u{i}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert accepted.count("same") == 1
        flushed = batcher.flush()
        assert len(flushed) == 9  # 1 shared + 8 unique
        assert len({dedupe_key(e) for e in flushed}) == 9

    def test_accept_many_dedupes_without_touching_pending_queue(self):
        batcher = ControllerEventBatcher(window_seconds=300)
        queued = UserNotification(text="queued elsewhere")
        batcher.add(queued, now=100.0)

        event = UserNotification(text="call local")
        accepted = batcher.accept_many([event, event], now=100.0)

        assert accepted == [event]
        assert batcher.flush() == [queued]


class TestEmission:
    def test_sink_receives_event(self):
        seen = []
        set_controller_event_sink(seen.append)
        try:
            event = UserNotification(text="hello")
            emit_controller_event(event)
        finally:
            set_controller_event_sink(None)
        assert seen == [event]

    def test_broken_sink_never_raises(self):
        def explode(_event):
            raise RuntimeError("sink down")

        set_controller_event_sink(explode)
        try:
            emit_controller_event(UserNotification(text="hello"))  # must not raise
        finally:
            set_controller_event_sink(None)

    def test_default_emission_logs_without_platform_io(self, caplog):
        import logging

        with caplog.at_level(logging.DEBUG, logger="gateway.controller_events"):
            emit_controller_event(ControllerWakeup(reason="cron_fire", job_id="j1"))
        assert any("controller_event" in r.message for r in caplog.records)
