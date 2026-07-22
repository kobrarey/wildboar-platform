from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any

from sqlalchemy.orm import Session

from app.bybit.client import BybitV5Client
from app.bybit.earn import (
    BybitEarnError,
    format_bybit_earn_amount,
    resolve_flexible_saving_product,
    total_flexible_saving_available_amount,
)
from app.bybit.transferable_balance import (
    BybitTransferableBalanceError,
    query_unified_transferable_balance,
)
from app.models import (
    FundNegativeSaleBatch,
    FundNegativeSaleLeg,
    FundSettlementBatch,
)
from app.operation_guard.hooks import (
    require_bybit_earn_redeem_guard,
)
from app.settlement.negative_sale_earn_persistence import (
    NegativeSaleEarnPersistenceError,
    persist_negative_sale_earn_state,
)
from app.settlement.negative_sale_earn_runtime import (
    EARN_RUNTIME_STATUS_ACKNOWLEDGED,
    EARN_RUNTIME_STATUS_FAILED,
    EARN_RUNTIME_STATUS_PENDING,
    EARN_RUNTIME_STATUS_PREPARED,
    EARN_RUNTIME_STATUS_SUBMITTED,
    EARN_RUNTIME_STATUS_SUCCESS,
    NEGATIVE_SALE_EARN_INTENT_SCHEMA,
    NegativeSaleEarnRuntimeError,
    build_negative_sale_earn_intent,
    confirm_negative_sale_earn_once,
    negative_sale_earn_runtime_summary,
    submit_negative_sale_earn_once,
    validate_negative_sale_earn_intent,
)
from app.settlement.negative_sale_execution_types import (
    ZERO,
    utcnow,
)
from app.settlement.negative_sale_live_batch_service import (
    NegativeSaleLiveBatchStepResult,
)
from app.settlement.statuses import (
    SALE_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    SALE_LEG_STATUS_PENDING_CONFIRMATION,
    SALE_LEG_STATUS_USDT_EARN_REDEEMED,
)


class NegativeSaleEarnLiveServiceError(
    RuntimeError
):
    pass


EARN_LEG_TYPE = "usdt_earn_buffer"


