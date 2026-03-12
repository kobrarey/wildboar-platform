"""Dashboard routes: dashboard, useragreement, set-language."""
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN

import segno
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from starlette.requests import Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.web import templates
from app.i18n import get_lang_from_request, t, SUPPORTED_LANGS, LANG_COOKIE_NAME
from app.auth import get_current_user
from app.portfolio import get_user_portfolio
from app.models import User, UserWallet, WalletTransfer, WithdrawSession, SecurityCode
from app.utils.wallet_check import validate_address_status
from app.codes import create_code, verify_code
from app.emails import send_withdraw_code

router = APIRouter()


def utcnow():
    return datetime.now(timezone.utc)


def parse_amount(s: str) -> Decimal:
    s = (s or "").strip().replace(",", ".")
    return Decimal(s)


def fmt_2dp(x: Decimal) -> str:
    return str(x.quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def mask_email(email: str) -> str:
    email = (email or "").strip()
    if "@" not in email:
        return "***"
    local, dom = email.split("@", 1)
    l = (local[:1] + "***") if local else "***"
    d = (dom[:1] + "***" + dom[-3:]) if len(dom) >= 3 else (dom[:1] + "***")
    return f"{l}@{d}"


@router.get("/dashboard")
def dashboard(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)
    portfolio = get_user_portfolio(db, user, lang)

    usdt_balance_total = float(portfolio.get("usdt_balance_total") or 0)
    usdt_balance_available = float(portfolio.get("usdt_balance_available") or 0)

    wallet = (
        db.query(UserWallet)
        .filter(UserWallet.user_id == user.id, UserWallet.blockchain == "BSC", UserWallet.is_active == True)
        .first()
    )
    user_usdt_address = wallet.address if wallet else ""
    usdt_balance = usdt_balance_total

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
            "user_compliance_status": getattr(user, "compliance_status", "ok"),
            "user_compliance_reason": getattr(user, "compliance_reason", None),
            "wallet_compliance_status": (wallet.compliance_status if wallet else "ok"),
            "usdt_balance_total": usdt_balance_total,
            "usdt_balance_available": usdt_balance_available,
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


class WithdrawRequestCodeIn(BaseModel):
    to_address: str
    amount_gross: str
    email_slot: int


class WithdrawResendIn(BaseModel):
    token: str


class WithdrawConfirmIn(BaseModel):
    token: str
    code: str


class WithdrawCancelIn(BaseModel):
    token: str


@router.post("/api/wallet/validate")
def api_wallet_validate(
    payload: WalletValidateRequest,
    user: User = Depends(get_current_user),
):
    if not user:
        return JSONResponse({"status": "error", "message": "Unauthorized"}, status_code=401)

    status = validate_address_status(payload.address)
    return {"status": status}


@router.get("/api/withdraw/email-options")
def withdraw_email_options(
    request: Request,
    user: User = Depends(get_current_user),
):
    lang = get_lang_from_request(request)

    options = []
    if user.is_email_verified and user.email:
        options.append({"slot": 1, "email": user.email, "email_masked": mask_email(user.email)})
    if user.is_backup_email_verified and user.backup_email:
        options.append({"slot": 2, "email": user.backup_email, "email_masked": mask_email(user.backup_email)})

    if not options:
        return JSONResponse({"status": "error", "message": t(lang, "no_verified_email")}, status_code=400)

    default_slot = 1 if any(o["slot"] == 1 for o in options) else options[0]["slot"]
    return {"options": options, "default_slot": default_slot}


@router.post("/api/withdraw/request-code")
def withdraw_request_code(
    request: Request,
    payload: WithdrawRequestCodeIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)

    addr_status = validate_address_status(payload.to_address)
    if addr_status == "invalid":
        return JSONResponse(
            {"status": "error", "message": t(lang, "withdraw_invalid_address")},
            status_code=400,
        )

    try:
        amount_gross = parse_amount(payload.amount_gross)
    except Exception:
        return JSONResponse(
            {"status": "error", "message": t(lang, "withdraw_amount_too_small")},
            status_code=400,
        )

    fee = Decimal(settings.WITHDRAW_FEE_USDT)
    if amount_gross <= fee:
        return JSONResponse(
            {"status": "error", "message": t(lang, "withdraw_amount_too_small")},
            status_code=400,
        )

    wallet = (
        db.query(UserWallet)
        .filter(
            UserWallet.user_id == user.id,
            UserWallet.blockchain == "BSC",
            UserWallet.is_active == True,
        )
        .first()
    )
    if not wallet:
        return JSONResponse(
            {"status": "error", "message": "Wallet not found"},
            status_code=400,
        )

    available = Decimal(wallet.usdt_balance or 0) - Decimal(wallet.usdt_reserved or 0)
    if available < amount_gross:
        return JSONResponse(
            {"status": "error", "message": t(lang, "withdraw_insufficient_balance")},
            status_code=400,
        )

    # email_slot validation
    if payload.email_slot == 1:
        if not user.is_email_verified or not user.email:
            return JSONResponse(
                {"status": "error", "message": t(lang, "email_not_verified")},
                status_code=400,
            )
        to_email = user.email
    elif payload.email_slot == 2:
        if not user.is_backup_email_verified or not user.backup_email:
            return JSONResponse(
                {"status": "error", "message": t(lang, "email_not_verified")},
                status_code=400,
            )
        to_email = user.backup_email
    else:
        return JSONResponse(
            {"status": "error", "message": t(lang, "unsupported_slot")},
            status_code=400,
        )

    # 1) create code first
    try:
        code = create_code(user.id, "withdraw", db=db)
    except ValueError as e:
        return JSONResponse(
            {"status": "error", "message": t(lang, str(e))},
            status_code=400,
        )

    # 2) send email second
    try:
        send_withdraw_code(
            to_email=to_email,
            lang=lang,
            amount_gross_2dp=fmt_2dp(amount_gross),
            to_address=payload.to_address,
            code=code,
        )
    except Exception:
        # rollback the just-created withdraw code,
        # otherwise cooldown blocks immediate retry
        bad_code = (
            db.query(SecurityCode)
            .filter(
                SecurityCode.user_id == user.id,
                SecurityCode.purpose == "withdraw",
                SecurityCode.is_used == False,
            )
            .order_by(SecurityCode.created_at.desc())
            .first()
        )
        if bad_code:
            db.delete(bad_code)
            db.commit()

        return JSONResponse(
            {"status": "error", "message": t(lang, "send_email_failed")},
            status_code=500,
        )

    # 3) only now create withdraw_session
    token = secrets.token_hex(32)
    now = utcnow()
    session = WithdrawSession(
        token=token,
        user_id=user.id,
        wallet_id=wallet.id,
        to_address=payload.to_address,
        amount_gross=amount_gross,
        fee_usdt=fee,
        email_slot=payload.email_slot,
        expires_at=now + timedelta(minutes=int(settings.WITHDRAW_SESSION_TTL_MIN)),
        used_at=None,
    )
    db.add(session)
    db.commit()

    return {
        "status": "ok",
        "token": token,
        "amount_net": str(amount_gross - fee),
        "fee": str(fee),
        "to_address": payload.to_address,
        "email_slot": payload.email_slot,
    }


@router.post("/api/withdraw/resend-code")
def withdraw_resend_code(
    request: Request,
    payload: WithdrawResendIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)

    s = (
        db.query(WithdrawSession)
        .filter(WithdrawSession.token == payload.token, WithdrawSession.user_id == user.id)
        .first()
    )
    if not s or s.used_at is not None or s.expires_at < utcnow():
        return JSONResponse({"status": "error", "message": "Session expired"}, status_code=400)

    if s.email_slot == 1:
        to_email = user.email
    else:
        to_email = user.backup_email

    try:
        code = create_code(user.id, "withdraw", db=db)
        send_withdraw_code(
            to_email=to_email,
            lang=lang,
            amount_gross_2dp=fmt_2dp(Decimal(s.amount_gross)),
            to_address=s.to_address,
            code=code,
        )
        return {"status": "ok"}
    except ValueError as e:
        return JSONResponse({"status": "error", "message": t(lang, str(e))}, status_code=400)
    except Exception:
        return JSONResponse({"status": "error", "message": t(lang, "send_email_failed")}, status_code=500)


@router.post("/api/withdraw/confirm")
def withdraw_confirm(
    request: Request,
    payload: WithdrawConfirmIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)

    s = (
        db.query(WithdrawSession)
        .filter(WithdrawSession.token == payload.token, WithdrawSession.user_id == user.id)
        .first()
    )
    if not s or s.used_at is not None or s.expires_at < utcnow():
        return JSONResponse({"status": "error", "message": "Session expired"}, status_code=400)

    try:
        verify_code(user.id, "withdraw", payload.code, db=db)
    except ValueError as e:
        return JSONResponse({"status": "error", "message": t(lang, str(e))}, status_code=400)

    wallet = db.query(UserWallet).filter(UserWallet.id == s.wallet_id).first()
    if not wallet:
        return JSONResponse({"status": "error", "message": "Wallet not found"}, status_code=400)

    fee = Decimal(s.fee_usdt or settings.WITHDRAW_FEE_USDT)
    amount_gross = Decimal(s.amount_gross)
    amount_net = amount_gross - fee

    is_ok = (getattr(user, "compliance_status", "ok") == "ok") and (getattr(wallet, "compliance_status", "ok") == "ok")
    compliance_status = "ok" if is_ok else "blocked"

    tr = WalletTransfer(
        user_id=user.id,
        wallet_id=wallet.id,
        coin="USDT",
        network="BSC (BEP20)",
        type="withdraw",
        from_address=wallet.address,
        to_address=s.to_address,
        amount=amount_net,
        amount_gross=amount_gross,
        fee_usdt=fee,
        email_slot=s.email_slot,
        status="processing",
        compliance_status=compliance_status,
        tx_hash=None,
        log_index=None,
    )

    wallet.usdt_reserved = Decimal(wallet.usdt_reserved or 0) + amount_gross
    s.used_at = utcnow()

    db.add(tr)
    db.add(wallet)
    db.add(s)
    db.commit()

    return {"status": "processing", "redirect_url": "/history?tab=transfers&sub=withdrawals"}


@router.post("/api/withdraw/cancel")
def withdraw_cancel(
    payload: WithdrawCancelIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    s = (
        db.query(WithdrawSession)
        .filter(
            WithdrawSession.token == payload.token,
            WithdrawSession.user_id == user.id,
        )
        .first()
    )

    if not s:
        return {"status": "ok"}

    if s.used_at is not None:
        return {"status": "ok"}

    if s.expires_at < utcnow():
        return {"status": "ok"}

    db.delete(s)
    db.commit()
    return {"status": "ok"}


@router.get("/history")
def history_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not user:
        return RedirectResponse("/", status_code=303)

    lang = get_lang_from_request(request)

    sort_by = func.coalesce(WalletTransfer.tx_time, WalletTransfer.detected_at).desc()

    transfers_all = (
        db.query(WalletTransfer)
        .filter(WalletTransfer.user_id == user.id)
        .order_by(sort_by)
        .all()
    )

    transfers_deposits = (
        db.query(WalletTransfer)
        .filter(WalletTransfer.user_id == user.id, WalletTransfer.type == "deposit")
        .order_by(sort_by)
        .all()
    )

    transfers_withdrawals = (
        db.query(WalletTransfer)
        .filter(WalletTransfer.user_id == user.id, WalletTransfer.type == "withdraw")
        .order_by(sort_by)
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
            "transfers_withdrawals": transfers_withdrawals,
        },
    )
