from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.db import SessionLocal
from app.navcalc.db_writer import (
    get_fund_by_code,
    insert_nav_sample,
    load_samples_for_minute,
    write_completed_minute,
)
from app.navcalc.exceptions import NavCalcError, NavConfigError
from app.navcalc.minute_builder import build_minute_candle, minute_floor
from app.navcalc.portfolio_nav import compute_nav
from app.navcalc.schemas import FundNavConfig, NavSample


log = logging.getLogger("navcalc.collector")


def _process_finished_minute(
    *,
    cfg: FundNavConfig,
    fund_id: int,
    minute_ts: datetime,
) -> None:
    with SessionLocal() as db:
        samples = load_samples_for_minute(db, fund_id, minute_ts)

        candle = build_minute_candle(
            fund_code=cfg.fund_code,
            minute_ts=minute_ts,
            samples=samples,
        )

        if candle is None:
            log.warning(
                "NAV sample gap detected fund=%s minute=%s",
                cfg.fund_code,
                minute_floor(minute_ts).isoformat(),
            )
            return

        if cfg.shares_outstanding is None:
            raise NavConfigError(
                f"Fund '{cfg.fund_code}' has no shares_outstanding configured"
            )

        write_completed_minute(
            db,
            fund_id=fund_id,
            nav_candle=candle,
            shares_outstanding=cfg.shares_outstanding,
        )

        log.info(
            "Closed minute fund=%s minute=%s sample_count=%s is_complete=%s "
            "nav_close=%s shares_outstanding=%s",
            candle.fund_code,
            candle.minute_ts.isoformat(),
            candle.sample_count,
            candle.is_complete,
            candle.close,
            cfg.shares_outstanding,
        )


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

    current_minute: datetime | None = None
    next_tick = time.monotonic()

    while True:
        now_mono = time.monotonic()
        if now_mono < next_tick:
            time.sleep(next_tick - now_mono)

        try:
            result = compute_nav(cfg)

            sample = NavSample(
                fund_code=result.fund_code,
                sample_ts=result.snapshot_ts,
                nav_usd=result.nav_usd,
                source=result.source,
                sanity_check_passed=result.sanity_check_passed,
            )

            sample_minute = minute_floor(sample.sample_ts)

            with SessionLocal() as db:
                insert_nav_sample(db, fund_id, sample)

            log.info(
                "Sample OK fund=%s ts=%s nav=%s",
                sample.fund_code,
                sample.sample_ts.isoformat(),
                sample.nav_usd,
            )

            if current_minute is None:
                current_minute = sample_minute

            elif sample_minute > current_minute:
                cursor = current_minute
                while cursor < sample_minute:
                    _process_finished_minute(
                        cfg=cfg,
                        fund_id=fund_id,
                        minute_ts=cursor,
                    )
                    cursor += timedelta(minutes=1)

                current_minute = sample_minute

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