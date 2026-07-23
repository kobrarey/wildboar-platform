from __future__ import annotations

import json

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session
from web3 import Web3

from app.config import settings
from app.models import FundBscTransactionIntent
from app.settlement.bsc_intent_reconciliation import (
    BscIntentReconciliationResult,
    reconcile_bsc_transaction_intent,
)
from app.settlement.bsc_intent_service import (
    BscIntentError,
    _commit_and_refresh_bsc_intent,
    _load_bsc_intent_for_update,
    _validate_persisted_bsc_intent_or_fail,
)
from app.settlement.erc20_receipt import (
    Erc20ReceiptError,
    normalize_transaction_hash,
)
from app.settlement.statuses import (
    BSC_INTENT_STATUS_BROADCAST,
    BSC_INTENT_STATUS_BROADCASTING,
    BSC_INTENT_STATUS_CONFIRMED,
    BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW,
    BSC_INTENT_STATUS_PENDING_CONFIRMATION,
    BSC_INTENT_STATUS_PREPARED,
    BSC_INTENT_STATUS_VISIBLE,
)


class BscIntentReconciliationPersistenceError(
    BscIntentError
):
    pass


@dataclass(frozen=True)
class BscIntentReconciliationCycleResult:
    action: str
    intent_id: int
    previous_status: str
    status: str
    tx_hash: str
    confirmations: int
    transitioned: bool
    rpc_used: bool


@dataclass(frozen=True)
class _BscIntentReconciliationSnapshot:
    intent_id: int
    status: str
    intent_fingerprint: str
    tx_hash: str
    rpc_intent: FundBscTransactionIntent


_RECONCILABLE_STATUSES = frozenset(
    {
        BSC_INTENT_STATUS_BROADCAST,
        BSC_INTENT_STATUS_VISIBLE,
        BSC_INTENT_STATUS_PENDING_CONFIRMATION,
    }
)

_TERMINAL_STATUSES = frozenset(
    {
        BSC_INTENT_STATUS_CONFIRMED,
        BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW,
    }
)

_KNOWN_NON_RECONCILABLE_STATUSES = frozenset(
    {
        BSC_INTENT_STATUS_PREPARED,
        BSC_INTENT_STATUS_BROADCASTING,
    }
)


def _utc_timestamp(
    value: datetime | None,
) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)

    if value.tzinfo is None:
        raise BscIntentReconciliationPersistenceError(
            "Naive reconciliation datetime is forbidden"
        )

    return value.astimezone(timezone.utc)


def _positive_required_confirmations(
    value: Any | None,
) -> int:
    raw_value = (
        settings
        .NEGATIVE_NET_BSC_INTENT_CONFIRMATIONS_REQUIRED
        if value is None
        else value
    )

    try:
        if isinstance(raw_value, bool):
            raise ValueError

        normalized = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise BscIntentReconciliationPersistenceError(
            "Required BSC confirmations must be "
            "an integer"
        ) from exc

    if normalized <= 0:
        raise BscIntentReconciliationPersistenceError(
            "Required BSC confirmations must be "
            "positive"
        )

    return normalized


def _status(
    intent: FundBscTransactionIntent,
) -> str:
    return str(intent.status or "").strip()


def _normalized_tx_hash(
    value: Any,
    *,
    field_name: str,
) -> str:
    try:
        return normalize_transaction_hash(
            value,
            field_name=field_name,
        )
    except Erc20ReceiptError as exc:
        raise BscIntentReconciliationPersistenceError(
            f"{field_name} is invalid"
        ) from exc


def _nonnegative_int(
    value: Any,
    *,
    field_name: str,
) -> int:
    try:
        if isinstance(value, bool):
            raise ValueError

        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise BscIntentReconciliationPersistenceError(
            f"{field_name} must be an integer"
        ) from exc

    if normalized < 0:
        raise BscIntentReconciliationPersistenceError(
            f"{field_name} cannot be negative"
        )

    return normalized


def _safe_confirmations(
    value: Any,
) -> int:
    try:
        normalized = int(value or 0)
    except (TypeError, ValueError):
        return 0

    return max(normalized, 0)


