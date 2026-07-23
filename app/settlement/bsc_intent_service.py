from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session
from web3 import Web3
from web3.exceptions import TransactionNotFound

from app.config import settings
from app.models import FundSettlementTransfer
from app.settlement.statuses import (
    BSC_INTENT_ACTION_TYPES,
    TRANSFER_STATUS_CONFIRMED,
    TRANSFER_STATUS_PREPARED,
    TRANSFER_STATUS_PROCESSING,
    TRANSFER_STATUS_SENT,
)


WEI_PER_BNB = Decimal("1000000000000000000")

ERC20_TRANSFER_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    }
]


class BscIntentError(RuntimeError):
    pass


@dataclass(frozen=True)
class PreparedBscTransaction:
    chain_id: int
    source_nonce: int
    tx_hash: str
    raw_tx_hex: str


@dataclass(frozen=True)
class BroadcastBscTransactionResult:
    action: str
    tx_hash: str


def _positive_intent_id(
    value: Any,
    *,
    field_name: str,
) -> int:
    try:
        normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise BscIntentError(
            f"{field_name} must be an integer"
        ) from exc

    if normalized <= 0:
        raise BscIntentError(
            f"{field_name} must be positive"
        )

    return normalized


def _optional_positive_intent_id(
    value: Any,
    *,
    field_name: str,
) -> int | None:
    if value is None:
        return None

    return _positive_intent_id(
        value,
        field_name=field_name,
    )


def _canonical_intent_amount(value: Any) -> str:
    if isinstance(value, float):
        raise BscIntentError(
            "Float BSC intent amount is forbidden"
        )

    try:
        amount = Decimal(str(value))
    except Exception as exc:
        raise BscIntentError(
            "BSC intent amount is invalid"
        ) from exc

    if not amount.is_finite():
        raise BscIntentError(
            "BSC intent amount must be finite"
        )

    if amount <= 0:
        raise BscIntentError(
            "BSC intent amount must be positive"
        )

    return format(amount.normalize(), "f")


def _normalize_intent_scope_key(value: Any) -> str:
    normalized = str(value or "").strip()

    if not normalized:
        raise BscIntentError(
            "BSC intent scope_key is empty"
        )

    if len(normalized) > 192:
        raise BscIntentError(
            "BSC intent scope_key is too long"
        )

    return normalized


def _normalize_intent_action_type(value: Any) -> str:
    normalized = str(value or "").strip()

    if normalized not in BSC_INTENT_ACTION_TYPES:
        raise BscIntentError(
            "Unsupported BSC intent action_type"
        )

    return normalized


def _normalize_intent_asset(value: Any) -> str:
    normalized = str(value or "").strip().upper()

    if not normalized:
        raise BscIntentError(
            "BSC intent asset is empty"
        )

    if len(normalized) > 16:
        raise BscIntentError(
            "BSC intent asset is too long"
        )

    return normalized


def _normalize_intent_address(
    value: Any,
    *,
    field_name: str,
) -> str:
    normalized = str(value or "").strip().lower()

    if not normalized:
        raise BscIntentError(
            f"{field_name} is empty"
        )

    if len(normalized) > 128:
        raise BscIntentError(
            f"{field_name} is too long"
        )

    return normalized


def _normalize_prepared_tx_hash(value: Any) -> str:
    normalized = str(value or "").strip()
    hex_value = (
        normalized[2:]
        if normalized.lower().startswith("0x")
        else normalized
    )

    try:
        raw_hash = bytes.fromhex(hex_value)
    except ValueError as exc:
        raise BscIntentError(
            "Prepared transaction hash is not valid hex"
        ) from exc

    if len(raw_hash) != 32:
        raise BscIntentError(
            "Prepared transaction hash must be 32 bytes"
        )

    return f"0x{raw_hash.hex()}"


