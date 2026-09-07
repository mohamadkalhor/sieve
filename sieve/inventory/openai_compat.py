"""Any OpenAI-compatible base URL: `GET {base_url}/v1/models`.

One connector covers most of what a person actually routes through -- a
gateway, a router, a local server. Some of them publish capability fields
alongside the id; where they do, they are believed over a catalogue, because
they describe this deployment rather than the model in general.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sieve.contracts import Capability, HttpClient, InventoryConfig, Reachable
from sieve.sources.base import as_int, utcnow

CAPABILITY_KEYS = {
    "tools": ("tools", "supports_tools", "function_calling", "supports_function_calling"),
    "reasoning": ("reasoning", "supports_reasoning", "thinking"),
    "structured_output": ("structured_output", "structured_outputs", "json_schema"),
}

CONTEXT_KEYS = ("context_window", "context_length", "max_context_tokens", "max_input_tokens")
OUTPUT_KEYS = ("max_output_tokens", "max_completion_tokens", "max_output")


def _flag(entry: dict[str, Any], keys: tuple[str, ...]) -> bool | None:
    for key in keys:
        value = entry.get(key)
        if isinstance(value, bool):
            return value
    return None


def _first_int(entry: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = as_int(entry.get(key))
        if value is not None:
            return value
    return None


def capability_of(entry: dict[str, Any]) -> Capability:
    """Only what the gateway actually said. Silence stays None, never False."""
    return Capability(
        tools=_flag(entry, CAPABILITY_KEYS["tools"]),
        reasoning=_flag(entry, CAPABILITY_KEYS["reasoning"]),
        structured_output=_flag(entry, CAPABILITY_KEYS["structured_output"]),
        context_window=_first_int(entry, CONTEXT_KEYS),
        max_output=_first_int(entry, OUTPUT_KEYS),
        input_modalities=[str(m) for m in (entry.get("input_modalities") or [])],
        output_modalities=[str(m) for m in (entry.get("output_modalities") or [])],
    )


class OpenAICompatInventory:
    name = "openai_compat"

    def list(self, cfg: InventoryConfig, http: HttpClient) -> list[Reachable]:
        base = (cfg.base_url or "").rstrip("/")
        if not base:
            raise ValueError(f"inventory {cfg.name!r}: base_url is required")
        url = base if base.endswith("/models") else f"{base}/v1/models"

        token = cfg.token()
        headers = {"authorization": f"Bearer {token}"} if token else {}
        response = http.get(url, headers=headers)
        if response.status != 200:
            raise RuntimeError(f"inventory {cfg.name!r}: HTTP {response.status} from {url}")

        body = response.body
        raw = body.get("data") if isinstance(body, dict) else body
        entries: list[Any] = raw if isinstance(raw, list) else []

        seen_at: datetime = utcnow()
        out: list[Reachable] = []
        for entry in entries:
            if isinstance(entry, str):
                out.append(Reachable(inventory=cfg.name, local_id=entry, seen_at=seen_at))
                continue
            if not isinstance(entry, dict):
                continue
            local_id = str(entry.get("id") or entry.get("model") or "").strip()
            if not local_id:
                continue
            out.append(
                Reachable(
                    inventory=cfg.name,
                    local_id=local_id,
                    capability=capability_of(entry),
                    seen_at=seen_at,
                )
            )
        return out
