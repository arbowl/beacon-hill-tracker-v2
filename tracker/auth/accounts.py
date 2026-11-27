"""Accounts and their email preferences."""

from dataclasses import dataclass
from typing import Any

from tracker.auth.links import LinkClaim, lock_email
from tracker.db import Connection
from tracker.enums import EMAIL_ALERT, AlertKind, EmailKind, UserRole


class AccountExists(ValueError):
    """An account already uses that address."""


@dataclass(frozen=True)
class AccountState:
    user_id: int
    disabled: bool


def find(conn: Connection, email: str) -> AccountState | None:
    row = conn.execute("SELECT id, disabled_at FROM users WHERE email = %s", (email,)).fetchone()
    return None if row is None else AccountState(row["id"], row["disabled_at"] is not None)


def create(conn: Connection, email: str, role: UserRole, enabled: frozenset[EmailKind]) -> int:
    """Create an account with one preference row per email kind."""
    row = conn.execute(
        "INSERT INTO users (email, role) VALUES (%s, %s) ON CONFLICT (email) DO NOTHING"
        " RETURNING id",
        (email, role),
    ).fetchone()
    if row is None:
        raise AccountExists(email)
    conn.execute(
        "INSERT INTO user_email_prefs (user_id, kind, enabled)"
        " SELECT %s, k, k = ANY(%s::email_kind[]) FROM unnest(enum_range(NULL::email_kind)) k",
        (row["id"], sorted(enabled)),
    )
    return row["id"]


def sign_in(conn: Connection, claim: LinkClaim) -> int | None:
    """The account a consumed link signs in to, creating it for a sign-up link.

    None when the account is disabled, or when a sign-in link names an address with no account.
    Call inside the transaction that consumed the link.
    """
    lock_email(conn, claim.email)
    account = find(conn, claim.email)
    if account is None:
        if claim.signup_prefs is None:
            return None
        user_id = create(conn, claim.email, UserRole.USER, claim.signup_prefs)
    elif account.disabled:
        return None
    else:
        user_id = account.user_id
    conn.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user_id,))
    return user_id


def email_prefs(conn: Connection, user_id: int) -> frozenset[EmailKind]:
    """The kinds the user has enabled; a kind with no recorded choice is off."""
    rows = conn.execute(
        "SELECT kind FROM user_email_prefs WHERE user_id = %s AND enabled", (user_id,)
    ).fetchall()
    return frozenset(EmailKind(r["kind"]) for r in rows)


def set_email_prefs(conn: Connection, user_id: int, enabled: frozenset[EmailKind]) -> None:
    """Record a choice for every kind; updated_at moves only for kinds that changed."""
    conn.execute(
        "INSERT INTO user_email_prefs (user_id, kind, enabled)"
        " SELECT %s, k, k = ANY(%s::email_kind[]) FROM unnest(enum_range(NULL::email_kind)) k"
        " ON CONFLICT (user_id, kind) DO UPDATE SET enabled = EXCLUDED.enabled, updated_at = now()"
        " WHERE user_email_prefs.enabled IS DISTINCT FROM EXCLUDED.enabled",
        (user_id, sorted(enabled)),
    )


def default_alerts(enabled: frozenset[EmailKind]) -> frozenset[AlertKind]:
    """The alerts a new watch starts with."""
    return frozenset(alert for kind, alert in EMAIL_ALERT.items() if kind in enabled)


def signup_summary(conn: Connection) -> dict[str, int]:
    """Account counts: total, new in the last 7 and 30 days, and those taking any email."""
    row = conn.execute(
        "SELECT count(*) AS total,"
        "   count(*) FILTER (WHERE created_at > now() - interval '7 days') AS week,"
        "   count(*) FILTER (WHERE created_at > now() - interval '30 days') AS month,"
        "   count(*) FILTER (WHERE EXISTS (SELECT 1 FROM user_email_prefs p"
        "     WHERE p.user_id = u.id AND p.enabled)) AS emailed"
        " FROM users u WHERE disabled_at IS NULL"
    ).fetchone()
    assert row is not None
    return dict(row)


def recent_signups(conn: Connection, limit: int) -> list[dict[str, Any]]:
    """The newest accounts with their sign-up time, last sign-in, and enabled email kinds."""
    return conn.execute(
        "SELECT u.email, u.created_at, u.last_login_at, u.disabled_at IS NOT NULL AS disabled,"
        "   coalesce(array_agg(p.kind::text ORDER BY p.kind) FILTER (WHERE p.enabled), '{}')"
        "     AS kinds"
        " FROM users u LEFT JOIN user_email_prefs p ON p.user_id = u.id"
        " GROUP BY u.id ORDER BY u.created_at DESC, u.id DESC LIMIT %s",
        (limit,),
    ).fetchall()
