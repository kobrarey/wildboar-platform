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
from app.settlement.negative_sale_planning_policy import (
    DERIVATIVE_REDUCTION_POLICY_VERSION,
    NegativeSalePlanningPolicyError,
    ProportionalDerivativeReduction,
    compute_proportional_derivative_reduction,
    derivative_close_side,
    derivative_raw_target_qty,
    is_derivative_asset,
    normalize_asset_order_quantity,
    normalize_position_side,
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

    position_side: str | None = None
    close_side: str | None = None
    position_idx: int | None = None

    exposure_notional_usdt: (
        Decimal | None
    ) = None
    confirmed_cash_delta_usdt: (
        Decimal | None
    ) = None

    order_quantity_preflight: dict[
        str,
        Any,
    ] = field(default_factory=dict)

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

    derivative_reduction_json: dict[
        str,
        Any,
    ]

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


def _is_live_bybit_snapshot(
    snapshot: NegativeSaleSnapshot,
) -> bool:
    raw = snapshot.raw_snapshot_json

    if not isinstance(raw, dict):
        return False

    return (
        str(raw.get("source") or "")
        .strip()
        .lower()
        == "bybit_readonly"
    )


def _asset_position_side(
    asset: NegativeSaleAsset,
) -> str | None:
    return normalize_position_side(
        asset.position_side
        or asset.side
    )


def _asset_close_side(
    asset: NegativeSaleAsset,
) -> str:
    if is_derivative_asset(asset):
        return derivative_close_side(
            _asset_position_side(asset)
        )

    return "Sell"


def _safe_asset_close_side(
    asset: NegativeSaleAsset,
) -> str | None:
    try:
        return _asset_close_side(asset)
    except (
        NegativeSalePlanningPolicyError,
        ValueError,
    ):
        return None


def _asset_available_qty(
    asset: NegativeSaleAsset,
) -> Decimal | None:
    value = (
        asset.qty
        if asset.qty is not None
        else asset.size
    )

    if value is None:
        return None

    return dec(value)


def _asset_instrument_preflight_complete(
    asset: NegativeSaleAsset,
) -> bool:
    info = asset.instrument_info

    if not isinstance(info, dict):
        return False

    return (
        info.get("preflight_complete")
        is True
    )


def _asset_instrument_reasons(
    asset: NegativeSaleAsset,
) -> list[str]:
    info = asset.instrument_info

    if not isinstance(info, dict):
        return [
            "instrument_snapshot_missing"
        ]

    raw_reasons = info.get(
        "completeness_reasons"
    )

    if not isinstance(
        raw_reasons,
        list | tuple,
    ):
        return []

    return [
        str(reason)
        for reason in raw_reasons
    ]


def _normalize_asset_target_qty(
    *,
    asset: NegativeSaleAsset,
    requested_qty: Decimal,
    strict_instrument_preflight: bool,
) -> tuple[
    Decimal | None,
    dict[str, Any],
]:
    available_qty = _asset_available_qty(
        asset
    )
    close_side = _asset_close_side(
        asset
    )

    if (
        not strict_instrument_preflight
        and not asset.instrument_info
    ):
        capped = (
            min(
                requested_qty,
                available_qty,
            )
            if available_qty is not None
            else requested_qty
        )

        return (
            capped,
            {
                "eligible": capped > ZERO,
                "legacy_mock_without_instrument": True,
                "requested_qty": str(
                    requested_qty
                ),
                "available_qty": (
                    str(available_qty)
                    if available_qty is not None
                    else None
                ),
                "normalized_qty": str(
                    capped
                ),
                "slices": (
                    [str(capped)]
                    if capped > ZERO
                    else []
                ),
                "reasons": (
                    []
                    if capped > ZERO
                    else [
                        "qty_not_positive",
                    ]
                ),
            },
        )

    try:
        normalized = (
            normalize_asset_order_quantity(
                asset=asset,
                requested_qty=requested_qty,
                available_qty=available_qty,
                close_side=close_side,
            )
        )
    except Exception as exc:
        return (
            None,
            {
                "eligible": False,
                "requested_qty": str(
                    requested_qty
                ),
                "available_qty": (
                    str(available_qty)
                    if available_qty is not None
                    else None
                ),
                "normalized_qty": "0",
                "slices": [],
                "reasons": [str(exc)],
            },
        )

    return (
        (
            normalized.normalized_qty
            if normalized.eligible
            else None
        ),
        normalized.to_dict(),
    )


def _is_trading_status(
    status: str | None,
) -> bool:
    if status is None:
        return False

    return (
        str(status).strip().lower()
        == "trading"
    )


def _map_symbol_from_coin(coin: str | None, symbol: str | None) -> str | None:
    if symbol:
        return symbol.upper()

    if not coin:
        return None

    clean = coin.upper()
    if clean in {"USDT", "USDC", "USD"}:
        return None

    return f"{clean}USDT"


def _asset_value(
    asset: NegativeSaleAsset,
) -> Decimal:
    if is_derivative_asset(asset):
        return ZERO

    value = dec(asset.usd_value)

    if value > ZERO:
        return value

    if (
        asset.asset_type
        == "non_stable_earn"
        and asset.redeemable_usdt
        is not None
    ):
        return max(
            dec(asset.redeemable_usdt),
            ZERO,
        )

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
                amount_usdt=(
                    snapshot
                    .fund_wallet_usdt_available
                ),
                target_cash_usdt=ZERO,
                expected_cash_delta_usdt=ZERO,
                status=(
                    SALE_LEG_STATUS_SKIPPED_NOT_ELIGIBLE
                ),
                reason=(
                    "requires_fund_to_unified_"
                    "transfer_task3"
                ),
                eligible=False,
                use_for_deficit_cover=False,
                instrument_status=(
                    "requires_task3_transfer"
                ),
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


def _asset_eligible_for_deficit_cover(
    asset: NegativeSaleAsset,
) -> bool:
    if (
        asset.requires_fund_to_unified_transfer
        or (
            asset.location or ""
        ).upper()
        in {
            "FUND",
            "FUND_WALLET",
        }
    ):
        return False

    if is_derivative_asset(asset):
        return False

    if (
        asset.use_for_deficit_cover
        is False
    ):
        return False

    return asset.asset_type in {
        "spot",
        "non_stable_earn",
    }


def _asset_skip_reason(
    asset: NegativeSaleAsset,
    *,
    strict_instrument_preflight: bool,
) -> tuple[str, str] | None:
    if (
        asset.requires_fund_to_unified_transfer
        or (
            asset.location or ""
        ).upper()
        in {
            "FUND",
            "FUND_WALLET",
        }
    ):
        return (
            SALE_LEG_STATUS_SKIPPED_NOT_ELIGIBLE,
            (
                asset.eligibility_reason
                or (
                    "requires_fund_to_unified_"
                    "transfer_task3"
                )
            ),
        )

    if not is_derivative_asset(asset):
        value = _asset_value(asset)

        if value <= ZERO:
            return (
                SALE_LEG_STATUS_SKIPPED_ZERO_VALUE,
                (
                    "Asset has zero or negative "
                    "cash-generating USD value."
                ),
            )

    symbol = _map_symbol_from_coin(
        asset.coin,
        asset.symbol,
    )

    if symbol is None:
        return (
            SALE_LEG_STATUS_SKIPPED_NOT_ELIGIBLE,
            (
                "Asset cannot be mapped to "
                "a tradable symbol."
            ),
        )

    if strict_instrument_preflight:
        if not _is_trading_status(
            asset.instrument_status
        ):
            return (
                SALE_LEG_STATUS_SKIPPED_SYMBOL_NOT_TRADING,
                (
                    "Instrument status is not "
                    f"Trading: {asset.instrument_status!r}"
                ),
            )

        if not (
            _asset_instrument_preflight_complete(
                asset
            )
        ):
            return (
                SALE_LEG_STATUS_SKIPPED_NOT_ELIGIBLE,
                (
                    "Instrument preflight is "
                    "incomplete: "
                    + ",".join(
                        _asset_instrument_reasons(
                            asset
                        )
                    )
                ),
            )

    elif (
        asset.instrument_status is not None
        and not _is_trading_status(
            asset.instrument_status
        )
    ):
        return (
            SALE_LEG_STATUS_SKIPPED_SYMBOL_NOT_TRADING,
            (
                "Instrument is not trading: "
                f"{asset.instrument_status}"
            ),
        )

    if is_derivative_asset(asset):
        try:
            _asset_close_side(asset)
        except (
            NegativeSalePlanningPolicyError,
            ValueError,
        ) as exc:
            return (
                SALE_LEG_STATUS_SKIPPED_NOT_ELIGIBLE,
                (
                    "Derivative side cannot be "
                    f"closed safely: {exc}"
                ),
            )

        if _asset_available_qty(asset) is None:
            return (
                SALE_LEG_STATUS_SKIPPED_NOT_ELIGIBLE,
                (
                    "Derivative current position "
                    "size is missing."
                ),
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
    order_quantity_preflight: (
        dict[str, Any] | None
    ) = None,
) -> SaleLegPlan:
    close_side = _safe_asset_close_side(
        asset
    )
    position_side = (
        _asset_position_side(asset)
        if is_derivative_asset(asset)
        else None
    )

    current_value = (
        max(dec(asset.usd_value), ZERO)
        if is_derivative_asset(asset)
        else _asset_value(asset)
    )

    preflight = dict(
        order_quantity_preflight or {}
    )

    raw = asset.to_dict()
    raw.update(
        {
            "position_side": position_side,
            "close_side": close_side,
            "position_idx": (
                asset.position_idx
            ),
            "order_quantity_preflight": (
                preflight
            ),
        }
    )

    return SaleLegPlan(
        leg_group=_asset_leg_group(asset),
        leg_type=_asset_sale_leg_type(
            asset
        ),
        coin=asset.coin,
        symbol=_map_symbol_from_coin(
            asset.coin,
            asset.symbol,
        ),
        category=asset.category,
        side=close_side or asset.side,
        location=asset.location,
        current_qty=asset.qty,
        current_size=asset.size,
        current_usd_value=current_value,
        current_notional_usd=(
            asset.notional_usd
        ),
        source_weight=None,
        target_cash_usdt=ZERO,
        target_qty=None,
        expected_cash_delta_usdt=ZERO,
        eligible=False,
        eligibility_reason=reason,
        use_for_deficit_cover=False,
        instrument_status=(
            asset.instrument_status
        ),
        min_order_passed=(
            False
            if status
            == SALE_LEG_STATUS_SKIPPED_MIN_ORDER
            else None
        ),
        liquidity_check_required=(
            _asset_liquidity_required(
                asset
            )
        ),
        margin_guard_required=(
            _asset_margin_required(asset)
        ),
        planned_execution_mode=(
            "mock_plan_only"
        ),
        status=status,
        error=None,
        raw=raw,
        position_side=position_side,
        close_side=close_side,
        position_idx=asset.position_idx,
        exposure_notional_usdt=(
            asset.exposure_notional_usdt
            if asset.exposure_notional_usdt
            is not None
            else asset.notional_usd
        ),
        confirmed_cash_delta_usdt=None,
        order_quantity_preflight=(
            preflight
        ),
    )


def _build_planned_asset_leg(
    *,
    asset: NegativeSaleAsset,
    source_weight: Decimal,
    target_cash_usdt: Decimal,
    target_qty: Decimal | None = None,
    expected_cash_delta_usdt: (
        Decimal | None
    ) = None,
    use_for_deficit_cover: (
        bool | None
    ) = None,
    reason: str | None = None,
    order_quantity_preflight: (
        dict[str, Any] | None
    ) = None,
) -> SaleLegPlan:
    derivative = is_derivative_asset(
        asset
    )
    close_side = _asset_close_side(asset)
    position_side = (
        _asset_position_side(asset)
        if derivative
        else None
    )

    resolved_use_for_deficit_cover = (
        _asset_eligible_for_deficit_cover(
            asset
        )
        if use_for_deficit_cover is None
        else bool(use_for_deficit_cover)
    )

    if target_qty is None and (
        target_cash_usdt > ZERO
        and not derivative
    ):
        target_qty = _target_qty_for_asset(
            asset=asset,
            target_cash_usdt=(
                target_cash_usdt
            ),
            value_usdt=_asset_value(asset),
        )

    resolved_expected_cash = (
        expected_cash_delta_usdt
        if expected_cash_delta_usdt
        is not None
        else (
            target_cash_usdt
            if resolved_use_for_deficit_cover
            else ZERO
        )
    )

    current_value = (
        max(dec(asset.usd_value), ZERO)
        if derivative
        else _asset_value(asset)
    )

    preflight = dict(
        order_quantity_preflight or {}
    )

    raw = asset.to_dict()
    raw.update(
        {
            "position_side": position_side,
            "close_side": close_side,
            "position_idx": (
                asset.position_idx
            ),
            "reduce_only": derivative,
            "market_unit": (
                "baseCoin"
                if asset.asset_type
                in {
                    "spot",
                    "non_stable_earn",
                }
                else None
            ),
            "order_quantity_preflight": (
                preflight
            ),
        }
    )

    return SaleLegPlan(
        leg_group=_asset_leg_group(asset),
        leg_type=_asset_sale_leg_type(
            asset
        ),
        coin=asset.coin,
        symbol=_map_symbol_from_coin(
            asset.coin,
            asset.symbol,
        ),
        category=asset.category,
        side=close_side,
        location=asset.location,
        current_qty=asset.qty,
        current_size=asset.size,
        current_usd_value=current_value,
        current_notional_usd=(
            asset.notional_usd
        ),
        source_weight=(
            source_weight
            if resolved_use_for_deficit_cover
            else None
        ),
        target_cash_usdt=(
            target_cash_usdt
        ),
        target_qty=target_qty,
        expected_cash_delta_usdt=(
            resolved_expected_cash
        ),
        eligible=True,
        eligibility_reason=(
            reason
            or (
                "Eligible cash-generating "
                "source for negative-net "
                "sale plan."
            )
        ),
        use_for_deficit_cover=(
            resolved_use_for_deficit_cover
        ),
        instrument_status=(
            asset.instrument_status
        ),
        min_order_passed=(
            bool(
                preflight.get(
                    "eligible",
                    True,
                )
            )
        ),
        liquidity_check_required=(
            _asset_liquidity_required(
                asset
            )
        ),
        margin_guard_required=(
            _asset_margin_required(asset)
        ),
        planned_execution_mode=(
            _asset_planned_execution_mode(
                asset
            )
        ),
        status=SALE_LEG_STATUS_PLANNED,
        error=None,
        raw=raw,
        position_side=position_side,
        close_side=close_side,
        position_idx=asset.position_idx,
        exposure_notional_usdt=(
            asset.exposure_notional_usdt
            if asset.exposure_notional_usdt
            is not None
            else asset.notional_usd
        ),
        confirmed_cash_delta_usdt=None,
        order_quantity_preflight=(
            preflight
        ),
    )


def _derivative_assets(
    snapshot: NegativeSaleSnapshot,
) -> list[NegativeSaleAsset]:
    return [
        *snapshot.perp_future_positions,
        *snapshot.long_options,
        *snapshot.short_options,
    ]


def _cash_deficit_assets(
    snapshot: NegativeSaleSnapshot,
) -> list[NegativeSaleAsset]:
    return [
        *snapshot.non_stable_earn_holdings,
        *snapshot.spot_holdings,
        *snapshot.funding_wallet_non_stable_assets,
    ]


def _preflight_skip_status(
    preflight: dict[str, Any],
) -> str:
    reasons = [
        str(reason).lower()
        for reason in (
            preflight.get("reasons")
            or []
        )
    ]

    if any(
        (
            "min" in reason
            or "below" in reason
            or "zero_after_round_down"
            in reason
        )
        for reason in reasons
    ):
        return (
            SALE_LEG_STATUS_SKIPPED_MIN_ORDER
        )

    return (
        SALE_LEG_STATUS_SKIPPED_NOT_ELIGIBLE
    )


def _build_derivative_reduction_legs(
    *,
    snapshot: NegativeSaleSnapshot,
    derivative_reduction: (
        ProportionalDerivativeReduction
    ),
) -> list[SaleLegPlan]:
    legs: list[SaleLegPlan] = []
    strict = _is_live_bybit_snapshot(
        snapshot
    )

    for asset in _derivative_assets(
        snapshot
    ):
        skip = _asset_skip_reason(
            asset,
            strict_instrument_preflight=(
                strict
            ),
        )

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

        current_size = (
            _asset_available_qty(asset)
        )

        if current_size is None:
            legs.append(
                _build_skipped_asset_leg(
                    asset=asset,
                    status=(
                        SALE_LEG_STATUS_SKIPPED_NOT_ELIGIBLE
                    ),
                    reason=(
                        "Derivative current "
                        "position size is missing."
                    ),
                )
            )
            continue

        try:
            raw_target_qty = (
                derivative_raw_target_qty(
                    current_size=current_size,
                    net_redeem_ratio=(
                        derivative_reduction
                        .net_redeem_ratio
                    ),
                )
            )
        except NegativeSalePlanningPolicyError as exc:
            legs.append(
                _build_skipped_asset_leg(
                    asset=asset,
                    status=(
                        SALE_LEG_STATUS_SKIPPED_NOT_ELIGIBLE
                    ),
                    reason=(
                        "Derivative target "
                        f"calculation failed: {exc}"
                    ),
                )
            )
            continue

        if raw_target_qty <= ZERO:
            legs.append(
                _build_skipped_asset_leg(
                    asset=asset,
                    status=(
                        SALE_LEG_STATUS_SKIPPED_ZERO_VALUE
                    ),
                    reason=(
                        "Derivative reduction "
                        "target is zero under "
                        f"{DERIVATIVE_REDUCTION_POLICY_VERSION}."
                    ),
                    order_quantity_preflight={
                        "eligible": False,
                        "requested_qty": str(
                            raw_target_qty
                        ),
                        "normalized_qty": "0",
                        "slices": [],
                        "reasons": [
                            "net_redeem_ratio_zero",
                        ],
                    },
                )
            )
            continue

        (
            normalized_qty,
            preflight,
        ) = _normalize_asset_target_qty(
            asset=asset,
            requested_qty=raw_target_qty,
            strict_instrument_preflight=(
                strict
            ),
        )

        if (
            normalized_qty is None
            or normalized_qty <= ZERO
        ):
            legs.append(
                _build_skipped_asset_leg(
                    asset=asset,
                    status=(
                        _preflight_skip_status(
                            preflight
                        )
                    ),
                    reason=(
                        "Derivative quantity "
                        "preflight failed: "
                        + ",".join(
                            str(reason)
                            for reason in (
                                preflight.get(
                                    "reasons"
                                )
                                or []
                            )
                        )
                    ),
                    order_quantity_preflight=(
                        preflight
                    ),
                )
            )
            continue

        legs.append(
            _build_planned_asset_leg(
                asset=asset,
                source_weight=ZERO,
                target_cash_usdt=ZERO,
                target_qty=normalized_qty,
                expected_cash_delta_usdt=ZERO,
                use_for_deficit_cover=False,
                reason=(
                    "Derivative position is "
                    "reduced proportionally under "
                    f"{DERIVATIVE_REDUCTION_POLICY_VERSION}; "
                    "derivative notional is not "
                    "expected cash."
                ),
                order_quantity_preflight=(
                    preflight
                ),
            )
        )

    return legs


def _collect_deficit_cover_assets(
    snapshot: NegativeSaleSnapshot,
    *,
    strict_instrument_preflight: bool,
) -> list[NegativeSaleAsset]:
    assets: list[NegativeSaleAsset] = []

    for asset in [
        *snapshot.non_stable_earn_holdings,
        *snapshot.spot_holdings,
    ]:
        if not (
            _asset_eligible_for_deficit_cover(
                asset
            )
        ):
            continue

        if (
            _asset_skip_reason(
                asset,
                strict_instrument_preflight=(
                    strict_instrument_preflight
                ),
            )
            is not None
        ):
            continue

        assets.append(asset)

    return assets


def _planned_spot_cash_from_preflight(
    *,
    target_cash_usdt: Decimal,
    preflight: dict[str, Any],
) -> Decimal:
    normalized_notional = (
        preflight.get(
            "normalized_notional"
        )
    )

    if (
        normalized_notional is None
        or normalized_notional == ""
    ):
        return target_cash_usdt

    normalized_value = dec(
        normalized_notional
    )

    return max(
        min(
            target_cash_usdt,
            normalized_value,
        ),
        ZERO,
    )


def _build_cash_deficit_legs(
    *,
    snapshot: NegativeSaleSnapshot,
    sale_target_usdt: Decimal,
) -> list[SaleLegPlan]:
    legs: list[SaleLegPlan] = []

    if sale_target_usdt <= ZERO:
        return legs

    strict = _is_live_bybit_snapshot(
        snapshot
    )

    eligible_sources = (
        _collect_deficit_cover_assets(
            snapshot,
            strict_instrument_preflight=(
                strict
            ),
        )
    )

    total_eligible_value = sum(
        (
            _asset_value(item)
            for item in eligible_sources
        ),
        ZERO,
    )

    for asset in _cash_deficit_assets(
        snapshot
    ):
        skip = _asset_skip_reason(
            asset,
            strict_instrument_preflight=(
                strict
            ),
        )

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

        if not (
            _asset_eligible_for_deficit_cover(
                asset
            )
        ):
            legs.append(
                _build_skipped_asset_leg(
                    asset=asset,
                    status=(
                        SALE_LEG_STATUS_SKIPPED_NOT_ELIGIBLE
                    ),
                    reason=(
                        asset.eligibility_reason
                        or (
                            "Asset is not eligible "
                            "for task-2 deficit cover."
                        )
                    ),
                )
            )
            continue

        value = _asset_value(asset)

        if total_eligible_value <= ZERO:
            legs.append(
                _build_skipped_asset_leg(
                    asset=asset,
                    status=(
                        SALE_LEG_STATUS_SKIPPED_NOT_ELIGIBLE
                    ),
                    reason=(
                        "No eligible positive-value "
                        "cash-generating source exists."
                    ),
                )
            )
            continue

        source_weight = (
            value / total_eligible_value
        )
        target_cash = min(
            value,
            sale_target_usdt
            * source_weight,
        )

        if target_cash <= ZERO:
            legs.append(
                _build_skipped_asset_leg(
                    asset=asset,
                    status=(
                        SALE_LEG_STATUS_SKIPPED_ZERO_VALUE
                    ),
                    reason=(
                        "Calculated cash target "
                        "is zero."
                    ),
                )
            )
            continue

        raw_target_qty = (
            _target_qty_for_asset(
                asset=asset,
                target_cash_usdt=(
                    target_cash
                ),
                value_usdt=value,
            )
        )

        if (
            raw_target_qty is None
            or raw_target_qty <= ZERO
        ):
            legs.append(
                _build_skipped_asset_leg(
                    asset=asset,
                    status=(
                        SALE_LEG_STATUS_SKIPPED_NOT_ELIGIBLE
                    ),
                    reason=(
                        "Cash-generating asset "
                        "quantity is missing."
                    ),
                )
            )
            continue

        (
            normalized_qty,
            preflight,
        ) = _normalize_asset_target_qty(
            asset=asset,
            requested_qty=raw_target_qty,
            strict_instrument_preflight=(
                strict
            ),
        )

        if (
            normalized_qty is None
            or normalized_qty <= ZERO
        ):
            legs.append(
                _build_skipped_asset_leg(
                    asset=asset,
                    status=(
                        _preflight_skip_status(
                            preflight
                        )
                    ),
                    reason=(
                        "Cash-generating quantity "
                        "preflight failed: "
                        + ",".join(
                            str(reason)
                            for reason in (
                                preflight.get(
                                    "reasons"
                                )
                                or []
                            )
                        )
                    ),
                    order_quantity_preflight=(
                        preflight
                    ),
                )
            )
            continue

        expected_cash = (
            _planned_spot_cash_from_preflight(
                target_cash_usdt=(
                    target_cash
                ),
                preflight=preflight,
            )
        )

        if expected_cash <= ZERO:
            legs.append(
                _build_skipped_asset_leg(
                    asset=asset,
                    status=(
                        SALE_LEG_STATUS_SKIPPED_MIN_ORDER
                    ),
                    reason=(
                        "Normalized expected cash "
                        "is zero."
                    ),
                    order_quantity_preflight=(
                        preflight
                    ),
                )
            )
            continue

        legs.append(
            _build_planned_asset_leg(
                asset=asset,
                source_weight=source_weight,
                target_cash_usdt=(
                    target_cash
                ),
                target_qty=normalized_qty,
                expected_cash_delta_usdt=(
                    expected_cash
                ),
                use_for_deficit_cover=True,
                reason=(
                    "Earn/spot source is planned "
                    "for the remaining cash deficit "
                    "after derivative reductions and "
                    "confirmed UNIFIED balance refresh."
                ),
                order_quantity_preflight=(
                    preflight
                ),
            )
        )

    return legs


def _build_asset_sale_legs(
    *,
    snapshot: NegativeSaleSnapshot,
    sale_target_usdt: Decimal,
    derivative_reduction: (
        ProportionalDerivativeReduction
    ),
) -> list[SaleLegPlan]:
    derivative_legs = (
        _build_derivative_reduction_legs(
            snapshot=snapshot,
            derivative_reduction=(
                derivative_reduction
            ),
        )
    )

    cash_deficit_legs = (
        _build_cash_deficit_legs(
            snapshot=snapshot,
            sale_target_usdt=(
                sale_target_usdt
            ),
        )
    )

    return [
        *derivative_legs,
        *cash_deficit_legs,
    ]


def _derivative_reduction_for_settlement(
    settlement_batch: FundSettlementBatch,
) -> ProportionalDerivativeReduction:
    planned_change = getattr(
        settlement_batch,
        "planned_net_shares_change",
        None,
    )
    shares_before = getattr(
        settlement_batch,
        "shares_outstanding_before",
        None,
    )

    if planned_change is None:
        raise NegativeSalePlanError(
            "planned_net_shares_change "
            "is required for derivative "
            "reduction planning"
        )

    if shares_before is None:
        raise NegativeSalePlanError(
            "shares_outstanding_before "
            "is required for derivative "
            "reduction planning"
        )

    try:
        return (
            compute_proportional_derivative_reduction(
                planned_net_shares_change=(
                    planned_change
                ),
                shares_outstanding_before=(
                    shares_before
                ),
            )
        )
    except NegativeSalePlanningPolicyError as exc:
        raise NegativeSalePlanError(
            "derivative_reduction_policy_"
            f"failed: {exc}"
        ) from exc

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

    derivative_reduction = (
        _derivative_reduction_for_settlement(
            settlement_batch
        )
    )

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
        - usdt_earn_used_as_buffer
    )

    cash_like_legs = _build_cash_like_legs(snapshot)
    asset_sale_legs = _build_asset_sale_legs(
        snapshot=snapshot,
        sale_target_usdt=sale_target_usdt,
        derivative_reduction=(
            derivative_reduction
        ),
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
        "policy": {
            "derivative_reduction": (
                derivative_reduction.to_dict()
            ),
            "derivative_reduction_policy_version": (
                DERIVATIVE_REDUCTION_POLICY_VERSION
            ),
            "execution_sequence": [
                "derivative_reductions",
                "confirmed_unified_usdt_refresh",
                "earn_and_spot_deficit_cover",
            ],
            "fund_wallet_cash_usable_in_task2": False,
            "derivative_close_side_matrix": {
                "long_or_buy_position": "Sell",
                "short_or_sell_position": "Buy",
            },
            "derivative_reduce_only": True,
            "spot_market_unit": "baseCoin",
        },
        "formula": {
            "sale_target_usdt": (
                "max(required_master_usdt - unified_usdt_available "
                "- usdt_earn_used_as_buffer, 0)"
            ),
            "required_master_usdt": str(required_master_usdt),
            "unified_usdt_available": str(unified_usdt_available),
            "fund_wallet_usdt_available": str(fund_wallet_usdt_available),
            "fund_wallet_usdt_excluded_reason": (
                "requires_fund_to_unified_transfer_task3"
            ),
            "usdt_earn_available": str(usdt_earn_available),
            "usdt_earn_redeemable": str(usdt_earn_redeemable),
            "usdt_earn_used_as_buffer": str(usdt_earn_used_as_buffer),
            "cash_like_available_for_plan": str(cash_like_available_for_plan),
            "total_cash_like_available_usdt": str(total_cash_like_available_usdt),
        },
        "targets": {
            "sale_target_usdt": str(sale_target_usdt),
            "planned_sale_usdt": str(planned_sale_usdt),
            "derivative_notional_included_in_planned_sale": False,
            "derivative_expected_cash_before_balance_refresh": "0",
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
        derivative_reduction_json=(
            derivative_reduction.to_dict()
        ),
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


def _validate_snapshot_for_sale_plan(
    *,
    settlement_batch: FundSettlementBatch,
    fund: Fund,
    snapshot: NegativeSaleSnapshot,
) -> None:
    if not _is_live_bybit_snapshot(
        snapshot
    ):
        return

    if not snapshot.snapshot_complete:
        reasons = list(
            snapshot.completeness_reasons
        )
        failed = list(
            snapshot.failed_endpoints
        )

        raise NegativeSalePlanError(
            "live_negative_sale_snapshot_"
            "incomplete: "
            f"reasons={reasons}, "
            f"failed_endpoints={failed}"
        )

    if snapshot.captured_at is None:
        raise NegativeSalePlanError(
            "live_negative_sale_snapshot_"
            "captured_at_missing"
        )

    if not snapshot.source_account:
        raise NegativeSalePlanError(
            "live_negative_sale_snapshot_"
            "source_account_missing"
        )

    if snapshot.fund_id is None:
        raise NegativeSalePlanError(
            "live_negative_sale_snapshot_"
            "fund_id_missing"
        )

    if int(snapshot.fund_id) != int(
        settlement_batch.fund_id
    ):
        raise NegativeSalePlanError(
            "live_negative_sale_snapshot_"
            "fund_id_mismatch: "
            f"snapshot={snapshot.fund_id}, "
            f"settlement="
            f"{settlement_batch.fund_id}"
        )

    if snapshot.fund_code is None:
        raise NegativeSalePlanError(
            "live_negative_sale_snapshot_"
            "fund_code_missing"
        )

    if str(snapshot.fund_code) != str(
        fund.code
    ):
        raise NegativeSalePlanError(
            "live_negative_sale_snapshot_"
            "fund_code_mismatch: "
            f"snapshot={snapshot.fund_code}, "
            f"fund={fund.code}"
        )

    required = set(
        snapshot.required_endpoints
    )
    successful = set(
        snapshot.successful_endpoints
    )

    if not required:
        raise NegativeSalePlanError(
            "live_negative_sale_snapshot_"
            "required_endpoints_missing"
        )

    missing_success = sorted(
        required - successful
    )

    if missing_success:
        raise NegativeSalePlanError(
            "live_negative_sale_snapshot_"
            "required_endpoints_not_confirmed: "
            f"{missing_success}"
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

        _validate_snapshot_for_sale_plan(
            settlement_batch=(
                settlement_batch
            ),
            fund=fund,
            snapshot=snapshot,
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
                    "- usdt_earn_used_as_buffer, 0)"
                ),
                "fund_wallet_usdt_available_diagnostic": str(
                    computation
                    .fund_wallet_usdt_available
                ),
                "fund_wallet_usdt_usable_in_task2": False,
                "derivative_reduction": (
                    computation
                    .derivative_reduction_json
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