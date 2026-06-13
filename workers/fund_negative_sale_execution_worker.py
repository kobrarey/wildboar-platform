from __future__ import annotations

import time
from pathlib import Path

from app.db import SessionLocal
from app.settlement.negative_sale_execution import (
    execute_negative_sale_plan_mock,
    load_negative_sale_execution_mock_file,
)
from app.settlement.statuses import SALE_BATCH_STATUS_SALE_PLAN_CREATED


DEFAULT_MOCK_PATH = Path("tests/fixtures/negative_sale_execution_mock_wb_test.json")


def process_one_batch(*, mock_path: Path = DEFAULT_MOCK_PATH) -> bool:
    mock_execution = load_negative_sale_execution_mock_file(mock_path)

    db = SessionLocal()
    try:
        from app.models import FundNegativeSaleBatch

        sale_batch = (
            db.query(FundNegativeSaleBatch)
            .filter(FundNegativeSaleBatch.status == SALE_BATCH_STATUS_SALE_PLAN_CREATED)
            .order_by(FundNegativeSaleBatch.id.asc())
            .with_for_update(skip_locked=True)
            .first()
        )

        if sale_batch is None:
            db.rollback()
            return False

        result = execute_negative_sale_plan_mock(
            db,
            sale_batch_id=int(sale_batch.id),
            mock_execution=mock_execution,
        )

        db.commit()

        print(
            "fund_negative_sale_execution_worker:",
            "sale_batch_id=",
            result.sale_batch_id,
            "status_after=",
            result.status_after,
            "settlement_status_after=",
            result.settlement_status_after,
            "final_shortage_usdt=",
            result.final_shortage_usdt,
        )

        return True

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def run_forever(*, sleep_seconds: int = 10) -> None:
    while True:
        processed = process_one_batch()

        if not processed:
            time.sleep(sleep_seconds)


if __name__ == "__main__":
    run_forever()