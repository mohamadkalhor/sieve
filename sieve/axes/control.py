"""Database-backed axis vocabulary, seeded once from shipped YAML."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sieve.axes.load import check_axis, load_all_axes
from sieve.contracts import Axis, Profile
from sieve.store import Store


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


def seed(store: Store, directory: str | Path) -> None:
    """Import shipped YAML only into a store that has no axes."""
    if store.db.execute("SELECT 1 FROM axes LIMIT 1").fetchone():
        return
    stamp = _stamp()
    with store.tx() as db:
        for axis in load_all_axes(directory):
            db.execute(
                "INSERT INTO axes(name,modality,json,builtin,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (axis.name, axis.modality, axis.model_dump_json(), 1, stamp, stamp),
            )


def axes(store: Store, modality: str | None = None) -> list[Axis]:
    sql: str = "SELECT json FROM axes"
    args: tuple[object, ...] = ()
    if modality is not None:
        sql, args = sql + " WHERE modality=?", (modality,)
    query = store.db.execute(sql + " ORDER BY modality,name", args)
    return [Axis.model_validate_json(r["json"]) for r in query]


def axis(store: Store, name: str, modality: str | None = None) -> Axis | None:
    sql: str = "SELECT json FROM axes WHERE name=?"
    args: tuple[object, ...] = (name,)
    if modality is not None:
        sql, args = sql + " AND modality=?", (name, modality)
    row = store.db.execute(sql + " ORDER BY modality", args).fetchone()
    return Axis.model_validate_json(row["json"]) if row else None


def row(store: Store, name: str, modality: str | None = None) -> dict[str, object] | None:
    held = axis(store, name, modality)
    if held is None:
        return None
    builtin = store.db.execute(
        "SELECT builtin FROM axes WHERE name=? AND modality=?", (name, held.modality)
    ).fetchone()
    return {
        **held.model_dump(mode="json"),
        "fields_count": len(held.fields),
        "profiles": profiles_using(store, name, held.modality),
        "builtin": bool(builtin["builtin"]),
    }


def rows(store: Store, modality: str | None = None) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for value in axes(store, modality):
        item = row(store, value.name, value.modality)
        if item is not None:
            result.append(item)
    return result


def profiles_using(store: Store, name: str, modality: str | None = None) -> list[str]:
    found: list[str] = []
    for dbrow in store.db.execute("SELECT name,json,settings FROM profiles ORDER BY name"):
        profile = Profile.model_validate_json(dbrow["json"])
        if modality is not None and profile.modality != modality:
            continue
        # Settings are authoritative once present, but preserve old profile rows.
        try:
            from sieve.contracts import ProfileSettings

            settings = ProfileSettings.model_validate_json(dbrow["settings"])
            weight = settings.weights.get(name)
            used = bool(weight and weight.value > 0)
        except Exception:
            used = profile.weights.get(name, 0) > 0
        if used:
            found.append(str(dbrow["name"]))
    return found


def validate(value: Axis, store: Store) -> list[str]:
    problems = list(check_axis(value))
    for field in value.fields:
        if field.phase > 1:
            continue
        exists = store.db.execute(
            "SELECT 1 FROM observations WHERE source=? AND field=? LIMIT 1",
            (field.source, field.field),
        ).fetchone()
        if exists is None:
            problems.append(
                f"axis {value.name!r} field {field.source}:{field.field}: no observations exist"
            )
    return problems


def put(store: Store, value: Axis, *, builtin: bool | None = None) -> None:
    stamp = _stamp()
    with store.tx() as db:
        db.execute(
            "INSERT INTO axes(name,modality,json,builtin,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(modality,name) DO UPDATE SET "
            "json=excluded.json,updated_at=excluded.updated_at",
            (value.name, value.modality, value.model_dump_json(), int(bool(builtin)), stamp, stamp),
        )


def delete(
    store: Store, name: str, *, modality: str | None = None, force: bool = False
) -> list[str]:
    held = axis(store, name, modality)
    if held is None:
        return []
    users = profiles_using(store, name, held.modality)
    if users and not force:
        return users
    with store.tx() as db:
        if force:
            from sieve.contracts import ProfileSettings

            for profile_name in users:
                current = db.execute(
                    "SELECT settings,json FROM profiles WHERE name=?", (profile_name,)
                ).fetchone()
                settings = ProfileSettings.model_validate_json(current["settings"])
                if name in settings.weights:
                    settings.weights[name].value = 0
                profile = Profile.model_validate_json(current["json"])
                if name in profile.weights:
                    profile.weights[name] = 0
                db.execute(
                    "UPDATE profiles SET settings=?,json=?,updated_at=? WHERE name=?",
                    (settings.model_dump_json(), profile.model_dump_json(), _stamp(), profile_name),
                )
        db.execute("DELETE FROM axes WHERE name=? AND modality=?", (name, held.modality))
    return []
