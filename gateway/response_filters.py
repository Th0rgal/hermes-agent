"""Gateway response filtering helpers.

These helpers operate at the gateway boundary: they decide whether a completed
agent turn should be delivered to the chat, not what should be persisted in the
conversation history.
"""

from __future__ import annotations

import unicodedata
from typing import Any

# Canonical model-emitted control token for intentional silence.
SILENT_REPLY_TOKEN = "NO_REPLY"

# Exact whole-response markers that mean "the agent intentionally chose not to
# reply".  Keep this list small and explicit; arbitrary empty output remains an
# error/empty-response path, not silence.
LIVE_GATEWAY_SILENT_MARKERS = frozenset({
    "[SILENT]",
    "SILENT",
    "NO_REPLY",
    "NO REPLY",
})


def _canonical_silence_candidate(text: str) -> str:
    return " ".join(text.strip().upper().split())


def _strip_edge_silence_punctuation(text: str) -> str:
    """Strip stray edge punctuation without erasing marker structure.

    Models sometimes emit ``.NO_REPLY`` or ``*NO_REPLY*`` instead of the exact
    marker. Keep square brackets structural so malformed ``[SILENT`` does not
    become ``SILENT``.
    """
    start = 0
    end = len(text)
    while start < end and text[start] not in "[]" and unicodedata.category(text[start]).startswith("P"):
        start += 1
    while end > start and text[end - 1] not in "[]" and unicodedata.category(text[end - 1]).startswith("P"):
        end -= 1
    return text[start:end].strip()


def _canonical_silence_candidates(text: str) -> tuple[str, ...]:
    exact = _canonical_silence_candidate(text)
    stripped = _strip_edge_silence_punctuation(text.strip())
    if stripped == text.strip():
        return (exact,)
    fallback = _canonical_silence_candidate(stripped)
    return (exact, fallback)


def _is_bracketish_silence_line(line: str) -> bool:
    """Autonomous-lane only: recognize a bracket-opened silence sentinel even
    when the closing bracket is missing/truncated.

    Models running an autonomous tick sometimes emit a bare ``[SILENT`` (no
    closing bracket) at the very end of a reasoning blob — the whole turn was
    meant to be silent, but a truncated sentinel defeats the strict matchers and
    the raw scratchpad gets delivered to Telegram.  A leading ``[`` is required
    so ordinary prose like ``SILENT retry succeeded`` is unaffected (that word
    case is already handled — or intentionally excluded — by ``_is_token``).
    """
    s = line.strip()
    if not s.startswith("["):
        return False
    inner = s[1:]
    if inner.endswith("]"):
        inner = inner[:-1]
    return _canonical_silence_candidate(inner) in LIVE_GATEWAY_SILENT_MARKERS


def is_intentional_silence_response(response: Any) -> bool:
    """Return True only when ``response`` is exactly a silence marker.

    Substantive prose that merely mentions ``NO_REPLY`` or ``[SILENT]`` must be
    delivered normally.  A blank response is also not silence; blank output is
    handled by the empty-response failure path.
    """
    if not isinstance(response, str):
        return False
    stripped = response.strip()
    if not stripped:
        return False
    if len(stripped) > 64:
        return False
    return any(candidate in LIVE_GATEWAY_SILENT_MARKERS for candidate in _canonical_silence_candidates(stripped))


def is_autonomous_silence_response(response: Any) -> bool:
    """Loose silence matcher for autonomous lanes (cron, webhook).

    Autonomous lanes instruct the agent to emit ``[SILENT]`` when a tick
    produced nothing worth a human's attention, and models reliably bracket
    the marker with a short note explaining why they stayed quiet.  Unlike
    :func:`is_intentional_silence_response` (the interactive-chat rule, which
    demands the response be EXACTLY a marker), this suppresses when a marker
    is the whole response, sits on its own first or last line, or the
    bracketed sentinel opens the response (the documented
    ``[SILENT] No changes detected`` pattern).  A token buried mid-sentence
    in a genuine report is still delivered.

    Shares :data:`LIVE_GATEWAY_SILENT_MARKERS` so the interactive and
    autonomous marker sets can never drift apart.
    """
    if not isinstance(response, str):
        return False
    stripped = response.strip()
    if not stripped:
        return False

    def _is_token(line: str) -> bool:
        return _canonical_silence_candidate(line) in LIVE_GATEWAY_SILENT_MARKERS

    # Whole response is exactly a token.
    if _is_token(stripped):
        return True
    # Marker on its own first or last line (leading/trailing note on a
    # separate line — e.g. "2 deals filtered\n\n[SILENT]").
    lines = [ln for ln in stripped.splitlines() if ln.strip()]
    if lines and (_is_token(lines[0]) or _is_token(lines[-1])):
        return True
    # Bracketed sentinel used as a same-line prefix — the documented pattern
    # "[SILENT] No changes detected".  Restricted to the bracketed form so a
    # bare word like "Silent retry succeeded" is NOT swallowed.
    if stripped.upper().startswith("[SILENT]"):
        return True
    # Truncated/malformed bracket sentinel on the first or last line — a bare
    # "[SILENT" (no closing bracket) that the model appended to a reasoning
    # blob it intended to suppress.  Autonomous lane only; the strict
    # interactive matcher above is unchanged.
    if lines and (_is_bracketish_silence_line(lines[0]) or _is_bracketish_silence_line(lines[-1])):
        return True
    return False


