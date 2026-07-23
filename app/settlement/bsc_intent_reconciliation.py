from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass
from typing import Any

from web3 import Web3
from web3.exceptions import TransactionNotFound

from app.config import settings
from app.models import FundBscTransactionIntent
from app.settlement.erc20_receipt import (
    Erc20ReceiptError,
    exact_decimal_amount_to_raw,
    normalize_evm_address,
    normalize_transaction_hash,
    parse_exact_erc20_transfer_receipt,
)
from app.settlement.statuses import (
    BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT,
    BSC_INTENT_ACTION_NEGATIVE_SETTLEMENT_GAS_TOPUP,
    BSC_INTENT_STATUS_CONFIRMED,
    BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW,
    BSC_INTENT_STATUS_PENDING_CONFIRMATION,
    BSC_INTENT_STATUS_VISIBLE,
)


class BscIntentReconciliationError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class BscIntentReconciliationResult:
    action: str
    intent_id: int | None
    suggested_status: str
    tx_hash: str
    receipt_status: int | None
    block_number: int | None
    current_block: int | None
    confirmations: int
    required_confirmations: int
    reason_code: str | None
    error: str | None
    reconciliation_fingerprint: str | None
    evidence: dict[str, Any]


@dataclass(frozen=True)
class _ValidatedIntentContract:
    intent_id: int
    current_status: str
    intent_fingerprint: str
    action_type: str
    asset: str
    amount_raw: int
    amount_decimals: int
    chain_id: int
    source_nonce: int
    tx_hash: str
    from_address: str
    destination_address: str
    transaction_to_address: str
    transaction_value_raw: int
    token_contract: str | None


def _value_get(
    value: Any,
    key: str,
    default: Any = None,
) -> Any:
    if value is None:
        return default

    if hasattr(value, "get"):
        return value.get(key, default)

    return getattr(value, key, default)


def _safe_int(
    value: Any,
) -> int | None:
    try:
        if isinstance(value, bool):
            return None

        if (
            isinstance(value, str)
            and value.strip().lower().startswith("0x")
        ):
            return int(value.strip(), 16)

        return int(value)
    except (TypeError, ValueError):
        return None


def _required_positive_int(
    value: Any,
    *,
    field_name: str,
) -> int:
    normalized = _safe_int(value)

    if normalized is None or normalized <= 0:
        raise BscIntentReconciliationError(
            f"{field_name} must be a positive integer"
        )

    return normalized


def _required_nonnegative_int(
    value: Any,
    *,
    field_name: str,
) -> int:
    normalized = _safe_int(value)

    if normalized is None or normalized < 0:
        raise BscIntentReconciliationError(
            f"{field_name} must be a "
            "nonnegative integer"
        )

    return normalized


def _safe_intent_id(
    intent: FundBscTransactionIntent,
) -> int | None:
    normalized = _safe_int(intent.id)

    if normalized is None or normalized <= 0:
        return None

    return normalized


def _safe_tx_hash(
    intent: FundBscTransactionIntent,
) -> str:
    return str(
        intent.prepared_tx_hash or ""
    ).strip()[:128]


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


def _validated_required_confirmations(
    value: Any,
) -> int:
    return _required_positive_int(
        value,
        field_name="required_confirmations",
    )


