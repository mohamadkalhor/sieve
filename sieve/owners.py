"""Who owns a row, and which people Sieve knows about (AMS-28).

Sieve keeps its **own** `users` table. gate signs people in and states an email
and a role; it does not offer a readable directory to this box, and an identity
that needs a network call to resolve is an identity that vanishes when gate is
down. So a sign-in is matched to a local row by email, and every row anybody
creates carries that row's id in `owner_id`.

Three rules the rest of the code leans on:

- **NULL is shared.** A builtin axis has no owner and everybody may read it.
- **The gate owner keeps what exists today.** On the first sign-in of
  `SIEVE_OWNER_EMAIL` every unowned row that is not a builtin axis is adopted,
  so Mohamad's twenty-two profiles, his connector and his multipliers stay his.
- **A new member starts with the shipped seed profiles and no connectors.** A
  copy, marked private, so editing `judge` cannot change anybody else's.
"""

from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sieve.store import Store

#: The email gate calls the owner. Set in `/etc/default/sieve`, never printed.
OWNER_EMAIL_ENV = "SIEVE_OWNER_EMAIL"

ROLES = ("owner", "member", "viewer")

_SLUG_BAD = re.compile(r"[^a-z0-9]+")


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class User:
    id: str
    email: str
    role: str
    slug: str
    gate_id: str | None = None

    def json(self) -> dict[str, Any]:
        return {"user_id": self.id, "email": self.email, "role": self.role, "slug": self.slug}


def slug_for(email: str, taken: set[str]) -> str:
    """A short, URL-safe name for this person, used in their combo names.

    It goes into `sieve-<profile>-<slug>` on a router, so it stays boring: the
    local part of the address, lowercased, anything else collapsed to a hyphen.
    """
    local = email.split("@", 1)[0].lower()
    base = _SLUG_BAD.sub("-", local).strip("-")[:24] or "user"
    candidate = base
    suffix = 2
    while candidate in taken:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def _user(row: Any) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        role=row["role"],
        slug=row["slug"],
        gate_id=row["gate_id"],
    )


def users(store: Store) -> list[User]:
    return [_user(r) for r in store.db.execute("SELECT * FROM users ORDER BY created_at")]


def by_id(store: Store, user_id: str) -> User | None:
    row = store.db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    return _user(row) if row else None


