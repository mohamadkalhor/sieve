"""fal — price for the media field, which nobody else supplies.

PLAN §2.1b, measured on 2026-09-08. Artificial Analysis scores 482 media models
and prices **none** of them. OpenRouter carries 11 image and 4 audio models and
no video at all. fal publishes 1,494 media models with prices and no quality
score whatsoever, because a generation marketplace never judges quality. Neither
side is enough; joined, they rank.

`GET https://fal.ai/api/models?limit=&page=`, no key, envelope
`{items, page, size, pages, total}`. 1,494 models over 8 pages at `limit=200`.

**Prices are English prose**, written for a human reading a model card:

    Your request will cost **$0.08** per image.
    For every second of video you generated, you will be charged **$0.04**.
    For 5s video your request will cost **$0.25**.
    Text tokens (per 1M): **$5.00** input, **$1.25** cached, **$10.00** output.

So the parser reads only the forms it can prove and **leaves everything else
blank**, counted and reported. A wrong price is worse than no price: it does not
merely mis-rank a model, it recommends the wrong one and bills for it.
"""

from __future__ import annotations

import re
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

URL = "https://fal.ai/api/models"

#: 200 is what the API serves happily and makes 8 requests of 1,494 models.
PAGE_SIZE = 200

#: A guard against paging for ever if the envelope ever stops saying `pages`.
MAX_PAGES = 40

#: fal's category -> our modality. Only mappings that are certain are here.
#: `video-to-video`, `image-to-3d`, `training`, `vision` and the rest have no
#: modality in Sieve, and the brief is explicit that a wrong one is worse than
#: none -- so those models are counted and skipped rather than forced.
CATEGORIES: dict[str, Modality] = {
    "text-to-image": "text-to-image",
    "image-to-image": "image-editing",
    "text-to-video": "text-to-video",
    "image-to-video": "image-to-video",
    "text-to-speech": "text-to-speech",
    "speech-to-text": "speech-to-text",
}

_MONEY = r"\*{0,2}\$?\s*([0-9]+(?:\.[0-9]+)?)\s*\*{0,2}"

#: Each pattern is a form measured in the live catalogue, with the unit it
#: means. Order matters: the more specific forms are tried first, because
#: "per second of video" would otherwise be read by the bare "per second" rule
#: and "for a 5s video" would be read as a rate.
_FORMS: tuple[tuple[re.Pattern[str], Unit, bool], ...] = (
    # "For 5s video your request will cost $0.25"  -> a total, divided by 5
    (
        re.compile(
            r"for\s+\*{0,2}(\d+(?:\.\d+)?)\s*s(?:econds?)?\*{0,2}\s+(?:of\s+)?video[^.]*?"
            r"(?:cost|charged)\D{0,24}" + _MONEY,
            re.I,
        ),
        "usd_per_second",
        True,
    ),
    # "For every second of video you generated, you will be charged $0.04"
    (
        re.compile(
            r"for\s+every\s+second\s+of\s+[^.]*?(?:charged|cost)\D{0,24}" + _MONEY,
            re.I,
        ),
        "usd_per_second",
        False,
    ),
    (re.compile(_MONEY + r"\s*(?:per|/)\s*(?:generated\s+)?image", re.I), "usd_per_image", False),
    (
        re.compile(
            _MONEY + r"\s*(?:per|/)\s*(?:compute\s+|output\s+video\s+|video\s+)?second", re.I
        ),
        "usd_per_second",
        False,
    ),
    (
        re.compile(r"per\s*1\s*m(?:illion)?\s*(?:tokens|chars)[^.$]{0,40}?" + _MONEY, re.I),
        "usd_per_1m_tokens",
        False,
    ),
    (
        re.compile(r"per\s*1[,\s]?000\s*characters[^.$]{0,40}?" + _MONEY, re.I),
        "usd_per_1m_chars",
        False,
    ),
)


#: The same unit priced more than once in one string: a tier table, not a rate.
_TIERED: tuple[re.Pattern[str], ...] = (
    re.compile(_MONEY + r"\s*(?:per|/)\s*(?:generated\s+)?image", re.I),
    re.compile(_MONEY + r"\s*(?:per|/)\s*(?:compute\s+|output\s+video\s+|video\s+)?second", re.I),
    re.compile(_MONEY + r"\s*(?:per|/)\s*(?:extra\s+)?megapixel", re.I),
)


def parse_price(prose: str) -> tuple[float, Unit] | None:
    """`(amount, unit)` for a form this parser can prove, else None.

    Deliberately narrow. Anything it cannot read with certainty -- a price that
    depends on resolution, a training run billed per step, a table of token
    tiers -- comes back None and is counted as unparsed.
    """
    if not prose:
        return None
    text = " ".join(prose.split())

    # A tiered price is not one price. "Video costs $0.0125 per second at
    # 480p, $0.02 per second at 768p" has no single rate, and taking the
    # first would quietly bill 4K work at the 480p line. Two different
    # amounts against the same unit means the answer is "unparsed".
    for tier in _TIERED:
        amounts = {found for found in tier.findall(text)}
        if len(amounts) > 1:
            return None
    for pattern, unit, is_total in _FORMS:
        found = pattern.search(text)
        if not found:
            continue
        groups = found.groups()
        if is_total:
            seconds = as_float(groups[0])
            amount = as_float(groups[1])
            if not seconds or amount is None:
                continue
            return amount / seconds, unit
        amount = as_float(groups[0])
        if amount is None:
            continue
        return amount, unit
    return None


