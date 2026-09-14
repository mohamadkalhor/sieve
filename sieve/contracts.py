"""The single source of types for Sieve.

Everything two owners share is declared here (CONTRACTS.md section 1 and 2).
Code follows this module; this module does not follow code.
"""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from typing import Any, Literal, Protocol, get_args, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

Modality = Literal[
    "llm",
    "text-to-image",
    "image-editing",
    "text-to-video",
    "image-to-video",
    # Editing an existing video, as distinct from generating one. Artificial
    # Analysis runs it as its own arena and the v2 data API does not expose it
    # at all; the leaderboard page does. Added in part 10.
    "video-editing",
    "text-to-speech",
    "speech-to-text",
    # Voice-to-voice models: one row per model with up to three published
    # scores. Added in phase 2 part 2 -- the free-tier endpoint existed and
    # nothing could store it, because the literal had no name for it.
    "speech-to-speech",
    "music",
]

#: Derived, not retyped. The hand-written tuple had drifted: `speech-to-speech`
#: was added to `Modality` in part 2 and never added here, so anything that
#: walked `MODALITIES` skipped a modality the catalogue holds 42 models for.
MODALITIES: tuple[Modality, ...] = get_args(Modality)

Unit = Literal[
    "index_0_100",
    "fraction",
    "elo",
    "usd_per_1m_tokens",
    "tokens_per_s",
    "seconds",
    "usd_per_image",
    "usd_per_second",
    #: Hardware time, not output length. PLAN 2.3: "$0.00111 per compute second"
    #: and "$0.025 per second of generated video" are not the same unit, and
    #: comparing them makes an upscaler look cheaper than a video model on a
    #: number that means something else. Stored, named, never costed.
    "usd_per_compute_second",
    "usd_per_1m_chars",
    "usd_per_megapixel",
    #: One whole request, where a source names no other unit -- "your request
    #: will cost $0.25 for 512p resolution".
    "usd_per_request",
    "count",
    #: what one task costs at a profile's shape -- derived from a Price and a
    #: Shape by the engine, not published by any source.
    "usd_per_task",
]

#: Units that can only describe a picture. Nothing audible has a pixel.
VISUAL_UNITS: frozenset[Unit] = frozenset({"usd_per_image", "usd_per_megapixel"})

#: The modalities whose output is sound.
AUDIO_MODALITIES: frozenset[Modality] = frozenset(
    {"text-to-speech", "speech-to-text", "speech-to-speech", "music"}
)


def unit_fits_modality(modality: Modality | None, unit: Unit) -> bool:
    """Whether a rate in `unit` could describe a model of this modality.

    Deliberately narrow. Almost every unit is defensible somewhere -- an image
    model really is billed per token by some vendors, and a video model really
    is billed per megapixel -- so a strict table would refuse real prices, and
    PLAN 2.3 is clear that a refused real price is its own kind of lie. The one
    thing no source can mean is a **picture** unit on a model that emits
    **sound**: a marketplace that lists one endpoint under several categories
    put a video rate on an audio row, and $0.0024 per megapixel was then served
    as the price of a music model.
    """
    if modality is None:
        return True
    return not (modality in AUDIO_MODALITIES and unit in VISUAL_UNITS)


Transform = Literal["identity", "neg_log", "log", "invert"]

Scope = Literal["read", "profiles:write", "apply", "telemetry"]

#: How many models a profile ships, and the room that number has to move in.
#: One is a routing decision with no fallback; ten is more fallbacks than any
#: gateway has ever walked.
SHIP_MIN = 1
SHIP_MAX = 10
SHIP_DEFAULT = 4

SCOPES: tuple[Scope, ...] = ("read", "profiles:write", "apply", "telemetry")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


# --------------------------------------------------------------------------- #
# 1. Types
# --------------------------------------------------------------------------- #


class ModelRef(_Model):
    """One model, in one modality, under its canonical id."""

    id: str
    modality: Modality
    name: str
    creator: str
    aliases: list[str] = Field(default_factory=list)
    release_date: date | None = None
    # Reasoning effort. A source that publishes one row per mode -- AA does,
    # for 230 of its 644 -- describes a different model in each, at the same
    # price per token. `family` groups the modes of one model so a profile
    # can choose between them; `effort` says which mode this row is.
    effort: str | None = None
    family: str | None = None