def by_email(store: Store, email: str) -> User | None:
    row = store.db.execute("SELECT * FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
    return _user(row) if row else None


def owner_email() -> str | None:
    value = os.environ.get(OWNER_EMAIL_ENV, "").strip().lower()
    return value or None


def owner(store: Store) -> User | None:
    """The gate owner as this store knows them, by email first, role second."""
    email = owner_email()
    if email:
        found = by_email(store, email)
        if found is not None:
            return found
    row = store.db.execute(
        "SELECT * FROM users WHERE role='owner' ORDER BY created_at LIMIT 1"
    ).fetchone()
    return _user(row) if row else None


def ensure_owner(store: Store) -> User | None:
    """The configured owner, with a row and everything that predates sign-ins.

    Called at every start. Without `SIEVE_OWNER_EMAIL` it only reports what the
    store already knows, so a box that never heard of gate stays exactly as it
    was: no users, every row unowned, every read matching.

    With it, the owner exists before anybody visits -- which matters because
    the seeds that run next would otherwise insert a second, unowned copy of
    the twenty-two profiles he has just adopted.
    """
    email = owner_email()
    if not email:
        return owner(store)
    found = by_email(store, email)
    if found is None:
        found = create(store, email, "owner")
    elif found.role != "owner":
        found = touch(store, found, role="owner")
    adopt_orphans(store, found.id)
    return found


def create(store: Store, email: str, role: str, gate_id: str | None = None) -> User:
    email = email.strip().lower()
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    taken = {u.slug for u in users(store)}
    made = User(
        id=uuid.uuid4().hex[:12],
        email=email,
        role=role,
        slug=slug_for(email, taken),
        gate_id=gate_id,
    )
    with store.tx() as db:
        db.execute(
            "INSERT INTO users(id,email,role,slug,gate_id,created_at,last_seen_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (made.id, made.email, made.role, made.slug, made.gate_id, _iso(now()), _iso(now())),
        )
    return made


def touch(store: Store, user: User, *, role: str | None = None, gate_id: str | None = None) -> User:
    """Record that this person was just seen, and take gate's word on the role.

    gate is where a promotion or a demotion happens; a stale role here would
    keep a demoted member writing for as long as the row lived.
    """
    changed = user
    if role and role in ROLES and role != user.role:
        changed = User(user.id, user.email, role, user.slug, gate_id or user.gate_id)
    elif gate_id and gate_id != user.gate_id:
        changed = User(user.id, user.email, user.role, user.slug, gate_id)
    with store.tx() as db:
        db.execute(
            "UPDATE users SET role=?, gate_id=COALESCE(?,gate_id), last_seen_at=? WHERE id=?",
            (changed.role, gate_id, _iso(now()), user.id),
        )
    return changed


#: Every table an unowned row can sit in. A builtin axis is deliberately not
#: adopted: it stays shared, so everybody keeps the shipped vocabulary.
_ADOPTED = (
    ("profiles", ""),
    ("profile_models", ""),
    ("outcomes", ""),
    ("cost_multipliers", ""),
    ("connectors", ""),
    ("chains", ""),
    ("rankings", ""),
    ("axes", " AND builtin=0"),
)


def adopt_orphans(store: Store, user_id: str) -> dict[str, int]:
    """Give every unowned row to this person. What the owner's first sign-in does."""
    counts: dict[str, int] = {}
    with store.tx() as db:
        for table, extra in _ADOPTED:
            cursor = db.execute(
                f"UPDATE {table} SET owner_id=? WHERE owner_id IS NULL{extra}", (user_id,)
            )
            if cursor.rowcount:
                counts[table] = int(cursor.rowcount)
        # The owner's own profiles were the only ones before this; they are his
        # and private, exactly as a member's copies will be.
        db.execute("UPDATE profiles SET visibility='private' WHERE owner_id=?", (user_id,))
    return counts


def seed_profiles_for(store: Store, user: User, directory: Any) -> int:
    """Give a new member a private copy of each shipped LLM seat.

    Copies of the YAML seeds rather than of the owner's profiles: his are tuned
    to his traffic, and handing them out would leak what he has been doing.
    """
    from sieve.profiles import control
    from sieve.profiles.load import load_profiles

    made = 0
    held = {p.name for p in control.profiles(store, owner_id=user.id, shared=False)}
    for profile in load_profiles(directory):
        if profile.modality != "llm" or profile.name in held:
            continue
        control.put_profile(store, profile, owner_id=user.id)
        made += 1
    return made


def sign_in(
    store: Store, email: str, role: str, directory: Any, gate_id: str | None = None
) -> User:
    """Resolve a gate session to a local user, making one the first time.

    The owner adopts everything that has no owner yet; anybody else starts with
    the seed profiles and no connectors.
    """
    email = email.strip().lower()
    known = by_email(store, email)
    configured = owner_email()
    if known is not None:
        # The configured owner is the owner here, whatever gate last called them.
        wanted = "owner" if configured and email == configured else role
        return touch(store, known, role=wanted, gate_id=gate_id)
    if configured and email == configured:
        made = create(store, email, "owner", gate_id)
        adopt_orphans(store, made.id)
        return made
    made = create(store, email, role if role in ROLES else "member", gate_id)
    if made.role != "viewer":
        seed_profiles_for(store, made, directory)
    return made


def counts_for(store: Store, user: User) -> dict[str, int]:
    """What `/v1/me` reports: how much of this store is theirs."""
    mine = (user.id,)
    one = store.db.execute
    return {
        "profiles": int(
            one(
                "SELECT COUNT(*) n FROM profiles WHERE owner_id=? OR visibility='shared'", mine
            ).fetchone()["n"]
        ),
        "connectors": int(
            one("SELECT COUNT(*) n FROM connectors WHERE owner_id=?", mine).fetchone()["n"]
        ),
        "axes": int(
            one(
                "SELECT COUNT(*) n FROM axes WHERE owner_id=? OR owner_id IS NULL"
                " OR visibility='shared'",
                mine,
            ).fetchone()["n"]
        ),
        "tokens": int(
            one("SELECT COUNT(*) n FROM tokens WHERE owner_id=? AND revoked=0", mine).fetchone()[
                "n"
            ]
        ),
    }
