"""Insight sentences generated at publish.

Each states a count or a change and links to the filtered view that backs it. Selection
decides which fact is surfaced; the sentence never characterizes a committee.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from urllib.parse import urlencode
from uuid import UUID

from tracker.db import Connection

MIN_SEGMENTS = 20


class InsightKind(StrEnum):
    DEADLINE_COUNT = "deadline_count"
    BILLS_AFFECTED = "bills_affected"
    WEEKLY_CHANGE = "weekly_change"
    DUE_SOON = "due_soon"


@dataclass(frozen=True)
class Insight:
    kind: InsightKind
    body: str
    href: str
    link_text: str


def _href(**params: str) -> str:
    return "/?" + urlencode(params) if params else "/"


def _segments(n: int) -> str:
    return f"{n:,} bill segment" + ("" if n == 1 else "s")


def _deadline_count(conn: Connection, session: int, _now: datetime) -> Insight | None:
    row = conn.execute(
        "SELECT s.*, c.name FROM committee_stats s JOIN committees c USING (session, committee_id)"
        " WHERE s.session = %s AND s.total >= %s AND s.report_out_unmet > 0"
        " ORDER BY s.report_out_unmet DESC, c.name LIMIT 1",
        (session, MIN_SEGMENTS),
    ).fetchone()
    if row is None:
        return None
    verb = "has" if row["report_out_unmet"] == 1 else "have"
    return Insight(
        InsightKind.DEADLINE_COUNT,
        f"{_segments(row['report_out_unmet'])} in the {row['name']} {verb} a detected missed"
        f" reporting deadline this session. {row['compliance_rate']:.0f}% of its bill segments"
        " have no detected violation.",
        _href(committee=row["committee_id"], status="non-compliant"),
        "View these bill segments",
    )


def _bills_affected(conn: Connection, session: int, _now: datetime) -> Insight | None:
    row = conn.execute(
        "SELECT bills_total, bills_with_violation FROM committee_stats"
        " WHERE session = %s AND committee_id = '*'",
        (session,),
    ).fetchone()
    if row is None or row["bills_total"] == 0:
        return None
    n = row["bills_with_violation"]
    verb = "has" if n == 1 else "have"
    return Insight(
        InsightKind.BILLS_AFFECTED,
        f"{n:,} of {row['bills_total']:,} tracked bills {verb} at least one bill segment with a"
        " detected violation.",
        _href(status="non-compliant"),
        "View these bill segments",
    )


def _weekly_change(conn: Connection, session: int, now: datetime) -> Insight | None:
    row = conn.execute(
        "SELECT s.committee_id, c.name, s.compliance_rate,"
        "       round(100.0 * (w.compliant + w.provisional) / w.total, 2) AS week_ago"
        " FROM committee_stats s JOIN committees c USING (session, committee_id)"
        " CROSS JOIN LATERAL (SELECT * FROM snapshots p WHERE p.session = s.session"
        "   AND p.committee_id = s.committee_id AND p.taken_at <= %s AND p.total > 0"
        "   ORDER BY p.taken_at DESC LIMIT 1) w"
        " WHERE s.session = %s AND s.total >= %s"
        " ORDER BY abs(s.compliance_rate - round(100.0 * (w.compliant + w.provisional)"
        "   / w.total, 2)) DESC, c.name LIMIT 1",
        (now - timedelta(days=7), session, MIN_SEGMENTS),
    ).fetchone()
    if row is None:
        return None
    change = (row["compliance_rate"] - row["week_ago"]).quantize(Decimal("0.1"))
    if change == 0:
        return None
    return Insight(
        InsightKind.WEEKLY_CHANGE,
        f"The share of the {row['name']}'s bill segments with no detected violation changed by"
        f" {change:+} points over the past week.",
        _href(committee=row["committee_id"]),
        "View this committee",
    )


def _due_soon(conn: Connection, session: int, _now: datetime) -> Insight | None:
    row = conn.execute(
        "SELECT t.due_next_14_days AS total, s.committee_id, c.name, s.due_next_14_days AS n"
        " FROM committee_stats t"
        " JOIN committee_stats s ON s.session = t.session AND s.committee_id <> '*'"
        " JOIN committees c ON c.session = s.session AND c.committee_id = s.committee_id"
        " WHERE t.session = %s AND t.committee_id = '*' AND t.due_next_14_days > 0"
        " ORDER BY s.due_next_14_days DESC, c.name LIMIT 1",
        (session,),
    ).fetchone()
    if row is None:
        return None
    total = row["total"]
    verb = "has" if total == 1 else "have"
    where = (
        f", all of them in the {row['name']}"
        if row["n"] == total
        else f", {row['n']:,} of them in the {row['name']}"
    )
    return Insight(
        InsightKind.DUE_SOON,
        f"{_segments(total)} {verb} a pending requirement due in the next 14 days{where}.",
        _href(sort="due"),
        "View upcoming deadlines",
    )


GENERATORS: tuple[Callable[[Connection, int, datetime], Insight | None], ...] = (
    _deadline_count,
    _bills_affected,
    _weekly_change,
    _due_soon,
)


def regenerate(conn: Connection, session: int, run_id: UUID, now: datetime) -> list[Insight]:
    """Replace the session's insights; generators whose precondition fails are skipped."""
    found = [i for g in GENERATORS if (i := g(conn, session, now)) is not None]
    conn.execute("DELETE FROM insights WHERE session = %s", (session,))
    conn.cursor().executemany(
        "INSERT INTO insights (session, position, kind, body, href, link_text, run_id)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        [(session, n, i.kind, i.body, i.href, i.link_text, run_id) for n, i in enumerate(found)],
    )
    return found
