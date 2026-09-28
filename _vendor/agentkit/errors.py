"""§3.3 -- one error envelope under ``/v1``.

Every non-2xx answer from a path under ``/v1`` is

    {"error": {"code": …, "message": …, "detail"?, "request_id": …}}

with the same id in the ``X-Request-Id`` header, ``Content-Type:
application/json`` whatever the ``Accept`` header says, ``Retry-After`` on 429
and 503, and never a line of the exception in a 500. A path outside ``/v1`` --
an app's own ``/api`` side -- is left in FastAPI's default shape: the kit owns
the machine surface only.

The handlers come from sieve's ``sieve/api/app.py:102-168`` and press's
``press/http.py:77`` (the donors), with ``request_id`` added and the path rule
made explicit:

| raised | answer |
|---|---|
| :class:`APIError` | its own status, code, message and detail |
| ``HTTPException`` | its status, the code of :data:`STATUS_CODES`, its ``detail`` when that is a string |
| ``RequestValidationError`` | 422 ``invalid_body`` with ``detail.fields`` = ``[{"path", "message"}]`` |
| malformed JSON in the body | 400 ``bad_json`` |
| a body declared as neither JSON nor a form | 415 ``bad_content_type`` |
| anything else | 500 ``server_error``, the traceback in the log only |

FastAPI's own 415 for a wrong content type does not exist in this version (a
foreign content type reaches the route as raw bytes and fails validation as if a
field were missing), which is why the content-type row lives here: "your field
is missing" is a lie when the real answer is "you sent text/plain".

The request id is the caller's when it looks like an id (``[A-Za-z0-9-]{1,64}``)
and a fresh ``uuid4`` hex otherwise: the header ends up in log lines and in the
audit rows (§3.10), so a caller does not get to write arbitrary text there.
"""

from __future__ import annotations

import email.message
import logging
import re
import uuid
from typing import Any, Awaitable, Callable

from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

__all__ = [
    "APIError",
    "MESSAGES",
    "PREFIX",
    "REQUEST_ID_HEADER",
    "STATUS_CODES",
    "envelope",
    "install",
    "request_id",
]

logger = logging.getLogger("agentkit.errors")

# Only this prefix is the machine surface; ``/v1something`` is not.
PREFIX = "/v1"

REQUEST_ID_HEADER = "X-Request-Id"
# The incoming id is echoed when it is a plausible id, and replaced when it is
# not: whatever arrives here ends up in the log and in an audit row.
REQUEST_ID_RE = re.compile(r"\A[A-Za-z0-9-]{1,64}\Z")

RETRY_AFTER_HEADER = "Retry-After"
# §3.3: Retry-After on 429 and 503. One second is the floor when the raiser did
# not name a number -- the header is part of the contract, so it is always there.
RETRY_STATUSES = frozenset({429, 503})
DEFAULT_RETRY_AFTER = 1

# HTTPException status -> the contract's code (§3.3). A status that is not here
# keeps FastAPI's ``http_<status>`` shape: an app may answer a status the
# contract has not named, and the code still says which one it was.
STATUS_CODES: dict[int, str] = {
    400: "bad_request",
    401: "sign_in",
    403: "not_allowed",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "too_large",
    415: "bad_content_type",
    422: "invalid_body",
    429: "rate_limited",
    503: "unavailable",
}

# One sentence per code, for the cases where the raiser gave no sentence of its
# own. A client branches on ``code``; this is for the person reading a log.
MESSAGES: dict[str, str] = {
    "auth_unavailable": "The gate cannot be reached, so keys are refused for now.",
    "bad_content_type": "Send the body as application/json.",
    "bad_cursor": "The cursor is not valid for this query.",
    "bad_idempotency_key": (
        "An Idempotency-Key is 1-128 characters of A-Z a-z 0-9 _ . : -"
    ),
    "bad_json": "The body is not valid JSON.",
    "bad_key": "That API key is not valid.",
    "bad_request": "The request is not valid.",
    "conflict": "The request conflicts with the current state.",
    "idempotency_interrupted": (
        "That Idempotency-Key was interrupted by a restart; poll the job it "
        "names, or send a new key."
    ),
    "idempotency_key_required": "This call needs an Idempotency-Key header.",
    "idempotency_mismatch": (
        "That Idempotency-Key was used with a different request."
    ),
    "in_progress": "That Idempotency-Key is already in flight.",
    "invalid_body": "The body does not match the contract.",
    "method_not_allowed": "That method is not allowed on this route.",
    "mixed_credentials": "Send the API key or the session cookie, not both.",
    "not_allowed": "You are not allowed to do that.",
    "not_found": "There is nothing at that address.",
    "rate_limited": "Too many requests; try again shortly.",
    "server_error": "Something went wrong; the log has the detail.",
    "sign_in": "Sign in to continue.",
    "too_large": "The body is larger than this route accepts.",
    "unavailable": "The service is busy; try again shortly.",
}