def _validated_intent_contract(
    intent: FundBscTransactionIntent,
) -> _ValidatedIntentContract:
    intent_id = _required_positive_int(
        intent.id,
        field_name="intent.id",
    )
    chain_id = _required_positive_int(
        intent.chain_id,
        field_name="intent.chain_id",
    )
    source_nonce = _required_nonnegative_int(
        intent.source_nonce,
        field_name="intent.source_nonce",
    )

    try:
        tx_hash = normalize_transaction_hash(
            intent.prepared_tx_hash,
            field_name="intent.prepared_tx_hash",
        )
        from_address = normalize_evm_address(
            intent.from_address,
            field_name="intent.from_address",
        )
        destination_address = (
            normalize_evm_address(
                intent.to_address,
                field_name="intent.to_address",
            )
        )
    except Erc20ReceiptError as exc:
        raise BscIntentReconciliationError(
            "Persisted BSC intent address/hash "
            "contract is invalid"
        ) from exc

    action_type = str(
        intent.action_type or ""
    ).strip()
    asset = str(
        intent.asset or ""
    ).strip().upper()

    if (
        action_type
        == BSC_INTENT_ACTION_NEGATIVE_SETTLEMENT_GAS_TOPUP
    ):
        if asset != "BNB":
            raise BscIntentReconciliationError(
                "Gas top-up intent asset must be BNB"
            )

        try:
            amount_raw = (
                exact_decimal_amount_to_raw(
                    intent.amount,
                    decimals=18,
                )
            )
        except Erc20ReceiptError as exc:
            raise BscIntentReconciliationError(
                "Gas top-up intent amount is invalid"
            ) from exc

        amount_decimals = 18
        token_contract = None
        transaction_to_address = (
            destination_address
        )
        transaction_value_raw = amount_raw

    elif (
        action_type
        == BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT
    ):
        if asset != "USDT":
            raise BscIntentReconciliationError(
                "Redeem payout intent asset must be USDT"
            )

        try:
            token_contract = normalize_evm_address(
                settings.BSC_USDT_CONTRACT,
                field_name="BSC_USDT_CONTRACT",
            )
            amount_decimals = int(
                settings.BSC_USDT_DECIMALS
            )
            amount_raw = (
                exact_decimal_amount_to_raw(
                    intent.amount,
                    decimals=amount_decimals,
                )
            )
        except (
            Erc20ReceiptError,
            TypeError,
            ValueError,
        ) as exc:
            raise BscIntentReconciliationError(
                "Redeem payout USDT contract or "
                "amount is invalid"
            ) from exc

        transaction_to_address = token_contract
        transaction_value_raw = 0

    else:
        raise BscIntentReconciliationError(
            "Unsupported BSC intent action type"
        )

    return _ValidatedIntentContract(
        intent_id=intent_id,
        current_status=str(
            intent.status or ""
        ).strip(),
        intent_fingerprint=str(
            intent.intent_fingerprint or ""
        ).strip(),
        action_type=action_type,
        asset=asset,
        amount_raw=amount_raw,
        amount_decimals=amount_decimals,
        chain_id=chain_id,
        source_nonce=source_nonce,
        tx_hash=tx_hash,
        from_address=from_address,
        destination_address=(
            destination_address
        ),
        transaction_to_address=(
            transaction_to_address
        ),
        transaction_value_raw=(
            transaction_value_raw
        ),
        token_contract=token_contract,
    )


def _result(
    *,
    action: str,
    intent_id: int | None,
    suggested_status: str,
    tx_hash: str,
    required_confirmations: int,
    reason_code: str | None = None,
    error: str | None = None,
    receipt_status: int | None = None,
    block_number: int | None = None,
    current_block: int | None = None,
    confirmations: int = 0,
    reconciliation_fingerprint: (
        str | None
    ) = None,
    evidence: dict[str, Any] | None = None,
) -> BscIntentReconciliationResult:
    return BscIntentReconciliationResult(
        action=action,
        intent_id=intent_id,
        suggested_status=suggested_status,
        tx_hash=tx_hash,
        receipt_status=receipt_status,
        block_number=block_number,
        current_block=current_block,
        confirmations=confirmations,
        required_confirmations=(
            required_confirmations
        ),
        reason_code=reason_code,
        error=error,
        reconciliation_fingerprint=(
            reconciliation_fingerprint
        ),
        evidence=dict(evidence or {}),
    )


def _retryable_result(
    *,
    contract: _ValidatedIntentContract,
    required_confirmations: int,
    reason_code: str,
    error: BaseException,
    evidence: dict[str, Any] | None = None,
) -> BscIntentReconciliationResult:
    return _result(
        action="retryable_error",
        intent_id=contract.intent_id,
        suggested_status=(
            contract.current_status
        ),
        tx_hash=contract.tx_hash,
        required_confirmations=(
            required_confirmations
        ),
        reason_code=reason_code,
        error=_safe_error_text(error),
        evidence=evidence,
    )


