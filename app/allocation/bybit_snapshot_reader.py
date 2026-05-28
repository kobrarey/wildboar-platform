from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
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
from app.bybit.client import BybitApiError, BybitV5Client
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
    suppressed_errors: list[dict[str, Any]]


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


def _bybit_error_text(exc: Exception) -> str:
    return str(exc).lower()


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
    suppressed_errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    try:
        payload = client.get(
            "/v5/earn/position",
            {
                "category": category,
            },
        )
        return _list_from_result(payload, "list", "rows", "data")
    except Exception as exc:
        if _is_noncritical_earn_error(exc):
            suppressed_errors.append(
                {
                    "endpoint": "/v5/earn/position",
                    "category": category,
                    "error": str(exc),
                    "suppressed": True,
                }
            )
            log.info(
                "Bybit Earn endpoint skipped category=%s reason=%s",
                category,
                exc,
            )
            return []

        raise


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


def _fetch_positions(
    client: BybitV5Client,
    *,
    inverse_coins: list[str],
    option_coins: list[str],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    linear_usdt = _paginate_get(
        client,
        "/v5/position/list",
        {
            "category": "linear",
            "settleCoin": "USDT",
        },
    )
    linear_usdc = _paginate_get(
        client,
        "/v5/position/list",
        {
            "category": "linear",
            "settleCoin": "USDC",
        },
    )

    inverse: list[dict[str, Any]] = []
    for coin in inverse_coins:
        inverse.extend(
            _paginate_get(
                client,
                "/v5/position/list",
                {
                    "category": "inverse",
                    "settleCoin": coin,
                },
            )
        )

    options: list[dict[str, Any]] = []
    for coin in option_coins:
        options.extend(
            _paginate_get(
                client,
                "/v5/position/list",
                {
                    "category": "option",
                    "baseCoin": coin,
                },
            )
        )

    return linear_usdt, linear_usdc, inverse, options


def _fetch_earn_positions(
    client: BybitV5Client,
    *,
    suppressed_errors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for category in EARN_CATEGORIES:
        category_rows = _safe_earn_call(
            client,
            category=category,
            suppressed_errors=suppressed_errors,
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
            "apr": dec(get_any(row, "apr", "annualRate", "annual_rate")),
            "status": get_any(row, "status", default=""),
            "source": "bybit_earn",
        },
    )


def _holding_from_position(row: dict[str, Any]) -> AllocationSnapshotHolding | None:
    category = str(get_any(row, "category", default="") or "").lower()
    symbol = normalize_symbol(get_any(row, "symbol"))
    side = normalize_side(get_any(row, "side"))
    size = dec(get_any(row, "size", "qty", "position_size"))
    avg_price = dec(get_any(row, "avgPrice", "avg_price", "entryPrice"))
    mark_price = dec(get_any(row, "markPrice", "mark_price"))
    position_value = dec(get_any(row, "positionValue", "position_value", "notionalUsd", "notional_usd"))
    leverage = dec(get_any(row, "leverage"))

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

    return holdings


def _fetch_bybit_readonly_raw_data(
    client: BybitV5Client,
    *,
    inverse_coins: list[str],
    option_coins: list[str],
) -> BybitReadonlyRawData:
    suppressed_errors: list[dict[str, Any]] = []

    unified_wallet = _fetch_unified_wallet(client)
    funding_wallet = _fetch_funding_wallet(client)
    spot_tickers = _fetch_spot_tickers(client)

    (
        linear_usdt_positions,
        linear_usdc_positions,
        inverse_positions,
        option_positions,
    ) = _fetch_positions(
        client,
        inverse_coins=inverse_coins,
        option_coins=option_coins,
    )

    earn_positions = _fetch_earn_positions(
        client,
        suppressed_errors=suppressed_errors,
    )

    return BybitReadonlyRawData(
        unified_wallet=unified_wallet,
        funding_wallet=funding_wallet,
        spot_tickers=spot_tickers,
        linear_usdt_positions=linear_usdt_positions,
        linear_usdc_positions=linear_usdc_positions,
        inverse_positions=inverse_positions,
        option_positions=option_positions,
        earn_positions=earn_positions,
        suppressed_errors=suppressed_errors,
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

    raw_summary_json = {
        "unified_wallet": json_safe(raw.unified_wallet),
        "funding_wallet": json_safe(raw.funding_wallet),
        "spot_tickers_count": len(raw.spot_tickers),
        "positions_count": {
            "linear_usdt": len(raw.linear_usdt_positions),
            "linear_usdc": len(raw.linear_usdc_positions),
            "inverse": len(raw.inverse_positions),
            "option": len(raw.option_positions),
        },
        "earn_positions_count": len(raw.earn_positions),
        "suppressed_errors": json_safe(raw.suppressed_errors),
        "read_only_endpoints": [
            "/v5/account/wallet-balance",
            "/v5/asset/transfer/query-account-coins-balance",
            "/v5/market/tickers",
            "/v5/position/list",
            "/v5/earn/position",
        ],
    }

    return AllocationSnapshot(
        fund_id=fund_id,
        fund_code=fund_code,
        snapshot_ts=utcnow(),
        account_type="UNIFIED",
        risk=risk,
        holdings=holdings,
        raw_summary_json=raw_summary_json,
        snapshot_source="bybit_readonly",
    )