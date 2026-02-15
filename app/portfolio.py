from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.orm import Session
from sqlalchemy import func as sa_func

from app.models import (
    User, Fund, FundNavMinute, UserFundPosition, UserPortfolioDaily
)


def get_user_portfolio(db: Session, user: User, lang: str) -> dict:
    # 1) USDT-баланс — на этом этапе просто 0
    stable_balance = Decimal("0")

    # 2) Список фондов
    funds = (
        db.query(Fund)
        .filter(Fund.is_active == True)
        .order_by(Fund.category, Fund.sort_order, Fund.id)
        .all()
    )

    # 3) Позиции пользователя по фондам
    positions = (
        db.query(UserFundPosition)
        .filter(UserFundPosition.user_id == user.id)
        .all()
    )
    pos_by_fund = {p.fund_id: p for p in positions}

    # 4) Текущие цены (последняя запись fund_nav_minute по каждому фонду)
    subq = (
        db.query(
            FundNavMinute.fund_id,
            sa_func.max(FundNavMinute.ts_utc).label("max_ts"),
        )
        .group_by(FundNavMinute.fund_id)
        .subquery()
    )

    prices_rows = (
        db.query(
            FundNavMinute.fund_id,
            FundNavMinute.nav_usdt,
            FundNavMinute.shares_outstanding,
        )
        .join(
            subq,
            (FundNavMinute.fund_id == subq.c.fund_id)
            & (FundNavMinute.ts_utc == subq.c.max_ts),
        )
        .all()
    )

    nav_by_fund = {r.fund_id: r.nav_usdt for r in prices_rows}
    shares_out_by_fund = {r.fund_id: r.shares_outstanding for r in prices_rows}

    # 5) Собираем payload
    funds_payload = []
    total_balance = Decimal("0")

    for fund in funds:
        nav_usdt = Decimal(nav_by_fund.get(fund.id) or 0)
        shares_outstanding = Decimal(shares_out_by_fund.get(fund.id) or 0)

        if shares_outstanding > 0:
            price = nav_usdt / shares_outstanding
        else:
            price = Decimal("0")

        shares = Decimal(pos_by_fund[fund.id].shares) if fund.id in pos_by_fund else Decimal("0")
        value = price * shares

        total_balance += value

        name = fund.name_ru if lang == "ru" else fund.name_en

        funds_payload.append(
            {
                "id": fund.id,
                "code": fund.code,
                "category": fund.category,
                "name": name,
                "price": price,
                "shares": shares,
                "value": value,
            }
        )

    # 6) Баланс "вчера" из user_portfolio_daily (если есть)
    today_utc = datetime.now(timezone.utc).date()
    yesterday = today_utc - timedelta(days=1)

    prev_row = (
        db.query(UserPortfolioDaily)
        .filter(
            UserPortfolioDaily.user_id == user.id,
            UserPortfolioDaily.date_utc == yesterday,
        )
        .first()
    )

    prev_balance = Decimal(prev_row.balance_usdt) if prev_row else None

    if prev_balance is not None and prev_balance > 0:
        daily_change_pct = (total_balance / prev_balance - Decimal("1")) * Decimal("100")
    else:
        daily_change_pct = None

    return {
        "current_balance": total_balance,
        "prev_balance": prev_balance,
        "daily_change_pct": daily_change_pct,
        "stable_balance": stable_balance,
        "stable_symbol": "USDT",
        "funds": funds_payload,
    }
