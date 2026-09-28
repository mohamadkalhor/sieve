"""Response models for the `/v1` reads an agent calls first.

These describe what the routes already return; none of them may change a body.
`tests/test_response_models.py` compares every one against a golden capture
taken before they existed. Where a value is free-form on purpose it is typed
loosely (`dict[str, Any]`) rather than guessed at, and every model allows extra
keys so a field added to a route later is passed through, not dropped.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sieve.contracts import ModelRef, Price


class _Open(BaseModel):
    model_config = ConfigDict(extra="allow")


class ModelListing(ModelRef):
    """One catalogue row: the model, whether this box can call it, and its latest price."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    reachable: bool
    local_ids: list[str] = Field(default_factory=list)
    price: Price | None = None


class ModelsPage(_Open):
    items: list[ModelListing]
    next_cursor: str | None = None


class RecommendedModel(_Open):
    id: str
    local_ids: list[str] = Field(default_factory=list)
    #: score in 0..1, or null when the answer is the shipped chain (it has no score)
    final: float | None = None
    confidence: float | None = None


class Recommendation(_Open):
    profile: str
    models: list[RecommendedModel]
    computed_at: datetime


class RunRow(_Open):
    id: str
    step: str
    requested_by: str
    started: str
    finished: str | None = None
    running: bool
    ok: bool | None = None
    summary: str | None = None
    error: str | None = None
    seconds: float
    has_log: bool


class StatusUser(_Open):
    name: str | None = None
    scopes: list[str] = Field(default_factory=list)
    role: str


class StatusResponse(_Open):
    """When Sieve last looked, how often, and what is running now."""

    pulled_at: str | None = None
    ran_at: str | None = None
    schedule: Any = None
    runs: dict[str, Any]
    schedules: list[dict[str, Any]]
    sources_enabled: int
    telemetry_calls: int
    reachable: int
    unscored: int
    telemetry_at: str | None = None
    user: StatusUser | None = None


class ConfigExport(_Open):
    """One owner's configuration bundle, as `PUT /v1/config` accepts it back."""

    version: int
    exported_at: str
    profiles: list[dict[str, Any]]
    axes: list[dict[str, Any]]
    cost_multipliers: dict[str, float]
    connectors: list[dict[str, Any]]
