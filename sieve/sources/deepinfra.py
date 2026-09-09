"""deepinfra — a second price source for media, and a complementary one.

PLAN §2.3 and part 9 job 2. fal publishes prices as English prose; deepinfra
publishes them as floats, and the two catalogues barely overlap. Measured on
2026-09-09: 372 models, 116 of them media, and **every one of the 116 carries a
machine-readable price**.

    GET https://api.deepinfra.com/models/list      no key, no account

Two rules govern this source, and both come from the same fact -- **a price is
one vendor charging to run one model, not the price of the model**:

- fal and deepinfra host the same models at different rates, so their prices are
  kept apart by `Price.source` and never merged into one number.
- `pricing.short` and `pricing.full` are English restatements of numbers that
  are already in the object as floats. They are ignored. Parsing prose when a
  float is offered is how a parser earns a wrong price it did not need.

**Every rate here is in cents**, which is the one thing about this API that will
bite somebody: `cents_per_output_sec: 5.0` is five cents a second, not five
dollars, and reading it as dollars overstates every video model by a hundred.
"""

from __future__ import annotations

from typing import Any, ClassVar

from sieve.catalog.registry import canonical_id
from sieve.contracts import (
    HttpClient,
    Modality,
    ModelRef,
    Price,
    PullResult,
    SourceConfig,
    Unit,
)
from sieve.sources.base import as_float, utcnow

URL = "https://api.deepinfra.com/models/list"

#: deepinfra's `type` where it names exactly one Sieve modality.
CERTAIN: dict[str, Modality] = {
    "text-to-speech": "text-to-speech",
    "automatic-speech-recognition": "speech-to-text",
    "text-to-music": "music",
}

#: deepinfra's `type` where its own category spans two of ours, and nothing in
#: the response separates them. `text-to-image` covers `Wan2.6-Image-Edit` and
#: `Bria/remove_background` beside `FLUX-1-dev`; `text-to-video` covers image
#: conditioning beside text conditioning.
#:
#: PLAN §2.2: the modality is then a **claim**, offered once per candidate and
#: kept only where the catalogue already holds that id in that modality --
#: which means a source that does separate them measured it. A row nothing
#: recognises is dropped and counted, never assigned to the nearest thing.
SPANNING: dict[str, tuple[Modality, ...]] = {
    "text-to-image": ("text-to-image", "image-editing"),
    "text-to-video": ("text-to-video", "image-to-video"),
}

#: `pricing` key -> (unit, what multiplies cents into that unit).
#:
#: A cent is a hundredth of a dollar, so a per-unit rate divides by 100 and a
#: per-million rate multiplies by 10,000. Both are arithmetic; neither is a
#: judgement.
RATES: tuple[tuple[str, Unit, float], ...] = (
    ("cents_per_image_unit", "usd_per_image", 0.01),
    ("cents_per_output_sec", "usd_per_second", 0.01),
    # speech-to-text bills the *input* audio, under two spellings. It is still a
    # second of a real recording and still proportional to the job, which is
    # what separates it from a compute second.
    ("cents_per_sec", "usd_per_second", 0.01),
    ("cents_per_input_sec", "usd_per_second", 0.01),
    ("cents_per_input_chars", "usd_per_1m_chars", 10_000.0),
)

#: Token rates land on `Price.input` rather than `per_unit`.
TOKEN_RATE: tuple[str, Unit, float] = ("cents_per_input_token", "usd_per_1m_tokens", 10_000.0)

#: A frame is not a second without a frame rate, and the response does not
#: publish one. Three models price this way; they are counted, not guessed at.
UNPRICEABLE = ("cents_per_frame_unit",)


def model_id_of(entry: dict[str, Any]) -> str | None:
    """`Wan-AI/Wan2.6-Image-Edit` -> `wan-ai/wan2.6-image-edit`.

    deepinfra's `model_name` is already `<org>/<model>`, which is the shape the
    canonical id wants, so this is a fold rather than a rewrite.
    """
    raw = str(entry.get("model_name") or "").strip()
    if "/" not in raw:
        return None
    creator, slug = raw.split("/", 1)
    return canonical_id(creator, slug) or None


