"""The FastAPI application.

Read-only without a token; writes need the scope named in CONTRACTS section 6.
Errors are always `{"error": {"code": ..., "message": ...}}`.
"""

from __future__ import annotations

import os
import sqlite3
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from sieve import __version__, owners
from sieve.api.routes.config import router as config_router
from sieve.api.routes.connectors import router as connectors_router
from sieve.api.routes.identity import router as identity_router
from sieve.api.routes.runs import router as runs_router
from sieve.api.routes.v1 import router as v1_router
from sieve.axes import control as axis_control
from sieve.config import Config, default_config, load_config
from sieve.connectors import seed_from_toml
from sieve.engine import RankingBusyError
from sieve.profiles import control as profile_control
from sieve.runs import Runner, Scheduler
from sieve.store import Store

CONFIG_ENV = "SIEVE_CONFIG"


def build_config() -> Config:
    path = Path(os.environ.get(CONFIG_ENV, "sieve.toml"))
    return load_config(path) if path.exists() else default_config()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg: Config = getattr(app.state, "config", None) or build_config()
    app.state.config = cfg
    app.state.store = Store(cfg.db_path)
    # Whose box this is, before anything is seeded into it.
    boss = owners.ensure_owner(app.state.store)
    owner_id = boss.id if boss else None
    profile_control.seed(app.state.store, cfg.profiles_dir, owner_id)
    axis_control.seed(app.state.store, cfg.axes_dir)
    # A box configured in TOML migrates itself: the `[inventories.*]` and
    # `[targets.*]` gateway blocks become connectors on the first start after
    # this landed, rather than waiting for somebody to POST their own gateway.
    seed_from_toml(cfg, app.state.store, owner_id)
    # The loop runs here now, not in a systemd timer nobody can edit from a
    # phone: one runner (a run is a background thread in this process), one
    # scheduler thread that fires the due steps through the same path `Run now`
    # takes. `reap_orphans` closes out a run a restart killed, which otherwise
    # leaves the status box saying "running" for ever.
    app.state.runner = Runner(cfg, app.state.store, reap_orphans=True)
    app.state.scheduler = Scheduler(app.state.runner)
    app.state.scheduler.start()
    try:
        yield
    finally:
        app.state.scheduler.stop()
        app.state.store.close()


def create_app(config: Config | None = None) -> FastAPI:
    app = FastAPI(
        title="Sieve",
        version=__version__,
        summary="Weighted model selection for agent fleets",
        lifespan=lifespan,
    )
    if config is not None:
        app.state.config = config
    cfg = config or build_config()

    if cfg.server.cors:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cfg.server.cors,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail: Any = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": f"http_{exc.status_code}", "message": str(detail)}},
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "invalid_body",
                    "message": "the payload does not match the contract",
                    "detail": exc.errors(),
                }
            },
        )

    def envelope(status: int, code: str, message: str, **extra: Any) -> JSONResponse:
        body: dict[str, Any] = {"error": {"code": code, "message": message, **extra}}
        headers = {"Retry-After": str(extra["retry_after"])} if "retry_after" in extra else None
        return JSONResponse(status_code=status, content=body, headers=headers)

    def is_locked(exc: BaseException) -> bool:
        return isinstance(exc, sqlite3.OperationalError) and "database is locked" in str(exc)

    @app.exception_handler(sqlite3.OperationalError)
    async def sqlite_error(_: Request, exc: sqlite3.OperationalError) -> JSONResponse:
        """A locked database is a queue, not a fault.

        SQLite serialises writers; a busy_timeout that runs out means the box
        is writing something long, and the honest answer is "come back", not a
        500. Anything else SQLite raises is a real fault and says so.
        """
        if not is_locked(exc):
            traceback.print_exception(exc)
            return envelope(500, "store_error", "the store could not answer that")
        return envelope(
            503,
            "database_locked",
            "the store is busy with another write; try again in a moment",
            retry_after=2,
        )

    @app.exception_handler(RankingBusyError)
    async def ranking_busy(_: Request, exc: RankingBusyError) -> JSONResponse:
        return envelope(503, "ranking_busy", str(exc), retry_after=10)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        """Every 500 in the envelope, whatever raised it.

        Without this, Starlette re-raises and uvicorn writes a bare
        `Internal Server Error` in text/plain, which a client parsing JSON
        reads as a broken server rather than a failed call. The traceback goes
        to the log, where it belongs; the caller gets a code and a sentence.
        """
        if is_locked(exc):
            return await sqlite_error(_, exc)  # type: ignore[arg-type]
        traceback.print_exception(exc)
        return envelope(500, "internal_error", "something went wrong; the log has the detail")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    app.include_router(v1_router)
    app.include_router(identity_router)
    app.include_router(connectors_router)
    app.include_router(config_router)
    app.include_router(runs_router)

    # Anything under /v1 that no route claims is an API call that went wrong,
    # and it has to say so in JSON. The SPA catch-all below would hand it the
    # app shell with a 200, which a client reads as "the endpoint exists and
    # answered nonsense" -- the web app spent a deploy showing "API not
    # available yet" because of exactly that.
    @app.api_route(
        "/v1/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        include_in_schema=False,
    )
    async def v1_not_found(path: str) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "not_found", "message": f"no /v1/{path} endpoint"}},
        )

    web = cfg.path(cfg.server.web)
    if web.is_dir():
        index = web / "index.html"

        # The web app is a static SPA: the client owns routing, so every path
        # the API does not claim has to serve the shell. Without this a deep
        # link like /rankings/coder -- the link a person actually shares -- is
        # a 404 from StaticFiles.
        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> Response:
            root = web.resolve()
            candidate = (web / path).resolve()
            if path and candidate.is_file() and candidate.is_relative_to(root):
                return FileResponse(candidate)

            # A prerendered route is `<path>.html` on disk. Without this the
            # request fell through to the empty SPA shell and the build-time
            # HTML -- the whole point of prerendering -- was never served, so
            # nothing painted until the JS bundle had run.
            if path:
                page = (web / f"{path.rstrip('/')}.html").resolve()
                if page.is_file() and page.is_relative_to(root):
                    return FileResponse(page)

            if index.is_file():
                return FileResponse(index)
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "no_web_build", "message": "run pnpm build in web/"}},
            )

        app.mount("/", StaticFiles(directory=str(web)), name="web")

    return app


app = create_app()
