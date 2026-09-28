"""The `[secrets.*]` table: a connector names an id, the file names the variable.

A connector used to carry `token_env`, the *name* of an environment variable, and
the API let anybody who could write a profile set it. That name is a pointer to a
secret this box holds: point it at somebody else's host and the token goes there.
A connector now carries a **secret id** instead -- an id that exists only in
`sieve.toml`, which is edited on the box and never over the wire, and that is
bound to the connector kinds it may be used with. A secret written for the
gateway cannot be spent on a kind that speaks to somewhere else.

Nothing here holds a value. `env` names the variable the operator sets; the value
is read from the process environment at the moment it is needed, and is never
stored, served or logged -- a database that leaks still leaks a list of names.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: A secret id, as boring as a connector name: it is written in a config file and
#: quoted in a refusal, so it stays readable and cannot smuggle anything.
ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.I)

#: The marker a policy refusal starts with, so the API's validation handler can
#: tell "you named an environment variable where an id belongs" from "the payload
#: does not match the contract", and answer with the sentence rather than the
#: generic one. Written by `refuse_environment_names`, read by `sieve/api/app.py`.
REFUSAL = "refused: "


class EnvironmentNameError(ValueError):
    """A body named an environment variable where a secret id belongs.

    Raised rather than returned so every door -- a connector body, an imported
    bundle, the seeder -- refuses a name the same way, with a sentence a person
    can act on, and so a new door that forgets to check is a bug rather than a
    hole.
    """


def env_key_paths(raw: Any, path: str = "") -> list[str]:
    """Every key in a document that names an environment variable, with its path.

    Everywhere, not just at the top: `token_env`, `admin_token_env` nested in an
    options table, or a key somebody invented ending in `_env`. A connector that
    carried a variable name would send this box's token wherever a writer of that
    name pointed it, so the answer is a refusal that quotes the key rather than a
    lesson in which fields happen to be checked.
    """
    named: list[str] = []
    if isinstance(raw, dict):
        for key, value in raw.items():
            where = f"{path}.{key}" if path else str(key)
            if isinstance(key, str) and key.lower().endswith("_env"):
                named.append(where)
            else:
                named.extend(env_key_paths(value, where))
    elif isinstance(raw, list):
        for index, value in enumerate(raw):
            named.extend(env_key_paths(value, f"{path}[{index}]"))
    return named


def refuse_environment_names(raw: Any) -> Any:
    """The one sentence every door gives a document that names a variable."""
    named = env_key_paths(raw)
    if named:
        raise EnvironmentNameError(
            REFUSAL + f"{', '.join(named)} names an environment variable, and a connector "
            "carries the id of an entry in [secrets] in sieve.toml instead -- the "
            "config file is edited on the box and never over the wire"
        )
    return raw


class SecretConfig(BaseModel):
    """One `[secrets.<id>]` entry: the variable, and who may name it.

    `kinds` is required to name the connector kinds this secret may be used
    with. A secret that names none is usable by none: the binding is the whole
    point of the id, so an empty list is a refusal rather than a wildcard.
    """

    model_config = ConfigDict(extra="forbid")

    #: the environment variable holding the value. A name, never the value.
    env: str
    #: the connector kinds this id may be used with, e.g. `["ninerouter"]`
    kinds: list[str] = Field(default_factory=list)


class Secrets:
    """The table as the rest of the program asks for it: id in, variable out."""

    def __init__(self, entries: dict[str, SecretConfig] | None = None) -> None:
        self.entries: dict[str, SecretConfig] = dict(entries or {})

    def __len__(self) -> int:
        return len(self.entries)

    def __bool__(self) -> bool:
        return bool(self.entries)

    def ids(self) -> list[str]:
        return sorted(self.entries)

    def get(self, secret_id: str | None) -> SecretConfig | None:
        return self.entries.get(secret_id) if secret_id else None

    def problem(self, secret_id: str | None, kind: str) -> str | None:
        """Why this id cannot be used with this kind, or None when it can.

        The sentence is shown to a person -- by `/test`, by the connector
        route, by the migration script -- so it names the id and the kind and
        never a value.
        """
        if not secret_id:
            return None
        secret = self.entries.get(secret_id)
        if secret is None:
            return f"secret {secret_id!r} is not in [secrets] in sieve.toml"
        if kind not in secret.kinds:
            allowed = ", ".join(secret.kinds) or "no kind"
            return f"secret not allowed for kind {kind!r}: {secret_id!r} names {allowed}"
        return None

    def env(self, secret_id: str | None, kind: str) -> str | None:
        """The variable this id names for this kind, or None.

        None means "there is nothing to read" -- no id at all, an id the config
        does not have, or an id bound to other kinds. Use `problem()` when the
        reason has to be said out loud.
        """
        if self.problem(secret_id, kind):
            return None
        secret = self.entries.get(secret_id or "")
        return secret.env if secret else None

    def id_for_env(self, env: str | None, kind: str) -> str | None:
        """The id an environment-variable name is registered under, or None.

        The reverse lookup, and the only way a name from `sieve.toml` -- the
        seeder's `[inventories.*]` block, or a row the migration script is
        about to move -- becomes an id. A name registered under several ids is
        ambiguous: the first id that may be used with this kind wins, and the
        ids are read in sorted order so the answer does not move between runs.
        """
        if not env:
            return None
        for secret_id in self.ids():
            secret = self.entries[secret_id]
            if secret.env == env and kind in secret.kinds:
                return secret_id
        return None
