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


def seed(store: Store, directory: Any) -> None:
    if store.db.execute("SELECT 1 FROM profiles LIMIT 1").fetchone():
        return
    stamp = _iso(datetime.now(UTC))
    with store.tx() as db:
        for profile in load_profiles(directory):
            db.execute(
                "INSERT INTO profiles(name,modality,json,settings,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                (
                    profile.name,
                    profile.modality,
                    profile.model_dump_json(),
                    default_settings(profile).model_dump_json(),
                    stamp,
                    stamp,
                ),
            )


def profiles(store: Store) -> list[Profile]:
    return [
        Profile.model_validate_json(r["json"])
        for r in store.db.execute("SELECT json FROM profiles ORDER BY name")
    ]


def profile(store: Store, name: str) -> Profile | None:
    row = store.db.execute("SELECT json FROM profiles WHERE name=?", (name,)).fetchone()
    return Profile.model_validate_json(row["json"]) if row else None


def settings(store: Store, name: str) -> ProfileSettings | None:
    row = store.db.execute("SELECT settings FROM profiles WHERE name=?", (name,)).fetchone()
    return ProfileSettings.model_validate_json(row["settings"]) if row else None


def put_profile(
    store: Store, value: Profile, value_settings: ProfileSettings | None = None
) -> None:
    stamp = _iso(datetime.now(UTC))
    current = settings(store, value.name)
    selected = value_settings or current or default_settings(value)
    with store.tx() as db:
        db.execute(
            "INSERT INTO profiles(name,modality,json,settings,created_at,updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET modality=excluded.modality,json=excluded.json,settings=excluded.settings,updated_at=excluded.updated_at",
            (
                value.name,
                value.modality,
                value.model_dump_json(),
                selected.model_dump_json(),
                stamp,
                stamp,
            ),
        )


def put_settings(store: Store, name: str, value: ProfileSettings) -> None:
    with store.tx() as db:
        db.execute(
            "UPDATE profiles SET settings=?, updated_at=? WHERE name=?",
            (value.model_dump_json(), _iso(datetime.now(UTC)), name),
        )


def update_settings(current: ProfileSettings, patch: dict[str, Any]) -> ProfileSettings:
    raw = current.model_dump()
    if "weights" in patch:
        incoming = patch["weights"]
        merged = raw["weights"]
        for axis, change in incoming.items():
            old = merged.get(axis, {"value": 0.0, "min": 0.0, "max": 1.0, "locked": False})
            candidate = {**old, **change}
            if not candidate["min"] <= candidate["value"] <= candidate["max"]:
                raise ValueError(f"weight {axis} value must be inside [min,max]")
            merged[axis] = candidate
        patch = {**patch, "weights": merged}
    result = ProfileSettings.model_validate({**raw, **patch})
    for axis, weight in result.weights.items():
        if weight.min > weight.max or not weight.min <= weight.value <= weight.max:
            raise ValueError(f"weight {axis} value must be inside [min,max]")
    return result


def create(store: Store, value: Profile, value_settings: ProfileSettings | None = None) -> None:
    if profile(store, value.name):
        raise ValueError("exists")
    put_profile(store, value, value_settings)


def rename(store: Store, old: str, new: str) -> None:
    held = profile(store, old)
    if held is None:
        raise KeyError(old)
    if profile(store, new):
        raise ValueError("exists")
    renamed = held.model_copy(update={"name": new})
    with store.tx() as db:
        db.execute(
            "UPDATE profiles SET name=?, json=?, updated_at=? WHERE name=?",
            (new, renamed.model_dump_json(), _iso(datetime.now(UTC)), old),
        )
        db.execute("UPDATE chains SET profile=? WHERE profile=?", (new, old))
        db.execute("UPDATE decisions SET profile=? WHERE profile=?", (new, old))


def delete(store: Store, name: str) -> bool:
    with store.tx() as db:
        db.execute("DELETE FROM chains WHERE profile=?", (name,))
        cur = db.execute("DELETE FROM profiles WHERE name=?", (name,))
    return bool(cur.rowcount)


