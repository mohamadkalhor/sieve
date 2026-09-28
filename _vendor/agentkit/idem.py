"""§3.4 -- idempotency: safe retries for every state-changing ``/v1`` call.

Every state-changing ``/v1`` call accepts an ``Idempotency-Key`` header (1-128
characters of ``[A-Za-z0-9_.:-]``); a ``run`` call must send one. The key names
one piece of work for one caller, and the table below remembers what happened to
it, so a retry after a timeout replays the first answer instead of doing the
work twice.

The state machine, verbatim from §3.4:

| on arrival | what happens |
|---|---|
| no row for ``(principal, key)`` | ``INSERT … state='pending'``; the insert winning is the claim |
| a row, same ``request_hash``, ``state='done'`` | replay the stored status and body, ``Idempotent-Replay: true`` |
| a row, same hash, ``state='pending'`` | 409 ``in_progress``, ``Retry-After: 1`` |
| a row, different hash | 409 ``idempotency_mismatch`` |
| a row, ``state='interrupted'`` | 409 ``idempotency_interrupted``, ``detail.job_id`` when one was stored |

The handler's answer is stored for **every** status it produces -- 2xx, 4xx and
5xx alike -- so a refusal is replayed too. A handler that *raises* is another
matter, and the line is drawn between the two kinds of raise:

* a refusal the kit knows how to answer -- :class:`agentkit.errors.APIError`, or
  a ``HTTPException`` -- carries a status and a body, so it is stored as
  ``done`` with exactly the bytes the app's installed handler will send. This is
  what "4xx and 5xx alike" means: a 409 refusal is a replayable answer, not a
  hole in the row.
* anything else is a bug with no answer of its own: the row becomes
  ``interrupted`` and the exception is re-raised for the app's 500 handler.

Where the work is a job (§3.5), the row holds the job id *before* the job is
queued: the handler calls :meth:`Claim.set_job`, and the job store can hand in
its own connection so the two writes share one transaction. An interrupted
job-creating call therefore leaves a row that tells the retry which job to poll,
and the caller never needs a second key to finish the same work.

Storage is one table in the app's kit-owned SQLite file (``<data dir>/aio.db``,
next to ``aio.lock`` and the jobs table)::

    aio_idem(principal, idem_key, request_hash, state, status, response,
             job_id, created, expires, PRIMARY KEY(principal, idem_key))

``principal`` is §3.1's identity string -- ``key:<key id>`` for a bearer,
``user:<gate user id>`` for a session -- and is ``TEXT NOT NULL``: a row without
an owner would let one caller's key collide with another's, so the column and
:meth:`Idempotency.begin` both refuse it. Rows expire after 24 h; at startup
every ``pending`` row becomes ``interrupted`` (nothing survived the restart that
was holding it) and expired rows are purged.

Two deliberate departures from the letter of the plan, both written down because
a reader will ask:

* **A wrapper, not a dependency.** A FastAPI dependency can refuse a request but
  cannot *answer* one: replaying a stored status and body means producing the
  response, which only a wrapper around the endpoint can do. So the seam is
  :meth:`Idempotency.idempotent`, a decorator applied *under* the route
  decorator (``@v1.post(...)`` then ``@idem.idempotent(...)``), and the claim it
  makes is left on ``request.state.idem`` for the handler. The wrapper is async
  whatever the endpoint is; a plain ``def`` endpoint is run in the threadpool,
  so a blocking handler keeps the app's threading behaviour.
* **The replayed envelope keeps this request's id.** The stored body is replayed
  byte for byte, with one exception: when it is a §3.3 error envelope, its
  ``error.request_id`` is replaced with the id of the request being answered
  (and the ``X-Request-Id`` header carries the same value, which is the pairing
  §3.3 and the error tests insist on). A request id describes a request, and
  this is a different request; everything else in the stored answer is
  untouched, and the ``Idempotent-Replay: true`` header says so.

The kit's answers under ``/v1`` are JSON, and the replay is ``application/json``
for that reason. An endpoint that returns a body which is not UTF-8 text is out
of contract: the row is still stored (status, no body) and the replay answers
the stored status with an empty body, with a warning in the log rather than
corrupted bytes on the wire.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping
from urllib.parse import urlencode

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentkit import errors

__all__ = [
    "HEADER",
    "KEY_RE",
    "REPLAY_HEADER",
    "STATES",
    "TTL",
    "Claim",
    "Idempotency",
    "Replay",
    "Row",
    "digest",
    "request_hash",
    "valid_key",
]

logger = logging.getLogger("agentkit.idem")

HEADER = "Idempotency-Key"
REPLAY_HEADER = "Idempotent-Replay"

# §3.4: 1-128 characters of this set, and nothing else. ``\A…\Z`` on purpose --
# ``$`` would accept a trailing newline, and a key is a name, not a body.
KEY_RE = re.compile(r"\A[A-Za-z0-9_.:-]{1,128}\Z")

PENDING = "pending"
DONE = "done"
INTERRUPTED = "interrupted"
STATES = frozenset({PENDING, DONE, INTERRUPTED})

# Rows expire after 24 h (§3.4).
TTL = 24 * 60 * 60

# The wrapper's own parameter, added to the endpoint's signature so FastAPI
# hands it the request; a route that already declares ``request`` keeps it.
_REQUEST = "request"

#: §3.4's table, by name. It is created here and *looked for* by
#: :func:`agentkit.jobs.router`, which writes a job's id into it inside the
#: job's own transaction -- a promise that only holds if the table is in the
#: jobs store's connection, so the router checks for it when it is wired.
TABLE = "aio_idem"

_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    principal    TEXT    NOT NULL,
    idem_key     TEXT    NOT NULL,
    request_hash TEXT    NOT NULL,
    state        TEXT    NOT NULL CHECK (state IN ('pending', 'done', 'interrupted')),
    status       INTEGER,
    response     TEXT,
    job_id       TEXT,
    created      INTEGER NOT NULL,
    expires      INTEGER NOT NULL,
    PRIMARY KEY (principal, idem_key)
)
"""

