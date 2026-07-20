from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.allocation.bybit_snapshot_reader import (
    build_allocation_snapshot_from_bybit,
)
from app.allocation.snapshot_service import (
    STABLECOINS,
    AllocationSnapshot,
    AllocationSnapshotHolding,
)
from app.bybit.client import BybitV5Client
from app.settlement.negative_sale_snapshot import (
    NegativeSaleSnapshot,
    NegativeSaleSnapshotError,
    dec,
    normalize_negative_sale_snapshot,
    utcnow,
)


ZERO = Decimal("0")


def _decimal_str(
    value: Decimal | None,
) -> str | None:
    if value is None:
        return None

    return str(value)


def _holding_extra(
    holding: AllocationSnapshotHolding,
) -> dict[str, Any]:
    extra = holding.extra

    return (
        extra
        if isinstance(extra, dict)
        else {}
    )


def _is_usdt(
    coin: str | None,
) -> bool:
    return (coin or "").upper() == "USDT"


def _is_stablecoin(
    coin: str | None,
) -> bool:
    return (
        (coin or "").upper()
        in STABLECOINS
    )


def _asset_row_from_holding(
    holding: AllocationSnapshotHolding,
) -> dict[str, Any]:
    extra = _holding_extra(holding)

    instrument_info = extra.get(
        "instrument_info"
    )
    position_side = (
        extra.get("position_side")
        or holding.side
    )

    row: dict[str, Any] = {
        "coin": holding.coin,
        "symbol": holding.symbol,
        "category": holding.category,
        "location": holding.location,
        "side": holding.side,
        "position_side": position_side,
        "position_idx": extra.get(
            "position_idx"
        ),
        "qty": _decimal_str(
            holding.size
        ),
        "size": _decimal_str(
            holding.size
        ),
        "usd_value": _decimal_str(
            holding.usd_value
        ),
        "notional_usd": _decimal_str(
            holding.notional_usd
        ),
        "exposure_notional_usdt": (
            _decimal_str(
                holding.notional_usd
            )
        ),
        "instrument_status": extra.get(
            "instrument_status"
        ),
        "instrument_preflight_complete": (
            extra.get(
                "instrument_preflight_complete"
            )
        ),
        "instrument_completeness_reasons": (
            list(
                extra.get(
                    "instrument_completeness_reasons"
                )
                or []
            )
        ),
        "instrument_info": (
            dict(instrument_info)
            if isinstance(
                instrument_info,
                dict,
            )
            else None
        ),
        "contract_type": extra.get(
            "contract_type"
        ),
        "settle_coin": extra.get(
            "settle_coin"
        ),
        "position_value": _decimal_str(
            extra.get("position_value")
        ),
        "position_im": _decimal_str(
            extra.get("position_im")
        ),
        "position_mm": _decimal_str(
            extra.get("position_mm")
        ),
        "unrealised_pnl": _decimal_str(
            extra.get("unrealised_pnl")
        ),
        "cum_realised_pnl": _decimal_str(
            extra.get("cum_realised_pnl")
        ),
        "source": extra.get("source"),
    }

    if holding.leg_group == "earn":
        redeemable_known = (
            extra.get("redeemable_known")
            is True
        )
        redeemable_amount = extra.get(
            "redeemable_amount"
        )

        row.update(
            {
                "total_amount": (
                    _decimal_str(
                        extra.get(
                            "total_amount"
                        )
                    )
                ),
                "available_amount": (
                    _decimal_str(
                        extra.get(
                            "available_amount"
                        )
                    )
                ),
                "redeemable_amount": (
                    _decimal_str(
                        redeemable_amount
                    )
                    if redeemable_known
                    else None
                ),
                "redeemable_usdt": (
                    _decimal_str(
                        redeemable_amount
                    )
                    if (
                        _is_usdt(
                            holding.coin
                        )
                        and redeemable_known
                    )
                    else None
                ),
                "locked_amount": (
                    _decimal_str(
                        extra.get(
                            "locked_amount"
                        )
                    )
                ),
                "redeemable_known": (
                    redeemable_known
                ),
                "product_id": extra.get(
                    "product_id"
                ),
                "product_category": (
                    extra.get(
                        "product_category"
                    )
                    or holding.product_category
                ),
                "product_status": (
                    extra.get(
                        "product_status"
                    )
                ),
                "precision": extra.get(
                    "precision"
                ),
                "source_endpoint": (
                    extra.get(
                        "source_endpoint"
                    )
                ),
            }
        )

    if (
        holding.leg_group
        == "funding_wallet"
        and not _is_stablecoin(
            holding.coin
        )
    ):
        row.update(
            {
                "requires_fund_to_unified_transfer": True,
                "use_for_deficit_cover": False,
                "eligibility_reason": (
                    "requires_fund_to_unified_transfer_task3"
                ),
                "transfer_balance": (
                    _decimal_str(
                        extra.get(
                            "transfer_balance"
                        )
                    )
                ),
            }
        )

    return row


