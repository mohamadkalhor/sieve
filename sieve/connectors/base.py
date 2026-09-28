"""What every connector kind shares.

A kind implements one method -- `_entries()`, the raw catalogue the router
serves -- and, if it can be written to, `put_combo()`. Everything else is here:
`list_models()` is those entries as ids, `reachable()` is them as store rows
with whatever capability the router published, and `test()` is `list_models()`
with the exception turned into a sentence.

`test()` never raises. A router that is down, misconfigured or refusing the
token is an ordinary answer to "is this working" -- rendering it as a 500 tells
the person nothing and loses the reason.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from sieve.contracts import ComboResult, Connector, ConnectorTest, Reachable
from sieve.inventory.openai_compat import capability_of
from sieve.pinned import AddressRefused, AllowedHosts, HostNotAllowed, pinned_client
from sieve.secrets import Secrets
from sieve.sources.base import utcnow

#: Long enough for a gateway that is thinking, short enough that an hourly run
#: is not held open by one that is gone.
DEFAULT_TIMEOUT = 10.0


class ConnectorError(RuntimeError):
    """A connector that could not be read or could not be written to.

    Its message is shown to a person and stored in `last_error`, so it names the
    secret *id* rather than the token, and the URL rather than a code.
    """


class Adapter:
    """One kind of router, reached over HTTP."""

    #: matches the `kind` column, and the key in `registry.KINDS`
    kind: str = ""
    #: whether this kind can be given combos at all. A connector asking to write
    #: through a kind that cannot is refused on the way in, rather than silently
    #: shipping nothing every hour.
    writes: bool = False

    #: whether this kind has a second credential beside its catalogue -- an
    #: admin API with its own token. A kind that says no refuses `admin_secret`.
    admin_api: bool = False

    def __init__(
        self,
        connector: Connector,
        *,
        timeout: float | None = None,
        secrets: Secrets | None = None,
        hosts: AllowedHosts | None = None,
    ) -> None:
        self.connector = connector
        #: the server's `[secrets.*]` table. An id means nothing without it, so
        #: an adapter built without one has no credential at all rather than a
        #: fallback that reads an environment variable somebody named.
        self.secrets = secrets if secrets is not None else Secrets()
        #: the server's `[connectors.hosts]`: the `host:port` this kind may be
        #: pointed at. Built without one, the list is empty -- a public host is
        #: still reachable, a loopback or private one is not, and that is the
        #: way round that fails safe for a caller who forgot to pass it.
        self.hosts = hosts if hosts is not None else AllowedHosts()
        raw = connector.options.get("timeout") if timeout is None else timeout
        self.timeout = float(raw if raw is not None else DEFAULT_TIMEOUT)

    # -- where it is ------------------------------------------------------ #

    @property
    def name(self) -> str:
        return self.connector.name

    @property
    def allowed(self) -> list[str]:
        """The `host:port` entries `[connectors.hosts]` allows *this* kind."""
        return self.hosts.allowed(self.kind)

    def client(self, url: str) -> httpx.Client:
        """A client for `url`, pinned to the address this call just checked.

        The name is resolved here rather than by the socket layer, so the
        address that was judged is the address that is dialled. Both refusals
        arrive as a `ConnectorError`: the caller is a person reading a screen,
        and `test()` renders it as the sentence it is.
        """
        try:
            return pinned_client(url, self.allowed, self.timeout)
        except (HostNotAllowed, AddressRefused) as exc:
            raise ConnectorError(f"connector {self.name!r}: {exc}") from exc

    @property
    def base(self) -> str:
        base = (self.connector.base_url or "").strip().rstrip("/")
        if not base:
            raise ConnectorError(f"connector {self.name!r}: base_url is required")
        return base

    def env(self, variable: str | None) -> str | None:
        """The value of a named environment variable, or None. The only way in.

        Nothing reads a token from the database, from `sieve.toml` or from an
        API body: `sieve.toml` names the variable for a secret id, the connector
        carries only the id, and the operator sets the value.
        """
        return os.environ.get(variable) if variable else None

    def secret_env(self, secret_id: str | None) -> str | None:
        """The environment variable a secret id names for *this* kind.

        An id the config does not have, or one bound to other kinds, is not a
        quiet `None`: it is the sentence somebody needs when the router answers
        401, so it is raised and rendered as `ok: false` by `test()`.
        """
        problem = self.secrets.problem(secret_id, self.kind)
        if problem:
            raise ConnectorError(f"connector {self.name!r}: {problem}")
        return self.secrets.env(secret_id, self.kind)

    # -- what a kind implements ------------------------------------------- #

    def _entries(self) -> list[Any]:
        """The router's catalogue, as it published it."""
        raise NotImplementedError

    def put_combo(self, name: str, ordered_ids: list[str]) -> ComboResult:
        """Seat one chain. A kind that only reads says so rather than pretending."""
        return ComboResult(
            ok=False,
            error=f"connector {self.name!r}: kind {self.kind!r} can only be read from",
        )

    # -- what every kind gets --------------------------------------------- #

    def list_models(self) -> list[str]:
        """Every id this router serves, in the order it served them."""
        out: list[str] = []
        seen: set[str] = set()
        for entry in self._entries():
            local_id = entry_id(entry)
            if local_id and local_id not in seen:
                seen.add(local_id)
                out.append(local_id)
        return out

    def reachable(self) -> list[Reachable]:
        """The same ids as store rows, carrying any capability the router said.

        A gateway that publishes `context_length` or `supports_tools` is
        describing *this deployment*, which is better evidence than a catalogue
        entry about the model in general, so it is kept.
        """
        seen_at = utcnow()
        out: list[Reachable] = []
        found: set[str] = set()
        for entry in self._entries():
            local_id = entry_id(entry)
            if not local_id or local_id in found:
                continue
            found.add(local_id)
            row = Reachable(inventory=self.name, local_id=local_id, seen_at=seen_at)
            if isinstance(entry, dict):
                row = row.model_copy(update={"capability": capability_of(entry)})
            out.append(row)
        return out

    def test(self) -> ConnectorTest:
        """Reach the router now and say what happened, in one sentence."""
        try:
            models = self.list_models()
        except ConnectorError as exc:
            return ConnectorTest(ok=False, error=str(exc))
        except Exception as exc:  # a kind's own client, misbehaving
            return ConnectorTest(ok=False, error=f"connector {self.name!r}: {exc}")
        return ConnectorTest(ok=True, models_count=len(models))

    # -- http ------------------------------------------------------------- #

    def get_json(self, url: str, headers: dict[str, str]) -> Any:
        """One GET, with every failure rendered as a `ConnectorError`.

        Through `client()`, so the name is resolved and checked once and the
        socket goes to that address rather than to whatever the name says the
        second time it is asked.
        """
        try:
            with self.client(url) as client:
                response = client.get(url, headers=headers)
        except httpx.HTTPError as exc:
            raise ConnectorError(
                f"connector {self.name!r}: {type(exc).__name__} reaching {url}: {exc}"
            ) from exc
        if response.status_code in (401, 403):
            named = self.connector.secret or "no secret is set"
            raise ConnectorError(
                f"connector {self.name!r}: {url} refused the credentials (secret "
                f"{named!r}); HTTP {response.status_code}"
            )
        if response.status_code != 200:
            raise ConnectorError(f"connector {self.name!r}: HTTP {response.status_code} from {url}")
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorError(f"connector {self.name!r}: {url} did not answer JSON") from exc


def entry_id(entry: Any) -> str:
    """The model id of one catalogue entry, whatever shape it came in."""
    if isinstance(entry, str):
        return entry.strip()
    if isinstance(entry, dict):
        return str(entry.get("id") or entry.get("model") or "").strip()
    return ""
