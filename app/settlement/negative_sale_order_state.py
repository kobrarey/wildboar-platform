from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    FundNegativeSaleBatch,
    FundNegativeSaleLeg,
)
from app.settlement.negative_sale_order_intent import (
    NegativeSaleOrderIntentError,
    build_negative_sale_order_intent,
    validate_negative_sale_order_intent,
)
from app.settlement.negative_sale_snapshot import dec
from app.settlement.negative_sale_execution_types import (
    ZERO,
    utcnow,
)


class NegativeSaleOrderStateError(
    RuntimeError
):
    pass


def _dict_or_empty(
    value: Any,
) -> dict[str, Any]:
    return (
        dict(value)
        if isinstance(value, dict)
        else {}
    )


def _optional_int(
    value: Any,
) -> int | None:
    if value is None or value == "":
        return None

    try:
        return int(str(value))
    except (TypeError, ValueError) as exc:
        raise NegativeSaleOrderStateError(
            f"Invalid integer value: {value!r}"
        ) from exc


def _required_positive_decimal(
    value: Any,
    *,
    field_name: str,
) -> Decimal:
    result = dec(value)

    if result <= ZERO:
        raise NegativeSaleOrderStateError(
            f"{field_name} must be positive"
        )

    return result


def sale_leg_plan_snapshot(
    *,
    sale_batch: FundNegativeSaleBatch,
    leg: FundNegativeSaleLeg,
) -> dict[str, Any]:
    plan_json = _dict_or_empty(
        sale_batch.plan_json
    )
    plan_legs = plan_json.get("legs")

    if not isinstance(plan_legs, list):
        raise NegativeSaleOrderStateError(
            "sale_batch.plan_json.legs "
            "must be a list"
        )

    leg_index = int(leg.leg_index)

    if leg_index <= 0:
        raise NegativeSaleOrderStateError(
            "leg_index must be positive"
        )

    offset = leg_index - 1

    if offset >= len(plan_legs):
        raise NegativeSaleOrderStateError(
            "leg_index is outside "
            "sale_batch.plan_json.legs"
        )

    raw_plan_leg = plan_legs[offset]

    if not isinstance(raw_plan_leg, dict):
        raise NegativeSaleOrderStateError(
            "Plan leg must be a dict"
        )

    plan_leg = dict(raw_plan_leg)

    identity_checks = {
        "leg_type": leg.leg_type,
        "symbol": leg.symbol,
        "category": leg.category,
        "side": leg.side,
    }

    for field_name, orm_value in (
        identity_checks.items()
    ):
        plan_value = plan_leg.get(
            field_name
        )

        if (
            plan_value is None
            or orm_value is None
        ):
            continue

        if (
            str(plan_value).strip().lower()
            != str(orm_value).strip().lower()
        ):
            raise NegativeSaleOrderStateError(
                "Plan leg identity mismatch: "
                f"field={field_name}, "
                f"plan={plan_value!r}, "
                f"orm={orm_value!r}"
            )

    return plan_leg


def _quantity_preflight(
    plan_leg: dict[str, Any],
) -> dict[str, Any]:
    preflight = plan_leg.get(
        "order_quantity_preflight"
    )

    if isinstance(preflight, dict):
        return dict(preflight)

    raw = _dict_or_empty(
        plan_leg.get("raw")
    )
    preflight = raw.get(
        "order_quantity_preflight"
    )

    return _dict_or_empty(preflight)


def _intent_slices(
    *,
    preflight: dict[str, Any],
    normalized_qty: Decimal,
) -> tuple[Decimal, ...]:
    raw_slices = preflight.get("slices")

    if not isinstance(
        raw_slices,
        (list, tuple),
    ):
        return (normalized_qty,)

    parsed = tuple(
        _required_positive_decimal(
            value,
            field_name=(
                f"preflight.slices[{index}]"
            ),
        )
        for index, value
        in enumerate(raw_slices)
    )

    if not parsed:
        return (normalized_qty,)

    if sum(parsed, ZERO) != normalized_qty:
        raise NegativeSaleOrderStateError(
            "Preflight slices do not sum "
            "to normalized target quantity"
        )

    return parsed


