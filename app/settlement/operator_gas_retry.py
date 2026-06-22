from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.settlement.negative_payout_flow import _ensure_live_settlement_wallet_gas

from app.models import Fund, FundNegativePayoutBatch, FundOperatorAction, FundSettlementBatch, FundWallet
from app.operator_actions.service import (
    ACTION_REASON_INSUFFICIENT_OK_GAS,
    ACTION_TYPE_RETRY_SETTLEMENT_GAS_TOPUP,
    OPERATOR_ACTION_STATUS_PENDING,
    OPERATOR_ACTION_STATUS_PROCESSING,
    get_pending_operator_actions_for_worker,
    mark_operator_action_failed,
    mark_operator_action_processing,
    mark_operator_action_success,
)
from app.settlement.statuses import (
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_GAS_READY,
    BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
    BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING,
    BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED,
    PAYOUT_BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    PAYOUT_BATCH_STATUS_GAS_READY,
    PAYOUT_BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED,
    PAYOUT_GAS_STATUS_FAILED_REQUIRES_REVIEW,
)


ZERO = Decimal("0")

ACTION_TYPE_NEGATIVE_NET_RETRY_GAS_TOPUP = "negative_net_retry_gas_topup"

SUPPORTED_LIVE_RETRY_ACTION_TYPES = {
    ACTION_TYPE_RETRY_SETTLEMENT_GAS_TOPUP,
    ACTION_TYPE_NEGATIVE_NET_RETRY_GAS_TOPUP,
}


class SettlementGasRetryError(RuntimeError):
    pass


@dataclass(frozen=True)
class SettlementGasRetryDecision:
    action_id: int
    settlement_batch_id: int | None
    fund_id: int | None
    ok: bool
    action_status: str
    batch_status: str | None
    topup_called: bool
    required_bnb: Decimal
    available_bnb: Decimal
    reason: str | None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["required_bnb"] = str(self.required_bnb)
        raw["available_bnb"] = str(self.available_bnb)
        raw["diagnostics"] = _json_dict(raw["diagnostics"])
        return raw


@dataclass(frozen=True)
class SettlementGasRetryRunResult:
    ok_count: int
    failed_count: int
    total_count: int
    decisions: list[SettlementGasRetryDecision]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok_count": self.ok_count,
            "failed_count": self.failed_count,
            "total_count": self.total_count,
            "decisions": [decision.to_dict() for decision in self.decisions],
        }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def dec(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)

    if isinstance(value, Decimal):
        return value

    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]

    return value


def _json_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_value(value) for key, value in data.items()}


def _payload_json(action: FundOperatorAction) -> dict[str, Any]:
    if isinstance(action.payload_json, dict):
        return dict(action.payload_json)

    return {}


def _required_bnb_from_action(action: FundOperatorAction) -> Decimal:
    payload = _payload_json(action)

    for key in (
        "required_bnb",
        "required_topup_bnb",
        "required_gas_bnb",
    ):
        value = dec(payload.get(key))
        if value > ZERO:
            return value

    return Decimal("0.01")


