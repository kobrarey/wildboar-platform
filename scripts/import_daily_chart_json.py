from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import SessionLocal
from app.models import Fund, FundChartDaily

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("scripts.import_daily_chart_json")


SUPPORTED_IMPORT_FUNDS = {"btc_fund", "defi_sniper", "wb10"}


def _parse_decimal(value: Any, field_name: str, row_idx: int) -> Decimal:
    if value is None or value == "":
        raise ValueError(f"Row {row_idx}: missing {field_name}")

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Row {row_idx}: invalid decimal {field_name}={value!r}") from exc


def _parse_time(value: Any, row_idx: int) -> datetime:
    if value is None or value == "":
        raise ValueError(f"Row {row_idx}: missing time")

    if isinstance(value, (int, float)):
        raw = float(value)
        # JS/TradingView timestamps are often milliseconds.
        if raw > 10_000_000_000:
            raw = raw / 1000
        return datetime.fromtimestamp(raw, tz=timezone.utc).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

    s = str(value).strip()

    if s.isdigit():
        raw = int(s)
        if raw > 10_000_000_000:
            raw = raw / 1000
        return datetime.fromtimestamp(raw, tz=timezone.utc).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

    if s.endswith("Z"):
        s = s[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(s)
    except ValueError as exc:
        raise ValueError(f"Row {row_idx}: invalid time={value!r}") from exc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(timezone.utc).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )


def _load_json_array(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"JSON file not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(data, list):
        raise ValueError("JSON root must be an array")

    out: list[dict[str, Any]] = []
    for idx, row in enumerate(data, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"Row {idx}: expected object, got {type(row).__name__}")
        out.append(row)

    return out


def _get_fund_id(fund_code: str) -> int:
    with SessionLocal() as db:
        fund = db.query(Fund).filter(Fund.code == fund_code).first()
        if fund is None:
            raise RuntimeError(f"Fund not found in DB: {fund_code}")
        return int(fund.id)


def import_daily_chart_json(*, fund_code: str, file_path: Path) -> dict[str, Any]:
    fund_code = (fund_code or "").strip().lower()

    if fund_code not in SUPPORTED_IMPORT_FUNDS:
        allowed = ", ".join(sorted(SUPPORTED_IMPORT_FUNDS))
        raise ValueError(
            f"Unsupported import fund_code='{fund_code}'. "
            f"Allowed for Stage 18.4 daily import: {allowed}"
        )

    fund_id = _get_fund_id(fund_code)
    rows = _load_json_array(file_path)

    parsed: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, start=1):
        ts_utc = _parse_time(row.get("time"), idx)
        parsed.append(
            {
                "fund_id": fund_id,
                "ts_utc": ts_utc,
                "open": _parse_decimal(row.get("open"), "open", idx),
                "high": _parse_decimal(row.get("high"), "high", idx),
                "low": _parse_decimal(row.get("low"), "low", idx),
                "close": _parse_decimal(row.get("close"), "close", idx),
                "volume": None,
            }
        )

    rows_written = 0

    with SessionLocal() as db:
        for item in parsed:
            stmt = (
                pg_insert(FundChartDaily.__table__)
                .values(**item)
                .on_conflict_do_update(
                    index_elements=["fund_id", "ts_utc"],
                    set_={
                        "open": item["open"],
                        "high": item["high"],
                        "low": item["low"],
                        "close": item["close"],
                        "volume": None,
                    },
                )
            )
            db.execute(stmt)
            rows_written += 1

        db.commit()

    dates = [item["ts_utc"] for item in parsed]
    min_date = min(dates).date().isoformat() if dates else None
    max_date = max(dates).date().isoformat() if dates else None

    return {
        "fund_code": fund_code,
        "rows_read": len(rows),
        "rows_inserted_or_updated": rows_written,
        "min_date": min_date,
        "max_date": max_date,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import daily fund chart candles from JSON.")
    parser.add_argument(
        "--fund-code",
        required=True,
        choices=sorted(SUPPORTED_IMPORT_FUNDS),
        help="Fund code to import daily candles for.",
    )
    parser.add_argument(
        "--file",
        required=True,
        help="Path to JSON file with array of {time, open, high, low, close}.",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()

    args = parse_args()
    summary = import_daily_chart_json(
        fund_code=args.fund_code,
        file_path=Path(args.file),
    )

    print("Daily chart import summary:")
    print(f"fund_code={summary['fund_code']}")
    print(f"rows_read={summary['rows_read']}")
    print(f"rows_inserted_or_updated={summary['rows_inserted_or_updated']}")
    print(f"min_date={summary['min_date']}")
    print(f"max_date={summary['max_date']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())