def _json_copy(
    value: Any,
) -> Any:
    try:
        serialized = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        return json.loads(serialized)
    except (TypeError, ValueError) as exc:
        raise BscIntentReconciliationPersistenceError(
            "Reconciliation evidence is not "
            "JSON serializable"
        ) from exc


def _safe_error_text(
    error: BaseException,
) -> str:
    text = str(error or "").strip()

    if not text:
        text = error.__class__.__name__

    for rpc_url in (
        settings.BSC_RPC_URL,
        settings.BSC_WS_URL,
    ):
        normalized_url = str(
            rpc_url or ""
        ).strip()

        if normalized_url:
            text = text.replace(
                normalized_url,
                "[redacted_rpc_url]",
            )

    return text[:512]


def _cycle_result(
    *,
    action: str,
    intent: FundBscTransactionIntent,
    previous_status: str,
    transitioned: bool,
    rpc_used: bool,
) -> BscIntentReconciliationCycleResult:
    return BscIntentReconciliationCycleResult(
        action=str(action),
        intent_id=int(intent.id),
        previous_status=str(previous_status),
        status=_status(intent),
        tx_hash=str(
            intent.prepared_tx_hash or ""
        ),
        confirmations=_safe_confirmations(
            intent.confirmations
        ),
        transitioned=bool(transitioned),
        rpc_used=bool(rpc_used),
    )


def _rpc_intent_copy(
    intent: FundBscTransactionIntent,
) -> FundBscTransactionIntent:
    return FundBscTransactionIntent(
        id=int(intent.id),
        scope_key=str(intent.scope_key or ""),
        action_type=str(intent.action_type or ""),
        asset=str(intent.asset or ""),
        amount=intent.amount,
        from_address=str(
            intent.from_address or ""
        ),
        to_address=str(
            intent.to_address or ""
        ),
        chain_id=int(intent.chain_id),
        source_nonce=int(intent.source_nonce),
        prepared_tx_hash=str(
            intent.prepared_tx_hash or ""
        ),
        intent_fingerprint=str(
            intent.intent_fingerprint or ""
        ),
        status=_status(intent),
    )


def _load_reconciliation_snapshot(
    db: Session,
    *,
    intent_id: int,
) -> tuple[
    _BscIntentReconciliationSnapshot | None,
    BscIntentReconciliationCycleResult | None,
]:
    intent = _load_bsc_intent_for_update(
        db,
        intent_id=intent_id,
    )

    _validate_persisted_bsc_intent_or_fail(
        db,
        intent=intent,
    )

    current_status = _status(intent)

    if current_status not in (
        _RECONCILABLE_STATUSES
        | _TERMINAL_STATUSES
        | _KNOWN_NON_RECONCILABLE_STATUSES
    ):
        raise BscIntentReconciliationPersistenceError(
            "Unsupported BSC intent reconciliation "
            f"status: {current_status or 'empty'}"
        )

    if current_status not in _RECONCILABLE_STATUSES:
        db.commit()
        db.refresh(intent)

        return (
            None,
            _cycle_result(
                action=f"status_{current_status}",
                intent=intent,
                previous_status=current_status,
                transitioned=False,
                rpc_used=False,
            ),
        )

    snapshot = _BscIntentReconciliationSnapshot(
        intent_id=int(intent.id),
        status=current_status,
        intent_fingerprint=str(
            intent.intent_fingerprint or ""
        ),
        tx_hash=_normalized_tx_hash(
            intent.prepared_tx_hash,
            field_name=(
                "intent.prepared_tx_hash"
            ),
        ),
        rpc_intent=_rpc_intent_copy(intent),
    )

    # Required boundary: no row/advisory lock may
    # remain held during external read-only RPC.
    db.commit()
    db.refresh(intent)

    return snapshot, None


def _stored_reconciliation_fingerprint(
    intent: FundBscTransactionIntent,
) -> str | None:
    payload = intent.reconciliation_json

    if not isinstance(payload, dict):
        return None

    value = str(
        payload.get(
            "reconciliation_fingerprint"
        )
        or ""
    ).strip().lower()

    return value or None


