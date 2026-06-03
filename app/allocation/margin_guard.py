from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.allocation.statuses import (
    ALLOCATION_LEG_STATUS_PLANNED,
    ALLOCATION_LEG_STATUS_SKIPPED_MARGIN_GUARD,
    ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER,
    LEG_TYPE_FUTURE_INCREASE,
    LEG_TYPE_LONG_OPTION_INCREASE,
    LEG_TYPE_PERP_INCREASE,
    LEG_TYPE_SHORT_OPTION_INCREASE,
)


ZERO = Decimal("0")
ONE = Decimal("1")


class MarginGuardError(RuntimeError):
    pass


@dataclass(frozen=True)
class AccountRiskSnapshot:
    total_equity_usdt: Decimal
    total_initial_margin_usdt: Decimal
    total_maintenance_margin_usdt: Decimal
    account_im_rate: Decimal
    account_mm_rate: Decimal
    total_available_balance_usdt: Decimal
    source: str
    raw: dict[str, Any]
    is_controlled_fixture: bool = False
    is_valid: bool = True
    error: str | None = None


@dataclass(frozen=True)
class MarginImpactEstimate:
    allocation_leg_id: int | None
    leg_type: str
    category: str | None
    symbol: str | None
    side: str | None
    target_qty: Decimal
    target_usdt: Decimal
    notional_usdt: Decimal
    estimated_initial_margin_usdt: Decimal
    estimated_maintenance_margin_usdt: Decimal
    is_short_option: bool
    uncertain: bool
    reason: str | None
    diagnostics: dict[str, Any]


@dataclass(frozen=True)
class MarginGuardResult:
    ok: bool
    status: str
    residual_usdt: Decimal
    reason: str | None
    account_im_rate: Decimal
    account_mm_rate: Decimal
    post_im_rate: Decimal
    post_mm_rate: Decimal
    max_im_rate: Decimal
    max_mm_rate: Decimal
    account_risk: AccountRiskSnapshot
    impact: MarginImpactEstimate
    diagnostics: dict[str, Any]


def dec(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)

    if isinstance(value, Decimal):
        return value

    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _lower(value: Any) -> str:
    return _normalize_text(value).lower()


