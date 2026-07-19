from __future__ import annotations

from typing import Any

from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy.exc import IntegrityError

import pytest
from web3.exceptions import TransactionNotFound

import app.settlement.batch_repository as batch_repository
import app.settlement.batch_service as batch_service
import app.settlement.positive_net_service as positive_net_service
import app.settlement.transfer_service as transfer_service
from app.models import (
    Fund,
    FundOrder,
    FundSettlementBatch,
    FundSettlementTransfer,
    UserWallet,
)
from app.settlement.bsc_intent_service import (
    PreparedBscTransaction,
    broadcast_prepared_transaction,
    persist_broadcast_transfer_result,
    persist_prepared_transfer_intent,
)
from app.settlement.buy_reserve_service import (
    BuyReserveReleaseError,
    release_buy_reserve_if_safe,
)
from app.settlement.statuses import (
    BATCH_STATUS_AWAITING_POSITIVE_NET_EXECUTION,
    BATCH_STATUS_CREATED,
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_GAS_CHECKING,
    ORDER_SIDE_BUY,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_BUY_COLLECTED,
    ORDER_STATUS_FAILED_REQUIRES_REVIEW,
    ORDER_STATUS_PENDING,
    TRANSFER_STATUS_PENDING,
    TRANSFER_STATUS_PREPARED,
    TRANSFER_STATUS_PROCESSING,
    TRANSFER_STATUS_SENT,
    TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT,
)

from app.settlement.bybit_deposit_service import (
    _parse_internal_transfer_status,
    classify_bybit_deposit_status,
    query_fund_to_unified_internal_transfer,
)


