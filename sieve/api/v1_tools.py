"""§3.9 on sieve: `POST /v1/mcp` serves the tools below, in-process.

A tool here is not code. It is a name for a `/v1` route sieve already serves,
with the route's own scope and a pydantic model of what the route takes and
answers. `agentkit.mcp` dispatches every call back into the route with the
*caller's own* credential, so a tool runs the route's credential rule, scope,
limits, idempotency and handler and can do nothing its route would refuse.
`tests/test_mcp_kit.py` runs the kit's `check_parity` over this registry, so a
route and its tool cannot drift apart.

The ten names `sieve mcp` (stdio) has always had are all here, plus the reads an
agent wants first: `list_models`, `leaderboard`, `status`, `list_runs` and
`export_config`. `sieve mcp` itself is unchanged in kind: it is a local process
and keeps its own `SIEVE_TOKEN` bearer; this module is the hosted door, and its
caller sends an ordinary API key.

Things that follow from "a tool is a route":

* A write tool's arguments other than the path ones are the route's JSON body.
  `set_weights` and `set_ship` take `name` and then the body itself: for
  `set_weights` each further argument is an axis weight (`{"name": "coder",
  "quality": 0.6, "cost": 0.4}`), for `set_ship` it is `ship`. Their routes take
  a free-form object, so the schema publishes `name` only.
* `report_outcome` takes `events` and goes to `POST /v1/telemetry/batch`, the
  object-bodied twin of `POST /v1/telemetry` (a bare list cannot be a tool's
  arguments). `explain` goes to `GET /v1/rankings/{profile}/explain`.
* A route with no parameter at all cannot be a tool whose schema both the
  kit's `check_parity` and its conformance suite accept, so `status` and
  `export_config` take one real optional filter, `sections`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, RootModel

from sieve.api import aio
from sieve.api.routes import v1 as routes_v1
from sieve.api import v1_models
from sieve.contracts import Leaderboard, Modality, Profile, Ranking

_REGISTRY: Any = None


def _without_null_defaults(node: Any) -> Any:
    """`node` with every `"default": null` taken out, at any depth."""
    if isinstance(node, dict):
        return {
            key: _without_null_defaults(value)
            for key, value in node.items()
            if not (key == "default" and value is None)
        }
    if isinstance(node, list):
        return [_without_null_defaults(item) for item in node]
    return node


class _Spec:
    """Publish the schema the way the route's own OpenAPI does.

    FastAPI leaves `"default": null` out of a route's parameters and body
    models; pydantic writes it. The parity check compares the two literally, so
    the tool's models are described the way the route's are.
    """

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        kwargs.setdefault("mode", "serialization")
        found = super().model_json_schema(*args, **kwargs)  # type: ignore[misc]
        return _without_null_defaults(found)  # type: ignore[no-any-return]


class Shape(_Spec, BaseModel):
    """The base of every tool's arguments."""


class AnyJSON(_Spec, RootModel[Any]):
    """An answer whose shape is the route's own and is not modelled."""


class Object(_Spec, RootModel[dict[str, Any]]):
    """A JSON object."""


class Objects(_Spec, RootModel[list[dict[str, Any]]]):
    """A list of JSON objects."""


class Counts(_Spec, RootModel[dict[str, int]]):
    """Names to counts."""


class ProfileList(_Spec, RootModel[list[Profile]]):
    """`GET /v1/profiles`'s answer."""


class ProfileOut(_Spec, Profile):
    """`GET /v1/profiles/{name}`'s answer."""


class RankingOut(_Spec, Ranking):
    """`GET /v1/rankings/{profile}`'s answer."""


class RecommendationOut(_Spec, v1_models.Recommendation):
    """`GET /v1/recommend`'s answer."""


class ModelsOut(_Spec, v1_models.ModelsPage):
    """`GET /v1/models`'s answer."""


class StatusOut(_Spec, v1_models.StatusResponse):
    """`GET /v1/status`'s answer."""


class RunsOut(_Spec, RootModel[list[v1_models.RunRow]]):
    """`GET /v1/runs`'s answer."""


class ConfigOut(_Spec, v1_models.ConfigExport):
    """`GET /v1/config`'s answer."""


class ApplyArgs(_Spec, routes_v1.ApplyBody):
    """`POST /v1/apply`'s body: which profiles, and optionally which targets."""


class LeaderboardOut(_Spec, Leaderboard):
    """`GET /v1/leaderboard`'s answer."""


class Profiles(Shape):
    modality: Modality | None = None


class OneProfile(Shape):
    name: str


class ProfileArg(Shape):
    profile: str


class Recommend(Shape):
    profile: str
    n: int = 3
    reachable_only: bool = True


class Models(Shape):
    modality: Modality | None = None
    reachable: bool | None = None
    q: str | None = None
    limit: int = Field(100, le=1000)
    cursor: str | None = None


class LeaderboardArgs(Shape):
    modality: Modality
    metric: str | None = None


class Sections(Shape):
    sections: str | None = None  # comma-separated top-level keys to keep


class Runs(Shape):
    limit: int = Field(20, ge=1, le=200)
    step: str | None = None


class Outcomes(Shape):
    events: list[routes_v1.TelemetryEvent]


def build() -> Any:
    """The registry: the vendored kit is imported here, never at module level."""
    aio._kit()  # puts `_vendor/` on sys.path
    from agentkit.registry import Registry, Tool

    tools = Registry()
    for tool in (
        Tool("list_profiles", "GET", "/v1/profiles", "read", Profiles, ProfileList),
        Tool("get_profile", "GET", "/v1/profiles/{name}", "read", OneProfile, ProfileOut),
        Tool("set_weights", "PATCH", "/v1/profiles/{name}/weights", "write", OneProfile, AnyJSON),
        Tool("set_ship", "PUT", "/v1/profiles/{name}/settings", "write", OneProfile, AnyJSON),
        Tool("evaluate", "POST", "/v1/profiles/{name}/evaluate", "read", OneProfile, AnyJSON),
        Tool("recommend", "GET", "/v1/recommend", "read", Recommend, RecommendationOut),
        Tool("get_ranking", "GET", "/v1/rankings/{profile}", "read", ProfileArg, RankingOut),
        Tool("explain", "GET", "/v1/rankings/{profile}/explain", "read", ProfileArg, AnyJSON),
        Tool("report_outcome", "POST", "/v1/telemetry/batch", "telemetry", Outcomes, Counts),
        Tool("apply", "POST", "/v1/apply", "run", ApplyArgs, AnyJSON, run=True),
        Tool("list_models", "GET", "/v1/models", "read", Models, ModelsOut),
        Tool("leaderboard", "GET", "/v1/leaderboard", "read", LeaderboardArgs, LeaderboardOut),
        Tool("status", "GET", "/v1/status", "read", Sections, StatusOut),
        Tool("list_runs", "GET", "/v1/runs", "read", Runs, RunsOut),
        Tool("export_config", "GET", "/v1/config", "read", Sections, ConfigOut),
    ):
        tools.add(tool)
    return tools


def registry() -> Any:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = build()
    return _REGISTRY


def mount(app: Any) -> None:
    """`POST /v1/mcp` on `app`, after `errors.install` (which `aio.mount` did)."""
    aio._kit()
    from agentkit import mcp

    mcp.mount(app, registry(), aio.auth_config(app).dependency)
