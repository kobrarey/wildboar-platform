from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from typing import Any

from app.allocation.statuses import (
    ALLOCATION_LEG_STATUS_PLANNED,
    ALLOCATION_LEG_STATUS_SKIPPED_EARN_UNAVAILABLE,
)


ZERO = Decimal("0")


class EarnProductError(RuntimeError):
    pass


class EarnProductUnavailableError(EarnProductError):
    pass


@dataclass(frozen=True)
class EarnProductInfo:
    product_id: str
    coin: str
    category: str
    status: str
    min_stake_amount: Decimal
    max_stake_amount: Decimal | None
    precision: int | Decimal | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class EarnValidationResult:
    ok: bool
    status: str
    product_id: str | None
    category: str | None
    coin: str | None
    original_amount: Decimal
    rounded_amount: Decimal
    stake_amount: Decimal
    residual_amount: Decimal
    min_stake_amount: Decimal | None
    max_stake_amount: Decimal | None
    error: str | None
    warnings: list[str]


NONCRITICAL_EARN_ERROR_PATTERNS = {
    "product unavailable",
    "category unavailable",
    "earn not eligible",
    "not eligible",
    "no permission",
    "permission denied",
    "unsupported category",
    "not support",
    "not supported",
    "product category invalid",
    "category invalid",
    "invalid category",
    "not open",
    "not available",
}


AVAILABLE_STATUSES = {
    "available",
    "active",
    "enabled",
    "online",
}


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


def _normalize_category(value: Any) -> str:
    return _normalize_text(value)


def _lower(value: Any) -> str:
    return _normalize_text(value).lower()


def is_noncritical_earn_error(exc: Exception | str) -> bool:
    text = str(exc).lower()
    return any(pattern in text for pattern in NONCRITICAL_EARN_ERROR_PATTERNS)


def _result_dict(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    return result if isinstance(result, dict) else {}


def _candidate_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    result = _result_dict(payload)

    for key in ("list", "products", "rows", "data"):
        value = result.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]

    for key in ("product", "item"):
        value = result.get(key)
        if isinstance(value, dict):
            return [value]

    if result:
        if any(
            key in result
            for key in (
                "productId",
                "product_id",
                "coin",
                "category",
                "status",
                "minStakeAmount",
            )
        ):
            return [result]

    return []


