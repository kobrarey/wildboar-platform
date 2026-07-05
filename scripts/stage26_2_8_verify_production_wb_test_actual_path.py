from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from app.allocation.bybit_snapshot_reader import (
    _holding_from_position,
    _tag_position_rows,
    build_allocation_snapshot_from_bybit,
)
from app.allocation.bybit_orders import build_protected_market_order_payload
from app.allocation.earn_orders import build_earn_stake_payload
from app.allocation.earn_products import (
    EarnProductUnavailableError,
    get_earn_product_info,
    validate_earn_product_for_stake,
)
from app.allocation.execution_config import get_allocation_execution_config
from app.allocation.instrument_info import (
    InstrumentInfo,
    get_instrument_info,
    validate_instrument_for_leg,
)
from app.allocation.liquidity import (
    check_liquidity_corridor,
    get_last_price,
    get_orderbook,
)
from app.allocation.live_earn_config import (
    allocation_earn_live_enabled,
    require_live_earn_whitelisted,
)
from app.allocation.live_policy import (
    BUY_THEN_STAKE_LIVE_POLICY_FAIL_CLOSED,
    BUY_THEN_STAKE_LIVE_POLICY_SPOT_ONLY,
    DERIVATIVE_LIVE_POLICY_FAIL_CLOSED,
    DERIVATIVE_LIVE_POLICY_SKIP_EXISTING_EXPOSURE_SCALING,
    classify_live_leg_policy,
)
from app.allocation.live_spot_orders import apply_spot_validation_terminal_skip_to_leg
from app.allocation.plan_service import _build_planned_legs
from app.allocation.snapshot_service import (
    AllocationAccountRisk,
    AllocationSnapshot,
    AllocationSnapshotHolding,
    build_allocation_snapshot_from_payload,
    dec,
    parse_ts,
)
from app.allocation.statuses import (
    ALLOCATION_LEG_STATUS_PLANNED,
    ALLOCATION_LEG_STATUS_SKIPPED_EARN_UNAVAILABLE,
    ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER,
    ALLOCATION_LEG_STATUS_SKIPPED_SYMBOL_NOT_TRADING,
    ALLOCATION_LEG_STATUS_SKIPPED_ZERO_VALUE,
    LEG_TYPE_BUY_THEN_STAKE,
    LEG_TYPE_OTHER,
    LEG_TYPE_RESIDUAL_USDT_EARN,
    LEG_TYPE_SPOT_BUY,
    LEG_TYPE_USDT_EARN_STAKE,
)
from app.bybit.credentials import get_active_fund_bybit_client
from app.config import settings
from app.db import SessionLocal
from app.models import Fund, FundAllocationLeg, FundBybitAccount, FundOrder


ROOT = Path(__file__).resolve().parents[1]

READY_MARKER = "STAGE26_2_8_PRODUCTION_WB_TEST_ACTUAL_ALLOCATION_PATH_READY_OK"
NOT_READY_MARKER = "STAGE26_2_8_PRODUCTION_WB_TEST_ACTUAL_ALLOCATION_PATH_NOT_READY"
FIXTURE_ONLY_MARKER = "STAGE26_2_8_PRODUCTION_WB_TEST_ACTUAL_ALLOCATION_PATH_BLOCKED_FIXTURE_ONLY"
SNAPSHOT_UNAVAILABLE_MARKER = "STAGE26_2_8_PRODUCTION_WB_TEST_ACTUAL_ALLOCATION_PATH_BLOCKED_SNAPSHOT_UNAVAILABLE"

CONFIRMATION_PATH_MARKER = "STAGE26_2_12_PRODUCTION_VERIFIER_CONFIRMATION_PATH_OK"
CONFIRMATION_PATH_ACTUAL_RUN_MARKER = (
    "STAGE26_2_12_PRODUCTION_VERIFIER_CONFIRMATION_PATH_ACTUAL_RUN_MARKER_OK"
)
BUY_COLLECTION_CONTINUATION_PATH_MARKER = (
    "STAGE26_2_13_BUY_COLLECTION_CONTINUATION_PATH_OK"
)
BYBIT_LOWER_LIMIT_REJECT_HANDLING_MARKER = (
    "STAGE26_2_14_BYBIT_LOWER_LIMIT_REJECT_HANDLING_OK"
)


class VerificationBlocked(RuntimeError):
    pass


class NoPostBybitClient:
    def __init__(self, inner: Any):
        self.inner = inner
        self.post_calls = 0

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.inner.get(path, params or {})

    def public_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        public_get = getattr(self.inner, "public_get", None)
        if callable(public_get):
            return public_get(path, params or {})
        return self.inner.get(path, params or {})

    def paginate_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        page_limit: int = 50,
        result_list_key: str = "list",
        cursor_param: str = "cursor",
        cursor_field: str = "nextPageCursor",
    ) -> list[dict[str, Any]]:
        paginate_get = getattr(self.inner, "paginate_get", None)
        if callable(paginate_get):
            return paginate_get(
                path,
                params or {},
                page_limit=page_limit,
                result_list_key=result_list_key,
                cursor_param=cursor_param,
                cursor_field=cursor_field,
            )

        items: list[dict[str, Any]] = []
        cursor = ""
        base_params = dict(params or {})

        for _ in range(max(int(page_limit), 1)):
            page_params = dict(base_params)
            if cursor:
                page_params[cursor_param] = cursor

            payload = self.inner.get(path, page_params)
            result = payload.get("result") or {}
            chunk = result.get(result_list_key) or []
            if isinstance(chunk, list):
                items.extend([row for row in chunk if isinstance(row, dict)])

            cursor = str(result.get(cursor_field) or "").strip()
            if not cursor:
                break

        return items

    def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.post_calls += 1
        raise VerificationBlocked(f"Bybit POST is forbidden in this verifier: {path}")


class Stage26SpotValidationFakeClient:
    def __init__(
        self,
        *,
        min_order_amt_by_symbol: dict[str, str] | None = None,
        status_by_symbol: dict[str, str] | None = None,
        last_price_by_symbol: dict[str, str] | None = None,
        orderbook_mode_by_symbol: dict[str, str] | None = None,
        earn_product_id_by_coin: dict[str, str] | None = None,
        earn_product_status_by_coin: dict[str, str] | None = None,
        earn_min_stake_by_coin: dict[str, str] | None = None,
        earn_precision_by_coin: dict[str, str] | None = None,
        earn_error_by_coin: dict[str, str] | None = None,
    ):
        self.min_order_amt_by_symbol = {
            str(k).upper(): str(v)
            for k, v in (min_order_amt_by_symbol or {}).items()
        }
        self.status_by_symbol = {
            str(k).upper(): str(v)
            for k, v in (status_by_symbol or {}).items()
        }
        self.last_price_by_symbol = {
            str(k).upper(): str(v)
            for k, v in (last_price_by_symbol or {}).items()
        }
        self.orderbook_mode_by_symbol = {
            str(k).upper(): str(v)
            for k, v in (orderbook_mode_by_symbol or {}).items()
        }
        self.earn_product_id_by_coin = {
            str(k).upper(): str(v)
            for k, v in (earn_product_id_by_coin or {}).items()
        }
        self.earn_product_status_by_coin = {
            str(k).upper(): str(v)
            for k, v in (earn_product_status_by_coin or {}).items()
        }
        self.earn_min_stake_by_coin = {
            str(k).upper(): str(v)
            for k, v in (earn_min_stake_by_coin or {}).items()
        }
        self.earn_precision_by_coin = {
            str(k).upper(): str(v)
            for k, v in (earn_precision_by_coin or {}).items()
        }
        self.earn_error_by_coin = {
            str(k).upper(): str(v)
            for k, v in (earn_error_by_coin or {}).items()
        }
        self.public_get_calls: list[tuple[str, dict[str, Any]]] = []
        self.get_calls: list[tuple[str, dict[str, Any]]] = []
        self.post_calls = 0

    def public_get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        self.public_get_calls.append((path, dict(params)))

        if path == "/v5/market/instruments-info":
            symbol = str(params.get("symbol") or "").upper()
            category = str(params.get("category") or "spot").lower()
            status = self.status_by_symbol.get(symbol, "Trading")
            min_order_amt = self.min_order_amt_by_symbol.get(symbol, "1")

            return {
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "list": [
                        {
                            "symbol": symbol,
                            "category": category,
                            "status": status,
                            "baseCoin": symbol.removesuffix("USDT") if symbol.endswith("USDT") else symbol,
                            "quoteCoin": "USDT",
                            "priceFilter": {
                                "tickSize": "0.0001",
                            },
                            "lotSizeFilter": {
                                "qtyStep": "0.000001",
                                "minOrderQty": "0.000001",
                                "maxOrderQty": "100000000",
                                "minOrderAmt": min_order_amt,
                                "maxMarketOrderQty": "100000000",
                                "maxLimitOrderQty": "100000000",
                            },
                        }
                    ]
                },
            }

        if path == "/v5/market/tickers":
            symbol = str(params.get("symbol") or "").upper()
            return {
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "list": [
                        {
                            "symbol": symbol,
                            "lastPrice": self.last_price_by_symbol.get(symbol, "1"),
                        }
                    ]
                },
            }

        if path == "/v5/market/orderbook":
            symbol = str(params.get("symbol") or "").upper()
            mode = self.orderbook_mode_by_symbol.get(symbol, "sufficient")

            if mode == "empty":
                bids: list[list[str]] = []
                asks: list[list[str]] = []
            elif mode == "insufficient":
                bids = [["0.9999", "0.000001"]]
                asks = [["1.0001", "0.000001"]]
            else:
                bids = [["0.9999", "1000000"]]
                asks = [["1.0001", "1000000"]]

            return {
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "s": symbol,
                    "b": bids,
                    "a": asks,
                },
            }

        raise RuntimeError(f"Unexpected fake public_get path={path} params={params}")

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        params = params or {}
        self.get_calls.append((path, dict(params)))

        if path == "/v5/earn/product":
            coin = str(params.get("coin") or "USDT").upper()
            category = str(params.get("category") or "FlexibleSaving")

            if coin in self.earn_error_by_coin:
                raise RuntimeError(self.earn_error_by_coin[coin])

            product_id = self.earn_product_id_by_coin.get(
                coin,
                f"{coin}-{category}-001",
            )

            return {
                "retCode": 0,
                "retMsg": "OK",
                "result": {
                    "list": [
                        {
                            "productId": product_id,
                            "coin": coin,
                            "category": category,
                            "status": self.earn_product_status_by_coin.get(coin, "Available"),
                            "minStakeAmount": self.earn_min_stake_by_coin.get(coin, "0.1"),
                            "maxStakeAmount": "100000000",
                            "precision": self.earn_precision_by_coin.get(coin, "2"),
                        }
                    ]
                },
            }

        return self.public_get(path, params)

    def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.post_calls += 1
        raise VerificationBlocked(f"POST is forbidden in Stage 26.2.8D fake client: {path}")


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def imported_names(path: str) -> set[str]:
    tree = ast.parse(read(path))
    out: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            out.add(module)
            for alias in node.names:
                out.add(alias.name)

    return out


def ast_call_names(path: str) -> set[str]:
    tree = ast.parse(read(path))
    out: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                out.add(func.id)
            elif isinstance(func, ast.Attribute):
                out.add(func.attr)

    return out


def verify_settlement_transfer_confirmation_path(
    *,
    emit_assert_ok: bool = False,
) -> dict[str, Any]:
    worker_path = "workers/fund_settlement_transfer_confirmation_worker.py"
    transfer_service_path = "app/settlement/transfer_service.py"

    checks: dict[str, bool] = {}
    failures: list[str] = []

    def record(name: str, ok: bool, marker: str | None = None) -> None:
        checks[name] = bool(ok)
        if emit_assert_ok and marker is not None:
            assert_ok(marker, bool(ok))
        if not ok:
            failures.append(name)

    try:
        from app.settlement.transfer_service import (
            confirm_sent_settlement_transfer,
            confirm_sent_settlement_transfers_for_batch,
        )
        import workers.fund_settlement_transfer_confirmation_worker as confirmation_worker
    except Exception as exc:
        return {
            "ok": False,
            "checks": checks,
            "reason": f"confirmation_path_import_failed: {exc}",
        }

    worker_source = read(worker_path)
    transfer_service_source = read(transfer_service_path)
    worker_calls = ast_call_names(worker_path)

    record(
        "worker_importable",
        confirmation_worker is not None,
        "STAGE26_2_12_VERIFIER_CONFIRMATION_WORKER_IMPORTABLE",
    )
    record(
        "service_confirm_one_importable",
        callable(confirm_sent_settlement_transfer),
        "STAGE26_2_12_VERIFIER_CONFIRMATION_SERVICE_IMPORTABLE",
    )
    record(
        "service_confirm_batch_importable",
        callable(confirm_sent_settlement_transfers_for_batch),
        "STAGE26_2_12_VERIFIER_CONFIRMATION_BATCH_SERVICE_IMPORTABLE",
    )
    record(
        "worker_no_usdt_send",
        "_send_usdt_transfer" not in worker_calls and "_send_usdt_transfer" not in worker_source,
        "STAGE26_2_12_VERIFIER_CONFIRMATION_WORKER_NO_USDT_SEND",
    )
    record(
        "worker_no_native_bnb_send",
        "send_native_bnb" not in worker_calls and "send_native_bnb" not in worker_source,
        "STAGE26_2_12_VERIFIER_CONFIRMATION_WORKER_NO_NATIVE_BNB_SEND",
    )
    record(
        "worker_no_raw_tx_send",
        "send_raw_transaction" not in worker_calls and "send_raw_transaction" not in worker_source,
        "STAGE26_2_12_VERIFIER_CONFIRMATION_WORKER_NO_RAW_TX_SEND",
    )
    record(
        "service_has_public_confirm_one",
        "def confirm_sent_settlement_transfer" in transfer_service_source,
        "STAGE26_2_12_VERIFIER_CONFIRMATION_SERVICE_HAS_PUBLIC_FUNCTION",
    )
    record(
        "service_has_public_confirm_batch",
        "def confirm_sent_settlement_transfers_for_batch" in transfer_service_source,
        "STAGE26_2_12_VERIFIER_CONFIRMATION_BATCH_SERVICE_HAS_PUBLIC_FUNCTION",
    )
    record(
        "worker_imports_confirmation_service",
        "confirm_sent_settlement_transfer" in worker_source,
        "STAGE26_2_12_VERIFIER_CONFIRMATION_WORKER_USES_CONFIRMATION_SERVICE",
    )
    record(
        "worker_no_fund_order_create",
        "FundOrder(" not in worker_source and "FundOrder" not in worker_calls,
        "STAGE26_2_12_VERIFIER_CONFIRMATION_WORKER_NO_FUND_ORDER_CREATE",
    )
    record(
        "worker_no_settlement_batch_create",
        "FundSettlementBatch(" not in worker_source
        and "create_settlement_batch" not in worker_source
        and "start_settlement_batch" not in worker_source,
        "STAGE26_2_12_VERIFIER_CONFIRMATION_WORKER_NO_BATCH_CREATE",
    )

    return {
        "ok": not failures,
        "checks": checks,
        "reason": "; ".join(failures) if failures else None,
    }


