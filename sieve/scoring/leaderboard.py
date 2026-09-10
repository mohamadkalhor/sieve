"""The media ranking: one metric, one modality, best first.

The Field chart plots a quality axis against cost, so a point needs **both**. On
media a model almost never has both — measured on the recordings, 5 of 313
scored media models carry a price, against 60 of 60 for LLMs. A scatter drawn
from that is five dots over an empty field, and it reads as "there are five
text-to-image models", which is false.

That is the same failure this whole phase keeps finding: **an absence rendering
as a fact.** So where cost is not answerable, the screen answers the question
that is — which of these is best — and says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from dataclasses import field as dataclass_field

from sieve.contracts import Modality, ModelRef, ObsTable

#: Below this share of scored models carrying a price, a cost scatter is more
#: misleading than useful and the ranking is shown instead. A number, in code,
#: rather than a rule living in somebody's head.
MIN_PRICED_SHARE = 0.25

#: The field that carries the quality of a model in each modality. Checked
#: against the recordings: these are the ones that actually hold a value.
METRIC: dict[Modality, tuple[str, ...]] = {
    "text-to-image": ("elo",),
    "image-editing": ("elo",),
    "text-to-video": ("elo",),
    "image-to-video": ("elo",),
    "video-editing": ("elo",),
    "text-to-speech": ("elo",),
    # two different contests, and a model good at one can be ordinary at the
    # other. With vocals is the default; instrumental is offered beside it.
    "music": ("elo:with_vocals", "elo:instrumental"),
    # Artificial Analysis weights the three equally, so the mean is its own
    # ranking rather than an invention -- but only where all three exist.
    "speech-to-speech": ("bba_score", "fdb_score", "tau_voice_score"),
}

#: Modalities that publish a number too coarse to rank on. Saying so in one line
#: is the honest screen; a chart of ties is not.
NO_RANKING: dict[Modality, str] = {
    "speech-to-text": (
        "Artificial Analysis publishes aa_wer_index rounded to one decimal, so 44 of the "
        "58 models that report a value all read 0.0. That is not a ranking, it is a tie "
        "with three exceptions, and drawing it as a chart would invent an order that the "
        "data does not contain."
    ),
}


@dataclass
class Row:
    """One model on the board."""

    model_id: str
    name: str
    creator: str
    value: float
    #: every id that collapsed into this one, for the reader who wonders where
    #: a name they expected went
    merged: list[str] = dataclass_field(default_factory=list)


@dataclass
class Board:
    """A ranking for one modality, and why it looks the way it does."""

    modality: Modality
    metric: str
    rows: list[Row]
    scored: int
    priced: int
    low: float | None = None
    high: float | None = None
    reason: str | None = None
    #: the sources these rows actually came from, in the order `_value` prefers
    #: them. Usually one. More than one means the board is comparing numbers
    #: from two scoreboards, which the reader has to be told.
    sources: list[str] = dataclass_field(default_factory=list)

    @property
    def priced_share(self) -> float:
        return self.priced / self.scored if self.scored else 0.0

    @property
    def scatter_ok(self) -> bool:
        """Is a quality-against-cost chart answerable for this modality?"""
        return self.priced_share >= MIN_PRICED_SHARE


def metrics_for(modality: Modality) -> tuple[str, ...]:
    """Which fields this modality can be ranked on, best choice first."""
    return METRIC.get(modality, ())


def _value(obs: ObsTable, model_id: str, metric: str) -> tuple[float, str] | None:
    """One model's value for one metric, **and which source published it**.

    The source is returned rather than discarded because this function falls
    back through three of them. A board built entirely from Artificial Analysis
    and a board that quietly mixed in an arena look identical once the number is
    on screen, and "ranked by elo" reads like a fourth party's metric when it is
    in fact AA's own unit. `Board.sources` carries this up so the screen can say
    whose ranking it is showing.

    `speech-to-speech` is the exception: its three scores are averaged, and only
    where **all three** are present. They have very different coverage --
    bba_score on 34 of 38 models, tau_voice_score on 21 -- so averaging whatever
    happens to exist would rank a model measured on one axis against a model
    measured on three and call the comparison fair.
    """
    if metric == "speech-to-speech":
        parts = []
        for name in METRIC["speech-to-speech"]:
            found = obs.get(model_id, "aa_media", name)
            if found is None:
                return None
            parts.append(found.value)
        return sum(parts) / len(parts), "aa_media"

    for source in ("aa_media", "arena", "aa_llm"):
        found = obs.get(model_id, source, metric)
        if found is not None:
            return found.value, source
    return None


def _key(value: float) -> float:
    """The value, kept to about four significant figures.

    "Rounded to one decimal" is the right tolerance for an Elo near 1200, where
    a tenth of a point is nothing. It is far too coarse for a 0-1 score: the
    speech-to-speech ratings all sit near 0.8, and rounding those to a tenth
    merged three genuinely different models into one row. Scaling the precision
    to the magnitude means one rule that is right on both.
    """
    if value == 0:
        return 0.0
    places = max(1, 4 - math.floor(math.log10(abs(value))) - 1)
    return round(value, places)


def deduplicate(rows: list[Row]) -> list[Row]:
    """Collapse the same model published under more than one identity.

    Artificial Analysis lists `Wan 3.0` and `Wan Text to Video` as separate
    models with the identical score, and `Gemini Omni Flash 1.1 Text to Video`
    beside `... Image to Video`. Undeduplicated, a top ten is five models each
    printed twice, which is a worse lie than showing five.

    The key is `(creator, value to about four significant figures)`: two rows
    from one creator with the same score to that precision are one measurement
    published twice. The shortest name wins, because the short one is the
    model's name and the long one describes an endpoint.
    """
    groups: dict[tuple[str, float], list[Row]] = {}
    for row in rows:
        groups.setdefault((row.creator, _key(row.value)), []).append(row)

    out: list[Row] = []
    for group in groups.values():
        if len(group) == 1:
            out.append(group[0])
            continue
        keeper = min(group, key=lambda r: (len(r.name), r.name))
        keeper.merged = sorted(r.model_id for r in group if r.model_id != keeper.model_id)
        out.append(keeper)
    return out


def board(
    modality: Modality,
    obs: ObsTable,
    models: list[ModelRef],
    priced: set[str],
    metric: str | None = None,
) -> Board:
    """The ranking for one modality, deduplicated and sorted best first."""
    if modality in NO_RANKING:
        return Board(
            modality=modality,
            metric="",
            rows=[],
            scored=len({m.id for m in models}),
            priced=len(priced),
            reason=NO_RANKING[modality],
        )

    choices = metrics_for(modality)
    if not choices:
        return Board(
            modality=modality,
            metric="",
            rows=[],
            scored=len({m.id for m in models}),
            priced=len(priced),
            reason=f"nothing published for {modality} can be ranked on.",
        )

    chosen = metric if metric in choices else choices[0]
    if modality == "speech-to-speech":
        chosen = "speech-to-speech"

    by_id = {m.id: m for m in models}
    rows: list[Row] = []
    from_sources: set[str] = set()
    for model_id, model in sorted(by_id.items()):
        found = _value(obs, model_id, chosen)
        if found is None:
            continue
        value, source = found
        from_sources.add(source)
        rows.append(
            Row(
                model_id=model_id,
                name=model.name or model_id,
                creator=model.creator or model_id.split("/", 1)[0],
                value=value,
            )
        )

    rows = deduplicate(rows)
    rows.sort(key=lambda r: (-r.value, r.model_id))

    values = [r.value for r in rows]
    return Board(
        modality=modality,
        metric=chosen,
        rows=rows,
        scored=len(by_id),
        priced=len(priced),
        # the population's own endpoints, so a bar is honest about the spread
        low=min(values) if values else None,
        high=max(values) if values else None,
        sources=[s for s in ("aa_media", "arena", "aa_llm") if s in from_sources],
    )