def _get_value(row: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in row and row[key] not in (None, ""):
            return row[key]
    return default


def _parse_precision(value: Any) -> int | Decimal | None:
    if value is None or value == "":
        return None

    if isinstance(value, int):
        return value

    text = str(value).strip()

    if text.isdigit():
        return int(text)

    out = dec(text)
    if out > ZERO:
        return out

    return None


def _parse_product_row(row: dict[str, Any], *, fallback_coin: str, fallback_category: str) -> EarnProductInfo:
    product_id = _normalize_text(
        _get_value(
            row,
            "productId",
            "product_id",
            "id",
            "product_id_str",
        )
    )

    coin = _normalize_coin(
        _get_value(
            row,
            "coin",
            "currency",
            "token",
            default=fallback_coin,
        )
    )

    category = _normalize_category(
        _get_value(
            row,
            "category",
            "productCategory",
            "product_category",
            default=fallback_category,
        )
    )

    status = _normalize_text(
        _get_value(
            row,
            "status",
            "productStatus",
            "product_status",
            default="",
        )
    )

    min_stake_amount = dec(
        _get_value(
            row,
            "minStakeAmount",
            "min_stake_amount",
            "minAmount",
            "min_amount",
            "minPurchaseAmount",
            "min_purchase_amount",
            default="0",
        )
    )

    max_raw = _get_value(
        row,
        "maxStakeAmount",
        "max_stake_amount",
        "maxAmount",
        "max_amount",
        "maxPurchaseAmount",
        "max_purchase_amount",
        default=None,
    )
    max_stake_amount = dec(max_raw) if max_raw not in (None, "") else None

    precision = _parse_precision(
        _get_value(
            row,
            "precision",
            "amountPrecision",
            "amount_precision",
            "qtyPrecision",
            "qty_precision",
            default=None,
        )
    )

    if not product_id:
        raise EarnProductUnavailableError(
            f"Earn product row does not contain productId for coin={coin}, category={category}"
        )

    if not coin:
        raise EarnProductUnavailableError(
            f"Earn product row does not contain coin for product_id={product_id}"
        )

    if not category:
        raise EarnProductUnavailableError(
            f"Earn product row does not contain category for product_id={product_id}"
        )

    return EarnProductInfo(
        product_id=product_id,
        coin=coin,
        category=category,
        status=status,
        min_stake_amount=min_stake_amount,
        max_stake_amount=max_stake_amount,
        precision=precision,
        raw=row,
    )


def _call_earn_product_endpoint(
    client: Any,
    *,
    coin: str,
    category: str,
) -> dict[str, Any]:
    params = {
        "coin": coin,
        "category": category,
    }

    try:
        get = getattr(client, "get", None)
        if callable(get):
            return get("/v5/earn/product", params)

        public_get = getattr(client, "public_get", None)
        if callable(public_get):
            return public_get("/v5/earn/product", params)

        raise EarnProductError("Client has no get/public_get method for Earn product lookup")

    except Exception as exc:
        if is_noncritical_earn_error(exc):
            raise EarnProductUnavailableError(str(exc)) from exc
        raise


def get_earn_product_info(
    client: Any,
    *,
    coin: str,
    category: str,
) -> EarnProductInfo:
    normalized_coin = _normalize_coin(coin)
    normalized_category = _normalize_category(category)

    if not normalized_coin:
        raise EarnProductUnavailableError("coin is required for Earn product lookup")

    if not normalized_category:
        raise EarnProductUnavailableError("category is required for Earn product lookup")

    payload = _call_earn_product_endpoint(
        client,
        coin=normalized_coin,
        category=normalized_category,
    )

    ret_code = payload.get("retCode")
    if ret_code not in (None, 0):
        message = payload.get("retMsg") or payload.get("ret_msg") or payload
        error = EarnProductUnavailableError(
            f"Earn product API returned retCode={ret_code}: {message}"
        )
        if is_noncritical_earn_error(error):
            raise error
        raise EarnProductError(str(error))

    rows = _candidate_rows(payload)

    for row in rows:
        row_coin = _normalize_coin(
            _get_value(row, "coin", "currency", "token", default=normalized_coin)
        )
        row_category = _normalize_category(
            _get_value(
                row,
                "category",
                "productCategory",
                "product_category",
                default=normalized_category,
            )
        )

        if row_coin != normalized_coin:
            continue

        if row_category != normalized_category:
            continue

        return _parse_product_row(
            row,
            fallback_coin=normalized_coin,
            fallback_category=normalized_category,
        )

    raise EarnProductUnavailableError(
        f"Earn product not found: coin={normalized_coin}, category={normalized_category}"
    )


def _step_from_precision(precision: int | Decimal | None) -> Decimal:
    if precision is None:
        return Decimal("0.00000001")

    if isinstance(precision, int):
        if precision < 0:
            return Decimal("1")
        return Decimal("1").scaleb(-precision)

    precision_dec = dec(precision)
    if precision_dec <= ZERO:
        return Decimal("0.00000001")

    if precision_dec >= Decimal("1"):
        as_int = int(precision_dec)
        if Decimal(as_int) == precision_dec:
            return Decimal("1").scaleb(-as_int)

    return precision_dec


def round_stake_amount_down(amount: Decimal | str | int, precision: int | Decimal | None) -> Decimal:
    amount_dec = dec(amount)
    if amount_dec <= ZERO:
        return ZERO

    step = _step_from_precision(precision)
    if step <= ZERO:
        return amount_dec

    units = (amount_dec / step).to_integral_value(rounding=ROUND_FLOOR)
    return units * step


def validate_earn_product_for_stake(
    *,
    product: EarnProductInfo,
    amount: Decimal,
) -> EarnValidationResult:
    amount_dec = dec(amount)
    warnings: list[str] = []

    if _lower(product.status) not in AVAILABLE_STATUSES:
        return EarnValidationResult(
            ok=False,
            status=ALLOCATION_LEG_STATUS_SKIPPED_EARN_UNAVAILABLE,
            product_id=product.product_id,
            category=product.category,
            coin=product.coin,
            original_amount=amount_dec,
            rounded_amount=ZERO,
            stake_amount=ZERO,
            residual_amount=amount_dec,
            min_stake_amount=product.min_stake_amount,
            max_stake_amount=product.max_stake_amount,
            error=(
                f"Earn product is not Available: "
                f"coin={product.coin}, category={product.category}, status={product.status}"
            ),
            warnings=[],
        )

    rounded_amount = round_stake_amount_down(amount_dec, product.precision)

    if rounded_amount <= ZERO:
        return EarnValidationResult(
            ok=False,
            status=ALLOCATION_LEG_STATUS_SKIPPED_EARN_UNAVAILABLE,
            product_id=product.product_id,
            category=product.category,
            coin=product.coin,
            original_amount=amount_dec,
            rounded_amount=rounded_amount,
            stake_amount=ZERO,
            residual_amount=amount_dec,
            min_stake_amount=product.min_stake_amount,
            max_stake_amount=product.max_stake_amount,
            error=(
                f"Earn stake amount rounds to zero: "
                f"amount={amount_dec}, precision={product.precision}"
            ),
            warnings=[],
        )

    if product.min_stake_amount > ZERO and rounded_amount < product.min_stake_amount:
        return EarnValidationResult(
            ok=False,
            status=ALLOCATION_LEG_STATUS_SKIPPED_EARN_UNAVAILABLE,
            product_id=product.product_id,
            category=product.category,
            coin=product.coin,
            original_amount=amount_dec,
            rounded_amount=rounded_amount,
            stake_amount=ZERO,
            residual_amount=amount_dec,
            min_stake_amount=product.min_stake_amount,
            max_stake_amount=product.max_stake_amount,
            error=(
                f"Earn stake amount below minStakeAmount: "
                f"rounded_amount={rounded_amount}, min_stake_amount={product.min_stake_amount}"
            ),
            warnings=[],
        )

    stake_amount = rounded_amount
    residual_amount = amount_dec - stake_amount

    if product.max_stake_amount is not None and product.max_stake_amount > ZERO:
        if stake_amount > product.max_stake_amount:
            stake_amount = round_stake_amount_down(
                product.max_stake_amount,
                product.precision,
            )
            residual_amount = amount_dec - stake_amount
            warnings.append(
                f"Stake amount capped to maxStakeAmount: "
                f"stake_amount={stake_amount}, max_stake_amount={product.max_stake_amount}, "
                f"residual_amount={residual_amount}"
            )

    if stake_amount <= ZERO:
        return EarnValidationResult(
            ok=False,
            status=ALLOCATION_LEG_STATUS_SKIPPED_EARN_UNAVAILABLE,
            product_id=product.product_id,
            category=product.category,
            coin=product.coin,
            original_amount=amount_dec,
            rounded_amount=rounded_amount,
            stake_amount=ZERO,
            residual_amount=amount_dec,
            min_stake_amount=product.min_stake_amount,
            max_stake_amount=product.max_stake_amount,
            error="Earn stake amount is zero after max/min validation",
            warnings=warnings,
        )

    return EarnValidationResult(
        ok=True,
        status=ALLOCATION_LEG_STATUS_PLANNED,
        product_id=product.product_id,
        category=product.category,
        coin=product.coin,
        original_amount=amount_dec,
        rounded_amount=rounded_amount,
        stake_amount=stake_amount,
        residual_amount=residual_amount if residual_amount > ZERO else ZERO,
        min_stake_amount=product.min_stake_amount,
        max_stake_amount=product.max_stake_amount,
        error=None,
        warnings=warnings,
    )