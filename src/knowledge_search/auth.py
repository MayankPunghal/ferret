"""Local accounts, opaque session tokens, and collection-level authorization.

Authorization is always resolved server-side into the set of collection ids the principal
may read; every query, passage, source download and status view filters on that set.
Unauthorized resources return 404 (never 403) so their existence is not disclosed.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from psycopg import Connection

_ph = PasswordHasher()
# Verified against when the username does not exist, to keep login timing uniform.
_DUMMY_HASH = _ph.hash("not-a-real-password")


@dataclass(frozen=True)
class Principal:
    user_id: int
    username: str
    is_admin: bool
    readable: frozenset[int]
    writable: frozenset[int]


def hash_password(pw: str) -> str:
    if len(pw) < 10:
        raise ValueError("password must be at least 10 characters")
    return _ph.hash(pw)


def _token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def authenticate(conn: Connection, username: str, password: str) -> int | None:
    row = conn.execute(
        "SELECT id, password_hash FROM users WHERE username = %s AND NOT disabled", (username.lower(),)
    ).fetchone()
    try:
        _ph.verify(row["password_hash"] if row else _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return None
    return row["id"] if row else None


def create_session(conn: Connection, user_id: int, hours: int, label: str | None = None, read_only: bool = False) -> str:
    """A browser session, or with a label an access token for an AI assistant: the user's rights, or with
    read_only only their read access (no uploads, no administration)."""
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions(token_hash, user_id, expires_at, label, read_only) VALUES (%s, %s, %s, %s, %s)",
        (_token_hash(token), user_id, datetime.now(UTC) + timedelta(hours=hours), label, read_only),
    )
    return token


def delete_session(conn: Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash = %s", (_token_hash(token),))


def load_principal(conn: Connection, token: str) -> Principal | None:
    row = conn.execute(
        "SELECT u.id, u.username, u.is_admin, s.read_only FROM sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token_hash = %s AND s.expires_at > now() AND NOT u.disabled",
        (_token_hash(token),),
    ).fetchone()
    if not row:
        return None
    p = principal_for(conn, row["id"], row["username"], row["is_admin"])
    return Principal(p.user_id, p.username, False, p.readable, frozenset()) if row["read_only"] else p


def principal_for(conn: Connection, user_id: int, username: str, is_admin: bool) -> Principal:
    if is_admin:
        ids = frozenset(r["id"] for r in conn.execute("SELECT id FROM collections"))
        return Principal(user_id, username, True, ids, ids)
    rows = conn.execute(
        "SELECT g.collection_id, bool_or(g.can_write) AS w FROM collection_grants g "
        "JOIN user_groups ug ON ug.group_id = g.group_id WHERE ug.user_id = %s GROUP BY g.collection_id",
        (user_id,),
    ).fetchall()
    return Principal(
        user_id,
        username,
        False,
        frozenset(r["collection_id"] for r in rows),
        frozenset(r["collection_id"] for r in rows if r["w"]),
    )


# --- administration helpers (used by CLI and admin API) ---------------------------------


def create_user(conn: Connection, username: str, password: str, is_admin: bool = False) -> int:
    return conn.execute(
        "INSERT INTO users(username, password_hash, is_admin) VALUES (%s, %s, %s) RETURNING id",
        (username.lower(), hash_password(password), is_admin),
    ).fetchone()["id"]


def ensure_group(conn: Connection, name: str) -> int:
    conn.execute("INSERT INTO groups(name) VALUES (%s) ON CONFLICT DO NOTHING", (name,))
    return conn.execute("SELECT id FROM groups WHERE name = %s", (name,)).fetchone()["id"]


def add_user_to_group(conn: Connection, username: str, group: str) -> None:
    gid = ensure_group(conn, group)
    conn.execute(
        "INSERT INTO user_groups(user_id, group_id) SELECT id, %s FROM users WHERE username = %s "
        "ON CONFLICT DO NOTHING",
        (gid, username.lower()),
    )


def remove_user_from_group(conn: Connection, username: str, group: str) -> None:
    conn.execute(
        "DELETE FROM user_groups WHERE user_id = (SELECT id FROM users WHERE username = %s) "
        "AND group_id = (SELECT id FROM groups WHERE name = %s)",
        (username.lower(), group),
    )


def ensure_collection(conn: Connection, name: str, description: str = "") -> int:
    conn.execute(
        "INSERT INTO collections(name, description) VALUES (%s, %s) ON CONFLICT (name) DO NOTHING",
        (name, description),
    )
    return conn.execute("SELECT id FROM collections WHERE name = %s", (name,)).fetchone()["id"]


def grant(conn: Connection, collection: str, group: str, can_write: bool = False) -> None:
    gid = ensure_group(conn, group)
    cid = ensure_collection(conn, collection)
    conn.execute(
        "INSERT INTO collection_grants(collection_id, group_id, can_write) VALUES (%s, %s, %s) "
        "ON CONFLICT (collection_id, group_id) DO UPDATE SET can_write = EXCLUDED.can_write",
        (cid, gid, can_write),
    )


def audit(conn: Connection, user_id: int | None, action: str, target: str | None = None, **detail) -> None:
    from psycopg.types.json import Jsonb

    conn.execute(
        "INSERT INTO audit_events(user_id, action, target, detail) VALUES (%s, %s, %s, %s)",
        (user_id, action, target, Jsonb(detail)),
    )
