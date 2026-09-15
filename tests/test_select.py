"""The hand controls over a list: pins, removals, a manual list, and needs."""

from __future__ import annotations

from types import SimpleNamespace

from sieve.contracts import Capability, ProfileSettings
from sieve.scoring.select import behind, has, select


def _ranked(*ids: str) -> list[SimpleNamespace]:
    rows = [SimpleNamespace(model_id=m, position=i + 1) for i, m in enumerate(ids)]
    return [*rows, SimpleNamespace(model_id="far/away", position=0)]


RANKED = _ranked("a", "b", "c", "d", "e")


def _ids(rows: list[SimpleNamespace]) -> list[str]:
    return [r.model_id for r in rows]


def test_auto_is_the_top_by_score() -> None:
    assert _ids(select(RANKED, ProfileSettings(ship=3)).rows) == ["a", "b", "c"]


def test_there_is_no_upper_bound_on_ship() -> None:
    assert ProfileSettings(ship=40).ship == 40
    assert _ids(select(RANKED, ProfileSettings(ship=40)).rows) == ["a", "b", "c", "d", "e"]


def test_pins_go_first_and_count_towards_the_length() -> None:
    chosen = select(RANKED, ProfileSettings(ship=3, pinned=["e", "c"]))
    assert _ids(chosen.rows) == ["e", "c", "a"]


def test_more_pins_than_ship_all_ship() -> None:
    chosen = select(RANKED, ProfileSettings(ship=1, pinned=["d", "e"]))
    assert _ids(chosen.rows) == ["d", "e"]


def test_a_removed_model_never_ships_and_the_next_one_moves_up() -> None:
    value = ProfileSettings(ship=2, removed=["a"])
    chosen = select(RANKED, value)
    assert _ids(chosen.rows) == ["b", "c"]
    assert "a" not in _ids(behind(RANKED, value, chosen))


def test_an_unreachable_pin_is_named_not_shipped() -> None:
    chosen = select(RANKED, ProfileSettings(ship=2, pinned=["far/away"]))
    assert chosen.missing == ["far/away"] and _ids(chosen.rows) == ["a", "b"]


def test_manual_is_exactly_the_list_in_its_order() -> None:
    value = ProfileSettings(mode="manual", manual=["d", "a", "d", "ghost"], ship=1)
    chosen = select(RANKED, value)
    assert _ids(chosen.rows) == ["d", "a"]
    assert chosen.missing == ["ghost"]


def test_a_need_is_met_only_when_it_is_known() -> None:
    caps = {
        "a": Capability(input_modalities=["text", "image"], reasoning=True),
        "b": Capability(input_modalities=["text"], reasoning=True),
        "c": Capability(),
    }
    assert has(caps["a"], "vision") is True
    assert has(caps["b"], "vision") is False
    assert has(caps["c"], "vision") is None
    chosen = select(RANKED, ProfileSettings(ship=5, needs=["vision"]), caps)
    assert _ids(chosen.rows) == ["a"]
    assert {"b", "c", "d", "e"} <= set(_ids(chosen.failed_needs))


def test_a_prefix_weight_is_stored_only_when_it_says_something() -> None:
    from sieve.profiles.control import update_settings

    body = {"prefix_weights": {"cc": 1.5, "ag": None, "cx": 1}}
    after, _ = update_settings(ProfileSettings(), body)
    assert after.prefix_weights == {"cc": 1.5}
    cleared, _ = update_settings(after, {"prefix_weights": {}})
    assert cleared.prefix_weights == {}


def test_a_prefix_weight_multiplies_the_score_through_the_best_router() -> None:
    from sieve.scoring.select import prefix_factor

    assert prefix_factor(["cx/a", "cc/a"], {}) == 1.0
    assert prefix_factor(["cx/a"], {"cx": 0.5}) == 0.5
    assert prefix_factor(["cx/a", "cc/a"], {"cx": 0.5, "cc": 2}) == 2
    assert prefix_factor(["cx/a", "cc/a"], {"cx": 0.5}) == 1.0, "cc is unnamed, so it counts 1"


def test_the_cached_rerank_applies_the_prefix_weights() -> None:
    from datetime import UTC, datetime

    from sieve.contracts import AxisScore, Rank, Ranking
    from sieve.profiles.control import rerank_cached

    def rank(model_id: str, value: float, local: str) -> Rank:
        axis = AxisScore(axis="q", value=value, coverage=1.0, contribution=value)
        return Rank(
            position=1,
            model_id=model_id,
            reachable=True,
            local_ids=[local],
            score=value,
            confidence=1.0,
            health=1.0,
            final=value,
            axes=[axis],
        )

    ranking = Ranking(
        profile="p",
        modality="llm",
        computed_at=datetime.now(UTC),
        snapshot="s",
        ranks=[rank("a", 0.9, "cx/a"), rank("b", 0.6, "cc/b")],
    )
    plain = rerank_cached(ranking, {"q": 1.0})
    assert [r.model_id for r in plain.ranks] == ["a", "b"]
    boosted = rerank_cached(ranking, {"q": 1.0}, {"cc": 2.0})
    assert [r.model_id for r in boosted.ranks] == ["b", "a"]
    assert boosted.ranks[0].final == 1.2 and boosted.ranks[0].score == 0.6
