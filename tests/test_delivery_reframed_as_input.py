"""A cron delivery is input to a conversation, never that conversation's turn.

Measured incident, 2026-08-05, project session `20260805_094128_95c099`:

    1785916703 | assistant | observed=1 | 2990ch | [Cron delivery: Lido 6h …]
    1785916803 | user      | observed=0 | 1293ch | Thomas demande une issue …
    1785916814 | assistant | observed=0 | 2933ch | Patches landed correctly. …

The reply is the delivery, minus its 57-character sentinel — the model
reproduced its "own" previous utterance instead of answering the question.

Storing another conversation's report under this conversation's assistant role
is what invites that. The role is nonetheless correct for the readers of the
transcript (dashboard watcher, desktop divider, TUI), so the fix belongs on the
replay projection, not on the stored row.
"""

import pytest

from hermes_state import _reframe_delivery_as_input


def _delivery(content, **extra):
    msg = {"role": "assistant", "content": content, "observed": True}
    msg.update(extra)
    return msg


class TestReframing:
    def test_a_delivery_is_replayed_as_input(self):
        msg = _delivery("[Cron delivery: Lido 6h metrics]\nCampaign 1ae66a04.")
        _reframe_delivery_as_input(msg)
        assert msg["role"] == "user"

    def test_it_says_the_model_did_not_write_it(self):
        msg = _delivery("[Cron delivery: Lido 6h metrics]\nCampaign 1ae66a04.")
        _reframe_delivery_as_input(msg)
        assert "did not write this" in msg["content"]

    def test_it_says_the_operator_did_not_write_it_either(self):
        # Without this the reframing trades one confusion for another: a
        # controller report becomes indistinguishable from a human instruction.
        msg = _delivery("[Cron delivery: Lido 6h metrics]\nCampaign 1ae66a04.")
        _reframe_delivery_as_input(msg)
        assert "not from the operator" in msg["content"]

    def test_the_payload_survives_intact(self):
        msg = _delivery("[Cron delivery: watcher]\nHead advanced to fbfde973.")
        _reframe_delivery_as_input(msg)
        assert msg["content"].endswith("Head advanced to fbfde973.")

    def test_the_sentinel_stays_at_the_front(self):
        # The TUI matches its delivery divider on this prefix, anchored at the
        # start of the string. Moving it would silently drop that divider.
        msg = _delivery("[Cron delivery: watcher]\nDone.")
        _reframe_delivery_as_input(msg)
        assert msg["content"].startswith("[Cron delivery: watcher]")

    def test_the_label_is_carried_into_the_provenance(self):
        msg = _delivery("[Cron delivery: Lido 6h component metrics]\nDone.")
        _reframe_delivery_as_input(msg)
        assert '"Lido 6h component metrics" controller' in msg["content"]

    def test_a_blank_label_still_names_something(self):
        msg = _delivery("[Cron delivery: ]\nDone.")
        _reframe_delivery_as_input(msg)
        assert '"cron" controller' in msg["content"]


class TestStaleSidecar:
    def test_the_api_content_sidecar_is_dropped(self):
        # The sidecar is a *substitute* for content at transport time. Left in
        # place it puts the unframed text straight back in front of the model
        # and the reframing does nothing at all — the failure mode that would
        # look fixed in the transcript and still be live in production.
        msg = _delivery(
            "[Cron delivery: watcher]\nDone.", api_content="Done."
        )
        _reframe_delivery_as_input(msg)
        assert "api_content" not in msg

    @pytest.mark.parametrize(
        "field",
        [
            "finish_reason",
            "reasoning",
            "reasoning_content",
            "reasoning_details",
            "codex_reasoning_items",
            "codex_message_items",
        ],
    )
    def test_assistant_only_replay_fields_are_dropped(self, field):
        # Reasoning hanging off a user message is malformed for every provider
        # that replays it.
        msg = _delivery("[Cron delivery: watcher]\nDone.", **{field: "x"})
        _reframe_delivery_as_input(msg)
        assert field not in msg


