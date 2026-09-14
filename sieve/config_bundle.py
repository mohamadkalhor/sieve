"""Export, validate, diff and atomically import Sieve's agent-facing configuration."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sieve.axes import control as axis_control
from sieve.contracts import Axis, Connector, Modality, Profile, ProfileSettings
from sieve.profiles import control
from sieve.store import Store

VERSION = 1
_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.I)
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProfileBundle(StrictModel):
    name: str
    modality: Modality
    purpose: str
    settings: ProfileSettings
    model_status: dict[str, dict[str, Any]] = Field(default_factory=dict)
    auto_apply: bool = False


class ConnectorBundle(StrictModel):
    name: str
    kind: str
    base_url: str
    token_env: str | None = None
    read: bool = True
    write: bool = False
    poll_minutes: int = Field(default=60, ge=1)


class ConfigBundle(StrictModel):
    version: int
    exported_at: datetime
    profiles: list[ProfileBundle] = Field(default_factory=list)
    axes: list[Axis] = Field(default_factory=list)
    cost_multipliers: dict[str, float] = Field(default_factory=dict)
    connectors: list[ConnectorBundle] = Field(default_factory=list)


def export_config(store: Store, owner_id: str | None = None) -> dict[str, Any]:
    """One owner's configuration, and nothing anybody else made.

    Unscoped, this was the widest leak in the API: a single GET handed back
    every person's profiles, their connectors' base URLs and the names of the
    environment variables holding their tokens.
    """
    profiles: list[dict[str, Any]] = []
    for profile in control.profiles(store, owner_id, shared=False):
        settings = control.settings(store, profile.name, owner_id) or control.default_settings(
            profile
        )
        statuses = {
            model_id: {"status": status, "pin_order": pin_order}
            for model_id, (status, pin_order) in sorted(
                control.statuses(store, profile.name, owner_id).items()
            )
        }
        profiles.append(
            {
                "name": profile.name,
                "modality": profile.modality,
                "purpose": profile.purpose,
                "settings": settings.model_dump(mode="json"),
                "model_status": statuses,
                "auto_apply": settings.auto_apply,
            }
        )
    connectors = [
        {
            "name": item.name,
            "kind": item.kind,
            "base_url": item.base_url,
            "token_env": item.token_env,
            "read": item.read,
            "write": item.write,
            "poll_minutes": item.poll_minutes,
        }
        for item in store.connectors(owner_id)
    ]
    return {
        "version": VERSION,
        "exported_at": datetime.now(UTC).isoformat(),
        "profiles": profiles,
        "axes": [item.model_dump(mode="json") for item in axis_control.axes(store, None, owner_id)],
        "cost_multipliers": control.multipliers(store, owner_id),
        "connectors": connectors,
    }


def _validate_statuses(profile: ProfileBundle) -> None:
    for model_id, value in profile.model_status.items():
        if set(value) - {"status", "pin_order"}:
            raise ValueError(f"profile {profile.name} model {model_id}: unknown status field")
        if value.get("status") not in {"active", "pinned", "removed"}:
            raise ValueError(f"profile {profile.name} model {model_id}: bad status")
        order = value.get("pin_order")
        if order is not None and (not isinstance(order, int) or order < 1):
            raise ValueError(f"profile {profile.name} model {model_id}: bad pin_order")


def validate_config(
    raw: dict[str, Any], store: Store, source_names: set[str], owner_id: str | None = None
) -> ConfigBundle:
    try:
        bundle = ConfigBundle.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc
    if bundle.version != VERSION:
        raise ValueError(f"unsupported config version {bundle.version}")
    if len({p.name for p in bundle.profiles}) != len(bundle.profiles):
        raise ValueError("duplicate profile name")
    if len({(a.modality, a.name) for a in bundle.axes}) != len(bundle.axes):
        raise ValueError("duplicate axis")
    if len({c.name for c in bundle.connectors}) != len(bundle.connectors):
        raise ValueError("duplicate connector name")

    proposed_axes = {(axis.modality, axis.name) for axis in bundle.axes}
    held_axes = {
        (axis.modality, axis.name): axis for axis in axis_control.axes(store, None, owner_id)
    }
    current_axes = set(held_axes)
    for axis in bundle.axes:
        if not _NAME.match(axis.name):
            raise ValueError(f"bad axis name {axis.name!r}")
        # Existing exported axes have already passed validation. Validate fields
        # against configured sources and observations when an axis is new or changed.
        if held_axes.get((axis.modality, axis.name)) != axis:
            for field in axis.fields:
                if field.source not in source_names and field.source != "price":
                    raise ValueError(f"axis {axis.name}: unknown source {field.source!r}")
            problems = axis_control.validate(axis, store)
            if problems:
                raise ValueError("; ".join(problems))
    known_axes = proposed_axes | current_axes
    for profile in bundle.profiles:
        if not _NAME.match(profile.name):
            raise ValueError(f"bad profile name {profile.name!r}")
        _validate_statuses(profile)
        if profile.auto_apply != profile.settings.auto_apply:
            raise ValueError(f"profile {profile.name}: auto_apply must match settings.auto_apply")
        for name, weight in profile.settings.weights.items():
            if (profile.modality, name) not in known_axes:
                raise ValueError(f"profile {profile.name}: unknown axis {name!r}")
            if not weight.min <= weight.value <= weight.max:
                raise ValueError(f"profile {profile.name}: weight {name} outside [min,max]")
    for prefix, value in bundle.cost_multipliers.items():
        if value < 0:
            raise ValueError(f"multiplier {prefix!r} must be non-negative")
    for connector in bundle.connectors:
        if not _NAME.match(connector.name):
            raise ValueError(f"bad connector name {connector.name!r}")
        parsed = urlparse(connector.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"connector {connector.name}: base_url must be an http(s) URL")
        if connector.kind not in {"openai_compat", "ninerouter"}:
            raise ValueError(f"unknown connector kind {connector.kind!r}")
        if connector.token_env and not _ENV_NAME.match(connector.token_env):
            raise ValueError(
                f"connector {connector.name}: token_env must name an environment variable"
            )
    return bundle


def diff(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(set(before) | set(after)):
            child = f"{path}/{key}" if path else str(key)
            if key not in before:
                changes.append({"path": child, "before": None, "after": after[key]})
            elif key not in after:
                changes.append({"path": child, "before": before[key], "after": None})
            else:
                changes.extend(diff(before[key], after[key], child))
        return changes
    if before != after:
        return [{"path": path, "before": before, "after": after}]
    return []


def desired_config(current: dict[str, Any], incoming: ConfigBundle, prune: bool) -> dict[str, Any]:
    result = json.loads(json.dumps(current))
    result["version"] = incoming.version
    result["exported_at"] = incoming.exported_at.isoformat().replace("+00:00", "Z")
    for key, identity in (
        ("profiles", "name"),
        ("axes", ("modality", "name")),
        ("connectors", "name"),
    ):
        supplied = [item.model_dump(mode="json") for item in getattr(incoming, key)]
        old = result[key]

        def ident(item: dict[str, Any], field: Any = identity) -> Any:
            return tuple(item[x] for x in field) if isinstance(field, tuple) else item[field]

        merged = {} if prune else {ident(item): item for item in old}
        merged.update({ident(item): item for item in supplied})
        result[key] = [merged[name] for name in sorted(merged)]
    result["cost_multipliers"] = (
        incoming.cost_multipliers
        if prune
        else {**result["cost_multipliers"], **incoming.cost_multipliers}
    )
    return dict(result)


def apply_config(
    store: Store,
    target: dict[str, Any],
    changes: list[dict[str, Any]],
    actor: str,
    owner_id: str | None = None,
) -> None:
    """Write a bundle back, inside one owner's half of the store.

    Every statement here is scoped, and the pruning ones especially: an import
    that deleted "the profiles not in this bundle" would have deleted everybody
    else's, because a bundle only ever describes what its owner can see.
    """
    stamp = datetime.now(UTC).isoformat()
    current_profiles = {p.name: p for p in control.profiles(store, owner_id, shared=False)}
    current_connectors = {c.name: c for c in store.connectors(owner_id)}
    owned = "IFNULL(owner_id,'') = IFNULL(?,'')"
    with store.tx() as db:
        wanted_profiles: set[str] = set()
        for item in target["profiles"]:
            wanted_profiles.add(item["name"])
            old_profile = current_profiles.get(item["name"])
            profile = (
                old_profile
                or Profile(
                    name=item["name"],
                    modality=item["modality"],
                    purpose=item["purpose"],
                    weights={},
                )
            ).model_copy(
                update={
                    "modality": item["modality"],
                    "purpose": item["purpose"],
                    "weights": {k: v["value"] for k, v in item["settings"]["weights"].items()},
                }
            )
            db.execute(
                "INSERT INTO profiles(name,modality,json,settings,owner_id,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?) ON CONFLICT(IFNULL(owner_id,''),name) DO UPDATE SET "
                "modality=excluded.modality,json=excluded.json,"
                "settings=excluded.settings,updated_at=excluded.updated_at",
                (
                    profile.name,
                    profile.modality,
                    profile.model_dump_json(),
                    json.dumps(item["settings"]),
                    owner_id,
                    stamp,
                    stamp,
                ),
            )
            db.execute(
                f"DELETE FROM profile_models WHERE profile=? AND {owned}",
                (profile.name, owner_id),
            )
            for model_id, state in item["model_status"].items():
                db.execute(
                    "INSERT INTO profile_models(profile,model_id,status,pin_order,owner_id)"
                    " VALUES(?,?,?,?,?)",
                    (profile.name, model_id, state["status"], state.get("pin_order"), owner_id),
                )
        if target.get("_prune"):
            db.executemany(
                f"DELETE FROM profiles WHERE name=? AND {owned}",
                [(name, owner_id) for name in set(current_profiles) - wanted_profiles],
            )

        wanted_axes: set[tuple[str, str]] = set()
        for item in target["axes"]:
            axis = Axis.model_validate(item)
            wanted_axes.add((axis.modality, axis.name))
            builtin = db.execute(
                f"SELECT builtin FROM axes WHERE modality=? AND name=? AND {owned}",
                (axis.modality, axis.name, owner_id),
            ).fetchone()
            db.execute(
                "INSERT INTO axes(name,modality,json,builtin,owner_id,visibility,"
                "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(IFNULL(owner_id,''),modality,name) DO UPDATE SET "
                "json=excluded.json,updated_at=excluded.updated_at",
                (
                    axis.name,
                    axis.modality,
                    axis.model_dump_json(),
                    builtin["builtin"] if builtin else 0,
                    owner_id,
                    "shared" if owner_id is None else "private",
                    stamp,
                    stamp,
                ),
            )
        if target.get("_prune"):
            for row in db.execute(
                f"SELECT modality,name FROM axes WHERE {owned}", (owner_id,)
            ).fetchall():
                if (row["modality"], row["name"]) not in wanted_axes:
                    db.execute(
                        f"DELETE FROM axes WHERE modality=? AND name=? AND {owned}",
                        (row["modality"], row["name"], owner_id),
                    )

        if target.get("_prune"):
            db.execute(f"DELETE FROM cost_multipliers WHERE {owned}", (owner_id,))
        for prefix, value in target["cost_multipliers"].items():
            db.execute(
                "INSERT INTO cost_multipliers(prefix,multiplier,owner_id) VALUES(?,?,?) "
                "ON CONFLICT(IFNULL(owner_id,''),prefix) "
                "DO UPDATE SET multiplier=excluded.multiplier",
                (prefix, value, owner_id),
            )

        wanted_connectors: set[str] = set()
        for item in target["connectors"]:
            wanted_connectors.add(item["name"])
            old_connector = current_connectors.get(item["name"])
            if old_connector:
                connector = old_connector.model_copy(update=item)
            else:
                connector = Connector(
                    id=uuid.uuid4().hex[:12],
                    created_at=datetime.now(UTC),
                    owner_id=owner_id,
                    **item,
                )
            db.execute(
                "INSERT OR REPLACE INTO connectors(id,name,kind,base_url,token_env,read,write,"
                "poll_minutes,last_pull_at,last_push_at,last_error,options,created_at,owner_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                Store._connector_row(connector),
            )
        if target.get("_prune"):
            db.executemany(
                f"DELETE FROM connectors WHERE name=? AND {owned}",
                [(name, owner_id) for name in set(current_connectors) - wanted_connectors],
            )

        for change in changes:
            parts = change["path"].split("/")
            profile = parts[1] if parts[0] == "profiles" and len(parts) > 1 else parts[0]
            kind = "weights" if "weights" in parts or parts[0] == "axes" else "policy"
            db.execute(
                "INSERT INTO decisions(id,at,profile,kind,actor,before,after,reason,detail) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex[:12],
                    stamp,
                    profile,
                    kind,
                    actor,
                    json.dumps(change["before"]) if change["before"] is not None else None,
                    json.dumps(change["after"]) if change["after"] is not None else None,
                    f"config import changed {change['path']}",
                    "{}",
                ),
            )
