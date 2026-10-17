"""Outgoing email over SMTP. Without SMTP settings, development prints messages to the log instead."""

import logging
import smtplib
from email.message import EmailMessage

from . import config

log = logging.getLogger("aureus.mail")


class MailNotConfigured(RuntimeError):
    pass


def enabled() -> bool:
    return bool(config.SMTP_HOST) or config.ENV != "production"


def send(to: str, subject: str, body: str) -> None:
    if not config.SMTP_HOST:
        if config.ENV == "production":
            raise MailNotConfigured("Email is not configured on this server (set SMTP_HOST)")
        log.warning("Email (SMTP not configured, development only)\nTo: %s\nSubject: %s\n\n%s", to, subject, body)
        return
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = config.MAIL_FROM, to, subject
    msg.set_content(body)
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as smtp:
        smtp.starttls()
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.send_message(msg)
