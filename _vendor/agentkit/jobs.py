"""§3.5 -- jobs: long work outlives the request, and never happens twice.

Anything that can exceed ~2 s does not run inside a request. The call answers

    202 {"job": {"id": …, "status": "queued", "poll": "/v1/jobs/<id>"}}

and the work happens on a bounded pool of threads, in a row of ``aio_jobs``
that outlives the process that started it.

The state machine, verbatim from §3.5::

    queued ──▶ running ──▶ succeeded | failed | cancelled
                    └──▶ interrupted        (not terminal: a re-run is pending)

Every transition is one ``UPDATE … WHERE id=? AND state=?``; a zero row count
means somebody else moved the row first, and the mover does not write again.

Where it lives: ``<data dir>/aio.db``, the file §3.4's table is in (the same
app, the same file), next to ``<data dir>/aio.lock``::

    aio_jobs(id, owner, key_id, kind, input, state, result, error, attempt,
             lease_until, cancel_requested, created, started, finished, claim)

``owner`` is §3.1's user id (the job's owner, and the only person who may read
it); ``key_id`` is the key that created it, when a bearer created it. ``input``,
``result`` and ``error`` are JSON text. ``state`` is the column, ``status`` is
what the wire calls it -- the 202 body above says ``status``, and so does every
answer of ``GET /v1/jobs/{id}``.

``claim`` is the store's own bookkeeping, the one column beyond §3.5's fourteen:
the token the pool wrote when it claimed the row. Every write a worker makes to
*its own* row carries ``AND claim = ?``, so a handler thread left over from a
pool that was already shut down -- ``shutdown(grace=0.0)`` does not kill a
thread, and cannot -- cannot write over the row a later pool has claimed since.
A row that is not ``running`` has no claim: the interruption rules below clear
it, because the row is nobody's any more.

**The pool.** ``AIO_JOB_WORKERS`` threads (default 2) claim with
``UPDATE … SET state='running', claim=<fresh token>, lease_until=now+60
WHERE id=? AND state='queued'`` and a keeper thread renews the lease, every
20 s, of the rows whose claim belongs to the contexts this pool is holding. In
single-process mode **nothing reclaims an expired lease while the process
lives** (no reaper): the lease is evidence for the restart rule below, and
nothing else, so a running thread is never doubled. A handler runs *outside*
the store's lock, so one slow job does not stop the others from being claimed.
A pool whose ``shutdown()`` has already begun claims nothing more: a worker
that was between two claims when the stop arrived leaves the row where it is,
and the next pool takes it.

**The restart rule.** At startup every ``running`` row becomes ``interrupted``
with ``attempt+1``; a row whose attempt has reached 3 becomes ``failed`` with
``error.code = "interrupted"``. An ``interrupted`` row is *not terminal* and is
claimable: the pool re-queues it and runs it again **under the same id**, which
is why ``GET /v1/jobs/{id}`` shows ``interrupted`` while a re-run is pending and
never ``failed``. A clean shutdown (``shutdown()``, i.e. SIGTERM) does not count
as an attempt for a handler that called :meth:`HandlerContext.checkpoint` -- the
handler said it was at a safe point, and deploys restart services often.

**Handlers are idempotent by job id.** Any side effect outside the app's own
database carries the job id as its idempotency key, so a re-run finds the
effect already done and completes instead of doing it twice; that is the whole
point of the restart rule, and it is why :meth:`Jobs.register` refuses a job
kind that does not declare *how* it is idempotent::

    jobs.register("export", export, idempotency="the file is named by job id")

A handler is ``handler(ctx)`` and gets :class:`HandlerContext`: ``ctx.job_id``,
``ctx.input``, ``ctx.cancelled()``, ``ctx.effect_done`` (set it to ``True``
right after the outside effect) and ``ctx.checkpoint()``. It returns a JSON
object or raises.

**Cancel.** A ``queued`` row is cancelled at once; a ``running`` row gets
``cancel_requested`` and the handler checks ``ctx.cancelled()`` between steps.
A handler that returns after a cancel was requested ends ``succeeded`` when it
reports ``ctx.effect_done = True`` (the effect happened; saying ``cancelled``
would be a lie), and ``cancelled`` otherwise. The same reading is applied to a
handler that raises after a cancel was requested and reports no effect: the
work was stopped, so the job says so. ``error.code`` is always
``"interrupted"`` for the attempt cap and ``"handler_error"`` for a handler that
raised -- with the exception's *type* only, never its message, because §3.3
refuses to hand an exception's words to a caller and a job's error is answered
to one.

**The single-process invariant, enforced.** :meth:`Jobs.startup` takes an
exclusive ``fcntl.flock`` on ``<data dir>/aio.lock`` and refuses to start
without it (:class:`Locked`); ``submit``, ``start`` and the pool refuse to work
while it is not held. One process per data dir is what makes "no reaper" safe,
so it is not a convention here -- it is checked.

Two readings worth naming, because a reader will ask:

* ``interrupted`` is a state the pool claims from, not a tombstone. §3.5's
  claim is ``WHERE id=? AND state='queued'``, so an interrupted row is
  re-queued by its own ``UPDATE … WHERE state='interrupted'`` in the same
  transaction, and only then claimed. Both statements are the single
  ``UPDATE … WHERE state=?`` the plan asks for, and a row that is not what it
  was is left exactly where it is.
* ``cancel_requested`` survives a restart: the person asked for the job to
  stop, and a re-run that finds no effect done ends ``cancelled`` like the
  first attempt would have.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import secrets
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from agentkit import (
    auth,
    cursor as cursors,
    errors,
    idem as idempotency,
    limits as rate_limits,
)

__all__ = [
    "COLUMNS",
    "DEFAULT_LIMIT",
    "DEFAULT_WORKERS",
    "LEASE",
    "LOCK_NAME",
    "MAX_ATTEMPTS",
    "MAX_LIMIT",
    "MAX_WORKERS",
    "POLL_PATH",
    "RENEW",
    "STATES",
    "TABLE",
    "TERMINAL",
    "WORKERS_ENV",
    "HandlerContext",
    "Job",
    "Jobs",
    "Kind",
    "Locked",
    "Page",
    "ProcessLock",
    "Submit",
    "owner_of",
    "router",
    "workers_from_env",
]

logger = logging.getLogger("agentkit.jobs")

# The table, the lock and the poll path §3.5 names.
TABLE = "aio_jobs"
LOCK_NAME = "aio.lock"
POLL_PATH = "/v1/jobs/"

# §3.5's states. ``interrupted`` is not terminal: a re-run is pending.
QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
CANCELLED = "cancelled"
INTERRUPTED = "interrupted"
STATES = frozenset({QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED, INTERRUPTED})
TERMINAL = frozenset({SUCCEEDED, FAILED, CANCELLED})

# The column list of §3.5, in its order: the card and the table agree. ``claim``
# is the store's own fifteenth column (see the module's docstring): §3.5's
# fourteen are the job, and this one is who is running it.
COLUMNS = (
    "id",
    "owner",
    "key_id",
    "kind",
    "input",
    "state",
    "result",
    "error",
    "attempt",
    "lease_until",
    "cancel_requested",
    "created",
    "started",
    "finished",
    "claim",
)

# The pool (§3.5): ``AIO_JOB_WORKERS`` threads, default 2, and a bound so a
# typo in the environment cannot spawn ten thousand of them.
WORKERS_ENV = "AIO_JOB_WORKERS"
DEFAULT_WORKERS = 2
MAX_WORKERS = 16

# The claim's lease and the keeper's renewal (§3.5).
LEASE = 60
RENEW = 20.0

# How long an idle worker sleeps before looking again. Not a policy: the wake
# event makes a submit immediate, this only bounds the wait for a job another
# process (or a test) wrote straight into the file.
POLL_SECONDS = 0.05

# §3.5's attempt cap: the third interruption is the last one.
MAX_ATTEMPTS = 3

# §3.10's retention, and the keeper that applies it. A row nobody will ask about
# again is still a row: the file grows forever, and the audit table of a busy app
# grows fastest of all. The pool's keeper thread deletes them at most once an
# hour; ``0`` means keep everything, which is a decision an app may make.
RETENTION_ENV = "AIO_JOB_RETENTION_DAYS"
DEFAULT_RETENTION_DAYS = 30
AUDIT_RETENTION_ENV = "AIO_AUDIT_RETENTION_DAYS"
DEFAULT_AUDIT_RETENTION_DAYS = 90
PURGE_EVERY = 3600.0
SECONDS_PER_DAY = 86400.0

# §3.6's page, the same bounds as every other list of the kit.
DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# A kind is a name, and nothing else.
KIND_MAX = 64
KIND_RE = r"^[a-z][a-z0-9_.-]*$"

# The ``?state=`` of §3.5's list: the six states, and nothing else.
STATE_RE = "^(queued|running|succeeded|failed|cancelled|interrupted)$"

# The two errors a job can carry, both the kit's own words.
_INTERRUPTED = {
    "code": "interrupted",
    "message": (
        "The job was interrupted too many times by restarts; it is not "
        "retried again."
    ),
}
_UNKNOWN_KIND = {
    "code": "unknown_kind",
    "message": "No handler is registered for this kind any more.",
}
_BAD_RESULT = {
    "code": "bad_result",
    "message": "The handler returned something that is not a JSON object.",
}
# The job's row when the *worker* was the thing that broke -- a store error, a
# bug in the claim, anything the worker's own loop had to survive. Not the
# handler's fault, and not hidden either: the row says a person should look.
_INTERNAL = {
    "code": "internal",
    "message": (
        "The worker could not finish this job -- see the app's log for the "
        "exception it survived."
    ),
}

_DDL = """
CREATE TABLE IF NOT EXISTS aio_jobs (
    id               TEXT    NOT NULL PRIMARY KEY,
    owner            TEXT    NOT NULL CHECK (owner <> ''),
    key_id           TEXT,
    kind             TEXT    NOT NULL,
    input            TEXT    NOT NULL,
    state            TEXT    NOT NULL CHECK (state IN
        ('queued', 'running', 'succeeded', 'failed', 'cancelled', 'interrupted')),
    result           TEXT,
    error            TEXT,
    attempt          INTEGER NOT NULL DEFAULT 0,
    lease_until      INTEGER,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created          INTEGER NOT NULL,
    started          INTEGER,
    finished         INTEGER,
    claim            TEXT
)
"""

# The claim looks for the oldest claimable row; the list pages a person's own
# jobs newest first. Two indexes, one per reader.
_INDEXES = (
    "CREATE INDEX IF NOT EXISTS aio_jobs_claim ON aio_jobs (state, created, id)",
    "CREATE INDEX IF NOT EXISTS aio_jobs_owner ON aio_jobs (owner, created, id)",
)


def workers_from_env(default: int = DEFAULT_WORKERS) -> int:
    """``AIO_JOB_WORKERS`` as a pool size: 1 to 16, or ``default``.

    A value that is not a number is the default; a value outside 1..16 is
    clamped, because a pool of zero threads serves nothing and a pool of ten
    thousand is a typo, not a decision.
    """
    raw = os.environ.get(WORKERS_ENV, "")
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return int(default)
    return max(1, min(value, MAX_WORKERS))


def retention_seconds(
    name: str = RETENTION_ENV,
    default_days: int = DEFAULT_RETENTION_DAYS,
    environ: Mapping[str, str] | None = None,
) -> float | None:
    """One retention window from the environment, in seconds.

    ``None`` means *keep forever*, which is what ``0`` says: an operator who
    writes ``0`` is deciding to keep every row, and that is not the same
    decision as a typo. Anything that is not a whole number is the default, for
    the same reason a pool size that is not a number is the default.
    """
    raw = (os.environ if environ is None else environ).get(name, "")
    try:
        days = int(str(raw).strip())
    except (TypeError, ValueError):
        days = int(default_days)
    return None if days <= 0 else float(days) * SECONDS_PER_DAY


def owner_of(principal: Any) -> str | None:
    """The ``owner`` column for a caller: §3.1's user id, else its principal id.

    ``None`` for a caller that owns nothing -- §3.1's anonymous principal, or
    nothing at all -- and :meth:`Jobs.submit` refuses that: a job with no owner
    is a job nobody may read. A plain string is taken as the owner, so the
    store can be driven without a request.
    """
    if isinstance(principal, str):
        text = principal.strip()
        return text or None
    owner = getattr(principal, "owner", None)
    if isinstance(owner, str) and owner.strip():
        return owner.strip()
    if str(getattr(principal, "kind", "")) == "anonymous":
        return None
    text = getattr(principal, "id", None)
    if isinstance(text, str) and text.strip():
        return text.strip()
    return None


class Locked(RuntimeError):
    """Another process holds ``<data dir>/aio.lock``: refuse to start.

    §3.5's single-process invariant, as an exception rather than a promise:
    the second process is told it may not run, and never starts.
    """


class ProcessLock:
    """An exclusive ``flock`` on one file, held for as long as the store lives.

    ``flock`` is attached to the open file description, so a second ``acquire``
    in this process -- a second :class:`Jobs` on the same data dir, a test --
    fails exactly like another process would. :meth:`acquire` twice on *this*
    object is a no-op: the same holder asking again already has it.
    """

    def __init__(self, path: str) -> None:
        self.path = str(path)
        self._fd: int | None = None

    def __repr__(self) -> str:
        return f"ProcessLock(path={self.path!r}, held={self.held!r})"

    @property
    def held(self) -> bool:
        """Is the lock held (by this object)? """
        return self._fd is not None

    def acquire(self) -> None:
        """Take the lock, or raise :class:`Locked`. Idempotent for its holder."""
        if self._fd is not None:
            return
        try:
            fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        except OSError as exc:
            raise Locked(f"cannot open {self.path}: {exc.strerror}") from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            holder = _holder(self.path)
            os.close(fd)
            raise Locked(
                f"{self.path} is held by another process{holder}: the kit "
                "serves one process per data dir (§3.5)"
            ) from exc
        self._fd = fd
        try:
            # The pid is evidence for a human reading the file; the lock is the
            # flock. Nothing decides anything from these bytes.
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode("ascii"))
        except OSError:  # pragma: no cover -- the lock is already ours
            pass

    def release(self) -> None:
        """Give the lock up. Releasing what is not held is not an error."""
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:  # pragma: no cover -- the fd is going away anyway
            pass
        os.close(fd)

    def __enter__(self) -> "ProcessLock":
        self.acquire()
        return self

    def __exit__(self, *exc: Any) -> bool:
        self.release()
        return False


@dataclass(frozen=True)
class Kind:
    """One registered kind of work: its handler and its idempotency claim."""

    name: str
    handler: Callable[[Any], Any]
    idempotency: str

    def as_dict(self) -> dict[str, Any]:
        """The kind as §3.8's self-description will answer it."""
        return {"kind": self.name, "idempotent": self.idempotency}


