from __future__ import annotations

import argparse
import logging
import time
from decimal import Decimal
from typing import Any

from dotenv import load_dotenv
from sqlalchemy.orm import Session

from app.config import settings
from app.lifecycle import evaluate_live_gate
from app.allocation.derivative_handlers import handle_derivative_leg_mock
from app.allocation.bybit_orders import BybitOrderPayloadError
from app.allocation.execution_engine import prepare_execution_for_leg
from app.allocation.instrument_info import InstrumentInfoError
from app.allocation.liquidity import LiquidityError
from app.allocation.residual_service import process_residual_leg_mock
from app.allocation.spot_earn_handlers import handle_spot_earn_leg_mock
from app.allocation.statuses import (
    ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED_WITH_RESIDUAL_CASH,
    ALLOCATION_BATCH_STATUS_ALLOCATION_PROCESSING,
    ALLOCATION_BATCH_STATUS_PLAN_CREATED,
    ALLOCATION_LEG_STATUS_PLANNED,
    DERIVATIVE_SUPPORTED_LEG_TYPES,
    LEG_TYPE_BUY_THEN_STAKE,
    LEG_TYPE_RESIDUAL_USDT_EARN,
    LEG_TYPE_SPOT_BUY,
    LEG_TYPE_STABLE_CASH,
    LEG_TYPE_USDT_EARN_STAKE,
    RESIDUAL_SOURCE_STATUSES,
    SPOT_EARN_SUPPORTED_LEG_TYPES,
)
from app.allocation.live_execution import (
    mark_allocation_batch_failed_requires_review,
    mark_leg_residual_cash_without_external_call,
    mark_policy_skipped_leg_without_external_call,
    mark_stable_cash_leg_filled_without_external_call,
    preflight_live_allocation_batch,
    refresh_live_allocation_batch_progress,
)
from app.allocation.live_earn_config import (
    allocation_earn_live_enabled,
    residual_earn_to_cash_when_live_disabled,
)
from app.allocation.live_policy import (
    DERIVATIVE_OPTION_SKIP_REASON,
    BUY_THEN_STAKE_SPOT_ONLY_REASON,
    classify_live_leg_policy,
)
from app.allocation.live_earn_orders import (
    build_live_earn_stake_order_plan,
    mark_live_earn_order_create_failed,
    prepare_live_earn_stake_order_or_terminal_skip,
    reconcile_live_earn_stake_leg_by_link_id,
    require_earn_guard_for_plan,
    submit_bybit_earn_stake_order,
)
from app.allocation.live_spot_orders import (
    BybitOrderCreateLowerLimitReject,
    LiveSpotOrderError,
    build_live_spot_market_order_plan,
    mark_live_spot_order_create_failed,
    mark_live_spot_order_lower_limit_rejected_as_terminal_skip,
    prepare_live_spot_market_order_or_terminal_skip,
    reconcile_live_spot_market_leg_by_link_id,
    require_trade_guard_for_plan,
    submit_bybit_spot_market_order,
)
from app.bybit.fund_client import build_fund_bybit_client
from app.db import SessionLocal
from app.models import Fund, FundAllocationBatch, FundAllocationLeg


log = logging.getLogger(__name__)


