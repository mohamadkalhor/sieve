"""EFFORT.md: a seat runs at one reasoning effort, and Sieve scores it there.

The fixture is three families on one gateway, small enough to read:

- `t/alpha` publishes low, medium and max (the bare row is max), reached
  through `gw/alpha`, an id that names no effort;
- `t/beta` has one setting;
- `t/gamma` publishes low and high (bare = high), reached through
  `gw/gamma-high`, an id that says which mode it is.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from sieve.config import Config, Paths, StoreConfig
from sieve.contracts import Effort, ModelRef, Observation, Profile, Rank, Reachable
from sieve.engine import Deps, rank_profile
from sieve.profiles import control
from sieve.scoring.select import select
from sieve.store import Store

REPO = Path(__file__).resolve().parent.parent

#: id -> (name, family, effort, intelligence index)
ROWS: dict[str, tuple[str, str, str | None, float]] = {
    "t/alpha": ("Alpha (max)", "t/alpha", "max", 50.0),
    "t/alpha-medium": ("Alpha (medium)", "t/alpha", "medium", 40.0),
    "t/alpha-low": ("Alpha (low)", "t/alpha", "low", 30.0),
    "t/beta": ("Beta", "t/beta", None, 35.0),
    "t/gamma": ("Gamma (high)", "t/gamma", "high", 45.0),
    "t/gamma-low": ("Gamma (low)", "t/gamma", "low", 20.0),
}
#: router id -> the catalogue id it matched
ROUTES = {"gw/alpha": "t/alpha", "gw/beta": "t/beta", "gw/gamma-high": "t/gamma"}


def build(tmp_path: Path) -> tuple[Config, Store]:
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    cfg = Config(
        root=tmp_path,
        store=StoreConfig(path=str(tmp_path / "sieve.db")),
        paths=Paths(axes=str(REPO / "data" / "axes"), profiles=str(profiles)),
    )
    now = datetime.now(UTC)
    store = Store(cfg.db_path)
    store.upsert_models(
        ModelRef(id=i, modality="llm", name=name, creator="t", family=fam, effort=mode)
        for i, (name, fam, mode, _) in ROWS.items()
    )
    store.add_observations(
        Observation(
            model_id=i,
            modality="llm",
            source="aa_llm",
            field="artificial_analysis_intelligence_index",
            value=value,
            unit="index_0_100",
            observed_at=now,
            pulled_at=now,
        )
        for i, (_, _, _, value) in ROWS.items()
    )
    store.set_reachable(
        "gw",
        [
            Reachable(inventory="gw", local_id=local, model_id=model, seen_at=now)
            for local, model in ROUTES.items()
        ],
    )
    return cfg, store


def seat(effort: Effort | None, **over: object) -> Profile:
    return Profile.model_validate(
        {
            "name": "t",
            "modality": "llm",
            "purpose": "test",
            "weights": {"intelligence": 1.0},
            "ship": 3,
            "effort": effort,
            **over,
        }
    )


def ranked(cfg: Config, store: Store, profile: Profile) -> dict[str, Rank]:
    ranking = rank_profile(cfg, store, profile, deps=Deps(), snapshot="test")
    assert ranking.effort == profile.effort
    return {r.model_id: r for r in ranking.ranks}


def value(rank: Rank) -> float | None:
    return next(a.value for a in rank.axes if a.axis == "intelligence")


@pytest.fixture
def box(tmp_path: Path) -> Iterator[tuple[Config, Store]]:
    cfg, store = build(tmp_path)
    yield cfg, store
    store.close()


def test_without_an_effort_every_model_is_scored_on_the_row_it_matched(
    box: tuple[Config, Store],
) -> None:
    cfg, store = box
    rows = ranked(cfg, store, seat(None))
    assert (rows["t/alpha"].scored_as, rows["t/alpha"].effort) == ("t/alpha", "max")
    assert {rows[m].effort_how for m in ROUTES.values()} == {"any"}
    assert rows["t/alpha"].score == max(r.score for r in rows.values())
    assert rows["t/alpha"].position == 1


def test_a_seat_at_medium_is_scored_on_the_medium_row(box: tuple[Config, Store]) -> None:
    cfg, store = box
    rows = ranked(cfg, store, seat("medium"))
    alpha = rows["t/alpha"]
    assert (alpha.scored_as, alpha.effort, alpha.effort_how) == (
        "t/alpha-medium",
        "medium",
        "exact",
    )
    # the score is the medium row's, to the bit
    assert value(alpha) == value(rows["t/alpha-medium"])
    assert alpha.score == rows["t/alpha-medium"].score
    # the row is still the model the router reaches
    assert alpha.model_id == "t/alpha"
    assert alpha.local_ids == ["gw/alpha"]
    assert alpha.reachable
    # one setting: nothing to choose
    beta = rows["t/beta"]
    assert (beta.scored_as, beta.effort, beta.effort_how) == ("t/beta", None, "one")
    # the router id names its mode, and the seat does not override it
    gamma = rows["t/gamma"]
    assert (gamma.scored_as, gamma.effort, gamma.effort_how) == ("t/gamma", "high", "id")
    # Gamma (high, 45) now leads Alpha at medium (40); at any, Alpha (max, 50) led
    order = sorted((r for r in rows.values() if r.position), key=lambda r: r.position)
    assert [r.model_id for r in order] == ["t/gamma", "t/alpha", "t/beta"]
    # a model nobody can call is scored on its own row, whatever the seat says
    assert rows["t/alpha-low"].scored_as == "t/alpha-low"
    assert rows["t/alpha-low"].effort_how is None


def test_an_unpublished_effort_takes_the_nearest_below(box: tuple[Config, Store]) -> None:
    cfg, store = box
    alpha = ranked(cfg, store, seat("high"))["t/alpha"]
    assert (alpha.scored_as, alpha.effort, alpha.effort_how) == (
        "t/alpha-medium",
        "medium",
        "nearest_below",
    )
    alpha = ranked(cfg, store, seat("non-reasoning"))["t/alpha"]
    assert (alpha.scored_as, alpha.effort_how) == ("t/alpha-low", "nearest_above")


def test_a_pin_survives_medium_to_high(box: tuple[Config, Store]) -> None:
    cfg, store = box
    shipped: list[list[str]] = []
    for effort in ("medium", "high"):
        profile = seat(effort, pinned=["t/beta"])
        ranking = rank_profile(cfg, store, profile, deps=Deps(), snapshot="test")
        rows = select(ranking.ranks, control.settings_of(profile), None).rows
        shipped.append([r.model_id for r in rows])
        assert rows[0].model_id == "t/beta"
        chain = [local for r in rows for local in r.local_ids]
        assert set(chain) <= set(ROUTES)
    assert shipped[0] == shipped[1]
