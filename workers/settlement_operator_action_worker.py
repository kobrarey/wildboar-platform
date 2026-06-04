from __future__ import annotations

import argparse
import logging
import time
from decimal import Decimal

from app.db import SessionLocal
from app.settlement.operator_gas_retry import (
    process_pending_retry_settlement_gas_topup_actions_mock,
)


log = logging.getLogger(__name__)

STAGE_NAME = "Stage 22.7"


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Stage 22.7 settlement operator action worker. "
            "Mock/local only. Processes DB operator actions for retrying "
            "settlement gas top-up after Telegram operator confirmation. "
            "No shell execution, no real Telegram calls, no real BSC transfer."
        )
    )

    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Run one polling cycle and exit.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Rollback worker changes after mock processing.",
    )
    parser.add_argument(
        "--mock-ok-gas-balance-bnb",
        type=str,
        default=None,
        help=(
            "Required in Stage 22.7. Mock OK gas wallet BNB balance used "
            "to decide whether the retry can proceed."
        ),
    )
    parser.add_argument(
        "--live-bsc",
        action="store_true",
        help="Blocked in Stage 22.7. Present only as a hard-fail guard.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum pending operator actions per cycle.",
    )
    parser.add_argument(
        "--sleep-sec",
        type=int,
        default=60,
        help="Sleep interval for loop mode.",
    )

    return parser


def _parse_decimal(value: str | None, *, name: str) -> Decimal:
    if value is None or str(value).strip() == "":
        raise RuntimeError(f"{name} is required")

    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise RuntimeError(f"{name} must be a valid Decimal value: {value}") from exc

    if result < Decimal("0"):
        raise RuntimeError(f"{name} must be non-negative")

    return result


def _validate_stage22_7_args(args: argparse.Namespace) -> Decimal:
    if args.live_bsc:
        raise RuntimeError(
            "--live-bsc is blocked in Stage 22.7. "
            "The worker is mock/local only and must not execute real BSC transfers."
        )

    mock_balance = _parse_decimal(
        args.mock_ok_gas_balance_bnb,
        name="--mock-ok-gas-balance-bnb",
    )

    if int(args.limit) <= 0:
        raise RuntimeError("--limit must be positive")

    if int(args.sleep_sec) <= 0:
        raise RuntimeError("--sleep-sec must be positive")

    return mock_balance


def _run_once(
    args: argparse.Namespace,
    *,
    mock_ok_gas_balance_bnb: Decimal,
) -> int:
    db = SessionLocal()

    try:
        result = process_pending_retry_settlement_gas_topup_actions_mock(
            db,
            mock_ok_gas_balance_bnb=mock_ok_gas_balance_bnb,
            limit=int(args.limit),
        )

        if args.dry_run:
            db.rollback()
            log.info(
                "Settlement operator action worker dry-run rollback completed "
                "ok=%s failed=%s total=%s decisions=%s",
                result.ok_count,
                result.failed_count,
                result.total_count,
                result.to_dict(),
            )
        else:
            db.commit()
            log.info(
                "Settlement operator action worker mock decisions committed "
                "ok=%s failed=%s total=%s decisions=%s",
                result.ok_count,
                result.failed_count,
                result.total_count,
                result.to_dict(),
            )

        return 0 if result.failed_count == 0 else 1

    except Exception as exc:
        db.rollback()
        log.exception(
            "Settlement operator action worker cycle failed error=%s",
            exc,
        )
        return 1

    finally:
        db.close()


def main() -> int:
    _setup_logging()

    parser = _build_parser()
    args = parser.parse_args()

    mock_ok_gas_balance_bnb = _validate_stage22_7_args(args)

    log.info(
        "%s settlement operator action worker started. "
        "Mock/local only. No shell execution, no real Telegram calls, "
        "no real BSC calls, no real BNB transfer, no server deploy.",
        STAGE_NAME,
    )

    if args.run_once:
        return _run_once(
            args,
            mock_ok_gas_balance_bnb=mock_ok_gas_balance_bnb,
        )

    while True:
        code = _run_once(
            args,
            mock_ok_gas_balance_bnb=mock_ok_gas_balance_bnb,
        )
        if code != 0:
            log.warning(
                "Settlement operator action worker cycle completed with failures code=%s",
                code,
            )

        time.sleep(int(args.sleep_sec))


if __name__ == "__main__":
    raise SystemExit(main())
