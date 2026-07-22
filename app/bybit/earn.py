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


EARN_PLACE_ORDER_PATH = (
    "/v5/earn/place-order"
)
EARN_ORDER_HISTORY_PATH = (
    "/v5/earn/order"
)

EARN_ORDER_STATUS_SUCCESS = "Success"
EARN_ORDER_STATUS_PENDING = "Pending"
EARN_ORDER_STATUS_FAIL = "Fail"

EARN_ORDER_STATUSES = {
    EARN_ORDER_STATUS_SUCCESS,
    EARN_ORDER_STATUS_PENDING,
    EARN_ORDER_STATUS_FAIL,
}


@dataclass(frozen=True)
class BybitEarnSubmitAck:
    order_id: str
    order_link_id: str
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


def _required_text(
    value: Any,
    *,
    field_name: str,
) -> str:
    text = str(
        value or ""
    ).strip()

    if not text:
        raise BybitEarnError(
            f"{field_name} must not be empty"
        )

    return text


def _validate_order_link_id(
    value: Any,
) -> str:
    order_link_id = _required_text(
        value,
        field_name="orderLinkId",
    )

    if len(order_link_id) > 36:
        raise BybitEarnError(
            "Earn orderLinkId exceeds "
            f"36 characters: "
            f"length={len(order_link_id)}"
        )

    return order_link_id


def _strict_non_negative_decimal(
    value: Any,
    *,
    field_name: str,
) -> Decimal:
    if isinstance(value, bool):
        raise BybitEarnError(
            f"{field_name} must not be bool"
        )

    if isinstance(value, float):
        raise BybitEarnError(
            f"{field_name} must not be float"
        )

    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise BybitEarnError(
            f"{field_name} is not Decimal"
        ) from exc

    if not result.is_finite():
        raise BybitEarnError(
            f"{field_name} must be finite"
        )

    if result < Decimal("0"):
        raise BybitEarnError(
            f"{field_name} must be "
            "non-negative"
        )

    return result