def verify_buy_collection_continuation_path(
    *,
    emit_assert_ok: bool = False,
) -> dict[str, Any]:
    worker_path = "workers/fund_buy_collection_continuation_worker.py"
    service_path = "app/settlement/buy_collection_continuation.py"
    transfer_service_path = "app/settlement/transfer_service.py"

    checks: dict[str, bool] = {}
    failures: list[str] = []

    def record(name: str, ok: bool, marker: str | None = None) -> None:
        checks[name] = bool(ok)
        if emit_assert_ok and marker is not None:
            assert_ok(marker, bool(ok))
        if not ok:
            failures.append(name)

    try:
        from app.settlement.buy_collection_continuation import (
            continue_buy_collection_for_active_batches,
            scan_active_collecting_buy_usdt_batch_ids,
        )
        import workers.fund_buy_collection_continuation_worker as continuation_worker
    except Exception as exc:
        return {
            "ok": False,
            "checks": checks,
            "reason": f"buy_collection_continuation_import_failed: {exc}",
        }

    worker_source = read(worker_path)
    service_source = read(service_path)
    transfer_service_source = read(transfer_service_path)
    worker_calls = ast_call_names(worker_path)
    service_calls = ast_call_names(service_path)

    record(
        "worker_importable",
        continuation_worker is not None,
        "STAGE26_2_13_VERIFIER_CONTINUATION_WORKER_IMPORTABLE",
    )
    record(
        "service_continue_importable",
        callable(continue_buy_collection_for_active_batches),
        "STAGE26_2_13_VERIFIER_CONTINUATION_SERVICE_IMPORTABLE",
    )
    record(
        "scanner_importable",
        callable(scan_active_collecting_buy_usdt_batch_ids),
        "STAGE26_2_13_VERIFIER_CONTINUATION_SCANNER_IMPORTABLE",
    )
    record(
        "scanner_exists",
        "def scan_active_collecting_buy_usdt_batch_ids" in service_source
        and "BATCH_STATUS_COLLECTING_BUY_USDT" in service_source
        and "pricing_locked_at" in service_source
        and "pricing_unlocked_at" in service_source,
        "STAGE26_2_13_VERIFIER_CONTINUATION_SCANNER_EXISTS",
    )
    record(
        "service_calls_existing_collection_service",
        "collect_buy_usdt_for_batch" in service_calls
        or "collect_buy_usdt_for_batch" in service_source,
        "STAGE26_2_13_VERIFIER_CONTINUATION_CALLS_EXISTING_SERVICE",
    )
    record(
        "worker_live_gated",
        "evaluate_live_gate" in worker_source
        and "LIFECYCLE_WORKERS_PRODUCTION_LIVE_ENABLED" in worker_source
        and "SETTLEMENT_ENABLED" in worker_source
        and "SETTLEMENT_BUY_COLLECTION_ALLOW_LIVE_BSC" in worker_source
        and "live_bsc" in worker_source,
        "STAGE26_2_13_VERIFIER_CONTINUATION_LIVE_GATED",
    )
    record(
        "worker_no_usdt_send",
        "_send_usdt_transfer" not in worker_calls and "_send_usdt_transfer" not in worker_source,
        "STAGE26_2_13_VERIFIER_CONTINUATION_WORKER_NO_USDT_SEND",
    )
    record(
        "worker_no_native_bnb_send",
        "send_native_bnb" not in worker_calls and "send_native_bnb" not in worker_source,
        "STAGE26_2_13_VERIFIER_CONTINUATION_WORKER_NO_NATIVE_BNB_SEND",
    )
    record(
        "worker_no_raw_tx_send",
        "send_raw_transaction" not in worker_calls and "send_raw_transaction" not in worker_source,
        "STAGE26_2_13_VERIFIER_CONTINUATION_WORKER_NO_RAW_TX_SEND",
    )
    record(
        "worker_no_transfer_creation_logic",
        "_create_or_update_transfer" not in worker_source
        and "FundSettlementTransfer(" not in worker_source,
        "STAGE26_2_13_VERIFIER_CONTINUATION_WORKER_NO_TRANSFER_CREATION_LOGIC",
    )
    record(
        "worker_no_order_or_batch_creation",
        "FundOrder(" not in worker_source
        and "FundSettlementBatch(" not in worker_source
        and "run_settlement_batches_once" not in worker_source,
        "STAGE26_2_13_VERIFIER_CONTINUATION_WORKER_NO_ORDER_BATCH_CREATE",
    )
    record(
        "operation_guard_still_in_transfer_service",
        "require_bsc_buy_collection_usdt_to_settlement_guard" in transfer_service_source
        and "_send_usdt_transfer" in transfer_service_source,
        "STAGE26_2_13_VERIFIER_CONTINUATION_OPERATION_GUARD_NOT_BYPASSED",
    )

    return {
        "ok": not failures,
        "checks": checks,
        "reason": "; ".join(failures) if failures else None,
    }


def verify_bybit_lower_limit_reject_handling_path(
    *,
    emit_assert_ok: bool = False,
) -> dict[str, Any]:
    live_spot_path = "app/allocation/live_spot_orders.py"
    worker_path = "workers/fund_allocation_execution_worker.py"
    repair_path = "scripts/stage26_2_14_repair_lower_limit_allocation_leg.py"

    checks: dict[str, bool] = {}
    failures: list[str] = []

    def record(name: str, ok: bool, marker: str | None = None) -> None:
        checks[name] = bool(ok)
        if emit_assert_ok and marker is not None:
            assert_ok(marker, bool(ok))
        if not ok:
            failures.append(name)

    try:
        from app.allocation.live_spot_orders import (
            BybitOrderCreateLowerLimitReject,
            is_bybit_order_create_lower_limit_reject,
            mark_live_spot_order_lower_limit_rejected_as_terminal_skip,
            repair_live_spot_lower_limit_order_not_found_if_safe,
        )
    except Exception as exc:
        return {
            "ok": False,
            "checks": checks,
            "reason": f"bybit_lower_limit_import_failed: {exc}",
        }

    live_spot_source = read(live_spot_path)
    worker_source = read(worker_path)
    repair_source = read(repair_path) if (ROOT / repair_path).exists() else ""

    record(
        "classifier_importable",
        callable(is_bybit_order_create_lower_limit_reject),
        "STAGE26_2_14_VERIFIER_LOWER_LIMIT_CLASSIFIER_IMPORTABLE",
    )
    record(
        "exception_importable",
        BybitOrderCreateLowerLimitReject is not None,
        "STAGE26_2_14_VERIFIER_LOWER_LIMIT_EXCEPTION_IMPORTABLE",
    )
    record(
        "terminal_skip_importable",
        callable(mark_live_spot_order_lower_limit_rejected_as_terminal_skip),
        "STAGE26_2_14_VERIFIER_LOWER_LIMIT_TERMINAL_SKIP_IMPORTABLE",
    )
    record(
        "repair_importable",
        callable(repair_live_spot_lower_limit_order_not_found_if_safe),
        "STAGE26_2_14_VERIFIER_LOWER_LIMIT_REPAIR_IMPORTABLE",
    )
    record(
        "classifier_detects_response_dict",
        is_bybit_order_create_lower_limit_reject(
            {"retCode": 170140, "retMsg": "Order value exceeded lower limit"}
        ),
        "STAGE26_2_14_VERIFIER_LOWER_LIMIT_CLASSIFIES_RESPONSE",
    )
    record(
        "classifier_detects_exception_text",
        is_bybit_order_create_lower_limit_reject(
            RuntimeError("retCode=170140 retMsg=Order value exceeded lower limit")
        ),
        "STAGE26_2_14_VERIFIER_LOWER_LIMIT_CLASSIFIES_EXCEPTION",
    )
    record(
        "classifier_rejects_other_errors",
        not is_bybit_order_create_lower_limit_reject(
            {"retCode": 10001, "retMsg": "generic parameter error"}
        ),
        "STAGE26_2_14_VERIFIER_LOWER_LIMIT_REJECTS_OTHER_ERRORS",
    )
    record(
        "submit_raises_deterministic_exception",
        "BybitOrderCreateLowerLimitReject" in live_spot_source
        and "is_bybit_order_create_lower_limit_reject(response)" in live_spot_source,
        "STAGE26_2_14_VERIFIER_LOWER_LIMIT_SUBMIT_CLASSIFIED",
    )
    record(
        "terminal_skip_clears_order_link_id",
        "leg.order_link_id = None" in live_spot_source
        and "ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER" in live_spot_source
        and "leg.actual_cash_used_usdt = ZERO" in live_spot_source,
        "STAGE26_2_14_VERIFIER_LOWER_LIMIT_CLEARS_ORDER_LINK",
    )
    record(
        "worker_special_cases_lower_limit",
        "except BybitOrderCreateLowerLimitReject" in worker_source
        and "mark_live_spot_order_lower_limit_rejected_as_terminal_skip" in worker_source
        and "return True" in worker_source,
        "STAGE26_2_14_VERIFIER_WORKER_LOWER_LIMIT_TERMINAL_SKIP",
    )
    record(
        "worker_non_lower_still_fail_closed",
        "spot_order_create_failed_or_uncertain" in worker_source
        and "mark_live_spot_order_create_failed" in worker_source,
        "STAGE26_2_14_VERIFIER_NON_LOWER_LIMIT_FAIL_CLOSED",
    )
    record(
        "repair_cli_exists",
        "stage26_2_14_repair_lower_limit_allocation_leg" in repair_path
        and "repair_live_spot_lower_limit_order_not_found_if_safe" in repair_source
        and "--allocation-leg-id" in repair_source
        and "--dry-run" in repair_source
        and "--apply" in repair_source,
        "STAGE26_2_14_VERIFIER_REPAIR_CLI_EXISTS",
    )
    record(
        "repair_cli_no_post_or_bsc",
        ".post(" not in repair_source
        and "send_raw_transaction" not in repair_source
        and "_send_usdt_transfer" not in repair_source
        and "send_native_bnb" not in repair_source,
        "STAGE26_2_14_VERIFIER_REPAIR_CLI_NO_SENDS",
    )

    return {
        "ok": not failures,
        "checks": checks,
        "reason": "; ".join(failures) if failures else None,
    }


def emit_actual_ready_markers_after_confirmation_path(
    *,
    confirmation_path_result: dict[str, Any] | None = None,
    buy_collection_continuation_result: dict[str, Any] | None = None,
    bybit_lower_limit_result: dict[str, Any] | None = None,
) -> bool:
    confirmation_result = confirmation_path_result

    if confirmation_result is None:
        try:
            confirmation_result = verify_settlement_transfer_confirmation_path()
        except Exception as exc:
            confirmation_result = {
                "ok": False,
                "reason": f"confirmation_path_verification_exception: {exc}",
            }

    if not confirmation_result.get("ok"):
        reason = confirmation_result.get("reason") or "unknown"
        print(f"confirmation_path_verification_failed: {reason}")
        print(NOT_READY_MARKER)
        return False

    continuation_result = buy_collection_continuation_result

    if continuation_result is None:
        try:
            continuation_result = verify_buy_collection_continuation_path()
        except Exception as exc:
            continuation_result = {
                "ok": False,
                "reason": f"buy_collection_continuation_path_verification_exception: {exc}",
            }

    if not continuation_result.get("ok"):
        reason = continuation_result.get("reason") or "unknown"
        print(f"buy_collection_continuation_path_verification_failed: {reason}")
        print(NOT_READY_MARKER)
        return False

    lower_limit_result = bybit_lower_limit_result

    if lower_limit_result is None:
        try:
            lower_limit_result = verify_bybit_lower_limit_reject_handling_path()
        except Exception as exc:
            lower_limit_result = {
                "ok": False,
                "reason": f"bybit_lower_limit_reject_handling_verification_exception: {exc}",
            }

    if not lower_limit_result.get("ok"):
        reason = lower_limit_result.get("reason") or "unknown"
        print(f"bybit_lower_limit_reject_handling_verification_failed: {reason}")
        print(NOT_READY_MARKER)
        return False

    print(CONFIRMATION_PATH_MARKER)
    print(BUY_COLLECTION_CONTINUATION_PATH_MARKER)
    print(BYBIT_LOWER_LIMIT_REJECT_HANDLING_MARKER)
    print(READY_MARKER)
    return True


def print_kv(key: str, value: Any) -> None:
    print(f"{key}={value}")


def decimal_to_str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def policy_snapshot() -> dict[str, Any]:
    return {
        "ALLOCATION_DERIVATIVE_LIVE_POLICY": settings.ALLOCATION_DERIVATIVE_LIVE_POLICY,
        "ALLOCATION_BUY_THEN_STAKE_LIVE_POLICY": settings.ALLOCATION_BUY_THEN_STAKE_LIVE_POLICY,
        "ALLOCATION_EARN_ENABLED": settings.ALLOCATION_EARN_ENABLED,
        "ALLOCATION_EARN_ALLOW_LIVE": settings.ALLOCATION_EARN_ALLOW_LIVE,
        "ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST": settings.ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST,
        "ALLOCATION_EARN_ALLOWED_FUND_CODES": settings.ALLOCATION_EARN_ALLOWED_FUND_CODES,
        "ALLOCATION_EARN_ALLOWED_COINS": settings.ALLOCATION_EARN_ALLOWED_COINS,
        "ALLOCATION_EARN_ALLOWED_CATEGORIES": settings.ALLOCATION_EARN_ALLOWED_CATEGORIES,
        "ALLOCATION_EARN_ALLOWED_PRODUCT_IDS": settings.ALLOCATION_EARN_ALLOWED_PRODUCT_IDS,
    }


