--
-- PostgreSQL database dump
--

\restrict Tuew2WgjePgjMKOeiZolrhqkAKtDyvZogAKSPJDOEfEX9fLf1ray45hqaTScP0d

-- Dumped from database version 16.11
-- Dumped by pg_dump version 16.11

-- Started on 2026-06-06 17:15:40

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
-- TOC entry 5393 (class 0 OID 0)
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
-- TOC entry 5394 (class 0 OID 0)
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
-- TOC entry 5395 (class 0 OID 0)
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
-- TOC entry 5396 (class 0 OID 0)
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
-- TOC entry 5397 (class 0 OID 0)
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
-- TOC entry 5398 (class 0 OID 0)
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
-- TOC entry 5399 (class 0 OID 0)
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
-- TOC entry 5400 (class 0 OID 0)
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
-- TOC entry 5401 (class 0 OID 0)
-- Dependencies: 216
-- Name: fund_nav_minute_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_nav_minute_id_seq OWNED BY public.fund_nav_minute.id;


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
    CONSTRAINT fund_negative_sale_batches_status_check CHECK (((status)::text = ANY ((ARRAY['snapshot_created'::character varying, 'sale_plan_created'::character varying, 'sale_plan_failed_requires_review'::character varying, 'sale_execution_processing'::character varying, 'sale_execution_completed'::character varying, 'sale_execution_failed_requires_review'::character varying])::text[])))
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
-- TOC entry 5402 (class 0 OID 0)
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
    CONSTRAINT fund_negative_sale_legs_status_check CHECK (((status)::text = ANY ((ARRAY['planned'::character varying, 'skipped_zero_value'::character varying, 'skipped_not_eligible'::character varying, 'skipped_min_order'::character varying, 'skipped_symbol_not_trading'::character varying, 'skipped_liquidity_guard'::character varying, 'skipped_margin_guard'::character varying, 'cash_available'::character varying, 'buffer_available'::character varying, 'failed_requires_review'::character varying])::text[])))
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
-- TOC entry 5403 (class 0 OID 0)
-- Dependencies: 268
-- Name: fund_negative_sale_legs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_negative_sale_legs_id_seq OWNED BY public.fund_negative_sale_legs.id;


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
    CONSTRAINT fund_operator_actions_action_type_check CHECK (((action_type)::text = 'retry_settlement_gas_topup'::text)),
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
-- TOC entry 5404 (class 0 OID 0)
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
-- TOC entry 5405 (class 0 OID 0)
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
    CONSTRAINT fund_settlement_batches_status_check CHECK (((status)::text = ANY ((ARRAY['created'::character varying, 'pricing_locked'::character varying, 'price_fixed'::character varying, 'gas_checking'::character varying, 'gas_ready'::character varying, 'collecting_buy_usdt'::character varying, 'buy_usdt_collected'::character varying, 'awaiting_positive_net_execution'::character varying, 'awaiting_negative_net_execution'::character varying, 'pending_confirmation'::character varying, 'positive_net_processing'::character varying, 'positive_net_accounting_finalized'::character varying, 'positive_cash_settlement_completed'::character varying, 'no_orders'::character varying, 'failed'::character varying, 'failed_requires_review'::character varying, 'paused_operator_action_required'::character varying, 'negative_net_targets_calculated'::character varying, 'negative_net_sale_planned'::character varying, 'negative_net_sale_processing'::character varying, 'awaiting_bybit_withdrawal'::character varying, 'bybit_withdrawal_confirmed'::character varying, 'seller_payout_processing'::character varying, 'negative_net_accounting_finalized'::character varying, 'negative_cash_settlement_completed'::character varying])::text[])))
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
-- TOC entry 5406 (class 0 OID 0)
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
-- TOC entry 5407 (class 0 OID 0)
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
-- TOC entry 5408 (class 0 OID 0)
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
-- TOC entry 5409 (class 0 OID 0)
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
-- TOC entry 5410 (class 0 OID 0)
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
-- TOC entry 5411 (class 0 OID 0)
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
-- TOC entry 5412 (class 0 OID 0)
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
-- TOC entry 5413 (class 0 OID 0)
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
-- TOC entry 5414 (class 0 OID 0)
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
-- TOC entry 5415 (class 0 OID 0)
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
-- TOC entry 5416 (class 0 OID 0)
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
-- TOC entry 5417 (class 0 OID 0)
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
-- TOC entry 5418 (class 0 OID 0)
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
-- TOC entry 4887 (class 2604 OID 33193)
-- Name: fee_wallet_swaps id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fee_wallet_swaps ALTER COLUMN id SET DEFAULT nextval('public.fee_wallet_swaps_id_seq'::regclass);


--
-- TOC entry 4927 (class 2604 OID 33426)
-- Name: fund_allocation_batches id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_batches ALTER COLUMN id SET DEFAULT nextval('public.fund_allocation_batches_id_seq'::regclass);


--
-- TOC entry 4933 (class 2604 OID 33454)
-- Name: fund_allocation_legs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_legs ALTER COLUMN id SET DEFAULT nextval('public.fund_allocation_legs_id_seq'::regclass);


--
-- TOC entry 4921 (class 2604 OID 33366)
-- Name: fund_bybit_accounts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_bybit_accounts ALTER COLUMN id SET DEFAULT nextval('public.fund_bybit_accounts_id_seq'::regclass);


--
-- TOC entry 4882 (class 2604 OID 33124)
-- Name: fund_chart_daily id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_daily ALTER COLUMN id SET DEFAULT nextval('public.fund_chart_daily_id_seq'::regclass);


--
-- TOC entry 4883 (class 2604 OID 33139)
-- Name: fund_chart_minute id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_minute ALTER COLUMN id SET DEFAULT nextval('public.fund_chart_minute_id_seq'::regclass);


