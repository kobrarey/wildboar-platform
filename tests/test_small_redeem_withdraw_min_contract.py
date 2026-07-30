from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.config import settings
import app.navcalc.collector as nav_collector
import app.navcalc.portfolio_nav as portfolio_nav
import app.settlement.negative_bybit_flow as bybit_flow
import app.settlement.negative_bybit_flow_live_service as bybit_live_service
import app.settlement.negative_finalization as negative_finalization
import app.settlement.negative_net_targets as target_service
import app.settlement.negative_payout_flow as payout_flow
from app.settlement.negative_net_fees import (
    BYBIT_WITHDRAWAL_FEE_TYPE,
    MonthOpenPriceResult,
    NegativeNetFeeError,
    RedeemOrderFeeResult,
    calculate_negative_net_batch_targets,
    calculate_negative_net_withdrawal_amount,
)
from app.settlement.share_quantity import (
    MIN_REDEEM_SHARES,
    RedeemSharePrecisionError,
    ShareQuantityError,
    validate_redeem_share_input_precision,
)
from app.trading.order_service import (
    TradingOrderError,
    validate_buy_amount_limits,
    validate_redeem_shares_limits,
)


NOW = datetime(
    2026,
    7,
    29,
    8,
    0,
    tzinfo=timezone.utc,
)


def make_month_open() -> MonthOpenPriceResult:
    return MonthOpenPriceResult(
        fund_id=9,
        settlement_ts=NOW,
        month_start=NOW.replace(day=1),
        price_usdt=Decimal("600"),
        source="test",
        chart_daily_id=1,
        chart_ts_utc=NOW,
    )


def make_fee_result(
    *,
    net_payout: Decimal,
    partial_fee: Decimal = Decimal("0"),
) -> RedeemOrderFeeResult:
    return RedeemOrderFeeResult(
        gross_redeem_usdt=(
            net_payout + partial_fee
        ),
        success_fee_usdt=partial_fee,
        management_fee_usdt=Decimal("0"),
        partial_month_fee_usdt=partial_fee,
        net_user_payout_usdt=net_payout,
        net_price_usdt=Decimal("1"),
        fee_calc_month_open_price_usdt=(
            Decimal("600")
        ),
        fee_calc_days_in_month_period=1,
        success_fee_rate=Decimal("0"),
        management_fee_rate=Decimal("0"),
        total_fee_usdt=partial_fee,
        diagnostics={},
    )


def test_minimum_redeem_share_contract() -> None:
    assert MIN_REDEEM_SHARES == Decimal(
        "0.0001"
    )

    assert (
        validate_redeem_share_input_precision(
            "0.0001"
        )
        == Decimal("0.0001")
    )

    assert (
        validate_redeem_share_input_precision(
            "0.0002"
        )
        == Decimal("0.0002")
    )

    validate_redeem_shares_limits(
        Decimal("0.0001")
    )


@pytest.mark.parametrize(
    "raw_value",
    [
        "0",
        "-0.0001",
    ],
)
def test_non_positive_redeem_rejected(
    raw_value: str,
) -> None:
    with pytest.raises(
        ShareQuantityError
    ):
        validate_redeem_share_input_precision(
            raw_value
        )


def test_redeem_precision_above_4dp_rejected(
) -> None:
    with pytest.raises(
        RedeemSharePrecisionError
    ):
        validate_redeem_share_input_precision(
            "0.00001"
        )


def test_direct_redeem_limit_rejects_below_min(
) -> None:
    with pytest.raises(
        TradingOrderError
    ) as exc_info:
        validate_redeem_shares_limits(
            Decimal("0")
        )

    assert (
        exc_info.value.error_key
        == "redeem_shares_below_minimum"
    )


def test_buy_minimum_contract_unchanged() -> None:
    buy_min = Decimal(
        str(settings.TRADING_BUY_MIN_USDT)
    )

    validate_buy_amount_limits(
        buy_min
    )

    with pytest.raises(
        TradingOrderError
    ) as exc_info:
        validate_buy_amount_limits(
            buy_min - Decimal("0.01")
        )

    assert (
        exc_info.value.error_key
        == "buy_amount_below_min"
    )


