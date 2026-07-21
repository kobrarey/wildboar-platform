from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any

from app.bybit.order_execution import (
    build_market_order_payload,
)


ZERO = Decimal("0")

NEGATIVE_SALE_ORDER_INTENT_SCHEMA = (
    "negative_sale_order_intent_v1"
)


class NegativeSaleOrderIntentError(
    RuntimeError
):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _decimal(
    value: Any,
    *,
    field_name: str,
) -> Decimal:
    if isinstance(value, bool):
        raise NegativeSaleOrderIntentError(
            f"{field_name} must not be bool"
        )

    if isinstance(value, float):
        raise NegativeSaleOrderIntentError(
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
        raise NegativeSaleOrderIntentError(
            f"{field_name} is not Decimal"
        ) from exc

    if not result.is_finite():
        raise NegativeSaleOrderIntentError(
            f"{field_name} must be finite"
        )

    return result


def _positive_decimal(
    value: Any,
    *,
    field_name: str,
) -> Decimal:
    result = _decimal(
        value,
        field_name=field_name,
    )

    if result <= ZERO:
        raise NegativeSaleOrderIntentError(
            f"{field_name} must be positive"
        )

    return result


def _required_text(
    value: Any,
    *,
    field_name: str,
) -> str:
    text = str(value or "").strip()

    if not text:
        raise NegativeSaleOrderIntentError(
            f"{field_name} must not be empty"
        )

    return text


def _json_value(
    value: Any,
) -> Any:
    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, datetime):
        effective = value

        if effective.tzinfo is None:
            effective = effective.replace(
                tzinfo=timezone.utc
            )

        return effective.astimezone(
            timezone.utc
        ).isoformat()

    if isinstance(value, dict):
        return {
            str(key): _json_value(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple)):
        return [
            _json_value(item)
            for item in value
        ]

    return value


