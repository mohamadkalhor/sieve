"""Axes: reading the YAML, and turning source fields into one value per model."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from sieve.axes import axis_values, load_axes, parse_axis
from sieve.axes.compute import PENALTY_PERCENTILE, field_is_live
from sieve.axes.load import AxisError
from sieve.contracts import Axis, AxisField, Observation, ObsTable

NOW = datetime(2026, 9, 7, tzinfo=UTC)


def _obs(model_id: str, source: str, field: str, value: float, n: int | None = None) -> Observation:
    return Observation(
        model_id=model_id,
        modality="llm",
        source=source,
        field=field,
        value=value,
        unit="fraction",
        n=n,
        observed_at=NOW,
        pulled_at=NOW,
    )


def _table(*observations: Observation) -> ObsTable:
    table = ObsTable(modality="llm")
    for observation in observations:
        table.add(observation)
    return table


def _axis(**over: object) -> Axis:
    body: dict[str, object] = {
        "name": "agentic_coding",
        "modality": "llm",
        "label": "Agentic coding",
        "describes": "end-to-end software tasks",
        "fields": [
            AxisField(source="aa", field="terminalbench_v2_1", weight=0.7),
            AxisField(source="aa", field="livecodebench", weight=0.3),
        ],
    }
    body.update(over)
    return Axis.model_validate(body)


# --------------------------------------------------------------------------- #
# loading
# --------------------------------------------------------------------------- #


def test_an_axis_file_takes_its_name_and_modality_from_its_path(tmp_path: Path) -> None:
    file = tmp_path / "llm" / "agentic_coding.yaml"
    file.parent.mkdir(parents=True)
    file.write_text(
        "describes: end-to-end software tasks\n"
        "fields:\n"
        "  - {source: aa, field: terminalbench_v2_1, weight: 0.7}\n"
        "  - {source: aa, field: livecodebench, weight: 0.3}\n"
        "missing: renormalise\n"
        "min_coverage: 0.5\n",
        encoding="utf-8",
    )
    axis = parse_axis(file)
    assert axis.name == "agentic_coding"
    assert axis.modality == "llm"
    assert axis.label == "Agentic coding"
    assert [f.field for f in axis.fields] == ["terminalbench_v2_1", "livecodebench"]

    assert [a.name for a in load_axes(tmp_path, "llm")] == ["agentic_coding"]
    assert load_axes(tmp_path, "text-to-video") == []


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("fields: []\n", "names no fields"),
        (
            "fields:\n  - {source: aa, field: x, weight: 0}\n",
            "weight must be greater than 0",
        ),
        (
            "fields:\n  - {source: aa, field: x, weight: 1}\nmin_coverage: 0\n",
            "min_coverage",
        ),
        (
            "fields:\n  - {source: livebench, field: coding, weight: 1, phase: 2}\n",
            "never be measured today",
        ),
        ("fields:\n  - {source: aa, field: x, weight: 1, transform: sideways}\n", "transform"),
    ],
)
def test_a_bad_axis_file_names_the_file_and_the_problem(
    tmp_path: Path, body: str, expected: str
) -> None:
    file = tmp_path / "llm" / "broken.yaml"
    file.parent.mkdir(parents=True)
    file.write_text(f"describes: x\n{body}", encoding="utf-8")

    with pytest.raises((AxisError, ValueError)) as caught:
        parse_axis(file)
    assert expected in str(caught.value)
    assert "broken.yaml" in str(caught.value)


# --------------------------------------------------------------------------- #
# computing
# --------------------------------------------------------------------------- #


def test_an_axis_weights_percentiles_not_raw_values() -> None:
    obs = _table(
        _obs("a/1", "aa", "terminalbench_v2_1", 0.90),
        _obs("a/1", "aa", "livecodebench", 0.10),
        _obs("b/2", "aa", "terminalbench_v2_1", 0.50),
        _obs("b/2", "aa", "livecodebench", 0.90),
        _obs("c/3", "aa", "terminalbench_v2_1", 0.10),
        _obs("c/3", "aa", "livecodebench", 0.50),
    )
    values = axis_values(_axis(), obs, ["a/1", "b/2", "c/3"])

    # a/1 is best on the 0.7 field and worst on the 0.3 field: 0.7*1 + 0.3*0
    assert values["a/1"][0] == pytest.approx(0.7)
    assert values["b/2"][0] == pytest.approx(0.5 * 0.7 + 1.0 * 0.3)
    assert values["c/3"][0] == pytest.approx(0.0 * 0.7 + 0.5 * 0.3)
    assert all(coverage == 1.0 for _value, coverage in values.values())


def test_renormalise_judges_a_model_on_what_was_measured() -> None:
    obs = _table(
        _obs("a/1", "aa", "terminalbench_v2_1", 0.90),
        _obs("b/2", "aa", "terminalbench_v2_1", 0.10),
        _obs("b/2", "aa", "livecodebench", 0.50),
    )
    values = axis_values(_axis(missing="renormalise"), obs, ["a/1", "b/2"])

    value, coverage = values["a/1"]
    assert coverage == pytest.approx(0.7), "only the 0.7 field was measured"
    assert value == pytest.approx(1.0), "and on that field it is the best"


def test_penalise_scores_a_missing_field_at_the_declared_percentile() -> None:
    obs = _table(
        _obs("a/1", "aa", "terminalbench_v2_1", 0.90),
        _obs("b/2", "aa", "terminalbench_v2_1", 0.10),
        _obs("b/2", "aa", "livecodebench", 0.50),
    )
    values = axis_values(_axis(missing="penalise", min_coverage=0.5), obs, ["a/1", "b/2"])

    value, coverage = values["a/1"]
    assert coverage == pytest.approx(0.7), "the coverage loss is reported either way"
    assert value == pytest.approx(0.7 * 1.0 + 0.3 * PENALTY_PERCENTILE)


def test_below_min_coverage_the_axis_is_unmeasured_not_zero() -> None:
    obs = _table(
        _obs("a/1", "aa", "livecodebench", 0.90),
        _obs("b/2", "aa", "terminalbench_v2_1", 0.10),
        _obs("b/2", "aa", "livecodebench", 0.50),
    )
    values = axis_values(_axis(min_coverage=0.5), obs, ["a/1", "b/2"])

    value, coverage = values["a/1"]
    assert coverage == pytest.approx(0.3)
    assert value is None, "unmeasured is not the same as bad, and must not score 0"


def test_min_n_treats_a_thin_sample_as_unmeasured() -> None:
    axis = _axis(
        fields=[AxisField(source="aa_media", field="elo", weight=1.0, min_n=500)],
        min_coverage=0.5,
    )
    obs = _table(
        _obs("a/1", "aa_media", "elo", 1300.0, n=4000),
        _obs("b/2", "aa_media", "elo", 1290.0, n=12),
    )
    values = axis_values(axis, obs, ["a/1", "b/2"])

    assert values["b/2"] == (None, 0.0), "12 votes is not a measurement"
    assert values["a/1"][1] == 1.0


def test_a_phase_two_field_waits_for_its_source_then_counts() -> None:
    axis = _axis(
        fields=[
            AxisField(source="aa", field="terminalbench_v2_1", weight=0.5),
            AxisField(source="livebench", field="coding", weight=0.5, phase=2),
        ]
    )
    pool = ["a/1", "b/2"]

    quiet = _table(
        _obs("a/1", "aa", "terminalbench_v2_1", 0.9),
        _obs("b/2", "aa", "terminalbench_v2_1", 0.1),
    )
    assert not field_is_live(axis.fields[1], quiet, pool)
    quiet_values = axis_values(axis, quiet, pool)
    assert quiet_values["a/1"][1] == 1.0, "an absent phase-2 source must not cost coverage"

    landed = _table(
        _obs("a/1", "aa", "terminalbench_v2_1", 0.9),
        _obs("b/2", "aa", "terminalbench_v2_1", 0.1),
        _obs("a/1", "livebench", "coding", 0.4),
        _obs("b/2", "livebench", "coding", 0.8),
    )
    assert field_is_live(axis.fields[1], landed, pool)
    assert axis_values(axis, landed, pool)["a/1"][0] == pytest.approx(0.5)


def test_a_lower_is_better_axis_is_inverted_at_the_end() -> None:
    axis = _axis(
        name="latency",
        fields=[AxisField(source="aa", field="ttft", weight=1.0)],
        higher_is_better=False,
    )
    obs = _table(_obs("fast/1", "aa", "ttft", 0.5), _obs("slow/2", "aa", "ttft", 5.0))
    values = axis_values(axis, obs, ["fast/1", "slow/2"])
    assert values["fast/1"][0] == pytest.approx(1.0)
    assert values["slow/2"][0] == pytest.approx(0.0)


def test_neg_log_on_cost_makes_the_cheap_model_score_high() -> None:
    axis = _axis(
        name="cost",
        fields=[AxisField(source="openrouter", field="blended", weight=1.0, transform="neg_log")],
    )
    obs = _table(
        _obs("cheap/1", "openrouter", "blended", 0.20),
        _obs("mid/2", "openrouter", "blended", 3.00),
        _obs("dear/3", "openrouter", "blended", 60.00),
    )
    values = axis_values(axis, obs, ["cheap/1", "mid/2", "dear/3"])
    assert values["cheap/1"][0] == pytest.approx(1.0)
    assert values["dear/3"][0] == pytest.approx(0.0)


def test_a_model_nobody_measured_reports_no_coverage() -> None:
    obs = _table(_obs("a/1", "aa", "terminalbench_v2_1", 0.9))
    values = axis_values(_axis(), obs, ["a/1", "ghost/9"])
    assert values["ghost/9"] == (None, 0.0)
