from fastapi.responses import Response

from app.config import settings


def _cookie_domain():
    value = (settings.COOKIE_DOMAIN or "").strip()
    return value or None


def set_auth_cookie(response: Response, session_id: str) -> None:
    response.set_cookie(
        key=settings.COOKIE_NAME,
        value=session_id,
        httponly=True,
        secure=bool(settings.COOKIE_SECURE),
        samesite=settings.COOKIE_SAMESITE,
        domain=_cookie_domain(),
        path=settings.COOKIE_PATH,
        max_age=settings.SESSION_TTL_DAYS * 24 * 60 * 60,
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.COOKIE_NAME,
        domain=_cookie_domain(),
        path=settings.COOKIE_PATH,
    )