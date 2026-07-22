from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Any, Callable

from app.bybit.client import BybitV5Client
from app.bybit.earn import (
    BybitEarnError,
    BybitEarnOrder,
    EARN_ORDER_STATUS_FAIL,
    EARN_ORDER_STATUS_PENDING,
    EARN_ORDER_STATUS_SUCCESS,
    build_flexible_saving_redeem_payload,
    query_earn_order_by_link_id,
    submit_flexible_saving_redeem_order,
)


ZERO = Decimal("0")

NEGATIVE_SALE_EARN_INTENT_SCHEMA = (
    "negative_sale_earn_redeem_intent_v1"
)

EARN_RUNTIME_STATUS_PREPARED = "prepared"
EARN_RUNTIME_STATUS_SUBMITTED = "submitted"
EARN_RUNTIME_STATUS_ACKNOWLEDGED = (
    "acknowledged"
)
EARN_RUNTIME_STATUS_PENDING = (
    "pending_confirmation"
)
EARN_RUNTIME_STATUS_SUCCESS = "success"
EARN_RUNTIME_STATUS_FAILED = "failed"

EARN_RUNTIME_STATUSES = {
    EARN_RUNTIME_STATUS_PREPARED,
    EARN_RUNTIME_STATUS_SUBMITTED,
    EARN_RUNTIME_STATUS_ACKNOWLEDGED,
    EARN_RUNTIME_STATUS_PENDING,
    EARN_RUNTIME_STATUS_SUCCESS,
    EARN_RUNTIME_STATUS_FAILED,
}

EARN_RUNTIME_NON_RESUBMITTABLE = {
    EARN_RUNTIME_STATUS_SUBMITTED,
    EARN_RUNTIME_STATUS_ACKNOWLEDGED,
    EARN_RUNTIME_STATUS_PENDING,
    EARN_RUNTIME_STATUS_SUCCESS,
    EARN_RUNTIME_STATUS_FAILED,
}

EARN_RUNTIME_TERMINAL = {
    EARN_RUNTIME_STATUS_SUCCESS,
    EARN_RUNTIME_STATUS_FAILED,
}

ALLOWED_STATUS_TRANSITIONS = {
    EARN_RUNTIME_STATUS_PREPARED: {
        EARN_RUNTIME_STATUS_PREPARED,
        EARN_RUNTIME_STATUS_SUBMITTED,
        EARN_RUNTIME_STATUS_PENDING,
        EARN_RUNTIME_STATUS_SUCCESS,
        EARN_RUNTIME_STATUS_FAILED,
    },
    EARN_RUNTIME_STATUS_SUBMITTED: {
        EARN_RUNTIME_STATUS_SUBMITTED,
        EARN_RUNTIME_STATUS_ACKNOWLEDGED,
        EARN_RUNTIME_STATUS_PENDING,
        EARN_RUNTIME_STATUS_SUCCESS,
        EARN_RUNTIME_STATUS_FAILED,
    },
    EARN_RUNTIME_STATUS_ACKNOWLEDGED: {
        EARN_RUNTIME_STATUS_ACKNOWLEDGED,
        EARN_RUNTIME_STATUS_PENDING,
        EARN_RUNTIME_STATUS_SUCCESS,
        EARN_RUNTIME_STATUS_FAILED,
    },
    EARN_RUNTIME_STATUS_PENDING: {
        EARN_RUNTIME_STATUS_PENDING,
        EARN_RUNTIME_STATUS_SUCCESS,
        EARN_RUNTIME_STATUS_FAILED,
    },
    EARN_RUNTIME_STATUS_SUCCESS: {
        EARN_RUNTIME_STATUS_SUCCESS,
    },
    EARN_RUNTIME_STATUS_FAILED: {
        EARN_RUNTIME_STATUS_FAILED,
    },
}


class NegativeSaleEarnRuntimeError(
    RuntimeError
):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(
    value: datetime,
) -> str:
    effective = value

    if effective.tzinfo is None:
        effective = effective.replace(
            tzinfo=timezone.utc
        )

    return effective.astimezone(
        timezone.utc
    ).isoformat()