def test_small_redeem_has_no_usdt_entry_minimum(
) -> None:
    shares = (
        validate_redeem_share_input_precision(
            "0.0001"
        )
    )

    validate_redeem_shares_limits(
        shares
    )

    assert shares == Decimal("0.0001")


def test_withdraw_min_is_technical_floor() -> None:
    targets = (
        calculate_negative_net_batch_targets(
            order_fee_results=[
                make_fee_result(
                    net_payout=Decimal("9.96"),
                    partial_fee=Decimal("0.10"),
                )
            ],
            bybit_withdrawal_fee_usdt=(
                Decimal("0.20")
            ),
            month_open_result=make_month_open(),
            withdraw_min_usdt=Decimal("10"),
            withdraw_max_usdt=Decimal("1000"),
            min_accuracy=4,
            fee_type=0,
        )
    )

    assert (
        targets.total_net_user_payout_usdt
        == Decimal("9.96")
    )

    assert str(
        targets.withdrawal_request_amount_usdt
    ) == "10.0000"

    assert (
        targets.settlement_wallet_residual_usdt
        == Decimal("0.04")
    )

    assert (
        targets.required_master_usdt
        == Decimal("10.30")
    )


@pytest.mark.parametrize(
    (
        "payout",
        "expected_withdrawal",
        "expected_residual",
    ),
    [
        (
            Decimal("10.00"),
            Decimal("10.0000"),
            Decimal("0.0000"),
        ),
        (
            Decimal("15.00"),
            Decimal("15.0000"),
            Decimal("0.0000"),
        ),
    ],
)
def test_withdrawal_amount_without_residual(
    payout: Decimal,
    expected_withdrawal: Decimal,
    expected_residual: Decimal,
) -> None:
    result = (
        calculate_negative_net_withdrawal_amount(
            total_net_user_payout_usdt=(
                payout
            ),
            withdraw_min_usdt=Decimal("10"),
            withdraw_max_usdt=Decimal("1000"),
            min_accuracy=4,
            fee_type=0,
        )
    )

    assert (
        result.withdrawal_request_amount_usdt
        == expected_withdrawal
    )

    assert (
        result.settlement_wallet_residual_usdt
        == expected_residual
    )


def test_min_accuracy_rounds_up() -> None:
    result = (
        calculate_negative_net_withdrawal_amount(
            total_net_user_payout_usdt=(
                Decimal("9.96")
            ),
            withdraw_min_usdt=(
                Decimal("10.00001")
            ),
            withdraw_max_usdt=(
                Decimal("1000")
            ),
            min_accuracy=4,
            fee_type=0,
        )
    )

    assert str(
        result.withdrawal_request_amount_usdt
    ) == "10.0001"

    assert (
        result.withdrawal_request_amount_usdt
        >= result.total_net_user_payout_usdt
    )

    assert (
        result.withdrawal_request_amount_usdt
        >= Decimal("10.00001")
    )


def test_withdraw_max_fails_closed() -> None:
    with pytest.raises(
        NegativeNetFeeError,
        match="above withdrawMax",
    ):
        calculate_negative_net_withdrawal_amount(
            total_net_user_payout_usdt=(
                Decimal("15")
            ),
            withdraw_min_usdt=Decimal("10"),
            withdraw_max_usdt=Decimal("14"),
            min_accuracy=4,
            fee_type=0,
        )


def test_fee_type_one_is_rejected() -> None:
    with pytest.raises(
        NegativeNetFeeError,
        match="feeType=0",
    ):
        calculate_negative_net_withdrawal_amount(
            total_net_user_payout_usdt=(
                Decimal("9.96")
            ),
            withdraw_min_usdt=Decimal("10"),
            withdraw_max_usdt=Decimal("1000"),
            min_accuracy=4,
            fee_type=1,
        )


