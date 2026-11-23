"""Publish rebuilds read models atomically; rollback and pruning keep history."""

from collections import Counter
from typing import Any
from uuid import uuid4

import pytest

from beacon_contract import v1
from tests.conftest import Pusher, bundle_dict
from tracker.db import Connection
from tracker.publish.lifecycle import RollbackError, prune, rollback


def _count(db: Connection, query: str, *params: Any) -> int:
    return db.execute(f"SELECT count(*) AS n FROM {query}", params).fetchone()["n"]


def _resolve(bundle: dict[str, Any]) -> str:
    """Mark the only Unmet requirement of one Non-Compliant tenure as Met; returns its id."""
    for t in bundle["tenures"]:
        unmet = [r for r in t["requirements"] if r["status"] == "Unmet"]
        if t["label"] == "Non-Compliant" and len(unmet) == 1:
            unmet[0]["status"] = "Met"
            t["label"] = v1.roll_up(v1.RequirementStatus(r["status"]) for r in t["requirements"])
            return t["tenure_id"]
    raise AssertionError("no tenure with exactly one Unmet requirement")


def _rerun(bundle: dict[str, Any]) -> dict[str, Any]:
    return bundle | {"evaluation_run_id": str(uuid4())}


def test_first_publish_builds_read_models(pusher: Pusher, db: Connection):
    j19, j45 = bundle_dict("J19"), bundle_dict("J45")
    run_id = pusher.push({"J19": j19, "J45": j45})
    tenures = j19["tenures"] + j45["tenures"]
    assert _count(db, "current_tenures") == len(tenures)
    labels = Counter(t["label"] for t in tenures)
    stats = db.execute("SELECT * FROM committee_stats WHERE committee_id = '*'").fetchone()
    assert (stats["total"], stats["compliant"], stats["provisional"], stats["non_compliant"]) == (
        len(tenures),
        labels["Compliant"],
        labels["Provisional"],
        labels["Non-Compliant"],
    )
    assert stats["bills_total"] == len({t["bill_id"] for t in tenures})
    expected_rate = round(100 * (labels["Compliant"] + labels["Provisional"]) / len(tenures), 2)
    assert float(stats["compliance_rate"]) == expected_rate
    assert stats["funnel"][0] == len(tenures) and stats["funnel"][-1] == labels["Compliant"]
    assert stats["funnel"] == sorted(stats["funnel"], reverse=True)
    assert _count(db, "committee_stats") == 4
    assert _count(db, "label_changes WHERE from_label IS NULL") == len(tenures)
    assert _count(db, "snapshots WHERE run_id = %s", run_id) == 4
    assert _count(db, "committees") == 2


def test_absent_requirements_and_unobserved_referrals(pusher: Pusher, db: Connection):
    j45 = bundle_dict("J45")
    pusher.push({"J45": j45})
    unobserved = db.execute(
        "SELECT * FROM current_tenures WHERE tenure_id = '194/H4319:J45:unobserved'"
    ).fetchone()
    assert unobserved["referral_observed"] is False and unobserved["referred_on"] is None
    assert unobserved["hearing_notice"] is None and unobserved["report_out"] is None
    assert unobserved["label"] == "Provisional"
    only_report = next(
        t
        for t in j45["tenures"]
        if [r["requirement_id"] for r in t["requirements"]] == ["report_out_deadline"]
    )
    row = db.execute(
        "SELECT * FROM current_tenures WHERE tenure_id = %s", (only_report["tenure_id"],)
    ).fetchone()
    assert row["hearing_notice"] is None and row["report_out"] is not None


def test_identical_republish_changes_nothing(pusher: Pusher, db: Connection):
    j45 = bundle_dict("J45")
    pusher.push({"J45": j45})
    before = db.execute("SELECT * FROM committee_stats ORDER BY committee_id").fetchall()
    changes = _count(db, "label_changes")
    pusher.push({"J45": _rerun(j45)})
    after = db.execute("SELECT * FROM committee_stats ORDER BY committee_id").fetchall()
    assert _count(db, "label_changes") == changes
    assert [{**r, "as_of": None} for r in before] == [{**r, "as_of": None} for r in after]
    assert _count(db, "snapshots WHERE committee_id = 'J45'") == 2


