import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from typing import Optional, Dict, Any

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, select_autoescape

load_dotenv()

_TEMPLATE_ENV: Optional[Environment] = None


def _get_template_env() -> Environment:
    global _TEMPLATE_ENV
    if _TEMPLATE_ENV is None:
        base_dir = Path(__file__).resolve().parent
        templates_dir = base_dir / "templates"
        _TEMPLATE_ENV = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(["html", "xml"]),
        )
    return _TEMPLATE_ENV


def render_email_template(template_name: str, context: Dict[str, Any]) -> str:
    """
    template_name example: "emails/registration_code.html"
    """
    env = _get_template_env()
    tpl = env.get_template(template_name)
    return tpl.render(**context)


def send_email(to: str, subject: str, html_body: str, text_body: Optional[str] = None) -> None:
    """
    SMTP params are taken from env:
      SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM
    """
    host = os.getenv("SMTP_HOST")
    port_raw = os.getenv("SMTP_PORT")
    user = os.getenv("SMTP_USER")
    password = os.getenv("SMTP_PASSWORD")
    from_addr = os.getenv("SMTP_FROM")

    if not host or not port_raw or not from_addr:
        raise RuntimeError("SMTP_HOST/SMTP_PORT/SMTP_FROM must be set in env/.env")

    port = int(port_raw)

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to

    if text_body is None:
        text_body = "Please use the code from this email in the form."

    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    # Heuristic: 465 => SSL, otherwise STARTTLS (typical 587)
    if port == 465:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(host, port, context=context, timeout=15) as server:
            if user and password:
                server.login(user, password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=15) as server:
            server.ehlo()
            # STARTTLS for 587/others
            context = ssl.create_default_context()
            server.starttls(context=context)
            server.ehlo()
            if user and password:
                server.login(user, password)
            server.send_message(msg)
