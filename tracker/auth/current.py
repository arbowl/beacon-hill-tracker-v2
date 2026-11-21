"""The signed-in user for this request, the session cookie, and post-sign-in redirects."""

from datetime import UTC, datetime
from typing import cast
from urllib.parse import urlsplit

from flask import abort, current_app, g, redirect, request, session, url_for
from werkzeug.wrappers import Response

from tracker.auth import sessions
from tracker.auth.sessions import SessionUser
from tracker.config import Settings
from tracker.db import pool

COOKIE = "bht_session"
_MAX_NEXT_CHARS = 500


def _settings() -> Settings:
    return cast(Settings, current_app.config["TRACKER_SETTINGS"])


def current_user() -> SessionUser | None:
    """The session cookie's user, looked up at most once per request."""
    if "current_user" not in g:
        session_id = request.cookies.get(COOKIE)
        user = None
        if session_id:
            with pool().connection() as conn:
                user = sessions.lookup(conn, session_id, datetime.now(UTC))
        g.current_user = user
    return cast(SessionUser | None, g.current_user)


def here() -> str:
    """This request's path and query string."""
    return request.full_path.rstrip("?")


def safe_next(value: str | None) -> str | None:
    """A same-site path to return to after signing in, or None."""
    if not value or len(value) > _MAX_NEXT_CHARS or "\\" in value:
        return None
    parts = urlsplit(value)
    if parts.scheme or parts.netloc or not value.startswith("/") or value.startswith("//"):
        return None
    return value


def require_user() -> SessionUser:
    """The signed-in user; otherwise ends the request with a redirect to sign in."""
    user = current_user()
    if user is None:
        target = safe_next(request.form.get("next")) if request.method == "POST" else here()
        abort(redirect(url_for("auth.signin", next=target)))
    return user


def start_session(response: Response, user_id: int) -> Response:
    """Sign the browser in; also resets the CSRF token."""
    settings = _settings()
    with pool().connection() as conn:
        session_id = sessions.create(conn, user_id, settings.session_days)
    session.clear()
    response.set_cookie(
        COOKIE,
        session_id,
        max_age=settings.session_days * 86400,
        secure=settings.secure_cookies,
        httponly=True,
        samesite="Lax",
    )
    return response


def end_session(response: Response, everywhere: bool) -> Response:
    """Sign this browser out, and with everywhere, every other session of the same user."""
    session_id = request.cookies.get(COOKIE)
    user = current_user()
    with pool().connection() as conn:
        if everywhere and user is not None:
            sessions.end_all(conn, user.user_id)
        elif session_id:
            sessions.end(conn, session_id)
    session.clear()
    response.delete_cookie(COOKIE, secure=_settings().secure_cookies, httponly=True, samesite="Lax")
    return response
