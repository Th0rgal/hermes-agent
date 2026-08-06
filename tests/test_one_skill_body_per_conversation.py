"""A skill body is a static document; a second copy teaches nothing.

Measured on production, 2026-08-05:

    Verity dev #28                 5 injections  (~435 KB)
    Audit formel Lean de Lido #25  4 injections  (3 copies = 75% of context)
    Audit formel Lean de Lido #27  2 injections  (one was 46% of 85 messages)

`sandboxed-sh-missions` is 94 KB. `agent.skill_commands` already declines to
re-send a body it can see in the conversation, and that guard now spans the
continuation chain — but it needs a session id to search, it went four hours
saying nothing when it had none, and on 2026-08-05 a duplicate got through by a
path still unidentified.

This is the read-side counterpart: it repairs conversations that already carry
copies, whatever produced them. Only the replayed text changes; the stored rows
keep every copy, so the guard still finds them and the operator still reads what
actually happened.
"""

from hermes_state import (
    _SKILL_BODY_MARKER,
    _elide_superseded_skill_bodies,
)


def _body(skill="sandboxed-sh-missions", text="the 94 KB body"):
    return {
        "role": "user",
        "content": (
            f'[IMPORTANT: The user has invoked the "{skill}" skill, indicating '
            f"they want you to follow its instructions. {_SKILL_BODY_MARKER}"
            f"\n\n{text}"
        ),
    }


def _human(text="what is the status?"):
    return {"role": "user", "content": text}


class TestTheConstantsAgree:
    def test_the_marker_matches_the_injector(self):
        # Three copies of these sentinels exist. If they drift, this elision
        # silently stops matching and the duplication returns with no error
        # anywhere — the failure shape this whole series has been about.
        from agent import skill_commands

        assert _SKILL_BODY_MARKER == skill_commands._SINGLE_SKILL_MARKER

    def test_the_prefix_matches_the_injector(self):
        from agent import skill_commands
        from hermes_state import _SKILL_BODY_RE

        sample = f'{skill_commands._SKILL_INVOCATION_PREFIX}"demo" skill, x'
        match = _SKILL_BODY_RE.match(sample)
        assert match and match.group(1) == "demo"


class TestElision:
    def test_a_single_body_is_untouched(self):
        # The agent needs the instructions. Eliding the only copy would break
        # the skill outright.
        messages = [_body()]
        assert _elide_superseded_skill_bodies(messages) == 0
        assert "the 94 KB body" in messages[0]["content"]

    def test_the_last_copy_survives(self):
        messages = [_body(text="old"), _body(text="current")]
        assert _elide_superseded_skill_bodies(messages) == 1
        assert "current" in messages[1]["content"]
        assert "old" not in messages[0]["content"]

    def test_the_last_one_is_kept_not_the_first(self):
        # It is the version the model most recently agreed to follow, and if
        # the skill file changed between invocations it is the current text.
        messages = [_body(text="stale"), _body(text="fresh")]
        _elide_superseded_skill_bodies(messages)
        assert "fresh" in messages[-1]["content"]

    def test_the_measured_verity_shape(self):
        messages = [_body(text=f"copy {i}") for i in range(5)]
        assert _elide_superseded_skill_bodies(messages) == 4
        assert "copy 4" in messages[-1]["content"]

    def test_each_skill_keeps_its_own_last_copy(self):
        messages = [
            _body("sandboxed-sh-missions", "A1"),
            _body("paloma-projects", "B1"),
            _body("sandboxed-sh-missions", "A2"),
            _body("paloma-projects", "B2"),
        ]
        assert _elide_superseded_skill_bodies(messages) == 2
        assert "A2" in messages[2]["content"]
        assert "B2" in messages[3]["content"]

    def test_the_stale_sidecar_is_dropped(self):
        # The body is gone from content; a sidecar holding the old bytes would
        # put it straight back at transport time.
        messages = [_body(text="old"), _body(text="new")]
        messages[0]["api_content"] = "the 94 KB body again"
        _elide_superseded_skill_bodies(messages)
        assert "api_content" not in messages[0]


class TestWhatTheReplacementSays:
    def test_it_names_the_skill(self):
        messages = [_body(), _body()]
        _elide_superseded_skill_bodies(messages)
        assert "sandboxed-sh-missions" in messages[0]["content"]

    def test_it_points_at_the_copy_that_survived(self):
        messages = [_body(), _body()]
        _elide_superseded_skill_bodies(messages)
        assert "appear later in this conversation" in messages[0]["content"]

    def test_it_counts_the_invocations(self):
        messages = [_body() for _ in range(4)]
        _elide_superseded_skill_bodies(messages)
        assert "most recent of 4 invocations" in messages[0]["content"]

    def test_the_grammar_holds_for_one_and_many(self):
        two = [_body(), _body()]
        _elide_superseded_skill_bodies(two)
        assert "1 later copy follows" in two[0]["content"]

        four = [_body() for _ in range(4)]
        _elide_superseded_skill_bodies(four)
        assert "3 later copies follow" in four[0]["content"]

    def test_the_replacement_is_orders_of_magnitude_smaller(self):
        messages = [_body(text="x" * 94_000), _body()]
        _elide_superseded_skill_bodies(messages)
        assert len(messages[0]["content"]) < 400


