"""A project conversation must not fill up with its own controller reports.

Measured 2026-08-05, session `20260805_152329_6058d9` ("Verity dev #32"):

    cron deliveries        22 msg   75 513 ch   43%
    compaction reference    1 msg   22 916 ch   13%
    tool results           73 msg   39 007 ch   22%
    assistant turns        64 msg   32 105 ch   18%

The conversation could no longer accept a message, and its dominant mass was
status reports it had received from its own controllers. Nothing bounded that,
so every project session refilled a few hours after being repaired by hand —
three separate manual repairs on 2026-08-05 alone.

Old reports are superseded by newer ones from the same controller: the point of
a status report is the current status. Only the replayed text shrinks; the
stored row keeps the full report, which is what the operator reads (the desktop
loads through `get_messages`, not this projection).
"""

import pytest

from hermes_state import (
    _DELIVERY_REPLAY_KEEP_DEFAULT,
    _delivery_replay_keep,
    _elide_superseded_deliveries,
)


def _delivery(label, body="the full report body"):
    return {
        "role": "user",
        "content": (
            f'[Cron delivery: {label}] (automated report delivered by the '
            f'"{label}" controller — you did not write this, and it is not '
            f"from the operator)\n{body}"
        ),
    }


def _human(text="what is the status?"):
    return {"role": "user", "content": text}


class TestTheCap:
    def test_the_default_is_three(self, monkeypatch):
        monkeypatch.delenv("HERMES_DELIVERY_REPLAY_KEEP", raising=False)
        assert _delivery_replay_keep() == _DELIVERY_REPLAY_KEEP_DEFAULT == 3

    def test_it_is_configurable(self, monkeypatch):
        monkeypatch.setenv("HERMES_DELIVERY_REPLAY_KEEP", "10")
        assert _delivery_replay_keep() == 10

    def test_zero_elides_every_report(self, monkeypatch):
        monkeypatch.setenv("HERMES_DELIVERY_REPLAY_KEEP", "0")
        messages = [_delivery("watcher"), _delivery("watcher")]
        assert _elide_superseded_deliveries(messages) == 2

    def test_a_negative_cap_is_clamped(self, monkeypatch):
        # No sensible reading, and guessing one is how a typo becomes a
        # silently empty conversation.
        monkeypatch.setenv("HERMES_DELIVERY_REPLAY_KEEP", "-5")
        assert _delivery_replay_keep() == 0

    def test_garbage_falls_back_to_the_default(self, monkeypatch):
        monkeypatch.setenv("HERMES_DELIVERY_REPLAY_KEEP", "three")
        assert _delivery_replay_keep() == 3

    def test_an_empty_value_falls_back(self, monkeypatch):
        monkeypatch.setenv("HERMES_DELIVERY_REPLAY_KEEP", "   ")
        assert _delivery_replay_keep() == 3


class TestWhatIsKept:
    def test_fewer_reports_than_the_cap_are_untouched(self, monkeypatch):
        monkeypatch.delenv("HERMES_DELIVERY_REPLAY_KEEP", raising=False)
        messages = [_delivery("a"), _delivery("b")]
        before = [m["content"] for m in messages]
        assert _elide_superseded_deliveries(messages) == 0
        assert [m["content"] for m in messages] == before

    def test_exactly_the_cap_is_untouched(self, monkeypatch):
        monkeypatch.delenv("HERMES_DELIVERY_REPLAY_KEEP", raising=False)
        messages = [_delivery(str(i)) for i in range(3)]
        assert _elide_superseded_deliveries(messages) == 0

    def test_the_most_recent_survive_in_full(self, monkeypatch):
        monkeypatch.delenv("HERMES_DELIVERY_REPLAY_KEEP", raising=False)
        messages = [_delivery(str(i)) for i in range(22)]
        assert _elide_superseded_deliveries(messages) == 19
        for message in messages[-3:]:
            assert "the full report body" in message["content"]
        for message in messages[:-3]:
            assert "the full report body" not in message["content"]

    def test_the_measured_session_shape(self, monkeypatch):
        # 22 reports, as measured. 19 elided is exactly the manual repair.
        monkeypatch.delenv("HERMES_DELIVERY_REPLAY_KEEP", raising=False)
        messages = [_delivery("Verity two-phase") for _ in range(22)]
        assert _elide_superseded_deliveries(messages) == 19


