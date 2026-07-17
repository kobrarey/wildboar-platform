from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import FundSettlementBatch
from app.settlement.statuses import BATCH_STATUS_CREATED


ZERO = Decimal("0")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_or_create_settlement_batch(
    db: Session,
    *,
    fund_id: int,
    settlement_date: date,
    cutoff_ts: datetime,
    settlement_ts: datetime,
) -> FundSettlementBatch:
    existing = (
        db.query(FundSettlementBatch)
        .filter(
            FundSettlementBatch.fund_id == int(fund_id),
            FundSettlementBatch.settlement_date == settlement_date,
        )
        .with_for_update()
        .first()
    )

    if existing is not None:
        return existing

    now = utcnow()

    batch = FundSettlementBatch(
        fund_id=int(fund_id),
        settlement_date=settlement_date,
        cutoff_ts=cutoff_ts,
        settlement_ts=settlement_ts,
        status=BATCH_STATUS_CREATED,
        total_buy_usdt=ZERO,
        total_redeem_shares=ZERO,
        total_redeem_usdt=ZERO,
        net_cash_usdt=ZERO,
        planned_shares_to_issue=ZERO,
        planned_shares_to_redeem=ZERO,
        planned_net_shares_change=ZERO,
        created_at=now,
        updated_at=now,
    )

    try:
        with db.begin_nested():
            db.add(batch)
            db.flush()

        return batch

    except IntegrityError:
        existing = (
            db.query(FundSettlementBatch)
            .filter(
                FundSettlementBatch.fund_id == int(fund_id),
                FundSettlementBatch.settlement_date == settlement_date,
            )
            .with_for_update()
            .one()
        )

        return existing