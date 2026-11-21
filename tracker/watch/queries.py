"""Watches: one row per user and watched bill or committee."""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from tracker.db import Connection
from tracker.enums import AlertKind, WatchKind


class WatchTarget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    kind: WatchKind
    session: Annotated[int, Field(gt=0)]
    target_id: Annotated[str, StringConstraints(pattern=r"^[A-Z0-9]{1,16}$")]


def target_exists(conn: Connection, target: WatchTarget) -> bool:
    """Whether the bill or committee is in the published data."""
    sql = (
        "SELECT 1 FROM current_tenures WHERE session = %s AND bill_id = %s LIMIT 1"
        if target.kind == WatchKind.BILL
        else "SELECT 1 FROM committees WHERE session = %s AND committee_id = %s"
    )
    return conn.execute(sql, (target.session, target.target_id)).fetchone() is not None


def is_watching(conn: Connection, user_id: int, target: WatchTarget) -> bool:
    row = conn.execute(
        "SELECT 1 FROM watches WHERE user_id = %s AND kind = %s AND session = %s"
        " AND target_id = %s",
        (user_id, target.kind, target.session, target.target_id),
    ).fetchone()
    return row is not None


def add(conn: Connection, user_id: int, target: WatchTarget, alerts: frozenset[AlertKind]) -> None:
    """Watch the target; an existing watch keeps its alerts."""
    conn.execute(
        "INSERT INTO watches (user_id, kind, session, target_id, alerts)"
        " VALUES (%s, %s, %s, %s, %s::alert_kind[]) ON CONFLICT DO NOTHING",
        (user_id, target.kind, target.session, target.target_id, sorted(alerts)),
    )


def remove(conn: Connection, user_id: int, target: WatchTarget) -> None:
    conn.execute(
        "DELETE FROM watches WHERE user_id = %s AND kind = %s AND session = %s AND target_id = %s",
        (user_id, target.kind, target.session, target.target_id),
    )


def set_alerts(conn: Connection, user_id: int, watch_id: int, alerts: frozenset[AlertKind]) -> bool:
    """Replace one of the user's watches' alerts; False if the user has no such watch."""
    return bool(
        conn.execute(
            "UPDATE watches SET alerts = %s::alert_kind[] WHERE id = %s AND user_id = %s",
            (sorted(alerts), watch_id, user_id),
        ).rowcount
    )


def watchlist(conn: Connection, user_id: int) -> list[dict[str, Any]]:
    """The user's watches, newest first, with each target's current published state.

    Bills carry title and segments [{committee_id, label}]; committees carry name, total, and
    compliance_rate. Fields are NULL when the target is no longer in the published data.
    """
    return conn.execute(
        "SELECT w.id, w.kind, w.session, w.target_id, w.alerts::text[] AS alerts,"
        "   c.name AS committee_name, s.total, s.compliance_rate, b.title, b.segments"
        " FROM watches w"
        " LEFT JOIN committees c ON w.kind = 'committee'"
        "   AND c.session = w.session AND c.committee_id = w.target_id"
        " LEFT JOIN committee_stats s ON w.kind = 'committee'"
        "   AND s.session = w.session AND s.committee_id = w.target_id"
        " LEFT JOIN LATERAL ("
        "   SELECT min(t.title) AS title,"
        "     jsonb_agg(jsonb_build_object('committee_id', t.committee_id, 'label', t.label)"
        "       ORDER BY t.committee_id) AS segments"
        "   FROM current_tenures t"
        "   WHERE w.kind = 'bill' AND t.session = w.session AND t.bill_id = w.target_id) b ON true"
        " WHERE w.user_id = %s ORDER BY w.created_at DESC, w.id DESC",
        (user_id,),
    ).fetchall()
