from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.bybit.client import BybitV5Client
from app.bybit.instruments import (
    BybitInstrumentInfoError,
    normalize_order_quantity,
    query_instrument_info,
)
from app.bybit.transferable_balance import (
    BybitTransferableBalanceError,
    query_unified_transferable_balance,
)
from app.config import settings
from app.models import (
    FundNegativeSaleBatch,
    FundNegativeSaleLeg,
    FundSettlementBatch,
)
from app.settlement.negative_sale_correction_policy import (
    CorrectionRoundDecision,
    compute_spot_correction_target_usdt,
    confirmed_shortage_usdt,
    evaluate_next_correction_round,
    select_largest_eligible_spot_source,
)
from app.settlement.negative_sale_execution_types import (
    ZERO,
    utcnow,
)
from app.settlement.negative_sale_live_leg_service import (
    NegativeSaleLiveLegStepResult,
    resume_live_leg_once,
)
from app.settlement.negative_sale_live_preflight import (
    NegativeSaleLivePreflightError,
    build_live_negative_sale_preflight,
)
from app.settlement.negative_sale_live_persistence import (
    archive_terminal_intent_and_activate_next_round,
    persist_new_correction_intent_without_previous,
    validated_terminal_intent_history,
)
from app.settlement.negative_sale_order_intent import (
    NegativeSaleOrderIntentError,
    build_negative_sale_order_intent,
    validate_negative_sale_order_intent,
)
from app.settlement.negative_sale_order_runtime import (
    prepared_intent_runtime_summary,
)
from app.settlement.negative_sale_order_state import (
    NegativeSaleOrderStateError,
    sale_leg_plan_snapshot,
)


class NegativeSaleLiveBatchServiceError(
    RuntimeError
):
    pass


ORDER_CATEGORIES = {
    "spot",
    "linear",
    "inverse",
    "option",
}

DERIVATIVE_ORDER_CATEGORIES = {
    "linear",
    "inverse",
    "option",
}

PENDING_EXTERNAL_ORDER_STATUSES = {
    "submitted",
    "acknowledged",
    "pending_confirmation",
    "partially_filled_pending_confirmation",
}


@dataclass(frozen=True)
class NegativeSaleLiveBatchStepResult:
    sale_batch_id: int
    settlement_batch_id: int
    action: str
    reason: str

    candidate_leg_count: int
    active_leg_id: int | None
    posted: bool

    all_order_legs_terminal: bool
    has_pending_action: bool
    requires_review: bool

    confirmed_available_usdt: (
        Decimal | None
    )
    shortage_usdt: Decimal | None

    correction_decision: (
        dict[str, Any] | None
    )
    transferable_balance: (
        dict[str, Any] | None
    )
    leg_step: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)

        for key in (
            "confirmed_available_usdt",
            "shortage_usdt",
        ):
            value = result[key]
            result[key] = (
                str(value)
                if value is not None
                else None
            )

        return result


def _decimal(
    value: Any,
    *,
    field_name: str,
) -> Decimal:
    if isinstance(value, bool):
        raise NegativeSaleLiveBatchServiceError(
            f"{field_name} must not be bool"
        )

    if isinstance(value, float):
        raise NegativeSaleLiveBatchServiceError(
            f"{field_name} must not be float"
        )

    try:
        result = (
            value
            if isinstance(value, Decimal)
            else Decimal(str(value))
        )
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ) as exc:
        raise NegativeSaleLiveBatchServiceError(
            f"{field_name} is not Decimal"
        ) from exc

    if not result.is_finite():
        raise NegativeSaleLiveBatchServiceError(
            f"{field_name} must be finite"
        )

    return result


def _validated_intent(
    leg: FundNegativeSaleLeg,
) -> dict[str, Any] | None:
    raw = leg.suborders_json

    if raw is None:
        return None

    if not isinstance(raw, dict):
        raise NegativeSaleLiveBatchServiceError(
            "Sale leg suborders_json must "
            f"be a dict: leg_id={leg.id}"
        )

    intent = deepcopy(raw)

    try:
        validate_negative_sale_order_intent(
            intent
        )
    except NegativeSaleOrderIntentError as exc:
        raise NegativeSaleLiveBatchServiceError(
            "Sale leg durable intent is "
            f"invalid: leg_id={leg.id}, "
            f"error={exc}"
        ) from exc

    return intent


def _plan_snapshot(
    *,
    sale_batch: FundNegativeSaleBatch,
    leg: FundNegativeSaleLeg,
) -> dict[str, Any]:
    try:
        return sale_leg_plan_snapshot(
            sale_batch=sale_batch,
            leg=leg,
        )
    except NegativeSaleOrderStateError as exc:
        raise NegativeSaleLiveBatchServiceError(
            "Cannot recover immutable plan "
            f"leg: leg_id={leg.id}, "
            f"error={exc}"
        ) from exc


