from __future__ import annotations

import inspect
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import inspect as sa_inspect

import app.settlement.negative_external_state as external_state_service
import app.settlement.negative_failure_service as failure_service
import app.settlement.negative_net_targets as negative_targets
import app.settlement.redeem_reserve_service as redeem_reserve_service
import workers.fund_negative_net_targets_worker as targets_worker
from app.models import (
    FundNegativeBybitFlow,
    FundNegativeFinalizationBatch,
    FundNegativePayoutBatch,
    FundNegativePayoutLeg,
    FundNegativeSaleBatch,
    FundNegativeSaleLeg,
    FundOrder,
    FundSettlementBatch,
    FundSettlementTransfer,
    UserFundPosition,
)
from app.settlement.negative_external_state import (
    NegativeExternalState,
    inspect_negative_external_state,
)
from app.settlement.negative_net_fees import (
    NegativeNetFeeError,
)
from app.settlement.negative_net_targets import (
    NegativeNetTargetError,
    resolve_negative_net_bybit_withdrawal_fee,
    validate_live_withdrawal_amount,
)
from app.settlement.redeem_reserve_service import (
    RedeemReserveReleaseError,
    release_redeem_reserve_if_safe,
)
from app.settlement.statuses import (
    BATCH_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_NEGATIVE_NET_SALE_PLANNED,
    BATCH_STATUS_NEGATIVE_NET_TARGETS_CALCULATED,
    ORDER_SIDE_BUY,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_FAILED_REQUIRES_REVIEW,
    ORDER_STATUS_PENDING,
)


NOW = datetime(
    2026,
    7,
    19,
    12,
    0,
    tzinfo=timezone.utc,
)


class Row:
    def __init__(
        self,
        **values: Any,
    ) -> None:
        self.__dict__.update(values)

    def __getattr__(
        self,
        name: str,
    ) -> Any:
        return None