class Observation(_Model):
    """One measured value. Append-only; nothing is ever overwritten."""

    model_id: str
    modality: Modality
    source: str
    field: str
    value: float
    unit: Unit
    n: int | None = None
    ci95: float | None = None
    observed_at: datetime
    pulled_at: datetime


class Capability(_Model):
    """What a model can do. From inventory and OpenRouter; never from a benchmark."""

    tools: bool | None = None
    reasoning: bool | None = None
    structured_output: bool | None = None
    context_window: int | None = None
    max_output: int | None = None
    input_modalities: list[str] = Field(default_factory=list)
    output_modalities: list[str] = Field(default_factory=list)


class Price(_Model):
    model_id: str
    source: str
    # Which modality this price is for. A media model is priced per endpoint --
    # one rate per image, another per second of video -- so a price keyed on the
    # model alone puts the wrong number on the other row. None means "whatever
    # modality this model is in", which is what a single-modality source means.
    modality: Modality | None = None
    unit: Unit
    input: float | None = None
    output: float | None = None
    cached_input: float | None = None
    per_unit: float | None = None
    source_url: str | None = None
    # Which tier this rate is, when a source prices one model several ways:
    # "480p", "1080p", "batch". A tiered price is not unparseable, it is
    # several prices, and dropping the model was losing more than it protected.
    # The cheapest tier is stored and named; a profile shape may one day ask
    # for a particular one.
    tier: str | None = None
    observed_at: datetime


class Reachable(_Model):
    """A model the user can actually reach, and where."""

    inventory: str
    local_id: str
    model_id: str | None = None
    capability: Capability = Field(default_factory=Capability)
    seen_at: datetime
    # True once a later pull of the same inventory stopped listing this id. The
    # row is kept so "we used to reach it here" stays answerable, but nothing
    # routes through it: `seen_at` then reads as the last time it was there.
    stale: bool = False


class Connector(_Model):
    """A router added at runtime: where it is, and what it is switched on for.

    The whole reason this type exists is that a router used to be two blocks of
    `sieve.toml` -- one to read its ids, one to write its combos -- which meant
    adding a second one needed a file on the box and a restart. A connector is
    the same facts as a row, so it can be added by URL, tested, and switched on
    for reading, for writing, or for both.

    **It never holds a token.** `token_env` is the *name* of the environment
    variable, exactly as a source names `key_env`. Nothing stores the value,
    nothing serves it, and a database that leaks leaks a list of variable names.
    """

    id: str
    name: str
    #: "openai_compat" | "ninerouter" -- see `sieve/connectors/registry.py`
    kind: str
    base_url: str
    token_env: str | None = None
    #: its ids join the inventory, so a profile may seat a model it serves
    read: bool = True
    #: it is given the chains: one combo per profile
    write: bool = False
    poll_minutes: int = 60
    last_pull_at: datetime | None = None
    last_push_at: datetime | None = None
    #: the last sentence this connector failed with, or None. Kept so that "the
    #: gateway is quiet" and "the gateway has been refusing us since Tuesday"
    #: do not look the same on a screen.
    last_error: str | None = None
    #: kind-specific, and every key *names* something rather than holding it:
    #: `admin_token_env`, `timeout`.
    options: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    #: whose router this is (`users.id`). NULL on a box where nobody has signed
    #: in: a connector is never shared, because writing a combo to it spends
    #: somebody's token.
    owner_id: str | None = None

    def token(self) -> str | None:
        """The secret this connector needs, read from the environment only."""
        return os.environ.get(self.token_env) if self.token_env else None


class ConnectorTest(_Model):
    """What `POST /v1/connectors/{id}/test` answers. Never raises upward.

    A router that is down is an ordinary answer to "is this working", so the
    call is 200 with `ok: false` and the reason, not a 500 with a traceback.
    """

    ok: bool
    models_count: int = 0
    error: str | None = None


class ComboResult(_Model):
    """One chain seated on one connector."""

    ok: bool
    #: whether the combo had to be created rather than updated
    created: bool = False
    error: str | None = None


class AxisField(_Model):
    source: str
    field: str
    weight: float = 1.0
    transform: Transform = "identity"
    min_n: int | None = None
    phase: int = 1


class Axis(_Model):
    name: str
    modality: Modality
    label: str
    describes: str
    fields: list[AxisField]
    missing: Literal["renormalise", "penalise"] = "renormalise"
    min_coverage: float = 0.5
    higher_is_better: bool = True


