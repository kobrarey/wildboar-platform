from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.auth.deps import NotAuthenticated, get_current_user as auth_get_current_user
from app.db import get_db
from app.i18n import get_lang_from_request
from app.web import templates
from app.trading.chart_service import (
    ChartNotFoundError,
    ChartResolutionError,
    get_chart_bars_payload,
    get_chart_config_by_code,
    get_latest_chart_bar_payload,
)
from app.trading.service import (
    get_first_active_fund_code,
    get_terminal_live_payload,
    get_terminal_page_payload,
)
from app.trading.order_service import (
    TradingOrderError,
    create_buy_order,
    create_redeem_order,
)

router = APIRouter()


class TradingBuyOrderIn(BaseModel):
    fund_code: str
    amount_usdt: str


class TradingRedeemOrderIn(BaseModel):
    fund_code: str
    shares: str


def get_optional_user(
    request: Request,
    db: Session = Depends(get_db),
):
    try:
        return auth_get_current_user(request, db)
    except NotAuthenticated:
        return None


def _trading_error_response(lang: str, error_key: str, status_code: int = 400) -> JSONResponse:
    from app.i18n import t

    return JSONResponse(
        {
            "status": "error",
            "message": t(lang, error_key),
            "error": error_key,
        },
        status_code=status_code,
    )


def _trading_error_status_code(error_key: str) -> int:
    if error_key == "not_authenticated":
        return 401

    if error_key == "order_entry_disabled":
        return 423

    return 400


@router.post("/api/trading/orders/buy")
def api_create_buy_order(
    payload: TradingBuyOrderIn,
    request: Request,
    user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)

    try:
        return create_buy_order(
            db=db,
            user=user,
            fund_code=payload.fund_code,
            amount_usdt=payload.amount_usdt,
            lang=lang,
        )
    except TradingOrderError as exc:
        return _trading_error_response(
            lang,
            exc.error_key,
            status_code=_trading_error_status_code(exc.error_key),
        )


@router.post("/api/trading/orders/redeem")
def api_create_redeem_order(
    payload: TradingRedeemOrderIn,
    request: Request,
    user=Depends(get_optional_user),
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)

    try:
        return create_redeem_order(
            db=db,
            user=user,
            fund_code=payload.fund_code,
            shares=payload.shares,
            lang=lang,
        )
    except TradingOrderError as exc:
        return _trading_error_response(
            lang,
            exc.error_key,
            status_code=_trading_error_status_code(exc.error_key),
        )


@router.get("/terminal")
def terminal_root(
    request: Request,
    db: Session = Depends(get_db),
):
    first_code = get_first_active_fund_code(db)
    if not first_code:
        raise HTTPException(status_code=404, detail="No active funds found")
    return RedirectResponse(url=f"/terminal/{first_code}", status_code=303)


@router.get("/terminal/{fund_code}")
def terminal_fund_page(
    fund_code: str,
    request: Request,
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)
    user = get_optional_user(request, db)

    payload = get_terminal_page_payload(
        db=db,
        user=user,
        lang=lang,
        fund_code=fund_code,
    )
    if not payload:
        raise HTTPException(status_code=404, detail="Fund not found")

    current_theme = (
        request.cookies.get("theme")
        or request.cookies.get("wb_theme")
        or request.cookies.get("app_theme")
    )

    return templates.TemplateResponse(
        "terminal.html",
        {
            "request": request,
            "user": user,
            "lang": lang,
            "is_authenticated": user is not None,
            "current_theme": current_theme,
            "account_type": (user.account_type if user else None),
            "current_fund": payload["current_fund"],
            "fund_menu": payload["fund_menu"],
            "fund_title_min_width_ch": payload["fund_title_min_width_ch"],
            "trade_history": payload["trade_history"],
            "asset_rows": payload["asset_rows"],
            "fund_info": payload["fund_info"],
            "form_state": payload["form_state"],
            "chart_config": payload["chart_config"],
        },
    )


@router.get("/api/chart/config/{fund_code}")
def chart_config_endpoint(
    fund_code: str,
    request: Request,
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)

    try:
        return get_chart_config_by_code(db, fund_code, lang)
    except ChartNotFoundError:
        raise HTTPException(status_code=404, detail="Fund not found")


@router.get("/api/chart/bars/{fund_code}")
def chart_bars_endpoint(
    fund_code: str,
    resolution: str = Query(...),
    from_ts: int = Query(..., alias="from"),
    to_ts: int = Query(..., alias="to"),
    db: Session = Depends(get_db),
):
    try:
        return get_chart_bars_payload(
            db=db,
            fund_code=fund_code,
            resolution=resolution,
            from_ts=from_ts,
            to_ts=to_ts,
        )
    except ChartNotFoundError:
        raise HTTPException(status_code=404, detail="Fund not found")
    except ChartResolutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/chart/latest-bar/{fund_code}")
def chart_latest_bar_endpoint(
    fund_code: str,
    resolution: str = Query(...),
    db: Session = Depends(get_db),
):
    try:
        return get_latest_chart_bar_payload(
            db=db,
            fund_code=fund_code,
            resolution=resolution,
        )
    except ChartNotFoundError:
        raise HTTPException(status_code=404, detail="Fund not found")
    except ChartResolutionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/api/terminal/live/{fund_code}")
def terminal_live_endpoint(
    fund_code: str,
    request: Request,
    db: Session = Depends(get_db),
):
    lang = get_lang_from_request(request)

    payload = get_terminal_live_payload(
        db=db,
        lang=lang,
        fund_code=fund_code,
    )
    if not payload:
        raise HTTPException(status_code=404, detail="Fund not found")

    return payload