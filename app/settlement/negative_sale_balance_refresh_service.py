from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.bybit.client import BybitV5Client
from app.bybit.transferable_balance import (
    BybitTransferableBalanceError,
    query_unified_transferable_balance,
)
from app.models import (
    FundNegativeSaleBatch,
    FundNegativeSaleLeg,
    FundSettlementBatch,
)
from app.settlement.negative_sale_balance_reconciliation import (
    has_balance_refresh_for_action,
)
from app.settlement.negative_sale_earn_runtime import (
    EARN_RUNTIME_STATUS_SUCCESS,
    NEGATIVE_SALE_EARN_INTENT_SCHEMA,
    NegativeSaleEarnRuntimeError,
    negative_sale_earn_runtime_summary,
    validate_negative_sale_earn_intent,
)
from app.settlement.negative_sale_execution_types import (
    ZERO,
    utcnow,
)
from app.settlement.negative_sale_live_batch_service import (
    NegativeSaleLiveBatchStepResult,
)
from app.settlement.negative_sale_order_intent import (
    NEGATIVE_SALE_ORDER_INTENT_SCHEMA,
    NegativeSaleOrderIntentError,
    validate_negative_sale_order_intent,
)


TERMINAL_ORDER_STATUSES = {
    "filled",
    "terminal_partial",
}


