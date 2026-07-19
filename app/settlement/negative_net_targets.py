from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.bybit.asset_flows import (
    BybitAssetFlowError,
    query_coin_info,
)
from app.bybit.client import BybitV5Client
from app.config import settings
from app.models import Fund, FundOrder, FundSettlementBatch
from app.settlement.negative_net_fees import (
    NEGATIVE_NET_FEE_POLICY_VERSION,
    MonthOpenPriceMissingError,
    NegativeNetBatchTargets,
    NegativeNetFeeError,
    RedeemOrderFeeResult,
    calculate_negative_net_batch_targets,
    calculate_redeem_order_fees,
    dec,
    get_month_open_price,
)
from app.settlement.negative_failure_service import (
    fail_negative_batch_pre_external,
)
from app.settlement.negative_external_state import (
    inspect_negative_external_state,
)
from app.settlement.pricing_lock import (
    get_runtime_state_for_update,
)
from app.settlement.share_quantity import (
    ShareQuantityError,
    require_share_quantity_4dp_aligned,
)
from app.settlement.statuses import (
    BATCH_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_NEGATIVE_NET_TARGETS_CALCULATED,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
    ORDER_STATUS_PENDING,
    ORDER_STATUS_PROCESSING,
)


REDEEM_ORDER_TARGET_STATUSES = {
    ORDER_STATUS_PENDING,
    ORDER_STATUS_PROCESSING,
    ORDER_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
}


class NegativeNetTargetError(RuntimeError):
    pass


@dataclass(frozen=True)
class NegativeNetWithdrawalFeeResult:
    amount_usdt: Decimal
    source: str
    mode: str
    coin: str
    chain: str
    withdraw_percentage_fee: Decimal
    withdraw_min: Decimal | None
    withdraw_max: Decimal | None
    min_accuracy: int | None
    chain_withdraw: str | None
    diagnostics: dict[str, Any] = field(
        default_factory=dict,
    )


@dataclass(frozen=True)
class NegativeNetOrderTargetResult:
    order_id: int
    gross_redeem_usdt: Decimal
    success_fee_usdt: Decimal
    management_fee_usdt: Decimal
    partial_month_fee_usdt: Decimal
    net_user_payout_usdt: Decimal
    net_price_usdt: Decimal
    success_fee_rate: Decimal
    management_fee_rate: Decimal
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        for key, value in list(raw.items()):
            if isinstance(value, Decimal):
                raw[key] = str(value)
        raw["diagnostics"] = _json_dict(raw["diagnostics"])
        return raw


@dataclass(frozen=True)
class NegativeNetTargetResult:
    ok: bool
    settlement_batch_id: int
    fund_id: int
    fund_code: str
    status_before: str
    status_after: str
    order_count: int
    bybit_withdrawal_fee_usdt: Decimal
    batch_targets: NegativeNetBatchTargets | None
    order_results: list[NegativeNetOrderTargetResult]
    error: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "settlement_batch_id": self.settlement_batch_id,
            "fund_id": self.fund_id,
            "fund_code": self.fund_code,
            "status_before": self.status_before,
            "status_after": self.status_after,
            "order_count": self.order_count,
            "bybit_withdrawal_fee_usdt": str(self.bybit_withdrawal_fee_usdt),
            "batch_targets": (
                self.batch_targets.to_dict()
                if self.batch_targets is not None
                else None
            ),
            "order_results": [
                item.to_dict()
                for item in self.order_results
            ],
            "error": self.error,
            "diagnostics": _json_dict(self.diagnostics),
        }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]

    return value


def _json_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_value(value) for key, value in data.items()}


