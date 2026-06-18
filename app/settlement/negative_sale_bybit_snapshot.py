from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.allocation.bybit_snapshot_reader import build_allocation_snapshot_from_bybit
from app.allocation.snapshot_service import STABLECOINS, AllocationSnapshot, AllocationSnapshotHolding
from app.bybit.client import BybitV5Client
from app.settlement.negative_sale_snapshot import (
    NegativeSaleSnapshot,
    NegativeSaleSnapshotError,
    dec,
    normalize_negative_sale_snapshot,
    utcnow,
)

ZERO = Decimal("0")


def _decimal_str(value: Decimal | None) -> str:
    if value is None:
        return "0"
    return str(value)


def _holding_extra(holding: AllocationSnapshotHolding) -> dict[str, Any]:
    extra = holding.extra
    return extra if isinstance(extra, dict) else {}


def _is_usdt(coin: str | None) -> bool:
    return (coin or "").upper() == "USDT"


def _is_stablecoin(coin: str | None) -> bool:
    return (coin or "").upper() in STABLECOINS


def _asset_row_from_holding(holding: AllocationSnapshotHolding) -> dict[str, Any]:
    extra = _holding_extra(holding)
    row: dict[str, Any] = {
        "coin": holding.coin,
        "symbol": holding.symbol,
        "category": holding.category,
        "location": holding.location,
        "side": holding.side,
        "qty": _decimal_str(holding.size),
        "size": _decimal_str(holding.size),
        "usd_value": _decimal_str(holding.usd_value),
        "notional_usd": _decimal_str(holding.notional_usd),
        "instrument_status": "trading",
        "source": extra.get("source"),
    }

    redeemable = extra.get("redeemable_usdt") or extra.get("transfer_balance")
    if redeemable is not None:
        row["redeemable_usdt"] = str(redeemable)

    return row


def _unified_usdt_available(holdings: list[AllocationSnapshotHolding]) -> Decimal:
    for holding in holdings:
        if holding.leg_group != "cash":
            continue
        if not _is_usdt(holding.coin):
            continue
        if (holding.location or "").upper() != "UNIFIED":
            continue

        extra = _holding_extra(holding)
        available = extra.get("available")
        if available is not None:
            return dec(available)

        if holding.size is not None:
            return dec(holding.size)

        if holding.usd_value is not None:
            return dec(holding.usd_value)

    return ZERO


def _fund_wallet_usdt_available(holdings: list[AllocationSnapshotHolding]) -> Decimal:
    for holding in holdings:
        if holding.leg_group != "funding_wallet":
            continue
        if not _is_usdt(holding.coin):
            continue

        extra = _holding_extra(holding)
        transfer_balance = extra.get("transfer_balance")
        if transfer_balance is not None:
            return dec(transfer_balance)

        if holding.size is not None:
            return dec(holding.size)

        if holding.usd_value is not None:
            return dec(holding.usd_value)

    return ZERO


def _usdt_earn_amounts(holdings: list[AllocationSnapshotHolding]) -> tuple[Decimal, Decimal]:
    available = ZERO
    redeemable = ZERO

    for holding in holdings:
        if holding.leg_group != "earn":
            continue
        if not _is_usdt(holding.coin):
            continue

        amount = dec(holding.size)
        available += amount

        extra = _holding_extra(holding)
        row_redeemable = extra.get("redeemable_usdt")
        if row_redeemable is not None:
            redeemable += dec(row_redeemable)
        else:
            redeemable += amount

    return available, redeemable


def _negative_sale_raw_from_allocation_snapshot(
    allocation: AllocationSnapshot,
) -> dict[str, Any]:
    holdings = allocation.holdings

    spot_rows: list[dict[str, Any]] = []
    non_stable_earn_rows: list[dict[str, Any]] = []
    perp_future_rows: list[dict[str, Any]] = []
    long_option_rows: list[dict[str, Any]] = []
    short_option_rows: list[dict[str, Any]] = []

    for holding in holdings:
        if holding.leg_group == "cash":
            continue

        if holding.leg_group == "funding_wallet":
            if _is_usdt(holding.coin):
                continue
            if _is_stablecoin(holding.coin):
                continue

        if holding.leg_group == "spot":
            if _is_stablecoin(holding.coin):
                continue
            spot_rows.append(_asset_row_from_holding(holding))
            continue

        if holding.leg_group == "earn":
            if _is_usdt(holding.coin):
                continue
            non_stable_earn_rows.append(_asset_row_from_holding(holding))
            continue

        if holding.leg_group in {"perp", "future"}:
            perp_future_rows.append(_asset_row_from_holding(holding))
            continue

        if holding.leg_group == "long_option":
            long_option_rows.append(_asset_row_from_holding(holding))
            continue

        if holding.leg_group == "short_option":
            short_option_rows.append(_asset_row_from_holding(holding))
            continue

    usdt_earn_available, usdt_earn_redeemable = _usdt_earn_amounts(holdings)

    return {
        "snapshot_ts": allocation.snapshot_ts.isoformat(),
        "source": "bybit_readonly",
        "fund_id": allocation.fund_id,
        "fund_code": allocation.fund_code,
        "cash": {
            "unified_usdt_available": str(_unified_usdt_available(holdings)),
            "fund_wallet_usdt_available": str(_fund_wallet_usdt_available(holdings)),
            "usdt_earn_available": str(usdt_earn_available),
            "usdt_earn_redeemable": str(usdt_earn_redeemable),
        },
        "assets": {
            "spot": spot_rows,
            "non_stable_earn": non_stable_earn_rows,
            "perp_future_positions": perp_future_rows,
            "long_options": long_option_rows,
            "short_options": short_option_rows,
        },
        "summary": {
            "total_portfolio_value_usdt": str(allocation.total_equity_usdt),
        },
        "raw_summary_json": allocation.raw_summary_json,
    }


def build_negative_sale_snapshot_from_bybit(
    db: Session,
    *,
    fund_id: int,
    client: BybitV5Client | None = None,
) -> NegativeSaleSnapshot:
    """
    Build negative-net sale snapshot from real Bybit read-only endpoints.

    Read-only only:
    - no trades;
    - no transfers;
    - no withdrawals;
    - no BSC calls;
    - no accounting finalization.
    """
    try:
        allocation = build_allocation_snapshot_from_bybit(
            db,
            fund_id=int(fund_id),
            client=client,
        )
    except Exception as exc:
        raise NegativeSaleSnapshotError(
            f"Failed to build live negative sale snapshot from Bybit: {exc}"
        ) from exc

    raw_snapshot = _negative_sale_raw_from_allocation_snapshot(allocation)
    raw_snapshot["snapshot_ts"] = utcnow().isoformat()
    return normalize_negative_sale_snapshot(raw_snapshot)
