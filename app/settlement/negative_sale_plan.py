from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    Fund,
    FundNegativeSaleBatch,
    FundNegativeSaleLeg,
    FundSettlementBatch,
)
from app.settlement.negative_sale_snapshot import (
    NegativeSaleAsset,
    NegativeSaleSnapshot,
    dec,
)
from app.settlement.statuses import (
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_NEGATIVE_NET_SALE_PLANNED,
    BATCH_STATUS_NEGATIVE_NET_TARGETS_CALCULATED,
    SALE_BATCH_STATUS_SALE_PLAN_CREATED,
    SALE_BATCH_STATUS_SALE_PLAN_FAILED_REQUIRES_REVIEW,
    SALE_BATCH_STATUS_SNAPSHOT_CREATED,
    SALE_LEG_STATUS_BUFFER_AVAILABLE,
    SALE_LEG_STATUS_CASH_AVAILABLE,
    SALE_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    SALE_LEG_STATUS_PLANNED,
    SALE_LEG_STATUS_SKIPPED_MIN_ORDER,
    SALE_LEG_STATUS_SKIPPED_NOT_ELIGIBLE,
    SALE_LEG_STATUS_SKIPPED_SYMBOL_NOT_TRADING,
    SALE_LEG_STATUS_SKIPPED_ZERO_VALUE,
)


ZERO = Decimal("0")
MIN_PLANNED_SALE_LEG_USDT = Decimal("5")


class NegativeSalePlanError(RuntimeError):
    pass


@dataclass(frozen=True)
class SaleLegPlan:
    leg_group: str
    leg_type: str
    coin: str | None
    symbol: str | None
    category: str | None
    side: str | None
    location: str | None

    current_qty: Decimal | None
    current_size: Decimal | None
    current_usd_value: Decimal | None
    current_notional_usd: Decimal | None
    source_weight: Decimal | None

    target_cash_usdt: Decimal | None
    target_qty: Decimal | None
    expected_cash_delta_usdt: Decimal | None

    eligible: bool
    eligibility_reason: str
    use_for_deficit_cover: bool
    instrument_status: str | None
    min_order_passed: bool | None
    liquidity_check_required: bool | None
    margin_guard_required: bool | None
    planned_execution_mode: str | None
    status: str
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)

        for key, value in list(raw.items()):
            if isinstance(value, Decimal):
                raw[key] = str(value)

        raw["raw"] = _json_dict(raw["raw"])
        return raw


@dataclass(frozen=True)
class NegativeSalePlanComputation:
    required_master_usdt: Decimal
    withdrawal_request_amount_usdt: Decimal
    total_net_user_payout_usdt: Decimal
    total_partial_month_fee_usdt: Decimal
    bybit_withdrawal_fee_usdt: Decimal

    unified_usdt_available: Decimal
    fund_wallet_usdt_available: Decimal
    usdt_earn_available: Decimal
    usdt_earn_redeemable: Decimal
    usdt_earn_used_as_buffer: Decimal
    cash_like_available_for_plan: Decimal
    total_cash_like_available_usdt: Decimal

    sale_target_usdt: Decimal
    planned_sale_usdt: Decimal
    expected_shortage_usdt: Decimal
    expected_surplus_usdt: Decimal
    largest_extra_sale_buffer_pct: Decimal

    legs: list[SaleLegPlan]
    plan_json: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)

        for key, value in list(raw.items()):
            if isinstance(value, Decimal):
                raw[key] = str(value)

        raw["legs"] = [leg.to_dict() for leg in self.legs]
        raw["plan_json"] = _json_dict(raw["plan_json"])
        return raw


@dataclass(frozen=True)
class NegativeSalePlanResult:
    ok: bool
    settlement_batch_id: int
    sale_batch_id: int | None
    fund_id: int
    fund_code: str
    status_before: str
    status_after: str
    sale_batch_status: str | None
    sale_target_usdt: Decimal | None
    planned_sale_usdt: Decimal | None
    leg_count: int
    error: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "settlement_batch_id": self.settlement_batch_id,
            "sale_batch_id": self.sale_batch_id,
            "fund_id": self.fund_id,
            "fund_code": self.fund_code,
            "status_before": self.status_before,
            "status_after": self.status_after,
            "sale_batch_status": self.sale_batch_status,
            "sale_target_usdt": (
                str(self.sale_target_usdt)
                if self.sale_target_usdt is not None
                else None
            ),
            "planned_sale_usdt": (
                str(self.planned_sale_usdt)
                if self.planned_sale_usdt is not None
                else None
            ),
            "leg_count": self.leg_count,
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