def _failed_result(
    *,
    contract: _ValidatedIntentContract,
    required_confirmations: int,
    reason_code: str,
    error: str,
    receipt_status: int | None = None,
    block_number: int | None = None,
    current_block: int | None = None,
    confirmations: int = 0,
    evidence: dict[str, Any] | None = None,
    reconciliation_fingerprint: (
        str | None
    ) = None,
) -> BscIntentReconciliationResult:
    return _result(
        action="failed_requires_review",
        intent_id=contract.intent_id,
        suggested_status=(
            BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
        ),
        tx_hash=contract.tx_hash,
        required_confirmations=(
            required_confirmations
        ),
        reason_code=reason_code,
        error=str(error)[:512],
        receipt_status=receipt_status,
        block_number=block_number,
        current_block=current_block,
        confirmations=confirmations,
        evidence=evidence,
        reconciliation_fingerprint=(
            reconciliation_fingerprint
        ),
    )


def _canonical_reconciliation_fingerprint(
    *,
    contract: _ValidatedIntentContract,
    transaction_evidence: dict[str, Any],
    receipt_status: int,
    block_number: int,
    current_block: int | None,
    confirmations: int,
    erc20_receipt_fingerprint: str | None,
) -> str:
    payload = {
        "schema": (
            "fund_bsc_transaction_intent_"
            "reconciliation_v1"
        ),
        "intent_id": contract.intent_id,
        "intent_fingerprint": (
            contract.intent_fingerprint
        ),
        "action_type": contract.action_type,
        "asset": contract.asset,
        "amount_raw": str(
            contract.amount_raw
        ),
        "amount_decimals": (
            contract.amount_decimals
        ),
        "chain_id": contract.chain_id,
        "source_nonce": contract.source_nonce,
        "tx_hash": contract.tx_hash,
        "from_address": (
            contract.from_address
        ),
        "destination_address": (
            contract.destination_address
        ),
        "transaction": transaction_evidence,
        "receipt": {
            "status": receipt_status,
            "block_number": block_number,
        },
        "erc20_receipt_fingerprint": (
            erc20_receipt_fingerprint
        ),
    }

    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def _transaction_evidence(
    transaction: Any,
    *,
    contract: _ValidatedIntentContract,
) -> tuple[
    dict[str, Any],
    list[str],
]:
    try:
        actual_hash = (
            normalize_transaction_hash(
                _value_get(
                    transaction,
                    "hash",
                ),
                field_name="transaction.hash",
            )
        )
        actual_from = normalize_evm_address(
            _value_get(
                transaction,
                "from",
            ),
            field_name="transaction.from",
        )
        actual_to = normalize_evm_address(
            _value_get(
                transaction,
                "to",
            ),
            field_name="transaction.to",
        )
        actual_nonce = (
            _required_nonnegative_int(
                _value_get(
                    transaction,
                    "nonce",
                ),
                field_name="transaction.nonce",
            )
        )
        actual_value = (
            _required_nonnegative_int(
                _value_get(
                    transaction,
                    "value",
                    0,
                ),
                field_name="transaction.value",
            )
        )
    except (
        Erc20ReceiptError,
        BscIntentReconciliationError,
    ) as exc:
        raise BscIntentReconciliationError(
            "Visible transaction envelope "
            "is invalid"
        ) from exc

    evidence = {
        "hash": actual_hash,
        "from_address": actual_from,
        "to_address": actual_to,
        "nonce": actual_nonce,
        "value_raw": str(actual_value),
    }

    mismatch_fields: list[str] = []

    if actual_hash != contract.tx_hash:
        mismatch_fields.append("transaction.hash")

    if actual_from != contract.from_address:
        mismatch_fields.append("transaction.from")

    if (
        actual_to
        != contract.transaction_to_address
    ):
        mismatch_fields.append("transaction.to")

    if actual_nonce != contract.source_nonce:
        mismatch_fields.append("transaction.nonce")

    if (
        actual_value
        != contract.transaction_value_raw
    ):
        mismatch_fields.append("transaction.value")

    return evidence, mismatch_fields


