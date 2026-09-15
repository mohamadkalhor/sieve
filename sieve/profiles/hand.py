"""Models no source scores, and the scores a person gives them by hand.

A router can serve a model no benchmark has ever measured: an internal build,
a vendor's agent variant, a model too new or too obscure for the scoreboards.
Sieve used to show those as reachable with a score of zero, which reads as
"bad" when the truth is "unknown". This module lists them, and lets a person --
or an agent through the API -- score them axis by axis.

The rule the engine follows: **a hand score is the axis value itself**, 0..1,
with full coverage, and it wins over whatever the axis computed. It is a
judgement, so it is kept apart from `observations` and can be edited or
cleared.

A router id that matches nothing in the catalogue gets a catalogue entry of
its own, `hand/<local id>`, and is aliased to it, so it has somewhere for its
scores to live. Clearing the scores leaves that entry and the alias in place;
they cost nothing and say only "this is the model that router serves".
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sieve.contracts import ModelRef
from sieve.profiles.control import mine
from sieve.store import Store

#: the catalogue prefix for a model that exists only because a router serves it
HAND_PREFIX = "hand/"


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def scores(store: Store, modality: str, owner_id: str | None = None) -> dict[str, dict[str, float]]:
    """`{model_id: {axis: value}}` for one modality."""
    clause, args = mine(owner_id)
    out: dict[str, dict[str, float]] = {}
    for row in store.db.execute(
        f"SELECT model_id, axis, value FROM hand_scores WHERE modality=? AND axis != ''"
        f" AND {clause}",
        (modality, *args),
    ):
        out.setdefault(row["model_id"], {})[row["axis"]] = float(row["value"])
    return out


def changed_at(store: Store, modality: str, owner_id: str | None = None) -> datetime | None:
    """When a hand score for this modality last changed, or None if none ever has.

    Every write also refreshes one stamp row (empty model and axis), because a
    clear deletes rows and would otherwise leave nothing newer behind.
    """
    clause, args = mine(owner_id)
    row = store.db.execute(
        f"SELECT MAX(at) AS at FROM hand_scores WHERE modality=? AND {clause}",
        (modality, *args),
    ).fetchone()
    return datetime.fromisoformat(row["at"]) if row and row["at"] else None


def _resolve(store: Store, local_id: str, modality: str, name: str | None) -> str:
    """The catalogue id behind a router id, making one if there is none."""
    row = store.db.execute(
        "SELECT model_id FROM reachable WHERE local_id=? AND stale=0 AND model_id IS NOT NULL",
        (local_id,),
    ).fetchone()
    if row is not None:
        return str(row["model_id"])
    if not store.db.execute(
        "SELECT 1 FROM reachable WHERE local_id=? AND stale=0", (local_id,)
    ).fetchone():
        raise LookupError(f"no router serves {local_id!r} right now")
    model_id = HAND_PREFIX + local_id
    slug = local_id.rsplit("/", 1)[-1]
    store.upsert_models(
        [
            ModelRef(
                id=model_id,
                modality=modality,  # type: ignore[arg-type]
                name=name or slug,
                creator="hand",
            )
        ]
    )
    store.put_alias(local_id, modality, model_id)  # type: ignore[arg-type]
    with store.tx() as db:
        db.execute(
            "UPDATE reachable SET model_id=? WHERE local_id=? AND model_id IS NULL",
            (model_id, local_id),
        )
    store.invalidate_views()
    return model_id


def put(
    store: Store,
    local_id: str,
    modality: str,
    given: dict[str, float | None],
    axes: set[str],
    by: str,
    name: str | None = None,
    owner_id: str | None = None,
) -> str:
    """Set (or, with None, clear) hand scores for the model a router id serves.

    Returns the catalogue id the scores were written against. Raises
    `ValueError` for an unknown axis or a value outside 0..1, and `LookupError`
    for an id no router serves.
    """
    for axis, value in given.items():
        if axis not in axes:
            raise ValueError(f"unknown axis {axis!r} for {modality}")
        if value is not None and not 0.0 <= float(value) <= 1.0:
            raise ValueError(f"a hand score is 0..1, got {value} for {axis!r}")
    model_id = _resolve(store, local_id, modality, name)
    stamp = _iso(datetime.now(UTC))
    clause, args = mine(owner_id)
    with store.tx() as db:
        for axis, value in given.items():
            db.execute(
                f"DELETE FROM hand_scores WHERE model_id=? AND modality=? AND axis=? AND {clause}",
                (model_id, modality, axis, *args),
            )
            if value is not None:
                db.execute(
                    "INSERT INTO hand_scores (model_id, modality, axis, value, by, at, owner_id)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (model_id, modality, axis, float(value), by, stamp, owner_id),
                )
        _stamp(db, modality, by, stamp, owner_id)
    return model_id


def _stamp(db: Any, modality: str, by: str, stamp: str, owner_id: str | None) -> None:
    """Refresh the change stamp: a clear deletes rows, and a preview still has
    to know its cached ranking is older than the scores."""
    clause, args = mine(owner_id)
    db.execute(
        f"DELETE FROM hand_scores WHERE modality=? AND axis='' AND {clause}",
        (modality, *args),
    )
    db.execute(
        "INSERT INTO hand_scores (model_id, modality, axis, value, by, at, owner_id)"
        " VALUES ('', ?, '', 0, ?, ?, ?)",
        (modality, by, stamp, owner_id),
    )


def link(
    store: Store,
    local_id: str,
    modality: str,
    by: str,
    name: str | None = None,
    owner_id: str | None = None,
) -> str:
    """Give a router id that matched nothing a catalogue entry, with no scores.

    This is what makes it pinnable: a profile holds catalogue ids, and a model
    only has one once it is linked. The change stamp moves too, so the next
    preview ranks again and the model is in its pool. Raises `LookupError` for
    an id no router serves.
    """
    model_id = _resolve(store, local_id, modality, name)
    with store.tx() as db:
        _stamp(db, modality, by, _iso(datetime.now(UTC)), owner_id)
    return model_id


def scored_ids(
    store: Store, model_ids: list[str], modality: str, owner_id: str | None = None
) -> set[str]:
    """Which of these ids a source measured or a person scored by hand."""
    out: set[str] = set(scores(store, modality, owner_id)) & set(model_ids)
    rest = [m for m in model_ids if m not in out]
    for start in range(0, len(rest), 500):
        chunk = rest[start : start + 500]
        marks = ",".join("?" * len(chunk))
        out |= {
            row["model_id"]
            for row in store.db.execute(
                f"SELECT DISTINCT model_id FROM observations WHERE model_id IN ({marks})", chunk
            )
        }
    return out


def unscored(store: Store, owner_id: str | None = None) -> list[dict[str, Any]]:
    """Every model a router serves that no source has benchmarked.

    Two kinds, and each row says which:

    - `no_match`: the router id matched nothing in the catalogue.
    - `no_scores`: it matched, but no source has measured that model.

    A router's own combos (an id with no provider prefix, like `sieve-coder`)
    are routing, not models, and are left out. Models scored by hand stay on
    the list with their scores, so they can be edited.
    """
    names: dict[tuple[str, str], str] = {
        (r["id"], r["modality"]): r["name"]
        for r in store.db.execute("SELECT id, modality, name FROM models")
    }
    by_model: dict[str, list[str]] = {}
    for r in store.db.execute("SELECT DISTINCT id, modality FROM models"):
        by_model.setdefault(r["id"], []).append(r["modality"])

    hand_by_modality: dict[str, dict[str, dict[str, float]]] = {}

    def hand_for(model_id: str, modality: str) -> dict[str, float]:
        if modality not in hand_by_modality:
            hand_by_modality[modality] = scores(store, modality, owner_id)
        return hand_by_modality[modality].get(model_id, {})

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in store.reachable():
        if "/" not in item.local_id:
            continue
        if item.model_id is None:
            rows.append(
                {
                    "local_id": item.local_id,
                    "model_id": None,
                    "name": item.local_id.rsplit("/", 1)[-1],
                    "modality": None,
                    "reason": "no_match",
                    "hand": {},
                    "router": item.inventory,
                }
            )
            continue
        for modality in by_model.get(item.model_id, []):
            key = (item.model_id, modality)
            if key in seen:
                continue
            measured = store.db.execute(
                "SELECT 1 FROM observations WHERE model_id=? AND modality=? LIMIT 1",
                (item.model_id, modality),
            ).fetchone()
            if measured:
                continue
            seen.add(key)
            rows.append(
                {
                    "local_id": item.local_id,
                    "model_id": item.model_id,
                    "name": names.get(key) or item.model_id,
                    "modality": modality,
                    "reason": "no_scores",
                    "hand": hand_for(item.model_id, modality),
                    "router": item.inventory,
                }
            )
    rows.sort(key=lambda r: (r["reason"] != "no_match", bool(r["hand"]), r["local_id"]))
    return rows