--
-- TOC entry 4895 (class 2604 OID 33224)
-- Name: fund_nav_guard_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_events ALTER COLUMN id SET DEFAULT nextval('public.fund_nav_guard_events_id_seq'::regclass);


--
-- TOC entry 4828 (class 2604 OID 32931)
-- Name: fund_nav_minute id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute ALTER COLUMN id SET DEFAULT nextval('public.fund_nav_minute_id_seq'::regclass);


--
-- TOC entry 4944 (class 2604 OID 33554)
-- Name: fund_negative_sale_batches id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_batches ALTER COLUMN id SET DEFAULT nextval('public.fund_negative_sale_batches_id_seq'::regclass);


--
-- TOC entry 4948 (class 2604 OID 33578)
-- Name: fund_negative_sale_legs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_legs ALTER COLUMN id SET DEFAULT nextval('public.fund_negative_sale_legs_id_seq'::regclass);


--
-- TOC entry 4938 (class 2604 OID 33512)
-- Name: fund_operator_actions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operator_actions ALTER COLUMN id SET DEFAULT nextval('public.fund_operator_actions_id_seq'::regclass);


--
-- TOC entry 4876 (class 2604 OID 33081)
-- Name: fund_orders id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders ALTER COLUMN id SET DEFAULT nextval('public.fund_orders_id_seq'::regclass);


--
-- TOC entry 4902 (class 2604 OID 33271)
-- Name: fund_settlement_batches id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_batches ALTER COLUMN id SET DEFAULT nextval('public.fund_settlement_batches_id_seq'::regclass);


--
-- TOC entry 4913 (class 2604 OID 33308)
-- Name: fund_settlement_transfers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers ALTER COLUMN id SET DEFAULT nextval('public.fund_settlement_transfers_id_seq'::regclass);


--
-- TOC entry 4897 (class 2604 OID 33248)
-- Name: fund_wallets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_wallets ALTER COLUMN id SET DEFAULT nextval('public.fund_wallets_id_seq'::regclass);


--
-- TOC entry 4829 (class 2604 OID 32932)
-- Name: funds id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funds ALTER COLUMN id SET DEFAULT nextval('public.funds_id_seq'::regclass);


--
-- TOC entry 4835 (class 2604 OID 32933)
-- Name: security_codes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_codes ALTER COLUMN id SET DEFAULT nextval('public.security_codes_id_seq'::regclass);


--
-- TOC entry 4879 (class 2604 OID 33103)
-- Name: user_fund_position_stats id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats ALTER COLUMN id SET DEFAULT nextval('public.user_fund_position_stats_id_seq'::regclass);


--
-- TOC entry 4840 (class 2604 OID 32934)
-- Name: user_fund_positions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions ALTER COLUMN id SET DEFAULT nextval('public.user_fund_positions_id_seq'::regclass);


--
-- TOC entry 4843 (class 2604 OID 32935)
-- Name: user_portfolio_daily id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily ALTER COLUMN id SET DEFAULT nextval('public.user_portfolio_daily_id_seq'::regclass);


--
-- TOC entry 4884 (class 2604 OID 33177)
-- Name: user_totp_recovery_codes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_totp_recovery_codes ALTER COLUMN id SET DEFAULT nextval('public.user_totp_recovery_codes_id_seq'::regclass);


--
-- TOC entry 4845 (class 2604 OID 32936)
-- Name: user_wallets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wallets ALTER COLUMN id SET DEFAULT nextval('public.user_wallets_id_seq'::regclass);


--
-- TOC entry 4852 (class 2604 OID 32937)
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- TOC entry 4863 (class 2604 OID 32938)
-- Name: wallet_transfers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers ALTER COLUMN id SET DEFAULT nextval('public.wallet_transfers_id_seq'::regclass);


--
-- TOC entry 4873 (class 2604 OID 33050)
-- Name: withdraw_sessions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions ALTER COLUMN id SET DEFAULT nextval('public.withdraw_sessions_id_seq'::regclass);


--
-- TOC entry 5061 (class 2606 OID 33204)
-- Name: fee_wallet_swaps fee_wallet_swaps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fee_wallet_swaps
    ADD CONSTRAINT fee_wallet_swaps_pkey PRIMARY KEY (id);


--
-- TOC entry 5102 (class 2606 OID 33435)
-- Name: fund_allocation_batches fund_allocation_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_batches
    ADD CONSTRAINT fund_allocation_batches_pkey PRIMARY KEY (id);


--
-- TOC entry 5119 (class 2606 OID 33462)
-- Name: fund_allocation_legs fund_allocation_legs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_legs
    ADD CONSTRAINT fund_allocation_legs_pkey PRIMARY KEY (id);


--
-- TOC entry 5096 (class 2606 OID 33374)
-- Name: fund_bybit_accounts fund_bybit_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_bybit_accounts
    ADD CONSTRAINT fund_bybit_accounts_pkey PRIMARY KEY (id);


--
-- TOC entry 5048 (class 2606 OID 33133)
-- Name: fund_chart_daily fund_chart_daily_fund_ts_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_daily
    ADD CONSTRAINT fund_chart_daily_fund_ts_unique UNIQUE (fund_id, ts_utc);


--
-- TOC entry 5050 (class 2606 OID 33126)
-- Name: fund_chart_daily fund_chart_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_daily
    ADD CONSTRAINT fund_chart_daily_pkey PRIMARY KEY (id);


--
-- TOC entry 5053 (class 2606 OID 33148)
-- Name: fund_chart_minute fund_chart_minute_fund_ts_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_minute
    ADD CONSTRAINT fund_chart_minute_fund_ts_unique UNIQUE (fund_id, ts_utc);