def test_mock_policy_keeps_zero_residual(
) -> None:
    result = (
        calculate_negative_net_withdrawal_amount(
            total_net_user_payout_usdt=(
                Decimal("9.96")
            ),
            fee_type=(
                BYBIT_WITHDRAWAL_FEE_TYPE
            ),
        )
    )

    assert (
        result.withdrawal_request_amount_usdt
        == Decimal("9.96")
    )

    assert (
        result.settlement_wallet_residual_usdt
        == Decimal("0")
    )

def make_persisted_target_batch(
    *,
    withdraw_min: str | None = "10",
    withdraw_max: str | None = "1000",
    min_accuracy: int | None = 4,
    withdrawal: str = "10.0000",
    residual: str = "0.0400",
) -> SimpleNamespace:
    return SimpleNamespace(
        negative_net_target_diagnostics_json={
            "withdrawal_amount_policy": {
                "schema": (
                    target_service
                    .WITHDRAWAL_AMOUNT_SNAPSHOT_SCHEMA
                ),
                "policy_version": (
                    target_service
                    .NEGATIVE_NET_WITHDRAWAL_AMOUNT_POLICY_VERSION
                ),
                "mode": "bybit_live_readonly",
                "coin": "USDT",
                "chain": "BSC",
                "feeType": 0,
                "withdrawFee": "0.20",
                "withdrawPercentageFee": "0",
                "withdrawMin": withdraw_min,
                "withdrawMax": withdraw_max,
                "minAccuracy": min_accuracy,
                "chainWithdraw": "1",
                "total_net_user_payout_usdt": (
                    "9.96"
                ),
                "withdrawal_request_amount_usdt": (
                    withdrawal
                ),
                "settlement_wallet_residual_usdt": (
                    residual
                ),
                "required_master_usdt": (
                    "10.30"
                ),
                "total_partial_month_fee_usdt": (
                    "0.10"
                ),
            }
        }
    )


def test_idempotent_contract_accepts_residual(
) -> None:
    batch = make_persisted_target_batch()

    residual, snapshot = (
        target_service
        ._validate_persisted_withdrawal_contract(
            batch=batch,
            stored_total_net=Decimal("9.96"),
            stored_bybit_fee=Decimal("0.20"),
            stored_partial_fee=Decimal("0.10"),
            stored_required_master=Decimal(
                "10.30"
            ),
            stored_withdrawal=Decimal(
                "10.0000"
            ),
        )
    )

    assert residual == Decimal("0.0400")
    assert snapshot["feeType"] == 0


def test_idempotent_contract_recomputes_from_saved_policy(
) -> None:
    batch = make_persisted_target_batch(
        withdraw_min="10.00001",
        withdrawal="10.0001",
        residual="0.0401",
    )

    batch.negative_net_target_diagnostics_json[
        "withdrawal_amount_policy"
    ]["required_master_usdt"] = "10.3001"

    residual, _ = (
        target_service
        ._validate_persisted_withdrawal_contract(
            batch=batch,
            stored_total_net=Decimal("9.96"),
            stored_bybit_fee=Decimal("0.20"),
            stored_partial_fee=Decimal("0.10"),
            stored_required_master=Decimal(
                "10.3001"
            ),
            stored_withdrawal=Decimal(
                "10.0001"
            ),
        )
    )

    assert residual == Decimal("0.0401")


def test_idempotent_contract_rejects_changed_snapshot(
) -> None:
    batch = make_persisted_target_batch(
        withdraw_min="11",
    )

    with pytest.raises(
        target_service.NegativeNetTargetError,
        match=(
            "does not match stored Bybit "
            "policy snapshot"
        ),
    ):
        (
            target_service
            ._validate_persisted_withdrawal_contract(
                batch=batch,
                stored_total_net=Decimal(
                    "9.96"
                ),
                stored_bybit_fee=Decimal(
                    "0.20"
                ),
                stored_partial_fee=Decimal(
                    "0.10"
                ),
                stored_required_master=Decimal(
                    "10.30"
                ),
                stored_withdrawal=Decimal(
                    "10.0000"
                ),
            )
        )


