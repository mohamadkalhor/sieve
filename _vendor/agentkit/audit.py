"""§3.10 -- the audit row, and the page that reads it.

§3.10's first bullet in one sentence: *a row per write -- actor user id, key id,
action, target, result, time, request id -- and ``GET /v1/audit`` needs
``admin``.*

:class:`Audit` is one table, ``aio_audit``, in the app's kit-owned ``aio.db``
(§3.4's file, §3.4's one-connection-one-lock shape), and :func:`router` is the
one route that reads it. Four decisions are worth writing down.

* **The row is written where the result is known.** :meth:`Audit.record` is
  called by the app after its handler answered -- and after it *refused*: a
  refused write is the row a security question is asked about, so
  ``result_status`` is the status the caller got, 403 and 429 included. Press
  records refusals the same way (``press/act/executor.py:115-146``). The app may
  hand :meth:`Audit.record` its own connection (``db=…``, the seam §3.4's
  ``set_job`` has) to put the row in the same transaction as the effect it
  describes; with no connection the row is its own commit, and a crash between
  the effect and the row loses the row (see REPORT).
* **The actor and the key are two columns.** ``principal`` is §3.1's id
  (``key:<id>`` or ``user:<id>``) and ``key_id`` is the key that acted, empty for
  a session. "Who" and "which credential" are different questions: a key is
  revoked by one row (§3.2), and the audit row has to keep saying which key did
  it after that row is gone.
* **The order is §3.6's order.** ``at`` is whole seconds off the injectable
  clock and ``id`` (SQLite's rowid) breaks ties inside one second, so
  ``GET /v1/audit`` is a §3.6 list: ``(created, id)`` descending, an opaque
  cursor bound to the query's filters, a limit of at most 200, and a cursor used
  against different filters is 400 ``bad_cursor`` -- :mod:`agentkit.cursor`
  owns that answer and this module never writes it.
* **Nothing secret goes in.** No token, no header, no body: the principal id,
  the key id, the action name, a short target string and a status. ``target``
  is text on purpose -- a JSON blob in a security table is a place a body or a
  token ends up by accident -- and every column has a length cap.

The donors are press's audit line (actor, action name, target, outcome -- written
for refused actions too, ``press/act/executor.py:115-146``) and its audit page
(``press/index.py:387-409``: keyset, newest first). The shape is press's; the
position is §3.6's signed cursor instead of a raw sequence number, because the
number would be guessable and movable.

The route is a plain ``def``: SQLite is blocking and FastAPI runs it in its
threadpool, which is that thread's business and not the event loop's.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from fastapi import APIRouter, Depends, Query, Request

from agentkit import auth, errors
from agentkit import cursor as cursors
from agentkit import limits as rate_limits

__all__ = [
    "ACTION_MAX",
    "ANONYMOUS",
    "COLUMNS",
    "DEFAULT_LIMIT",
    "MCP_PATH",
    "MAX_LIMIT",
    "PRINCIPAL_MAX",
    "SAFE_METHODS",
    "TABLE",
    "TARGET_MAX",
    "Audit",
    "Page",
    "Row",
    "mount",
    "router",
]

logger = logging.getLogger("agentkit.audit")

TABLE = "aio_audit"

# §3.10's middleware: a write is what changes something, so the three methods
# that only read are not it (§3.7 says the same three are the read bucket), and
# the transport's own path is skipped -- see :func:`mount`.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MCP_PATH = "/v1/mcp"

# The actor of a write that reached a route with no principal: a refused write
# is the row a security question is asked about, and ``unknown`` is a lie --
# ``principal`` is NOT NULL, so the row needs a word of its own.
ANONYMOUS = "anon"

# §3.6's limit, and the one this page defaults to: 50 rows is a screenful of
# audit, 200 is the contract's ceiling.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# Every column has a cap: an audit row is a record, not a place to paste things.
PRINCIPAL_MAX = 64
ACTION_MAX = 64
TARGET_MAX = 256
REQUEST_ID_MAX = 64

# The columns in the order the contract names them (§3.10), which is also the
# order :class:`Row` carries them.
COLUMNS = (
    "id",
    "at",
    "principal",
    "key_id",
    "action",
    "target",
    "result_status",
    "request_id",
)

# ``id`` is the rowid: monotonic inside one file, and the tiebreaker of §3.6's
# ``(created, id)`` order. AUTOINCREMENT keeps a deleted row's id from being
# handed out again -- an audit id that names two different rows over time is
# worse than a gap.
_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    at            INTEGER NOT NULL,
    principal     TEXT    NOT NULL,
    key_id        TEXT,
    action        TEXT    NOT NULL,
    target        TEXT,
    result_status INTEGER,
    request_id    TEXT
)
"""

