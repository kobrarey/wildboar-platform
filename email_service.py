from pathlib import Path
from typing import Optional, Dict, Any

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader, select_autoescape
from gmail_client import send_email_gmail_api

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


def send_email(to_email: str, subject: str, html_body: str) -> None:
    send_email_gmail_api(
        to_email=to_email,
        subject=subject,
        html_body=html_body,
        from_email="Wild Boar <notification@wildboar.finance>",
    )