def test_idempotent_contract_rejects_fee_type_one(
) -> None:
    batch = make_persisted_target_batch()

    batch.negative_net_target_diagnostics_json[
        "withdrawal_amount_policy"
    ]["feeType"] = 1

    with pytest.raises(
        target_service.NegativeNetTargetError,
        match="feeType=0",
    ):
        (
            target_service
            ._validate_persisted_withdrawal_contract(
                batch=batch,
                stored_total_net=Decimal(
                    "9.96"
                ),
                stored_bybit_fee=Decimal(
                    "0.20"
                ),
                stored_partial_fee=Decimal(
                    "0.10"
                ),
                stored_required_master=Decimal(
                    "10.30"
                ),
                stored_withdrawal=Decimal(
                    "10.0000"
                ),
            )
        )


def test_idempotent_contract_rejects_old_required_master_formula(
) -> None:
    batch = make_persisted_target_batch()

    with pytest.raises(
        target_service.NegativeNetTargetError,
        match=(
            "snapshot required master mismatch"
        ),
    ):
        (
            target_service
            ._validate_persisted_withdrawal_contract(
                batch=batch,
                stored_total_net=Decimal(
                    "9.96"
                ),
                stored_bybit_fee=Decimal(
                    "0.20"
                ),
                stored_partial_fee=Decimal(
                    "0.10"
                ),
                stored_required_master=Decimal(
                    "10.26"
                ),
                stored_withdrawal=Decimal(
                    "10.0000"
                ),
            )
        )


def test_missing_fee_type_fails_closed() -> None:
    with pytest.raises(
        NegativeNetFeeError,
        match="feeType is required",
    ):
        calculate_negative_net_withdrawal_amount(
            total_net_user_payout_usdt=(
                Decimal("9.96")
            ),
            withdraw_min_usdt=Decimal("10"),
            withdraw_max_usdt=Decimal("1000"),
            min_accuracy=4,
            fee_type=None,
        )


def test_idempotent_contract_rejects_missing_fee_type(
) -> None:
    batch = make_persisted_target_batch()

    del batch.negative_net_target_diagnostics_json[
        "withdrawal_amount_policy"
    ]["feeType"]

    with pytest.raises(
        target_service.NegativeNetTargetError,
        match="missing feeType",
    ):
        (
            target_service
            ._validate_persisted_withdrawal_contract(
                batch=batch,
                stored_total_net=Decimal(
                    "9.96"
                ),
                stored_bybit_fee=Decimal(
                    "0.20"
                ),
                stored_partial_fee=Decimal(
                    "0.10"
                ),
                stored_required_master=Decimal(
                    "10.30"
                ),
                stored_withdrawal=Decimal(
                    "10.0000"
                ),
            )
        )

def make_cash_delivery_rows(
    *,
    total_net: Decimal = Decimal("9.96"),
    withdrawal: Decimal = Decimal("10.00"),
    bybit_fee: Decimal = Decimal("0.20"),
    partial_fee: Decimal = Decimal("0.10"),
    required_master: Decimal = Decimal(
        "10.30"
    ),
) -> tuple[
    SimpleNamespace,
    SimpleNamespace,
]:
    settlement_batch = SimpleNamespace(
        required_master_usdt=required_master,
        withdrawal_request_amount_usdt=(
            withdrawal
        ),
        bybit_withdrawal_fee_usdt=bybit_fee,
        total_net_user_payout_usdt=(
            total_net
        ),
        total_partial_month_fee_usdt=(
            partial_fee
        ),
    )

    sale_batch = SimpleNamespace(
        required_master_usdt=required_master,
        withdrawal_request_amount_usdt=(
            withdrawal
        ),
        bybit_withdrawal_fee_usdt=bybit_fee,
        total_net_user_payout_usdt=(
            total_net
        ),
        total_partial_month_fee_usdt=(
            partial_fee
        ),
    )

    return settlement_batch, sale_batch