def _decimal(
    value: Any,
    *,
    field_name: str,
    positive: bool = False,
    non_negative: bool = False,
) -> Decimal:
    if isinstance(value, bool):
        raise NegativeSaleEarnLiveServiceError(
            f"{field_name} must not be bool"
        )

    if isinstance(value, float):
        raise NegativeSaleEarnLiveServiceError(
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
        raise NegativeSaleEarnLiveServiceError(
            f"{field_name} is not Decimal"
        ) from exc

    if not result.is_finite():
        raise NegativeSaleEarnLiveServiceError(
            f"{field_name} must be finite"
        )

    if positive and result <= ZERO:
        raise NegativeSaleEarnLiveServiceError(
            f"{field_name} must be positive"
        )

    if non_negative and result < ZERO:
        raise NegativeSaleEarnLiveServiceError(
            f"{field_name} must be "
            "non-negative"
        )

    return result


def _earn_legs(
    legs: list[FundNegativeSaleLeg],
) -> list[FundNegativeSaleLeg]:
    return [
        leg
        for leg in legs
        if str(
            getattr(
                leg,
                "leg_type",
                "",
            )
            or ""
        ).strip().lower()
        == EARN_LEG_TYPE
    ]


def _single_earn_leg(
    legs: list[FundNegativeSaleLeg],
) -> FundNegativeSaleLeg | None:
    candidates = _earn_legs(legs)

    if not candidates:
        return None

    if len(candidates) != 1:
        raise NegativeSaleEarnLiveServiceError(
            "Negative sale batch must have "
            "at most one USDT Earn leg: "
            f"count={len(candidates)}"
        )

    return candidates[0]


def _earn_order_link_id(
    *,
    sale_batch_id: int,
    leg_id: int,
    leg_index: int,
    execution_round: int,
) -> str:
    seed = (
        f"negative-sale-earn:"
        f"{int(sale_batch_id)}:"
        f"{int(leg_id)}:"
        f"{int(leg_index)}:"
        f"{int(execution_round)}"
    )

    digest = sha256(
        seed.encode("utf-8")
    ).hexdigest()

    # 31 characters, below Bybit's
    # 36-character orderLinkId limit.
    return f"wbne-{digest[:26]}"


def _step(
    *,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    leg: FundNegativeSaleLeg,
    action: str,
    reason: str,
    posted: bool,
    has_pending_action: bool,
    requires_review: bool,
    leg_step: dict[str, Any] | None,
) -> NegativeSaleLiveBatchStepResult:
    return NegativeSaleLiveBatchStepResult(
        sale_batch_id=int(
            sale_batch.id
        ),
        settlement_batch_id=int(
            settlement_batch.id
        ),
        action=action,
        reason=reason,
        candidate_leg_count=1,
        active_leg_id=int(leg.id),
        posted=posted,
        all_order_legs_terminal=False,
        has_pending_action=(
            has_pending_action
        ),
        requires_review=(
            requires_review
        ),
        confirmed_available_usdt=None,
        shortage_usdt=None,
        correction_decision=None,
        transferable_balance=None,
        leg_step=(
            deepcopy(leg_step)
            if leg_step is not None
            else None
        ),
    )


def _validated_existing_intent(
    leg: FundNegativeSaleLeg,
) -> dict[str, Any] | None:
    raw = getattr(
        leg,
        "suborders_json",
        None,
    )

    if raw is None:
        return None

    if not isinstance(raw, dict):
        raise NegativeSaleEarnLiveServiceError(
            "Earn leg suborders_json must "
            f"be a dict: leg_id={leg.id}"
        )

    intent = deepcopy(raw)

    if (
        intent.get("schema")
        != NEGATIVE_SALE_EARN_INTENT_SCHEMA
    ):
        raise NegativeSaleEarnLiveServiceError(
            "Earn leg contains non-Earn "
            "durable intent: "
            f"leg_id={leg.id}, "
            f"schema={intent.get('schema')!r}"
        )

    try:
        validate_negative_sale_earn_intent(
            intent
        )
    except NegativeSaleEarnRuntimeError as exc:
        raise NegativeSaleEarnLiveServiceError(
            "Earn leg durable intent is "
            f"invalid: leg_id={leg.id}, "
            f"error={exc}"
        ) from exc

    if (
        int(intent["leg_id"])
        != int(leg.id)
    ):
        raise NegativeSaleEarnLiveServiceError(
            "Earn durable intent leg_id "
            "mismatch"
        )

    if (
        int(intent["sale_batch_id"])
        != int(leg.sale_batch_id)
    ):
        raise NegativeSaleEarnLiveServiceError(
            "Earn durable intent "
            "sale_batch_id mismatch"
        )

    return intent


def _persist_callback(
    db: Session,
    *,
    leg: FundNegativeSaleLeg,
    now: datetime,
):
    def persist(
        state: dict[str, Any],
    ) -> None:
        try:
            persist_negative_sale_earn_state(
                db,
                leg=leg,
                raw_intent=state,
                now=now,
            )
        except (
            NegativeSaleEarnPersistenceError
        ) as exc:
            raise (
                NegativeSaleEarnLiveServiceError(
                    "Earn durable persistence "
                    f"failed: leg_id={leg.id}, "
                    f"error={exc}"
                )
            ) from exc

    return persist


def _prepare_earn_intent(
    db: Session,
    *,
    client: BybitV5Client,
    sale_batch: FundNegativeSaleBatch,
    leg: FundNegativeSaleLeg,
    now: datetime,
) -> tuple[
    dict[str, Any] | None,
    dict[str, Any],
]:
    required_master_usdt = _decimal(
        sale_batch.required_master_usdt,
        field_name=(
            "sale_batch.required_master_usdt"
        ),
        positive=True,
    )
    target_cash_usdt = _decimal(
        leg.target_cash_usdt,
        field_name=(
            "earn_leg.target_cash_usdt"
        ),
        non_negative=True,
    )

    if target_cash_usdt <= ZERO:
        return None, {
            "reason": (
                "earn_target_is_zero"
            ),
        }

    try:
        transferable = (
            query_unified_transferable_balance(
                client,
                coin="USDT",
                destination_account_type=(
                    "FUND"
                ),
            )
        )
    except (
        BybitTransferableBalanceError
    ) as exc:
        raise NegativeSaleEarnLiveServiceError(
            "Confirmed transferable USDT "
            f"query failed: {exc}"
        ) from exc

    confirmed_available_usdt = (
        transferable
        .confirmed_transferable_amount
    )

    shortage_usdt = max(
        required_master_usdt
        - confirmed_available_usdt,
        ZERO,
    )

    if shortage_usdt <= ZERO:
        return None, {
            "reason": (
                "confirmed_transferable_"
                "usdt_already_sufficient"
            ),
            "required_master_usdt": str(
                required_master_usdt
            ),
            "confirmed_available_usdt": str(
                confirmed_available_usdt
            ),
            "shortage_usdt": "0",
            "transferable_balance": (
                transferable.to_dict()
            ),
        }

    try:
        product = (
            resolve_flexible_saving_product(
                client,
                coin="USDT",
            )
        )

        if product.precision is None:
            raise BybitEarnError(
                "Earn product precision "
                "is missing"
            )

        available_earn_usdt = (
            total_flexible_saving_available_amount(
                client,
                coin="USDT",
                product_id=(
                    product.product_id
                ),
            )
        )
    except BybitEarnError as exc:
        raise NegativeSaleEarnLiveServiceError(
            "USDT Earn live preflight "
            f"failed: {exc}"
        ) from exc

    available_earn_usdt = max(
        available_earn_usdt,
        ZERO,
    )

    needed_from_earn_usdt = min(
        shortage_usdt,
        target_cash_usdt,
        available_earn_usdt,
    )

    if needed_from_earn_usdt <= ZERO:
        return None, {
            "reason": (
                "no_redeemable_usdt_earn"
            ),
            "required_master_usdt": str(
                required_master_usdt
            ),
            "confirmed_available_usdt": str(
                confirmed_available_usdt
            ),
            "shortage_usdt": str(
                shortage_usdt
            ),
            "target_cash_usdt": str(
                target_cash_usdt
            ),
            "available_earn_usdt": str(
                available_earn_usdt
            ),
            "transferable_balance": (
                transferable.to_dict()
            ),
        }

    try:
        amount_str = (
            format_bybit_earn_amount(
                needed_from_earn_usdt,
                precision=int(
                    product.precision
                ),
                rounding="up",
            )
        )
    except BybitEarnError as exc:
        raise NegativeSaleEarnLiveServiceError(
            "USDT Earn amount "
            f"normalization failed: {exc}"
        ) from exc

    amount = _decimal(
        amount_str,
        field_name="earn.amount",
        positive=True,
    )

    if amount > available_earn_usdt:
        raise NegativeSaleEarnLiveServiceError(
            "Normalized Earn amount exceeds "
            "live available Earn amount: "
            f"amount={amount}, "
            f"available={available_earn_usdt}"
        )

    order_link_id = _earn_order_link_id(
        sale_batch_id=int(
            sale_batch.id
        ),
        leg_id=int(leg.id),
        leg_index=int(
            leg.leg_index
        ),
        execution_round=0,
    )

    intent = (
        build_negative_sale_earn_intent(
            sale_batch_id=int(
                sale_batch.id
            ),
            leg_id=int(leg.id),
            leg_index=int(
                leg.leg_index
            ),
            execution_round=0,
            product_id=str(
                product.product_id
            ),
            product_precision=int(
                product.precision
            ),
            target_cash_usdt=(
                target_cash_usdt
            ),
            confirmed_available_usdt=(
                confirmed_available_usdt
            ),
            available_earn_usdt=(
                available_earn_usdt
            ),
            needed_from_earn_usdt=(
                needed_from_earn_usdt
            ),
            amount=amount,
            amount_str=amount_str,
            order_link_id=(
                order_link_id
            ),
            prepared_at=now,
        )
    )

    persist = _persist_callback(
        db,
        leg=leg,
        now=now,
    )
    persist(intent)

    return intent, {
        "reason": "earn_intent_prepared",
        "required_master_usdt": str(
            required_master_usdt
        ),
        "confirmed_available_usdt": str(
            confirmed_available_usdt
        ),
        "shortage_usdt": str(
            shortage_usdt
        ),
        "target_cash_usdt": str(
            target_cash_usdt
        ),
        "available_earn_usdt": str(
            available_earn_usdt
        ),
        "needed_from_earn_usdt": str(
            needed_from_earn_usdt
        ),
        "amount": str(amount),
        "product_id": str(
            product.product_id
        ),
        "transferable_balance": (
            transferable.to_dict()
        ),
    }


def _before_submit_callback(
    db: Session,
    *,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    leg: FundNegativeSaleLeg,
    intent: dict[str, Any],
):
    expected_payload = intent.get(
        "payload"
    )

    if not isinstance(
        expected_payload,
        dict,
    ):
        raise NegativeSaleEarnLiveServiceError(
            "Earn immutable payload "
            "is missing"
        )

    amount = _decimal(
        intent["amount"],
        field_name="earn.amount",
        positive=True,
    )

    def before_submit(
        payload: dict[str, Any],
    ) -> None:
        if payload != expected_payload:
            raise (
                NegativeSaleEarnLiveServiceError(
                    "Earn Operation Guard "
                    "payload differs from "
                    "immutable intent"
                )
            )

        require_bybit_earn_redeem_guard(
            db,
            fund_id=int(
                sale_batch.fund_id
            ),
            settlement_batch_id=int(
                settlement_batch.id
            ),
            amount_usdt=amount,
            request_id=str(
                intent["order_link_id"]
            ),
            metadata={
                "sale_batch_id": int(
                    sale_batch.id
                ),
                "sale_leg_id": int(
                    leg.id
                ),
                "leg_index": int(
                    leg.leg_index
                ),
                "operation": (
                    "negative_sale_"
                    "usdt_earn_redeem"
                ),
                "product_id": str(
                    intent["product_id"]
                ),
                "account_type": "FUND",
                "coin": "USDT",
                "amount": str(amount),
                "exact_payload": (
                    deepcopy(payload)
                ),
                "persisted_submitted_"
                "before_post": True,
                "no_transfer": True,
                "no_withdrawal": True,
                "no_bsc_action": True,
            },
        )

    return before_submit


def resume_negative_sale_earn_once(
    db: Session,
    *,
    client: BybitV5Client,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    legs: list[FundNegativeSaleLeg],
    now: datetime | None = None,
) -> NegativeSaleLiveBatchStepResult | None:
    effective_now = now or utcnow()
    leg = _single_earn_leg(legs)

    if leg is None:
        return None

    if (
        str(
            getattr(
                leg,
                "status",
                "",
            )
            or ""
        )
        == SALE_LEG_STATUS_FAILED_REQUIRES_REVIEW
    ):
        return _step(
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            leg=leg,
            action="review_required",
            reason=(
                "earn_leg_already_failed_"
                "requires_review"
            ),
            posted=False,
            has_pending_action=False,
            requires_review=True,
            leg_step={
                "status": leg.status,
                "execution_error": (
                    getattr(
                        leg,
                        "execution_error",
                        None,
                    )
                ),
            },
        )

    intent = _validated_existing_intent(
        leg
    )

    if intent is None:
        if (
            str(
                getattr(
                    leg,
                    "status",
                    "",
                )
                or ""
            )
            == SALE_LEG_STATUS_PENDING_CONFIRMATION
        ):
            raise NegativeSaleEarnLiveServiceError(
                "Earn leg is pending "
                "confirmation without durable "
                f"intent: leg_id={leg.id}"
            )

        if (
            str(
                getattr(
                    leg,
                    "status",
                    "",
                )
                or ""
            )
            == SALE_LEG_STATUS_USDT_EARN_REDEEMED
        ):
            # Legacy already-confirmed Earn leg.
            return None

        intent, diagnostics = (
            _prepare_earn_intent(
                db,
                client=client,
                sale_batch=sale_batch,
                leg=leg,
                now=effective_now,
            )
        )

        if intent is None:
            # Earn is not required or is not
            # available. The order state
            # machine may continue and rely on
            # confirmed transferable USDT.
            return None

        summary = (
            negative_sale_earn_runtime_summary(
                intent
            )
        )

        return _step(
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            leg=leg,
            action="earn_prepare",
            reason="earn_intent_prepared",
            posted=False,
            has_pending_action=False,
            requires_review=False,
            leg_step={
                "summary": summary,
                "preflight": diagnostics,
            },
        )

    summary = (
        negative_sale_earn_runtime_summary(
            intent
        )
    )
    runtime_status = str(
        summary["status"]
    )

    if (
        runtime_status
        == EARN_RUNTIME_STATUS_SUCCESS
    ):
        return None

    if (
        runtime_status
        == EARN_RUNTIME_STATUS_FAILED
    ):
        return _step(
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            leg=leg,
            action="review_required",
            reason=(
                "earn_runtime_failed:"
                f"{summary.get('failure_reason')}"
            ),
            posted=False,
            has_pending_action=False,
            requires_review=True,
            leg_step={
                "summary": summary,
            },
        )

    persist = _persist_callback(
        db,
        leg=leg,
        now=effective_now,
    )

    if (
        runtime_status
        == EARN_RUNTIME_STATUS_PREPARED
    ):
        before_submit = (
            _before_submit_callback(
                db,
                sale_batch=sale_batch,
                settlement_batch=(
                    settlement_batch
                ),
                leg=leg,
                intent=intent,
            )
        )

        try:
            updated, posted = (
                submit_negative_sale_earn_once(
                    client,
                    raw_intent=intent,
                    before_submit=(
                        before_submit
                    ),
                    persist_state=persist,
                    now=effective_now,
                )
            )
        except NegativeSaleEarnRuntimeError as exc:
            raise (
                NegativeSaleEarnLiveServiceError(
                    "Earn submit cycle failed: "
                    f"leg_id={leg.id}, "
                    f"error={exc}"
                )
            ) from exc

        updated_summary = (
            negative_sale_earn_runtime_summary(
                updated
            )
        )
        updated_status = str(
            updated_summary["status"]
        )

        requires_review = (
            updated_status
            == EARN_RUNTIME_STATUS_FAILED
        )
        has_pending_action = (
            updated_status
            in {
                EARN_RUNTIME_STATUS_SUBMITTED,
                EARN_RUNTIME_STATUS_ACKNOWLEDGED,
                EARN_RUNTIME_STATUS_PENDING,
            }
        )

        return _step(
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            leg=leg,
            action=(
                "review_required"
                if requires_review
                else "earn_submit"
            ),
            reason=(
                "earn_submit_failed"
                if requires_review
                else (
                    "earn_post_acknowledged"
                    if posted
                    else
                    "earn_existing_history_"
                    "reconciled"
                )
            ),
            posted=posted,
            has_pending_action=(
                has_pending_action
            ),
            requires_review=(
                requires_review
            ),
            leg_step={
                "summary": (
                    updated_summary
                ),
            },
        )

    if runtime_status not in {
        EARN_RUNTIME_STATUS_SUBMITTED,
        EARN_RUNTIME_STATUS_ACKNOWLEDGED,
        EARN_RUNTIME_STATUS_PENDING,
    }:
        raise NegativeSaleEarnLiveServiceError(
            "Unsupported resumable Earn "
            f"status: {runtime_status}"
        )

    try:
        updated = (
            confirm_negative_sale_earn_once(
                client,
                raw_intent=intent,
                persist_state=persist,
                now=effective_now,
            )
        )
    except NegativeSaleEarnRuntimeError as exc:
        raise NegativeSaleEarnLiveServiceError(
            "Earn confirmation cycle "
            f"failed: leg_id={leg.id}, "
            f"error={exc}"
        ) from exc

    updated_summary = (
        negative_sale_earn_runtime_summary(
            updated
        )
    )
    updated_status = str(
        updated_summary["status"]
    )

    requires_review = (
        updated_status
        == EARN_RUNTIME_STATUS_FAILED
    )
    has_pending_action = (
        updated_status
        in {
            EARN_RUNTIME_STATUS_SUBMITTED,
            EARN_RUNTIME_STATUS_ACKNOWLEDGED,
            EARN_RUNTIME_STATUS_PENDING,
        }
    )

    return _step(
        sale_batch=sale_batch,
        settlement_batch=(
            settlement_batch
        ),
        leg=leg,
        action=(
            "review_required"
            if requires_review
            else "earn_confirm"
        ),
        reason=(
            "earn_confirmation_failed"
            if requires_review
            else (
                "earn_redeem_confirmed"
                if updated_status
                == EARN_RUNTIME_STATUS_SUCCESS
                else
                "earn_redeem_pending"
            )
        ),
        posted=False,
        has_pending_action=(
            has_pending_action
        ),
        requires_review=(
            requires_review
        ),
        leg_step={
            "summary": updated_summary,
        },
    )