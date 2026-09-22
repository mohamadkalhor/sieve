"""A seat ranks and ships only models the catalogue holds in its own modality.

A router id is matched to a catalogue id with no modality, so `reachable` says
"this box can call it" and nothing about what it makes. The engine used to add
every reachable id to every profile's pool, so an image seat gave each llm the
box can reach a position at a score of 0 -- which reads as "ranked, and last"
when the truth is "not an image model" -- and shipped them whenever fewer than
`ship` image models were reachable.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from sieve.api.app import create_app
from sieve.config import Config
from sieve.contracts import ModelRef, Observation, Reachable
from sieve.engine import Deps, rank_profile
from sieve.profiles import control
from sieve.store import Store
from tests.test_api_acceptance import workspace  # noqa: F401  (the fixture)

IMAGE = "blackforest/flux-9"
BOTH = "google/gemini-9-image"


def _serve_an_image_model(cfg: Config) -> set[str]:
    """One reachable text-to-image model with a score, beside the fixture's llms.

    Returns the llm ids the fixture's gateway reaches.
    """
    now = datetime.now(UTC)
    store = Store(cfg.db_path)
    llms = set(store.local_ids())
    store.upsert_models(
        [ModelRef(id=IMAGE, modality="text-to-image", name="Flux 9", creator="blackforest")]
    )
    store.add_observations(
        [
            Observation(
                model_id=IMAGE,
                modality="text-to-image",
                source="aa_media",
                field="elo",
                value=1150.0,
                unit="elo",
                observed_at=now,
                pulled_at=now,
            )
        ]
    )
    store.set_reachable(
        "images",
        [Reachable(inventory="images", local_id=IMAGE, model_id=IMAGE, seen_at=now)],
    )
    store.close()
    return llms


def _rank(cfg: Config, store: Store, name: str) -> list[str]:
    """The positioned ids of one shipped profile, in position order."""
    control.seed(store, cfg.profiles_dir)
    found = control.profile(store, name)
    assert found is not None
    ranking = rank_profile(cfg, store, found, deps=Deps(), snapshot="test")
    return [r.model_id for r in sorted(ranking.ranks, key=lambda r: r.position) if r.position]


def test_an_image_seat_positions_no_llm(workspace: Config) -> None:  # noqa: F811
    llms = _serve_an_image_model(workspace)
    assert llms, "the fixture's gateway reaches llms, or this proves nothing"
    store = Store(workspace.db_path)
    try:
        assert _rank(workspace, store, "image_general") == [IMAGE]
        # and the llm seat still ranks every llm it can reach
        assert set(_rank(workspace, store, "coder")) == llms
        assert store.local_ids(modality="text-to-image") == {IMAGE: [IMAGE]}
        assert set(store.local_ids(modality="llm")) == llms
    finally:
        store.close()


def test_an_id_the_catalogue_holds_in_two_modalities_is_reachable_in_both(
    workspace: Config,  # noqa: F811
) -> None:
    """The catalogue's claim decides, not a guess of one modality per id."""
    now = datetime.now(UTC)
    store = Store(workspace.db_path)
    try:
        store.upsert_models(
            [
                ModelRef(id=BOTH, modality="llm", name="Gemini 9 Image", creator="google"),
                ModelRef(
                    id=BOTH, modality="text-to-image", name="Gemini 9 Image", creator="google"
                ),
            ]
        )
        store.set_reachable(
            "images",
            [Reachable(inventory="images", local_id=BOTH, model_id=BOTH, seen_at=now)],
        )
        assert BOTH in store.local_ids(modality="llm")
        assert BOTH in store.local_ids(modality="text-to-image")
        assert BOTH not in store.local_ids(modality="text-to-video")
    finally:
        store.close()


def test_an_image_seat_neither_offers_nor_ships_an_llm(workspace: Config) -> None:  # noqa: F811
    llms = _serve_an_image_model(workspace)
    with TestClient(create_app(workspace)) as client:
        preview = client.post("/v1/profiles/image_general/preview", json={}).json()
        assert [m["id"] for m in preview["pool"]] == [IMAGE]
        assert [m["id"] for m in preview["models"]] == [IMAGE]
        assert not {m["id"] for m in preview["next"]} & llms

    store = Store(workspace.db_path)
    try:
        found = control.profile(store, "image_general")
        assert found is not None
        ranking = rank_profile(workspace, store, found, deps=Deps(), snapshot="test")
        chain = control.chain_for(store, "image_general", ranking.ranks)
        assert chain is not None
        assert [chain.primary, *chain.fallbacks] == [IMAGE]
        assert set(chain.local) == {IMAGE}
    finally:
        store.close()
