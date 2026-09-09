"""Artificial Analysis prices media after all — on its leaderboards.

PLAN §2.3 said "Artificial Analysis scores media and prices nothing". That was
wrong, and it was wrong in a specific way worth recording: the v2 data API
returns nine fields for a media arena and none of them is a price, even with
`include_prices=true`. The **leaderboard pages** carry one on every row. The API
was checked and the page was not.

This source reads the pages. That makes it different in kind from every other
source here, and the difference has to be paid for rather than hidden:

- **It parses JSON, not prose and not HTML.** Next.js streams its data as
  `self.__next_f.push([1,"<chunk>"])`, where the chunk is a JSON string inside a
  JS string literal inside HTML. Unescaping once gives a flight payload whose
  row arrays decode with `json.loads`. Nothing here pattern-matches a number.
- **It fails loudly.** A page whose shape has moved returns `ok=False` and the
  run goes red. A page-shaped source that silently returns nothing is the exact
  failure this phase keeps finding — an absence rendering as a fact — and it is
  far more likely here than against a documented endpoint.

The join is exact. Row `id` is the same uuid the v2 API returns, and the alias
table already holds those uuids, so a price lands on the right model without
name matching. That is the whole reason a measurement source beats a
marketplace: fal needed fuzzy matching and this needs none.

Attribution is required and the payload supplies it: the pages embed a
schema.org Dataset carrying AA's licence url and the citation they ask for.
Both are in `docs/sources.md`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, ClassVar

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

SITE = "https://artificialanalysis.ai"

#: What AA publishes, and what it means. `scale` turns the published number into
#: the unit Sieve stores: a thousand images is a thousand images, a minute is
#: sixty seconds. Exact arithmetic, no judgement.
#:
#: The units stay **different across modalities on purpose**. Per image and per
#: second are not comparable, and because `cost_per_task` reads a different
#: `Shape` field for each, comparing them is impossible by construction rather
#: than forbidden by convention.


@dataclass(frozen=True)
class Board:
    """One leaderboard page, and how to read it."""

    modality: Modality
    path: str
    key: str
    unit: Unit
    scale: float
    #: the second rate, where a board publishes an input and an output price
    out_key: str | None = None

    @property
    def url(self) -> str:
        return f"{SITE}{self.path}"


BOARDS: tuple[Board, ...] = (
    Board(
        "text-to-image",
        "/image/leaderboard/text-to-image",
        "pricePer1kImages",
        "usd_per_image",
        1 / 1000,
    ),
    Board(
        "image-editing", "/image/leaderboard/editing", "pricePer1kImages", "usd_per_image", 1 / 1000
    ),
    Board(
        "text-to-video",
        "/video/leaderboard/text-to-video",
        "pricePerMinute",
        "usd_per_second",
        1 / 60,
    ),
    Board(
        "image-to-video",
        "/video/leaderboard/image-to-video",
        "pricePerMinute",
        "usd_per_second",
        1 / 60,
    ),
    # a board Sieve has no modality for yet; part 10 job 2 adds `video-editing`
    Board("text-to-speech", "/text-to-speech", "pricePer1mCharacters", "usd_per_1m_chars", 1.0),
    Board("speech-to-text", "/speech-to-text", "pricePer1kMinutes", "usd_per_second", 1 / 60_000),
    Board(
        "speech-to-speech",
        "/speech-to-speech",
        "pricePerHourInput",
        "usd_per_second",
        1 / 3600,
        out_key="pricePerHourOutput",
    ),
)

#: `priceDisplayOverride` says the number on the row is not a price a caller can
#: pay. `no_api` is the one AA uses: the model has no public API. A model with no
#: public price is not a priced model, whatever number sits in the field.
SKIP_OVERRIDE = ("no_api",)

#: Next.js App Router. Each push carries a JSON string; concatenating the
#: unescaped chunks gives the flight payload.
_CHUNK = re.compile(r'self\.__next_f\.push\(\[1,\s*(".*?")\]\)', re.S)


class ShapeMovedError(Exception):
    """The page no longer looks the way this parser was written against."""


def flight(html: str) -> str:
    """The RSC payload, unescaped once. Empty when the page carries none."""
    return "".join(json.loads(found.group(1)) for found in _CHUNK.finditer(html))


def rows_of(payload: str, key: str) -> list[dict[str, Any]]:
    """Every object in the payload that carries `key`, decoded.

    There is deliberately **no anchor on a prop name**. The first version of
    this looked for `hostModels`, and the speech boards turned out to publish
    the same rows four times under `stsIndexHostModels`,
    `costPerHourOfInputAudioHostModels`, `pricingHostModels` and
    `tauChartModels` -- one array per chart, each a different subset. Anchoring
    on the price key itself finds all of them and cannot go stale when a chart
    is renamed.

    It is still a real parse: the key's enclosing `{` is located by bracket
    depth and the slice is handed to `json.JSONDecoder`. Anything that does not
    decode is not a row. Nothing here pattern-matches a number.
    """
    decoder = json.JSONDecoder()
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for found in re.finditer(re.escape(f'"{key}"'), payload):
        depth = 0
        start = -1
        for i in range(found.start(), -1, -1):
            if payload[i] == "}":
                depth += 1
            elif payload[i] == "{":
                if depth == 0:
                    start = i
                    break
                depth -= 1
        if start < 0 or start in seen:
            continue
        seen.add(start)
        try:
            value, _ = decoder.raw_decode(payload, start)
        except ValueError:
            continue
        if isinstance(value, dict):
            out.append(value)
    if not out:
        raise ShapeMovedError(f"nothing carries {key!r}; the payload shape has moved")
    return out


def price_of(row: dict[str, Any], board: Board) -> tuple[float, float | None] | None:
    """`(rate, output rate)` in the board's unit, or None.

    A rate of zero is not a price, and neither is a number the row itself says
    is not payable.
    """
    if str(row.get("priceDisplayOverride") or "") in SKIP_OVERRIDE:
        return None
    amount = as_float(row.get(board.key))
    if amount is None or amount <= 0:
        return None
    out = as_float(row.get(board.out_key)) if board.out_key else None
    return amount * board.scale, (out * board.scale if out and out > 0 else None)


def model_of(row: dict[str, Any]) -> tuple[str, str, list[str]] | None:
    """`(uuid, name, aliases)` — the uuid is the join and the rest is context.

    The `board` pages put the uuid on the row. The speech pages nest the model
    under `model`, and the row's own id identifies a *hosting* of it, so the
    inner id is the one that matches the catalogue.
    """
    values = row.get("values")
    if isinstance(values, dict):
        row = values
    inner = row.get("model")
    if isinstance(inner, dict) and isinstance(inner.get("id"), str):
        name = str(inner.get("name") or row.get("shortName") or row.get("name") or "")
        aliases = [
            str(row.get("id") or ""),
            str(row.get("name") or ""),
            str(inner.get("slug") or ""),
        ]
        return str(inner["id"]), name, [a for a in aliases if a]
    if isinstance(row.get("id"), str):
        name = str(row.get("name") or "")
        return str(row["id"]), name, [name] if name else []
    return None


class AAMediaPricesSource:
    """`Source` for the Artificial Analysis media leaderboards. Prices only."""

    name = "aa_media_prices"
    needs_key = False
    modalities: ClassVar[list[Modality]] = sorted({b.modality for b in BOARDS})

    def pull(self, cfg: SourceConfig, http: HttpClient) -> PullResult:
        result = PullResult(source=self.name)
        at = utcnow()
        wanted = set(cfg.modalities or self.modalities)
        seen: set[tuple[str, Modality]] = set()
        no_price = 0
        overridden = 0

        for board in BOARDS:
            if board.modality not in wanted:
                continue
            response = http.get(board.url)
            if response.status != 200:
                result.warnings.append(
                    f"aa_media_prices: HTTP {response.status} from {board.url} -- "
                    f"{board.modality} priced nothing this run"
                )
                result.ok = False
                continue

            body = response.body if isinstance(response.body, str) else ""
            try:
                rows = rows_of(flight(body), board.key)
            except (ShapeMovedError, ValueError) as exc:
                # Loudly. A page-shaped source that quietly returns nothing is
                # the failure this whole phase exists to prevent.
                result.warnings.append(
                    f"aa_media_prices: {board.url} no longer parses -- {exc}. Nothing stored "
                    f"for {board.modality}; the page shape has to be re-read before this "
                    "source can be trusted again"
                )
                result.ok = False
                continue

            for row in rows:
                found = model_of(row)
                if found is None:
                    continue
                uuid, name, aliases = found
                key = (uuid, board.modality)
                if key in seen:
                    continue
                seen.add(key)

                if str(row.get("priceDisplayOverride") or "") in SKIP_OVERRIDE:
                    overridden += 1
                    continue
                rate = price_of(row, board)
                if rate is None:
                    no_price += 1
                    continue

                creator = row.get("creator")
                creator_name = (
                    str(creator.get("name") or "") if isinstance(creator, dict) else ""
                ) or (
                    str((row.get("host") or {}).get("name") or "")
                    if isinstance(row.get("host"), dict)
                    else ""
                )
                result.models.append(
                    ModelRef(
                        id=uuid,
                        modality=board.modality,
                        name=name or uuid,
                        creator=creator_name or "unknown",
                        aliases=sorted({*aliases} - {uuid, ""}),
                    )
                )
                result.prices.append(
                    Price(
                        model_id=uuid,
                        source=self.name,
                        modality=board.modality,
                        unit=board.unit,
                        input=rate[0] if board.out_key else None,
                        output=rate[1] if board.out_key else None,
                        per_unit=None if board.out_key else rate[0],
                        source_url=board.url,
                        observed_at=at,
                    )
                )

        if overridden:
            result.warnings.append(
                f"aa_media_prices: {overridden} model(s) skipped, priceDisplayOverride says the "
                "number is not a price a caller can pay (no public API)"
            )
        if no_price:
            result.warnings.append(
                f"aa_media_prices: {no_price} scored model(s) carry no price on their board -- "
                "left unpriced, which is what a marketplace source is still for"
            )
        result.rate_limit = http.rate_limit()
        return result
