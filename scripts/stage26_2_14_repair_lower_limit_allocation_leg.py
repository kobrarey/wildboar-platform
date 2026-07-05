from __future__ import annotations

import argparse
from decimal import Decimal

from dotenv import load_dotenv

from app.allocation.live_execution import refresh_live_allocation_batch_progress
from app.allocation.live_spot_orders import (
    repair_live_spot_lower_limit_order_not_found_if_safe,
)
from app.bybit.fund_client import build_fund_bybit_client
from app.db import SessionLocal
from app.models import Fund, FundAllocationBatch, FundAllocationLeg


def dec(value) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    return value if isinstance(value, Decimal) else Decimal(str(value))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Safely repair-forward a live allocation spot leg stuck after deterministic "
            "Bybit lower-limit order-create reject. No POST, no BSC, no secrets printed."
        )
    )
    parser.add_argument("--allocation-leg-id", type=int, required=True)
    parser.add_argument("--fund-code", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser.parse_args()


def _read_context(db, *, allocation_leg_id: int, fund_code: str):
    row = (
        db.query(FundAllocationLeg, FundAllocationBatch, Fund)
        .join(FundAllocationBatch, FundAllocationBatch.id == FundAllocationLeg.allocation_batch_id)
        .join(Fund, Fund.id == FundAllocationLeg.fund_id)
        .filter(FundAllocationLeg.id == int(allocation_leg_id))
        .first()
    )

    if row is None:
        raise RuntimeError(f"Allocation leg not found: {allocation_leg_id}")

    leg, batch, fund = row

    if str(fund.code).lower() != str(fund_code).lower():
        raise RuntimeError(f"fund_code_mismatch: expected={fund_code}, actual={fund.code}")

    return leg, batch, fund


def main() -> int:
    load_dotenv()
    args = parse_args()

    db = SessionLocal()

    try:
        leg, batch, fund = _read_context(
            db,
            allocation_leg_id=int(args.allocation_leg_id),
            fund_code=args.fund_code,
        )

        client_ctx = build_fund_bybit_client(db, fund_id=int(fund.id))
        client = client_ctx.client

        print(f"DRY_RUN={bool(args.dry_run)}")
        print(f"APPLY={bool(args.apply)}")
        print(f"fund_code={fund.code}")
        print(f"allocation_batch_id={batch.id}")
        print(f"allocation_batch_status={batch.status}")
        print(f"allocation_leg_id={leg.id}")
        print(f"leg_status_before={leg.status}")
        print(f"leg_type={leg.leg_type}")
        print(f"symbol={leg.symbol}")
        print(f"target_usdt={leg.target_usdt}")
        print(f"order_link_id_present={bool(leg.order_link_id)}")
        print(f"bybit_order_id_present={bool(leg.bybit_order_id)}")

        result = repair_live_spot_lower_limit_order_not_found_if_safe(
            db,
            allocation_leg_id=int(args.allocation_leg_id),
            client=client,
            fund_code=args.fund_code,
            reason="stage26_2_14_repair_cli",
        )

        progress = refresh_live_allocation_batch_progress(
            db,
            allocation_batch_id=int(result.allocation_batch_id),
        )

        if args.dry_run:
            db.rollback()
            print("ROLLBACK_COMPLETED=True")
        else:
            db.commit()
            print("APPLY_COMMITTED=True")

        print(f"repair_action={result.action}")
        print(f"leg_status_after={result.status}")
        print(f"leg_order_link_id_after={result.order_link_id}")
        print(f"leg_bybit_order_id_after={result.bybit_order_id}")
        print(f"batch_status_after={progress['status']}")
        print(f"batch_active_legs_after={progress['active_legs_count']}")
        print("STAGE26_2_14_LOWER_LIMIT_REPAIR_FORWARD_OK")

        return 0

    except Exception as exc:
        db.rollback()
        print("ROLLBACK_COMPLETED=True")
        print(f"REPAIR_BLOCKED={exc}")
        return 1

    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())