def _result_contract_mismatches(
    *,
    snapshot: _BscIntentReconciliationSnapshot,
    result: BscIntentReconciliationResult,
    required_confirmations: int,
) -> list[str]:
    mismatches: list[str] = []

    if result.intent_id != snapshot.intent_id:
        mismatches.append("result.intent_id")

    try:
        result_hash = _normalized_tx_hash(
            result.tx_hash,
            field_name="result.tx_hash",
        )
    except BscIntentReconciliationPersistenceError:
        result_hash = None
        mismatches.append("result.tx_hash")

    if (
        result_hash is not None
        and result_hash != snapshot.tx_hash
    ):
        mismatches.append("result.tx_hash")

    if (
        int(result.required_confirmations)
        != required_confirmations
    ):
        mismatches.append(
            "result.required_confirmations"
        )

    expected_status_by_action = {
        "visible": BSC_INTENT_STATUS_VISIBLE,
        "pending_confirmation": (
            BSC_INTENT_STATUS_PENDING_CONFIRMATION
        ),
        "confirmed": BSC_INTENT_STATUS_CONFIRMED,
        "failed_requires_review": (
            BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
        ),
    }

    if result.action in {
        "not_visible",
        "retryable_error",
    }:
        expected_status = snapshot.status
    else:
        expected_status = (
            expected_status_by_action.get(
                result.action
            )
        )

    if expected_status is None:
        mismatches.append("result.action")
    elif (
        str(result.suggested_status or "").strip()
        != expected_status
    ):
        mismatches.append(
            "result.suggested_status"
        )

    try:
        confirmations = _nonnegative_int(
            result.confirmations,
            field_name="result.confirmations",
        )
    except BscIntentReconciliationPersistenceError:
        confirmations = 0
        mismatches.append(
            "result.confirmations"
        )

    if result.action in {
        "not_visible",
        "visible",
        "retryable_error",
    }:
        if result.receipt_status is not None:
            mismatches.append(
                "result.receipt_status"
            )

        if result.block_number is not None:
            mismatches.append(
                "result.block_number"
            )

        if result.current_block is not None:
            mismatches.append(
                "result.current_block"
            )

        if confirmations != 0:
            mismatches.append(
                "result.confirmations"
            )

        if result.reconciliation_fingerprint:
            mismatches.append(
                "result.reconciliation_fingerprint"
            )

    if result.action in {
        "pending_confirmation",
        "confirmed",
    }:
        if result.receipt_status != 1:
            mismatches.append(
                "result.receipt_status"
            )

        try:
            _nonnegative_int(
                result.block_number,
                field_name="result.block_number",
            )
        except BscIntentReconciliationPersistenceError:
            mismatches.append(
                "result.block_number"
            )

        try:
            _nonnegative_int(
                result.current_block,
                field_name="result.current_block",
            )
        except BscIntentReconciliationPersistenceError:
            mismatches.append(
                "result.current_block"
            )

        fingerprint = str(
            result.reconciliation_fingerprint
            or ""
        ).strip().lower()

        if len(fingerprint) != 64:
            mismatches.append(
                "result.reconciliation_fingerprint"
            )
        else:
            try:
                bytes.fromhex(fingerprint)
            except ValueError:
                mismatches.append(
                    "result.reconciliation_fingerprint"
                )

        if (
            result.action
            == "pending_confirmation"
            and confirmations
            >= required_confirmations
        ):
            mismatches.append(
                "result.confirmations"
            )

        if (
            result.action == "confirmed"
            and confirmations
            < required_confirmations
        ):
            mismatches.append(
                "result.confirmations"
            )

    return sorted(set(mismatches))


def _receipt_evidence_mismatches(
    *,
    intent: FundBscTransactionIntent,
    result: BscIntentReconciliationResult,
) -> list[str]:
    mismatches: list[str] = []

    if (
        intent.receipt_status is not None
        and result.receipt_status is not None
        and int(intent.receipt_status)
        != int(result.receipt_status)
    ):
        mismatches.append("receipt_status")

    if (
        intent.block_number is not None
        and result.block_number is not None
        and int(intent.block_number)
        != int(result.block_number)
    ):
        mismatches.append("block_number")

    stored_fingerprint = (
        _stored_reconciliation_fingerprint(
            intent
        )
    )
    observed_fingerprint = str(
        result.reconciliation_fingerprint
        or ""
    ).strip().lower() or None

    if (
        stored_fingerprint is not None
        and observed_fingerprint is not None
        and stored_fingerprint
        != observed_fingerprint
    ):
        mismatches.append(
            "reconciliation_fingerprint"
        )

    return sorted(set(mismatches))


