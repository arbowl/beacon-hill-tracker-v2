"""Outgoing email over SMTP (Postmark or SES SMTP endpoints both work)."""

import smtplib
from email.message import EmailMessage

from tracker.config import Settings


class MailNotConfigured(RuntimeError):
    """SMTP settings are missing."""


def send(settings: Settings, message: EmailMessage) -> None:
    """Send one message with STARTTLS; raises MailNotConfigured or smtplib errors."""
    if not settings.mail_configured or settings.smtp_host is None:
        raise MailNotConfigured("Set SMTP_HOST and MAIL_FROM")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        smtp.starttls()
        if settings.smtp_user and settings.smtp_password:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)
