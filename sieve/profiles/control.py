"""Database-backed, directly controlled profile lists."""
# ruff: noqa: E501

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sieve.contracts import Outcome, Profile, ProfileSettings, Ranking, WeightSetting
from sieve.profiles.load import load_profiles
from sieve.store import Store


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def default_settings(profile: Profile) -> ProfileSettings:
    return ProfileSettings(
        list_length=profile.policy.chain,
        auto_apply=profile.policy.auto_apply,
        weights={name: WeightSetting(value=value) for name, value in profile.weights.items()},
    )


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


def profiles(store: Store, owner_id: str | None = None, shared: bool = True) -> list[Profile]:
    clause, args = readable(store, owner_id) if shared else mine(owner_id)
    return [
        Profile.model_validate_json(r["json"])
        for r in store.db.execute(f"SELECT json FROM profiles WHERE {clause} ORDER BY name", args)
    ]


def _row(store: Store, name: str, owner_id: str | None, shared: bool = True) -> Any:
    clause, args = readable(store, owner_id) if shared else mine(owner_id)
    return store.db.execute(
        f"SELECT * FROM profiles WHERE name=? AND {clause}", (name, *args)
    ).fetchone()


def profile(store: Store, name: str, owner_id: str | None = None) -> Profile | None:
    row = _row(store, name, owner_id)
    return Profile.model_validate_json(row["json"]) if row else None


def owner_of(store: Store, name: str, owner_id: str | None = None) -> str | None:
    """Whose profile this is, as far as `owner_id` can see it."""
    row = _row(store, name, owner_id)
    return row["owner_id"] if row else None


def settings(store: Store, name: str, owner_id: str | None = None) -> ProfileSettings | None:
    row = _row(store, name, owner_id)
    return ProfileSettings.model_validate_json(row["settings"]) if row else None


def put_profile(
    store: Store,
    value: Profile,
    value_settings: ProfileSettings | None = None,
    owner_id: str | None = None,
) -> None:
    stamp = _iso(datetime.now(UTC))
    current = settings(store, value.name, owner_id)
    selected = value_settings or current or default_settings(value)
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
    clause, args = mine(owner_id)
    with store.tx() as db:
        db.execute(
            f"UPDATE profiles SET settings=?, updated_at=? WHERE name=? AND {clause}",
            (value.model_dump_json(), _iso(datetime.now(UTC)), name, *args),
        )


def update_settings(current: ProfileSettings, patch: dict[str, Any]) -> ProfileSettings:
    """Merge a patch into one profile's settings, and say no to an invalid one.

    Weights merge per axis rather than replacing the map, so a page that knows
    about one slider cannot wipe the other nine. Two spellings remove an axis
    outright -- `{"weights": {"axis": null}}`, which is what a form sends when a
    row is cleared, and `{"remove_axes": ["axis"]}`, which is what a script
    writes when it means it -- because without either, an axis could be set to
    zero but never taken off the profile at all.
    """
    raw = current.model_dump()
    dropped = {str(axis) for axis in patch.get("remove_axes") or []}
    patch = {k: v for k, v in patch.items() if k != "remove_axes"}
    if "weights" in patch:
        incoming = patch["weights"] or {}
        merged = raw["weights"]
        for axis, change in incoming.items():
            if change is None:
                dropped.add(axis)
                continue
            old = merged.get(axis, {"value": 0.0, "min": 0.0, "max": 1.0, "locked": False})
            candidate = {**old, **change}
            if not candidate["min"] <= candidate["value"] <= candidate["max"]:
                raise ValueError(f"weight {axis} value must be inside [min,max]")
            merged[axis] = candidate
        patch = {**patch, "weights": merged}
    if dropped:
        weights = dict(patch.get("weights", raw["weights"]))
        for axis in dropped:
            weights.pop(axis, None)
        patch = {**patch, "weights": weights}
    result = ProfileSettings.model_validate({**raw, **patch})
    for axis, weight in result.weights.items():
        if weight.min > weight.max or not weight.min <= weight.value <= weight.max:
            raise ValueError(f"weight {axis} value must be inside [min,max]")
    return result


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
        db.execute(
            f"UPDATE profile_models SET profile=? WHERE profile=? AND {clause}", (new, old, *args)
        )
        db.execute(f"UPDATE chains SET profile=? WHERE profile=? AND {clause}", (new, old, *args))
        db.execute(f"UPDATE outcomes SET profile=? WHERE profile=? AND {clause}", (new, old, *args))
        db.execute("UPDATE decisions SET profile=? WHERE profile=?", (new, old))


