from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta
from decimal import Decimal

from app.config import settings
from app.db import SessionLocal
from app.navcalc.db_writer import (
    get_fund_by_code,
    get_fund_shares_outstanding_current,
    upsert_minute_state,
)
from app.navcalc.exceptions import NavCalcError, NavConfigError
from app.navcalc.minute_builder import minute_floor, open_new_minute_state, update_minute_state
from app.navcalc.nav_guard import evaluate_and_record_nav_guard, mark_nav_guard_accepted
from app.navcalc.portfolio_nav import compute_nav
from app.navcalc.schemas import FundNavConfig, MinuteState
from app.settlement.pricing_lock import is_pricing_locked


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


def _read_current_shares_outstanding(
    *,
    fund_id: int,
    fund_code: str,
) -> Decimal:
    with SessionLocal() as db:
        shares = get_fund_shares_outstanding_current(db, fund_id=fund_id)

    if shares is None:
        raise NavConfigError(
            f"Fund '{fund_code}' has no shares_outstanding_current in DB"
        )

    if shares <= 0:
        raise NavConfigError(
            f"Fund '{fund_code}' has non-positive shares_outstanding_current in DB: {shares}"
        )

    return shares


def run_collector_forever(
    cfg: FundNavConfig,
    *,
    interval_sec: int | None = None,
) -> None:
    poll_interval = int(interval_sec or settings.NAV_POLL_INTERVAL_SEC)
    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")

    with SessionLocal() as db:
        fund = get_fund_by_code(db, cfg.fund_code)

    if fund is None:
        raise NavConfigError(f"Fund '{cfg.fund_code}' not found in local DB")

    fund_id = fund.id

    initial_shares = _read_current_shares_outstanding(
        fund_id=fund_id,
        fund_code=cfg.fund_code,
    )

    log.info(
        "Starting NAV collector fund=%s fund_id=%s interval=%ss shares_outstanding_source=db shares_outstanding=%s",
        cfg.fund_code,
        fund_id,
        poll_interval,
        initial_shares,
    )

    current_state: MinuteState | None = None
    prev_close_nav = None
    next_tick = time.monotonic()

    while True:
        now_mono = time.monotonic()
        if now_mono < next_tick:
            time.sleep(next_tick - now_mono)

        try:
            shares_outstanding = _read_current_shares_outstanding(
                fund_id=fund_id,
                fund_code=cfg.fund_code,
            )

            result = compute_nav(cfg)
            sample_ts = result.snapshot_ts
            sample_nav = result.nav_usd
            sample_minute = minute_floor(sample_ts)

            with SessionLocal() as db:
                guard_decision = evaluate_and_record_nav_guard(
                    db,
                    fund_id=fund_id,
                    fund_code=cfg.fund_code,
                    current=result,
                )

            if guard_decision.decision == "rejected":
                log.warning(
                    "NAV Guard rejected snapshot fund=%s sample_ts=%s nav=%s "
                    "nav_drop_pct=%s earn_drop_pct=%s compensation_ratio=%s reason=%s",
                    cfg.fund_code,
                    sample_ts.isoformat(),
                    sample_nav,
                    guard_decision.nav_drop_pct,
                    guard_decision.earn_drop_pct,
                    guard_decision.compensation_ratio,
                    guard_decision.reason,
                )
                next_tick += poll_interval
                continue

            if guard_decision.decision == "warning":
                log.warning(
                    "NAV Guard warning fund=%s sample_ts=%s nav=%s "
                    "nav_drop_pct=%s earn_drop_pct=%s compensation_ratio=%s reason=%s",
                    cfg.fund_code,
                    sample_ts.isoformat(),
                    sample_nav,
                    guard_decision.nav_drop_pct,
                    guard_decision.earn_drop_pct,
                    guard_decision.compensation_ratio,
                    guard_decision.reason,
                )

            with SessionLocal() as db:
                if is_pricing_locked(db, fund_id=fund_id):
                    log.info(
                        "Pricing locked; NAV/chart write skipped fund=%s fund_id=%s "
                        "sample_ts=%s sample_minute=%s nav=%s",
                        cfg.fund_code,
                        fund_id,
                        sample_ts.isoformat(),
                        sample_minute.isoformat(),
                        sample_nav,
                    )
                    next_tick += poll_interval
                    continue

            if current_state is None:
                current_state = open_new_minute_state(
                    fund_code=cfg.fund_code,
                    minute_ts=sample_minute,
                    current_sample_nav=sample_nav,
                    sample_ts=sample_ts,
                    shares_outstanding=shares_outstanding,
                    prev_close_nav=None,
                )

            elif sample_minute == current_state.minute_ts:
                current_state = update_minute_state(
                    current_state,
                    current_sample_nav=sample_nav,
                    sample_ts=sample_ts,
                )
                current_state.shares_outstanding = shares_outstanding

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
                    shares_outstanding=shares_outstanding,
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
                mark_nav_guard_accepted(
                    db,
                    fund_id=fund_id,
                    current=result,
                )

            log.info(
                "Minute upsert fund=%s minute=%s sample_ts=%s open=%s high=%s low=%s close=%s "
                "sample_count=%s shares_outstanding=%s shares_source=db",
                cfg.fund_code,
                current_state.minute_ts.isoformat(),
                sample_ts.isoformat(),
                current_state.open_nav,
                current_state.high_nav,
                current_state.low_nav,
                current_state.close_nav,
                current_state.sample_count,
                current_state.shares_outstanding,
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