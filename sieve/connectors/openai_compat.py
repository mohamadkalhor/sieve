"""Anything that speaks `GET {base_url}/v1/models`.

A gateway, a router, a local server, somebody else's proxy. It is the kind most
routers already are, and it only reads: an OpenAI-compatible catalogue has no
documented way to be told what to route, so `write` on this kind is refused
rather than accepted and quietly ignored.
"""

from __future__ import annotations

from typing import Any

from sieve.connectors.base import Adapter, ConnectorError


class OpenAICompatConnector(Adapter):
    kind = "openai_compat"
    writes = False

    def catalogue_url(self) -> str:
        """`/v1/models` under the base, unless the base already names it."""
        base = self.base
        return base if base.endswith("/models") else f"{base}/v1/models"

    def headers(self) -> dict[str, str]:
        token = self.env(self.connector.token_env)
        return {"authorization": f"Bearer {token}"} if token else {}

    def _entries(self) -> list[Any]:
        url = self.catalogue_url()
        body = self.get_json(url, self.headers())
        raw = body.get("data") if isinstance(body, dict) else body
        if not isinstance(raw, list):
            raise ConnectorError(
                f"connector {self.name!r}: {url} did not answer with a list of models"
            )
        return list(raw)
