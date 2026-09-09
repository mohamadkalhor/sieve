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
from datetime import datetime
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
    # added when Sieve gained the modality: fal files voice conversion under
    # the same name, and it was being skipped as "no Sieve modality" long after
    # there was one.
    "speech-to-speech": "speech-to-speech",
}

#: A dollar amount, with fal's markdown bold allowed around it. The `$` is
#: **required**: without it "75% off" and "1.5 times" read as prices, and the
#: whole point of this module is that a wrong price is worse than no price.
_MONEY = r"\*{0,2}\$\s*([0-9]+(?:\.[0-9]+)?)\*{0,2}"

#: fal writes both `$0.25` and `0.25 $`. One shape is easier to prove than two,
#: so the suffix form is rewritten into the prefix form before anything reads
#: it. 21 models in the live catalogue use the suffix and were unreadable.
_SUFFIX_DOLLAR = re.compile(r"([0-9]+(?:\.[0-9]+)?)\s*\$")

#: Each pattern is a form measured in the live catalogue, with the unit it
#: means. Order matters: the more specific forms are tried first, because
#: "per second of video" would otherwise be read by the bare "per second" rule
#: and "for a 5s video" would be read as a rate.
_FORMS: tuple[tuple[re.Pattern[str], Unit, float], ...] = (
    # "For 5s video your request will cost $0.25"  -> a total, divided by 5
    (
        re.compile(
            r"for\s+\*{0,2}(\d+(?:\.\d+)?)\s*s(?:econds?)?\*{0,2}\s+(?:of\s+)?video[^.]*?"
            r"(?:cost|charged)\D{0,24}" + _MONEY,
            re.I,
        ),
        "usd_per_second",
        0.0,  # 0 means "the first group is a duration; divide by it"
    ),
    # "For every second of video you generated, you will be charged $0.04"
    (
        re.compile(
            r"for\s+every\s+second\s+of\s+[^.]*?(?:charged|cost)\D{0,24}" + _MONEY,
            re.I,
        ),
        "usd_per_second",
        1.0,
    ),
    (re.compile(_MONEY + r"\s*(?:per|/)\s*(?:generated\s+)?image", re.I), "usd_per_image", 1.0),
    # PLAN 2.3: **per compute second is not per second of generated output.**
    # Hardware time varies with the job and with nothing a shape can declare, so
    # this rate is stored, named, and never costed -- an upscaler billed by
    # compute second must not look cheaper than a video model billed by output
    # second, because the two numbers do not measure the same thing.
    (
        re.compile(_MONEY + r"\s*(?:per|/)\s*compute\s+second", re.I),
        "usd_per_compute_second",
        1.0,
    ),
    (
        re.compile(_MONEY + r"\s*(?:per|/)\s*(?:output\s+video\s+|video\s+)?second", re.I),
        "usd_per_second",
        1.0,
    ),
    # "At an output resolution of 480p, every second costs $0.05, and at 720p,
    # every second costs $0.07" -- the rate follows the unit instead of leading
    # it, which is the same price written the other way round.
    (
        re.compile(r"every\s+second\s+costs?\s*" + _MONEY, re.I),
        "usd_per_second",
        1.0,
    ),
    # a minute is sixty seconds and nothing else; converting is arithmetic, not
    # a judgement, so it does not need a unit of its own
    (re.compile(_MONEY + r"\s*(?:per|/)\s*minute", re.I), "usd_per_second", 1 / 60),
    (
        re.compile(_MONEY + r"\s*(?:per|/)\s*(?:megapixel|mp)\b", re.I),
        "usd_per_megapixel",
        1.0,
    ),
    (
        re.compile(r"per\s*1\s*m(?:illion)?\s*(?:tokens|chars)[^.$]{0,40}?" + _MONEY, re.I),
        "usd_per_1m_tokens",
        1.0,
    ),
    # published per 1,000 and stored per 1,000,000. This used to keep the
    # published number under the per-million unit, which understated every such
    # model by a factor of a thousand.
    (
        re.compile(r"per\s*1[,\s]?000\s*characters[^.$]{0,40}?" + _MONEY, re.I),
        "usd_per_1m_chars",
        1000.0,
    ),
    # "Your request will cost $0.25 for 512p" -- a whole request, no other unit
    # named. Last, so anything with a real unit is read as that unit first.
    (
        re.compile(_MONEY + r"\s*(?:per|/)\s*(?:request|generation|call)\b", re.I),
        "usd_per_request",
        1.0,
    ),
    (
        re.compile(
            r"(?:your\s+)?requests?\s+(?:will\s+)?cost\D{0,12}" + _MONEY,
            re.I,
        ),
        "usd_per_request",
        1.0,
    ),
)


