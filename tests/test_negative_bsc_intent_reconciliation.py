from decimal import Decimal
from typing import Any

from web3.exceptions import TransactionNotFound

from app.models import FundBscTransactionIntent
from app.settlement.bsc_intent_reconciliation import (
    reconcile_bsc_transaction_intent,
)
from app.settlement.erc20_receipt import (
    ERC20_TRANSFER_TOPIC0,
)
from app.settlement.statuses import (
    BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT,
    BSC_INTENT_ACTION_NEGATIVE_SETTLEMENT_GAS_TOPUP,
    BSC_INTENT_STATUS_BROADCAST,
    BSC_INTENT_STATUS_CONFIRMED,
    BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW,
    BSC_INTENT_STATUS_PENDING_CONFIRMATION,
    BSC_INTENT_STATUS_VISIBLE,
)


TX_HASH = f"0x{'ab' * 32}"
OTHER_TX_HASH = f"0x{'cd' * 32}"
SOURCE = f"0x{'11' * 20}"
DESTINATION = f"0x{'22' * 20}"
USDT = (
    "0x55d398326f99059ff775485246999027b"
    "3197955"
)


def _address_topic(
    address: str,
) -> str:
    return (
        "0x"
        + ("00" * 12)
        + address.removeprefix("0x")
    )


def _uint256_data(
    value: int,
) -> str:
    return f"0x{value:064x}"


def _transfer_log(
    *,
    amount_raw: int,
    destination: str = DESTINATION,
    contract: str = USDT,
    tx_hash: str = TX_HASH,
    log_index: int = 1,
) -> dict[str, Any]:
    return {
        "address": contract,
        "transactionHash": tx_hash,
        "logIndex": log_index,
        "topics": [
            ERC20_TRANSFER_TOPIC0,
            _address_topic(SOURCE),
            _address_topic(destination),
        ],
        "data": _uint256_data(amount_raw),
    }


def _gas_intent() -> FundBscTransactionIntent:
    return FundBscTransactionIntent(
        id=1,
        scope_key="negative-gas:10:20",
        action_type=(
            BSC_INTENT_ACTION_NEGATIVE_SETTLEMENT_GAS_TOPUP
        ),
        asset="BNB",
        amount=Decimal("0.01"),
        from_address=SOURCE,
        to_address=DESTINATION,
        chain_id=56,
        source_nonce=7,
        prepared_tx_hash=TX_HASH,
        intent_fingerprint="a" * 64,
        status=BSC_INTENT_STATUS_BROADCAST,
    )


def _payout_intent() -> FundBscTransactionIntent:
    return FundBscTransactionIntent(
        id=2,
        scope_key="negative-payout:10:20:30",
        action_type=(
            BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT
        ),
        asset="USDT",
        amount=Decimal("10"),
        from_address=SOURCE,
        to_address=DESTINATION,
        chain_id=56,
        source_nonce=8,
        prepared_tx_hash=TX_HASH,
        intent_fingerprint="b" * 64,
        status=BSC_INTENT_STATUS_BROADCAST,
    )


def _gas_transaction(
    **overrides: Any,
) -> dict[str, Any]:
    result = {
        "hash": TX_HASH,
        "from": SOURCE,
        "to": DESTINATION,
        "nonce": 7,
        "value": 10**16,
    }
    result.update(overrides)
    return result


def _payout_transaction(
    **overrides: Any,
) -> dict[str, Any]:
    result = {
        "hash": TX_HASH,
        "from": SOURCE,
        "to": USDT,
        "nonce": 8,
        "value": 0,
    }
    result.update(overrides)
    return result


def _receipt(
    *,
    status: int = 1,
    block_number: int = 100,
    tx_hash: str = TX_HASH,
    logs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "transactionHash": tx_hash,
        "status": status,
        "blockNumber": block_number,
        "logs": list(logs or []),
    }