def _bsc_intent_fingerprint_payload(
    *,
    scope_key: str,
    action_type: str,
    settlement_batch_id: int,
    payout_batch_id: int,
    payout_leg_id: int | None,
    fund_id: int,
    asset: str,
    amount: Decimal,
    from_address: str,
    to_address: str,
    prepared: PreparedBscTransaction,
) -> dict[str, Any]:
    chain_id = int(prepared.chain_id)
    source_nonce = int(prepared.source_nonce)

    if chain_id <= 0:
        raise BscIntentError(
            "BSC intent chain_id must be positive"
        )

    if source_nonce < 0:
        raise BscIntentError(
            "BSC intent source_nonce cannot be negative"
        )

    raw_transaction = _raw_transaction_bytes(
        prepared.raw_tx_hex
    )

    return {
        "schema": (
            "fund_bsc_transaction_intent_fingerprint_v1"
        ),
        "scope_key": _normalize_intent_scope_key(
            scope_key
        ),
        "action_type": _normalize_intent_action_type(
            action_type
        ),
        "settlement_batch_id": _positive_intent_id(
            settlement_batch_id,
            field_name="settlement_batch_id",
        ),
        "payout_batch_id": _positive_intent_id(
            payout_batch_id,
            field_name="payout_batch_id",
        ),
        "payout_leg_id": _optional_positive_intent_id(
            payout_leg_id,
            field_name="payout_leg_id",
        ),
        "fund_id": _positive_intent_id(
            fund_id,
            field_name="fund_id",
        ),
        "asset": _normalize_intent_asset(asset),
        "amount": _canonical_intent_amount(amount),
        "from_address": _normalize_intent_address(
            from_address,
            field_name="from_address",
        ),
        "to_address": _normalize_intent_address(
            to_address,
            field_name="to_address",
        ),
        "chain_id": chain_id,
        "source_nonce": source_nonce,
        "prepared_tx_hash": (
            _normalize_prepared_tx_hash(
                prepared.tx_hash
            )
        ),
        "raw_transaction_sha256": hashlib.sha256(
            raw_transaction
        ).hexdigest(),
    }


def build_bsc_intent_fingerprint(
    *,
    scope_key: str,
    action_type: str,
    settlement_batch_id: int,
    payout_batch_id: int,
    payout_leg_id: int | None,
    fund_id: int,
    asset: str,
    amount: Decimal,
    from_address: str,
    to_address: str,
    prepared: PreparedBscTransaction,
) -> str:
    payload = _bsc_intent_fingerprint_payload(
        scope_key=scope_key,
        action_type=action_type,
        settlement_batch_id=settlement_batch_id,
        payout_batch_id=payout_batch_id,
        payout_leg_id=payout_leg_id,
        fund_id=fund_id,
        asset=asset,
        amount=amount,
        from_address=from_address,
        to_address=to_address,
        prepared=prepared,
    )

    canonical_json = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )

    return hashlib.sha256(
        canonical_json.encode("utf-8")
    ).hexdigest()


def build_bsc_intent_safe_audit(
    *,
    scope_key: str,
    action_type: str,
    settlement_batch_id: int,
    payout_batch_id: int,
    payout_leg_id: int | None,
    fund_id: int,
    asset: str,
    amount: Decimal,
    from_address: str,
    to_address: str,
    prepared: PreparedBscTransaction,
) -> dict[str, Any]:
    payload = _bsc_intent_fingerprint_payload(
        scope_key=scope_key,
        action_type=action_type,
        settlement_batch_id=settlement_batch_id,
        payout_batch_id=payout_batch_id,
        payout_leg_id=payout_leg_id,
        fund_id=fund_id,
        asset=asset,
        amount=amount,
        from_address=from_address,
        to_address=to_address,
        prepared=prepared,
    )

    return {
        **payload,
        "schema": "fund_bsc_transaction_intent_audit_v1",
        "intent_fingerprint": (
            build_bsc_intent_fingerprint(
                scope_key=scope_key,
                action_type=action_type,
                settlement_batch_id=(
                    settlement_batch_id
                ),
                payout_batch_id=payout_batch_id,
                payout_leg_id=payout_leg_id,
                fund_id=fund_id,
                asset=asset,
                amount=amount,
                from_address=from_address,
                to_address=to_address,
                prepared=prepared,
            )
        ),
    }


def _normalize_private_key(private_key: str) -> str:
    value = str(private_key or "").strip()

    if not value:
        raise BscIntentError("Private key is empty")

    if not value.startswith("0x"):
        value = f"0x{value}"

    return value


