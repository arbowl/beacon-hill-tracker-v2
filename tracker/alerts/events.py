"""Alert events: written at publish (status changes, missed deadlines) and by the daily scan."""

from collections.abc import Sequence
from datetime import date, datetime
from uuid import UUID

from tracker.db import Connection

APPROACHING_DAYS = 7

# tenure_id is "<session>/<bill_id>:<committee_id>:<referral>", validated at ingest by the contract.
_BILL_ID = "split_part(split_part(lc.tenure_id, ':', 1), '/', 2)"

_STATUS_CHANGED = f"""
INSERT INTO alert_events (kind, session, tenure_id, bill_id, committee_id, run_id,
                          label_change_id, created_at)
SELECT 'status_changed', lc.session, lc.tenure_id, {_BILL_ID}, lc.committee_id, lc.run_id,
       lc.id, %(now)s
FROM label_changes lc
WHERE lc.session = %(session)s AND lc.committee_id = ANY(%(committees)s)
  AND lc.changed_at = %(now)s
ON CONFLICT DO NOTHING
"""

# A requirement that went from Provisional to Unmet with its due date already past.
_DEADLINE_MISSED = f"""
INSERT INTO alert_events (kind, session, tenure_id, bill_id, committee_id, run_id, requirement,
                          obligation_id, due, created_at)
SELECT 'deadline_missed', lc.session, lc.tenure_id, {_BILL_ID}, lc.committee_id, lc.run_id,
       r.requirement, r.obligation_id, r.due, %(now)s
FROM label_changes lc
CROSS JOIN LATERAL jsonb_to_recordset(lc.changed_requirements)
  AS x(requirement text, "from" text, "to" text)
JOIN requirements r ON r.run_id = lc.run_id AND r.tenure_id = lc.tenure_id
                   AND r.requirement = x.requirement::requirement
WHERE lc.session = %(session)s AND lc.committee_id = ANY(%(committees)s)
  AND lc.changed_at = %(now)s
  AND x."from" = 'Provisional' AND x."to" = 'Unmet'
  AND r.due < (%(now)s AT TIME ZONE 'America/New_York')::date
ON CONFLICT DO NOTHING
"""

_DEADLINE_APPROACHING = """
INSERT INTO alert_events (kind, session, tenure_id, bill_id, committee_id, run_id, requirement,
                          obligation_id, due, created_at)
SELECT 'deadline_approaching', ct.session, ct.tenure_id, ct.bill_id, ct.committee_id, ct.run_id,
       r.requirement, r.obligation_id, r.due, now()
FROM current_tenures ct
JOIN requirements r ON r.run_id = ct.run_id AND r.tenure_id = ct.tenure_id
WHERE r.status = 'Provisional' AND r.reason = 'not_yet_due'
  AND r.due BETWEEN %(today)s AND %(today)s + %(days)s
ON CONFLICT DO NOTHING
"""


def record_publish(conn: Connection, session: int, committees: Sequence[str], now: datetime) -> int:
    """Events for the label_changes written at transaction time now; returns how many."""
    params = {"session": session, "committees": list(committees), "now": now}
    changed = conn.execute(_STATUS_CHANGED, params).rowcount
    return changed + conn.execute(_DEADLINE_MISSED, params).rowcount


def withdraw(conn: Connection, session: int, committee_id: str, run_id: UUID, now: datetime) -> int:
    """Stop sending events that came from a rolled-back run; returns how many were still live."""
    return conn.execute(
        "UPDATE alert_events SET withdrawn_at = %s WHERE session = %s AND committee_id = %s"
        " AND run_id = %s AND withdrawn_at IS NULL",
        (now, session, committee_id, run_id),
    ).rowcount


def scan_deadlines(conn: Connection, today: date) -> int:
    """Events for live Provisional requirements due within 7 days; each obligation and due date
    produces one event however often the scan runs. Returns how many were new."""
    return conn.execute(_DEADLINE_APPROACHING, {"today": today, "days": APPROACHING_DAYS}).rowcount