--
-- TOC entry 5055 (class 2606 OID 33141)
-- Name: fund_chart_minute fund_chart_minute_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_minute
    ADD CONSTRAINT fund_chart_minute_pkey PRIMARY KEY (id);


--
-- TOC entry 5068 (class 2606 OID 33230)
-- Name: fund_nav_guard_events fund_nav_guard_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_events
    ADD CONSTRAINT fund_nav_guard_events_pkey PRIMARY KEY (id);


--
-- TOC entry 5064 (class 2606 OID 33214)
-- Name: fund_nav_guard_state fund_nav_guard_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_state
    ADD CONSTRAINT fund_nav_guard_state_pkey PRIMARY KEY (fund_id);


--
-- TOC entry 4980 (class 2606 OID 32940)
-- Name: fund_nav_minute fund_nav_minute_fund_ts_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute
    ADD CONSTRAINT fund_nav_minute_fund_ts_unique UNIQUE (fund_id, ts_utc);


--
-- TOC entry 4982 (class 2606 OID 32942)
-- Name: fund_nav_minute fund_nav_minute_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute
    ADD CONSTRAINT fund_nav_minute_pkey PRIMARY KEY (id);


--
-- TOC entry 5131 (class 2606 OID 33561)
-- Name: fund_negative_sale_batches fund_negative_sale_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_batches
    ADD CONSTRAINT fund_negative_sale_batches_pkey PRIMARY KEY (id);


--
-- TOC entry 5134 (class 2606 OID 33563)
-- Name: fund_negative_sale_batches fund_negative_sale_batches_settlement_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_batches
    ADD CONSTRAINT fund_negative_sale_batches_settlement_uq UNIQUE (settlement_batch_id);


--
-- TOC entry 5137 (class 2606 OID 33589)
-- Name: fund_negative_sale_legs fund_negative_sale_legs_batch_index_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_legs
    ADD CONSTRAINT fund_negative_sale_legs_batch_index_uq UNIQUE (sale_batch_id, leg_index);


--
-- TOC entry 5142 (class 2606 OID 33587)
-- Name: fund_negative_sale_legs fund_negative_sale_legs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_legs
    ADD CONSTRAINT fund_negative_sale_legs_pkey PRIMARY KEY (id);


--
-- TOC entry 5127 (class 2606 OID 33521)
-- Name: fund_operator_actions fund_operator_actions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operator_actions
    ADD CONSTRAINT fund_operator_actions_pkey PRIMARY KEY (id);


--
-- TOC entry 5039 (class 2606 OID 33086)
-- Name: fund_orders fund_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders
    ADD CONSTRAINT fund_orders_pkey PRIMARY KEY (id);


--
-- TOC entry 5090 (class 2606 OID 33351)
-- Name: fund_runtime_state fund_runtime_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_runtime_state
    ADD CONSTRAINT fund_runtime_state_pkey PRIMARY KEY (fund_id);


--
-- TOC entry 5079 (class 2606 OID 33285)
-- Name: fund_settlement_batches fund_settlement_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_batches
    ADD CONSTRAINT fund_settlement_batches_pkey PRIMARY KEY (id);


--
-- TOC entry 5086 (class 2606 OID 33316)
-- Name: fund_settlement_transfers fund_settlement_transfers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_pkey PRIMARY KEY (id);


--
-- TOC entry 5073 (class 2606 OID 33256)
-- Name: fund_wallets fund_wallets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_wallets
    ADD CONSTRAINT fund_wallets_pkey PRIMARY KEY (id);


--
-- TOC entry 4984 (class 2606 OID 32944)
-- Name: funds funds_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funds
    ADD CONSTRAINT funds_code_key UNIQUE (code);


--
-- TOC entry 4986 (class 2606 OID 32946)
-- Name: funds funds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funds
    ADD CONSTRAINT funds_pkey PRIMARY KEY (id);


--
-- TOC entry 4988 (class 2606 OID 32948)
-- Name: password_reset_sessions password_reset_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_sessions
    ADD CONSTRAINT password_reset_sessions_pkey PRIMARY KEY (id);


--
-- TOC entry 4990 (class 2606 OID 32950)
-- Name: security_codes security_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_codes
    ADD CONSTRAINT security_codes_pkey PRIMARY KEY (id);


--
-- TOC entry 4993 (class 2606 OID 32952)
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);


--
-- TOC entry 5043 (class 2606 OID 33107)
-- Name: user_fund_position_stats user_fund_position_stats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats
    ADD CONSTRAINT user_fund_position_stats_pkey PRIMARY KEY (id);


--
-- TOC entry 5045 (class 2606 OID 33109)
-- Name: user_fund_position_stats user_fund_position_stats_user_fund_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats
    ADD CONSTRAINT user_fund_position_stats_user_fund_uq UNIQUE (user_id, fund_id);


--
-- TOC entry 4995 (class 2606 OID 32954)
-- Name: user_fund_positions user_fund_positions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_pkey PRIMARY KEY (id);


--
-- TOC entry 4997 (class 2606 OID 32956)
-- Name: user_fund_positions user_fund_positions_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_unique UNIQUE (user_id, fund_id);


--
-- TOC entry 5000 (class 2606 OID 32958)
-- Name: user_portfolio_daily user_portfolio_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily
    ADD CONSTRAINT user_portfolio_daily_pkey PRIMARY KEY (id);


--
-- TOC entry 5002 (class 2606 OID 32960)
-- Name: user_portfolio_daily user_portfolio_daily_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily
    ADD CONSTRAINT user_portfolio_daily_unique UNIQUE (user_id, date_utc);


--
-- TOC entry 5057 (class 2606 OID 33181)
-- Name: user_totp_recovery_codes user_totp_recovery_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_totp_recovery_codes
    ADD CONSTRAINT user_totp_recovery_codes_pkey PRIMARY KEY (id);


