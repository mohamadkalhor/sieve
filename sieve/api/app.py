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
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from sieve import __version__
from sieve.api.routes.v1 import router as v1_router
from sieve.config import Config, default_config, load_config
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
    try:
        yield
    finally:
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

    web = cfg.path(cfg.server.web)
    if web.is_dir():
        app.mount("/", StaticFiles(directory=str(web), html=True), name="web")

    return app


app = create_app()