def _preflight_normalized_qty(
    plan_leg: dict[str, Any],
) -> Decimal:
    preflight = plan_leg.get(
        "order_quantity_preflight"
    )

    if not isinstance(preflight, dict):
        return ZERO

    value = preflight.get(
        "normalized_qty"
    )

    if value is None or value == "":
        return ZERO

    result = _decimal(
        value,
        field_name=(
            "order_quantity_preflight."
            "normalized_qty"
        ),
    )

    return max(result, ZERO)


def _is_order_candidate(
    *,
    sale_batch: FundNegativeSaleBatch,
    leg: FundNegativeSaleLeg,
) -> bool:
    intent = _validated_intent(leg)

    if intent is not None:
        return True

    plan_leg = _plan_snapshot(
        sale_batch=sale_batch,
        leg=leg,
    )

    category = str(
        plan_leg.get("category")
        or ""
    ).strip().lower()

    if category not in ORDER_CATEGORIES:
        return False

    if (
        plan_leg.get(
            "requires_fund_to_unified_transfer"
        )
        is True
    ):
        return False

    raw = plan_leg.get("raw")

    if isinstance(raw, dict):
        if (
            raw.get(
                "requires_fund_to_unified_transfer"
            )
            is True
        ):
            return False

    return (
        _preflight_normalized_qty(
            plan_leg
        )
        > ZERO
    )


def _candidate_category(
    *,
    sale_batch: FundNegativeSaleBatch,
    leg: FundNegativeSaleLeg,
) -> str:
    intent = _validated_intent(leg)

    if intent is not None:
        return str(
            intent.get("category")
            or ""
        ).strip().lower()

    plan_leg = _plan_snapshot(
        sale_batch=sale_batch,
        leg=leg,
    )

    return str(
        plan_leg.get("category")
        or ""
    ).strip().lower()


def _candidate_priority(
    *,
    sale_batch: FundNegativeSaleBatch,
    leg: FundNegativeSaleLeg,
) -> int:
    category = _candidate_category(
        sale_batch=sale_batch,
        leg=leg,
    )

    if (
        category
        in DERIVATIVE_ORDER_CATEGORIES
    ):
        return 0

    if category == "spot":
        return 1

    return 2


def _execution_round_for_leg(
    leg: FundNegativeSaleLeg,
) -> int:
    intent = _validated_intent(leg)

    if intent is None:
        return 0

    raw_round = intent.get(
        "execution_round"
    )

    if raw_round is None:
        raise NegativeSaleLiveBatchServiceError(
            "Durable intent has no "
            "execution_round: "
            f"leg_id={leg.id}"
        )

    if isinstance(raw_round, bool):
        raise NegativeSaleLiveBatchServiceError(
            "execution_round must not "
            f"be bool: leg_id={leg.id}"
        )

    try:
        execution_round = int(raw_round)
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise NegativeSaleLiveBatchServiceError(
            "execution_round must be int: "
            f"leg_id={leg.id}"
        ) from exc

    if execution_round < 0:
        raise NegativeSaleLiveBatchServiceError(
            "execution_round must be "
            f"non-negative: leg_id={leg.id}"
        )

    return execution_round


def order_candidate_legs(
    *,
    sale_batch: FundNegativeSaleBatch,
    legs: list[FundNegativeSaleLeg],
) -> list[FundNegativeSaleLeg]:
    candidates = [
        leg
        for leg in legs
        if _is_order_candidate(
            sale_batch=sale_batch,
            leg=leg,
        )
    ]

    candidates.sort(
        key=lambda item: (
            _candidate_priority(
                sale_batch=sale_batch,
                leg=item,
            ),
            int(item.leg_index),
            int(item.id),
        )
    )

    return candidates


def _intent_summary(
    leg: FundNegativeSaleLeg,
) -> dict[str, Any] | None:
    intent = _validated_intent(leg)

    if intent is None:
        return None

    return prepared_intent_runtime_summary(
        intent
    )


def _intent_has_pending_external_action(
    intent: dict[str, Any],
) -> bool:
    raw_suborders = intent.get(
        "suborders"
    )

    if not isinstance(
        raw_suborders,
        list,
    ):
        raise NegativeSaleLiveBatchServiceError(
            "Intent suborders must be "
            "a list"
        )

    for index, item in enumerate(
        raw_suborders
    ):
        if not isinstance(item, dict):
            raise (
                NegativeSaleLiveBatchServiceError(
                    f"suborders[{index}] "
                    "must be a dict"
                )
            )

        status = str(
            item.get("status")
            or "prepared"
        ).strip()

        if (
            status
            in PENDING_EXTERNAL_ORDER_STATUSES
        ):
            return True

    return False


