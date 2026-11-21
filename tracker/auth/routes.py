"""Sign-up, sign-in by emailed link, and sign-out."""

import logging
from email.message import EmailMessage
from typing import Annotated, cast

from flask import Blueprint, current_app, redirect, render_template, request, url_for
from pydantic import BaseModel, ConfigDict, EmailStr, StringConstraints, ValidationError
from werkzeug.wrappers import Response

from tracker import csrf
from tracker.auth import accounts, links
from tracker.auth.current import current_user, end_session, safe_next, start_session
from tracker.config import Settings
from tracker.db import pool
from tracker.display import email_kind_groups
from tracker.enums import SIGNUP_DEFAULTS, EmailKind
from tracker.mail import MailNotConfigured, send
from tracker.mail.compose import compose

bp = Blueprint("auth", __name__)
log = logging.getLogger(__name__)

_NO_STORE = {"Cache-Control": "no-store"}
Page = tuple[str, int, dict[str, str]]
_TOKEN = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class SignInForm(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    email: EmailStr
    next: str | None = None


class SignUpForm(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    email: EmailStr
    email_kind: frozenset[EmailKind] = frozenset()


class VerifyForm(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    t: _TOKEN


def _settings() -> Settings:
    return cast(Settings, current_app.config["TRACKER_SETTINGS"])


def _ip() -> str:
    return request.remote_addr or "0.0.0.0"


def _verify_url(token: str) -> str:
    return _settings().site_link(url_for("auth.verify", t=token))


def _form_page(template: str, status: int, **context: object) -> Page:
    minutes = _settings().login_link_minutes
    return render_template(template, minutes=minutes, **context), status, _NO_STORE


def _send_or_error(message: EmailMessage, template: str, **context: object) -> Page:
    """Send the message and show 'check your email', or the form again with the failure."""
    try:
        send(_settings(), message)
    except (MailNotConfigured, OSError):
        log.exception("sign-in email failed")
        return _form_page(
            template,
            502,
            errors=["The email could not be sent. Please try again later."],
            **context,
        )
    return _form_page("auth/check_email.html", 200)


def _admitted(email: str) -> bool:
    settings = _settings()
    with pool().connection() as conn:
        return links.admit_request(
            conn,
            email,
            _ip(),
            settings.login_limit_per_email,
            settings.login_limit_per_ip_per_hour,
        )


_BAD_EMAIL = "Enter a valid email address."
_THROTTLED = "Too many sign-in emails were requested. Please try again in 15 minutes."


@bp.route("/signin", methods=["GET", "POST"])
def signin() -> Response | Page:
    settings = _settings()
    next_path = safe_next(request.values.get("next"))
    context = dict(available=settings.links_configured, next_path=next_path, form=request.form)
    if current_user() is not None and request.method == "GET":
        return redirect(next_path or url_for("watch.index"))
    if request.method == "GET":
        return _form_page("auth/signin.html", 200, **context)
    csrf.protect()
    if not settings.links_configured:
        return _form_page("auth/signin.html", 503, **context)
    try:
        form = SignInForm.model_validate(request.form.to_dict())
    except ValidationError:
        return _form_page("auth/signin.html", 422, errors=[_BAD_EMAIL], **context)
    email = str(form.email)
    if not _admitted(email):
        return _form_page("auth/signin.html", 429, errors=[_THROTTLED], **context)
    with pool().connection() as conn:
        account = accounts.find(conn, email)
        if account is not None and account.disabled:
            return _form_page("auth/check_email.html", 200)
        if account is None:
            message = compose(
                settings, email, "no_account", signup_url=settings.site_link(url_for("auth.signup"))
            )
        else:
            token = links.issue(conn, email, _ip(), settings.login_link_minutes, None, next_path)
            message = compose(
                settings,
                email,
                "signin",
                url=_verify_url(token),
                minutes=settings.login_link_minutes,
                already_registered=False,
            )
    return _send_or_error(message, "auth/signin.html", **context)


@bp.route("/signup", methods=["GET", "POST"])
def signup() -> Response | Page:
    settings = _settings()
    context = dict(available=settings.links_configured, groups=email_kind_groups())
    if current_user() is not None and request.method == "GET":
        return redirect(url_for("watch.index"))
    if request.method == "GET":
        return _form_page("auth/signup.html", 200, email="", checked=SIGNUP_DEFAULTS, **context)
    csrf.protect()
    raw = {"email": request.form.get("email", ""), "email_kind": request.form.getlist("email_kind")}
    checked = frozenset(k for k in EmailKind if k.value in raw["email_kind"])
    context |= dict(email=raw["email"], checked=checked)
    if not settings.links_configured:
        return _form_page("auth/signup.html", 503, **context)
    if request.form.get("website"):
        return _form_page("auth/check_email.html", 200)
    try:
        form = SignUpForm.model_validate(raw)
    except ValidationError:
        return _form_page("auth/signup.html", 422, errors=[_BAD_EMAIL], **context)
    email = str(form.email)
    if not _admitted(email):
        return _form_page("auth/signup.html", 429, errors=[_THROTTLED], **context)
    with pool().connection() as conn:
        account = accounts.find(conn, email)
        if account is not None and account.disabled:
            return _form_page("auth/check_email.html", 200)
        prefs = form.email_kind if account is None else None
        token = links.issue(conn, email, _ip(), settings.login_link_minutes, prefs, None)
    message = compose(
        settings,
        email,
        "signup" if account is None else "signin",
        url=_verify_url(token),
        minutes=settings.login_link_minutes,
        already_registered=account is not None,
    )
    return _send_or_error(message, "auth/signup.html", **context)


@bp.get("/signin/verify")
def verify() -> Page:
    token = request.args.get("t", "")
    with pool().connection() as conn:
        live = bool(token) and links.is_live(conn, token)
    status = 200 if live else 410
    page, status, headers = _form_page("auth/verify.html", status, token=token, live=live)
    return page, status, headers | {"Referrer-Policy": "no-referrer"}


@bp.post("/signin/verify")
def verify_confirm() -> Response | Page:
    csrf.protect()
    try:
        form = VerifyForm.model_validate(request.form.to_dict())
    except ValidationError:
        return _form_page("auth/verify.html", 410, token="", live=False)
    with pool().connection() as conn, conn.transaction():
        claim = links.consume(conn, form.t)
        user_id = None if claim is None else accounts.sign_in(conn, claim)
    if claim is None or user_id is None:
        return _form_page("auth/verify.html", 410, token="", live=False)
    target = safe_next(claim.next_path) or url_for("watch.index")
    return start_session(redirect(target, 303), user_id)


@bp.post("/signout")
def signout() -> Response:
    csrf.protect()
    everywhere = request.form.get("everywhere") == "1"
    return end_session(redirect(url_for("dashboard.index"), 303), everywhere)
