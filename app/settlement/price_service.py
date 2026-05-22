from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models import FundNavMinute, FundSettlementBatch
from app.settlement.statuses import (
    BATCH_STATUS_FAILED,
    BATCH_STATUS_PRICE_FIXED,
)


class SettlementPriceError(RuntimeError):
    pass


@dataclass(frozen=True)
class SettlementPriceSnapshot:
    fund_id: int
    price_ts: datetime
    nav_usdt: Decimal
    shares_outstanding_before: Decimal
    settlement_price_usdt: Decimal
    age_sec: int


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _mark_batch_failed(
    batch: FundSettlementBatch,
    *,
    error: str,
) -> None:
    now = utcnow()
    batch.status = BATCH_STATUS_FAILED
    batch.error = error
    batch.updated_at = now


def get_latest_price_snapshot(
    db: Session,
    *,
    fund_id: int,
    settlement_ts: datetime,
    max_age_sec: int | None = None,
) -> SettlementPriceSnapshot:
    """
    Read the latest valid NAV minute row at or before settlement_ts.

    Does not modify DB.
    Does not commit.

    Settlement price:
        settlement_price_usdt = nav_usdt / shares_outstanding
    """
    max_age = int(max_age_sec or settings.SETTLEMENT_PRICE_MAX_AGE_SEC)
    settlement_ts_utc = _as_utc(settlement_ts)

    row = (
        db.query(FundNavMinute)
        .filter(
            FundNavMinute.fund_id == fund_id,
            FundNavMinute.ts_utc <= settlement_ts_utc,
        )
        .order_by(FundNavMinute.ts_utc.desc())
        .first()
    )

    if row is None:
        raise SettlementPriceError(
            f"Settlement price missing: no fund_nav_minute row for fund_id={fund_id} "
            f"at or before {settlement_ts_utc.isoformat()}"
        )

    price_ts = _as_utc(row.ts_utc)
    age_sec = int((settlement_ts_utc - price_ts).total_seconds())

    if age_sec < 0:
        raise SettlementPriceError(
            f"Settlement price invalid: price_ts={price_ts.isoformat()} is after "
            f"settlement_ts={settlement_ts_utc.isoformat()}"
        )

    if age_sec > max_age:
        raise SettlementPriceError(
            f"Settlement price stale: fund_id={fund_id} price_ts={price_ts.isoformat()} "
            f"settlement_ts={settlement_ts_utc.isoformat()} age_sec={age_sec} "
            f"max_age_sec={max_age}"
        )

    nav_usdt = _to_decimal(row.nav_usdt)
    shares_outstanding = _to_decimal(row.shares_outstanding)

    if nav_usdt <= 0:
        raise SettlementPriceError(
            f"Settlement price invalid: non-positive nav_usdt={nav_usdt} "
            f"fund_id={fund_id} price_ts={price_ts.isoformat()}"
        )

    if shares_outstanding <= 0:
        raise SettlementPriceError(
            f"Settlement price invalid: non-positive shares_outstanding={shares_outstanding} "
            f"fund_id={fund_id} price_ts={price_ts.isoformat()}"
        )

    price = nav_usdt / shares_outstanding

    if price <= 0:
        raise SettlementPriceError(
            f"Settlement price invalid: non-positive price={price} "
            f"fund_id={fund_id} price_ts={price_ts.isoformat()}"
        )

    return SettlementPriceSnapshot(
        fund_id=fund_id,
        price_ts=price_ts,
        nav_usdt=nav_usdt,
        shares_outstanding_before=shares_outstanding,
        settlement_price_usdt=price,
        age_sec=age_sec,
    )


def fix_settlement_price_for_batch(
    db: Session,
    *,
    batch: FundSettlementBatch,
    max_age_sec: int | None = None,
) -> SettlementPriceSnapshot:
    """
    Fix settlement price for an existing batch.

    On success:
        batch.price_ts
        batch.nav_usdt
        batch.shares_outstanding_before
        batch.settlement_price_usdt
        batch.status = price_fixed
        batch.error = None
        batch.updated_at = now

    On failure:
        batch.status = failed
        batch.error = readable error
        batch.updated_at = now
        raises SettlementPriceError

    Does not commit.
    Caller controls transaction boundary and pricing unlock behavior.
    """
    try:
        snapshot = get_latest_price_snapshot(
            db,
            fund_id=batch.fund_id,
            settlement_ts=batch.settlement_ts,
            max_age_sec=max_age_sec,
        )

    except SettlementPriceError as exc:
        _mark_batch_failed(batch, error=str(exc))
        db.add(batch)
        db.flush()
        raise

    now = utcnow()

    batch.price_ts = snapshot.price_ts
    batch.nav_usdt = snapshot.nav_usdt
    batch.shares_outstanding_before = snapshot.shares_outstanding_before
    batch.settlement_price_usdt = snapshot.settlement_price_usdt
    batch.status = BATCH_STATUS_PRICE_FIXED
    batch.error = None
    batch.updated_at = now

    db.add(batch)
    db.flush()

    return snapshot