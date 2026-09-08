"""`sieve.toml` (CONTRACTS section 5), resolved into typed config.

Secrets are never read from this file: a source names the environment
variable that holds its key, and nothing else.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from sieve.contracts import InventoryConfig, Modality, SourceConfig, TargetConfig

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

    return Config(
        root=base,
        store=StoreConfig(**_section(raw, "store")),
        server=ServerConfig(**_section(raw, "server")),
        paths=Paths(**_section(raw, "paths")),
        schedule=ScheduleConfig(**_section(raw, "schedule")),
        sources=sources,
        inventories=inventories,
        targets=targets,
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
