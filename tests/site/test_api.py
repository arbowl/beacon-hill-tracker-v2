"""Public API: keyed, rate-limited, and returning the dashboard's own figures."""

import re
from typing import Any

import pytest
from flask import Flask
from flask.testing import FlaskClient

from beacon_contract import v1
from tests.conftest import Pusher, bundle_dict, make_user
from tracker.auth import sessions
from tracker.auth.current import COOKIE
from tracker.auth.keys import create_key
from tracker.db import Connection
from tracker.enums import KeyScope, UserRole


@pytest.fixture
def published(pusher: Pusher) -> None:
    pusher.push({"J19": bundle_dict("J19"), "J45": bundle_dict("J45")})


@pytest.fixture
def read_key(db: Connection) -> str:
    user_id = make_user(db, "reader@example.org", UserRole.USER)
    return create_key(db, user_id, KeyScope.READ, "test").secret


def _get(client: FlaskClient, key: str, url: str, status: int = 200) -> Any:
    response = client.get(url, headers={"Authorization": f"Bearer {key}"})
    assert response.status_code == status, (url, response.get_data(as_text=True))
    assert response.mimetype == "application/json"
    return response.get_json()


def test_requests_need_a_live_read_key(
    client: FlaskClient, db: Connection, published: None, read_key: str, ingest_key: str
):
    for headers in ({}, {"Authorization": "Bearer bht_read_nope"}, {"Authorization": read_key}):
        response = client.get("/api/v1/stats", headers=headers)
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"].startswith("Bearer")
        assert response.get_json()["error"]["code"] == "unauthorized"
    _get(client, ingest_key, "/api/v1/stats", 401)
    _get(client, read_key, "/api/v1/stats")
    db.execute("UPDATE api_keys SET revoked_at = now() WHERE scope = 'read'")
    _get(client, read_key, "/api/v1/stats", 401)


def test_stats_match_the_dashboard(
    client: FlaskClient, db: Connection, published: None, read_key: str
):
    session = _get(client, read_key, "/api/v1/stats")
    row = db.execute("SELECT * FROM committee_stats WHERE committee_id = '*'").fetchone()
    assert session["committee_id"] is None and session["segments"] == 858 == row["total"]
    assert session["labels"] == {
        "compliant": row["compliant"],
        "provisional": row["provisional"],
        "non_compliant": row["non_compliant"],
    }
    assert session["compliance_rate"] == float(row["compliance_rate"])
    html = client.get("/").get_data(as_text=True)
    assert f"{session['compliance_rate']:.1f}" in html

    j45 = _get(client, read_key, "/api/v1/stats?committee=J45")
    assert j45["committee_id"] == "J45" and j45["segments"] == 67
    _get(client, read_key, "/api/v1/stats?committee=J99", 404)
    _get(client, read_key, "/api/v1/stats?committee=nope", 400)
    _get(client, read_key, "/api/v1/stats?colour=red", 400)


def test_segments_page_like_the_dashboard(client: FlaskClient, published: None, read_key: str):
    first = _get(client, read_key, "/api/v1/segments?committee=J45&status=compliant")
    assert first["total"] == 34 and len(first["segments"]) == 34 and first["next_cursor"] is None
    assert "Showing 34 of 34" in client.get("/?committee=J45&status=compliant").get_data(
        as_text=True
    )
    assert {s["label"] for s in first["segments"]} == {"Compliant"}

    seen: list[str] = []
    url = "/api/v1/segments?committee=J19&sort=bill"
    while url:
        page = _get(client, read_key, url)
        assert page["total"] == 791
        seen += [s["tenure_id"] for s in page["segments"]]
        url = (
            f"/api/v1/segments?committee=J19&sort=bill&after={page['next_cursor']}"
            if page["next_cursor"]
            else ""
        )
    assert len(seen) == len(set(seen)) == 791

    found = _get(client, read_key, "/api/v1/segments?q=h.4319")["segments"]
    assert [s["tenure_id"] for s in found] == ["194/H4319:J45:unobserved"]
    assert found[0]["referral_observed"] is False and found[0]["hearing_notice"] is None
    _get(client, read_key, "/api/v1/segments?after=zzz", 400)


def test_committees(client: FlaskClient, published: None, read_key: str):
    listing = _get(client, read_key, "/api/v1/committees")
    assert sorted(c["committee_id"] for c in listing["committees"]) == ["J19", "J45"]
    j19 = _get(client, read_key, "/api/v1/committees/J19")
    assert j19["committee"]["name"] == "Joint Committee on the Judiciary"
    assert j19["committee"]["url"].endswith("/?committee=J19")
    assert j19["stats"]["segments"] == 791
    _get(client, read_key, "/api/v1/committees/J99", 404)