def _canonical_json(
    value: dict[str, Any],
) -> str:
    return json.dumps(
        _json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def _fingerprint(
    value: dict[str, Any],
) -> str:
    canonical = _canonical_json(value)

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def _immutable_intent_body(
    raw: dict[str, Any],
) -> dict[str, Any]:
    immutable_fields = (
        "schema",
        "prepared_at",
        "sale_batch_id",
        "leg_id",
        "execution_round",
        "deterministic_key",
        "actual_execution_mode",
        "category",
        "symbol",
        "position_side",
        "close_side",
        "position_idx",
        "reduce_only",
        "market_unit",
        "requested_qty",
        "normalized_qty",
        "target_cash_usdt",
        "instrument_snapshot",
        "position_snapshot",
        "planned_suborders",
    )

    body = {
        field_name: raw.get(field_name)
        for field_name in immutable_fields
    }

    raw_suborders = raw.get("suborders")

    if not isinstance(raw_suborders, list):
        body["suborders"] = raw_suborders
        return body

    body["suborders"] = [
        {
            "suborder_index": (
                item.get("suborder_index")
            ),
            "order_link_id": (
                item.get("order_link_id")
            ),
            "qty": item.get("qty"),
            "payload": item.get("payload"),
        }
        if isinstance(item, dict)
        else item
        for item in raw_suborders
    ]

    return body


def deterministic_negative_sale_order_link_id(
    *,
    sale_batch_id: int,
    leg_id: int,
    execution_round: int,
    suborder_index: int,
) -> str:
    batch_id = int(sale_batch_id)
    normalized_leg_id = int(leg_id)
    normalized_round = int(execution_round)
    normalized_index = int(suborder_index)

    if batch_id <= 0:
        raise NegativeSaleOrderIntentError(
            "sale_batch_id must be positive"
        )

    if normalized_leg_id <= 0:
        raise NegativeSaleOrderIntentError(
            "leg_id must be positive"
        )

    if normalized_round < 0:
        raise NegativeSaleOrderIntentError(
            "execution_round must be "
            "non-negative"
        )

    if normalized_index < 0:
        raise NegativeSaleOrderIntentError(
            "suborder_index must be "
            "non-negative"
        )

    readable = (
        f"wbns-{batch_id}-{normalized_leg_id}"
        f"-r{normalized_round}"
        f"-s{normalized_index}"
    )

    if len(readable) <= 36:
        return readable

    material = (
        f"{batch_id}:"
        f"{normalized_leg_id}:"
        f"{normalized_round}:"
        f"{normalized_index}"
    )

    digest = hashlib.sha256(
        material.encode("ascii")
    ).hexdigest()[:28]

    result = f"wbns-{digest}"

    if len(result) > 36:
        raise NegativeSaleOrderIntentError(
            "Generated orderLinkId exceeds "
            "Bybit limit"
        )

    return result


def deterministic_negative_sale_intent_key(
    *,
    sale_batch_id: int,
    leg_id: int,
    execution_round: int,
) -> str:
    return (
        "negative-sale-intent:"
        f"{int(sale_batch_id)}:"
        f"{int(leg_id)}:"
        f"r{int(execution_round)}"
    )


@dataclass(frozen=True)
class PreparedNegativeSaleSuborder:
    suborder_index: int
    order_link_id: str
    qty: Decimal
    payload: dict[str, Any]

    status: str = "prepared"
    order_id: str | None = None
    submitted_at: datetime | None = None
    acknowledged_at: datetime | None = None
    terminal_at: datetime | None = None
    reconciliation: (
        dict[str, Any] | None
    ) = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "suborder_index": (
                self.suborder_index
            ),
            "order_link_id": (
                self.order_link_id
            ),
            "qty": str(self.qty),
            "payload": _json_value(
                self.payload
            ),
            "status": self.status,
            "order_id": self.order_id,
            "submitted_at": _json_value(
                self.submitted_at
            ),
            "acknowledged_at": _json_value(
                self.acknowledged_at
            ),
            "terminal_at": _json_value(
                self.terminal_at
            ),
            "reconciliation": _json_value(
                self.reconciliation
            ),
        }


