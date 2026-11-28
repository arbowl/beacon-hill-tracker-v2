"""robots.txt and the sitemap."""

from datetime import date
from xml.etree import ElementTree

from flask import Flask
from flask.testing import FlaskClient

from tests.conftest import Pusher, bundle_dict
from tracker.updates import posts

_NS = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}


def _sitemap(client: FlaskClient) -> dict[str, str | None]:
    """lastmod by URL; fails on a repeated URL."""
    response = client.get("/sitemap.xml")
    assert response.status_code == 200 and response.mimetype == "application/xml"
    urls = ElementTree.fromstring(response.data).findall("s:url", _NS)
    found = {u.findtext("s:loc", namespaces=_NS): u.findtext("s:lastmod", None, _NS) for u in urls}
    assert len(found) == len(urls)
    return found


def test_robots_allows_everything_and_names_the_sitemap(client: FlaskClient):
    response = client.get("/robots.txt")
    assert response.status_code == 200 and response.mimetype == "text/plain"
    assert response.get_data(as_text=True) == (
        "User-agent: *\nDisallow:\n\nSitemap: http://localhost/sitemap.xml\n"
    )


def test_sitemap_urls_use_the_site_url(client: FlaskClient, app: Flask):
    app.config["TRACKER_SETTINGS"] = app.config["TRACKER_SETTINGS"].model_copy(
        update={"site_url": "https://tracker.example.org"}
    )
    assert all(url.startswith("https://tracker.example.org/") for url in _sitemap(client))
    assert "Sitemap: https://tracker.example.org/sitemap.xml" in client.get("/robots.txt").text


def test_sitemap_pages_resolve_and_are_their_own_canonical(client: FlaskClient, pusher: Pusher):
    pusher.push({"J45": bundle_dict("J45")})
    pages = [u for u in _sitemap(client) if "/bills/" not in u]
    assert "http://localhost/" in pages
    for url in pages:
        response = client.get(url.removeprefix("http://localhost"))
        assert response.status_code == 200, url
        assert f'<link rel="canonical" href="{url}">' in response.text, url
        assert 'name="robots"' not in response.text, url


def test_sitemap_lists_each_bill_page_once(client: FlaskClient, pusher: Pusher):
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
    pusher.push({"J19": bundle_dict("J19"), "J45": bundle})
    expected = {
        f"http://localhost/bills/194/{t['bill_id']}"
        for b in (bundle_dict("J19"), bundle)
        for t in [*b["tenures"], *b.get("no_requirements", [])]
    }
    assert "http://localhost/bills/194/H9999" in expected
    assert {u for u in _sitemap(client) if "/bills/" in u} == expected


def test_sitemap_dates_updates(client: FlaskClient, app: Flask):
    text = (
        '+++\ntitle = "A post"\npublished = 2026-01-02\nupdated = 2026-02-03\n'
        'category = "announcement"\nsummary = "Summary"\n+++\nBody\n'
    )
    posts.install(app, (posts.parse("a-post", text),))
    found = _sitemap(client)
    assert found["http://localhost/updates/a-post"] == date(2026, 2, 3).isoformat()
    assert found["http://localhost/"] is None
