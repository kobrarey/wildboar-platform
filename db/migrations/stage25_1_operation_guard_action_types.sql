-- Stage 25.1 — Operation Guard action_type CHECK constraints hotfix
-- Production-safe, non-destructive constraint migration.
-- Scope: fund_operation_guard_state / fund_operation_guard_overrides / fund_operation_guard_events
-- Does not create seed rows. Does not touch existing data.

BEGIN;

-- 1. Verify guard tables exist and existing action_type values are compatible.
DO $$
DECLARE
    missing_tables TEXT;
    bad_state_actions TEXT;
    bad_override_actions TEXT;
    bad_event_actions TEXT;
BEGIN
    SELECT string_agg(table_name, ', ' ORDER BY table_name)
    INTO missing_tables
    FROM (
        VALUES
            ('fund_operation_guard_state'),
            ('fund_operation_guard_overrides'),
            ('fund_operation_guard_events')
    ) AS required(table_name)
    WHERE to_regclass('public.' || required.table_name) IS NULL;

    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 25.1 blocked. Missing Operation Guard tables: %. Apply Stage 24/25 base migration first.',
            missing_tables;
    END IF;

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
          'bybit_negative_sale_order'
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
          'bybit_negative_sale_order'
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
          'bybit_negative_sale_order'
      );

    IF bad_state_actions IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 25.1 blocked. Unsupported action_type values in fund_operation_guard_state: %',
            bad_state_actions;
    END IF;

    IF bad_override_actions IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 25.1 blocked. Unsupported action_type values in fund_operation_guard_overrides: %',
            bad_override_actions;
    END IF;

    IF bad_event_actions IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 25.1 blocked. Unsupported action_type values in fund_operation_guard_events: %',
            bad_event_actions;
    END IF;
END
$$;

-- 2. Recreate fund_operation_guard_state action_type CHECK.
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
        'bybit_negative_sale_order'
    )
);

-- 3. Recreate fund_operation_guard_overrides action_type CHECK.
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
        'bybit_negative_sale_order'
    )
);

-- 4. Recreate fund_operation_guard_events action_type CHECK.
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
        'bybit_negative_sale_order'
    )
);

COMMIT;

-- Verification SQL:
-- SELECT conname, pg_get_constraintdef(oid)
-- FROM pg_constraint
-- WHERE conname IN (
--   'fund_operation_guard_state_action_type_check',
--   'fund_operation_guard_overrides_action_type_check',
--   'fund_operation_guard_events_action_type_check'
-- )
-- ORDER BY conname;
