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


def test_a_seat_multiplier_overrides_the_default_prefix_by_prefix() -> None:
    from sieve.profiles.control import update_settings

    after, _ = update_settings(ProfileSettings(), {"cost_multipliers": {"cc": 0.1, "ag": None}})
    assert after.cost_multipliers == {"cc": 0.1}
    cleared, _ = update_settings(after, {"cost_multipliers": {}})
    assert cleared.cost_multipliers == {}
