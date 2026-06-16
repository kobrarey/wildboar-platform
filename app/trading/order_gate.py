from __future__ import annotations

from app.config import settings

ORDER_ENTRY_DISABLED_ERROR_KEY = "order_entry_disabled"
ORDER_ENTRY_DISABLED_MODE_REJECT = "reject"


def get_order_entry_enabled_fund_codes() -> set[str]:
    raw = str(settings.ORDER_ENTRY_ENABLED_FUND_CODES or "")
    return {
        item.strip().lower()
        for item in raw.replace(";", ",").split(",")
        if item.strip()
    }


def get_order_entry_disabled_mode() -> str:
    mode = str(settings.ORDER_ENTRY_DISABLED_MODE or ORDER_ENTRY_DISABLED_MODE_REJECT)
    mode = mode.strip().lower()
    return mode or ORDER_ENTRY_DISABLED_MODE_REJECT


def is_order_entry_enabled_for_fund_code(fund_code: str | None) -> bool:
    code = str(fund_code or "").strip().lower()
    if not code:
        return False

    return code in get_order_entry_enabled_fund_codes()