"""Matching a gateway's model id to a canonical one.

Two passes. The first is lossless-ish: case, punctuation and the vendor prefix
only. The second strips the decorations providers add -- a date stamp, `:free`,
`-preview` -- and is worth less, because `-preview` really can be a different
model. Nothing below 0.8 is a match: an unmatched id is shown on the Sources
screen for a person to alias, never guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: confidence per rule, highest first. Anything below MIN_CONFIDENCE is no match.
EXACT = 1.0
ALIAS = 0.95
NORMALISED_FULL = 0.9
NORMALISED_SLUG = 0.85
STRIPPED = 0.8
MIN_CONFIDENCE = 0.8

#: decorations a provider adds to the same underlying model.
_DATE = re.compile(r"[-_]?(?:20\d{6}|20\d{4}|\d{6})$")
_TAGS = re.compile(r"(?::(?:free|nitro|beta|extended|floor|online))+$")
_SUFFIX = re.compile(r"[-_](?:preview|latest|beta|exp|experimental|stable|new)$")


def slug_of(model_id: str) -> str:
    """The part after the last vendor prefix: `oc-go/glm-5.3` -> `glm-5.3`."""
    return model_id.rsplit("/", 1)[-1]


def normalise(model_id: str) -> str:
    """Fold case, versions and punctuation: `Claude-Opus-4.6` -> `claudeopus46`.

    `claude-opus-4-6` and `claude-opus-4.6` land on the same key, which is the
    single most common shape difference between a gateway and a benchmark.
    """
    text = model_id.strip().lower().replace(".", "-")
    return re.sub(r"[^a-z0-9]", "", text)


def strip_decorations(model_id: str) -> str:
    """Second pass: drop a trailing date stamp, `:free`-style tags and
    `-preview`-style suffixes. Lossy, so it is worth less confidence."""
    text = model_id.strip().lower()
    text = _TAGS.sub("", text)
    previous = None
    while previous != text:
        previous = text
        text = _DATE.sub("", text)
        text = _SUFFIX.sub("", text)
    return text


@dataclass(frozen=True)
class Match:
    model_id: str | None
    confidence: float
    rule: str

    def __bool__(self) -> bool:
        return self.model_id is not None


class Matcher:
    """Indexes the catalog once, then answers many ids.

    `aliases` maps an alias to a canonical id; it wins over every fuzzy rule
    because a person wrote it.
    """

    def __init__(self, canonical_ids: list[str], aliases: dict[str, str] | None = None) -> None:
        self.canonical = list(canonical_ids)
        self.aliases = dict(aliases or {})

        self._by_id = {c: c for c in self.canonical}
        self._by_norm: dict[str, list[str]] = {}
        self._by_slug: dict[str, list[str]] = {}
        self._by_stripped: dict[str, list[str]] = {}
        for canonical in self.canonical:
            self._by_norm.setdefault(normalise(canonical), []).append(canonical)
            self._by_slug.setdefault(normalise(slug_of(canonical)), []).append(canonical)
            self._by_stripped.setdefault(
                normalise(strip_decorations(slug_of(canonical))), []
            ).append(canonical)

        self._alias_norm = {normalise(a): c for a, c in self.aliases.items()}

    def knows(self, model_id: str) -> bool:
        """True when this id is already canonical in the catalog."""
        return model_id in self._by_id

    def match(self, local_id: str) -> Match:
        """`(model_id, confidence)` for a gateway id, or a falsy Match."""
        if local_id in self._by_id:
            return Match(local_id, EXACT, "exact")
        if local_id in self.aliases:
            return Match(self.aliases[local_id], ALIAS, "alias")

        key = normalise(local_id)
        if key in self._alias_norm:
            return Match(self._alias_norm[key], ALIAS, "alias")

        for index, confidence, rule in (
            (self._by_norm, NORMALISED_FULL, "normalised"),
            (self._by_slug, NORMALISED_SLUG, "slug"),
        ):
            hit = self._unique(index, key)
            if hit:
                return Match(hit, confidence, rule)

        slug_key = normalise(slug_of(local_id))
        hit = self._unique(self._by_slug, slug_key)
        if hit:
            return Match(hit, NORMALISED_SLUG, "slug")

        stripped = normalise(strip_decorations(slug_of(local_id)))
        hit = self._unique(self._by_stripped, stripped)
        if hit:
            return Match(hit, STRIPPED, "stripped")

        return Match(None, 0.0, "none")

    @staticmethod
    def _unique(index: dict[str, list[str]], key: str) -> str | None:
        """Ambiguity is not a match: two canonical ids on one key means neither."""
        found = index.get(key)
        return found[0] if found and len(found) == 1 else None


def match(
    local_id: str, canonical_ids: list[str], aliases: dict[str, str] | None = None
) -> tuple[str | None, float]:
    """One-shot form of `Matcher.match` (CONTRACTS section: catalog/match)."""
    result = Matcher(canonical_ids, aliases).match(local_id)
    if result.confidence < MIN_CONFIDENCE:
        return None, result.confidence
    return result.model_id, result.confidence
