"""Static pages and the contact form."""

import logging
from email.message import EmailMessage
from typing import Annotated, cast

from flask import Blueprint, current_app, render_template, request
from pydantic import BaseModel, ConfigDict, EmailStr, StringConstraints, ValidationError

from tracker import csrf
from tracker.config import Settings
from tracker.db import pool
from tracker.display import clause_long
from tracker.mail import MailNotConfigured, send

bp = Blueprint("pages", __name__)
log = logging.getLogger(__name__)


@bp.get("/methodology")
def methodology() -> str:
    with pool().connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT ON (source, rule, paragraph) source, rule, paragraph, text"
            " FROM clauses ORDER BY source, rule, paragraph, first_seen_at DESC"
        ).fetchall()
    rule_order = {"JOINT": 0, "SENATE": 1, "HOUSE": 2}
    clauses = [
        r | {"label": clause_long(r["source"], r["rule"], r["paragraph"])}
        for r in sorted(
            rows, key=lambda r: (rule_order[r["source"]], _rule_key(r["rule"]), r["paragraph"] or 0)
        )
    ]
    return render_template("pages/methodology.html", clauses=clauses)


def _rule_key(rule: str) -> tuple[int, str]:
    digits = "".join(c for c in rule if c.isdigit())
    return int(digits or 0), rule


@bp.get("/about")
def about() -> str:
    return render_template("pages/about.html")


@bp.get("/press")
def press() -> str:
    return render_template("pages/press.html")


@bp.get("/privacy")
def privacy() -> str:
    return render_template("pages/privacy.html")


@bp.get("/terms")
def terms() -> str:
    return render_template("pages/terms.html")


class ContactForm(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    name: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = ""
    email: EmailStr
    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=10, max_length=5000)
    ]


_FIELD_TEXT = {
    "email": "Enter a valid email address.",
    "message": "Write a message of 10 to 5,000 characters.",
    "name": "Keep the name under 200 characters.",
}


def _settings() -> Settings:
    return cast(Settings, current_app.config["TRACKER_SETTINGS"])


def _over_limit(ip: str, limit: int) -> bool:
    """Record this submission and report whether the address exceeded the hourly limit."""
    with pool().connection() as conn:
        conn.execute(
            "DELETE FROM contact_submissions WHERE submitted_at < now() - interval '1 day'"
        )
        conn.execute("INSERT INTO contact_submissions (ip) VALUES (%s)", (ip,))
        row = conn.execute(
            "SELECT count(*) AS n FROM contact_submissions"
            " WHERE ip = %s AND submitted_at > now() - interval '1 hour'",
            (ip,),
        ).fetchone()
    return row is not None and row["n"] > limit


@bp.route("/contact", methods=["GET", "POST"])
def contact() -> tuple[str, int] | str:
    settings = _settings()
    if request.method == "GET":
        return render_template("pages/contact.html", available=settings.contact_configured, form={})
    csrf.protect()
    if not settings.contact_configured:
        return render_template("pages/contact.html", available=False, form=request.form), 503
    if request.form.get("website"):
        return render_template("pages/contact_sent.html")
    try:
        form = ContactForm.model_validate(request.form.to_dict())
    except ValidationError as exc:
        errors = sorted({_FIELD_TEXT[str(e["loc"][0])] for e in exc.errors() if e["loc"]})
        return render_template(
            "pages/contact.html", available=True, errors=errors, form=request.form
        ), 422
    if _over_limit(request.remote_addr or "0.0.0.0", settings.contact_limit_per_hour):
        return render_template(
            "pages/contact.html",
            available=True,
            errors=["Too many messages from this network. Please try again in an hour."],
            form=request.form,
        ), 429
    message = EmailMessage()
    message["Subject"] = f"Tracker contact: {form.name or form.email}"
    message["From"] = settings.mail_from
    message["To"] = settings.webmaster_email
    message["Reply-To"] = str(form.email)
    message.set_content(f"From: {form.name or '(no name)'} <{form.email}>\n\n{form.message}\n")
    try:
        send(settings, message)
    except MailNotConfigured:
        return render_template("pages/contact.html", available=False, form=request.form), 503
    except OSError:
        log.exception("contact form email failed")
        return render_template(
            "pages/contact.html",
            available=True,
            errors=["The message could not be sent. Please try again later."],
            form=request.form,
        ), 502
    return render_template("pages/contact_sent.html")
