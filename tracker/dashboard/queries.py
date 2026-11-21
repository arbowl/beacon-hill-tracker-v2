"""Read-model queries for the dashboard."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import sql

from beacon_contract.v1 import ComplianceLabel
from tracker.dashboard.filters import PAGE_SIZE, SORT_KEYS, DashboardFilters, encode_cursor
from tracker.db import Connection
from tracker.display import FILTER_LABEL, LABEL_SEVERITY

SESSION_ROW = "*"


@dataclass(frozen=True)
class SiteStatus:
    session: int
    as_of: datetime
    rulesets: tuple[str, ...]


@dataclass(frozen=True)
class Page:
    rows: list[dict[str, Any]]
    next_cursor: str | None
    total: int
    label_counts: dict[ComplianceLabel, int]
    other_segments: dict[str, list[dict[str, Any]]]


def site_status(conn: Connection) -> SiteStatus | None:
    """The newest session with published data, when it was last updated, and its rulesets."""
    row = conn.execute(
        "SELECT cc.session, max(cc.as_of) AS as_of,"
        " array_agg(DISTINCT rc.ruleset_id || ' v' || rc.ruleset_version) AS rulesets"
        " FROM committee_current cc"
        " JOIN run_committees rc ON rc.run_id = cc.run_id AND rc.committee_id = cc.committee_id"
        " GROUP BY cc.session ORDER BY cc.session DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    return SiteStatus(row["session"], row["as_of"], tuple(sorted(row["rulesets"])))


def committees(conn: Connection, session: int) -> list[dict[str, Any]]:
    return conn.execute(
        "SELECT c.committee_id, c.name, c.chamber, cc.as_of FROM committees c"
        " JOIN committee_current cc USING (session, committee_id)"
        " WHERE c.session = %s ORDER BY c.name, c.committee_id",
        (session,),
    ).fetchall()


def stats(conn: Connection, session: int, committee_id: str | None) -> dict[str, Any] | None:
    return conn.execute(
        "SELECT * FROM committee_stats WHERE session = %s AND committee_id = %s",
        (session, committee_id or SESSION_ROW),
    ).fetchone()


def _where(
    session: int, f: DashboardFilters, *, with_status: bool, with_keyset: bool
) -> tuple[sql.Composable, dict[str, Any]]:
    conds = [sql.SQL("session = %(session)s")]
    params: dict[str, Any] = {"session": session}
    if f.committee:
        conds.append(sql.SQL("committee_id = %(committee)s"))
        params["committee"] = f.committee
    if with_status and f.status:
        conds.append(sql.SQL("severity = %(severity)s"))
        params["severity"] = LABEL_SEVERITY[FILTER_LABEL[f.status]]
    if f.bill_prefix:
        conds.append(sql.SQL("bill_id LIKE %(prefix)s"))
        params["prefix"] = f.bill_prefix + "%"
    elif f.q:
        conds.append(sql.SQL("search @@ websearch_to_tsquery('english', %(q)s)"))
        params["q"] = f.q
    keyset = f.keyset
    if with_keyset and keyset is not None:
        names = SORT_KEYS[f.sort]
        conds.append(
            sql.SQL("({}) > ({})").format(
                sql.SQL(", ").join(map(sql.Identifier, names)),
                sql.SQL(", ").join(sql.Placeholder(f"k{i}") for i in range(len(names))),
            )
        )
        params |= {f"k{i}": v for i, v in enumerate(keyset)}
    return sql.SQL(" AND ").join(conds), params


def label_counts(conn: Connection, session: int, f: DashboardFilters) -> dict[ComplianceLabel, int]:
    """Segments per label under every filter except status."""
    if not f.q:
        row = stats(conn, session, f.committee)
        if row is None:
            return dict.fromkeys(ComplianceLabel, 0)
        return {
            ComplianceLabel.COMPLIANT: row["compliant"],
            ComplianceLabel.PROVISIONAL: row["provisional"],
            ComplianceLabel.NON_COMPLIANT: row["non_compliant"],
        }
    where, params = _where(session, f, with_status=False, with_keyset=False)
    rows = conn.execute(
        sql.SQL("SELECT label, count(*) AS n FROM current_tenures WHERE {} GROUP BY label").format(
            where
        ),
        params,
    ).fetchall()
    counts = dict.fromkeys(ComplianceLabel, 0)
    counts.update({ComplianceLabel(r["label"]): r["n"] for r in rows})
    return counts


def other_segments(
    conn: Connection, session: int, bill_ids: Sequence[str]
) -> dict[str, list[dict[str, Any]]]:
    """Every live segment of these bills, grouped by bill."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in conn.execute(
        "SELECT bill_id, tenure_id, committee_id, committee_name, label FROM current_tenures"
        " WHERE session = %s AND bill_id = ANY(%s) ORDER BY referred_on NULLS LAST, committee_id",
        (session, list(bill_ids)),
    ):
        grouped.setdefault(r["bill_id"], []).append(r)
    return grouped


def segment_page(conn: Connection, session: int, f: DashboardFilters) -> Page:
    where, params = _where(session, f, with_status=True, with_keyset=True)
    order = sql.SQL(", ").join(map(sql.Identifier, SORT_KEYS[f.sort]))
    rows = conn.execute(
        sql.SQL("SELECT * FROM current_tenures WHERE {} ORDER BY {} LIMIT {}").format(
            where, order, sql.Literal(PAGE_SIZE + 1)
        ),
        params,
    ).fetchall()
    more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    counts = label_counts(conn, session, f)
    total = counts[FILTER_LABEL[f.status]] if f.status else sum(counts.values())
    return Page(
        rows=rows,
        next_cursor=encode_cursor(f.sort, rows[-1]) if more else None,
        total=total,
        label_counts=counts,
        other_segments=other_segments(conn, session, sorted({r["bill_id"] for r in rows})),
    )
