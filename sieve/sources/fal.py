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
from typing import Any, ClassVar, NamedTuple

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

#: A fal category that spans more than one of our modalities, so the modality it
#: implies is a *claim* and not a reading. fal files music generation and sound
#: effects together under `text-to-audio`; Artificial Analysis separates them by
#: publishing music as two leaderboards and never scoring sound effects. So a
#: text-to-audio row is offered as `music` and kept only if it folds onto a music
#: model the catalogue already holds. The rest are dropped and counted -- a list
#: nobody can rank is dead weight on every screen, and a sound-effects modality
#: with no score source would be exactly that.
PROVISIONAL: dict[str, Modality] = {"text-to-audio": "music"}

#: fal's category -> our modality, where the mapping is certain.
#: `video-to-video`, `image-to-3d`, `training`, `vision` and the rest have no
#: modality in Sieve, and a wrong one is worse than none -- so those models are
#: counted and skipped rather than forced into the nearest thing.
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


class Rate(NamedTuple):
    """One readable price, and the tier it applies to if there is more than one."""

    amount: float
    unit: Unit
    tier: str | None


#: What a tier is called, taken from the words fal actually writes after a rate:
#: "at 480p", "for 1080p", "at 4K". Anything else is not treated as a tier.
_TIER = re.compile(
    r"(?:at|for|in)\s+\*{0,2}((?:\d{3,4}p|4k|2k|8k|hd|sd|standard|pro|fast|turbo))\*{0,2}",
    re.I,
)


def parse_rates(prose: str) -> list[Rate]:
    """Every rate this parser can prove, cheapest first.

    A tiered price is not unparseable -- it is several prices. "Video costs
    $0.0125 per second at 480p, $0.02 at 768p, $0.04 at 1080p" is three honest
    numbers, and refusing the string dropped the model from the catalogue
    altogether. What must never happen is taking the first number and calling it
    "the" price, which would bill 4K work at the 480p line.

    Anything genuinely ambiguous still comes back empty: a table of token tiers,
    a first-unit-plus-marginal price, a training run billed per step.
    """
    if not prose:
        return []
    text = " ".join(prose.split())

    # A price whose parts are not alternatives but *additions* -- "$0.03 for the
    # first megapixel, plus $0.015 per extra" -- has no single rate at all. The
    # same words also appear where every amount is identical ("each extra image
    # is $0.08 per image"), and that is one rate stated twice, so the language
    # alone does not decide it: differing amounts alongside it do.
    if re.search(r"\bplus\b|\bfirst\b|\bextra\b|\badditional\b", text, re.I):
        amounts = {found.group(1) for found in re.finditer(_MONEY, text)}
        if len(amounts) > 1:
            return []
    # several rates against different units in one string is a bill, not a price
    if re.search(r"per\s*1\s*m(?:illion)?\s*tokens", text, re.I) and re.search(
        r"image tokens|audio tokens|video tokens", text, re.I
    ):
        return []

    for pattern, unit, is_total in _FORMS:
        found = list(pattern.finditer(text))
        if not found:
            continue
        rates: list[Rate] = []
        for match in found:
            groups = match.groups()
            if is_total:
                seconds = as_float(groups[0])
                amount = as_float(groups[1])
                if not seconds or amount is None:
                    continue
                value = amount / seconds
            else:
                value = as_float(groups[0]) or 0.0
            if value <= 0:
                continue
            after = text[match.end() : match.end() + 40]
            tier = _TIER.search(after)
            rates.append(Rate(value, unit, tier.group(1).lower() if tier else None))

        if not rates:
            continue
        # One rate is simply the price. The same rate written twice is still one
        # price -- "each extra image is $0.08 per image" restates it. Several
        # *different* rates are tiers, and only tiers we can name are
        # trustworthy: an unlabelled second amount might be a discount, a typo
        # or a second product, and none of those is a price.
        if len({r.amount for r in rates}) > 1 and not all(r.tier for r in rates):
            return []
        if len({r.amount for r in rates}) == 1:
            rates = rates[:1]
        return sorted(rates, key=lambda r: r.amount)
    return []


def parse_price(prose: str) -> tuple[float, Unit] | None:
    """The cheapest readable rate, or None. Kept for callers that want no tier."""
    rates = parse_rates(prose)
    return (rates[0].amount, rates[0].unit) if rates else None


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
    # `music` is here because of PROVISIONAL: a text-to-audio row is offered
    # as music and kept only if something that separates music from sound
    # effects already knows the id.
    modalities: ClassVar[list[Modality]] = sorted({*CATEGORIES.values(), *PROVISIONAL.values()})

    def pull(self, cfg: SourceConfig, http: HttpClient) -> PullResult:
        result = PullResult(source=self.name)
        at = utcnow()
        wanted = set(cfg.modalities or self.modalities)

        entries = self._pages(http, result)
        if not entries:
            return result

        skipped_category: dict[str, int] = {}
        unreadable: dict[tuple[str, Modality], int] = {}
        tiered = 0
        seen: set[tuple[str, Modality]] = set()
        have_price: set[tuple[str, Modality]] = set()

        for entry in entries:
            if entry.get("deprecated") or entry.get("removed"):
                continue

            category = str(entry.get("category") or "")
            modality = CATEGORIES.get(category)
            provisional = modality is None and category in PROVISIONAL
            if provisional:
                modality = PROVISIONAL[category]
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
            if provisional:
                result.provisional.add(model_id)

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
            rates = parse_rates(prose)
            if not rates:
                unreadable.setdefault(key, 0)
                unreadable[key] += 1
                continue
            # cheapest tier, named. Storing the lowest without saying which
            # tier it is would understate the cost of anything bigger.
            cheapest = rates[0]
            if len(rates) > 1:
                tiered += 1
            have_price.add(key)
            result.prices.append(
                Price(
                    model_id=model_id,
                    source=self.name,
                    modality=modality,
                    unit=cheapest.unit,
                    per_unit=cheapest.amount,
                    tier=cheapest.tier,
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
        if tiered:
            result.warnings.append(
                f"fal: {tiered} model(s) priced by tier -- the cheapest tier is"
                " stored and named, so their real cost at a higher setting is more"
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
                result.ok = False
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