class ModelQuery:
    def __init__(
        self,
        rows: list[Any],
    ) -> None:
        self.rows = rows

    def filter(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "ModelQuery":
        return self

    def order_by(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "ModelQuery":
        return self

    def with_for_update(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> "ModelQuery":
        return self

    def first(self) -> Any:
        return (
            self.rows[0]
            if self.rows
            else None
        )

    def all(self) -> list[Any]:
        return list(self.rows)


class ModelSession:
    def __init__(
        self,
        rows_by_model: dict[
            type[Any],
            list[Any],
        ],
    ) -> None:
        self.rows_by_model = rows_by_model
        self.added: list[Any] = []
        self.flush_count = 0

    def query(
        self,
        model: type[Any],
    ) -> ModelQuery:
        return ModelQuery(
            self.rows_by_model.get(
                model,
                [],
            )
        )

    def add(
        self,
        value: Any,
    ) -> None:
        self.added.append(value)

    def flush(self) -> None:
        self.flush_count += 1

    def commit(self) -> None:
        raise AssertionError(
            "Service must not commit"
        )


def make_external_state(
    *,
    safe: bool,
    accounting_finalized: bool = False,
    reasons: tuple[str, ...] = (),
    evidence: tuple[
        dict[str, Any],
        ...,
    ] = (),
) -> NegativeExternalState:
    return NegativeExternalState(
        settlement_batch_id=101,
        safe_to_release_reserves=safe,
        safe_to_unlock_pricing=safe,
        accounting_finalized=(
            accounting_finalized
        ),
        sale_action_detected=False,
        earn_action_detected=False,
        universal_transfer_action_detected=False,
        withdrawal_action_detected=False,
        payout_action_detected=False,
        gas_topup_action_detected=False,
        other_external_action_detected=False,
        reasons=reasons,
        evidence=evidence,
    )


def make_classifier_batch(
    **overrides: Any,
) -> Row:
    values = {
        "id": 101,
        "fund_id": 7,
        "status": (
            BATCH_STATUS_AWAITING_NEGATIVE_NET_EXECUTION
        ),
        "accounting_finalized_at": None,
        "pricing_locked_at": NOW,
        "pricing_unlocked_at": None,
    }
    values.update(overrides)
    return Row(**values)


def classifier_session(
    *,
    batch: Any,
    orders: list[Any] | None = None,
    sale_batch: Any | None = None,
    sale_legs: list[Any] | None = None,
    bybit_flow: Any | None = None,
    payout_batch: Any | None = None,
    payout_legs: list[Any] | None = None,
    finalization: Any | None = None,
    transfers: list[Any] | None = None,
) -> ModelSession:
    return ModelSession(
        {
            FundSettlementBatch: [batch],
            FundOrder: list(orders or []),
            FundNegativeSaleBatch: (
                [sale_batch]
                if sale_batch is not None
                else []
            ),
            FundNegativeSaleLeg: list(
                sale_legs or []
            ),
            FundNegativeBybitFlow: (
                [bybit_flow]
                if bybit_flow is not None
                else []
            ),
            FundNegativePayoutBatch: (
                [payout_batch]
                if payout_batch is not None
                else []
            ),
            FundNegativePayoutLeg: list(
                payout_legs or []
            ),
            FundNegativeFinalizationBatch: (
                [finalization]
                if finalization is not None
                else []
            ),
            FundSettlementTransfer: list(
                transfers or []
            ),
        }
    )


def test_new_orm_contract() -> None:
    order_columns = (
        sa_inspect(FundOrder).columns
    )
    released = order_columns[
        "redeem_reserve_released_shares"
    ]

    assert released.nullable is False
    assert released.type.precision == 30
    assert released.type.scale == 10
    assert (
        order_columns[
            "redeem_reserve_released_at"
        ].nullable
        is True
    )
    assert (
        order_columns[
            "redeem_reserve_release_reason"
        ].nullable
        is True
    )

    batch_columns = (
        sa_inspect(
            FundSettlementBatch
        ).columns
    )

    assert (
        batch_columns[
            "negative_net_target_diagnostics_json"
        ].nullable
        is True
    )
    policy = batch_columns[
        "negative_net_fee_policy_version"
    ]
    assert policy.nullable is True
    assert policy.type.length == 64


def test_redeem_release_is_exact_and_idempotent(
    monkeypatch: Any,
) -> None:
    order = Row(
        id=11,
        user_id=5,
        fund_id=7,
        settlement_batch_id=101,
        side=ORDER_SIDE_REDEEM,
        status=(
            ORDER_STATUS_FAILED_REQUIRES_REVIEW
        ),
        shares=Decimal("2.5000"),
        executed_at=None,
        redeem_reserve_released_shares=(
            Decimal("0")
        ),
        redeem_reserve_released_at=None,
        redeem_reserve_release_reason=None,
        error=None,
    )
    position = Row(
        user_id=5,
        fund_id=7,
        shares=Decimal("9.0000"),
        shares_reserved=Decimal("2.5000"),
    )
    db = ModelSession(
        {
            FundOrder: [order],
            UserFundPosition: [position],
        }
    )

    monkeypatch.setattr(
        redeem_reserve_service,
        "inspect_negative_external_state",
        lambda *args, **kwargs: (
            make_external_state(safe=True)
        ),
    )

    first = release_redeem_reserve_if_safe(
        db,
        order_id=order.id,
        reason="target_validation_failed",
    )
    second = release_redeem_reserve_if_safe(
        db,
        order_id=order.id,
        reason="repeated_failure",
    )

    assert first == Decimal("2.5000")
    assert second == Decimal("0")
    assert position.shares == Decimal("9.0000")
    assert (
        position.shares_reserved
        == Decimal("0.0000")
    )
    assert (
        order.redeem_reserve_released_shares
        == Decimal("2.5000")
    )
    assert (
        order.redeem_reserve_released_at
        is not None
    )
    assert (
        order.redeem_reserve_release_reason
        == "target_validation_failed"
    )


def test_partial_redeem_release_fails_closed(
    monkeypatch: Any,
) -> None:
    order = Row(
        id=12,
        user_id=5,
        fund_id=7,
        settlement_batch_id=101,
        side=ORDER_SIDE_REDEEM,
        status=ORDER_STATUS_PENDING,
        shares=Decimal("2.5000"),
        executed_at=None,
        redeem_reserve_released_shares=(
            Decimal("1.0000")
        ),
        error=None,
    )
    position = Row(
        user_id=5,
        fund_id=7,
        shares=Decimal("9.0000"),
        shares_reserved=Decimal("2.5000"),
    )
    db = ModelSession(
        {
            FundOrder: [order],
            UserFundPosition: [position],
        }
    )

    monkeypatch.setattr(
        redeem_reserve_service,
        "inspect_negative_external_state",
        lambda *args, **kwargs: (
            make_external_state(safe=True)
        ),
    )

    with pytest.raises(
        RedeemReserveReleaseError,
    ):
        release_redeem_reserve_if_safe(
            db,
            order_id=order.id,
            reason="partial_release",
        )

    assert (
        position.shares_reserved
        == Decimal("2.5000")
    )


def test_classifier_accepts_db_only_sale_plan() -> None:
    batch = make_classifier_batch()
    sale_batch = Row(
        id=201,
        status="sale_plan_created",
        settlement_batch_id=batch.id,
    )
    sale_leg = Row(
        id=301,
        status="planned",
        leg_group="asset",
        leg_type="sale",
        order_link_id=None,
        bybit_order_id=None,
        bybit_strategy_id=None,
        sent_at=None,
        confirmed_at=None,
        suborders_json=None,
    )

    state = inspect_negative_external_state(
        classifier_session(
            batch=batch,
            sale_batch=sale_batch,
            sale_legs=[sale_leg],
        ),
        settlement_batch_id=batch.id,
    )

    assert state.safe_to_release_reserves is True
    assert state.safe_to_unlock_pricing is True
    assert state.evidence == ()


def test_classifier_detects_sale_external_id() -> None:
    batch = make_classifier_batch()
    sale_batch = Row(
        id=202,
        status="sale_plan_created",
    )
    sale_leg = Row(
        id=302,
        status="planned",
        leg_group="asset",
        leg_type="sale",
        order_link_id="wb-sale-302",
    )

    state = inspect_negative_external_state(
        classifier_session(
            batch=batch,
            sale_batch=sale_batch,
            sale_legs=[sale_leg],
        ),
        settlement_batch_id=batch.id,
    )

    assert state.safe_to_release_reserves is False
    assert state.sale_action_detected is True
    assert any(
        item["field"] == "order_link_id"
        for item in state.evidence
    )


def test_withdrawal_request_requires_attempt_evidence() -> None:
    batch = make_classifier_batch()
    flow = Row(
        id=401,
        status="created",
        withdrawal_request_id="request401",
        withdrawal_status=None,
        withdrawal_id=None,
        withdrawal_record_json=None,
        withdrawal_reconciliation_json=None,
    )
    db = classifier_session(
        batch=batch,
        bybit_flow=flow,
    )

    safe_state = (
        inspect_negative_external_state(
            db,
            settlement_batch_id=batch.id,
        )
    )

    assert (
        safe_state.withdrawal_action_detected
        is False
    )
    assert (
        safe_state.safe_to_release_reserves
        is True
    )

    flow.withdrawal_status = "UNKNOWN"

    unsafe_state = (
        inspect_negative_external_state(
            db,
            settlement_batch_id=batch.id,
        )
    )

    assert (
        unsafe_state.withdrawal_action_detected
        is True
    )
    assert (
        unsafe_state.safe_to_release_reserves
        is False
    )


def test_payout_execution_json_fails_closed() -> None:
    batch = make_classifier_batch()
    payout = Row(
        id=501,
        status="created",
        payout_execution_json={
            "request_sent": True,
        },
    )

    state = inspect_negative_external_state(
        classifier_session(
            batch=batch,
            payout_batch=payout,
        ),
        settlement_batch_id=batch.id,
    )

    assert state.payout_action_detected is True
    assert state.safe_to_unlock_pricing is False


def test_common_failure_releases_both_reserves(
    monkeypatch: Any,
) -> None:
    batch = Row(
        id=101,
        fund_id=7,
        status=(
            BATCH_STATUS_AWAITING_NEGATIVE_NET_EXECUTION
        ),
        pricing_locked_at=NOW,
        pricing_unlocked_at=None,
        accounting_finalized_at=None,
        error=None,
        updated_at=NOW,
    )
    buy = Row(
        id=601,
        side=ORDER_SIDE_BUY,
        status=ORDER_STATUS_PENDING,
        amount_usdt=Decimal("10"),
        buy_reserve_released_usdt=(
            Decimal("0")
        ),
        settlement_locked_at=None,
        error=None,
    )
    redeem = Row(
        id=602,
        side=ORDER_SIDE_REDEEM,
        status=ORDER_STATUS_PENDING,
        shares=Decimal("3.0000"),
        redeem_reserve_released_shares=(
            Decimal("0")
        ),
        settlement_locked_at=None,
        error=None,
    )
    runtime = Row(
        pricing_locked=True,
        pricing_lock_batch_id=batch.id,
        pricing_unlocked_at=None,
    )
    db = ModelSession({})

    monkeypatch.setattr(
        failure_service,
        "_load_batch_for_update",
        lambda *args, **kwargs: batch,
    )
    monkeypatch.setattr(
        failure_service,
        "_load_orders_for_update",
        lambda *args, **kwargs: [
            buy,
            redeem,
        ],
    )
    monkeypatch.setattr(
        failure_service,
        "inspect_negative_external_state",
        lambda *args, **kwargs: (
            make_external_state(safe=True)
        ),
    )

    def release_buy(
        *args: Any,
        **kwargs: Any,
    ) -> Decimal:
        if (
            buy.buy_reserve_released_usdt
            == buy.amount_usdt
        ):
            return Decimal("0")

        buy.buy_reserve_released_usdt = (
            buy.amount_usdt
        )
        return buy.amount_usdt

    def release_redeem(
        *args: Any,
        **kwargs: Any,
    ) -> Decimal:
        if (
            redeem
            .redeem_reserve_released_shares
            == redeem.shares
        ):
            return Decimal("0")

        redeem.redeem_reserve_released_shares = (
            redeem.shares
        )
        return redeem.shares

    monkeypatch.setattr(
        failure_service,
        "release_buy_reserve_if_safe",
        release_buy,
    )
    monkeypatch.setattr(
        failure_service,
        "release_redeem_reserve_if_safe",
        release_redeem,
    )
    monkeypatch.setattr(
        failure_service,
        "get_runtime_state_for_update",
        lambda *args, **kwargs: runtime,
    )

    def unlock(
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        runtime.pricing_locked = False
        runtime.pricing_unlocked_at = NOW
        return runtime

    monkeypatch.setattr(
        failure_service,
        "unlock_pricing_for_fund",
        unlock,
    )

    first = (
        failure_service
        .fail_negative_batch_pre_external(
            db,
            settlement_batch_id=batch.id,
            error="validation_failed",
            source="unit_test",
        )
    )
    second = (
        failure_service
        .fail_negative_batch_pre_external(
            db,
            settlement_batch_id=batch.id,
            error="validation_failed",
            source="unit_test",
        )
    )

    assert (
        batch.status
        == BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert (
        buy.status
        == ORDER_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert (
        redeem.status
        == ORDER_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert (
        first.buy_reserve_released_usdt
        == Decimal("10")
    )
    assert (
        first.redeem_reserve_released_shares
        == Decimal("3.0000")
    )
    assert first.pricing_unlocked is True
    assert second.buy_reserve_released_usdt == 0
    assert (
        second.redeem_reserve_released_shares
        == 0
    )


class ReadOnlyBybitClient:
    def __init__(
        self,
        *,
        chain_row: dict[str, Any],
    ) -> None:
        self.chain_row = chain_row
        self.get_calls: list[
            tuple[str, dict[str, Any]]
        ] = []

    def get(
        self,
        path: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self.get_calls.append(
            (path, params)
        )
        return {
            "result": {
                "rows": [
                    {
                        "coin": "USDT",
                        "chains": [
                            self.chain_row,
                        ],
                    }
                ]
            }
        }

    def post(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        raise AssertionError(
            "Live read-only mode must not POST"
        )


def valid_chain_row() -> dict[str, Any]:
    return {
        "chain": "BSC",
        "withdrawFee": "0.2",
        "withdrawPercentageFee": "0",
        "withdrawMin": "1",
        "withdrawMax": "1000",
        "minAccuracy": "0.01",
        "chainWithdraw": "1",
    }


def test_live_fee_uses_only_coin_info_get() -> None:
    client = ReadOnlyBybitClient(
        chain_row=valid_chain_row()
    )

    policy = (
        resolve_negative_net_bybit_withdrawal_fee(
            bybit_client=client,
            use_live_bybit_withdrawal_fee=True,
        )
    )

    checks = validate_live_withdrawal_amount(
        fee_policy=policy,
        withdrawal_request_amount_usdt=(
            Decimal("10.25")
        ),
    )

    assert policy.amount_usdt == Decimal("0.2")
    assert all(checks.values())
    assert client.get_calls == [
        (
            "/v5/asset/coin/query-info",
            {"coin": "USDT"},
        )
    ]

    with pytest.raises(NegativeNetFeeError):
        validate_live_withdrawal_amount(
            fee_policy=policy,
            withdrawal_request_amount_usdt=(
                Decimal("10.251")
            ),
        )


@pytest.mark.parametrize(
    "missing_field",
    [
        "withdrawFee",
        "withdrawPercentageFee",
        "withdrawMin",
        "minAccuracy",
        "chainWithdraw",
    ],
)
def test_live_coin_info_missing_field_fails_closed(
    missing_field: str,
) -> None:
    row = valid_chain_row()
    row.pop(missing_field)
    client = ReadOnlyBybitClient(
        chain_row=row
    )

    with pytest.raises(
        NegativeNetFeeError,
    ):
        resolve_negative_net_bybit_withdrawal_fee(
            bybit_client=client,
            use_live_bybit_withdrawal_fee=True,
        )


def test_target_replay_uses_idempotent_path(
    monkeypatch: Any,
) -> None:
    batch = Row(
        id=701,
        fund_id=7,
        status=(
            BATCH_STATUS_NEGATIVE_NET_TARGETS_CALCULATED
        ),
    )
    expected = SimpleNamespace(ok=True)

    monkeypatch.setattr(
        negative_targets,
        "_lock_settlement_batch",
        lambda *args, **kwargs: batch,
    )
    monkeypatch.setattr(
        negative_targets,
        "_get_fund",
        lambda *args, **kwargs: Row(
            id=7,
            code="wb_test",
        ),
    )
    monkeypatch.setattr(
        negative_targets,
        "_build_idempotent_target_result",
        lambda *args, **kwargs: expected,
    )

    result = (
        negative_targets
        .calculate_and_store_negative_net_targets(
            ModelSession({}),
            settlement_batch_id=batch.id,
            bybit_withdrawal_fee_usdt=(
                Decimal("0.2")
            ),
            use_live_bybit_withdrawal_fee=False,
        )
    )

    assert result is expected


def test_downstream_status_is_not_mutated(
    monkeypatch: Any,
) -> None:
    batch = Row(
        id=702,
        fund_id=7,
        status=(
            BATCH_STATUS_NEGATIVE_NET_SALE_PLANNED
        ),
    )

    monkeypatch.setattr(
        negative_targets,
        "_lock_settlement_batch",
        lambda *args, **kwargs: batch,
    )
    monkeypatch.setattr(
        negative_targets,
        "_get_fund",
        lambda *args, **kwargs: Row(
            id=7,
            code="wb_test",
        ),
    )

    with pytest.raises(
        NegativeNetTargetError,
    ):
        negative_targets.calculate_and_store_negative_net_targets(
            ModelSession({}),
            settlement_batch_id=batch.id,
            bybit_withdrawal_fee_usdt=(
                Decimal("0.2")
            ),
            use_live_bybit_withdrawal_fee=False,
        )

    assert (
        batch.status
        == BATCH_STATUS_NEGATIVE_NET_SALE_PLANNED
    )


def test_worker_has_only_live_read_only_mode(
    monkeypatch: Any,
) -> None:
    parser = targets_worker._build_parser()
    option_strings = {
        option
        for action in parser._actions
        for option in action.option_strings
    }

    assert "--live-read-only" in option_strings
    assert "--live-execution" not in option_strings
    assert (
        "--static-bybit-withdrawal-fee-usdt"
        not in option_strings
    )

    monkeypatch.setattr(
        targets_worker.settings,
        "LIFECYCLE_WORKERS_PRODUCTION_LIVE_ENABLED",
        False,
    )
    monkeypatch.setattr(
        targets_worker.settings,
        "NEGATIVE_NET_TARGETS_ALLOW_LIVE_FEE",
        False,
    )
    monkeypatch.setattr(
        targets_worker,
        "_build_master_bybit_client",
        lambda: (_ for _ in ()).throw(
            AssertionError(
                "Blocked gate must not build client"
            )
        ),
    )

    args = parser.parse_args(
        [
            "--run-once",
            "--live-read-only",
        ]
    )

    assert (
        targets_worker
        ._validate_stage23_1_args(args)
        is None
    )


def test_row_locks_and_no_commit_contract() -> None:
    redeem_source = inspect.getsource(
        release_redeem_reserve_if_safe
    )
    classifier_source = inspect.getsource(
        inspect_negative_external_state
    )
    failure_source = inspect.getsource(
        failure_service
        .fail_negative_batch_pre_external
    )

    assert (
        redeem_source.count(
            ".with_for_update("
        )
        == 2
    )
    assert (
        classifier_source.count(
            ".with_for_update("
        )
        == 9
    )
    assert ".commit(" not in redeem_source
    assert ".commit(" not in failure_source