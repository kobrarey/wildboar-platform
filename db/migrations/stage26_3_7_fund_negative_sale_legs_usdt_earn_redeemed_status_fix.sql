-- Stage 26.3.7A
-- fund_negative_sale_legs status CHECK constraint fix
-- Goal: allow live Bybit USDT Earn redeemed status: usdt_earn_redeemed.
-- Scope: public.fund_negative_sale_legs only.
-- Safety: schema-only, no business rows modified, no secrets touched/printed,
--         no pricing unlock, no Operation Guard live window created.

BEGIN;

DO $$
BEGIN
    IF to_regclass('public.fund_negative_sale_legs') IS NULL THEN
        RAISE EXCEPTION
            'Stage 26.3.7A blocked. Missing table: public.fund_negative_sale_legs';
    END IF;
END
$$;

DO $$
DECLARE
    bad_statuses text;
BEGIN
    SELECT string_agg(DISTINCT status, ', ' ORDER BY status)
    INTO bad_statuses
    FROM public.fund_negative_sale_legs
    WHERE status IS NOT NULL
      AND status NOT IN (
          'planned',
          'cash_available',
          'buffer_available',
          'skipped_zero_value',
          'skipped_not_eligible',
          'skipped_min_order',
          'skipped_symbol_not_trading',
          'skipped_liquidity_guard',
          'skipped_margin_guard',
          'market_order_mocked',
          'native_iceberg_mocked',
          'sliced_ioc_mocked',
          'filled',
          'partial_filled_accepted',
          'partial_filled_below_threshold',
          'residualized',
          'usdt_earn_redeem_mocked',
          'usdt_earn_redeemed',
          'extra_sale_planned',
          'extra_sale_mocked',
          'pending_confirmation',
          'failed_requires_review'
      );

    IF bad_statuses IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 26.3.7A blocked. Unsupported existing status values in fund_negative_sale_legs: %',
            bad_statuses;
    END IF;
END
$$;

ALTER TABLE public.fund_negative_sale_legs
DROP CONSTRAINT IF EXISTS fund_negative_sale_legs_status_check;

ALTER TABLE public.fund_negative_sale_legs
ADD CONSTRAINT fund_negative_sale_legs_status_check
CHECK (
    status IN (
        'planned',
        'cash_available',
        'buffer_available',
        'skipped_zero_value',
        'skipped_not_eligible',
        'skipped_min_order',
        'skipped_symbol_not_trading',
        'skipped_liquidity_guard',
        'skipped_margin_guard',
        'market_order_mocked',
        'native_iceberg_mocked',
        'sliced_ioc_mocked',
        'filled',
        'partial_filled_accepted',
        'partial_filled_below_threshold',
        'residualized',
        'usdt_earn_redeem_mocked',
        'usdt_earn_redeemed',
        'extra_sale_planned',
        'extra_sale_mocked',
        'pending_confirmation',
        'failed_requires_review'
    )
);

COMMIT;