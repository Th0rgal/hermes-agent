"""A delivery's display label must not reach the model.

Measured incident, 2026-08-05. A project conversation accumulated 132 controller
reports against a handful of human turns. Every report was stored with a
`[Cron delivery: …]` opener. The operator then asked a direct question, and the
agent answered by reproducing three old reports verbatim — including a
supervisor report from two hours earlier and its `[SILENT]` marker — instead of
replying. The question went unanswered.

The label is provenance for a human reader. It is chrome to the model, and when
it dominates the transcript the model learns that the next message is another
report.
"""

import inspect

from cron import scheduler


class TestDeliveryApiContent:
    def test_the_transcript_delivery_passes_a_clean_sidecar(self):
        source = inspect.getsource(scheduler._deliver_to_local_session)
        assert "api_content=text" in source, (
            "the transcript append must carry the unlabelled text as api_content, "
            "or the model reads the display label"
        )

    def test_the_stored_content_keeps_its_label(self):
        # The label is deliberate: it preserves callback provenance when
        # SQLite drops mirror metadata, and it is what the operator reads.
        # Only the model-facing copy is stripped.
        source = inspect.getsource(scheduler._deliver_to_local_session)
        assert 'delivery_content = f"[Cron delivery: {label}]' in source
        assert "delivery_content," in source, "the labelled text is still stored"

    def test_the_sidecar_is_the_text_not_the_labelled_form(self):
        # Guards the mistake that would silently do nothing: passing
        # delivery_content as the sidecar leaves the label in the model's view.
        source = inspect.getsource(scheduler._deliver_to_local_session)
        assert "api_content=delivery_content" not in source

    def test_append_message_accepts_the_sidecar(self):
        # The parameter has to exist on the real signature, not just in our
        # call site, or the delivery raises at runtime for every tick.
        from hermes_state import SessionDB

        params = inspect.signature(SessionDB.append_message).parameters
        assert "api_content" in params
        assert "delivery_id" in params
        assert "observed" in params
