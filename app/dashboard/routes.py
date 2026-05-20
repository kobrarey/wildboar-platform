"""Dashboard routes: dashboard, useragreement, set-language."""
import io
import json
import secrets
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

import segno
from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel
from starlette.requests import Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.web import templates
from app.i18n import get_lang_from_request, t, SUPPORTED_LANGS, LANG_COOKIE_NAME
from app.auth import get_current_user
from app.auth.deps import NotAuthenticated
from app.auth.code_action_cooldown import enforce_code_action_cooldown
from app.portfolio import get_user_portfolio
from app.models import (
    User,
    UserWallet,
    WalletTransfer,
    WithdrawSession,
    SecurityCode,
    Fund,
    FundOrder,
)
from app.utils.wallet_check import validate_address_status
from app.codes import create_code, verify_code, get_active_code
from app.emails import send_withdraw_code
from app.totp import require_totp_if_enabled
from app.trading.history_formatter import format_trading_history_rows

router = APIRouter()


def _cooldown_key(action: str, *parts) -> str:
    safe_parts = [str(p or "").strip().lower() for p in parts]
    return ":".join([action.strip().lower(), *safe_parts])


def _get_current_user_or_none(request: Request, db: Session) -> User | None:
    try:
        return get_current_user(request, db)
    except NotAuthenticated:
        return None


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


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _dec_str(value: Any, places: str = "0.00") -> str:
    return str(_to_decimal(value).quantize(Decimal(places), rounding=ROUND_DOWN))


def _optional_dec_str(value: Any, places: str = "0.00") -> str | None:
    if value is None:
        return None
    return _dec_str(value, places)


def _dt_str(value: datetime | None) -> str:
    if not value:
        return ""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _short_or_empty(value: str | None) -> str:
    return (value or "").strip()


def _transfer_address(tx: WalletTransfer) -> str:
    ttype = (tx.type or "").lower()
    if ttype in {"withdraw", "withdrawal"}:
        return _short_or_empty(tx.to_address)
    return _short_or_empty(tx.from_address or tx.to_address)


def _transfer_datetime(tx: WalletTransfer) -> datetime | None:
    return tx.tx_time or tx.detected_at


def _excel_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return value


def _build_xlsx_response(headers: list[str], rows: list[list[Any]], filename_prefix: str) -> StreamingResponse:
    wb = Workbook()
    ws = wb.active
    ws.title = "data"

    ws.append(headers)
    for row in rows:
        ws.append([_excel_value(v) for v in row])

    bio = io.BytesIO()
    wb.save(bio)
    bio.seek(0)

    filename = f"{filename_prefix}_{utcnow().strftime('%Y%m%d_%H%M%S')}.xlsx"
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"'
        },
    )


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
            "stablecoin_icon_name": portfolio.get("stablecoin_icon_name", "usdt.svg"),
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


@router.get("/cookie-policy", response_class=HTMLResponse)
def cookie_policy(request: Request):
    lang = get_lang_from_request(request)
    return templates.TemplateResponse(
        "cookie_policy.html",
        {
            "request": request,
            "lang": lang,
            "session_cookie_name": settings.COOKIE_NAME,
        },
    )


