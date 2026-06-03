from __future__ import annotations

import argparse
import logging
import time
from decimal import Decimal
from typing import Any

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from app.allocation.derivative_handlers import handle_derivative_leg_mock
from app.allocation.execution_engine import prepare_execution_for_leg
from app.allocation.residual_service import process_residual_leg_mock
from app.allocation.spot_earn_handlers import handle_spot_earn_leg_mock
from app.allocation.statuses import (
    ALLOCATION_BATCH_STATUS_PLAN_CREATED,
    ALLOCATION_LEG_STATUS_PLANNED,
    DERIVATIVE_SUPPORTED_LEG_TYPES,
    LEG_TYPE_RESIDUAL_USDT_EARN,
    RESIDUAL_SOURCE_STATUSES,
    SPOT_EARN_SUPPORTED_LEG_TYPES,
)
from app.db import SessionLocal
from app.models import Fund, FundAllocationBatch, FundAllocationLeg


log = logging.getLogger(__name__)


SUPPORTED_FUNDS = {
    "btc_fund",
    "defi_sniper",
    "wb10",
    "wb_test",
    "wb_defi",
    "wb_web3",
}


class MockAllocationExecutionClient:
    """
    Stage 22.5 mock market-data client.

    Supports only read-style public_get/get methods required by:
    - get_instrument_info
    - get_last_price
    - get_orderbook

    Any POST is forbidden.
    """

    def __init__(self):
        self.public_get_calls: list[tuple[str, dict[str, Any]]] = []
        self.get_calls: list[tuple[str, dict[str, Any]]] = []
        self.post_calls: list[tuple[str, dict[str, Any]]] = []

    def public_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        self.public_get_calls.append((path, dict(params)))

        if path == "/v5/market/instruments-info":
            return self._instrument_info(params)

        if path == "/v5/market/tickers":
            return self._ticker(params)

        if path == "/v5/market/orderbook":
            return self._orderbook(params)

        raise RuntimeError(f"Unexpected mock public_get path={path} params={params}")

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        self.get_calls.append((path, dict(params)))

        if path == "/v5/account/wallet-balance":
            return self._wallet_balance(params)

        if path == "/v5/earn/product":
            return self._earn_product(params)

        return self.public_get(path, params)

    def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        self.post_calls.append((path, dict(payload)))
        raise RuntimeError(f"POST is forbidden in Stage 22.5 mock execution worker: {path}")

    def _wallet_balance(self, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    {
                        "accountType": params.get("accountType") or "UNIFIED",
                        "totalEquity": "10000",
                        "totalInitialMargin": "1000",
                        "totalMaintenanceMargin": "500",
                        "totalAvailableBalance": "9000",
                    }
                ]
            },
        }

    def _earn_product(self, params: dict[str, Any]) -> dict[str, Any]:
        coin = str(params.get("coin") or "USDT").upper()
        category = str(params.get("category") or "FlexibleSaving")

        if coin == "USDT":
            min_amount = "1"
            max_amount = "100000"
            precision = "2"
        else:
            min_amount = "0.01"
            max_amount = "100000"
            precision = "6"

        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    {
                        "productId": f"{coin}-{category}-001",
                        "coin": coin,
                        "category": category,
                        "status": "Available",
                        "minStakeAmount": min_amount,
                        "maxStakeAmount": max_amount,
                        "precision": precision,
                    }
                ]
            },
        }

    def _instrument_info(self, params: dict[str, Any]) -> dict[str, Any]:
        symbol = str(params["symbol"]).upper()
        category = str(params["category"]).lower()

        status = "Trading"
        min_qty = "0.001"
        qty_step = "0.001"
        min_amt = "5"
        max_market_qty = "10"
        max_limit_qty = "50"
        base_coin = "BTC"
        quote_coin = "USDT"

        if category == "linear":
            min_qty = "0.01"
            qty_step = "0.01"
            min_amt = "0"
            base_coin = "ETH" if symbol.startswith("ETH") else "BTC"

        if category == "inverse":
            min_qty = "1"
            qty_step = "1"
            min_amt = "0"
            base_coin = "BTC"
            quote_coin = "USD"

        if category == "option":
            min_qty = "0.1"
            qty_step = "0.1"
            min_amt = "0"
            base_coin = symbol.split("-")[0] if "-" in symbol else "BTC"
            quote_coin = "USDC"

        if symbol == "TINYUSDT":
            min_amt = "50"

        if symbol == "HALTUSDT":
            status = "PreLaunch"

        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    {
                        "symbol": symbol,
                        "category": category,
                        "status": status,
                        "baseCoin": base_coin,
                        "quoteCoin": quote_coin,
                        "priceFilter": {
                            "tickSize": "0.10",
                        },
                        "lotSizeFilter": {
                            "qtyStep": qty_step,
                            "minOrderQty": min_qty,
                            "maxOrderQty": "100",
                            "minOrderAmt": min_amt,
                            "maxMarketOrderQty": max_market_qty,
                            "maxLimitOrderQty": max_limit_qty,
                        },
                    }
                ]
            },
        }

    def _ticker(self, params: dict[str, Any]) -> dict[str, Any]:
        symbol = str(params["symbol"]).upper()

        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "list": [
                    {
                        "symbol": symbol,
                        "lastPrice": "100",
                    }
                ]
            },
        }

    def _orderbook(self, params: dict[str, Any]) -> dict[str, Any]:
        symbol = str(params["symbol"]).upper()

        # Strong liquidity symbols trigger protected market path.
        strong_symbols = {
            "BTCUSDT",
            "TINYUSDT",
            "HALTUSDT",
            "BTC-31DEC26-100000-C",
        }

        if symbol in strong_symbols:
            asks = [
                ["100.20", "1.00"],
                ["100.80", "2.00"],
                ["101.50", "100.00"],
            ]
            bids = [
                ["99.80", "1.00"],
                ["99.20", "2.00"],
                ["98.50", "100.00"],
            ]
        else:
            asks = [
                ["100.20", "0.20"],
                ["100.80", "0.10"],
                ["101.50", "100.00"],
            ]
            bids = [
                ["99.80", "0.20"],
                ["99.20", "0.10"],
                ["98.50", "100.00"],
            ]

        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "s": symbol,
                "b": bids,
                "a": asks,
            },
        }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stage 22.5 allocation execution worker. "
            "Mocked only: no real Bybit orders, no Strategy orders, no transfers, no Earn stake."
        )
    )

    parser.add_argument(
        "--run-once",
        action="store_true",
        help="Run one execution cycle and exit.",
    )

    parser.add_argument(
        "--fund-code",
        default=None,
        help="Optional fund code filter. Example: wb_test.",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Rollback DB changes after processing legs.",
    )

    parser.add_argument(
        "--mock-market-data",
        action="store_true",
        help="Use built-in mock market-data client. Required in Stage 22.5.",
    )

    parser.add_argument(
        "--live-execution",
        action="store_true",
        help="Reserved for a later approved stage. Blocked in Stage 22.5.",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum planned legs to process in one cycle.",
    )

    parser.add_argument(
        "--sleep-sec",
        type=int,
        default=60,
        help="Loop sleep seconds if --run-once is not used.",
    )

    return parser.parse_args()


