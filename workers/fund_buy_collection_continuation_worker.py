from __future__ import annotations

import argparse
import logging
import sys
import time

from dotenv import load_dotenv

from app.config import settings
from app.db import SessionLocal
from app.lifecycle import evaluate_live_gate
from app.settlement.buy_collection_continuation import (
    continue_buy_collection_for_active_batches,
    scan_active_collecting_buy_usdt_batch_ids,
)

if sys.platform.startswith("win"):
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("workers.fund_buy_collection_continuation_worker")


SUPPORTED_FUNDS = {
    "btc_fund",
    "defi_sniper",
    "wb10",
    "wb_test",
    "wb_defi",
    "wb_web3",
}


def _parse_csv(raw: str | None) -> list[str]:
    return [
        item.strip().lower()
        for item in str(raw or "").replace(";", ",").split(",")
        if item.strip()
    ]


def _parse_fund_codes(raw: str | None) -> list[str]:
    codes = _parse_csv(raw)

    for code in codes:
        if code not in SUPPORTED_FUNDS:
            allowed = ", ".join(sorted(SUPPORTED_FUNDS))
            raise argparse.ArgumentTypeError(
                f"Unsupported fund code: {code}. Allowed: {allowed}"
            )

    return codes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Continue active collecting_buy_usdt settlement batches by re-entering "
            "the existing buy collection service. Does not duplicate transfer logic."
        )
    )
    parser.add_argument("--fund-code", default=None)
    parser.add_argument("--fund-codes", default=None)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--sleep-sec", type=int, default=None)
    parser.add_argument("--run-now", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--live-bsc",
        action="store_true",
        help=(
            "Allow live BSC buy collection continuation only when matching "
            "environment live flags are enabled."
        ),
    )
    return parser.parse_args()


def _resolve_fund_codes(args: argparse.Namespace) -> list[str] | None:
    codes: list[str] = []

    if args.fund_code:
        codes.extend(_parse_fund_codes(args.fund_code))

    if args.fund_codes:
        codes.extend(_parse_fund_codes(args.fund_codes))

    if not codes:
        codes.extend(
            _parse_fund_codes(settings.SETTLEMENT_BUY_COLLECTION_CONTINUATION_FUND_CODES)
        )

    deduped = sorted(set(codes))
    return deduped or None


def _validate_buy_collection_continuation_live_gate(args: argparse.Namespace) -> bool:
    if bool(args.dry_run):
        return True

    gate = evaluate_live_gate(
        feature="settlement_buy_collection_bsc",
        env_enabled=(
            bool(settings.LIFECYCLE_WORKERS_PRODUCTION_LIVE_ENABLED)
            and bool(settings.SETTLEMENT_ENABLED)
            and bool(settings.SETTLEMENT_BUY_COLLECTION_ALLOW_LIVE_BSC)
        ),
        cli_enabled=bool(args.live_bsc),
    )

    if not gate.allowed:
        log.info(
            "Buy collection continuation live-BSC gate blocked. "
            "No user gas top-up or USDT collection will be sent. gate=%s",
            gate.to_dict(),
        )
        return False

    return True


def run_once(args: argparse.Namespace) -> int:
    fund_codes = _resolve_fund_codes(args)
    limit = max(int(args.limit or 50), 1)

    if not _validate_buy_collection_continuation_live_gate(args):
        return 0

    db = SessionLocal()

    try:
        candidate_ids = scan_active_collecting_buy_usdt_batch_ids(
            db,
            fund_codes=fund_codes,
            limit=limit,
        )

        if not candidate_ids:
            log.info(
                "No active collecting_buy_usdt batches to continue fund_codes=%s dry_run=%s",
                fund_codes or "all_active",
                bool(args.dry_run),
            )
            db.rollback()
            return 0

        log.info(
            "Buy collection continuation candidates fund_codes=%s batch_ids=%s dry_run=%s",
            fund_codes or "all_active",
            candidate_ids,
            bool(args.dry_run),
        )

        result = continue_buy_collection_for_active_batches(
            db,
            fund_codes=fund_codes,
            limit=limit,
            dry_run=bool(args.dry_run),
        )

        for collection_result in result.processed_results:
            log.info(
                "Buy collection continuation result fund=%s batch_id=%s status=%s "
                "buy_orders=%s collected=%s pending=%s failed=%s message=%s",
                collection_result.fund_code,
                collection_result.batch_id,
                collection_result.batch_status,
                collection_result.buy_orders_count,
                collection_result.collected_orders_count,
                collection_result.pending_orders_count,
                collection_result.failed_orders_count,
                collection_result.message,
            )

        if args.dry_run:
            db.rollback()
            log.info("Buy collection continuation dry-run rollback completed.")
        else:
            db.commit()
            log.info(
                "Buy collection continuation DB commit completed processed=%s",
                result.processed_count,
            )

        return 0

    except Exception as exc:
        db.rollback()
        log.exception("Buy collection continuation pass failed: %s", exc)
        return 1

    finally:
        db.close()


def main() -> int:
    load_dotenv()
    args = parse_args()

    if not settings.SETTLEMENT_BUY_COLLECTION_CONTINUATION_ENABLED and not args.run_now:
        log.info(
            "Buy collection continuation worker disabled: "
            "SETTLEMENT_BUY_COLLECTION_CONTINUATION_ENABLED=false. "
            "Use --run-now for approved one-shot checks."
        )
        return 0

    if args.run_now:
        return run_once(args)

    sleep_sec = int(args.sleep_sec or settings.SETTLEMENT_BUY_COLLECTION_CONTINUATION_POLL_SEC)
    sleep_sec = max(sleep_sec, 10)

    log.info(
        "Buy collection continuation worker loop started fund_code=%s fund_codes=%s "
        "sleep_sec=%s dry_run=%s live_bsc=%s",
        args.fund_code or "",
        args.fund_codes or settings.SETTLEMENT_BUY_COLLECTION_CONTINUATION_FUND_CODES,
        sleep_sec,
        bool(args.dry_run),
        bool(args.live_bsc),
    )

    while True:
        rc = run_once(args)
        if rc != 0:
            log.warning("Buy collection continuation pass returned rc=%s", rc)
        time.sleep(sleep_sec)


if __name__ == "__main__":
    raise SystemExit(main())