@router.post("/api/cookie-notice/ack")
def cookie_notice_ack(
    request: Request,
    db: Session = Depends(get_db),
):
    user = _get_current_user_or_none(request, db)

    if user is not None:
        user.cookie_notice_acknowledged = True
        user.cookie_notice_acknowledged_at = utcnow()
        db.add(user)
        db.commit()

    resp = JSONResponse({"status": "ok"})
    resp.set_cookie(
        key="cookie_notice_ack",
        value="true",
        max_age=60 * 60 * 24 * 365,
        httponly=False,
        samesite="lax",
        secure=(request.url.scheme == "https"),
        path="/",
    )
    return resp


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
    totp_code: str | None = None


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

    try:
        enforce_code_action_cooldown(
            _cooldown_key("withdraw_initial", user.id, payload.email_slot, payload.to_address)
        )
    except ValueError as e:
        return JSONResponse(
            {"status": "error", "message": t(lang, str(e))},
            status_code=400,
        )

    # 1) create or reuse active code first
    try:
        code = get_active_code(user.id, "withdraw", db) or create_code(user.id, "withdraw", db=db)
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
        "totp_required": bool(getattr(user, "totp_enabled", False)),
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
        enforce_code_action_cooldown(
            _cooldown_key("withdraw_resend", user.id, s.email_slot, s.token)
        )
    except ValueError as e:
        return JSONResponse(
            {"status": "error", "message": t(lang, str(e))},
            status_code=400,
        )

    try:
        code = get_active_code(user.id, "withdraw", db) or create_code(user.id, "withdraw", db=db)
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

    ok, err_key = require_totp_if_enabled(
        user=user,
        totp_code=payload.totp_code,
        db=db,
        lang=lang,
    )
    if not ok:
        return JSONResponse(
            {"status": "error", "message": t(lang, err_key or "totp_verification_failed")},
            status_code=400,
        )

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

    # Если сессии уже нет / истекла / использована — просто ok
    if not s:
        return {"status": "ok"}
    if s.used_at is not None:
        return {"status": "ok"}
    if s.expires_at < utcnow():
        return {"status": "ok"}

    # Определяем email по slot
    email_value = None
    if s.email_slot == 1:
        email_value = user.email
    elif s.email_slot == 2:
        email_value = user.backup_email

    # Удаляем все активные withdraw-коды,
    # чтобы не оставался cooldown-хвост
    codes_q = db.query(SecurityCode).filter(
        SecurityCode.user_id == user.id,
        SecurityCode.purpose == "withdraw",
        SecurityCode.is_used == False,
        SecurityCode.expires_at > utcnow(),
    )
    # Если в SecurityCode есть поле email — фильтруем по нему
    if hasattr(SecurityCode, "email") and email_value:
        codes_q = codes_q.filter(SecurityCode.email == email_value)

    active_codes = codes_q.all()
    for c in active_codes:
        db.delete(c)

    # Удаляем саму withdraw_session
    db.delete(s)
    db.commit()

    return {"status": "ok"}


@router.get("/api/dashboard/live")
def dashboard_live(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)
    portfolio = get_user_portfolio(db, user, lang)

    wallet = (
        db.query(UserWallet)
        .filter(
            UserWallet.user_id == user.id,
            UserWallet.blockchain == "BSC",
            UserWallet.is_active == True,
        )
        .first()
    )

    funds_payload = []
    for f in portfolio.get("funds") or []:
        funds_payload.append(
            {
                "id": f.get("id"),
                "code": f.get("code"),
                "category": f.get("category"),
                "name": f.get("name"),
                "price": _dec_str(f.get("price"), "0.00"),
                "shares": _dec_str(f.get("shares"), "0.0000"),
                "shares_reserved": _dec_str(f.get("shares_reserved"), "0.0000"),
                "shares_available": _dec_str(f.get("shares_available"), "0.0000"),
                "value": _dec_str(f.get("value"), "0.00"),
                "icon_name": f.get("icon_name") or "fund-default.svg",
            }
        )

    return {
        "status": "ok",
        "current_balance": _dec_str(portfolio.get("current_balance"), "0.00"),
        "stable_symbol": portfolio.get("stable_symbol") or "USDT",
        "daily_change_display_mode": portfolio.get("daily_change_display_mode") or "pct",
        "daily_change_pct": _optional_dec_str(portfolio.get("daily_change_pct"), "0.00"),
        "daily_change_abs": _dec_str(portfolio.get("daily_change_abs"), "0.00"),
        "usdt_balance_total": _dec_str(portfolio.get("usdt_balance_total"), "0.00"),
        "usdt_balance_available": _dec_str(portfolio.get("usdt_balance_available"), "0.00"),
        "user_compliance_status": getattr(user, "compliance_status", "ok"),
        "wallet_compliance_status": (wallet.compliance_status if wallet else "ok"),
        "funds": funds_payload,
    }