def _normalize_fund_code(value: str | None) -> str | None:
    if not value:
        return None

    code = value.strip().lower()
    if not code:
        return None

    if code not in SUPPORTED_FUNDS:
        allowed = ", ".join(sorted(SUPPORTED_FUNDS))
        raise ValueError(f"Unsupported fund code: {code}. Allowed: {allowed}")

    return code


def _validate_stage22_5_args(args: argparse.Namespace) -> None:
    if args.live_execution:
        raise RuntimeError(
            "--live-execution is blocked in Stage 22.5. "
            "Use --mock-market-data for mocked/local checks only."
        )

    if not args.mock_market_data:
        raise RuntimeError(
            "--mock-market-data is required in Stage 22.5. "
            "Real Bybit execution and real market-data execution are blocked."
        )

    if int(args.limit) <= 0:
        raise RuntimeError("--limit must be positive")


def _find_candidate_leg_ids(
    db: Session,
    *,
    fund_code: str | None,
    limit: int,
) -> list[int]:
    q = (
        db.query(FundAllocationLeg.id)
        .join(
            FundAllocationBatch,
            FundAllocationBatch.id == FundAllocationLeg.allocation_batch_id,
        )
        .join(Fund, Fund.id == FundAllocationLeg.fund_id)
        .filter(
            FundAllocationBatch.status == ALLOCATION_BATCH_STATUS_PLAN_CREATED,
            FundAllocationLeg.status == ALLOCATION_LEG_STATUS_PLANNED,
        )
    )

    if fund_code:
        q = q.filter(Fund.code == fund_code)

    rows = (
        q.order_by(
            FundAllocationLeg.allocation_batch_id.asc(),
            FundAllocationLeg.leg_index.asc(),
            FundAllocationLeg.id.asc(),
        )
        .limit(int(limit))
        .all()
    )

    return [int(row[0]) for row in rows]