# Request sources in a FastAPI error location, dropped from a field's path.
_SOURCES = frozenset({"body", "query", "path", "header", "cookie"})
# A body of one of these is not "wrong content type": a form route validates
# form fields, and its 422 is a 422.
_FORM_TYPES = frozenset(
    {"multipart/form-data", "application/x-www-form-urlencoded"}
)

_NO_HANDLERS = (
    "errors.install() needs the FastAPI application, not a router: FastAPI keeps "
    "its exception handlers on the app, and a request that matches no route under "
    "/v1 never reaches a router at all ('unknown /v1 path -> 404' cannot be "
    "answered from one). Call errors.install(app) where the app is built."
)


class APIError(Exception):
    """A refusal the kit and every app raise: the §3.3 envelope, verbatim.

    ``status`` is the HTTP status, ``code`` the stable machine name a client
    branches on, ``message`` one sentence a person can read, ``detail`` an
    optional JSON value saying which part of the request failed (422 puts
    ``{"fields": […]}}`` there, §3.3). ``retry_after`` is the integer seconds of
    a 429 or 503; when omitted, one second is sent, because the contract says
    those two statuses carry the header.
    """

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        detail: Any = None,
        *,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = int(status)
        self.code = str(code)
        self.message = str(message)
        self.detail = detail
        self.retry_after = retry_after

    def __repr__(self) -> str:
        return f"APIError({self.status}, {self.code!r}, {self.message!r})"


def request_id(request: Any) -> str:
    """The id of this request: the caller's when it is a plausible id, else ours.

    Settled once and kept on ``request.state``: the id goes into the response
    header, into the envelope's body, into the log line and into an audit row
    (§3.10), and one request must not be able to produce two answers -- a
    refusal stored by §3.4 is replayed later as the bytes the first caller saw.
    """
    settled = getattr(getattr(request, "state", None), "request_id", None)
    if isinstance(settled, str) and settled:
        return settled
    given = request.headers.get(REQUEST_ID_HEADER) or ""
    rid = given if REQUEST_ID_RE.match(given) else uuid.uuid4().hex
    try:
        request.state.request_id = rid
    except AttributeError:  # not a FastAPI request: nothing to remember it on
        pass
    return rid


def envelope(
    status: int,
    code: str,
    message: str,
    *,
    request_id: str,
    detail: Any = None,
    retry_after: int | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """The response every non-2xx under ``/v1`` is built from.

    ``detail`` is left out entirely when there is none, so a client can tell
    "no detail" from "an empty detail". Headers the raiser gave (``Allow`` on a
    405) survive; the kit's own two win over them.
    """
    body: dict[str, Any] = {"code": code, "message": message}
    if detail is not None:
        body["detail"] = detail
    body["request_id"] = request_id
    out = dict(headers or {})
    out[REQUEST_ID_HEADER] = request_id
    if status in RETRY_STATUSES:
        seconds = DEFAULT_RETRY_AFTER if retry_after is None else int(retry_after)
        out[RETRY_AFTER_HEADER] = str(seconds)
    return JSONResponse(status_code=status, content={"error": body}, headers=out)


def install(app_or_router: Any) -> Any:
    """Register the four handlers, and return what was handed in.

    Hand it the app that includes the ``/v1`` router, and call it once, before
    the app serves anything: the handler table is read when FastAPI builds the
    middleware stack. The handlers themselves answer only paths under
    :data:`PREFIX`, so a router for ``/api`` is untouched, and it does not
    matter whether the app serves more routes than the kit's.
    """
    add = getattr(app_or_router, "add_exception_handler", None)
    if add is None:
        raise TypeError(_NO_HANDLERS)
    add(APIError, api_error)
    add(StarletteHTTPException, http_error)
    add(RequestValidationError, validation_error)
    add(Exception, unhandled)
    return app_or_router


async def api_error(request: Any, exc: APIError) -> JSONResponse:
    """A refusal the app raised: its own words, in the envelope."""
    rid = request_id(request)
    if not _under_v1(request):
        # The kit does not own /api: an APIError escaping there still answers
        # with its status, in FastAPI's default shape, and nothing more.
        return JSONResponse(status_code=exc.status, content={"detail": exc.message})
    headers = None
    if exc.retry_after is not None:
        # §3.4's 409 ``in_progress`` names Retry-After too, so §3.3's rule for
        # 429 and 503 is a floor rather than a ceiling: a raiser that names
        # seconds gets the header whatever the status is.
        headers = {RETRY_AFTER_HEADER: str(int(exc.retry_after))}
    return envelope(
        exc.status,
        exc.code,
        exc.message,
        request_id=rid,
        detail=exc.detail,
        retry_after=exc.retry_after,
        headers=headers,
    )


async def http_error(request: Any, exc: StarletteHTTPException) -> JSONResponse:
    """A ``HTTPException``: its status through the contract's code map."""
    if not _under_v1(request):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail},
            headers=getattr(exc, "headers", None),
        )
    rid = request_id(request)
    code = STATUS_CODES.get(exc.status_code, f"http_{exc.status_code}")
    message, detail = _message_of(exc.detail, code)
    return envelope(
        exc.status_code,
        code,
        message,
        request_id=rid,
        detail=detail,
        headers=getattr(exc, "headers", None),
    )


