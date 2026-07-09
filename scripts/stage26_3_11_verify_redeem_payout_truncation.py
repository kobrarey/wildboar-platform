from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

from app.settlement.negative_bybit_flow import withdrawal_actual_amount
from app.settlement.negative_net_fees import (
    PAYOUT_TRUNCATION_POLICY,
    MonthOpenPriceResult,
    RedeemOrderFeeResult,
    calculate_negative_net_batch_targets,
    calculate_redeem_order_fees,
    truncate_usdt_to_cents_down,
)


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def settlement_ts() -> datetime:
    return datetime(2026, 7, 15, tzinfo=timezone.utc)


def month_open_result() -> MonthOpenPriceResult:
    return MonthOpenPriceResult(
        fund_id=1,
        settlement_ts=settlement_ts(),
        month_start=datetime(2026, 7, 1, tzinfo=timezone.utc),
        price_usdt=Decimal("600"),
        source="test",
        chart_daily_id=None,
        chart_ts_utc=None,
    )


def fake_fee_result(
    *,
    gross: Decimal,
    raw_net: Decimal,
) -> RedeemOrderFeeResult:
    net = truncate_usdt_to_cents_down(raw_net)
    fee = gross - raw_net
    return RedeemOrderFeeResult(
        gross_redeem_usdt=gross,
        success_fee_usdt=Decimal("0"),
        management_fee_usdt=fee,
        partial_month_fee_usdt=fee,
        net_user_payout_usdt=net,
        net_price_usdt=raw_net,
        fee_calc_month_open_price_usdt=Decimal("600"),
        fee_calc_days_in_month_period=15,
        success_fee_rate=Decimal("0"),
        management_fee_rate=Decimal("0"),
        total_fee_usdt=fee,
        diagnostics={
            "raw_net_user_payout_usdt": raw_net,
            "payout_truncation_policy": PAYOUT_TRUNCATION_POLICY,
            "payout_truncation_remainder_usdt": raw_net - net,
            "net_user_payout_usdt": net,
        },
    )


def test_redeem_payout_truncates_to_2dp() -> None:
    gross = Decimal("622") * Decimal("0.0127")

    assert_ok("GROSS_EXAMPLE_OK", gross == Decimal("7.8994"))
    assert_ok(
        "EXAMPLE_TRUNCATES_TO_789",
        truncate_usdt_to_cents_down(gross) == Decimal("7.89"),
    )

    cases = {
        "7.8900": Decimal("7.89"),
        "7.899999": Decimal("7.89"),
        "7.891": Decimal("7.89"),
        "7.999": Decimal("7.99"),
    }

    for raw, expected in cases.items():
        assert_ok(
            f"TRUNCATE_{raw}_TO_{expected}",
            truncate_usdt_to_cents_down(Decimal(raw)) == expected,
        )

    print("STAGE26_3_11_REDEEM_PAYOUT_TRUNCATES_TO_2DP_OK")


def test_no_round_up() -> None:
    assert_ok(
        "DOES_NOT_ROUND_78994_TO_790",
        truncate_usdt_to_cents_down(Decimal("7.8994")) != Decimal("7.90"),
    )
    assert_ok(
        "DOES_NOT_ROUND_7899999_TO_790",
        truncate_usdt_to_cents_down(Decimal("7.899999")) != Decimal("7.90"),
    )
    assert_ok(
        "DOES_NOT_ROUND_7891_TO_790",
        truncate_usdt_to_cents_down(Decimal("7.891")) != Decimal("7.90"),
    )

    print("STAGE26_3_11_REDEEM_PAYOUT_NO_ROUND_UP_OK")


def test_fees_before_truncation() -> None:
    result = calculate_redeem_order_fees(
        fund_code="wb_test",
        settlement_price_usdt=Decimal("622"),
        redeem_shares=Decimal("0.0127"),
        month_open_price_usdt=Decimal("600"),
        settlement_ts=settlement_ts(),
    )

    expected_gross = Decimal("622") * Decimal("0.0127")
    expected_raw_net = expected_gross - result.total_fee_usdt
    expected_net = truncate_usdt_to_cents_down(expected_raw_net)

    assert_ok("FEES_GROSS_EXACT", result.gross_redeem_usdt == expected_gross)
    assert_ok(
        "FEES_RAW_NET_DIAGNOSTIC",
        Decimal(result.diagnostics["raw_net_user_payout_usdt"]) == expected_raw_net,
    )
    assert_ok("FEES_TRUNCATE_AFTER_FEES", result.net_user_payout_usdt == expected_net)
    assert_ok(
        "FEES_REMAINDER_DIAGNOSTIC",
        Decimal(result.diagnostics["payout_truncation_remainder_usdt"])
        == expected_raw_net - expected_net,
    )
    assert_ok(
        "FEES_POLICY_DIAGNOSTIC",
        result.diagnostics["payout_truncation_policy"]
        == "floor_to_2_decimals_after_fees",
    )

    print("STAGE26_3_11_FEES_BEFORE_TRUNCATION_OK")


