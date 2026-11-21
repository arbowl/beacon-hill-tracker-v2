"""Accounts and their email preferences."""

from dataclasses import dataclass

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
