from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    FundNegativeSaleLeg,
)
from app.settlement.negative_sale_earn_runtime import (
    EARN_RUNTIME_STATUS_ACKNOWLEDGED,
    EARN_RUNTIME_STATUS_FAILED,
    EARN_RUNTIME_STATUS_PENDING,
    EARN_RUNTIME_STATUS_PREPARED,
    EARN_RUNTIME_STATUS_SUBMITTED,
    EARN_RUNTIME_STATUS_SUCCESS,
    NegativeSaleEarnRuntimeError,
    validate_earn_runtime_transition,
    validate_negative_sale_earn_intent,
)
from app.settlement.statuses import (
    SALE_LEG_STATUS_BUFFER_AVAILABLE,
    SALE_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    SALE_LEG_STATUS_PENDING_CONFIRMATION,
    SALE_LEG_STATUS_USDT_EARN_REDEEMED,
)


ZERO = Decimal("0")
ONE = Decimal("1")


class NegativeSaleEarnPersistenceError(
    RuntimeError
):
    pass


def _decimal(
    value: Any,
) -> Decimal:
    if value is None or value == "":
        return ZERO

    return Decimal(str(value))


def persist_negative_sale_earn_state(
    db: Session,
    *,
    leg: FundNegativeSaleLeg,
    raw_intent: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    intent = deepcopy(raw_intent)

    try:
        validate_negative_sale_earn_intent(
            intent
        )
    except NegativeSaleEarnRuntimeError as exc:
        raise NegativeSaleEarnPersistenceError(
            "Cannot persist invalid Earn "
            f"intent: {exc}"
        ) from exc

    existing = leg.suborders_json

    if existing is not None:
        if not isinstance(existing, dict):
            raise NegativeSaleEarnPersistenceError(
                "Existing Earn JSONB state "
                "must be a dict"
            )

        try:
            validate_earn_runtime_transition(
                existing,
                intent,
            )
        except NegativeSaleEarnRuntimeError as exc:
            raise (
                NegativeSaleEarnPersistenceError(
                    "Illegal durable Earn "
                    f"transition: {exc}"
                )
            ) from exc

    if (
        int(intent["leg_id"])
        != int(leg.id)
    ):
        raise NegativeSaleEarnPersistenceError(
            "Earn intent leg_id mismatch"
        )

    if (
        int(intent["sale_batch_id"])
        != int(leg.sale_batch_id)
    ):
        raise NegativeSaleEarnPersistenceError(
            "Earn intent sale_batch_id "
            "mismatch"
        )

    if (
        int(intent["leg_index"])
        != int(leg.leg_index)
    ):
        raise NegativeSaleEarnPersistenceError(
            "Earn intent leg_index mismatch"
        )

    status = str(intent["status"])
    amount = _decimal(
        intent["amount"]
    )
    redeemed = _decimal(
        intent["redeemed_usdt"]
    )
    target_cash = _decimal(
        intent["target_cash_usdt"]
    )

    leg.actual_execution_mode = (
        "live_usdt_earn_redeem"
    )
    leg.execution_round = str(
        intent["execution_round"]
    )
    leg.deterministic_key = str(
        intent["intent_fingerprint"]
    )

    leg.order_link_id = str(
        intent["order_link_id"]
    )
    leg.bybit_order_id = (
        str(intent["order_id"])
        if intent.get("order_id")
        else None
    )
    leg.bybit_strategy_id = None

    leg.planned_suborders = 1
    leg.executed_suborders = (
        1
        if status
        == EARN_RUNTIME_STATUS_SUCCESS
        else 0
    )
    leg.suborders_json = deepcopy(
        intent
    )

    leg.fee_usdt = ZERO
    leg.updated_at = now

    if status == EARN_RUNTIME_STATUS_PREPARED:
        leg.status = (
            SALE_LEG_STATUS_BUFFER_AVAILABLE
        )
        leg.execution_error = None
        leg.failed_at = None

    elif status in {
        EARN_RUNTIME_STATUS_SUBMITTED,
        EARN_RUNTIME_STATUS_ACKNOWLEDGED,
        EARN_RUNTIME_STATUS_PENDING,
    }:
        leg.status = (
            SALE_LEG_STATUS_PENDING_CONFIRMATION
        )
        leg.sent_at = (
            leg.sent_at or now
        )
        leg.confirmed_at = None
        leg.failed_at = None
        leg.execution_error = None

    elif status == EARN_RUNTIME_STATUS_SUCCESS:
        leg.status = (
            SALE_LEG_STATUS_USDT_EARN_REDEEMED
        )
        leg.sent_at = (
            leg.sent_at or now
        )
        leg.confirmed_at = now
        leg.failed_at = None
        leg.execution_error = None

        leg.filled_qty = redeemed
        leg.filled_usdt = redeemed
        leg.avg_fill_price = ONE
        leg.fill_ratio = (
            min(
                redeemed / target_cash,
                ONE,
            )
            if target_cash > ZERO
            else ZERO
        )
        leg.unfilled_usdt = max(
            target_cash - redeemed,
            ZERO,
        )
        leg.cash_delta_usdt = redeemed

    elif status == EARN_RUNTIME_STATUS_FAILED:
        leg.status = (
            SALE_LEG_STATUS_FAILED_REQUIRES_REVIEW
        )
        leg.sent_at = (
            leg.sent_at or now
        )
        leg.confirmed_at = None
        leg.failed_at = now
        leg.execution_error = str(
            intent.get(
                "failure_reason"
            )
            or "earn_runtime_failed"
        )

        leg.filled_qty = ZERO
        leg.filled_usdt = ZERO
        leg.avg_fill_price = None
        leg.fill_ratio = ZERO
        leg.unfilled_usdt = amount
        leg.cash_delta_usdt = ZERO

    else:
        raise NegativeSaleEarnPersistenceError(
            "Unsupported Earn persistence "
            f"status: {status}"
        )

    db.add(leg)
    db.flush()

    # Every runtime transition is durable
    # independently. In particular, submitted
    # is committed before the external POST.
    db.commit()
    db.refresh(leg)

    persisted = leg.suborders_json

    if not isinstance(persisted, dict):
        raise NegativeSaleEarnPersistenceError(
            "Persisted Earn JSONB state "
            "is missing"
        )

    return deepcopy(persisted)