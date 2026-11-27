"""Embeddable widgets and the embed builder."""

import re
from html import unescape

import pytest
from flask.testing import FlaskClient

from tests.conftest import Pusher, bundle_dict
from tracker.db import Connection

BUDGET_BYTES = 20 * 1024


@pytest.fixture
def published(pusher: Pusher) -> None:
    pusher.push({"J19": bundle_dict("J19"), "J45": bundle_dict("J45")})


def _widget(client: FlaskClient, url: str, status: int = 200) -> str:
    response = client.get(url)
    assert response.status_code == status, url
    assert "frame-ancestors *" in response.headers["Content-Security-Policy"]
    assert response.headers["Cache-Control"] == "public, max-age=300"
    assert "Set-Cookie" not in response.headers
    html = response.get_data(as_text=True)
    assert "<script" not in html and 'style="' not in html
    stylesheet = re.search(r'href="(/static/css/embed\.css[^"]*)"', html)[1]
    css = client.get(unescape(stylesheet)).get_data()
    assert len(response.get_data()) + len(css) < BUDGET_BYTES
    return html


def test_committee_widget(client: FlaskClient, db: Connection, published: None):
    html = _widget(client, "/embed/committee/J19")
    stats = db.execute("SELECT * FROM committee_stats WHERE committee_id = 'J19'").fetchone()
    assert "Joint Committee on the Judiciary" in html
    assert f"{stats['compliance_rate']:.1f}%" in html and "791 bill segments" in html
    assert "report_out" not in html and f"reported out {stats['report_out_unmet']}" in html
    assert "How detections work" in html and 'href="http://localhost/methodology"' in html
    assert 'class="theme-light"' in html
    assert 'class="theme-dark"' in _widget(client, "/embed/committee/J19?theme=dark")
    assert 'class="theme-light"' in _widget(client, "/embed/committee/J19?theme=neon")


def test_bill_widget(client: FlaskClient, published: None):
    html = _widget(client, "/embed/bill/194/h.10")
    assert ">H10<" in html and "Joint Committee on the Judiciary" in html
    assert "Violation detected" in html
    assert 'href="http://localhost/bills/194/H10"' in html


def test_missing_widgets_can_still_be_framed(client: FlaskClient, published: None):
    assert "no committee J99" in _widget(client, "/embed/committee/J99", 404)
    assert "no bill H999999" in _widget(client, "/embed/bill/194/H999999", 404)


def test_other_pages_refuse_framing(client: FlaskClient, published: None):
    for url in ("/", "/embed", "/bills/194/H10"):
        assert "frame-ancestors 'none'" in client.get(url).headers["Content-Security-Policy"]


def test_builder(client: FlaskClient, published: None):
    html = unescape(client.get("/embed").get_data(as_text=True))
    assert '<iframe src="http://localhost/embed/committee/J' in html
    html = unescape(
        client.get("/embed?kind=bill&bill=h.10&theme=dark&size=large").get_data(as_text=True)
    )
    assert '<iframe src="http://localhost/embed/bill/194/H10?theme=dark" width="600"' in html
    assert 'src="/embed/bill/194/H10?theme=dark"' in html
    assert "is not a bill number" in client.get("/embed?kind=bill&bill=zz").get_data(as_text=True)
    assert "could not be read" in client.get("/embed?size=huge").get_data(as_text=True)
    bill_page = client.get("/bills/194/H10").get_data(as_text=True)
    assert 'href="/embed?kind=bill&amp;bill=H10"' in bill_page


def test_aggregate_widgets(client: FlaskClient, db: Connection, published: None):
    session = db.execute("SELECT * FROM committee_stats WHERE committee_id = '*'").fetchone()
    html = _widget(client, "/embed/all?theme=dark")
    assert "All committees" in html and f"{session['total']:,} bill segments" in html
    assert 'class="theme-dark"' in html and 'href="http://localhost/"' in html
    joint = db.execute("SELECT * FROM committee_stats WHERE committee_id = 'J'").fetchone()
    html = _widget(client, "/embed/chamber/j")
    assert "Joint committees" in html and f"{joint['compliance_rate']:.1f}%" in html
    assert 'href="http://localhost/?committee=J"' in html
    assert "no house committees" in _widget(client, "/embed/chamber/H", 404)
    assert "no chamber X" in _widget(client, "/embed/chamber/x", 404)


def test_builder_aggregates(client: FlaskClient, published: None):
    html = unescape(client.get("/embed?kind=chamber&chamber=S").get_data(as_text=True))
    assert '<iframe src="http://localhost/embed/chamber/S?theme=light"' in html
    html = unescape(client.get("/embed?kind=all&theme=dark").get_data(as_text=True))
    assert '<iframe src="http://localhost/embed/all?theme=dark"' in html
    assert "could not be read" in client.get("/embed?kind=chamber&chamber=Q").get_data(as_text=True)
