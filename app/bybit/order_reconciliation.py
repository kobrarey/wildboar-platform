from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from app.bybit.client import BybitV5Client
from app.bybit.order_execution import (
    BybitExecutionFill,
    BybitOrderResult,
    query_execution_fills,
    query_order_history,
    query_order_realtime,
)


ZERO = Decimal("0")

PENDING_STATUSES = {
    "",
    "pending",
    "new",
    "created",
    "partiallyfilled",
    "untriggered",
    "triggered",
    "active",
}

SUCCESS_STATUSES = {
    "filled",
}

PARTIAL_TERMINAL_STATUSES = {
    "partiallyfilledcanceled",
    "partiallyfilledcancelled",
}

_CANCELLED_TERMINAL_STATUSES = {
    "cancelled",
    "canceled",
}

_FAILED_TERMINAL_STATUSES = {
    "rejected",
    "deactivated",
    "failed",
}


@dataclass(frozen=True)
class BybitOrderClassification:
    state: str
    provider_status: str | None

    terminal: bool
    success: bool
    partial: bool
    failed: bool
    pending_confirmation: bool

    execution_confirmed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BybitOrderReconciliation:
    category: str
    symbol: str

    requested_order_id: str | None
    requested_order_link_id: str | None

    order: BybitOrderResult | None
    fills: tuple[BybitExecutionFill, ...]

    aggregate_exec_qty: Decimal
    aggregate_exec_value: Decimal | None
    weighted_avg_price: Decimal | None
    fees_by_currency: dict[str, Decimal]

    classification: BybitOrderClassification

    sources_checked: tuple[str, ...]
    source_errors: tuple[
        dict[str, str],
        ...,
    ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "symbol": self.symbol,
            "requested_order_id": (
                self.requested_order_id
            ),
            "requested_order_link_id": (
                self.requested_order_link_id
            ),
            "order": (
                self.order.to_dict()
                if self.order is not None
                else None
            ),
            "fills": [
                fill.to_dict()
                for fill in self.fills
            ],
            "aggregate_exec_qty": str(
                self.aggregate_exec_qty
            ),
            "aggregate_exec_value": (
                str(self.aggregate_exec_value)
                if self.aggregate_exec_value
                is not None
                else None
            ),
            "weighted_avg_price": (
                str(self.weighted_avg_price)
                if self.weighted_avg_price
                is not None
                else None
            ),
            "fees_by_currency": {
                currency: str(amount)
                for currency, amount
                in self.fees_by_currency.items()
            },
            "classification": (
                self.classification.to_dict()
            ),
            "sources_checked": list(
                self.sources_checked
            ),
            "source_errors": [
                dict(row)
                for row in self.source_errors
            ],
        }


def _normalized_status(
    value: Any,
) -> str:
    return (
        str(value or "")
        .strip()
        .replace("_", "")
        .replace("-", "")
        .lower()
    )


def _deduplicate_fills(
    fills: list[BybitExecutionFill],
) -> tuple[BybitExecutionFill, ...]:
    unique: dict[
        str,
        BybitExecutionFill,
    ] = {}

    for fill in fills:
        exec_id = str(
            fill.exec_id or ""
        ).strip()

        if not exec_id:
            continue

        if exec_id not in unique:
            unique[exec_id] = fill

    rows = list(unique.values())

    rows.sort(
        key=lambda item: (
            item.exec_time or "",
            item.exec_id,
        )
    )

    return tuple(rows)


