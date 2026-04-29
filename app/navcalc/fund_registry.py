from __future__ import annotations

import os

from app.navcalc.exceptions import FundDisabledError, NavConfigError
from app.navcalc.schemas import FundNavConfig


FUND_CODES = (
    "btc_fund",
    "defi_sniper",
    "wb10",
    "wb_test",
    "wb_defi",
    "wb_web3",
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _prefix(fund_code: str) -> str:
    return f"FUND_{fund_code.upper()}"


def build_fund_config(fund_code: str) -> FundNavConfig:
    code = (fund_code or "").strip().lower()
    if code not in FUND_CODES:
        raise NavConfigError(f"Unknown fund_code: {fund_code}")

    prefix = _prefix(code)
    default_enabled = code not in {"wb_defi", "wb_web3"}

    enabled = _env_bool(f"{prefix}_ENABLED", default_enabled)
    collect_nav = _env_bool(f"{prefix}_COLLECT_NAV", True)
    collect_breakdown = _env_bool(f"{prefix}_COLLECT_BREAKDOWN", False)

    api_key = (os.getenv(f"{prefix}_BYBIT_API_KEY") or "").strip()
    api_secret = (os.getenv(f"{prefix}_BYBIT_API_SECRET") or "").strip()
    testnet = _env_bool(f"{prefix}_BYBIT_TESTNET", False)

    return FundNavConfig(
        fund_code=code,
        provider="bybit",
        enabled=enabled,
        collect_nav=collect_nav,
        collect_breakdown=collect_breakdown,
        env_prefix=prefix,
        bybit_api_key=api_key,
        bybit_api_secret=api_secret,
        bybit_testnet=testnet,
    )


def get_all_funds() -> list[FundNavConfig]:
    return [build_fund_config(code) for code in FUND_CODES]


def get_enabled_funds() -> list[FundNavConfig]:
    return [cfg for cfg in get_all_funds() if cfg.enabled]


def require_runnable_fund(fund_code: str) -> FundNavConfig:
    cfg = build_fund_config(fund_code)

    if not cfg.enabled:
        raise FundDisabledError(f"Fund '{cfg.fund_code}' is disabled by config")

    if not cfg.bybit_api_key or not cfg.bybit_api_secret:
        raise NavConfigError(
            f"Fund '{cfg.fund_code}' is enabled but Bybit API credentials are missing"
        )

    return cfg