def _completed_correction_rounds(
    candidates: list[
        FundNegativeSaleLeg
    ],
) -> int:
    rounds: dict[
        int,
        list[bool],
    ] = {}

    for leg in candidates:
        try:
            history = (
                validated_terminal_intent_history(
                    getattr(
                        leg,
                        "mock_execution_json",
                        None,
                    )
                )
            )
        except Exception as exc:
            raise (
                NegativeSaleLiveBatchServiceError(
                    "Invalid durable intent "
                    "history: "
                    f"leg_id={leg.id}, "
                    f"error={exc}"
                )
            ) from exc

        for entry in history:
            round_index = int(
                entry["execution_round"]
            )

            if round_index <= 0:
                continue

            rounds.setdefault(
                round_index,
                [],
            ).append(True)

        intent = _validated_intent(
            leg
        )

        if intent is None:
            continue

        round_raw = intent.get(
            "execution_round"
        )

        if round_raw is None:
            raise (
                NegativeSaleLiveBatchServiceError(
                    "Durable intent has no "
                    "execution_round: "
                    f"leg_id={leg.id}"
                )
            )

        round_index = int(
            round_raw
        )

        if round_index <= 0:
            continue

        summary = (
            prepared_intent_runtime_summary(
                intent
            )
        )

        rounds.setdefault(
            round_index,
            [],
        ).append(
            bool(
                summary["all_terminal"]
            )
        )

    return sum(
        1
        for states in rounds.values()
        if states and all(states)
    )


def _correction_decision(
    *,
    sale_batch: FundNegativeSaleBatch,
    confirmed_available_usdt: Decimal,
    completed_rounds: int,
) -> CorrectionRoundDecision:
    return evaluate_next_correction_round(
        required_master_usdt=_decimal(
            sale_batch.required_master_usdt,
            field_name=(
                "required_master_usdt"
            ),
        ),
        confirmed_available_usdt=(
            confirmed_available_usdt
        ),
        completed_rounds=completed_rounds,
        max_rounds=(
            settings
            .NEGATIVE_NET_LIVE_CORRECTION_MAX_ROUNDS
        ),
        has_pending_action=False,
    )


def _confirmed_balance_state(
    *,
    client: BybitV5Client,
    sale_batch: FundNegativeSaleBatch,
) -> tuple[
    Any,
    Decimal,
    Decimal,
]:
    balance = (
        query_unified_transferable_balance(
            client,
            coin="USDT",
            destination_account_type="FUND",
        )
    )

    confirmed_available = (
        balance
        .confirmed_transferable_amount
    )

    required = _decimal(
        sale_batch.required_master_usdt,
        field_name=(
            "required_master_usdt"
        ),
    )

    shortage = confirmed_shortage_usdt(
        required_master_usdt=required,
        confirmed_available_usdt=(
            confirmed_available
        ),
    )

    return (
        balance,
        confirmed_available,
        shortage,
    )


def _confirmed_exec_qty_for_intent(
    intent: dict[str, Any],
) -> Decimal:
    summary = (
        prepared_intent_runtime_summary(
            intent
        )
    )

    return _decimal(
        summary["aggregate_exec_qty"],
        field_name="aggregate_exec_qty",
    )


def _total_confirmed_exec_qty_for_leg(
    leg: FundNegativeSaleLeg,
) -> Decimal:
    try:
        history = (
            validated_terminal_intent_history(
                getattr(
                    leg,
                    "mock_execution_json",
                    None,
                )
            )
        )
    except Exception as exc:
        raise NegativeSaleLiveBatchServiceError(
            "Cannot recover confirmed "
            "execution history: "
            f"leg_id={leg.id}, error={exc}"
        ) from exc

    total = ZERO

    for entry in history:
        intent = entry.get("intent")

        if not isinstance(intent, dict):
            raise NegativeSaleLiveBatchServiceError(
                "Archived correction entry "
                "has no intent dict: "
                f"leg_id={leg.id}"
            )

        total += (
            _confirmed_exec_qty_for_intent(
                intent
            )
        )

    active_intent = _validated_intent(
        leg
    )

    if active_intent is not None:
        total += (
            _confirmed_exec_qty_for_intent(
                active_intent
            )
        )

    return total


def _remaining_spot_qty(
    *,
    sale_batch: FundNegativeSaleBatch,
    leg: FundNegativeSaleLeg,
) -> Decimal:
    plan_leg = _plan_snapshot(
        sale_batch=sale_batch,
        leg=leg,
    )

    current_qty_raw = plan_leg.get(
        "current_qty"
    )

    if current_qty_raw is None:
        raise NegativeSaleLiveBatchServiceError(
            "Spot correction source has no "
            "snapshot current_qty: "
            f"leg_id={leg.id}"
        )

    current_qty = _decimal(
        current_qty_raw,
        field_name="current_qty",
    )
    confirmed_exec_qty = (
        _total_confirmed_exec_qty_for_leg(
            leg
        )
    )

    if confirmed_exec_qty > current_qty:
        raise NegativeSaleLiveBatchServiceError(
            "Confirmed sold quantity exceeds "
            "snapshot quantity: "
            f"leg_id={leg.id}, "
            f"current_qty={current_qty}, "
            f"confirmed_exec_qty="
            f"{confirmed_exec_qty}"
        )

    return current_qty - confirmed_exec_qty


