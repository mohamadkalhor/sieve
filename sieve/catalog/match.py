"""Matching a gateway's model id to a canonical one.

Two passes. The first is lossless-ish: case, punctuation and the vendor prefix
only. The second strips the decorations providers add -- a date stamp, `:free`,
`-preview` -- and is worth less, because `-preview` really can be a different
model. Nothing below 0.8 is a match: an unmatched id is shown on the Sources
screen for a person to alias, never guessed at.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

#: confidence per rule, highest first. Anything below MIN_CONFIDENCE is no match.
EXACT = 1.0
ALIAS = 0.95
NORMALISED_FULL = 0.9
NORMALISED_SLUG = 0.85
STRIPPED = 0.8
#: a benchmarked twin found by `family_key` (see `Matcher.scored_twin`)
FAMILY = 0.8
MIN_CONFIDENCE = 0.8

#: decorations a provider adds to the same underlying model.
_DATE = re.compile(r"[-_]?(?:20\d{6}|20\d{4}|\d{6})$")
# `:batch` joins the list for the same reason `:free` is on it: both name a
# serving tier for a model the catalogue already holds, not a different
# model. Twelve OpenRouter ids in the 2026-09-08 recording -- every
# `<model>:batch` -- matched nothing at all without it.
_TAGS = re.compile(r"(?::(?:free|nitro|beta|extended|floor|online|batch))+$")
_SUFFIX = re.compile(r"[-_](?:preview|latest|beta|exp|experimental|stable|new)$")


def slug_of(model_id: str) -> str:
    """The part after the last vendor prefix: `oc-go/glm-5.3` -> `glm-5.3`."""
    return model_id.rsplit("/", 1)[-1]


#: Everything that separates the parts of a model name: whitespace, an
#: underscore, and -- the one that caused the bug -- a dot. `Wan 3.0` and
#: `wan-3-0` are one model, and until this rule was shared they became two
#: canonical ids sitting side by side in the catalogue, because `canonical_id`
#: folded whitespace and underscores while `normalise` also folded the dot.
#: A fal id with a dot could then never meet an AA id with a dash.
SEPARATORS = re.compile(r"[\s_.]+")


def fold_separators(text: str) -> str:
    """Lowercase, and every separator folded to a single dash.

    The one place that decides what a separator is. `canonical_id` builds a
    readable id from this; `normalise` strips the dashes out afterwards to make
    a comparison key. Two callers, one rule.
    """
    return SEPARATORS.sub("-", text.strip().lower())


def normalise(model_id: str) -> str:
    """Fold case, versions and punctuation: `Claude-Opus-4.6` -> `claudeopus46`.

    `claude-opus-4-6` and `claude-opus-4.6` land on the same key, which is the
    single most common shape difference between a gateway and a benchmark.
    """
    return re.sub(r"[^a-z0-9]", "", fold_separators(model_id))


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


#: Words that describe a packaging of a model, not a different model. `-it` and
#: `-instruct` are the tuned release every router serves; a benchmark lists the
#: same model without them (`gemma-4-31b-it` is AA's `gemma-4-31b`).
_FILLER = frozenset({"it", "instruct", "chat", "preview", "exp", "experimental", "latest"})
#: A vendor's name repeated inside the slug: AA's `nvidia-nemotron-3-super`
#: is a router's `nemotron-3-super`. Dropped only when other words remain.
_VENDORS = frozenset({"nvidia", "google", "meta", "anthropic", "openai", "mistralai", "microsoft"})
#: The reasoning switch. A second, weaker pass drops it, for the model a
#: benchmark lists only once (`nemotron-3-nano-omni-30b-a3b`) while a router
#: names its reasoning mode.
_MODE = frozenset({"reasoning", "thinking"})
#: A parameter count, total or active: `31b`, `a12b`, `1.1b`, `550b`.
_SIZE = re.compile(r"^a?\d+(?:p\d+)?[bm]$")


def family_key(model_id: str, *, drop_size: bool = False, drop_mode: bool = False) -> str:
    """The words of a model's name with their order taken out.

    `claude-haiku-4-5-20251001` and `claude-4-5-haiku`, or
    `llama-3.2-1b-instruct` and `llama-3-2-instruct-1b`, are one model written
    two ways, and no rule above can see it because each keeps the words in
    order. This key keeps what tells two models apart -- every word, every
    size, and the version numbers *in their order*, so `4-5` never meets `5-4`
    -- and drops only order, packaging words and a repeated vendor name.
    """
    text = strip_decorations(slug_of(model_id))
    # a size with a decimal point, `1.1b`, is one token: mark the point before
    # the dots fold, or `1.1b` reads as version `1` and size `1b`
    text = re.sub(r"(?<![\d.])(\d+)\.(\d+[bm])(?![a-z0-9])", r"\1p\2", text)
    text = re.sub(r"[^a-z0-9-]", "", fold_separators(text))
    words: list[str] = []
    numbers: list[str] = []
    sizes: list[str] = []
    for token in (t for t in text.split("-") if t):
        if token.isdigit():
            numbers.append(token)
        elif _SIZE.match(token):
            sizes.append(token)
        elif token in _FILLER or (drop_mode and token in _MODE):
            continue
        else:
            words.append(token)
    named = [w for w in words if w not in _VENDORS]
    words = named or words
    return "|".join(
        (" ".join(sorted(words)), ".".join(numbers), "" if drop_size else " ".join(sorted(sizes)))
    )


def has_size(model_id: str) -> bool:
    """True when the name states a parameter count."""
    return family_key(model_id).rsplit("|", 1)[-1] != ""


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
        # An alias is usually written as a full canonical id, while a gateway
        # id carries the gateway's own vendor prefix -- so `oc-go/x` never
        # equals the alias `creator/x` however it is normalised. Index the
        # alias slugs as well, which is the same courtesy `_by_slug` extends
        # to canonical ids.
        self._alias_slug: dict[str, list[str]] = {}
        for alias, canonical in self.aliases.items():
            self._alias_slug.setdefault(normalise(slug_of(alias)), []).append(canonical)

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

        hit = self._unique(self._alias_slug, normalise(slug_of(local_id)))
        if hit:
            return Match(hit, NORMALISED_SLUG, "alias-slug")

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
        """Ambiguity is not a match: two canonical ids on one key means neither.

        Two *entries* on one key are not ambiguous when they name the same
        model. A row often reaches the same key twice -- its published name
        and its spelled-out mode both normalise to `gemini38flashhigh` -- and
        counting that as a conflict rejected a match that was never in doubt.
        """
        found = set(index.get(key) or ())
        return next(iter(found)) if len(found) == 1 else None


class ScoredTwins:
    """Finds the benchmarked record for a router id the catalogue holds unscored.

    A catalogue fills from several sources, and they name one model differently:
    OpenRouter lists `anthropic/claude-haiku-4.5` with a price and no scores,
    Artificial Analysis lists `anthropic/claude-4-5-haiku` with scores and a
    different word order. A router id matches the first exactly and the model
    reads as unmeasured. This looks among the *scored* ids only, by
    `family_key`, and answers only when exactly one fits.

    Three passes, loosest last: the whole key; the key without a reasoning
    word; and the key without a size, against scored names that state no size
    at all -- AA's `mistral-small-3-1` is the one model a router calls
    `mistral-small-3.1-24b-instruct`, but a scored `gemma-4-12b` is never
    offered for a `gemma-4-31b`.
    """

    def __init__(self, scored_ids: Iterable[str]) -> None:
        self._full: dict[str, list[str]] = {}
        self._no_mode: dict[str, list[str]] = {}
        self._sizeless: dict[str, list[str]] = {}
        for scored in scored_ids:
            self._full.setdefault(family_key(scored), []).append(scored)
            self._no_mode.setdefault(family_key(scored, drop_mode=True), []).append(scored)
            if not has_size(scored):
                self._sizeless.setdefault(family_key(scored, drop_size=True), []).append(scored)

    def find(self, local_id: str) -> Match:
        for index, key, rule in (
            (self._full, family_key(local_id), "family"),
            (self._no_mode, family_key(local_id, drop_mode=True), "family-mode"),
            (self._sizeless, family_key(local_id, drop_size=True), "family-size"),
        ):
            if key.startswith("|"):
                continue  # no words at all: nothing to compare
            hit = Matcher._unique(index, key)
            if hit:
                return Match(hit, FAMILY, rule)
        return Match(None, 0.0, "none")


def match(
    local_id: str, canonical_ids: list[str], aliases: dict[str, str] | None = None
) -> tuple[str | None, float]:
    """One-shot form of `Matcher.match` (CONTRACTS section: catalog/match)."""
    result = Matcher(canonical_ids, aliases).match(local_id)
    if result.confidence < MIN_CONFIDENCE:
        return None, result.confidence
    return result.model_id, result.confidence