def _max_zero(value: Decimal) -> Decimal:
    return value if value > ZERO else ZERO


def _is_trading_status(status: str | None) -> bool:
    if status is None:
        return True

    raw = str(status).strip().lower()
    return raw in {
        "",
        "trading",
        "normal",
        "online",
        "active",
    }


def _map_symbol_from_coin(coin: str | None, symbol: str | None) -> str | None:
    if symbol:
        return symbol.upper()

    if not coin:
        return None

    clean = coin.upper()
    if clean in {"USDT", "USDC", "USD"}:
        return None

    return f"{clean}USDT"


def _asset_value(asset: NegativeSaleAsset) -> Decimal:
    value = dec(asset.usd_value)

    if value > ZERO:
        return value

    if asset.redeemable_usdt is not None:
        return dec(asset.redeemable_usdt)

    if asset.notional_usd is not None:
        return dec(asset.notional_usd)

    return ZERO
def _cash_like_leg(
    *,
    leg_type: str,
    location: str,
    amount_usdt: Decimal,
    status: str,
    reason: str,
    eligible: bool = True,
    use_for_deficit_cover: bool = True,
    target_cash_usdt: Decimal | None = None,
    expected_cash_delta_usdt: Decimal | None = None,
    instrument_status: str = "cash",
) -> SaleLegPlan:
    target_cash = amount_usdt if target_cash_usdt is None else target_cash_usdt
    expected_cash_delta = (
        target_cash
        if expected_cash_delta_usdt is None
        else expected_cash_delta_usdt
    )

    return SaleLegPlan(
        leg_group="cash_like",
        leg_type=leg_type,
        coin="USDT",
        symbol=None,
        category="cash",
        side=None,
        location=location,
        current_qty=amount_usdt,
        current_size=None,
        current_usd_value=amount_usdt,
        current_notional_usd=None,
        source_weight=None,
        target_cash_usdt=target_cash,
        target_qty=None,
        expected_cash_delta_usdt=expected_cash_delta,
        eligible=eligible,
        eligibility_reason=reason,
        use_for_deficit_cover=use_for_deficit_cover,
        instrument_status=instrument_status,
        min_order_passed=True if eligible else None,
        liquidity_check_required=False,
        margin_guard_required=False,
        planned_execution_mode="cash_source_only",
        status=status,
        error=None,
        raw={},
    )


def _build_cash_like_legs(snapshot: NegativeSaleSnapshot) -> list[SaleLegPlan]:
    legs: list[SaleLegPlan] = []

    if snapshot.unified_usdt_available > ZERO:
        legs.append(
            _cash_like_leg(
                leg_type="unified_usdt_cash",
                location="UNIFIED",
                amount_usdt=snapshot.unified_usdt_available,
                status=SALE_LEG_STATUS_CASH_AVAILABLE,
                reason="Unified USDT cash reduces sale target.",
            )
        )

    if snapshot.fund_wallet_usdt_available > ZERO:
        legs.append(
            _cash_like_leg(
                leg_type="fund_wallet_usdt_cash",
                location="FUND_WALLET",
                amount_usdt=snapshot.fund_wallet_usdt_available,
                status=SALE_LEG_STATUS_CASH_AVAILABLE,
                reason="Fund wallet USDT cash reduces sale target.",
            )
        )

    if snapshot.usdt_earn_available > ZERO:
        usdt_earn_used_as_buffer = snapshot.usdt_earn_used_as_buffer()

        if usdt_earn_used_as_buffer > ZERO:
            legs.append(
                _cash_like_leg(
                    leg_type="usdt_earn_buffer",
                    location="EARN",
                    amount_usdt=snapshot.usdt_earn_available,
                    target_cash_usdt=usdt_earn_used_as_buffer,
                    expected_cash_delta_usdt=usdt_earn_used_as_buffer,
                    status=SALE_LEG_STATUS_BUFFER_AVAILABLE,
                    reason=(
                        "USDT Earn is planned as redeemable buffer only. "
                        f"available={snapshot.usdt_earn_available}, "
                        f"redeemable={snapshot.usdt_earn_redeemable}, "
                        f"used_as_buffer={usdt_earn_used_as_buffer}. "
                        "No Earn redeem is executed in Stage 23.2."
                    ),
                )
            )
        else:
            legs.append(
                _cash_like_leg(
                    leg_type="usdt_earn_buffer",
                    location="EARN",
                    amount_usdt=snapshot.usdt_earn_available,
                    target_cash_usdt=ZERO,
                    expected_cash_delta_usdt=ZERO,
                    status=SALE_LEG_STATUS_SKIPPED_NOT_ELIGIBLE,
                    reason=(
                        "usdt_earn_not_redeemable: USDT Earn balance exists, "
                        "but redeemable amount is zero or missing. "
                        f"available={snapshot.usdt_earn_available}, "
                        f"redeemable={snapshot.usdt_earn_redeemable}."
                    ),
                    eligible=False,
                    use_for_deficit_cover=False,
                    instrument_status="not_redeemable",
                )
            )

    return legs