def price_of(pricing: dict[str, Any]) -> tuple[float, Unit, str | None] | None:
    """`(amount in USD, unit, the pricing key it came from)`, or None.

    A rate of exactly zero is **not** a price. Three text-to-speech models
    publish `cents_per_input_chars: 0.0`, and storing that would put a free
    model at the top of every cost ranking on the strength of a field nobody
    filled in.
    """
    for key, unit, scale in RATES:
        amount = as_float(pricing.get(key))
        if amount is not None and amount > 0:
            return amount * scale, unit, key
    key, unit, scale = TOKEN_RATE
    amount = as_float(pricing.get(key))
    if amount is not None and amount > 0:
        return amount * scale, unit, key
    return None


class DeepInfraSource:
    """`Source` for deepinfra. Prices for media; no measurement of quality."""

    name = "deepinfra"
    needs_key = False
    modalities: ClassVar[list[Modality]] = sorted(
        {*CERTAIN.values(), *(m for group in SPANNING.values() for m in group)}
    )

    def pull(self, cfg: SourceConfig, http: HttpClient) -> PullResult:
        result = PullResult(source=self.name)
        at = utcnow()
        wanted = set(cfg.modalities or self.modalities)

        response = http.get(URL)
        if response.status != 200:
            result.warnings.append(
                f"deepinfra: HTTP {response.status} from {URL} -- nothing stored"
            )
            result.ok = False
            return result
        rows = response.body if isinstance(response.body, list) else []
        if not rows:
            result.warnings.append(f"deepinfra: {URL} returned no models")
            return result

        skipped_type: dict[str, int] = {}
        no_rate = 0
        frames = 0
        seen: set[tuple[str, Modality]] = set()

        for entry in rows:
            if not isinstance(entry, dict):
                continue
            if entry.get("deprecated") or entry.get("private"):
                continue

            kind = str(entry.get("type") or "")
            certain = CERTAIN.get(kind)
            candidates: tuple[Modality, ...] = (certain,) if certain else SPANNING.get(kind, ())
            if not candidates:
                skipped_type[kind or "(none)"] = skipped_type.get(kind or "(none)", 0) + 1
                continue

            model_id = model_id_of(entry)
            if model_id is None:
                continue

            pricing = entry.get("pricing")
            pricing = pricing if isinstance(pricing, dict) else {}
            found = price_of(pricing)
            if found is None:
                if any(as_float(pricing.get(key)) for key in UNPRICEABLE):
                    frames += 1
                else:
                    no_rate += 1
                continue
            amount, unit, _key = found
            token = unit == "usd_per_1m_tokens"

            for modality in candidates:
                if modality not in wanted:
                    continue
                key = (model_id, modality)
                if key in seen:
                    continue
                seen.add(key)
                if certain is None:
                    result.provisional.add(model_id)
                result.models.append(
                    ModelRef(
                        id=model_id,
                        modality=modality,
                        name=str(entry.get("model_name") or model_id).split("/")[-1],
                        creator=model_id.split("/", 1)[0],
                        aliases=sorted({str(entry.get("model_name") or "")} - {"", model_id}),
                    )
                )
                result.prices.append(
                    Price(
                        model_id=model_id,
                        source=self.name,
                        modality=modality,
                        unit=unit,
                        input=amount if token else None,
                        per_unit=None if token else amount,
                        source_url=f"https://deepinfra.com/{entry.get('model_name')}",
                        observed_at=at,
                    )
                )

        if no_rate:
            result.warnings.append(
                f"deepinfra: {no_rate} media model(s) publish a pricing object with no rate "
                "above zero -- left unpriced, because a zero rate is not a free model"
            )
        if frames:
            result.warnings.append(
                f"deepinfra: {frames} model(s) priced per frame -- skipped, because a frame "
                "is not a second without a frame rate and the response publishes none"
            )
        if skipped_type:
            listed = ", ".join(f"{k} {v}" for k, v in sorted(skipped_type.items()))
            result.warnings.append(
                f"deepinfra: {sum(skipped_type.values())} models skipped, no Sieve modality "
                f"for their type ({listed})"
            )
        result.rate_limit = http.rate_limit()
        return result
