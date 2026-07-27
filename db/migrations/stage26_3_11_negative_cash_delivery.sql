-- TASK 3/3 — Negative Cash Delivery database migration.
-- Schema-only, transactional, idempotent and fail-closed.
-- No UPDATE / INSERT / DELETE / TRUNCATE / DROP TABLE / DROP COLUMN.

BEGIN;

DO $$
DECLARE
    missing_tables text;
BEGIN
    SELECT string_agg(table_name, ', ' ORDER BY table_name)
    INTO missing_tables
    FROM (
        VALUES
            ('fund_negative_bybit_flows'),
            ('fund_settlement_batches'),
            ('fund_negative_payout_batches'),
            ('fund_negative_payout_legs'),
            ('funds')
    ) AS required(table_name)
    WHERE to_regclass(format('public.%I', required.table_name)) IS NULL;

    IF missing_tables IS NOT NULL THEN
        RAISE EXCEPTION
            'Negative cash delivery migration blocked. Missing prerequisite tables: %',
            missing_tables;
    END IF;
END
$$;

ALTER TABLE public.fund_negative_bybit_flows
    ADD COLUMN IF NOT EXISTS withdrawal_policy_version character varying(64),
    ADD COLUMN IF NOT EXISTS coin_info_snapshot_json jsonb,
    ADD COLUMN IF NOT EXISTS universal_transfer_intent_json jsonb,
    ADD COLUMN IF NOT EXISTS withdrawal_intent_json jsonb,
    ADD COLUMN IF NOT EXISTS universal_transfer_submitted_at timestamp with time zone,
    ADD COLUMN IF NOT EXISTS withdrawal_submitted_at timestamp with time zone,
    ADD COLUMN IF NOT EXISTS settlement_wallet_balance_before_usdt numeric(30,10),
    ADD COLUMN IF NOT EXISTS settlement_wallet_balance_after_usdt numeric(30,10),
    ADD COLUMN IF NOT EXISTS settlement_wallet_receipt_confirmations integer,
    ADD COLUMN IF NOT EXISTS settlement_wallet_receipt_block_number bigint;

DO $$
DECLARE
    expected record;
    actual record;
