from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable

from app.bybit.client import BybitV5Client
from app.bybit.order_execution import (
    create_market_order_from_payload,
)
from app.bybit.order_reconciliation import (
    BybitOrderReconciliation,
    reconcile_bybit_order,
)
from app.settlement.negative_sale_execution_types import (
    ZERO,
    utcnow,
)
from app.settlement.negative_sale_order_intent import (
    NegativeSaleOrderIntentError,
    validate_negative_sale_order_intent,
)


class NegativeSaleOrderRuntimeError(
    RuntimeError
):
    pass


TERMINAL_SUBORDER_STATUSES = {
    "filled",
    "terminal_partial",
    "failed",
}


NON_RESUBMITTABLE_SUBORDER_STATUSES = {
    "submitted",
    "acknowledged",
    "pending_confirmation",
    "partially_filled_pending_confirmation",
    *TERMINAL_SUBORDER_STATUSES,
}


def _as_decimal(
    value: Any,
) -> Decimal:
    if value is None or value == "":
        return ZERO

    return Decimal(str(value))


def _iso(
    value: datetime,
) -> str:
    return value.isoformat()


def _validated_copy(
    raw_intent: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(raw_intent, dict):
        raise NegativeSaleOrderRuntimeError(
            "Prepared intent must be a dict"
        )

    result = deepcopy(raw_intent)

    try:
        validate_negative_sale_order_intent(
            result
        )
    except NegativeSaleOrderIntentError as exc:
        raise NegativeSaleOrderRuntimeError(
            f"Prepared intent is invalid: {exc}"
        ) from exc

    return result


def _suborders(
    intent: dict[str, Any],
) -> list[dict[str, Any]]:
    raw = intent.get("suborders")

    if not isinstance(raw, list):
        raise NegativeSaleOrderRuntimeError(
            "Prepared intent suborders "
            "must be a list"
        )

    rows: list[dict[str, Any]] = []

    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise NegativeSaleOrderRuntimeError(
                f"suborders[{index}] "
                "must be a dict"
            )

        rows.append(item)

    return rows


def _suborder(
    intent: dict[str, Any],
    *,
    suborder_index: int,
) -> dict[str, Any]:
    rows = _suborders(intent)
    index = int(suborder_index)

    if index < 0 or index >= len(rows):
        raise NegativeSaleOrderRuntimeError(
            "suborder_index is outside "
            "prepared intent"
        )

    item = rows[index]

    stored_index = int(
        item.get("suborder_index")
        if item.get("suborder_index")
        is not None
        else -1
    )

    if stored_index != index:
        raise NegativeSaleOrderRuntimeError(
            "Stored suborder_index mismatch"
        )

    return item


def suborder_is_terminal(
    suborder: dict[str, Any],
) -> bool:
    return str(
        suborder.get("status")
        or ""
    ).strip() in TERMINAL_SUBORDER_STATUSES


def _apply_reconciliation(
    *,
    suborder: dict[str, Any],
    reconciliation: (
        BybitOrderReconciliation
    ),
    now: datetime,
) -> None:
    classification = (
        reconciliation.classification
    )
    previous_status = str(
        suborder.get("status")
        or "prepared"
    ).strip()

    order = reconciliation.order

    requested_qty = _as_decimal(
        suborder.get("qty")
    )

    if requested_qty <= ZERO:
        raise (
            NegativeSaleOrderRuntimeError(
                "Prepared suborder qty "
                "must be positive"
            )
        )

    confirmed_exec_qty = (
        reconciliation
        .aggregate_exec_qty
    )

    exact_execution_coverage = (
        confirmed_exec_qty
        == requested_qty
    )
    over_execution_coverage = (
        confirmed_exec_qty
        > requested_qty
    )

    if order is not None:
        if order.order_id:
            suborder["order_id"] = (
                order.order_id
            )

        if (
            suborder.get("acknowledged_at")
            is None
        ):
            suborder["acknowledged_at"] = (
                _iso(now)
            )

    if over_execution_coverage:
        # Confirmed executions above the
        # immutable requested quantity are
        # contradictory external evidence.
        # Never classify this as successful.
        status = "pending_confirmation"
    elif (
        classification.success
        and exact_execution_coverage
    ):
        status = "filled"
    elif classification.success:
        status = (
            "partially_filled_"
            "pending_confirmation"
            if confirmed_exec_qty > ZERO
            else "pending_confirmation"
        )
    elif classification.partial:
        status = "terminal_partial"
    elif classification.failed:
        status = "failed"
    elif (
        reconciliation.aggregate_exec_qty
        > ZERO
    ):
        status = (
            "partially_filled_"
            "pending_confirmation"
        )
    elif order is not None:
        status = "pending_confirmation"
    elif reconciliation.source_errors:
        status = "pending_confirmation"
    elif (
        previous_status
        in NON_RESUBMITTABLE_SUBORDER_STATUSES
    ):
        # A previously submitted economic
        # intent must never be submitted again
        # merely because Bybit temporarily
        # returns no row.
        status = "pending_confirmation"
    else:
        status = "prepared"

    suborder["status"] = status
    suborder["reconciliation"] = (
        reconciliation.to_dict()
    )

    if status in {
        "filled",
        "terminal_partial",
        "failed",
    }:
        suborder["terminal_at"] = _iso(
            now
        )
    else:
        suborder.pop(
            "terminal_at",
            None,
        )


def reconcile_prepared_suborder(
    client: BybitV5Client,
    *,
    raw_intent: dict[str, Any],
    suborder_index: int,
    now: datetime | None = None,
) -> tuple[
    dict[str, Any],
    BybitOrderReconciliation,
]:
    effective_now = now or utcnow()
    intent = _validated_copy(raw_intent)

    item = _suborder(
        intent,
        suborder_index=suborder_index,
    )

    payload = item.get("payload")

    if not isinstance(payload, dict):
        raise NegativeSaleOrderRuntimeError(
            "Prepared suborder payload "
            "must be a dict"
        )

    category = str(
        payload.get("category")
        or intent.get("category")
        or ""
    ).strip()
    symbol = str(
        payload.get("symbol")
        or intent.get("symbol")
        or ""
    ).strip()
    order_link_id = str(
        item.get("order_link_id")
        or payload.get("orderLinkId")
        or ""
    ).strip()
    order_id = str(
        item.get("order_id")
        or ""
    ).strip() or None

    reconciliation = reconcile_bybit_order(
        client,
        category=category,
        symbol=symbol,
        order_id=order_id,
        order_link_id=(
            order_link_id
            if order_id is None
            else None
        ),
    )

    _apply_reconciliation(
        suborder=item,
        reconciliation=reconciliation,
        now=effective_now,
    )

    validate_negative_sale_order_intent(
        intent
    )

    return intent, reconciliation


def submit_prepared_suborder(
    client: BybitV5Client,
    *,
    raw_intent: dict[str, Any],
    suborder_index: int,
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
    original = _validated_copy(raw_intent)

    original_item = _suborder(
        original,
        suborder_index=suborder_index,
    )
    original_status = str(
        original_item.get("status")
        or "prepared"
    ).strip()

    if suborder_is_terminal(
        original_item
    ):
        return original, False

    reconciled, reconciliation = (
        reconcile_prepared_suborder(
            client,
            raw_intent=original,
            suborder_index=suborder_index,
            now=effective_now,
        )
    )

    item = _suborder(
        reconciled,
        suborder_index=suborder_index,
    )

    if reconciliation.source_errors:
        return reconciled, False

    if (
        reconciliation.order is not None
        or reconciliation.aggregate_exec_qty
        > ZERO
    ):
        return reconciled, False

    if (
        original_status
        in NON_RESUBMITTABLE_SUBORDER_STATUSES
    ):
        return reconciled, False

    if original_status != "prepared":
        return reconciled, False

    payload = item.get("payload")

    if not isinstance(payload, dict):
        raise NegativeSaleOrderRuntimeError(
            "Prepared suborder payload "
            "must be a dict"
        )

    # Operation Guard must approve the exact
    # immutable payload before submission.
    before_submit(
        deepcopy(payload)
    )

    item["status"] = "submitted"
    item["submitted_at"] = _iso(
        effective_now
    )

    validate_negative_sale_order_intent(
        reconciled
    )

    # Persist and commit the submitted state
    # before the external POST. A crash after
    # this point must never permit automatic
    # resubmission of the same economic intent.
    persist_state(
        deepcopy(reconciled)
    )

    order = (
        create_market_order_from_payload(
            client,
            payload=deepcopy(
                payload
            ),
        )
    )

    item["status"] = "acknowledged"
    item["order_id"] = order.order_id
    item["acknowledged_at"] = _iso(
        effective_now
    )
    item["submit_ack"] = order.to_dict()

    validate_negative_sale_order_intent(
        reconciled
    )

    # Persist acknowledgement separately.
    # It is not execution confirmation.
    persist_state(
        deepcopy(reconciled)
    )

    return reconciled, True


def confirm_prepared_suborder(
    client: BybitV5Client,
    *,
    raw_intent: dict[str, Any],
    suborder_index: int,
    now: datetime | None = None,
) -> tuple[
    dict[str, Any],
    BybitOrderReconciliation,
]:
    return reconcile_prepared_suborder(
        client,
        raw_intent=raw_intent,
        suborder_index=suborder_index,
        now=now,
    )


def prepared_intent_runtime_summary(
    raw_intent: dict[str, Any],
) -> dict[str, Any]:
    intent = _validated_copy(raw_intent)
    rows = _suborders(intent)

    status_counts: dict[str, int] = {}
    aggregate_exec_qty = ZERO
    aggregate_exec_value = ZERO
    aggregate_exec_value_known = False

    for item in rows:
        status = str(
            item.get("status")
            or "prepared"
        ).strip()

        status_counts[status] = (
            status_counts.get(status, 0)
            + 1
        )

        reconciliation = item.get(
            "reconciliation"
        )

        if not isinstance(
            reconciliation,
            dict,
        ):
            continue

        aggregate_exec_qty += (
            _as_decimal(
                reconciliation.get(
                    "aggregate_exec_qty"
                )
            )
        )

        value = reconciliation.get(
            "aggregate_exec_value"
        )

        if value is not None:
            aggregate_exec_value += (
                _as_decimal(value)
            )
            aggregate_exec_value_known = (
                True
            )

    terminal_count = sum(
        1
        for item in rows
        if suborder_is_terminal(item)
    )

    failed_count = sum(
        1
        for item in rows
        if str(
            item.get("status") or ""
        ).strip() == "failed"
    )

    partial_terminal_count = sum(
        1
        for item in rows
        if str(
            item.get("status") or ""
        ).strip() == "terminal_partial"
    )

    return {
        "planned_suborders": len(rows),
        "terminal_suborders": terminal_count,
        "failed_suborders": failed_count,
        "partial_terminal_suborders": (
            partial_terminal_count
        ),
        "all_terminal": (
            terminal_count == len(rows)
            and bool(rows)
        ),
        "all_filled": (
            bool(rows)
            and all(
                str(
                    item.get("status")
                    or ""
                ).strip() == "filled"
                for item in rows
            )
        ),
        "has_failure": (
            failed_count > 0
        ),
        "has_terminal_partial": (
            partial_terminal_count > 0
        ),
        "aggregate_exec_qty": str(
            aggregate_exec_qty
        ),
        "aggregate_exec_value": (
            str(aggregate_exec_value)
            if aggregate_exec_value_known
            else None
        ),
        "status_counts": status_counts,
    }