def test_cash_delivery_accepts_residual_contract(
) -> None:
    settlement_batch, sale_batch = (
        make_cash_delivery_rows()
    )

    amounts = bybit_flow._validate_target_fields(
        settlement_batch=settlement_batch,
        sale_batch=sale_batch,
    )

    assert (
        amounts[
            "total_net_user_payout_usdt"
        ]
        == Decimal("9.96")
    )

    assert (
        amounts[
            "withdrawal_request_amount_usdt"
        ]
        == Decimal("10.00")
    )

    assert (
        amounts[
            "settlement_wallet_residual_usdt"
        ]
        == Decimal("0.04")
    )

    assert (
        amounts["required_master_usdt"]
        == Decimal("10.30")
    )


def test_cash_delivery_rejects_old_required_master_formula(
) -> None:
    settlement_batch, sale_batch = (
        make_cash_delivery_rows(
            required_master=Decimal("10.26")
        )
    )

    with pytest.raises(
        bybit_flow.NegativeBybitFlowError,
        match=(
            "required_master_usdt formula "
            "mismatch"
        ),
    ):
        bybit_flow._validate_target_fields(
            settlement_batch=(
                settlement_batch
            ),
            sale_batch=sale_batch,
        )


def test_cash_delivery_rejects_withdrawal_below_user_payout(
) -> None:
    settlement_batch, sale_batch = (
        make_cash_delivery_rows(
            total_net=Decimal("9.96"),
            withdrawal=Decimal("9.95"),
            required_master=Decimal("10.25"),
        )
    )

    with pytest.raises(
        bybit_flow.NegativeBybitFlowError,
        match=(
            "must be >= "
            "total_net_user_payout_usdt"
        ),
    ):
        bybit_flow._validate_target_fields(
            settlement_batch=(
                settlement_batch
            ),
            sale_batch=sale_batch,
        )


def test_universal_transfer_uses_required_master(
) -> None:
    amount_text, amount_actual = (
        bybit_flow
        .universal_transfer_actual_amount(
            required_master_usdt=Decimal(
                "10.30"
            ),
            precision=4,
        )
    )

    assert amount_text == "10.3"
    assert amount_actual == Decimal("10.3")
    assert amount_actual >= Decimal("10.30")


def test_withdrawal_uses_technical_request_amount(
) -> None:
    amount_text, amount_actual = (
        bybit_flow.withdrawal_actual_amount(
            withdrawal_request_amount_usdt=(
                Decimal("10.0000")
            ),
            precision=4,
        )
    )

    assert amount_text == "10"
    assert amount_actual == Decimal("10")
    assert amount_actual != Decimal("9.96")


def test_settlement_receipt_expects_withdrawal_amount(
) -> None:
    address = "0x" + ("1" * 40)
    tx_hash = "0x" + ("2" * 64)

    receipt = (
        bybit_flow
        ._validate_settlement_wallet_receipt(
            raw={
                "status": "CONFIRMED",
                "address": address,
                "received_amount_usdt": (
                    "10.00"
                ),
                "tx_hash": tx_hash,
            },
            expected_address=address,
            expected_received_amount_usdt=(
                Decimal("10.00")
            ),
            expected_tx_hash=tx_hash,
        )
    )

    assert (
        Decimal(str(receipt["received_usdt"]))
        == Decimal("10.00")
    )


def test_durable_withdrawal_intent_uses_fee_type_zero(
) -> None:
    intent = (
        bybit_live_service
        ._build_withdrawal_intent(
            settlement_batch_id=182,
            fund_id=9,
            request_id=(
                "wbng"
                "1111111111111111111111111111"
            ),
            coin="USDT",
            chain="BSC",
            address=(
                "0x" + ("1" * 40)
            ),
            amount="10",
            fee_usdt=Decimal("0.20"),
            amount_precision=4,
            fee_snapshot={
                "schema": (
                    "negative_withdrawal_"
                    "fee_snapshot_v1"
                ),
            },
            balance_baseline={
                "address": (
                    "0x" + ("1" * 40)
                ),
            },
            prepared_at=NOW,
        )
    )

    assert intent["amount"] == "10"
    assert intent["fee_type"] == 0
    assert (
        intent["payload_template"]["amount"]
        == "10"
    )
    assert (
        intent["payload_template"]["feeType"]
        == 0
    )
    assert intent["payload_fingerprint"]


