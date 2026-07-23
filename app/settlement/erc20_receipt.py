from __future__ import annotations

import hashlib
import json

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


ERC20_TRANSFER_TOPIC0 = (
    "0xddf252ad1be2c89b69c2b068fc378daa"
    "952ba7f163c4a11628f55a4df523b3ef"
)


class Erc20ReceiptError(RuntimeError):
    pass


@dataclass(frozen=True)
class Erc20TransferLog:
    contract_address: str
    transaction_hash: str
    log_index: int
    from_address: str
    to_address: str
    amount_raw: int
    amount: Decimal


@dataclass(frozen=True)
class ExactErc20ReceiptMatch:
    contract_address: str
    transaction_hash: str
    destination_address: str
    expected_source_address: str | None
    decimals: int
    expected_amount_raw: int
    received_amount_raw: int
    received_amount: Decimal
    transfers: tuple[Erc20TransferLog, ...]
    receipt_fingerprint: str


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


def _hex_bytes(
    value: Any,
    *,
    expected_length: int,
    field_name: str,
) -> bytes:
    if isinstance(value, bytes):
        raw = bytes(value)
    else:
        text = str(value or "").strip().lower()

        if text.startswith("0x"):
            text = text[2:]

        if not text:
            raise Erc20ReceiptError(
                f"{field_name} is empty"
            )

        try:
            raw = bytes.fromhex(text)
        except ValueError as exc:
            raise Erc20ReceiptError(
                f"{field_name} is not valid hex"
            ) from exc

    if len(raw) != expected_length:
        raise Erc20ReceiptError(
            f"{field_name} must be "
            f"{expected_length} bytes"
        )

    return raw


def normalize_evm_address(
    value: Any,
    *,
    field_name: str,
) -> str:
    raw = _hex_bytes(
        value,
        expected_length=20,
        field_name=field_name,
    )

    return f"0x{raw.hex()}"


def normalize_transaction_hash(
    value: Any,
    *,
    field_name: str = "transaction_hash",
) -> str:
    raw = _hex_bytes(
        value,
        expected_length=32,
        field_name=field_name,
    )

    return f"0x{raw.hex()}"


def _normalize_topic(
    value: Any,
    *,
    field_name: str,
) -> str:
    raw = _hex_bytes(
        value,
        expected_length=32,
        field_name=field_name,
    )

    return f"0x{raw.hex()}"


def _topic_address(
    value: Any,
    *,
    field_name: str,
) -> str:
    raw = _hex_bytes(
        value,
        expected_length=32,
        field_name=field_name,
    )

    return f"0x{raw[-20:].hex()}"


def _uint256_from_data(
    value: Any,
    *,
    field_name: str,
) -> int:
    raw = _hex_bytes(
        value,
        expected_length=32,
        field_name=field_name,
    )

    return int.from_bytes(
        raw,
        byteorder="big",
        signed=False,
    )


def _nonnegative_log_index(
    value: Any,
) -> int:
    try:
        if (
            isinstance(value, str)
            and value.strip().lower().startswith("0x")
        ):
            normalized = int(
                value.strip(),
                16,
            )
        else:
            normalized = int(value)
    except (TypeError, ValueError) as exc:
        raise Erc20ReceiptError(
            "ERC20 logIndex must be an integer"
        ) from exc

    if normalized < 0:
        raise Erc20ReceiptError(
            "ERC20 logIndex cannot be negative"
        )

    return normalized


def _validated_decimals(
    value: Any,
) -> int:
    try:
        decimals = int(value)
    except (TypeError, ValueError) as exc:
        raise Erc20ReceiptError(
            "ERC20 decimals must be an integer"
        ) from exc

    if decimals < 0 or decimals > 255:
        raise Erc20ReceiptError(
            "ERC20 decimals are outside "
            "the supported range"
        )

    return decimals


