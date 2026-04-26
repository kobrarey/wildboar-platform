from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from app.config import settings
from app.navcalc.exceptions import NavCalcError
from app.navcalc.portfolio_nav import compute_nav
from app.navcalc.schemas import FundNavConfig, MinuteCandle, NavResult, NavSample


log = logging.getLogger("navcalc.collector")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _minute_floor(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(second=0, microsecond=0)


def _json_default(value):
    if isinstance(value, Decimal):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _append_jsonl(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n")


def _nav_result_to_sample(result: NavResult) -> NavSample:
    return NavSample(
        fund_code=result.fund_code,
        sample_ts=result.snapshot_ts,
        nav_usd=result.nav_usd,
        source=result.source,
        sanity_check_passed=result.sanity_check_passed,
    )


class MinuteAccumulator:
    def __init__(self, fund_code: str, minute_ts: datetime, expected_sample_count: int = 6) -> None:
        self.fund_code = fund_code
        self.minute_ts = _minute_floor(minute_ts)
        self.expected_sample_count = expected_sample_count

        self.open: Decimal | None = None
        self.high: Decimal | None = None
        self.low: Decimal | None = None
        self.close: Decimal | None = None
        self.sample_count = 0

    def add(self, sample: NavSample) -> None:
        nav = sample.nav_usd

        if self.sample_count == 0:
            self.open = nav
            self.high = nav
            self.low = nav
            self.close = nav
            self.sample_count = 1
            return

        assert self.high is not None
        assert self.low is not None

        self.high = max(self.high, nav)
        self.low = min(self.low, nav)
        self.close = nav
        self.sample_count += 1

    def build_candle(self) -> MinuteCandle | None:
        if self.sample_count == 0:
            return None

        assert self.open is not None
        assert self.high is not None
        assert self.low is not None
        assert self.close is not None

        return MinuteCandle(
            fund_code=self.fund_code,
            minute_ts=self.minute_ts,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            sample_count=self.sample_count,
            expected_sample_count=self.expected_sample_count,
            is_complete=(self.sample_count == self.expected_sample_count),
        )


def _write_sample(sample_path: Path, sample: NavSample) -> None:
    _append_jsonl(sample_path, asdict(sample))


def _write_candle(candle_path: Path, candle: MinuteCandle) -> None:
    _append_jsonl(candle_path, asdict(candle))


def _warn_gap_minutes(prev_minute: datetime, new_minute: datetime) -> None:
    cursor = prev_minute + timedelta(minutes=1)
    while cursor < new_minute:
        log.warning("NAV sample gap detected: no successful samples for minute %s", cursor.isoformat())
        cursor += timedelta(minutes=1)


def run_collector_forever(
    cfg: FundNavConfig,
    *,
    interval_sec: int | None = None,
    data_dir: str | Path = "data/nav_samples",
) -> None:
    poll_interval = int(interval_sec or settings.NAV_POLL_INTERVAL_SEC)
    if poll_interval <= 0:
        raise ValueError("poll_interval must be positive")

    data_dir = Path(data_dir)
    sample_path = data_dir / f"{cfg.fund_code}_samples.jsonl"
    candle_path = data_dir / f"{cfg.fund_code}_ohlc_1m.jsonl"

    log.info(
        "Starting NAV collector fund=%s interval=%ss sample_path=%s candle_path=%s",
        cfg.fund_code,
        poll_interval,
        sample_path,
        candle_path,
    )

    current_acc: MinuteAccumulator | None = None
    next_tick = time.monotonic()

    while True:
        now_mono = time.monotonic()
        if now_mono < next_tick:
            time.sleep(next_tick - now_mono)

        started_at = time.monotonic()

        try:
            result = compute_nav(cfg)
            sample = _nav_result_to_sample(result)
            sample_minute = _minute_floor(sample.sample_ts)

            _write_sample(sample_path, sample)

            if current_acc is None:
                current_acc = MinuteAccumulator(cfg.fund_code, sample_minute)

            elif sample_minute > current_acc.minute_ts:
                candle = current_acc.build_candle()
                if candle is not None:
                    _write_candle(candle_path, candle)
                    log.info(
                        "Closed candle fund=%s minute=%s o=%s h=%s l=%s c=%s sample_count=%s is_complete=%s",
                        candle.fund_code,
                        candle.minute_ts.isoformat(),
                        candle.open,
                        candle.high,
                        candle.low,
                        candle.close,
                        candle.sample_count,
                        candle.is_complete,
                    )

                _warn_gap_minutes(current_acc.minute_ts, sample_minute)
                current_acc = MinuteAccumulator(cfg.fund_code, sample_minute)

            current_acc.add(sample)

            log.info(
                "Sample OK fund=%s ts=%s nav=%s",
                sample.fund_code,
                sample.sample_ts.isoformat(),
                sample.nav_usd,
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