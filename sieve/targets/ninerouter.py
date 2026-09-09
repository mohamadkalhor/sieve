"""The `ninerouter` target: a 9router combo per profile.

9router serves an OpenAI-compatible catalogue in which each **combo** appears as
a model with `owned_by: "combo"`, and keeps its state in a SQLite file whose
`combos` table is `(id, name, kind, models, createdAt, updatedAt)` -- `models`
being a JSON array of local model ids **in fallback order**. That array is
exactly what a Sieve chain is, which is why this target can read the live chain
back and give `sieve diff` something real to compare against.

**`kind` is a service kind, not a description.** 9router filters its catalogue
by `kind`, defaulting a null one to `"llm"`; a combo whose kind is outside
`SERVICE_KINDS` is written, stored, and then served to nobody. This target once
wrote `"fallback"` there -- true of what a chain *is*, and not a member of that
vocabulary, so every combo it wrote was silently dropped from `/v1/models`. An
`UPDATE` therefore rewrites `kind` as well as `models`, so a row left behind by
that mistake is repaired the next time its chain is applied.

**Two ways in, and a refusal.** The admin API answers `{"error":"Unauthorized"}`
without a token, so: the HTTP API when a token is configured, the SQLite file
when a path is given, and a clear refusal naming both when neither is. Guessing
would mean writing to a database nobody asked this to touch.

**Never against a live gateway from a build.** The file path is backed up before
it is written, and `write()` refuses a path it was not given explicitly.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sieve.contracts import Chain, TargetConfig, TargetResult

#: The service kinds 9router recognises. It is a closed vocabulary: a combo
#: carrying anything else is filtered out of every catalogue listing, which is
#: indistinguishable from never having been written.
SERVICE_KINDS = frozenset(
    {
        "llm",
        "embedding",
        "image",
        "imageToText",
        "video",
        "tts",
        "stt",
        "webSearch",
        "webFetch",
    }
)

#: What Sieve writes. Every profile this target serves picks a chat model, and
#: `"llm"` is also what 9router assumes for a combo with no kind at all, so a
#: hand-made combo and a Sieve-managed one are listed on the same terms.
SERVICE_KIND = "llm"

#: The admin API. Not `authorization: Bearer` -- 9router authenticates its own
#: CLI with a machine-local shared secret under this header instead.
CLI_TOKEN_HEADER = "x-9r-cli-token"

#: Where combos live on the admin API. `/v1/models` lists a combo by name only
#: and never carries its `models` array, so it cannot answer `current()`.
COMBOS_PATH = "/api/combos"

#: The prefix a Sieve-managed combo carries, so a hand-made combo is never
#: touched: this target only edits what it created.
MANAGED_PREFIX = "sieve-"


class NineRouterError(RuntimeError):
    """Misconfiguration or a refusal. Never a silent no-op."""


def combo_name(profile: str) -> str:
    """The combo a profile owns. Stable, prefixed, and its own namespace."""
    return f"{MANAGED_PREFIX}{profile}"


def chain_models(chain: Chain) -> list[str]:
    """The local ids for one chain, in fallback order.

    9router routes to *its own* model ids, not to canonical ones, so a chain
    whose models it does not serve becomes a shorter combo rather than a broken
    one. A chain with no reachable local id at all is refused by the caller.
    """
    out: list[str] = []
    for model_id in (chain.primary, *chain.fallbacks):
        for local in chain.local.get(model_id, []):
            if local not in out:
                out.append(local)
    return out


class NineRouterTarget:
    name = "ninerouter"

    def plan(self, cfg: TargetConfig, chains: list[Chain]) -> dict[str, list[str]]:
        """Local ids, because that is what a combo holds and what `current()` reads.

        Without this the diff compares canonical ids against 9router's own and
        reports a change on every run, which teaches everyone to ignore it.
        """
        return {chain.profile: models for chain in chains if (models := chain_models(chain))}

    # -- how to reach it ------------------------------------------------- #

    def _mode(self, cfg: TargetConfig) -> str:
        token_env = str(cfg.options.get("token_env") or "")
        import os

        if token_env and os.environ.get(token_env):
            return "http"
        if cfg.options.get("sqlite_path"):
            return "sqlite"
        raise NineRouterError(
            f"target {cfg.name!r}: no way in. Set `token_env` to the environment "
            "variable holding a 9router admin token (the admin API answers "
            '{"error":"Unauthorized"} without one), or `sqlite_path` to its '
            "database file. Both are missing, and this will not guess."
        )

    # -- reading the live chain ------------------------------------------ #

    def current(self, cfg: TargetConfig) -> dict[str, list[str]]:
        """`{profile: [local ids]}` as 9router holds it *now*.

        This is the whole reason the target exists in this shape. A diff against
        the last `apply` decision only says what Sieve believes it wrote; a diff
        against this says what is actually routing traffic, which is the thing
        that drifts.
        """
        mode = self._mode(cfg)
        rows = self._read_http(cfg) if mode == "http" else self._read_sqlite(cfg)
        out: dict[str, list[str]] = {}
        for name, models in rows.items():
            if name.startswith(MANAGED_PREFIX):
                out[name[len(MANAGED_PREFIX) :]] = models
        return out

    def _http(self, cfg: TargetConfig) -> tuple[str, dict[str, str], float]:
        """Base URL, headers and timeout for the admin API, or a refusal."""
        import os

        base = str(cfg.url or "").rstrip("/")
        if not base:
            raise NineRouterError(f"target {cfg.name!r}: `url` is needed to use the admin API")
        token = os.environ[str(cfg.options["token_env"])]
        headers = {CLI_TOKEN_HEADER: token, "content-type": "application/json"}
        return base, headers, float(cfg.options.get("timeout", 10.0))

    def _combos_http(self, cfg: TargetConfig) -> list[dict[str, Any]]:
        """Every combo the admin API holds, with its id and its models.

        `/v1/models` is the wrong endpoint for this. It lists a combo as
        `{id, object, owned_by}` and never carries the models array, so reading
        it would report an empty chain for a gateway that is routing fine --
        an absence rendering as a fact.
        """
        import httpx

        base, headers, timeout = self._http(cfg)
        response = httpx.get(f"{base}{COMBOS_PATH}", headers=headers, timeout=timeout)
        if response.status_code == 401:
            raise NineRouterError(
                f"target {cfg.name!r}: 9router answered Unauthorized -- "
                f"{cfg.options['token_env']} is set but not accepted. It wants the CLI "
                f"token it derives from its own machine id, sent as {CLI_TOKEN_HEADER}, "
                f"not an API key in an authorization header."
            )
        if response.status_code != 200:
            raise NineRouterError(f"target {cfg.name!r}: HTTP {response.status_code} from {base}")

        body = response.json()
        combos = body.get("combos") if isinstance(body, dict) else None
        if not isinstance(combos, list):
            raise NineRouterError(
                f"target {cfg.name!r}: {COMBOS_PATH} did not answer with a `combos` list"
            )
        return [entry for entry in combos if isinstance(entry, dict)]

    def _read_http(self, cfg: TargetConfig) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for entry in self._combos_http(cfg):
            name = str(entry.get("name") or "")
            models = entry.get("models")
            if name and isinstance(models, list):
                out[name] = [str(m) for m in models]
        return out

    def _sqlite_path(self, cfg: TargetConfig) -> Path:
        path = Path(str(cfg.options["sqlite_path"]))
        if not path.is_file():
            raise NineRouterError(f"target {cfg.name!r}: no such 9router database: {path}")
        return path

    def _read_sqlite(self, cfg: TargetConfig) -> dict[str, list[str]]:
        path = self._sqlite_path(cfg)
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT name, models FROM combos").fetchall()
        except sqlite3.DatabaseError as exc:
            raise NineRouterError(
                f"target {cfg.name!r}: {path} is not a 9router database: {exc}"
            ) from exc
        finally:
            db.close()

        out: dict[str, list[str]] = {}
        for row in rows:
            try:
                models = json.loads(row["models"] or "[]")
            except ValueError:
                continue
            if isinstance(models, list):
                out[str(row["name"])] = [str(m) for m in models]
        return out

    # -- writing ---------------------------------------------------------- #

    def write(self, cfg: TargetConfig, chains: list[Chain], dry_run: bool) -> TargetResult:
        mode = self._mode(cfg)
        planned: dict[str, list[str]] = {}
        skipped: dict[str, str] = {}

        for chain in chains:
            models = chain_models(chain)
            if not models:
                skipped[chain.profile] = "no reachable local id in the chain"
                continue
            planned[combo_name(chain.profile)] = models

        detail: dict[str, Any] = {
            "mode": mode,
            "combos": sorted(planned),
            "skipped": skipped,
        }
        if dry_run:
            detail["written"] = False
            return TargetResult(
                target=cfg.name,
                written=[
                    f"{name}: {' -> '.join(models)}" for name, models in sorted(planned.items())
                ],
                dry_run=True,
                detail=detail,
            )

        if mode == "sqlite":
            backup = self._write_sqlite(cfg, planned)
            detail["backup"] = str(backup)
        else:
            self._write_http(cfg, planned)

        return TargetResult(
            target=cfg.name,
            written=[f"{name}: {' -> '.join(models)}" for name, models in sorted(planned.items())],
            dry_run=False,
            detail=detail,
        )

    def _write_sqlite(self, cfg: TargetConfig, planned: dict[str, list[str]]) -> Path:
        """Upsert each managed combo. Everything else in the table is left alone.

        The file is copied first. It belongs to another program, and a target
        that corrupts a gateway's database has done far more damage than one
        that failed to write.
        """
        path = self._sqlite_path(cfg)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        backup = path.with_suffix(path.suffix + f".sieve-{stamp}.bak")
        shutil.copy2(path, backup)

        now = datetime.now(UTC).isoformat()
        db = sqlite3.connect(path)
        try:
            db.row_factory = sqlite3.Row
            held = {
                str(r["name"]): str(r["id"])
                for r in db.execute("SELECT id, name FROM combos").fetchall()
            }
            with db:
                for name, models in sorted(planned.items()):
                    blob = json.dumps(models)
                    if name in held:
                        # `kind` is rewritten too, so a row still carrying the
                        # old `"fallback"` starts being served again.
                        db.execute(
                            "UPDATE combos SET kind=?, models=?, updatedAt=? WHERE id=?",
                            (SERVICE_KIND, blob, now, held[name]),
                        )
                    else:
                        db.execute(
                            "INSERT INTO combos (id, name, kind, models, createdAt, updatedAt)"
                            " VALUES (?,?,?,?,?,?)",
                            (uuid.uuid4().hex, name, SERVICE_KIND, blob, now, now),
                        )
        finally:
            db.close()
        return backup

    def _write_http(self, cfg: TargetConfig, planned: dict[str, list[str]]) -> None:
        """Upsert over the admin API, which addresses a combo by id, not by name.

        So the list is read first: a name already held is a `PUT` against its
        id, and a name that is new is a `POST`. Posting a name that already
        exists would collide with the table's `UNIQUE` constraint instead of
        updating it.
        """
        import httpx

        base, headers, timeout = self._http(cfg)
        held = {str(c.get("name") or ""): str(c.get("id") or "") for c in self._combos_http(cfg)}

        for name, models in sorted(planned.items()):
            payload = {"name": name, "kind": SERVICE_KIND, "models": models}
            combo_id = held.get(name)
            if combo_id:
                response = httpx.put(
                    f"{base}{COMBOS_PATH}/{combo_id}",
                    json=payload,
                    headers=headers,
                    timeout=timeout,
                )
            else:
                response = httpx.post(
                    f"{base}{COMBOS_PATH}", json=payload, headers=headers, timeout=timeout
                )
            if response.status_code == 401:
                raise NineRouterError(
                    f"target {cfg.name!r}: 9router answered Unauthorized writing {name}"
                )
            if not 200 <= response.status_code < 300:
                raise NineRouterError(
                    f"target {cfg.name!r}: HTTP {response.status_code} writing {name}"
                )