def reconcile_bsc_transaction_intent(
    w3: Web3,
    *,
    intent: FundBscTransactionIntent,
    required_confirmations: int,
) -> BscIntentReconciliationResult:
    try:
        required = (
            _validated_required_confirmations(
                required_confirmations
            )
        )
    except BscIntentReconciliationError:
        required = 1

    try:
        contract = _validated_intent_contract(
            intent
        )
    except BscIntentReconciliationError as exc:
        return _result(
            action="failed_requires_review",
            intent_id=_safe_intent_id(intent),
            suggested_status=(
                BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
            ),
            tx_hash=_safe_tx_hash(intent),
            required_confirmations=required,
            reason_code=(
                "stored_intent_contract_invalid"
            ),
            error=str(exc)[:512],
            evidence={
                "intent_status": str(
                    intent.status or ""
                ),
            },
        )

    try:
        required = (
            _validated_required_confirmations(
                required_confirmations
            )
        )
    except BscIntentReconciliationError as exc:
        return _failed_result(
            contract=contract,
            required_confirmations=1,
            reason_code=(
                "required_confirmations_invalid"
            ),
            error=str(exc),
        )

    try:
        current_chain_id = (
            _required_positive_int(
                w3.eth.chain_id,
                field_name="web3.chain_id",
            )
        )
    except Exception as exc:
        return _retryable_result(
            contract=contract,
            required_confirmations=required,
            reason_code="chain_id_unavailable",
            error=exc,
        )

    if current_chain_id != contract.chain_id:
        return _failed_result(
            contract=contract,
            required_confirmations=required,
            reason_code="chain_id_mismatch",
            error=(
                "BSC chain id mismatch: "
                f"expected={contract.chain_id} "
                f"actual={current_chain_id}"
            ),
            evidence={
                "expected_chain_id": (
                    contract.chain_id
                ),
                "actual_chain_id": (
                    current_chain_id
                ),
            },
        )

    try:
        transaction = w3.eth.get_transaction(
            contract.tx_hash
        )
    except TransactionNotFound:
        return _result(
            action="not_visible",
            intent_id=contract.intent_id,
            suggested_status=(
                contract.current_status
            ),
            tx_hash=contract.tx_hash,
            required_confirmations=required,
            reason_code=(
                "transaction_not_visible"
            ),
            evidence={
                "chain_id": current_chain_id,
            },
        )
    except Exception as exc:
        return _retryable_result(
            contract=contract,
            required_confirmations=required,
            reason_code=(
                "transaction_lookup_unavailable"
            ),
            error=exc,
        )

    if transaction is None:
        return _result(
            action="not_visible",
            intent_id=contract.intent_id,
            suggested_status=(
                contract.current_status
            ),
            tx_hash=contract.tx_hash,
            required_confirmations=required,
            reason_code=(
                "transaction_not_visible"
            ),
            evidence={
                "chain_id": current_chain_id,
            },
        )

    try:
        transaction_evidence, mismatch_fields = (
            _transaction_evidence(
                transaction,
                contract=contract,
            )
        )
    except BscIntentReconciliationError as exc:
        return _failed_result(
            contract=contract,
            required_confirmations=required,
            reason_code=(
                "transaction_envelope_invalid"
            ),
            error=str(exc),
        )

    if mismatch_fields:
        return _failed_result(
            contract=contract,
            required_confirmations=required,
            reason_code=(
                "transaction_envelope_mismatch"
            ),
            error=(
                "Visible transaction envelope "
                "does not match durable intent"
            ),
            evidence={
                "mismatch_fields": sorted(
                    mismatch_fields
                ),
                "transaction": (
                    transaction_evidence
                ),
            },
        )

    try:
        receipt = (
            w3.eth.get_transaction_receipt(
                contract.tx_hash
            )
        )
    except TransactionNotFound:
        return _result(
            action="visible",
            intent_id=contract.intent_id,
            suggested_status=(
                BSC_INTENT_STATUS_VISIBLE
            ),
            tx_hash=contract.tx_hash,
            required_confirmations=required,
            reason_code="receipt_not_visible",
            evidence={
                "chain_id": current_chain_id,
                "transaction": (
                    transaction_evidence
                ),
            },
        )
    except Exception as exc:
        return _retryable_result(
            contract=contract,
            required_confirmations=required,
            reason_code=(
                "receipt_lookup_unavailable"
            ),
            error=exc,
            evidence={
                "transaction": transaction_evidence,
            },
        )

    if receipt is None:
        return _result(
            action="visible",
            intent_id=contract.intent_id,
            suggested_status=(
                BSC_INTENT_STATUS_VISIBLE
            ),
            tx_hash=contract.tx_hash,
            required_confirmations=required,
            reason_code="receipt_not_visible",
            evidence={
                "chain_id": current_chain_id,
                "transaction": (
                    transaction_evidence
                ),
            },
        )

    try:
        receipt_hash = (
            normalize_transaction_hash(
                _value_get(
                    receipt,
                    "transactionHash",
                ),
                field_name=(
                    "receipt.transactionHash"
                ),
            )
        )
    except Erc20ReceiptError as exc:
        return _failed_result(
            contract=contract,
            required_confirmations=required,
            reason_code=(
                "receipt_transaction_hash_invalid"
            ),
            error=str(exc),
        )

    if receipt_hash != contract.tx_hash:
        return _failed_result(
            contract=contract,
            required_confirmations=required,
            reason_code=(
                "receipt_transaction_hash_mismatch"
            ),
            error=(
                "Receipt transaction hash does not "
                "match durable intent"
            ),
            evidence={
                "receipt_tx_hash": receipt_hash,
                "expected_tx_hash": contract.tx_hash,
            },
        )

    receipt_status = _safe_int(
        _value_get(
            receipt,
            "status",
        )
    )

    if receipt_status not in {0, 1}:
        return _failed_result(
            contract=contract,
            required_confirmations=required,
            reason_code=(
                "receipt_status_invalid"
            ),
            error=(
                "Receipt status must be 0 or 1"
            ),
            evidence={
                "receipt_status": receipt_status,
            },
        )

    block_number = _safe_int(
        _value_get(
            receipt,
            "blockNumber",
        )
    )

    if (
        block_number is None
        or block_number < 0
    ):
        return _failed_result(
            contract=contract,
            required_confirmations=required,
            reason_code=(
                "receipt_block_number_invalid"
            ),
            error=(
                "Receipt block number is invalid"
            ),
            receipt_status=receipt_status,
        )

    if receipt_status == 0:
        fingerprint = (
            _canonical_reconciliation_fingerprint(
                contract=contract,
                transaction_evidence=(
                    transaction_evidence
                ),
                receipt_status=receipt_status,
                block_number=block_number,
                current_block=None,
                confirmations=0,
                erc20_receipt_fingerprint=None,
            )
        )

        return _failed_result(
            contract=contract,
            required_confirmations=required,
            reason_code="receipt_execution_failed",
            error=(
                "BSC transaction receipt status is 0"
            ),
            receipt_status=receipt_status,
            block_number=block_number,
            evidence={
                "transaction": (
                    transaction_evidence
                ),
            },
            reconciliation_fingerprint=(
                fingerprint
            ),
        )

    try:
        current_block = (
            _required_nonnegative_int(
                w3.eth.block_number,
                field_name="web3.block_number",
            )
        )
    except Exception as exc:
        return _retryable_result(
            contract=contract,
            required_confirmations=required,
            reason_code=(
                "current_block_unavailable"
            ),
            error=exc,
            evidence={
                "receipt_status": receipt_status,
                "block_number": block_number,
                "transaction": (
                    transaction_evidence
                ),
            },
        )

    if current_block < block_number:
        return _retryable_result(
            contract=contract,
            required_confirmations=required,
            reason_code=(
                "receipt_block_ahead_of_chain_head"
            ),
            error=BscIntentReconciliationError(
                "Receipt block is ahead of "
                "current chain head"
            ),
            evidence={
                "block_number": block_number,
                "current_block": current_block,
            },
        )

    confirmations = (
        current_block - block_number + 1
    )

    erc20_fingerprint: str | None = None
    erc20_evidence: dict[str, Any] | None = None

    if (
        contract.action_type
        == BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT
    ):
        try:
            exact_match = (
                parse_exact_erc20_transfer_receipt(
                    receipt,
                    transaction_hash=(
                        contract.tx_hash
                    ),
                    token_contract=(
                        contract.token_contract
                    ),
                    destination_address=(
                        contract.destination_address
                    ),
                    expected_source_address=(
                        contract.from_address
                    ),
                    expected_amount=intent.amount,
                    decimals=(
                        contract.amount_decimals
                    ),
                )
            )
        except Erc20ReceiptError as exc:
            return _failed_result(
                contract=contract,
                required_confirmations=required,
                reason_code=(
                    "erc20_transfer_receipt_mismatch"
                ),
                error=str(exc),
                receipt_status=receipt_status,
                block_number=block_number,
                current_block=current_block,
                confirmations=confirmations,
                evidence={
                    "transaction": (
                        transaction_evidence
                    ),
                },
            )

        erc20_fingerprint = (
            exact_match.receipt_fingerprint
        )
        erc20_evidence = {
            "contract_address": (
                exact_match.contract_address
            ),
            "destination_address": (
                exact_match.destination_address
            ),
            "expected_source_address": (
                exact_match.expected_source_address
            ),
            "expected_amount_raw": str(
                exact_match.expected_amount_raw
            ),
            "received_amount_raw": str(
                exact_match.received_amount_raw
            ),
            "matching_log_indexes": [
                transfer.log_index
                for transfer
                in exact_match.transfers
            ],
            "receipt_fingerprint": (
                exact_match.receipt_fingerprint
            ),
        }

    fingerprint = (
        _canonical_reconciliation_fingerprint(
            contract=contract,
            transaction_evidence=(
                transaction_evidence
            ),
            receipt_status=receipt_status,
            block_number=block_number,
            current_block=current_block,
            confirmations=confirmations,
            erc20_receipt_fingerprint=(
                erc20_fingerprint
            ),
        )
    )

    evidence = {
        "chain_id": current_chain_id,
        "transaction": transaction_evidence,
        "receipt": {
            "status": receipt_status,
            "block_number": block_number,
            "current_block": current_block,
            "confirmations": confirmations,
            "required_confirmations": required,
        },
        "erc20_transfer": erc20_evidence,
        "reconciliation_fingerprint": (
            fingerprint
        ),
    }

    if confirmations < required:
        return _result(
            action="pending_confirmation",
            intent_id=contract.intent_id,
            suggested_status=(
                BSC_INTENT_STATUS_PENDING_CONFIRMATION
            ),
            tx_hash=contract.tx_hash,
            required_confirmations=required,
            receipt_status=receipt_status,
            block_number=block_number,
            current_block=current_block,
            confirmations=confirmations,
            reason_code=(
                "insufficient_confirmations"
            ),
            reconciliation_fingerprint=(
                fingerprint
            ),
            evidence=evidence,
        )

    return _result(
        action="confirmed",
        intent_id=contract.intent_id,
        suggested_status=(
            BSC_INTENT_STATUS_CONFIRMED
        ),
        tx_hash=contract.tx_hash,
        required_confirmations=required,
        receipt_status=receipt_status,
        block_number=block_number,
        current_block=current_block,
        confirmations=confirmations,
        reconciliation_fingerprint=(
            fingerprint
        ),
        evidence=evidence,
    )