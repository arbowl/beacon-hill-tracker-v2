"""Every published figure is reproducible from the ingested rows by one query."""

import re
from datetime import timedelta
from decimal import Decimal

import pytest
from flask.testing import FlaskClient

from tests.conftest import Pusher, bundle_dict
from tracker.db import Connection

# Recomputes committee_stats from raw tenures and requirements of the live runs, per committee
# chamber ('J'), and for the session ('*'), without using current_tenures.
REPRODUCE = """
WITH live AS (
  SELECT t.committee_id, left(t.committee_id, 1) AS chamber, t.bill_id, t.label,
         t.referred_on IS NOT NULL AS observed,
         max(r.status) FILTER (WHERE r.requirement = 'hearing_notice') AS notice,
         max(r.status) FILTER (WHERE r.requirement = 'summary_posted') AS summary,
         max(r.status) FILTER (WHERE r.requirement = 'votes_posted') AS votes,
         max(r.status) FILTER (WHERE r.requirement = 'report_out_deadline') AS report
  FROM committee_current cc
  JOIN tenures t ON t.run_id = cc.run_id AND t.committee_id = cc.committee_id
  JOIN requirements r ON r.run_id = t.run_id AND r.tenure_id = t.tenure_id
  GROUP BY t.committee_id, t.tenure_id, t.bill_id, t.label, t.referred_on)
SELECT coalesce(committee_id, chamber, '*') AS committee_id,
       count(*) AS total,
       count(*) FILTER (WHERE label = 'Compliant') AS compliant,
       count(*) FILTER (WHERE label = 'Provisional') AS provisional,
       count(*) FILTER (WHERE label = 'Non-Compliant') AS non_compliant,
       count(DISTINCT bill_id) AS bills_total,
       count(DISTINCT bill_id) FILTER (WHERE label = 'Non-Compliant') AS bills_with_violation,
       count(*) FILTER (WHERE report = 'Unmet') AS report_out_unmet,
       count(*) FILTER (WHERE notice = 'Unmet') AS hearing_notice_unmet,
       ARRAY[count(*),
             count(*) FILTER (WHERE observed AND coalesce(notice, 'Met') = 'Met'),
             count(*) FILTER (WHERE observed AND coalesce(notice, 'Met') = 'Met'
                                    AND coalesce(summary, 'Met') = 'Met'),
             count(*) FILTER (WHERE observed AND coalesce(notice, 'Met') = 'Met'
                                    AND coalesce(summary, 'Met') = 'Met'
                                    AND coalesce(votes, 'Met') = 'Met'),
             count(*) FILTER (WHERE observed AND coalesce(notice, 'Met') = 'Met'
                                    AND coalesce(summary, 'Met') = 'Met'
                                    AND coalesce(votes, 'Met') = 'Met'
                                    AND coalesce(report, 'Met') = 'Met')]::int[] AS funnel,
       round(100.0 * count(*) FILTER (WHERE label <> 'Non-Compliant') / count(*), 2)
         AS compliance_rate,
       round(100.0 * count(*) FILTER (WHERE label = 'Compliant')
             / nullif(count(*) FILTER (WHERE label <> 'Provisional'), 0), 2) AS decided_rate
FROM live GROUP BY GROUPING SETS ((committee_id), (chamber), ())
"""

BANNED = re.compile(
    r"\b(best|worst|leads?|lags?|top|bottom|grade|score|failed|violated|we|our|us)\b", re.I
)


@pytest.fixture
def published(pusher: Pusher) -> None:
    pusher.push({"J19": bundle_dict("J19"), "J45": bundle_dict("J45")})


def test_stats_reproduce_from_raw_rows(published: None, db: Connection):
    expected = {r["committee_id"]: r for r in db.execute(REPRODUCE)}
    actual = {r["committee_id"]: r for r in db.execute("SELECT * FROM committee_stats")}
    assert expected.keys() == actual.keys() == {"*", "J", "J19", "J45"}
    for committee_id, row in expected.items():
        for column, value in row.items():
            assert actual[committee_id][column] == value, (committee_id, column)


def test_dashboard_shows_the_stats(published: None, client: FlaskClient, db: Connection):
    stats = db.execute("SELECT * FROM committee_stats WHERE committee_id = '*'").fetchone()
    html = client.get("/").get_data(as_text=True)
    assert f"{stats['compliance_rate']:.1f}<small>%</small>" in html
    assert f"{stats['non_compliant']:,}</p>" in html
    assert f"in {stats['bills_with_violation']:,} bills" in html
    funnel = ", ".join(str(n) for n in stats["funnel"])
    assert f'"values": [{funnel}]' in html


def test_insights_are_backed_and_neutral(published: None, client: FlaskClient, db: Connection):
    rows = db.execute("SELECT * FROM insights ORDER BY position").fetchall()
    assert {r["kind"] for r in rows} == {"deadline_count", "bills_affected"}
    j19 = db.execute("SELECT * FROM committee_stats WHERE committee_id = 'J19'").fetchone()
    deadline = next(r for r in rows if r["kind"] == "deadline_count")
    assert deadline["body"].startswith(f"{j19['report_out_unmet']} bill segments in the Joint")
    assert deadline["href"] == "/?committee=J19&status=non-compliant"
    for r in rows:
        assert not BANNED.search(r["body"]), r["body"]
        assert client.get(r["href"]).status_code == 200


def test_deltas_and_weekly_change(
    published: None, pusher: Pusher, client: FlaskClient, db: Connection
):
    assert 'class="delta"' not in client.get("/").get_data(as_text=True)
    db.execute(
        "UPDATE snapshots SET taken_at = taken_at - %s, compliant = 0, provisional = 0",
        (timedelta(days=8),),
    )
    pusher.push(
        {"J45": bundle_dict("J45") | {"evaluation_run_id": "9" * 8 + "-0000-4000-8000-" + "0" * 12}}
    )
    html = client.get("/").get_data(as_text=True)
    stats = db.execute(
        "SELECT compliance_rate FROM committee_stats WHERE committee_id = '*'"
    ).fetchone()
    expected = Decimal(stats["compliance_rate"]).quantize(Decimal("0.1"))
    assert f'&#9650; {expected} pts <span class="period">7d</span>' in html
    weekly = db.execute("SELECT body FROM insights WHERE kind = 'weekly_change'").fetchone()
    assert weekly is not None and "changed by +" in weekly["body"]
    assert not BANNED.search(weekly["body"])
