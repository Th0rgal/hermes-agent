"""`[STATE_SIGNATURE: …]` is a machine token and must never be delivered.

`_extract_state_signature`'s docstring has always promised "the human-facing
result never includes the marker". Measured 2026-08-05 on two live project
conversations, four separate emission shapes defeated it and reached the
operator's screen verbatim in the Hermes desktop app:

    `[STATE_SIGNATURE: verity|phase1k|#2231|…]`          wrapped in backticks
    **Status signature:** `[STATE_SIGNATURE: …]`         behind a bold label
    [STATE_SIGNATURE: …|next-tick [blocked]]             bracket in the payload
    [STATE_SIGNATURE: …]                                 over 512 characters

The old pattern required a bare whole line, starting with `[`, containing no
`]`, at most 512 characters. Each of the four missed it, and the failure was
silent — nothing logs a marker that fails to match.
"""

from cron.scheduler import _extract_state_signature


def _clean(text):
    return _extract_state_signature(text)[0]


def _digest(text):
    return _extract_state_signature(text)[1]


class TestTheShapesMeasuredOnProd:
    def test_a_bare_line(self):
        text = "Still blocked.\n[STATE_SIGNATURE: repo|pr7|abc|blocked|ci]"
        assert _clean(text) == "Still blocked."
        assert _digest(text)

    def test_wrapped_in_backticks(self):
        text = "Done.\n`[STATE_SIGNATURE: verity|phase1k|#2231|2a380049|CONFLICTING]`"
        assert "STATE_SIGNATURE" not in _clean(text)
        assert _digest(text)

    def test_behind_a_bold_label(self):
        text = (
            "Tick summary.\n\n**Status signature:** "
            "`[STATE_SIGNATURE: verity|phase1k|#2231|2a380049|CONFLICTING]`"
        )
        cleaned = _clean(text)
        assert "STATE_SIGNATURE" not in cleaned
        assert "Status signature" not in cleaned, "the label goes with the token"
        assert cleaned.startswith("Tick summary.")

    def test_a_bracket_inside_the_payload(self):
        # The exact line the operator reported.
        text = (
            "All lanes merged.\n[STATE_SIGNATURE: verity-program|none|"
            "066f1bf5772ebc6cc218902b8f05ad70cbf36866|no-open-prs|none|"
            "next-tick [blocked]]"
        )
        cleaned = _clean(text)
        assert "STATE_SIGNATURE" not in cleaned
        assert "]" not in cleaned, f"a stray bracket survived: {cleaned!r}"
        assert cleaned == "All lanes merged."

    def test_a_payload_longer_than_the_old_cap(self):
        payload = "verity|" + ("x" * 900)
        text = f"Report.\n[STATE_SIGNATURE: {payload}]"
        assert "STATE_SIGNATURE" not in _clean(text)


class TestItStillDoesItsJob:
    def test_the_digest_is_stable_across_prose(self):
        # The whole point: recurring monitors suppress unchanged state even
        # when their wording drifts.
        a = _digest("One wording.\n[STATE_SIGNATURE: repo|pr7|abc|green]")
        b = _digest("Entirely different wording.\n[STATE_SIGNATURE: repo|pr7|abc|green]")
        assert a == b

    def test_a_changed_state_changes_the_digest(self):
        a = _digest("x\n[STATE_SIGNATURE: repo|pr7|abc|green]")
        b = _digest("x\n[STATE_SIGNATURE: repo|pr7|abc|red]")
        assert a != b

    def test_the_last_marker_wins(self):
        text = (
            "x\n[STATE_SIGNATURE: repo|pr7|abc|stale]\n"
            "y\n[STATE_SIGNATURE: repo|pr7|abc|fresh]"
        )
        assert _digest(text) == _digest("[STATE_SIGNATURE: repo|pr7|abc|fresh]")

    def test_no_marker_means_no_digest_and_no_edit(self):
        text = "Just a report, nothing machine-readable."
        assert _extract_state_signature(text) == (text, None)

    def test_non_string_input_is_returned_untouched(self):
        assert _extract_state_signature(None) == (None, None)


class TestTheReadingStaysClean:
    def test_removing_a_marker_leaves_no_gap(self):
        # A marker on its own line leaves a blank line behind, and a run of
        # blanks renders as a hole the reader cannot explain.
        text = "Before.\n\n[STATE_SIGNATURE: a|b|c]\n\nAfter."
        assert _clean(text) == "Before.\n\nAfter."

    def test_prose_mentioning_the_word_is_not_eaten(self):
        # Only the bracketed token is a token. Talking about it is prose.
        text = "I emit a STATE_SIGNATURE line at the end of each tick."
        assert _clean(text) == text

    def test_surrounding_content_survives(self):
        text = "Line one.\n[STATE_SIGNATURE: a|b]\nLine two."
        cleaned = _clean(text)
        assert "Line one." in cleaned and "Line two." in cleaned
