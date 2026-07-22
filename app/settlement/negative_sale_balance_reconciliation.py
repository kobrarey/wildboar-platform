from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Any


ZERO = Decimal("0")

NEGATIVE_SALE_BALANCE_RECONCILIATION_SCHEMA = (
    "negative_sale_balance_reconciliation_v2"
)


class NegativeSaleBalanceReconciliationError(
    RuntimeError
):
    pass


def _decimal(
    value: Any,
    *,
    field_name: str,
    non_negative: bool = True,
) -> Decimal:
    if isinstance(value, bool):
        raise NegativeSaleBalanceReconciliationError(
            f"{field_name} must not be bool"
        )

    if isinstance(value, float):
        raise NegativeSaleBalanceReconciliationError(
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
        raise NegativeSaleBalanceReconciliationError(
            f"{field_name} is not Decimal"
        ) from exc

    if not result.is_finite():
        raise NegativeSaleBalanceReconciliationError(
            f"{field_name} must be finite"
        )

    if non_negative and result < ZERO:
        raise NegativeSaleBalanceReconciliationError(
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
        raise NegativeSaleBalanceReconciliationError(
            f"{field_name} must not be empty"
        )

    return result


def _optional_text(
    value: Any,
) -> str | None:
    result = str(
        value or ""
    ).strip()

    return result or None


def _optional_positive_int(
    value: Any,
    *,
    field_name: str,
) -> int | None:
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        raise NegativeSaleBalanceReconciliationError(
            f"{field_name} must not be bool"
        )

    try:
        result = int(value)
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise NegativeSaleBalanceReconciliationError(
            f"{field_name} must be int"
        ) from exc

    if result <= 0:
        raise NegativeSaleBalanceReconciliationError(
            f"{field_name} must be positive"
        )

    return result


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


def _canonical_json(
    value: Any,
) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise NegativeSaleBalanceReconciliationError(
            "Balance payload is not "
            "JSON-serializable"
        ) from exc


def _fingerprint(
    value: Any,
) -> str:
    return sha256(
        _canonical_json(
            value
        ).encode("utf-8")
    ).hexdigest()


def balance_refresh_action_key(
    *,
    action_type: str,
    active_leg_id: int | None,
    order_link_id: str | None,
) -> str:
    projection = {
        "action_type": _required_text(
            action_type,
            field_name="action_type",
        ),
        "active_leg_id": (
            _optional_positive_int(
                active_leg_id,
                field_name="active_leg_id",
            )
        ),
        "order_link_id": (
            _optional_text(
                order_link_id
            )
        ),
    }

    return _fingerprint(
        projection
    )


def _record_event_projection(
    record: dict[str, Any],
) -> dict[str, Any]:
    return {
        "action_key": record.get(
            "action_key"
        ),
        "action_type": record.get(
            "action_type"
        ),
        "active_leg_id": record.get(
            "active_leg_id"
        ),
        "order_link_id": record.get(
            "order_link_id"
        ),
        "required_master_usdt": (
            record.get(
                "required_master_usdt"
            )
        ),
        "balance_after_usdt": (
            record.get(
                "balance_after_usdt"
            )
        ),
        "confirmed_shortage_usdt": (
            record.get(
                "confirmed_shortage_usdt"
            )
        ),
        "confirmed_surplus_usdt": (
            record.get(
                "confirmed_surplus_usdt"
            )
        ),
        "raw_fingerprint": (
            record.get(
                "raw_fingerprint"
            )
        ),
    }


def _record_event_id(
    record: dict[str, Any],
) -> str:
    return _fingerprint(
        _record_event_projection(
            record
        )
    )


def _balance_payload_for_fingerprint(
    transferable_balance: dict[str, Any],
) -> Any:
    raw = transferable_balance.get(
        "raw"
    )

    return (
        raw
        if raw is not None
        else transferable_balance
    )


def validate_balance_reconciliation_json(
    raw: dict[str, Any],
) -> None:
    if not isinstance(raw, dict):
        raise NegativeSaleBalanceReconciliationError(
            "Reconciliation JSON must "
            "be a dict"
        )

    if (
        raw.get("schema")
        != NEGATIVE_SALE_BALANCE_RECONCILIATION_SCHEMA
    ):
        raise NegativeSaleBalanceReconciliationError(
            "Unsupported balance "
            "reconciliation schema"
        )

    if (
        raw.get("cash_source")
        != "confirmed_transferable_usdt"
    ):
        raise NegativeSaleBalanceReconciliationError(
            "Unsupported reconciliation "
            "cash source"
        )

    history = raw.get(
        "refresh_history"
    )

    if not isinstance(history, list):
        raise NegativeSaleBalanceReconciliationError(
            "refresh_history must be a list"
        )

    event_ids: set[str] = set()

    for index, item in enumerate(history):
        if not isinstance(item, dict):
            raise NegativeSaleBalanceReconciliationError(
                f"refresh_history[{index}] "
                "must be a dict"
            )

        action_type = _required_text(
            item.get("action_type"),
            field_name=(
                f"refresh_history[{index}]"
                ".action_type"
            ),
        )
        active_leg_id = (
            _optional_positive_int(
                item.get(
                    "active_leg_id"
                ),
                field_name=(
                    f"refresh_history[{index}]"
                    ".active_leg_id"
                ),
            )
        )
        order_link_id = (
            _optional_text(
                item.get(
                    "order_link_id"
                )
            )
        )

        expected_action_key = (
            balance_refresh_action_key(
                action_type=action_type,
                active_leg_id=(
                    active_leg_id
                ),
                order_link_id=(
                    order_link_id
                ),
            )
        )

        if (
            item.get("action_key")
            != expected_action_key
        ):
            raise NegativeSaleBalanceReconciliationError(
                "Balance refresh action_key "
                f"mismatch at index {index}"
            )

        required = _decimal(
            item.get(
                "required_master_usdt"
            ),
            field_name=(
                f"refresh_history[{index}]"
                ".required_master_usdt"
            ),
        )
        balance_after = _decimal(
            item.get(
                "balance_after_usdt"
            ),
            field_name=(
                f"refresh_history[{index}]"
                ".balance_after_usdt"
            ),
        )

        before_raw = item.get(
            "balance_before_usdt"
        )

        if before_raw is not None:
            _decimal(
                before_raw,
                field_name=(
                    f"refresh_history[{index}]"
                    ".balance_before_usdt"
                ),
            )

        shortage = _decimal(
            item.get(
                "confirmed_shortage_usdt"
            ),
            field_name=(
                f"refresh_history[{index}]"
                ".confirmed_shortage_usdt"
            ),
        )
        surplus = _decimal(
            item.get(
                "confirmed_surplus_usdt"
            ),
            field_name=(
                f"refresh_history[{index}]"
                ".confirmed_surplus_usdt"
            ),
        )

        if shortage != max(
            required - balance_after,
            ZERO,
        ):
            raise NegativeSaleBalanceReconciliationError(
                "Confirmed shortage mismatch "
                f"at index {index}"
            )

        if surplus != max(
            balance_after - required,
            ZERO,
        ):
            raise NegativeSaleBalanceReconciliationError(
                "Confirmed surplus mismatch "
                f"at index {index}"
            )

        transferable_balance = item.get(
            "transferable_balance"
        )

        if not isinstance(
            transferable_balance,
            dict,
        ):
            raise NegativeSaleBalanceReconciliationError(
                "transferable_balance must "
                f"be a dict at index {index}"
            )

        expected_raw_fingerprint = (
            _fingerprint(
                _balance_payload_for_fingerprint(
                    transferable_balance
                )
            )
        )

        if (
            item.get("raw_fingerprint")
            != expected_raw_fingerprint
        ):
            raise NegativeSaleBalanceReconciliationError(
                "Balance raw fingerprint "
                f"mismatch at index {index}"
            )

        expected_event_id = (
            _record_event_id(item)
        )
        actual_event_id = _required_text(
            item.get("event_id"),
            field_name=(
                f"refresh_history[{index}]"
                ".event_id"
            ),
        )

        if (
            actual_event_id
            != expected_event_id
        ):
            raise NegativeSaleBalanceReconciliationError(
                "Balance event_id mismatch "
                f"at index {index}"
            )

        if actual_event_id in event_ids:
            raise NegativeSaleBalanceReconciliationError(
                "Duplicate balance event_id: "
                f"{actual_event_id}"
            )

        event_ids.add(
            actual_event_id
        )

        _required_text(
            item.get("captured_at"),
            field_name=(
                f"refresh_history[{index}]"
                ".captured_at"
            ),
        )

    latest = raw.get(
        "latest_refresh"
    )

    if history:
        if latest != history[-1]:
            raise NegativeSaleBalanceReconciliationError(
                "latest_refresh must equal "
                "the final history item"
            )

        latest_item = history[-1]

        if (
            raw.get(
                "required_master_usdt"
            )
            != latest_item[
                "required_master_usdt"
            ]
        ):
            raise NegativeSaleBalanceReconciliationError(
                "Top-level required amount "
                "does not match latest refresh"
            )

        if (
            raw.get(
                "confirmed_available_usdt"
            )
            != latest_item[
                "balance_after_usdt"
            ]
        ):
            raise NegativeSaleBalanceReconciliationError(
                "Top-level available balance "
                "does not match latest refresh"
            )

        if (
            raw.get(
                "confirmed_shortage_usdt"
            )
            != latest_item[
                "confirmed_shortage_usdt"
            ]
        ):
            raise NegativeSaleBalanceReconciliationError(
                "Top-level shortage does not "
                "match latest refresh"
            )

        if (
            raw.get(
                "confirmed_surplus_usdt"
            )
            != latest_item[
                "confirmed_surplus_usdt"
            ]
        ):
            raise NegativeSaleBalanceReconciliationError(
                "Top-level surplus does not "
                "match latest refresh"
            )

    elif latest is not None:
        raise NegativeSaleBalanceReconciliationError(
            "latest_refresh must be None "
            "when history is empty"
        )


def latest_confirmed_available_usdt(
    raw: Any,
) -> Decimal | None:
    if not isinstance(raw, dict):
        return None

    if (
        raw.get("schema")
        != NEGATIVE_SALE_BALANCE_RECONCILIATION_SCHEMA
    ):
        return None

    validate_balance_reconciliation_json(
        raw
    )

    value = raw.get(
        "confirmed_available_usdt"
    )

    if value is None:
        return None

    return _decimal(
        value,
        field_name=(
            "confirmed_available_usdt"
        ),
    )


def has_balance_refresh_for_action(
    raw: Any,
    *,
    action_type: str,
    active_leg_id: int | None,
    order_link_id: str | None,
) -> bool:
    if not isinstance(raw, dict):
        return False

    if (
        raw.get("schema")
        != NEGATIVE_SALE_BALANCE_RECONCILIATION_SCHEMA
    ):
        return False

    validate_balance_reconciliation_json(
        raw
    )

    expected_action_key = (
        balance_refresh_action_key(
            action_type=action_type,
            active_leg_id=(
                active_leg_id
            ),
            order_link_id=(
                order_link_id
            ),
        )
    )

    history = raw[
        "refresh_history"
    ]

    return any(
        item.get("action_key")
        == expected_action_key
        for item in history
    )


def append_confirmed_balance_refresh(
    *,
    existing_reconciliation_json: Any,
    required_master_usdt: Any,
    balance_before_usdt: Any | None,
    balance_after_usdt: Any,
    transferable_balance: dict[str, Any],
    action_type: str,
    active_leg_id: int | None,
    order_link_id: str | None,
    captured_at: datetime,
) -> dict[str, Any]:
    required = _decimal(
        required_master_usdt,
        field_name="required_master_usdt",
    )
    balance_after = _decimal(
        balance_after_usdt,
        field_name="balance_after_usdt",
    )

    normalized_action_type = (
        _required_text(
            action_type,
            field_name="action_type",
        )
    )
    normalized_leg_id = (
        _optional_positive_int(
            active_leg_id,
            field_name="active_leg_id",
        )
    )
    normalized_order_link_id = (
        _optional_text(
            order_link_id
        )
    )

    if not isinstance(
        transferable_balance,
        dict,
    ):
        raise NegativeSaleBalanceReconciliationError(
            "transferable_balance must "
            "be a dict"
        )

    existing = (
        deepcopy(
            existing_reconciliation_json
        )
        if isinstance(
            existing_reconciliation_json,
            dict,
        )
        else {}
    )

    same_schema = (
        existing.get("schema")
        == NEGATIVE_SALE_BALANCE_RECONCILIATION_SCHEMA
    )

    if same_schema:
        validate_balance_reconciliation_json(
            existing
        )

        history = [
            deepcopy(item)
            for item in existing[
                "refresh_history"
            ]
        ]
    else:
        history = []

    if balance_before_usdt is None:
        if history:
            balance_before = _decimal(
                history[-1][
                    "balance_after_usdt"
                ],
                field_name=(
                    "previous_balance_after_usdt"
                ),
            )
        else:
            balance_before = None
    else:
        balance_before = _decimal(
            balance_before_usdt,
            field_name=(
                "balance_before_usdt"
            ),
        )

    shortage = max(
        required - balance_after,
        ZERO,
    )
    surplus = max(
        balance_after - required,
        ZERO,
    )

    action_key = (
        balance_refresh_action_key(
            action_type=(
                normalized_action_type
            ),
            active_leg_id=(
                normalized_leg_id
            ),
            order_link_id=(
                normalized_order_link_id
            ),
        )
    )

    raw_fingerprint = _fingerprint(
        _balance_payload_for_fingerprint(
            transferable_balance
        )
    )

    record: dict[str, Any] = {
        "event_id": None,
        "action_key": action_key,
        "action_type": (
            normalized_action_type
        ),
        "active_leg_id": (
            normalized_leg_id
        ),
        "order_link_id": (
            normalized_order_link_id
        ),
        "required_master_usdt": str(
            required
        ),
        "balance_before_usdt": (
            str(balance_before)
            if balance_before is not None
            else None
        ),
        "balance_after_usdt": str(
            balance_after
        ),
        "confirmed_shortage_usdt": str(
            shortage
        ),
        "confirmed_surplus_usdt": str(
            surplus
        ),
        "captured_at": _iso(
            captured_at
        ),
        "raw_fingerprint": (
            raw_fingerprint
        ),
        "transferable_balance": (
            deepcopy(
                transferable_balance
            )
        ),
    }

    record["event_id"] = (
        _record_event_id(
            record
        )
    )

    duplicate = next(
        (
            item
            for item in history
            if item.get("event_id")
            == record["event_id"]
        ),
        None,
    )

    if duplicate is not None:
        return deepcopy(existing)

    history.append(record)

    result: dict[str, Any] = {
        "schema": (
            NEGATIVE_SALE_BALANCE_RECONCILIATION_SCHEMA
        ),
        "cash_source": (
            "confirmed_transferable_usdt"
        ),
        "required_master_usdt": str(
            required
        ),
        "confirmed_available_usdt": str(
            balance_after
        ),
        "confirmed_shortage_usdt": str(
            shortage
        ),
        "confirmed_surplus_usdt": str(
            surplus
        ),
        "latest_refresh": deepcopy(
            record
        ),
        "refresh_history": history,
        "safety": {
            "confirmed_transferable_usdt_"
            "is_only_cash_source": True,
            "no_derivative_exec_value_"
            "as_cash": True,
            "append_only_history": True,
            "idempotent_event_id": True,
            "no_transfer": True,
            "no_withdrawal": True,
            "no_bsc_action": True,
            "no_accounting_finalization": (
                True
            ),
        },
    }

    if existing and not same_schema:
        result[
            "previous_reconciliation_json"
        ] = existing

    validate_balance_reconciliation_json(
        result
    )

    return result