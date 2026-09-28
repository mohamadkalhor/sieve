"""One writer at a time on the store -- card O9b, step 1.

Three writers reach the same SQLite file today and never coordinate:
`sieve-pull.service` (`sieve pull` and `sieve plan --store`), `sieve-run.service`
(`sieve run`), and the API's own bulk writers -- `POST /v1/sources/{name}/pull`
and `POST /v1/apply`. SQLite serialises a *statement*, not a pull: two of these
running at once interleave whole snapshots, fold the same row into two sources,
or lose a chain write to a `database is locked` that reaches whoever asked as a
503. The timer firing while somebody pressed "pull" is not a fault in either
caller -- it is two writers and one file.

So there is one lock, an `flock` on `<data dir>/store.lock`, taken by every
command and route that writes the store in bulk:

* the CLI waits `SIEVE_STORE_LOCK_WAIT` seconds (default 600) for it and then
  exits non-zero with a sentence naming who holds it;
* the API does the same and answers 503 `store_busy` with `Retry-After` when
  the wait runs out -- a waiting writer is not a 500.

Two properties this module owes its callers:

* **A reader never blocks.** This is a *writer* lock: a `sieve plan` without
  `--store`, a `GET /v1/rankings/...`, the web app -- none of them take it, and
  WAL is what lets them read while the lock is held.
* **Re-entrant within one process.** `sieve run` calls the pull path itself, and
  the API's pull job runs inside the process that mounted it. `flock` is per
  open file description, so a second `flock` on a second descriptor of the same
  file from the same process would deadlock against the first: the nesting is
  counted here instead, and the last release is the one that lets go. The
  exclusion this module promises is therefore *between processes*, which is
  where the unsynchronised writers are (three systemd units and the service).
"""

from __future__ import annotations

import fcntl
import os
import threading
import time
from pathlib import Path
from types import TracebackType
from typing import Any

__all__ = [
    "DEFAULT_WAIT",
    "LOCK_NAME",
    "WAIT_ENV",
    "StoreBusy",
    "StoreLock",
    "lock_path",
    "store_lock",
    "wait_seconds",
]

#: The file, beside the store it guards: `<data dir>/store.lock`. The kit's own
#: `aio.lock` (its single-process invariant, §3.5) is a different promise about
#: a different file, and the two never name the same path.
LOCK_NAME = "store.lock"

#: How long a second writer waits before giving up, in seconds. Overridable so
#: a test -- or an operator watching a pull that will never finish -- can say
#: "do not queue behind that": `SIEVE_STORE_LOCK_WAIT=0` is an immediate
#: refusal, which is what a caller that cannot wait wants.
WAIT_ENV = "SIEVE_STORE_LOCK_WAIT"
DEFAULT_WAIT = 600.0

#: How often a waiting writer looks again. Short enough that the handover after
#: a pull feels immediate, long enough that a queue of them is not a spin.
POLL = 0.1


class StoreBusy(RuntimeError):
    """Another writer holds the store: nothing was written, try again later.

    Raised instead of blocking for ever, and instead of writing underneath
    somebody else's pull. The message names the file and, when the holder left
    a pid in it, the process -- a person reading a timer's log deserves to know
    which one has the store.
    """


def wait_seconds(default: float = DEFAULT_WAIT) -> float:
    """`SIEVE_STORE_LOCK_WAIT` in seconds, or `default` when it says nothing.

    A value that is not a number is not a reason to fail a pull: it is a typo
    in `/etc/default/sieve`, and the default is what it meant. A negative one is
    read as zero, since "wait minus ten seconds" is not a wait.
    """
    raw = os.environ.get(WAIT_ENV, "").strip()
    if not raw:
        return default
    try:
        return max(0.0, float(raw))
    except ValueError:
        return default


def lock_path(target: Any) -> Path:
    """`<data dir>/store.lock` for a `Config`, a store path, or a directory.

    The data dir is derived from the setting that already says where the store
    lives (`Config.db_path`), never from the working directory: a lock file in
    whatever directory a timer happened to start in would guard nothing.
    """
    db = getattr(target, "db_path", None)
    if db is None:
        given = Path(str(target))
        base = given.parent if given.suffix else given
    else:
        base = Path(db).parent
    return base / LOCK_NAME


