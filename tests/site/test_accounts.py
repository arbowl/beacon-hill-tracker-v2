"""Sign-up, magic-link sign-in, sessions, and the watchlist."""

import re
from email.message import EmailMessage
from html import unescape

import pytest
from flask import Flask
from flask.testing import FlaskClient

from tests.conftest import Pusher, bundle_dict, make_user
from tracker.auth import accounts
from tracker.auth.current import COOKIE, safe_next
from tracker.config import Settings
from tracker.db import Connection
from tracker.enums import EmailKind, UserRole


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={
            "smtp_host": "smtp.invalid",
            "mail_from": "tracker@example.org",
            "site_url": "https://tracker.example.org",
        }
    )


@pytest.fixture
def outbox(monkeypatch: pytest.MonkeyPatch) -> list[EmailMessage]:
    sent: list[EmailMessage] = []
    monkeypatch.setattr("tracker.auth.routes.send", lambda _settings, message: sent.append(message))
    return sent


@pytest.fixture
def published(pusher: Pusher) -> None:
    pusher.push({"J19": bundle_dict("J19"), "J45": bundle_dict("J45")})


def _csrf(client: FlaskClient, url: str = "/signin") -> str:
    html = client.get(url).get_data(as_text=True)
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, url
    return match[1]


def _link(message: EmailMessage) -> str:
    match = re.search(
        r"https://tracker\.example\.org(/signin/verify\?t=\S+)", message.get_content()
    )
    assert match, message.get_content()
    return match[1]


def _token(path: str) -> str:
    return path.split("t=", 1)[1]


def _sign_up(client: FlaskClient, outbox: list[EmailMessage], email: str, kinds: list[str]):
    response = client.post(
        "/signup",
        data={"csrf_token": _csrf(client, "/signup"), "email": email, "email_kind": kinds},
    )
    assert response.status_code == 200 and "Check your email" in response.get_data(as_text=True)
    return _link(outbox[-1])


def _follow(client: FlaskClient, path: str):
    """Open an emailed link and press its button, as a person does."""
    page = client.get(path)
    assert page.status_code == 200
    return client.post(
        "/signin/verify", data={"csrf_token": _csrf(client, path), "t": _token(path)}
    )


def _sign_in(client: FlaskClient, outbox: list[EmailMessage], email: str, next_path: str = ""):
    data = {"csrf_token": _csrf(client), "email": email}
    if next_path:
        data["next"] = next_path
    assert client.post("/signin", data=data).status_code == 200
    return _follow(client, _link(outbox[-1]))


def test_sign_up_creates_the_account_only_when_the_link_is_used(
    client: FlaskClient, db: Connection, outbox: list[EmailMessage]
):
    page = client.get("/signup").get_data(as_text=True)
    assert page.count("checked") == 3
    link = _sign_up(client, outbox, "Reader@Example.org", ["watch_status_changed", "announcements"])
    assert outbox[-1]["To"] == "Reader@example.org"
    assert "Confirm" in outbox[-1]["Subject"]
    assert db.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 0
    stored = db.execute("SELECT token_hash FROM login_tokens").fetchone()["token_hash"]
    assert _token(link).encode() not in bytes(stored)

    for _ in range(3):
        scanned = client.get(link)
        assert scanned.status_code == 200 and "Finish signing in" in scanned.get_data(as_text=True)
        assert scanned.headers["Referrer-Policy"] == "no-referrer"
        assert scanned.headers["Cache-Control"] == "no-store"
    assert db.execute("SELECT used_at FROM login_tokens").fetchone()["used_at"] is None

    signed_in = _follow(client, link)
    assert signed_in.status_code == 303 and signed_in.headers["Location"] == "/watchlist"
    cookie = signed_in.headers["Set-Cookie"]
    assert cookie.startswith(f"{COOKIE}=") and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "Max-Age=2592000" in cookie
    user = db.execute("SELECT id, role, last_login_at FROM users").fetchone()
    assert user["role"] == UserRole.USER and user["last_login_at"] is not None
    assert accounts.email_prefs(db, user["id"]) == {
        EmailKind.WATCH_STATUS_CHANGED,
        EmailKind.ANNOUNCEMENTS,
    }
    assert db.execute("SELECT count(*) AS n FROM user_email_prefs").fetchone()["n"] == len(
        EmailKind
    )
    watchlist = client.get("/watchlist")
    assert watchlist.mimetype == "text/html"
    assert "reader@example.org" in watchlist.get_data(as_text=True).lower()

    token = _csrf(client, "/watchlist")
    again = client.post("/signin/verify", data={"csrf_token": token, "t": _token(link)})
    assert again.status_code == 410
    assert client.get(link).status_code == 410


