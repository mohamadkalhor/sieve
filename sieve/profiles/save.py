"""Write a `Profile` back to its YAML file.

A profile is a file in the repo *and* a row the API can change, so a write from
the API has to leave the file a human still wants to read: key order and
comments survive wherever the file already exists (ruamel round-trip), and a new
file is written in the order of PLAN section 4.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

from sieve.contracts import Profile
from sieve.profiles.load import profile_path

KEY_ORDER = ("name", "modality", "purpose", "weights", "require", "shape", "policy", "targets")


def _yaml() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.indent(mapping=2, sequence=4, offset=2)
    y.width = 88
    return y


def as_mapping(profile: Profile) -> dict[str, Any]:
    """The profile as YAML sees it: `shape` uses the `in`/`out` spelling, and
    every empty optional is dropped rather than written as null."""
    body = profile.model_dump(mode="json", exclude_defaults=False)

    shape = {k: v for k, v in (body.get("shape") or {}).items() if v is not None}
    if "in_tokens" in shape:
        shape["in"] = shape.pop("in_tokens")
    if "out_tokens" in shape:
        shape["out"] = shape.pop("out_tokens")
    body["shape"] = shape

    for key in ("require", "shape", "targets"):
        if not body.get(key):
            body.pop(key, None)

    return {k: body[k] for k in KEY_ORDER if k in body}


def _merge(existing: Any, wanted: dict[str, Any]) -> Any:
    """Update the round-tripped document in place, so comments stay put."""
    for key, value in wanted.items():
        if isinstance(value, dict) and isinstance(existing.get(key), dict):
            _merge(existing[key], value)
        else:
            existing[key] = value
    for key in [k for k in existing if k not in wanted]:
        del existing[key]
    return existing


def dump_profile(profile: Profile, existing_text: str | None = None) -> str:
    yaml = _yaml()
    wanted = as_mapping(profile)
    document: Any = wanted
    if existing_text:
        loaded = yaml.load(existing_text)
        if isinstance(loaded, dict):
            document = _merge(loaded, wanted)
    stream = io.StringIO()
    yaml.dump(document, stream)
    return stream.getvalue()


def save_profile(directory: str | Path, profile: Profile) -> Path:
    """Write the profile; returns the path written."""
    path = profile_path(directory, profile)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else None
    path.write_text(dump_profile(profile, existing), encoding="utf-8", newline="\n")
    return path