@dataclass(frozen=True)
class Job:
    """One row of ``aio_jobs``, decoded.

    ``state`` is the column and ``status`` is the wire's word for it (§3.5's
    202 body says ``status``); :meth:`as_dict` answers with ``status``, so a
    client polls one shape and reads one key.
    """

    id: str
    owner: str
    key_id: str | None
    kind: str
    input: Any
    state: str
    result: Any
    error: Any
    attempt: int
    lease_until: int | None
    cancel_requested: bool
    created: int
    started: int | None
    finished: int | None
    claim: str | None = None

    @classmethod
    def of(cls, row: Any) -> "Job":
        """The :class:`Job` of a ``sqlite3.Row``."""
        return cls(
            id=str(row["id"]),
            owner=str(row["owner"]),
            key_id=None if row["key_id"] is None else str(row["key_id"]),
            kind=str(row["kind"]),
            input=_load(row["input"]),
            state=str(row["state"]),
            result=_load(row["result"]),
            error=_load(row["error"]),
            attempt=int(row["attempt"]),
            lease_until=(
                None if row["lease_until"] is None else int(row["lease_until"])
            ),
            cancel_requested=bool(row["cancel_requested"]),
            created=int(row["created"]),
            started=None if row["started"] is None else int(row["started"]),
            finished=None if row["finished"] is None else int(row["finished"]),
            claim=None if row["claim"] is None else str(row["claim"]),
        )

    @property
    def status(self) -> str:
        """§3.5's wire name for the ``state`` column."""
        return self.state

    @property
    def poll(self) -> str:
        """Where this job is polled: ``/v1/jobs/<id>``."""
        return f"{POLL_PATH}{self.id}"

    @property
    def terminal(self) -> bool:
        """Is this job finished for good?"""
        return self.state in TERMINAL

    def as_dict(self) -> dict[str, Any]:
        """The job as the wire answers it: no owner, no lease, no key id.

        The lease is the store's own bookkeeping and the owner is the caller
        (they asked about their own job); neither is anybody's business but the
        store's. ``result`` and ``error`` are ``None`` until they mean
        something, and are always present, so a client reads one shape.
        """
        return {
            "id": self.id,
            "status": self.status,
            "kind": self.kind,
            "attempt": self.attempt,
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "cancel_requested": self.cancel_requested,
            "result": self.result,
            "error": self.error,
            "poll": self.poll,
        }

    def submitted(self) -> dict[str, Any]:
        """§3.5's 202 body, verbatim: ``{"job": {"id", "status", "poll"}}``."""
        return {"job": {"id": self.id, "status": self.status, "poll": self.poll}}