def build_prepared_intent_for_leg(
    *,
    sale_batch: FundNegativeSaleBatch,
    leg: FundNegativeSaleLeg,
    execution_round: int,
    prepared_at: datetime | None = None,
) -> dict[str, Any]:
    if leg.id is None:
        raise NegativeSaleOrderStateError(
            "Sale leg must be persisted "
            "before intent preparation"
        )

    if sale_batch.id is None:
        raise NegativeSaleOrderStateError(
            "Sale batch must be persisted "
            "before intent preparation"
        )

    existing = leg.suborders_json

    if existing is not None:
        if not isinstance(existing, dict):
            raise NegativeSaleOrderStateError(
                "Existing suborders_json "
                "must be a dict"
            )

        try:
            validate_negative_sale_order_intent(
                existing
            )
        except NegativeSaleOrderIntentError as exc:
            raise NegativeSaleOrderStateError(
                "Existing prepared intent "
                f"is invalid: {exc}"
            ) from exc

        existing_round = existing.get(
            "execution_round"
        )

        if existing_round is None:
            raise NegativeSaleOrderStateError(
                "Existing prepared intent "
                "execution_round is missing"
            )

        if int(existing_round) != int(
            execution_round
        ):
            raise NegativeSaleOrderStateError(
                "Existing prepared intent "
                "belongs to another round"
            )

        return dict(existing)

    if (
        leg.bybit_order_id
        or leg.sent_at is not None
        or int(leg.executed_suborders or 0)
        > 0
    ):
        raise NegativeSaleOrderStateError(
            "External execution evidence "
            "exists without prepared intent"
        )

    plan_leg = sale_leg_plan_snapshot(
        sale_batch=sale_batch,
        leg=leg,
    )
    raw = _dict_or_empty(
        plan_leg.get("raw")
    )
    preflight = _quantity_preflight(
        plan_leg
    )

    category = str(
        plan_leg.get("category")
        or leg.category
        or ""
    ).strip()

    symbol = str(
        plan_leg.get("symbol")
        or leg.symbol
        or ""
    ).strip()

    close_side = str(
        plan_leg.get("close_side")
        or plan_leg.get("side")
        or leg.side
        or ""
    ).strip()

    position_side_raw = (
        plan_leg.get("position_side")
        or raw.get("position_side")
    )
    position_side = (
        str(position_side_raw)
        if position_side_raw is not None
        else None
    )

    position_idx = _optional_int(
        plan_leg.get("position_idx")
        if plan_leg.get("position_idx")
        is not None
        else raw.get("position_idx")
    )

    reduce_only_raw = raw.get(
        "reduce_only"
    )
    reduce_only = (
        bool(reduce_only_raw)
        if reduce_only_raw is not None
        else (
            True
            if category.lower()
            in {
                "linear",
                "inverse",
                "option",
            }
            else None
        )
    )

    market_unit_raw = raw.get(
        "market_unit"
    )
    market_unit = (
        str(market_unit_raw)
        if market_unit_raw
        else None
    )

    requested_qty = (
        preflight.get("requested_qty")
        or plan_leg.get("target_qty")
        or leg.target_qty
    )
    normalized_qty_raw = (
        preflight.get("normalized_qty")
        or plan_leg.get("target_qty")
        or leg.target_qty
    )

    requested = (
        _required_positive_decimal(
            requested_qty,
            field_name="requested_qty",
        )
    )
    normalized_qty = (
        _required_positive_decimal(
            normalized_qty_raw,
            field_name="normalized_qty",
        )
    )

    slices = _intent_slices(
        preflight=preflight,
        normalized_qty=normalized_qty,
    )

    instrument_snapshot = (
        _dict_or_empty(
            raw.get("instrument_info")
        )
    )

    position_snapshot = {
        "current_qty": (
            plan_leg.get("current_qty")
        ),
        "current_size": (
            plan_leg.get("current_size")
        ),
        "position_side": position_side,
        "close_side": close_side,
        "position_idx": position_idx,
        "current_notional_usd": (
            plan_leg.get(
                "current_notional_usd"
            )
        ),
        "exposure_notional_usdt": (
            plan_leg.get(
                "exposure_notional_usdt"
            )
        ),
        "location": (
            plan_leg.get("location")
        ),
    }

    try:
        intent = (
            build_negative_sale_order_intent(
                sale_batch_id=int(
                    sale_batch.id
                ),
                leg_id=int(leg.id),
                execution_round=int(
                    execution_round
                ),
                category=category,
                symbol=symbol,
                position_side=position_side,
                close_side=close_side,
                position_idx=position_idx,
                reduce_only=reduce_only,
                market_unit=market_unit,
                requested_qty=requested,
                normalized_qty=(
                    normalized_qty
                ),
                target_cash_usdt=dec(
                    leg.target_cash_usdt
                ),
                slices=slices,
                instrument_snapshot=(
                    instrument_snapshot
                ),
                position_snapshot=(
                    position_snapshot
                ),
                prepared_at=prepared_at,
            )
        )
    except NegativeSaleOrderIntentError as exc:
        raise NegativeSaleOrderStateError(
            "Prepared intent creation "
            f"failed: {exc}"
        ) from exc

    result = intent.to_dict()

    validate_negative_sale_order_intent(
        result
    )

    return result


def persist_prepared_intent_before_submit(
    db: Session,
    *,
    sale_batch: FundNegativeSaleBatch,
    leg: FundNegativeSaleLeg,
    execution_round: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    effective_now = now or utcnow()

    intent = build_prepared_intent_for_leg(
        sale_batch=sale_batch,
        leg=leg,
        execution_round=execution_round,
        prepared_at=effective_now,
    )

    suborders = intent.get("suborders")

    if not isinstance(suborders, list):
        raise NegativeSaleOrderStateError(
            "Prepared intent suborders "
            "must be a list"
        )

    if not suborders:
        raise NegativeSaleOrderStateError(
            "Prepared intent has no "
            "suborders"
        )

    first = suborders[0]

    if not isinstance(first, dict):
        raise NegativeSaleOrderStateError(
            "Prepared first suborder "
            "must be a dict"
        )

    leg.actual_execution_mode = (
        "live_market_order"
    )
    leg.execution_round = str(
        int(execution_round)
    )
    leg.deterministic_key = str(
        intent["deterministic_key"]
    )
    leg.order_link_id = str(
        first["order_link_id"]
    )
    leg.planned_suborders = len(
        suborders
    )
    leg.executed_suborders = int(
        leg.executed_suborders or 0
    )
    leg.suborders_json = intent
    leg.updated_at = effective_now

    db.add(leg)
    db.flush()

    # This commit is intentional. The immutable
    # economic intent must be durable before the
    # first external Bybit POST is attempted.
    db.commit()
    db.refresh(leg)

    return dict(
        leg.suborders_json or {}
    )