"""Database-backed profiles: weights, and how many models to ship."""
# ruff: noqa: E501

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from sieve.contracts import Capability, Outcome, Profile, ProfileSettings, Ranking
from sieve.profiles.legacy import clean_profile, clean_settings
from sieve.profiles.load import load_profiles
from sieve.store import Store


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


#: the fields a profile and its settings share; the settings are the truth
TUNED = tuple(ProfileSettings.model_fields)


def settings_of(profile: Profile) -> ProfileSettings:
    return ProfileSettings.model_validate({k: getattr(profile, k) for k in TUNED})


def tuned(profile: Profile, value: ProfileSettings) -> Profile:
    """The profile, carrying these settings."""
    return profile.model_copy(update={k: getattr(value, k) for k in TUNED})


def default_settings(profile: Profile) -> ProfileSettings:
    return settings_of(profile)


def mine(owner_id: str | None, column: str = "owner_id") -> tuple[str, tuple[Any, ...]]:
    """`WHERE` fragment matching exactly this owner's rows, NULL included.

    `owner_id = ?` never matches NULL, and NULL is what an unowned row carries
    on a box where nobody has signed in yet -- which is every box running only
    on `SIEVE_TOKENS`. `IFNULL` on both sides is the one comparison that means
    "mine" for a person and for nobody alike.
    """
    return f"IFNULL({column},'') = IFNULL(?,'')", (owner_id,)


def visible(owner_id: str | None, column: str = "owner_id") -> tuple[str, tuple[Any, ...]]:
    """`WHERE` fragment for what this owner may read: their own, plus shared."""
    clause, args = mine(owner_id, column)
    return f"({clause} OR visibility='shared')", args


def readable(store: Store, owner_id: str | None) -> tuple[str, tuple[Any, ...]]:
    """`WHERE` fragment for what this seat may read.

    The gate owner reads the whole box -- it is his machine, he is the one who
    answers for what runs on it, and a support question about a member's seat
    is otherwise unanswerable. Everybody else reads their own rows plus what is
    shared, which is the rule for every other seat in this file.
    """
    if owner_id is not None:
        from sieve import owners

        who = owners.by_id(store, owner_id)
        if who is not None and who.role == "owner":
            return "1=1", ()
    return visible(owner_id)


def seed(store: Store, directory: Any, owner_id: str | None = None) -> None:
    """Import the shipped profiles once, into a store that holds none of theirs."""
    clause, args = mine(owner_id)
    if store.db.execute(f"SELECT 1 FROM profiles WHERE {clause} LIMIT 1", args).fetchone():
        return
    for profile in load_profiles(directory):
        put_profile(store, profile, owner_id=owner_id)


def validate_weights(weights: dict[str, float], axes: set[str]) -> None:
    """The same weight rules for every profile write door.

    Each weight is a share of the score and the shares add to one. The page
    keeps them there by renormalising the others whenever one moves, so a sum
    that has drifted is a bug in a caller, not a thing to quietly fix here.
    """
    if not weights:
        raise ValueError("a profile is its weights; it needs at least one axis")
    for axis, value in weights.items():
        if axis not in axes:
            raise ValueError(f"unknown weight axis {axis!r} for this modality")
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"weight {axis!r} must be in [0,1]")
    total = sum(weights.values())
    if abs(total - 1.0) > 0.001:
        raise ValueError(f"weights must sum to 1 +/- 0.001, got {total:.4f}")


def read_profile(raw: str | dict[str, Any]) -> Profile:
    """One stored profile document, however old the shape it was written in."""
    body = json.loads(raw) if isinstance(raw, str) else dict(raw)
    cleaned, _ = clean_profile(body)
    return Profile.model_validate(cleaned)


def read_settings(raw: str | dict[str, Any]) -> ProfileSettings:
    """One stored settings document, however old the shape it was written in."""
    body = json.loads(raw) if isinstance(raw, str) else dict(raw)
    cleaned, _ = clean_settings(body)
    return ProfileSettings.model_validate(cleaned)


def _effective_profile(row: Any) -> Profile:
    """The profile as it is tuned: the document, resolved through its settings."""
    value = read_profile(row["json"])
    return tuned(value, read_settings(row["settings"]))


def profiles(store: Store, owner_id: str | None = None, shared: bool = True) -> list[Profile]:
    clause, args = readable(store, owner_id) if shared else mine(owner_id)
    return [
        _effective_profile(r)
        for r in store.db.execute(
            f"SELECT json,settings FROM profiles WHERE {clause} ORDER BY name", args
        )
    ]


def _row(store: Store, name: str, owner_id: str | None, shared: bool = True) -> Any:
    clause, args = readable(store, owner_id) if shared else mine(owner_id)
    return store.db.execute(
        f"SELECT * FROM profiles WHERE name=? AND {clause}", (name, *args)
    ).fetchone()


