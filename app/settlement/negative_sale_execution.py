from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import json

from sqlalchemy.orm import Session

from app.bybit.client import BybitV5Client
from app.bybit.order_execution import (
    BybitOrderResult,
    create_market_sell_order,
    query_order_by_link_id,
)
from app.bybit.earn import (
    BybitEarnOrder,
    format_bybit_earn_amount,
    place_flexible_saving_redeem_order,
    query_earn_order_by_link_id,
    resolve_flexible_saving_product,
    total_flexible_saving_available_amount,
)
from app.config import settings
from app.models import (
    Fund,
    FundNegativeSaleBatch,
    FundNegativeSaleLeg,
    FundSettlementBatch,
)
from app.settlement.negative_sale_snapshot import dec
from app.settlement.accounting_service import (
    SettlementShareQuantityError,
    validate_settlement_share_state_before_external,
)
from app.operation_guard.hooks import (
    require_bybit_earn_redeem_guard,
    require_bybit_negative_sale_order_guard,
)
from app.settlement.statuses import (
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_NEGATIVE_NET_SALE_EXECUTED,
    BATCH_STATUS_NEGATIVE_NET_SALE_PLANNED,
    BATCH_STATUS_NEGATIVE_NET_SALE_PROCESSING,
    BATCH_STATUS_PENDING_CONFIRMATION,
    SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED,
    SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED_WITH_EXTRA_SALE,
    SALE_BATCH_STATUS_SALE_EXECUTION_FAILED_REQUIRES_REVIEW,
    SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING,
    SALE_BATCH_STATUS_SALE_PLAN_CREATED,
    SALE_LEG_STATUS_BUFFER_AVAILABLE,
    SALE_LEG_STATUS_CASH_AVAILABLE,
    SALE_LEG_STATUS_EXTRA_SALE_MOCKED,
    SALE_LEG_STATUS_EXTRA_SALE_PLANNED,
    SALE_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    SALE_LEG_STATUS_FILLED,
    SALE_LEG_STATUS_MARKET_ORDER_MOCKED,
    SALE_LEG_STATUS_NATIVE_ICEBERG_MOCKED,
    SALE_LEG_STATUS_PARTIAL_FILLED_ACCEPTED,
    SALE_LEG_STATUS_PARTIAL_FILLED_BELOW_THRESHOLD,
    SALE_LEG_STATUS_PLANNED,
    SALE_LEG_STATUS_PENDING_CONFIRMATION,
    SALE_LEG_STATUS_RESIDUALIZED,
    SALE_LEG_STATUS_SKIPPED_LIQUIDITY_GUARD,
    SALE_LEG_STATUS_SKIPPED_MARGIN_GUARD,
    SALE_LEG_STATUS_SLICED_IOC_MOCKED,
    SALE_LEG_STATUS_USDT_EARN_REDEEMED,
    SALE_LEG_STATUS_USDT_EARN_REDEEM_MOCKED,
)

from app.settlement.negative_sale_execution_types import (
    HUNDRED,
    ONE,
    ZERO,
    EarnExecutionMock,
    ExtraSaleExecutionMock,
    LegExecutionComputation,
    NegativeSaleExecutionError,
    NegativeSaleExecutionMock,
    NegativeSaleExecutionResult,
    SymbolExecutionMock,
    _json_dict,
    _json_value,
    _max_zero,
    utcnow,
)

from app.settlement.negative_sale_execution_mock import (
    _bool,
    _earn_mock_from_raw,
    _extra_sale_mock_from_raw,
    _optional_str,
    _symbol_key,
    _symbol_mock_from_raw,
    load_negative_sale_execution_mock_file,
    normalize_negative_sale_execution_mock,
)
from app.settlement.negative_sale_live_batch_service import (
    NegativeSaleLiveBatchStepResult,
    resume_negative_sale_order_batch_once,
)
from app.settlement.negative_sale_earn_live_service import (
    resume_negative_sale_earn_once,
)


def supports_native_iceberg(category: str | None) -> bool:
    if category is None:
        return False

    return str(category).strip().lower() in {
        "linear",
        "spot",
        "inverse",
        "usdt_futures",
        "usdc",
    }


def fill_acceptance_ratio(mock: NegativeSaleExecutionMock) -> Decimal:
    return dec(mock.fill_acceptance_pct) / HUNDRED


def sell_corridor_floor_price(
    *,
    last_price: Decimal,
    corridor_pct: Decimal,
) -> Decimal:
    return last_price * (ONE - corridor_pct / HUNDRED)


def has_sufficient_corridor_liquidity(
    *,
    target_cash_usdt: Decimal,
    symbol_mock: SymbolExecutionMock,
    corridor_pct: Decimal,
) -> bool:
    if target_cash_usdt <= ZERO:
        return False

    floor_price = sell_corridor_floor_price(
        last_price=symbol_mock.last_price,
        corridor_pct=corridor_pct,
    )

    return (
        symbol_mock.best_bid >= floor_price
        and symbol_mock.available_liquidity_usdt >= target_cash_usdt
    )


def choose_mock_execution_mode(
    *,
    target_cash_usdt: Decimal,
    category: str | None,
    symbol_mock: SymbolExecutionMock,
    corridor_pct: Decimal,
) -> tuple[str, str]:
    if has_sufficient_corridor_liquidity(
        target_cash_usdt=target_cash_usdt,
        symbol_mock=symbol_mock,
        corridor_pct=corridor_pct,
    ):
        return "market", SALE_LEG_STATUS_MARKET_ORDER_MOCKED

    if symbol_mock.native_strategy_supported and supports_native_iceberg(category):
        return "native_iceberg", SALE_LEG_STATUS_NATIVE_ICEBERG_MOCKED

    return "sliced_ioc_fallback", SALE_LEG_STATUS_SLICED_IOC_MOCKED


def final_fill_status(
    *,
    fill_ratio: Decimal,
    acceptance_ratio: Decimal,
) -> str:
    if fill_ratio >= ONE:
        return SALE_LEG_STATUS_FILLED

    if fill_ratio >= acceptance_ratio:
        return SALE_LEG_STATUS_PARTIAL_FILLED_ACCEPTED

    return SALE_LEG_STATUS_PARTIAL_FILLED_BELOW_THRESHOLD

def deterministic_leg_key(
    *,
    sale_batch_id: int,
    leg_id: int,
    leg_index: int,
) -> str:
    return f"neg-sale:{sale_batch_id}:{leg_id}:{leg_index}"


def deterministic_negative_sale_order_link_id(
    *,
    sale_batch_id: int,
    leg_id: int,
    leg_index: int,
) -> str:
    return f"wb-neg-sale-{int(sale_batch_id)}-{int(leg_id)}-{int(leg_index)}"


def deterministic_negative_sale_earn_redeem_link_id(
    *,
    sale_batch_id: int,
    leg_id: int,
    leg_index: int,
) -> str:
    return f"wb-neg-earn-{int(sale_batch_id)}-{int(leg_id)}-{int(leg_index)}"


def bybit_order_success_status(status: str | None) -> bool:
    return str(status or "").strip() in {
        "Filled",
        "PartiallyFilled",
    }


def bybit_order_cash_delta_usdt(order: BybitOrderResult) -> Decimal:
    return _max_zero(dec(order.cum_exec_value))


def execute_live_market_sell_order_guarded(
    db: Session,
    *,
    client: BybitV5Client,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    leg: FundNegativeSaleLeg,
    qty: Decimal,
) -> BybitOrderResult:
    if qty <= ZERO:
        raise NegativeSaleExecutionError(
            f"Live sale leg qty must be positive: leg_id={leg.id}"
        )

    if not leg.symbol:
        raise NegativeSaleExecutionError(
            f"Live sale leg has no symbol: leg_id={leg.id}"
        )

    category = str(leg.category or "").strip()
    if not category:
        raise NegativeSaleExecutionError(
            f"Live sale leg has no category: leg_id={leg.id}"
        )

    order_link_id = deterministic_negative_sale_order_link_id(
        sale_batch_id=int(sale_batch.id),
        leg_id=int(leg.id),
        leg_index=int(leg.leg_index),
    )

    existing = query_order_by_link_id(
        client,
        category=category,
        symbol=str(leg.symbol),
        order_link_id=order_link_id,
    )
    if existing is not None:
        return existing

    target_cash_usdt = _planned_cash_for_leg(leg)

    require_bybit_negative_sale_order_guard(
        db,
        fund_id=int(sale_batch.fund_id),
        settlement_batch_id=int(settlement_batch.id),
        amount_usdt=target_cash_usdt,
        request_id=order_link_id,
        metadata={
            "sale_batch_id": int(sale_batch.id),
            "sale_leg_id": int(leg.id),
            "leg_index": int(leg.leg_index),
            "symbol": str(leg.symbol),
            "category": category,
            "qty": str(qty),
            "target_cash_usdt": str(target_cash_usdt),
        },
    )

    reduce_only = True if category.lower() in {"linear", "inverse"} else None

    return create_market_sell_order(
        client,
        category=category,
        symbol=str(leg.symbol),
        qty=qty,
        order_link_id=order_link_id,
        reduce_only=reduce_only,
    )


def live_target_qty_for_leg(leg: FundNegativeSaleLeg) -> Decimal:
    qty = dec(leg.target_qty)
    if qty <= ZERO:
        raise NegativeSaleExecutionError(
            f"Live sale leg target_qty must be positive: leg_id={leg.id}"
        )
    return qty


def live_order_status_for_leg(
    *,
    order: BybitOrderResult,
    cash_delta_usdt: Decimal,
) -> str:
    if str(order.status or "").strip() == "Filled":
        return SALE_LEG_STATUS_FILLED

    if str(order.status or "").strip() == "PartiallyFilled" and cash_delta_usdt > ZERO:
        return SALE_LEG_STATUS_PARTIAL_FILLED_ACCEPTED

    return SALE_LEG_STATUS_PENDING_CONFIRMATION


