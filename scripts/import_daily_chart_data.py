from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert

from app.db import SessionLocal
from app.models import Fund, FundChartDaily


UTC = timezone.utc
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "chart_daily"

FILE_TO_FUND_CODE = {
    "wb_btc.json": "btc_fund",
    "wb_defi_sniper.json": "defi_sniper",
    "wb_10.json": "wb10",
}


def _parse_ts(value: str) -> datetime:
    text = str(value).strip()

    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.strptime(text, "%Y-%m-%d")

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)

    return dt


def _to_decimal(value):
    if value is None:
        return None
    return Decimal(str(value))


def _load_json_rows(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        raise ValueError(f"{path.name}: expected top-level list")

    rows: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue

        rows.append(
            {
                "ts_utc": _parse_ts(item["time"]),
                "open": _to_decimal(item["open"]),
                "high": _to_decimal(item["high"]),
                "low": _to_decimal(item["low"]),
                "close": _to_decimal(item["close"]),
                "volume": _to_decimal(item.get("volume")),
            }
        )

    return rows


def main():
    db = SessionLocal()
    try:
        funds = db.query(Fund).all()
        fund_by_code = {str(f.code): f for f in funds}

        total_imported = 0

        for filename, fund_code in FILE_TO_FUND_CODE.items():
            path = DATA_DIR / filename
            if not path.exists():
                raise FileNotFoundError(f"Missing file: {path}")

            fund = fund_by_code.get(fund_code)
            if not fund:
                raise ValueError(f"Fund code not found in DB: {fund_code}")

            raw_rows = _load_json_rows(path)

            values = []
            for row in raw_rows:
                values.append(
                    {
                        "fund_id": fund.id,
                        "ts_utc": row["ts_utc"],
                        "open": row["open"],
                        "high": row["high"],
                        "low": row["low"],
                        "close": row["close"],
                        "volume": row["volume"],
                    }
                )

            if not values:
                print(f"{fund_code}: 0 rows")
                continue

            stmt = insert(FundChartDaily.__table__).values(values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["fund_id", "ts_utc"],
                set_={
                    "open": stmt.excluded.open,
                    "high": stmt.excluded.high,
                    "low": stmt.excluded.low,
                    "close": stmt.excluded.close,
                    "volume": stmt.excluded.volume,
                },
            )

            db.execute(stmt)
            db.commit()

            total_imported += len(values)
            print(f"{fund_code}: imported/upserted {len(values)} rows from {filename}")

        print(f"Done. Total imported/upserted rows: {total_imported}")

    finally:
        db.close()


if __name__ == "__main__":
    main()