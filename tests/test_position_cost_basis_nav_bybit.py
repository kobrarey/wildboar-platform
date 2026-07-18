from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from app.models import UserFundPositionStats
from app.navcalc.minute_builder import (
    open_new_minute_state,
    rebase_minute_state_for_shares,
)
from app.settlement import bybit_deposit_service
from app.settlement.bybit_deposit_service import (
    BYBIT_INTERNAL_TRANSFER_STATUS_FAILED,
    BYBIT_INTERNAL_TRANSFER_STATUS_PENDING,
    BYBIT_INTERNAL_TRANSFER_STATUS_SKIPPED_ALREADY_UNIFIED,
    BYBIT_INTERNAL_TRANSFER_STATUS_SKIPPED_ZERO_NET,
    BYBIT_INTERNAL_TRANSFER_STATUS_SUCCESS,
    BYBIT_INTERNAL_TRANSFER_STATUS_UNKNOWN,
    BybitInternalTransferResult,
    ensure_fund_to_unified_internal_transfer,
    is_internal_transfer_accounting_ready,
)
from app.settlement.position_cost_basis import (
    PositionCostBasisError,
    apply_buy_cost_basis,
    apply_redeem_cost_basis,
    validate_position_cost_basis,
)
from app.settlement.statuses import (
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_PENDING_CONFIRMATION,
)
from app.trading.service import (
    _calculate_position_metrics,
)


D = Decimal
ZERO = D("0")
Q10 = D("0.0000000001")
NOW = datetime(
    2043,
    1,
    1,
    12,
    0,
    tzinfo=timezone.utc,
)


