"""The nginx edge boundary (AUTH-CONTRACT.md section 7; gate/APP-INTEGRATION.md).

Cloudflare Access is gone. nginx on the box is now the only wall, and it lets
`/v1/*` through when the request carries *either* a live gate session (the
cookie) *or* any `Authorization: Bearer` header at all -- validating that
bearer is this app's job, not nginx's, because nginx cannot tell a real Sieve
token from a guess. So a request that reached us this way carries
`X-Gate-Edge` (nginx sets it, and strips whatever a client sent under that
name -- see `gate/AUTH-CONTRACT.md` section 7) and must resolve to *somebody*
before it goes near a route: a bearer that fails to resolve is a 401, never a
silent fall-through to the open-read behaviour `require_read()` still gives a
request with no edge header at all -- an agent on the box, the in-process MCP
bridge, a test, exactly as before.

A request without `X-Gate-Edge` reached us straight over loopback and is
untouched by any of this.

This has to run once, centrally, ahead of every route under `/v1`: writing it
into each route would eventually miss one, and it already had --
`POST /v1/connectors/{id}/test` and `/pull` carried no scope dependency at
all before this file existed. A FastAPI dependency on the router would work
for an ordinary handler but not for `GET /v1/events`, whose handler holds a
`StreamingResponse` open for as long as the browser listens; a
`BaseHTTPMiddleware` has to buffer that response to look at it, which means
holding the whole SSE stream in memory until it ends, i.e. never. So this is
a bare ASGI middleware: it reads headers off `scope` and, once past them,
touches neither `receive` nor `send` again.

The same middleware carries the CSRF belt (AUTH-CONTRACT.md's own login
forms already have a real token; this is the second, cheaper line for
Sieve's own JSON writes): a cookie-authenticated state-changing call that
carries `X-Gate-Edge` is refused when `Sec-Fetch-Site` says `cross-site` or
`Origin` names a different host than `Host`. A bearer-authenticated call is
exempt -- it already proved something a cross-site page cannot forge.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from sieve.api import auth

_EDGE_HEADER = b"x-gate-edge"
_STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


def _unauthenticated(message: str) -> JSONResponse:
    # `unauthenticated` is gate's own wall's code (AUTH-CONTRACT.md section
    # 9) so a front-end fetch wrapper written against that contract reacts to
    # this the same way it reacts to gate's `/auth/wall`.
    return JSONResponse(status_code=401, content={"error": {"code": "unauthenticated", "message": message}})


def _cross_site(message: str) -> JSONResponse:
    return JSONResponse(status_code=403, content={"error": {"code": "cross_site", "message": message}})


def _csrf_refusal(request: Request) -> JSONResponse | None:
    """Why a cookie-authenticated write should be refused, or None."""
    if request.headers.get("sec-fetch-site") == "cross-site":
        return _cross_site("cross-site request refused")
    origin = request.headers.get("origin")
    if origin:
        origin_host = urlsplit(origin).hostname
        request_host = (request.headers.get("host") or "").split(":", 1)[0]
        if origin_host and request_host and origin_host != request_host:
            return _cross_site("origin does not match this host")
    return None


class EdgeAuthMiddleware:
    """Require a real identity on every `/v1` call that came from the internet."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/v1"):
            await self.app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        if _EDGE_HEADER not in headers:
            # Straight over loopback: an agent, a timer, the MCP bridge, a
            # test. Keeps whatever rule the route already applies.
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive=receive)
        secret = auth.bearer(request.headers.get("authorization"))
        if secret:
            # A bearer is present: it must resolve, full stop. Never falls
            # back to treating the caller as anonymous -- that fallback is
            # exactly what an open-read route used to do, and a guessed
            # token must not be quietly downgraded into "no token sent".
            if auth.resolve_bearer(request, secret) is None:
                await _unauthenticated("that bearer token is not known here")(scope, receive, send)
                return
        else:
            if auth.gate_identity(request) is None:
                await _unauthenticated("no bearer token and no gate session")(scope, receive, send)
                return
            if request.method in _STATE_CHANGING:
                refused = _csrf_refusal(request)
                if refused is not None:
                    await refused(scope, receive, send)
                    return

        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    """Three headers on every response, edge or not.

    These cost nothing, help a browser that ever renders this API's JSON or
    the SPA shell directly, and do not depend on anything the route decided,
    so they belong outside the auth check rather than duplicated per route.
    """

    _HEADERS: tuple[tuple[bytes, bytes], ...] = (
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"strict-origin-when-cross-origin"),
        (b"x-frame-options", b"SAMEORIGIN"),
    )

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.extend(self._HEADERS)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