def _unified_usdt_available(
    holdings: list[
        AllocationSnapshotHolding
    ],
) -> Decimal:
    for holding in holdings:
        if holding.leg_group != "cash":
            continue

        if not _is_usdt(holding.coin):
            continue

        if (
            holding.location or ""
        ).upper() != "UNIFIED":
            continue

        extra = _holding_extra(holding)
        available = extra.get("available")

        if available is not None:
            return dec(available)

        return ZERO

    return ZERO


def _fund_wallet_usdt_available(
    holdings: list[
        AllocationSnapshotHolding
    ],
) -> Decimal:
    for holding in holdings:
        if (
            holding.leg_group
            != "funding_wallet"
        ):
            continue

        if not _is_usdt(holding.coin):
            continue

        extra = _holding_extra(holding)
        transfer_balance = extra.get(
            "transfer_balance"
        )

        if transfer_balance is not None:
            return dec(transfer_balance)

        return ZERO

    return ZERO


def _usdt_earn_amounts(
    holdings: list[
        AllocationSnapshotHolding
    ],
) -> tuple[
    Decimal,
    Decimal,
    bool,
]:
    available = ZERO
    redeemable = ZERO
    redeemable_known = True
    found_usdt_earn = False

    for holding in holdings:
        if holding.leg_group != "earn":
            continue

        if not _is_usdt(holding.coin):
            continue

        found_usdt_earn = True
        extra = _holding_extra(holding)

        available_amount = extra.get(
            "available_amount"
        )

        if available_amount is not None:
            available += dec(
                available_amount
            )
        elif holding.size is not None:
            available += dec(
                holding.size
            )

        if (
            extra.get("redeemable_known")
            is not True
        ):
            redeemable_known = False
            continue

        row_redeemable = extra.get(
            "redeemable_amount"
        )

        if row_redeemable is None:
            redeemable_known = False
            continue

        redeemable += dec(
            row_redeemable
        )

    if not found_usdt_earn:
        redeemable_known = True

    return (
        available,
        redeemable,
        redeemable_known,
    )