# The page walks ``(at, id)`` descending; both indexes lead with ``at`` so the
# scan is the page and not the table.
_INDEXES = (
    f"CREATE INDEX IF NOT EXISTS {TABLE}_page ON {TABLE} (at, id)",
    f"CREATE INDEX IF NOT EXISTS {TABLE}_principal ON {TABLE} (principal, at, id)",
)

_SELECT = f"SELECT {', '.join(COLUMNS)} FROM {TABLE}"


@dataclass(frozen=True)
class Row:
    """One row of ``aio_audit``, as stored."""

    id: int
    at: int
    principal: str
    key_id: str | None
    action: str
    target: str | None
    result_status: int | None
    request_id: str | None

    @classmethod
    def of(cls, row: Any) -> "Row":
        """The :class:`Row` of a ``sqlite3.Row``."""
        return cls(
            id=int(row["id"]),
            at=int(row["at"]),
            principal=str(row["principal"]),
            key_id=None if row["key_id"] is None else str(row["key_id"]),
            action=str(row["action"]),
            target=None if row["target"] is None else str(row["target"]),
            result_status=(
                None if row["result_status"] is None else int(row["result_status"])
            ),
            request_id=None if row["request_id"] is None else str(row["request_id"]),
        )

    def as_dict(self) -> dict[str, Any]:
        """The row as the route answers it: §3.10's eight fields, no more."""
        return {column: getattr(self, column) for column in COLUMNS}


@dataclass(frozen=True)
class Page:
    """One §3.6 page: the rows, and the cursor of the next one."""

    items: tuple[Row, ...]
    next_cursor: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": [row.as_dict() for row in self.items],
            "next_cursor": self.next_cursor,
        }


