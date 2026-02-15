from __future__ import annotations

import base64
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path
from typing import Any, Dict

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import settings


BASE_DIR = Path(__file__).resolve().parent.parent  # корень проекта
_TEMPLATES_DIR = BASE_DIR / "templates"

_jinja = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def render_email_template(template_name: str, context: Dict[str, Any]) -> str:
    """
    template_name: например "emails/registration_code.html"
    """
    tpl = _jinja.get_template(template_name)
    return tpl.render(**context)


def _send_via_gmail_api(to_email: str, subject: str, html_body: str) -> None:
    # ВАЖНО: token.json / credentials.json лежат в корне проекта (как раньше)
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
    token_path = BASE_DIR / "token.json"
    creds_path = BASE_DIR / "credentials.json"

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # На проде вы обычно не будете запускать flow, но в dev это ок (если у вас так и было)
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    service = build("gmail", "v1", credentials=creds)

    from_email = settings.EMAIL_FROM_EMAIL.strip()
    if not from_email:
        raise RuntimeError("EMAIL_FROM_EMAIL is not set")

    msg = MIMEText(html_body, "html", "utf-8")
    msg["to"] = to_email
    msg["subject"] = subject
    msg["From"] = formataddr((settings.EMAIL_FROM_NAME, from_email))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")
    service.users().messages().send(userId="me", body={"raw": raw}).execute()


def send_email(to_email: str, subject: str, html_body: str) -> None:
    """
    Единая точка отправки писем.
    Выбор провайдера делаем по settings.EMAIL_PROVIDER.
    """
    provider = (settings.EMAIL_PROVIDER or "gmail_api").strip().lower()

    if provider == "gmail_api":
        _send_via_gmail_api(to_email, subject, html_body)
        return

    # Пока SMTP не используем на практике (как и договорено), но оставляем понятную ошибку
    raise RuntimeError(f"Unsupported EMAIL_PROVIDER: {provider}")
