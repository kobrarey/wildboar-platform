-- Stage 26.3.8
-- BUY E2E hardening schema migration.
-- Scope:
-- - durable BSC intent fields for fund_settlement_transfers
-- - safe BUY reserve release fields for fund_orders
-- - strict Bybit deposit confirmation audit fields for fund_settlement_batches
-- Safety:
-- - schema-only
-- - no business rows modified
-- - no order_id=134 update
-- - no batch_id=170 update
-- - no backfill of request_key/nonce/raw tx/Bybit audit fields
-- - production DB must not be changed by DB chat

BEGIN;

DO $$
DECLARE
    missing_tables text;
BEGIN
    SELECT string_agg(table_name, ', ' ORDER BY table_name)
    INTO missing_tables
    FROM (
        VALUES
            ('fund_orders'),
            ('fund_settlement_batches'),
            ('fund_settlement_transfers')
    ) AS required(table_name)
    WHERE to_regclass('public.' || required.table_name) IS NULL;

    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION
            'BUY E2E hardening migration blocked. Missing tables: %',
            missing_tables;
    END IF;
END
$$;

DO $$
DECLARE
    bad_statuses text;
BEGIN
    SELECT string_agg(DISTINCT status, ', ' ORDER BY status)
    INTO bad_statuses
    FROM public.fund_settlement_transfers
    WHERE status IS NOT NULL
      AND status NOT IN (
          'pending',
          'processing',
          'waiting_for_gas',
          'prepared',
          'sent',
          'confirmed',
          'skipped',
          'pending_confirmation',
          'failed',
          'failed_requires_review'
      );

    IF bad_statuses IS NOT NULL THEN
        RAISE EXCEPTION
            'BUY E2E hardening migration blocked. Unsupported fund_settlement_transfers.status values: %',
            bad_statuses;
    END IF;
END
$$;

ALTER TABLE public.fund_orders
ADD COLUMN IF NOT EXISTS buy_reserve_released_usdt numeric(30,10) NOT NULL DEFAULT 0;

ALTER TABLE public.fund_orders
ADD COLUMN IF NOT EXISTS buy_reserve_released_at timestamp with time zone;

ALTER TABLE public.fund_settlement_batches
ADD COLUMN IF NOT EXISTS bybit_deposit_status character varying(64);

ALTER TABLE public.fund_settlement_batches
ADD COLUMN IF NOT EXISTS bybit_deposit_type character varying(32);

ALTER TABLE public.fund_settlement_batches
ADD COLUMN IF NOT EXISTS bybit_deposit_record_json jsonb;

ALTER TABLE public.fund_settlement_transfers
ADD COLUMN IF NOT EXISTS request_key character varying(256);

ALTER TABLE public.fund_settlement_transfers
ADD COLUMN IF NOT EXISTS chain_id bigint;

ALTER TABLE public.fund_settlement_transfers
ADD COLUMN IF NOT EXISTS source_nonce bigint;

ALTER TABLE public.fund_settlement_transfers
ADD COLUMN IF NOT EXISTS prepared_tx_hash character varying(80);

ALTER TABLE public.fund_settlement_transfers
ADD COLUMN IF NOT EXISTS prepared_raw_tx text;

ALTER TABLE public.fund_settlement_transfers
ADD COLUMN IF NOT EXISTS prepared_at timestamp with time zone;

ALTER TABLE public.fund_settlement_transfers
ADD COLUMN IF NOT EXISTS broadcast_at timestamp with time zone;

DO $$
DECLARE
    duplicate_request_keys text;
BEGIN
    SELECT string_agg(request_key, ', ' ORDER BY request_key)
    INTO duplicate_request_keys
    FROM (
        SELECT request_key
        FROM public.fund_settlement_transfers
        WHERE request_key IS NOT NULL
        GROUP BY request_key
        HAVING COUNT(*) > 1
    ) AS dupes;

    IF duplicate_request_keys IS NOT NULL THEN
        RAISE EXCEPTION
            'BUY E2E hardening migration blocked. Duplicate non-null request_key values already exist: %',
            duplicate_request_keys;
    END IF;
END
$$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'public.fund_settlement_transfers'::regclass
          AND conname = 'fund_settlement_transfers_request_key_uq'
    ) THEN
        ALTER TABLE public.fund_settlement_transfers
        ADD CONSTRAINT fund_settlement_transfers_request_key_uq UNIQUE (request_key);
    END IF;
END
$$;

ALTER TABLE public.fund_settlement_transfers
DROP CONSTRAINT IF EXISTS fund_settlement_transfers_status_check;

ALTER TABLE public.fund_settlement_transfers
ADD CONSTRAINT fund_settlement_transfers_status_check
CHECK (
    status IN (
        'pending',
        'processing',
        'waiting_for_gas',
        'prepared',
        'sent',
        'confirmed',
        'skipped',
        'pending_confirmation',
        'failed',
        'failed_requires_review'
    )
);

COMMIT;