class FakeBybitClient:
    def __init__(
        self,
        *,
        response: dict[str, Any],
    ) -> None:
        self.response = response
        self.calls: list[
            tuple[str, dict[str, Any]]
        ] = []

    def get(
        self,
        path: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self.calls.append((path, params))
        return self.response


def test_bybit_pending_deposit_is_not_final_success() -> None:
    pending_statuses = [
        None,
        "0",
        "1",
        "2",
        "7",
        "10011",
        "UNKNOWN_STATUS",
    ]

    for status in pending_statuses:
        assert (
            classify_bybit_deposit_status(
                status=status,
                deposit_type=None,
            )
            == "pending"
        )


def test_bybit_status_3_is_final_success() -> None:
    assert (
        classify_bybit_deposit_status(
            status="3",
            deposit_type=None,
        )
        == "success"
    )


def test_bybit_failed_or_rollback_is_not_success() -> None:
    failed_statuses = [
        "4",
        "70011",
        "FAILED",
        "ROLLBACK",
        "REJECTED",
    ]

    for status in failed_statuses:
        assert (
            classify_bybit_deposit_status(
                status=status,
                deposit_type=None,
            )
            == "failed"
        )


def test_deposit_type_50_blocks_confirmation() -> None:
    assert (
        classify_bybit_deposit_status(
            status="3",
            deposit_type="50",
        )
        == "failed"
    )


def test_internal_transfer_pending_is_queried_by_transfer_id() -> None:
    transfer_id = "wb-settlement-170-9"

    client = FakeBybitClient(
        response={
            "result": {
                "list": [
                    {
                        "transferId": transfer_id,
                        "status": "PENDING",
                    }
                ]
            }
        }
    )

    result = query_fund_to_unified_internal_transfer(
        client,
        transfer_id=transfer_id,
    )

    assert result is not None
    assert result.transfer_id == transfer_id
    assert result.status == "PENDING"
    assert result.completed is False

    assert client.calls == [
        (
            "/v5/asset/transfer/query-inter-transfer-list",
            {
                "transferId": transfer_id,
                "coin": "USDT",
                "limit": 50,
            },
        )
    ]


def test_missing_internal_transfer_status_is_not_success() -> None:
    assert (
        _parse_internal_transfer_status(
            {
                "result": {
                    "transferId": "wb-settlement-test",
                }
            }
        )
        == "STATUS_UNKNOWN"
    )


class BatchCreationSession:
    def __init__(self, order: Any) -> None:
        self.order = order
        self.flush_count = 0
        self.visible_batch_id: int | None = None
        self.validator_saw_attached_order = False

    def add(self, value: Any) -> None:
        return None

    def flush(self) -> None:
        self.flush_count += 1

        if self.order.settlement_batch_id is not None:
            self.visible_batch_id = int(
                self.order.settlement_batch_id
            )


def run_valid_buy_batch(
    monkeypatch: Any,
    *,
    settlement_price_usdt: Decimal,
) -> tuple[Any, Any, BatchCreationSession]:
    order = SimpleNamespace(
        id=1340,
        user_id=71,
        fund_id=9,
        side=ORDER_SIDE_BUY,
        amount_usdt=Decimal("10"),
        shares=None,
        price_usdt=None,
        settlement_batch_id=None,
        status=ORDER_STATUS_PENDING,
        created_at=datetime(
            2026,
            7,
            16,
            12,
            0,
            tzinfo=timezone.utc,
        ),
        settlement_locked_at=None,
        error=None,
    )

    fund = SimpleNamespace(
        id=9,
        code="wb_test",
        shares_outstanding_current=Decimal("100.0000"),
    )

    batch = SimpleNamespace(
        id=1700,
        fund_id=9,
        settlement_date=date(2026, 7, 16),
        cutoff_ts=None,
        settlement_ts=None,
        settlement_price_usdt=None,
        status=BATCH_STATUS_CREATED,
        total_buy_usdt=Decimal("0"),
        total_redeem_shares=Decimal("0"),
        total_redeem_usdt=Decimal("0"),
        net_cash_usdt=Decimal("0"),
        planned_shares_to_issue=Decimal("0"),
        planned_shares_to_redeem=Decimal("0"),
        planned_net_shares_change=Decimal("0"),
        pricing_locked_at=None,
        updated_at=None,
        error=None,
    )

    db = BatchCreationSession(order)

    monkeypatch.setattr(
        batch_service,
        "_lock_pending_orders_for_batch",
        lambda *args, **kwargs: [order],
    )
    monkeypatch.setattr(
        batch_service,
        "get_or_create_settlement_batch",
        lambda *args, **kwargs: batch,
    )
    monkeypatch.setattr(
        batch_service,
        "_validate_pre_lock_share_state",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        batch_service,
        "lock_pricing_for_fund",
        lambda *args, **kwargs: None,
    )

    def fake_price_snapshot(
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        batch.settlement_price_usdt = (
            settlement_price_usdt
        )

        return SimpleNamespace(
            settlement_price_usdt=(
                settlement_price_usdt
            ),
        )

    monkeypatch.setattr(
        batch_service,
        "fix_settlement_price_for_batch",
        fake_price_snapshot,
    )

    def fake_validator(
        session: Any,
        *,
        batch: Any,
        mark_failed: bool,
    ) -> None:
        assert mark_failed is True
        assert session.visible_batch_id == batch.id
        session.validator_saw_attached_order = True

    monkeypatch.setattr(
        batch_service,
        "validate_settlement_share_state_before_external",
        fake_validator,
    )

    result = (
        batch_service.create_settlement_batch_for_fund(
            db,
            fund=fund,
            settlement_date=date(2026, 7, 16),
        )
    )

    return result, order, db


def test_autoflush_false_order_is_flushed_before_validator(
    monkeypatch: Any,
) -> None:
    result, order, db = run_valid_buy_batch(
        monkeypatch,
        settlement_price_usdt=Decimal("3"),
    )

    assert db.validator_saw_attached_order is True
    assert db.visible_batch_id == result.batch_id
    assert order.settlement_batch_id == result.batch_id


def test_valid_ten_usdt_buy_uses_4dp_and_gas_checking(
    monkeypatch: Any,
) -> None:
    result, order, _db = run_valid_buy_batch(
        monkeypatch,
        settlement_price_usdt=Decimal("3"),
    )

    assert result.status == BATCH_STATUS_GAS_CHECKING
    assert result.total_buy_usdt == Decimal("10")
    assert order.shares == Decimal("3.3333")
    assert order.shares.as_tuple().exponent == -4


class CollectionQuery:
    def __init__(
        self,
        *,
        model: type[Any],
        batch: Any,
        fund: Any,
    ) -> None:
        self.model = model
        self.batch = batch
        self.fund = fund

    def filter(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "CollectionQuery":
        return self

    def with_for_update(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "CollectionQuery":
        return self

    def first(self) -> Any:
        if self.model is FundSettlementBatch:
            return self.batch

        if self.model is Fund:
            return self.fund

        return None


class CollectionSession:
    def __init__(
        self,
        *,
        batch: Any,
        fund: Any,
    ) -> None:
        self.batch = batch
        self.fund = fund
        self.durable_transfers: list[Any] = []
        self.flush_count = 0

    def query(self, model: type[Any]) -> CollectionQuery:
        return CollectionQuery(
            model=model,
            batch=self.batch,
            fund=self.fund,
        )

    def add(self, value: Any) -> None:
        return None

    def flush(self) -> None:
        self.flush_count += 1


def test_second_order_failure_keeps_first_durable_transfer(
    monkeypatch: Any,
) -> None:
    batch = SimpleNamespace(
        id=801,
        fund_id=9,
        status=BATCH_STATUS_GAS_CHECKING,
        net_cash_usdt=Decimal("20"),
        updated_at=None,
        error=None,
    )
    fund = SimpleNamespace(
        id=9,
        code="wb_test",
    )

    first_order = SimpleNamespace(
        id=1001,
        user_id=71,
        status=ORDER_STATUS_PENDING,
        error=None,
    )
    second_order = SimpleNamespace(
        id=1002,
        user_id=72,
        status=ORDER_STATUS_PENDING,
        error=None,
    )

    db = CollectionSession(
        batch=batch,
        fund=fund,
    )

    monkeypatch.setattr(
        transfer_service,
        "_get_active_fund_settlement_wallet",
        lambda *args, **kwargs: SimpleNamespace(
            address="0xsettlement",
        ),
    )
    monkeypatch.setattr(
        transfer_service,
        "_get_buy_orders_for_batch",
        lambda *args, **kwargs: [
            first_order,
            second_order,
        ],
    )
    monkeypatch.setattr(
        transfer_service,
        "get_web3",
        lambda: object(),
    )
    monkeypatch.setattr(
        transfer_service,
        "_get_active_user_wallet_for_update",
        lambda *args, **kwargs: SimpleNamespace(
            address="0xuser",
        ),
    )
    monkeypatch.setattr(
        transfer_service,
        "_ensure_user_wallet_gas",
        lambda *args, **kwargs: True,
    )
    monkeypatch.setattr(
        transfer_service,
        "release_buy_reserve_if_safe",
        lambda *args, **kwargs: Decimal("10"),
    )

    def fake_collect(
        *args: Any,
        order: Any,
        **kwargs: Any,
    ) -> bool:
        if order.id == first_order.id:
            durable = SimpleNamespace(
                order_id=order.id,
                status="sent",
                tx_hash="0xfirst",
            )
            db.durable_transfers.append(durable)
            order.status = ORDER_STATUS_BUY_COLLECTED
            return True

        raise transfer_service.SettlementTransferError(
            "second order failed before broadcast"
        )

    monkeypatch.setattr(
        transfer_service,
        "_collect_buy_order_usdt",
        fake_collect,
    )

    result = transfer_service.collect_buy_usdt_for_batch(
        db,
        batch_id=batch.id,
    )

    assert result.collected_orders_count == 1
    assert result.failed_orders_count == 1
    assert len(db.durable_transfers) == 1
    assert db.durable_transfers[0].tx_hash == "0xfirst"
    assert first_order.status == ORDER_STATUS_BUY_COLLECTED
    assert second_order.status != ORDER_STATUS_BUY_COLLECTED


class LockQuery:
    def __init__(self, order: Any) -> None:
        self.order = order
        self.lock_args: tuple[Any, ...] | None = None
        self.lock_kwargs: dict[str, Any] | None = None

    def filter(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "LockQuery":
        return self

    def order_by(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "LockQuery":
        return self

    def with_for_update(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "LockQuery":
        self.lock_args = args
        self.lock_kwargs = kwargs
        return self

    def all(self) -> list[Any]:
        return [self.order]


class LockSession:
    def __init__(self, query: LockQuery) -> None:
        self.lock_query = query

    def query(self, model: type[Any]) -> LockQuery:
        assert model is FundOrder
        return self.lock_query


def test_locked_buy_order_is_not_treated_as_absent() -> None:
    order = SimpleNamespace(id=2001)
    query = LockQuery(order)
    db = LockSession(query)

    result = transfer_service._get_buy_orders_for_batch(
        db,
        batch_id=901,
    )

    assert result == [order]
    assert query.lock_args == ()
    assert query.lock_kwargs == {}
    assert "skip_locked" not in query.lock_kwargs


class NestedTransaction:
    def __enter__(self) -> "NestedTransaction":
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc: Any,
        traceback: Any,
    ) -> bool:
        return False


class BatchRepositoryQuery:
    def __init__(
        self,
        *,
        first_value: Any,
        one_value: Any,
    ) -> None:
        self.first_value = first_value
        self.one_value = one_value

    def filter(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "BatchRepositoryQuery":
        return self

    def with_for_update(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "BatchRepositoryQuery":
        return self

    def first(self) -> Any:
        return self.first_value

    def one(self) -> Any:
        return self.one_value


class BatchRepositorySession:
    def __init__(self, existing: Any) -> None:
        self.existing = existing
        self.query_count = 0
        self.add_count = 0
        self.flush_count = 0

    def query(
        self,
        model: type[Any],
    ) -> BatchRepositoryQuery:
        assert model is FundSettlementBatch
        self.query_count += 1

        if self.query_count == 1:
            return BatchRepositoryQuery(
                first_value=None,
                one_value=None,
            )

        return BatchRepositoryQuery(
            first_value=self.existing,
            one_value=self.existing,
        )

    def begin_nested(self) -> NestedTransaction:
        return NestedTransaction()

    def add(self, value: Any) -> None:
        self.add_count += 1

    def flush(self) -> None:
        self.flush_count += 1

        raise IntegrityError(
            "INSERT",
            {},
            RuntimeError("duplicate batch"),
        )


def test_concurrent_batch_creation_returns_single_row() -> None:
    existing = SimpleNamespace(
        id=3001,
        fund_id=9,
        settlement_date=date(2026, 7, 16),
    )
    db = BatchRepositorySession(existing)

    result = (
        batch_repository.get_or_create_settlement_batch(
            db,
            fund_id=9,
            settlement_date=date(2026, 7, 16),
            cutoff_ts=datetime(
                2026,
                7,
                16,
                23,
                59,
                tzinfo=timezone.utc,
            ),
            settlement_ts=datetime(
                2026,
                7,
                16,
                23,
                59,
                tzinfo=timezone.utc,
            ),
        )
    )

    assert result is existing
    assert db.query_count == 2
    assert db.add_count == 1
    assert db.flush_count == 1


class PositiveNetSession:
    def __init__(self) -> None:
        self.flush_count = 0

    def add(self, value: Any) -> None:
        return None

    def flush(self) -> None:
        self.flush_count += 1


def test_accounting_runs_only_after_all_final_confirmations(
    monkeypatch: Any,
) -> None:
    now = datetime.now(timezone.utc)

    batch = SimpleNamespace(
        id=4001,
        fund_id=9,
        status=BATCH_STATUS_AWAITING_POSITIVE_NET_EXECUTION,
        net_cash_usdt=Decimal("10"),
        settlement_price_usdt=Decimal("3"),
        accounting_finalized_at=None,
        seller_payouts_completed_at=now,
        bybit_deposit_confirmed_at=None,
        bybit_internal_transfer_completed_at=None,
        bybit_internal_transfer_status=None,
        bybit_internal_transfer_error=None,
        pricing_unlocked_at=None,
        positive_net_started_at=None,
        updated_at=None,
        error=None,
    )

    db = PositiveNetSession()
    state = {
        "deposit_confirmed": False,
        "internal_ready": False,
        "persist_internal_status": False,
        "accounting_calls": 0,
    }

    monkeypatch.setattr(
        positive_net_service,
        "_get_batch_for_update",
        lambda *args, **kwargs: batch,
    )
    monkeypatch.setattr(
        positive_net_service,
        "_validate_positive_net_batch",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        positive_net_service,
        "_validate_pricing_lock",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        positive_net_service,
        "_validate_buy_collection_completed",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        positive_net_service,
        "validate_positive_net_share_preflight",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        positive_net_service,
        "send_or_confirm_positive_net_transfer",
        lambda *args, **kwargs: SimpleNamespace(
            transfer_status="confirmed",
        ),
    )

    def fake_confirm_deposit(
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        if state["deposit_confirmed"]:
            batch.bybit_deposit_confirmed_at = now
            return True

        return False

    monkeypatch.setattr(
        positive_net_service,
        "confirm_bybit_deposit_for_batch",
        fake_confirm_deposit,
    )

    def fake_internal_transfer(
        *args: Any,
        **kwargs: Any,
    ) -> bool:
        if state["internal_ready"]:
            batch.bybit_internal_transfer_completed_at = now

            if state["persist_internal_status"]:
                batch.bybit_internal_transfer_status = (
                    "SUCCESS"
                )
                batch.bybit_internal_transfer_error = None

            return True

        return False

    monkeypatch.setattr(
        positive_net_service,
        "_internal_transfer_ready_or_skipped",
        fake_internal_transfer,
    )

    def fake_finalize(
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        state["accounting_calls"] += 1
        batch.accounting_finalized_at = now
        batch.pricing_unlocked_at = now
        return SimpleNamespace(batch_id=batch.id)

    monkeypatch.setattr(
        positive_net_service,
        "finalize_positive_net_accounting",
        fake_finalize,
    )

    pending_result = (
        positive_net_service.process_positive_net_batch(
            db,
            batch_id=batch.id,
            finalize_accounting=True,
        )
    )

    assert pending_result.accounting_finalized is False
    assert state["accounting_calls"] == 0

    state["deposit_confirmed"] = True
    state["internal_ready"] = True

    legacy_completed_result = (
        positive_net_service.process_positive_net_batch(
            db,
            batch_id=batch.id,
            finalize_accounting=True,
        )
    )

    assert (
        legacy_completed_result.accounting_finalized
        is False
    )
    assert state["accounting_calls"] == 0
    assert (
        batch.bybit_internal_transfer_completed_at
        is not None
    )
    assert (
        batch.bybit_internal_transfer_status
        is None
    )

    state["persist_internal_status"] = True

    final_result = (
        positive_net_service.process_positive_net_batch(
            db,
            batch_id=batch.id,
            finalize_accounting=True,
        )
    )

    assert state["accounting_calls"] == 1
    assert final_result.accounting_finalized is True
    assert batch.accounting_finalized_at is not None

class ReserveQuery:
    def __init__(
        self,
        *,
        model: type[Any],
        order: Any,
        transfers: list[Any],
        wallet: Any,
    ) -> None:
        self.model = model
        self.order = order
        self.transfers = transfers
        self.wallet = wallet

    def filter(self, *args: Any, **kwargs: Any) -> "ReserveQuery":
        return self

    def with_for_update(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "ReserveQuery":
        return self

    def first(self) -> Any:
        if self.model is FundOrder:
            return self.order

        if self.model is UserWallet:
            return self.wallet

        return None

    def all(self) -> list[Any]:
        if self.model is FundSettlementTransfer:
            return list(self.transfers)

        return []


class ReserveSession:
    def __init__(
        self,
        *,
        order: Any,
        transfers: list[Any],
        wallet: Any,
    ) -> None:
        self.order = order
        self.transfers = transfers
        self.wallet = wallet
        self.added: list[Any] = []
        self.flush_count = 0

    def query(self, model: type[Any]) -> ReserveQuery:
        return ReserveQuery(
            model=model,
            order=self.order,
            transfers=self.transfers,
            wallet=self.wallet,
        )

    def add(self, value: Any) -> None:
        self.added.append(value)

    def flush(self) -> None:
        self.flush_count += 1


def make_order(
    *,
    amount_usdt: Decimal = Decimal("10"),
) -> Any:
    return SimpleNamespace(
        id=101,
        user_id=7,
        side=ORDER_SIDE_BUY,
        amount_usdt=amount_usdt,
        collection_confirmed_at=None,
        buy_reserve_released_usdt=Decimal("0"),
        buy_reserve_released_at=None,
        error=None,
    )


def make_wallet(
    *,
    reserved: Decimal = Decimal("10"),
) -> Any:
    return SimpleNamespace(
        id=55,
        user_id=7,
        blockchain="BSC",
        is_active=True,
        usdt_reserved=reserved,
        usdt_balance=Decimal("25"),
    )


def make_reserve_transfer(
    *,
    status: str,
    tx_hash: str | None = None,
    prepared_tx_hash: str | None = None,
    prepared_raw_tx: str | None = None,
) -> Any:
    return SimpleNamespace(
        id=900,
        order_id=101,
        transfer_type=(
            TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT
        ),
        status=status,
        tx_hash=tx_hash,
        prepared_tx_hash=prepared_tx_hash,
        prepared_raw_tx=prepared_raw_tx,
        broadcast_at=None,
        confirmed_at=None,
    )


def test_pre_external_failure_releases_exact_buy_reserve() -> None:
    order = make_order()
    wallet = make_wallet()

    db = ReserveSession(
        order=order,
        transfers=[],
        wallet=wallet,
    )

    released = release_buy_reserve_if_safe(
        db,
        order_id=order.id,
        reason="pre_external_validation_failed",
    )

    assert released == Decimal("10")
    assert wallet.usdt_reserved == Decimal("0")
    assert wallet.usdt_balance == Decimal("25")
    assert order.buy_reserve_released_usdt == Decimal("10")
    assert order.buy_reserve_released_at is not None
    assert "released_reserved_usdt=10" in order.error
    assert db.flush_count == 1


def test_repeated_release_is_idempotent() -> None:
    order = make_order()
    wallet = make_wallet()

    db = ReserveSession(
        order=order,
        transfers=[],
        wallet=wallet,
    )

    first = release_buy_reserve_if_safe(
        db,
        order_id=order.id,
        reason="first_failure",
    )

    second = release_buy_reserve_if_safe(
        db,
        order_id=order.id,
        reason="repeated_failure",
    )

    assert first == Decimal("10")
    assert second == Decimal("0")
    assert wallet.usdt_reserved == Decimal("0")
    assert order.buy_reserve_released_usdt == Decimal("10")
    assert order.error.count("released_reserved_usdt=10") == 1


@pytest.mark.parametrize(
    ("status", "tx_hash", "prepared_tx_hash"),
    [
        (
            TRANSFER_STATUS_SENT,
            "0xsent",
            None,
        ),
        (
            TRANSFER_STATUS_PROCESSING,
            None,
            "0xprepared",
        ),
    ],
)
def test_sent_or_ambiguous_usdt_transfer_blocks_release(
    status: str,
    tx_hash: str | None,
    prepared_tx_hash: str | None,
) -> None:
    order = make_order()
    wallet = make_wallet()

    transfer = make_reserve_transfer(
        status=status,
        tx_hash=tx_hash,
        prepared_tx_hash=prepared_tx_hash,
        prepared_raw_tx=(
            "0xraw"
            if prepared_tx_hash is not None
            else None
        ),
    )

    db = ReserveSession(
        order=order,
        transfers=[transfer],
        wallet=wallet,
    )

    with pytest.raises(BuyReserveReleaseError):
        release_buy_reserve_if_safe(
            db,
            order_id=order.id,
            reason="must_not_release",
        )

    assert wallet.usdt_reserved == Decimal("10")
    assert wallet.usdt_balance == Decimal("25")
    assert order.buy_reserve_released_usdt == Decimal("0")
    assert order.buy_reserve_released_at is None


class BatchFailureSession(ReserveSession):
    def __init__(
        self,
        *,
        buy_order: Any,
        redeem_order: Any,
        wallet: Any,
    ) -> None:
        super().__init__(
            order=buy_order,
            transfers=[],
            wallet=wallet,
        )
        self.orders = [
            buy_order,
            redeem_order,
        ]
        self.attached_orders_flushed = False

    def flush(self) -> None:
        super().flush()

        if all(
            order.settlement_batch_id is not None
            for order in self.orders
        ):
            self.attached_orders_flushed = True


def test_batch_validation_failure_delegates_to_safe_recovery(
    monkeypatch: Any,
) -> None:
    created_at = datetime(
        2026,
        7,
        16,
        12,
        0,
        tzinfo=timezone.utc,
    )

    buy_order = SimpleNamespace(
        id=5101,
        user_id=71,
        fund_id=9,
        side=ORDER_SIDE_BUY,
        amount_usdt=Decimal("10"),
        shares=None,
        price_usdt=None,
        settlement_batch_id=None,
        status=ORDER_STATUS_PENDING,
        created_at=created_at,
        settlement_locked_at=None,
        collection_confirmed_at=None,
        buy_reserve_released_usdt=Decimal("0"),
        buy_reserve_released_at=None,
        error=None,
    )

    redeem_order = SimpleNamespace(
        id=5102,
        user_id=72,
        fund_id=9,
        side=ORDER_SIDE_REDEEM,
        amount_usdt=Decimal("0"),
        shares=Decimal("1.0000"),
        price_usdt=None,
        settlement_batch_id=None,
        status=ORDER_STATUS_PENDING,
        created_at=created_at,
        settlement_locked_at=None,
        error=None,
    )

    wallet = SimpleNamespace(
        id=6101,
        user_id=buy_order.user_id,
        blockchain="BSC",
        is_active=True,
        usdt_reserved=Decimal("10"),
        usdt_balance=Decimal("25"),
    )

    fund = SimpleNamespace(
        id=9,
        code="wb_test",
        shares_outstanding_current=Decimal("100.0000"),
    )

    batch = SimpleNamespace(
        id=7101,
        fund_id=fund.id,
        settlement_date=date(2026, 7, 16),
        cutoff_ts=None,
        settlement_ts=None,
        settlement_price_usdt=None,
        status=BATCH_STATUS_CREATED,
        total_buy_usdt=Decimal("0"),
        total_redeem_shares=Decimal("0"),
        total_redeem_usdt=Decimal("0"),
        net_cash_usdt=Decimal("0"),
        planned_shares_to_issue=Decimal("0"),
        planned_shares_to_redeem=Decimal("0"),
        planned_net_shares_change=Decimal("0"),
        pricing_locked_at=None,
        pricing_unlocked_at=None,
        updated_at=None,
        error=None,
    )

    db = BatchFailureSession(
        buy_order=buy_order,
        redeem_order=redeem_order,
        wallet=wallet,
    )

    monkeypatch.setattr(
        batch_service,
        "_lock_pending_orders_for_batch",
        lambda *args, **kwargs: [
            buy_order,
            redeem_order,
        ],
    )
    monkeypatch.setattr(
        batch_service,
        "get_or_create_settlement_batch",
        lambda *args, **kwargs: batch,
    )
    monkeypatch.setattr(
        batch_service,
        "_validate_pre_lock_share_state",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        batch_service,
        "lock_pricing_for_fund",
        lambda *args, **kwargs: None,
    )

    def fake_price_snapshot(
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        batch.settlement_price_usdt = Decimal("2")

        return SimpleNamespace(
            settlement_price_usdt=Decimal("2"),
        )

    monkeypatch.setattr(
        batch_service,
        "fix_settlement_price_for_batch",
        fake_price_snapshot,
    )

    validator_calls: list[int] = []

    def failing_validator(
        session: Any,
        *,
        batch: Any,
        mark_failed: bool,
    ) -> None:
        assert mark_failed is True
        assert session.attached_orders_flushed is True

        validator_calls.append(int(batch.id))

        raise batch_service.SettlementShareQuantityError(
            "forced_post_flush_share_validation_failure"
        )

    monkeypatch.setattr(
        batch_service,
        "validate_settlement_share_state_before_external",
        failing_validator,
    )

    recovery_calls: list[
        tuple[int, str, str]
    ] = []

    def fake_safe_recovery(
        session: Any,
        *,
        settlement_batch_id: int,
        error: str,
        source: str,
    ) -> Any:
        assert session is db

        recovery_calls.append(
            (
                int(settlement_batch_id),
                str(error),
                str(source),
            )
        )

        batch.status = (
            BATCH_STATUS_FAILED_REQUIRES_REVIEW
        )
        batch.error = str(error)

        for order in (
            buy_order,
            redeem_order,
        ):
            order.status = (
                ORDER_STATUS_FAILED_REQUIRES_REVIEW
            )
            order.error = str(error)

        return SimpleNamespace(
            settlement_batch_id=int(
                settlement_batch_id
            ),
            status=batch.status,
        )

    monkeypatch.setattr(
        batch_service,
        "fail_negative_batch_pre_external",
        fake_safe_recovery,
    )

    first_result = (
        batch_service.create_settlement_batch_for_fund(
            db,
            fund=fund,
            settlement_date=date(2026, 7, 16),
        )
    )

    assert db.attached_orders_flushed is True
    assert validator_calls == [batch.id]

    assert recovery_calls == [
        (
            batch.id,
            "forced_post_flush_share_validation_failure",
            "settlement_batch_share_validation",
        )
    ]

    assert (
        first_result.status
        == BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert (
        batch.status
        == BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert (
        buy_order.status
        == ORDER_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert (
        redeem_order.status
        == ORDER_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert wallet.usdt_reserved == Decimal("10")
    assert wallet.usdt_balance == Decimal("25")
    assert (
        buy_order.buy_reserve_released_usdt
        == Decimal("0")
    )

    assert (
        "forced_post_flush_share_validation_failure"
        in buy_order.error
    )
    assert (
        "forced_post_flush_share_validation_failure"
        in redeem_order.error
    )

    second_result = (
        batch_service.create_settlement_batch_for_fund(
            db,
            fund=fund,
            settlement_date=date(2026, 7, 16),
        )
    )

    assert (
        second_result.status
        == BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert len(recovery_calls) == 1
    assert validator_calls == [batch.id]
    assert wallet.usdt_reserved == Decimal("10")
    assert wallet.usdt_balance == Decimal("25")

class IntentQuery:
    def __init__(self, row: Any) -> None:
        self.row = row

    def filter(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "IntentQuery":
        return self

    def with_for_update(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "IntentQuery":
        return self

    def first(self) -> Any:
        return self.row


class IntentSession:
    def __init__(self, row: Any) -> None:
        self.row = row
        self.commit_count = 0
        self.refresh_count = 0
        self.added: list[Any] = []

    def query(self, model: type[Any]) -> IntentQuery:
        return IntentQuery(self.row)

    def add(self, value: Any) -> None:
        self.added.append(value)

    def commit(self) -> None:
        self.commit_count += 1

    def refresh(self, value: Any) -> None:
        assert value is self.row
        self.refresh_count += 1


class IntentEth:
    def __init__(
        self,
        *,
        tx_hash: str,
        source_nonce: int,
    ) -> None:
        self.chain_id = 56
        self.tx_hash = tx_hash
        self.source_nonce = source_nonce
        self.visible = False
        self.send_count = 0

    def get_transaction(self, tx_hash: str) -> dict[str, str]:
        if not self.visible:
            raise TransactionNotFound(tx_hash)

        return {"hash": tx_hash}

    def get_transaction_count(
        self,
        address: str,
        block_identifier: str,
    ) -> int:
        assert block_identifier == "pending"
        return self.source_nonce

    def send_raw_transaction(self, raw_tx: bytes) -> bytes:
        assert raw_tx == bytes.fromhex("0102")

        self.send_count += 1
        self.visible = True

        return bytes.fromhex(
            self.tx_hash.removeprefix("0x")
        )


class IntentWeb3:
    def __init__(self, eth: IntentEth) -> None:
        self.eth = eth

    def to_checksum_address(self, address: str) -> str:
        return address

    def to_hex(self, value: bytes | str) -> str:
        if isinstance(value, bytes):
            return f"0x{value.hex()}"

        return str(value)


def make_intent_transfer() -> Any:
    return SimpleNamespace(
        id=701,
        request_key=None,
        chain_id=None,
        source_nonce=None,
        prepared_tx_hash=None,
        prepared_raw_tx=None,
        prepared_at=None,
        broadcast_at=None,
        tx_hash=None,
        gas_tx_hash=None,
        sent_at=None,
        confirmed_at=None,
        status=TRANSFER_STATUS_PENDING,
        error=None,
        updated_at=None,
    )


def test_prepared_intent_and_broadcast_hash_use_separate_commits() -> None:
    transfer = make_intent_transfer()
    db = IntentSession(transfer)

    prepared = PreparedBscTransaction(
        chain_id=56,
        source_nonce=19,
        tx_hash=f"0x{'ab' * 32}",
        raw_tx_hex="0x0102",
    )

    prepared_row = persist_prepared_transfer_intent(
        db,
        transfer_id=transfer.id,
        request_key="buy-usdt:batch-8:order-12",
        prepared=prepared,
    )

    assert prepared_row is transfer
    assert transfer.status == TRANSFER_STATUS_PREPARED
    assert transfer.request_key == (
        "buy-usdt:batch-8:order-12"
    )
    assert transfer.chain_id == 56
    assert transfer.source_nonce == 19
    assert transfer.prepared_tx_hash == prepared.tx_hash
    assert transfer.prepared_raw_tx == "0x0102"
    assert transfer.tx_hash is None
    assert db.commit_count == 1

    sent_row = persist_broadcast_transfer_result(
        db,
        transfer_id=transfer.id,
        tx_hash=prepared.tx_hash,
    )

    assert sent_row is transfer
    assert transfer.status == TRANSFER_STATUS_SENT
    assert transfer.tx_hash == prepared.tx_hash
    assert transfer.prepared_tx_hash == prepared.tx_hash
    assert transfer.prepared_raw_tx == "0x0102"
    assert transfer.broadcast_at is not None
    assert transfer.sent_at is not None
    assert db.commit_count == 2


def test_restart_reconciles_hash_without_second_broadcast() -> None:
    tx_hash = f"0x{'cd' * 32}"

    eth = IntentEth(
        tx_hash=tx_hash,
        source_nonce=27,
    )
    w3 = IntentWeb3(eth)

    first = broadcast_prepared_transaction(
        w3,
        prepared_tx_hash=tx_hash,
        raw_tx_hex="0x0102",
        from_address="0xsource",
        chain_id=56,
        source_nonce=27,
    )

    assert first.action == "broadcast"
    assert first.tx_hash == tx_hash
    assert eth.send_count == 1

    restarted = broadcast_prepared_transaction(
        w3,
        prepared_tx_hash=tx_hash,
        raw_tx_hex="0x0102",
        from_address="0xsource",
        chain_id=56,
        source_nonce=27,
    )

    assert restarted.action == "already_visible"
    assert restarted.tx_hash == tx_hash
    assert eth.send_count == 1