def _query_spot_best_bid(
    client: BybitV5Client,
    *,
    symbol: str,
) -> Decimal:
    normalized_symbol = str(
        symbol
    ).strip().upper()

    public_get = getattr(
        client,
        "public_get",
        None,
    )

    params = {
        "category": "spot",
        "symbol": normalized_symbol,
    }

    response = (
        public_get(
            "/v5/market/tickers",
            params,
        )
        if callable(public_get)
        else client.get(
            "/v5/market/tickers",
            params,
        )
    )

    if not isinstance(response, dict):
        raise NegativeSaleLiveBatchServiceError(
            "Spot ticker response must be "
            "a dict"
        )

    if response.get("retCode") not in {
        None,
        0,
        "0",
    }:
        raise NegativeSaleLiveBatchServiceError(
            "Spot ticker query failed: "
            f"symbol={normalized_symbol}, "
            f"retCode={response.get('retCode')}"
        )

    result = response.get("result")

    if not isinstance(result, dict):
        raise NegativeSaleLiveBatchServiceError(
            "Spot ticker result must be "
            "a dict"
        )

    rows = result.get("list")

    if not isinstance(rows, list):
        raise NegativeSaleLiveBatchServiceError(
            "Spot ticker result.list must "
            "be a list"
        )

    matching_rows = [
        row
        for row in rows
        if isinstance(row, dict)
        and str(
            row.get("symbol")
            or ""
        ).strip().upper()
        == normalized_symbol
    ]

    if len(matching_rows) != 1:
        raise NegativeSaleLiveBatchServiceError(
            "Spot ticker response must "
            "contain exactly one matching "
            f"symbol: {normalized_symbol}"
        )

    best_bid = _decimal(
        matching_rows[0].get(
            "bid1Price"
        ),
        field_name="bid1Price",
    )

    if best_bid <= ZERO:
        raise NegativeSaleLiveBatchServiceError(
            "Spot bid1Price must be "
            f"positive: {normalized_symbol}"
        )

    return best_bid


