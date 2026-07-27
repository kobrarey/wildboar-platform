from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    Fund,
    FundBscTransactionIntent,
    FundNegativeBybitFlow,
    FundNegativeFinalizationBatch,
    FundNegativePayoutBatch,
    FundNegativePayoutLeg,
    FundNegativeSaleBatch,
    FundOrder,
    FundRuntimeState,
    FundSettlementBatch,
    UserFundPosition,
    UserWallet,
)
from app.settlement.negative_finalization_types import (
    NegativeFinalizationError,
    NegativeFinalizationResult,
    _json_dict,
    utcnow,
)
from app.settlement.negative_bybit_flow import (
    _is_bybit_success,
    _is_withdrawal_success_like,
)
from app.settlement.negative_sale_snapshot import dec
from app.settlement.position_cost_basis import (
    PositionCostBasisError,
    apply_buy_cost_basis,
    apply_redeem_cost_basis,
    validate_position_cost_basis,
)
from app.settlement.share_quantity import (
    ShareQuantityError,
    calculate_successful_buy_share_quantity,
    require_share_quantity_4dp_aligned,
)
from app.settlement.statuses import (
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_NEGATIVE_CASH_SETTLEMENT_COMPLETED,
    BATCH_STATUS_NEGATIVE_NET_ACCOUNTING_FINALIZED,
    BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED,
    BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT,
    BSC_INTENT_ACTION_NEGATIVE_SETTLEMENT_GAS_TOPUP,
    BSC_INTENT_STATUS_CONFIRMED,
    BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW,
    BSC_INTENT_UNRESOLVED_STATUSES,
    BYBIT_FLOW_STATUS_COMPLETED,
    FINALIZATION_BATCH_STATUS_ACCOUNTING_FINALIZED,
    FINALIZATION_BATCH_STATUS_ACCOUNTING_PROCESSING,
    FINALIZATION_BATCH_STATUS_COMPLETED,
    FINALIZATION_BATCH_STATUS_CREATED,
    FINALIZATION_BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    FINALIZATION_BATCH_STATUS_PRICING_UNLOCKED,
    FINALIZATION_BATCH_STATUS_VALIDATING,
    ORDER_SIDE_BUY,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_FAILED,
    ORDER_STATUS_FAILED_REQUIRES_REVIEW,
    ORDER_STATUS_SUCCESS,
    PAYOUT_BATCH_STATUS_COMPLETED,
    PAYOUT_BALANCE_REFRESH_STATUS_CONFIRMED,
    PAYOUT_GAS_STATUS_READY,
    PAYOUT_LEG_STATUS_BALANCE_REFRESHED,
    PRICING_LOCK_REASON_SETTLEMENT,
    SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED,
    SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED_WITH_EXTRA_SALE,
)


ZERO = Decimal("0")
Q10 = Decimal("0.0000000001")

BYBIT_CASH_DELIVERY_POLICY_VERSION = (
    "negative_cash_delivery_v1"
)

BYBIT_UNIVERSAL_INTENT_SCHEMA = (
    "negative_universal_transfer_intent_v2"
)

BYBIT_UNIVERSAL_RECONCILIATION_SCHEMA = (
    "negative_universal_transfer_reconciliation_v2"
)

BYBIT_MASTER_BALANCE_SCHEMA = (
    "negative_master_transferable_balance_barrier_v1"
)

BYBIT_WITHDRAWAL_INTENT_SCHEMA = (
    "negative_withdrawal_intent_v2"
)

BYBIT_WITHDRAWAL_RECONCILIATION_SCHEMA = (
    "negative_withdrawal_reconciliation_v1"
)

BYBIT_SETTLEMENT_RECEIPT_SCHEMA = (
    "negative_settlement_wallet_receipt_v1"
)

BYBIT_CASH_DELIVERY_COMPLETION_SCHEMA = (
    "negative_cash_delivery_completion_v1"
)

BYBIT_CASH_DELIVERY_REPORT_SCHEMA = (
    "negative_cash_delivery_report_v1"
)

USER_WALLET_DB_GATE_SCHEMA = (
    "negative_finalization_user_wallet_db_gate_v1"
)


class NegativeShareQuantityError(
    NegativeFinalizationError
):
    pass


def _q10(value: Any) -> Decimal:
    return dec(value).quantize(Q10)


def _share_4dp(
    value: Any,
    *,
    field_name: str,
    allow_negative: bool = False,
) -> Decimal:
    try:
        return require_share_quantity_4dp_aligned(
            value,
            field_name=field_name,
            allow_negative=allow_negative,
        )
    except ShareQuantityError as exc:
        raise NegativeShareQuantityError(
            str(exc)
        ) from exc


def _same_decimal(left: Any, right: Any) -> bool:
    return _q10(left) == _q10(right)


def _positive(value: Any) -> bool:
    return dec(value) > ZERO


def _now_or_supplied(now):
    return now or utcnow()


def _order_ids_from_leg(leg: FundNegativePayoutLeg) -> list[int]:
    raw = leg.order_ids_json

    if raw is None:
        return []

    if isinstance(raw, dict):
        values = raw.get("order_ids") or []
    elif isinstance(raw, list):
        values = raw
    else:
        return []

    return [int(value) for value in values]


def _position_key(user_id: int, fund_id: int) -> str:
    return f"{int(user_id)}:{int(fund_id)}"


def _wallet_key(user_wallet_id: int) -> str:
    return str(int(user_wallet_id))


def _result_from_completed(
    *,
    finalization: FundNegativeFinalizationBatch,
    settlement_batch: FundSettlementBatch,
    fund: Fund,
    status_before: str | None,
    settlement_status_before: str | None,
    idempotent: bool,
) -> NegativeFinalizationResult:
    return NegativeFinalizationResult(
        ok=True,
        finalization_batch_id=int(finalization.id),
        settlement_batch_id=int(settlement_batch.id),
        payout_batch_id=int(finalization.payout_batch_id),
        fund_id=int(fund.id),
        fund_code=str(fund.code),
        status_before=status_before,
        status_after=finalization.status,
        settlement_status_before=settlement_status_before,
        settlement_status_after=settlement_batch.status,
        buy_order_count=finalization.buy_order_count,
        redeem_order_count=finalization.redeem_order_count,
        success_order_count=finalization.success_order_count,
        shares_outstanding_before=str(finalization.shares_outstanding_before),
        shares_outstanding_after=(
            str(finalization.shares_outstanding_after)
            if finalization.shares_outstanding_after is not None
            else None
        ),
        total_buy_usdt=(
            str(finalization.total_buy_usdt)
            if finalization.total_buy_usdt is not None
            else None
        ),
        total_buy_shares=(
            str(finalization.total_buy_shares)
            if finalization.total_buy_shares is not None
            else None
        ),
        total_redeem_shares=(
            str(finalization.total_redeem_shares)
            if finalization.total_redeem_shares is not None
            else None
        ),
        planned_net_shares_change=(
            str(finalization.planned_net_shares_change)
            if finalization.planned_net_shares_change is not None
            else None
        ),
        actual_net_shares_change=(
            str(finalization.actual_net_shares_change)
            if finalization.actual_net_shares_change is not None
            else None
        ),
        total_net_user_payout_usdt=(
            str(finalization.total_net_user_payout_usdt)
            if finalization.total_net_user_payout_usdt is not None
            else None
        ),
        total_partial_month_fee_usdt=(
            str(finalization.total_partial_month_fee_usdt)
            if finalization.total_partial_month_fee_usdt is not None
            else None
        ),
        accounting_finalized_at=(
            finalization.accounting_finalized_at.isoformat()
            if finalization.accounting_finalized_at is not None
            else None
        ),
        pricing_unlocked_at=(
            finalization.pricing_unlocked_at.isoformat()
            if finalization.pricing_unlocked_at is not None
            else None
        ),
        idempotent=idempotent,
        diagnostics={"idempotent": idempotent},
    )


def _set_failed(
    *,
    finalization: FundNegativeFinalizationBatch,
    settlement_batch: FundSettlementBatch,
    fund: Fund | None,
    status_before: str | None,
    settlement_status_before: str | None,
    error: str,
    now,
    diagnostics: dict[str, Any] | None = None,
) -> NegativeFinalizationResult:
    finalization.status = FINALIZATION_BATCH_STATUS_FAILED_REQUIRES_REVIEW
    finalization.error = error
    finalization.updated_at = now
    finalization.reconciliation_json = _json_dict(
        {
            "ok": False,
            "error": error,
            "diagnostics": diagnostics or {},
            "no_real_bybit_calls": True,
            "no_real_bsc_calls": True,
            "no_payout_transfers": True,
            "no_nav_chart_writes": True,
            "no_server_deploy": True,
        }
    )
    finalization.report_json = _json_dict(
        {
            "stage": "23.6",
            "ok": False,
            "error": error,
            "final_state": FINALIZATION_BATCH_STATUS_FAILED_REQUIRES_REVIEW,
        }
    )

    settlement_batch.status = BATCH_STATUS_FAILED_REQUIRES_REVIEW
    settlement_batch.error = error
    settlement_batch.updated_at = now

    return NegativeFinalizationResult(
        ok=False,
        finalization_batch_id=(
            int(finalization.id) if finalization.id is not None else None
        ),
        settlement_batch_id=int(settlement_batch.id),
        payout_batch_id=(
            int(finalization.payout_batch_id)
            if finalization.payout_batch_id is not None
            else None
        ),
        fund_id=int(finalization.fund_id) if finalization.fund_id is not None else None,
        fund_code=str(fund.code) if fund is not None else None,
        status_before=status_before,
        status_after=finalization.status,
        settlement_status_before=settlement_status_before,
        settlement_status_after=settlement_batch.status,
        buy_order_count=finalization.buy_order_count,
        redeem_order_count=finalization.redeem_order_count,
        success_order_count=finalization.success_order_count,
        shares_outstanding_before=(
            str(finalization.shares_outstanding_before)
            if finalization.shares_outstanding_before is not None
            else None
        ),
        shares_outstanding_after=(
            str(finalization.shares_outstanding_after)
            if finalization.shares_outstanding_after is not None
            else None
        ),
        total_buy_usdt=(
            str(finalization.total_buy_usdt)
            if finalization.total_buy_usdt is not None
            else None
        ),
        total_buy_shares=(
            str(finalization.total_buy_shares)
            if finalization.total_buy_shares is not None
            else None
        ),
        total_redeem_shares=(
            str(finalization.total_redeem_shares)
            if finalization.total_redeem_shares is not None
            else None
        ),
        planned_net_shares_change=(
            str(finalization.planned_net_shares_change)
            if finalization.planned_net_shares_change is not None
            else None
        ),
        actual_net_shares_change=(
            str(finalization.actual_net_shares_change)
            if finalization.actual_net_shares_change is not None
            else None
        ),
        total_net_user_payout_usdt=(
            str(finalization.total_net_user_payout_usdt)
            if finalization.total_net_user_payout_usdt is not None
            else None
        ),
        total_partial_month_fee_usdt=(
            str(finalization.total_partial_month_fee_usdt)
            if finalization.total_partial_month_fee_usdt is not None
            else None
        ),
        accounting_finalized_at=(
            finalization.accounting_finalized_at.isoformat()
            if finalization.accounting_finalized_at is not None
            else None
        ),
        pricing_unlocked_at=(
            finalization.pricing_unlocked_at.isoformat()
            if finalization.pricing_unlocked_at is not None
            else None
        ),
        error=error,
        diagnostics=diagnostics or {},
    )


def _mark_share_failed_orders(
    db: Session,
    *,
    settlement_batch_id: int,
    error: str,
) -> None:
    orders = (
        db.query(FundOrder)
        .filter(
            FundOrder.settlement_batch_id
            == int(settlement_batch_id)
        )
        .filter(
            FundOrder.side.in_(
                [ORDER_SIDE_BUY, ORDER_SIDE_REDEEM]
            )
        )
        .with_for_update()
        .all()
    )

    for order in orders:
        if order.status in {
            ORDER_STATUS_SUCCESS,
            ORDER_STATUS_CANCELLED,
        }:
            continue

        order.status = (
            ORDER_STATUS_FAILED_REQUIRES_REVIEW
        )
        order.error = error
        db.add(order)

    db.flush()


