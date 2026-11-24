"""Dashboard, bill pages, and static pages render from published data."""

import re
from html import unescape

import pytest
from flask.testing import FlaskClient

from tests.conftest import Pusher, bundle_dict
from tracker.db import Connection
from tracker.publish import dumps


@pytest.fixture
def published(pusher: Pusher) -> None:
    pusher.push({"J19": bundle_dict("J19"), "J45": bundle_dict("J45")})


def _text(client: FlaskClient, url: str, status: int = 200, **kw) -> str:
    response = client.get(url, **kw)
    assert response.status_code == status, (url, response.status_code)
    return response.get_data(as_text=True)


def _segment_ids(html: str) -> list[str]:
    return re.findall(r"tenure=([^\"&]+)", unescape(html))


def test_empty_site_says_so(client: FlaskClient):
    assert "No data has been published yet" in _text(client, "/")


def test_dashboard_full_page(client: FlaskClient, published: None):
    html = _text(client, "/")
    assert "Is Beacon Hill Following Its Own Rules?" in html
    assert "858 bill segments across 2 committees" in html
    assert "data-charts='" in html and 'class="strip"' in html
    assert "Showing 50 of 858" in html
    assert "Violation detected" in html and ">Non-Compliant<" not in html
    assert "<b>Unmet</b>" not in html and "<b>Not shown</b>" in html
    assert re.search(r"style=\"", html) is None, "inline styles break the CSP"


def test_keyset_pages_cover_every_segment_once(client: FlaskClient, published: None):
    for sort in ("severity", "bill", "due"):
        seen: list[str] = []
        url = f"/?committee=J19&sort={sort}"
        while url:
            html = _text(client, url, headers={"HX-Request": "true"})
            seen += _segment_ids(html.split('id="status-radios"')[0])
            match = re.search(r'href="\?([^"]*after=[^"]+)"[^>]*>Next', html)
            url = "/?" + unescape(match[1]) if match else ""
        assert len(seen) == len(set(seen)) == 791, sort


def test_filters(client: FlaskClient, published: None, db: Connection):
    html = _text(client, "/?status=compliant&committee=J45")
    assert "Showing 34 of 34" in html
    html = _text(client, "/?q=h.4319")
    assert "194/H4319:J45:unobserved" in html
    assert "referral not found in the bill history" in html
    word = (
        db.execute(
            "SELECT title FROM current_tenures WHERE committee_id = 'J45' ORDER BY bill_num LIMIT 1"
        )
        .fetchone()["title"]
        .split()[-1]
    )
    assert "No bill segments match" not in _text(client, f"/?q={word}")


def test_chamber_filter(client: FlaskClient, published: None):
    html = _text(client, "/")
    assert '<option value="J">All Joint committees</option>' in html
    assert '<option value="H">' not in html, "chambers without data are not offered"
    joint = _text(client, "/?committee=J")
    assert "Showing 50 of 858" in joint
    assert "in Joint committees" in joint and "Compliance rate, Joint" in joint
    assert '<option value="J" selected>' in joint
    assert "No published data for this selection" in _text(client, "/?committee=H")
    assert "No bill segments match" in _text(client, "/?committee=S")


@pytest.mark.parametrize(
    "query",
    [
        "status=bogus",
        "committee=J0",
        "committee=X",
        "committee=HJ",
        "sort=up",
        "after=zzz",
        "nonsense=1",
        "q=" + "x" * 101,
    ],
)
def test_invalid_filters_are_400(client: FlaskClient, published: None, query: str):
    _text(client, f"/?{query}", status=400)


def test_htmx_fragment_swaps_out_of_band(client: FlaskClient, published: None):
    response = client.get(
        "/?q=&status=&committee=J45&sort=severity", headers={"HX-Request": "true"}
    )
    assert response.headers["HX-Push-Url"] == "/?committee=J45"
    html = response.get_data(as_text=True)
    assert html.lstrip().startswith('<div id="results">')
    assert html.count('hx-swap-oob="true"') == 4
    assert "<html" not in html


def test_etag_and_cache_headers(client: FlaskClient, app, published: None):
    first = client.get("/?committee=J19")
    assert first.headers["Cache-Control"] == "public, max-age=60"
    assert "frame-ancestors 'none'" in first.headers["Content-Security-Policy"]
    again = client.get("/?committee=J19", headers={"If-None-Match": first.headers["ETag"]})
    assert again.status_code == 304
    other = client.get(
        "/?committee=J19", headers={"If-None-Match": first.headers["ETag"], "HX-Request": "true"}
    )
    assert other.status_code == 200
    app.config["TRACKER_BUILD"] = "redeployed"
    redeployed = client.get("/?committee=J19", headers={"If-None-Match": first.headers["ETag"]})
    assert redeployed.status_code == 200, "a new build must not revalidate pages from the old one"


def test_bill_page_and_audit(client: FlaskClient, published: None):
    _text(client, "/bills/194/h.10", status=301)
    html = _text(client, "/bills/194/H10")
    assert (
        "This bill has one segment: it was referred to the Joint Committee on the Judiciary" in html
    )
    assert "How this was decided" in html and "Provenance" in html
    assert "first published" in html
    fragment = _text(client, "/bills/194/H10/audit?tenure=194/H10:J19:2025-02-27")
    assert fragment.lstrip().startswith('<div class="audit-body">')
    assert "Joint Rule 10" in fragment or "House Rule 27" in fragment
    _text(client, "/bills/194/H10/audit?tenure=194/H11:J19:2025-02-27", status=404)
    _text(client, "/bills/194/H999999", status=404)


