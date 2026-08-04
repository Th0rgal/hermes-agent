"""Read-only proxy to the sandboxed.sh mission inventory.

A conversation's missions live in sandboxed.sh, not in Hermes. The desktop
could fetch them directly, but that would mean shipping a sandboxed.sh
credential inside a desktop app and having a second place to rotate and revoke.
Hermes already holds those credentials in order to run the assistant MCP, so
the gateway answers on the client's behalf and the desktop stays credential-free.

Deliberately read-only: nothing here can start, cancel or modify a mission.
Widening it later is a decision, not an accident.
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, HTTPException

_log = logging.getLogger("hermes_cli.web_server")

router = APIRouter()

# Same env the assistant MCP is configured with, so there is exactly one place
# that says where sandboxed.sh is.
_API_URL_ENV = "HERMES_SANDBOXED_API_URL"
_SECRET_ENV = "JWT_SECRET"

# Short-lived on purpose: the token is minted per request and never stored.
_TOKEN_TTL_SECONDS = 120
# A conversation with more workers than this is not a display problem worth
# solving here — the board is the place for that.
_MAX_LIMIT = 200
_REQUEST_TIMEOUT_SECONDS = 15


def _b64(raw: bytes) -> bytes:
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def _mint_token(secret: str, subject: str) -> str:
    """An HS256 JWT valid for the next couple of minutes."""
    now = int(time.time())
    header = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = _b64(
        json.dumps(
            {
                "sub": subject,
                "usr": subject,
                "iat": now,
                "exp": now + _TOKEN_TTL_SECONDS,
            },
            separators=(",", ":"),
        ).encode()
    )
    signed = header + b"." + payload
    signature = _b64(hmac.new(secret.encode(), signed, hashlib.sha256).digest())
    return (signed + b"." + signature).decode()


def _sandboxed_config() -> tuple[str, str, str]:
    """(base_url, secret, subject), or a 503 when this host has no backend."""
    base_url = (os.environ.get(_API_URL_ENV) or "").strip().rstrip("/")
    secret = (os.environ.get(_SECRET_ENV) or "").strip()
    if not base_url or not secret:
        # Not an error: plenty of Hermes installs have no sandboxed.sh at all.
        # The desktop treats 503 as "hide the section".
        raise HTTPException(
            status_code=503,
            detail="sandboxed.sh is not configured on this host",
        )
    subject = (os.environ.get("HERMES_ASSISTANT_USER_ID") or "hermes").strip() or "hermes"
    return base_url, secret, subject


def _summarize(mission: Dict[str, Any]) -> Dict[str, Any]:
    """Only the fields a conversation view needs.

    Forwarding the whole record would hand the desktop mission internals it has
    no use for, and would make every future backend field a de-facto part of
    this endpoint's contract.
    """
    return {
        "id": mission.get("id"),
        "status": mission.get("status"),
        "title": mission.get("title"),
        "short_description": mission.get("short_description"),
        "project": mission.get("project"),
        "track": mission.get("track"),
        "created_at": mission.get("created_at"),
        "updated_at": mission.get("updated_at"),
    }


@router.get("/api/sessions/{session_id}/missions")
async def get_session_missions(session_id: str, limit: int = 50) -> Dict[str, Any]:
    """Missions started from this conversation, newest first.

    Filtered by the backend on `origin_session_id`. That matters: the unfiltered
    listing is capped, so filtering client-side made a conversation whose
    workers had aged out of the window show none at all — silently.
    """
    session_id = (session_id or "").strip()
    if not session_id:
        raise HTTPException(status_code=400, detail="session_id is required")

    base_url, secret, subject = _sandboxed_config()
    # FastAPI already supplies the default, so clamp rather than re-defaulting:
    # `limit or 50` would silently turn an explicit 0 into 50.
    limit = max(1, min(int(limit), _MAX_LIMIT))
    token = _mint_token(secret, subject)

    try:
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{base_url}/api/control/missions",
                params={"origin_session_id": session_id, "limit": limit},
                headers={"Authorization": f"Bearer {token}"},
            )
    except httpx.HTTPError as error:
        # The backend being unreachable is an availability problem, not a
        # client error — say so rather than returning an empty list, which
        # would read as "this conversation has no missions".
        _log.warning("sandboxed.sh mission lookup failed: %s", error)
        raise HTTPException(status_code=502, detail="sandboxed.sh is unreachable") from error

    if response.status_code >= 400:
        _log.warning(
            "sandboxed.sh mission lookup returned %s: %s",
            response.status_code,
            response.text[:200],
        )
        raise HTTPException(
            status_code=502,
            detail=f"sandboxed.sh returned {response.status_code}",
        )

    try:
        body = response.json()
    except ValueError as error:
        raise HTTPException(status_code=502, detail="sandboxed.sh returned invalid JSON") from error

    missions: Optional[List[Dict[str, Any]]]
    if isinstance(body, dict):
        missions = body.get("missions")
    else:
        missions = body
    if not isinstance(missions, list):
        missions = []

    return {
        "session_id": session_id,
        "missions": [_summarize(m) for m in missions if isinstance(m, dict)],
    }
