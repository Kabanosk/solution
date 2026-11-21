"""Outbound email for urgent alerts to the CEO. SMTP when configured, otherwise a log-only stand-in (like LogNotifier)."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Any, Protocol

log = logging.getLogger(__name__)


class Mailer(Protocol):
    def send(self, to: str, subject: str, body: str) -> str | None:
        """Send one plain-text email; returns a reference, or None when it was not sent."""


class LogMailer:
    """No SMTP configured: the email goes to the log and to `sent` (tests, demo)."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send(self, to: str, subject: str, body: str) -> str | None:
        self.sent.append({"to": to, "subject": subject, "body": body})
        log.info("email (log only) to %s: %s", to, subject)
        return f"log-mail:{len(self.sent)}"


class SmtpMailer:
    def __init__(self, host: str, port: int, sender: str, *, user: str = "", password: str = "",
                 starttls: bool = True, timeout: float = 15.0) -> None:
        self.host, self.port, self.sender = host, port, sender
        self.user, self.password, self.starttls, self.timeout = user, password, starttls, timeout

    def send(self, to: str, subject: str, body: str) -> str | None:
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.sender, to, subject
        msg.set_content(body)
        try:
            with smtplib.SMTP(self.host, self.port, timeout=self.timeout) as smtp:
                if self.starttls:
                    smtp.starttls()
                if self.user:
                    smtp.login(self.user, self.password)
                smtp.send_message(msg)
        except (OSError, smtplib.SMTPException):
            log.warning("could not send email to %s", to, exc_info=True)
            return None
        return msg.get("Message-ID") or f"smtp:{to}"


def from_settings(settings: Any) -> Mailer:
    if getattr(settings, "smtp_host", "") and getattr(settings, "smtp_from", ""):
        return SmtpMailer(settings.smtp_host, settings.smtp_port, settings.smtp_from, user=settings.smtp_user,
                          password=settings.smtp_password, starttls=settings.smtp_starttls)
    return LogMailer()
