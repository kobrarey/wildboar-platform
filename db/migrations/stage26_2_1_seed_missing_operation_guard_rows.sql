BEGIN;

-- ============================================================
-- Stage 26.2.1 - Seed missing Operation Guard rows for wb_test
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

WITH target_fund AS (
    SELECT id
    FROM public.funds
    WHERE code = 'wb_test'
),
required_actions(action_type) AS (
    VALUES
        ('bybit_negative_sale_order'),
        ('bybit_allocation_strategy_order')
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
    'Stage 26.2.1 seed missing wb_test Operation Guard state',
    now(),
    now()
FROM target_fund tf
CROSS JOIN required_actions ra
ON CONFLICT (scope_key, action_type) DO NOTHING;

COMMIT;