def test_bill_carries_full_results_in_contract_form(
    client: FlaskClient, published: None, read_key: str
):
    bill = _get(client, read_key, "/api/v1/bills/194/h.10")
    assert bill["bill_id"] == "H10" and bill["url"].endswith("/bills/194/H10")
    (segment,) = bill["segments"]
    tenure = v1.Tenure.model_validate(segment["tenure"])
    pushed = next(t for t in bundle_dict("J19")["tenures"] if t["tenure_id"] == tenure.tenure_id)
    by_requirement = {r.requirement_id: r for r in v1.Tenure.model_validate(pushed).requirements}
    assert {r.requirement_id: r for r in tenure.requirements} == by_requirement
    assert tenure.model_copy(update={"requirements": ()}) == v1.Tenure.model_validate(
        pushed
    ).model_copy(update={"requirements": ()})
    cited = {c.text_hash for r in tenure.requirements for c in r.clauses}
    assert cited == {c["text_hash"] for c in bill["clauses"]}
    _get(client, read_key, "/api/v1/bills/194/H999999", 404)
    _get(client, read_key, "/api/v1/bills/194/nonsense", 404)


def test_rate_limit(app: Flask, client: FlaskClient, published: None, read_key: str):
    app.config["TRACKER_SETTINGS"] = app.config["TRACKER_SETTINGS"].model_copy(
        update={"api_requests_per_minute": 3}
    )
    for _ in range(3):
        _get(client, read_key, "/api/v1/committees")
    response = client.get("/api/v1/committees", headers={"Authorization": f"Bearer {read_key}"})
    assert response.status_code == 429 and 1 <= int(response.headers["Retry-After"]) <= 60
    assert response.get_json()["error"]["code"] == "rate_limited"


def test_openapi_document_is_public_and_complete(client: FlaskClient):
    doc = client.get("/api/v1/openapi.json").get_json()
    assert doc["openapi"].startswith("3.1")
    assert set(doc["paths"]) == {
        "/api/v1/stats",
        "/api/v1/committees",
        "/api/v1/committees/{committee_id}",
        "/api/v1/segments",
        "/api/v1/bills/{session}/{bill_id}",
    }
    for path in doc["paths"].values():
        ref = path["get"]["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        assert ref.rsplit("/", 1)[1] in doc["components"]["schemas"]
    text = str(doc).lower()
    assert not re.search(r"\b(we|our|us|best|worst|rank|grade|score)\b", text)


def test_unknown_api_paths_answer_in_json(client: FlaskClient, read_key: str):
    body = _get(client, read_key, "/api/v1/nowhere", 404)
    assert body["error"]["code"] == "not_found"


def test_users_manage_read_keys_on_the_watchlist(
    client: FlaskClient, db: Connection, published: None
):
    user_id = make_user(db, "owner@example.org", UserRole.USER)
    client.set_cookie(COOKIE, sessions.create(db, user_id, 1))
    page = client.get("/watchlist").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page)[1]

    created = client.post("/watchlist/keys", data={"csrf_token": token, "name": "Newsroom"})
    assert created.status_code == 200 and created.headers["Cache-Control"] == "private, no-store"
    secret = re.search(r'id="new-key">(bht_read_[^<]+)<', created.get_data(as_text=True))[1]
    assert secret not in client.get("/watchlist").get_data(as_text=True)
    _get(client, secret, "/api/v1/committees")
    assert "Used " in client.get("/watchlist").get_data(as_text=True)

    key_id = db.execute("SELECT id FROM api_keys WHERE name = 'Newsroom'").fetchone()["id"]
    other = make_user(db, "other@example.org", UserRole.USER)
    db.execute("UPDATE api_keys SET user_id = %s WHERE id = %s", (other, key_id))
    stolen = client.post(f"/watchlist/keys/{key_id}/revoke", data={"csrf_token": token})
    assert stolen.status_code == 404
    db.execute("UPDATE api_keys SET user_id = %s WHERE id = %s", (user_id, key_id))
    revoked = client.post(f"/watchlist/keys/{key_id}/revoke", data={"csrf_token": token})
    assert revoked.status_code == 303
    _get(client, secret, "/api/v1/committees", 401)

    blank = client.post("/watchlist/keys", data={"csrf_token": token, "name": " "})
    assert blank.status_code == 422
    for n in range(5):
        client.post("/watchlist/keys", data={"csrf_token": token, "name": f"k{n}"})
    over = client.post("/watchlist/keys", data={"csrf_token": token, "name": "sixth"})
    assert over.status_code == 422 and "can hold 5 keys" in over.get_data(as_text=True)