def _snapshot_contract_mismatches(
    *,
    intent: FundBscTransactionIntent,
    snapshot: _BscIntentReconciliationSnapshot,
) -> list[str]:
    mismatches: list[str] = []

    try:
        current_intent_id = int(intent.id)
    except (TypeError, ValueError):
        current_intent_id = None

    if current_intent_id != snapshot.intent_id:
        mismatches.append("intent.id")

    current_fingerprint = str(
        intent.intent_fingerprint or ""
    ).strip()

    if (
        current_fingerprint
        != snapshot.intent_fingerprint
    ):
        mismatches.append(
            "intent.intent_fingerprint"
        )

    try:
        current_tx_hash = _normalized_tx_hash(
            intent.prepared_tx_hash,
            field_name=(
                "intent.prepared_tx_hash"
            ),
        )
    except (
        BscIntentReconciliationPersistenceError
    ):
        current_tx_hash = None
        mismatches.append(
            "intent.prepared_tx_hash"
        )

    if (
        current_tx_hash is not None
        and current_tx_hash != snapshot.tx_hash
    ):
        mismatches.append(
            "intent.prepared_tx_hash"
        )

    return sorted(set(mismatches))


def _apply_receipt_fields(
    *,
    intent: FundBscTransactionIntent,
    result: BscIntentReconciliationResult,
) -> None:
    if result.receipt_status is not None:
        receipt_status = _nonnegative_int(
            result.receipt_status,
            field_name="result.receipt_status",
        )

        if receipt_status not in {0, 1}:
            raise (
                BscIntentReconciliationPersistenceError(
                    "Receipt status must be 0 or 1"
                )
            )

        intent.receipt_status = receipt_status

    if result.block_number is not None:
        intent.block_number = _nonnegative_int(
            result.block_number,
            field_name="result.block_number",
        )

    observed_confirmations = _nonnegative_int(
        result.confirmations,
        field_name="result.confirmations",
    )
    stored_confirmations = _safe_confirmations(
        intent.confirmations
    )

    intent.confirmations = max(
        stored_confirmations,
        observed_confirmations,
    )


def _result_payload(
    *,
    intent: FundBscTransactionIntent,
    result: BscIntentReconciliationResult,
    observed_at: datetime,
    previous_status: str,
    persisted_status: str,
    transitioned: bool,
) -> dict[str, Any]:
    return {
        "schema": (
            "fund_bsc_transaction_intent_"
            "reconciliation_state_v1"
        ),
        "intent_id": int(intent.id),
        "scope_key": str(
            intent.scope_key or ""
        ),
        "intent_fingerprint": str(
            intent.intent_fingerprint or ""
        ),
        "prepared_tx_hash": str(
            intent.prepared_tx_hash or ""
        ),
        "previous_status": previous_status,
        "persisted_status": persisted_status,
        "transitioned": bool(transitioned),
        "result_action": str(result.action),
        "suggested_status": str(
            result.suggested_status or ""
        ),
        "reason_code": result.reason_code,
        "error": result.error,
        "receipt_status": (
            int(result.receipt_status)
            if result.receipt_status is not None
            else None
        ),
        "block_number": (
            int(result.block_number)
            if result.block_number is not None
            else None
        ),
        "current_block": (
            int(result.current_block)
            if result.current_block is not None
            else None
        ),
        "confirmations": int(
            result.confirmations
        ),
        "required_confirmations": int(
            result.required_confirmations
        ),
        "reconciliation_fingerprint": (
            str(
                result.reconciliation_fingerprint
                or ""
            ).strip().lower()
            or None
        ),
        "evidence": _json_copy(
            result.evidence
        ),
        "observed_at": (
            observed_at.isoformat()
        ),
    }


