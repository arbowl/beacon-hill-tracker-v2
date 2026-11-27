"""Link preview card images for the dashboard, committees, and bills.

Cards are drawn on request and kept in a small per-process cache. Their URLs carry the data
version (?v=), so a new publication or deploy gives every card a new URL, which is the only
reliable way to make Slack, Facebook, and X fetch an image again.
"""

import re
import threading
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

from flask import Blueprint, Response, abort, current_app, g, request, url_for

from beacon_contract.v1 import ComplianceLabel
from tracker.bills import queries as bills
from tracker.cards import render
from tracker.dashboard import queries
from tracker.dashboard.filters import CHAMBERS, COMMITTEE_OR_CHAMBER, normalize_bill_id
from tracker.db import pool
from tracker.display import long_day
from tracker.web import _ordinal, site_status, site_url

bp = Blueprint("cards", __name__, url_prefix="/cards")

CACHE_SIZE = 256
_cache: OrderedDict[tuple[str, str], bytes] = OrderedDict()
_lock = threading.Lock()


def version() -> str | None:
    """Changes whenever published data or the site's code does; None before any data.

    Every publish and rollback rebuild writes a session snapshot, so its newest time changes
    even when a rollback leaves the newest as_of where it was.
    """
    if "card_version" not in g:
        status = site_status()
        row = None
        if status is not None:
            with pool().connection() as conn:
                row = conn.execute(
                    "SELECT max(taken_at) AS t FROM snapshots"
                    " WHERE session = %s AND committee_id = %s",
                    (status.session, queries.SESSION_ROW),
                ).fetchone()
        g.card_version = (
            f"{int(row['t'].timestamp() * 1000)}-{current_app.config['TRACKER_BUILD'][:8]}"
            if row and row["t"]
            else None
        )
    return g.card_version


def card_url(endpoint: str, **values: Any) -> str | None:
    """Absolute, versioned URL of a card, for og:image; None before any data is published."""
    v = version()
    return site_url(url_for(endpoint, v=v, **values)) if v else None


def _png(draw: Callable[[], bytes | None]) -> Response:
    """The card for this path at the current version, drawn at most once per process."""
    v = version()
    if v is None:
        abort(404)
    key = (request.path, v)
    with _lock:
        body = _cache.get(key)
        if body is not None:
            _cache.move_to_end(key)
    if body is None:
        body = draw()
        if body is None:
            abort(404)
        with _lock:
            _cache[key] = body
            while len(_cache) > CACHE_SIZE:
                _cache.popitem(last=False)
    response = Response(body, mimetype="image/png")
    response.set_etag(f"{v}-{len(body)}")
    # A request for the current version can be cached for good; any other may change soon.
    current = request.args.get("v") == v
    response.headers["Cache-Control"] = (
        "public, max-age=31536000, immutable" if current else "public, max-age=300"
    )
    return response.make_conditional(request)


def _figures(title: str, subtitle: str, row: dict[str, Any], as_of: Any) -> bytes:
    return render.figures_card(
        render.Figures(
            title=title,
            subtitle=subtitle,
            rate=float(row["compliance_rate"] or 0),
            total=row["total"],
            compliant=row["compliant"],
            provisional=row["provisional"],
            non_compliant=row["non_compliant"],
            as_of=long_day(as_of),
        )
    )


@bp.get("/site.png")
def site() -> Response:
    def draw() -> bytes | None:
        status = site_status()
        assert status is not None
        with pool().connection() as conn:
            row = queries.stats(conn, status.session, None)
            count = len(queries.committees(conn, status.session))
        if row is None:
            return None
        subtitle = (
            f"{row['bills_total']:,} bills across {count} committees"
            f" · {_ordinal(status.session)} General Court"
        )
        return _figures("Is Beacon Hill following its own rules?", subtitle, row, row["as_of"])

    return _png(draw)


@bp.get("/committee/<committee_id>.png")
def committee(committee_id: str) -> Response:
    if not re.match(COMMITTEE_OR_CHAMBER, committee_id):
        abort(404)

    def draw() -> bytes | None:
        status = site_status()
        assert status is not None
        with pool().connection() as conn:
            row = queries.stats(conn, status.session, committee_id)
            rows = queries.committees(conn, status.session)
        if row is None:
            return None
        court = f"{_ordinal(status.session)} General Court"
        if committee_id in CHAMBERS:
            count = sum(c["committee_id"][0] == committee_id for c in rows)
            title = f"{CHAMBERS[committee_id]} committees"
            subtitle = f"{count} committees · {court}"
        else:
            name = next((c["name"] for c in rows if c["committee_id"] == committee_id), None)
            if name is None:
                return None
            title, subtitle = name, f"{committee_id} · {court}"
        return _figures(title, subtitle, row, row["as_of"])

    return _png(draw)


@bp.get("/bill/<int:session>/<bill_id>.png")
def bill(session: int, bill_id: str) -> Response:
    if normalize_bill_id(bill_id) != bill_id:
        abort(404)

    def draw() -> bytes | None:
        with pool().connection() as conn:
            segments = bills.bill_segments(conn, session, bill_id)
            owed_nothing = bills.bill_owed_nothing(conn, session, bill_id)
            as_of = conn.execute(
                "SELECT max(as_of) AS as_of FROM committee_current"
                " WHERE session = %s AND committee_id = ANY(%s)",
                (session, [s["committee_id"] for s in [*segments, *owed_nothing]]),
            ).fetchone()
        if (not segments and not owed_nothing) or as_of is None or as_of["as_of"] is None:
            return None
        first = segments[0] if segments else owed_nothing[0]
        chamber = "House" if bill_id.startswith("H") else "Senate"
        rows = [render.Segment(s["committee_name"], ComplianceLabel(s["label"])) for s in segments]
        rows += [render.Segment(s["committee_name"], None) for s in owed_nothing]
        return render.bill_card(
            render.Bill(
                bill_id=bill_id,
                kicker=f"{bill_id} · {_ordinal(session)} General Court · Filed in the {chamber}",
                title=first["title"] or bill_id,
                segments=rows,
                as_of=long_day(as_of["as_of"]),
            )
        )

    return _png(draw)
