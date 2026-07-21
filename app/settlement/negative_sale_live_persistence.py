from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models import FundNegativeSaleLeg
from app.settlement.negative_sale_execution_types import (
    ONE,
    ZERO,
    _max_zero,
    utcnow,
)
from app.settlement.negative_sale_order_intent import (
    NegativeSaleOrderIntentError,
    validate_negative_sale_order_intent,
)
from app.settlement.negative_sale_order_runtime import (
    prepared_intent_runtime_summary,
)
from app.settlement.statuses import (
    SALE_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    SALE_LEG_STATUS_FILLED,
    SALE_LEG_STATUS_PARTIAL_FILLED_ACCEPTED,
    SALE_LEG_STATUS_PARTIAL_FILLED_BELOW_THRESHOLD,
    SALE_LEG_STATUS_PENDING_CONFIRMATION,
)


class NegativeSaleLivePersistenceError(
    RuntimeError
):
    pass


_RUNTIME_ALLOWED_TRANSITIONS = {
    "prepared": {
        "prepared",
        "submitted",
        "pending_confirmation",
    },
    "submitted": {
        "submitted",
        "acknowledged",
        "pending_confirmation",
        "partially_filled_pending_confirmation",
        "filled",
        "terminal_partial",
        "failed",
    },
    "acknowledged": {
        "acknowledged",
        "pending_confirmation",
        "partially_filled_pending_confirmation",
        "filled",
        "terminal_partial",
        "failed",
    },
    "pending_confirmation": {
        "pending_confirmation",
        "partially_filled_pending_confirmation",
        "filled",
        "terminal_partial",
        "failed",
    },
    "partially_filled_pending_confirmation": {
        "pending_confirmation",
        "partially_filled_pending_confirmation",
        "filled",
        "terminal_partial",
        "failed",
    },
    "filled": {
        "filled",
    },
    "terminal_partial": {
        "terminal_partial",
    },
    "failed": {
        "failed",
    },
}


