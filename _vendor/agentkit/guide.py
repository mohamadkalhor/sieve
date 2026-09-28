"""§3.8's self-description: the guide, ``/llms.txt``, the contract header, the spec.

``mount(app, guide_markdown_path, llms_text)`` adds four things to a FastAPI app
and nothing else:

* ``GET /v1/guide`` -- the app's guide: the markdown file at
  ``guide_markdown_path`` (an app ships it inside its own package), answered as
  ``text/markdown``.
* ``GET /llms.txt`` -- the short text an agent reads first, as ``text/plain``.
* ``AIO-Contract: 1`` on **every** ``/v1`` response, the 200 and the 404 alike,
  so a client learns which revision of §3 it is talking to from any answer it
  gets.
* ``GET /v1/openapi.json`` -- the app's own generated spec, with ``info.version``
  set to the app's version and the ``x-aio-contract: 1`` extension, so one fetch
  says what the app can do *and* which contract it speaks.

Both documents answer ``Cache-Control: max-age=300``: an agent that re-reads the
guide every turn costs one request a minute instead of one a turn. The guide's
markdown is read once, here, at wiring time -- an app whose guide is missing
fails when it is built, not at the first fetch.

Wiring order matters once: :func:`mount` wraps the app's **outermost** layer, so
the header sits outside the app's own middleware *and* outside Starlette's
``ServerErrorMiddleware`` -- the layer that answers §3.3's 500. A 413 written by
:class:`~agentkit.limits.BodyCap` and a crash inside a route are both ``/v1``
answers, so both carry it. Call :func:`mount` last, where the app is built:
middleware added afterwards is built into a new stack, outside this one.

The same spec is served at ``/openapi.json`` (FastAPI's own URL): this module
wraps ``app.openapi`` rather than adding a second copy, so the two URLs can
never disagree and the app's snapshot test covers both.

Environment, §3.10's list, named here because this file is what an agent reads
first (``/v1/guide`` is where an app repeats it in its own words):

* ``AIO_CURSOR_KEY`` -- the app's cursor HMAC key, at least 16 bytes (§3.6),
  read with ``cursor.key_from_env()``: a value shorter than that is refused by
  name, and a missing one refuses startup outside ``APP_ENV=dev`` (§3.10) --
  under ``dev`` the app signs with a random key for the process and warns once,
  so cursors do not survive a reload. The app hands it to
  ``jobs.Jobs(key=...)`` and to ``cursor.encode``/``cursor.decode``.
* ``AIO_RATE_REQ`` / ``AIO_RATE_WRITE`` / ``AIO_RATE_RUN`` -- §3.7's three
  buckets, per key, per minute.
* ``AIO_JOB_WORKERS`` -- §3.5's pool size.
* ``AIO_JOB_RETENTION_DAYS`` -- §3.10's retention for ``aio_jobs``: the pool's
  keeper deletes ``succeeded``/``failed``/``cancelled`` rows whose ``finished``
  is older than this, at most once an hour (default 30). ``0`` keeps every row.
* ``AIO_AUDIT_RETENTION_DAYS`` -- the same window for the audit table, deleted
  by the same keeper (default 90; ``0`` keeps every row).
* ``APP_ENV`` -- ``dev`` is the only value that relaxes §3.10's defaults.

``conformance/`` checks all of this over HTTP, which is why nothing here is a
convention in prose: the header, the two cache directives and the extension are
each one assertion away from being wrong.
"""

from __future__ import annotations

import logging
from os import PathLike
from pathlib import Path
from typing import Any, Sequence

from fastapi import FastAPI
from fastapi.responses import JSONResponse, Response
from starlette.datastructures import MutableHeaders

from . import CONTRACT_VERSION, VERSION

__all__ = [
    "CONTRACT_HEADER",
    "GUIDE_PATH",
    "LLMS_PATH",
    "MAX_AGE",
    "OPENAPI_PATH",
    "mount",
]

logger = logging.getLogger(__name__)

#: §3.8's header, and the value both it and the spec extension carry.
CONTRACT_HEADER = "AIO-Contract"
#: The three addresses §3.8 names.
GUIDE_PATH = "/v1/guide"
LLMS_PATH = "/llms.txt"
OPENAPI_PATH = "/v1/openapi.json"
#: §3.8's cache directive, in seconds.
MAX_AGE = 300

_MARKDOWN = "text/markdown; charset=utf-8"
_PLAIN = "text/plain; charset=utf-8"
_CACHE = f"max-age={MAX_AGE}"
#: The extension's key in the spec (§3.8); the value is ``CONTRACT_VERSION``.
SPEC_EXTENSION = "x-aio-contract"

#: §3.8's other extension: the §3 surfaces the app claims to have. A conformance
#: run reads it before it starts, so an app that publishes it is measured against
#: the checks it asked for, and one that says nothing is measured against all of
#: them (all of them is what a run does when there is nothing to read).
SURFACES_EXTENSION = "x-aio-surfaces"


def _under_v1(path: str) -> bool:
    """Is this path on the machine surface? ``/v1something`` is not (§3.3)."""
    return path == "/v1" or path.startswith("/v1/")