class Rate(NamedTuple):
    """One readable price, and the tier it applies to if there is more than one."""

    amount: float
    unit: Unit
    tier: str | None
    #: which side of a token price this is, where a source publishes both
    role: str | None = None


#: What a tier is called, taken from the words fal actually writes around a
#: rate: "at 480p", "for 1080p", "at 4K", "512p resolution".
_TIER = re.compile(
    r"(?:at|for|in|of)\s+\*{0,2}((?:\d{3,4}p|0\.5k|[248]k|hd|sd|standard|pro|fast|turbo))\*{0,2}"
    # a resolution may carry a qualifier, and then the resolution alone is not
    # the tier: veo 3.1 charges "$0.05 for 720p with audio, $0.03 for 720p
    # without audio", which under the bare resolution is one tier priced twice.
    r"(\s+(?:with|without)\s+audio)?",
    re.I,
)

#: "Text tokens (per 1M): $5.00 input, $1.25 cached, $10.00 output."
#: The kind names the tier, because one model may publish both text and image
#: token rates and they are different prices for different things.
_TOKEN_BLOCK = re.compile(
    r"\b(text|image|audio|video)?\s*tokens\s*\(\s*per\s*1\s*m(?:illion)?\s*\)", re.I
)
_TOKEN_ROLE = re.compile(_MONEY + r"\s*(input|cached|output)\b", re.I)

#: The tier vocabulary without the lead-in word. `_TIER` needs "at 480p" before
#: it will name a tier, because naming one is a claim. Asking only whether an
#: amount sits in tiered company is a weaker question and takes the weaker test:
#: "so a **5-second** **768p** clip costs **$0.40**" writes the resolution with
#: no preposition at all.
_TIER_TOKEN = re.compile(r"\b(?:\d{3,4}p|0\.5k|[248]k|hd|sd)\b", re.I)

#: A clause that is not the price, and why. PLAN 2.3: several numbers in a
#: sentence is not a reason to refuse the sentence -- but some of those numbers
#: are answering a different question, and reading them as rates is how a
#: parser invents a price. So the clause goes and the rest is read.
_SKIP: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "a restatement of the same price as marketing arithmetic",
        re.compile(r"for\s+\*{0,2}\$[\d.]+\*{0,2}\s*,?\s*you\s+can\s+|\bfor\s+example\b", re.I),
    ),
    (
        # "$0.003 per step (minimum of 100 steps is charged)" -- a rate against
        # a count the source does not publish is not a price for a task.
        "billed per step, and the step count is not published",
        re.compile(r"\bper\s+step\b", re.I),
    ),
    (
        "a rate that applies at a different time",
        re.compile(
            r"\bafter which\b|\bdiscount ends\b|\bpromotional\b|\blaunch rates\b"
            r"|\bwas previously\b|\bregular price\b",
            re.I,
        ),
    ),
    (
        "an add-on charge, not the rate",
        re.compile(
            r"\badds\s+\*{0,2}\$|\ban?\s+additional\s+\*{0,2}\$|\+\s*\*{0,2}\$"
            r"|\bplus\s+(?:a\s+charge|an?\s+additional)|\beach\s+(?:extra|additional)\b"
            r"|\bfor\s+every\s+additional\b|\bbeyond\s+an?\s+included\s+allowance\b"
            r"|\bfor\s+the\s+first\b.*\bplus\b|\bper\s+extra\b",
            re.I,
        ),
    ),
    (
        "a multiplier on the standard rate",
        re.compile(r"times\s+the\s+standard\s+rate|double\s+the\s+standard\s+rate", re.I),
    ),
)

#: The clause reason that means "the rate we did read is the *standard* one and
#: other settings cost a multiple of it". The tier is named so nobody reads the
#: number as the only price there is.
_MULTIPLIER = "a multiplier on the standard rate"


def _tier_name(found: re.Match[str]) -> str:
    """The tier a `_TIER` match names, qualifier included."""
    return (found.group(1) + (found.group(2) or "")).strip().lower()


def _clauses(prose: str) -> tuple[list[str], set[str]]:
    """The prose split into clauses, with the ones that are not a rate removed.

    Clauses, not sentences: fal writes `Video costs $0.05 per second at 480p,
    ... ; the first 5 reference images are free and each additional image costs
    $0.08.` in one sentence, and dropping the whole sentence for the sake of the
    second half loses four honest tiers.
    """
    text = " ".join(prose.split())
    text = _SUFFIX_DOLLAR.sub(r"$\1", text)
    kept: list[str] = []
    dropped: set[str] = set()
    for clause in re.split(r"(?<=[.!?;])\s+|;\s*", text):
        if not clause.strip():
            continue
        for reason, pattern in _SKIP:
            if pattern.search(clause):
                dropped.add(reason)
                break
        else:
            kept.append(clause)
    return kept, dropped


