"""Read `profiles/<modality>/*.yaml` into `Profile`."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from sieve.contracts import Profile


class ProfileError(ValueError):
    """A profile file that cannot be read as a Profile."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


def profile_files(directory: str | Path) -> list[Path]:
    """Every profile file, sorted, so a run is reproducible."""
    root = Path(directory)
    if not root.is_dir():
        return []
    return sorted(p for p in root.rglob("*.y*ml") if p.is_file())


def parse_profile(path: str | Path) -> Profile:
    file = Path(path)
    try:
        raw: Any = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProfileError(file, f"not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise ProfileError(file, "the top level must be a mapping")
    raw.setdefault("name", file.stem)
    if "modality" not in raw and file.parent.name:
        raw["modality"] = file.parent.name
    try:
        return Profile.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError, reported with the path
        raise ProfileError(file, str(exc)) from exc


def load_profiles(directory: str | Path) -> Iterator[Profile]:
    """Every profile under `directory`. Raises on the first unreadable file."""
    for file in profile_files(directory):
        yield parse_profile(file)


def profile_path(directory: str | Path, profile: Profile) -> Path:
    """Where a profile lives: `<dir>/<modality>/<name>.yaml`."""
    return Path(directory) / profile.modality / f"{profile.name}.yaml"
