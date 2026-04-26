from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class FundNavConfig:
    fund_code: str
    provider: str
    enabled: bool
    collect_nav: bool
    collect_breakdown: bool
    env_prefix: str
    bybit_api_key: str
    bybit_api_secret: str
    bybit_testnet: bool


@dataclass
class NavResult:
    fund_code: str
    snapshot_ts: datetime
    nav_usd: Decimal
    uta_equity_usd: Decimal
    funding_wallet_usd: Decimal
    earn_usd: Decimal
    sanity_check_passed: bool
    source: str = "bybit_v5"
    raw_meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class NavSample:
    fund_code: str
    sample_ts: datetime
    nav_usd: Decimal
    source: str
    sanity_check_passed: bool


@dataclass
class MinuteCandle:
    fund_code: str
    minute_ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    sample_count: int
    expected_sample_count: int
    is_complete: bool