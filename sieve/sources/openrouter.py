"""OpenRouter — the best key-free capability and price source.

`GET https://openrouter.ai/api/v1/models`, no key. It gives prices, context
length, input and output modalities and `supported_parameters`, which is where
`require: {tools: true, reasoning: true, context_min: ...}` gets its answer.

OpenRouter also republishes a subset of Artificial Analysis scores. Those are
deliberately **not** stored: AA is the source of AA's numbers, and storing them
twice would double their weight in an axis.
"""

from __future__ import annotations

from typing import Any, ClassVar

from sieve.catalog.registry import canonical_id
from sieve.contracts import (
    Capability,
    HttpClient,
    Modality,
    ModelRef,
    Price,
    PullResult,
    SourceConfig,
)
from sieve.sources.base import as_float, as_int, utcnow

URL = "https://openrouter.ai/api/v1/models"

MODALITY: Modality = "llm"

#: `supported_parameters` entries that mean each capability.
TOOL_PARAMS = frozenset({"tools", "tool_choice", "functions"})
REASONING_PARAMS = frozenset({"reasoning", "include_reasoning", "thinking"})
STRUCTURED_PARAMS = frozenset({"structured_outputs", "response_format", "json_schema"})

#: AA numbers OpenRouter republishes. Stored by aa_llm, never here.
REPUBLISHED = ("artificial_analysis", "aa_")


def _per_million(raw: Any) -> float | None:
    """OpenRouter prices are USD per token; Sieve stores USD per 1M tokens."""
    value = as_float(raw)
    if value is None:
        return None
    return value * 1_000_000


def capability_of(entry: dict[str, Any]) -> Capability:
    architecture = entry.get("architecture") or {}
    top = entry.get("top_provider") or {}
    supported = {str(p).lower() for p in (entry.get("supported_parameters") or [])}
    return Capability(
        tools=bool(supported & TOOL_PARAMS) or None,
        reasoning=bool(supported & REASONING_PARAMS) or None,
        structured_output=bool(supported & STRUCTURED_PARAMS) or None,
        context_window=as_int(entry.get("context_length")),
        max_output=as_int(top.get("max_completion_tokens")),
        input_modalities=[str(m) for m in (architecture.get("input_modalities") or [])],
        output_modalities=[str(m) for m in (architecture.get("output_modalities") or [])],
    )


def _model_of(entry: dict[str, Any], model_id: str) -> ModelRef:
    aliases = {str(entry.get("canonical_slug") or ""), str(entry.get("id") or "")}
    aliases.discard("")
    aliases.discard(model_id)
    return ModelRef(
        id=model_id,
        modality=MODALITY,
        name=str(entry.get("name") or model_id),
        creator=model_id.split("/", 1)[0],
        aliases=sorted(aliases),
    )


def _price_of(entry: dict[str, Any], model_id: str, at: Any) -> Price | None:
    pricing = entry.get("pricing") or {}
    values = {
        "input": _per_million(pricing.get("prompt")),
        "output": _per_million(pricing.get("completion")),
        "cached_input": _per_million(pricing.get("input_cache_read")),
    }
    if all(v is None for v in values.values()):
        return None
    return Price(
        model_id=model_id,
        source="openrouter",
        unit="usd_per_1m_tokens",
        input=values["input"],
        output=values["output"],
        cached_input=values["cached_input"],
        source_url=URL,
        observed_at=at,
    )


class OpenRouterSource:
    name = "openrouter"
    modality: ClassVar[list[Modality]] = [MODALITY]
    needs_key = False

    def pull(self, cfg: SourceConfig, http: HttpClient) -> PullResult:
        at = utcnow()
        result = PullResult(source=self.name)

        response = http.get(URL)
        if response.status != 200:
            result.warnings.append(f"openrouter: HTTP {response.status} from {URL}")
            result.ok = False
            result.rate_limit = http.rate_limit()
            return result

        body = response.body if isinstance(response.body, dict) else {}
        raw = body.get("data")
        entries: list[Any] = raw if isinstance(raw, list) else []
        if not entries:
            result.warnings.append("openrouter: the payload carried no `data` list")

        republished: set[str] = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            raw_id = str(entry.get("id") or "").strip().lower()
            if not raw_id:
                continue
            model_id = raw_id if "/" in raw_id else canonical_id("openrouter", raw_id)

            result.models.append(_model_of(entry, model_id))

            capability = capability_of(entry)
            if capability.model_dump(exclude_none=True, exclude_defaults=True):
                result.capabilities[model_id] = capability

            price = _price_of(entry, model_id, at)
            if price is not None:
                result.prices.append(price)

            republished |= {
                key for key in entry if isinstance(key, str) and key.lower().startswith(REPUBLISHED)
            }

        if republished:
            result.warnings.append(
                "openrouter republishes "
                + ", ".join(sorted(republished))
                + "; not stored -- aa_llm is the source of those numbers"
            )

        result.rate_limit = http.rate_limit()
        return result