def _negative_sale_raw_from_allocation_snapshot(
    allocation: AllocationSnapshot,
) -> dict[str, Any]:
    holdings = allocation.holdings

    spot_rows: list[
        dict[str, Any]
    ] = []
    funding_wallet_asset_rows: list[
        dict[str, Any]
    ] = []
    non_stable_earn_rows: list[
        dict[str, Any]
    ] = []
    perp_future_rows: list[
        dict[str, Any]
    ] = []
    long_option_rows: list[
        dict[str, Any]
    ] = []
    short_option_rows: list[
        dict[str, Any]
    ] = []

    for holding in holdings:
        if holding.leg_group == "cash":
            continue

        if (
            holding.leg_group
            == "funding_wallet"
        ):
            if _is_stablecoin(
                holding.coin
            ):
                continue

            row = _asset_row_from_holding(
                holding
            )
            funding_wallet_asset_rows.append(
                row
            )
            continue

        if holding.leg_group == "spot":
            if _is_stablecoin(
                holding.coin
            ):
                continue

            spot_rows.append(
                _asset_row_from_holding(
                    holding
                )
            )
            continue

        if holding.leg_group == "earn":
            if _is_usdt(holding.coin):
                continue

            non_stable_earn_rows.append(
                _asset_row_from_holding(
                    holding
                )
            )
            continue

        if holding.leg_group in {
            "perp",
            "future",
        }:
            perp_future_rows.append(
                _asset_row_from_holding(
                    holding
                )
            )
            continue

        if (
            holding.leg_group
            == "long_option"
        ):
            long_option_rows.append(
                _asset_row_from_holding(
                    holding
                )
            )
            continue

        if (
            holding.leg_group
            == "short_option"
        ):
            short_option_rows.append(
                _asset_row_from_holding(
                    holding
                )
            )
            continue

    (
        usdt_earn_available,
        usdt_earn_redeemable,
        usdt_earn_redeemable_known,
    ) = _usdt_earn_amounts(
        holdings
    )

    captured_at = (
        allocation.captured_at
        or allocation.snapshot_ts
    )

    return {
        "snapshot_ts": (
            allocation.snapshot_ts.isoformat()
        ),
        "captured_at": (
            captured_at.isoformat()
        ),
        "source": "bybit_readonly",
        "source_account": (
            allocation.source_account
            or allocation.account_type
        ),
        "fund_id": allocation.fund_id,
        "fund_code": allocation.fund_code,
        "snapshot_complete": (
            allocation.snapshot_complete
        ),
        "completeness_reasons": list(
            allocation.completeness_reasons
        ),
        "required_endpoints": list(
            allocation.required_endpoints
        ),
        "successful_endpoints": list(
            allocation.successful_endpoints
        ),
        "failed_endpoints": list(
            allocation.failed_endpoints
        ),
        "suppressed_errors": [
            dict(row)
            for row
            in allocation.suppressed_errors
        ],
        "cash": {
            "unified_usdt_available": str(
                _unified_usdt_available(
                    holdings
                )
            ),
            "fund_wallet_usdt_available": str(
                _fund_wallet_usdt_available(
                    holdings
                )
            ),
            "usdt_earn_available": str(
                usdt_earn_available
            ),
            "usdt_earn_redeemable": str(
                usdt_earn_redeemable
            ),
            "usdt_earn_redeemable_known": (
                usdt_earn_redeemable_known
            ),
        },
        "assets": {
            "spot": spot_rows,
            "funding_wallet_non_stable": (
                funding_wallet_asset_rows
            ),
            "non_stable_earn": (
                non_stable_earn_rows
            ),
            "perp_future_positions": (
                perp_future_rows
            ),
            "long_options": (
                long_option_rows
            ),
            "short_options": (
                short_option_rows
            ),
        },
        "summary": {
            "total_portfolio_value_usdt": str(
                allocation.total_equity_usdt
            ),
            "funding_wallet_non_stable_count": (
                len(
                    funding_wallet_asset_rows
                )
            ),
            "funding_wallet_non_stable_usable": False,
            "funding_wallet_non_stable_reason": (
                "requires_fund_to_unified_transfer_task3"
            ),
        },
        "raw_summary_json": (
            allocation.raw_summary_json
        ),
    }


def build_negative_sale_snapshot_from_bybit(
    db: Session,
    *,
    fund_id: int,
    client: BybitV5Client | None = None,
) -> NegativeSaleSnapshot:
    """
    Build a negative-net sale snapshot from
    Bybit read-only endpoints.

    Safety:
    - no trades;
    - no transfers;
    - no withdrawals;
    - no BSC calls;
    - no accounting finalization.
    """

    try:
        allocation = (
            build_allocation_snapshot_from_bybit(
                db,
                fund_id=int(fund_id),
                client=client,
            )
        )
    except Exception as exc:
        raise NegativeSaleSnapshotError(
            "Failed to build live negative "
            f"sale snapshot from Bybit: {exc}"
        ) from exc

    raw_snapshot = (
        _negative_sale_raw_from_allocation_snapshot(
            allocation
        )
    )
    raw_snapshot["snapshot_ts"] = (
        utcnow().isoformat()
    )

    return normalize_negative_sale_snapshot(
        raw_snapshot
    )
