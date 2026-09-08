"""The single source of types for Sieve.

Everything two owners share is declared here (CONTRACTS.md section 1 and 2).
Code follows this module; this module does not follow code.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Any, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

Modality = Literal[
    "llm",
    "text-to-image",
    "image-editing",
    "text-to-video",
    "image-to-video",
    "text-to-speech",
    "speech-to-text",
    # Voice-to-voice models: one row per model with up to three published
    # scores. Added in phase 2 part 2 -- the free-tier endpoint existed and
    # nothing could store it, because the literal had no name for it.
    "speech-to-speech",
    "music",
]

MODALITIES: tuple[Modality, ...] = (
    "llm",
    "text-to-image",
    "image-editing",
    "text-to-video",
    "image-to-video",
    "text-to-speech",
    "speech-to-text",
    "music",
)

Unit = Literal[
    "index_0_100",
    "fraction",
    "elo",
    "usd_per_1m_tokens",
    "tokens_per_s",
    "seconds",
    "usd_per_image",
    "usd_per_second",
    "usd_per_1m_chars",
    "count",
    #: what one task costs at a profile's shape -- derived from a Price and a
    #: Shape by the engine, not published by any source.
    "usd_per_task",
]

Transform = Literal["identity", "neg_log", "log", "invert"]

Scope = Literal["read", "profiles:write", "apply", "telemetry"]

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


class Shape(_Model):
    """Per profile; only the keys the modality uses.

    The YAML spells the token fields `in` and `out` (PLAN section 4); both are
    accepted, and the JSON name stays `in_tokens` / `out_tokens`.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    in_tokens: int | None = Field(default=None, validation_alias="in")
    out_tokens: int | None = Field(default=None, validation_alias="out")
    cached: float | None = None
    images: int | None = None
    seconds: float | None = None
    chars: int | None = None


class Policy(_Model):
    margin: float = 3.0
    max_tenure_days: int = 14
    min_confidence: float = 0.75
    chain: int = 5
    suspend_below_health: float = 0.75
    auto_apply: bool = False
    require_telemetry: bool = False


class Profile(_Model):
    name: str
    modality: Modality
    purpose: str
    weights: dict[str, float]
    require: dict[str, Any] = Field(default_factory=dict)
    shape: Shape = Field(default_factory=Shape)
    policy: Policy = Field(default_factory=Policy)
    targets: list[str] = Field(default_factory=list)
    # Which effort mode of a family to seat when several are reachable.
    # "best" takes the highest mode, "cheapest_clearing" the lowest one that
    # still meets every floor, and a mode name pins that mode. None leaves
    # every mode in the ranking as its own row, which is the old behaviour.
    prefer_effort: str | None = None


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
    dominated_by: str | None = None
    excluded_by: str | None = None
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
class Target(Protocol):
    name: str

    def current(self, cfg: TargetConfig) -> dict[str, list[str]]: ...

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
EXPORTED: tuple[type[BaseModel], ...] = (
    ModelRef,
    Observation,
    Capability,
    Price,
    Reachable,
    AxisField,
    Axis,
    Shape,
    Policy,
    Profile,
    AxisScore,
    Rank,
    Ranking,
    Chain,
    Decision,
    TelemetryEvent,
    HealthRow,
    SourceConfig,
    InventoryConfig,
    TargetConfig,
    RateLimit,
    PullResult,
    TargetResult,
    EngineResult,
)