def status(store: Store, name: str, model_id: str) -> dict[str, Any]:
    row = store.db.execute(
        "SELECT status,pin_order FROM profile_models WHERE profile=? AND model_id=?",
        (name, model_id),
    ).fetchone()
    return {
        "profile": name,
        "model_id": model_id,
        "status": row["status"] if row else "active",
        "pin_order": row["pin_order"] if row else None,
    }


def put_status(store: Store, name: str, model_id: str, state: str) -> dict[str, Any]:
    if state not in {"active", "pinned", "removed"}:
        raise ValueError("status must be active, pinned or removed")
    order = None
    if state == "pinned":
        row = store.db.execute(
            "SELECT COALESCE(MAX(pin_order),0)+1 n FROM profile_models WHERE profile=?", (name,)
        ).fetchone()
        order = row["n"]
    with store.tx() as db:
        db.execute(
            "INSERT INTO profile_models(profile,model_id,status,pin_order) VALUES(?,?,?,?) ON CONFLICT(profile,model_id) DO UPDATE SET status=excluded.status,pin_order=excluded.pin_order",
            (name, model_id, state, order),
        )
    return status(store, name, model_id)


def statuses(store: Store, name: str) -> dict[str, tuple[str, int | None]]:
    return {
        r["model_id"]: (r["status"], r["pin_order"])
        for r in store.db.execute("SELECT * FROM profile_models WHERE profile=?", (name,))
    }


def sync_prefixes(store: Store) -> None:
    prefixes = {r.local_id.split("/", 1)[0] for r in store.reachable() if "/" in r.local_id}
    with store.tx() as db:
        for prefix in prefixes:
            db.execute(
                "INSERT OR IGNORE INTO cost_multipliers(prefix,multiplier) VALUES(?,1.0)", (prefix,)
            )


def multipliers(store: Store) -> dict[str, float]:
    sync_prefixes(store)
    return {
        r["prefix"]: r["multiplier"]
        for r in store.db.execute("SELECT * FROM cost_multipliers ORDER BY prefix")
    }


def put_multipliers(store: Store, values: dict[str, float]) -> dict[str, float]:
    if any(v < 0 for v in values.values()):
        raise ValueError("multipliers must be non-negative")
    with store.tx() as db:
        for prefix, value in values.items():
            db.execute(
                "INSERT INTO cost_multipliers VALUES(?,?) ON CONFLICT(prefix) DO UPDATE SET multiplier=excluded.multiplier",
                (prefix, value),
            )
    return multipliers(store)


def add_outcome(store: Store, value: Outcome) -> None:
    with store.tx() as db:
        db.execute(
            "INSERT INTO outcomes(profile,model_id,local_id,ok,seconds,vote,note,at) VALUES(?,?,?,?,?,?,?,?)",
            (
                value.profile,
                value.model_id,
                value.local_id,
                int(value.ok),
                value.seconds,
                value.vote,
                value.note,
                _iso(value.at),
            ),
        )


def experience(store: Store, name: str, at: datetime | None = None) -> list[dict[str, Any]]:
    cutoff = _iso((at or datetime.now(UTC)) - timedelta(days=30))
    rows = store.db.execute(
        "SELECT model_id,COUNT(*) n,SUM(ok) successes FROM outcomes WHERE profile=? AND at>=? GROUP BY model_id ORDER BY model_id",
        (name, cutoff),
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
    store: Store, name: str, ranked: list[Any], limit: int, floor: float
) -> list[str]:
    states = statuses(store, name)
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


def chain_for(store: Store, name: str, ranked: list[Any], at: datetime | None = None) -> Any:
    from sieve.contracts import Chain

    cfg = settings(store, name)
    if cfg is None:
        return None
    ids = controlled_ids(store, name, ranked, cfg.list_length, cfg.floor_score)
    local = store.local_ids()
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
