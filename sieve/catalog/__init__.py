"""The canonical model registry, aliases and id matching."""

from sieve.catalog.aliases import load_aliases, save_aliases
from sieve.catalog.match import Match, Matcher, match, normalise
from sieve.catalog.registry import Registry, canonical_id, model_ref

__all__ = [
    "Match",
    "Matcher",
    "Registry",
    "canonical_id",
    "load_aliases",
    "match",
    "model_ref",
    "normalise",
    "save_aliases",
]