@dataclass(frozen=True)
class NegativeSaleOrderIntent:
    prepared_at: datetime

    sale_batch_id: int
    leg_id: int
    execution_round: int

    deterministic_key: str
    actual_execution_mode: str

    category: str
    symbol: str

    position_side: str | None
    close_side: str
    position_idx: int | None

    reduce_only: bool | None
    market_unit: str | None

    requested_qty: Decimal
    normalized_qty: Decimal
    target_cash_usdt: Decimal

    instrument_snapshot: dict[str, Any]
    position_snapshot: dict[str, Any]

    suborders: tuple[
        PreparedNegativeSaleSuborder,
        ...,
    ]

    def body_dict(self) -> dict[str, Any]:
        return {
            "schema": (
                NEGATIVE_SALE_ORDER_INTENT_SCHEMA
            ),
            "prepared_at": _json_value(
                self.prepared_at
            ),
            "sale_batch_id": (
                self.sale_batch_id
            ),
            "leg_id": self.leg_id,
            "execution_round": (
                self.execution_round
            ),
            "deterministic_key": (
                self.deterministic_key
            ),
            "actual_execution_mode": (
                self.actual_execution_mode
            ),
            "category": self.category,
            "symbol": self.symbol,
            "position_side": (
                self.position_side
            ),
            "close_side": self.close_side,
            "position_idx": (
                self.position_idx
            ),
            "reduce_only": (
                self.reduce_only
            ),
            "market_unit": (
                self.market_unit
            ),
            "requested_qty": str(
                self.requested_qty
            ),
            "normalized_qty": str(
                self.normalized_qty
            ),
            "target_cash_usdt": str(
                self.target_cash_usdt
            ),
            "instrument_snapshot": (
                _json_value(
                    self.instrument_snapshot
                )
            ),
            "position_snapshot": (
                _json_value(
                    self.position_snapshot
                )
            ),
            "planned_suborders": len(
                self.suborders
            ),
            "suborders": [
                item.to_dict()
                for item in self.suborders
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        body = self.body_dict()

        return {
            **body,
            "intent_fingerprint": (
                _fingerprint(
                    _immutable_intent_body(
                        body
                    )
                )
            ),
        }


def build_negative_sale_order_intent(
    *,
    sale_batch_id: int,
    leg_id: int,
    execution_round: int,
    category: str,
    symbol: str,
    position_side: str | None,
    close_side: str,
    position_idx: int | None,
    reduce_only: bool | None,
    market_unit: str | None,
    requested_qty: Any,
    normalized_qty: Any,
    target_cash_usdt: Any,
    slices: list[Any] | tuple[Any, ...],
    instrument_snapshot: (
        dict[str, Any] | None
    ) = None,
    position_snapshot: (
        dict[str, Any] | None
    ) = None,
    prepared_at: datetime | None = None,
) -> NegativeSaleOrderIntent:
    normalized_category = _required_text(
        category,
        field_name="category",
    ).lower()
    normalized_symbol = _required_text(
        symbol,
        field_name="symbol",
    ).upper()
    normalized_close_side = (
        _required_text(
            close_side,
            field_name="close_side",
        )
    )

    requested = _positive_decimal(
        requested_qty,
        field_name="requested_qty",
    )
    normalized = _positive_decimal(
        normalized_qty,
        field_name="normalized_qty",
    )
    target_cash = _decimal(
        target_cash_usdt,
        field_name="target_cash_usdt",
    )

    if target_cash < ZERO:
        raise NegativeSaleOrderIntentError(
            "target_cash_usdt must be "
            "non-negative"
        )

    parsed_slices = tuple(
        _positive_decimal(
            value,
            field_name=(
                f"slices[{index}]"
            ),
        )
        for index, value
        in enumerate(slices)
    )

    if not parsed_slices:
        raise NegativeSaleOrderIntentError(
            "At least one suborder slice "
            "is required"
        )

    if (
        sum(parsed_slices, ZERO)
        != normalized
    ):
        raise NegativeSaleOrderIntentError(
            "Suborder slices do not sum "
            "to normalized_qty"
        )

    suborders: list[
        PreparedNegativeSaleSuborder
    ] = []

    for index, qty in enumerate(
        parsed_slices
    ):
        order_link_id = (
            deterministic_negative_sale_order_link_id(
                sale_batch_id=(
                    sale_batch_id
                ),
                leg_id=leg_id,
                execution_round=(
                    execution_round
                ),
                suborder_index=index,
            )
        )

        payload = build_market_order_payload(
            category=normalized_category,
            symbol=normalized_symbol,
            side=normalized_close_side,
            qty=qty,
            order_link_id=order_link_id,
            reduce_only=reduce_only,
            position_idx=position_idx,
            market_unit=market_unit,
        )

        suborders.append(
            PreparedNegativeSaleSuborder(
                suborder_index=index,
                order_link_id=(
                    order_link_id
                ),
                qty=qty,
                payload=payload,
            )
        )

    effective_prepared_at = (
        prepared_at or utcnow()
    )

    if effective_prepared_at.tzinfo is None:
        effective_prepared_at = (
            effective_prepared_at.replace(
                tzinfo=timezone.utc
            )
        )

    return NegativeSaleOrderIntent(
        prepared_at=(
            effective_prepared_at
            .astimezone(timezone.utc)
        ),
        sale_batch_id=int(
            sale_batch_id
        ),
        leg_id=int(leg_id),
        execution_round=int(
            execution_round
        ),
        deterministic_key=(
            deterministic_negative_sale_intent_key(
                sale_batch_id=(
                    sale_batch_id
                ),
                leg_id=leg_id,
                execution_round=(
                    execution_round
                ),
            )
        ),
        actual_execution_mode=(
            "live_market_order"
        ),
        category=normalized_category,
        symbol=normalized_symbol,
        position_side=(
            str(position_side)
            if position_side is not None
            else None
        ),
        close_side=(
            normalized_close_side
        ),
        position_idx=position_idx,
        reduce_only=reduce_only,
        market_unit=market_unit,
        requested_qty=requested,
        normalized_qty=normalized,
        target_cash_usdt=target_cash,
        instrument_snapshot=dict(
            instrument_snapshot or {}
        ),
        position_snapshot=dict(
            position_snapshot or {}
        ),
        suborders=tuple(suborders),
    )


def validate_negative_sale_order_intent(
    raw: dict[str, Any],
) -> None:
    if not isinstance(raw, dict):
        raise NegativeSaleOrderIntentError(
            "Prepared intent must be a dict"
        )

    if raw.get("schema") != (
        NEGATIVE_SALE_ORDER_INTENT_SCHEMA
    ):
        raise NegativeSaleOrderIntentError(
            "Unsupported prepared intent "
            "schema"
        )

    supplied_fingerprint = str(
        raw.get("intent_fingerprint")
        or ""
    ).strip()

    if not supplied_fingerprint:
        raise NegativeSaleOrderIntentError(
            "Prepared intent fingerprint "
            "is missing"
        )

    actual_fingerprint = _fingerprint(
        _immutable_intent_body(raw)
    )

    if (
        actual_fingerprint
        != supplied_fingerprint
    ):
        raise NegativeSaleOrderIntentError(
            "Prepared intent fingerprint "
            "mismatch"
        )

    suborders = raw.get("suborders")

    if not isinstance(suborders, list):
        raise NegativeSaleOrderIntentError(
            "Prepared intent suborders "
            "must be a list"
        )

    planned_suborders = int(
        raw.get("planned_suborders")
        or 0
    )

    if planned_suborders != len(
        suborders
    ):
        raise NegativeSaleOrderIntentError(
            "planned_suborders does not "
            "match suborders length"
        )

    seen_order_link_ids: set[str] = set()
    normalized_total = ZERO

    for index, item in enumerate(
        suborders
    ):
        if not isinstance(item, dict):
            raise NegativeSaleOrderIntentError(
                f"suborders[{index}] "
                "must be a dict"
            )

        order_link_id = str(
            item.get("order_link_id")
            or ""
        ).strip()

        if not order_link_id:
            raise NegativeSaleOrderIntentError(
                f"suborders[{index}] "
                "order_link_id is missing"
            )

        if order_link_id in (
            seen_order_link_ids
        ):
            raise NegativeSaleOrderIntentError(
                "Duplicate order_link_id "
                "inside prepared intent"
            )

        seen_order_link_ids.add(
            order_link_id
        )

        payload = item.get("payload")

        if not isinstance(payload, dict):
            raise NegativeSaleOrderIntentError(
                f"suborders[{index}] "
                "payload must be a dict"
            )

        if (
            payload.get("orderLinkId")
            != order_link_id
        ):
            raise NegativeSaleOrderIntentError(
                "Suborder payload "
                "orderLinkId mismatch"
            )

        item_qty = _positive_decimal(
            item.get("qty"),
            field_name=(
                f"suborders[{index}].qty"
            ),
        )

        payload_qty = _positive_decimal(
            payload.get("qty"),
            field_name=(
                f"suborders[{index}]"
                ".payload.qty"
            ),
        )

        if item_qty != payload_qty:
            raise NegativeSaleOrderIntentError(
                "Suborder qty does not "
                "match payload qty"
            )

        normalized_total += item_qty

    expected_total = _positive_decimal(
        raw.get("normalized_qty"),
        field_name="normalized_qty",
    )

    if normalized_total != expected_total:
        raise NegativeSaleOrderIntentError(
            "Prepared suborder quantities "
            "do not sum to normalized_qty"
        )