from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import (
    FundNegativeBybitFlow,
    FundNegativeFinalizationBatch,
    FundNegativePayoutBatch,
    FundNegativePayoutLeg,
    FundNegativeSaleBatch,
    FundNegativeSaleLeg,
    FundOrder,
    FundSettlementBatch,
    FundSettlementTransfer,
)


ZERO = Decimal("0")


class NegativeExternalStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class NegativeExternalState:
    settlement_batch_id: int
    safe_to_release_reserves: bool
    safe_to_unlock_pricing: bool
    accounting_finalized: bool
    sale_action_detected: bool
    earn_action_detected: bool
    universal_transfer_action_detected: bool
    withdrawal_action_detected: bool
    payout_action_detected: bool
    gas_topup_action_detected: bool
    other_external_action_detected: bool
    reasons: tuple[str, ...]
    evidence: tuple[dict[str, Any], ...]


def _dec(value: Any) -> Decimal:
    if value is None:
        return ZERO

    if isinstance(value, Decimal):
        return value

    try:
        return Decimal(str(value))
    except Exception:
        return ZERO


def _present(value: Any) -> bool:
    if value is None:
        return False

    if isinstance(value, str):
        return bool(value.strip())

    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)

    return True


def _audit_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, (dict, list, tuple, set)):
        return "present"

    text = str(value)

    if len(text) > 256:
        return f"{text[:256]}..."

    return text


def _is_earn_leg(leg: FundNegativeSaleLeg) -> bool:
    text = " ".join(
        [
            str(leg.leg_group or ""),
            str(leg.leg_type or ""),
            str(leg.actual_execution_mode or ""),
            str(leg.planned_execution_mode or ""),
        ]
    ).lower()

    return "earn" in text


