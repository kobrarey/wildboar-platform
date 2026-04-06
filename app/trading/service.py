from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    Fund,
    FundNavMinute,
    FundOrder,
    User,
    UserFundPosition,
    UserFundPositionStats,
)
from app.portfolio import FUND_ICON_MAP, get_user_portfolio


ZERO = Decimal("0")


def _to_decimal(value: Any) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _safe_price(nav_usdt: Any, shares_outstanding: Any) -> Decimal:
    nav = _to_decimal(nav_usdt)
    shares = _to_decimal(shares_outstanding)
    if shares <= 0:
        return ZERO
    return nav / shares


def _round_0(value: Any) -> Decimal:
    return _to_decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def _localize(ru_value: Any, en_value: Any, lang: str, fallback: str = "") -> str:
    if lang == "en":
        return str(en_value or ru_value or fallback or "")
    return str(ru_value or en_value or fallback or "")


def _fund_name(fund: Fund, lang: str) -> str:
    return _localize(fund.name_ru, fund.name_en, lang, fallback=fund.code or "")


def _fund_short_name(fund: Fund, lang: str) -> str:
    return _localize(
        getattr(fund, "short_name_ru", None),
        getattr(fund, "short_name_en", None),
        lang,
        fallback=_fund_name(fund, lang),
    )


def _fund_full_name(fund: Fund, lang: str) -> str:
    return _localize(
        getattr(fund, "full_name_ru", None),
        getattr(fund, "full_name_en", None),
        lang,
        fallback=_fund_name(fund, lang),
    )


def _fund_benchmark_name(fund: Fund, lang: str) -> str:
    return _localize(
        getattr(fund, "benchmark_name_ru", None),
        getattr(fund, "benchmark_name_en", None),
        lang,
        fallback="",
    )


def _fund_icon_name(fund: Fund) -> str:
    db_icon = (getattr(fund, "icon_name", None) or "").strip()
    if db_icon:
        return db_icon
    return FUND_ICON_MAP.get((fund.code or "").strip().lower(), "fund-default.svg")


def get_first_active_fund_code(db: Session) -> str | None:
    fund = (
        db.query(Fund)
        .filter(Fund.is_active == True)
        .order_by(Fund.sort_order.asc(), Fund.id.asc())
        .first()
    )
    return fund.code if fund else None


def _get_fund_by_code(db: Session, fund_code: str) -> Fund | None:
    return (
        db.query(Fund)
        .filter(func.lower(Fund.code) == (fund_code or "").strip().lower())
        .first()
    )


def _get_latest_nav_row(db: Session, fund_id: int) -> FundNavMinute | None:
    return (
        db.query(FundNavMinute)
        .filter(FundNavMinute.fund_id == fund_id)
        .order_by(FundNavMinute.ts_utc.desc())
        .first()
    )


def _get_previous_day_close_row(db: Session, fund_id: int, day_start_utc: datetime) -> FundNavMinute | None:
    return (
        db.query(FundNavMinute)
        .filter(
            FundNavMinute.fund_id == fund_id,
            FundNavMinute.ts_utc < day_start_utc,
        )
        .order_by(FundNavMinute.ts_utc.desc())
        .first()
    )


def _get_today_rows(
    db: Session,
    fund_id: int,
    day_start_utc: datetime,
    day_end_utc: datetime,
) -> list[FundNavMinute]:
    return (
        db.query(FundNavMinute)
        .filter(
            FundNavMinute.fund_id == fund_id,
            FundNavMinute.ts_utc >= day_start_utc,
            FundNavMinute.ts_utc < day_end_utc,
        )
        .order_by(FundNavMinute.ts_utc.asc())
        .all()
    )


def _build_market_snapshot(db: Session, fund: Fund, lang: str) -> dict:
    now_utc = datetime.now(timezone.utc)
    day_start_utc = datetime(now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc)
    day_end_utc = day_start_utc + timedelta(days=1)

    latest_row = _get_latest_nav_row(db, fund.id)
    current_price = _safe_price(
        latest_row.nav_usdt if latest_row else None,
        latest_row.shares_outstanding if latest_row else None,
    )

    prev_close_row = _get_previous_day_close_row(db, fund.id, day_start_utc)
    prev_close_price = None
    if prev_close_row:
        prev_close_price = _safe_price(prev_close_row.nav_usdt, prev_close_row.shares_outstanding)

    change_24h_pct = None
    if prev_close_price is not None and prev_close_price > 0 and current_price > 0:
        change_24h_pct = (current_price / prev_close_price - Decimal("1")) * Decimal("100")

    today_rows = _get_today_rows(db, fund.id, day_start_utc, day_end_utc)
    today_prices = [_safe_price(r.nav_usdt, r.shares_outstanding) for r in today_rows]

    day_high = max(today_prices) if today_prices else None
    day_low = min(today_prices) if today_prices else None

    return {
        "fund_id": fund.id,
        "fund_code": fund.code,
        "short_name": _fund_short_name(fund, lang),
        "icon_name": _fund_icon_name(fund),
        "current_price_usdt": current_price,
        "change_24h_pct": change_24h_pct,
        "day_high_usdt": day_high,
        "day_low_usdt": day_low,
    }


