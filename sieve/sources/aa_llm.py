"""Artificial Analysis — language models.

`GET https://artificialanalysis.ai/api/v2/data/llms/models` with `x-api-key`.
Free, 1 000 requests a day.

Every key under `evaluations` is stored with its name **verbatim**, including
ones this file has never heard of: Artificial Analysis puts a new benchmark on
the site before the API documents it, and an axis must be able to name it the
day it appears. Unknown keys come back as a warning, not as a silent drop.
"""

from __future__ import annotations

from typing import Any, ClassVar

from sieve.catalog.effort import effort_of, family_of
from sieve.catalog.registry import canonical_id
from sieve.contracts import (
    HttpClient,
    Modality,
    ModelRef,
    Observation,
    Price,
    PullResult,
    SourceConfig,
    Unit,
)
from sieve.sources.base import as_float, as_int, make_observation, utcnow

URL = "https://artificialanalysis.ai/api/v2/data/llms/models"

MODALITY: Modality = "llm"

#: the evaluation keys documented today, and the unit each is measured in.
#: Anything absent from here is still stored -- as a fraction, with a warning.
KNOWN_FIELDS: dict[str, Unit] = {
    "artificial_analysis_intelligence_index": "index_0_100",
    "artificial_analysis_coding_index": "index_0_100",
    "artificial_analysis_math_index": "index_0_100",
    "gpqa": "fraction",
    "hle": "fraction",
    "mmlu_pro": "fraction",
    "livecodebench": "fraction",
    "scicode": "fraction",
    "math_500": "fraction",
    "aime": "fraction",
    "aime_25": "fraction",
    "ifbench": "fraction",
    "lcr": "fraction",
    "terminalbench_hard": "fraction",
    "terminalbench_v2_1": "fraction",
    "tau2": "fraction",
    "tau_banking": "fraction",
}

#: throughput and latency live beside the evaluations, not inside them.
SPEED_FIELDS: dict[str, Unit] = {
    "median_output_tokens_per_second": "tokens_per_s",
    "median_time_to_first_token_seconds": "seconds",
}

PRICE_KEYS = {
    "input": ("price_1m_input_tokens", "price_1m_input", "input"),
    "output": ("price_1m_output_tokens", "price_1m_output", "output"),
    "cached_input": ("price_1m_cached_input_tokens", "price_1m_cached_input", "cached_input"),
    "blended": ("price_1m_blended_3_to_1", "price_1m_blended", "blended"),
}


def unit_of(field: str) -> Unit:
    """The documented unit, or `fraction` for a benchmark we have not met."""
    if field in KNOWN_FIELDS:
        return KNOWN_FIELDS[field]
    if field.endswith("_index"):
        return "index_0_100"
    return "fraction"


def model_id_of(entry: dict[str, Any]) -> str | None:
    """`<model_creator.slug>/<slug>`, as the brief specifies."""
    slug = str(entry.get("slug") or entry.get("id") or "").strip()
    if not slug:
        return None
    creator = entry.get("model_creator") or {}
    creator_slug = ""
    if isinstance(creator, dict):
        creator_slug = str(creator.get("slug") or creator.get("name") or "").strip()
    elif isinstance(creator, str):
        creator_slug = creator.strip()
    return canonical_id(creator_slug, slug) if creator_slug else canonical_id("", slug)


def _price_of(entry: dict[str, Any], model_id: str, at: Any) -> Price | None:
    pricing = entry.get("pricing")
    if not isinstance(pricing, dict):
        return None
    found: dict[str, float | None] = {}
    for name, keys in PRICE_KEYS.items():
        for key in keys:
            value = as_float(pricing.get(key))
            if value is not None:
                found[name] = value
                break
        else:
            found[name] = None
    if all(v is None for v in found.values()):
        return None
    return Price(
        model_id=model_id,
        source="aa_llm",
        unit="usd_per_1m_tokens",
        input=found["input"],
        output=found["output"],
        cached_input=found["cached_input"],
        per_unit=found["blended"],
        source_url=URL,
        observed_at=at,
    )


