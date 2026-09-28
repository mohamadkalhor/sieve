"""A call with no owner on a box that has one seeds as the owner.

`sieve check` from a shell seeded as "nobody" and wrote an unowned copy of
every profile; the owner reads the whole box, so every seat showed twice and
the Seats screen crashed on the repeated names.
"""

from __future__ import annotations

from sieve import owners
from sieve.config import Config
from sieve.profiles import control
from sieve.store import Store
from tests.test_multiuser import OWNER_EMAIL, box  # noqa: F401  (the fixture)


def test_an_ownerless_seed_on_an_owned_box_adds_nothing(box: Config) -> None:  # noqa: F811
    store = Store(box.db_path)
    boss = owners.sign_in(store, OWNER_EMAIL, "owner", box.profiles_dir)
    control.seed(store, box.profiles_dir, boss.id)
    before = store.db.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
    assert before

    control.seed(store, box.profiles_dir)  # what `sieve check` does

    rows = store.db.execute("SELECT COUNT(*), COUNT(owner_id) FROM profiles").fetchone()
    assert rows[0] == before, "no second copy"
    assert rows[1] == before, "no unowned rows"
    names = [p.name for p in control.profiles(store, boss.id)]
    assert len(names) == len(set(names))


def test_a_box_nobody_owns_still_seeds_unowned(box: Config) -> None:  # noqa: F811
    store = Store(box.db_path)
    control.seed(store, box.profiles_dir)
    rows = store.db.execute("SELECT COUNT(*), COUNT(owner_id) FROM profiles").fetchone()
    assert rows[0] and rows[1] == 0