def _spot_correction_sources(
    *,
    client: BybitV5Client,
    sale_batch: FundNegativeSaleBatch,
    legs: list[FundNegativeSaleLeg],
    now: datetime,
) -> list[dict[str, Any]]:
    sources: list[
        dict[str, Any]
    ] = []

    for leg in legs:
        plan_leg = _plan_snapshot(
            sale_batch=sale_batch,
            leg=leg,
        )

        category = str(
            plan_leg.get("category")
            or leg.category
            or ""
        ).strip().lower()

        if category != "spot":
            continue

        if plan_leg.get("eligible") is not True:
            continue

        if (
            plan_leg.get(
                "use_for_deficit_cover"
            )
            is not True
        ):
            continue

        raw = plan_leg.get("raw")
        raw = (
            dict(raw)
            if isinstance(raw, dict)
            else {}
        )

        requires_transfer = (
            plan_leg.get(
                "requires_fund_to_unified_transfer"
            )
            is True
            or raw.get(
                "requires_fund_to_unified_transfer"
            )
            is True
        )

        if requires_transfer:
            continue

        location = str(
            plan_leg.get("location")
            or leg.location
            or ""
        ).strip().upper() or None

        if location in {
            "FUND",
            "FUND_WALLET",
            "FUNDING",
            "FUNDING_WALLET",
        }:
            continue

        symbol = str(
            plan_leg.get("symbol")
            or leg.symbol
            or ""
        ).strip().upper()

        if not symbol:
            raise (
                NegativeSaleLiveBatchServiceError(
                    "Eligible spot correction "
                    "source has no symbol: "
                    f"leg_id={leg.id}"
                )
            )

        snapshot_remaining_qty = (
            _remaining_spot_qty(
                sale_batch=sale_batch,
                leg=leg,
            )
        )

        if snapshot_remaining_qty <= ZERO:
            continue

        try:
            instrument = query_instrument_info(
                client,
                category="spot",
                symbol=symbol,
                captured_at=now,
            )
        except BybitInstrumentInfoError as exc:
            raise (
                NegativeSaleLiveBatchServiceError(
                    "Correction source "
                    "instrument query failed: "
                    f"symbol={symbol}, "
                    f"error={exc}"
                )
            ) from exc

        if not instrument.trading:
            raise (
                NegativeSaleLiveBatchServiceError(
                    "Correction source "
                    "instrument is not trading: "
                    f"{symbol}"
                )
            )

        if not instrument.preflight_complete:
            raise (
                NegativeSaleLiveBatchServiceError(
                    "Correction source "
                    "instrument filters are "
                    "incomplete: "
                    f"symbol={symbol}, "
                    "reasons="
                    f"{list(instrument.completeness_reasons)}"
                )
            )

        base_coin = str(
            instrument.base_coin or ""
        ).strip().upper()

        if not base_coin:
            raise (
                NegativeSaleLiveBatchServiceError(
                    "Correction source "
                    "instrument has no base coin: "
                    f"symbol={symbol}"
                )
            )

        try:
            transferable_balance = (
                query_unified_transferable_balance(
                    client,
                    coin=base_coin,
                    destination_account_type=(
                        "FUND"
                    ),
                )
            )
        except (
            BybitTransferableBalanceError
        ) as exc:
            raise (
                NegativeSaleLiveBatchServiceError(
                    "Correction source live "
                    "balance query failed: "
                    f"symbol={symbol}, "
                    f"base_coin={base_coin}, "
                    f"error={exc}"
                )
            ) from exc

        confirmed_live_qty = (
            transferable_balance
            .confirmed_transferable_amount
        )

        live_available_qty = min(
            snapshot_remaining_qty,
            confirmed_live_qty,
        )

        if live_available_qty <= ZERO:
            continue

        best_bid = _query_spot_best_bid(
            client,
            symbol=symbol,
        )

        try:
            capacity = normalize_order_quantity(
                instrument=instrument,
                requested_qty=(
                    live_available_qty
                ),
                available_qty=(
                    live_available_qty
                ),
                price=best_bid,
            )
        except BybitInstrumentInfoError as exc:
            raise (
                NegativeSaleLiveBatchServiceError(
                    "Correction source live "
                    "capacity normalization "
                    "failed: "
                    f"symbol={symbol}, "
                    f"error={exc}"
                )
            ) from exc

        if (
            not capacity.eligible
            or capacity.normalized_qty
            <= ZERO
            or not capacity.slices
        ):
            # A real balance can legitimately
            # be below current min-order or
            # precision requirements. Such a
            # source is not correction-eligible.
            continue

        live_sellable_qty = (
            capacity.normalized_qty
        )

        remaining_value = (
            capacity.normalized_notional
        )

        if (
            remaining_value is None
            or remaining_value <= ZERO
        ):
            remaining_value = (
                live_sellable_qty
                * best_bid
            )

        if remaining_value <= ZERO:
            continue

        sources.append(
            {
                "source_key": (
                    f"sale-leg:{int(leg.id)}"
                ),
                "leg_id": int(leg.id),
                "symbol": symbol,
                "category": "spot",
                "asset_type": str(
                    raw.get("asset_type")
                    or "spot"
                ).strip().lower(),
                "location": location,
                "eligible": True,
                "use_for_deficit_cover": True,
                "requires_fund_to_unified_transfer": (
                    False
                ),
                # Keep remaining_qty for the
                # existing correction policy,
                # but make it the live,
                # normalized sellable amount.
                "remaining_qty": str(
                    live_sellable_qty
                ),
                "snapshot_remaining_qty": str(
                    snapshot_remaining_qty
                ),
                "confirmed_live_qty": str(
                    confirmed_live_qty
                ),
                "live_available_qty": str(
                    live_available_qty
                ),
                "live_sellable_qty": str(
                    live_sellable_qty
                ),
                "base_coin": base_coin,
                "best_bid": str(
                    best_bid
                ),
                "remaining_sellable_usdt": (
                    str(remaining_value)
                ),
                "instrument_snapshot": (
                    instrument.to_dict()
                ),
                "transferable_balance": (
                    transferable_balance
                    .to_dict()
                ),
                "capacity_preflight": (
                    capacity.to_dict()
                ),
            }
        )

    return sources


