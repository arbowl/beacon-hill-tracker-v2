"""Server-side web sessions; the cookie holds a random id and the table its sha256."""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from tracker.db import Connection
from tracker.enums import UserRole

_ID_BYTES = 32
_MAX_ID_CHARS = 64
_SEEN_RESOLUTION = timedelta(minutes=5)


@dataclass(frozen=True)
class SessionUser:
    user_id: int
    email: str
    role: UserRole


def _hash(session_id: str) -> bytes:
    return hashlib.sha256(session_id.encode("utf-8")).digest()


def create(conn: Connection, user_id: int, days: int) -> str:
    """Start a session and return its id for the cookie; also drops the user's expired ones."""
    session_id = secrets.token_urlsafe(_ID_BYTES)
    conn.execute("DELETE FROM web_sessions WHERE user_id = %s AND expires_at <= now()", (user_id,))
    conn.execute(
        "INSERT INTO web_sessions (id_hash, user_id, expires_at)"
        " VALUES (%s, %s, now() + make_interval(days => %s))",
        (_hash(session_id), user_id, days),
    )
    return session_id


def lookup(conn: Connection, session_id: str, now: datetime) -> SessionUser | None:
    """The live session's enabled user, or None. Refreshes last_seen_at at most every 5 minutes."""
    if len(session_id) > _MAX_ID_CHARS:
        return None
    row = conn.execute(
        "SELECT u.id, u.email, u.role, s.last_seen_at FROM web_sessions s"
        " JOIN users u ON u.id = s.user_id"
        " WHERE s.id_hash = %s AND s.expires_at > now() AND u.disabled_at IS NULL",
        (_hash(session_id),),
    ).fetchone()
    if row is None:
        return None
    if now - row["last_seen_at"] > _SEEN_RESOLUTION:
        conn.execute(
            "UPDATE web_sessions SET last_seen_at = now() WHERE id_hash = %s", (_hash(session_id),)
        )
    return SessionUser(row["id"], row["email"], UserRole(row["role"]))


def end(conn: Connection, session_id: str) -> None:
    conn.execute("DELETE FROM web_sessions WHERE id_hash = %s", (_hash(session_id),))


def end_all(conn: Connection, user_id: int) -> int:
    """End every session the user has; returns how many there were."""
    return conn.execute("DELETE FROM web_sessions WHERE user_id = %s", (user_id,)).rowcount
