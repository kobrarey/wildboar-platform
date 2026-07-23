from decimal import Decimal

import pytest

from app.settlement.erc20_receipt import (
    ERC20_TRANSFER_TOPIC0,
    Erc20ReceiptError,
    exact_decimal_amount_to_raw,
    parse_exact_erc20_transfer_receipt,
)


TX_HASH = f"0x{'ab' * 32}"
SOURCE = f"0x{'11' * 20}"
DESTINATION = f"0x{'22' * 20}"
USDT = (
    "0x55d398326f99059ff775485246999027b"
    "3197955"
)
OTHER_TOKEN = f"0x{'33' * 20}"
OTHER_ADDRESS = f"0x{'44' * 20}"


def _address_topic(address: str) -> str:
    return (
        "0x"
        + ("00" * 12)
        + address.removeprefix("0x")
    )


def _uint256_data(value: int) -> str:
    return f"0x{value:064x}"


def _transfer_log(
    *,
    contract: str = USDT,
    source: str = SOURCE,
    destination: str = DESTINATION,
    amount_raw: int,
    log_index: int,
    tx_hash: str = TX_HASH,
) -> dict:
    return {
        "address": contract,
        "transactionHash": tx_hash,
        "logIndex": log_index,
        "topics": [
            ERC20_TRANSFER_TOPIC0,
            _address_topic(source),
            _address_topic(destination),
        ],
        "data": _uint256_data(amount_raw),
    }


def _receipt(logs: list[dict]) -> dict:
    return {
        "transactionHash": TX_HASH,
        "logs": logs,
    }


def _parse(
    logs: list[dict],
    *,
    expected_amount: Decimal = Decimal("10"),
    expected_source: str | None = SOURCE,
):
    return parse_exact_erc20_transfer_receipt(
        _receipt(logs),
        transaction_hash=TX_HASH,
        token_contract=USDT,
        destination_address=DESTINATION,
        expected_source_address=(
            expected_source
        ),
        expected_amount=expected_amount,
        decimals=18,
    )


def test_correct_transfer_log_is_parsed_exactly():
    amount_raw = 10 * 10**18

    result = _parse(
        [
            _transfer_log(
                amount_raw=amount_raw,
                log_index=7,
            )
        ]
    )

    assert result.expected_amount_raw == amount_raw
    assert result.received_amount_raw == amount_raw
    assert result.received_amount == Decimal("10")
    assert len(result.transfers) == 1
    assert result.transfers[0].log_index == 7
    assert len(result.receipt_fingerprint) == 64


def test_wrong_contract_is_not_counted():
    with pytest.raises(
        Erc20ReceiptError,
        match="amount mismatch",
    ):
        _parse(
            [
                _transfer_log(
                    contract=OTHER_TOKEN,
                    amount_raw=10 * 10**18,
                    log_index=1,
                )
            ]
        )


def test_wrong_destination_is_not_counted():
    with pytest.raises(
        Erc20ReceiptError,
        match="amount mismatch",
    ):
        _parse(
            [
                _transfer_log(
                    destination=OTHER_ADDRESS,
                    amount_raw=10 * 10**18,
                    log_index=1,
                )
            ]
        )


def test_wrong_source_is_not_counted():
    with pytest.raises(
        Erc20ReceiptError,
        match="amount mismatch",
    ):
        _parse(
            [
                _transfer_log(
                    source=OTHER_ADDRESS,
                    amount_raw=10 * 10**18,
                    log_index=1,
                )
            ]
        )


def test_wrong_amount_fails_closed():
    with pytest.raises(
        Erc20ReceiptError,
        match="amount mismatch",
    ):
        _parse(
            [
                _transfer_log(
                    amount_raw=9 * 10**18,
                    log_index=1,
                )
            ]
        )


def test_unrelated_logs_are_ignored():
    result = _parse(
        [
            _transfer_log(
                contract=OTHER_TOKEN,
                amount_raw=100 * 10**18,
                log_index=1,
            ),
            _transfer_log(
                destination=OTHER_ADDRESS,
                amount_raw=50 * 10**18,
                log_index=2,
            ),
            _transfer_log(
                amount_raw=10 * 10**18,
                log_index=3,
            ),
        ]
    )

    assert len(result.transfers) == 1
    assert result.transfers[0].log_index == 3
    assert result.received_amount == Decimal("10")


def test_multiple_matching_logs_are_summed():
    result = _parse(
        [
            _transfer_log(
                amount_raw=4 * 10**18,
                log_index=8,
            ),
            _transfer_log(
                amount_raw=6 * 10**18,
                log_index=3,
            ),
        ]
    )

    assert result.received_amount == Decimal("10")
    assert [
        item.log_index
        for item in result.transfers
    ] == [3, 8]


def test_fingerprint_is_independent_of_log_order():
    first_log = _transfer_log(
        amount_raw=4 * 10**18,
        log_index=8,
    )
    second_log = _transfer_log(
        amount_raw=6 * 10**18,
        log_index=3,
    )

    first = _parse(
        [
            first_log,
            second_log,
        ]
    )
    second = _parse(
        [
            second_log,
            first_log,
        ]
    )

    assert (
        first.receipt_fingerprint
        == second.receipt_fingerprint
    )


def test_receipt_transaction_hash_mismatch_fails():
    receipt = _receipt(
        [
            _transfer_log(
                amount_raw=10 * 10**18,
                log_index=1,
            )
        ]
    )
    receipt["transactionHash"] = (
        f"0x{'cd' * 32}"
    )

    with pytest.raises(
        Erc20ReceiptError,
        match="Receipt transaction hash mismatch",
    ):
        parse_exact_erc20_transfer_receipt(
            receipt,
            transaction_hash=TX_HASH,
            token_contract=USDT,
            destination_address=DESTINATION,
            expected_source_address=SOURCE,
            expected_amount=Decimal("10"),
            decimals=18,
        )


def test_log_transaction_hash_mismatch_fails():
    with pytest.raises(
        Erc20ReceiptError,
        match="log transaction hash mismatch",
    ):
        _parse(
            [
                _transfer_log(
                    amount_raw=10 * 10**18,
                    log_index=1,
                    tx_hash=f"0x{'cd' * 32}",
                )
            ]
        )


def test_duplicate_matching_log_index_fails():
    with pytest.raises(
        Erc20ReceiptError,
        match="Duplicate matching",
    ):
        _parse(
            [
                _transfer_log(
                    amount_raw=4 * 10**18,
                    log_index=1,
                ),
                _transfer_log(
                    amount_raw=6 * 10**18,
                    log_index=1,
                ),
            ]
        )


def test_amount_must_match_configured_decimals():
    with pytest.raises(
        Erc20ReceiptError,
        match="represented exactly",
    ):
        exact_decimal_amount_to_raw(
            Decimal(
                "0.0000000000000000001"
            ),
            decimals=18,
        )


def test_withdrawal_parser_can_accept_any_source():
    result = _parse(
        [
            _transfer_log(
                source=OTHER_ADDRESS,
                amount_raw=10 * 10**18,
                log_index=1,
            )
        ],
        expected_source=None,
    )

    assert result.received_amount == Decimal("10")
    assert (
        result.expected_source_address
        is None
    )