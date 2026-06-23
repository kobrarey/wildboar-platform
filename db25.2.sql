--
-- PostgreSQL database dump
--

\restrict 6SrhP9JApeKIoIfTI38IoBkEtsSbrVEcFHVNqt0mKO0ExPzZNgmLj9a9t05dFuK

-- Dumped from database version 16.11
-- Dumped by pg_dump version 16.11

-- Started on 2026-06-23 14:43:34

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- TOC entry 5 (class 2615 OID 32854)
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

-- *not* creating schema, since initdb creates it


--
-- TOC entry 5591 (class 0 OID 0)
-- Dependencies: 5
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS '';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- TOC entry 247 (class 1259 OID 33190)
-- Name: fee_wallet_swaps; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fee_wallet_swaps (
    id bigint NOT NULL,
    wallet_type character varying(16) NOT NULL,
    wallet_address character varying(64) NOT NULL,
    token_in character varying(16) DEFAULT 'USDT'::character varying NOT NULL,
    token_out character varying(16) DEFAULT 'BNB'::character varying NOT NULL,
    amount_in_usdt numeric(38,18) DEFAULT 0 NOT NULL,
    amount_out_bnb numeric(38,18),
    tx_hash character varying(80),
    status character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    executed_at timestamp with time zone,
    CONSTRAINT fee_wallet_swaps_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'success'::character varying, 'failed'::character varying, 'skipped'::character varying])::text[]))),
    CONSTRAINT fee_wallet_swaps_wallet_type_check CHECK (((wallet_type)::text = ANY ((ARRAY['ok'::character varying, 'blocked'::character varying])::text[])))
);


--
-- TOC entry 246 (class 1259 OID 33189)
-- Name: fee_wallet_swaps_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fee_wallet_swaps_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5592 (class 0 OID 0)
-- Dependencies: 246
-- Name: fee_wallet_swaps_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fee_wallet_swaps_id_seq OWNED BY public.fee_wallet_swaps.id;


--
-- TOC entry 261 (class 1259 OID 33423)
-- Name: fund_allocation_batches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_allocation_batches (
    id bigint NOT NULL,
    settlement_batch_id bigint NOT NULL,
    fund_id integer NOT NULL,
    snapshot_ts timestamp with time zone,
    positive_net_usdt numeric(30,10) DEFAULT 0 NOT NULL,
    settlement_nav_usdt numeric(30,10),
    snapshot_total_equity_usdt numeric(30,10),
    base_nav_for_scale_usdt numeric(30,10),
    scale numeric(30,18),
    snapshot_source character varying(64) DEFAULT 'bybit_subaccount'::character varying NOT NULL,
    snapshot_json jsonb,
    status character varying(64) DEFAULT 'planned'::character varying NOT NULL,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    report_json jsonb,
    allocation_started_at timestamp with time zone,
    reconciliation_started_at timestamp with time zone,
    reconciliation_completed_at timestamp with time zone,
    alert_sent_at timestamp with time zone,
    total_legs_count integer,
    filled_legs_count integer,
    skipped_legs_count integer,
    partial_legs_count integer,
    failed_legs_count integer,
    active_legs_count integer,
    total_target_usdt numeric(30,10),
    total_filled_usdt numeric(30,10),
    total_residual_usdt numeric(30,10),
    residual_earn_usdt numeric(30,10),
    residual_cash_usdt numeric(30,10),
    CONSTRAINT fund_allocation_batches_status_check CHECK (((status)::text = ANY ((ARRAY['planned'::character varying, 'snapshot_created'::character varying, 'plan_created'::character varying, 'allocation_processing'::character varying, 'allocation_completed'::character varying, 'allocation_completed_with_residual_earn'::character varying, 'allocation_completed_with_residual_cash'::character varying, 'allocation_failed_requires_review'::character varying, 'failed_requires_review'::character varying])::text[])))
);


--
-- TOC entry 260 (class 1259 OID 33422)
-- Name: fund_allocation_batches_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_allocation_batches_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5593 (class 0 OID 0)
-- Dependencies: 260
-- Name: fund_allocation_batches_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_allocation_batches_id_seq OWNED BY public.fund_allocation_batches.id;


--
-- TOC entry 263 (class 1259 OID 33451)
-- Name: fund_allocation_legs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_allocation_legs (
    id bigint NOT NULL,
    allocation_batch_id bigint NOT NULL,
    settlement_batch_id bigint NOT NULL,
    fund_id integer NOT NULL,
    parent_leg_id bigint,
    leg_index integer NOT NULL,
    leg_key character varying(160) NOT NULL,
    leg_group character varying(64) NOT NULL,
    leg_type character varying(64) NOT NULL,
    coin character varying(32),
    symbol character varying(80),
    category character varying(32),
    side character varying(16),
    location character varying(64),
    current_size numeric(38,18),
    current_usd_value numeric(30,10),
    current_notional_usd numeric(30,10),
    source_weight numeric(30,18),
    target_usdt numeric(30,10),
    target_qty numeric(38,18),
    execution_mode character varying(64) DEFAULT 'planned'::character varying NOT NULL,
    planned_suborders integer,
    executed_suborders integer,
    order_link_id character varying(128),
    bybit_order_id character varying(128),
    strategy_id character varying(128),
    earn_order_id character varying(128),
    transfer_id character varying(128),
    last_price numeric(38,18),
    best_bid numeric(38,18),
    best_ask numeric(38,18),
    corridor_pct numeric(10,6),
    available_liquidity_qty numeric(38,18),
    available_liquidity_usdt numeric(30,10),
    required_qty numeric(38,18),
    required_usdt numeric(30,10),
    filled_qty numeric(38,18),
    filled_usdt numeric(30,10),
    avg_fill_price numeric(38,18),
    fill_ratio numeric(30,18),
    fee_usdt numeric(30,10),
    actual_cash_used_usdt numeric(30,10),
    actual_margin_change_usdt numeric(30,10),
    residual_usdt numeric(30,10),
    status character varying(64) DEFAULT 'planned'::character varying NOT NULL,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    sent_at timestamp with time zone,
    confirmed_at timestamp with time zone,
    earn_product_id character varying(128),
    earn_product_category character varying(64),
    earn_product_status character varying(64),
    earn_min_stake_amount numeric(38,18),
    earn_max_stake_amount numeric(38,18),
    earn_precision numeric(38,18),
    staked_qty numeric(38,18),
    staked_usdt numeric(30,10),
    stake_status character varying(64),
    account_im_rate_before numeric(30,18),
    account_mm_rate_before numeric(30,18),
    account_im_rate_after_est numeric(30,18),
    account_mm_rate_after_est numeric(30,18),
    total_equity_usdt_before numeric(30,10),
    total_initial_margin_usdt_before numeric(30,10),
    total_maintenance_margin_usdt_before numeric(30,10),
    estimated_initial_margin_change_usdt numeric(30,10),
    estimated_maintenance_margin_change_usdt numeric(30,10),
    margin_guard_status character varying(64),
    margin_guard_error text,
    CONSTRAINT fund_allocation_legs_status_check CHECK (((status)::text = ANY ((ARRAY['planned'::character varying, 'skipped_zero_value'::character varying, 'failed_requires_review'::character varying, 'skipped_min_order'::character varying, 'skipped_symbol_not_trading'::character varying, 'skipped_earn_unavailable'::character varying, 'skipped_margin_guard'::character varying, 'market_order_sent'::character varying, 'native_iceberg_processing'::character varying, 'sliced_ioc_processing'::character varying, 'filled'::character varying, 'partial_filled_residualized'::character varying, 'residual_earn_completed'::character varying, 'residual_cash'::character varying])::text[])))
);


--
-- TOC entry 262 (class 1259 OID 33450)
-- Name: fund_allocation_legs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_allocation_legs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5594 (class 0 OID 0)
-- Dependencies: 262
-- Name: fund_allocation_legs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_allocation_legs_id_seq OWNED BY public.fund_allocation_legs.id;


--
-- TOC entry 259 (class 1259 OID 33363)
-- Name: fund_bybit_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_bybit_accounts (
    id bigint NOT NULL,
    fund_id integer NOT NULL,
    bybit_sub_uid character varying(64) NOT NULL,
    bybit_subaccount_name character varying(128),
    coin character varying(16) DEFAULT 'USDT'::character varying NOT NULL,
    chain character varying(64),
    chain_type character varying(64) NOT NULL,
    deposit_address character varying(128) NOT NULL,
    deposit_tag character varying(128),
    is_active boolean DEFAULT true NOT NULL,
    last_verified_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    api_key_encrypted text,
    api_secret_encrypted text,
    api_permissions text,
    api_ip_whitelist text,
    api_key_label character varying(128),
    api_key_added_at timestamp with time zone,
    api_key_last_verified_at timestamp with time zone,
    api_key_is_active boolean DEFAULT false NOT NULL
);


--
-- TOC entry 258 (class 1259 OID 33362)
-- Name: fund_bybit_accounts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_bybit_accounts_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5595 (class 0 OID 0)
-- Dependencies: 258
-- Name: fund_bybit_accounts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_bybit_accounts_id_seq OWNED BY public.fund_bybit_accounts.id;


--
-- TOC entry 241 (class 1259 OID 33121)
-- Name: fund_chart_daily; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_chart_daily (
    id bigint NOT NULL,
    fund_id integer NOT NULL,
    ts_utc timestamp with time zone NOT NULL,
    open numeric(30,10) NOT NULL,
    high numeric(30,10) NOT NULL,
    low numeric(30,10) NOT NULL,
    close numeric(30,10) NOT NULL,
    volume numeric(30,10)
);


--
-- TOC entry 240 (class 1259 OID 33120)
-- Name: fund_chart_daily_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_chart_daily_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5596 (class 0 OID 0)
-- Dependencies: 240
-- Name: fund_chart_daily_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_chart_daily_id_seq OWNED BY public.fund_chart_daily.id;


--
-- TOC entry 243 (class 1259 OID 33136)
-- Name: fund_chart_minute; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_chart_minute (
    id bigint NOT NULL,
    fund_id integer NOT NULL,
    ts_utc timestamp with time zone NOT NULL,
    open numeric(30,10) NOT NULL,
    high numeric(30,10) NOT NULL,
    low numeric(30,10) NOT NULL,
    close numeric(30,10) NOT NULL,
    volume numeric(30,10)
);


--
-- TOC entry 242 (class 1259 OID 33135)
-- Name: fund_chart_minute_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_chart_minute_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5597 (class 0 OID 0)
-- Dependencies: 242
-- Name: fund_chart_minute_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_chart_minute_id_seq OWNED BY public.fund_chart_minute.id;


--
-- TOC entry 250 (class 1259 OID 33221)
-- Name: fund_nav_guard_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_nav_guard_events (
    id bigint NOT NULL,
    fund_id integer NOT NULL,
    snapshot_ts timestamp with time zone NOT NULL,
    decision character varying(16) NOT NULL,
    reason text NOT NULL,
    old_nav_usd numeric(30,10),
    new_nav_usd numeric(30,10),
    old_uta_equity_usd numeric(30,10),
    new_uta_equity_usd numeric(30,10),
    old_funding_wallet_usd numeric(30,10),
    new_funding_wallet_usd numeric(30,10),
    old_earn_usd numeric(30,10),
    new_earn_usd numeric(30,10),
    nav_drop_pct numeric(18,8),
    earn_drop_pct numeric(18,8),
    compensation_ratio numeric(18,8),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT fund_nav_guard_events_decision_check CHECK (((decision)::text = ANY ((ARRAY['accepted'::character varying, 'warning'::character varying, 'rejected'::character varying])::text[])))
);


--
-- TOC entry 249 (class 1259 OID 33220)
-- Name: fund_nav_guard_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_nav_guard_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5598 (class 0 OID 0)
-- Dependencies: 249
-- Name: fund_nav_guard_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_nav_guard_events_id_seq OWNED BY public.fund_nav_guard_events.id;


--
-- TOC entry 248 (class 1259 OID 33208)
-- Name: fund_nav_guard_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_nav_guard_state (
    fund_id integer NOT NULL,
    last_snapshot_ts timestamp with time zone NOT NULL,
    nav_usd numeric(30,10) NOT NULL,
    uta_equity_usd numeric(30,10) NOT NULL,
    funding_wallet_usd numeric(30,10) NOT NULL,
    earn_usd numeric(30,10) NOT NULL,
    source character varying(32) DEFAULT 'bybit_v5'::character varying NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- TOC entry 215 (class 1259 OID 32855)
-- Name: fund_nav_minute; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_nav_minute (
    id bigint NOT NULL,
    fund_id integer NOT NULL,
    ts_utc timestamp with time zone NOT NULL,
    nav_usdt numeric(30,10) NOT NULL,
    shares_outstanding numeric(30,10) NOT NULL
);


--
-- TOC entry 216 (class 1259 OID 32858)
-- Name: fund_nav_minute_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_nav_minute_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5599 (class 0 OID 0)
-- Dependencies: 216
-- Name: fund_nav_minute_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_nav_minute_id_seq OWNED BY public.fund_nav_minute.id;


--
-- TOC entry 271 (class 1259 OID 33643)
-- Name: fund_negative_bybit_flows; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_negative_bybit_flows (
    id bigint NOT NULL,
    settlement_batch_id bigint NOT NULL,
    sale_batch_id bigint NOT NULL,
    fund_id integer NOT NULL,
    status character varying(64) DEFAULT 'created'::character varying NOT NULL,
    coin character varying(16) DEFAULT 'USDT'::character varying NOT NULL,
    chain character varying(32) DEFAULT 'BSC'::character varying NOT NULL,
    required_master_usdt numeric(30,10) NOT NULL,
    withdrawal_request_amount_usdt numeric(30,10) NOT NULL,
    bybit_withdrawal_fee_usdt numeric(30,10) NOT NULL,
    retained_fees_usdt numeric(30,10),
    settlement_wallet_id bigint,
    settlement_wallet_address character varying(128),
    preflight_passed boolean,
    preflight_error text,
    preflight_json jsonb,
    from_sub_uid character varying(64),
    to_master_uid character varying(64),
    from_account_type character varying(32),
    to_account_type character varying(32),
    universal_transfer_id character varying(128),
    universal_transfer_status character varying(64),
    universal_transfer_amount_usdt numeric(30,10),
    universal_transfer_coin character varying(16),
    universal_transfer_created_at timestamp with time zone,
    universal_transfer_confirmed_at timestamp with time zone,
    universal_transfer_mock_json jsonb,
    universal_transfer_reconciliation_json jsonb,
    withdrawal_request_id character varying(128),
    withdrawal_id character varying(128),
    withdrawal_status character varying(64),
    withdrawal_amount_usdt numeric(30,10),
    withdrawal_fee_usdt numeric(30,10),
    withdrawal_coin character varying(16),
    withdrawal_chain character varying(32),
    withdrawal_address character varying(128),
    withdrawal_tx_hash character varying(128),
    withdrawal_created_at timestamp with time zone,
    withdrawal_confirmed_at timestamp with time zone,
    withdrawal_mock_json jsonb,
    withdrawal_record_json jsonb,
    withdrawal_reconciliation_json jsonb,
    settlement_wallet_receipt_status character varying(64),
    settlement_wallet_received_usdt numeric(30,10),
    settlement_wallet_receipt_tx_hash character varying(128),
    settlement_wallet_receipt_confirmed_at timestamp with time zone,
    settlement_wallet_receipt_json jsonb,
    reconciliation_json jsonb,
    report_json jsonb,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT fund_negative_bybit_flows_status_check CHECK (((status)::text = ANY ((ARRAY['created'::character varying, 'preflight_passed'::character varying, 'preflight_failed_requires_review'::character varying, 'universal_transfer_mocked'::character varying, 'universal_transfer_reconciled'::character varying, 'withdrawal_mocked'::character varying, 'withdrawal_reconciled'::character varying, 'settlement_wallet_receipt_confirmed'::character varying, 'completed'::character varying, 'failed_requires_review'::character varying])::text[])))
);


--
-- TOC entry 270 (class 1259 OID 33642)
-- Name: fund_negative_bybit_flows_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_negative_bybit_flows_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5600 (class 0 OID 0)
-- Dependencies: 270
-- Name: fund_negative_bybit_flows_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_negative_bybit_flows_id_seq OWNED BY public.fund_negative_bybit_flows.id;


--
-- TOC entry 277 (class 1259 OID 33802)
-- Name: fund_negative_finalization_batches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_negative_finalization_batches (
    id bigint NOT NULL,
    settlement_batch_id bigint NOT NULL,
    payout_batch_id bigint NOT NULL,
    bybit_flow_id bigint,
    sale_batch_id bigint,
    fund_id integer NOT NULL,
    status character varying(64) DEFAULT 'created'::character varying NOT NULL,
    settlement_price_usdt numeric(30,10) NOT NULL,
    shares_outstanding_before numeric(30,10) NOT NULL,
    shares_outstanding_after numeric(30,10),
    buy_order_count integer,
    redeem_order_count integer,
    success_order_count integer,
    total_buy_usdt numeric(30,10),
    total_buy_shares numeric(30,10),
    total_redeem_shares numeric(30,10),
    planned_net_shares_change numeric(30,10),
    actual_net_shares_change numeric(30,10),
    total_net_user_payout_usdt numeric(30,10),
    total_partial_month_fee_usdt numeric(30,10),
    positions_before_json jsonb,
    positions_after_json jsonb,
    user_wallet_reserves_before_json jsonb,
    user_wallet_reserves_after_json jsonb,
    order_updates_json jsonb,
    fund_update_json jsonb,
    pricing_lock_json jsonb,
    validation_json jsonb,
    accounting_json jsonb,
    reconciliation_json jsonb,
    report_json jsonb,
    finalization_started_at timestamp with time zone,
    accounting_finalized_at timestamp with time zone,
    pricing_unlocked_at timestamp with time zone,
    completed_at timestamp with time zone,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT fund_negative_finalization_batches_status_check CHECK (((status)::text = ANY ((ARRAY['created'::character varying, 'validating'::character varying, 'accounting_processing'::character varying, 'accounting_finalized'::character varying, 'pricing_unlocked'::character varying, 'completed'::character varying, 'failed_requires_review'::character varying])::text[])))
);


--
-- TOC entry 276 (class 1259 OID 33801)
-- Name: fund_negative_finalization_batches_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_negative_finalization_batches_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5601 (class 0 OID 0)
-- Dependencies: 276
-- Name: fund_negative_finalization_batches_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_negative_finalization_batches_id_seq OWNED BY public.fund_negative_finalization_batches.id;


--
-- TOC entry 273 (class 1259 OID 33690)
-- Name: fund_negative_payout_batches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_negative_payout_batches (
    id bigint NOT NULL,
    settlement_batch_id bigint NOT NULL,
    bybit_flow_id bigint NOT NULL,
    fund_id integer NOT NULL,
    status character varying(64) DEFAULT 'created'::character varying NOT NULL,
    coin character varying(16) DEFAULT 'USDT'::character varying NOT NULL,
    chain character varying(32) DEFAULT 'BSC'::character varying NOT NULL,
    settlement_wallet_id bigint,
    settlement_wallet_address character varying(128),
    expected_total_payout_usdt numeric(30,10) NOT NULL,
    planned_total_payout_usdt numeric(30,10),
    confirmed_total_payout_usdt numeric(30,10),
    payout_leg_count integer,
    confirmed_payout_leg_count integer,
    gas_status character varying(64),
    settlement_wallet_bnb_before numeric(38,18),
    settlement_wallet_bnb_required numeric(38,18),
    settlement_wallet_bnb_after numeric(38,18),
    ok_gas_wallet_bnb_available numeric(38,18),
    gas_topup_required_bnb numeric(38,18),
    gas_topup_amount_bnb numeric(38,18),
    gas_topup_tx_hash character varying(128),
    gas_topup_mock_json jsonb,
    gas_reconciliation_json jsonb,
    operator_action_id bigint,
    pause_reason character varying(128),
    payout_started_at timestamp with time zone,
    payout_completed_at timestamp with time zone,
    settlement_wallet_usdt_before numeric(30,10),
    settlement_wallet_usdt_after numeric(30,10),
    balance_refresh_status character varying(64),
    balance_refresh_started_at timestamp with time zone,
    balance_refresh_completed_at timestamp with time zone,
    balance_refresh_json jsonb,
    payout_plan_json jsonb,
    payout_execution_json jsonb,
    reconciliation_json jsonb,
    report_json jsonb,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT fund_negative_payout_batches_balance_refresh_status_check CHECK (((balance_refresh_status IS NULL) OR ((balance_refresh_status)::text = ANY ((ARRAY['not_started'::character varying, 'mocked'::character varying, 'confirmed'::character varying, 'failed_requires_review'::character varying])::text[])))),
    CONSTRAINT fund_negative_payout_batches_gas_status_check CHECK (((gas_status IS NULL) OR ((gas_status)::text = ANY ((ARRAY['not_checked'::character varying, 'sufficient'::character varying, 'topup_required'::character varying, 'topup_mocked'::character varying, 'ready'::character varying, 'insufficient_ok_gas'::character varying, 'failed_requires_review'::character varying])::text[])))),
    CONSTRAINT fund_negative_payout_batches_status_check CHECK (((status)::text = ANY ((ARRAY['created'::character varying, 'gas_check_passed'::character varying, 'gas_topup_mocked'::character varying, 'gas_ready'::character varying, 'paused_operator_action_required'::character varying, 'payouts_planned'::character varying, 'payouts_mocked'::character varying, 'payouts_confirmed'::character varying, 'balance_refresh_mocked'::character varying, 'completed'::character varying, 'failed_requires_review'::character varying])::text[])))
);


--
-- TOC entry 272 (class 1259 OID 33689)
-- Name: fund_negative_payout_batches_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_negative_payout_batches_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5602 (class 0 OID 0)
-- Dependencies: 272
-- Name: fund_negative_payout_batches_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_negative_payout_batches_id_seq OWNED BY public.fund_negative_payout_batches.id;


--
-- TOC entry 275 (class 1259 OID 33736)
-- Name: fund_negative_payout_legs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_negative_payout_legs (
    id bigint NOT NULL,
    payout_batch_id bigint NOT NULL,
    settlement_batch_id bigint NOT NULL,
    bybit_flow_id bigint NOT NULL,
    fund_id integer NOT NULL,
    user_id integer NOT NULL,
    user_wallet_id integer,
    status character varying(64) DEFAULT 'planned'::character varying NOT NULL,
    coin character varying(16) DEFAULT 'USDT'::character varying NOT NULL,
    chain character varying(32) DEFAULT 'BSC'::character varying NOT NULL,
    from_settlement_wallet_id bigint,
    from_address character varying(128),
    to_user_wallet_id integer,
    to_address character varying(128),
    amount_usdt numeric(30,10) NOT NULL,
    order_ids_json jsonb,
    order_allocations_json jsonb,
    deterministic_key character varying(192),
    tx_hash character varying(128),
    confirmations integer,
    sent_at timestamp with time zone,
    confirmed_at timestamp with time zone,
    failed_at timestamp with time zone,
    wallet_balance_before_usdt numeric(30,10),
    wallet_balance_after_usdt numeric(30,10),
    payout_mock_json jsonb,
    confirmation_json jsonb,
    balance_refresh_json jsonb,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT fund_negative_payout_legs_amount_positive CHECK ((amount_usdt > (0)::numeric)),
    CONSTRAINT fund_negative_payout_legs_status_check CHECK (((status)::text = ANY ((ARRAY['planned'::character varying, 'payout_mocked'::character varying, 'payout_confirmed'::character varying, 'balance_refreshed'::character varying, 'skipped_zero_amount'::character varying, 'failed_requires_review'::character varying])::text[])))
);


--
-- TOC entry 274 (class 1259 OID 33735)
-- Name: fund_negative_payout_legs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_negative_payout_legs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5603 (class 0 OID 0)
-- Dependencies: 274
-- Name: fund_negative_payout_legs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_negative_payout_legs_id_seq OWNED BY public.fund_negative_payout_legs.id;


--
-- TOC entry 267 (class 1259 OID 33551)
-- Name: fund_negative_sale_batches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_negative_sale_batches (
    id bigint NOT NULL,
    settlement_batch_id bigint NOT NULL,
    fund_id integer NOT NULL,
    status character varying(64) DEFAULT 'snapshot_created'::character varying NOT NULL,
    required_master_usdt numeric(30,10),
    withdrawal_request_amount_usdt numeric(30,10),
    total_net_user_payout_usdt numeric(30,10),
    total_partial_month_fee_usdt numeric(30,10),
    bybit_withdrawal_fee_usdt numeric(30,10),
    unified_usdt_available numeric(30,10),
    fund_wallet_usdt_available numeric(30,10),
    usdt_earn_available numeric(30,10),
    total_cash_like_available_usdt numeric(30,10),
    sale_target_usdt numeric(30,10),
    planned_sale_usdt numeric(30,10),
    expected_shortage_usdt numeric(30,10),
    expected_surplus_usdt numeric(30,10),
    largest_extra_sale_buffer_pct numeric(18,10),
    snapshot_json jsonb,
    plan_json jsonb,
    report_json jsonb,
    error text,
    snapshot_created_at timestamp with time zone,
    plan_created_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    execution_started_at timestamp with time zone,
    execution_completed_at timestamp with time zone,
    available_usdt_before_execution numeric(30,10),
    initial_cash_like_usdt numeric(30,10),
    usdt_earn_redeemed_usdt numeric(30,10),
    initial_sale_executed_usdt numeric(30,10),
    available_usdt_after_initial_sales numeric(30,10),
    shortage_after_initial_sales_usdt numeric(30,10),
    extra_sale_required_usdt numeric(30,10),
    extra_sale_target_usdt numeric(30,10),
    extra_sale_executed_usdt numeric(30,10),
    final_available_usdt numeric(30,10),
    final_shortage_usdt numeric(30,10),
    final_surplus_usdt numeric(30,10),
    execution_json jsonb,
    reconciliation_json jsonb,
    CONSTRAINT fund_negative_sale_batches_status_check CHECK (((status)::text = ANY ((ARRAY['snapshot_created'::character varying, 'sale_plan_created'::character varying, 'sale_execution_processing'::character varying, 'sale_execution_completed'::character varying, 'sale_execution_completed_with_extra_sale'::character varying, 'sale_execution_failed_requires_review'::character varying, 'sale_plan_failed_requires_review'::character varying])::text[])))
);


--
-- TOC entry 266 (class 1259 OID 33550)
-- Name: fund_negative_sale_batches_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_negative_sale_batches_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5604 (class 0 OID 0)
-- Dependencies: 266
-- Name: fund_negative_sale_batches_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_negative_sale_batches_id_seq OWNED BY public.fund_negative_sale_batches.id;


--
-- TOC entry 269 (class 1259 OID 33575)
-- Name: fund_negative_sale_legs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_negative_sale_legs (
    id bigint NOT NULL,
    sale_batch_id bigint NOT NULL,
    settlement_batch_id bigint NOT NULL,
    fund_id integer NOT NULL,
    leg_index integer NOT NULL,
    leg_group character varying(64) NOT NULL,
    leg_type character varying(64) NOT NULL,
    coin character varying(32),
    symbol character varying(64),
    category character varying(32),
    side character varying(16),
    location character varying(64),
    current_qty numeric(38,18),
    current_size numeric(38,18),
    current_usd_value numeric(30,10),
    current_notional_usd numeric(30,10),
    source_weight numeric(30,18),
    target_cash_usdt numeric(30,10),
    target_qty numeric(38,18),
    expected_cash_delta_usdt numeric(30,10),
    eligible boolean DEFAULT true NOT NULL,
    eligibility_reason text,
    use_for_deficit_cover boolean DEFAULT true NOT NULL,
    instrument_status character varying(64),
    min_order_passed boolean,
    liquidity_check_required boolean,
    margin_guard_required boolean,
    planned_execution_mode character varying(64),
    order_link_id character varying(128),
    strategy_id character varying(128),
    status character varying(64) DEFAULT 'planned'::character varying NOT NULL,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    actual_execution_mode character varying(64),
    execution_round character varying(64),
    deterministic_key character varying(160),
    bybit_order_id character varying(128),
    bybit_strategy_id character varying(128),
    planned_suborders integer,
    executed_suborders integer,
    suborders_json jsonb,
    mock_execution_json jsonb,
    last_price numeric(30,10),
    best_bid numeric(30,10),
    best_ask numeric(30,10),
    corridor_pct numeric(18,10),
    available_liquidity_usdt numeric(30,10),
    available_liquidity_qty numeric(38,18),
    filled_qty numeric(38,18),
    filled_usdt numeric(30,10),
    avg_fill_price numeric(30,10),
    fill_ratio numeric(18,10),
    unfilled_usdt numeric(30,10),
    fee_usdt numeric(30,10),
    cash_delta_usdt numeric(30,10),
    sent_at timestamp with time zone,
    confirmed_at timestamp with time zone,
    failed_at timestamp with time zone,
    execution_error text,
    CONSTRAINT fund_negative_sale_legs_status_check CHECK (((status)::text = ANY ((ARRAY['planned'::character varying, 'cash_available'::character varying, 'buffer_available'::character varying, 'skipped_zero_value'::character varying, 'skipped_not_eligible'::character varying, 'skipped_min_order'::character varying, 'skipped_symbol_not_trading'::character varying, 'skipped_liquidity_guard'::character varying, 'skipped_margin_guard'::character varying, 'market_order_mocked'::character varying, 'native_iceberg_mocked'::character varying, 'sliced_ioc_mocked'::character varying, 'filled'::character varying, 'partial_filled_accepted'::character varying, 'partial_filled_below_threshold'::character varying, 'residualized'::character varying, 'usdt_earn_redeem_mocked'::character varying, 'extra_sale_planned'::character varying, 'extra_sale_mocked'::character varying, 'pending_confirmation'::character varying, 'failed_requires_review'::character varying])::text[])))
);


--
-- TOC entry 268 (class 1259 OID 33574)
-- Name: fund_negative_sale_legs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_negative_sale_legs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5605 (class 0 OID 0)
-- Dependencies: 268
-- Name: fund_negative_sale_legs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_negative_sale_legs_id_seq OWNED BY public.fund_negative_sale_legs.id;


--
-- TOC entry 283 (class 1259 OID 33915)
-- Name: fund_operation_guard_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_operation_guard_events (
    id bigint NOT NULL,
    action_type character varying(64) NOT NULL,
    scope_key character varying(128) NOT NULL,
    scope_type character varying(16) NOT NULL,
    fund_id integer,
    settlement_batch_id bigint,
    request_id character varying(192),
    amount_usdt numeric(30,10),
    decision character varying(32) NOT NULL,
    reason text,
    guard_state_id bigint,
    override_id bigint,
    mode_snapshot character varying(32),
    metadata_json jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT fund_operation_guard_events_action_type_check CHECK (((action_type)::text = ANY ((ARRAY['bybit_universal_transfer'::character varying, 'bybit_master_withdrawal'::character varying, 'bsc_redeem_payout'::character varying, 'bsc_settlement_gas_topup'::character varying, 'bsc_positive_net_to_bybit'::character varying, 'bsc_buy_collection_gas_topup'::character varying, 'bsc_buy_collection_usdt_to_settlement'::character varying, 'bybit_negative_sale_order'::character varying, 'bybit_allocation_trade_order'::character varying, 'bybit_allocation_strategy_order'::character varying, 'bybit_allocation_earn_order'::character varying, 'bybit_allocation_transfer'::character varying])::text[]))),
    CONSTRAINT fund_operation_guard_events_decision_check CHECK (((decision)::text = ANY ((ARRAY['allowed'::character varying, 'blocked'::character varying, 'error'::character varying])::text[]))),
    CONSTRAINT fund_operation_guard_events_mode_snapshot_check CHECK (((mode_snapshot IS NULL) OR ((mode_snapshot)::text = ANY ((ARRAY['blocked'::character varying, 'live_allowed'::character varying])::text[])))),
    CONSTRAINT fund_operation_guard_events_scope_type_check CHECK (((scope_type)::text = ANY ((ARRAY['global'::character varying, 'fund'::character varying])::text[])))
);


--
-- TOC entry 282 (class 1259 OID 33914)
-- Name: fund_operation_guard_events_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_operation_guard_events_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5606 (class 0 OID 0)
-- Dependencies: 282
-- Name: fund_operation_guard_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_operation_guard_events_id_seq OWNED BY public.fund_operation_guard_events.id;


--
-- TOC entry 281 (class 1259 OID 33881)
-- Name: fund_operation_guard_overrides; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_operation_guard_overrides (
    id bigint NOT NULL,
    scope_key character varying(128) NOT NULL,
    scope_type character varying(16) NOT NULL,
    fund_id integer,
    action_type character varying(64) NOT NULL,
    status character varying(32) DEFAULT 'active'::character varying NOT NULL,
    manager_user_id bigint NOT NULL,
    settlement_batch_id bigint,
    request_id character varying(192),
    idempotency_key character varying(192) NOT NULL,
    max_amount_usdt numeric(30,10),
    starts_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone,
    revoked_at timestamp with time zone,
    reason text,
    payload_json jsonb,
    result_json jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT fund_operation_guard_overrides_action_type_check CHECK (((action_type)::text = ANY ((ARRAY['bybit_universal_transfer'::character varying, 'bybit_master_withdrawal'::character varying, 'bsc_redeem_payout'::character varying, 'bsc_settlement_gas_topup'::character varying, 'bsc_positive_net_to_bybit'::character varying, 'bsc_buy_collection_gas_topup'::character varying, 'bsc_buy_collection_usdt_to_settlement'::character varying, 'bybit_negative_sale_order'::character varying, 'bybit_allocation_trade_order'::character varying, 'bybit_allocation_strategy_order'::character varying, 'bybit_allocation_earn_order'::character varying, 'bybit_allocation_transfer'::character varying])::text[]))),
    CONSTRAINT fund_operation_guard_overrides_expiry_check CHECK ((expires_at > starts_at)),
    CONSTRAINT fund_operation_guard_overrides_scope_type_check CHECK (((scope_type)::text = ANY ((ARRAY['global'::character varying, 'fund'::character varying])::text[]))),
    CONSTRAINT fund_operation_guard_overrides_status_check CHECK (((status)::text = ANY ((ARRAY['active'::character varying, 'used'::character varying, 'expired'::character varying, 'revoked'::character varying])::text[])))
);


--
-- TOC entry 280 (class 1259 OID 33880)
-- Name: fund_operation_guard_overrides_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_operation_guard_overrides_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5607 (class 0 OID 0)
-- Dependencies: 280
-- Name: fund_operation_guard_overrides_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_operation_guard_overrides_id_seq OWNED BY public.fund_operation_guard_overrides.id;


--
-- TOC entry 279 (class 1259 OID 33854)
-- Name: fund_operation_guard_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_operation_guard_state (
    id bigint NOT NULL,
    scope_key character varying(128) NOT NULL,
    scope_type character varying(16) NOT NULL,
    fund_id integer,
    action_type character varying(64) NOT NULL,
    mode character varying(32) DEFAULT 'blocked'::character varying NOT NULL,
    reason text,
    updated_by_user_id bigint,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT fund_operation_guard_state_action_type_check CHECK (((action_type)::text = ANY ((ARRAY['bybit_universal_transfer'::character varying, 'bybit_master_withdrawal'::character varying, 'bsc_redeem_payout'::character varying, 'bsc_settlement_gas_topup'::character varying, 'bsc_positive_net_to_bybit'::character varying, 'bsc_buy_collection_gas_topup'::character varying, 'bsc_buy_collection_usdt_to_settlement'::character varying, 'bybit_negative_sale_order'::character varying, 'bybit_allocation_trade_order'::character varying, 'bybit_allocation_strategy_order'::character varying, 'bybit_allocation_earn_order'::character varying, 'bybit_allocation_transfer'::character varying])::text[]))),
    CONSTRAINT fund_operation_guard_state_mode_check CHECK (((mode)::text = ANY ((ARRAY['blocked'::character varying, 'live_allowed'::character varying])::text[]))),
    CONSTRAINT fund_operation_guard_state_scope_type_check CHECK (((scope_type)::text = ANY ((ARRAY['global'::character varying, 'fund'::character varying])::text[])))
);


--
-- TOC entry 278 (class 1259 OID 33853)
-- Name: fund_operation_guard_state_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_operation_guard_state_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5608 (class 0 OID 0)
-- Dependencies: 278
-- Name: fund_operation_guard_state_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_operation_guard_state_id_seq OWNED BY public.fund_operation_guard_state.id;


--
-- TOC entry 265 (class 1259 OID 33509)
-- Name: fund_operator_actions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_operator_actions (
    id bigint NOT NULL,
    fund_id integer,
    settlement_batch_id bigint,
    allocation_batch_id bigint,
    action_type character varying(64) NOT NULL,
    reason character varying(128),
    status character varying(64) DEFAULT 'pending'::character varying NOT NULL,
    idempotency_key character varying(160) NOT NULL,
    callback_token_hash character varying(128),
    telegram_chat_id character varying(64),
    telegram_user_id character varying(64),
    telegram_message_id character varying(64),
    telegram_callback_query_id character varying(128),
    requested_by character varying(128),
    requested_at timestamp with time zone DEFAULT now() NOT NULL,
    processing_started_at timestamp with time zone,
    processed_at timestamp with time zone,
    expires_at timestamp with time zone,
    attempts integer DEFAULT 0 NOT NULL,
    payload_json jsonb,
    result_json jsonb,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT fund_operator_actions_action_type_check CHECK (((action_type)::text = ANY ((ARRAY['retry_settlement_gas_topup'::character varying, 'negative_net_retry_gas_topup'::character varying])::text[]))),
    CONSTRAINT fund_operator_actions_reason_check CHECK (((reason IS NULL) OR ((reason)::text = 'insufficient_ok_gas'::text))),
    CONSTRAINT fund_operator_actions_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'processing'::character varying, 'success'::character varying, 'failed'::character varying, 'expired'::character varying, 'cancelled'::character varying, 'ignored'::character varying])::text[])))
);


--
-- TOC entry 264 (class 1259 OID 33508)
-- Name: fund_operator_actions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_operator_actions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5609 (class 0 OID 0)
-- Dependencies: 264
-- Name: fund_operator_actions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_operator_actions_id_seq OWNED BY public.fund_operator_actions.id;


--
-- TOC entry 237 (class 1259 OID 33078)
-- Name: fund_orders; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_orders (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    fund_id integer NOT NULL,
    side character varying(16) NOT NULL,
    amount_usdt numeric(30,10),
    shares numeric(30,10),
    price_usdt numeric(30,10),
    status character varying(64) DEFAULT 'pending'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    executed_at timestamp with time zone,
    settlement_batch_id bigint,
    reserved_at timestamp with time zone,
    settlement_locked_at timestamp with time zone,
    collection_confirmed_at timestamp with time zone,
    error text,
    gross_redeem_usdt numeric(30,10),
    success_fee_usdt numeric(30,10),
    management_fee_usdt numeric(30,10),
    partial_month_fee_usdt numeric(30,10),
    net_user_payout_usdt numeric(30,10),
    net_price_usdt numeric(30,10),
    fee_calc_month_open_price_usdt numeric(30,10),
    fee_calc_days_in_month_period integer,
    success_fee_rate numeric(18,10),
    management_fee_rate numeric(18,10),
    CONSTRAINT fund_orders_side_check CHECK (((side)::text = ANY ((ARRAY['buy'::character varying, 'redeem'::character varying])::text[]))),
    CONSTRAINT fund_orders_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'settling'::character varying, 'buy_collecting'::character varying, 'buy_collected'::character varying, 'awaiting_positive_net_execution'::character varying, 'awaiting_negative_net_execution'::character varying, 'processing'::character varying, 'success'::character varying, 'failed'::character varying, 'cancelled'::character varying, 'failed_requires_review'::character varying])::text[])))
);


--
-- TOC entry 236 (class 1259 OID 33077)
-- Name: fund_orders_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_orders_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5610 (class 0 OID 0)
-- Dependencies: 236
-- Name: fund_orders_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_orders_id_seq OWNED BY public.fund_orders.id;


--
-- TOC entry 257 (class 1259 OID 33344)
-- Name: fund_runtime_state; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_runtime_state (
    fund_id integer NOT NULL,
    pricing_locked boolean DEFAULT false NOT NULL,
    pricing_lock_reason character varying(128),
    pricing_lock_batch_id bigint,
    pricing_locked_at timestamp with time zone,
    pricing_unlocked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- TOC entry 254 (class 1259 OID 33268)
-- Name: fund_settlement_batches; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_settlement_batches (
    id bigint NOT NULL,
    fund_id integer NOT NULL,
    settlement_date date NOT NULL,
    cutoff_ts timestamp with time zone NOT NULL,
    settlement_ts timestamp with time zone NOT NULL,
    price_ts timestamp with time zone,
    settlement_price_usdt numeric(30,10),
    nav_usdt numeric(30,10),
    shares_outstanding_before numeric(30,10),
    total_buy_usdt numeric(30,10) DEFAULT 0 NOT NULL,
    total_redeem_shares numeric(30,10) DEFAULT 0 NOT NULL,
    total_redeem_usdt numeric(30,10) DEFAULT 0 NOT NULL,
    net_cash_usdt numeric(30,10) DEFAULT 0 NOT NULL,
    planned_shares_to_issue numeric(30,10) DEFAULT 0 NOT NULL,
    planned_shares_to_redeem numeric(30,10) DEFAULT 0 NOT NULL,
    planned_net_shares_change numeric(30,10) DEFAULT 0 NOT NULL,
    status character varying(64) DEFAULT 'created'::character varying NOT NULL,
    error text,
    pricing_locked_at timestamp with time zone,
    pricing_unlocked_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    positive_net_started_at timestamp with time zone,
    seller_payouts_completed_at timestamp with time zone,
    bybit_deposit_tx_hash character varying(80),
    bybit_deposit_confirmed_at timestamp with time zone,
    bybit_deposit_account_type character varying(32),
    bybit_internal_transfer_id character varying(128),
    bybit_internal_transfer_completed_at timestamp with time zone,
    accounting_finalized_at timestamp with time zone,
    bybit_deposit_record_id character varying(128),
    bybit_deposit_to_address character varying(128),
    bybit_deposit_success_at character varying(64),
    bybit_internal_transfer_status character varying(32),
    bybit_internal_transfer_error text,
    total_gross_redeem_usdt numeric(30,10),
    total_net_user_payout_usdt numeric(30,10),
    total_success_fee_usdt numeric(30,10),
    total_management_fee_usdt numeric(30,10),
    total_partial_month_fee_usdt numeric(30,10),
    bybit_withdrawal_fee_usdt numeric(30,10),
    required_master_usdt numeric(30,10),
    withdrawal_request_amount_usdt numeric(30,10),
    negative_net_target_calculated_at timestamp with time zone,
    fee_calc_month_open_price_usdt numeric(30,10),
    fee_calc_month_open_source character varying(64),
    fee_calc_days_in_month_period integer,
    CONSTRAINT fund_settlement_batches_status_check CHECK (((status)::text = ANY ((ARRAY['created'::character varying, 'pricing_locked'::character varying, 'price_fixed'::character varying, 'gas_checking'::character varying, 'gas_ready'::character varying, 'collecting_buy_usdt'::character varying, 'buy_usdt_collected'::character varying, 'awaiting_positive_net_execution'::character varying, 'awaiting_negative_net_execution'::character varying, 'pending_confirmation'::character varying, 'positive_net_processing'::character varying, 'positive_net_accounting_finalized'::character varying, 'positive_cash_settlement_completed'::character varying, 'no_orders'::character varying, 'failed'::character varying, 'failed_requires_review'::character varying, 'paused_operator_action_required'::character varying, 'negative_net_targets_calculated'::character varying, 'negative_net_sale_planned'::character varying, 'negative_net_sale_processing'::character varying, 'negative_net_sale_executed'::character varying, 'negative_net_master_flow_processing'::character varying, 'negative_net_withdrawal_pending'::character varying, 'negative_net_withdrawal_reconciling'::character varying, 'negative_net_cash_ready_for_payout'::character varying, 'negative_net_payout_processing'::character varying, 'negative_net_payouts_confirmed'::character varying, 'awaiting_bybit_withdrawal'::character varying, 'bybit_withdrawal_confirmed'::character varying, 'seller_payout_processing'::character varying, 'negative_net_accounting_finalized'::character varying, 'negative_cash_settlement_completed'::character varying])::text[])))
);


--
-- TOC entry 253 (class 1259 OID 33267)
-- Name: fund_settlement_batches_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_settlement_batches_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5611 (class 0 OID 0)
-- Dependencies: 253
-- Name: fund_settlement_batches_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_settlement_batches_id_seq OWNED BY public.fund_settlement_batches.id;


--
-- TOC entry 256 (class 1259 OID 33305)
-- Name: fund_settlement_transfers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_settlement_transfers (
    id bigint NOT NULL,
    batch_id bigint NOT NULL,
    order_id bigint,
    fund_id integer NOT NULL,
    user_id bigint,
    transfer_type character varying(64) NOT NULL,
    from_address character varying(64),
    to_address character varying(64),
    amount_usdt numeric(38,18),
    amount_bnb numeric(38,18),
    gas_tx_hash character varying(80),
    tx_hash character varying(80),
    status character varying(64) DEFAULT 'pending'::character varying NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    sent_at timestamp with time zone,
    confirmed_at timestamp with time zone,
    CONSTRAINT fund_settlement_transfers_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'processing'::character varying, 'sent'::character varying, 'confirmed'::character varying, 'skipped'::character varying, 'pending_confirmation'::character varying, 'failed'::character varying, 'failed_requires_review'::character varying])::text[]))),
    CONSTRAINT fund_settlement_transfers_transfer_type_check CHECK (((transfer_type)::text = ANY ((ARRAY['settlement_wallet_gas_topup'::character varying, 'user_wallet_gas_topup'::character varying, 'user_buy_usdt_to_settlement'::character varying, 'redeem_payout_settlement_to_user_wallet'::character varying, 'positive_net_settlement_to_bybit_subaccount'::character varying, 'bybit_fund_to_unified_internal_transfer'::character varying])::text[])))
);


--
-- TOC entry 255 (class 1259 OID 33304)
-- Name: fund_settlement_transfers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_settlement_transfers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5612 (class 0 OID 0)
-- Dependencies: 255
-- Name: fund_settlement_transfers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_settlement_transfers_id_seq OWNED BY public.fund_settlement_transfers.id;


--
-- TOC entry 252 (class 1259 OID 33245)
-- Name: fund_wallets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fund_wallets (
    id bigint NOT NULL,
    fund_id integer NOT NULL,
    blockchain character varying(32) DEFAULT 'BSC'::character varying NOT NULL,
    wallet_type character varying(32) DEFAULT 'settlement'::character varying NOT NULL,
    address character varying(64) NOT NULL,
    encrypted_private_key text NOT NULL,
    derivation_path character varying(128),
    derivation_index integer,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    archived_at timestamp with time zone,
    CONSTRAINT fund_wallets_blockchain_check CHECK (((blockchain)::text = 'BSC'::text)),
    CONSTRAINT fund_wallets_wallet_type_check CHECK (((wallet_type)::text = 'settlement'::text))
);


--
-- TOC entry 251 (class 1259 OID 33244)
-- Name: fund_wallets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_wallets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5613 (class 0 OID 0)
-- Dependencies: 251
-- Name: fund_wallets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_wallets_id_seq OWNED BY public.fund_wallets.id;


--
-- TOC entry 217 (class 1259 OID 32859)
-- Name: funds; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.funds (
    id integer NOT NULL,
    code character varying(32) NOT NULL,
    name_ru character varying(100) NOT NULL,
    name_en character varying(100) NOT NULL,
    category character varying(16) NOT NULL,
    sort_order integer DEFAULT 0 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    short_name_ru character varying(100),
    short_name_en character varying(100),
    full_name_ru character varying(150),
    full_name_en character varying(150),
    benchmark_name_ru character varying(150),
    benchmark_name_en character varying(150),
    management_fee_pct numeric(10,4),
    performance_fee_pct numeric(10,4),
    icon_name character varying(100),
    launch_date date,
    shares_outstanding_current numeric(30,10) DEFAULT 0 NOT NULL
);


--
-- TOC entry 218 (class 1259 OID 32864)
-- Name: funds_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.funds_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5614 (class 0 OID 0)
-- Dependencies: 218
-- Name: funds_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.funds_id_seq OWNED BY public.funds.id;


--
-- TOC entry 219 (class 1259 OID 32865)
-- Name: password_reset_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.password_reset_sessions (
    id character varying(64) NOT NULL,
    user_id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    is_used boolean DEFAULT false NOT NULL
);


--
-- TOC entry 220 (class 1259 OID 32870)
-- Name: security_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.security_codes (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    purpose character varying(32) NOT NULL,
    code character varying(16) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    is_used boolean DEFAULT false NOT NULL,
    attempts smallint DEFAULT 0 NOT NULL,
    CONSTRAINT security_codes_purpose_check CHECK (((purpose)::text = ANY ((ARRAY['registration'::character varying, 'reset'::character varying, 'login_2fa'::character varying, 'password_change'::character varying, 'withdraw'::character varying])::text[])))
);


--
-- TOC entry 221 (class 1259 OID 32877)
-- Name: security_codes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.security_codes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5615 (class 0 OID 0)
-- Dependencies: 221
-- Name: security_codes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.security_codes_id_seq OWNED BY public.security_codes.id;


--
-- TOC entry 222 (class 1259 OID 32878)
-- Name: sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sessions (
    id character varying(255) NOT NULL,
    user_id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


--
-- TOC entry 239 (class 1259 OID 33100)
-- Name: user_fund_position_stats; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_fund_position_stats (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    fund_id integer NOT NULL,
    avg_entry_price_usdt numeric(30,10) DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- TOC entry 238 (class 1259 OID 33099)
-- Name: user_fund_position_stats_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_fund_position_stats_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5616 (class 0 OID 0)
-- Dependencies: 238
-- Name: user_fund_position_stats_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_fund_position_stats_id_seq OWNED BY public.user_fund_position_stats.id;


--
-- TOC entry 223 (class 1259 OID 32882)
-- Name: user_fund_positions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_fund_positions (
    id bigint NOT NULL,
    user_id integer NOT NULL,
    fund_id integer NOT NULL,
    shares numeric(30,10) DEFAULT 0 NOT NULL,
    shares_reserved numeric(30,10) DEFAULT 0 NOT NULL,
    CONSTRAINT user_fund_positions_shares_nonnegative_check CHECK ((shares >= (0)::numeric)),
    CONSTRAINT user_fund_positions_shares_reserved_nonnegative_check CHECK ((shares_reserved >= (0)::numeric))
);


--
-- TOC entry 224 (class 1259 OID 32886)
-- Name: user_fund_positions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_fund_positions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5617 (class 0 OID 0)
-- Dependencies: 224
-- Name: user_fund_positions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_fund_positions_id_seq OWNED BY public.user_fund_positions.id;


--
-- TOC entry 225 (class 1259 OID 32887)
-- Name: user_portfolio_daily; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_portfolio_daily (
    id bigint NOT NULL,
    user_id integer NOT NULL,
    date_utc date NOT NULL,
    balance_usdt numeric(30,10) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- TOC entry 226 (class 1259 OID 32891)
-- Name: user_portfolio_daily_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_portfolio_daily_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5618 (class 0 OID 0)
-- Dependencies: 226
-- Name: user_portfolio_daily_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_portfolio_daily_id_seq OWNED BY public.user_portfolio_daily.id;


--
-- TOC entry 245 (class 1259 OID 33174)
-- Name: user_totp_recovery_codes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_totp_recovery_codes (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    code_hash character varying(255) NOT NULL,
    is_used boolean DEFAULT false NOT NULL,
    used_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- TOC entry 244 (class 1259 OID 33173)
-- Name: user_totp_recovery_codes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_totp_recovery_codes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5619 (class 0 OID 0)
-- Dependencies: 244
-- Name: user_totp_recovery_codes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_totp_recovery_codes_id_seq OWNED BY public.user_totp_recovery_codes.id;


--
-- TOC entry 227 (class 1259 OID 32892)
-- Name: user_wallets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_wallets (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    blockchain character varying(32) DEFAULT 'BSC'::character varying NOT NULL,
    address character varying(64) NOT NULL,
    encrypted_private_key text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    usdt_balance numeric(38,18) DEFAULT 0 NOT NULL,
    usdt_balance_updated_at timestamp with time zone,
    usdt_balance_block bigint,
    usdt_reserved numeric(38,18) DEFAULT 0 NOT NULL,
    compliance_status character varying(32) DEFAULT 'ok'::character varying NOT NULL,
    freeze_reason text,
    compliance_checked_at timestamp with time zone,
    is_active boolean DEFAULT true NOT NULL,
    archived_at timestamp with time zone,
    CONSTRAINT user_wallets_compliance_status_check CHECK (((compliance_status)::text = ANY (ARRAY[('ok'::character varying)::text, ('blocked'::character varying)::text, ('pending_check'::character varying)::text])))
);


--
-- TOC entry 228 (class 1259 OID 32903)
-- Name: user_wallets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_wallets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5620 (class 0 OID 0)
-- Dependencies: 228
-- Name: user_wallets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_wallets_id_seq OWNED BY public.user_wallets.id;


--
-- TOC entry 229 (class 1259 OID 32904)
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    email character varying(255) NOT NULL,
    first_name character varying(100) NOT NULL,
    last_name character varying(100) NOT NULL,
    phone character varying(32),
    password_hash character varying(255) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    is_email_verified boolean DEFAULT false NOT NULL,
    two_factor_enabled boolean DEFAULT true NOT NULL,
    account_type character varying(16) DEFAULT 'basic'::character varying NOT NULL,
    backup_email character varying(255),
    is_backup_email_verified boolean DEFAULT false NOT NULL,
    compliance_status character varying(32) DEFAULT 'ok'::character varying NOT NULL,
    compliance_reason text,
    compliance_updated_at timestamp with time zone,
    non_us_citizen_confirmed boolean DEFAULT false NOT NULL,
    non_us_citizen_confirmed_at timestamp with time zone,
    totp_enabled boolean DEFAULT false NOT NULL,
    totp_secret_encrypted text,
    totp_confirmed_at timestamp with time zone,
    totp_last_used_step bigint,
    cookie_notice_acknowledged boolean DEFAULT false NOT NULL,
    cookie_notice_acknowledged_at timestamp with time zone,
    CONSTRAINT users_account_type_check CHECK (((account_type)::text = ANY ((ARRAY['basic'::character varying, 'vip'::character varying, 'manager'::character varying, 'employee'::character varying, 'employee2'::character varying, 'ai_agent'::character varying, 'tester'::character varying])::text[]))),
    CONSTRAINT users_compliance_status_check CHECK (((compliance_status)::text = ANY (ARRAY[('ok'::character varying)::text, ('blocked'::character varying)::text, ('pending_check'::character varying)::text])))
);


--
-- TOC entry 230 (class 1259 OID 32918)
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5621 (class 0 OID 0)
-- Dependencies: 230
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- TOC entry 231 (class 1259 OID 32919)
-- Name: wallet_transfers; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.wallet_transfers (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    wallet_id bigint NOT NULL,
    coin character varying(16) DEFAULT 'USDT'::character varying NOT NULL,
    network character varying(32) DEFAULT 'BSC (BEP20)'::character varying NOT NULL,
    type character varying(16) NOT NULL,
    from_address character varying(64),
    to_address character varying(64),
    tx_hash character varying(80),
    log_index integer DEFAULT 0,
    amount numeric(38,18) DEFAULT 0 NOT NULL,
    status character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    compliance_status character varying(32),
    block_number bigint,
    tx_time timestamp with time zone,
    detected_at timestamp with time zone DEFAULT now() NOT NULL,
    confirmed_at timestamp with time zone,
    compliance_checked_at timestamp with time zone,
    compliance_details jsonb,
    amount_gross numeric(38,18),
    fee_usdt numeric(38,18) DEFAULT 1 NOT NULL,
    gas_tx_hash character varying(80),
    fee_tx_hash character varying(80),
    email_slot smallint,
    error text,
    CONSTRAINT wallet_transfers_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'processing'::character varying, 'success'::character varying, 'failed'::character varying])::text[])))
);


--
-- TOC entry 232 (class 1259 OID 32930)
-- Name: wallet_transfers_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.wallet_transfers_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5622 (class 0 OID 0)
-- Dependencies: 232
-- Name: wallet_transfers_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.wallet_transfers_id_seq OWNED BY public.wallet_transfers.id;


--
-- TOC entry 235 (class 1259 OID 33047)
-- Name: withdraw_sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.withdraw_sessions (
    id bigint NOT NULL,
    token character varying(64) NOT NULL,
    user_id bigint NOT NULL,
    wallet_id bigint NOT NULL,
    to_address character varying(64) NOT NULL,
    amount_gross numeric(38,18) NOT NULL,
    fee_usdt numeric(38,18) DEFAULT 1 NOT NULL,
    email_slot smallint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    used_at timestamp with time zone
);


--
-- TOC entry 234 (class 1259 OID 33046)
-- Name: withdraw_sessions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.withdraw_sessions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 5623 (class 0 OID 0)
-- Dependencies: 234
-- Name: withdraw_sessions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.withdraw_sessions_id_seq OWNED BY public.withdraw_sessions.id;


--
-- TOC entry 233 (class 1259 OID 33036)
-- Name: worker_cursors; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.worker_cursors (
    name text NOT NULL,
    last_block bigint NOT NULL,
    last_log_index integer DEFAULT 0 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- TOC entry 4922 (class 2604 OID 33193)
-- Name: fee_wallet_swaps id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fee_wallet_swaps ALTER COLUMN id SET DEFAULT nextval('public.fee_wallet_swaps_id_seq'::regclass);


--
-- TOC entry 4962 (class 2604 OID 33426)
-- Name: fund_allocation_batches id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_batches ALTER COLUMN id SET DEFAULT nextval('public.fund_allocation_batches_id_seq'::regclass);


--
-- TOC entry 4968 (class 2604 OID 33454)
-- Name: fund_allocation_legs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_legs ALTER COLUMN id SET DEFAULT nextval('public.fund_allocation_legs_id_seq'::regclass);


--
-- TOC entry 4956 (class 2604 OID 33366)
-- Name: fund_bybit_accounts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_bybit_accounts ALTER COLUMN id SET DEFAULT nextval('public.fund_bybit_accounts_id_seq'::regclass);


--
-- TOC entry 4917 (class 2604 OID 33124)
-- Name: fund_chart_daily id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_daily ALTER COLUMN id SET DEFAULT nextval('public.fund_chart_daily_id_seq'::regclass);


--
-- TOC entry 4918 (class 2604 OID 33139)
-- Name: fund_chart_minute id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_minute ALTER COLUMN id SET DEFAULT nextval('public.fund_chart_minute_id_seq'::regclass);


--
-- TOC entry 4930 (class 2604 OID 33224)
-- Name: fund_nav_guard_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_events ALTER COLUMN id SET DEFAULT nextval('public.fund_nav_guard_events_id_seq'::regclass);


--
-- TOC entry 4863 (class 2604 OID 32931)
-- Name: fund_nav_minute id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute ALTER COLUMN id SET DEFAULT nextval('public.fund_nav_minute_id_seq'::regclass);


--
-- TOC entry 4989 (class 2604 OID 33646)
-- Name: fund_negative_bybit_flows id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_bybit_flows ALTER COLUMN id SET DEFAULT nextval('public.fund_negative_bybit_flows_id_seq'::regclass);


--
-- TOC entry 5007 (class 2604 OID 33805)
-- Name: fund_negative_finalization_batches id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_finalization_batches ALTER COLUMN id SET DEFAULT nextval('public.fund_negative_finalization_batches_id_seq'::regclass);


--
-- TOC entry 4995 (class 2604 OID 33693)
-- Name: fund_negative_payout_batches id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_payout_batches ALTER COLUMN id SET DEFAULT nextval('public.fund_negative_payout_batches_id_seq'::regclass);


--
-- TOC entry 5001 (class 2604 OID 33739)
-- Name: fund_negative_payout_legs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_payout_legs ALTER COLUMN id SET DEFAULT nextval('public.fund_negative_payout_legs_id_seq'::regclass);


--
-- TOC entry 4979 (class 2604 OID 33554)
-- Name: fund_negative_sale_batches id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_batches ALTER COLUMN id SET DEFAULT nextval('public.fund_negative_sale_batches_id_seq'::regclass);


--
-- TOC entry 4983 (class 2604 OID 33578)
-- Name: fund_negative_sale_legs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_legs ALTER COLUMN id SET DEFAULT nextval('public.fund_negative_sale_legs_id_seq'::regclass);


--
-- TOC entry 5020 (class 2604 OID 33918)
-- Name: fund_operation_guard_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operation_guard_events ALTER COLUMN id SET DEFAULT nextval('public.fund_operation_guard_events_id_seq'::regclass);


--
-- TOC entry 5015 (class 2604 OID 33884)
-- Name: fund_operation_guard_overrides id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operation_guard_overrides ALTER COLUMN id SET DEFAULT nextval('public.fund_operation_guard_overrides_id_seq'::regclass);


--
-- TOC entry 5011 (class 2604 OID 33857)
-- Name: fund_operation_guard_state id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operation_guard_state ALTER COLUMN id SET DEFAULT nextval('public.fund_operation_guard_state_id_seq'::regclass);


--
-- TOC entry 4973 (class 2604 OID 33512)
-- Name: fund_operator_actions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operator_actions ALTER COLUMN id SET DEFAULT nextval('public.fund_operator_actions_id_seq'::regclass);


--
-- TOC entry 4911 (class 2604 OID 33081)
-- Name: fund_orders id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders ALTER COLUMN id SET DEFAULT nextval('public.fund_orders_id_seq'::regclass);


--
-- TOC entry 4937 (class 2604 OID 33271)
-- Name: fund_settlement_batches id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_batches ALTER COLUMN id SET DEFAULT nextval('public.fund_settlement_batches_id_seq'::regclass);


--
-- TOC entry 4948 (class 2604 OID 33308)
-- Name: fund_settlement_transfers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers ALTER COLUMN id SET DEFAULT nextval('public.fund_settlement_transfers_id_seq'::regclass);


--
-- TOC entry 4932 (class 2604 OID 33248)
-- Name: fund_wallets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_wallets ALTER COLUMN id SET DEFAULT nextval('public.fund_wallets_id_seq'::regclass);


--
-- TOC entry 4864 (class 2604 OID 32932)
-- Name: funds id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funds ALTER COLUMN id SET DEFAULT nextval('public.funds_id_seq'::regclass);


--
-- TOC entry 4870 (class 2604 OID 32933)
-- Name: security_codes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_codes ALTER COLUMN id SET DEFAULT nextval('public.security_codes_id_seq'::regclass);


--
-- TOC entry 4914 (class 2604 OID 33103)
-- Name: user_fund_position_stats id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats ALTER COLUMN id SET DEFAULT nextval('public.user_fund_position_stats_id_seq'::regclass);


--
-- TOC entry 4875 (class 2604 OID 32934)
-- Name: user_fund_positions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions ALTER COLUMN id SET DEFAULT nextval('public.user_fund_positions_id_seq'::regclass);


--
-- TOC entry 4878 (class 2604 OID 32935)
-- Name: user_portfolio_daily id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily ALTER COLUMN id SET DEFAULT nextval('public.user_portfolio_daily_id_seq'::regclass);


--
-- TOC entry 4919 (class 2604 OID 33177)
-- Name: user_totp_recovery_codes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_totp_recovery_codes ALTER COLUMN id SET DEFAULT nextval('public.user_totp_recovery_codes_id_seq'::regclass);


--
-- TOC entry 4880 (class 2604 OID 32936)
-- Name: user_wallets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wallets ALTER COLUMN id SET DEFAULT nextval('public.user_wallets_id_seq'::regclass);


--
-- TOC entry 4887 (class 2604 OID 32937)
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- TOC entry 4898 (class 2604 OID 32938)
-- Name: wallet_transfers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers ALTER COLUMN id SET DEFAULT nextval('public.wallet_transfers_id_seq'::regclass);


--
-- TOC entry 4908 (class 2604 OID 33050)
-- Name: withdraw_sessions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions ALTER COLUMN id SET DEFAULT nextval('public.withdraw_sessions_id_seq'::regclass);


--
-- TOC entry 5549 (class 0 OID 33190)
-- Dependencies: 247
-- Data for Name: fee_wallet_swaps; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fee_wallet_swaps (id, wallet_type, wallet_address, token_in, token_out, amount_in_usdt, amount_out_bnb, tx_hash, status, error, created_at, executed_at) FROM stdin;
\.


--
-- TOC entry 5563 (class 0 OID 33423)
-- Dependencies: 261
-- Data for Name: fund_allocation_batches; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_allocation_batches (id, settlement_batch_id, fund_id, snapshot_ts, positive_net_usdt, settlement_nav_usdt, snapshot_total_equity_usdt, base_nav_for_scale_usdt, scale, snapshot_source, snapshot_json, status, error, created_at, updated_at, completed_at, report_json, allocation_started_at, reconciliation_started_at, reconciliation_completed_at, alert_sent_at, total_legs_count, filled_legs_count, skipped_legs_count, partial_legs_count, failed_legs_count, active_legs_count, total_target_usdt, total_filled_usdt, total_residual_usdt, residual_earn_usdt, residual_cash_usdt) FROM stdin;
\.


--
-- TOC entry 5565 (class 0 OID 33451)
-- Dependencies: 263
-- Data for Name: fund_allocation_legs; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_allocation_legs (id, allocation_batch_id, settlement_batch_id, fund_id, parent_leg_id, leg_index, leg_key, leg_group, leg_type, coin, symbol, category, side, location, current_size, current_usd_value, current_notional_usd, source_weight, target_usdt, target_qty, execution_mode, planned_suborders, executed_suborders, order_link_id, bybit_order_id, strategy_id, earn_order_id, transfer_id, last_price, best_bid, best_ask, corridor_pct, available_liquidity_qty, available_liquidity_usdt, required_qty, required_usdt, filled_qty, filled_usdt, avg_fill_price, fill_ratio, fee_usdt, actual_cash_used_usdt, actual_margin_change_usdt, residual_usdt, status, error, created_at, updated_at, sent_at, confirmed_at, earn_product_id, earn_product_category, earn_product_status, earn_min_stake_amount, earn_max_stake_amount, earn_precision, staked_qty, staked_usdt, stake_status, account_im_rate_before, account_mm_rate_before, account_im_rate_after_est, account_mm_rate_after_est, total_equity_usdt_before, total_initial_margin_usdt_before, total_maintenance_margin_usdt_before, estimated_initial_margin_change_usdt, estimated_maintenance_margin_change_usdt, margin_guard_status, margin_guard_error) FROM stdin;
\.


--
-- TOC entry 5561 (class 0 OID 33363)
-- Dependencies: 259
-- Data for Name: fund_bybit_accounts; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_bybit_accounts (id, fund_id, bybit_sub_uid, bybit_subaccount_name, coin, chain, chain_type, deposit_address, deposit_tag, is_active, last_verified_at, created_at, updated_at, api_key_encrypted, api_secret_encrypted, api_permissions, api_ip_whitelist, api_key_label, api_key_added_at, api_key_last_verified_at, api_key_is_active) FROM stdin;
2	5	142357168	WildBoarBTC	USDT	BSC	BSC	0xcccb1458e09590105f811bd69fce7e39af4de66b	\N	t	2026-05-23 16:41:15.742403+03	2026-05-23 16:41:15.742403+03	2026-05-23 16:41:15.742403+03	\N	\N	\N	\N	\N	\N	\N	f
3	4	142361148	WildBoarDeFiS	USDT	BSC	BSC	0xffceb92ac02214ef029238ee0429c87ffe284b42	\N	t	2026-05-23 16:41:22.750063+03	2026-05-23 16:41:22.750063+03	2026-05-23 16:41:22.750063+03	\N	\N	\N	\N	\N	\N	\N	f
4	6	141159819	WildBoar10	USDT	BSC	BSC	0x7fce782ef975e70a7a918d5abb4730ee1da336fa	\N	t	2026-05-23 16:41:29.688222+03	2026-05-23 16:41:29.688222+03	2026-05-23 16:41:29.688222+03	\N	\N	\N	\N	\N	\N	\N	f
5	7	142358629	WildBoarDeFi	USDT	BSC	BSC	0xc2e31400bc07b60dd0ad0f7b7ecb8e627300e9e3	\N	t	2026-05-23 16:41:36.506445+03	2026-05-23 16:41:36.506445+03	2026-05-23 16:41:36.506445+03	\N	\N	\N	\N	\N	\N	\N	f
6	9	559127559	Bybit9D44TLhO3bc	USDT	BSC	BSC	0xbc08180b5a7f7bc47b6c403544e28df8e98f7ba7	\N	t	2026-05-23 16:41:43.562663+03	2026-05-23 16:41:43.562663+03	2026-05-23 16:41:43.562663+03	\N	\N	\N	\N	\N	\N	\N	f
7	8	142359498	WildBoarWeb30	USDT	BSC	BSC	0xb342309f5492dacc0b9bb50c777dc6a08e2257a3	\N	t	2026-05-23 16:41:50.740369+03	2026-05-23 16:41:50.740369+03	2026-05-23 16:41:50.740369+03	\N	\N	\N	\N	\N	\N	\N	f
\.


--
-- TOC entry 5543 (class 0 OID 33121)
-- Dependencies: 241
-- Data for Name: fund_chart_daily; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_chart_daily (id, fund_id, ts_utc, open, high, low, close, volume) FROM stdin;
3965	4	2026-05-12 03:00:00+03	7893.9700000000	7899.1700000000	7611.4300000000	7718.1500000000	\N
3966	4	2026-05-13 03:00:00+03	7718.1500000000	7862.2000000000	7451.1900000000	7517.7700000000	\N
3967	4	2026-05-14 03:00:00+03	7517.7700000000	7745.1700000000	7420.5700000000	7624.0600000000	\N
3968	4	2026-05-15 03:00:00+03	7624.0600000000	7659.2900000000	7260.9500000000	7344.0400000000	\N
3969	4	2026-05-16 03:00:00+03	7344.0400000000	7370.7500000000	7081.6900000000	7150.8800000000	\N
3970	5	2026-05-12 03:00:00+03	14841.6700000000	14847.5800000000	14505.0800000000	14620.3100000000	\N
3971	5	2026-05-13 03:00:00+03	14620.3100000000	14761.8600000000	14307.9400000000	14407.2800000000	\N
3972	5	2026-05-14 03:00:00+03	14407.2800000000	14896.4400000000	14337.6500000000	14724.9100000000	\N
3973	5	2026-05-15 03:00:00+03	14724.9100000000	14825.0100000000	14291.8000000000	14368.7800000000	\N
3974	5	2026-05-16 03:00:00+03	14368.7800000000	14390.7300000000	14113.7500000000	14178.4200000000	\N
3975	6	2026-05-12 03:00:00+03	4648.6500000000	4653.7600000000	4488.9400000000	4549.6300000000	\N
3976	6	2026-05-13 03:00:00+03	4549.6300000000	4645.1100000000	4477.9800000000	4524.8200000000	\N
3977	6	2026-05-14 03:00:00+03	4524.8200000000	4726.0900000000	4492.7000000000	4637.8800000000	\N
3978	6	2026-05-15 03:00:00+03	4637.8800000000	4680.0700000000	4508.5200000000	4536.9300000000	\N
3979	6	2026-05-16 03:00:00+03	4536.9300000000	4546.7600000000	4392.7000000000	4420.8100000000	\N
3980	9	2026-05-12 03:00:00+03	683.8000000000	689.0300000000	679.1600000000	684.3400000000	\N
3981	9	2026-05-13 03:00:00+03	684.3400000000	695.0400000000	673.2300000000	678.0900000000	\N
3982	9	2026-05-14 03:00:00+03	678.0900000000	699.7100000000	672.9900000000	687.3300000000	\N
3983	9	2026-05-15 03:00:00+03	687.3300000000	692.5400000000	663.5100000000	665.0900000000	\N
3984	9	2026-05-16 03:00:00+03	665.0900000000	666.6000000000	652.0900000000	656.3900000000	\N
661	4	2024-05-08 03:00:00+03	9999.4800000000	9999.4800000000	9996.5800000000	9996.5800000000	\N
662	4	2024-05-09 03:00:00+03	9996.5800000000	10003.5400000000	9972.6200000000	10003.5400000000	\N
663	4	2024-05-10 03:00:00+03	10003.5400000000	10022.3900000000	9969.2600000000	9969.2600000000	\N
664	4	2024-05-11 03:00:00+03	9969.2600000000	9973.7600000000	9963.7900000000	9967.6700000000	\N
665	4	2024-05-12 03:00:00+03	9967.6700000000	9969.6700000000	9958.4800000000	9962.0700000000	\N
666	4	2024-05-13 03:00:00+03	9962.0700000000	9973.2100000000	9930.9500000000	9956.3900000000	\N
667	4	2024-05-14 03:00:00+03	9956.3900000000	9950.1700000000	9924.3400000000	9928.5400000000	\N
668	4	2024-05-15 03:00:00+03	9928.5400000000	9999.1400000000	9919.7800000000	9999.1400000000	\N
669	4	2024-05-16 03:00:00+03	9999.1400000000	9999.2200000000	9974.4500000000	9986.7200000000	\N
670	4	2024-05-17 03:00:00+03	9986.7200000000	10042.8500000000	9990.3000000000	10038.4300000000	\N
671	4	2024-05-18 03:00:00+03	10038.4300000000	10046.4900000000	10034.9800000000	10039.5700000000	\N
672	4	2024-05-19 03:00:00+03	10039.5700000000	10058.1200000000	9854.5900000000	9886.0300000000	\N
673	4	2024-05-20 03:00:00+03	9886.0300000000	10205.9400000000	9850.1000000000	10205.9400000000	\N
674	4	2024-05-21 03:00:00+03	10205.9400000000	10390.3000000000	10300.5300000000	10326.1800000000	\N
675	4	2024-05-22 03:00:00+03	10326.1800000000	10379.2800000000	10183.4600000000	10245.8800000000	\N
676	4	2024-05-23 03:00:00+03	10245.8800000000	10318.9400000000	10061.3700000000	10061.3700000000	\N
677	4	2024-05-24 03:00:00+03	10061.3700000000	10198.3400000000	10075.2200000000	10197.5000000000	\N
678	4	2024-05-25 03:00:00+03	10197.5000000000	10271.4000000000	10172.2100000000	10247.7000000000	\N
679	4	2024-05-26 03:00:00+03	10247.7000000000	10269.2800000000	10198.9700000000	10203.2500000000	\N
680	4	2024-05-27 03:00:00+03	10203.2500000000	10329.5400000000	10178.3700000000	10264.8800000000	\N
681	4	2024-05-28 03:00:00+03	10264.8800000000	10287.7300000000	10139.9800000000	10201.8700000000	\N
682	4	2024-05-29 03:00:00+03	10201.8700000000	10225.1300000000	10115.5700000000	10115.5700000000	\N
683	4	2024-05-30 03:00:00+03	10115.5700000000	10170.8600000000	10031.2900000000	10124.4300000000	\N
684	4	2024-05-31 03:00:00+03	10124.4300000000	10122.8300000000	10002.7800000000	10032.0900000000	\N
685	4	2024-06-01 03:00:00+03	10032.0900000000	10037.2600000000	10008.9300000000	10030.3400000000	\N
686	4	2024-06-02 03:00:00+03	10030.3400000000	10054.8800000000	9965.8700000000	9967.7300000000	\N
687	4	2024-06-03 03:00:00+03	9967.7300000000	10074.8600000000	9952.5200000000	10066.9200000000	\N
688	4	2024-06-04 03:00:00+03	10066.9200000000	10167.1700000000	9972.2400000000	10167.1700000000	\N
689	4	2024-06-05 03:00:00+03	10167.1700000000	10191.9600000000	10138.9900000000	10191.9600000000	\N
690	4	2024-06-06 03:00:00+03	10191.9600000000	10185.8400000000	10115.0800000000	10115.0800000000	\N
691	4	2024-06-07 03:00:00+03	10115.0800000000	10213.3400000000	9865.8300000000	9886.1300000000	\N
692	4	2024-06-08 03:00:00+03	9886.1300000000	9916.5500000000	9803.3900000000	9803.3900000000	\N
693	4	2024-06-09 03:00:00+03	9803.3900000000	9860.7100000000	9791.6700000000	9860.7100000000	\N
694	4	2024-06-10 03:00:00+03	9860.7100000000	9897.3700000000	9771.3500000000	9838.7500000000	\N
695	4	2024-06-11 03:00:00+03	9838.7500000000	9829.7000000000	9613.9500000000	9656.9900000000	\N
696	4	2024-06-12 03:00:00+03	9656.9900000000	9857.9000000000	9619.7100000000	9764.6400000000	\N
1332	6	2024-05-26 03:00:00+03	5000.0000000000	4979.3000000000	4915.9900000000	4924.1700000000	\N
1333	6	2024-05-27 03:00:00+03	4924.1700000000	5075.4500000000	4919.8400000000	4987.9700000000	\N
1334	6	2024-05-28 03:00:00+03	4987.9700000000	5027.9700000000	4907.7000000000	5047.1500000000	\N
1335	6	2024-05-29 03:00:00+03	5047.1500000000	5055.5000000000	4961.1900000000	4961.7900000000	\N
1336	6	2024-05-30 03:00:00+03	4961.7900000000	4991.6000000000	4861.5200000000	4938.6400000000	\N
1337	6	2024-05-31 03:00:00+03	4938.6400000000	4947.4800000000	4848.9400000000	4884.3900000000	\N
1338	6	2024-06-01 03:00:00+03	4884.3900000000	4906.1800000000	4873.1700000000	4903.3600000000	\N
1339	6	2024-06-02 03:00:00+03	4903.3600000000	4923.5800000000	4847.5900000000	4860.6000000000	\N
1340	6	2024-06-03 03:00:00+03	4860.6000000000	5021.6300000000	4878.5900000000	4997.7800000000	\N
1341	6	2024-06-04 03:00:00+03	4997.7800000000	5145.4200000000	4950.4600000000	5145.4200000000	\N
1342	6	2024-06-05 03:00:00+03	5145.4200000000	5313.8800000000	5259.3500000000	5274.1500000000	\N
1343	6	2024-06-06 03:00:00+03	5274.1500000000	5322.2800000000	5236.9600000000	5236.9600000000	\N
1344	6	2024-06-07 03:00:00+03	5236.9600000000	5301.7900000000	4987.6400000000	5016.9100000000	\N
1345	6	2024-06-08 03:00:00+03	5016.9100000000	5082.5500000000	4973.2200000000	4978.7500000000	\N
1346	6	2024-06-09 03:00:00+03	4978.7500000000	5012.4700000000	4917.0700000000	5002.8200000000	\N
1347	6	2024-06-10 03:00:00+03	5002.8200000000	4993.6600000000	4841.6300000000	4907.2100000000	\N
1348	6	2024-06-11 03:00:00+03	4907.2100000000	4842.3800000000	4593.4400000000	4653.8200000000	\N
1349	6	2024-06-12 03:00:00+03	4653.8200000000	4896.3700000000	4594.9100000000	4755.8300000000	\N
1350	6	2024-06-13 03:00:00+03	4755.8300000000	4819.5700000000	4628.7800000000	4669.2100000000	\N
1351	6	2024-06-14 03:00:00+03	4669.2100000000	4722.7100000000	4545.5300000000	4552.5200000000	\N
1352	6	2024-06-15 03:00:00+03	4552.5200000000	4655.1200000000	4603.3600000000	4650.6200000000	\N
1	5	2024-05-19 03:00:00+03	10000.0000000000	9964.6300000000	9938.6700000000	9938.6700000000	\N
2	5	2024-05-20 03:00:00+03	9938.6700000000	10257.6800000000	9939.0700000000	10257.6800000000	\N
3	5	2024-05-21 03:00:00+03	10257.6800000000	10340.7200000000	10195.3200000000	10195.3200000000	\N
4	5	2024-05-22 03:00:00+03	10195.3200000000	10269.8100000000	10215.7200000000	10207.0000000000	\N
5	5	2024-05-23 03:00:00+03	10207.0000000000	10235.6900000000	10054.2800000000	10054.2800000000	\N
6	5	2024-05-24 03:00:00+03	10054.2800000000	10191.8800000000	10045.1900000000	10191.8800000000	\N
7	5	2024-05-25 03:00:00+03	10191.8800000000	10203.5200000000	10149.2400000000	10189.8000000000	\N
8	5	2024-05-26 03:00:00+03	10189.8000000000	10197.4200000000	10162.2900000000	10168.1500000000	\N
9	5	2024-05-27 03:00:00+03	10168.1500000000	10257.9900000000	10138.1200000000	10189.5300000000	\N
10	5	2024-05-28 03:00:00+03	10189.5300000000	10218.9700000000	10088.6400000000	10131.3800000000	\N
11	5	2024-05-29 03:00:00+03	10131.3800000000	10158.4700000000	10061.9000000000	10061.9000000000	\N
12	5	2024-05-30 03:00:00+03	10061.9000000000	10179.9100000000	10081.5600000000	10142.8900000000	\N
13	5	2024-05-31 03:00:00+03	10142.8900000000	10136.9700000000	10049.9800000000	10069.9600000000	\N
14	5	2024-06-01 03:00:00+03	10069.9600000000	10081.4600000000	10066.1500000000	10079.2800000000	\N
15	5	2024-06-02 03:00:00+03	10079.2800000000	10106.5800000000	10069.5900000000	10080.1500000000	\N
16	5	2024-06-03 03:00:00+03	10080.1500000000	10200.3000000000	10083.8100000000	10162.3900000000	\N
17	5	2024-06-04 03:00:00+03	10162.3900000000	10250.7500000000	10133.6400000000	10242.2700000000	\N
18	5	2024-06-05 03:00:00+03	10242.2700000000	10287.8100000000	10233.9500000000	10272.8000000000	\N
19	5	2024-06-06 03:00:00+03	10272.8000000000	10276.9100000000	10224.0500000000	10224.0500000000	\N
20	5	2024-06-07 03:00:00+03	10224.0500000000	10286.2600000000	10146.5200000000	10157.1200000000	\N
21	5	2024-06-08 03:00:00+03	10157.1200000000	10175.2800000000	10157.9200000000	10174.2500000000	\N
22	5	2024-06-09 03:00:00+03	10174.2500000000	10187.9300000000	10160.2200000000	10187.8100000000	\N
23	5	2024-06-10 03:00:00+03	10187.8100000000	10204.1100000000	10164.5200000000	10172.8700000000	\N
24	5	2024-06-11 03:00:00+03	10172.8700000000	10178.1500000000	10003.3600000000	10066.2300000000	\N
25	5	2024-06-12 03:00:00+03	10066.2300000000	10203.2000000000	10056.6200000000	10076.9100000000	\N
26	5	2024-06-13 03:00:00+03	10076.9100000000	10123.9400000000	10015.9000000000	10022.2800000000	\N
27	5	2024-06-14 03:00:00+03	10022.2800000000	10047.8200000000	9953.3200000000	9965.2700000000	\N
28	5	2024-06-15 03:00:00+03	9965.2700000000	10008.0000000000	9991.1900000000	9999.6500000000	\N
29	5	2024-06-16 03:00:00+03	9999.6500000000	10030.5000000000	9997.6400000000	10017.9100000000	\N
30	5	2024-06-17 03:00:00+03	10017.9100000000	10043.0400000000	9946.9600000000	10026.0100000000	\N
31	5	2024-06-18 03:00:00+03	10026.0100000000	10025.9700000000	9928.0200000000	9928.0200000000	\N
32	5	2024-06-19 03:00:00+03	9928.0200000000	9983.7400000000	9951.4800000000	9951.4800000000	\N
33	5	2024-06-20 03:00:00+03	9951.4800000000	10009.0800000000	9945.5800000000	9957.3400000000	\N
34	5	2024-06-21 03:00:00+03	9957.3400000000	9955.6500000000	9894.3700000000	9921.4400000000	\N
35	5	2024-06-22 03:00:00+03	9921.4400000000	9935.3300000000	9915.5400000000	9927.8900000000	\N
36	5	2024-06-23 03:00:00+03	9927.8900000000	9933.0100000000	9916.8900000000	9919.9000000000	\N
37	5	2024-06-24 03:00:00+03	9919.9000000000	9912.0600000000	9790.5700000000	9790.5700000000	\N
38	5	2024-06-25 03:00:00+03	9790.5700000000	9870.1300000000	9810.5700000000	9865.4200000000	\N
39	5	2024-06-26 03:00:00+03	9865.4200000000	9868.4400000000	9833.1100000000	9833.1100000000	\N
40	5	2024-06-27 03:00:00+03	9833.1100000000	9861.5600000000	9825.0900000000	9840.0800000000	\N
41	5	2024-06-28 03:00:00+03	9840.0800000000	9861.4500000000	9837.5600000000	9793.6600000000	\N
42	5	2024-06-29 03:00:00+03	9793.6600000000	9832.1700000000	9803.2400000000	9830.3500000000	\N
43	5	2024-06-30 03:00:00+03	9830.3500000000	9845.8400000000	9820.7800000000	9845.8400000000	\N
44	5	2024-07-01 03:00:00+03	9845.8400000000	9893.9000000000	9843.7200000000	9882.8600000000	\N
45	5	2024-07-02 03:00:00+03	9882.8600000000	9879.4300000000	9842.1900000000	9842.1900000000	\N
46	5	2024-07-03 03:00:00+03	9842.1900000000	9842.1900000000	9786.4500000000	9786.4500000000	\N
47	5	2024-07-04 03:00:00+03	9786.4500000000	9768.2900000000	9726.0600000000	9753.8500000000	\N
48	5	2024-07-05 03:00:00+03	9753.8500000000	9894.5700000000	9618.4000000000	9869.2300000000	\N
49	5	2024-07-06 03:00:00+03	9869.2300000000	10029.0700000000	9829.9300000000	10017.7700000000	\N
50	5	2024-07-07 03:00:00+03	10017.7700000000	10070.5800000000	9900.6500000000	9940.2500000000	\N
51	5	2024-07-08 03:00:00+03	9940.2500000000	10009.2700000000	9709.6000000000	9793.2100000000	\N
52	5	2024-07-09 03:00:00+03	9793.2100000000	10023.5300000000	9939.6500000000	10016.6200000000	\N
53	5	2024-07-10 03:00:00+03	10016.6200000000	10174.9000000000	9966.4900000000	9966.4900000000	\N
54	5	2024-07-11 03:00:00+03	9966.4900000000	10114.5900000000	9966.3000000000	9966.3000000000	\N
55	5	2024-07-12 03:00:00+03	9966.3000000000	10067.1000000000	9917.6700000000	10067.1000000000	\N
56	5	2024-07-13 03:00:00+03	10067.1000000000	10122.7100000000	9991.2000000000	10098.2300000000	\N
57	5	2024-07-14 03:00:00+03	10098.2300000000	10273.3600000000	10105.3200000000	10236.0200000000	\N
58	5	2024-07-15 03:00:00+03	10236.0200000000	10622.8900000000	10327.5100000000	10622.8900000000	\N
59	5	2024-07-16 03:00:00+03	10622.8900000000	10786.7400000000	10541.2400000000	10786.7400000000	\N
60	5	2024-07-17 03:00:00+03	10786.7400000000	10786.7400000000	10717.0100000000	10717.0100000000	\N
61	5	2024-07-18 03:00:00+03	10717.0100000000	10766.8800000000	10609.6300000000	10611.4400000000	\N
62	5	2024-07-19 03:00:00+03	10611.4400000000	11003.4900000000	10615.3500000000	11003.4900000000	\N
63	5	2024-07-20 03:00:00+03	11003.4900000000	11032.9600000000	10915.7500000000	11007.1000000000	\N
64	5	2024-07-21 03:00:00+03	11007.1000000000	11042.2300000000	10942.5700000000	11027.0400000000	\N
65	5	2024-07-22 03:00:00+03	11027.0400000000	11113.2000000000	10946.9000000000	11113.2000000000	\N
66	5	2024-07-23 03:00:00+03	11113.2000000000	11067.0200000000	10823.4900000000	10823.4900000000	\N
67	5	2024-07-24 03:00:00+03	10823.4900000000	10941.5300000000	10793.9200000000	10793.9200000000	\N
68	5	2024-07-25 03:00:00+03	10793.9200000000	10791.0800000000	10565.0800000000	10660.2700000000	\N
69	5	2024-07-26 03:00:00+03	10660.2700000000	11109.3800000000	10788.5500000000	11109.3800000000	\N
70	5	2024-07-27 03:00:00+03	11109.3800000000	11281.2800000000	11065.0400000000	11144.5000000000	\N
71	5	2024-07-28 03:00:00+03	11144.5000000000	11222.7200000000	11021.5800000000	11128.6400000000	\N
72	5	2024-07-29 03:00:00+03	11128.6400000000	11352.8700000000	10962.2300000000	11013.2200000000	\N
73	5	2024-07-30 03:00:00+03	11013.2200000000	11035.9100000000	10807.3400000000	10861.1800000000	\N
74	5	2024-07-31 03:00:00+03	10861.1800000000	10902.0400000000	10622.0300000000	10799.3800000000	\N
75	5	2024-08-01 03:00:00+03	10799.3800000000	10587.4000000000	10302.3500000000	10370.4500000000	\N
76	5	2024-08-02 03:00:00+03	10370.4500000000	10634.4600000000	10264.5700000000	10264.5700000000	\N
77	5	2024-08-03 03:00:00+03	10264.5700000000	10187.5100000000	9947.8200000000	9948.6300000000	\N
78	5	2024-08-04 03:00:00+03	9948.6300000000	10060.6900000000	9626.7400000000	9818.4700000000	\N
79	5	2024-08-05 03:00:00+03	9818.4700000000	9745.8500000000	8555.5500000000	9039.1400000000	\N
80	5	2024-08-06 03:00:00+03	9039.1400000000	9323.7900000000	9077.9400000000	9319.0200000000	\N
81	5	2024-08-07 03:00:00+03	9319.0200000000	9377.1700000000	9136.9000000000	9136.9000000000	\N
82	5	2024-08-08 03:00:00+03	9136.9000000000	9575.8900000000	9172.5500000000	9561.7200000000	\N
83	5	2024-08-09 03:00:00+03	9561.7200000000	9822.1100000000	9594.5100000000	9671.0000000000	\N
84	5	2024-08-10 03:00:00+03	9671.0000000000	9701.6700000000	9643.6500000000	9701.6700000000	\N
85	5	2024-08-11 03:00:00+03	9701.6700000000	9724.0500000000	9577.7000000000	9577.7000000000	\N
86	5	2024-08-12 03:00:00+03	9577.7000000000	9637.2400000000	9446.4700000000	9522.4400000000	\N
87	5	2024-08-13 03:00:00+03	9522.4400000000	9720.0600000000	9492.1000000000	9684.3500000000	\N
88	5	2024-08-14 03:00:00+03	9684.3500000000	9728.7500000000	9513.8800000000	9513.8800000000	\N
89	5	2024-08-15 03:00:00+03	9513.8800000000	9583.9000000000	9344.2500000000	9344.2500000000	\N
90	5	2024-08-16 03:00:00+03	9344.2500000000	9585.1900000000	9382.6000000000	9585.1900000000	\N
91	5	2024-08-17 03:00:00+03	9585.1900000000	9573.7300000000	9509.8600000000	9562.3100000000	\N
92	5	2024-08-18 03:00:00+03	9562.3100000000	9622.0900000000	9547.4000000000	9583.8300000000	\N
93	5	2024-08-19 03:00:00+03	9583.8300000000	9560.2500000000	9430.3800000000	9530.3300000000	\N
94	5	2024-08-20 03:00:00+03	9530.3300000000	9716.6500000000	9502.0600000000	9563.5100000000	\N
95	5	2024-08-21 03:00:00+03	9563.5100000000	9759.0400000000	9519.9000000000	9759.0400000000	\N
96	5	2024-08-22 03:00:00+03	9759.0400000000	9733.4100000000	9623.7200000000	9623.7200000000	\N
97	5	2024-08-23 03:00:00+03	9623.7200000000	9936.2200000000	9642.9900000000	9936.2200000000	\N
98	5	2024-08-24 03:00:00+03	9936.2200000000	10011.1700000000	9958.1800000000	9988.8300000000	\N
99	5	2024-08-25 03:00:00+03	9988.8300000000	9987.8000000000	9933.8400000000	9986.4600000000	\N
100	5	2024-08-26 03:00:00+03	9986.4600000000	10013.4400000000	9909.9200000000	9909.9200000000	\N
101	5	2024-08-27 03:00:00+03	9909.9200000000	9894.7400000000	9749.0000000000	9795.0000000000	\N
102	5	2024-08-28 03:00:00+03	9795.0000000000	9636.8900000000	9485.7100000000	9511.2200000000	\N
103	5	2024-08-29 03:00:00+03	9511.2200000000	9685.6800000000	9520.4700000000	9542.0800000000	\N
104	5	2024-08-30 03:00:00+03	9542.0800000000	9577.2600000000	9438.0300000000	9493.9200000000	\N
105	5	2024-08-31 03:00:00+03	9493.9200000000	9550.1700000000	9500.9600000000	9500.9600000000	\N
106	5	2024-09-01 03:00:00+03	9500.9600000000	9515.0500000000	9427.7700000000	9473.7100000000	\N
107	5	2024-09-02 03:00:00+03	9473.7100000000	9464.8400000000	9381.7400000000	9464.8400000000	\N
108	5	2024-09-03 03:00:00+03	9464.8400000000	9538.5700000000	9409.5100000000	9435.7300000000	\N
109	5	2024-09-04 03:00:00+03	9435.7300000000	9467.0600000000	9304.8400000000	9439.0300000000	\N
110	5	2024-09-05 03:00:00+03	9439.0300000000	9444.9100000000	9285.6700000000	9289.5100000000	\N
111	5	2024-09-06 03:00:00+03	9289.5100000000	9351.0300000000	9092.3300000000	9097.6600000000	\N
112	5	2024-09-07 03:00:00+03	9097.6600000000	9222.5700000000	9097.6600000000	9174.8500000000	\N
113	5	2024-09-08 03:00:00+03	9174.8500000000	9201.1700000000	9088.1300000000	9169.1700000000	\N
114	5	2024-09-09 03:00:00+03	9169.1700000000	9512.5700000000	9155.9700000000	9512.5700000000	\N
115	5	2024-09-10 03:00:00+03	9512.5700000000	9614.6600000000	9438.0400000000	9614.6600000000	\N
116	5	2024-09-11 03:00:00+03	9614.6600000000	9585.1800000000	9332.3500000000	9574.3500000000	\N
117	5	2024-09-12 03:00:00+03	9574.3500000000	9667.8000000000	9537.5800000000	9664.4000000000	\N
118	5	2024-09-13 03:00:00+03	9664.4000000000	9835.8000000000	9589.6600000000	9832.9900000000	\N
119	5	2024-09-14 03:00:00+03	9832.9900000000	9945.0600000000	9831.3300000000	9842.8200000000	\N
120	5	2024-09-15 03:00:00+03	9842.8200000000	9908.7700000000	9832.8000000000	9847.8600000000	\N
121	5	2024-09-16 03:00:00+03	9847.8600000000	9852.3800000000	9566.8500000000	9607.8900000000	\N
122	5	2024-09-17 03:00:00+03	9607.8900000000	10008.7800000000	9607.8900000000	9859.9700000000	\N
123	5	2024-09-18 03:00:00+03	9859.9700000000	9929.0000000000	9791.6600000000	9901.4700000000	\N
124	5	2024-09-19 03:00:00+03	9901.4700000000	10286.4700000000	9908.1700000000	10237.7900000000	\N
125	5	2024-09-20 03:00:00+03	10237.7900000000	10384.0200000000	10139.6600000000	10199.6700000000	\N
126	5	2024-09-21 03:00:00+03	10199.6700000000	10285.2200000000	10195.2600000000	10247.5200000000	\N
127	5	2024-09-22 03:00:00+03	10247.5200000000	10305.2100000000	10151.8200000000	10252.6700000000	\N
128	5	2024-09-23 03:00:00+03	10252.6700000000	10474.3500000000	10140.4100000000	10272.5900000000	\N
129	5	2024-09-24 03:00:00+03	10272.5900000000	10444.1600000000	10187.1500000000	10399.9300000000	\N
130	5	2024-09-25 03:00:00+03	10399.9300000000	10484.6600000000	10225.2100000000	10294.5400000000	\N
131	5	2024-09-26 03:00:00+03	10294.5400000000	10631.2900000000	10179.2100000000	10466.3700000000	\N
132	5	2024-09-27 03:00:00+03	10466.3700000000	10728.7200000000	10463.0800000000	10629.8700000000	\N
133	5	2024-09-28 03:00:00+03	10629.8700000000	10690.7400000000	10577.6500000000	10607.4500000000	\N
134	5	2024-09-29 03:00:00+03	10607.4500000000	10666.2000000000	10576.5700000000	10632.2000000000	\N
135	5	2024-09-30 03:00:00+03	10632.2000000000	10654.7800000000	10146.4800000000	10246.1100000000	\N
136	5	2024-10-01 03:00:00+03	10246.1100000000	10295.7000000000	9750.2100000000	9822.6900000000	\N
137	5	2024-10-02 03:00:00+03	9822.6900000000	10046.4000000000	9711.0100000000	9833.3100000000	\N
138	5	2024-10-03 03:00:00+03	9833.3100000000	9921.0200000000	9691.3200000000	9819.8000000000	\N
139	5	2024-10-04 03:00:00+03	9819.8000000000	10063.6400000000	9775.9500000000	10055.0100000000	\N
140	5	2024-10-05 03:00:00+03	10055.0100000000	10060.3500000000	9952.6600000000	9956.1500000000	\N
141	5	2024-10-06 03:00:00+03	9956.1500000000	10132.8500000000	9953.1300000000	10084.1900000000	\N
142	5	2024-10-07 03:00:00+03	10084.1900000000	10343.3600000000	10037.9300000000	10135.9500000000	\N
143	5	2024-10-08 03:00:00+03	10135.9500000000	10188.4300000000	9974.7800000000	10044.4300000000	\N
144	5	2024-10-09 03:00:00+03	10044.4300000000	10068.0300000000	9754.6200000000	9780.0100000000	\N
145	5	2024-10-10 03:00:00+03	9780.0100000000	9887.0400000000	9551.3200000000	9671.2000000000	\N
146	5	2024-10-11 03:00:00+03	9671.2000000000	10189.5200000000	9657.5500000000	10137.5100000000	\N
147	5	2024-10-12 03:00:00+03	10137.5100000000	10201.5800000000	10043.3000000000	10141.6100000000	\N
148	5	2024-10-13 03:00:00+03	10141.6100000000	10187.8300000000	10001.2800000000	10101.4000000000	\N
149	5	2024-10-14 03:00:00+03	10101.4000000000	10603.2600000000	10059.4600000000	10553.3300000000	\N
150	5	2024-10-15 03:00:00+03	10553.3300000000	10826.5200000000	10395.8000000000	10633.6400000000	\N
151	5	2024-10-16 03:00:00+03	10633.6400000000	10903.5700000000	10587.6500000000	10798.4700000000	\N
152	5	2024-10-17 03:00:00+03	10798.4700000000	10838.5900000000	10650.9900000000	10691.8500000000	\N
153	5	2024-10-18 03:00:00+03	10691.8500000000	11004.6000000000	10691.9200000000	10919.3000000000	\N
154	5	2024-10-19 03:00:00+03	10919.3000000000	10957.4800000000	10859.6400000000	10892.6300000000	\N
155	5	2024-10-20 03:00:00+03	10892.6300000000	10989.2600000000	10869.4100000000	10971.1500000000	\N
156	5	2024-10-21 03:00:00+03	10971.1500000000	11078.4300000000	10679.6900000000	10813.5800000000	\N
157	5	2024-10-22 03:00:00+03	10813.5800000000	10841.9300000000	10639.9400000000	10780.2400000000	\N
158	5	2024-10-23 03:00:00+03	10780.2400000000	10822.8500000000	10427.0700000000	10640.4700000000	\N
159	5	2024-10-24 03:00:00+03	10640.4700000000	10898.2400000000	10597.4900000000	10878.2000000000	\N
160	5	2024-10-25 03:00:00+03	10878.2000000000	10977.0100000000	10538.0400000000	10660.6400000000	\N
161	5	2024-10-26 03:00:00+03	10660.6400000000	10756.2200000000	10479.5100000000	10728.8300000000	\N
162	5	2024-10-27 03:00:00+03	10728.8300000000	10838.7500000000	10680.7600000000	10806.4300000000	\N
163	5	2024-10-28 03:00:00+03	10806.4300000000	11137.9300000000	10785.4100000000	11100.6200000000	\N
164	5	2024-10-29 03:00:00+03	11100.6200000000	11704.2300000000	11082.6400000000	11509.8400000000	\N
165	5	2024-10-30 03:00:00+03	11509.8400000000	11605.0200000000	11374.2400000000	11584.4000000000	\N
166	5	2024-10-31 03:00:00+03	11584.4000000000	11596.5400000000	10988.0400000000	11041.4600000000	\N
167	5	2024-11-01 03:00:00+03	11041.4600000000	11291.2300000000	10869.6000000000	10928.0800000000	\N
168	5	2024-11-02 03:00:00+03	10928.0800000000	11032.1900000000	10873.9000000000	10975.8400000000	\N
169	5	2024-11-03 03:00:00+03	10975.8400000000	10980.0300000000	10671.0400000000	10888.3700000000	\N
170	5	2024-11-04 03:00:00+03	10888.3700000000	10964.8400000000	10644.3200000000	10644.3200000000	\N
171	5	2024-11-05 03:00:00+03	10644.3200000000	11129.2500000000	10572.9400000000	10967.7100000000	\N
172	5	2024-11-06 03:00:00+03	10967.7100000000	12016.6500000000	10885.1300000000	11981.6400000000	\N
173	5	2024-11-07 03:00:00+03	11981.6400000000	12090.9400000000	11720.6200000000	12042.4600000000	\N
174	5	2024-11-08 03:00:00+03	12042.4600000000	12140.1200000000	11881.4800000000	12065.2800000000	\N
175	5	2024-11-09 03:00:00+03	12065.2800000000	12072.1900000000	11913.0000000000	11998.3000000000	\N
176	5	2024-11-10 03:00:00+03	11998.3000000000	12705.8300000000	11987.8300000000	12397.9600000000	\N
177	5	2024-11-11 03:00:00+03	12397.9600000000	13667.6600000000	12338.9700000000	13609.1100000000	\N
178	5	2024-11-12 03:00:00+03	13609.1100000000	14045.2700000000	13319.4500000000	13974.5200000000	\N
179	5	2024-11-13 03:00:00+03	13974.5200000000	14554.0800000000	13486.1000000000	14021.1600000000	\N
180	5	2024-11-14 03:00:00+03	14021.1600000000	14311.4900000000	13691.0000000000	13691.0000000000	\N
181	5	2024-11-15 03:00:00+03	13691.0000000000	14270.3500000000	13564.9700000000	14268.7000000000	\N
182	5	2024-11-16 03:00:00+03	14268.7000000000	14325.6700000000	14064.7100000000	14166.2100000000	\N
183	5	2024-11-17 03:00:00+03	14166.2100000000	14207.3900000000	13961.9900000000	13992.4000000000	\N
184	5	2024-11-18 03:00:00+03	13992.4000000000	14440.2100000000	13863.4700000000	14284.8200000000	\N
185	5	2024-11-19 03:00:00+03	14284.8200000000	14649.2300000000	14095.0000000000	14437.8800000000	\N
186	5	2024-11-20 03:00:00+03	14437.8800000000	14800.3200000000	14273.7500000000	14705.3100000000	\N
187	5	2024-11-21 03:00:00+03	14705.3100000000	15403.6200000000	14627.4900000000	15281.4200000000	\N
188	5	2024-11-22 03:00:00+03	15281.4200000000	15513.5600000000	15129.6200000000	15434.8100000000	\N
189	5	2024-11-23 03:00:00+03	15434.8100000000	15477.3900000000	15133.4000000000	15220.2700000000	\N
190	5	2024-11-24 03:00:00+03	15220.2700000000	15341.7700000000	14913.7600000000	15077.2000000000	\N
191	5	2024-11-25 03:00:00+03	15077.2000000000	15318.3900000000	14714.4100000000	14750.6500000000	\N
192	5	2024-11-26 03:00:00+03	14750.6500000000	14806.2200000000	14156.1000000000	14180.1600000000	\N
193	5	2024-11-27 03:00:00+03	14180.1600000000	15152.1600000000	14160.4800000000	15034.8500000000	\N
194	5	2024-11-28 03:00:00+03	15034.8500000000	15054.9200000000	14749.9800000000	14770.4400000000	\N
195	5	2024-11-29 03:00:00+03	14770.4400000000	15353.5800000000	14769.9600000000	15158.9100000000	\N
196	5	2024-11-30 03:00:00+03	15158.9100000000	15181.3500000000	14653.9400000000	14685.0000000000	\N
197	5	2024-12-01 03:00:00+03	14685.0000000000	14762.2800000000	14500.5000000000	14699.9000000000	\N
198	5	2024-12-02 03:00:00+03	14699.9000000000	14848.5800000000	14320.1200000000	14483.1100000000	\N
199	5	2024-12-03 03:00:00+03	14483.1100000000	14571.9700000000	14181.1900000000	14489.5000000000	\N
200	5	2024-12-04 03:00:00+03	14489.5000000000	15002.5700000000	14341.6200000000	14968.2700000000	\N
201	5	2024-12-05 03:00:00+03	14968.2700000000	15691.2800000000	14773.4700000000	14990.6700000000	\N
202	5	2024-12-06 03:00:00+03	14990.6700000000	15051.6400000000	14792.1400000000	14798.5500000000	\N
203	5	2024-12-07 03:00:00+03	14798.5500000000	14810.7400000000	14793.9700000000	14798.8200000000	\N
204	5	2024-12-08 03:00:00+03	14798.8200000000	14803.3600000000	14792.3400000000	14798.6100000000	\N
205	5	2024-12-09 03:00:00+03	14798.6100000000	14805.4300000000	14782.0700000000	14801.2500000000	\N
206	5	2024-12-10 03:00:00+03	14801.2500000000	14805.3700000000	14786.9100000000	14798.7500000000	\N
207	5	2024-12-11 03:00:00+03	14798.7500000000	14812.0300000000	14789.8300000000	14805.0900000000	\N
208	5	2024-12-12 03:00:00+03	14805.0900000000	14815.2500000000	14794.0400000000	14799.4700000000	\N
209	5	2024-12-13 03:00:00+03	14799.4700000000	14807.1000000000	14792.3400000000	14798.4900000000	\N
210	5	2024-12-14 03:00:00+03	14798.4900000000	14806.3800000000	14793.4000000000	14798.9200000000	\N
211	5	2024-12-15 03:00:00+03	14798.9200000000	14805.0200000000	14795.1500000000	14796.9600000000	\N
212	5	2024-12-16 03:00:00+03	14796.9600000000	14818.2600000000	14794.0100000000	14796.5800000000	\N
213	5	2024-12-17 03:00:00+03	14796.5800000000	14813.3100000000	14794.7600000000	14800.9600000000	\N
214	5	2024-12-18 03:00:00+03	14800.9600000000	14810.1000000000	14786.9300000000	14793.6700000000	\N
215	5	2024-12-19 03:00:00+03	14793.6700000000	14809.6000000000	14786.1500000000	14792.4800000000	\N
216	5	2024-12-20 03:00:00+03	14792.4800000000	14805.8000000000	14781.0000000000	14794.7600000000	\N
217	5	2024-12-21 03:00:00+03	14794.7600000000	14803.4700000000	14785.4500000000	14798.5600000000	\N
218	5	2024-12-22 03:00:00+03	14798.5600000000	14805.6100000000	14785.2500000000	14801.1100000000	\N
219	5	2024-12-23 03:00:00+03	14801.1100000000	14806.5200000000	14783.3900000000	14799.8300000000	\N
220	5	2024-12-24 03:00:00+03	14799.8300000000	14809.0300000000	14784.2400000000	14797.6700000000	\N
221	5	2024-12-25 03:00:00+03	14797.6700000000	14803.5600000000	14784.0300000000	14796.9700000000	\N
222	5	2024-12-26 03:00:00+03	14796.9700000000	14805.1300000000	14788.5400000000	14802.0700000000	\N
223	5	2024-12-27 03:00:00+03	14802.0700000000	14803.2200000000	14779.7900000000	14800.0600000000	\N
224	5	2024-12-28 03:00:00+03	14800.0600000000	14805.0200000000	14791.2400000000	14799.3400000000	\N
225	5	2024-12-29 03:00:00+03	14799.3400000000	14807.1700000000	14792.6900000000	14803.0700000000	\N
226	5	2024-12-30 03:00:00+03	14803.0700000000	14807.6200000000	14788.2600000000	14797.5400000000	\N
227	5	2024-12-31 03:00:00+03	14797.5400000000	14807.8000000000	14764.4400000000	14777.5700000000	\N
228	5	2025-01-01 03:00:00+03	14777.5700000000	14781.9300000000	14763.9000000000	14774.6700000000	\N
229	5	2025-01-02 03:00:00+03	14774.6700000000	14779.7000000000	14764.0700000000	14773.6400000000	\N
230	5	2025-01-03 03:00:00+03	14773.6400000000	14779.4200000000	14764.5700000000	14769.5400000000	\N
231	5	2025-01-04 03:00:00+03	14769.5400000000	14776.1800000000	14765.3100000000	14773.3300000000	\N
232	5	2025-01-05 03:00:00+03	14773.3300000000	14776.3400000000	14764.1300000000	14773.0300000000	\N
233	5	2025-01-06 03:00:00+03	14773.0300000000	14781.7000000000	14763.5000000000	14774.0000000000	\N
234	5	2025-01-07 03:00:00+03	14774.0000000000	14778.1600000000	14765.8400000000	14770.4800000000	\N
235	5	2025-01-08 03:00:00+03	14770.4800000000	14779.8600000000	14760.9700000000	14771.9700000000	\N
236	5	2025-01-09 03:00:00+03	14771.9700000000	14780.3000000000	14760.1300000000	14773.2000000000	\N
237	5	2025-01-10 03:00:00+03	14773.2000000000	14800.2200000000	14757.7200000000	14771.6500000000	\N
238	5	2025-01-11 03:00:00+03	14771.6500000000	14779.0600000000	14764.1000000000	14771.8000000000	\N
239	5	2025-01-12 03:00:00+03	14771.8000000000	14777.7200000000	14761.6200000000	14775.3800000000	\N
240	5	2025-01-13 03:00:00+03	14775.3800000000	14781.2600000000	14755.3300000000	14775.7400000000	\N
241	5	2025-01-14 03:00:00+03	14775.7400000000	14779.1900000000	14761.3500000000	14770.0900000000	\N
242	5	2025-01-15 03:00:00+03	14770.0900000000	14779.1700000000	14714.8300000000	14772.7900000000	\N
243	5	2025-01-16 03:00:00+03	14772.7900000000	14783.1200000000	14767.0100000000	14773.7600000000	\N
244	5	2025-01-17 03:00:00+03	14773.7600000000	14788.9700000000	14768.3500000000	14773.5900000000	\N
245	5	2025-01-18 03:00:00+03	14773.5900000000	14783.7800000000	14761.2500000000	14775.3300000000	\N
246	5	2025-01-19 03:00:00+03	14775.3300000000	14779.8600000000	14764.3700000000	14768.1400000000	\N
247	5	2025-01-20 03:00:00+03	14768.1400000000	14785.2000000000	14749.1800000000	14772.5000000000	\N
248	5	2025-01-21 03:00:00+03	14772.5000000000	14788.1700000000	14762.6500000000	14776.3400000000	\N
249	5	2025-01-22 03:00:00+03	14776.3400000000	14782.6500000000	14767.2400000000	14777.4100000000	\N
250	5	2025-01-23 03:00:00+03	14777.4100000000	14780.9700000000	14766.8500000000	14770.6900000000	\N
251	5	2025-01-24 03:00:00+03	14770.6900000000	14783.0800000000	14768.1000000000	14773.3300000000	\N
252	5	2025-01-25 03:00:00+03	14773.3300000000	14782.0600000000	14770.0000000000	14772.0300000000	\N
253	5	2025-01-26 03:00:00+03	14772.0300000000	14778.3600000000	14769.2400000000	14770.8800000000	\N
254	5	2025-01-27 03:00:00+03	14770.8800000000	14779.3700000000	14756.3200000000	14779.3700000000	\N
255	5	2025-01-28 03:00:00+03	14779.3700000000	14782.2500000000	14764.8900000000	14772.1900000000	\N
256	5	2025-01-29 03:00:00+03	14772.1900000000	14780.1100000000	14764.8900000000	14778.2600000000	\N
257	5	2025-01-30 03:00:00+03	14778.2600000000	14782.0200000000	14767.2500000000	14773.8300000000	\N
258	5	2025-01-31 03:00:00+03	14773.8300000000	14764.9400000000	14750.4800000000	14761.6600000000	\N
259	5	2025-02-01 03:00:00+03	14761.6600000000	14765.4400000000	14752.2400000000	14759.0400000000	\N
260	5	2025-02-02 03:00:00+03	14759.0400000000	14770.7200000000	14748.2900000000	14759.7300000000	\N
261	5	2025-02-03 03:00:00+03	14759.7300000000	14786.6800000000	14744.5800000000	14760.9000000000	\N
262	5	2025-02-04 03:00:00+03	14760.9000000000	14775.5600000000	14755.3100000000	14762.5700000000	\N
263	5	2025-02-05 03:00:00+03	14762.5700000000	14771.1200000000	14699.5000000000	14764.1600000000	\N
264	5	2025-02-06 03:00:00+03	14764.1600000000	14769.2700000000	14746.6500000000	14760.9500000000	\N
265	5	2025-02-07 03:00:00+03	14760.9500000000	14769.4300000000	14751.1000000000	14757.0700000000	\N
266	5	2025-02-08 03:00:00+03	14757.0700000000	14767.8800000000	14750.2200000000	14761.1400000000	\N
267	5	2025-02-09 03:00:00+03	14761.1400000000	14766.6600000000	14751.9700000000	14760.5800000000	\N
268	5	2025-02-10 03:00:00+03	14760.5800000000	14767.9900000000	14746.0600000000	14761.1200000000	\N
269	5	2025-02-11 03:00:00+03	14761.1200000000	14767.7900000000	14744.9800000000	14760.0200000000	\N
270	5	2025-02-12 03:00:00+03	14760.0200000000	14770.8500000000	14748.0200000000	14763.2200000000	\N
271	5	2025-02-13 03:00:00+03	14763.2200000000	14768.9000000000	14751.3800000000	14764.1800000000	\N
272	5	2025-02-14 03:00:00+03	14764.1800000000	14771.0700000000	14750.2500000000	14763.8600000000	\N
273	5	2025-02-15 03:00:00+03	14763.8600000000	14769.2800000000	14756.5700000000	14762.2700000000	\N
274	5	2025-02-16 03:00:00+03	14762.2700000000	14766.3200000000	14755.9200000000	14762.1600000000	\N
275	5	2025-02-17 03:00:00+03	14762.1600000000	14768.1100000000	14752.1700000000	14763.6000000000	\N
276	5	2025-02-18 03:00:00+03	14763.6000000000	14767.6400000000	14751.3200000000	14763.8800000000	\N
277	5	2025-02-19 03:00:00+03	14763.8800000000	14769.4800000000	14749.1100000000	14765.3400000000	\N
278	5	2025-02-20 03:00:00+03	14765.3400000000	14768.8600000000	14753.3500000000	14760.8500000000	\N
279	5	2025-02-21 03:00:00+03	14760.8500000000	14796.7000000000	14743.7700000000	14767.1400000000	\N
280	5	2025-02-22 03:00:00+03	14767.1400000000	14778.1000000000	14755.6400000000	14763.6200000000	\N
281	5	2025-02-23 03:00:00+03	14763.6200000000	14767.5300000000	14754.2400000000	14762.1700000000	\N
282	5	2025-02-24 03:00:00+03	14762.1700000000	14765.8900000000	14747.1100000000	14761.9800000000	\N
283	5	2025-02-25 03:00:00+03	14761.9800000000	14773.3400000000	14731.3900000000	14757.8400000000	\N
284	5	2025-02-26 03:00:00+03	14757.8400000000	14773.2400000000	14746.2400000000	14762.0200000000	\N
285	5	2025-02-27 03:00:00+03	14762.0200000000	14768.4300000000	14741.2100000000	14761.7800000000	\N
286	5	2025-02-28 03:00:00+03	14761.7800000000	15407.5500000000	14707.1400000000	15311.1400000000	\N
287	5	2025-03-01 03:00:00+03	15311.1400000000	15552.6800000000	15278.3200000000	15448.3500000000	\N
288	5	2025-03-02 03:00:00+03	15448.3500000000	16404.9900000000	15403.3800000000	16342.7400000000	\N
289	5	2025-03-03 03:00:00+03	16342.7400000000	16391.7600000000	15412.6300000000	15499.7000000000	\N
290	5	2025-03-04 03:00:00+03	15499.7000000000	15782.9900000000	15063.4300000000	15584.8900000000	\N
291	5	2025-03-05 03:00:00+03	15584.8900000000	15990.6800000000	15536.8600000000	15957.3700000000	\N
292	5	2025-03-06 03:00:00+03	15957.3700000000	16188.4500000000	15683.5900000000	15806.5500000000	\N
293	5	2025-03-07 03:00:00+03	15806.5500000000	16016.1000000000	15394.3100000000	15597.8300000000	\N
294	5	2025-03-08 03:00:00+03	15597.8300000000	15629.3300000000	15423.2800000000	15525.7900000000	\N
295	5	2025-03-09 03:00:00+03	15525.7900000000	15549.4800000000	15113.7500000000	15203.8900000000	\N
296	5	2025-03-10 03:00:00+03	15203.8900000000	15286.7100000000	14632.7200000000	14820.8800000000	\N
297	5	2025-03-11 03:00:00+03	14820.8800000000	15246.6200000000	14552.5500000000	15175.5700000000	\N
298	5	2025-03-12 03:00:00+03	15175.5700000000	15316.1300000000	14962.9100000000	15206.9900000000	\N
299	5	2025-03-13 03:00:00+03	15206.9900000000	15328.2000000000	14881.1500000000	14925.3300000000	\N
300	5	2025-03-14 03:00:00+03	14925.3300000000	15425.3600000000	14919.8200000000	15309.3000000000	\N
301	5	2025-03-15 03:00:00+03	15309.3000000000	15367.2600000000	15255.6000000000	15332.0200000000	\N
302	5	2025-03-16 03:00:00+03	15332.0200000000	15392.0100000000	15132.7000000000	15214.7800000000	\N
303	5	2025-03-17 03:00:00+03	15214.7800000000	15373.0000000000	15087.2900000000	15297.3900000000	\N
304	5	2025-03-18 03:00:00+03	15297.3900000000	15322.8500000000	15009.9500000000	15097.8600000000	\N
305	5	2025-03-19 03:00:00+03	15097.8600000000	15491.9500000000	15087.7000000000	15431.0500000000	\N
306	5	2025-03-20 03:00:00+03	15431.0500000000	15638.4100000000	15258.5800000000	15348.6200000000	\N
307	5	2025-03-21 03:00:00+03	15348.6200000000	15379.0000000000	15210.8900000000	15315.0000000000	\N
308	5	2025-03-22 03:00:00+03	15315.0000000000	15351.2100000000	15289.6100000000	15289.6100000000	\N
309	5	2025-03-23 03:00:00+03	15289.6100000000	15433.8100000000	15263.6700000000	15409.4200000000	\N
310	5	2025-03-24 03:00:00+03	15409.4200000000	15776.5600000000	15390.7000000000	15685.5200000000	\N
311	5	2025-03-25 03:00:00+03	15685.5200000000	15830.2600000000	15517.0900000000	15737.8000000000	\N
312	5	2025-03-26 03:00:00+03	15737.8000000000	15793.6500000000	15450.9000000000	15651.3000000000	\N
313	5	2025-03-27 03:00:00+03	15651.3000000000	15718.2000000000	15459.7700000000	15658.4900000000	\N
314	5	2025-03-28 03:00:00+03	15658.4900000000	15713.5600000000	15130.2500000000	15159.8200000000	\N
315	5	2025-03-29 03:00:00+03	15159.8200000000	15268.7500000000	14854.6200000000	14973.3100000000	\N
316	5	2025-03-30 03:00:00+03	14973.3100000000	15120.4400000000	14911.9400000000	14979.3200000000	\N
317	5	2025-03-31 03:00:00+03	14979.3200000000	15101.3200000000	14801.6200000000	14890.6600000000	\N
318	5	2025-04-01 03:00:00+03	14890.6600000000	15319.2000000000	14869.3200000000	15287.5400000000	\N
319	5	2025-04-02 03:00:00+03	15287.5400000000	15735.0300000000	15099.8700000000	15340.4900000000	\N
320	5	2025-04-03 03:00:00+03	15340.4900000000	15340.4500000000	14725.3600000000	14876.1300000000	\N
321	5	2025-04-04 03:00:00+03	14876.1300000000	15213.3300000000	14783.8500000000	15134.3500000000	\N
322	5	2025-04-05 03:00:00+03	15134.3500000000	15175.4800000000	14884.1500000000	14977.8300000000	\N
323	5	2025-04-06 03:00:00+03	14977.8300000000	15071.1100000000	14343.5400000000	14380.4000000000	\N
324	5	2025-04-07 03:00:00+03	14380.4000000000	14702.6200000000	13774.4200000000	14391.3500000000	\N
325	5	2025-04-08 03:00:00+03	14391.3500000000	14665.8700000000	14032.3000000000	14127.1600000000	\N
326	5	2025-04-09 03:00:00+03	14127.1600000000	14999.7400000000	13789.6100000000	14999.7400000000	\N
327	5	2025-04-10 03:00:00+03	14999.7400000000	15046.6600000000	14335.9100000000	14529.3400000000	\N
328	5	2025-04-11 03:00:00+03	14529.3400000000	15142.3800000000	14402.5400000000	15093.9400000000	\N
329	5	2025-04-12 03:00:00+03	15093.9400000000	15377.4300000000	14943.8300000000	15324.4700000000	\N
330	5	2025-04-13 03:00:00+03	15324.4700000000	15402.9700000000	14979.1900000000	15039.6900000000	\N
331	5	2025-04-14 03:00:00+03	15039.6900000000	15368.7400000000	14974.8800000000	15237.0800000000	\N
332	5	2025-04-15 03:00:00+03	15237.0800000000	15456.9800000000	15085.4300000000	15114.0700000000	\N
333	5	2025-04-16 03:00:00+03	15114.0700000000	15314.7000000000	14987.7600000000	15153.0300000000	\N
334	5	2025-04-17 03:00:00+03	15153.0300000000	15318.0400000000	15078.0000000000	15270.7500000000	\N
335	5	2025-04-18 03:00:00+03	15270.7500000000	15270.7900000000	15158.3800000000	15181.4500000000	\N
336	5	2025-04-19 03:00:00+03	15181.4500000000	15335.3900000000	15158.3600000000	15283.5700000000	\N
337	5	2025-04-20 03:00:00+03	15283.5700000000	15304.1000000000	15108.6500000000	15259.8400000000	\N
338	5	2025-04-21 03:00:00+03	15259.8400000000	15746.1900000000	15205.1400000000	15580.7200000000	\N
339	5	2025-04-22 03:00:00+03	15580.7200000000	16258.4200000000	15528.0000000000	16173.7900000000	\N
340	5	2025-04-23 03:00:00+03	16173.7900000000	16690.3600000000	16155.8500000000	16571.2700000000	\N
341	5	2025-04-24 03:00:00+03	16571.2700000000	16607.0600000000	16251.9500000000	16533.8600000000	\N
342	5	2025-04-25 03:00:00+03	16533.8600000000	16911.7800000000	16438.0500000000	16765.8500000000	\N
343	5	2025-04-26 03:00:00+03	16765.8500000000	16816.4000000000	16603.9400000000	16640.7700000000	\N
344	5	2025-04-27 03:00:00+03	16640.7700000000	16831.4800000000	16562.8400000000	16667.3600000000	\N
345	5	2025-04-28 03:00:00+03	16667.3600000000	16872.4700000000	16429.2100000000	16699.8900000000	\N
346	5	2025-04-29 03:00:00+03	16699.8900000000	16857.9200000000	16658.1400000000	16758.2700000000	\N
347	5	2025-04-30 03:00:00+03	16758.2700000000	16818.7900000000	16265.4100000000	16522.3900000000	\N
348	5	2025-05-01 03:00:00+03	16522.3900000000	16983.6000000000	16434.9400000000	16823.7000000000	\N
349	5	2025-05-02 03:00:00+03	16823.7000000000	17054.0300000000	16786.4600000000	16919.3400000000	\N
350	5	2025-05-03 03:00:00+03	16919.3400000000	16919.9600000000	16731.8900000000	16801.8400000000	\N
351	5	2025-05-04 03:00:00+03	16801.8400000000	16817.6600000000	16631.2500000000	16707.7000000000	\N
352	5	2025-05-05 03:00:00+03	16707.7000000000	16712.5300000000	16366.9800000000	16462.3600000000	\N
353	5	2025-05-06 03:00:00+03	16462.3600000000	16584.6100000000	16356.6700000000	16519.3700000000	\N
354	5	2025-05-07 03:00:00+03	16519.3700000000	16887.0400000000	16489.8800000000	16777.5200000000	\N
355	5	2025-05-08 03:00:00+03	16777.5200000000	17534.9700000000	16767.5000000000	17515.6300000000	\N
356	5	2025-05-09 03:00:00+03	17515.6300000000	17724.9100000000	17457.4500000000	17588.5200000000	\N
357	5	2025-05-10 03:00:00+03	17588.5200000000	17700.5200000000	17535.2200000000	17586.8200000000	\N
358	5	2025-05-11 03:00:00+03	17586.8200000000	17810.8600000000	17588.0000000000	17724.4100000000	\N
359	5	2025-05-12 03:00:00+03	17724.4100000000	17906.6500000000	17280.3900000000	17528.2000000000	\N
360	5	2025-05-13 03:00:00+03	17528.2000000000	17814.2200000000	17374.6600000000	17761.6000000000	\N
361	5	2025-05-14 03:00:00+03	17761.6000000000	17766.2000000000	17545.0900000000	17641.3700000000	\N
362	5	2025-05-15 03:00:00+03	17641.3700000000	17698.9500000000	17425.2300000000	17631.2800000000	\N
363	5	2025-05-16 03:00:00+03	17631.2800000000	17741.5600000000	17556.8000000000	17656.9300000000	\N
364	5	2025-05-17 03:00:00+03	17656.9300000000	17663.4300000000	17545.9900000000	17626.4100000000	\N
365	5	2025-05-18 03:00:00+03	17626.4100000000	17877.5600000000	17577.2300000000	17693.3400000000	\N
366	5	2025-05-19 03:00:00+03	17693.3400000000	17993.1200000000	17496.8400000000	17831.5200000000	\N
367	5	2025-05-20 03:00:00+03	17831.5200000000	18013.4700000000	17706.9400000000	17979.3500000000	\N
368	5	2025-05-21 03:00:00+03	17979.3500000000	18259.2400000000	17881.9600000000	18109.3300000000	\N
369	5	2025-05-22 03:00:00+03	18109.3300000000	18522.4000000000	18082.6600000000	18397.1600000000	\N
370	5	2025-05-23 03:00:00+03	18397.1600000000	18496.7100000000	17908.5000000000	18023.1800000000	\N
371	5	2025-05-24 03:00:00+03	18023.1800000000	18184.0200000000	17844.4600000000	18101.4400000000	\N
372	5	2025-05-25 03:00:00+03	18101.4400000000	18103.9000000000	17807.5700000000	17945.7500000000	\N
373	5	2025-05-26 03:00:00+03	17945.7500000000	18306.3500000000	17889.6100000000	18187.6700000000	\N
374	5	2025-05-27 03:00:00+03	18187.6700000000	18364.7900000000	17884.9000000000	18191.1200000000	\N
375	5	2025-05-28 03:00:00+03	18191.1200000000	18206.4200000000	17771.3500000000	17848.6100000000	\N
376	5	2025-05-29 03:00:00+03	17848.6100000000	18087.1800000000	17598.7900000000	17680.9800000000	\N
377	5	2025-05-30 03:00:00+03	17680.9800000000	17720.6200000000	17303.0500000000	17434.3700000000	\N
378	5	2025-05-31 03:00:00+03	17434.3700000000	17486.0900000000	17155.6600000000	17378.0000000000	\N
379	5	2025-06-01 03:00:00+03	17378.0000000000	17457.0900000000	17223.8800000000	17403.7100000000	\N
380	5	2025-06-02 03:00:00+03	17403.7100000000	17550.0600000000	17212.8900000000	17391.0700000000	\N
381	5	2025-06-03 03:00:00+03	17391.0700000000	17675.6600000000	17349.1800000000	17526.4700000000	\N
382	5	2025-06-04 03:00:00+03	17526.4700000000	17561.6200000000	17290.3300000000	17349.8400000000	\N
383	5	2025-06-05 03:00:00+03	17349.8400000000	17547.9600000000	16729.0400000000	16729.0400000000	\N
384	5	2025-06-06 03:00:00+03	16729.0400000000	17457.9300000000	16725.3500000000	17332.5200000000	\N
385	5	2025-06-07 03:00:00+03	17332.5200000000	17543.7800000000	17245.2700000000	17536.5300000000	\N
386	5	2025-06-08 03:00:00+03	17536.5300000000	17608.1900000000	17410.8300000000	17579.6500000000	\N
387	5	2025-06-09 03:00:00+03	17579.6500000000	17973.2800000000	17457.0800000000	17965.5100000000	\N
388	5	2025-06-10 03:00:00+03	17965.5100000000	18214.2400000000	17938.6400000000	18123.9000000000	\N
389	5	2025-06-11 03:00:00+03	18123.9000000000	18173.6500000000	17943.2900000000	18001.7900000000	\N
390	5	2025-06-12 03:00:00+03	18001.7900000000	18038.5700000000	17638.1300000000	17656.6200000000	\N
391	5	2025-06-13 03:00:00+03	17656.6200000000	17709.9400000000	17282.2400000000	17592.6600000000	\N
392	5	2025-06-14 03:00:00+03	17592.6600000000	17680.7600000000	17460.8400000000	17528.7500000000	\N
393	5	2025-06-15 03:00:00+03	17528.7500000000	17669.6400000000	17496.0800000000	17507.7700000000	\N
394	5	2025-06-16 03:00:00+03	17507.7700000000	17997.5900000000	17500.1600000000	17983.3400000000	\N
395	5	2025-06-17 03:00:00+03	17983.3400000000	17990.9100000000	17355.3900000000	17464.3600000000	\N
396	5	2025-06-18 03:00:00+03	17464.3600000000	17604.3600000000	17373.4800000000	17519.4800000000	\N
397	5	2025-06-19 03:00:00+03	17519.4800000000	17568.7000000000	17415.2900000000	17456.4800000000	\N
398	5	2025-06-20 03:00:00+03	17456.4800000000	17718.6700000000	17234.0400000000	17390.1600000000	\N
399	5	2025-06-21 03:00:00+03	17390.1600000000	17424.0100000000	17217.9600000000	17282.9800000000	\N
400	5	2025-06-22 03:00:00+03	17282.9800000000	17334.6700000000	16746.4100000000	16901.1900000000	\N
401	5	2025-06-23 03:00:00+03	16901.1900000000	17417.8100000000	16838.6500000000	17390.9400000000	\N
402	5	2025-06-24 03:00:00+03	17390.9400000000	17681.2600000000	17383.0900000000	17676.1700000000	\N
403	5	2025-06-25 03:00:00+03	17676.1700000000	17914.7300000000	17631.3100000000	17872.3700000000	\N
404	5	2025-06-26 03:00:00+03	17872.3700000000	17927.9500000000	17740.4700000000	17870.5000000000	\N
405	5	2025-06-27 03:00:00+03	17870.5000000000	17871.6000000000	17707.5700000000	17793.8700000000	\N
406	5	2025-06-28 03:00:00+03	17793.8700000000	17847.1900000000	17759.5600000000	17806.5000000000	\N
407	5	2025-06-29 03:00:00+03	17806.5000000000	17955.5100000000	17801.6900000000	17825.8200000000	\N
408	5	2025-06-30 03:00:00+03	17825.8200000000	17990.3800000000	17680.0300000000	17773.2500000000	\N
409	5	2025-07-01 03:00:00+03	17773.2500000000	17778.7300000000	17508.8000000000	17583.9000000000	\N
410	5	2025-07-02 03:00:00+03	17583.9000000000	18027.8700000000	17490.9100000000	17959.1400000000	\N
411	5	2025-07-03 03:00:00+03	17959.1400000000	18118.5200000000	17889.9400000000	18053.5700000000	\N
412	5	2025-07-04 03:00:00+03	18053.5700000000	18062.8200000000	17742.0300000000	17792.4000000000	\N
413	5	2025-07-05 03:00:00+03	17792.4000000000	17869.3800000000	17792.9400000000	17839.6700000000	\N
414	5	2025-07-06 03:00:00+03	17839.6700000000	17954.9200000000	17806.9200000000	17911.7300000000	\N
415	5	2025-07-07 03:00:00+03	17911.7300000000	18025.1100000000	17768.5600000000	17815.6700000000	\N
416	5	2025-07-08 03:00:00+03	17815.6700000000	17971.1500000000	17767.5200000000	17902.7200000000	\N
417	5	2025-07-09 03:00:00+03	17902.7200000000	18297.6300000000	17863.5500000000	18148.5300000000	\N
418	5	2025-07-10 03:00:00+03	18148.5300000000	18507.4700000000	18117.2600000000	18479.2100000000	\N
419	5	2025-07-11 03:00:00+03	18479.2100000000	19096.4300000000	18463.7400000000	18980.8400000000	\N
420	5	2025-07-12 03:00:00+03	18980.8400000000	19025.3500000000	18914.8200000000	18926.4800000000	\N
421	5	2025-07-13 03:00:00+03	18926.4800000000	19133.6500000000	18920.9000000000	19109.8000000000	\N
422	5	2025-07-14 03:00:00+03	19109.8000000000	19468.2700000000	19037.2700000000	19206.4600000000	\N
423	5	2025-07-15 03:00:00+03	19206.4600000000	19215.8700000000	18812.1900000000	18872.1500000000	\N
424	5	2025-07-16 03:00:00+03	18872.1500000000	19183.9000000000	18861.8600000000	19183.7800000000	\N
425	5	2025-07-17 03:00:00+03	19183.7800000000	19193.6500000000	18968.6700000000	19140.8100000000	\N
426	5	2025-07-18 03:00:00+03	19140.8100000000	19276.7100000000	18909.8500000000	18961.3500000000	\N
427	5	2025-07-19 03:00:00+03	18961.3500000000	19061.7300000000	18958.5800000000	18993.1700000000	\N
428	5	2025-07-20 03:00:00+03	18993.1700000000	19092.3700000000	18954.5100000000	19025.5400000000	\N
429	5	2025-07-21 03:00:00+03	19025.5400000000	19162.0500000000	18885.6300000000	18925.1900000000	\N
430	5	2025-07-22 03:00:00+03	18925.1900000000	19217.5900000000	18858.0600000000	19177.3200000000	\N
431	5	2025-07-23 03:00:00+03	19177.3200000000	19220.2500000000	18959.0800000000	19015.1700000000	\N
432	5	2025-07-24 03:00:00+03	19015.1700000000	19187.8700000000	18937.4100000000	19093.8200000000	\N
433	5	2025-07-25 03:00:00+03	19093.8200000000	19104.0600000000	18586.6900000000	18879.1600000000	\N
434	5	2025-07-26 03:00:00+03	18879.1600000000	19041.5300000000	18871.3900000000	19003.1700000000	\N
435	5	2025-07-27 03:00:00+03	19003.1700000000	19188.5900000000	18982.2300000000	19095.7700000000	\N
436	5	2025-07-28 03:00:00+03	19095.7700000000	19231.1400000000	18933.2800000000	19003.7300000000	\N
437	5	2025-07-29 03:00:00+03	19003.7300000000	19165.2300000000	18871.7600000000	18928.6400000000	\N
438	5	2025-07-30 03:00:00+03	18928.6400000000	19105.4700000000	18729.4600000000	18901.9800000000	\N
439	5	2025-07-31 03:00:00+03	18901.9800000000	19078.5300000000	18670.5200000000	18676.9000000000	\N
440	5	2025-08-01 03:00:00+03	18676.9000000000	18723.9200000000	18255.1300000000	18356.5600000000	\N
441	5	2025-08-02 03:00:00+03	18356.5600000000	18370.0500000000	18112.0100000000	18204.4200000000	\N
442	5	2025-08-03 03:00:00+03	18204.4200000000	18459.9100000000	18125.9100000000	18417.1500000000	\N
443	5	2025-08-04 03:00:00+03	18417.1500000000	18578.3000000000	18372.2300000000	18467.9600000000	\N
444	5	2025-08-05 03:00:00+03	18467.9600000000	18530.7300000000	18199.5000000000	18313.6500000000	\N
445	5	2025-08-06 03:00:00+03	18313.6500000000	18578.0100000000	18278.7200000000	18496.3000000000	\N
446	5	2025-08-07 03:00:00+03	18496.3000000000	18806.2600000000	18394.9400000000	18765.9700000000	\N
447	5	2025-08-08 03:00:00+03	18765.9700000000	18817.9600000000	18596.3900000000	18726.2700000000	\N
448	5	2025-08-09 03:00:00+03	18726.2700000000	18871.1000000000	18659.3000000000	18711.1000000000	\N
449	5	2025-08-10 03:00:00+03	18711.1000000000	19043.4300000000	18654.6700000000	18935.4700000000	\N
450	5	2025-08-11 03:00:00+03	18935.4700000000	19506.3800000000	18897.9000000000	19016.2600000000	\N
451	5	2025-08-12 03:00:00+03	19016.2600000000	19219.8700000000	18914.3300000000	19205.9600000000	\N
452	5	2025-08-13 03:00:00+03	19205.9600000000	19608.4600000000	19029.1600000000	19594.4600000000	\N
453	5	2025-08-14 03:00:00+03	19594.4600000000	19813.7300000000	18946.0800000000	19019.9000000000	\N
454	5	2025-08-15 03:00:00+03	19019.9000000000	19170.6200000000	18900.4300000000	18956.1800000000	\N
455	5	2025-08-16 03:00:00+03	18956.1800000000	19025.0200000000	18927.4200000000	18997.5100000000	\N
456	5	2025-08-17 03:00:00+03	18997.5100000000	19098.8300000000	18944.3900000000	18999.2400000000	\N
457	5	2025-08-18 03:00:00+03	18999.2400000000	19035.1100000000	18661.0800000000	18853.0800000000	\N
458	5	2025-08-19 03:00:00+03	18853.0800000000	18920.7800000000	18449.9000000000	18536.8000000000	\N
459	5	2025-08-20 03:00:00+03	18536.8000000000	18635.8100000000	18407.0000000000	18632.2400000000	\N
460	5	2025-08-21 03:00:00+03	18632.2400000000	18675.0900000000	18365.2500000000	18415.8000000000	\N
461	5	2025-08-22 03:00:00+03	18415.8000000000	18976.6500000000	18330.7500000000	18924.1100000000	\N
462	5	2025-08-23 03:00:00+03	18924.1100000000	18945.6200000000	18659.1800000000	18738.1200000000	\N
463	5	2025-08-24 03:00:00+03	18738.1200000000	18777.8700000000	18291.0700000000	18444.6200000000	\N
464	5	2025-08-25 03:00:00+03	18444.6200000000	18553.2300000000	18083.3300000000	18112.1000000000	\N
465	5	2025-08-26 03:00:00+03	18112.1000000000	18299.5600000000	18003.1300000000	18294.2900000000	\N
466	5	2025-08-27 03:00:00+03	18294.2900000000	18442.2000000000	18194.3400000000	18411.4800000000	\N
467	5	2025-08-28 03:00:00+03	18411.4800000000	18534.3400000000	18242.6600000000	18360.0200000000	\N
468	5	2025-08-29 03:00:00+03	18360.0200000000	18439.4700000000	17872.1500000000	17900.4300000000	\N
469	5	2025-08-30 03:00:00+03	17900.4300000000	18025.7200000000	17855.4200000000	17994.9700000000	\N
470	5	2025-08-31 03:00:00+03	17994.9700000000	18089.9700000000	17928.1500000000	18033.3800000000	\N
471	5	2025-09-01 03:00:00+03	18033.3800000000	18170.0900000000	17824.8600000000	18028.2000000000	\N
472	5	2025-09-02 03:00:00+03	18028.2000000000	18427.5000000000	17837.2500000000	18386.0400000000	\N
473	5	2025-09-03 03:00:00+03	18386.0400000000	18542.7200000000	18265.9400000000	18496.6100000000	\N
474	5	2025-09-04 03:00:00+03	18496.6100000000	18504.8000000000	18094.4600000000	18243.9400000000	\N
475	5	2025-09-05 03:00:00+03	18243.9400000000	18653.7600000000	18225.8900000000	18412.2300000000	\N
476	5	2025-09-06 03:00:00+03	18412.2300000000	18422.7100000000	18190.1000000000	18213.3100000000	\N
477	5	2025-09-07 03:00:00+03	18213.3100000000	18385.1100000000	18202.1500000000	18380.4800000000	\N
478	5	2025-09-08 03:00:00+03	18380.4800000000	18597.9800000000	18276.4200000000	18467.8200000000	\N
479	5	2025-09-09 03:00:00+03	18467.8200000000	18653.3000000000	18300.2300000000	18398.4000000000	\N
480	5	2025-09-10 03:00:00+03	18398.4000000000	18792.9400000000	18319.0300000000	18695.0400000000	\N
481	5	2025-09-11 03:00:00+03	18695.0400000000	18849.9900000000	18678.9700000000	18818.1500000000	\N
482	5	2025-09-12 03:00:00+03	18818.1500000000	19131.6100000000	18810.8900000000	19059.4800000000	\N
483	5	2025-09-13 03:00:00+03	19059.4800000000	19079.9800000000	18924.5000000000	19021.4300000000	\N
484	5	2025-09-14 03:00:00+03	19021.4300000000	19058.5800000000	18920.0000000000	19008.1600000000	\N
485	5	2025-09-15 03:00:00+03	19008.1600000000	19132.6900000000	18808.6900000000	18941.4600000000	\N
486	5	2025-09-16 03:00:00+03	18941.4600000000	19160.9400000000	18858.3200000000	19156.2500000000	\N
487	5	2025-09-17 03:00:00+03	19156.2500000000	19209.6000000000	18866.7800000000	18981.2100000000	\N
488	5	2025-09-18 03:00:00+03	18981.2100000000	19312.9400000000	18942.3000000000	19261.1800000000	\N
489	5	2025-09-19 03:00:00+03	19261.1800000000	19254.7300000000	18884.3800000000	18920.6800000000	\N
490	5	2025-09-20 03:00:00+03	18920.6800000000	19041.2700000000	18916.0600000000	18984.0500000000	\N
491	5	2025-09-21 03:00:00+03	18984.0500000000	19011.0800000000	18903.9700000000	18922.0600000000	\N
492	5	2025-09-22 03:00:00+03	18922.0600000000	18951.8500000000	18406.3500000000	18529.6600000000	\N
493	5	2025-09-23 03:00:00+03	18529.6600000000	18608.7200000000	18331.7400000000	18404.2900000000	\N
494	5	2025-09-24 03:00:00+03	18404.2900000000	18707.9000000000	18300.4500000000	18640.6600000000	\N
495	5	2025-09-25 03:00:00+03	18640.6600000000	18662.3600000000	17895.5400000000	17980.2100000000	\N
496	5	2025-09-26 03:00:00+03	17980.2100000000	18104.5300000000	17930.4400000000	18007.9700000000	\N
497	5	2025-09-27 03:00:00+03	18007.9700000000	18047.2200000000	17985.2900000000	18017.7700000000	\N
498	5	2025-09-28 03:00:00+03	18017.7700000000	18171.0700000000	17999.5700000000	18156.0400000000	\N
499	5	2025-09-29 03:00:00+03	18156.0400000000	18510.6200000000	18142.0200000000	18497.7900000000	\N
500	5	2025-09-30 03:00:00+03	18497.7900000000	18545.8000000000	18290.3300000000	18481.0900000000	\N
501	5	2025-10-01 03:00:00+03	18481.0900000000	18821.8700000000	18398.0600000000	18784.9200000000	\N
502	5	2025-10-02 03:00:00+03	18784.9200000000	18993.0800000000	18841.0000000000	18991.4300000000	\N
503	5	2025-10-03 03:00:00+03	18991.4300000000	19389.9000000000	18936.0600000000	19252.2100000000	\N
504	5	2025-10-04 03:00:00+03	19252.2100000000	19283.3500000000	19173.3800000000	19200.9600000000	\N
505	5	2025-10-05 03:00:00+03	19200.9600000000	19482.4800000000	19200.3400000000	19268.4900000000	\N
506	5	2025-10-06 03:00:00+03	19268.4900000000	19532.0500000000	19247.7100000000	19460.5300000000	\N
507	5	2025-10-07 03:00:00+03	19460.5300000000	19467.5900000000	19105.7400000000	19211.9100000000	\N
508	5	2025-10-08 03:00:00+03	19211.9100000000	19380.4800000000	19137.0400000000	19275.9000000000	\N
509	5	2025-10-09 03:00:00+03	19275.9000000000	19347.8100000000	19039.2000000000	19145.6000000000	\N
510	5	2025-10-10 03:00:00+03	19145.6000000000	19256.4200000000	18589.8400000000	18615.0400000000	\N
511	5	2025-10-11 03:00:00+03	18615.0400000000	18678.3200000000	17915.6100000000	18314.6400000000	\N
512	5	2025-10-12 03:00:00+03	18314.6400000000	18705.2300000000	18258.1800000000	18670.8300000000	\N
513	5	2025-10-13 03:00:00+03	18670.8300000000	18744.1300000000	18588.9600000000	18727.4900000000	\N
514	5	2025-10-14 03:00:00+03	18727.4900000000	18737.7900000000	18288.7600000000	18521.0100000000	\N
515	5	2025-10-15 03:00:00+03	18521.0100000000	18564.5300000000	18304.6300000000	18375.2600000000	\N
516	5	2025-10-16 03:00:00+03	18375.2600000000	18443.5600000000	18099.1500000000	18122.9800000000	\N
517	5	2025-10-17 03:00:00+03	18122.9800000000	18231.1000000000	17803.1700000000	18061.9300000000	\N
518	5	2025-10-18 03:00:00+03	18061.9300000000	18095.8400000000	18009.1300000000	18061.9700000000	\N
519	5	2025-10-19 03:00:00+03	18061.9700000000	18249.7000000000	17993.6500000000	18203.6600000000	\N
520	5	2025-10-20 03:00:00+03	18203.6600000000	18421.1200000000	18094.4300000000	18370.6800000000	\N
521	5	2025-10-21 03:00:00+03	18370.6800000000	18603.0600000000	18092.7700000000	18358.1200000000	\N
522	5	2025-10-22 03:00:00+03	18358.1200000000	18378.1900000000	18064.3700000000	18111.6200000000	\N
523	5	2025-10-23 03:00:00+03	18111.6200000000	18393.4600000000	18043.6200000000	18256.2300000000	\N
524	5	2025-10-24 03:00:00+03	18256.2300000000	18448.6400000000	18246.9300000000	18358.1700000000	\N
525	5	2025-10-25 03:00:00+03	18358.1700000000	18444.4200000000	18344.1700000000	18404.5000000000	\N
526	5	2025-10-26 03:00:00+03	18404.5000000000	18605.0800000000	18393.0900000000	18556.2800000000	\N
527	5	2025-10-27 03:00:00+03	18556.2800000000	18784.6500000000	18529.9900000000	18643.9300000000	\N
528	5	2025-10-28 03:00:00+03	18643.9300000000	18768.2900000000	18483.6200000000	18516.5500000000	\N
529	5	2025-10-29 03:00:00+03	18516.5500000000	18575.7800000000	18253.0500000000	18403.7100000000	\N
530	5	2025-10-30 03:00:00+03	18403.7100000000	18434.3300000000	18017.2100000000	18107.9300000000	\N
531	5	2025-10-31 03:00:00+03	18107.9300000000	18364.6900000000	18098.6200000000	18241.9300000000	\N
532	5	2025-11-01 03:00:00+03	18241.9300000000	18323.0800000000	18235.6000000000	18304.3500000000	\N
533	5	2025-11-02 03:00:00+03	18304.3500000000	18375.8000000000	18264.9900000000	18289.3900000000	\N
534	5	2025-11-03 03:00:00+03	18289.3900000000	18340.4300000000	17926.9600000000	18028.5600000000	\N
535	5	2025-11-04 03:00:00+03	18028.5600000000	18075.4700000000	17489.3500000000	17569.6000000000	\N
536	5	2025-11-05 03:00:00+03	17569.6000000000	17985.0700000000	17436.5700000000	17898.6400000000	\N
537	5	2025-11-06 03:00:00+03	17898.6400000000	17939.4700000000	17446.7400000000	17520.3200000000	\N
538	5	2025-11-07 03:00:00+03	17520.3200000000	17903.0100000000	17316.4300000000	17882.7600000000	\N
539	5	2025-11-08 03:00:00+03	17882.7600000000	17918.9400000000	17601.9800000000	17674.0500000000	\N
540	5	2025-11-09 03:00:00+03	17674.0500000000	18042.0000000000	17598.7500000000	17993.5900000000	\N
541	5	2025-11-10 03:00:00+03	17993.5900000000	18247.8700000000	17928.2000000000	18176.1000000000	\N
542	5	2025-11-11 03:00:00+03	18176.1000000000	18358.4400000000	17758.3600000000	17758.3600000000	\N
543	5	2025-11-12 03:00:00+03	17758.3600000000	18082.3700000000	17521.2200000000	17604.4100000000	\N
544	5	2025-11-13 03:00:00+03	17604.4100000000	17918.7300000000	17154.4200000000	17169.9100000000	\N
545	5	2025-11-14 03:00:00+03	17169.9100000000	17470.8000000000	16676.3500000000	16685.3800000000	\N
546	5	2025-11-15 03:00:00+03	16685.3800000000	17005.3900000000	16668.6100000000	16808.5900000000	\N
547	5	2025-11-16 03:00:00+03	16808.5900000000	17010.5400000000	16526.2500000000	16569.1700000000	\N
548	5	2025-11-17 03:00:00+03	16569.1700000000	16905.7700000000	16077.3400000000	16190.6500000000	\N
549	5	2025-11-18 03:00:00+03	16190.6500000000	16512.1000000000	15745.3100000000	16345.2500000000	\N
550	5	2025-11-19 03:00:00+03	16345.2500000000	16448.1800000000	15605.1100000000	15760.9900000000	\N
551	5	2025-11-20 03:00:00+03	15760.9900000000	16393.4300000000	15165.2900000000	15209.6600000000	\N
552	5	2025-11-21 03:00:00+03	15209.6600000000	15530.6700000000	14210.7400000000	14890.2500000000	\N
553	5	2025-11-22 03:00:00+03	14890.2500000000	15059.3800000000	14705.8800000000	14874.8400000000	\N
554	5	2025-11-23 03:00:00+03	14874.8400000000	15440.3500000000	14839.3500000000	15405.4700000000	\N
555	5	2025-11-24 03:00:00+03	15405.4700000000	15709.1300000000	15015.4000000000	15703.5700000000	\N
556	5	2025-11-25 03:00:00+03	15703.5700000000	15692.6200000000	15172.3800000000	15389.8700000000	\N
557	5	2025-11-26 03:00:00+03	15389.8700000000	15919.0800000000	15205.7400000000	15828.8900000000	\N
558	5	2025-11-27 03:00:00+03	15828.8900000000	16178.6200000000	15819.8500000000	16102.2100000000	\N
559	5	2025-11-28 03:00:00+03	16102.2100000000	16359.9700000000	15885.6200000000	16060.3200000000	\N
560	5	2025-11-29 03:00:00+03	16060.3200000000	16067.0000000000	15886.8600000000	16019.2800000000	\N
561	5	2025-11-30 03:00:00+03	16019.2800000000	16175.6500000000	15930.6700000000	16065.0200000000	\N
562	5	2025-12-01 03:00:00+03	16065.0200000000	16070.4600000000	14742.9000000000	15037.4800000000	\N
563	5	2025-12-02 03:00:00+03	15037.4800000000	16236.9100000000	15057.6300000000	16027.8000000000	\N
564	5	2025-12-03 03:00:00+03	16027.8000000000	16532.2700000000	16012.5500000000	16354.2300000000	\N
565	5	2025-12-04 03:00:00+03	16354.2300000000	16549.2400000000	15997.3500000000	16280.5600000000	\N
566	5	2025-12-05 03:00:00+03	16280.5600000000	16307.8600000000	15519.4200000000	15705.3000000000	\N
567	5	2025-12-06 03:00:00+03	15705.3000000000	15874.5200000000	15647.8400000000	15757.8800000000	\N
568	5	2025-12-07 03:00:00+03	15757.8800000000	16144.9700000000	15441.6900000000	16091.1300000000	\N
569	5	2025-12-08 03:00:00+03	16091.1300000000	16229.8700000000	15679.4400000000	15975.9900000000	\N
570	5	2025-12-09 03:00:00+03	15975.9900000000	16643.3100000000	15764.7600000000	16377.4500000000	\N
571	5	2025-12-10 03:00:00+03	16377.4500000000	16623.0800000000	16121.1400000000	16268.0800000000	\N
572	5	2025-12-11 03:00:00+03	16268.0800000000	16318.9700000000	15707.3700000000	16151.2300000000	\N
573	5	2025-12-12 03:00:00+03	16151.2300000000	16462.0100000000	15753.9600000000	15864.8400000000	\N
574	5	2025-12-13 03:00:00+03	15864.8400000000	15947.5400000000	15825.9500000000	15850.4400000000	\N
575	5	2025-12-14 03:00:00+03	15850.4400000000	15914.4200000000	15558.5500000000	15581.1900000000	\N
576	5	2025-12-15 03:00:00+03	15581.1900000000	15830.4400000000	15001.3900000000	15108.7400000000	\N
577	5	2025-12-16 03:00:00+03	15108.7400000000	15506.0600000000	15025.0000000000	15414.7500000000	\N
578	5	2025-12-17 03:00:00+03	15414.7500000000	15885.5700000000	15016.7000000000	15099.5800000000	\N
579	5	2025-12-18 03:00:00+03	15099.5800000000	15729.8800000000	14862.6000000000	14920.3600000000	\N
580	5	2025-12-19 03:00:00+03	14920.3600000000	15718.1300000000	14925.3500000000	15487.6300000000	\N
581	5	2025-12-20 03:00:00+03	15487.6300000000	15583.7300000000	15449.7800000000	15534.5100000000	\N
582	5	2025-12-21 03:00:00+03	15534.5100000000	15667.6200000000	15416.2200000000	15525.4400000000	\N
583	5	2025-12-22 03:00:00+03	15525.4400000000	15916.5400000000	15468.6000000000	15554.1100000000	\N
584	5	2025-12-23 03:00:00+03	15554.1100000000	15643.6000000000	15246.3900000000	15429.1800000000	\N
585	5	2025-12-24 03:00:00+03	15429.1800000000	15456.5900000000	15210.6900000000	15406.5800000000	\N
586	5	2025-12-25 03:00:00+03	15406.5800000000	15576.5300000000	15354.2900000000	15463.0600000000	\N
587	5	2025-12-26 03:00:00+03	15463.0600000000	15752.6000000000	15250.1600000000	15409.5000000000	\N
588	5	2025-12-27 03:00:00+03	15409.5000000000	15432.7500000000	15355.7200000000	15406.4900000000	\N
589	5	2025-12-28 03:00:00+03	15406.4900000000	15489.7600000000	15389.8700000000	15399.5400000000	\N
590	5	2025-12-29 03:00:00+03	15399.5400000000	15905.6800000000	15275.7000000000	15343.2900000000	\N
591	5	2025-12-30 03:00:00+03	15343.2900000000	15721.9700000000	15280.9700000000	15470.4600000000	\N
592	5	2025-12-31 03:00:00+03	15470.4600000000	15674.9100000000	15346.2700000000	15416.2200000000	\N
593	5	2026-01-01 03:00:00+03	15416.2200000000	15555.0100000000	15392.7500000000	15516.9400000000	\N
594	5	2026-01-02 03:00:00+03	15516.9400000000	15988.2000000000	15516.1400000000	15779.6500000000	\N
595	5	2026-01-03 03:00:00+03	15779.6500000000	15896.2500000000	15707.7000000000	15888.0600000000	\N
596	5	2026-01-04 03:00:00+03	15888.0600000000	16130.6600000000	15881.6800000000	16054.3200000000	\N
597	5	2026-01-05 03:00:00+03	16054.3200000000	16665.5000000000	16029.5200000000	16558.0600000000	\N
598	5	2026-01-06 03:00:00+03	16558.0600000000	16612.0600000000	16056.7400000000	16266.0900000000	\N
599	5	2026-01-07 03:00:00+03	16266.0900000000	16504.0500000000	15943.9300000000	16004.2400000000	\N
600	5	2026-01-08 03:00:00+03	16004.2400000000	16089.5300000000	15712.5900000000	15985.6500000000	\N
601	5	2026-01-09 03:00:00+03	15985.6500000000	16170.7300000000	15771.6600000000	15874.3300000000	\N
602	5	2026-01-10 03:00:00+03	15874.3300000000	15967.0300000000	15860.7200000000	15923.2500000000	\N
603	5	2026-01-11 03:00:00+03	15923.2500000000	16045.2600000000	15893.7600000000	15946.2900000000	\N
604	5	2026-01-12 03:00:00+03	15946.2900000000	16267.0700000000	15851.8500000000	16084.0800000000	\N
605	5	2026-01-13 03:00:00+03	16084.0800000000	16619.3600000000	15988.8200000000	16608.3400000000	\N
606	5	2026-01-14 03:00:00+03	16608.3400000000	17203.0300000000	16545.3700000000	17169.7200000000	\N
607	5	2026-01-15 03:00:00+03	17169.7200000000	17178.1200000000	16760.1900000000	16768.5700000000	\N
608	5	2026-01-16 03:00:00+03	16768.5700000000	16864.3100000000	16637.4400000000	16821.4100000000	\N
609	5	2026-01-17 03:00:00+03	16821.4100000000	16830.3800000000	16747.5900000000	16793.7000000000	\N
610	5	2026-01-18 03:00:00+03	16793.7000000000	16816.8100000000	16719.3500000000	16782.1100000000	\N
611	5	2026-01-19 03:00:00+03	16782.1100000000	16817.6100000000	16270.8500000000	16438.4900000000	\N
612	5	2026-01-20 03:00:00+03	16438.4900000000	16463.3500000000	15808.5200000000	15866.5700000000	\N
613	5	2026-01-21 03:00:00+03	15866.5700000000	15939.5500000000	15593.7200000000	15895.8900000000	\N
614	5	2026-01-22 03:00:00+03	15895.8900000000	15918.1000000000	15725.5800000000	15819.0200000000	\N
615	5	2026-01-23 03:00:00+03	15819.0200000000	15975.4200000000	15772.2400000000	15828.2600000000	\N
616	5	2026-01-24 03:00:00+03	15828.2600000000	15862.5000000000	15803.4100000000	15810.8700000000	\N
617	5	2026-01-25 03:00:00+03	15810.8700000000	15803.1200000000	15545.7400000000	15545.7400000000	\N
618	5	2026-01-26 03:00:00+03	15545.7400000000	15741.3500000000	15477.4200000000	15653.4500000000	\N
619	5	2026-01-27 03:00:00+03	15653.4500000000	15842.0400000000	15635.1100000000	15825.1800000000	\N
620	5	2026-01-28 03:00:00+03	15825.1800000000	16022.1500000000	15812.6900000000	15849.9900000000	\N
621	5	2026-01-29 03:00:00+03	15849.9900000000	15882.4600000000	15044.8100000000	15119.4000000000	\N
622	5	2026-01-30 03:00:00+03	15119.4000000000	15185.9100000000	14827.5800000000	15100.0400000000	\N
623	5	2026-01-31 03:00:00+03	15100.0400000000	15137.4600000000	14008.1300000000	14175.6300000000	\N
624	5	2026-02-01 03:00:00+03	14175.6300000000	14382.8200000000	14058.3700000000	14113.6600000000	\N
625	5	2026-02-02 03:00:00+03	14113.6600000000	14416.2500000000	14028.9100000000	14294.3600000000	\N
626	5	2026-02-03 03:00:00+03	14294.3600000000	14380.9000000000	13601.6000000000	14019.8600000000	\N
627	5	2026-02-04 03:00:00+03	14019.8600000000	14058.0900000000	13484.1700000000	13627.5400000000	\N
628	5	2026-02-05 03:00:00+03	13627.5400000000	13564.2200000000	12074.6800000000	12206.4200000000	\N
629	5	2026-02-06 03:00:00+03	12206.4200000000	13316.5700000000	12183.9100000000	13184.5600000000	\N
630	5	2026-02-07 03:00:00+03	13184.5600000000	13276.4700000000	12669.8800000000	13005.1900000000	\N
631	5	2026-02-08 03:00:00+03	13005.1900000000	13361.5400000000	12914.2700000000	13283.0500000000	\N
632	5	2026-02-09 03:00:00+03	13283.0500000000	13265.9800000000	12801.1200000000	13189.2600000000	\N
633	5	2026-02-10 03:00:00+03	13189.2600000000	13159.0900000000	12821.9200000000	12849.6000000000	\N
634	5	2026-02-11 03:00:00+03	12849.6000000000	12894.9900000000	12352.2600000000	12627.1300000000	\N
635	5	2026-02-12 03:00:00+03	12627.1300000000	12730.1300000000	12191.4000000000	12268.6800000000	\N
636	5	2026-02-13 03:00:00+03	12268.6800000000	12956.7500000000	12268.6100000000	12898.6700000000	\N
637	5	2026-02-14 03:00:00+03	12898.6700000000	13158.0900000000	12852.5600000000	13065.1800000000	\N
638	5	2026-02-15 03:00:00+03	13065.1800000000	13182.5200000000	12772.3800000000	12774.2400000000	\N
639	5	2026-02-16 03:00:00+03	12774.2400000000	12855.9400000000	12675.6000000000	12682.6700000000	\N
640	5	2026-02-17 03:00:00+03	12682.6700000000	12880.8100000000	12494.0700000000	12664.5500000000	\N
641	5	2026-02-18 03:00:00+03	12664.5500000000	12687.8600000000	12425.0000000000	12425.0000000000	\N
642	5	2026-02-19 03:00:00+03	12425.0000000000	12555.2500000000	12267.0600000000	12508.4500000000	\N
643	5	2026-02-20 03:00:00+03	12508.4500000000	12743.0300000000	12496.7400000000	12496.7400000000	\N
644	5	2026-02-21 03:00:00+03	12496.7400000000	12826.0400000000	12628.8100000000	12805.4500000000	\N
645	5	2026-02-22 03:00:00+03	12805.4500000000	12756.0600000000	12567.7700000000	12596.5400000000	\N
646	5	2026-02-23 03:00:00+03	12596.5400000000	12630.6500000000	11993.3400000000	11993.7100000000	\N
647	5	2026-02-24 03:00:00+03	11993.7100000000	12076.7400000000	11742.2200000000	12037.8300000000	\N
648	5	2026-02-25 03:00:00+03	12037.8300000000	12985.4800000000	11961.1400000000	12935.6600000000	\N
649	5	2026-02-26 03:00:00+03	12935.6600000000	13051.6900000000	12455.0600000000	12596.9300000000	\N
650	5	2026-02-27 03:00:00+03	12596.9300000000	12737.9200000000	12177.5300000000	12257.1600000000	\N
651	5	2026-02-28 03:00:00+03	12257.1600000000	12524.6500000000	11866.5200000000	12506.7300000000	\N
652	5	2026-03-01 03:00:00+03	12506.7300000000	12629.5100000000	12149.4500000000	12198.0800000000	\N
653	5	2026-03-02 03:00:00+03	12198.0800000000	13030.1800000000	12208.1400000000	12879.2400000000	\N
654	5	2026-03-03 03:00:00+03	12879.2400000000	12936.4500000000	12414.9600000000	12774.9600000000	\N
655	5	2026-03-04 03:00:00+03	12774.9600000000	13793.6000000000	12632.8400000000	13740.3600000000	\N
656	5	2026-03-05 03:00:00+03	13740.3600000000	13617.9400000000	13214.9900000000	13314.0400000000	\N
657	5	2026-03-06 03:00:00+03	13314.0400000000	13322.4300000000	12730.2100000000	12730.2100000000	\N
658	5	2026-03-07 03:00:00+03	12730.2100000000	12752.4100000000	12542.8100000000	12562.6600000000	\N
659	5	2026-03-08 03:00:00+03	12562.6600000000	12670.0000000000	12473.7200000000	12548.5800000000	\N
660	5	2026-03-09 03:00:00+03	12548.5800000000	12950.2700000000	12283.0000000000	12844.2300000000	\N
697	4	2024-06-13 03:00:00+03	9764.6400000000	9821.4500000000	9655.6800000000	9688.5600000000	\N
698	4	2024-06-14 03:00:00+03	9688.5600000000	9727.3000000000	9537.3100000000	9537.3100000000	\N
699	4	2024-06-15 03:00:00+03	9537.3100000000	9677.2100000000	9595.1200000000	9669.9000000000	\N
700	4	2024-06-16 03:00:00+03	9669.9000000000	9696.3200000000	9648.8100000000	9677.0400000000	\N
701	4	2024-06-17 03:00:00+03	9677.0400000000	9705.7500000000	9501.6900000000	9584.4400000000	\N
702	4	2024-06-18 03:00:00+03	9584.4400000000	9545.4700000000	9252.3200000000	9284.8900000000	\N
703	4	2024-06-19 03:00:00+03	9284.8900000000	9447.3800000000	9333.9600000000	9407.4800000000	\N
704	4	2024-06-20 03:00:00+03	9407.4800000000	9491.0200000000	9389.9000000000	9438.1800000000	\N
705	4	2024-06-21 03:00:00+03	9438.1800000000	9441.4800000000	9351.2500000000	9387.1100000000	\N
706	4	2024-06-22 03:00:00+03	9387.1100000000	9380.6600000000	9318.5000000000	9369.0800000000	\N
707	4	2024-06-23 03:00:00+03	9369.0800000000	9391.1800000000	9330.0400000000	9352.0300000000	\N
708	4	2024-06-24 03:00:00+03	9352.0300000000	9345.1000000000	9238.3900000000	9240.2800000000	\N
709	4	2024-06-25 03:00:00+03	9240.2800000000	9378.3900000000	9281.8800000000	9375.2800000000	\N
710	4	2024-06-26 03:00:00+03	9375.2800000000	9373.3600000000	9335.3800000000	9363.7900000000	\N
711	4	2024-06-27 03:00:00+03	9363.7900000000	9401.7100000000	9308.1200000000	9397.5700000000	\N
712	4	2024-06-28 03:00:00+03	9397.5700000000	9416.6100000000	9375.3400000000	9383.3400000000	\N
713	4	2024-06-29 03:00:00+03	9383.3400000000	9403.6600000000	9354.7300000000	9370.3200000000	\N
714	4	2024-06-30 03:00:00+03	9370.3200000000	9377.3200000000	9336.9400000000	9368.5500000000	\N
715	4	2024-07-01 03:00:00+03	9368.5500000000	9446.4800000000	9365.1800000000	9437.1700000000	\N
716	4	2024-07-02 03:00:00+03	9437.1700000000	9429.5800000000	9400.8700000000	9423.7600000000	\N
717	4	2024-07-03 03:00:00+03	9423.7600000000	9311.2500000000	9311.2500000000	9311.2500000000	\N
718	4	2024-07-04 03:00:00+03	9311.2500000000	9264.4200000000	9186.5400000000	9186.9200000000	\N
719	4	2024-07-05 03:00:00+03	9186.9200000000	9368.5900000000	8839.8200000000	9340.0400000000	\N
720	4	2024-07-06 03:00:00+03	9340.0400000000	9751.0400000000	9338.8000000000	9742.2100000000	\N
721	4	2024-07-07 03:00:00+03	9742.2100000000	9795.4800000000	9464.6900000000	9516.6200000000	\N
722	4	2024-07-08 03:00:00+03	9516.6200000000	9743.1800000000	9250.2700000000	9583.0700000000	\N
723	4	2024-07-09 03:00:00+03	9583.0700000000	9714.6000000000	9535.3100000000	9714.6000000000	\N
724	4	2024-07-10 03:00:00+03	9714.6000000000	9844.9300000000	9642.0400000000	9763.2700000000	\N
725	4	2024-07-11 03:00:00+03	9763.2700000000	9892.0000000000	9721.8600000000	9771.9500000000	\N
726	4	2024-07-12 03:00:00+03	9771.9500000000	9935.6300000000	9727.1500000000	9891.1000000000	\N
727	4	2024-07-13 03:00:00+03	9891.1000000000	10122.7700000000	9879.1100000000	10122.7700000000	\N
728	4	2024-07-14 03:00:00+03	10122.7700000000	10135.3600000000	10030.4500000000	10072.4000000000	\N
729	4	2024-07-15 03:00:00+03	10072.4000000000	10289.8900000000	10155.6100000000	10284.2800000000	\N
730	4	2024-07-16 03:00:00+03	10284.2800000000	10513.0900000000	10207.5200000000	10422.1400000000	\N
731	4	2024-07-17 03:00:00+03	10422.1400000000	10515.4700000000	10320.0300000000	10359.4400000000	\N
732	4	2024-07-18 03:00:00+03	10359.4400000000	10372.9700000000	10053.1400000000	10053.1400000000	\N
733	4	2024-07-19 03:00:00+03	10053.1400000000	10345.7400000000	10011.6100000000	10345.7400000000	\N
734	4	2024-07-20 03:00:00+03	10345.7400000000	10388.1700000000	10262.0600000000	10348.8600000000	\N
735	4	2024-07-21 03:00:00+03	10348.8600000000	10458.0400000000	10286.5000000000	10418.0800000000	\N
736	4	2024-07-22 03:00:00+03	10418.0800000000	10598.6300000000	10362.2500000000	10461.2400000000	\N
737	4	2024-07-23 03:00:00+03	10461.2400000000	10413.1100000000	10050.2000000000	10142.3200000000	\N
738	4	2024-07-24 03:00:00+03	10142.3200000000	10229.8300000000	10019.4300000000	10029.3900000000	\N
739	4	2024-07-25 03:00:00+03	10029.3900000000	9998.6200000000	9548.8300000000	9571.8400000000	\N
740	4	2024-07-26 03:00:00+03	9571.8400000000	10058.0400000000	9673.9000000000	10058.0400000000	\N
741	4	2024-07-27 03:00:00+03	10058.0400000000	10227.0100000000	10026.5300000000	10100.2800000000	\N
742	4	2024-07-28 03:00:00+03	10100.2800000000	10132.8600000000	9887.1800000000	9927.7600000000	\N
743	4	2024-07-29 03:00:00+03	9927.7600000000	10191.4100000000	9864.5500000000	9963.4200000000	\N
744	4	2024-07-30 03:00:00+03	9963.4200000000	9951.4300000000	9746.2700000000	9746.2700000000	\N
745	4	2024-07-31 03:00:00+03	9746.2700000000	9817.1700000000	9529.6300000000	9529.6300000000	\N
746	4	2024-08-01 03:00:00+03	9529.6300000000	9509.6800000000	9098.9700000000	9203.7100000000	\N
747	4	2024-08-02 03:00:00+03	9203.7100000000	9462.7100000000	8976.6700000000	8976.6700000000	\N
748	4	2024-08-03 03:00:00+03	8976.6700000000	9036.1100000000	8651.1400000000	8651.1400000000	\N
749	4	2024-08-04 03:00:00+03	8651.1400000000	8842.6700000000	8342.5300000000	8556.0300000000	\N
750	4	2024-08-05 03:00:00+03	8556.0300000000	8518.3600000000	7342.1000000000	7893.3900000000	\N
751	4	2024-08-06 03:00:00+03	7893.3900000000	8276.7500000000	7934.7200000000	8232.0500000000	\N
752	4	2024-08-07 03:00:00+03	8232.0500000000	8315.0200000000	7981.8400000000	8022.1300000000	\N
753	4	2024-08-08 03:00:00+03	8022.1300000000	8393.7800000000	8016.4200000000	8346.6800000000	\N
754	4	2024-08-09 03:00:00+03	8346.6800000000	8562.0400000000	8329.6400000000	8405.1100000000	\N
755	4	2024-08-10 03:00:00+03	8405.1100000000	8460.4200000000	8395.0300000000	8452.6100000000	\N
756	4	2024-08-11 03:00:00+03	8452.6100000000	8544.7000000000	8259.9400000000	8259.9400000000	\N
757	4	2024-08-12 03:00:00+03	8259.9400000000	8437.5900000000	8163.9600000000	8318.1800000000	\N
758	4	2024-08-13 03:00:00+03	8318.1800000000	8473.3400000000	8285.1800000000	8438.5200000000	\N
759	4	2024-08-14 03:00:00+03	8438.5200000000	8477.5400000000	8353.8800000000	8383.3400000000	\N
760	4	2024-08-15 03:00:00+03	8383.3400000000	8436.8300000000	8213.4900000000	8213.4900000000	\N
761	4	2024-08-16 03:00:00+03	8213.4900000000	8403.7400000000	8218.9600000000	8358.4500000000	\N
762	4	2024-08-17 03:00:00+03	8358.4500000000	8388.7500000000	8323.9300000000	8380.9000000000	\N
763	4	2024-08-18 03:00:00+03	8380.9000000000	8456.5500000000	8370.2600000000	8449.7800000000	\N
764	4	2024-08-19 03:00:00+03	8449.7800000000	8432.5500000000	8320.9700000000	8412.8400000000	\N
765	4	2024-08-20 03:00:00+03	8412.8400000000	8637.6900000000	8424.6900000000	8620.0300000000	\N
766	4	2024-08-21 03:00:00+03	8620.0300000000	8992.3200000000	8594.9500000000	8992.3200000000	\N
767	4	2024-08-22 03:00:00+03	8992.3200000000	9012.3800000000	8887.8100000000	8973.3800000000	\N
768	4	2024-08-23 03:00:00+03	8973.3800000000	9174.0700000000	9008.0000000000	9116.2100000000	\N
769	4	2024-08-24 03:00:00+03	9116.2100000000	9534.5200000000	9294.4600000000	9517.2000000000	\N
770	4	2024-08-25 03:00:00+03	9517.2000000000	9425.0200000000	9182.3000000000	9279.2700000000	\N
771	4	2024-08-26 03:00:00+03	9279.2700000000	9337.9700000000	9013.2100000000	9013.2100000000	\N
772	4	2024-08-27 03:00:00+03	9013.2100000000	9089.0400000000	8870.1100000000	8904.4900000000	\N
773	4	2024-08-28 03:00:00+03	8904.4900000000	8773.5200000000	8460.4500000000	8638.3900000000	\N
774	4	2024-08-29 03:00:00+03	8638.3900000000	8791.6000000000	8561.2800000000	8603.9700000000	\N
775	4	2024-08-30 03:00:00+03	8603.9700000000	8667.2900000000	8412.3100000000	8563.6900000000	\N
776	4	2024-08-31 03:00:00+03	8563.6900000000	8652.3100000000	8540.5700000000	8489.6600000000	\N
777	4	2024-09-01 03:00:00+03	8489.6600000000	8667.2900000000	8412.3100000000	8443.7800000000	\N
778	4	2024-09-02 03:00:00+03	8443.7800000000	8512.6700000000	8273.0900000000	8437.5300000000	\N
779	4	2024-09-03 03:00:00+03	8437.5300000000	8519.2500000000	8347.4500000000	8358.6100000000	\N
780	4	2024-09-04 03:00:00+03	8358.6100000000	8418.7500000000	8102.9700000000	8411.8000000000	\N
781	4	2024-09-05 03:00:00+03	8411.8000000000	8457.2000000000	8292.7400000000	8311.7400000000	\N
782	4	2024-09-06 03:00:00+03	8311.7400000000	8399.9200000000	8079.1500000000	8177.6800000000	\N
783	4	2024-09-07 03:00:00+03	8177.6800000000	8387.3900000000	8051.8900000000	8360.3100000000	\N
784	4	2024-09-08 03:00:00+03	8360.3100000000	8387.3900000000	8051.8900000000	8435.7100000000	\N
785	4	2024-09-09 03:00:00+03	8435.7100000000	8734.2800000000	8453.1200000000	8734.2800000000	\N
786	4	2024-09-10 03:00:00+03	8734.2800000000	8761.7000000000	8609.3500000000	8761.7000000000	\N
787	4	2024-09-11 03:00:00+03	8761.7000000000	8740.5600000000	8386.4300000000	8696.6100000000	\N
788	4	2024-09-12 03:00:00+03	8696.6100000000	8778.4700000000	8681.1800000000	8774.8700000000	\N
789	4	2024-09-13 03:00:00+03	8774.8700000000	9003.9600000000	8743.2800000000	8960.6200000000	\N
790	4	2024-09-14 03:00:00+03	8960.6200000000	9021.5700000000	8880.3000000000	8904.2300000000	\N
791	4	2024-09-15 03:00:00+03	8904.2300000000	8949.4700000000	8790.2000000000	8790.2000000000	\N
792	4	2024-09-16 03:00:00+03	8790.2000000000	8725.3000000000	8511.3000000000	8519.0800000000	\N
793	4	2024-09-17 03:00:00+03	8519.0800000000	8784.6600000000	8484.2600000000	8657.8400000000	\N
794	4	2024-09-18 03:00:00+03	8657.8400000000	8677.9400000000	8625.3500000000	8582.7200000000	\N
795	4	2024-09-19 03:00:00+03	8582.7200000000	9096.7400000000	8667.3900000000	9069.5900000000	\N
796	4	2024-09-20 03:00:00+03	9069.5900000000	9314.9900000000	8994.9500000000	9104.6700000000	\N
797	4	2024-09-21 03:00:00+03	9104.6700000000	9309.2300000000	9058.0700000000	9253.3300000000	\N
798	4	2024-09-22 03:00:00+03	9253.3300000000	9363.2700000000	9034.7300000000	9154.5500000000	\N
799	4	2024-09-23 03:00:00+03	9154.5500000000	9418.9800000000	8976.1700000000	9310.9300000000	\N
800	4	2024-09-24 03:00:00+03	9310.9300000000	9554.2700000000	9210.6000000000	9511.5900000000	\N
801	4	2024-09-25 03:00:00+03	9511.5900000000	9660.2100000000	9440.9800000000	9575.5700000000	\N
802	4	2024-09-26 03:00:00+03	9575.5700000000	10188.9100000000	9367.4200000000	9979.9400000000	\N
803	4	2024-09-27 03:00:00+03	9979.9400000000	10562.2100000000	9974.1600000000	10379.2300000000	\N
804	4	2024-09-28 03:00:00+03	10379.2300000000	10567.4800000000	10071.4200000000	10259.7800000000	\N
805	4	2024-09-29 03:00:00+03	10259.7800000000	10317.4900000000	10042.1800000000	10232.8400000000	\N
806	4	2024-09-30 03:00:00+03	10232.8400000000	10286.8400000000	9453.5900000000	9554.5400000000	\N
807	4	2024-10-01 03:00:00+03	9554.5400000000	9707.6100000000	8637.0600000000	8844.3400000000	\N
808	4	2024-10-02 03:00:00+03	8844.3400000000	9093.2700000000	8576.5500000000	8721.9600000000	\N
809	4	2024-10-03 03:00:00+03	8721.9600000000	8819.7200000000	8409.8400000000	8578.5200000000	\N
810	4	2024-10-04 03:00:00+03	8578.5200000000	9012.5100000000	8538.1500000000	8978.8800000000	\N
811	4	2024-10-05 03:00:00+03	8978.8800000000	9038.5000000000	8791.1100000000	8795.6000000000	\N
812	4	2024-10-06 03:00:00+03	8795.6000000000	9176.5400000000	8783.8100000000	9101.8700000000	\N
813	4	2024-10-07 03:00:00+03	9101.8700000000	9374.5300000000	9035.7700000000	9109.5900000000	\N
814	4	2024-10-08 03:00:00+03	9109.5900000000	9185.7600000000	8839.1500000000	8950.2400000000	\N
815	4	2024-10-09 03:00:00+03	8950.2400000000	9029.2300000000	8734.7000000000	8740.5700000000	\N
816	4	2024-10-10 03:00:00+03	8740.5700000000	8923.1400000000	8664.9200000000	8780.5100000000	\N
817	4	2024-10-11 03:00:00+03	8780.5100000000	9182.0800000000	8767.9100000000	9162.0200000000	\N
818	4	2024-10-12 03:00:00+03	9162.0200000000	9361.2000000000	9120.2300000000	9312.7200000000	\N
819	4	2024-10-13 03:00:00+03	9312.7200000000	9340.3300000000	9064.5600000000	9125.1300000000	\N
820	4	2024-10-14 03:00:00+03	9125.1300000000	9558.3600000000	9112.1000000000	9497.4400000000	\N
821	4	2024-10-15 03:00:00+03	9497.4400000000	9648.3700000000	9141.5800000000	9263.5800000000	\N
822	4	2024-10-16 03:00:00+03	9263.5800000000	9497.0500000000	9243.2100000000	9406.5700000000	\N
823	4	2024-10-17 03:00:00+03	9406.5700000000	9433.1800000000	9056.3700000000	9203.1400000000	\N
824	4	2024-10-18 03:00:00+03	9203.1400000000	9483.7600000000	9194.4300000000	9401.6400000000	\N
825	4	2024-10-19 03:00:00+03	9401.6400000000	9660.2900000000	9391.5100000000	9496.6100000000	\N
826	4	2024-10-20 03:00:00+03	9496.6100000000	9739.2000000000	9429.7600000000	9718.8400000000	\N
827	4	2024-10-21 03:00:00+03	9718.8400000000	9881.2500000000	9422.3000000000	9607.9700000000	\N
828	4	2024-10-22 03:00:00+03	9607.9700000000	9741.6300000000	9417.7200000000	9538.9700000000	\N
829	4	2024-10-23 03:00:00+03	9538.9700000000	9597.8700000000	9018.2800000000	9269.6000000000	\N
830	4	2024-10-24 03:00:00+03	9269.6000000000	9410.3700000000	9157.7500000000	9341.6800000000	\N
831	4	2024-10-25 03:00:00+03	9341.6800000000	9422.5800000000	8940.7400000000	9041.9500000000	\N
832	4	2024-10-26 03:00:00+03	9041.9500000000	9132.2400000000	8500.5300000000	8880.6300000000	\N
833	4	2024-10-27 03:00:00+03	8880.6300000000	9067.6900000000	8828.8300000000	9001.2800000000	\N
834	4	2024-10-28 03:00:00+03	9001.2800000000	9168.3000000000	8846.4900000000	9162.1300000000	\N
835	4	2024-10-29 03:00:00+03	9162.1300000000	9641.8300000000	9169.2300000000	9461.5300000000	\N
836	4	2024-10-30 03:00:00+03	9461.5300000000	9626.7900000000	9366.3000000000	9547.3700000000	\N
837	4	2024-10-31 03:00:00+03	9547.3700000000	9553.6900000000	8966.4700000000	9013.0900000000	\N
838	4	2024-11-01 03:00:00+03	9013.0900000000	9323.7400000000	8874.5800000000	9094.7700000000	\N
839	4	2024-11-02 03:00:00+03	9094.7700000000	9201.5000000000	8877.7500000000	8953.7600000000	\N
840	4	2024-11-03 03:00:00+03	8953.7600000000	8959.6000000000	8396.8700000000	8612.1300000000	\N
841	4	2024-11-04 03:00:00+03	8612.1300000000	8727.2000000000	8445.4100000000	8481.3600000000	\N
842	4	2024-11-05 03:00:00+03	8481.3600000000	8938.6000000000	8297.0100000000	8796.5900000000	\N
843	4	2024-11-06 03:00:00+03	8796.5900000000	9786.7800000000	8707.6500000000	9704.0100000000	\N
844	4	2024-11-07 03:00:00+03	9704.0100000000	10063.9300000000	9665.3400000000	10053.2800000000	\N
845	4	2024-11-08 03:00:00+03	10053.2800000000	10446.6200000000	9924.8200000000	10337.3500000000	\N
846	4	2024-11-09 03:00:00+03	10337.3500000000	10620.7400000000	10264.3600000000	10578.1900000000	\N
847	4	2024-11-10 03:00:00+03	10578.1900000000	12915.4700000000	10579.5900000000	11930.6000000000	\N
848	4	2024-11-11 03:00:00+03	11930.6000000000	13126.1100000000	11553.1900000000	12973.9300000000	\N
849	4	2024-11-12 03:00:00+03	12973.9300000000	14164.8400000000	12217.1900000000	12789.9800000000	\N
850	4	2024-11-13 03:00:00+03	12789.9800000000	13290.4900000000	11748.6100000000	12476.8900000000	\N
851	4	2024-11-14 03:00:00+03	12476.8900000000	13067.7700000000	12082.7700000000	12304.5300000000	\N
852	4	2024-11-15 03:00:00+03	12304.5300000000	13146.1100000000	11954.9300000000	13109.2100000000	\N
853	4	2024-11-16 03:00:00+03	13109.2100000000	14332.1100000000	13052.9100000000	14043.1800000000	\N
854	4	2024-11-17 03:00:00+03	14043.1800000000	14223.3500000000	13207.7900000000	13593.0500000000	\N
855	4	2024-11-18 03:00:00+03	13593.0500000000	14395.4600000000	13330.6700000000	13988.8800000000	\N
856	4	2024-11-19 03:00:00+03	13988.8800000000	14334.8300000000	13815.9900000000	14067.2900000000	\N
857	4	2024-11-20 03:00:00+03	14067.2900000000	14710.2100000000	13719.8500000000	14088.5000000000	\N
858	4	2024-11-21 03:00:00+03	14088.5000000000	14586.2900000000	13583.8600000000	14357.6500000000	\N
859	4	2024-11-22 03:00:00+03	14357.6500000000	15902.1700000000	14291.3800000000	15881.5000000000	\N
860	4	2024-11-23 03:00:00+03	15881.5000000000	18065.6100000000	15830.0600000000	16956.0800000000	\N
861	4	2024-11-24 03:00:00+03	16956.0800000000	17754.5200000000	15849.7700000000	16752.6900000000	\N
862	4	2024-11-25 03:00:00+03	16752.6900000000	17678.2400000000	16442.3900000000	16988.2900000000	\N
863	4	2024-11-26 03:00:00+03	16988.2900000000	17053.4900000000	15263.9900000000	16240.2800000000	\N
864	4	2024-11-27 03:00:00+03	16240.2800000000	17513.1700000000	16072.3900000000	17334.6500000000	\N
865	4	2024-11-28 03:00:00+03	17334.6500000000	17538.0400000000	16700.3100000000	17022.5700000000	\N
866	4	2024-11-29 03:00:00+03	17022.5700000000	18216.7900000000	17025.9400000000	17961.4800000000	\N
867	4	2024-11-30 03:00:00+03	17961.4800000000	18760.4200000000	16428.8800000000	16614.4400000000	\N
868	4	2024-12-01 03:00:00+03	16614.4400000000	17027.4900000000	16093.0500000000	17027.4900000000	\N
869	4	2024-12-02 03:00:00+03	17027.4900000000	18160.6200000000	16358.4400000000	18013.3000000000	\N
870	4	2024-12-03 03:00:00+03	18013.3000000000	19120.9200000000	17202.1800000000	18379.1300000000	\N
871	4	2024-12-04 03:00:00+03	18379.1300000000	19213.1000000000	18210.5800000000	18930.1800000000	\N
872	4	2024-12-05 03:00:00+03	18930.1800000000	19329.6900000000	17869.9800000000	18701.2400000000	\N
873	4	2024-12-06 03:00:00+03	18701.2400000000	18936.1500000000	18629.2100000000	18915.7200000000	\N
874	4	2024-12-07 03:00:00+03	18915.7200000000	18930.9500000000	18911.3300000000	18915.2900000000	\N
875	4	2024-12-08 03:00:00+03	18915.2900000000	18921.0700000000	18907.3900000000	18914.2900000000	\N
876	4	2024-12-09 03:00:00+03	18914.2900000000	18925.2200000000	18901.6600000000	18918.1600000000	\N
877	4	2024-12-10 03:00:00+03	18918.1600000000	18924.5500000000	18898.2300000000	18915.6000000000	\N
878	4	2024-12-11 03:00:00+03	18915.6000000000	18931.7800000000	18905.1600000000	18926.5800000000	\N
879	4	2024-12-12 03:00:00+03	18926.5800000000	18936.6000000000	18910.1100000000	18915.6700000000	\N
880	4	2024-12-13 03:00:00+03	18915.6700000000	18926.9800000000	18908.3700000000	18915.9900000000	\N
881	4	2024-12-14 03:00:00+03	18915.9900000000	18925.0400000000	18908.7700000000	18916.3000000000	\N
882	4	2024-12-15 03:00:00+03	18916.3000000000	18923.3500000000	18910.6900000000	18912.6900000000	\N
883	4	2024-12-16 03:00:00+03	18912.6900000000	18934.3700000000	18909.4000000000	18913.4300000000	\N
884	4	2024-12-17 03:00:00+03	18913.4300000000	18934.3000000000	18910.1500000000	18917.5800000000	\N
885	4	2024-12-18 03:00:00+03	18917.5800000000	18929.8300000000	18900.8000000000	18908.5700000000	\N
886	4	2024-12-19 03:00:00+03	18908.5700000000	18925.8700000000	18899.6000000000	18907.7900000000	\N
887	4	2024-12-20 03:00:00+03	18907.7900000000	18924.1500000000	18893.8000000000	18911.3000000000	\N
888	4	2024-12-21 03:00:00+03	18911.3000000000	18922.9700000000	18897.9000000000	18915.1600000000	\N
889	4	2024-12-22 03:00:00+03	18915.1600000000	18924.7600000000	18898.1800000000	18918.1500000000	\N
890	4	2024-12-23 03:00:00+03	18918.1500000000	18924.2500000000	18896.6500000000	18915.8600000000	\N
891	4	2024-12-24 03:00:00+03	18915.8600000000	18928.1800000000	18897.4700000000	18914.4300000000	\N
892	4	2024-12-25 03:00:00+03	18914.4300000000	18921.6200000000	18893.9700000000	18913.1500000000	\N
893	4	2024-12-26 03:00:00+03	18913.1500000000	18921.9900000000	18902.2100000000	18918.5700000000	\N
894	4	2024-12-27 03:00:00+03	18918.5700000000	18923.1500000000	18891.3700000000	18916.3300000000	\N
895	4	2024-12-28 03:00:00+03	18916.3300000000	18923.4600000000	18908.4200000000	18916.6600000000	\N
896	4	2024-12-29 03:00:00+03	18916.6600000000	18926.9800000000	18907.2600000000	18920.1800000000	\N
897	4	2024-12-30 03:00:00+03	18920.1800000000	18926.7800000000	18902.0600000000	18915.0400000000	\N
898	4	2024-12-31 03:00:00+03	18915.0400000000	18927.6500000000	18412.0200000000	18427.4300000000	\N
899	4	2025-01-01 03:00:00+03	18427.4300000000	18433.2900000000	18411.7000000000	18425.7600000000	\N
900	4	2025-01-02 03:00:00+03	18425.7600000000	18431.0800000000	18412.3200000000	18425.8000000000	\N
901	4	2025-01-03 03:00:00+03	18425.8000000000	18430.9400000000	18413.1600000000	18418.4800000000	\N
902	4	2025-01-04 03:00:00+03	18418.4800000000	18427.7400000000	18414.1500000000	18424.0900000000	\N
903	4	2025-01-05 03:00:00+03	18424.0900000000	18428.4500000000	18412.4100000000	18424.3900000000	\N
904	4	2025-01-06 03:00:00+03	18424.3900000000	18435.5000000000	18411.6100000000	18424.3200000000	\N
905	4	2025-01-07 03:00:00+03	18424.3200000000	18429.4500000000	18413.5000000000	18420.9100000000	\N
906	4	2025-01-08 03:00:00+03	18420.9100000000	18431.2600000000	18408.7700000000	18424.7200000000	\N
907	4	2025-01-09 03:00:00+03	18424.7200000000	18431.9800000000	18407.9400000000	18423.0500000000	\N
908	4	2025-01-10 03:00:00+03	18423.0500000000	18448.2300000000	18407.1400000000	18421.6400000000	\N
909	4	2025-01-11 03:00:00+03	18421.6400000000	18429.0300000000	18413.1300000000	18421.1300000000	\N
910	4	2025-01-12 03:00:00+03	18421.1300000000	18429.5700000000	18409.3000000000	18426.6100000000	\N
911	4	2025-01-13 03:00:00+03	18426.6100000000	18433.3400000000	18401.1100000000	18426.3000000000	\N
912	4	2025-01-14 03:00:00+03	18426.3000000000	18432.5600000000	18410.8500000000	18422.5300000000	\N
913	4	2025-01-15 03:00:00+03	18422.5300000000	18431.5600000000	18364.7200000000	18424.0400000000	\N
914	4	2025-01-16 03:00:00+03	18424.0400000000	18436.3000000000	18416.3500000000	18423.0700000000	\N
915	4	2025-01-17 03:00:00+03	18423.0700000000	18442.8600000000	18418.4400000000	18424.1800000000	\N
916	4	2025-01-18 03:00:00+03	18424.1800000000	18436.4300000000	18410.7600000000	18425.2300000000	\N
917	4	2025-01-19 03:00:00+03	18425.2300000000	18434.1400000000	18411.1500000000	18417.8300000000	\N
918	4	2025-01-20 03:00:00+03	18417.8300000000	18443.4600000000	18394.8000000000	18422.5800000000	\N
919	4	2025-01-21 03:00:00+03	18422.5800000000	18439.3600000000	18409.9100000000	18427.0900000000	\N
920	4	2025-01-22 03:00:00+03	18427.0900000000	18435.6800000000	18415.2400000000	18428.3900000000	\N
921	4	2025-01-23 03:00:00+03	18428.3900000000	18434.7700000000	18415.1300000000	18417.3700000000	\N
922	4	2025-01-24 03:00:00+03	18417.3700000000	18434.3700000000	18418.2400000000	18424.4400000000	\N
923	4	2025-01-25 03:00:00+03	18424.4400000000	18433.9000000000	18418.4000000000	18422.1400000000	\N
924	4	2025-01-26 03:00:00+03	18422.1400000000	18429.5200000000	18417.6000000000	18418.0600000000	\N
925	4	2025-01-27 03:00:00+03	18418.0600000000	18431.5800000000	18402.1700000000	18428.3800000000	\N
926	4	2025-01-28 03:00:00+03	18428.3800000000	18432.7100000000	18414.2000000000	18421.0500000000	\N
927	4	2025-01-29 03:00:00+03	18421.0500000000	18431.5100000000	18412.3700000000	18426.4600000000	\N
928	4	2025-01-30 03:00:00+03	18426.4600000000	18432.3400000000	18414.3800000000	18422.6200000000	\N
929	4	2025-01-31 03:00:00+03	18422.6200000000	18423.3200000000	18378.8100000000	18393.5700000000	\N
930	4	2025-02-01 03:00:00+03	18393.5700000000	18397.7700000000	18381.4000000000	18389.7300000000	\N
931	4	2025-02-02 03:00:00+03	18389.7300000000	18402.6400000000	18380.5700000000	18391.5700000000	\N
932	4	2025-02-03 03:00:00+03	18391.5700000000	18423.2800000000	18371.2100000000	18392.5900000000	\N
933	4	2025-02-04 03:00:00+03	18392.5900000000	18411.0100000000	18384.6700000000	18395.7400000000	\N
934	4	2025-02-05 03:00:00+03	18395.7400000000	18404.7400000000	18276.4600000000	18392.1400000000	\N
935	4	2025-02-06 03:00:00+03	18392.1400000000	18402.7800000000	18362.0400000000	18391.3800000000	\N
936	4	2025-02-07 03:00:00+03	18391.3800000000	18401.8400000000	18379.7900000000	18387.6300000000	\N
937	4	2025-02-08 03:00:00+03	18387.6300000000	18400.7200000000	18379.7700000000	18392.3000000000	\N
938	4	2025-02-09 03:00:00+03	18392.3000000000	18398.8400000000	18380.5800000000	18391.4900000000	\N
939	4	2025-02-10 03:00:00+03	18391.4900000000	18401.3300000000	18374.5200000000	18391.9000000000	\N
940	4	2025-02-11 03:00:00+03	18391.9000000000	18400.3600000000	18371.1900000000	18389.6700000000	\N
941	4	2025-02-12 03:00:00+03	18389.6700000000	18402.6000000000	18376.7300000000	18396.4600000000	\N
942	4	2025-02-13 03:00:00+03	18396.4600000000	18401.8200000000	18379.5600000000	18395.2800000000	\N
943	4	2025-02-14 03:00:00+03	18395.2800000000	18405.5000000000	18378.6300000000	18395.6900000000	\N
944	4	2025-02-15 03:00:00+03	18395.6900000000	18402.6400000000	18386.5000000000	18393.6700000000	\N
945	4	2025-02-16 03:00:00+03	18393.6700000000	18398.8600000000	18385.9100000000	18393.5700000000	\N
946	4	2025-02-17 03:00:00+03	18393.5700000000	18399.4900000000	18381.1200000000	18394.9200000000	\N
947	4	2025-02-18 03:00:00+03	18394.9200000000	18400.3200000000	18381.4800000000	18395.3300000000	\N
948	4	2025-02-19 03:00:00+03	18395.3300000000	18404.1200000000	18378.0700000000	18398.6700000000	\N
949	4	2025-02-20 03:00:00+03	18398.6700000000	18401.3900000000	18382.2600000000	18391.7900000000	\N
950	4	2025-02-21 03:00:00+03	18391.7900000000	18435.8200000000	18372.2600000000	18399.8300000000	\N
951	4	2025-02-22 03:00:00+03	18399.8300000000	18413.6200000000	18385.4800000000	18395.5600000000	\N
952	4	2025-02-23 03:00:00+03	18395.5600000000	18400.2600000000	18384.3100000000	18393.6000000000	\N
953	4	2025-02-24 03:00:00+03	18393.6000000000	18399.6100000000	18376.4000000000	18392.7400000000	\N
954	4	2025-02-25 03:00:00+03	18392.7400000000	18406.5900000000	18358.1700000000	18389.0700000000	\N
955	4	2025-02-26 03:00:00+03	18389.0700000000	18412.5600000000	18372.7600000000	18394.2600000000	\N
956	4	2025-02-27 03:00:00+03	18394.2600000000	18402.4500000000	18365.4600000000	18394.0300000000	\N
957	4	2025-02-28 03:00:00+03	18394.0300000000	18691.0700000000	18310.2600000000	18613.9200000000	\N
958	4	2025-03-01 03:00:00+03	18613.9200000000	18693.2600000000	18335.0700000000	18436.4700000000	\N
959	4	2025-03-02 03:00:00+03	18436.4700000000	19360.0500000000	18380.3500000000	19353.8800000000	\N
960	4	2025-03-03 03:00:00+03	19353.8800000000	19420.8400000000	18032.9100000000	18111.4800000000	\N
961	4	2025-03-04 03:00:00+03	18111.4800000000	18158.5400000000	17402.3800000000	17860.6600000000	\N
962	4	2025-03-05 03:00:00+03	17860.6600000000	18172.3400000000	17794.5100000000	18111.1000000000	\N
963	4	2025-03-06 03:00:00+03	18111.1000000000	18276.2200000000	17872.2600000000	17923.9200000000	\N
964	4	2025-03-07 03:00:00+03	17923.9200000000	18074.3100000000	17592.1900000000	17911.9300000000	\N
965	4	2025-03-08 03:00:00+03	17911.9300000000	17910.7500000000	17538.1400000000	17651.9800000000	\N
966	4	2025-03-09 03:00:00+03	17651.9800000000	17679.2500000000	16754.7000000000	16927.0000000000	\N
967	4	2025-03-10 03:00:00+03	16927.0000000000	17214.7300000000	16344.4500000000	16547.6800000000	\N
968	4	2025-03-11 03:00:00+03	16547.6800000000	16934.6100000000	16129.9700000000	16854.1900000000	\N
969	4	2025-03-12 03:00:00+03	16854.1900000000	17049.7800000000	16625.2800000000	16903.5300000000	\N
970	4	2025-03-13 03:00:00+03	16903.5300000000	17001.0600000000	16681.1200000000	16687.4200000000	\N
971	4	2025-03-14 03:00:00+03	16687.4200000000	17169.8300000000	16684.4800000000	17116.8200000000	\N
972	4	2025-03-15 03:00:00+03	17116.8200000000	17380.5700000000	17065.9700000000	17378.6100000000	\N
973	4	2025-03-16 03:00:00+03	17378.6100000000	17457.7000000000	17080.9300000000	17202.8200000000	\N
974	4	2025-03-17 03:00:00+03	17202.8200000000	17639.2400000000	17165.6800000000	17611.3300000000	\N
975	4	2025-03-18 03:00:00+03	17611.3300000000	17628.2700000000	17360.3500000000	17465.7500000000	\N
976	4	2025-03-19 03:00:00+03	17465.7500000000	17883.1500000000	17446.4600000000	17804.9600000000	\N
977	4	2025-03-20 03:00:00+03	17804.9600000000	17936.5100000000	17569.2400000000	17657.6400000000	\N
978	4	2025-03-21 03:00:00+03	17657.6400000000	17680.0600000000	17355.5300000000	17532.4900000000	\N
979	4	2025-03-22 03:00:00+03	17532.4900000000	17658.2700000000	17445.4400000000	17624.5400000000	\N
980	4	2025-03-23 03:00:00+03	17624.5400000000	17630.1200000000	17447.5600000000	17491.2600000000	\N
981	4	2025-03-24 03:00:00+03	17491.2600000000	17901.1100000000	17447.0700000000	17872.4600000000	\N
982	4	2025-03-25 03:00:00+03	17872.4600000000	17997.1900000000	17761.3200000000	17934.8600000000	\N
983	4	2025-03-26 03:00:00+03	17934.8600000000	18427.1200000000	17707.7200000000	17933.3000000000	\N
984	4	2025-03-27 03:00:00+03	17933.3000000000	18147.0300000000	17808.6100000000	18010.5400000000	\N
985	4	2025-03-28 03:00:00+03	18010.5400000000	18084.3500000000	16913.3500000000	16919.7500000000	\N
986	4	2025-03-29 03:00:00+03	16919.7500000000	17161.0500000000	16319.1500000000	16335.3600000000	\N
987	4	2025-03-30 03:00:00+03	16335.3600000000	16720.9600000000	16327.0300000000	16580.7800000000	\N
988	4	2025-03-31 03:00:00+03	16580.7800000000	16622.5500000000	16170.3800000000	16440.2500000000	\N
989	4	2025-04-01 03:00:00+03	16440.2500000000	16924.6700000000	16410.3300000000	16629.1300000000	\N
990	4	2025-04-02 03:00:00+03	16629.1300000000	16708.8400000000	16121.0200000000	16319.3700000000	\N
991	4	2025-04-03 03:00:00+03	16319.3700000000	16331.4900000000	15344.1400000000	15646.1000000000	\N
992	4	2025-04-04 03:00:00+03	15646.1000000000	15840.7800000000	15373.1700000000	15724.7500000000	\N
993	4	2025-04-05 03:00:00+03	15724.7500000000	15771.1000000000	15328.3900000000	15363.5700000000	\N
994	4	2025-04-06 03:00:00+03	15363.5700000000	15439.3800000000	14273.7300000000	14284.4400000000	\N
995	4	2025-04-07 03:00:00+03	14284.4400000000	14807.6100000000	13692.8500000000	14628.6200000000	\N
996	4	2025-04-08 03:00:00+03	14628.6200000000	14803.0400000000	14141.0200000000	14240.9800000000	\N
997	4	2025-04-09 03:00:00+03	14240.9800000000	15157.9100000000	13898.0500000000	15157.9100000000	\N
998	4	2025-04-10 03:00:00+03	15157.9100000000	15198.9400000000	14522.8300000000	14741.2300000000	\N
999	4	2025-04-11 03:00:00+03	14741.2300000000	15235.5400000000	14675.1500000000	15189.7500000000	\N
1000	4	2025-04-12 03:00:00+03	15189.7500000000	15841.3300000000	15043.8000000000	15800.8500000000	\N
1001	4	2025-04-13 03:00:00+03	15800.8500000000	15805.8100000000	15173.4800000000	15173.4800000000	\N
1002	4	2025-04-14 03:00:00+03	15173.4800000000	15449.9500000000	15029.4900000000	15164.8100000000	\N
1003	4	2025-04-15 03:00:00+03	15164.8100000000	15310.4600000000	14843.8500000000	14863.5800000000	\N
1004	4	2025-04-16 03:00:00+03	14863.5800000000	14925.5000000000	14535.9500000000	14702.7100000000	\N
1005	4	2025-04-17 03:00:00+03	14702.7100000000	14966.4900000000	14591.0100000000	14898.9000000000	\N
1006	4	2025-04-18 03:00:00+03	14898.9000000000	15267.5400000000	14825.8400000000	15144.7500000000	\N
1007	4	2025-04-19 03:00:00+03	15144.7500000000	15481.8700000000	15123.4100000000	15479.6700000000	\N
1008	4	2025-04-20 03:00:00+03	15479.6700000000	15680.5400000000	15327.5700000000	15629.8800000000	\N
1009	4	2025-04-21 03:00:00+03	15629.8800000000	15987.2300000000	15532.3200000000	15683.0800000000	\N
1010	4	2025-04-22 03:00:00+03	15683.0800000000	16281.1500000000	15475.3400000000	16213.3100000000	\N
1011	4	2025-04-23 03:00:00+03	16213.3100000000	17222.1400000000	16201.6100000000	16903.6100000000	\N
1012	4	2025-04-24 03:00:00+03	16903.6100000000	17070.4300000000	16299.0200000000	17014.9000000000	\N
1013	4	2025-04-25 03:00:00+03	17014.9000000000	17772.5800000000	16902.3100000000	17567.5700000000	\N
1014	4	2025-04-26 03:00:00+03	17567.5700000000	18218.8600000000	17459.4400000000	17981.7700000000	\N
1015	4	2025-04-27 03:00:00+03	17981.7700000000	18256.6200000000	17301.7400000000	17512.1500000000	\N
1016	4	2025-04-28 03:00:00+03	17512.1500000000	17877.0200000000	16919.4900000000	17783.0600000000	\N
1017	4	2025-04-29 03:00:00+03	17783.0600000000	18082.0700000000	17619.5200000000	17958.5300000000	\N
1018	4	2025-04-30 03:00:00+03	17958.5300000000	18020.0200000000	17233.2100000000	17523.2200000000	\N
1019	4	2025-05-01 03:00:00+03	17523.2200000000	18046.9200000000	17472.1600000000	17795.4600000000	\N
1020	4	2025-05-02 03:00:00+03	17795.4600000000	17859.5400000000	17424.9700000000	17578.6600000000	\N
1021	4	2025-05-03 03:00:00+03	17578.6600000000	17607.5000000000	16757.9600000000	16944.6800000000	\N
1022	4	2025-05-04 03:00:00+03	16944.6800000000	17005.2500000000	16417.8300000000	16590.4700000000	\N
1023	4	2025-05-05 03:00:00+03	16590.4700000000	16660.8700000000	16218.0400000000	16361.2800000000	\N
1024	4	2025-05-06 03:00:00+03	16361.2800000000	16557.5000000000	16013.9000000000	16079.6300000000	\N
1025	4	2025-05-07 03:00:00+03	16079.6300000000	16568.1000000000	15898.4800000000	16245.5200000000	\N
1026	4	2025-05-08 03:00:00+03	16245.5200000000	18303.6900000000	16240.2500000000	18284.0200000000	\N
1027	4	2025-05-09 03:00:00+03	18284.0200000000	19493.5400000000	18181.3800000000	19052.4100000000	\N
1028	4	2025-05-10 03:00:00+03	19052.4100000000	20146.5900000000	18967.9700000000	20095.9700000000	\N
1029	4	2025-05-11 03:00:00+03	20095.9700000000	20898.4800000000	19908.9100000000	20167.2300000000	\N
1030	4	2025-05-12 03:00:00+03	20167.2300000000	21187.4000000000	19331.3200000000	19884.8100000000	\N
1031	4	2025-05-13 03:00:00+03	19884.8100000000	21099.2000000000	19221.3400000000	20967.1000000000	\N
1032	4	2025-05-14 03:00:00+03	20967.1000000000	21037.4000000000	20050.0600000000	20304.5400000000	\N
1033	4	2025-05-15 03:00:00+03	20304.5400000000	20385.5600000000	19092.3600000000	19382.2800000000	\N
1034	4	2025-05-16 03:00:00+03	19382.2800000000	19795.9200000000	19029.6400000000	19259.6900000000	\N
1035	4	2025-05-17 03:00:00+03	19259.6900000000	19325.2200000000	18535.3300000000	18789.1800000000	\N
1036	4	2025-05-18 03:00:00+03	18789.1800000000	19705.8400000000	18555.0000000000	18900.3300000000	\N
1037	4	2025-05-19 03:00:00+03	18900.3300000000	19519.8800000000	18446.1000000000	18963.8400000000	\N
1038	4	2025-05-20 03:00:00+03	18963.8400000000	19379.9900000000	18723.5400000000	19208.4800000000	\N
1039	4	2025-05-21 03:00:00+03	19208.4800000000	19798.4400000000	18965.1400000000	19264.5000000000	\N
1040	4	2025-05-22 03:00:00+03	19264.5000000000	20397.9300000000	19235.6500000000	20237.5300000000	\N
1041	4	2025-05-23 03:00:00+03	20237.5300000000	20756.4100000000	19248.7000000000	19263.0200000000	\N
1042	4	2025-05-24 03:00:00+03	19263.0200000000	19337.7100000000	18762.9100000000	19011.0800000000	\N
1043	4	2025-05-25 03:00:00+03	19011.0800000000	19016.5400000000	18135.1700000000	18409.2200000000	\N
1044	4	2025-05-26 03:00:00+03	18409.2200000000	19260.1300000000	18336.7900000000	18775.9300000000	\N
1045	4	2025-05-27 03:00:00+03	18775.9300000000	19449.4400000000	18352.4700000000	19285.8700000000	\N
1046	4	2025-05-28 03:00:00+03	19285.8700000000	19518.7800000000	18600.8400000000	18723.0300000000	\N
1047	4	2025-05-29 03:00:00+03	18723.0300000000	19546.3700000000	18557.8700000000	18620.7400000000	\N
1048	4	2025-05-30 03:00:00+03	18620.7400000000	18815.7300000000	17125.9500000000	17291.9200000000	\N
1049	4	2025-05-31 03:00:00+03	17291.9200000000	17336.7700000000	16118.9900000000	16912.8700000000	\N
1050	4	2025-06-01 03:00:00+03	16912.8700000000	16993.1100000000	16473.5800000000	16870.7700000000	\N
1051	4	2025-06-02 03:00:00+03	16870.7700000000	17361.4700000000	16521.1900000000	17319.3300000000	\N
1052	4	2025-06-03 03:00:00+03	17319.3300000000	17987.1000000000	17291.5600000000	17739.5400000000	\N
1053	4	2025-06-04 03:00:00+03	17739.5400000000	17763.5100000000	16785.6800000000	16830.2000000000	\N
1054	4	2025-06-05 03:00:00+03	16830.2000000000	17038.7900000000	15688.2400000000	15690.0300000000	\N
1055	4	2025-06-06 03:00:00+03	15690.0300000000	16549.9800000000	15670.4900000000	16146.2500000000	\N
1056	4	2025-06-07 03:00:00+03	16146.2500000000	16580.4700000000	15982.0200000000	16460.1200000000	\N
1057	4	2025-06-08 03:00:00+03	16460.1200000000	16572.0300000000	16192.3400000000	16538.0700000000	\N
1058	4	2025-06-09 03:00:00+03	16538.0700000000	16972.9800000000	16098.1900000000	16966.1900000000	\N
1059	4	2025-06-10 03:00:00+03	16966.1900000000	18013.4100000000	16964.5300000000	17886.8400000000	\N
1060	4	2025-06-11 03:00:00+03	17886.8400000000	18352.4900000000	17812.1900000000	17888.9700000000	\N
1061	4	2025-06-12 03:00:00+03	17888.9700000000	17955.6600000000	16652.4300000000	16690.5800000000	\N
1062	4	2025-06-13 03:00:00+03	16690.5800000000	16812.4300000000	15757.4200000000	16181.7600000000	\N
1063	4	2025-06-14 03:00:00+03	16181.7600000000	16311.7000000000	15893.1900000000	15959.4400000000	\N
1064	4	2025-06-15 03:00:00+03	15959.4400000000	16199.9900000000	15828.7700000000	15862.2000000000	\N
1065	4	2025-06-16 03:00:00+03	15862.2000000000	16698.9200000000	15860.2600000000	16670.6000000000	\N
1066	4	2025-06-17 03:00:00+03	16670.6000000000	16679.4400000000	15425.7500000000	15675.0200000000	\N
1067	4	2025-06-18 03:00:00+03	15675.0200000000	15729.4800000000	15182.8900000000	15611.9700000000	\N
1068	4	2025-06-19 03:00:00+03	15611.9700000000	15734.9200000000	15357.1900000000	15483.6700000000	\N
1069	4	2025-06-20 03:00:00+03	15483.6700000000	15848.9700000000	14854.9600000000	15203.5300000000	\N
1070	4	2025-06-21 03:00:00+03	15203.5300000000	15204.2600000000	14577.8100000000	14688.1100000000	\N
1071	4	2025-06-22 03:00:00+03	14688.1100000000	14692.4700000000	13591.3000000000	13867.9600000000	\N
1072	4	2025-06-23 03:00:00+03	13867.9600000000	14861.5600000000	13805.7200000000	14823.7000000000	\N
1073	4	2025-06-24 03:00:00+03	14823.7000000000	15486.2300000000	14808.3700000000	15341.0300000000	\N
1074	4	2025-06-25 03:00:00+03	15341.0300000000	15467.0600000000	15077.0300000000	15253.8800000000	\N
1075	4	2025-06-26 03:00:00+03	15253.8800000000	15447.0000000000	14773.1100000000	14972.0000000000	\N
1076	4	2025-06-27 03:00:00+03	14972.0000000000	14990.0800000000	14659.6300000000	14835.7000000000	\N
1077	4	2025-06-28 03:00:00+03	14835.7000000000	15076.7000000000	14780.2000000000	15001.2500000000	\N
1078	4	2025-06-29 03:00:00+03	15001.2500000000	15200.3200000000	14956.1600000000	15151.3500000000	\N
1079	4	2025-06-30 03:00:00+03	15151.3500000000	15675.9100000000	15101.9900000000	15387.4400000000	\N
1080	4	2025-07-01 03:00:00+03	15387.4400000000	15399.8100000000	14722.0400000000	14808.6400000000	\N
1081	4	2025-07-02 03:00:00+03	14808.6400000000	15640.2200000000	14686.4600000000	15602.5200000000	\N
1082	4	2025-07-03 03:00:00+03	15602.5200000000	15785.6200000000	15388.5500000000	15545.4200000000	\N
1083	4	2025-07-04 03:00:00+03	15545.4200000000	15569.4400000000	14719.5100000000	14770.8100000000	\N
1084	4	2025-07-05 03:00:00+03	14770.8100000000	14936.7700000000	14641.6400000000	14790.4600000000	\N
1085	4	2025-07-06 03:00:00+03	14790.4600000000	15174.6500000000	14734.3600000000	15140.4600000000	\N
1086	4	2025-07-07 03:00:00+03	15140.4600000000	15299.5900000000	14927.6600000000	14943.8700000000	\N
1087	4	2025-07-08 03:00:00+03	14943.8700000000	15339.8800000000	14858.2300000000	15242.5600000000	\N
1088	4	2025-07-09 03:00:00+03	15242.5600000000	15872.3200000000	15219.7100000000	15809.0100000000	\N
1089	4	2025-07-10 03:00:00+03	15809.0100000000	16501.5000000000	15806.4200000000	16488.1900000000	\N
1090	4	2025-07-11 03:00:00+03	16488.1900000000	17640.7500000000	16478.0700000000	17379.2000000000	\N
1091	4	2025-07-12 03:00:00+03	17379.2000000000	17393.3300000000	16701.2700000000	16884.9200000000	\N
1092	4	2025-07-13 03:00:00+03	16884.9200000000	17597.4500000000	16879.6900000000	17560.9500000000	\N
1093	4	2025-07-14 03:00:00+03	17560.9500000000	17879.0100000000	17056.2500000000	17178.2200000000	\N
1094	4	2025-07-15 03:00:00+03	17178.2200000000	17397.9600000000	16775.2600000000	17197.7700000000	\N
1095	4	2025-07-16 03:00:00+03	17197.7700000000	18153.3400000000	17183.3200000000	18072.3400000000	\N
1096	4	2025-07-17 03:00:00+03	18072.3400000000	18216.1100000000	17639.6800000000	17790.0300000000	\N
1097	4	2025-07-18 03:00:00+03	17790.0300000000	18692.5400000000	17709.1700000000	17909.9300000000	\N
1098	4	2025-07-19 03:00:00+03	17909.9300000000	18201.7000000000	17676.5100000000	18136.4600000000	\N
1099	4	2025-07-20 03:00:00+03	18136.4600000000	18951.2800000000	17980.0300000000	18770.3000000000	\N
1100	4	2025-07-21 03:00:00+03	18770.3000000000	19158.0600000000	18399.1600000000	18687.1100000000	\N
1101	4	2025-07-22 03:00:00+03	18687.1100000000	18933.1600000000	18170.5300000000	18617.3700000000	\N
1102	4	2025-07-23 03:00:00+03	18617.3700000000	18868.5300000000	17567.0600000000	17725.4600000000	\N
1103	4	2025-07-24 03:00:00+03	17725.4600000000	18180.6100000000	17116.6200000000	17901.8700000000	\N
1104	4	2025-07-25 03:00:00+03	17901.8700000000	17902.5800000000	17169.4300000000	17684.0900000000	\N
1105	4	2025-07-26 03:00:00+03	17684.0900000000	18042.5900000000	17701.9300000000	17927.5600000000	\N
1106	4	2025-07-27 03:00:00+03	17927.5600000000	18303.6300000000	17820.9700000000	18160.2200000000	\N
1107	4	2025-07-28 03:00:00+03	18160.2200000000	18655.7900000000	17468.5800000000	17535.8000000000	\N
1108	4	2025-07-29 03:00:00+03	17535.8000000000	17820.4500000000	17044.7500000000	17097.9800000000	\N
1109	4	2025-07-30 03:00:00+03	17097.9800000000	17334.3300000000	16551.7000000000	17085.4600000000	\N
1110	4	2025-07-31 03:00:00+03	17085.4600000000	17582.1700000000	16448.1800000000	16472.5300000000	\N
1111	4	2025-08-01 03:00:00+03	16472.5300000000	16498.1500000000	15741.4900000000	15912.5800000000	\N
1112	4	2025-08-02 03:00:00+03	15912.5800000000	16017.9200000000	15239.5400000000	15440.6000000000	\N
1113	4	2025-08-03 03:00:00+03	15440.6000000000	15947.8900000000	15336.8100000000	15922.4300000000	\N
1114	4	2025-08-04 03:00:00+03	15922.4300000000	16456.0500000000	15869.9500000000	16283.0600000000	\N
1115	4	2025-08-05 03:00:00+03	16283.0600000000	16421.1700000000	15735.7600000000	15791.5500000000	\N
1116	4	2025-08-06 03:00:00+03	15791.5500000000	16136.6300000000	15587.8100000000	16054.1000000000	\N
1117	4	2025-08-07 03:00:00+03	16054.1000000000	16574.7300000000	15982.5400000000	16526.4800000000	\N
1118	4	2025-08-08 03:00:00+03	16526.4800000000	16990.1700000000	16527.2600000000	16933.1500000000	\N
1119	4	2025-08-09 03:00:00+03	16933.1500000000	17885.9200000000	16845.6700000000	17885.9200000000	\N
1120	4	2025-08-10 03:00:00+03	17885.9200000000	17885.9000000000	17156.3300000000	17560.8000000000	\N
1121	4	2025-08-11 03:00:00+03	17560.8000000000	17976.5500000000	17041.1700000000	17173.6900000000	\N
1122	4	2025-08-12 03:00:00+03	17173.6900000000	17918.8800000000	16791.1300000000	17918.8800000000	\N
1123	4	2025-08-13 03:00:00+03	17918.8800000000	18381.1300000000	17623.3400000000	18273.0100000000	\N
1124	4	2025-08-14 03:00:00+03	18273.0100000000	18363.2900000000	16799.0800000000	16925.3300000000	\N
1125	4	2025-08-15 03:00:00+03	16925.3300000000	17201.8600000000	16417.1000000000	16643.5300000000	\N
1126	4	2025-08-16 03:00:00+03	16643.5300000000	16959.1200000000	16601.7700000000	16947.6400000000	\N
1127	4	2025-08-17 03:00:00+03	16947.6400000000	17377.8200000000	16876.5200000000	17063.6300000000	\N
1128	4	2025-08-18 03:00:00+03	17063.6300000000	17176.2400000000	16419.6000000000	16590.0400000000	\N
1129	4	2025-08-19 03:00:00+03	16590.0400000000	16774.7300000000	16093.6100000000	16240.1900000000	\N
1130	4	2025-08-20 03:00:00+03	16240.1900000000	16754.0300000000	16008.0400000000	16741.1800000000	\N
1131	4	2025-08-21 03:00:00+03	16741.1800000000	16761.6700000000	16231.8300000000	16324.1100000000	\N
1132	4	2025-08-22 03:00:00+03	16324.1100000000	17549.2500000000	15984.9700000000	17540.7600000000	\N
1133	4	2025-08-23 03:00:00+03	17540.7600000000	17795.1200000000	17313.3800000000	17487.8300000000	\N
1134	4	2025-08-24 03:00:00+03	17487.8300000000	17785.6600000000	17027.7900000000	17353.2200000000	\N
1135	4	2025-08-25 03:00:00+03	17353.2200000000	17427.5600000000	16086.2400000000	16118.8300000000	\N
1136	4	2025-08-26 03:00:00+03	16118.8300000000	16712.6800000000	16047.7600000000	16655.4100000000	\N
1137	4	2025-08-27 03:00:00+03	16655.4100000000	16744.3200000000	16408.5600000000	16569.2700000000	\N
1138	4	2025-08-28 03:00:00+03	16569.2700000000	16738.3000000000	16350.0200000000	16399.8700000000	\N
1139	4	2025-08-29 03:00:00+03	16399.8700000000	16620.1100000000	15776.1800000000	15856.9200000000	\N
1140	4	2025-08-30 03:00:00+03	15856.9200000000	16180.2400000000	15825.8200000000	16090.9800000000	\N
1141	4	2025-08-31 03:00:00+03	16090.9800000000	16288.9300000000	16044.7200000000	16150.9400000000	\N
1142	4	2025-09-01 03:00:00+03	16150.9400000000	16192.3100000000	15350.7000000000	15422.0900000000	\N
1143	4	2025-09-02 03:00:00+03	15422.0900000000	15831.3300000000	15265.8600000000	15754.7500000000	\N
1144	4	2025-09-03 03:00:00+03	15754.7500000000	16065.0500000000	15667.0500000000	16010.3900000000	\N
1145	4	2025-09-04 03:00:00+03	16010.3900000000	16025.0600000000	15332.0000000000	15417.6500000000	\N
1146	4	2025-09-05 03:00:00+03	15417.6500000000	16067.3300000000	15404.2600000000	15932.7500000000	\N
1147	4	2025-09-06 03:00:00+03	15932.7500000000	15922.2600000000	15646.9300000000	15727.7700000000	\N
1148	4	2025-09-07 03:00:00+03	15727.7700000000	16002.9900000000	15716.7200000000	15919.4800000000	\N
1149	4	2025-09-08 03:00:00+03	15919.4800000000	16443.8200000000	15891.8100000000	16253.7400000000	\N
1150	4	2025-09-09 03:00:00+03	16253.7400000000	16888.2700000000	16226.4200000000	16347.3400000000	\N
1151	4	2025-09-10 03:00:00+03	16347.3400000000	16815.6300000000	16325.2800000000	16543.4500000000	\N
1152	4	2025-09-11 03:00:00+03	16543.4500000000	16873.8000000000	16537.3200000000	16848.7900000000	\N
1153	4	2025-09-12 03:00:00+03	16848.7900000000	17175.1300000000	16783.7500000000	17147.9400000000	\N
1154	4	2025-09-13 03:00:00+03	17147.9400000000	17655.1600000000	17098.7400000000	17450.7800000000	\N
1155	4	2025-09-14 03:00:00+03	17450.7800000000	17532.6300000000	17073.6900000000	17263.7300000000	\N
1156	4	2025-09-15 03:00:00+03	17263.7300000000	17659.8400000000	16692.9200000000	17023.2800000000	\N
1157	4	2025-09-16 03:00:00+03	17023.2800000000	17079.1800000000	16683.7500000000	16950.2900000000	\N
1158	4	2025-09-17 03:00:00+03	16950.2900000000	17321.3500000000	16707.1100000000	17134.0000000000	\N
1159	4	2025-09-18 03:00:00+03	17134.0000000000	17627.1200000000	17048.8500000000	17590.6100000000	\N
1160	4	2025-09-19 03:00:00+03	17590.6100000000	17657.3500000000	16737.7900000000	16776.9800000000	\N
1161	4	2025-09-20 03:00:00+03	16776.9800000000	17036.7700000000	16636.7800000000	16931.1700000000	\N
1162	4	2025-09-21 03:00:00+03	16931.1700000000	17077.2300000000	16620.6900000000	16678.9100000000	\N
1163	4	2025-09-22 03:00:00+03	16678.9100000000	16740.4400000000	15121.6600000000	15366.2600000000	\N
1164	4	2025-09-23 03:00:00+03	15366.2600000000	15655.8000000000	15076.6400000000	15384.3800000000	\N
1165	4	2025-09-24 03:00:00+03	15384.3800000000	15754.3600000000	15124.8500000000	15640.2900000000	\N
1166	4	2025-09-25 03:00:00+03	15640.2900000000	15649.0100000000	14639.6300000000	14811.4900000000	\N
1167	4	2025-09-26 03:00:00+03	14811.4900000000	15432.8300000000	14665.4400000000	15300.5800000000	\N
1168	4	2025-09-27 03:00:00+03	15300.5800000000	16843.5700000000	15282.3300000000	16393.9000000000	\N
1169	4	2025-09-28 03:00:00+03	16393.9000000000	16515.4200000000	15916.1300000000	16442.8600000000	\N
1170	4	2025-09-29 03:00:00+03	16442.8600000000	16801.0000000000	16137.8600000000	16466.0800000000	\N
1171	4	2025-09-30 03:00:00+03	16466.0800000000	16501.9200000000	15519.7000000000	15928.5400000000	\N
1172	4	2025-10-01 03:00:00+03	15928.5400000000	16441.1900000000	15843.3800000000	16417.7800000000	\N
1173	4	2025-10-02 03:00:00+03	16417.7800000000	17039.7400000000	16655.5700000000	17027.9500000000	\N
1174	4	2025-10-03 03:00:00+03	17027.9500000000	17326.4900000000	16799.4300000000	17275.8100000000	\N
1175	4	2025-10-04 03:00:00+03	17275.8100000000	17348.4900000000	16826.5900000000	16944.6100000000	\N
1176	4	2025-10-05 03:00:00+03	16944.6100000000	17291.3700000000	16752.0400000000	16808.6200000000	\N
1177	4	2025-10-06 03:00:00+03	16808.6200000000	17403.1700000000	16775.7500000000	17347.6300000000	\N
1178	4	2025-10-07 03:00:00+03	17347.6300000000	17384.7100000000	16754.0800000000	16896.3400000000	\N
1179	4	2025-10-08 03:00:00+03	16896.3400000000	16973.5600000000	16619.5800000000	16857.8200000000	\N
1180	4	2025-10-09 03:00:00+03	16857.8200000000	16896.8200000000	16110.7100000000	16261.7100000000	\N
1181	4	2025-10-10 03:00:00+03	16261.7100000000	16525.0000000000	15182.7200000000	15182.7200000000	\N
1182	4	2025-10-11 03:00:00+03	15182.7200000000	15290.3700000000	11608.1900000000	13631.4500000000	\N
1183	4	2025-10-12 03:00:00+03	13631.4500000000	14774.3700000000	13518.9300000000	14704.0800000000	\N
1184	4	2025-10-13 03:00:00+03	14704.0800000000	15123.6600000000	14511.7100000000	15101.7600000000	\N
1185	4	2025-10-14 03:00:00+03	15101.7600000000	15124.6200000000	14177.4400000000	14554.8800000000	\N
1186	4	2025-10-15 03:00:00+03	14554.8800000000	14775.7000000000	14139.8100000000	14190.5500000000	\N
1187	4	2025-10-16 03:00:00+03	14190.5500000000	14584.5900000000	13887.0800000000	13897.3500000000	\N
1188	4	2025-10-17 03:00:00+03	13897.3500000000	14017.4000000000	13401.0100000000	13771.9100000000	\N
1189	4	2025-10-18 03:00:00+03	13771.9100000000	13883.1200000000	13671.0400000000	13796.7300000000	\N
1190	4	2025-10-19 03:00:00+03	13796.7300000000	14200.6800000000	13657.1900000000	14172.7300000000	\N
1191	4	2025-10-20 03:00:00+03	14172.7300000000	14369.7600000000	13940.1200000000	14192.4700000000	\N
1192	4	2025-10-21 03:00:00+03	14192.4700000000	14326.6900000000	13772.7600000000	14047.8000000000	\N
1193	4	2025-10-22 03:00:00+03	14047.8000000000	14053.2600000000	13570.1200000000	13570.8300000000	\N
1194	4	2025-10-23 03:00:00+03	13570.8300000000	13843.8600000000	13436.0400000000	13685.7400000000	\N
1195	4	2025-10-24 03:00:00+03	13685.7400000000	13900.7200000000	13679.3500000000	13818.7700000000	\N
1196	4	2025-10-25 03:00:00+03	13818.7700000000	13837.7300000000	13733.1600000000	13822.0600000000	\N
1197	4	2025-10-26 03:00:00+03	13822.0600000000	14004.0900000000	13714.3600000000	13869.7400000000	\N
1198	4	2025-10-27 03:00:00+03	13869.7400000000	14153.6800000000	13846.6500000000	13864.6600000000	\N
1199	4	2025-10-28 03:00:00+03	13864.6600000000	13952.0600000000	13579.5600000000	13601.2300000000	\N
1200	4	2025-10-29 03:00:00+03	13601.2300000000	13754.4300000000	13470.5700000000	13734.1000000000	\N
1201	4	2025-10-30 03:00:00+03	13734.1000000000	13747.2600000000	12959.5300000000	13045.1300000000	\N
1202	4	2025-10-31 03:00:00+03	13045.1300000000	13304.0200000000	13038.0900000000	13201.2500000000	\N
1203	4	2025-11-01 03:00:00+03	13201.2500000000	13441.4000000000	13143.7600000000	13417.7800000000	\N
1204	4	2025-11-02 03:00:00+03	13417.7800000000	13455.5200000000	13242.3900000000	13298.1400000000	\N
1205	4	2025-11-03 03:00:00+03	13298.1400000000	13404.7300000000	12429.4800000000	12443.2400000000	\N
1206	4	2025-11-04 03:00:00+03	12443.2400000000	12593.0900000000	11961.4500000000	12148.8400000000	\N
1207	4	2025-11-05 03:00:00+03	12148.8400000000	12611.9500000000	11955.6600000000	12563.9900000000	\N
1208	4	2025-11-06 03:00:00+03	12563.9900000000	12602.4100000000	12113.9100000000	12316.6200000000	\N
1209	4	2025-11-07 03:00:00+03	12316.6200000000	13440.0100000000	12300.2200000000	13424.9800000000	\N
1210	4	2025-11-08 03:00:00+03	13424.9800000000	13483.9300000000	13009.9700000000	13118.1100000000	\N
1211	4	2025-11-09 03:00:00+03	13118.1100000000	13408.9600000000	12844.2500000000	13345.1600000000	\N
1212	4	2025-11-10 03:00:00+03	13345.1600000000	13532.0000000000	13236.6900000000	13463.2800000000	\N
1213	4	2025-11-11 03:00:00+03	13463.2800000000	13784.6500000000	13047.6000000000	13047.6000000000	\N
1214	4	2025-11-12 03:00:00+03	13047.6000000000	13333.3600000000	12633.9200000000	12792.2800000000	\N
1215	4	2025-11-13 03:00:00+03	12792.2800000000	13100.5800000000	12354.8400000000	12373.2100000000	\N
1216	4	2025-11-14 03:00:00+03	12373.2100000000	12639.1900000000	12111.5100000000	12193.2600000000	\N
1217	4	2025-11-15 03:00:00+03	12193.2600000000	12468.8900000000	12035.5900000000	12366.1400000000	\N
1218	4	2025-11-16 03:00:00+03	12366.1400000000	12550.1600000000	11783.5300000000	11915.3200000000	\N
1219	4	2025-11-17 03:00:00+03	11915.3200000000	12447.5200000000	11414.6500000000	11488.8700000000	\N
1220	4	2025-11-18 03:00:00+03	11488.8700000000	12042.2600000000	11395.9700000000	11922.7700000000	\N
1221	4	2025-11-19 03:00:00+03	11922.7700000000	11947.2500000000	11155.9800000000	11363.0600000000	\N
1222	4	2025-11-20 03:00:00+03	11363.0600000000	11876.6500000000	10929.3200000000	11057.4900000000	\N
1223	4	2025-11-21 03:00:00+03	11057.4900000000	11324.3000000000	10230.3800000000	10482.7000000000	\N
1224	4	2025-11-22 03:00:00+03	10482.7000000000	10623.0000000000	10216.6700000000	10302.1800000000	\N
1225	4	2025-11-23 03:00:00+03	10302.1800000000	10629.0700000000	10301.3500000000	10582.7600000000	\N
1226	4	2025-11-24 03:00:00+03	10582.7600000000	10968.3200000000	10391.1000000000	10949.9400000000	\N
1227	4	2025-11-25 03:00:00+03	10949.9400000000	10951.6800000000	10504.7000000000	10835.0900000000	\N
1228	4	2025-11-26 03:00:00+03	10835.0900000000	11063.7400000000	10638.8600000000	11031.0600000000	\N
1229	4	2025-11-27 03:00:00+03	11031.0600000000	11152.3900000000	10904.3100000000	11103.3600000000	\N
1230	4	2025-11-28 03:00:00+03	11103.3600000000	11158.4000000000	10826.6300000000	10955.9400000000	\N
1231	4	2025-11-29 03:00:00+03	10955.9400000000	10998.0900000000	10699.5200000000	10751.0200000000	\N
1232	4	2025-11-30 03:00:00+03	10751.0200000000	10804.4800000000	10607.5400000000	10677.6300000000	\N
1233	4	2025-12-01 03:00:00+03	10677.6300000000	10697.6900000000	9620.1800000000	9715.1500000000	\N
1234	4	2025-12-02 03:00:00+03	9715.1500000000	10539.8800000000	9713.2300000000	10388.0000000000	\N
1235	4	2025-12-03 03:00:00+03	10388.0000000000	10739.5600000000	10391.2700000000	10668.5800000000	\N
1236	4	2025-12-04 03:00:00+03	10668.5800000000	10856.2200000000	10367.5800000000	10552.7100000000	\N
1237	4	2025-12-05 03:00:00+03	10552.7100000000	10591.7800000000	9920.3100000000	9999.2700000000	\N
1238	4	2025-12-06 03:00:00+03	9999.2700000000	10223.0300000000	9993.7400000000	10153.6300000000	\N
1239	4	2025-12-07 03:00:00+03	10153.6300000000	10292.7800000000	9828.3100000000	10275.1800000000	\N
1240	4	2025-12-08 03:00:00+03	10275.1800000000	10356.5100000000	9920.6600000000	10218.9600000000	\N
1241	4	2025-12-09 03:00:00+03	10218.9600000000	10778.7900000000	10081.1600000000	10554.7500000000	\N
1242	4	2025-12-10 03:00:00+03	10554.7500000000	10714.2100000000	10282.4900000000	10478.4400000000	\N
1243	4	2025-12-11 03:00:00+03	10478.4400000000	10500.7500000000	9790.7200000000	10023.9200000000	\N
1244	4	2025-12-12 03:00:00+03	10023.9200000000	10186.0200000000	9623.6600000000	9764.1400000000	\N
1245	4	2025-12-13 03:00:00+03	9764.1400000000	9978.6500000000	9759.7500000000	9918.9500000000	\N
1246	4	2025-12-14 03:00:00+03	9918.9500000000	9967.9000000000	9604.3200000000	9622.8600000000	\N
1247	4	2025-12-15 03:00:00+03	9622.8600000000	9806.9300000000	9088.5300000000	9169.0300000000	\N
1248	4	2025-12-16 03:00:00+03	9169.0300000000	9351.4800000000	9085.0700000000	9271.3600000000	\N
1249	4	2025-12-17 03:00:00+03	9271.3600000000	9422.1900000000	8803.9300000000	8861.0700000000	\N
1250	4	2025-12-18 03:00:00+03	8861.0700000000	9094.1800000000	8461.1300000000	8489.1700000000	\N
1251	4	2025-12-19 03:00:00+03	8489.1700000000	9125.4400000000	8476.9400000000	9104.5400000000	\N
1252	4	2025-12-20 03:00:00+03	9104.5400000000	9201.0700000000	9007.7200000000	9201.0700000000	\N
1253	4	2025-12-21 03:00:00+03	9201.0700000000	9228.2700000000	8925.8700000000	8975.5500000000	\N
1254	4	2025-12-22 03:00:00+03	8975.5500000000	9201.6300000000	8932.1100000000	8992.3800000000	\N
1255	4	2025-12-23 03:00:00+03	8992.3800000000	9132.7400000000	8777.4600000000	8986.9900000000	\N
1256	4	2025-12-24 03:00:00+03	8986.9900000000	9016.2700000000	8801.7400000000	8929.7100000000	\N
1257	4	2025-12-25 03:00:00+03	8929.7100000000	9116.3900000000	8923.9400000000	9081.0200000000	\N
1258	4	2025-12-26 03:00:00+03	9081.0200000000	9131.0900000000	8843.3400000000	9052.7800000000	\N
1259	4	2025-12-27 03:00:00+03	9052.7800000000	9207.7600000000	9030.2500000000	9190.5100000000	\N
1260	4	2025-12-28 03:00:00+03	9190.5100000000	9417.8800000000	9188.0900000000	9254.8000000000	\N
1261	4	2025-12-29 03:00:00+03	9254.8000000000	9563.5200000000	9115.6300000000	9124.9200000000	\N
1262	4	2025-12-30 03:00:00+03	9124.9200000000	9181.7000000000	9045.5400000000	9057.7300000000	\N
1263	4	2025-12-31 03:00:00+03	9057.7300000000	9113.8600000000	8838.5800000000	8903.3000000000	\N
1264	4	2026-01-01 03:00:00+03	8903.3000000000	9346.8600000000	8857.8300000000	9303.2400000000	\N
1265	4	2026-01-02 03:00:00+03	9303.2400000000	9767.5100000000	9290.8300000000	9721.3300000000	\N
1266	4	2026-01-03 03:00:00+03	9721.3300000000	9917.0400000000	9600.3500000000	9831.1500000000	\N
1267	4	2026-01-04 03:00:00+03	9831.1500000000	10347.8200000000	9818.2500000000	10206.5600000000	\N
1268	4	2026-01-05 03:00:00+03	10206.5600000000	10481.0000000000	10061.6800000000	10413.7600000000	\N
1269	4	2026-01-06 03:00:00+03	10413.7600000000	10774.8800000000	10206.7800000000	10429.7100000000	\N
1270	4	2026-01-07 03:00:00+03	10429.7100000000	10582.0600000000	10121.4300000000	10170.7700000000	\N
1271	4	2026-01-08 03:00:00+03	10170.7700000000	10255.1300000000	9826.3900000000	10026.4400000000	\N
1272	4	2026-01-09 03:00:00+03	10026.4400000000	10315.0300000000	9972.1800000000	10035.4800000000	\N
1273	4	2026-01-10 03:00:00+03	10035.4800000000	10291.5900000000	10007.5900000000	10269.3400000000	\N
1274	4	2026-01-11 03:00:00+03	10269.3400000000	10276.6800000000	10057.9300000000	10096.5300000000	\N
1275	4	2026-01-12 03:00:00+03	10096.5300000000	10293.1000000000	9799.2600000000	9899.5900000000	\N
1276	4	2026-01-13 03:00:00+03	9899.5900000000	10401.8500000000	9820.1300000000	10342.4500000000	\N
1277	4	2026-01-14 03:00:00+03	10342.4500000000	10652.3600000000	10347.1900000000	10451.8000000000	\N
1278	4	2026-01-15 03:00:00+03	10451.8000000000	10468.6000000000	9885.4900000000	9896.6900000000	\N
1279	4	2026-01-16 03:00:00+03	9896.6900000000	10062.7200000000	9787.2600000000	10034.5000000000	\N
1280	4	2026-01-17 03:00:00+03	10034.5000000000	10291.4500000000	10029.1200000000	10220.6100000000	\N
1281	4	2026-01-18 03:00:00+03	10220.6100000000	10224.2200000000	9990.0900000000	10116.1500000000	\N
1282	4	2026-01-19 03:00:00+03	10116.1500000000	10149.0200000000	9172.7000000000	9558.2800000000	\N
1283	4	2026-01-20 03:00:00+03	9558.2800000000	9564.5200000000	9090.6000000000	9193.6100000000	\N
1284	4	2026-01-21 03:00:00+03	9193.6100000000	9414.3900000000	9050.5400000000	9376.4900000000	\N
1285	4	2026-01-22 03:00:00+03	9376.4900000000	9429.6900000000	9153.4100000000	9220.7100000000	\N
1286	4	2026-01-23 03:00:00+03	9220.7100000000	9372.8100000000	9209.3500000000	9212.9800000000	\N
1287	4	2026-01-24 03:00:00+03	9212.9800000000	9253.2900000000	9178.2500000000	9183.7500000000	\N
1288	4	2026-01-25 03:00:00+03	9183.7500000000	9188.4200000000	8846.1600000000	8846.1600000000	\N
1289	4	2026-01-26 03:00:00+03	8846.1600000000	9068.3800000000	8812.7800000000	9014.3100000000	\N
1290	4	2026-01-27 03:00:00+03	9014.3100000000	9150.5700000000	8964.5800000000	9145.0400000000	\N
1291	4	2026-01-28 03:00:00+03	9145.0400000000	9152.8800000000	9028.0200000000	9094.5900000000	\N
1292	4	2026-01-29 03:00:00+03	9094.5900000000	9134.9800000000	8475.9900000000	8524.9600000000	\N
1293	4	2026-01-30 03:00:00+03	8524.9600000000	8596.0600000000	8399.2100000000	8514.2400000000	\N
1294	4	2026-01-31 03:00:00+03	8514.2400000000	8462.0400000000	7466.4200000000	7732.1600000000	\N
1295	4	2026-02-01 03:00:00+03	7732.1600000000	7994.0700000000	7768.2900000000	7814.7000000000	\N
1296	4	2026-02-02 03:00:00+03	7814.7000000000	8068.9500000000	7780.9100000000	8001.1400000000	\N
1297	4	2026-02-03 03:00:00+03	8001.1400000000	8065.8700000000	7700.7500000000	8065.8700000000	\N
1298	4	2026-02-04 03:00:00+03	8065.8700000000	7988.5000000000	7690.2400000000	7854.9200000000	\N
1299	4	2026-02-05 03:00:00+03	7854.9200000000	7797.3600000000	6909.0300000000	6961.8600000000	\N
1300	4	2026-02-06 03:00:00+03	6961.8600000000	7569.7600000000	6930.4900000000	7399.1500000000	\N
1301	4	2026-02-07 03:00:00+03	7399.1500000000	7496.3500000000	7217.1900000000	7442.1400000000	\N
1302	4	2026-02-08 03:00:00+03	7442.1400000000	7452.8600000000	7191.8000000000	7269.3500000000	\N
1303	4	2026-02-09 03:00:00+03	7269.3500000000	7296.6800000000	7032.3500000000	7236.4700000000	\N
1304	4	2026-02-10 03:00:00+03	7236.4700000000	7249.9200000000	7006.5400000000	7041.2700000000	\N
1305	4	2026-02-11 03:00:00+03	7041.2700000000	7158.9500000000	6854.7300000000	6982.2000000000	\N
1306	4	2026-02-12 03:00:00+03	6982.2000000000	7163.2600000000	6953.5100000000	7001.6400000000	\N
1307	4	2026-02-13 03:00:00+03	7001.6400000000	7353.9800000000	6989.3000000000	7334.5100000000	\N
1308	4	2026-02-14 03:00:00+03	7334.5100000000	7815.0400000000	7332.3900000000	7815.0400000000	\N
1309	4	2026-02-15 03:00:00+03	7815.0400000000	7833.6000000000	7385.8200000000	7404.6800000000	\N
1310	4	2026-02-16 03:00:00+03	7404.6800000000	7485.9800000000	7412.5600000000	7440.6600000000	\N
1311	4	2026-02-17 03:00:00+03	7440.6600000000	7527.9600000000	7360.4200000000	7438.2400000000	\N
1312	4	2026-02-18 03:00:00+03	7438.2400000000	7470.7500000000	7302.0200000000	7302.0200000000	\N
1313	4	2026-02-19 03:00:00+03	7302.0200000000	7251.2300000000	7011.0300000000	7109.6800000000	\N
1314	4	2026-02-20 03:00:00+03	7109.6800000000	7258.8800000000	7104.3800000000	7104.3800000000	\N
1315	4	2026-02-21 03:00:00+03	7104.3800000000	7416.2700000000	7244.3500000000	7352.2600000000	\N
1316	4	2026-02-22 03:00:00+03	7352.2600000000	7317.9400000000	7075.0700000000	7082.7300000000	\N
1317	4	2026-02-23 03:00:00+03	7082.7300000000	7121.4900000000	6837.3600000000	6846.1100000000	\N
1318	4	2026-02-24 03:00:00+03	6846.1100000000	6888.6700000000	6755.0100000000	6860.3000000000	\N
1319	4	2026-02-25 03:00:00+03	6860.3000000000	7600.4600000000	6829.0400000000	7595.6000000000	\N
1320	4	2026-02-26 03:00:00+03	7595.6000000000	7666.6500000000	7065.0200000000	7163.0900000000	\N
1321	4	2026-02-27 03:00:00+03	7163.0900000000	7337.9400000000	6969.7100000000	6999.8800000000	\N
1322	4	2026-02-28 03:00:00+03	6999.8800000000	7031.8400000000	6622.3600000000	7031.2700000000	\N
1323	4	2026-03-01 03:00:00+03	7031.2700000000	7193.5700000000	6733.3900000000	6744.5100000000	\N
1324	4	2026-03-02 03:00:00+03	6744.5100000000	7181.9400000000	6817.0400000000	7016.9200000000	\N
1325	4	2026-03-03 03:00:00+03	7016.9200000000	7010.9400000000	6787.6000000000	6943.0300000000	\N
1326	4	2026-03-04 03:00:00+03	6943.0300000000	7311.5400000000	6890.4400000000	7271.2100000000	\N
1327	4	2026-03-05 03:00:00+03	7271.2100000000	7170.6700000000	6984.4800000000	7120.2600000000	\N
1328	4	2026-03-06 03:00:00+03	7120.2600000000	7124.7400000000	6877.0300000000	6877.0300000000	\N
1329	4	2026-03-07 03:00:00+03	6877.0300000000	6898.5200000000	6762.0100000000	6771.4300000000	\N
1330	4	2026-03-08 03:00:00+03	6771.4300000000	6793.9900000000	6676.0100000000	6760.7200000000	\N
1331	4	2026-03-09 03:00:00+03	6760.7200000000	6937.9000000000	6657.3100000000	6911.2900000000	\N
1353	6	2024-06-16 03:00:00+03	4650.6200000000	4693.7700000000	4610.9500000000	4692.6800000000	\N
1354	6	2024-06-17 03:00:00+03	4692.6800000000	4714.3100000000	4548.2900000000	4640.8700000000	\N
1355	6	2024-06-18 03:00:00+03	4640.8700000000	4600.6000000000	4290.5100000000	4311.3300000000	\N
1356	6	2024-06-19 03:00:00+03	4311.3300000000	4500.6000000000	4391.7100000000	4418.7500000000	\N
1357	6	2024-06-20 03:00:00+03	4418.7500000000	4508.5100000000	4387.1500000000	4403.4700000000	\N
1358	6	2024-06-21 03:00:00+03	4403.4700000000	4392.9500000000	4299.6200000000	4356.3100000000	\N
1359	6	2024-06-22 03:00:00+03	4356.3100000000	4387.2100000000	4343.0900000000	4386.7800000000	\N
1360	6	2024-06-23 03:00:00+03	4386.7800000000	4424.8800000000	4329.4300000000	4365.0900000000	\N
1361	6	2024-06-24 03:00:00+03	4365.0900000000	4351.0000000000	4183.9600000000	4404.2800000000	\N
1362	6	2024-06-25 03:00:00+03	4404.2800000000	4429.5400000000	4247.1000000000	4414.8500000000	\N
1363	6	2024-06-26 03:00:00+03	4414.8500000000	4426.7800000000	4342.3300000000	4342.3300000000	\N
1364	6	2024-06-27 03:00:00+03	4342.3300000000	4521.2500000000	4327.8000000000	4512.2000000000	\N
1365	6	2024-06-28 03:00:00+03	4512.2000000000	4535.0700000000	4407.8600000000	4407.8600000000	\N
1366	6	2024-06-29 03:00:00+03	4407.8600000000	4443.7900000000	4384.4400000000	4421.6800000000	\N
1367	6	2024-06-30 03:00:00+03	4421.6800000000	4468.2400000000	4369.2100000000	4461.9000000000	\N
1368	6	2024-07-01 03:00:00+03	4461.9000000000	4548.1300000000	4453.6600000000	4527.5900000000	\N
1369	6	2024-07-02 03:00:00+03	4527.5900000000	4590.0800000000	4499.5700000000	4580.3300000000	\N
1370	6	2024-07-03 03:00:00+03	4580.3300000000	4580.3300000000	4335.4500000000	4335.4500000000	\N
1371	6	2024-07-04 03:00:00+03	4335.4500000000	4239.5700000000	4088.5100000000	4111.8200000000	\N
1372	6	2024-07-05 03:00:00+03	4111.8200000000	4145.0900000000	3679.3000000000	4009.3800000000	\N
1373	6	2024-07-06 03:00:00+03	4009.3800000000	4269.9200000000	4008.2900000000	4269.9200000000	\N
1374	6	2024-07-07 03:00:00+03	4269.9200000000	4288.4600000000	4055.5600000000	4082.0600000000	\N
1375	6	2024-07-08 03:00:00+03	4082.0600000000	4197.4500000000	3884.9300000000	4104.2800000000	\N
1376	6	2024-07-09 03:00:00+03	4104.2800000000	4191.7100000000	4085.4200000000	4189.3200000000	\N
1377	6	2024-07-10 03:00:00+03	4189.3200000000	4264.7200000000	4140.0000000000	4179.8400000000	\N
1378	6	2024-07-11 03:00:00+03	4179.8400000000	4289.2700000000	4179.8400000000	4189.1600000000	\N
1379	6	2024-07-12 03:00:00+03	4189.1600000000	4283.1400000000	4159.0800000000	4254.6900000000	\N
1380	6	2024-07-13 03:00:00+03	4254.6900000000	4396.3700000000	4249.3200000000	4396.3700000000	\N
1381	6	2024-07-14 03:00:00+03	4396.3700000000	4441.0800000000	4361.4600000000	4413.4400000000	\N
1382	6	2024-07-15 03:00:00+03	4413.4400000000	4657.4000000000	4461.5200000000	4651.4400000000	\N
1383	6	2024-07-16 03:00:00+03	4651.4400000000	4774.7400000000	4588.8900000000	4765.0900000000	\N
1384	6	2024-07-17 03:00:00+03	4765.0900000000	4817.6200000000	4694.1500000000	4715.8800000000	\N
1385	6	2024-07-18 03:00:00+03	4715.8800000000	4737.0200000000	4594.9100000000	4612.6300000000	\N
1386	6	2024-07-19 03:00:00+03	4612.6300000000	4852.1400000000	4608.6200000000	4852.1400000000	\N
1387	6	2024-07-20 03:00:00+03	4852.1400000000	4925.0400000000	4816.2100000000	4897.8900000000	\N
1388	6	2024-07-21 03:00:00+03	4897.8900000000	4950.0800000000	4860.4900000000	4949.1600000000	\N
1389	6	2024-07-22 03:00:00+03	4949.1600000000	5058.7500000000	4935.6600000000	5028.9400000000	\N
1390	6	2024-07-23 03:00:00+03	5028.9400000000	4976.7500000000	4769.9700000000	4806.2900000000	\N
1391	6	2024-07-24 03:00:00+03	4806.2900000000	4943.8500000000	4786.6100000000	4868.1800000000	\N
1392	6	2024-07-25 03:00:00+03	4868.1800000000	4844.9300000000	4654.5300000000	4666.5000000000	\N
1393	6	2024-07-26 03:00:00+03	4666.5000000000	4890.1400000000	4728.0200000000	4890.1400000000	\N
1394	6	2024-07-27 03:00:00+03	4890.1400000000	4986.2900000000	4871.9200000000	4895.9300000000	\N
1395	6	2024-07-28 03:00:00+03	4895.9300000000	4926.4500000000	4845.5800000000	4894.4200000000	\N
1396	6	2024-07-29 03:00:00+03	4894.4200000000	5024.5000000000	4851.8200000000	4891.9800000000	\N
1397	6	2024-07-30 03:00:00+03	4891.9800000000	4868.3500000000	4795.9900000000	4830.1900000000	\N
1398	6	2024-07-31 03:00:00+03	4830.1900000000	4898.7000000000	4785.3700000000	4876.2600000000	\N
1399	6	2024-08-01 03:00:00+03	4876.2600000000	4754.5800000000	4472.6800000000	4521.5200000000	\N
1400	6	2024-08-02 03:00:00+03	4521.5200000000	4649.3800000000	4360.4300000000	4360.4300000000	\N
1401	6	2024-08-03 03:00:00+03	4360.4300000000	4366.6700000000	4109.9700000000	4109.9700000000	\N
1402	6	2024-08-04 03:00:00+03	4109.9700000000	4210.8900000000	3943.4700000000	4079.6700000000	\N
1403	6	2024-08-05 03:00:00+03	4079.6700000000	4047.4300000000	3310.3000000000	3686.5000000000	\N
1404	6	2024-08-06 03:00:00+03	3686.5000000000	4009.9700000000	3721.1400000000	3983.4100000000	\N
1405	6	2024-08-07 03:00:00+03	3983.4100000000	4076.4200000000	3863.7400000000	3902.6000000000	\N
1406	6	2024-08-08 03:00:00+03	3902.6000000000	4281.7900000000	3958.6400000000	4240.8700000000	\N
1407	6	2024-08-09 03:00:00+03	4240.8700000000	4368.7800000000	4183.9600000000	4235.3900000000	\N
1408	6	2024-08-10 03:00:00+03	4235.3900000000	4300.4900000000	4238.8100000000	4229.8100000000	\N
1409	6	2024-08-11 03:00:00+03	4229.8100000000	4357.1300000000	4128.0800000000	4132.2000000000	\N
1410	6	2024-08-12 03:00:00+03	4132.2000000000	4229.4300000000	4048.0800000000	4123.3100000000	\N
1411	6	2024-08-13 03:00:00+03	4123.3100000000	4238.5900000000	4130.3500000000	4206.0700000000	\N
1412	6	2024-08-14 03:00:00+03	4206.0700000000	4273.6600000000	4174.3600000000	4200.0500000000	\N
1413	6	2024-08-15 03:00:00+03	4200.0500000000	4219.1900000000	4071.3300000000	4071.3300000000	\N
1414	6	2024-08-16 03:00:00+03	4071.3300000000	4171.3800000000	4058.2700000000	4073.5000000000	\N
1415	6	2024-08-17 03:00:00+03	4073.5000000000	4195.0700000000	4104.1700000000	4195.0700000000	\N
1416	6	2024-08-18 03:00:00+03	4195.0700000000	4246.4500000000	4169.8100000000	4246.4500000000	\N
1417	6	2024-08-19 03:00:00+03	4246.4500000000	4283.9600000000	4168.0200000000	4296.1000000000	\N
1418	6	2024-08-20 03:00:00+03	4296.1000000000	4410.4600000000	4296.1000000000	4381.3600000000	\N
1419	6	2024-08-21 03:00:00+03	4381.3600000000	4417.9400000000	4300.4900000000	4417.9400000000	\N
1420	6	2024-08-22 03:00:00+03	4417.9400000000	4446.4500000000	4342.0100000000	4422.4900000000	\N
1421	6	2024-08-23 03:00:00+03	4422.4900000000	4564.3400000000	4430.2400000000	4564.3400000000	\N
1422	6	2024-08-24 03:00:00+03	4564.3400000000	4692.0300000000	4570.7900000000	4692.0300000000	\N
1423	6	2024-08-25 03:00:00+03	4692.0300000000	4612.7900000000	4484.3900000000	4567.9700000000	\N
1424	6	2024-08-26 03:00:00+03	4567.9700000000	4600.1600000000	4426.7200000000	4426.7200000000	\N
1425	6	2024-08-27 03:00:00+03	4426.7200000000	4469.7600000000	4365.5300000000	4399.0200000000	\N
1426	6	2024-08-28 03:00:00+03	4399.0200000000	4295.5000000000	4167.0500000000	4208.5600000000	\N
1427	6	2024-08-29 03:00:00+03	4208.5600000000	4279.5700000000	4157.4500000000	4171.4900000000	\N
1428	6	2024-08-30 03:00:00+03	4171.4900000000	4194.2500000000	4050.7300000000	4119.0800000000	\N
1429	6	2024-08-31 03:00:00+03	4119.0800000000	4181.2500000000	4118.4300000000	4118.4300000000	\N
1430	6	2024-09-01 03:00:00+03	4118.4300000000	4128.6700000000	4012.1400000000	4059.0800000000	\N
1431	6	2024-09-02 03:00:00+03	4059.0800000000	4038.4300000000	3909.8600000000	4003.2000000000	\N
1432	6	2024-09-05 03:00:00+03	4003.2000000000	4003.2000000000	3863.5200000000	3884.2300000000	\N
1433	6	2024-09-06 03:00:00+03	3884.2300000000	3943.5800000000	3664.1200000000	3670.1400000000	\N
1434	6	2024-09-07 03:00:00+03	3670.1400000000	3875.9300000000	3645.3100000000	3842.3800000000	\N
1435	6	2024-09-08 03:00:00+03	3842.3800000000	3915.3900000000	3819.0200000000	3835.3400000000	\N
1436	6	2024-09-09 03:00:00+03	3835.3400000000	4058.8100000000	3897.6700000000	4058.8100000000	\N
1437	6	2024-09-10 03:00:00+03	4058.8100000000	4084.6600000000	4008.1800000000	4050.5100000000	\N
1438	6	2024-09-11 03:00:00+03	4050.5100000000	4084.6600000000	3937.0200000000	4084.6600000000	\N
1439	6	2024-09-12 03:00:00+03	4084.6600000000	4150.7900000000	4043.6300000000	4070.0800000000	\N
1440	6	2024-09-13 03:00:00+03	4070.0800000000	4236.7500000000	4061.0300000000	4061.0300000000	\N
1441	6	2024-09-14 03:00:00+03	4061.0300000000	4268.5100000000	4038.2100000000	4070.0800000000	\N
1442	6	2024-09-15 03:00:00+03	4070.0800000000	4250.3000000000	4055.1100000000	4234.4700000000	\N
1443	6	2024-09-16 03:00:00+03	4234.4700000000	4234.4700000000	4059.8400000000	4193.7100000000	\N
1444	6	2024-09-17 03:00:00+03	4193.7100000000	4198.4500000000	4052.0900000000	4129.7000000000	\N
1445	6	2024-09-18 03:00:00+03	4129.7000000000	4135.6000000000	4035.0100000000	4076.6900000000	\N
1446	6	2024-09-19 03:00:00+03	4076.6900000000	4342.6600000000	4326.2900000000	4334.8000000000	\N
1447	6	2024-09-20 03:00:00+03	4334.8000000000	4452.1400000000	4271.5400000000	4354.8000000000	\N
1448	6	2024-09-21 03:00:00+03	4354.8000000000	4458.5400000000	4323.4100000000	4435.5000000000	\N
1449	6	2024-09-22 03:00:00+03	4435.5000000000	4480.8700000000	4335.7700000000	4396.5900000000	\N
1450	6	2024-09-23 03:00:00+03	4396.5900000000	4490.8900000000	4307.0500000000	4448.3500000000	\N
1451	6	2024-09-24 03:00:00+03	4448.3500000000	4532.3000000000	4410.5100000000	4512.4100000000	\N
1452	6	2024-09-25 03:00:00+03	4512.4100000000	4580.1100000000	4462.1700000000	4493.1200000000	\N
1453	6	2024-09-26 03:00:00+03	4493.1200000000	4703.6900000000	4410.8900000000	4601.4600000000	\N
1454	6	2024-09-27 03:00:00+03	4601.4600000000	4804.1700000000	4598.0500000000	4730.5700000000	\N
1455	6	2024-09-28 03:00:00+03	4730.5700000000	4786.4500000000	4639.9500000000	4725.2600000000	\N
1456	6	2024-09-29 03:00:00+03	4725.2600000000	4789.8600000000	4668.5100000000	4763.4100000000	\N
1457	6	2024-09-30 03:00:00+03	4763.4100000000	4789.4900000000	4543.3100000000	4596.3700000000	\N
1458	6	2024-10-01 03:00:00+03	4596.3700000000	4635.8300000000	4220.4300000000	4303.0900000000	\N
1459	6	2024-10-02 03:00:00+03	4303.0900000000	4400.3800000000	4169.3800000000	4229.7000000000	\N
1460	6	2024-10-03 03:00:00+03	4229.7000000000	4244.5000000000	4058.3700000000	4133.5000000000	\N
1461	6	2024-10-04 03:00:00+03	4133.5000000000	4302.7600000000	4115.7700000000	4291.6000000000	\N
1462	6	2024-10-05 03:00:00+03	4291.6000000000	4320.0000000000	4233.0600000000	4234.7400000000	\N
1463	6	2024-10-06 03:00:00+03	4234.7400000000	4359.7300000000	4230.5700000000	4333.1700000000	\N
1464	6	2024-10-07 03:00:00+03	4333.1700000000	4458.7000000000	4305.6900000000	4343.9000000000	\N
1465	6	2024-10-08 03:00:00+03	4343.9000000000	4377.2900000000	4249.0000000000	4339.8400000000	\N
1466	6	2024-10-09 03:00:00+03	4339.8400000000	4355.0700000000	4219.5700000000	4223.6300000000	\N
1467	6	2024-10-10 03:00:00+03	4223.6300000000	4271.7100000000	4133.1200000000	4191.2700000000	\N
1468	6	2024-10-11 03:00:00+03	4191.2700000000	4380.4300000000	4185.3100000000	4364.3400000000	\N
1469	6	2024-10-12 03:00:00+03	4364.3400000000	4411.9200000000	4344.8200000000	4393.1700000000	\N
1470	6	2024-10-13 03:00:00+03	4393.1700000000	4401.4600000000	4306.7800000000	4344.1200000000	\N
1471	6	2024-10-14 03:00:00+03	4344.1200000000	4543.6300000000	4337.5600000000	4521.2500000000	\N
1472	6	2024-10-15 03:00:00+03	4521.2500000000	4593.6600000000	4391.6500000000	4450.9500000000	\N
1473	6	2024-10-16 03:00:00+03	4450.9500000000	4584.9900000000	4443.3600000000	4564.5500000000	\N
1474	6	2024-10-17 03:00:00+03	4564.5500000000	4574.2500000000	4413.2800000000	4469.1600000000	\N
1475	6	2024-10-18 03:00:00+03	4469.1600000000	4610.0800000000	4465.9100000000	4568.8300000000	\N
1476	6	2024-10-19 03:00:00+03	4568.8300000000	4628.8900000000	4562.9300000000	4590.8400000000	\N
1477	6	2024-10-20 03:00:00+03	4590.8400000000	4665.6900000000	4580.9200000000	4658.8600000000	\N
1478	6	2024-10-21 03:00:00+03	4658.8600000000	4785.0900000000	4609.4900000000	4685.6900000000	\N
1479	6	2024-10-22 03:00:00+03	4685.6900000000	4718.6400000000	4605.4200000000	4670.7300000000	\N
1480	6	2024-10-23 03:00:00+03	4670.7300000000	4688.2400000000	4499.8900000000	4641.4100000000	\N
1481	6	2024-10-24 03:00:00+03	4641.4100000000	4728.6200000000	4600.3300000000	4722.7100000000	\N
1482	6	2024-10-25 03:00:00+03	4722.7100000000	4753.3900000000	4505.6900000000	4547.5300000000	\N
1483	6	2024-10-26 03:00:00+03	4547.5300000000	4604.5500000000	4348.4000000000	4587.2600000000	\N
1484	6	2024-10-27 03:00:00+03	4587.2600000000	4666.8800000000	4561.0300000000	4640.7600000000	\N
1485	6	2024-10-28 03:00:00+03	4640.7600000000	4737.1300000000	4599.3000000000	4731.0000000000	\N
1486	6	2024-10-29 03:00:00+03	4731.0000000000	4919.5100000000	4732.0900000000	4826.1200000000	\N
1487	6	2024-10-30 03:00:00+03	4826.1200000000	4881.1900000000	4745.5800000000	4810.0300000000	\N
1488	6	2024-10-31 03:00:00+03	4810.0300000000	4814.3100000000	4573.6000000000	4594.9600000000	\N
1489	6	2024-11-01 03:00:00+03	4594.9600000000	4719.6700000000	4538.2700000000	4583.3600000000	\N
1490	6	2024-11-02 03:00:00+03	4583.3600000000	4633.2200000000	4499.5700000000	4568.7800000000	\N
1491	6	2024-11-03 03:00:00+03	4568.7800000000	4571.1700000000	4339.2400000000	4452.9500000000	\N
1492	6	2024-11-04 03:00:00+03	4452.9500000000	4516.7500000000	4396.5900000000	4400.9800000000	\N
1493	6	2024-11-05 03:00:00+03	4400.9800000000	4621.6300000000	4322.1100000000	4554.9600000000	\N
1494	6	2024-11-06 03:00:00+03	4554.9600000000	4964.3400000000	4506.5000000000	4927.5900000000	\N
1495	6	2024-11-07 03:00:00+03	4927.5900000000	5082.1700000000	4894.8500000000	5080.9800000000	\N
1496	6	2024-11-08 03:00:00+03	5080.9800000000	5192.6800000000	5019.0200000000	5113.9300000000	\N
1497	6	2024-11-09 03:00:00+03	5113.9300000000	5295.6100000000	5087.2100000000	5227.2600000000	\N
1498	6	2024-11-10 03:00:00+03	5227.2600000000	5901.3600000000	5225.5300000000	5538.0500000000	\N
1499	6	2024-11-11 03:00:00+03	5538.0500000000	6065.0900000000	5425.5300000000	6002.4400000000	\N
1500	6	2024-11-12 03:00:00+03	6002.4400000000	6524.1200000000	5804.8800000000	6136.9600000000	\N
1501	6	2024-11-13 03:00:00+03	6136.9600000000	6350.8900000000	5742.2800000000	6041.5700000000	\N
1502	6	2024-11-14 03:00:00+03	6041.5700000000	6302.5500000000	5894.3600000000	6172.6800000000	\N
1503	6	2024-11-15 03:00:00+03	6172.6800000000	6313.7100000000	5943.2000000000	6308.6200000000	\N
1504	6	2024-11-16 03:00:00+03	6308.6200000000	6791.2200000000	6295.1800000000	6660.3300000000	\N
1505	6	2024-11-17 03:00:00+03	6660.3300000000	6787.3200000000	6330.3000000000	6638.2100000000	\N
1506	6	2024-11-18 03:00:00+03	6638.2100000000	6949.2700000000	6536.5900000000	6783.7900000000	\N
1507	6	2024-11-19 03:00:00+03	6783.7900000000	6943.2500000000	6740.8700000000	6810.0800000000	\N
1508	6	2024-11-20 03:00:00+03	6810.0800000000	6952.7400000000	6587.5300000000	6726.3400000000	\N
1509	6	2024-11-21 03:00:00+03	6726.3400000000	7162.5500000000	6568.5100000000	7057.0000000000	\N
1510	6	2024-11-22 03:00:00+03	7057.0000000000	7551.8700000000	7015.6800000000	7498.3500000000	\N
1511	6	2024-11-23 03:00:00+03	7498.3500000000	8153.1400000000	7471.8900000000	7735.8300000000	\N
1512	6	2024-11-24 03:00:00+03	7735.8300000000	7968.3600000000	7261.4600000000	7639.1400000000	\N
1513	6	2024-11-25 03:00:00+03	7639.1400000000	7801.2600000000	7332.6000000000	7508.2500000000	\N
1514	6	2024-11-26 03:00:00+03	7508.2500000000	7544.4200000000	6866.4600000000	7189.9000000000	\N
1515	6	2024-11-27 03:00:00+03	7189.9000000000	7628.1300000000	7114.5500000000	7581.3200000000	\N
1516	6	2024-11-28 03:00:00+03	7581.3200000000	7712.8400000000	7387.7000000000	7500.7500000000	\N
1517	6	2024-11-29 03:00:00+03	7500.7500000000	8038.3300000000	7503.1900000000	7938.3000000000	\N
1518	6	2024-11-30 03:00:00+03	7938.3000000000	8289.5300000000	7887.6400000000	8127.5500000000	\N
1519	6	2024-12-01 03:00:00+03	8127.5500000000	8362.4400000000	7922.4800000000	8353.2200000000	\N
1520	6	2024-12-02 03:00:00+03	8353.2200000000	8978.2700000000	8058.8100000000	8707.1000000000	\N
1521	6	2024-12-03 03:00:00+03	8707.1000000000	9036.3100000000	8090.1900000000	8939.4000000000	\N
1522	6	2024-12-04 03:00:00+03	8939.4000000000	9402.7600000000	8656.9600000000	8899.1900000000	\N
1523	6	2024-12-05 03:00:00+03	8899.1900000000	9125.0400000000	8409.9200000000	8758.0500000000	\N
1524	6	2024-12-06 03:00:00+03	8758.0500000000	9006.2300000000	8561.2500000000	8965.0400000000	\N
1525	6	2024-12-07 03:00:00+03	8965.0400000000	9239.5700000000	8887.4300000000	9209.3200000000	\N
1526	6	2024-12-08 03:00:00+03	9209.3200000000	9295.3900000000	8936.9100000000	9117.1800000000	\N
1527	6	2024-12-09 03:00:00+03	9117.1800000000	9243.0900000000	8243.4100000000	8271.4900000000	\N
1528	6	2024-12-10 03:00:00+03	8271.4900000000	8271.6500000000	7346.0200000000	8029.1600000000	\N
1529	6	2024-12-11 03:00:00+03	8029.1600000000	8611.1100000000	7864.1200000000	8540.1600000000	\N
1530	6	2024-12-12 03:00:00+03	8540.1600000000	8782.0100000000	8443.3600000000	8586.0700000000	\N
1531	6	2024-12-13 03:00:00+03	8586.0700000000	8675.7200000000	8323.5800000000	8595.0700000000	\N
1532	6	2024-12-14 03:00:00+03	8595.0700000000	8737.4000000000	8260.7000000000	8272.7900000000	\N
1533	6	2024-12-15 03:00:00+03	8272.7900000000	8529.9700000000	8273.9800000000	8468.0800000000	\N
1534	6	2024-12-16 03:00:00+03	8468.0800000000	8661.3000000000	8232.4100000000	8525.8000000000	\N
1535	6	2024-12-17 03:00:00+03	8525.8000000000	8842.9900000000	8358.5900000000	8673.9200000000	\N
1536	6	2024-12-18 03:00:00+03	8673.9200000000	8712.0600000000	7802.5100000000	7895.2400000000	\N
1537	6	2024-12-19 03:00:00+03	7895.2400000000	8101.7200000000	7194.1900000000	7383.9800000000	\N
1538	6	2024-12-20 03:00:00+03	7383.9800000000	7669.3700000000	6589.4300000000	7412.3000000000	\N
1539	6	2024-12-21 03:00:00+03	7412.3000000000	7885.3000000000	7212.5000000000	7336.7800000000	\N
1540	6	2024-12-22 03:00:00+03	7336.7800000000	7470.5200000000	7074.9100000000	7242.2700000000	\N
1541	6	2024-12-23 03:00:00+03	7242.2700000000	7520.8500000000	7046.2000000000	7518.8000000000	\N
1542	6	2024-12-24 03:00:00+03	7518.8000000000	7821.5600000000	7390.2200000000	7655.3300000000	\N
1543	6	2024-12-25 03:00:00+03	7655.3300000000	7844.7000000000	7624.1400000000	7709.9100000000	\N
1544	6	2024-12-26 03:00:00+03	7709.9100000000	7771.4600000000	7281.6300000000	7342.3000000000	\N
1545	6	2024-12-27 03:00:00+03	7342.3000000000	7587.4200000000	7242.2600000000	7378.5000000000	\N
1546	6	2024-12-28 03:00:00+03	7378.5000000000	7578.9200000000	7275.3100000000	7541.3600000000	\N
1547	6	2024-12-29 03:00:00+03	7541.3600000000	7605.8000000000	7312.7400000000	7371.9300000000	\N
1548	6	2024-12-30 03:00:00+03	7371.9300000000	7456.4200000000	7088.6000000000	7407.6500000000	\N
1549	6	2024-12-31 03:00:00+03	7407.6500000000	7491.5400000000	7156.5400000000	7319.8200000000	\N
1550	6	2025-01-01 03:00:00+03	7319.8200000000	7621.3600000000	7217.6700000000	7609.9200000000	\N
1551	6	2025-01-02 03:00:00+03	7609.9200000000	7955.2300000000	7577.7200000000	7854.5800000000	\N
1552	6	2025-01-03 03:00:00+03	7854.5800000000	8285.5300000000	7828.6200000000	8230.9500000000	\N
1553	6	2025-01-04 03:00:00+03	8230.9500000000	8318.1600000000	8118.9200000000	8235.9900000000	\N
1554	6	2025-01-05 03:00:00+03	8235.9900000000	8263.4100000000	8030.7900000000	8128.5600000000	\N
1555	6	2025-01-06 03:00:00+03	8128.5600000000	8403.0900000000	8064.2800000000	8320.0000000000	\N
1556	6	2025-01-07 03:00:00+03	8320.0000000000	8348.7300000000	7697.8300000000	7725.2000000000	\N
1557	6	2025-01-08 03:00:00+03	7725.2000000000	7821.0300000000	7324.2300000000	7575.2800000000	\N
1558	6	2025-01-09 03:00:00+03	7575.2800000000	7749.1600000000	7258.9700000000	7335.8300000000	\N
1559	6	2025-01-10 03:00:00+03	7335.8300000000	7624.2300000000	7306.7800000000	7540.5400000000	\N
1560	6	2025-01-11 03:00:00+03	7540.5400000000	7861.5700000000	7469.5900000000	7861.0300000000	\N
1561	6	2025-01-12 03:00:00+03	7861.0300000000	7888.2900000000	7637.9400000000	7763.7900000000	\N
1562	6	2025-01-13 03:00:00+03	7763.7900000000	7839.8400000000	7177.2400000000	7555.0100000000	\N
1563	6	2025-01-14 03:00:00+03	7555.0100000000	7934.1500000000	7522.4400000000	7874.4200000000	\N
1564	6	2025-01-15 03:00:00+03	7874.4200000000	8481.2500000000	7864.5000000000	8443.6300000000	\N
1565	6	2025-01-16 03:00:00+03	8443.6300000000	9026.9400000000	8389.2700000000	8915.0700000000	\N
1566	6	2025-01-17 03:00:00+03	8915.0700000000	9143.3100000000	8699.1300000000	9130.8900000000	\N
1567	6	2025-01-18 03:00:00+03	9130.8900000000	9194.8500000000	8755.1800000000	9047.0500000000	\N
1568	6	2025-01-19 03:00:00+03	9047.0500000000	9315.0700000000	8919.3500000000	9268.6700000000	\N
1569	6	2025-01-20 03:00:00+03	9268.6700000000	9320.4300000000	8384.7200000000	8829.2100000000	\N
1570	6	2025-01-21 03:00:00+03	8829.2100000000	9115.0700000000	8453.5500000000	9002.8200000000	\N
1571	6	2025-01-22 03:00:00+03	9002.8200000000	9173.5500000000	8869.3800000000	9098.0500000000	\N
1572	6	2025-01-23 03:00:00+03	9098.0500000000	9098.3200000000	8585.8500000000	8750.0800000000	\N
1573	6	2025-01-24 03:00:00+03	8750.0800000000	9087.0500000000	8625.5800000000	8824.2800000000	\N
1574	6	2025-01-25 03:00:00+03	8824.2800000000	8911.1100000000	8686.1200000000	8875.2300000000	\N
1575	6	2025-01-26 03:00:00+03	8875.2300000000	8933.1200000000	8801.4100000000	8829.7000000000	\N
1576	6	2025-01-27 03:00:00+03	8829.7000000000	8835.0100000000	7807.2600000000	8419.2400000000	\N
1577	6	2025-01-28 03:00:00+03	8419.2400000000	8676.8600000000	8338.5400000000	8347.3700000000	\N
1578	6	2025-01-29 03:00:00+03	8347.3700000000	8574.3100000000	8161.5700000000	8490.7900000000	\N
1579	6	2025-01-30 03:00:00+03	8490.7900000000	8716.2100000000	8336.9100000000	8584.1700000000	\N
1580	6	2025-01-31 03:00:00+03	8567.1000000000	8580.2200000000	8359.6200000000	8416.5900000000	\N
1581	6	2025-02-01 03:00:00+03	8416.5900000000	8526.8800000000	8053.8200000000	8109.2100000000	\N
1582	6	2025-02-02 03:00:00+03	8109.2100000000	8152.2500000000	7133.1200000000	7379.0800000000	\N
1583	6	2025-02-03 03:00:00+03	7379.0800000000	7620.8700000000	5695.8800000000	7421.7300000000	\N
1584	6	2025-02-04 03:00:00+03	7421.7300000000	7670.5700000000	6972.7900000000	7142.1100000000	\N
1585	6	2025-02-05 03:00:00+03	7142.1100000000	7214.4700000000	6801.7900000000	6904.8800000000	\N
1586	6	2025-02-06 03:00:00+03	6904.8800000000	7086.1800000000	6651.9800000000	6805.6900000000	\N
1587	6	2025-02-07 03:00:00+03	6805.6900000000	7139.0200000000	6659.5700000000	6701.7900000000	\N
1588	6	2025-02-08 03:00:00+03	6701.7900000000	7016.8000000000	6670.7300000000	6990.5100000000	\N
1589	6	2025-02-09 03:00:00+03	6990.5100000000	7185.0900000000	6907.2100000000	6969.4900000000	\N
1590	6	2025-02-10 03:00:00+03	6969.4900000000	7132.5700000000	6727.5300000000	7046.5000000000	\N
1591	6	2025-02-11 03:00:00+03	7046.5000000000	7343.4700000000	6968.0800000000	7060.3800000000	\N
1592	6	2025-02-12 03:00:00+03	7060.3800000000	7307.9100000000	6887.4800000000	7285.3100000000	\N
1593	6	2025-02-13 03:00:00+03	7285.3100000000	7404.1700000000	7082.2200000000	7215.1800000000	\N
1594	6	2025-02-14 03:00:00+03	7215.1800000000	7690.7900000000	7208.5600000000	7531.7100000000	\N
1595	6	2025-02-15 03:00:00+03	7531.7100000000	7637.0200000000	7393.2200000000	7422.6600000000	\N
1596	6	2025-02-16 03:00:00+03	7422.6600000000	7527.5900000000	7357.2400000000	7428.2400000000	\N
1597	6	2025-02-17 03:00:00+03	7428.2400000000	7475.0100000000	7134.5800000000	7253.7700000000	\N
1598	6	2025-02-18 03:00:00+03	7253.7700000000	7320.6500000000	6739.0800000000	6836.5900000000	\N
1599	6	2025-02-19 03:00:00+03	6836.5900000000	7160.9200000000	6837.5600000000	7072.1400000000	\N
1600	6	2025-02-20 03:00:00+03	7072.1400000000	7280.8100000000	7063.8500000000	7234.8000000000	\N
1601	6	2025-02-21 03:00:00+03	7234.8000000000	7364.7200000000	6858.3700000000	6896.3700000000	\N
1602	6	2025-02-22 03:00:00+03	6896.3700000000	7143.0900000000	6872.4100000000	7094.1500000000	\N
1603	6	2025-02-23 03:00:00+03	7094.1500000000	7137.0700000000	6921.7900000000	6983.7900000000	\N
1604	6	2025-02-24 03:00:00+03	6983.7900000000	7073.3900000000	6488.2900000000	6583.8500000000	\N
1605	6	2025-02-25 03:00:00+03	6583.8500000000	6604.2800000000	5843.2000000000	6293.4400000000	\N
1606	6	2025-02-26 03:00:00+03	6293.4400000000	6418.7500000000	5922.2800000000	6082.9800000000	\N
1607	6	2025-02-27 03:00:00+03	6082.9800000000	6241.5700000000	6013.6000000000	6053.4400000000	\N
1608	6	2025-02-28 03:00:00+03	6053.4400000000	6077.6700000000	5544.9900000000	6025.8000000000	\N
1609	6	2025-03-01 03:00:00+03	6025.8000000000	6186.2300000000	5991.7600000000	6092.3200000000	\N
1610	6	2025-03-02 03:00:00+03	6092.3200000000	7594.4800000000	6082.7300000000	7377.6200000000	\N
1611	6	2025-03-03 03:00:00+03	7377.6200000000	7596.0300000000	6239.5800000000	6355.6700000000	\N
1612	6	2025-03-04 03:00:00+03	6355.6700000000	6508.0400000000	5900.9300000000	6382.3000000000	\N
1613	6	2025-03-05 03:00:00+03	6382.3000000000	6705.7200000000	6361.0000000000	6608.5400000000	\N
1614	6	2025-03-06 03:00:00+03	6608.5400000000	6820.1100000000	6515.4600000000	6615.3300000000	\N
1615	6	2025-03-07 03:00:00+03	6615.3300000000	6688.8000000000	6204.8500000000	6484.4200000000	\N
1616	6	2025-03-08 03:00:00+03	6484.4200000000	6492.1700000000	6181.0500000000	6260.3400000000	\N
1617	6	2025-03-09 03:00:00+03	6260.3400000000	6292.4700000000	5648.0900000000	5826.2700000000	\N
1618	6	2025-03-10 03:00:00+03	5826.2700000000	5982.0300000000	5306.9800000000	5476.0400000000	\N
1619	6	2025-03-11 03:00:00+03	5476.0400000000	5792.3600000000	5113.5500000000	5742.9800000000	\N
1620	6	2025-03-12 03:00:00+03	5742.9800000000	5909.1400000000	5587.2000000000	5808.1300000000	\N
1621	6	2025-03-13 03:00:00+03	5808.1300000000	6019.4500000000	5727.8000000000	5731.6300000000	\N
1622	6	2025-03-14 03:00:00+03	5731.6300000000	6105.5500000000	5718.2700000000	6038.0200000000	\N
1623	6	2025-03-15 03:00:00+03	6038.0200000000	6212.4100000000	6013.0700000000	6195.4400000000	\N
1624	6	2025-03-16 03:00:00+03	6195.4400000000	6227.2700000000	5883.2100000000	5939.9600000000	\N
1625	6	2025-03-17 03:00:00+03	5939.9600000000	6145.5500000000	5873.6200000000	6083.3800000000	\N
1626	6	2025-03-18 03:00:00+03	6083.3800000000	6104.6400000000	5856.0400000000	5950.4700000000	\N
1627	6	2025-03-19 03:00:00+03	5950.4700000000	6338.4600000000	5945.9900000000	6260.1400000000	\N
1628	6	2025-03-20 03:00:00+03	6260.1400000000	6382.4900000000	6079.1500000000	6198.0700000000	\N
1629	6	2025-03-21 03:00:00+03	6198.0700000000	6228.4100000000	6067.3800000000	6157.8000000000	\N
1630	6	2025-03-22 03:00:00+03	6157.8000000000	6200.9600000000	6098.4500000000	6125.6100000000	\N
1631	6	2025-03-23 03:00:00+03	6125.6100000000	6217.0500000000	6089.4700000000	6148.2400000000	\N
1632	6	2025-03-24 03:00:00+03	6148.2400000000	6448.9900000000	6128.6600000000	6394.8200000000	\N
1633	6	2025-03-25 03:00:00+03	6394.8200000000	6467.4700000000	6296.9000000000	6427.0000000000	\N
1634	6	2025-03-26 03:00:00+03	6427.0000000000	6513.8400000000	6218.1000000000	6284.9900000000	\N
1635	6	2025-03-27 03:00:00+03	6284.9900000000	6360.3000000000	6200.0100000000	6301.7800000000	\N
1636	6	2025-03-28 03:00:00+03	6301.7800000000	6336.8400000000	5912.2600000000	5920.0600000000	\N
1637	6	2025-03-29 03:00:00+03	5920.0600000000	6021.0200000000	5684.5700000000	5735.6400000000	\N
1638	6	2025-03-30 03:00:00+03	5735.6400000000	5903.9700000000	5719.9600000000	5777.2800000000	\N
1639	6	2025-03-31 03:00:00+03	5777.2800000000	5809.3700000000	5569.7600000000	5726.1400000000	\N
1640	6	2025-04-01 03:00:00+03	5726.1400000000	5933.8400000000	5693.5600000000	5833.0000000000	\N
1641	6	2025-04-02 03:00:00+03	5833.0000000000	6016.9500000000	5684.5400000000	5802.5800000000	\N
1642	6	2025-04-03 03:00:00+03	5802.5800000000	5807.3600000000	5357.6100000000	5537.2300000000	\N
1643	6	2025-04-04 03:00:00+03	5537.2300000000	5743.6600000000	5451.0900000000	5717.7800000000	\N
1644	6	2025-04-05 03:00:00+03	5717.7800000000	5751.6400000000	5602.6600000000	5633.0400000000	\N
1645	6	2025-04-06 03:00:00+03	5633.0400000000	5687.2100000000	5144.1500000000	5153.8300000000	\N
1646	6	2025-04-07 03:00:00+03	5153.8300000000	5320.8100000000	4600.2500000000	5171.3100000000	\N
1647	6	2025-04-08 03:00:00+03	5171.3100000000	5314.5100000000	4964.2300000000	5036.0800000000	\N
1648	6	2025-04-09 03:00:00+03	5036.0800000000	5557.6600000000	4802.3100000000	5557.6600000000	\N
1649	6	2025-04-10 03:00:00+03	5557.6600000000	5573.3200000000	5197.8800000000	5347.5400000000	\N
1650	6	2025-04-11 03:00:00+03	5347.5400000000	5566.4900000000	5301.2000000000	5547.8900000000	\N
1651	6	2025-04-12 03:00:00+03	5547.8900000000	5832.4000000000	5460.9300000000	5807.4800000000	\N
1652	6	2025-04-13 03:00:00+03	5807.4800000000	5869.8800000000	5634.4100000000	5649.5500000000	\N
1653	6	2025-04-14 03:00:00+03	5649.5500000000	5845.8900000000	5610.2500000000	5717.3200000000	\N
1654	6	2025-04-15 03:00:00+03	5717.3200000000	5801.2800000000	5601.4900000000	5625.3600000000	\N
1655	6	2025-04-16 03:00:00+03	5625.3600000000	5690.1000000000	5489.3100000000	5631.3300000000	\N
1656	6	2025-04-17 03:00:00+03	5631.3300000000	5726.5000000000	5575.4400000000	5673.2800000000	\N
1657	6	2025-04-18 03:00:00+03	5673.2800000000	5693.1800000000	5617.7100000000	5668.8900000000	\N
1658	6	2025-04-19 03:00:00+03	5668.8900000000	5748.3900000000	5641.4200000000	5728.3800000000	\N
1659	6	2025-04-20 03:00:00+03	5728.3800000000	5766.2400000000	5633.3500000000	5702.6300000000	\N
1660	6	2025-04-21 03:00:00+03	5702.6300000000	5865.5600000000	5659.6300000000	5727.2300000000	\N
1661	6	2025-04-22 03:00:00+03	5727.2300000000	6005.2400000000	5678.6200000000	5948.1400000000	\N
1662	6	2025-04-23 03:00:00+03	5948.1400000000	6248.7500000000	5937.9800000000	6111.6100000000	\N
1663	6	2025-04-24 03:00:00+03	6111.6100000000	6147.4100000000	5892.8600000000	6107.5200000000	\N
1664	6	2025-04-25 03:00:00+03	6107.5200000000	6198.7200000000	6035.1900000000	6108.0700000000	\N
1665	6	2025-04-26 03:00:00+03	6108.0700000000	6196.2200000000	6060.7200000000	6091.6200000000	\N
1666	6	2025-04-27 03:00:00+03	6091.6200000000	6199.1500000000	6002.9500000000	6171.7500000000	\N
1667	6	2025-04-28 03:00:00+03	6171.7500000000	6277.1600000000	6015.3000000000	6140.8100000000	\N
1668	6	2025-04-29 03:00:00+03	6140.8100000000	6207.7500000000	6112.3500000000	6134.8200000000	\N
1669	6	2025-04-30 03:00:00+03	6134.8200000000	6135.5800000000	5838.3000000000	6009.5100000000	\N
1670	6	2025-05-01 03:00:00+03	6009.5100000000	6192.4700000000	5990.4700000000	6097.6600000000	\N
1671	6	2025-05-02 03:00:00+03	6097.6600000000	6140.2800000000	6031.0400000000	6068.3700000000	\N
1672	6	2025-05-03 03:00:00+03	6068.3700000000	6075.6600000000	5976.2700000000	6016.3500000000	\N
1673	6	2025-05-04 03:00:00+03	6016.3500000000	6048.8100000000	5922.5400000000	5961.2600000000	\N
1674	6	2025-05-05 03:00:00+03	5961.2600000000	5988.4200000000	5840.6800000000	5885.9800000000	\N
1675	6	2025-05-06 03:00:00+03	5885.9800000000	5948.3400000000	5760.5800000000	5858.5000000000	\N
1676	6	2025-05-07 03:00:00+03	5858.5000000000	6005.3600000000	5822.5400000000	5880.6500000000	\N
1677	6	2025-05-08 03:00:00+03	5880.6500000000	6456.3700000000	5881.4100000000	6443.1400000000	\N
1678	6	2025-05-09 03:00:00+03	6443.1400000000	6783.0500000000	6378.5700000000	6640.2700000000	\N
1679	6	2025-05-10 03:00:00+03	6640.2700000000	6909.4000000000	6604.2500000000	6847.9600000000	\N
1680	6	2025-05-11 03:00:00+03	6847.9600000000	7074.4800000000	6665.2200000000	6778.8700000000	\N
1681	6	2025-05-12 03:00:00+03	6778.8700000000	7242.6900000000	6744.9800000000	6956.7700000000	\N
1682	6	2025-05-13 03:00:00+03	6956.7700000000	7197.7200000000	6671.1200000000	7137.3500000000	\N
1683	6	2025-05-14 03:00:00+03	7137.3500000000	7170.0500000000	6899.6100000000	7004.8500000000	\N
1684	6	2025-05-15 03:00:00+03	7004.8500000000	7037.6800000000	6648.2300000000	6759.2300000000	\N
1685	6	2025-05-16 03:00:00+03	6759.2300000000	6847.0300000000	6613.7700000000	6724.0200000000	\N
1686	6	2025-05-17 03:00:00+03	6724.0200000000	6729.3900000000	6487.8500000000	6616.8300000000	\N
1687	6	2025-05-18 03:00:00+03	6616.8300000000	6878.4300000000	6512.5700000000	6610.3500000000	\N
1688	6	2025-05-19 03:00:00+03	6610.3500000000	6816.9800000000	6405.0200000000	6648.1100000000	\N
1689	6	2025-05-20 03:00:00+03	6648.1100000000	6767.4100000000	6537.9900000000	6654.6500000000	\N
1690	6	2025-05-21 03:00:00+03	6654.6500000000	6892.9300000000	6605.7100000000	6748.5300000000	\N
1691	6	2025-05-22 03:00:00+03	6748.5300000000	7033.7000000000	6735.2500000000	6951.4400000000	\N
1692	6	2025-05-23 03:00:00+03	6951.4400000000	7141.8700000000	6686.9300000000	6749.3800000000	\N
1693	6	2025-05-24 03:00:00+03	6749.3800000000	6810.7600000000	6614.7200000000	6767.0500000000	\N
1694	6	2025-05-25 03:00:00+03	6767.0500000000	6766.8900000000	6542.0100000000	6641.9900000000	\N
1695	6	2025-05-26 03:00:00+03	6641.9900000000	6823.6800000000	6618.1800000000	6720.2400000000	\N
1696	6	2025-05-27 03:00:00+03	6720.2400000000	6859.7500000000	6595.7800000000	6813.6500000000	\N
1697	6	2025-05-28 03:00:00+03	6813.6500000000	6821.7300000000	6577.0800000000	6633.9600000000	\N
1698	6	2025-05-29 03:00:00+03	6633.9600000000	6782.1700000000	6546.2400000000	6589.9700000000	\N
1699	6	2025-05-30 03:00:00+03	6589.9700000000	6623.8100000000	6223.3600000000	6338.7900000000	\N
1700	6	2025-05-31 03:00:00+03	6338.7900000000	6362.3200000000	6045.4200000000	6275.0200000000	\N
1701	6	2025-06-01 03:00:00+03	6275.0200000000	6292.8500000000	6092.5800000000	6206.5600000000	\N
1702	6	2025-06-02 03:00:00+03	6206.5600000000	6291.6800000000	6132.2800000000	6229.6800000000	\N
1703	6	2025-06-03 03:00:00+03	6229.6800000000	6426.2200000000	6230.4600000000	6341.8900000000	\N
1704	6	2025-06-04 03:00:00+03	6341.8900000000	6391.0600000000	6224.9800000000	6240.7500000000	\N
1705	6	2025-06-05 03:00:00+03	6240.7500000000	6287.4600000000	5813.5200000000	5813.5200000000	\N
1706	6	2025-06-06 03:00:00+03	5813.5200000000	6181.1800000000	5816.9100000000	6112.4700000000	\N
1707	6	2025-06-07 03:00:00+03	6112.4700000000	6206.8900000000	6048.5900000000	6177.3800000000	\N
1708	6	2025-06-08 03:00:00+03	6177.3800000000	6330.2500000000	6138.0100000000	6311.2000000000	\N
1709	6	2025-06-09 03:00:00+03	6311.2000000000	6390.7600000000	6175.4000000000	6390.7600000000	\N
1710	6	2025-06-10 03:00:00+03	6390.7600000000	6551.1800000000	6369.4800000000	6532.8400000000	\N
1711	6	2025-06-11 03:00:00+03	6532.8400000000	6634.3300000000	6447.5800000000	6481.2800000000	\N
1712	6	2025-06-12 03:00:00+03	6481.2800000000	6502.5800000000	6150.6600000000	6174.9500000000	\N
1713	6	2025-06-13 03:00:00+03	6174.9500000000	6211.1400000000	5871.0900000000	6042.7100000000	\N
1714	6	2025-06-14 03:00:00+03	6042.7100000000	6080.9900000000	5908.5700000000	5942.1500000000	\N
1715	6	2025-06-15 03:00:00+03	5942.1500000000	6099.0100000000	5938.6800000000	6010.7900000000	\N
1716	6	2025-06-16 03:00:00+03	6010.7900000000	6358.9700000000	6005.9000000000	6334.6500000000	\N
1717	6	2025-06-17 03:00:00+03	6334.6500000000	6341.1900000000	5931.0700000000	6016.9100000000	\N
1718	6	2025-06-18 03:00:00+03	6016.9100000000	6056.1500000000	5869.2400000000	6000.6600000000	\N
1719	6	2025-06-19 03:00:00+03	6000.6600000000	6024.1200000000	5910.1000000000	5967.3500000000	\N
1720	6	2025-06-20 03:00:00+03	5967.3500000000	6025.7100000000	5742.9600000000	5872.9900000000	\N
1721	6	2025-06-21 03:00:00+03	5872.9900000000	5891.5600000000	5713.0500000000	5754.1800000000	\N
1722	6	2025-06-22 03:00:00+03	5754.1800000000	5757.0900000000	5313.3900000000	5431.8600000000	\N
1723	6	2025-06-23 03:00:00+03	5431.8600000000	5805.8500000000	5392.5200000000	5763.9200000000	\N
1724	6	2025-06-24 03:00:00+03	5763.9200000000	6006.6700000000	5758.5600000000	5999.0700000000	\N
1725	6	2025-06-25 03:00:00+03	5999.0700000000	6036.9700000000	5903.1700000000	5974.1400000000	\N
1726	6	2025-06-26 03:00:00+03	5974.1400000000	6027.5100000000	5807.7500000000	5869.1800000000	\N
1727	6	2025-06-27 03:00:00+03	5869.1800000000	5889.0100000000	5741.0900000000	5841.8600000000	\N
1728	6	2025-06-28 03:00:00+03	5841.8600000000	6031.5500000000	5840.4100000000	5989.2500000000	\N
1729	6	2025-06-29 03:00:00+03	5989.2500000000	6032.9000000000	5978.7300000000	5996.4200000000	\N
1730	6	2025-06-30 03:00:00+03	5996.4200000000	6235.2500000000	5983.9700000000	6188.2900000000	\N
1731	6	2025-07-01 03:00:00+03	6188.2900000000	6193.7700000000	5874.3500000000	5918.0600000000	\N
1732	6	2025-07-02 03:00:00+03	5918.0600000000	6194.4400000000	5883.9700000000	6164.3600000000	\N
1733	6	2025-07-03 03:00:00+03	6164.3600000000	6257.7700000000	6104.0900000000	6194.8000000000	\N
1734	6	2025-07-04 03:00:00+03	6194.8000000000	6201.9000000000	5961.9400000000	6013.0400000000	\N
1735	6	2025-07-05 03:00:00+03	6013.0400000000	6058.4900000000	5989.4900000000	6023.0500000000	\N
1736	6	2025-07-06 03:00:00+03	6023.0500000000	6186.1700000000	6013.5900000000	6150.8700000000	\N
1737	6	2025-07-07 03:00:00+03	6150.8700000000	6241.6000000000	6091.8400000000	6102.5800000000	\N
1738	6	2025-07-08 03:00:00+03	6102.5800000000	6220.3700000000	6074.0500000000	6162.3300000000	\N
1739	6	2025-07-09 03:00:00+03	6162.3300000000	6423.2000000000	6163.5900000000	6378.3300000000	\N
1740	6	2025-07-10 03:00:00+03	6378.3300000000	6595.2500000000	6374.4800000000	6590.5100000000	\N
1741	6	2025-07-11 03:00:00+03	6590.5100000000	7221.8100000000	6584.8300000000	6974.5500000000	\N
1742	6	2025-07-12 03:00:00+03	6974.5500000000	7069.0000000000	6754.3900000000	6797.5200000000	\N
1743	6	2025-07-13 03:00:00+03	6797.5200000000	7122.1900000000	6794.5000000000	7059.7000000000	\N
1744	6	2025-07-14 03:00:00+03	7059.7000000000	7337.1000000000	6948.3900000000	7125.9600000000	\N
1745	6	2025-07-15 03:00:00+03	7125.9600000000	7159.3200000000	6904.1200000000	7033.9300000000	\N
1746	6	2025-07-16 03:00:00+03	7033.9300000000	7511.2900000000	7024.1600000000	7470.0400000000	\N
1747	6	2025-07-17 03:00:00+03	7470.0400000000	7823.5400000000	7314.9200000000	7743.5500000000	\N
1748	6	2025-07-18 03:00:00+03	7743.5500000000	8314.3200000000	7742.5600000000	7899.7400000000	\N
1749	6	2025-07-19 03:00:00+03	7899.7400000000	8080.9000000000	7839.2000000000	7977.7800000000	\N
1750	6	2025-07-20 03:00:00+03	7977.7800000000	8309.3300000000	7885.4700000000	8233.0900000000	\N
1751	6	2025-07-21 03:00:00+03	8233.0900000000	8612.5300000000	8051.9100000000	8344.7900000000	\N
1752	6	2025-07-22 03:00:00+03	8344.7900000000	8536.6200000000	8176.3200000000	8447.4500000000	\N
1753	6	2025-07-23 03:00:00+03	8447.4500000000	8573.8100000000	7695.8100000000	7819.1300000000	\N
1754	6	2025-07-24 03:00:00+03	7819.1300000000	8056.5600000000	7475.7800000000	7986.1700000000	\N
1755	6	2025-07-25 03:00:00+03	7986.1700000000	7986.7000000000	7567.1200000000	7824.6500000000	\N
1756	6	2025-07-26 03:00:00+03	7824.6500000000	8010.1800000000	7834.3700000000	7973.6700000000	\N
1757	6	2025-07-27 03:00:00+03	7973.6700000000	8158.0500000000	7944.0400000000	8112.3400000000	\N
1758	6	2025-07-28 03:00:00+03	8112.3400000000	8374.5800000000	7905.8700000000	7958.9100000000	\N
1759	6	2025-07-29 03:00:00+03	7958.9100000000	8057.3100000000	7745.9900000000	7820.7600000000	\N
1760	6	2025-07-30 03:00:00+03	7820.7600000000	7920.2500000000	7498.9500000000	7711.4200000000	\N
1761	6	2025-07-31 03:00:00+03	7711.4200000000	7935.1600000000	7617.9600000000	7647.3100000000	\N
1762	6	2025-08-01 03:00:00+03	7647.3100000000	7667.9500000000	7286.7400000000	7389.2100000000	\N
1763	6	2025-08-02 03:00:00+03	7389.2100000000	7423.7000000000	6930.7200000000	7050.8500000000	\N
1764	6	2025-08-03 03:00:00+03	7050.8500000000	7282.7600000000	6919.4000000000	7260.0200000000	\N
1765	6	2025-08-04 03:00:00+03	7260.0200000000	7586.0400000000	7243.7600000000	7504.6600000000	\N
1766	6	2025-08-05 03:00:00+03	7504.6600000000	7598.1200000000	7267.8900000000	7267.8900000000	\N
1767	6	2025-08-06 03:00:00+03	7267.8900000000	7510.5000000000	7207.3300000000	7456.4800000000	\N
1768	6	2025-08-07 03:00:00+03	7456.4800000000	7712.9100000000	7401.6800000000	7703.3600000000	\N
1769	6	2025-08-08 03:00:00+03	7703.3600000000	8094.7700000000	7694.0000000000	8029.9000000000	\N
1770	6	2025-08-09 03:00:00+03	8029.9000000000	8196.0400000000	7979.2200000000	8104.5000000000	\N
1771	6	2025-08-10 03:00:00+03	8104.5000000000	8184.3000000000	7880.7000000000	7996.5400000000	\N
1772	6	2025-08-11 03:00:00+03	7996.5400000000	8202.6700000000	7854.3600000000	7901.0400000000	\N
1773	6	2025-08-12 03:00:00+03	7901.0400000000	8317.3300000000	7778.4200000000	8310.6700000000	\N
1774	6	2025-08-13 03:00:00+03	8310.6700000000	8557.6100000000	8198.1000000000	8407.9000000000	\N
1775	6	2025-08-14 03:00:00+03	8407.9000000000	8688.6000000000	7974.5100000000	8082.1600000000	\N
1776	6	2025-08-15 03:00:00+03	8082.1600000000	8282.0000000000	7866.4800000000	8014.3100000000	\N
1777	6	2025-08-16 03:00:00+03	8014.3100000000	8163.7500000000	8000.9200000000	8106.6100000000	\N
1778	6	2025-08-17 03:00:00+03	8106.6100000000	8351.8900000000	8071.8900000000	8207.8800000000	\N
1779	6	2025-08-18 03:00:00+03	8207.8800000000	8273.5900000000	7831.3500000000	8046.1700000000	\N
1780	6	2025-08-19 03:00:00+03	8046.1700000000	8125.9200000000	7668.4500000000	7756.8600000000	\N
1781	6	2025-08-20 03:00:00+03	7756.8600000000	8044.1400000000	7613.4200000000	8026.0200000000	\N
1782	6	2025-08-21 03:00:00+03	8026.0200000000	8068.8000000000	7720.4800000000	7772.7300000000	\N
1783	6	2025-08-22 03:00:00+03	7772.7300000000	8398.5100000000	7612.9400000000	8381.7900000000	\N
1784	6	2025-08-23 03:00:00+03	8381.7900000000	8434.7300000000	8134.7100000000	8305.3300000000	\N
1785	6	2025-08-24 03:00:00+03	8305.3300000000	8521.0500000000	8175.7000000000	8262.7000000000	\N
1786	6	2025-08-25 03:00:00+03	8262.7000000000	8380.9900000000	7710.9400000000	7710.9400000000	\N
1787	6	2025-08-26 03:00:00+03	7710.9400000000	8144.8100000000	7684.5100000000	8089.7200000000	\N
1788	6	2025-08-27 03:00:00+03	8089.7200000000	8231.7400000000	7997.1500000000	8168.1700000000	\N
1789	6	2025-08-28 03:00:00+03	8168.1700000000	8275.4000000000	7988.3900000000	8132.9100000000	\N
1790	6	2025-08-29 03:00:00+03	8132.9100000000	8241.4700000000	7770.1400000000	7779.4700000000	\N
1791	6	2025-08-30 03:00:00+03	7779.4700000000	7915.9700000000	7732.8200000000	7816.1800000000	\N
1792	6	2025-08-31 03:00:00+03	7816.1800000000	7959.0000000000	7808.2200000000	7884.6300000000	\N
1793	6	2025-09-01 03:00:00+03	7884.6300000000	7908.3100000000	7621.3200000000	7671.5300000000	\N
1794	6	2025-09-02 03:00:00+03	7671.5300000000	7905.3000000000	7564.9000000000	7880.9600000000	\N
1795	6	2025-09-03 03:00:00+03	7880.9600000000	8025.3200000000	7858.0100000000	7959.4100000000	\N
1796	6	2025-09-04 03:00:00+03	7959.4100000000	7982.1000000000	7734.5000000000	7769.1500000000	\N
1797	6	2025-09-05 03:00:00+03	7769.1500000000	7993.5500000000	7729.7400000000	7898.2800000000	\N
1798	6	2025-09-06 03:00:00+03	7898.2800000000	7901.5300000000	7752.7200000000	7780.6000000000	\N
1799	6	2025-09-07 03:00:00+03	7780.6000000000	7998.3200000000	7750.7100000000	7957.3200000000	\N
1800	6	2025-09-08 03:00:00+03	7957.3200000000	8264.4200000000	7932.2700000000	8197.4400000000	\N
1801	6	2025-09-09 03:00:00+03	8197.4400000000	8380.1600000000	8120.7300000000	8227.6100000000	\N
1802	6	2025-09-10 03:00:00+03	8227.6100000000	8482.2500000000	8185.2800000000	8355.0400000000	\N
1803	6	2025-09-11 03:00:00+03	8355.0400000000	8515.8700000000	8349.7600000000	8475.0600000000	\N
1804	6	2025-09-12 03:00:00+03	8475.0600000000	8842.7200000000	8472.3200000000	8820.7600000000	\N
1805	6	2025-09-13 03:00:00+03	8820.7600000000	9041.7700000000	8772.0300000000	8875.3000000000	\N
1806	6	2025-09-14 03:00:00+03	8875.3000000000	8971.2700000000	8685.5200000000	8762.0500000000	\N
1807	6	2025-09-15 03:00:00+03	8762.0500000000	8822.4300000000	8471.7900000000	8571.9100000000	\N
1808	6	2025-09-16 03:00:00+03	8571.9100000000	8766.3600000000	8502.7900000000	8733.4100000000	\N
1809	6	2025-09-17 03:00:00+03	8733.4100000000	8805.4300000000	8594.9000000000	8712.3400000000	\N
1810	6	2025-09-18 03:00:00+03	8712.3400000000	9121.4500000000	8673.4400000000	9050.2300000000	\N
1811	6	2025-09-19 03:00:00+03	9050.2300000000	9049.4800000000	8706.6300000000	8735.3900000000	\N
1812	6	2025-09-20 03:00:00+03	8735.3900000000	8881.1700000000	8713.2100000000	8826.4300000000	\N
1813	6	2025-09-21 03:00:00+03	8826.4300000000	8983.4100000000	8771.7500000000	8821.8100000000	\N
1814	6	2025-09-22 03:00:00+03	8821.8100000000	8859.1200000000	8134.6800000000	8303.3400000000	\N
1815	6	2025-09-23 03:00:00+03	8303.3400000000	8463.1000000000	8125.2300000000	8316.1000000000	\N
1816	6	2025-09-24 03:00:00+03	8316.1000000000	8465.4500000000	8111.1300000000	8429.5400000000	\N
1817	6	2025-09-25 03:00:00+03	8429.5400000000	8438.2200000000	7790.6800000000	7851.5000000000	\N
1818	6	2025-09-26 03:00:00+03	7851.5000000000	8047.5000000000	7701.3400000000	7963.9800000000	\N
1819	6	2025-09-27 03:00:00+03	7963.9800000000	8062.8900000000	7949.0900000000	8026.5000000000	\N
1820	6	2025-09-28 03:00:00+03	8026.5000000000	8163.4500000000	7920.1100000000	8146.3400000000	\N
1821	6	2025-09-29 03:00:00+03	8146.3400000000	8399.6500000000	8136.3400000000	8393.5800000000	\N
1822	6	2025-09-30 03:00:00+03	8393.5800000000	8398.4500000000	8073.6000000000	8259.1200000000	\N
1823	6	2025-10-01 03:00:00+03	8259.1200000000	8532.9200000000	8151.5700000000	8490.8200000000	\N
1824	6	2025-10-02 03:00:00+03	8490.8200000000	8809.6200000000	8576.7300000000	8799.4000000000	\N
1825	6	2025-10-03 03:00:00+03	8799.4000000000	9153.5800000000	8790.7900000000	9079.8100000000	\N
1826	6	2025-10-04 03:00:00+03	9079.8100000000	9116.1000000000	8757.2100000000	8818.8800000000	\N
1827	6	2025-10-05 03:00:00+03	8818.8800000000	9158.0700000000	8807.4500000000	8868.1400000000	\N
1828	6	2025-10-06 03:00:00+03	8868.1400000000	9235.6000000000	8848.7400000000	9167.2400000000	\N
1829	6	2025-10-07 03:00:00+03	9167.2400000000	9305.6500000000	8909.1000000000	9028.3800000000	\N
1830	6	2025-10-08 03:00:00+03	9028.3800000000	9170.2600000000	8820.1200000000	9100.3100000000	\N
1831	6	2025-10-09 03:00:00+03	9100.3100000000	9133.3800000000	8638.3600000000	8753.5700000000	\N
1832	6	2025-10-10 03:00:00+03	8753.5700000000	8904.9500000000	7957.0700000000	7957.0700000000	\N
1833	6	2025-10-11 03:00:00+03	7957.0700000000	8084.6300000000	5694.0000000000	7375.2700000000	\N
1834	6	2025-10-12 03:00:00+03	7375.2700000000	8333.3600000000	7269.1900000000	8243.5500000000	\N
1835	6	2025-10-13 03:00:00+03	8243.5500000000	8504.1300000000	8103.0200000000	8441.3300000000	\N
1836	6	2025-10-14 03:00:00+03	8441.3300000000	8492.2300000000	7655.0200000000	8012.7200000000	\N
1837	6	2025-10-15 03:00:00+03	8012.7200000000	8161.3100000000	7651.9900000000	7700.5500000000	\N
1838	6	2025-10-16 03:00:00+03	7700.5500000000	7891.9400000000	7409.6400000000	7431.6400000000	\N
1839	6	2025-10-17 03:00:00+03	7431.6400000000	7605.9900000000	6958.3600000000	7296.3400000000	\N
1840	6	2025-10-18 03:00:00+03	7296.3400000000	7517.0200000000	7235.2500000000	7426.9100000000	\N
1841	6	2025-10-19 03:00:00+03	7426.9100000000	7653.2600000000	7284.8400000000	7592.7100000000	\N
1842	6	2025-10-20 03:00:00+03	7592.7100000000	7758.2900000000	7399.4200000000	7694.4200000000	\N
1843	6	2025-10-21 03:00:00+03	7694.4200000000	7798.1200000000	7393.9400000000	7596.0300000000	\N
1844	6	2025-10-22 03:00:00+03	7596.0300000000	7616.5400000000	7284.0400000000	7299.1100000000	\N
1845	6	2025-10-23 03:00:00+03	7299.1100000000	7677.8700000000	7211.2400000000	7518.1600000000	\N
1846	6	2025-10-24 03:00:00+03	7518.1600000000	7751.6300000000	7510.6900000000	7672.7300000000	\N
1847	6	2025-10-25 03:00:00+03	7672.7300000000	7821.3900000000	7641.1900000000	7771.6000000000	\N
1848	6	2025-10-26 03:00:00+03	7771.6000000000	7973.2400000000	7753.7500000000	7874.1900000000	\N
1849	6	2025-10-27 03:00:00+03	7874.1900000000	8113.8900000000	7865.9800000000	7939.0600000000	\N
1850	6	2025-10-28 03:00:00+03	7939.0600000000	8041.2800000000	7673.9100000000	7712.2100000000	\N
1851	6	2025-10-29 03:00:00+03	7712.2100000000	7921.3600000000	7598.6900000000	7805.8700000000	\N
1852	6	2025-10-30 03:00:00+03	7805.8700000000	7854.8400000000	7205.3400000000	7328.3300000000	\N
1853	6	2025-10-31 03:00:00+03	7328.3300000000	7577.4600000000	7298.0300000000	7500.3400000000	\N
1854	6	2025-11-01 03:00:00+03	7500.3400000000	7538.9700000000	7440.0900000000	7519.8600000000	\N
1855	6	2025-11-02 03:00:00+03	7519.8600000000	7570.9700000000	7378.6800000000	7438.8800000000	\N
1856	6	2025-11-03 03:00:00+03	7438.8800000000	7542.1100000000	6748.2800000000	6786.6800000000	\N
1857	6	2025-11-04 03:00:00+03	6786.6800000000	6947.9200000000	6207.0800000000	6393.8200000000	\N
1858	6	2025-11-05 03:00:00+03	6393.8200000000	6823.0300000000	6147.5800000000	6800.4100000000	\N
1859	6	2025-11-06 03:00:00+03	6800.4100000000	6825.9300000000	6450.1100000000	6510.3700000000	\N
1860	6	2025-11-07 03:00:00+03	6510.3700000000	6949.0400000000	6405.3900000000	6949.0400000000	\N
1861	6	2025-11-08 03:00:00+03	6949.0400000000	6974.9700000000	6719.1100000000	6792.2800000000	\N
1862	6	2025-11-09 03:00:00+03	6792.2800000000	6989.0500000000	6673.0900000000	6941.5400000000	\N
1863	6	2025-11-10 03:00:00+03	6941.5400000000	7236.1500000000	6897.6600000000	7165.8100000000	\N
1864	6	2025-11-11 03:00:00+03	7165.8100000000	7256.9100000000	6815.8300000000	6820.2700000000	\N
1865	6	2025-11-12 03:00:00+03	6820.2700000000	6977.2800000000	6630.4600000000	6720.1300000000	\N
1866	6	2025-11-13 03:00:00+03	6720.1300000000	6970.4000000000	6407.5300000000	6443.4200000000	\N
1867	6	2025-11-14 03:00:00+03	6443.4200000000	6585.5700000000	6243.6300000000	6376.2000000000	\N
1868	6	2025-11-15 03:00:00+03	6376.2000000000	6507.1300000000	6301.1200000000	6412.3100000000	\N
1869	6	2025-11-16 03:00:00+03	6412.3100000000	6531.0600000000	6195.4400000000	6289.8100000000	\N
1870	6	2025-11-17 03:00:00+03	6289.8100000000	6478.7900000000	6070.8900000000	6135.2300000000	\N
1871	6	2025-11-18 03:00:00+03	6135.2300000000	6435.2900000000	6074.7600000000	6405.2700000000	\N
1872	6	2025-11-19 03:00:00+03	6405.2700000000	6421.0000000000	5931.0800000000	6017.5500000000	\N
1873	6	2025-11-20 03:00:00+03	6017.5500000000	6301.4200000000	5867.5300000000	5929.9600000000	\N
1874	6	2025-11-21 03:00:00+03	5929.9600000000	6034.5700000000	5402.7600000000	5659.7600000000	\N
1875	6	2025-11-22 03:00:00+03	5659.7600000000	5760.5900000000	5568.2300000000	5646.3600000000	\N
1876	6	2025-11-23 03:00:00+03	5646.3600000000	5921.3200000000	5639.7600000000	5906.9200000000	\N
1877	6	2025-11-24 03:00:00+03	5906.9200000000	6229.6400000000	5793.6500000000	6192.1300000000	\N
1878	6	2025-11-25 03:00:00+03	6192.1300000000	6199.0100000000	5942.1800000000	6086.5700000000	\N
1879	6	2025-11-26 03:00:00+03	6086.5700000000	6297.7400000000	6005.1200000000	6265.5900000000	\N
1880	6	2025-11-27 03:00:00+03	6265.5900000000	6306.2800000000	6163.7000000000	6269.4100000000	\N
1881	6	2025-11-28 03:00:00+03	6269.4100000000	6331.2000000000	6090.2900000000	6170.4900000000	\N
1882	6	2025-11-29 03:00:00+03	6170.4900000000	6173.9200000000	6080.0700000000	6127.2200000000	\N
1883	6	2025-11-30 03:00:00+03	6127.2200000000	6220.2800000000	6082.5400000000	6169.3800000000	\N
1884	6	2025-12-01 03:00:00+03	6169.3800000000	6170.1900000000	5567.1000000000	5646.0100000000	\N
1885	6	2025-12-02 03:00:00+03	5646.0100000000	6166.3900000000	5646.2100000000	6106.0900000000	\N
1886	6	2025-12-03 03:00:00+03	6106.0900000000	6311.1000000000	6079.1600000000	6273.6500000000	\N
1887	6	2025-12-04 03:00:00+03	6273.6500000000	6386.4400000000	6056.6100000000	6157.9600000000	\N
1888	6	2025-12-05 03:00:00+03	6157.9600000000	6175.7600000000	5884.3800000000	5923.7800000000	\N
1889	6	2025-12-06 03:00:00+03	5923.7800000000	6001.6500000000	5907.3000000000	5972.1100000000	\N
1890	6	2025-12-07 03:00:00+03	5972.1100000000	6102.8800000000	5818.9700000000	6079.1200000000	\N
1891	6	2025-12-08 03:00:00+03	6079.1200000000	6158.3700000000	5886.6700000000	6048.1200000000	\N
1892	6	2025-12-09 03:00:00+03	6048.1200000000	6317.5800000000	5929.1900000000	6161.7300000000	\N
1893	6	2025-12-10 03:00:00+03	6161.7300000000	6209.9300000000	6016.2400000000	6079.0300000000	\N
1894	6	2025-12-11 03:00:00+03	6079.0300000000	6093.4700000000	5799.5500000000	5947.7000000000	\N
1895	6	2025-12-12 03:00:00+03	5947.7000000000	6034.6000000000	5820.0800000000	5854.4600000000	\N
1896	6	2025-12-13 03:00:00+03	5854.4600000000	5966.8100000000	5849.6700000000	5912.2500000000	\N
1897	6	2025-12-14 03:00:00+03	5912.2500000000	5948.9400000000	5817.7100000000	5827.5800000000	\N
1898	6	2025-12-15 03:00:00+03	5827.5800000000	5925.5900000000	5547.6000000000	5584.8900000000	\N
1899	6	2025-12-16 03:00:00+03	5584.8900000000	5768.6400000000	5558.3300000000	5710.4800000000	\N
1900	6	2025-12-17 03:00:00+03	5710.4800000000	5838.4900000000	5468.2300000000	5523.0700000000	\N
1901	6	2025-12-18 03:00:00+03	5523.0700000000	5671.6700000000	5336.2000000000	5346.0100000000	\N
1902	6	2025-12-19 03:00:00+03	5346.0100000000	5691.9000000000	5324.0800000000	5672.1700000000	\N
1903	6	2025-12-20 03:00:00+03	5672.1700000000	5694.6300000000	5621.1300000000	5660.9600000000	\N
1904	6	2025-12-21 03:00:00+03	5660.9600000000	5693.4700000000	5569.0900000000	5626.7900000000	\N
1905	6	2025-12-22 03:00:00+03	5626.7900000000	5732.9100000000	5604.8800000000	5619.8300000000	\N
1906	6	2025-12-23 03:00:00+03	5619.8300000000	5665.3800000000	5512.4500000000	5567.7900000000	\N
1907	6	2025-12-24 03:00:00+03	5567.7900000000	5574.8100000000	5466.4600000000	5531.1000000000	\N
1908	6	2025-12-25 03:00:00+03	5531.1000000000	5562.9400000000	5479.9200000000	5532.0800000000	\N
1909	6	2025-12-26 03:00:00+03	5532.0800000000	5569.8100000000	5416.6600000000	5491.9600000000	\N
1910	6	2025-12-27 03:00:00+03	5491.9600000000	5543.5100000000	5473.5100000000	5534.7600000000	\N
1911	6	2025-12-28 03:00:00+03	5534.7600000000	5622.0600000000	5535.0100000000	5574.5500000000	\N
1912	6	2025-12-29 03:00:00+03	5574.5500000000	5726.6000000000	5529.3000000000	5538.4900000000	\N
1913	6	2025-12-30 03:00:00+03	5538.4900000000	5620.3800000000	5520.8800000000	5572.8300000000	\N
1914	6	2025-12-31 03:00:00+03	5572.8300000000	5638.9800000000	5490.0900000000	5515.9300000000	\N
1915	6	2026-01-01 03:00:00+03	5515.9300000000	5600.9800000000	5513.5200000000	5586.2800000000	\N
1916	6	2026-01-02 03:00:00+03	5586.2800000000	5873.3800000000	5583.0300000000	5828.6600000000	\N
1917	6	2026-01-03 03:00:00+03	5828.6600000000	5931.3000000000	5805.2100000000	5885.7800000000	\N
1918	6	2026-01-04 03:00:00+03	5885.7800000000	6083.5000000000	5879.5200000000	6044.4300000000	\N
1919	6	2026-01-05 03:00:00+03	6044.4300000000	6389.2700000000	6022.9400000000	6329.4100000000	\N
1920	6	2026-01-06 03:00:00+03	6329.4100000000	6474.4600000000	6134.4600000000	6251.3800000000	\N
1921	6	2026-01-07 03:00:00+03	6251.3800000000	6366.0500000000	6102.4700000000	6124.7300000000	\N
1922	6	2026-01-08 03:00:00+03	6124.7300000000	6175.8900000000	5933.6300000000	6059.2300000000	\N
1923	6	2026-01-09 03:00:00+03	6059.2300000000	6150.5200000000	5984.8600000000	6007.3600000000	\N
1924	6	2026-01-10 03:00:00+03	6007.3600000000	6068.6800000000	5999.3900000000	6052.2400000000	\N
1925	6	2026-01-11 03:00:00+03	6052.2400000000	6114.9800000000	6001.7700000000	6030.5900000000	\N
1926	6	2026-01-12 03:00:00+03	6030.5900000000	6149.3400000000	5969.0800000000	6035.7300000000	\N
1927	6	2026-01-13 03:00:00+03	6035.7300000000	6266.8900000000	5982.0300000000	6245.6000000000	\N
1928	6	2026-01-14 03:00:00+03	6245.6000000000	6364.7200000000	6200.0400000000	6303.9400000000	\N
1929	6	2026-01-15 03:00:00+03	6303.9400000000	6313.6700000000	6087.7400000000	6099.6500000000	\N
1930	6	2026-01-16 03:00:00+03	6099.6500000000	6166.6800000000	6041.0000000000	6166.0700000000	\N
1931	6	2026-01-17 03:00:00+03	6166.0700000000	6223.8400000000	6134.2200000000	6209.8100000000	\N
1932	6	2026-01-18 03:00:00+03	6209.8100000000	6211.2700000000	6134.6500000000	6173.1100000000	\N
1933	6	2026-01-19 03:00:00+03	6173.1100000000	6183.8500000000	5750.8700000000	5956.1100000000	\N
1934	6	2026-01-20 03:00:00+03	5956.1100000000	5963.4400000000	5655.7500000000	5685.9200000000	\N
1935	6	2026-01-21 03:00:00+03	5685.9200000000	5799.7200000000	5570.3000000000	5791.3900000000	\N
1936	6	2026-01-22 03:00:00+03	5791.3900000000	5826.4600000000	5653.5000000000	5720.1500000000	\N
1937	6	2026-01-23 03:00:00+03	5720.1500000000	5790.8600000000	5667.7900000000	5702.9200000000	\N
1938	6	2026-01-24 03:00:00+03	5702.9200000000	5729.7300000000	5688.9500000000	5703.6100000000	\N
1939	6	2026-01-25 03:00:00+03	5703.6100000000	5696.7400000000	5472.0800000000	5472.0800000000	\N
1940	6	2026-01-26 03:00:00+03	5472.0800000000	5657.5900000000	5436.3700000000	5617.8000000000	\N
1941	6	2026-01-27 03:00:00+03	5617.8000000000	5755.7700000000	5606.3800000000	5746.4200000000	\N
1942	6	2026-01-28 03:00:00+03	5746.4200000000	5796.7700000000	5720.5600000000	5757.5400000000	\N
1943	6	2026-01-29 03:00:00+03	5757.5400000000	5759.4800000000	5395.4400000000	5425.0400000000	\N
1944	6	2026-01-30 03:00:00+03	5425.0400000000	5468.2400000000	5291.9700000000	5405.4900000000	\N
1945	6	2026-01-31 03:00:00+03	5405.4900000000	5388.2000000000	4692.7900000000	4829.8600000000	\N
1946	6	2026-02-01 03:00:00+03	4829.8600000000	5013.9900000000	4791.2000000000	4829.4300000000	\N
1947	6	2026-02-02 03:00:00+03	4829.4300000000	5002.8900000000	4806.8200000000	4930.5600000000	\N
1948	6	2026-02-03 03:00:00+03	4930.5600000000	4989.2200000000	4720.6600000000	4935.9000000000	\N
1949	6	2026-02-04 03:00:00+03	4935.9000000000	4897.7800000000	4591.7100000000	4670.7800000000	\N
1950	6	2026-02-05 03:00:00+03	4670.7800000000	4617.8700000000	3914.0900000000	3955.7200000000	\N
1951	6	2026-02-06 03:00:00+03	3955.7200000000	4477.8500000000	3928.3300000000	4367.4600000000	\N
1952	6	2026-02-07 03:00:00+03	4367.4600000000	4436.0300000000	4258.3700000000	4371.6600000000	\N
1953	6	2026-02-08 03:00:00+03	4371.6600000000	4378.3200000000	4271.6600000000	4349.9600000000	\N
1954	6	2026-02-09 03:00:00+03	4349.9600000000	4344.3000000000	4187.2200000000	4334.7200000000	\N
1955	6	2026-02-10 03:00:00+03	4334.7200000000	4336.2100000000	4171.2100000000	4182.8300000000	\N
1956	6	2026-02-11 03:00:00+03	4182.8300000000	4226.7600000000	4034.8200000000	4116.8500000000	\N
1957	6	2026-02-12 03:00:00+03	4116.8500000000	4186.8600000000	4055.0300000000	4077.8400000000	\N
1958	6	2026-02-13 03:00:00+03	4077.8400000000	4269.8200000000	4055.2300000000	4249.9900000000	\N
1959	6	2026-02-14 03:00:00+03	4249.9900000000	4440.4400000000	4237.7700000000	4440.4400000000	\N
1960	6	2026-02-15 03:00:00+03	4440.4400000000	4551.1900000000	4289.1200000000	4295.4900000000	\N
1961	6	2026-02-16 03:00:00+03	4295.4900000000	4338.5200000000	4291.8400000000	4310.3400000000	\N
1962	6	2026-02-17 03:00:00+03	4310.3400000000	4376.7800000000	4239.5400000000	4328.0500000000	\N
1963	6	2026-02-18 03:00:00+03	4328.0500000000	4337.3000000000	4235.9200000000	4235.9200000000	\N
1964	6	2026-02-19 03:00:00+03	4235.9200000000	4228.6900000000	4118.5300000000	4188.1200000000	\N
1965	6	2026-02-20 03:00:00+03	4188.1200000000	4245.5500000000	4173.0500000000	4173.0500000000	\N
1966	6	2026-02-21 03:00:00+03	4173.0500000000	4337.5100000000	4278.0800000000	4322.1100000000	\N
1967	6	2026-02-22 03:00:00+03	4322.1100000000	4307.1500000000	4192.1300000000	4198.2200000000	\N
1968	6	2026-02-23 03:00:00+03	4198.2200000000	4215.4200000000	4030.3500000000	4049.0600000000	\N
1969	6	2026-02-24 03:00:00+03	4049.0600000000	4044.9600000000	3966.1300000000	4040.0400000000	\N
1970	6	2026-02-25 03:00:00+03	4040.0400000000	4391.7200000000	4023.0800000000	4390.9900000000	\N
1971	6	2026-02-26 03:00:00+03	4390.9900000000	4453.5800000000	4197.9600000000	4239.8300000000	\N
1972	6	2026-02-27 03:00:00+03	4239.8300000000	4315.3500000000	4092.8600000000	4119.9400000000	\N
1973	6	2026-02-28 03:00:00+03	4119.9400000000	4194.1500000000	3938.9500000000	4190.8200000000	\N
1974	6	2026-03-01 03:00:00+03	4190.8200000000	4293.6300000000	4090.0600000000	4099.3600000000	\N
1975	6	2026-03-02 03:00:00+03	4099.3600000000	4339.4100000000	4112.1300000000	4256.0100000000	\N
1976	6	2026-03-03 03:00:00+03	4256.0100000000	4273.2700000000	4127.2000000000	4176.7400000000	\N
1977	6	2026-03-04 03:00:00+03	4176.7400000000	4465.6900000000	4146.1100000000	4445.9300000000	\N
1978	6	2026-03-05 03:00:00+03	4445.9300000000	4386.5200000000	4274.5400000000	4316.1900000000	\N
1979	6	2026-03-06 03:00:00+03	4316.1900000000	4314.9900000000	4178.6700000000	4178.6700000000	\N
1980	6	2026-03-07 03:00:00+03	4178.6700000000	4178.8100000000	4119.4500000000	4126.2800000000	\N
1981	6	2026-03-08 03:00:00+03	4126.2800000000	4141.2300000000	4079.5400000000	4119.2000000000	\N
1982	6	2026-03-09 03:00:00+03	4119.2000000000	4217.8000000000	4059.1000000000	4203.2600000000	\N
\.


--
-- TOC entry 5545 (class 0 OID 33136)
-- Dependencies: 243
-- Data for Name: fund_chart_minute; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_chart_minute (id, fund_id, ts_utc, open, high, low, close, volume) FROM stdin;
1	9	2026-04-27 16:34:00+03	0.6939379944	0.6939379944	0.6933885095	0.6933885095	\N
2	9	2026-04-27 16:35:00+03	0.6932866600	0.6937602019	0.6932866600	0.6937602019	\N
3	9	2026-04-27 16:42:00+03	0.6944678493	0.6944678493	0.6944678493	0.6944678493	\N
4	9	2026-04-27 16:43:00+03	0.6944248766	0.6944248766	0.6937366008	0.6938130676	\N
130	9	2026-04-29 14:38:00+03	691.1981105300	691.1981105300	690.7987399200	690.7987399200	\N
5	9	2026-04-27 17:48:00+03	696.8974522400	697.2069354100	696.8974522400	697.0490762100	\N
70	9	2026-04-27 17:59:00+03	694.0680635900	694.0680635900	693.7176484200	693.7454931200	\N
76	9	2026-04-27 18:00:00+03	693.7454931200	693.7995494500	693.7454931200	693.7995494500	\N
10	9	2026-04-27 17:49:00+03	697.0490762100	697.0935454100	696.7497810900	696.8543193200	\N
77	9	2026-04-27 18:01:00+03	693.2856692500	693.3309983900	693.0647915600	693.3309983900	\N
16	9	2026-04-27 17:50:00+03	696.8543193200	697.0849657200	696.7461390900	696.7461390900	\N
136	9	2026-04-29 14:39:00+03	690.7987399200	690.7987399200	690.5861957900	690.5861957900	\N
142	9	2026-04-29 14:40:00+03	690.5861957900	690.5861957900	690.4655453300	690.4655453300	\N
143	9	2026-04-29 15:56:00+03	687.0029694600	687.0029694600	687.0029694600	687.0029694600	\N
22	9	2026-04-27 17:51:00+03	696.7461390900	696.7531391000	696.5489853900	696.6125466600	\N
81	9	2026-04-27 18:02:00+03	693.3309983900	693.3733565700	692.4501621800	692.4501621800	\N
28	9	2026-04-27 17:52:00+03	696.6125466600	696.6125466600	695.6107702800	695.6408955300	\N
87	9	2026-04-27 18:03:00+03	692.4501621800	693.0017604200	692.4243736200	692.6596845100	\N
34	9	2026-04-27 17:53:00+03	695.6408955300	695.6408955300	694.3660550200	694.3660550200	\N
93	9	2026-04-27 18:04:00+03	692.6596845100	692.6596845100	692.3793243900	692.3793243900	\N
144	9	2026-04-29 15:57:00+03	687.0029694600	687.1877438200	687.0029694600	687.1239982500	\N
40	9	2026-04-27 17:54:00+03	694.3660550200	694.7653324900	694.3107187200	694.7653324900	\N
95	9	2026-04-27 18:05:00+03	690.8631205300	691.4255063200	690.8631205300	691.3192808800	\N
46	9	2026-04-27 17:55:00+03	694.7653324900	694.8464928900	694.3218683600	694.3218683600	\N
150	9	2026-04-29 15:58:00+03	687.1239982500	687.1370534000	687.0829723900	687.1370534000	\N
99	9	2026-04-27 18:06:00+03	691.3192808800	691.3192808800	690.5541250900	690.5541250900	\N
52	9	2026-04-27 17:56:00+03	694.3218683600	694.3218683600	693.6215901800	694.2045050300	\N
154	9	2026-04-29 16:02:00+03	687.3369896100	687.5509459700	687.3369896100	687.5509459700	\N
58	9	2026-04-27 17:57:00+03	694.2045050300	694.5510963300	694.2045050300	694.4476548000	\N
105	9	2026-04-27 18:07:00+03	690.5541250900	690.7595039500	689.8255388800	690.0503679000	\N
64	9	2026-04-27 17:58:00+03	694.4476548000	694.4476548000	693.9243980100	694.0680635900	\N
111	9	2026-04-29 14:33:00+03	690.2350581300	690.5809280800	690.2350581300	690.5423107600	\N
157	9	2026-04-29 16:03:00+03	687.5509459700	687.5898189600	687.2263255500	687.2263255500	\N
163	9	2026-04-29 16:04:00+03	687.2263255500	687.2263255500	687.0491671900	687.0491671900	\N
116	9	2026-04-29 14:34:00+03	690.5423107600	690.6353370600	690.5205582500	690.5997914700	\N
122	9	2026-04-29 14:35:00+03	690.5997914700	690.6399566800	690.5997914700	690.6399566800	\N
123	9	2026-04-29 14:36:00+03	690.8486086800	690.8486086800	690.8486086800	690.8486086800	\N
124	9	2026-04-29 14:37:00+03	690.8486086800	691.1981105300	690.8486086800	691.1981105300	\N
\.


--
-- TOC entry 5552 (class 0 OID 33221)
-- Dependencies: 250
-- Data for Name: fund_nav_guard_events; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_nav_guard_events (id, fund_id, snapshot_ts, decision, reason, old_nav_usd, new_nav_usd, old_uta_equity_usd, new_uta_equity_usd, old_funding_wallet_usd, new_funding_wallet_usd, old_earn_usd, new_earn_usd, nav_drop_pct, earn_drop_pct, compensation_ratio, created_at) FROM stdin;
\.


--
-- TOC entry 5550 (class 0 OID 33208)
-- Dependencies: 248
-- Data for Name: fund_nav_guard_state; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_nav_guard_state (fund_id, last_snapshot_ts, nav_usd, uta_equity_usd, funding_wallet_usd, earn_usd, source, updated_at) FROM stdin;
\.


--
-- TOC entry 5517 (class 0 OID 32855)
-- Dependencies: 215
-- Data for Name: fund_nav_minute; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_nav_minute (id, fund_id, ts_utc, nav_usdt, shares_outstanding) FROM stdin;
146	9	2026-04-29 15:56:00+03	687.0029694600	1.0000000000
4	9	2026-04-27 16:34:00+03	693.3885095000	1000.0000000000
5	9	2026-04-27 16:35:00+03	693.7602018900	1000.0000000000
6	9	2026-04-27 16:42:00+03	694.4678492500	1000.0000000000
7	9	2026-04-27 16:43:00+03	693.8130675700	1000.0000000000
73	9	2026-04-27 17:59:00+03	693.7454931200	1.0000000000
79	9	2026-04-27 18:00:00+03	693.7995494500	1.0000000000
8	9	2026-04-27 17:48:00+03	697.0490762100	1.0000000000
80	9	2026-04-27 18:01:00+03	693.3309983900	1.0000000000
13	9	2026-04-27 17:49:00+03	696.8543193200	1.0000000000
147	9	2026-04-29 15:57:00+03	687.1239982500	1.0000000000
19	9	2026-04-27 17:50:00+03	696.7461390900	1.0000000000
84	9	2026-04-27 18:02:00+03	692.4501621800	1.0000000000
153	9	2026-04-29 15:58:00+03	687.1370534000	1.0000000000
25	9	2026-04-27 17:51:00+03	696.6125466600	1.0000000000
157	9	2026-04-29 16:02:00+03	687.5509459700	1.0000000000
90	9	2026-04-27 18:03:00+03	692.6596845100	1.0000000000
31	9	2026-04-27 17:52:00+03	695.6408955300	1.0000000000
96	9	2026-04-27 18:04:00+03	692.3793243900	1.0000000000
37	9	2026-04-27 17:53:00+03	694.3660550200	1.0000000000
98	9	2026-04-27 18:05:00+03	691.3192808800	1.0000000000
43	9	2026-04-27 17:54:00+03	694.7653324900	1.0000000000
160	9	2026-04-29 16:03:00+03	687.2263255500	1.0000000000
102	9	2026-04-27 18:06:00+03	690.5541250900	1.0000000000
49	9	2026-04-27 17:55:00+03	694.3218683600	1.0000000000
166	9	2026-04-29 16:04:00+03	687.0491671900	1.0000000000
55	9	2026-04-27 17:56:00+03	694.2045050300	1.0000000000
108	9	2026-04-27 18:07:00+03	690.0503679000	1.0000000000
61	9	2026-04-27 17:57:00+03	694.4476548000	1.0000000000
114	9	2026-04-29 14:33:00+03	690.5423107600	1.0000000000
67	9	2026-04-27 17:58:00+03	694.0680635900	1.0000000000
119	9	2026-04-29 14:34:00+03	690.5997914700	1.0000000000
125	9	2026-04-29 14:35:00+03	690.6399566800	1.0000000000
126	9	2026-04-29 14:36:00+03	690.8486086800	1.0000000000
127	9	2026-04-29 14:37:00+03	691.1981105300	1.0000000000
133	9	2026-04-29 14:38:00+03	690.7987399200	1.0000000000
139	9	2026-04-29 14:39:00+03	690.5861957900	1.0000000000
145	9	2026-04-29 14:40:00+03	690.4655453300	1.0000000000
\.


--
-- TOC entry 5573 (class 0 OID 33643)
-- Dependencies: 271
-- Data for Name: fund_negative_bybit_flows; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_negative_bybit_flows (id, settlement_batch_id, sale_batch_id, fund_id, status, coin, chain, required_master_usdt, withdrawal_request_amount_usdt, bybit_withdrawal_fee_usdt, retained_fees_usdt, settlement_wallet_id, settlement_wallet_address, preflight_passed, preflight_error, preflight_json, from_sub_uid, to_master_uid, from_account_type, to_account_type, universal_transfer_id, universal_transfer_status, universal_transfer_amount_usdt, universal_transfer_coin, universal_transfer_created_at, universal_transfer_confirmed_at, universal_transfer_mock_json, universal_transfer_reconciliation_json, withdrawal_request_id, withdrawal_id, withdrawal_status, withdrawal_amount_usdt, withdrawal_fee_usdt, withdrawal_coin, withdrawal_chain, withdrawal_address, withdrawal_tx_hash, withdrawal_created_at, withdrawal_confirmed_at, withdrawal_mock_json, withdrawal_record_json, withdrawal_reconciliation_json, settlement_wallet_receipt_status, settlement_wallet_received_usdt, settlement_wallet_receipt_tx_hash, settlement_wallet_receipt_confirmed_at, settlement_wallet_receipt_json, reconciliation_json, report_json, error, created_at, updated_at) FROM stdin;
\.


--
-- TOC entry 5579 (class 0 OID 33802)
-- Dependencies: 277
-- Data for Name: fund_negative_finalization_batches; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_negative_finalization_batches (id, settlement_batch_id, payout_batch_id, bybit_flow_id, sale_batch_id, fund_id, status, settlement_price_usdt, shares_outstanding_before, shares_outstanding_after, buy_order_count, redeem_order_count, success_order_count, total_buy_usdt, total_buy_shares, total_redeem_shares, planned_net_shares_change, actual_net_shares_change, total_net_user_payout_usdt, total_partial_month_fee_usdt, positions_before_json, positions_after_json, user_wallet_reserves_before_json, user_wallet_reserves_after_json, order_updates_json, fund_update_json, pricing_lock_json, validation_json, accounting_json, reconciliation_json, report_json, finalization_started_at, accounting_finalized_at, pricing_unlocked_at, completed_at, error, created_at, updated_at) FROM stdin;
\.


--
-- TOC entry 5575 (class 0 OID 33690)
-- Dependencies: 273
-- Data for Name: fund_negative_payout_batches; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_negative_payout_batches (id, settlement_batch_id, bybit_flow_id, fund_id, status, coin, chain, settlement_wallet_id, settlement_wallet_address, expected_total_payout_usdt, planned_total_payout_usdt, confirmed_total_payout_usdt, payout_leg_count, confirmed_payout_leg_count, gas_status, settlement_wallet_bnb_before, settlement_wallet_bnb_required, settlement_wallet_bnb_after, ok_gas_wallet_bnb_available, gas_topup_required_bnb, gas_topup_amount_bnb, gas_topup_tx_hash, gas_topup_mock_json, gas_reconciliation_json, operator_action_id, pause_reason, payout_started_at, payout_completed_at, settlement_wallet_usdt_before, settlement_wallet_usdt_after, balance_refresh_status, balance_refresh_started_at, balance_refresh_completed_at, balance_refresh_json, payout_plan_json, payout_execution_json, reconciliation_json, report_json, error, created_at, updated_at) FROM stdin;
\.


--
-- TOC entry 5577 (class 0 OID 33736)
-- Dependencies: 275
-- Data for Name: fund_negative_payout_legs; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_negative_payout_legs (id, payout_batch_id, settlement_batch_id, bybit_flow_id, fund_id, user_id, user_wallet_id, status, coin, chain, from_settlement_wallet_id, from_address, to_user_wallet_id, to_address, amount_usdt, order_ids_json, order_allocations_json, deterministic_key, tx_hash, confirmations, sent_at, confirmed_at, failed_at, wallet_balance_before_usdt, wallet_balance_after_usdt, payout_mock_json, confirmation_json, balance_refresh_json, error, created_at, updated_at) FROM stdin;
\.


--
-- TOC entry 5569 (class 0 OID 33551)
-- Dependencies: 267
-- Data for Name: fund_negative_sale_batches; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_negative_sale_batches (id, settlement_batch_id, fund_id, status, required_master_usdt, withdrawal_request_amount_usdt, total_net_user_payout_usdt, total_partial_month_fee_usdt, bybit_withdrawal_fee_usdt, unified_usdt_available, fund_wallet_usdt_available, usdt_earn_available, total_cash_like_available_usdt, sale_target_usdt, planned_sale_usdt, expected_shortage_usdt, expected_surplus_usdt, largest_extra_sale_buffer_pct, snapshot_json, plan_json, report_json, error, snapshot_created_at, plan_created_at, created_at, updated_at, execution_started_at, execution_completed_at, available_usdt_before_execution, initial_cash_like_usdt, usdt_earn_redeemed_usdt, initial_sale_executed_usdt, available_usdt_after_initial_sales, shortage_after_initial_sales_usdt, extra_sale_required_usdt, extra_sale_target_usdt, extra_sale_executed_usdt, final_available_usdt, final_shortage_usdt, final_surplus_usdt, execution_json, reconciliation_json) FROM stdin;
\.


--
-- TOC entry 5571 (class 0 OID 33575)
-- Dependencies: 269
-- Data for Name: fund_negative_sale_legs; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_negative_sale_legs (id, sale_batch_id, settlement_batch_id, fund_id, leg_index, leg_group, leg_type, coin, symbol, category, side, location, current_qty, current_size, current_usd_value, current_notional_usd, source_weight, target_cash_usdt, target_qty, expected_cash_delta_usdt, eligible, eligibility_reason, use_for_deficit_cover, instrument_status, min_order_passed, liquidity_check_required, margin_guard_required, planned_execution_mode, order_link_id, strategy_id, status, error, created_at, updated_at, actual_execution_mode, execution_round, deterministic_key, bybit_order_id, bybit_strategy_id, planned_suborders, executed_suborders, suborders_json, mock_execution_json, last_price, best_bid, best_ask, corridor_pct, available_liquidity_usdt, available_liquidity_qty, filled_qty, filled_usdt, avg_fill_price, fill_ratio, unfilled_usdt, fee_usdt, cash_delta_usdt, sent_at, confirmed_at, failed_at, execution_error) FROM stdin;
\.


--
-- TOC entry 5585 (class 0 OID 33915)
-- Dependencies: 283
-- Data for Name: fund_operation_guard_events; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_operation_guard_events (id, action_type, scope_key, scope_type, fund_id, settlement_batch_id, request_id, amount_usdt, decision, reason, guard_state_id, override_id, mode_snapshot, metadata_json, created_at) FROM stdin;
\.


--
-- TOC entry 5583 (class 0 OID 33881)
-- Dependencies: 281
-- Data for Name: fund_operation_guard_overrides; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_operation_guard_overrides (id, scope_key, scope_type, fund_id, action_type, status, manager_user_id, settlement_batch_id, request_id, idempotency_key, max_amount_usdt, starts_at, expires_at, used_at, revoked_at, reason, payload_json, result_json, created_at, updated_at) FROM stdin;
\.


--
-- TOC entry 5581 (class 0 OID 33854)
-- Dependencies: 279
-- Data for Name: fund_operation_guard_state; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_operation_guard_state (id, scope_key, scope_type, fund_id, action_type, mode, reason, updated_by_user_id, created_at, updated_at) FROM stdin;
\.


--
-- TOC entry 5567 (class 0 OID 33509)
-- Dependencies: 265
-- Data for Name: fund_operator_actions; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_operator_actions (id, fund_id, settlement_batch_id, allocation_batch_id, action_type, reason, status, idempotency_key, callback_token_hash, telegram_chat_id, telegram_user_id, telegram_message_id, telegram_callback_query_id, requested_by, requested_at, processing_started_at, processed_at, expires_at, attempts, payload_json, result_json, error, created_at, updated_at) FROM stdin;
\.


--
-- TOC entry 5539 (class 0 OID 33078)
-- Dependencies: 237
-- Data for Name: fund_orders; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_orders (id, user_id, fund_id, side, amount_usdt, shares, price_usdt, status, created_at, executed_at, settlement_batch_id, reserved_at, settlement_locked_at, collection_confirmed_at, error, gross_redeem_usdt, success_fee_usdt, management_fee_usdt, partial_month_fee_usdt, net_user_payout_usdt, net_price_usdt, fee_calc_month_open_price_usdt, fee_calc_days_in_month_period, success_fee_rate, management_fee_rate) FROM stdin;
\.


--
-- TOC entry 5559 (class 0 OID 33344)
-- Dependencies: 257
-- Data for Name: fund_runtime_state; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_runtime_state (fund_id, pricing_locked, pricing_lock_reason, pricing_lock_batch_id, pricing_locked_at, pricing_unlocked_at, created_at, updated_at) FROM stdin;
\.


--
-- TOC entry 5556 (class 0 OID 33268)
-- Dependencies: 254
-- Data for Name: fund_settlement_batches; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_settlement_batches (id, fund_id, settlement_date, cutoff_ts, settlement_ts, price_ts, settlement_price_usdt, nav_usdt, shares_outstanding_before, total_buy_usdt, total_redeem_shares, total_redeem_usdt, net_cash_usdt, planned_shares_to_issue, planned_shares_to_redeem, planned_net_shares_change, status, error, pricing_locked_at, pricing_unlocked_at, created_at, updated_at, positive_net_started_at, seller_payouts_completed_at, bybit_deposit_tx_hash, bybit_deposit_confirmed_at, bybit_deposit_account_type, bybit_internal_transfer_id, bybit_internal_transfer_completed_at, accounting_finalized_at, bybit_deposit_record_id, bybit_deposit_to_address, bybit_deposit_success_at, bybit_internal_transfer_status, bybit_internal_transfer_error, total_gross_redeem_usdt, total_net_user_payout_usdt, total_success_fee_usdt, total_management_fee_usdt, total_partial_month_fee_usdt, bybit_withdrawal_fee_usdt, required_master_usdt, withdrawal_request_amount_usdt, negative_net_target_calculated_at, fee_calc_month_open_price_usdt, fee_calc_month_open_source, fee_calc_days_in_month_period) FROM stdin;
\.


--
-- TOC entry 5558 (class 0 OID 33305)
-- Dependencies: 256
-- Data for Name: fund_settlement_transfers; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_settlement_transfers (id, batch_id, order_id, fund_id, user_id, transfer_type, from_address, to_address, amount_usdt, amount_bnb, gas_tx_hash, tx_hash, status, attempts, error, created_at, updated_at, sent_at, confirmed_at) FROM stdin;
\.


--
-- TOC entry 5554 (class 0 OID 33245)
-- Dependencies: 252
-- Data for Name: fund_wallets; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_wallets (id, fund_id, blockchain, wallet_type, address, encrypted_private_key, derivation_path, derivation_index, is_active, created_at, archived_at) FROM stdin;
1	5	BSC	settlement	0x061ac2E755C146D0BbCCA734c123F7c40075F420	gAAAAABqDa22elgZjOrtzL16AvQQKIJGddStf97iW0NUdHTdZmToda2POp4jn316zJ3Ws6D9DmsfYTq8n_SgTYwMdW8KNpfQ1Qn1RdcFmaCahZoowZBgaOFpscSnl9J-J2LGk6rOmYCAyesubj4p6UIpR_ItfYKVxOa5xp3GwEDipMKlCpJDHQU=	m/44'/60'/0'/0/0	0	t	2026-05-20 15:48:54.749485+03	\N
2	4	BSC	settlement	0xf9abD195e0B7f016933D14E7D9B556D5c2882e2d	gAAAAABqDa22Fu85Nkv7W4pRWXz-NEb3AjbbjsCYBd0XuA1RVygUIyHnmCQNYXwvreWX-CrYr2zUq_b_1hah1glBR_3ePYx4qnkAuJ2I-pLFfunT8nn3gN_KkhUlMgsPF0ah2S2X9E5HUATCNhNyxN0I5EIbWcpqxSyDa8Ov_9OGDY-J6pG5emA=	m/44'/60'/0'/0/1	1	t	2026-05-20 15:48:54.749485+03	\N
3	6	BSC	settlement	0x3f182B603fECc2E9a515EabdDFca38E00b8658b9	gAAAAABqDa22l6gX6u8EM0B-Ge3u77Sei4L3tQkA2WV5hHa5p4hIaKUFUzO_tWWDBlNA6dgBOBKmwmhEWazLYyr6HK4EvHeek1MCEqXekmoQ7hzlIHlirz5mkX8221rh2oKOn2e80glZMqO4eQ0NXVV8FxPORxdoxmWqiCnl_by6iQmx6DTqvjc=	m/44'/60'/0'/0/2	2	t	2026-05-20 15:48:54.749485+03	\N
4	9	BSC	settlement	0x87369aE37c8ed3750b204CC159a5AC1a45cD5186	gAAAAABqDa22nXcLXbOIV2uHtxKQSm4vFj8S_j0uzUJUis9pm4ZKN1fpJrCFc8PhN5rBqibF6akiwCWDxa4n9ThB-Lpgtc84TmDoFRMrnDhKr4lEzzxXPXJAckXNvCa3YdHngBWCIh6XSHTFgR3rv_SqUAkjLJAkiMWkuhDgQL1Ny6WEJVjCxYI=	m/44'/60'/0'/0/3	3	t	2026-05-20 15:48:54.749485+03	\N
5	7	BSC	settlement	0x1d4F14e2475D26A82921DF3D2F05f9d428835a59	gAAAAABqDa22Mm1gYztBiDp8MHzPq6MfuYxfvk7CrVKE8eGzbUuqOrHKge4Bbmx42TpHSkiNiyomT5fje_dJSh4KO_S6_izkzEBkQSK3xoKJ3JRjaU7acJDj9Cf2az_2PgHHqZVV9pG9QP_hpMfpMvOskhi56ImaypEvHBRR14smXxyrJLVCIOY=	m/44'/60'/0'/0/4	4	t	2026-05-20 15:48:54.749485+03	\N
6	8	BSC	settlement	0x7d45d2c1Fe0D7DB8cA55Cc6E2a8835c2b75c9D4d	gAAAAABqDa22z6YZDWHhXiRsvtNP7EE2UirKYN6NvDeiRkHWSw0A8SyXTz6VWnkthznHhBxrMxYH3RB3enaX9ergm68ovDL8HTsTcuW_USNYsUbg9K7PgZbUGjz3aDJ5Dp5EJBrCBilrcZe10PFD6zGJ_6cAT-l3o6sLzmWCL6LbPsC5Msqg_9I=	m/44'/60'/0'/0/5	5	t	2026-05-20 15:48:54.749485+03	\N
\.


--
-- TOC entry 5519 (class 0 OID 32859)
-- Dependencies: 217
-- Data for Name: funds; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.funds (id, code, name_ru, name_en, category, sort_order, is_active, short_name_ru, short_name_en, full_name_ru, full_name_en, benchmark_name_ru, benchmark_name_en, management_fee_pct, performance_fee_pct, icon_name, launch_date, shares_outstanding_current) FROM stdin;
8	wb_web3	WB Web 3.0	WB Web 3.0	index	30	t	WB WEB 3.0	WB WEB 3.0	Wild Boar WEB 3.0 fund	Wild Boar WEB 3.0 fund	-	-	0.0800	\N	fund-wb-web3.svg	\N	0.0000000000
7	wb_defi	WB DeFi	WB DeFi	index	20	t	WB DeFi	WB DeFi	Wild Boar DeFi fund	Wild Boar DeFi fund	-	-	0.0800	\N	fund-wb-defi.svg	\N	0.0000000000
9	wb_test	WB test fund	WB test fund	test	10	t	WB Test	WB Test	Wild Boar Test fund	Wild Boar Test fund	-	-	0.0800	\N	fund-test.svg	\N	1.0000000000
5	btc_fund	Bitcoin fund	Bitcoin fund	active	20	t	WB Bitcoin	WB Bitcoin	Wild Boar Bitcoin fund	Wild Boar Bitcoin fund	Bitcoin	Bitcoin	0.0800	0.8300	fund-btc.svg	2024-05-19	6.7747000000
4	defi_sniper	DeFi Sniper	DeFi Sniper	active	10	t	WB DeFi Sniper	WB DeFi Sniper	Wild Boar DeFi Sniper fund	Wild Boar DeFi Sniper fund	Altcoin Index WildBoar	Altcoin Index WildBoar	0.1700	1.6700	fund-defi-sniper.svg	2024-05-08	30.0460000000
6	wb10	WB 10	WB 10	index	10	t	WB 10	WB 10	Wild Boar Top 10 fund	Wild Boar Top 10 fund	-	-	0.0800	\N	fund-wb10.svg	2024-05-23	0.6849000000
\.


--
-- TOC entry 5521 (class 0 OID 32865)
-- Dependencies: 219
-- Data for Name: password_reset_sessions; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.password_reset_sessions (id, user_id, created_at, expires_at, is_used) FROM stdin;
37d8e04949354104afe6c3fe76ee7b2b	1	2026-04-06 18:36:19.97126+03	2026-04-06 19:06:19.97126+03	t
\.


--
-- TOC entry 5522 (class 0 OID 32870)
-- Dependencies: 220
-- Data for Name: security_codes; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.security_codes (id, user_id, purpose, code, created_at, expires_at, is_used, attempts) FROM stdin;
1	1	registration	849530	2026-03-04 18:23:32.179189+03	2026-03-04 18:38:32.179448+03	t	1
2	1	registration	846828	2026-03-04 18:24:57.242629+03	2026-03-04 18:39:57.242853+03	t	1
3	1	password_change	034668	2026-03-04 18:25:42.620298+03	2026-03-04 18:40:42.621353+03	t	1
4	1	login_2fa	792940	2026-03-05 12:26:39.289125+03	2026-03-05 12:41:39.522255+03	t	1
5	1	withdraw	919968	2026-03-08 18:06:05.206073+03	2026-03-08 18:21:05.206297+03	t	0
6	1	withdraw	687745	2026-03-09 15:42:29.62297+03	2026-03-09 15:57:29.622787+03	t	0
7	1	withdraw	631119	2026-03-09 16:40:41.756396+03	2026-03-09 16:55:41.757286+03	t	0
8	1	withdraw	663467	2026-03-09 16:42:01.516691+03	2026-03-09 16:57:01.516646+03	t	0
9	1	withdraw	876575	2026-03-09 16:46:34.068249+03	2026-03-09 17:01:34.06824+03	t	0
10	1	withdraw	659039	2026-03-09 16:47:36.793486+03	2026-03-09 17:02:36.799289+03	t	0
11	1	withdraw	762463	2026-03-09 16:53:19.692797+03	2026-03-09 17:08:19.692944+03	t	0
13	1	registration	686384	2026-03-09 16:58:24.236717+03	2026-03-09 17:13:24.236757+03	t	1
12	1	withdraw	793023	2026-03-09 16:54:37.541542+03	2026-03-09 17:09:37.546935+03	t	0
14	1	withdraw	552200	2026-03-09 16:59:02.363148+03	2026-03-09 17:14:02.362861+03	t	1
15	1	withdraw	644281	2026-03-10 11:37:11.095547+03	2026-03-10 11:52:11.097154+03	t	0
16	1	withdraw	990328	2026-03-12 20:46:49.475748+03	2026-03-12 21:01:49.477801+03	t	0
17	1	withdraw	631273	2026-03-12 20:47:52.275919+03	2026-03-12 21:02:52.277773+03	t	0
18	1	withdraw	807531	2026-03-12 20:59:30.050913+03	2026-03-12 21:14:30.053064+03	t	0
19	1	withdraw	630532	2026-03-13 17:38:42.545698+03	2026-03-13 17:53:42.548852+03	t	0
22	1	reset	681653	2026-04-06 18:36:01.764677+03	2026-04-06 18:51:01.764261+03	t	1
23	1	login_2fa	319753	2026-04-06 18:36:51.694489+03	2026-04-06 18:51:51.93668+03	t	1
\.


--
-- TOC entry 5524 (class 0 OID 32878)
-- Dependencies: 222
-- Data for Name: sessions; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.sessions (id, user_id, created_at, expires_at) FROM stdin;
5078a332eeec471a957239a68c0ca25e	1	2026-04-06 18:37:10.272038+03	2026-05-06 18:37:10.272038+03
e26e8dd62fb17498bace60c5da3893ed0f18a312f26eb6d3f8e66fb41118d3d6	1	2026-05-14 16:16:43.277333+03	2026-06-13 16:16:43.277333+03
\.


--
-- TOC entry 5541 (class 0 OID 33100)
-- Dependencies: 239
-- Data for Name: user_fund_position_stats; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.user_fund_position_stats (id, user_id, fund_id, avg_entry_price_usdt, updated_at) FROM stdin;
\.


--
-- TOC entry 5525 (class 0 OID 32882)
-- Dependencies: 223
-- Data for Name: user_fund_positions; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.user_fund_positions (id, user_id, fund_id, shares, shares_reserved) FROM stdin;
\.


--
-- TOC entry 5527 (class 0 OID 32887)
-- Dependencies: 225
-- Data for Name: user_portfolio_daily; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.user_portfolio_daily (id, user_id, date_utc, balance_usdt, created_at) FROM stdin;
1	1	2026-05-05	3.2000000000	2026-05-05 17:33:06.04022+03
2	1	2026-05-06	3.2000000000	2026-05-06 17:31:59.599078+03
3	1	2026-05-14	3.2000000000	2026-05-14 16:21:40.726081+03
\.


--
-- TOC entry 5547 (class 0 OID 33174)
-- Dependencies: 245
-- Data for Name: user_totp_recovery_codes; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.user_totp_recovery_codes (id, user_id, code_hash, is_used, used_at, created_at) FROM stdin;
\.


--
-- TOC entry 5529 (class 0 OID 32892)
-- Dependencies: 227
-- Data for Name: user_wallets; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.user_wallets (id, user_id, blockchain, address, encrypted_private_key, created_at, usdt_balance, usdt_balance_updated_at, usdt_balance_block, usdt_reserved, compliance_status, freeze_reason, compliance_checked_at, is_active, archived_at) FROM stdin;
1	1	BSC	0xcb97Ea3C8Ff8187026901F6Db5977E1b1815D1d8	gAAAAABpqE6UH_WoP4t0f3JvcXKWh-XwZsJPANgq9gWgjCFGBjCPZJEMgg56xeUhxfUAXeO8nsDaP4YI9Wg09u4sOIHb4Horl6Rjb6KrcSOGRCY_d-c34Dd4m4RxqQRBocdnPpcQEokUpbBhsNFVMqDkTXlSMtGwbZtDfXvzDTl_7WTKO76E9_M=	2026-03-04 18:24:04.389618+03	3.200000000000000000	2026-03-10 11:33:02.384649+03	85745362	0.000000000000000000	ok	\N	2026-03-10 11:33:50.820757+03	t	\N
\.


--
-- TOC entry 5531 (class 0 OID 32904)
-- Dependencies: 229
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.users (id, created_at, email, first_name, last_name, phone, password_hash, is_active, is_email_verified, two_factor_enabled, account_type, backup_email, is_backup_email_verified, compliance_status, compliance_reason, compliance_updated_at, non_us_citizen_confirmed, non_us_citizen_confirmed_at, totp_enabled, totp_secret_encrypted, totp_confirmed_at, totp_last_used_step, cookie_notice_acknowledged, cookie_notice_acknowledged_at) FROM stdin;
1	2026-03-04 18:23:31.870297+03	kobra_rey99@mail.ru	Kirill	Vokulov	034 466 90 92	$2b$12$1SJHPJoIGFbdY2FICuaOi.u/e.Op6upXnPk0bzpiT9qsfoVKf1Pge	t	t	t	basic	volchypastyh@gmail.com	t	ok	\N	2026-03-10 11:33:50.821766+03	f	\N	f	gAAAAABp-gBqWngLj24LBpCPQ7e7nxfQ86QjLbWN9huXh9dihLg_BoV-X1uTjU39VJxKMNjfwaBJWuVrhymAjP6AnMDtFKiXp0vEym_-NHs7g491GZKldtL4TX-ktlc8uGXYwCS6Mjy3	\N	\N	f	\N
\.


--
-- TOC entry 5533 (class 0 OID 32919)
-- Dependencies: 231
-- Data for Name: wallet_transfers; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.wallet_transfers (id, user_id, wallet_id, coin, network, type, from_address, to_address, tx_hash, log_index, amount, status, compliance_status, block_number, tx_time, detected_at, confirmed_at, compliance_checked_at, compliance_details, amount_gross, fee_usdt, gas_tx_hash, fee_tx_hash, email_slot, error) FROM stdin;
1	1	1	USDT	BSC (BEP20)	deposit	0x480680002c0627eB79A38025bB72Dc05bF32392E	0xcb97Ea3C8Ff8187026901F6Db5977E1b1815D1d8	0xd99ab09315303b0781d4cae412abe066c42681c0a5e6a3891f4f35b4bf79fed7	10	1.010000000000000000	success	ok	84664855	2026-03-04 20:27:53+03	2026-03-04 20:29:43.40681+03	2026-03-04 20:31:12.51804+03	2026-03-04 20:32:40.213634+03	{"final": "ok", "tx_hash": "0xd99ab09315303b0781d4cae412abe066c42681c0a5e6a3891f4f35b4bf79fed7", "addresses": {"0x480680002c0627eb79a38025bb72dc05bf32392e": {"final": "ok", "details": {"ts": "2026-03-04T17:32:39.532154+00:00", "oracle": {"status": "ok", "details": {"isSanctioned": false}}, "address": "0x480680002c0627eb79a38025bb72dc05bf32392e", "ofac_local": {"status": "ok", "details": {"hit": false}}, "chainalysis_api": {"status": "ok", "details": {"identifications": []}}}}}, "checked_at": "2026-03-04T17:32:40.211635+00:00", "transfer_id": 1}	\N	1.000000000000000000	\N	\N	\N	\N
6	1	1	USDT	BSC (BEP20)	deposit	0x480680002c0627eB79A38025bB72Dc05bF32392E	0xcb97Ea3C8Ff8187026901F6Db5977E1b1815D1d8	0x50c866a02c489493d151faa321d5fa94c1b6cb1ff82c98ba412f2b27b49e9afd	129	3.200000000000000000	success	ok	85745297	2026-03-10 11:32:32+03	2026-03-10 11:32:33.286779+03	2026-03-10 11:32:48.375861+03	2026-03-10 11:33:50.817761+03	{"final": "ok", "tx_hash": "0x50c866a02c489493d151faa321d5fa94c1b6cb1ff82c98ba412f2b27b49e9afd", "addresses": {"0x480680002c0627eb79a38025bb72dc05bf32392e": {"final": "ok", "details": {"ts": "2026-03-10T08:33:50.122466+00:00", "oracle": {"status": "ok", "details": {"isSanctioned": false}}, "address": "0x480680002c0627eb79a38025bb72dc05bf32392e", "ofac_local": {"status": "ok", "details": {"hit": false}}, "chainalysis_api": {"status": "ok", "details": {"identifications": []}}}}}, "checked_at": "2026-03-10T08:33:50.815666+00:00", "transfer_id": 6}	\N	1.000000000000000000	\N	\N	\N	\N
2	1	1	USDT	BSC (BEP20)	deposit	0x480680002c0627eB79A38025bB72Dc05bF32392E	0xcb97Ea3C8Ff8187026901F6Db5977E1b1815D1d8	0x55c826f1a1e8c41185abdd894acfdd6141c298ea5be31d0280bdd7c59016b123	3	1.000000000000000000	success	ok	84669331	2026-03-04 21:01:28+03	2026-03-04 21:03:12.080631+03	2026-03-04 21:03:58.838698+03	2026-03-04 21:12:35.06787+03	{"final": "ok", "tx_hash": "0x55c826f1a1e8c41185abdd894acfdd6141c298ea5be31d0280bdd7c59016b123", "addresses": {"0x480680002c0627eb79a38025bb72dc05bf32392e": {"final": "ok", "details": {"ts": "2026-03-04T18:12:34.363601+00:00", "oracle": {"status": "ok", "details": {"isSanctioned": false}}, "address": "0x480680002c0627eb79a38025bb72dc05bf32392e", "ofac_local": {"status": "ok", "details": {"hit": false}}, "chainalysis_api": {"status": "ok", "details": {"identifications": []}}}}}, "checked_at": "2026-03-04T18:12:35.065870+00:00", "transfer_id": 2}	\N	1.000000000000000000	\N	\N	\N	\N
3	1	1	USDT	BSC (BEP20)	deposit	0x480680002c0627eB79A38025bB72Dc05bF32392E	0xcb97Ea3C8Ff8187026901F6Db5977E1b1815D1d8	0x37492ccdbc84743b7a23eed2ac2d3daaf18e99645a35fead968a5a531d823fe4	63	1.000000000000000000	success	ok	84795388	2026-03-05 12:47:05+03	2026-03-05 12:47:01.258043+03	2026-03-05 12:47:23.818444+03	2026-03-05 12:47:29.855066+03	{"final": "ok", "tx_hash": "0x37492ccdbc84743b7a23eed2ac2d3daaf18e99645a35fead968a5a531d823fe4", "addresses": {"0x480680002c0627eb79a38025bb72dc05bf32392e": {"final": "ok", "details": {"ts": "2026-03-05T09:47:29.077629+00:00", "oracle": {"status": "ok", "details": {"isSanctioned": false}}, "address": "0x480680002c0627eb79a38025bb72dc05bf32392e", "ofac_local": {"status": "ok", "details": {"hit": false}}, "chainalysis_api": {"status": "ok", "details": {"identifications": []}}}}}, "checked_at": "2026-03-05T09:47:29.852553+00:00", "transfer_id": 3}	\N	1.000000000000000000	\N	\N	\N	\N
5	1	1	USDT	BSC (BEP20)	withdraw	0xcb97Ea3C8Ff8187026901F6Db5977E1b1815D1d8	0x480680002c0627eB79A38025bB72Dc05bF32392E	a8f92b1b12e94d7bd81ed382218249795209df0d4c0a1b2692de1a2f39ffbf7a	\N	2.010000000000000000	success	ok	85597850	\N	2026-03-09 16:59:15.786148+03	2026-03-09 17:06:35.206788+03	\N	\N	3.010000000000000000	1.000000000000000000	b696fa99ac52cc634635686be07145f8b67ca0e2c0b3ec7c2d3d2794580e9be2	97baf4355927331867b0f88522531611aaecf3b3ca577d15eb911c359cdd4d91	2	\N
\.


--
-- TOC entry 5537 (class 0 OID 33047)
-- Dependencies: 235
-- Data for Name: withdraw_sessions; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.withdraw_sessions (id, token, user_id, wallet_id, to_address, amount_gross, fee_usdt, email_slot, created_at, expires_at, used_at) FROM stdin;
1	e34733d0b7d4e07b09dcec346915473867a271eee952ca9a8e2ae668aade3290	1	1	0x480680002c0627eB79A38025bB72Dc05bF32392E	3.010000000000000000	1.000000000000000000	1	2026-03-08 18:06:05.193355+03	2026-03-08 18:21:05.195658+03	\N
2	9dce4313dc3a82b2c39b0101eca37418e252016a5be5b975d1655f754b6c0fa7	1	1	0x480680002c0627eB79A38025bB72Dc05bF32392E	3.010000000000000000	1.000000000000000000	1	2026-03-08 18:06:40.245167+03	2026-03-08 18:21:40.247581+03	\N
3	8e1c4abb96eb3aebe2b1d6b2ef87c68df10a0df07714de9f4de3a0e878740283	1	1	0x480680002c0627eB79A38025bB72Dc05bF32392E	3.010000000000000000	1.000000000000000000	1	2026-03-09 15:42:29.611552+03	2026-03-09 15:57:29.613784+03	\N
4	ee1d6c114a8a86b0728b5db72bfa963bfe68df6dbd03aa3383435d150d035cb0	1	1	0x480680002c0627eB79A38025bB72Dc05bF32392E	3.010000000000000000	1.000000000000000000	1	2026-03-09 16:40:41.746198+03	2026-03-09 16:55:41.747668+03	\N
5	8eefd4f081acf66c484d86a98e6a7f12a97b7adbcf13cd6d4b00d03134ccd0ac	1	1	0x480680002c0627eB79A38025bB72Dc05bF32392E	3.010000000000000000	1.000000000000000000	1	2026-03-09 16:42:01.511634+03	2026-03-09 16:57:01.513646+03	\N
6	bb42667257555f00601f1d83dc7bc404625d8ab7c51afe97516a045284f50dce	1	1	0x480680002c0627eB79A38025bB72Dc05bF32392E	3.010000000000000000	1.000000000000000000	1	2026-03-09 16:42:05.560927+03	2026-03-09 16:57:05.562449+03	\N
7	ed0a7462d115a45217f7e54f45c8102d2d9a20282f5a4db9b5003c6e2a29b21a	1	1	0x480680002c0627eB79A38025bB72Dc05bF32392E	3.010000000000000000	1.000000000000000000	1	2026-03-09 16:42:05.736556+03	2026-03-09 16:57:05.740817+03	\N
8	fbf6384844713ee84a61376243fd01c950078ad73d7bb2812b48816c2fb80abd	1	1	0x480680002c0627eB79A38025bB72Dc05bF32392E	3.010000000000000000	1.000000000000000000	1	2026-03-09 16:42:05.921545+03	2026-03-09 16:57:05.924307+03	\N
9	f7e9d086784ab5afdc95e71e078705b490e8d114ccf1d1805f7418593cdc064c	1	1	0x480680002c0627eB79A38025bB72Dc05bF32392E	3.010000000000000000	1.000000000000000000	1	2026-03-09 16:46:34.059026+03	2026-03-09 17:01:34.061132+03	\N
10	ba673a330fa53c1a6b728dbf4d68a0e200f95d607ec81545f800c3725a71b89d	1	1	0x480680002c0627eB79A38025bB72Dc05bF32392E	3.010000000000000000	1.000000000000000000	1	2026-03-09 16:53:19.680227+03	2026-03-09 17:08:19.682419+03	\N
11	4301e9737c32bf944186726b92f0890a91f09988a4d05a49b35c9cc2c193bdad	1	1	0x480680002c0627eB79A38025bB72Dc05bF32392E	3.010000000000000000	1.000000000000000000	2	2026-03-09 16:59:02.359114+03	2026-03-09 17:14:02.360846+03	2026-03-09 16:59:15.788706+03
12	652d0942eafa141b06782fc3107d7bccef3a280770fb5fe606a1003374e24d96	1	1	0x480680002c0627eB79A38025bB72Dc05bF32392E	3.200000000000000000	1.000000000000000000	1	2026-03-10 11:37:11.080859+03	2026-03-10 11:52:11.083693+03	\N
13	628f23e3c81842acc8e8096009c24eac690df8870d52a26a3f64aa534cfc1993	1	1	0x480680002c0627eB79A38025bB72Dc05bf32392E	3.200000000000000000	1.000000000000000000	1	2026-03-12 20:47:53.438773+03	2026-03-12 21:02:53.382817+03	\N
\.


--
-- TOC entry 5535 (class 0 OID 33036)
-- Dependencies: 233
-- Data for Name: worker_cursors; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.worker_cursors (name, last_block, last_log_index, updated_at) FROM stdin;
bsc_usdt_listener	85745297	129	2026-03-10 11:32:33.300548+03
\.


--
-- TOC entry 5624 (class 0 OID 0)
-- Dependencies: 246
-- Name: fee_wallet_swaps_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fee_wallet_swaps_id_seq', 1, false);


--
-- TOC entry 5625 (class 0 OID 0)
-- Dependencies: 260
-- Name: fund_allocation_batches_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_allocation_batches_id_seq', 35, true);


--
-- TOC entry 5626 (class 0 OID 0)
-- Dependencies: 262
-- Name: fund_allocation_legs_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_allocation_legs_id_seq', 129, true);


--
-- TOC entry 5627 (class 0 OID 0)
-- Dependencies: 258
-- Name: fund_bybit_accounts_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_bybit_accounts_id_seq', 7, true);


--
-- TOC entry 5628 (class 0 OID 0)
-- Dependencies: 240
-- Name: fund_chart_daily_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_chart_daily_id_seq', 3991, true);


--
-- TOC entry 5629 (class 0 OID 0)
-- Dependencies: 242
-- Name: fund_chart_minute_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_chart_minute_id_seq', 165, true);


--
-- TOC entry 5630 (class 0 OID 0)
-- Dependencies: 249
-- Name: fund_nav_guard_events_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_nav_guard_events_id_seq', 1, false);


--
-- TOC entry 5631 (class 0 OID 0)
-- Dependencies: 216
-- Name: fund_nav_minute_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_nav_minute_id_seq', 169, true);


--
-- TOC entry 5632 (class 0 OID 0)
-- Dependencies: 270
-- Name: fund_negative_bybit_flows_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_negative_bybit_flows_id_seq', 104, true);


--
-- TOC entry 5633 (class 0 OID 0)
-- Dependencies: 276
-- Name: fund_negative_finalization_batches_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_negative_finalization_batches_id_seq', 12, true);


--
-- TOC entry 5634 (class 0 OID 0)
-- Dependencies: 272
-- Name: fund_negative_payout_batches_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_negative_payout_batches_id_seq', 39, true);


--
-- TOC entry 5635 (class 0 OID 0)
-- Dependencies: 274
-- Name: fund_negative_payout_legs_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_negative_payout_legs_id_seq', 76, true);


--
-- TOC entry 5636 (class 0 OID 0)
-- Dependencies: 266
-- Name: fund_negative_sale_batches_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_negative_sale_batches_id_seq', 118, true);


--
-- TOC entry 5637 (class 0 OID 0)
-- Dependencies: 268
-- Name: fund_negative_sale_legs_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_negative_sale_legs_id_seq', 101, true);


--
-- TOC entry 5638 (class 0 OID 0)
-- Dependencies: 282
-- Name: fund_operation_guard_events_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_operation_guard_events_id_seq', 31, true);


--
-- TOC entry 5639 (class 0 OID 0)
-- Dependencies: 280
-- Name: fund_operation_guard_overrides_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_operation_guard_overrides_id_seq', 15, true);


--
-- TOC entry 5640 (class 0 OID 0)
-- Dependencies: 278
-- Name: fund_operation_guard_state_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_operation_guard_state_id_seq', 11, true);


--
-- TOC entry 5641 (class 0 OID 0)
-- Dependencies: 264
-- Name: fund_operator_actions_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_operator_actions_id_seq', 7, true);


--
-- TOC entry 5642 (class 0 OID 0)
-- Dependencies: 236
-- Name: fund_orders_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_orders_id_seq', 237, true);


--
-- TOC entry 5643 (class 0 OID 0)
-- Dependencies: 253
-- Name: fund_settlement_batches_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_settlement_batches_id_seq', 185, true);


--
-- TOC entry 5644 (class 0 OID 0)
-- Dependencies: 255
-- Name: fund_settlement_transfers_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_settlement_transfers_id_seq', 33, true);


--
-- TOC entry 5645 (class 0 OID 0)
-- Dependencies: 251
-- Name: fund_wallets_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_wallets_id_seq', 92, true);


--
-- TOC entry 5646 (class 0 OID 0)
-- Dependencies: 218
-- Name: funds_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.funds_id_seq', 132, true);


--
-- TOC entry 5647 (class 0 OID 0)
-- Dependencies: 221
-- Name: security_codes_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.security_codes_id_seq', 23, true);


--
-- TOC entry 5648 (class 0 OID 0)
-- Dependencies: 238
-- Name: user_fund_position_stats_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.user_fund_position_stats_id_seq', 1, false);


--
-- TOC entry 5649 (class 0 OID 0)
-- Dependencies: 224
-- Name: user_fund_positions_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.user_fund_positions_id_seq', 163, true);


--
-- TOC entry 5650 (class 0 OID 0)
-- Dependencies: 226
-- Name: user_portfolio_daily_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.user_portfolio_daily_id_seq', 3, true);


--
-- TOC entry 5651 (class 0 OID 0)
-- Dependencies: 244
-- Name: user_totp_recovery_codes_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.user_totp_recovery_codes_id_seq', 1, false);


--
-- TOC entry 5652 (class 0 OID 0)
-- Dependencies: 228
-- Name: user_wallets_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.user_wallets_id_seq', 187, true);


--
-- TOC entry 5653 (class 0 OID 0)
-- Dependencies: 230
-- Name: users_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.users_id_seq', 192, true);


--
-- TOC entry 5654 (class 0 OID 0)
-- Dependencies: 232
-- Name: wallet_transfers_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.wallet_transfers_id_seq', 6, true);


--
-- TOC entry 5655 (class 0 OID 0)
-- Dependencies: 234
-- Name: withdraw_sessions_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.withdraw_sessions_id_seq', 17, true);


--
-- TOC entry 5147 (class 2606 OID 33204)
-- Name: fee_wallet_swaps fee_wallet_swaps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fee_wallet_swaps
    ADD CONSTRAINT fee_wallet_swaps_pkey PRIMARY KEY (id);


--
-- TOC entry 5188 (class 2606 OID 33435)
-- Name: fund_allocation_batches fund_allocation_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_batches
    ADD CONSTRAINT fund_allocation_batches_pkey PRIMARY KEY (id);


--
-- TOC entry 5208 (class 2606 OID 33462)
-- Name: fund_allocation_legs fund_allocation_legs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_legs
    ADD CONSTRAINT fund_allocation_legs_pkey PRIMARY KEY (id);


--
-- TOC entry 5182 (class 2606 OID 33374)
-- Name: fund_bybit_accounts fund_bybit_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_bybit_accounts
    ADD CONSTRAINT fund_bybit_accounts_pkey PRIMARY KEY (id);


--
-- TOC entry 5134 (class 2606 OID 33133)
-- Name: fund_chart_daily fund_chart_daily_fund_ts_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_daily
    ADD CONSTRAINT fund_chart_daily_fund_ts_unique UNIQUE (fund_id, ts_utc);


--
-- TOC entry 5136 (class 2606 OID 33126)
-- Name: fund_chart_daily fund_chart_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_daily
    ADD CONSTRAINT fund_chart_daily_pkey PRIMARY KEY (id);


--
-- TOC entry 5139 (class 2606 OID 33148)
-- Name: fund_chart_minute fund_chart_minute_fund_ts_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_minute
    ADD CONSTRAINT fund_chart_minute_fund_ts_unique UNIQUE (fund_id, ts_utc);


--
-- TOC entry 5141 (class 2606 OID 33141)
-- Name: fund_chart_minute fund_chart_minute_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_minute
    ADD CONSTRAINT fund_chart_minute_pkey PRIMARY KEY (id);


--
-- TOC entry 5154 (class 2606 OID 33230)
-- Name: fund_nav_guard_events fund_nav_guard_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_events
    ADD CONSTRAINT fund_nav_guard_events_pkey PRIMARY KEY (id);


--
-- TOC entry 5150 (class 2606 OID 33214)
-- Name: fund_nav_guard_state fund_nav_guard_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_state
    ADD CONSTRAINT fund_nav_guard_state_pkey PRIMARY KEY (fund_id);


--
-- TOC entry 5066 (class 2606 OID 32940)
-- Name: fund_nav_minute fund_nav_minute_fund_ts_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute
    ADD CONSTRAINT fund_nav_minute_fund_ts_unique UNIQUE (fund_id, ts_utc);


--
-- TOC entry 5068 (class 2606 OID 32942)
-- Name: fund_nav_minute fund_nav_minute_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute
    ADD CONSTRAINT fund_nav_minute_pkey PRIMARY KEY (id);


--
-- TOC entry 5243 (class 2606 OID 33655)
-- Name: fund_negative_bybit_flows fund_negative_bybit_flows_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_bybit_flows
    ADD CONSTRAINT fund_negative_bybit_flows_pkey PRIMARY KEY (id);


--
-- TOC entry 5247 (class 2606 OID 33659)
-- Name: fund_negative_bybit_flows fund_negative_bybit_flows_sale_batch_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_bybit_flows
    ADD CONSTRAINT fund_negative_bybit_flows_sale_batch_uq UNIQUE (sale_batch_id);


--
-- TOC entry 5250 (class 2606 OID 33657)
-- Name: fund_negative_bybit_flows fund_negative_bybit_flows_settlement_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_bybit_flows
    ADD CONSTRAINT fund_negative_bybit_flows_settlement_uq UNIQUE (settlement_batch_id);


--
-- TOC entry 5274 (class 2606 OID 33816)
-- Name: fund_negative_finalization_batches fund_negative_finalization_batches_payout_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_finalization_batches
    ADD CONSTRAINT fund_negative_finalization_batches_payout_uq UNIQUE (payout_batch_id);


--
-- TOC entry 5276 (class 2606 OID 33812)
-- Name: fund_negative_finalization_batches fund_negative_finalization_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_finalization_batches
    ADD CONSTRAINT fund_negative_finalization_batches_pkey PRIMARY KEY (id);


--
-- TOC entry 5279 (class 2606 OID 33814)
-- Name: fund_negative_finalization_batches fund_negative_finalization_batches_settlement_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_finalization_batches
    ADD CONSTRAINT fund_negative_finalization_batches_settlement_uq UNIQUE (settlement_batch_id);


--
-- TOC entry 5256 (class 2606 OID 33706)
-- Name: fund_negative_payout_batches fund_negative_payout_batches_bybit_flow_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_payout_batches
    ADD CONSTRAINT fund_negative_payout_batches_bybit_flow_uq UNIQUE (bybit_flow_id);


--
-- TOC entry 5259 (class 2606 OID 33702)
-- Name: fund_negative_payout_batches fund_negative_payout_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_payout_batches
    ADD CONSTRAINT fund_negative_payout_batches_pkey PRIMARY KEY (id);


--
-- TOC entry 5262 (class 2606 OID 33704)
-- Name: fund_negative_payout_batches fund_negative_payout_batches_settlement_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_payout_batches
    ADD CONSTRAINT fund_negative_payout_batches_settlement_uq UNIQUE (settlement_batch_id);


--
-- TOC entry 5267 (class 2606 OID 33749)
-- Name: fund_negative_payout_legs fund_negative_payout_legs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_payout_legs
    ADD CONSTRAINT fund_negative_payout_legs_pkey PRIMARY KEY (id);


--
-- TOC entry 5223 (class 2606 OID 33561)
-- Name: fund_negative_sale_batches fund_negative_sale_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_batches
    ADD CONSTRAINT fund_negative_sale_batches_pkey PRIMARY KEY (id);


--
-- TOC entry 5226 (class 2606 OID 33563)
-- Name: fund_negative_sale_batches fund_negative_sale_batches_settlement_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_batches
    ADD CONSTRAINT fund_negative_sale_batches_settlement_uq UNIQUE (settlement_batch_id);


--
-- TOC entry 5229 (class 2606 OID 33589)
-- Name: fund_negative_sale_legs fund_negative_sale_legs_batch_index_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_legs
    ADD CONSTRAINT fund_negative_sale_legs_batch_index_uq UNIQUE (sale_batch_id, leg_index);


--
-- TOC entry 5238 (class 2606 OID 33587)
-- Name: fund_negative_sale_legs fund_negative_sale_legs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_legs
    ADD CONSTRAINT fund_negative_sale_legs_pkey PRIMARY KEY (id);


--
-- TOC entry 5296 (class 2606 OID 33923)
-- Name: fund_operation_guard_events fund_operation_guard_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operation_guard_events
    ADD CONSTRAINT fund_operation_guard_events_pkey PRIMARY KEY (id);


--
-- TOC entry 5289 (class 2606 OID 33895)
-- Name: fund_operation_guard_overrides fund_operation_guard_overrides_idempotency_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operation_guard_overrides
    ADD CONSTRAINT fund_operation_guard_overrides_idempotency_uq UNIQUE (idempotency_key);


--
-- TOC entry 5291 (class 2606 OID 33893)
-- Name: fund_operation_guard_overrides fund_operation_guard_overrides_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operation_guard_overrides
    ADD CONSTRAINT fund_operation_guard_overrides_pkey PRIMARY KEY (id);


--
-- TOC entry 5283 (class 2606 OID 33864)
-- Name: fund_operation_guard_state fund_operation_guard_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operation_guard_state
    ADD CONSTRAINT fund_operation_guard_state_pkey PRIMARY KEY (id);


--
-- TOC entry 5285 (class 2606 OID 33866)
-- Name: fund_operation_guard_state fund_operation_guard_state_scope_action_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operation_guard_state
    ADD CONSTRAINT fund_operation_guard_state_scope_action_uq UNIQUE (scope_key, action_type);


--
-- TOC entry 5217 (class 2606 OID 33521)
-- Name: fund_operator_actions fund_operator_actions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operator_actions
    ADD CONSTRAINT fund_operator_actions_pkey PRIMARY KEY (id);


--
-- TOC entry 5125 (class 2606 OID 33086)
-- Name: fund_orders fund_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders
    ADD CONSTRAINT fund_orders_pkey PRIMARY KEY (id);


--
-- TOC entry 5176 (class 2606 OID 33351)
-- Name: fund_runtime_state fund_runtime_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_runtime_state
    ADD CONSTRAINT fund_runtime_state_pkey PRIMARY KEY (fund_id);


--
-- TOC entry 5165 (class 2606 OID 33285)
-- Name: fund_settlement_batches fund_settlement_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_batches
    ADD CONSTRAINT fund_settlement_batches_pkey PRIMARY KEY (id);


--
-- TOC entry 5172 (class 2606 OID 33316)
-- Name: fund_settlement_transfers fund_settlement_transfers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_pkey PRIMARY KEY (id);


--
-- TOC entry 5159 (class 2606 OID 33256)
-- Name: fund_wallets fund_wallets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_wallets
    ADD CONSTRAINT fund_wallets_pkey PRIMARY KEY (id);


--
-- TOC entry 5070 (class 2606 OID 32944)
-- Name: funds funds_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funds
    ADD CONSTRAINT funds_code_key UNIQUE (code);


--
-- TOC entry 5072 (class 2606 OID 32946)
-- Name: funds funds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funds
    ADD CONSTRAINT funds_pkey PRIMARY KEY (id);


--
-- TOC entry 5074 (class 2606 OID 32948)
-- Name: password_reset_sessions password_reset_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_sessions
    ADD CONSTRAINT password_reset_sessions_pkey PRIMARY KEY (id);


--
-- TOC entry 5076 (class 2606 OID 32950)
-- Name: security_codes security_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_codes
    ADD CONSTRAINT security_codes_pkey PRIMARY KEY (id);


--
-- TOC entry 5079 (class 2606 OID 32952)
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);


--
-- TOC entry 5129 (class 2606 OID 33107)
-- Name: user_fund_position_stats user_fund_position_stats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats
    ADD CONSTRAINT user_fund_position_stats_pkey PRIMARY KEY (id);


--
-- TOC entry 5131 (class 2606 OID 33109)
-- Name: user_fund_position_stats user_fund_position_stats_user_fund_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats
    ADD CONSTRAINT user_fund_position_stats_user_fund_uq UNIQUE (user_id, fund_id);


--
-- TOC entry 5081 (class 2606 OID 32954)
-- Name: user_fund_positions user_fund_positions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_pkey PRIMARY KEY (id);


--
-- TOC entry 5083 (class 2606 OID 32956)
-- Name: user_fund_positions user_fund_positions_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_unique UNIQUE (user_id, fund_id);


--
-- TOC entry 5086 (class 2606 OID 32958)
-- Name: user_portfolio_daily user_portfolio_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily
    ADD CONSTRAINT user_portfolio_daily_pkey PRIMARY KEY (id);


--
-- TOC entry 5088 (class 2606 OID 32960)
-- Name: user_portfolio_daily user_portfolio_daily_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily
    ADD CONSTRAINT user_portfolio_daily_unique UNIQUE (user_id, date_utc);


--
-- TOC entry 5143 (class 2606 OID 33181)
-- Name: user_totp_recovery_codes user_totp_recovery_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_totp_recovery_codes
    ADD CONSTRAINT user_totp_recovery_codes_pkey PRIMARY KEY (id);


--
-- TOC entry 5093 (class 2606 OID 32962)
-- Name: user_wallets user_wallets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wallets
    ADD CONSTRAINT user_wallets_pkey PRIMARY KEY (id);


--
-- TOC entry 5098 (class 2606 OID 32964)
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- TOC entry 5100 (class 2606 OID 32966)
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- TOC entry 5106 (class 2606 OID 32968)
-- Name: wallet_transfers wallet_transfers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_pkey PRIMARY KEY (id);


--
-- TOC entry 5109 (class 2606 OID 32970)
-- Name: wallet_transfers wallet_transfers_tx_hash_log_index_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_tx_hash_log_index_uq UNIQUE (tx_hash, log_index);


--
-- TOC entry 5116 (class 2606 OID 33054)
-- Name: withdraw_sessions withdraw_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_pkey PRIMARY KEY (id);


--
-- TOC entry 5118 (class 2606 OID 33056)
-- Name: withdraw_sessions withdraw_sessions_token_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_token_key UNIQUE (token);


--
-- TOC entry 5113 (class 2606 OID 33044)
-- Name: worker_cursors worker_cursors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.worker_cursors
    ADD CONSTRAINT worker_cursors_pkey PRIMARY KEY (name);


--
-- TOC entry 5145 (class 1259 OID 33206)
-- Name: fee_wallet_swaps_one_success_per_day_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fee_wallet_swaps_one_success_per_day_idx ON public.fee_wallet_swaps USING btree (wallet_type, (((created_at AT TIME ZONE 'UTC'::text))::date)) WHERE ((status)::text = 'success'::text);


--
-- TOC entry 5148 (class 1259 OID 33205)
-- Name: fee_wallet_swaps_wallet_type_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fee_wallet_swaps_wallet_type_created_idx ON public.fee_wallet_swaps USING btree (wallet_type, created_at DESC);


--
-- TOC entry 5184 (class 1259 OID 33448)
-- Name: fund_allocation_batches_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_batches_created_idx ON public.fund_allocation_batches USING btree (created_at DESC);


--
-- TOC entry 5185 (class 1259 OID 33505)
-- Name: fund_allocation_batches_fund_status_completed_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_batches_fund_status_completed_idx ON public.fund_allocation_batches USING btree (fund_id, status, completed_at DESC);


--
-- TOC entry 5186 (class 1259 OID 33447)
-- Name: fund_allocation_batches_fund_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_batches_fund_status_idx ON public.fund_allocation_batches USING btree (fund_id, status);


--
-- TOC entry 5189 (class 1259 OID 33506)
-- Name: fund_allocation_batches_residual_cash_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_batches_residual_cash_idx ON public.fund_allocation_batches USING btree (residual_cash_usdt) WHERE (residual_cash_usdt IS NOT NULL);


--
-- TOC entry 5190 (class 1259 OID 33446)
-- Name: fund_allocation_batches_settlement_batch_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_allocation_batches_settlement_batch_uq ON public.fund_allocation_batches USING btree (settlement_batch_id);


--
-- TOC entry 5191 (class 1259 OID 33504)
-- Name: fund_allocation_batches_status_completed_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_batches_status_completed_idx ON public.fund_allocation_batches USING btree (status, completed_at DESC);


--
-- TOC entry 5192 (class 1259 OID 33483)
-- Name: fund_allocation_legs_batch_leg_index_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_allocation_legs_batch_leg_index_uq ON public.fund_allocation_legs USING btree (allocation_batch_id, leg_index);


--
-- TOC entry 5193 (class 1259 OID 33484)
-- Name: fund_allocation_legs_batch_leg_key_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_allocation_legs_batch_leg_key_uq ON public.fund_allocation_legs USING btree (allocation_batch_id, leg_key);


--
-- TOC entry 5194 (class 1259 OID 33485)
-- Name: fund_allocation_legs_batch_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_batch_status_idx ON public.fund_allocation_legs USING btree (allocation_batch_id, status);


--
-- TOC entry 5195 (class 1259 OID 33972)
-- Name: fund_allocation_legs_bybit_order_id_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_allocation_legs_bybit_order_id_uq ON public.fund_allocation_legs USING btree (bybit_order_id) WHERE (bybit_order_id IS NOT NULL);


--
-- TOC entry 5196 (class 1259 OID 33494)
-- Name: fund_allocation_legs_bybit_order_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_bybit_order_idx ON public.fund_allocation_legs USING btree (bybit_order_id) WHERE (bybit_order_id IS NOT NULL);


--
-- TOC entry 5197 (class 1259 OID 33500)
-- Name: fund_allocation_legs_category_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_category_status_idx ON public.fund_allocation_legs USING btree (category, status);


--
-- TOC entry 5198 (class 1259 OID 33974)
-- Name: fund_allocation_legs_earn_order_id_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_allocation_legs_earn_order_id_uq ON public.fund_allocation_legs USING btree (earn_order_id) WHERE (earn_order_id IS NOT NULL);


--
-- TOC entry 5199 (class 1259 OID 33495)
-- Name: fund_allocation_legs_earn_order_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_earn_order_idx ON public.fund_allocation_legs USING btree (earn_order_id) WHERE (earn_order_id IS NOT NULL);


--
-- TOC entry 5200 (class 1259 OID 33493)
-- Name: fund_allocation_legs_execution_mode_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_execution_mode_status_idx ON public.fund_allocation_legs USING btree (execution_mode, status);


--
-- TOC entry 5201 (class 1259 OID 33486)
-- Name: fund_allocation_legs_fund_group_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_fund_group_idx ON public.fund_allocation_legs USING btree (fund_id, leg_group);


--
-- TOC entry 5202 (class 1259 OID 33501)
-- Name: fund_allocation_legs_group_type_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_group_type_status_idx ON public.fund_allocation_legs USING btree (leg_group, leg_type, status);


--
-- TOC entry 5203 (class 1259 OID 33499)
-- Name: fund_allocation_legs_margin_guard_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_margin_guard_idx ON public.fund_allocation_legs USING btree (margin_guard_status) WHERE (margin_guard_status IS NOT NULL);


--
-- TOC entry 5204 (class 1259 OID 33971)
-- Name: fund_allocation_legs_order_link_id_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_allocation_legs_order_link_id_uq ON public.fund_allocation_legs USING btree (order_link_id) WHERE (order_link_id IS NOT NULL);


--
-- TOC entry 5205 (class 1259 OID 33488)
-- Name: fund_allocation_legs_order_link_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_order_link_idx ON public.fund_allocation_legs USING btree (order_link_id) WHERE (order_link_id IS NOT NULL);


--
-- TOC entry 5206 (class 1259 OID 33496)
-- Name: fund_allocation_legs_parent_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_parent_idx ON public.fund_allocation_legs USING btree (parent_leg_id) WHERE (parent_leg_id IS NOT NULL);


--
-- TOC entry 5209 (class 1259 OID 33497)
-- Name: fund_allocation_legs_residual_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_residual_idx ON public.fund_allocation_legs USING btree (allocation_batch_id, residual_usdt) WHERE (residual_usdt IS NOT NULL);


--
-- TOC entry 5210 (class 1259 OID 33973)
-- Name: fund_allocation_legs_strategy_id_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_allocation_legs_strategy_id_uq ON public.fund_allocation_legs USING btree (strategy_id) WHERE (strategy_id IS NOT NULL);


--
-- TOC entry 5211 (class 1259 OID 33487)
-- Name: fund_allocation_legs_strategy_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_strategy_idx ON public.fund_allocation_legs USING btree (strategy_id) WHERE (strategy_id IS NOT NULL);


--
-- TOC entry 5177 (class 1259 OID 33380)
-- Name: fund_bybit_accounts_active_fund_coin_chain_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_bybit_accounts_active_fund_coin_chain_uq ON public.fund_bybit_accounts USING btree (fund_id, coin, chain_type) WHERE (is_active = true);


--
-- TOC entry 5178 (class 1259 OID 33421)
-- Name: fund_bybit_accounts_api_key_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_bybit_accounts_api_key_active_idx ON public.fund_bybit_accounts USING btree (fund_id, api_key_is_active);


--
-- TOC entry 5179 (class 1259 OID 33383)
-- Name: fund_bybit_accounts_deposit_address_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_bybit_accounts_deposit_address_idx ON public.fund_bybit_accounts USING btree (deposit_address);


--
-- TOC entry 5180 (class 1259 OID 33382)
-- Name: fund_bybit_accounts_fund_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_bybit_accounts_fund_id_idx ON public.fund_bybit_accounts USING btree (fund_id);


--
-- TOC entry 5183 (class 1259 OID 33381)
-- Name: fund_bybit_accounts_sub_uid_coin_chain_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_bybit_accounts_sub_uid_coin_chain_uq ON public.fund_bybit_accounts USING btree (bybit_sub_uid, coin, chain_type) WHERE (is_active = true);


--
-- TOC entry 5132 (class 1259 OID 33134)
-- Name: fund_chart_daily_fund_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_chart_daily_fund_ts_idx ON public.fund_chart_daily USING btree (fund_id, ts_utc DESC);


--
-- TOC entry 5137 (class 1259 OID 33149)
-- Name: fund_chart_minute_fund_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_chart_minute_fund_ts_idx ON public.fund_chart_minute USING btree (fund_id, ts_utc DESC);


--
-- TOC entry 5151 (class 1259 OID 33237)
-- Name: fund_nav_guard_events_decision_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_nav_guard_events_decision_created_idx ON public.fund_nav_guard_events USING btree (decision, created_at DESC);


--
-- TOC entry 5152 (class 1259 OID 33236)
-- Name: fund_nav_guard_events_fund_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_nav_guard_events_fund_created_idx ON public.fund_nav_guard_events USING btree (fund_id, created_at DESC);


--
-- TOC entry 5064 (class 1259 OID 32971)
-- Name: fund_nav_minute_fund_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_nav_minute_fund_ts_idx ON public.fund_nav_minute USING btree (fund_id, ts_utc DESC);


--
-- TOC entry 5241 (class 1259 OID 33682)
-- Name: fund_negative_bybit_flows_fund_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_bybit_flows_fund_status_idx ON public.fund_negative_bybit_flows USING btree (fund_id, status, created_at DESC);


--
-- TOC entry 5244 (class 1259 OID 33686)
-- Name: fund_negative_bybit_flows_request_id_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_negative_bybit_flows_request_id_uq ON public.fund_negative_bybit_flows USING btree (withdrawal_request_id) WHERE (withdrawal_request_id IS NOT NULL);


--
-- TOC entry 5245 (class 1259 OID 33684)
-- Name: fund_negative_bybit_flows_sale_batch_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_bybit_flows_sale_batch_idx ON public.fund_negative_bybit_flows USING btree (sale_batch_id);


--
-- TOC entry 5248 (class 1259 OID 33683)
-- Name: fund_negative_bybit_flows_settlement_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_bybit_flows_settlement_idx ON public.fund_negative_bybit_flows USING btree (settlement_batch_id);


--
-- TOC entry 5251 (class 1259 OID 33685)
-- Name: fund_negative_bybit_flows_transfer_id_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_negative_bybit_flows_transfer_id_uq ON public.fund_negative_bybit_flows USING btree (universal_transfer_id) WHERE (universal_transfer_id IS NOT NULL);


--
-- TOC entry 5252 (class 1259 OID 33688)
-- Name: fund_negative_bybit_flows_tx_hash_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_bybit_flows_tx_hash_idx ON public.fund_negative_bybit_flows USING btree (withdrawal_tx_hash) WHERE (withdrawal_tx_hash IS NOT NULL);


--
-- TOC entry 5253 (class 1259 OID 33687)
-- Name: fund_negative_bybit_flows_withdrawal_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_bybit_flows_withdrawal_id_idx ON public.fund_negative_bybit_flows USING btree (withdrawal_id) WHERE (withdrawal_id IS NOT NULL);


--
-- TOC entry 5270 (class 1259 OID 33847)
-- Name: fund_negative_finalization_batches_completed_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_finalization_batches_completed_idx ON public.fund_negative_finalization_batches USING btree (fund_id, completed_at DESC) WHERE (completed_at IS NOT NULL);


--
-- TOC entry 5271 (class 1259 OID 33844)
-- Name: fund_negative_finalization_batches_fund_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_finalization_batches_fund_status_idx ON public.fund_negative_finalization_batches USING btree (fund_id, status, created_at DESC);


--
-- TOC entry 5272 (class 1259 OID 33846)
-- Name: fund_negative_finalization_batches_payout_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_finalization_batches_payout_idx ON public.fund_negative_finalization_batches USING btree (payout_batch_id);


--
-- TOC entry 5277 (class 1259 OID 33845)
-- Name: fund_negative_finalization_batches_settlement_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_finalization_batches_settlement_idx ON public.fund_negative_finalization_batches USING btree (settlement_batch_id);


--
-- TOC entry 5254 (class 1259 OID 33795)
-- Name: fund_negative_payout_batches_bybit_flow_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_payout_batches_bybit_flow_idx ON public.fund_negative_payout_batches USING btree (bybit_flow_id);


--
-- TOC entry 5257 (class 1259 OID 33793)
-- Name: fund_negative_payout_batches_fund_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_payout_batches_fund_status_idx ON public.fund_negative_payout_batches USING btree (fund_id, status, created_at DESC);


--
-- TOC entry 5260 (class 1259 OID 33794)
-- Name: fund_negative_payout_batches_settlement_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_payout_batches_settlement_idx ON public.fund_negative_payout_batches USING btree (settlement_batch_id);


--
-- TOC entry 5263 (class 1259 OID 33796)
-- Name: fund_negative_payout_legs_batch_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_payout_legs_batch_status_idx ON public.fund_negative_payout_legs USING btree (payout_batch_id, status);


--
-- TOC entry 5264 (class 1259 OID 33800)
-- Name: fund_negative_payout_legs_batch_wallet_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_negative_payout_legs_batch_wallet_uq ON public.fund_negative_payout_legs USING btree (payout_batch_id, to_user_wallet_id) WHERE (to_user_wallet_id IS NOT NULL);


--
-- TOC entry 5265 (class 1259 OID 33798)
-- Name: fund_negative_payout_legs_deterministic_key_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_negative_payout_legs_deterministic_key_uq ON public.fund_negative_payout_legs USING btree (deterministic_key) WHERE (deterministic_key IS NOT NULL);


--
-- TOC entry 5268 (class 1259 OID 33799)
-- Name: fund_negative_payout_legs_tx_hash_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_negative_payout_legs_tx_hash_uq ON public.fund_negative_payout_legs USING btree (tx_hash) WHERE (tx_hash IS NOT NULL);


--
-- TOC entry 5269 (class 1259 OID 33797)
-- Name: fund_negative_payout_legs_user_wallet_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_payout_legs_user_wallet_idx ON public.fund_negative_payout_legs USING btree (user_wallet_id, status);


--
-- TOC entry 5219 (class 1259 OID 33631)
-- Name: fund_negative_sale_batches_completed_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_batches_completed_idx ON public.fund_negative_sale_batches USING btree (fund_id, execution_completed_at DESC) WHERE (execution_completed_at IS NOT NULL);


--
-- TOC entry 5220 (class 1259 OID 33630)
-- Name: fund_negative_sale_batches_execution_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_batches_execution_status_idx ON public.fund_negative_sale_batches USING btree (fund_id, status, execution_started_at DESC);


--
-- TOC entry 5221 (class 1259 OID 33608)
-- Name: fund_negative_sale_batches_fund_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_batches_fund_status_idx ON public.fund_negative_sale_batches USING btree (fund_id, status, created_at DESC);


--
-- TOC entry 5224 (class 1259 OID 33609)
-- Name: fund_negative_sale_batches_settlement_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_batches_settlement_idx ON public.fund_negative_sale_batches USING btree (settlement_batch_id);


--
-- TOC entry 5227 (class 1259 OID 33607)
-- Name: fund_negative_sale_batches_settlement_uq_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_negative_sale_batches_settlement_uq_idx ON public.fund_negative_sale_batches USING btree (settlement_batch_id);


--
-- TOC entry 5230 (class 1259 OID 33610)
-- Name: fund_negative_sale_legs_batch_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_legs_batch_status_idx ON public.fund_negative_sale_legs USING btree (sale_batch_id, status);


--
-- TOC entry 5231 (class 1259 OID 33634)
-- Name: fund_negative_sale_legs_bybit_order_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_legs_bybit_order_idx ON public.fund_negative_sale_legs USING btree (bybit_order_id) WHERE (bybit_order_id IS NOT NULL);


--
-- TOC entry 5232 (class 1259 OID 33635)
-- Name: fund_negative_sale_legs_bybit_strategy_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_legs_bybit_strategy_idx ON public.fund_negative_sale_legs USING btree (bybit_strategy_id) WHERE (bybit_strategy_id IS NOT NULL);


--
-- TOC entry 5233 (class 1259 OID 33633)
-- Name: fund_negative_sale_legs_deterministic_key_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_legs_deterministic_key_idx ON public.fund_negative_sale_legs USING btree (deterministic_key) WHERE (deterministic_key IS NOT NULL);


--
-- TOC entry 5234 (class 1259 OID 33632)
-- Name: fund_negative_sale_legs_execution_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_legs_execution_status_idx ON public.fund_negative_sale_legs USING btree (sale_batch_id, status, actual_execution_mode);


--
-- TOC entry 5235 (class 1259 OID 33611)
-- Name: fund_negative_sale_legs_group_type_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_legs_group_type_status_idx ON public.fund_negative_sale_legs USING btree (leg_group, leg_type, status);


--
-- TOC entry 5236 (class 1259 OID 33613)
-- Name: fund_negative_sale_legs_order_link_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_legs_order_link_idx ON public.fund_negative_sale_legs USING btree (order_link_id) WHERE (order_link_id IS NOT NULL);


--
-- TOC entry 5239 (class 1259 OID 33614)
-- Name: fund_negative_sale_legs_strategy_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_legs_strategy_idx ON public.fund_negative_sale_legs USING btree (strategy_id) WHERE (strategy_id IS NOT NULL);


--
-- TOC entry 5240 (class 1259 OID 33612)
-- Name: fund_negative_sale_legs_symbol_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_legs_symbol_status_idx ON public.fund_negative_sale_legs USING btree (symbol, status) WHERE (symbol IS NOT NULL);


--
-- TOC entry 5293 (class 1259 OID 33953)
-- Name: fund_operation_guard_events_action_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_operation_guard_events_action_created_idx ON public.fund_operation_guard_events USING btree (action_type, created_at DESC);


--
-- TOC entry 5294 (class 1259 OID 33954)
-- Name: fund_operation_guard_events_fund_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_operation_guard_events_fund_created_idx ON public.fund_operation_guard_events USING btree (fund_id, created_at DESC) WHERE (fund_id IS NOT NULL);


--
-- TOC entry 5297 (class 1259 OID 33955)
-- Name: fund_operation_guard_events_settlement_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_operation_guard_events_settlement_idx ON public.fund_operation_guard_events USING btree (settlement_batch_id, created_at DESC) WHERE (settlement_batch_id IS NOT NULL);


--
-- TOC entry 5286 (class 1259 OID 33950)
-- Name: fund_operation_guard_overrides_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_operation_guard_overrides_active_idx ON public.fund_operation_guard_overrides USING btree (action_type, scope_key, status, expires_at);


--
-- TOC entry 5287 (class 1259 OID 33951)
-- Name: fund_operation_guard_overrides_fund_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_operation_guard_overrides_fund_idx ON public.fund_operation_guard_overrides USING btree (fund_id, action_type, status, expires_at) WHERE (fund_id IS NOT NULL);


--
-- TOC entry 5292 (class 1259 OID 33952)
-- Name: fund_operation_guard_overrides_request_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_operation_guard_overrides_request_idx ON public.fund_operation_guard_overrides USING btree (request_id) WHERE (request_id IS NOT NULL);


--
-- TOC entry 5280 (class 1259 OID 33948)
-- Name: fund_operation_guard_state_action_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_operation_guard_state_action_idx ON public.fund_operation_guard_state USING btree (action_type, scope_key);


--
-- TOC entry 5281 (class 1259 OID 33949)
-- Name: fund_operation_guard_state_fund_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_operation_guard_state_fund_idx ON public.fund_operation_guard_state USING btree (fund_id, action_type) WHERE (fund_id IS NOT NULL);


--
-- TOC entry 5212 (class 1259 OID 33544)
-- Name: fund_operator_actions_callback_token_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_operator_actions_callback_token_idx ON public.fund_operator_actions USING btree (callback_token_hash) WHERE (callback_token_hash IS NOT NULL);


--
-- TOC entry 5213 (class 1259 OID 33542)
-- Name: fund_operator_actions_fund_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_operator_actions_fund_status_idx ON public.fund_operator_actions USING btree (fund_id, status, requested_at DESC);


--
-- TOC entry 5214 (class 1259 OID 33540)
-- Name: fund_operator_actions_idempotency_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_operator_actions_idempotency_uq ON public.fund_operator_actions USING btree (idempotency_key);


--
-- TOC entry 5215 (class 1259 OID 33541)
-- Name: fund_operator_actions_pending_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_operator_actions_pending_idx ON public.fund_operator_actions USING btree (action_type, status, requested_at);


--
-- TOC entry 5218 (class 1259 OID 33543)
-- Name: fund_operator_actions_settlement_batch_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_operator_actions_settlement_batch_idx ON public.fund_operator_actions USING btree (settlement_batch_id);


--
-- TOC entry 5119 (class 1259 OID 33300)
-- Name: fund_orders_batch_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_batch_id_idx ON public.fund_orders USING btree (settlement_batch_id);


--
-- TOC entry 5120 (class 1259 OID 33392)
-- Name: fund_orders_batch_side_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_batch_side_status_idx ON public.fund_orders USING btree (settlement_batch_id, side, status);


--
-- TOC entry 5121 (class 1259 OID 33548)
-- Name: fund_orders_fee_audit_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_fee_audit_idx ON public.fund_orders USING btree (settlement_batch_id, partial_month_fee_usdt) WHERE (partial_month_fee_usdt IS NOT NULL);


--
-- TOC entry 5122 (class 1259 OID 33098)
-- Name: fund_orders_fund_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_fund_created_at_idx ON public.fund_orders USING btree (fund_id, created_at DESC);


--
-- TOC entry 5123 (class 1259 OID 33391)
-- Name: fund_orders_pending_cutoff_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_pending_cutoff_idx ON public.fund_orders USING btree (fund_id, status, created_at);


--
-- TOC entry 5126 (class 1259 OID 33547)
-- Name: fund_orders_settlement_side_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_settlement_side_status_idx ON public.fund_orders USING btree (settlement_batch_id, side, status);


--
-- TOC entry 5127 (class 1259 OID 33097)
-- Name: fund_orders_user_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_user_created_at_idx ON public.fund_orders USING btree (user_id, created_at DESC);


--
-- TOC entry 5160 (class 1259 OID 33417)
-- Name: fund_settlement_batches_bybit_tx_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_batches_bybit_tx_idx ON public.fund_settlement_batches USING btree (bybit_deposit_tx_hash);


--
-- TOC entry 5161 (class 1259 OID 33291)
-- Name: fund_settlement_batches_fund_date_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_settlement_batches_fund_date_uq ON public.fund_settlement_batches USING btree (fund_id, settlement_date);


--
-- TOC entry 5162 (class 1259 OID 33418)
-- Name: fund_settlement_batches_internal_transfer_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_batches_internal_transfer_idx ON public.fund_settlement_batches USING btree (bybit_internal_transfer_id);


--
-- TOC entry 5163 (class 1259 OID 33549)
-- Name: fund_settlement_batches_negative_targets_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_batches_negative_targets_idx ON public.fund_settlement_batches USING btree (fund_id, status, negative_net_target_calculated_at DESC) WHERE (negative_net_target_calculated_at IS NOT NULL);


--
-- TOC entry 5166 (class 1259 OID 33395)
-- Name: fund_settlement_batches_positive_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_batches_positive_status_idx ON public.fund_settlement_batches USING btree (status, settlement_date);


--
-- TOC entry 5167 (class 1259 OID 33339)
-- Name: fund_settlement_transfers_batch_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_transfers_batch_idx ON public.fund_settlement_transfers USING btree (batch_id);


--
-- TOC entry 5168 (class 1259 OID 33413)
-- Name: fund_settlement_transfers_batch_type_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_transfers_batch_type_status_idx ON public.fund_settlement_transfers USING btree (batch_id, transfer_type, status);


--
-- TOC entry 5169 (class 1259 OID 33340)
-- Name: fund_settlement_transfers_order_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_transfers_order_idx ON public.fund_settlement_transfers USING btree (order_id);


--
-- TOC entry 5170 (class 1259 OID 33412)
-- Name: fund_settlement_transfers_order_type_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_settlement_transfers_order_type_uq ON public.fund_settlement_transfers USING btree (batch_id, order_id, transfer_type) WHERE (order_id IS NOT NULL);


--
-- TOC entry 5173 (class 1259 OID 33409)
-- Name: fund_settlement_transfers_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_transfers_status_idx ON public.fund_settlement_transfers USING btree (status);


--
-- TOC entry 5174 (class 1259 OID 33342)
-- Name: fund_settlement_transfers_tx_hash_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_settlement_transfers_tx_hash_uq ON public.fund_settlement_transfers USING btree (tx_hash) WHERE (tx_hash IS NOT NULL);


--
-- TOC entry 5155 (class 1259 OID 33263)
-- Name: fund_wallets_active_settlement_fund_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_wallets_active_settlement_fund_uq ON public.fund_wallets USING btree (fund_id, blockchain, wallet_type) WHERE (is_active = true);


--
-- TOC entry 5156 (class 1259 OID 33262)
-- Name: fund_wallets_blockchain_address_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_wallets_blockchain_address_uq ON public.fund_wallets USING btree (blockchain, address);


--
-- TOC entry 5157 (class 1259 OID 33264)
-- Name: fund_wallets_fund_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_wallets_fund_id_idx ON public.fund_wallets USING btree (fund_id);


--
-- TOC entry 5077 (class 1259 OID 32972)
-- Name: idx_sessions_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_expires_at ON public.sessions USING btree (expires_at);


--
-- TOC entry 5095 (class 1259 OID 32973)
-- Name: idx_users_compliance_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_compliance_status ON public.users USING btree (compliance_status);


--
-- TOC entry 5101 (class 1259 OID 32974)
-- Name: idx_wallet_transfers_compliance_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_compliance_status ON public.wallet_transfers USING btree (compliance_status);


--
-- TOC entry 5102 (class 1259 OID 32975)
-- Name: idx_wallet_transfers_need_compliance; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_need_compliance ON public.wallet_transfers USING btree (status, compliance_status);


--
-- TOC entry 5103 (class 1259 OID 33073)
-- Name: idx_wallet_transfers_user_type_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_user_type_time ON public.wallet_transfers USING btree (user_id, type, detected_at DESC);


--
-- TOC entry 5104 (class 1259 OID 33072)
-- Name: idx_wallet_transfers_withdraw_processing; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_withdraw_processing ON public.wallet_transfers USING btree (type, status) WHERE (((type)::text = 'withdraw'::text) AND ((status)::text = 'processing'::text));


--
-- TOC entry 5114 (class 1259 OID 33067)
-- Name: idx_withdraw_sessions_user_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_withdraw_sessions_user_expires ON public.withdraw_sessions USING btree (user_id, expires_at DESC);


--
-- TOC entry 5084 (class 1259 OID 32976)
-- Name: user_fund_positions_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_fund_positions_user_idx ON public.user_fund_positions USING btree (user_id);


--
-- TOC entry 5089 (class 1259 OID 32977)
-- Name: user_portfolio_daily_user_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_portfolio_daily_user_date_idx ON public.user_portfolio_daily USING btree (user_id, date_utc DESC);


--
-- TOC entry 5144 (class 1259 OID 33187)
-- Name: user_totp_recovery_codes_user_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_totp_recovery_codes_user_active_idx ON public.user_totp_recovery_codes USING btree (user_id, is_used);


--
-- TOC entry 5090 (class 1259 OID 32978)
-- Name: user_wallets_blockchain_address_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX user_wallets_blockchain_address_uq ON public.user_wallets USING btree (blockchain, address);


--
-- TOC entry 5091 (class 1259 OID 33069)
-- Name: user_wallets_one_active_bsc; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX user_wallets_one_active_bsc ON public.user_wallets USING btree (user_id) WHERE (((blockchain)::text = 'BSC'::text) AND (is_active = true));


--
-- TOC entry 5094 (class 1259 OID 32980)
-- Name: user_wallets_user_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_wallets_user_id_idx ON public.user_wallets USING btree (user_id);


--
-- TOC entry 5096 (class 1259 OID 32981)
-- Name: users_backup_email_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX users_backup_email_idx ON public.users USING btree (backup_email);


--
-- TOC entry 5107 (class 1259 OID 32982)
-- Name: wallet_transfers_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX wallet_transfers_status_idx ON public.wallet_transfers USING btree (status);


--
-- TOC entry 5110 (class 1259 OID 32983)
-- Name: wallet_transfers_user_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX wallet_transfers_user_time_idx ON public.wallet_transfers USING btree (user_id, tx_time DESC);


--
-- TOC entry 5111 (class 1259 OID 32984)
-- Name: wallet_transfers_wallet_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX wallet_transfers_wallet_time_idx ON public.wallet_transfers USING btree (wallet_id, tx_time DESC);


--
-- TOC entry 5329 (class 2606 OID 33441)
-- Name: fund_allocation_batches fund_allocation_batches_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_batches
    ADD CONSTRAINT fund_allocation_batches_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5330 (class 2606 OID 33436)
-- Name: fund_allocation_batches fund_allocation_batches_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_batches
    ADD CONSTRAINT fund_allocation_batches_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5331 (class 2606 OID 33463)
-- Name: fund_allocation_legs fund_allocation_legs_allocation_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_legs
    ADD CONSTRAINT fund_allocation_legs_allocation_batch_id_fkey FOREIGN KEY (allocation_batch_id) REFERENCES public.fund_allocation_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5332 (class 2606 OID 33473)
-- Name: fund_allocation_legs fund_allocation_legs_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_legs
    ADD CONSTRAINT fund_allocation_legs_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5333 (class 2606 OID 33478)
-- Name: fund_allocation_legs fund_allocation_legs_parent_leg_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_legs
    ADD CONSTRAINT fund_allocation_legs_parent_leg_id_fkey FOREIGN KEY (parent_leg_id) REFERENCES public.fund_allocation_legs(id) ON DELETE SET NULL;


--
-- TOC entry 5334 (class 2606 OID 33468)
-- Name: fund_allocation_legs fund_allocation_legs_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_legs
    ADD CONSTRAINT fund_allocation_legs_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5328 (class 2606 OID 33375)
-- Name: fund_bybit_accounts fund_bybit_accounts_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_bybit_accounts
    ADD CONSTRAINT fund_bybit_accounts_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5315 (class 2606 OID 33127)
-- Name: fund_chart_daily fund_chart_daily_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_daily
    ADD CONSTRAINT fund_chart_daily_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5316 (class 2606 OID 33142)
-- Name: fund_chart_minute fund_chart_minute_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_minute
    ADD CONSTRAINT fund_chart_minute_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5319 (class 2606 OID 33231)
-- Name: fund_nav_guard_events fund_nav_guard_events_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_events
    ADD CONSTRAINT fund_nav_guard_events_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5318 (class 2606 OID 33215)
-- Name: fund_nav_guard_state fund_nav_guard_state_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_state
    ADD CONSTRAINT fund_nav_guard_state_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5298 (class 2606 OID 32985)
-- Name: fund_nav_minute fund_nav_minute_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute
    ADD CONSTRAINT fund_nav_minute_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5343 (class 2606 OID 33670)
-- Name: fund_negative_bybit_flows fund_negative_bybit_flows_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_bybit_flows
    ADD CONSTRAINT fund_negative_bybit_flows_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5344 (class 2606 OID 33665)
-- Name: fund_negative_bybit_flows fund_negative_bybit_flows_sale_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_bybit_flows
    ADD CONSTRAINT fund_negative_bybit_flows_sale_batch_id_fkey FOREIGN KEY (sale_batch_id) REFERENCES public.fund_negative_sale_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5345 (class 2606 OID 33660)
-- Name: fund_negative_bybit_flows fund_negative_bybit_flows_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_bybit_flows
    ADD CONSTRAINT fund_negative_bybit_flows_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5346 (class 2606 OID 33675)
-- Name: fund_negative_bybit_flows fund_negative_bybit_flows_settlement_wallet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_bybit_flows
    ADD CONSTRAINT fund_negative_bybit_flows_settlement_wallet_id_fkey FOREIGN KEY (settlement_wallet_id) REFERENCES public.fund_wallets(id) ON DELETE SET NULL;


--
-- TOC entry 5360 (class 2606 OID 33827)
-- Name: fund_negative_finalization_batches fund_negative_finalization_batches_bybit_flow_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_finalization_batches
    ADD CONSTRAINT fund_negative_finalization_batches_bybit_flow_id_fkey FOREIGN KEY (bybit_flow_id) REFERENCES public.fund_negative_bybit_flows(id) ON DELETE SET NULL;


--
-- TOC entry 5361 (class 2606 OID 33837)
-- Name: fund_negative_finalization_batches fund_negative_finalization_batches_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_finalization_batches
    ADD CONSTRAINT fund_negative_finalization_batches_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5362 (class 2606 OID 33822)
-- Name: fund_negative_finalization_batches fund_negative_finalization_batches_payout_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_finalization_batches
    ADD CONSTRAINT fund_negative_finalization_batches_payout_batch_id_fkey FOREIGN KEY (payout_batch_id) REFERENCES public.fund_negative_payout_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5363 (class 2606 OID 33832)
-- Name: fund_negative_finalization_batches fund_negative_finalization_batches_sale_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_finalization_batches
    ADD CONSTRAINT fund_negative_finalization_batches_sale_batch_id_fkey FOREIGN KEY (sale_batch_id) REFERENCES public.fund_negative_sale_batches(id) ON DELETE SET NULL;


--
-- TOC entry 5364 (class 2606 OID 33817)
-- Name: fund_negative_finalization_batches fund_negative_finalization_batches_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_finalization_batches
    ADD CONSTRAINT fund_negative_finalization_batches_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5347 (class 2606 OID 33712)
-- Name: fund_negative_payout_batches fund_negative_payout_batches_bybit_flow_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_payout_batches
    ADD CONSTRAINT fund_negative_payout_batches_bybit_flow_id_fkey FOREIGN KEY (bybit_flow_id) REFERENCES public.fund_negative_bybit_flows(id) ON DELETE CASCADE;


--
-- TOC entry 5348 (class 2606 OID 33717)
-- Name: fund_negative_payout_batches fund_negative_payout_batches_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_payout_batches
    ADD CONSTRAINT fund_negative_payout_batches_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5349 (class 2606 OID 33727)
-- Name: fund_negative_payout_batches fund_negative_payout_batches_operator_action_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_payout_batches
    ADD CONSTRAINT fund_negative_payout_batches_operator_action_id_fkey FOREIGN KEY (operator_action_id) REFERENCES public.fund_operator_actions(id) ON DELETE SET NULL;


--
-- TOC entry 5350 (class 2606 OID 33707)
-- Name: fund_negative_payout_batches fund_negative_payout_batches_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_payout_batches
    ADD CONSTRAINT fund_negative_payout_batches_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5351 (class 2606 OID 33722)
-- Name: fund_negative_payout_batches fund_negative_payout_batches_settlement_wallet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_payout_batches
    ADD CONSTRAINT fund_negative_payout_batches_settlement_wallet_id_fkey FOREIGN KEY (settlement_wallet_id) REFERENCES public.fund_wallets(id) ON DELETE SET NULL;


--
-- TOC entry 5352 (class 2606 OID 33760)
-- Name: fund_negative_payout_legs fund_negative_payout_legs_bybit_flow_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_payout_legs
    ADD CONSTRAINT fund_negative_payout_legs_bybit_flow_id_fkey FOREIGN KEY (bybit_flow_id) REFERENCES public.fund_negative_bybit_flows(id) ON DELETE CASCADE;


--
-- TOC entry 5353 (class 2606 OID 33780)
-- Name: fund_negative_payout_legs fund_negative_payout_legs_from_settlement_wallet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_payout_legs
    ADD CONSTRAINT fund_negative_payout_legs_from_settlement_wallet_id_fkey FOREIGN KEY (from_settlement_wallet_id) REFERENCES public.fund_wallets(id) ON DELETE SET NULL;


--
-- TOC entry 5354 (class 2606 OID 33765)
-- Name: fund_negative_payout_legs fund_negative_payout_legs_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_payout_legs
    ADD CONSTRAINT fund_negative_payout_legs_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5355 (class 2606 OID 33750)
-- Name: fund_negative_payout_legs fund_negative_payout_legs_payout_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_payout_legs
    ADD CONSTRAINT fund_negative_payout_legs_payout_batch_id_fkey FOREIGN KEY (payout_batch_id) REFERENCES public.fund_negative_payout_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5356 (class 2606 OID 33755)
-- Name: fund_negative_payout_legs fund_negative_payout_legs_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_payout_legs
    ADD CONSTRAINT fund_negative_payout_legs_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5357 (class 2606 OID 33785)
-- Name: fund_negative_payout_legs fund_negative_payout_legs_to_user_wallet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_payout_legs
    ADD CONSTRAINT fund_negative_payout_legs_to_user_wallet_id_fkey FOREIGN KEY (to_user_wallet_id) REFERENCES public.user_wallets(id) ON DELETE SET NULL;


--
-- TOC entry 5358 (class 2606 OID 33770)
-- Name: fund_negative_payout_legs fund_negative_payout_legs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_payout_legs
    ADD CONSTRAINT fund_negative_payout_legs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5359 (class 2606 OID 33775)
-- Name: fund_negative_payout_legs fund_negative_payout_legs_user_wallet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_payout_legs
    ADD CONSTRAINT fund_negative_payout_legs_user_wallet_id_fkey FOREIGN KEY (user_wallet_id) REFERENCES public.user_wallets(id) ON DELETE SET NULL;


--
-- TOC entry 5338 (class 2606 OID 33569)
-- Name: fund_negative_sale_batches fund_negative_sale_batches_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_batches
    ADD CONSTRAINT fund_negative_sale_batches_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5339 (class 2606 OID 33564)
-- Name: fund_negative_sale_batches fund_negative_sale_batches_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_batches
    ADD CONSTRAINT fund_negative_sale_batches_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5340 (class 2606 OID 33600)
-- Name: fund_negative_sale_legs fund_negative_sale_legs_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_legs
    ADD CONSTRAINT fund_negative_sale_legs_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5341 (class 2606 OID 33590)
-- Name: fund_negative_sale_legs fund_negative_sale_legs_sale_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_legs
    ADD CONSTRAINT fund_negative_sale_legs_sale_batch_id_fkey FOREIGN KEY (sale_batch_id) REFERENCES public.fund_negative_sale_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5342 (class 2606 OID 33595)
-- Name: fund_negative_sale_legs fund_negative_sale_legs_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_legs
    ADD CONSTRAINT fund_negative_sale_legs_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5370 (class 2606 OID 33924)
-- Name: fund_operation_guard_events fund_operation_guard_events_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operation_guard_events
    ADD CONSTRAINT fund_operation_guard_events_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE SET NULL;


--
-- TOC entry 5371 (class 2606 OID 33934)
-- Name: fund_operation_guard_events fund_operation_guard_events_guard_state_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operation_guard_events
    ADD CONSTRAINT fund_operation_guard_events_guard_state_id_fkey FOREIGN KEY (guard_state_id) REFERENCES public.fund_operation_guard_state(id) ON DELETE SET NULL;


--
-- TOC entry 5372 (class 2606 OID 33939)
-- Name: fund_operation_guard_events fund_operation_guard_events_override_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operation_guard_events
    ADD CONSTRAINT fund_operation_guard_events_override_id_fkey FOREIGN KEY (override_id) REFERENCES public.fund_operation_guard_overrides(id) ON DELETE SET NULL;


--
-- TOC entry 5373 (class 2606 OID 33929)
-- Name: fund_operation_guard_events fund_operation_guard_events_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operation_guard_events
    ADD CONSTRAINT fund_operation_guard_events_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE SET NULL;


--
-- TOC entry 5367 (class 2606 OID 33896)
-- Name: fund_operation_guard_overrides fund_operation_guard_overrides_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operation_guard_overrides
    ADD CONSTRAINT fund_operation_guard_overrides_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5368 (class 2606 OID 33901)
-- Name: fund_operation_guard_overrides fund_operation_guard_overrides_manager_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operation_guard_overrides
    ADD CONSTRAINT fund_operation_guard_overrides_manager_user_id_fkey FOREIGN KEY (manager_user_id) REFERENCES public.users(id) ON DELETE RESTRICT;


--
-- TOC entry 5369 (class 2606 OID 33906)
-- Name: fund_operation_guard_overrides fund_operation_guard_overrides_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operation_guard_overrides
    ADD CONSTRAINT fund_operation_guard_overrides_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE SET NULL;


--
-- TOC entry 5365 (class 2606 OID 33867)
-- Name: fund_operation_guard_state fund_operation_guard_state_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operation_guard_state
    ADD CONSTRAINT fund_operation_guard_state_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5366 (class 2606 OID 33872)
-- Name: fund_operation_guard_state fund_operation_guard_state_updated_by_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operation_guard_state
    ADD CONSTRAINT fund_operation_guard_state_updated_by_user_id_fkey FOREIGN KEY (updated_by_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- TOC entry 5335 (class 2606 OID 33532)
-- Name: fund_operator_actions fund_operator_actions_allocation_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operator_actions
    ADD CONSTRAINT fund_operator_actions_allocation_batch_id_fkey FOREIGN KEY (allocation_batch_id) REFERENCES public.fund_allocation_batches(id) ON DELETE SET NULL;


--
-- TOC entry 5336 (class 2606 OID 33522)
-- Name: fund_operator_actions fund_operator_actions_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operator_actions
    ADD CONSTRAINT fund_operator_actions_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE SET NULL;


--
-- TOC entry 5337 (class 2606 OID 33527)
-- Name: fund_operator_actions fund_operator_actions_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operator_actions
    ADD CONSTRAINT fund_operator_actions_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE SET NULL;


--
-- TOC entry 5310 (class 2606 OID 33092)
-- Name: fund_orders fund_orders_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders
    ADD CONSTRAINT fund_orders_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5311 (class 2606 OID 33293)
-- Name: fund_orders fund_orders_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders
    ADD CONSTRAINT fund_orders_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE SET NULL;


--
-- TOC entry 5312 (class 2606 OID 33087)
-- Name: fund_orders fund_orders_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders
    ADD CONSTRAINT fund_orders_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5326 (class 2606 OID 33352)
-- Name: fund_runtime_state fund_runtime_state_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_runtime_state
    ADD CONSTRAINT fund_runtime_state_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5327 (class 2606 OID 33357)
-- Name: fund_runtime_state fund_runtime_state_pricing_lock_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_runtime_state
    ADD CONSTRAINT fund_runtime_state_pricing_lock_batch_id_fkey FOREIGN KEY (pricing_lock_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE SET NULL;


--
-- TOC entry 5321 (class 2606 OID 33286)
-- Name: fund_settlement_batches fund_settlement_batches_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_batches
    ADD CONSTRAINT fund_settlement_batches_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5322 (class 2606 OID 33317)
-- Name: fund_settlement_transfers fund_settlement_transfers_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5323 (class 2606 OID 33327)
-- Name: fund_settlement_transfers fund_settlement_transfers_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5324 (class 2606 OID 33322)
-- Name: fund_settlement_transfers fund_settlement_transfers_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.fund_orders(id) ON DELETE SET NULL;


--
-- TOC entry 5325 (class 2606 OID 33332)
-- Name: fund_settlement_transfers fund_settlement_transfers_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- TOC entry 5320 (class 2606 OID 33257)
-- Name: fund_wallets fund_wallets_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_wallets
    ADD CONSTRAINT fund_wallets_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5299 (class 2606 OID 32990)
-- Name: password_reset_sessions password_reset_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_sessions
    ADD CONSTRAINT password_reset_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5300 (class 2606 OID 32995)
-- Name: security_codes security_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_codes
    ADD CONSTRAINT security_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5301 (class 2606 OID 33000)
-- Name: sessions sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5313 (class 2606 OID 33115)
-- Name: user_fund_position_stats user_fund_position_stats_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats
    ADD CONSTRAINT user_fund_position_stats_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5314 (class 2606 OID 33110)
-- Name: user_fund_position_stats user_fund_position_stats_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats
    ADD CONSTRAINT user_fund_position_stats_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5302 (class 2606 OID 33005)
-- Name: user_fund_positions user_fund_positions_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5303 (class 2606 OID 33010)
-- Name: user_fund_positions user_fund_positions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5304 (class 2606 OID 33015)
-- Name: user_portfolio_daily user_portfolio_daily_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily
    ADD CONSTRAINT user_portfolio_daily_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5317 (class 2606 OID 33182)
-- Name: user_totp_recovery_codes user_totp_recovery_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_totp_recovery_codes
    ADD CONSTRAINT user_totp_recovery_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5305 (class 2606 OID 33020)
-- Name: user_wallets user_wallets_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wallets
    ADD CONSTRAINT user_wallets_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5306 (class 2606 OID 33025)
-- Name: wallet_transfers wallet_transfers_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5307 (class 2606 OID 33030)
-- Name: wallet_transfers wallet_transfers_wallet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_wallet_id_fkey FOREIGN KEY (wallet_id) REFERENCES public.user_wallets(id) ON DELETE CASCADE;


--
-- TOC entry 5308 (class 2606 OID 33057)
-- Name: withdraw_sessions withdraw_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5309 (class 2606 OID 33062)
-- Name: withdraw_sessions withdraw_sessions_wallet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_wallet_id_fkey FOREIGN KEY (wallet_id) REFERENCES public.user_wallets(id) ON DELETE CASCADE;


-- Completed on 2026-06-23 14:43:35

--
-- PostgreSQL database dump complete
--

\unrestrict 6SrhP9JApeKIoIfTI38IoBkEtsSbrVEcFHVNqt0mKO0ExPzZNgmLj9a9t05dFuK