def _lock_settlement_batch(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundSettlementBatch:
    settlement_batch = (
        db.query(FundSettlementBatch)
        .filter(FundSettlementBatch.id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )
    if settlement_batch is None:
        raise NegativeFinalizationError(
            f"Settlement batch not found: {settlement_batch_id}"
        )

    return settlement_batch


def _lock_fund(db: Session, *, fund_id: int) -> Fund:
    fund = (
        db.query(Fund)
        .filter(Fund.id == int(fund_id))
        .with_for_update()
        .first()
    )
    if fund is None:
        raise NegativeFinalizationError(f"Fund not found: {fund_id}")

    return fund


def _lock_runtime_state(db: Session, *, fund_id: int) -> FundRuntimeState | None:
    return (
        db.query(FundRuntimeState)
        .filter(FundRuntimeState.fund_id == int(fund_id))
        .with_for_update()
        .first()
    )


def _lock_sale_batch(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundNegativeSaleBatch:
    sale_batch = (
        db.query(FundNegativeSaleBatch)
        .filter(FundNegativeSaleBatch.settlement_batch_id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )
    if sale_batch is None:
        raise NegativeFinalizationError("Negative sale batch not found")

    return sale_batch


def _lock_bybit_flow(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundNegativeBybitFlow:
    bybit_flow = (
        db.query(FundNegativeBybitFlow)
        .filter(FundNegativeBybitFlow.settlement_batch_id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )
    if bybit_flow is None:
        raise NegativeFinalizationError("Negative Bybit flow not found")

    return bybit_flow


def _lock_payout_batch(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundNegativePayoutBatch:
    payout_batch = (
        db.query(FundNegativePayoutBatch)
        .filter(FundNegativePayoutBatch.settlement_batch_id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )
    if payout_batch is None:
        raise NegativeFinalizationError("Negative payout batch not found")

    return payout_batch


def _lock_payout_legs(
    db: Session,
    *,
    payout_batch_id: int,
) -> list[FundNegativePayoutLeg]:
    legs = (
        db.query(FundNegativePayoutLeg)
        .filter(FundNegativePayoutLeg.payout_batch_id == int(payout_batch_id))
        .order_by(FundNegativePayoutLeg.id.asc())
        .with_for_update()
        .all()
    )
    if not legs:
        raise NegativeFinalizationError("Negative payout legs not found")

    return legs


def _lock_bsc_intents(
    db: Session,
    *,
    settlement_batch_id: int,
) -> list[FundBscTransactionIntent]:
    return (
        db.query(
            FundBscTransactionIntent
        )
        .filter(
            FundBscTransactionIntent
            .settlement_batch_id
            == int(settlement_batch_id)
        )
        .order_by(
            FundBscTransactionIntent.id.asc()
        )
        .with_for_update()
        .all()
    )


def _required_intent_text(
    value: Any,
    *,
    field_name: str,
) -> str:
    text = str(
        value
        if value is not None
        else ""
    ).strip()

    if not text:
        raise NegativeFinalizationError(
            f"{field_name} is required"
        )

    return text


def _normalized_intent_address(
    value: Any,
    *,
    field_name: str,
) -> str:
    return _required_intent_text(
        value,
        field_name=field_name,
    ).lower()


def _normalized_intent_hash(
    value: Any,
    *,
    field_name: str,
) -> str:
    normalized = _required_intent_text(
        value,
        field_name=field_name,
    ).lower()

    if normalized.startswith("0x"):
        normalized = normalized[2:]

    if len(normalized) != 64:
        raise NegativeFinalizationError(
            f"{field_name} must be a 32-byte hash"
        )

    try:
        bytes.fromhex(normalized)
    except ValueError as exc:
        raise NegativeFinalizationError(
            f"{field_name} must be valid hex"
        ) from exc

    return f"0x{normalized}"


def _validate_confirmed_bsc_intent(
    intent: FundBscTransactionIntent,
) -> None:
    if (
        str(intent.status or "").strip()
        != BSC_INTENT_STATUS_CONFIRMED
    ):
        raise NegativeFinalizationError(
            "BSC intent must be confirmed: "
            f"intent_id={intent.id}, "
            f"status={intent.status}"
        )

    if intent.confirmed_at is None:
        raise NegativeFinalizationError(
            "Confirmed BSC intent confirmed_at "
            f"is missing: intent_id={intent.id}"
        )

    if int(intent.receipt_status or 0) != 1:
        raise NegativeFinalizationError(
            "Confirmed BSC intent receipt_status "
            f"must equal 1: intent_id={intent.id}"
        )

    required_confirmations = int(
        settings
        .NEGATIVE_NET_PAYOUT_CONFIRMATIONS_REQUIRED
    )

    if (
        int(intent.confirmations or 0)
        < required_confirmations
    ):
        raise NegativeFinalizationError(
            "Confirmed BSC intent has insufficient "
            "confirmations: "
            f"intent_id={intent.id}, "
            f"observed={intent.confirmations}, "
            f"required={required_confirmations}"
        )

    _normalized_intent_hash(
        intent.prepared_tx_hash,
        field_name=(
            f"bsc_intent_{intent.id}."
            "prepared_tx_hash"
        ),
    )

    _required_intent_text(
        intent.intent_fingerprint,
        field_name=(
            f"bsc_intent_{intent.id}."
            "intent_fingerprint"
        ),
    )


def _validate_payout_intent(
    *,
    intent: FundBscTransactionIntent,
    leg: FundNegativePayoutLeg,
    payout_batch: FundNegativePayoutBatch,
) -> dict[str, Any]:
    _validate_confirmed_bsc_intent(
        intent
    )

    if (
        int(intent.settlement_batch_id)
        != int(leg.settlement_batch_id)
    ):
        raise NegativeFinalizationError(
            "Payout BSC intent settlement batch "
            f"mismatch: intent_id={intent.id}"
        )

    if (
        int(intent.payout_batch_id)
        != int(payout_batch.id)
        or int(leg.payout_batch_id)
        != int(payout_batch.id)
    ):
        raise NegativeFinalizationError(
            "Payout BSC intent payout batch "
            f"mismatch: intent_id={intent.id}"
        )

    if (
        intent.payout_leg_id is None
        or int(intent.payout_leg_id)
        != int(leg.id)
    ):
        raise NegativeFinalizationError(
            "Payout BSC intent payout leg "
            f"mismatch: intent_id={intent.id}"
        )

    if int(intent.fund_id) != int(
        leg.fund_id
    ):
        raise NegativeFinalizationError(
            "Payout BSC intent fund mismatch: "
            f"intent_id={intent.id}"
        )

    if str(intent.asset or "").strip().upper() != (
        "USDT"
    ):
        raise NegativeFinalizationError(
            "Payout BSC intent asset must be USDT: "
            f"intent_id={intent.id}"
        )

    if not _same_decimal(
        intent.amount,
        leg.amount_usdt,
    ):
        raise NegativeFinalizationError(
            "Payout BSC intent amount mismatch: "
            f"intent_id={intent.id}, "
            f"payout_leg_id={leg.id}"
        )

    if (
        _normalized_intent_address(
            intent.from_address,
            field_name=(
                f"bsc_intent_{intent.id}."
                "from_address"
            ),
        )
        != _normalized_intent_address(
            leg.from_address,
            field_name=(
                f"payout_leg_{leg.id}."
                "from_address"
            ),
        )
    ):
        raise NegativeFinalizationError(
            "Payout BSC intent source address "
            f"mismatch: intent_id={intent.id}"
        )

    if (
        _normalized_intent_address(
            intent.to_address,
            field_name=(
                f"bsc_intent_{intent.id}."
                "to_address"
            ),
        )
        != _normalized_intent_address(
            leg.to_address,
            field_name=(
                f"payout_leg_{leg.id}."
                "to_address"
            ),
        )
    ):
        raise NegativeFinalizationError(
            "Payout BSC intent destination "
            f"mismatch: intent_id={intent.id}"
        )

    intent_tx_hash = (
        _normalized_intent_hash(
            intent.prepared_tx_hash,
            field_name=(
                f"bsc_intent_{intent.id}."
                "prepared_tx_hash"
            ),
        )
    )

    leg_tx_hash = _normalized_intent_hash(
        leg.tx_hash,
        field_name=(
            f"payout_leg_{leg.id}.tx_hash"
        ),
    )

    if intent_tx_hash != leg_tx_hash:
        raise NegativeFinalizationError(
            "Payout BSC intent transaction hash "
            f"mismatch: intent_id={intent.id}"
        )

    confirmation = leg.confirmation_json

    if not isinstance(
        confirmation,
        dict,
    ):
        raise NegativeFinalizationError(
            "Payout leg confirmation evidence "
            f"is missing: payout_leg_id={leg.id}"
        )

    if (
        confirmation.get(
            "durable_intent"
        )
        is not True
        or confirmation.get(
            "confirmed"
        )
        is not True
    ):
        raise NegativeFinalizationError(
            "Payout leg durable confirmation "
            f"is incomplete: payout_leg_id={leg.id}"
        )

    if int(
        confirmation.get(
            "intent_id"
        )
        or 0
    ) != int(intent.id):
        raise NegativeFinalizationError(
            "Payout leg confirmation intent_id "
            f"mismatch: payout_leg_id={leg.id}"
        )

    if str(
        confirmation.get(
            "intent_status"
        )
        or ""
    ).strip() != BSC_INTENT_STATUS_CONFIRMED:
        raise NegativeFinalizationError(
            "Payout leg confirmation intent status "
            f"mismatch: payout_leg_id={leg.id}"
        )

    confirmation_tx_hash = (
        _normalized_intent_hash(
            confirmation.get(
                "tx_hash"
            ),
            field_name=(
                f"payout_leg_{leg.id}."
                "confirmation.tx_hash"
            ),
        )
    )

    if confirmation_tx_hash != intent_tx_hash:
        raise NegativeFinalizationError(
            "Payout leg confirmation transaction "
            f"hash mismatch: payout_leg_id={leg.id}"
        )

    return {
        "intent_id": int(intent.id),
        "payout_leg_id": int(leg.id),
        "status": str(intent.status),
        "tx_hash": intent_tx_hash,
        "amount_usdt": _q10(
            intent.amount
        ),
        "confirmations": int(
            intent.confirmations or 0
        ),
        "receipt_status": int(
            intent.receipt_status or 0
        ),
        "intent_fingerprint": str(
            intent.intent_fingerprint
        ),
    }


def _validate_gas_intent(
    *,
    intent: FundBscTransactionIntent,
    payout_batch: FundNegativePayoutBatch,
) -> dict[str, Any]:
    _validate_confirmed_bsc_intent(
        intent
    )

    if (
        int(intent.settlement_batch_id)
        != int(payout_batch.settlement_batch_id)
        or int(intent.payout_batch_id)
        != int(payout_batch.id)
    ):
        raise NegativeFinalizationError(
            "Gas BSC intent batch mismatch: "
            f"intent_id={intent.id}"
        )

    if intent.payout_leg_id is not None:
        raise NegativeFinalizationError(
            "Gas BSC intent must not reference "
            f"a payout leg: intent_id={intent.id}"
        )

    if str(intent.asset or "").strip().upper() != (
        "BNB"
    ):
        raise NegativeFinalizationError(
            "Gas BSC intent asset must be BNB: "
            f"intent_id={intent.id}"
        )

    if dec(intent.amount) <= ZERO:
        raise NegativeFinalizationError(
            "Gas BSC intent amount must be "
            f"positive: intent_id={intent.id}"
        )

    if (
        _normalized_intent_address(
            intent.to_address,
            field_name=(
                f"bsc_intent_{intent.id}."
                "to_address"
            ),
        )
        != _normalized_intent_address(
            payout_batch
            .settlement_wallet_address,
            field_name=(
                "payout_batch."
                "settlement_wallet_address"
            ),
        )
    ):
        raise NegativeFinalizationError(
            "Gas BSC intent destination mismatch: "
            f"intent_id={intent.id}"
        )

    intent_tx_hash = (
        _normalized_intent_hash(
            intent.prepared_tx_hash,
            field_name=(
                f"bsc_intent_{intent.id}."
                "prepared_tx_hash"
            ),
        )
    )

    batch_tx_hash = _normalized_intent_hash(
        payout_batch.gas_topup_tx_hash,
        field_name=(
            "payout_batch.gas_topup_tx_hash"
        ),
    )

    if intent_tx_hash != batch_tx_hash:
        raise NegativeFinalizationError(
            "Gas BSC intent transaction hash "
            f"mismatch: intent_id={intent.id}"
        )

    reconciliation = (
        payout_batch.gas_reconciliation_json
    )

    if not isinstance(
        reconciliation,
        dict,
    ):
        raise NegativeFinalizationError(
            "Gas reconciliation evidence "
            "is missing"
        )

    if (
        reconciliation.get(
            "durable_intent"
        )
        is not True
        or reconciliation.get(
            "confirmed"
        )
        is not True
    ):
        raise NegativeFinalizationError(
            "Gas durable intent confirmation "
            "is incomplete"
        )

    if int(
        reconciliation.get(
            "intent_id"
        )
        or 0
    ) != int(intent.id):
        raise NegativeFinalizationError(
            "Gas reconciliation intent_id "
            "mismatch"
        )

    if str(
        reconciliation.get(
            "intent_status"
        )
        or ""
    ).strip() != BSC_INTENT_STATUS_CONFIRMED:
        raise NegativeFinalizationError(
            "Gas reconciliation intent status "
            "mismatch"
        )

    reconciliation_hash = (
        _normalized_intent_hash(
            reconciliation.get(
                "prepared_tx_hash"
            ),
            field_name=(
                "payout_batch."
                "gas_reconciliation."
                "prepared_tx_hash"
            ),
        )
    )

    if reconciliation_hash != (
        intent_tx_hash
    ):
        raise NegativeFinalizationError(
            "Gas reconciliation transaction "
            "hash mismatch"
        )

    return {
        "intent_id": int(intent.id),
        "status": str(intent.status),
        "tx_hash": intent_tx_hash,
        "amount_bnb": str(
            dec(intent.amount)
        ),
        "confirmations": int(
            intent.confirmations or 0
        ),
        "receipt_status": int(
            intent.receipt_status or 0
        ),
        "intent_fingerprint": str(
            intent.intent_fingerprint
        ),
    }


def _validate_bsc_delivery_intents(
    *,
    payout_batch: FundNegativePayoutBatch,
    payout_legs: list[FundNegativePayoutLeg],
    bsc_intents: list[
        FundBscTransactionIntent
    ],
) -> dict[str, Any]:
    unresolved_ids = [
        int(intent.id)
        for intent in bsc_intents
        if str(intent.status or "").strip()
        in BSC_INTENT_UNRESOLVED_STATUSES
    ]

    if unresolved_ids:
        raise NegativeFinalizationError(
            "Unresolved BSC intents block "
            f"finalization: {unresolved_ids}"
        )

    review_ids = [
        int(intent.id)
        for intent in bsc_intents
        if str(intent.status or "").strip()
        == (
            BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
        )
    ]

    if review_ids:
        raise NegativeFinalizationError(
            "BSC intents requiring review block "
            f"finalization: {review_ids}"
        )

    unsupported_status_ids = [
        int(intent.id)
        for intent in bsc_intents
        if str(intent.status or "").strip()
        not in (
            BSC_INTENT_UNRESOLVED_STATUSES
            | {
                BSC_INTENT_STATUS_CONFIRMED,
                BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW,
            }
        )
    ]

    if unsupported_status_ids:
        raise NegativeFinalizationError(
            "Unknown BSC intent statuses block "
            f"finalization: {unsupported_status_ids}"
        )

    payout_intents = [
        intent
        for intent in bsc_intents
        if str(
            intent.action_type or ""
        ).strip()
        == (
            BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT
        )
    ]

    gas_intents = [
        intent
        for intent in bsc_intents
        if str(
            intent.action_type or ""
        ).strip()
        == (
            BSC_INTENT_ACTION_NEGATIVE_SETTLEMENT_GAS_TOPUP
        )
    ]

    known_intent_ids = {
        int(intent.id)
        for intent in (
            payout_intents
            + gas_intents
        )
    }

    unknown_action_ids = [
        int(intent.id)
        for intent in bsc_intents
        if int(intent.id)
        not in known_intent_ids
    ]

    if unknown_action_ids:
        raise NegativeFinalizationError(
            "Unknown BSC intent actions block "
            f"finalization: {unknown_action_ids}"
        )

    payout_intents_by_leg: dict[
        int,
        list[FundBscTransactionIntent],
    ] = defaultdict(list)

    for intent in payout_intents:
        if intent.payout_leg_id is None:
            raise NegativeFinalizationError(
                "Payout BSC intent has no "
                f"payout_leg_id: intent_id={intent.id}"
            )

        payout_intents_by_leg[
            int(intent.payout_leg_id)
        ].append(intent)

    payout_evidence: list[
        dict[str, Any]
    ] = []

    expected_leg_ids = {
        int(leg.id)
        for leg in payout_legs
    }

    unexpected_leg_ids = sorted(
        set(payout_intents_by_leg)
        - expected_leg_ids
    )

    if unexpected_leg_ids:
        raise NegativeFinalizationError(
            "BSC payout intents reference "
            "unexpected payout legs: "
            f"{unexpected_leg_ids}"
        )

    for leg in payout_legs:
        matching = payout_intents_by_leg.get(
            int(leg.id),
            [],
        )

        if len(matching) != 1:
            raise NegativeFinalizationError(
                "Each payout leg must have exactly "
                "one confirmed BSC intent: "
                f"payout_leg_id={leg.id}, "
                f"intent_count={len(matching)}"
            )

        payout_evidence.append(
            _validate_payout_intent(
                intent=matching[0],
                leg=leg,
                payout_batch=payout_batch,
            )
        )

    if (
        str(payout_batch.gas_status or "")
        .strip()
        != PAYOUT_GAS_STATUS_READY
    ):
        raise NegativeFinalizationError(
            "Payout gas status must be ready "
            "before finalization"
        )

    gas_reconciliation = (
        payout_batch.gas_reconciliation_json
    )

    if not isinstance(
        gas_reconciliation,
        dict,
    ):
        raise NegativeFinalizationError(
            "Payout gas reconciliation evidence "
            "is required"
        )

    no_topup_needed = (
        gas_reconciliation.get(
            "no_real_gas_topup_needed"
        )
        is True
        and gas_reconciliation.get(
            "durable_intent_not_required"
        )
        is True
        and gas_reconciliation.get(
            "gas_sufficient"
        )
        is True
    )

    gas_evidence: dict[str, Any]

    if no_topup_needed:
        if gas_intents:
            raise NegativeFinalizationError(
                "Gas intent exists despite "
                "no-topup-needed evidence"
            )

        if str(
            payout_batch.gas_topup_tx_hash
            or ""
        ).strip():
            raise NegativeFinalizationError(
                "Gas top-up tx hash exists despite "
                "no-topup-needed evidence"
            )

        gas_evidence = {
            "mode": "not_needed",
            "intent_id": None,
            "tx_hash": None,
        }

    else:
        if len(gas_intents) != 1:
            raise NegativeFinalizationError(
                "Exactly one confirmed gas intent "
                "is required when gas top-up was "
                f"performed: count={len(gas_intents)}"
            )

        gas_evidence = {
            "mode": "confirmed_intent",
            **_validate_gas_intent(
                intent=gas_intents[0],
                payout_batch=payout_batch,
            ),
        }

    return {
        "schema": (
            "negative_finalization_"
            "bsc_delivery_evidence_v1"
        ),
        "bsc_intent_count": len(
            bsc_intents
        ),
        "payout_leg_count": len(
            payout_legs
        ),
        "payout_intents": (
            payout_evidence
        ),
        "gas": gas_evidence,
        "all_intents_terminal_confirmed": (
            True
        ),
        "prepared_raw_tx_omitted": True,
    }

def _lock_existing_finalization(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundNegativeFinalizationBatch | None:
    return (
        db.query(FundNegativeFinalizationBatch)
        .filter(
            FundNegativeFinalizationBatch.settlement_batch_id
            == int(settlement_batch_id)
        )
        .with_for_update()
        .first()
    )


def _balance_refresh_mapping(
    value: Any,
    *,
    field_name: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NegativeFinalizationError(
            f"{field_name} must be a mapping"
        )

    return value


def _balance_refresh_decimal(
    value: Any,
    *,
    field_name: str,
) -> Decimal:
    if value is None:
        raise NegativeFinalizationError(
            f"{field_name} is required"
        )

    try:
        return dec(value)
    except Exception as exc:
        raise NegativeFinalizationError(
            f"{field_name} must be a valid decimal"
        ) from exc


def _balance_refresh_integer(
    value: Any,
    *,
    field_name: str,
) -> int:
    if (
        value is None
        or isinstance(value, bool)
    ):
        raise NegativeFinalizationError(
            f"{field_name} must be a valid integer"
        )

    try:
        result = int(
            str(value).strip()
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise NegativeFinalizationError(
            f"{field_name} must be a valid integer"
        ) from exc

    if result < 0:
        raise NegativeFinalizationError(
            f"{field_name} must not be negative"
        )

    return result


def _validate_balance_refresh_evidence(
    *,
    payout_batch: FundNegativePayoutBatch,
    payout_legs: list[
        FundNegativePayoutLeg
    ],
) -> dict[str, Any]:
    if (
        str(
            payout_batch
            .balance_refresh_status
            or ""
        ).strip()
        != (
            PAYOUT_BALANCE_REFRESH_STATUS_CONFIRMED
        )
    ):
        raise NegativeFinalizationError(
            "Payout balance refresh status "
            "must be confirmed"
        )

    if (
        payout_batch
        .balance_refresh_completed_at
        is None
    ):
        raise NegativeFinalizationError(
            "Payout balance refresh completed_at "
            "is required"
        )

    expected_total = (
        _balance_refresh_decimal(
            payout_batch
            .expected_total_payout_usdt,
            field_name=(
                "payout_batch."
                "expected_total_payout_usdt"
            ),
        )
    )

    confirmed_total = (
        _balance_refresh_decimal(
            payout_batch
            .confirmed_total_payout_usdt,
            field_name=(
                "payout_batch."
                "confirmed_total_payout_usdt"
            ),
        )
    )

    if not _same_decimal(
        expected_total,
        confirmed_total,
    ):
        raise NegativeFinalizationError(
            "Payout expected and confirmed totals "
            "do not match"
        )

    leg_total = sum(
        (
            _balance_refresh_decimal(
                leg.amount_usdt,
                field_name=(
                    f"payout_leg_{leg.id}."
                    "amount_usdt"
                ),
            )
            for leg in payout_legs
        ),
        ZERO,
    )

    if not _same_decimal(
        leg_total,
        confirmed_total,
    ):
        raise NegativeFinalizationError(
            "Payout leg total does not match "
            "confirmed payout total"
        )

    batch_evidence = (
        _balance_refresh_mapping(
            payout_batch
            .balance_refresh_json,
            field_name=(
                "payout_batch."
                "balance_refresh_json"
            ),
        )
    )

    if (
        batch_evidence.get("live")
        is not True
        or batch_evidence.get(
            "absolute_onchain_sync"
        )
        is not True
    ):
        raise NegativeFinalizationError(
            "Payout batch balance refresh must "
            "contain live absolute on-chain sync "
            "evidence"
        )

    block_number = (
        _balance_refresh_integer(
            batch_evidence.get(
                "block_number"
            ),
            field_name=(
                "payout_batch."
                "balance_refresh.block_number"
            ),
        )
    )

    settlement_evidence = (
        _balance_refresh_mapping(
            batch_evidence.get(
                "settlement_wallet"
            ),
            field_name=(
                "payout_batch.balance_refresh."
                "settlement_wallet"
            ),
        )
    )

    expected_settlement_address = str(
        payout_batch
        .settlement_wallet_address
        or ""
    ).strip().lower()

    observed_settlement_address = str(
        settlement_evidence.get(
            "address"
        )
        or ""
    ).strip().lower()

    if (
        not expected_settlement_address
        or observed_settlement_address
        != expected_settlement_address
    ):
        raise NegativeFinalizationError(
            "Settlement wallet balance refresh "
            "address mismatch"
        )

    settlement_before = (
        _balance_refresh_decimal(
            settlement_evidence.get(
                "before_usdt"
            ),
            field_name=(
                "payout_batch.balance_refresh."
                "settlement_wallet.before_usdt"
            ),
        )
    )

    settlement_after = (
        _balance_refresh_decimal(
            settlement_evidence.get(
                "observed_after_usdt"
            ),
            field_name=(
                "payout_batch.balance_refresh."
                "settlement_wallet."
                "observed_after_usdt"
            ),
        )
    )

    evidence_confirmed_total = (
        _balance_refresh_decimal(
            settlement_evidence.get(
                "confirmed_total_payout_usdt"
            ),
            field_name=(
                "payout_batch.balance_refresh."
                "settlement_wallet."
                "confirmed_total_payout_usdt"
            ),
        )
    )

    if not _same_decimal(
        settlement_before,
        payout_batch
        .settlement_wallet_usdt_before,
    ):
        raise NegativeFinalizationError(
            "Settlement wallet balance-before "
            "evidence mismatch"
        )

    if not _same_decimal(
        settlement_after,
        payout_batch
        .settlement_wallet_usdt_after,
    ):
        raise NegativeFinalizationError(
            "Settlement wallet balance-after "
            "evidence mismatch"
        )

    if not _same_decimal(
        evidence_confirmed_total,
        confirmed_total,
    ):
        raise NegativeFinalizationError(
            "Settlement wallet confirmed payout "
            "evidence mismatch"
        )

    if settlement_evidence.get(
        "arithmetic_debit_applied"
    ) is not False:
        raise NegativeFinalizationError(
            "Settlement wallet balance refresh "
            "must be an absolute snapshot, not "
            "an arithmetic debit"
        )

    user_rows = batch_evidence.get(
        "user_wallets"
    )

    if not isinstance(
        user_rows,
        list,
    ):
        raise NegativeFinalizationError(
            "Payout batch user wallet refresh "
            "evidence is missing"
        )

    if len(user_rows) != len(
        payout_legs
    ):
        raise NegativeFinalizationError(
            "Payout batch user wallet refresh "
            "count mismatch"
        )

    rows_by_wallet_id: dict[
        int,
        dict[str, Any],
    ] = {}

    for position, raw_row in enumerate(
        user_rows
    ):
        row = _balance_refresh_mapping(
            raw_row,
            field_name=(
                "payout_batch.balance_refresh."
                f"user_wallets.{position}"
            ),
        )

        wallet_id = (
            _balance_refresh_integer(
                row.get(
                    "user_wallet_id"
                ),
                field_name=(
                    "payout_batch.balance_refresh."
                    f"user_wallets.{position}."
                    "user_wallet_id"
                ),
            )
        )

        if wallet_id in rows_by_wallet_id:
            raise NegativeFinalizationError(
                "Payout balance refresh contains "
                "duplicate user wallet evidence"
            )

        rows_by_wallet_id[
            wallet_id
        ] = row

    validated_legs: list[
        dict[str, Any]
    ] = []

    for leg in payout_legs:
        wallet_id_value = (
            leg.to_user_wallet_id
            if leg.to_user_wallet_id
            is not None
            else leg.user_wallet_id
        )

        wallet_id = (
            _balance_refresh_integer(
                wallet_id_value,
                field_name=(
                    f"payout_leg_{leg.id}."
                    "user_wallet_id"
                ),
            )
        )

        batch_row = (
            rows_by_wallet_id.get(
                wallet_id
            )
        )

        if batch_row is None:
            raise NegativeFinalizationError(
                "Payout leg has no matching "
                "batch wallet refresh evidence: "
                f"payout_leg_id={leg.id}"
            )

        leg_evidence = (
            _balance_refresh_mapping(
                leg.balance_refresh_json,
                field_name=(
                    f"payout_leg_{leg.id}."
                    "balance_refresh_json"
                ),
            )
        )

        if (
            leg_evidence.get("live")
            is not True
            or leg_evidence.get(
                "absolute_onchain_sync"
            )
            is not True
        ):
            raise NegativeFinalizationError(
                "Payout leg balance refresh must "
                "contain live absolute on-chain "
                f"sync evidence: leg_id={leg.id}"
            )

        leg_block_number = (
            _balance_refresh_integer(
                leg_evidence.get(
                    "block_number"
                ),
                field_name=(
                    f"payout_leg_{leg.id}."
                    "balance_refresh.block_number"
                ),
            )
        )

        batch_row_block_number = (
            _balance_refresh_integer(
                batch_row.get(
                    "block_number"
                ),
                field_name=(
                    "payout_batch.balance_refresh."
                    f"user_wallet_{wallet_id}."
                    "block_number"
                ),
            )
        )

        if (
            leg_block_number
            != block_number
            or batch_row_block_number
            != block_number
        ):
            raise NegativeFinalizationError(
                "Payout balance refresh block "
                f"mismatch: payout_leg_id={leg.id}"
            )

        if (
            _balance_refresh_integer(
                leg_evidence.get(
                    "user_wallet_id"
                ),
                field_name=(
                    f"payout_leg_{leg.id}."
                    "balance_refresh.user_wallet_id"
                ),
            )
            != wallet_id
        ):
            raise NegativeFinalizationError(
                "Payout leg balance refresh wallet "
                f"mismatch: payout_leg_id={leg.id}"
            )

        expected_address = str(
            leg.to_address or ""
        ).strip().lower()

        leg_address = str(
            leg_evidence.get(
                "address"
            )
            or ""
        ).strip().lower()

        batch_address = str(
            batch_row.get(
                "address"
            )
            or ""
        ).strip().lower()

        if (
            not expected_address
            or leg_address
            != expected_address
            or batch_address
            != expected_address
        ):
            raise NegativeFinalizationError(
                "Payout balance refresh destination "
                f"mismatch: payout_leg_id={leg.id}"
            )

        leg_before = (
            _balance_refresh_decimal(
                leg_evidence.get(
                    "before_usdt"
                ),
                field_name=(
                    f"payout_leg_{leg.id}."
                    "balance_refresh.before_usdt"
                ),
            )
        )

        leg_after = (
            _balance_refresh_decimal(
                leg_evidence.get(
                    "observed_after_usdt"
                ),
                field_name=(
                    f"payout_leg_{leg.id}."
                    "balance_refresh."
                    "observed_after_usdt"
                ),
            )
        )

        leg_amount = (
            _balance_refresh_decimal(
                leg_evidence.get(
                    "payout_amount_usdt"
                ),
                field_name=(
                    f"payout_leg_{leg.id}."
                    "balance_refresh."
                    "payout_amount_usdt"
                ),
            )
        )

        if not _same_decimal(
            leg_before,
            leg.wallet_balance_before_usdt,
        ):
            raise NegativeFinalizationError(
                "Payout leg wallet balance-before "
                f"mismatch: payout_leg_id={leg.id}"
            )

        if not _same_decimal(
            leg_after,
            leg.wallet_balance_after_usdt,
        ):
            raise NegativeFinalizationError(
                "Payout leg wallet balance-after "
                f"mismatch: payout_leg_id={leg.id}"
            )

        if not _same_decimal(
            leg_amount,
            leg.amount_usdt,
        ):
            raise NegativeFinalizationError(
                "Payout leg refreshed amount "
                f"mismatch: payout_leg_id={leg.id}"
            )

        for (
            field_name,
            expected_value,
        ) in (
            (
                "before_usdt",
                leg_before,
            ),
            (
                "observed_after_usdt",
                leg_after,
            ),
            (
                "payout_amount_usdt",
                leg_amount,
            ),
        ):
            if not _same_decimal(
                batch_row.get(
                    field_name
                ),
                expected_value,
            ):
                raise NegativeFinalizationError(
                    "Payout batch and leg balance "
                    "refresh evidence mismatch: "
                    f"payout_leg_id={leg.id}, "
                    f"field={field_name}"
                )

        if (
            leg_evidence.get(
                "arithmetic_credit_applied"
            )
            is not False
        ):
            raise NegativeFinalizationError(
                "User wallet balance refresh must "
                "be an absolute snapshot, not an "
                "arithmetic credit"
            )

        if batch_row.get(
            "absolute_onchain_sync"
        ) is not True:
            raise NegativeFinalizationError(
                "Batch user wallet evidence is not "
                "an absolute on-chain sync"
            )

        validated_legs.append(
            {
                "payout_leg_id": int(
                    leg.id
                ),
                "user_wallet_id": wallet_id,
                "address": expected_address,
                "amount_usdt": _q10(
                    leg.amount_usdt
                ),
                "observed_after_usdt": (
                    _q10(leg_after)
                ),
                "block_number": (
                    block_number
                ),
                "absolute_onchain_sync": (
                    True
                ),
            }
        )

    return {
        "schema": (
            "negative_finalization_"
            "balance_refresh_gate_v1"
        ),
        "status": (
            PAYOUT_BALANCE_REFRESH_STATUS_CONFIRMED
        ),
        "completed_at": (
            payout_batch
            .balance_refresh_completed_at
        ),
        "block_number": block_number,
        "expected_total_payout_usdt": (
            _q10(expected_total)
        ),
        "confirmed_total_payout_usdt": (
            _q10(confirmed_total)
        ),
        "settlement_wallet_address": (
            expected_settlement_address
        ),
        "payout_leg_count": len(
            payout_legs
        ),
        "validated_legs": validated_legs,
        "absolute_onchain_sync": True,
        "arithmetic_balance_updates": False,
    }


def _authoritative_payout_wallet_id(
    leg: FundNegativePayoutLeg,
) -> int:
    raw_wallet_id = (
        leg.to_user_wallet_id
        if leg.to_user_wallet_id is not None
        else leg.user_wallet_id
    )

    if (
        raw_wallet_id is None
        or isinstance(
            raw_wallet_id,
            (bool, float),
        )
    ):
        raise NegativeFinalizationError(
            "payout_user_wallet_missing: "
            "authoritative payout wallet ID is "
            f"missing for payout_leg_id={leg.id}"
        )

    try:
        wallet_id = int(
            str(raw_wallet_id).strip()
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise NegativeFinalizationError(
            "payout_user_wallet_missing: "
            "authoritative payout wallet ID is "
            f"invalid for payout_leg_id={leg.id}"
        ) from exc

    if wallet_id <= 0:
        raise NegativeFinalizationError(
            "payout_user_wallet_missing: "
            "authoritative payout wallet ID must "
            f"be positive for payout_leg_id={leg.id}"
        )

    return wallet_id


def _lock_payout_user_wallets(
    db: Session,
    *,
    payout_legs: list[
        FundNegativePayoutLeg
    ],
) -> dict[int, UserWallet]:
    if not payout_legs:
        raise NegativeFinalizationError(
            "payout_user_wallet_missing: "
            "no payout legs were supplied"
        )

    wallet_id_to_leg_id: dict[
        int,
        int,
    ] = {}

    for leg in payout_legs:
        wallet_id = (
            _authoritative_payout_wallet_id(
                leg
            )
        )

        existing_leg_id = (
            wallet_id_to_leg_id.get(
                wallet_id
            )
        )

        if existing_leg_id is not None:
            raise NegativeFinalizationError(
                "payout_user_wallet_duplicate_mapping: "
                f"user_wallet_id={wallet_id} is "
                "referenced by payout legs "
                f"{existing_leg_id} and {leg.id}"
            )

        wallet_id_to_leg_id[
            wallet_id
        ] = int(leg.id)

    wallet_ids = sorted(
        wallet_id_to_leg_id
    )

    wallet_rows = (
        db.query(UserWallet)
        .filter(
            UserWallet.id.in_(
                wallet_ids
            )
        )
        .order_by(
            UserWallet.id.asc()
        )
        .with_for_update()
        .all()
    )

    wallets_by_id: dict[
        int,
        UserWallet,
    ] = {}

    for wallet in wallet_rows:
        try:
            wallet_id = int(wallet.id)
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise NegativeFinalizationError(
                "payout_user_wallet_duplicate_mapping: "
                "loaded payout wallet has an "
                "invalid primary key"
            ) from exc

        if wallet_id in wallets_by_id:
            raise NegativeFinalizationError(
                "payout_user_wallet_duplicate_mapping: "
                "duplicate locked UserWallet row: "
                f"user_wallet_id={wallet_id}"
            )

        if wallet_id not in (
            wallet_id_to_leg_id
        ):
            raise NegativeFinalizationError(
                "payout_user_wallet_duplicate_mapping: "
                "unexpected UserWallet row returned: "
                f"user_wallet_id={wallet_id}"
            )

        wallets_by_id[
            wallet_id
        ] = wallet

    missing_wallet_ids = sorted(
        set(wallet_ids)
        - set(wallets_by_id)
    )

    if missing_wallet_ids:
        raise NegativeFinalizationError(
            "payout_user_wallet_missing: "
            "exact payout UserWallet rows were "
            f"not found: {missing_wallet_ids}"
        )

    if len(wallets_by_id) != len(
        payout_legs
    ):
        raise NegativeFinalizationError(
            "payout_user_wallet_duplicate_mapping: "
            "payout leg to UserWallet mapping "
            "is not one-to-one"
        )

    return wallets_by_id


def _wallet_gate_aware_datetime(
    value: Any,
    *,
    missing_reason: str,
    naive_reason: str,
    field_name: str,
) -> datetime:
    if value is None:
        raise NegativeFinalizationError(
            f"{missing_reason}: "
            f"{field_name} is missing"
        )

    if not isinstance(
        value,
        datetime,
    ):
        raise NegativeFinalizationError(
            f"{naive_reason}: "
            f"{field_name} is not a datetime"
        )

    if (
        value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise NegativeFinalizationError(
            f"{naive_reason}: "
            f"{field_name} must be timezone-aware"
        )

    return value.astimezone(
        timezone.utc
    )


def _validate_payout_user_wallet_db_gate(
    *,
    payout_batch: FundNegativePayoutBatch,
    payout_legs: list[
        FundNegativePayoutLeg
    ],
    locked_wallets: dict[
        int,
        UserWallet,
    ],
    balance_refresh_validation: dict[
        str,
        Any,
    ],
) -> dict[str, Any]:
    validated_rows = (
        balance_refresh_validation.get(
            "validated_legs"
        )
    )

    if not isinstance(
        validated_rows,
        list,
    ):
        raise NegativeFinalizationError(
            "payout_user_wallet_missing: "
            "validated payout balance-refresh "
            "rows are missing"
        )

    refresh_by_leg_id: dict[
        int,
        dict[str, Any],
    ] = {}

    for raw_row in validated_rows:
        if not isinstance(
            raw_row,
            dict,
        ):
            raise NegativeFinalizationError(
                "payout_user_wallet_duplicate_mapping: "
                "invalid validated payout wallet row"
            )

        leg_id = (
            _balance_refresh_integer(
                raw_row.get(
                    "payout_leg_id"
                ),
                field_name=(
                    "balance_refresh.validated_legs."
                    "payout_leg_id"
                ),
            )
        )

        if leg_id in refresh_by_leg_id:
            raise NegativeFinalizationError(
                "payout_user_wallet_duplicate_mapping: "
                "duplicate validated payout leg: "
                f"payout_leg_id={leg_id}"
            )

        refresh_by_leg_id[
            leg_id
        ] = raw_row

    if len(refresh_by_leg_id) != len(
        payout_legs
    ):
        raise NegativeFinalizationError(
            "payout_user_wallet_duplicate_mapping: "
            "validated payout wallet row count "
            "does not match payout leg count"
        )

    batch_refresh_completed_at = (
        _wallet_gate_aware_datetime(
            payout_batch
            .balance_refresh_completed_at,
            missing_reason=(
                "payout_user_wallet_updated_at_missing"
            ),
            naive_reason=(
                "payout_user_wallet_updated_at_"
                "not_timezone_aware"
            ),
            field_name=(
                "payout_batch."
                "balance_refresh_completed_at"
            ),
        )
    )

    evidence_rows: list[
        dict[str, Any]
    ] = []

    for leg in sorted(
        payout_legs,
        key=lambda item: int(item.id),
    ):
        leg_id = int(leg.id)
        wallet_id = (
            _authoritative_payout_wallet_id(
                leg
            )
        )

        wallet = locked_wallets.get(
            wallet_id
        )

        if wallet is None:
            raise NegativeFinalizationError(
                "payout_user_wallet_missing: "
                "locked payout UserWallet is "
                f"missing for payout_leg_id={leg_id}, "
                f"user_wallet_id={wallet_id}"
            )

        try:
            loaded_wallet_id = int(
                wallet.id
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise NegativeFinalizationError(
                "payout_user_wallet_missing: "
                "loaded payout UserWallet ID "
                f"is invalid for payout_leg_id={leg_id}"
            ) from exc

        if loaded_wallet_id != wallet_id:
            raise NegativeFinalizationError(
                "payout_user_wallet_missing: "
                "loaded UserWallet does not match "
                "the authoritative payout wallet ID: "
                f"payout_leg_id={leg_id}, "
                f"expected={wallet_id}, "
                f"observed={loaded_wallet_id}"
            )

        if leg.user_id is None:
            raise NegativeFinalizationError(
                "payout_user_wallet_user_mismatch: "
                "payout leg user_id is missing: "
                f"payout_leg_id={leg_id}"
            )

        expected_user_id = int(
            leg.user_id
        )

        if int(wallet.user_id) != (
            expected_user_id
        ):
            raise NegativeFinalizationError(
                "payout_user_wallet_user_mismatch: "
                f"payout_leg_id={leg_id}, "
                f"user_wallet_id={wallet_id}, "
                f"expected_user_id={expected_user_id}, "
                f"observed_user_id={wallet.user_id}"
            )

        wallet_blockchain = str(
            wallet.blockchain or ""
        ).strip().upper()

        if wallet_blockchain != "BSC":
            raise NegativeFinalizationError(
                "payout_user_wallet_blockchain_mismatch: "
                f"payout_leg_id={leg_id}, "
                f"user_wallet_id={wallet_id}, "
                f"observed={wallet_blockchain or 'empty'}"
            )

        expected_address = str(
            leg.to_address or ""
        ).strip().lower()

        wallet_address = str(
            wallet.address or ""
        ).strip().lower()

        if (
            not expected_address
            or wallet_address
            != expected_address
        ):
            raise NegativeFinalizationError(
                "payout_user_wallet_address_mismatch: "
                f"payout_leg_id={leg_id}, "
                f"user_wallet_id={wallet_id}"
            )

        refresh_row = (
            refresh_by_leg_id.get(
                leg_id
            )
        )

        if refresh_row is None:
            raise NegativeFinalizationError(
                "payout_user_wallet_missing: "
                "validated balance-refresh row "
                f"is missing for payout_leg_id={leg_id}"
            )

        refresh_wallet_id = (
            _balance_refresh_integer(
                refresh_row.get(
                    "user_wallet_id"
                ),
                field_name=(
                    "balance_refresh.validated_legs."
                    f"{leg_id}.user_wallet_id"
                ),
            )
        )

        if refresh_wallet_id != wallet_id:
            raise NegativeFinalizationError(
                "payout_user_wallet_missing: "
                "balance-refresh wallet ID does "
                "not match authoritative payout "
                f"wallet: payout_leg_id={leg_id}"
            )

        refresh_address = str(
            refresh_row.get(
                "address"
            )
            or ""
        ).strip().lower()

        if refresh_address != (
            expected_address
        ):
            raise NegativeFinalizationError(
                "payout_user_wallet_address_mismatch: "
                "balance-refresh address does not "
                "match exact payout wallet: "
                f"payout_leg_id={leg_id}"
            )

        expected_after = (
            _balance_refresh_decimal(
                refresh_row.get(
                    "observed_after_usdt"
                ),
                field_name=(
                    "balance_refresh.validated_legs."
                    f"{leg_id}.observed_after_usdt"
                ),
            )
        )

        if wallet.usdt_balance is None:
            raise NegativeFinalizationError(
                "payout_user_wallet_balance_missing: "
                f"payout_leg_id={leg_id}, "
                f"user_wallet_id={wallet_id}"
            )

        db_balance = (
            _balance_refresh_decimal(
                wallet.usdt_balance,
                field_name=(
                    f"user_wallet_{wallet_id}."
                    "usdt_balance"
                ),
            )
        )

        if not _same_decimal(
            db_balance,
            expected_after,
        ):
            raise NegativeFinalizationError(
                "payout_user_wallet_balance_mismatch: "
                f"payout_leg_id={leg_id}, "
                f"user_wallet_id={wallet_id}, "
                f"expected={_q10(expected_after)}, "
                f"observed={_q10(db_balance)}"
            )

        if not _same_decimal(
            leg.wallet_balance_after_usdt,
            expected_after,
        ):
            raise NegativeFinalizationError(
                "payout_user_wallet_balance_mismatch: "
                "payout leg balance-after no longer "
                "matches authoritative refresh: "
                f"payout_leg_id={leg_id}"
            )

        if (
            wallet.usdt_balance_block
            is None
        ):
            raise NegativeFinalizationError(
                "payout_user_wallet_block_missing: "
                f"payout_leg_id={leg_id}, "
                f"user_wallet_id={wallet_id}"
            )

        db_block = (
            _balance_refresh_integer(
                wallet.usdt_balance_block,
                field_name=(
                    f"user_wallet_{wallet_id}."
                    "usdt_balance_block"
                ),
            )
        )

        expected_block = (
            _balance_refresh_integer(
                refresh_row.get(
                    "block_number"
                ),
                field_name=(
                    "balance_refresh.validated_legs."
                    f"{leg_id}.block_number"
                ),
            )
        )

        if db_block != expected_block:
            raise NegativeFinalizationError(
                "payout_user_wallet_block_mismatch: "
                f"payout_leg_id={leg_id}, "
                f"user_wallet_id={wallet_id}, "
                f"expected={expected_block}, "
                f"observed={db_block}"
            )

        payout_confirmed_at = (
            _wallet_gate_aware_datetime(
                leg.confirmed_at,
                missing_reason=(
                    "payout_user_wallet_updated_at_stale"
                ),
                naive_reason=(
                    "payout_user_wallet_updated_at_"
                    "not_timezone_aware"
                ),
                field_name=(
                    f"payout_leg_{leg_id}."
                    "confirmed_at"
                ),
            )
        )

        db_updated_at = (
            _wallet_gate_aware_datetime(
                wallet
                .usdt_balance_updated_at,
                missing_reason=(
                    "payout_user_wallet_updated_at_"
                    "missing"
                ),
                naive_reason=(
                    "payout_user_wallet_updated_at_"
                    "not_timezone_aware"
                ),
                field_name=(
                    f"user_wallet_{wallet_id}."
                    "usdt_balance_updated_at"
                ),
            )
        )

        if db_updated_at < (
            payout_confirmed_at
        ):
            raise NegativeFinalizationError(
                "payout_user_wallet_updated_at_stale: "
                f"payout_leg_id={leg_id}, "
                f"user_wallet_id={wallet_id}, "
                f"wallet_updated_at="
                f"{db_updated_at.isoformat()}, "
                f"payout_confirmed_at="
                f"{payout_confirmed_at.isoformat()}"
            )

        evidence_rows.append(
            {
                "payout_leg_id": leg_id,
                "user_id": expected_user_id,
                "user_wallet_id": wallet_id,
                "address": expected_address,
                "db_usdt_balance": (
                    _q10(db_balance)
                ),
                "expected_observed_after_usdt": (
                    _q10(expected_after)
                ),
                "db_usdt_balance_block": (
                    db_block
                ),
                "expected_refresh_block": (
                    expected_block
                ),
                "db_usdt_balance_updated_at": (
                    db_updated_at
                ),
                "payout_confirmed_at": (
                    payout_confirmed_at
                ),
                "batch_balance_refresh_completed_at": (
                    batch_refresh_completed_at
                ),
                "exact_balance_match": True,
                "exact_block_match": True,
            }
        )

    if set(locked_wallets) != {
        row["user_wallet_id"]
        for row in evidence_rows
    }:
        raise NegativeFinalizationError(
            "payout_user_wallet_duplicate_mapping: "
            "locked UserWallet set does not exactly "
            "match payout evidence"
        )

    return {
        "schema": (
            USER_WALLET_DB_GATE_SCHEMA
        ),
        "all_wallets_locked": True,
        "all_wallets_exact_match": True,
        "arithmetic_balance_updates": False,
        "wallets": evidence_rows,
    }


def _bybit_required_mapping(
    value: Any,
    *,
    field_name: str,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NegativeFinalizationError(
            f"{field_name} must be a mapping"
        )

    return value


def _bybit_required_text(
    value: Any,
    *,
    field_name: str,
) -> str:
    text = str(
        value
        if value is not None
        else ""
    ).strip()

    if not text:
        raise NegativeFinalizationError(
            f"{field_name} is required"
        )

    return text


def _bybit_required_integer(
    value: Any,
    *,
    field_name: str,
) -> int:
    if (
        value is None
        or isinstance(value, bool)
    ):
        raise NegativeFinalizationError(
            f"{field_name} must be a valid integer"
        )

    try:
        result = int(
            str(value).strip()
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise NegativeFinalizationError(
            f"{field_name} must be a valid integer"
        ) from exc

    if result < 0:
        raise NegativeFinalizationError(
            f"{field_name} must not be negative"
        )

    return result


def _bybit_required_fingerprint(
    value: Any,
    *,
    field_name: str,
) -> str:
    fingerprint = _bybit_required_text(
        value,
        field_name=field_name,
    ).lower()

    if (
        len(fingerprint) != 64
        or any(
            character
            not in "0123456789abcdef"
            for character in fingerprint
        )
    ):
        raise NegativeFinalizationError(
            f"{field_name} must be a SHA-256 "
            "fingerprint"
        )

    return fingerprint


def _bybit_aware_datetime(
    value: Any,
    *,
    field_name: str,
) -> datetime:
    text = _bybit_required_text(
        value,
        field_name=field_name,
    )

    try:
        result = datetime.fromisoformat(
            text
        )
    except ValueError as exc:
        raise NegativeFinalizationError(
            f"{field_name} must be ISO datetime"
        ) from exc

    if (
        result.tzinfo is None
        or result.utcoffset() is None
    ):
        raise NegativeFinalizationError(
            f"{field_name} must be timezone-aware"
        )

    return result.astimezone(
        timezone.utc
    )


def _bybit_reject_float(
    value: Any,
    *,
    path: str = "root",
) -> None:
    if isinstance(value, float):
        raise NegativeFinalizationError(
            "Float is forbidden in durable "
            f"Bybit evidence: {path}"
        )

    if isinstance(value, dict):
        for key, item in value.items():
            _bybit_reject_float(
                item,
                path=f"{path}.{key}",
            )

        return

    if isinstance(value, (list, tuple)):
        for index, item in enumerate(
            value
        ):
            _bybit_reject_float(
                item,
                path=f"{path}[{index}]",
            )


def _bybit_evidence_fingerprint(
    value: dict[str, Any],
) -> str:
    _bybit_reject_float(value)

    try:
        canonical = json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise NegativeFinalizationError(
            "Durable Bybit evidence is not "
            "canonical JSON"
        ) from exc

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def _bybit_decimal_text(
    value: Any,
    *,
    field_name: str,
) -> str:
    if value is None:
        raise NegativeFinalizationError(
            f"{field_name} is required"
        )

    try:
        resolved = Decimal(
            str(value)
        )
    except (
        ArithmeticError,
        TypeError,
        ValueError,
    ) as exc:
        raise NegativeFinalizationError(
            f"{field_name} must be a valid decimal"
        ) from exc

    if not resolved.is_finite():
        raise NegativeFinalizationError(
            f"{field_name} must be finite"
        )

    text = format(
        resolved,
        "f",
    )

    if "." in text:
        text = text.rstrip(
            "0"
        ).rstrip(".")

    return text or "0"


def _bybit_normalized_tx_hash(
    value: Any,
    *,
    field_name: str,
) -> str:
    text = _bybit_required_text(
        value,
        field_name=field_name,
    ).lower()

    if not text.startswith("0x"):
        text = f"0x{text}"

    raw = text[2:]

    if (
        len(raw) != 64
        or any(
            character
            not in "0123456789abcdef"
            for character in raw
        )
    ):
        raise NegativeFinalizationError(
            f"{field_name} must be a 32-byte "
            "transaction hash"
        )

    return f"0x{raw}"


def _bybit_cash_delivery_fingerprints(
    *,
    bybit_flow: FundNegativeBybitFlow,
) -> dict[str, str]:
    reconciliation_root = (
        _bybit_required_mapping(
            bybit_flow.reconciliation_json,
            field_name=(
                "bybit_flow.reconciliation_json"
            ),
        )
    )

    master_barrier = (
        _bybit_required_mapping(
            reconciliation_root.get(
                "master_transferable_"
                "balance_barrier"
            ),
            field_name=(
                "bybit_flow.reconciliation."
                "master_transferable_balance_barrier"
            ),
        )
    )

    universal_reconciliation = (
        _bybit_required_mapping(
            bybit_flow
            .universal_transfer_reconciliation_json,
            field_name=(
                "bybit_flow.universal_transfer_"
                "reconciliation_json"
            ),
        )
    )

    withdrawal_reconciliation = (
        _bybit_required_mapping(
            bybit_flow
            .withdrawal_reconciliation_json,
            field_name=(
                "bybit_flow.withdrawal_"
                "reconciliation_json"
            ),
        )
    )

    receipt = _bybit_required_mapping(
        bybit_flow
        .settlement_wallet_receipt_json,
        field_name=(
            "bybit_flow.settlement_wallet_"
            "receipt_json"
        ),
    )

    return {
        "universal_reconciliation": (
            _bybit_evidence_fingerprint(
                universal_reconciliation
            )
        ),
        "master_balance_barrier": (
            _bybit_evidence_fingerprint(
                master_barrier
            )
        ),
        "withdrawal_reconciliation": (
            _bybit_evidence_fingerprint(
                withdrawal_reconciliation
            )
        ),
        "settlement_wallet_receipt": (
            _bybit_evidence_fingerprint(
                receipt
            )
        ),
    }


def _bybit_cash_delivery_identity(
    *,
    settlement_batch: (
        FundSettlementBatch
    ),
    bybit_flow: (
        FundNegativeBybitFlow
    ),
) -> dict[str, Any]:
    if (
        int(bybit_flow.settlement_batch_id)
        != int(settlement_batch.id)
    ):
        raise NegativeFinalizationError(
            "Bybit flow settlement batch mismatch"
        )

    if (
        int(bybit_flow.fund_id)
        != int(settlement_batch.fund_id)
    ):
        raise NegativeFinalizationError(
            "Bybit flow fund mismatch"
        )

    settlement_wallet_id = (
        _bybit_required_integer(
            bybit_flow.settlement_wallet_id,
            field_name=(
                "bybit_flow.settlement_wallet_id"
            ),
        )
    )

    address = _bybit_required_text(
        bybit_flow
        .settlement_wallet_address,
        field_name=(
            "bybit_flow."
            "settlement_wallet_address"
        ),
    ).lower()

    address_hash = hashlib.sha256(
        address.encode("utf-8")
    ).hexdigest()

    withdrawal_tx_hash = (
        _bybit_normalized_tx_hash(
            bybit_flow.withdrawal_tx_hash,
            field_name=(
                "bybit_flow.withdrawal_tx_hash"
            ),
        )
    )

    return {
        "settlement_batch_id": str(
            int(settlement_batch.id)
        ),
        "flow_id": str(
            int(bybit_flow.id)
        ),
        "fund_id": str(
            int(settlement_batch.fund_id)
        ),
        "settlement_wallet_id": str(
            settlement_wallet_id
        ),
        "settlement_wallet_address_sha256": (
            address_hash
        ),
        "universal_transfer_id": (
            _bybit_required_text(
                bybit_flow
                .universal_transfer_id,
                field_name=(
                    "bybit_flow."
                    "universal_transfer_id"
                ),
            )
        ),
        "withdrawal_request_id": (
            _bybit_required_text(
                bybit_flow
                .withdrawal_request_id,
                field_name=(
                    "bybit_flow."
                    "withdrawal_request_id"
                ),
            )
        ),
        "withdrawal_id": (
            _bybit_required_text(
                bybit_flow.withdrawal_id,
                field_name=(
                    "bybit_flow.withdrawal_id"
                ),
            )
        ),
        "withdrawal_tx_hash": (
            withdrawal_tx_hash
        ),
        "required_master_usdt": (
            _bybit_decimal_text(
                bybit_flow
                .required_master_usdt,
                field_name=(
                    "bybit_flow."
                    "required_master_usdt"
                ),
            )
        ),
        "withdrawal_amount_usdt": (
            _bybit_decimal_text(
                bybit_flow
                .withdrawal_amount_usdt,
                field_name=(
                    "bybit_flow."
                    "withdrawal_amount_usdt"
                ),
            )
        ),
        "withdrawal_fee_usdt": (
            _bybit_decimal_text(
                bybit_flow
                .bybit_withdrawal_fee_usdt,
                field_name=(
                    "bybit_flow."
                    "bybit_withdrawal_fee_usdt"
                ),
            )
        ),
        "retained_fees_usdt": (
            _bybit_decimal_text(
                bybit_flow.retained_fees_usdt,
                field_name=(
                    "bybit_flow."
                    "retained_fees_usdt"
                ),
            )
        ),
        "balance_before_usdt": (
            _bybit_decimal_text(
                bybit_flow
                .settlement_wallet_balance_before_usdt,
                field_name=(
                    "bybit_flow.settlement_wallet_"
                    "balance_before_usdt"
                ),
            )
        ),
        "balance_after_usdt": (
            _bybit_decimal_text(
                bybit_flow
                .settlement_wallet_balance_after_usdt,
                field_name=(
                    "bybit_flow.settlement_wallet_"
                    "balance_after_usdt"
                ),
            )
        ),
        "confirmations": (
            _bybit_required_integer(
                bybit_flow
                .settlement_wallet_receipt_confirmations,
                field_name=(
                    "bybit_flow.settlement_wallet_"
                    "receipt_confirmations"
                ),
            )
        ),
        "receipt_block_number": (
            _bybit_required_integer(
                bybit_flow
                .settlement_wallet_receipt_block_number,
                field_name=(
                    "bybit_flow.settlement_wallet_"
                    "receipt_block_number"
                ),
            )
        ),
    }


def _validate_bybit_cash_delivery_evidence(
    *,
    settlement_batch: (
        FundSettlementBatch
    ),
    bybit_flow: (
        FundNegativeBybitFlow
    ),
) -> dict[str, Any]:
    if str(bybit_flow.status) != (
        BYBIT_FLOW_STATUS_COMPLETED
    ):
        raise NegativeFinalizationError(
            "Bybit cash delivery flow must be "
            "completed"
        )

    identity = (
        _bybit_cash_delivery_identity(
            settlement_batch=(
                settlement_batch
            ),
            bybit_flow=bybit_flow,
        )
    )

    universal_intent = (
        _bybit_required_mapping(
            bybit_flow
            .universal_transfer_intent_json,
            field_name=(
                "bybit_flow.universal_transfer_"
                "intent_json"
            ),
        )
    )

    if universal_intent.get(
        "schema"
    ) != BYBIT_UNIVERSAL_INTENT_SCHEMA:
        raise NegativeFinalizationError(
            "Universal Transfer intent schema "
            "mismatch"
        )

    if universal_intent.get(
        "policy_version"
    ) != BYBIT_CASH_DELIVERY_POLICY_VERSION:
        raise NegativeFinalizationError(
            "Universal Transfer intent policy "
            "mismatch"
        )

    if universal_intent.get(
        "state"
    ) != "confirmed":
        raise NegativeFinalizationError(
            "Universal Transfer intent is not "
            "confirmed"
        )

    universal_payload = (
        _bybit_required_mapping(
            universal_intent.get(
                "payload"
            ),
            field_name=(
                "universal_transfer_intent."
                "payload"
            ),
        )
    )

    universal_payload_fingerprint = (
        _bybit_required_fingerprint(
            universal_intent.get(
                "payload_fingerprint"
            ),
            field_name=(
                "universal_transfer_intent."
                "payload_fingerprint"
            ),
        )
    )

    if universal_payload_fingerprint != (
        _bybit_evidence_fingerprint(
            universal_payload
        )
    ):
        raise NegativeFinalizationError(
            "Universal Transfer payload "
            "fingerprint mismatch"
        )

    expected_universal_payload = {
        "transferId": (
            identity[
                "universal_transfer_id"
            ]
        ),
        "coin": _bybit_required_text(
            (
                bybit_flow
                .universal_transfer_coin
                or bybit_flow.coin
            ),
            field_name=(
                "bybit_flow."
                "universal_transfer_coin"
            ),
        ).upper(),
        "amount": _bybit_decimal_text(
            bybit_flow
            .universal_transfer_amount_usdt,
            field_name=(
                "bybit_flow.universal_transfer_"
                "amount_usdt"
            ),
        ),
        "fromMemberId": (
            _bybit_required_text(
                bybit_flow.from_sub_uid,
                field_name=(
                    "bybit_flow.from_sub_uid"
                ),
            )
        ),
        "toMemberId": (
            _bybit_required_text(
                bybit_flow.to_master_uid,
                field_name=(
                    "bybit_flow.to_master_uid"
                ),
            )
        ),
        "fromAccountType": (
            _bybit_required_text(
                bybit_flow.from_account_type,
                field_name=(
                    "bybit_flow."
                    "from_account_type"
                ),
            ).upper()
        ),
        "toAccountType": (
            _bybit_required_text(
                bybit_flow.to_account_type,
                field_name=(
                    "bybit_flow.to_account_type"
                ),
            ).upper()
        ),
    }

    if universal_payload != (
        expected_universal_payload
    ):
        raise NegativeFinalizationError(
            "Universal Transfer immutable "
            "payload mismatch"
        )

    universal_reconciliation = (
        _bybit_required_mapping(
            bybit_flow
            .universal_transfer_reconciliation_json,
            field_name=(
                "bybit_flow.universal_transfer_"
                "reconciliation_json"
            ),
        )
    )

    if universal_intent.get(
        "reconciliation"
    ) != universal_reconciliation:
        raise NegativeFinalizationError(
            "Universal Transfer durable "
            "reconciliation mismatch"
        )

    if universal_reconciliation.get(
        "schema"
    ) != BYBIT_UNIVERSAL_RECONCILIATION_SCHEMA:
        raise NegativeFinalizationError(
            "Universal Transfer reconciliation "
            "schema mismatch"
        )

    if (
        universal_reconciliation.get(
            "phase"
        )
        != "exact_transfer_id_query"
        or universal_reconciliation.get(
            "record_found"
        )
        is not True
        or universal_reconciliation.get(
            "query_succeeded"
        )
        is not True
        or universal_reconciliation.get(
            "exact_match"
        )
        is not True
        or universal_reconciliation.get(
            "no_automatic_resend"
        )
        is not True
    ):
        raise NegativeFinalizationError(
            "Universal Transfer reconciliation "
            "evidence is incomplete"
        )

    if (
        _bybit_required_text(
            universal_reconciliation.get(
                "transfer_id"
            ),
            field_name=(
                "universal_transfer_"
                "reconciliation.transfer_id"
            ),
        )
        != identity[
            "universal_transfer_id"
        ]
    ):
        raise NegativeFinalizationError(
            "Universal Transfer reconciliation "
            "transfer ID mismatch"
        )

    if not _is_bybit_success(
        universal_reconciliation.get(
            "observed_status"
        )
    ):
        raise NegativeFinalizationError(
            "Universal Transfer reconciliation "
            "status is not successful"
        )

    if not _is_bybit_success(
        bybit_flow
        .universal_transfer_status
    ):
        raise NegativeFinalizationError(
            "Universal Transfer flow status "
            "is not successful"
        )

    if (
        bybit_flow
        .universal_transfer_confirmed_at
        is None
    ):
        raise NegativeFinalizationError(
            "Universal Transfer confirmed_at "
            "is required"
        )

    reconciliation_root = (
        _bybit_required_mapping(
            bybit_flow.reconciliation_json,
            field_name=(
                "bybit_flow.reconciliation_json"
            ),
        )
    )

    master_barrier = (
        _bybit_required_mapping(
            reconciliation_root.get(
                "master_transferable_"
                "balance_barrier"
            ),
            field_name=(
                "bybit_flow.reconciliation."
                "master_transferable_balance_barrier"
            ),
        )
    )

    if (
        master_barrier.get("schema")
        != BYBIT_MASTER_BALANCE_SCHEMA
        or master_barrier.get("state")
        != "confirmed"
        or master_barrier.get(
            "query_succeeded"
        )
        is not True
        or master_barrier.get(
            "sufficient"
        )
        is not True
        or master_barrier.get(
            "withdrawal_allowed"
        )
        is not True
    ):
        raise NegativeFinalizationError(
            "Master transferable balance "
            "barrier is not confirmed"
        )

    if (
        str(
            master_barrier.get(
                "account_type"
            )
            or ""
        ).strip().upper()
        != "FUND"
    ):
        raise NegativeFinalizationError(
            "Master balance barrier account "
            "type mismatch"
        )

    if (
        str(
            master_barrier.get("coin")
            or ""
        ).strip().upper()
        != str(
            bybit_flow.coin or ""
        ).strip().upper()
    ):
        raise NegativeFinalizationError(
            "Master balance barrier coin "
            "mismatch"
        )

    if (
        str(
            master_barrier.get(
                "member_id"
            )
            or ""
        ).strip()
        != str(
            bybit_flow.to_master_uid
            or ""
        ).strip()
    ):
        raise NegativeFinalizationError(
            "Master balance barrier member "
            "ID mismatch"
        )

    if not _same_decimal(
        master_barrier.get(
            "required_master_usdt"
        ),
        bybit_flow.required_master_usdt,
    ):
        raise NegativeFinalizationError(
            "Master balance barrier amount "
            "mismatch"
        )

    withdrawal_intent = (
        _bybit_required_mapping(
            bybit_flow
            .withdrawal_intent_json,
            field_name=(
                "bybit_flow."
                "withdrawal_intent_json"
            ),
        )
    )

    if withdrawal_intent.get(
        "schema"
    ) != BYBIT_WITHDRAWAL_INTENT_SCHEMA:
        raise NegativeFinalizationError(
            "Withdrawal intent schema mismatch"
        )

    if withdrawal_intent.get(
        "policy_version"
    ) != settings.NEGATIVE_NET_WITHDRAWAL_POLICY_VERSION:
        raise NegativeFinalizationError(
            "Withdrawal intent policy mismatch"
        )

    if withdrawal_intent.get(
        "state"
    ) != "confirmed":
        raise NegativeFinalizationError(
            "Withdrawal intent is not confirmed"
        )

    withdrawal_payload = (
        _bybit_required_mapping(
            withdrawal_intent.get(
                "payload_template"
            ),
            field_name=(
                "withdrawal_intent."
                "payload_template"
            ),
        )
    )

    withdrawal_payload_fingerprint = (
        _bybit_required_fingerprint(
            withdrawal_intent.get(
                "payload_fingerprint"
            ),
            field_name=(
                "withdrawal_intent."
                "payload_fingerprint"
            ),
        )
    )

    if withdrawal_payload_fingerprint != (
        _bybit_evidence_fingerprint(
            withdrawal_payload
        )
    ):
        raise NegativeFinalizationError(
            "Withdrawal payload fingerprint "
            "mismatch"
        )

    expected_withdrawal_payload = {
        "requestId": (
            identity[
                "withdrawal_request_id"
            ]
        ),
        "coin": (
            _bybit_required_text(
                bybit_flow.withdrawal_coin,
                field_name=(
                    "bybit_flow.withdrawal_coin"
                ),
            ).upper()
        ),
        "chain": (
            _bybit_required_text(
                bybit_flow.withdrawal_chain,
                field_name=(
                    "bybit_flow.withdrawal_chain"
                ),
            ).upper()
        ),
        "address": (
            _bybit_required_text(
                bybit_flow
                .withdrawal_address,
                field_name=(
                    "bybit_flow."
                    "withdrawal_address"
                ),
            )
        ),
        "amount": (
            _bybit_decimal_text(
                bybit_flow
                .withdrawal_amount_usdt,
                field_name=(
                    "bybit_flow."
                    "withdrawal_amount_usdt"
                ),
            )
        ),
        "forceChain": 1,
        "feeType": int(
            settings
            .NEGATIVE_NET_WITHDRAWAL_FEE_TYPE
        ),
        "accountType": "FUND",
    }

    if withdrawal_payload != (
        expected_withdrawal_payload
    ):
        raise NegativeFinalizationError(
            "Withdrawal immutable payload "
            "mismatch"
        )

    if not _same_decimal(
        withdrawal_intent.get(
            "fee_usdt"
        ),
        bybit_flow.withdrawal_fee_usdt,
    ):
        raise NegativeFinalizationError(
            "Withdrawal intent fee mismatch"
        )

    if not _same_decimal(
        bybit_flow.withdrawal_fee_usdt,
        bybit_flow
        .bybit_withdrawal_fee_usdt,
    ):
        raise NegativeFinalizationError(
            "Withdrawal flow fee snapshot "
            "mismatch"
        )

    if not _same_decimal(
        bybit_flow.withdrawal_amount_usdt,
        bybit_flow
        .withdrawal_request_amount_usdt,
    ):
        raise NegativeFinalizationError(
            "Withdrawal amount does not match "
            "the immutable request amount"
        )

    withdrawal_reconciliation = (
        _bybit_required_mapping(
            bybit_flow
            .withdrawal_reconciliation_json,
            field_name=(
                "bybit_flow.withdrawal_"
                "reconciliation_json"
            ),
        )
    )

    if withdrawal_intent.get(
        "reconciliation"
    ) != withdrawal_reconciliation:
        raise NegativeFinalizationError(
            "Withdrawal durable reconciliation "
            "mismatch"
        )

    if withdrawal_reconciliation.get(
        "schema"
    ) != BYBIT_WITHDRAWAL_RECONCILIATION_SCHEMA:
        raise NegativeFinalizationError(
            "Withdrawal reconciliation schema "
            "mismatch"
        )

    if withdrawal_reconciliation.get(
        "state"
    ) != "confirmed":
        raise NegativeFinalizationError(
            "Withdrawal reconciliation state "
            "is not confirmed"
        )

    if (
        withdrawal_reconciliation.get(
            "unique_match"
        )
        is not True
        or withdrawal_reconciliation.get(
            "ambiguous"
        )
        is not False
        or withdrawal_reconciliation.get(
            "exact_fingerprint_match"
        )
        is not True
    ):
        raise NegativeFinalizationError(
            "Withdrawal reconciliation "
            "unique-match evidence is invalid"
        )

    if withdrawal_reconciliation.get(
        "no_automatic_resend"
    ) is not True:
        raise NegativeFinalizationError(
            "Withdrawal reconciliation "
            "no-resend marker is missing"
        )

    if (
        _bybit_required_text(
            withdrawal_reconciliation.get(
                "request_id"
            ),
            field_name=(
                "withdrawal_reconciliation."
                "request_id"
            ),
        )
        != identity[
            "withdrawal_request_id"
        ]
    ):
        raise NegativeFinalizationError(
            "Withdrawal reconciliation "
            "request ID mismatch"
        )

    reconciliation_tx_hash = (
        _bybit_normalized_tx_hash(
            withdrawal_reconciliation.get(
                "tx_hash"
            ),
            field_name=(
                "withdrawal_reconciliation."
                "tx_hash"
            ),
        )
    )

    if reconciliation_tx_hash != (
        identity["withdrawal_tx_hash"]
    ):
        raise NegativeFinalizationError(
            "Withdrawal reconciliation "
            "transaction hash mismatch"
        )

    record_fingerprint = (
        _bybit_required_fingerprint(
            withdrawal_reconciliation.get(
                "record_fingerprint"
            ),
            field_name=(
                "withdrawal_reconciliation."
                "record_fingerprint"
            ),
        )
    )

    withdrawal_record = (
        _bybit_required_mapping(
            bybit_flow.withdrawal_record_json,
            field_name=(
                "bybit_flow."
                "withdrawal_record_json"
            ),
        )
    )

    if (
        _bybit_required_fingerprint(
            withdrawal_record.get(
                "record_fingerprint"
            ),
            field_name=(
                "withdrawal_record."
                "record_fingerprint"
            ),
        )
        != record_fingerprint
    ):
        raise NegativeFinalizationError(
            "Withdrawal record fingerprint "
            "mismatch"
        )

    if (
        _bybit_required_text(
            withdrawal_record.get(
                "withdrawal_id"
            ),
            field_name=(
                "withdrawal_record."
                "withdrawal_id"
            ),
        )
        != identity["withdrawal_id"]
    ):
        raise NegativeFinalizationError(
            "Withdrawal record ID mismatch"
        )

    if (
        _bybit_normalized_tx_hash(
            withdrawal_record.get(
                "tx_hash"
            ),
            field_name=(
                "withdrawal_record.tx_hash"
            ),
        )
        != identity["withdrawal_tx_hash"]
    ):
        raise NegativeFinalizationError(
            "Withdrawal record transaction "
            "hash mismatch"
        )

    if withdrawal_record.get(
        "raw_omitted"
    ) is not True:
        raise NegativeFinalizationError(
            "Withdrawal record raw-state "
            "redaction marker is missing"
        )

    if not _is_withdrawal_success_like(
        bybit_flow.withdrawal_status
    ):
        raise NegativeFinalizationError(
            "Withdrawal flow status is not "
            "successful"
        )

    if (
        bybit_flow.withdrawal_confirmed_at
        is None
    ):
        raise NegativeFinalizationError(
            "Withdrawal confirmed_at is required"
        )

    receipt = _bybit_required_mapping(
        bybit_flow
        .settlement_wallet_receipt_json,
        field_name=(
            "bybit_flow.settlement_wallet_"
            "receipt_json"
        ),
    )

    if (
        receipt.get("schema")
        != BYBIT_SETTLEMENT_RECEIPT_SCHEMA
    ):
        raise NegativeFinalizationError(
            "Settlement wallet receipt schema "
            "mismatch"
        )

    if receipt.get(
        "policy_version"
    ) != settings.NEGATIVE_NET_WITHDRAWAL_POLICY_VERSION:
        raise NegativeFinalizationError(
            "Settlement wallet receipt policy "
            "mismatch"
        )

    if receipt.get("state") != "confirmed":
        raise NegativeFinalizationError(
            "Settlement wallet receipt state "
            "is not confirmed"
        )

    if (
        receipt.get(
            "exact_transfer_log_match"
        )
        is not True
        or receipt.get(
            "balance_delta_covers_expected"
        )
        is not True
        or receipt.get(
            "raw_receipt_omitted"
        )
        is not True
    ):
        raise NegativeFinalizationError(
            "Settlement wallet receipt "
            "confirmation evidence is incomplete"
        )

    if (
        _bybit_normalized_tx_hash(
            receipt.get("tx_hash"),
            field_name=(
                "settlement_wallet_receipt."
                "tx_hash"
            ),
        )
        != identity["withdrawal_tx_hash"]
    ):
        raise NegativeFinalizationError(
            "Settlement wallet receipt "
            "transaction hash mismatch"
        )

    if (
        str(
            bybit_flow
            .settlement_wallet_receipt_status
            or ""
        ).strip().upper()
        != "CONFIRMED"
    ):
        raise NegativeFinalizationError(
            "Settlement wallet flow receipt "
            "status is not confirmed"
        )

    if (
        _bybit_normalized_tx_hash(
            bybit_flow
            .settlement_wallet_receipt_tx_hash,
            field_name=(
                "bybit_flow.settlement_wallet_"
                "receipt_tx_hash"
            ),
        )
        != identity["withdrawal_tx_hash"]
    ):
        raise NegativeFinalizationError(
            "Settlement wallet flow receipt "
            "transaction hash mismatch"
        )

    if (
        bybit_flow
        .settlement_wallet_receipt_confirmed_at
        is None
    ):
        raise NegativeFinalizationError(
            "Settlement wallet receipt "
            "confirmed_at is required"
        )

    expected_amount = Decimal(
        identity[
            "withdrawal_amount_usdt"
        ]
    )

    if not _same_decimal(
        receipt.get(
            "expected_amount_usdt"
        ),
        expected_amount,
    ):
        raise NegativeFinalizationError(
            "Settlement wallet receipt "
            "expected amount mismatch"
        )

    if not _same_decimal(
        bybit_flow
        .settlement_wallet_received_usdt,
        expected_amount,
    ):
        raise NegativeFinalizationError(
            "Settlement wallet received amount "
            "mismatch"
        )

    receipt_confirmations = (
        _bybit_required_integer(
            receipt.get(
                "confirmations"
            ),
            field_name=(
                "settlement_wallet_receipt."
                "confirmations"
            ),
        )
    )

    if receipt_confirmations != (
        identity["confirmations"]
    ):
        raise NegativeFinalizationError(
            "Settlement wallet receipt "
            "confirmations mismatch"
        )

    required_confirmations = int(
        settings
        .NEGATIVE_NET_BSC_INTENT_CONFIRMATIONS_REQUIRED
    )

    if (
        receipt_confirmations
        < required_confirmations
    ):
        raise NegativeFinalizationError(
            "Settlement wallet receipt has "
            "insufficient confirmations"
        )

    receipt_block_number = (
        _bybit_required_integer(
            receipt.get(
                "receipt_block_number"
            ),
            field_name=(
                "settlement_wallet_receipt."
                "receipt_block_number"
            ),
        )
    )

    if receipt_block_number != (
        identity[
            "receipt_block_number"
        ]
    ):
        raise NegativeFinalizationError(
            "Settlement wallet receipt block "
            "number mismatch"
        )

    decimals = int(
        settings.BSC_USDT_DECIMALS
    )

    expected_raw_decimal = (
        expected_amount
        * (
            Decimal("10")
            ** decimals
        )
    )

    expected_raw = int(
        expected_raw_decimal
    )

    if Decimal(expected_raw) != (
        expected_raw_decimal
    ):
        raise NegativeFinalizationError(
            "Withdrawal amount cannot be "
            "represented exactly in raw units"
        )

    receipt_expected_raw = (
        _bybit_required_integer(
            receipt.get(
                "expected_raw"
            ),
            field_name=(
                "settlement_wallet_receipt."
                "expected_raw"
            ),
        )
    )

    matched_total_raw = (
        _bybit_required_integer(
            receipt.get(
                "matched_transfer_total_raw"
            ),
            field_name=(
                "settlement_wallet_receipt."
                "matched_transfer_total_raw"
            ),
        )
    )

    balance_delta_raw = (
        _bybit_required_integer(
            receipt.get(
                "balance_delta_raw"
            ),
            field_name=(
                "settlement_wallet_receipt."
                "balance_delta_raw"
            ),
        )
    )

    unrelated_raw = (
        _bybit_required_integer(
            receipt.get(
                "unrelated_additional_"
                "incoming_raw"
            ),
            field_name=(
                "settlement_wallet_receipt."
                "unrelated_additional_incoming_raw"
            ),
        )
    )

    if (
        receipt_expected_raw
        != expected_raw
        or matched_total_raw
        != expected_raw
        or balance_delta_raw
        < expected_raw
        or unrelated_raw
        != (
            balance_delta_raw
            - expected_raw
        )
    ):
        raise NegativeFinalizationError(
            "Settlement wallet receipt raw "
            "amount evidence mismatch"
        )

    malformed_count = (
        _bybit_required_integer(
            receipt.get(
                "malformed_matching_log_count"
            ),
            field_name=(
                "settlement_wallet_receipt."
                "malformed_matching_log_count"
            ),
        )
    )

    if (
        malformed_count != 0
        or receipt.get(
            "malformed_matching_logs"
        )
        != []
    ):
        raise NegativeFinalizationError(
            "Settlement wallet receipt contains "
            "malformed matching logs"
        )

    matched_log_count = (
        _bybit_required_integer(
            receipt.get(
                "matched_transfer_log_count"
            ),
            field_name=(
                "settlement_wallet_receipt."
                "matched_transfer_log_count"
            ),
        )
    )

    if matched_log_count <= 0:
        raise NegativeFinalizationError(
            "Settlement wallet receipt has no "
            "matching transfer logs"
        )

    before_usdt = Decimal(
        identity["balance_before_usdt"]
    )

    after_usdt = Decimal(
        identity["balance_after_usdt"]
    )

    if (
        after_usdt
        - before_usdt
        < expected_amount
    ):
        raise NegativeFinalizationError(
            "Settlement wallet balance delta "
            "does not cover withdrawal amount"
        )

    fingerprints = (
        _bybit_cash_delivery_fingerprints(
            bybit_flow=bybit_flow,
        )
    )

    completion = (
        _bybit_required_mapping(
            reconciliation_root.get(
                "cash_delivery_completion"
            ),
            field_name=(
                "bybit_flow.reconciliation."
                "cash_delivery_completion"
            ),
        )
    )

    report = _bybit_required_mapping(
        bybit_flow.report_json,
        field_name=(
            "bybit_flow.report_json"
        ),
    )

    if completion.get(
        "schema"
    ) != BYBIT_CASH_DELIVERY_COMPLETION_SCHEMA:
        raise NegativeFinalizationError(
            "Cash-delivery completion schema "
            "mismatch"
        )

    if completion.get(
        "policy_version"
    ) != BYBIT_CASH_DELIVERY_POLICY_VERSION:
        raise NegativeFinalizationError(
            "Cash-delivery completion policy "
            "mismatch"
        )

    if completion.get(
        "state"
    ) != "completed":
        raise NegativeFinalizationError(
            "Cash-delivery completion state "
            "mismatch"
        )

    if report.get(
        "schema"
    ) != BYBIT_CASH_DELIVERY_REPORT_SCHEMA:
        raise NegativeFinalizationError(
            "Cash-delivery report schema "
            "mismatch"
        )

    if report.get(
        "policy_version"
    ) != BYBIT_CASH_DELIVERY_POLICY_VERSION:
        raise NegativeFinalizationError(
            "Cash-delivery report policy "
            "mismatch"
        )

    if report.get(
        "state"
    ) != "completed":
        raise NegativeFinalizationError(
            "Cash-delivery report state mismatch"
        )

    completion_at = (
        _bybit_aware_datetime(
            completion.get(
                "completed_at"
            ),
            field_name=(
                "cash_delivery_completion."
                "completed_at"
            ),
        )
    )

    report_at = _bybit_aware_datetime(
        report.get(
            "completed_at"
        ),
        field_name=(
            "cash_delivery_report.completed_at"
        ),
    )

    if completion_at != report_at:
        raise NegativeFinalizationError(
            "Cash-delivery completion and report "
            "timestamps mismatch"
        )

    for key, expected_value in (
        identity.items()
    ):
        if completion.get(
            key
        ) != expected_value:
            raise NegativeFinalizationError(
                "Cash-delivery completion identity "
                f"mismatch: {key}"
            )

        if report.get(
            key
        ) != expected_value:
            raise NegativeFinalizationError(
                "Cash-delivery report identity "
                f"mismatch: {key}"
            )

    if completion.get(
        "evidence_fingerprints"
    ) != fingerprints:
        raise NegativeFinalizationError(
            "Cash-delivery completion evidence "
            "fingerprints mismatch"
        )

    if report.get(
        "evidence_fingerprints"
    ) != fingerprints:
        raise NegativeFinalizationError(
            "Cash-delivery report evidence "
            "fingerprints mismatch"
        )

    if (
        completion.get(
            "db_only_transition"
        )
        is not True
        or completion.get(
            "bybit_get_count"
        )
        != 0
        or completion.get(
            "bybit_post_count"
        )
        != 0
        or completion.get(
            "bsc_rpc_read_count"
        )
        != 0
        or completion.get(
            "seller_payouts_started"
        )
        is not False
        or completion.get(
            "accounting_finalized"
        )
        is not False
        or completion.get(
            "reserve_release_allowed"
        )
        is not False
        or completion.get(
            "pricing_unlock_allowed"
        )
        is not False
    ):
        raise NegativeFinalizationError(
            "Cash-delivery completion violates "
            "the strict finalization boundary"
        )

    if (
        report.get(
            "cash_ready_for_payout"
        )
        is not True
        or report.get(
            "seller_payouts_started"
        )
        is not False
        or report.get(
            "accounting_finalized"
        )
        is not False
        or report.get(
            "reserve_release_allowed"
        )
        is not False
        or report.get(
            "pricing_unlock_allowed"
        )
        is not False
    ):
        raise NegativeFinalizationError(
            "Cash-delivery report violates "
            "the strict finalization boundary"
        )

    return {
        "schema": (
            "negative_finalization_"
            "bybit_cash_delivery_gate_v1"
        ),
        "flow_id": int(
            bybit_flow.id
        ),
        "settlement_batch_id": int(
            settlement_batch.id
        ),
        "universal_transfer_id": (
            identity[
                "universal_transfer_id"
            ]
        ),
        "withdrawal_id": (
            identity["withdrawal_id"]
        ),
        "withdrawal_tx_hash": (
            identity[
                "withdrawal_tx_hash"
            ]
        ),
        "completion_fingerprint": (
            _bybit_evidence_fingerprint(
                completion
            )
        ),
        "report_fingerprint": (
            _bybit_evidence_fingerprint(
                report
            )
        ),
        "evidence_fingerprints": (
            fingerprints
        ),
        "durable_evidence_validated": True,
        "selected_source_required": False,
        "raw_external_payloads_omitted": True,
    }


def _validate_input_state(
    *,
    settlement_batch: FundSettlementBatch,
    sale_batch: FundNegativeSaleBatch,
    bybit_flow: FundNegativeBybitFlow,
    payout_batch: FundNegativePayoutBatch,
    payout_legs: list[FundNegativePayoutLeg],
    bsc_intents: list[
        FundBscTransactionIntent
    ],
    existing_finalization: FundNegativeFinalizationBatch | None,
) -> dict[str, Any]:
    idempotent_completed = (
        existing_finalization is not None
        and existing_finalization.status == FINALIZATION_BATCH_STATUS_COMPLETED
        and settlement_batch.status == BATCH_STATUS_NEGATIVE_CASH_SETTLEMENT_COMPLETED
    )

    if not idempotent_completed:
        if settlement_batch.status != BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED:
            raise NegativeFinalizationError(
                "Settlement batch status must be negative_net_payouts_confirmed"
            )

    allowed_sale_statuses = {
        SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED,
        SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED_WITH_EXTRA_SALE,
    }
    if sale_batch.status not in allowed_sale_statuses:
        raise NegativeFinalizationError("Sale batch must be completed")

    if bybit_flow.status != BYBIT_FLOW_STATUS_COMPLETED:
        raise NegativeFinalizationError("Bybit flow must be completed")

    if settings.NEGATIVE_NET_FINALIZATION_REQUIRE_PAYOUTS_CONFIRMED:
        if payout_batch.status != PAYOUT_BATCH_STATUS_COMPLETED:
            raise NegativeFinalizationError("Payout batch must be completed")

        bad_legs = [
            int(leg.id)
            for leg in payout_legs
            if leg.status != PAYOUT_LEG_STATUS_BALANCE_REFRESHED
        ]
        if bad_legs:
            raise NegativeFinalizationError(
                f"All payout legs must be balance_refreshed: {bad_legs}"
            )

        if not payout_batch.balance_refresh_json:
            raise NegativeFinalizationError(
                "Payout batch balance_refresh_json is required"
            )

        missing_leg_refresh = [
            int(leg.id) for leg in payout_legs if not leg.balance_refresh_json
        ]
        if missing_leg_refresh:
            raise NegativeFinalizationError(
                f"Payout leg balance_refresh_json is required: {missing_leg_refresh}"
            )

        if payout_batch.confirmed_total_payout_usdt is None:
            raise NegativeFinalizationError(
                "Payout confirmed_total_payout_usdt is required"
            )

        if payout_batch.confirmed_payout_leg_count != payout_batch.payout_leg_count:
            raise NegativeFinalizationError("Payout confirmed count mismatch")

    if settlement_batch.settlement_price_usdt is None:
        raise NegativeFinalizationError("Settlement price is required")

    if dec(settlement_batch.settlement_price_usdt) <= ZERO:
        raise NegativeFinalizationError("Settlement price must be positive")

    if settlement_batch.shares_outstanding_before is None:
        raise NegativeFinalizationError("Shares outstanding before is required")

    if settlement_batch.pricing_locked_at is None:
        raise NegativeFinalizationError("Pricing lock must exist before finalization")

    if not idempotent_completed and settlement_batch.pricing_unlocked_at is not None:
        raise NegativeFinalizationError("Pricing must not be unlocked before finalization")

    bybit_cash_delivery = (
        _validate_bybit_cash_delivery_evidence(
            settlement_batch=(
                settlement_batch
            ),
            bybit_flow=bybit_flow,
        )
    )

    bsc_delivery = (
        _validate_bsc_delivery_intents(
            payout_batch=payout_batch,
            payout_legs=payout_legs,
            bsc_intents=bsc_intents,
        )
    )

    balance_refresh = (
        _validate_balance_refresh_evidence(
            payout_batch=payout_batch,
            payout_legs=payout_legs,
        )
    )

    return {
        "bybit_cash_delivery": (
            bybit_cash_delivery
        ),
        "bsc_delivery": bsc_delivery,
        "balance_refresh": (
            balance_refresh
        ),
    }


def _new_or_existing_finalization(
    db: Session,
    *,
    existing: FundNegativeFinalizationBatch | None,
    settlement_batch: FundSettlementBatch,
    payout_batch: FundNegativePayoutBatch,
    bybit_flow: FundNegativeBybitFlow,
    sale_batch: FundNegativeSaleBatch,
    fund: Fund,
    now,
) -> FundNegativeFinalizationBatch:
    if existing is not None:
        return existing

    finalization = FundNegativeFinalizationBatch(
        settlement_batch_id=int(settlement_batch.id),
        payout_batch_id=int(payout_batch.id),
        bybit_flow_id=int(bybit_flow.id),
        sale_batch_id=int(sale_batch.id),
        fund_id=int(fund.id),
        status=FINALIZATION_BATCH_STATUS_CREATED,
        settlement_price_usdt=dec(settlement_batch.settlement_price_usdt),
        shares_outstanding_before=dec(settlement_batch.shares_outstanding_before),
        created_at=now,
        updated_at=now,
    )
    db.add(finalization)
    db.flush()
    return finalization


def _load_relevant_orders(
    db: Session,
    *,
    settlement_batch_id: int,
) -> list[FundOrder]:
    excluded_statuses = [
        ORDER_STATUS_FAILED,
        ORDER_STATUS_FAILED_REQUIRES_REVIEW,
        ORDER_STATUS_CANCELLED,
    ]

    orders = (
        db.query(FundOrder)
        .filter(FundOrder.settlement_batch_id == int(settlement_batch_id))
        .filter(FundOrder.side.in_([ORDER_SIDE_BUY, ORDER_SIDE_REDEEM]))
        .filter(~FundOrder.status.in_(excluded_statuses))
        .order_by(FundOrder.side.asc(), FundOrder.user_id.asc(), FundOrder.id.asc())
        .with_for_update()
        .all()
    )
    if not orders:
        raise NegativeFinalizationError("No relevant settlement orders found")

    return orders


def _detect_partial_finalization(
    *,
    orders: list[FundOrder],
    existing_finalization: FundNegativeFinalizationBatch | None,
) -> bool:
    success_count = sum(1 for order in orders if order.status == ORDER_STATUS_SUCCESS)
    if success_count == 0:
        return False

    if success_count == len(orders):
        return False

    return True


def _all_orders_success(orders: list[FundOrder]) -> bool:
    return all(order.status == ORDER_STATUS_SUCCESS for order in orders)


def _split_orders(orders: list[FundOrder]) -> tuple[list[FundOrder], list[FundOrder]]:
    buy_orders = [order for order in orders if order.side == ORDER_SIDE_BUY]
    redeem_orders = [order for order in orders if order.side == ORDER_SIDE_REDEEM]
    return buy_orders, redeem_orders


def _covered_redeem_order_ids(
    *,
    payout_legs: list[FundNegativePayoutLeg],
) -> set[int]:
    covered: set[int] = set()
    for leg in payout_legs:
        covered.update(_order_ids_from_leg(leg))
    return covered


def _validate_redeem_orders(
    *,
    redeem_orders: list[FundOrder],
    payout_batch: FundNegativePayoutBatch,
    payout_legs: list[FundNegativePayoutLeg],
) -> dict[str, Any]:
    redeem_ids = {
        int(order.id)
        for order in redeem_orders
    }
    covered_ids = _covered_redeem_order_ids(
        payout_legs=payout_legs,
    )

    if redeem_ids != covered_ids:
        missing = sorted(redeem_ids - covered_ids)
        extra = sorted(covered_ids - redeem_ids)
        raise NegativeFinalizationError(
            "Payout legs must cover all redeem orders. "
            f"missing={missing}, extra={extra}"
        )

    total_net_payout = ZERO
    total_redeem_shares = ZERO
    total_partial_month_fee = ZERO

    for order in redeem_orders:
        redeem_shares = _share_4dp(
            order.shares,
            field_name=(
                f"redeem_order_{order.id}_shares"
            ),
        )

        if redeem_shares <= ZERO:
            raise NegativeShareQuantityError(
                f"Redeem order {order.id} "
                "shares must be positive"
            )

        if (
            order.net_user_payout_usdt is None
            or dec(order.net_user_payout_usdt) <= ZERO
        ):
            raise NegativeFinalizationError(
                f"Redeem order {order.id} "
                "net_user_payout_usdt must be positive"
            )

        if (
            order.net_price_usdt is None
            or dec(order.net_price_usdt) <= ZERO
        ):
            raise NegativeFinalizationError(
                f"Redeem order {order.id} "
                "net_price_usdt must be positive"
            )

        if (
            order.partial_month_fee_usdt is not None
            and dec(order.partial_month_fee_usdt) < ZERO
        ):
            raise NegativeFinalizationError(
                f"Redeem order {order.id} "
                "partial_month_fee_usdt must be >= 0"
            )

        total_net_payout += dec(
            order.net_user_payout_usdt
        )
        total_redeem_shares += redeem_shares
        total_partial_month_fee += dec(
            order.partial_month_fee_usdt or ZERO
        )

    total_redeem_shares = _share_4dp(
        total_redeem_shares,
        field_name="total_redeem_shares",
    )

    if not _same_decimal(
        total_net_payout,
        payout_batch.confirmed_total_payout_usdt,
    ):
        raise NegativeFinalizationError(
            "Payout total must match redeem orders"
        )

    return {
        "redeem_order_ids": sorted(redeem_ids),
        "payout_leg_order_ids": sorted(covered_ids),
        "total_net_user_payout_usdt": (
            _q10(total_net_payout)
        ),
        "total_redeem_shares": (
            total_redeem_shares
        ),
        "total_partial_month_fee_usdt": (
            _q10(total_partial_month_fee)
        ),
    }


def _validate_buy_orders(
    *,
    buy_orders: list[FundOrder],
    settlement_price_usdt: Decimal,
) -> dict[str, Any]:
    total_buy_usdt = ZERO
    total_buy_shares = ZERO
    computed_shares_by_order_id: dict[
        int,
        Decimal,
    ] = {}

    for order in buy_orders:
        try:
            quantity = (
                calculate_successful_buy_share_quantity(
                    amount_usdt=order.amount_usdt,
                    settlement_price_usdt=(
                        settlement_price_usdt
                    ),
                )
            )
        except ShareQuantityError as exc:
            raise NegativeShareQuantityError(
                f"buy_order_{order.id}:{exc}"
            ) from exc

        buy_shares = quantity.issued_shares

        if order.shares is not None:
            stored_shares = _share_4dp(
                order.shares,
                field_name=(
                    f"buy_order_{order.id}_shares"
                ),
            )

            if stored_shares != buy_shares:
                raise NegativeShareQuantityError(
                    f"Buy order {order.id} "
                    "shares mismatch with canonical "
                    "4dp settlement calculation"
                )

        computed_shares_by_order_id[
            int(order.id)
        ] = buy_shares
        total_buy_usdt += (
            quantity.full_investment_usdt
        )
        total_buy_shares += buy_shares

    total_buy_shares = _share_4dp(
        total_buy_shares,
        field_name="total_buy_shares",
    )

    return {
        "total_buy_usdt": _q10(total_buy_usdt),
        "total_buy_shares": total_buy_shares,
        "computed_shares_by_order_id": (
            computed_shares_by_order_id
        ),
    }


def _lock_position(
    db: Session,
    *,
    user_id: int,
    fund_id: int,
) -> UserFundPosition | None:
    return (
        db.query(UserFundPosition)
        .filter(UserFundPosition.user_id == int(user_id))
        .filter(UserFundPosition.fund_id == int(fund_id))
        .with_for_update()
        .first()
    )


def _lock_active_user_wallet(
    db: Session,
    *,
    user_id: int,
) -> UserWallet:
    wallet = (
        db.query(UserWallet)
        .filter(UserWallet.user_id == int(user_id))
        .filter(UserWallet.blockchain == "BSC")
        .filter(UserWallet.is_active.is_(True))
        .order_by(UserWallet.id.asc())
        .with_for_update()
        .first()
    )
    if wallet is None:
        raise NegativeFinalizationError(
            f"Active BSC user wallet not found for user_id={user_id}"
        )

    return wallet


def _validate_positions_and_wallets(
    db: Session,
    *,
    fund_id: int,
    buy_orders: list[FundOrder],
    redeem_orders: list[FundOrder],
) -> dict[str, Any]:
    redeem_positions: dict[int, UserFundPosition] = {}
    buy_positions: dict[
        int,
        UserFundPosition | None,
    ] = {}
    buy_wallets: dict[int, UserWallet] = {}

    positions_before: dict[str, Any] = {}
    wallets_before: dict[str, Any] = {}

    for order in redeem_orders:
        position = _lock_position(db, user_id=int(order.user_id), fund_id=int(fund_id))
        if position is None:
            raise NegativeFinalizationError(
                f"Missing user fund position for redeem order {order.id}"
            )

        try:
            validate_position_cost_basis(
                db,
                position=position,
                user_id=int(order.user_id),
                fund_id=int(fund_id),
            )
        except PositionCostBasisError as exc:
            raise NegativeFinalizationError(
                str(exc)
            ) from exc

        redeem_shares = _share_4dp(
            order.shares,
            field_name=(
                f"redeem_order_{order.id}_shares"
            ),
        )
        position_shares = _share_4dp(
            position.shares,
            field_name=(
                f"position_{order.user_id}_{fund_id}"
                "_shares"
            ),
        )
        position_reserved = _share_4dp(
            position.shares_reserved or ZERO,
            field_name=(
                f"position_{order.user_id}_{fund_id}"
                "_shares_reserved"
            ),
        )

        if position_shares < redeem_shares:
            raise NegativeFinalizationError(
                f"Insufficient position shares for redeem order {order.id}"
            )

        if position_reserved < redeem_shares:
            raise NegativeFinalizationError(
                f"Insufficient shares_reserved for redeem order {order.id}"
            )

        redeem_positions[int(order.id)] = position
        positions_before[_position_key(int(order.user_id), int(fund_id))] = {
            "user_id": int(order.user_id),
            "fund_id": int(fund_id),
            "shares": position.shares,
            "shares_reserved": position.shares_reserved,
        }

    for order in buy_orders:
        wallet = _lock_active_user_wallet(db, user_id=int(order.user_id))
        if dec(wallet.usdt_reserved or ZERO) < dec(order.amount_usdt):
            raise NegativeFinalizationError(
                f"Insufficient usdt_reserved for buy order {order.id}"
            )

        position = _lock_position(
            db,
            user_id=int(order.user_id),
            fund_id=int(fund_id),
        )

        try:
            validate_position_cost_basis(
                db,
                position=position,
                user_id=int(order.user_id),
                fund_id=int(fund_id),
            )
        except PositionCostBasisError as exc:
            raise NegativeFinalizationError(
                str(exc)
            ) from exc

        if position is not None:
            _share_4dp(
                position.shares,
                field_name=(
                    f"position_{order.user_id}_{fund_id}"
                    "_shares"
                ),
            )
            _share_4dp(
                position.shares_reserved or ZERO,
                field_name=(
                    f"position_{order.user_id}_{fund_id}"
                    "_shares_reserved"
                ),
            )

        buy_wallets[int(order.id)] = wallet
        buy_positions[int(order.id)] = position

        positions_before[
            _position_key(
                int(order.user_id),
                int(fund_id),
            )
        ] = {
            "user_id": int(order.user_id),
            "fund_id": int(fund_id),
            "shares": (
                position.shares
                if position is not None
                else ZERO
            ),
            "shares_reserved": (
                position.shares_reserved
                if position is not None
                else ZERO
            ),
            "position_existed_before": (
                position is not None
            ),
        }
        wallets_before[_wallet_key(int(wallet.id))] = {
            "user_id": int(order.user_id),
            "wallet_id": int(wallet.id),
            "address": wallet.address,
            "usdt_balance": wallet.usdt_balance,
            "usdt_reserved": wallet.usdt_reserved,
        }

    return {
        "redeem_positions": redeem_positions,
        "buy_positions": buy_positions,
        "buy_wallets": buy_wallets,
        "positions_before": positions_before,
        "user_wallet_reserves_before": wallets_before,
    }


def _validate_share_totals(
    *,
    settlement_batch: FundSettlementBatch,
    fund: Fund,
    total_buy_shares: Decimal,
    total_redeem_shares: Decimal,
) -> dict[str, Decimal]:
    total_buy_shares = _share_4dp(
        total_buy_shares,
        field_name="total_buy_shares",
    )
    total_redeem_shares = _share_4dp(
        total_redeem_shares,
        field_name="total_redeem_shares",
    )
    shares_outstanding_before = _share_4dp(
        settlement_batch.shares_outstanding_before,
        field_name="shares_outstanding_before",
    )

    planned_issue = _share_4dp(
        settlement_batch.planned_shares_to_issue
        or ZERO,
        field_name="planned_shares_to_issue",
    )
    planned_redeem = _share_4dp(
        settlement_batch.planned_shares_to_redeem
        or ZERO,
        field_name="planned_shares_to_redeem",
    )
    planned_net_change = _share_4dp(
        settlement_batch.planned_net_shares_change
        or ZERO,
        field_name="planned_net_shares_change",
        allow_negative=True,
    )

    actual_net_change = _share_4dp(
        total_buy_shares - total_redeem_shares,
        field_name="actual_net_shares_change",
        allow_negative=True,
    )
    shares_outstanding_after = _share_4dp(
        shares_outstanding_before
        + actual_net_change,
        field_name="shares_outstanding_after",
    )
    current_fund_shares = _share_4dp(
        fund.shares_outstanding_current,
        field_name="fund_shares_outstanding_current",
    )

    if planned_issue != total_buy_shares:
        raise NegativeShareQuantityError(
            "Planned shares to issue mismatch"
        )

    if planned_redeem != total_redeem_shares:
        raise NegativeShareQuantityError(
            "Planned shares to redeem mismatch"
        )

    if planned_net_change != actual_net_change:
        raise NegativeShareQuantityError(
            "Planned net shares change mismatch"
        )

    if current_fund_shares != shares_outstanding_before:
        raise NegativeShareQuantityError(
            "Fund shares_outstanding_current mismatch"
        )

    return {
        "shares_outstanding_before": (
            shares_outstanding_before
        ),
        "shares_outstanding_after": (
            shares_outstanding_after
        ),
        "actual_net_shares_change": (
            actual_net_change
        ),
        "planned_net_shares_change": (
            planned_net_change
        ),
    }


def _prepare_accounting_context(
    db: Session,
    *,
    settlement_batch: FundSettlementBatch,
    fund: Fund,
    payout_batch: FundNegativePayoutBatch,
    payout_legs: list[FundNegativePayoutLeg],
) -> dict[str, Any]:
    orders = _load_relevant_orders(db, settlement_batch_id=int(settlement_batch.id))

    if _detect_partial_finalization(orders=orders, existing_finalization=None):
        raise NegativeFinalizationError(
            "Partial finalization detected: some orders are success and some are not"
        )

    buy_orders, redeem_orders = _split_orders(orders)

    redeem_validation = _validate_redeem_orders(
        redeem_orders=redeem_orders,
        payout_batch=payout_batch,
        payout_legs=payout_legs,
    )
    buy_validation = _validate_buy_orders(
        buy_orders=buy_orders,
        settlement_price_usdt=dec(settlement_batch.settlement_price_usdt),
    )

    position_wallet_validation = _validate_positions_and_wallets(
        db,
        fund_id=int(fund.id),
        buy_orders=buy_orders,
        redeem_orders=redeem_orders,
    )

    share_validation = _validate_share_totals(
        settlement_batch=settlement_batch,
        fund=fund,
        total_buy_shares=buy_validation["total_buy_shares"],
        total_redeem_shares=redeem_validation["total_redeem_shares"],
    )

    return {
        "orders": orders,
        "buy_orders": buy_orders,
        "redeem_orders": redeem_orders,
        "redeem_validation": redeem_validation,
        "buy_validation": buy_validation,
        "position_wallet_validation": position_wallet_validation,
        "share_validation": share_validation,
    }


def _positions_after_json(
    *,
    positions_before: dict[str, Any],
    redeem_positions: dict[int, UserFundPosition],
    buy_positions: dict[int, UserFundPosition],
    orders: list[FundOrder],
    fund_id: int,
) -> dict[str, Any]:
    result = dict(positions_before)

    for order in orders:
        position = None
        if int(order.id) in redeem_positions:
            position = redeem_positions[int(order.id)]
        if int(order.id) in buy_positions:
            position = buy_positions[int(order.id)]

        if position is None:
            continue

        result[_position_key(int(order.user_id), int(fund_id))] = {
            "user_id": int(order.user_id),
            "fund_id": int(fund_id),
            "shares": position.shares,
            "shares_reserved": position.shares_reserved,
        }

    return result


def _wallet_reserves_after_json(
    *,
    wallets_before: dict[str, Any],
    buy_wallets: dict[int, UserWallet],
) -> dict[str, Any]:
    result = dict(wallets_before)

    for wallet in buy_wallets.values():
        result[_wallet_key(int(wallet.id))] = {
            "user_id": int(wallet.user_id),
            "wallet_id": int(wallet.id),
            "address": wallet.address,
            "usdt_balance": wallet.usdt_balance,
            "usdt_reserved": wallet.usdt_reserved,
        }

    return result


def _apply_redeem_accounting(
    db: Session,
    *,
    redeem_orders: list[FundOrder],
    redeem_positions: dict[int, UserFundPosition],
    executed_at,
) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []

    for order in redeem_orders:
        position = redeem_positions.get(int(order.id))
        if position is None:
            raise NegativeFinalizationError(
                f"Redeem position not locked for order {order.id}"
            )

        position_shares_before = dec(position.shares)
        position_reserved_before = dec(position.shares_reserved or ZERO)

        redeem_shares = _share_4dp(
            order.shares,
            field_name=(
                f"redeem_order_{order.id}_shares"
            ),
        )

        try:
            apply_redeem_cost_basis(
                db,
                position=position,
                redeem_shares=redeem_shares,
                now=executed_at,
            )
        except PositionCostBasisError as exc:
            raise NegativeFinalizationError(
                str(exc)
            ) from exc

        position.shares = _share_4dp(
            position_shares_before - redeem_shares,
            field_name=(
                f"position_{order.user_id}_shares_after"
            ),
        )
        position.shares_reserved = _share_4dp(
            position_reserved_before
            - redeem_shares,
            field_name=(
                f"position_{order.user_id}"
                "_shares_reserved_after"
            ),
        )

        order_amount_before = order.amount_usdt
        order_price_before = order.price_usdt
        order_status_before = order.status
        order_executed_before = order.executed_at

        order.amount_usdt = dec(order.net_user_payout_usdt)
        order.price_usdt = dec(order.net_price_usdt)
        order.status = ORDER_STATUS_SUCCESS
        order.executed_at = executed_at

        updates.append(
            {
                "order_id": int(order.id),
                "side": ORDER_SIDE_REDEEM,
                "user_id": int(order.user_id),
                "shares": order.shares,
                "amount_usdt_before": order_amount_before,
                "amount_usdt_after": order.amount_usdt,
                "price_usdt_before": order_price_before,
                "price_usdt_after": order.price_usdt,
                "status_before": order_status_before,
                "status_after": order.status,
                "executed_at_before": order_executed_before,
                "executed_at_after": order.executed_at,
                "gross_redeem_usdt": order.gross_redeem_usdt,
                "success_fee_usdt": order.success_fee_usdt,
                "management_fee_usdt": order.management_fee_usdt,
                "partial_month_fee_usdt": order.partial_month_fee_usdt,
                "net_user_payout_usdt": order.net_user_payout_usdt,
                "net_price_usdt": order.net_price_usdt,
                "position_shares_before": position_shares_before,
                "position_shares_after": position.shares,
                "position_shares_reserved_before": position_reserved_before,
                "position_shares_reserved_after": position.shares_reserved,
            }
        )

    return updates


def _apply_buy_accounting(
    db: Session,
    *,
    fund_id: int,
    buy_orders: list[FundOrder],
    buy_positions: dict[
        int,
        UserFundPosition | None,
    ],
    buy_wallets: dict[int, UserWallet],
    computed_shares_by_order_id: dict[
        int,
        Decimal,
    ],
    settlement_price_usdt: Decimal,
    executed_at,
) -> list[dict[str, Any]]:
    updates: list[dict[str, Any]] = []
    created_positions: dict[
        tuple[int, int],
        UserFundPosition,
    ] = {}

    for order in buy_orders:
        position = buy_positions.get(int(order.id))
        wallet = buy_wallets.get(int(order.id))
        buy_shares = computed_shares_by_order_id.get(
            int(order.id)
        )

        if wallet is None:
            raise NegativeFinalizationError(
                f"Buy wallet not locked for order {order.id}"
            )

        if buy_shares is None:
            raise NegativeFinalizationError(
                f"Buy shares not calculated for order {order.id}"
            )

        buy_shares = _share_4dp(
            buy_shares,
            field_name=f"buy_order_{order.id}_shares",
        )

        if position is None:
            position_key = (
                int(order.user_id),
                int(fund_id),
            )
            position = created_positions.get(
                position_key
            )

            if position is None:
                position = UserFundPosition(
                    user_id=int(order.user_id),
                    fund_id=int(fund_id),
                    shares=ZERO,
                    shares_reserved=ZERO,
                )
                db.add(position)
                db.flush()

                created_positions[
                    position_key
                ] = position

        buy_positions[int(order.id)] = position

        wallet_balance_before = dec(wallet.usdt_balance or ZERO)
        wallet_reserved_before = dec(wallet.usdt_reserved or ZERO)
        position_shares_before = dec(position.shares or ZERO)
        position_reserved_before = dec(
            position.shares_reserved or ZERO
        )

        try:
            apply_buy_cost_basis(
                db,
                position=position,
                amount_usdt=dec(order.amount_usdt),
                issued_shares=buy_shares,
                now=executed_at,
            )
        except PositionCostBasisError as exc:
            raise NegativeFinalizationError(
                str(exc)
            ) from exc

        wallet.usdt_reserved = _q10(
            wallet_reserved_before
            - dec(order.amount_usdt)
        )
        position.shares = _share_4dp(
            position_shares_before + buy_shares,
            field_name=(
                f"position_{order.user_id}_shares_after"
            ),
        )

        order_shares_before = order.shares
        order_price_before = order.price_usdt
        order_status_before = order.status
        order_executed_before = order.executed_at

        order.shares = buy_shares
        order.price_usdt = settlement_price_usdt
        order.status = ORDER_STATUS_SUCCESS
        order.executed_at = executed_at

        updates.append(
            {
                "order_id": int(order.id),
                "side": ORDER_SIDE_BUY,
                "user_id": int(order.user_id),
                "amount_usdt": order.amount_usdt,
                "shares_before": order_shares_before,
                "shares_after": order.shares,
                "price_usdt_before": order_price_before,
                "price_usdt_after": order.price_usdt,
                "status_before": order_status_before,
                "status_after": order.status,
                "executed_at_before": order_executed_before,
                "executed_at_after": order.executed_at,
                "wallet_id": int(wallet.id),
                "wallet_usdt_balance_before": wallet_balance_before,
                "wallet_usdt_balance_after": wallet.usdt_balance,
                "wallet_usdt_reserved_before": wallet_reserved_before,
                "wallet_usdt_reserved_after": wallet.usdt_reserved,
                "position_shares_before": position_shares_before,
                "position_shares_after": position.shares,
                "position_shares_reserved_before": position_reserved_before,
                "position_shares_reserved_after": position.shares_reserved,
                "note": "user_wallet.usdt_balance is not double-debited in Stage 23.6",
            }
        )

    return updates


def _pricing_lock_state_evidence(
    *,
    runtime_state: FundRuntimeState,
    settlement_batch: FundSettlementBatch,
) -> dict[str, Any]:
    return {
        "settlement_batch_id": int(
            settlement_batch.id
        ),
        "runtime_fund_id": int(
            runtime_state.fund_id
        ),
        "runtime_pricing_locked": (
            runtime_state.pricing_locked
        ),
        "runtime_pricing_lock_reason": (
            runtime_state.pricing_lock_reason
        ),
        "runtime_pricing_lock_batch_id": (
            int(
                runtime_state
                .pricing_lock_batch_id
            )
            if runtime_state
            .pricing_lock_batch_id
            is not None
            else None
        ),
        "runtime_pricing_locked_at": (
            runtime_state.pricing_locked_at
        ),
        "runtime_pricing_unlocked_at": (
            runtime_state.pricing_unlocked_at
        ),
        "settlement_pricing_locked_at": (
            settlement_batch.pricing_locked_at
        ),
        "settlement_pricing_unlocked_at": (
            settlement_batch
            .pricing_unlocked_at
        ),
    }


def _validate_pricing_lock_ownership(
    *,
    runtime_state: (
        FundRuntimeState | None
    ),
    settlement_batch: (
        FundSettlementBatch
    ),
) -> dict[str, Any]:
    if runtime_state is None:
        raise NegativeFinalizationError(
            "Fund runtime state is missing; "
            "pricing lock ownership cannot "
            "be proven"
        )

    if (
        runtime_state.pricing_locked
        is not True
    ):
        raise NegativeFinalizationError(
            "Fund pricing lock is not actively "
            "locked for finalization"
        )

    lock_reason = str(
        runtime_state.pricing_lock_reason
        or ""
    ).strip()

    if lock_reason != (
        PRICING_LOCK_REASON_SETTLEMENT
    ):
        raise NegativeFinalizationError(
            "Fund pricing lock reason mismatch: "
            f"observed={lock_reason or 'empty'}"
        )

    owner_value = (
        runtime_state.pricing_lock_batch_id
    )

    if owner_value is None:
        raise NegativeFinalizationError(
            "Fund pricing lock has no settlement "
            "batch owner"
        )

    try:
        owner_batch_id = int(owner_value)
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise NegativeFinalizationError(
            "Fund pricing lock batch owner "
            "is invalid"
        ) from exc

    if owner_batch_id != int(
        settlement_batch.id
    ):
        raise NegativeFinalizationError(
            "Fund pricing lock is owned by another "
            "settlement batch: "
            f"owner={owner_batch_id}, "
            f"current={settlement_batch.id}"
        )

    if (
        runtime_state.pricing_locked_at
        is None
    ):
        raise NegativeFinalizationError(
            "Fund runtime pricing_locked_at "
            "is missing"
        )

    if (
        settlement_batch.pricing_locked_at
        is None
    ):
        raise NegativeFinalizationError(
            "Settlement pricing_locked_at "
            "is missing"
        )

    if (
        runtime_state.pricing_unlocked_at
        is not None
    ):
        raise NegativeFinalizationError(
            "Fund runtime pricing lock is already "
            "marked unlocked"
        )

    if (
        settlement_batch.pricing_unlocked_at
        is not None
    ):
        raise NegativeFinalizationError(
            "Settlement pricing lock is already "
            "marked unlocked"
        )

    if int(runtime_state.fund_id) != int(
        settlement_batch.fund_id
    ):
        raise NegativeFinalizationError(
            "Fund runtime pricing lock fund "
            "does not match settlement fund"
        )

    return _pricing_lock_state_evidence(
        runtime_state=runtime_state,
        settlement_batch=settlement_batch,
    )


def _release_pricing_lock(
    *,
    runtime_state: FundRuntimeState | None,
    settlement_batch: FundSettlementBatch,
    unlock_ts,
    validated_ownership: (
        dict[str, Any] | None
    ) = None,
) -> dict[str, Any]:
    current_ownership = (
        _validate_pricing_lock_ownership(
            runtime_state=runtime_state,
            settlement_batch=(
                settlement_batch
            ),
        )
    )

    if (
        validated_ownership is not None
        and current_ownership
        != validated_ownership
    ):
        raise NegativeFinalizationError(
            "Pricing lock ownership changed "
            "before release"
        )

    before = {
        "runtime_found": runtime_state is not None,
        "settlement_pricing_locked_at": settlement_batch.pricing_locked_at,
        "settlement_pricing_unlocked_at": settlement_batch.pricing_unlocked_at,
    }

    if runtime_state is not None:
        before.update(
            {
                "runtime_pricing_locked": getattr(runtime_state, "pricing_locked", None),
                "runtime_pricing_lock_reason": getattr(runtime_state, "pricing_lock_reason", None),
                "runtime_pricing_lock_batch_id": getattr(runtime_state, "pricing_lock_batch_id", None),
                "runtime_pricing_locked_at": getattr(runtime_state, "pricing_locked_at", None),
                "runtime_pricing_unlocked_at": getattr(runtime_state, "pricing_unlocked_at", None),
            }
        )

        if hasattr(runtime_state, "pricing_locked"):
            runtime_state.pricing_locked = False
        if hasattr(runtime_state, "pricing_lock_reason"):
            runtime_state.pricing_lock_reason = None
        if hasattr(runtime_state, "pricing_lock_batch_id"):
            runtime_state.pricing_lock_batch_id = None
        if hasattr(runtime_state, "pricing_unlocked_at"):
            runtime_state.pricing_unlocked_at = unlock_ts
        if hasattr(runtime_state, "updated_at"):
            runtime_state.updated_at = unlock_ts

    settlement_batch.pricing_unlocked_at = unlock_ts

    after = {
        "runtime_found": runtime_state is not None,
        "settlement_pricing_locked_at": settlement_batch.pricing_locked_at,
        "settlement_pricing_unlocked_at": settlement_batch.pricing_unlocked_at,
    }

    if runtime_state is not None:
        after.update(
            {
                "runtime_pricing_locked": getattr(runtime_state, "pricing_locked", None),
                "runtime_pricing_lock_reason": getattr(runtime_state, "pricing_lock_reason", None),
                "runtime_pricing_lock_batch_id": getattr(runtime_state, "pricing_lock_batch_id", None),
                "runtime_pricing_locked_at": getattr(runtime_state, "pricing_locked_at", None),
                "runtime_pricing_unlocked_at": getattr(runtime_state, "pricing_unlocked_at", None),
            }
        )

    return {
        "ownership": current_ownership,
        "before": before,
        "after": after,
        "unlock_ts": unlock_ts,
    }


def _apply_accounting_finalization(
    db: Session,
    *,
    finalization: FundNegativeFinalizationBatch,
    settlement_batch: FundSettlementBatch,
    fund: Fund,
    runtime_state: FundRuntimeState | None,
    context: dict[str, Any],
    now,
) -> None:
    if not settings.NEGATIVE_NET_FINALIZATION_UNLOCK_PRICING:
        raise NegativeFinalizationError(
            "NEGATIVE_NET_FINALIZATION_UNLOCK_PRICING must be true for Stage 23.6"
        )

    pricing_lock_ownership = (
        _validate_pricing_lock_ownership(
            runtime_state=runtime_state,
            settlement_batch=(
                settlement_batch
            ),
        )
    )

    buy_orders = context["buy_orders"]
    redeem_orders = context["redeem_orders"]
    buy_validation = context["buy_validation"]
    redeem_validation = context["redeem_validation"]
    position_wallet_validation = context["position_wallet_validation"]
    share_validation = context["share_validation"]

    finalization.status = FINALIZATION_BATCH_STATUS_ACCOUNTING_PROCESSING
    finalization.updated_at = now

    redeem_updates = _apply_redeem_accounting(
        db,
        redeem_orders=redeem_orders,
        redeem_positions=position_wallet_validation["redeem_positions"],
        executed_at=now,
    )
    buy_updates = _apply_buy_accounting(
        db,
        fund_id=int(fund.id),
        buy_orders=buy_orders,
        buy_positions=(
            position_wallet_validation[
                "buy_positions"
            ]
        ),
        buy_wallets=(
            position_wallet_validation[
                "buy_wallets"
            ]
        ),
        computed_shares_by_order_id=(
            buy_validation[
                "computed_shares_by_order_id"
            ]
        ),
        settlement_price_usdt=dec(
            settlement_batch.settlement_price_usdt
        ),
        executed_at=now,
    )

    fund_shares_before = dec(fund.shares_outstanding_current)
    fund.shares_outstanding_current = share_validation["shares_outstanding_after"]

    finalization.order_updates_json = _json_dict(
        {
            "redeem_updates": redeem_updates,
            "buy_updates": buy_updates,
            "executed_at": now,
        }
    )
    finalization.positions_after_json = _json_dict(
        _positions_after_json(
            positions_before=position_wallet_validation["positions_before"],
            redeem_positions=position_wallet_validation["redeem_positions"],
            buy_positions=position_wallet_validation["buy_positions"],
            orders=context["orders"],
            fund_id=int(fund.id),
        )
    )
    finalization.user_wallet_reserves_after_json = _json_dict(
        _wallet_reserves_after_json(
            wallets_before=position_wallet_validation["user_wallet_reserves_before"],
            buy_wallets=position_wallet_validation["buy_wallets"],
        )
    )
    finalization.fund_update_json = _json_dict(
        {
            "fund_id": int(fund.id),
            "fund_code": fund.code,
            "shares_outstanding_current_before": fund_shares_before,
            "shares_outstanding_current_after": fund.shares_outstanding_current,
            "shares_outstanding_before_from_settlement": share_validation[
                "shares_outstanding_before"
            ],
            "shares_outstanding_after": share_validation["shares_outstanding_after"],
            "actual_net_shares_change": share_validation["actual_net_shares_change"],
        }
    )

    finalization.accounting_finalized_at = now
    finalization.status = FINALIZATION_BATCH_STATUS_ACCOUNTING_FINALIZED

    settlement_batch.accounting_finalized_at = now
    settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_ACCOUNTING_FINALIZED
    settlement_batch.updated_at = now

    pricing_lock_json = _release_pricing_lock(
        runtime_state=runtime_state,
        settlement_batch=settlement_batch,
        unlock_ts=now,
        validated_ownership=(
            pricing_lock_ownership
        ),
    )

    finalization.pricing_lock_json = _json_dict(pricing_lock_json)
    finalization.pricing_unlocked_at = now
    finalization.status = FINALIZATION_BATCH_STATUS_PRICING_UNLOCKED

    settlement_batch.pricing_unlocked_at = now

    finalization.success_order_count = len(context["orders"])
    finalization.status = FINALIZATION_BATCH_STATUS_COMPLETED
    finalization.completed_at = now
    finalization.updated_at = now

    settlement_batch.status = BATCH_STATUS_NEGATIVE_CASH_SETTLEMENT_COMPLETED
    settlement_batch.updated_at = now

    finalization.accounting_json = _json_dict(
        {
            "buy_order_count": len(buy_orders),
            "redeem_order_count": len(redeem_orders),
            "success_order_count": len(context["orders"]),
            "total_buy_usdt": buy_validation["total_buy_usdt"],
            "total_buy_shares": buy_validation["total_buy_shares"],
            "total_redeem_shares": redeem_validation["total_redeem_shares"],
            "actual_net_shares_change": share_validation["actual_net_shares_change"],
            "shares_outstanding_before": share_validation["shares_outstanding_before"],
            "shares_outstanding_after": share_validation["shares_outstanding_after"],
            "total_net_user_payout_usdt": redeem_validation[
                "total_net_user_payout_usdt"
            ],
            "total_partial_month_fee_usdt": redeem_validation[
                "total_partial_month_fee_usdt"
            ],
            "accounting_finalized_at": now,
            "pricing_unlocked_at": now,
            "orders_executed_at_equals_pricing_unlocked_at": True,
            "buy_user_wallet_usdt_balance_not_double_debited": True,
        }
    )

    finalization.reconciliation_json = _json_dict(
        {
            "ok": True,
            "payout_total_matches_redeem_orders": True,
            "payout_legs_cover_all_redeem_orders": True,
            "planned_net_shares_change_matches_actual": True,
            "fund_shares_outstanding_current_updated": True,
            "settlement_accounting_finalized_at_set": True,
            "settlement_pricing_unlocked_at_set": True,
            "order_executed_at_equals_pricing_unlocked_at": True,
            "settlement_status": settlement_batch.status,
            "no_real_bybit_calls": True,
            "no_real_bsc_calls": True,
            "no_payout_transfers": True,
            "no_nav_chart_writes": True,
            "no_server_deploy": True,
        }
    )

    finalization.report_json = _json_dict(
        {
            "stage": "23.6",
            "ok": True,
            "fund_id": int(fund.id),
            "fund_code": fund.code,
            "settlement_batch_id": int(settlement_batch.id),
            "finalization_batch_id": int(finalization.id),
            "buy_order_count": len(buy_orders),
            "redeem_order_count": len(redeem_orders),
            "success_order_count": len(context["orders"]),
            "total_buy_usdt": buy_validation["total_buy_usdt"],
            "total_buy_shares": buy_validation["total_buy_shares"],
            "total_redeem_shares": redeem_validation["total_redeem_shares"],
            "net_shares_change": share_validation["actual_net_shares_change"],
            "shares_outstanding_before": share_validation["shares_outstanding_before"],
            "shares_outstanding_after": share_validation["shares_outstanding_after"],
            "total_payout_usdt": redeem_validation["total_net_user_payout_usdt"],
            "total_partial_month_fee_usdt": redeem_validation[
                "total_partial_month_fee_usdt"
            ],
            "pricing_unlock_timestamp": now,
            "final_settlement_status": settlement_batch.status,
        }
    )

    db.flush()


def finalize_negative_net_settlement(
    db: Session,
    *,
    settlement_batch_id: int,
    now=None,
) -> NegativeFinalizationResult:
    if not settings.NEGATIVE_NET_FINALIZATION_ENABLED:
        raise NegativeFinalizationError("Negative-net finalization is disabled")

    now = _now_or_supplied(now)

    settlement_batch = _lock_settlement_batch(
        db,
        settlement_batch_id=int(settlement_batch_id),
    )
    settlement_status_before = str(settlement_batch.status)

    fund = _lock_fund(db, fund_id=int(settlement_batch.fund_id))
    sale_batch = _lock_sale_batch(db, settlement_batch_id=int(settlement_batch.id))
    bybit_flow = _lock_bybit_flow(db, settlement_batch_id=int(settlement_batch.id))
    payout_batch = _lock_payout_batch(db, settlement_batch_id=int(settlement_batch.id))
    payout_legs = _lock_payout_legs(db, payout_batch_id=int(payout_batch.id))
    bsc_intents = _lock_bsc_intents(
        db,
        settlement_batch_id=int(
            settlement_batch.id
        ),
    )
    existing_finalization = _lock_existing_finalization(
        db,
        settlement_batch_id=int(settlement_batch.id),
    )

    status_before = (
        str(existing_finalization.status) if existing_finalization is not None else None
    )

    try:
        input_validation = (
            _validate_input_state(
                settlement_batch=(
                    settlement_batch
                ),
                sale_batch=sale_batch,
                bybit_flow=bybit_flow,
                payout_batch=payout_batch,
                payout_legs=payout_legs,
                bsc_intents=bsc_intents,
                existing_finalization=(
                    existing_finalization
                ),
            )
        )

        if (
            existing_finalization is not None
            and existing_finalization.status == FINALIZATION_BATCH_STATUS_COMPLETED
            and settlement_batch.status == BATCH_STATUS_NEGATIVE_CASH_SETTLEMENT_COMPLETED
        ):
            return _result_from_completed(
                finalization=existing_finalization,
                settlement_batch=settlement_batch,
                fund=fund,
                status_before=status_before,
                settlement_status_before=settlement_status_before,
                idempotent=True,
            )

        finalization = _new_or_existing_finalization(
            db,
            existing=existing_finalization,
            settlement_batch=settlement_batch,
            payout_batch=payout_batch,
            bybit_flow=bybit_flow,
            sale_batch=sale_batch,
            fund=fund,
            now=now,
        )
        status_before = str(finalization.status)

        finalization.status = FINALIZATION_BATCH_STATUS_VALIDATING
        finalization.settlement_price_usdt = dec(settlement_batch.settlement_price_usdt)
        finalization.shares_outstanding_before = dec(
            settlement_batch.shares_outstanding_before
        )
        finalization.payout_batch_id = int(payout_batch.id)
        finalization.bybit_flow_id = int(bybit_flow.id)
        finalization.sale_batch_id = int(sale_batch.id)
        finalization.fund_id = int(fund.id)
        finalization.finalization_started_at = now
        finalization.updated_at = now
        finalization.validation_json = _json_dict(
            {
                "stage": "23.6",
                "settlement_status": settlement_batch.status,
                "sale_batch_status": sale_batch.status,
                "bybit_flow_status": bybit_flow.status,
                "payout_batch_status": payout_batch.status,
                "payout_leg_count": len(payout_legs),
                "bybit_cash_delivery": (
                    input_validation[
                        "bybit_cash_delivery"
                    ]
                ),
                "bsc_delivery": (
                    input_validation[
                        "bsc_delivery"
                    ]
                ),
                "balance_refresh": (
                    input_validation[
                        "balance_refresh"
                    ]
                ),
                "pricing_locked_at": settlement_batch.pricing_locked_at,
                "pricing_unlocked_at": settlement_batch.pricing_unlocked_at,
                "no_real_bybit_calls": True,
                "no_real_bsc_calls": True,
                "no_payout_transfers": True,
                "no_nav_chart_writes": True,
            }
        )

        locked_payout_wallets = (
            _lock_payout_user_wallets(
                db,
                payout_legs=payout_legs,
            )
        )

        user_wallet_db_gate = (
            _validate_payout_user_wallet_db_gate(
                payout_batch=payout_batch,
                payout_legs=payout_legs,
                locked_wallets=(
                    locked_payout_wallets
                ),
                balance_refresh_validation=(
                    input_validation[
                        "balance_refresh"
                    ]
                ),
            )
        )

        finalization.validation_json = (
            _json_dict(
                {
                    **(
                        finalization
                        .validation_json
                        or {}
                    ),
                    "user_wallet_db_gate": (
                        user_wallet_db_gate
                    ),
                }
            )
        )
        finalization.updated_at = now

        context = _prepare_accounting_context(
            db,
            settlement_batch=settlement_batch,
            fund=fund,
            payout_batch=payout_batch,
            payout_legs=payout_legs,
        )

        finalization.buy_order_count = len(context["buy_orders"])
        finalization.redeem_order_count = len(context["redeem_orders"])
        finalization.success_order_count = 0
        finalization.total_buy_usdt = context["buy_validation"]["total_buy_usdt"]
        finalization.total_buy_shares = context["buy_validation"]["total_buy_shares"]
        finalization.total_redeem_shares = context["redeem_validation"]["total_redeem_shares"]
        finalization.planned_net_shares_change = context["share_validation"][
            "planned_net_shares_change"
        ]
        finalization.actual_net_shares_change = context["share_validation"][
            "actual_net_shares_change"
        ]
        finalization.shares_outstanding_after = context["share_validation"][
            "shares_outstanding_after"
        ]
        finalization.total_net_user_payout_usdt = context["redeem_validation"][
            "total_net_user_payout_usdt"
        ]
        finalization.total_partial_month_fee_usdt = context["redeem_validation"][
            "total_partial_month_fee_usdt"
        ]
        finalization.positions_before_json = _json_dict(
            context["position_wallet_validation"]["positions_before"]
        )
        finalization.user_wallet_reserves_before_json = _json_dict(
            context["position_wallet_validation"]["user_wallet_reserves_before"]
        )
        finalization.validation_json = _json_dict(
            {
                **(finalization.validation_json or {}),
                "orders_loaded": len(context["orders"]),
                "buy_order_count": len(context["buy_orders"]),
                "redeem_order_count": len(context["redeem_orders"]),
                "redeem_validation": context["redeem_validation"],
                "buy_validation": {
                    "total_buy_usdt": context["buy_validation"]["total_buy_usdt"],
                    "total_buy_shares": context["buy_validation"]["total_buy_shares"],
                    "computed_shares_by_order_id": context["buy_validation"][
                        "computed_shares_by_order_id"
                    ],
                },
                "share_validation": context["share_validation"],
            }
        )
        finalization.updated_at = now

        runtime_state = _lock_runtime_state(db, fund_id=int(fund.id))

        _apply_accounting_finalization(
            db,
            finalization=finalization,
            settlement_batch=settlement_batch,
            fund=fund,
            runtime_state=runtime_state,
            context=context,
            now=now,
        )

        return NegativeFinalizationResult(
            ok=True,
            finalization_batch_id=int(finalization.id),
            settlement_batch_id=int(settlement_batch.id),
            payout_batch_id=int(payout_batch.id),
            fund_id=int(fund.id),
            fund_code=str(fund.code),
            status_before=status_before,
            status_after=finalization.status,
            settlement_status_before=settlement_status_before,
            settlement_status_after=settlement_batch.status,
            buy_order_count=finalization.buy_order_count,
            redeem_order_count=finalization.redeem_order_count,
            success_order_count=finalization.success_order_count,
            shares_outstanding_before=str(finalization.shares_outstanding_before),
            shares_outstanding_after=str(finalization.shares_outstanding_after),
            total_buy_usdt=str(finalization.total_buy_usdt),
            total_buy_shares=str(finalization.total_buy_shares),
            total_redeem_shares=str(finalization.total_redeem_shares),
            planned_net_shares_change=str(finalization.planned_net_shares_change),
            actual_net_shares_change=str(finalization.actual_net_shares_change),
            total_net_user_payout_usdt=str(finalization.total_net_user_payout_usdt),
            total_partial_month_fee_usdt=str(finalization.total_partial_month_fee_usdt),
            accounting_finalized_at=finalization.accounting_finalized_at.isoformat(),
            pricing_unlocked_at=finalization.pricing_unlocked_at.isoformat(),
            diagnostics={
                "finalized": True,
                "no_real_bybit_calls": True,
                "no_real_bsc_calls": True,
                "no_payout_transfers": True,
                "no_nav_chart_writes": True,
                "no_server_deploy": True,
            },
        )

    except NegativeShareQuantityError as exc:
        if (
            "finalization" not in locals()
            or finalization is None
        ):
            raise

        _mark_share_failed_orders(
            db,
            settlement_batch_id=int(
                settlement_batch.id
            ),
            error=str(exc),
        )

        return _set_failed(
            finalization=finalization,
            settlement_batch=settlement_batch,
            fund=fund,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            error=str(exc),
            now=now,
            diagnostics={
                "share_quantity_failure": True,
                "share_quantum": "0.0001",
                "rounding_mode": "ROUND_DOWN",
            },
        )

    except NegativeFinalizationError as exc:
        if "finalization" not in locals() or finalization is None:
            raise

        return _set_failed(
            finalization=finalization,
            settlement_batch=settlement_batch,
            fund=fund,
            status_before=status_before,
            settlement_status_before=settlement_status_before,
            error=str(exc),
            now=now,
        )