async def validation_error(
    request: Any, exc: RequestValidationError
) -> JSONResponse:
    """422 with the failing fields, 400 for a malformed body, 415 for a foreign one."""
    if not _under_v1(request):
        return JSONResponse(
            status_code=422, content={"detail": jsonable_encoder(exc.errors())}
        )
    rid = request_id(request)
    errors = list(exc.errors())
    if any(str(error.get("type")) == "json_invalid" for error in errors):
        return envelope(400, "bad_json", MESSAGES["bad_json"], request_id=rid)
    if _foreign_content_type(request):
        return envelope(
            415, "bad_content_type", MESSAGES["bad_content_type"], request_id=rid
        )
    fields = [{"path": _field_path(e), "message": _field_message(e)} for e in errors]
    return envelope(
        422,
        "invalid_body",
        MESSAGES["invalid_body"],
        request_id=rid,
        detail={"fields": fields},
    )


async def unhandled(request: Any, exc: Exception) -> PlainTextResponse | JSONResponse:
    """A bug: the envelope says so, the log says where, nobody else is told."""
    rid = request_id(request)
    logger.error(
        "unhandled %s in %s %s",
        type(exc).__name__,
        request.method,
        request.url.path,
        exc_info=exc,
    )
    if not _under_v1(request):
        return PlainTextResponse("Internal Server Error", status_code=500)
    return envelope(500, "server_error", MESSAGES["server_error"], request_id=rid)


def _under_v1(request: Any) -> bool:
    path = request.url.path
    return path == PREFIX or path.startswith(PREFIX + "/")


def _message_of(detail: Any, code: str) -> tuple[str, Any]:
    """``(message, detail)`` for an ``HTTPException``'s detail."""
    fallback = MESSAGES.get(code, "The request was refused.")
    if detail is None:
        return fallback, None
    if isinstance(detail, str):
        return (detail.strip() or fallback), None
    if isinstance(detail, dict):
        return fallback, (detail or None)
    return fallback, detail


def _field_path(error: Any) -> str:
    """``body.user.name`` -> ``user.name``; a problem with the whole body -> ``body``."""
    parts = [str(part) for part in (error.get("loc") or ())]
    if len(parts) > 1 and parts[0] in _SOURCES:
        parts = parts[1:]
    return ".".join(parts) or "body"


def _field_message(error: Any) -> str:
    text = str(error.get("msg") or "Is not valid")
    for prefix in ("Value error, ", "Assertion failed, "):
        text = text.removeprefix(prefix)
    text = text.strip() or "Is not valid"
    return text[:1].upper() + text[1:]


def _foreign_content_type(request: Any) -> bool:
    """Was the body declared as something that is neither JSON nor a form?

    No ``Content-Type`` at all is not this: a missing header with a missing body
    is a missing body, and saying 415 there would be a worse answer than 422.
    """
    declared = request.headers.get("content-type")
    if not declared:
        return False
    message = email.message.Message()
    message["content-type"] = declared
    main, sub = message.get_content_maintype(), message.get_content_subtype()
    if main == "application" and (sub == "json" or sub.endswith("+json")):
        return False
    return f"{main}/{sub}" not in _FORM_TYPES