def restore_policy(snapshot: dict[str, Any]) -> None:
    for key, value in snapshot.items():
        setattr(settings, key, value)


def print_effective_policies() -> None:
    for key, value in policy_snapshot().items():
        print_kv(key, value)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stage 26.2.8A production-safe wb_test allocation path verifier. "
            "No live orders, no lifecycle, no BSC transactions."
        )
    )
    parser.add_argument("--fund-code", default="wb_test")
    parser.add_argument("--positive-net-usdt", default="10")
    parser.add_argument("--snapshot-json", default=None)
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument(
        "--fixture-mode",
        action="store_true",
        help="Local unit mode only. Always prints BLOCKED_FIXTURE_ONLY, never READY.",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Run local source/unit checks without production readiness marker.",
    )
    return parser.parse_args()


def require_rollback(args: argparse.Namespace) -> None:
    if not args.rollback:
        raise VerificationBlocked(
            "--rollback is required. This verifier refuses non-rollback execution."
        )


def normalize_fund_code(value: str) -> str:
    code = str(value or "").strip().lower()
    if not code:
        raise VerificationBlocked("--fund-code is required")
    return code


def parse_positive_net(value: str) -> Decimal:
    amount = Decimal(str(value))
    if amount <= Decimal("0"):
        raise VerificationBlocked("--positive-net-usdt must be > 0")
    return amount


def get_fund_or_fail(db: Any, *, fund_code: str) -> Fund:
    fund = db.query(Fund).filter(Fund.code == fund_code).first()
    if fund is None:
        raise VerificationBlocked(f"Fund not found: {fund_code}")
    if not fund.is_active:
        raise VerificationBlocked(f"Fund is not active: {fund_code}")
    return fund


def get_active_bybit_account_or_fail(db: Any, *, fund_id: int) -> FundBybitAccount:
    account = (
        db.query(FundBybitAccount)
        .filter(
            FundBybitAccount.fund_id == int(fund_id),
            FundBybitAccount.coin == "USDT",
            FundBybitAccount.chain_type == "BSC",
            FundBybitAccount.is_active == True,
        )
        .first()
    )

    if account is None:
        raise VerificationBlocked(
            f"Active fund_bybit_accounts row not found for fund_id={fund_id}"
        )
    if not account.api_key_is_active:
        raise VerificationBlocked(f"Bybit API key is inactive for fund_id={fund_id}")
    if not account.api_key_encrypted or not account.api_secret_encrypted:
        raise VerificationBlocked(f"Bybit API encrypted credentials missing for fund_id={fund_id}")

    return account


def load_snapshot_dict(path: str) -> dict[str, Any]:
    snapshot_path = Path(path)
    if not snapshot_path.exists():
        raise VerificationBlocked(f"Snapshot JSON not found: {snapshot_path}")

    with snapshot_path.open("r", encoding="utf-8-sig") as f:
        payload = json.load(f)

    if not isinstance(payload, dict):
        raise VerificationBlocked("Snapshot JSON root must be an object")

    return payload


def account_risk_from_dict(data: dict[str, Any]) -> AllocationAccountRisk:
    return AllocationAccountRisk(
        total_equity_usdt=dec(data.get("total_equity_usdt") or data.get("totalEquity")),
        total_wallet_balance_usdt=dec(
            data.get("total_wallet_balance_usdt") or data.get("totalWalletBalance")
        ),
        total_available_usdt=dec(
            data.get("total_available_usdt") or data.get("totalAvailableBalance")
        ),
        total_initial_margin_usdt=dec(
            data.get("total_initial_margin_usdt") or data.get("totalInitialMargin")
        ),
        total_maintenance_margin_usdt=dec(
            data.get("total_maintenance_margin_usdt") or data.get("totalMaintenanceMargin")
        ),
        account_im_rate=dec(data.get("account_im_rate") or data.get("accountIMRate")),
        account_mm_rate=dec(data.get("account_mm_rate") or data.get("accountMMRate")),
    )


def _maybe_dec(data: dict[str, Any], key: str) -> Decimal | None:
    value = data.get(key)
    return dec(value) if value not in (None, "") else None


def holding_from_dict(data: dict[str, Any]) -> AllocationSnapshotHolding:
    return AllocationSnapshotHolding(
        leg_group=str(data.get("leg_group") or data.get("legGroup") or ""),
        leg_type=str(data.get("leg_type") or data.get("legType") or ""),
        coin=data.get("coin"),
        symbol=data.get("symbol"),
        category=data.get("category"),
        side=data.get("side"),
        location=data.get("location"),
        size=_maybe_dec(data, "size"),
        usd_value=_maybe_dec(data, "usd_value"),
        notional_usd=_maybe_dec(data, "notional_usd"),
        avg_price=_maybe_dec(data, "avg_price"),
        mark_price=_maybe_dec(data, "mark_price"),
        leverage=_maybe_dec(data, "leverage"),
        product=data.get("product"),
        product_category=data.get("product_category"),
        extra=data.get("extra") if isinstance(data.get("extra"), dict) else {},
    )


def snapshot_from_saved_json(
    *,
    fund_id: int,
    fund_code: str,
    payload: dict[str, Any],
    path: str,
) -> AllocationSnapshot:
    if isinstance(payload.get("holdings"), list) and isinstance(payload.get("risk"), dict):
        return AllocationSnapshot(
            fund_id=fund_id,
            fund_code=fund_code,
            snapshot_ts=parse_ts(payload.get("snapshot_ts")),
            account_type=str(payload.get("account_type") or "UNIFIED"),
            risk=account_risk_from_dict(payload["risk"]),
            holdings=[holding_from_dict(row) for row in payload["holdings"] if isinstance(row, dict)],
            raw_summary_json=payload.get("raw_summary_json") if isinstance(payload.get("raw_summary_json"), dict) else payload,
            snapshot_source=f"real_snapshot_json:{Path(path).name}",
        )

    return build_allocation_snapshot_from_payload(
        fund_id=fund_id,
        fund_code=fund_code,
        payload=payload,
        snapshot_source=f"real_snapshot_json:{Path(path).name}",
    )


def build_fixture_snapshot() -> AllocationSnapshot:
    return AllocationSnapshot(
        fund_id=1,
        fund_code="wb_test",
        snapshot_ts=datetime.now(timezone.utc),
        account_type="UNIFIED",
        snapshot_source="stage26_2_8_fixture_only",
        risk=AllocationAccountRisk(
            total_equity_usdt=Decimal("1010"),
            total_wallet_balance_usdt=Decimal("1010"),
            total_available_usdt=Decimal("900"),
            total_initial_margin_usdt=Decimal("100"),
            total_maintenance_margin_usdt=Decimal("50"),
            account_im_rate=Decimal("0.10"),
            account_mm_rate=Decimal("0.05"),
        ),
        holdings=[
            AllocationSnapshotHolding(
                leg_group="cash",
                leg_type="stable_cash",
                coin="USDT",
                category="wallet",
                location="UNIFIED",
                size=Decimal("110"),
                usd_value=Decimal("110"),
            ),
            AllocationSnapshotHolding(
                leg_group="spot",
                leg_type="spot_holding",
                coin="BTC",
                symbol="BTCUSDT",
                category="spot",
                location="UNIFIED",
                size=Decimal("0.01"),
                usd_value=Decimal("300"),
            ),
            AllocationSnapshotHolding(
                leg_group="earn",
                leg_type="earn_holding",
                coin="USDT",
                category="earn",
                location="EARN",
                size=Decimal("200"),
                usd_value=Decimal("200"),
                product="Earn",
                product_category="FlexibleSaving",
            ),
            AllocationSnapshotHolding(
                leg_group="earn",
                leg_type="earn_holding",
                coin="LDO",
                symbol="LDOUSDT",
                category="earn",
                location="EARN",
                size=Decimal("20"),
                usd_value=Decimal("40"),
                product="Earn",
                product_category="FlexibleSaving",
            ),
            AllocationSnapshotHolding(
                leg_group="perp",
                leg_type="perp_position",
                coin="ETH",
                symbol="ETHUSDT",
                category="linear",
                side="Buy",
                location="UNIFIED",
                size=Decimal("0.10"),
                notional_usd=Decimal("250"),
            ),
            AllocationSnapshotHolding(
                leg_group="long_option",
                leg_type="long_option_position",
                coin="BTC",
                symbol="BTC-31DEC26-100000-C",
                category="option",
                side="Buy",
                location="UNIFIED",
                size=Decimal("1"),
                notional_usd=Decimal("100"),
            ),
        ],
        raw_summary_json={"source": "stage26_2_8_fixture_only"},
    )


def build_stage26_2_8c_production_blocker_snapshot() -> AllocationSnapshot:
    return AllocationSnapshot(
        fund_id=1,
        fund_code="wb_test",
        snapshot_ts=datetime.now(timezone.utc),
        account_type="UNIFIED",
        snapshot_source="stage26_2_8c_real_production_blockers_fixture",
        risk=AllocationAccountRisk(
            total_equity_usdt=Decimal("537.85742384"),
            total_wallet_balance_usdt=Decimal("537.85742384"),
            total_available_usdt=Decimal("400"),
            total_initial_margin_usdt=Decimal("10"),
            total_maintenance_margin_usdt=Decimal("5"),
            account_im_rate=Decimal("0.02"),
            account_mm_rate=Decimal("0.01"),
        ),
        holdings=[
            AllocationSnapshotHolding(
                leg_group="cash",
                leg_type="stable_cash",
                coin="USDT",
                category="wallet",
                location="UNIFIED",
                size=Decimal("100"),
                usd_value=Decimal("100"),
            ),
            AllocationSnapshotHolding(
                leg_group="spot",
                leg_type="spot_holding",
                coin="BTC",
                symbol="BTCUSDT",
                category="spot",
                location="UNIFIED",
                size=Decimal("0.001"),
                usd_value=Decimal("100"),
            ),
            AllocationSnapshotHolding(
                leg_group="earn",
                leg_type="earn_holding",
                coin="USDT",
                category="earn",
                location="EARN",
                size=Decimal("100"),
                usd_value=Decimal("100"),
                product="Earn",
                product_category="FlexibleSaving",
            ),
            AllocationSnapshotHolding(
                leg_group="funding_wallet",
                leg_type="funding_wallet_asset",
                coin="LDO",
                symbol="LDOUSDT",
                category="funding",
                location="FUND",
                size=Decimal("1"),
                usd_value=Decimal("0.50"),
                extra={"source": "stage26_2_8c_regression_funding_wallet_asset"},
            ),
            AllocationSnapshotHolding(
                leg_group="perp",
                leg_type="perp_position",
                coin="ENA",
                symbol="ENAUSDT",
                category="linear",
                side="Sell",
                location="UNIFIED",
                size=Decimal("0"),
                notional_usd=Decimal("0"),
            ),
            AllocationSnapshotHolding(
                leg_group="perp",
                leg_type="perp_position",
                coin="ETH",
                symbol="ETHUSDT",
                category="linear",
                side="Buy",
                location="UNIFIED",
                size=Decimal("0"),
                notional_usd=Decimal("0"),
            ),
            AllocationSnapshotHolding(
                leg_group="perp",
                leg_type="perp_position",
                coin="BTC",
                symbol="BTCUSDT",
                category="linear",
                side="Buy",
                location="UNIFIED",
                size=Decimal("0"),
                notional_usd=Decimal("0"),
            ),
            AllocationSnapshotHolding(
                leg_group="short_option",
                leg_type="short_option_position",
                coin="BTC",
                symbol="BTC-25SEP26-80000-C-USDT",
                category="option",
                side="Sell",
                location="UNIFIED",
                size=Decimal("0"),
                notional_usd=Decimal("0"),
            ),
            AllocationSnapshotHolding(
                leg_group="long_option",
                leg_type="long_option_position",
                coin="BTC",
                symbol="BTC-25SEP26-78000-C-USDT",
                category="option",
                side="Buy",
                location="UNIFIED",
                size=Decimal("0"),
                notional_usd=Decimal("0"),
            ),
            AllocationSnapshotHolding(
                leg_group="long_option",
                leg_type="long_option_position",
                coin="ETH",
                symbol="ETH-25SEP26-2200-C-USDT",
                category="option",
                side="Buy",
                location="UNIFIED",
                size=Decimal("0"),
                notional_usd=Decimal("0"),
            ),
        ],
        raw_summary_json={"source": "stage26_2_8c_real_production_blockers_fixture"},
    )


def build_snapshot(
    db: Any,
    *,
    args: argparse.Namespace,
    fund: Fund,
    no_post_client_holder: dict[str, NoPostBybitClient | None],
) -> tuple[AllocationSnapshot, str, bool]:
    if args.fixture_mode:
        return build_fixture_snapshot(), "fixture_only", True

    if args.snapshot_json:
        payload = load_snapshot_dict(args.snapshot_json)
        return (
            snapshot_from_saved_json(
                fund_id=int(fund.id),
                fund_code=str(fund.code),
                payload=payload,
                path=args.snapshot_json,
            ),
            "snapshot_json",
            False,
        )

    try:
        client = get_active_fund_bybit_client(
            db,
            fund_id=int(fund.id),
            coin="USDT",
            chain_type="BSC",
        )
        no_post_client = NoPostBybitClient(client)
        no_post_client_holder["client"] = no_post_client

        snapshot = build_allocation_snapshot_from_bybit(
            db,
            fund_id=int(fund.id),
            client=no_post_client,
        )
        return snapshot, "bybit_readonly", False

    except Exception as exc:
        print(f"SNAPSHOT_ERROR={exc}")
        raise VerificationBlocked(SNAPSHOT_UNAVAILABLE_MARKER) from exc


def planned_leg_to_model(
    *,
    planned: Any,
    idx: int,
    fund_id: int,
) -> FundAllocationLeg:
    return FundAllocationLeg(
        id=idx,
        allocation_batch_id=0,
        settlement_batch_id=0,
        fund_id=int(fund_id),
        leg_index=planned.leg_index,
        leg_key=planned.leg_key,
        leg_group=planned.leg_group,
        leg_type=planned.leg_type,
        coin=planned.coin,
        symbol=planned.symbol,
        category=planned.category,
        side=planned.side,
        location=planned.location,
        current_size=planned.current_size,
        current_usd_value=planned.current_usd_value,
        current_notional_usd=planned.current_notional_usd,
        source_weight=planned.source_weight,
        target_usdt=planned.target_usdt,
        target_qty=planned.target_qty,
        status=planned.status,
        execution_mode="planned",
        error=planned.error,
    )