# The purge is by ``expires``; the index keeps it from walking the table.
_INDEX = f"CREATE INDEX IF NOT EXISTS {TABLE}_expires ON {TABLE} (expires)"


def valid_key(key: Any) -> bool:
    """Is this a §3.4 ``Idempotency-Key``: 1-128 of ``[A-Za-z0-9_.:-]``?"""
    return isinstance(key, str) and KEY_RE.match(key) is not None


def digest(method: Any, path: Any, query: Any, body: Any = b"") -> str:
    """The ``request_hash`` of §3.4: sha256 of method, path, sorted query, body.

    The four parts are joined with ``\\n`` so that no part can be confused with
    the next one, and the body is whatever bytes the caller hashed (canonical
    JSON, or the raw bytes of a body that is not JSON). ``?dry_run=true`` and
    its absence are different requests, which is why the query string is here:
    the query is passed already canonical (sorted, percent-encoded) by
    :func:`request_hash`.
    """
    material = f"{str(method).upper()}\n{path}\n{query}\n".encode("utf-8")
    if isinstance(body, str):
        body = body.encode("utf-8")
    return hashlib.sha256(material + bytes(body or b"")).hexdigest()


async def request_hash(request: Any) -> str:
    """The hash of *this* request, body included.

    The body is canonical JSON when the request declares JSON -- ``json.dumps``
    of the parsed value with sorted keys and no whitespace, so a re-serialised
    body hashes the same -- and its raw bytes otherwise. The body is read
    through Starlette's cache, so a body FastAPI has already parsed is not read
    twice.
    """
    raw = await request.body()
    return digest(
        request.method, request.url.path, canonical_query(request), _body(raw, request)
    )


def canonical_query(request: Any) -> str:
    """The query string of a request, canonical: sorted, percent-encoded.

    Repeats are kept (``a=1&a=2`` is not ``a=2&a=1``), the pairs are sorted, and
    ``urlencode`` gives one spelling for a value whatever the client sent
    (``%20`` and ``+`` are the same space).
    """
    params = getattr(request, "query_params", None)
    items: Iterable[tuple[Any, Any]] = params.multi_items() if params else ()
    return urlencode(sorted((str(k), str(v)) for k, v in items))