class TestLeftAlone:
    def test_a_real_assistant_turn_is_untouched(self):
        msg = {"role": "assistant", "content": "Patches landed correctly."}
        _reframe_delivery_as_input(msg)
        assert msg == {"role": "assistant", "content": "Patches landed correctly."}

    def test_an_unobserved_row_carrying_the_sentinel_is_untouched(self):
        # `observed` is what marks a row as a real delivery rather than an
        # agent quoting the scaffolding back into its own answer.
        msg = {"role": "assistant", "content": "[Cron delivery: x]\nDone."}
        _reframe_delivery_as_input(msg)
        assert msg["role"] == "assistant"

    def test_a_human_message_is_untouched(self):
        msg = {"role": "user", "content": "[Cron delivery: x]\nDone.", "observed": True}
        _reframe_delivery_as_input(msg)
        assert msg["content"] == "[Cron delivery: x]\nDone."

    def test_a_tool_call_row_is_untouched(self):
        # Rewriting the role inside an assistant→tool sequence breaks the
        # adjacency the API validates, and costs an HTTP 400 on every replay.
        msg = _delivery(
            "[Cron delivery: x]\nDone.", tool_calls=[{"id": "c1"}]
        )
        _reframe_delivery_as_input(msg)
        assert msg["role"] == "assistant"

    def test_non_string_content_is_untouched(self):
        msg = _delivery([{"type": "text", "text": "hi"}])
        _reframe_delivery_as_input(msg)
        assert msg["role"] == "assistant"


class TestTheMeasuredIncident:
    def test_the_echoed_delivery_is_no_longer_the_models_own_words(self):
        stored = (
            "[Cron delivery: Lido 6h component progression controller]\n"
            "Patches landed correctly. State signature: lido | repair-running"
        )
        msg = _delivery(stored, api_content=stored.split("\n", 1)[1])
        _reframe_delivery_as_input(msg)

        assert msg["role"] == "user", "the model must not own the report"
        assert "api_content" not in msg, "the unframed bytes must not survive"
        assert "Patches landed correctly." in msg["content"], "payload preserved"


class TestAnElidedDeliveryStaysElided:
    """The sidecar is how a delivery gets shrunk without hiding it.

    Measured 2026-08-05, session `20260805_152329_6058d9` ("Verity dev #32"):

        cron deliveries      22 msg   75 513 ch   43%
        compaction reference  1 msg   22 916 ch   13%
        tool results         73 msg   39 007 ch   22%

    The conversation could no longer accept a message, and its mass was
    controller reports. Reframing threw the sidecar away, so eliding a delivery
    for the model was impossible — the only remaining lever was deactivating
    those rows, which removes them from the operator's transcript as well.
    """

    def test_the_sidecar_becomes_the_body(self):
        msg = _delivery(
            "[Cron delivery: watcher]\n" + "the full 8 KB report " * 400,
            api_content="[report elided]",
        )
        _reframe_delivery_as_input(msg)
        assert "[report elided]" in msg["content"]
        assert "the full 8 KB report" not in msg["content"]

    def test_the_provenance_still_wraps_it(self):
        # Eliding must not cost the framing: the model still has to know this
        # is a controller report and not its own words.
        msg = _delivery("[Cron delivery: watcher]\nlong…", api_content="[elided]")
        _reframe_delivery_as_input(msg)
        assert msg["role"] == "user"
        assert msg["content"].startswith("[Cron delivery: watcher]")
        assert "did not write this" in msg["content"]

    def test_the_sidecar_is_still_dropped_afterwards(self):
        # Its bytes are inside `content` now. Leaving it would substitute the
        # unframed text back in at transport time.
        msg = _delivery("[Cron delivery: watcher]\nlong…", api_content="[elided]")
        _reframe_delivery_as_input(msg)
        assert "api_content" not in msg

    def test_a_blank_sidecar_falls_back_to_the_stored_body(self):
        # An empty sidecar is not an elision request; treating it as one would
        # silently delete a report.
        msg = _delivery("[Cron delivery: watcher]\nDone.", api_content="   ")
        _reframe_delivery_as_input(msg)
        assert msg["content"].endswith("Done.")

    def test_a_non_string_sidecar_falls_back(self):
        msg = _delivery("[Cron delivery: watcher]\nDone.", api_content={"a": 1})
        _reframe_delivery_as_input(msg)
        assert msg["content"].endswith("Done.")

    def test_no_sidecar_behaves_exactly_as_before(self):
        msg = _delivery("[Cron delivery: watcher]\nDone.")
        _reframe_delivery_as_input(msg)
        assert msg["content"].endswith("Done.")
