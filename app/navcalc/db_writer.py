from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Fund, FundChartDaily, FundChartMinute, FundNavMinute
from app.navcalc.schemas import MinuteState


def _minute_floor(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)


def _day_floor(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


def get_fund_by_code(db: Session, fund_code: str) -> Fund | None:
    stmt = select(Fund).where(Fund.code == fund_code)
    return db.execute(stmt).scalar_one_or_none()


def get_fund_shares_outstanding_current(
    db: Session,
    *,
    fund_id: int,
) -> Decimal | None:
    stmt = select(Fund.shares_outstanding_current).where(Fund.id == fund_id)
    value = db.execute(stmt).scalar_one_or_none()

    if value is None:
        return None

    return Decimal(str(value))


def get_latest_nav_minute(
    db: Session,
    *,
    fund_id: int,
) -> FundNavMinute | None:
    return (
        db.query(FundNavMinute)
        .filter(FundNavMinute.fund_id == int(fund_id))
        .order_by(FundNavMinute.ts_utc.desc())
        .first()
    )


def upsert_nav_minute(
    db: Session,
    *,
    fund_id: int,
    minute_ts: datetime,
    nav_close_usdt: Decimal,
    shares_outstanding: Decimal,
) -> None:
    stmt = (
        pg_insert(FundNavMinute.__table__)
        .values(
            fund_id=fund_id,
            ts_utc=_minute_floor(minute_ts),
            nav_usdt=nav_close_usdt,
            shares_outstanding=shares_outstanding,
        )
        .on_conflict_do_update(
            index_elements=["fund_id", "ts_utc"],
            set_={
                "nav_usdt": nav_close_usdt,
                "shares_outstanding": shares_outstanding,
            },
        )
    )
    db.execute(stmt)
    db.commit()


def upsert_chart_minute(
    db: Session,
    *,
    fund_id: int,
    minute_ts: datetime,
    open_price: Decimal,
    high_price: Decimal,
    low_price: Decimal,
    close_price: Decimal,
) -> None:
    stmt = (
        pg_insert(FundChartMinute.__table__)
        .values(
            fund_id=fund_id,
            ts_utc=_minute_floor(minute_ts),
            open=open_price,
            high=high_price,
            low=low_price,
            close=close_price,
            volume=None,
        )
        .on_conflict_do_update(
            index_elements=["fund_id", "ts_utc"],
            set_={
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": None,
            },
        )
    )
    db.execute(stmt)
    db.commit()


def upsert_chart_daily_from_minute_state(
    db: Session,
    *,
    fund_id: int,
    state: MinuteState,
) -> None:
    """
    Update daily price candle from the accepted minute NAV state.

    fund_chart_daily stores share price OHLC, not fund NAV.
    Daily open is fixed:
    - if a daily row already exists, open is not changed;
    - if this is the first row of a new day and previous daily row exists,
      open = previous daily close;
    - otherwise open = current minute open price.
    """
    day_ts = _day_floor(state.minute_ts)

    open_price = state.open_nav / state.shares_outstanding
    high_price = state.high_nav / state.shares_outstanding
    low_price = state.low_nav / state.shares_outstanding
    close_price = state.close_nav / state.shares_outstanding

    previous_row = (
        db.query(FundChartDaily)
        .filter(
            FundChartDaily.fund_id == fund_id,
            FundChartDaily.ts_utc < day_ts,
        )
        .order_by(FundChartDaily.ts_utc.desc())
        .first()
    )

    daily_open = Decimal(str(previous_row.close)) if previous_row else open_price
    daily_high = max(daily_open, high_price)
    daily_low = min(daily_open, low_price)

    table = FundChartDaily.__table__

    stmt = (
        pg_insert(table)
        .values(
            fund_id=fund_id,
            ts_utc=day_ts,
            open=daily_open,
            high=daily_high,
            low=daily_low,
            close=close_price,
            volume=None,
        )
        .on_conflict_do_update(
            index_elements=["fund_id", "ts_utc"],
            set_={
                # Do not update open for an existing daily candle.
                "high": func.greatest(table.c.high, high_price),
                "low": func.least(table.c.low, low_price),
                "close": close_price,
                "volume": None,
            },
        )
    )

    db.execute(stmt)
    db.commit()


def upsert_minute_state(
    db: Session,
    *,
    fund_id: int,
    state: MinuteState,
) -> None:
    upsert_nav_minute(
        db,
        fund_id=fund_id,
        minute_ts=state.minute_ts,
        nav_close_usdt=state.close_nav,
        shares_outstanding=state.shares_outstanding,
    )

    open_price = state.open_nav / state.shares_outstanding
    high_price = state.high_nav / state.shares_outstanding
    low_price = state.low_nav / state.shares_outstanding
    close_price = state.close_nav / state.shares_outstanding

    upsert_chart_minute(
        db,
        fund_id=fund_id,
        minute_ts=state.minute_ts,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
    )

    upsert_chart_daily_from_minute_state(
        db,
        fund_id=fund_id,
        state=state,
    )