class Audit:
    """§3.10's table: one row per write, and the page that reads it.

    ``path`` is the app's kit-owned file (``":memory:"`` for tests). ``key`` is
    §3.6's cursor key -- at least 16 bytes of the app's own secret material,
    which is what makes the page's cursor unforgeable; it is kept here and
    printed by nothing. ``clock`` is injectable so a test can place rows in the
    past without waiting.
    """

    def __init__(
        self,
        path: str,
        key: bytes | None = None,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.path = str(path)
        self.key = key
        if key is not None:
            _cursor_key(key)
        self._clock = clock
        # One connection, one lock, short statements: the same shape §3.4 uses
        # for the other table of this file.
        self._lock = threading.RLock()
        self._db = sqlite3.connect(
            self.path, check_same_thread=False, isolation_level=None
        )
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA busy_timeout=5000")
        if self.path != ":memory:":
            self._db.execute("PRAGMA journal_mode=WAL")
        with self._lock:
            self._db.execute(_DDL)
            for index in _INDEXES:
                self._db.execute(index)

    def __repr__(self) -> str:
        # The cursor key is never printed, not even here.
        return f"Audit(path={self.path!r})"

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Close the connection. A closed store refuses further work."""
        with self._lock:
            self._db.close()

    def count(self) -> int:
        """How many rows this table holds."""
        with self._lock:
            return int(self._db.execute(f"SELECT COUNT(*) FROM {TABLE}").fetchone()[0])

    # -- writing ------------------------------------------------------------

    def record(
        self,
        principal: Any,
        action: Any,
        target: Any = None,
        result_status: Any = None,
        *,
        key_id: Any = None,
        request_id: Any = None,
        at: Any = None,
        db: sqlite3.Connection | None = None,
    ) -> int:
        """Write one audit row; return its id.

        ``principal`` is §3.1's :class:`~agentkit.auth.Principal` or its id as a
        string, and ``key_id`` is taken from the principal when it has one, so
        the usual call is ``audit.record(principal, "job.create", job_id, 202)``.
        ``result_status`` is the status the caller got, refusals included.
        ``at`` defaults to the clock, whole seconds. ``db`` is a connection to
        this same file -- hand in the one the effect was written with and the
        row commits with it.

        A value that cannot be written down is refused here, loudly: an audit
        row with a silently truncated or empty field is a record that lies.
        """
        who = _principal(principal)
        if key_id is None:
            key_id = getattr(principal, "key_id", None)
        values = (
            _at(self._clock() if at is None else at),
            who,
            _optional(key_id, "key_id", PRINCIPAL_MAX),
            _required(action, "action", ACTION_MAX),
            _optional(target, "target", TARGET_MAX),
            _status(result_status),
            _optional(request_id, "request_id", REQUEST_ID_MAX),
        )
        connection = db if db is not None else self._db
        with self._lock:
            written = connection.execute(
                f"INSERT INTO {TABLE} (at, principal, key_id, action, target,"
                " result_status, request_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                values,
            )
            if db is None:
                self._db.commit()
        return int(written.lastrowid or 0)

    def record_request(
        self,
        request: Any,
        action: Any,
        target: Any = None,
        result_status: Any = None,
        *,
        at: Any = None,
    ) -> int:
        """Record a write from inside the request that made it.

        The principal and the request id come off the request (§3.1 leaves the
        principal on ``request.state``; §3.3 settles the id), so an app's write
        path is one call and cannot record the wrong actor. A route that reaches
        here without a principal is a route that forgot
        ``Depends(credential(app_cfg))``; that is a wiring bug, so it is a 500
        and nothing is written -- a row under no actor is not an option
        (``principal`` is NOT NULL).
        """
        principal = auth.principal_of(request)
        if principal is None:
            logger.error(
                "audit: %s %s has no principal; is it missing "
                "Depends(credential(app_cfg))?",
                getattr(request, "method", "?"),
                getattr(getattr(request, "url", None), "path", "?"),
            )
            raise errors.APIError(500, "server_error", errors.MESSAGES["server_error"])
        return self.record(
            principal,
            action,
            target,
            result_status,
            request_id=errors.request_id(request),
            at=at,
        )

    # -- retention ----------------------------------------------------------

    def purge(self, older_than_seconds: Any) -> int:
        """§3.10's retention: delete rows older than the window; return how many.

        The keeper of §3.5's pool calls this (``AIO_AUDIT_RETENTION_DAYS``,
        default 90) -- the file is kit-owned and one audit table per app, and a
        table nobody ever trims is a table that grows with every write the app
        has ever served.

        ``older_than_seconds`` is a window, not a time: the cutoff is that many
        seconds behind the clock, so the caller's number is the policy. A
        non-positive window is refused rather than treated as ``0`` -- ``0``
        means *keep everything*, which is a decision the caller makes by not
        calling this at all.
        """
        window = float(older_than_seconds)
        if window <= 0:
            raise ValueError("a retention window is a positive number of seconds")
        cutoff = _at(self._clock() - window)
        with self._lock:
            return int(
                self._db.execute(
                    f"DELETE FROM {TABLE} WHERE at < ?", (cutoff,)
                ).rowcount
            )

    # -- reading ------------------------------------------------------------

    def page(
        self,
        *,
        limit: Any = DEFAULT_LIMIT,
        cursor: Any = None,
        principal: Any = None,
        action: Any = None,
        target: Any = None,
    ) -> Page:
        """One §3.6 page of the audit, newest first.

        ``principal``/``action``/``target`` are equality filters and they are
        the query the cursor is bound to: a cursor minted for one set of them is
        400 ``bad_cursor`` against another. ``limit`` is 1 to 200.
        """
        size = _limit(limit)
        key = _cursor_key(self.key)
        filters = {
            "principal": None if principal is None else str(principal),
            "action": None if action is None else str(action),
            "target": None if target is None else str(target),
        }
        where, args = _where(filters, _position(cursor, filters, key))
        with self._lock:
            rows = self._db.execute(
                f"{_SELECT}{where} ORDER BY at DESC, id DESC LIMIT ?",
                [*args, size + 1],
            ).fetchall()
        # One row over the page is how "is there more" is answered without a
        # second query -- and it is dropped before anything sees it.
        more = len(rows) > size
        items = tuple(Row.of(row) for row in rows[:size])
        last = items[-1] if items else None
        return Page(
            items=items,
            next_cursor=(
                cursors.encode(last.at, last.id, filters, key)
                if more and last is not None
                else None
            ),
        )


class _Writes:
    """The ASGI middleware :func:`mount` adds: one row per write under ``/v1``.

    A route cannot opt out and an app cannot forget: the row is written around
    the request, after the answer is known, from what the request itself carries
    (its route, its path params, the principal §3.1 left on it) rather than from
    anything a handler chose to pass along.
    """

    def __init__(self, app: Any, store: Audit, *, skip: frozenset[str] = frozenset()) -> None:
        self.app = app
        self._store = store
        self._skip = skip

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not self._recordable(scope):
            await self.app(scope, receive, send)
            return
        status = 0

        async def watch(message: dict[str, Any]) -> None:
            nonlocal status
            if message.get("type") == "http.response.start":
                status = int(message.get("status") or 0)
            await send(message)

        try:
            await self.app(scope, receive, watch)
        finally:
            # In ``finally``, so a request that never reached a route (a 404, a
            # 500, a disconnect) still leaves the row a security question needs
            # -- and so does a *refused* write, which is the row most worth
            # having (§3.10 asks for the status the caller got, 403 and 429
            # included).
            self._write(scope, status)

    def _recordable(self, scope: Any) -> bool:
        """A write, under ``/v1``, that is not one of the skipped paths."""
        if str(scope.get("method") or "").upper() in SAFE_METHODS:
            return False
        path = str(scope.get("path") or "")
        if path in self._skip:
            return False
        return path == errors.PREFIX or path.startswith(errors.PREFIX + "/")

    def _write(self, scope: Any, status: int) -> None:
        try:
            request = Request(scope)
            principal = auth.principal_of(request)
            route = scope.get("route")
            template = getattr(route, "path", None) or str(scope.get("path") or "")
            self._store.record(
                ANONYMOUS if principal is None else principal,
                f"{str(scope.get('method') or '').upper()} {template}",
                _target_of(scope.get("path_params")),
                status or None,
                request_id=errors.request_id(request),
            )
        except Exception as exc:
            # An audit row that cannot be written is worth a loud log line, and
            # it is not worth a broken answer: this runs after the response, and
            # raising here would take out the connection that already succeeded.
            logger.error(
                "audit: %s %s was not recorded: %s",
                scope.get("method"),
                scope.get("path"),
                exc,
            )


def mount(app: Any, store: Audit, *, skip: Iterable[str] = (MCP_PATH,)) -> None:
    """Put §3.10's "a row per write" on ``app`` as middleware.

    The alternative is asking every route to call :meth:`Audit.record_request`
    itself, and the row that goes missing is always the one somebody asks about
    later. Middleware cannot be forgotten: every write under ``/v1`` is recorded
    once it answered -- refusals included -- with the actor and the request id
    off the request itself.

    ``skip`` is the one hole, and it is deliberate: ``POST /v1/mcp`` is a
    transport. The tool it names runs through the whole stack again -- same app,
    same credential rule, same routes -- and *that* request is the write worth
    recording. Recording both would put ``POST /v1/mcp`` in the table for every
    tool call ever made: a row with no route, no action, and no effect of its
    own.
    """
    if not isinstance(store, Audit):
        raise TypeError(f"mount() takes an Audit: got {store!r}")
    app.add_middleware(_Writes, store=store, skip=frozenset(skip))


def router(
    audit: Audit,
    app_cfg: auth.AuthConfig,
    *,
    limits: rate_limits.Limits | None = None,
) -> APIRouter:
    """§3.10's ``GET /v1/audit``: admin only, one §3.6 page.

    ``app.include_router(audit.router(store, cfg, limits=limits))``. The admin
    scope is demanded by a dependency, so a caller without it is 403
    ``not_allowed`` before a single row is read. Hand in the app's
    :class:`~agentkit.limits.Limits` and the page is charged like every other
    ``/v1`` route of §3.7 -- both dependencies resolve §3.1's rule once, because
    the config builds its credential dependency once.

    The cursor key is required *here*, at wiring time, and not at request time:
    an app that forgot it has to fail when it is built, not on the first page a
    person asks for.

    An empty ``cursor`` is not a cursor: it fails the parameter's own rule (422
    ``invalid_body``, the same answer ``limit=0`` gets) rather than quietly
    meaning "the first page". The stricter reading is the honest one -- a
    position that is not there is not a position.
    """
    _cursor_key(audit.key)
    guards = [Depends(auth.require("admin", app_cfg))]
    if limits is not None:
        guards.append(Depends(rate_limits.dependency(limits, app_cfg)))

    v1 = APIRouter(prefix=errors.PREFIX)

    @v1.get("/audit", dependencies=guards)
    def read_audit(
        limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
        cursor: str | None = Query(None, min_length=1, max_length=1024),
        principal: str | None = Query(None, max_length=PRINCIPAL_MAX),
        action: str | None = Query(None, max_length=ACTION_MAX),
        target: str | None = Query(None, max_length=TARGET_MAX),
    ) -> dict[str, Any]:
        return audit.page(
            limit=limit,
            cursor=cursor,
            principal=principal,
            action=action,
            target=target,
        ).as_dict()

    return v1


# -- the pieces the two halves share -----------------------------------------


def _where(
    filters: Mapping[str, Any], position: tuple[int, int] | None
) -> tuple[str, list[Any]]:
    """The page's ``WHERE``: the filters, then the keyset position.

    The column names come from this module's own tuple, never from a caller, and
    every value is a bound parameter.
    """
    clauses: list[str] = []
    args: list[Any] = []
    for column in ("principal", "action", "target"):
        value = filters.get(column)
        if value is not None:
            clauses.append(f"{column} = ?")
            args.append(value)
    if position is not None:
        at, row_id = position
        # Row-value comparison spelled out: ``(at, id) < (?, ?)`` needs SQLite
        # 3.15, and the two-clause form is the same plan on every version.
        clauses.append("(at < ? OR (at = ? AND id < ?))")
        args.extend([at, at, row_id])
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return where, args


def _position(
    cursor: Any, filters: Mapping[str, Any], key: bytes
) -> tuple[int, int] | None:
    """``(at, id)`` of the last row of the previous page, or ``None``.

    A cursor this module did not mint for these filters is :mod:`agentkit.cursor`'s
    400 ``bad_cursor``, unchanged; a position that is not two whole numbers is
    the same answer, because that is a cursor that cannot be used here either.
    """
    if cursor is None:
        return None
    at, row_id = cursors.decode(str(cursor), dict(filters), key)
    return (_whole(at, "at"), _whole(row_id, "id"))


def _whole(value: Any, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _bad_cursor(f"The cursor's {what} is not a whole number.")
    return int(value)


def _bad_cursor(why: str) -> errors.APIError:
    return errors.APIError(400, "bad_cursor", errors.MESSAGES["bad_cursor"], why)


def _cursor_key(key: Any) -> bytes:
    if not isinstance(key, (bytes, bytearray)) or len(key) < cursors.MIN_KEY_BYTES:
        raise ValueError(
            "the audit page needs §3.6's cursor key: at least "
            f"{cursors.MIN_KEY_BYTES} bytes of the app's own secret material"
        )
    return bytes(key)


def _limit(value: Any) -> int:
    size = _whole_number(value, "limit")
    if size < 1 or size > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    return size


def _whole_number(value: Any, what: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{what} must be a whole number")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{what} must be a whole number") from exc


def _at(value: Any) -> int:
    """A row's ``at``: whole seconds, from a clock or a caller."""
    return _whole_number(value, "at")