def test_batch_total_sums_truncated_order_payouts() -> None:
    order_1 = fake_fee_result(
        gross=Decimal("7.8994"),
        raw_net=Decimal("7.8994"),
    )
    order_2 = fake_fee_result(
        gross=Decimal("1.239"),
        raw_net=Decimal("1.239"),
    )

    targets = calculate_negative_net_batch_targets(
        order_fee_results=[order_1, order_2],
        bybit_withdrawal_fee_usdt=Decimal("1"),
        month_open_result=month_open_result(),
    )

    expected_sum = Decimal("7.89") + Decimal("1.23")

    assert_ok("ORDER1_TRUNCATED", order_1.net_user_payout_usdt == Decimal("7.89"))
    assert_ok("ORDER2_TRUNCATED", order_2.net_user_payout_usdt == Decimal("1.23"))
    assert_ok("BATCH_TOTAL_SUMS_TRUNCATED", targets.total_net_user_payout_usdt == expected_sum)
    assert_ok(
        "BATCH_DOES_NOT_TRUNCATE_AGGREGATE_RAW",
        targets.total_net_user_payout_usdt != truncate_usdt_to_cents_down(
            Decimal("7.8994") + Decimal("1.239")
        ),
    )

    print("STAGE26_3_11_BATCH_TOTAL_SUMS_TRUNCATED_ORDER_PAYOUTS_OK")


def test_withdrawal_amount_equals_truncated_payout() -> None:
    order_1 = fake_fee_result(
        gross=Decimal("7.8994"),
        raw_net=Decimal("7.8994"),
    )
    order_2 = fake_fee_result(
        gross=Decimal("1.239"),
        raw_net=Decimal("1.239"),
    )

    targets = calculate_negative_net_batch_targets(
        order_fee_results=[order_1, order_2],
        bybit_withdrawal_fee_usdt=Decimal("1"),
        month_open_result=month_open_result(),
    )

    assert_ok(
        "WITHDRAWAL_REQUEST_EQUALS_TOTAL_TRUNCATED",
        targets.withdrawal_request_amount_usdt == targets.total_net_user_payout_usdt,
    )

    amount_str, actual = withdrawal_actual_amount(
        withdrawal_request_amount_usdt=targets.withdrawal_request_amount_usdt,
        precision=6,
    )

    assert_ok("NEGATIVE_BYBIT_ACCEPTS_2DP_AMOUNT_STR", amount_str == "9.12")
    assert_ok("NEGATIVE_BYBIT_ACCEPTS_2DP_AMOUNT_ACTUAL", actual == Decimal("9.12"))

    print("STAGE26_3_11_WITHDRAWAL_AMOUNT_EQUALS_TRUNCATED_PAYOUT_OK")


def test_no_float_round_for_payout() -> None:
    production_sources = "\n".join(
        [
            read("app/settlement/negative_net_fees.py"),
            read("app/settlement/negative_net_targets.py"),
            read("app/settlement/negative_payout_flow.py"),
            read("app/settlement/negative_bybit_flow.py"),
        ]
    )

    assert_ok("NO_PYTHON_ROUND_CALL", "round(" not in production_sources)
    assert_ok("NO_ROUND_HALF_UP", "ROUND_HALF_UP" not in production_sources)
    assert_ok("NO_FLOAT_CAST_FOR_PAYOUT", "float(" not in production_sources)
    assert_ok("USES_ROUND_FLOOR", "ROUND_FLOOR" in read("app/settlement/negative_net_fees.py"))

    print("STAGE26_3_11_NO_FLOAT_ROUND_FOR_PAYOUT_OK")


def test_redeem_payout_truncation_policy_source() -> None:
    fees_source = read("app/settlement/negative_net_fees.py")
    targets_source = read("app/settlement/negative_net_targets.py")
    payout_source = read("app/settlement/negative_payout_flow.py")

    assert_ok(
        "TRUNCATION_FUNCTION_PRESENT",
        "def truncate_usdt_to_cents_down" in fees_source,
    )
    assert_ok(
        "TRUNCATION_USES_ROUND_FLOOR",
        "ROUND_FLOOR" in fees_source and "USDT_CENT" in fees_source,
    )
    assert_ok(
        "RAW_NET_DIAGNOSTIC_PRESENT",
        "raw_net_user_payout_usdt" in fees_source,
    )
    assert_ok(
        "POLICY_DIAGNOSTIC_PRESENT",
        "payout_truncation_policy" in fees_source
        and "floor_to_2_decimals_after_fees" in fees_source,
    )
    assert_ok(
        "REMAINDER_DIAGNOSTIC_PRESENT",
        "payout_truncation_remainder_usdt" in fees_source,
    )
    assert_ok(
        "ORDER_STORES_NET_USER_PAYOUT",
        "order.net_user_payout_usdt = fee_result.net_user_payout_usdt"
        in targets_source,
    )
    assert_ok(
        "BATCH_WITHDRAWAL_USES_TOTAL_NET",
        "withdrawal_request_amount_usdt = total_net_user_payout_usdt"
        in fees_source,
    )
    assert_ok(
        "PAYOUT_FLOW_USES_NET_USER_PAYOUT",
        "amount = dec(order.net_user_payout_usdt)" in payout_source,
    )

    print("STAGE26_3_11_REDEEM_PAYOUT_TRUNCATION_POLICY_OK")


def main() -> int:
    load_dotenv()

    test_redeem_payout_truncates_to_2dp()
    test_no_round_up()
    test_fees_before_truncation()
    test_batch_total_sums_truncated_order_payouts()
    test_withdrawal_amount_equals_truncated_payout()
    test_no_float_round_for_payout()
    test_redeem_payout_truncation_policy_source()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())