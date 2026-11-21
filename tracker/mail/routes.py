"""One-click unsubscribe from any subscribed email.

The signed link is the credential, so these routes take no CSRF token: mail clients POST to the
link with the RFC 8058 body. Opening the link (GET) changes nothing.
"""

from typing import cast

from flask import Blueprint, current_app, render_template, request

from tracker.auth import accounts
from tracker.config import Settings
from tracker.db import pool
from tracker.display import EMAIL_KIND_SHORT
from tracker.enums import EmailKind
from tracker.mail import unsubscribe

bp = Blueprint("email", __name__, url_prefix="/email")

_HEADERS = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


def _texts(kinds: frozenset[EmailKind]) -> list[str]:
    return [EMAIL_KIND_SHORT[k] for k in EmailKind if k in kinds]


@bp.route("/unsubscribe", methods=["GET", "POST"], endpoint="unsubscribe")
def unsubscribe_link() -> tuple[str, int, dict[str, str]]:
    settings = cast(Settings, current_app.config["TRACKER_SETTINGS"])
    token = request.args.get("t", "")
    claim = unsubscribe.read(settings.secret_key, token)
    with pool().connection() as conn:
        user = None
        if claim is not None:
            user = conn.execute(
                "SELECT email FROM users WHERE id = %s", (claim.user_id,)
            ).fetchone()
        if claim is None or user is None:
            return render_template("pages/unsubscribe.html", claim=None), 400, _HEADERS
        context = dict(claim=claim, token=token, email=user["email"], kinds=_texts(claim.kinds))
        if request.method == "GET":
            return render_template("pages/unsubscribe.html", **context), 200, _HEADERS
        off = frozenset(EmailKind) if request.form.get("scope") == "all" else claim.kinds
        with conn.transaction():
            enabled = accounts.email_prefs(conn, claim.user_id)
            accounts.set_email_prefs(conn, claim.user_id, enabled - off)
    return render_template("pages/unsubscribe.html", done=_texts(off), **context), 200, _HEADERS
