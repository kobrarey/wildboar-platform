from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from app.allocation.snapshot_service import (
    AllocationAccountRisk,
    AllocationSnapshot,
    AllocationSnapshotHolding,
    AllocationSnapshotError,
    STABLECOINS,
    classify_contract,
    dec,
    get_any,
    infer_spot_symbol,
    json_safe,
    normalize_coin,
    normalize_side,
    normalize_symbol,
)
from app.allocation.bybit_snapshot_completeness import (
    SnapshotEndpointMatrix,
)
from app.bybit.client import BybitApiError, BybitV5Client
from app.bybit.instruments import (
    BybitInstrumentInfo,
    query_instrument_info,
)
from app.bybit.credentials import get_active_fund_bybit_client
from app.models import Fund


log = logging.getLogger(__name__)


DEFAULT_INVERSE_COINS = ["BTC", "ETH", "XRP", "SOL", "EOS", "DOGE", "LTC"]
DEFAULT_OPTION_COINS = ["BTC", "ETH", "SOL"]
EARN_CATEGORIES = ["FlexibleSaving", "OnChain"]


class BybitAllocationSnapshotError(AllocationSnapshotError):
    pass


@dataclass(frozen=True)
class BybitReadonlyRawData:
    unified_wallet: dict[str, Any]
    funding_wallet: dict[str, Any]
    spot_tickers: dict[str, Any]
    linear_usdt_positions: list[dict[str, Any]]
    linear_usdc_positions: list[dict[str, Any]]
    inverse_positions: list[dict[str, Any]]
    option_positions: list[dict[str, Any]]
    earn_positions: list[dict[str, Any]]
    instruments: dict[str, BybitInstrumentInfo]
    suppressed_errors: list[dict[str, Any]]
    endpoint_matrix: SnapshotEndpointMatrix
    captured_at: datetime


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_fund_code(db: Session, *, fund_id: int) -> str:
    fund = db.query(Fund).filter(Fund.id == fund_id).first()
    if fund is None:
        raise BybitAllocationSnapshotError(f"Fund not found: fund_id={fund_id}")
    return fund.code


