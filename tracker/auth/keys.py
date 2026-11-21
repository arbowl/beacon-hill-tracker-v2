"""API keys: bht_<scope>_<random>, stored as sha256 with a display prefix."""

import hashlib
import secrets
from dataclasses import dataclass

from tracker.db import Connection
from tracker.enums import KeyScope, UserRole

_PREFIX_CHARS = 8


class KeyNotAllowed(PermissionError):
    """The key cannot be created for this user."""


@dataclass(frozen=True)
class NewKey:
    id: int
    secret: str
    prefix: str


@dataclass(frozen=True)
class KeyOwner:
    key_id: int
    user_id: int
    role: UserRole


def _hash(secret: str) -> bytes:
    return hashlib.sha256(secret.encode("ascii")).digest()


def create_key(conn: Connection, user_id: int, scope: KeyScope, name: str) -> NewKey:
    """Create a key; ingest keys only for an enabled webmaster. The secret is not stored."""
    user = conn.execute("SELECT role, disabled_at FROM users WHERE id = %s", (user_id,)).fetchone()
    if user is None or user["disabled_at"] is not None:
        raise KeyNotAllowed("No enabled user with that id")
    if scope == KeyScope.INGEST and user["role"] != UserRole.WEBMASTER:
        raise KeyNotAllowed("Only a webmaster can hold an ingest key")
    secret = f"bht_{scope}_{secrets.token_urlsafe(32)}"
    prefix = secret[: len(f"bht_{scope}_") + _PREFIX_CHARS]
    row = conn.execute(
        "INSERT INTO api_keys (user_id, scope, name, prefix, key_hash)"
        " VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (user_id, scope, name, prefix, _hash(secret)),
    ).fetchone()
    assert row is not None
    return NewKey(row["id"], secret, prefix)


def authenticate(conn: Connection, secret: str, scope: KeyScope) -> KeyOwner | None:
    """The live key's owner, or None. Ingest keys stop working if the owner loses webmaster."""
    row = conn.execute(
        "UPDATE api_keys k SET last_used_at = now() FROM users u"
        " WHERE k.key_hash = %s AND k.scope = %s AND k.revoked_at IS NULL"
        "   AND u.id = k.user_id AND u.disabled_at IS NULL"
        "   AND (k.scope <> 'ingest' OR u.role = 'webmaster')"
        " RETURNING k.id, u.id AS user_id, u.role",
        (_hash(secret), scope),
    ).fetchone()
    return None if row is None else KeyOwner(row["id"], row["user_id"], UserRole(row["role"]))


def live_keys(conn: Connection, user_id: int, scope: KeyScope) -> list[dict]:
    """The user's unrevoked keys of a scope, newest first, without their hashes."""
    return conn.execute(
        "SELECT id, name, prefix, created_at, last_used_at FROM api_keys"
        " WHERE user_id = %s AND scope = %s AND revoked_at IS NULL"
        " ORDER BY created_at DESC, id DESC",
        (user_id, scope),
    ).fetchall()


def revoke(conn: Connection, user_id: int, key_id: int) -> bool:
    """Revoke one of the user's live keys; False if they have no such key."""
    return bool(
        conn.execute(
            "UPDATE api_keys SET revoked_at = now() WHERE id = %s AND user_id = %s"
            " AND revoked_at IS NULL",
            (key_id, user_id),
        ).rowcount
    )