@router.get("/api/history/live")
def history_live(
    request: Request,
    section: str = Query(default="transfers"),
    sub: str = Query(default="all"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)

    section = (section or "").strip().lower()
    sub = (sub or "").strip().lower()

    if section not in {"transfers", "trading"}:
        return JSONResponse(
            {"status": "error", "message": "Unsupported section"},
            status_code=400,
        )

    if section == "transfers":
        sort_by = func.coalesce(WalletTransfer.tx_time, WalletTransfer.detected_at).desc()
        q = db.query(WalletTransfer).filter(WalletTransfer.user_id == user.id)

        if sub == "deposits":
            q = q.filter(WalletTransfer.type == "deposit")
        elif sub == "withdrawals":
            q = q.filter(WalletTransfer.type == "withdraw")
        elif sub != "all":
            return JSONResponse(
                {"status": "error", "message": "Unsupported sub"},
                status_code=400,
            )

        rows = q.order_by(sort_by).all()

        payload_rows = []
        for tx in rows:
            addr = _transfer_address(tx)
            payload_rows.append(
                {
                    "id": tx.id,
                    "coin": tx.coin or "USDT",
                    "network": tx.network or "BSC (BEP20)",
                    "amount": _dec_str(tx.amount, "0.00"),
                    "type": tx.type,
                    "address": addr,
                    "txid": tx.tx_hash or "",
                    "status": tx.status,
                    "compliance_status": tx.compliance_status,
                    "date_time": _dt_str(_transfer_datetime(tx)),
                    "full_address": addr,
                    "full_tx_hash": tx.tx_hash or "",
                }
            )

        return {
            "status": "ok",
            "section": "transfers",
            "sub": sub,
            "rows": payload_rows,
        }

    q = (
        db.query(FundOrder, Fund)
        .join(Fund, Fund.id == FundOrder.fund_id)
        .filter(FundOrder.user_id == user.id)
    )

    if sub in {"purchases", "buys"}:
        q = q.filter(FundOrder.side == "buy")
    elif sub in {"redemptions", "redeem"}:
        q = q.filter(FundOrder.side == "redeem")
    elif sub != "all":
        return JSONResponse(
            {"status": "error", "message": "Unsupported sub"},
            status_code=400,
        )

    rows = q.order_by(FundOrder.created_at.desc()).all()

    payload_rows = format_trading_history_rows(rows, lang)

    return {
        "status": "ok",
        "section": "trading",
        "sub": sub,
        "rows": payload_rows,
    }


@router.get("/api/history/export/transfers")
def history_export_transfers(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    headers = [
        "coin",
        "network",
        "amount",
        "type",
        "address",
        "txid",
        "status",
        "compliance_status",
        "date_time",
    ]

    sort_by = func.coalesce(WalletTransfer.tx_time, WalletTransfer.detected_at).desc()
    transfers = (
        db.query(WalletTransfer)
        .filter(WalletTransfer.user_id == user.id)
        .order_by(sort_by)
        .all()
    )

    rows = []
    for tx in transfers:
        addr = _transfer_address(tx)
        dt = _transfer_datetime(tx)

        rows.append(
            [
                tx.coin or "USDT",
                tx.network or "BSC (BEP20)",
                tx.amount,
                tx.type,
                addr,
                tx.tx_hash,
                tx.status,
                tx.compliance_status,
                dt,
            ]
        )

    return _build_xlsx_response(headers, rows, "wildboar_transfers")


@router.get("/api/history/export/trading")
def history_export_trading(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)

    headers = [
        "name",
        "side",
        "amount",
        "shares",
        "price",
        "status",
        "created",
        "executed",
    ]

    order_rows = (
        db.query(FundOrder, Fund)
        .join(Fund, Fund.id == FundOrder.fund_id)
        .filter(FundOrder.user_id == user.id)
        .order_by(FundOrder.created_at.desc())
        .all()
    )

    formatted_rows = format_trading_history_rows(order_rows, lang)

    rows = []
    for row in formatted_rows:
        rows.append(
            [
                row.get("name"),
                row.get("side_label"),
                row.get("amount"),
                row.get("shares_display"),
                row.get("price"),
                row.get("status_label"),
                row.get("created"),
                row.get("executed"),
            ]
        )

    return _build_xlsx_response(headers, rows, "wildboar_trading_operations")


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
