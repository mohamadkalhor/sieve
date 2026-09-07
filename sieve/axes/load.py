"""Read `data/axes/<modality>/*.yaml` into `Axis`.

An axis is the vocabulary a profile speaks. Adding one is adding a file, and
adding a field to one is adding a line -- so the loader's job is to catch the
mistakes a person makes writing YAML, and say which file and which line-item
is wrong.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

from sieve.contracts import Axis, Modality

VALID_TRANSFORMS = ("identity", "neg_log", "log", "invert")


class AxisError(ValueError):
    """An axis file that cannot be read as an Axis."""

    def __init__(self, path: Path, reason: str) -> None:
        super().__init__(f"{path}: {reason}")
        self.path = path
        self.reason = reason


def axis_files(directory: str | Path, modality: Modality | None = None) -> list[Path]:
    root = Path(directory)
    if not root.is_dir():
        return []
    found = root.rglob("*.y*ml") if modality is None else (root / modality).glob("*.y*ml")
    return sorted(p for p in found if p.is_file())


def parse_axis(path: str | Path) -> Axis:
    file = Path(path)
    try:
        raw: Any = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise AxisError(file, f"not valid YAML: {exc}") from exc
    if not isinstance(raw, dict):
        raise AxisError(file, "the top level must be a mapping")

    raw.setdefault("name", file.stem)
    if "modality" not in raw and file.parent.name:
        raw["modality"] = file.parent.name
    raw.setdefault("label", str(raw["name"]).replace("_", " ").capitalize())
    raw.setdefault("describes", "")

    try:
        axis = Axis.model_validate(raw)
    except Exception as exc:
        raise AxisError(file, str(exc)) from exc

    for problem in check_axis(axis):
        raise AxisError(file, problem)
    return axis


def check_axis(axis: Axis) -> Iterator[str]:
    """Everything pydantic cannot say on its own."""
    if not axis.fields:
        yield f"axis {axis.name!r} names no fields"
    if not 0.0 < axis.min_coverage <= 1.0:
        yield f"axis {axis.name!r}: min_coverage must be greater than 0 and at most 1"

    for field in axis.fields:
        where = f"axis {axis.name!r} field {field.source}:{field.field}"
        if field.weight <= 0:
            yield f"{where}: weight must be greater than 0, got {field.weight}"
        if field.transform not in VALID_TRANSFORMS:
            yield f"{where}: unknown transform {field.transform!r}"
        if field.min_n is not None and field.min_n < 0:
            yield f"{where}: min_n cannot be negative"
        if field.phase < 1:
            yield f"{where}: phase must be 1 or more"

    live = [f for f in axis.fields if f.phase == 1]
    if not live:
        yield (
            f"axis {axis.name!r}: every field is phase 2 or later, so the axis can "
            "never be measured today"
        )


def load_axes(directory: str | Path, modality: Modality | None = None) -> list[Axis]:
    """Every axis for one modality (or all of them), sorted by name."""
    axes = [parse_axis(file) for file in axis_files(directory, modality)]
    if modality is not None:
        axes = [a for a in axes if a.modality == modality]
    return sorted(axes, key=lambda a: (a.modality, a.name))


def load_all_axes(directory: str | Path) -> Iterator[Axis]:
    yield from load_axes(directory)


def axis_names(axes: list[Axis]) -> set[tuple[str, str]]:
    """`(modality, name)` pairs, the shape `validate_profile` wants."""
    return {(a.modality, a.name) for a in axes}
