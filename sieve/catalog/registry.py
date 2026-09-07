"""The canonical model registry.

One record per model per modality, with every id a source or gateway has used
for it. The registry is the only place that decides "these two ids are the same
model", and it never decides it silently: an id it cannot place stays unmatched
and is shown on the Sources screen.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime

from sieve.catalog.match import MIN_CONFIDENCE, Match, Matcher
from sieve.contracts import Modality, ModelRef, PullResult, Reachable

_SEPARATORS = re.compile(r"[\s_]+")


def canonical_id(creator: str, slug: str) -> str:
    """`<creator>/<slug>`, lowercase — the id everything else refers to."""
    clean_creator = _SEPARATORS.sub("-", creator.strip().lower()).strip("-/")
    clean_slug = _SEPARATORS.sub("-", slug.strip().lower()).strip("-/")
    clean_slug = clean_slug.rsplit("/", 1)[-1]
    return f"{clean_creator}/{clean_slug}" if clean_creator else clean_slug


class Registry:
    """An in-memory catalog for one run: upsert models, then resolve local ids."""

    def __init__(self, aliases: dict[str, str] | None = None) -> None:
        self.models: dict[tuple[str, str], ModelRef] = {}
        self.aliases = dict(aliases or {})

    # -- building ------------------------------------------------------- #

    def add(self, model: ModelRef) -> ModelRef:
        """Merge a model into the registry, keeping every alias ever seen."""
        key = (model.id, model.modality)
        held = self.models.get(key)
        if held is None:
            self.models[key] = model.model_copy(deep=True)
            return self.models[key]
        merged = sorted({*held.aliases, *model.aliases} - {held.id})
        self.models[key] = held.model_copy(
            update={
                "aliases": merged,
                "name": model.name or held.name,
                "release_date": model.release_date or held.release_date,
            }
        )
        return self.models[key]

    def extend(self, models: Iterable[ModelRef]) -> int:
        count = 0
        for model in models:
            self.add(model)
            count += 1
        return count

    def ids(self, modality: Modality | None = None) -> list[str]:
        return sorted(
            model_id for (model_id, mod) in self.models if modality is None or mod == modality
        )

    def alias_map(self, modality: Modality | None = None) -> dict[str, str]:
        """Every alias the registry knows, plus the hand-written file."""
        out: dict[str, str] = {}
        for (model_id, mod), model in self.models.items():
            if modality is not None and mod != modality:
                continue
            for alias in model.aliases:
                out.setdefault(alias, model_id)
        out.update(self.aliases)
        return out

    # -- resolving ------------------------------------------------------ #

    def matcher(self, modality: Modality | None = None) -> Matcher:
        return Matcher(self.ids(modality), self.alias_map(modality))

    def resolve(self, local_id: str, modality: Modality | None = None) -> Match:
        return self.matcher(modality).match(local_id)

    def attach(
        self,
        reachable: Iterable[Reachable],
        modality: Modality | None = None,
        *,
        min_confidence: float = MIN_CONFIDENCE,
    ) -> tuple[list[Reachable], list[Reachable]]:
        """Fill in `model_id` where a match is confident enough.

        Returns `(matched, unmatched)`. An unmatched entry keeps `model_id`
        None and is listed on the Sources screen -- it is never dropped.
        """
        matcher = self.matcher(modality)
        matched: list[Reachable] = []
        unmatched: list[Reachable] = []
        for item in reachable:
            if item.model_id:
                matched.append(item)
                continue
            found = matcher.match(item.local_id)
            if found.model_id and found.confidence >= min_confidence:
                matched.append(item.model_copy(update={"model_id": found.model_id}))
            else:
                unmatched.append(item)
        return matched, unmatched


def model_ref(
    *,
    creator: str,
    slug: str,
    modality: Modality,
    name: str | None = None,
    aliases: Iterable[str] = (),
    release_date: datetime | None = None,
) -> ModelRef:
    """Build a ModelRef with a canonical id and the source's own id as an alias."""
    model_id = canonical_id(creator, slug)
    every = {a for a in aliases if a and a != model_id}
    return ModelRef(
        id=model_id,
        modality=modality,
        name=name or slug,
        creator=creator.strip().lower(),
        aliases=sorted(every),
        release_date=(
            release_date.astimezone(UTC).date() if isinstance(release_date, datetime) else None
        ),
    )


def merge_pull(
    result: PullResult,
    known_ids: list[str],
    aliases: dict[str, str] | None = None,
    *,
    min_confidence: float = 0.9,
) -> tuple[PullResult, dict[str, str]]:
    """Fold a pull onto ids the catalog already holds.

    Artificial Analysis calls a model `openai/gpt-5-2` and OpenRouter calls it
    `openai/gpt-5.2`. Left alone that is two catalog records: one carrying the
    benchmarks and one carrying the price and the capabilities, and a profile
    that asks for `tools: true` silently drops the benchmarked one.

    So a pull's ids are matched against what the catalog already knows before
    anything is stored. The id seen first stays canonical and the newcomer
    becomes an alias -- deterministic, and visible in the model's alias list.
    The bar is deliberately higher than the matcher's own floor: a wrong merge
    fuses two different models, which is far worse than two records a person
    can alias by hand.

    Returns the rewritten pull and `{new id: canonical id}` for what was folded.
    """
    if not known_ids:
        return result, {}

    matcher = Matcher(known_ids, aliases)
    rewrite: dict[str, str] = {}
    for model in result.models:
        if matcher.knows(model.id):
            continue
        found = matcher.match(model.id)
        if found.model_id and found.confidence >= min_confidence:
            rewrite[model.id] = found.model_id

    if not rewrite:
        return result, {}

    folded = result.model_copy(deep=True)
    folded.models = [
        m.model_copy(
            update={
                "id": rewrite[m.id],
                "aliases": sorted({*m.aliases, m.id} - {rewrite[m.id]}),
            }
        )
        if m.id in rewrite
        else m
        for m in folded.models
    ]
    for observation in folded.observations:
        observation.model_id = rewrite.get(observation.model_id, observation.model_id)
    for price in folded.prices:
        price.model_id = rewrite.get(price.model_id, price.model_id)
    folded.capabilities = {
        rewrite.get(model_id, model_id): capability
        for model_id, capability in folded.capabilities.items()
    }
    return folded, rewrite
