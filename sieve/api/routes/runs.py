"""`/v1/runs` and `/v1/schedules` — the loop under hand control.

Six routes. Starting a run is a write (`profiles:write`), because a run ships
combos to the gateways of every profile with auto-apply on; reading runs and
schedules is a read, because the status box on every page asks for them.

`POST /v1/runs/{step}` answers **202** with the id of the run it started, and
**409** with the id of the run already going. It never queues: two loops over
one store at the same time is how a half-written inventory gets ranked.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict

from sieve import runs as runs_module
from sieve.api.auth import Token
from sieve.api.auth import require as require_scope
from sieve.api.routes.v1 import Read, config_of, error, store_of

router = APIRouter(prefix="/v1", tags=["runs"])

Write = Annotated[Token, Depends(require_scope("profiles:write"))]


def runner_of(request: Request) -> runs_module.Runner:
    """The app's one runner, built on first use.

    It is held on `app.state` rather than made per request: the in-flight run
    is a row in the store, but the thread that is writing it belongs to the
    process, and a second runner would happily start a second thread.
    """
    runner: runs_module.Runner | None = getattr(request.app.state, "runner", None)
    if runner is None:
        runner = runs_module.Runner(config_of(request), store_of(request), reap_orphans=True)
        request.app.state.runner = runner
    return runner


class ScheduleBody(BaseModel):
    """What `PUT /v1/schedules/{step}` accepts."""

    model_config = ConfigDict(extra="forbid")

    mode: str
    #: minutes past the hour, for `hourly`
    at_minute: int | None = None
    #: HH:MM in the schedule's timezone, for `daily`
    at_time: str | None = None


# --------------------------------------------------------------------------- #
# schedules
# --------------------------------------------------------------------------- #


@router.get("/schedules")
def get_schedules(request: Request, _: Read = None) -> list[dict[str, Any]]:
    """All four rows -- the three steps and `full` -- with `next_fire`."""
    return runs_module.schedules_block(store_of(request))


@router.put("/schedules/{step}")
def put_schedule(
    request: Request,
    step: str,
    body: Annotated[ScheduleBody, Body()],
    token: Write,
) -> Any:
    try:
        changed = runs_module.put_schedule(
            store_of(request),
            step,
            mode=body.mode,
            at_minute=body.at_minute,
            at_time=body.at_time,
        )
    except ValueError as exc:
        return error(400, "bad_schedule", str(exc))
    return changed.json()


# --------------------------------------------------------------------------- #
# runs
# --------------------------------------------------------------------------- #


@router.get("/runs")
def get_runs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=200),
    step: str | None = None,
    _: Read = None,
) -> list[dict[str, Any]]:
    return [r.json() for r in runs_module.recent(store_of(request), limit=limit, step=step)]


@router.get("/runs/{run_id}")
def get_run(request: Request, run_id: str, _: Read = None) -> Any:
    row = runs_module.run_by_id(store_of(request), run_id)
    if row is None:
        return error(404, "not_found", f"no run {run_id!r}")
    return row.json()


@router.get("/runs/{run_id}/log", response_class=PlainTextResponse)
def get_run_log(request: Request, run_id: str, _: Read = None) -> Any:
    row = runs_module.run_by_id(store_of(request), run_id)
    if row is None:
        return error(404, "not_found", f"no run {run_id!r}")
    return PlainTextResponse(runs_module.read_log(row) or "(this run wrote nothing yet)")


@router.post("/runs/{step}")
def post_run(request: Request, step: str, token: Write) -> Any:
    """Start one step now. 202 with the id, or 409 naming the run already going."""
    runner = runner_of(request)
    try:
        started = runner.start(step, token.name)
    except runs_module.RunBusyError as busy:
        return error(
            409,
            "run_in_flight",
            f"a run is already going ({busy.run_id}); this one was not started",
            running=busy.run_id,
        )
    except ValueError as exc:
        return error(404, "no_such_step", str(exc))
    return JSONResponse(status_code=202, content=started.json())