def _prepare_spot_correction_round(
    db: Session,
    *,
    client: BybitV5Client,
    sale_batch: FundNegativeSaleBatch,
    legs: list[FundNegativeSaleLeg],
    decision: CorrectionRoundDecision,
    confirmed_available_usdt: Decimal,
    now: datetime,
) -> dict[str, Any] | None:
    if (
        not decision.allowed
        or decision.next_round is None
    ):
        return None

    source_rows = _spot_correction_sources(
        client=client,
        sale_batch=sale_batch,
        legs=legs,
        now=now,
    )

    selected = (
        select_largest_eligible_spot_source(
            source_rows
        )
    )

    if selected is None:
        return None

    selected_leg_id = int(
        selected.raw["leg_id"]
    )

    selected_leg = next(
        (
            leg
            for leg in legs
            if int(leg.id)
            == selected_leg_id
        ),
        None,
    )

    if selected_leg is None:
        raise NegativeSaleLiveBatchServiceError(
            "Selected correction leg "
            "cannot be recovered"
        )

    source_live_sellable_qty = _decimal(
        selected.raw[
            "live_sellable_qty"
        ],
        field_name=(
            "live_sellable_qty"
        ),
    )
    source_best_bid = _decimal(
        selected.raw["best_bid"],
        field_name="best_bid",
    )
    snapshot_remaining_qty = _decimal(
        selected.raw[
            "snapshot_remaining_qty"
        ],
        field_name=(
            "snapshot_remaining_qty"
        ),
    )

    buffer_pct = _decimal(
        settings
        .NEGATIVE_NET_LIVE_CORRECTION_BUFFER_PCT,
        field_name=(
            "NEGATIVE_NET_LIVE_"
            "CORRECTION_BUFFER_PCT"
        ),
    )

    correction_target = (
        compute_spot_correction_target_usdt(
            shortage_usdt=(
                decision.shortage_usdt
            ),
            remaining_sellable_usdt=(
                selected
                .remaining_sellable_usdt
            ),
            oversell_cap_usdt=(
                selected
                .remaining_sellable_usdt
            ),
            buffer_pct=buffer_pct,
        )
    )

    if correction_target <= ZERO:
        return None

    source_requested_qty = min(
        correction_target
        / source_best_bid,
        source_live_sellable_qty,
    )

    if source_requested_qty <= ZERO:
        return None

    plan_leg = _plan_snapshot(
        sale_batch=sale_batch,
        leg=selected_leg,
    )

    planned_close_side = str(
        plan_leg.get("close_side")
        or plan_leg.get("side")
        or selected_leg.side
        or ""
    ).strip()

    if (
        planned_close_side.lower()
        != "sell"
    ):
        raise (
            NegativeSaleLiveBatchServiceError(
                "Spot correction close_side "
                "must be Sell"
            )
        )

    try:
        live_preflight = (
            build_live_negative_sale_preflight(
                client,
                category="spot",
                symbol=selected.symbol,
                requested_qty=(
                    source_requested_qty
                ),
                planned_close_side=(
                    planned_close_side
                ),
                captured_at=now,
            )
        )
    except (
        NegativeSaleLivePreflightError
    ) as exc:
        raise (
            NegativeSaleLiveBatchServiceError(
                "Correction final live "
                "preflight failed: "
                f"symbol={selected.symbol}, "
                f"error={exc}"
            )
        ) from exc

    if live_preflight.category != "spot":
        raise (
            NegativeSaleLiveBatchServiceError(
                "Correction live preflight "
                "category must be spot"
            )
        )

    if (
        live_preflight.symbol
        != selected.symbol
    ):
        raise (
            NegativeSaleLiveBatchServiceError(
                "Correction live preflight "
                "symbol mismatch"
            )
        )

    if (
        live_preflight.close_side
        != "Sell"
    ):
        raise (
            NegativeSaleLiveBatchServiceError(
                "Correction live preflight "
                "close_side must be Sell"
            )
        )

    if (
        live_preflight.normalized_qty
        <= ZERO
        or not live_preflight.slices
    ):
        raise (
            NegativeSaleLiveBatchServiceError(
                "Correction live preflight "
                "returned no executable "
                "quantity"
            )
        )

    if (
        live_preflight.normalized_qty
        > snapshot_remaining_qty
    ):
        raise (
            NegativeSaleLiveBatchServiceError(
                "Correction live quantity "
                "exceeds snapshot remaining "
                "quantity"
            )
        )

    target_cash = (
        live_preflight
        .normalized_notional
    )

    if (
        target_cash is None
        or target_cash <= ZERO
    ):
        raise (
            NegativeSaleLiveBatchServiceError(
                "Correction live normalized "
                "notional must be positive"
            )
        )

    try:
        new_intent = (
            build_negative_sale_order_intent(
                sale_batch_id=int(
                    sale_batch.id
                ),
                leg_id=int(
                    selected_leg.id
                ),
                execution_round=int(
                    decision.next_round
                ),
                category="spot",
                symbol=selected.symbol,
                position_side=(
                    live_preflight
                    .position_side
                ),
                close_side=(
                    live_preflight
                    .close_side
                ),
                position_idx=(
                    live_preflight
                    .position_idx
                ),
                reduce_only=(
                    live_preflight
                    .reduce_only
                ),
                market_unit=(
                    live_preflight
                    .market_unit
                ),
                requested_qty=(
                    live_preflight
                    .requested_qty
                ),
                normalized_qty=(
                    live_preflight
                    .normalized_qty
                ),
                target_cash_usdt=(
                    target_cash
                ),
                slices=(
                    live_preflight.slices
                ),
                instrument_snapshot=(
                    live_preflight
                    .instrument_snapshot
                ),
                position_snapshot={
                    **dict(
                        live_preflight
                        .position_snapshot
                    ),
                    "correction_policy": (
                        decision.policy_version
                    ),
                    "source_key": (
                        selected.source_key
                    ),
                    "snapshot_remaining_qty": (
                        str(
                            snapshot_remaining_qty
                        )
                    ),
                    "source_live_sellable_qty": (
                        str(
                            source_live_sellable_qty
                        )
                    ),
                    "source_best_bid": str(
                        source_best_bid
                    ),
                    "confirmed_available_usdt": (
                        str(
                            confirmed_available_usdt
                        )
                    ),
                    "shortage_usdt": str(
                        decision.shortage_usdt
                    ),
                    "source_scan": dict(
                        selected.raw
                    ),
                    "final_live_preflight": (
                        live_preflight.to_dict()
                    ),
                },
                prepared_at=now,
            )
        ).to_dict()
    except NegativeSaleOrderIntentError as exc:
        raise NegativeSaleLiveBatchServiceError(
            "Correction intent creation "
            f"failed: {exc}"
        ) from exc

    if selected_leg.suborders_json is None:
        persist_new_correction_intent_without_previous(
            db,
            leg=selected_leg,
            new_intent=new_intent,
            now=now,
        )
    else:
        archive_terminal_intent_and_activate_next_round(
            db,
            leg=selected_leg,
            new_intent=new_intent,
            now=now,
        )

    return {
        "leg_id": int(
            selected_leg.id
        ),
        "execution_round": int(
            decision.next_round
        ),
        "source_key": selected.source_key,
        "symbol": selected.symbol,
        "snapshot_remaining_qty": str(
            snapshot_remaining_qty
        ),
        "source_live_sellable_qty": str(
            source_live_sellable_qty
        ),
        "source_best_bid": str(
            source_best_bid
        ),
        "final_live_available_qty": str(
            live_preflight.available_qty
        ),
        "final_live_price": str(
            live_preflight.price
        ),
        "correction_target_usdt": (
            str(correction_target)
        ),
        "target_cash_usdt": str(
            target_cash
        ),
        "requested_qty": str(
            live_preflight.requested_qty
        ),
        "normalized_qty": str(
            live_preflight.normalized_qty
        ),
        "slices": [
            str(value)
            for value
            in live_preflight.slices
        ],
        "live_preflight": (
            live_preflight.to_dict()
        ),
        "intent_fingerprint": (
            new_intent[
                "intent_fingerprint"
            ]
        ),
        "posted": False,
        "no_transfer": True,
        "no_withdrawal": True,
        "no_bsc_action": True,
        "no_final_accounting": True,
    }