def profile(store: Store, name: str, owner_id: str | None = None) -> Profile | None:
    row = _row(store, name, owner_id)
    return _effective_profile(row) if row else None


def owner_of(store: Store, name: str, owner_id: str | None = None) -> str | None:
    """Whose profile this is, as far as `owner_id` can see it."""
    row = _row(store, name, owner_id)
    return row["owner_id"] if row else None


def settings(store: Store, name: str, owner_id: str | None = None) -> ProfileSettings | None:
    row = _row(store, name, owner_id)
    return read_settings(row["settings"]) if row else None


def put_profile(
    store: Store,
    value: Profile,
    value_settings: ProfileSettings | None = None,
    owner_id: str | None = None,
) -> None:
    stamp = _iso(datetime.now(UTC))
    selected = value_settings or settings_of(value)
    value = tuned(value, selected)
    with store.tx() as db:
        db.execute(
            "INSERT INTO profiles(name,modality,json,settings,owner_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(IFNULL(owner_id,''),name) DO UPDATE SET modality=excluded.modality,json=excluded.json,settings=excluded.settings,updated_at=excluded.updated_at",
            (
                value.name,
                value.modality,
                value.model_dump_json(),
                selected.model_dump_json(),
                owner_id,
                stamp,
                stamp,
            ),
        )


def put_settings(
    store: Store, name: str, value: ProfileSettings, owner_id: str | None = None
) -> None:
    """Write the settings, and keep the profile document saying the same thing.

    The document is what `GET /v1/profiles/{name}` answers with and what a copy
    is made from; letting it drift from the settings is how a profile came to
    have two sets of weights, one of which was a lie.
    """
    clause, args = mine(owner_id)
    row = _row(store, name, owner_id, shared=False)
    document = None
    if row is not None:
        held = read_profile(row["json"])
        document = tuned(held, value).model_dump_json()
    with store.tx() as db:
        if document is None:
            db.execute(
                f"UPDATE profiles SET settings=?, updated_at=? WHERE name=? AND {clause}",
                (value.model_dump_json(), _iso(datetime.now(UTC)), name, *args),
            )
        else:
            db.execute(
                f"UPDATE profiles SET settings=?, json=?, updated_at=? WHERE name=? AND {clause}",
                (value.model_dump_json(), document, _iso(datetime.now(UTC)), name, *args),
            )


def update_settings(
    current: ProfileSettings, patch: dict[str, Any]
) -> tuple[ProfileSettings, list[str]]:
    """Merge a patch into one profile's settings, and say what it ignored.

    The hand controls -- `manual`, `pinned`, `removed`, `needs` -- are lists
    and replace what was there; a patch that does not name one leaves it alone.

    Weights merge per axis rather than replacing the map, so a page that knows
    about one slider cannot wipe the other nine. Two spellings remove an axis
    outright -- `{"weights": {"axis": null}}`, which is what a form sends when a
    row is cleared, and `{"remove_axes": ["axis"]}`, which is what a script
    writes when it means it -- because without either, an axis could be set to
    zero but never taken off the profile at all.

    Retired keys (a floor, a price sensitivity, an experience weight, an
    auto-apply switch) are accepted and dropped, with one sentence each in the
    warnings, so a script written against the old shape keeps working and its
    author finds out why nothing changed.
    """
    cleaned, warnings = clean_settings(patch)
    raw = current.model_dump()
    dropped = {str(axis) for axis in cleaned.pop("remove_axes", None) or []}

    if "weights" in cleaned:
        merged = dict(raw["weights"])
        for axis, value in (cleaned["weights"] or {}).items():
            if value is None:
                dropped.add(axis)
                continue
            merged[axis] = float(value)
        cleaned["weights"] = merged
    if dropped:
        weights = dict(cleaned.get("weights", raw["weights"]))
        for axis in dropped:
            weights.pop(axis, None)
        cleaned["weights"] = weights

    return ProfileSettings.model_validate({**raw, **cleaned}), warnings


def create(
    store: Store,
    value: Profile,
    value_settings: ProfileSettings | None = None,
    owner_id: str | None = None,
) -> None:
    if profile(store, value.name, owner_id):
        raise ValueError("exists")
    put_profile(store, value, value_settings, owner_id=owner_id)


def rename(store: Store, old: str, new: str, owner_id: str | None = None) -> None:
    held = profile(store, old, owner_id)
    if held is None:
        raise KeyError(old)
    if profile(store, new, owner_id):
        raise ValueError("exists")
    renamed = held.model_copy(update={"name": new})
    clause, args = mine(owner_id)
    with store.tx() as db:
        db.execute(
            f"UPDATE profiles SET name=?, json=?, updated_at=? WHERE name=? AND {clause}",
            (new, renamed.model_dump_json(), _iso(datetime.now(UTC)), old, *args),
        )
        db.execute(f"UPDATE chains SET profile=? WHERE profile=? AND {clause}", (new, old, *args))
        db.execute(f"UPDATE outcomes SET profile=? WHERE profile=? AND {clause}", (new, old, *args))
        db.execute("UPDATE decisions SET profile=? WHERE profile=?", (new, old))


