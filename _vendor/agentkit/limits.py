"""§3.7 -- the body cap and the three rate limits.

§3.7 in one paragraph: *body cap 1 MB default; per-route overrides declared in
code; uploads stream to a temp file, the cap counted while reading; per key 120
requests/min, 30 writes/min, 10 ``run``/min (env-overridable), token bucket on a
monotonic clock, in process; 429 with ``Retry-After``.* The two halves of that
sentence are the two pieces of this module.

:class:`BodyCap` is an ASGI middleware -- ``app.add_middleware(BodyCap,
cap=…, overrides={"/v1/uploads": 64 * 1024 * 1024})`` -- and it owns the ``/v1``
surface only. It counts the body *while it streams*, so the answer does not
depend on what the client claims:

* a declared ``Content-Length`` over the cap is refused before a byte is read;
* a body without one (``Transfer-Encoding: chunked``) is counted chunk by chunk,
  and the chunk that takes the total over the cap ends the request. A lying
  ``Content-Length`` changes nothing: the count is what decides.
* The refusal is *raised* -- a 413 ``HTTPException`` from inside the app's body
  read -- rather than written by the middleware. Every ``/v1`` app has already
  installed §3.3's handlers (:func:`agentkit.errors.install`), and those own the
  error envelope and the request id, so a body over the cap answers with exactly
  the bytes any other 413 of this app answers with. The handler never runs, so
  nothing partial is ever acted on, and the app never reads past the cap.
* The one exception is an app that answers *before* reading the body: there a
  response is already committed, so the middleware stops feeding the body and
  leaves the answer to the app.

:class:`Limits` is the three buckets -- requests, writes (POST/PUT/PATCH/DELETE)
and ``run`` routes -- keyed by §3.1's principal id, on ``time.monotonic``. It is
charged from a dependency, :func:`dependency`, and not from a middleware,
because §3.1's order on every ``/v1`` request is *credentials → limits →
idempotency insert → handler*: the bucket key is the principal id, and only the
credential rule knows it. ``Depends(dependency(limits, cfg))`` resolves the
credential first and charges second, and a 429 raised there never reaches §3.4's
table -- a refused request leaves no idempotency row (plan §3.1).

A route is marked a ``run`` with :func:`run_route` on the endpoint; the marker is
read off the matched route at request time, so it costs the route nothing.

The donors are press's ``press/system.py:43-46`` and ``press/http.py:31-53``
(bodies capped per action name, session writes limited per person); the shapes
are theirs, the accounting is §3.7's. Both pieces are per process: the apps run
one worker (plan §10), so a bucket is not shared state between processes and does
not need to be.
"""

from __future__ import annotations

import math
import os
import threading
import time
from typing import Any, Callable, Iterable, Mapping, MutableMapping

from fastapi import Depends, Request
from starlette.exceptions import HTTPException as StarletteHTTPException

from agentkit import auth, errors

__all__ = [
    "BodyCap",
    "DEFAULT_BODY_CAP",
    "DEFAULT_REQ_PER_MINUTE",
    "DEFAULT_RUN_PER_MINUTE",
    "DEFAULT_WRITE_PER_MINUTE",
    "ENV_REQ",
    "ENV_RUN",
    "ENV_WRITE",
    "MAX_BUCKETS",
    "RUN_MARK",
    "WRITE_METHODS",
    "Limits",
    "TokenBucket",
    "dependency",
    "is_run_route",
    "run_route",
]

# -- the body cap (§3.7) ------------------------------------------------------

# One megabyte, the default of §3.7. A route that genuinely needs more says so in
# the ``overrides`` dict, next to the route, so the exception is in the code a
# reviewer reads.
DEFAULT_BODY_CAP = 1024 * 1024

CONTENT_LENGTH = "content-length"


# -- the rate limits (§3.7) ---------------------------------------------------

DEFAULT_REQ_PER_MINUTE = 120
DEFAULT_WRITE_PER_MINUTE = 30
DEFAULT_RUN_PER_MINUTE = 10

ENV_REQ = "AIO_RATE_REQ"
ENV_WRITE = "AIO_RATE_WRITE"
ENV_RUN = "AIO_RATE_RUN"

