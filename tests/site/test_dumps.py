"""Data dumps: built from the live data, faithful to what was published, served behind sign-in."""

import csv
import gzip
import hashlib
import io
import json
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from beacon_contract import v1
from tests.conftest import Pusher, bundle_dict, make_user
from tracker.auth import sessions
from tracker.auth.current import COOKIE
from tracker.db import Connection
from tracker.enums import UserRole
from tracker.publish import dumps
from tracker.publish.lifecycle import rollback


def _refresh(db: Connection, keep_recent: int = 30, keep_monthly: bool = True) -> None:
    dumps.refresh(db, 194, keep_recent=keep_recent, keep_monthly=keep_monthly)


@pytest.fixture
def published(pusher: Pusher, db: Connection) -> None:
    pusher.push({"J19": bundle_dict("J19"), "J45": bundle_dict("J45")})
    _refresh(db)


@pytest.fixture
def reader(client: FlaskClient, db: Connection) -> FlaskClient:
    user_id = make_user(db, "reader@example.org", UserRole.USER)
    client.set_cookie(COOKIE, sessions.create(db, user_id, 1))
    return client


def _download(client: FlaskClient, fmt: str) -> tuple[bytes, Any]:
    response = client.get(f"/watchlist/downloads/{fmt}")
    assert response.status_code == 200, response.status_code
    assert response.mimetype == "application/gzip"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert f"beacon-hill-tracker-194-current.{fmt}" in response.headers["Content-Disposition"]
    data = response.get_data()
    assert hashlib.sha256(data).hexdigest() == response.headers["X-Tracker-SHA256"]
    return data, response.headers


def _normalized(bundle: v1.CommitteeBundle) -> dict[str, Any]:
    """The bundle with every list in a canonical order; order carries no meaning in the contract."""
    data = bundle.model_dump(mode="json")
    for t in data["tenures"]:
        t["requirements"].sort(key=lambda r: r["requirement_id"])
    data["tenures"].sort(key=lambda t: t["tenure_id"])
    data["bills"].sort(key=lambda b: b["bill_id"])
    data["clauses"].sort(key=lambda c: c["text_hash"])
    data["committee_output"].sort(key=lambda o: o["bill_id"])
    return data


def test_json_dump_reproduces_the_published_bundles(reader: FlaskClient, published: None):
    data, headers = _download(reader, "json.gz")
    document = json.loads(gzip.decompress(data))
    assert document["session"] == 194 and document["schema_version"] == v1.SCHEMA_VERSION
    assert document["as_of"] == headers["X-Tracker-As-Of"]
    served = {b["committee"]["committee_id"]: b for b in document["bundles"]}
    assert sorted(served) == ["J19", "J45"]
    for committee_id, raw in served.items():
        pushed = v1.CommitteeBundle.model_validate(bundle_dict(committee_id))
        assert _normalized(v1.CommitteeBundle.model_validate(raw)) == _normalized(pushed)


def test_csv_dump_has_one_row_per_live_segment(
    reader: FlaskClient, published: None, db: Connection
):
    data, _ = _download(reader, "csv.gz")
    rows = list(csv.DictReader(io.StringIO(gzip.decompress(data).decode())))
    live = db.execute("SELECT tenure_id, label FROM current_tenures").fetchall()
    assert {r["tenure_id"]: r["label"] for r in rows} == {r["tenure_id"]: r["label"] for r in live}
    h10 = next(r for r in rows if r["tenure_id"] == "194/H10:J19:2025-02-27")
    assert h10["report_out_deadline_status"] == "Unmet"
    assert h10["report_out_deadline_due"] == "2026-01-03"
    assert h10["committee_name"] == "Joint Committee on the Judiciary"
    unobserved = next(r for r in rows if r["tenure_id"].endswith(":unobserved"))
    assert unobserved["referral_observed"] == "f" and unobserved["hearing_notice_status"] == ""


def test_downloads_need_sign_in(client: FlaskClient, published: None):
    response = client.get("/watchlist/downloads/csv.gz")
    assert response.status_code == 302
    assert response.headers["Location"] == "/signin?next=/watchlist/downloads/csv.gz"
    assert client.get("/watchlist/downloads/tar.gz").status_code == 404


def test_watchlist_lists_downloads_with_their_hashes(
    reader: FlaskClient, published: None, db: Connection
):
    html = reader.get("/watchlist").get_data(as_text=True)
    for row in db.execute("SELECT format, sha256 FROM data_dumps"):
        assert f"current.{row['format']}" in html and row["sha256"] in html
    assert "/watchlist/downloads/json.gz" in html


def test_same_data_gives_same_file_and_rollback_reuses_it(
    pusher: Pusher, db: Connection, published: None
):
    first = {d.format: d for d in dumps.current(db, 194)}
    rebuilt = dumps.build_csv(db, 194)
    assert hashlib.sha256(rebuilt).hexdigest() == first["csv.gz"].sha256

    j19 = bundle_dict("J19")
    j19["evaluation_run_id"] = "00000000-0000-4000-8000-000000000001"
    pusher.push({"J19": j19})
    _refresh(db)
    second = {d.format: d for d in dumps.current(db, 194)}
    assert second["json.gz"].id != first["json.gz"].id
    assert db.execute("SELECT count(*) AS n FROM data_dumps").fetchone()["n"] == 4

    rollback(db, 194, "J19")
    _refresh(db)
    restored = {d.format: d for d in dumps.current(db, 194)}
    assert restored["json.gz"].id == first["json.gz"].id
    assert restored["csv.gz"].sha256 == first["csv.gz"].sha256
    assert db.execute("SELECT count(*) AS n FROM data_dumps").fetchone()["n"] == 4


def test_retention_keeps_the_configured_number(pusher: Pusher, db: Connection, published: None):
    for n in range(2):
        j45 = bundle_dict("J45")
        j45["evaluation_run_id"] = f"00000000-0000-4000-8000-00000000000{n + 2}"
        pusher.push({"J45": j45})
        _refresh(db)
    assert db.execute("SELECT count(*) AS n FROM data_dumps").fetchone()["n"] == 6
    _refresh(db, keep_recent=1, keep_monthly=False)
    assert db.execute("SELECT count(*) AS n FROM data_dumps").fetchone()["n"] == 2
    db.execute("UPDATE data_dumps SET built_at = built_at - interval '40 days'")
    j45["evaluation_run_id"] = "00000000-0000-4000-8000-000000000009"
    pusher.push({"J45": j45})
    _refresh(db, keep_recent=1, keep_monthly=True)
    assert db.execute("SELECT count(*) AS n FROM data_dumps").fetchone()["n"] == 4


def test_refresh_is_a_no_op_when_current(pusher: Pusher, db: Connection):
    pusher.push({"J45": bundle_dict("J45")})
    assert db.execute("SELECT count(*) AS n FROM data_dumps").fetchone()["n"] == 0
    _refresh(db)
    before = db.execute("SELECT id, current_since FROM data_dumps ORDER BY id").fetchall()
    _refresh(db)
    assert db.execute("SELECT id, current_since FROM data_dumps ORDER BY id").fetchall() == before


def test_rebuild_command(app: Flask, db: Connection, published: None):
    db.execute("DELETE FROM data_dumps")
    result = app.test_cli_runner().invoke(args=["dumps", "rebuild"])
    assert result.exit_code == 0, result.output
    assert "csv.gz" in result.output and "json.gz" in result.output