@pytest.mark.parametrize(
    "path",
    [
        "/methodology",
        "/about",
        "/press",
        "/privacy",
        "/terms",
        "/contact",
        "/committees",
        "/changes",
    ],
)
def test_static_pages(client: FlaskClient, published: None, path: str):
    html = _text(client, path)
    assert not re.search(r"\b(we|our|us)\b", re.sub(r"<[^>]+>", " ", html), re.I)


def test_methodology_quotes_cited_rules(client: FlaskClient, published: None):
    assert "Joint Rule 10" in _text(client, "/methodology")


def test_contact_form(client: FlaskClient, app, published: None):
    html = _text(client, "/contact")
    assert "not available" in html
    token = None
    app.config["TRACKER_SETTINGS"] = app.config["TRACKER_SETTINGS"].model_copy(
        update={"smtp_host": "localhost", "mail_from": "a@b.org", "webmaster_email": "c@d.org"}
    )
    html = _text(client, "/contact")
    token = re.search(r'name="csrf_token" value="([^"]+)"', html)[1]
    assert (
        client.post("/contact", data={"email": "x@y.org", "message": "hello there"}).status_code
        == 400
    )
    bad = client.post("/contact", data={"csrf_token": token, "email": "nope", "message": "short"})
    assert bad.status_code == 422
    bot = client.post(
        "/contact",
        data={
            "csrf_token": token,
            "website": "spam",
            "email": "x@y.org",
            "message": "buy things now",
        },
    )
    assert bot.status_code == 200


def test_documents_served_under_site_csp(client: FlaskClient):
    pdf = client.get("/documents/missing_votes.pdf")
    assert pdf.status_code == 200 and pdf.mimetype == "application/pdf"
    for name in (
        "chair_independence_analysis",
        "compliance_cost_report",
        "hr27_timeout_analysis",
        "study_order_analysis",
    ):
        report = client.get(f"/documents/{name}.html")
        assert report.status_code == 200 and report.mimetype == "text/html"
        assert "'unsafe-inline'" not in report.headers["Content-Security-Policy"]
        html = report.get_data(as_text=True)
        assert re.search(r"<style|style=\"|<script|https://fonts", html) is None, name
        for sheet in re.findall(r'href="(/static/css/[^"]+)"', html):
            assert client.get(sheet).status_code == 200, sheet
    assert "max-age=86400" in pdf.headers["Cache-Control"]
    assert client.get("/documents/../config.py").status_code == 404
    assert client.get("/documents/nope.pdf").status_code == 404


def _with_owed_nothing() -> dict:
    bundle = bundle_dict("J45")
    bundle["bills"].append(
        {
            "bill_id": "H9999",
            "title": "An Act owing nothing",
            "url": "https://malegislature.gov/Bills/194/H9999",
        }
    )
    bundle["no_requirements"] = [
        {
            "tenure_id": "194/H9999:J45:2025-03-01",
            "bill_id": "H9999",
            "committee_id": "J45",
            "referred_on": "2025-03-01",
            "reason": "none_yet",
            "awaiting": ["votes_posted"],
        }
    ]
    return bundle


def test_referral_owing_nothing_shows_without_a_status(
    client: FlaskClient, pusher: Pusher, db: Connection
):
    pusher.push({"J45": bundle_dict("J45")})
    before = db.execute("SELECT * FROM committee_stats WHERE committee_id = 'J45'").fetchone()
    run_id = pusher.push({"J45": _with_owed_nothing()})

    after = db.execute("SELECT * FROM committee_stats WHERE committee_id = 'J45'").fetchone()
    for column in ("total", "compliant", "provisional", "non_compliant", "compliance_rate"):
        assert after[column] == before[column], column
    html = _text(client, "/bills/194/H9999")
    assert "No applicable requirements" in html
    assert "Nothing is owed yet. A vote record will be due once the committee reports." in html
    assert "has no status" in html
    assert "1 more referral to this committee owes nothing" in _text(client, "/?committee=J45")

    rebuilt = dumps.bundle(db, run_id, "J45")
    (item,) = rebuilt.no_requirements
    assert (item.tenure_id, item.reason.value, [a.value for a in item.awaiting]) == (
        "194/H9999:J45:2025-03-01",
        "none_yet",
        ["votes_posted"],
    )


def test_carried_bill_points_to_the_document_checked(
    client: FlaskClient, pusher: Pusher, db: Connection
):
    bundle = _with_owed_nothing()
    (item,) = bundle["no_requirements"]
    carrier = bundle["tenures"][0]["bill_id"]
    item |= {"reason": "accompanied", "awaiting": [], "carried_by": carrier}
    run_id = pusher.push({"J45": bundle})

    html = _text(client, "/bills/194/H9999")
    assert "Came before the committee" in html
    assert f'href="/bills/194/{carrier}"' in html
    assert "which is checked instead" in html
    assert "as part of a study order or new draft checked instead" in _text(
        client, "/?committee=J45"
    )

    (rebuilt,) = dumps.bundle(db, run_id, "J45").no_requirements
    assert (rebuilt.reason.value, rebuilt.carried_by) == ("accompanied", carrier)


def test_record_gap_shows_its_note(client: FlaskClient, pusher: Pusher, db: Connection):
    bundle = _with_owed_nothing()
    (item,) = bundle["no_requirements"]
    note = "The bill history does not record its transfer to the committee."
    item |= {"reason": "record_incomplete", "awaiting": [], "note": note}
    run_id = pusher.push({"J45": bundle})

    html = _text(client, "/bills/194/H9999")
    assert "Came before the committee" in html
    assert "does not show how this bill came before the committee" in html
    assert note in html
    assert "where the legislative record does not show" in _text(client, "/?committee=J45")

    (rebuilt,) = dumps.bundle(db, run_id, "J45").no_requirements
    assert (rebuilt.reason.value, rebuilt.note) == ("record_incomplete", note)
