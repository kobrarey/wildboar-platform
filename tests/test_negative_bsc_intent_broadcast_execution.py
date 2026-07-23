import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from web3.exceptions import TransactionNotFound

from app.models import FundBscTransactionIntent
from app.settlement import bsc_intent_service
from app.settlement.bsc_intent_service import (
    BroadcastBscTransactionResult,
    PreparedBscTransaction,
    build_bsc_intent_safe_audit,
    execute_claimed_bsc_intent_broadcast,
)
from app.settlement.statuses import (
    BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT,
    BSC_INTENT_STATUS_BROADCAST,
    BSC_INTENT_STATUS_BROADCASTING,
    BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW,
    BSC_INTENT_STATUS_VISIBLE,
)


CLAIM_TOKEN = "a" * 32


class FakeQuery:
    def __init__(
        self,
        session: "FakeSession",
    ) -> None:
        self.session = session

    def filter(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "FakeQuery":
        return self

    def with_for_update(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "FakeQuery":
        return self

    def first(self) -> Any:
        self.session.query_count += 1

        return self.session.intent


class FakeSession:
    def __init__(
        self,
        intent: FundBscTransactionIntent,
    ) -> None:
        self.intent = intent
        self.query_count = 0
        self.execute_count = 0
        self.commit_count = 0
        self.rollback_count = 0
        self.refresh_count = 0
        self.added: list[Any] = []

    def query(
        self,
        model: type[Any],
    ) -> FakeQuery:
        assert model is FundBscTransactionIntent

        return FakeQuery(self)

    def execute(
        self,
        statement: Any,
        params: dict[str, Any] | None = None,
    ) -> None:
        self.execute_count += 1

    def add(self, value: Any) -> None:
        assert value is self.intent
        self.added.append(value)

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1

    def refresh(self, value: Any) -> None:
        assert value is self.intent
        self.refresh_count += 1


class FakeEth:
    def __init__(
        self,
        *,
        tx_hash: str,
        visible: bool = False,
        nonce_values: list[int] | None = None,
        send_error: Exception | None = None,
        chain_id: int = 56,
    ) -> None:
        self.tx_hash = tx_hash
        self.visible = visible
        self.nonce_values = list(
            nonce_values or [7]
        )
        self.send_error = send_error
        self.chain_id = chain_id

        self.send_count = 0
        self.get_transaction_count_calls = 0

    def get_transaction(
        self,
        tx_hash: str,
    ) -> dict[str, str]:
        if not self.visible:
            raise TransactionNotFound(tx_hash)

        return {
            "hash": tx_hash,
        }

    def get_transaction_count(
        self,
        address: str,
        block_identifier: str,
    ) -> int:
        assert block_identifier == "pending"

        index = min(
            self.get_transaction_count_calls,
            len(self.nonce_values) - 1,
        )
        self.get_transaction_count_calls += 1

        return self.nonce_values[index]

    def send_raw_transaction(
        self,
        raw_tx: bytes,
    ) -> bytes:
        assert raw_tx == bytes.fromhex("0102")

        self.send_count += 1

        if self.send_error is not None:
            raise self.send_error

        self.visible = True

        return bytes.fromhex(
            self.tx_hash.removeprefix("0x")
        )


class FakeWeb3:
    def __init__(self, eth: FakeEth) -> None:
        self.eth = eth

    def to_checksum_address(
        self,
        address: str,
    ) -> str:
        return address

    def to_hex(
        self,
        value: bytes | str,
    ) -> str:
        if isinstance(value, bytes):
            return f"0x{value.hex()}"

        return str(value)


def _prepared() -> PreparedBscTransaction:
    return PreparedBscTransaction(
        chain_id=56,
        source_nonce=7,
        tx_hash=f"0x{'ab' * 32}",
        raw_tx_hex="0x0102",
    )


def _intent(
    *,
    status: str = BSC_INTENT_STATUS_BROADCASTING,
) -> FundBscTransactionIntent:
    prepared = _prepared()

    audit = build_bsc_intent_safe_audit(
        scope_key="negative-payout:11:22:33",
        action_type=(
            BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT
        ),
        settlement_batch_id=11,
        payout_batch_id=22,
        payout_leg_id=33,
        fund_id=9,
        asset="USDT",
        amount=Decimal("10.5"),
        from_address="0xabcdef",
        to_address="0x123456",
        prepared=prepared,
    )

    created_at = datetime(
        2026,
        7,
        23,
        10,
        0,
        tzinfo=timezone.utc,
    )

    return FundBscTransactionIntent(
        id=42,
        scope_key="negative-payout:11:22:33",
        action_type=(
            BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT
        ),
        settlement_batch_id=11,
        payout_batch_id=22,
        payout_leg_id=33,
        fund_id=9,
        asset="USDT",
        amount=Decimal("10.5"),
        from_address="0xabcdef",
        to_address="0x123456",
        chain_id=56,
        source_nonce=7,
        prepared_tx_hash=prepared.tx_hash,
        prepared_raw_tx=prepared.raw_tx_hex,
        intent_fingerprint=(
            audit["intent_fingerprint"]
        ),
        status=status,
        broadcast_attempts=1,
        prepared_at=created_at,
        broadcast_started_at=created_at,
        prepared_json={
            **audit,
            "durable_boundary": (
                "prepared_before_broadcast"
            ),
        },
        broadcast_json={
            "schema": (
                "fund_bsc_transaction_intent_broadcast_v1"
            ),
            "phase": "attempt_claimed",
            "claim_token": CLAIM_TOKEN,
            "claimed_at": (
                created_at.isoformat()
            ),
            "broadcast_attempts": 1,
        },
        created_at=created_at,
        updated_at=created_at,
    )


def test_claimed_cycle_broadcasts_once_and_persists():
    intent = _intent()
    db = FakeSession(intent)
    eth = FakeEth(
        tx_hash=intent.prepared_tx_hash,
        nonce_values=[7],
    )

    result = execute_claimed_bsc_intent_broadcast(
        db,
        FakeWeb3(eth),
        intent_id=intent.id,
        claim_token=CLAIM_TOKEN,
    )

    assert result.action == "broadcast"
    assert result.status == (
        BSC_INTENT_STATUS_BROADCAST
    )
    assert eth.send_count == 1

    assert intent.status == (
        BSC_INTENT_STATUS_BROADCAST
    )
    assert intent.broadcast_at is not None
    assert intent.broadcast_json["phase"] == (
        "result_persisted"
    )
    assert (
        intent.broadcast_json["result_action"]
        == "broadcast"
    )

    assert db.commit_count == 2
    assert db.execute_count == 2

    serialized = json.dumps(
        intent.broadcast_json,
        sort_keys=True,
    )

    assert intent.prepared_raw_tx not in serialized
    assert "prepared_raw_tx" not in serialized
    assert "raw_tx_hex" not in serialized


def test_restart_reconciles_visible_hash_without_send():
    intent = _intent()
    db = FakeSession(intent)
    eth = FakeEth(
        tx_hash=intent.prepared_tx_hash,
        visible=True,
    )

    result = execute_claimed_bsc_intent_broadcast(
        db,
        FakeWeb3(eth),
        intent_id=intent.id,
        claim_token=CLAIM_TOKEN,
    )

    assert result.action == "already_visible"
    assert result.status == (
        BSC_INTENT_STATUS_VISIBLE
    )
    assert eth.send_count == 0
    assert intent.visible_at is not None


def test_retryable_error_releases_claim_for_reconciliation():
    intent = _intent()
    db = FakeSession(intent)
    eth = FakeEth(
        tx_hash=intent.prepared_tx_hash,
        nonce_values=[7, 7],
        send_error=RuntimeError(
            "temporary provider failure"
        ),
    )

    result = execute_claimed_bsc_intent_broadcast(
        db,
        FakeWeb3(eth),
        intent_id=intent.id,
        claim_token=CLAIM_TOKEN,
    )

    assert result.action == "retryable_error"
    assert result.status == (
        BSC_INTENT_STATUS_BROADCASTING
    )
    assert eth.send_count == 1

    assert intent.status == (
        BSC_INTENT_STATUS_BROADCASTING
    )
    assert intent.broadcast_json["phase"] == (
        "retryable_reconciliation_required"
    )
    assert "claim_token" not in (
        intent.broadcast_json
    )


def test_consumed_nonce_without_hash_fails_review():
    intent = _intent()
    db = FakeSession(intent)
    eth = FakeEth(
        tx_hash=intent.prepared_tx_hash,
        nonce_values=[7, 8],
        send_error=RuntimeError(
            "provider timeout"
        ),
    )

    result = execute_claimed_bsc_intent_broadcast(
        db,
        FakeWeb3(eth),
        intent_id=intent.id,
        claim_token=CLAIM_TOKEN,
    )

    assert result.action == (
        "failed_requires_review"
    )
    assert result.status == (
        BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert eth.send_count == 1
    assert intent.failed_at is not None

    serialized = json.dumps(
        intent.reconciliation_json,
        sort_keys=True,
    )

    assert intent.prepared_raw_tx not in serialized


def test_later_status_never_calls_web3():
    intent = _intent(
        status=BSC_INTENT_STATUS_BROADCAST
    )
    db = FakeSession(intent)
    eth = FakeEth(
        tx_hash=intent.prepared_tx_hash,
    )

    result = execute_claimed_bsc_intent_broadcast(
        db,
        FakeWeb3(eth),
        intent_id=intent.id,
        claim_token=CLAIM_TOKEN,
    )

    assert result.action == "status_broadcast"
    assert eth.send_count == 0
    assert (
        eth.get_transaction_count_calls
        == 0
    )
    assert db.commit_count == 1


def test_claim_lost_after_external_cannot_overwrite(
    monkeypatch,
):
    intent = _intent()
    db = FakeSession(intent)

    calls = 0

    def fake_broadcast(
        *args: Any,
        **kwargs: Any,
    ) -> BroadcastBscTransactionResult:
        nonlocal calls
        calls += 1

        intent.broadcast_json = {
            **dict(intent.broadcast_json),
            "claim_token": "b" * 32,
        }

        return BroadcastBscTransactionResult(
            action="broadcast",
            tx_hash=intent.prepared_tx_hash,
        )

    monkeypatch.setattr(
        bsc_intent_service,
        "broadcast_prepared_transaction",
        fake_broadcast,
    )

    result = execute_claimed_bsc_intent_broadcast(
        db,
        FakeWeb3(
            FakeEth(
                tx_hash=(
                    intent.prepared_tx_hash
                ),
            )
        ),
        intent_id=intent.id,
        claim_token=CLAIM_TOKEN,
    )

    assert calls == 1
    assert result.action == (
        "claim_lost_after_external"
    )
    assert intent.status == (
        BSC_INTENT_STATUS_BROADCASTING
    )
    assert intent.broadcast_at is None