def resolve_negative_net_bybit_withdrawal_fee(
    *,
    bybit_withdrawal_fee_usdt: (
        Decimal | str | None
    ) = None,
    bybit_client: BybitV5Client | None = None,
    use_live_bybit_withdrawal_fee: (
        bool | None
    ) = None,
    coin: str = "USDT",
    chain: str = "BSC",
) -> NegativeNetWithdrawalFeeResult:
    clean_coin = str(
        coin or ""
    ).strip().upper()
    clean_chain = str(
        chain or ""
    ).strip().upper()

    if use_live_bybit_withdrawal_fee is True:
        use_live_fee = True
    elif use_live_bybit_withdrawal_fee is False:
        use_live_fee = False
    elif bybit_withdrawal_fee_usdt is not None:
        use_live_fee = False
    else:
        use_live_fee = bool(
            settings.NEGATIVE_NET_TARGETS_ALLOW_LIVE_FEE
        )

    if clean_coin != "USDT":
        raise NegativeNetFeeError(
            "Negative-net withdrawal fee coin "
            "must be USDT"
        )

    if clean_chain != "BSC":
        raise NegativeNetFeeError(
            "Negative-net withdrawal fee chain "
            "must be BSC"
        )

    if use_live_fee:
        if bybit_client is None:
            raise NegativeNetFeeError(
                "Live read-only negative-net "
                "withdrawal fee requires bybit_client"
            )

        try:
            coin_info = query_coin_info(
                bybit_client,
                coin=clean_coin,
                chain=clean_chain,
            )
        except BybitAssetFlowError as exc:
            raise NegativeNetFeeError(
                "Bybit coin info query failed: "
                f"{exc}"
            ) from exc

        returned_coin = str(
            coin_info.coin or ""
        ).strip().upper()
        returned_chain = str(
            coin_info.chain or ""
        ).strip().upper()

        if returned_coin != clean_coin:
            raise NegativeNetFeeError(
                "Bybit coin info returned an "
                "unexpected coin: "
                f"expected={clean_coin}, "
                f"actual={returned_coin}"
            )

        if returned_chain != clean_chain:
            raise NegativeNetFeeError(
                "Bybit coin info returned an "
                "unexpected chain: "
                f"expected={clean_chain}, "
                f"actual={returned_chain}"
            )

        chain_withdraw = str(
            coin_info.chain_withdraw or ""
        ).strip()

        if chain_withdraw != "1":
            raise NegativeNetFeeError(
                "Bybit coin info says BSC USDT "
                "withdrawal is disabled: "
                f"chainWithdraw={chain_withdraw or 'missing'}"
            )

        raw_withdraw_fee = (
            coin_info.raw.get("withdrawFee")
        )

        if (
            raw_withdraw_fee is None
            or not str(raw_withdraw_fee).strip()
        ):
            raise NegativeNetFeeError(
                "Bybit withdrawFee is missing"
            )

        withdraw_fee = dec(
            raw_withdraw_fee
        )

        if withdraw_fee < Decimal("0"):
            raise NegativeNetFeeError(
                "Bybit withdrawFee must be "
                f"non-negative: {withdraw_fee}"
            )

        raw_percentage_fee = (
            coin_info.raw.get(
                "withdrawPercentageFee"
            )
        )

        if (
            raw_percentage_fee is None
            or not str(
                raw_percentage_fee
            ).strip()
        ):
            raise NegativeNetFeeError(
                "Bybit withdrawPercentageFee "
                "is missing"
            )

        withdraw_percentage_fee = dec(
            raw_percentage_fee
        )

        if (
            withdraw_percentage_fee
            != Decimal("0")
        ):
            raise NegativeNetFeeError(
                "Bybit withdrawPercentageFee "
                "must be zero; percentage-based "
                "withdrawals are unsupported: "
                f"{withdraw_percentage_fee}"
            )

        raw_withdraw_min = (
            coin_info.raw.get("withdrawMin")
        )

        if (
            raw_withdraw_min is None
            or not str(raw_withdraw_min).strip()
        ):
            raise NegativeNetFeeError(
                "Bybit withdrawMin is missing"
            )

        withdraw_min = dec(
            raw_withdraw_min
        )

        if withdraw_min < Decimal("0"):
            raise NegativeNetFeeError(
                "Bybit withdrawMin must be "
                f"non-negative: {withdraw_min}"
            )

        raw_withdraw_max = (
            coin_info.raw.get("withdrawMax")
        )

        if (
            raw_withdraw_max is None
            or not str(raw_withdraw_max).strip()
        ):
            withdraw_max = None
        else:
            withdraw_max = dec(
                raw_withdraw_max
            )

            if withdraw_max <= Decimal("0"):
                raise NegativeNetFeeError(
                    "Bybit withdrawMax must be "
                    f"positive when provided: "
                    f"{withdraw_max}"
                )

            if withdraw_max < withdraw_min:
                raise NegativeNetFeeError(
                    "Bybit withdrawMax is below "
                    "withdrawMin: "
                    f"withdrawMin={withdraw_min}, "
                    f"withdrawMax={withdraw_max}"
                )

        raw_min_accuracy = (
            coin_info.raw.get("minAccuracy")
        )

        if (
            raw_min_accuracy is None
            or not str(raw_min_accuracy).strip()
        ):
            raise NegativeNetFeeError(
                "Bybit minAccuracy is missing"
            )

        raw_min_accuracy_decimal = dec(
            raw_min_accuracy
        )
        min_accuracy = int(
            coin_info.min_accuracy
        )

        if (
            min_accuracy < 0
            or min_accuracy > 18
        ):
            raise NegativeNetFeeError(
                "Bybit minAccuracy is outside "
                "the supported range 0..18: "
                f"{min_accuracy}"
            )

        if (
            raw_min_accuracy_decimal
            == raw_min_accuracy_decimal
            .to_integral_value()
        ):
            expected_min_accuracy = int(
                raw_min_accuracy_decimal
            )
        else:
            if (
                raw_min_accuracy_decimal
                <= Decimal("0")
            ):
                raise NegativeNetFeeError(
                    "Bybit minAccuracy must be "
                    "non-negative"
                )

            expected_quantum = (
                Decimal("1").scaleb(
                    -min_accuracy
                )
            )

            if (
                raw_min_accuracy_decimal
                != expected_quantum
            ):
                raise NegativeNetFeeError(
                    "Bybit minAccuracy raw value "
                    "does not exactly match parsed "
                    "precision: "
                    f"raw={raw_min_accuracy_decimal}, "
                    f"parsed={min_accuracy}"
                )

            expected_min_accuracy = (
                min_accuracy
            )

        if expected_min_accuracy != min_accuracy:
            raise NegativeNetFeeError(
                "Bybit minAccuracy parsed value "
                "does not exactly match raw value: "
                f"raw={raw_min_accuracy_decimal}, "
                f"parsed={min_accuracy}"
            )

        diagnostics = {
            "mode": "bybit_live_readonly",
            "source": "bybit_coin_info",
            "coin": returned_coin,
            "chain": returned_chain,
            "withdrawFee": withdraw_fee,
            "withdrawPercentageFee": (
                withdraw_percentage_fee
            ),
            "withdrawMin": withdraw_min,
            "withdrawMax": withdraw_max,
            "minAccuracy": min_accuracy,
            "chainWithdraw": chain_withdraw,
        }

        return NegativeNetWithdrawalFeeResult(
            amount_usdt=withdraw_fee,
            source="bybit_coin_info",
            mode="bybit_live_readonly",
            coin=returned_coin,
            chain=returned_chain,
            withdraw_percentage_fee=(
                withdraw_percentage_fee
            ),
            withdraw_min=withdraw_min,
            withdraw_max=withdraw_max,
            min_accuracy=min_accuracy,
            chain_withdraw=chain_withdraw,
            diagnostics=diagnostics,
        )

    mock_fee = dec(
        (
            bybit_withdrawal_fee_usdt
            if bybit_withdrawal_fee_usdt
            is not None
            else (
                settings
                .NEGATIVE_NET_MOCK_BYBIT_WITHDRAWAL_FEE_USDT
            )
        )
    )

    if mock_fee < Decimal("0"):
        raise NegativeNetFeeError(
            "bybit_withdrawal_fee_usdt must "
            f"be non-negative: {mock_fee}"
        )

    diagnostics = {
        "mode": "mock",
        "source": "mock_config",
        "coin": clean_coin,
        "chain": clean_chain,
        "withdrawFee": mock_fee,
        "withdrawPercentageFee": Decimal("0"),
        "withdrawMin": None,
        "withdrawMax": None,
        "minAccuracy": None,
        "chainWithdraw": None,
    }

    return NegativeNetWithdrawalFeeResult(
        amount_usdt=mock_fee,
        source="mock_config",
        mode="mock",
        coin=clean_coin,
        chain=clean_chain,
        withdraw_percentage_fee=Decimal("0"),
        withdraw_min=None,
        withdraw_max=None,
        min_accuracy=None,
        chain_withdraw=None,
        diagnostics=diagnostics,
    )


