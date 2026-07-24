from __future__ import annotations

from decimal import Decimal
from typing import Any

from web3 import Web3

from app.config import settings


BALANCE_OF_SELECTOR = "70a08231"


class BscBalanceReadError(RuntimeError):
    pass


def _address_hex(
    value: Any,
    *,
    field_name: str,
) -> str:
    raw = str(value or "").strip().lower()

    if raw.startswith("0x"):
        raw = raw[2:]

    if len(raw) != 40:
        raise BscBalanceReadError(
            f"{field_name} must contain 20 bytes"
        )

    try:
        bytes.fromhex(raw)
    except ValueError as exc:
        raise BscBalanceReadError(
            f"{field_name} must be hexadecimal"
        ) from exc

    return raw


def _usdt_contract_address() -> str:
    configured = (
        settings.USDT_BSC_ADDRESS
        or settings.BSC_USDT_CONTRACT
    )

    contract_hex = _address_hex(
        configured,
        field_name="BSC USDT contract address",
    )

    return Web3.to_checksum_address(
        f"0x{contract_hex}"
    )


def read_bsc_block_number(
    w3: Any,
) -> int:
    try:
        block_number = int(w3.eth.block_number)
    except Exception as exc:
        raise BscBalanceReadError(
            "Unable to read BSC block number"
        ) from exc

    if block_number < 0:
        raise BscBalanceReadError(
            "BSC block number cannot be negative"
        )

    return block_number


def read_bsc_usdt_balance(
    w3: Any,
    address: str,
    *,
    block_identifier: int | str,
) -> Decimal:
    address_hex = _address_hex(
        address,
        field_name="wallet address",
    )
    contract_address = (
        _usdt_contract_address()
    )

    try:
        decimals = int(
            settings.BSC_USDT_DECIMALS
        )
    except (TypeError, ValueError) as exc:
        raise BscBalanceReadError(
            "BSC_USDT_DECIMALS must be an integer"
        ) from exc

    if decimals < 0 or decimals > 36:
        raise BscBalanceReadError(
            "BSC_USDT_DECIMALS is outside "
            "the supported range"
        )

    call_data = (
        "0x"
        + BALANCE_OF_SELECTOR
        + ("0" * 24)
        + address_hex
    )

    try:
        result = w3.eth.call(
            {
                "to": contract_address,
                "data": call_data,
            },
            block_identifier,
        )
    except Exception as exc:
        raise BscBalanceReadError(
            "Unable to read BSC USDT balance"
        ) from exc

    try:
        if isinstance(result, str):
            raw_hex = result.strip().lower()

            if raw_hex.startswith("0x"):
                raw_hex = raw_hex[2:]

            raw_result = bytes.fromhex(
                raw_hex
            )
        else:
            raw_result = bytes(result)
    except (
        TypeError,
        ValueError,
        OverflowError,
    ) as exc:
        raise BscBalanceReadError(
            "Invalid BSC USDT balance response"
        ) from exc

    if len(raw_result) != 32:
        raise BscBalanceReadError(
            "BSC USDT balance response must "
            "contain exactly 32 bytes"
        )

    balance_units = int.from_bytes(
        raw_result,
        byteorder="big",
    )

    return (
        Decimal(balance_units)
        / (Decimal(10) ** decimals)
    )