def _failure_payload(
    *,
    intent: FundBscTransactionIntent,
    observed_at: datetime,
    previous_status: str,
    reason_code: str,
    error: str,
    evidence: dict[str, Any] | None = None,
    result: (
        BscIntentReconciliationResult | None
    ) = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": (
            "fund_bsc_transaction_intent_"
            "reconciliation_failure_v1"
        ),
        "intent_id": int(intent.id),
        "scope_key": str(
            intent.scope_key or ""
        ),
        "intent_fingerprint": str(
            intent.intent_fingerprint or ""
        ),
        "prepared_tx_hash": str(
            intent.prepared_tx_hash or ""
        ),
        "previous_status": previous_status,
        "persisted_status": (
            BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
        ),
        "reason_code": str(reason_code),
        "error": str(error)[:512],
        "evidence": _json_copy(
            evidence or {}
        ),
        "observed_at": (
            observed_at.isoformat()
        ),
    }

    if result is not None:
        payload.update(
            {
                "result_action": result.action,
                "suggested_status": (
                    result.suggested_status
                ),
                "receipt_status": (
                    result.receipt_status
                ),
                "block_number": (
                    result.block_number
                ),
                "current_block": (
                    result.current_block
                ),
                "confirmations": (
                    result.confirmations
                ),
                "required_confirmations": (
                    result.required_confirmations
                ),
                "reconciliation_fingerprint": (
                    result
                    .reconciliation_fingerprint
                ),
                "result_evidence": _json_copy(
                    result.evidence
                ),
            }
        )

    return payload


def _fail_locked_intent(
    db: Session,
    *,
    intent: FundBscTransactionIntent,
    previous_status: str,
    observed_at: datetime,
    reason_code: str,
    error: str,
    evidence: dict[str, Any] | None = None,
    result: (
        BscIntentReconciliationResult | None
    ) = None,
) -> BscIntentReconciliationCycleResult:
    intent.status = (
        BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
    )
    intent.failed_at = (
        intent.failed_at or observed_at
    )
    intent.updated_at = observed_at
    intent.error = str(error)[:512]
    intent.reconciliation_json = (
        _failure_payload(
            intent=intent,
            observed_at=observed_at,
            previous_status=previous_status,
            reason_code=reason_code,
            error=error,
            evidence=evidence,
            result=result,
        )
    )

    _commit_and_refresh_bsc_intent(
        db,
        intent=intent,
    )

    return _cycle_result(
        action="failed_requires_review",
        intent=intent,
        previous_status=previous_status,
        transitioned=(
            previous_status
            != BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
        ),
        rpc_used=True,
    )


