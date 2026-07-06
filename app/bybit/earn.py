from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.bybit.client import BybitV5Client


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


def resolve_flexible_saving_product_id(
    client: BybitV5Client,
    *,
    coin: str = "USDT",
) -> str:
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

    return available[0].product_id


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
    product_id: str,
    order_link_id: str,
    coin: str = "USDT",
    account_type: str = "FUND",
) -> BybitEarnOrder:
    if amount <= Decimal("0"):
        raise BybitEarnError(f"Earn redeem amount must be positive: {amount}")

    payload = client.post(
        "/v5/earn/place-order",
        {
            "category": "FlexibleSaving",
            "orderType": "Redeem",
            "accountType": account_type,
            "amount": str(amount),
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