def build_plan(snapshot: AllocationSnapshot, *, positive_net_usdt: Decimal) -> dict[str, Any]:
    base_nav_for_scale_usdt = dec(snapshot.total_equity_usdt) - positive_net_usdt

    if base_nav_for_scale_usdt <= Decimal("0"):
        raise VerificationBlocked("base_nav_for_scale_usdt must be positive for verification")

    scale = positive_net_usdt / base_nav_for_scale_usdt

    planned_legs, raw_cash_usdt, adjusted_cash_usdt = _build_planned_legs(
        snapshot=snapshot,
        positive_net_usdt=positive_net_usdt,
        scale=scale,
        base_nav_for_scale_usdt=base_nav_for_scale_usdt,
    )

    leg_models = [
        planned_leg_to_model(planned=planned, idx=idx, fund_id=snapshot.fund_id)
        for idx, planned in enumerate(planned_legs, start=1)
    ]

    return {
        "base_nav_for_scale_usdt": base_nav_for_scale_usdt,
        "scale": scale,
        "raw_cash_usdt": raw_cash_usdt,
        "adjusted_cash_usdt": adjusted_cash_usdt,
        "planned_legs": planned_legs,
        "leg_models": leg_models,
    }


def _is_earn_execution_path_leg(leg: FundAllocationLeg) -> bool:
    return str(leg.leg_type or "") in {
        LEG_TYPE_USDT_EARN_STAKE,
        LEG_TYPE_RESIDUAL_USDT_EARN,
    }


def _earn_execution_path_coin(leg: FundAllocationLeg) -> str:
    return str(leg.coin or "USDT").strip().upper() or "USDT"


def _earn_execution_path_category(leg: FundAllocationLeg) -> str:
    raw = str(leg.category or "").strip()

    if raw and raw.lower() not in {"earn", "spot"}:
        return raw

    return str(settings.ALLOCATION_USDT_EARN_CATEGORY)


def _earn_execution_path_amount(leg: FundAllocationLeg) -> Decimal:
    coin = _earn_execution_path_coin(leg)
    target_usdt = dec(leg.target_usdt)
    target_qty = dec(leg.target_qty)

    if coin == "USDT":
        return target_usdt if target_usdt > Decimal("0") else target_qty

    return target_qty if target_qty > Decimal("0") else target_usdt


def _is_spot_execution_path_leg(leg: FundAllocationLeg) -> bool:
    return str(leg.leg_type or "") in {LEG_TYPE_SPOT_BUY, LEG_TYPE_BUY_THEN_STAKE}


def _spot_execution_path_category(leg: FundAllocationLeg) -> str:
    if str(leg.leg_type or "") == LEG_TYPE_BUY_THEN_STAKE:
        return "spot"

    raw = str(leg.category or "").strip().lower()
    return raw or "spot"


def _spot_execution_path_side(leg: FundAllocationLeg) -> str:
    raw = str(leg.side or "").strip().lower()

    if raw in {"sell", "short"}:
        return "Sell"

    return "Buy"


def _target_qty_for_liquidity_check(
    *,
    leg: FundAllocationLeg,
    last_price: Decimal,
) -> Decimal:
    target_qty = dec(leg.target_qty)
    if target_qty > Decimal("0"):
        return target_qty

    target_usdt = dec(leg.target_usdt)
    if target_usdt > Decimal("0") and dec(last_price) > Decimal("0"):
        return target_usdt / dec(last_price)

    return Decimal("0")


def check_spot_execution_path_readonly(
    leg: FundAllocationLeg,
    *,
    client: Any,
) -> dict[str, Any]:
    config = get_allocation_execution_config()
    symbol = str(leg.symbol or "").strip().upper()
    category = _spot_execution_path_category(leg)
    target_usdt = dec(leg.target_usdt)

    base = {
        "execution_path_checked": True,
        "execution_path_action": None,
        "execution_path_reason": None,
        "spot_validation_status": None,
        "spot_min_order_amt": None,
        "spot_target_usdt": str(target_usdt),
        "liquidity_checked": False,
        "liquidity_ok": None,
        "liquidity_error": None,
        "last_price": None,
        "best_bid": None,
        "best_ask": None,
        "available_liquidity_qty": None,
        "available_liquidity_usdt": None,
        "required_qty": None,
        "required_usdt": None,
        "order_payload_build_ok": False,
        "required_guard_actions": ["bybit_allocation_trade_order"],
        "supported_live": True,
        "policy_skipped": False,
        "fail_closed": False,
    }

    if not symbol:
        base.update(
            {
                "execution_path_action": "fail_closed",
                "execution_path_reason": "spot_execution_path_symbol_required",
                "supported_live": False,
                "policy_skipped": False,
                "fail_closed": True,
                "required_guard_actions": [],
            }
        )
        return base

    if category != "spot":
        base.update(
            {
                "execution_path_action": "fail_closed",
                "execution_path_reason": f"spot_execution_path_category_not_spot: {category}",
                "supported_live": False,
                "policy_skipped": False,
                "fail_closed": True,
                "required_guard_actions": [],
            }
        )
        return base

    try:
        info = get_instrument_info(
            client,
            category="spot",
            symbol=symbol,
        )
        validation = validate_instrument_for_leg(leg, info)
    except Exception as exc:
        base.update(
            {
                "execution_path_action": "fail_closed",
                "execution_path_reason": f"spot_execution_path_instrument_read_failed: {exc}",
                "supported_live": False,
                "policy_skipped": False,
                "fail_closed": True,
                "required_guard_actions": [],
            }
        )
        return base

    base["spot_validation_status"] = validation.status
    base["spot_min_order_amt"] = str(validation.min_order_amt) if validation.min_order_amt is not None else None

    if not validation.ok:
        if validation.status in {
            ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER,
            ALLOCATION_LEG_STATUS_SKIPPED_SYMBOL_NOT_TRADING,
        }:
            base.update(
                {
                    "execution_path_action": "terminal_skip",
                    "execution_path_reason": validation.error or validation.status,
                    "required_guard_actions": [],
                    "supported_live": False,
                    "policy_skipped": True,
                    "fail_closed": False,
                }
            )
            return base

        base.update(
            {
                "execution_path_action": "fail_closed",
                "execution_path_reason": validation.error or validation.status,
                "required_guard_actions": [],
                "supported_live": False,
                "policy_skipped": False,
                "fail_closed": True,
            }
        )
        return base

    try:
        last_price = get_last_price(
            client,
            category=info.category,
            symbol=info.symbol,
        )
        orderbook = get_orderbook(
            client,
            category=info.category,
            symbol=info.symbol,
        )
        target_qty = _target_qty_for_liquidity_check(
            leg=leg,
            last_price=last_price,
        )
        liquidity = check_liquidity_corridor(
            side=_spot_execution_path_side(leg),
            target_qty=target_qty,
            target_usdt=target_usdt,
            last_price=last_price,
            orderbook=orderbook,
            corridor_pct=config.liquidity_corridor_pct,
            liquidity_multiplier=Decimal("1.0"),
        )

        base.update(
            {
                "liquidity_checked": True,
                "liquidity_ok": bool(liquidity.ok),
                "liquidity_error": liquidity.error,
                "last_price": str(liquidity.last_price),
                "best_bid": str(liquidity.best_bid) if liquidity.best_bid is not None else None,
                "best_ask": str(liquidity.best_ask) if liquidity.best_ask is not None else None,
                "available_liquidity_qty": str(liquidity.available_liquidity_qty),
                "available_liquidity_usdt": str(liquidity.available_liquidity_usdt),
                "required_qty": str(liquidity.required_qty),
                "required_usdt": str(liquidity.required_usdt),
            }
        )

        if not liquidity.ok:
            base.update(
                {
                    "execution_path_action": "fail_closed",
                    "execution_path_reason": liquidity.error or "spot_liquidity_check_failed",
                    "required_guard_actions": [],
                    "supported_live": False,
                    "policy_skipped": False,
                    "fail_closed": True,
                    "order_payload_build_ok": False,
                }
            )
            return base

        order_payload = build_protected_market_order_payload(
            category=info.category,
            symbol=info.symbol,
            side=_spot_execution_path_side(leg),
            target_qty=liquidity.required_qty,
            target_usdt=target_usdt,
            order_link_id=f"stage26-verify-{int(leg.leg_index)}-{int(leg.id or 0)}",
            slippage_pct=config.market_slippage_pct,
        )

        if not order_payload.payload.get("orderLinkId"):
            raise RuntimeError("protected market payload missing orderLinkId")

        base.update(
            {
                "execution_path_action": "orderable",
                "execution_path_reason": None,
                "required_guard_actions": ["bybit_allocation_trade_order"],
                "supported_live": True,
                "policy_skipped": False,
                "fail_closed": False,
                "order_payload_build_ok": True,
            }
        )
        return base

    except Exception as exc:
        base.update(
            {
                "execution_path_action": "fail_closed",
                "execution_path_reason": f"spot_execution_path_pre_order_failed: {exc}",
                "required_guard_actions": [],
                "supported_live": False,
                "policy_skipped": False,
                "fail_closed": True,
                "order_payload_build_ok": False,
            }
        )
        return base


def check_earn_execution_path_readonly(
    leg: FundAllocationLeg,
    *,
    client: Any,
    fund_code: str,
) -> dict[str, Any]:
    coin = _earn_execution_path_coin(leg)
    category = _earn_execution_path_category(leg)
    target_amount = _earn_execution_path_amount(leg)

    base = {
        "earn_execution_path_checked": True,
        "earn_execution_path_action": None,
        "earn_execution_path_reason": None,
        "earn_product_id": None,
        "earn_product_category": category,
        "earn_product_status": None,
        "earn_min_stake_amount": None,
        "earn_target_amount": str(target_amount),
        "earn_stake_amount": None,
        "earn_residual_amount": None,
        "earn_payload_build_ok": False,
        "required_guard_actions": ["bybit_allocation_earn_order"],
        "supported_live": True,
        "policy_skipped": False,
        "fail_closed": False,
    }

    if not coin:
        base.update(
            {
                "earn_execution_path_action": "fail_closed",
                "earn_execution_path_reason": "earn_execution_path_coin_required",
                "required_guard_actions": [],
                "supported_live": False,
                "policy_skipped": False,
                "fail_closed": True,
            }
        )
        return base

    if not category:
        base.update(
            {
                "earn_execution_path_action": "fail_closed",
                "earn_execution_path_reason": "earn_execution_path_category_required",
                "required_guard_actions": [],
                "supported_live": False,
                "policy_skipped": False,
                "fail_closed": True,
            }
        )
        return base

    try:
        product = get_earn_product_info(
            client,
            coin=coin,
            category=category,
        )
    except EarnProductUnavailableError as exc:
        base.update(
            {
                "earn_execution_path_action": "terminal_skip",
                "earn_execution_path_reason": (
                    "earn_product_unavailable_terminal_skip_to_residual_cash: "
                    f"{exc}"
                ),
                "required_guard_actions": [],
                "supported_live": False,
                "policy_skipped": True,
                "fail_closed": False,
                "earn_residual_amount": str(target_amount),
            }
        )
        return base
    except Exception as exc:
        base.update(
            {
                "earn_execution_path_action": "fail_closed",
                "earn_execution_path_reason": f"earn_product_read_failed: {exc}",
                "required_guard_actions": [],
                "supported_live": False,
                "policy_skipped": False,
                "fail_closed": True,
            }
        )
        return base

    base.update(
        {
            "earn_product_id": product.product_id,
            "earn_product_category": product.category,
            "earn_product_status": product.status,
            "earn_min_stake_amount": str(product.min_stake_amount),
        }
    )

    validation = validate_earn_product_for_stake(
        product=product,
        amount=target_amount,
    )

    base.update(
        {
            "earn_stake_amount": str(validation.stake_amount),
            "earn_residual_amount": str(validation.residual_amount),
        }
    )

    if not validation.ok:
        if validation.status == ALLOCATION_LEG_STATUS_SKIPPED_EARN_UNAVAILABLE:
            base.update(
                {
                    "earn_execution_path_action": "terminal_skip",
                    "earn_execution_path_reason": (
                        "earn_validation_terminal_skip_to_residual_cash: "
                        f"{validation.error or validation.status}"
                    ),
                    "required_guard_actions": [],
                    "supported_live": False,
                    "policy_skipped": True,
                    "fail_closed": False,
                    "earn_payload_build_ok": False,
                }
            )
            return base

        base.update(
            {
                "earn_execution_path_action": "fail_closed",
                "earn_execution_path_reason": validation.error or validation.status,
                "required_guard_actions": [],
                "supported_live": False,
                "policy_skipped": False,
                "fail_closed": True,
                "earn_payload_build_ok": False,
            }
        )
        return base

    whitelist = require_live_earn_whitelisted(
        fund_code=fund_code,
        coin=product.coin,
        category=product.category,
        product_id=product.product_id,
        amount=validation.stake_amount,
    )

    if not whitelist.ok:
        base.update(
            {
                "earn_execution_path_action": "fail_closed",
                "earn_execution_path_reason": f"earn_whitelist_blocked: {whitelist.reason}",
                "required_guard_actions": [],
                "supported_live": False,
                "policy_skipped": False,
                "fail_closed": True,
                "earn_payload_build_ok": False,
            }
        )
        return base

    try:
        payload = build_earn_stake_payload(
            category=product.category,
            product_id=product.product_id,
            coin=product.coin,
            amount=validation.stake_amount,
            order_link_id=f"stage26-earn-verify-{int(leg.leg_index)}-{int(leg.id or 0)}",
            account_type="UNIFIED",
        )

        if not payload.payload.get("productId"):
            raise RuntimeError("earn payload missing productId")

    except Exception as exc:
        base.update(
            {
                "earn_execution_path_action": "fail_closed",
                "earn_execution_path_reason": f"earn_payload_build_failed: {exc}",
                "required_guard_actions": [],
                "supported_live": False,
                "policy_skipped": False,
                "fail_closed": True,
                "earn_payload_build_ok": False,
            }
        )
        return base

    base.update(
        {
            "earn_execution_path_action": "orderable",
            "earn_execution_path_reason": None,
            "required_guard_actions": ["bybit_allocation_earn_order"],
            "supported_live": True,
            "policy_skipped": False,
            "fail_closed": False,
            "earn_payload_build_ok": True,
        }
    )
    return base