def exact_decimal_amount_to_raw(
    amount: Any,
    *,
    decimals: int,
) -> int:
    if isinstance(amount, float):
        raise Erc20ReceiptError(
            "Float ERC20 amount is forbidden"
        )

    validated_decimals = _validated_decimals(
        decimals
    )

    try:
        normalized = Decimal(str(amount))
    except Exception as exc:
        raise Erc20ReceiptError(
            "ERC20 amount is invalid"
        ) from exc

    if not normalized.is_finite():
        raise Erc20ReceiptError(
            "ERC20 amount must be finite"
        )

    if normalized <= 0:
        raise Erc20ReceiptError(
            "ERC20 amount must be positive"
        )

    scale = Decimal(10) ** validated_decimals
    scaled = normalized * scale
    integral = scaled.to_integral_value()

    if scaled != integral:
        raise Erc20ReceiptError(
            "ERC20 amount cannot be represented "
            "exactly with configured decimals"
        )

    return int(integral)


def _canonical_decimal(
    value: Decimal,
) -> str:
    normalized = format(value, "f")

    if "." in normalized:
        normalized = normalized.rstrip("0").rstrip(".")

    return normalized or "0"


def _receipt_fingerprint(
    *,
    contract_address: str,
    transaction_hash: str,
    destination_address: str,
    expected_source_address: str | None,
    decimals: int,
    expected_amount_raw: int,
    received_amount_raw: int,
    transfers: tuple[Erc20TransferLog, ...],
) -> str:
    payload = {
        "schema": "exact_erc20_receipt_match_v1",
        "contract_address": contract_address,
        "transaction_hash": transaction_hash,
        "destination_address": (
            destination_address
        ),
        "expected_source_address": (
            expected_source_address
        ),
        "decimals": decimals,
        "expected_amount_raw": (
            str(expected_amount_raw)
        ),
        "received_amount_raw": (
            str(received_amount_raw)
        ),
        "transfers": [
            {
                "contract_address": (
                    transfer.contract_address
                ),
                "transaction_hash": (
                    transfer.transaction_hash
                ),
                "log_index": transfer.log_index,
                "from_address": (
                    transfer.from_address
                ),
                "to_address": transfer.to_address,
                "amount_raw": str(
                    transfer.amount_raw
                ),
                "amount": _canonical_decimal(
                    transfer.amount
                ),
            }
            for transfer in transfers
        ],
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


def parse_exact_erc20_transfer_receipt(
    receipt: Any,
    *,
    transaction_hash: Any,
    token_contract: Any,
    destination_address: Any,
    expected_amount: Any,
    decimals: int,
    expected_source_address: Any | None = None,
) -> ExactErc20ReceiptMatch:
    normalized_tx_hash = (
        normalize_transaction_hash(
            transaction_hash
        )
    )
    normalized_contract = normalize_evm_address(
        token_contract,
        field_name="token_contract",
    )
    normalized_destination = (
        normalize_evm_address(
            destination_address,
            field_name="destination_address",
        )
    )
    normalized_source = (
        normalize_evm_address(
            expected_source_address,
            field_name="expected_source_address",
        )
        if expected_source_address is not None
        else None
    )
    validated_decimals = _validated_decimals(
        decimals
    )
    expected_amount_raw = (
        exact_decimal_amount_to_raw(
            expected_amount,
            decimals=validated_decimals,
        )
    )

    receipt_tx_hash = normalize_transaction_hash(
        _value_get(
            receipt,
            "transactionHash",
        ),
        field_name="receipt.transactionHash",
    )

    if receipt_tx_hash != normalized_tx_hash:
        raise Erc20ReceiptError(
            "Receipt transaction hash mismatch"
        )

    raw_logs = _value_get(
        receipt,
        "logs",
        [],
    )

    if raw_logs is None:
        raw_logs = []

    if not isinstance(
        raw_logs,
        (list, tuple),
    ):
        raise Erc20ReceiptError(
            "Receipt logs must be a sequence"
        )

    matching: list[Erc20TransferLog] = []
    used_log_indexes: set[int] = set()

    for raw_log in raw_logs:
        try:
            log_contract = normalize_evm_address(
                _value_get(
                    raw_log,
                    "address",
                ),
                field_name="log.address",
            )
        except Erc20ReceiptError:
            continue

        if log_contract != normalized_contract:
            continue

        topics = _value_get(
            raw_log,
            "topics",
            [],
        )

        if not isinstance(
            topics,
            (list, tuple),
        ):
            raise Erc20ReceiptError(
                "ERC20 log topics must be a sequence"
            )

        if not topics:
            continue

        topic0 = _normalize_topic(
            topics[0],
            field_name="log.topics[0]",
        )

        if topic0 != ERC20_TRANSFER_TOPIC0:
            continue

        if len(topics) != 3:
            raise Erc20ReceiptError(
                "ERC20 Transfer log must have "
                "exactly three topics"
            )

        from_address = _topic_address(
            topics[1],
            field_name="log.topics[1]",
        )
        to_address = _topic_address(
            topics[2],
            field_name="log.topics[2]",
        )

        if to_address != normalized_destination:
            continue

        if (
            normalized_source is not None
            and from_address != normalized_source
        ):
            continue

        log_tx_hash = normalize_transaction_hash(
            _value_get(
                raw_log,
                "transactionHash",
            ),
            field_name="log.transactionHash",
        )

        if log_tx_hash != normalized_tx_hash:
            raise Erc20ReceiptError(
                "ERC20 log transaction hash mismatch"
            )

        log_index = _nonnegative_log_index(
            _value_get(
                raw_log,
                "logIndex",
            )
        )

        if log_index in used_log_indexes:
            raise Erc20ReceiptError(
                "Duplicate matching ERC20 logIndex"
            )

        used_log_indexes.add(log_index)

        amount_raw = _uint256_from_data(
            _value_get(
                raw_log,
                "data",
            ),
            field_name="log.data",
        )
        amount = (
            Decimal(amount_raw)
            / (
                Decimal(10)
                ** validated_decimals
            )
        )

        matching.append(
            Erc20TransferLog(
                contract_address=log_contract,
                transaction_hash=log_tx_hash,
                log_index=log_index,
                from_address=from_address,
                to_address=to_address,
                amount_raw=amount_raw,
                amount=amount,
            )
        )

    transfers = tuple(
        sorted(
            matching,
            key=lambda item: item.log_index,
        )
    )
    received_amount_raw = sum(
        item.amount_raw
        for item in transfers
    )

    if received_amount_raw != expected_amount_raw:
        raise Erc20ReceiptError(
            "Exact ERC20 transfer amount mismatch: "
            f"expected_raw={expected_amount_raw} "
            f"received_raw={received_amount_raw}"
        )

    received_amount = (
        Decimal(received_amount_raw)
        / (
            Decimal(10)
            ** validated_decimals
        )
    )

    fingerprint = _receipt_fingerprint(
        contract_address=normalized_contract,
        transaction_hash=normalized_tx_hash,
        destination_address=(
            normalized_destination
        ),
        expected_source_address=(
            normalized_source
        ),
        decimals=validated_decimals,
        expected_amount_raw=expected_amount_raw,
        received_amount_raw=(
            received_amount_raw
        ),
        transfers=transfers,
    )

    return ExactErc20ReceiptMatch(
        contract_address=normalized_contract,
        transaction_hash=normalized_tx_hash,
        destination_address=(
            normalized_destination
        ),
        expected_source_address=(
            normalized_source
        ),
        decimals=validated_decimals,
        expected_amount_raw=expected_amount_raw,
        received_amount_raw=(
            received_amount_raw
        ),
        received_amount=received_amount,
        transfers=transfers,
        receipt_fingerprint=fingerprint,
    )