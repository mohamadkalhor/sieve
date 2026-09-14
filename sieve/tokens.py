"""Per-user script tokens: a secret somebody's cron job carries (AMS-28).

`SIEVE_TOKENS` is the box's own configuration and authenticates as the gate
owner, which is what keeps every existing script working. These are different:
a person mints one from their own seat, it authenticates as *them*, and it can
be revoked without editing a unit file and restarting the service.

Only the sha256 is stored. The secret is returned once, by the call that made
it, and is unrecoverable afterwards -- so a leaked database is not a set of
working credentials.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sieve.store import Store

#: A minted secret is prefixed so it is recognisable in a log as a Sieve token
#: that should not be there, rather than as an opaque blob nobody investigates.
PREFIX = "sv_"


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


@dataclass(frozen=True)
class ScriptToken:
    id: str
    owner_id: str
    name: str
    scopes: frozenset[str]
    sha256: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked: bool = False

    def json(self) -> dict[str, Any]:
        """What the API shows. Never the secret, and never the hash: the hash
        is a verifier, and printing it turns a stolen read into a stolen
        offline guessing game."""
        return {
            "id": self.id,
            "name": self.name,
            "scopes": sorted(self.scopes),
            "created_at": self.created_at.isoformat(),
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "revoked": self.revoked,
        }


def _row(r: Any) -> ScriptToken:
    from sieve.store.db import _dt

    return ScriptToken(
        id=r["id"],
        owner_id=r["owner_id"],
        name=r["name"],
        scopes=frozenset(s for s in (r["scopes"] or "").split(",") if s),
        sha256=r["sha256"],
        created_at=_dt(r["created_at"]),
        last_used_at=_dt(r["last_used_at"]) if r["last_used_at"] else None,
        revoked=bool(r["revoked"]),
    )


def tokens(store: Store, owner_id: str) -> list[ScriptToken]:
    """The live tokens this person holds. A revoked one is gone, not greyed
    out: its row stays only so the hash can never be honoured again."""
    return [
        _row(r)
        for r in store.db.execute(
            "SELECT * FROM tokens WHERE owner_id=? AND revoked=0 ORDER BY created_at DESC",
            (owner_id,),
        )
    ]


def mint(store: Store, owner_id: str, name: str, scopes: set[str]) -> tuple[ScriptToken, str]:
    """Make a token. Returns the record and the secret, which is shown once.

    Two live tokens of one person may not share a name -- that is the label she
    revokes by -- but a revoked one holds nothing, so its name is free again.
    """
    name = name.strip()
    if not name:
        raise ValueError("a token needs a name")
    if not scopes:
        raise ValueError("a token needs at least one scope")
    held = store.db.execute(
        "SELECT 1 FROM tokens WHERE owner_id=? AND name=? AND revoked=0", (owner_id, name)
    ).fetchone()
    if held:
        raise ValueError(f"you already have a token called {name!r}")
    secret = PREFIX + secrets.token_urlsafe(32)
    made = ScriptToken(
        id=uuid.uuid4().hex[:12],
        owner_id=owner_id,
        name=name,
        scopes=frozenset(scopes),
        sha256=digest(secret),
        created_at=datetime.now(UTC),
    )
    with store.tx() as db:
        db.execute(
            "INSERT INTO tokens(id,owner_id,name,scopes,sha256,created_at,revoked)"
            " VALUES(?,?,?,?,?,?,0)",
            (
                made.id,
                made.owner_id,
                made.name,
                ",".join(sorted(made.scopes)),
                made.sha256,
                _iso(made.created_at),
            ),
        )
    return made, secret


def revoke(store: Store, owner_id: str, token_id: str) -> bool:
    """Retire a token. The row stays, so the name keeps meaning something in a
    list of what was once issued; only its ability to authenticate ends."""
    with store.tx() as db:
        cur = db.execute(
            "UPDATE tokens SET revoked=1 WHERE id=? AND owner_id=? AND revoked=0",
            (token_id, owner_id),
        )
    return bool(cur.rowcount)


def lookup(store: Store, secret: str) -> ScriptToken | None:
    """The live token this secret is, by hash. Constant work, no scan of secrets."""
    row = store.db.execute(
        "SELECT * FROM tokens WHERE sha256=? AND revoked=0", (digest(secret),)
    ).fetchone()
    if row is None:
        return None
    found = _row(row)
    with store.tx() as db:
        db.execute(
            "UPDATE tokens SET last_used_at=? WHERE id=?", (_iso(datetime.now(UTC)), found.id)
        )
    return found
