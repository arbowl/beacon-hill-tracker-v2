"""The ingest API authenticates, validates, and is idempotent."""

import gzip
import json
from uuid import uuid4

import pytest

from tests.conftest import Pusher, bundle_dict, gz, make_user
from tracker.auth.keys import KeyNotAllowed, create_key
from tracker.db import Connection
from tracker.enums import KeyScope, UserRole


def test_requires_a_live_ingest_key(client, db: Connection, pusher: Pusher):
    user_id = make_user(db, "reader@example.org", UserRole.USER)
    read_key = create_key(db, user_id, KeyScope.READ, "reader").secret
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": f"Bearer {read_key}"}):
        response = client.post("/api/ingest/v1/runs", headers=headers)
        assert response.status_code == 401
        assert response.get_json()["error"]["code"] == "unauthorized"
    assert pusher.open(uuid4(), ["J45"]).status_code == 200
    db.execute("UPDATE users SET role = 'user' WHERE email = 'webmaster@example.org'")
    assert pusher.open(uuid4(), ["J45"]).status_code == 401


def test_only_webmasters_get_ingest_keys(db: Connection):
    user_id = make_user(db, "reader@example.org", UserRole.USER)
    with pytest.raises(KeyNotAllowed):
        create_key(db, user_id, KeyScope.INGEST, "nope")


def test_open_is_idempotent_per_scope(pusher: Pusher):
    push_id = uuid4()
    first = pusher.open(push_id, ["J19", "J45"])
    again = pusher.open(push_id, ["J19", "J45"])
    assert first.status_code == again.status_code == 200
    assert first.get_json()["opened_at"] == again.get_json()["opened_at"]
    conflict = pusher.open(push_id, ["J19"])
    assert conflict.status_code == 409
    assert conflict.get_json()["error"]["code"] == "scope_conflict"


def test_invalid_bundle_is_422_with_field_paths(pusher: Pusher):
    push_id = uuid4()
    pusher.open(push_id, ["J45"])
    bad = bundle_dict("J45")
    index = next(i for i, t in enumerate(bad["tenures"]) if t["label"] == "Non-Compliant")
    bad["tenures"][index]["label"] = "Compliant"
    response = pusher.upload(push_id, "J45", gzip.compress(json.dumps(bad).encode()))
    assert response.status_code == 422
    error = response.get_json()["error"]
    assert error["code"] == "invalid"
    assert error["details"][0]["loc"] == ["tenures", index]


def test_upload_must_match_path_and_scope(pusher: Pusher):
    push_id = uuid4()
    pusher.open(push_id, ["J19"])
    wrong_path = pusher.upload(push_id, "J19", gz(bundle_dict("J45")))
    assert wrong_path.status_code == 422
    assert wrong_path.get_json()["error"]["code"] == "upload_mismatch"
    out_of_scope = pusher.upload(push_id, "J45", gz(bundle_dict("J45")))
    assert out_of_scope.status_code == 422


def test_bad_encodings(pusher: Pusher):
    push_id = uuid4()
    pusher.open(push_id, ["J45"])
    assert pusher.upload(push_id, "J45", b"not gzip").status_code == 400
    response = pusher.client.put(
        f"/api/ingest/v1/runs/{push_id}/committees/J45",
        data=b"x",
        headers=pusher.headers | {"Content-Encoding": "br"},
    )
    assert response.status_code == 415


def test_gzip_bomb_is_refused(app, pusher: Pusher):
    app.config["TRACKER_SETTINGS"] = app.config["TRACKER_SETTINGS"].model_copy(
        update={"ingest_max_json_bytes": 1024}
    )
    push_id = uuid4()
    pusher.open(push_id, ["J45"])
    assert pusher.upload(push_id, "J45", gzip.compress(b" " * 10_000)).status_code == 413


def test_publish_requires_every_committee(pusher: Pusher):
    push_id = uuid4()
    pusher.open(push_id, ["J19", "J45"])
    pusher.upload(push_id, "J45", gz(bundle_dict("J45")))
    response = pusher.publish(push_id)
    assert response.status_code == 409
    assert "J19" in response.get_json()["error"]["message"]


def test_reupload_replaces_and_publish_is_idempotent(pusher: Pusher, db: Connection):
    push_id = uuid4()
    pusher.open(push_id, ["J45"])
    pusher.upload(push_id, "J45", gz(bundle_dict("J45")))
    pusher.upload(push_id, "J45", gz(bundle_dict("J45")))
    assert db.execute("SELECT count(*) AS n FROM tenures").fetchone()["n"] == 67
    first = pusher.publish(push_id).get_json()
    again = pusher.publish(push_id).get_json()
    assert first == again and first["status"] == "published"
    late = pusher.upload(push_id, "J45", gz(bundle_dict("J45")))
    assert late.status_code == 409
    assert late.get_json()["error"]["code"] == "run_not_open"
    reopened = pusher.open(push_id, ["J45"]).get_json()
    assert reopened["status"] == "published"


def test_status_and_abandon(pusher: Pusher, db: Connection):
    push_id = uuid4()
    pusher.open(push_id, ["J45"])
    pusher.upload(push_id, "J45", gz(bundle_dict("J45")))
    state = pusher.client.get(f"/api/ingest/v1/runs/{push_id}", headers=pusher.headers).get_json()
    assert state["uploaded"] == ["J45"] and state["status"] == "open"
    gone = pusher.client.delete(f"/api/ingest/v1/runs/{push_id}", headers=pusher.headers)
    assert gone.get_json()["status"] == "abandoned"
    assert db.execute("SELECT count(*) AS n FROM tenures").fetchone()["n"] == 0
    assert pusher.publish(push_id).status_code == 409
    missing = pusher.client.get(f"/api/ingest/v1/runs/{uuid4()}", headers=pusher.headers)
    assert missing.status_code == 404
