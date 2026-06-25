-- Stage 26  gas exhaustion resilience and Bybit withdrawal watchdog / emergency lock
-- Production-safe, non-destructive schema migration.
--
-- Scope:
--   1) waiting_for_gas statuses for retryable gas exhaustion
--   2) retry/cooldown columns
--   3) emergency lock / Bybit withdrawal watchdog tables
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
--
-- Note:
--   This migration drops/recreates only CHECK constraints, not tables/data.

BEGIN;

-- ============================================================
-- 1. Pre-check required existing tables.
-- ============================================================

DO $$
DECLARE
    missing_tables TEXT;
BEGIN
    SELECT string_agg(table_name, ', ' ORDER BY table_name)
    INTO missing_tables
    FROM (
        VALUES
            ('wallet_transfers'),
            ('fund_settlement_transfers'),
            ('fee_wallet_swaps'),
            ('funds')
    ) AS required(table_name)
    WHERE to_regclass('public.' || required.table_name) IS NULL;

    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 26 blocked. Missing required existing tables: %',
            missing_tables;
    END IF;
END
$$;


-- ============================================================
-- 2. Pre-check current status values before CHECK replacement.
-- ============================================================

DO $$
DECLARE
    bad_wallet_transfer_statuses TEXT;
    bad_settlement_transfer_statuses TEXT;
    bad_fee_swap_statuses TEXT;
BEGIN
    SELECT string_agg(DISTINCT status, ', ' ORDER BY status)
    INTO bad_wallet_transfer_statuses
    FROM public.wallet_transfers
    WHERE status IS NOT NULL
      AND status NOT IN (
          'pending',
          'processing',
          'waiting_for_gas',
          'success',
          'failed'
      );

    SELECT string_agg(DISTINCT status, ', ' ORDER BY status)
    INTO bad_settlement_transfer_statuses
    FROM public.fund_settlement_transfers
    WHERE status IS NOT NULL
      AND status NOT IN (
          'pending',
          'processing',
          'waiting_for_gas',
          'sent',
          'confirmed',
          'skipped',
          'pending_confirmation',
          'failed',
          'failed_requires_review'
      );

    SELECT string_agg(DISTINCT status, ', ' ORDER BY status)
    INTO bad_fee_swap_statuses
    FROM public.fee_wallet_swaps
    WHERE status IS NOT NULL
      AND status NOT IN (
          'pending',
          'success',
          'failed',
          'skipped',
          'waiting_for_gas'
      );

    IF bad_wallet_transfer_statuses IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 26 blocked. Unsupported wallet_transfers.status values: %',
            bad_wallet_transfer_statuses;
    END IF;

    IF bad_settlement_transfer_statuses IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 26 blocked. Unsupported fund_settlement_transfers.status values: %',
            bad_settlement_transfer_statuses;
    END IF;

    IF bad_fee_swap_statuses IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 26 blocked. Unsupported fee_wallet_swaps.status values: %',
            bad_fee_swap_statuses;
    END IF;
END
$$;


-- ============================================================
-- 3. Add retry / cooldown fields.
-- ============================================================

ALTER TABLE public.wallet_transfers
ADD COLUMN IF NOT EXISTS next_retry_at timestamp with time zone,
ADD COLUMN IF NOT EXISTS last_gas_alert_at timestamp with time zone;

ALTER TABLE public.fund_settlement_transfers
ADD COLUMN IF NOT EXISTS next_retry_at timestamp with time zone,
ADD COLUMN IF NOT EXISTS last_gas_alert_at timestamp with time zone;

ALTER TABLE public.fee_wallet_swaps
ADD COLUMN IF NOT EXISTS next_retry_at timestamp with time zone,
ADD COLUMN IF NOT EXISTS last_gas_alert_at timestamp with time zone;


-- ============================================================
-- 4. Recreate status CHECK constraints with waiting_for_gas.
-- ============================================================

ALTER TABLE public.wallet_transfers
DROP CONSTRAINT IF EXISTS wallet_transfers_status_check;

ALTER TABLE public.wallet_transfers
ADD CONSTRAINT wallet_transfers_status_check
CHECK (
    status IN (
        'pending',
        'processing',
        'waiting_for_gas',
        'success',
        'failed'
    )
);

ALTER TABLE public.fund_settlement_transfers
DROP CONSTRAINT IF EXISTS fund_settlement_transfers_status_check;

ALTER TABLE public.fund_settlement_transfers
ADD CONSTRAINT fund_settlement_transfers_status_check
CHECK (
    status IN (
        'pending',
        'processing',
        'waiting_for_gas',
        'sent',
        'confirmed',
        'skipped',
        'pending_confirmation',
        'failed',
        'failed_requires_review'
    )
);

ALTER TABLE public.fee_wallet_swaps
DROP CONSTRAINT IF EXISTS fee_wallet_swaps_status_check;