# §3.7: a write is a method that changes state. HEAD and OPTIONS are not reads
# either, but they change nothing and are counted as requests.
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# One bucket per principal, and a ceiling on how many can exist: a flood of
# distinct keys (a key minted per request) must not become unbounded memory.
MAX_BUCKETS = 1024

RUN_MARK = "__aio_run__"


class BodyCap:
    """§3.7's body cap, as ASGI middleware on the ``/v1`` surface.

    ``overrides`` is ``{path prefix: bytes}``: the longest prefix that matches
    the request path wins, and a path under no prefix gets ``cap``. Prefixes are
    compared on whole segments, so ``/v1/uploads`` does not cover
    ``/v1/uploads-archive``.
    """

    def __init__(
        self,
        app: Any,
        *,
        cap: int = DEFAULT_BODY_CAP,
        overrides: Mapping[str, int] | None = None,
        prefix: str = errors.PREFIX,
    ) -> None:
        self.app = app
        self.cap = _positive(cap, "cap")
        self.overrides = _overrides(overrides)
        self.prefix = str(prefix or errors.PREFIX)

    def limit_for(self, path: str) -> int:
        """The cap of this path: the longest matching override, else ``cap``."""
        chosen, longest = self.cap, -1
        for prefix, cap in self.overrides.items():
            if _under(path, prefix) and len(prefix) > longest:
                chosen, longest = cap, len(prefix)
        return chosen

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http" or not _under(
            str(scope.get("path") or ""), self.prefix
        ):
            await self.app(scope, receive, send)
            return

        limit = self.limit_for(str(scope.get("path") or ""))
        declared = _declared(scope)
        if declared is not None and declared > limit:
            # A declared length over the cap needs no reading to believe.
            await _refuse(scope, send)
            return

        counted = 0
        started = False

        async def capped_receive() -> Any:
            nonlocal counted
            message = await receive()
            if message.get("type") != "http.request":
                return message
            counted += len(message.get("body") or b"")
            if counted <= limit:
                return message
            if started:
                # The app began answering without the rest of the body: its
                # response is committed, so stop feeding it and let it be.
                return {"type": "http.disconnect"}
            # §3.3's handlers own the envelope, so the refusal travels as an
            # HTTPException to the app that installed them -- one 413 byte for
            # byte like any other 413 of this app, and no partial body acted on.
            raise StarletteHTTPException(413, errors.MESSAGES["too_large"])

        async def capped_send(message: Any) -> None:
            nonlocal started
            if message.get("type") == "http.response.start":
                started = True
            await send(message)

        await self.app(scope, capped_receive, capped_send)


class TokenBucket:
    """One §3.7 bucket: ``per_minute`` tokens a minute, refilled smoothly.

    A bucket starts full and refills one token every ``60 / per_minute`` seconds
    on ``time.monotonic`` -- a wall clock can step backwards and hand out tokens
    that were never earned. One bucket per key, in memory, in this process.

    :meth:`take` is the atomic check-and-spend: ``None`` when a token was there,
    otherwise the whole seconds until one is -- the number that goes into
    ``Retry-After`` (§3.7), never below 1. :meth:`peek` asks the same question
    without spending, which is what a request charged to three buckets needs.
    """

    def __init__(
        self,
        per_minute: int = DEFAULT_REQ_PER_MINUTE,
        *,
        clock: Callable[[], float] = time.monotonic,
        burst: int | None = None,
    ) -> None:
        self.per_minute = _positive(per_minute, "per_minute")
        self.capacity = float(
            self.per_minute if burst is None else _positive(burst, "burst")
        )
        # One token this many seconds apart. Seconds-per-token rather than
        # tokens-per-second because every number a bucket answers in is seconds:
        # dividing by it keeps ``Retry-After`` exact where 30 * (2 / 60) is
        # 0.9999999999999999 and a caller is told to wait again for no reason.
        self.seconds_per_token = 60.0 / self.per_minute
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def take(self, key: Any) -> int | None:
        """Spend one token of ``key``, or answer the seconds until one is there."""
        name = _key(key)
        now = self._clock()
        with self._lock:
            tokens, _ = self._tokens(name, now)
            if tokens < 1.0:
                return self._wait(tokens)
            self._buckets[name] = (tokens - 1.0, now)
            if len(self._buckets) > MAX_BUCKETS:
                self._sweep(now)
            return None

    def peek(self, key: Any) -> int | None:
        """The seconds until ``key`` has a token, or ``None``, spending nothing."""
        name = _key(key)
        now = self._clock()
        with self._lock:
            tokens, _ = self._tokens(name, now)
            return None if tokens >= 1.0 else self._wait(tokens)

    def forget(self, key: Any | None = None) -> None:
        """Drop one key's bucket (its tokens come back full), or every bucket."""
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(_key(key), None)

    def _tokens(self, name: str, now: float) -> tuple[float, float]:
        """``(tokens, stamp)`` for one key, refilled up to ``now``. Under the lock."""
        tokens, stamp = self._buckets.get(name, (self.capacity, now))
        if now > stamp:
            tokens = min(
                self.capacity, tokens + (now - stamp) / self.seconds_per_token
            )
            stamp = now
        return tokens, stamp

    def _wait(self, tokens: float) -> int:
        """The whole seconds until this many tokens become one, never below 1."""
        seconds = (1.0 - tokens) * self.seconds_per_token
        # Rounded before the ceiling, so float dust (a bucket that is exactly
        # empty) does not turn a one-second wait into a two-second one.
        return max(1, int(math.ceil(round(seconds, 6))))

    def _sweep(self, now: float) -> None:
        """Called with the lock held: forget the buckets that are full again."""
        for name, (_, stamp) in list(self._buckets.items()):
            if (now - stamp) / self.seconds_per_token >= self.capacity:
                del self._buckets[name]


