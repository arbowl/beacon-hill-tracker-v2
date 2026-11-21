"""Single-use emailed links for sign-in and sign-up, and their throttle."""

import hashlib
import secrets
from dataclasses import dataclass

from tracker.db import Connection
from tracker.enums import EmailKind

_TOKEN_BYTES = 32
_MAX_TOKEN_CHARS = 64


@dataclass(frozen=True)
class LinkClaim:
    """What a consumed link carries; signup_prefs is None for a sign-in link."""

    email: str
    signup_prefs: frozenset[EmailKind] | None
    next_path: str | None


def _hash(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def lock_email(conn: Connection, email: str) -> None:
    """Serialize work on one address until the transaction ends, across workers."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext(lower(%s)))", (email,))


def admit_request(conn: Connection, email: str, ip: str, per_email: int, per_ip_hour: int) -> bool:
    """Record a link request if under both limits; False when throttled.

    The per-email limit covers 15 minutes and is exact; the per-IP limit covers an hour and can be
    exceeded by a few concurrent requests from different addresses.
    """
    with conn.transaction():
        lock_email(conn, email)
        conn.execute("DELETE FROM login_requests WHERE requested_at < now() - interval '1 day'")
        conn.execute("DELETE FROM login_tokens WHERE expires_at < now() - interval '1 day'")
        row = conn.execute(
            "SELECT count(*) FILTER (WHERE email = %(email)s"
            "   AND requested_at > now() - interval '15 minutes') AS by_email,"
            " count(*) FILTER (WHERE ip = %(ip)s) AS by_ip"
            " FROM login_requests WHERE requested_at > now() - interval '1 hour'"
            "   AND (email = %(email)s OR ip = %(ip)s)",
            {"email": email, "ip": ip},
        ).fetchone()
        assert row is not None
        if row["by_email"] >= per_email or row["by_ip"] >= per_ip_hour:
            return False
        conn.execute("INSERT INTO login_requests (email, ip) VALUES (%s, %s)", (email, ip))
    return True


def issue(
    conn: Connection,
    email: str,
    ip: str,
    minutes: int,
    signup_prefs: frozenset[EmailKind] | None,
    next_path: str | None,
) -> str:
    """Store a new link's hash and return the token to email; it is not stored."""
    token = secrets.token_urlsafe(_TOKEN_BYTES)
    conn.execute(
        "INSERT INTO login_tokens (token_hash, email, expires_at, request_ip, signup_prefs,"
        " next_path) VALUES (%s, %s, now() + make_interval(mins => %s), %s, %s::email_kind[], %s)",
        (
            _hash(token),
            email,
            minutes,
            ip,
            None if signup_prefs is None else sorted(signup_prefs),
            next_path,
        ),
    )
    return token


def is_live(conn: Connection, token: str) -> bool:
    """Whether the link is unused and unexpired; does not consume it."""
    if len(token) > _MAX_TOKEN_CHARS:
        return False
    row = conn.execute(
        "SELECT 1 FROM login_tokens WHERE token_hash = %s AND used_at IS NULL"
        " AND expires_at > now()",
        (_hash(token),),
    ).fetchone()
    return row is not None


def consume(conn: Connection, token: str) -> LinkClaim | None:
    """Mark the link used and return its claim, or None if it is unknown, used, or expired."""
    if len(token) > _MAX_TOKEN_CHARS:
        return None
    row = conn.execute(
        "UPDATE login_tokens SET used_at = now() WHERE token_hash = %s AND used_at IS NULL"
        " AND expires_at > now() RETURNING email, signup_prefs::text[] AS signup_prefs, next_path",
        (_hash(token),),
    ).fetchone()
    if row is None:
        return None
    prefs = row["signup_prefs"]
    return LinkClaim(
        email=row["email"],
        signup_prefs=None if prefs is None else frozenset(EmailKind(k) for k in prefs),
        next_path=row["next_path"],
    )