#: How far from an amount a tier may sit and still be about it. Two rates in one
#: clause are about 30 characters apart, so a tier further away than this is
#: describing something else.
_TIER_REACH = 44


def _assign_tiers(clause: str, spans: list[tuple[int, int]]) -> list[str | None]:
    """Which tier belongs to which amount, by nearest, each tier used once.

    fal writes the tier after the rate (`$0.02 per second at 768p`) and before
    it (`At an output resolution of 480p, every second costs $0.05, and at 720p,
    every second costs $0.07`) in the same catalogue -- and taking "the tier
    after" in that second sentence hands **720p** to both amounts, which reads
    as one tier priced two ways.

    Nearest-first with each tier consumed once gets both shapes right, and it
    fails loudly rather than quietly: an amount with no tier within reach keeps
    None, and the caller refuses a mixed list.
    """
    tiers = [(found.start(), _tier_name(found)) for found in _TIER.finditer(clause)]
    out: list[str | None] = [None] * len(spans)

    # Prose is written in order. When there are exactly as many tiers as amounts
    # and each pair is close enough to be about each other, reading them in
    # order is right and distance is not: in "At an output resolution of 480p,
    # every second costs $0.05, and at 720p, every second costs $0.07" the
    # **nearest** tier to the first amount is 720p, four words further away than
    # the 480p it belongs to.
    if len(tiers) == len(spans) and all(
        min(abs(at - start), abs(at - end)) <= _TIER_REACH
        for (at, _), (start, end) in zip(tiers, spans, strict=True)
    ):
        return [name for _, name in tiers]

    pairs = sorted(
        (
            (min(abs(at - start), abs(at - end)), i, j)
            for i, (start, end) in enumerate(spans)
            for j, (at, _) in enumerate(tiers)
        ),
        key=lambda pair: pair[:3],
    )
    taken_amount: set[int] = set()
    taken_tier: set[int] = set()
    for distance, i, j in pairs:
        if distance > _TIER_REACH or i in taken_amount or j in taken_tier:
            continue
        out[i] = tiers[j][1]
        taken_amount.add(i)
        taken_tier.add(j)
    return out


def _token_rates(clause: str) -> list[Rate]:
    """`$5.00 input, $1.25 cached, $10.00 output` against a per-million block.

    PLAN 2.3 is explicit that this is a price and not a refusal. It is stored as
    input / cached / output rather than collapsed, because a profile's own shape
    decides which of them dominates.
    """
    block = _TOKEN_BLOCK.search(clause)
    if not block:
        return []
    kind = (block.group(1) or "").lower()
    tier = f"{kind} tokens" if kind else None
    out: list[Rate] = []
    for found in _TOKEN_ROLE.finditer(clause[block.end() :]):
        amount = as_float(found.group(1))
        if amount is None or amount <= 0:
            continue
        out.append(Rate(amount, "usd_per_1m_tokens", tier, found.group(2).lower()))
    return out


def _tier_list(
    clauses: list[str], pattern: re.Pattern[str], unit: Unit, rates: list[Rate]
) -> list[Rate]:
    """Amounts that share the unit the clause already named.

    `Your request will cost $0.25 for 512p resolution, $0.30 for 1024p and $0.35
    for 1536p` names its unit once and then lists three amounts against it. The
    form that proved the unit matches only the first, so the other two would be
    lost -- and losing them is exactly what PLAN 2.3 forbids, because the model
    would then rank at its 512p price while a 1536p job costs 40% more.

    Every extra amount must carry a tier of its own. An unlabelled second amount
    could be a discount, a typo or a second product, and none of those is a
    price.
    """
    if not rates:
        return rates
    extra: list[Rate] = []
    for clause in clauses:
        covered = [(m.start(), m.end()) for m in pattern.finditer(clause)]
        for found in re.finditer(_MONEY, clause):
            if any(start <= found.start() < end for start, end in covered):
                continue
            after = _TIER.search(clause[found.end() : found.end() + 30])
            amount = as_float(found.group(1))
            if after is None or amount is None or amount <= 0:
                continue
            extra.append(Rate(amount, unit, _tier_name(after)))
    if not extra:
        return rates
    seen = {(r.amount, r.tier) for r in rates}
    return rates + [r for r in extra if (r.amount, r.tier) not in seen]


