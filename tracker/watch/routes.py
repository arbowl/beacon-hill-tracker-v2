"""Watchlist page, Watch buttons, per-watch alerts, and email preferences."""

from typing import Annotated, cast

from flask import (
    Blueprint,
    Response,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from tracker import csrf
from tracker.auth import accounts, keys
from tracker.auth.current import require_user, safe_next
from tracker.auth.keys import NewKey
from tracker.auth.sessions import SessionUser
from tracker.config import Settings
from tracker.db import pool
from tracker.display import ALERT_KIND_TEXT, EMAIL_KIND_SHORT, email_kind_groups
from tracker.enums import AlertKind, DumpFormat, EmailKind, KeyScope, UserRole
from tracker.publish import dumps
from tracker.watch import queries
from tracker.watch.button import WatchButton
from tracker.watch.queries import WatchTarget
from tracker.web import site_status

bp = Blueprint("watch", __name__, url_prefix="/watchlist")

_SIGNUPS_SHOWN = 50


def _private(body: str, status: int = 200) -> Response:
    response = Response(body, status=status)
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _page(user: SessionUser, new_key: NewKey | None = None, key_error: str | None = None) -> str:
    status = site_status()
    with pool().connection() as conn:
        watches = queries.watchlist(conn, user.user_id)
        enabled = accounts.email_prefs(conn, user.user_id)
        downloads = dumps.current(conn, status.session) if status else []
        api_keys = keys.live_keys(conn, user.user_id, KeyScope.READ)
        webmaster = user.role == UserRole.WEBMASTER
        signup_summary = accounts.signup_summary(conn) if webmaster else None
        signups = accounts.recent_signups(conn, _SIGNUPS_SHOWN) if webmaster else []
    return render_template(
        "watchlist.html",
        user=user,
        watches=watches,
        enabled=enabled,
        groups=email_kind_groups(),
        alert_kinds=list(ALERT_KIND_TEXT.items()),
        downloads=downloads,
        api_keys=api_keys,
        new_key=new_key,
        key_error=key_error,
        signup_summary=signup_summary,
        signups=signups,
        email_kind_short=EMAIL_KIND_SHORT,
        signup_limit=_SIGNUPS_SHOWN,
    )


@bp.get("")
def index() -> Response:
    return _private(_page(require_user()))


class KeyForm(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]


@bp.post("/keys")
def create_key() -> Response:
    """Create a read key and show it on this response only; it is never stored or shown again."""
    csrf.protect()
    user = require_user()
    limit = cast(Settings, current_app.config["TRACKER_SETTINGS"]).api_keys_per_user
    try:
        form = KeyForm.model_validate(request.form.to_dict())
    except ValidationError:
        return _private(_page(user, key_error="Name the key in 1 to 80 characters."), 422)
    with pool().connection() as conn:
        if len(keys.live_keys(conn, user.user_id, KeyScope.READ)) >= limit:
            error = f"An account can hold {limit} keys. Revoke one to create another."
            return _private(_page(user, key_error=error), 422)
        new_key = keys.create_key(conn, user.user_id, KeyScope.READ, form.name)
    return _private(_page(user, new_key=new_key))


@bp.post("/keys/<int:key_id>/revoke")
def revoke_key(key_id: int) -> Response:
    csrf.protect()
    user = require_user()
    with pool().connection() as conn:
        if not keys.revoke(conn, user.user_id, key_id):
            abort(404)
    flash("Key revoked. Requests with it are refused from now on.")
    return redirect(url_for("watch.index") + "#api-keys", 303)


@bp.get("/downloads/<any('csv.gz', 'json.gz'):fmt>")
def download(fmt: str) -> Response:
    """The newest file of a format for the newest session, with its sha256 and as-of time."""
    require_user()
    status = site_status()
    if status is None:
        abort(404)
    with pool().connection() as conn:
        found = {d.format: d for d in dumps.current(conn, status.session)}
        dump = found.get(DumpFormat(fmt))
        if dump is None:
            abort(404)
        data = dumps.contents(conn, dump.id)
    response = Response(data, mimetype="application/gzip")
    filename = f"beacon-hill-tracker-{dump.session}-current.{fmt}"
    response.headers["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.headers["X-Tracker-SHA256"] = dump.sha256
    response.headers["X-Tracker-As-Of"] = dump.as_of.isoformat()
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _posted_target() -> WatchTarget:
    try:
        return WatchTarget.model_validate(request.form.to_dict())
    except ValidationError:
        abort(400, description="That watch request could not be read.")


def _after_watch_change(target: WatchTarget, watching: bool) -> Response:
    next_path = safe_next(request.form.get("next")) or url_for("watch.index")
    if request.headers.get("HX-Request"):
        button = WatchButton(target, watching, next_path)
        return _private(render_template("partials/_watch.html", watch=button))
    return redirect(next_path, 303)


@bp.post("/watch")
def add() -> Response:
    csrf.protect()
    user = require_user()
    target = _posted_target()
    with pool().connection() as conn:
        if not queries.target_exists(conn, target):
            abort(404)
        alerts = accounts.default_alerts(accounts.email_prefs(conn, user.user_id))
        queries.add(conn, user.user_id, target, alerts)
    return _after_watch_change(target, watching=True)


@bp.post("/unwatch")
def remove() -> Response:
    csrf.protect()
    user = require_user()
    target = _posted_target()
    with pool().connection() as conn:
        queries.remove(conn, user.user_id, target)
    return _after_watch_change(target, watching=False)


def _posted_kinds[K: (AlertKind, EmailKind)](field: str, kind: type[K]) -> frozenset[K]:
    values = request.form.getlist(field)
    try:
        return frozenset(kind(v) for v in values)
    except ValueError:
        abort(400, description="That form could not be read.")


@bp.post("/watches/<int:watch_id>/alerts")
def alerts(watch_id: int) -> Response:
    csrf.protect()
    user = require_user()
    chosen = _posted_kinds("alert", AlertKind)
    with pool().connection() as conn:
        if not queries.set_alerts(conn, user.user_id, watch_id, chosen):
            abort(404)
    if request.headers.get("HX-Request"):
        return Response(status=204)
    flash("Alerts saved.")
    return redirect(url_for("watch.index"), 303)


@bp.post("/preferences")
def preferences() -> Response:
    csrf.protect()
    user = require_user()
    chosen = _posted_kinds("email_kind", EmailKind)
    with pool().connection() as conn:
        accounts.set_email_prefs(conn, user.user_id, chosen)
    flash("Email preferences saved.")
    return redirect(url_for("watch.index") + "#preferences", 303)