def _resume_candidate_phase_once(
    db: Session,
    *,
    client: BybitV5Client,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    candidates: list[
        FundNegativeSaleLeg
    ],
    total_candidate_count: int,
    now: datetime,
    confirmed_available_usdt: (
        Decimal | None
    ) = None,
    shortage_usdt: (
        Decimal | None
    ) = None,
) -> (
    NegativeSaleLiveBatchStepResult
    | None
):
    for leg in candidates:
        summary = _intent_summary(leg)

        if (
            summary is not None
            and summary["has_failure"]
        ):
            return (
                NegativeSaleLiveBatchStepResult(
                    sale_batch_id=int(
                        sale_batch.id
                    ),
                    settlement_batch_id=int(
                        settlement_batch.id
                    ),
                    action=(
                        "review_required"
                    ),
                    reason=(
                        "terminal_order_failure"
                    ),
                    candidate_leg_count=(
                        total_candidate_count
                    ),
                    active_leg_id=int(
                        leg.id
                    ),
                    posted=False,
                    all_order_legs_terminal=(
                        False
                    ),
                    has_pending_action=False,
                    requires_review=True,
                    confirmed_available_usdt=(
                        confirmed_available_usdt
                    ),
                    shortage_usdt=(
                        shortage_usdt
                    ),
                    correction_decision=None,
                    transferable_balance=None,
                    leg_step=None,
                )
            )

        if (
            summary is not None
            and summary["all_terminal"]
        ):
            continue

        execution_round = (
            _execution_round_for_leg(
                leg
            )
        )

        leg_step = resume_live_leg_once(
            db,
            client=client,
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            leg=leg,
            execution_round=(
                execution_round
            ),
            now=now,
        )

        has_pending = (
            _intent_has_pending_external_action(
                leg_step.intent
            )
        )

        return (
            NegativeSaleLiveBatchStepResult(
                sale_batch_id=int(
                    sale_batch.id
                ),
                settlement_batch_id=int(
                    settlement_batch.id
                ),
                action="order_step",
                reason=leg_step.reason,
                candidate_leg_count=(
                    total_candidate_count
                ),
                active_leg_id=int(
                    leg.id
                ),
                posted=bool(
                    leg_step.posted
                ),
                all_order_legs_terminal=(
                    False
                ),
                has_pending_action=(
                    has_pending
                ),
                requires_review=False,
                confirmed_available_usdt=(
                    confirmed_available_usdt
                ),
                shortage_usdt=(
                    shortage_usdt
                ),
                correction_decision=None,
                transferable_balance=None,
                leg_step=(
                    leg_step.to_dict()
                ),
            )
        )

    return None