def _unexplained(clauses: list[str], pattern: re.Pattern[str], rates: list[Rate]) -> bool:
    """Is there a dollar amount here that nothing accounts for?

    An amount the winning form did not match and no tier names is an
    alternative -- audio on or off, vector style or not -- and choosing between
    alternatives is not something a parser can do from prose.
    """
    named = {round(rate.amount, 10) for rate in rates}
    for clause in clauses:
        covered = [(m.start(), m.end()) for m in pattern.finditer(clause)]
        if not covered:
            continue
        for found in re.finditer(_MONEY, clause):
            if any(start <= found.start() < end for start, end in covered):
                continue
            amount = as_float(found.group(1))
            if amount is None or amount <= 0:
                continue
            if round(amount, 10) in named:
                continue
            # A tier on either side accounts for the amount: "so a 5-second
            # **768p** clip costs $0.40" restates a tiered rate, and refusing
            # the model over its own worked example would throw away three
            # tiers that were read correctly.
            near = clause[max(0, found.start() - 30) : found.end() + 30]
            if _TIER.search(near) or _TIER_TOKEN.search(near):
                continue
            return True
    return False


def parse_rates(prose: str) -> list[Rate]:
    """Every rate this parser can prove, cheapest first.

    A tiered price is not unparseable -- it is several prices. "Video costs
    $0.0125 per second at 480p, $0.02 at 768p, $0.04 at 1080p" is three honest
    numbers, and refusing the string dropped the model from the catalogue
    altogether. **Every** tier is returned; the caller decides which is the
    default and says so.

    What must never happen is taking one number and calling it "the" price. So
    two different amounts against one unit with nothing naming which is which
    still comes back empty, and so does a clause this parser cannot classify.
    """
    if not prose:
        return []
    # "$0.0024 cents per step" names two currencies for one number, and whichever
    # is meant, the other is wrong by a factor of a hundred. The word alone is
    # not enough: fal also writes "rounded up to the closest hundredth of a
    # cent", which is a rounding rule and not a rate.
    if re.search(r"\$\s*[\d.]+\s*\**\s*cents?\b", prose, re.I):
        return []
    clauses, dropped = _clauses(prose)
    standard = _MULTIPLIER in dropped

    tokens: list[Rate] = []
    for clause in clauses:
        tokens.extend(_token_rates(clause))
    if tokens:
        return tokens

    for pattern, unit, scale in _FORMS:
        rates: list[Rate] = []
        for clause in clauses:
            found = list(pattern.finditer(clause))
            if not found:
                continue
            # the money group, not the whole match: `For 5s video ... cost $0.25`
            # spans forty characters and the amount is at the end of them
            spans = [(m.start(len(m.groups())), m.end(len(m.groups()))) for m in found]
            tiers = _assign_tiers(clause, spans)
            for match, tier in zip(found, tiers, strict=True):
                groups = match.groups()
                if scale == 0.0:
                    duration = as_float(groups[0])
                    amount = as_float(groups[1])
                    if not duration or amount is None:
                        continue
                    value = amount / duration
                else:
                    value = (as_float(groups[0]) or 0.0) * scale
                if value <= 0:
                    continue
                rates.append(Rate(value, unit, tier))
        rates = _tier_list(clauses, pattern, unit, rates)
        if not rates:
            continue
        # An amount this form did not read, in a clause it did read, with no
        # tier to say what it is. `$0.04 per image (or $0.08 if you are using a
        # vector style)` is two prices and nothing names the second, so taking
        # the first is exactly the mistake this module exists to avoid.
        if _unexplained(clauses, pattern, rates):
            return []
        # Two amounts under one tier is a distinction the tier vocabulary cannot
        # express -- `$0.05 for 720p with audio, $0.03 for 720p without` -- so
        # storing either under "720p" would be a coin toss.
        by_tier: dict[str | None, set[float]] = {}
        for rate in rates:
            by_tier.setdefault(rate.tier, set()).add(rate.amount)
        if any(tier is not None and len(amounts) > 1 for tier, amounts in by_tier.items()):
            return []
        # One rate is simply the price. The same rate written twice is still one
        # price. Several *different* rates are tiers, and only tiers we can name
        # are trustworthy: an unlabelled second amount might be a discount, a
        # typo or a second product, and none of those is a price.
        distinct = {r.amount for r in rates}
        if len(distinct) > 1 and not all(r.tier for r in rates):
            return []
        if len(distinct) == 1:
            rates = [rates[0]._replace(tier=rates[0].tier or ("standard" if standard else None))]
        return sorted(rates, key=lambda r: r.amount)
    return []


