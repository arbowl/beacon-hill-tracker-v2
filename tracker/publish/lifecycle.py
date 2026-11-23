"""Rollback and retention of published runs."""

from dataclasses import dataclass
from uuid import UUID

from tracker.alerts import events
from tracker.db import Connection, transaction_time
from tracker.ingest.service import PUBLISH_LOCK, upsert_committees
from tracker.publish.rebuild import rebuild


class RollbackError(RuntimeError):
    """There is nothing retained to roll back to."""


@dataclass(frozen=True)
class Rollback:
    committee_id: str
    rolled_back_run: UUID
    restored_run: UUID


def rollback(conn: Connection, session: int, committee_id: str) -> Rollback:
    """Withdraw a committee's latest publication and restore the one before it."""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (PUBLISH_LOCK,))
        rows = conn.execute(
            "SELECT p.run_id, rc.pruned_at FROM committee_publications p"
            " JOIN run_committees rc ON rc.run_id = p.run_id AND rc.committee_id = p.committee_id"
            " WHERE p.session = %s AND p.committee_id = %s AND p.rolled_back_at IS NULL"
            " ORDER BY p.published_at DESC LIMIT 2",
            (session, committee_id),
        ).fetchall()
        if not rows:
            raise RollbackError(f"{committee_id} has no live publication")
        if len(rows) < 2 or rows[1]["pruned_at"] is not None:
            raise RollbackError(f"{committee_id} has no retained earlier publication")
        latest, restored = rows[0]["run_id"], rows[1]["run_id"]
        now = transaction_time(conn)
        conn.execute(
            "UPDATE committee_publications SET rolled_back_at = %s"
            " WHERE session = %s AND committee_id = %s AND run_id = %s",
            (now, session, committee_id, latest),
        )
        conn.execute(
            "UPDATE committee_current cc SET run_id = rc.run_id, as_of = rc.as_of"
            " FROM run_committees rc WHERE rc.run_id = %s AND rc.committee_id = cc.committee_id"
            " AND cc.session = %s AND cc.committee_id = %s",
            (restored, session, committee_id),
        )
        upsert_committees(conn, session, restored, [committee_id])
        rebuild(conn, session, [committee_id], restored, now)
        events.withdraw(conn, session, committee_id, latest, now)
        return Rollback(committee_id, latest, restored)


@dataclass(frozen=True)
class PruneResult:
    committees_pruned: int
    runs_pruned: int
    runs_abandoned: int


def prune(conn: Connection, keep_publications: int, abandon_after_hours: int) -> PruneResult:
    """Drop detail rows no longer needed for display or rollback; keep the ledger rows."""
    if keep_publications < 1:
        raise ValueError("keep_publications must be at least 1")
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (PUBLISH_LOCK,))
        targets = conn.execute(
            "WITH retained AS ("
            "  SELECT run_id, committee_id FROM ("
            "    SELECT run_id, committee_id, row_number() OVER ("
            "      PARTITION BY session, committee_id ORDER BY published_at DESC) AS n"
            "    FROM committee_publications WHERE rolled_back_at IS NULL) p"
            "  WHERE n <= %s)"
            " UPDATE run_committees rc SET pruned_at = now() FROM runs r"
            " WHERE r.run_id = rc.run_id AND r.status = 'published' AND rc.pruned_at IS NULL"
            " AND (rc.run_id, rc.committee_id) NOT IN (SELECT run_id, committee_id FROM retained)"
            " RETURNING rc.run_id, rc.committee_id",
            (keep_publications,),
        ).fetchall()
        pairs = [(t["run_id"], t["committee_id"]) for t in targets]
        for table in ("tenures", "run_bills", "committee_outputs", "no_requirement_tenures"):
            conn.cursor().executemany(
                f"DELETE FROM {table} WHERE run_id = %s AND committee_id = %s", pairs
            )
        runs = conn.execute(
            "UPDATE runs r SET status = 'pruned' WHERE r.status = 'published' AND NOT EXISTS ("
            "  SELECT 1 FROM run_committees rc WHERE rc.run_id = r.run_id AND rc.pruned_at IS NULL)"
            " RETURNING run_id"
        ).fetchall()
        stale = conn.execute(
            "UPDATE runs SET status = 'abandoned' WHERE status = 'open'"
            " AND opened_at < now() - make_interval(hours => %s) RETURNING run_id",
            (abandon_after_hours,),
        ).fetchall()
        conn.execute(
            "DELETE FROM run_committees WHERE run_id = ANY(%s)", ([r["run_id"] for r in stale],)
        )
        return PruneResult(len(pairs), len(runs), len(stale))
