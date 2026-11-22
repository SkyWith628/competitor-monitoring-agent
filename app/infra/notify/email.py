"""SMTP HTML 메일 발송. 담당: D"""
from __future__ import annotations

import smtplib
from email.message import EmailMessage

from app.core.config import env


def send_html(subject: str, html: str, text: str | None = None) -> None:
    """HTML 메일 발송. text를 주면 multipart/alternative(plain + html)로 보낸다."""
    missing = [k for k in ("SMTP_USER", "SMTP_PASSWORD", "MAIL_TO") if not (env(k) or "").strip()]
    recipients = [r.strip() for r in (env("MAIL_TO") or "").split(",") if r.strip()]
    if not recipients and "MAIL_TO" not in missing:
        missing.append("MAIL_TO")
    if missing:
        raise RuntimeError(f"{', '.join(missing)} 가 .env 에 없습니다")

    msg = EmailMessage()
    msg["Subject"], msg["From"], msg["To"] = subject, env("SMTP_USER"), ", ".join(recipients)
    if text is None:
        msg.set_content(html, subtype="html", charset="utf-8")
    else:
        msg.set_content(text, charset="utf-8")
        msg.add_alternative(html, subtype="html", charset="utf-8")
    with smtplib.SMTP(env("SMTP_HOST", "smtp.gmail.com"), int(env("SMTP_PORT", "587"))) as s:
        s.starttls()
        s.login(env("SMTP_USER"), env("SMTP_PASSWORD"))
        s.send_message(msg)
