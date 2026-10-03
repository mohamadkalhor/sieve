"""§3.5's pull job: one source, pulled by a worker instead of by a request.

`POST /v1/sources/{name}/pull` with the kit on answers 202 and queues this
kind. What runs is the route's own pull -- `routes.v1._pull_source`, inside
`store_lock`, exactly as the request ran it -- and the one thing that changes is
where it happens: a worker of the pool, so a source whose upstream takes a
minute is a row to poll rather than a request held open.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from fastapi.responses import JSONResponse

from sieve.api import aio
from sieve.api.routes.v1 import _pull_source
from sieve.storelock import StoreBusy, store_lock

#: The kind's name: what `POST /v1/jobs` queues and what `/v1/jobs/{id}` answers.
PULL = "pull"

#: How this kind is idempotent, declared where §3.5 can refuse a kind that does
#: not say. A second attempt of the same job derives the same snapshot id, so it
#: writes the row the first attempt wrote instead of a second one beside it.
IDEMPOTENT = (
    "the snapshot id is derived from (source, job id), so a second attempt "
    "overwrites its own snapshot rather than making another"
)


def snapshot_id(source: str, job_id: str) -> str:
    """The snapshot a pull job writes: derived from (source, job id).

    Deterministic on purpose. A pull interrupted mid-write is handed back by
    §3.5's shutdown and run again under the same job id, and the second attempt
    has to land on the row the first one started: two snapshots of the same work
    would leave `latest_snapshot` choosing between them.
    """
    return hashlib.sha256(f"{source}\n{job_id}".encode()).hexdigest()[:12]


class _Actor:
    """The `token` `_pull_source` files its decision under: just a name.

    The route hands it its `Token`. A job has no token -- the credential was the
    submitting *call's* -- so the actor comes off the job's own row: the key
    that submitted it, else the owner it was submitted for. Never the job's
    input, which any caller may write.
    """

    def __init__(self, name: str) -> None:
        self.name = name


def actor_of(row: Any) -> str:
    """The name a job's pull is filed under, from the row and nothing else."""
    named = str(getattr(row, "key_id", None) or "").strip()
    if named:
        return named
    owner = str(getattr(row, "owner", None) or "").strip()
    return owner or "job"


def _asked_for(ctx: Any) -> tuple[str, bool]:
    """`(source, force)` out of the job's input, refused if it names nothing."""
    payload = ctx.input if isinstance(ctx.input, dict) else {}
    return str(payload.get("source") or "").strip(), bool(payload.get("force"))


def _pulled(
    cfg: Any,
    store: Any,
    name: str,
    source_cfg: Any,
    actor: Any,
    job_id: str,
    sid: str,
) -> Any:
    """`_pull_source` under the store lock, a busy store being a refusal.

    A waiting writer is not a failure of the work: whatever holds the store is
    doing this same kind of writing, and the job can be submitted again. A
    worker that waited would hold its slot for the whole wait.
    """
    try:
        with store_lock(cfg):
            return _pull_source(
                cfg, store, name, source_cfg, actor, snapshot=sid, job=job_id
            )
    except StoreBusy as busy:
        raise _failed("store_busy", aio.store_busy_message(busy)) from busy


def _failed(code: str, message: str) -> Exception:
    """The kit's `JobFailed`: the job ends `failed` with this code and sentence."""
    return aio._kit().jobs.JobFailed(code, message)


def _refusal(answer: Any) -> Exception:
    """The same refusal one of `/v1`'s helpers answered as a JSONResponse."""
    try:
        named = json.loads(bytes(answer.body).decode("utf-8"))["error"]
        return _failed(str(named.get("code") or "bad_request"), str(named.get("message") or ""))
    except Exception:
        return _failed("bad_request", "the answer to that call could not be read")


def handler(app: Any, jobs: Any) -> Callable[[Any], dict[str, Any]]:
    """The pull as a job: `_pull_source` inside `store_lock`, from a worker."""

    def pull(ctx: Any) -> dict[str, Any]:
        """Pull one source, and answer the route's own body plus its snapshot."""
        cfg = getattr(app.state, "config", None)
        store = getattr(app.state, "store", None)
        if cfg is None or store is None:
            raise _failed("store_busy", "this process is shutting down")
        name, force = _asked_for(ctx)
        source_cfg = cfg.sources.get(name)
        # The route answers 404 and 409 before queuing, so these are the same
        # two refusals for a job submitted through the kit's own `POST /v1/jobs`,
        # which takes any input a caller writes.
        if source_cfg is None:
            raise _failed("not_found", f"no source {name!r}")
        if not source_cfg.enabled and not force:
            raise _failed(
                "source_disabled", f"source {name!r} is disabled; use force=true to pull"
            )
        sid = snapshot_id(name, ctx.job_id)
        row = jobs.get(ctx.job_id)
        out = _pulled(cfg, store, name, source_cfg, _Actor(actor_of(row)), ctx.job_id, sid)
        if isinstance(out, JSONResponse):
            # The pull refused: a source whose plugin has not landed (501). The
            # row carries that same code and sentence rather than a handler error.
            raise _refusal(out)
        out["snapshot"] = sid
        return out

    return pull


def register(app: Any, jobs: Any) -> Any:
    """Declare the pull kind on this app's store, once."""
    run = handler(app, jobs)
    run.__name__ = "pull"
    run.__doc__ = handler.__doc__
    return jobs.register(PULL, run, idempotency=IDEMPOTENT)