class NegativeSaleBalanceRefreshServiceError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class TerminalBalanceRefreshAction:
    action_type: str
    active_leg_id: int
    leg_index: int
    order_link_id: str
    external_status: str
    execution_round: int
    suborder_index: int | None
    source_schema: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _decimal(
    value: Any,
    *,
    field_name: str,
    non_negative: bool = True,
) -> Decimal:
    if isinstance(value, bool):
        raise (
            NegativeSaleBalanceRefreshServiceError(
                f"{field_name} must not be bool"
            )
        )

    if isinstance(value, float):
        raise (
            NegativeSaleBalanceRefreshServiceError(
                f"{field_name} must not be float"
            )
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
        raise (
            NegativeSaleBalanceRefreshServiceError(
                f"{field_name} is not Decimal"
            )
        ) from exc

    if not result.is_finite():
        raise (
            NegativeSaleBalanceRefreshServiceError(
                f"{field_name} must be finite"
            )
        )

    if non_negative and result < ZERO:
        raise (
            NegativeSaleBalanceRefreshServiceError(
                f"{field_name} must be "
                "non-negative"
            )
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
        raise (
            NegativeSaleBalanceRefreshServiceError(
                f"{field_name} must not be empty"
            )
        )

    return result


def _int(
    value: Any,
    *,
    field_name: str,
    positive: bool = False,
    non_negative: bool = False,
) -> int:
    if isinstance(value, bool):
        raise (
            NegativeSaleBalanceRefreshServiceError(
                f"{field_name} must not be bool"
            )
        )

    try:
        result = int(value)
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise (
            NegativeSaleBalanceRefreshServiceError(
                f"{field_name} must be int"
            )
        ) from exc

    if positive and result <= 0:
        raise (
            NegativeSaleBalanceRefreshServiceError(
                f"{field_name} must be positive"
            )
        )

    if non_negative and result < 0:
        raise (
            NegativeSaleBalanceRefreshServiceError(
                f"{field_name} must be "
                "non-negative"
            )
        )

    return result


def _leg_identity(
    leg: FundNegativeSaleLeg,
) -> tuple[int, int]:
    leg_id = _int(
        getattr(leg, "id", None),
        field_name="leg.id",
        positive=True,
    )
    leg_index = _int(
        getattr(
            leg,
            "leg_index",
            None,
        ),
        field_name="leg.leg_index",
        non_negative=True,
    )

    return leg_id, leg_index


def _earn_action(
    leg: FundNegativeSaleLeg,
    intent: dict[str, Any],
) -> TerminalBalanceRefreshAction | None:
    try:
        validate_negative_sale_earn_intent(
            intent
        )
        summary = (
            negative_sale_earn_runtime_summary(
                intent
            )
        )
    except NegativeSaleEarnRuntimeError as exc:
        raise (
            NegativeSaleBalanceRefreshServiceError(
                "Invalid durable Earn intent: "
                f"leg_id={leg.id}, error={exc}"
            )
        ) from exc

    runtime_status = str(
        summary["status"]
    )

    if (
        runtime_status
        != EARN_RUNTIME_STATUS_SUCCESS
    ):
        return None

    leg_id, leg_index = (
        _leg_identity(leg)
    )

    return TerminalBalanceRefreshAction(
        action_type=(
            "earn_terminal_confirmed"
        ),
        active_leg_id=leg_id,
        leg_index=leg_index,
        order_link_id=_required_text(
            intent.get("order_link_id"),
            field_name=(
                "earn.order_link_id"
            ),
        ),
        external_status=runtime_status,
        execution_round=_int(
            intent.get(
                "execution_round"
            ),
            field_name=(
                "earn.execution_round"
            ),
            non_negative=True,
        ),
        suborder_index=None,
        source_schema=(
            NEGATIVE_SALE_EARN_INTENT_SCHEMA
        ),
    )


def _order_actions(
    leg: FundNegativeSaleLeg,
    intent: dict[str, Any],
) -> list[TerminalBalanceRefreshAction]:
    try:
        validate_negative_sale_order_intent(
            intent
        )
    except NegativeSaleOrderIntentError as exc:
        raise (
            NegativeSaleBalanceRefreshServiceError(
                "Invalid durable order intent: "
                f"leg_id={leg.id}, error={exc}"
            )
        ) from exc

    raw_suborders = intent.get(
        "suborders"
    )

    if not isinstance(
        raw_suborders,
        list,
    ):
        raise (
            NegativeSaleBalanceRefreshServiceError(
                "Order intent suborders must "
                "be a list"
            )
        )

    leg_id, leg_index = (
        _leg_identity(leg)
    )
    execution_round = _int(
        intent.get("execution_round"),
        field_name=(
            "order.execution_round"
        ),
        non_negative=True,
    )

    result: list[
        TerminalBalanceRefreshAction
    ] = []

    for index, item in enumerate(
        raw_suborders
    ):
        if not isinstance(item, dict):
            raise (
                NegativeSaleBalanceRefreshServiceError(
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
            not in TERMINAL_ORDER_STATUSES
        ):
            continue

        suborder_index = _int(
            item.get(
                "suborder_index"
            ),
            field_name=(
                f"suborders[{index}]"
                ".suborder_index"
            ),
            non_negative=True,
        )

        result.append(
            TerminalBalanceRefreshAction(
                action_type=(
                    "order_terminal_confirmed"
                ),
                active_leg_id=leg_id,
                leg_index=leg_index,
                order_link_id=(
                    _required_text(
                        item.get(
                            "order_link_id"
                        ),
                        field_name=(
                            f"suborders[{index}]"
                            ".order_link_id"
                        ),
                    )
                ),
                external_status=status,
                execution_round=(
                    execution_round
                ),
                suborder_index=(
                    suborder_index
                ),
                source_schema=(
                    NEGATIVE_SALE_ORDER_INTENT_SCHEMA
                ),
            )
        )

    return result


def terminal_balance_refresh_actions(
    *,
    legs: list[FundNegativeSaleLeg],
) -> list[TerminalBalanceRefreshAction]:
    actions: list[
        TerminalBalanceRefreshAction
    ] = []

    for leg in legs:
        raw_intent = getattr(
            leg,
            "suborders_json",
            None,
        )

        if raw_intent is None:
            continue

        if not isinstance(
            raw_intent,
            dict,
        ):
            raise (
                NegativeSaleBalanceRefreshServiceError(
                    "Durable external intent "
                    "must be a dict: "
                    f"leg_id={leg.id}"
                )
            )

        schema = raw_intent.get(
            "schema"
        )

        if (
            schema
            == NEGATIVE_SALE_EARN_INTENT_SCHEMA
        ):
            action = _earn_action(
                leg,
                raw_intent,
            )

            if action is not None:
                actions.append(action)

            continue

        if (
            schema
            == NEGATIVE_SALE_ORDER_INTENT_SCHEMA
        ):
            actions.extend(
                _order_actions(
                    leg,
                    raw_intent,
                )
            )

            continue

        # Non-external or legacy JSON is left
        # to its owning state machine. This
        # barrier only handles the two current
        # immutable live intent schemas.

    actions.sort(
        key=lambda item: (
            0
            if item.action_type
            == "earn_terminal_confirmed"
            else 1,
            item.leg_index,
            item.active_leg_id,
            item.execution_round,
            (
                item.suborder_index
                if item.suborder_index
                is not None
                else -1
            ),
            item.order_link_id,
        )
    )

    return actions


def next_unreconciled_terminal_action(
    *,
    sale_batch: FundNegativeSaleBatch,
    legs: list[FundNegativeSaleLeg],
) -> (
    TerminalBalanceRefreshAction
    | None
):
    actions = (
        terminal_balance_refresh_actions(
            legs=legs
        )
    )

    for action in actions:
        already_refreshed = (
            has_balance_refresh_for_action(
                getattr(
                    sale_batch,
                    "reconciliation_json",
                    None,
                ),
                action_type=(
                    action.action_type
                ),
                active_leg_id=(
                    action.active_leg_id
                ),
                order_link_id=(
                    action.order_link_id
                ),
            )
        )

        if not already_refreshed:
            return action

    return None


def resume_negative_sale_balance_refresh_once(
    *,
    client: BybitV5Client,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    legs: list[FundNegativeSaleLeg],
    now: datetime | None = None,
) -> NegativeSaleLiveBatchStepResult | None:
    effective_now = now or utcnow()

    actions = (
        terminal_balance_refresh_actions(
            legs=legs
        )
    )

    action = next(
        (
            item
            for item in actions
            if not has_balance_refresh_for_action(
                getattr(
                    sale_batch,
                    "reconciliation_json",
                    None,
                ),
                action_type=(
                    item.action_type
                ),
                active_leg_id=(
                    item.active_leg_id
                ),
                order_link_id=(
                    item.order_link_id
                ),
            )
        ),
        None,
    )

    if action is None:
        return None

    try:
        balance = (
            query_unified_transferable_balance(
                client,
                coin="USDT",
                destination_account_type=(
                    "FUND"
                ),
            )
        )
    except BybitTransferableBalanceError as exc:
        raise (
            NegativeSaleBalanceRefreshServiceError(
                "Terminal action balance "
                f"refresh failed: {exc}"
            )
        ) from exc

    required_master_usdt = _decimal(
        sale_batch.required_master_usdt,
        field_name=(
            "sale_batch.required_master_usdt"
        ),
    )
    confirmed_available_usdt = (
        balance
        .confirmed_transferable_amount
    )
    shortage_usdt = max(
        required_master_usdt
        - confirmed_available_usdt,
        ZERO,
    )

    return NegativeSaleLiveBatchStepResult(
        sale_batch_id=int(
            sale_batch.id
        ),
        settlement_batch_id=int(
            settlement_batch.id
        ),
        action="balance_refresh",
        reason=(
            "terminal_external_action_"
            "balance_refreshed"
        ),
        candidate_leg_count=len(
            actions
        ),
        active_leg_id=(
            action.active_leg_id
        ),
        posted=False,
        all_order_legs_terminal=False,
        has_pending_action=False,
        requires_review=False,
        confirmed_available_usdt=(
            confirmed_available_usdt
        ),
        shortage_usdt=(
            shortage_usdt
        ),
        correction_decision=None,
        transferable_balance=(
            balance.to_dict()
        ),
        leg_step={
            "balance_refresh_action": (
                action.to_dict()
            ),
            "captured_at": (
                effective_now.isoformat()
            ),
            "read_only": True,
            "no_order_post": True,
            "no_earn_post": True,
            "no_transfer": True,
            "no_withdrawal": True,
            "no_bsc_action": True,
            "no_accounting_finalization": (
                True
            ),
        },
    )