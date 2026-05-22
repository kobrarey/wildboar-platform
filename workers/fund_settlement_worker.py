from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timezone

from dotenv import load_dotenv

from app.config import settings
from app.db import SessionLocal
from app.models import FundSettlementBatch
from app.settlement.batch_service import (
    get_default_settlement_date,
    run_settlement_batches_once,
)
from app.settlement.statuses import (
    BATCH_STATUS_GAS_CHECKING,
    BATCH_STATUS_GAS_READY,
)
from app.settlement.transfer_service import collect_buy_usdt_for_batch

if sys.platform.startswith("win"):
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("workers.fund_settlement_worker")


SUPPORTED_FUNDS = {
    "btc_fund",
    "defi_sniper",
    "wb10",
    "wb_test",
    "wb_defi",
    "wb_web3",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_settlement_date(raw: str | None) -> date | None:
    if not raw:
        return None

    try:
        return date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid --settlement-date={raw!r}. Expected YYYY-MM-DD."
        ) from exc


def _parse_fund_codes(raw: str | None) -> list[str] | None:
    if not raw:
        return None

    out: list[str] = []
    for item in raw.split(","):
        code = item.strip().lower()
        if not code:
            continue
        if code not in SUPPORTED_FUNDS:
            allowed = ", ".join(sorted(SUPPORTED_FUNDS))
            raise argparse.ArgumentTypeError(
                f"Unsupported fund code: {code}. Allowed: {allowed}"
            )
        out.append(code)

    return out or None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Wild Boar fund settlement batch worker."
    )

    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Run one settlement batch pass and exit.",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Allow manual local run even when SETTLEMENT_ENABLED=false. "
            "Do not use on production unless explicitly approved."
        ),
    )

    parser.add_argument(
        "--settlement-date",
        default=None,
        help=(
            "Optional settlement date in YYYY-MM-DD. "
            "Default: previous UTC date."
        ),
    )

    parser.add_argument(
        "--fund-codes",
        default=None,
        help=(
            "Optional comma-separated fund codes. "
            "Default: all active funds."
        ),
    )

    parser.add_argument(
        "--create-no-orders",
        action="store_true",
        help="Create no_orders batches for funds without pending orders.",
    )

    parser.add_argument(
        "--skip-buy-collection",
        action="store_true",
        help=(
            "Only create/calculate settlement batches. "
            "Do not run user gas top-up / buy USDT collection."
        ),
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Run inside a transaction and rollback before exit. "
            "Also prevents real buy-side on-chain sends."
        ),
    )

    parser.add_argument(
        "--sleep-sec",
        type=int,
        default=60,
        help="Loop sleep seconds when running without --run-once.",
    )

    return parser.parse_args()


def _mark_batch_gas_ready(db, *, batch_id: int) -> FundSettlementBatch | None:
    """
    Stage 21 transition:
    gas_checking -> gas_ready

    Settlement wallet gas top-up is handled by a separate worker.
    This transition allows buy-side collection to proceed after batch calculation.
    """
    batch = (
        db.query(FundSettlementBatch)
        .filter(FundSettlementBatch.id == batch_id)
        .with_for_update()
        .first()
    )

    if batch is None:
        return None

    if batch.status == BATCH_STATUS_GAS_CHECKING:
        batch.status = BATCH_STATUS_GAS_READY
        batch.updated_at = utcnow()
        db.add(batch)
        db.flush()

    return batch


def _collect_buy_usdt_for_results(
    db,
    *,
    results,
    dry_run: bool,
    skip_buy_collection: bool,
) -> None:
    if skip_buy_collection:
        log.info("Buy-side USDT collection skipped by --skip-buy-collection.")
        return

    for result in results:
        if not result.batch_id:
            log.info(
                "Buy collection skipped fund=%s settlement_date=%s reason=no_batch status=%s",
                result.fund_code,
                result.settlement_date.isoformat(),
                result.status,
            )
            continue

        if result.status != BATCH_STATUS_GAS_CHECKING:
            log.info(
                "Buy collection skipped fund=%s batch_id=%s reason=status_not_gas_checking status=%s",
                result.fund_code,
                result.batch_id,
                result.status,
            )
            continue

        batch = _mark_batch_gas_ready(db, batch_id=result.batch_id)
        if batch is None:
            log.warning(
                "Buy collection skipped fund=%s batch_id=%s reason=batch_not_found",
                result.fund_code,
                result.batch_id,
            )
            continue

        collection_result = collect_buy_usdt_for_batch(
            db,
            batch_id=result.batch_id,
            dry_run=dry_run,
        )

        log.info(
            "Buy collection result fund=%s batch_id=%s status=%s buy_orders=%s "
            "collected=%s pending=%s failed=%s message=%s",
            collection_result.fund_code,
            collection_result.batch_id,
            collection_result.batch_status,
            collection_result.buy_orders_count,
            collection_result.collected_orders_count,
            collection_result.pending_orders_count,
            collection_result.failed_orders_count,
            collection_result.message,
        )


