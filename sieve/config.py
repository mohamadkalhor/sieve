"""`sieve.toml` (CONTRACTS section 5), resolved into typed config.

Secrets are never read from this file: a source names the environment
variable that holds its key, and a connector names a `[secrets.<id>]` entry
that names that variable -- ids and names, never a value.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from sieve.contracts import InventoryConfig, Modality, SourceConfig, TargetConfig
from sieve.pinned import AllowedHosts, normalize_entry
from sieve.secrets import ID, SecretConfig, Secrets

DEFAULT_CONFIG = "sieve.toml"


class StoreConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = "data/sieve.db"


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    host: str = "127.0.0.1"
    port: int = 8110
    cors: list[str] = Field(default_factory=list)
    read_token: bool = False
    web: str = "web/build"


class Paths(BaseModel):
    """Where the file-backed vocabulary lives. Defaults match PLAN section 9."""

    model_config = ConfigDict(extra="forbid")
    axes: str = "data/axes"
    profiles: str = "profiles"
    aliases: str = "data/aliases.yaml"
    out: str = "out"


class ScheduleConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pull: str = "hourly"
    evaluate: str = "hourly"


class ConnectorConfig(BaseModel):
    """`[connectors]`: where a connector kind may be pointed.

    A connector's `base_url` is a host somebody typed, so it is data, not
    config. This is the config side of the check: `hosts` names, per kind, the
    `host:port` that kind may call, and everything else is refused on the way in
    (see `sieve/pinned.py` for the second half of the rule, the address that is
    actually dialled).
    """

    model_config = ConfigDict(extra="forbid")
    #: kind -> entries written as `host:port`, e.g. ["127.0.0.1:20128"]
    hosts: dict[str, list[str]] = Field(default_factory=dict)


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    root: Path = Path()
    store: StoreConfig = Field(default_factory=StoreConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    paths: Paths = Field(default_factory=Paths)
    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    sources: dict[str, SourceConfig] = Field(default_factory=dict)
    inventories: dict[str, InventoryConfig] = Field(default_factory=dict)
    targets: dict[str, TargetConfig] = Field(default_factory=dict)
    #: `[secrets.<id>]`: the server-side registry of secret ids. A connector
    #: carries one of these ids, and this file -- edited on the box, never over
    #: the wire -- is the only place that says which variable it names and which
    #: connector kinds may use it.
    secrets: dict[str, SecretConfig] = Field(default_factory=dict)
    #: `[connectors.hosts]`: the `host:port` each connector kind may be pointed
    #: at. Empty is a real answer: nothing is allowed until the box says so.
    connectors: ConnectorConfig = Field(default_factory=ConnectorConfig)

    @property
    def secret_registry(self) -> Secrets:
        """The `[secrets.*]` table, as the connectors ask for it."""
        return Secrets(self.secrets)

    @property
    def allowed_hosts(self) -> AllowedHosts:
        """The `[connectors.hosts]` table, as the routes and connectors ask for it."""
        return AllowedHosts(self.connectors.hosts)

    # -- resolved paths ------------------------------------------------- #

    def path(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else self.root / p

    @property
    def db_path(self) -> Path:
        return self.path(self.store.path)

    @property
    def axes_dir(self) -> Path:
        return self.path(self.paths.axes)

    @property
    def profiles_dir(self) -> Path:
        return self.path(self.paths.profiles)

    @property
    def aliases_file(self) -> Path:
        return self.path(self.paths.aliases)

    @property
    def out_dir(self) -> Path:
        return self.path(self.paths.out)

    def enabled_sources(self) -> list[SourceConfig]:
        return [s for s in self.sources.values() if s.enabled]


def _section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"[{name}] must be a table in sieve.toml")
    return value


def load_config(path: str | Path | None = None, *, root: Path | None = None) -> Config:
    """Read `sieve.toml`; every section is optional and falls back to defaults."""
    file = Path(path) if path else Path(DEFAULT_CONFIG)
    # A missing sieve.toml is normal and falls back to defaults. A missing file
    # somebody *named* is not: it used to fall back silently, so a typo in
    # `--config` ran the whole command against the default configuration and
    # looked like the source simply published nothing.
    if path is not None and not file.exists():
        raise ValueError(f"no such config file: {file}")
    base = root or (file.parent.resolve() if file.exists() else Path.cwd())
    raw: dict[str, Any] = {}
    if file.exists():
        raw = tomllib.loads(file.read_text(encoding="utf-8"))

    sources: dict[str, SourceConfig] = {}
    for name, body in _section(raw, "sources").items():
        known = {"enabled", "key_env", "modalities", "dir"}
        sources[name] = SourceConfig(
            name=name,
            enabled=bool(body.get("enabled", True)),
            key_env=body.get("key_env"),
            modalities=[m for m in body.get("modalities", [])],
            dir=body.get("dir"),
            options={k: v for k, v in body.items() if k not in known},
        )

    inventories: dict[str, InventoryConfig] = {}
    for name, body in _section(raw, "inventories").items():
        known = {"kind", "base_url", "token_env", "models"}
        inventories[name] = InventoryConfig(
            name=name,
            kind=str(body.get("kind", "openai_compat")),
            base_url=body.get("base_url"),
            token_env=body.get("token_env"),
            models=list(body.get("models", [])),
            options={k: v for k, v in body.items() if k not in known},
        )

    targets: dict[str, TargetConfig] = {}
    for name, body in _section(raw, "targets").items():
        known = {"kind", "dir", "url"}
        targets[name] = TargetConfig(
            name=name,
            kind=str(body.get("kind", "file")),
            dir=body.get("dir"),
            url=body.get("url"),
            options={k: v for k, v in body.items() if k not in known},
        )

    secrets: dict[str, SecretConfig] = {}
    for name, body in _section(raw, "secrets").items():
        if not ID.match(name):
            raise ValueError(
                f"[secrets.{name}] is not a usable secret id: letters, digits, "
                "hyphen and underscore, up to 64 characters"
            )
        try:
            secrets[name] = SecretConfig(**body)
        except TypeError as exc:
            raise ValueError(f"[secrets.{name}]: {exc}") from exc
        except ValidationError as exc:
            raise ValueError(f"[secrets.{name}]: {exc}") from exc

    hosts: dict[str, list[str]] = {}
    for kind, entries in _section(_section(raw, "connectors"), "hosts").items():
        if not isinstance(entries, list):
            raise ValueError(
                f"[connectors.hosts] {kind} must be a list of host:port entries"
            )
        try:
            hosts[kind] = [normalize_entry(entry) for entry in entries]
        except ValueError as exc:
            raise ValueError(f"[connectors.hosts] {kind}: {exc}") from exc

    return Config(
        root=base,
        store=StoreConfig(**_section(raw, "store")),
        server=ServerConfig(**_section(raw, "server")),
        paths=Paths(**_section(raw, "paths")),
        schedule=ScheduleConfig(**_section(raw, "schedule")),
        sources=sources,
        inventories=inventories,
        targets=targets,
        secrets=secrets,
        connectors=ConnectorConfig(hosts=hosts),
    )


def default_config(root: Path | None = None) -> Config:
    """The config used when no `sieve.toml` is present: everything free, no gateway."""
    base = root or Path.cwd()
    llm_media: list[Modality] = [
        "text-to-image",
        "image-editing",
        "text-to-video",
        "image-to-video",
        "text-to-speech",
    ]
    return Config(
        root=base,
        sources={
            "aa_llm": SourceConfig(
                name="aa_llm", key_env="ARTIFICIAL_ANALYSIS_API_KEY", modalities=["llm"]
            ),
            "aa_media": SourceConfig(
                name="aa_media",
                key_env="ARTIFICIAL_ANALYSIS_API_KEY",
                modalities=llm_media,
            ),
            "openrouter": SourceConfig(name="openrouter", modalities=["llm"]),
            "manual": SourceConfig(name="manual", dir="data/observations"),
        },
        targets={"out": TargetConfig(name="out", kind="file", dir="out")},
    )
