"""Hourly digests: each user's undelivered alert events in one email.

Run on an autocommit connection, inside an app request context (templates and url_for).
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from itertools import groupby
from typing import Any

from flask import url_for

from beacon_contract.v1 import ComplianceLabel, RequirementId, RequirementStatus
from tracker.config import Settings
from tracker.db import Connection
from tracker.display import CHIP_TEXT, LABEL_TEXT, REQUIREMENT_TEXT, day
from tracker.enums import ALERT_EMAIL, AlertKind
from tracker.mail import MailNotConfigured, send
from tracker.mail.compose import compose, subscriber_links

log = logging.getLogger(__name__)

DIGEST_LOCK = 7_194_003
MAX_EVENT_AGE_DAYS = 14
MAX_SEGMENTS_SHOWN = 50

# Events a user should get and has not: matched through a bill or committee watch created
# no later than the event, with the alert on for that watch and its email kind enabled.
_PENDING = """
WITH recent AS (
  SELECT * FROM alert_events
  WHERE withdrawn_at IS NULL AND created_at > now() - make_interval(days => %(max_age)s)),
matched AS (
  SELECT w.user_id, e.id FROM recent e
  JOIN watches w ON w.kind = 'bill' AND w.session = e.session AND w.target_id = e.bill_id
   AND e.kind = ANY(w.alerts) AND e.created_at >= w.created_at
  UNION
  SELECT w.user_id, e.id FROM recent e
  JOIN watches w ON w.kind = 'committee' AND w.session = e.session
   AND w.target_id = e.committee_id AND e.kind = ANY(w.alerts) AND e.created_at >= w.created_at)
SELECT m.user_id, u.email, e.id, e.kind, e.session, e.bill_id, e.committee_id, e.requirement,
       e.due, lc.from_label, lc.to_label, lc.changed_requirements,
       coalesce(c.name, e.committee_id) AS committee_name, t.title
FROM matched m
JOIN alert_events e ON e.id = m.id
JOIN users u ON u.id = m.user_id AND u.disabled_at IS NULL
JOIN unnest(%(alerts)s::alert_kind[], %(emails)s::email_kind[]) k(alert, email) ON k.alert = e.kind
JOIN user_email_prefs p ON p.user_id = m.user_id AND p.kind = k.email AND p.enabled
LEFT JOIN alert_deliveries d ON d.user_id = m.user_id AND d.alert_event_id = e.id
LEFT JOIN label_changes lc ON lc.id = e.label_change_id
LEFT JOIN committees c ON c.session = e.session AND c.committee_id = e.committee_id
LEFT JOIN LATERAL (
  SELECT min(ct.title) AS title FROM current_tenures ct
  WHERE ct.session = e.session AND ct.bill_id = e.bill_id) t ON true
WHERE d.user_id IS NULL
ORDER BY m.user_id, e.session, substring(e.bill_id FROM 2)::int, e.bill_id, e.committee_id, e.id
"""


class DigestBusy(RuntimeError):
    """Another digest run holds the lock."""


@dataclass(frozen=True)
class DigestSegment:
    """One bill segment's lines in a digest."""

    bill_id: str
    title: str | None
    committee_id: str
    committee_name: str
    url: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class DigestResult:
    emails_sent: int
    events_delivered: int
    failures: int


def _status(value: str | None) -> str:
    return CHIP_TEXT[None if value is None else RequirementStatus(value)]


def _lines(row: dict[str, Any]) -> list[str]:
    kind = AlertKind(row["kind"])
    if kind == AlertKind.DEADLINE_APPROACHING:
        return [f"{REQUIREMENT_TEXT[RequirementId(row['requirement'])]} is due {day(row['due'])}."]
    if kind == AlertKind.DEADLINE_MISSED:
        name = REQUIREMENT_TEXT[RequirementId(row["requirement"])]
        return [f"{name} was due {day(row['due'])}; the public record does not show it."]
    old, new = row["from_label"], row["to_label"]
    if old is None:
        lines = [f"Now tracked: {LABEL_TEXT[ComplianceLabel(new)]}."]
    elif new is None:
        lines = ["No longer in the committee's published data."]
    elif old != new:
        lines = [
            f"Changed from {LABEL_TEXT[ComplianceLabel(old)]}"
            f" to {LABEL_TEXT[ComplianceLabel(new)]}."
        ]
    else:
        lines = []
    if old is not None and new is not None:
        lines += [
            f"{REQUIREMENT_TEXT[RequirementId(c['requirement'])]}: {_status(c['from'])}"
            f" to {_status(c['to'])}."
            for c in row["changed_requirements"]
        ]
    return lines


def segments(settings: Settings, rows: Iterable[dict[str, Any]]) -> list[DigestSegment]:
    """Group one user's events by bill segment, keeping their order."""
    found = []
    for (session, bill_id, committee_id), group in groupby(
        rows, key=lambda r: (r["session"], r["bill_id"], r["committee_id"])
    ):
        items = list(group)
        path = url_for("bills.bill", session=session, bill_id=bill_id)
        found.append(
            DigestSegment(
                bill_id=bill_id,
                title=items[0]["title"],
                committee_id=committee_id,
                committee_name=items[0]["committee_name"],
                url=settings.site_link(f"{path}#seg-{committee_id.lower()}"),
                lines=tuple(line for r in items for line in _lines(r)),
            )
        )
    return found


def _claim(conn: Connection, user_id: int, event_ids: list[int]) -> set[int]:
    rows = conn.execute(
        "INSERT INTO alert_deliveries (user_id, alert_event_id, sent_at)"
        " SELECT %s, unnest(%s::bigint[]), now() ON CONFLICT DO NOTHING RETURNING alert_event_id",
        (user_id, event_ids),
    ).fetchall()
    return {r["alert_event_id"] for r in rows}


def send_digests(conn: Connection, settings: Settings) -> DigestResult:
    """Send every pending digest. A user's deliveries commit only if their email was accepted."""
    acquired = conn.execute("SELECT pg_try_advisory_lock(%s) AS ok", (DIGEST_LOCK,)).fetchone()
    if acquired is None or not acquired["ok"]:
        raise DigestBusy("Another digest run is in progress")
    try:
        rows = conn.execute(
            _PENDING,
            {
                "max_age": MAX_EVENT_AGE_DAYS,
                "alerts": list(ALERT_EMAIL),
                "emails": list(ALERT_EMAIL.values()),
            },
        ).fetchall()
        sent = delivered = failures = 0
        for (user_id, email), group in groupby(rows, key=lambda r: (r["user_id"], r["email"])):
            items = list(group)
            try:
                with conn.transaction():
                    claimed = _claim(conn, user_id, [r["id"] for r in items])
                    items = [r for r in items if r["id"] in claimed]
                    if not items:
                        continue
                    kinds = frozenset(ALERT_EMAIL[AlertKind(r["kind"])] for r in items)
                    shown = segments(settings, items)
                    message = compose(
                        settings,
                        email,
                        "digest",
                        links=subscriber_links(settings, user_id, kinds),
                        segments=shown[:MAX_SEGMENTS_SHOWN],
                        hidden=max(0, len(shown) - MAX_SEGMENTS_SHOWN),
                        total=len(shown),
                    )
                    send(settings, message)
            except (MailNotConfigured, OSError):
                log.exception("digest to user %s failed", user_id)
                failures += 1
                continue
            sent += 1
            delivered += len(items)
        return DigestResult(sent, delivered, failures)
    finally:
        conn.execute("SELECT pg_advisory_unlock(%s)", (DIGEST_LOCK,))
