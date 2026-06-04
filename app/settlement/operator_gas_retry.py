from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import FundOperatorAction, FundSettlementBatch
from app.operator_actions.service import (
    ACTION_REASON_INSUFFICIENT_OK_GAS,
    ACTION_TYPE_RETRY_SETTLEMENT_GAS_TOPUP,
    OPERATOR_ACTION_STATUS_PROCESSING,
    get_pending_operator_actions_for_worker,
    mark_operator_action_failed,
    mark_operator_action_processing,
    mark_operator_action_success,
)
from app.settlement.statuses import (
    BATCH_STATUS_GAS_READY,
    BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED,
)


ZERO = Decimal("0")


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
    Stage 22.7 mock alert decision only.
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
    Stage 22.7 integration point.

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