--
-- TOC entry 5007 (class 2606 OID 32962)
-- Name: user_wallets user_wallets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wallets
    ADD CONSTRAINT user_wallets_pkey PRIMARY KEY (id);


--
-- TOC entry 5012 (class 2606 OID 32964)
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- TOC entry 5014 (class 2606 OID 32966)
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- TOC entry 5020 (class 2606 OID 32968)
-- Name: wallet_transfers wallet_transfers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_pkey PRIMARY KEY (id);


--
-- TOC entry 5023 (class 2606 OID 32970)
-- Name: wallet_transfers wallet_transfers_tx_hash_log_index_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_tx_hash_log_index_uq UNIQUE (tx_hash, log_index);


--
-- TOC entry 5030 (class 2606 OID 33054)
-- Name: withdraw_sessions withdraw_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_pkey PRIMARY KEY (id);


--
-- TOC entry 5032 (class 2606 OID 33056)
-- Name: withdraw_sessions withdraw_sessions_token_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_token_key UNIQUE (token);


--
-- TOC entry 5027 (class 2606 OID 33044)
-- Name: worker_cursors worker_cursors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.worker_cursors
    ADD CONSTRAINT worker_cursors_pkey PRIMARY KEY (name);


--
-- TOC entry 5059 (class 1259 OID 33206)
-- Name: fee_wallet_swaps_one_success_per_day_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fee_wallet_swaps_one_success_per_day_idx ON public.fee_wallet_swaps USING btree (wallet_type, (((created_at AT TIME ZONE 'UTC'::text))::date)) WHERE ((status)::text = 'success'::text);


--
-- TOC entry 5062 (class 1259 OID 33205)
-- Name: fee_wallet_swaps_wallet_type_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fee_wallet_swaps_wallet_type_created_idx ON public.fee_wallet_swaps USING btree (wallet_type, created_at DESC);


--
-- TOC entry 5098 (class 1259 OID 33448)
-- Name: fund_allocation_batches_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_batches_created_idx ON public.fund_allocation_batches USING btree (created_at DESC);


--
-- TOC entry 5099 (class 1259 OID 33505)
-- Name: fund_allocation_batches_fund_status_completed_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_batches_fund_status_completed_idx ON public.fund_allocation_batches USING btree (fund_id, status, completed_at DESC);


--
-- TOC entry 5100 (class 1259 OID 33447)
-- Name: fund_allocation_batches_fund_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_batches_fund_status_idx ON public.fund_allocation_batches USING btree (fund_id, status);


--
-- TOC entry 5103 (class 1259 OID 33506)
-- Name: fund_allocation_batches_residual_cash_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_batches_residual_cash_idx ON public.fund_allocation_batches USING btree (residual_cash_usdt) WHERE (residual_cash_usdt IS NOT NULL);


--
-- TOC entry 5104 (class 1259 OID 33446)
-- Name: fund_allocation_batches_settlement_batch_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_allocation_batches_settlement_batch_uq ON public.fund_allocation_batches USING btree (settlement_batch_id);


--
-- TOC entry 5105 (class 1259 OID 33504)
-- Name: fund_allocation_batches_status_completed_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_batches_status_completed_idx ON public.fund_allocation_batches USING btree (status, completed_at DESC);


--
-- TOC entry 5106 (class 1259 OID 33483)
-- Name: fund_allocation_legs_batch_leg_index_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_allocation_legs_batch_leg_index_uq ON public.fund_allocation_legs USING btree (allocation_batch_id, leg_index);


--
-- TOC entry 5107 (class 1259 OID 33484)
-- Name: fund_allocation_legs_batch_leg_key_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_allocation_legs_batch_leg_key_uq ON public.fund_allocation_legs USING btree (allocation_batch_id, leg_key);


--
-- TOC entry 5108 (class 1259 OID 33485)
-- Name: fund_allocation_legs_batch_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_batch_status_idx ON public.fund_allocation_legs USING btree (allocation_batch_id, status);


--
-- TOC entry 5109 (class 1259 OID 33494)
-- Name: fund_allocation_legs_bybit_order_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_bybit_order_idx ON public.fund_allocation_legs USING btree (bybit_order_id) WHERE (bybit_order_id IS NOT NULL);


--
-- TOC entry 5110 (class 1259 OID 33500)
-- Name: fund_allocation_legs_category_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_category_status_idx ON public.fund_allocation_legs USING btree (category, status);


--
-- TOC entry 5111 (class 1259 OID 33495)
-- Name: fund_allocation_legs_earn_order_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_earn_order_idx ON public.fund_allocation_legs USING btree (earn_order_id) WHERE (earn_order_id IS NOT NULL);


--
-- TOC entry 5112 (class 1259 OID 33493)
-- Name: fund_allocation_legs_execution_mode_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_execution_mode_status_idx ON public.fund_allocation_legs USING btree (execution_mode, status);


--
-- TOC entry 5113 (class 1259 OID 33486)
-- Name: fund_allocation_legs_fund_group_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_fund_group_idx ON public.fund_allocation_legs USING btree (fund_id, leg_group);


--
-- TOC entry 5114 (class 1259 OID 33501)
-- Name: fund_allocation_legs_group_type_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_group_type_status_idx ON public.fund_allocation_legs USING btree (leg_group, leg_type, status);


--
-- TOC entry 5115 (class 1259 OID 33499)
-- Name: fund_allocation_legs_margin_guard_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_margin_guard_idx ON public.fund_allocation_legs USING btree (margin_guard_status) WHERE (margin_guard_status IS NOT NULL);


--
-- TOC entry 5116 (class 1259 OID 33488)
-- Name: fund_allocation_legs_order_link_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_order_link_idx ON public.fund_allocation_legs USING btree (order_link_id) WHERE (order_link_id IS NOT NULL);


