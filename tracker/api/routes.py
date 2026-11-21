"""Public JSON API v1: read-only, keyed, rate-limited. Figures come from the dashboard's queries."""

from typing import Annotated, Any, cast

from flask import Blueprint, Response, current_app, g, jsonify, request, url_for
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from beacon_contract import v1
from tracker.api import limits, models, openapi
from tracker.auth.keys import authenticate
from tracker.bills import queries as bills
from tracker.config import Settings
from tracker.dashboard import queries
from tracker.dashboard.filters import DashboardFilters, normalize_bill_id
from tracker.db import pool
from tracker.enums import KeyScope
from tracker.publish import records
from tracker.web import site_status

bp = Blueprint("api", __name__, url_prefix="/api/v1")

_PUBLIC = {"api.openapi_document"}


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


class StatsQuery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    committee: Annotated[str, StringConstraints(pattern=v1.COMMITTEE_ID_PATTERN)] | None = None


def _settings() -> Settings:
    return cast(Settings, current_app.config["TRACKER_SETTINGS"])


def _error(status: int, code: str, message: str, details: tuple = ()) -> Response:
    body = v1.ErrorBody(error=v1.ErrorInfo(code=code, message=message, details=details))
    response = Response(body.model_dump_json(), status=status, mimetype="application/json")
    if status == 401:
        response.headers["WWW-Authenticate"] = 'Bearer realm="api"'
    return response


def _json(model: BaseModel) -> Response:
    response = Response(model.model_dump_json(), mimetype="application/json")
    response.headers["Cache-Control"] = "private, max-age=60"
    return response


@bp.before_request
def _authenticate() -> Response | None:
    if request.endpoint in _PUBLIC:
        return None
    scheme, _, secret = request.headers.get("Authorization", "").partition(" ")
    with pool().connection() as conn:
        owner = authenticate(conn, secret, KeyScope.READ) if scheme == "Bearer" and secret else None
        if owner is None:
            return _error(401, "unauthorized", "A read key is required: Authorization: Bearer ...")
        wait = limits.seconds_until_allowed(conn, owner.key_id, _settings().api_requests_per_minute)
    if wait:
        response = _error(429, "rate_limited", "Too many requests for this key; slow down")
        response.headers["Retry-After"] = str(wait)
        return response
    g.api_key = owner
    return None


@bp.errorhandler(ApiError)
def _api_error(exc: ApiError) -> Response:
    return _error(exc.status, exc.code, exc.message)


@bp.errorhandler(ValidationError)
def _invalid(exc: ValidationError) -> Response:
    details = tuple(
        v1.ErrorDetail(loc=tuple(e["loc"]), msg=e["msg"])
        for e in exc.errors(include_url=False, include_input=False)
    )
    return _error(400, "invalid", "Invalid query parameters", details)


def _session() -> int:
    status = site_status()
    if status is None:
        raise ApiError(404, "no_data", "No data has been published yet")
    return status.session


def _committee_item(row: dict[str, Any]) -> models.CommitteeItem:
    return models.CommitteeItem(
        committee_id=row["committee_id"],
        name=row["name"],
        chamber=row["chamber"],
        as_of=row["as_of"],
        url=url_for("dashboard.index", committee=row["committee_id"], _external=True),
    )


def _segment_item(row: dict[str, Any]) -> models.SegmentItem:
    return models.SegmentItem(
        tenure_id=row["tenure_id"],
        bill_id=row["bill_id"],
        title=row["title"],
        committee_id=row["committee_id"],
        committee_name=row["committee_name"],
        referred_on=row["referred_on"],
        referral_observed=row["referral_observed"],
        label=row["label"],
        hearing_notice=row["hearing_notice"],
        summary_posted=row["summary_posted"],
        votes_posted=row["votes_posted"],
        report_out_deadline=row["report_out"],
        report_due=row["report_due"],
        next_due=row["next_due"],
        url=url_for("bills.bill", session=row["session"], bill_id=row["bill_id"], _external=True),
    )


@bp.get("/openapi.json")
def openapi_document() -> Response:
    response = jsonify(openapi.document())
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@bp.get("/stats")
def stats() -> Response:
    query = StatsQuery.model_validate(request.args.to_dict())
    session = _session()
    with pool().connection() as conn:
        row = queries.stats(conn, session, query.committee)
    if row is None:
        raise ApiError(404, "not_found", f"No committee {query.committee} in session {session}")
    return _json(models.Stats.from_row(row))


@bp.get("/committees")
def committees() -> Response:
    if request.args:
        raise ApiError(400, "invalid", "This endpoint takes no query parameters")
    session = _session()
    with pool().connection() as conn:
        rows = queries.committees(conn, session)
    return _json(
        models.CommitteeList(session=session, committees=[_committee_item(r) for r in rows])
    )


@bp.get("/committees/<committee_id>")
def committee(committee_id: str) -> Response:
    session = _session()
    with pool().connection() as conn:
        row = next(
            (c for c in queries.committees(conn, session) if c["committee_id"] == committee_id),
            None,
        )
        stats_row = queries.stats(conn, session, committee_id) if row else None
    if row is None or stats_row is None:
        raise ApiError(404, "not_found", f"No committee {committee_id} in session {session}")
    return _json(
        models.CommitteeDetail(
            committee=_committee_item(row), stats=models.Stats.from_row(stats_row)
        )
    )


@bp.get("/segments")
def segments() -> Response:
    filters = DashboardFilters.from_args(request.args.to_dict())
    session = _session()
    with pool().connection() as conn:
        page = queries.segment_page(conn, session, filters)
    return _json(
        models.SegmentPage(
            session=session,
            total=page.total,
            next_cursor=page.next_cursor,
            segments=[_segment_item(r) for r in page.rows],
        )
    )


@bp.get("/bills/<int:session>/<bill_id>")
def bill(session: int, bill_id: str) -> Response:
    canonical = normalize_bill_id(bill_id)
    if canonical is None:
        raise ApiError(404, "not_found", f"{bill_id} is not a bill number")
    with pool().connection() as conn:
        rows = bills.bill_segments(conn, session, canonical)
        if not rows:
            raise ApiError(404, "not_found", f"No bill {canonical} in session {session}")
        found: list[models.BillSegment] = []
        clauses: dict[str, v1.Clause] = {}
        for row in rows:
            tenures, cited = records.bill_records(
                conn, row["run_id"], row["committee_id"], canonical
            )
            tenure = next(t for t in tenures if t.tenure_id == row["tenure_id"])
            found.append(models.BillSegment(segment=_segment_item(row), tenure=tenure))
            clauses |= {c.text_hash: c for c in cited}
    return _json(
        models.BillDetail(
            session=session,
            bill_id=canonical,
            title=rows[0]["title"],
            url=url_for("bills.bill", session=session, bill_id=canonical, _external=True),
            segments=found,
            clauses=sorted(clauses.values(), key=lambda c: c.text_hash),
        )
    )
