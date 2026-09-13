"""9router: an OpenAI-compatible catalogue to read, an admin API to write.

Reading is the same `/v1/models` every other gateway serves, so that half is
inherited. Writing is not: a chain becomes a **combo**, a named list of local
ids in fallback order, and the admin API addresses one by id rather than by
name -- so the list is read first, a name already held is a `PUT` and a new one
a `POST`. Posting a name that exists collides with a UNIQUE constraint instead
of updating it.

**Two tokens, and they are not interchangeable.** The catalogue takes an
ordinary bearer key in `token_env`; the admin API takes the CLI token 9router
derives from its own machine id, in an `x-9r-cli-token` header, named by
`admin_token_env`. The vocabulary of combo kinds, the managed `sieve-` prefix
and the header itself are the target's, imported rather than restated, so the
connector and the old `[targets.gateway]` path can never drift apart.
"""

from __future__ import annotations

from typing import Any

import httpx

from sieve.connectors.base import ConnectorError
from sieve.connectors.openai_compat import OpenAICompatConnector
from sieve.contracts import ComboResult
from sieve.targets.ninerouter import CLI_TOKEN_HEADER, COMBOS_PATH, SERVICE_KIND

#: Which option names the variable holding the admin token. Falls back to
#: `token_env` for a router where one token does both.
ADMIN_TOKEN_ENV = "admin_token_env"


class NineRouterConnector(OpenAICompatConnector):
    kind = "ninerouter"
    writes = True

    # -- the admin API ---------------------------------------------------- #

    @property
    def admin_token_env(self) -> str | None:
        named = self.connector.options.get(ADMIN_TOKEN_ENV) or self.connector.token_env
        return str(named) if named else None

    def admin_headers(self) -> dict[str, str]:
        token = self.env(self.admin_token_env)
        if not token:
            named = self.admin_token_env or ADMIN_TOKEN_ENV
            raise ConnectorError(
                f"connector {self.name!r}: the 9router admin API answers Unauthorized "
                f"without a token, and {named} is not set in this process. It wants the "
                f"CLI token 9router derives from its own machine id, sent as "
                f"{CLI_TOKEN_HEADER}, not an API key in an authorization header."
            )
        return {CLI_TOKEN_HEADER: token, "content-type": "application/json"}

    def combos(self) -> list[dict[str, Any]]:
        """Every combo the router holds, with its id and its models.

        `/v1/models` is the wrong endpoint for this: it lists a combo by name
        and never carries the models array, so reading it would report an empty
        chain for a gateway that is routing fine.
        """
        body = self.get_json(f"{self.base}{COMBOS_PATH}", self.admin_headers())
        combos = body.get("combos") if isinstance(body, dict) else None
        if not isinstance(combos, list):
            raise ConnectorError(
                f"connector {self.name!r}: {COMBOS_PATH} did not answer with a `combos` list"
            )
        return [entry for entry in combos if isinstance(entry, dict)]

    # -- writing ---------------------------------------------------------- #

    def put_combo(self, name: str, ordered_ids: list[str]) -> ComboResult:
        """Seat one chain under `name`, creating the combo or updating it.

        Returns rather than raises: one unseatable profile should not lose the
        other four, and the reason belongs beside the profile it is about.
        """
        if not ordered_ids:
            return ComboResult(ok=False, error="a combo with no models would route nowhere")
        try:
            headers = self.admin_headers()
            held = {str(c.get("name") or ""): str(c.get("id") or "") for c in self.combos()}
            payload = {"name": name, "kind": SERVICE_KIND, "models": ordered_ids}
            combo_id = held.get(name)
            url = f"{self.base}{COMBOS_PATH}"
            if combo_id:
                response = httpx.put(
                    f"{url}/{combo_id}", json=payload, headers=headers, timeout=self.timeout
                )
            else:
                response = httpx.post(url, json=payload, headers=headers, timeout=self.timeout)
        except ConnectorError as exc:
            return ComboResult(ok=False, error=str(exc))
        except httpx.HTTPError as exc:
            return ComboResult(
                ok=False, error=f"{type(exc).__name__} writing {name} to {self.name}: {exc}"
            )
        if response.status_code in (401, 403):
            return ComboResult(
                ok=False,
                error=(
                    f"9router refused {self.admin_token_env} writing {name}; "
                    f"it wants its CLI token in {CLI_TOKEN_HEADER}"
                ),
            )
        if not 200 <= response.status_code < 300:
            return ComboResult(
                ok=False, error=f"HTTP {response.status_code} writing {name} to {self.name}"
            )
        return ComboResult(ok=True, created=combo_id is None)
