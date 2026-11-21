"""Alert events, hourly digests, the weekly summary, announcements, and unsubscribe."""

import copy
import re
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from flask import Flask
from flask.testing import FlaskClient

from beacon_contract import v1
from tests.conftest import Pusher, bundle_dict
from tracker.alerts import digest
from tracker.auth import accounts
from tracker.config import Settings
from tracker.db import Connection
from tracker.display import LEGISLATURE_TZ
from tracker.enums import AlertKind, EmailKind, UserRole, WatchKind
from tracker.mail.mailings import week_key
from tracker.publish.lifecycle import rollback
from tracker.watch import queries as watches
from tracker.watch.queries import WatchTarget

H10 = "194/H10:J19:2025-02-27"
ALL_WATCH = frozenset(
    {
        EmailKind.WATCH_STATUS_CHANGED,
        EmailKind.WATCH_DEADLINE_MISSED,
        EmailKind.WATCH_DEADLINE_APPROACHING,
    }
)


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
    for module in ("tracker.alerts.digest", "tracker.mail.mailings"):
        monkeypatch.setattr(f"{module}.send", lambda _settings, message: sent.append(message))
    return sent


def _run(app: Flask, *args: str) -> str:
    result = app.test_cli_runner().invoke(args=list(args))
    assert result.exit_code == 0, result.output
    return result.output


def _rerun(bundle: dict[str, Any]) -> dict[str, Any]:
    return bundle | {"evaluation_run_id": str(uuid4())}


def _h10_pending(bundle: dict[str, Any]) -> dict[str, Any]:
    """J19 with H10's report-out deadline Provisional instead of Unmet."""
    changed = copy.deepcopy(bundle)
    tenure = next(t for t in changed["tenures"] if t["tenure_id"] == H10)
    req = next(r for r in tenure["requirements"] if r["requirement_id"] == "report_out_deadline")
    req |= {"status": "Provisional", "reason": "not_yet_due", "detail": "Due 2026-01-03"}
    tenure["label"] = v1.roll_up(v1.RequirementStatus(r["status"]) for r in tenure["requirements"])
    return changed


def _watcher(
    db: Connection,
    email: str,
    prefs: frozenset[EmailKind],
    kind: WatchKind = WatchKind.BILL,
    target_id: str = "H10",
) -> int:
    user_id = accounts.create(db, email, UserRole.USER, prefs)
    target = WatchTarget(kind=kind, session=194, target_id=target_id)
    watches.add(db, user_id, target, accounts.default_alerts(prefs))
    return user_id


