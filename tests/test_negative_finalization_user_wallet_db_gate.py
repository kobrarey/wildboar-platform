from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import (
    FundNegativeFinalizationBatch,
    UserWallet,
)
import app.settlement.negative_finalization as service
from app.settlement.statuses import (
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED,
    FINALIZATION_BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    ORDER_STATUS_PROCESSING,
    PAYOUT_BALANCE_REFRESH_STATUS_CONFIRMED,
)


NOW = datetime(
    2026,
    7,
    27,
    12,
    0,
    tzinfo=timezone.utc,
)

PAYOUT_CONFIRMED_AT = (
    NOW - timedelta(minutes=2)
)

REFRESH_BLOCK = 500

SETTLEMENT_ADDRESS = (
    "0x1111111111111111111111111111111111111111"
)

USER_ADDRESS = (
    "0x2222222222222222222222222222222222222222"
)


def make_leg(
    **overrides: Any,
) -> Any:
    values = {
        "id": 51,
        "user_id": 101,
        "user_wallet_id": 501,
        "to_user_wallet_id": 501,
        "to_address": USER_ADDRESS,
        "amount_usdt": Decimal("10"),
        "confirmed_at": PAYOUT_CONFIRMED_AT,
        "wallet_balance_before_usdt": Decimal(
            "0"
        ),
        "wallet_balance_after_usdt": Decimal(
            "10"
        ),
        "balance_refresh_json": {
            "live": True,
            "absolute_onchain_sync": True,
            "block_number": REFRESH_BLOCK,
            "user_wallet_id": 501,
            "address": USER_ADDRESS,
            "before_usdt": Decimal("0"),
            "payout_amount_usdt": Decimal(
                "10"
            ),
            "observed_after_usdt": Decimal(
                "10"
            ),
            "arithmetic_credit_applied": False,
        },
    }

    values.update(overrides)

    return SimpleNamespace(**values)


def make_batch(
    **overrides: Any,
) -> Any:
    values = {
        "balance_refresh_status": (
            PAYOUT_BALANCE_REFRESH_STATUS_CONFIRMED
        ),
        "balance_refresh_completed_at": NOW,
        "expected_total_payout_usdt": Decimal(
            "10"
        ),
        "confirmed_total_payout_usdt": Decimal(
            "10"
        ),
        "settlement_wallet_address": (
            SETTLEMENT_ADDRESS
        ),
        "settlement_wallet_usdt_before": Decimal(
            "20"
        ),
        "settlement_wallet_usdt_after": Decimal(
            "10"
        ),
        "balance_refresh_json": {
            "live": True,
            "absolute_onchain_sync": True,
            "block_number": REFRESH_BLOCK,
            "settlement_wallet": {
                "address": SETTLEMENT_ADDRESS,
                "before_usdt": Decimal("20"),
                "confirmed_total_payout_usdt": (
                    Decimal("10")
                ),
                "observed_after_usdt": Decimal(
                    "10"
                ),
                "arithmetic_debit_applied": False,
            },
            "user_wallets": [
                {
                    "user_wallet_id": 501,
                    "address": USER_ADDRESS,
                    "before_usdt": Decimal("0"),
                    "payout_amount_usdt": Decimal(
                        "10"
                    ),
                    "observed_after_usdt": Decimal(
                        "10"
                    ),
                    "block_number": REFRESH_BLOCK,
                    "absolute_onchain_sync": True,
                }
            ],
        },
    }

    values.update(overrides)

    return SimpleNamespace(**values)


def make_wallet(
    **overrides: Any,
) -> Any:
    values = {
        "id": 501,
        "user_id": 101,
        "blockchain": "BSC",
        "address": USER_ADDRESS,
        "usdt_balance": Decimal("10"),
        "usdt_balance_updated_at": NOW,
        "usdt_balance_block": REFRESH_BLOCK,
        "is_active": False,
    }

    values.update(overrides)

    return SimpleNamespace(**values)


def balance_refresh_validation(
    *,
    batch: Any | None = None,
    legs: list[Any] | None = None,
) -> dict[str, Any]:
    resolved_legs = (
        legs
        if legs is not None
        else [make_leg()]
    )

    return (
        service
        ._validate_balance_refresh_evidence(
            payout_batch=(
                batch
                if batch is not None
                else make_batch()
            ),
            payout_legs=resolved_legs,
        )
    )


