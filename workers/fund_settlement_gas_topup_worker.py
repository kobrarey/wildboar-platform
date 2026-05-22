from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timezone

from dotenv import load_dotenv

from app.config import settings
from app.db import SessionLocal
from app.settlement.batch_service import get_default_settlement_date
from app.settlement.gas_service import top_up_settlement_wallets_once

if sys.platform.startswith("win"):
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("workers.fund_settlement_gas_topup_worker")


def _parse_settlement_date(raw: str | None) -> date | None:
    if not raw:
        return None
    try:
        return date.fromisoformat(raw.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid --settlement-date={raw!r}. Expected YYYY-MM-DD."
        ) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Top up BNB gas on active fund settlement wallets."
    )

    parser.add_argument(
        "--run-now",
        action="store_true",
        help="Run one gas top-up pass and exit.",
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
        "--dry-run",
        action="store_true",
        help="Calculate top-ups and create rollback-safe rows, but do not send BNB.",
    )

    parser.add_argument(
        "--retry",
        action="store_true",
        help="Use retry-mode fallback logic for 23:50 run.",
    )

    parser.add_argument(
        "--settlement-date",
        default=None,
        help="Optional settlement date in YYYY-MM-DD. Default: previous UTC date.",
    )

    parser.add_argument(
        "--sleep-sec",
        type=int,
        default=60,
        help="Loop sleep seconds when running without --run-now.",
    )

    return parser.parse_args()


def _run_once(args: argparse.Namespace) -> int:
    settlement_date = _parse_settlement_date(args.settlement_date)
    actual_date = settlement_date or get_default_settlement_date()

    log.info(
        "Settlement gas top-up run started settlement_date=%s retry=%s dry_run=%s",
        actual_date.isoformat(),
        bool(args.retry),
        bool(args.dry_run),
    )

    db = SessionLocal()

    try:
        results = top_up_settlement_wallets_once(
            db,
            settlement_date=actual_date,
            retry_mode=bool(args.retry),
            dry_run=bool(args.dry_run),
        )

        if args.dry_run:
            db.rollback()
            log.info("Settlement gas top-up dry-run rollback completed.")
        else:
            db.commit()

        for result in results:
            log.info(
                "Gas top-up result fund=%s batch_id=%s wallet=%s status=%s "
                "bnb_balance=%s target_bnb=%s min_operational_bnb=%s "
                "amount_sent_bnb=%s tx_hash=%s message=%s",
                result.fund_code,
                result.batch_id,
                result.wallet_address,
                result.status,
                result.bnb_balance,
                result.target_bnb,
                result.min_operational_bnb,
                result.amount_sent_bnb,
                result.tx_hash,
                result.message,
            )

        log.info("Settlement gas top-up run completed.")
        return 0

    except Exception as exc:
        db.rollback()
        log.exception("Settlement gas top-up run failed: %s", exc)
        return 1

    finally:
        db.close()


def _should_run_initial() -> bool:
    now = datetime.now(timezone.utc)
    return (
        now.hour == int(settings.SETTLEMENT_RUN_HOUR_UTC)
        and now.minute == 0
    )


def _should_run_retry() -> bool:
    now = datetime.now(timezone.utc)
    return (
        now.hour == int(settings.SETTLEMENT_GAS_TOPUP_RETRY_HOUR_UTC)
        and now.minute == int(settings.SETTLEMENT_GAS_TOPUP_RETRY_MINUTE_UTC)
    )


def _run_loop(args: argparse.Namespace) -> int:
    sleep_sec = max(int(args.sleep_sec), 10)
    last_run_key: str | None = None

    log.info(
        "Settlement gas top-up loop started retry_hour=%s retry_minute=%s sleep_sec=%s",
        settings.SETTLEMENT_GAS_TOPUP_RETRY_HOUR_UTC,
        settings.SETTLEMENT_GAS_TOPUP_RETRY_MINUTE_UTC,
        sleep_sec,
    )

    while True:
        now = datetime.now(timezone.utc)
        run_key = now.strftime("%Y-%m-%d %H:%M")

        should_initial = _should_run_initial()
        should_retry = _should_run_retry()

        if (should_initial or should_retry) and run_key != last_run_key:
            args.retry = should_retry
            rc = _run_once(args)
            last_run_key = run_key

            if rc != 0:
                log.warning("Settlement gas scheduled run failed rc=%s", rc)

        time.sleep(sleep_sec)


def main() -> int:
    load_dotenv()

    args = parse_args()

    if not settings.SETTLEMENT_ENABLED and not args.force:
        log.info(
            "Settlement gas top-up worker disabled: SETTLEMENT_ENABLED=false. "
            "Exit without changes. Use --force only for approved local/manual checks."
        )
        return 0

    if args.run_now:
        return _run_once(args)

    log.warning(
        "Settlement gas top-up worker started without --run-now. "
        "Loop mode is for future production scheduling, not Stage 21 local checks."
    )
    return _run_loop(args)


if __name__ == "__main__":
    raise SystemExit(main())