@dataclass(frozen=True)
class Page:
    """One §3.6 page of jobs: the rows, and the cursor of the next one."""

    items: tuple[Job, ...]
    next_cursor: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "items": [job.as_dict() for job in self.items],
            "next_cursor": self.next_cursor,
        }


class HandlerContext:
    """What a handler is handed: ``ctx.job_id``, ``ctx.input``, ``ctx.cancelled()``.

    ``effect_done`` is the handler's own report that the outside effect has
    happened -- it is what decides ``succeeded`` from ``cancelled`` when a
    cancel arrives late, so it is set *after* the effect, never before.
    ``checkpoint()`` is the handler's report that it is at a safe point: a
    clean shutdown after it does not count as an attempt.
    """

    def __init__(self, jobs: "Jobs", job: Job, stop: threading.Event) -> None:
        self.job_id = job.id
        self.kind = job.kind
        self.input = job.input
        self.attempt = job.attempt
        self.effect_done = False
        #: The token this worker's claim wrote on the row. Every write this
        #: worker makes to its own row carries it, so a claim that is no longer
        #: there is a write that does not happen.
        self.claim = job.claim
        self._jobs = jobs
        self._stop = stop
        self._checkpointed = False

    def __repr__(self) -> str:
        return f"HandlerContext(job_id={self.job_id!r}, kind={self.kind!r})"

    def cancelled(self) -> bool:
        """Has a cancel been asked for this job? The row answers, not a cache."""
        return self._jobs.cancel_requested(self.job_id)

    def stopping(self) -> bool:
        """Is the pool shutting down? Checkpoint and return."""
        return self._stop.is_set()

    def checkpoint(self) -> None:
        """Say the handler is at a safe point, so a clean stop is not an attempt."""
        self._checkpointed = True

    @property
    def checkpointed(self) -> bool:
        """Has :meth:`checkpoint` been called?"""
        return self._checkpointed