def validate_live_withdrawal_amount(
    *,
    fee_policy: NegativeNetWithdrawalFeeResult,
    withdrawal_request_amount_usdt: Decimal,
) -> dict[str, bool]:
    if fee_policy.mode != "bybit_live_readonly":
        raise NegativeNetFeeError(
            "Live withdrawal constraints require "
            "bybit_live_readonly fee policy"
        )

    amount = dec(
        withdrawal_request_amount_usdt
    )

    if amount <= Decimal("0"):
        raise NegativeNetFeeError(
            "withdrawal_request_amount_usdt "
            f"must be positive: {amount}"
        )

    if fee_policy.withdraw_min is None:
        raise NegativeNetFeeError(
            "Bybit withdrawMin is missing"
        )

    if amount < fee_policy.withdraw_min:
        raise NegativeNetFeeError(
            "Withdrawal request is below "
            "Bybit withdrawMin: "
            f"amount={amount}, "
            f"withdrawMin={fee_policy.withdraw_min}"
        )

    if (
        fee_policy.withdraw_max is not None
        and amount > fee_policy.withdraw_max
    ):
        raise NegativeNetFeeError(
            "Withdrawal request is above "
            "Bybit withdrawMax: "
            f"amount={amount}, "
            f"withdrawMax={fee_policy.withdraw_max}"
        )

    if fee_policy.min_accuracy is None:
        raise NegativeNetFeeError(
            "Bybit minAccuracy is missing"
        )

    quantum = Decimal("1").scaleb(
        -int(fee_policy.min_accuracy)
    )

    try:
        represented = amount.quantize(
            quantum
        )
    except Exception as exc:
        raise NegativeNetFeeError(
            "Withdrawal request cannot be "
            "represented using Bybit "
            f"minAccuracy={fee_policy.min_accuracy}"
        ) from exc

    if represented != amount:
        raise NegativeNetFeeError(
            "Withdrawal request precision "
            "exceeds Bybit minAccuracy: "
            f"amount={amount}, "
            f"minAccuracy={fee_policy.min_accuracy}"
        )

    return {
        "withdrawal_amount_positive": True,
        "withdrawal_gte_withdraw_min": True,
        "withdrawal_lte_withdraw_max": True,
        "withdrawal_exact_min_accuracy": True,
        "withdrawal_percentage_fee_zero": (
            fee_policy.withdraw_percentage_fee
            == Decimal("0")
        ),
        "chain_withdraw_enabled": (
            fee_policy.chain_withdraw == "1"
        ),
    }

