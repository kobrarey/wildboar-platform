from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Fund, FundChartMinute, FundNavMinute
from app.navcalc.schemas import MinuteState


def _minute_floor(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)


def get_fund_by_code(db: Session, fund_code: str) -> Fund | None:
    stmt = select(Fund).where(Fund.code == fund_code)
    return db.execute(stmt).scalar_one_or_none()


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