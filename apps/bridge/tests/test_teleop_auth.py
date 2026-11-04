"""Who may open the teleop socket (X15, backlog 1.5).

It cannot bind loopback (the headset is on the LAN), so the handshake is the
gate: a browser Origin must be the console's, and when TELEOP_SECRET is set a
short-lived token minted by apps/back is required. No secret keeps it working,
with a loud warning, so a test-day setup without one does not silently break.
"""

from __future__ import annotations

import hashlib
import hmac
import time

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import InvalidStatus

from bridge.teleop import server as srv

SECRET = "s3cret"
#: The same vector is pinned in apps/back/src/routes/teleop.test.ts, so the
#: two halves cannot drift apart.
VECTOR = "2000000000.ed699e91ba1f8c4d77c5c127987656732ed9ebea0ab638c9ce2937485964ff63"


def _token(exp: int, secret: str = SECRET) -> str:
    sig = hmac.new(secret.encode(), f"teleop:{exp}".encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


@pytest.fixture(autouse=True)
def _no_stale_session():
    srv._active = None
    yield
    srv._active = None


async def _serve():
    server = await serve(srv.handle_client, "127.0.0.1", 0, process_request=srv.authorize)
    port = next(iter(server.sockets)).getsockname()[1]
    return server, f"ws://127.0.0.1:{port}"


async def _status(url: str, **kw) -> int:
    """101 if the handshake was accepted, else the refusal's HTTP status."""
    try:
        async with connect(url, **kw):
            return 101
    except InvalidStatus as exc:
        return exc.response.status_code


def test_the_shared_vector_matches_back():
    assert _token(2_000_000_000) == VECTOR
    assert srv.token_valid(VECTOR, SECRET, now=1_999_999_000) is True


@pytest.mark.parametrize(
    "token",
    [
        "",
        "garbage",
        "abc.def",
        f"{int(time.time()) + 60}.{'0' * 64}",
        _token(int(time.time()) + 60, "other"),
    ],
)
def test_bad_tokens_are_refused(token):
    assert srv.token_valid(token, SECRET, now=time.time()) is False


def test_an_expired_token_is_refused():
    assert srv.token_valid(_token(1000), SECRET, now=1001.0) is False


async def test_a_foreign_origin_is_refused(monkeypatch):
    monkeypatch.setattr(srv, "TELEOP_SECRET", "")
    server, url = await _serve()
    async with server:
        assert await _status(url, origin="http://evil.example") == 403
        assert await _status(url, origin="http://localhost:3001") == 101


async def test_no_secret_keeps_working_without_a_token(monkeypatch):
    monkeypatch.setattr(srv, "TELEOP_SECRET", "")
    server, url = await _serve()
    async with server:
        assert await _status(url) == 101


async def test_a_secret_fails_closed_without_a_valid_token(monkeypatch):
    monkeypatch.setattr(srv, "TELEOP_SECRET", SECRET)
    server, url = await _serve()
    async with server:
        assert await _status(url, origin="http://localhost:3001") == 401
        assert await _status(f"{url}/?token={_token(1000)}") == 401
        good = f"{url}/?token={_token(int(time.time()) + 60)}"
        assert await _status(good, origin="http://localhost:3001") == 101
        # A valid token does not excuse a foreign page.
        assert await _status(good, origin="http://evil.example") == 403