def _lock_settlement_batch(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundSettlementBatch:
    batch = (
        db.query(FundSettlementBatch)
        .filter(FundSettlementBatch.id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )

    if batch is None:
        raise NegativeNetTargetError(
            f"Settlement batch not found: {settlement_batch_id}"
        )

    return batch


def _get_fund(
    db: Session,
    *,
    fund_id: int,
) -> Fund:
    fund = (
        db.query(Fund)
        .filter(Fund.id == int(fund_id))
        .first()
    )

    if fund is None:
        raise NegativeNetTargetError(
            f"Fund not found: {fund_id}"
        )

    return fund


def _validate_batch_for_negative_net_targets(batch: FundSettlementBatch) -> None:
    if batch.status != BATCH_STATUS_AWAITING_NEGATIVE_NET_EXECUTION:
        raise NegativeNetTargetError(
            (
                "Settlement batch must be awaiting_negative_net_execution: "
                f"batch_id={batch.id}, status={batch.status}"
            )
        )

    if dec(batch.net_cash_usdt) >= Decimal("0"):
        raise NegativeNetTargetError(
            (
                "Settlement batch is not negative-net: "
                f"batch_id={batch.id}, net_cash_usdt={batch.net_cash_usdt}"
            )
        )

    if batch.settlement_ts is None:
        raise NegativeNetTargetError(
            f"Settlement batch settlement_ts is missing: batch_id={batch.id}"
        )

    if dec(batch.settlement_price_usdt) <= Decimal("0"):
        raise NegativeNetTargetError(
            (
                "Settlement batch settlement_price_usdt must be positive: "
                f"batch_id={batch.id}, settlement_price_usdt={batch.settlement_price_usdt}"
            )
        )


def _load_redeem_orders_for_update(
    db: Session,
    *,
    settlement_batch_id: int,
) -> list[FundOrder]:
    return (
        db.query(FundOrder)
        .filter(
            FundOrder.settlement_batch_id
            == int(settlement_batch_id),
            FundOrder.side == ORDER_SIDE_REDEEM,
        )
        .order_by(FundOrder.id.asc())
        .with_for_update()
        .all()
    )


def _validate_no_external_action_evidence(
    db: Session,
    *,
    batch: FundSettlementBatch,
) -> None:
    external_state = (
        inspect_negative_external_state(
            db,
            settlement_batch_id=int(batch.id),
        )
    )

    action_detected = any(
        (
            external_state.sale_action_detected,
            external_state.earn_action_detected,
            external_state
            .universal_transfer_action_detected,
            external_state.withdrawal_action_detected,
            external_state.payout_action_detected,
            external_state.gas_topup_action_detected,
            external_state
            .other_external_action_detected,
        )
    )

    if (
        external_state.accounting_finalized
        or action_detected
        or external_state.reasons
        or external_state.evidence
    ):
        raise NegativeNetTargetError(
            "External action absence is not "
            "proven for negative-net targets: "
            f"batch_id={batch.id}, "
            f"reasons={external_state.reasons}, "
            f"evidence={external_state.evidence}"
        )


def _validate_active_pricing_lock(
    db: Session,
    *,
    batch: FundSettlementBatch,
) -> None:
    if batch.pricing_locked_at is None:
        raise NegativeNetTargetError(
            "Settlement batch pricing_locked_at "
            f"is missing: batch_id={batch.id}"
        )

    if batch.pricing_unlocked_at is not None:
        raise NegativeNetTargetError(
            "Settlement batch pricing is already "
            f"unlocked: batch_id={batch.id}"
        )

    runtime_state = (
        get_runtime_state_for_update(
            db,
            fund_id=int(batch.fund_id),
        )
    )

    if runtime_state is None:
        raise NegativeNetTargetError(
            "Fund runtime state is missing for "
            f"pricing-locked batch: batch_id={batch.id}"
        )

    if not bool(runtime_state.pricing_locked):
        raise NegativeNetTargetError(
            "Fund runtime pricing lock is not "
            f"active: batch_id={batch.id}"
        )

    lock_batch_id = (
        int(runtime_state.pricing_lock_batch_id)
        if runtime_state.pricing_lock_batch_id
        is not None
        else None
    )

    if lock_batch_id != int(batch.id):
        raise NegativeNetTargetError(
            "Pricing lock identity mismatch: "
            f"expected_batch_id={batch.id}, "
            f"actual_batch_id={lock_batch_id}"
        )


def _validate_redeem_orders_and_share_totals(
    *,
    batch: FundSettlementBatch,
    redeem_orders: list[FundOrder],
) -> Decimal:
    if not redeem_orders:
        raise NegativeNetTargetError(
            "No redeem orders found for "
            f"negative-net batch: batch_id={batch.id}"
        )

    total_redeem_shares = Decimal("0")

    for order in redeem_orders:
        status = str(order.status or "")

        if status not in REDEEM_ORDER_TARGET_STATUSES:
            raise NegativeNetTargetError(
                "Redeem order has invalid status "
                "for negative-net targets: "
                f"order_id={order.id}, "
                f"status={status}"
            )

        try:
            shares = (
                require_share_quantity_4dp_aligned(
                    order.shares,
                    field_name=(
                        f"order[{order.id}].shares"
                    ),
                )
            )
        except ShareQuantityError as exc:
            raise NegativeNetTargetError(
                "Redeem order shares are invalid: "
                f"order_id={order.id}, error={exc}"
            ) from exc

        if shares <= Decimal("0"):
            raise NegativeNetTargetError(
                "Redeem order shares must be "
                f"positive: order_id={order.id}"
            )

        total_redeem_shares += shares

    if (
        dec(batch.total_redeem_shares)
        != total_redeem_shares
    ):
        raise NegativeNetTargetError(
            "batch.total_redeem_shares does not "
            "match Redeem orders: "
            f"batch={batch.total_redeem_shares}, "
            f"orders={total_redeem_shares}"
        )

    if (
        dec(batch.planned_shares_to_redeem)
        != total_redeem_shares
    ):
        raise NegativeNetTargetError(
            "batch.planned_shares_to_redeem does "
            "not match Redeem orders: "
            f"batch={batch.planned_shares_to_redeem}, "
            f"orders={total_redeem_shares}"
        )

    return total_redeem_shares


def _require_persisted_decimal(
    value: Any,
    *,
    field_name: str,
) -> Decimal:
    if value is None:
        raise NegativeNetTargetError(
            f"{field_name} is missing in "
            "calculated target snapshot"
        )

    try:
        result = dec(value)
    except NegativeNetFeeError as exc:
        raise NegativeNetTargetError(
            f"{field_name} is invalid: {exc}"
        ) from exc

    if not result.is_finite():
        raise NegativeNetTargetError(
            f"{field_name} is not finite"
        )

    return result


def _build_idempotent_target_result(
    db: Session,
    *,
    batch: FundSettlementBatch,
    fund: Fund,
    status_before: str,
) -> NegativeNetTargetResult:
    if (
        str(batch.status)
        != BATCH_STATUS_NEGATIVE_NET_TARGETS_CALCULATED
    ):
        raise NegativeNetTargetError(
            "Idempotent target replay requires "
            "negative_net_targets_calculated"
        )

    if batch.accounting_finalized_at is not None:
        raise NegativeNetTargetError(
            "Idempotent target replay is blocked "
            "after accounting finalization"
        )

    if batch.negative_net_target_calculated_at is None:
        raise NegativeNetTargetError(
            "Calculated batch is missing "
            "negative_net_target_calculated_at"
        )

    if (
        str(
            batch.negative_net_fee_policy_version
            or ""
        )
        != NEGATIVE_NET_FEE_POLICY_VERSION
    ):
        raise NegativeNetTargetError(
            "Calculated batch fee policy version "
            "does not match current policy: "
            f"stored={batch.negative_net_fee_policy_version}, "
            f"expected={NEGATIVE_NET_FEE_POLICY_VERSION}"
        )

    _validate_active_pricing_lock(
        db,
        batch=batch,
    )
    _validate_no_external_action_evidence(
        db,
        batch=batch,
    )

    redeem_orders = (
        _load_redeem_orders_for_update(
            db,
            settlement_batch_id=int(batch.id),
        )
    )
    _validate_redeem_orders_and_share_totals(
        batch=batch,
        redeem_orders=redeem_orders,
    )

    order_results: list[
        NegativeNetOrderTargetResult
    ] = []

    total_gross = Decimal("0")
    total_net = Decimal("0")
    total_success_fee = Decimal("0")
    total_management_fee = Decimal("0")
    total_partial_fee = Decimal("0")

    for order in redeem_orders:
        gross = _require_persisted_decimal(
            order.gross_redeem_usdt,
            field_name=(
                f"order[{order.id}].gross_redeem_usdt"
            ),
        )
        success_fee = _require_persisted_decimal(
            order.success_fee_usdt,
            field_name=(
                f"order[{order.id}].success_fee_usdt"
            ),
        )
        management_fee = (
            _require_persisted_decimal(
                order.management_fee_usdt,
                field_name=(
                    f"order[{order.id}]"
                    ".management_fee_usdt"
                ),
            )
        )
        partial_fee = _require_persisted_decimal(
            order.partial_month_fee_usdt,
            field_name=(
                f"order[{order.id}]"
                ".partial_month_fee_usdt"
            ),
        )
        net_payout = _require_persisted_decimal(
            order.net_user_payout_usdt,
            field_name=(
                f"order[{order.id}]"
                ".net_user_payout_usdt"
            ),
        )
        net_price = _require_persisted_decimal(
            order.net_price_usdt,
            field_name=(
                f"order[{order.id}].net_price_usdt"
            ),
        )
        success_rate = _require_persisted_decimal(
            order.success_fee_rate,
            field_name=(
                f"order[{order.id}].success_fee_rate"
            ),
        )
        management_rate = (
            _require_persisted_decimal(
                order.management_fee_rate,
                field_name=(
                    f"order[{order.id}]"
                    ".management_fee_rate"
                ),
            )
        )

        if gross <= Decimal("0"):
            raise NegativeNetTargetError(
                "Persisted gross redeem must "
                f"be positive: order_id={order.id}"
            )

        if min(
            success_fee,
            management_fee,
            partial_fee,
            success_rate,
            management_rate,
        ) < Decimal("0"):
            raise NegativeNetTargetError(
                "Persisted fee value is negative: "
                f"order_id={order.id}"
            )

        if partial_fee != (
            success_fee + management_fee
        ):
            raise NegativeNetTargetError(
                "Persisted partial fee does not "
                "equal success plus management fee: "
                f"order_id={order.id}"
            )

        if partial_fee > gross:
            raise NegativeNetTargetError(
                "Persisted total fee exceeds gross: "
                f"order_id={order.id}"
            )

        if (
            net_payout <= Decimal("0")
            or net_price <= Decimal("0")
        ):
            raise NegativeNetTargetError(
                "Persisted payout or net price is "
                f"non-positive: order_id={order.id}"
            )

        if (
            net_payout
            != net_payout.quantize(Decimal("0.01"))
        ):
            raise NegativeNetTargetError(
                "Persisted net payout is not "
                f"cent-aligned: order_id={order.id}"
            )

        total_gross += gross
        total_net += net_payout
        total_success_fee += success_fee
        total_management_fee += management_fee
        total_partial_fee += partial_fee

        order_results.append(
            NegativeNetOrderTargetResult(
                order_id=int(order.id),
                gross_redeem_usdt=gross,
                success_fee_usdt=success_fee,
                management_fee_usdt=(
                    management_fee
                ),
                partial_month_fee_usdt=(
                    partial_fee
                ),
                net_user_payout_usdt=(
                    net_payout
                ),
                net_price_usdt=net_price,
                success_fee_rate=success_rate,
                management_fee_rate=(
                    management_rate
                ),
                diagnostics={
                    "idempotent_replay": True,
                    "fee_policy_version": (
                        NEGATIVE_NET_FEE_POLICY_VERSION
                    ),
                },
            )
        )

    stored_total_gross = (
        _require_persisted_decimal(
            batch.total_gross_redeem_usdt,
            field_name=(
                "batch.total_gross_redeem_usdt"
            ),
        )
    )
    stored_total_net = _require_persisted_decimal(
        batch.total_net_user_payout_usdt,
        field_name=(
            "batch.total_net_user_payout_usdt"
        ),
    )
    stored_success_fee = (
        _require_persisted_decimal(
            batch.total_success_fee_usdt,
            field_name=(
                "batch.total_success_fee_usdt"
            ),
        )
    )
    stored_management_fee = (
        _require_persisted_decimal(
            batch.total_management_fee_usdt,
            field_name=(
                "batch.total_management_fee_usdt"
            ),
        )
    )
    stored_partial_fee = (
        _require_persisted_decimal(
            batch.total_partial_month_fee_usdt,
            field_name=(
                "batch.total_partial_month_fee_usdt"
            ),
        )
    )
    stored_bybit_fee = _require_persisted_decimal(
        batch.bybit_withdrawal_fee_usdt,
        field_name=(
            "batch.bybit_withdrawal_fee_usdt"
        ),
    )
    stored_required_master = (
        _require_persisted_decimal(
            batch.required_master_usdt,
            field_name=(
                "batch.required_master_usdt"
            ),
        )
    )
    stored_withdrawal = (
        _require_persisted_decimal(
            batch.withdrawal_request_amount_usdt,
            field_name=(
                "batch.withdrawal_request_amount_usdt"
            ),
        )
    )

    expected_values = (
        (stored_total_gross, total_gross),
        (stored_total_net, total_net),
        (stored_success_fee, total_success_fee),
        (
            stored_management_fee,
            total_management_fee,
        ),
        (stored_partial_fee, total_partial_fee),
    )

    if any(
        stored != calculated
        for stored, calculated in expected_values
    ):
        raise NegativeNetTargetError(
            "Persisted batch target totals do not "
            "match persisted order targets"
        )

    if stored_bybit_fee < Decimal("0"):
        raise NegativeNetTargetError(
            "Persisted Bybit withdrawal fee "
            "is negative"
        )

    if stored_required_master != (
        stored_total_net
        + stored_bybit_fee
        + stored_partial_fee
    ):
        raise NegativeNetTargetError(
            "Persisted required_master_usdt "
            "arithmetic mismatch"
        )

    if stored_withdrawal != stored_total_net:
        raise NegativeNetTargetError(
            "Persisted withdrawal amount does "
            "not equal total net user payout"
        )

    month_open_price = (
        _require_persisted_decimal(
            batch.fee_calc_month_open_price_usdt,
            field_name=(
                "batch.fee_calc_month_open_price_usdt"
            ),
        )
    )

    month_open_source = str(
        batch.fee_calc_month_open_source or ""
    ).strip()

    if not month_open_source:
        raise NegativeNetTargetError(
            "Persisted month-open source is missing"
        )

    fee_days = int(
        batch.fee_calc_days_in_month_period
        or 0
    )

    if fee_days < 1 or fee_days > 31:
        raise NegativeNetTargetError(
            "Persisted fee day is invalid: "
            f"{fee_days}"
        )

    targets = NegativeNetBatchTargets(
        total_gross_redeem_usdt=(
            stored_total_gross
        ),
        total_net_user_payout_usdt=(
            stored_total_net
        ),
        total_success_fee_usdt=(
            stored_success_fee
        ),
        total_management_fee_usdt=(
            stored_management_fee
        ),
        total_partial_month_fee_usdt=(
            stored_partial_fee
        ),
        bybit_withdrawal_fee_usdt=(
            stored_bybit_fee
        ),
        required_master_usdt=(
            stored_required_master
        ),
        withdrawal_request_amount_usdt=(
            stored_withdrawal
        ),
        fee_calc_month_open_price_usdt=(
            month_open_price
        ),
        fee_calc_month_open_source=(
            month_open_source
        ),
        fee_calc_days_in_month_period=(
            fee_days
        ),
        order_count=len(redeem_orders),
    )

    diagnostics = dict(
        batch.negative_net_target_diagnostics_json
        or {}
    )
    diagnostics["idempotent_replay"] = True

    return NegativeNetTargetResult(
        ok=True,
        settlement_batch_id=int(batch.id),
        fund_id=int(batch.fund_id),
        fund_code=str(fund.code),
        status_before=status_before,
        status_after=str(batch.status),
        order_count=len(redeem_orders),
        bybit_withdrawal_fee_usdt=(
            stored_bybit_fee
        ),
        batch_targets=targets,
        order_results=order_results,
        error=None,
        diagnostics=diagnostics,
    )

def _redeem_order_lookup_diagnostics(
    db: Session,
    *,
    settlement_batch_id: int,
) -> dict[str, Any]:
    rows = (
        db.query(FundOrder.status, func.count(FundOrder.id))
        .filter(
            FundOrder.settlement_batch_id == int(settlement_batch_id),
            FundOrder.side == ORDER_SIDE_REDEEM,
        )
        .group_by(FundOrder.status)
        .order_by(FundOrder.status.asc())
        .all()
    )

    statuses_found = {
        str(status): int(count)
        for status, count in rows
    }

    return {
        "settlement_batch_id": int(settlement_batch_id),
        "allowed_statuses": sorted(REDEEM_ORDER_TARGET_STATUSES),
        "redeem_order_count_ignoring_status": sum(statuses_found.values()),
        "redeem_order_statuses_found": statuses_found,
    }


def _apply_fee_result_to_order(
    *,
    order: FundOrder,
    fee_result: RedeemOrderFeeResult,
) -> NegativeNetOrderTargetResult:
    order.gross_redeem_usdt = fee_result.gross_redeem_usdt
    order.success_fee_usdt = fee_result.success_fee_usdt
    order.management_fee_usdt = fee_result.management_fee_usdt
    order.partial_month_fee_usdt = fee_result.partial_month_fee_usdt
    order.net_user_payout_usdt = fee_result.net_user_payout_usdt
    order.net_price_usdt = fee_result.net_price_usdt
    order.fee_calc_month_open_price_usdt = fee_result.fee_calc_month_open_price_usdt
    order.fee_calc_days_in_month_period = fee_result.fee_calc_days_in_month_period
    order.success_fee_rate = fee_result.success_fee_rate
    order.management_fee_rate = fee_result.management_fee_rate

    return NegativeNetOrderTargetResult(
        order_id=order.id,
        gross_redeem_usdt=fee_result.gross_redeem_usdt,
        success_fee_usdt=fee_result.success_fee_usdt,
        management_fee_usdt=fee_result.management_fee_usdt,
        partial_month_fee_usdt=fee_result.partial_month_fee_usdt,
        net_user_payout_usdt=fee_result.net_user_payout_usdt,
        net_price_usdt=fee_result.net_price_usdt,
        success_fee_rate=fee_result.success_fee_rate,
        management_fee_rate=fee_result.management_fee_rate,
        diagnostics=fee_result.diagnostics,
    )


def _apply_batch_targets(
    *,
    batch: FundSettlementBatch,
    targets: NegativeNetBatchTargets,
    calculated_at: datetime,
    diagnostics: dict[str, Any],
) -> None:
    batch.total_gross_redeem_usdt = targets.total_gross_redeem_usdt
    batch.total_net_user_payout_usdt = targets.total_net_user_payout_usdt
    batch.total_success_fee_usdt = targets.total_success_fee_usdt
    batch.total_management_fee_usdt = targets.total_management_fee_usdt
    batch.total_partial_month_fee_usdt = targets.total_partial_month_fee_usdt
    batch.bybit_withdrawal_fee_usdt = targets.bybit_withdrawal_fee_usdt
    batch.required_master_usdt = targets.required_master_usdt
    batch.withdrawal_request_amount_usdt = targets.withdrawal_request_amount_usdt
    batch.negative_net_target_calculated_at = calculated_at
    batch.fee_calc_month_open_price_usdt = targets.fee_calc_month_open_price_usdt
    batch.fee_calc_month_open_source = targets.fee_calc_month_open_source
    batch.fee_calc_days_in_month_period = targets.fee_calc_days_in_month_period
    batch.negative_net_target_diagnostics_json = (
        _json_dict(diagnostics)
    )
    batch.negative_net_fee_policy_version = (
        NEGATIVE_NET_FEE_POLICY_VERSION
    )
    batch.status = BATCH_STATUS_NEGATIVE_NET_TARGETS_CALCULATED
    batch.error = None
    batch.updated_at = calculated_at


def _mark_batch_failed_requires_review(
    db: Session,
    *,
    batch: FundSettlementBatch,
    status_before: str,
    fund: Fund,
    bybit_withdrawal_fee_usdt: Decimal,
    error: str,
    diagnostics: dict[str, Any],
    now: datetime,
) -> NegativeNetTargetResult:
    persisted_diagnostics = _json_dict(
        {
            **diagnostics,
            "controlled_failure": True,
            "fee_policy_version": (
                NEGATIVE_NET_FEE_POLICY_VERSION
            ),
        }
    )

    batch.negative_net_target_diagnostics_json = (
        persisted_diagnostics
    )
    batch.negative_net_fee_policy_version = (
        NEGATIVE_NET_FEE_POLICY_VERSION
    )
    batch.updated_at = now

    db.add(batch)
    db.flush()

    recovery = fail_negative_batch_pre_external(
        db,
        settlement_batch_id=int(batch.id),
        error=error,
        source="negative_net_targets",
    )

    persisted_diagnostics[
        "reserve_recovery"
    ] = {
        "buy_reserve_released_usdt": str(
            recovery.buy_reserve_released_usdt
        ),
        "redeem_reserve_released_shares": str(
            recovery
            .redeem_reserve_released_shares
        ),
        "reserve_release_blocked": list(
            recovery.reserve_release_blocked
        ),
        "pricing_unlocked": (
            recovery.pricing_unlocked
        ),
        "pricing_unlock_blocked": (
            recovery.pricing_unlock_blocked
        ),
    }

    batch.negative_net_target_diagnostics_json = (
        persisted_diagnostics
    )
    batch.updated_at = utcnow()

    db.add(batch)
    db.flush()

    return NegativeNetTargetResult(
        ok=False,
        settlement_batch_id=int(batch.id),
        fund_id=int(batch.fund_id),
        fund_code=str(fund.code),
        status_before=status_before,
        status_after=str(batch.status),
        order_count=0,
        bybit_withdrawal_fee_usdt=(
            bybit_withdrawal_fee_usdt
        ),
        batch_targets=None,
        order_results=[],
        error=error,
        diagnostics=persisted_diagnostics,
    )


def calculate_and_store_negative_net_targets(
    db: Session,
    *,
    settlement_batch_id: int,
    bybit_withdrawal_fee_usdt: (
        Decimal | str | None
    ) = None,
    bybit_client: BybitV5Client | None = None,
    use_live_bybit_withdrawal_fee: (
        bool | None
    ) = None,
    now: datetime | None = None,
) -> NegativeNetTargetResult:
    """
    Calculate and persist negative-net targets.

    Mock mode performs no Bybit calls.

    Live read-only mode may perform only the
    authenticated GET coin-info request needed
    to snapshot BSC USDT withdrawal constraints.

    This function performs no Bybit POST,
    no transfer, no withdrawal, no trade,
    no BSC transaction and no accounting
    finalization.
    """
    now = now or utcnow()

    batch = _lock_settlement_batch(
        db,
        settlement_batch_id=(
            settlement_batch_id
        ),
    )
    status_before = str(batch.status)

    fund = _get_fund(
        db,
        fund_id=int(batch.fund_id),
    )

    if (
        status_before
        == BATCH_STATUS_NEGATIVE_NET_TARGETS_CALCULATED
    ):
        return _build_idempotent_target_result(
            db,
            batch=batch,
            fund=fund,
            status_before=status_before,
        )

    if (
        status_before
        != BATCH_STATUS_AWAITING_NEGATIVE_NET_EXECUTION
    ):
        raise NegativeNetTargetError(
            "Negative-net targets cannot run "
            "for this batch status and the batch "
            "will not be mutated: "
            f"batch_id={batch.id}, "
            f"status={status_before}"
        )

    fee_policy: (
        NegativeNetWithdrawalFeeResult | None
    ) = None
    bybit_fee = Decimal("0")

    try:
        _validate_batch_for_negative_net_targets(
            batch
        )

        if batch.accounting_finalized_at is not None:
            raise NegativeNetTargetError(
                "Settlement accounting is already "
                f"finalized: batch_id={batch.id}"
            )

        if (
            batch.negative_net_target_calculated_at
            is not None
        ):
            raise NegativeNetTargetError(
                "Initial target calculation found "
                "an existing calculated timestamp: "
                f"batch_id={batch.id}"
            )

        _validate_active_pricing_lock(
            db,
            batch=batch,
        )
        _validate_no_external_action_evidence(
            db,
            batch=batch,
        )

        fee_policy = (
            resolve_negative_net_bybit_withdrawal_fee(
                bybit_withdrawal_fee_usdt=(
                    bybit_withdrawal_fee_usdt
                ),
                bybit_client=bybit_client,
                use_live_bybit_withdrawal_fee=(
                    use_live_bybit_withdrawal_fee
                ),
                coin=(
                    settings
                    .NEGATIVE_NET_BYBIT_FLOW_COIN
                ),
                chain=(
                    settings
                    .NEGATIVE_NET_BYBIT_FLOW_CHAIN
                ),
            )
        )
        bybit_fee = fee_policy.amount_usdt

        month_open = get_month_open_price(
            db,
            fund_id=int(batch.fund_id),
            settlement_ts=batch.settlement_ts,
        )

        redeem_orders = (
            _load_redeem_orders_for_update(
                db,
                settlement_batch_id=int(
                    batch.id
                ),
            )
        )

        if not redeem_orders:
            lookup_diagnostics = (
                _redeem_order_lookup_diagnostics(
                    db,
                    settlement_batch_id=int(
                        batch.id
                    ),
                )
            )

            raise NegativeNetTargetError(
                "No redeem orders found for "
                "negative-net batch: "
                f"batch_id={batch.id}, "
                "allowed_statuses="
                f"{lookup_diagnostics['allowed_statuses']}, "
                "redeem_order_count_ignoring_status="
                f"{lookup_diagnostics['redeem_order_count_ignoring_status']}, "
                "redeem_order_statuses_found="
                f"{lookup_diagnostics['redeem_order_statuses_found']}"
            )

        total_redeem_shares = (
            _validate_redeem_orders_and_share_totals(
                batch=batch,
                redeem_orders=redeem_orders,
            )
        )

        order_fee_results: list[
            RedeemOrderFeeResult
        ] = []
        order_results: list[
            NegativeNetOrderTargetResult
        ] = []

        for order in redeem_orders:
            fee_result = (
                calculate_redeem_order_fees(
                    fund_code=str(fund.code),
                    settlement_price_usdt=(
                        batch.settlement_price_usdt
                    ),
                    redeem_shares=order.shares,
                    month_open_price_usdt=(
                        month_open.price_usdt
                    ),
                    settlement_ts=(
                        batch.settlement_ts
                    ),
                )
            )

            order_fee_results.append(
                fee_result
            )
            order_results.append(
                _apply_fee_result_to_order(
                    order=order,
                    fee_result=fee_result,
                )
            )

            db.add(order)

        targets = (
            calculate_negative_net_batch_targets(
                order_fee_results=(
                    order_fee_results
                ),
                bybit_withdrawal_fee_usdt=(
                    bybit_fee
                ),
                month_open_result=month_open,
            )
        )

        live_withdrawal_checks: dict[
            str,
            bool,
        ] = {}

        if (
            fee_policy.mode
            == "bybit_live_readonly"
        ):
            live_withdrawal_checks = (
                validate_live_withdrawal_amount(
                    fee_policy=fee_policy,
                    withdrawal_request_amount_usdt=(
                        targets
                        .withdrawal_request_amount_usdt
                    ),
                )
            )

        diagnostics = {
            "mode": fee_policy.mode,
            "calculated_at": now.isoformat(),
            "fee_policy_version": (
                NEGATIVE_NET_FEE_POLICY_VERSION
            ),
            "withdrawal_fee": (
                fee_policy.diagnostics
            ),
            "bybit_withdrawal_fee": (
                fee_policy.diagnostics
            ),
            "targets": targets.to_dict(),
            "invariants": {
                "net_cash_negative": True,
                "pricing_lock_active": True,
                "accounting_not_finalized": True,
                "no_external_action_evidence": True,
                "redeem_order_statuses_valid": True,
                "redeem_shares_4dp_aligned": True,
                "batch_total_redeem_shares_matches_orders": True,
                "planned_shares_to_redeem_matches_orders": True,
                "target_arithmetic_valid": True,
                "decimal_only": True,
                "all_values_finite": True,
            },
            "redeem_order_count": len(
                redeem_orders
            ),
            "total_redeem_shares": str(
                total_redeem_shares
            ),
            "month_open": (
                month_open.to_dict()
            ),
            "live_withdrawal_checks": (
                live_withdrawal_checks
            ),
            "bybit_mode": fee_policy.mode,
            "read_only_bybit_get_used": (
                fee_policy.mode
                == "bybit_live_readonly"
            ),
            "no_bybit_post": True,
            "no_bybit_transfer": True,
            "no_bybit_withdrawal": True,
            "no_bybit_trade": True,
            "no_bsc_transfers": True,
            "no_accounting_finalization": True,
        }

        _apply_batch_targets(
            batch=batch,
            targets=targets,
            calculated_at=now,
            diagnostics=diagnostics,
        )

        db.add(batch)
        db.flush()

        return NegativeNetTargetResult(
            ok=True,
            settlement_batch_id=int(
                batch.id
            ),
            fund_id=int(batch.fund_id),
            fund_code=str(fund.code),
            status_before=status_before,
            status_after=str(batch.status),
            order_count=len(redeem_orders),
            bybit_withdrawal_fee_usdt=(
                bybit_fee
            ),
            batch_targets=targets,
            order_results=order_results,
            error=None,
            diagnostics=_json_dict(
                diagnostics
            ),
        )

    except (
        MonthOpenPriceMissingError,
        NegativeNetFeeError,
        NegativeNetTargetError,
    ) as exc:
        if isinstance(
            exc,
            MonthOpenPriceMissingError,
        ):
            error = (
                "month_open_price_missing: "
                f"{exc}"
            )
        elif isinstance(
            exc,
            NegativeNetTargetError,
        ):
            error = (
                "negative_net_target_error: "
                f"{exc}"
            )
        else:
            error = (
                "negative_net_fee_error: "
                f"{exc}"
            )

        return _mark_batch_failed_requires_review(
            db,
            batch=batch,
            status_before=status_before,
            fund=fund,
            bybit_withdrawal_fee_usdt=(
                bybit_fee
            ),
            error=error,
            diagnostics={
                "error_type": (
                    type(exc).__name__
                ),
                "bybit_mode": (
                    fee_policy.mode
                    if fee_policy is not None
                    else None
                ),
                "bybit_withdrawal_fee": (
                    fee_policy.diagnostics
                    if fee_policy is not None
                    else None
                ),
                "no_bybit_post": True,
                "no_bybit_transfer": True,
                "no_bybit_withdrawal": True,
                "no_bybit_trade": True,
                "no_bsc_transfers": True,
                "no_accounting_finalization": True,
            },
            now=now,
        )