def _build_client(args: argparse.Namespace) -> MockAllocationExecutionClient:
    if not args.mock_market_data:
        raise RuntimeError("Only mock market-data client is allowed in Stage 22.5")

    return MockAllocationExecutionClient()


def _process_leg_in_own_session(
    *,
    allocation_leg_id: int,
    dry_run: bool,
    args: argparse.Namespace,
) -> bool:
    db = SessionLocal()
    client = _build_client(args)

    try:
        leg_type_row = (
            db.query(FundAllocationLeg.leg_type)
            .filter(FundAllocationLeg.id == allocation_leg_id)
            .first()
        )
        if leg_type_row is None:
            raise RuntimeError(f"Allocation leg not found: {allocation_leg_id}")

        leg_type = str(leg_type_row[0] or "")

        if leg_type in SPOT_EARN_SUPPORTED_LEG_TYPES:
            decision = handle_spot_earn_leg_mock(
                db,
                allocation_leg_id=allocation_leg_id,
                client=client,
            )
        elif leg_type in DERIVATIVE_SUPPORTED_LEG_TYPES:
            decision = handle_derivative_leg_mock(
                db,
                allocation_leg_id=allocation_leg_id,
                client=client,
            )
        else:
            decision = prepare_execution_for_leg(
                db,
                allocation_leg_id=allocation_leg_id,
                client=client,
                mock_mode=True,
            )

        if client.post_calls:
            raise RuntimeError(f"POST calls are forbidden: {client.post_calls}")

        if dry_run:
            db.rollback()
            log.info(
                "Allocation execution dry-run rollback completed "
                "leg_id=%s batch_id=%s action=%s status=%s mode=%s reason=%s",
                decision.allocation_leg_id,
                decision.allocation_batch_id,
                decision.action,
                decision.status,
                decision.execution_mode,
                decision.reason,
            )
        else:
            db.commit()
            log.info(
                "Allocation execution mock decision committed "
                "leg_id=%s batch_id=%s action=%s status=%s mode=%s reason=%s",
                decision.allocation_leg_id,
                decision.allocation_batch_id,
                decision.action,
                decision.status,
                decision.execution_mode,
                decision.reason,
            )

        return True

    except Exception as exc:
        db.rollback()
        log.exception(
            "Allocation execution leg failed leg_id=%s error=%s",
            allocation_leg_id,
            exc,
        )
        return False

    finally:
        db.close()



def _find_residual_candidate_batch_ids(
    db: Session,
    *,
    fund_code: str | None,
    limit: int,
) -> list[int]:
    q = (
        db.query(FundAllocationLeg.allocation_batch_id)
        .join(
            FundAllocationBatch,
            FundAllocationBatch.id == FundAllocationLeg.allocation_batch_id,
        )
        .join(Fund, Fund.id == FundAllocationLeg.fund_id)
        .filter(
            FundAllocationBatch.status == ALLOCATION_BATCH_STATUS_PLAN_CREATED,
            FundAllocationLeg.status.in_(RESIDUAL_SOURCE_STATUSES),
            FundAllocationLeg.residual_usdt.isnot(None),
            FundAllocationLeg.residual_usdt > 0,
            FundAllocationLeg.leg_type != LEG_TYPE_RESIDUAL_USDT_EARN,
        )
        .distinct()
    )

    if fund_code:
        q = q.filter(Fund.code == fund_code)

    rows = (
        q.order_by(FundAllocationLeg.allocation_batch_id.asc())
        .limit(int(limit))
        .all()
    )

    return [int(row[0]) for row in rows]


