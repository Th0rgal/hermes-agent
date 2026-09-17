"""The session→missions proxy.

The cases worth pinning are the failure ones: an unreachable or unconfigured
backend must not look like "this conversation has no missions", because the
desktop would render that as an empty, reassuring section.
"""

import json

import httpx
import pytest
from fastapi import HTTPException

from plugins.projects.dashboard import plugin_api as missions_router


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("HERMES_SANDBOXED_API_URL", raising=False)
    monkeypatch.delenv("JWT_SECRET", raising=False)
    monkeypatch.delenv("HERMES_ASSISTANT_USER_ID", raising=False)


def _configure(monkeypatch, url="http://127.0.0.1:3000", secret="s3cret"):
    monkeypatch.setenv("HERMES_SANDBOXED_API_URL", url)
    monkeypatch.setenv("JWT_SECRET", secret)


class _Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or json.dumps(payload or {})

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _Client:
    """Stands in for httpx.AsyncClient, capturing the outbound request."""

    captured = {}

    def __init__(self, response=None, error=None, **_kwargs):
        self._response = response
        self._error = error

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def get(self, url, params=None, headers=None):
        type(self).captured = {"url": url, "params": params, "headers": headers}
        if self._error is not None:
            raise self._error
        return self._response


def _patch_client(monkeypatch, response=None, error=None):
    monkeypatch.setattr(
        missions_router.httpx,
        "AsyncClient",
        lambda **kwargs: _Client(response=response, error=error, **kwargs),
    )


@pytest.mark.asyncio
async def test_returns_only_the_fields_a_conversation_needs(monkeypatch):
    _configure(monkeypatch)
    _patch_client(
        monkeypatch,
        _Response(
            payload={
                "missions": [
                    {
                        "id": "m1",
                        "status": "active",
                        "title": "Fix CI",
                        "project": "verity",
                        "track": "phase1d/core-c3",
                        "created_at": "2026-08-04T10:00:00Z",
                        "updated_at": "2026-08-04T11:00:00Z",
                        "workspace_id": "should-not-be-forwarded",
                        "history": [{"secret": "internals"}],
                    }
                ]
            }
        ),
    )

    result = await missions_router.get_session_missions("20260804_103847_86ca5c")

    assert result["session_id"] == "20260804_103847_86ca5c"
    mission = result["missions"][0]
    assert mission["project"] == "verity"
    assert mission["track"] == "phase1d/core-c3"
    assert "history" not in mission
    assert "workspace_id" not in mission


@pytest.mark.asyncio
async def test_filters_server_side_on_the_origin_session(monkeypatch):
    # The unfiltered listing is capped, so filtering client-side made a
    # conversation whose workers had aged out show none at all — silently.
    _configure(monkeypatch)
    _patch_client(monkeypatch, _Response(payload={"missions": []}))

    await missions_router.get_session_missions("sess-1", limit=10)

    assert _Client.captured["params"] == {"origin_session_id": "sess-1", "limit": 10}
    assert _Client.captured["headers"]["Authorization"].startswith("Bearer ")


@pytest.mark.asyncio
async def test_the_limit_is_clamped_rather_than_trusted(monkeypatch):
    _configure(monkeypatch)
    _patch_client(monkeypatch, _Response(payload={"missions": []}))

    await missions_router.get_session_missions("sess-1", limit=100_000)
    assert _Client.captured["params"]["limit"] == missions_router._MAX_LIMIT

    await missions_router.get_session_missions("sess-1", limit=0)
    assert _Client.captured["params"]["limit"] == 1


@pytest.mark.asyncio
async def test_an_unconfigured_host_says_so_instead_of_returning_nothing(monkeypatch):
    with pytest.raises(HTTPException) as caught:
        await missions_router.get_session_missions("sess-1")
    # 503, not 200-with-empty-list: plenty of Hermes installs have no
    # sandboxed.sh, and the client hides the section rather than showing
    # "no missions" as if that were a fact about the conversation.
    assert caught.value.status_code == 503


@pytest.mark.asyncio
async def test_an_unreachable_backend_is_an_error_not_an_empty_list(monkeypatch):
    _configure(monkeypatch)
    _patch_client(monkeypatch, error=httpx.ConnectError("refused"))

    with pytest.raises(HTTPException) as caught:
        await missions_router.get_session_missions("sess-1")
    assert caught.value.status_code == 502


@pytest.mark.asyncio
async def test_a_backend_error_status_is_not_swallowed(monkeypatch):
    _configure(monkeypatch)
    _patch_client(monkeypatch, _Response(status_code=401, payload=None, text="nope"))

    with pytest.raises(HTTPException) as caught:
        await missions_router.get_session_missions("sess-1")
    assert caught.value.status_code == 502


@pytest.mark.asyncio
async def test_a_bare_list_response_is_accepted(monkeypatch):
    # The backend has returned both shapes over time; tolerate the older one
    # rather than reporting an empty conversation.
    _configure(monkeypatch)
    _patch_client(monkeypatch, _Response(payload=[{"id": "m1", "status": "completed"}]))

    result = await missions_router.get_session_missions("sess-1")
    assert [m["id"] for m in result["missions"]] == ["m1"]


@pytest.mark.asyncio
async def test_a_blank_session_id_is_rejected(monkeypatch):
    _configure(monkeypatch)
    with pytest.raises(HTTPException) as caught:
        await missions_router.get_session_missions("   ")
    assert caught.value.status_code == 400


def test_the_minted_token_is_short_lived_and_signed(monkeypatch):
    token = missions_router._mint_token("s3cret", "prod")
    header, payload, signature = token.split(".")

    import base64
    import hashlib
    import hmac

    def decode(part):
        return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))

    claims = decode(payload)
    assert claims["sub"] == "prod"
    assert 0 < claims["exp"] - claims["iat"] <= missions_router._TOKEN_TTL_SECONDS

    expected = base64.urlsafe_b64encode(
        hmac.new(b"s3cret", f"{header}.{payload}".encode(), hashlib.sha256).digest()
    ).rstrip(b"=")
    assert signature == expected.decode()