def test_durable_withdrawal_intent_rejects_fee_type_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        bybit_live_service.settings,
        "NEGATIVE_NET_WITHDRAWAL_FEE_TYPE",
        1,
    )

    with pytest.raises(
        bybit_flow.NegativeBybitFlowError,
        match="requires feeType=0",
    ):
        (
            bybit_live_service
            ._build_withdrawal_intent(
                settlement_batch_id=182,
                fund_id=9,
                request_id=(
                    "wbng"
                    "2222222222222222222222222222"
                ),
                coin="USDT",
                chain="BSC",
                address=(
                    "0x" + ("2" * 40)
                ),
                amount="10",
                fee_usdt=Decimal("0.20"),
                amount_precision=4,
                fee_snapshot={},
                balance_baseline={},
                prepared_at=NOW,
            )
        )

def make_residual_payout_inputs(
    *,
    total_net: Decimal = Decimal("9.96"),
    withdrawal: Decimal = Decimal("10.00"),
    flow_withdrawal: Decimal = Decimal(
        "10.00"
    ),
    received: Decimal = Decimal("10.00"),
) -> tuple[
    SimpleNamespace,
    SimpleNamespace,
]:
    settlement_batch = SimpleNamespace(
        status=(
            payout_flow
            .BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT
        ),
        total_net_user_payout_usdt=(
            total_net
        ),
        withdrawal_request_amount_usdt=(
            withdrawal
        ),
    )

    bybit_flow_row = SimpleNamespace(
        status=(
            payout_flow
            .BYBIT_FLOW_STATUS_COMPLETED
        ),
        settlement_wallet_receipt_status=(
            "CONFIRMED"
        ),
        withdrawal_tx_hash=(
            "0x" + ("3" * 64)
        ),
        withdrawal_request_amount_usdt=(
            flow_withdrawal
        ),
        settlement_wallet_received_usdt=(
            received
        ),
    )

    return settlement_batch, bybit_flow_row


def test_payout_contract_accepts_settlement_residual(
) -> None:
    settlement_batch, bybit_flow_row = (
        make_residual_payout_inputs()
    )

    amounts = (
        payout_flow._validate_bybit_flow_input(
            settlement_batch=(
                settlement_batch
            ),
            bybit_flow=bybit_flow_row,
        )
    )

    assert (
        amounts["expected_total_payout_usdt"]
        == Decimal("9.96")
    )

    assert (
        amounts[
            "withdrawal_request_amount_usdt"
        ]
        == Decimal("10.00")
    )

    assert (
        amounts[
            "settlement_wallet_received_usdt"
        ]
        == Decimal("10.00")
    )

    assert (
        amounts[
            "settlement_wallet_residual_usdt"
        ]
        == Decimal("0.04")
    )


def test_payout_contract_does_not_pay_residual_to_user(
) -> None:
    settlement_batch, bybit_flow_row = (
        make_residual_payout_inputs()
    )

    amounts = (
        payout_flow._validate_bybit_flow_input(
            settlement_batch=(
                settlement_batch
            ),
            bybit_flow=bybit_flow_row,
        )
    )

    payout_leg_total = amounts[
        "expected_total_payout_usdt"
    ]

    assert payout_leg_total == Decimal("9.96")
    assert payout_leg_total != Decimal("10.00")


def test_payout_contract_rejects_withdrawal_below_user_payout(
) -> None:
    settlement_batch, bybit_flow_row = (
        make_residual_payout_inputs(
            total_net=Decimal("9.96"),
            withdrawal=Decimal("9.95"),
            flow_withdrawal=Decimal("9.95"),
            received=Decimal("9.95"),
        )
    )

    with pytest.raises(
        payout_flow.NegativePayoutFlowError,
        match=(
            "must be >= total net user payout"
        ),
    ):
        payout_flow._validate_bybit_flow_input(
            settlement_batch=(
                settlement_batch
            ),
            bybit_flow=bybit_flow_row,
        )