def _run_once(args: argparse.Namespace) -> int:
    settlement_date = _parse_settlement_date(args.settlement_date)
    fund_codes = _parse_fund_codes(args.fund_codes)

    actual_date = settlement_date or get_default_settlement_date()

    log.info(
        "Settlement worker run_once started settlement_date=%s fund_codes=%s dry_run=%s "
        "create_no_orders=%s skip_buy_collection=%s",
        actual_date.isoformat(),
        fund_codes or "active_funds",
        bool(args.dry_run),
        bool(args.create_no_orders),
        bool(args.skip_buy_collection),
    )

    db = SessionLocal()

    try:
        results = run_settlement_batches_once(
            db,
            settlement_date=actual_date,
            fund_codes=fund_codes,
            create_no_orders=bool(args.create_no_orders),
            # Keep the whole Stage 21 run under worker control.
            # In dry-run everything is rolled back at the end.
            # In real mode we commit after batch creation + buy collection.
            commit=False,
        )

        for result in results:
            log.info(
                "Settlement batch result fund=%s settlement_date=%s batch_id=%s status=%s "
                "orders=%s buys=%s redeems=%s total_buy_usdt=%s total_redeem_shares=%s "
                "total_redeem_usdt=%s net_cash_usdt=%s planned_issue=%s planned_redeem=%s "
                "planned_net_shares=%s message=%s",
                result.fund_code,
                result.settlement_date.isoformat(),
                result.batch_id,
                result.status,
                result.orders_count,
                result.buy_orders_count,
                result.redeem_orders_count,
                result.total_buy_usdt,
                result.total_redeem_shares,
                result.total_redeem_usdt,
                result.net_cash_usdt,
                result.planned_shares_to_issue,
                result.planned_shares_to_redeem,
                result.planned_net_shares_change,
                result.message,
            )

        _collect_buy_usdt_for_results(
            db,
            results=results,
            dry_run=bool(args.dry_run),
            skip_buy_collection=bool(args.skip_buy_collection),
        )

        if args.dry_run:
            db.rollback()
            log.info("Settlement dry-run rollback completed.")
        else:
            db.commit()
            log.info("Settlement DB commit completed.")

        log.info("Settlement worker run_once completed.")
        return 0

    except Exception as exc:
        db.rollback()
        log.exception("Settlement worker run_once failed: %s", exc)
        return 1

    finally:
        db.close()


def _should_run_now() -> bool:
    now = datetime.now(timezone.utc)
    return (
        now.hour == int(settings.SETTLEMENT_RUN_HOUR_UTC)
        and now.minute == int(settings.SETTLEMENT_RUN_MINUTE_UTC)
    )


def _run_loop(args: argparse.Namespace) -> int:
    sleep_sec = max(int(args.sleep_sec), 10)

    log.info(
        "Settlement worker loop started run_hour=%s run_minute=%s sleep_sec=%s",
        settings.SETTLEMENT_RUN_HOUR_UTC,
        settings.SETTLEMENT_RUN_MINUTE_UTC,
        sleep_sec,
    )

    last_run_key: str | None = None

    while True:
        now = datetime.now(timezone.utc)
        run_key = now.strftime("%Y-%m-%d %H:%M")

        if _should_run_now() and run_key != last_run_key:
            rc = _run_once(args)
            last_run_key = run_key

            if rc != 0:
                log.warning("Settlement scheduled run failed rc=%s", rc)

        time.sleep(sleep_sec)


def main() -> int:
    load_dotenv()

    args = parse_args()

    if not settings.SETTLEMENT_ENABLED and not args.force:
        log.info(
            "Settlement worker disabled: SETTLEMENT_ENABLED=false. "
            "Exit without changes. Use --force only for approved local/manual checks."
        )
        return 0

    if not args.run_once:
        log.warning(
            "Settlement worker started without --run-once. This loop mode is for future "
            "production scheduling, not for Stage 21 local checks."
        )
        return _run_loop(args)

    return _run_once(args)


if __name__ == "__main__":
    raise SystemExit(main())