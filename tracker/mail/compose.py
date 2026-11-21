"""Build messages from text templates whose first line is the subject."""

from dataclasses import dataclass
from email.message import EmailMessage

from flask import render_template, url_for

from tracker.config import Settings
from tracker.enums import EmailKind
from tracker.mail import unsubscribe


@dataclass(frozen=True)
class SubscriberLinks:
    """Footer links for an email someone subscribed to."""

    unsubscribe: str
    preferences: str
    methodology: str


def subscriber_links(
    settings: Settings, user_id: int, kinds: frozenset[EmailKind]
) -> SubscriberLinks:
    """Links for an email sent for these kinds; unsubscribing turns those kinds off."""
    token = unsubscribe.sign(settings.secret_key, user_id, kinds)
    return SubscriberLinks(
        unsubscribe=settings.site_link(url_for("email.unsubscribe", t=token)),
        preferences=settings.site_link(url_for("watch.index") + "#preferences"),
        methodology=settings.site_link(url_for("pages.methodology")),
    )


def compose(
    settings: Settings,
    to: str,
    template: str,
    links: SubscriberLinks | None = None,
    **context: object,
) -> EmailMessage:
    """Render email/<template>.txt; with links, add RFC 8058 one-click unsubscribe headers."""
    text = render_template(f"email/{template}.txt", links=links, **context)
    subject, _, body = text.partition("\n\n")
    message = EmailMessage()
    message["Subject"] = subject.strip()
    message["From"] = settings.mail_from
    message["To"] = to
    message["Auto-Submitted"] = "auto-generated"
    if links is not None:
        message["List-Unsubscribe"] = f"<{links.unsubscribe}>"
        message["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    message.set_content(body.lstrip())
    return message