def _persist_reconciliation_result(
    db: Session,
    *,
    snapshot: _BscIntentReconciliationSnapshot,
    result: BscIntentReconciliationResult,
    required_confirmations: int,
    observed_at: datetime,
) -> BscIntentReconciliationCycleResult:
    intent = _load_bsc_intent_for_update(
        db,
        intent_id=snapshot.intent_id,
    )

    _validate_persisted_bsc_intent_or_fail(
        db,
        intent=intent,
    )

    current_status = _status(intent)

    if current_status in _TERMINAL_STATUSES:
        db.commit()
        db.refresh(intent)

        return _cycle_result(
            action=f"status_{current_status}",
            intent=intent,
            previous_status=current_status,
            transitioned=False,
            rpc_used=True,
        )

    snapshot_contract_mismatches = (
        _snapshot_contract_mismatches(
            intent=intent,
            snapshot=snapshot,
        )
    )

    if snapshot_contract_mismatches:
        return _fail_locked_intent(
            db,
            intent=intent,
            previous_status=current_status,
            observed_at=observed_at,
            reason_code=(
                "stale_reconciliation_snapshot_"
                "contract_mismatch"
            ),
            error=(
                "BSC intent immutable contract "
                "changed during reconciliation"
            ),
            evidence={
                "mismatch_fields": (
                    snapshot_contract_mismatches
                ),
                "snapshot_intent_fingerprint": (
                    snapshot.intent_fingerprint
                ),
                "snapshot_tx_hash": (
                    snapshot.tx_hash
                ),
            },
            result=result,
        )

    if current_status != snapshot.status:
        db.commit()
        db.refresh(intent)

        return _cycle_result(
            action=(
                "stale_snapshot_status_"
                f"{current_status}"
            ),
            intent=intent,
            previous_status=snapshot.status,
            transitioned=False,
            rpc_used=True,
        )

    contract_mismatches = (
        _result_contract_mismatches(
            snapshot=snapshot,
            result=result,
            required_confirmations=(
                required_confirmations
            ),
        )
    )

    if contract_mismatches:
        return _fail_locked_intent(
            db,
            intent=intent,
            previous_status=current_status,
            observed_at=observed_at,
            reason_code=(
                "reconciliation_result_contract_mismatch"
            ),
            error=(
                "BSC reconciliation result does not "
                "match durable intent"
            ),
            evidence={
                "mismatch_fields": (
                    contract_mismatches
                ),
            },
            result=result,
        )

    receipt_mismatches = (
        _receipt_evidence_mismatches(
            intent=intent,
            result=result,
        )
    )

    if receipt_mismatches:
        return _fail_locked_intent(
            db,
            intent=intent,
            previous_status=current_status,
            observed_at=observed_at,
            reason_code=(
                "reconciliation_evidence_mismatch"
            ),
            error=(
                "Previously persisted BSC receipt "
                "evidence changed"
            ),
            evidence={
                "mismatch_fields": (
                    receipt_mismatches
                ),
            },
            result=result,
        )

    if (
        current_status
        == BSC_INTENT_STATUS_PENDING_CONFIRMATION
        and (
            intent.receipt_status != 1
            or intent.block_number is None
            or not _stored_reconciliation_fingerprint(
                intent
            )
        )
    ):
        return _fail_locked_intent(
            db,
            intent=intent,
            previous_status=current_status,
            observed_at=observed_at,
            reason_code=(
                "pending_confirmation_evidence_missing"
            ),
            error=(
                "Pending confirmation intent has no "
                "complete durable receipt evidence"
            ),
        )

    if result.action == "failed_requires_review":
        _apply_receipt_fields(
            intent=intent,
            result=result,
        )

        return _fail_locked_intent(
            db,
            intent=intent,
            previous_status=current_status,
            observed_at=observed_at,
            reason_code=(
                result.reason_code
                or "on_chain_reconciliation_failed"
            ),
            error=(
                result.error
                or "BSC on-chain reconciliation failed"
            ),
            result=result,
        )

    if (
        current_status
        == BSC_INTENT_STATUS_PENDING_CONFIRMATION
        and result.action
        in {
            "not_visible",
            "visible",
        }
    ):
        return _fail_locked_intent(
            db,
            intent=intent,
            previous_status=current_status,
            observed_at=observed_at,
            reason_code=(
                "receipt_regressed_after_"
                "pending_confirmation"
            ),
            error=(
                "Previously visible BSC receipt "
                "is no longer available"
            ),
            result=result,
        )

    if result.action == "retryable_error":
        intent.updated_at = observed_at
        intent.error = (
            result.error
            or result.reason_code
            or "retryable reconciliation error"
        )[:512]
        intent.reconciliation_json = (
            _result_payload(
                intent=intent,
                result=result,
                observed_at=observed_at,
                previous_status=current_status,
                persisted_status=current_status,
                transitioned=False,
            )
        )

        _commit_and_refresh_bsc_intent(
            db,
            intent=intent,
        )

        return _cycle_result(
            action="retryable_error",
            intent=intent,
            previous_status=current_status,
            transitioned=False,
            rpc_used=True,
        )

    target_status = current_status

    if result.action == "visible":
        if (
            current_status
            == BSC_INTENT_STATUS_BROADCAST
        ):
            target_status = (
                BSC_INTENT_STATUS_VISIBLE
            )

    elif result.action in {
        "pending_confirmation",
        "confirmed",
    }:
        _apply_receipt_fields(
            intent=intent,
            result=result,
        )

        if (
            current_status
            == BSC_INTENT_STATUS_BROADCAST
        ):
            target_status = (
                BSC_INTENT_STATUS_VISIBLE
            )

        elif (
            current_status
            == BSC_INTENT_STATUS_VISIBLE
        ):
            target_status = (
                BSC_INTENT_STATUS_PENDING_CONFIRMATION
            )

        elif (
            current_status
            == BSC_INTENT_STATUS_PENDING_CONFIRMATION
            and result.action == "confirmed"
        ):
            target_status = (
                BSC_INTENT_STATUS_CONFIRMED
            )

    transitioned = (
        target_status != current_status
    )

    intent.status = target_status
    intent.updated_at = observed_at
    intent.error = None

    if target_status in {
        BSC_INTENT_STATUS_VISIBLE,
        BSC_INTENT_STATUS_PENDING_CONFIRMATION,
        BSC_INTENT_STATUS_CONFIRMED,
    }:
        intent.visible_at = (
            intent.visible_at or observed_at
        )

    if (
        target_status
        == BSC_INTENT_STATUS_CONFIRMED
    ):
        intent.confirmed_at = (
            intent.confirmed_at or observed_at
        )

    intent.reconciliation_json = (
        _result_payload(
            intent=intent,
            result=result,
            observed_at=observed_at,
            previous_status=current_status,
            persisted_status=target_status,
            transitioned=transitioned,
        )
    )

    _commit_and_refresh_bsc_intent(
        db,
        intent=intent,
    )

    return _cycle_result(
        action=(
            f"transition_{target_status}"
            if transitioned
            else result.action
        ),
        intent=intent,
        previous_status=current_status,
        transitioned=transitioned,
        rpc_used=True,
    )