ALTER TABLE public.fee_wallet_swaps
ADD CONSTRAINT fee_wallet_swaps_status_check
CHECK (
    status IN (
        'pending',
        'success',
        'failed',
        'skipped',
        'waiting_for_gas'
    )
);


-- ============================================================
-- 5. Retry / cooldown indexes.
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_wallet_transfers_withdraw_gas_retry
ON public.wallet_transfers (type, status, next_retry_at)
WHERE type = 'withdraw'
  AND status IN ('processing', 'waiting_for_gas');

CREATE INDEX IF NOT EXISTS idx_fund_settlement_transfers_gas_waiting
ON public.fund_settlement_transfers (
    fund_id,
    batch_id,
    transfer_type,
    to_address,
    status,
    next_retry_at
)
WHERE status IN ('pending', 'processing', 'waiting_for_gas');

CREATE INDEX IF NOT EXISTS idx_fee_wallet_swaps_waiting_for_gas
ON public.fee_wallet_swaps (wallet_type, status, next_retry_at)
WHERE status = 'waiting_for_gas';

-- Unique partial index fee_wallet_swaps_one_waiting_for_gas_idx is intentionally
-- not created automatically at Stage 26 because existing rows could conflict.
-- Backend should use idx_fee_wallet_swaps_waiting_for_gas + next_retry_at cooldown.


-- ============================================================
-- 6. platform_emergency_locks.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.platform_emergency_locks (
    id bigserial PRIMARY KEY,
    status varchar(32) NOT NULL DEFAULT 'active',
    reason text NOT NULL,
    source varchar(64) NOT NULL,
    source_event_id bigint NULL,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    resolved_at timestamp with time zone NULL,
    resolved_by varchar(128) NULL,
    resolve_reason text NULL,
    metadata_json jsonb NULL
);

ALTER TABLE public.platform_emergency_locks
DROP CONSTRAINT IF EXISTS platform_emergency_locks_status_check;

ALTER TABLE public.platform_emergency_locks
ADD CONSTRAINT platform_emergency_locks_status_check
CHECK (
    status IN (
        'active',
        'resolved'
    )
);

CREATE INDEX IF NOT EXISTS idx_platform_emergency_locks_active
ON public.platform_emergency_locks (status, created_at DESC)
WHERE status = 'active';


-- ============================================================
-- 7. approved_bybit_withdrawal_windows.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.approved_bybit_withdrawal_windows (
    id bigserial PRIMARY KEY,
    scope varchar(32) NOT NULL DEFAULT 'global',
    fund_id integer NULL REFERENCES public.funds(id),
    coin varchar(32) NOT NULL,
    chain varchar(32) NULL,
    address varchar(256) NULL,
    amount_min numeric(38,18) NULL,
    amount_max numeric(38,18) NULL,
    starts_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    reason text NULL,
    status varchar(32) NOT NULL DEFAULT 'active',
    created_by varchar(128) NULL,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    metadata_json jsonb NULL
);

ALTER TABLE public.approved_bybit_withdrawal_windows
DROP CONSTRAINT IF EXISTS approved_bybit_withdrawal_windows_status_check;

ALTER TABLE public.approved_bybit_withdrawal_windows
ADD CONSTRAINT approved_bybit_withdrawal_windows_status_check
CHECK (
    status IN (
        'active',
        'used',
        'expired',
        'cancelled'
    )
);

ALTER TABLE public.approved_bybit_withdrawal_windows
DROP CONSTRAINT IF EXISTS approved_bybit_withdrawal_windows_scope_check;

ALTER TABLE public.approved_bybit_withdrawal_windows
ADD CONSTRAINT approved_bybit_withdrawal_windows_scope_check
CHECK (
    scope IN (
        'global',
        'fund'
    )
);

ALTER TABLE public.approved_bybit_withdrawal_windows
DROP CONSTRAINT IF EXISTS approved_bybit_withdrawal_windows_time_check;

ALTER TABLE public.approved_bybit_withdrawal_windows
ADD CONSTRAINT approved_bybit_withdrawal_windows_time_check
CHECK (
    expires_at > starts_at
);

CREATE INDEX IF NOT EXISTS idx_approved_bybit_withdrawal_windows_active
ON public.approved_bybit_withdrawal_windows (status, starts_at, expires_at);

CREATE INDEX IF NOT EXISTS idx_approved_bybit_withdrawal_windows_fund
ON public.approved_bybit_withdrawal_windows (fund_id, status, starts_at, expires_at);


-- ============================================================
-- 8. bybit_withdrawal_watchdog_events.
-- ============================================================

