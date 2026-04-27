from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta

from app.config import settings
from app.db import SessionLocal
from app.navcalc.db_writer import get_fund_by_code, upsert_minute_state
from app.navcalc.exceptions import NavCalcError, NavConfigError
from app.navcalc.minute_builder import minute_floor, open_new_minute_state, update_minute_state
from app.navcalc.portfolio_nav import compute_nav
from app.navcalc.schemas import FundNavConfig, MinuteState


log = logging.getLogger("navcalc.collector")


def _log_gap_minutes(
    *,
    fund_code: str,
    from_minute: datetime,
    to_minute: datetime,
) -> None:
    cursor = minute_floor(from_minute) + timedelta(minutes=1)
    target = minute_floor(to_minute)

    while cursor < target:
        log.warning(
            "NAV sample gap detected fund=%s minute=%s",
            fund_code,
            cursor.isoformat(),
        )
        cursor += timedelta(minutes=1)


def run_collector_forever(
    cfg: FundNavConfig,
    *,
    interval_sec: int | None = None,
) -> None:
    poll_interval = int(interval_sec or settings.NAV_POLL_INTERVAL_SEC)
    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")

    if cfg.shares_outstanding is None:
        raise NavConfigError(
            f"Fund '{cfg.fund_code}' is enabled but SHARES_OUTSTANDING is missing"
        )

    if cfg.shares_outstanding <= 0:
        raise NavConfigError(
            f"Fund '{cfg.fund_code}' has non-positive SHARES_OUTSTANDING: {cfg.shares_outstanding}"
        )

    with SessionLocal() as db:
        fund = get_fund_by_code(db, cfg.fund_code)

    if fund is None:
        raise NavConfigError(f"Fund '{cfg.fund_code}' not found in local DB")

    fund_id = fund.id

    log.info(
        "Starting NAV collector fund=%s fund_id=%s interval=%ss shares_outstanding=%s",
        cfg.fund_code,
        fund_id,
        poll_interval,
        cfg.shares_outstanding,
    )

    current_state: MinuteState | None = None
    prev_close_nav = None
    next_tick = time.monotonic()

    while True:
        now_mono = time.monotonic()
        if now_mono < next_tick:
            time.sleep(next_tick - now_mono)

        try:
            result = compute_nav(cfg)
            sample_ts = result.snapshot_ts
            sample_nav = result.nav_usd
            sample_minute = minute_floor(sample_ts)

            if current_state is None:
                current_state = open_new_minute_state(
                    fund_code=cfg.fund_code,
                    minute_ts=sample_minute,
                    current_sample_nav=sample_nav,
                    sample_ts=sample_ts,
                    shares_outstanding=cfg.shares_outstanding,
                    prev_close_nav=None,
                )

            elif sample_minute == current_state.minute_ts:
                current_state = update_minute_state(
                    current_state,
                    current_sample_nav=sample_nav,
                    sample_ts=sample_ts,
                )

            elif sample_minute > current_state.minute_ts:
                prev_close_nav = current_state.close_nav

                _log_gap_minutes(
                    fund_code=cfg.fund_code,
                    from_minute=current_state.minute_ts,
                    to_minute=sample_minute,
                )

                current_state = open_new_minute_state(
                    fund_code=cfg.fund_code,
                    minute_ts=sample_minute,
                    current_sample_nav=sample_nav,
                    sample_ts=sample_ts,
                    shares_outstanding=cfg.shares_outstanding,
                    prev_close_nav=prev_close_nav,
                )

            else:
                log.warning(
                    "Out-of-order NAV sample ignored fund=%s sample_ts=%s current_minute=%s",
                    cfg.fund_code,
                    sample_ts.isoformat(),
                    current_state.minute_ts.isoformat() if current_state else None,
                )
                next_tick += poll_interval
                continue

            with SessionLocal() as db:
                upsert_minute_state(
                    db,
                    fund_id=fund_id,
                    state=current_state,
                )

            log.info(
                "Minute upsert fund=%s minute=%s sample_ts=%s open=%s high=%s low=%s close=%s sample_count=%s",
                cfg.fund_code,
                current_state.minute_ts.isoformat(),
                sample_ts.isoformat(),
                current_state.open_nav,
                current_state.high_nav,
                current_state.low_nav,
                current_state.close_nav,
                current_state.sample_count,
            )

        except NavCalcError as exc:
            log.error("Sample failed fund=%s: %s", cfg.fund_code, exc)
        except Exception as exc:
            log.exception("Unexpected collector failure fund=%s: %s", cfg.fund_code, exc)

        next_tick += poll_interval

        after_run = time.monotonic()
        if after_run > next_tick:
            skipped = 0
            while after_run > next_tick:
                next_tick += poll_interval
                skipped += 1
            if skipped > 0:
                log.warning(
                    "Collector lag fund=%s: skipped %s poll slots to avoid overlap",
                    cfg.fund_code,
                    skipped,
                )