class Jobs:
    """§3.5's table, its pool and its state machine, in the app's own ``aio.db``.

    ``path`` is the app's kit-owned file. ``lock_path`` defaults to
    ``aio.lock`` next to it -- §3.5's single-process lock, taken by
    :meth:`startup`. ``key`` is §3.6's cursor key, needed by the list page (and
    checked by :func:`router` at wiring time). ``workers`` defaults to
    ``AIO_JOB_WORKERS`` or 2. ``clock`` is injectable so a test can age a lease
    without waiting a minute; ``lease``, ``renew`` and ``max_attempts`` are the
    three numbers of §3.5 and are injectable for the same reason. ``audit`` is
    §3.10's store, when the app keeps both tables in the same file: the keeper
    is the only clock this kit has running, so retention of both happens there
    (:meth:`maybe_purge`).
    """

    def __init__(
        self,
        path: str,
        *,
        key: bytes | None = None,
        lock_path: str | None = None,
        workers: int | None = None,
        clock: Callable[[], float] = time.time,
        lease: int = LEASE,
        renew: float = RENEW,
        poll: float = POLL_SECONDS,
        max_attempts: int = MAX_ATTEMPTS,
        audit: Any = None,
        purge_every: float = PURGE_EVERY,
    ) -> None:
        self.path = str(path)
        self.key = key
        if key is not None:
            _cursor_key(key)
        if self.path == ":memory:" and lock_path is None:
            raise ValueError(
                "a job store needs its data dir: pass lock_path for an "
                "in-memory database, or the single-process lock has no file"
            )
        self.lock_path = (
            str(lock_path)
            if lock_path is not None
            else os.path.join(os.path.dirname(os.path.abspath(self.path)), LOCK_NAME)
        )
        self.workers = (
            workers_from_env(DEFAULT_WORKERS) if workers is None else int(workers)
        )
        self.lease = int(lease)
        self.renew = float(renew)
        self.poll = float(poll)
        self.max_attempts = int(max_attempts)
        self.purge_every = float(purge_every)
        # §3.10's audit table, when the app keeps it in the same file: the
        # keeper is the only clock this kit has running anyway, so retention is
        # its job. ``None`` -- an app that wires no audit store -- is fine.
        self._audit = audit
        self._purged_at = 0.0
        if not 1 <= self.workers <= MAX_WORKERS:
            raise ValueError(f"a pool is 1 to {MAX_WORKERS} threads")
        if self.lease <= 0 or self.renew <= 0:
            raise ValueError("a lease is a positive number of seconds")
        if self.purge_every <= 0:
            raise ValueError("retention runs at most once an hour, not never")
        if self.max_attempts < 1:
            raise ValueError("max_attempts is at least 1")
        self._clock = clock
        # One connection, one lock: every statement here is short, and a claim
        # must be atomic against another thread of this process (the pool's
        # workers are threads, not processes).
        self._lock = threading.RLock()
        self._pool_lock = threading.Lock()
        self._kinds: dict[str, Kind] = {}
        self._live: dict[str, HandlerContext] = {}
        # ``_live`` is written by the handler threads and read by the keeper and
        # by shutdown, so its readers take a snapshot under its own lock: a
        # handler that is starting must not make the lease keeper's iteration
        # fail.
        self._live_lock = threading.Lock()
        # One stop event per pool generation: a handler that outlived
        # shutdown() returns into a worker whose loop must not see a *later*
        # pool's event, so start() hands each generation its own event and
        # everybody else -- ctx.stopping(), the lease keeper -- keeps the one
        # they were given.
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._threads: list[threading.Thread] = []
        self._keeper: threading.Thread | None = None
        self._lock_file = ProcessLock(self.lock_path)
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
        return (
            f"Jobs(path={self.path!r}, workers={self.workers}, "
            f"lock={self._lock_file.held!r})"
        )

    # -- the registry -------------------------------------------------------

    def register(
        self,
        kind: Any,
        handler: Any,
        idempotency: Any = None,
    ) -> Kind:
        """Declare one kind of work: ``register(kind, handler, idempotency=…)``.

        The ``idempotency`` string is how *this* kind cannot do its outside
        effect twice (§3.5: "every job kind in a registry declares how it is
        idempotent; conformance refuses a job kind without that declaration"),
        so a registration without it is refused here -- the earliest place the
        refusal can happen. A second registration of the same name is refused
        too: one kind, one handler, or a redeploy would silently change what a
        queued job means.
        """
        name = str(kind).strip() if kind is not None else ""
        if not name:
            raise ValueError("a job kind has a name")
        if not callable(handler):
            raise TypeError("a job kind has a handler")
        claim = str(idempotency).strip() if isinstance(idempotency, str) else ""
        if not claim:
            raise ValueError(
                f"the job kind {name!r} must declare how it is idempotent: "
                'register(kind, handler, idempotency="<how this kind is '
                'idempotent>")'
            )
        if name in self._kinds:
            raise ValueError(f"the job kind {name!r} is already registered")
        self._kinds[name] = Kind(name=name, handler=handler, idempotency=claim)
        return self._kinds[name]

    def kinds(self) -> tuple[Kind, ...]:
        """Every registered kind, in the order they were registered."""
        return tuple(self._kinds.values())

    def kind(self, name: Any) -> Kind | None:
        """One registered kind, or ``None``."""
        return self._kinds.get(str(name))

    def idempotency(self, name: Any) -> str | None:
        """How one kind says it is idempotent, or ``None`` if it is not registered."""
        found = self.kind(name)
        return None if found is None else found.idempotency

    # -- lifecycle ----------------------------------------------------------

    @property
    def connection(self) -> sqlite3.Connection:
        """The store's connection to ``aio.db``, for a same-file transaction.

        §3.4's ``Claim.set_job(…, db=jobs.connection)`` is the seam it is for:
        the idempotency row and the job it names commit together, so a caller
        never needs a second key to finish the same work.
        """
        return self._db

    @property
    def locked(self) -> bool:
        """Is this store holding the single-process lock?"""
        return self._lock_file.held

    @property
    def running(self) -> int:
        """How many handler threads are alive."""
        return sum(1 for thread in self._threads if thread.is_alive())

    def startup(self) -> tuple[int, int]:
        """Take the lock and apply §3.5's restart rule. ``(interrupted, failed)``.

        Raises :class:`Locked` when another process holds the data dir: a
        second process would double every running job, and refusing to start is
        the only honest answer. Then every ``running`` row becomes
        ``interrupted`` with ``attempt+1`` -- nothing survived the restart that
        was holding it -- and a row whose attempt has reached
        :data:`MAX_ATTEMPTS` becomes ``failed`` with ``error.code =
        "interrupted"``. Both moves are one statement each, inside one
        transaction.
        """
        self._lock_file.acquire()
        now = self._now()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                interrupted = self._db.execute(
                    f"UPDATE {TABLE} SET state = ?, attempt = attempt + 1,"
                    " lease_until = NULL, claim = NULL WHERE state = ?",
                    (INTERRUPTED, RUNNING),
                ).rowcount
                failed = self._db.execute(
                    f"UPDATE {TABLE} SET state = ?, error = ?, finished = ?,"
                    " claim = NULL WHERE state = ? AND attempt >= ?",
                    (FAILED, _json(_INTERRUPTED), now, INTERRUPTED, self.max_attempts),
                ).rowcount
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        if interrupted or failed:
            logger.info(
                "jobs startup: %d running -> interrupted, %d gave up after %d "
                "attempts",
                interrupted,
                failed,
                self.max_attempts,
            )
        return int(interrupted), int(failed)

    def start(self) -> int:
        """Start the pool: ``workers`` handler threads and the lease keeper.

        Refuses to start without the lock (§3.5's invariant is not a
        convention): call :meth:`startup` first. Idempotent -- a second call
        with the pool already up returns its size.
        """
        if not self._lock_file.held:
            raise RuntimeError(
                "start() needs startup(): a job store serves nothing without "
                f"{self.lock_path}"
            )
        self._stop = threading.Event()
        stop = self._stop
        with self._pool_lock:
            if any(thread.is_alive() for thread in self._threads):
                return len(self._threads)
            self._threads = [
                threading.Thread(
                    target=self._work, args=(stop,), name=f"aio-job-{number}", daemon=True
                )
                for number in range(self.workers)
            ]
            self._keeper = threading.Thread(
                target=self._renew_loop, args=(stop,), name="aio-job-lease", daemon=True
            )
            for thread in self._threads:
                thread.start()
            self._keeper.start()
        self._wake.set()
        logger.info("jobs: pool of %d threads up", len(self._threads))
        return len(self._threads)

    def shutdown(self, *, grace: float = 5.0) -> tuple[int, int]:
        """Stop the pool, tell the truth about what was running, drop the lock.

        A handler is not killed -- no thread is -- so the pool waits up to
        ``grace`` seconds for the handlers that are mid-step. Every row still
        ``running`` when that is over becomes ``interrupted`` and is claimable
        again under the same id; it counts as an attempt unless its handler had
        called ``ctx.checkpoint()``, which is §3.5's "a clean shutdown does not
        count as an attempt". Returns ``(interrupted, failed)`` like
        :meth:`startup`, and is idempotent.
        """
        self._stop.set()
        self._wake.set()
        deadline = time.monotonic() + max(0.0, float(grace))
        for thread in [*self._threads, self._keeper]:
            if thread is not None:
                thread.join(max(0.0, deadline - time.monotonic()))
        self._threads = []
        self._keeper = None
        interrupted, failed = self._stop_running()
        with self._live_lock:
            self._live.clear()
        self._lock_file.release()
        logger.info(
            "jobs: pool down; %d interrupted, %d out of attempts",
            interrupted,
            failed,
        )
        return interrupted, failed

    def close(self) -> None:
        """Shut the pool down at once and close the connection."""
        self.shutdown(grace=0.0)
        with self._lock:
            self._db.close()

    # -- writing ------------------------------------------------------------

    def submit(
        self,
        principal: Any,
        kind: Any,
        input: Any = None,
        *,
        before_queue: Callable[[str], None] | None = None,
    ) -> Job:
        """Queue one job: ``state='queued'``, and the row is the job.

        ``principal`` is §3.1's :class:`~agentkit.auth.Principal` (or its id as
        a string) and owns the job; a caller that owns nothing is refused.
        ``kind`` must be registered -- an unregistered kind is a 400
        ``unknown_job_kind``, not a job nobody can run.

        ``before_queue(job_id)`` is called *inside* the insert's transaction,
        before it commits and therefore before any worker can see the row. That
        is §3.4's seam: ``before_queue=lambda job_id: claim.set_job(job_id,
        db=jobs.connection)`` writes the idempotency row that names this job in
        the same transaction, so a crash between the two writes cannot leave a
        job the caller has no way to poll.
        """
        if not self._lock_file.held:
            raise RuntimeError(
                "submit() needs startup(): a job store serves nothing without "
                f"{self.lock_path}"
            )
        owner = owner_of(principal)
        if owner is None:
            raise errors.APIError(
                403,
                "not_allowed",
                "A job belongs to somebody: this caller owns nothing.",
            )
        name = str(kind).strip() if kind is not None else ""
        if name not in self._kinds:
            raise errors.APIError(
                400,
                "unknown_job_kind",
                f"There is no job kind called {name!r}.",
            )
        payload = {} if input is None else dict(input)
        body = _json(payload)  # refuses anything that is not JSON, before the write
        job_id = uuid.uuid4().hex
        key_id = _key_id(principal)
        now = self._now()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    f"INSERT INTO {TABLE} (id, owner, key_id, kind, input, state,"
                    " result, error, attempt, lease_until, cancel_requested,"
                    " created, started, finished)"
                    " VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, 0, NULL, 0, ?, NULL, NULL)",
                    (job_id, owner, key_id, name, body, QUEUED, now),
                )
                if before_queue is not None:
                    before_queue(job_id)
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        self._wake.set()
        job = self.get(job_id)
        if job is None:  # pragma: no cover -- the insert just committed
            raise RuntimeError(f"{job_id} vanished between its insert and its read")
        return job

    def cancel(self, job_id: Any) -> Job | None:
        """Ask a job to stop: at once when queued, else ``cancel_requested``.

        A ``queued`` (or ``interrupted``: a re-run is pending, and the work has
        not started) row becomes ``cancelled`` here and now -- nothing will run
        it. A ``running`` row only gets ``cancel_requested``: the handler checks
        :meth:`HandlerContext.cancelled` between steps, and what the job ends as
        is decided by what the handler reports. A job that is already finished
        is answered unchanged: the state says what happened, and cancelling
        yesterday's success is not a thing this store can do.
        """
        job_id = str(job_id)
        now = self._now()
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                stopped = self._db.execute(
                    f"UPDATE {TABLE} SET state = ?, cancel_requested = 1,"
                    " finished = ? WHERE id = ? AND state = ?",
                    (CANCELLED, now, job_id, QUEUED),
                ).rowcount
                if not stopped:
                    stopped = self._db.execute(
                        f"UPDATE {TABLE} SET state = ?, cancel_requested = 1,"
                        " finished = ? WHERE id = ? AND state = ?",
                        (CANCELLED, now, job_id, INTERRUPTED),
                    ).rowcount
                asked = self._db.execute(
                    f"UPDATE {TABLE} SET cancel_requested = 1 WHERE id = ? AND state = ?",
                    (job_id, RUNNING),
                ).rowcount
                # The answer is read here, before the lock is dropped: a handler
                # that sees the cancel and returns must not be able to race the
                # answer out of the caller's hands -- the reply describes what
                # cancelling did, and the pole it is polling is the next request.
                row = self._db.execute(
                    f"SELECT * FROM {TABLE} WHERE id = ?", (job_id,)
                ).fetchone()
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        if not stopped and not asked:
            logger.info("jobs: %s is not running or queued; nothing to cancel", job_id)
        return None if row is None else Job.of(row)

    # -- reading ------------------------------------------------------------

    def get(self, job_id: Any) -> Job | None:
        """One job by id, or ``None``. The owner rule is the route's, not this."""
        with self._lock:
            row = self._db.execute(
                f"SELECT * FROM {TABLE} WHERE id = ?", (str(job_id),)
            ).fetchone()
        return None if row is None else Job.of(row)

    def count(self, state: Any = None) -> int:
        """How many rows this table holds, all of them or one state's."""
        sql = f"SELECT COUNT(*) FROM {TABLE}"
        args: tuple[Any, ...] = ()
        if state is not None:
            sql += " WHERE state = ?"
            args = (str(state),)
        with self._lock:
            return int(self._db.execute(sql, args).fetchone()[0])

    def cancel_requested(self, job_id: Any) -> bool:
        """Has a cancel been asked for this job? Read from the row, every time."""
        with self._lock:
            row = self._db.execute(
                f"SELECT cancel_requested FROM {TABLE} WHERE id = ?", (str(job_id),)
            ).fetchone()
        return bool(row is not None and row["cancel_requested"])

    def page(
        self,
        *,
        owner: Any = None,
        state: Any = None,
        limit: Any = DEFAULT_LIMIT,
        cursor: Any = None,
    ) -> Page:
        """One §3.6 page of jobs, newest first: ``(created, id)`` descending.

        ``owner`` and ``state`` are equality filters, and they are the query the
        cursor is bound to: a cursor minted for one set of them is 400
        ``bad_cursor`` against another. ``limit`` is 1 to 200.
        """
        size = _limit(limit)
        wanted = None if state is None else str(state)
        if wanted is not None and wanted not in STATES:
            raise ValueError(f"{state!r} is not one of the six job states")
        key = _cursor_key(self.key)
        filters = {
            "owner": None if owner is None else str(owner),
            "state": wanted,
        }
        where, args = _where(filters, _position(cursor, filters, key))
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM {TABLE}{where} ORDER BY created DESC, id DESC LIMIT ?",
                [*args, size + 1],
            ).fetchall()
        # One row over the page is how "is there more" is answered without a
        # second query -- and it is dropped before anything sees it.
        more = len(rows) > size
        items = tuple(Job.of(row) for row in rows[:size])
        last = items[-1] if items else None
        return Page(
            items=items,
            next_cursor=(
                cursors.encode(last.created, last.id, filters, key)
                if more and last is not None
                else None
            ),
        )

    # -- the pool -----------------------------------------------------------

    def _work(self, stop: threading.Event) -> None:
        """One worker thread: claim, run, claim again, until its pool stops.

        ``stop`` is *this* generation's event, never ``self._stop``: a worker
        whose handler outlived :meth:`shutdown` returns here after a later
        ``start()``, and it must exit rather than join a pool it is not part of.
        """
        while not stop.is_set():
            job: Job | None = None
            try:
                job = self._claim(stop)
                if job is None:
                    self._wake.wait(self.poll)
                    self._wake.clear()
                    continue
                self._run(job, stop)
            except BaseException as exc:  # noqa: BLE001 -- the loop is the pool
                # Nothing a row's own work does may end a worker: a worker that
                # dies leaves every later job queued and nobody to run it, and
                # the row it was holding ``running`` forever. The claim-guarded
                # write below is the one attempt to leave that row honest; if it
                # fails too, the next startup's restart rule picks the row up.
                logger.error(
                    "jobs: worker survived %s from %r",
                    type(exc).__name__,
                    job.id if job is not None else "a claim",
                    exc_info=exc,
                )
                if job is not None:
                    try:
                        self._finish(job, None, error=_INTERNAL)
                    except BaseException:  # pragma: no cover -- a broken store
                        logger.error(
                            "jobs: %s could not be marked failed either", job.id
                        )
                continue

    def _claim(self, stop: threading.Event | None = None) -> Job | None:
        """Claim the oldest claimable job, or ``None``. §3.5's transition.

        An ``interrupted`` row is re-queued first (its own ``UPDATE … WHERE
        state=?``) and claimed second; both statements are inside one
        transaction, so a row is never running twice and a cancel never loses a
        race with a claim. The claim writes a fresh token on the row, and every
        write the worker makes to that row afterwards carries the token (see
        :meth:`_finish`), so a thread from an older pool cannot write over the
        row a newer one is holding.

        ``stop`` is the claiming pool's own generation event. A worker that was
        between two claims when its pool's ``shutdown()`` arrived must not take
        the row that shutdown has just given back: it would run a handler for a
        pool that is gone, on a row the next pool is starting. The check sits
        inside the same lock section as the transaction it guards, so either the
        claim happens first -- and ``_stop_running`` interrupts that row like any
        other -- or the stop is already set and this takes nothing.
        """
        if (stop is not None and stop.is_set()) or not self._lock_file.held:
            return None
        now = self._now()
        job_id = ""
        claimed = 0
        with self._lock:
            if (stop is not None and stop.is_set()) or not self._lock_file.held:
                # The pool is stopping (or the lock is gone, so this process
                # must not work at all): the row stays where shutdown left it.
                return None
            self._db.execute("BEGIN IMMEDIATE")
            try:
                row = self._db.execute(
                    f"SELECT id FROM {TABLE} WHERE state IN (?, ?)"
                    " ORDER BY created, id LIMIT 1",
                    (QUEUED, INTERRUPTED),
                ).fetchone()
                if row is not None:
                    job_id = str(row["id"])
                    self._db.execute(
                        f"UPDATE {TABLE} SET state = ?, claim = NULL"
                        " WHERE id = ? AND state = ?",
                        (QUEUED, job_id, INTERRUPTED),
                    )
                    claimed = self._db.execute(
                        f"UPDATE {TABLE} SET state = ?, claim = ?,"
                        " lease_until = ?, started = COALESCE(started, ?)"
                        " WHERE id = ? AND state = ?",
                        (
                            RUNNING,
                            secrets.token_hex(8),
                            now + self.lease,
                            now,
                            job_id,
                            QUEUED,
                        ),
                    ).rowcount
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        if not claimed:
            # Somebody else moved the row between the pick and the claim. §3.5:
            # a zero row count means exactly that, and it is not an error.
            return None
        return self.get(job_id)

    def _run(self, job: Job, stop: threading.Event) -> None:
        """Run one claimed job's handler and write down what it did."""
        kind = self._kinds.get(job.kind)
        if kind is None:
            # The row names a kind this deployment does not have any more (a
            # handler was removed between the submit and the run). It is a
            # failed job, not a stuck one.
            logger.error("jobs: %s names the unknown kind %r", job.id, job.kind)
            self._finish(job, None, error=_UNKNOWN_KIND)
            return
        ctx = HandlerContext(self, job, stop)
        with self._live_lock:
            self._live[job.id] = ctx
        try:
            value = kind.handler(ctx)
        except BaseException as exc:
            # A handler that raises is a failed job, never a dead worker.
            logger.error(
                "jobs: %s (%s) raised", job.id, job.kind, exc_info=exc
            )
            self._finish(job, ctx, error=_error_of(exc))
        else:
            result, error = _result_of(value)
            self._finish(job, ctx, result=result, error=error)
        finally:
            # Only the context this call put there. A handler that outlived its
            # pool returns here *after* a newer pool claimed the same row (the
            # id is the row's), and an unconditional pop would take the new
            # claim's context with it: the keeper would renew nothing and a
            # clean shutdown would count an attempt §3.5 says it does not.
            with self._live_lock:
                if self._live.get(job.id) is ctx:
                    del self._live[job.id]

    def _finish(
        self,
        job: Job,
        ctx: HandlerContext | None = None,
        *,
        result: Any = None,
        error: Any = None,
    ) -> bool:
        """Move a running job to its terminal state, or leave the row alone.

        The rule, in one place: a cancel that arrived after the handler's last
        outside effect does **not** make the job ``cancelled`` -- the effect
        happened, and §3.5 says the job ends ``succeeded``. With no effect
        reported, a cancel means ``cancelled``.

        Two guards keep a row somebody else moved from being written over, and
        they are the same idea -- write only the row you are holding:
        ``WHERE state='running'`` (a shutdown or a cancel moved it), and
        ``AND claim IS ?`` with the token this worker's claim wrote. The second
        is what stops a handler thread left over from an earlier pool from
        finishing the row a later pool has claimed and re-run since: its token
        is not on the row any more, so its write does not happen.
        """
        job_id = job.id
        try:
            with self._lock:
                cancel = self.cancel_requested(job_id)
                effect = bool(ctx is not None and ctx.effect_done)
                if error is None:
                    state = SUCCEEDED
                    if cancel and not effect:
                        state, result = CANCELLED, None
                elif cancel and not effect:
                    state, result, error = CANCELLED, None, None
                else:
                    state = FAILED
                written = self._db.execute(
                    f"UPDATE {TABLE} SET state = ?, result = ?, error = ?,"
                    " finished = ?, lease_until = NULL, claim = NULL"
                    " WHERE id = ? AND state = ? AND claim IS ?",
                    (
                        state,
                        _optional_json(result),
                        _optional_json(error),
                        self._now(),
                        job_id,
                        RUNNING,
                        job.claim,
                    ),
                ).rowcount
        except sqlite3.ProgrammingError:  # pragma: no cover -- closed store
            # The handler outlived close(); its row was already moved by the
            # shutdown that closed this store, and there is nothing to write.
            logger.debug("jobs: the store is closed; %s was left as it was", job_id)
            return False
        if not written:
            logger.warning(
                "jobs: %s was not running under our claim any more; the row "
                "keeps its state",
                job_id,
            )
        return bool(written)

    def _renew_loop(self, stop: threading.Event) -> None:
        """The lease keeper: renew every running row's lease every 20 s.

        Not a reaper: it only ever pushes ``lease_until`` forward, and an
        expired lease is evidence for the restart rule rather than a signal to
        take a job away from a thread that is still holding it.
        """
        while not stop.wait(self.renew):
            try:
                self._touch_leases()
            except Exception as exc:  # pragma: no cover -- a closed store
                logger.debug("jobs: lease renewal stopped: %s", type(exc).__name__)
                return
            try:
                self.maybe_purge()
            except Exception as exc:  # a closed store, or a broken one
                logger.warning("jobs: retention failed: %s", type(exc).__name__)

    def maybe_purge(self) -> tuple[int, int]:
        """One retention pass, at most once per ``purge_every`` seconds.

        The keeper calls this; it is public so a test (or an operator's script)
        can run one pass without waiting an hour. Returns ``(job rows, audit
        rows)`` deleted. A window of ``0`` in the environment keeps everything,
        which is why a window can be absent rather than zero.
        """
        now = time.monotonic()
        if now - self._purged_at < self.purge_every:
            return (0, 0)
        self._purged_at = now
        removed = 0
        jobs_window = retention_seconds()
        if jobs_window is not None:
            removed = self.purge(jobs_window)
        audited = 0
        if self._audit is not None:
            audit_window = retention_seconds(
                AUDIT_RETENTION_ENV, DEFAULT_AUDIT_RETENTION_DAYS
            )
            if audit_window is not None:
                audited = int(self._audit.purge(audit_window))
        if removed or audited:
            logger.info(
                "jobs: retention removed %d job rows and %d audit rows",
                removed,
                audited,
            )
        return (removed, audited)

    def purge(self, older_than_seconds: Any) -> int:
        """§3.10's retention: delete terminal rows finished before the cutoff.

        ``older_than_seconds`` is a window, not a time: the cutoff is that many
        seconds behind the clock, so the caller's number is the policy. Only the
        three terminal states are touched -- a ``queued`` or ``running`` row is
        work, and an ``interrupted`` one is work §3.5 still owes somebody an
        answer for -- and only rows that have a ``finished`` at all. Returns how
        many rows went.
        """
        window = float(older_than_seconds)
        if window <= 0:
            raise ValueError("a retention window is a positive number of seconds")
        cutoff = self._now() - int(window)
        states = ", ".join("?" for _ in TERMINAL)
        with self._lock:
            return int(
                self._db.execute(
                    f"DELETE FROM {TABLE} WHERE state IN ({states})"
                    " AND finished IS NOT NULL AND finished < ?",
                    (*sorted(TERMINAL), cutoff),
                ).rowcount
            )

    def _touch_leases(self) -> int:
        """``lease_until = now + lease`` for the rows this pool is holding.

        Only the rows whose claim belongs to a context this pool has live: a row
        another pool (or a handler thread left over from one) owns is not this
        keeper's to renew. With nothing live there is nothing to renew and no
        statement to run.
        """
        with self._live_lock:
            claims = [ctx.claim for ctx in self._live.values() if ctx.claim]
        if not claims:
            return 0
        holders = ",".join("?" for _ in claims)
        with self._lock:
            return int(
                self._db.execute(
                    f"UPDATE {TABLE} SET lease_until = ? WHERE state = ?"
                    f" AND claim IN ({holders})",
                    (self._now() + self.lease, RUNNING, *claims),
                ).rowcount
            )

    def _stop_running(self) -> tuple[int, int]:
        """Interrupt what is still running, with §3.5's attempt rule.

        A handler that checkpointed said it was at a safe point, so its
        interruption does not count as an attempt; anything else is interrupted
        exactly as a restart would have interrupted it. A row whose attempt has
        reached the cap gives up here, so a job cannot be re-run forever. Both
        moves drop the row's claim: it is nobody's any more, and a handler
        thread that outlived this shutdown must not be able to write it.
        """
        now = self._now()
        interrupted = 0
        failed = 0
        with self._lock:
            with self._live_lock:
                live = dict(self._live)
            self._db.execute("BEGIN IMMEDIATE")
            try:
                rows = self._db.execute(
                    f"SELECT id, attempt FROM {TABLE} WHERE state = ?", (RUNNING,)
                ).fetchall()
                for row in rows:
                    ctx = live.get(str(row["id"]))
                    checkpointed = bool(ctx is not None and ctx.checkpointed)
                    attempt = int(row["attempt"]) + (0 if checkpointed else 1)
                    if attempt >= self.max_attempts:
                        self._db.execute(
                            f"UPDATE {TABLE} SET state = ?, attempt = ?, error = ?,"
                            " finished = ?, lease_until = NULL, claim = NULL"
                            " WHERE id = ? AND state = ?",
                            (
                                FAILED,
                                attempt,
                                _json(_INTERRUPTED),
                                now,
                                row["id"],
                                RUNNING,
                            ),
                        )
                        failed += 1
                    else:
                        self._db.execute(
                            f"UPDATE {TABLE} SET state = ?, attempt = ?,"
                            " lease_until = NULL, claim = NULL"
                            " WHERE id = ? AND state = ?",
                            (INTERRUPTED, attempt, row["id"], RUNNING),
                        )
                        interrupted += 1
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return interrupted, failed

    def _now(self) -> int:
        """The clock, in whole seconds (a lease is seconds)."""
        return int(self._clock())