def _aggregate_fills(
    fills: tuple[BybitExecutionFill, ...],
) -> tuple[
    Decimal,
    Decimal | None,
    Decimal | None,
    dict[str, Decimal],
]:
    aggregate_qty = sum(
        (
            fill.exec_qty
            for fill in fills
        ),
        ZERO,
    )

    value_rows = [
        fill.exec_value
        for fill in fills
        if fill.exec_value is not None
    ]

    aggregate_value = (
        sum(value_rows, ZERO)
        if value_rows
        else None
    )

    weighted_qty = ZERO
    weighted_value = ZERO

    for fill in fills:
        if (
            fill.exec_price is None
            or fill.exec_qty <= ZERO
        ):
            continue

        weighted_qty += fill.exec_qty
        weighted_value += (
            fill.exec_qty
            * fill.exec_price
        )

    weighted_avg_price = (
        weighted_value / weighted_qty
        if weighted_qty > ZERO
        else None
    )

    fees_by_currency: dict[
        str,
        Decimal,
    ] = {}

    for fill in fills:
        if fill.exec_fee is None:
            continue

        currency = str(
            fill.fee_currency
            or "__unknown__"
        ).strip().upper()

        fees_by_currency[currency] = (
            fees_by_currency.get(
                currency,
                ZERO,
            )
            + fill.exec_fee
        )

    return (
        aggregate_qty,
        aggregate_value,
        weighted_avg_price,
        fees_by_currency,
    )


def classify_reconciled_order(
    *,
    order: BybitOrderResult | None,
    aggregate_exec_qty: Decimal,
    source_errors: tuple[
        dict[str, str],
        ...,
    ] = (),
) -> BybitOrderClassification:
    has_execution = (
        aggregate_exec_qty > ZERO
    )

    if order is None:
        reason = (
            "order_not_found"
            if not source_errors
            else (
                "order_state_unconfirmed:"
                "provider_endpoint_error"
            )
        )

        return BybitOrderClassification(
            state="pending_confirmation",
            provider_status=None,
            terminal=False,
            success=False,
            partial=False,
            failed=False,
            pending_confirmation=True,
            execution_confirmed=(
                has_execution
            ),
            reason=reason,
        )

    provider_status = (
        order.status
    )
    status = _normalized_status(
        provider_status
    )

    if status in SUCCESS_STATUSES:
        if not has_execution:
            return BybitOrderClassification(
                state="pending_confirmation",
                provider_status=(
                    provider_status
                ),
                terminal=False,
                success=False,
                partial=False,
                failed=False,
                pending_confirmation=True,
                execution_confirmed=False,
                reason=(
                    "filled_status_without_"
                    "execution_confirmation"
                ),
            )

        return BybitOrderClassification(
            state="terminal_success",
            provider_status=provider_status,
            terminal=True,
            success=True,
            partial=False,
            failed=False,
            pending_confirmation=False,
            execution_confirmed=True,
            reason=(
                "filled_with_confirmed_"
                "executions"
            ),
        )

    if status in PARTIAL_TERMINAL_STATUSES:
        if has_execution:
            return BybitOrderClassification(
                state="terminal_partial",
                provider_status=(
                    provider_status
                ),
                terminal=True,
                success=False,
                partial=True,
                failed=False,
                pending_confirmation=False,
                execution_confirmed=True,
                reason=(
                    "terminal_cancelled_"
                    "with_partial_executions"
                ),
            )

        return BybitOrderClassification(
            state="terminal_failed",
            provider_status=provider_status,
            terminal=True,
            success=False,
            partial=False,
            failed=True,
            pending_confirmation=False,
            execution_confirmed=False,
            reason=(
                "terminal_partial_status_"
                "without_executions"
            ),
        )

    if status in (
        _CANCELLED_TERMINAL_STATUSES
    ):
        if has_execution:
            return BybitOrderClassification(
                state="terminal_partial",
                provider_status=(
                    order.status
                ),
                terminal=True,
                success=False,
                partial=True,
                failed=False,
                pending_confirmation=False,
                execution_confirmed=True,
                reason=(
                    "cancelled_with_"
                    "confirmed_executions"
                ),
            )

        return BybitOrderClassification(
            state="terminal_failed",
            provider_status=order.status,
            terminal=True,
            success=False,
            partial=False,
            failed=True,
            pending_confirmation=False,
            execution_confirmed=False,
            reason=(
                "cancelled_without_"
                "confirmed_execution"
            ),
        )

    if status in (
        _FAILED_TERMINAL_STATUSES
    ):
        if has_execution:
            return BybitOrderClassification(
                state="pending_confirmation",
                provider_status=(
                    order.status
                ),
                terminal=False,
                success=False,
                partial=False,
                failed=False,
                pending_confirmation=True,
                execution_confirmed=True,
                reason=(
                    "provider_status_conflicts_"
                    "with_confirmed_executions"
                ),
            )

        return BybitOrderClassification(
            state="terminal_failed",
            provider_status=order.status,
            terminal=True,
            success=False,
            partial=False,
            failed=True,
            pending_confirmation=False,
            execution_confirmed=False,
            reason=(
                "terminal_failure_without_"
                "confirmed_execution"
            ),
        )

    if status in PENDING_STATUSES:
        return BybitOrderClassification(
            state="pending_confirmation",
            provider_status=provider_status,
            terminal=False,
            success=False,
            partial=False,
            failed=False,
            pending_confirmation=True,
            execution_confirmed=(
                has_execution
            ),
            reason=(
                "provider_status_nonterminal"
            ),
        )

    return BybitOrderClassification(
        state="pending_confirmation",
        provider_status=provider_status,
        terminal=False,
        success=False,
        partial=False,
        failed=False,
        pending_confirmation=True,
        execution_confirmed=(
            has_execution
        ),
        reason=(
            "unknown_provider_status_"
            "requires_confirmation"
        ),
    )