def classify_plan(
    legs: list[FundAllocationLeg],
    *,
    spot_execution_client: Any | None = None,
    earn_execution_client: Any | None = None,
    fund_code: str = "wb_test",
) -> dict[str, Any]:
    supported_items = []
    skipped_items = []
    failed_items = []
    rows = []
    required_guard_actions: set[str] = set()

    earn_enabled = allocation_earn_live_enabled()

    for leg in legs:
        decision = classify_live_leg_policy(leg)
        leg_status = str(leg.status or "")

        row_fail_closed = bool(decision.fail_closed)
        row_supported_live = bool(decision.supported_live)
        row_policy_skipped = bool(decision.policy_skipped)
        row_reason = decision.reason
        row_policy_action = decision.action
        row_required_guard_actions = list(decision.required_guard_actions)

        execution_path_checked = False
        execution_path_action = None
        execution_path_reason = None
        spot_validation_status = None
        spot_min_order_amt = None
        spot_target_usdt = str(dec(leg.target_usdt))
        liquidity_checked = False
        liquidity_ok = None
        liquidity_error = None
        last_price = None
        best_bid = None
        best_ask = None
        available_liquidity_qty = None
        available_liquidity_usdt = None
        required_qty = None
        required_usdt = None
        order_payload_build_ok = False
        earn_execution_path_checked = False
        earn_execution_path_action = None
        earn_execution_path_reason = None
        earn_product_id = None
        earn_product_category = None
        earn_product_status = None
        earn_min_stake_amount = None
        earn_target_amount = None
        earn_stake_amount = None
        earn_residual_amount = None
        earn_payload_build_ok = False

        if leg_status == ALLOCATION_LEG_STATUS_SKIPPED_ZERO_VALUE:
            row_fail_closed = False
            row_supported_live = False
            row_policy_skipped = True
            row_reason = "planned_zero_value_noop"
            row_policy_action = "planned_zero_value_noop"
            row_required_guard_actions = []

        elif leg_status != ALLOCATION_LEG_STATUS_PLANNED:
            row_fail_closed = True
            row_supported_live = False
            row_policy_skipped = False
            row_reason = f"unsupported_verifier_leg_status: {leg.status}"
            row_required_guard_actions = []

        elif (
            leg.leg_type in {LEG_TYPE_USDT_EARN_STAKE, LEG_TYPE_RESIDUAL_USDT_EARN}
            and not earn_enabled
        ):
            row_fail_closed = True
            row_supported_live = False
            row_policy_skipped = False
            row_reason = "allocation_earn_live_disabled"
            row_required_guard_actions = []

        if (
            not row_fail_closed
            and row_supported_live
            and spot_execution_client is not None
            and _is_spot_execution_path_leg(leg)
        ):
            execution_check = check_spot_execution_path_readonly(
                leg,
                client=spot_execution_client,
            )

            execution_path_checked = bool(execution_check["execution_path_checked"])
            execution_path_action = execution_check["execution_path_action"]
            execution_path_reason = execution_check["execution_path_reason"]
            spot_validation_status = execution_check["spot_validation_status"]
            spot_min_order_amt = execution_check["spot_min_order_amt"]
            spot_target_usdt = execution_check["spot_target_usdt"]
            liquidity_checked = execution_check["liquidity_checked"]
            liquidity_ok = execution_check["liquidity_ok"]
            liquidity_error = execution_check["liquidity_error"]
            last_price = execution_check["last_price"]
            best_bid = execution_check["best_bid"]
            best_ask = execution_check["best_ask"]
            available_liquidity_qty = execution_check["available_liquidity_qty"]
            available_liquidity_usdt = execution_check["available_liquidity_usdt"]
            required_qty = execution_check["required_qty"]
            required_usdt = execution_check["required_usdt"]
            order_payload_build_ok = execution_check["order_payload_build_ok"]

            row_supported_live = bool(execution_check["supported_live"])
            row_policy_skipped = bool(execution_check["policy_skipped"])
            row_fail_closed = bool(execution_check["fail_closed"])

            if execution_path_reason is not None:
                row_reason = execution_path_reason

            row_required_guard_actions = list(execution_check["required_guard_actions"])

        if (
            not row_fail_closed
            and row_supported_live
            and earn_execution_client is not None
            and _is_earn_execution_path_leg(leg)
        ):
            earn_check = check_earn_execution_path_readonly(
                leg,
                client=earn_execution_client,
                fund_code=fund_code,
            )

            earn_execution_path_checked = bool(earn_check["earn_execution_path_checked"])
            earn_execution_path_action = earn_check["earn_execution_path_action"]
            earn_execution_path_reason = earn_check["earn_execution_path_reason"]
            earn_product_id = earn_check["earn_product_id"]
            earn_product_category = earn_check["earn_product_category"]
            earn_product_status = earn_check["earn_product_status"]
            earn_min_stake_amount = earn_check["earn_min_stake_amount"]
            earn_target_amount = earn_check["earn_target_amount"]
            earn_stake_amount = earn_check["earn_stake_amount"]
            earn_residual_amount = earn_check["earn_residual_amount"]
            earn_payload_build_ok = bool(earn_check["earn_payload_build_ok"])

            row_supported_live = bool(earn_check["supported_live"])
            row_policy_skipped = bool(earn_check["policy_skipped"])
            row_fail_closed = bool(earn_check["fail_closed"])

            if earn_execution_path_reason is not None:
                row_reason = earn_execution_path_reason

            row_required_guard_actions = list(earn_check["required_guard_actions"])

        if not row_fail_closed:
            required_guard_actions.update(row_required_guard_actions)

        row = {
            "leg_index": int(leg.leg_index),
            "leg_group": leg.leg_group,
            "leg_type": leg.leg_type,
            "coin": leg.coin,
            "symbol": leg.symbol,
            "category": leg.category,
            "side": leg.side,
            "location": leg.location,
            "target_usdt": decimal_to_str(dec(leg.target_usdt)),
            "target_qty": decimal_to_str(dec(leg.target_qty)) if leg.target_qty is not None else None,
            "policy_action": row_policy_action,
            "policy_reason": row_reason,
            "supported_live": row_supported_live,
            "policy_skipped": row_policy_skipped,
            "fail_closed": row_fail_closed,
            "required_guard_actions": row_required_guard_actions if not row_fail_closed else [],
            "execution_path_checked": execution_path_checked,
            "execution_path_action": execution_path_action,
            "execution_path_reason": execution_path_reason,
            "spot_validation_status": spot_validation_status,
            "spot_min_order_amt": spot_min_order_amt,
            "spot_target_usdt": spot_target_usdt,
            "liquidity_checked": liquidity_checked,
            "liquidity_ok": liquidity_ok,
            "liquidity_error": liquidity_error,
            "last_price": last_price,
            "best_bid": best_bid,
            "best_ask": best_ask,
            "available_liquidity_qty": available_liquidity_qty,
            "available_liquidity_usdt": available_liquidity_usdt,
            "required_qty": required_qty,
            "required_usdt": required_usdt,
            "order_payload_build_ok": order_payload_build_ok,
            "earn_execution_path_checked": earn_execution_path_checked,
            "earn_execution_path_action": earn_execution_path_action,
            "earn_execution_path_reason": earn_execution_path_reason,
            "earn_product_id": earn_product_id,
            "earn_product_category": earn_product_category,
            "earn_product_status": earn_product_status,
            "earn_min_stake_amount": earn_min_stake_amount,
            "earn_target_amount": earn_target_amount,
            "earn_stake_amount": earn_stake_amount,
            "earn_residual_amount": earn_residual_amount,
            "earn_payload_build_ok": earn_payload_build_ok,
        }
        rows.append(row)

        if row_fail_closed:
            failed_items.append((leg, decision, row_reason))
        elif row_policy_skipped:
            skipped_items.append((leg, decision))
        elif row_supported_live:
            supported_items.append((leg, decision))
        else:
            failed_items.append((leg, decision, "unsupported_live_policy_state"))

    return {
        "rows": rows,
        "supported_live": supported_items,
        "policy_skipped": skipped_items,
        "fail_closed": failed_items,
        "required_guard_actions": sorted(required_guard_actions),
        "ready": len(failed_items) == 0,
    }


def print_leg_rows(rows: list[dict[str, Any]]) -> None:
    for row in rows:
        parts = [
            f"leg_index={row['leg_index']}",
            f"leg_group={row['leg_group']}",
            f"leg_type={row['leg_type']}",
            f"coin={row['coin']}",
            f"symbol={row['symbol']}",
            f"category={row['category']}",
            f"side={row['side']}",
            f"location={row['location']}",
            f"target_usdt={row['target_usdt']}",
            f"target_qty={row['target_qty']}",
            f"policy_action={row['policy_action']}",
            f"policy_reason={row['policy_reason']}",
            f"supported_live={row['supported_live']}",
            f"policy_skipped={row['policy_skipped']}",
            f"fail_closed={row['fail_closed']}",
            f"required_guard_actions={row['required_guard_actions']}",
            f"execution_path_checked={row['execution_path_checked']}",
            f"execution_path_action={row['execution_path_action']}",
            f"execution_path_reason={row['execution_path_reason']}",
            f"spot_validation_status={row['spot_validation_status']}",
            f"spot_min_order_amt={row['spot_min_order_amt']}",
            f"spot_target_usdt={row['spot_target_usdt']}",
            f"liquidity_checked={row['liquidity_checked']}",
            f"liquidity_ok={row['liquidity_ok']}",
            f"liquidity_error={row['liquidity_error']}",
            f"last_price={row['last_price']}",
            f"best_bid={row['best_bid']}",
            f"best_ask={row['best_ask']}",
            f"available_liquidity_qty={row['available_liquidity_qty']}",
            f"available_liquidity_usdt={row['available_liquidity_usdt']}",
            f"required_qty={row['required_qty']}",
            f"required_usdt={row['required_usdt']}",
            f"order_payload_build_ok={row['order_payload_build_ok']}",
            f"earn_execution_path_checked={row['earn_execution_path_checked']}",
            f"earn_execution_path_action={row['earn_execution_path_action']}",
            f"earn_execution_path_reason={row['earn_execution_path_reason']}",
            f"earn_product_id={row['earn_product_id']}",
            f"earn_product_category={row['earn_product_category']}",
            f"earn_product_status={row['earn_product_status']}",
            f"earn_min_stake_amount={row['earn_min_stake_amount']}",
            f"earn_target_amount={row['earn_target_amount']}",
            f"earn_stake_amount={row['earn_stake_amount']}",
            f"earn_residual_amount={row['earn_residual_amount']}",
            f"earn_payload_build_ok={row['earn_payload_build_ok']}",
        ]
        print("PLANNED_LEG " + " ".join(parts))


def print_summary(
    *,
    fund: Fund | None,
    fund_code: str,
    positive_net_usdt: Decimal,
    snapshot: AllocationSnapshot,
    snapshot_mode: str,
    plan: dict[str, Any],
    classification: dict[str, Any],
    fixture_only: bool,
) -> None:
    legs: list[FundAllocationLeg] = plan["leg_models"]
    leg_type_counts = Counter(str(leg.leg_type) for leg in legs)
    leg_group_counts = Counter(str(leg.leg_group) for leg in legs)

    print_kv("fund_code", fund_code)
    print_kv("fund_id", int(fund.id) if fund is not None else snapshot.fund_id)
    print_kv("positive_net_usdt", positive_net_usdt)
    print_kv("snapshot_mode", snapshot_mode)
    print_kv("snapshot_source", snapshot.snapshot_source)
    print_kv("snapshot_fixture_only", fixture_only)
    print_kv("snapshot_total_equity_usdt", snapshot.total_equity_usdt)
    print_kv("base_nav_for_scale_usdt", plan["base_nav_for_scale_usdt"])
    print_kv("scale", plan["scale"])
    print_kv("raw_cash_usdt", plan["raw_cash_usdt"])
    print_kv("adjusted_cash_usdt", plan["adjusted_cash_usdt"])
    print_kv("total_planned_legs", len(legs))
    print_kv("leg_type_counts", dict(sorted(leg_type_counts.items())))
    print_kv("leg_group_counts", dict(sorted(leg_group_counts.items())))

    print_leg_rows(classification["rows"])

    print_kv("supported_live_legs", len(classification["supported_live"]))
    print_kv("policy_skipped_legs", len(classification["policy_skipped"]))
    print_kv("fail_closed_legs", len(classification["fail_closed"]))
    print_kv("required_operation_guard_action_types", classification["required_guard_actions"])
    print_kv("ready", classification["ready"])