def set_purpose(store: Store, name: str, purpose: str, owner_id: str | None = None) -> Profile:
    """Rewrite what a profile is *for*, and nothing else.

    The description is the only part of a profile a person edits often, and
    until now the only way to change it was to PUT the whole profile back --
    which meant a page had to hold, and re-send, every weight and constraint it
    never asked about. One field, one call.
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


def status(store: Store, name: str, model_id: str, owner_id: str | None = None) -> dict[str, Any]:
    clause, args = mine(owner_id)
    row = store.db.execute(
        f"SELECT status,pin_order FROM profile_models WHERE profile=? AND model_id=? AND {clause}",
        (name, model_id, *args),
    ).fetchone()
    return {
        "profile": name,
        "model_id": model_id,
        "status": row["status"] if row else "active",
        "pin_order": row["pin_order"] if row else None,
    }


def put_status(
    store: Store, name: str, model_id: str, state: str, owner_id: str | None = None
) -> dict[str, Any]:
    if state not in {"active", "pinned", "removed"}:
        raise ValueError("status must be active, pinned or removed")
    clause, args = mine(owner_id)
    order = None
    if state == "pinned":
        row = store.db.execute(
            f"SELECT COALESCE(MAX(pin_order),0)+1 n FROM profile_models WHERE profile=? AND {clause}",
            (name, *args),
        ).fetchone()
        order = row["n"]
    with store.tx() as db:
        db.execute(
            "INSERT INTO profile_models(profile,model_id,status,pin_order,owner_id) VALUES(?,?,?,?,?) ON CONFLICT(IFNULL(owner_id,''),profile,model_id) DO UPDATE SET status=excluded.status,pin_order=excluded.pin_order",
            (name, model_id, state, order, owner_id),
        )
    return status(store, name, model_id, owner_id)


def statuses(
    store: Store, name: str, owner_id: str | None = None
) -> dict[str, tuple[str, int | None]]:
    clause, args = mine(owner_id)
    return {
        r["model_id"]: (r["status"], r["pin_order"])
        for r in store.db.execute(
            f"SELECT * FROM profile_models WHERE profile=? AND {clause}", (name, *args)
        )
    }


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


def rerank_cached(
    ranking: Ranking,
    weights: dict[str, WeightSetting],
    experience_weight: float,
    observed_experience: dict[str, float],
) -> Ranking:
    """Reweight cached axis values without rebuilding the observation table."""
    ranks = []
    for row in ranking.ranks:
        axes = {axis.axis: axis for axis in row.axes}
        contributions: dict[str, float] = {}
        confidence = 0.0
        score = 0.0
        for axis, setting in weights.items():
            cached = axes.get(axis)
            value = cached.value if cached else None
            coverage = cached.coverage if cached else 0.0
            contribution = setting.value * (value if value is not None else 0.0)
            contributions[axis] = contribution
            score += contribution
            if value is not None:
                confidence += setting.value * coverage
        if experience_weight:
            value = observed_experience.get(row.model_id, 0.5)
            contributions["experience"] = experience_weight * value
            score = (1.0 - experience_weight) * score + experience_weight * value
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
    eligible = [row for row in ranks if not row.excluded_by and not row.dominated_by]
    eligible.sort(key=lambda row: (-row.final, row.model_id))
    for position, row in enumerate(eligible, start=1):
        row.position = position
    return ranking.model_copy(
        update={"ranks": eligible + [row for row in ranks if row.excluded_by or row.dominated_by]}
    )


def controlled_ids(
    store: Store,
    name: str,
    ranked: list[Any],
    limit: int,
    floor: float,
    owner_id: str | None = None,
) -> list[str]:
    states = statuses(store, name, owner_id)
    pinned = sorted(
        ((order or 0, model) for model, (state, order) in states.items() if state == "pinned")
    )
    out = [model for _, model in pinned]
    for row in ranked:
        state = states.get(row.model_id, ("active", None))[0]
        if (
            state == "active"
            and row.reachable
            and row.position > 0
            and row.final >= floor
            and row.model_id not in out
        ):
            out.append(row.model_id)
    return out[:limit]


def chain_for(
    store: Store,
    name: str,
    ranked: list[Any],
    at: datetime | None = None,
    owner_id: str | None = None,
) -> Any:
    from sieve.contracts import Chain

    cfg = settings(store, name, owner_id)
    if cfg is None:
        return None
    ids = controlled_ids(store, name, ranked, cfg.list_length, cfg.floor_score, owner_id)
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
