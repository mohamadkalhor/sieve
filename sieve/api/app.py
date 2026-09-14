"""The FastAPI application.

Read-only without a token; writes need the scope named in CONTRACTS section 6.
Errors are always `{"error": {"code": ..., "message": ...}}`.
"""

from __future__ import annotations

import os
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

from sieve import __version__
from sieve.api.routes.config import router as config_router
from sieve.api.routes.connectors import router as connectors_router
from sieve.api.routes.identity import router as identity_router
from sieve.api.routes.runs import router as runs_router
from sieve.api.routes.v1 import router as v1_router
from sieve.axes import control as axis_control
from sieve.config import Config, default_config, load_config
from sieve.connectors import seed_from_toml
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
    profile_control.seed(app.state.store, cfg.profiles_dir)
    axis_control.seed(app.state.store, cfg.axes_dir)
    # A box configured in TOML migrates itself: the `[inventories.*]` and
    # `[targets.*]` gateway blocks become connectors on the first start after
    # this landed, rather than waiting for somebody to POST their own gateway.
    seed_from_toml(cfg, app.state.store)
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
