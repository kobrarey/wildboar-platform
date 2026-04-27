from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterable, Any

from app.navcalc.schemas import MinuteCandle


EXPECTED_SAMPLE_COUNT = 6


def minute_floor(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)


def _sample_ts(sample: Any) -> datetime:
    if hasattr(sample, "sample_ts"):
        return sample.sample_ts
    raise AttributeError("Sample object has no sample_ts")


def _sample_nav(sample: Any) -> Decimal:
    if hasattr(sample, "nav_usd"):
        return Decimal(sample.nav_usd)
    if hasattr(sample, "nav_usdt"):
        return Decimal(sample.nav_usdt)
    raise AttributeError("Sample object has neither nav_usd nor nav_usdt")


def build_minute_candle(
    *,
    fund_code: str,
    minute_ts: datetime,
    samples: Iterable[Any],
    expected_sample_count: int = EXPECTED_SAMPLE_COUNT,
) -> MinuteCandle | None:
    rows = sorted(list(samples), key=_sample_ts)
    if not rows:
        return None

    navs = [_sample_nav(row) for row in rows]

    return MinuteCandle(
        fund_code=fund_code,
        minute_ts=minute_floor(minute_ts),
        open=navs[0],
        high=max(navs),
        low=min(navs),
        close=navs[-1],
        sample_count=len(navs),
        expected_sample_count=expected_sample_count,
        is_complete=(len(navs) == expected_sample_count),
    )