def test_payout_contract_rejects_flow_withdrawal_mismatch(
) -> None:
    settlement_batch, bybit_flow_row = (
        make_residual_payout_inputs(
            flow_withdrawal=Decimal("9.96"),
        )
    )

    with pytest.raises(
        payout_flow.NegativePayoutFlowError,
        match=(
            "Bybit flow withdrawal amount"
        ),
    ):
        payout_flow._validate_bybit_flow_input(
            settlement_batch=(
                settlement_batch
            ),
            bybit_flow=bybit_flow_row,
        )


def test_payout_contract_rejects_receipt_mismatch(
) -> None:
    settlement_batch, bybit_flow_row = (
        make_residual_payout_inputs(
            received=Decimal("9.96"),
        )
    )

    with pytest.raises(
        payout_flow.NegativePayoutFlowError,
        match=(
            "Settlement wallet received amount"
        ),
    ):
        payout_flow._validate_bybit_flow_input(
            settlement_batch=(
                settlement_batch
            ),
            bybit_flow=bybit_flow_row,
        )


def make_finalization_residual_rows(
    *,
    payout_after: Decimal = Decimal("0.04"),
    unrelated_raw: int = 0,
) -> tuple[
    SimpleNamespace,
    SimpleNamespace,
    SimpleNamespace,
]:
    settlement_batch = SimpleNamespace(
        total_net_user_payout_usdt=(
            Decimal("9.96")
        ),
        withdrawal_request_amount_usdt=(
            Decimal("10.00")
        ),
    )

    unrelated_usdt = (
        Decimal(unrelated_raw)
        / Decimal(10**18)
    )

    bybit_flow_row = SimpleNamespace(
        settlement_wallet_received_usdt=(
            Decimal("10.00")
        ),
        settlement_wallet_balance_before_usdt=(
            Decimal("0")
        ),
        settlement_wallet_balance_after_usdt=(
            Decimal("10.00")
            + unrelated_usdt
        ),
        settlement_wallet_receipt_json={
            "unrelated_additional_incoming_raw": (
                unrelated_raw
            )
        },
    )

    payout_batch = SimpleNamespace(
        settlement_wallet_usdt_before=(
            Decimal("10.00")
            + unrelated_usdt
        ),
        settlement_wallet_usdt_after=(
            payout_after
        ),
    )

    return (
        settlement_batch,
        bybit_flow_row,
        payout_batch,
    )


def test_finalization_accepts_exact_fund_residual(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        negative_finalization.settings,
        "BSC_USDT_DECIMALS",
        18,
    )

    (
        settlement_batch,
        bybit_flow_row,
        payout_batch,
    ) = make_finalization_residual_rows()

    evidence = (
        negative_finalization
        ._validate_settlement_wallet_residual(
            settlement_batch=(
                settlement_batch
            ),
            bybit_flow=bybit_flow_row,
            payout_batch=payout_batch,
        )
    )

    assert (
        evidence["expected_residual_usdt"]
        == "0.04"
    )
    assert (
        evidence[
            "actual_attributable_residual_usdt"
        ]
        == "0.04"
    )
    assert evidence["residual_owner"] == "fund"
    assert (
        evidence["residual_is_user_payout"]
        is False
    )


def test_finalization_residual_allows_proven_unrelated_incoming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        negative_finalization.settings,
        "BSC_USDT_DECIMALS",
        18,
    )

    unrelated_raw = 10**16

    (
        settlement_batch,
        bybit_flow_row,
        payout_batch,
    ) = make_finalization_residual_rows(
        payout_after=Decimal("0.05"),
        unrelated_raw=unrelated_raw,
    )

    evidence = (
        negative_finalization
        ._validate_settlement_wallet_residual(
            settlement_batch=(
                settlement_batch
            ),
            bybit_flow=bybit_flow_row,
            payout_batch=payout_batch,
        )
    )

    assert (
        evidence["expected_residual_usdt"]
        == "0.04"
    )
    assert (
        evidence[
            "unrelated_additional_incoming_usdt"
        ]
        == "0.01"
    )
    assert (
        evidence[
            "actual_attributable_residual_usdt"
        ]
        == "0.04"
    )


