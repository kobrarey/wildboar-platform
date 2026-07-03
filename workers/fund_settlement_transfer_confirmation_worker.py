from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import Fund, FundSettlementBatch, FundSettlementTransfer
from app.settlement.statuses import (
    BATCH_STATUS_COLLECTING_BUY_USDT,
    BATCH_STATUS_GAS_CHECKING,
    TRANSFER_STATUS_SENT,
    TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT,
    TRANSFER_TYPE_USER_WALLET_GAS_TOPUP,
)
from app.settlement.transfer_service import confirm_sent_settlement_transfer


if sys.platform.startswith("win"):
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("workers.fund_settlement_transfer_confirmation_worker")

ACTIVE_BATCH_STATUSES = {
    BATCH_STATUS_COLLECTING_BUY_USDT,
    BATCH_STATUS_GAS_CHECKING,
}

SUPPORTED_TRANSFER_TYPES = {
    TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT,
    TRANSFER_TYPE_USER_WALLET_GAS_TOPUP,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_fund_codes(raw: str | None) -> set[str]:
    return {
        item.strip().lower()
        for item in str(raw or "").replace(";", ",").split(",")
        if item.strip()
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Confirm already-sent settlement BSC transfers without sending new txs."
    )
    parser.add_argument("--fund-code", default=None)
    parser.add_argument("--batch-id", type=int, default=None)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--sleep-sec", type=int, default=None)
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _scan_sent_transfer_ids(
    db: Session,
    *,
    fund_code: str | None,
    batch_id: int | None,
    limit: int,
) -> list[int]:
    q = (
        db.query(FundSettlementTransfer.id)
        .join(FundSettlementBatch, FundSettlementBatch.id == FundSettlementTransfer.batch_id)
        .join(Fund, Fund.id == FundSettlementTransfer.fund_id)
        .filter(
            FundSettlementTransfer.status == TRANSFER_STATUS_SENT,
            FundSettlementTransfer.tx_hash.isnot(None),
            FundSettlementTransfer.confirmed_at.is_(None),
            FundSettlementTransfer.transfer_type.in_(sorted(SUPPORTED_TRANSFER_TYPES)),
            FundSettlementBatch.status.in_(sorted(ACTIVE_BATCH_STATUSES)),
        )
    )

    if fund_code:
        q = q.filter(Fund.code == fund_code.strip().lower())

    if batch_id is not None:
        q = q.filter(FundSettlementTransfer.batch_id == int(batch_id))

    q = q.order_by(
        FundSettlementTransfer.sent_at.asc().nullslast(),
        FundSettlementTransfer.id.asc(),
    ).limit(max(int(limit), 1))

    return [int(row[0]) for row in q.all()]


def _log_stale_sent_transfers(
    db: Session,
    *,
    fund_code: str | None,
    batch_id: int | None,
) -> None:
    lookback_hours = int(settings.SETTLEMENT_TRANSFER_CONFIRMATION_LOOKBACK_HOURS)
    if lookback_hours <= 0:
        return

    cutoff = utcnow() - timedelta(hours=lookback_hours)

    q = (
        db.query(FundSettlementTransfer, FundSettlementBatch, Fund)
        .join(FundSettlementBatch, FundSettlementBatch.id == FundSettlementTransfer.batch_id)
        .join(Fund, Fund.id == FundSettlementTransfer.fund_id)
        .filter(
            FundSettlementTransfer.status == TRANSFER_STATUS_SENT,
            FundSettlementTransfer.tx_hash.isnot(None),
            FundSettlementTransfer.confirmed_at.is_(None),
            FundSettlementTransfer.sent_at.isnot(None),
            FundSettlementTransfer.sent_at < cutoff,
            FundSettlementTransfer.transfer_type.in_(sorted(SUPPORTED_TRANSFER_TYPES)),
            FundSettlementBatch.status.in_(sorted(ACTIVE_BATCH_STATUSES)),
        )
    )

    if fund_code:
        q = q.filter(Fund.code == fund_code.strip().lower())

    if batch_id is not None:
        q = q.filter(FundSettlementTransfer.batch_id == int(batch_id))

    for transfer, batch, fund in q.order_by(FundSettlementTransfer.sent_at.asc()).limit(20).all():
        log.warning(
            "Stale sent settlement transfer fund=%s batch_id=%s order_id=%s "
            "transfer_id=%s tx_hash=%s sent_at=%s batch_status=%s",
            fund.code,
            batch.id,
            transfer.order_id,
            transfer.id,
            transfer.tx_hash,
            transfer.sent_at,
            batch.status,
        )


def run_once(args: argparse.Namespace) -> int:
    fund_code = (args.fund_code or "").strip().lower() or None
    batch_id = int(args.batch_id) if args.batch_id is not None else None
    limit = max(int(args.limit or 50), 1)

    if fund_code is None:
        allowed = _parse_fund_codes(settings.SETTLEMENT_TRANSFER_CONFIRMATION_FUND_CODES)
        if allowed:
            # No CLI fund-code means all configured funds, handled by filtering per id scan below.
            # For strict production use, pass --fund-code wb_test explicitly.
            log.info("Configured confirmation fund codes: %s", sorted(allowed))

    db = SessionLocal()
    processed = 0
    errors = 0

    try:
        _log_stale_sent_transfers(db, fund_code=fund_code, batch_id=batch_id)

        ids = _scan_sent_transfer_ids(
            db,
            fund_code=fund_code,
            batch_id=batch_id,
            limit=limit,
        )

        if not ids:
            log.info(
                "No sent settlement transfers to confirm fund_code=%s batch_id=%s dry_run=%s",
                fund_code or "",
                batch_id or "",
                bool(args.dry_run),
            )
            return 0

        for transfer_id in ids:
            try:
                result = confirm_sent_settlement_transfer(
                    db,
                    transfer_id,
                    dry_run=bool(args.dry_run),
                    min_confirmations=int(settings.SETTLEMENT_TRANSFER_CONFIRMATION_MIN_CONFIRMATIONS),
                )

                if args.dry_run:
                    db.rollback()
                else:
                    db.commit()

                processed += 1
                log.info(
                    "Transfer confirmation result transfer_id=%s batch_id=%s order_id=%s "
                    "tx_hash=%s action=%s receipt_status=%s confirmations=%s "
                    "batch_status=%s order_status=%s message=%s",
                    result.transfer_id,
                    result.batch_id,
                    result.order_id,
                    result.tx_hash,
                    result.action,
                    result.receipt_status,
                    result.confirmations,
                    result.batch_status,
                    result.order_status,
                    result.message,
                )

            except Exception as exc:
                errors += 1
                db.rollback()
                log.exception("Transfer confirmation failed transfer_id=%s error=%s", transfer_id, exc)

        log.info(
            "Settlement transfer confirmation pass completed processed=%s errors=%s dry_run=%s",
            processed,
            errors,
            bool(args.dry_run),
        )
        return 1 if errors else 0

    finally:
        db.close()


def main() -> int:
    load_dotenv()
    args = parse_args()

    if not settings.SETTLEMENT_TRANSFER_CONFIRMATION_ENABLED and not args.run_now:
        log.info(
            "Settlement transfer confirmation worker disabled: "
            "SETTLEMENT_TRANSFER_CONFIRMATION_ENABLED=false. "
            "Use --run-now for approved one-shot checks."
        )
        return 0

    if args.run_now:
        return run_once(args)

    sleep_sec = int(args.sleep_sec or settings.SETTLEMENT_TRANSFER_CONFIRMATION_POLL_SEC)
    sleep_sec = max(sleep_sec, 10)

    log.info(
        "Settlement transfer confirmation worker loop started sleep_sec=%s dry_run=%s",
        sleep_sec,
        bool(args.dry_run),
    )

    while True:
        rc = run_once(args)
        if rc != 0:
            log.warning("Settlement transfer confirmation pass returned rc=%s", rc)
        time.sleep(sleep_sec)


if __name__ == "__main__":
    raise SystemExit(main())