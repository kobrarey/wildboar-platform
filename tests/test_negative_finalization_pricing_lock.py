from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import (
    FundNegativeFinalizationBatch,
)
import app.settlement.negative_finalization as service
from app.settlement.statuses import (
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED,
    BYBIT_FLOW_STATUS_COMPLETED,
    FINALIZATION_BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    PAYOUT_BATCH_STATUS_COMPLETED,
    PRICING_LOCK_REASON_SETTLEMENT,
)


NOW = datetime(
    2026,
    7,
    26,
    15,
    0,
    tzinfo=timezone.utc,
)


def make_settlement_batch() -> Any:
    return SimpleNamespace(
        id=11,
        fund_id=3,
        status=(
            BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED
        ),
        settlement_price_usdt=Decimal("10"),
        shares_outstanding_before=Decimal(
            "100"
        ),
        pricing_locked_at=NOW,
        pricing_unlocked_at=None,
        accounting_finalized_at=None,
        updated_at=NOW,
        error=None,
    )


def make_runtime_state(
    **overrides: Any,
) -> Any:
    values = {
        "fund_id": 3,
        "pricing_locked": True,
        "pricing_lock_reason": (
            PRICING_LOCK_REASON_SETTLEMENT
        ),
        "pricing_lock_batch_id": 11,
        "pricing_locked_at": NOW,
        "pricing_unlocked_at": None,
        "updated_at": NOW,
    }

    values.update(overrides)

    return SimpleNamespace(**values)


def test_owned_pricing_lock_is_accepted() -> None:
    evidence = (
        service
        ._validate_pricing_lock_ownership(
            runtime_state=(
                make_runtime_state()
            ),
            settlement_batch=(
                make_settlement_batch()
            ),
        )
    )

    assert evidence[
        "runtime_pricing_locked"
    ] is True

    assert evidence[
        "runtime_pricing_lock_batch_id"
    ] == 11

    assert evidence[
        "runtime_pricing_lock_reason"
    ] == PRICING_LOCK_REASON_SETTLEMENT


@pytest.mark.parametrize(
    (
        "runtime_state",
        "error_match",
    ),
    [
        (
            None,
            "runtime state is missing",
        ),
        (
            make_runtime_state(
                pricing_locked=False,
            ),
            "not actively locked",
        ),
        (
            make_runtime_state(
                pricing_lock_reason="manual",
            ),
            "reason mismatch",
        ),
        (
            make_runtime_state(
                pricing_lock_batch_id=99,
            ),
            "owned by another settlement batch",
        ),
        (
            make_runtime_state(
                pricing_unlocked_at=NOW,
            ),
            "already marked unlocked",
        ),
    ],
)
def test_invalid_pricing_lock_ownership_is_rejected(
    runtime_state: Any,
    error_match: str,
) -> None:
    with pytest.raises(
        service.NegativeFinalizationError,
        match=error_match,
    ):
        (
            service
            ._validate_pricing_lock_ownership(
                runtime_state=runtime_state,
                settlement_batch=(
                    make_settlement_batch()
                ),
            )
        )


def test_release_clears_only_owned_pricing_lock() -> None:
    settlement_batch = (
        make_settlement_batch()
    )

    runtime_state = make_runtime_state()

    ownership = (
        service
        ._validate_pricing_lock_ownership(
            runtime_state=runtime_state,
            settlement_batch=(
                settlement_batch
            ),
        )
    )

    result = service._release_pricing_lock(
        runtime_state=runtime_state,
        settlement_batch=(
            settlement_batch
        ),
        unlock_ts=NOW,
        validated_ownership=ownership,
    )

    assert result["ownership"] == ownership

    assert runtime_state.pricing_locked is False
    assert (
        runtime_state.pricing_lock_reason
        is None
    )
    assert (
        runtime_state.pricing_lock_batch_id
        is None
    )
    assert (
        runtime_state.pricing_unlocked_at
        == NOW
    )

    assert (
        settlement_batch.pricing_unlocked_at
        == NOW
    )


class FakeSession:
    def __init__(self) -> None:
        self.finalization = None
        self.flush_calls = 0

    def add(
        self,
        value: Any,
    ) -> None:
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


def test_wrong_owner_marks_current_batch_review_before_accounting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settlement_batch = (
        make_settlement_batch()
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
        status=BYBIT_FLOW_STATUS_COMPLETED,
    )

    payout_batch = SimpleNamespace(
        id=41,
        status=PAYOUT_BATCH_STATUS_COMPLETED,
    )

    payout_legs = [
        SimpleNamespace(
            id=51,
        )
    ]

    wrong_owner_runtime = (
        make_runtime_state(
            pricing_lock_batch_id=99,
        )
    )

    context = {
        "orders": [],
        "buy_orders": [],
        "redeem_orders": [],
        "buy_validation": {
            "total_buy_usdt": Decimal("0"),
            "total_buy_shares": Decimal("0"),
            "computed_shares_by_order_id": {},
        },
        "redeem_validation": {
            "total_redeem_shares": Decimal(
                "0"
            ),
            "total_net_user_payout_usdt": Decimal(
                "0"
            ),
            "total_partial_month_fee_usdt": Decimal(
                "0"
            ),
        },
        "share_validation": {
            "shares_outstanding_before": Decimal(
                "100"
            ),
            "shares_outstanding_after": Decimal(
                "100"
            ),
            "planned_net_shares_change": Decimal(
                "0"
            ),
            "actual_net_shares_change": Decimal(
                "0"
            ),
        },
        "position_wallet_validation": {
            "positions_before": {},
            "user_wallet_reserves_before": {},
            "redeem_positions": {},
            "buy_positions": {},
            "buy_wallets": {},
        },
    }

    db = FakeSession()

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
        "_lock_payout_legs": payout_legs,
        "_lock_bsc_intents": [],
        "_lock_runtime_state": (
            wrong_owner_runtime
        ),
    }

    for name, value in replacements.items():
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
        lambda *args, **kwargs: None,
    )

    monkeypatch.setattr(
        service,
        "_validate_input_state",
        lambda *args, **kwargs: {
            "bybit_cash_delivery": {
                "validated": True,
            },
            "bsc_delivery": {
                "validated": True,
            },
            "balance_refresh": {
                "validated": True,
            },
        },
    )

    monkeypatch.setattr(
        service,
        "_prepare_accounting_context",
        lambda *args, **kwargs: context,
    )

    def forbidden_accounting(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "Accounting must not start when "
            "pricing lock has the wrong owner"
        )

    monkeypatch.setattr(
        service,
        "_apply_redeem_accounting",
        forbidden_accounting,
    )

    monkeypatch.setattr(
        service,
        "_apply_buy_accounting",
        forbidden_accounting,
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
        result.status_after
        == FINALIZATION_BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert (
        settlement_batch.status
        == BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert (
        "owned by another settlement batch"
        in str(result.error)
    )

    assert (
        fund.shares_outstanding_current
        == Decimal("100")
    )

    assert (
        wrong_owner_runtime.pricing_locked
        is True
    )

    assert (
        wrong_owner_runtime
        .pricing_lock_batch_id
        == 99
    )

    assert (
        wrong_owner_runtime
        .pricing_unlocked_at
        is None
    )

    assert db.finalization is not None

    assert (
        db.finalization.accounting_finalized_at
        is None
    )

    assert (
        db.finalization.pricing_unlocked_at
        is None
    )