def _required_result(
    payload: Any,
    *,
    endpoint: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise BybitEarnError(
            f"{endpoint} response must "
            "be a dict"
        )

    ret_code = payload.get("retCode")

    if ret_code not in {
        None,
        0,
        "0",
    }:
        raise BybitEarnError(
            f"{endpoint} request failed: "
            f"retCode={ret_code}, "
            f"retMsg={payload.get('retMsg')}"
        )

    result = payload.get("result")

    if not isinstance(result, dict):
        raise BybitEarnError(
            f"{endpoint}.result must "
            "be a dict"
        )

    return dict(result)


def _required_result_list(
    payload: Any,
    *,
    endpoint: str,
) -> list[dict[str, Any]]:
    result = _required_result(
        payload,
        endpoint=endpoint,
    )
    rows = result.get("list")

    if not isinstance(rows, list):
        raise BybitEarnError(
            f"{endpoint}.result.list "
            "must be a list"
        )

    return [
        dict(row)
        for row in rows
        if isinstance(row, dict)
    ]


def build_flexible_saving_redeem_payload(
    *,
    amount: Decimal,
    amount_str: str,
    product_id: str,
    order_link_id: str,
    coin: str = "USDT",
    account_type: str = "FUND",
) -> dict[str, Any]:
    parsed_amount = (
        _strict_non_negative_decimal(
            amount,
            field_name="amount",
        )
    )

    if parsed_amount <= Decimal("0"):
        raise BybitEarnError(
            "Earn redeem amount must "
            "be positive"
        )

    formatted_amount = _required_text(
        amount_str,
        field_name="amount_str",
    )

    if "E" in formatted_amount.upper():
        raise BybitEarnError(
            "Formatted Earn redeem amount "
            "must not use scientific notation"
        )

    parsed_formatted_amount = (
        _strict_non_negative_decimal(
            formatted_amount,
            field_name="amount_str",
        )
    )

    if parsed_formatted_amount <= Decimal("0"):
        raise BybitEarnError(
            "Formatted Earn redeem amount "
            "must be positive"
        )

    if (
        parsed_formatted_amount
        != parsed_amount
    ):
        raise BybitEarnError(
            "Formatted Earn redeem amount "
            "does not match amount"
        )

    normalized_product_id = (
        _required_text(
            product_id,
            field_name="productId",
        )
    )
    normalized_order_link_id = (
        _validate_order_link_id(
            order_link_id
        )
    )
    normalized_coin = (
        _required_text(
            coin,
            field_name="coin",
        ).upper()
    )
    normalized_account_type = (
        _required_text(
            account_type,
            field_name="accountType",
        ).upper()
    )

    if normalized_account_type not in {
        "FUND",
        "UNIFIED",
    }:
        raise BybitEarnError(
            "Earn accountType must be "
            "FUND or UNIFIED"
        )

    return {
        "category": "FlexibleSaving",
        "orderType": "Redeem",
        "accountType": (
            normalized_account_type
        ),
        "amount": formatted_amount,
        "coin": normalized_coin,
        "productId": (
            normalized_product_id
        ),
        "orderLinkId": (
            normalized_order_link_id
        ),
    }


def submit_flexible_saving_redeem_order(
    client: BybitV5Client,
    *,
    amount: Decimal,
    amount_str: str,
    product_id: str,
    order_link_id: str,
    coin: str = "USDT",
    account_type: str = "FUND",
) -> BybitEarnSubmitAck:
    request_payload = (
        build_flexible_saving_redeem_payload(
            amount=amount,
            amount_str=amount_str,
            product_id=product_id,
            order_link_id=order_link_id,
            coin=coin,
            account_type=account_type,
        )
    )

    response = client.post(
        EARN_PLACE_ORDER_PATH,
        request_payload,
    )

    result = _required_result(
        response,
        endpoint=EARN_PLACE_ORDER_PATH,
    )

    order_id = _required_text(
        result.get("orderId"),
        field_name="result.orderId",
    )
    returned_link_id = (
        _required_text(
            result.get("orderLinkId"),
            field_name=(
                "result.orderLinkId"
            ),
        )
    )

    if (
        returned_link_id
        != request_payload[
            "orderLinkId"
        ]
    ):
        raise BybitEarnError(
            "Earn submit ACK "
            "orderLinkId mismatch"
        )

    return BybitEarnSubmitAck(
        order_id=order_id,
        order_link_id=(
            returned_link_id
        ),
        raw=dict(result),
    )


def query_earn_order_by_link_id(
    client: BybitV5Client,
    *,
    order_link_id: str,
    category: str = "FlexibleSaving",
    product_id: str | None = None,
) -> BybitEarnOrder | None:
    normalized_link_id = (
        _validate_order_link_id(
            order_link_id
        )
    )
    normalized_category = (
        _required_text(
            category,
            field_name="category",
        )
    )

    if normalized_category not in {
        "FlexibleSaving",
        "OnChain",
    }:
        raise BybitEarnError(
            "Unsupported Earn category: "
            f"{normalized_category}"
        )

    normalized_product_id = (
        str(product_id).strip()
        if product_id is not None
        else None
    )

    if normalized_product_id == "":
        normalized_product_id = None

    params: dict[str, Any] = {
        "category": normalized_category,
        "orderLinkId": (
            normalized_link_id
        ),
    }

    if normalized_product_id:
        params["productId"] = (
            normalized_product_id
        )

    payload = client.get(
        EARN_ORDER_HISTORY_PATH,
        params,
    )

    rows = _required_result_list(
        payload,
        endpoint=(
            EARN_ORDER_HISTORY_PATH
        ),
    )

    matching_rows = [
        row
        for row in rows
        if str(
            row.get("orderLinkId")
            or ""
        ).strip()
        == normalized_link_id
        and (
            normalized_product_id
            is None
            or str(
                row.get("productId")
                or ""
            ).strip()
            == normalized_product_id
        )
    ]

    if not matching_rows:
        return None

    if len(matching_rows) != 1:
        raise BybitEarnError(
            "Earn order history returned "
            "multiple matching rows: "
            f"orderLinkId="
            f"{normalized_link_id}, "
            f"matches={len(matching_rows)}"
        )

    row = matching_rows[0]

    row_order_link_id = (
        _required_text(
            row.get("orderLinkId"),
            field_name=(
                "order.orderLinkId"
            ),
        )
    )

    if (
        row_order_link_id
        != normalized_link_id
    ):
        raise BybitEarnError(
            "Earn order history "
            "orderLinkId mismatch"
        )

    order_id = _required_text(
        row.get("orderId"),
        field_name="order.orderId",
    )
    status = _required_text(
        row.get("status"),
        field_name="order.status",
    )
    order_type = _required_text(
        row.get("orderType"),
        field_name="order.orderType",
    )

    if order_type not in {
        "Redeem",
        "Stake",
    }:
        raise BybitEarnError(
            "Unsupported Earn orderType: "
            f"{order_type}"
        )

    amount = (
        _strict_non_negative_decimal(
            row.get("orderValue"),
            field_name=(
                "order.orderValue"
            ),
        )
    )

    return BybitEarnOrder(
        category=normalized_category,
        coin=(
            str(row.get("coin"))
            if row.get("coin")
            not in {None, ""}
            else None
        ),
        order_type=order_type,
        order_id=order_id,
        order_link_id=(
            row_order_link_id
        ),
        status=status,
        amount=amount,
        product_id=(
            str(row.get("productId"))
            if row.get("productId")
            not in {None, ""}
            else None
        ),
        raw=dict(row),
    )

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
    request_payload = (
        build_flexible_saving_redeem_payload(
            amount=amount,
            amount_str=amount_str,
            product_id=product_id,
            order_link_id=order_link_id,
            coin=coin,
            account_type=account_type,
        )
    )

    payload_summary = {
        "endpoint": (
            EARN_PLACE_ORDER_PATH
        ),
        **request_payload,
        "orderLinkId_len": len(
            request_payload[
                "orderLinkId"
            ]
        ),
        "product_precision": int(
            product_precision
        ),
        "availableAmount": str(
            available_amount
        ),
        "target_cash_usdt": str(
            target_cash_usdt
        ),
        "needed_from_earn": str(
            needed_from_earn
        ),
    }

    log.info(
        "Bybit Earn redeem payload "
        "summary: %s",
        payload_summary,
    )

    ack = (
        submit_flexible_saving_redeem_order(
            client,
            amount=amount,
            amount_str=amount_str,
            product_id=product_id,
            order_link_id=order_link_id,
            coin=coin,
            account_type=account_type,
        )
    )

    # ACK is not confirmation. The caller
    # must query /v5/earn/order in a later
    # resumable state-machine cycle.
    return BybitEarnOrder(
        category="FlexibleSaving",
        coin=str(coin).upper(),
        order_type="Redeem",
        order_id=ack.order_id,
        order_link_id=(
            ack.order_link_id
        ),
        status=None,
        amount=amount,
        product_id=product_id,
        raw=dict(ack.raw),
    )