-- Stage 25.2 — Operation Guard action_type CHECK constraints for live allocation execution
-- Production-safe, non-destructive schema migration.
-- Scope:
--   1) Operation Guard action_type CHECK constraints
--   2) fund_allocation_legs partial unique indexes for external-order idempotency
--
-- Safety:
--   - no DROP TABLE
--   - no TRUNCATE
--   - no DELETE FROM
--   - no DROP SCHEMA
--   - no DROP DATABASE
--   - no full restore
--   - no wallet/private key changes
--   - no seed rows
--   - no existing data updates

BEGIN;

-- ============================================================
-- 1. Pre-check required tables exist.
-- ============================================================

DO $$
DECLARE
    missing_tables TEXT;
BEGIN
    SELECT string_agg(table_name, ', ' ORDER BY table_name)
    INTO missing_tables
    FROM (
        VALUES
            ('fund_operation_guard_state'),
            ('fund_operation_guard_overrides'),
            ('fund_operation_guard_events'),
            ('fund_allocation_legs')
    ) AS required(table_name)
    WHERE to_regclass('public.' || required.table_name) IS NULL;

    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 25.2 blocked. Missing required tables: %. Apply Stage 24/25 base migrations first.',
            missing_tables;
    END IF;
END
$$;


-- ============================================================
-- 2. Pre-check existing Operation Guard action_type values.
-- ============================================================

DO $$
DECLARE
    bad_state_actions TEXT;
    bad_override_actions TEXT;
    bad_event_actions TEXT;
BEGIN
    SELECT string_agg(DISTINCT action_type, ', ' ORDER BY action_type)
    INTO bad_state_actions
    FROM public.fund_operation_guard_state
    WHERE action_type IS NOT NULL
      AND action_type NOT IN (
          'bybit_universal_transfer',
          'bybit_master_withdrawal',
          'bsc_redeem_payout',
          'bsc_settlement_gas_topup',
          'bsc_positive_net_to_bybit',
          'bsc_buy_collection_gas_topup',
          'bsc_buy_collection_usdt_to_settlement',
          'bybit_negative_sale_order',
          'bybit_allocation_trade_order',
          'bybit_allocation_strategy_order',
          'bybit_allocation_earn_order',
          'bybit_allocation_transfer'
      );

    SELECT string_agg(DISTINCT action_type, ', ' ORDER BY action_type)
    INTO bad_override_actions
    FROM public.fund_operation_guard_overrides
    WHERE action_type IS NOT NULL
      AND action_type NOT IN (
          'bybit_universal_transfer',
          'bybit_master_withdrawal',
          'bsc_redeem_payout',
          'bsc_settlement_gas_topup',
          'bsc_positive_net_to_bybit',
          'bsc_buy_collection_gas_topup',
          'bsc_buy_collection_usdt_to_settlement',
          'bybit_negative_sale_order',
          'bybit_allocation_trade_order',
          'bybit_allocation_strategy_order',
          'bybit_allocation_earn_order',
          'bybit_allocation_transfer'
      );

    SELECT string_agg(DISTINCT action_type, ', ' ORDER BY action_type)
    INTO bad_event_actions
    FROM public.fund_operation_guard_events
    WHERE action_type IS NOT NULL
      AND action_type NOT IN (
          'bybit_universal_transfer',
          'bybit_master_withdrawal',
          'bsc_redeem_payout',
          'bsc_settlement_gas_topup',
          'bsc_positive_net_to_bybit',
          'bsc_buy_collection_gas_topup',
          'bsc_buy_collection_usdt_to_settlement',
          'bybit_negative_sale_order',
          'bybit_allocation_trade_order',
          'bybit_allocation_strategy_order',
          'bybit_allocation_earn_order',
          'bybit_allocation_transfer'
      );

    IF bad_state_actions IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 25.2 blocked. Unsupported action_type values in fund_operation_guard_state: %',
            bad_state_actions;
    END IF;

    IF bad_override_actions IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 25.2 blocked. Unsupported action_type values in fund_operation_guard_overrides: %',
            bad_override_actions;
    END IF;

    IF bad_event_actions IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 25.2 blocked. Unsupported action_type values in fund_operation_guard_events: %',
            bad_event_actions;
    END IF;