def _asset_sale_leg_type(asset: NegativeSaleAsset) -> str:
    if asset.asset_type == "spot":
        return "spot_sell"

    if asset.asset_type == "non_stable_earn":
        return "earn_redeem_then_sell"

    if asset.asset_type == "perp_future":
        return "perp_future_reduce"

    if asset.asset_type == "long_option":
        return "long_option_sell"

    if asset.asset_type == "short_option":
        return "short_option_buyback"

    return "unknown_asset"


def _asset_leg_group(asset: NegativeSaleAsset) -> str:
    if asset.asset_type in {"spot", "non_stable_earn"}:
        return "asset_sale"

    if asset.asset_type in {"perp_future", "long_option", "short_option"}:
        return "derivative_reduce"

    return "unknown"


def _asset_planned_execution_mode(asset: NegativeSaleAsset) -> str:
    if asset.asset_type == "spot":
        return "mock_spot_market_sell"

    if asset.asset_type == "non_stable_earn":
        return "mock_earn_redeem_then_spot_sell"

    if asset.asset_type == "perp_future":
        return "mock_derivative_reduce"

    if asset.asset_type == "long_option":
        return "mock_long_option_sell"

    if asset.asset_type == "short_option":
        return "mock_short_option_buyback"

    return "mock_plan_only"


def _asset_liquidity_required(asset: NegativeSaleAsset) -> bool:
    return asset.asset_type in {
        "spot",
        "non_stable_earn",
        "perp_future",
        "long_option",
        "short_option",
    }


def _asset_margin_required(asset: NegativeSaleAsset) -> bool:
    return asset.asset_type in {
        "perp_future",
        "long_option",
        "short_option",
    }


def _asset_eligible_for_deficit_cover(asset: NegativeSaleAsset) -> bool:
    if asset.asset_type == "short_option":
        return False

    return asset.asset_type in {
        "spot",
        "non_stable_earn",
        "perp_future",
        "long_option",
    }


def _asset_skip_reason(asset: NegativeSaleAsset) -> tuple[str, str] | None:
    value = _asset_value(asset)

    if value <= ZERO:
        return (
            SALE_LEG_STATUS_SKIPPED_ZERO_VALUE,
            "Asset has zero or negative USD value.",
        )

    symbol = _map_symbol_from_coin(asset.coin, asset.symbol)
    if symbol is None and asset.asset_type not in {"short_option"}:
        return (
            SALE_LEG_STATUS_SKIPPED_NOT_ELIGIBLE,
            "Asset cannot be mapped to a tradable symbol.",
        )

    if not _is_trading_status(asset.instrument_status):
        return (
            SALE_LEG_STATUS_SKIPPED_SYMBOL_NOT_TRADING,
            f"Instrument is not trading: {asset.instrument_status}",
        )

    return None
def _target_qty_for_asset(
    *,
    asset: NegativeSaleAsset,
    target_cash_usdt: Decimal,
    value_usdt: Decimal,
) -> Decimal | None:
    if value_usdt <= ZERO or target_cash_usdt <= ZERO:
        return None

    qty = asset.qty
    if qty is None:
        qty = asset.size

    if qty is None:
        return None

    return dec(qty) * target_cash_usdt / value_usdt


def _build_skipped_asset_leg(
    *,
    asset: NegativeSaleAsset,
    status: str,
    reason: str,
) -> SaleLegPlan:
    value = _asset_value(asset)

    return SaleLegPlan(
        leg_group=_asset_leg_group(asset),
        leg_type=_asset_sale_leg_type(asset),
        coin=asset.coin,
        symbol=_map_symbol_from_coin(asset.coin, asset.symbol),
        category=asset.category,
        side=asset.side,
        location=asset.location,
        current_qty=asset.qty,
        current_size=asset.size,
        current_usd_value=value,
        current_notional_usd=asset.notional_usd,
        source_weight=None,
        target_cash_usdt=ZERO,
        target_qty=None,
        expected_cash_delta_usdt=ZERO,
        eligible=False,
        eligibility_reason=reason,
        use_for_deficit_cover=False,
        instrument_status=asset.instrument_status,
        min_order_passed=False if status == SALE_LEG_STATUS_SKIPPED_MIN_ORDER else None,
        liquidity_check_required=_asset_liquidity_required(asset),
        margin_guard_required=_asset_margin_required(asset),
        planned_execution_mode="mock_plan_only",
        status=status,
        error=None,
        raw=asset.to_dict(),
    )


