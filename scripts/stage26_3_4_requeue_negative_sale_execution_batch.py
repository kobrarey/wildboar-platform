from __future__ import annotations

import argparse
from decimal import Decimal
from typing import Any

from dotenv import load_dotenv

from app.db import SessionLocal
from app.models import (
    Fund,
    FundNegativeBybitFlow,
    FundNegativeFinalizationBatch,
    FundNegativePayoutBatch,
    FundNegativeSaleBatch,
    FundOrder,
    FundSettlementBatch,
    UserFundPosition,
)
from app.settlement.statuses import (
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_NEGATIVE_NET_SALE_PLANNED,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
    SALE_BATCH_STATUS_SALE_EXECUTION_FAILED_REQUIRES_REVIEW,
    SALE_BATCH_STATUS_SALE_PLAN_CREATED,
)


EXPECTED_ERROR = "negative_net_sale_execution_final_shortage"


def dec(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def assert_state(name: str, condition: bool, details: str = "") -> None:
    if not condition:
        suffix = f": {details}" if details else ""
        raise RuntimeError(f"{name}{suffix}")
    print(f"{name}: OK")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--settlement-batch-id", type=int, required=True)
    parser.add_argument("--sale-batch-id", type=int, required=True)
    parser.add_argument("--fund-code", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = parse_args()

    db = SessionLocal()

    try:
        fund = db.query(Fund).filter(Fund.code == args.fund_code).first()
        assert_state("REQUEUE_FUND_FOUND", fund is not None)
        assert_state("REQUEUE_FUND_CODE_WB_TEST", str(fund.code) == "wb_test")

        settlement_batch = (
            db.query(FundSettlementBatch)
            .filter(FundSettlementBatch.id == int(args.settlement_batch_id))
            .with_for_update()
            .first()
        )
        assert_state("REQUEUE_SETTLEMENT_BATCH_FOUND", settlement_batch is not None)
        assert_state(
            "REQUEUE_SETTLEMENT_BATCH_FUND_MATCH",
            int(settlement_batch.fund_id) == int(fund.id),
        )
        assert_state(
            "REQUEUE_SETTLEMENT_BATCH_STATUS_FAILED",
            settlement_batch.status == BATCH_STATUS_FAILED_REQUIRES_REVIEW,
            str(settlement_batch.status),
        )
        assert_state(
            "REQUEUE_SETTLEMENT_BATCH_ERROR_MATCH",
            settlement_batch.error == EXPECTED_ERROR,
            str(settlement_batch.error),
        )

        sale_batch = (
            db.query(FundNegativeSaleBatch)
            .filter(FundNegativeSaleBatch.id == int(args.sale_batch_id))
            .with_for_update()
            .first()
        )
        assert_state("REQUEUE_SALE_BATCH_FOUND", sale_batch is not None)
        assert_state(
            "REQUEUE_SALE_BATCH_SETTLEMENT_MATCH",
            int(sale_batch.settlement_batch_id) == int(settlement_batch.id),
        )
        assert_state(
            "REQUEUE_SALE_BATCH_STATUS_FAILED",
            sale_batch.status == SALE_BATCH_STATUS_SALE_EXECUTION_FAILED_REQUIRES_REVIEW,
            str(sale_batch.status),
        )
        assert_state(
            "REQUEUE_SALE_BATCH_ERROR_MATCH",
            sale_batch.error == EXPECTED_ERROR,
            str(sale_batch.error),
        )

        order = (
            db.query(FundOrder)
            .filter(
                FundOrder.settlement_batch_id == int(settlement_batch.id),
                FundOrder.side == ORDER_SIDE_REDEEM,
                FundOrder.status == ORDER_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
            )
            .first()
        )
        assert_state("REQUEUE_REDEEM_ORDER_FOUND", order is not None)
        assert_state("REQUEUE_REDEEM_ORDER_ID_46_SHAPE", int(order.id) == 46)
        assert_state("REQUEUE_REDEEM_ORDER_SHARES_OK", dec(order.shares) == Decimal("0.0160000000"))

        position = (
            db.query(UserFundPosition)
            .filter(
                UserFundPosition.user_id == int(order.user_id),
                UserFundPosition.fund_id == int(fund.id),
            )
            .first()
        )
        assert_state("REQUEUE_POSITION_FOUND", position is not None)
        assert_state(
            "REQUEUE_POSITION_RESERVED_OK",
            dec(position.shares_reserved) == Decimal("0.0160000000"),
            str(position.shares_reserved),
        )

        bybit_flow_count = (
            db.query(FundNegativeBybitFlow)
            .filter(FundNegativeBybitFlow.settlement_batch_id == int(settlement_batch.id))
            .count()
        )
        payout_count = (
            db.query(FundNegativePayoutBatch)
            .filter(FundNegativePayoutBatch.settlement_batch_id == int(settlement_batch.id))
            .count()
        )
        finalization_count = (
            db.query(FundNegativeFinalizationBatch)
            .filter(FundNegativeFinalizationBatch.settlement_batch_id == int(settlement_batch.id))
            .count()
        )

        assert_state("REQUEUE_NO_BYBIT_FLOW_EXISTS", bybit_flow_count == 0)
        assert_state("REQUEUE_NO_PAYOUT_BATCH_EXISTS", payout_count == 0)
        assert_state("REQUEUE_NO_FINALIZATION_BATCH_EXISTS", finalization_count == 0)

        print(
            {
                "dry_run": bool(args.dry_run),
                "apply": bool(args.apply),
                "would_set_settlement_status": BATCH_STATUS_NEGATIVE_NET_SALE_PLANNED,
                "would_set_sale_batch_status": SALE_BATCH_STATUS_SALE_PLAN_CREATED,
                "preserve_order_id": int(order.id),
                "preserve_required_master_usdt": str(sale_batch.required_master_usdt),
                "no_bybit_action": True,
                "no_bsc_tx": True,
            }
        )

        settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_SALE_PLANNED
        settlement_batch.error = None

        sale_batch.status = SALE_BATCH_STATUS_SALE_PLAN_CREATED
        sale_batch.error = None

        for field in [
            "execution_started_at",
            "execution_completed_at",
            "available_usdt_before_execution",
            "initial_cash_like_usdt",
            "usdt_earn_redeemed_usdt",
            "initial_sale_executed_usdt",
            "available_usdt_after_initial_sales",
            "shortage_after_initial_sales_usdt",
            "extra_sale_required_usdt",
            "extra_sale_target_usdt",
            "extra_sale_executed_usdt",
            "final_available_usdt",
            "final_shortage_usdt",
            "final_surplus_usdt",
            "execution_json",
            "reconciliation_json",
        ]:
            setattr(sale_batch, field, None)

        db.add(settlement_batch)
        db.add(sale_batch)
        db.flush()

        if args.dry_run:
            db.rollback()
            print("STAGE26_3_4_REQUEUE_BATCH80_DRY_RUN_OK")
        else:
            db.commit()
            print("STAGE26_3_4_REQUEUE_BATCH80_APPLY_OK")

        return 0

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
