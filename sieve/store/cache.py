"""One built view of a snapshot, shared by everything that ranks.

Ranking rebuilt the world on every call. `obs_table("llm")` is a scan of every
observation in the modality -- 1,046,838 rows on the production store -- to end
up with the 7,536 that are the latest of their (model, source, field). Measured
read-only against the live database on 2026-09-14: 34 seconds a call, and
`rank_profile` cost 33.7 s, of which the table was all but a second. Preview,
apply, the leaderboard and the hourly run each paid it, on a request thread, at
the same time, which is how two cores and a 400 MB cap were exhausted by a
handful of clicks.

Nothing in a view depends on the request: the same snapshot yields the same
table for everybody. So it is built once per (snapshot, modality) and kept.
A new snapshot is a new key, so a pull invalidates by construction; `invalidate`
covers a run that changes rows without writing a snapshot, which is what a
harvest does.

The cache is small and bounded: `max_entries` views, evicted oldest first. One
llm view measures about 9 MB, so even every modality at once stays far inside
the 250 MB this is allowed to hold.
"""

from __future__ import annotations

import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sieve.contracts import Capability, Modality, ModelRef, ObsTable

if TYPE_CHECKING:  # pragma: no cover - the import would be a cycle at runtime
    from sieve.store.db import Store

#: How many (snapshot, modality) views to hold. Four covers a page showing one
#: modality while the loop ranks another, with room for the snapshot to turn
#: over mid-request, and is roughly 36 MB.
DEFAULT_MAX_ENTRIES = 4


def _max_entries() -> int:
    raw = os.environ.get("SIEVE_VIEW_CACHE")
    if raw and raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_MAX_ENTRIES


@dataclass(frozen=True)
class ModalityView:
    """What one snapshot says about one modality, built once.

    `obs` is shared, so no caller may write to it: `rank_profile` adds a cost
    observation per model and rewrites it for a router multiplier. `working`
    hands out the copy those writes belong in -- the two dict levels are copied,
    and an observation is replaced rather than mutated, so the observations
    themselves can be shared.
    """

    snapshot: str
    modality: Modality
    obs: ObsTable
    models: list[ModelRef]
    capabilities: dict[str, Capability]

    def working(self) -> ObsTable:
        return ObsTable(
            modality=self.obs.modality,
            latest={model_id: dict(bucket) for model_id, bucket in self.obs.latest.items()},
            prices=dict(self.obs.prices),
        )


class SnapshotCache:
    """The views this process holds: one build per key, however many ask."""

    def __init__(self, store: Store, *, max_entries: int | None = None) -> None:
        self._store = store
        self.max_entries = max_entries or _max_entries()
        self._views: OrderedDict[tuple[str, str], ModalityView] = OrderedDict()
        self._guard = threading.Lock()
        self._building: dict[tuple[str, str], threading.Lock] = {}
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        return len(self._views)

    @property
    def keys(self) -> list[tuple[str, str]]:
        with self._guard:
            return list(self._views)

    def view(self, modality: Modality, snapshot: str | None = None) -> ModalityView:
        key = (snapshot or self._store.latest_snapshot() or "none", str(modality))
        held = self._held(key)
        if held is not None:
            return held
        # One builder per key. A second thread asking for the same view waits
        # here and finds it built, rather than scanning the table beside it.
        with self._guard:
            lock = self._building.setdefault(key, threading.Lock())
        with lock:
            held = self._held(key)
            if held is not None:
                return held
            built = self._build(key[0], modality)
            with self._guard:
                self.misses += 1
                self._views[key] = built
                self._views.move_to_end(key)
                while len(self._views) > self.max_entries:
                    self._views.popitem(last=False)
                self._building.pop(key, None)
            return built

    def invalidate(self) -> None:
        """Forget everything. A run that changed rows calls this; a new
        snapshot needs no call, since it is already a different key."""
        with self._guard:
            self._views.clear()

    # ------------------------------------------------------------------ #

    def _held(self, key: tuple[str, str]) -> ModalityView | None:
        with self._guard:
            found = self._views.get(key)
            if found is not None:
                self._views.move_to_end(key)
                self.hits += 1
            return found

    def _build(self, snapshot: str, modality: Modality) -> ModalityView:
        return ModalityView(
            snapshot=snapshot,
            modality=modality,
            obs=self._store.obs_table(modality),
            models=self._store.models(modality),
            capabilities=self._store.capabilities(modality),
        )