def validate_wallet_gate(
    *,
    batch: Any | None = None,
    legs: list[Any] | None = None,
    wallets: dict[int, Any] | None = None,
) -> dict[str, Any]:
    resolved_batch = (
        batch
        if batch is not None
        else make_batch()
    )

    resolved_legs = (
        legs
        if legs is not None
        else [make_leg()]
    )

    resolved_wallets = (
        wallets
        if wallets is not None
        else {
            501: make_wallet(),
        }
    )

    refresh_validation = (
        balance_refresh_validation(
            batch=resolved_batch,
            legs=resolved_legs,
        )
    )

    return (
        service
        ._validate_payout_user_wallet_db_gate(
            payout_batch=resolved_batch,
            payout_legs=resolved_legs,
            locked_wallets=resolved_wallets,
            balance_refresh_validation=(
                refresh_validation
            ),
        )
    )


class FakeQuery:
    def __init__(
        self,
        *,
        rows: list[Any],
    ):
        self.rows = rows
        self.filters: list[str] = []
        self.order_by_called = False
        self.locked = False
        self.all_calls = 0

    def filter(
        self,
        *criteria: Any,
    ):
        self.filters.extend(
            str(criterion)
            for criterion in criteria
        )
        return self

    def order_by(
        self,
        *criteria: Any,
    ):
        self.order_by_called = True
        return self

    def with_for_update(
        self,
        *args: Any,
        **kwargs: Any,
    ):
        self.locked = True
        return self

    def all(self) -> list[Any]:
        self.all_calls += 1
        return list(self.rows)


class FakeSession:
    def __init__(
        self,
        *,
        wallet_rows: list[Any],
    ):
        self.wallet_rows = wallet_rows
        self.query_calls = 0
        self.last_query: FakeQuery | None = None

    def query(
        self,
        model: Any,
    ) -> FakeQuery:
        assert model is UserWallet

        self.query_calls += 1

        self.last_query = FakeQuery(
            rows=self.wallet_rows,
        )

        return self.last_query


class FinalizationSession:
    def __init__(self):
        self.finalization = None
        self.added: list[Any] = []
        self.flush_calls = 0

    def add(
        self,
        value: Any,
    ) -> None:
        self.added.append(value)

        if isinstance(
            value,
            FundNegativeFinalizationBatch,
        ):
            self.finalization = value

    def flush(self) -> None:
        self.flush_calls += 1

        if (
            self.finalization is not None
            and self.finalization.id is None
        ):
            self.finalization.id = 701


def test_happy_path_exact_wallet_db_state_is_accepted() -> None:
    result = validate_wallet_gate()

    assert result["schema"] == (
        service.USER_WALLET_DB_GATE_SCHEMA
    )

    assert result[
        "all_wallets_locked"
    ] is True

    assert result[
        "all_wallets_exact_match"
    ] is True

    assert result[
        "arithmetic_balance_updates"
    ] is False

    assert len(result["wallets"]) == 1

    row = result["wallets"][0]

    assert row["payout_leg_id"] == 51
    assert row["user_id"] == 101
    assert row["user_wallet_id"] == 501
    assert row["address"] == (
        USER_ADDRESS.lower()
    )

    assert row["db_usdt_balance"] == Decimal(
        "10.0000000000"
    )

    assert row[
        "expected_observed_after_usdt"
    ] == Decimal("10.0000000000")

    assert row[
        "db_usdt_balance_block"
    ] == REFRESH_BLOCK

    assert row[
        "expected_refresh_block"
    ] == REFRESH_BLOCK

    assert row[
        "exact_balance_match"
    ] is True

    assert row[
        "exact_block_match"
    ] is True


def test_exact_archived_wallet_is_locked_without_active_substitution() -> None:
    leg = make_leg(
        user_wallet_id=999,
        to_user_wallet_id=501,
    )

    archived_wallet = make_wallet(
        is_active=False,
    )

    db = FakeSession(
        wallet_rows=[
            archived_wallet,
        ]
    )

    result = (
        service
        ._lock_payout_user_wallets(
            db,
            payout_legs=[leg],
        )
    )

    assert result == {
        501: archived_wallet,
    }

    assert db.query_calls == 1
    assert db.last_query is not None
    assert db.last_query.locked is True
    assert (
        db.last_query.order_by_called
        is True
    )
    assert db.last_query.all_calls == 1

    joined_filters = " ".join(
        db.last_query.filters
    ).lower()

    assert "user_wallets.id" in (
        joined_filters
    )

    assert "is_active" not in (
        joined_filters
    )


def test_user_wallet_id_is_fallback_when_to_wallet_id_is_null() -> None:
    leg = make_leg(
        user_wallet_id=501,
        to_user_wallet_id=None,
    )

    assert (
        service
        ._authoritative_payout_wallet_id(
            leg
        )
        == 501
    )


