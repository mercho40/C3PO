"""Small helpers the route modules share."""

from __future__ import annotations

from typing import Any

#: For a non-JSON response (an image, a WAV) that must not be cached either.
NO_STORE = {"Cache-Control": "no-store"}


def no_store(body: Any, status_code: int = 200):  # noqa: ANN201 - starlette types
    """A JSON response nothing may cache: live state, or an answer about one request."""
    from starlette.responses import JSONResponse

    return JSONResponse(body, status_code=status_code, headers=NO_STORE)


async def json_body(request) -> dict:  # noqa: ANN001 - starlette types
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


def origin_of(request) -> str | None:  # noqa: ANN001 - starlette types
    """The request's `Origin`, or None. Browsers send it; native clients do not."""
    try:
        return request.headers.get("origin")
    except Exception:  # noqa: BLE001 — a request without headers is still a request
        return None