def test_sign_in_replies_the_same_whether_or_not_an_account_exists(
    client: FlaskClient, db: Connection, outbox: list[EmailMessage]
):
    make_user(db, "known@example.org", UserRole.USER)
    bodies = []
    for email in ("known@example.org", "unknown@example.org"):
        response = client.post("/signin", data={"csrf_token": _csrf(client), "email": email})
        assert response.status_code == 200
        bodies.append(response.get_data(as_text=True))
    assert bodies[0] == bodies[1]
    known, unknown = outbox
    assert "/signin/verify?t=" in known.get_content()
    assert "has no account" in unknown.get_content()
    assert "https://tracker.example.org/signup" in unknown.get_content()
    assert db.execute("SELECT count(*) AS n FROM login_tokens").fetchone()["n"] == 1


def test_sign_up_for_an_existing_account_sends_a_sign_in_link(
    client: FlaskClient, db: Connection, outbox: list[EmailMessage]
):
    user_id = make_user(db, "known@example.org", UserRole.USER)
    accounts.set_email_prefs(db, user_id, frozenset({EmailKind.WEEKLY_SUMMARY}))
    link = _sign_up(client, outbox, "known@example.org", ["announcements"])
    assert "already has one" in outbox[-1].get_content()
    assert _follow(client, link).status_code == 303
    assert accounts.email_prefs(db, user_id) == {EmailKind.WEEKLY_SUMMARY}


def test_links_expire_and_require_the_form_token(
    client: FlaskClient, db: Connection, outbox: list[EmailMessage]
):
    make_user(db, "known@example.org", UserRole.USER)
    client.post("/signin", data={"csrf_token": _csrf(client), "email": "known@example.org"})
    link = _link(outbox[-1])
    assert client.post("/signin/verify", data={"t": _token(link)}).status_code == 400
    db.execute("UPDATE login_tokens SET expires_at = now() - interval '1 second'")
    assert client.get(link).status_code == 410
    expired = client.post("/signin/verify", data={"csrf_token": _csrf(client), "t": _token(link)})
    assert expired.status_code == 410
    assert client.get("/signin/verify?t=nonsense").status_code == 410
    assert client.get("/signin/verify?t=" + "x" * 500).status_code == 410


def test_link_requests_are_throttled_per_address_and_per_network(
    client: FlaskClient, app: Flask, db: Connection, outbox: list[EmailMessage]
):
    token = _csrf(client)
    codes = [
        client.post("/signin", data={"csrf_token": token, "email": "a@example.org"}).status_code
        for _ in range(4)
    ]
    assert codes == [200, 200, 200, 429]
    assert len(outbox) == 3
    app.config["TRACKER_SETTINGS"] = app.config["TRACKER_SETTINGS"].model_copy(
        update={"login_limit_per_ip_per_hour": 5}
    )
    codes = [
        client.post("/signin", data={"csrf_token": token, "email": f"{n}@example.org"}).status_code
        for n in range(3)
    ]
    assert codes == [200, 200, 429]


def test_disabled_accounts_cannot_sign_in_and_lose_their_sessions(
    client: FlaskClient, db: Connection, outbox: list[EmailMessage]
):
    make_user(db, "known@example.org", UserRole.USER)
    assert _sign_in(client, outbox, "known@example.org").status_code == 303
    assert client.get("/watchlist").status_code == 200
    db.execute("UPDATE users SET disabled_at = now()")
    assert client.get("/watchlist").status_code == 302
    client.post("/signin", data={"csrf_token": _csrf(client), "email": "known@example.org"})
    assert len(outbox) == 1


def test_sign_out_and_sign_out_everywhere(app: Flask, db: Connection, outbox: list[EmailMessage]):
    make_user(db, "known@example.org", UserRole.USER)
    laptop, phone = app.test_client(), app.test_client()
    for device in (laptop, phone):
        assert _sign_in(device, outbox, "known@example.org").status_code == 303
    token = _csrf(laptop, "/watchlist")
    assert laptop.post("/signout", data={"csrf_token": token}).status_code == 303
    assert laptop.get("/watchlist").status_code == 302
    assert phone.get("/watchlist").status_code == 200
    assert _sign_in(laptop, outbox, "known@example.org").status_code == 303
    token = _csrf(phone, "/watchlist")
    phone.post("/signout", data={"csrf_token": token, "everywhere": "1"})
    assert phone.get("/watchlist").status_code == 302
    assert laptop.get("/watchlist").status_code == 302
    assert db.execute("SELECT count(*) AS n FROM web_sessions").fetchone()["n"] == 0


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/bills/194/H10", "/bills/194/H10"),
        ("/?committee=J19", "/?committee=J19"),
        ("//evil.example", None),
        ("https://evil.example/", None),
        ("/\\evil.example", None),
        ("bills", None),
        ("", None),
        (None, None),
    ],
)
def test_safe_next(value: str | None, expected: str | None):
    assert safe_next(value) == expected