CREATE TABLE IF NOT EXISTS public.bybit_withdrawal_watchdog_events (
    id bigserial PRIMARY KEY,
    bybit_withdrawal_id varchar(128) NOT NULL,
    coin varchar(32) NULL,
    chain varchar(32) NULL,
    address varchar(256) NULL,
    amount numeric(38,18) NULL,
    bybit_status varchar(64) NULL,
    source_detected varchar(64) NOT NULL DEFAULT 'bybit_master_api',
    approved_window_id bigint NULL REFERENCES public.approved_bybit_withdrawal_windows(id),
    decision varchar(64) NOT NULL,
    cancel_attempted boolean NOT NULL DEFAULT false,
    cancel_success boolean NULL,
    cancel_error text NULL,
    raw_json jsonb NULL,
    created_at timestamp with time zone NOT NULL DEFAULT now()
);

ALTER TABLE public.bybit_withdrawal_watchdog_events
DROP CONSTRAINT IF EXISTS bybit_withdrawal_watchdog_events_decision_check;

ALTER TABLE public.bybit_withdrawal_watchdog_events
ADD CONSTRAINT bybit_withdrawal_watchdog_events_decision_check
CHECK (
    decision IN (
        'allowed',
        'unexpected',
        'cancel_attempted',
        'cancel_success',
        'cancel_failed',
        'api_unavailable_fail_closed'
    )
);

DO $$
DECLARE
    duplicated_withdrawal_ids TEXT;
BEGIN
    SELECT string_agg(bybit_withdrawal_id, ', ' ORDER BY bybit_withdrawal_id)
    INTO duplicated_withdrawal_ids
    FROM (
        SELECT bybit_withdrawal_id
        FROM public.bybit_withdrawal_watchdog_events
        WHERE bybit_withdrawal_id IS NOT NULL
        GROUP BY bybit_withdrawal_id
        HAVING count(*) > 1
        LIMIT 20
    ) d;

    IF duplicated_withdrawal_ids IS NOT NULL THEN
        RAISE EXCEPTION
            'Stage 26 blocked. Duplicate bybit_withdrawal_watchdog_events.bybit_withdrawal_id values found: %',
            duplicated_withdrawal_ids;
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS bybit_withdrawal_watchdog_events_withdrawal_id_idx
ON public.bybit_withdrawal_watchdog_events (bybit_withdrawal_id);

CREATE INDEX IF NOT EXISTS idx_bybit_withdrawal_watchdog_events_created
ON public.bybit_withdrawal_watchdog_events (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_bybit_withdrawal_watchdog_events_decision
ON public.bybit_withdrawal_watchdog_events (decision, created_at DESC);


COMMIT;


-- ============================================================
-- Verification SQL.
-- ============================================================

-- Required tables:
-- SELECT to_regclass('public.platform_emergency_locks');
-- SELECT to_regclass('public.approved_bybit_withdrawal_windows');
-- SELECT to_regclass('public.bybit_withdrawal_watchdog_events');

-- Retry/cooldown columns:
-- SELECT table_name, column_name, data_type
-- FROM information_schema.columns
-- WHERE table_schema = 'public'
--   AND (
--     (table_name = 'wallet_transfers'
--      AND column_name IN ('next_retry_at', 'last_gas_alert_at'))
--     OR
--     (table_name = 'fund_settlement_transfers'
--      AND column_name IN ('next_retry_at', 'last_gas_alert_at'))
--     OR
--     (table_name = 'fee_wallet_swaps'
--      AND column_name IN ('next_retry_at', 'last_gas_alert_at'))
--   )
-- ORDER BY table_name, column_name;

-- CHECK constraints:
-- SELECT conname, pg_get_constraintdef(oid)
-- FROM pg_constraint
-- WHERE conname IN (
--     'wallet_transfers_status_check',
--     'fund_settlement_transfers_status_check',
--     'fee_wallet_swaps_status_check',
--     'platform_emergency_locks_status_check',
--     'approved_bybit_withdrawal_windows_status_check',
--     'approved_bybit_withdrawal_windows_scope_check',
--     'approved_bybit_withdrawal_windows_time_check',
--     'bybit_withdrawal_watchdog_events_decision_check'
-- )
-- ORDER BY conname;

-- Indexes:
-- SELECT schemaname, tablename, indexname, indexdef
-- FROM pg_indexes
-- WHERE schemaname = 'public'
--   AND indexname IN (
--     'idx_wallet_transfers_withdraw_gas_retry',
--     'idx_fund_settlement_transfers_gas_waiting',
--     'idx_fee_wallet_swaps_waiting_for_gas',
--     'idx_platform_emergency_locks_active',
--     'idx_approved_bybit_withdrawal_windows_active',
--     'idx_approved_bybit_withdrawal_windows_fund',
--     'bybit_withdrawal_watchdog_events_withdrawal_id_idx',
--     'idx_bybit_withdrawal_watchdog_events_created',
--     'idx_bybit_withdrawal_watchdog_events_decision'
--   )
-- ORDER BY tablename, indexname;