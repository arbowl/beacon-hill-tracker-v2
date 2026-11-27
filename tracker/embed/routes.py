"""Public widgets other sites can frame, and the builder that writes their iframe snippets.

Widgets read only precomputed tables, use no JavaScript or cookies, and are cached for five
minutes, so heavy embed traffic stays cheap.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Any, cast

from flask import Blueprint, Response, current_app, render_template, request, url_for
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from beacon_contract import v1
from tracker.bills import queries as bills
from tracker.config import Settings
from tracker.dashboard import queries
from tracker.dashboard.filters import CHAMBERS, normalize_bill_id
from tracker.db import pool
from tracker.web import content_security_policy, site_status

bp = Blueprint("embed", __name__, url_prefix="/embed")

MAX_BILL_SEGMENTS = 4


class Theme(StrEnum):
    LIGHT = "light"
    DARK = "dark"


class EmbedKind(StrEnum):
    COMMITTEE = "committee"
    CHAMBER = "chamber"
    ALL = "all"
    BILL = "bill"


class Size(StrEnum):
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"


SIZE_PIXELS = {Size.SMALL: (300, 240), Size.MEDIUM: (400, 240), Size.LARGE: (600, 240)}


class WidgetQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    theme: Theme = Theme.LIGHT


class BuilderQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    kind: EmbedKind = EmbedKind.COMMITTEE
    committee: Annotated[str, StringConstraints(pattern=v1.COMMITTEE_ID_PATTERN)] | None = None
    chamber: Annotated[str, StringConstraints(pattern=r"^[HSJ]$")] = "H"
    bill: Annotated[str, StringConstraints(strip_whitespace=True, max_length=20)] = ""
    theme: Theme = Theme.LIGHT
    size: Size = Size.MEDIUM


@dataclass(frozen=True)
class BarPart:
    css: str
    count: int
    x: float
    width: float


def _bar(stats: dict[str, Any]) -> list[BarPart]:
    """The label breakdown as widths on a 0 to 100 scale, for an SVG bar."""
    parts, x = [], 0.0
    for css, key in (
        ("compliant", "compliant"),
        ("provisional", "provisional"),
        ("noncompliant", "non_compliant"),
    ):
        width = 100 * stats[key] / stats["total"] if stats["total"] else 0
        parts.append(BarPart(css, stats[key], round(x, 3), round(width, 3)))
        x += width
    return parts


def _widget(template: str, status: int = 200, **context: Any) -> Response:
    context.setdefault("link", _site_url(url_for("dashboard.index")))
    body = render_template(template, methodology=_site_url(url_for("pages.methodology")), **context)
    response = Response(body, status=status)
    response.headers["Content-Security-Policy"] = content_security_policy(frame_ancestors="*")
    response.headers["Cache-Control"] = "public, max-age=300"
    return response


def _theme() -> Theme:
    try:
        return WidgetQuery.model_validate(request.args.to_dict()).theme
    except ValidationError:
        return Theme.LIGHT


def _missing(theme: Theme, what: str) -> Response:
    return _widget("embed/missing.html", 404, theme=theme, what=what)


@bp.get("/committee/<committee_id>")
def committee(committee_id: str) -> Response:
    theme = _theme()
    status = site_status()
    if status is None:
        return _missing(theme, "No data has been published yet.")
    with pool().connection() as conn:
        info = conn.execute(
            "SELECT c.name, cc.as_of FROM committees c"
            " JOIN committee_current cc USING (session, committee_id)"
            " WHERE c.session = %s AND c.committee_id = %s",
            (status.session, committee_id),
        ).fetchone()
        stats = queries.stats(conn, status.session, committee_id) if info else None
    if info is None or stats is None:
        return _missing(theme, f"The Tracker has no committee {committee_id}.")
    return _widget(
        "embed/committee.html",
        theme=theme,
        name=info["name"],
        as_of=info["as_of"],
        stats=stats,
        bar=_bar(stats),
        link=_site_url(url_for("dashboard.index", committee=committee_id)),
    )


def _aggregate(key: str, name: str, link: str) -> Response:
    """The committee widget for a stats row that spans committees: a chamber or the session."""
    theme = _theme()
    status = site_status()
    if status is None:
        return _missing(theme, "No data has been published yet.")
    with pool().connection() as conn:
        stats = queries.stats(conn, status.session, key)
    if stats is None:
        return _missing(theme, f"The Tracker has no {name.lower()}.")
    return _widget(
        "embed/committee.html",
        theme=theme,
        name=name,
        as_of=stats["as_of"],
        stats=stats,
        bar=_bar(stats),
        link=_site_url(link),
    )


@bp.get("/all")
def all_committees() -> Response:
    return _aggregate(queries.SESSION_ROW, "All committees", url_for("dashboard.index"))


@bp.get("/chamber/<letter>")
def chamber(letter: str) -> Response:
    letter = letter.upper()
    if letter not in CHAMBERS:
        return _missing(_theme(), f"The Tracker has no chamber {letter}.")
    return _aggregate(
        letter, f"{CHAMBERS[letter]} committees", url_for("dashboard.index", committee=letter)
    )


@bp.get("/bill/<int:session>/<bill_id>")
def bill(session: int, bill_id: str) -> Response:
    theme = _theme()
    canonical = normalize_bill_id(bill_id)
    with pool().connection() as conn:
        segments = bills.bill_segments(conn, session, canonical) if canonical else []
        as_of = None
        if segments:
            row = conn.execute(
                "SELECT max(as_of) AS as_of FROM committee_current"
                " WHERE session = %s AND committee_id = ANY(%s)",
                (session, [s["committee_id"] for s in segments]),
            ).fetchone()
            as_of = row["as_of"] if row else None
    if not segments:
        return _missing(theme, f"The Tracker has no bill {bill_id} in session {session}.")
    return _widget(
        "embed/bill.html",
        theme=theme,
        bill_id=canonical,
        title=segments[0]["title"],
        segments=segments[:MAX_BILL_SEGMENTS],
        hidden=max(0, len(segments) - MAX_BILL_SEGMENTS),
        as_of=as_of,
        link=_site_url(url_for("bills.bill", session=session, bill_id=canonical)),
    )


def _site_url(path: str) -> str:
    """Absolute link to the site: SITE_URL when set, else this request's host."""
    settings = cast(Settings, current_app.config["TRACKER_SETTINGS"])
    if settings.site_url is not None:
        return settings.site_link(path)
    return request.url_root.rstrip("/") + path