def apply_live_order_result_to_leg(
    *,
    sale_batch: FundNegativeSaleBatch,
    leg: FundNegativeSaleLeg,
    order: BybitOrderResult,
    execution_round: str,
    now: datetime,
) -> dict[str, Any]:
    target_cash_usdt = _planned_cash_for_leg(leg)
    cash_delta_usdt = bybit_order_cash_delta_usdt(order)
    filled_qty = order.cum_exec_qty
    avg_fill_price = order.avg_price

    fill_ratio = (
        min(cash_delta_usdt / target_cash_usdt, ONE)
        if target_cash_usdt > ZERO
        else ZERO
    )
    unfilled_usdt = _max_zero(target_cash_usdt - cash_delta_usdt)

    status = live_order_status_for_leg(
        order=order,
        cash_delta_usdt=cash_delta_usdt,
    )

    deterministic_key = deterministic_leg_key(
        sale_batch_id=int(sale_batch.id),
        leg_id=int(leg.id),
        leg_index=int(leg.leg_index),
    )

    leg.actual_execution_mode = "live_market_sell"
    leg.execution_round = execution_round
    leg.deterministic_key = deterministic_key

    leg.order_link_id = order.order_link_id
    leg.bybit_order_id = order.order_id
    leg.bybit_strategy_id = None

    leg.planned_suborders = 1
    leg.executed_suborders = 1 if cash_delta_usdt > ZERO else 0
    leg.suborders_json = None

    leg.mock_execution_json = {
        "mock_only": False,
        "live_bybit_order": True,
        "category": order.category,
        "symbol": order.symbol,
        "order_id": order.order_id,
        "order_link_id": order.order_link_id,
        "order_status": order.status,
        "side": order.side,
        "order_type": order.order_type,
        "qty": str(order.qty) if order.qty is not None else None,
        "cum_exec_qty": (
            str(order.cum_exec_qty)
            if order.cum_exec_qty is not None
            else None
        ),
        "cum_exec_value": (
            str(order.cum_exec_value)
            if order.cum_exec_value is not None
            else None
        ),
        "avg_price": str(order.avg_price) if order.avg_price is not None else None,
        "raw": order.raw,
    }

    leg.last_price = avg_fill_price
    leg.best_bid = None
    leg.best_ask = None
    leg.corridor_pct = None

    leg.available_liquidity_usdt = None
    leg.available_liquidity_qty = None

    leg.filled_qty = filled_qty
    leg.filled_usdt = cash_delta_usdt
    leg.avg_fill_price = avg_fill_price
    leg.fill_ratio = fill_ratio
    leg.unfilled_usdt = unfilled_usdt
    leg.fee_usdt = ZERO
    leg.cash_delta_usdt = cash_delta_usdt

    leg.sent_at = now
    leg.confirmed_at = now if cash_delta_usdt > ZERO else None
    leg.failed_at = None
    leg.execution_error = None

    leg.status = status
    leg.updated_at = now

    return {
        "leg_id": int(leg.id),
        "leg_index": int(leg.leg_index),
        "symbol": leg.symbol,
        "category": leg.category,
        "target_cash_usdt": str(target_cash_usdt),
        "target_qty": str(leg.target_qty),
        "cash_delta_usdt": str(cash_delta_usdt),
        "filled_qty": str(filled_qty) if filled_qty is not None else None,
        "avg_fill_price": str(avg_fill_price) if avg_fill_price is not None else None,
        "status": status,
        "order_id": order.order_id,
        "order_link_id": order.order_link_id,
        "order_status": order.status,
    }


def execute_initial_sale_legs_live(
    db: Session,
    *,
    client: BybitV5Client,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    legs: list[FundNegativeSaleLeg],
    now: datetime,
) -> list[dict[str, Any]]:
    live_results: list[dict[str, Any]] = []

    for leg in legs:
        if not planned_executable_leg(leg):
            continue

        qty = live_target_qty_for_leg(leg)

        order = execute_live_market_sell_order_guarded(
            db,
            client=client,
            sale_batch=sale_batch,
            settlement_batch=settlement_batch,
            leg=leg,
            qty=qty,
        )

        live_results.append(
            apply_live_order_result_to_leg(
                sale_batch=sale_batch,
                leg=leg,
                order=order,
                execution_round="initial",
                now=now,
            )
        )

    return live_results


def live_sale_cash_delta(results: list[dict[str, Any]]) -> Decimal:
    total = ZERO
    for item in results:
        total += dec(item.get("cash_delta_usdt"))
    return total


def build_live_execution_json(
    *,
    live_results: list[dict[str, Any]],
    earn_redeem_results: list[dict[str, Any]],
    initial_earn_redeemed_usdt: Decimal,
) -> dict[str, Any]:
    return {
        "mock_only": False,
        "stage": "26.3.4",
        "initial_earn_redeem": earn_redeem_results,
        "initial_sale_legs": live_results,
        "extra_sale": None,
        "initial_earn_redeemed_usdt": str(initial_earn_redeemed_usdt),
        "additional_earn_redeemed_usdt": "0",
        "safety": {
            "real_bybit_order_calls": True,
            "real_bybit_earn_redeem_calls": bool(earn_redeem_results),
            "operation_guard_required": True,
            "no_bybit_transfers_or_withdrawals": True,
            "no_bsc_transfers": True,
            "no_accounting_finalization": True,
        },
    }


def build_live_sale_reconciliation_values(
    *,
    required_master_usdt: Decimal,
    initial_cash_usdt: Decimal,
    initial_earn_redeemed_usdt: Decimal,
    live_results: list[dict[str, Any]],
) -> dict[str, Decimal]:
    initial_sale_executed_usdt = live_sale_cash_delta(live_results)

    available_after_initial_sales = (
        initial_cash_usdt
        + initial_earn_redeemed_usdt
        + initial_sale_executed_usdt
    )

    shortage_after_initial_sales = shortage_usdt(
        required_master_usdt=required_master_usdt,
        available_usdt=available_after_initial_sales,
    )

    additional_earn_redeemed_usdt = ZERO
    extra_sale_required_usdt = shortage_after_initial_sales
    extra_sale_target = ZERO
    extra_sale_executed_usdt = ZERO

    final_available_usdt = available_after_initial_sales
    final_shortage_usdt = shortage_usdt(
        required_master_usdt=required_master_usdt,
        available_usdt=final_available_usdt,
    )
    final_surplus_usdt = _max_zero(final_available_usdt - required_master_usdt)

    return {
        "initial_earn_redeemed_usdt": initial_earn_redeemed_usdt,
        "initial_sale_executed_usdt": initial_sale_executed_usdt,
        "available_after_initial_sales": available_after_initial_sales,
        "shortage_after_initial_sales": shortage_after_initial_sales,
        "additional_earn_redeemed_usdt": additional_earn_redeemed_usdt,
        "extra_sale_required_usdt": extra_sale_required_usdt,
        "extra_sale_target": extra_sale_target,
        "extra_sale_executed_usdt": extra_sale_executed_usdt,
        "final_available_usdt": final_available_usdt,
        "final_shortage_usdt": final_shortage_usdt,
        "final_surplus_usdt": final_surplus_usdt,
    }


