"""Rebuilds read models from whatever committee_current points at.

Called inside the publish or rollback transaction, after committee_current is updated.
"""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from tracker.db import Connection
from tracker.publish import insights

# Live rows for the named committees, built from their current runs.
_LIVE = """
SELECT t.session, t.tenure_id, t.run_id, t.bill_id, substring(t.bill_id FROM 2)::int AS bill_num,
       b.title, b.url AS bill_url, t.committee_id, rc.name AS committee_name, t.referred_on,
       t.label, q.hearing_notice, q.summary_posted, q.votes_posted, q.report_out,
       t.referred_on IS NOT NULL AS referral_observed, q.report_due, q.next_due,
       CASE t.label WHEN 'Non-Compliant' THEN 0 WHEN 'Provisional' THEN 1 ELSE 2 END AS severity,
       q.hearing_notice_detail, q.summary_posted_detail, q.votes_posted_detail,
       q.report_out_detail, q.tenure_observed_detail
FROM committee_current cc
JOIN run_committees rc ON rc.run_id = cc.run_id AND rc.committee_id = cc.committee_id
JOIN tenures t ON t.run_id = cc.run_id AND t.committee_id = cc.committee_id
JOIN run_bills b ON b.run_id = t.run_id AND b.committee_id = t.committee_id
                AND b.bill_id = t.bill_id
CROSS JOIN LATERAL (
  SELECT max(status) FILTER (WHERE requirement = 'hearing_notice') AS hearing_notice,
         max(status) FILTER (WHERE requirement = 'summary_posted') AS summary_posted,
         max(status) FILTER (WHERE requirement = 'votes_posted') AS votes_posted,
         max(status) FILTER (WHERE requirement = 'report_out_deadline') AS report_out,
         max(due) FILTER (WHERE requirement = 'report_out_deadline') AS report_due,
         min(due) FILTER (WHERE status = 'Provisional') AS next_due,
         max(detail) FILTER (WHERE requirement = 'hearing_notice') AS hearing_notice_detail,
         max(detail) FILTER (WHERE requirement = 'summary_posted') AS summary_posted_detail,
         max(detail) FILTER (WHERE requirement = 'votes_posted') AS votes_posted_detail,
         max(detail) FILTER (WHERE requirement = 'report_out_deadline') AS report_out_detail,
         max(detail) FILTER (WHERE requirement = 'tenure_observed') AS tenure_observed_detail
  FROM requirements r WHERE r.run_id = t.run_id AND r.tenure_id = t.tenure_id) q
WHERE cc.session = %(session)s AND cc.committee_id = ANY(%(committees)s)
"""

_COLUMNS = (
    "session, tenure_id, run_id, bill_id, bill_num, title, bill_url, committee_id,"
    " committee_name, referred_on, label, hearing_notice, summary_posted, votes_posted,"
    " report_out, referral_observed, report_due, next_due, severity, hearing_notice_detail,"
    " summary_posted_detail, votes_posted_detail, report_out_detail, tenure_observed_detail"
)

_RECORD_CHANGES = f"""
WITH new AS ({_LIVE}),
old AS (SELECT * FROM current_tenures
        WHERE session = %(session)s AND committee_id = ANY(%(committees)s))
INSERT INTO label_changes (session, tenure_id, committee_id, run_id, changed_at,
                           from_label, to_label, changed_requirements)
SELECT %(session)s, coalesce(n.tenure_id, o.tenure_id), cc.committee_id, cc.run_id, %(now)s,
       o.label, n.label,
       (SELECT coalesce(jsonb_agg(jsonb_build_object('requirement', x.req, 'from', x.f, 'to', x.t)),
                        '[]'::jsonb)
        FROM (VALUES ('hearing_notice', o.hearing_notice, n.hearing_notice),
                     ('summary_posted', o.summary_posted, n.summary_posted),
                     ('votes_posted', o.votes_posted, n.votes_posted),
                     ('report_out_deadline', o.report_out, n.report_out)) x(req, f, t)
        WHERE x.f IS DISTINCT FROM x.t)
FROM new n FULL JOIN old o ON o.tenure_id = n.tenure_id
JOIN committee_current cc ON cc.session = %(session)s
                         AND cc.committee_id = coalesce(n.committee_id, o.committee_id)
WHERE n.tenure_id IS NULL OR o.tenure_id IS NULL
   OR o.label <> n.label
   OR (o.hearing_notice, o.summary_posted, o.votes_posted, o.report_out)
      IS DISTINCT FROM (n.hearing_notice, n.summary_posted, n.votes_posted, n.report_out)
"""