--
-- TOC entry 5117 (class 1259 OID 33496)
-- Name: fund_allocation_legs_parent_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_parent_idx ON public.fund_allocation_legs USING btree (parent_leg_id) WHERE (parent_leg_id IS NOT NULL);


--
-- TOC entry 5120 (class 1259 OID 33497)
-- Name: fund_allocation_legs_residual_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_residual_idx ON public.fund_allocation_legs USING btree (allocation_batch_id, residual_usdt) WHERE (residual_usdt IS NOT NULL);


--
-- TOC entry 5121 (class 1259 OID 33487)
-- Name: fund_allocation_legs_strategy_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_strategy_idx ON public.fund_allocation_legs USING btree (strategy_id) WHERE (strategy_id IS NOT NULL);


--
-- TOC entry 5091 (class 1259 OID 33380)
-- Name: fund_bybit_accounts_active_fund_coin_chain_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_bybit_accounts_active_fund_coin_chain_uq ON public.fund_bybit_accounts USING btree (fund_id, coin, chain_type) WHERE (is_active = true);


--
-- TOC entry 5092 (class 1259 OID 33421)
-- Name: fund_bybit_accounts_api_key_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_bybit_accounts_api_key_active_idx ON public.fund_bybit_accounts USING btree (fund_id, api_key_is_active);


--
-- TOC entry 5093 (class 1259 OID 33383)
-- Name: fund_bybit_accounts_deposit_address_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_bybit_accounts_deposit_address_idx ON public.fund_bybit_accounts USING btree (deposit_address);


--
-- TOC entry 5094 (class 1259 OID 33382)
-- Name: fund_bybit_accounts_fund_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_bybit_accounts_fund_id_idx ON public.fund_bybit_accounts USING btree (fund_id);


--
-- TOC entry 5097 (class 1259 OID 33381)
-- Name: fund_bybit_accounts_sub_uid_coin_chain_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_bybit_accounts_sub_uid_coin_chain_uq ON public.fund_bybit_accounts USING btree (bybit_sub_uid, coin, chain_type) WHERE (is_active = true);


--
-- TOC entry 5046 (class 1259 OID 33134)
-- Name: fund_chart_daily_fund_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_chart_daily_fund_ts_idx ON public.fund_chart_daily USING btree (fund_id, ts_utc DESC);


--
-- TOC entry 5051 (class 1259 OID 33149)
-- Name: fund_chart_minute_fund_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_chart_minute_fund_ts_idx ON public.fund_chart_minute USING btree (fund_id, ts_utc DESC);


--
-- TOC entry 5065 (class 1259 OID 33237)
-- Name: fund_nav_guard_events_decision_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_nav_guard_events_decision_created_idx ON public.fund_nav_guard_events USING btree (decision, created_at DESC);


--
-- TOC entry 5066 (class 1259 OID 33236)
-- Name: fund_nav_guard_events_fund_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_nav_guard_events_fund_created_idx ON public.fund_nav_guard_events USING btree (fund_id, created_at DESC);


--
-- TOC entry 4978 (class 1259 OID 32971)
-- Name: fund_nav_minute_fund_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_nav_minute_fund_ts_idx ON public.fund_nav_minute USING btree (fund_id, ts_utc DESC);


--
-- TOC entry 5129 (class 1259 OID 33608)
-- Name: fund_negative_sale_batches_fund_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_batches_fund_status_idx ON public.fund_negative_sale_batches USING btree (fund_id, status, created_at DESC);


--
-- TOC entry 5132 (class 1259 OID 33609)
-- Name: fund_negative_sale_batches_settlement_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_batches_settlement_idx ON public.fund_negative_sale_batches USING btree (settlement_batch_id);


--
-- TOC entry 5135 (class 1259 OID 33607)
-- Name: fund_negative_sale_batches_settlement_uq_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_negative_sale_batches_settlement_uq_idx ON public.fund_negative_sale_batches USING btree (settlement_batch_id);


--
-- TOC entry 5138 (class 1259 OID 33610)
-- Name: fund_negative_sale_legs_batch_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_legs_batch_status_idx ON public.fund_negative_sale_legs USING btree (sale_batch_id, status);


--
-- TOC entry 5139 (class 1259 OID 33611)
-- Name: fund_negative_sale_legs_group_type_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_legs_group_type_status_idx ON public.fund_negative_sale_legs USING btree (leg_group, leg_type, status);


--
-- TOC entry 5140 (class 1259 OID 33613)
-- Name: fund_negative_sale_legs_order_link_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_legs_order_link_idx ON public.fund_negative_sale_legs USING btree (order_link_id) WHERE (order_link_id IS NOT NULL);


--
-- TOC entry 5143 (class 1259 OID 33614)
-- Name: fund_negative_sale_legs_strategy_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_legs_strategy_idx ON public.fund_negative_sale_legs USING btree (strategy_id) WHERE (strategy_id IS NOT NULL);


--
-- TOC entry 5144 (class 1259 OID 33612)
-- Name: fund_negative_sale_legs_symbol_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_negative_sale_legs_symbol_status_idx ON public.fund_negative_sale_legs USING btree (symbol, status) WHERE (symbol IS NOT NULL);


--
-- TOC entry 5122 (class 1259 OID 33544)
-- Name: fund_operator_actions_callback_token_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_operator_actions_callback_token_idx ON public.fund_operator_actions USING btree (callback_token_hash) WHERE (callback_token_hash IS NOT NULL);


--
-- TOC entry 5123 (class 1259 OID 33542)
-- Name: fund_operator_actions_fund_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_operator_actions_fund_status_idx ON public.fund_operator_actions USING btree (fund_id, status, requested_at DESC);


