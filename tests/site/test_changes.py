"""The client's changelog is stored with its history and shown without crowding the dashboard."""

from datetime import date, datetime, timedelta
from typing import Any

import pytest
from flask.testing import FlaskClient

from tests.conftest import Pusher, bundle_dict
from tracker.changes.queries import NOTICE_DAYS
from tracker.db import Connection
from tracker.display import LEGISLATURE_TZ

TODAY = datetime.now(LEGISLATURE_TZ).date()


def release(version: str, released_on: date, item: str = "Checks four requirements.") -> dict:
    return {
        "version": version,
        "released_on": released_on.isoformat(),
        "sections": [{"kind": "Added", "items": [item]}],
    }


def changelog(*releases: dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": 1, "code_version": releases[0]["version"], "releases": releases}


def _ok(pusher: Pusher, body: dict[str, Any]) -> dict[str, Any]:
    response = pusher.changelog(body)
    assert response.status_code == 200, response.get_json()
    return response.get_json()


@pytest.fixture
def published(pusher: Pusher) -> None:
    pusher.push({"J19": bundle_dict("J19")})


def test_upload_is_idempotent_and_shown(client: FlaskClient, pusher: Pusher):
    body = changelog(
        release("0.2.0", TODAY, "Hearing notice counts calendar days."),
        release("0.1.0", TODAY - timedelta(days=30)),
    )
    assert _ok(pusher, body) == {"releases": ["0.2.0", "0.1.0"], "updated": ["0.2.0", "0.1.0"]}
    assert _ok(pusher, body)["updated"] == []
    html = client.get("/changes").get_data(as_text=True)
    assert html.index("Version 0.2.0") < html.index("Version 0.1.0")
    assert "Hearing notice counts calendar days." in html and "corrected" not in html


def test_items_are_escaped(client: FlaskClient, pusher: Pusher):
    _ok(pusher, changelog(release("0.1.0", TODAY, "<script>alert(1)</script>")))
    assert "&lt;script&gt;" in client.get("/changes").get_data(as_text=True)


def test_correction_keeps_both_texts(client: FlaskClient, pusher: Pusher, db: Connection):
    _ok(pusher, changelog(release("0.1.0", TODAY, "First wording.")))
    assert _ok(pusher, changelog(release("0.1.0", TODAY, "Second wording.")))["updated"] == [
        "0.1.0"
    ]
    rows = db.execute("SELECT count(*) AS n FROM changelog_releases").fetchone()
    assert rows is not None and rows["n"] == 2
    html = client.get("/changes").get_data(as_text=True)
    assert "Second wording." in html and "First wording." not in html
    assert "Note corrected" in html
    assert _ok(pusher, changelog(release("0.1.0", TODAY, "First wording.")))["updated"] == ["0.1.0"]
    assert "First wording." in client.get("/changes").get_data(as_text=True)


def test_refusals(client: FlaskClient, pusher: Pusher):
    assert client.put("/api/ingest/v1/changelog", json={}).status_code == 401
    _ok(pusher, changelog(release("0.2.0", TODAY), release("0.1.0", TODAY)))
    removed = pusher.changelog(changelog(release("0.2.0", TODAY)))
    assert removed.status_code == 409
    assert removed.get_json()["error"]["code"] == "release_removed"
    future = pusher.changelog(
        changelog(
            release("0.3.0", TODAY + timedelta(days=2)),
            release("0.2.0", TODAY),
            release("0.1.0", TODAY),
        )
    )
    assert future.status_code == 422
    assert future.get_json()["error"]["code"] == "future_release"
    invalid = pusher.changelog(changelog(release("0.2", TODAY)))
    assert invalid.status_code == 422 and invalid.get_json()["error"]["code"] == "invalid"


def _notice(client: FlaskClient) -> str | None:
    html = client.get("/").get_data(as_text=True)
    start = html.find('class="notice"')
    return html[start : html.index("</p>", start)] if start >= 0 else None


def test_dashboard_notice(client: FlaskClient, pusher: Pusher, published: None):
    assert _notice(client) is None
    old = release("0.1.0", TODAY - timedelta(days=NOTICE_DAYS))
    _ok(pusher, changelog(old))
    assert _notice(client) is None, "notice expires"
    etag = client.get("/").headers["ETag"]
    _ok(pusher, changelog(release("0.2.0", TODAY - timedelta(days=1)), old))
    notice = _notice(client)
    assert notice is not None and "version 0.2.0" in notice and "#v0.2.0" in notice
    assert client.get("/", headers={"If-None-Match": etag}).status_code == 200
    _ok(
        pusher, changelog(release("0.2.1", TODAY), release("0.2.0", TODAY - timedelta(days=1)), old)
    )
    notice = _notice(client)
    assert notice is not None and "version 0.2.0" in notice, "patches change no finding"


def test_footer_links_to_changes(client: FlaskClient, published: None):
    html = client.get("/methodology").get_data(as_text=True)
    assert html.count('href="/changes"') == 3
    assert "No changes have been published yet." in client.get("/changes").get_data(as_text=True)