def _build_planned_asset_leg(
    *,
    asset: NegativeSaleAsset,
    source_weight: Decimal,
    target_cash_usdt: Decimal,
) -> SaleLegPlan:
    value = _asset_value(asset)
    use_for_deficit_cover = _asset_eligible_for_deficit_cover(asset)

    if asset.asset_type == "short_option":
        expected_cash_delta_usdt = ZERO
        target_cash_usdt = ZERO
        target_qty = None
        reason = (
            "Short option buyback is planned for audit only and is not used "
            "for deficit cover by default because cash effect is uncertain."
        )
    else:
        expected_cash_delta_usdt = target_cash_usdt
        target_qty = _target_qty_for_asset(
            asset=asset,
            target_cash_usdt=target_cash_usdt,
            value_usdt=value,
        )
        reason = "Eligible cash-generating source for negative-net sale plan."

    return SaleLegPlan(
        leg_group=_asset_leg_group(asset),
        leg_type=_asset_sale_leg_type(asset),
        coin=asset.coin,
        symbol=_map_symbol_from_coin(asset.coin, asset.symbol),
        category=asset.category,
        side=asset.side,
        location=asset.location,
        current_qty=asset.qty,
        current_size=asset.size,
        current_usd_value=value,
        current_notional_usd=asset.notional_usd,
        source_weight=source_weight if use_for_deficit_cover else None,
        target_cash_usdt=target_cash_usdt,
        target_qty=target_qty,
        expected_cash_delta_usdt=expected_cash_delta_usdt,
        eligible=True,
        eligibility_reason=reason,
        use_for_deficit_cover=use_for_deficit_cover,
        instrument_status=asset.instrument_status,
        min_order_passed=(
            target_cash_usdt >= MIN_PLANNED_SALE_LEG_USDT
            if use_for_deficit_cover
            else None
        ),
        liquidity_check_required=_asset_liquidity_required(asset),
        margin_guard_required=_asset_margin_required(asset),
        planned_execution_mode=_asset_planned_execution_mode(asset),
        status=SALE_LEG_STATUS_PLANNED,
        error=None,
        raw=asset.to_dict(),
    )


def _collect_deficit_cover_assets(snapshot: NegativeSaleSnapshot) -> list[NegativeSaleAsset]:
    assets: list[NegativeSaleAsset] = []

    for asset in snapshot.all_assets():
        if not _asset_eligible_for_deficit_cover(asset):
            continue

        if _asset_skip_reason(asset) is not None:
            continue

        assets.append(asset)

    return assets


def _build_asset_sale_legs(
    *,
    snapshot: NegativeSaleSnapshot,
    sale_target_usdt: Decimal,
) -> list[SaleLegPlan]:
    legs: list[SaleLegPlan] = []

    if sale_target_usdt <= ZERO:
        return legs

    eligible_sources = _collect_deficit_cover_assets(snapshot)
    total_eligible_value = sum((_asset_value(item) for item in eligible_sources), ZERO)

    for asset in snapshot.all_assets():
        skip = _asset_skip_reason(asset)
        if skip is not None:
            status, reason = skip
            legs.append(
                _build_skipped_asset_leg(
                    asset=asset,
                    status=status,
                    reason=reason,
                )
            )
            continue

        if asset.asset_type == "short_option":
            legs.append(
                _build_planned_asset_leg(
                    asset=asset,
                    source_weight=ZERO,
                    target_cash_usdt=ZERO,
                )
            )
            continue

        if not _asset_eligible_for_deficit_cover(asset):
            legs.append(
                _build_skipped_asset_leg(
                    asset=asset,
                    status=SALE_LEG_STATUS_SKIPPED_NOT_ELIGIBLE,
                    reason="Asset type is not eligible for deficit cover.",
                )
            )
            continue

        value = _asset_value(asset)
        if total_eligible_value <= ZERO:
            legs.append(
                _build_skipped_asset_leg(
                    asset=asset,
                    status=SALE_LEG_STATUS_SKIPPED_NOT_ELIGIBLE,
                    reason="No eligible positive-value source exists.",
                )
            )
            continue

        source_weight = value / total_eligible_value
        target_cash_usdt = min(value, sale_target_usdt * source_weight)

        if target_cash_usdt <= ZERO:
            legs.append(
                _build_skipped_asset_leg(
                    asset=asset,
                    status=SALE_LEG_STATUS_SKIPPED_ZERO_VALUE,
                    reason="Calculated target cash is zero.",
                )
            )
            continue

        if target_cash_usdt < MIN_PLANNED_SALE_LEG_USDT:
            legs.append(
                _build_skipped_asset_leg(
                    asset=asset,
                    status=SALE_LEG_STATUS_SKIPPED_MIN_ORDER,
                    reason=(
                        "Calculated target cash is below Stage 23.2 "
                        f"minimum planning threshold {MIN_PLANNED_SALE_LEG_USDT} USDT."
                    ),
                )
            )
            continue

        legs.append(
            _build_planned_asset_leg(
                asset=asset,
                source_weight=source_weight,
                target_cash_usdt=target_cash_usdt,
            )
        )

    return legs