def test_finalization_rejects_residual_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        negative_finalization.settings,
        "BSC_USDT_DECIMALS",
        18,
    )

    (
        settlement_batch,
        bybit_flow_row,
        payout_batch,
    ) = make_finalization_residual_rows(
        payout_after=Decimal("0.03")
    )

    with pytest.raises(
        negative_finalization
        .NegativeFinalizationError,
        match=(
            "USDT debit does not match total "
            "user payout"
        ),
    ):
        (
            negative_finalization
            ._validate_settlement_wallet_residual(
                settlement_batch=(
                    settlement_batch
                ),
                bybit_flow=bybit_flow_row,
                payout_batch=payout_batch,
            )
        )


def test_nav_selects_only_active_bsc_settlement_wallet(
) -> None:
    rows = [
        SimpleNamespace(
            id=1,
            blockchain="BSC",
            wallet_type="settlement",
            is_active=True,
            address="0x" + ("1" * 40),
        ),
        SimpleNamespace(
            id=2,
            blockchain="BSC",
            wallet_type="settlement",
            is_active=False,
            address="0x" + ("2" * 40),
        ),
        SimpleNamespace(
            id=3,
            blockchain="BSC",
            wallet_type="treasury",
            is_active=True,
            address="0x" + ("3" * 40),
        ),
        SimpleNamespace(
            id=4,
            blockchain="ETH",
            wallet_type="settlement",
            is_active=True,
            address="0x" + ("4" * 40),
        ),
    ]

    selected = (
        nav_collector
        ._select_active_settlement_wallet(
            rows
        )
    )

    assert selected is not None
    assert selected.id == 1


def test_nav_rejects_multiple_active_settlement_wallets(
) -> None:
    rows = [
        SimpleNamespace(
            id=1,
            blockchain="BSC",
            wallet_type="settlement",
            is_active=True,
        ),
        SimpleNamespace(
            id=2,
            blockchain="BSC",
            wallet_type="settlement",
            is_active=True,
        ),
    ]

    with pytest.raises(
        nav_collector.NavConfigError,
        match=(
            "more than one active "
            "BSC settlement wallet"
        ),
    ):
        (
            nav_collector
            ._select_active_settlement_wallet(
                rows
            )
        )


def test_nav_counts_settlement_residual_exactly_once(
) -> None:
    (
        nav_usd,
        fund_cash_wallets_usd,
    ) = portfolio_nav._compose_nav_usd(
        cash_usd=Decimal("100"),
        spot_usd=Decimal("20"),
        bybit_funding_wallet_usd=Decimal(
            "5"
        ),
        settlement_wallet_usdt=Decimal(
            "0.04"
        ),
        earn_usd=Decimal("10"),
    )

    assert (
        fund_cash_wallets_usd
        == Decimal("5.04")
    )
    assert nav_usd == Decimal("135.04")

    nav_without_settlement, _ = (
        portfolio_nav._compose_nav_usd(
            cash_usd=Decimal("100"),
            spot_usd=Decimal("20"),
            bybit_funding_wallet_usd=Decimal(
                "5"
            ),
            settlement_wallet_usdt=Decimal(
                "0"
            ),
            earn_usd=Decimal("10"),
        )
    )

    assert (
        nav_usd - nav_without_settlement
        == Decimal("0.04")
    )


def test_nav_rejects_negative_settlement_wallet_balance(
) -> None:
    with pytest.raises(
        portfolio_nav.NavSanityCheckError,
        match="cannot be negative",
    ):
        portfolio_nav._compose_nav_usd(
            cash_usd=Decimal("100"),
            spot_usd=Decimal("20"),
            bybit_funding_wallet_usd=Decimal(
                "5"
            ),
            settlement_wallet_usdt=Decimal(
                "-0.01"
            ),
            earn_usd=Decimal("10"),
        )
