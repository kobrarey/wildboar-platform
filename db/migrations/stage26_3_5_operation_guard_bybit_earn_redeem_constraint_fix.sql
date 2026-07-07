-- Stage 26.3.5A-R1
-- Operation Guard action_type CHECK constraint fix
--
-- Goal:
-- Allow new Operation Guard action_type:
--   bybit_earn_redeem
--
-- Scope:
--   - public.fund_operation_guard_state
--   - public.fund_operation_guard_events
--   - public.fund_operation_guard_overrides
--
-- Safety:
--   - schema-only
--   - no business rows modified
--   - no DELETE FROM
--   - no TRUNCATE
--   - no DROP TABLE
--   - no secrets touched or printed
--   - no WALLET_ENC_KEY changes
--   - no BYBIT_API_ENC_KEY changes
--   - no pricing unlock
--   - no Operation Guard live window created

BEGIN;

-- ============================================================
-- 1. Pre-check required Operation Guard tables.
-- ============================================================

DO $$
DECLARE
    missing_tables text;
BEGIN
    SELECT string_agg(table_name, ', ' ORDER BY table_name)
    INTO missing_tables
    FROM (
        VALUES
            ('fund_operation_guard_state'),
            ('fund_operation_guard_events'),
            ('fund_operation_guard_overrides')
    ) AS required(table_name)
    WHERE to_regclass('public.' || required.table_name) IS NULL;

    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 26.3.5A-R1 blocked. Missing Operation Guard tables: %',
            missing_tables;
    END IF;
END
$$;


-- ============================================================
-- 2. Existing data pre-check.
--    This does not modify rows.
-- ============================================================

DO $$
DECLARE
    bad_state_actions text;
    bad_event_actions text;
    bad_override_actions text;
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
          'bybit_earn_redeem',
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
          'bybit_earn_redeem',
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
          'bybit_earn_redeem',
          'bybit_allocation_trade_order',
          'bybit_allocation_strategy_order',
          'bybit_allocation_earn_order',
          'bybit_allocation_transfer'
      );

    IF bad_state_actions IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 26.3.5A-R1 blocked. Unsupported action_type values in fund_operation_guard_state: %',
            bad_state_actions;
    END IF;

    IF bad_event_actions IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 26.3.5A-R1 blocked. Unsupported action_type values in fund_operation_guard_events: %',
            bad_event_actions;
    END IF;

    IF bad_override_actions IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 26.3.5A-R1 blocked. Unsupported action_type values in fund_operation_guard_overrides: %',
            bad_override_actions;
    END IF;
END
$$;


-- ============================================================
-- 3. Recreate Operation Guard action_type CHECK constraints.
-- ============================================================

ALTER TABLE public.fund_operation_guard_state
DROP CONSTRAINT IF EXISTS fund_operation_guard_state_action_type_check;

ALTER TABLE public.fund_operation_guard_events
DROP CONSTRAINT IF EXISTS fund_operation_guard_events_action_type_check;

ALTER TABLE public.fund_operation_guard_overrides
DROP CONSTRAINT IF EXISTS fund_operation_guard_overrides_action_type_check;


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
        'bybit_earn_redeem',
        'bybit_allocation_trade_order',
        'bybit_allocation_strategy_order',
        'bybit_allocation_earn_order',
        'bybit_allocation_transfer'
    )
);

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
        'bybit_earn_redeem',
        'bybit_allocation_trade_order',
        'bybit_allocation_strategy_order',
        'bybit_allocation_earn_order',
        'bybit_allocation_transfer'
    )
);

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
        'bybit_earn_redeem',
        'bybit_allocation_trade_order',
        'bybit_allocation_strategy_order',
        'bybit_allocation_earn_order',
        'bybit_allocation_transfer'
    )
);

COMMIT;


-- ============================================================
-- Verification SQL
-- ============================================================

-- SELECT
--     conrelid::regclass AS table_name,
--     conname,
--     pg_get_constraintdef(oid) AS constraint_def,
--     CASE
--         WHEN pg_get_constraintdef(oid) LIKE '%bybit_earn_redeem%'
--         THEN 'OK_BYBIT_EARN_REDEEM_ALLOWED'
--         ELSE 'MISSING_BYBIT_EARN_REDEEM'
--     END AS bybit_earn_redeem_check
-- FROM pg_constraint
-- WHERE conrelid IN (
--     'public.fund_operation_guard_state'::regclass,
--     'public.fund_operation_guard_events'::regclass,
--     'public.fund_operation_guard_overrides'::regclass
-- )
-- AND contype = 'c'
-- AND conname IN (
--     'fund_operation_guard_state_action_type_check',
--     'fund_operation_guard_events_action_type_check',
--     'fund_operation_guard_overrides_action_type_check'
-- )
-- ORDER BY conrelid::regclass::text, conname;