def test_change_is_recorded_and_other_committees_untouched(pusher: Pusher, db: Connection):
    first = pusher.push({"J19": bundle_dict("J19"), "J45": bundle_dict("J45")})
    changed = _rerun(bundle_dict("J45"))
    tenure_id = _resolve(changed)
    second = pusher.push({"J45": changed})
    rows = db.execute("SELECT * FROM label_changes WHERE run_id = %s", (second,)).fetchall()
    assert [r["tenure_id"] for r in rows] == [tenure_id]
    assert rows[0]["from_label"] == "Non-Compliant"
    assert [c["from"] for c in rows[0]["changed_requirements"]] == ["Unmet"]
    assert _count(db, "current_tenures WHERE committee_id = 'J19' AND run_id = %s", first) == 791
    j45_stats = db.execute("SELECT * FROM committee_stats WHERE committee_id = 'J45'").fetchone()
    assert j45_stats["non_compliant"] == 31


def test_rollback_restores_previous_publication(pusher: Pusher, db: Connection):
    first = pusher.push({"J45": bundle_dict("J45")})
    changed = _rerun(bundle_dict("J45"))
    tenure_id = _resolve(changed)
    second = pusher.push({"J45": changed})
    result = rollback(db, 194, "J45")
    assert (result.rolled_back_run, result.restored_run) == (second, first)
    assert _count(db, "current_tenures WHERE run_id = %s", first) == 67
    label = db.execute(
        "SELECT label FROM current_tenures WHERE tenure_id = %s", (tenure_id,)
    ).fetchone()["label"]
    assert label == "Non-Compliant"
    reverse = db.execute(
        "SELECT from_label, to_label FROM label_changes WHERE run_id = %s AND tenure_id = %s",
        (first, tenure_id),
    ).fetchall()
    assert [(r["from_label"], r["to_label"]) for r in reverse][-1] == ("Compliant", "Non-Compliant")
    with pytest.raises(RollbackError):
        rollback(db, 194, "J45")


def test_prune_keeps_configured_publications(pusher: Pusher, db: Connection):
    runs = [pusher.push({"J45": _rerun(bundle_dict("J45"))}) for _ in range(3)]
    assert _count(db, "tenures WHERE run_id = %s", runs[0]) == 0
    assert (
        db.execute("SELECT status FROM runs WHERE run_id = %s", (runs[0],)).fetchone()["status"]
        == "pruned"
    )
    assert all(_count(db, "tenures WHERE run_id = %s", r) == 67 for r in runs[1:])
    assert _count(db, "run_committees") == 3
    result = prune(db, keep_publications=1, abandon_after_hours=48)
    assert (result.committees_pruned, result.runs_pruned) == (1, 1)
    with pytest.raises(RollbackError, match="no retained"):
        rollback(db, 194, "J45")
    assert _count(db, "snapshots WHERE committee_id = 'J45'") == 3


def test_prune_counts_committees_separately(pusher: Pusher, db: Connection):
    shared = pusher.push({"J19": bundle_dict("J19"), "J45": bundle_dict("J45")})
    for _ in range(2):
        pusher.push({"J45": _rerun(bundle_dict("J45"))})
    assert _count(db, "tenures WHERE run_id = %s AND committee_id = 'J45'", shared) == 0
    assert _count(db, "tenures WHERE run_id = %s AND committee_id = 'J19'", shared) == 791
    assert (
        db.execute("SELECT status FROM runs WHERE run_id = %s", (shared,)).fetchone()["status"]
        == "published"
    )


def test_prune_abandons_stale_open_pushes(pusher: Pusher, db: Connection):
    push_id = uuid4()
    pusher.open(push_id, ["J45"])
    db.execute(
        "UPDATE runs SET opened_at = now() - interval '3 days' WHERE run_id = %s", (push_id,)
    )
    assert prune(db, 2, 48).runs_abandoned == 1
    assert (
        db.execute("SELECT status FROM runs WHERE run_id = %s", (push_id,)).fetchone()["status"]
        == "abandoned"
    )