def test_watch_from_the_bill_page_returns_there_after_sign_in(
    client: FlaskClient, db: Connection, outbox: list[EmailMessage], published: None
):
    page = client.get("/bills/194/H10").get_data(as_text=True)
    href = unescape(re.search(r'href="(/signin\?next=[^"]+)"[^>]*>Watch this bill', page)[1])
    assert href == "/signin?next=/bills/194/H10"
    user_id = make_user(db, "known@example.org", UserRole.USER)
    accounts.set_email_prefs(
        db, user_id, frozenset({EmailKind.WATCH_DEADLINE_MISSED, EmailKind.WEEKLY_SUMMARY})
    )
    assert "Sign In" in client.get(href).get_data(as_text=True)
    signed_in = _sign_in(client, outbox, "known@example.org", next_path="/bills/194/H10")
    assert signed_in.headers["Location"] == "/bills/194/H10"

    token = _csrf(client, "/bills/194/H10")
    target = {"csrf_token": token, "kind": "bill", "session": "194", "target_id": "H10"}
    fragment = client.post(
        "/watchlist/watch",
        data=target | {"next": "/bills/194/H10"},
        headers={"HX-Request": "true"},
    )
    assert fragment.status_code == 200 and "Stop watching" in fragment.get_data(as_text=True)
    assert fragment.mimetype == "text/html"
    assert fragment.headers["Cache-Control"] == "private, no-store"
    watch = db.execute("SELECT id, alerts::text[] AS alerts FROM watches").fetchone()
    assert watch["alerts"] == ["deadline_missed"]
    assert client.post("/watchlist/watch", data=target).status_code == 303
    assert db.execute("SELECT count(*) AS n FROM watches").fetchone()["n"] == 1

    watchlist = client.get("/watchlist").get_data(as_text=True)
    assert ">H10<" in watchlist and "J19: " in watchlist
    assert "Stop watching" in client.get("/bills/194/H10").get_data(as_text=True)

    client.post(
        f"/watchlist/watches/{watch['id']}/alerts",
        data={"csrf_token": token, "alert": ["status_changed", "deadline_approaching"]},
    )
    alerts = db.execute("SELECT alerts::text[] AS a FROM watches").fetchone()["a"]
    assert alerts == ["deadline_approaching", "status_changed"]
    bad = client.post(
        f"/watchlist/watches/{watch['id']}/alerts", data={"csrf_token": token, "alert": ["nope"]}
    )
    assert bad.status_code == 400

    removed = client.post("/watchlist/unwatch", data=target | {"next": "/watchlist"})
    assert removed.status_code == 303 and removed.headers["Location"] == "/watchlist"
    assert db.execute("SELECT count(*) AS n FROM watches").fetchone()["n"] == 0


def test_watch_targets_must_exist_and_belong_to_the_user(
    app: Flask, db: Connection, outbox: list[EmailMessage], published: None
):
    owner, other = app.test_client(), app.test_client()
    for device, email in ((owner, "owner@example.org"), (other, "other@example.org")):
        accounts.create(db, email, UserRole.USER, frozenset({EmailKind.WATCH_STATUS_CHANGED}))
        _sign_in(device, outbox, email)
    token = _csrf(owner, "/watchlist")
    committee = {"csrf_token": token, "kind": "committee", "session": "194", "target_id": "J19"}
    assert owner.post("/watchlist/watch", data=committee).status_code == 303
    missing = committee | {"target_id": "J999"}
    assert owner.post("/watchlist/watch", data=missing).status_code == 404
    malformed = committee | {"target_id": "j19'"}
    assert owner.post("/watchlist/watch", data=malformed).status_code == 400
    assert "compliance rate" in owner.get("/watchlist").get_data(as_text=True)

    watch_id = db.execute("SELECT id FROM watches").fetchone()["id"]
    other_token = _csrf(other, "/watchlist")
    stolen = other.post(
        f"/watchlist/watches/{watch_id}/alerts", data={"csrf_token": other_token, "alert": []}
    )
    assert stolen.status_code == 404
    assert db.execute("SELECT alerts::text[] AS a FROM watches").fetchone()["a"] == [
        "status_changed"
    ]