def apply_live_sale_reconciliation_to_batch(
    *,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    required_master_usdt: Decimal,
    initial_cash_usdt: Decimal,
    initial_earn_redeemed_usdt: Decimal,
    live_results: list[dict[str, Any]],
    earn_redeem_results: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    values = build_live_sale_reconciliation_values(
        required_master_usdt=required_master_usdt,
        initial_cash_usdt=initial_cash_usdt,
        initial_earn_redeemed_usdt=initial_earn_redeemed_usdt,
        live_results=live_results,
    )

    execution_json = build_live_execution_json(
        live_results=live_results,
        earn_redeem_results=earn_redeem_results,
        initial_earn_redeemed_usdt=initial_earn_redeemed_usdt,
    )

    reconciliation_json = build_reconciliation_json(
        required_master_usdt=required_master_usdt,
        initial_cash_usdt=initial_cash_usdt,
        initial_earn_redeemed_usdt=values["initial_earn_redeemed_usdt"],
        initial_sale_executed_usdt=values["initial_sale_executed_usdt"],
        available_after_initial_sales=values["available_after_initial_sales"],
        shortage_after_initial_sales=values["shortage_after_initial_sales"],
        additional_earn_redeemed_usdt=values["additional_earn_redeemed_usdt"],
        extra_sale_required_usdt=values["extra_sale_required_usdt"],
        extra_sale_target=values["extra_sale_target"],
        extra_sale_executed_usdt=values["extra_sale_executed_usdt"],
        final_available_usdt=values["final_available_usdt"],
        final_shortage_usdt=values["final_shortage_usdt"],
        final_surplus_usdt=values["final_surplus_usdt"],
    )

    apply_execution_reconciliation_to_batch(
        sale_batch=sale_batch,
        settlement_batch=settlement_batch,
        initial_cash_usdt=initial_cash_usdt,
        initial_earn_redeemed_usdt=values["initial_earn_redeemed_usdt"],
        initial_sale_executed_usdt=values["initial_sale_executed_usdt"],
        available_after_initial_sales=values["available_after_initial_sales"],
        shortage_after_initial_sales=values["shortage_after_initial_sales"],
        additional_earn_redeemed_usdt=values["additional_earn_redeemed_usdt"],
        extra_sale_required_usdt=values["extra_sale_required_usdt"],
        extra_sale_target=values["extra_sale_target"],
        extra_sale_executed_usdt=values["extra_sale_executed_usdt"],
        final_available_usdt=values["final_available_usdt"],
        final_shortage_usdt=values["final_shortage_usdt"],
        final_surplus_usdt=values["final_surplus_usdt"],
        execution_json=execution_json,
        reconciliation_json=reconciliation_json,
        now=now,
    )

    return {
        "values": values,
        "execution_json": execution_json,
        "reconciliation_json": reconciliation_json,
    }


def prepare_negative_sale_live_execution(
    db: Session,
    *,
    sale_batch_id: int,
    now: datetime,
) -> tuple[
    FundNegativeSaleBatch,
    FundSettlementBatch,
    Fund,
    list[FundNegativeSaleLeg],
    str,
    str,
]:
    sale_batch = _lock_sale_batch(
        db,
        sale_batch_id=sale_batch_id,
    )
    status_before = str(sale_batch.status)

    settlement_batch = _lock_settlement_batch(
        db,
        settlement_batch_id=int(sale_batch.settlement_batch_id),
    )
    settlement_status_before = str(settlement_batch.status)

    fund = _get_fund(
        db,
        fund_id=int(sale_batch.fund_id),
    )

    legs = _load_sale_legs_for_update(
        db,
        sale_batch_id=int(sale_batch.id),
    )

    try:
        validate_settlement_share_state_before_external(
            db,
            batch=settlement_batch,
            mark_failed=True,
        )
    except SettlementShareQuantityError as exc:
        error = str(exc)

        sale_batch.status = (
            SALE_BATCH_STATUS_SALE_EXECUTION_FAILED_REQUIRES_REVIEW
        )
        sale_batch.error = error
        sale_batch.updated_at = now

        settlement_batch.status = (
            BATCH_STATUS_FAILED_REQUIRES_REVIEW
        )
        settlement_batch.error = error
        settlement_batch.updated_at = now

        db.add(sale_batch)
        db.add(settlement_batch)
        db.flush()
        raise

    _validate_sale_execution_input(
        sale_batch=sale_batch,
        settlement_batch=settlement_batch,
        legs=legs,
    )

    is_initial_transition = (
        sale_batch.status
        == SALE_BATCH_STATUS_SALE_PLAN_CREATED
        and settlement_batch.status
        == BATCH_STATUS_NEGATIVE_NET_SALE_PLANNED
    )

    if is_initial_transition:
        sale_batch.status = (
            SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING
        )
        settlement_batch.status = (
            BATCH_STATUS_NEGATIVE_NET_SALE_PROCESSING
        )

    # On restart, PROCESSING and
    # PENDING_CONFIRMATION are preserved.
    # The state machine must reconcile
    # durable evidence before any new POST.
    sale_batch.execution_started_at = (
        sale_batch.execution_started_at
        or now
    )
    sale_batch.updated_at = now
    settlement_batch.updated_at = now

    db.add(sale_batch)
    db.add(settlement_batch)
    db.flush()

    return (
        sale_batch,
        settlement_batch,
        fund,
        legs,
        status_before,
        settlement_status_before,
    )


NEGATIVE_SALE_LIVE_STATE_MACHINE_SCHEMA = (
    "negative_sale_live_state_machine_v1"
)




def _has_completed_correction_round(
    legs: list[FundNegativeSaleLeg],
) -> bool:
    for leg in legs:
        raw_round = getattr(
            leg,
            "execution_round",
            None,
        )

        if raw_round in (
            None,
            "",
            "initial",
        ):
            continue

        try:
            round_number = int(
                raw_round
            )
        except (
            TypeError,
            ValueError,
        ):
            continue

        if round_number > 0:
            return True

    return False


def _executed_live_leg_count(
    legs: list[FundNegativeSaleLeg],
) -> int:
    return sum(
        1
        for leg in legs
        if int(
            getattr(
                leg,
                "executed_suborders",
                0,
            )
            or 0
        )
        > 0
    )


def _append_negative_sale_live_step_audit(
    *,
    sale_batch: FundNegativeSaleBatch,
    step: NegativeSaleLiveBatchStepResult,
    now: datetime,
) -> None:
    existing = (
        dict(
            sale_batch.execution_json
        )
        if isinstance(
            sale_batch.execution_json,
            dict,
        )
        else {}
    )

    same_schema = (
        existing.get("schema")
        == NEGATIVE_SALE_LIVE_STATE_MACHINE_SCHEMA
    )

    existing_steps = (
        existing.get("steps")
        if same_schema
        else None
    )

    steps = (
        [
            dict(item)
            for item in existing_steps
            if isinstance(item, dict)
        ]
        if isinstance(
            existing_steps,
            list,
        )
        else []
    )

    step_dict = step.to_dict()

    steps.append(
        {
            "recorded_at": (
                now.isoformat()
            ),
            "step": step_dict,
        }
    )

    execution_json: dict[
        str,
        Any,
    ] = {
        "schema": (
            NEGATIVE_SALE_LIVE_STATE_MACHINE_SCHEMA
        ),
        "latest_step": step_dict,
        "steps": steps,
        "safety": {
            "operation_guard_required": True,
            "no_bybit_transfer": True,
            "no_bybit_withdrawal": True,
            "no_bsc_action": True,
            "no_accounting_finalization": (
                True
            ),
        },
    }

    if existing and not same_schema:
        execution_json[
            "previous_execution_json"
        ] = existing

    sale_batch.execution_json = (
        execution_json
    )


def apply_negative_sale_live_batch_step(
    db: Session,
    *,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    legs: list[FundNegativeSaleLeg],
    step: NegativeSaleLiveBatchStepResult,
    now: datetime,
) -> None:
    required_master_usdt = (
        _max_zero(
            dec(
                sale_batch
                .required_master_usdt
            )
        )
    )

    confirmed_available = (
        step.confirmed_available_usdt
    )
    confirmed_shortage = (
        step.shortage_usdt
    )

    if confirmed_available is not None:
        confirmed_available = (
            _max_zero(
                dec(
                    confirmed_available
                )
            )
        )

        if confirmed_shortage is None:
            confirmed_shortage = (
                _max_zero(
                    required_master_usdt
                    - confirmed_available
                )
            )
        else:
            confirmed_shortage = (
                _max_zero(
                    dec(
                        confirmed_shortage
                    )
                )
            )

        confirmed_surplus = (
            _max_zero(
                confirmed_available
                - required_master_usdt
            )
        )

        sale_batch.final_available_usdt = (
            confirmed_available
        )
        sale_batch.final_shortage_usdt = (
            confirmed_shortage
        )
        sale_batch.final_surplus_usdt = (
            confirmed_surplus
        )

        sale_batch.reconciliation_json = {
            "schema": (
                "negative_sale_confirmed_"
                "transferable_balance_v1"
            ),
            "cash_source": (
                "confirmed_transferable_"
                "usdt"
            ),
            "required_master_usdt": str(
                required_master_usdt
            ),
            "confirmed_available_usdt": (
                str(
                    confirmed_available
                )
            ),
            "confirmed_shortage_usdt": (
                str(
                    confirmed_shortage
                )
            ),
            "confirmed_surplus_usdt": (
                str(
                    confirmed_surplus
                )
            ),
            "transferable_balance": (
                step.transferable_balance
            ),
            "correction_decision": (
                step.correction_decision
            ),
            "no_derivative_exec_value_"
            "as_cash": True,
        }

    successful_balance_check = (
        step.action == "balance_check"
        and confirmed_shortage
        is not None
        and confirmed_shortage <= ZERO
        and not step.requires_review
    )

    unresolved_terminal_shortage = (
        step.action == "balance_check"
        and confirmed_shortage
        is not None
        and confirmed_shortage > ZERO
        and not step.has_pending_action
    )

    requires_review = (
        step.requires_review
        or step.action
        == "review_required"
        or unresolved_terminal_shortage
    )

    if requires_review:
        sale_batch.status = (
            SALE_BATCH_STATUS_SALE_EXECUTION_FAILED_REQUIRES_REVIEW
        )
        settlement_batch.status = (
            BATCH_STATUS_FAILED_REQUIRES_REVIEW
        )

        error = (
            "Negative sale state machine "
            f"requires review: {step.reason}"
        )

        sale_batch.error = error
        settlement_batch.error = error

    elif successful_balance_check:
        sale_batch.status = (
            SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED_WITH_EXTRA_SALE
            if _has_completed_correction_round(
                legs
            )
            else SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED
        )
        settlement_batch.status = (
            BATCH_STATUS_NEGATIVE_NET_SALE_EXECUTED
        )

        sale_batch.execution_completed_at = (
            now
        )
        sale_batch.error = None
        settlement_batch.error = None

    elif step.has_pending_action:
        sale_batch.status = (
            SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING
        )
        settlement_batch.status = (
            BATCH_STATUS_PENDING_CONFIRMATION
        )

        sale_batch.error = None
        settlement_batch.error = None

    else:
        sale_batch.status = (
            SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING
        )
        settlement_batch.status = (
            BATCH_STATUS_NEGATIVE_NET_SALE_PROCESSING
        )

        sale_batch.error = None
        settlement_batch.error = None

    _append_negative_sale_live_step_audit(
        sale_batch=sale_batch,
        step=step,
        now=now,
    )

    sale_batch.updated_at = now
    settlement_batch.updated_at = now

    db.add(sale_batch)
    db.add(settlement_batch)
    db.flush()


def build_negative_sale_live_step_result(
    *,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    fund: Fund,
    legs: list[FundNegativeSaleLeg],
    status_before: str,
    settlement_status_before: str,
    step: NegativeSaleLiveBatchStepResult,
) -> NegativeSaleExecutionResult:
    final_available = (
        step.confirmed_available_usdt
        if step.confirmed_available_usdt
        is not None
        else sale_batch.final_available_usdt
    )
    final_shortage = (
        step.shortage_usdt
        if step.shortage_usdt
        is not None
        else sale_batch.final_shortage_usdt
    )

    final_surplus = (
        _max_zero(
            dec(final_available)
            - _max_zero(
                dec(
                    sale_batch
                    .required_master_usdt
                )
            )
        )
        if final_available is not None
        else None
    )

    completed = (
        sale_batch.status
        in {
            SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED,
            SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED_WITH_EXTRA_SALE,
        }
    )

    return NegativeSaleExecutionResult(
        ok=(
            completed
            and final_shortage
            is not None
            and dec(
                final_shortage
            )
            <= ZERO
        ),
        sale_batch_id=int(
            sale_batch.id
        ),
        settlement_batch_id=int(
            settlement_batch.id
        ),
        fund_id=int(
            fund.id
        ),
        fund_code=str(
            fund.code
        ),
        status_before=status_before,
        status_after=str(
            sale_batch.status
        ),
        settlement_status_before=(
            settlement_status_before
        ),
        settlement_status_after=str(
            settlement_batch.status
        ),
        final_available_usdt=(
            dec(final_available)
            if final_available
            is not None
            else None
        ),
        final_shortage_usdt=(
            dec(final_shortage)
            if final_shortage
            is not None
            else None
        ),
        final_surplus_usdt=(
            final_surplus
        ),
        executed_leg_count=(
            _executed_live_leg_count(
                legs
            )
        ),
        error=sale_batch.error,
        diagnostics={
            "mock_only": False,
            "state_machine_schema": (
                NEGATIVE_SALE_LIVE_STATE_MACHINE_SCHEMA
            ),
            "state_machine_step": (
                step.to_dict()
            ),
            "bybit_order_posted": (
                step.posted
            ),
            "operation_guard_required": True,
            "confirmed_transferable_usdt_"
            "is_cash_source": True,
            "no_derivative_exec_value_"
            "as_cash": True,
            "no_bybit_transfers_or_"
            "withdrawals": True,
            "no_bsc_transfers": True,
            "no_accounting_finalization": (
                True
            ),
        },
    )


def build_negative_sale_live_result(
    *,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    fund: Fund,
    status_before: str,
    settlement_status_before: str,
    values: dict[str, Decimal],
    live_results: list[dict[str, Any]],
) -> NegativeSaleExecutionResult:
    final_shortage_usdt = values["final_shortage_usdt"]

    return NegativeSaleExecutionResult(
        ok=final_shortage_usdt <= ZERO,
        sale_batch_id=int(sale_batch.id),
        settlement_batch_id=int(settlement_batch.id),
        fund_id=int(fund.id),
        fund_code=str(fund.code),
        status_before=status_before,
        status_after=str(sale_batch.status),
        settlement_status_before=settlement_status_before,
        settlement_status_after=str(settlement_batch.status),
        final_available_usdt=values["final_available_usdt"],
        final_shortage_usdt=final_shortage_usdt,
        final_surplus_usdt=values["final_surplus_usdt"],
        executed_leg_count=len(live_results),
        error=sale_batch.error,
        diagnostics={
            "mock_only": False,
            "live_bybit_order_calls": True,
            "operation_guard_required": True,
            "initial_earn_redeemed_usdt": str(values["initial_earn_redeemed_usdt"]),
            "initial_sale_executed_usdt": str(values["initial_sale_executed_usdt"]),
            "shortage_after_initial_sales_usdt": str(
                values["shortage_after_initial_sales"]
            ),
            "no_bybit_transfers_or_withdrawals": True,
            "no_bsc_transfers": True,
            "no_accounting_finalization": True,
        },
    )


def execute_negative_sale_plan_live(
    db: Session,
    *,
    sale_batch_id: int,
    client: BybitV5Client,
    now: datetime | None = None,
) -> NegativeSaleExecutionResult:
    effective_now = now or utcnow()

    (
        sale_batch,
        settlement_batch,
        fund,
        legs,
        status_before,
        settlement_status_before,
    ) = prepare_negative_sale_live_execution(
        db,
        sale_batch_id=sale_batch_id,
        now=effective_now,
    )

    # Persist the initial PROCESSING
    # transition and release all row locks
    # before any Bybit HTTP request.
    db.commit()

    # Earn redemption has priority over
    # trading sale legs. The Earn service
    # performs at most one durable runtime
    # step in this executor invocation:
    #
    # prepare
    # submit/ACK
    # confirmation
    # review
    #
    # Trading execution is reached only
    # after Earn has no active step.
    earn_step = (
        resume_negative_sale_earn_once(
            db,
            client=client,
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            legs=legs,
            now=effective_now,
        )
    )

    if earn_step is not None:
        step = earn_step
    else:
        step = (
            resume_negative_sale_order_batch_once(
                db,
                client=client,
                sale_batch=sale_batch,
                settlement_batch=(
                    settlement_batch
                ),
                legs=legs,
                now=effective_now,
            )
        )

    apply_negative_sale_live_batch_step(
        db,
        sale_batch=sale_batch,
        settlement_batch=(
            settlement_batch
        ),
        legs=legs,
        step=step,
        now=effective_now,
    )

    db.commit()

    return (
        build_negative_sale_live_step_result(
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            fund=fund,
            legs=legs,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            step=step,
        )
    )


def sliced_ioc_suborders(
    *,
    deterministic_key: str,
    target_cash_usdt: Decimal,
    slices: int,
) -> dict[str, Any]:
    if slices <= 0:
        raise NegativeSaleExecutionError("slices must be positive")

    slice_cash = target_cash_usdt / Decimal(str(slices))

    return {
        "mode": "sliced_ioc_fallback",
        "slices": [
            {
                "slice_index": i,
                "deterministic_key": f"{deterministic_key}:slice:{i}",
                "target_cash_usdt": str(slice_cash),
            }
            for i in range(1, slices + 1)
        ],
    }
    
def _lock_sale_batch(
    db: Session,
    *,
    sale_batch_id: int,
) -> FundNegativeSaleBatch:
    sale_batch = (
        db.query(FundNegativeSaleBatch)
        .filter(FundNegativeSaleBatch.id == int(sale_batch_id))
        .with_for_update()
        .first()
    )

    if sale_batch is None:
        raise NegativeSaleExecutionError(f"Negative sale batch not found: {sale_batch_id}")

    return sale_batch


def _lock_settlement_batch(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundSettlementBatch:
    settlement_batch = (
        db.query(FundSettlementBatch)
        .filter(FundSettlementBatch.id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )

    if settlement_batch is None:
        raise NegativeSaleExecutionError(
            f"Settlement batch not found: {settlement_batch_id}"
        )

    return settlement_batch


def _get_fund(
    db: Session,
    *,
    fund_id: int,
) -> Fund:
    fund = (
        db.query(Fund)
        .filter(Fund.id == int(fund_id))
        .first()
    )

    if fund is None:
        raise NegativeSaleExecutionError(f"Fund not found: {fund_id}")

    return fund


def _load_sale_legs_for_update(
    db: Session,
    *,
    sale_batch_id: int,
) -> list[FundNegativeSaleLeg]:
    return (
        db.query(FundNegativeSaleLeg)
        .filter(FundNegativeSaleLeg.sale_batch_id == int(sale_batch_id))
        .order_by(FundNegativeSaleLeg.leg_index.asc())
        .with_for_update()
        .all()
    )
    
def _validate_stage23_3_safety(mock_execution: NegativeSaleExecutionMock) -> None:
    if settings.NEGATIVE_NET_SALE_ALLOW_LIVE_EXECUTION:
        raise NegativeSaleExecutionError(
            "Live negative-net sale execution is blocked in Stage 23.3"
        )

    if not settings.NEGATIVE_NET_SALE_EXECUTION_MOCK_ONLY:
        raise NegativeSaleExecutionError(
            "Stage 23.3 requires NEGATIVE_NET_SALE_EXECUTION_MOCK_ONLY=true"
        )

    if not mock_execution.mock_only:
        raise NegativeSaleExecutionError("Stage 23.3 execution requires mock_only=true")


def _validate_sale_execution_input(
    *,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    legs: list[
        FundNegativeSaleLeg
    ],
) -> None:
    status_pair = (
        str(sale_batch.status),
        str(settlement_batch.status),
    )

    allowed_status_pairs = {
        (
            SALE_BATCH_STATUS_SALE_PLAN_CREATED,
            BATCH_STATUS_NEGATIVE_NET_SALE_PLANNED,
        ),
        (
            SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING,
            BATCH_STATUS_NEGATIVE_NET_SALE_PROCESSING,
        ),
        (
            SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING,
            BATCH_STATUS_PENDING_CONFIRMATION,
        ),
    }

    if status_pair not in (
        allowed_status_pairs
    ):
        raise NegativeSaleExecutionError(
            "Unsupported negative-sale "
            "resume status pair: "
            f"sale_batch={status_pair[0]}, "
            "settlement_batch="
            f"{status_pair[1]}"
        )

    if (
        sale_batch.settlement_batch_id
        != settlement_batch.id
    ):
        raise NegativeSaleExecutionError(
            "Sale batch "
            "settlement_batch_id mismatch"
        )

    if (
        sale_batch.fund_id
        != settlement_batch.fund_id
    ):
        raise NegativeSaleExecutionError(
            "Sale batch fund_id mismatch"
        )

    if (
        dec(
            sale_batch
            .required_master_usdt
        )
        <= ZERO
    ):
        raise NegativeSaleExecutionError(
            "Sale batch "
            "required_master_usdt "
            "must be positive"
        )

    if not legs:
        raise NegativeSaleExecutionError(
            "Sale batch has no sale legs"
        )


def _leg_symbol_key(leg: FundNegativeSaleLeg) -> str:
    symbol = _optional_str(leg.symbol)
    if symbol is None:
        raise NegativeSaleExecutionError(
            f"Sale leg {leg.id} has no symbol and cannot be mock-executed"
        )

    return _symbol_key(symbol)


def _symbol_mock_for_leg(
    *,
    leg: FundNegativeSaleLeg,
    mock_execution: NegativeSaleExecutionMock,
) -> SymbolExecutionMock:
    symbol = _leg_symbol_key(leg)

    item = mock_execution.symbols.get(symbol)
    if item is None:
        raise NegativeSaleExecutionError(
            f"No execution mock found for symbol={symbol}, leg_id={leg.id}"
        )

    return item


def _planned_cash_for_leg(leg: FundNegativeSaleLeg) -> Decimal:
    return _max_zero(dec(leg.target_cash_usdt))


def compute_planned_leg_execution(
    *,
    sale_batch_id: int,
    leg: FundNegativeSaleLeg,
    mock_execution: NegativeSaleExecutionMock,
    execution_round: str = "initial",
) -> LegExecutionComputation:
    if leg.status != SALE_LEG_STATUS_PLANNED:
        raise NegativeSaleExecutionError(
            f"Leg {leg.id} status must be planned, got {leg.status}"
        )

    if not leg.use_for_deficit_cover:
        raise NegativeSaleExecutionError(
            f"Leg {leg.id} is not marked use_for_deficit_cover=true"
        )

    target_cash_usdt = _planned_cash_for_leg(leg)
    if target_cash_usdt <= ZERO:
        raise NegativeSaleExecutionError(f"Leg {leg.id} target_cash_usdt must be positive")

    symbol_mock = _symbol_mock_for_leg(
        leg=leg,
        mock_execution=mock_execution,
    )

    deterministic_key = deterministic_leg_key(
        sale_batch_id=sale_batch_id,
        leg_id=int(leg.id),
        leg_index=int(leg.leg_index),
    )

    actual_execution_mode, transition_status = choose_mock_execution_mode(
        target_cash_usdt=target_cash_usdt,
        category=leg.category or symbol_mock.category,
        symbol_mock=symbol_mock,
        corridor_pct=mock_execution.sell_corridor_pct,
    )

    acceptance_ratio = fill_acceptance_ratio(mock_execution)
    fill_ratio = max(min(symbol_mock.mock_fill_ratio, ONE), ZERO)
    filled_usdt = target_cash_usdt * fill_ratio
    unfilled_usdt = _max_zero(target_cash_usdt - filled_usdt)
    fee_usdt = min(symbol_mock.fee_usdt, filled_usdt)
    cash_delta_usdt = _max_zero(filled_usdt - fee_usdt)

    avg_fill_price = symbol_mock.last_price if filled_usdt > ZERO else None
    filled_qty = (
        filled_usdt / avg_fill_price
        if avg_fill_price is not None and avg_fill_price > ZERO
        else None
    )

    status = final_fill_status(
        fill_ratio=fill_ratio,
        acceptance_ratio=acceptance_ratio,
    )

    suborders_json = None
    planned_suborders = None
    executed_suborders = None

    if actual_execution_mode == "sliced_ioc_fallback":
        suborders_json = sliced_ioc_suborders(
            deterministic_key=deterministic_key,
            target_cash_usdt=target_cash_usdt,
            slices=mock_execution.slices,
        )
        planned_suborders = mock_execution.slices
        executed_suborders = mock_execution.slices

    return LegExecutionComputation(
        leg_id=int(leg.id),
        leg_index=int(leg.leg_index),
        deterministic_key=deterministic_key,
        symbol=leg.symbol,
        category=leg.category or symbol_mock.category,
        planned_cash_usdt=target_cash_usdt,
        actual_execution_mode=actual_execution_mode,
        execution_round=execution_round,
        status=status,
        transition_status=transition_status,
        last_price=symbol_mock.last_price,
        best_bid=symbol_mock.best_bid,
        best_ask=symbol_mock.best_ask,
        corridor_pct=mock_execution.sell_corridor_pct,
        available_liquidity_usdt=symbol_mock.available_liquidity_usdt,
        available_liquidity_qty=symbol_mock.available_liquidity_qty,
        filled_qty=filled_qty,
        filled_usdt=filled_usdt,
        avg_fill_price=avg_fill_price,
        fill_ratio=fill_ratio,
        unfilled_usdt=unfilled_usdt,
        fee_usdt=fee_usdt,
        cash_delta_usdt=cash_delta_usdt,
        planned_suborders=planned_suborders,
        executed_suborders=executed_suborders,
        suborders_json=suborders_json,
        mock_execution_json={
            "mock_id": mock_execution.mock_id,
            "mock_only": True,
            "transition_status": transition_status,
            "final_status": status,
            "no_real_bybit_calls": True,
            "no_trades": True,
            "no_real_strategy_orders": True,
        },
    )
    
def apply_leg_execution_computation(
    *,
    leg: FundNegativeSaleLeg,
    computation: LegExecutionComputation,
    now: datetime,
) -> None:
    leg.actual_execution_mode = computation.actual_execution_mode
    leg.execution_round = computation.execution_round
    leg.deterministic_key = computation.deterministic_key

    leg.bybit_order_id = None
    leg.bybit_strategy_id = None

    leg.planned_suborders = computation.planned_suborders
    leg.executed_suborders = computation.executed_suborders
    leg.suborders_json = computation.suborders_json
    leg.mock_execution_json = computation.mock_execution_json

    leg.last_price = computation.last_price
    leg.best_bid = computation.best_bid
    leg.best_ask = computation.best_ask
    leg.corridor_pct = computation.corridor_pct

    leg.available_liquidity_usdt = computation.available_liquidity_usdt
    leg.available_liquidity_qty = computation.available_liquidity_qty

    leg.filled_qty = computation.filled_qty
    leg.filled_usdt = computation.filled_usdt
    leg.avg_fill_price = computation.avg_fill_price
    leg.fill_ratio = computation.fill_ratio
    leg.unfilled_usdt = computation.unfilled_usdt
    leg.fee_usdt = computation.fee_usdt
    leg.cash_delta_usdt = computation.cash_delta_usdt

    leg.sent_at = now
    leg.confirmed_at = now
    leg.failed_at = None
    leg.execution_error = computation.execution_error

    leg.status = computation.status
    leg.updated_at = now
    
EXECUTED_LEG_FINAL_STATUSES = {
    SALE_LEG_STATUS_FILLED,
    SALE_LEG_STATUS_PARTIAL_FILLED_ACCEPTED,
    SALE_LEG_STATUS_PARTIAL_FILLED_BELOW_THRESHOLD,
    SALE_LEG_STATUS_RESIDUALIZED,
    SALE_LEG_STATUS_USDT_EARN_REDEEMED,
    SALE_LEG_STATUS_USDT_EARN_REDEEM_MOCKED,
    SALE_LEG_STATUS_EXTRA_SALE_MOCKED,
}


def leg_has_execution_result(leg: FundNegativeSaleLeg) -> bool:
    if leg.status in EXECUTED_LEG_FINAL_STATUSES:
        return True

    if leg.deterministic_key and leg.actual_execution_mode:
        return True

    return False


def planned_executable_leg(
    leg: FundNegativeSaleLeg,
) -> bool:
    if (
        leg.status
        != SALE_LEG_STATUS_PLANNED
    ):
        return False

    if leg_has_execution_result(leg):
        return False

    category = str(
        leg.category
        or ""
    ).strip().lower()

    if category in {
        "linear",
        "inverse",
        "option",
    }:
        # Derivative reduction is measured
        # by confirmed quantity, never by
        # expected cash.
        return (
            dec(leg.target_qty)
            > ZERO
        )

    return (
        bool(
            leg.use_for_deficit_cover
        )
        and dec(
            leg.target_cash_usdt
        )
        > ZERO
    )


def existing_leg_cash_delta_usdt(leg: FundNegativeSaleLeg) -> Decimal:
    if not leg_has_execution_result(leg):
        return ZERO

    return _max_zero(dec(leg.cash_delta_usdt))


def sum_existing_execution_cash_delta(legs: list[FundNegativeSaleLeg]) -> Decimal:
    return sum((existing_leg_cash_delta_usdt(leg) for leg in legs), ZERO)

def execute_initial_sale_legs_mock(
    *,
    sale_batch: FundNegativeSaleBatch,
    legs: list[FundNegativeSaleLeg],
    mock_execution: NegativeSaleExecutionMock,
    now: datetime,
) -> list[LegExecutionComputation]:
    computations: list[LegExecutionComputation] = []

    for leg in legs:
        if not planned_executable_leg(leg):
            continue

        computation = compute_planned_leg_execution(
            sale_batch_id=int(sale_batch.id),
            leg=leg,
            mock_execution=mock_execution,
            execution_round="initial",
        )

        apply_leg_execution_computation(
            leg=leg,
            computation=computation,
            now=now,
        )

        computations.append(computation)

    return computations

def _find_usdt_earn_buffer_leg(
    legs: list[FundNegativeSaleLeg],
) -> FundNegativeSaleLeg | None:
    for leg in legs:
        if leg.leg_type == "usdt_earn_buffer":
            return leg

    return None


def mock_initial_usdt_earn_redeem(
    *,
    legs: list[FundNegativeSaleLeg],
    mock_execution: NegativeSaleExecutionMock,
    now: datetime,
) -> Decimal:
    leg = _find_usdt_earn_buffer_leg(legs)
    if leg is None:
        return ZERO

    if leg.status != SALE_LEG_STATUS_BUFFER_AVAILABLE:
        return ZERO

    planned_buffer = _max_zero(dec(leg.target_cash_usdt))
    redeemable = _max_zero(mock_execution.usdt_earn.initial_redeemable_usdt)
    requested_fill = _max_zero(mock_execution.usdt_earn.initial_redeem_fill_usdt)

    redeemed = min(planned_buffer, redeemable, requested_fill)

    leg.actual_execution_mode = "mock_usdt_earn_redeem"
    leg.execution_round = "initial"
    leg.deterministic_key = (
        leg.deterministic_key
        or f"neg-sale-earn-redeem:{leg.sale_batch_id}:{leg.id}:{leg.leg_index}"
    )

    leg.bybit_order_id = None
    leg.bybit_strategy_id = None
    leg.planned_suborders = None
    leg.executed_suborders = None
    leg.suborders_json = None

    leg.filled_qty = redeemed
    leg.filled_usdt = redeemed
    leg.avg_fill_price = Decimal("1") if redeemed > ZERO else None
    leg.fill_ratio = (
        redeemed / planned_buffer
        if planned_buffer > ZERO
        else ZERO
    )
    leg.unfilled_usdt = _max_zero(planned_buffer - redeemed)
    leg.fee_usdt = ZERO
    leg.cash_delta_usdt = redeemed

    leg.mock_execution_json = {
        "mock_id": mock_execution.mock_id,
        "mock_only": True,
        "type": "usdt_earn_redeem",
        "execution_round": "initial",
        "planned_buffer_usdt": str(planned_buffer),
        "mock_redeemable_usdt": str(redeemable),
        "mock_redeem_fill_usdt": str(requested_fill),
        "redeemed_usdt": str(redeemed),
        "no_real_bybit_calls": True,
        "no_trades": True,
        "no_real_strategy_orders": True,
        "no_transfers_or_withdrawals": True,
    }

    leg.sent_at = now
    leg.confirmed_at = now
    leg.failed_at = None
    leg.execution_error = None
    leg.status = SALE_LEG_STATUS_USDT_EARN_REDEEM_MOCKED
    leg.updated_at = now

    return redeemed


def _earn_order_success(order: BybitEarnOrder | None) -> bool:
    return str(order.status or "").strip() == "Success"


def _earn_order_pending(order: BybitEarnOrder | None) -> bool:
    return str(order.status or "").strip() == "Pending"


def _earn_order_failed(order: BybitEarnOrder | None) -> bool:
    return str(order.status or "").strip() == "Fail"


def compute_needed_from_earn(
    *,
    required_master_usdt: Decimal,
    already_realized_cash_usdt: Decimal,
    target_cash_usdt: Decimal,
    available_amount: Decimal,
) -> Decimal:
    shortage = _max_zero(required_master_usdt - already_realized_cash_usdt)
    return min(
        shortage,
        _max_zero(target_cash_usdt),
        _max_zero(available_amount),
    )


def apply_live_usdt_earn_redeem_to_leg(
    *,
    leg: FundNegativeSaleLeg,
    order: BybitEarnOrder,
    redeemed_usdt: Decimal,
    order_link_id: str,
    now: datetime,
) -> dict[str, Any]:
    planned_buffer = _max_zero(dec(leg.target_cash_usdt))

    leg.actual_execution_mode = "live_usdt_earn_redeem"
    leg.execution_round = "initial"
    leg.deterministic_key = order_link_id

    leg.order_link_id = order_link_id
    leg.bybit_order_id = order.order_id
    leg.bybit_strategy_id = None

    leg.planned_suborders = None
    leg.executed_suborders = 1
    leg.suborders_json = None

    leg.filled_qty = redeemed_usdt
    leg.filled_usdt = redeemed_usdt
    leg.avg_fill_price = Decimal("1")
    leg.fill_ratio = (
        min(redeemed_usdt / planned_buffer, ONE)
        if planned_buffer > ZERO
        else ZERO
    )
    leg.unfilled_usdt = _max_zero(planned_buffer - redeemed_usdt)
    leg.fee_usdt = ZERO
    leg.cash_delta_usdt = redeemed_usdt

    leg.mock_execution_json = {
        "mock_only": False,
        "type": "live_usdt_earn_redeem",
        "execution_round": "initial",
        "planned_buffer_usdt": str(planned_buffer),
        "redeemed_usdt": str(redeemed_usdt),
        "order_id": order.order_id,
        "order_link_id": order_link_id,
        "order_status": order.status,
        "product_id": order.product_id,
        "coin": order.coin,
        "operation_guard_required": True,
        "no_trades": True,
        "no_bsc_transfers": True,
    }

    leg.sent_at = now
    leg.confirmed_at = now
    leg.failed_at = None
    leg.execution_error = None
    leg.status = SALE_LEG_STATUS_USDT_EARN_REDEEMED
    leg.updated_at = now

    return {
        "leg_id": int(leg.id),
        "leg_index": int(leg.leg_index),
        "type": "live_usdt_earn_redeem",
        "target_cash_usdt": str(planned_buffer),
        "redeemed_usdt": str(redeemed_usdt),
        "cash_delta_usdt": str(redeemed_usdt),
        "order_id": order.order_id,
        "order_link_id": order_link_id,
        "order_status": order.status,
        "status": SALE_LEG_STATUS_USDT_EARN_REDEEMED,
    }


def execute_live_usdt_earn_redeem_guarded(
    db: Session,
    *,
    client: BybitV5Client,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    leg: FundNegativeSaleLeg,
    already_realized_cash_usdt: Decimal,
    now: datetime,
) -> tuple[Decimal, dict[str, Any] | None]:
    if leg.leg_type != "usdt_earn_buffer":
        return ZERO, None

    if leg.status == SALE_LEG_STATUS_USDT_EARN_REDEEMED:
        existing_delta = _max_zero(dec(leg.cash_delta_usdt))
        return existing_delta, {
            "leg_id": int(leg.id),
            "leg_index": int(leg.leg_index),
            "type": "live_usdt_earn_redeem",
            "idempotent": True,
            "cash_delta_usdt": str(existing_delta),
            "status": leg.status,
            "order_id": leg.bybit_order_id,
            "order_link_id": leg.order_link_id,
        }

    if leg.status != SALE_LEG_STATUS_BUFFER_AVAILABLE:
        return ZERO, None

    target_cash_usdt = _max_zero(dec(leg.target_cash_usdt))
    if target_cash_usdt <= ZERO:
        return ZERO, None

    product = resolve_flexible_saving_product(
        client,
        coin="USDT",
    )
    product_id = product.product_id
    product_precision = product.precision
    if product_precision is None:
        raise NegativeSaleExecutionError(
            "live_usdt_earn_product_precision_missing: "
            f"leg_id={leg.id}, product_id={product_id}, raw={product.raw}"
        )

    available_amount = total_flexible_saving_available_amount(
        client,
        coin="USDT",
        product_id=product_id,
    )

    needed_from_earn = compute_needed_from_earn(
        required_master_usdt=_max_zero(dec(sale_batch.required_master_usdt)),
        already_realized_cash_usdt=_max_zero(dec(already_realized_cash_usdt)),
        target_cash_usdt=target_cash_usdt,
        available_amount=available_amount,
    )

    if needed_from_earn <= ZERO:
        return ZERO, None

    amount_str = format_bybit_earn_amount(
        needed_from_earn,
        precision=int(product_precision),
        rounding="up",
    )
    amount = Decimal(amount_str)

    if amount <= ZERO:
        raise NegativeSaleExecutionError(
            "live_usdt_earn_redeem_rounded_amount_not_positive: "
            f"leg_id={leg.id}, needed_from_earn={needed_from_earn}, "
            f"amount_str={amount_str}, precision={product_precision}"
        )

    if amount > available_amount:
        raise NegativeSaleExecutionError(
            "live_usdt_earn_redeem_rounded_amount_exceeds_available: "
            f"leg_id={leg.id}, product_id={product_id}, "
            f"availableAmount={available_amount}, needed_from_earn={needed_from_earn}, "
            f"rounded_amount={amount}, precision={product_precision}"
        )

    order_link_id = deterministic_negative_sale_earn_redeem_link_id(
        sale_batch_id=int(sale_batch.id),
        leg_id=int(leg.id),
        leg_index=int(leg.leg_index),
    )

    if len(order_link_id) > 36:
        raise NegativeSaleExecutionError(
            "live_usdt_earn_redeem_order_link_id_too_long: "
            f"leg_id={leg.id}, len={len(order_link_id)}, order_link_id={order_link_id}"
        )

    existing = query_earn_order_by_link_id(
        client,
        order_link_id=order_link_id,
        category="FlexibleSaving",
        product_id=product_id,
    )

    if existing is not None:
        if _earn_order_success(existing):
            redeemed = min(amount, _max_zero(existing.amount))
            return redeemed, apply_live_usdt_earn_redeem_to_leg(
                leg=leg,
                order=existing,
                redeemed_usdt=redeemed,
                order_link_id=order_link_id,
                now=now,
            )

        if _earn_order_pending(existing):
            raise NegativeSaleExecutionError(
                "live_usdt_earn_redeem_pending_fail_closed: "
                f"leg_id={leg.id}, order_link_id={order_link_id}, "
                f"order_id={existing.order_id}"
            )

        if _earn_order_failed(existing):
            raise NegativeSaleExecutionError(
                "live_usdt_earn_redeem_failed: "
                f"leg_id={leg.id}, order_link_id={order_link_id}, "
                f"order_id={existing.order_id}"
            )

        raise NegativeSaleExecutionError(
            "live_usdt_earn_redeem_unknown_existing_status: "
            f"leg_id={leg.id}, order_link_id={order_link_id}, "
            f"status={existing.status}"
        )

    require_bybit_earn_redeem_guard(
        db,
        fund_id=int(sale_batch.fund_id),
        settlement_batch_id=int(settlement_batch.id),
        amount_usdt=amount,
        request_id=order_link_id,
        metadata={
            "sale_batch_id": int(sale_batch.id),
            "sale_leg_id": int(leg.id),
            "leg_index": int(leg.leg_index),
            "coin": "USDT",
            "product_id": product_id,
            "account_type": "FUND",
            "operation": "negative_sale_usdt_earn_redeem",
            "product_precision": int(product_precision),
            "amount_str": amount_str,
            "availableAmount": str(available_amount),
            "target_cash_usdt": str(target_cash_usdt),
            "needed_from_earn": str(needed_from_earn),
        },
    )

    placed = place_flexible_saving_redeem_order(
        client,
        amount=amount,
        amount_str=amount_str,
        product_id=product_id,
        order_link_id=order_link_id,
        coin="USDT",
        account_type="FUND",
        product_precision=int(product_precision),
        available_amount=available_amount,
        target_cash_usdt=target_cash_usdt,
        needed_from_earn=needed_from_earn,
    )

    confirmed = query_earn_order_by_link_id(
        client,
        order_link_id=order_link_id,
        category="FlexibleSaving",
        product_id=product_id,
    )

    if confirmed is None:
        raise NegativeSaleExecutionError(
            "live_usdt_earn_redeem_uncertain_no_history_fail_closed: "
            f"leg_id={leg.id}, order_link_id={order_link_id}, "
            f"placed_order_id={placed.order_id}"
        )

    if not _earn_order_success(confirmed):
        raise NegativeSaleExecutionError(
            "live_usdt_earn_redeem_uncertain_not_success_fail_closed: "
            f"leg_id={leg.id}, order_link_id={order_link_id}, "
            f"order_id={confirmed.order_id}, status={confirmed.status}"
        )

    redeemed = min(amount, _max_zero(confirmed.amount))
    return redeemed, apply_live_usdt_earn_redeem_to_leg(
        leg=leg,
        order=confirmed,
        redeemed_usdt=redeemed,
        order_link_id=order_link_id,
        now=now,
    )


def execute_initial_usdt_earn_redeem_live(
    db: Session,
    *,
    client: BybitV5Client,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    legs: list[FundNegativeSaleLeg],
    initial_cash_usdt: Decimal,
    now: datetime,
) -> tuple[Decimal, list[dict[str, Any]]]:
    results: list[dict[str, Any]] = []
    total = ZERO

    for leg in legs:
        if leg.leg_type != "usdt_earn_buffer":
            continue

        redeemed, result = execute_live_usdt_earn_redeem_guarded(
            db,
            client=client,
            sale_batch=sale_batch,
            settlement_batch=settlement_batch,
            leg=leg,
            already_realized_cash_usdt=initial_cash_usdt + total,
            now=now,
        )
        total += redeemed

        if result is not None:
            results.append(result)

    return total, results


def cash_available_from_cash_like_legs(
    legs: list[FundNegativeSaleLeg],
) -> Decimal:
    total = ZERO

    cash_leg_types = {
        "unified_usdt_cash",
        "fund_wallet_cash",
        "cash",
        "usdt_cash",
    }

    for leg in legs:
        leg_type = str(leg.leg_type or "").strip().lower()
        leg_group = str(leg.leg_group or "").strip().lower()
        status = str(leg.status or "").strip().lower()

        # USDT Earn buffer is counted only after mock redemption.
        if leg_type == "usdt_earn_buffer":
            continue

        is_cash_like = (
            leg_type in cash_leg_types
            or status == SALE_LEG_STATUS_CASH_AVAILABLE
            or (
                leg_group == "cash_like"
                and leg_type not in {"usdt_earn_buffer"}
            )
        )

        if not is_cash_like:
            continue

        cash_amount = max(
            _max_zero(dec(leg.cash_delta_usdt)),
            _max_zero(dec(leg.current_usd_value)),
            _max_zero(dec(leg.target_cash_usdt)),
            _max_zero(dec(leg.expected_cash_delta_usdt)),
        )

        total += cash_amount

    return total


def sum_initial_sale_cash_delta(
    computations: list[LegExecutionComputation],
) -> Decimal:
    return sum((_max_zero(item.cash_delta_usdt) for item in computations), ZERO)


def shortage_usdt(
    *,
    required_master_usdt: Decimal,
    available_usdt: Decimal,
) -> Decimal:
    return _max_zero(required_master_usdt - available_usdt)


def mock_additional_usdt_earn_redeem(
    *,
    shortage: Decimal,
    mock_execution: NegativeSaleExecutionMock,
) -> Decimal:
    if shortage <= ZERO:
        return ZERO

    redeemable = _max_zero(mock_execution.usdt_earn.additional_redeemable_usdt)
    requested_fill = _max_zero(mock_execution.usdt_earn.additional_redeem_fill_usdt)

    return min(shortage, redeemable, requested_fill)

def remaining_value_for_extra_sale(leg: FundNegativeSaleLeg) -> Decimal:
    current_value = _max_zero(dec(leg.current_usd_value))
    executed_cash = _max_zero(dec(leg.cash_delta_usdt))

    if current_value > ZERO:
        return _max_zero(current_value - executed_cash)

    target_cash = _max_zero(dec(leg.target_cash_usdt))
    return _max_zero(target_cash - executed_cash)


def leg_is_extra_sale_candidate(leg: FundNegativeSaleLeg) -> bool:
    if not leg.eligible:
        return False

    if not leg.use_for_deficit_cover:
        return False

    if str(leg.instrument_status or "").lower() not in {"", "trading"}:
        return False

    if leg.leg_type == "short_option_buyback":
        return False

    if leg.status in {
        SALE_LEG_STATUS_SKIPPED_LIQUIDITY_GUARD,
        SALE_LEG_STATUS_SKIPPED_MARGIN_GUARD,
        SALE_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    }:
        return False

    return remaining_value_for_extra_sale(leg) > ZERO


def select_largest_extra_sale_candidate(
    legs: list[FundNegativeSaleLeg],
) -> FundNegativeSaleLeg | None:
    candidates = [leg for leg in legs if leg_is_extra_sale_candidate(leg)]

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda leg: (
            remaining_value_for_extra_sale(leg),
            int(leg.id or 0),
        ),
    )
    
def extra_sale_target_usdt(
    *,
    shortage: Decimal,
    buffer_pct: Decimal,
) -> Decimal:
    if shortage <= ZERO:
        return ZERO

    return shortage * (ONE + buffer_pct / HUNDRED)


def _extra_sale_symbol_mock(
    *,
    candidate: FundNegativeSaleLeg,
    mock_execution: NegativeSaleExecutionMock,
) -> SymbolExecutionMock:
    candidate_symbol = _leg_symbol_key(candidate)
    preferred_symbol = (
        _symbol_key(mock_execution.extra_sale.preferred_symbol)
        if mock_execution.extra_sale.preferred_symbol
        else None
    )

    if mock_execution.extra_sale.enabled and preferred_symbol == candidate_symbol:
        return SymbolExecutionMock(
            symbol=candidate_symbol,
            category=candidate.category,
            last_price=mock_execution.extra_sale.last_price,
            best_bid=mock_execution.extra_sale.best_bid,
            best_ask=mock_execution.extra_sale.best_ask,
            available_liquidity_usdt=mock_execution.extra_sale.available_liquidity_usdt,
            available_liquidity_qty=mock_execution.extra_sale.available_liquidity_qty,
            native_strategy_supported=supports_native_iceberg(candidate.category),
            mock_fill_ratio=mock_execution.extra_sale.mock_fill_ratio,
            fee_usdt=mock_execution.extra_sale.fee_usdt,
            raw=mock_execution.extra_sale.raw,
        )

    return _symbol_mock_for_leg(
        leg=candidate,
        mock_execution=mock_execution,
    )
    
def compute_extra_sale_execution(
    *,
    sale_batch_id: int,
    candidate: FundNegativeSaleLeg,
    shortage: Decimal,
    mock_execution: NegativeSaleExecutionMock,
    execution_round: str = "extra",
) -> LegExecutionComputation | None:
    if shortage <= ZERO:
        return None

    if not mock_execution.extra_sale.enabled:
        return None

    remaining_value = remaining_value_for_extra_sale(candidate)
    target_cash_usdt = min(
        remaining_value,
        extra_sale_target_usdt(
            shortage=shortage,
            buffer_pct=mock_execution.extra_largest_asset_buffer_pct,
        ),
    )

    if target_cash_usdt <= ZERO:
        return None

    symbol_mock = _extra_sale_symbol_mock(
        candidate=candidate,
        mock_execution=mock_execution,
    )

    base_key = deterministic_leg_key(
        sale_batch_id=sale_batch_id,
        leg_id=int(candidate.id),
        leg_index=int(candidate.leg_index),
    )
    deterministic_key = f"{base_key}:extra"

    actual_execution_mode, transition_status = choose_mock_execution_mode(
        target_cash_usdt=target_cash_usdt,
        category=candidate.category or symbol_mock.category,
        symbol_mock=symbol_mock,
        corridor_pct=mock_execution.sell_corridor_pct,
    )

    fill_ratio = max(min(symbol_mock.mock_fill_ratio, ONE), ZERO)
    filled_usdt = target_cash_usdt * fill_ratio
    unfilled_usdt = _max_zero(target_cash_usdt - filled_usdt)
    fee_usdt = min(symbol_mock.fee_usdt, filled_usdt)
    cash_delta_usdt = _max_zero(filled_usdt - fee_usdt)

    avg_fill_price = symbol_mock.last_price if filled_usdt > ZERO else None
    filled_qty = (
        filled_usdt / avg_fill_price
        if avg_fill_price is not None and avg_fill_price > ZERO
        else None
    )

    suborders_json = None
    planned_suborders = None
    executed_suborders = None

    if actual_execution_mode == "sliced_ioc_fallback":
        suborders_json = sliced_ioc_suborders(
            deterministic_key=deterministic_key,
            target_cash_usdt=target_cash_usdt,
            slices=mock_execution.slices,
        )
        planned_suborders = mock_execution.slices
        executed_suborders = mock_execution.slices

    return LegExecutionComputation(
        leg_id=int(candidate.id),
        leg_index=int(candidate.leg_index),
        deterministic_key=deterministic_key,
        symbol=candidate.symbol,
        category=candidate.category or symbol_mock.category,
        planned_cash_usdt=target_cash_usdt,
        actual_execution_mode=actual_execution_mode,
        execution_round=execution_round,
        status=SALE_LEG_STATUS_EXTRA_SALE_MOCKED,
        transition_status=transition_status,
        last_price=symbol_mock.last_price,
        best_bid=symbol_mock.best_bid,
        best_ask=symbol_mock.best_ask,
        corridor_pct=mock_execution.sell_corridor_pct,
        available_liquidity_usdt=symbol_mock.available_liquidity_usdt,
        available_liquidity_qty=symbol_mock.available_liquidity_qty,
        filled_qty=filled_qty,
        filled_usdt=filled_usdt,
        avg_fill_price=avg_fill_price,
        fill_ratio=fill_ratio,
        unfilled_usdt=unfilled_usdt,
        fee_usdt=fee_usdt,
        cash_delta_usdt=cash_delta_usdt,
        planned_suborders=planned_suborders,
        executed_suborders=executed_suborders,
        suborders_json=suborders_json,
        mock_execution_json={
            "mock_id": mock_execution.mock_id,
            "mock_only": True,
            "type": "extra_largest_asset_sale",
            "transition_status": transition_status,
            "final_status": SALE_LEG_STATUS_EXTRA_SALE_MOCKED,
            "shortage_usdt": str(shortage),
            "target_cash_usdt": str(target_cash_usdt),
            "remaining_value_usdt": str(remaining_value),
            "no_real_bybit_calls": True,
            "no_trades": True,
            "no_real_strategy_orders": True,
        },
    )
    
def build_extra_sale_leg_row(
    *,
    candidate: FundNegativeSaleLeg,
    computation: LegExecutionComputation,
    leg_index: int,
    now: datetime,
) -> FundNegativeSaleLeg:
    row = FundNegativeSaleLeg(
        sale_batch_id=candidate.sale_batch_id,
        settlement_batch_id=candidate.settlement_batch_id,
        fund_id=candidate.fund_id,
        leg_index=leg_index,
        leg_group=candidate.leg_group,
        leg_type="extra_largest_asset_sale",
        coin=candidate.coin,
        symbol=candidate.symbol,
        category=candidate.category,
        side=candidate.side,
        location=candidate.location,
        current_qty=candidate.current_qty,
        current_size=candidate.current_size,
        current_usd_value=remaining_value_for_extra_sale(candidate),
        current_notional_usd=candidate.current_notional_usd,
        source_weight=None,
        target_cash_usdt=computation.planned_cash_usdt,
        target_qty=computation.filled_qty,
        expected_cash_delta_usdt=computation.cash_delta_usdt,
        eligible=True,
        eligibility_reason=(
            "Extra largest eligible asset sale for remaining negative-net shortage."
        ),
        use_for_deficit_cover=True,
        instrument_status=candidate.instrument_status,
        min_order_passed=True,
        liquidity_check_required=True,
        margin_guard_required=bool(candidate.margin_guard_required),
        planned_execution_mode="mock_extra_largest_asset_sale",
        order_link_id=None,
        strategy_id=None,
        actual_execution_mode=computation.actual_execution_mode,
        execution_round=computation.execution_round,
        deterministic_key=computation.deterministic_key,
        bybit_order_id=None,
        bybit_strategy_id=None,
        planned_suborders=computation.planned_suborders,
        executed_suborders=computation.executed_suborders,
        suborders_json=computation.suborders_json,
        mock_execution_json=computation.mock_execution_json,
        last_price=computation.last_price,
        best_bid=computation.best_bid,
        best_ask=computation.best_ask,
        corridor_pct=computation.corridor_pct,
        available_liquidity_usdt=computation.available_liquidity_usdt,
        available_liquidity_qty=computation.available_liquidity_qty,
        filled_qty=computation.filled_qty,
        filled_usdt=computation.filled_usdt,
        avg_fill_price=computation.avg_fill_price,
        fill_ratio=computation.fill_ratio,
        unfilled_usdt=computation.unfilled_usdt,
        fee_usdt=computation.fee_usdt,
        cash_delta_usdt=computation.cash_delta_usdt,
        sent_at=now,
        confirmed_at=now,
        failed_at=None,
        execution_error=computation.execution_error,
        status=SALE_LEG_STATUS_EXTRA_SALE_MOCKED,
        error=None,
        created_at=now,
        updated_at=now,
    )

    return row

def apply_execution_reconciliation_to_batch(
    *,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    initial_cash_usdt: Decimal,
    initial_earn_redeemed_usdt: Decimal,
    initial_sale_executed_usdt: Decimal,
    available_after_initial_sales: Decimal,
    shortage_after_initial_sales: Decimal,
    additional_earn_redeemed_usdt: Decimal,
    extra_sale_required_usdt: Decimal,
    extra_sale_target: Decimal,
    extra_sale_executed_usdt: Decimal,
    final_available_usdt: Decimal,
    final_shortage_usdt: Decimal,
    final_surplus_usdt: Decimal,
    execution_json: dict[str, Any],
    reconciliation_json: dict[str, Any],
    now: datetime,
) -> None:
    total_earn_redeemed = initial_earn_redeemed_usdt + additional_earn_redeemed_usdt
    initial_cash_like = initial_cash_usdt + initial_earn_redeemed_usdt

    sale_batch.execution_started_at = sale_batch.execution_started_at or now
    sale_batch.execution_completed_at = now

    sale_batch.available_usdt_before_execution = initial_cash_usdt
    sale_batch.initial_cash_like_usdt = initial_cash_like
    sale_batch.usdt_earn_redeemed_usdt = total_earn_redeemed
    sale_batch.initial_sale_executed_usdt = initial_sale_executed_usdt
    sale_batch.available_usdt_after_initial_sales = available_after_initial_sales
    sale_batch.shortage_after_initial_sales_usdt = shortage_after_initial_sales
    sale_batch.extra_sale_required_usdt = extra_sale_required_usdt
    sale_batch.extra_sale_target_usdt = extra_sale_target
    sale_batch.extra_sale_executed_usdt = extra_sale_executed_usdt
    sale_batch.final_available_usdt = final_available_usdt
    sale_batch.final_shortage_usdt = final_shortage_usdt
    sale_batch.final_surplus_usdt = final_surplus_usdt

    sale_batch.execution_json = _json_dict(execution_json)
    sale_batch.reconciliation_json = _json_dict(reconciliation_json)

    report_json = dict(sale_batch.report_json or {})
    report_json["execution"] = sale_batch.execution_json
    report_json["reconciliation"] = sale_batch.reconciliation_json
    sale_batch.report_json = _json_dict(report_json)

    if final_shortage_usdt > ZERO:
        sale_batch.status = SALE_BATCH_STATUS_SALE_EXECUTION_FAILED_REQUIRES_REVIEW
        settlement_batch.status = BATCH_STATUS_FAILED_REQUIRES_REVIEW
        sale_batch.error = "negative_net_sale_execution_final_shortage"
        settlement_batch.error = "negative_net_sale_execution_final_shortage"
    else:
        if extra_sale_executed_usdt > ZERO:
            sale_batch.status = SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED_WITH_EXTRA_SALE
        else:
            sale_batch.status = SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED

        settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_SALE_EXECUTED
        sale_batch.error = None
        settlement_batch.error = None

    sale_batch.updated_at = now
    settlement_batch.updated_at = now
    
def build_execution_json(
    *,
    mock_execution: NegativeSaleExecutionMock,
    initial_computations: list[LegExecutionComputation],
    extra_computation: LegExecutionComputation | None,
    additional_earn_redeemed_usdt: Decimal,
) -> dict[str, Any]:
    return {
        "mock_id": mock_execution.mock_id,
        "mock_only": True,
        "stage": "23.3",
        "initial_sale_legs": [item.to_dict() for item in initial_computations],
        "extra_sale": (
            extra_computation.to_dict()
            if extra_computation is not None
            else None
        ),
        "additional_earn_redeemed_usdt": str(additional_earn_redeemed_usdt),
        "safety": {
            "no_real_bybit_calls": True,
            "no_trades": True,
            "no_real_strategy_orders": True,
            "no_bybit_transfers_or_withdrawals": True,
            "no_bsc_transfers": True,
            "no_accounting_finalization": True,
        },
    }


def build_reconciliation_json(
    *,
    required_master_usdt: Decimal,
    initial_cash_usdt: Decimal,
    initial_earn_redeemed_usdt: Decimal,
    initial_sale_executed_usdt: Decimal,
    available_after_initial_sales: Decimal,
    shortage_after_initial_sales: Decimal,
    additional_earn_redeemed_usdt: Decimal,
    extra_sale_required_usdt: Decimal,
    extra_sale_target: Decimal,
    extra_sale_executed_usdt: Decimal,
    final_available_usdt: Decimal,
    final_shortage_usdt: Decimal,
    final_surplus_usdt: Decimal,
) -> dict[str, Any]:
    return {
        "required_master_usdt": str(required_master_usdt),
        "initial_cash_usdt": str(initial_cash_usdt),
        "initial_earn_redeemed_usdt": str(initial_earn_redeemed_usdt),
        "initial_sale_executed_usdt": str(initial_sale_executed_usdt),
        "available_after_initial_sales": str(available_after_initial_sales),
        "shortage_after_initial_sales_usdt": str(shortage_after_initial_sales),
        "additional_earn_redeemed_usdt": str(additional_earn_redeemed_usdt),
        "extra_sale_required_usdt": str(extra_sale_required_usdt),
        "extra_sale_target_usdt": str(extra_sale_target),
        "extra_sale_executed_usdt": str(extra_sale_executed_usdt),
        "final_available_usdt": str(final_available_usdt),
        "final_shortage_usdt": str(final_shortage_usdt),
        "final_surplus_usdt": str(final_surplus_usdt),
        "surplus_policy": "final_surplus_remains_fund_usdt_cash",
    }
    
def execute_deficit_correction_mock(
    *,
    sale_batch: FundNegativeSaleBatch,
    legs: list[FundNegativeSaleLeg],
    required_master_usdt: Decimal,
    available_after_initial_sales: Decimal,
    shortage_after_initial_sales: Decimal,
    mock_execution: NegativeSaleExecutionMock,
    now: datetime,
) -> tuple[Decimal, Decimal, Decimal, Decimal, LegExecutionComputation | None, FundNegativeSaleLeg | None]:
    additional_earn_redeemed_usdt = mock_additional_usdt_earn_redeem(
        shortage=shortage_after_initial_sales,
        mock_execution=mock_execution,
    )

    available_after_additional_earn = (
        available_after_initial_sales + additional_earn_redeemed_usdt
    )

    extra_sale_required_usdt = shortage_usdt(
        required_master_usdt=required_master_usdt,
        available_usdt=available_after_additional_earn,
    )

    extra_sale_target = extra_sale_target_usdt(
        shortage=extra_sale_required_usdt,
        buffer_pct=mock_execution.extra_largest_asset_buffer_pct,
    )

    extra_computation: LegExecutionComputation | None = None
    extra_row: FundNegativeSaleLeg | None = None
    extra_sale_executed_usdt = ZERO

    if extra_sale_required_usdt > ZERO:
        candidate = select_largest_extra_sale_candidate(legs)

        if candidate is not None:
            extra_computation = compute_extra_sale_execution(
                sale_batch_id=int(sale_batch.id),
                candidate=candidate,
                shortage=extra_sale_required_usdt,
                mock_execution=mock_execution,
            )

            if extra_computation is not None:
                next_leg_index = max((int(leg.leg_index) for leg in legs), default=0) + 1

                extra_row = build_extra_sale_leg_row(
                    candidate=candidate,
                    computation=extra_computation,
                    leg_index=next_leg_index,
                    now=now,
                )

                extra_sale_executed_usdt = _max_zero(extra_computation.cash_delta_usdt)

    return (
        additional_earn_redeemed_usdt,
        extra_sale_required_usdt,
        extra_sale_target,
        extra_sale_executed_usdt,
        extra_computation,
        extra_row,
    )
    
def execute_negative_sale_plan_mock(
    db: Session,
    *,
    sale_batch_id: int,
    mock_execution: NegativeSaleExecutionMock,
    now: datetime | None = None,
) -> NegativeSaleExecutionResult:
    now = now or utcnow()

    _validate_stage23_3_safety(mock_execution)

    sale_batch = _lock_sale_batch(
        db,
        sale_batch_id=sale_batch_id,
    )
    status_before = str(sale_batch.status)

    settlement_batch = _lock_settlement_batch(
        db,
        settlement_batch_id=int(sale_batch.settlement_batch_id),
    )
    settlement_status_before = str(settlement_batch.status)

    fund = _get_fund(
        db,
        fund_id=int(sale_batch.fund_id),
    )

    legs = _load_sale_legs_for_update(
        db,
        sale_batch_id=int(sale_batch.id),
    )

    _validate_sale_execution_input(
        sale_batch=sale_batch,
        settlement_batch=settlement_batch,
        legs=legs,
    )

    sale_batch.status = SALE_BATCH_STATUS_SALE_EXECUTION_PROCESSING
    sale_batch.execution_started_at = sale_batch.execution_started_at or now
    sale_batch.updated_at = now

    settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_SALE_PROCESSING
    settlement_batch.updated_at = now

    db.add(sale_batch)
    db.add(settlement_batch)
    db.flush()

    required_master_usdt = _max_zero(dec(sale_batch.required_master_usdt))

    initial_cash_usdt = cash_available_from_cash_like_legs(legs)

    initial_earn_redeemed_usdt = mock_initial_usdt_earn_redeem(
        legs=legs,
        mock_execution=mock_execution,
        now=now,
    )

    initial_computations = execute_initial_sale_legs_mock(
        sale_batch=sale_batch,
        legs=legs,
        mock_execution=mock_execution,
        now=now,
    )

    initial_sale_executed_usdt = sum_initial_sale_cash_delta(initial_computations)

    available_after_initial_sales = (
        initial_cash_usdt
        + initial_earn_redeemed_usdt
        + initial_sale_executed_usdt
    )

    shortage_after_initial_sales = shortage_usdt(
        required_master_usdt=required_master_usdt,
        available_usdt=available_after_initial_sales,
    )

    (
        additional_earn_redeemed_usdt,
        extra_sale_required_usdt,
        extra_sale_target,
        extra_sale_executed_usdt,
        extra_computation,
        extra_row,
    ) = execute_deficit_correction_mock(
        sale_batch=sale_batch,
        legs=legs,
        required_master_usdt=required_master_usdt,
        available_after_initial_sales=available_after_initial_sales,
        shortage_after_initial_sales=shortage_after_initial_sales,
        mock_execution=mock_execution,
        now=now,
    )

    if extra_row is not None:
        db.add(extra_row)
        legs.append(extra_row)

    final_available_usdt = (
        available_after_initial_sales
        + additional_earn_redeemed_usdt
        + extra_sale_executed_usdt
    )
    final_shortage_usdt = shortage_usdt(
        required_master_usdt=required_master_usdt,
        available_usdt=final_available_usdt,
    )
    final_surplus_usdt = _max_zero(final_available_usdt - required_master_usdt)

    execution_json = build_execution_json(
        mock_execution=mock_execution,
        initial_computations=initial_computations,
        extra_computation=extra_computation,
        additional_earn_redeemed_usdt=additional_earn_redeemed_usdt,
    )

    reconciliation_json = build_reconciliation_json(
        required_master_usdt=required_master_usdt,
        initial_cash_usdt=initial_cash_usdt,
        initial_earn_redeemed_usdt=initial_earn_redeemed_usdt,
        initial_sale_executed_usdt=initial_sale_executed_usdt,
        available_after_initial_sales=available_after_initial_sales,
        shortage_after_initial_sales=shortage_after_initial_sales,
        additional_earn_redeemed_usdt=additional_earn_redeemed_usdt,
        extra_sale_required_usdt=extra_sale_required_usdt,
        extra_sale_target=extra_sale_target,
        extra_sale_executed_usdt=extra_sale_executed_usdt,
        final_available_usdt=final_available_usdt,
        final_shortage_usdt=final_shortage_usdt,
        final_surplus_usdt=final_surplus_usdt,
    )

    apply_execution_reconciliation_to_batch(
        sale_batch=sale_batch,
        settlement_batch=settlement_batch,
        initial_cash_usdt=initial_cash_usdt,
        initial_earn_redeemed_usdt=initial_earn_redeemed_usdt,
        initial_sale_executed_usdt=initial_sale_executed_usdt,
        available_after_initial_sales=available_after_initial_sales,
        shortage_after_initial_sales=shortage_after_initial_sales,
        additional_earn_redeemed_usdt=additional_earn_redeemed_usdt,
        extra_sale_required_usdt=extra_sale_required_usdt,
        extra_sale_target=extra_sale_target,
        extra_sale_executed_usdt=extra_sale_executed_usdt,
        final_available_usdt=final_available_usdt,
        final_shortage_usdt=final_shortage_usdt,
        final_surplus_usdt=final_surplus_usdt,
        execution_json=execution_json,
        reconciliation_json=reconciliation_json,
        now=now,
    )

    db.add(sale_batch)
    db.add(settlement_batch)
    db.flush()

    return NegativeSaleExecutionResult(
        ok=final_shortage_usdt <= ZERO,
        sale_batch_id=int(sale_batch.id),
        settlement_batch_id=int(settlement_batch.id),
        fund_id=int(fund.id),
        fund_code=str(fund.code),
        status_before=status_before,
        status_after=str(sale_batch.status),
        settlement_status_before=settlement_status_before,
        settlement_status_after=str(settlement_batch.status),
        final_available_usdt=final_available_usdt,
        final_shortage_usdt=final_shortage_usdt,
        final_surplus_usdt=final_surplus_usdt,
        executed_leg_count=len(initial_computations) + (1 if extra_row is not None else 0),
        error=sale_batch.error,
        diagnostics={
            "mock_only": True,
            "initial_cash_usdt": str(initial_cash_usdt),
            "initial_earn_redeemed_usdt": str(initial_earn_redeemed_usdt),
            "initial_sale_executed_usdt": str(initial_sale_executed_usdt),
            "shortage_after_initial_sales_usdt": str(shortage_after_initial_sales),
            "additional_earn_redeemed_usdt": str(additional_earn_redeemed_usdt),
            "extra_sale_executed_usdt": str(extra_sale_executed_usdt),
            "no_real_bybit_calls": True,
            "no_trades": True,
            "no_real_strategy_orders": True,
            "no_bybit_transfers_or_withdrawals": True,
            "no_bsc_transfers": True,
            "no_accounting_finalization": True,
        },
    )