def _compute_negative_sale_plan(
    *,
    settlement_batch: FundSettlementBatch,
    snapshot: NegativeSaleSnapshot,
) -> NegativeSalePlanComputation:
    required_master_usdt = dec(settlement_batch.required_master_usdt)
    withdrawal_request_amount_usdt = dec(settlement_batch.withdrawal_request_amount_usdt)
    total_net_user_payout_usdt = dec(settlement_batch.total_net_user_payout_usdt)
    total_partial_month_fee_usdt = dec(settlement_batch.total_partial_month_fee_usdt)
    bybit_withdrawal_fee_usdt = dec(settlement_batch.bybit_withdrawal_fee_usdt)

    unified_usdt_available = snapshot.unified_usdt_available
    fund_wallet_usdt_available = snapshot.fund_wallet_usdt_available
    usdt_earn_available = snapshot.usdt_earn_available
    usdt_earn_redeemable = snapshot.usdt_earn_redeemable
    usdt_earn_used_as_buffer = snapshot.usdt_earn_used_as_buffer()
    cash_like_available_for_plan = snapshot.total_cash_like_available_usdt()
    total_cash_like_available_usdt = cash_like_available_for_plan

    sale_target_usdt = _max_zero(
        required_master_usdt
        - unified_usdt_available
        - fund_wallet_usdt_available
        - usdt_earn_used_as_buffer
    )

    cash_like_legs = _build_cash_like_legs(snapshot)
    asset_sale_legs = _build_asset_sale_legs(
        snapshot=snapshot,
        sale_target_usdt=sale_target_usdt,
    )

    legs = [*cash_like_legs, *asset_sale_legs]

    planned_sale_usdt = sum(
        (
            dec(leg.expected_cash_delta_usdt)
            for leg in legs
            if leg.use_for_deficit_cover
            and leg.status == SALE_LEG_STATUS_PLANNED
        ),
        ZERO,
    )

    expected_shortage_usdt = _max_zero(sale_target_usdt - planned_sale_usdt)
    expected_surplus_usdt = _max_zero(planned_sale_usdt - sale_target_usdt)

    largest_extra_sale_buffer_pct = dec(
        settings.NEGATIVE_NET_EXTRA_LARGEST_ASSET_BUFFER_PCT
    )

    plan_json = {
        "formula": {
            "sale_target_usdt": (
                "max(required_master_usdt - unified_usdt_available "
                "- fund_wallet_usdt_available - usdt_earn_used_as_buffer, 0)"
            ),
            "required_master_usdt": str(required_master_usdt),
            "unified_usdt_available": str(unified_usdt_available),
            "fund_wallet_usdt_available": str(fund_wallet_usdt_available),
            "usdt_earn_available": str(usdt_earn_available),
            "usdt_earn_redeemable": str(usdt_earn_redeemable),
            "usdt_earn_used_as_buffer": str(usdt_earn_used_as_buffer),
            "cash_like_available_for_plan": str(cash_like_available_for_plan),
            "total_cash_like_available_usdt": str(total_cash_like_available_usdt),
        },
        "targets": {
            "sale_target_usdt": str(sale_target_usdt),
            "planned_sale_usdt": str(planned_sale_usdt),
            "expected_shortage_usdt": str(expected_shortage_usdt),
            "expected_surplus_usdt": str(expected_surplus_usdt),
            "largest_extra_sale_buffer_pct": str(largest_extra_sale_buffer_pct),
        },
        "settlement": {
            "withdrawal_request_amount_usdt": str(withdrawal_request_amount_usdt),
            "total_net_user_payout_usdt": str(total_net_user_payout_usdt),
            "total_partial_month_fee_usdt": str(total_partial_month_fee_usdt),
            "bybit_withdrawal_fee_usdt": str(bybit_withdrawal_fee_usdt),
        },
        "safety": {
            "mock_only": True,
            "no_real_bybit_calls": True,
            "no_trades": True,
            "no_transfers_or_withdrawals": True,
            "no_bsc_transfers": True,
            "no_accounting_finalization": True,
        },
        "legs": [leg.to_dict() for leg in legs],
    }

    return NegativeSalePlanComputation(
        required_master_usdt=required_master_usdt,
        withdrawal_request_amount_usdt=withdrawal_request_amount_usdt,
        total_net_user_payout_usdt=total_net_user_payout_usdt,
        total_partial_month_fee_usdt=total_partial_month_fee_usdt,
        bybit_withdrawal_fee_usdt=bybit_withdrawal_fee_usdt,
        unified_usdt_available=unified_usdt_available,
        fund_wallet_usdt_available=fund_wallet_usdt_available,
        usdt_earn_available=usdt_earn_available,
        usdt_earn_redeemable=usdt_earn_redeemable,
        usdt_earn_used_as_buffer=usdt_earn_used_as_buffer,
        cash_like_available_for_plan=cash_like_available_for_plan,
        total_cash_like_available_usdt=total_cash_like_available_usdt,
        sale_target_usdt=sale_target_usdt,
        planned_sale_usdt=planned_sale_usdt,
        expected_shortage_usdt=expected_shortage_usdt,
        expected_surplus_usdt=expected_surplus_usdt,
        largest_extra_sale_buffer_pct=largest_extra_sale_buffer_pct,
        legs=legs,
        plan_json=plan_json,
    )


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
        raise NegativeSalePlanError(
            f"Settlement batch not found: {settlement_batch_id}"
        )

    return batch