def test_signed_out_post_redirects_to_sign_in(client: FlaskClient, published: None):
    token = _csrf(client)
    response = client.post(
        "/watchlist/watch",
        data={
            "csrf_token": token,
            "kind": "bill",
            "session": "194",
            "target_id": "H10",
            "next": "/bills/194/H10",
        },
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/signin?next=/bills/194/H10"


def test_email_preferences_are_saved_with_provenance(
    client: FlaskClient, db: Connection, outbox: list[EmailMessage]
):
    user_id = make_user(db, "known@example.org", UserRole.USER)
    accounts.create(db, "fresh@example.org", UserRole.USER, frozenset())
    _sign_in(client, outbox, "known@example.org")
    token = _csrf(client, "/watchlist")
    saved = client.post(
        "/watchlist/preferences", data={"csrf_token": token, "email_kind": ["weekly_summary"]}
    )
    assert saved.status_code == 303
    assert accounts.email_prefs(db, user_id) == {EmailKind.WEEKLY_SUMMARY}
    assert "Email preferences saved." in client.get("/watchlist").get_data(as_text=True)
    before = db.execute(
        "SELECT kind, updated_at FROM user_email_prefs WHERE user_id = %s", (user_id,)
    ).fetchall()
    client.post(
        "/watchlist/preferences", data={"csrf_token": token, "email_kind": ["weekly_summary"]}
    )
    after = db.execute(
        "SELECT kind, updated_at FROM user_email_prefs WHERE user_id = %s", (user_id,)
    ).fetchall()
    assert before == after


def test_signed_in_pages_are_private(
    client: FlaskClient, db: Connection, outbox: list[EmailMessage], published: None
):
    anonymous = client.get("/?committee=J19")
    assert anonymous.headers["Cache-Control"] == "public, max-age=60"
    assert "Sign In" in anonymous.get_data(as_text=True)
    make_user(db, "known@example.org", UserRole.USER)
    _sign_in(client, outbox, "known@example.org")
    mine = client.get("/?committee=J19", headers={"If-None-Match": anonymous.headers["ETag"]})
    assert mine.status_code == 200
    assert mine.headers["Cache-Control"] == "private, no-cache" and "ETag" not in mine.headers
    html = mine.get_data(as_text=True)
    assert "Watchlist" in html and "Watch this committee" in html
    assert re.search(r"style=\"", html) is None
    start = html.index('<form id="filter-form"')
    filter_form = html[start : html.index("</form>", start)]
    assert "<form" not in filter_form[1:] and 'id="share"' not in filter_form
    fragment = client.get("/?committee=J19", headers={"HX-Request": "true"}).get_data(as_text=True)
    assert 'hx-post="/watchlist/watch" hx-target="this"' in fragment


def test_sign_in_is_unavailable_without_mail(app: Flask, client: FlaskClient):
    app.config["TRACKER_SETTINGS"] = app.config["TRACKER_SETTINGS"].model_copy(
        update={"site_url": None}
    )
    assert "not available" in client.get("/signin").get_data(as_text=True)
    response = client.post("/signin", data={"csrf_token": _csrf(client), "email": "a@b.org"})
    assert response.status_code == 503


def test_watchlist_shows_email_sign_ups_to_webmasters_only(
    client: FlaskClient, db: Connection, outbox: list[EmailMessage]
):
    accounts.create(db, "reader@example.org", UserRole.USER, frozenset({EmailKind.WEEKLY_SUMMARY}))
    accounts.create(db, "quiet@example.org", UserRole.USER, frozenset())
    accounts.create(db, "admin@example.org", UserRole.WEBMASTER, frozenset())

    _sign_in(client, outbox, "reader@example.org")
    assert "Email sign-ups" not in client.get("/watchlist").get_data(as_text=True)

    token = _csrf(client, "/watchlist")
    client.post("/signout", data={"csrf_token": token})
    _sign_in(client, outbox, "admin@example.org")
    html = client.get("/watchlist").get_data(as_text=True)
    start = html.index('id="signups"')
    block = html[start : html.index("</section>", start)]
    assert "3 active accounts, 1 taking at least one kind of email" in block
    assert "3 new in the last 7 days" in block
    assert "reader@example.org" in block and "the weekly summary" in block
    assert "quiet@example.org" in block and "No email" in block