def set_purpose(store: Store, name: str, purpose: str, owner_id: str | None = None) -> Profile:
    """Rewrite what a profile is *for*, and nothing else.

    The description is the only part of a profile a person edits often, and
    until now the only way to change it was to PUT the whole profile back --
    which meant a page had to hold, and re-send, every weight it never asked
    about. One field, one call.
    """
    held = profile(store, name, owner_id)
    if held is None:
        raise KeyError(name)
    updated = held.model_copy(update={"purpose": purpose})
    clause, args = mine(owner_id)
    with store.tx() as db:
        db.execute(
            f"UPDATE profiles SET json=?, updated_at=? WHERE name=? AND {clause}",
            (updated.model_dump_json(), _iso(datetime.now(UTC)), name, *args),
        )
    return updated


def delete(store: Store, name: str, owner_id: str | None = None) -> bool:
    clause, args = mine(owner_id)
    with store.tx() as db:
        db.execute(f"DELETE FROM chains WHERE profile=? AND {clause}", (name, *args))
        db.execute(f"DELETE FROM profile_models WHERE profile=? AND {clause}", (name, *args))
        cur = db.execute(f"DELETE FROM profiles WHERE name=? AND {clause}", (name, *args))
    return bool(cur.rowcount)


def live_prefixes(store: Store, owner_id: str | None = None) -> set[str]:
    """The router-local prefixes the last successful pull actually served.

    Scoped to this owner's own connectors when there is one: a multiplier is a
    negotiated rate on *your* router, and a prefix only somebody else's gateway
    serves is not a knob you have.
    """
    sql = (
        "SELECT DISTINCT r.local_id FROM reachable r"
        " LEFT JOIN connectors c ON c.id = r.connector_id"
        " WHERE r.stale=0"
    )
    args: tuple[Any, ...] = ()
    if owner_id is not None:
        clause, args = mine(owner_id, "c.owner_id")
        sql += f" AND {clause}"
    return {
        r["local_id"].split("/", 1)[0] for r in store.db.execute(sql, args) if "/" in r["local_id"]
    }


def sync_prefixes(store: Store, owner_id: str | None = None) -> None:
    with store.tx() as db:
        for prefix in live_prefixes(store, owner_id):
            db.execute(
                "INSERT INTO cost_multipliers(prefix,multiplier,owner_id) VALUES(?,1.0,?)"
                " ON CONFLICT(IFNULL(owner_id,''),prefix) DO NOTHING",
                (prefix, owner_id),
            )


def multipliers(store: Store, owner_id: str | None = None) -> dict[str, float]:
    """Every multiplier this owner holds, whether or not its prefix is reachable.

    This is the stored truth, so it is what a configuration export copies and an
    import restores: unplugging a router for an afternoon must not silently drop
    the number somebody tuned. `live_multipliers` is the answer to the different
    question -- which of these is in effect right now.
    """
    sync_prefixes(store, owner_id)
    clause, args = mine(owner_id)
    return {
        r["prefix"]: r["multiplier"]
        for r in store.db.execute(
            f"SELECT * FROM cost_multipliers WHERE {clause} ORDER BY prefix", args
        )
    }


def live_multipliers(store: Store, owner_id: str | None = None) -> dict[str, float]:
    """The multipliers whose prefix the last successful pull actually served.

    What the API shows. A multiplier on a prefix nothing serves is not a price,
    it is a leftover, and a screen that shows it invites someone to tune a knob
    wired to nothing -- which is exactly what 37 retired `oc-go/*` ids left
    behind. The row stays in the table either way.
    """
    live = live_prefixes(store, owner_id)
    return {
        prefix: value for prefix, value in multipliers(store, owner_id).items() if prefix in live
    }


def put_multipliers(
    store: Store, values: dict[str, float], owner_id: str | None = None
) -> dict[str, float]:
    if any(v < 0 for v in values.values()):
        raise ValueError("multipliers must be non-negative")
    with store.tx() as db:
        for prefix, value in values.items():
            db.execute(
                "INSERT INTO cost_multipliers(prefix,multiplier,owner_id) VALUES(?,?,?)"
                " ON CONFLICT(IFNULL(owner_id,''),prefix) DO UPDATE SET multiplier=excluded.multiplier",
                (prefix, value, owner_id),
            )
    return live_multipliers(store, owner_id)