def _checksum(
    w3: Web3,
    address: str,
) -> str:
    value = str(address or "").strip()

    if not value:
        raise BscIntentError("Address is empty")

    return w3.to_checksum_address(value)


def _signed_raw_transaction(
    signed: Any,
) -> Any:
    raw_tx = getattr(signed, "rawTransaction", None)

    if raw_tx is None:
        raw_tx = getattr(signed, "raw_transaction", None)

    if raw_tx is None:
        raise BscIntentError(
            "Signed transaction has no raw transaction bytes"
        )

    return raw_tx


def _prepared_from_signed(
    w3: Web3,
    *,
    signed: Any,
    chain_id: int,
    source_nonce: int,
) -> PreparedBscTransaction:
    raw_tx = _signed_raw_transaction(signed)

    signed_hash = getattr(signed, "hash", None)

    if signed_hash is None:
        tx_hash = w3.keccak(raw_tx)
    else:
        tx_hash = signed_hash

    return PreparedBscTransaction(
        chain_id=int(chain_id),
        source_nonce=int(source_nonce),
        tx_hash=w3.to_hex(tx_hash),
        raw_tx_hex=w3.to_hex(raw_tx),
    )


def prepare_native_bnb_transaction(
    w3: Web3,
    *,
    from_private_key: str,
    from_address: str,
    to_address: str,
    amount_bnb: Decimal,
) -> PreparedBscTransaction:
    amount = Decimal(str(amount_bnb))

    if amount <= 0:
        raise BscIntentError(
            f"Invalid BNB amount: {amount}"
        )

    private_key = _normalize_private_key(
        from_private_key
    )
    from_checksum = _checksum(
        w3,
        from_address,
    )
    to_checksum = _checksum(
        w3,
        to_address,
    )

    chain_id = int(w3.eth.chain_id)
    source_nonce = int(
        w3.eth.get_transaction_count(
            from_checksum,
            "pending",
        )
    )
    gas_price = int(w3.eth.gas_price)
    value_wei = int(amount * WEI_PER_BNB)

    tx = {
        "to": to_checksum,
        "value": value_wei,
        "gas": 21000,
        "gasPrice": gas_price,
        "nonce": source_nonce,
        "chainId": chain_id,
    }

    signed = w3.eth.account.sign_transaction(
        tx,
        private_key,
    )

    return _prepared_from_signed(
        w3,
        signed=signed,
        chain_id=chain_id,
        source_nonce=source_nonce,
    )


def prepare_usdt_transfer_transaction(
    w3: Web3,
    *,
    from_private_key: str,
    from_address: str,
    to_address: str,
    amount_usdt: Decimal,
) -> PreparedBscTransaction:
    amount = Decimal(str(amount_usdt))

    if amount <= 0:
        raise BscIntentError(
            f"Invalid USDT amount: {amount}"
        )

    if not settings.BSC_USDT_CONTRACT:
        raise BscIntentError(
            "BSC_USDT_CONTRACT is not configured"
        )

    private_key = _normalize_private_key(
        from_private_key
    )
    from_checksum = _checksum(
        w3,
        from_address,
    )
    to_checksum = _checksum(
        w3,
        to_address,
    )

    contract = w3.eth.contract(
        address=_checksum(
            w3,
            settings.BSC_USDT_CONTRACT,
        ),
        abi=ERC20_TRANSFER_ABI,
    )

    decimals = int(settings.BSC_USDT_DECIMALS)
    amount_raw = int(
        amount * (Decimal(10) ** decimals)
    )

    chain_id = int(w3.eth.chain_id)
    source_nonce = int(
        w3.eth.get_transaction_count(
            from_checksum,
            "pending",
        )
    )
    gas_price = int(w3.eth.gas_price)

    tx = contract.functions.transfer(
        to_checksum,
        amount_raw,
    ).build_transaction(
        {
            "from": from_checksum,
            "nonce": source_nonce,
            "gasPrice": gas_price,
            "chainId": chain_id,
        }
    )

    if "gas" not in tx or not tx["gas"]:
        tx["gas"] = int(
            settings.ERC20_TRANSFER_GAS_FALLBACK
        )

    signed = w3.eth.account.sign_transaction(
        tx,
        private_key,
    )

    return _prepared_from_signed(
        w3,
        signed=signed,
        chain_id=chain_id,
        source_nonce=source_nonce,
    )