def run_verification(args: argparse.Namespace) -> int:
    require_rollback(args)

    fund_code = normalize_fund_code(args.fund_code)
    positive_net_usdt = parse_positive_net(args.positive_net_usdt)

    print_effective_policies()
    print_kv("ROLLBACK_REQUIRED", True)
    print_kv("READ_ONLY_IN_MEMORY_PLAN", True)

    if args.fixture_mode:
        snapshot = build_fixture_snapshot()
        plan = build_plan(snapshot, positive_net_usdt=positive_net_usdt)
        classification = classify_plan(plan["leg_models"])

        print_summary(
            fund=None,
            fund_code=fund_code,
            positive_net_usdt=positive_net_usdt,
            snapshot=snapshot,
            snapshot_mode="fixture_only",
            plan=plan,
            classification=classification,
            fixture_only=True,
        )

        print_kv("ROLLBACK_COMPLETED", True)
        print_kv("FUND_ORDERS_CREATED", 0)
        print_kv("EXTERNAL_POST_CALLS", 0)
        print_kv("BSC_TX_SENT", 0)
        print(FIXTURE_ONLY_MARKER)
        return 2

    db = SessionLocal()
    no_post_client_holder: dict[str, NoPostBybitClient | None] = {"client": None}

    try:
        fund = get_fund_or_fail(db, fund_code=fund_code)
        bybit_account = get_active_bybit_account_or_fail(db, fund_id=int(fund.id))
        before_fund_orders = db.query(FundOrder).filter(FundOrder.fund_id == int(fund.id)).count()

        print_kv("fund_bybit_sub_uid_present", bool(bybit_account.bybit_sub_uid))
        print_kv("fund_bybit_api_key_active", bool(bybit_account.api_key_is_active))
        print_kv("fund_bybit_api_key_label", bybit_account.api_key_label or "")

        snapshot, snapshot_mode, fixture_only = build_snapshot(
            db,
            args=args,
            fund=fund,
            no_post_client_holder=no_post_client_holder,
        )

        if no_post_client_holder["client"] is None:
            client = get_active_fund_bybit_client(
                db,
                fund_id=int(fund.id),
                coin="USDT",
                chain_type="BSC",
            )
            no_post_client_holder["client"] = NoPostBybitClient(client)

        plan = build_plan(snapshot, positive_net_usdt=positive_net_usdt)
        classification = classify_plan(
            plan["leg_models"],
            spot_execution_client=no_post_client_holder["client"],
            earn_execution_client=no_post_client_holder["client"],
            fund_code=fund_code,
        )

        after_fund_orders = db.query(FundOrder).filter(FundOrder.fund_id == int(fund.id)).count()
        fund_orders_created = after_fund_orders - before_fund_orders
        external_post_calls = (
            no_post_client_holder["client"].post_calls
            if no_post_client_holder["client"] is not None
            else 0
        )

        print_summary(
            fund=fund,
            fund_code=fund_code,
            positive_net_usdt=positive_net_usdt,
            snapshot=snapshot,
            snapshot_mode=snapshot_mode,
            plan=plan,
            classification=classification,
            fixture_only=fixture_only,
        )

        db.rollback()

        print_kv("ROLLBACK_COMPLETED", True)
        print_kv("FUND_ORDERS_CREATED", fund_orders_created)
        print_kv("EXTERNAL_POST_CALLS", external_post_calls)
        print_kv("BSC_TX_SENT", 0)

        if fund_orders_created != 0:
            print("fund_orders_created_non_zero")
            print(NOT_READY_MARKER)
            return 1

        if external_post_calls != 0:
            print("external_post_calls_non_zero")
            print(NOT_READY_MARKER)
            return 1

        if fixture_only:
            print(FIXTURE_ONLY_MARKER)
            return 2

        if not classification["ready"]:
            print(NOT_READY_MARKER)
            return 1

        if not emit_actual_ready_markers_after_confirmation_path():
            return 1

        return 0

    except VerificationBlocked as exc:
        db.rollback()

        print_kv("ROLLBACK_COMPLETED", True)
        print_kv("FUND_ORDERS_CREATED", 0)
        print_kv("EXTERNAL_POST_CALLS", 0)
        print_kv("BSC_TX_SENT", 0)

        marker = str(exc)
        if marker == SNAPSHOT_UNAVAILABLE_MARKER:
            print(SNAPSHOT_UNAVAILABLE_MARKER)
            return 3

        print(f"VERIFICATION_BLOCKED={exc}")
        print(NOT_READY_MARKER)
        return 1

    except Exception as exc:
        db.rollback()

        print_kv("ROLLBACK_COMPLETED", True)
        print_kv("FUND_ORDERS_CREATED", 0)
        print_kv("EXTERNAL_POST_CALLS", 0)
        print_kv("BSC_TX_SENT", 0)
        print(f"VERIFICATION_ERROR={exc}")
        print(NOT_READY_MARKER)
        return 1

    finally:
        db.close()


def configure_default_policy() -> dict[str, Any]:
    original = policy_snapshot()

    settings.ALLOCATION_DERIVATIVE_LIVE_POLICY = DERIVATIVE_LIVE_POLICY_FAIL_CLOSED
    settings.ALLOCATION_BUY_THEN_STAKE_LIVE_POLICY = BUY_THEN_STAKE_LIVE_POLICY_FAIL_CLOSED
    settings.ALLOCATION_EARN_ENABLED = True
    settings.ALLOCATION_EARN_ALLOW_LIVE = True
    settings.ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST = False
    settings.ALLOCATION_EARN_ALLOWED_FUND_CODES = "wb_test"
    settings.ALLOCATION_EARN_ALLOWED_COINS = ""
    settings.ALLOCATION_EARN_ALLOWED_CATEGORIES = "FlexibleSaving"
    settings.ALLOCATION_EARN_ALLOWED_PRODUCT_IDS = ""

    return original


def configure_controlled_policy() -> dict[str, Any]:
    original = policy_snapshot()

    settings.ALLOCATION_DERIVATIVE_LIVE_POLICY = DERIVATIVE_LIVE_POLICY_SKIP_EXISTING_EXPOSURE_SCALING
    settings.ALLOCATION_BUY_THEN_STAKE_LIVE_POLICY = BUY_THEN_STAKE_LIVE_POLICY_SPOT_ONLY
    settings.ALLOCATION_EARN_ENABLED = True
    settings.ALLOCATION_EARN_ALLOW_LIVE = True
    settings.ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST = False
    settings.ALLOCATION_EARN_ALLOWED_FUND_CODES = "wb_test"
    settings.ALLOCATION_EARN_ALLOWED_COINS = ""
    settings.ALLOCATION_EARN_ALLOWED_CATEGORIES = "FlexibleSaving"
    settings.ALLOCATION_EARN_ALLOWED_PRODUCT_IDS = ""

    return original


def test_stage26_2_8d_live_spot_terminal_skip_service_result() -> None:
    info = InstrumentInfo(
        symbol="LDOUSDT",
        category="spot",
        status="Trading",
        base_coin="LDO",
        quote_coin="USDT",
        tick_size=Decimal("0.0001"),
        qty_step=Decimal("0.000001"),
        min_order_qty=Decimal("0.000001"),
        max_order_qty=Decimal("100000000"),
        min_order_amt=Decimal("1"),
        max_market_order_qty=Decimal("100000000"),
        max_limit_order_qty=Decimal("100000000"),
        raw={},
    )
    leg = FundAllocationLeg(
        id=1,
        allocation_batch_id=10,
        settlement_batch_id=20,
        fund_id=1,
        leg_index=1,
        leg_key="stage26_2_8d_ldo_min_order",
        leg_group="spot",
        leg_type=LEG_TYPE_SPOT_BUY,
        coin="LDO",
        symbol="LDOUSDT",
        category="spot",
        side="Buy",
        location="FUND",
        target_usdt=Decimal("0.0005"),
        target_qty=Decimal("0.0005"),
        status=ALLOCATION_LEG_STATUS_PLANNED,
        execution_mode="planned",
    )

    validation = validate_instrument_for_leg(leg, info)
    result = apply_spot_validation_terminal_skip_to_leg(
        leg,
        validation=validation,
        info=info,
    )

    assert_ok("STAGE26_2_8D_SERVICE_VALIDATION_IS_MIN_ORDER", validation.status == ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER)
    assert_ok("STAGE26_2_8D_SERVICE_TERMINAL_SKIP_STATUS", leg.status == ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER)
    assert_ok("STAGE26_2_8D_SERVICE_TERMINAL_SKIP_MODE", leg.execution_mode == "skipped")
    assert_ok("STAGE26_2_8D_SERVICE_TERMINAL_SKIP_RESIDUAL", dec(leg.residual_usdt) == Decimal("0.0005"))
    assert_ok("STAGE26_2_8D_SERVICE_TERMINAL_SKIP_NO_ORDER_LINK", leg.order_link_id is None)
    assert_ok("STAGE26_2_8D_SERVICE_TERMINAL_SKIP_RESULT_OK", result.ok is True and result.action == "terminal_validation_skip")


def test_stage26_2_8c_position_endpoint_category_tagging() -> None:
    linear_rows = _tag_position_rows(
        [
            {
                "symbol": "ETHUSDT",
                "baseCoin": "ETH",
                "side": "Buy",
                "size": "1",
                "positionValue": "100",
            }
        ],
        category="linear",
    )
    linear_holding = _holding_from_position(linear_rows[0])

    assert_ok(
        "STAGE26_2_8C_LINEAR_POSITION_WITHOUT_ROW_CATEGORY_TAGGED_AS_PERP",
        linear_holding is not None and linear_holding.leg_group == "perp",
    )

    short_option_rows = _tag_position_rows(
        [
            {
                "symbol": "BTC-25SEP26-80000-C-USDT",
                "baseCoin": "BTC",
                "side": "Sell",
                "size": "1",
                "positionValue": "10",
            }
        ],
        category="option",
    )
    short_option_holding = _holding_from_position(short_option_rows[0])

    assert_ok(
        "STAGE26_2_8C_OPTION_SELL_WITHOUT_ROW_CATEGORY_TAGGED_AS_SHORT_OPTION",
        short_option_holding is not None and short_option_holding.leg_group == "short_option",
    )

    long_option_rows = _tag_position_rows(
        [
            {
                "symbol": "ETH-25SEP26-2200-C-USDT",
                "baseCoin": "ETH",
                "side": "Buy",
                "size": "1",
                "positionValue": "10",
            }
        ],
        category="option",
    )
    long_option_holding = _holding_from_position(long_option_rows[0])

    assert_ok(
        "STAGE26_2_8C_OPTION_BUY_WITHOUT_ROW_CATEGORY_TAGGED_AS_LONG_OPTION",
        long_option_holding is not None and long_option_holding.leg_group == "long_option",
    )


def test_stage26_2_8c_real_production_blockers_regression() -> None:
    snapshot = build_stage26_2_8c_production_blocker_snapshot()
    plan = build_plan(snapshot, positive_net_usdt=Decimal("10"))

    original = configure_controlled_policy()
    try:
        result = classify_plan(plan["leg_models"])
    finally:
        restore_policy(original)

    rows = result["rows"]

    funding_ldo_rows = [
        row
        for row in rows
        if row["coin"] == "LDO"
        and row["symbol"] == "LDOUSDT"
        and row["location"] == "FUND"
    ]

    assert_ok(
        "STAGE26_2_8C_FUNDING_LDO_ROW_PRESENT",
        len(funding_ldo_rows) == 1,
    )
    assert_ok(
        "STAGE26_2_8C_FUNDING_LDO_NOT_OTHER",
        funding_ldo_rows[0]["leg_type"] != LEG_TYPE_OTHER,
    )
    assert_ok(
        "STAGE26_2_8C_FUNDING_LDO_SCALED_AS_SPOT_BUY",
        funding_ldo_rows[0]["leg_type"] == LEG_TYPE_SPOT_BUY,
    )

    zero_value_rows = [
        row
        for row in rows
        if row["policy_reason"] == "planned_zero_value_noop"
    ]

    assert_ok(
        "STAGE26_2_8C_ZERO_VALUE_ROWS_PRESENT",
        len(zero_value_rows) >= 6,
    )
    assert_ok(
        "STAGE26_2_8C_ZERO_VALUE_ROWS_SAFE_NOOP",
        all(
            row["policy_skipped"] is True
            and row["supported_live"] is False
            and row["fail_closed"] is False
            and row["required_guard_actions"] == []
            for row in zero_value_rows
        ),
    )

    assert_ok("STAGE26_2_8C_PRODUCTION_BLOCKER_FIXTURE_READY", result["ready"] is True)
    assert_ok("STAGE26_2_8C_PRODUCTION_BLOCKER_FAIL_CLOSED_ZERO", len(result["fail_closed"]) == 0)

    assert_ok(
        "STAGE26_2_8C_REQUIRED_GUARDS_TRADE_EARN_ONLY",
        "bybit_allocation_trade_order" in result["required_guard_actions"]
        and "bybit_allocation_earn_order" in result["required_guard_actions"]
        and "bybit_allocation_strategy_order" not in result["required_guard_actions"],
    )

    print("STAGE26_2_8C_REAL_PRODUCTION_BLOCKERS_REGRESSION_OK")


def _make_stage26_2_8f_earn_leg(
    *,
    target_usdt: Decimal = Decimal("0.9"),
    coin: str = "USDT",
) -> FundAllocationLeg:
    return FundAllocationLeg(
        id=1,
        allocation_batch_id=10,
        settlement_batch_id=20,
        fund_id=1,
        leg_index=1,
        leg_key="stage26_2_8f_earn",
        leg_group="earn",
        leg_type=LEG_TYPE_USDT_EARN_STAKE,
        coin=coin,
        symbol=None,
        category="earn",
        side=None,
        location="EARN",
        target_usdt=target_usdt,
        target_qty=target_usdt,
        status=ALLOCATION_LEG_STATUS_PLANNED,
        execution_mode="planned",
    )