def inspect_negative_external_state(
    db: Session,
    *,
    settlement_batch_id: int,
) -> NegativeExternalState:
    batch = (
        db.query(FundSettlementBatch)
        .filter(
            FundSettlementBatch.id
            == int(settlement_batch_id)
        )
        .with_for_update()
        .first()
    )

    if batch is None:
        raise NegativeExternalStateError(
            "Settlement batch not found: "
            f"settlement_batch_id={settlement_batch_id}"
        )

    orders = (
        db.query(FundOrder)
        .filter(
            FundOrder.settlement_batch_id
            == int(settlement_batch_id)
        )
        .order_by(FundOrder.id.asc())
        .with_for_update()
        .all()
    )

    sale_batch = (
        db.query(FundNegativeSaleBatch)
        .filter(
            FundNegativeSaleBatch.settlement_batch_id
            == int(settlement_batch_id)
        )
        .with_for_update()
        .first()
    )

    sale_legs = (
        db.query(FundNegativeSaleLeg)
        .filter(
            FundNegativeSaleLeg.settlement_batch_id
            == int(settlement_batch_id)
        )
        .order_by(
            FundNegativeSaleLeg.leg_index.asc(),
            FundNegativeSaleLeg.id.asc(),
        )
        .with_for_update()
        .all()
    )

    bybit_flow = (
        db.query(FundNegativeBybitFlow)
        .filter(
            FundNegativeBybitFlow.settlement_batch_id
            == int(settlement_batch_id)
        )
        .with_for_update()
        .first()
    )

    payout_batch = (
        db.query(FundNegativePayoutBatch)
        .filter(
            FundNegativePayoutBatch.settlement_batch_id
            == int(settlement_batch_id)
        )
        .with_for_update()
        .first()
    )

    payout_legs = (
        db.query(FundNegativePayoutLeg)
        .filter(
            FundNegativePayoutLeg.settlement_batch_id
            == int(settlement_batch_id)
        )
        .order_by(FundNegativePayoutLeg.id.asc())
        .with_for_update()
        .all()
    )

    finalization = (
        db.query(FundNegativeFinalizationBatch)
        .filter(
            FundNegativeFinalizationBatch.settlement_batch_id
            == int(settlement_batch_id)
        )
        .with_for_update()
        .first()
    )

    transfers = (
        db.query(FundSettlementTransfer)
        .filter(
            FundSettlementTransfer.batch_id
            == int(settlement_batch_id)
        )
        .order_by(FundSettlementTransfer.id.asc())
        .with_for_update()
        .all()
    )

    flags = {
        "accounting": False,
        "sale": False,
        "earn": False,
        "universal_transfer": False,
        "withdrawal": False,
        "payout": False,
        "gas_topup": False,
        "other": False,
    }

    reasons: list[str] = []
    evidence: list[dict[str, Any]] = []

    def add_evidence(
        *,
        action: str,
        model: str,
        row_id: int | None,
        field: str,
        value: Any,
        reason: str,
    ) -> None:
        flags[action] = True

        if reason not in reasons:
            reasons.append(reason)

        evidence_item = {
            "action": action,
            "model": model,
            "row_id": row_id,
            "field": field,
            "value": _audit_value(value),
        }

        if evidence_item not in evidence:
            evidence.append(evidence_item)

    if batch.accounting_finalized_at is not None:
        add_evidence(
            action="accounting",
            model="FundSettlementBatch",
            row_id=int(batch.id),
            field="accounting_finalized_at",
            value=batch.accounting_finalized_at,
            reason="settlement_batch_accounting_finalized",
        )

    batch_external_fields = (
        "seller_payouts_completed_at",
        "bybit_deposit_tx_hash",
        "bybit_deposit_confirmed_at",
        "bybit_internal_transfer_id",
        "bybit_internal_transfer_completed_at",
        "bybit_internal_transfer_status",
    )

    for field_name in batch_external_fields:
        value = getattr(
            batch,
            field_name,
            None,
        )

        if _present(value):
            add_evidence(
                action="other",
                model="FundSettlementBatch",
                row_id=int(batch.id),
                field=field_name,
                value=value,
                reason=(
                    "unexpected_settlement_batch_external_evidence:"
                    f"{field_name}"
                ),
            )

    for order in orders:
        if order.executed_at is not None:
            add_evidence(
                action="accounting",
                model="FundOrder",
                row_id=int(order.id),
                field="executed_at",
                value=order.executed_at,
                reason=f"order_{order.id}_executed",
            )

        if str(order.status or "").strip().lower() == "success":
            add_evidence(
                action="accounting",
                model="FundOrder",
                row_id=int(order.id),
                field="status",
                value=order.status,
                reason=f"order_{order.id}_success",
            )

        if order.collection_confirmed_at is not None:
            add_evidence(
                action="other",
                model="FundOrder",
                row_id=int(order.id),
                field="collection_confirmed_at",
                value=order.collection_confirmed_at,
                reason=(
                    f"order_{order.id}_buy_collection_confirmed"
                ),
            )

    if sale_batch is not None:
        sale_batch_fields = (
            "execution_started_at",
            "execution_completed_at",
        )

        for field_name in sale_batch_fields:
            value = getattr(
                sale_batch,
                field_name,
                None,
            )

            if _present(value):
                add_evidence(
                    action="sale",
                    model="FundNegativeSaleBatch",
                    row_id=int(sale_batch.id),
                    field=field_name,
                    value=value,
                    reason=(
                        "sale_batch_execution_evidence:"
                        f"{field_name}"
                    ),
                )

        if _dec(
            sale_batch.usdt_earn_redeemed_usdt
        ) > ZERO:
            add_evidence(
                action="earn",
                model="FundNegativeSaleBatch",
                row_id=int(sale_batch.id),
                field="usdt_earn_redeemed_usdt",
                value=(
                    sale_batch.usdt_earn_redeemed_usdt
                ),
                reason="sale_batch_earn_redeemed",
            )

        for field_name in (
            "initial_sale_executed_usdt",
            "extra_sale_executed_usdt",
        ):
            value = getattr(
                sale_batch,
                field_name,
                None,
            )

            if _dec(value) > ZERO:
                add_evidence(
                    action="sale",
                    model="FundNegativeSaleBatch",
                    row_id=int(sale_batch.id),
                    field=field_name,
                    value=value,
                    reason=(
                        "sale_batch_executed_amount:"
                        f"{field_name}"
                    ),
                )

        for field_name in (
            "execution_json",
            "reconciliation_json",
        ):
            value = getattr(
                sale_batch,
                field_name,
                None,
            )

            if _present(value):
                add_evidence(
                    action="sale",
                    model="FundNegativeSaleBatch",
                    row_id=int(sale_batch.id),
                    field=field_name,
                    value=value,
                    reason=(
                        "sale_batch_execution_json_present:"
                        f"{field_name}"
                    ),
                )

        safe_sale_batch_statuses = {
            "snapshot_created",
            "sale_plan_created",
            "sale_plan_failed_requires_review",
        }

        sale_batch_status = str(
            sale_batch.status or ""
        ).strip().lower()

        if (
            sale_batch_status
            and sale_batch_status
            not in safe_sale_batch_statuses
        ):
            add_evidence(
                action="sale",
                model="FundNegativeSaleBatch",
                row_id=int(sale_batch.id),
                field="status",
                value=sale_batch.status,
                reason=(
                    "sale_batch_status_not_proven_pre_external:"
                    f"{sale_batch_status}"
                ),
            )

    safe_sale_leg_statuses = {
        "planned",
        "skipped_zero_value",
        "skipped_not_eligible",
        "skipped_min_order",
        "skipped_symbol_not_trading",
        "skipped_liquidity_guard",
        "skipped_margin_guard",
        "cash_available",
        "buffer_available",
        "extra_sale_planned",
        "failed_requires_review",
    }

    for leg in sale_legs:
        action = (
            "earn"
            if _is_earn_leg(leg)
            else "sale"
        )

        for field_name in (
            "order_link_id",
            "bybit_order_id",
            "bybit_strategy_id",
            "sent_at",
            "confirmed_at",
        ):
            value = getattr(
                leg,
                field_name,
                None,
            )

            if _present(value):
                add_evidence(
                    action=action,
                    model="FundNegativeSaleLeg",
                    row_id=int(leg.id),
                    field=field_name,
                    value=value,
                    reason=(
                        f"sale_leg_{leg.id}_external_evidence:"
                        f"{field_name}"
                    ),
                )

        for field_name in (
            "filled_qty",
            "filled_usdt",
            "cash_delta_usdt",
        ):
            value = getattr(
                leg,
                field_name,
                None,
            )

            if _dec(value) > ZERO:
                add_evidence(
                    action=action,
                    model="FundNegativeSaleLeg",
                    row_id=int(leg.id),
                    field=field_name,
                    value=value,
                    reason=(
                        f"sale_leg_{leg.id}_positive_execution_value:"
                        f"{field_name}"
                    ),
                )

        if _present(leg.suborders_json):
            add_evidence(
                action=action,
                model="FundNegativeSaleLeg",
                row_id=int(leg.id),
                field="suborders_json",
                value=leg.suborders_json,
                reason=(
                    f"sale_leg_{leg.id}_suborders_present"
                ),
            )

        leg_status = str(
            leg.status or ""
        ).strip().lower()

        if (
            leg_status
            and leg_status
            not in safe_sale_leg_statuses
        ):
            add_evidence(
                action=action,
                model="FundNegativeSaleLeg",
                row_id=int(leg.id),
                field="status",
                value=leg.status,
                reason=(
                    f"sale_leg_{leg.id}_status_not_proven_pre_external:"
                    f"{leg_status}"
                ),
            )

    if bybit_flow is not None:
        universal_fields = (
            "universal_transfer_id",
            "universal_transfer_status",
            "universal_transfer_created_at",
            "universal_transfer_confirmed_at",
            "universal_transfer_reconciliation_json",
        )

        for field_name in universal_fields:
            value = getattr(
                bybit_flow,
                field_name,
                None,
            )

            if _present(value):
                add_evidence(
                    action="universal_transfer",
                    model="FundNegativeBybitFlow",
                    row_id=int(bybit_flow.id),
                    field=field_name,
                    value=value,
                    reason=(
                        "universal_transfer_evidence:"
                        f"{field_name}"
                    ),
                )

        withdrawal_attempt_fields = (
            "withdrawal_id",
            "withdrawal_status",
            "withdrawal_tx_hash",
            "withdrawal_created_at",
            "withdrawal_confirmed_at",
            "withdrawal_record_json",
            "withdrawal_reconciliation_json",
        )

        withdrawal_attempt_detected = any(
            _present(
                getattr(
                    bybit_flow,
                    field_name,
                    None,
                )
            )
            for field_name
            in withdrawal_attempt_fields
        )

        if (
            _present(
                bybit_flow.withdrawal_request_id
            )
            and withdrawal_attempt_detected
        ):
            add_evidence(
                action="withdrawal",
                model="FundNegativeBybitFlow",
                row_id=int(bybit_flow.id),
                field="withdrawal_request_id",
                value=(
                    bybit_flow.withdrawal_request_id
                ),
                reason=(
                    "withdrawal_request_with_attempt_evidence"
                ),
            )

        for field_name in withdrawal_attempt_fields:
            value = getattr(
                bybit_flow,
                field_name,
                None,
            )

            if _present(value):
                add_evidence(
                    action="withdrawal",
                    model="FundNegativeBybitFlow",
                    row_id=int(bybit_flow.id),
                    field=field_name,
                    value=value,
                    reason=(
                        "withdrawal_evidence:"
                        f"{field_name}"
                    ),
                )

        for field_name in (
            "settlement_wallet_receipt_tx_hash",
            "settlement_wallet_receipt_confirmed_at",
        ):
            value = getattr(
                bybit_flow,
                field_name,
                None,
            )

            if _present(value):
                add_evidence(
                    action="withdrawal",
                    model="FundNegativeBybitFlow",
                    row_id=int(bybit_flow.id),
                    field=field_name,
                    value=value,
                    reason=(
                        "settlement_wallet_receipt_evidence:"
                        f"{field_name}"
                    ),
                )

        safe_bybit_flow_statuses = {
            "created",
            "preflight_passed",
            "preflight_failed_requires_review",
        }

        bybit_status = str(
            bybit_flow.status or ""
        ).strip().lower()

        if (
            bybit_status
            and bybit_status
            not in safe_bybit_flow_statuses
        ):
            add_evidence(
                action="other",
                model="FundNegativeBybitFlow",
                row_id=int(bybit_flow.id),
                field="status",
                value=bybit_flow.status,
                reason=(
                    "bybit_flow_status_not_proven_pre_external:"
                    f"{bybit_status}"
                ),
            )

    if payout_batch is not None:
        if _present(
            payout_batch.gas_topup_tx_hash
        ):
            add_evidence(
                action="gas_topup",
                model="FundNegativePayoutBatch",
                row_id=int(payout_batch.id),
                field="gas_topup_tx_hash",
                value=payout_batch.gas_topup_tx_hash,
                reason="gas_topup_tx_hash_present",
            )

        for field_name in (
            "payout_started_at",
            "payout_completed_at",
        ):
            value = getattr(
                payout_batch,
                field_name,
                None,
            )

            if _present(value):
                add_evidence(
                    action="payout",
                    model="FundNegativePayoutBatch",
                    row_id=int(payout_batch.id),
                    field=field_name,
                    value=value,
                    reason=(
                        "payout_batch_execution_evidence:"
                        f"{field_name}"
                    ),
                )

        if _dec(
            payout_batch.confirmed_total_payout_usdt
        ) > ZERO:
            add_evidence(
                action="payout",
                model="FundNegativePayoutBatch",
                row_id=int(payout_batch.id),
                field="confirmed_total_payout_usdt",
                value=(
                    payout_batch.confirmed_total_payout_usdt
                ),
                reason="confirmed_payout_amount_present",
            )

        if _present(
            payout_batch.payout_execution_json
        ):
            add_evidence(
                action="payout",
                model="FundNegativePayoutBatch",
                row_id=int(payout_batch.id),
                field="payout_execution_json",
                value=payout_batch.payout_execution_json,
                reason="payout_execution_json_present",
            )

        safe_payout_batch_statuses = {
            "created",
            "gas_check_passed",
            "gas_ready",
            "paused_operator_action_required",
            "payouts_planned",
            "failed_requires_review",
        }

        payout_batch_status = str(
            payout_batch.status or ""
        ).strip().lower()

        if (
            payout_batch_status
            and payout_batch_status
            not in safe_payout_batch_statuses
        ):
            add_evidence(
                action="payout",
                model="FundNegativePayoutBatch",
                row_id=int(payout_batch.id),
                field="status",
                value=payout_batch.status,
                reason=(
                    "payout_batch_status_not_proven_pre_external:"
                    f"{payout_batch_status}"
                ),
            )

        safe_gas_statuses = {
            "",
            "not_checked",
            "sufficient",
            "topup_required",
            "ready",
            "insufficient_ok_gas",
            "failed_requires_review",
        }

        gas_status = str(
            payout_batch.gas_status or ""
        ).strip().lower()

        if gas_status not in safe_gas_statuses:
            add_evidence(
                action="gas_topup",
                model="FundNegativePayoutBatch",
                row_id=int(payout_batch.id),
                field="gas_status",
                value=payout_batch.gas_status,
                reason=(
                    "gas_status_not_proven_pre_external:"
                    f"{gas_status}"
                ),
            )

    safe_payout_leg_statuses = {
        "planned",
        "skipped_zero_amount",
        "failed_requires_review",
    }

    for leg in payout_legs:
        for field_name in (
            "tx_hash",
            "sent_at",
            "confirmed_at",
        ):
            value = getattr(
                leg,
                field_name,
                None,
            )

            if _present(value):
                add_evidence(
                    action="payout",
                    model="FundNegativePayoutLeg",
                    row_id=int(leg.id),
                    field=field_name,
                    value=value,
                    reason=(
                        f"payout_leg_{leg.id}_external_evidence:"
                        f"{field_name}"
                    ),
                )

        leg_status = str(
            leg.status or ""
        ).strip().lower()

        if (
            leg_status
            and leg_status
            not in safe_payout_leg_statuses
        ):
            add_evidence(
                action="payout",
                model="FundNegativePayoutLeg",
                row_id=int(leg.id),
                field="status",
                value=leg.status,
                reason=(
                    f"payout_leg_{leg.id}_status_not_proven_pre_external:"
                    f"{leg_status}"
                ),
            )

    if finalization is not None:
        for field_name in (
            "accounting_finalized_at",
            "completed_at",
        ):
            value = getattr(
                finalization,
                field_name,
                None,
            )

            if _present(value):
                add_evidence(
                    action="accounting",
                    model="FundNegativeFinalizationBatch",
                    row_id=int(finalization.id),
                    field=field_name,
                    value=value,
                    reason=(
                        "negative_finalization_evidence:"
                        f"{field_name}"
                    ),
                )

        safe_finalization_statuses = {
            "created",
            "validating",
            "failed_requires_review",
        }

        finalization_status = str(
            finalization.status or ""
        ).strip().lower()

        if (
            finalization_status
            and finalization_status
            not in safe_finalization_statuses
        ):
            add_evidence(
                action="accounting",
                model="FundNegativeFinalizationBatch",
                row_id=int(finalization.id),
                field="status",
                value=finalization.status,
                reason=(
                    "finalization_status_not_proven_pre_accounting:"
                    f"{finalization_status}"
                ),
            )

    safe_transfer_statuses = {
        "pending",
        "failed",
        "failed_requires_review",
        "skipped",
    }

    for transfer in transfers:
        transfer_type = str(
            transfer.transfer_type or ""
        ).strip().lower()

        if "gas_topup" in transfer_type:
            transfer_action = "gas_topup"
        elif "redeem_payout" in transfer_type:
            transfer_action = "payout"
        else:
            transfer_action = "other"

        for field_name in (
            "source_nonce",
            "prepared_tx_hash",
            "prepared_raw_tx",
            "gas_tx_hash",
            "tx_hash",
            "prepared_at",
            "broadcast_at",
            "sent_at",
            "confirmed_at",
        ):
            value = getattr(
                transfer,
                field_name,
                None,
            )

            if _present(value):
                add_evidence(
                    action=transfer_action,
                    model="FundSettlementTransfer",
                    row_id=int(transfer.id),
                    field=field_name,
                    value=value,
                    reason=(
                        f"settlement_transfer_{transfer.id}_"
                        f"external_evidence:{field_name}"
                    ),
                )

        transfer_status = str(
            transfer.status or ""
        ).strip().lower()

        if (
            not transfer_status
            or transfer_status
            not in safe_transfer_statuses
        ):
            add_evidence(
                action=transfer_action,
                model="FundSettlementTransfer",
                row_id=int(transfer.id),
                field="status",
                value=transfer.status,
                reason=(
                    f"settlement_transfer_{transfer.id}_"
                    "status_not_proven_pre_external:"
                    f"{transfer_status or 'empty'}"
                ),
            )

    accounting_finalized = bool(
        flags["accounting"]
    )

    any_external_action = any(
        (
            flags["sale"],
            flags["earn"],
            flags["universal_transfer"],
            flags["withdrawal"],
            flags["payout"],
            flags["gas_topup"],
            flags["other"],
        )
    )

    safe = (
        not accounting_finalized
        and not any_external_action
    )

    return NegativeExternalState(
        settlement_batch_id=int(batch.id),
        safe_to_release_reserves=safe,
        safe_to_unlock_pricing=safe,
        accounting_finalized=accounting_finalized,
        sale_action_detected=bool(
            flags["sale"]
        ),
        earn_action_detected=bool(
            flags["earn"]
        ),
        universal_transfer_action_detected=bool(
            flags["universal_transfer"]
        ),
        withdrawal_action_detected=bool(
            flags["withdrawal"]
        ),
        payout_action_detected=bool(
            flags["payout"]
        ),
        gas_topup_action_detected=bool(
            flags["gas_topup"]
        ),
        other_external_action_detected=bool(
            flags["other"]
        ),
        reasons=tuple(reasons),
        evidence=tuple(evidence),
    )