class Submit(BaseModel):
    """The body of ``POST /v1/jobs``: which kind of work, and its input."""

    kind: str = Field(min_length=1, max_length=KIND_MAX, pattern=KIND_RE)
    input: dict[str, Any] = Field(default_factory=dict)


def _idem_seam(jobs: Jobs, idem: Any) -> None:
    """Refuse to wire §3.4 and §3.5 together across two databases.

    ``submit`` writes the new job's id into the key's row *inside the job's own
    transaction* -- that is the promise a replay rests on, and it only holds if
    ``aio_idem`` is a table in this store's connection. An app that mounted the
    two stores against different files would otherwise look right until its
    first submit, and then answer 500; this fails at wiring time instead, the way
    a missing cursor key does, and names §3.4 so the fix is obvious.
    """
    if idem is None:
        return
    table = getattr(idem, "TABLE", idempotency.TABLE)
    found = jobs.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    if found is None:
        raise RuntimeError(
            f"§3.4: the idempotency store has no ``{table}`` table in the jobs "
            "database, so a job's id cannot be recorded in the same "
            "transaction that queues it (a replay would run the work twice). "
            "Build the idempotency store on this store's database -- the same "
            "file ``jobs.connection`` points at -- or mount these routes "
            "without ``idem=`` and lose the replay protection."
        )