SPOT_PLAN_DETERMINISTIC_FAILURE_TYPES = (
    LiveSpotOrderError,
    InstrumentInfoError,
    LiquidityError,
    BybitOrderPayloadError,
)


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
    Mock market-data client for safe execution checks.

    Supports only read-style public_get/get methods required by:
    - get_instrument_info
    - get_last_price
    - get_orderbook

    Any POST is not allowed in mock mode.
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
        raise RuntimeError(f"POST is not allowed in mock execution worker: {path}")

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
            "Stage 25 allocation execution worker. "
            "Mock mode by default; guarded live mode runs safe preflight "
            "and delegates supported legs to live adapters."
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
        help="Use built-in mock market-data client for safe execution checks.",
    )

    parser.add_argument(
        "--live-execution",
        action="store_true",
        help="Live execution is safe-gated by env + CLI flags; no external action is sent when the gate is disabled.",
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


def _validate_stage22_5_args(args: argparse.Namespace) -> bool:
    if int(args.limit) <= 0:
        raise RuntimeError("--limit must be positive")

    if int(args.sleep_sec) <= 0:
        raise RuntimeError("--sleep-sec must be positive")

    if args.live_execution:
        gate = evaluate_live_gate(
            feature="allocation_execution",
            env_enabled=(
                bool(settings.LIFECYCLE_WORKERS_PRODUCTION_LIVE_ENABLED)
                and bool(settings.ALLOCATION_EXECUTION_ENABLED)
                and bool(settings.ALLOCATION_EXECUTION_ALLOW_LIVE)
            ),
            cli_enabled=True,
        )
        if not gate.allowed:
            log.info(
                "Allocation execution live gate blocked. No changes. gate=%s",
                gate.to_dict(),
            )
            return False

        return True

    if not args.mock_market_data:
        raise RuntimeError(
            "--mock-market-data is required when --live-execution is not used."
        )

    return True


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
            FundAllocationBatch.status.in_(
                [
                    ALLOCATION_BATCH_STATUS_PLAN_CREATED,
                    ALLOCATION_BATCH_STATUS_ALLOCATION_PROCESSING,
                ]
            ),
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


def _get_batch_id_for_leg(
    db: Session,
    *,
    allocation_leg_id: int,
) -> int:
    row = (
        db.query(FundAllocationLeg.allocation_batch_id)
        .filter(FundAllocationLeg.id == int(allocation_leg_id))
        .first()
    )

    if row is None:
        raise RuntimeError(f"Allocation leg not found: {allocation_leg_id}")

    return int(row[0])


def time_now_utc():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _complete_residual_cash_only_batch(
    db: Session,
    *,
    allocation_batch_id: int,
    residual_leg_ids: list[int],
) -> None:
    batch = (
        db.query(FundAllocationBatch)
        .filter(FundAllocationBatch.id == int(allocation_batch_id))
        .with_for_update()
        .first()
    )

    if batch is None:
        raise RuntimeError(f"Allocation batch not found: {allocation_batch_id}")

    for leg_id in residual_leg_ids:
        leg = (
            db.query(FundAllocationLeg)
            .filter(FundAllocationLeg.id == int(leg_id))
            .first()
        )
        if leg is None:
            raise RuntimeError(f"Allocation leg not found: {leg_id}")

        policy_decision = classify_live_leg_policy(leg)

        if policy_decision.policy_skipped:
            mark_policy_skipped_leg_without_external_call(
                db,
                allocation_leg_id=int(leg_id),
                reason=policy_decision.reason or DERIVATIVE_OPTION_SKIP_REASON,
            )
        else:
            mark_leg_residual_cash_without_external_call(
                db,
                allocation_leg_id=int(leg_id),
                reason="live_earn_disabled_residual_kept_as_cash",
            )

    batch.status = ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED_WITH_RESIDUAL_CASH
    batch.residual_cash_usdt = sum(
        (
            leg.residual_usdt or Decimal("0")
            for leg in db.query(FundAllocationLeg)
            .filter(FundAllocationLeg.allocation_batch_id == int(allocation_batch_id))
            .all()
        ),
        Decimal("0"),
    )
    batch.error = None
    batch.completed_at = batch.completed_at or time_now_utc()
    batch.updated_at = time_now_utc()

    db.add(batch)
    db.flush()


def _refresh_live_batch_progress_in_own_session(
    *,
    allocation_batch_id: int,
    dry_run: bool,
) -> bool:
    db = SessionLocal()

    try:
        progress = refresh_live_allocation_batch_progress(
            db,
            allocation_batch_id=int(allocation_batch_id),
        )

        if dry_run:
            db.rollback()
        else:
            db.commit()

        log.info(
            "Allocation live batch progress refreshed "
            "batch_id=%s status=%s filled=%s partial=%s skipped=%s failed=%s active=%s",
            allocation_batch_id,
            progress["status"],
            progress["filled_legs_count"],
            progress["partial_legs_count"],
            progress["skipped_legs_count"],
            progress["failed_legs_count"],
            progress["active_legs_count"],
        )

        return True

    except Exception as exc:
        db.rollback()
        log.exception(
            "Allocation live batch progress refresh failed batch_id=%s error=%s",
            allocation_batch_id,
            exc,
        )
        return False

    finally:
        db.close()


def _process_live_batch_preflight_in_own_session(
    *,
    allocation_batch_id: int,
    dry_run: bool,
    fund_code: str | None,
) -> bool:
    db = SessionLocal()

    try:
        preflight = preflight_live_allocation_batch(
            db,
            allocation_batch_id=int(allocation_batch_id),
            fund_code=fund_code,
        )

        if not preflight.ok:
            mark_allocation_batch_failed_requires_review(
                db,
                allocation_batch_id=int(allocation_batch_id),
                error="unsupported_live_allocation_batch_preflight",
                diagnostics=preflight.to_dict(),
            )

            if dry_run:
                db.rollback()
            else:
                db.commit()

            log.error(
                "Allocation live preflight blocked batch before external calls "
                "batch_id=%s issues=%s external_calls=0",
                allocation_batch_id,
                [item.reason for item in preflight.issues],
            )

            return False

        # Stage 25.2B: Earn/residual live adapter is intentionally not used.
        # Residual/Earn legs are safely kept as cash without external calls.
        if preflight.residual_cash_leg_ids and not preflight.supported_leg_ids:
            _complete_residual_cash_only_batch(
                db,
                allocation_batch_id=int(allocation_batch_id),
                residual_leg_ids=preflight.residual_cash_leg_ids,
            )

            if dry_run:
                db.rollback()
            else:
                db.commit()

            log.info(
                "Allocation live preflight completed residual-cash-only batch "
                "batch_id=%s residual_legs=%s external_calls=0",
                allocation_batch_id,
                preflight.residual_cash_leg_ids,
            )

            return True

        batch = (
            db.query(FundAllocationBatch)
            .filter(FundAllocationBatch.id == int(allocation_batch_id))
            .with_for_update()
            .first()
        )

        if batch is None:
            raise RuntimeError(f"Allocation batch not found: {allocation_batch_id}")

        batch.status = ALLOCATION_BATCH_STATUS_ALLOCATION_PROCESSING
        batch.report_json = {
            "stage25_2_live_preflight": {
                "ok": True,
                "supported_leg_ids": preflight.supported_leg_ids,
                "residual_cash_leg_ids": preflight.residual_cash_leg_ids,
                "external_calls": 0,
                "next": "spot_live_adapter",
            }
        }
        batch.updated_at = time_now_utc()

        db.add(batch)

        if dry_run:
            db.rollback()
        else:
            db.commit()

        log.info(
            "Allocation live preflight passed batch_id=%s supported_legs=%s "
            "residual_cash_legs=%s external_calls=0",
            allocation_batch_id,
            preflight.supported_leg_ids,
            preflight.residual_cash_leg_ids,
        )

        return True

    except Exception as exc:
        db.rollback()
        log.exception(
            "Allocation live preflight failed batch_id=%s error=%s",
            allocation_batch_id,
            exc,
        )
        return False

    finally:
        db.close()


def _get_live_leg_snapshot(
    db: Session,
    *,
    allocation_leg_id: int,
) -> tuple[int, str, str | None, str | None]:
    row = (
        db.query(
            FundAllocationLeg.fund_id,
            FundAllocationLeg.leg_type,
            FundAllocationLeg.order_link_id,
            FundAllocationLeg.bybit_order_id,
        )
        .filter(FundAllocationLeg.id == int(allocation_leg_id))
        .first()
    )

    if row is None:
        raise RuntimeError(f"Allocation leg not found: {allocation_leg_id}")

    return int(row[0]), str(row[1] or ""), row[2], row[3]


def _mark_stable_cash_leg_in_own_session(
    *,
    allocation_leg_id: int,
    dry_run: bool,
) -> bool:
    db = SessionLocal()

    try:
        leg = mark_stable_cash_leg_filled_without_external_call(
            db,
            allocation_leg_id=int(allocation_leg_id),
        )

        if dry_run:
            db.rollback()
        else:
            db.commit()

        allocation_batch_id = int(leg.allocation_batch_id)

        log.info(
            "Allocation live stable_cash leg completed without external calls "
            "leg_id=%s batch_id=%s external_calls=0",
            leg.id,
            allocation_batch_id,
        )

        _refresh_live_batch_progress_in_own_session(
            allocation_batch_id=allocation_batch_id,
            dry_run=dry_run,
        )

        return True

    except Exception as exc:
        db.rollback()
        log.exception(
            "Allocation live stable_cash leg failed leg_id=%s error=%s",
            allocation_leg_id,
            exc,
        )
        return False

    finally:
        db.close()


def _mark_policy_skipped_leg_in_own_session(
    *,
    allocation_leg_id: int,
    dry_run: bool,
    reason: str,
) -> bool:
    db = SessionLocal()

    try:
        leg = mark_policy_skipped_leg_without_external_call(
            db,
            allocation_leg_id=int(allocation_leg_id),
            reason=reason,
        )
        allocation_batch_id = int(leg.allocation_batch_id)

        if dry_run:
            db.rollback()
        else:
            db.commit()

        log.info(
            "Allocation live policy-skipped leg completed without external calls "
            "leg_id=%s batch_id=%s leg_type=%s reason=%s external_calls=0",
            allocation_leg_id,
            allocation_batch_id,
            leg.leg_type,
            reason,
        )

        _refresh_live_batch_progress_in_own_session(
            allocation_batch_id=allocation_batch_id,
            dry_run=dry_run,
        )

        return True

    except Exception as exc:
        db.rollback()
        log.exception(
            "Allocation live policy skip failed leg_id=%s reason=%s error=%s",
            allocation_leg_id,
            reason,
            exc,
        )
        return False

    finally:
        db.close()


def _mark_residual_earn_cash_in_own_session(
    *,
    allocation_leg_id: int,
    dry_run: bool,
) -> bool:
    db = SessionLocal()

    try:
        leg = mark_leg_residual_cash_without_external_call(
            db,
            allocation_leg_id=int(allocation_leg_id),
            reason="residual_earn_kept_as_cash_because_live_earn_disabled",
        )
        allocation_batch_id = int(leg.allocation_batch_id)

        if dry_run:
            db.rollback()
        else:
            db.commit()

        log.info(
            "Allocation live residual Earn kept as cash without external calls "
            "leg_id=%s batch_id=%s external_calls=0",
            allocation_leg_id,
            allocation_batch_id,
        )

        _refresh_live_batch_progress_in_own_session(
            allocation_batch_id=allocation_batch_id,
            dry_run=dry_run,
        )

        return True

    except Exception as exc:
        db.rollback()
        log.exception(
            "Allocation live residual Earn cash fallback failed "
            "leg_id=%s error=%s",
            allocation_leg_id,
            exc,
        )
        return False

    finally:
        db.close()


def _process_live_earn_leg_in_own_session(
    *,
    allocation_leg_id: int,
    dry_run: bool,
    fund_code: str | None,
) -> bool:
    # Phase 0: batch-level preflight before any external Earn action.
    db0 = SessionLocal()
    try:
        allocation_batch_id = _get_batch_id_for_leg(
            db0,
            allocation_leg_id=int(allocation_leg_id),
        )
    finally:
        db0.close()

    preflight_ok = _process_live_batch_preflight_in_own_session(
        allocation_batch_id=int(allocation_batch_id),
        dry_run=dry_run,
        fund_code=fund_code,
    )

    if not preflight_ok:
        return False

    # Phase 1: identify leg + fund client. This only decrypts local credentials,
    # it does not perform an external Bybit call.
    db1 = SessionLocal()
    try:
        fund_id, leg_type, order_link_id, bybit_order_id = _get_live_leg_snapshot(
            db1,
            allocation_leg_id=int(allocation_leg_id),
        )

        if leg_type not in {LEG_TYPE_USDT_EARN_STAKE, LEG_TYPE_RESIDUAL_USDT_EARN}:
            log.error(
                "Allocation live Earn adapter blocked unsupported leg "
                "leg_id=%s leg_type=%s external_calls=0",
                allocation_leg_id,
                leg_type,
            )
            return False

        if (
            leg_type == LEG_TYPE_RESIDUAL_USDT_EARN
            and not allocation_earn_live_enabled()
            and residual_earn_to_cash_when_live_disabled()
        ):
            db1.close()
            return _mark_residual_earn_cash_in_own_session(
                allocation_leg_id=int(allocation_leg_id),
                dry_run=dry_run,
            )

        if not allocation_earn_live_enabled():
            log.error(
                "Allocation live Earn blocked because Earn live flags are disabled "
                "leg_id=%s leg_type=%s external_calls=0",
                allocation_leg_id,
                leg_type,
            )
            return False

        fund_client_ctx = build_fund_bybit_client(
            db1,
            fund_id=int(fund_id),
        )
        client = fund_client_ctx.client

    except Exception as exc:
        db1.rollback()
        log.exception(
            "Allocation live Earn setup failed leg_id=%s error=%s",
            allocation_leg_id,
            exc,
        )
        return False

    finally:
        if db1.is_active:
            db1.close()

    # Phase 2: idempotency-first reconciliation.
    # If earn_order_id/orderLinkId/orderId already exists, never send a duplicate order.
    if order_link_id or bybit_order_id:
        db2 = SessionLocal()
        try:
            result = reconcile_live_earn_stake_leg_by_link_id(
                db2,
                allocation_leg_id=int(allocation_leg_id),
                client=client,
            )

            if dry_run:
                db2.rollback()
            else:
                db2.commit()

            _refresh_live_batch_progress_in_own_session(
                allocation_batch_id=int(result.allocation_batch_id),
                dry_run=dry_run,
            )

            log.info(
                "Allocation live Earn idempotent reconciliation completed "
                "leg_id=%s action=%s status=%s order_link_id=%s bybit_order_id=%s",
                allocation_leg_id,
                result.action,
                result.status,
                result.order_link_id,
                result.bybit_order_id,
            )

            return bool(result.ok)

        except Exception as exc:
            db2.rollback()
            log.exception(
                "Allocation live Earn idempotent reconciliation failed "
                "leg_id=%s error=%s",
                allocation_leg_id,
                exc,
            )
            return False

        finally:
            db2.close()

    # Phase 3: build plan or terminalize safe Earn validation skip before POST.
    db3 = SessionLocal()
    try:
        plan_or_skip = prepare_live_earn_stake_order_or_terminal_skip(
            db3,
            allocation_leg_id=int(allocation_leg_id),
            client=client,
            default_category=settings.ALLOCATION_USDT_EARN_CATEGORY,
        )

        if getattr(plan_or_skip, "action", "") == "terminal_earn_validation_skip":
            allocation_batch_id = int(plan_or_skip.allocation_batch_id)

            if dry_run:
                db3.rollback()
            else:
                db3.commit()

            log.info(
                "Allocation live Earn validation terminal skip "
                "leg_id=%s status=%s reason=%s external_post_calls=0 guard_required=0",
                allocation_leg_id,
                plan_or_skip.status,
                plan_or_skip.reason,
            )

            _refresh_live_batch_progress_in_own_session(
                allocation_batch_id=allocation_batch_id,
                dry_run=dry_run,
            )
            return True

        plan = plan_or_skip

        if dry_run:
            db3.rollback()
            log.info(
                "Allocation live Earn dry-run stopped after order plan "
                "leg_id=%s earn_order_id=%s order_link_id=%s external_post_calls=0",
                allocation_leg_id,
                plan.earn_order_id,
                plan.order_link_id,
            )
            return True

        db3.commit()

        log.info(
            "Allocation live Earn earn_order_id/orderLinkId persisted before POST "
            "leg_id=%s earn_order_id=%s order_link_id=%s external_post_calls=0",
            allocation_leg_id,
            plan.earn_order_id,
            plan.order_link_id,
        )

    except Exception as exc:
        db3.rollback()

        db_plan_fail = SessionLocal()
        try:
            mark_live_earn_order_create_failed(
                db_plan_fail,
                allocation_leg_id=int(allocation_leg_id),
                error=f"earn_order_plan_failed: {exc}",
            )
            db_plan_fail.commit()
        except Exception:
            db_plan_fail.rollback()
        finally:
            db_plan_fail.close()

        _refresh_live_batch_progress_in_own_session(
            allocation_batch_id=int(allocation_batch_id),
            dry_run=dry_run,
        )

        log.exception(
            "Allocation live Earn plan failed leg_id=%s error=%s",
            allocation_leg_id,
            exc,
        )
        return False

    finally:
        db3.close()

    # Phase 4: Operation Guard must pass before external POST.
    db4 = SessionLocal()
    try:
        guard_decision = require_earn_guard_for_plan(
            db4,
            plan=plan,
        )
        db4.commit()

        log.info(
            "Allocation live Earn Operation Guard allowed "
            "leg_id=%s earn_order_id=%s order_link_id=%s guard=%s",
            allocation_leg_id,
            plan.earn_order_id,
            plan.order_link_id,
            guard_decision,
        )

    except Exception as exc:
        db4.rollback()

        db_guard_fail = SessionLocal()
        try:
            mark_live_earn_order_create_failed(
                db_guard_fail,
                allocation_leg_id=int(allocation_leg_id),
                error=f"earn_order_guard_blocked_or_error: {exc}",
            )
            db_guard_fail.commit()
        except Exception:
            db_guard_fail.rollback()
        finally:
            db_guard_fail.close()

        _refresh_live_batch_progress_in_own_session(
            allocation_batch_id=int(plan.allocation_batch_id),
            dry_run=dry_run,
        )

        log.exception(
            "Allocation live Earn Operation Guard blocked "
            "leg_id=%s earn_order_id=%s order_link_id=%s error=%s",
            allocation_leg_id,
            plan.earn_order_id,
            plan.order_link_id,
            exc,
        )
        return False

    finally:
        db4.close()

    # Phase 5: single external POST. If this errors, mark review and do not retry blindly.
    try:
        create_result = submit_bybit_earn_stake_order(
            client,
            payload=plan.payload,
        )
    except Exception as exc:
        db_fail = SessionLocal()
        try:
            mark_live_earn_order_create_failed(
                db_fail,
                allocation_leg_id=int(allocation_leg_id),
                error=f"earn_order_create_failed_or_uncertain: {exc}",
            )
            db_fail.commit()
        except Exception:
            db_fail.rollback()
        finally:
            db_fail.close()

        _refresh_live_batch_progress_in_own_session(
            allocation_batch_id=int(plan.allocation_batch_id),
            dry_run=dry_run,
        )

        log.exception(
            "Allocation live Earn order POST failed or uncertain "
            "leg_id=%s earn_order_id=%s order_link_id=%s",
            allocation_leg_id,
            plan.earn_order_id,
            plan.order_link_id,
        )
        return False

    # Phase 6: store returned external order id if available, then reconcile by orderLinkId.
    db5 = SessionLocal()
    try:
        leg = (
            db5.query(FundAllocationLeg)
            .filter(FundAllocationLeg.id == int(allocation_leg_id))
            .with_for_update()
            .first()
        )

        if leg is None:
            raise RuntimeError(f"Allocation leg not found: {allocation_leg_id}")

        order_id = (
            create_result.get("orderId")
            or create_result.get("order_id")
            or create_result.get("earnOrderId")
            or create_result.get("earn_order_id")
        )

        if order_id:
            leg.bybit_order_id = str(order_id)

        leg.error = None
        db5.add(leg)
        db5.commit()

    except Exception as exc:
        db5.rollback()
        log.exception(
            "Allocation live Earn orderId persist failed "
            "leg_id=%s earn_order_id=%s order_link_id=%s error=%s",
            allocation_leg_id,
            plan.earn_order_id,
            plan.order_link_id,
            exc,
        )
        return False

    finally:
        db5.close()

    db6 = SessionLocal()
    try:
        result = reconcile_live_earn_stake_leg_by_link_id(
            db6,
            allocation_leg_id=int(allocation_leg_id),
            client=client,
        )

        if dry_run:
            db6.rollback()
        else:
            db6.commit()

        _refresh_live_batch_progress_in_own_session(
            allocation_batch_id=int(result.allocation_batch_id),
            dry_run=dry_run,
        )

        log.info(
            "Allocation live Earn POST/reconciliation completed "
            "leg_id=%s action=%s status=%s earn_order_id=%s order_link_id=%s bybit_order_id=%s",
            allocation_leg_id,
            result.action,
            result.status,
            result.earn_order_id,
            result.order_link_id,
            result.bybit_order_id,
        )

        return bool(result.ok)

    except Exception as exc:
        db6.rollback()
        log.exception(
            "Allocation live Earn post-send reconciliation failed "
            "leg_id=%s error=%s",
            allocation_leg_id,
            exc,
        )
        return False

    finally:
        db6.close()


def _mark_live_spot_plan_failure_requires_review_in_own_session(
    *,
    allocation_leg_id: int,
    dry_run: bool,
    error: str,
) -> bool:
    db = SessionLocal()

    try:
        result = mark_live_spot_order_create_failed(
            db,
            allocation_leg_id=int(allocation_leg_id),
            error=error,
        )
        allocation_batch_id = int(result.allocation_batch_id)

        if dry_run:
            db.rollback()
        else:
            db.commit()

        log.error(
            "Allocation live spot deterministic plan failure marked requires_review "
            "leg_id=%s status=%s error=%s external_post_calls=0 guard_required=0",
            allocation_leg_id,
            result.status,
            error,
        )

        _refresh_live_batch_progress_in_own_session(
            allocation_batch_id=allocation_batch_id,
            dry_run=dry_run,
        )

        return False

    except Exception as exc:
        db.rollback()
        log.exception(
            "Allocation live spot deterministic plan failure marking failed "
            "leg_id=%s original_error=%s mark_error=%s",
            allocation_leg_id,
            error,
            exc,
        )
        return False

    finally:
        db.close()


def _mark_live_spot_lower_limit_skip_in_own_session(
    *,
    allocation_leg_id: int,
    dry_run: bool,
    error: str,
    diagnostics: dict[str, Any] | None = None,
) -> bool:
    db = SessionLocal()

    try:
        result = mark_live_spot_order_lower_limit_rejected_as_terminal_skip(
            db,
            allocation_leg_id=int(allocation_leg_id),
            error=error,
            diagnostics=diagnostics,
        )
        allocation_batch_id = int(result.allocation_batch_id)

        if dry_run:
            db.rollback()
        else:
            db.commit()

        log.info(
            "Allocation live spot lower-limit reject terminal skip "
            "leg_id=%s batch_id=%s status=%s action=%s reason=%s",
            allocation_leg_id,
            allocation_batch_id,
            result.status,
            result.action,
            result.reason,
        )

        _refresh_live_batch_progress_in_own_session(
            allocation_batch_id=allocation_batch_id,
            dry_run=dry_run,
        )

        return True

    except Exception as exc:
        db.rollback()
        log.exception(
            "Allocation live spot lower-limit terminal skip failed "
            "leg_id=%s original_error=%s mark_error=%s",
            allocation_leg_id,
            error,
            exc,
        )
        return False

    finally:
        db.close()


def _process_live_spot_leg_in_own_session(
    *,
    allocation_leg_id: int,
    dry_run: bool,
    fund_code: str | None,
) -> bool:
    # Phase 0: batch-level preflight before any write external action.
    db0 = SessionLocal()
    try:
        allocation_batch_id = _get_batch_id_for_leg(
            db0,
            allocation_leg_id=int(allocation_leg_id),
        )
    finally:
        db0.close()

    preflight_ok = _process_live_batch_preflight_in_own_session(
        allocation_batch_id=int(allocation_batch_id),
        dry_run=dry_run,
        fund_code=fund_code,
    )

    if not preflight_ok:
        return False

    # Phase 1: identify leg + fund client. This only decrypts local credentials,
    # it does not perform an external Bybit call.
    db1 = SessionLocal()
    try:
        fund_id, leg_type, order_link_id, bybit_order_id = _get_live_leg_snapshot(
            db1,
            allocation_leg_id=int(allocation_leg_id),
        )

        if leg_type == LEG_TYPE_STABLE_CASH:
            db1.close()
            return _mark_stable_cash_leg_in_own_session(
                allocation_leg_id=int(allocation_leg_id),
                dry_run=dry_run,
            )

        if (
            leg_type == LEG_TYPE_RESIDUAL_USDT_EARN
            and not allocation_earn_live_enabled()
            and residual_earn_to_cash_when_live_disabled()
        ):
            db1.close()
            return _mark_residual_earn_cash_in_own_session(
                allocation_leg_id=int(allocation_leg_id),
                dry_run=dry_run,
            )

        if leg_type in {LEG_TYPE_USDT_EARN_STAKE, LEG_TYPE_RESIDUAL_USDT_EARN}:
            db1.close()
            return _process_live_earn_leg_in_own_session(
                allocation_leg_id=int(allocation_leg_id),
                dry_run=dry_run,
                fund_code=fund_code,
            )

        leg = (
            db1.query(FundAllocationLeg)
            .filter(FundAllocationLeg.id == int(allocation_leg_id))
            .first()
        )
        if leg is None:
            raise RuntimeError(f"Allocation leg not found: {allocation_leg_id}")

        policy_decision = classify_live_leg_policy(leg)

        if policy_decision.policy_skipped:
            db1.close()
            return _mark_policy_skipped_leg_in_own_session(
                allocation_leg_id=int(allocation_leg_id),
                dry_run=dry_run,
                reason=policy_decision.reason or DERIVATIVE_OPTION_SKIP_REASON,
            )

        if leg_type not in {LEG_TYPE_SPOT_BUY, LEG_TYPE_BUY_THEN_STAKE}:
            log.error(
                "Allocation live adapter blocked unsupported leg after preflight "
                "leg_id=%s leg_type=%s policy_reason=%s external_calls=0",
                allocation_leg_id,
                leg_type,
                policy_decision.reason,
            )
            return False

        fund_client_ctx = build_fund_bybit_client(
            db1,
            fund_id=int(fund_id),
        )
        client = fund_client_ctx.client

    except Exception as exc:
        db1.rollback()
        log.exception(
            "Allocation live spot setup failed leg_id=%s error=%s",
            allocation_leg_id,
            exc,
        )
        return False

    finally:
        if db1.is_active:
            db1.close()

    # Phase 2: idempotency-first reconciliation.
    # If orderLinkId/orderId already exists, never send a duplicate order.
    if order_link_id or bybit_order_id:
        db2 = SessionLocal()
        try:
            result = reconcile_live_spot_market_leg_by_link_id(
                db2,
                allocation_leg_id=int(allocation_leg_id),
                client=client,
            )

            if dry_run:
                db2.rollback()
            else:
                db2.commit()

            _refresh_live_batch_progress_in_own_session(
                allocation_batch_id=int(result.allocation_batch_id),
                dry_run=dry_run,
            )

            log.info(
                "Allocation live spot idempotent reconciliation completed "
                "leg_id=%s action=%s status=%s order_link_id=%s bybit_order_id=%s",
                allocation_leg_id,
                result.action,
                result.status,
                result.order_link_id,
                result.bybit_order_id,
            )

            return bool(result.ok)

        except Exception as exc:
            db2.rollback()
            log.exception(
                "Allocation live spot idempotent reconciliation failed "
                "leg_id=%s error=%s",
                allocation_leg_id,
                exc,
            )
            return False

        finally:
            db2.close()

    # Phase 3: build plan or terminalize safe validation skip before POST.
    db3 = SessionLocal()
    try:
        plan_or_skip = prepare_live_spot_market_order_or_terminal_skip(
            db3,
            allocation_leg_id=int(allocation_leg_id),
            client=client,
        )

        if getattr(plan_or_skip, "action", "") == "terminal_validation_skip":
            allocation_batch_id = int(plan_or_skip.allocation_batch_id)

            if dry_run:
                db3.rollback()
            else:
                db3.commit()

            log.info(
                "Allocation live spot validation terminal skip "
                "leg_id=%s status=%s reason=%s external_post_calls=0 guard_required=0",
                allocation_leg_id,
                plan_or_skip.status,
                plan_or_skip.reason,
            )

            _refresh_live_batch_progress_in_own_session(
                allocation_batch_id=allocation_batch_id,
                dry_run=dry_run,
            )
            return True

        plan = plan_or_skip

        if dry_run:
            db3.rollback()
            log.info(
                "Allocation live spot dry-run stopped after order plan "
                "leg_id=%s order_link_id=%s external_post_calls=0",
                allocation_leg_id,
                plan.order_link_id,
            )
            return True

        db3.commit()

        log.info(
            "Allocation live spot orderLinkId persisted before POST "
            "leg_id=%s order_link_id=%s external_post_calls=0",
            allocation_leg_id,
            plan.order_link_id,
        )

    except SPOT_PLAN_DETERMINISTIC_FAILURE_TYPES as exc:
        db3.rollback()
        log.exception(
            "Allocation live spot deterministic plan failed leg_id=%s error=%s",
            allocation_leg_id,
            exc,
        )
        return _mark_live_spot_plan_failure_requires_review_in_own_session(
            allocation_leg_id=int(allocation_leg_id),
            dry_run=dry_run,
            error=f"spot_order_plan_deterministic_failed: {exc}",
        )
    except Exception as exc:
        db3.rollback()
        log.exception(
            "Allocation live spot uncertain plan failed leg_id=%s error=%s",
            allocation_leg_id,
            exc,
        )
        return False
    finally:
        db3.close()

    # Phase 4: Operation Guard must pass before external POST.
    db4 = SessionLocal()
    try:
        guard_decision = require_trade_guard_for_plan(
            db4,
            plan=plan,
        )
        db4.commit()

        log.info(
            "Allocation live spot Operation Guard allowed "
            "leg_id=%s order_link_id=%s guard=%s",
            allocation_leg_id,
            plan.order_link_id,
            guard_decision,
        )

    except Exception as exc:
        db4.rollback()

        db_guard_fail = SessionLocal()
        try:
            mark_live_spot_order_create_failed(
                db_guard_fail,
                allocation_leg_id=int(allocation_leg_id),
                error=f"spot_order_guard_blocked_or_error: {exc}",
            )
            db_guard_fail.commit()
        except Exception:
            db_guard_fail.rollback()
        finally:
            db_guard_fail.close()

        _refresh_live_batch_progress_in_own_session(
            allocation_batch_id=int(plan.allocation_batch_id),
            dry_run=dry_run,
        )

        log.exception(
            "Allocation live spot Operation Guard blocked "
            "leg_id=%s order_link_id=%s error=%s",
            allocation_leg_id,
            plan.order_link_id,
            exc,
        )
        return False

    finally:
        db4.close()

    # Phase 5: single external POST. If this errors, mark review and do not retry blindly.
    try:
        create_result = submit_bybit_spot_market_order(
            client,
            payload=plan.payload,
        )
    except BybitOrderCreateLowerLimitReject as exc:
        return _mark_live_spot_lower_limit_skip_in_own_session(
            allocation_leg_id=int(allocation_leg_id),
            dry_run=dry_run,
            error=f"spot_order_create_lower_limit_rejected: retCode=170140 lower-limit: {exc}",
            diagnostics={
                "source": "bybit_order_create_post",
                "order_link_id": plan.order_link_id,
                "symbol": plan.symbol,
                "category": plan.category,
                "target_usdt": str(plan.target_usdt),
                "required_usdt": str(plan.required_usdt),
                "bybit_order_created": False,
            },
        )
    except Exception as exc:
        db_fail = SessionLocal()
        try:
            mark_live_spot_order_create_failed(
                db_fail,
                allocation_leg_id=int(allocation_leg_id),
                error=f"spot_order_create_failed_or_uncertain: {exc}",
            )
            db_fail.commit()
        except Exception:
            db_fail.rollback()
        finally:
            db_fail.close()

        _refresh_live_batch_progress_in_own_session(
            allocation_batch_id=int(plan.allocation_batch_id),
            dry_run=dry_run,
        )

        log.exception(
            "Allocation live spot order POST failed or uncertain "
            "leg_id=%s order_link_id=%s",
            allocation_leg_id,
            plan.order_link_id,
        )
        return False

    # Phase 6: store returned orderId, then reconcile by orderLinkId.
    db5 = SessionLocal()
    try:
        leg = (
            db5.query(FundAllocationLeg)
            .filter(FundAllocationLeg.id == int(allocation_leg_id))
            .with_for_update()
            .first()
        )

        if leg is None:
            raise RuntimeError(f"Allocation leg not found: {allocation_leg_id}")

        order_id = create_result.get("orderId")
        if order_id:
            leg.bybit_order_id = str(order_id)

        leg.error = None
        db5.add(leg)
        db5.commit()

    except Exception as exc:
        db5.rollback()
        log.exception(
            "Allocation live spot orderId persist failed "
            "leg_id=%s order_link_id=%s error=%s",
            allocation_leg_id,
            plan.order_link_id,
            exc,
        )
        return False

    finally:
        db5.close()

    db6 = SessionLocal()
    try:
        result = reconcile_live_spot_market_leg_by_link_id(
            db6,
            allocation_leg_id=int(allocation_leg_id),
            client=client,
        )

        if dry_run:
            db6.rollback()
        else:
            db6.commit()

        _refresh_live_batch_progress_in_own_session(
            allocation_batch_id=int(result.allocation_batch_id),
            dry_run=dry_run,
        )

        log.info(
            "Allocation live spot POST/reconciliation completed "
            "leg_id=%s action=%s status=%s order_link_id=%s bybit_order_id=%s",
            allocation_leg_id,
            result.action,
            result.status,
            result.order_link_id,
            result.bybit_order_id,
        )

        return bool(result.ok)

    except Exception as exc:
        db6.rollback()
        log.exception(
            "Allocation live spot post-send reconciliation failed "
            "leg_id=%s error=%s",
            allocation_leg_id,
            exc,
        )
        return False

    finally:
        db6.close()


def _build_client(args: argparse.Namespace) -> MockAllocationExecutionClient:
    if not args.mock_market_data:
        raise RuntimeError("Only mock market-data client is allowed for mock execution paths")

    return MockAllocationExecutionClient()


def _process_leg_in_own_session(
    *,
    allocation_leg_id: int,
    dry_run: bool,
    args: argparse.Namespace,
) -> bool:
    if args.live_execution:
        return _process_live_spot_leg_in_own_session(
            allocation_leg_id=int(allocation_leg_id),
            dry_run=dry_run,
            fund_code=_normalize_fund_code(args.fund_code),
        )

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
            raise RuntimeError(f"Unexpected POST calls recorded: {client.post_calls}")

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
            FundAllocationBatch.status.in_(
                [
                    ALLOCATION_BATCH_STATUS_PLAN_CREATED,
                    ALLOCATION_BATCH_STATUS_ALLOCATION_PROCESSING,
                ]
            ),
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
    if args.live_execution:
        return _process_live_batch_preflight_in_own_session(
            allocation_batch_id=allocation_batch_id,
            dry_run=dry_run,
            fund_code=_normalize_fund_code(args.fund_code),
        )

    db = SessionLocal()
    client = _build_client(args)

    try:
        decision = process_residual_leg_mock(
            db,
            allocation_batch_id=allocation_batch_id,
            client=client,
        )

        if client.post_calls:
            raise RuntimeError(f"Unexpected POST calls recorded: {client.post_calls}")

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
    if not _validate_stage22_5_args(args):
        return 0

    log.info(
        "Stage 25 allocation execution worker started. "
        "Mock mode by default; guarded live mode runs safe preflight "
        "before any external allocation action."
    )

    if args.run_once:
        return _run_once(args)

    return _run_loop(args)


if __name__ == "__main__":
    raise SystemExit(main())