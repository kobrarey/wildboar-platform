from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from app.config import settings
from app.navcalc.bybit_client import BybitApiError, BybitClient
from app.navcalc.exceptions import (
    BybitNetworkError,
    InvalidWalletResponseError,
    NavSanityCheckError,
)
from app.navcalc.schemas import FundNavConfig, NavResult


STABLECOINS = {
    "USDT",
    "USDC",
    "DAI",
    "BUSD",
    "TUSD",
    "USDE",
    "FDUSD",
    "PYUSD",
    "USD",
    "USDP",
}

SUPPRESSED_EARN_ERROR_CODES = {
    176201,  # VIP loan unavailable
    180001,  # Earn category unavailable
    177003,  # Crypto loan probe not available
    181001,  # Earn not eligible
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_decimal(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def _coin_to_usd(coin: str, amount: Decimal, prices: dict[str, Decimal]) -> Decimal:
    if amount == 0:
        return Decimal(0)

    coin = (coin or "").upper()
    if coin in STABLECOINS:
        return amount

    for quote in ("USDT", "USDC", "USD"):
        symbol = f"{coin}{quote}"
        if symbol in prices:
            return amount * prices[symbol]

    return Decimal(0)


def _fetch_wallet_summary(client: BybitClient) -> tuple[Decimal, list[dict[str, Any]]]:
    raw = client.get("/v5/account/wallet-balance", accountType="UNIFIED")
    lst = raw.get("list") or []
    if not lst:
        raise InvalidWalletResponseError("UNIFIED wallet response has no 'list'")

    head = lst[0]
    raw_coins = head.get("coin")
    if raw_coins is None:
        raise InvalidWalletResponseError("UNIFIED wallet response has no 'coin' array")

    total_equity = _to_decimal(head.get("totalEquity"))
    coins: list[dict[str, Any]] = []

    for row in raw_coins:
        coin = str(row.get("coin") or "").upper()
        if not coin:
            continue

        usd_value = _to_decimal(row.get("usdValue"))
        wallet_balance = _to_decimal(row.get("walletBalance"))
        equity = _to_decimal(row.get("equity"))

        if wallet_balance == 0 and equity == 0 and usd_value == 0:
            continue

        coins.append(
            {
                "coin": coin,
                "usd_value": usd_value,
                "wallet_balance": wallet_balance,
                "equity": equity,
            }
        )

    if not coins and total_equity == 0:
        raise InvalidWalletResponseError(
            "UTA wallet not obtained: no coins and totalEquity == 0"
        )

    return total_equity, coins


def _fetch_spot_prices(client: BybitClient) -> dict[str, Decimal]:
    raw = client.public_get("/v5/market/tickers", category="spot")
    rows = raw.get("list") or []
    if not rows:
        raise BybitNetworkError("Spot tickers response is empty")

    prices: dict[str, Decimal] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        last_price = _to_decimal(row.get("lastPrice"))
        if symbol and last_price > 0:
            prices[symbol] = last_price

    if not prices:
        raise BybitNetworkError("Spot tickers parsed to empty price map")

    return prices


def _fetch_funding_wallet(client: BybitClient, prices: dict[str, Decimal]) -> tuple[Decimal, list[dict[str, Any]]]:
    raw = client.get(
        "/v5/asset/transfer/query-account-coins-balance",
        accountType="FUND",
    )

    balance = raw.get("balance")
    if balance is None:
        raise InvalidWalletResponseError("Funding wallet response has no 'balance' field")

    rows: list[dict[str, Any]] = []
    total = Decimal(0)

    for entry in balance:
        coin = str(entry.get("coin") or "").upper()
        wallet_balance = _to_decimal(entry.get("walletBalance"))
        if not coin or wallet_balance == 0:
            continue

        usd_value = _coin_to_usd(coin, wallet_balance, prices)
        total += usd_value

        rows.append(
            {
                "coin": coin,
                "wallet_balance": wallet_balance,
                "usd_value": usd_value,
            }
        )

    return total, rows


def _fetch_earn_category(
    client: BybitClient,
    category: str,
    prices: dict[str, Decimal],
) -> tuple[Decimal, list[dict[str, Any]]]:
    raw = client.get("/v5/earn/position", category=category)
    rows = raw.get("list")
    if rows is None:
        raise InvalidWalletResponseError(f"Earn response has no 'list' for category={category}")

    total = Decimal(0)
    out: list[dict[str, Any]] = []

    for row in rows:
        coin = str(row.get("coin") or "").upper()
        amount = _to_decimal(row.get("amount") or row.get("orderQty"))
        if not coin or amount == 0:
            continue

        usd_value = _to_decimal(row.get("totalPrincipalAmount"))
        if usd_value == 0:
            usd_value = _coin_to_usd(coin, amount, prices)

        total += usd_value
        out.append(
            {
                "product": category,
                "coin": coin,
                "amount": amount,
                "usd_value": usd_value,
            }
        )

    return total, out


def _fetch_crypto_loan(client: BybitClient, prices: dict[str, Decimal]) -> tuple[Decimal, list[dict[str, Any]]]:
    try:
        raw = client.get("/v5/crypto-loan/ongoing-orders")
    except BybitApiError as exc:
        if exc.code in SUPPRESSED_EARN_ERROR_CODES:
            return Decimal(0), []
        raise

    rows = raw.get("list") or []
    total = Decimal(0)
    out: list[dict[str, Any]] = []

    for row in rows:
        coin = str(row.get("loanCurrency") or "").upper()
        amount = _to_decimal(row.get("loanAmount"))
        if not coin or amount == 0:
            continue

        usd_value = -_coin_to_usd(coin, amount, prices)
        total += usd_value
        out.append(
            {
                "product": "CryptoLoan",
                "coin": coin,
                "amount": -amount,
                "usd_value": usd_value,
            }
        )

    return total, out


def _fetch_vip_loan(client: BybitClient, prices: dict[str, Decimal]) -> tuple[Decimal, list[dict[str, Any]]]:
    try:
        raw = client.get("/v5/ins-loan/loan-order")
    except BybitApiError as exc:
        if exc.code in SUPPRESSED_EARN_ERROR_CODES:
            return Decimal(0), []
        raise

    rows = raw.get("loanInfo") or []
    total = Decimal(0)
    out: list[dict[str, Any]] = []

    for row in rows:
        coin = str(row.get("loanCurrency") or "").upper()
        amount = _to_decimal(row.get("loanBalance") or row.get("loanAmount"))
        if not coin or amount == 0:
            continue

        usd_value = -_coin_to_usd(coin, amount, prices)
        total += usd_value
        out.append(
            {
                "product": "VipLoan",
                "coin": coin,
                "amount": -amount,
                "usd_value": usd_value,
            }
        )

    return total, out


def _fetch_earn_total(client: BybitClient, prices: dict[str, Decimal]) -> tuple[Decimal, list[dict[str, Any]]]:
    total = Decimal(0)
    rows: list[dict[str, Any]] = []

    flex_total, flex_rows = _fetch_earn_category(client, "FlexibleSaving", prices)
    total += flex_total
    rows.extend(flex_rows)

    onchain_total, onchain_rows = _fetch_earn_category(client, "OnChain", prices)
    total += onchain_total
    rows.extend(onchain_rows)

    crypto_loan_total, crypto_loan_rows = _fetch_crypto_loan(client, prices)
    total += crypto_loan_total
    rows.extend(crypto_loan_rows)

    vip_total, vip_rows = _fetch_vip_loan(client, prices)
    total += vip_total
    rows.extend(vip_rows)

    return total, rows


def _sum_cash(uta_coins: list[dict[str, Any]]) -> Decimal:
    return sum(
        (row["usd_value"] for row in uta_coins if row["coin"] in STABLECOINS),
        Decimal(0),
    )


def _sum_spot(uta_coins: list[dict[str, Any]]) -> Decimal:
    return sum(
        (row["usd_value"] for row in uta_coins if row["coin"] not in STABLECOINS),
        Decimal(0),
    )


def _sanity_check(cash_spot: Decimal, total_equity: Decimal) -> Decimal:
    diff_abs = abs(cash_spot - total_equity)

    if total_equity == 0:
        diff_pct = Decimal(0) if cash_spot == 0 else Decimal(100)
    else:
        diff_pct = (diff_abs / abs(total_equity)) * Decimal(100)

    tol_pct = Decimal(settings.BYBIT_NAV_EQUITY_TOL_PCT)
    if diff_pct > tol_pct:
        raise NavSanityCheckError(
            f"Sanity-check failed: cash+spot={cash_spot:.2f}, "
            f"uta_equity={total_equity:.2f}, diff_pct={diff_pct:.4f}, tol_pct={tol_pct}"
        )

    return diff_pct


def compute_nav(cfg: FundNavConfig) -> NavResult:
    client = BybitClient(
        cfg.bybit_api_key,
        cfg.bybit_api_secret,
        testnet=cfg.bybit_testnet,
    )

    total_equity, uta_coins = _fetch_wallet_summary(client)
    prices = _fetch_spot_prices(client)
    funding_total, funding_rows = _fetch_funding_wallet(client, prices)
    earn_total, earn_rows = _fetch_earn_total(client, prices)

    cash = _sum_cash(uta_coins)
    spot = _sum_spot(uta_coins)
    cash_spot = cash + spot

    diff_pct = _sanity_check(cash_spot, total_equity)
    nav_usd = cash + spot + funding_total + earn_total

    return NavResult(
        fund_code=cfg.fund_code,
        snapshot_ts=_utcnow(),
        nav_usd=nav_usd,
        uta_equity_usd=total_equity,
        funding_wallet_usd=funding_total,
        earn_usd=earn_total,
        sanity_check_passed=True,
        source="bybit_v5",
        raw_meta={
            "cash_usd": str(cash),
            "spot_usd": str(spot),
            "cash_plus_spot_usd": str(cash_spot),
            "equity_diff_pct": str(diff_pct),
            "uta_coin_count": len(uta_coins),
            "funding_coin_count": len(funding_rows),
            "earn_item_count": len(earn_rows),
            "bybit_testnet": cfg.bybit_testnet,
        },
    )