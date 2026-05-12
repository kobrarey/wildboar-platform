from __future__ import annotations

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

from app.navcalc.collector import run_collector_forever
from app.navcalc.exceptions import NavConfigError
from app.navcalc.schemas import FundNavConfig

if sys.platform.startswith("win"):
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("workers.nav_collector")


SUPPORTED_FUNDS: dict[str, str] = {
    "btc_fund": "FUND_BTC_FUND",
    "defi_sniper": "FUND_DEFI_SNIPER",
    "wb10": "FUND_WB10",
    "wb_test": "FUND_WB_TEST",
    "wb_defi": "FUND_WB_DEFI",
    "wb_web3": "FUND_WB_WEB3",
}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_str(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def build_fund_nav_config(fund_code: str) -> FundNavConfig:
    fund_code = (fund_code or "").strip().lower()

    if fund_code not in SUPPORTED_FUNDS:
        allowed = ", ".join(sorted(SUPPORTED_FUNDS))
        raise NavConfigError(f"Unsupported fund_code='{fund_code}'. Allowed: {allowed}")

    prefix = SUPPORTED_FUNDS[fund_code]

    enabled = _env_bool(f"{prefix}_ENABLED", default=False)
    collect_nav = _env_bool(f"{prefix}_COLLECT_NAV", default=True)
    collect_breakdown = _env_bool(f"{prefix}_COLLECT_BREAKDOWN", default=False)
    bybit_testnet = _env_bool(f"{prefix}_BYBIT_TESTNET", default=False)

    api_key = _env_str(f"{prefix}_BYBIT_API_KEY")
    api_secret = _env_str(f"{prefix}_BYBIT_API_SECRET")

    if not enabled:
        return FundNavConfig(
            fund_code=fund_code,
            provider="bybit_v5",
            enabled=False,
            collect_nav=collect_nav,
            collect_breakdown=collect_breakdown,
            env_prefix=prefix,
            bybit_api_key=api_key,
            bybit_api_secret=api_secret,
            bybit_testnet=bybit_testnet,
        )

    if not collect_nav:
        raise NavConfigError(
            f"Fund '{fund_code}' is enabled, but {prefix}_COLLECT_NAV is false"
        )

    if not api_key or not api_secret:
        raise NavConfigError(
            f"Fund '{fund_code}' is enabled, but {prefix}_BYBIT_API_KEY "
            f"or {prefix}_BYBIT_API_SECRET is missing"
        )

    return FundNavConfig(
        fund_code=fund_code,
        provider="bybit_v5",
        enabled=enabled,
        collect_nav=collect_nav,
        collect_breakdown=collect_breakdown,
        env_prefix=prefix,
        bybit_api_key=api_key,
        bybit_api_secret=api_secret,
        bybit_testnet=bybit_testnet,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Wild Boar NAV collector for one fund.")
    parser.add_argument(
        "--fund-code",
        required=True,
        choices=sorted(SUPPORTED_FUNDS.keys()),
        help="Fund code to collect NAV for.",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()

    args = parse_args()
    cfg = build_fund_nav_config(args.fund_code)

    if not cfg.enabled:
        log.info(
            "NAV collector disabled for fund=%s env_prefix=%s. Exit without writing data.",
            cfg.fund_code,
            cfg.env_prefix,
        )
        return 0

    log.info(
        "Starting NAV collector worker fund=%s env_prefix=%s provider=%s testnet=%s",
        cfg.fund_code,
        cfg.env_prefix,
        cfg.provider,
        cfg.bybit_testnet,
    )

    run_collector_forever(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())