class ProfileSettings(_Model):
    """The tuned half of a profile: its weights, and how many models to ship.

    There is nothing else, on purpose. A floor, a price sensitivity, an
    experience weight, per-weight bounds and an auto-apply switch all used to
    live here, and every one of them changed the answer without moving a
    slider -- which made the sliders unreadable.
    """

    ship: int = Field(default=SHIP_DEFAULT, ge=SHIP_MIN, le=SHIP_MAX)
    weights: dict[str, float] = Field(default_factory=dict)


class Outcome(_Model):
    profile: str
    model_id: str
    local_id: str
    ok: bool
    seconds: float = Field(ge=0)
    vote: int = Field(ge=-1, le=1)
    note: str | None = None
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Profile(_Model):
    """One seat: what it is for, what it cares about, and how many to ship.

    Every axis in `weights` is a share of the score and the shares add to one,
    so raising one lowers the others and the whole profile is readable at a
    glance. `ship` is the length of the list: the first model is used, the rest
    are fallbacks.
    """

    name: str
    modality: Modality
    purpose: str
    weights: dict[str, float]
    ship: int = Field(default=SHIP_DEFAULT, ge=SHIP_MIN, le=SHIP_MAX)


class AxisScore(_Model):
    axis: str
    value: float | None
    coverage: float
    contribution: float


class Rank(_Model):
    position: int
    model_id: str
    reachable: bool
    local_ids: list[str] = Field(default_factory=list)
    score: float
    confidence: float
    health: float
    final: float
    axes: list[AxisScore] = Field(default_factory=list)
    cost_per_task: float | None = None
    # Where that cost came from. "shape" means the profile's declared token
    # counts -- an estimate, and the same one for every effort mode of a model,
    # since no source publishes per-task tokens. "telemetry" means the model's
    # own observed output tokens. A cost nobody can tell is an estimate is worse
    # than one that admits it.
    cost_from: Literal["shape", "telemetry"] | None = None
    flip: str | None = None


class Ranking(_Model):
    profile: str
    modality: Modality
    computed_at: datetime
    snapshot: str
    ranks: list[Rank] = Field(default_factory=list)


class Chain(_Model):
    profile: str
    computed_at: datetime
    primary: str
    fallbacks: list[str] = Field(default_factory=list)
    local: dict[str, list[str]] = Field(default_factory=dict)
    incumbent: str | None = None
    incumbent_since: datetime | None = None


DecisionKind = Literal["switch", "hold", "suspend", "weights", "policy", "apply", "pull"]


class Decision(_Model):
    id: str
    at: datetime
    profile: str
    kind: DecisionKind
    actor: str
    before: Any | None = None
    after: Any | None = None
    reason: str
    detail: dict[str, Any] = Field(default_factory=dict)


class TargetDiff(_Model):
    """What one target holds *now*, against what the engine would write.

    The Chains screen used to diff against the last `apply` decision, which only
    says what Sieve believes it wrote. A target that drifts underneath -- edited
    by hand, rolled back, or written by something else -- showed no difference at
    all. This asks the target.

    `supported` is false for a target that cannot read back, like a webhook.
    That is not the same as "holds nothing", and the two must not look alike:
    an empty `current` would read as "everything is a change".
    """

    target: str
    kind: str
    supported: bool = True
    #: profile -> the model ids the target holds now, in order
    current: dict[str, list[str]] = Field(default_factory=dict)
    #: what `write()` would put there, in the same vocabulary. A target that
    #: routes by the gateway's own local ids plans in those, so the two sides
    #: of the comparison are the same kind of thing.
    planned: dict[str, list[str]] = Field(default_factory=dict)
    error: str | None = None


class BoardRow(_Model):
    """One model on the media ranking."""

    model_id: str
    name: str
    creator: str
    value: float
    #: ids that collapsed into this one. Artificial Analysis publishes the same
    #: model under several identities -- `Wan 3.0` and `Wan Text to Video` carry
    #: an identical score -- and an undeduplicated top ten is five models each
    #: printed twice.
    merged: list[str] = Field(default_factory=list)