--
-- TOC entry 5124 (class 1259 OID 33540)
-- Name: fund_operator_actions_idempotency_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_operator_actions_idempotency_uq ON public.fund_operator_actions USING btree (idempotency_key);


--
-- TOC entry 5125 (class 1259 OID 33541)
-- Name: fund_operator_actions_pending_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_operator_actions_pending_idx ON public.fund_operator_actions USING btree (action_type, status, requested_at);


--
-- TOC entry 5128 (class 1259 OID 33543)
-- Name: fund_operator_actions_settlement_batch_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_operator_actions_settlement_batch_idx ON public.fund_operator_actions USING btree (settlement_batch_id);


--
-- TOC entry 5033 (class 1259 OID 33300)
-- Name: fund_orders_batch_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_batch_id_idx ON public.fund_orders USING btree (settlement_batch_id);


--
-- TOC entry 5034 (class 1259 OID 33392)
-- Name: fund_orders_batch_side_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_batch_side_status_idx ON public.fund_orders USING btree (settlement_batch_id, side, status);


--
-- TOC entry 5035 (class 1259 OID 33548)
-- Name: fund_orders_fee_audit_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_fee_audit_idx ON public.fund_orders USING btree (settlement_batch_id, partial_month_fee_usdt) WHERE (partial_month_fee_usdt IS NOT NULL);


--
-- TOC entry 5036 (class 1259 OID 33098)
-- Name: fund_orders_fund_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_fund_created_at_idx ON public.fund_orders USING btree (fund_id, created_at DESC);


--
-- TOC entry 5037 (class 1259 OID 33391)
-- Name: fund_orders_pending_cutoff_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_pending_cutoff_idx ON public.fund_orders USING btree (fund_id, status, created_at);


--
-- TOC entry 5040 (class 1259 OID 33547)
-- Name: fund_orders_settlement_side_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_settlement_side_status_idx ON public.fund_orders USING btree (settlement_batch_id, side, status);


--
-- TOC entry 5041 (class 1259 OID 33097)
-- Name: fund_orders_user_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_user_created_at_idx ON public.fund_orders USING btree (user_id, created_at DESC);


--
-- TOC entry 5074 (class 1259 OID 33417)
-- Name: fund_settlement_batches_bybit_tx_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_batches_bybit_tx_idx ON public.fund_settlement_batches USING btree (bybit_deposit_tx_hash);


--
-- TOC entry 5075 (class 1259 OID 33291)
-- Name: fund_settlement_batches_fund_date_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_settlement_batches_fund_date_uq ON public.fund_settlement_batches USING btree (fund_id, settlement_date);


--
-- TOC entry 5076 (class 1259 OID 33418)
-- Name: fund_settlement_batches_internal_transfer_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_batches_internal_transfer_idx ON public.fund_settlement_batches USING btree (bybit_internal_transfer_id);


--
-- TOC entry 5077 (class 1259 OID 33549)
-- Name: fund_settlement_batches_negative_targets_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_batches_negative_targets_idx ON public.fund_settlement_batches USING btree (fund_id, status, negative_net_target_calculated_at DESC) WHERE (negative_net_target_calculated_at IS NOT NULL);


--
-- TOC entry 5080 (class 1259 OID 33395)
-- Name: fund_settlement_batches_positive_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_batches_positive_status_idx ON public.fund_settlement_batches USING btree (status, settlement_date);


--
-- TOC entry 5081 (class 1259 OID 33339)
-- Name: fund_settlement_transfers_batch_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_transfers_batch_idx ON public.fund_settlement_transfers USING btree (batch_id);


--
-- TOC entry 5082 (class 1259 OID 33413)
-- Name: fund_settlement_transfers_batch_type_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_transfers_batch_type_status_idx ON public.fund_settlement_transfers USING btree (batch_id, transfer_type, status);


--
-- TOC entry 5083 (class 1259 OID 33340)
-- Name: fund_settlement_transfers_order_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_transfers_order_idx ON public.fund_settlement_transfers USING btree (order_id);


--
-- TOC entry 5084 (class 1259 OID 33412)
-- Name: fund_settlement_transfers_order_type_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_settlement_transfers_order_type_uq ON public.fund_settlement_transfers USING btree (batch_id, order_id, transfer_type) WHERE (order_id IS NOT NULL);


--
-- TOC entry 5087 (class 1259 OID 33409)
-- Name: fund_settlement_transfers_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_transfers_status_idx ON public.fund_settlement_transfers USING btree (status);


--
-- TOC entry 5088 (class 1259 OID 33342)
-- Name: fund_settlement_transfers_tx_hash_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_settlement_transfers_tx_hash_uq ON public.fund_settlement_transfers USING btree (tx_hash) WHERE (tx_hash IS NOT NULL);


--
-- TOC entry 5069 (class 1259 OID 33263)
-- Name: fund_wallets_active_settlement_fund_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_wallets_active_settlement_fund_uq ON public.fund_wallets USING btree (fund_id, blockchain, wallet_type) WHERE (is_active = true);


--
-- TOC entry 5070 (class 1259 OID 33262)
-- Name: fund_wallets_blockchain_address_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_wallets_blockchain_address_uq ON public.fund_wallets USING btree (blockchain, address);


--
-- TOC entry 5071 (class 1259 OID 33264)
-- Name: fund_wallets_fund_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_wallets_fund_id_idx ON public.fund_wallets USING btree (fund_id);


--
-- TOC entry 4991 (class 1259 OID 32972)
-- Name: idx_sessions_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_expires_at ON public.sessions USING btree (expires_at);


--
-- TOC entry 5009 (class 1259 OID 32973)
-- Name: idx_users_compliance_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_compliance_status ON public.users USING btree (compliance_status);