class Limits:
    """§3.7's three buckets, keyed by principal id.

    ``requests`` counts every request, ``writes`` the ones that change state and
    ``runs`` the ones on a route marked :func:`run_route`. A request is charged
    to every bucket that applies and is refused if *any* of them is empty: the
    three are checked first and spent after, so a refusal never eats a token of
    a bucket that had one.
    """

    def __init__(
        self,
        requests: int = DEFAULT_REQ_PER_MINUTE,
        writes: int = DEFAULT_WRITE_PER_MINUTE,
        runs: int = DEFAULT_RUN_PER_MINUTE,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.requests = TokenBucket(requests, clock=clock)
        self.writes = TokenBucket(writes, clock=clock)
        self.runs = TokenBucket(runs, clock=clock)
        # One lock over the three, so the group of checks and spends is atomic
        # against another thread of this process.
        self._lock = threading.Lock()

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> "Limits":
        """§3.7's limits, with ``AIO_RATE_REQ``/``_WRITE``/``_RUN`` over them.

        An unset (or empty) variable keeps the default. A value that is not a
        positive whole number is refused outright: a limit nobody can read is a
        limit nobody can honour, and guessing 120 for a typo would be worse than
        not starting.
        """
        source = os.environ if env is None else env
        return cls(
            _env_limit(source, ENV_REQ, DEFAULT_REQ_PER_MINUTE),
            _env_limit(source, ENV_WRITE, DEFAULT_WRITE_PER_MINUTE),
            _env_limit(source, ENV_RUN, DEFAULT_RUN_PER_MINUTE),
            clock=clock,
        )

    def check(
        self, principal_id: Any, *, method: str = "GET", run: bool = False
    ) -> int | None:
        """Charge one request to its buckets: ``None`` when it is allowed.

        Otherwise the whole seconds of §3.7's ``Retry-After``, and nothing was
        spent -- the same answer a retry will get until a token comes back.
        """
        buckets = [self.requests]
        if str(method or "").strip().upper() in WRITE_METHODS:
            buckets.append(self.writes)
        if run:
            buckets.append(self.runs)
        with self._lock:
            for bucket in buckets:
                wait = bucket.peek(principal_id)
                if wait is not None:
                    return wait
            for bucket in buckets:
                bucket.take(principal_id)
        return None


def run_route(endpoint: Any) -> Any:
    """Mark one route as a §3.7 ``run``: 10 a minute, not 120.

    ``@v1.post("/runs") @run_route`` (any order, as long as the marked function
    is the one FastAPI is handed -- a wrapper that copies ``__dict__``, as
    :meth:`agentkit.idem.Idempotency.idempotent` does, keeps the mark).
    """
    setattr(endpoint, RUN_MARK, True)
    return endpoint


def is_run_route(request: Any) -> bool:
    """Is the route that matched this request marked a ``run``?"""
    route = request.scope.get("route") if hasattr(request, "scope") else None
    return bool(getattr(getattr(route, "endpoint", None), RUN_MARK, False))


def dependency(
    limits: Limits, app_cfg: auth.AuthConfig
) -> Callable[..., auth.Principal]:
    """§3.7's enforcement point: ``Depends(dependency(limits, cfg))``.

    It depends on §3.1's credential rule itself, so the rule has run first
    whatever the route's parameter order is, and it hands the route the same
    :class:`agentkit.auth.Principal` that rule produced. A bucket that is empty
    raises 429 ``rate_limited`` with ``Retry-After``; the app's §3.3 handlers
    answer it.

    An anonymous caller (a path the app listed as public) is a principal too --
    ``anon`` -- so every anonymous caller on the surface shares one bucket. That
    is the stricter reading of §3.7, which says *per key*: a caller with no key
    is not handed more than a caller with one.
    """
    credential = auth.credential(app_cfg)

    def limit_dependency(
        request: Request,
        principal: auth.Principal = Depends(credential),
    ) -> auth.Principal:
        wait = limits.check(
            principal.id, method=request.method, run=is_run_route(request)
        )
        if wait is not None:
            raise errors.APIError(
                429,
                "rate_limited",
                errors.MESSAGES["rate_limited"],
                retry_after=wait,
            )
        return principal

    return limit_dependency


def _overrides(overrides: Mapping[str, int] | None) -> dict[str, int]:
    if not overrides:
        return {}
    out: dict[str, int] = {}
    for raw_prefix, cap in dict(overrides).items():
        prefix = str(raw_prefix).rstrip("/") or "/"
        out[prefix] = _positive(cap, f"the cap for {prefix!r}")
    return out


def _under(path: str, prefix: str) -> bool:
    """Is ``path`` the prefix or under it? Whole segments only."""
    prefix = str(prefix or "").rstrip("/")
    if not prefix:
        return True
    return path == prefix or path.startswith(prefix + "/")


def _declared(scope: Any) -> int | None:
    """The body's declared length, or ``None``.

    Two ``Content-Length`` headers are a request smuggling question, not a size
    question: the largest of them is the one that could be honoured, so that is
    the one measured. A value that is not a number is no declaration at all --
    the count while reading still decides.
    """
    seen: list[int] = []
    for name, value in scope.get("headers") or ():
        if name.lower() == CONTENT_LENGTH.encode("latin-1"):
            text = value.decode("latin-1").strip()
            if text.isdigit():
                seen.append(int(text))
    return max(seen) if seen else None


async def _refuse(scope: Any, send: Any) -> None:
    """§3.3's 413, written by the middleware: this request never reaches the app."""
    rid = errors.request_id(Request(scope))
    response = errors.envelope(
        413, "too_large", errors.MESSAGES["too_large"], request_id=rid
    )
    await response(scope, _no_receive, send)


async def _no_receive() -> Any:
    """A response needs a receive it never calls."""
    return {"type": "http.disconnect"}


def _positive(value: Any, what: str) -> int:
    """``value`` as a whole number of at least one, or ``ValueError``.

    A string is read the way ``int`` reads it, so ``"2"`` from the environment is
    a two. A fraction is refused rather than truncated: a cap of ``0.5`` turned
    into zero would refuse every request, and a limit of ``120.9`` turned into 120
    would be a limit nobody asked for.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError(f"{what} must be a positive whole number, not {value!r}")
    if isinstance(value, str):
        try:
            number = int(value.strip())
        except ValueError:
            raise ValueError(
                f"{what} must be a positive whole number, not {value!r}"
            ) from None
    elif isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"{what} must be a positive whole number, not {value!r}")
        number = int(value)
    else:
        number = value
    if number < 1:
        raise ValueError(f"{what} must be a positive whole number, not {number}")
    return number


def _env_limit(env: MutableMapping[str, str] | Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name) if hasattr(env, "get") else None
    if raw is None or str(raw).strip() == "":
        return default
    return _positive(str(raw).strip(), name)


def _key(key: Any) -> str:
    """A bucket key: §3.1's principal id, which every caller has."""
    text = str(key).strip() if key is not None else ""
    if not text:
        raise ValueError(
            "a principal id is required: every /v1 caller has one (§3.1)"
        )
    return text