class _ContractHeader:
    """``AIO-Contract: 1`` on every ``/v1`` response, whatever the answer is.

    Bare ASGI rather than ``BaseHTTPMiddleware``: this must not touch the body of
    a streaming answer, and it must see messages another middleware wrote
    straight to ``send`` (a 413 from the body cap, say) as well as the ones a
    route returned. :func:`mount` wraps it around the app's outermost layer, so
    it is what Starlette's own ``ServerErrorMiddleware`` -- the 500 -- is inside.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not _under_v1(str(scope.get("path") or "")):
            await self.app(scope, receive, send)
            return

        async def with_header(message: Any) -> None:
            if message.get("type") == "http.response.start":
                MutableHeaders(scope=message)[CONTRACT_HEADER] = str(CONTRACT_VERSION)
            await send(message)

        await self.app(scope, receive, with_header)


def mount(
    app: FastAPI,
    guide_markdown_path: str | PathLike[str],
    llms_text: str,
    *,
    version: str | None = None,
    surfaces: Sequence[str] | None = None,
) -> None:
    """Give ``app`` §3.8's four things: guide, ``/llms.txt``, header, spec.

    ``guide_markdown_path`` is the app's own guide, as a path to a ``.md`` file
    that ships with the app. ``llms_text`` is the short text: what the app is,
    which credential opens it, and where the guide is. ``version`` overrides the
    version in the spec; left out, the app's own (what it passed to
    ``FastAPI(version=...)``) is kept. ``surfaces`` is the §3 groups of checks
    the app claims -- it goes into the spec as ``x-aio-surfaces``, and a
    conformance run reads it there; left out, the app claims nothing and a run
    checks it against every surface.

    Raises ``ValueError`` for a guide that is not a file, or an empty guide or
    text: this is wiring time, and both are mistakes the app should hear about
    once rather than on every request.
    """
    guide = _read_guide(guide_markdown_path)
    short = _checked_text(llms_text, "llms_text")

    _wrap_outermost(app)

    @app.get(
        GUIDE_PATH,
        response_class=Response,
        tags=["self-description"],
        summary="The app's guide, in markdown.",
        responses={
            200: {
                "description": (
                    "What this app is, how to authenticate, and what it can do."
                ),
                "content": {"text/markdown": {"schema": {"type": "string"}}},
            }
        },
    )
    def guide_markdown() -> Response:
        """§3.8's guide: the markdown the app shipped, cached for five minutes."""
        return Response(
            content=guide, media_type=_MARKDOWN, headers={"Cache-Control": _CACHE}
        )

    @app.get(
        LLMS_PATH,
        response_class=Response,
        tags=["self-description"],
        summary="The short text an agent reads first.",
        responses={
            200: {
                "description": "One screen: what this app is, and how to call it.",
                "content": {"text/plain": {"schema": {"type": "string"}}},
            }
        },
    )
    def llms_txt() -> Response:
        """§3.8's ``/llms.txt``, cached for five minutes like the guide."""
        return Response(content=short, media_type=_PLAIN, headers={"Cache-Control": _CACHE})

    @app.get(
        OPENAPI_PATH,
        response_class=JSONResponse,
        include_in_schema=False,
        tags=["self-description"],
        summary="This app's OpenAPI, with §3.8's extension.",
    )
    def openapi_json() -> JSONResponse:
        """The spec, generated from the routes: ``app.openapi()``, §3.8 applied."""
        return JSONResponse(content=app.openapi())

    _wrap_openapi(app, version, surfaces)


def _wrap_outermost(app: FastAPI) -> None:
    """Make :class:`_ContractHeader` the outside of everything the app has.

    ``app.add_middleware`` would place it *inside* Starlette's
    ``ServerErrorMiddleware``, and that is the layer that answers §3.3's 500 --
    so a crash would be the one ``/v1`` answer missing the header, which is
    exactly the answer a client is least sure about. Wrapping the built stack
    instead covers the routes, the app's own middleware, the error handlers and
    the 500 alike.

    Call :func:`mount` last: ``Starlette`` builds a new stack when the middleware
    list changes, and middleware added after this call would end up outside it.
    """
    stack = app.middleware_stack
    if stack is None:  # added middleware, not built yet: build it as Starlette would
        stack = app.build_middleware_stack()
    app.middleware_stack = _ContractHeader(stack)


def _wrap_openapi(
    app: FastAPI, version: str | None, surfaces: Sequence[str] | None = None
) -> None:
    """Put §3.8's two facts on every future read of ``app.openapi()``.

    FastAPI caches the spec in ``app.openapi_schema`` and serves it at
    ``/openapi.json``; wrapping the method means the app's own snapshot test,
    that URL and ``/v1/openapi.json`` all see the same document.
    """
    original = app.openapi

    def contract_openapi() -> dict[str, Any]:
        return _inject(original(), version, surfaces)

    app.openapi = contract_openapi  # type: ignore[method-assign]


def _inject(
    spec: dict[str, Any],
    version: str | None,
    surfaces: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Set ``info.version`` and the contract's extensions on a spec, in place."""
    info = spec.setdefault("info", {})
    info["version"] = str(version or info.get("version") or VERSION)
    spec[SPEC_EXTENSION] = CONTRACT_VERSION
    if surfaces is not None:
        spec[SURFACES_EXTENSION] = [str(name) for name in surfaces]
    return spec


def _read_guide(path: str | PathLike[str]) -> str:
    """The guide's markdown, read once. A missing or empty guide is an error."""
    source = Path(path)
    if not source.is_file():
        raise ValueError(
            f"guide: no markdown at {source!s} -- §3.8 ships the guide inside the "
            "app's package, and a missing one is a wiring mistake"
        )
    text = source.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"guide: {source!s} is empty")
    return text


def _checked_text(text: str, what: str) -> str:
    """The ``/llms.txt`` text: a non-empty string, with one trailing newline."""
    if not isinstance(text, str):
        raise TypeError(f"guide: {what} is the text itself, not a {type(text).__name__}")
    if not text.strip():
        raise ValueError(f"guide: {what} is empty")
    return text.strip() + "\n"