def _get_value(row: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return default


def _safe_rate(
    *,
    explicit_rate: Decimal,
    numerator: Decimal,
    denominator: Decimal,
) -> Decimal:
    if explicit_rate > ZERO:
        return explicit_rate

    if denominator > ZERO:
        return numerator / denominator

    return ZERO


def _controlled_fixture_snapshot(reason: str | None = None) -> AccountRiskSnapshot:
    total_equity = Decimal("10000")
    initial_margin = Decimal("1000")
    maintenance_margin = Decimal("500")

    return AccountRiskSnapshot(
        total_equity_usdt=total_equity,
        total_initial_margin_usdt=initial_margin,
        total_maintenance_margin_usdt=maintenance_margin,
        account_im_rate=initial_margin / total_equity,
        account_mm_rate=maintenance_margin / total_equity,
        total_available_balance_usdt=Decimal("9000"),
        source="controlled_fixture",
        raw={
            "mode": "stage22_5_controlled_fixture",
            "reason": reason,
        },
        is_controlled_fixture=True,
        is_valid=True,
        error=None,
    )


def _extract_wallet_balance_row(payload: dict[str, Any]) -> dict[str, Any] | None:
    result = payload.get("result")

    if isinstance(result, dict):
        rows = result.get("list")
        if isinstance(rows, list) and rows:
            first = rows[0]
            if isinstance(first, dict):
                return first

        if any(
            key in result
            for key in (
                "totalEquity",
                "totalInitialMargin",
                "totalMaintenanceMargin",
                "accountIMRate",
                "accountMMRate",
                "totalAvailableBalance",
            )
        ):
            return result

    if any(
        key in payload
        for key in (
            "totalEquity",
            "totalInitialMargin",
            "totalMaintenanceMargin",
            "accountIMRate",
            "accountMMRate",
            "totalAvailableBalance",
        )
    ):
        return payload

    return None


def get_account_risk_snapshot(client: Any) -> AccountRiskSnapshot:
    """
    Read/parse future Bybit Unified account risk snapshot.

    Stage 22.5:
    - tests should use fake/mock client responses;
    - no real trading/order endpoints;
    - if mock client has no risk endpoint, use controlled fixture.
    """
    try:
        get = getattr(client, "get", None)
        if not callable(get):
            return _controlled_fixture_snapshot("client has no get method")

        payload = get(
            "/v5/account/wallet-balance",
            {"accountType": "UNIFIED"},
        )

    except Exception as exc:
        return _controlled_fixture_snapshot(f"mock account risk endpoint unavailable: {exc}")

    if not isinstance(payload, dict):
        return AccountRiskSnapshot(
            total_equity_usdt=ZERO,
            total_initial_margin_usdt=ZERO,
            total_maintenance_margin_usdt=ZERO,
            account_im_rate=Decimal("1"),
            account_mm_rate=Decimal("1"),
            total_available_balance_usdt=ZERO,
            source="invalid_payload",
            raw={"payload": payload},
            is_controlled_fixture=False,
            is_valid=False,
            error="Account risk payload is not a dict",
        )

    ret_code = payload.get("retCode")
    if ret_code not in (None, 0):
        return AccountRiskSnapshot(
            total_equity_usdt=ZERO,
            total_initial_margin_usdt=ZERO,
            total_maintenance_margin_usdt=ZERO,
            account_im_rate=Decimal("1"),
            account_mm_rate=Decimal("1"),
            total_available_balance_usdt=ZERO,
            source="ret_code_error",
            raw=payload,
            is_controlled_fixture=False,
            is_valid=False,
            error=f"Account risk endpoint returned retCode={ret_code}",
        )

    row = _extract_wallet_balance_row(payload)
    if row is None:
        return _controlled_fixture_snapshot("mock account risk row missing")

    total_equity = dec(
        _get_value(
            row,
            "totalEquity",
            "total_equity",
            "equity",
            default="0",
        )
    )
    initial_margin = dec(
        _get_value(
            row,
            "totalInitialMargin",
            "total_initial_margin",
            "initialMargin",
            default="0",
        )
    )
    maintenance_margin = dec(
        _get_value(
            row,
            "totalMaintenanceMargin",
            "total_maintenance_margin",
            "maintenanceMargin",
            default="0",
        )
    )
    account_im_rate = dec(
        _get_value(
            row,
            "accountIMRate",
            "account_im_rate",
            "imRate",
            default="0",
        )
    )
    account_mm_rate = dec(
        _get_value(
            row,
            "accountMMRate",
            "account_mm_rate",
            "mmRate",
            default="0",
        )
    )
    available_balance = dec(
        _get_value(
            row,
            "totalAvailableBalance",
            "total_available_balance",
            "availableBalance",
            default="0",
        )
    )

    account_im_rate = _safe_rate(
        explicit_rate=account_im_rate,
        numerator=initial_margin,
        denominator=total_equity,
    )
    account_mm_rate = _safe_rate(
        explicit_rate=account_mm_rate,
        numerator=maintenance_margin,
        denominator=total_equity,
    )

    is_valid = total_equity > ZERO
    error = None if is_valid else "totalEquity must be positive"

    return AccountRiskSnapshot(
        total_equity_usdt=total_equity,
        total_initial_margin_usdt=initial_margin,
        total_maintenance_margin_usdt=maintenance_margin,
        account_im_rate=account_im_rate,
        account_mm_rate=account_mm_rate,
        total_available_balance_usdt=available_balance,
        source="client",
        raw=row,
        is_controlled_fixture=False,
        is_valid=is_valid,
        error=error,
    )


def _leg_attr(leg: Any, name: str, default: Any = None) -> Any:
    return getattr(leg, name, default)


def _leg_type(leg: Any) -> str:
    return _normalize_text(_leg_attr(leg, "leg_type", ""))


def _category(leg: Any) -> str:
    return _lower(_leg_attr(leg, "category", ""))


def _side(leg: Any) -> str:
    return _normalize_text(_leg_attr(leg, "side", ""))


def _target_qty(leg: Any) -> Decimal:
    return dec(_leg_attr(leg, "target_qty", None))


def _target_usdt(leg: Any) -> Decimal:
    return dec(_leg_attr(leg, "target_usdt", None))


def _is_short_option_leg(leg: Any) -> bool:
    return _leg_type(leg) == LEG_TYPE_SHORT_OPTION_INCREASE


def _is_long_option_leg(leg: Any) -> bool:
    return _leg_type(leg) == LEG_TYPE_LONG_OPTION_INCREASE


def _is_perp_or_future_leg(leg: Any) -> bool:
    return _leg_type(leg) in {
        LEG_TYPE_PERP_INCREASE,
        LEG_TYPE_FUTURE_INCREASE,
    }


def _mock_last_price(mock_market_data: Any) -> Decimal:
    if isinstance(mock_market_data, dict):
        return dec(
            mock_market_data.get("last_price")
            or mock_market_data.get("lastPrice")
            or mock_market_data.get("price")
        )

    return dec(getattr(mock_market_data, "last_price", None))


def _impact_rates_for_leg(leg: Any, *, is_short_option: bool) -> tuple[Decimal, Decimal, str]:
    """
    Conservative Stage 22.5 mock margin model.

    These are not production Bybit formulas. They are controlled guard estimates
    used only to decide whether a mock leg can continue to execution_engine.
    """
    leg_type = _leg_type(leg)

    if leg_type == LEG_TYPE_SHORT_OPTION_INCREASE or is_short_option:
        # Strict mock estimate for short options.
        return Decimal("0.50"), Decimal("0.25"), "short_option_conservative"

    if leg_type == LEG_TYPE_LONG_OPTION_INCREASE:
        # Long option is premium-funded, but we still reserve conservative IM.
        return Decimal("0.10"), Decimal("0.02"), "long_option_conservative"

    if leg_type in {LEG_TYPE_PERP_INCREASE, LEG_TYPE_FUTURE_INCREASE}:
        return Decimal("0.10"), Decimal("0.05"), "perp_future_conservative"

    return Decimal("0.10"), Decimal("0.05"), "generic_derivative_conservative"


def estimate_margin_impact_for_leg(
    leg: Any,
    account_risk: AccountRiskSnapshot,
    mock_market_data: Any = None,
) -> MarginImpactEstimate:
    target_qty = _target_qty(leg)
    target_usdt = _target_usdt(leg)
    last_price = _mock_last_price(mock_market_data)
    leg_type = _leg_type(leg)
    category = _category(leg)
    symbol = _normalize_text(_leg_attr(leg, "symbol", None))
    side = _side(leg)
    is_short_option = _is_short_option_leg(leg)

    diagnostics: dict[str, Any] = {
        "account_source": account_risk.source,
        "category": category,
        "last_price": str(last_price),
    }

    uncertain = False
    reason = None

    if target_qty <= ZERO:
        uncertain = True
        reason = "target_qty must be positive for derivative/option margin estimate"

    if target_usdt <= ZERO and target_qty > ZERO and last_price > ZERO:
        target_usdt = target_qty * last_price

    notional_usdt = target_usdt
    if notional_usdt <= ZERO and target_qty > ZERO and last_price > ZERO:
        notional_usdt = target_qty * last_price

    if notional_usdt <= ZERO:
        uncertain = True
        reason = reason or "notional_usdt cannot be estimated"

    if not account_risk.is_valid:
        uncertain = True
        reason = reason or account_risk.error or "account risk snapshot is invalid"

    im_mult, mm_mult, model = _impact_rates_for_leg(
        leg,
        is_short_option=is_short_option,
    )

    estimated_initial_margin = notional_usdt * im_mult
    estimated_maintenance_margin = notional_usdt * mm_mult

    diagnostics.update(
        {
            "model": model,
            "im_mult": str(im_mult),
            "mm_mult": str(mm_mult),
            "notional_usdt": str(notional_usdt),
        }
    )

    return MarginImpactEstimate(
        allocation_leg_id=_leg_attr(leg, "id", None),
        leg_type=leg_type,
        category=category,
        symbol=symbol,
        side=side,
        target_qty=target_qty,
        target_usdt=target_usdt,
        notional_usdt=notional_usdt,
        estimated_initial_margin_usdt=estimated_initial_margin,
        estimated_maintenance_margin_usdt=estimated_maintenance_margin,
        is_short_option=is_short_option,
        uncertain=uncertain,
        reason=reason,
        diagnostics=diagnostics,
    )


def _guard_result(
    *,
    ok: bool,
    status: str,
    residual_usdt: Decimal,
    reason: str | None,
    account_risk: AccountRiskSnapshot,
    impact: MarginImpactEstimate,
    post_im_rate: Decimal,
    post_mm_rate: Decimal,
    max_im_rate: Decimal,
    max_mm_rate: Decimal,
) -> MarginGuardResult:
    return MarginGuardResult(
        ok=ok,
        status=status,
        residual_usdt=residual_usdt,
        reason=reason,
        account_im_rate=account_risk.account_im_rate,
        account_mm_rate=account_risk.account_mm_rate,
        post_im_rate=post_im_rate,
        post_mm_rate=post_mm_rate,
        max_im_rate=max_im_rate,
        max_mm_rate=max_mm_rate,
        account_risk=account_risk,
        impact=impact,
        diagnostics={
            "total_equity_usdt": str(account_risk.total_equity_usdt),
            "current_initial_margin_usdt": str(account_risk.total_initial_margin_usdt),
            "current_maintenance_margin_usdt": str(account_risk.total_maintenance_margin_usdt),
            "estimated_initial_margin_usdt": str(impact.estimated_initial_margin_usdt),
            "estimated_maintenance_margin_usdt": str(impact.estimated_maintenance_margin_usdt),
            "account_source": account_risk.source,
            "impact_model": impact.diagnostics.get("model"),
        },
    )


def check_margin_guard(
    *,
    account_risk: AccountRiskSnapshot,
    impact: MarginImpactEstimate,
    max_im_rate: Decimal,
    max_mm_rate: Decimal,
    is_short_option: bool,
) -> MarginGuardResult:
    max_im = dec(max_im_rate)
    max_mm = dec(max_mm_rate)

    if max_im <= ZERO:
        max_im = Decimal("0.70")

    if max_mm <= ZERO:
        max_mm = Decimal("0.50")

    residual_usdt = impact.target_usdt if impact.target_usdt > ZERO else impact.notional_usdt

    if impact.target_qty <= ZERO:
        return _guard_result(
            ok=False,
            status=ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER,
            residual_usdt=residual_usdt,
            reason="target_qty must be positive",
            account_risk=account_risk,
            impact=impact,
            post_im_rate=account_risk.account_im_rate,
            post_mm_rate=account_risk.account_mm_rate,
            max_im_rate=max_im,
            max_mm_rate=max_mm,
        )

    if not account_risk.is_valid:
        return _guard_result(
            ok=False,
            status=ALLOCATION_LEG_STATUS_SKIPPED_MARGIN_GUARD,
            residual_usdt=residual_usdt,
            reason=account_risk.error or "account risk snapshot is invalid",
            account_risk=account_risk,
            impact=impact,
            post_im_rate=Decimal("1"),
            post_mm_rate=Decimal("1"),
            max_im_rate=max_im,
            max_mm_rate=max_mm,
        )

    if impact.uncertain:
        return _guard_result(
            ok=False,
            status=ALLOCATION_LEG_STATUS_SKIPPED_MARGIN_GUARD,
            residual_usdt=residual_usdt,
            reason=impact.reason or "margin impact estimate is uncertain",
            account_risk=account_risk,
            impact=impact,
            post_im_rate=account_risk.account_im_rate,
            post_mm_rate=account_risk.account_mm_rate,
            max_im_rate=max_im,
            max_mm_rate=max_mm,
        )

    if account_risk.account_im_rate > max_im:
        return _guard_result(
            ok=False,
            status=ALLOCATION_LEG_STATUS_SKIPPED_MARGIN_GUARD,
            residual_usdt=residual_usdt,
            reason=(
                f"Current account IM rate is above threshold: "
                f"account_im_rate={account_risk.account_im_rate}, max_im_rate={max_im}"
            ),
            account_risk=account_risk,
            impact=impact,
            post_im_rate=account_risk.account_im_rate,
            post_mm_rate=account_risk.account_mm_rate,
            max_im_rate=max_im,
            max_mm_rate=max_mm,
        )

    if account_risk.account_mm_rate > max_mm:
        return _guard_result(
            ok=False,
            status=ALLOCATION_LEG_STATUS_SKIPPED_MARGIN_GUARD,
            residual_usdt=residual_usdt,
            reason=(
                f"Current account MM rate is above threshold: "
                f"account_mm_rate={account_risk.account_mm_rate}, max_mm_rate={max_mm}"
            ),
            account_risk=account_risk,
            impact=impact,
            post_im_rate=account_risk.account_im_rate,
            post_mm_rate=account_risk.account_mm_rate,
            max_im_rate=max_im,
            max_mm_rate=max_mm,
        )

    if account_risk.total_equity_usdt <= ZERO:
        return _guard_result(
            ok=False,
            status=ALLOCATION_LEG_STATUS_SKIPPED_MARGIN_GUARD,
            residual_usdt=residual_usdt,
            reason="total_equity_usdt must be positive",
            account_risk=account_risk,
            impact=impact,
            post_im_rate=Decimal("1"),
            post_mm_rate=Decimal("1"),
            max_im_rate=max_im,
            max_mm_rate=max_mm,
        )

    post_im_rate = (
        account_risk.total_initial_margin_usdt
        + impact.estimated_initial_margin_usdt
    ) / account_risk.total_equity_usdt

    post_mm_rate = (
        account_risk.total_maintenance_margin_usdt
        + impact.estimated_maintenance_margin_usdt
    ) / account_risk.total_equity_usdt

    if post_im_rate > max_im:
        return _guard_result(
            ok=False,
            status=ALLOCATION_LEG_STATUS_SKIPPED_MARGIN_GUARD,
            residual_usdt=residual_usdt,
            reason=(
                f"Estimated post-trade IM rate is above threshold: "
                f"post_im_rate={post_im_rate}, max_im_rate={max_im}"
            ),
            account_risk=account_risk,
            impact=impact,
            post_im_rate=post_im_rate,
            post_mm_rate=post_mm_rate,
            max_im_rate=max_im,
            max_mm_rate=max_mm,
        )

    if post_mm_rate > max_mm:
        return _guard_result(
            ok=False,
            status=ALLOCATION_LEG_STATUS_SKIPPED_MARGIN_GUARD,
            residual_usdt=residual_usdt,
            reason=(
                f"Estimated post-trade MM rate is above threshold: "
                f"post_mm_rate={post_mm_rate}, max_mm_rate={max_mm}"
            ),
            account_risk=account_risk,
            impact=impact,
            post_im_rate=post_im_rate,
            post_mm_rate=post_mm_rate,
            max_im_rate=max_im,
            max_mm_rate=max_mm,
        )

    return _guard_result(
        ok=True,
        status=ALLOCATION_LEG_STATUS_PLANNED,
        residual_usdt=ZERO,
        reason=None,
        account_risk=account_risk,
        impact=impact,
        post_im_rate=post_im_rate,
        post_mm_rate=post_mm_rate,
        max_im_rate=max_im,
        max_mm_rate=max_mm,
    )