class Leaderboard(_Model):
    """A media ranking, and why the screen looks the way it does.

    The Field scatter plots quality against cost, so a point needs both. Media
    models almost never have both, so `scatter_ok` says whether that chart is
    answerable at all for this modality; where it is not, this ranking is shown
    in its place rather than a handful of dots over an empty field.
    """

    modality: Modality
    #: the field ranked on, e.g. `elo` or `elo:with_vocals`
    metric: str
    #: every metric this modality could be ranked on, best first
    metrics: list[str] = Field(default_factory=list)
    rows: list[BoardRow] = Field(default_factory=list)
    scored: int = 0
    priced: int = 0
    priced_share: float = 0.0
    scatter_ok: bool = False
    #: the population's own endpoints, so a bar length is honest about the
    #: spread rather than rescaled to look dramatic
    low: float | None = None
    high: float | None = None
    #: set when there is no ranking to draw, and says why in a sentence
    reason: str | None = None
    #: which scoreboards these rows came from. `elo` is Artificial Analysis's
    #: own unit for its media arenas, and a screen that only prints the unit
    #: reads as though some fourth party ranked them -- so the source is named.
    #: More than one entry means two scoreboards are being compared, which is a
    #: thing the reader must be able to see.
    sources: list[str] = Field(default_factory=list)


class HealthRow(_Model):
    """One model's own traffic, for the Pulse screen. `GET /v1/health` serves these.

    `health` is the number the ranking multiplies the score by; everything else
    is why it is that number. A field is None when nothing was measured -- no
    calls, or none that reported a latency -- never 0, because "nobody called
    it" and "every call failed" are opposite facts.
    """

    model_id: str
    local_ids: list[str] = Field(default_factory=list)
    health: float
    #: one health value per day, oldest first; a day with no calls is None
    series: list[float | None] = Field(default_factory=list)
    window: str = "24h"
    events: int = 0
    ok_rate: float | None = None
    rate_limited_share: float | None = None
    p50_latency_ms: float | None = None
    p95_latency_ms: float | None = None
    #: what a call really burned, which is the only thing that can separate the
    #: cost of one effort mode from another. See PLAN 2.1.
    median_tokens_out: float | None = None


class TelemetryEvent(_Model):
    model: str
    profile: str | None = None
    ok: bool
    status: int | None = None
    latency_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    at: datetime


# --------------------------------------------------------------------------- #
# 2. Plugin interfaces
# --------------------------------------------------------------------------- #


class SourceConfig(_Model):
    """One `[sources.<name>]` block of sieve.toml, resolved."""

    name: str
    enabled: bool = True
    key_env: str | None = None
    modalities: list[Modality] = Field(default_factory=list)
    dir: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)

    def key(self) -> str | None:
        """The secret this source needs, read from the environment only."""
        return os.environ.get(self.key_env) if self.key_env else None


class InventoryConfig(_Model):
    name: str
    kind: str
    base_url: str | None = None
    token_env: str | None = None
    models: list[str] = Field(default_factory=list)
    options: dict[str, Any] = Field(default_factory=dict)

    def token(self) -> str | None:
        return os.environ.get(self.token_env) if self.token_env else None


class TargetConfig(_Model):
    name: str
    kind: str
    dir: str | None = None
    url: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)


class RateLimit(_Model):
    limit: int | None = None
    remaining: int | None = None
    reset_at: datetime | None = None


class PullResult(_Model):
    source: str
    #: False when the source could not be read -- an HTTP failure, not an
    #: empty answer. A caller needs to tell "the endpoint is down" from "the
    #: endpoint published nothing new", because the first should fail a
    #: scheduled run and the second is an ordinary quiet hour.
    ok: bool = True
    models: list[ModelRef] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    prices: list[Price] = Field(default_factory=list)
    #: model_id -> what the source says the model can do. Never from a
    #: benchmark: only a catalogue that publishes it (OpenRouter today).
    capabilities: dict[str, Capability] = Field(default_factory=dict)
    #: Model ids whose modality is a *claim*, not a reading. A source whose
    #: own category spans more than one of our modalities cannot say which
    #: one a row is -- fal files music and sound effects together under
    #: `text-to-audio`. The rule (PLAN 2.2): claim the modality only when a
    #: source that *does* separate them agrees, which means the id folded onto
    #: one the catalogue already holds. Anything unconfirmed is dropped and
    #: counted, never assigned.
    provisional: set[str] = Field(default_factory=set)
    rate_limit: RateLimit = Field(default_factory=RateLimit)
    warnings: list[str] = Field(default_factory=list)


class TargetResult(_Model):
    target: str
    written: list[str] = Field(default_factory=list)
    dry_run: bool = False
    detail: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class HttpResponse(_Model):
    url: str
    status: int
    headers: dict[str, str] = Field(default_factory=dict)
    body: Any = None


