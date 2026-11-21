"""Site-wide mailings: the weekly summary and announcements.

Each recipient is recorded in email_sends under a key (the ISO week, or the announcement's
name) in the same transaction as their send, so rerunning a mailing reaches only those it missed.
Run on an autocommit connection, inside an app request context.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from typing import Any

from flask import url_for

from tracker.config import Settings
from tracker.dashboard import figures, queries
from tracker.db import Connection
from tracker.enums import EmailKind
from tracker.mail import MailNotConfigured, send
from tracker.mail.compose import compose, subscriber_links

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MailingResult:
    sent: int
    failures: int


@dataclass(frozen=True)
class Announcement:
    """A plain-text announcement: the first line is the subject, the rest is the body."""

    key: str
    subject: str
    body: str

    @classmethod
    def parse(cls, key: str, text: str) -> "Announcement":
        subject, _, body = text.strip().partition("\n")
        if not subject.strip() or not body.strip():
            raise ValueError("An announcement needs a subject line and a body")
        return cls(key, subject.strip(), body.strip())


def week_key(today: date) -> str:
    year, week, _ = today.isocalendar()
    return f"{year}-W{week:02d}"


def recipients(conn: Connection, kind: EmailKind, key: str) -> list[dict[str, Any]]:
    """Enabled accounts that opted in to kind and have not been sent this key."""
    return conn.execute(
        "SELECT u.id, u.email FROM users u"
        " JOIN user_email_prefs p ON p.user_id = u.id AND p.kind = %(kind)s AND p.enabled"
        " WHERE u.disabled_at IS NULL AND NOT EXISTS ("
        "   SELECT 1 FROM email_sends s WHERE s.user_id = u.id AND s.kind = %(kind)s"
        "   AND s.key = %(key)s)"
        " ORDER BY u.id",
        {"kind": kind, "key": key},
    ).fetchall()


def _send_each(
    conn: Connection,
    settings: Settings,
    kind: EmailKind,
    key: str,
    build: Callable[[int, str], EmailMessage],
) -> MailingResult:
    sent = failures = 0
    for r in recipients(conn, kind, key):
        try:
            with conn.transaction():
                claimed = conn.execute(
                    "INSERT INTO email_sends (user_id, kind, key, sent_at)"
                    " VALUES (%s, %s, %s, now()) ON CONFLICT DO NOTHING RETURNING user_id",
                    (r["id"], kind, key),
                ).fetchone()
                if claimed is None:
                    continue
                send(settings, build(r["id"], r["email"]))
        except (MailNotConfigured, OSError):
            log.exception("%s %s to user %s failed", kind, key, r["id"])
            failures += 1
            continue
        sent += 1
    return MailingResult(sent, failures)


def send_weekly(conn: Connection, settings: Settings, today: date, now: datetime) -> MailingResult:
    """Send this ISO week's summary of the newest session; nothing when no data is published."""
    status = queries.site_status(conn)
    if status is None:
        return MailingResult(0, 0)
    stats = queries.stats(conn, status.session, None)
    if stats is None:
        return MailingResult(0, 0)
    week = next(
        (
            d
            for d in figures.deltas(
                conn, status.session, queries.SESSION_ROW, stats["compliance_rate"], now
            )
            if d.period == "7d"
        ),
        None,
    )
    committees = conn.execute(
        "SELECT count(*) AS n FROM committees WHERE session = %s", (status.session,)
    ).fetchone()
    insights = [
        r | {"url": settings.site_link(r["href"])}
        for r in conn.execute(
            "SELECT body, href FROM insights WHERE session = %s ORDER BY position",
            (status.session,),
        )
    ]
    context = dict(
        status=status,
        stats=stats,
        week=week,
        committees=committees["n"] if committees else 0,
        insights=insights,
        week_of=today,
        dashboard_url=settings.site_link(url_for("dashboard.index")),
    )
    kinds = frozenset({EmailKind.WEEKLY_SUMMARY})
    return _send_each(
        conn,
        settings,
        EmailKind.WEEKLY_SUMMARY,
        week_key(today),
        lambda user_id, email: compose(
            settings, email, "weekly", links=subscriber_links(settings, user_id, kinds), **context
        ),
    )


def send_announcement(
    conn: Connection, settings: Settings, announcement: Announcement
) -> MailingResult:
    kinds = frozenset({EmailKind.ANNOUNCEMENTS})
    return _send_each(
        conn,
        settings,
        EmailKind.ANNOUNCEMENTS,
        announcement.key,
        lambda user_id, email: compose(
            settings,
            email,
            "announcement",
            links=subscriber_links(settings, user_id, kinds),
            announcement=announcement,
        ),
    )