def test_stage26_2_8f_earn_execution_path_regression() -> None:
    below_min_leg = _make_stage26_2_8f_earn_leg(target_usdt=Decimal("0.9"))
    below_min_client = Stage26SpotValidationFakeClient(
        earn_min_stake_by_coin={"USDT": "1"},
        earn_product_status_by_coin={"USDT": "Available"},
    )
    below_min = check_earn_execution_path_readonly(
        below_min_leg,
        client=below_min_client,
        fund_code="wb_test",
    )

    assert_ok("STAGE26_2_8F_BELOW_MIN_EARN_TERMINAL_SKIP", below_min["earn_execution_path_action"] == "terminal_skip")
    assert_ok("STAGE26_2_8F_BELOW_MIN_EARN_NO_GUARD", below_min["required_guard_actions"] == [])
    assert_ok("STAGE26_2_8F_BELOW_MIN_EARN_NOT_FAIL_CLOSED", below_min["fail_closed"] is False)

    orderable_leg = _make_stage26_2_8f_earn_leg(target_usdt=Decimal("0.9"))
    orderable_client = Stage26SpotValidationFakeClient(
        earn_min_stake_by_coin={"USDT": "0.1"},
        earn_product_status_by_coin={"USDT": "Available"},
    )
    original = configure_controlled_policy()
    try:
        orderable = check_earn_execution_path_readonly(
            orderable_leg,
            client=orderable_client,
            fund_code="wb_test",
        )
    finally:
        restore_policy(original)

    assert_ok("STAGE26_2_8F_ORDERABLE_EARN_FULL_PATH_ORDERABLE", orderable["earn_execution_path_action"] == "orderable")
    assert_ok("STAGE26_2_8F_ORDERABLE_EARN_PAYLOAD_OK", orderable["earn_payload_build_ok"] is True)
    assert_ok("STAGE26_2_8F_ORDERABLE_EARN_GUARD_REQUIRED", orderable["required_guard_actions"] == ["bybit_allocation_earn_order"])

    unavailable_leg = _make_stage26_2_8f_earn_leg(target_usdt=Decimal("0.9"))
    unavailable_client = Stage26SpotValidationFakeClient(
        earn_min_stake_by_coin={"USDT": "0.1"},
        earn_product_status_by_coin={"USDT": "Unavailable"},
    )
    unavailable = check_earn_execution_path_readonly(
        unavailable_leg,
        client=unavailable_client,
        fund_code="wb_test",
    )

    assert_ok("STAGE26_2_8F_UNAVAILABLE_EARN_TERMINAL_SKIP", unavailable["earn_execution_path_action"] == "terminal_skip")
    assert_ok("STAGE26_2_8F_UNAVAILABLE_EARN_NO_GUARD", unavailable["required_guard_actions"] == [])
    assert_ok("STAGE26_2_8F_UNAVAILABLE_EARN_NOT_FAIL_CLOSED", unavailable["fail_closed"] is False)

    uncertain_leg = _make_stage26_2_8f_earn_leg(target_usdt=Decimal("0.9"))
    uncertain_client = Stage26SpotValidationFakeClient(
        earn_error_by_coin={"USDT": "earn api timeout uncertain"},
    )
    uncertain = check_earn_execution_path_readonly(
        uncertain_leg,
        client=uncertain_client,
        fund_code="wb_test",
    )

    assert_ok("STAGE26_2_8F_UNCERTAIN_EARN_ERROR_FAIL_CLOSED", uncertain["earn_execution_path_action"] == "fail_closed")
    assert_ok("STAGE26_2_8F_UNCERTAIN_EARN_ERROR_NOT_READY", uncertain["fail_closed"] is True)

    whitelist_leg = _make_stage26_2_8f_earn_leg(target_usdt=Decimal("0.9"))
    whitelist_client = Stage26SpotValidationFakeClient(
        earn_min_stake_by_coin={"USDT": "0.1"},
        earn_product_id_by_coin={"USDT": "USDT-FlexibleSaving-001"},
    )

    original = policy_snapshot()
    try:
        settings.ALLOCATION_EARN_ENABLED = True
        settings.ALLOCATION_EARN_ALLOW_LIVE = True
        settings.ALLOCATION_EARN_REQUIRE_PRODUCT_ID_WHITELIST = True
        settings.ALLOCATION_EARN_ALLOWED_FUND_CODES = "wb_test"
        settings.ALLOCATION_EARN_ALLOWED_COINS = ""
        settings.ALLOCATION_EARN_ALLOWED_CATEGORIES = "FlexibleSaving"
        settings.ALLOCATION_EARN_ALLOWED_PRODUCT_IDS = "OTHER-PRODUCT"

        whitelist = check_earn_execution_path_readonly(
            whitelist_leg,
            client=whitelist_client,
            fund_code="wb_test",
        )
    finally:
        restore_policy(original)

    assert_ok("STAGE26_2_8F_WHITELIST_BLOCKED_FAIL_CLOSED", whitelist["earn_execution_path_action"] == "fail_closed")
    assert_ok("STAGE26_2_8F_WHITELIST_BLOCKED_NOT_READY", whitelist["fail_closed"] is True)
    assert_ok(
        "STAGE26_2_8F_FAKE_CLIENT_NO_POST",
        below_min_client.post_calls == 0
        and orderable_client.post_calls == 0
        and unavailable_client.post_calls == 0
        and uncertain_client.post_calls == 0
        and whitelist_client.post_calls == 0,
    )

    print("STAGE26_2_8F_EARN_EXECUTION_PATH_REGRESSION_OK")


def test_stage26_2_8f_production_verifier_full_earn_path() -> None:
    orderable_leg = _make_stage26_2_8f_earn_leg(target_usdt=Decimal("0.9"))
    orderable_client = Stage26SpotValidationFakeClient(
        earn_min_stake_by_coin={"USDT": "0.1"},
        earn_product_status_by_coin={"USDT": "Available"},
    )

    original = configure_controlled_policy()
    try:
        orderable_result = classify_plan(
            [orderable_leg],
            earn_execution_client=orderable_client,
            fund_code="wb_test",
        )
    finally:
        restore_policy(original)

    orderable_row = orderable_result["rows"][0]
    assert_ok("STAGE26_2_8F_CLASSIFY_ORDERABLE_EARN_READY", orderable_result["ready"] is True)
    assert_ok("STAGE26_2_8F_CLASSIFY_ORDERABLE_EARN_PATH_CHECKED", orderable_row["earn_execution_path_checked"] is True)
    assert_ok("STAGE26_2_8F_CLASSIFY_ORDERABLE_EARN_PAYLOAD_OK", orderable_row["earn_payload_build_ok"] is True)
    assert_ok("STAGE26_2_8F_CLASSIFY_ORDERABLE_EARN_GUARD", "bybit_allocation_earn_order" in orderable_result["required_guard_actions"])

    below_min_leg = _make_stage26_2_8f_earn_leg(target_usdt=Decimal("0.9"))
    below_min_client = Stage26SpotValidationFakeClient(
        earn_min_stake_by_coin={"USDT": "1"},
        earn_product_status_by_coin={"USDT": "Available"},
    )

    original = configure_controlled_policy()
    try:
        below_min_result = classify_plan(
            [below_min_leg],
            earn_execution_client=below_min_client,
            fund_code="wb_test",
        )
    finally:
        restore_policy(original)

    below_min_row = below_min_result["rows"][0]
    assert_ok("STAGE26_2_8F_CLASSIFY_BELOW_MIN_EARN_READY", below_min_result["ready"] is True)
    assert_ok("STAGE26_2_8F_CLASSIFY_BELOW_MIN_EARN_TERMINAL_SKIP", below_min_row["earn_execution_path_action"] == "terminal_skip")
    assert_ok("STAGE26_2_8F_CLASSIFY_BELOW_MIN_EARN_NO_GUARD", below_min_row["required_guard_actions"] == [])

    uncertain_leg = _make_stage26_2_8f_earn_leg(target_usdt=Decimal("0.9"))
    uncertain_client = Stage26SpotValidationFakeClient(
        earn_error_by_coin={"USDT": "earn api timeout uncertain"},
    )

    original = configure_controlled_policy()
    try:
        uncertain_result = classify_plan(
            [uncertain_leg],
            earn_execution_client=uncertain_client,
            fund_code="wb_test",
        )
    finally:
        restore_policy(original)

    uncertain_row = uncertain_result["rows"][0]
    assert_ok("STAGE26_2_8F_CLASSIFY_UNCERTAIN_EARN_NOT_READY", uncertain_result["ready"] is False)
    assert_ok("STAGE26_2_8F_CLASSIFY_UNCERTAIN_EARN_FAIL_CLOSED", uncertain_row["fail_closed"] is True)

    print("STAGE26_2_8F_PRODUCTION_VERIFIER_FULL_EARN_PATH_OK")


def _make_stage26_2_8e_spot_leg(
    *,
    symbol: str,
    target_usdt: Decimal = Decimal("10"),
    target_qty: Decimal = Decimal("10"),
) -> FundAllocationLeg:
    return FundAllocationLeg(
        id=1,
        allocation_batch_id=10,
        settlement_batch_id=20,
        fund_id=1,
        leg_index=1,
        leg_key=f"stage26_2_8e_{symbol.lower()}",
        leg_group="spot",
        leg_type=LEG_TYPE_SPOT_BUY,
        coin=symbol.removesuffix("USDT"),
        symbol=symbol,
        category="spot",
        side="Buy",
        location="UNIFIED",
        target_usdt=target_usdt,
        target_qty=target_qty,
        status=ALLOCATION_LEG_STATUS_PLANNED,
        execution_mode="planned",
    )


def test_stage26_2_8e_spot_liquidity_execution_path_regression() -> None:
    orderable_leg = _make_stage26_2_8e_spot_leg(symbol="OKUSDT")
    orderable_client = Stage26SpotValidationFakeClient(
        min_order_amt_by_symbol={"OKUSDT": "1"},
        orderbook_mode_by_symbol={"OKUSDT": "sufficient"},
    )
    orderable = check_spot_execution_path_readonly(
        orderable_leg,
        client=orderable_client,
    )

    assert_ok("STAGE26_2_8E_ORDERABLE_SPOT_FULL_PATH_ORDERABLE", orderable["execution_path_action"] == "orderable")
    assert_ok("STAGE26_2_8E_ORDERABLE_SPOT_LIQUIDITY_OK", orderable["liquidity_ok"] is True)
    assert_ok("STAGE26_2_8E_ORDERABLE_SPOT_PAYLOAD_OK", orderable["order_payload_build_ok"] is True)
    assert_ok("STAGE26_2_8E_ORDERABLE_SPOT_TRADE_GUARD_REQUIRED", orderable["required_guard_actions"] == ["bybit_allocation_trade_order"])

    empty_leg = _make_stage26_2_8e_spot_leg(symbol="EMPTYUSDT")
    empty_client = Stage26SpotValidationFakeClient(
        min_order_amt_by_symbol={"EMPTYUSDT": "1"},
        orderbook_mode_by_symbol={"EMPTYUSDT": "empty"},
    )
    empty = check_spot_execution_path_readonly(
        empty_leg,
        client=empty_client,
    )

    assert_ok("STAGE26_2_8E_EMPTY_ORDERBOOK_FAIL_CLOSED", empty["execution_path_action"] == "fail_closed")
    assert_ok("STAGE26_2_8E_EMPTY_ORDERBOOK_NOT_READY", empty["fail_closed"] is True)
    assert_ok("STAGE26_2_8E_EMPTY_ORDERBOOK_NO_PAYLOAD", empty["order_payload_build_ok"] is False)

    insufficient_leg = _make_stage26_2_8e_spot_leg(symbol="THINUSDT")
    insufficient_client = Stage26SpotValidationFakeClient(
        min_order_amt_by_symbol={"THINUSDT": "1"},
        orderbook_mode_by_symbol={"THINUSDT": "insufficient"},
    )
    insufficient = check_spot_execution_path_readonly(
        insufficient_leg,
        client=insufficient_client,
    )

    assert_ok("STAGE26_2_8E_INSUFFICIENT_LIQUIDITY_FAIL_CLOSED", insufficient["execution_path_action"] == "fail_closed")
    assert_ok("STAGE26_2_8E_INSUFFICIENT_LIQUIDITY_NOT_READY", insufficient["fail_closed"] is True)
    assert_ok("STAGE26_2_8E_INSUFFICIENT_LIQUIDITY_NO_PAYLOAD", insufficient["order_payload_build_ok"] is False)

    below_min_leg = _make_stage26_2_8e_spot_leg(
        symbol="MINUSDT",
        target_usdt=Decimal("0.01"),
        target_qty=Decimal("0.01"),
    )
    below_min_client = Stage26SpotValidationFakeClient(
        min_order_amt_by_symbol={"MINUSDT": "1"},
        orderbook_mode_by_symbol={"MINUSDT": "sufficient"},
    )
    below_min = check_spot_execution_path_readonly(
        below_min_leg,
        client=below_min_client,
    )

    assert_ok("STAGE26_2_8E_BELOW_MIN_TERMINAL_SKIP", below_min["execution_path_action"] == "terminal_skip")
    assert_ok("STAGE26_2_8E_BELOW_MIN_NO_GUARD", below_min["required_guard_actions"] == [])
    assert_ok("STAGE26_2_8E_BELOW_MIN_NOT_FAIL_CLOSED", below_min["fail_closed"] is False)
    assert_ok("STAGE26_2_8E_FAKE_CLIENT_NO_POST", orderable_client.post_calls == 0 and empty_client.post_calls == 0 and insufficient_client.post_calls == 0 and below_min_client.post_calls == 0)

    print("STAGE26_2_8E_SPOT_LIQUIDITY_EXECUTION_PATH_REGRESSION_OK")


def test_stage26_2_8e_production_verifier_full_spot_path() -> None:
    ready_leg = _make_stage26_2_8e_spot_leg(symbol="READYUSDT")
    ready_client = Stage26SpotValidationFakeClient(
        min_order_amt_by_symbol={"READYUSDT": "1"},
        orderbook_mode_by_symbol={"READYUSDT": "sufficient"},
    )

    original = configure_controlled_policy()
    try:
        ready_result = classify_plan(
            [ready_leg],
            spot_execution_client=ready_client,
        )
    finally:
        restore_policy(original)

    ready_row = ready_result["rows"][0]
    assert_ok("STAGE26_2_8E_CLASSIFY_READY_ORDERABLE", ready_result["ready"] is True)
    assert_ok("STAGE26_2_8E_CLASSIFY_READY_FULL_PATH_CHECKED", ready_row["execution_path_checked"] is True and ready_row["liquidity_checked"] is True)
    assert_ok("STAGE26_2_8E_CLASSIFY_READY_PAYLOAD_OK", ready_row["order_payload_build_ok"] is True)

    empty_leg = _make_stage26_2_8e_spot_leg(symbol="EMPTYREADYUSDT")
    empty_client = Stage26SpotValidationFakeClient(
        min_order_amt_by_symbol={"EMPTYREADYUSDT": "1"},
        orderbook_mode_by_symbol={"EMPTYREADYUSDT": "empty"},
    )

    original = configure_controlled_policy()
    try:
        empty_result = classify_plan(
            [empty_leg],
            spot_execution_client=empty_client,
        )
    finally:
        restore_policy(original)

    empty_row = empty_result["rows"][0]
    assert_ok("STAGE26_2_8E_CLASSIFY_EMPTY_ORDERBOOK_NOT_READY", empty_result["ready"] is False)
    assert_ok("STAGE26_2_8E_CLASSIFY_EMPTY_ORDERBOOK_FAIL_CLOSED", empty_row["fail_closed"] is True)
    assert_ok("STAGE26_2_8E_CLASSIFY_EMPTY_ORDERBOOK_NO_PAYLOAD", empty_row["order_payload_build_ok"] is False)

    insufficient_leg = _make_stage26_2_8e_spot_leg(symbol="THINREADYUSDT")
    insufficient_client = Stage26SpotValidationFakeClient(
        min_order_amt_by_symbol={"THINREADYUSDT": "1"},
        orderbook_mode_by_symbol={"THINREADYUSDT": "insufficient"},
    )

    original = configure_controlled_policy()
    try:
        insufficient_result = classify_plan(
            [insufficient_leg],
            spot_execution_client=insufficient_client,
        )
    finally:
        restore_policy(original)

    insufficient_row = insufficient_result["rows"][0]
    assert_ok("STAGE26_2_8E_CLASSIFY_INSUFFICIENT_LIQUIDITY_NOT_READY", insufficient_result["ready"] is False)
    assert_ok("STAGE26_2_8E_CLASSIFY_INSUFFICIENT_LIQUIDITY_FAIL_CLOSED", insufficient_row["fail_closed"] is True)
    assert_ok("STAGE26_2_8E_CLASSIFY_INSUFFICIENT_LIQUIDITY_NO_PAYLOAD", insufficient_row["order_payload_build_ok"] is False)

    print("STAGE26_2_8E_PRODUCTION_VERIFIER_FULL_SPOT_PATH_OK")