def _raw_transaction_bytes(
    raw_tx_hex: str,
) -> bytes:
    value = str(raw_tx_hex or "").strip()

    if value.startswith("0x"):
        value = value[2:]

    if not value:
        raise BscIntentError(
            "Prepared raw transaction is empty"
        )

    try:
        return bytes.fromhex(value)
    except ValueError as exc:
        raise BscIntentError(
            "Prepared raw transaction is not valid hex"
        ) from exc


def _prepared_transaction_is_visible(
    w3: Web3,
    *,
    tx_hash: str,
) -> bool:
    try:
        transaction = w3.eth.get_transaction(
            tx_hash
        )
    except TransactionNotFound:
        return False
    except Exception as exc:
        raise BscIntentError(
            "Cannot reconcile prepared transaction "
            f"by hash={tx_hash}: {exc}"
        ) from exc

    return transaction is not None


def broadcast_prepared_transaction(
    w3: Web3,
    *,
    prepared_tx_hash: str,
    raw_tx_hex: str,
    from_address: str,
    chain_id: int,
    source_nonce: int,
) -> BroadcastBscTransactionResult:
    tx_hash = str(
        prepared_tx_hash or ""
    ).strip()

    if not tx_hash:
        raise BscIntentError(
            "Prepared transaction hash is empty"
        )

    expected_chain_id = int(chain_id)
    current_chain_id = int(w3.eth.chain_id)

    if current_chain_id != expected_chain_id:
        raise BscIntentError(
            "Prepared transaction chain mismatch: "
            f"prepared_chain_id={expected_chain_id} "
            f"current_chain_id={current_chain_id}"
        )

    from_checksum = _checksum(
        w3,
        from_address,
    )
    nonce = int(source_nonce)

    if _prepared_transaction_is_visible(
        w3,
        tx_hash=tx_hash,
    ):
        return BroadcastBscTransactionResult(
            action="already_visible",
            tx_hash=tx_hash,
        )

    try:
        pending_nonce = int(
            w3.eth.get_transaction_count(
                from_checksum,
                "pending",
            )
        )
    except Exception as exc:
        raise BscIntentError(
            "Cannot reconcile prepared transaction "
            f"nonce for hash={tx_hash}: {exc}"
        ) from exc

    if pending_nonce > nonce:
        raise BscIntentError(
            "Prepared transaction is not visible by hash, "
            "but its source nonce may already be consumed: "
            f"hash={tx_hash} "
            f"source_nonce={nonce} "
            f"pending_nonce={pending_nonce}"
        )

    raw_tx = _raw_transaction_bytes(
        raw_tx_hex
    )

    try:
        sent_hash = w3.eth.send_raw_transaction(
            raw_tx
        )
    except Exception as exc:
        if _prepared_transaction_is_visible(
            w3,
            tx_hash=tx_hash,
        ):
            return BroadcastBscTransactionResult(
                action="visible_after_broadcast_error",
                tx_hash=tx_hash,
            )

        try:
            refreshed_pending_nonce = int(
                w3.eth.get_transaction_count(
                    from_checksum,
                    "pending",
                )
            )
        except Exception as nonce_exc:
            raise BscIntentError(
                "Prepared transaction broadcast outcome "
                "is ambiguous and nonce reconciliation failed: "
                f"hash={tx_hash}; "
                f"broadcast_error={exc}; "
                f"nonce_error={nonce_exc}"
            ) from exc

        if refreshed_pending_nonce > nonce:
            raise BscIntentError(
                "Prepared transaction broadcast outcome "
                "is ambiguous because its nonce is consumed "
                "but the transaction is not visible by hash: "
                f"hash={tx_hash} "
                f"source_nonce={nonce} "
                f"pending_nonce={refreshed_pending_nonce}"
            ) from exc

        raise BscIntentError(
            "Prepared transaction was not broadcast and "
            "its nonce remains available: "
            f"hash={tx_hash}; error={exc}"
        ) from exc

    actual_tx_hash = w3.to_hex(
        sent_hash
    )

    if actual_tx_hash.lower() != tx_hash.lower():
        raise BscIntentError(
            "Broadcast transaction hash mismatch: "
            f"prepared_hash={tx_hash} "
            f"broadcast_hash={actual_tx_hash}"
        )

    return BroadcastBscTransactionResult(
        action="broadcast",
        tx_hash=actual_tx_hash,
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def prepared_transaction_from_transfer(
    transfer: FundSettlementTransfer,
) -> PreparedBscTransaction | None:
    prepared_tx_hash = str(
        transfer.prepared_tx_hash or ""
    ).strip()
    prepared_raw_tx = str(
        transfer.prepared_raw_tx or ""
    ).strip()

    has_any_prepared_field = bool(
        prepared_tx_hash
        or prepared_raw_tx
        or transfer.chain_id is not None
        or transfer.source_nonce is not None
    )

    if not has_any_prepared_field:
        return None

    if (
        not prepared_tx_hash
        or not prepared_raw_tx
        or transfer.chain_id is None
        or transfer.source_nonce is None
    ):
        raise BscIntentError(
            "Settlement transfer contains incomplete "
            "prepared transaction intent: "
            f"transfer_id={transfer.id}"
        )

    return PreparedBscTransaction(
        chain_id=int(transfer.chain_id),
        source_nonce=int(transfer.source_nonce),
        tx_hash=prepared_tx_hash,
        raw_tx_hex=prepared_raw_tx,
    )


def persist_prepared_transfer_intent(
    db: Session,
    *,
    transfer_id: int,
    request_key: str,
    prepared: PreparedBscTransaction,
) -> FundSettlementTransfer:
    normalized_request_key = str(
        request_key or ""
    ).strip()

    if not normalized_request_key:
        raise BscIntentError(
            "Prepared transfer request key is empty"
        )

    transfer = (
        db.query(FundSettlementTransfer)
        .filter(
            FundSettlementTransfer.id
            == int(transfer_id)
        )
        .with_for_update()
        .first()
    )

    if transfer is None:
        raise BscIntentError(
            "Settlement transfer not found while "
            "persisting prepared intent: "
            f"transfer_id={transfer_id}"
        )

    if (
        transfer.request_key
        and str(transfer.request_key)
        != normalized_request_key
    ):
        raise BscIntentError(
            "Settlement transfer request key mismatch: "
            f"transfer_id={transfer.id}"
        )

    existing_prepared = (
        prepared_transaction_from_transfer(
            transfer
        )
    )

    if existing_prepared is not None:
        if (
            existing_prepared.chain_id
            != prepared.chain_id
            or existing_prepared.source_nonce
            != prepared.source_nonce
            or existing_prepared.tx_hash.lower()
            != prepared.tx_hash.lower()
            or existing_prepared.raw_tx_hex.lower()
            != prepared.raw_tx_hex.lower()
        ):
            raise BscIntentError(
                "Prepared transaction intent mismatch: "
                f"transfer_id={transfer.id}"
            )

        return transfer

    if transfer.tx_hash:
        raise BscIntentError(
            "Cannot prepare a new intent for a transfer "
            "that already has tx_hash: "
            f"transfer_id={transfer.id}"
        )

    now = utcnow()

    transfer.request_key = normalized_request_key
    transfer.chain_id = int(
        prepared.chain_id
    )
    transfer.source_nonce = int(
        prepared.source_nonce
    )
    transfer.prepared_tx_hash = str(
        prepared.tx_hash
    )
    transfer.prepared_raw_tx = str(
        prepared.raw_tx_hex
    )
    transfer.prepared_at = (
        transfer.prepared_at or now
    )
    transfer.status = TRANSFER_STATUS_PREPARED
    transfer.error = None
    transfer.updated_at = now

    db.add(transfer)

    # Required durable boundary:
    # prepared intent must exist before BSC broadcast.
    db.commit()
    db.refresh(transfer)

    return transfer


def persist_broadcast_transfer_result(
    db: Session,
    *,
    transfer_id: int,
    tx_hash: str,
    copy_to_gas_tx_hash: bool = False,
) -> FundSettlementTransfer:
    normalized_tx_hash = str(
        tx_hash or ""
    ).strip()

    if not normalized_tx_hash:
        raise BscIntentError(
            "Broadcast transaction hash is empty"
        )

    transfer = (
        db.query(FundSettlementTransfer)
        .filter(
            FundSettlementTransfer.id
            == int(transfer_id)
        )
        .with_for_update()
        .first()
    )

    if transfer is None:
        raise BscIntentError(
            "Settlement transfer not found while "
            "persisting broadcast result: "
            f"transfer_id={transfer_id}"
        )

    if (
        transfer.prepared_tx_hash
        and str(
            transfer.prepared_tx_hash
        ).lower()
        != normalized_tx_hash.lower()
    ):
        raise BscIntentError(
            "Prepared and broadcast transaction "
            "hashes differ: "
            f"transfer_id={transfer.id}"
        )

    if transfer.tx_hash:
        if (
            str(transfer.tx_hash).lower()
            != normalized_tx_hash.lower()
        ):
            raise BscIntentError(
                "Settlement transfer already contains "
                "another tx_hash: "
                f"transfer_id={transfer.id}"
            )

        return transfer

    now = utcnow()

    transfer.tx_hash = normalized_tx_hash

    if copy_to_gas_tx_hash:
        transfer.gas_tx_hash = (
            normalized_tx_hash
        )

    transfer.status = TRANSFER_STATUS_SENT
    transfer.broadcast_at = (
        transfer.broadcast_at or now
    )
    transfer.sent_at = transfer.sent_at or now
    transfer.error = None
    transfer.updated_at = now

    db.add(transfer)

    # Required durable boundary:
    # save tx_hash immediately after broadcast.
    db.commit()
    db.refresh(transfer)

    return transfer


def persist_transfer_intent_processing(
    db: Session,
    *,
    transfer_id: int,
    error: str,
) -> FundSettlementTransfer:
    transfer = (
        db.query(FundSettlementTransfer)
        .filter(
            FundSettlementTransfer.id
            == int(transfer_id)
        )
        .with_for_update()
        .first()
    )

    if transfer is None:
        raise BscIntentError(
            "Settlement transfer not found while "
            "persisting reconciliation error: "
            f"transfer_id={transfer_id}"
        )

    if (
        transfer.tx_hash
        or transfer.status
        in {
            TRANSFER_STATUS_SENT,
            TRANSFER_STATUS_CONFIRMED,
        }
    ):
        return transfer

    transfer.status = (
        TRANSFER_STATUS_PROCESSING
    )
    transfer.error = str(error)
    transfer.updated_at = utcnow()

    db.add(transfer)
    db.commit()
    db.refresh(transfer)

    return transfer


def broadcast_persisted_transfer_intent(
    db: Session,
    *,
    w3: Web3,
    transfer_id: int,
    from_address: str,
    copy_to_gas_tx_hash: bool = False,
) -> FundSettlementTransfer:
    transfer = (
        db.query(FundSettlementTransfer)
        .filter(
            FundSettlementTransfer.id
            == int(transfer_id)
        )
        .with_for_update()
        .first()
    )

    if transfer is None:
        raise BscIntentError(
            "Settlement transfer not found while "
            "broadcasting prepared intent: "
            f"transfer_id={transfer_id}"
        )

    if transfer.tx_hash:
        return transfer

    prepared = prepared_transaction_from_transfer(
        transfer
    )

    if prepared is None:
        raise BscIntentError(
            "Settlement transfer has no durable "
            "prepared transaction intent: "
            f"transfer_id={transfer.id}"
        )

    try:
        broadcast_result = (
            broadcast_prepared_transaction(
                w3,
                prepared_tx_hash=(
                    prepared.tx_hash
                ),
                raw_tx_hex=prepared.raw_tx_hex,
                from_address=from_address,
                chain_id=prepared.chain_id,
                source_nonce=(
                    prepared.source_nonce
                ),
            )
        )
    except BscIntentError as exc:
        persist_transfer_intent_processing(
            db,
            transfer_id=int(transfer.id),
            error=str(exc),
        )
        raise

    return persist_broadcast_transfer_result(
        db,
        transfer_id=int(transfer.id),
        tx_hash=broadcast_result.tx_hash,
        copy_to_gas_tx_hash=(
            copy_to_gas_tx_hash
        ),
    )