_STATS = """
INSERT INTO committee_stats (
  session, committee_id, as_of, total, compliant, provisional, non_compliant,
  bills_total, bills_with_violation, hearing_notice_total, hearing_notice_unmet, summary_unmet,
  votes_unmet, report_out_unmet, due_next_14_days, funnel, compliance_rate, decided_rate)
SELECT s.*,
       round(100.0 * (s.compliant + s.provisional) / nullif(s.total, 0), 2),
       round(100.0 * s.compliant / nullif(s.compliant + s.non_compliant, 0), 2)
FROM (
  SELECT ct.session,
         CASE WHEN GROUPING(ct.committee_id) = 1 THEN '*' ELSE ct.committee_id END
           AS committee_id,
         max(cc.as_of) AS as_of,
         count(*) AS total,
         count(*) FILTER (WHERE ct.label = 'Compliant') AS compliant,
         count(*) FILTER (WHERE ct.label = 'Provisional') AS provisional,
         count(*) FILTER (WHERE ct.label = 'Non-Compliant') AS non_compliant,
         count(DISTINCT ct.bill_id) AS bills_total,
         count(DISTINCT ct.bill_id) FILTER (WHERE ct.label = 'Non-Compliant')
           AS bills_with_violation,
         count(*) FILTER (WHERE ct.hearing_notice IS NOT NULL) AS hearing_notice_total,
         count(*) FILTER (WHERE ct.hearing_notice = 'Unmet') AS hearing_notice_unmet,
         count(*) FILTER (WHERE ct.summary_posted = 'Unmet') AS summary_unmet,
         count(*) FILTER (WHERE ct.votes_posted = 'Unmet') AS votes_unmet,
         count(*) FILTER (WHERE ct.report_out = 'Unmet') AS report_out_unmet,
         count(*) FILTER (WHERE ct.next_due >= d.today AND ct.next_due < d.today + 14)
           AS due_next_14_days,
         ARRAY[
           count(*),
           count(*) FILTER (WHERE ct.referral_observed AND p.notice),
           count(*) FILTER (WHERE ct.referral_observed AND p.notice AND p.summary),
           count(*) FILTER (WHERE ct.referral_observed AND p.notice AND p.summary AND p.votes),
           count(*) FILTER (WHERE ct.referral_observed AND p.notice AND p.summary AND p.votes
                                  AND p.report)
         ]::int[] AS funnel
  FROM current_tenures ct
  JOIN committee_current cc ON cc.session = ct.session AND cc.committee_id = ct.committee_id
  CROSS JOIN (SELECT (%(now)s AT TIME ZONE 'America/New_York')::date AS today) d
  CROSS JOIN LATERAL (
    SELECT coalesce(ct.hearing_notice, 'Met') = 'Met' AS notice,
           coalesce(ct.summary_posted, 'Met') = 'Met' AS summary,
           coalesce(ct.votes_posted, 'Met') = 'Met' AS votes,
           coalesce(ct.report_out, 'Met') = 'Met' AS report) p
  WHERE ct.session = %(session)s
  GROUP BY GROUPING SETS ((ct.session, ct.committee_id), (ct.session))
) s
"""

_SNAPSHOT = """
INSERT INTO snapshots (session, committee_id, taken_at, run_id, total, compliant, provisional,
                       non_compliant)
SELECT s.session, s.committee_id, %(now)s, coalesce(cc.run_id, %(run_id)s),
       s.total, s.compliant, s.provisional, s.non_compliant
FROM committee_stats s
LEFT JOIN committee_current cc ON cc.session = s.session AND cc.committee_id = s.committee_id
WHERE s.session = %(session)s AND (s.committee_id = ANY(%(committees)s) OR s.committee_id = '*')
"""


def rebuild(
    conn: Connection, session: int, committees: Sequence[str], run_id: UUID, now: datetime
) -> None:
    """Record changes, then replace live rows, stats, and snapshots for these committees.

    run_id is the run being published (or restored), recorded on the session snapshot.
    """
    params = {"session": session, "committees": list(committees), "now": now, "run_id": run_id}
    conn.execute(_RECORD_CHANGES, params)
    conn.execute(
        "DELETE FROM current_tenures WHERE session = %(session)s"
        " AND committee_id = ANY(%(committees)s)",
        params,
    )
    conn.execute(f"INSERT INTO current_tenures ({_COLUMNS}) {_LIVE}", params)
    conn.execute("DELETE FROM committee_stats WHERE session = %(session)s", params)
    conn.execute(_STATS, params)
    conn.execute(_SNAPSHOT, params)
    insights.regenerate(conn, session, run_id, now)