@bp.get("")
def builder() -> str:
    """Pick what to show, a theme, and a size; shows the snippet and a live preview."""
    status = site_status()
    try:
        choice = BuilderQuery.model_validate(
            {k: v for k, v in request.args.to_dict().items() if v != ""}
        )
    except ValidationError:
        choice, error = BuilderQuery(), "That choice could not be read; showing the defaults."
    else:
        error = None
    with pool().connection() as conn:
        committees = queries.committees(conn, status.session) if status else []
    src = None
    if status is not None:
        if choice.kind == EmbedKind.COMMITTEE:
            committee_id = choice.committee or (
                committees[0]["committee_id"] if committees else None
            )
            if committee_id:
                src = url_for("embed.committee", committee_id=committee_id, theme=choice.theme)
        elif choice.kind == EmbedKind.CHAMBER:
            src = url_for("embed.chamber", letter=choice.chamber, theme=choice.theme)
        elif choice.kind == EmbedKind.ALL:
            src = url_for("embed.all_committees", theme=choice.theme)
        else:
            bill_id = normalize_bill_id(choice.bill)
            if bill_id:
                src = url_for(
                    "embed.bill", session=status.session, bill_id=bill_id, theme=choice.theme
                )
            elif choice.bill:
                error = f"{choice.bill} is not a bill number, such as H10."
    width, height = SIZE_PIXELS[choice.size]
    return render_template(
        "embed/builder.html",
        choice=choice,
        committees=committees,
        chambers=list(CHAMBERS.items()),
        sizes=list(Size),
        themes=list(Theme),
        src=src,
        snippet_src=_site_url(src) if src else None,
        width=width,
        height=height,
        error=error,
    )