@pytest.mark.parametrize(
    (
        "user_wallet_id",
        "to_user_wallet_id",
    ),
    [
        (None, None),
        (0, None),
        (-1, None),
    ],
)
def test_authoritative_wallet_id_is_required(
    user_wallet_id: Any,
    to_user_wallet_id: Any,
) -> None:
    leg = make_leg(
        user_wallet_id=user_wallet_id,
        to_user_wallet_id=(
            to_user_wallet_id
        ),
    )

    with pytest.raises(
        service.NegativeFinalizationError,
        match="payout_user_wallet_missing",
    ):
        (
            service
            ._authoritative_payout_wallet_id(
                leg
            )
        )


def test_missing_exact_wallet_row_is_rejected() -> None:
    db = FakeSession(
        wallet_rows=[]
    )

    with pytest.raises(
        service.NegativeFinalizationError,
        match="payout_user_wallet_missing",
    ):
        (
            service
            ._lock_payout_user_wallets(
                db,
                payout_legs=[
                    make_leg()
                ],
            )
        )

    assert db.query_calls == 1
    assert db.last_query is not None
    assert db.last_query.locked is True


def test_duplicate_wallet_mapping_is_rejected() -> None:
    first_leg = make_leg(
        id=51,
    )

    second_leg = make_leg(
        id=52,
    )

    db = FakeSession(
        wallet_rows=[
            make_wallet()
        ]
    )

    with pytest.raises(
        service.NegativeFinalizationError,
        match=(
            "payout_user_wallet_"
            "duplicate_mapping"
        ),
    ):
        (
            service
            ._lock_payout_user_wallets(
                db,
                payout_legs=[
                    first_leg,
                    second_leg,
                ],
            )
        )

    assert db.query_calls == 0


def test_wrong_wallet_user_is_rejected() -> None:
    with pytest.raises(
        service.NegativeFinalizationError,
        match=(
            "payout_user_wallet_"
            "user_mismatch"
        ),
    ):
        validate_wallet_gate(
            wallets={
                501: make_wallet(
                    user_id=999
                ),
            }
        )


def test_wrong_wallet_address_is_rejected() -> None:
    with pytest.raises(
        service.NegativeFinalizationError,
        match=(
            "payout_user_wallet_"
            "address_mismatch"
        ),
    ):
        validate_wallet_gate(
            wallets={
                501: make_wallet(
                    address=(
                        "0x3333333333333333333333333333333333333333"
                    )
                ),
            }
        )


@pytest.mark.parametrize(
    "blockchain",
    [
        "ETH",
        "",
        None,
    ],
)
def test_wrong_wallet_blockchain_is_rejected(
    blockchain: Any,
) -> None:
    with pytest.raises(
        service.NegativeFinalizationError,
        match=(
            "payout_user_wallet_"
            "blockchain_mismatch"
        ),
    ):
        validate_wallet_gate(
            wallets={
                501: make_wallet(
                    blockchain=blockchain
                ),
            }
        )


def test_missing_wallet_balance_is_rejected() -> None:
    with pytest.raises(
        service.NegativeFinalizationError,
        match=(
            "payout_user_wallet_"
            "balance_missing"
        ),
    ):
        validate_wallet_gate(
            wallets={
                501: make_wallet(
                    usdt_balance=None
                ),
            }
        )


def test_wallet_balance_mismatch_is_rejected() -> None:
    with pytest.raises(
        service.NegativeFinalizationError,
        match=(
            "payout_user_wallet_"
            "balance_mismatch"
        ),
    ):
        validate_wallet_gate(
            wallets={
                501: make_wallet(
                    usdt_balance=Decimal(
                        "9.99"
                    )
                ),
            }
        )


def test_missing_wallet_balance_block_is_rejected() -> None:
    with pytest.raises(
        service.NegativeFinalizationError,
        match=(
            "payout_user_wallet_"
            "block_missing"
        ),
    ):
        validate_wallet_gate(
            wallets={
                501: make_wallet(
                    usdt_balance_block=None
                ),
            }
        )


@pytest.mark.parametrize(
    "db_block",
    [
        REFRESH_BLOCK - 1,
        REFRESH_BLOCK + 1,
    ],
)
def test_non_exact_wallet_balance_block_is_rejected(
    db_block: int,
) -> None:
    with pytest.raises(
        service.NegativeFinalizationError,
        match=(
            "payout_user_wallet_"
            "block_mismatch"
        ),
    ):
        validate_wallet_gate(
            wallets={
                501: make_wallet(
                    usdt_balance_block=(
                        db_block
                    )
                ),
            }
        )