def parse_price(prose: str) -> tuple[float, Unit] | None:
    """The cheapest readable rate, or None. Kept for callers that want no tier."""
    rates = parse_rates(prose)
    return (rates[0].amount, rates[0].unit) if rates else None


#: A path segment that names an *endpoint* rather than a model: the thing the
#: model is being asked to do. `image-to-video`, `video-to-sound-effects`,
#: `edit`. The modality already records this, so it is not part of an identity.
_ENDPOINT = re.compile(r"^(?:[a-z0-9]+-to-[a-z0-9-]+|edit)$", re.I)


def model_id_of(entry: dict[str, Any]) -> str | None:
    """fal ids are provider paths (`fal-ai/nano-banana-2/edit`), not model names.

    The canonical id has to be the *model*, so the vendor segment goes and the
    trailing endpoint segments go with it -- `minimax/h3-max/image-to-video` and
    `minimax/h3-max/text-to-video` are one model asked two questions.

    **Everything between them stays**, which is the part this used to get wrong.
    Taking only the first segment after the vendor collapsed
    `fal-ai/veo3.1/image-to-video`, `.../fast/...` and `.../lite/...` onto one
    id -- three different models at $0.20, $0.15 and $0.05 a second -- and
    whichever price was written first won. Five models of Kling collapsed the
    same way, and so did FLUX schnell onto FLUX dev.

    That is not a display problem. The folded id then matched a real model in
    the catalogue and put a cheaper model's price on it, which is a wrong
    recommendation rather than a missing one.
    """
    raw = str(entry.get("id") or entry.get("modelId") or "").strip()
    if not raw:
        return None
    parts = [p for p in raw.split("/") if p]
    while len(parts) > 2 and _ENDPOINT.match(parts[-1]):
        parts = parts[:-1]
    if len(parts) < 2:
        return None
    # `modelFamily` is the model's own name ("Nano Banana 2"), not its
    # creator, so using it would produce `nano-banana-2/nano-banana-2`. The
    # vendor segment is honest about where the record came from, and the
    # slug -- which is what AA also uses -- is what the matcher folds on.
    return canonical_id(parts[0], "-".join(parts[1:]))


def _prices_for(
    model_id: str,
    modality: Modality,
    rates: list[Rate],
    entry: dict[str, Any],
    at: datetime,
) -> list[Price]:
    """One `Price` per tier.

    A token price is *one* price with several sides -- input, cached, output --
    so its rates fold onto a single row rather than becoming three. Everything
    else is a flat rate per unit, and each tier is its own row.
    """
    url = f"https://fal.ai/models/{entry.get('id')}"
    by_tier: dict[str | None, list[Rate]] = {}
    for rate in rates:
        by_tier.setdefault(rate.tier, []).append(rate)

    out: list[Price] = []
    for tier, group in by_tier.items():
        roles = {rate.role: rate.amount for rate in group if rate.role}
        if roles:
            out.append(
                Price(
                    model_id=model_id,
                    source="fal",
                    modality=modality,
                    unit=group[0].unit,
                    input=roles.get("input"),
                    output=roles.get("output"),
                    cached_input=roles.get("cached"),
                    tier=tier,
                    source_url=url,
                    observed_at=at,
                )
            )
            continue
        for rate in group:
            out.append(
                Price(
                    model_id=model_id,
                    source="fal",
                    modality=modality,
                    unit=rate.unit,
                    per_unit=rate.amount,
                    tier=rate.tier,
                    source_url=url,
                    observed_at=at,
                )
            )
    return out


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
            # PLAN 2.3: every tier, not the cheapest one. A model that is cheap
            # at 480p and dear at 1080p must not be stored as cheap; the store
            # holds all three and `latest_prices` declares which is the default.
            written = _prices_for(model_id, modality, rates, entry, at)
            if len(written) > 1:
                tiered += 1
            have_price.add(key)
            result.prices.extend(written)

        # a model counts as unparsed only if *no* endpoint of it could be read
        unparsed = len({m for m in unreadable if m not in have_price})
        if unparsed:
            result.warnings.append(
                f"fal: {unparsed} of {len(have_price) + unparsed} prices unparsed -- "
                "left blank on purpose; a wrong price is worse than no price"
            )
        if tiered:
            result.warnings.append(
                f"fal: {tiered} model(s) priced by tier -- every tier is stored and"
                " named; ranking uses the cheapest, so their cost at a higher"
                " setting is more"
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
