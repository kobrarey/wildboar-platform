from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
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
)
from app.trading.service import (
    get_first_active_fund_code,
    get_terminal_page_payload,
)

router = APIRouter()


def get_optional_user(request: Request, db: Session):
    try:
        return auth_get_current_user(request, db)
    except NotAuthenticated:
        return None


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