def _decimal(
    value: Any,
    *,
    field_name: str,
    positive: bool = False,
    non_negative: bool = False,
) -> Decimal:
    if isinstance(value, bool):
        raise NegativeSaleEarnRuntimeError(
            f"{field_name} must not be bool"
        )

    if isinstance(value, float):
        raise NegativeSaleEarnRuntimeError(
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
        raise NegativeSaleEarnRuntimeError(
            f"{field_name} is not Decimal"
        ) from exc

    if not result.is_finite():
        raise NegativeSaleEarnRuntimeError(
            f"{field_name} must be finite"
        )

    if positive and result <= ZERO:
        raise NegativeSaleEarnRuntimeError(
            f"{field_name} must be positive"
        )

    if non_negative and result < ZERO:
        raise NegativeSaleEarnRuntimeError(
            f"{field_name} must be "
            "non-negative"
        )

    return result


def _required_text(
    value: Any,
    *,
    field_name: str,
) -> str:
    result = str(
        value or ""
    ).strip()

    if not result:
        raise NegativeSaleEarnRuntimeError(
            f"{field_name} must not be empty"
        )

    return result


def _required_int(
    value: Any,
    *,
    field_name: str,
    non_negative: bool = True,
) -> int:
    if isinstance(value, bool):
        raise NegativeSaleEarnRuntimeError(
            f"{field_name} must not be bool"
        )

    try:
        result = int(value)
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise NegativeSaleEarnRuntimeError(
            f"{field_name} must be int"
        ) from exc

    if non_negative and result < 0:
        raise NegativeSaleEarnRuntimeError(
            f"{field_name} must be "
            "non-negative"
        )

    return result


def _immutable_projection(
    intent: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": intent.get("schema"),
        "sale_batch_id": (
            intent.get("sale_batch_id")
        ),
        "leg_id": intent.get("leg_id"),
        "leg_index": (
            intent.get("leg_index")
        ),
        "execution_round": (
            intent.get("execution_round")
        ),
        "category": intent.get("category"),
        "operation": (
            intent.get("operation")
        ),
        "account_type": (
            intent.get("account_type")
        ),
        "coin": intent.get("coin"),
        "product_id": (
            intent.get("product_id")
        ),
        "product_precision": (
            intent.get(
                "product_precision"
            )
        ),
        "target_cash_usdt": (
            intent.get(
                "target_cash_usdt"
            )
        ),
        "confirmed_available_usdt_at_prepare": (
            intent.get(
                "confirmed_available_"
                "usdt_at_prepare"
            )
        ),
        "available_earn_usdt_at_prepare": (
            intent.get(
                "available_earn_usdt_"
                "at_prepare"
            )
        ),
        "needed_from_earn_usdt": (
            intent.get(
                "needed_from_earn_usdt"
            )
        ),
        "amount": intent.get("amount"),
        "amount_str": (
            intent.get("amount_str")
        ),
        "order_link_id": (
            intent.get("order_link_id")
        ),
        "payload": deepcopy(
            intent.get("payload")
        ),
        "prepared_at": (
            intent.get("prepared_at")
        ),
    }


def _fingerprint(
    projection: dict[str, Any],
) -> str:
    encoded = json.dumps(
        projection,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")

    return sha256(
        encoded
    ).hexdigest()


def build_negative_sale_earn_intent(
    *,
    sale_batch_id: int,
    leg_id: int,
    leg_index: int,
    execution_round: int,
    product_id: str,
    product_precision: int,
    target_cash_usdt: Any,
    confirmed_available_usdt: Any,
    available_earn_usdt: Any,
    needed_from_earn_usdt: Any,
    amount: Any,
    amount_str: str,
    order_link_id: str,
    prepared_at: datetime | None = None,
) -> dict[str, Any]:
    target_cash = _decimal(
        target_cash_usdt,
        field_name="target_cash_usdt",
        positive=True,
    )
    confirmed_available = _decimal(
        confirmed_available_usdt,
        field_name=(
            "confirmed_available_usdt"
        ),
        non_negative=True,
    )
    available_earn = _decimal(
        available_earn_usdt,
        field_name="available_earn_usdt",
        non_negative=True,
    )
    needed = _decimal(
        needed_from_earn_usdt,
        field_name=(
            "needed_from_earn_usdt"
        ),
        positive=True,
    )
    actual_amount = _decimal(
        amount,
        field_name="amount",
        positive=True,
    )

    if needed > target_cash:
        raise NegativeSaleEarnRuntimeError(
            "needed_from_earn_usdt exceeds "
            "target_cash_usdt"
        )

    if needed > available_earn:
        raise NegativeSaleEarnRuntimeError(
            "needed_from_earn_usdt exceeds "
            "available_earn_usdt"
        )

    if actual_amount < needed:
        raise NegativeSaleEarnRuntimeError(
            "Rounded Earn amount is below "
            "needed_from_earn_usdt"
        )

    if actual_amount > available_earn:
        raise NegativeSaleEarnRuntimeError(
            "Rounded Earn amount exceeds "
            "available_earn_usdt"
        )

    normalized_product_id = (
        _required_text(
            product_id,
            field_name="product_id",
        )
    )
    normalized_precision = (
        _required_int(
            product_precision,
            field_name=(
                "product_precision"
            ),
        )
    )
    normalized_link_id = (
        _required_text(
            order_link_id,
            field_name="order_link_id",
        )
    )

    try:
        payload = (
            build_flexible_saving_redeem_payload(
                amount=actual_amount,
                amount_str=amount_str,
                product_id=(
                    normalized_product_id
                ),
                order_link_id=(
                    normalized_link_id
                ),
                coin="USDT",
                account_type="FUND",
            )
        )
    except BybitEarnError as exc:
        raise NegativeSaleEarnRuntimeError(
            "Cannot build immutable Earn "
            f"payload: {exc}"
        ) from exc

    effective_now = (
        prepared_at or utcnow()
    )

    intent: dict[str, Any] = {
        "schema": (
            NEGATIVE_SALE_EARN_INTENT_SCHEMA
        ),
        "sale_batch_id": int(
            sale_batch_id
        ),
        "leg_id": int(leg_id),
        "leg_index": int(leg_index),
        "execution_round": int(
            execution_round
        ),
        "category": "FlexibleSaving",
        "operation": "Redeem",
        "account_type": "FUND",
        "coin": "USDT",
        "product_id": (
            normalized_product_id
        ),
        "product_precision": (
            normalized_precision
        ),
        "target_cash_usdt": str(
            target_cash
        ),
        "confirmed_available_"
        "usdt_at_prepare": str(
            confirmed_available
        ),
        "available_earn_usdt_at_prepare": (
            str(available_earn)
        ),
        "needed_from_earn_usdt": str(
            needed
        ),
        "amount": str(actual_amount),
        "amount_str": str(
            amount_str
        ),
        "order_link_id": (
            normalized_link_id
        ),
        "payload": deepcopy(payload),
        "prepared_at": _iso(
            effective_now
        ),
        "status": (
            EARN_RUNTIME_STATUS_PREPARED
        ),
        "order_id": None,
        "submitted_at": None,
        "acknowledged_at": None,
        "confirmed_at": None,
        "failed_at": None,
        "last_checked_at": None,
        "submit_ack": None,
        "history_checks": [],
        "redeemed_usdt": "0",
        "failure_reason": None,
        "safety": {
            "operation_guard_required": True,
            "persist_submitted_before_post": (
                True
            ),
            "no_automatic_resubmit_after_"
            "submitted": True,
            "no_transfer": True,
            "no_withdrawal": True,
            "no_bsc_action": True,
            "no_accounting_finalization": (
                True
            ),
        },
    }

    intent["intent_fingerprint"] = (
        _fingerprint(
            _immutable_projection(intent)
        )
    )

    validate_negative_sale_earn_intent(
        intent
    )

    return intent


def validate_negative_sale_earn_intent(
    raw_intent: dict[str, Any],
) -> None:
    if not isinstance(
        raw_intent,
        dict,
    ):
        raise NegativeSaleEarnRuntimeError(
            "Earn intent must be a dict"
        )

    if (
        raw_intent.get("schema")
        != NEGATIVE_SALE_EARN_INTENT_SCHEMA
    ):
        raise NegativeSaleEarnRuntimeError(
            "Unsupported Earn intent schema"
        )

    for field_name in (
        "sale_batch_id",
        "leg_id",
        "leg_index",
        "execution_round",
    ):
        _required_int(
            raw_intent.get(field_name),
            field_name=field_name,
        )

    if (
        raw_intent.get("category")
        != "FlexibleSaving"
    ):
        raise NegativeSaleEarnRuntimeError(
            "Earn category must be "
            "FlexibleSaving"
        )

    if (
        raw_intent.get("operation")
        != "Redeem"
    ):
        raise NegativeSaleEarnRuntimeError(
            "Earn operation must be Redeem"
        )

    if (
        raw_intent.get("account_type")
        != "FUND"
    ):
        raise NegativeSaleEarnRuntimeError(
            "Earn account_type must be FUND"
        )

    if raw_intent.get("coin") != "USDT":
        raise NegativeSaleEarnRuntimeError(
            "Earn coin must be USDT"
        )

    product_id = _required_text(
        raw_intent.get("product_id"),
        field_name="product_id",
    )
    _required_int(
        raw_intent.get(
            "product_precision"
        ),
        field_name="product_precision",
    )

    target_cash = _decimal(
        raw_intent.get(
            "target_cash_usdt"
        ),
        field_name="target_cash_usdt",
        positive=True,
    )
    available_earn = _decimal(
        raw_intent.get(
            "available_earn_usdt_at_prepare"
        ),
        field_name=(
            "available_earn_usdt_at_prepare"
        ),
        non_negative=True,
    )
    needed = _decimal(
        raw_intent.get(
            "needed_from_earn_usdt"
        ),
        field_name=(
            "needed_from_earn_usdt"
        ),
        positive=True,
    )
    amount = _decimal(
        raw_intent.get("amount"),
        field_name="amount",
        positive=True,
    )

    _decimal(
        raw_intent.get(
            "confirmed_available_"
            "usdt_at_prepare"
        ),
        field_name=(
            "confirmed_available_"
            "usdt_at_prepare"
        ),
        non_negative=True,
    )

    if needed > target_cash:
        raise NegativeSaleEarnRuntimeError(
            "Earn needed amount exceeds "
            "target cash"
        )

    if needed > available_earn:
        raise NegativeSaleEarnRuntimeError(
            "Earn needed amount exceeds "
            "available Earn"
        )

    if amount < needed:
        raise NegativeSaleEarnRuntimeError(
            "Earn amount is below needed "
            "amount"
        )

    if amount > available_earn:
        raise NegativeSaleEarnRuntimeError(
            "Earn amount exceeds available "
            "Earn"
        )

    order_link_id = _required_text(
        raw_intent.get("order_link_id"),
        field_name="order_link_id",
    )

    payload = raw_intent.get("payload")

    if not isinstance(payload, dict):
        raise NegativeSaleEarnRuntimeError(
            "Earn payload must be a dict"
        )

    try:
        expected_payload = (
            build_flexible_saving_redeem_payload(
                amount=amount,
                amount_str=_required_text(
                    raw_intent.get(
                        "amount_str"
                    ),
                    field_name="amount_str",
                ),
                product_id=product_id,
                order_link_id=order_link_id,
                coin="USDT",
                account_type="FUND",
            )
        )
    except BybitEarnError as exc:
        raise NegativeSaleEarnRuntimeError(
            "Earn payload validation "
            f"failed: {exc}"
        ) from exc

    if payload != expected_payload:
        raise NegativeSaleEarnRuntimeError(
            "Earn exact payload mismatch"
        )

    status = _required_text(
        raw_intent.get("status"),
        field_name="status",
    )

    if status not in EARN_RUNTIME_STATUSES:
        raise NegativeSaleEarnRuntimeError(
            "Unsupported Earn runtime "
            f"status: {status}"
        )

    history = raw_intent.get(
        "history_checks"
    )

    if not isinstance(history, list):
        raise NegativeSaleEarnRuntimeError(
            "history_checks must be a list"
        )

    for index, item in enumerate(history):
        if not isinstance(item, dict):
            raise NegativeSaleEarnRuntimeError(
                f"history_checks[{index}] "
                "must be a dict"
            )

    stored_fingerprint = (
        _required_text(
            raw_intent.get(
                "intent_fingerprint"
            ),
            field_name=(
                "intent_fingerprint"
            ),
        )
    )
    expected_fingerprint = (
        _fingerprint(
            _immutable_projection(
                raw_intent
            )
        )
    )

    if (
        stored_fingerprint
        != expected_fingerprint
    ):
        raise NegativeSaleEarnRuntimeError(
            "Earn intent fingerprint "
            "mismatch"
        )

    redeemed = _decimal(
        raw_intent.get(
            "redeemed_usdt"
        ),
        field_name="redeemed_usdt",
        non_negative=True,
    )

    if status == EARN_RUNTIME_STATUS_SUCCESS:
        if redeemed != amount:
            raise NegativeSaleEarnRuntimeError(
                "Successful Earn intent must "
                "redeem the exact amount"
            )

        _required_text(
            raw_intent.get("order_id"),
            field_name="order_id",
        )

        if not raw_intent.get(
            "confirmed_at"
        ):
            raise NegativeSaleEarnRuntimeError(
                "Successful Earn intent has "
                "no confirmed_at"
            )

    elif redeemed != ZERO:
        raise NegativeSaleEarnRuntimeError(
            "Non-success Earn intent must "
            "have redeemed_usdt=0"
        )

    if status == EARN_RUNTIME_STATUS_FAILED:
        _required_text(
            raw_intent.get(
                "failure_reason"
            ),
            field_name="failure_reason",
        )

        if not raw_intent.get(
            "failed_at"
        ):
            raise NegativeSaleEarnRuntimeError(
                "Failed Earn intent has no "
                "failed_at"
            )


def validated_earn_intent_copy(
    raw_intent: dict[str, Any],
) -> dict[str, Any]:
    result = deepcopy(raw_intent)

    validate_negative_sale_earn_intent(
        result
    )

    return result


def validate_earn_runtime_transition(
    previous: dict[str, Any],
    updated: dict[str, Any],
) -> None:
    old = validated_earn_intent_copy(
        previous
    )
    new = validated_earn_intent_copy(
        updated
    )

    if (
        _immutable_projection(old)
        != _immutable_projection(new)
    ):
        raise NegativeSaleEarnRuntimeError(
            "Earn immutable economic intent "
            "changed"
        )

    if (
        old["intent_fingerprint"]
        != new["intent_fingerprint"]
    ):
        raise NegativeSaleEarnRuntimeError(
            "Earn intent fingerprint changed"
        )

    old_status = str(
        old["status"]
    )
    new_status = str(
        new["status"]
    )

    if (
        new_status
        not in ALLOWED_STATUS_TRANSITIONS[
            old_status
        ]
    ):
        raise NegativeSaleEarnRuntimeError(
            "Illegal Earn runtime "
            "transition: "
            f"{old_status} -> {new_status}"
        )

    old_order_id = str(
        old.get("order_id") or ""
    ).strip()
    new_order_id = str(
        new.get("order_id") or ""
    ).strip()

    if (
        old_order_id
        and new_order_id != old_order_id
    ):
        raise NegativeSaleEarnRuntimeError(
            "Earn order_id changed"
        )

    old_history = old.get(
        "history_checks"
    )
    new_history = new.get(
        "history_checks"
    )

    assert isinstance(old_history, list)
    assert isinstance(new_history, list)

    if (
        new_history[:len(old_history)]
        != old_history
    ):
        raise NegativeSaleEarnRuntimeError(
            "Earn history_checks must be "
            "append-only"
        )


def _append_history_check(
    intent: dict[str, Any],
    *,
    order: BybitEarnOrder | None,
    checked_at: datetime,
) -> None:
    history = intent.get(
        "history_checks"
    )

    if not isinstance(history, list):
        raise NegativeSaleEarnRuntimeError(
            "history_checks must be a list"
        )

    history.append(
        {
            "checked_at": _iso(
                checked_at
            ),
            "found": order is not None,
            "order_id": (
                order.order_id
                if order is not None
                else None
            ),
            "order_link_id": (
                order.order_link_id
                if order is not None
                else intent.get(
                    "order_link_id"
                )
            ),
            "status": (
                order.status
                if order is not None
                else None
            ),
            "amount": (
                str(order.amount)
                if order is not None
                else None
            ),
            "raw": (
                deepcopy(order.raw)
                if order is not None
                else None
            ),
        }
    )

    intent["last_checked_at"] = _iso(
        checked_at
    )


def _apply_history_order(
    intent: dict[str, Any],
    *,
    order: BybitEarnOrder | None,
    now: datetime,
) -> None:
    current_status = str(
        intent.get("status")
        or ""
    ).strip()

    _append_history_check(
        intent,
        order=order,
        checked_at=now,
    )

    if order is None:
        if (
            current_status
            in EARN_RUNTIME_NON_RESUBMITTABLE
        ):
            intent["status"] = (
                EARN_RUNTIME_STATUS_PENDING
            )

        validate_negative_sale_earn_intent(
            intent
        )
        return

    expected_link_id = str(
        intent["order_link_id"]
    )
    actual_link_id = str(
        order.order_link_id or ""
    )

    if actual_link_id != expected_link_id:
        raise NegativeSaleEarnRuntimeError(
            "Earn history orderLinkId "
            "mismatch"
        )

    expected_product_id = str(
        intent["product_id"]
    )

    if (
        order.product_id is not None
        and str(order.product_id)
        != expected_product_id
    ):
        raise NegativeSaleEarnRuntimeError(
            "Earn history productId "
            "mismatch"
        )

    if (
        order.order_type is not None
        and str(order.order_type)
        != "Redeem"
    ):
        raise NegativeSaleEarnRuntimeError(
            "Earn history orderType "
            "mismatch"
        )

    if (
        order.coin is not None
        and str(order.coin).upper()
        != "USDT"
    ):
        raise NegativeSaleEarnRuntimeError(
            "Earn history coin mismatch"
        )

    existing_order_id = str(
        intent.get("order_id") or ""
    ).strip()
    confirmed_order_id = str(
        order.order_id or ""
    ).strip()

    if not confirmed_order_id:
        raise NegativeSaleEarnRuntimeError(
            "Earn history order has no "
            "order_id"
        )

    if (
        existing_order_id
        and confirmed_order_id
        != existing_order_id
    ):
        raise NegativeSaleEarnRuntimeError(
            "Earn history order_id changed"
        )

    intent["order_id"] = (
        confirmed_order_id
    )

    status = str(
        order.status or ""
    ).strip()
    requested_amount = _decimal(
        intent["amount"],
        field_name="amount",
        positive=True,
    )

    if status == EARN_ORDER_STATUS_PENDING:
        intent["status"] = (
            EARN_RUNTIME_STATUS_PENDING
        )
        intent["failure_reason"] = None

    elif status == EARN_ORDER_STATUS_SUCCESS:
        if order.amount != requested_amount:
            intent["status"] = (
                EARN_RUNTIME_STATUS_FAILED
            )
            intent["failure_reason"] = (
                "confirmed_earn_amount_"
                "mismatch"
            )
            intent["failed_at"] = _iso(
                now
            )
        else:
            intent["status"] = (
                EARN_RUNTIME_STATUS_SUCCESS
            )
            intent["redeemed_usdt"] = str(
                requested_amount
            )
            intent["confirmed_at"] = _iso(
                now
            )
            intent["failure_reason"] = None
            intent["failed_at"] = None

    elif status == EARN_ORDER_STATUS_FAIL:
        intent["status"] = (
            EARN_RUNTIME_STATUS_FAILED
        )
        intent["failure_reason"] = (
            "bybit_earn_order_failed"
        )
        intent["failed_at"] = _iso(
            now
        )

    else:
        intent["status"] = (
            EARN_RUNTIME_STATUS_FAILED
        )
        intent["failure_reason"] = (
            "unknown_bybit_earn_status:"
            f"{status or 'empty'}"
        )
        intent["failed_at"] = _iso(
            now
        )

    validate_negative_sale_earn_intent(
        intent
    )


def submit_negative_sale_earn_once(
    client: BybitV5Client,
    *,
    raw_intent: dict[str, Any],
    before_submit: Callable[
        [dict[str, Any]],
        None,
    ],
    persist_state: Callable[
        [dict[str, Any]],
        None,
    ],
    now: datetime | None = None,
) -> tuple[
    dict[str, Any],
    bool,
]:
    effective_now = now or utcnow()
    intent = validated_earn_intent_copy(
        raw_intent
    )
    status = str(intent["status"])

    if status in EARN_RUNTIME_TERMINAL:
        return intent, False

    if (
        status
        in EARN_RUNTIME_NON_RESUBMITTABLE
    ):
        return intent, False

    if (
        status
        != EARN_RUNTIME_STATUS_PREPARED
    ):
        raise NegativeSaleEarnRuntimeError(
            "Earn submit requires prepared "
            f"status, got {status}"
        )

    try:
        existing = (
            query_earn_order_by_link_id(
                client,
                order_link_id=str(
                    intent["order_link_id"]
                ),
                category="FlexibleSaving",
                product_id=str(
                    intent["product_id"]
                ),
            )
        )
    except BybitEarnError as exc:
        raise NegativeSaleEarnRuntimeError(
            "Earn pre-submit history query "
            f"failed: {exc}"
        ) from exc

    if existing is not None:
        _apply_history_order(
            intent,
            order=existing,
            now=effective_now,
        )
        persist_state(
            deepcopy(intent)
        )
        return intent, False

    payload = intent.get("payload")

    if not isinstance(payload, dict):
        raise NegativeSaleEarnRuntimeError(
            "Earn exact payload is missing"
        )

    before_submit(
        deepcopy(payload)
    )

    intent["status"] = (
        EARN_RUNTIME_STATUS_SUBMITTED
    )
    intent["submitted_at"] = _iso(
        effective_now
    )

    validate_negative_sale_earn_intent(
        intent
    )

    # Durable claim before POST. Any crash
    # after this point must not permit an
    # automatic second POST.
    persist_state(
        deepcopy(intent)
    )

    try:
        ack = (
            submit_flexible_saving_redeem_order(
                client,
                amount=_decimal(
                    intent["amount"],
                    field_name="amount",
                    positive=True,
                ),
                amount_str=str(
                    intent["amount_str"]
                ),
                product_id=str(
                    intent["product_id"]
                ),
                order_link_id=str(
                    intent["order_link_id"]
                ),
                coin="USDT",
                account_type="FUND",
            )
        )
    except BybitEarnError as exc:
        raise NegativeSaleEarnRuntimeError(
            "Earn submit failed after durable "
            f"submitted claim: {exc}"
        ) from exc

    intent["status"] = (
        EARN_RUNTIME_STATUS_ACKNOWLEDGED
    )
    intent["order_id"] = ack.order_id
    intent["acknowledged_at"] = _iso(
        effective_now
    )
    intent["submit_ack"] = {
        "order_id": ack.order_id,
        "order_link_id": (
            ack.order_link_id
        ),
        "raw": deepcopy(ack.raw),
    }

    validate_negative_sale_earn_intent(
        intent
    )

    # ACK is durable but is not confirmation.
    persist_state(
        deepcopy(intent)
    )

    return intent, True


def confirm_negative_sale_earn_once(
    client: BybitV5Client,
    *,
    raw_intent: dict[str, Any],
    persist_state: Callable[
        [dict[str, Any]],
        None,
    ],
    now: datetime | None = None,
) -> dict[str, Any]:
    effective_now = now or utcnow()
    intent = validated_earn_intent_copy(
        raw_intent
    )

    if (
        str(intent["status"])
        in EARN_RUNTIME_TERMINAL
    ):
        return intent

    try:
        order = query_earn_order_by_link_id(
            client,
            order_link_id=str(
                intent["order_link_id"]
            ),
            category="FlexibleSaving",
            product_id=str(
                intent["product_id"]
            ),
        )
    except BybitEarnError as exc:
        raise NegativeSaleEarnRuntimeError(
            "Earn confirmation query failed: "
            f"{exc}"
        ) from exc

    _apply_history_order(
        intent,
        order=order,
        now=effective_now,
    )

    persist_state(
        deepcopy(intent)
    )

    return intent


def negative_sale_earn_runtime_summary(
    raw_intent: dict[str, Any],
) -> dict[str, Any]:
    intent = validated_earn_intent_copy(
        raw_intent
    )
    status = str(intent["status"])

    return {
        "status": status,
        "terminal": (
            status
            in EARN_RUNTIME_TERMINAL
        ),
        "success": (
            status
            == EARN_RUNTIME_STATUS_SUCCESS
        ),
        "failed": (
            status
            == EARN_RUNTIME_STATUS_FAILED
        ),
        "pending_external_action": (
            status
            in {
                EARN_RUNTIME_STATUS_SUBMITTED,
                EARN_RUNTIME_STATUS_ACKNOWLEDGED,
                EARN_RUNTIME_STATUS_PENDING,
            }
        ),
        "resubmittable": (
            status
            == EARN_RUNTIME_STATUS_PREPARED
        ),
        "amount": intent["amount"],
        "redeemed_usdt": (
            intent["redeemed_usdt"]
        ),
        "order_id": (
            intent.get("order_id")
        ),
        "order_link_id": (
            intent["order_link_id"]
        ),
        "failure_reason": (
            intent.get("failure_reason")
        ),
        "history_check_count": len(
            intent["history_checks"]
        ),
    }