class TestItTouchesNothingElse:
    def test_human_turns_survive(self):
        messages = [_human("keep me"), _body(), _body()]
        _elide_superseded_skill_bodies(messages)
        assert messages[0]["content"] == "keep me"

    def test_an_assistant_echo_is_not_a_body(self):
        # Agents quote scaffolding back into their own replies.
        echo = dict(_body(), role="assistant")
        messages = [echo, _body(), _body()]
        assert _elide_superseded_skill_bodies(messages) == 1
        assert "the 94 KB body" in messages[0]["content"]

    def test_a_short_reference_is_not_a_body(self):
        # The reference form keeps the prefix but drops the loaded-below
        # marker. Counting it as a body would let the real one be elided and
        # leave the conversation with no instructions at all.
        reference = {
            "role": "user",
            "content": '[IMPORTANT: The user has invoked the "x" skill again. '
            "Its full content is already loaded earlier.]",
        }
        messages = [reference, _body("x", "real")]
        assert _elide_superseded_skill_bodies(messages) == 0
        assert "real" in messages[1]["content"]

    def test_non_string_content_is_skipped(self):
        messages = [{"role": "user", "content": [{"type": "text"}]}, _body(), _body()]
        _elide_superseded_skill_bodies(messages)
        assert messages[0]["content"] == [{"type": "text"}]

    def test_an_empty_conversation_is_fine(self):
        assert _elide_superseded_skill_bodies([]) == 0


class TestSkillViewToolResults:
    """The tool-result guise of the same document.

    Measured 2026-08-06 on "Lean Silicon #9": a 90 545-character
    `sandboxed-sh-missions` body lived in a TOOL row — `skill_view`'s JSON
    result — 37% of a conversation that could no longer accept a message.
    The user-role-only scan could not see it.
    """

    def _view(self, name="autonomous-ai-agents/sandboxed-sh-missions", size=90_000):
        return {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": '{"success": true, "name": "%s", "content": "%s"}'
            % (name, "x" * size),
        }

    def test_a_lone_tool_copy_is_never_elided(self):
        # In the measured session the tool result was the ONLY copy — the
        # agent's instructions. Eliding the only copy breaks the skill.
        messages = [self._view()]
        assert _elide_superseded_skill_bodies(messages) == 0
        assert len(messages[0]["content"]) > 50_000

    def test_a_tool_copy_superseded_by_a_user_body_is_elided(self):
        messages = [self._view(), _body()]
        assert _elide_superseded_skill_bodies(messages) == 1
        assert "superseded skill body elided" in messages[0]["content"]
        assert "the 94 KB body" in messages[1]["content"]

    def test_a_user_body_superseded_by_a_tool_copy_is_elided(self):
        # Last copy wins regardless of role: the tool result carries the full
        # document too, so the instructions survive.
        messages = [_body(), self._view()]
        assert _elide_superseded_skill_bodies(messages) == 1
        assert "appear later in this conversation" in messages[0]["content"]
        assert len(messages[1]["content"]) > 50_000

    def test_the_elided_tool_row_stays_a_plausible_tool_result(self):
        messages = [self._view(), self._view()]
        _elide_superseded_skill_bodies(messages)
        import json

        parsed = json.loads(messages[0]["content"])
        assert parsed["success"] is True
        assert parsed["name"] == "sandboxed-sh-missions"

    def test_hub_qualified_and_bare_names_are_one_skill(self):
        # skill_view says "autonomous-ai-agents/sandboxed-sh-missions"; the
        # injected body says "sandboxed-sh-missions". Two buckets would mean
        # neither ever reaches two copies and nothing is ever elided.
        messages = [self._view("autonomous-ai-agents/sandboxed-sh-missions"), _body()]
        assert _elide_superseded_skill_bodies(messages) == 1

    def test_a_small_tool_result_is_not_a_copy(self):
        # Error payloads and tiny views share the JSON shape; only a body
        # large enough to matter counts.
        messages = [self._view(size=1_000), _body()]
        assert _elide_superseded_skill_bodies(messages) == 0

    def test_a_different_skill_view_is_its_own_bucket(self):
        messages = [self._view("hub/paloma-projects"), _body()]
        assert _elide_superseded_skill_bodies(messages) == 0