--
-- TOC entry 5015 (class 1259 OID 32974)
-- Name: idx_wallet_transfers_compliance_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_compliance_status ON public.wallet_transfers USING btree (compliance_status);


--
-- TOC entry 5016 (class 1259 OID 32975)
-- Name: idx_wallet_transfers_need_compliance; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_need_compliance ON public.wallet_transfers USING btree (status, compliance_status);


--
-- TOC entry 5017 (class 1259 OID 33073)
-- Name: idx_wallet_transfers_user_type_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_user_type_time ON public.wallet_transfers USING btree (user_id, type, detected_at DESC);


--
-- TOC entry 5018 (class 1259 OID 33072)
-- Name: idx_wallet_transfers_withdraw_processing; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_withdraw_processing ON public.wallet_transfers USING btree (type, status) WHERE (((type)::text = 'withdraw'::text) AND ((status)::text = 'processing'::text));


--
-- TOC entry 5028 (class 1259 OID 33067)
-- Name: idx_withdraw_sessions_user_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_withdraw_sessions_user_expires ON public.withdraw_sessions USING btree (user_id, expires_at DESC);


--
-- TOC entry 4998 (class 1259 OID 32976)
-- Name: user_fund_positions_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_fund_positions_user_idx ON public.user_fund_positions USING btree (user_id);


--
-- TOC entry 5003 (class 1259 OID 32977)
-- Name: user_portfolio_daily_user_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_portfolio_daily_user_date_idx ON public.user_portfolio_daily USING btree (user_id, date_utc DESC);


--
-- TOC entry 5058 (class 1259 OID 33187)
-- Name: user_totp_recovery_codes_user_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_totp_recovery_codes_user_active_idx ON public.user_totp_recovery_codes USING btree (user_id, is_used);


--
-- TOC entry 5004 (class 1259 OID 32978)
-- Name: user_wallets_blockchain_address_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX user_wallets_blockchain_address_uq ON public.user_wallets USING btree (blockchain, address);


--
-- TOC entry 5005 (class 1259 OID 33069)
-- Name: user_wallets_one_active_bsc; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX user_wallets_one_active_bsc ON public.user_wallets USING btree (user_id) WHERE (((blockchain)::text = 'BSC'::text) AND (is_active = true));


--
-- TOC entry 5008 (class 1259 OID 32980)
-- Name: user_wallets_user_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_wallets_user_id_idx ON public.user_wallets USING btree (user_id);


--
-- TOC entry 5010 (class 1259 OID 32981)
-- Name: users_backup_email_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX users_backup_email_idx ON public.users USING btree (backup_email);


--
-- TOC entry 5021 (class 1259 OID 32982)
-- Name: wallet_transfers_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX wallet_transfers_status_idx ON public.wallet_transfers USING btree (status);


--
-- TOC entry 5024 (class 1259 OID 32983)
-- Name: wallet_transfers_user_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX wallet_transfers_user_time_idx ON public.wallet_transfers USING btree (user_id, tx_time DESC);


--
-- TOC entry 5025 (class 1259 OID 32984)
-- Name: wallet_transfers_wallet_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX wallet_transfers_wallet_time_idx ON public.wallet_transfers USING btree (wallet_id, tx_time DESC);


--
-- TOC entry 5176 (class 2606 OID 33441)
-- Name: fund_allocation_batches fund_allocation_batches_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_batches
    ADD CONSTRAINT fund_allocation_batches_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5177 (class 2606 OID 33436)
-- Name: fund_allocation_batches fund_allocation_batches_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_batches
    ADD CONSTRAINT fund_allocation_batches_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5178 (class 2606 OID 33463)
-- Name: fund_allocation_legs fund_allocation_legs_allocation_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_legs
    ADD CONSTRAINT fund_allocation_legs_allocation_batch_id_fkey FOREIGN KEY (allocation_batch_id) REFERENCES public.fund_allocation_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5179 (class 2606 OID 33473)
-- Name: fund_allocation_legs fund_allocation_legs_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_legs
    ADD CONSTRAINT fund_allocation_legs_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5180 (class 2606 OID 33478)
-- Name: fund_allocation_legs fund_allocation_legs_parent_leg_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_legs
    ADD CONSTRAINT fund_allocation_legs_parent_leg_id_fkey FOREIGN KEY (parent_leg_id) REFERENCES public.fund_allocation_legs(id) ON DELETE SET NULL;


--
-- TOC entry 5181 (class 2606 OID 33468)
-- Name: fund_allocation_legs fund_allocation_legs_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_legs
    ADD CONSTRAINT fund_allocation_legs_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5175 (class 2606 OID 33375)
-- Name: fund_bybit_accounts fund_bybit_accounts_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_bybit_accounts
    ADD CONSTRAINT fund_bybit_accounts_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5162 (class 2606 OID 33127)
-- Name: fund_chart_daily fund_chart_daily_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_daily
    ADD CONSTRAINT fund_chart_daily_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5163 (class 2606 OID 33142)
-- Name: fund_chart_minute fund_chart_minute_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_minute
    ADD CONSTRAINT fund_chart_minute_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5166 (class 2606 OID 33231)
-- Name: fund_nav_guard_events fund_nav_guard_events_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_events
    ADD CONSTRAINT fund_nav_guard_events_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5165 (class 2606 OID 33215)
-- Name: fund_nav_guard_state fund_nav_guard_state_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_state
    ADD CONSTRAINT fund_nav_guard_state_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5145 (class 2606 OID 32985)
-- Name: fund_nav_minute fund_nav_minute_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute
    ADD CONSTRAINT fund_nav_minute_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5185 (class 2606 OID 33569)
-- Name: fund_negative_sale_batches fund_negative_sale_batches_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_batches
    ADD CONSTRAINT fund_negative_sale_batches_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5186 (class 2606 OID 33564)