class StatsQuery:
    def __init__(
        self,
        db: "CostBasisSession",
    ) -> None:
        self.db = db

    def filter(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "StatsQuery":
        return self

    def with_for_update(
        self,
    ) -> "StatsQuery":
        return self

    def first(
        self,
    ) -> UserFundPositionStats | None:
        return self.db.stats


class CostBasisSession:
    def __init__(
        self,
        *,
        stats: UserFundPositionStats | None = None,
    ) -> None:
        self.stats = stats
        self.flush_calls = 0

    def query(
        self,
        model: type[Any],
    ) -> StatsQuery:
        assert model is UserFundPositionStats
        return StatsQuery(self)

    def add(
        self,
        obj: Any,
    ) -> None:
        if isinstance(
            obj,
            UserFundPositionStats,
        ):
            self.stats = obj

    def flush(
        self,
    ) -> None:
        self.flush_calls += 1


class FlushSession:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flush_calls = 0

    def add(
        self,
        obj: Any,
    ) -> None:
        self.added.append(obj)

    def flush(
        self,
    ) -> None:
        self.flush_calls += 1


def make_stats(
    *,
    average: Decimal,
) -> UserFundPositionStats:
    return UserFundPositionStats(
        user_id=1,
        fund_id=9,
        avg_entry_price_usdt=average,
        updated_at=NOW,
    )


def make_position(
    *,
    shares: Decimal,
) -> SimpleNamespace:
    return SimpleNamespace(
        user_id=1,
        fund_id=9,
        shares=shares,
        shares_reserved=ZERO,
    )


def make_internal_transfer_batch(
    *,
    amount: Decimal = D("10"),
    transfer_id: str | None = "transfer-1",
    completed_at: datetime | None = None,
    status: str | None = None,
    account_type: str | None = "FUND",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=171,
        fund_id=9,
        net_cash_usdt=amount,
        bybit_deposit_account_type=account_type,
        bybit_internal_transfer_id=transfer_id,
        bybit_internal_transfer_completed_at=(
            completed_at
        ),
        bybit_internal_transfer_status=status,
        bybit_internal_transfer_error=None,
        status=BATCH_STATUS_PENDING_CONFIRMATION,
        error=None,
        updated_at=None,
    )


def test_first_buy_cost_basis_uses_full_amount() -> None:
    db = CostBasisSession()
    position = make_position(
        shares=D("0"),
    )

    average = apply_buy_cost_basis(
        db,
        position=position,
        amount_usdt=D("10"),
        issued_shares=D("0.0158"),
        now=NOW,
    )

    assert average == D("632.9113924051")
    assert db.stats is not None
    assert (
        D(str(db.stats.avg_entry_price_usdt))
        == D("632.9113924051")
    )


def test_multiple_buys_use_weighted_cost_basis() -> None:
    db = CostBasisSession()
    position = make_position(
        shares=D("0"),
    )

    first_average = apply_buy_cost_basis(
        db,
        position=position,
        amount_usdt=D("10"),
        issued_shares=D("0.0200"),
        now=NOW,
    )
    assert first_average == D("500.0000000000")

    position.shares = D("0.0200")

    second_average = apply_buy_cost_basis(
        db,
        position=position,
        amount_usdt=D("10"),
        issued_shares=D("0.0100"),
        now=NOW,
    )

    assert second_average == D("666.6666666667")


def test_partial_and_full_redeem_cost_basis() -> None:
    original_average = D("632.9113924051")
    db = CostBasisSession(
        stats=make_stats(
            average=original_average,
        )
    )
    position = make_position(
        shares=D("0.0158"),
    )

    partial_average = apply_redeem_cost_basis(
        db,
        position=position,
        redeem_shares=D("0.0058"),
        now=NOW,
    )

    assert partial_average == original_average
    assert (
        D(str(db.stats.avg_entry_price_usdt))
        == original_average
    )

    position.shares = D("0.0100")

    full_average = apply_redeem_cost_basis(
        db,
        position=position,
        redeem_shares=D("0.0100"),
        now=NOW,
    )

    assert full_average == ZERO
    assert (
        D(str(db.stats.avg_entry_price_usdt))
        == ZERO
    )


def test_missing_cost_basis_fails_closed() -> None:
    db = CostBasisSession()
    position = make_position(
        shares=D("0.1000"),
    )

    with pytest.raises(
        PositionCostBasisError,
        match="missing_or_invalid_position_cost_basis",
    ):
        validate_position_cost_basis(
            db,
            position=position,
            user_id=1,
            fund_id=9,
        )


def test_zero_position_with_nonzero_average_fails_closed(
) -> None:
    db = CostBasisSession(
        stats=make_stats(
            average=D("500"),
        )
    )
    position = make_position(
        shares=ZERO,
    )

    with pytest.raises(
        PositionCostBasisError,
        match="zero_position_with_nonzero_average",
    ):
        validate_position_cost_basis(
            db,
            position=position,
            user_id=1,
            fund_id=9,
        )


def test_position_pnl_uses_long_position_sign() -> None:
    average = D("632.9113924051")

    (
        stored_average,
        result_pct,
        result_usdt,
    ) = _calculate_position_metrics(
        shares=D("0.0158"),
        current_price=D("630.75"),
        avg_entry_price=average,
    )

    assert stored_average == average
    assert result_pct is not None
    assert result_usdt is not None
    assert (
        result_pct.quantize(Q10)
        == D("-0.3415000000")
    )
    assert (
        result_usdt.quantize(Q10)
        == D("-0.0341500000")
    )


@pytest.mark.parametrize(
    (
        "shares",
        "current_price",
        "average",
    ),
    [
        (ZERO, D("630.75"), D("632")),
        (D("0.0158"), ZERO, D("632")),
        (D("0.0158"), D("630.75"), None),
        (D("0.0158"), D("630.75"), ZERO),
    ],
)
def test_invalid_pnl_inputs_return_none(
    shares: Decimal,
    current_price: Decimal,
    average: Decimal | None,
) -> None:
    assert _calculate_position_metrics(
        shares=shares,
        current_price=current_price,
        avg_entry_price=average,
    ) == (None, None, None)


def test_nav_rebase_preserves_previous_share_price(
) -> None:
    previous_nav = D("631.2595255200")
    previous_shares = D("1.0000000000")
    current_shares = D("1.0158000000")
    current_nav = D("641.2426277400")

    state = open_new_minute_state(
        fund_code="wb_test",
        minute_ts=NOW,
        current_sample_nav=current_nav,
        sample_ts=NOW,
        shares_outstanding=current_shares,
        prev_close_nav=previous_nav,
        prev_close_shares_outstanding=(
            previous_shares
        ),
    )

    open_price = (
        state.open_nav
        / state.shares_outstanding
    )
    low_price = (
        state.low_nav
        / state.shares_outstanding
    )
    close_price = (
        state.close_nav
        / state.shares_outstanding
    )

    assert (
        open_price.quantize(Q10)
        == D("631.2595255200")
    )
    assert (
        low_price.quantize(Q10)
        == D("631.2595255200")
    )
    assert (
        close_price.quantize(Q10)
        == D("631.2685841110")
    )
    assert (
        low_price.quantize(Q10)
        != D("621.4407614885")
    )


def test_same_minute_rebase_preserves_ohlc_prices(
) -> None:
    state = open_new_minute_state(
        fund_code="wb_test",
        minute_ts=NOW,
        current_sample_nav=D("631.25"),
        sample_ts=NOW,
        shares_outstanding=D("1"),
        prev_close_nav=D("631.20"),
        prev_close_shares_outstanding=D("1"),
    )

    old_prices = (
        state.open_nav,
        state.high_nav,
        state.low_nav,
        state.close_nav,
    )

    rebase_minute_state_for_shares(
        state,
        shares_outstanding=D("1.0158"),
    )

    new_prices = (
        state.open_nav
        / state.shares_outstanding,
        state.high_nav
        / state.shares_outstanding,
        state.low_nav
        / state.shares_outstanding,
        state.close_nav
        / state.shares_outstanding,
    )

    assert tuple(
        value.quantize(Q10)
        for value in new_prices
    ) == tuple(
        value.quantize(Q10)
        for value in old_prices
    )


@pytest.mark.parametrize(
    "status",
    [
        BYBIT_INTERNAL_TRANSFER_STATUS_SUCCESS,
        BYBIT_INTERNAL_TRANSFER_STATUS_SKIPPED_ZERO_NET,
        BYBIT_INTERNAL_TRANSFER_STATUS_SKIPPED_ALREADY_UNIFIED,
    ],
)
def test_internal_transfer_ready_statuses(
    status: str,
) -> None:
    batch = make_internal_transfer_batch(
        status=status,
    )

    assert (
        is_internal_transfer_accounting_ready(
            batch
        )
        is True
    )


@pytest.mark.parametrize(
    "status",
    [
        None,
        BYBIT_INTERNAL_TRANSFER_STATUS_PENDING,
        BYBIT_INTERNAL_TRANSFER_STATUS_FAILED,
        BYBIT_INTERNAL_TRANSFER_STATUS_UNKNOWN,
    ],
)
def test_internal_transfer_non_ready_statuses(
    status: str | None,
) -> None:
    batch = make_internal_transfer_batch(
        status=status,
    )

    assert (
        is_internal_transfer_accounting_ready(
            batch
        )
        is False
    )


def test_legacy_completed_at_without_status_is_polled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FlushSession()
    batch = make_internal_transfer_batch(
        completed_at=NOW,
        status=None,
    )
    calls = {
        "query": 0,
        "post": 0,
    }

    monkeypatch.setattr(
        bybit_deposit_service,
        "_get_batch_for_update",
        lambda *args, **kwargs: batch,
    )

    def fake_query(
        *args: Any,
        **kwargs: Any,
    ) -> BybitInternalTransferResult:
        calls["query"] += 1
        return BybitInternalTransferResult(
            transfer_id="transfer-1",
            status=(
                BYBIT_INTERNAL_TRANSFER_STATUS_PENDING
            ),
            completed=False,
            raw={},
        )

    monkeypatch.setattr(
        bybit_deposit_service,
        "query_fund_to_unified_internal_transfer",
        fake_query,
    )

    def forbidden_post(
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        calls["post"] += 1
        raise AssertionError(
            "duplicate internal transfer POST"
        )

    monkeypatch.setattr(
        bybit_deposit_service,
        "execute_fund_to_unified_internal_transfer",
        forbidden_post,
    )

    result = (
        ensure_fund_to_unified_internal_transfer(
            db,
            batch_id=171,
            fund_client=SimpleNamespace(),
        )
    )

    assert result is False
    assert calls == {
        "query": 1,
        "post": 0,
    }
    assert (
        batch.bybit_internal_transfer_status
        == BYBIT_INTERNAL_TRANSFER_STATUS_PENDING
    )
    assert (
        batch.bybit_internal_transfer_completed_at
        is None
    )


def test_existing_internal_transfer_success_is_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FlushSession()
    batch = make_internal_transfer_batch()

    monkeypatch.setattr(
        bybit_deposit_service,
        "_get_batch_for_update",
        lambda *args, **kwargs: batch,
    )
    monkeypatch.setattr(
        bybit_deposit_service,
        "query_fund_to_unified_internal_transfer",
        lambda *args, **kwargs: (
            BybitInternalTransferResult(
                transfer_id="transfer-1",
                status=(
                    BYBIT_INTERNAL_TRANSFER_STATUS_SUCCESS
                ),
                completed=True,
                raw={},
            )
        ),
    )

    result = (
        ensure_fund_to_unified_internal_transfer(
            db,
            batch_id=171,
            fund_client=SimpleNamespace(),
        )
    )

    assert result is True
    assert (
        batch.bybit_internal_transfer_status
        == BYBIT_INTERNAL_TRANSFER_STATUS_SUCCESS
    )
    assert (
        batch.bybit_internal_transfer_completed_at
        is not None
    )
    assert (
        batch.bybit_internal_transfer_error
        is None
    )


def test_existing_internal_transfer_failed_is_saved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FlushSession()
    batch = make_internal_transfer_batch()

    monkeypatch.setattr(
        bybit_deposit_service,
        "_get_batch_for_update",
        lambda *args, **kwargs: batch,
    )
    monkeypatch.setattr(
        bybit_deposit_service,
        "query_fund_to_unified_internal_transfer",
        lambda *args, **kwargs: (
            BybitInternalTransferResult(
                transfer_id="transfer-1",
                status=(
                    BYBIT_INTERNAL_TRANSFER_STATUS_FAILED
                ),
                completed=False,
                raw={},
            )
        ),
    )

    result = (
        ensure_fund_to_unified_internal_transfer(
            db,
            batch_id=171,
            fund_client=SimpleNamespace(),
        )
    )

    assert result is False
    assert (
        batch.bybit_internal_transfer_status
        == BYBIT_INTERNAL_TRANSFER_STATUS_FAILED
    )
    assert (
        batch.status
        == BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert (
        batch.bybit_internal_transfer_completed_at
        is None
    )
    assert (
        "transferId=transfer-1"
        in batch.bybit_internal_transfer_error
    )


def test_missing_internal_transfer_query_row_is_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FlushSession()
    batch = make_internal_transfer_batch()
    post_calls = 0

    monkeypatch.setattr(
        bybit_deposit_service,
        "_get_batch_for_update",
        lambda *args, **kwargs: batch,
    )
    monkeypatch.setattr(
        bybit_deposit_service,
        "query_fund_to_unified_internal_transfer",
        lambda *args, **kwargs: None,
    )

    def forbidden_post(
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        nonlocal post_calls
        post_calls += 1
        raise AssertionError(
            "duplicate internal transfer POST"
        )

    monkeypatch.setattr(
        bybit_deposit_service,
        "execute_fund_to_unified_internal_transfer",
        forbidden_post,
    )

    result = (
        ensure_fund_to_unified_internal_transfer(
            db,
            batch_id=171,
            fund_client=SimpleNamespace(),
        )
    )

    assert result is False
    assert post_calls == 0
    assert (
        batch.bybit_internal_transfer_status
        == BYBIT_INTERNAL_TRANSFER_STATUS_UNKNOWN
    )
    assert (
        batch.status
        == BATCH_STATUS_PENDING_CONFIRMATION
    )


def test_saved_success_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = FlushSession()
    batch = make_internal_transfer_batch(
        completed_at=NOW,
        status=(
            BYBIT_INTERNAL_TRANSFER_STATUS_SUCCESS
        ),
    )

    monkeypatch.setattr(
        bybit_deposit_service,
        "_get_batch_for_update",
        lambda *args, **kwargs: batch,
    )

    def forbidden_query(
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        raise AssertionError(
            "completed SUCCESS was polled again"
        )

    monkeypatch.setattr(
        bybit_deposit_service,
        "query_fund_to_unified_internal_transfer",
        forbidden_query,
    )

    result = (
        ensure_fund_to_unified_internal_transfer(
            db,
            batch_id=171,
            fund_client=SimpleNamespace(),
        )
    )

    assert result is True
    assert (
        batch.bybit_internal_transfer_status
        == BYBIT_INTERNAL_TRANSFER_STATUS_SUCCESS
    )