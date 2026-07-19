-- REDEEM FOUNDATION 1/3
-- Safe redeem reserve recovery and negative-net target audit columns.
--
-- Schema-only migration:
-- - no business backfill;
-- - no UPDATE / INSERT / DELETE;
-- - no indexes, triggers, functions or CHECK constraints;
-- - existing rows receive only the server default 0 for the new
--   NOT NULL redeem_reserve_released_shares column.

BEGIN;

DO $$
BEGIN
    IF to_regclass('public.fund_orders') IS NULL THEN
        RAISE EXCEPTION
            'REDEEM FOUNDATION 1/3 blocked: public.fund_orders does not exist';
    END IF;

    IF to_regclass('public.fund_settlement_batches') IS NULL THEN
        RAISE EXCEPTION
            'REDEEM FOUNDATION 1/3 blocked: public.fund_settlement_batches does not exist';
    END IF;
END
$$;

-- Validate any columns that may already exist.
-- Incompatible definitions block the entire transaction instead of
-- being modified automatically.
DO $$
DECLARE
    expected record;
    actual record;
    normalized_default text;
BEGIN
    FOR expected IN
        SELECT *
        FROM (
            VALUES
                (
                    'fund_orders'::text,
                    'redeem_reserve_released_shares'::text,
                    'numeric'::text,
                    NULL::integer,
                    30::integer,
                    10::integer,
                    'NO'::text,
                    true
                ),
                (
                    'fund_orders'::text,
                    'redeem_reserve_released_at'::text,
                    'timestamp with time zone'::text,
                    NULL::integer,
                    NULL::integer,
                    NULL::integer,
                    'YES'::text,
                    false
                ),
                (
                    'fund_orders'::text,
                    'redeem_reserve_release_reason'::text,
                    'text'::text,
                    NULL::integer,
                    NULL::integer,
                    NULL::integer,
                    'YES'::text,
                    false
                ),
                (
                    'fund_settlement_batches'::text,
                    'negative_net_target_diagnostics_json'::text,
                    'jsonb'::text,
                    NULL::integer,
                    NULL::integer,
                    NULL::integer,
                    'YES'::text,
                    false
                ),
                (
                    'fund_settlement_batches'::text,
                    'negative_net_fee_policy_version'::text,
                    'character varying'::text,
                    64::integer,
                    NULL::integer,
                    NULL::integer,
                    'YES'::text,
                    false
                )
        ) AS definitions (
            table_name,
            column_name,
            expected_data_type,
            expected_character_length,
            expected_numeric_precision,
            expected_numeric_scale,
            expected_nullable,
            expected_default_zero
        )
    LOOP
        SELECT
            c.data_type,
            c.character_maximum_length,
            c.numeric_precision,
            c.numeric_scale,
            c.is_nullable,
            c.column_default
        INTO actual
        FROM information_schema.columns AS c
        WHERE c.table_schema = 'public'
          AND c.table_name = expected.table_name
          AND c.column_name = expected.column_name;

        IF FOUND THEN
            IF actual.data_type IS DISTINCT FROM expected.expected_data_type
               OR actual.character_maximum_length
                    IS DISTINCT FROM expected.expected_character_length
               OR actual.numeric_precision
                    IS DISTINCT FROM expected.expected_numeric_precision
               OR actual.numeric_scale
                    IS DISTINCT FROM expected.expected_numeric_scale
               OR actual.is_nullable
                    IS DISTINCT FROM expected.expected_nullable
            THEN
                RAISE EXCEPTION
                    'REDEEM FOUNDATION 1/3 blocked: incompatible definition for %.%. Actual: type=%, char_length=%, precision=%, scale=%, nullable=%',
                    expected.table_name,
                    expected.column_name,
                    actual.data_type,
                    actual.character_maximum_length,
                    actual.numeric_precision,
                    actual.numeric_scale,
                    actual.is_nullable;
            END IF;

            IF expected.expected_default_zero THEN
                normalized_default :=
                    lower(coalesce(actual.column_default, ''));

                normalized_default :=
                    replace(normalized_default, ' ', '');
                normalized_default :=
                    replace(normalized_default, '::numeric', '');
                normalized_default :=
                    replace(normalized_default, '(', '');
                normalized_default :=
                    replace(normalized_default, ')', '');

                IF normalized_default = ''
                   OR normalized_default
                        !~ '^-?[0-9]+([.][0-9]+)?$'
                   OR normalized_default::numeric <> 0
                THEN
                    RAISE EXCEPTION
                        'REDEEM FOUNDATION 1/3 blocked: %.% must have a default equivalent to 0; actual default=%',
                        expected.table_name,
                        expected.column_name,
                        actual.column_default;
                END IF;
            ELSE
                IF actual.column_default IS NOT NULL THEN
                    RAISE EXCEPTION
                        'REDEEM FOUNDATION 1/3 blocked: %.% must not have a default; actual default=%',
                        expected.table_name,
                        expected.column_name,
                        actual.column_default;
                END IF;
            END IF;
        END IF;
    END LOOP;
END
$$;

ALTER TABLE public.fund_orders
    ADD COLUMN IF NOT EXISTS
        redeem_reserve_released_shares numeric(30,10)
        DEFAULT 0 NOT NULL,
    ADD COLUMN IF NOT EXISTS
        redeem_reserve_released_at timestamp with time zone,
    ADD COLUMN IF NOT EXISTS
        redeem_reserve_release_reason text;

ALTER TABLE public.fund_settlement_batches
    ADD COLUMN IF NOT EXISTS
        negative_net_target_diagnostics_json jsonb,
    ADD COLUMN IF NOT EXISTS
        negative_net_fee_policy_version character varying(64);

COMMIT;