@dataclass(frozen=True)
class Row:
    """One row of ``aio_idem``, as stored."""

    principal: str
    idem_key: str
    request_hash: str
    state: str
    status: int | None
    response: str | None
    job_id: str | None
    created: int
    expires: int

    @classmethod
    def of(cls, row: Any) -> "Row":
        return cls(
            principal=row["principal"],
            idem_key=row["idem_key"],
            request_hash=row["request_hash"],
            state=row["state"],
            status=row["status"],
            response=row["response"],
            job_id=row["job_id"],
            created=row["created"],
            expires=row["expires"],
        )


@dataclass(frozen=True)
class Replay:
    """A stored answer to be replayed instead of running the handler again."""

    status: int
    response: str | None


@dataclass
class Claim:
    """The claim this request won: it owns ``(principal, key)`` for the call.

    It is left on ``request.state.idem`` for the handler, which needs it for one
    thing only: :meth:`set_job`, on a call that creates a job (§3.5).
    """

    store: "Idempotency"
    principal: str
    key: str
    request_hash: str
    job_id: str | None = None

    def set_job(self, job_id: Any, *, db: sqlite3.Connection | None = None) -> str:
        """Remember the job id this call created, before the job is queued."""
        job_id = self.store.set_job(self.principal, self.key, job_id, db=db)
        self.job_id = job_id
        return job_id