def add_outcome(store: Store, value: Outcome, owner_id: str | None = None) -> None:
    with store.tx() as db:
        db.execute(
            "INSERT INTO outcomes(profile,model_id,local_id,ok,seconds,vote,note,at,owner_id) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                value.profile,
                value.model_id,
                value.local_id,
                int(value.ok),
                value.seconds,
                value.vote,
                value.note,
                _iso(value.at),
                owner_id,
            ),
        )


def experience(
    store: Store, name: str, at: datetime | None = None, owner_id: str | None = None
) -> list[dict[str, Any]]:
    """What your own traffic says about each model on this seat.

    Recorded, and worth reading, but it does not move a score: a number that
    edits the ranking from behind the sliders is exactly what this rebuild took
    out.
    """
    cutoff = _iso((at or datetime.now(UTC)) - timedelta(days=30))
    clause, args = mine(owner_id)
    rows = store.db.execute(
        f"SELECT model_id,COUNT(*) n,SUM(ok) successes FROM outcomes WHERE profile=? AND at>=? AND {clause} GROUP BY model_id ORDER BY model_id",
        (name, cutoff, *args),
    )
    return [
        {
            "model_id": r["model_id"],
            "successes": r["successes"],
            "outcomes": r["n"],
            "experience": (r["successes"] + 1) / (r["n"] + 2),
        }
        for r in rows
    ]


def rerank_cached(ranking: Ranking, weights: dict[str, float]) -> Ranking:
    """Reweight cached axis values without rebuilding the observation table.

    The same arithmetic the engine does, over the axis values a ranking already
    carries: weights in, score out, reachable models in score order. It is what
    makes a slider answer in milliseconds instead of seconds, and it has to
    agree with the engine to the last decimal or the preview is a lie.
    """
    ranks = []
    for row in ranking.ranks:
        axes = {axis.axis: axis for axis in row.axes}
        contributions: dict[str, float] = {}
        confidence = 0.0
        score = 0.0
        for axis, weight in weights.items():
            cached = axes.get(axis)
            value = cached.value if cached else None
            coverage = cached.coverage if cached else 0.0
            contribution = weight * (value if value is not None else 0.0)
            contributions[axis] = contribution
            score += contribution
            if value is not None:
                confidence += weight * coverage
        updated_axes = [
            axis.model_copy(update={"contribution": contributions.get(axis.axis, 0.0)})
            for axis in row.axes
        ]
        ranks.append(
            row.model_copy(
                update={
                    "position": 0,
                    "score": score,
                    "confidence": confidence,
                    "final": score * row.health,
                    "axes": updated_axes,
                }
            )
        )
    reachable = [row for row in ranks if row.reachable]
    reachable.sort(key=lambda row: (-row.final, row.model_id))
    for position, row in enumerate(reachable, start=1):
        row.position = position
    return ranking.model_copy(
        update={"ranks": reachable + [row for row in ranks if not row.reachable]}
    )


def capability_map(store: Store, modality: Any) -> dict[str, Capability]:
    """What every source and every router says each model can do, merged.

    The capabilities table is what catalogues publish; a router's own model
    list sometimes says more (an image input, a tool flag), so it fills the
    gaps. What a source states wins over what a router implies.
    """
    from sieve.store.db import _merge_capability

    merged = dict(store.capabilities(modality))
    for row in store.reachable():
        if row.model_id is None:
            continue
        held = merged.get(row.model_id)
        merged[row.model_id] = (
            row.capability if held is None else _merge_capability(held, row.capability)
        )
    return merged


def shipped_rows(
    ranked: list[Any], value: Any, capabilities: dict[str, Capability] | None = None
) -> list[Any]:
    """The rows that ship, as `sieve.scoring.select` decides them."""
    from sieve.scoring.select import select

    return select(ranked, value, capabilities).rows


def shipped_ids(
    ranked: list[Any], value: Any, capabilities: dict[str, Capability] | None = None
) -> list[str]:
    return [row.model_id for row in shipped_rows(ranked, value, capabilities)]


def chain_for(
    store: Store,
    name: str,
    ranked: list[Any],
    at: datetime | None = None,
    owner_id: str | None = None,
) -> Any:
    from sieve.contracts import Chain

    cfg = settings(store, name, owner_id)
    found = profile(store, name, owner_id)
    if cfg is None or found is None:
        return None
    caps = capability_map(store, found.modality) if cfg.needs else None
    ids = shipped_ids(ranked, cfg, caps)
    local = store.local_ids(owner_id=owner_id)
    ids = [model for model in ids if local.get(model)]
    if not ids:
        return None
    now = at or datetime.now(UTC)
    return Chain(
        profile=name,
        computed_at=now,
        primary=ids[0],
        fallbacks=ids[1:],
        local={m: local[m] for m in ids},
        incumbent=ids[0],
        incumbent_since=now,
    )