class FakeEth:
    def __init__(
        self,
        *,
        transaction: Any = None,
        receipt: Any = None,
        chain_id: int = 56,
        block_number: int = 111,
        transaction_error: BaseException | None = None,
        receipt_error: BaseException | None = None,
    ) -> None:
        self.transaction = transaction
        self.receipt = receipt
        self.chain_id = chain_id
        self.block_number = block_number
        self.transaction_error = transaction_error
        self.receipt_error = receipt_error
        self.send_calls = 0

    def get_transaction(
        self,
        tx_hash: str,
    ) -> Any:
        assert tx_hash == TX_HASH

        if self.transaction_error is not None:
            raise self.transaction_error

        return self.transaction

    def get_transaction_receipt(
        self,
        tx_hash: str,
    ) -> Any:
        assert tx_hash == TX_HASH

        if self.receipt_error is not None:
            raise self.receipt_error

        return self.receipt

    def send_raw_transaction(
        self,
        value: Any,
    ) -> None:
        self.send_calls += 1
        raise AssertionError(
            "Reconciliation must never broadcast"
        )


class FakeWeb3:
    def __init__(
        self,
        eth: FakeEth,
    ) -> None:
        self.eth = eth


def test_transaction_not_visible_is_read_only():
    eth = FakeEth(
        transaction_error=TransactionNotFound(
            "missing"
        ),
    )

    result = reconcile_bsc_transaction_intent(
        FakeWeb3(eth),
        intent=_gas_intent(),
        required_confirmations=12,
    )

    assert result.action == "not_visible"
    assert result.reason_code == (
        "transaction_not_visible"
    )
    assert eth.send_calls == 0


def test_visible_transaction_without_receipt():
    eth = FakeEth(
        transaction=_gas_transaction(),
        receipt_error=TransactionNotFound(
            "missing receipt"
        ),
    )

    result = reconcile_bsc_transaction_intent(
        FakeWeb3(eth),
        intent=_gas_intent(),
        required_confirmations=12,
    )

    assert result.action == "visible"
    assert result.suggested_status == (
        BSC_INTENT_STATUS_VISIBLE
    )
    assert result.receipt_status is None


def test_gas_receipt_pending_confirmations():
    eth = FakeEth(
        transaction=_gas_transaction(),
        receipt=_receipt(),
        block_number=110,
    )

    result = reconcile_bsc_transaction_intent(
        FakeWeb3(eth),
        intent=_gas_intent(),
        required_confirmations=12,
    )

    assert result.action == (
        "pending_confirmation"
    )
    assert result.suggested_status == (
        BSC_INTENT_STATUS_PENDING_CONFIRMATION
    )
    assert result.confirmations == 11
    assert (
        result.reconciliation_fingerprint
        is not None
    )


def test_gas_receipt_confirmed():
    eth = FakeEth(
        transaction=_gas_transaction(),
        receipt=_receipt(),
        block_number=111,
    )

    result = reconcile_bsc_transaction_intent(
        FakeWeb3(eth),
        intent=_gas_intent(),
        required_confirmations=12,
    )

    assert result.action == "confirmed"
    assert result.suggested_status == (
        BSC_INTENT_STATUS_CONFIRMED
    )
    assert result.receipt_status == 1
    assert result.block_number == 100
    assert result.confirmations == 12


def test_gas_transaction_envelope_mismatch():
    eth = FakeEth(
        transaction=_gas_transaction(
            to=f"0x{'33' * 20}",
        ),
        receipt=_receipt(),
    )

    result = reconcile_bsc_transaction_intent(
        FakeWeb3(eth),
        intent=_gas_intent(),
        required_confirmations=12,
    )

    assert result.action == (
        "failed_requires_review"
    )
    assert result.reason_code == (
        "transaction_envelope_mismatch"
    )
    assert (
        "transaction.to"
        in result.evidence["mismatch_fields"]
    )