def _get_fund(
    db: Session,
    *,
    fund_id: int,
) -> Fund:
    fund = db.query(Fund).filter(Fund.id == int(fund_id)).first()

    if fund is None:
        raise NegativeSalePlanError(f"Fund not found: {fund_id}")

    return fund


def _validate_settlement_batch_for_sale_plan(batch: FundSettlementBatch) -> None:
    allowed_statuses = {
        BATCH_STATUS_NEGATIVE_NET_TARGETS_CALCULATED,
        BATCH_STATUS_NEGATIVE_NET_SALE_PLANNED,
    }

    if batch.status not in allowed_statuses:
        raise NegativeSalePlanError(
            "Settlement batch must be negative_net_targets_calculated "
            "or negative_net_sale_planned for idempotent rerun: "
            f"batch_id={batch.id}, status={batch.status}"
        )

    required_fields = [
        "required_master_usdt",
        "withdrawal_request_amount_usdt",
        "total_net_user_payout_usdt",
        "total_partial_month_fee_usdt",
        "bybit_withdrawal_fee_usdt",
    ]

    missing = [
        field
        for field in required_fields
        if getattr(batch, field, None) is None
    ]

    if missing:
        raise NegativeSalePlanError(
            f"Settlement batch Stage 23.1 fields are missing: {missing}"
        )

    if dec(batch.required_master_usdt) < ZERO:
        raise NegativeSalePlanError("required_master_usdt must be non-negative")

    if dec(batch.withdrawal_request_amount_usdt) < ZERO:
        raise NegativeSalePlanError(
            "withdrawal_request_amount_usdt must be non-negative"
        )


def _get_or_create_sale_batch(
    db: Session,
    *,
    settlement_batch: FundSettlementBatch,
    now: datetime,
) -> FundNegativeSaleBatch:
    sale_batch = (
        db.query(FundNegativeSaleBatch)
        .filter(
            FundNegativeSaleBatch.settlement_batch_id == settlement_batch.id,
        )
        .with_for_update()
        .first()
    )

    if sale_batch is not None:
        return sale_batch

    sale_batch = FundNegativeSaleBatch(
        settlement_batch_id=settlement_batch.id,
        fund_id=settlement_batch.fund_id,
        status=SALE_BATCH_STATUS_SNAPSHOT_CREATED,
        snapshot_created_at=now,
        created_at=now,
        updated_at=now,
    )

    db.add(sale_batch)
    db.flush()

    return sale_batch