END
$$;


-- ============================================================
-- 3. Pre-check external-order idempotency duplicate values.
--    If duplicates exist, fail before creating unique indexes.
-- ============================================================

DO $$
DECLARE
    dup_order_link_ids TEXT;
    dup_bybit_order_ids TEXT;
    dup_strategy_ids TEXT;
    dup_earn_order_ids TEXT;
BEGIN
    SELECT string_agg(format('%s (%s rows)', order_link_id, row_count), ', ' ORDER BY order_link_id)
    INTO dup_order_link_ids
    FROM (
        SELECT order_link_id, count(*) AS row_count
        FROM public.fund_allocation_legs
        WHERE order_link_id IS NOT NULL
        GROUP BY order_link_id
        HAVING count(*) > 1
        LIMIT 20
    ) d;

    SELECT string_agg(format('%s (%s rows)', bybit_order_id, row_count), ', ' ORDER BY bybit_order_id)
    INTO dup_bybit_order_ids
    FROM (
        SELECT bybit_order_id, count(*) AS row_count
        FROM public.fund_allocation_legs
        WHERE bybit_order_id IS NOT NULL
        GROUP BY bybit_order_id
        HAVING count(*) > 1
        LIMIT 20
    ) d;

    SELECT string_agg(format('%s (%s rows)', strategy_id, row_count), ', ' ORDER BY strategy_id)
    INTO dup_strategy_ids
    FROM (
        SELECT strategy_id, count(*) AS row_count
        FROM public.fund_allocation_legs
        WHERE strategy_id IS NOT NULL
        GROUP BY strategy_id
        HAVING count(*) > 1
        LIMIT 20
    ) d;

    SELECT string_agg(format('%s (%s rows)', earn_order_id, row_count), ', ' ORDER BY earn_order_id)
    INTO dup_earn_order_ids
    FROM (
        SELECT earn_order_id, count(*) AS row_count
        FROM public.fund_allocation_legs
        WHERE earn_order_id IS NOT NULL
        GROUP BY earn_order_id
        HAVING count(*) > 1
        LIMIT 20
    ) d;

    IF dup_order_link_ids IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 25.2 blocked. Duplicate fund_allocation_legs.order_link_id values found: %',
            dup_order_link_ids;
    END IF;

    IF dup_bybit_order_ids IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 25.2 blocked. Duplicate fund_allocation_legs.bybit_order_id values found: %',
            dup_bybit_order_ids;
    END IF;

    IF dup_strategy_ids IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 25.2 blocked. Duplicate fund_allocation_legs.strategy_id values found: %',
            dup_strategy_ids;
    END IF;

    IF dup_earn_order_ids IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 25.2 blocked. Duplicate fund_allocation_legs.earn_order_id values found: %',
            dup_earn_order_ids;
    END IF;
END
$$;


-- ============================================================
-- 4. Recreate Operation Guard action_type CHECK constraints.
-- ============================================================

ALTER TABLE public.fund_operation_guard_state
DROP CONSTRAINT IF EXISTS fund_operation_guard_state_action_type_check;

ALTER TABLE public.fund_operation_guard_state
ADD CONSTRAINT fund_operation_guard_state_action_type_check
CHECK (
    action_type IN (
        'bybit_universal_transfer',
        'bybit_master_withdrawal',
        'bsc_redeem_payout',
        'bsc_settlement_gas_topup',
        'bsc_positive_net_to_bybit',
        'bsc_buy_collection_gas_topup',
        'bsc_buy_collection_usdt_to_settlement',
        'bybit_negative_sale_order',
        'bybit_allocation_trade_order',
        'bybit_allocation_strategy_order',
        'bybit_allocation_earn_order',
        'bybit_allocation_transfer'
    )
);

