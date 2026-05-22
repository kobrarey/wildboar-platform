from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

from app.models import Fund, FundOrder
from app.portfolio import FUND_ICON_MAP


DASH = "—"


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _format_decimal(value: Any, places: str) -> str | None:
    dec = _to_decimal(value)
    if dec is None:
        return None
    return str(dec.quantize(Decimal(places), rounding=ROUND_DOWN))


def _dt_str(value: datetime | None) -> str:
    if not value:
        return ""
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _fund_name(fund: Fund, lang: str) -> str:
    if lang == "en":
        return str(
            getattr(fund, "short_name_en", None)
            or getattr(fund, "name_en", None)
            or getattr(fund, "short_name_ru", None)
            or getattr(fund, "name_ru", None)
            or getattr(fund, "code", "")
            or ""
        )

    return str(
        getattr(fund, "short_name_ru", None)
        or getattr(fund, "name_ru", None)
        or getattr(fund, "short_name_en", None)
        or getattr(fund, "name_en", None)
        or getattr(fund, "code", "")
        or ""
    )


def _fund_icon_name(fund: Fund) -> str:
    db_icon = (getattr(fund, "icon_name", None) or "").strip()
    if db_icon:
        return db_icon

    fund_code = (getattr(fund, "code", None) or "").strip().lower()
    return FUND_ICON_MAP.get(fund_code, "fund-default.svg")


def _side_label(side: str | None, lang: str) -> str:
    raw = (side or "").strip().lower()

    if raw == "redeem":
        return "Redeem" if lang == "en" else "Погашение"

    return "Buy" if lang == "en" else "Покупка"


def _status_meta(status: str | None, lang: str) -> tuple[str, str, str]:
    raw = (status or "").strip().lower()

    processing_statuses = {
        "pending",
        "processing",
        "settling",
        "buy_collecting",
        "buy_collected",
        "awaiting_positive_net_execution",
        "awaiting_negative_net_execution",
    }

    failed_statuses = {
        "failed",
        "failed_requires_review",
    }

    success_statuses = {
        "success",
    }

    cancelled_statuses = {
        "cancelled",
        "canceled",
    }

    if raw in processing_statuses:
        return (
            "Processing" if lang == "en" else "Обрабатывается",
            "orange",
            "tx-status--pending",
        )

    if raw in success_statuses:
        return (
            "Completed" if lang == "en" else "Выполнено",
            "green",
            "tx-status--success",
        )

    if raw in failed_statuses:
        return (
            "Failed" if lang == "en" else "Ошибка",
            "red",
            "tx-status--failed",
        )

    if raw in cancelled_statuses:
        return (
            "Cancelled" if lang == "en" else "Отменено",
            "gray",
            "tx-status--cancelled",
        )

    return (
        "Processing" if lang == "en" else "Обрабатывается",
        "orange",
        "tx-status--pending",
    )


def _amount_display(order: FundOrder, lang: str) -> str:
    amount = _format_decimal(order.amount_usdt, "0.00")
    if amount is None:
        return DASH
    return f"{amount} USDT"


def _shares_display(order: FundOrder, lang: str) -> str:
    shares = _format_decimal(order.shares, "0.0000")
    if shares is None:
        return DASH

    suffix = "shares" if lang == "en" else "паёв"
    return f"{shares} {suffix}"


def _price_display(order: FundOrder, lang: str) -> str:
    price = _format_decimal(order.price_usdt, "0.00")
    if price is None:
        return DASH
    return f"{price} USDT"


def format_trading_history_row(order: FundOrder, fund: Fund, lang: str) -> dict:
    status_label, status_color, status_class = _status_meta(order.status, lang)

    amount_usdt = _format_decimal(order.amount_usdt, "0.00")
    shares = _format_decimal(order.shares, "0.0000")
    price_usdt = _format_decimal(order.price_usdt, "0.00")

    fund_name = _fund_name(fund, lang)
    side_label = _side_label(order.side, lang)
    created_at = _dt_str(order.created_at)
    executed_at = _dt_str(order.executed_at)

    return {
        "id": order.id,
        "fund_id": order.fund_id,
        "fund_code": fund.code,
        "fund_name": fund_name,
        "icon_name": _fund_icon_name(fund),

        # Canonical final fields
        "name": fund_name,
        "side": order.side,
        "side_label": side_label,
        "amount": _amount_display(order, lang),
        "shares_display": _shares_display(order, lang),
        "price": _price_display(order, lang),
        "status": order.status,
        "status_label": status_label,
        "status_color": status_color,
        "status_class": status_class,
        "created": created_at,
        "executed": executed_at or DASH,

        # Machine / existing frontend fields
        "amount_usdt": amount_usdt,
        "shares": shares,
        "price_usdt": price_usdt,
        "created_at": created_at,
        "executed_at": executed_at,

        # Backward-compatible terminal aliases
        "direction": side_label,
    }


def format_trading_history_rows(rows: list[tuple[FundOrder, Fund]], lang: str) -> list[dict]:
    return [format_trading_history_row(order, fund, lang) for order, fund in rows]