def _process_residual_batch_in_own_session(
    *,
    allocation_batch_id: int,
    dry_run: bool,
    args: argparse.Namespace,
) -> bool:
    db = SessionLocal()
    client = _build_client(args)

    try:
        decision = process_residual_leg_mock(
            db,
            allocation_batch_id=allocation_batch_id,
            client=client,
        )

        if client.post_calls:
            raise RuntimeError(f"POST calls are forbidden: {client.post_calls}")

        if dry_run:
            db.rollback()
            log.info(
                "Allocation residual dry-run rollback completed "
                "batch_id=%s residual_leg_id=%s action=%s status=%s mode=%s reason=%s",
                decision.allocation_batch_id,
                decision.residual_leg_id,
                decision.action,
                decision.status,
                decision.execution_mode,
                decision.reason,
            )
        else:
            db.commit()
            log.info(
                "Allocation residual mock decision committed "
                "batch_id=%s residual_leg_id=%s action=%s status=%s mode=%s reason=%s",
                decision.allocation_batch_id,
                decision.residual_leg_id,
                decision.action,
                decision.status,
                decision.execution_mode,
                decision.reason,
            )

        return True

    except Exception as exc:
        db.rollback()
        log.exception(
            "Allocation residual processing failed batch_id=%s error=%s",
            allocation_batch_id,
            exc,
        )
        return False

    finally:
        db.close()


def _run_once(args: argparse.Namespace) -> int:
    fund_code = _normalize_fund_code(args.fund_code)

    with SessionLocal() as db:
        leg_ids = _find_candidate_leg_ids(
            db,
            fund_code=fund_code,
            limit=int(args.limit),
        )

    log.info(
        "Allocation execution worker run_once started fund_code=%s dry_run=%s "
        "mock_market_data=%s candidate_legs=%s",
        fund_code or "all",
        bool(args.dry_run),
        bool(args.mock_market_data),
        leg_ids,
    )

    ok_count = 0
    failed_count = 0

    if not leg_ids:
        log.info("No allocation execution candidate legs found.")
    else:
        for leg_id in leg_ids:
            ok = _process_leg_in_own_session(
                allocation_leg_id=leg_id,
                dry_run=bool(args.dry_run),
                args=args,
            )

            if ok:
                ok_count += 1
            else:
                failed_count += 1

    with SessionLocal() as db:
        residual_batch_ids = _find_residual_candidate_batch_ids(
            db,
            fund_code=fund_code,
            limit=int(args.limit),
        )

    if residual_batch_ids:
        log.info(
            "Allocation residual candidate batches found fund_code=%s dry_run=%s batches=%s",
            fund_code or "all",
            bool(args.dry_run),
            residual_batch_ids,
        )
    else:
        log.info("No allocation residual candidate batches found.")

    residual_ok_count = 0
    residual_failed_count = 0

    for batch_id in residual_batch_ids:
        ok = _process_residual_batch_in_own_session(
            allocation_batch_id=batch_id,
            dry_run=bool(args.dry_run),
            args=args,
        )

        if ok:
            residual_ok_count += 1
        else:
            residual_failed_count += 1

    failed_total = failed_count + residual_failed_count

    log.info(
        "Allocation execution worker run_once completed "
        "legs_ok=%s legs_failed=%s legs_total=%s "
        "residual_ok=%s residual_failed=%s residual_total=%s",
        ok_count,
        failed_count,
        len(leg_ids),
        residual_ok_count,
        residual_failed_count,
        len(residual_batch_ids),
    )

    return 0 if failed_total == 0 else 1


def _run_loop(args: argparse.Namespace) -> int:
    sleep_sec = max(int(args.sleep_sec), 10)

    log.info(
        "Allocation execution worker loop started sleep_sec=%s dry_run=%s mock_market_data=%s",
        sleep_sec,
        bool(args.dry_run),
        bool(args.mock_market_data),
    )

    while True:
        code = _run_once(args)
        if code != 0:
            log.warning("Allocation execution worker loop iteration completed with code=%s", code)
        time.sleep(sleep_sec)


def main() -> int:
    load_dotenv()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    args = parse_args()
    _validate_stage22_5_args(args)

    log.info(
        "Stage 22.5 allocation execution worker started. "
        "Mocked only. No real Bybit orders, no Strategy orders, no transfers, no Earn stake."
    )

    if args.run_once:
        return _run_once(args)

    return _run_loop(args)


if __name__ == "__main__":
    raise SystemExit(main())