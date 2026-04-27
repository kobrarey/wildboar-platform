from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Fund, FundChartMinute, FundNavMinute, FundNavSample
from app.navcalc.schemas import MinuteCandle, NavSample


def _minute_floor(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)


def get_fund_by_code(db: Session, fund_code: str) -> Fund | None:
    stmt = select(Fund).where(Fund.code == fund_code)
    return db.execute(stmt).scalar_one_or_none()


def insert_nav_sample(db: Session, fund_id: int, sample: NavSample) -> None:
    stmt = (
        pg_insert(FundNavSample.__table__)
        .values(
            fund_id=fund_id,
            sample_ts=sample.sample_ts,
            nav_usdt=sample.nav_usd,
            source=sample.source,
            sanity_check_passed=sample.sanity_check_passed,
        )
        .on_conflict_do_nothing(
            index_elements=["fund_id", "sample_ts"],
        )
    )
    db.execute(stmt)
    db.commit()


def load_samples_for_minute(
    db: Session,
    fund_id: int,
    minute_ts: datetime,
) -> list[FundNavSample]:
    minute_ts = _minute_floor(minute_ts)
    minute_end = minute_ts + timedelta(minutes=1)

    stmt = (
        select(FundNavSample)
        .where(FundNavSample.fund_id == fund_id)
        .where(FundNavSample.sample_ts >= minute_ts)
        .where(FundNavSample.sample_ts < minute_end)
        .order_by(FundNavSample.sample_ts.asc())
    )
    return list(db.execute(stmt).scalars().all())


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


def write_completed_minute(
    db: Session,
    *,
    fund_id: int,
    nav_candle: MinuteCandle,
    shares_outstanding: Decimal,
) -> None:
    upsert_nav_minute(
        db,
        fund_id=fund_id,
        minute_ts=nav_candle.minute_ts,
        nav_close_usdt=nav_candle.close,
        shares_outstanding=shares_outstanding,
    )

    open_price = nav_candle.open / shares_outstanding
    high_price = nav_candle.high / shares_outstanding
    low_price = nav_candle.low / shares_outstanding
    close_price = nav_candle.close / shares_outstanding

    upsert_chart_minute(
        db,
        fund_id=fund_id,
        minute_ts=nav_candle.minute_ts,
        open_price=open_price,
        high_price=high_price,
        low_price=low_price,
        close_price=close_price,
    )