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
#: The same vectors are pinned in apps/back/src/routes/teleop.test.ts, so the
#: two halves cannot drift apart. The first is the pre-nonce format.
VECTOR = "2000000000.ed699e91ba1f8c4d77c5c127987656732ed9ebea0ab638c9ce2937485964ff63"
NONCE_VECTOR = (
    "2000000000.00112233445566778899aabbccddeeff"
    ".7d584c9233bce66c16e6bebbf5e8875eb04b0f0f9f6593f9fea1c313677920c1"
)


def _token(exp: int, secret: str = SECRET, nonce: str | None = None) -> str:
    body = f"{exp}" if nonce is None else f"{exp}.{nonce}"
    sig = hmac.new(secret.encode(), f"teleop:{body}".encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


@pytest.fixture(autouse=True)
def _no_stale_session():
    srv._active = None
    srv._spent.clear()
    yield
    srv._active = None
    srv._spent.clear()


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


def test_the_shared_vectors_match_back():
    assert _token(2_000_000_000) == VECTOR
    assert _token(2_000_000_000, nonce="00112233445566778899aabbccddeeff") == NONCE_VECTOR
    for token in (VECTOR, NONCE_VECTOR):
        assert srv.token_valid(token, SECRET, now=1_999_999_000) is True


@pytest.mark.parametrize(
    "token",
    [
        "",
        "garbage",
        "abc.def",
        f"{int(time.time()) + 60}.{'0' * 64}",
        _token(int(time.time()) + 60, "other"),
        # A nonce the signature does not cover.
        _token(int(time.time()) + 60, nonce="a")[:-64] + _token(int(time.time()) + 60)[-64:],
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


async def test_a_token_opens_the_socket_once(monkeypatch):
    # ws:// on the LAN can be sniffed: a replayed token must not open a second session.
    monkeypatch.setattr(srv, "TELEOP_SECRET", SECRET)
    server, url = await _serve()
    async with server:
        token = _token(int(time.time()) + 60, nonce="n1")
        assert await _status(f"{url}/?token={token}") == 101
        assert await _status(f"{url}/?token={token}") == 401
        # A reconnect with a fresh token from back is fine.
        assert await _status(f"{url}/?token={_token(int(time.time()) + 60, nonce='n2')}") == 101


def test_a_spent_token_is_forgotten_once_it_has_expired():
    assert srv.spend_token(_token(1000), now=999.0) is True
    assert srv.spend_token(_token(1000), now=999.5) is False
    srv.spend_token(_token(2000), now=1001.0)
    assert _token(1000) not in srv._spent