-- Name: fund_negative_sale_batches fund_negative_sale_batches_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_batches
    ADD CONSTRAINT fund_negative_sale_batches_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5187 (class 2606 OID 33600)
-- Name: fund_negative_sale_legs fund_negative_sale_legs_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_legs
    ADD CONSTRAINT fund_negative_sale_legs_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5188 (class 2606 OID 33590)
-- Name: fund_negative_sale_legs fund_negative_sale_legs_sale_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_legs
    ADD CONSTRAINT fund_negative_sale_legs_sale_batch_id_fkey FOREIGN KEY (sale_batch_id) REFERENCES public.fund_negative_sale_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5189 (class 2606 OID 33595)
-- Name: fund_negative_sale_legs fund_negative_sale_legs_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_negative_sale_legs
    ADD CONSTRAINT fund_negative_sale_legs_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5182 (class 2606 OID 33532)
-- Name: fund_operator_actions fund_operator_actions_allocation_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operator_actions
    ADD CONSTRAINT fund_operator_actions_allocation_batch_id_fkey FOREIGN KEY (allocation_batch_id) REFERENCES public.fund_allocation_batches(id) ON DELETE SET NULL;


--
-- TOC entry 5183 (class 2606 OID 33522)
-- Name: fund_operator_actions fund_operator_actions_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operator_actions
    ADD CONSTRAINT fund_operator_actions_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE SET NULL;


--
-- TOC entry 5184 (class 2606 OID 33527)
-- Name: fund_operator_actions fund_operator_actions_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_operator_actions
    ADD CONSTRAINT fund_operator_actions_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE SET NULL;


--
-- TOC entry 5157 (class 2606 OID 33092)
-- Name: fund_orders fund_orders_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders
    ADD CONSTRAINT fund_orders_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5158 (class 2606 OID 33293)
-- Name: fund_orders fund_orders_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders
    ADD CONSTRAINT fund_orders_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE SET NULL;


--
-- TOC entry 5159 (class 2606 OID 33087)
-- Name: fund_orders fund_orders_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders
    ADD CONSTRAINT fund_orders_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5173 (class 2606 OID 33352)
-- Name: fund_runtime_state fund_runtime_state_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_runtime_state
    ADD CONSTRAINT fund_runtime_state_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5174 (class 2606 OID 33357)
-- Name: fund_runtime_state fund_runtime_state_pricing_lock_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_runtime_state
    ADD CONSTRAINT fund_runtime_state_pricing_lock_batch_id_fkey FOREIGN KEY (pricing_lock_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE SET NULL;


--
-- TOC entry 5168 (class 2606 OID 33286)
-- Name: fund_settlement_batches fund_settlement_batches_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_batches
    ADD CONSTRAINT fund_settlement_batches_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5169 (class 2606 OID 33317)
-- Name: fund_settlement_transfers fund_settlement_transfers_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5170 (class 2606 OID 33327)
-- Name: fund_settlement_transfers fund_settlement_transfers_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5171 (class 2606 OID 33322)
-- Name: fund_settlement_transfers fund_settlement_transfers_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.fund_orders(id) ON DELETE SET NULL;


--
-- TOC entry 5172 (class 2606 OID 33332)
-- Name: fund_settlement_transfers fund_settlement_transfers_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- TOC entry 5167 (class 2606 OID 33257)
-- Name: fund_wallets fund_wallets_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_wallets
    ADD CONSTRAINT fund_wallets_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5146 (class 2606 OID 32990)
-- Name: password_reset_sessions password_reset_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_sessions
    ADD CONSTRAINT password_reset_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5147 (class 2606 OID 32995)
-- Name: security_codes security_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_codes
    ADD CONSTRAINT security_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5148 (class 2606 OID 33000)
-- Name: sessions sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5160 (class 2606 OID 33115)
-- Name: user_fund_position_stats user_fund_position_stats_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats
    ADD CONSTRAINT user_fund_position_stats_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5161 (class 2606 OID 33110)
-- Name: user_fund_position_stats user_fund_position_stats_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats
    ADD CONSTRAINT user_fund_position_stats_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5149 (class 2606 OID 33005)
-- Name: user_fund_positions user_fund_positions_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5150 (class 2606 OID 33010)
-- Name: user_fund_positions user_fund_positions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5151 (class 2606 OID 33015)
-- Name: user_portfolio_daily user_portfolio_daily_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily
    ADD CONSTRAINT user_portfolio_daily_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5164 (class 2606 OID 33182)
-- Name: user_totp_recovery_codes user_totp_recovery_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_totp_recovery_codes
    ADD CONSTRAINT user_totp_recovery_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5152 (class 2606 OID 33020)
-- Name: user_wallets user_wallets_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wallets
    ADD CONSTRAINT user_wallets_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5153 (class 2606 OID 33025)
-- Name: wallet_transfers wallet_transfers_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5154 (class 2606 OID 33030)
-- Name: wallet_transfers wallet_transfers_wallet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_wallet_id_fkey FOREIGN KEY (wallet_id) REFERENCES public.user_wallets(id) ON DELETE CASCADE;


--
-- TOC entry 5155 (class 2606 OID 33057)
-- Name: withdraw_sessions withdraw_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5156 (class 2606 OID 33062)
-- Name: withdraw_sessions withdraw_sessions_wallet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_wallet_id_fkey FOREIGN KEY (wallet_id) REFERENCES public.user_wallets(id) ON DELETE CASCADE;


-- Completed on 2026-06-06 17:15:40

--
-- PostgreSQL database dump complete
--

\unrestrict Tuew2WgjePgjMKOeiZolrhqkAKtDyvZogAKSPJDOEfEX9fLf1ray45hqaTScP0d

