"""`data/aliases.yaml`: the ids a person has told Sieve are the same model.

    anthropic/claude-opus-5:
      - claude-opus-5
      - claude-opus-5-20260101

A hand-written alias beats every fuzzy rule in `match.py`, which is the point:
it is how a user fixes a match Sieve got wrong or would not guess.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_aliases(path: str | Path) -> dict[str, str]:
    """alias -> canonical id. A missing file is simply no aliases."""
    file = Path(path)
    if not file.is_file():
        return {}
    raw: Any = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"{file}: the top level must be a mapping of canonical id to aliases")

    out: dict[str, str] = {}
    for canonical, listed in raw.items():
        if listed is None:
            continue
        names = [listed] if isinstance(listed, str) else list(listed)
        for alias in names:
            if not isinstance(alias, str):
                raise ValueError(f"{file}: alias {alias!r} under {canonical!r} is not a string")
            out[alias] = str(canonical)
    return out


def by_canonical(aliases: dict[str, str]) -> dict[str, list[str]]:
    """The file's own shape: canonical id -> sorted aliases."""
    grouped: dict[str, list[str]] = {}
    for alias, canonical in aliases.items():
        grouped.setdefault(canonical, []).append(alias)
    return {canonical: sorted(names) for canonical, names in sorted(grouped.items())}


def save_aliases(path: str | Path, aliases: dict[str, str]) -> Path:
    """Write the file back, sorted, so a diff is readable."""
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(by_canonical(aliases), sort_keys=True, default_flow_style=False)
    header = "# alias file: canonical id -> the ids a source or gateway uses for it.\n"
    file.write_text(header + body, encoding="utf-8", newline="\n")
    return file