@runtime_checkable
class HttpClient(Protocol):
    """The only way to the network.

    Sets timeouts, retries with backoff on 429/5xx, records rate-limit headers,
    and is replaced by a fixture player in tests.
    """

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
    ) -> HttpResponse: ...

    def rate_limit(self) -> RateLimit: ...


@runtime_checkable
class Source(Protocol):
    name: str
    modality: list[Modality]
    needs_key: bool

    def pull(self, cfg: SourceConfig, http: HttpClient) -> PullResult: ...


@runtime_checkable
class Inventory(Protocol):
    name: str

    def list(self, cfg: InventoryConfig, http: HttpClient) -> list[Reachable]: ...


@runtime_checkable
class ConnectorAdapter(Protocol):
    """One kind of router. Adding a kind is one file under `sieve/connectors`.

    Not an entry-point group like the three above: a connector is chosen by a
    row in the database while a request is in flight, and a lookup that can fail
    with `ImportError` halfway through an hourly run is a worse trade than a
    table a reader can see all of.
    """

    kind: str
    #: whether this kind can be given combos at all. A connector asking to write
    #: through a kind that cannot is refused when it is created, rather than
    #: accepted and then shipping nothing every hour.
    writes: bool

    def list_models(self) -> list[str]: ...

    def test(self) -> ConnectorTest: ...

    def put_combo(self, name: str, ordered_ids: list[str]) -> ComboResult: ...


@runtime_checkable
class Target(Protocol):
    name: str

    def current(self, cfg: TargetConfig) -> dict[str, list[str]]: ...

    # Optional. What `write()` *would* put there, in the same vocabulary
    # `current()` reads back. A target that routes by the gateway's own local
    # ids must implement it, or a diff compares canonical ids against local
    # ones and reports a change on every run. The default is the chain itself.
    def plan(self, cfg: TargetConfig, chains: list[Chain]) -> dict[str, list[str]]: ...

    def write(self, cfg: TargetConfig, chains: list[Chain], dry_run: bool) -> TargetResult: ...


# --------------------------------------------------------------------------- #
# Shared read model for scoring (section 3, `ObsTable`)
# --------------------------------------------------------------------------- #


class ObsTable(_Model):
    """The latest observation per (model, source, field), plus prices.

    Built by the engine from the store and handed to the pure scoring code, so
    `sieve/axes` and `sieve/scoring` never touch SQLite.
    """

    modality: Modality
    latest: dict[str, dict[str, Observation]] = Field(default_factory=dict)
    prices: dict[str, Price] = Field(default_factory=dict)

    @staticmethod
    def key(source: str, field: str) -> str:
        return f"{source}:{field}"

    def get(self, model_id: str, source: str, field: str) -> Observation | None:
        return self.latest.get(model_id, {}).get(self.key(source, field))

    def price(self, model_id: str) -> Price | None:
        return self.prices.get(model_id)

    def add(self, obs: Observation) -> None:
        """Keep the newest observation per (model, source, field)."""
        bucket = self.latest.setdefault(obs.model_id, {})
        k = self.key(obs.source, obs.field)
        held = bucket.get(k)
        if held is None or obs.observed_at >= held.observed_at:
            bucket[k] = obs

    def models(self) -> list[str]:
        return sorted(self.latest)


class EngineResult(_Model):
    """What one engine run produced."""

    snapshot: str
    at: datetime
    rankings: list[Ranking] = Field(default_factory=list)
    chains: list[Chain] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    targets: list[TargetResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    dry_run: bool = True


#: The models `sieve export-types` renders into `web/src/lib/types.ts`.
#:
#: `Connector` is deliberately not here yet. `types.ts` is generated **and**
#: committed, and a test fails if the two disagree, so the type joins this
#: tuple in the same commit that regenerates the file -- which is the one
#: that builds the Connectors screen, in `web/`.
EXPORTED: tuple[type[BaseModel], ...] = (
    ModelRef,
    Observation,
    Capability,
    Price,
    Reachable,
    AxisField,
    Axis,
    Profile,
    AxisScore,
    Rank,
    Ranking,
    Chain,
    Decision,
    TelemetryEvent,
    HealthRow,
    BoardRow,
    Leaderboard,
    TargetDiff,
    SourceConfig,
    InventoryConfig,
    TargetConfig,
    RateLimit,
    PullResult,
    TargetResult,
    EngineResult,
)
