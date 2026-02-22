"""Dashboard routes: dashboard, useragreement, set-language."""
import segno
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.web import templates
from app.i18n import get_lang_from_request, SUPPORTED_LANGS, LANG_COOKIE_NAME
from app.auth import get_current_user
from app.portfolio import get_user_portfolio
from app.models import User, UserWallet, WalletTransfer
from app.utils.wallet_check import validate_address_status

router = APIRouter()


@router.get("/dashboard")
def dashboard(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)
    portfolio = get_user_portfolio(db, user, lang)

    wallet = (
        db.query(UserWallet)
        .filter(UserWallet.user_id == user.id, UserWallet.blockchain == "BSC")
        .first()
    )
    user_usdt_address = wallet.address if wallet else ""
    usdt_balance = float(portfolio.get("stable_balance") or 0)

    deposit_qr_svg = (
        segno.make(user_usdt_address).svg_inline(scale=3)
        if user_usdt_address else ""
    )

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "lang": lang,
            "account_type": user.account_type,
            "portfolio": portfolio,
            "user_usdt_address": user_usdt_address,
            "usdt_balance": usdt_balance,
            "deposit_qr_svg": deposit_qr_svg,
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


class WalletValidateRequest(BaseModel):
    address: str = ""


@router.post("/api/wallet/validate")
def api_wallet_validate(
    payload: WalletValidateRequest,
    user: User = Depends(get_current_user),
):
    if not user:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)

    status = validate_address_status(payload.address)
    return {"status": status}


@router.get("/history")
def history_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/", status_code=303)

    lang = get_lang_from_request(request)

    transfers_all = (
        db.query(WalletTransfer)
        .filter(WalletTransfer.user_id == user.id)
        .order_by(WalletTransfer.tx_time.desc().nullslast(), WalletTransfer.detected_at.desc().nullslast())
        .all()
    )

    transfers_deposits = (
        db.query(WalletTransfer)
        .filter(WalletTransfer.user_id == user.id, WalletTransfer.type == "deposit")
        .order_by(WalletTransfer.tx_time.desc().nullslast(), WalletTransfer.detected_at.desc().nullslast())
        .all()
    )

    return templates.TemplateResponse(
        "history.html",
        {
            "request": request,
            "user": user,
            "lang": lang,
            "account_type": user.account_type,
            "transfers_all": transfers_all,
            "transfers_deposits": transfers_deposits,
        },
    )
