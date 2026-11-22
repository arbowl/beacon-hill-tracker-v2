"""Template filters, per-request site status, security headers, and error pages."""

import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, Response, g, render_template, request
from werkzeug.exceptions import HTTPException

from beacon_contract.v1 import ComplianceLabel, ErrorBody, ErrorInfo
from tracker import display, segments
from tracker.auth.current import current_user, here
from tracker.dashboard.queries import SiteStatus
from tracker.dashboard.queries import site_status as query_site_status
from tracker.db import pool
from tracker.updates import posts


def content_security_policy(frame_ancestors: str = "'none'") -> str:
    """The site's CSP; only embeds pass frame_ancestors="*" so other sites can frame them."""
    return (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:;"
        " font-src 'self'; connect-src 'self'; base-uri 'self'; form-action 'self';"
        f" object-src 'none'; frame-ancestors {frame_ancestors}"
    )


def build_fingerprint() -> str:
    """Digest of the site's code, templates, and assets; changes when a deploy changes output."""
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]


def site_status() -> SiteStatus | None:
    """Newest session's status, queried at most once per request."""
    if "site_status" not in g:
        with pool().connection() as conn:
            g.site_status = query_site_status(conn)
    return g.site_status


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def _display_url(url: str) -> str:
    parts = urlsplit(url)
    return (parts.netloc.removeprefix("www.") + parts.path).rstrip("/")


def _security_headers(response: Response) -> Response:
    response.headers.setdefault("Content-Security-Policy", content_security_policy())
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=86400"
    return response


def _error(exc: HTTPException) -> Response | tuple[str, int]:
    if request.path.startswith("/api/"):
        code = (exc.name or "error").lower().replace(" ", "_")
        body = ErrorBody(error=ErrorInfo(code=code, message=exc.description or exc.name))
        return Response(body.model_dump_json(), status=exc.code or 500, mimetype="application/json")
    return render_template("error.html", error=exc), exc.code or 500


def register(app: Flask) -> None:
    app.add_template_global(site_status)
    app.add_template_global(current_user)
    app.add_template_global(here)
    app.add_template_global(posts.latest, "latest_update")
    app.add_template_filter(display.moment, "moment")
    app.add_template_filter(display.day, "day")
    app.add_template_filter(display.long_day, "long_day")
    app.add_template_filter(display.number, "number")
    app.add_template_filter(display.file_size, "file_size")
    app.add_template_filter(_ordinal, "ordinal")
    app.add_template_filter(_display_url, "display_url")
    app.add_template_filter(lambda v: display.LABEL_TEXT[ComplianceLabel(v)], "label_text")
    app.add_template_filter(display.CHANGE_KIND_TEXT.__getitem__, "change_kind")
    app.add_template_filter(posts.CATEGORY_TEXT.__getitem__, "category_text")
    app.add_template_filter(segments.label, "label_view")
    app.add_template_filter(segments.chips, "chips")
    app.after_request(_security_headers)
    for code in (400, 404, 405, 500):
        app.register_error_handler(code, _error)
