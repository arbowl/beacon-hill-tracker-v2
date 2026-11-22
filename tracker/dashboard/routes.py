"""Dashboard and committee table."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    make_response,
    render_template,
    request,
    url_for,
)
from pydantic import ValidationError

from beacon_contract.v1 import ComplianceLabel
from tracker.auth.current import current_user
from tracker.changes import queries as changes
from tracker.changes.queries import ShownRelease
from tracker.dashboard import figures, queries
from tracker.dashboard.filters import PAGE_SIZE, SORT_TEXT, DashboardFilters
from tracker.db import pool
from tracker.display import LABEL_FILTER, LEGISLATURE_TZ
from tracker.enums import WatchKind
from tracker.updates import posts
from tracker.watch.button import watch_button
from tracker.web import site_status

bp = Blueprint("dashboard", __name__)

STATUS_OPTIONS = [
    (LABEL_FILTER[label], label)
    for label in (
        ComplianceLabel.COMPLIANT,
        ComplianceLabel.PROVISIONAL,
        ComplianceLabel.NON_COMPLIANT,
    )
]


@dataclass(frozen=True)
class HeroNotice:
    label: str
    day: date
    text: str
    href: str


def _hero_notices(release: ShownRelease | None, update: posts.Update | None) -> list[HeroNotice]:
    """Recent methods release and site update, oldest first."""
    found = []
    if release is not None:
        href = url_for("changes.index", _anchor=f"v{release.version}")
        text = f"What changed in version {release.version}"
        found.append(HeroNotice("Methods update", release.released_on, text, href))
    if update is not None:
        href = url_for("updates.show", slug=update.slug)
        found.append(HeroNotice("Site update", update.meta.published, update.meta.title, href))
    return sorted(found, key=lambda n: n.day)


def _cached(as_of: datetime, key: str) -> tuple[str, bool]:
    """ETag for this data version, build, and request; True if a signed-out client has it."""
    build = current_app.config["TRACKER_BUILD"]
    digest = hashlib.sha256(f"{build}|{key}|{request.headers.get('HX-Request', '')}".encode())
    etag = f"{int(as_of.timestamp())}-{digest.hexdigest()[:16]}"
    return etag, current_user() is None and etag in request.if_none_match


def _respond(body: str, etag: str) -> Response:
    """Signed-out pages are shared and cacheable; signed-in pages carry per-user controls."""
    response = make_response(body)
    if current_user() is None:
        response.set_etag(etag)
        response.headers["Cache-Control"] = "public, max-age=60"
    else:
        response.headers["Cache-Control"] = "private, no-cache"
    response.headers["Vary"] = "HX-Request, Cookie"
    return response


@bp.get("/")
def index() -> Response:
    try:
        filters = DashboardFilters.from_args(request.args.to_dict())
    except ValidationError as exc:
        abort(400, description=_describe(exc))
    status = site_status()
    if status is None:
        return make_response(render_template("empty.html"))
    today = datetime.now(LEGISLATURE_TZ).date()
    with pool().connection() as conn:
        notice = changes.notice(conn, today)
    update = posts.recent(today)
    notice_key = f"{notice.content_hash if notice else ''}|{update.slug if update else ''}"
    etag, fresh = _cached(status.as_of, f"{filters.query_string(after=filters.after)}|{notice_key}")
    if fresh:
        return Response(status=304, headers={"ETag": f'"{etag}"'})
    session = status.session
    with pool().connection() as conn:
        committee_rows = queries.committees(conn, session)
        selected = next((c for c in committee_rows if c["committee_id"] == filters.committee), None)
        stats = queries.stats(conn, session, filters.committee)
        page = queries.segment_page(conn, session, filters)
        context = dict(
            filters=filters,
            page=page,
            status=status,
            session=session,
            stats=stats,
            committees=committee_rows,
            selected_committee=selected,
            deltas=figures.deltas(
                conn,
                session,
                filters.committee or queries.SESSION_ROW,
                stats["compliance_rate"] if stats else None,
                datetime.now(UTC),
            ),
            chart_data=figures.chart_data(stats) if stats else None,
            funnel_labels=figures.FUNNEL_STAGES,
            strip=figures.strip(conn, session, filters.committee),
            insights=conn.execute(
                "SELECT * FROM insights WHERE session = %s ORDER BY position", (session,)
            ).fetchall(),
            notices=_hero_notices(notice, update),
            committee_watch=watch_button(WatchKind.COMMITTEE, session, filters.committee)
            if filters.committee
            else None,
            status_options=STATUS_OPTIONS,
            sort_options=list(SORT_TEXT.items()),
            page_size=PAGE_SIZE,
            share_url=request.url_root.rstrip("/") + "/?" + filters.query_string()
            if filters.query_string()
            else request.url_root,
        )
    if not request.headers.get("HX-Request"):
        return _respond(render_template("dashboard.html", **context), etag)
    response = _respond(render_template("partials/_fragment.html", **context), etag)
    query = filters.query_string(after=filters.after)
    response.headers["HX-Push-Url"] = f"/?{query}" if query else "/"
    return response


@bp.get("/committees")
def committees() -> Response:
    status = site_status()
    if status is None:
        return make_response(render_template("empty.html"))
    with pool().connection() as conn:
        rows = conn.execute(
            "SELECT s.*, c.name FROM committee_stats s"
            " JOIN committees c USING (session, committee_id)"
            " WHERE s.session = %s ORDER BY c.name, s.committee_id",
            (status.session,),
        ).fetchall()
    etag, fresh = _cached(status.as_of, "committees")
    if fresh:
        return Response(status=304, headers={"ETag": f'"{etag}"'})
    return _respond(render_template("committees.html", rows=rows), etag)


def _describe(exc: ValidationError) -> str:
    fields = sorted({str(e["loc"][0]) for e in exc.errors() if e["loc"]})
    return "Invalid filter value: " + ", ".join(fields) if fields else "Invalid filters"