BEGIN
    FOR expected IN
        SELECT *
        FROM (
            VALUES
                ('withdrawal_policy_version', 'character varying', 64, NULL, NULL),
                ('coin_info_snapshot_json', 'jsonb', NULL, NULL, NULL),
                ('universal_transfer_intent_json', 'jsonb', NULL, NULL, NULL),
                ('withdrawal_intent_json', 'jsonb', NULL, NULL, NULL),
                ('universal_transfer_submitted_at', 'timestamp with time zone', NULL, NULL, NULL),
                ('withdrawal_submitted_at', 'timestamp with time zone', NULL, NULL, NULL),
                ('settlement_wallet_balance_before_usdt', 'numeric', NULL, 30, 10),
                ('settlement_wallet_balance_after_usdt', 'numeric', NULL, 30, 10),
                ('settlement_wallet_receipt_confirmations', 'integer', NULL, NULL, NULL),
                ('settlement_wallet_receipt_block_number', 'bigint', NULL, NULL, NULL)
        ) AS definitions(
            column_name,
            expected_data_type,
            expected_character_length,
            expected_numeric_precision,
            expected_numeric_scale
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
          AND c.table_name = 'fund_negative_bybit_flows'
          AND c.column_name = expected.column_name;

        IF NOT FOUND THEN
            RAISE EXCEPTION
                'Negative cash delivery migration blocked. Missing column public.fund_negative_bybit_flows.%',
                expected.column_name;
        END IF;

        IF actual.data_type IS DISTINCT FROM expected.expected_data_type THEN
            RAISE EXCEPTION
                'Negative cash delivery migration blocked. Incompatible type for public.fund_negative_bybit_flows.%: expected %, observed %',
                expected.column_name,
                expected.expected_data_type,
                actual.data_type;
        END IF;

        IF expected.expected_character_length IS NOT NULL
           AND actual.character_maximum_length
               IS DISTINCT FROM expected.expected_character_length
        THEN
            RAISE EXCEPTION
                'Negative cash delivery migration blocked. Incompatible character length for public.fund_negative_bybit_flows.%: expected %, observed %',
                expected.column_name,
                expected.expected_character_length,
                actual.character_maximum_length;
        END IF;

        IF expected.expected_numeric_precision IS NOT NULL
           AND (
                actual.numeric_precision
                    IS DISTINCT FROM expected.expected_numeric_precision
                OR actual.numeric_scale
                    IS DISTINCT FROM expected.expected_numeric_scale
           )
        THEN
            RAISE EXCEPTION
                'Negative cash delivery migration blocked. Incompatible numeric contract for public.fund_negative_bybit_flows.%: expected numeric(%,%), observed numeric(%,%)',
                expected.column_name,
                expected.expected_numeric_precision,
                expected.expected_numeric_scale,
                actual.numeric_precision,
                actual.numeric_scale;
        END IF;

        IF actual.is_nullable <> 'YES' THEN
            RAISE EXCEPTION
                'Negative cash delivery migration blocked. public.fund_negative_bybit_flows.% must be nullable',
                expected.column_name;
        END IF;

        IF actual.column_default IS NOT NULL THEN
            RAISE EXCEPTION
                'Negative cash delivery migration blocked. public.fund_negative_bybit_flows.% must not have a default; observed %',
                expected.column_name,
                actual.column_default;
        END IF;
    END LOOP;
END
$$;

DO $$
DECLARE
    status_data_type text;
    status_length integer;
    status_nullable text;
    status_default text;
    normalized_default text;
    status_attnum smallint;

    constraint_oid oid;
    constraint_type "char";
    constraint_columns smallint[];
    constraint_definition text;

    legacy_values text[] := ARRAY[
        'created',
        'preflight_passed',
        'preflight_failed_requires_review',
        'universal_transfer_mocked',
        'universal_transfer_reconciled',
        'withdrawal_mocked',
        'withdrawal_reconciled',
        'settlement_wallet_receipt_confirmed',
        'completed',
        'failed_requires_review'
    ];

    expanded_values text[] := ARRAY[
        'created',
        'preflight_passed',
        'preflight_failed_requires_review',
        'universal_transfer_intent_prepared',
        'universal_transfer_submitting',
        'universal_transfer_reconciling',
        'universal_transfer_mocked',
        'universal_transfer_reconciled',
        'master_balance_confirmed',
        'withdrawal_intent_prepared',
        'withdrawal_submitting',
        'withdrawal_reconciling',
        'withdrawal_mocked',
        'withdrawal_reconciled',
        'settlement_wallet_receipt_pending',
        'settlement_wallet_receipt_confirmed',
        'completed',
        'failed_requires_review'
    ];

    legacy_sorted text[];
    expanded_sorted text[];
    actual_values text[];
    literal_count integer;
    unsupported_statuses text;
    other_checks_before text[];
    other_checks_after text[];
BEGIN
    SELECT
        c.data_type,
        c.character_maximum_length,
        c.is_nullable,
        c.column_default
    INTO
        status_data_type,
        status_length,
        status_nullable,
        status_default
    FROM information_schema.columns AS c
    WHERE c.table_schema = 'public'
      AND c.table_name = 'fund_negative_bybit_flows'
      AND c.column_name = 'status';

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Negative cash delivery migration blocked. Missing public.fund_negative_bybit_flows.status';
    END IF;

    normalized_default := lower(
        regexp_replace(coalesce(status_default, ''), '\s+', '', 'g')
    );
    normalized_default := replace(normalized_default, '::charactervarying', '');
    normalized_default := replace(normalized_default, '::varchar', '');
    normalized_default := replace(normalized_default, '(', '');
    normalized_default := replace(normalized_default, ')', '');

    IF status_data_type <> 'character varying'
       OR status_length <> 64
       OR status_nullable <> 'NO'
       OR normalized_default <> '''created'''
    THEN
        RAISE EXCEPTION
            'Negative cash delivery migration blocked. Incompatible public.fund_negative_bybit_flows.status contract: type=%, length=%, nullable=%, default=%',
            status_data_type,
            status_length,
            status_nullable,
            status_default;
    END IF;

    SELECT a.attnum
    INTO status_attnum
    FROM pg_attribute AS a
    WHERE a.attrelid = 'public.fund_negative_bybit_flows'::regclass
      AND a.attname = 'status'
      AND a.attnum > 0
      AND NOT a.attisdropped;

    SELECT
        c.oid,
        c.contype,
        c.conkey,
        pg_get_constraintdef(c.oid, true)
    INTO
        constraint_oid,
        constraint_type,
        constraint_columns,
        constraint_definition
    FROM pg_constraint AS c
    WHERE c.conrelid = 'public.fund_negative_bybit_flows'::regclass
      AND c.conname = 'fund_negative_bybit_flows_status_check';

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Negative cash delivery migration blocked. Missing fund_negative_bybit_flows_status_check';
    END IF;

    IF constraint_type <> 'c'
       OR constraint_columns
            IS DISTINCT FROM ARRAY[status_attnum]::smallint[]
    THEN
        RAISE EXCEPTION
            'Negative cash delivery migration blocked. fund_negative_bybit_flows_status_check is not the expected single-column CHECK on status';
    END IF;

    SELECT array_agg(value ORDER BY value)
    INTO legacy_sorted
    FROM unnest(legacy_values) AS values_list(value);

    SELECT array_agg(value ORDER BY value)
    INTO expanded_sorted
    FROM unnest(expanded_values) AS values_list(value);

    SELECT
        count(*)::integer,
        coalesce(
            array_agg(DISTINCT m[1] ORDER BY m[1]),
            ARRAY[]::text[]
        )
    INTO
        literal_count,
        actual_values
    FROM regexp_matches(
        constraint_definition,
        '''([^'']*)''',
        'g'
    ) AS m;

    IF literal_count <> cardinality(actual_values) THEN
        RAISE EXCEPTION
            'Negative cash delivery migration blocked. fund_negative_bybit_flows_status_check contains duplicate or unexpected string literals: %',
            constraint_definition;
    END IF;

    SELECT string_agg(status, ', ' ORDER BY status)
    INTO unsupported_statuses
    FROM (
        SELECT DISTINCT status
        FROM public.fund_negative_bybit_flows
        WHERE NOT (status = ANY(expanded_values))
    ) AS unsupported;

    IF unsupported_statuses IS NOT NULL THEN
        RAISE EXCEPTION
            'Negative cash delivery migration blocked. Existing rows contain unsupported fund_negative_bybit_flows.status values: %',
            unsupported_statuses;
    END IF;

    SELECT coalesce(
        array_agg(c.conname ORDER BY c.conname),
        ARRAY[]::text[]
    )
    INTO other_checks_before
    FROM pg_constraint AS c
    WHERE c.conrelid = 'public.fund_negative_bybit_flows'::regclass
      AND c.contype = 'c'
      AND c.conname <> 'fund_negative_bybit_flows_status_check';

    IF actual_values = expanded_sorted THEN
        NULL;
    ELSIF actual_values = legacy_sorted THEN
        ALTER TABLE public.fund_negative_bybit_flows
            DROP CONSTRAINT fund_negative_bybit_flows_status_check;

        ALTER TABLE public.fund_negative_bybit_flows
            ADD CONSTRAINT fund_negative_bybit_flows_status_check
            CHECK (
                status IN (
                    'created',
                    'preflight_passed',
                    'preflight_failed_requires_review',
                    'universal_transfer_intent_prepared',
                    'universal_transfer_submitting',
                    'universal_transfer_reconciling',
                    'universal_transfer_mocked',
                    'universal_transfer_reconciled',
                    'master_balance_confirmed',
                    'withdrawal_intent_prepared',
                    'withdrawal_submitting',
                    'withdrawal_reconciling',
                    'withdrawal_mocked',
                    'withdrawal_reconciled',
                    'settlement_wallet_receipt_pending',
                    'settlement_wallet_receipt_confirmed',
                    'completed',
                    'failed_requires_review'
                )
            );
    ELSE
        RAISE EXCEPTION
            'Negative cash delivery migration blocked. Unexpected legacy status CHECK contract: observed values=%; definition=%',
            actual_values,
            constraint_definition;
    END IF;

    SELECT coalesce(
        array_agg(c.conname ORDER BY c.conname),
        ARRAY[]::text[]
    )
    INTO other_checks_after
    FROM pg_constraint AS c
    WHERE c.conrelid = 'public.fund_negative_bybit_flows'::regclass
      AND c.contype = 'c'
      AND c.conname <> 'fund_negative_bybit_flows_status_check';

    IF other_checks_after IS DISTINCT FROM other_checks_before THEN
        RAISE EXCEPTION
            'Negative cash delivery migration blocked. Unrelated CHECK constraints changed unexpectedly';
    END IF;

    SELECT pg_get_constraintdef(c.oid, true)
    INTO constraint_definition
    FROM pg_constraint AS c
    WHERE c.conrelid = 'public.fund_negative_bybit_flows'::regclass
      AND c.contype = 'c'
      AND c.conname = 'fund_negative_bybit_flows_status_check';

    SELECT
        count(*)::integer,
        coalesce(
            array_agg(DISTINCT m[1] ORDER BY m[1]),
            ARRAY[]::text[]
        )
    INTO
        literal_count,
        actual_values
    FROM regexp_matches(
        constraint_definition,
        '''([^'']*)''',
        'g'
    ) AS m;

    IF literal_count <> 18
       OR actual_values IS DISTINCT FROM expanded_sorted
    THEN
        RAISE EXCEPTION
            'Negative cash delivery migration blocked. Final status CHECK validation failed: values=%, definition=%',
            actual_values,
            constraint_definition;
    END IF;
END
$$;

DO $$
BEGIN
    IF to_regclass('public.fund_bsc_transaction_intents') IS NULL THEN
        CREATE TABLE public.fund_bsc_transaction_intents (
            id bigserial NOT NULL,
            scope_key character varying(192) NOT NULL,
            action_type character varying(64) NOT NULL,
            settlement_batch_id bigint NOT NULL,
            payout_batch_id bigint NOT NULL,
            payout_leg_id bigint,
            fund_id integer NOT NULL,
            asset character varying(16) NOT NULL,
            amount numeric(38,18) NOT NULL,
            from_address character varying(128) NOT NULL,
            to_address character varying(128) NOT NULL,
            chain_id bigint NOT NULL,
            source_nonce bigint NOT NULL,
            prepared_tx_hash character varying(128) NOT NULL,
            prepared_raw_tx text NOT NULL,
            intent_fingerprint character varying(64) NOT NULL,
            status character varying(64) DEFAULT 'prepared'::character varying NOT NULL,
            broadcast_attempts integer DEFAULT 0 NOT NULL,
            receipt_status smallint,
            block_number bigint,
            confirmations integer,
            prepared_at timestamp with time zone NOT NULL,
            broadcast_started_at timestamp with time zone,
            broadcast_at timestamp with time zone,
            visible_at timestamp with time zone,
            confirmed_at timestamp with time zone,
            failed_at timestamp with time zone,
            prepared_json jsonb,
            broadcast_json jsonb,
            reconciliation_json jsonb,
            error text,
            created_at timestamp with time zone DEFAULT now() NOT NULL,
            updated_at timestamp with time zone DEFAULT now() NOT NULL,
            CONSTRAINT fund_bsc_transaction_intents_pkey PRIMARY KEY (id)
        );
    END IF;
END
$$;

DO $$
DECLARE
    expected record;
    actual record;
    expected_names text[] := ARRAY[
        'id', 'scope_key', 'action_type', 'settlement_batch_id',
        'payout_batch_id', 'payout_leg_id', 'fund_id', 'asset',
        'amount', 'from_address', 'to_address', 'chain_id',
        'source_nonce', 'prepared_tx_hash', 'prepared_raw_tx',
        'intent_fingerprint', 'status', 'broadcast_attempts',
        'receipt_status', 'block_number', 'confirmations', 'prepared_at',
        'broadcast_started_at', 'broadcast_at', 'visible_at',
        'confirmed_at', 'failed_at', 'prepared_json', 'broadcast_json',
        'reconciliation_json', 'error', 'created_at', 'updated_at'
    ];
    missing_columns text;
    extra_columns text;
    normalized_default text;
    serial_sequence text;
    pk_count integer;
    pk_columns text[];
    relkind "char";
BEGIN
    SELECT c.relkind
    INTO relkind
    FROM pg_class AS c
    JOIN pg_namespace AS n
      ON n.oid = c.relnamespace
    WHERE n.nspname = 'public'
      AND c.relname = 'fund_bsc_transaction_intents';

    IF NOT FOUND OR relkind <> 'r' THEN
        RAISE EXCEPTION
            'Negative cash delivery migration blocked. public.fund_bsc_transaction_intents is not an ordinary table';
    END IF;

    SELECT string_agg(
        expected_column.column_name,
        ', ' ORDER BY expected_column.column_name
    )
    INTO missing_columns
    FROM unnest(expected_names)
        AS expected_column(column_name)
    WHERE NOT EXISTS (
        SELECT 1
        FROM information_schema.columns AS c
        WHERE c.table_schema = 'public'
          AND c.table_name = 'fund_bsc_transaction_intents'
          AND c.column_name =
              expected_column.column_name
    );

    SELECT string_agg(c.column_name, ', ' ORDER BY c.column_name)
    INTO extra_columns
    FROM information_schema.columns AS c
    WHERE c.table_schema = 'public'
      AND c.table_name = 'fund_bsc_transaction_intents'
      AND NOT (c.column_name = ANY(expected_names));

    IF missing_columns IS NOT NULL OR extra_columns IS NOT NULL THEN
        RAISE EXCEPTION
            'Negative cash delivery migration blocked. fund_bsc_transaction_intents column-set mismatch. Missing=[%], extra=[%]',
            coalesce(missing_columns, ''),
            coalesce(extra_columns, '');
    END IF;

    FOR expected IN
        SELECT *
        FROM (
            VALUES
                ('id', 'bigint', NULL, NULL, NULL, 'NO', 'auto'),
                ('scope_key', 'character varying', 192, NULL, NULL, 'NO', 'none'),
                ('action_type', 'character varying', 64, NULL, NULL, 'NO', 'none'),
                ('settlement_batch_id', 'bigint', NULL, NULL, NULL, 'NO', 'none'),
                ('payout_batch_id', 'bigint', NULL, NULL, NULL, 'NO', 'none'),
                ('payout_leg_id', 'bigint', NULL, NULL, NULL, 'YES', 'none'),
                ('fund_id', 'integer', NULL, NULL, NULL, 'NO', 'none'),
                ('asset', 'character varying', 16, NULL, NULL, 'NO', 'none'),
                ('amount', 'numeric', NULL, 38, 18, 'NO', 'none'),
                ('from_address', 'character varying', 128, NULL, NULL, 'NO', 'none'),
                ('to_address', 'character varying', 128, NULL, NULL, 'NO', 'none'),
                ('chain_id', 'bigint', NULL, NULL, NULL, 'NO', 'none'),
                ('source_nonce', 'bigint', NULL, NULL, NULL, 'NO', 'none'),
                ('prepared_tx_hash', 'character varying', 128, NULL, NULL, 'NO', 'none'),
                ('prepared_raw_tx', 'text', NULL, NULL, NULL, 'NO', 'none'),
                ('intent_fingerprint', 'character varying', 64, NULL, NULL, 'NO', 'none'),
                ('status', 'character varying', 64, NULL, NULL, 'NO', 'prepared'),
                ('broadcast_attempts', 'integer', NULL, NULL, NULL, 'NO', 'zero'),
                ('receipt_status', 'smallint', NULL, NULL, NULL, 'YES', 'none'),
                ('block_number', 'bigint', NULL, NULL, NULL, 'YES', 'none'),
                ('confirmations', 'integer', NULL, NULL, NULL, 'YES', 'none'),
                ('prepared_at', 'timestamp with time zone', NULL, NULL, NULL, 'NO', 'none'),
                ('broadcast_started_at', 'timestamp with time zone', NULL, NULL, NULL, 'YES', 'none'),
                ('broadcast_at', 'timestamp with time zone', NULL, NULL, NULL, 'YES', 'none'),
                ('visible_at', 'timestamp with time zone', NULL, NULL, NULL, 'YES', 'none'),
                ('confirmed_at', 'timestamp with time zone', NULL, NULL, NULL, 'YES', 'none'),
                ('failed_at', 'timestamp with time zone', NULL, NULL, NULL, 'YES', 'none'),
                ('prepared_json', 'jsonb', NULL, NULL, NULL, 'YES', 'none'),
                ('broadcast_json', 'jsonb', NULL, NULL, NULL, 'YES', 'none'),
                ('reconciliation_json', 'jsonb', NULL, NULL, NULL, 'YES', 'none'),
                ('error', 'text', NULL, NULL, NULL, 'YES', 'none'),
                ('created_at', 'timestamp with time zone', NULL, NULL, NULL, 'NO', 'now'),
                ('updated_at', 'timestamp with time zone', NULL, NULL, NULL, 'NO', 'now')
        ) AS definitions(
            column_name,
            expected_data_type,
            expected_character_length,
            expected_numeric_precision,
            expected_numeric_scale,
            expected_nullable,
            default_kind
        )
    LOOP
        SELECT
            c.data_type,
            c.character_maximum_length,
            c.numeric_precision,
            c.numeric_scale,
            c.is_nullable,
            c.column_default,
            c.is_identity
        INTO actual
        FROM information_schema.columns AS c
        WHERE c.table_schema = 'public'
          AND c.table_name = 'fund_bsc_transaction_intents'
          AND c.column_name = expected.column_name;

        IF actual.data_type IS DISTINCT FROM expected.expected_data_type THEN
            RAISE EXCEPTION
                'Negative cash delivery migration blocked. Incompatible type for fund_bsc_transaction_intents.%: expected %, observed %',
                expected.column_name,
                expected.expected_data_type,
                actual.data_type;
        END IF;

        IF expected.expected_character_length IS NOT NULL
           AND actual.character_maximum_length
               IS DISTINCT FROM expected.expected_character_length
        THEN
            RAISE EXCEPTION
                'Negative cash delivery migration blocked. Incompatible character length for fund_bsc_transaction_intents.%: expected %, observed %',
                expected.column_name,
                expected.expected_character_length,
                actual.character_maximum_length;
        END IF;

        IF expected.expected_numeric_precision IS NOT NULL
           AND (
                actual.numeric_precision
                    IS DISTINCT FROM expected.expected_numeric_precision
                OR actual.numeric_scale
                    IS DISTINCT FROM expected.expected_numeric_scale
           )
        THEN
            RAISE EXCEPTION
                'Negative cash delivery migration blocked. Incompatible numeric contract for fund_bsc_transaction_intents.%: expected numeric(%,%), observed numeric(%,%)',
                expected.column_name,
                expected.expected_numeric_precision,
                expected.expected_numeric_scale,
                actual.numeric_precision,
                actual.numeric_scale;
        END IF;

        IF actual.is_nullable IS DISTINCT FROM expected.expected_nullable THEN
            RAISE EXCEPTION
                'Negative cash delivery migration blocked. Incompatible nullability for fund_bsc_transaction_intents.%: expected %, observed %',
                expected.column_name,
                expected.expected_nullable,
                actual.is_nullable;
        END IF;

        IF expected.default_kind = 'none' THEN
            IF actual.column_default IS NOT NULL THEN
                RAISE EXCEPTION
                    'Negative cash delivery migration blocked. fund_bsc_transaction_intents.% must not have a default; observed %',
                    expected.column_name,
                    actual.column_default;
            END IF;
        ELSIF expected.default_kind = 'prepared' THEN
            normalized_default := lower(
                regexp_replace(coalesce(actual.column_default, ''), '\s+', '', 'g')
            );
            normalized_default := replace(normalized_default, '::charactervarying', '');
            normalized_default := replace(normalized_default, '::varchar', '');
            normalized_default := replace(normalized_default, '(', '');
            normalized_default := replace(normalized_default, ')', '');

            IF normalized_default <> '''prepared''' THEN
                RAISE EXCEPTION
                    'Negative cash delivery migration blocked. fund_bsc_transaction_intents.status default must equal prepared; observed %',
                    actual.column_default;
            END IF;
        ELSIF expected.default_kind = 'zero' THEN
            normalized_default := lower(
                regexp_replace(coalesce(actual.column_default, ''), '\s+', '', 'g')
            );
            normalized_default := replace(normalized_default, '::integer', '');
            normalized_default := replace(normalized_default, '::numeric', '');
            normalized_default := replace(normalized_default, '(', '');
            normalized_default := replace(normalized_default, ')', '');

            IF normalized_default = ''
               OR normalized_default !~ '^-?[0-9]+([.][0-9]+)?$'
               OR normalized_default::numeric <> 0
            THEN
                RAISE EXCEPTION
                    'Negative cash delivery migration blocked. fund_bsc_transaction_intents.broadcast_attempts default must equal 0; observed %',
                    actual.column_default;
            END IF;
        ELSIF expected.default_kind = 'now' THEN
            normalized_default := lower(
                regexp_replace(coalesce(actual.column_default, ''), '\s+', '', 'g')
            );

            IF normalized_default <> 'now()' THEN
                RAISE EXCEPTION
                    'Negative cash delivery migration blocked. fund_bsc_transaction_intents.% default must equal now(); observed %',
                    expected.column_name,
                    actual.column_default;
            END IF;
        ELSIF expected.default_kind = 'auto' THEN
            SELECT pg_get_serial_sequence(
                'public.fund_bsc_transaction_intents',
                'id'
            )
            INTO serial_sequence;

            IF actual.is_identity <> 'YES'
               AND (
                    serial_sequence IS NULL
                    OR actual.column_default IS NULL
                    OR actual.column_default NOT LIKE 'nextval(%'
               )
            THEN
                RAISE EXCEPTION
                    'Negative cash delivery migration blocked. fund_bsc_transaction_intents.id is not backed by an identity/owned sequence';
            END IF;
        ELSE
            RAISE EXCEPTION
                'Negative cash delivery migration blocked. Unknown internal default_kind=%',
                expected.default_kind;
        END IF;
    END LOOP;

    SELECT count(*)::integer
    INTO pk_count
    FROM pg_constraint AS c
    WHERE c.conrelid = 'public.fund_bsc_transaction_intents'::regclass
      AND c.contype = 'p';

    IF pk_count <> 1 THEN
        RAISE EXCEPTION
            'Negative cash delivery migration blocked. fund_bsc_transaction_intents must have exactly one primary key; observed %',
            pk_count;
    END IF;

    SELECT ARRAY(
        SELECT a.attname
        FROM pg_constraint AS c
        CROSS JOIN LATERAL unnest(c.conkey)
            WITH ORDINALITY AS key_columns(attnum, ordinal_position)
        JOIN pg_attribute AS a
          ON a.attrelid = c.conrelid
         AND a.attnum = key_columns.attnum
        WHERE c.conrelid = 'public.fund_bsc_transaction_intents'::regclass
          AND c.contype = 'p'
        ORDER BY key_columns.ordinal_position
    )
    INTO pk_columns;

    IF pk_columns IS DISTINCT FROM ARRAY['id']::text[] THEN
        RAISE EXCEPTION
            'Negative cash delivery migration blocked. fund_bsc_transaction_intents primary key must be exactly (id); observed %',
            pk_columns;
    END IF;
END
$$;

DO $$
DECLARE
    expected record;
    source_attnum smallint;
    target_attnum smallint;
    compatible_count integer;
    incompatible_count integer;
    compatible_name text;
    named_conflict boolean;
BEGIN
    FOR expected IN
        SELECT *
        FROM (
            VALUES
                ('settlement_batch_id', 'fund_settlement_batches', 'id', 'fund_bsc_transaction_intents_settlement_batch_id_fkey'),
                ('payout_batch_id', 'fund_negative_payout_batches', 'id', 'fund_bsc_transaction_intents_payout_batch_id_fkey'),
                ('payout_leg_id', 'fund_negative_payout_legs', 'id', 'fund_bsc_transaction_intents_payout_leg_id_fkey'),
                ('fund_id', 'funds', 'id', 'fund_bsc_transaction_intents_fund_id_fkey')
        ) AS definitions(
            source_column,
            target_table,
            target_column,
            preferred_constraint_name
        )
    LOOP
        SELECT a.attnum
        INTO source_attnum
        FROM pg_attribute AS a
        WHERE a.attrelid = 'public.fund_bsc_transaction_intents'::regclass
          AND a.attname = expected.source_column
          AND a.attnum > 0
          AND NOT a.attisdropped;

        SELECT a.attnum
        INTO target_attnum
        FROM pg_attribute AS a
        WHERE a.attrelid = format('public.%I', expected.target_table)::regclass
          AND a.attname = expected.target_column
          AND a.attnum > 0
          AND NOT a.attisdropped;

        SELECT EXISTS (
            SELECT 1
            FROM pg_constraint AS c
            WHERE c.conrelid = 'public.fund_bsc_transaction_intents'::regclass
              AND c.conname = expected.preferred_constraint_name
              AND NOT (
                    c.contype = 'f'
                    AND c.conkey = ARRAY[source_attnum]::smallint[]
                    AND c.confrelid = format('public.%I', expected.target_table)::regclass
                    AND c.confkey = ARRAY[target_attnum]::smallint[]
                    AND c.confdeltype = 'c'
              )
        )
        INTO named_conflict;

        IF named_conflict THEN
            RAISE EXCEPTION
                'Negative cash delivery migration blocked. Constraint name % exists with an incompatible contract',
                expected.preferred_constraint_name;
        END IF;

        SELECT
            count(*) FILTER (
                WHERE c.confrelid = format('public.%I', expected.target_table)::regclass
                  AND c.confkey = ARRAY[target_attnum]::smallint[]
                  AND c.confdeltype = 'c'
            )::integer,
            count(*) FILTER (
                WHERE NOT (
                    c.confrelid = format('public.%I', expected.target_table)::regclass
                    AND c.confkey = ARRAY[target_attnum]::smallint[]
                    AND c.confdeltype = 'c'
                )
            )::integer,
            min(c.conname) FILTER (
                WHERE c.confrelid = format('public.%I', expected.target_table)::regclass
                  AND c.confkey = ARRAY[target_attnum]::smallint[]
                  AND c.confdeltype = 'c'
            )
        INTO
            compatible_count,
            incompatible_count,
            compatible_name
        FROM pg_constraint AS c
        WHERE c.conrelid = 'public.fund_bsc_transaction_intents'::regclass
          AND c.contype = 'f'
          AND c.conkey = ARRAY[source_attnum]::smallint[];

        IF incompatible_count > 0 THEN
            RAISE EXCEPTION
                'Negative cash delivery migration blocked. Incompatible FK exists on fund_bsc_transaction_intents.%',
                expected.source_column;
        END IF;

        IF compatible_count > 1 THEN
            RAISE EXCEPTION
                'Negative cash delivery migration blocked. Duplicate compatible FKs exist on fund_bsc_transaction_intents.%: count=%',
                expected.source_column,
                compatible_count;
        END IF;

        IF compatible_count = 0 THEN
            EXECUTE format(
                'ALTER TABLE public.fund_bsc_transaction_intents ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES public.%I (%I) ON DELETE CASCADE',
                expected.preferred_constraint_name,
                expected.source_column,
                expected.target_table,
                expected.target_column
            );
        ELSE
            RAISE NOTICE
                'Compatible FK retained for fund_bsc_transaction_intents.%: %',
                expected.source_column,
                compatible_name;
        END IF;
    END LOOP;

    IF (
        SELECT count(*)
        FROM pg_constraint AS c
        WHERE c.conrelid = 'public.fund_bsc_transaction_intents'::regclass
          AND c.contype = 'f'
    ) <> 4 THEN
        RAISE EXCEPTION
            'Negative cash delivery migration blocked. fund_bsc_transaction_intents must have exactly four foreign keys';
    END IF;
END
$$;

DO $$
DECLARE
    expected record;
    expected_attnums smallint[];
    existing_type "char";
    existing_attnums smallint[];
    alternate_name text;
    column_sql text;
BEGIN
    FOR expected IN
        SELECT *
        FROM (
            VALUES
                ('fund_bsc_transaction_intents_scope_key_uq', ARRAY['scope_key']::text[]),
                ('fund_bsc_transaction_intents_source_nonce_uq', ARRAY['from_address', 'source_nonce']::text[]),
                ('fund_bsc_transaction_intents_payout_leg_uq', ARRAY['payout_leg_id']::text[])
        ) AS definitions(constraint_name, column_names)
    LOOP
        SELECT array_agg(a.attnum::smallint ORDER BY columns_list.ordinal_position)
        INTO expected_attnums
        FROM unnest(expected.column_names)
            WITH ORDINALITY AS columns_list(column_name, ordinal_position)
        JOIN pg_attribute AS a
          ON a.attrelid = 'public.fund_bsc_transaction_intents'::regclass
         AND a.attname = columns_list.column_name
         AND a.attnum > 0
         AND NOT a.attisdropped;

        SELECT c.contype, c.conkey
        INTO existing_type, existing_attnums
        FROM pg_constraint AS c
        WHERE c.conrelid = 'public.fund_bsc_transaction_intents'::regclass
          AND c.conname = expected.constraint_name;

        IF FOUND THEN
            IF existing_type <> 'u'
               OR existing_attnums IS DISTINCT FROM expected_attnums
            THEN
                RAISE EXCEPTION
                    'Negative cash delivery migration blocked. UNIQUE constraint % has an incompatible contract',
                    expected.constraint_name;
            END IF;
        ELSE
            SELECT c.conname
            INTO alternate_name
            FROM pg_constraint AS c
            WHERE c.conrelid = 'public.fund_bsc_transaction_intents'::regclass
              AND c.contype = 'u'
              AND c.conkey = expected_attnums
            LIMIT 1;

            IF alternate_name IS NOT NULL THEN
                RAISE EXCEPTION
                    'Negative cash delivery migration blocked. Required UNIQUE contract % already exists under unexpected name %',
                    expected.constraint_name,
                    alternate_name;
            END IF;

            SELECT string_agg(format('%I', column_name), ', ' ORDER BY ordinal_position)
            INTO column_sql
            FROM unnest(expected.column_names)
                WITH ORDINALITY AS columns_list(column_name, ordinal_position);

            EXECUTE format(
                'ALTER TABLE public.fund_bsc_transaction_intents ADD CONSTRAINT %I UNIQUE (%s)',
                expected.constraint_name,
                column_sql
            );
        END IF;
    END LOOP;

    IF (
        SELECT count(*)
        FROM pg_constraint AS c
        WHERE c.conrelid = 'public.fund_bsc_transaction_intents'::regclass
          AND c.contype = 'u'
    ) <> 3 THEN
        RAISE EXCEPTION
            'Negative cash delivery migration blocked. fund_bsc_transaction_intents must have exactly three UNIQUE constraints';
    END IF;
END
$$;

DO $$
DECLARE
    expected record;
    observed record;
    alternate_name text;
    column_sql text;
BEGIN
    FOR expected IN
        SELECT *
        FROM (
            VALUES
                ('idx_fund_bsc_transaction_intents_settlement_batch', ARRAY['settlement_batch_id']::text[]),
                ('idx_fund_bsc_transaction_intents_payout_batch', ARRAY['payout_batch_id']::text[]),
                ('idx_fund_bsc_transaction_intents_status_updated', ARRAY['status', 'updated_at']::text[])
        ) AS definitions(index_name, column_names)
    LOOP
        SELECT
            i.indisunique,
            i.indisprimary,
            am.amname,
            i.indpred IS NULL AS has_no_predicate,
            i.indexprs IS NULL AS has_no_expressions,
            i.indnatts = i.indnkeyatts AS has_no_include_columns,
            ARRAY(
                SELECT pg_get_indexdef(
                    i.indexrelid,
                    key_position,
                    true
                )
                FROM generate_series(
                    1,
                    i.indnkeyatts
                ) AS key_positions(key_position)
                ORDER BY key_position
            ) AS column_names
        INTO observed
        FROM pg_class AS index_class
        JOIN pg_namespace AS index_namespace
          ON index_namespace.oid = index_class.relnamespace
        JOIN pg_index AS i
          ON i.indexrelid = index_class.oid
        JOIN pg_class AS table_class
          ON table_class.oid = i.indrelid
        JOIN pg_am AS am
          ON am.oid = index_class.relam
        WHERE index_namespace.nspname = 'public'
          AND index_class.relname = expected.index_name
          AND table_class.oid = 'public.fund_bsc_transaction_intents'::regclass;

        IF FOUND THEN
            IF observed.indisunique
               OR observed.indisprimary
               OR observed.amname <> 'btree'
               OR NOT observed.has_no_predicate
               OR NOT observed.has_no_expressions
               OR NOT observed.has_no_include_columns
               OR observed.column_names IS DISTINCT FROM expected.column_names
            THEN
                RAISE EXCEPTION
                    'Negative cash delivery migration blocked. Index % has an incompatible contract',
                    expected.index_name;
            END IF;
        ELSE
            SELECT index_class.relname
            INTO alternate_name
            FROM pg_class AS index_class
            JOIN pg_namespace AS index_namespace
              ON index_namespace.oid = index_class.relnamespace
            JOIN pg_index AS i
              ON i.indexrelid = index_class.oid
            JOIN pg_am AS am
              ON am.oid = index_class.relam
            WHERE i.indrelid = 'public.fund_bsc_transaction_intents'::regclass
              AND NOT i.indisunique
              AND NOT i.indisprimary
              AND am.amname = 'btree'
              AND i.indpred IS NULL
              AND i.indexprs IS NULL
              AND i.indnatts = i.indnkeyatts
              AND ARRAY(
                    SELECT pg_get_indexdef(
                        i.indexrelid,
                        key_position,
                        true
                    )
                    FROM generate_series(
                        1,
                        i.indnkeyatts
                    ) AS key_positions(key_position)
                    ORDER BY key_position
                  ) = expected.column_names
              AND index_namespace.nspname = 'public'
              AND index_class.relname <> expected.index_name
            LIMIT 1;

            IF alternate_name IS NOT NULL THEN
                RAISE EXCEPTION
                    'Negative cash delivery migration blocked. Required index contract % already exists under unexpected name %',
                    expected.index_name,
                    alternate_name;
            END IF;

            SELECT string_agg(format('%I', column_name), ', ' ORDER BY ordinal_position)
            INTO column_sql
            FROM unnest(expected.column_names)
                WITH ORDINALITY AS columns_list(column_name, ordinal_position);

            EXECUTE format(
                'CREATE INDEX %I ON public.fund_bsc_transaction_intents USING btree (%s)',
                expected.index_name,
                column_sql
            );
        END IF;
    END LOOP;

    IF (
        SELECT count(*)
        FROM pg_index AS i
        WHERE i.indrelid = 'public.fund_bsc_transaction_intents'::regclass
          AND NOT i.indisunique
          AND NOT i.indisprimary
    ) <> 3 THEN
        RAISE EXCEPTION
            'Negative cash delivery migration blocked. fund_bsc_transaction_intents must have exactly three ordinary non-unique indexes';
    END IF;
END
$$;

DO $$
DECLARE
    check_count integer;
    trigger_count integer;
    fk_count integer;
    unique_count integer;
    ordinary_index_count integer;
    table_column_count integer;
    flow_column_count integer;
BEGIN
    SELECT count(*)::integer
    INTO flow_column_count
    FROM information_schema.columns AS c
    WHERE c.table_schema = 'public'
      AND c.table_name = 'fund_negative_bybit_flows'
      AND c.column_name IN (
          'withdrawal_policy_version',
          'coin_info_snapshot_json',
          'universal_transfer_intent_json',
          'withdrawal_intent_json',
          'universal_transfer_submitted_at',
          'withdrawal_submitted_at',
          'settlement_wallet_balance_before_usdt',
          'settlement_wallet_balance_after_usdt',
          'settlement_wallet_receipt_confirmations',
          'settlement_wallet_receipt_block_number'
      );

    SELECT count(*)::integer
    INTO table_column_count
    FROM information_schema.columns AS c
    WHERE c.table_schema = 'public'
      AND c.table_name = 'fund_bsc_transaction_intents';

    SELECT count(*)::integer INTO fk_count
    FROM pg_constraint AS c
    WHERE c.conrelid = 'public.fund_bsc_transaction_intents'::regclass
      AND c.contype = 'f';

    SELECT count(*)::integer INTO unique_count
    FROM pg_constraint AS c
    WHERE c.conrelid = 'public.fund_bsc_transaction_intents'::regclass
      AND c.contype = 'u';

    SELECT count(*)::integer INTO check_count
    FROM pg_constraint AS c
    WHERE c.conrelid = 'public.fund_bsc_transaction_intents'::regclass
      AND c.contype = 'c';

    SELECT count(*)::integer INTO ordinary_index_count
    FROM pg_index AS i
    WHERE i.indrelid = 'public.fund_bsc_transaction_intents'::regclass
      AND NOT i.indisunique
      AND NOT i.indisprimary;

    SELECT count(*)::integer INTO trigger_count
    FROM pg_trigger AS t
    WHERE t.tgrelid = 'public.fund_bsc_transaction_intents'::regclass
      AND NOT t.tgisinternal;

    IF flow_column_count <> 10
       OR table_column_count <> 33
       OR fk_count <> 4
       OR unique_count <> 3
       OR ordinary_index_count <> 3
       OR check_count <> 0
       OR trigger_count <> 0
    THEN
        RAISE EXCEPTION
            'Negative cash delivery migration final validation failed. flow_columns=%, intent_columns=%, fks=%, unique_constraints=%, ordinary_indexes=%, check_constraints=%, user_triggers=%',
            flow_column_count,
            table_column_count,
            fk_count,
            unique_count,
            ordinary_index_count,
            check_count,
            trigger_count;
    END IF;
END
$$;

COMMIT;