def reconcile_bybit_order(
    client: BybitV5Client,
    *,
    category: str,
    symbol: str,
    order_id: str | None = None,
    order_link_id: str | None = None,
) -> BybitOrderReconciliation:
    if not order_id and not order_link_id:
        raise ValueError(
            "order_id or order_link_id "
            "is required"
        )

    normalized_category = str(
        category
    ).strip().lower()
    normalized_symbol = str(
        symbol
    ).strip().upper()

    sources_checked: list[str] = []
    source_errors: list[
        dict[str, str]
    ] = []

    order: BybitOrderResult | None = None

    sources_checked.append("realtime")

    try:
        order = query_order_realtime(
            client,
            category=normalized_category,
            symbol=normalized_symbol,
            order_id=order_id,
            order_link_id=order_link_id,
        )
    except Exception as exc:
        source_errors.append(
            {
                "source": "realtime",
                "error": str(exc),
            }
        )

    if order is None:
        sources_checked.append("history")

        try:
            order = query_order_history(
                client,
                category=normalized_category,
                symbol=normalized_symbol,
                order_id=order_id,
                order_link_id=order_link_id,
            )
        except Exception as exc:
            source_errors.append(
                {
                    "source": "history",
                    "error": str(exc),
                }
            )

    effective_order_id = (
        order.order_id
        if (
            order is not None
            and order.order_id
        )
        else order_id
    )
    effective_order_link_id = (
        order.order_link_id
        if (
            order is not None
            and order.order_link_id
        )
        else order_link_id
    )

    sources_checked.append("executions")

    raw_fills: list[
        BybitExecutionFill
    ] = []

    try:
        raw_fills = query_execution_fills(
            client,
            category=normalized_category,
            symbol=normalized_symbol,
            order_id=effective_order_id,
            order_link_id=(
                effective_order_link_id
                if effective_order_id is None
                else None
            ),
        )
    except Exception as exc:
        source_errors.append(
            {
                "source": "executions",
                "error": str(exc),
            }
        )

    fills = _deduplicate_fills(
        raw_fills
    )

    (
        aggregate_exec_qty,
        aggregate_exec_value,
        weighted_avg_price,
        fees_by_currency,
    ) = _aggregate_fills(fills)

    frozen_errors = tuple(
        dict(row)
        for row in source_errors
    )

    classification = (
        classify_reconciled_order(
            order=order,
            aggregate_exec_qty=(
                aggregate_exec_qty
            ),
            source_errors=(
                frozen_errors
            ),
        )
    )

    return BybitOrderReconciliation(
        category=normalized_category,
        symbol=normalized_symbol,
        requested_order_id=order_id,
        requested_order_link_id=(
            order_link_id
        ),
        order=order,
        fills=fills,
        aggregate_exec_qty=(
            aggregate_exec_qty
        ),
        aggregate_exec_value=(
            aggregate_exec_value
        ),
        weighted_avg_price=(
            weighted_avg_price
        ),
        fees_by_currency=(
            fees_by_currency
        ),
        classification=classification,
        sources_checked=tuple(
            sources_checked
        ),
        source_errors=frozen_errors,
    )