def test_stage26_2_8d_production_verifier_execution_path_check() -> None:
    snapshot = build_stage26_2_8c_production_blocker_snapshot()

    for holding in snapshot.holdings:
        if (
            holding.leg_group == "funding_wallet"
            and holding.coin == "LDO"
            and holding.symbol == "LDOUSDT"
        ):
            object.__setattr__(holding, "usd_value", Decimal("0.03"))
            object.__setattr__(holding, "size", Decimal("0.03"))

    plan = build_plan(snapshot, positive_net_usdt=Decimal("10"))
    fake_client = Stage26SpotValidationFakeClient(
        min_order_amt_by_symbol={
            "LDOUSDT": "1",
            "BTCUSDT": "0.000001",
        }
    )

    original = configure_controlled_policy()
    try:
        result = classify_plan(
            plan["leg_models"],
            spot_execution_client=fake_client,
        )
    finally:
        restore_policy(original)

    rows = result["rows"]
    ldo_rows = [
        row
        for row in rows
        if row["coin"] == "LDO"
        and row["symbol"] == "LDOUSDT"
        and row["location"] == "FUND"
    ]

    assert_ok("STAGE26_2_8D_TINY_FUNDING_LDO_ROW_PRESENT", len(ldo_rows) == 1)
    assert_ok("STAGE26_2_8D_TINY_FUNDING_LDO_IS_SPOT_BUY", ldo_rows[0]["leg_type"] == LEG_TYPE_SPOT_BUY)
    assert_ok("STAGE26_2_8D_TINY_FUNDING_LDO_EXEC_PATH_CHECKED", ldo_rows[0]["execution_path_checked"] is True)
    assert_ok("STAGE26_2_8D_TINY_FUNDING_LDO_TERMINAL_SKIP", ldo_rows[0]["execution_path_action"] == "terminal_skip")
    assert_ok("STAGE26_2_8D_TINY_FUNDING_LDO_MIN_ORDER_STATUS", ldo_rows[0]["spot_validation_status"] == ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER)
    assert_ok("STAGE26_2_8D_TINY_FUNDING_LDO_NO_GUARD_REQUIRED", ldo_rows[0]["required_guard_actions"] == [])
    assert_ok("STAGE26_2_8D_TINY_FUNDING_LDO_NOT_FAIL_CLOSED", ldo_rows[0]["fail_closed"] is False)

    assert_ok("STAGE26_2_8D_VERIFIER_RESULT_READY", result["ready"] is True)
    assert_ok("STAGE26_2_8D_VERIFIER_FAIL_CLOSED_ZERO", len(result["fail_closed"]) == 0)
    assert_ok("STAGE26_2_8D_FAKE_CLIENT_NO_POST", fake_client.post_calls == 0)

    print("STAGE26_2_8D_SPOT_MIN_ORDER_TERMINAL_SKIP_REGRESSION_OK")
    print("STAGE26_2_8D_PRODUCTION_VERIFIER_EXECUTION_PATH_CHECK_OK")


def test_stage26_2_12_confirmation_path_actual_run_marker(source: str) -> None:
    helper_start = source.index("def emit_actual_ready_markers_after_confirmation_path")
    helper_end = source.index("def print_kv")
    helper_source = source[helper_start:helper_end]

    verifier_start = source.index("def run_verification")
    verifier_end = source.index("def configure_default_policy")
    verifier_source = source[verifier_start:verifier_end]

    assert_ok(
        "STAGE26_2_12_PRODUCTION_VERIFIER_CONFIRMATION_PATH_ACTUAL_RUN_VALIDATES_BEFORE_READY",
        "verify_settlement_transfer_confirmation_path()" in helper_source
        and "print(READY_MARKER)" in helper_source
        and helper_source.index("verify_settlement_transfer_confirmation_path()")
        < helper_source.index("print(READY_MARKER)")
        and "emit_actual_ready_markers_after_confirmation_path()" in verifier_source,
    )

    ok_buffer = io.StringIO()
    with contextlib.redirect_stdout(ok_buffer):
        ok = emit_actual_ready_markers_after_confirmation_path(
            confirmation_path_result={
                "ok": True,
                "checks": {"forced_ok": True},
                "reason": None,
            },
            buy_collection_continuation_result={
                "ok": True,
                "checks": {"forced_ok": True},
                "reason": None,
            },
            bybit_lower_limit_result={
                "ok": True,
                "checks": {"forced_ok": True},
                "reason": None,
            },
        )

    ok_output = ok_buffer.getvalue()
    assert_ok(
        CONFIRMATION_PATH_ACTUAL_RUN_MARKER,
        ok is True
        and CONFIRMATION_PATH_MARKER in ok_output
        and READY_MARKER in ok_output
        and ok_output.index(CONFIRMATION_PATH_MARKER) < ok_output.index(READY_MARKER)
        and BUY_COLLECTION_CONTINUATION_PATH_MARKER in ok_output
        and ok_output.index(BUY_COLLECTION_CONTINUATION_PATH_MARKER) < ok_output.index(READY_MARKER)
        and BYBIT_LOWER_LIMIT_REJECT_HANDLING_MARKER in ok_output
        and ok_output.index(BYBIT_LOWER_LIMIT_REJECT_HANDLING_MARKER) < ok_output.index(READY_MARKER),
    )

    fail_buffer = io.StringIO()
    with contextlib.redirect_stdout(fail_buffer):
        failed_ok = emit_actual_ready_markers_after_confirmation_path(
            confirmation_path_result={
                "ok": False,
                "checks": {"forced_failure": False},
                "reason": "forced_failure",
            }
        )

    fail_output = fail_buffer.getvalue()
    assert_ok(
        "STAGE26_2_12_PRODUCTION_VERIFIER_CONFIRMATION_PATH_ACTUAL_RUN_FAIL_CLOSED_OK",
        failed_ok is False
        and "confirmation_path_verification_failed: forced_failure" in fail_output
        and NOT_READY_MARKER in fail_output
        and READY_MARKER not in fail_output
        and CONFIRMATION_PATH_MARKER not in fail_output,
    )

    continuation_fail_buffer = io.StringIO()
    with contextlib.redirect_stdout(continuation_fail_buffer):
        continuation_failed_ok = emit_actual_ready_markers_after_confirmation_path(
            confirmation_path_result={
                "ok": True,
                "checks": {"forced_ok": True},
                "reason": None,
            },
            buy_collection_continuation_result={
                "ok": False,
                "checks": {"forced_failure": False},
                "reason": "forced_continuation_failure",
            },
        )

    continuation_fail_output = continuation_fail_buffer.getvalue()
    assert_ok(
        "STAGE26_2_13_PRODUCTION_VERIFIER_CONTINUATION_PATH_ACTUAL_RUN_FAIL_CLOSED_OK",
        continuation_failed_ok is False
        and "buy_collection_continuation_path_verification_failed: forced_continuation_failure" in continuation_fail_output
        and NOT_READY_MARKER in continuation_fail_output
        and READY_MARKER not in continuation_fail_output
        and BUY_COLLECTION_CONTINUATION_PATH_MARKER not in continuation_fail_output,
    )

    lower_limit_fail_buffer = io.StringIO()
    with contextlib.redirect_stdout(lower_limit_fail_buffer):
        lower_limit_failed_ok = emit_actual_ready_markers_after_confirmation_path(
            confirmation_path_result={
                "ok": True,
                "checks": {"forced_ok": True},
                "reason": None,
            },
            buy_collection_continuation_result={
                "ok": True,
                "checks": {"forced_ok": True},
                "reason": None,
            },
            bybit_lower_limit_result={
                "ok": False,
                "checks": {"forced_failure": False},
                "reason": "forced_lower_limit_failure",
            },
        )

    lower_limit_fail_output = lower_limit_fail_buffer.getvalue()
    assert_ok(
        "STAGE26_2_14_PRODUCTION_VERIFIER_LOWER_LIMIT_PATH_ACTUAL_RUN_FAIL_CLOSED_OK",
        lower_limit_failed_ok is False
        and "bybit_lower_limit_reject_handling_verification_failed: forced_lower_limit_failure" in lower_limit_fail_output
        and NOT_READY_MARKER in lower_limit_fail_output
        and READY_MARKER not in lower_limit_fail_output
        and BYBIT_LOWER_LIMIT_REJECT_HANDLING_MARKER not in lower_limit_fail_output,
    )

    fixture_args = argparse.Namespace(
        fund_code="wb_test",
        positive_net_usdt="10",
        snapshot_json=None,
        rollback=True,
        fixture_mode=True,
        self_test=False,
    )

    fixture_buffer = io.StringIO()
    with contextlib.redirect_stdout(fixture_buffer):
        fixture_rc = run_verification(fixture_args)

    fixture_output = fixture_buffer.getvalue()
    assert_ok(
        "STAGE26_2_12_PRODUCTION_VERIFIER_CONFIRMATION_PATH_FIXTURE_NO_MARKER_OK",
        fixture_rc == 2
        and FIXTURE_ONLY_MARKER in fixture_output
        and CONFIRMATION_PATH_MARKER not in fixture_output
        and READY_MARKER not in fixture_output
        and BUY_COLLECTION_CONTINUATION_PATH_MARKER not in fixture_output,
    )


def run_self_test() -> int:
    script_path = "scripts/stage26_2_8_verify_production_wb_test_actual_path.py"
    source = read(script_path)
    imports = imported_names(script_path)

    snapshot = build_fixture_snapshot()
    positive_net_usdt = Decimal("10")
    plan = build_plan(snapshot, positive_net_usdt=positive_net_usdt)

    original = configure_default_policy()
    try:
        default_result = classify_plan(plan["leg_models"])
    finally:
        restore_policy(original)

    original = configure_controlled_policy()
    try:
        controlled_result = classify_plan(plan["leg_models"])
    finally:
        restore_policy(original)

    assert_ok("SELFTEST_FIXTURE_UNIT_PLAN_HAS_LEGS", len(plan["leg_models"]) > 0)
    assert_ok("SELFTEST_FIXTURE_FINAL_OK_BLOCKED", FIXTURE_ONLY_MARKER in source and "args.fixture_mode" in source)
    assert_ok("SELFTEST_DEFAULT_POLICY_NOT_READY", default_result["ready"] is False)
    assert_ok("SELFTEST_CONTROLLED_POLICY_READY", controlled_result["ready"] is True)
    assert_ok("SELFTEST_CONTROLLED_SUPPORTED_OR_SKIPPED", len(controlled_result["fail_closed"]) == 0)

    assert_ok(
        "SELFTEST_REQUIRED_GUARDS_TRADE_EARN",
        "bybit_allocation_trade_order" in controlled_result["required_guard_actions"]
        and "bybit_allocation_earn_order" in controlled_result["required_guard_actions"],
    )
    assert_ok(
        "SELFTEST_REQUIRED_GUARDS_NO_STRATEGY",
        "bybit_allocation_strategy_order" not in controlled_result["required_guard_actions"],
    )
    calls = ast_call_names(script_path)

    confirmation_path = verify_settlement_transfer_confirmation_path(emit_assert_ok=True)
    assert_ok(
        "STAGE26_2_12_VERIFIER_CONFIRMATION_PATH_SHARED_OK",
        confirmation_path["ok"] is True,
    )

    continuation_path = verify_buy_collection_continuation_path(emit_assert_ok=True)
    assert_ok(
        "STAGE26_2_13_VERIFIER_CONTINUATION_PATH_SHARED_OK",
        continuation_path["ok"] is True,
    )

    lower_limit_path = verify_bybit_lower_limit_reject_handling_path(emit_assert_ok=True)
    assert_ok(
        "STAGE26_2_14_VERIFIER_LOWER_LIMIT_PATH_SHARED_OK",
        lower_limit_path["ok"] is True,
    )
    test_stage26_2_12_confirmation_path_actual_run_marker(source)

    assert_ok("SELFTEST_SOURCE_NO_CLIENT_POST_CALL", "post" not in calls)
    assert_ok("SELFTEST_SOURCE_NO_FUND_ORDER_CREATE", "FundOrder" not in calls)
    assert_ok("SELFTEST_SOURCE_NO_BSC_SEND", "send_raw_transaction" not in calls)
    assert_ok("SELFTEST_SOURCE_NO_LIFECYCLE_IMPORT", "app.lifecycle" not in imports and "workers" not in imports)
    forbidden_freeze_endpoint = "frozen" + "-sub-member"
    assert_ok("SELFTEST_SOURCE_NO_FREEZE_GUARD", forbidden_freeze_endpoint not in source)

    test_stage26_2_8d_live_spot_terminal_skip_service_result()
    test_stage26_2_8c_position_endpoint_category_tagging()
    test_stage26_2_8c_real_production_blockers_regression()
    test_stage26_2_8d_production_verifier_execution_path_check()
    test_stage26_2_8e_spot_liquidity_execution_path_regression()
    test_stage26_2_8e_production_verifier_full_spot_path()
    test_stage26_2_8f_earn_execution_path_regression()
    test_stage26_2_8f_production_verifier_full_earn_path()

    print("STAGE26_2_8A_PRODUCTION_VERIFIER_LOCAL_SAFETY_TESTS_OK")
    print(CONFIRMATION_PATH_MARKER)
    print(BUY_COLLECTION_CONTINUATION_PATH_MARKER)
    print(BYBIT_LOWER_LIMIT_REJECT_HANDLING_MARKER)
    return 0


def main() -> int:
    load_dotenv()

    args = parse_args()

    if args.self_test:
        return run_self_test()

    try:
        return run_verification(args)
    except VerificationBlocked as exc:
        print(f"VERIFICATION_BLOCKED={exc}")
        print(NOT_READY_MARKER)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())