def resume_negative_sale_order_batch_once(
    db: Session,
    *,
    client: BybitV5Client,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    legs: list[FundNegativeSaleLeg],
    now: datetime | None = None,
) -> NegativeSaleLiveBatchStepResult:
    effective_now = now or utcnow()

    candidates = order_candidate_legs(
        sale_batch=sale_batch,
        legs=legs,
    )

    derivative_candidates = [
        leg
        for leg in candidates
        if _candidate_category(
            sale_batch=sale_batch,
            leg=leg,
        )
        in DERIVATIVE_ORDER_CATEGORIES
    ]

    spot_candidates = [
        leg
        for leg in candidates
        if _candidate_category(
            sale_batch=sale_batch,
            leg=leg,
        )
        == "spot"
    ]

    derivative_step = (
        _resume_candidate_phase_once(
            db,
            client=client,
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            candidates=(
                derivative_candidates
            ),
            total_candidate_count=len(
                candidates
            ),
            now=effective_now,
        )
    )

    if derivative_step is not None:
        return derivative_step

    (
        pre_spot_balance,
        pre_spot_available,
        pre_spot_shortage,
    ) = _confirmed_balance_state(
        client=client,
        sale_batch=sale_batch,
    )

    if pre_spot_shortage <= ZERO:
        completed_rounds = (
            _completed_correction_rounds(
                candidates
            )
        )

        decision = _correction_decision(
            sale_batch=sale_batch,
            confirmed_available_usdt=(
                pre_spot_available
            ),
            completed_rounds=(
                completed_rounds
            ),
        )

        return (
            NegativeSaleLiveBatchStepResult(
                sale_batch_id=int(
                    sale_batch.id
                ),
                settlement_batch_id=int(
                    settlement_batch.id
                ),
                action="balance_check",
                reason=(
                    "confirmed_balance_"
                    "covers_requirement_"
                    "before_spot"
                ),
                candidate_leg_count=len(
                    candidates
                ),
                active_leg_id=None,
                posted=False,
                all_order_legs_terminal=True,
                has_pending_action=False,
                requires_review=False,
                confirmed_available_usdt=(
                    pre_spot_available
                ),
                shortage_usdt=(
                    pre_spot_shortage
                ),
                correction_decision=(
                    decision.to_dict()
                ),
                transferable_balance=(
                    pre_spot_balance.to_dict()
                ),
                leg_step=None,
            )
        )

    spot_step = (
        _resume_candidate_phase_once(
            db,
            client=client,
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            candidates=spot_candidates,
            total_candidate_count=len(
                candidates
            ),
            now=effective_now,
            confirmed_available_usdt=(
                pre_spot_available
            ),
            shortage_usdt=(
                pre_spot_shortage
            ),
        )
    )

    if spot_step is not None:
        return spot_step

    (
        final_balance,
        final_available,
        final_shortage,
    ) = _confirmed_balance_state(
        client=client,
        sale_batch=sale_batch,
    )

    completed_rounds = (
        _completed_correction_rounds(
            candidates
        )
    )

    decision = _correction_decision(
        sale_batch=sale_batch,
        confirmed_available_usdt=(
            final_available
        ),
        completed_rounds=(
            completed_rounds
        ),
    )

    if decision.allowed:
        correction = (
            _prepare_spot_correction_round(
                db,
                client=client,
                sale_batch=sale_batch,
                legs=legs,
                decision=decision,
                confirmed_available_usdt=(
                    final_available
                ),
                now=effective_now,
            )
        )

        if correction is None:
            return NegativeSaleLiveBatchStepResult(
                sale_batch_id=int(
                    sale_batch.id
                ),
                settlement_batch_id=int(
                    settlement_batch.id
                ),
                action="review_required",
                reason=(
                    "no_eligible_spot_"
                    "correction_source"
                ),
                candidate_leg_count=len(
                    candidates
                ),
                active_leg_id=None,
                posted=False,
                all_order_legs_terminal=True,
                has_pending_action=False,
                requires_review=True,
                confirmed_available_usdt=(
                    final_available
                ),
                shortage_usdt=(
                    final_shortage
                ),
                correction_decision=(
                    decision.to_dict()
                ),
                transferable_balance=(
                    final_balance.to_dict()
                ),
                leg_step=None,
            )

        return NegativeSaleLiveBatchStepResult(
            sale_batch_id=int(
                sale_batch.id
            ),
            settlement_batch_id=int(
                settlement_batch.id
            ),
            action="correction_prepared",
            reason=(
                "spot_correction_intent_"
                "prepared"
            ),
            candidate_leg_count=len(
                candidates
            ),
            active_leg_id=int(
                correction["leg_id"]
            ),
            posted=False,
            all_order_legs_terminal=False,
            has_pending_action=False,
            requires_review=False,
            confirmed_available_usdt=(
                final_available
            ),
            shortage_usdt=(
                final_shortage
            ),
            correction_decision=(
                decision.to_dict()
            ),
            transferable_balance=(
                final_balance.to_dict()
            ),
            leg_step=correction,
        )

    return NegativeSaleLiveBatchStepResult(
        sale_batch_id=int(
            sale_batch.id
        ),
        settlement_batch_id=int(
            settlement_batch.id
        ),
        action="balance_check",
        reason=decision.reason,
        candidate_leg_count=len(
            candidates
        ),
        active_leg_id=None,
        posted=False,
        all_order_legs_terminal=True,
        has_pending_action=False,
        requires_review=False,
        confirmed_available_usdt=(
            final_available
        ),
        shortage_usdt=(
            final_shortage
        ),
        correction_decision=(
            decision.to_dict()
        ),
        transferable_balance=(
            final_balance.to_dict()
        ),
        leg_step=None,
    )