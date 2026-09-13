"""Whole-configuration API and the operating guide."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse

from sieve.api.auth import Token
from sieve.api.auth import require as require_scope
from sieve.api.routes.v1 import Read, config_of, error, store_of
from sieve.axes import control as axis_control
from sieve.config_bundle import apply_config, desired_config, diff, export_config, validate_config
from sieve.connectors import seed_from_toml
from sieve.profiles import control

router = APIRouter(prefix="/v1", tags=["config"])


def _indexed(document: dict[str, Any]) -> dict[str, Any]:
    result = dict(document)
    result.pop("exported_at", None)
    result["profiles"] = {item["name"]: item for item in document["profiles"]}
    result["axes"] = {f"{item['modality']}:{item['name']}": item for item in document["axes"]}
    result["connectors"] = {item["name"]: item for item in document["connectors"]}
    return result


def _ready(request: Request) -> tuple[Any, Any]:
    cfg, store = config_of(request), store_of(request)
    control.seed(store, cfg.profiles_dir)
    axis_control.seed(store, cfg.axes_dir)
    seed_from_toml(cfg, store)
    return cfg, store


@router.get("/config")
def get_config(request: Request, _: Read = None) -> dict[str, Any]:
    _, store = _ready(request)
    return export_config(store)


@router.put("/config")
def put_config(
    request: Request,
    body: dict[str, Any],
    token: Annotated[Token, Depends(require_scope("profiles:write"))],
    dry_run: bool = False,
    prune: bool = False,
) -> Any:
    cfg, store = _ready(request)
    before = export_config(store)
    try:
        incoming = validate_config(body, store, set(cfg.sources))
        target = desired_config(before, incoming, prune)
    except ValueError as exc:
        return error(400, "bad_config", str(exc))
    changes = diff(_indexed(before), _indexed(target))
    if dry_run:
        return changes
    target["_prune"] = prune
    apply_config(store, target, changes, token.name)
    return {"diff": changes, "applied": len(changes)}


@router.get("/guide", response_class=PlainTextResponse)
def get_guide(_: Read = None) -> PlainTextResponse:
    path = Path(__file__).resolve().parents[3] / "OPERATING.md"
    return PlainTextResponse(path.read_text(), media_type="text/markdown")
