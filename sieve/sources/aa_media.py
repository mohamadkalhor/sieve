"""Artificial Analysis — image, video and speech models.

Five endpoints under `/api/v2/data/media/`, each `?include_categories=true`,
each its own modality. An arena Elo comes with `appearances` and a `ci95` that
the API writes as a string like `"-12/12"`; both are kept, because a model with
200 votes and a wide interval is not a safe primary and the `maturity` axis is
how a profile says so.

Each published category becomes its own field, `elo:<slug>` -- `elo:physics`,
`elo:moving_camera` -- so an axis can name one without this file knowing which
categories exist.

The free-tier music and speech endpoints are probed only when switched on in
`sieve.toml`; their shape is not documented, so whatever is numeric is stored
and anything else is a warning. They never fail the pull.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar, NamedTuple

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
from sieve.sources.base import as_float, as_int, make_observation, slugify, utcnow

BASE = "https://artificialanalysis.ai/api/v2/data/media"
FREE_BASE = "https://artificialanalysis.ai/api/v2/media"

#: endpoint path -> the modality it measures.
ENDPOINTS: dict[str, Modality] = {
    "text-to-image": "text-to-image",
    "image-editing": "image-editing",
    "text-to-video": "text-to-video",
    "image-to-video": "image-to-video",
    "text-to-speech": "text-to-speech",
}

#: free-tier endpoints, off unless `sieve.toml` turns them on.
FREE_ENDPOINTS: dict[str, Modality] = {
    "music/instrumental": "music",
    "music/with-vocals": "music",
    "speech-to-text": "speech-to-text",
    "text-to-speech": "text-to-speech",
}

#: the per-unit price key each modality publishes, and its unit.
PRICE_UNITS: dict[Modality, Unit] = {
    "text-to-image": "usd_per_image",
    "image-editing": "usd_per_image",
    "text-to-video": "usd_per_second",
    "image-to-video": "usd_per_second",
    "text-to-speech": "usd_per_1m_chars",
    "speech-to-text": "usd_per_1m_chars",
    "music": "usd_per_second",
}

PRICE_KEYS = (
    "price_per_image",
    "price_1k_images",
    "price_per_second",
    "price_1m_characters",
    "price_1m_chars",
    "price",
)

CATEGORY_KEYS = ("style_category", "subject_matter_category", "format_category", "categories")

_CI = re.compile(r"[-+]?\s*([0-9]*\.?[0-9]+)\s*/\s*[-+]?\s*([0-9]*\.?[0-9]+)")


def parse_ci95(raw: Any) -> float | None:
    """`"-12/12"` -> 12.0. A lopsided interval reports its wider half."""
    if raw is None:
        return None
    value = as_float(raw)
    if value is not None:
        return abs(value)
    found = _CI.search(str(raw))
    if not found:
        return None
    return max(abs(float(found.group(1))), abs(float(found.group(2))))


def model_id_of(entry: dict[str, Any]) -> str | None:
    slug = str(entry.get("slug") or entry.get("id") or entry.get("name") or "").strip()
    if not slug:
        return None
    creator = entry.get("model_creator") or entry.get("creator") or {}
    creator_slug = ""
    if isinstance(creator, dict):
        creator_slug = str(creator.get("slug") or creator.get("name") or "").strip()
    elif isinstance(creator, str):
        creator_slug = creator.strip()
    return canonical_id(creator_slug, slug)


class Category(NamedTuple):
    """One published per-category Elo, with its own sample size and interval."""

    field: str
    elo: float | None
    appearances: int | None
    ci95: float | None


def _categories(entry: dict[str, Any]) -> list[Category]:
    """Every published per-category Elo for one model.

    The arena endpoints return `categories` as a list, and each item names its
    category in exactly one of three columns -- `format_category`,
    `style_category`, `subject_matter_category` -- leaving the other two null.
    The parser used to look for a `name`/`category`/`slug` key, found none, and
    silently produced nothing at all, which is why per-category Elo looked like
    it worked against a hand-built fixture and vanished against the real API.

    Each item also carries its *own* `appearances` and `ci95`. Those are the
    honest ones: a model can have 1,056 votes on Physics and 392 on Moving
    camera, so reusing the model's overall figures would overstate both.

    The dict and flat forms below are kept because a `<key>: {name: elo}` shape
    is what a `style_category` column would look like if the API ever inlined
    it, and reading it costs nothing.
    """
    out: list[Category] = []
    seen: set[str] = set()

    def add(name: Any, elo: Any, appearances: Any = None, ci: Any = None) -> None:
        field = slugify(str(name))
        if not field or field in seen:
            return
        seen.add(field)
        out.append(Category(field, as_float(elo), as_int(appearances), parse_ci95(ci)))

    for key in CATEGORY_KEYS:
        block = entry.get(key)
        if isinstance(block, dict):
            for name, value in block.items():
                add(name, value)
        elif isinstance(block, str):
            add(block, entry.get("elo"), entry.get("appearances"), entry.get("ci95"))
        elif isinstance(block, list):
            for item in block:
                if not isinstance(item, dict):
                    continue
                name = (
                    item.get("format_category")
                    or item.get("style_category")
                    or item.get("subject_matter_category")
                    or item.get("name")
                    or item.get("category")
                    or item.get("slug")
                )
                value = item.get("elo", item.get("score", item.get("value")))
                if name is not None:
                    add(name, value, item.get("appearances"), item.get("ci95"))
    return out


def _price_of(entry: dict[str, Any], model_id: str, modality: Modality, at: Any) -> Price | None:
    pricing = entry.get("pricing") if isinstance(entry.get("pricing"), dict) else entry
    for key in PRICE_KEYS:
        value = as_float(pricing.get(key)) if isinstance(pricing, dict) else None
        if value is None:
            continue
        if key == "price_1k_images":
            value /= 1000.0
        return Price(
            model_id=model_id,
            source="aa_media",
            unit=PRICE_UNITS.get(modality, "usd_per_second"),
            per_unit=value,
            source_url=f"{BASE}/{modality}",
            observed_at=at,
        )
    return None


class AAMediaSource:
    name = "aa_media"
    modality: ClassVar[list[Modality]] = list(ENDPOINTS.values())
    needs_key = True

    def pull(self, cfg: SourceConfig, http: HttpClient) -> PullResult:
        at = utcnow()
        result = PullResult(source=self.name)
        key = cfg.key()
        headers = {"x-api-key": key} if key else {}

        wanted = [m for m in (cfg.modalities or list(ENDPOINTS.values()))]
        for path, modality in ENDPOINTS.items():
            if modality not in wanted:
                continue
            self._pull_arena(http, headers, path, modality, at, result)

        for path, modality in FREE_ENDPOINTS.items():
            option = f"free_{slugify(path)}"
            if not cfg.options.get(option, False):
                continue
            self._pull_free(http, headers, path, modality, at, result)

        result.rate_limit = http.rate_limit()
        return result

    # -- the documented arena endpoints --------------------------------- #

    def _pull_arena(
        self,
        http: HttpClient,
        headers: dict[str, str],
        path: str,
        modality: Modality,
        at: Any,
        result: PullResult,
    ) -> None:
        url = f"{BASE}/{path}"
        response = http.get(url, headers=headers, params={"include_categories": "true"})
        if response.status != 200:
            result.warnings.append(f"aa_media: HTTP {response.status} from {url}")
            return

        body = response.body if isinstance(response.body, dict) else {}
        raw = body.get("data")
        entries: list[Any] = raw if isinstance(raw, list) else []
        if not entries:
            result.warnings.append(f"aa_media: {path} carried no `data` list")
            return

        for entry in entries:
            if not isinstance(entry, dict):
                continue
            model_id = model_id_of(entry)
            if model_id is None:
                continue

            aliases = {str(entry.get("slug") or ""), str(entry.get("id") or "")} - {"", model_id}
            result.models.append(
                ModelRef(
                    id=model_id,
                    modality=modality,
                    name=str(entry.get("name") or model_id),
                    creator=model_id.split("/", 1)[0],
                    aliases=sorted(aliases),
                )
            )
            result.observations.extend(self._arena_observations(entry, model_id, modality, at))

            price = _price_of(entry, model_id, modality, at)
            if price is not None:
                result.prices.append(price)

    def _arena_observations(
        self, entry: dict[str, Any], model_id: str, modality: Modality, at: Any
    ) -> list[Observation]:
        appearances = as_int(entry.get("appearances") or entry.get("votes"))
        ci95 = parse_ci95(entry.get("ci95") or entry.get("ci_95") or entry.get("confidence"))

        out: list[Observation] = []
        for field, value, unit, n, ci in (
            ("elo", entry.get("elo") or entry.get("arena_elo"), "elo", appearances, ci95),
            ("rank", entry.get("rank"), "count", None, None),
            ("appearances", appearances, "count", None, None),
            ("ci95", ci95, "elo", None, None),
        ):
            observation = make_observation(
                model_id=model_id,
                modality=modality,
                source=self.name,
                field=field,
                value=value,
                unit=unit,  # type: ignore[arg-type]
                n=n,
                ci95=ci,
                observed_at=at,
                pulled_at=at,
            )
            if observation is not None:
                out.append(observation)

        for category in _categories(entry):
            observation = make_observation(
                model_id=model_id,
                modality=modality,
                source=self.name,
                field=f"elo:{category.field}",
                value=category.elo,
                unit="elo",
                # the category's own sample size and interval, not the model's
                n=category.appearances if category.appearances is not None else appearances,
                ci95=category.ci95 if category.ci95 is not None else ci95,
                observed_at=at,
                pulled_at=at,
            )
            if observation is not None:
                out.append(observation)

        return out

    # -- the undocumented free tier ------------------------------------- #

    def _pull_free(
        self,
        http: HttpClient,
        headers: dict[str, str],
        path: str,
        modality: Modality,
        at: Any,
        result: PullResult,
    ) -> None:
        """Probe an endpoint whose shape is not documented. Never fails the pull."""
        url = f"{FREE_BASE}/{path}/models/free"
        try:
            response = http.get(url, headers=headers)
        except Exception as exc:  # a probe must not take the pull down
            result.warnings.append(f"aa_media: {path} free tier unreachable: {exc}")
            return
        if response.status != 200:
            result.warnings.append(f"aa_media: {path} free tier answered HTTP {response.status}")
            return

        body = response.body
        entries = body.get("data") if isinstance(body, dict) else body
        if not isinstance(entries, list):
            result.warnings.append(f"aa_media: {path} free tier returned an unexpected shape")
            return

        stored = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            model_id = model_id_of(entry)
            if model_id is None:
                continue
            result.models.append(
                ModelRef(
                    id=model_id,
                    modality=modality,
                    name=str(entry.get("name") or model_id),
                    creator=model_id.split("/", 1)[0],
                )
            )
            for field, value in entry.items():
                if as_float(value) is None or field in ("id", "rank"):
                    continue
                observation = make_observation(
                    model_id=model_id,
                    modality=modality,
                    source=self.name,
                    field=slugify(str(field)),
                    value=value,
                    observed_at=at,
                    pulled_at=at,
                )
                if observation is not None:
                    result.observations.append(observation)
                    stored += 1

        result.warnings.append(
            f"aa_media: {path} free tier is undocumented; stored {stored} numeric field(s) "
            "as-is -- check the field names before pointing an axis at them"
        )