def model_id_of(entry: dict[str, Any]) -> str | None:
    """fal ids are provider paths (`fal-ai/nano-banana-2/edit`), not model names.

    The canonical id has to be the *model*, so the vendor segment and any
    endpoint segment after it are dropped and the title is preferred where the
    path carries a variant. Matching to AA then goes through the normal alias
    machinery.
    """
    raw = str(entry.get("id") or entry.get("modelId") or "").strip()
    if not raw:
        return None
    parts = [p for p in raw.split("/") if p]
    if len(parts) < 2:
        return None
    # `modelFamily` is the model's own name ("Nano Banana 2"), not its
    # creator, so using it would produce `nano-banana-2/nano-banana-2`. The
    # vendor segment is honest about where the record came from, and the
    # slug -- which is what AA also uses -- is what the matcher folds on.
    return canonical_id(parts[0], parts[1])


class FalSource:
    """`Source` for fal.ai. Prices for media; no measurements of quality."""

    name = "fal"
    needs_key = False
    modalities: ClassVar[list[Modality]] = sorted(set(CATEGORIES.values()))

    def pull(self, cfg: SourceConfig, http: HttpClient) -> PullResult:
        result = PullResult(source=self.name)
        at = utcnow()
        wanted = set(cfg.modalities or self.modalities)

        entries = self._pages(http, result)
        if not entries:
            return result

        skipped_category: dict[str, int] = {}
        unreadable: dict[tuple[str, Modality], int] = {}
        seen: set[tuple[str, Modality]] = set()
        have_price: set[tuple[str, Modality]] = set()

        for entry in entries:
            if entry.get("deprecated") or entry.get("removed"):
                continue

            category = str(entry.get("category") or "")
            modality = CATEGORIES.get(category)
            if modality is None:
                skipped_category[category or "(none)"] = skipped_category.get(category, 0) + 1
                continue
            if modality not in wanted:
                continue

            model_id = model_id_of(entry)
            if model_id is None:
                continue

            # One model reaches the catalogue through several endpoints --
            # `nano-banana-2/edit` and `nano-banana-2/text-to-image` are one
            # model -- so the id is deduplicated. The price, though, is not
            # written on every endpoint: skipping the repeats outright threw
            # away the priced entry whenever an unpriced one came first.
            key = (model_id, modality)
            first_time = key not in seen
            if first_time:
                seen.add(key)

            aliases = {str(entry.get("id") or ""), str(entry.get("title") or "")} - {"", model_id}
            if first_time:
                result.models.append(
                    ModelRef(
                        id=model_id,
                        modality=modality,
                        name=str(entry.get("title") or model_id),
                        creator=model_id.split("/", 1)[0],
                        aliases=sorted(aliases),
                    )
                )

            prose = str(entry.get("pricingInfoOverride") or "")
            if not prose.strip() or key in have_price:
                continue
            parsed = parse_price(prose)
            if parsed is None:
                unreadable.setdefault(key, 0)
                unreadable[key] += 1
                continue
            amount, unit = parsed
            have_price.add(key)
            result.prices.append(
                Price(
                    model_id=model_id,
                    source=self.name,
                    modality=modality,
                    unit=unit,
                    per_unit=amount,
                    source_url=f"https://fal.ai/models/{entry.get('id')}",
                    observed_at=at,
                )
            )

        # a model counts as unparsed only if *no* endpoint of it could be read
        unparsed = len({m for m in unreadable if m not in have_price})
        if unparsed:
            result.warnings.append(
                f"fal: {unparsed} of {len(have_price) + unparsed} prices unparsed -- "
                "left blank on purpose; a wrong price is worse than no price"
            )
        if skipped_category:
            listed = ", ".join(f"{k} {v}" for k, v in sorted(skipped_category.items()))
            result.warnings.append(
                f"fal: {sum(skipped_category.values())} models skipped, no Sieve modality "
                f"for their category ({listed})"
            )
        result.rate_limit = http.rate_limit()
        return result

    def _pages(self, http: HttpClient, result: PullResult) -> list[dict[str, Any]]:
        """Every page, politely. The envelope says how many there are."""
        out: list[dict[str, Any]] = []
        page = 1
        while page <= MAX_PAGES:
            response = http.get(URL, params={"limit": str(PAGE_SIZE), "page": str(page)})
            if response.status != 200:
                result.warnings.append(
                    f"fal: HTTP {response.status} from {URL} page {page}"
                    + (" -- nothing stored" if not out else " -- kept the earlier pages")
                )
                return out
            body = response.body if isinstance(response.body, dict) else {}
            items = body.get("items")
            if not isinstance(items, list) or not items:
                break
            out.extend(item for item in items if isinstance(item, dict))
            pages = body.get("pages")
            if not isinstance(pages, int) or page >= pages:
                break
            page += 1
        return out
