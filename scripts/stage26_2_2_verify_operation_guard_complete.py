from __future__ import annotations

from sqlalchemy import text

from app.db import SessionLocal


REQUIRED_ACTIONS = {
    "bybit_universal_transfer",
    "bybit_master_withdrawal",
    "bsc_redeem_payout",
    "bsc_settlement_gas_topup",
    "bsc_positive_net_to_bybit",
    "bsc_buy_collection_gas_topup",
    "bsc_buy_collection_usdt_to_settlement",
    "bybit_negative_sale_order",
    "bybit_allocation_trade_order",
    "bybit_allocation_strategy_order",
    "bybit_allocation_earn_order",
    "bybit_allocation_transfer",
}


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def fetch_rows(db, *, scope_key: str, fund_id: int | None):
    if fund_id is None:
        return db.execute(
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
                WHERE scope_key = :scope_key
                  AND fund_id IS NULL
                  AND action_type = ANY(:actions)
                ORDER BY action_type
                """
            ),
            {
                "scope_key": scope_key,
                "actions": sorted(REQUIRED_ACTIONS),
            },
        ).mappings().all()

    return db.execute(
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
            WHERE scope_key = :scope_key
              AND fund_id = :fund_id
              AND action_type = ANY(:actions)
            ORDER BY action_type
            """
        ),
        {
            "scope_key": scope_key,
            "fund_id": fund_id,
            "actions": sorted(REQUIRED_ACTIONS),
        },
    ).mappings().all()


def main() -> None:
    db = SessionLocal()
    try:
        global_rows = fetch_rows(db, scope_key="global", fund_id=None)
        global_actions = {str(row["action_type"]) for row in global_rows}

        assert_ok(
            "GLOBAL_REQUIRED_GUARD_ROWS_EXIST",
            global_actions == REQUIRED_ACTIONS,
        )

        for row in global_rows:
            action_type = str(row["action_type"])

            assert_ok(
                f"GLOBAL_{action_type}_SCOPE_KEY",
                row["scope_key"] == "global",
            )
            assert_ok(
                f"GLOBAL_{action_type}_SCOPE_TYPE",
                row["scope_type"] == "global",
            )
            assert_ok(
                f"GLOBAL_{action_type}_FUND_ID_NULL",
                row["fund_id"] is None,
            )
            assert_ok(
                f"GLOBAL_{action_type}_MODE_BLOCKED",
                row["mode"] == "blocked",
            )

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
        expected_fund_scope_key = f"fund:{fund_id}"

        fund_rows = fetch_rows(
            db,
            scope_key=expected_fund_scope_key,
            fund_id=fund_id,
        )
        fund_actions = {str(row["action_type"]) for row in fund_rows}

        assert_ok(
            "WB_TEST_ALL_REQUIRED_FUND_GUARD_ROWS_EXIST",
            fund_actions == REQUIRED_ACTIONS,
        )

        for row in fund_rows:
            action_type = str(row["action_type"])

            assert_ok(
                f"WB_TEST_{action_type}_SCOPE_KEY",
                row["scope_key"] == expected_fund_scope_key,
            )
            assert_ok(
                f"WB_TEST_{action_type}_SCOPE_TYPE",
                row["scope_type"] == "fund",
            )
            assert_ok(
                f"WB_TEST_{action_type}_FUND_ID",
                int(row["fund_id"]) == fund_id,
            )
            assert_ok(
                f"WB_TEST_{action_type}_MODE_BLOCKED",
                row["mode"] == "blocked",
            )

        non_wb_test_live_allowed = db.execute(
            text(
                """
                SELECT COUNT(*) AS count_rows
                FROM public.fund_operation_guard_state s
                LEFT JOIN public.funds f ON f.id = s.fund_id
                WHERE s.action_type = ANY(:actions)
                  AND s.mode = 'live_allowed'
                  AND NOT (
                      s.scope_key = :wb_test_scope_key
                      AND s.fund_id = :wb_test_fund_id
                  )
                """
            ),
            {
                "actions": sorted(REQUIRED_ACTIONS),
                "wb_test_scope_key": expected_fund_scope_key,
                "wb_test_fund_id": fund_id,
            },
        ).scalar_one()

        assert_ok(
            "NO_NON_WB_TEST_ROWS_LIVE_ALLOWED",
            int(non_wb_test_live_allowed) == 0,
        )

        earn_rows_live_allowed = db.execute(
            text(
                """
                SELECT COUNT(*) AS count_rows
                FROM public.fund_operation_guard_state
                WHERE action_type = 'bybit_allocation_earn_order'
                  AND mode = 'live_allowed'
                """
            )
        ).scalar_one()

        assert_ok(
            "BYBIT_ALLOCATION_EARN_ORDER_REMAINS_BLOCKED",
            int(earn_rows_live_allowed) == 0,
        )

        print("STAGE26_2_2_OPERATION_GUARD_COMPLETE_VERIFY_OK")

    finally:
        db.close()


if __name__ == "__main__":
    main()