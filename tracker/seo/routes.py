"""robots.txt and the XML sitemap for search engines."""

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from xml.etree import ElementTree

from flask import Blueprint, Response, url_for

from tracker.bills import queries as bills
from tracker.db import pool
from tracker.updates import posts
from tracker.web import site_url

bp = Blueprint("seo", __name__)

_NAMESPACE = "http://www.sitemaps.org/schemas/sitemap/0.9"
_PAGES = (
    "dashboard.index",
    "dashboard.committees",
    "pages.methodology",
    "pages.about",
    "pages.contact",
    "pages.press",
    "pages.privacy",
    "pages.terms",
    "changes.index",
    "updates.index",
)


@dataclass(frozen=True)
class SitemapEntry:
    path: str
    last_modified: date | None = None


def _entries() -> Iterator[SitemapEntry]:
    for endpoint in _PAGES:
        yield SitemapEntry(url_for(endpoint))
    for update in posts.published():
        yield SitemapEntry(
            url_for("updates.show", slug=update.slug),
            update.meta.updated or update.meta.published,
        )
    with pool().connection() as conn:
        refs = bills.live_bills(conn)
    for ref in refs:
        yield SitemapEntry(url_for("bills.bill", session=ref.session, bill_id=ref.bill_id))


def _public(response: Response) -> Response:
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@bp.get("/robots.txt")
def robots() -> Response:
    body = f"User-agent: *\nDisallow:\n\nSitemap: {site_url(url_for('seo.sitemap'))}\n"
    return _public(Response(body, mimetype="text/plain"))


@bp.get("/sitemap.xml")
def sitemap() -> Response:
    root = ElementTree.Element("urlset", xmlns=_NAMESPACE)
    for entry in _entries():
        url = ElementTree.SubElement(root, "url")
        ElementTree.SubElement(url, "loc").text = site_url(entry.path)
        if entry.last_modified is not None:
            ElementTree.SubElement(url, "lastmod").text = entry.last_modified.isoformat()
    body = ElementTree.tostring(root, encoding="utf-8", xml_declaration=True)
    return _public(Response(body, mimetype="application/xml"))