def test_missing_wallet_updated_at_is_rejected() -> None:
    with pytest.raises(
        service.NegativeFinalizationError,
        match=(
            "payout_user_wallet_"
            "updated_at_missing"
        ),
    ):
        validate_wallet_gate(
            wallets={
                501: make_wallet(
                    usdt_balance_updated_at=None
                ),
            }
        )


def test_naive_wallet_updated_at_is_rejected() -> None:
    with pytest.raises(
        service.NegativeFinalizationError,
        match=(
            "payout_user_wallet_updated_at_"
            "not_timezone_aware"
        ),
    ):
        validate_wallet_gate(
            wallets={
                501: make_wallet(
                    usdt_balance_updated_at=(
                        NOW.replace(
                            tzinfo=None
                        )
                    )
                ),
            }
        )


def test_wallet_updated_before_payout_confirmation_is_rejected() -> None:
    stale_time = (
        PAYOUT_CONFIRMED_AT
        - timedelta(seconds=1)
    )

    with pytest.raises(
        service.NegativeFinalizationError,
        match=(
            "payout_user_wallet_"
            "updated_at_stale"
        ),
    ):
        validate_wallet_gate(
            wallets={
                501: make_wallet(
                    usdt_balance_updated_at=(
                        stale_time
                    )
                ),
            }
        )


def test_wallet_validation_does_not_mutate_db_balance_fields() -> None:
    wallet = make_wallet()

    before = deepcopy(
        vars(wallet)
    )

    result = validate_wallet_gate(
        wallets={
            501: wallet,
        }
    )

    assert result[
        "arithmetic_balance_updates"
    ] is False

    assert vars(wallet) == before