def _persist_unexpected_exception(
    db: Session,
    *,
    snapshot: _BscIntentReconciliationSnapshot,
    error: BaseException,
    observed_at: datetime,
) -> BscIntentReconciliationCycleResult:
    intent = _load_bsc_intent_for_update(
        db,
        intent_id=snapshot.intent_id,
    )

    _validate_persisted_bsc_intent_or_fail(
        db,
        intent=intent,
    )

    current_status = _status(intent)

    if current_status in _TERMINAL_STATUSES:
        db.commit()
        db.refresh(intent)

        return _cycle_result(
            action=f"status_{current_status}",
            intent=intent,
            previous_status=current_status,
            transitioned=False,
            rpc_used=True,
        )

    snapshot_contract_mismatches = (
        _snapshot_contract_mismatches(
            intent=intent,
            snapshot=snapshot,
        )
    )

    if snapshot_contract_mismatches:
        return _fail_locked_intent(
            db,
            intent=intent,
            previous_status=current_status,
            observed_at=observed_at,
            reason_code=(
                "stale_reconciliation_snapshot_"
                "contract_mismatch"
            ),
            error=(
                "BSC intent immutable contract "
                "changed during reconciliation"
            ),
            evidence={
                "mismatch_fields": (
                    snapshot_contract_mismatches
                ),
                "snapshot_intent_fingerprint": (
                    snapshot.intent_fingerprint
                ),
                "snapshot_tx_hash": (
                    snapshot.tx_hash
                ),
            },
        )

    if current_status != snapshot.status:
        db.commit()
        db.refresh(intent)

        return _cycle_result(
            action=(
                "stale_snapshot_status_"
                f"{current_status}"
            ),
            intent=intent,
            previous_status=snapshot.status,
            transitioned=False,
            rpc_used=True,
        )

    safe_error = _safe_error_text(error)

    return _fail_locked_intent(
        db,
        intent=intent,
        previous_status=current_status,
        observed_at=observed_at,
        reason_code=(
            "unexpected_reconciliation_exception"
        ),
        error=safe_error,
        evidence={
            "error_type": (
                error.__class__.__name__
            ),
        },
    )


def reconcile_bsc_intent_once(
    db: Session,
    w3: Web3,
    *,
    intent_id: int,
    required_confirmations: int | None = None,
    now: datetime | None = None,
) -> BscIntentReconciliationCycleResult:
    observed_at = _utc_timestamp(now)
    required = _positive_required_confirmations(
        required_confirmations
    )

    snapshot, completed_result = (
        _load_reconciliation_snapshot(
            db,
            intent_id=intent_id,
        )
    )

    if completed_result is not None:
        return completed_result

    if snapshot is None:
        raise (
            BscIntentReconciliationPersistenceError(
                "BSC reconciliation snapshot "
                "is missing"
            )
        )

    try:
        # Read-only external phase. DB lock was
        # released by snapshot loader.
        result = reconcile_bsc_transaction_intent(
            w3,
            intent=snapshot.rpc_intent,
            required_confirmations=required,
        )
    except Exception as exc:
        return _persist_unexpected_exception(
            db,
            snapshot=snapshot,
            error=exc,
            observed_at=observed_at,
        )

    return _persist_reconciliation_result(
        db,
        snapshot=snapshot,
        result=result,
        required_confirmations=required,
        observed_at=observed_at,
    )