def _delete_existing_sale_legs(
    db: Session,
    *,
    sale_batch_id: int,
) -> None:
    (
        db.query(FundNegativeSaleLeg)
        .filter(FundNegativeSaleLeg.sale_batch_id == int(sale_batch_id))
        .delete(synchronize_session=False)
    )
    
def _apply_sale_batch_computation(
    *,
    sale_batch: FundNegativeSaleBatch,
    computation: NegativeSalePlanComputation,
    snapshot: NegativeSaleSnapshot,
    now: datetime,
) -> None:
    sale_batch.status = SALE_BATCH_STATUS_SALE_PLAN_CREATED

    sale_batch.required_master_usdt = computation.required_master_usdt
    sale_batch.withdrawal_request_amount_usdt = computation.withdrawal_request_amount_usdt
    sale_batch.total_net_user_payout_usdt = computation.total_net_user_payout_usdt
    sale_batch.total_partial_month_fee_usdt = computation.total_partial_month_fee_usdt
    sale_batch.bybit_withdrawal_fee_usdt = computation.bybit_withdrawal_fee_usdt

    sale_batch.unified_usdt_available = computation.unified_usdt_available
    sale_batch.fund_wallet_usdt_available = computation.fund_wallet_usdt_available
    sale_batch.usdt_earn_available = computation.usdt_earn_available
    sale_batch.total_cash_like_available_usdt = computation.total_cash_like_available_usdt

    sale_batch.sale_target_usdt = computation.sale_target_usdt
    sale_batch.planned_sale_usdt = computation.planned_sale_usdt
    sale_batch.expected_shortage_usdt = computation.expected_shortage_usdt
    sale_batch.expected_surplus_usdt = computation.expected_surplus_usdt
    sale_batch.largest_extra_sale_buffer_pct = computation.largest_extra_sale_buffer_pct

    sale_batch.snapshot_json = snapshot.to_dict()
    sale_batch.plan_json = computation.plan_json
    sale_batch.report_json = computation.to_dict()

    sale_batch.error = None
    sale_batch.snapshot_created_at = sale_batch.snapshot_created_at or now
    sale_batch.plan_created_at = now
    sale_batch.updated_at = now


def _create_sale_leg_rows(
    db: Session,
    *,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    computation: NegativeSalePlanComputation,
    now: datetime,
) -> None:
    for index, leg in enumerate(computation.legs, start=1):
        row = FundNegativeSaleLeg(
            sale_batch_id=sale_batch.id,
            settlement_batch_id=settlement_batch.id,
            fund_id=settlement_batch.fund_id,
            leg_index=index,
            leg_group=leg.leg_group,
            leg_type=leg.leg_type,
            coin=leg.coin,
            symbol=leg.symbol,
            category=leg.category,
            side=leg.side,
            location=leg.location,
            current_qty=leg.current_qty,
            current_size=leg.current_size,
            current_usd_value=leg.current_usd_value,
            current_notional_usd=leg.current_notional_usd,
            source_weight=leg.source_weight,
            target_cash_usdt=leg.target_cash_usdt,
            target_qty=leg.target_qty,
            expected_cash_delta_usdt=leg.expected_cash_delta_usdt,
            eligible=leg.eligible,
            eligibility_reason=leg.eligibility_reason,
            use_for_deficit_cover=leg.use_for_deficit_cover,
            instrument_status=leg.instrument_status,
            min_order_passed=leg.min_order_passed,
            liquidity_check_required=leg.liquidity_check_required,
            margin_guard_required=leg.margin_guard_required,
            planned_execution_mode=leg.planned_execution_mode,
            order_link_id=None,
            strategy_id=None,
            status=leg.status,
            error=leg.error,
            created_at=now,
            updated_at=now,
        )
        db.add(row)