def router(
    jobs: Jobs,
    app_cfg: auth.AuthConfig,
    *,
    limits: rate_limits.Limits | None = None,
    idem: Any = None,
) -> APIRouter:
    """§3.5's four routes, and nothing else.

    ``app.include_router(jobs.router(store, cfg, limits=limits, idem=idem))``::

        POST   /v1/jobs                202 {"job": {"id", "status", "poll"}}
        GET    /v1/jobs/{id}           its owner only, 404 for anybody else
        GET    /v1/jobs?state=         one §3.6 page of the caller's own jobs
        POST   /v1/jobs/{id}/cancel    200, the job as it stands

    Hand in the app's :class:`~agentkit.limits.Limits` and every route is
    charged like every other ``/v1`` route of §3.7 -- the submit is a *run*
    route (10 a minute), a cancel is a write. Hand in the app's
    :class:`~agentkit.idem.Idempotency` and the submit is wrapped in §3.4's
    rule, *required*: a job-creating call must send an ``Idempotency-Key``, and
    the key's row names the job id inside the same transaction that queues it.
    Without ``idem`` the routes work and the caller gets no replay protection --
    an app that queues jobs mounts both.

    The cursor key is required *here*, at wiring time, and not at request time:
    an app that forgot it has to fail when it is built. The same goes for
    ``idem``: §3.4's table has to be in this store's connection, or the wiring
    is refused by name.
    """
    _cursor_key(jobs.key)
    _idem_seam(jobs, idem)
    charge: list[Any] = []
    if limits is not None:
        charge.append(Depends(rate_limits.dependency(limits, app_cfg)))

    def guarded(required: bool) -> Callable[[Any], Any]:
        """§3.4's wrapper when the app handed one in, the endpoint when not."""

        def decorate(endpoint: Any) -> Any:
            if idem is None:
                return endpoint
            return idem.idempotent(required=required)(endpoint)

        return decorate

    v1 = APIRouter(prefix=errors.PREFIX)

    @v1.post("/jobs", status_code=202, dependencies=charge)
    @rate_limits.run_route
    @guarded(True)
    def create_job(
        request: Request,
        body: Submit,
        principal: auth.Principal = Depends(auth.require("run", app_cfg)),
    ) -> dict[str, Any]:
        claim = getattr(request.state, "idem", None)
        job = jobs.submit(
            principal,
            body.kind,
            body.input,
            before_queue=(
                None
                if claim is None
                else lambda job_id: claim.set_job(job_id, db=jobs.connection)
            ),
        )
        return job.submitted()

    @v1.get("/jobs/{job_id}", dependencies=charge)
    def read_job(
        job_id: str,
        principal: auth.Principal = Depends(auth.require("read", app_cfg)),
    ) -> dict[str, Any]:
        return {"job": _mine(jobs, job_id, principal).as_dict()}

    @v1.get("/jobs", dependencies=charge)
    def list_jobs(
        state: str | None = Query(None, pattern=STATE_RE),
        limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
        cursor: str | None = Query(None, min_length=1, max_length=1024),
        principal: auth.Principal = Depends(auth.require("read", app_cfg)),
    ) -> dict[str, Any]:
        return jobs.page(
            owner=owner_of(principal), state=state, limit=limit, cursor=cursor
        ).as_dict()

    @v1.post("/jobs/{job_id}/cancel", dependencies=charge)
    @guarded(False)
    def cancel_job(
        request: Request,
        job_id: str,
        principal: auth.Principal = Depends(auth.require("write", app_cfg)),
    ) -> dict[str, Any]:
        job = _mine(jobs, job_id, principal)
        return {"job": (jobs.cancel(job.id) or job).as_dict()}

    return v1