def _result(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


def _list_from_result(payload: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    result = _result(payload)

    for key in keys:
        value = result.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]

    return []


def _is_stablecoin(coin: str | None) -> bool:
    return (coin or "").upper() in STABLECOINS


def _abs_dec(value: Any) -> Decimal:
    return abs(dec(value))


def _optional_dec(
    value: Any,
) -> Decimal | None:
    if value is None or value == "":
        return None

    return dec(value)


def _optional_int(
    value: Any,
) -> int | None:
    if value is None or value == "":
        return None

    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _first_present(
    data: dict[str, Any],
    keys: tuple[str, ...],
) -> tuple[str | None, Any]:
    for key in keys:
        if (
            key in data
            and data[key] is not None
            and data[key] != ""
        ):
            return key, data[key]

    return None, None


def _instrument_lookup_key(
    *,
    category: str,
    symbol: str,
) -> str:
    return (
        f"{str(category).strip().lower()}:"
        f"{str(symbol).strip().upper()}"
    )


def _instrument_endpoint_key(
    *,
    category: str,
    symbol: str,
) -> str:
    return (
        f"instruments:{str(category).strip().lower()}:"
        f"{str(symbol).strip().upper()}"
    )


def _bybit_error_text(exc: Exception) -> str:
    return str(exc).lower()


def _required_call(
    *,
    matrix: SnapshotEndpointMatrix,
    endpoint_key: str,
    call: Callable[[], Any],
    default: Any,
) -> Any:
    matrix.require(endpoint_key)

    try:
        result = call()
    except Exception as exc:
        matrix.mark_failure(
            endpoint_key,
            error=exc,
            suppressed=True,
        )
        log.warning(
            "Required Bybit snapshot endpoint failed "
            "endpoint_key=%s error=%s",
            endpoint_key,
            exc,
        )
        return default

    matrix.mark_success(endpoint_key)
    return result


def _is_noncritical_earn_error(exc: Exception) -> bool:
    text = _bybit_error_text(exc)

    patterns = [
        "not eligible",
        "not support",
        "not supported",
        "unsupported",
        "product unavailable",
        "product category invalid",
        "category invalid",
        "invalid category",
        "no vip loan access",
        "no crypto loan access",
        "no permission",
        "permission denied",
        "access denied",
        "not open",
        "not available",
    ]

    return any(pattern in text for pattern in patterns)


def _safe_earn_call(
    client: BybitV5Client,
    *,
    category: str,
    matrix: SnapshotEndpointMatrix,
) -> list[dict[str, Any]]:
    endpoint_key = f"earn:{category}"

    def call() -> list[dict[str, Any]]:
        payload = client.get(
            "/v5/earn/position",
            {
                "category": category,
            },
        )
        return _list_from_result(
            payload,
            "list",
            "rows",
            "data",
        )

    return _required_call(
        matrix=matrix,
        endpoint_key=endpoint_key,
        call=call,
        default=[],
    )


def _public_get(client: BybitV5Client, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    public_get = getattr(client, "public_get", None)
    if callable(public_get):
        return public_get(path, params or {})

    # Compatibility fallback for tests or older client objects.
    return client.get(path, params or {})


def _paginate_get(
    client: BybitV5Client,
    path: str,
    params: dict[str, Any] | None = None,
    *,
    page_limit: int = 50,
) -> list[dict[str, Any]]:
    paginate_get = getattr(client, "paginate_get", None)

    if callable(paginate_get):
        return paginate_get(
            path,
            params or {},
            page_limit=page_limit,
            result_list_key="list",
            cursor_param="cursor",
            cursor_field="nextPageCursor",
        )

    items: list[dict[str, Any]] = []
    cursor = ""
    base_params = dict(params or {})

    for _ in range(max(int(page_limit), 1)):
        page_params = dict(base_params)
        if cursor:
            page_params["cursor"] = cursor

        payload = client.get(path, page_params)
        result = _result(payload)

        chunk = result.get("list") or []
        if isinstance(chunk, list):
            items.extend([row for row in chunk if isinstance(row, dict)])

        next_cursor = str(result.get("nextPageCursor") or "").strip()
        if not next_cursor:
            break

        cursor = next_cursor

    return items


def _fetch_unified_wallet(client: BybitV5Client) -> dict[str, Any]:
    payload = client.get(
        "/v5/account/wallet-balance",
        {
            "accountType": "UNIFIED",
        },
    )

    rows = _list_from_result(payload, "list")
    if not rows:
        raise BybitAllocationSnapshotError(
            "Bybit UNIFIED wallet response does not contain result.list"
        )

    return rows[0]


def _fetch_funding_wallet(client: BybitV5Client) -> dict[str, Any]:
    return client.get(
        "/v5/asset/transfer/query-account-coins-balance",
        {
            "accountType": "FUND",
        },
    )


def _fetch_spot_tickers(client: BybitV5Client) -> dict[str, Any]:
    payload = _public_get(
        client,
        "/v5/market/tickers",
        {
            "category": "spot",
        },
    )

    tickers: dict[str, Any] = {}
    for row in _list_from_result(payload, "list"):
        symbol = normalize_symbol(get_any(row, "symbol"))
        if symbol:
            tickers[symbol] = row

    return tickers


def _tag_position_rows(
    rows: list[dict[str, Any]],
    *,
    category: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    for row in rows:
        item = dict(row)
        item.setdefault("category", category)
        item["_wb_endpoint_category"] = category
        out.append(item)

    return out


def _fetch_positions(
    client: BybitV5Client,
    *,
    inverse_coins: list[str],
    option_coins: list[str],
    matrix: SnapshotEndpointMatrix,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    def fetch_rows(
        *,
        endpoint_key: str,
        params: dict[str, Any],
        category: str,
    ) -> list[dict[str, Any]]:
        rows = _required_call(
            matrix=matrix,
            endpoint_key=endpoint_key,
            call=lambda: _paginate_get(
                client,
                "/v5/position/list",
                params,
            ),
            default=[],
        )

        return _tag_position_rows(
            rows,
            category=category,
        )

    linear_usdt = fetch_rows(
        endpoint_key="positions:linear:USDT",
        params={
            "category": "linear",
            "settleCoin": "USDT",
        },
        category="linear",
    )

    linear_usdc = fetch_rows(
        endpoint_key="positions:linear:USDC",
        params={
            "category": "linear",
            "settleCoin": "USDC",
        },
        category="linear",
    )

    inverse: list[dict[str, Any]] = []

    for coin in inverse_coins:
        inverse.extend(
            fetch_rows(
                endpoint_key=(
                    f"positions:inverse:{coin}"
                ),
                params={
                    "category": "inverse",
                    "settleCoin": coin,
                },
                category="inverse",
            )
        )

    options: list[dict[str, Any]] = []

    for coin in option_coins:
        options.extend(
            fetch_rows(
                endpoint_key=(
                    f"positions:option:{coin}"
                ),
                params={
                    "category": "option",
                    "baseCoin": coin,
                },
                category="option",
            )
        )

    return (
        linear_usdt,
        linear_usdc,
        inverse,
        options,
    )


def _fetch_earn_positions(
    client: BybitV5Client,
    *,
    matrix: SnapshotEndpointMatrix,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for category in EARN_CATEGORIES:
        category_rows = _safe_earn_call(
            client,
            category=category,
            matrix=matrix,
        )

        for row in category_rows:
            item = dict(row)
            item["_wb_earn_category"] = category
            rows.append(item)

    return rows


def _spot_price_for_coin(
    *,
    coin: str | None,
    spot_tickers: dict[str, Any],
) -> Decimal:
    normalized = normalize_coin(coin)

    if not normalized:
        return Decimal("0")

    if normalized in STABLECOINS:
        return Decimal("1")

    symbol = f"{normalized}USDT"
    ticker = spot_tickers.get(symbol)
    if not isinstance(ticker, dict):
        return Decimal("0")

    return dec(
        get_any(
            ticker,
            "lastPrice",
            "last_price",
            "markPrice",
            "indexPrice",
            "bid1Price",
            "ask1Price",
        )
    )


def _usd_value_from_coin_amount(
    *,
    coin: str | None,
    amount: Decimal,
    spot_tickers: dict[str, Any],
) -> Decimal:
    if amount == 0:
        return Decimal("0")

    price = _spot_price_for_coin(coin=coin, spot_tickers=spot_tickers)
    if price <= 0:
        return Decimal("0")

    return amount * price


def _risk_from_unified_wallet(row: dict[str, Any]) -> AllocationAccountRisk:
    return AllocationAccountRisk(
        total_equity_usdt=dec(get_any(row, "totalEquity", "total_equity")),
        total_wallet_balance_usdt=dec(get_any(row, "totalWalletBalance", "total_wallet_balance")),
        total_available_usdt=dec(get_any(row, "totalAvailableBalance", "total_available_balance")),
        total_initial_margin_usdt=dec(get_any(row, "totalInitialMargin", "total_initial_margin")),
        total_maintenance_margin_usdt=dec(get_any(row, "totalMaintenanceMargin", "total_maintenance_margin")),
        account_im_rate=dec(get_any(row, "accountIMRate", "account_im_rate")),
        account_mm_rate=dec(get_any(row, "accountMMRate", "account_mm_rate")),
    )


def _unified_coin_rows(unified_wallet: dict[str, Any]) -> list[dict[str, Any]]:
    coins = unified_wallet.get("coin")
    if isinstance(coins, list):
        return [row for row in coins if isinstance(row, dict)]
    return []


def _funding_coin_rows(funding_payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = _result(funding_payload)

    for key in ["balance", "list", "rows"]:
        value = result.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]

    # Some Bybit-like mock responses wrap balances differently.
    for key in ["coin", "coins"]:
        value = result.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]

    return []


def _holding_from_unified_coin(row: dict[str, Any]) -> AllocationSnapshotHolding | None:
    coin = normalize_coin(get_any(row, "coin", "currency"))
    if not coin:
        return None

    wallet_balance = dec(
        get_any(
            row,
            "walletBalance",
            "wallet_balance",
            "equity",
            "amount",
            "size",
        )
    )
    equity = dec(get_any(row, "equity"))
    usd_value = dec(get_any(row, "usdValue", "usd_value", "value_usdt"))
    available = dec(get_any(row, "availableToWithdraw", "available", "free"))
    locked = dec(get_any(row, "locked"))
    mark_price = dec(get_any(row, "markPrice", "mark_price", "lastPrice"), default="0")

    size = wallet_balance if wallet_balance != 0 else equity
    if usd_value == 0 and _is_stablecoin(coin):
        usd_value = size

    if size == 0 and usd_value == 0:
        return None

    if _is_stablecoin(coin):
        return AllocationSnapshotHolding(
            leg_group="cash",
            leg_type="stable_cash",
            coin=coin,
            symbol=None,
            category="wallet",
            side=None,
            location="UNIFIED",
            size=size,
            usd_value=usd_value,
            notional_usd=None,
            avg_price=None,
            mark_price=mark_price if mark_price != 0 else None,
            leverage=None,
            product=None,
            product_category=None,
            extra={
                "available": available,
                "locked": locked,
                "equity": equity,
                "unrealised_pnl": dec(get_any(row, "unrealisedPnl", "unrealised_pnl")),
                "cum_realised_pnl": dec(get_any(row, "cumRealisedPnl", "cum_realised_pnl")),
                "bonus": dec(get_any(row, "bonus")),
                "source": "bybit_unified_wallet",
            },
        )

    return AllocationSnapshotHolding(
        leg_group="spot",
        leg_type="spot_holding",
        coin=coin,
        symbol=infer_spot_symbol(coin, get_any(row, "symbol")),
        category="spot",
        side=None,
        location="UNIFIED",
        size=size,
        usd_value=usd_value,
        notional_usd=None,
        avg_price=None,
        mark_price=mark_price if mark_price != 0 else None,
        leverage=None,
        product=None,
        product_category=None,
        extra={
            "available": available,
            "locked": locked,
            "equity": equity,
            "unrealised_pnl": dec(get_any(row, "unrealisedPnl", "unrealised_pnl")),
            "cum_realised_pnl": dec(get_any(row, "cumRealisedPnl", "cum_realised_pnl")),
            "bonus": dec(get_any(row, "bonus")),
            "source": "bybit_unified_wallet",
        },
    )


def _holding_from_funding_coin(
    row: dict[str, Any],
    *,
    spot_tickers: dict[str, Any],
) -> AllocationSnapshotHolding | None:
    coin = normalize_coin(get_any(row, "coin", "currency"))
    if not coin:
        return None

    wallet_balance = dec(
        get_any(
            row,
            "walletBalance",
            "wallet_balance",
            "amount",
            "size",
        )
    )
    transfer_balance = dec(
        get_any(
            row,
            "transferBalance",
            "transfer_balance",
            "available",
            "free",
            "transferable",
        )
    )

    usd_value = dec(get_any(row, "usdValue", "usd_value", "value_usdt"))
    if usd_value == 0:
        usd_value = _usd_value_from_coin_amount(
            coin=coin,
            amount=wallet_balance,
            spot_tickers=spot_tickers,
        )

    if wallet_balance == 0 and usd_value == 0:
        return None

    if _is_stablecoin(coin):
        leg_type = "funding_wallet_cash"
        if usd_value == 0:
            usd_value = wallet_balance
    else:
        leg_type = "funding_wallet_asset"

    mark_price = _spot_price_for_coin(coin=coin, spot_tickers=spot_tickers)

    return AllocationSnapshotHolding(
        leg_group="funding_wallet",
        leg_type=leg_type,
        coin=coin,
        symbol=infer_spot_symbol(coin, get_any(row, "symbol")),
        category="funding",
        side=None,
        location="FUND",
        size=wallet_balance,
        usd_value=usd_value,
        notional_usd=None,
        avg_price=None,
        mark_price=mark_price if mark_price != 0 else None,
        leverage=None,
        product=None,
        product_category=None,
        extra={
            "transfer_balance": transfer_balance,
            "source": "bybit_fund_wallet",
        },
    )


def _holding_from_earn_row(
    row: dict[str, Any],
    *,
    spot_tickers: dict[str, Any],
) -> AllocationSnapshotHolding | None:
    coin = normalize_coin(get_any(row, "coin", "currency"))
    if not coin:
        return None

    amount = dec(
        get_any(
            row,
            "amount",
            "orderQty",
            "order_qty",
            "size",
            "totalAmount",
            "total_amount",
        )
    )

    available_key, available_raw = (
        _first_present(
            row,
            (
                "availableAmount",
                "available_amount",
                "available",
            ),
        )
    )
    redeemable_key, redeemable_raw = (
        _first_present(
            row,
            (
                "redeemableAmount",
                "redeemable_amount",
                "redeemable",
                "redeemableUsdt",
                "redeemable_usdt",
            ),
        )
    )
    locked_key, locked_raw = (
        _first_present(
            row,
            (
                "lockedAmount",
                "locked_amount",
                "locked",
            ),
        )
    )

    available_amount = _optional_dec(
        available_raw
    )
    redeemable_amount = _optional_dec(
        redeemable_raw
    )
    locked_amount = _optional_dec(
        locked_raw
    )
    redeemable_known = (
        redeemable_key is not None
    )

    usd_value = dec(
        get_any(
            row,
            "usdValue",
            "usd_value",
            "totalPrincipalAmount",
            "total_principal_amount",
            "value_usdt",
        )
    )

    if usd_value == 0:
        usd_value = _usd_value_from_coin_amount(
            coin=coin,
            amount=amount,
            spot_tickers=spot_tickers,
        )

    if amount == 0 and usd_value == 0:
        return None

    product_category = str(
        get_any(
            row,
            "_wb_earn_category",
            "productCategory",
            "product_category",
            "category",
            default="",
        )
        or ""
    )
    product = str(
        get_any(
            row,
            "product",
            "productName",
            "product_name",
            default=(
                "Flexible Savings"
                if product_category == "FlexibleSaving"
                else "On-chain Staking"
                if product_category == "OnChain"
                else "Earn"
            ),
        )
        or "Earn"
    )

    mark_price = _spot_price_for_coin(coin=coin, spot_tickers=spot_tickers)

    return AllocationSnapshotHolding(
        leg_group="earn",
        leg_type="earn_holding",
        coin=coin,
        symbol=infer_spot_symbol(coin, get_any(row, "symbol")),
        category="earn",
        side=None,
        location="EARN",
        size=amount,
        usd_value=usd_value,
        notional_usd=None,
        avg_price=None,
        mark_price=mark_price if mark_price != 0 else None,
        leverage=None,
        product=product,
        product_category=product_category,
        extra={
            "apr": dec(
                get_any(
                    row,
                    "apr",
                    "annualRate",
                    "annual_rate",
                )
            ),
            "status": get_any(
                row,
                "status",
                default="",
            ),
            "total_amount": amount,
            "available_amount": available_amount,
            "available_amount_source": (
                available_key
            ),
            "redeemable_amount": (
                redeemable_amount
            ),
            "redeemable_usdt": (
                redeemable_amount
                if coin == "USDT"
                and redeemable_known
                else None
            ),
            "redeemable_amount_source": (
                redeemable_key
            ),
            "redeemable_known": (
                redeemable_known
            ),
            "locked_amount": locked_amount,
            "locked_amount_source": (
                locked_key
            ),
            "product_id": get_any(
                row,
                "productId",
                "product_id",
            ),
            "product_category": (
                product_category
            ),
            "product_status": get_any(
                row,
                "status",
                default="",
            ),
            "precision": get_any(
                row,
                "precision",
                "amountPrecision",
                "amount_precision",
                "minAccuracy",
            ),
            "source_endpoint": (
                "/v5/earn/position"
            ),
            "source": "bybit_earn",
        },
    )


def _holding_from_position(row: dict[str, Any]) -> AllocationSnapshotHolding | None:
    category = str(get_any(row, "category", "_wb_endpoint_category", default="") or "").lower()
    symbol = normalize_symbol(get_any(row, "symbol"))
    side = normalize_side(get_any(row, "side"))
    size = dec(get_any(row, "size", "qty", "position_size"))
    avg_price = dec(get_any(row, "avgPrice", "avg_price", "entryPrice"))
    mark_price = dec(get_any(row, "markPrice", "mark_price"))
    position_value = dec(get_any(row, "positionValue", "position_value", "notionalUsd", "notional_usd"))
    leverage = dec(get_any(row, "leverage"))

    position_idx = _optional_int(
        get_any(
            row,
            "positionIdx",
            "position_idx",
        )
    )
    settle_coin = normalize_coin(
        get_any(
            row,
            "settleCoin",
            "settle_coin",
        )
    )
    position_im = _optional_dec(
        get_any(
            row,
            "positionIM",
            "position_im",
        )
    )
    position_mm = _optional_dec(
        get_any(
            row,
            "positionMM",
            "position_mm",
        )
    )

    if size == 0 and position_value == 0:
        return None

    notional = abs(position_value)
    if notional == 0 and size != 0 and mark_price != 0:
        notional = abs(size * mark_price)

    contract_type = classify_contract(
        category,
        symbol,
        explicit_contract_type=get_any(row, "contractType", "contract_type"),
    )

    if contract_type in {"LinearPerpetual", "InversePerpetual"}:
        leg_group = "perp"
        leg_type = "perp_position"
    elif contract_type in {"LinearFutures", "InverseFutures"}:
        leg_group = "future"
        leg_type = "future_position"
    elif contract_type == "Option" and (side or "").lower() == "sell":
        leg_group = "short_option"
        leg_type = "short_option_position"
    elif contract_type == "Option":
        leg_group = "long_option"
        leg_type = "long_option_position"
    else:
        leg_group = "other"
        leg_type = "unknown_position"

    return AllocationSnapshotHolding(
        leg_group=leg_group,
        leg_type=leg_type,
        coin=normalize_coin(get_any(row, "coin", "baseCoin", "base_coin")),
        symbol=symbol,
        category=category or None,
        side=side,
        location="UNIFIED",
        size=size,
        usd_value=None,
        notional_usd=notional,
        avg_price=avg_price if avg_price != 0 else None,
        mark_price=mark_price if mark_price != 0 else None,
        leverage=leverage if leverage != 0 else None,
        product=contract_type,
        product_category=category or None,
        extra={
            "unrealised_pnl": dec(get_any(row, "unrealisedPnl", "unrealised_pnl")),
            "cum_realised_pnl": dec(get_any(row, "cumRealisedPnl", "cum_realised_pnl")),
            "source": "bybit_position",
            "contract_type": contract_type,
            "position_side": side,
            "position_idx": position_idx,
            "position_value": position_value,
            "settle_coin": settle_coin,
            "position_im": position_im,
            "position_mm": position_mm,
        },
    )


def _build_holdings_from_raw(raw: BybitReadonlyRawData) -> list[AllocationSnapshotHolding]:
    holdings: list[AllocationSnapshotHolding] = []

    for row in _unified_coin_rows(raw.unified_wallet):
        holding = _holding_from_unified_coin(row)
        if holding is not None:
            holdings.append(holding)

    for row in _funding_coin_rows(raw.funding_wallet):
        holding = _holding_from_funding_coin(
            row,
            spot_tickers=raw.spot_tickers,
        )
        if holding is not None:
            holdings.append(holding)

    for row in raw.earn_positions:
        holding = _holding_from_earn_row(
            row,
            spot_tickers=raw.spot_tickers,
        )
        if holding is not None:
            holdings.append(holding)

    for row in (
        raw.linear_usdt_positions
        + raw.linear_usdc_positions
        + raw.inverse_positions
        + raw.option_positions
    ):
        holding = _holding_from_position(row)
        if holding is not None:
            holdings.append(holding)

    return [
        _attach_instrument_info(
            holding,
            instruments=raw.instruments,
        )
        for holding in holdings
    ]


def _collect_required_instrument_specs(
    *,
    unified_wallet: dict[str, Any],
    funding_wallet: dict[str, Any],
    earn_positions: list[dict[str, Any]],
    position_rows: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    specs: set[tuple[str, str]] = set()

    def add(
        category: str | None,
        symbol: str | None,
    ) -> None:
        normalized_category = str(
            category or ""
        ).strip().lower()
        normalized_symbol = normalize_symbol(
            symbol
        )

        if (
            normalized_category
            and normalized_symbol
        ):
            specs.add(
                (
                    normalized_category,
                    normalized_symbol,
                )
            )

    for row in _unified_coin_rows(
        unified_wallet
    ):
        coin = normalize_coin(
            get_any(row, "coin", "currency")
        )

        if coin and not _is_stablecoin(coin):
            add(
                "spot",
                infer_spot_symbol(
                    coin,
                    get_any(row, "symbol"),
                ),
            )

    for row in _funding_coin_rows(
        funding_wallet
    ):
        coin = normalize_coin(
            get_any(row, "coin", "currency")
        )

        if coin and not _is_stablecoin(coin):
            add(
                "spot",
                infer_spot_symbol(
                    coin,
                    get_any(row, "symbol"),
                ),
            )

    for row in earn_positions:
        coin = normalize_coin(
            get_any(row, "coin", "currency")
        )

        if coin and not _is_stablecoin(coin):
            add(
                "spot",
                infer_spot_symbol(
                    coin,
                    get_any(row, "symbol"),
                ),
            )

    for row in position_rows:
        add(
            str(
                get_any(
                    row,
                    "category",
                    "_wb_endpoint_category",
                    default="",
                )
                or ""
            ),
            normalize_symbol(
                get_any(row, "symbol")
            ),
        )

    return sorted(specs)


def _fetch_required_instruments(
    client: BybitV5Client,
    *,
    specs: list[tuple[str, str]],
    matrix: SnapshotEndpointMatrix,
    captured_at: datetime,
) -> dict[str, BybitInstrumentInfo]:
    instruments: dict[
        str,
        BybitInstrumentInfo,
    ] = {}

    for category, symbol in specs:
        endpoint_key = _instrument_endpoint_key(
            category=category,
            symbol=symbol,
        )
        matrix.require(endpoint_key)

        try:
            info = query_instrument_info(
                client,
                category=category,
                symbol=symbol,
                captured_at=captured_at,
            )
        except Exception as exc:
            matrix.mark_failure(
                endpoint_key,
                error=exc,
                suppressed=True,
                metadata={
                    "category": category,
                    "symbol": symbol,
                },
            )
            continue

        lookup_key = _instrument_lookup_key(
            category=category,
            symbol=symbol,
        )
        instruments[lookup_key] = info

        if info.preflight_complete:
            matrix.mark_success(endpoint_key)
        else:
            matrix.mark_failure(
                endpoint_key,
                error=(
                    "instrument_preflight_incomplete:"
                    + ",".join(
                        info.completeness_reasons
                    )
                ),
                suppressed=True,
                metadata={
                    "category": category,
                    "symbol": symbol,
                },
            )

    return instruments


def _holding_instrument_category(
    holding: AllocationSnapshotHolding,
) -> str | None:
    if holding.leg_group in {
        "spot",
        "funding_wallet",
        "earn",
    }:
        return "spot"

    category = str(
        holding.category or ""
    ).strip().lower()

    return category or None


def _attach_instrument_info(
    holding: AllocationSnapshotHolding,
    *,
    instruments: dict[
        str,
        BybitInstrumentInfo,
    ],
) -> AllocationSnapshotHolding:
    category = _holding_instrument_category(
        holding
    )
    symbol = normalize_symbol(
        holding.symbol
    )

    if not category or not symbol:
        return holding

    key = _instrument_lookup_key(
        category=category,
        symbol=symbol,
    )
    info = instruments.get(key)

    if info is None:
        return holding

    extra = dict(holding.extra)
    extra.update(
        {
            "instrument_status": info.status,
            "instrument_preflight_complete": (
                info.preflight_complete
            ),
            "instrument_completeness_reasons": (
                list(
                    info.completeness_reasons
                )
            ),
            "instrument_info": info.to_dict(),
        }
    )

    return replace(
        holding,
        extra=extra,
    )

def _fetch_bybit_readonly_raw_data(
    client: BybitV5Client,
    *,
    inverse_coins: list[str],
    option_coins: list[str],
) -> BybitReadonlyRawData:
    captured_at = utcnow()
    matrix = SnapshotEndpointMatrix()

    unified_wallet = _required_call(
        matrix=matrix,
        endpoint_key="wallet:UNIFIED",
        call=lambda: _fetch_unified_wallet(
            client
        ),
        default={},
    )

    funding_wallet = _required_call(
        matrix=matrix,
        endpoint_key="wallet:FUND",
        call=lambda: _fetch_funding_wallet(
            client
        ),
        default={},
    )

    spot_tickers = _required_call(
        matrix=matrix,
        endpoint_key="tickers:spot",
        call=lambda: _fetch_spot_tickers(
            client
        ),
        default={},
    )

    (
        linear_usdt_positions,
        linear_usdc_positions,
        inverse_positions,
        option_positions,
    ) = _fetch_positions(
        client,
        inverse_coins=inverse_coins,
        option_coins=option_coins,
        matrix=matrix,
    )

    earn_positions = _fetch_earn_positions(
        client,
        matrix=matrix,
    )

    all_position_rows = [
        *linear_usdt_positions,
        *linear_usdc_positions,
        *inverse_positions,
        *option_positions,
    ]

    instrument_specs = (
        _collect_required_instrument_specs(
            unified_wallet=unified_wallet,
            funding_wallet=funding_wallet,
            earn_positions=earn_positions,
            position_rows=all_position_rows,
        )
    )

    instruments = (
        _fetch_required_instruments(
            client,
            specs=instrument_specs,
            matrix=matrix,
            captured_at=captured_at,
        )
    )

    return BybitReadonlyRawData(
        unified_wallet=unified_wallet,
        funding_wallet=funding_wallet,
        spot_tickers=spot_tickers,
        linear_usdt_positions=(
            linear_usdt_positions
        ),
        linear_usdc_positions=(
            linear_usdc_positions
        ),
        inverse_positions=inverse_positions,
        option_positions=option_positions,
        earn_positions=earn_positions,
        instruments=instruments,
        suppressed_errors=list(
            matrix.suppressed_errors
        ),
        endpoint_matrix=matrix,
        captured_at=captured_at,
    )


def build_allocation_snapshot_from_bybit(
    db: Session,
    *,
    fund_id: int,
    history_days: int = 30,
    inverse_coins: list[str] | None = None,
    option_coins: list[str] | None = None,
    client: BybitV5Client | None = None,
) -> AllocationSnapshot:
    """
    Build allocation snapshot from real Bybit read-only endpoints.

    Stage 22.2.1:
    - production-style service is implemented;
    - tests must use fake/mocked client;
    - do not run real Bybit calls until a later approved stage.

    Credentials:
    - if client is None, uses per-fund encrypted Bybit subaccount credentials
      via get_active_fund_bybit_client(...);
    - master API is not used here.

    Read-only only:
    - no trades;
    - no transfers;
    - no Earn stake.
    """
    del history_days  # reserved for later historical extensions

    fund_code = _get_fund_code(db, fund_id=fund_id)

    effective_client = client
    if effective_client is None:
        effective_client = get_active_fund_bybit_client(
            db,
            fund_id=fund_id,
            coin="USDT",
            chain_type="BSC",
        )

    inv_coins = [normalize_coin(x) or "" for x in (inverse_coins or DEFAULT_INVERSE_COINS)]
    inv_coins = [x for x in inv_coins if x]

    opt_coins = [normalize_coin(x) or "" for x in (option_coins or DEFAULT_OPTION_COINS)]
    opt_coins = [x for x in opt_coins if x]

    raw = _fetch_bybit_readonly_raw_data(
        effective_client,
        inverse_coins=inv_coins,
        option_coins=opt_coins,
    )

    risk = _risk_from_unified_wallet(raw.unified_wallet)
    holdings = _build_holdings_from_raw(raw)

    endpoint_matrix_json = (
        raw.endpoint_matrix.to_dict(
            captured_at=raw.captured_at,
        )
    )

    raw_summary_json = {
        "snapshot_complete": (
            raw.endpoint_matrix.snapshot_complete
        ),
        "completeness_reasons": list(
            raw.endpoint_matrix
            .completeness_reasons
        ),
        "required_endpoints": list(
            raw.endpoint_matrix
            .required_endpoints
        ),
        "successful_endpoints": list(
            raw.endpoint_matrix
            .successful_endpoints
        ),
        "failed_endpoints": list(
            raw.endpoint_matrix
            .failed_endpoints
        ),
        "captured_at": (
            raw.captured_at.isoformat()
        ),
        "source_account": (
            f"fund:{fund_id}:UNIFIED"
        ),
        "fund_id": fund_id,
        "fund_code": fund_code,
        "endpoint_matrix": (
            endpoint_matrix_json
        ),
        "unified_wallet": json_safe(
            raw.unified_wallet
        ),
        "funding_wallet": json_safe(
            raw.funding_wallet
        ),
        "spot_tickers_count": len(
            raw.spot_tickers
        ),
        "positions_count": {
            "linear_usdt": len(
                raw.linear_usdt_positions
            ),
            "linear_usdc": len(
                raw.linear_usdc_positions
            ),
            "inverse": len(
                raw.inverse_positions
            ),
            "option": len(
                raw.option_positions
            ),
        },
        "earn_positions_count": len(
            raw.earn_positions
        ),
        "instruments_count": len(
            raw.instruments
        ),
        "instruments": {
            key: info.to_dict()
            for key, info
            in raw.instruments.items()
        },
        "suppressed_errors": json_safe(
            raw.suppressed_errors
        ),
        "read_only_paths": [
            "/v5/account/wallet-balance",
            (
                "/v5/asset/transfer/"
                "query-account-coins-balance"
            ),
            "/v5/market/tickers",
            "/v5/position/list",
            "/v5/earn/position",
            "/v5/market/instruments-info",
        ],
    }

    return AllocationSnapshot(
        fund_id=fund_id,
        fund_code=fund_code,
        snapshot_ts=raw.captured_at,
        account_type="UNIFIED",
        risk=risk,
        holdings=holdings,
        raw_summary_json=raw_summary_json,
        snapshot_source="bybit_readonly",
        snapshot_complete=(
            raw.endpoint_matrix.snapshot_complete
        ),
        completeness_reasons=(
            raw.endpoint_matrix
            .completeness_reasons
        ),
        required_endpoints=(
            raw.endpoint_matrix
            .required_endpoints
        ),
        successful_endpoints=(
            raw.endpoint_matrix
            .successful_endpoints
        ),
        failed_endpoints=(
            raw.endpoint_matrix
            .failed_endpoints
        ),
        suppressed_errors=tuple(
            raw.suppressed_errors
        ),
        captured_at=raw.captured_at,
        source_account=(
            f"fund:{fund_id}:UNIFIED"
        ),
    )