def _unsubscribe_path(message: EmailMessage) -> str:
    url = message["List-Unsubscribe"].strip("<>")
    assert url.startswith("https://tracker.example.org/email/unsubscribe?t=")
    assert message["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    return url.removeprefix("https://tracker.example.org")


def test_status_change_reaches_a_watcher_once(
    app: Flask, pusher: Pusher, db: Connection, outbox: list[EmailMessage]
):
    j19 = bundle_dict("J19")
    pusher.push({"J19": _h10_pending(j19), "J45": bundle_dict("J45")})
    first = _watcher(db, "bill@example.org", ALL_WATCH)
    watches.add(
        db,
        first,
        WatchTarget(kind=WatchKind.COMMITTEE, session=194, target_id="J19"),
        frozenset({AlertKind.STATUS_CHANGED}),
    )
    _watcher(db, "other@example.org", ALL_WATCH, target_id="H11")
    _watcher(db, "quiet@example.org", frozenset({EmailKind.WEEKLY_SUMMARY}))
    assert "sent 0 digests" in _run(app, "alerts", "send")

    pusher.push({"J19": _rerun(j19)})
    assert "sent 1 digests" in _run(app, "alerts", "send")
    (message,) = outbox
    body = message.get_content()
    assert message["To"] == "bill@example.org"
    assert "updates on 1 bill segment you watch" in message["Subject"]
    assert body.count("/bills/194/H10#") == 1 and "Joint Committee on the Judiciary (J19)" in body
    assert "Changed from" not in body, "H10 has other Unmet requirements, so its label holds"
    assert "Reported out: Pending to Not shown." in body
    assert "Reported out was due 2026-01-03; the public record does not show it." in body
    assert "https://tracker.example.org/bills/194/H10#seg-j19" in body
    assert "may differ from what a committee did" in body
    _unsubscribe_path(message)

    assert "sent 0 digests" in _run(app, "alerts", "send")
    assert len(outbox) == 1
    kinds = db.execute("SELECT kind FROM alert_events WHERE tenure_id = %s ORDER BY kind", (H10,))
    assert [r["kind"] for r in kinds] == ["status_changed", "status_changed", "deadline_missed"]


def test_preferences_and_per_watch_alerts_both_gate_delivery(
    app: Flask, pusher: Pusher, db: Connection, outbox: list[EmailMessage]
):
    j19 = bundle_dict("J19")
    pusher.push({"J19": _h10_pending(j19)})
    pref_off = _watcher(db, "prefoff@example.org", ALL_WATCH)
    accounts.set_email_prefs(db, pref_off, frozenset())
    watch_off = _watcher(db, "watchoff@example.org", ALL_WATCH)
    db.execute("UPDATE watches SET alerts = '{}' WHERE user_id = %s", (watch_off,))
    disabled = _watcher(db, "disabled@example.org", ALL_WATCH)
    db.execute("UPDATE users SET disabled_at = now() WHERE id = %s", (disabled,))
    pusher.push({"J19": _rerun(j19)})
    assert "sent 0 digests" in _run(app, "alerts", "send")
    assert outbox == []


def test_rolled_back_changes_are_not_sent(
    app: Flask, pusher: Pusher, db: Connection, outbox: list[EmailMessage]
):
    j19 = bundle_dict("J19")
    pusher.push({"J19": _h10_pending(j19)})
    _watcher(db, "bill@example.org", ALL_WATCH)
    pusher.push({"J19": _rerun(j19)})
    rollback(db, 194, "J19")
    assert "sent 0 digests" in _run(app, "alerts", "send")
    live = db.execute(
        "SELECT count(*) AS n FROM alert_events WHERE withdrawn_at IS NULL"
        " AND tenure_id = %s AND created_at > now() - interval '1 minute'",
        (H10,),
    )
    assert live.fetchone()["n"] == 1, "only the first publish's 'now tracked' event stays live"


def test_failed_sends_are_retried(
    app: Flask,
    pusher: Pusher,
    db: Connection,
    monkeypatch: pytest.MonkeyPatch,
    outbox: list[EmailMessage],
):
    j19 = bundle_dict("J19")
    pusher.push({"J19": _h10_pending(j19)})
    _watcher(db, "bill@example.org", ALL_WATCH)
    pusher.push({"J19": _rerun(j19)})

    def refuse(_settings: Settings, _message: EmailMessage) -> None:
        raise OSError("connection refused")

    monkeypatch.setattr("tracker.alerts.digest.send", refuse)
    result = app.test_cli_runner().invoke(args=["alerts", "send"])
    assert result.exit_code == 1 and "1 failed" in result.output
    assert db.execute("SELECT count(*) AS n FROM alert_deliveries").fetchone()["n"] == 0
    monkeypatch.setattr("tracker.alerts.digest.send", lambda _s, m: outbox.append(m))
    assert "sent 1 digests" in _run(app, "alerts", "send")


def test_deadline_scan_records_each_deadline_once(
    app: Flask, pusher: Pusher, db: Connection, outbox: list[EmailMessage]
):
    pusher.push({"J19": bundle_dict("J19")})
    _watcher(db, "committee@example.org", ALL_WATCH, WatchKind.COMMITTEE, "J19")
    due = datetime.now(LEGISLATURE_TZ).date() + timedelta(days=3)
    db.execute(
        "UPDATE requirements SET status = 'Provisional', reason = 'not_yet_due', due = %s"
        " WHERE tenure_id = %s AND requirement = 'report_out_deadline'",
        (due, H10),
    )
    assert "1 new deadline reminders" in _run(app, "alerts", "scan")
    assert "0 new deadline reminders" in _run(app, "alerts", "scan")
    _run(app, "alerts", "send")
    (message,) = outbox
    assert f"Reported out is due {due.isoformat()}." in message.get_content()


def test_unsubscribe_takes_effect_immediately(
    app: Flask, client: FlaskClient, pusher: Pusher, db: Connection, outbox: list[EmailMessage]
):
    j19 = bundle_dict("J19")
    pusher.push({"J19": _h10_pending(j19)})
    user_id = _watcher(db, "bill@example.org", ALL_WATCH | {EmailKind.WEEKLY_SUMMARY})
    pusher.push({"J19": _rerun(j19)})
    _run(app, "alerts", "send")
    path = _unsubscribe_path(outbox[-1])

    page = client.get(path)
    assert page.status_code == 200 and "bill@example.org" in page.get_data(as_text=True)
    assert page.headers["Referrer-Policy"] == "no-referrer"
    assert accounts.email_prefs(db, user_id) == ALL_WATCH | {EmailKind.WEEKLY_SUMMARY}

    one_click = client.post(path, data={"List-Unsubscribe": "One-Click"})
    assert one_click.status_code == 200
    assert accounts.email_prefs(db, user_id) == {
        EmailKind.WATCH_DEADLINE_APPROACHING,
        EmailKind.WEEKLY_SUMMARY,
    }
    pusher.push({"J19": _rerun(_h10_pending(j19))})
    assert "sent 0 digests" in _run(app, "alerts", "send")

    client.post(path, data={"scope": "all"})
    assert accounts.email_prefs(db, user_id) == frozenset()
    tampered = path[:-3] + ("AAA" if not path.endswith("AAA") else "BBB")
    assert client.get(tampered).status_code == 400
    assert client.post(tampered).status_code == 400


def test_weekly_summary_goes_once_per_week_to_those_who_asked(
    app: Flask, pusher: Pusher, db: Connection, outbox: list[EmailMessage]
):
    _run(app, "emails", "weekly")
    pusher.push({"J19": bundle_dict("J19"), "J45": bundle_dict("J45")})
    accounts.create(db, "weekly@example.org", UserRole.USER, frozenset({EmailKind.WEEKLY_SUMMARY}))
    accounts.create(db, "alerts@example.org", UserRole.USER, ALL_WATCH)
    assert "sent 1;" in _run(app, "emails", "weekly")
    (message,) = outbox
    body = message.get_content()
    assert message["To"] == "weekly@example.org" and "weekly summary" in message["Subject"]
    assert "858 bill segments across 2 committees" in body
    assert "Violation detected:" in body and "Compliance rate:" in body
    assert not re.search(r"\b(we|our|us|best|worst|rank)\b", body, re.I)
    assert "sent 0;" in _run(app, "emails", "weekly")


def test_announcements(app: Flask, db: Connection, outbox: list[EmailMessage], tmp_path: Path):
    accounts.create(db, "news@example.org", UserRole.USER, frozenset({EmailKind.ANNOUNCEMENTS}))
    accounts.create(db, "none@example.org", UserRole.USER, frozenset())
    notice = tmp_path / "2026-12-launch.txt"
    notice.write_text("The Tracker has moved\n\nThe site now lives at a new address.\n")
    assert "to 1 accounts" in _run(app, "emails", "announce", str(notice), "--yes")
    (message,) = outbox
    assert message["Subject"] == "The Tracker has moved"
    assert "a new address" in message.get_content() and message["List-Unsubscribe"]
    assert "to 0 accounts" in _run(app, "emails", "announce", str(notice), "--yes")
    assert len(outbox) == 1
    empty = tmp_path / "empty.txt"
    empty.write_text("Subject only\n")
    result = app.test_cli_runner().invoke(args=["emails", "announce", str(empty), "--yes"])
    assert result.exit_code != 0 and "needs a subject line and a body" in result.output


def test_mail_jobs_need_mail_settings(app: Flask):
    app.config["TRACKER_SETTINGS"] = app.config["TRACKER_SETTINGS"].model_copy(
        update={"site_url": None}
    )
    for args in (["alerts", "send"], ["emails", "weekly"]):
        result = app.test_cli_runner().invoke(args=args)
        assert result.exit_code != 0 and "SITE_URL" in result.output


def test_week_key():
    assert week_key(date(2026, 11, 20)) == "2026-W47"
    assert week_key(date(2027, 1, 1)) == "2026-W53"


@pytest.mark.parametrize(
    ("old", "new", "changes", "expected"),
    [
        (
            "Non-Compliant",
            "Compliant",
            [{"requirement": "votes_posted", "from": "Unmet", "to": "Met"}],
            [
                "Changed from Violation detected to No violation detected.",
                "Vote: Not shown to Met.",
            ],
        ),
        (None, "Provisional", [], ["Now tracked: Provisional."]),
        ("Compliant", None, [], ["No longer in the committee's published data."]),
        (
            "Provisional",
            "Provisional",
            [{"requirement": "hearing_notice", "from": None, "to": "Provisional"}],
            ["Hearing notice: Not applicable to Pending."],
        ),
    ],
)
def test_status_change_wording(old, new, changes, expected):
    row = {
        "kind": "status_changed",
        "from_label": old,
        "to_label": new,
        "changed_requirements": changes,
    }
    assert digest._lines(row) == expected
