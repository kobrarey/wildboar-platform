from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.allocation.idempotency import make_mock_earn_order_id


ZERO = Decimal("0")


class EarnOrderPayloadError(RuntimeError):
    pass


@dataclass(frozen=True)
class EarnStakePayload:
    payload: dict[str, Any]
    order_link_id: str
    product_id: str
    coin: str
    category: str
    amount: Decimal


@dataclass(frozen=True)
class MockEarnStakeResult:
    earn_order_id: str
    status: str
    product_id: str
    category: str
    coin: str
    staked_qty: Decimal
    staked_usdt: Decimal
    residual_qty: Decimal
    residual_usdt: Decimal
    raw: dict[str, Any]


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


def _normalize_coin(value: Any) -> str:
    return _normalize_text(value).upper()


def _decimal_str(value: Decimal | int | str | None) -> str:
    return str(dec(value))


def build_earn_stake_payload(
    *,
    category: str,
    product_id: str,
    coin: str,
    amount: Decimal,
    order_link_id: str,
    account_type: str = "UNIFIED",
) -> EarnStakePayload:
    """
    Build future Bybit Earn stake payload.

    Stage 22.4:
    - payload build only;
    - no real POST /v5/earn/place-order.
    """
    normalized_category = _normalize_text(category)
    normalized_product_id = _normalize_text(product_id)
    normalized_coin = _normalize_coin(coin)
    normalized_account_type = _normalize_text(account_type).upper()
    amount_dec = dec(amount)

    if not normalized_category:
        raise EarnOrderPayloadError("category is required")

    if not normalized_product_id:
        raise EarnOrderPayloadError("product_id is required")

    if not normalized_coin:
        raise EarnOrderPayloadError("coin is required")

    if amount_dec <= ZERO:
        raise EarnOrderPayloadError(f"amount must be positive: {amount}")

    if not order_link_id:
        raise EarnOrderPayloadError("order_link_id is required")

    payload = {
        "category": normalized_category,
        "orderType": "Stake",
        "accountType": normalized_account_type,
        "amount": _decimal_str(amount_dec),
        "coin": normalized_coin,
        "productId": normalized_product_id,

        # If Bybit Earn supports client refs/orderLinkId later, this is ready.
        # If not, we still keep it as deterministic local ref in DB/logs.
        "orderLinkId": order_link_id,
        "localRef": order_link_id,
    }

    return EarnStakePayload(
        payload=payload,
        order_link_id=order_link_id,
        product_id=normalized_product_id,
        coin=normalized_coin,
        category=normalized_category,
        amount=amount_dec,
    )


def simulate_earn_stake(
    *,
    payload: EarnStakePayload,
    stake_usdt_price: Decimal = Decimal("1"),
    requested_amount: Decimal | None = None,
    mock_fill_ratio: Decimal = Decimal("1"),
    residual_usdt_hint: Decimal = Decimal("0"),
    final_status: str = "filled",
) -> MockEarnStakeResult:
    """
    Mock Earn stake result.

    Does not call client.post().
    Used by Stage 22.4 tests and handlers.
    """
    price = dec(stake_usdt_price)
    if price <= ZERO:
        raise EarnOrderPayloadError(f"stake_usdt_price must be positive: {stake_usdt_price}")

    ratio = dec(mock_fill_ratio)
    if ratio < ZERO:
        ratio = ZERO
    if ratio > Decimal("1"):
        ratio = Decimal("1")

    requested = dec(requested_amount) if requested_amount is not None else payload.amount
    if requested <= ZERO:
        requested = payload.amount

    staked_qty = payload.amount * ratio
    residual_qty = requested - staked_qty
    if residual_qty < ZERO:
        residual_qty = ZERO

    staked_usdt = staked_qty * price
    residual_usdt = dec(residual_usdt_hint)
    if residual_usdt <= ZERO:
        residual_usdt = residual_qty * price

    return MockEarnStakeResult(
        earn_order_id=make_mock_earn_order_id(payload.order_link_id),
        status=final_status,
        product_id=payload.product_id,
        category=payload.category,
        coin=payload.coin,
        staked_qty=staked_qty,
        staked_usdt=staked_usdt,
        residual_qty=residual_qty,
        residual_usdt=residual_usdt,
        raw={
            "mode": "mock_earn_stake",
            "payload": payload.payload,
            "mock_fill_ratio": str(ratio),
            "stake_usdt_price": str(price),
        },
    )


def assert_no_real_earn_post_allowed(*args: Any, **kwargs: Any) -> None:
    """
    Guard helper for future integration points.
    Stage 22.4 must never place real Earn orders.
    """
    raise EarnOrderPayloadError(
        "Real Earn stake is blocked in Stage 22.4. Use build payload + simulate_earn_stake only."
    )