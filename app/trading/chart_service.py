from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.models import Fund, FundChartDaily, FundChartMinute


UTC = timezone.utc

DAILY_RESOLUTIONS = ("1D", "1W", "1M", "12M")
INTRADAY_RESOLUTIONS = ("1", "5", "15", "30", "60", "240")
KNOWN_RESOLUTIONS = set(DAILY_RESOLUTIONS) | set(INTRADAY_RESOLUTIONS)

DEFAULT_PRICESCALE = 100


class ChartNotFoundError(ValueError):
    pass


class ChartResolutionError(ValueError):
    pass


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _localize(ru_value: Any, en_value: Any, lang: str, fallback: str = "") -> str:
    if lang == "en":
        return str(en_value or ru_value or fallback or "")
    return str(ru_value or en_value or fallback or "")


def _fund_short_name(fund: Fund, lang: str) -> str:
    return _localize(
        getattr(fund, "short_name_ru", None),
        getattr(fund, "short_name_en", None),
        lang,
        fallback=fund.code or "",
    )


def _fund_full_name(fund: Fund, lang: str) -> str:
    return _localize(
        getattr(fund, "full_name_ru", None),
        getattr(fund, "full_name_en", None),
        lang,
        fallback=_fund_short_name(fund, lang),
    )


def _get_active_fund_by_code(db: Session, fund_code: str) -> Fund | None:
    code = (fund_code or "").strip().lower()
    if not code:
        return None

    funds = db.query(Fund).filter(Fund.is_active == True).all()
    for fund in funds:
        if (fund.code or "").strip().lower() == code:
            return fund
    return None


def _has_rows(db: Session, model, fund_id: int) -> bool:
    row = (
        db.query(model.id)
        .filter(model.fund_id == fund_id)
        .limit(1)
        .first()
    )
    return row is not None


def _normalize_resolution(resolution: str) -> str:
    raw = (resolution or "").strip()
    if raw in INTRADAY_RESOLUTIONS:
        return raw

    upper = raw.upper()
    if upper in DAILY_RESOLUTIONS:
        return upper

    raise ChartResolutionError(f"Unsupported resolution: {resolution}")


def build_chart_config_for_fund(db: Session, fund: Fund, lang: str) -> dict:
    has_daily = _has_rows(db, FundChartDaily, fund.id)
    has_minute = _has_rows(db, FundChartMinute, fund.id)

    supported_resolutions: list[str] = []
    if has_minute:
        supported_resolutions.extend(INTRADAY_RESOLUTIONS)
    if has_daily:
        supported_resolutions.extend(DAILY_RESOLUTIONS)

    if has_minute:
        default_interval = "60"
    else:
        default_interval = "1D"

    return {
        "fund_code": fund.code,
        "ticker": f"WB:{(fund.code or '').upper()}",
        "name": _fund_short_name(fund, lang),
        "description": _fund_full_name(fund, lang),
        "pricescale": DEFAULT_PRICESCALE,
        "supported_resolutions": supported_resolutions,
        "default_interval": default_interval,
        "has_data": bool(has_daily or has_minute),
        "has_intraday": bool(has_minute),
        "timezone": "Etc/UTC",
        "bars_endpoint": f"/api/chart/bars/{fund.code}",
        "config_endpoint": f"/api/chart/config/{fund.code}",
    }


def get_chart_config_by_code(db: Session, fund_code: str, lang: str) -> dict:
    fund = _get_active_fund_by_code(db, fund_code)
    if not fund:
        raise ChartNotFoundError(f"Fund not found: {fund_code}")
    return build_chart_config_for_fund(db, fund, lang)


def _dt_from_unix_seconds(value: int | str) -> datetime:
    return datetime.fromtimestamp(int(value), tz=UTC)


def _to_unix_seconds(dt: datetime) -> int:
    return int(dt.astimezone(UTC).timestamp())


def _week_start(dt: datetime) -> datetime:
    dt = dt.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return dt - timedelta(days=dt.weekday())


def _month_start(dt: datetime) -> datetime:
    dt = dt.astimezone(UTC)
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_month_start(dt: datetime) -> datetime:
    dt = _month_start(dt)
    if dt.month == 12:
        return dt.replace(year=dt.year + 1, month=1)
    return dt.replace(month=dt.month + 1)


def _year_start(dt: datetime) -> datetime:
    dt = dt.astimezone(UTC)
    return dt.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)


def _next_year_start(dt: datetime) -> datetime:
    dt = _year_start(dt)
    return dt.replace(year=dt.year + 1)