def _mark_sale_plan_failed_requires_review(
    db: Session,
    *,
    settlement_batch: FundSettlementBatch,
    sale_batch: FundNegativeSaleBatch | None,
    fund: Fund,
    status_before: str,
    error: str,
    now: datetime,
) -> NegativeSalePlanResult:
    settlement_batch.status = BATCH_STATUS_FAILED_REQUIRES_REVIEW
    settlement_batch.error = error
    settlement_batch.updated_at = now

    sale_batch_id = None
    sale_batch_status = None

    if sale_batch is not None:
        sale_batch.status = SALE_BATCH_STATUS_SALE_PLAN_FAILED_REQUIRES_REVIEW
        sale_batch.error = error
        sale_batch.updated_at = now
        sale_batch_id = sale_batch.id
        sale_batch_status = sale_batch.status
        db.add(sale_batch)

    db.add(settlement_batch)
    db.flush()

    return NegativeSalePlanResult(
        ok=False,
        settlement_batch_id=settlement_batch.id,
        sale_batch_id=sale_batch_id,
        fund_id=settlement_batch.fund_id,
        fund_code=str(fund.code),
        status_before=status_before,
        status_after=settlement_batch.status,
        sale_batch_status=sale_batch_status,
        sale_target_usdt=None,
        planned_sale_usdt=None,
        leg_count=0,
        error=error,
        diagnostics={
            "controlled_failure": True,
            "no_real_bybit_calls": True,
            "no_trades": True,
            "no_transfers_or_withdrawals": True,
            "no_bsc_transfers": True,
            "no_accounting_finalization": True,
        },
    )


def create_negative_sale_plan(
    db: Session,
    *,
    settlement_batch_id: int,
    snapshot: NegativeSaleSnapshot,
    now: datetime | None = None,
) -> NegativeSalePlanResult:
    """
    Stage 23.2 negative-net sell-side snapshot and sale plan.

    Safety policy:
    - no real Bybit calls;
    - no trades;
    - no Bybit transfers/withdrawals;
    - no BSC transfers;
    - no accounting finalization;
    - no user position mutation;
    - no shares_outstanding mutation;
    - no order success finalization.
    """
    now = now or utcnow()

    settlement_batch = _lock_settlement_batch(
        db,
        settlement_batch_id=settlement_batch_id,
    )
    status_before = str(settlement_batch.status)

    fund = _get_fund(
        db,
        fund_id=settlement_batch.fund_id,
    )

    sale_batch: FundNegativeSaleBatch | None = None

    try:
        _validate_settlement_batch_for_sale_plan(settlement_batch)

        sale_batch = _get_or_create_sale_batch(
            db,
            settlement_batch=settlement_batch,
            now=now,
        )

        computation = _compute_negative_sale_plan(
            settlement_batch=settlement_batch,
            snapshot=snapshot,
        )

        _delete_existing_sale_legs(
            db,
            sale_batch_id=sale_batch.id,
        )

        _apply_sale_batch_computation(
            sale_batch=sale_batch,
            computation=computation,
            snapshot=snapshot,
            now=now,
        )

        _create_sale_leg_rows(
            db,
            sale_batch=sale_batch,
            settlement_batch=settlement_batch,
            computation=computation,
            now=now,
        )

        settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_SALE_PLANNED
        settlement_batch.updated_at = now
        settlement_batch.error = None

        db.add(sale_batch)
        db.add(settlement_batch)
        db.flush()

        return NegativeSalePlanResult(
            ok=True,
            settlement_batch_id=settlement_batch.id,
            sale_batch_id=sale_batch.id,
            fund_id=settlement_batch.fund_id,
            fund_code=str(fund.code),
            status_before=status_before,
            status_after=settlement_batch.status,
            sale_batch_status=sale_batch.status,
            sale_target_usdt=computation.sale_target_usdt,
            planned_sale_usdt=computation.planned_sale_usdt,
            leg_count=len(computation.legs),
            error=None,
            diagnostics={
                "sale_target_formula": (
                    "max(required_master_usdt - unified_usdt_available "
                    "- fund_wallet_usdt_available - usdt_earn_used_as_buffer, 0)"
                ),
                "usdt_earn_available": str(computation.usdt_earn_available),
                "usdt_earn_redeemable": str(computation.usdt_earn_redeemable),
                "usdt_earn_used_as_buffer": str(computation.usdt_earn_used_as_buffer),
                "cash_like_available_for_plan": str(
                    computation.cash_like_available_for_plan
                ),
                "total_cash_like_available_usdt": str(
                    computation.total_cash_like_available_usdt
                ),
                "expected_shortage_usdt": str(computation.expected_shortage_usdt),
                "expected_surplus_usdt": str(computation.expected_surplus_usdt),
                "no_real_bybit_calls": True,
                "no_trades": True,
                "no_transfers_or_withdrawals": True,
                "no_bsc_transfers": True,
                "no_accounting_finalization": True,
            },
        )

    except NegativeSalePlanError as exc:
        return _mark_sale_plan_failed_requires_review(
            db,
            settlement_batch=settlement_batch,
            sale_batch=sale_batch,
            fund=fund,
            status_before=status_before,
            error=str(exc),
            now=now,
        )