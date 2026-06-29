from __future__ import annotations

from sqlalchemy import text

from app.db import SessionLocal


REQUIRED_ACTIONS = {
    "bybit_negative_sale_order",
    "bybit_allocation_strategy_order",
}

EXPECTED_REASON = "Stage 26.2.1 seed missing wb_test Operation Guard state"


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def main() -> None:
    db = SessionLocal()
    try:
        fund = db.execute(
            text(
                """
                SELECT id, code
                FROM public.funds
                WHERE code = :code
                """
            ),
            {"code": "wb_test"},
        ).mappings().first()

        assert_ok("WB_TEST_FUND_EXISTS", fund is not None)

        fund_id = int(fund["id"])
        expected_scope_key = f"fund:{fund_id}"

        rows = db.execute(
            text(
                """
                SELECT
                    id,
                    scope_key,
                    scope_type,
                    fund_id,
                    action_type,
                    mode,
                    reason
                FROM public.fund_operation_guard_state
                WHERE fund_id = :fund_id
                  AND action_type = ANY(:actions)
                ORDER BY action_type
                """
            ),
            {
                "fund_id": fund_id,
                "actions": sorted(REQUIRED_ACTIONS),
            },
        ).mappings().all()

        found_actions = {str(row["action_type"]) for row in rows}

        assert_ok(
            "WB_TEST_REQUIRED_GUARD_ROWS_EXIST",
            found_actions == REQUIRED_ACTIONS,
        )

        for row in rows:
            action_type = str(row["action_type"])

            assert_ok(
                f"{action_type}_SCOPE_KEY",
                row["scope_key"] == expected_scope_key,
            )
            assert_ok(
                f"{action_type}_SCOPE_TYPE_FUND",
                row["scope_type"] == "fund",
            )
            assert_ok(
                f"{action_type}_FUND_ID",
                int(row["fund_id"]) == fund_id,
            )
            assert_ok(
                f"{action_type}_MODE_BLOCKED",
                row["mode"] == "blocked",
            )
            assert_ok(
                f"{action_type}_REASON",
                row["reason"] == EXPECTED_REASON,
            )

        non_wb_test_rows = db.execute(
            text(
                """
                SELECT COUNT(*) AS count_rows
                FROM public.fund_operation_guard_state s
                LEFT JOIN public.funds f ON f.id = s.fund_id
                WHERE s.action_type = ANY(:actions)
                  AND COALESCE(f.code, '') <> 'wb_test'
                """
            ),
            {"actions": sorted(REQUIRED_ACTIONS)},
        ).scalar_one()

        print(f"NON_WB_TEST_ROWS_FOR_SAME_ACTIONS_SEEN={int(non_wb_test_rows)}")
        print("STAGE26_2_1_OPERATION_GUARD_ROWS_VERIFY_OK")

    finally:
        db.close()


if __name__ == "__main__":
    main()