def _floor_to_bucket(dt: datetime, bucket_seconds: int) -> datetime:
    ts = int(dt.astimezone(UTC).timestamp())
    floored = (ts // bucket_seconds) * bucket_seconds
    return datetime.fromtimestamp(floored, tz=UTC)


def _bucket_start(dt: datetime, resolution: str) -> datetime:
    if resolution == "1W":
        return _week_start(dt)
    if resolution == "1M":
        return _month_start(dt)
    if resolution == "12M":
        return _year_start(dt)

    if resolution in INTRADAY_RESOLUTIONS and resolution != "1":
        bucket_seconds = int(resolution) * 60
        return _floor_to_bucket(dt, bucket_seconds)

    return dt.astimezone(UTC)


def _expand_range(from_dt: datetime, to_dt: datetime, resolution: str) -> tuple[datetime, datetime]:
    if resolution == "1D":
        start = from_dt.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        end = to_dt.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        return start, end

    if resolution == "1W":
        start = _week_start(from_dt)
        end = _week_start(to_dt) + timedelta(days=7)
        return start, end

    if resolution == "1M":
        start = _month_start(from_dt)
        end = _next_month_start(to_dt)
        return start, end

    if resolution == "12M":
        start = _year_start(from_dt)
        end = _next_year_start(to_dt)
        return start, end

    if resolution == "1":
        return from_dt.astimezone(UTC), to_dt.astimezone(UTC)

    bucket_seconds = int(resolution) * 60
    start = _floor_to_bucket(from_dt, bucket_seconds)
    end = _floor_to_bucket(to_dt, bucket_seconds) + timedelta(seconds=bucket_seconds)
    return start, end


def _get_source_model(resolution: str):
    if resolution in DAILY_RESOLUTIONS:
        return FundChartDaily
    return FundChartMinute


def _fetch_rows(
    db: Session,
    model,
    fund_id: int,
    from_dt: datetime,
    to_dt: datetime,
) -> list:
    return (
        db.query(model)
        .filter(
            model.fund_id == fund_id,
            model.ts_utc >= from_dt,
            model.ts_utc < to_dt,
        )
        .order_by(model.ts_utc.asc())
        .all()
    )


def _row_to_dict(row) -> dict:
    return {
        "ts_utc": row.ts_utc.astimezone(UTC),
        "open": _to_decimal(row.open),
        "high": _to_decimal(row.high),
        "low": _to_decimal(row.low),
        "close": _to_decimal(row.close),
        "volume": _to_decimal(row.volume),
    }


def _aggregate_rows(rows: list[dict], resolution: str) -> list[dict]:
    if resolution in ("1D", "1"):
        return rows

    aggregated: list[dict] = []
    current: dict | None = None
    current_bucket: datetime | None = None

    for row in rows:
        bucket = _bucket_start(row["ts_utc"], resolution)

        if current is None or bucket != current_bucket:
            if current is not None:
                aggregated.append(current)

            current_bucket = bucket
            current = {
                "ts_utc": bucket,
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
            }
            continue

        if row["high"] is not None and (current["high"] is None or row["high"] > current["high"]):
            current["high"] = row["high"]

        if row["low"] is not None and (current["low"] is None or row["low"] < current["low"]):
            current["low"] = row["low"]

        current["close"] = row["close"]

        if current["volume"] is None:
            current["volume"] = row["volume"]
        elif row["volume"] is not None:
            current["volume"] = current["volume"] + row["volume"]

    if current is not None:
        aggregated.append(current)

    return aggregated


def _filter_aggregated_range(rows: list[dict], from_dt: datetime, to_dt: datetime) -> list[dict]:
    result: list[dict] = []
    for row in rows:
        ts = row["ts_utc"]
        if from_dt.astimezone(UTC) <= ts <= to_dt.astimezone(UTC):
            result.append(row)
    return result


def _bars_to_udf_payload(rows: list[dict]) -> dict:
    if not rows:
        return {
            "s": "no_data",
            "t": [],
            "o": [],
            "h": [],
            "l": [],
            "c": [],
            "v": [],
        }

    return {
        "s": "ok",
        "t": [_to_unix_seconds(row["ts_utc"]) for row in rows],
        "o": [float(row["open"]) if row["open"] is not None else None for row in rows],
        "h": [float(row["high"]) if row["high"] is not None else None for row in rows],
        "l": [float(row["low"]) if row["low"] is not None else None for row in rows],
        "c": [float(row["close"]) if row["close"] is not None else None for row in rows],
        "v": [float(row["volume"]) if row["volume"] is not None else None for row in rows],
    }


def get_chart_bars_payload(
    db: Session,
    fund_code: str,
    resolution: str,
    from_ts: int,
    to_ts: int,
) -> dict:
    fund = _get_active_fund_by_code(db, fund_code)
    if not fund:
        raise ChartNotFoundError(f"Fund not found: {fund_code}")

    norm_resolution = _normalize_resolution(resolution)

    from_dt = _dt_from_unix_seconds(from_ts)
    to_dt = _dt_from_unix_seconds(to_ts)

    if to_dt < from_dt:
        raise ChartResolutionError("`to` must be greater than or equal to `from`")

    model = _get_source_model(norm_resolution)
    query_from_dt, query_to_dt = _expand_range(from_dt, to_dt, norm_resolution)

    raw_rows = _fetch_rows(
        db=db,
        model=model,
        fund_id=fund.id,
        from_dt=query_from_dt,
        to_dt=query_to_dt,
    )

    normalized_rows = [_row_to_dict(row) for row in raw_rows]
    aggregated_rows = _aggregate_rows(normalized_rows, norm_resolution)
    filtered_rows = _filter_aggregated_range(aggregated_rows, from_dt, to_dt)

    return _bars_to_udf_payload(filtered_rows)