def test_exact_usdt_payout_receipt_confirmed():
    amount_raw = 10 * 10**18
    eth = FakeEth(
        transaction=_payout_transaction(),
        receipt=_receipt(
            logs=[
                _transfer_log(
                    amount_raw=amount_raw,
                )
            ]
        ),
        block_number=111,
    )

    result = reconcile_bsc_transaction_intent(
        FakeWeb3(eth),
        intent=_payout_intent(),
        required_confirmations=12,
    )

    assert result.action == "confirmed"
    assert result.confirmations == 12
    assert (
        result.evidence["erc20_transfer"][
            "received_amount_raw"
        ]
        == str(amount_raw)
    )
    assert (
        len(
            result.evidence["erc20_transfer"][
                "receipt_fingerprint"
            ]
        )
        == 64
    )


def test_wrong_usdt_payout_amount_fails():
    eth = FakeEth(
        transaction=_payout_transaction(),
        receipt=_receipt(
            logs=[
                _transfer_log(
                    amount_raw=9 * 10**18,
                )
            ]
        ),
    )

    result = reconcile_bsc_transaction_intent(
        FakeWeb3(eth),
        intent=_payout_intent(),
        required_confirmations=12,
    )

    assert result.action == (
        "failed_requires_review"
    )
    assert result.reason_code == (
        "erc20_transfer_receipt_mismatch"
    )


def test_failed_receipt_fails_requires_review():
    eth = FakeEth(
        transaction=_gas_transaction(),
        receipt=_receipt(status=0),
    )

    result = reconcile_bsc_transaction_intent(
        FakeWeb3(eth),
        intent=_gas_intent(),
        required_confirmations=12,
    )

    assert result.action == (
        "failed_requires_review"
    )
    assert result.suggested_status == (
        BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert result.reason_code == (
        "receipt_execution_failed"
    )
    assert result.receipt_status == 0


def test_chain_mismatch_fails_before_receipt():
    eth = FakeEth(
        transaction=_gas_transaction(),
        receipt=_receipt(),
        chain_id=97,
    )

    result = reconcile_bsc_transaction_intent(
        FakeWeb3(eth),
        intent=_gas_intent(),
        required_confirmations=12,
    )

    assert result.action == (
        "failed_requires_review"
    )
    assert result.reason_code == (
        "chain_id_mismatch"
    )


def test_receipt_hash_mismatch_fails():
    eth = FakeEth(
        transaction=_gas_transaction(),
        receipt=_receipt(
            tx_hash=OTHER_TX_HASH,
        ),
    )

    result = reconcile_bsc_transaction_intent(
        FakeWeb3(eth),
        intent=_gas_intent(),
        required_confirmations=12,
    )

    assert result.action == (
        "failed_requires_review"
    )
    assert result.reason_code == (
        "receipt_transaction_hash_mismatch"
    )


def test_rpc_error_is_retryable():
    eth = FakeEth(
        transaction_error=RuntimeError(
            "temporary rpc failure"
        ),
    )

    result = reconcile_bsc_transaction_intent(
        FakeWeb3(eth),
        intent=_gas_intent(),
        required_confirmations=12,
    )

    assert result.action == "retryable_error"
    assert result.reason_code == (
        "transaction_lookup_unavailable"
    )
    assert result.suggested_status == (
        BSC_INTENT_STATUS_BROADCAST
    )


def test_confirmed_fingerprint_is_stable_as_chain_advances():
    eth = FakeEth(
        transaction=_payout_transaction(),
        receipt=_receipt(
            logs=[
                _transfer_log(
                    amount_raw=10 * 10**18,
                )
            ]
        ),
        block_number=111,
    )
    w3 = FakeWeb3(eth)
    intent = _payout_intent()

    first = reconcile_bsc_transaction_intent(
        w3,
        intent=intent,
        required_confirmations=12,
    )

    eth.block_number = 150

    second = reconcile_bsc_transaction_intent(
        w3,
        intent=intent,
        required_confirmations=12,
    )

    assert first.action == "confirmed"
    assert second.action == "confirmed"
    assert first.confirmations == 12
    assert second.confirmations == 51
    assert (
        first.reconciliation_fingerprint
        == second.reconciliation_fingerprint
    )
    assert eth.send_calls == 0
