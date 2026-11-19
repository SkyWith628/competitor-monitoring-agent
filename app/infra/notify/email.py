"""SMTP HTML 메일 발송. 담당: D"""
from __future__ import annotations

import smtplib
from email.mime.text import MIMEText

from app.core.config import env


def send_html(subject: str, html: str) -> None:
    recipients = [r.strip() for r in (env("MAIL_TO") or "").split(",") if r.strip()]
    if not recipients:
        raise RuntimeError("MAIL_TO 가 .env 에 없습니다")
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, env("SMTP_USER"), ", ".join(recipients)
    with smtplib.SMTP(env("SMTP_HOST", "smtp.gmail.com"), int(env("SMTP_PORT", "587"))) as s:
        s.starttls()
        s.login(env("SMTP_USER"), env("SMTP_PASSWORD"))
        s.send_message(msg)