class Idempotency:
    """§3.4's table and state machine, in the app's own ``aio.db``.

    ``path`` is the app's kit-owned file (``":memory:"`` for tests). ``clock``
    is injectable so a test can age a row out without waiting a day.
    """

    def __init__(
        self,
        path: str,
        *,
        clock: Callable[[], float] = time.time,
        ttl: int = TTL,
    ) -> None:
        self.path = str(path)
        self.ttl = int(ttl)
        if self.ttl <= 0:
            raise ValueError("ttl must be a positive number of seconds")
        self._clock = clock
        # One connection, one lock: every write in this module is a short
        # statement or a short transaction, and a claim must be atomic against
        # another thread of the same process (the 20-requests-one-handler rule).
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
            self._db.execute(_INDEX)

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Close the connection. A closed store refuses further work."""
        with self._lock:
            self._db.close()

    def startup(self) -> tuple[int, int]:
        """§3.4's restart rule: ``pending`` -> ``interrupted``, purge expired.

        Returns ``(interrupted, purged)``. Nothing held a pending row across the
        restart, so the honest state is ``interrupted`` -- a retry with that key
        is told so, with the job id when one was stored, and the caller starts a
        new key for a synchronous call (or polls the job it named).
        """
        now = int(self._clock())
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                interrupted = self._db.execute(
                    "UPDATE aio_idem SET state = ? WHERE state = ?",
                    (INTERRUPTED, PENDING),
                ).rowcount
                purged = self._db.execute(
                    "DELETE FROM aio_idem WHERE expires <= ?", (now,)
                ).rowcount
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        if interrupted or purged:
            logger.info(
                "idempotency startup: %d pending -> interrupted, %d expired rows purged",
                interrupted,
                purged,
            )
        return int(interrupted), int(purged)

    # -- the state machine --------------------------------------------------

    def begin(self, principal: Any, key: Any, request_hash: str) -> Claim | Replay:
        """Claim ``(principal, key)``, or answer with what is already stored.

        The insert of the ``pending`` row *is* the claim, in its own
        transaction: the winner gets a :class:`Claim` and runs the handler,
        everyone else gets a :class:`Replay` or one of the four 409s of §3.4.
        """
        principal = _principal(principal)
        key = _key(key)
        request_hash = str(request_hash)
        now = int(self._clock())
        for _ in range(2):  # a second pass only if a row vanished under us
            with self._lock:
                try:
                    self._db.execute("BEGIN IMMEDIATE")
                    self._db.execute(
                        "INSERT INTO aio_idem (principal, idem_key, request_hash,"
                        " state, status, response, job_id, created, expires)"
                        " VALUES (?, ?, ?, ?, NULL, NULL, NULL, ?, ?)",
                        (principal, key, request_hash, PENDING, now, now + self.ttl),
                    )
                    self._db.execute("COMMIT")
                except sqlite3.IntegrityError:
                    self._db.execute("ROLLBACK")
                else:
                    return Claim(
                        store=self, principal=principal, key=key, request_hash=request_hash
                    )
                row = self._row(principal, key)
                if row is None:
                    continue  # purged between the insert and the read: try again
                if row.expires <= now:
                    # Expired is gone (§3.4: rows live 24 h), so the key is free.
                    self._purge(principal, key)
                    continue
            return _conflict(row, request_hash)
        raise RuntimeError(
            f"the row for {principal!r} / {key!r} kept disappearing; giving up"
        )

    def finish(self, principal: Any, key: Any, status: Any, response: Any = None) -> bool:
        """Store the handler's answer: ``state='done'`` with status and body.

        Called for every status the handler produced, refusals included. Only a
        ``pending`` row is moved, so a row that another path already interrupted
        is not quietly turned back into a success.
        """
        principal = _principal(principal)
        key = _key(key)
        with self._lock:
            written = self._db.execute(
                "UPDATE aio_idem SET state = ?, status = ?, response = ?"
                " WHERE principal = ? AND idem_key = ? AND state = ?",
                (
                    DONE,
                    int(status),
                    None if response is None else str(response),
                    principal,
                    key,
                    PENDING,
                ),
            ).rowcount
        if not written:
            logger.warning(
                "idempotency: no pending row to finish for %r / %r", principal, key
            )
        return bool(written)

    def interrupt(self, principal: Any, key: Any) -> bool:
        """Mark the row ``interrupted``, keeping any job id it already holds."""
        principal = _principal(principal)
        key = _key(key)
        with self._lock:
            written = self._db.execute(
                "UPDATE aio_idem SET state = ? WHERE principal = ? AND idem_key = ?"
                " AND state = ?",
                (INTERRUPTED, principal, key, PENDING),
            ).rowcount
        if not written:
            logger.warning(
                "idempotency: no pending row to interrupt for %r / %r", principal, key
            )
        return bool(written)

    def set_job(
        self,
        principal: Any,
        key: Any,
        job_id: Any,
        *,
        db: sqlite3.Connection | None = None,
    ) -> str:
        """Store the job id of a job-creating call, before the job is queued.

        ``db`` is the seam for §3.4's "same connection, same transaction": a job
        store that lives in the same ``aio.db`` hands its own connection in, and
        the row and the job commit or roll back together. Without it the write
        is its own short transaction.
        """
        principal = _principal(principal)
        key = _key(key)
        job_id = _job_id(job_id)
        if db is None:
            with self._lock:
                self._db.execute("BEGIN IMMEDIATE")
                try:
                    written = self._db.execute(
                        "UPDATE aio_idem SET job_id = ? WHERE principal = ? AND idem_key = ?",
                        (job_id, principal, key),
                    ).rowcount
                    self._db.execute("COMMIT")
                except BaseException:
                    self._db.execute("ROLLBACK")
                    raise
        else:
            written = db.execute(
                "UPDATE aio_idem SET job_id = ? WHERE principal = ? AND idem_key = ?",
                (job_id, principal, key),
            ).rowcount
        if not written:
            logger.warning(
                "idempotency: no row to name job %r for %r / %r", job_id, principal, key
            )
        return job_id

    def get(self, principal: Any, key: Any) -> Row | None:
        """The row for one ``(principal, key)``, or ``None``. For tests and apps."""
        with self._lock:
            return self._row(_principal(principal), _key(key))

    # -- the seam an app mounts --------------------------------------------

    def idempotent(
        self, *, required: bool = False
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Wrap a ``/v1`` endpoint: ``@idem.idempotent(required=True)``.

        Put it *under* the route decorator, so the route is registered against
        the wrapper::

            @v1.post("/runs", status_code=202)
            @idem.idempotent(required=True)
            def make_run(request: Request, body: Run) -> dict[str, Any]:
                request.state.idem.set_job(job.id)   # §3.5, same transaction
                return {"job": job}

        ``required=True`` is for the run-scope routes of §3.4: a call without the
        header is 400 ``idempotency_key_required`` instead of being run
        unprotected. The wrapper runs *after* the credential rule and the
        limits, because those run before the endpoint is reached at all: a 401,
        429 or 503 is never stored, and never consumes a key.
        """
        if not isinstance(required, bool):
            raise TypeError("required is a bool")

        def decorate(endpoint: Callable[..., Any]) -> Callable[..., Any]:
            if not callable(endpoint):
                raise TypeError("idempotent() wraps a callable")
            # Both read the endpoint's signature; the first may refuse it.
            hands_over = _takes_request(endpoint)
            signature = _signature(endpoint)

            async def wrapper(**kwargs: Any) -> Any:
                request = kwargs[_REQUEST]
                return await self._serve(
                    request,
                    endpoint,
                    kwargs,
                    required=required,
                    hands_over=hands_over,
                )

            functools.update_wrapper(wrapper, endpoint)
            wrapper.__signature__ = signature  # type: ignore[attr-defined]
            return wrapper

        return decorate

    # -- one request --------------------------------------------------------

    async def _serve(
        self,
        request: Any,
        endpoint: Callable[..., Any],
        kwargs: Mapping[str, Any],
        *,
        required: bool,
        hands_over: bool = False,
    ) -> Any:
        key = request.headers.get(HEADER)
        if key is None:
            if required:
                raise errors.APIError(
                    400,
                    "idempotency_key_required",
                    errors.MESSAGES["idempotency_key_required"],
                )
            return await self._call(request, endpoint, kwargs, hands_over)
        if not valid_key(key):
            # An empty or malformed value is not "no key": it is a key that
            # cannot be honoured, and saying so is the safer answer.
            raise errors.APIError(
                400, "bad_idempotency_key", errors.MESSAGES["bad_idempotency_key"]
            )

        principal = _caller(request)
        # Before anything is stored: the request id is this request's, and the
        # stored refusal must be the very bytes the error handler will send.
        rid = errors.request_id(request)
        outcome = self.begin(principal, key, await request_hash(request))
        if isinstance(outcome, Replay):
            return _replayed(outcome, rid)

        request.state.idem = outcome
        try:
            value = await self._call(request, endpoint, kwargs, hands_over)
        except errors.APIError as exc:
            self.finish(principal, key, *(await _refusal(request, exc)))
            raise
        except StarletteHTTPException as exc:
            self.finish(principal, key, *(await _refusal(request, exc)))
            raise
        except BaseException:
            self.interrupt(principal, key)
            raise

        status, body = _answer(value, request)
        self.finish(principal, key, status, body)
        return value

    async def _call(
        self,
        request: Any,
        endpoint: Callable[..., Any],
        kwargs: Mapping[str, Any],
        hands_over: bool = False,
    ) -> Any:
        """Run the endpoint: awaited when it is async, in the threadpool when not."""
        given = {name: value for name, value in kwargs.items() if name != _REQUEST}
        if hands_over:
            given[_REQUEST] = request
        if inspect.iscoroutinefunction(endpoint):
            return await endpoint(**given)
        return await run_in_threadpool(functools.partial(endpoint, **given))

    # -- storage ------------------------------------------------------------

    def _row(self, principal: str, key: str) -> Row | None:
        row = self._db.execute(
            "SELECT * FROM aio_idem WHERE principal = ? AND idem_key = ?",
            (principal, key),
        ).fetchone()
        return Row.of(row) if row is not None else None

    def _purge(self, principal: str, key: str) -> None:
        self._db.execute("BEGIN IMMEDIATE")
        try:
            self._db.execute(
                "DELETE FROM aio_idem WHERE principal = ? AND idem_key = ?",
                (principal, key),
            )
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise


# -- the conflict table -------------------------------------------------------


def _conflict(row: Row, request_hash: str) -> Claim | Replay:
    """§3.4's four conflict rows, in the order they are decided.

    A different hash is a different request whatever the row's state: the key
    was used for something else, and answering that with anything but a mismatch
    would hand the caller another request's answer.
    """
    if row.request_hash != request_hash:
        raise errors.APIError(
            409, "idempotency_mismatch", errors.MESSAGES["idempotency_mismatch"]
        )
    if row.state == DONE:
        return Replay(status=row.status or 200, response=row.response)
    if row.state == PENDING:
        # §3.4 names the header on this 409 even though §3.3's 429/503 rule does
        # not cover 409: the caller is told to come back in a second.
        raise errors.APIError(
            409,
            "in_progress",
            errors.MESSAGES["in_progress"],
            retry_after=1,
        )
    detail = {"job_id": row.job_id} if row.job_id else None
    raise errors.APIError(
        409,
        "idempotency_interrupted",
        errors.MESSAGES["idempotency_interrupted"],
        detail=detail,
    )


# -- the answer ---------------------------------------------------------------


def _answer(value: Any, request: Any) -> tuple[int, str | None]:
    """``(status, body text)`` for what the handler produced.

    A ``Response`` speaks for itself (its status, its body). Anything else is
    the JSON answer the route declared: the status is the route's own
    ``status_code=``, read off the route Starlette put in the scope, and the
    body is serialised exactly as ``JSONResponse`` would serialise it, so the
    stored bytes and the bytes on the wire are the same.
    """
    if isinstance(value, Response):
        status = int(value.status_code)
        raw = value.body
        if not isinstance(raw, bytes):
            raw = str(raw).encode("utf-8")
        body = _text(raw)
        if body is None:
            logger.warning(
                "idempotency: the answer of %s %s is not UTF-8 text; storing the "
                "status only",
                request.method,
                request.url.path,
            )
        return status, body
    try:
        body = json.dumps(
            jsonable_encoder(value),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        logger.warning(
            "idempotency: the answer of %s %s is not JSON (%s); storing the status only",
            request.method,
            request.url.path,
            type(exc).__name__,
        )
        return _declared_status(request), None
    return _declared_status(request), body


def _declared_status(request: Any) -> int:
    """The status the route declared (``status_code=``, 200 by default)."""
    route = getattr(request, "scope", {}).get("route")
    status = getattr(route, "status_code", None)
    return int(status) if isinstance(status, int) and status else 200


async def _refusal(
    request: Any, exc: errors.APIError | StarletteHTTPException
) -> tuple[int, str]:
    """``(status, body)`` of a refusal: the exact envelope the app will send.

    The kit's own handler functions are called, not a copy of their logic, so a
    stored refusal is the answer of this request byte for byte -- the same
    request id included, which is why the id is settled before this runs.
    """
    if isinstance(exc, errors.APIError):
        response = await errors.api_error(request, exc)
    else:
        response = await errors.http_error(request, exc)
    status = int(response.status_code)
    body = _text(bytes(response.body))
    return status, body if body is not None else ""


def _replayed(replay: Replay, rid: str) -> Response:
    """The stored answer, with this request's id in it and the replay header."""
    body = _rerid(replay.response, rid)
    headers = {REPLAY_HEADER: "true", errors.REQUEST_ID_HEADER: rid}
    return Response(
        content=body,
        status_code=int(replay.status),
        media_type="application/json",
        headers=headers,
    )


def _rerid(body: str | None, rid: str) -> str:
    """The stored body, with the error envelope's ``request_id`` set to ours."""
    if not body:
        return ""
    try:
        parsed = json.loads(body)
    except ValueError:
        return body
    error = parsed.get("error") if isinstance(parsed, dict) else None
    if not isinstance(error, dict) or "request_id" not in error:
        return body
    error["request_id"] = rid
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


# -- small helpers ------------------------------------------------------------


def _body(raw: bytes, request: Any) -> bytes:
    """The body as §3.4 hashes it: canonical JSON, or the raw bytes."""
    if not raw or not _is_json(request.headers.get("content-type")):
        return raw
    try:
        parsed = json.loads(raw)
    except ValueError:
        return raw
    return json.dumps(
        parsed, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def _is_json(declared: Any) -> bool:
    if not declared:
        return False
    media = str(declared).split(";", 1)[0].strip().lower()
    return media == "application/json" or media.endswith("+json")


def _text(raw: bytes) -> str | None:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _caller(request: Any) -> str:
    """§3.1's principal id, or a wiring error: the rule runs *before* this.

    A route that reaches the wrapper without a principal is a route that did not
    depend on ``credential(app_cfg)``; storing a row under no owner is not an
    option (``principal`` is ``NOT NULL``), so the request is refused as the bug
    it is -- and nothing is stored, because nothing was claimed.
    """
    from agentkit import auth

    principal = auth.principal_of(request)
    owner = getattr(principal, "id", None)
    if not owner or not str(owner).strip():
        logger.error(
            "idempotency: %s %s has no principal; is it missing "
            "Depends(credential(app_cfg))?",
            request.method,
            request.url.path,
        )
        raise errors.APIError(500, "server_error", errors.MESSAGES["server_error"])
    return str(owner)


def _principal(principal: Any) -> str:
    text = str(principal).strip() if principal is not None else ""
    if not text:
        raise ValueError(
            "a principal is required: §3.1 gives every caller an id, and the "
            "column is NOT NULL"
        )
    return text


def _key(key: Any) -> str:
    if not valid_key(key):
        raise ValueError(f"{key!r} is not a valid idempotency key")
    return str(key)


def _job_id(job_id: Any) -> str:
    text = str(job_id).strip() if job_id is not None else ""
    if not text:
        raise ValueError("a job id is required")
    return text


def _hints(endpoint: Callable[..., Any]) -> dict[str, Any]:
    """The endpoint's annotations, with string annotations resolved.

    ``from __future__ import annotations`` (the kit's own modules use it) turns
    every annotation into a string, and FastAPI resolves the strings of the
    callable it calls -- the wrapper -- against the wrapper's globals, which are
    this module's. Resolving them against the *endpoint's* globals first, and
    handing FastAPI objects instead of names, keeps a route's own parameters its
    own.
    """
    try:
        return inspect.get_annotations(endpoint, eval_str=True)
    except Exception:  # a name that does not resolve: leave the string to FastAPI
        try:
            return inspect.get_annotations(endpoint, eval_str=False)
        except Exception:
            return {}


def _is_request(annotation: Any) -> bool:
    """Whether an annotation is ``Request`` (possibly still a name)."""
    if annotation is Request:
        return True
    return isinstance(annotation, str) and annotation.strip().split(".")[-1] == "Request"


def _takes_request(endpoint: Callable[..., Any]) -> bool:
    """Whether the endpoint declares the request itself.

    ``request: Request`` is the name the kit uses for it, and the wrapper adds
    the parameter when it is missing. A parameter of that name carrying another
    annotation would be silently shadowed by the one the wrapper adds, so it is
    refused loudly at decoration time instead.
    """
    parameter = inspect.signature(endpoint).parameters.get(_REQUEST)
    if parameter is None:
        return False
    annotation = _hints(endpoint).get(_REQUEST, parameter.annotation)
    if not _is_request(annotation):
        raise TypeError(
            f"{getattr(endpoint, '__name__', endpoint)!r} has a parameter named "
            f"{_REQUEST!r}; an idempotent endpoint's request is annotated Request"
        )
    return True


def _signature(endpoint: Callable[..., Any]) -> inspect.Signature:
    """The endpoint's signature plus ``request``, so FastAPI injects it.

    FastAPI reads the wrapper's signature to build the route's dependants, and
    ``__signature__`` is what it reads; adding the request parameter there (and
    only there) leaves the endpoint's own parameters -- and their order -- alone.
    """
    signature = inspect.signature(endpoint)
    hints = _hints(endpoint)
    parameters = [
        parameter.replace(annotation=hints.get(name, parameter.annotation))
        for name, parameter in signature.parameters.items()
    ]
    if _REQUEST not in signature.parameters:
        request = inspect.Parameter(
            _REQUEST, inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Request
        )
        parameters.insert(0, request)
    return signature.replace(parameters=parameters)