def _decimal(
    value: Any,
    *,
    field_name: str,
    default_zero: bool = False,
) -> Decimal:
    if value is None or value == "":
        if default_zero:
            return ZERO

        raise NegativeSaleLivePersistenceError(
            f"{field_name} is required"
        )

    if isinstance(value, bool):
        raise NegativeSaleLivePersistenceError(
            f"{field_name} must not be bool"
        )

    if isinstance(value, float):
        raise NegativeSaleLivePersistenceError(
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
        raise NegativeSaleLivePersistenceError(
            f"{field_name} is not Decimal"
        ) from exc

    if not result.is_finite():
        raise NegativeSaleLivePersistenceError(
            f"{field_name} must be finite"
        )

    return result


def _runtime_status(
    item: dict[str, Any],
) -> str:
    return str(
        item.get("status")
        or "prepared"
    ).strip()


def _suborders(
    intent: dict[str, Any],
) -> list[dict[str, Any]]:
    raw = intent.get("suborders")

    if not isinstance(raw, list):
        raise NegativeSaleLivePersistenceError(
            "Intent suborders must be a list"
        )

    result: list[dict[str, Any]] = []

    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise NegativeSaleLivePersistenceError(
                f"suborders[{index}] "
                "must be a dict"
            )

        result.append(item)

    return result


def _reconciliation_decimal(
    item: dict[str, Any],
    *,
    field_name: str,
) -> Decimal:
    reconciliation = item.get(
        "reconciliation"
    )

    if not isinstance(
        reconciliation,
        dict,
    ):
        return ZERO

    return _decimal(
        reconciliation.get(field_name),
        field_name=field_name,
        default_zero=True,
    )


def validate_runtime_intent_transition(
    existing_intent: dict[str, Any],
    updated_intent: dict[str, Any],
    *,
    enforce_submit_claim: bool = False,
) -> None:
    try:
        validate_negative_sale_order_intent(
            existing_intent
        )
        validate_negative_sale_order_intent(
            updated_intent
        )
    except NegativeSaleOrderIntentError as exc:
        raise NegativeSaleLivePersistenceError(
            f"Intent validation failed: {exc}"
        ) from exc

    if (
        existing_intent.get(
            "intent_fingerprint"
        )
        != updated_intent.get(
            "intent_fingerprint"
        )
    ):
        raise NegativeSaleLivePersistenceError(
            "Intent fingerprint changed"
        )

    existing_rows = _suborders(
        existing_intent
    )
    updated_rows = _suborders(
        updated_intent
    )

    if len(existing_rows) != len(
        updated_rows
    ):
        raise NegativeSaleLivePersistenceError(
            "Suborder count changed"
        )

    for index, (
        existing,
        updated,
    ) in enumerate(
        zip(
            existing_rows,
            updated_rows,
            strict=True,
        )
    ):
        if (
            existing.get("order_link_id")
            != updated.get("order_link_id")
        ):
            raise NegativeSaleLivePersistenceError(
                f"suborders[{index}] "
                "order_link_id changed"
            )

        existing_status = (
            _runtime_status(existing)
        )
        updated_status = (
            _runtime_status(updated)
        )

        allowed = (
            _RUNTIME_ALLOWED_TRANSITIONS
            .get(existing_status)
        )

        if (
            allowed is None
            or updated_status not in allowed
        ):
            raise NegativeSaleLivePersistenceError(
                "Invalid runtime transition: "
                f"suborder={index}, "
                f"{existing_status} -> "
                f"{updated_status}"
            )

        if enforce_submit_claim:
            if (
                updated_status == "submitted"
                and existing_status
                != "prepared"
            ):
                raise NegativeSaleLivePersistenceError(
                    "Concurrent submit claim "
                    f"detected: suborder={index}"
                )

            if (
                updated_status
                == "acknowledged"
                and existing_status
                != "submitted"
            ):
                raise NegativeSaleLivePersistenceError(
                    "Acknowledgement requires "
                    "durable submitted state: "
                    f"suborder={index}"
                )

        existing_order_id = str(
            existing.get("order_id")
            or ""
        ).strip()
        updated_order_id = str(
            updated.get("order_id")
            or ""
        ).strip()

        if (
            existing_order_id
            and updated_order_id
            and existing_order_id
            != updated_order_id
        ):
            raise NegativeSaleLivePersistenceError(
                f"suborders[{index}] "
                "order_id changed"
            )

        existing_qty = (
            _reconciliation_decimal(
                existing,
                field_name=(
                    "aggregate_exec_qty"
                ),
            )
        )
        updated_qty = (
            _reconciliation_decimal(
                updated,
                field_name=(
                    "aggregate_exec_qty"
                ),
            )
        )

        if updated_qty < existing_qty:
            raise NegativeSaleLivePersistenceError(
                "Confirmed execution quantity "
                "regressed: "
                f"suborder={index}, "
                f"existing={existing_qty}, "
                f"updated={updated_qty}"
            )


def _runtime_aggregates(
    intent: dict[str, Any],
) -> dict[str, Any]:
    aggregate_exec_qty = ZERO
    aggregate_exec_value = ZERO
    aggregate_value_complete = True

    executed_suborders = 0
    fees_by_currency: dict[
        str,
        Decimal,
    ] = {}

    for item in _suborders(intent):
        reconciliation = item.get(
            "reconciliation"
        )

        if not isinstance(
            reconciliation,
            dict,
        ):
            continue

        exec_qty = _decimal(
            reconciliation.get(
                "aggregate_exec_qty"
            ),
            field_name=(
                "aggregate_exec_qty"
            ),
            default_zero=True,
        )

        exec_value_raw = (
            reconciliation.get(
                "aggregate_exec_value"
            )
        )

        aggregate_exec_qty += exec_qty

        if exec_qty > ZERO:
            executed_suborders += 1

            if (
                exec_value_raw is None
                or exec_value_raw == ""
            ):
                aggregate_value_complete = (
                    False
                )
            else:
                aggregate_exec_value += (
                    _decimal(
                        exec_value_raw,
                        field_name=(
                            "aggregate_exec_value"
                        ),
                    )
                )

        raw_fees = reconciliation.get(
            "fees_by_currency"
        )

        if isinstance(raw_fees, dict):
            for currency, raw_amount in (
                raw_fees.items()
            ):
                normalized_currency = str(
                    currency
                ).strip().upper()

                if not normalized_currency:
                    normalized_currency = (
                        "__UNKNOWN__"
                    )

                amount = _decimal(
                    raw_amount,
                    field_name=(
                        "fees_by_currency."
                        f"{normalized_currency}"
                    ),
                    default_zero=True,
                )

                fees_by_currency[
                    normalized_currency
                ] = (
                    fees_by_currency.get(
                        normalized_currency,
                        ZERO,
                    )
                    + amount
                )

    return {
        "aggregate_exec_qty": (
            aggregate_exec_qty
        ),
        "aggregate_exec_value": (
            aggregate_exec_value
            if aggregate_value_complete
            else None
        ),
        "aggregate_value_complete": (
            aggregate_value_complete
        ),
        "executed_suborders": (
            executed_suborders
        ),
        "fees_by_currency": (
            fees_by_currency
        ),
    }


NEGATIVE_SALE_INTENT_HISTORY_SCHEMA = (
    "negative_sale_intent_history_v1"
)


def validated_terminal_intent_history(
    raw_audit: Any,
) -> list[dict[str, Any]]:
    if raw_audit is None:
        return []

    if not isinstance(raw_audit, dict):
        raise NegativeSaleLivePersistenceError(
            "Leg execution audit must be "
            "a dict"
        )

    raw_history = raw_audit.get(
        "intent_history"
    )

    if raw_history is None:
        return []

    if not isinstance(raw_history, list):
        raise NegativeSaleLivePersistenceError(
            "intent_history must be a list"
        )

    result: list[dict[str, Any]] = []
    seen_fingerprints: set[str] = set()
    seen_rounds: set[int] = set()
    previous_round: int | None = None

    for index, raw_entry in enumerate(
        raw_history
    ):
        if not isinstance(raw_entry, dict):
            raise NegativeSaleLivePersistenceError(
                f"intent_history[{index}] "
                "must be a dict"
            )

        entry = deepcopy(raw_entry)

        if entry.get("schema") != (
            NEGATIVE_SALE_INTENT_HISTORY_SCHEMA
        ):
            raise NegativeSaleLivePersistenceError(
                f"intent_history[{index}] "
                "has unsupported schema"
            )

        intent = entry.get("intent")

        if not isinstance(intent, dict):
            raise NegativeSaleLivePersistenceError(
                f"intent_history[{index}] "
                "has no intent dict"
            )

        try:
            validate_negative_sale_order_intent(
                intent
            )
        except NegativeSaleOrderIntentError as exc:
            raise NegativeSaleLivePersistenceError(
                f"intent_history[{index}] "
                f"intent is invalid: {exc}"
            ) from exc

        summary = (
            prepared_intent_runtime_summary(
                intent
            )
        )

        if not summary["all_terminal"]:
            raise NegativeSaleLivePersistenceError(
                f"intent_history[{index}] "
                "intent is not terminal"
            )

        if summary["has_failure"]:
            raise NegativeSaleLivePersistenceError(
                f"intent_history[{index}] "
                "contains failed suborders"
            )

        raw_round = intent.get(
            "execution_round"
        )

        if (
            raw_round is None
            or isinstance(raw_round, bool)
        ):
            raise NegativeSaleLivePersistenceError(
                f"intent_history[{index}] "
                "execution_round is invalid"
            )

        try:
            execution_round = int(
                raw_round
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise NegativeSaleLivePersistenceError(
                f"intent_history[{index}] "
                "execution_round is invalid"
            ) from exc

        if execution_round < 0:
            raise NegativeSaleLivePersistenceError(
                f"intent_history[{index}] "
                "execution_round is negative"
            )

        entry_round = entry.get(
            "execution_round"
        )

        if (
            isinstance(entry_round, bool)
            or int(entry_round)
            != execution_round
        ):
            raise NegativeSaleLivePersistenceError(
                f"intent_history[{index}] "
                "round mismatch"
            )

        fingerprint = str(
            intent.get(
                "intent_fingerprint"
            )
            or ""
        ).strip()

        if (
            entry.get(
                "intent_fingerprint"
            )
            != fingerprint
        ):
            raise NegativeSaleLivePersistenceError(
                f"intent_history[{index}] "
                "fingerprint mismatch"
            )

        if fingerprint in seen_fingerprints:
            raise NegativeSaleLivePersistenceError(
                "Duplicate intent fingerprint "
                "in intent_history"
            )

        if execution_round in seen_rounds:
            raise NegativeSaleLivePersistenceError(
                "Duplicate execution_round "
                "in intent_history"
            )

        if (
            previous_round is not None
            and execution_round
            <= previous_round
        ):
            raise NegativeSaleLivePersistenceError(
                "intent_history rounds are "
                "not strictly increasing"
            )

        seen_fingerprints.add(
            fingerprint
        )
        seen_rounds.add(
            execution_round
        )
        previous_round = execution_round
        result.append(entry)

    return result


def archive_terminal_intent_and_activate_next_round(
    db: Session,
    *,
    leg: FundNegativeSaleLeg,
    new_intent: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    effective_now = now or utcnow()

    if effective_now.tzinfo is None:
        effective_now = (
            effective_now.replace(
                tzinfo=timezone.utc
            )
        )

    effective_now = (
        effective_now.astimezone(
            timezone.utc
        )
    )

    previous = leg.suborders_json

    if not isinstance(previous, dict):
        raise NegativeSaleLivePersistenceError(
            "Current durable intent is "
            "missing"
        )

    previous_intent = deepcopy(
        previous
    )
    next_intent = deepcopy(
        new_intent
    )

    try:
        validate_negative_sale_order_intent(
            previous_intent
        )
        validate_negative_sale_order_intent(
            next_intent
        )
    except NegativeSaleOrderIntentError as exc:
        raise NegativeSaleLivePersistenceError(
            f"Correction intent is invalid: "
            f"{exc}"
        ) from exc

    previous_summary = (
        prepared_intent_runtime_summary(
            previous_intent
        )
    )

    if not previous_summary[
        "all_terminal"
    ]:
        raise NegativeSaleLivePersistenceError(
            "Previous intent must be "
            "terminal before rollover"
        )

    if previous_summary["has_failure"]:
        raise NegativeSaleLivePersistenceError(
            "Failed intent cannot roll over "
            "to a correction round"
        )

    previous_round = int(
        previous_intent[
            "execution_round"
        ]
    )
    next_round = int(
        next_intent[
            "execution_round"
        ]
    )

    if next_round != previous_round + 1:
        raise NegativeSaleLivePersistenceError(
            "Correction execution_round "
            "must increment by one"
        )

    for field_name in (
        "sale_batch_id",
        "leg_id",
    ):
        if (
            int(previous_intent[field_name])
            != int(next_intent[field_name])
        ):
            raise NegativeSaleLivePersistenceError(
                "Correction intent identity "
                f"changed: {field_name}"
            )

    if int(next_intent["leg_id"]) != int(
        leg.id
    ):
        raise NegativeSaleLivePersistenceError(
            "Correction intent leg_id "
            "mismatch"
        )

    previous_fingerprint = str(
        previous_intent[
            "intent_fingerprint"
        ]
    )
    next_fingerprint = str(
        next_intent[
            "intent_fingerprint"
        ]
    )

    if (
        previous_fingerprint
        == next_fingerprint
    ):
        raise NegativeSaleLivePersistenceError(
            "Correction intent fingerprint "
            "did not change"
        )

    existing_audit = (
        deepcopy(
            leg.mock_execution_json
        )
        if isinstance(
            leg.mock_execution_json,
            dict,
        )
        else {}
    )

    history = (
        validated_terminal_intent_history(
            existing_audit
        )
    )

    if any(
        entry["intent_fingerprint"]
        == previous_fingerprint
        for entry in history
    ):
        raise NegativeSaleLivePersistenceError(
            "Previous intent is already "
            "archived"
        )

    history.append(
        {
            "schema": (
                NEGATIVE_SALE_INTENT_HISTORY_SCHEMA
            ),
            "archived_at": (
                effective_now.isoformat()
            ),
            "execution_round": (
                previous_round
            ),
            "intent_fingerprint": (
                previous_fingerprint
            ),
            "runtime_summary": (
                previous_summary
            ),
            "intent": previous_intent,
        }
    )

    validated_terminal_intent_history(
        {
            "intent_history": history,
        }
    )

    suborders = next_intent.get(
        "suborders"
    )

    if (
        not isinstance(suborders, list)
        or not suborders
        or not isinstance(
            suborders[0],
            dict,
        )
    ):
        raise NegativeSaleLivePersistenceError(
            "Next correction intent has "
            "no first suborder"
        )

    first = suborders[0]

    leg.actual_execution_mode = str(
        next_intent.get(
            "actual_execution_mode"
        )
        or "live_market_order"
    )
    leg.execution_round = str(
        next_round
    )
    leg.deterministic_key = str(
        next_intent[
            "deterministic_key"
        ]
    )
    leg.order_link_id = str(
        first["order_link_id"]
    )

    leg.strategy_id = None
    leg.bybit_order_id = None
    leg.bybit_strategy_id = None

    leg.planned_suborders = len(
        suborders
    )
    leg.executed_suborders = 0
    leg.suborders_json = next_intent

    leg.filled_qty = ZERO
    leg.filled_usdt = ZERO
    leg.avg_fill_price = None
    leg.fills_ratio = ZERO
    leg.unfilled_usdt = None
    leg.fee_usdt = ZERO
    leg.cash_delta_usdt = ZERO
    leg.last_price = None

    leg.sent_at = None
    leg.confirmed_at = None
    leg.failed_at = None
    leg.execution_error = None
    leg.error = None

    existing_audit[
        "intent_history"
    ] = history
    existing_audit[
        "active_intent_fingerprint"
    ] = next_fingerprint
    existing_audit[
        "active_execution_round"
    ] = next_round
    existing_audit[
        "state_machine"
    ] = "negative_sale_order_intent_v1"

    leg.mock_execution_json = (
        existing_audit
    )
    leg.updated_at = effective_now

    db.add(leg)
    db.flush()

    # The archived terminal intent and the
    # next immutable correction intent become
    # durable atomically before any new POST.
    db.commit()
    db.refresh(leg)

    return deepcopy(
        leg.suborders_json
    )


def apply_runtime_intent_to_leg(
    *,
    leg: FundNegativeSaleLeg,
    raw_intent: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    effective_now = now or utcnow()

    try:
        validate_negative_sale_order_intent(
            raw_intent
        )
    except NegativeSaleOrderIntentError as exc:
        raise NegativeSaleLivePersistenceError(
            f"Runtime intent is invalid: {exc}"
        ) from exc

    intent = deepcopy(raw_intent)
    rows = _suborders(intent)

    summary = (
        prepared_intent_runtime_summary(
            intent
        )
    )
    aggregates = _runtime_aggregates(
        intent
    )

    normalized_qty = _decimal(
        intent.get("normalized_qty"),
        field_name="normalized_qty",
    )
    aggregate_exec_qty = aggregates[
        "aggregate_exec_qty"
    ]
    aggregate_exec_value = aggregates[
        "aggregate_exec_value"
    ]

    fill_ratio = (
        min(
            aggregate_exec_qty
            / normalized_qty,
            ONE,
        )
        if normalized_qty > ZERO
        else ZERO
    )

    acceptance_ratio = (
        _decimal(
            settings
            .NEGATIVE_NET_SALE_FILL_ACCEPTANCE_PCT,
            field_name=(
                "NEGATIVE_NET_SALE_"
                "FILL_ACCEPTANCE_PCT"
            ),
        )
        / Decimal("100")
    )

    if summary["has_failure"]:
        leg_status = (
            SALE_LEG_STATUS_FAILED_REQUIRES_REVIEW
        )
    elif not summary["all_terminal"]:
        leg_status = (
            SALE_LEG_STATUS_PENDING_CONFIRMATION
        )
    elif summary["all_filled"]:
        leg_status = SALE_LEG_STATUS_FILLED
    elif aggregate_exec_qty > ZERO:
        leg_status = (
            SALE_LEG_STATUS_PARTIAL_FILLED_ACCEPTED
            if fill_ratio
            >= acceptance_ratio
            else (
                SALE_LEG_STATUS_PARTIAL_FILLED_BELOW_THRESHOLD
            )
        )
    else:
        leg_status = (
            SALE_LEG_STATUS_FAILED_REQUIRES_REVIEW
        )

    category = str(
        intent.get("category")
        or ""
    ).strip().lower()

    is_derivative = category in {
        "linear",
        "inverse",
        "option",
    }

    fees_by_currency = aggregates[
        "fees_by_currency"
    ]
    fee_usdt = fees_by_currency.get(
        "USDT",
        ZERO,
    )

    if is_derivative:
        # Derivative execution value/notional
        # must never be treated as cash.
        filled_usdt = ZERO
        cash_delta_usdt = ZERO
        unfilled_usdt = ZERO
    else:
        filled_usdt = (
            aggregate_exec_value
        )

        cash_delta_usdt = (
            _max_zero(
                aggregate_exec_value
                - fee_usdt
            )
            if aggregate_exec_value
            is not None
            else None
        )

        target_cash_usdt = _decimal(
            intent.get(
                "target_cash_usdt"
            ),
            field_name=(
                "target_cash_usdt"
            ),
            default_zero=True,
        )

        unfilled_usdt = (
            _max_zero(
                target_cash_usdt
                - cash_delta_usdt
            )
            if cash_delta_usdt
            is not None
            else None
        )

    avg_fill_price = (
        aggregate_exec_value
        / aggregate_exec_qty
        if (
            aggregate_exec_value
            is not None
            and aggregate_exec_qty > ZERO
        )
        else None
    )

    order_ids = [
        str(item.get("order_id"))
        for item in rows
        if item.get("order_id")
    ]
    order_link_ids = [
        str(item.get("order_link_id"))
        for item in rows
        if item.get("order_link_id")
    ]

    has_submit_state = any(
        _runtime_status(item)
        != "prepared"
        for item in rows
    )

    leg.actual_execution_mode = str(
        intent.get(
            "actual_execution_mode"
        )
        or "live_market_order"
    )
    leg.execution_round = str(
        intent.get("execution_round")
    )
    leg.deterministic_key = str(
        intent.get("deterministic_key")
        or ""
    )

    leg.order_link_id = (
        order_link_ids[0]
        if order_link_ids
        else None
    )
    leg.bybit_order_id = (
        order_ids[0]
        if order_ids
        else None
    )
    leg.bybit_strategy_id = None

    leg.planned_suborders = len(rows)
    leg.executed_suborders = int(
        aggregates["executed_suborders"]
    )
    leg.suborders_json = intent

    leg.filled_qty = aggregate_exec_qty
    leg.filled_usdt = filled_usdt
    leg.avg_fill_price = avg_fill_price
    leg.fill_ratio = fill_ratio
    leg.unfilled_usdt = unfilled_usdt
    leg.fee_usdt = fee_usdt
    leg.cash_delta_usdt = (
        cash_delta_usdt
    )

    leg.last_price = avg_fill_price

    if (
        has_submit_state
        and leg.sent_at is None
    ):
        leg.sent_at = effective_now

    if (
        summary["all_terminal"]
        and not summary["has_failure"]
    ):
        leg.confirmed_at = effective_now
        leg.failed_at = None
        leg.execution_error = None
    elif summary["has_failure"]:
        leg.confirmed_at = None
        leg.failed_at = effective_now
        leg.execution_error = (
            "One or more durable Bybit "
            "suborders failed."
        )
    else:
        leg.confirmed_at = None
        leg.failed_at = None
        leg.execution_error = None

    leg.status = leg_status
    leg.updated_at = effective_now

    audit = {
        "mock_only": False,
        "state_machine": (
            "negative_sale_order_intent_v1"
        ),
        "runtime_summary": summary,
        "fees_by_currency": {
            currency: str(amount)
            for currency, amount
            in fees_by_currency.items()
        },
        "derivative_execution_value_is_cash": (
            False
        ),
        "cash_source_of_truth": (
            "confirmed_transferable_"
            "usdt_balance"
        ),
        "diagnostic_spot_cash_delta_usdt": (
            str(cash_delta_usdt)
            if cash_delta_usdt is not None
            else None
        ),
    }

    existing_audit = (
        deepcopy(
            leg.mock_execution_json
        )
        if isinstance(
            leg.mock_execution_json,
            dict,
        )
        else {}
    )

    intent_history = (
        validated_terminal_intent_history(
            existing_audit
        )
    )

    if intent_history:
        audit["intent_history"] = (
            intent_history
        )

    audit[
        "active_intent_fingerprint"
    ] = str(
        intent.get(
            "intent_fingerprint"
        )
        or ""
    )
    audit[
        "active_execution_round"
    ] = int(
        intent.get(
            "execution_round"
        )
    )

    leg.mock_execution_json = audit

    return audit


def persist_runtime_intent_state(
    db: Session,
    *,
    leg_id: int,
    raw_intent: dict[str, Any],
    now: datetime | None = None,
) -> FundNegativeSaleLeg:
    effective_now = now or utcnow()

    leg = (
        db.query(FundNegativeSaleLeg)
        .filter(
            FundNegativeSaleLeg.id
            == int(leg_id)
        )
        .with_for_update()
        .first()
    )

    if leg is None:
        raise NegativeSaleLivePersistenceError(
            "Negative sale leg not found: "
            f"{leg_id}"
        )

    existing = leg.suborders_json

    if not isinstance(existing, dict):
        raise NegativeSaleLivePersistenceError(
            "Durable prepared intent "
            "is missing"
        )

    validate_runtime_intent_transition(
        existing,
        raw_intent,
        enforce_submit_claim=True,
    )

    apply_runtime_intent_to_leg(
        leg=leg,
        raw_intent=raw_intent,
        now=effective_now,
    )

    db.add(leg)
    db.flush()
    db.commit()
    db.refresh(leg)

    return leg