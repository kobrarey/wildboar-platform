from __future__ import annotations

import base64
from email.message import EmailMessage
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

CREDENTIALS_FILE = Path("credentials.json")
TOKEN_FILE = Path("token.json")
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def _get_creds() -> Credentials:
    if not TOKEN_FILE.exists():
        raise RuntimeError("token.json not found. Run auth_bootstrap.py to create it.")

    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    # auto-refresh
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")

    return creds


def send_email_gmail_api(to_email: str, subject: str, html_body: str, from_email: str) -> None:
    creds = _get_creds()
    service = build("gmail", "v1", credentials=creds)

    msg = EmailMessage()
    msg["To"] = to_email
    msg["From"] = from_email
    msg["Subject"] = subject

    msg.set_content("This email contains HTML content.")
    msg.add_alternative(html_body, subtype="html")

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    service.users().messages().send(
        userId="me",
        body={"raw": raw},
    ).execute()