def test_wallet_balance_mismatch_blocks_full_finalization_before_accounting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settlement_batch = SimpleNamespace(
        id=11,
        fund_id=3,
        status=(
            BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED
        ),
        settlement_price_usdt=Decimal("10"),
        shares_outstanding_before=Decimal(
            "100"
        ),
        planned_shares_to_issue=Decimal("0"),
        planned_shares_to_redeem=Decimal(
            "10"
        ),
        planned_net_shares_change=Decimal(
            "-10"
        ),
        pricing_locked_at=NOW,
        pricing_unlocked_at=None,
        accounting_finalized_at=None,
        updated_at=NOW,
        error=None,
    )

    fund = SimpleNamespace(
        id=3,
        code="wb10",
        shares_outstanding_current=Decimal(
            "100"
        ),
    )

    sale_batch = SimpleNamespace(
        id=21,
        status="sale_execution_completed",
    )

    bybit_flow = SimpleNamespace(
        id=31,
        status="completed",
    )

    payout_batch = make_batch(
        id=41,
        settlement_batch_id=11,
        status="completed",
        payout_leg_count=1,
        confirmed_payout_leg_count=1,
    )

    payout_leg = make_leg(
        payout_batch_id=41,
        settlement_batch_id=11,
        fund_id=3,
        status="balance_refreshed",
    )

    refresh_validation = (
        balance_refresh_validation(
            batch=payout_batch,
            legs=[payout_leg],
        )
    )

    payout_wallet = make_wallet(
        usdt_balance=Decimal("9"),
    )

    order = SimpleNamespace(
        id=61,
        user_id=101,
        status=ORDER_STATUS_PROCESSING,
        executed_at=None,
    )

    position = SimpleNamespace(
        user_id=101,
        fund_id=3,
        shares=Decimal("20"),
        shares_reserved=Decimal("10"),
    )

    runtime_state = SimpleNamespace(
        fund_id=3,
        pricing_locked=True,
        pricing_lock_reason="settlement",
        pricing_lock_batch_id=11,
        pricing_locked_at=NOW,
        pricing_unlocked_at=None,
        updated_at=NOW,
    )

    state_before = {
        "order_status": order.status,
        "order_executed_at": (
            order.executed_at
        ),
        "position_shares": (
            position.shares
        ),
        "position_shares_reserved": (
            position.shares_reserved
        ),
        "fund_shares": (
            fund.shares_outstanding_current
        ),
        "wallet_balance": (
            payout_wallet.usdt_balance
        ),
        "runtime_locked": (
            runtime_state.pricing_locked
        ),
        "runtime_owner": (
            runtime_state
            .pricing_lock_batch_id
        ),
        "runtime_unlocked_at": (
            runtime_state
            .pricing_unlocked_at
        ),
    }

    db = FinalizationSession()

    monkeypatch.setattr(
        service.settings,
        "NEGATIVE_NET_FINALIZATION_ENABLED",
        True,
    )

    monkeypatch.setattr(
        service.settings,
        (
            "NEGATIVE_NET_FINALIZATION_"
            "UNLOCK_PRICING"
        ),
        True,
    )

    replacements = {
        "_lock_settlement_batch": (
            settlement_batch
        ),
        "_lock_fund": fund,
        "_lock_sale_batch": sale_batch,
        "_lock_bybit_flow": bybit_flow,
        "_lock_payout_batch": payout_batch,
        "_lock_payout_legs": [
            payout_leg
        ],
        "_lock_bsc_intents": [],
    }

    for name, value in (
        replacements.items()
    ):
        monkeypatch.setattr(
            service,
            name,
            lambda *args,
            _value=value,
            **kwargs: _value,
        )

    monkeypatch.setattr(
        service,
        "_lock_existing_finalization",
        lambda *args, **kwargs: (
            db.finalization
        ),
    )

    monkeypatch.setattr(
        service,
        "_validate_input_state",
        lambda *args, **kwargs: {
            "bybit_cash_delivery": {
                "durable_evidence_validated": (
                    True
                ),
            },
            "bsc_delivery": {
                "all_intents_terminal_confirmed": (
                    True
                ),
            },
            "balance_refresh": (
                refresh_validation
            ),
            "settlement_wallet_residual": {
                "expected_residual_usdt": "0",
                "actual_attributable_residual_usdt": (
                    "0"
                ),
                "residual_owner": "fund",
                "residual_is_user_payout": False,
            },
        },
    )

    monkeypatch.setattr(
        service,
        "_lock_payout_user_wallets",
        lambda *args, **kwargs: {
            501: payout_wallet,
        },
    )

    forbidden_calls: list[str] = []

    def forbidden_after_wallet_gate(
        *args: Any,
        **kwargs: Any,
    ) -> None:
        forbidden_calls.append(
            "called"
        )

        raise AssertionError(
            "Accounting preparation, pricing-lock "
            "mutation or accounting must not run "
            "after wallet DB mismatch"
        )

    monkeypatch.setattr(
        service,
        "_prepare_accounting_context",
        forbidden_after_wallet_gate,
    )

    monkeypatch.setattr(
        service,
        "_lock_runtime_state",
        forbidden_after_wallet_gate,
    )

    monkeypatch.setattr(
        service,
        "_apply_accounting_finalization",
        forbidden_after_wallet_gate,
    )

    result = (
        service
        .finalize_negative_net_settlement(
            db,
            settlement_batch_id=11,
            now=NOW,
        )
    )

    assert result.ok is False

    assert (
        "payout_user_wallet_balance_mismatch"
        in str(result.error)
    )

    assert db.finalization is not None

    assert db.finalization.status == (
        FINALIZATION_BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert settlement_batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert (
        "payout_user_wallet_balance_mismatch"
        in str(db.finalization.error)
    )

    assert (
        "payout_user_wallet_balance_mismatch"
        in str(settlement_batch.error)
    )

    assert (
        db.finalization
        .accounting_finalized_at
        is None
    )

    assert (
        settlement_batch
        .accounting_finalized_at
        is None
    )

    assert (
        settlement_batch
        .pricing_unlocked_at
        is None
    )

    assert order.status == (
        state_before[
            "order_status"
        ]
    )

    assert order.status != "success"

    assert order.executed_at == (
        state_before[
            "order_executed_at"
        ]
    )

    assert position.shares == (
        state_before[
            "position_shares"
        ]
    )

    assert position.shares_reserved == (
        state_before[
            "position_shares_reserved"
        ]
    )

    assert (
        fund.shares_outstanding_current
        == state_before[
            "fund_shares"
        ]
    )

    assert payout_wallet.usdt_balance == (
        state_before[
            "wallet_balance"
        ]
    )

    assert runtime_state.pricing_locked == (
        state_before[
            "runtime_locked"
        ]
    )

    assert (
        runtime_state
        .pricing_lock_batch_id
        == state_before[
            "runtime_owner"
        ]
    )

    assert (
        runtime_state
        .pricing_unlocked_at
        == state_before[
            "runtime_unlocked_at"
        ]
    )

    assert forbidden_calls == []

    assert (
        db.finalization
        .reconciliation_json[
            "no_real_bybit_calls"
        ]
        is True
    )

    assert (
        db.finalization
        .reconciliation_json[
            "no_real_bsc_calls"
        ]
        is True
    )

    assert (
        db.finalization
        .reconciliation_json[
            "no_payout_transfers"
        ]
        is True
    )