def _build_fund_menu(db: Session, lang: str) -> list[dict]:
    funds = (
        db.query(Fund)
        .filter(Fund.is_active == True)
        .order_by(Fund.sort_order.asc(), Fund.id.asc())
        .all()
    )

    items: list[dict] = []
    for fund in funds:
        snap = _build_market_snapshot(db, fund, lang)
        items.append(
            {
                "fund_code": fund.code,
                "short_name": snap["short_name"],
                "icon_name": snap["icon_name"],
                "current_price_usdt": snap["current_price_usdt"],
                "change_24h_pct": snap["change_24h_pct"],
            }
        )
    return items


def _build_trade_history(db: Session, user: User | None, lang: str) -> list[dict]:
    if not user:
        return []

    rows = (
        db.query(FundOrder, Fund)
        .join(Fund, Fund.id == FundOrder.fund_id)
        .filter(FundOrder.user_id == user.id)
        .order_by(FundOrder.created_at.desc())
        .limit(100)
        .all()
    )

    payload: list[dict] = []
    for order, fund in rows:
        payload.append(
            {
                "fund_code": fund.code,
                "name": _fund_short_name(fund, lang),
                "icon_name": _fund_icon_name(fund),
                "direction": order.side,
                "amount_usdt": order.amount_usdt,
                "price_usdt": order.price_usdt,
                "created_at": order.created_at,
                "status": order.status,
            }
        )
    return payload


def _build_assets_block(db: Session, user: User | None, lang: str) -> list[dict]:
    if not user:
        return []

    positions = (
        db.query(UserFundPosition)
        .filter(UserFundPosition.user_id == user.id)
        .all()
    )
    stats = (
        db.query(UserFundPositionStats)
        .filter(UserFundPositionStats.user_id == user.id)
        .all()
    )

    pos_by_fund = {row.fund_id: row for row in positions}
    stats_by_fund = {row.fund_id: row for row in stats}

    fund_ids = set(pos_by_fund.keys()) | set(stats_by_fund.keys())
    if not fund_ids:
        return []

    funds = (
        db.query(Fund)
        .filter(Fund.id.in_(fund_ids))
        .order_by(Fund.sort_order.asc(), Fund.id.asc())
        .all()
    )

    payload: list[dict] = []
    for fund in funds:
        pos_row = pos_by_fund.get(fund.id)
        stats_row = stats_by_fund.get(fund.id)

        shares = _to_decimal(pos_row.shares if pos_row else 0)
        avg_entry_price = _to_decimal(stats_row.avg_entry_price_usdt if stats_row else 0)

        current_snap = _build_market_snapshot(db, fund, lang)
        current_price = _to_decimal(current_snap["current_price_usdt"])

        position_result_pct = None
        if current_price > 0:
            position_result_pct = (avg_entry_price / current_price - Decimal("1")) * Decimal("100")

        position_result_usdt = (avg_entry_price - current_price) * shares

        payload.append(
            {
                "fund_code": fund.code,
                "name": _fund_short_name(fund, lang),
                "icon_name": _fund_icon_name(fund),
                "avg_entry_price_usdt": avg_entry_price,
                "current_price_usdt": current_price,
                "shares": shares,
                "position_result_pct": position_result_pct,
                "position_result_usdt": position_result_usdt,
            }
        )

    return payload


def _build_fund_info(db: Session, fund: Fund, lang: str) -> dict:
    latest_row = _get_latest_nav_row(db, fund.id)

    first_row = (
        db.query(FundNavMinute)
        .filter(FundNavMinute.fund_id == fund.id)
        .order_by(FundNavMinute.ts_utc.asc())
        .first()
    )

    launch_date = None
    if first_row and first_row.ts_utc:
        launch_date = first_row.ts_utc.strftime("%d.%m.%Y")

    return {
        "fund_code": fund.code,
        "full_name": _fund_full_name(fund, lang),
        "aum_usdt": _round_0(latest_row.nav_usdt if latest_row else 0),
        "shares_outstanding": _round_0(latest_row.shares_outstanding if latest_row else 0),
        "launch_date": launch_date,
        "benchmark_name": _fund_benchmark_name(fund, lang),
        "management_fee_pct": getattr(fund, "management_fee_pct", None),
        "performance_fee_pct": getattr(fund, "performance_fee_pct", None),
    }


def get_terminal_page_payload(
    db: Session,
    user: User | None,
    lang: str,
    fund_code: str,
) -> dict | None:
    fund = _get_fund_by_code(db, fund_code)
    if not fund:
        return None

    market_snapshot = _build_market_snapshot(db, fund, lang)
    fund_menu = _build_fund_menu(db, lang)
    trade_history = _build_trade_history(db, user, lang)
    asset_rows = _build_assets_block(db, user, lang)
    fund_info = _build_fund_info(db, fund, lang)

    available_usdt = ZERO
    available_shares_current_fund = ZERO

    if user:
        portfolio = get_user_portfolio(db, user, lang)
        available_usdt = _to_decimal(portfolio.get("stable_balance") or 0)

        current_position = (
            db.query(UserFundPosition)
            .filter(
                UserFundPosition.user_id == user.id,
                UserFundPosition.fund_id == fund.id,
            )
            .first()
        )
        if current_position:
            available_shares_current_fund = _to_decimal(current_position.shares)

    return {
        "current_fund": market_snapshot,
        "fund_menu": fund_menu,
        "trade_history": trade_history,
        "asset_rows": asset_rows,
        "fund_info": fund_info,
        "form_state": {
            "available_usdt": available_usdt,
            "available_shares_current_fund": available_shares_current_fund,
        },
    }