class TestWhatTheSummarySays:
    @pytest.fixture(autouse=True)
    def _default_cap(self, monkeypatch):
        monkeypatch.delenv("HERMES_DELIVERY_REPLAY_KEEP", raising=False)

    def test_it_keeps_the_sentinel_at_the_front(self):
        # The TUI matches its delivery divider on this prefix, anchored at the
        # start of the string.
        messages = [_delivery("watcher") for _ in range(5)]
        _elide_superseded_deliveries(messages)
        assert messages[0]["content"].startswith("[Cron delivery: watcher]")

    def test_it_keeps_the_label(self):
        messages = [_delivery("Lido 6h metrics") for _ in range(5)]
        _elide_superseded_deliveries(messages)
        assert "Lido 6h metrics" in messages[0]["content"]

    def test_it_says_the_report_was_superseded_not_absent(self):
        # A reader who sees "superseded" knows a report existed. Deleting it
        # silently would make the conversation look like it never arrived.
        messages = [_delivery("watcher") for _ in range(5)]
        _elide_superseded_deliveries(messages)
        assert "superseded" in messages[0]["content"]
        assert "elided" in messages[0]["content"]

    def test_it_counts_the_later_reports(self):
        messages = [_delivery("watcher") for _ in range(5)]
        _elide_superseded_deliveries(messages)
        assert "4 later reports" in messages[0]["content"]
        assert "3 later reports" in messages[1]["content"]

    def test_it_gets_the_singular_right(self, monkeypatch):
        # Unreachable at the default cap of 3 -- a superseded report always has
        # at least three successors there -- so exercise it at a lower cap.
        monkeypatch.setenv("HERMES_DELIVERY_REPLAY_KEEP", "1")
        messages = [_delivery("watcher") for _ in range(2)]
        assert _elide_superseded_deliveries(messages) == 1
        assert "1 later report from" in messages[0]["content"]

    def test_the_summary_is_far_smaller_than_a_report(self):
        messages = [_delivery("watcher", body="x" * 8000) for _ in range(5)]
        _elide_superseded_deliveries(messages)
        assert len(messages[0]["content"]) < 300


class TestItTouchesNothingElse:
    @pytest.fixture(autouse=True)
    def _default_cap(self, monkeypatch):
        monkeypatch.delenv("HERMES_DELIVERY_REPLAY_KEEP", raising=False)

    def test_human_turns_survive(self):
        messages = [_human("keep me")] + [_delivery("w") for _ in range(5)]
        _elide_superseded_deliveries(messages)
        assert messages[0]["content"] == "keep me"

    def test_assistant_turns_survive(self):
        turn = {"role": "assistant", "content": "[Cron delivery: w] not a delivery"}
        messages = [turn] + [_delivery("w") for _ in range(5)]
        _elide_superseded_deliveries(messages)
        assert messages[0]["content"] == "[Cron delivery: w] not a delivery"

    def test_interleaved_human_turns_do_not_shift_the_window(self):
        messages = []
        for i in range(5):
            messages.append(_delivery(str(i)))
            messages.append(_human(f"question {i}"))
        assert _elide_superseded_deliveries(messages) == 2
        assert "the full report body" in messages[4]["content"]
        assert all(m["content"].startswith("question") for m in messages[1::2])

    def test_non_string_content_is_skipped(self):
        messages = [{"role": "user", "content": [{"type": "text"}]}] + [
            _delivery("w") for _ in range(5)
        ]
        _elide_superseded_deliveries(messages)
        assert messages[0]["content"] == [{"type": "text"}]

    def test_an_empty_conversation_is_fine(self):
        assert _elide_superseded_deliveries([]) == 0