def _status(value: Any) -> int | None:
    """``result_status``: an HTTP status, or nothing. Never a guess."""
    if value is None:
        return None
    status = _whole_number(value, "result_status")
    if status < 100 or status > 599:
        raise ValueError(f"result_status must be an HTTP status, not {status}")
    return status


def _principal(value: Any) -> str:
    """§3.1's principal id: a :class:`Principal`, or the string itself."""
    text = getattr(value, "id", value)
    text = str(text).strip() if text is not None else ""
    if not text:
        raise ValueError(
            "a principal is required: §3.1 gives every caller an id, and the "
            "column is NOT NULL"
        )
    if len(text) > PRINCIPAL_MAX:
        raise ValueError(f"principal must be at most {PRINCIPAL_MAX} characters")
    return text


def _target_of(params: Any) -> str | None:
    """§3.10's ``target`` for a route: its path params, as ``name=value`` text.

    Text, not a JSON blob: the rule above is that only the app's own naming goes
    in this column, and a route's path params are exactly that -- the ids the
    action named. Sorted, so one route writes one shape, and capped downstream
    like every other value.
    """
    if not isinstance(params, Mapping) or not params:
        return None
    return ", ".join(f"{name}={params[name]}" for name in sorted(params))


def _required(value: Any, what: str, limit: int) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ValueError(f"{what} must not be empty")
    if len(text) > limit:
        raise ValueError(f"{what} must be at most {limit} characters")
    return text


def _optional(value: Any, what: str, limit: int) -> str | None:
    """``None`` stays ``None``; a value is text, capped, and never truncated.

    A number is not text here: a target is a name an app chose (a job id, a
    path, a row id written down), and ``str()`` on a mapping would put JSON in
    a security column by accident.
    """
    if value is None:
        return None
    if not isinstance(value, (str, int)):
        raise ValueError(f"{what} must be a string, not {type(value).__name__}")
    text = str(value).strip()
    if not text:
        return None
    if len(text) > limit:
        raise ValueError(f"{what} must be at most {limit} characters")
    return text