def _get_negative_payout_batch_for_update(
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
        raise SettlementGasRetryError(
            f"Negative payout batch not found for settlement_batch_id={settlement_batch_id}"
        )

    return payout_batch


def _get_fund_for_update(db: Session, *, fund_id: int) -> Fund:
    fund = (
        db.query(Fund)
        .filter(Fund.id == int(fund_id))
        .with_for_update()
        .first()
    )

    if fund is None:
        raise SettlementGasRetryError(f"Fund not found: {fund_id}")

    return fund


def _get_settlement_wallet_for_update(
    db: Session,
    *,
    wallet_id: int | None,
    fund_id: int,
) -> FundWallet:
    if wallet_id is None:
        raise SettlementGasRetryError("payout_batch.settlement_wallet_id is required")

    wallet = (
        db.query(FundWallet)
        .filter(FundWallet.id == int(wallet_id))
        .with_for_update()
        .first()
    )

    if wallet is None:
        raise SettlementGasRetryError(f"Settlement wallet not found: {wallet_id}")

    if int(wallet.fund_id) != int(fund_id):
        raise SettlementGasRetryError("Settlement wallet fund_id mismatch")

    if wallet.blockchain != "BSC":
        raise SettlementGasRetryError("Settlement wallet blockchain must be BSC")

    if wallet.wallet_type != "settlement":
        raise SettlementGasRetryError("Settlement wallet type must be settlement")

    if not wallet.is_active:
        raise SettlementGasRetryError("Settlement wallet must be active")

    if not wallet.address:
        raise SettlementGasRetryError("Settlement wallet address is required")

    return wallet


def _payout_leg_count(db: Session, *, payout_batch_id: int) -> int:
    from app.models import FundNegativePayoutLeg

    count = (
        db.query(FundNegativePayoutLeg.id)
        .filter(FundNegativePayoutLeg.payout_batch_id == int(payout_batch_id))
        .count()
    )

    return max(int(count), 1)


def _tx_receipt_status(w3: Any, tx_hash: str | None) -> int | None:
    tx_hash = str(tx_hash or "").strip()
    if not tx_hash:
        return None

    try:
        receipt = w3.eth.get_transaction_receipt(tx_hash)
    except Exception:
        return None

    if receipt is None:
        return None

    return int(receipt.get("status", 0))


def _validate_live_action(action: FundOperatorAction) -> None:
    if action.action_type not in SUPPORTED_LIVE_RETRY_ACTION_TYPES:
        raise SettlementGasRetryError(
            f"Unsupported operator action type: {action.action_type}"
        )

    if action.action_type == ACTION_TYPE_RETRY_SETTLEMENT_GAS_TOPUP:
        if action.reason != ACTION_REASON_INSUFFICIENT_OK_GAS:
            raise SettlementGasRetryError(
                f"Unsupported operator action reason: {action.reason}"
            )

    if action.action_type == ACTION_TYPE_NEGATIVE_NET_RETRY_GAS_TOPUP:
        if action.reason not in {None, ACTION_REASON_INSUFFICIENT_OK_GAS}:
            raise SettlementGasRetryError(
                f"Unsupported negative-net retry action reason: {action.reason}"
            )

    if action.settlement_batch_id is None:
        raise SettlementGasRetryError(
            "settlement_batch_id is required for settlement gas retry action"
        )


def _lock_action_for_live_processing(
    db: Session,
    *,
    action_id: int,
    now: datetime,
) -> FundOperatorAction:
    action = (
        db.query(FundOperatorAction)
        .filter(FundOperatorAction.id == int(action_id))
        .with_for_update()
        .first()
    )

    if action is None:
        raise SettlementGasRetryError(f"Operator action not found: {action_id}")

    if action.status == OPERATOR_ACTION_STATUS_PENDING:
        return mark_operator_action_processing(
            db,
            action_id=int(action.id),
            now=now,
        )

    if action.status == OPERATOR_ACTION_STATUS_PROCESSING:
        return action

    return action


def _get_retry_actions_for_live_worker(
    db: Session,
    *,
    limit: int,
    now: datetime,
) -> list[FundOperatorAction]:
    return (
        db.query(FundOperatorAction)
        .filter(FundOperatorAction.action_type.in_(list(SUPPORTED_LIVE_RETRY_ACTION_TYPES)))
        .filter(
            (FundOperatorAction.reason == ACTION_REASON_INSUFFICIENT_OK_GAS)
            | (FundOperatorAction.reason.is_(None))
        )
        .filter(
            FundOperatorAction.status.in_(
                [
                    OPERATOR_ACTION_STATUS_PENDING,
                    OPERATOR_ACTION_STATUS_PROCESSING,
                ]
            )
        )
        .filter(
            (FundOperatorAction.expires_at.is_(None))
            | (FundOperatorAction.expires_at > now)
            | (FundOperatorAction.status == OPERATOR_ACTION_STATUS_PROCESSING)
        )
        .order_by(FundOperatorAction.requested_at.asc(), FundOperatorAction.id.asc())
        .limit(int(limit))
        .all()
    )


def _mark_live_retry_failed_requires_review(
    db: Session,
    *,
    action: FundOperatorAction,
    batch: FundSettlementBatch | None,
    payout_batch: FundNegativePayoutBatch | None,
    error: str,
    diagnostics: dict[str, Any] | None,
    now: datetime,
) -> FundOperatorAction:
    if payout_batch is not None:
        payout_batch.status = PAYOUT_BATCH_STATUS_FAILED_REQUIRES_REVIEW
        payout_batch.gas_status = PAYOUT_GAS_STATUS_FAILED_REQUIRES_REVIEW
        payout_batch.error = error
        payout_batch.updated_at = now
        payout_batch.gas_reconciliation_json = _json_dict(
            {
                "ok": False,
                "error": error,
                "operator_action_id": int(action.id),
                "real_bsc_transfer": True,
                "diagnostics": diagnostics or {},
            }
        )
        db.add(payout_batch)

    if batch is not None:
        batch.status = BATCH_STATUS_FAILED_REQUIRES_REVIEW
        batch.error = error
        batch.updated_at = now
        db.add(batch)

    db.flush()

    return mark_operator_action_failed(
        db,
        action_id=int(action.id),
        error=error,
        result_json={
            "result": "real_bsc_gas_retry_failed_requires_review",
            "real_bsc_transfer": True,
            "diagnostics": diagnostics or {},
        },
        now=now,
    )


def _get_settlement_batch_for_update(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundSettlementBatch:
    batch = (
        db.query(FundSettlementBatch)
        .filter(FundSettlementBatch.id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )

    if batch is None:
        raise SettlementGasRetryError(
            f"Settlement batch not found: {settlement_batch_id}"
        )

    return batch


def _validate_action(action: FundOperatorAction) -> None:
    if action.action_type != ACTION_TYPE_RETRY_SETTLEMENT_GAS_TOPUP:
        raise SettlementGasRetryError(
            f"Unsupported operator action type: {action.action_type}"
        )

    if action.reason != ACTION_REASON_INSUFFICIENT_OK_GAS:
        raise SettlementGasRetryError(
            f"Unsupported operator action reason: {action.reason}"
        )

    if action.settlement_batch_id is None:
        raise SettlementGasRetryError(
            "settlement_batch_id is required for settlement gas retry action"
        )


def _validate_batch_still_paused_for_insufficient_ok_gas(
    *,
    batch: FundSettlementBatch,
    action: FundOperatorAction,
) -> None:
    if action.fund_id is not None and int(batch.fund_id) != int(action.fund_id):
        raise SettlementGasRetryError(
            (
                "Operator action fund_id does not match settlement batch fund_id: "
                f"action={action.fund_id}, batch={batch.fund_id}"
            )
        )

    if batch.status != BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED:
        raise SettlementGasRetryError(
            (
                "Settlement batch is not paused for operator action: "
                f"batch_id={batch.id}, status={batch.status}"
            )
        )

    if ACTION_REASON_INSUFFICIENT_OK_GAS not in str(batch.error or ""):
        raise SettlementGasRetryError(
            (
                "Settlement batch is not paused for insufficient_ok_gas: "
                f"batch_id={batch.id}, error={batch.error!r}"
            )
        )


def build_retry_settlement_gas_alert_decision_mock(
    *,
    action: FundOperatorAction,
    batch: FundSettlementBatch,
    required_bnb: Decimal,
    available_bnb: Decimal,
) -> dict[str, Any]:
    """
    Stage 25 mock alert decision only.
    Does not call Telegram.
    """
    return {
        "alert_type": "insufficient_ok_gas_retry_failed",
        "action_id": action.id,
        "fund_id": batch.fund_id,
        "settlement_batch_id": batch.id,
        "reason": ACTION_REASON_INSUFFICIENT_OK_GAS,
        "required_bnb": required_bnb,
        "available_bnb": available_bnb,
        "telegram_delivery": "mock_suppressed",
    }


def execute_existing_settlement_gas_topup_algorithm_mock(
    *,
    action: FundOperatorAction,
    batch: FundSettlementBatch,
    required_bnb: Decimal,
    available_bnb: Decimal,
) -> dict[str, Any]:
    """
    Stage 25 guarded integration point.

    This is a mock wrapper for the existing settlement gas top-up algorithm.
    It intentionally does not call BSC and does not send BNB.
    Future real implementation must plug the existing production-safe gas top-up
    function here without changing the Telegram callback flow.
    """
    if available_bnb < required_bnb:
        raise SettlementGasRetryError(
            (
                "Mock OK gas wallet balance is still insufficient: "
                f"available_bnb={available_bnb}, required_bnb={required_bnb}"
            )
        )

    return {
        "mock_topup": True,
        "topup_called": True,
        "fund_id": batch.fund_id,
        "settlement_batch_id": batch.id,
        "action_id": action.id,
        "required_bnb": required_bnb,
        "available_bnb": available_bnb,
        "tx_hash": None,
        "real_bsc_transfer": False,
    }


def process_retry_settlement_gas_topup_action_mock(
    db: Session,
    *,
    action_id: int,
    mock_ok_gas_balance_bnb: Decimal | str,
    now: datetime | None = None,
) -> SettlementGasRetryDecision:
    now = now or utcnow()

    processing_action = mark_operator_action_processing(
        db,
        action_id=action_id,
        now=now,
    )

    if processing_action.status != OPERATOR_ACTION_STATUS_PROCESSING:
        return SettlementGasRetryDecision(
            action_id=processing_action.id,
            settlement_batch_id=processing_action.settlement_batch_id,
            fund_id=processing_action.fund_id,
            ok=False,
            action_status=processing_action.status,
            batch_status=None,
            topup_called=False,
            required_bnb=ZERO,
            available_bnb=dec(mock_ok_gas_balance_bnb),
            reason=processing_action.error,
            diagnostics={
                "expired_or_not_processing": True,
            },
        )

    try:
        _validate_action(processing_action)

        batch = _get_settlement_batch_for_update(
            db,
            settlement_batch_id=int(processing_action.settlement_batch_id),
        )

        _validate_batch_still_paused_for_insufficient_ok_gas(
            batch=batch,
            action=processing_action,
        )

        required_bnb = _required_bnb_from_action(processing_action)
        available_bnb = dec(mock_ok_gas_balance_bnb)

        if available_bnb >= required_bnb:
            topup_result = execute_existing_settlement_gas_topup_algorithm_mock(
                action=processing_action,
                batch=batch,
                required_bnb=required_bnb,
                available_bnb=available_bnb,
            )

            batch.status = BATCH_STATUS_GAS_READY
            batch.error = None
            batch.updated_at = now

            db.add(batch)
            db.flush()

            success_action = mark_operator_action_success(
                db,
                action_id=processing_action.id,
                result_json={
                    "result": "mock_gas_topup_success",
                    "batch_status": batch.status,
                    "topup_result": topup_result,
                    "real_bsc_transfer": False,
                },
                now=now,
            )

            return SettlementGasRetryDecision(
                action_id=success_action.id,
                settlement_batch_id=batch.id,
                fund_id=batch.fund_id,
                ok=True,
                action_status=success_action.status,
                batch_status=batch.status,
                topup_called=True,
                required_bnb=required_bnb,
                available_bnb=available_bnb,
                reason=None,
                diagnostics={
                    "topup_result": topup_result,
                    "real_bsc_transfer": False,
                },
            )

        alert_decision = build_retry_settlement_gas_alert_decision_mock(
            action=processing_action,
            batch=batch,
            required_bnb=required_bnb,
            available_bnb=available_bnb,
        )

        batch.status = BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED
        batch.error = (
            "insufficient_ok_gas: OK gas wallet BNB is still below "
            f"required top-up amount; available_bnb={available_bnb}; "
            f"required_bnb={required_bnb}"
        )
        batch.updated_at = now

        db.add(batch)
        db.flush()

        failed_action = mark_operator_action_failed(
            db,
            action_id=processing_action.id,
            error="OK gas wallet BNB still insufficient",
            result_json={
                "result": "mock_gas_topup_not_attempted",
                "batch_status": batch.status,
                "required_bnb": required_bnb,
                "available_bnb": available_bnb,
                "alert_decision": alert_decision,
                "real_bsc_transfer": False,
            },
            now=now,
        )

        return SettlementGasRetryDecision(
            action_id=failed_action.id,
            settlement_batch_id=batch.id,
            fund_id=batch.fund_id,
            ok=False,
            action_status=failed_action.status,
            batch_status=batch.status,
            topup_called=False,
            required_bnb=required_bnb,
            available_bnb=available_bnb,
            reason=failed_action.error,
            diagnostics={
                "alert_decision": alert_decision,
                "real_bsc_transfer": False,
            },
        )

    except Exception as exc:
        failed_action = mark_operator_action_failed(
            db,
            action_id=processing_action.id,
            error=f"{type(exc).__name__}: {exc}",
            result_json={
                "result": "mock_gas_topup_retry_failed",
                "error_type": type(exc).__name__,
                "real_bsc_transfer": False,
            },
            now=now,
        )

        return SettlementGasRetryDecision(
            action_id=failed_action.id,
            settlement_batch_id=failed_action.settlement_batch_id,
            fund_id=failed_action.fund_id,
            ok=False,
            action_status=failed_action.status,
            batch_status=None,
            topup_called=False,
            required_bnb=ZERO,
            available_bnb=dec(mock_ok_gas_balance_bnb),
            reason=failed_action.error,
            diagnostics={
                "exception_type": type(exc).__name__,
                "real_bsc_transfer": False,
            },
        )


def process_retry_settlement_gas_topup_action_live(
    db: Session,
    *,
    action_id: int,
    now: datetime | None = None,
) -> SettlementGasRetryDecision:
    from app.settlement.gas_service import get_bnb_balance, get_web3

    now = now or utcnow()

    processing_action = _lock_action_for_live_processing(
        db,
        action_id=action_id,
        now=now,
    )

    if processing_action.status != OPERATOR_ACTION_STATUS_PROCESSING:
        return SettlementGasRetryDecision(
            action_id=processing_action.id,
            settlement_batch_id=processing_action.settlement_batch_id,
            fund_id=processing_action.fund_id,
            ok=False,
            action_status=processing_action.status,
            batch_status=None,
            topup_called=False,
            required_bnb=ZERO,
            available_bnb=ZERO,
            reason=processing_action.error,
            diagnostics={
                "not_processing": True,
                "real_bsc_transfer": True,
            },
        )

    batch: FundSettlementBatch | None = None
    payout_batch: FundNegativePayoutBatch | None = None
    required_bnb = ZERO
    available_bnb = ZERO

    try:
        _validate_live_action(processing_action)

        batch = _get_settlement_batch_for_update(
            db,
            settlement_batch_id=int(processing_action.settlement_batch_id),
        )

        payout_batch = _get_negative_payout_batch_for_update(
            db,
            settlement_batch_id=int(batch.id),
        )

        fund = _get_fund_for_update(db, fund_id=int(batch.fund_id))

        settlement_wallet = _get_settlement_wallet_for_update(
            db,
            wallet_id=payout_batch.settlement_wallet_id,
            fund_id=int(batch.fund_id),
        )

        if processing_action.fund_id is not None and int(processing_action.fund_id) != int(batch.fund_id):
            raise SettlementGasRetryError("Operator action fund_id mismatch")

        if payout_batch.operator_action_id is not None and int(payout_batch.operator_action_id) != int(processing_action.id):
            raise SettlementGasRetryError("Payout batch is linked to another operator action")

        if payout_batch.status not in {
            PAYOUT_BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED,
            PAYOUT_BATCH_STATUS_GAS_READY,
        }:
            raise SettlementGasRetryError(
                f"Payout batch status is not retryable: {payout_batch.status}"
            )

        if batch.status not in {
            BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED,
            BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING,
            BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
        }:
            raise SettlementGasRetryError(
                f"Settlement batch status is not retryable: {batch.status}"
            )

        payout_batch.operator_action_id = int(processing_action.id)
        db.add(payout_batch)
        db.flush()

        w3 = get_web3()
        before_tx_hash = str(payout_batch.gas_topup_tx_hash or "").strip()

        if before_tx_hash:
            receipt_status = _tx_receipt_status(w3, before_tx_hash)

            if receipt_status == 0:
                failed_action = _mark_live_retry_failed_requires_review(
                    db,
                    action=processing_action,
                    batch=batch,
                    payout_batch=payout_batch,
                    error="existing_topup_tx_failed",
                    diagnostics={
                        "tx_hash": before_tx_hash,
                        "receipt_status": receipt_status,
                        "idempotent_no_resend": True,
                    },
                    now=now,
                )

                return SettlementGasRetryDecision(
                    action_id=failed_action.id,
                    settlement_batch_id=batch.id,
                    fund_id=batch.fund_id,
                    ok=False,
                    action_status=failed_action.status,
                    batch_status=batch.status,
                    topup_called=False,
                    required_bnb=dec(payout_batch.gas_topup_required_bnb),
                    available_bnb=dec(payout_batch.ok_gas_wallet_bnb_available),
                    reason=failed_action.error,
                    diagnostics={
                        "tx_hash": before_tx_hash,
                        "receipt_status": receipt_status,
                        "idempotent_no_resend": True,
                        "existing_topup_tx_failed": True,
                        "real_bsc_transfer": True,
                    },
                )

        leg_count = _payout_leg_count(db, payout_batch_id=int(payout_batch.id))

        gas_ready = _ensure_live_settlement_wallet_gas(
            db,
            w3=w3,
            batch=payout_batch,
            settlement_batch=batch,
            fund=fund,
            settlement_wallet=settlement_wallet,
            leg_count=leg_count,
            now=now,
        )

        required_bnb = dec(payout_batch.gas_topup_required_bnb)
        available_bnb = dec(payout_batch.ok_gas_wallet_bnb_available)

        if not gas_ready:
            batch.status = BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING
            batch.updated_at = now
            db.add(batch)

            processing_action.result_json = _json_dict(
                {
                    "result": "real_bsc_gas_topup_pending_confirmation",
                    "payout_batch_id": int(payout_batch.id),
                    "tx_hash": payout_batch.gas_topup_tx_hash,
                    "required_bnb": required_bnb,
                    "available_bnb": available_bnb,
                    "idempotent_no_resend": bool(before_tx_hash),
                    "pending_chain_confirmation": True,
                    "real_bsc_transfer": True,
                }
            )
            processing_action.updated_at = now
            db.add(processing_action)
            db.flush()

            return SettlementGasRetryDecision(
                action_id=processing_action.id,
                settlement_batch_id=batch.id,
                fund_id=batch.fund_id,
                ok=True,
                action_status=processing_action.status,
                batch_status=batch.status,
                topup_called=not bool(before_tx_hash),
                required_bnb=required_bnb,
                available_bnb=available_bnb,
                reason="pending_chain_confirmation_no_resend" if before_tx_hash else "sent_pending_chain_confirmation",
                diagnostics={
                    "payout_batch_id": int(payout_batch.id),
                    "tx_hash": payout_batch.gas_topup_tx_hash,
                    "pending_chain_confirmation": True,
                    "idempotent_no_resend": bool(before_tx_hash),
                    "guard_before_bsc_send": True,
                    "real_bsc_transfer": True,
                },
            )

        try:
            settlement_wallet_bnb_after = get_bnb_balance(w3, str(settlement_wallet.address))
        except Exception:
            settlement_wallet_bnb_after = None

        batch.status = BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT
        batch.error = None
        batch.updated_at = now

        payout_batch.status = PAYOUT_BATCH_STATUS_GAS_READY
        payout_batch.error = None
        payout_batch.updated_at = now

        db.add(batch)
        db.add(payout_batch)
        db.flush()

        success_action = mark_operator_action_success(
            db,
            action_id=int(processing_action.id),
            result_json={
                "result": "real_bsc_gas_topup_confirmed",
                "batch_status": batch.status,
                "payout_batch_status": payout_batch.status,
                "payout_batch_id": int(payout_batch.id),
                "tx_hash": payout_batch.gas_topup_tx_hash,
                "required_bnb": required_bnb,
                "available_bnb": available_bnb,
                "settlement_wallet_bnb_after": settlement_wallet_bnb_after,
                "real_bsc_transfer": True,
            },
            now=now,
        )

        return SettlementGasRetryDecision(
            action_id=success_action.id,
            settlement_batch_id=batch.id,
            fund_id=batch.fund_id,
            ok=True,
            action_status=success_action.status,
            batch_status=batch.status,
            topup_called=False,
            required_bnb=required_bnb,
            available_bnb=available_bnb,
            reason=None,
            diagnostics={
                "payout_batch_id": int(payout_batch.id),
                "tx_hash": payout_batch.gas_topup_tx_hash,
                "settlement_wallet_bnb_after": settlement_wallet_bnb_after,
                "real_bsc_transfer": True,
                "success_marks_action": True,
            },
        )

    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"

        failed_action = _mark_live_retry_failed_requires_review(
            db,
            action=processing_action,
            batch=batch,
            payout_batch=payout_batch,
            error=error,
            diagnostics={
                "required_bnb": required_bnb,
                "available_bnb": available_bnb,
                "exception_type": type(exc).__name__,
            },
            now=now,
        )

        return SettlementGasRetryDecision(
            action_id=failed_action.id,
            settlement_batch_id=failed_action.settlement_batch_id,
            fund_id=failed_action.fund_id,
            ok=False,
            action_status=failed_action.status,
            batch_status=batch.status if batch is not None else None,
            topup_called=False,
            required_bnb=required_bnb,
            available_bnb=available_bnb,
            reason=failed_action.error or error,
            diagnostics={
                "exception_type": type(exc).__name__,
                "real_bsc_transfer": True,
            },
        )


def process_pending_retry_settlement_gas_topup_actions_mock(
    db: Session,
    *,
    mock_ok_gas_balance_bnb: Decimal | str,
    limit: int = 20,
    now: datetime | None = None,
) -> SettlementGasRetryRunResult:
    now = now or utcnow()

    pending_actions = get_pending_operator_actions_for_worker(
        db,
        action_type=ACTION_TYPE_RETRY_SETTLEMENT_GAS_TOPUP,
        reason=ACTION_REASON_INSUFFICIENT_OK_GAS,
        limit=limit,
        now=now,
    )

    decisions: list[SettlementGasRetryDecision] = []

    for action in pending_actions:
        decision = process_retry_settlement_gas_topup_action_mock(
            db,
            action_id=action.id,
            mock_ok_gas_balance_bnb=mock_ok_gas_balance_bnb,
            now=now,
        )
        decisions.append(decision)

    ok_count = sum(1 for decision in decisions if decision.ok)
    failed_count = len(decisions) - ok_count

    return SettlementGasRetryRunResult(
        ok_count=ok_count,
        failed_count=failed_count,
        total_count=len(decisions),
        decisions=decisions,
    )


def process_pending_retry_settlement_gas_topup_actions_live(
    db: Session,
    *,
    limit: int = 20,
    now: datetime | None = None,
) -> SettlementGasRetryRunResult:
    now = now or utcnow()

    actions = _get_retry_actions_for_live_worker(
        db,
        limit=limit,
        now=now,
    )

    decisions: list[SettlementGasRetryDecision] = []

    for action in actions:
        decision = process_retry_settlement_gas_topup_action_live(
            db,
            action_id=int(action.id),
            now=now,
        )
        decisions.append(decision)

    ok_count = sum(1 for decision in decisions if decision.ok)
    failed_count = len(decisions) - ok_count

    return SettlementGasRetryRunResult(
        ok_count=ok_count,
        failed_count=failed_count,
        total_count=len(decisions),
        decisions=decisions,
    )