from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import text as sa_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from web3 import Web3
from web3.exceptions import TransactionNotFound

from app.config import settings
from app.models import (
    FundBscTransactionIntent,
    FundSettlementTransfer,
)
from app.settlement.statuses import (
    BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT,
    BSC_INTENT_ACTION_NEGATIVE_SETTLEMENT_GAS_TOPUP,
    BSC_INTENT_ACTION_TYPES,
    BSC_INTENT_STATUS_BROADCAST,
    BSC_INTENT_STATUS_BROADCASTING,
    BSC_INTENT_STATUS_CONFIRMED,
    BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW,
    BSC_INTENT_STATUS_PENDING_CONFIRMATION,
    BSC_INTENT_STATUS_PREPARED,
    BSC_INTENT_STATUS_VISIBLE,
    BSC_INTENT_TERMINAL_STATUSES,
    BSC_INTENT_UNRESOLVED_STATUSES,
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


class BscIntentBroadcastRetryableError(
    BscIntentError
):
    pass


class BscIntentBroadcastAmbiguousError(
    BscIntentError
):
    pass


class BscIntentBroadcastPermanentError(
    BscIntentError
):
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


@dataclass(frozen=True)
class BscIntentBroadcastClaim:
    action: str
    intent_id: int
    status: str
    claim_token: str | None
    broadcast_attempts: int


@dataclass(frozen=True)
class BscIntentBroadcastCycleResult:
    action: str
    intent_id: int
    status: str
    tx_hash: str
    broadcast_attempts: int


@dataclass(frozen=True)
class _ClaimedBscIntentBroadcast:
    intent_id: int
    claim_token: str
    prepared_tx_hash: str
    prepared_raw_tx: str = field(repr=False)
    from_address: str
    chain_id: int
    source_nonce: int
    broadcast_attempts: int


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


def _normalized_prepared_transaction(
    prepared: PreparedBscTransaction,
) -> PreparedBscTransaction:
    try:
        chain_id = int(prepared.chain_id)
    except (TypeError, ValueError) as exc:
        raise BscIntentError(
            "BSC intent chain_id must be an integer"
        ) from exc

    try:
        source_nonce = int(prepared.source_nonce)
    except (TypeError, ValueError) as exc:
        raise BscIntentError(
            "BSC intent source_nonce must be an integer"
        ) from exc

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

    return PreparedBscTransaction(
        chain_id=chain_id,
        source_nonce=source_nonce,
        tx_hash=_normalize_prepared_tx_hash(
            prepared.tx_hash
        ),
        raw_tx_hex=f"0x{raw_transaction.hex()}",
    )


def _validate_bsc_intent_action_contract(
    *,
    action_type: str,
    asset: str,
    payout_leg_id: int | None,
) -> None:
    if (
        action_type
        == BSC_INTENT_ACTION_NEGATIVE_SETTLEMENT_GAS_TOPUP
    ):
        if asset != "BNB":
            raise BscIntentError(
                "Negative settlement gas top-up "
                "intent must use BNB"
            )

        if payout_leg_id is not None:
            raise BscIntentError(
                "Negative settlement gas top-up "
                "intent cannot reference payout_leg_id"
            )

        return

    if (
        action_type
        == BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT
    ):
        if asset != "USDT":
            raise BscIntentError(
                "Negative redeem payout intent "
                "must use USDT"
            )

        if payout_leg_id is None:
            raise BscIntentError(
                "Negative redeem payout intent "
                "requires payout_leg_id"
            )

        return

    raise BscIntentError(
        "Unsupported BSC intent action_type"
    )


def _validated_prepared_intent_contract(
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
    normalized_scope_key = (
        _normalize_intent_scope_key(scope_key)
    )
    normalized_action_type = (
        _normalize_intent_action_type(action_type)
    )
    normalized_settlement_batch_id = (
        _positive_intent_id(
            settlement_batch_id,
            field_name="settlement_batch_id",
        )
    )
    normalized_payout_batch_id = (
        _positive_intent_id(
            payout_batch_id,
            field_name="payout_batch_id",
        )
    )
    normalized_payout_leg_id = (
        _optional_positive_intent_id(
            payout_leg_id,
            field_name="payout_leg_id",
        )
    )
    normalized_fund_id = _positive_intent_id(
        fund_id,
        field_name="fund_id",
    )
    normalized_asset = _normalize_intent_asset(
        asset
    )
    normalized_amount = Decimal(
        _canonical_intent_amount(amount)
    )
    normalized_from_address = (
        _normalize_intent_address(
            from_address,
            field_name="from_address",
        )
    )
    normalized_to_address = (
        _normalize_intent_address(
            to_address,
            field_name="to_address",
        )
    )
    normalized_prepared = (
        _normalized_prepared_transaction(prepared)
    )

    _validate_bsc_intent_action_contract(
        action_type=normalized_action_type,
        asset=normalized_asset,
        payout_leg_id=normalized_payout_leg_id,
    )

    intent_fingerprint = (
        build_bsc_intent_fingerprint(
            scope_key=normalized_scope_key,
            action_type=normalized_action_type,
            settlement_batch_id=(
                normalized_settlement_batch_id
            ),
            payout_batch_id=(
                normalized_payout_batch_id
            ),
            payout_leg_id=normalized_payout_leg_id,
            fund_id=normalized_fund_id,
            asset=normalized_asset,
            amount=normalized_amount,
            from_address=normalized_from_address,
            to_address=normalized_to_address,
            prepared=normalized_prepared,
        )
    )

    prepared_audit = build_bsc_intent_safe_audit(
        scope_key=normalized_scope_key,
        action_type=normalized_action_type,
        settlement_batch_id=(
            normalized_settlement_batch_id
        ),
        payout_batch_id=normalized_payout_batch_id,
        payout_leg_id=normalized_payout_leg_id,
        fund_id=normalized_fund_id,
        asset=normalized_asset,
        amount=normalized_amount,
        from_address=normalized_from_address,
        to_address=normalized_to_address,
        prepared=normalized_prepared,
    )

    return {
        "scope_key": normalized_scope_key,
        "action_type": normalized_action_type,
        "settlement_batch_id": (
            normalized_settlement_batch_id
        ),
        "payout_batch_id": normalized_payout_batch_id,
        "payout_leg_id": normalized_payout_leg_id,
        "fund_id": normalized_fund_id,
        "asset": normalized_asset,
        "amount": normalized_amount,
        "from_address": normalized_from_address,
        "to_address": normalized_to_address,
        "prepared": normalized_prepared,
        "intent_fingerprint": intent_fingerprint,
        "prepared_audit": prepared_audit,
    }


def _source_advisory_lock_key(
    from_address: str,
) -> int:
    normalized_address = _normalize_intent_address(
        from_address,
        field_name="from_address",
    )

    digest = hashlib.sha256(
        (
            "fund-bsc-transaction-intent-source:"
            f"{normalized_address}"
        ).encode("utf-8")
    ).digest()

    unsigned_value = int.from_bytes(
        digest[:8],
        byteorder="big",
        signed=False,
    )

    if unsigned_value >= (1 << 63):
        return unsigned_value - (1 << 64)

    return unsigned_value


def _acquire_source_transaction_lock(
    db: Session,
    *,
    from_address: str,
) -> int:
    lock_key = _source_advisory_lock_key(
        from_address
    )

    db.execute(
        sa_text(
            "SELECT pg_advisory_xact_lock(:lock_key)"
        ),
        {
            "lock_key": lock_key,
        },
    )

    return lock_key


def prepared_transaction_from_intent(
    intent: FundBscTransactionIntent,
) -> PreparedBscTransaction:
    return _normalized_prepared_transaction(
        PreparedBscTransaction(
            chain_id=int(intent.chain_id),
            source_nonce=int(intent.source_nonce),
            tx_hash=str(intent.prepared_tx_hash or ""),
            raw_tx_hex=str(intent.prepared_raw_tx or ""),
        )
    )


def _safe_int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_decimal_or_none(
    value: Any,
) -> Decimal | None:
    try:
        normalized = Decimal(str(value))
    except Exception:
        return None

    if not normalized.is_finite():
        return None

    return normalized


def _existing_intent_mismatch_fields(
    *,
    existing: FundBscTransactionIntent,
    contract: dict[str, Any],
) -> list[str]:
    mismatch_fields: list[str] = []

    scalar_comparisons = (
        (
            "scope_key",
            str(existing.scope_key or "").strip(),
            contract["scope_key"],
        ),
        (
            "action_type",
            str(existing.action_type or "").strip(),
            contract["action_type"],
        ),
        (
            "settlement_batch_id",
            _safe_int_or_none(
                existing.settlement_batch_id
            ),
            contract["settlement_batch_id"],
        ),
        (
            "payout_batch_id",
            _safe_int_or_none(
                existing.payout_batch_id
            ),
            contract["payout_batch_id"],
        ),
        (
            "payout_leg_id",
            _safe_int_or_none(
                existing.payout_leg_id
            ),
            contract["payout_leg_id"],
        ),
        (
            "fund_id",
            _safe_int_or_none(existing.fund_id),
            contract["fund_id"],
        ),
        (
            "asset",
            str(existing.asset or "").strip().upper(),
            contract["asset"],
        ),
        (
            "amount",
            _safe_decimal_or_none(existing.amount),
            contract["amount"],
        ),
        (
            "from_address",
            str(
                existing.from_address or ""
            ).strip().lower(),
            contract["from_address"],
        ),
        (
            "to_address",
            str(
                existing.to_address or ""
            ).strip().lower(),
            contract["to_address"],
        ),
        (
            "chain_id",
            _safe_int_or_none(existing.chain_id),
            contract["prepared"].chain_id,
        ),
        (
            "source_nonce",
            _safe_int_or_none(existing.source_nonce),
            contract["prepared"].source_nonce,
        ),
    )

    for (
        field_name,
        stored_value,
        requested_value,
    ) in scalar_comparisons:
        if stored_value != requested_value:
            mismatch_fields.append(field_name)

    try:
        existing_hash = _normalize_prepared_tx_hash(
            existing.prepared_tx_hash
        )
    except BscIntentError:
        existing_hash = None

    if (
        existing_hash
        != contract["prepared"].tx_hash
    ):
        mismatch_fields.append(
            "prepared_tx_hash"
        )

    try:
        existing_raw_sha256 = hashlib.sha256(
            _raw_transaction_bytes(
                existing.prepared_raw_tx
            )
        ).hexdigest()
    except BscIntentError:
        existing_raw_sha256 = None

    if (
        existing_raw_sha256
        != contract["prepared_audit"][
            "raw_transaction_sha256"
        ]
    ):
        mismatch_fields.append(
            "prepared_raw_tx"
        )

    if (
        str(
            existing.intent_fingerprint or ""
        ).strip()
        != contract["intent_fingerprint"]
    ):
        mismatch_fields.append(
            "intent_fingerprint"
        )

    stored_audit = existing.prepared_json

    if not isinstance(stored_audit, dict):
        mismatch_fields.append(
            "prepared_json"
        )
    else:
        for key, expected_value in (
            contract["prepared_audit"].items()
        ):
            if stored_audit.get(key) != expected_value:
                mismatch_fields.append(
                    f"prepared_json.{key}"
                )

    known_statuses = (
        BSC_INTENT_UNRESOLVED_STATUSES
        | BSC_INTENT_TERMINAL_STATUSES
    )

    if (
        str(existing.status or "").strip()
        not in known_statuses
    ):
        mismatch_fields.append("status")

    return sorted(set(mismatch_fields))


def _mark_intent_failed_requires_review(
    db: Session,
    *,
    intent: FundBscTransactionIntent,
    reason_code: str,
    mismatch_fields: list[str],
    requested_fingerprint: str,
) -> FundBscTransactionIntent:
    now = utcnow()

    intent.status = (
        BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
    )
    intent.failed_at = intent.failed_at or now
    intent.updated_at = now
    intent.error = (
        "Immutable BSC transaction intent "
        "contract mismatch"
    )
    intent.reconciliation_json = {
        "schema": (
            "fund_bsc_transaction_intent_failure_v1"
        ),
        "reason_code": reason_code,
        "scope_key": str(intent.scope_key or ""),
        "intent_id": (
            int(intent.id)
            if intent.id is not None
            else None
        ),
        "mismatch_fields": list(
            sorted(set(mismatch_fields))
        ),
        "stored_fingerprint": str(
            intent.intent_fingerprint or ""
        ),
        "requested_fingerprint": (
            requested_fingerprint
        ),
    }

    db.add(intent)
    db.commit()
    db.refresh(intent)

    return intent


def _rollback_and_raise_intent_error(
    db: Session,
    message: str,
) -> None:
    db.rollback()
    raise BscIntentError(message)


def persist_prepared_bsc_intent(
    db: Session,
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
) -> FundBscTransactionIntent:
    contract = _validated_prepared_intent_contract(
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

    _acquire_source_transaction_lock(
        db,
        from_address=contract["from_address"],
    )

    existing = (
        db.query(FundBscTransactionIntent)
        .filter(
            FundBscTransactionIntent.scope_key
            == contract["scope_key"]
        )
        .with_for_update()
        .first()
    )

    if existing is not None:
        mismatch_fields = (
            _existing_intent_mismatch_fields(
                existing=existing,
                contract=contract,
            )
        )

        if mismatch_fields:
            _mark_intent_failed_requires_review(
                db,
                intent=existing,
                reason_code=(
                    "immutable_contract_mismatch"
                ),
                mismatch_fields=mismatch_fields,
                requested_fingerprint=(
                    contract["intent_fingerprint"]
                ),
            )

            raise BscIntentError(
                "Immutable BSC transaction intent "
                "contract mismatch: "
                f"scope_key={contract['scope_key']}"
            )

        if (
            existing.status
            == BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
        ):
            db.commit()
            db.refresh(existing)

            raise BscIntentError(
                "BSC transaction intent is already "
                "failed_requires_review: "
                f"scope_key={contract['scope_key']}"
            )

        # Idempotent query-before-create path.
        # Commit releases pg_advisory_xact_lock.
        db.commit()
        db.refresh(existing)

        return existing

    unresolved_source_intent = (
        db.query(FundBscTransactionIntent)
        .filter(
            FundBscTransactionIntent.from_address
            == contract["from_address"]
        )
        .filter(
            FundBscTransactionIntent.status.in_(
                sorted(
                    BSC_INTENT_UNRESOLVED_STATUSES
                )
            )
        )
        .order_by(
            FundBscTransactionIntent.id.asc()
        )
        .with_for_update()
        .first()
    )

    if unresolved_source_intent is not None:
        _rollback_and_raise_intent_error(
            db,
            (
                "Another unresolved BSC transaction "
                "intent already exists for source: "
                f"source={contract['from_address']} "
                f"intent_id={unresolved_source_intent.id}"
            ),
        )

    nonce_owner = (
        db.query(FundBscTransactionIntent)
        .filter(
            FundBscTransactionIntent.from_address
            == contract["from_address"]
        )
        .filter(
            FundBscTransactionIntent.source_nonce
            == contract["prepared"].source_nonce
        )
        .with_for_update()
        .first()
    )

    if nonce_owner is not None:
        _rollback_and_raise_intent_error(
            db,
            (
                "BSC source nonce is already owned "
                "by another durable intent: "
                f"intent_id={nonce_owner.id}"
            ),
        )

    if contract["payout_leg_id"] is not None:
        payout_leg_owner = (
            db.query(FundBscTransactionIntent)
            .filter(
                FundBscTransactionIntent.payout_leg_id
                == contract["payout_leg_id"]
            )
            .with_for_update()
            .first()
        )

        if payout_leg_owner is not None:
            _rollback_and_raise_intent_error(
                db,
                (
                    "Payout leg already has a durable "
                    "BSC transaction intent: "
                    f"intent_id={payout_leg_owner.id}"
                ),
            )

    now = utcnow()

    intent = FundBscTransactionIntent(
        scope_key=contract["scope_key"],
        action_type=contract["action_type"],
        settlement_batch_id=(
            contract["settlement_batch_id"]
        ),
        payout_batch_id=(
            contract["payout_batch_id"]
        ),
        payout_leg_id=contract["payout_leg_id"],
        fund_id=contract["fund_id"],
        asset=contract["asset"],
        amount=contract["amount"],
        from_address=contract["from_address"],
        to_address=contract["to_address"],
        chain_id=contract["prepared"].chain_id,
        source_nonce=(
            contract["prepared"].source_nonce
        ),
        prepared_tx_hash=(
            contract["prepared"].tx_hash
        ),
        prepared_raw_tx=(
            contract["prepared"].raw_tx_hex
        ),
        intent_fingerprint=(
            contract["intent_fingerprint"]
        ),
        status=BSC_INTENT_STATUS_PREPARED,
        broadcast_attempts=0,
        prepared_at=now,
        prepared_json={
            **contract["prepared_audit"],
            "durable_boundary": (
                "prepared_before_broadcast"
            ),
        },
        error=None,
        created_at=now,
        updated_at=now,
    )

    try:
        db.add(intent)
        db.flush()
        db.commit()
    except IntegrityError as exc:
        db.rollback()

        raise BscIntentError(
            "BSC transaction intent uniqueness "
            "conflict during durable prepare"
        ) from exc
    except Exception:
        db.rollback()
        raise

    db.refresh(intent)

    return intent


def _persisted_bsc_intent_contract(
    intent: FundBscTransactionIntent,
) -> dict[str, Any]:
    prepared = prepared_transaction_from_intent(
        intent
    )

    return _validated_prepared_intent_contract(
        scope_key=str(intent.scope_key or ""),
        action_type=str(intent.action_type or ""),
        settlement_batch_id=(
            intent.settlement_batch_id
        ),
        payout_batch_id=intent.payout_batch_id,
        payout_leg_id=intent.payout_leg_id,
        fund_id=intent.fund_id,
        asset=str(intent.asset or ""),
        amount=intent.amount,
        from_address=str(
            intent.from_address or ""
        ),
        to_address=str(intent.to_address or ""),
        prepared=prepared,
    )


def _validate_persisted_bsc_intent_or_fail(
    db: Session,
    *,
    intent: FundBscTransactionIntent,
) -> dict[str, Any]:
    try:
        contract = _persisted_bsc_intent_contract(
            intent
        )
    except BscIntentError as exc:
        _mark_intent_failed_requires_review(
            db,
            intent=intent,
            reason_code="stored_contract_invalid",
            mismatch_fields=["stored_contract"],
            requested_fingerprint=str(
                intent.intent_fingerprint or ""
            ),
        )

        raise BscIntentError(
            "Persisted BSC transaction intent "
            "contract is invalid: "
            f"intent_id={intent.id}"
        ) from exc

    mismatch_fields = (
        _existing_intent_mismatch_fields(
            existing=intent,
            contract=contract,
        )
    )

    if mismatch_fields:
        _mark_intent_failed_requires_review(
            db,
            intent=intent,
            reason_code=(
                "persisted_contract_mismatch"
            ),
            mismatch_fields=mismatch_fields,
            requested_fingerprint=(
                contract["intent_fingerprint"]
            ),
        )

        raise BscIntentError(
            "Persisted BSC transaction intent "
            "contract mismatch: "
            f"intent_id={intent.id}"
        )

    return contract


def _load_bsc_intent_for_update(
    db: Session,
    *,
    intent_id: int,
) -> FundBscTransactionIntent:
    normalized_intent_id = _positive_intent_id(
        intent_id,
        field_name="intent_id",
    )

    candidate = (
        db.query(FundBscTransactionIntent)
        .filter(
            FundBscTransactionIntent.id
            == normalized_intent_id
        )
        .first()
    )

    if candidate is None:
        db.rollback()

        raise BscIntentError(
            "BSC transaction intent not found: "
            f"intent_id={normalized_intent_id}"
        )

    try:
        candidate_source = (
            _normalize_intent_address(
                candidate.from_address,
                field_name="from_address",
            )
        )
    except BscIntentError as exc:
        db.rollback()

        raise BscIntentError(
            "BSC transaction intent contains an "
            "invalid source address before lock: "
            f"intent_id={normalized_intent_id}"
        ) from exc

    # Lock order must match durable prepare:
    # source advisory lock before row FOR UPDATE.
    _acquire_source_transaction_lock(
        db,
        from_address=candidate_source,
    )

    locked_query = (
        db.query(FundBscTransactionIntent)
        .filter(
            FundBscTransactionIntent.id
            == normalized_intent_id
        )
        .with_for_update()
    )

    # SQLAlchemy identity map may otherwise return
    # attributes captured before advisory lock.
    populate_existing = getattr(
        locked_query,
        "populate_existing",
        None,
    )

    if callable(populate_existing):
        locked_query = populate_existing()

    intent = locked_query.first()

    if intent is None:
        db.rollback()

        raise BscIntentError(
            "BSC transaction intent disappeared "
            "during lock acquisition: "
            f"intent_id={normalized_intent_id}"
        )

    try:
        locked_source = _normalize_intent_address(
            intent.from_address,
            field_name="from_address",
        )
    except BscIntentError as exc:
        _mark_intent_failed_requires_review(
            db,
            intent=intent,
            reason_code=(
                "invalid_source_after_lock_acquisition"
            ),
            mismatch_fields=["from_address"],
            requested_fingerprint=str(
                intent.intent_fingerprint or ""
            ),
        )

        raise BscIntentError(
            "Locked BSC transaction intent contains "
            "an invalid source address: "
            f"intent_id={normalized_intent_id}"
        ) from exc

    if locked_source != candidate_source:
        _mark_intent_failed_requires_review(
            db,
            intent=intent,
            reason_code=(
                "source_changed_during_lock_acquisition"
            ),
            mismatch_fields=["from_address"],
            requested_fingerprint=str(
                intent.intent_fingerprint or ""
            ),
        )

        raise BscIntentError(
            "BSC transaction intent source changed "
            "during lock acquisition: "
            f"intent_id={normalized_intent_id}"
        )

    return intent


def _commit_and_refresh_bsc_intent(
    db: Session,
    *,
    intent: FundBscTransactionIntent,
) -> FundBscTransactionIntent:
    db.add(intent)
    db.commit()
    db.refresh(intent)

    return intent


def _safe_broadcast_attempts(
    value: Any,
) -> int:
    try:
        attempts = int(value or 0)
    except (TypeError, ValueError) as exc:
        raise BscIntentError(
            "BSC intent broadcast_attempts "
            "must be an integer"
        ) from exc

    if attempts < 0:
        raise BscIntentError(
            "BSC intent broadcast_attempts "
            "cannot be negative"
        )

    return attempts


def _parse_claimed_at(
    value: Any,
) -> datetime | None:
    text = str(value or "").strip()

    if not text:
        return None

    try:
        parsed = datetime.fromisoformat(
            text.replace("Z", "+00:00")
        )
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return None

    return parsed.astimezone(timezone.utc)


def _broadcast_claim_timeout() -> timedelta:
    try:
        seconds = int(
            settings
            .NEGATIVE_NET_BSC_INTENT_MAX_PENDING_SEC
        )
    except (TypeError, ValueError) as exc:
        raise BscIntentError(
            "NEGATIVE_NET_BSC_INTENT_MAX_PENDING_SEC "
            "must be an integer"
        ) from exc

    if seconds <= 0:
        raise BscIntentError(
            "NEGATIVE_NET_BSC_INTENT_MAX_PENDING_SEC "
            "must be positive"
        )

    return timedelta(seconds=seconds)


def _mark_bsc_intent_operational_failure(
    db: Session,
    *,
    intent: FundBscTransactionIntent,
    reason_code: str,
    evidence: dict[str, Any] | None = None,
) -> FundBscTransactionIntent:
    now = utcnow()

    intent.status = (
        BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
    )
    intent.failed_at = intent.failed_at or now
    intent.updated_at = now
    intent.error = (
        "BSC transaction intent operational "
        f"failure: {reason_code}"
    )
    intent.reconciliation_json = {
        "schema": (
            "fund_bsc_transaction_intent_failure_v1"
        ),
        "reason_code": str(reason_code),
        "intent_id": (
            int(intent.id)
            if intent.id is not None
            else None
        ),
        "scope_key": str(
            intent.scope_key or ""
        ),
        "intent_fingerprint": str(
            intent.intent_fingerprint or ""
        ),
        "evidence": dict(evidence or {}),
    }

    return _commit_and_refresh_bsc_intent(
        db,
        intent=intent,
    )


def mark_bsc_intent_broadcasting(
    db: Session,
    *,
    intent_id: int,
    now: datetime | None = None,
) -> FundBscTransactionIntent:
    intent = _load_bsc_intent_for_update(
        db,
        intent_id=intent_id,
    )

    _validate_persisted_bsc_intent_or_fail(
        db,
        intent=intent,
    )

    status = str(intent.status or "").strip()

    if (
        status
        == BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
    ):
        db.commit()
        db.refresh(intent)

        raise BscIntentError(
            "BSC transaction intent is "
            "failed_requires_review: "
            f"intent_id={intent.id}"
        )

    idempotent_statuses = {
        BSC_INTENT_STATUS_BROADCASTING,
        BSC_INTENT_STATUS_BROADCAST,
        BSC_INTENT_STATUS_VISIBLE,
        BSC_INTENT_STATUS_PENDING_CONFIRMATION,
        BSC_INTENT_STATUS_CONFIRMED,
    }

    if status in idempotent_statuses:
        db.commit()
        db.refresh(intent)

        return intent

    if status != BSC_INTENT_STATUS_PREPARED:
        _mark_bsc_intent_operational_failure(
            db,
            intent=intent,
            reason_code=(
                "unexpected_status_before_broadcasting"
            ),
            evidence={
                "status": status,
            },
        )

        raise BscIntentError(
            "Unexpected BSC intent status before "
            "broadcasting: "
            f"intent_id={intent.id} "
            f"status={status or 'empty'}"
        )

    transition_at = (
        now.astimezone(timezone.utc)
        if now is not None
        else utcnow()
    )

    attempts = _safe_broadcast_attempts(
        intent.broadcast_attempts
    )

    intent.status = (
        BSC_INTENT_STATUS_BROADCASTING
    )
    intent.broadcast_started_at = (
        intent.broadcast_started_at
        or transition_at
    )
    intent.updated_at = transition_at
    intent.error = None
    intent.broadcast_json = {
        "schema": (
            "fund_bsc_transaction_intent_broadcast_v1"
        ),
        "phase": "ready_for_broadcast",
        "intent_id": int(intent.id),
        "scope_key": str(intent.scope_key),
        "intent_fingerprint": str(
            intent.intent_fingerprint
        ),
        "prepared_tx_hash": str(
            intent.prepared_tx_hash
        ),
        "marked_at": transition_at.isoformat(),
        "broadcast_attempts": attempts,
    }

    return _commit_and_refresh_bsc_intent(
        db,
        intent=intent,
    )


def claim_bsc_intent_broadcast_attempt(
    db: Session,
    *,
    intent_id: int,
    now: datetime | None = None,
) -> BscIntentBroadcastClaim:
    intent = _load_bsc_intent_for_update(
        db,
        intent_id=intent_id,
    )

    _validate_persisted_bsc_intent_or_fail(
        db,
        intent=intent,
    )

    status = str(intent.status or "").strip()
    attempts = _safe_broadcast_attempts(
        intent.broadcast_attempts
    )

    if (
        status
        == BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
    ):
        db.commit()
        db.refresh(intent)

        raise BscIntentError(
            "BSC transaction intent is "
            "failed_requires_review: "
            f"intent_id={intent.id}"
        )

    if status == BSC_INTENT_STATUS_PREPARED:
        db.commit()
        db.refresh(intent)

        raise BscIntentError(
            "BSC transaction intent must first be "
            "marked broadcasting: "
            f"intent_id={intent.id}"
        )

    terminal_or_later_statuses = {
        BSC_INTENT_STATUS_BROADCAST,
        BSC_INTENT_STATUS_VISIBLE,
        BSC_INTENT_STATUS_PENDING_CONFIRMATION,
        BSC_INTENT_STATUS_CONFIRMED,
    }

    if status in terminal_or_later_statuses:
        db.commit()
        db.refresh(intent)

        return BscIntentBroadcastClaim(
            action=f"status_{status}",
            intent_id=int(intent.id),
            status=status,
            claim_token=None,
            broadcast_attempts=attempts,
        )

    if status != BSC_INTENT_STATUS_BROADCASTING:
        _mark_bsc_intent_operational_failure(
            db,
            intent=intent,
            reason_code=(
                "unexpected_status_before_broadcast_claim"
            ),
            evidence={
                "status": status,
            },
        )

        raise BscIntentError(
            "Unexpected BSC intent status before "
            "broadcast claim: "
            f"intent_id={intent.id} "
            f"status={status or 'empty'}"
        )

    claim_at = (
        now.astimezone(timezone.utc)
        if now is not None
        else utcnow()
    )
    timeout = _broadcast_claim_timeout()

    broadcast_json = (
        dict(intent.broadcast_json)
        if isinstance(intent.broadcast_json, dict)
        else {}
    )

    phase = str(
        broadcast_json.get("phase") or ""
    ).strip()
    existing_claim_token = str(
        broadcast_json.get("claim_token") or ""
    ).strip()
    existing_claimed_at = _parse_claimed_at(
        broadcast_json.get("claimed_at")
    )

    if phase == "attempt_claimed":
        if (
            not existing_claim_token
            or existing_claimed_at is None
        ):
            _mark_bsc_intent_operational_failure(
                db,
                intent=intent,
                reason_code=(
                    "invalid_active_broadcast_claim"
                ),
                evidence={
                    "has_claim_token": bool(
                        existing_claim_token
                    ),
                    "has_claimed_at": (
                        existing_claimed_at
                        is not None
                    ),
                },
            )

            raise BscIntentError(
                "Active BSC broadcast claim "
                "metadata is invalid: "
                f"intent_id={intent.id}"
            )

        claim_age = (
            claim_at - existing_claimed_at
        )

        if claim_age < timeout:
            db.commit()
            db.refresh(intent)

            return BscIntentBroadcastClaim(
                action="claim_already_active",
                intent_id=int(intent.id),
                status=status,
                claim_token=None,
                broadcast_attempts=attempts,
            )

    new_claim_token = uuid4().hex
    new_attempts = attempts + 1

    intent.broadcast_attempts = new_attempts
    intent.updated_at = claim_at
    intent.error = None
    intent.broadcast_json = {
        "schema": (
            "fund_bsc_transaction_intent_broadcast_v1"
        ),
        "phase": "attempt_claimed",
        "intent_id": int(intent.id),
        "scope_key": str(intent.scope_key),
        "intent_fingerprint": str(
            intent.intent_fingerprint
        ),
        "prepared_tx_hash": str(
            intent.prepared_tx_hash
        ),
        "claim_token": new_claim_token,
        "claimed_at": claim_at.isoformat(),
        "broadcast_attempts": new_attempts,
        "previous_claim_was_stale": bool(
            phase == "attempt_claimed"
        ),
        "claim_timeout_sec": int(
            timeout.total_seconds()
        ),
    }

    _commit_and_refresh_bsc_intent(
        db,
        intent=intent,
    )

    return BscIntentBroadcastClaim(
        action="claim_created",
        intent_id=int(intent.id),
        status=str(intent.status),
        claim_token=new_claim_token,
        broadcast_attempts=new_attempts,
    )


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
        raise BscIntentBroadcastRetryableError(
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
    try:
        tx_hash = _normalize_prepared_tx_hash(
            prepared_tx_hash
        )
    except BscIntentError as exc:
        raise BscIntentBroadcastPermanentError(
            "Prepared transaction hash is invalid"
        ) from exc

    try:
        expected_chain_id = int(chain_id)
        current_chain_id = int(w3.eth.chain_id)
    except (TypeError, ValueError) as exc:
        raise BscIntentBroadcastPermanentError(
            "Prepared transaction chain id is invalid"
        ) from exc

    if current_chain_id != expected_chain_id:
        raise BscIntentBroadcastPermanentError(
            "Prepared transaction chain mismatch: "
            f"prepared_chain_id={expected_chain_id} "
            f"current_chain_id={current_chain_id}"
        )

    try:
        from_checksum = _checksum(
            w3,
            from_address,
        )
    except Exception as exc:
        raise BscIntentBroadcastPermanentError(
            "Prepared transaction source address "
            "is invalid"
        ) from exc

    try:
        nonce = int(source_nonce)
    except (TypeError, ValueError) as exc:
        raise BscIntentBroadcastPermanentError(
            "Prepared transaction source nonce "
            "is invalid"
        ) from exc

    if nonce < 0:
        raise BscIntentBroadcastPermanentError(
            "Prepared transaction source nonce "
            "cannot be negative"
        )

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
        raise BscIntentBroadcastRetryableError(
            "Cannot reconcile prepared transaction "
            f"nonce for hash={tx_hash}: {exc}"
        ) from exc

    if pending_nonce > nonce:
        raise BscIntentBroadcastAmbiguousError(
            "Prepared transaction is not visible by "
            "hash, but its source nonce may already "
            "be consumed: "
            f"hash={tx_hash} "
            f"source_nonce={nonce} "
            f"pending_nonce={pending_nonce}"
        )

    try:
        raw_tx = _raw_transaction_bytes(
            raw_tx_hex
        )
    except BscIntentError as exc:
        raise BscIntentBroadcastPermanentError(
            "Prepared raw transaction is invalid"
        ) from exc

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
                action=(
                    "visible_after_broadcast_error"
                ),
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
            raise BscIntentBroadcastAmbiguousError(
                "Prepared transaction broadcast "
                "outcome is ambiguous and nonce "
                "reconciliation failed: "
                f"hash={tx_hash}; "
                f"broadcast_error={exc}; "
                f"nonce_error={nonce_exc}"
            ) from exc

        if refreshed_pending_nonce > nonce:
            raise BscIntentBroadcastAmbiguousError(
                "Prepared transaction broadcast "
                "outcome is ambiguous because its "
                "nonce is consumed but the transaction "
                "is not visible by hash: "
                f"hash={tx_hash} "
                f"source_nonce={nonce} "
                f"pending_nonce="
                f"{refreshed_pending_nonce}"
            ) from exc

        raise BscIntentBroadcastRetryableError(
            "Prepared transaction was not visible "
            "after broadcast error and its nonce "
            "remains available: "
            f"hash={tx_hash}; error={exc}"
        ) from exc

    actual_tx_hash = _normalize_prepared_tx_hash(
        w3.to_hex(sent_hash)
    )

    if actual_tx_hash != tx_hash:
        raise BscIntentBroadcastPermanentError(
            "Broadcast transaction hash mismatch: "
            f"prepared_hash={tx_hash} "
            f"broadcast_hash={actual_tx_hash}"
        )

    return BroadcastBscTransactionResult(
        action="broadcast",
        tx_hash=actual_tx_hash,
    )




def _normalize_broadcast_claim_token(
    value: Any,
) -> str:
    normalized = str(value or "").strip().lower()

    try:
        raw_token = bytes.fromhex(normalized)
    except ValueError as exc:
        raise BscIntentError(
            "BSC broadcast claim token is not "
            "valid hex"
        ) from exc

    if len(raw_token) != 16:
        raise BscIntentError(
            "BSC broadcast claim token must be "
            "16 bytes"
        )

    return raw_token.hex()


def _safe_broadcast_error_text(
    error: BaseException,
    *,
    prepared_raw_tx: str,
) -> str:
    text = str(error or "").strip()

    if not text:
        text = error.__class__.__name__

    raw_value = str(
        prepared_raw_tx or ""
    ).strip()

    redaction_candidates = {
        raw_value,
        raw_value.removeprefix("0x"),
    }

    for candidate in sorted(
        redaction_candidates,
        key=len,
        reverse=True,
    ):
        if candidate:
            text = text.replace(
                candidate,
                "[redacted_raw_transaction]",
            )

    return text[:512]


def _broadcast_cycle_result(
    *,
    action: str,
    intent: FundBscTransactionIntent,
) -> BscIntentBroadcastCycleResult:
    return BscIntentBroadcastCycleResult(
        action=str(action),
        intent_id=int(intent.id),
        status=str(intent.status or ""),
        tx_hash=str(
            intent.prepared_tx_hash or ""
        ),
        broadcast_attempts=(
            _safe_broadcast_attempts(
                intent.broadcast_attempts
            )
        ),
    )


def _load_claimed_bsc_intent_broadcast(
    db: Session,
    *,
    intent_id: int,
    claim_token: str,
) -> tuple[
    _ClaimedBscIntentBroadcast | None,
    BscIntentBroadcastCycleResult | None,
]:
    normalized_claim_token = (
        _normalize_broadcast_claim_token(
            claim_token
        )
    )

    intent = _load_bsc_intent_for_update(
        db,
        intent_id=intent_id,
    )

    _validate_persisted_bsc_intent_or_fail(
        db,
        intent=intent,
    )

    status = str(intent.status or "").strip()
    attempts = _safe_broadcast_attempts(
        intent.broadcast_attempts
    )

    completed_statuses = {
        BSC_INTENT_STATUS_BROADCAST,
        BSC_INTENT_STATUS_VISIBLE,
        BSC_INTENT_STATUS_PENDING_CONFIRMATION,
        BSC_INTENT_STATUS_CONFIRMED,
        BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW,
    }

    if status in completed_statuses:
        db.commit()
        db.refresh(intent)

        return (
            None,
            _broadcast_cycle_result(
                action=f"status_{status}",
                intent=intent,
            ),
        )

    if status != BSC_INTENT_STATUS_BROADCASTING:
        _mark_bsc_intent_operational_failure(
            db,
            intent=intent,
            reason_code=(
                "unexpected_status_before_external_broadcast"
            ),
            evidence={
                "status": status,
            },
        )

        raise BscIntentError(
            "Unexpected BSC intent status before "
            "external broadcast: "
            f"intent_id={intent.id} "
            f"status={status or 'empty'}"
        )

    broadcast_json = (
        dict(intent.broadcast_json)
        if isinstance(intent.broadcast_json, dict)
        else {}
    )

    phase = str(
        broadcast_json.get("phase") or ""
    ).strip()

    if phase != "attempt_claimed":
        db.commit()
        db.refresh(intent)

        raise BscIntentError(
            "BSC transaction intent has no active "
            "broadcast claim: "
            f"intent_id={intent.id}"
        )

    try:
        stored_claim_token = (
            _normalize_broadcast_claim_token(
                broadcast_json.get(
                    "claim_token"
                )
            )
        )
    except BscIntentError as exc:
        _mark_bsc_intent_operational_failure(
            db,
            intent=intent,
            reason_code=(
                "invalid_persisted_broadcast_claim_token"
            ),
            evidence={
                "phase": phase,
            },
        )

        raise BscIntentError(
            "Persisted BSC broadcast claim token "
            "is invalid: "
            f"intent_id={intent.id}"
        ) from exc

    if stored_claim_token != normalized_claim_token:
        db.commit()
        db.refresh(intent)

        raise BscIntentError(
            "BSC broadcast claim token mismatch: "
            f"intent_id={intent.id}"
        )

    snapshot = _ClaimedBscIntentBroadcast(
        intent_id=int(intent.id),
        claim_token=normalized_claim_token,
        prepared_tx_hash=str(
            intent.prepared_tx_hash
        ),
        prepared_raw_tx=str(
            intent.prepared_raw_tx
        ),
        from_address=str(
            intent.from_address
        ),
        chain_id=int(intent.chain_id),
        source_nonce=int(intent.source_nonce),
        broadcast_attempts=attempts,
    )

    # No database lock may remain held during RPC.
    db.commit()
    db.refresh(intent)

    return snapshot, None


def _claim_still_owned(
    intent: FundBscTransactionIntent,
    *,
    claim_token: str,
) -> bool:
    broadcast_json = (
        dict(intent.broadcast_json)
        if isinstance(intent.broadcast_json, dict)
        else {}
    )

    if (
        str(
            broadcast_json.get("phase") or ""
        ).strip()
        != "attempt_claimed"
    ):
        return False

    try:
        stored_claim_token = (
            _normalize_broadcast_claim_token(
                broadcast_json.get(
                    "claim_token"
                )
            )
        )
    except BscIntentError:
        return False

    return stored_claim_token == claim_token


def _persist_bsc_intent_broadcast_success(
    db: Session,
    *,
    snapshot: _ClaimedBscIntentBroadcast,
    result: BroadcastBscTransactionResult,
    now: datetime | None = None,
) -> BscIntentBroadcastCycleResult:
    normalized_result_hash = (
        _normalize_prepared_tx_hash(
            result.tx_hash
        )
    )
    normalized_prepared_hash = (
        _normalize_prepared_tx_hash(
            snapshot.prepared_tx_hash
        )
    )

    if normalized_result_hash != normalized_prepared_hash:
        raise BscIntentBroadcastPermanentError(
            "Persisted broadcast result hash does "
            "not match claimed prepared hash"
        )

    allowed_actions = {
        "broadcast",
        "already_visible",
        "visible_after_broadcast_error",
    }

    if result.action not in allowed_actions:
        raise BscIntentBroadcastPermanentError(
            "Unsupported BSC broadcast result action"
        )

    intent = _load_bsc_intent_for_update(
        db,
        intent_id=snapshot.intent_id,
    )

    _validate_persisted_bsc_intent_or_fail(
        db,
        intent=intent,
    )

    status = str(intent.status or "").strip()

    later_statuses = {
        BSC_INTENT_STATUS_BROADCAST,
        BSC_INTENT_STATUS_VISIBLE,
        BSC_INTENT_STATUS_PENDING_CONFIRMATION,
        BSC_INTENT_STATUS_CONFIRMED,
    }

    if status in later_statuses:
        stored_hash = _normalize_prepared_tx_hash(
            intent.prepared_tx_hash
        )

        if stored_hash != normalized_result_hash:
            _mark_bsc_intent_operational_failure(
                db,
                intent=intent,
                reason_code=(
                    "later_status_transaction_hash_mismatch"
                ),
                evidence={
                    "status": status,
                    "stored_tx_hash": stored_hash,
                    "result_tx_hash": (
                        normalized_result_hash
                    ),
                },
            )

            raise BscIntentError(
                "Later BSC intent status contains "
                "another transaction hash"
            )

        db.commit()
        db.refresh(intent)

        return _broadcast_cycle_result(
            action=f"status_{status}",
            intent=intent,
        )

    if (
        status
        == BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
    ):
        db.commit()
        db.refresh(intent)

        return _broadcast_cycle_result(
            action=(
                "status_failed_requires_review"
            ),
            intent=intent,
        )

    if status != BSC_INTENT_STATUS_BROADCASTING:
        _mark_bsc_intent_operational_failure(
            db,
            intent=intent,
            reason_code=(
                "unexpected_status_after_external_broadcast"
            ),
            evidence={
                "status": status,
            },
        )

        raise BscIntentError(
            "Unexpected BSC intent status after "
            "external broadcast: "
            f"intent_id={intent.id}"
        )

    if not _claim_still_owned(
        intent,
        claim_token=snapshot.claim_token,
    ):
        db.commit()
        db.refresh(intent)

        return _broadcast_cycle_result(
            action="claim_lost_after_external",
            intent=intent,
        )

    result_at = (
        now.astimezone(timezone.utc)
        if now is not None
        else utcnow()
    )

    prior_broadcast_json = (
        dict(intent.broadcast_json)
        if isinstance(intent.broadcast_json, dict)
        else {}
    )

    if result.action == "broadcast":
        intent.status = (
            BSC_INTENT_STATUS_BROADCAST
        )
        intent.broadcast_at = (
            intent.broadcast_at or result_at
        )
    else:
        intent.status = BSC_INTENT_STATUS_VISIBLE
        intent.visible_at = (
            intent.visible_at or result_at
        )

        if (
            result.action
            == "visible_after_broadcast_error"
        ):
            intent.broadcast_at = (
                intent.broadcast_at or result_at
            )

    intent.updated_at = result_at
    intent.error = None
    intent.broadcast_json = {
        "schema": (
            "fund_bsc_transaction_intent_broadcast_v1"
        ),
        "phase": "result_persisted",
        "intent_id": int(intent.id),
        "scope_key": str(intent.scope_key),
        "intent_fingerprint": str(
            intent.intent_fingerprint
        ),
        "prepared_tx_hash": (
            normalized_prepared_hash
        ),
        "result_action": str(result.action),
        "result_tx_hash": normalized_result_hash,
        "broadcast_attempts": (
            _safe_broadcast_attempts(
                intent.broadcast_attempts
            )
        ),
        "claimed_at": (
            prior_broadcast_json.get(
                "claimed_at"
            )
        ),
        "result_persisted_at": (
            result_at.isoformat()
        ),
    }

    _commit_and_refresh_bsc_intent(
        db,
        intent=intent,
    )

    return _broadcast_cycle_result(
        action=str(result.action),
        intent=intent,
    )


def _persist_bsc_intent_broadcast_failure(
    db: Session,
    *,
    snapshot: _ClaimedBscIntentBroadcast,
    error: BaseException,
    review_required: bool,
    reason_code: str,
    now: datetime | None = None,
) -> BscIntentBroadcastCycleResult:
    intent = _load_bsc_intent_for_update(
        db,
        intent_id=snapshot.intent_id,
    )

    _validate_persisted_bsc_intent_or_fail(
        db,
        intent=intent,
    )

    status = str(intent.status or "").strip()

    if status != BSC_INTENT_STATUS_BROADCASTING:
        db.commit()
        db.refresh(intent)

        return _broadcast_cycle_result(
            action=f"status_{status}",
            intent=intent,
        )

    if not _claim_still_owned(
        intent,
        claim_token=snapshot.claim_token,
    ):
        db.commit()
        db.refresh(intent)

        return _broadcast_cycle_result(
            action="claim_lost_after_external",
            intent=intent,
        )

    safe_error = _safe_broadcast_error_text(
        error,
        prepared_raw_tx=(
            snapshot.prepared_raw_tx
        ),
    )

    if review_required:
        _mark_bsc_intent_operational_failure(
            db,
            intent=intent,
            reason_code=reason_code,
            evidence={
                "error_type": (
                    error.__class__.__name__
                ),
                "error": safe_error,
                "prepared_tx_hash": str(
                    intent.prepared_tx_hash
                ),
                "broadcast_attempts": (
                    _safe_broadcast_attempts(
                        intent.broadcast_attempts
                    )
                ),
            },
        )

        return _broadcast_cycle_result(
            action=(
                "failed_requires_review"
            ),
            intent=intent,
        )

    failure_at = (
        now.astimezone(timezone.utc)
        if now is not None
        else utcnow()
    )

    intent.status = (
        BSC_INTENT_STATUS_BROADCASTING
    )
    intent.updated_at = failure_at
    intent.error = safe_error
    intent.broadcast_json = {
        "schema": (
            "fund_bsc_transaction_intent_broadcast_v1"
        ),
        "phase": (
            "retryable_reconciliation_required"
        ),
        "intent_id": int(intent.id),
        "scope_key": str(intent.scope_key),
        "intent_fingerprint": str(
            intent.intent_fingerprint
        ),
        "prepared_tx_hash": str(
            intent.prepared_tx_hash
        ),
        "error_type": (
            error.__class__.__name__
        ),
        "error": safe_error,
        "broadcast_attempts": (
            _safe_broadcast_attempts(
                intent.broadcast_attempts
            )
        ),
        "failed_at": failure_at.isoformat(),
    }

    _commit_and_refresh_bsc_intent(
        db,
        intent=intent,
    )

    return _broadcast_cycle_result(
        action="retryable_error",
        intent=intent,
    )


def execute_claimed_bsc_intent_broadcast(
    db: Session,
    w3: Web3,
    *,
    intent_id: int,
    claim_token: str,
    now: datetime | None = None,
) -> BscIntentBroadcastCycleResult:
    snapshot, completed_result = (
        _load_claimed_bsc_intent_broadcast(
            db,
            intent_id=intent_id,
            claim_token=claim_token,
        )
    )

    if completed_result is not None:
        return completed_result

    if snapshot is None:
        raise BscIntentError(
            "BSC broadcast snapshot is missing"
        )

    try:
        # Exactly one helper invocation and at most
        # one send_raw_transaction in this cycle.
        broadcast_result = (
            broadcast_prepared_transaction(
                w3,
                prepared_tx_hash=(
                    snapshot.prepared_tx_hash
                ),
                raw_tx_hex=(
                    snapshot.prepared_raw_tx
                ),
                from_address=(
                    snapshot.from_address
                ),
                chain_id=snapshot.chain_id,
                source_nonce=(
                    snapshot.source_nonce
                ),
            )
        )
    except BscIntentBroadcastRetryableError as exc:
        return _persist_bsc_intent_broadcast_failure(
            db,
            snapshot=snapshot,
            error=exc,
            review_required=False,
            reason_code=(
                "retryable_broadcast_reconciliation_error"
            ),
            now=now,
        )
    except BscIntentBroadcastAmbiguousError as exc:
        return _persist_bsc_intent_broadcast_failure(
            db,
            snapshot=snapshot,
            error=exc,
            review_required=True,
            reason_code=(
                "ambiguous_broadcast_outcome"
            ),
            now=now,
        )
    except BscIntentBroadcastPermanentError as exc:
        return _persist_bsc_intent_broadcast_failure(
            db,
            snapshot=snapshot,
            error=exc,
            review_required=True,
            reason_code=(
                "permanent_broadcast_contract_error"
            ),
            now=now,
        )
    except BscIntentError as exc:
        return _persist_bsc_intent_broadcast_failure(
            db,
            snapshot=snapshot,
            error=exc,
            review_required=True,
            reason_code=(
                "unexpected_bsc_intent_error"
            ),
            now=now,
        )
    except Exception as exc:
        return _persist_bsc_intent_broadcast_failure(
            db,
            snapshot=snapshot,
            error=exc,
            review_required=True,
            reason_code=(
                "unexpected_broadcast_exception"
            ),
            now=now,
        )

    return _persist_bsc_intent_broadcast_success(
        db,
        snapshot=snapshot,
        result=broadcast_result,
        now=now,
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