# -- the pieces the two halves share -----------------------------------------


def _mine(jobs: Jobs, job_id: Any, principal: Any) -> Job:
    """The caller's own job, or 404 -- never "somebody else's job"."""
    job = jobs.get(job_id)
    if job is None or job.owner != owner_of(principal):
        raise errors.APIError(404, "not_found", errors.MESSAGES["not_found"])
    return job


def _where(
    filters: Mapping[str, Any], position: tuple[Any, Any] | None
) -> tuple[str, list[Any]]:
    """The page's ``WHERE``: the filters, then the keyset position."""
    clauses: list[str] = []
    args: list[Any] = []
    for column in ("owner", "state"):
        if filters.get(column) is not None:
            clauses.append(f"{column} = ?")
            args.append(filters[column])
    if position is not None:
        created, last_id = position
        clauses.append("(created < ? OR (created = ? AND id < ?))")
        args.extend([created, created, last_id])
    return (f" WHERE {' AND '.join(clauses)}" if clauses else ""), args


def _position(
    cursor: Any, filters: Mapping[str, Any], key: bytes | None
) -> tuple[Any, Any] | None:
    """The keyset position a cursor names, or ``None`` for the first page."""
    if cursor is None:
        return None
    return cursors.decode(str(cursor), dict(filters), key)


