BEGIN;

-- ============================================================
-- Stage 26.1 - Bybit Subaccount Freeze Guard schema
--
-- Local DB only.
-- Production DB not touched.
--
-- Non-destructive migration:
-- no DROP TABLE
-- no TRUNCATE
-- no DELETE FROM
-- no DROP SCHEMA
-- no DROP DATABASE
-- no full restore
-- no wallet/private key changes
-- no BYBIT_API_ENC_KEY changes
-- no seed rows
-- no existing data updates
-- existing withdrawal watchdog tables are not changed
-- ============================================================


-- ============================================================
-- 1. Pre-check required existing table
-- ============================================================

DO $$
BEGIN
    IF to_regclass('public.funds') IS NULL THEN
        RAISE EXCEPTION
            'Stage 26.1 blocked. Missing required existing table: public.funds';
    END IF;
END
$$;


-- ============================================================
-- 2. approved_bybit_subaccount_unfreeze_windows
-- ============================================================

CREATE TABLE IF NOT EXISTS public.approved_bybit_subaccount_unfreeze_windows (
    id bigserial PRIMARY KEY,
    fund_id integer NULL REFERENCES public.funds(id) ON DELETE SET NULL,
    bybit_sub_uid character varying(64) NOT NULL,
    starts_at timestamp with time zone NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    reason text NULL,
    status character varying(32) NOT NULL DEFAULT 'active',
    created_by character varying(128) NULL,
    created_at timestamp with time zone NOT NULL DEFAULT now(),
    metadata_json jsonb NULL,

    CONSTRAINT approved_bybit_subaccount_unfreeze_windows_time_check
        CHECK (expires_at > starts_at),

    CONSTRAINT approved_bybit_subaccount_unfreeze_windows_status_check
        CHECK (
            status IN (
                'active',
                'used',
                'expired',
                'cancelled'
            )
        )
);

CREATE INDEX IF NOT EXISTS idx_approved_bybit_subaccount_unfreeze_windows_active
ON public.approved_bybit_subaccount_unfreeze_windows (
    bybit_sub_uid,
    starts_at,
    expires_at
)
WHERE status = 'active';

CREATE INDEX IF NOT EXISTS idx_approved_bybit_subaccount_unfreeze_windows_fund
ON public.approved_bybit_subaccount_unfreeze_windows (
    fund_id,
    status,
    starts_at,
    expires_at
);


-- ============================================================
-- 3. bybit_subaccount_freeze_guard_events
-- ============================================================

CREATE TABLE IF NOT EXISTS public.bybit_subaccount_freeze_guard_events (
    id bigserial PRIMARY KEY,
    fund_id integer NULL REFERENCES public.funds(id) ON DELETE SET NULL,
    bybit_sub_uid character varying(64) NOT NULL,
    desired_frozen integer NOT NULL,
    actual_action character varying(32) NOT NULL,
    approved_window_id bigint NULL REFERENCES public.approved_bybit_subaccount_unfreeze_windows(id) ON DELETE SET NULL,
    decision character varying(64) NOT NULL,
    error text NULL,
    raw_json jsonb NULL,
    created_at timestamp with time zone NOT NULL DEFAULT now(),

    CONSTRAINT bybit_subaccount_freeze_guard_events_desired_frozen_check
        CHECK (desired_frozen IN (0, 1)),

    CONSTRAINT bybit_subaccount_freeze_guard_events_actual_action_check
        CHECK (
            actual_action IN (
                'dry_run_freeze',
                'dry_run_unfreeze',
                'freeze_success',
                'unfreeze_success',
                'freeze_failed',
                'unfreeze_failed',
                'skipped_no_change'
            )
        ),

    CONSTRAINT bybit_subaccount_freeze_guard_events_decision_check
        CHECK (
            decision IN (
                'freeze_required',
                'unfreeze_window_active',
                'api_error_fail_closed',
                'dry_run',
                'skipped'
            )
        )
);

CREATE INDEX IF NOT EXISTS idx_bybit_subaccount_freeze_guard_events_subuid_created
ON public.bybit_subaccount_freeze_guard_events (
    bybit_sub_uid,
    created_at DESC
);

CREATE INDEX IF NOT EXISTS idx_bybit_subaccount_freeze_guard_events_decision
ON public.bybit_subaccount_freeze_guard_events (
    decision,
    created_at DESC
);

CREATE INDEX IF NOT EXISTS idx_bybit_subaccount_freeze_guard_events_created
ON public.bybit_subaccount_freeze_guard_events (
    created_at DESC
);


COMMIT;