class _Held:
    """The lock one process holds on one path: its descriptor, and how deep."""

    __slots__ = ("count", "fd")

    def __init__(self, fd: int) -> None:
        self.fd = fd
        self.count = 1


#: What this process holds, by path. `flock` cannot answer "do I already hold
#: this?" -- a second descriptor of the same file conflicts with the first,
#: even in one process -- so the nesting is kept here.
_REGISTRY_LOCK = threading.Lock()
_HELD: dict[str, _Held] = {}


class StoreLock:
    """The store's writer lock, as a context manager.

    `acquire()` waits up to `wait` seconds for the lock and raises
    :class:`StoreBusy` when the wait runs out; `release()` gives it back.
    Nested `with` blocks in one process are one hold, not a deadlock.
    """

    def __init__(self, path: Any, wait: float | None = None) -> None:
        self.path = str(path)
        self.wait = wait_seconds() if wait is None else max(0.0, float(wait))
        self._took = False

    def __repr__(self) -> str:
        return f"StoreLock(path={self.path!r}, wait={self.wait:g}, took={self._took!r})"

    @property
    def held(self) -> bool:
        """Is the store locked by *this* object?"""
        return self._took

    def acquire(self) -> "StoreLock":
        """Take the lock, waiting, or raise :class:`StoreBusy`."""
        deadline = time.monotonic() + self.wait
        while True:
            if _nested(self):
                return self
            try:
                fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
            except OSError as exc:  # no data dir, no permission: say which
                raise StoreBusy(
                    f"cannot open {self.path}: {exc.strerror}"
                ) from exc
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                if time.monotonic() >= deadline:
                    raise StoreBusy(_busy(self.path, self.wait)) from None
                time.sleep(POLL)
                continue
            _take(self, fd)
            return self

    def release(self) -> None:
        """Give the lock up. Releasing what is not held is not an error."""
        if not self._took:
            return
        self._took = False
        with _REGISTRY_LOCK:
            entry = _HELD.get(self.path)
            if entry is None:  # pragma: no cover -- only if somebody dropped it
                return
            entry.count -= 1
            if entry.count > 0:
                return
            del _HELD[self.path]
        try:
            fcntl.flock(entry.fd, fcntl.LOCK_UN)
        except OSError:  # pragma: no cover -- the descriptor is going anyway
            pass
        os.close(entry.fd)

    def __enter__(self) -> "StoreLock":
        return self.acquire()

    def __exit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        self.release()
        return False


def store_lock(target: Any, wait: float | None = None) -> StoreLock:
    """The store's writer lock for `target` (`Config`, store path, or dir)."""
    return StoreLock(lock_path(target), wait=wait)


def _nested(lock: StoreLock) -> bool:
    """One hold deeper on a path this process already holds, or False."""
    with _REGISTRY_LOCK:
        entry = _HELD.get(lock.path)
        if entry is None:
            return False
        entry.count += 1
    lock._took = True
    return True


def _take(lock: StoreLock, fd: int) -> None:
    """Record a freshly taken lock, and leave the pid for a human."""
    with _REGISTRY_LOCK:
        _HELD[lock.path] = _Held(fd)
    lock._took = True
    try:
        # Evidence for the person who reads the message below; nothing decides
        # anything from these bytes. The flock is the lock.
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
    except OSError:  # pragma: no cover -- the lock is already ours
        pass


def _holder(path: str) -> int | None:
    """The pid the holder wrote into the lock file, when it wrote one."""
    try:
        return int(Path(path).read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def _busy(path: str, wait: float) -> str:
    """The sentence a writer that ran out of patience is told."""
    pid = _holder(path)
    who = f" (pid {pid})" if pid else ""
    waited = f"waited {wait:g}s" if wait else "no wait"
    return (
        f"another process{who} is writing the store: {path} is held, {waited}. "
        f"Try again when that one finishes, or give {WAIT_ENV} more seconds."
    )
