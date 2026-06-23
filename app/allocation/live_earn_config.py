from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.config import settings


ZERO = Decimal("0")


class LiveEarnConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveEarnWhitelistDecision:
    ok: bool
    reason: str | None
    diagnostics: dict[str, Any]


def _split_csv(value: Any) -> set[str]:
    text = str(value or "").strip()
    if not text:
        return set()

    return {item.strip() for item in text.split(",") if item.strip()}


def _split_csv_upper(value: Any) -> set[str]:
    return {item.upper() for item in _split_csv(value)}


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_coin(value: Any) -> str:
    return _normalize_text(value).upper()


def allocation_earn_live_enabled() -> bool:
    return bool(settings.ALLOCATION_EARN_ENABLED) and bool(settings.ALLOCATION_EARN_ALLOW_LIVE)


def residual_earn_to_cash_when_live_disabled() -> bool:
    return bool(settings.ALLOCATION_EARN_RESIDUAL_TO_CASH_WHEN_DISABLED)


def allowed_fund_codes() -> set[str]:
    return {item.lower() for item in _split_csv(settings.ALLOCATION_EARN_ALLOWED_FUND_CODES)}


def allowed_coins() -> set[str]:
    return _split_csv_upper(settings.ALLOCATION_EARN_ALLOWED_COINS)


def allowed_product_ids() -> set[str]:
    return _split_csv(settings.ALLOCATION_EARN_ALLOWED_PRODUCT_IDS)


def allowed_categories() -> set[str]:
    return _split_csv(settings.ALLOCATION_EARN_ALLOWED_CATEGORIES)


def require_live_earn_whitelisted(
    *,
    fund_code: str,
    coin: str,
    category: str,
    product_id: str,
    amount: Decimal,
) -> LiveEarnWhitelistDecision:
    fund_code_norm = _normalize_text(fund_code).lower()
    coin_norm = _normalize_coin(coin)
    category_norm = _normalize_text(category)
    product_id_norm = _normalize_text(product_id)
    amount_dec = Decimal(str(amount or "0"))

    fund_codes = allowed_fund_codes()
    coins = allowed_coins()
    categories = allowed_categories()
    product_ids = allowed_product_ids()

    diagnostics = {
        "fund_code": fund_code_norm,
        "coin": coin_norm,
        "category": category_norm,
        "product_id": product_id_norm,
        "amount": str(amount_dec),
        "allowed_fund_codes": sorted(fund_codes),
        "allowed_coins": sorted(coins),
        "allowed_categories": sorted(categories),
        "allowed_product_ids": sorted(product_ids),
        "earn_enabled": bool(settings.ALLOCATION_EARN_ENABLED),
        "earn_allow_live": bool(settings.ALLOCATION_EARN_ALLOW_LIVE),
    }

    if not bool(settings.ALLOCATION_EARN_ENABLED):
        return LiveEarnWhitelistDecision(
            ok=False,
            reason="allocation_earn_enabled_false",
            diagnostics=diagnostics,
        )

    if not bool(settings.ALLOCATION_EARN_ALLOW_LIVE):
        return LiveEarnWhitelistDecision(
            ok=False,
            reason="allocation_earn_allow_live_false",
            diagnostics=diagnostics,
        )

    if not fund_code_norm:
        return LiveEarnWhitelistDecision(
            ok=False,
            reason="fund_code_required",
            diagnostics=diagnostics,
        )

    if fund_codes and fund_code_norm not in fund_codes:
        return LiveEarnWhitelistDecision(
            ok=False,
            reason="fund_code_not_whitelisted",
            diagnostics=diagnostics,
        )

    if not coin_norm:
        return LiveEarnWhitelistDecision(
            ok=False,
            reason="coin_required",
            diagnostics=diagnostics,
        )

    if coins and coin_norm not in coins:
        return LiveEarnWhitelistDecision(
            ok=False,
            reason="coin_not_whitelisted",
            diagnostics=diagnostics,
        )

    if not category_norm:
        return LiveEarnWhitelistDecision(
            ok=False,
            reason="earn_category_required",
            diagnostics=diagnostics,
        )

    if categories and category_norm not in categories:
        return LiveEarnWhitelistDecision(
            ok=False,
            reason="earn_category_not_whitelisted",
            diagnostics=diagnostics,
        )

    if not product_id_norm:
        return LiveEarnWhitelistDecision(
            ok=False,
            reason="earn_product_id_required",
            diagnostics=diagnostics,
        )

    # Product whitelist is mandatory for real live Earn.
    if not product_ids:
        return LiveEarnWhitelistDecision(
            ok=False,
            reason="earn_product_id_whitelist_empty",
            diagnostics=diagnostics,
        )

    if product_id_norm not in product_ids:
        return LiveEarnWhitelistDecision(
            ok=False,
            reason="earn_product_id_not_whitelisted",
            diagnostics=diagnostics,
        )

    if amount_dec <= ZERO:
        return LiveEarnWhitelistDecision(
            ok=False,
            reason="earn_amount_must_be_positive",
            diagnostics=diagnostics,
        )

    return LiveEarnWhitelistDecision(
        ok=True,
        reason=None,
        diagnostics=diagnostics,
    )