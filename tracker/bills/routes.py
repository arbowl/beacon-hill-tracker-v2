"""Bill permalink and lazily loaded audit trails."""

from typing import Any

from flask import Blueprint, abort, redirect, render_template, request, url_for
from werkzeug.wrappers import Response

from beacon_contract.v1 import ComplianceLabel
from tracker.bills import queries
from tracker.dashboard.filters import normalize_bill_id
from tracker.db import pool
from tracker.enums import WatchKind
from tracker.watch.button import watch_button
from tracker.web import site_status

bp = Blueprint("bills", __name__, url_prefix="/bills")

_OUTCOME = {
    ComplianceLabel.COMPLIANT: "no violation was detected",
    ComplianceLabel.PROVISIONAL: "it is provisional",
    ComplianceLabel.NON_COMPLIANT: "a violation was detected",
}
_COUNT_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five"}


def _summary(segments: list[dict[str, Any]]) -> str:
    """'This bill has two segments: it was referred to X, where ..., and later to Y, where ...'."""
    parts = []
    for n, s in enumerate(segments):
        where = (
            "where its referral was not found in the bill history"
            if not s["referral_observed"]
            else f"where {_OUTCOME[ComplianceLabel(s['label'])]}"
        )
        lead = (
            "it was referred to" if n == 0 else "later to" if n == len(segments) - 1 else "then to"
        )
        parts.append(f"{lead} the {s['committee_name']}, {where}")
    count = len(segments)
    noun = f"{_COUNT_WORDS.get(count, str(count))} segment{'s' if count > 1 else ''}"
    body = ", and ".join([", ".join(parts[:-1]), parts[-1]]) if count > 1 else parts[0]
    return f"This bill has {noun}: {body}."


_OWED_NOTHING_SUMMARY = (
    "No referral of this bill owes anything under the rules tracked here, so it has no status."
)


@bp.get("/<int:session>/<bill_id>")
def bill(session: int, bill_id: str) -> Response | str:
    canonical = normalize_bill_id(bill_id)
    if canonical is None:
        abort(404)
    if canonical != bill_id:
        return redirect(url_for("bills.bill", session=session, bill_id=canonical), 301)
    if site_status() is None:
        abort(404)
    with pool().connection() as conn:
        segments = queries.bill_segments(conn, session, bill_id)
        owed_nothing = queries.bill_owed_nothing(conn, session, bill_id)
        if not segments and not owed_nothing:
            abort(404)
        audits = {s["tenure_id"]: queries.audit(conn, session, s["tenure_id"]) for s in segments}
    first = segments[0] if segments else owed_nothing[0]
    return render_template(
        "bill.html",
        session=session,
        bill_id=bill_id,
        title=first["title"],
        bill_url=first["bill_url"],
        summary=_summary(segments) if segments else _OWED_NOTHING_SUMMARY,
        segments=segments,
        owed_nothing=owed_nothing,
        audits=audits,
        watch=watch_button(WatchKind.BILL, session, bill_id),
    )


@bp.get("/<int:session>/<bill_id>/audit")
def audit(session: int, bill_id: str) -> str:
    tenure_id = request.args.get("tenure", "")
    if not tenure_id.startswith(f"{session}/{bill_id}:"):
        abort(404)
    with pool().connection() as conn:
        result = queries.audit(conn, session, tenure_id)
    if result is None:
        abort(404)
    return render_template("partials/_audit.html", audit=result)