class AALLMSource:
    name = "aa_llm"
    modality: ClassVar[list[Modality]] = [MODALITY]
    needs_key = True

    def pull(self, cfg: SourceConfig, http: HttpClient) -> PullResult:
        at = utcnow()
        result = PullResult(source=self.name)

        key = cfg.key()
        headers = {"x-api-key": key} if key else {}
        response = http.get(URL, headers=headers)
        if response.status != 200:
            result.warnings.append(
                f"aa_llm: HTTP {response.status} from {URL}"
                + ("" if key else f" (no key in {cfg.key_env or 'the environment'})")
            )
            result.ok = False
            result.rate_limit = http.rate_limit()
            return result

        body = response.body if isinstance(response.body, dict) else {}
        raw = body.get("data")
        entries: list[Any] = raw if isinstance(raw, list) else []
        if not entries:
            result.warnings.append("aa_llm: the payload carried no `data` list")

        unknown: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            model_id = model_id_of(entry)
            if model_id is None:
                continue

            name = str(entry.get("name") or model_id)
            aliases = {
                str(entry.get("slug") or ""),
                str(entry.get("id") or ""),
                str(entry.get("name") or ""),
            } - {"", model_id}
            result.models.append(
                ModelRef(
                    id=model_id,
                    modality=MODALITY,
                    name=name,
                    creator=model_id.split("/", 1)[0],
                    aliases=sorted(aliases),
                    # PLAN 2.1: one row per effort mode, so the mode has to
                    # survive into the catalogue or a low-effort call gets
                    # credited with a high-effort score.
                    effort=effort_of(name),
                    family=family_of(model_id),
                )
            )

            result.observations.extend(self._observations(entry, model_id, at, unknown))

            price = _price_of(entry, model_id, at)
            if price is not None:
                result.prices.append(price)

        # a family's bare row also answers to its own spelled-out mode
        _name_the_bare_mode(result)

        if unknown:
            result.warnings.append(
                "aa_llm: stored "
                + str(len(unknown))
                + " evaluation field(s) this build has not met: "
                + ", ".join(sorted(unknown))
                + " -- add them to an axis in data/axes/llm/ when you want them counted"
            )

        result.rate_limit = http.rate_limit()
        return result

    def _observations(
        self, entry: dict[str, Any], model_id: str, at: Any, unknown: set[str]
    ) -> list[Observation]:
        out: list[Observation] = []

        evaluations = entry.get("evaluations")
        if isinstance(evaluations, dict):
            for field, value in evaluations.items():
                name = str(field)
                if name not in KNOWN_FIELDS:
                    unknown.add(name)
                observation = make_observation(
                    model_id=model_id,
                    modality=MODALITY,
                    source=self.name,
                    field=name,
                    value=value,
                    unit=unit_of(name),
                    observed_at=at,
                    pulled_at=at,
                )
                if observation is not None:
                    out.append(observation)

        for field, unit in SPEED_FIELDS.items():
            observation = make_observation(
                model_id=model_id,
                modality=MODALITY,
                source=self.name,
                field=field,
                value=entry.get(field),
                unit=unit,
                n=as_int(entry.get("median_samples")),
                observed_at=at,
                pulled_at=at,
            )
            if observation is not None:
                out.append(observation)

        return out


def _name_the_bare_mode(result: PullResult) -> None:
    """Give the bare row of a family the spelled-out name of its own mode.

    AA writes a family's top mode without a suffix, and which mode that is
    varies: `gpt-5-6-sol` is max, `gemini-3-8-flash` is high. A gateway,
    meanwhile, names every mode explicitly -- `gemini-3.8-flash-high`. Without
    an alias that id matches nothing, and the obvious repair (strip the suffix
    and match the base) is the exact bug PLAN 2.1 forbids, because it would
    also send `-low` to the high row.

    So the alias is written from the data: the bare row for a family that says
    it is `high` also answers to `<family>-high`. Never invented where AA
    already publishes a distinct row under that id.
    """
    taken = {m.id for m in result.models}
    for model in result.models:
        if model.effort is None or model.family is None or model.id != model.family:
            continue
        spelled = f"{model.family}-{model.effort}"
        if spelled in taken or spelled in model.aliases:
            continue
        model.aliases = sorted({*model.aliases, spelled})
