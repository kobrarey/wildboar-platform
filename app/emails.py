from __future__ import annotations

import base64
import smtplib
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path
from typing import Any, Dict

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings


BASE_DIR = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = BASE_DIR / "templates"

_jinja = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def render_email_template(template_name: str, context: Dict[str, Any]) -> str:
    tpl = _jinja.get_template(template_name)
    return tpl.render(**context)


def _build_message(to_email: str, subject: str, html_body: str) -> MIMEText:
    from_email = (settings.EMAIL_FROM_EMAIL or "").strip()
    if not from_email:
        raise RuntimeError("EMAIL_FROM_EMAIL is not set")

    msg = MIMEText(html_body, "html", "utf-8")
    msg["to"] = to_email
    msg["subject"] = subject
    msg["From"] = formataddr((settings.EMAIL_FROM_NAME, from_email))
    return msg


def _send_via_smtp(to_email: str, subject: str, html_body: str) -> None:
    host = (settings.SMTP_HOST or "").strip()
    if not host:
        raise RuntimeError("SMTP_HOST is not set")

    msg = _build_message(to_email, subject, html_body)

    use_ssl = bool(settings.SMTP_SSL)
    use_starttls = bool(settings.SMTP_STARTTLS)
    timeout = int(settings.SMTP_TIMEOUT_SEC)

    smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP

    with smtp_cls(host, int(settings.SMTP_PORT), timeout=timeout) as smtp:
        smtp.ehlo()

        if (not use_ssl) and use_starttls:
            smtp.starttls()
            smtp.ehlo()

        username = (settings.SMTP_USERNAME or "").strip()
        password = settings.SMTP_PASSWORD or ""
        if username and password:
            smtp.login(username, password)

        smtp.send_message(msg)


def _send_via_gmail_api(to_email: str, subject: str, html_body: str) -> None:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    scopes = ["https://www.googleapis.com/auth/gmail.send"]
    token_path = BASE_DIR / "token.json"
    creds_path = BASE_DIR / "credentials.json"

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), scopes)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    service = build("gmail", "v1", credentials=creds)

    msg = _build_message(to_email, subject, html_body)
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


def send_email(to_email: str, subject: str, html_body: str) -> None:
    provider = (settings.EMAIL_PROVIDER or "smtp_relay").strip().lower()

    if provider == "smtp_relay":
        _send_via_smtp(to_email, subject, html_body)
        return

    if provider == "gmail_api":
        _send_via_gmail_api(to_email, subject, html_body)
        return

    raise RuntimeError(f"Unsupported EMAIL_PROVIDER: {provider}")


def send_withdraw_code(to_email: str, lang: str, amount_gross_2dp: str, to_address: str, code: str) -> None:
    subject = "Withdraw confirmation code" if lang == "en" else "Код подтверждения вывода"
    html = render_email_template(
        "emails/withdraw_code.html",
        {
            "title": "Wild Boar",
            "lang": lang,
            "amount_gross_2dp": amount_gross_2dp,
            "to_address": to_address,
            "code": code,
        },
    )
    send_email(to_email, subject, html)