def _limit(limit: Any) -> int:
    """A §3.6 page size: 1 to 200."""
    size = int(limit)
    if not 1 <= size <= MAX_LIMIT:
        raise ValueError(f"limit is 1 to {MAX_LIMIT}")
    return size


def _cursor_key(key: Any) -> bytes:
    """§3.6's cursor key: at least 16 bytes of the app's own secret material."""
    if not isinstance(key, (bytes, bytearray)) or len(key) < cursors.MIN_KEY_BYTES:
        raise ValueError(
            "the job list needs a §3.6 cursor key: at least "
            f"{cursors.MIN_KEY_BYTES} bytes of the app's own secret material"
        )
    return bytes(key)


def _key_id(principal: Any) -> str | None:
    """The key that created a job, when a bearer did."""
    key_id = getattr(principal, "key_id", None)
    if isinstance(key_id, str) and key_id.strip():
        return key_id.strip()
    return None


def _result_of(value: Any) -> tuple[Any, Any]:
    """``(result, error)`` for what a handler returned: a JSON object, or a bug.

    Every ``Exception`` the encoder can raise is the same answer -- the handler
    returned something §3.5 cannot write down -- and none of them may reach the
    worker's loop: a result nested deep enough to exhaust the stack raises
    ``RecursionError``, which is not a ``ValueError``, and a worker killed by a
    *result* is a row left ``running`` forever with nobody to run it.
    """
    if value is None:
        return {}, None
    if not isinstance(value, Mapping):
        return None, dict(_BAD_RESULT)
    try:
        result = dict(value)
        _json(result)
    except Exception:  # noqa: BLE001 -- the answer is the same for all of them
        return None, dict(_BAD_RESULT)
    return result, None


def _error_of(exc: BaseException) -> dict[str, str]:
    """The error of a failed job: the exception's type, never its message.

    §3.3 refuses to hand an exception's words to a caller, and a job's error is
    answered to one; the traceback goes to the log, where it belongs.
    """
    return {"code": "handler_error", "type": type(exc).__name__}


def _json(value: Any) -> str:
    """JSON text for a column. A value that is not JSON is refused, loudly."""
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def _optional_json(value: Any) -> str | None:
    """JSON text for a nullable column: ``None`` stays ``None``."""
    return None if value is None else _json(value)


def _load(text: Any) -> Any:
    """The value of a JSON column: ``None`` for an empty column."""
    if text is None:
        return None
    try:
        return json.loads(text)
    except ValueError:  # pragma: no cover -- only a hand-edited row can do this
        logger.warning("jobs: a JSON column holds something that is not JSON")
        return None


def _holder(path: str) -> str:
    """`` (pid 1234)`` when the lock file names one, else ``""``. Evidence only."""
    try:
        with open(path, "r", encoding="ascii", errors="replace") as handle:
            pid = handle.read(32).strip()
    except OSError:
        return ""
    return f" (pid {pid})" if pid.isdigit() else ""
