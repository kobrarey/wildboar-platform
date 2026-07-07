from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import logging
from typing import Any

from app.bybit.client import BybitV5Client

log = logging.getLogger("app.bybit.earn")

class BybitEarnError(RuntimeError):
    pass


@dataclass(frozen=True)
class BybitEarnProduct:
    category: str
    coin: str
    product_id: str
    status: str
    precision: int | None
    min_stake_amount: Decimal | None
    max_stake_amount: Decimal | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class BybitEarnPosition:
    category: str
    coin: str
    product_id: str
    amount: Decimal
    available_amount: Decimal
    freeze_details: Any
    position_id: str | None
    status: str | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class BybitEarnOrder:
    category: str
    coin: str | None
    order_type: str | None
    order_id: str | None
    order_link_id: str | None
    status: str | None
    amount: Decimal
    product_id: str | None
    raw: dict[str, Any]


def _dec(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except Exception:
        return None


def format_bybit_earn_amount(
    amount: Decimal,
    precision: int,
    rounding: str,
) -> str:
    if precision is None:
        raise BybitEarnError("Earn product precision is missing")

    precision = int(precision)
    if precision < 0:
        raise BybitEarnError(f"Earn product precision must be non-negative: {precision}")

    amount = _dec(amount)
    if amount <= Decimal("0"):
        raise BybitEarnError(f"Earn amount must be positive: {amount}")

    rounding_mode = {
        "up": ROUND_UP,
        "down": ROUND_DOWN,
    }.get(str(rounding).strip().lower())

    if rounding_mode is None:
        raise BybitEarnError(f"Unsupported Earn amount rounding mode: {rounding}")

    quantum = Decimal("1").scaleb(-precision)
    rounded = amount.quantize(quantum, rounding=rounding_mode)

    text = format(rounded, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")

    if text in {"", "-0"}:
        text = "0"

    if "E" in text.upper():
        raise BybitEarnError(f"Formatted Earn amount uses scientific notation: {text}")

    return text


def list_earn_products(
    client: BybitV5Client,
    *,
    category: str = "FlexibleSaving",
    coin: str = "USDT",
) -> list[BybitEarnProduct]:
    payload = client.get(
        "/v5/earn/product",
        {
            "category": category,
            "coin": coin,
        },
    )
    rows = ((payload.get("result") or {}).get("list") or [])
    products: list[BybitEarnProduct] = []

    for row in rows:
        if not isinstance(row, dict):
            continue

        products.append(
            BybitEarnProduct(
                category=str(row.get("category") or category),
                coin=str(row.get("coin") or coin),
                product_id=str(row.get("productId") or ""),
                status=str(row.get("status") or ""),
                precision=_int_or_none(row.get("precision")),
                min_stake_amount=(
                    _dec(row.get("minStakeAmount"))
                    if row.get("minStakeAmount") not in {None, ""}
                    else None
                ),
                max_stake_amount=(
                    _dec(row.get("maxStakeAmount"))
                    if row.get("maxStakeAmount") not in {None, ""}
                    else None
                ),
                raw=row,
            )
        )

    return products


def resolve_flexible_saving_product(
    client: BybitV5Client,
    *,
    coin: str = "USDT",
) -> BybitEarnProduct:
    products = list_earn_products(
        client,
        category="FlexibleSaving",
        coin=coin,
    )

    available = [
        item
        for item in products
        if item.product_id
        and item.coin.upper() == coin.upper()
        and item.category == "FlexibleSaving"
        and item.status == "Available"
    ]

    if not available:
        raise BybitEarnError(
            f"Available FlexibleSaving Earn product not found for coin={coin}"
        )

    product = available[0]
    if product.precision is None:
        raise BybitEarnError(
            "FlexibleSaving Earn product precision is missing: "
            f"coin={coin}, product_id={product.product_id}, raw={product.raw}"
        )

    return product


def resolve_flexible_saving_product_id(
    client: BybitV5Client,
    *,
    coin: str = "USDT",
) -> str:
    return resolve_flexible_saving_product(
        client,
        coin=coin,
    ).product_id


def get_earn_positions(
    client: BybitV5Client,
    *,
    category: str = "FlexibleSaving",
    coin: str = "USDT",
    product_id: str | None = None,
) -> list[BybitEarnPosition]:
    params: dict[str, Any] = {
        "category": category,
        "coin": coin,
    }
    if product_id:
        params["productId"] = product_id

    payload = client.get("/v5/earn/position", params)
    rows = ((payload.get("result") or {}).get("list") or [])

    positions: list[BybitEarnPosition] = []
    for row in rows:
        if not isinstance(row, dict):
            continue

        row_product_id = str(row.get("productId") or "")
        if product_id and row_product_id != str(product_id):
            continue

        row_coin = str(row.get("coin") or coin)
        if row_coin.upper() != coin.upper():
            continue

        positions.append(
            BybitEarnPosition(
                category=category,
                coin=row_coin,
                product_id=row_product_id,
                amount=_dec(row.get("amount")),
                available_amount=_dec(row.get("availableAmount")),
                freeze_details=row.get("freezeDetails"),
                position_id=(
                    str(row.get("id"))
                    if row.get("id") not in {None, ""}
                    else None
                ),
                status=(
                    str(row.get("status"))
                    if row.get("status") not in {None, ""}
                    else None
                ),
                raw=row,
            )
        )

    return positions


def total_flexible_saving_amount(
    client: BybitV5Client,
    *,
    coin: str = "USDT",
    product_id: str | None = None,
) -> Decimal:
    return sum(
        (item.amount for item in get_earn_positions(
            client,
            category="FlexibleSaving",
            coin=coin,
            product_id=product_id,
        )),
        Decimal("0"),
    )


def total_flexible_saving_available_amount(
    client: BybitV5Client,
    *,
    coin: str = "USDT",
    product_id: str | None = None,
) -> Decimal:
    return sum(
        (
            item.available_amount
            for item in get_earn_positions(
                client,
                category="FlexibleSaving",
                coin=coin,
                product_id=product_id,
            )
        ),
        Decimal("0"),
    )


def query_earn_order_by_link_id(
    client: BybitV5Client,
    *,
    order_link_id: str,
    category: str = "FlexibleSaving",
    product_id: str | None = None,
) -> BybitEarnOrder | None:
    params: dict[str, Any] = {
        "category": category,
        "orderLinkId": order_link_id,
    }
    if product_id:
        params["productId"] = product_id

    payload = client.get("/v5/earn/order", params)
    rows = ((payload.get("result") or {}).get("list") or [])

    for row in rows:
        if not isinstance(row, dict):
            continue

        if str(row.get("orderLinkId") or "") != str(order_link_id):
            continue

        return BybitEarnOrder(
            category=category,
            coin=(
                str(row.get("coin"))
                if row.get("coin") not in {None, ""}
                else None
            ),
            order_type=(
                str(row.get("orderType"))
                if row.get("orderType") not in {None, ""}
                else None
            ),
            order_id=(
                str(row.get("orderId"))
                if row.get("orderId") not in {None, ""}
                else None
            ),
            order_link_id=str(row.get("orderLinkId") or ""),
            status=(
                str(row.get("status"))
                if row.get("status") not in {None, ""}
                else None
            ),
            amount=_dec(row.get("orderValue")),
            product_id=(
                str(row.get("productId"))
                if row.get("productId") not in {None, ""}
                else None
            ),
            raw=row,
        )

    return None


def place_flexible_saving_redeem_order(
    client: BybitV5Client,
    *,
    amount: Decimal,
    amount_str: str,
    product_id: str,
    order_link_id: str,
    coin: str = "USDT",
    account_type: str = "FUND",
    product_precision: int,
    available_amount: Decimal,
    target_cash_usdt: Decimal,
    needed_from_earn: Decimal,
) -> BybitEarnOrder:
    if amount <= Decimal("0"):
        raise BybitEarnError(f"Earn redeem amount must be positive: {amount}")

    amount_str = str(amount_str or "").strip()
    if not amount_str:
        raise BybitEarnError("Formatted Earn redeem amount is empty")

    if "E" in amount_str.upper():
        raise BybitEarnError(
            f"Formatted Earn redeem amount uses scientific notation: {amount_str}"
        )

    if len(str(order_link_id)) > 36:
        raise BybitEarnError(
            f"Earn redeem orderLinkId is longer than 36 chars: {len(str(order_link_id))}"
        )

    payload_summary = {
        "endpoint": "/v5/earn/place-order",
        "category": "FlexibleSaving",
        "orderType": "Redeem",
        "accountType": account_type,
        "coin": coin,
        "productId": product_id,
        "orderLinkId_len": len(str(order_link_id)),
        "amount": amount_str,
        "product_precision": int(product_precision),
        "availableAmount": str(available_amount),
        "target_cash_usdt": str(target_cash_usdt),
        "needed_from_earn": str(needed_from_earn),
    }

    log.info(
        "Bybit Earn redeem payload summary: %s",
        payload_summary,
    )

    payload = client.post(
        "/v5/earn/place-order",
        {
            "category": "FlexibleSaving",
            "orderType": "Redeem",
            "accountType": account_type,
            "amount": amount_str,
            "coin": coin,
            "productId": product_id,
            "orderLinkId": order_link_id,
        },
    )

    result = payload.get("result") or {}
    order_id = str(result.get("orderId") or "")
    returned_link_id = str(result.get("orderLinkId") or "")

    if not order_id or returned_link_id != order_link_id:
        raise BybitEarnError(
            "Unexpected Earn redeem response: "
            f"order_id={order_id}, order_link_id={returned_link_id}"
        )

    return BybitEarnOrder(
        category="FlexibleSaving",
        coin=coin,
        order_type="Redeem",
        order_id=order_id,
        order_link_id=returned_link_id,
        status=None,
        amount=amount,
        product_id=product_id,
        raw=result,
    )