# ── Human-facing delivery sanitation ─────────────────────────────────────────

import re as _re

# Machine-only trailers the controller-policy skill appends for the sandboxed.sh
# ingestor (mode/state parse).  They must survive to ingestion on the *local
# session* path, but must never be shown to a human on a *platform* delivery
# (Telegram/Discord/…), which is terminal — no ingestor downstream.
_CTRL_TRAILER_RE = _re.compile(r"\[CTRL:[^\]]*\]", _re.IGNORECASE)
_STATE_SIG_TRAILER_RE = _re.compile(r"\[STATE_SIGNATURE:[^\]]*\]", _re.IGNORECASE)
# Empty-tag improvisation: `[CTRL:]` then prose outside the brackets.
_EMPTY_CTRL_LINE_RE = _re.compile(r"^[ \t]*\[CTRL:\][^\n]*$", _re.IGNORECASE | _re.MULTILINE)
# Narrated tool calls: a model without its real tools echoes "[tool call: X]"
# followed by a JSON object.  Strip the marker and an immediately-following
# JSON blob line.
_TOOL_CALL_MARKER_RE = _re.compile(r"\[tool[_ ]call:[^\]]*\]", _re.IGNORECASE)


def sanitize_platform_delivery(text: Any) -> str:
    """Strip machine-only scaffolding from a human-facing *platform* delivery.

    Removes ``[CTRL: …]``/``[STATE_SIGNATURE: …]`` ingestor trailers, narrated
    ``[tool call: …]`` markers and any JSON blob line immediately following one.
    Returns the cleaned text (may be empty — callers should treat an empty
    result as "nothing substantive to deliver" and suppress).

    Only for terminal platform lanes.  The local-session lane keeps the raw
    trailers so the sandboxed.sh ingestor can still parse mode/state.
    """
    if not isinstance(text, str):
        return ""
    # Empty-tag `[CTRL:] prose` first: the generic `[CTRL:…]` matcher would
    # consume only the empty token and leave the rest of the line visible.
    cleaned = _EMPTY_CTRL_LINE_RE.sub("", text)
    cleaned = _CTRL_TRAILER_RE.sub("", cleaned)
    cleaned = _STATE_SIG_TRAILER_RE.sub("", cleaned)
    out_lines: list[str] = []
    drop_next_json = False
    for line in cleaned.splitlines():
        marker_hit = bool(_TOOL_CALL_MARKER_RE.search(line))
        stripped_line = _TOOL_CALL_MARKER_RE.sub("", line).strip()
        if marker_hit:
            # If the marker leaves nothing behind, drop the line and arm the
            # JSON-blob eater for the next line.
            drop_next_json = True
            if not stripped_line:
                continue
            out_lines.append(stripped_line)
            continue
        if drop_next_json:
            ls = line.strip()
            if ls.startswith("{") and ('"name"' in ls or '"arguments"' in ls or ls.endswith("}")):
                continue
            drop_next_json = False
        out_lines.append(line)
    # Collapse the runs of blank lines the stripping may leave behind.
    result = "\n".join(out_lines)
    result = _re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def is_intentional_silence_agent_result(agent_result: dict | None, response: Any) -> bool:
    """Silence markers suppress delivery only for successful agent turns."""
    if not isinstance(agent_result, dict):
        return False
    if agent_result.get("failed"):
        return False
    return is_intentional_silence_response(response)


def is_partial_silence_marker(text: Any) -> bool:
    """Return True while ``text`` could still resolve to a silence marker.

    The streaming path accumulates the reply delta-by-delta and must decide,
    before the whole response is known, whether to show what it has so far.
    A buffer whose canonical form is a non-empty *prefix* of a silence marker
    (e.g. ``"NO"`` on the way to ``"NO_REPLY"``, or an exact marker that has
    not yet been terminated by stream-end) is held back so a raw marker is
    never edited onto the screen and then belatedly retracted.

    Anything that has already diverged from every marker (ordinary prose) —
    and anything longer than the marker cap — returns False so normal
    streaming resumes immediately.  This is the streaming counterpart to
    :func:`is_intentional_silence_response`, sharing the same marker set and
    canonicalization so the two never drift.
    """
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped or len(stripped) > 64:
        return False
    for candidate in _canonical_silence_candidates(stripped):
        if candidate and any(marker.startswith(candidate) for marker in LIVE_GATEWAY_SILENT_MARKERS):
            return True
    return False
