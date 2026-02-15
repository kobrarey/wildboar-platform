"""Dashboard routes: dashboard, useragreement, set-language."""
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.requests import Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.web import templates
from app.i18n import get_lang_from_request, SUPPORTED_LANGS, LANG_COOKIE_NAME
from app.auth import get_current_user
from app.portfolio import get_user_portfolio
from app.models import User

router = APIRouter()


@router.get("/dashboard")
def dashboard(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)
    portfolio = get_user_portfolio(db, user, lang)
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "lang": lang,
            "account_type": user.account_type,
            "portfolio": portfolio,
        },
    )


@router.get("/useragreement", response_class=HTMLResponse)
def useragreement(request: Request):
    lang = get_lang_from_request(request)
    return templates.TemplateResponse("useragreement.html", {"request": request, "lang": lang})


@router.post("/set-language")
async def set_language(request: Request):
    data = await request.json()
    lang = (data.get("lang") or "").lower()
    if lang not in SUPPORTED_LANGS:
        return JSONResponse({"status": "error", "message": "Unsupported language"}, status_code=400)

    resp = JSONResponse({"status": "ok"})
    resp.set_cookie(
        key=LANG_COOKIE_NAME,
        value=lang,
        max_age=60 * 60 * 24 * 365,
        httponly=False,
        samesite="lax",
        secure=(request.url.scheme == "https"),
        path="/",
    )
    return resp
