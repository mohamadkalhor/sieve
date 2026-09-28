"""Whole-configuration API and the operating guide."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse

from sieve.api.auth import Token, owner_of_request
from sieve.api.v1_models import ConfigExport
from sieve.api.auth import require as require_scope
from sieve.api.routes.v1 import Read, config_of, error, store_of
from sieve.axes import control as axis_control
from sieve.config_bundle import (
    ConfigBundle,
    apply_config,
    desired_config,
    diff,
    export_config,
    touches_connectors,
    validate_config,
)
from sieve.connectors import seed_from_toml
from sieve.pinned import AllowedHosts
from sieve.profiles import control
from sieve.secrets import EnvironmentNameError, Secrets

router = APIRouter(prefix="/v1", tags=["config"])


def _indexed(document: dict[str, Any]) -> dict[str, Any]:
    result = dict(document)
    result.pop("exported_at", None)
    result["profiles"] = {item["name"]: item for item in document["profiles"]}
    result["axes"] = {f"{item['modality']}:{item['name']}": item for item in document["axes"]}
    result["connectors"] = {item["name"]: item for item in document["connectors"]}
    return result


def _ready(request: Request) -> tuple[Any, Any, str | None]:
    cfg, store = config_of(request), store_of(request)
    owner_id = owner_of_request(request)
    control.seed(store, cfg.profiles_dir, owner_id)
    axis_control.seed(store, cfg.axes_dir)
    seed_from_toml(cfg, store, owner_id)
    return cfg, store, owner_id


@router.get("/config", response_model=ConfigExport)
def get_config(request: Request, _: Read = None) -> dict[str, Any]:
    _, store, owner_id = _ready(request)
    return export_config(store, owner_id)


def _unresolvable(bundle: ConfigBundle, secrets: Secrets) -> str | None:
    """The first connector in a bundle whose secret id this box cannot resolve.

    A bundle can arrive from a box with a `[secrets]` table this one does not
    have -- the ids travel and the table does not -- so an id that does not
    resolve here is refused on the way in, rather than stored as a connector
    that will quietly send nothing.
    """
    for connector in bundle.connectors:
        for field, secret_id in (
            ("secret", connector.secret),
            ("admin_secret", connector.admin_secret),
        ):
            reason = secrets.problem(secret_id, connector.kind)
            if reason:
                return f"connector {connector.name}: {field}: {reason}"
    return None


def _unreachable(bundle: ConfigBundle, hosts: AllowedHosts) -> str | None:
    """The first connector in a bundle whose host this box may not call.

    A bundle travels between boxes and carries whatever `base_url` it was built
    with; `[connectors.hosts]` is local, and the list here is not the list
    there. A host this box did not name is refused on the way in with the same
    422 the connector routes answer, so the two doors into the same table agree.
    """
    for connector in bundle.connectors:
        reason = hosts.problem(connector.kind, connector.base_url)
        if reason:
            return f"connector {connector.name}: {reason}"
    return None


@router.put("/config")
def put_config(
    request: Request,
    body: dict[str, Any],
    token: Annotated[Token, Depends(require_scope("profiles:write"))],
    dry_run: bool = False,
    prune: bool = False,
) -> Any:
    cfg, store, owner_id = _ready(request)
    before = export_config(store, owner_id)
    try:
        incoming = validate_config(body, store, set(cfg.sources), owner_id)
        target = desired_config(before, incoming, prune)
    except EnvironmentNameError as exc:
        # A body that names an environment variable is a connector problem
        # (422), not an unreadable document (400): the document reads fine, and
        # what is wrong with it is the credential it points at.
        return error(422, "bad_connector", str(exc))
    except ValueError as exc:
        return error(400, "bad_config", str(exc))
    unreachable = _unreachable(incoming, cfg.allowed_hosts)
    if unreachable:
        return error(422, "host_not_allowed", unreachable)
    refused = _unresolvable(incoming, cfg.secret_registry)
    if refused:
        return error(422, "bad_connector", refused)
    changes = diff(_indexed(before), _indexed(target))
    if touches_connectors(changes) and not token.allows("admin"):
        # Refused as a whole, not connector by connector: the rest of the
        # document was accepted on the strength of this write, and half of it
        # landing while the connectors silently did not would be worse than
        # none of it landing.
        return error(403, "not_allowed", "changing connectors needs the admin scope")
    if dry_run:
        return changes
    target["_prune"] = prune
    apply_config(store, target, changes, token.name, owner_id)
    return {"diff": changes, "applied": len(changes)}


@router.get("/guide", response_class=PlainTextResponse)
def get_guide(_: Read = None) -> PlainTextResponse:
    path = Path(__file__).resolve().parents[3] / "OPERATING.md"
    return PlainTextResponse(path.read_text(), media_type="text/markdown")
