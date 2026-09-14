"""Database-backed axis vocabulary, seeded once from shipped YAML.

Axes are the one thing several people share by default. A shipped axis is a
*vocabulary* -- "latency", "price" mean the same to everybody, and a member who
could not see them would have no way to weigh anything. So the seeded rows carry
`owner_id IS NULL` and `visibility='shared'`, and every read falls back to them.

What a person writes is theirs: `put` stamps their `owner_id`, and their copy
shadows the shared one of the same name, which is how somebody redefines
"quality" for their own profiles without touching anybody else's.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sieve.axes.load import check_axis, load_all_axes
from sieve.contracts import Axis, Profile
from sieve.store import Store


def _stamp() -> str:
    return datetime.now(UTC).isoformat()


def _mine(owner_id: str | None) -> tuple[str, tuple[Any, ...]]:
    return "IFNULL(owner_id,'') = IFNULL(?,'')", (owner_id,)


def _visible(owner_id: str | None) -> tuple[str, tuple[Any, ...]]:
    """This owner's axes, plus the shared vocabulary everybody reads."""
    clause, args = _mine(owner_id)
    return f"({clause} OR owner_id IS NULL OR visibility='shared')", args


def _readable(store: Store, owner_id: str | None) -> tuple[str, tuple[Any, ...]]:
    """What this seat may read: the whole box for the gate owner, their own
    axes plus the shared vocabulary for everybody else."""
    if owner_id is not None:
        from sieve import owners

        who = owners.by_id(store, owner_id)
        if who is not None and who.role == "owner":
            return "1=1", ()
    return _visible(owner_id)


def seed(store: Store, directory: str | Path) -> None:
    """Import shipped YAML only into a store that has no builtin axes.

    Seeded unowned and shared on purpose: these are everybody's words.
    """
    if store.db.execute("SELECT 1 FROM axes WHERE builtin=1 LIMIT 1").fetchone():
        return
    stamp = _stamp()
    with store.tx() as db:
        for axis in load_all_axes(directory):
            db.execute(
                "INSERT INTO axes(name,modality,json,builtin,owner_id,visibility,"
                "created_at,updated_at) VALUES(?,?,?,1,NULL,'shared',?,?)"
                " ON CONFLICT(IFNULL(owner_id,''),modality,name) DO NOTHING",
                (axis.name, axis.modality, axis.model_dump_json(), stamp, stamp),
            )


def axes(store: Store, modality: str | None = None, owner_id: str | None = None) -> list[Axis]:
    """Readable axes, one per name: this owner's copy wins over the shared one."""
    clause, args = _readable(store, owner_id)
    sql = f"SELECT json, owner_id FROM axes WHERE {clause}"
    if modality is not None:
        sql, args = sql + " AND modality=?", (*args, modality)
    # Owned rows last, so they overwrite the shared entry of the same key.
    query = store.db.execute(
        sql + " ORDER BY modality, name, (owner_id IS NOT NULL)",
        args,
    )
    held: dict[tuple[str, str], Axis] = {}
    for r in query:
        value = Axis.model_validate_json(r["json"])
        held[(value.modality, value.name)] = value
    return [held[key] for key in sorted(held)]


def axis(
    store: Store, name: str, modality: str | None = None, owner_id: str | None = None
) -> Axis | None:
    clause, args = _readable(store, owner_id)
    sql = f"SELECT json FROM axes WHERE name=? AND {clause}"
    args = (name, *args)
    if modality is not None:
        sql, args = sql + " AND modality=?", (*args, modality)
    row = store.db.execute(sql + " ORDER BY (owner_id IS NULL), modality", args).fetchone()
    return Axis.model_validate_json(row["json"]) if row else None


def row(
    store: Store, name: str, modality: str | None = None, owner_id: str | None = None
) -> dict[str, object] | None:
    held = axis(store, name, modality, owner_id)
    if held is None:
        return None
    clause, args = _readable(store, owner_id)
    meta = store.db.execute(
        f"SELECT builtin, owner_id FROM axes WHERE name=? AND modality=? AND {clause}"
        " ORDER BY (owner_id IS NULL)",
        (name, held.modality, *args),
    ).fetchone()
    return {
        **held.model_dump(mode="json"),
        "fields_count": len(held.fields),
        "profiles": profiles_using(store, name, held.modality, owner_id),
        "builtin": bool(meta["builtin"]) if meta else False,
        # Shown so a page can say "shipped" rather than "yours" without a
        # second call; NULL means the shared vocabulary, not a missing owner.
        "owner_id": meta["owner_id"] if meta else None,
    }


def rows(
    store: Store, modality: str | None = None, owner_id: str | None = None
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for value in axes(store, modality, owner_id):
        item = row(store, value.name, value.modality, owner_id)
        if item is not None:
            result.append(item)
    return result


def profiles_using(
    store: Store, name: str, modality: str | None = None, owner_id: str | None = None
) -> list[str]:
    """Which of *this owner's* profiles weigh this axis.

    Scoped: whether somebody else leans on an axis is not an answer the person
    asking is entitled to, and it is not what they meant by the question.
    """
    found: list[str] = []
    clause, args = _mine(owner_id)
    for dbrow in store.db.execute(
        f"SELECT name,json,settings FROM profiles WHERE {clause} ORDER BY name", args
    ):
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


def put(
    store: Store, value: Axis, *, builtin: bool | None = None, owner_id: str | None = None
) -> None:
    """Write an axis into this owner's vocabulary.

    A person editing a shipped axis gets their own copy, shadowing the shared
    one; the original stays intact for everybody else.
    """
    stamp = _stamp()
    visibility = "shared" if owner_id is None else "private"
    with store.tx() as db:
        db.execute(
            "INSERT INTO axes(name,modality,json,builtin,owner_id,visibility,"
            "created_at,updated_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(IFNULL(owner_id,''),modality,name) DO UPDATE SET "
            "json=excluded.json,updated_at=excluded.updated_at",
            (
                value.name,
                value.modality,
                value.model_dump_json(),
                int(bool(builtin)),
                owner_id,
                visibility,
                stamp,
                stamp,
            ),
        )


def delete(
    store: Store,
    name: str,
    *,
    modality: str | None = None,
    force: bool = False,
    owner_id: str | None = None,
) -> list[str]:
    """Drop this owner's axis. Returns the profiles blocking it, if any.

    Only their own row is ever removed: deleting the shared "latency" because
    one person stopped weighing it would take it from everybody.
    """
    held = axis(store, name, modality, owner_id)
    if held is None:
        return []
    using = profiles_using(store, name, held.modality, owner_id)
    if using and not force:
        return using
    clause, args = _mine(owner_id)
    with store.tx() as db:
        if force:
            from sieve.contracts import ProfileSettings

            for profile_name in using:
                current = db.execute(
                    f"SELECT settings,json FROM profiles WHERE name=? AND {clause}",
                    (profile_name, *args),
                ).fetchone()
                settings = ProfileSettings.model_validate_json(current["settings"])
                if name in settings.weights:
                    settings.weights[name].value = 0
                profile = Profile.model_validate_json(current["json"])
                if name in profile.weights:
                    profile.weights[name] = 0
                db.execute(
                    f"UPDATE profiles SET settings=?,json=?,updated_at=? WHERE name=? AND {clause}",
                    (
                        settings.model_dump_json(),
                        profile.model_dump_json(),
                        _stamp(),
                        profile_name,
                        *args,
                    ),
                )
        db.execute(
            f"DELETE FROM axes WHERE name=? AND modality=? AND {clause}",
            (name, held.modality, *args),
        )
    return []
