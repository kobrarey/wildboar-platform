BEGIN;

-- ============================================================
-- Stage 26.2.2 - Seed global Operation Guard rows
--
-- Repository-tracked data seed migration.
-- Production DB must be updated only by Server chat after review.
--
-- Safety:
-- - no live flags changed
-- - no secrets changed
-- - no encryption-key settings changed
-- - no fund orders created
-- - no Buy/Sell lifecycle run
-- - no existing rows overwritten
-- ============================================================

WITH required_actions(action_type) AS (
    VALUES
        ('bybit_universal_transfer'),
        ('bybit_master_withdrawal'),
        ('bsc_redeem_payout'),
        ('bsc_settlement_gas_topup'),
        ('bsc_positive_net_to_bybit'),
        ('bsc_buy_collection_gas_topup'),
        ('bsc_buy_collection_usdt_to_settlement'),
        ('bybit_negative_sale_order'),
        ('bybit_allocation_trade_order'),
        ('bybit_allocation_strategy_order'),
        ('bybit_allocation_earn_order'),
        ('bybit_allocation_transfer')
)
INSERT INTO public.fund_operation_guard_state (
    scope_key,
    scope_type,
    fund_id,
    action_type,
    mode,
    reason,
    created_at,
    updated_at
)
SELECT
    'global',
    'global',
    NULL,
    ra.action_type,
    'blocked',
    'Stage 26.2.2 seed missing global Operation Guard state',
    now(),
    now()
FROM required_actions ra
ON CONFLICT (scope_key, action_type) DO NOTHING;

WITH target_fund AS (
    SELECT id
    FROM public.funds
    WHERE code = 'wb_test'
),
required_actions(action_type) AS (
    VALUES
        ('bybit_universal_transfer'),
        ('bybit_master_withdrawal'),
        ('bsc_redeem_payout'),
        ('bsc_settlement_gas_topup'),
        ('bsc_positive_net_to_bybit'),
        ('bsc_buy_collection_gas_topup'),
        ('bsc_buy_collection_usdt_to_settlement'),
        ('bybit_negative_sale_order'),
        ('bybit_allocation_trade_order'),
        ('bybit_allocation_strategy_order'),
        ('bybit_allocation_earn_order'),
        ('bybit_allocation_transfer')
)
INSERT INTO public.fund_operation_guard_state (
    scope_key,
    scope_type,
    fund_id,
    action_type,
    mode,
    reason,
    created_at,
    updated_at
)
SELECT
    'fund:' || tf.id::text,
    'fund',
    tf.id,
    ra.action_type,
    'blocked',
    'Stage 26.2.2 seed missing wb_test all-action Operation Guard state',
    now(),
    now()
FROM target_fund tf
CROSS JOIN required_actions ra
ON CONFLICT (scope_key, action_type) DO NOTHING;

COMMIT;