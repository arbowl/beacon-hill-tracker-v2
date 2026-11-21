"""Synchronizer-token CSRF protection for form posts."""

import hmac
import secrets

from flask import abort, request, session

_KEY = "csrf"
FIELD = "csrf_token"
HEADER = "X-CSRF-Token"


def token() -> str:
    """The session's token, created on first use."""
    if _KEY not in session:
        session[_KEY] = secrets.token_urlsafe(32)
    return session[_KEY]


def protect() -> None:
    """Abort with 400 unless the request carries the session's token."""
    sent = request.form.get(FIELD) or request.headers.get(HEADER, "")
    expected = session.get(_KEY)
    if not expected or not hmac.compare_digest(sent, expected):
        abort(400, description="The form expired. Reload the page and try again.")