ALTER TABLE public.fund_operation_guard_overrides
DROP CONSTRAINT IF EXISTS fund_operation_guard_overrides_action_type_check;

ALTER TABLE public.fund_operation_guard_overrides
ADD CONSTRAINT fund_operation_guard_overrides_action_type_check
CHECK (
    action_type IN (
        'bybit_universal_transfer',
        'bybit_master_withdrawal',
        'bsc_redeem_payout',
        'bsc_settlement_gas_topup',
        'bsc_positive_net_to_bybit',
        'bsc_buy_collection_gas_topup',
        'bsc_buy_collection_usdt_to_settlement',
        'bybit_negative_sale_order',
        'bybit_allocation_trade_order',
        'bybit_allocation_strategy_order',
        'bybit_allocation_earn_order',
        'bybit_allocation_transfer'
    )
);

ALTER TABLE public.fund_operation_guard_events
DROP CONSTRAINT IF EXISTS fund_operation_guard_events_action_type_check;

ALTER TABLE public.fund_operation_guard_events
ADD CONSTRAINT fund_operation_guard_events_action_type_check
CHECK (
    action_type IN (
        'bybit_universal_transfer',
        'bybit_master_withdrawal',
        'bsc_redeem_payout',
        'bsc_settlement_gas_topup',
        'bsc_positive_net_to_bybit',
        'bsc_buy_collection_gas_topup',
        'bsc_buy_collection_usdt_to_settlement',
        'bybit_negative_sale_order',
        'bybit_allocation_trade_order',
        'bybit_allocation_strategy_order',
        'bybit_allocation_earn_order',
        'bybit_allocation_transfer'
    )
);


-- ============================================================
-- 5. Idempotency unique indexes for external allocation orders.
-- ============================================================

CREATE UNIQUE INDEX IF NOT EXISTS fund_allocation_legs_order_link_id_uq
ON public.fund_allocation_legs (order_link_id)
WHERE order_link_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS fund_allocation_legs_bybit_order_id_uq
ON public.fund_allocation_legs (bybit_order_id)
WHERE bybit_order_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS fund_allocation_legs_strategy_id_uq
ON public.fund_allocation_legs (strategy_id)
WHERE strategy_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS fund_allocation_legs_earn_order_id_uq
ON public.fund_allocation_legs (earn_order_id)
WHERE earn_order_id IS NOT NULL;


-- ============================================================
-- 6. Status CHECK audit result.
-- Backend Stage 25.2 did not report new allocation batch/leg statuses.
-- Therefore this migration intentionally does not change:
--   - fund_allocation_batches_status_check
--   - fund_allocation_legs_status_check
-- Result: NO_STATUS_CONSTRAINT_CHANGE_REQUIRED
-- ============================================================

COMMIT;


-- ============================================================
-- Verification SQL:
-- ============================================================
-- SELECT conname, pg_get_constraintdef(oid)
-- FROM pg_constraint
-- WHERE conname IN (
--   'fund_operation_guard_state_action_type_check',
--   'fund_operation_guard_overrides_action_type_check',
--   'fund_operation_guard_events_action_type_check',
--   'fund_allocation_batches_status_check',
--   'fund_allocation_legs_status_check'
-- )
-- ORDER BY conname;
--
-- SELECT indexname, indexdef
-- FROM pg_indexes
-- WHERE schemaname = 'public'
--   AND tablename = 'fund_allocation_legs'
--   AND indexname IN (
--     'fund_allocation_legs_order_link_id_uq',
--     'fund_allocation_legs_bybit_order_id_uq',
--     'fund_allocation_legs_strategy_id_uq',
--     'fund_allocation_legs_earn_order_id_uq'
--   )
-- ORDER BY indexname;
