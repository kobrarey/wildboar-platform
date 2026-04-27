from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.navcalc.schemas import MinuteState


def minute_floor(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)


def open_new_minute_state(
    *,
    fund_code: str,
    minute_ts: datetime,
    current_sample_nav: Decimal,
    sample_ts: datetime,
    shares_outstanding: Decimal,
    prev_close_nav: Decimal | None,
) -> MinuteState:
    minute_ts = minute_floor(minute_ts)

    if prev_close_nav is None:
        open_nav = current_sample_nav
        high_nav = current_sample_nav
        low_nav = current_sample_nav
        close_nav = current_sample_nav
    else:
        open_nav = prev_close_nav
        high_nav = max(prev_close_nav, current_sample_nav)
        low_nav = min(prev_close_nav, current_sample_nav)
        close_nav = current_sample_nav

    return MinuteState(
        fund_code=fund_code,
        minute_ts=minute_ts,
        open_nav=open_nav,
        high_nav=high_nav,
        low_nav=low_nav,
        close_nav=close_nav,
        last_sample_ts=sample_ts.astimezone(timezone.utc),
        sample_count=1,
        shares_outstanding=shares_outstanding,
    )


def update_minute_state(
    state: MinuteState,
    *,
    current_sample_nav: Decimal,
    sample_ts: datetime,
) -> MinuteState:
    state.high_nav = max(state.high_nav, current_sample_nav)
    state.low_nav = min(state.low_nav, current_sample_nav)
    state.close_nav = current_sample_nav
    state.last_sample_ts = sample_ts.astimezone(timezone.utc)
    state.sample_count += 1
    return state