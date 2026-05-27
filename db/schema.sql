--
-- PostgreSQL database dump
--

\restrict Wg2lNBQIt94OFX8QaVValpWtKnIGJYJTkZvYoM3jZ2TWgTPoO9D84xEvhSCHo1Q

-- Dumped from database version 16.11
-- Dumped by pg_dump version 16.11

-- Started on 2026-05-27 16:49:29

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
-- TOC entry 5306 (class 0 OID 0)
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
-- TOC entry 5307 (class 0 OID 0)
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
    CONSTRAINT fund_allocation_batches_status_check CHECK (((status)::text = ANY ((ARRAY['planned'::character varying, 'snapshot_created'::character varying, 'plan_created'::character varying, 'failed_requires_review'::character varying, 'allocation_processing'::character varying, 'allocation_completed'::character varying, 'allocation_completed_with_residual_earn'::character varying, 'allocation_completed_with_residual_cash'::character varying])::text[])))
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
-- TOC entry 5308 (class 0 OID 0)
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
-- TOC entry 5309 (class 0 OID 0)
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
-- TOC entry 5310 (class 0 OID 0)
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
-- TOC entry 5311 (class 0 OID 0)
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
-- TOC entry 5312 (class 0 OID 0)
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
-- TOC entry 5313 (class 0 OID 0)
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
-- TOC entry 5314 (class 0 OID 0)
-- Dependencies: 216
-- Name: fund_nav_minute_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_nav_minute_id_seq OWNED BY public.fund_nav_minute.id;


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
-- TOC entry 5315 (class 0 OID 0)
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
    CONSTRAINT fund_settlement_batches_status_check CHECK (((status)::text = ANY ((ARRAY['created'::character varying, 'pricing_locked'::character varying, 'price_fixed'::character varying, 'gas_checking'::character varying, 'gas_ready'::character varying, 'collecting_buy_usdt'::character varying, 'buy_usdt_collected'::character varying, 'awaiting_positive_net_execution'::character varying, 'awaiting_negative_net_execution'::character varying, 'pending_confirmation'::character varying, 'positive_net_processing'::character varying, 'positive_net_accounting_finalized'::character varying, 'positive_cash_settlement_completed'::character varying, 'no_orders'::character varying, 'failed'::character varying, 'failed_requires_review'::character varying])::text[])))
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
-- TOC entry 5316 (class 0 OID 0)
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
-- TOC entry 5317 (class 0 OID 0)
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
-- TOC entry 5318 (class 0 OID 0)
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
-- TOC entry 5319 (class 0 OID 0)
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
-- TOC entry 5320 (class 0 OID 0)
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
-- TOC entry 5321 (class 0 OID 0)
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
-- TOC entry 5322 (class 0 OID 0)
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
-- TOC entry 5323 (class 0 OID 0)
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
-- TOC entry 5324 (class 0 OID 0)
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
-- TOC entry 5325 (class 0 OID 0)
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
-- TOC entry 5326 (class 0 OID 0)
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
-- TOC entry 5327 (class 0 OID 0)
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
-- TOC entry 5328 (class 0 OID 0)
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
-- TOC entry 4872 (class 2604 OID 33193)
-- Name: fee_wallet_swaps id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fee_wallet_swaps ALTER COLUMN id SET DEFAULT nextval('public.fee_wallet_swaps_id_seq'::regclass);


--
-- TOC entry 4912 (class 2604 OID 33426)
-- Name: fund_allocation_batches id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_batches ALTER COLUMN id SET DEFAULT nextval('public.fund_allocation_batches_id_seq'::regclass);


--
-- TOC entry 4918 (class 2604 OID 33454)
-- Name: fund_allocation_legs id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_legs ALTER COLUMN id SET DEFAULT nextval('public.fund_allocation_legs_id_seq'::regclass);


--
-- TOC entry 4906 (class 2604 OID 33366)
-- Name: fund_bybit_accounts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_bybit_accounts ALTER COLUMN id SET DEFAULT nextval('public.fund_bybit_accounts_id_seq'::regclass);


--
-- TOC entry 4867 (class 2604 OID 33124)
-- Name: fund_chart_daily id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_daily ALTER COLUMN id SET DEFAULT nextval('public.fund_chart_daily_id_seq'::regclass);


--
-- TOC entry 4868 (class 2604 OID 33139)
-- Name: fund_chart_minute id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_minute ALTER COLUMN id SET DEFAULT nextval('public.fund_chart_minute_id_seq'::regclass);


--
-- TOC entry 4880 (class 2604 OID 33224)
-- Name: fund_nav_guard_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_events ALTER COLUMN id SET DEFAULT nextval('public.fund_nav_guard_events_id_seq'::regclass);


--
-- TOC entry 4813 (class 2604 OID 32931)
-- Name: fund_nav_minute id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute ALTER COLUMN id SET DEFAULT nextval('public.fund_nav_minute_id_seq'::regclass);


--
-- TOC entry 4861 (class 2604 OID 33081)
-- Name: fund_orders id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders ALTER COLUMN id SET DEFAULT nextval('public.fund_orders_id_seq'::regclass);


--
-- TOC entry 4887 (class 2604 OID 33271)
-- Name: fund_settlement_batches id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_batches ALTER COLUMN id SET DEFAULT nextval('public.fund_settlement_batches_id_seq'::regclass);


--
-- TOC entry 4898 (class 2604 OID 33308)
-- Name: fund_settlement_transfers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers ALTER COLUMN id SET DEFAULT nextval('public.fund_settlement_transfers_id_seq'::regclass);


--
-- TOC entry 4882 (class 2604 OID 33248)
-- Name: fund_wallets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_wallets ALTER COLUMN id SET DEFAULT nextval('public.fund_wallets_id_seq'::regclass);


--
-- TOC entry 4814 (class 2604 OID 32932)
-- Name: funds id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funds ALTER COLUMN id SET DEFAULT nextval('public.funds_id_seq'::regclass);


--
-- TOC entry 4820 (class 2604 OID 32933)
-- Name: security_codes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_codes ALTER COLUMN id SET DEFAULT nextval('public.security_codes_id_seq'::regclass);


--
-- TOC entry 4864 (class 2604 OID 33103)
-- Name: user_fund_position_stats id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats ALTER COLUMN id SET DEFAULT nextval('public.user_fund_position_stats_id_seq'::regclass);


--
-- TOC entry 4825 (class 2604 OID 32934)
-- Name: user_fund_positions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions ALTER COLUMN id SET DEFAULT nextval('public.user_fund_positions_id_seq'::regclass);


--
-- TOC entry 4828 (class 2604 OID 32935)
-- Name: user_portfolio_daily id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily ALTER COLUMN id SET DEFAULT nextval('public.user_portfolio_daily_id_seq'::regclass);


--
-- TOC entry 4869 (class 2604 OID 33177)
-- Name: user_totp_recovery_codes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_totp_recovery_codes ALTER COLUMN id SET DEFAULT nextval('public.user_totp_recovery_codes_id_seq'::regclass);


--
-- TOC entry 4830 (class 2604 OID 32936)
-- Name: user_wallets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wallets ALTER COLUMN id SET DEFAULT nextval('public.user_wallets_id_seq'::regclass);


--
-- TOC entry 4837 (class 2604 OID 32937)
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- TOC entry 4848 (class 2604 OID 32938)
-- Name: wallet_transfers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers ALTER COLUMN id SET DEFAULT nextval('public.wallet_transfers_id_seq'::regclass);


--
-- TOC entry 4858 (class 2604 OID 33050)
-- Name: withdraw_sessions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions ALTER COLUMN id SET DEFAULT nextval('public.withdraw_sessions_id_seq'::regclass);


--
-- TOC entry 5023 (class 2606 OID 33204)
-- Name: fee_wallet_swaps fee_wallet_swaps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fee_wallet_swaps
    ADD CONSTRAINT fee_wallet_swaps_pkey PRIMARY KEY (id);


--
-- TOC entry 5062 (class 2606 OID 33435)
-- Name: fund_allocation_batches fund_allocation_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_batches
    ADD CONSTRAINT fund_allocation_batches_pkey PRIMARY KEY (id);


--
-- TOC entry 5070 (class 2606 OID 33462)
-- Name: fund_allocation_legs fund_allocation_legs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_legs
    ADD CONSTRAINT fund_allocation_legs_pkey PRIMARY KEY (id);


--
-- TOC entry 5057 (class 2606 OID 33374)
-- Name: fund_bybit_accounts fund_bybit_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_bybit_accounts
    ADD CONSTRAINT fund_bybit_accounts_pkey PRIMARY KEY (id);


--
-- TOC entry 5010 (class 2606 OID 33133)
-- Name: fund_chart_daily fund_chart_daily_fund_ts_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_daily
    ADD CONSTRAINT fund_chart_daily_fund_ts_unique UNIQUE (fund_id, ts_utc);


--
-- TOC entry 5012 (class 2606 OID 33126)
-- Name: fund_chart_daily fund_chart_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_daily
    ADD CONSTRAINT fund_chart_daily_pkey PRIMARY KEY (id);


--
-- TOC entry 5015 (class 2606 OID 33148)
-- Name: fund_chart_minute fund_chart_minute_fund_ts_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_minute
    ADD CONSTRAINT fund_chart_minute_fund_ts_unique UNIQUE (fund_id, ts_utc);


--
-- TOC entry 5017 (class 2606 OID 33141)
-- Name: fund_chart_minute fund_chart_minute_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_minute
    ADD CONSTRAINT fund_chart_minute_pkey PRIMARY KEY (id);


--
-- TOC entry 5030 (class 2606 OID 33230)
-- Name: fund_nav_guard_events fund_nav_guard_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_events
    ADD CONSTRAINT fund_nav_guard_events_pkey PRIMARY KEY (id);


--
-- TOC entry 5026 (class 2606 OID 33214)
-- Name: fund_nav_guard_state fund_nav_guard_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_state
    ADD CONSTRAINT fund_nav_guard_state_pkey PRIMARY KEY (fund_id);


--
-- TOC entry 4944 (class 2606 OID 32940)
-- Name: fund_nav_minute fund_nav_minute_fund_ts_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute
    ADD CONSTRAINT fund_nav_minute_fund_ts_unique UNIQUE (fund_id, ts_utc);


--
-- TOC entry 4946 (class 2606 OID 32942)
-- Name: fund_nav_minute fund_nav_minute_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute
    ADD CONSTRAINT fund_nav_minute_pkey PRIMARY KEY (id);


--
-- TOC entry 5002 (class 2606 OID 33086)
-- Name: fund_orders fund_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders
    ADD CONSTRAINT fund_orders_pkey PRIMARY KEY (id);


--
-- TOC entry 5051 (class 2606 OID 33351)
-- Name: fund_runtime_state fund_runtime_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_runtime_state
    ADD CONSTRAINT fund_runtime_state_pkey PRIMARY KEY (fund_id);


--
-- TOC entry 5040 (class 2606 OID 33285)
-- Name: fund_settlement_batches fund_settlement_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_batches
    ADD CONSTRAINT fund_settlement_batches_pkey PRIMARY KEY (id);


--
-- TOC entry 5047 (class 2606 OID 33316)
-- Name: fund_settlement_transfers fund_settlement_transfers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_pkey PRIMARY KEY (id);


--
-- TOC entry 5035 (class 2606 OID 33256)
-- Name: fund_wallets fund_wallets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_wallets
    ADD CONSTRAINT fund_wallets_pkey PRIMARY KEY (id);


--
-- TOC entry 4948 (class 2606 OID 32944)
-- Name: funds funds_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funds
    ADD CONSTRAINT funds_code_key UNIQUE (code);


--
-- TOC entry 4950 (class 2606 OID 32946)
-- Name: funds funds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funds
    ADD CONSTRAINT funds_pkey PRIMARY KEY (id);


--
-- TOC entry 4952 (class 2606 OID 32948)
-- Name: password_reset_sessions password_reset_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_sessions
    ADD CONSTRAINT password_reset_sessions_pkey PRIMARY KEY (id);


--
-- TOC entry 4954 (class 2606 OID 32950)
-- Name: security_codes security_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_codes
    ADD CONSTRAINT security_codes_pkey PRIMARY KEY (id);


--
-- TOC entry 4957 (class 2606 OID 32952)
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);


--
-- TOC entry 5005 (class 2606 OID 33107)
-- Name: user_fund_position_stats user_fund_position_stats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats
    ADD CONSTRAINT user_fund_position_stats_pkey PRIMARY KEY (id);


--
-- TOC entry 5007 (class 2606 OID 33109)
-- Name: user_fund_position_stats user_fund_position_stats_user_fund_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats
    ADD CONSTRAINT user_fund_position_stats_user_fund_uq UNIQUE (user_id, fund_id);


--
-- TOC entry 4959 (class 2606 OID 32954)
-- Name: user_fund_positions user_fund_positions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_pkey PRIMARY KEY (id);


--
-- TOC entry 4961 (class 2606 OID 32956)
-- Name: user_fund_positions user_fund_positions_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_unique UNIQUE (user_id, fund_id);


--
-- TOC entry 4964 (class 2606 OID 32958)
-- Name: user_portfolio_daily user_portfolio_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily
    ADD CONSTRAINT user_portfolio_daily_pkey PRIMARY KEY (id);


--
-- TOC entry 4966 (class 2606 OID 32960)
-- Name: user_portfolio_daily user_portfolio_daily_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily
    ADD CONSTRAINT user_portfolio_daily_unique UNIQUE (user_id, date_utc);


--
-- TOC entry 5019 (class 2606 OID 33181)
-- Name: user_totp_recovery_codes user_totp_recovery_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_totp_recovery_codes
    ADD CONSTRAINT user_totp_recovery_codes_pkey PRIMARY KEY (id);


--
-- TOC entry 4971 (class 2606 OID 32962)
-- Name: user_wallets user_wallets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wallets
    ADD CONSTRAINT user_wallets_pkey PRIMARY KEY (id);


--
-- TOC entry 4976 (class 2606 OID 32964)
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- TOC entry 4978 (class 2606 OID 32966)
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- TOC entry 4984 (class 2606 OID 32968)
-- Name: wallet_transfers wallet_transfers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_pkey PRIMARY KEY (id);


--
-- TOC entry 4987 (class 2606 OID 32970)
-- Name: wallet_transfers wallet_transfers_tx_hash_log_index_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_tx_hash_log_index_uq UNIQUE (tx_hash, log_index);


--
-- TOC entry 4994 (class 2606 OID 33054)
-- Name: withdraw_sessions withdraw_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_pkey PRIMARY KEY (id);


--
-- TOC entry 4996 (class 2606 OID 33056)
-- Name: withdraw_sessions withdraw_sessions_token_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_token_key UNIQUE (token);


--
-- TOC entry 4991 (class 2606 OID 33044)
-- Name: worker_cursors worker_cursors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.worker_cursors
    ADD CONSTRAINT worker_cursors_pkey PRIMARY KEY (name);


--
-- TOC entry 5021 (class 1259 OID 33206)
-- Name: fee_wallet_swaps_one_success_per_day_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fee_wallet_swaps_one_success_per_day_idx ON public.fee_wallet_swaps USING btree (wallet_type, (((created_at AT TIME ZONE 'UTC'::text))::date)) WHERE ((status)::text = 'success'::text);


--
-- TOC entry 5024 (class 1259 OID 33205)
-- Name: fee_wallet_swaps_wallet_type_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fee_wallet_swaps_wallet_type_created_idx ON public.fee_wallet_swaps USING btree (wallet_type, created_at DESC);


--
-- TOC entry 5059 (class 1259 OID 33448)
-- Name: fund_allocation_batches_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_batches_created_idx ON public.fund_allocation_batches USING btree (created_at DESC);


--
-- TOC entry 5060 (class 1259 OID 33447)
-- Name: fund_allocation_batches_fund_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_batches_fund_status_idx ON public.fund_allocation_batches USING btree (fund_id, status);


--
-- TOC entry 5063 (class 1259 OID 33446)
-- Name: fund_allocation_batches_settlement_batch_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_allocation_batches_settlement_batch_uq ON public.fund_allocation_batches USING btree (settlement_batch_id);


--
-- TOC entry 5064 (class 1259 OID 33483)
-- Name: fund_allocation_legs_batch_leg_index_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_allocation_legs_batch_leg_index_uq ON public.fund_allocation_legs USING btree (allocation_batch_id, leg_index);


--
-- TOC entry 5065 (class 1259 OID 33484)
-- Name: fund_allocation_legs_batch_leg_key_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_allocation_legs_batch_leg_key_uq ON public.fund_allocation_legs USING btree (allocation_batch_id, leg_key);


--
-- TOC entry 5066 (class 1259 OID 33485)
-- Name: fund_allocation_legs_batch_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_batch_status_idx ON public.fund_allocation_legs USING btree (allocation_batch_id, status);


--
-- TOC entry 5067 (class 1259 OID 33486)
-- Name: fund_allocation_legs_fund_group_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_fund_group_idx ON public.fund_allocation_legs USING btree (fund_id, leg_group);


--
-- TOC entry 5068 (class 1259 OID 33488)
-- Name: fund_allocation_legs_order_link_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_order_link_idx ON public.fund_allocation_legs USING btree (order_link_id) WHERE (order_link_id IS NOT NULL);


--
-- TOC entry 5071 (class 1259 OID 33487)
-- Name: fund_allocation_legs_strategy_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_allocation_legs_strategy_idx ON public.fund_allocation_legs USING btree (strategy_id) WHERE (strategy_id IS NOT NULL);


--
-- TOC entry 5052 (class 1259 OID 33380)
-- Name: fund_bybit_accounts_active_fund_coin_chain_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_bybit_accounts_active_fund_coin_chain_uq ON public.fund_bybit_accounts USING btree (fund_id, coin, chain_type) WHERE (is_active = true);


--
-- TOC entry 5053 (class 1259 OID 33421)
-- Name: fund_bybit_accounts_api_key_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_bybit_accounts_api_key_active_idx ON public.fund_bybit_accounts USING btree (fund_id, api_key_is_active);


--
-- TOC entry 5054 (class 1259 OID 33383)
-- Name: fund_bybit_accounts_deposit_address_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_bybit_accounts_deposit_address_idx ON public.fund_bybit_accounts USING btree (deposit_address);


--
-- TOC entry 5055 (class 1259 OID 33382)
-- Name: fund_bybit_accounts_fund_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_bybit_accounts_fund_id_idx ON public.fund_bybit_accounts USING btree (fund_id);


--
-- TOC entry 5058 (class 1259 OID 33381)
-- Name: fund_bybit_accounts_sub_uid_coin_chain_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_bybit_accounts_sub_uid_coin_chain_uq ON public.fund_bybit_accounts USING btree (bybit_sub_uid, coin, chain_type) WHERE (is_active = true);


--
-- TOC entry 5008 (class 1259 OID 33134)
-- Name: fund_chart_daily_fund_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_chart_daily_fund_ts_idx ON public.fund_chart_daily USING btree (fund_id, ts_utc DESC);


--
-- TOC entry 5013 (class 1259 OID 33149)
-- Name: fund_chart_minute_fund_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_chart_minute_fund_ts_idx ON public.fund_chart_minute USING btree (fund_id, ts_utc DESC);


--
-- TOC entry 5027 (class 1259 OID 33237)
-- Name: fund_nav_guard_events_decision_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_nav_guard_events_decision_created_idx ON public.fund_nav_guard_events USING btree (decision, created_at DESC);


--
-- TOC entry 5028 (class 1259 OID 33236)
-- Name: fund_nav_guard_events_fund_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_nav_guard_events_fund_created_idx ON public.fund_nav_guard_events USING btree (fund_id, created_at DESC);


--
-- TOC entry 4942 (class 1259 OID 32971)
-- Name: fund_nav_minute_fund_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_nav_minute_fund_ts_idx ON public.fund_nav_minute USING btree (fund_id, ts_utc DESC);


--
-- TOC entry 4997 (class 1259 OID 33300)
-- Name: fund_orders_batch_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_batch_id_idx ON public.fund_orders USING btree (settlement_batch_id);


--
-- TOC entry 4998 (class 1259 OID 33392)
-- Name: fund_orders_batch_side_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_batch_side_status_idx ON public.fund_orders USING btree (settlement_batch_id, side, status);


--
-- TOC entry 4999 (class 1259 OID 33098)
-- Name: fund_orders_fund_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_fund_created_at_idx ON public.fund_orders USING btree (fund_id, created_at DESC);


--
-- TOC entry 5000 (class 1259 OID 33391)
-- Name: fund_orders_pending_cutoff_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_pending_cutoff_idx ON public.fund_orders USING btree (fund_id, status, created_at);


--
-- TOC entry 5003 (class 1259 OID 33097)
-- Name: fund_orders_user_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_user_created_at_idx ON public.fund_orders USING btree (user_id, created_at DESC);


--
-- TOC entry 5036 (class 1259 OID 33417)
-- Name: fund_settlement_batches_bybit_tx_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_batches_bybit_tx_idx ON public.fund_settlement_batches USING btree (bybit_deposit_tx_hash);


--
-- TOC entry 5037 (class 1259 OID 33291)
-- Name: fund_settlement_batches_fund_date_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_settlement_batches_fund_date_uq ON public.fund_settlement_batches USING btree (fund_id, settlement_date);


--
-- TOC entry 5038 (class 1259 OID 33418)
-- Name: fund_settlement_batches_internal_transfer_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_batches_internal_transfer_idx ON public.fund_settlement_batches USING btree (bybit_internal_transfer_id);


--
-- TOC entry 5041 (class 1259 OID 33395)
-- Name: fund_settlement_batches_positive_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_batches_positive_status_idx ON public.fund_settlement_batches USING btree (status, settlement_date);


--
-- TOC entry 5042 (class 1259 OID 33339)
-- Name: fund_settlement_transfers_batch_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_transfers_batch_idx ON public.fund_settlement_transfers USING btree (batch_id);


--
-- TOC entry 5043 (class 1259 OID 33413)
-- Name: fund_settlement_transfers_batch_type_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_transfers_batch_type_status_idx ON public.fund_settlement_transfers USING btree (batch_id, transfer_type, status);


--
-- TOC entry 5044 (class 1259 OID 33340)
-- Name: fund_settlement_transfers_order_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_transfers_order_idx ON public.fund_settlement_transfers USING btree (order_id);


--
-- TOC entry 5045 (class 1259 OID 33412)
-- Name: fund_settlement_transfers_order_type_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_settlement_transfers_order_type_uq ON public.fund_settlement_transfers USING btree (batch_id, order_id, transfer_type) WHERE (order_id IS NOT NULL);


--
-- TOC entry 5048 (class 1259 OID 33409)
-- Name: fund_settlement_transfers_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_transfers_status_idx ON public.fund_settlement_transfers USING btree (status);


--
-- TOC entry 5049 (class 1259 OID 33342)
-- Name: fund_settlement_transfers_tx_hash_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_settlement_transfers_tx_hash_uq ON public.fund_settlement_transfers USING btree (tx_hash) WHERE (tx_hash IS NOT NULL);


--
-- TOC entry 5031 (class 1259 OID 33263)
-- Name: fund_wallets_active_settlement_fund_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_wallets_active_settlement_fund_uq ON public.fund_wallets USING btree (fund_id, blockchain, wallet_type) WHERE (is_active = true);


--
-- TOC entry 5032 (class 1259 OID 33262)
-- Name: fund_wallets_blockchain_address_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_wallets_blockchain_address_uq ON public.fund_wallets USING btree (blockchain, address);


--
-- TOC entry 5033 (class 1259 OID 33264)
-- Name: fund_wallets_fund_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_wallets_fund_id_idx ON public.fund_wallets USING btree (fund_id);


--
-- TOC entry 4955 (class 1259 OID 32972)
-- Name: idx_sessions_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_expires_at ON public.sessions USING btree (expires_at);


--
-- TOC entry 4973 (class 1259 OID 32973)
-- Name: idx_users_compliance_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_compliance_status ON public.users USING btree (compliance_status);


--
-- TOC entry 4979 (class 1259 OID 32974)
-- Name: idx_wallet_transfers_compliance_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_compliance_status ON public.wallet_transfers USING btree (compliance_status);


--
-- TOC entry 4980 (class 1259 OID 32975)
-- Name: idx_wallet_transfers_need_compliance; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_need_compliance ON public.wallet_transfers USING btree (status, compliance_status);


--
-- TOC entry 4981 (class 1259 OID 33073)
-- Name: idx_wallet_transfers_user_type_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_user_type_time ON public.wallet_transfers USING btree (user_id, type, detected_at DESC);


--
-- TOC entry 4982 (class 1259 OID 33072)
-- Name: idx_wallet_transfers_withdraw_processing; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_withdraw_processing ON public.wallet_transfers USING btree (type, status) WHERE (((type)::text = 'withdraw'::text) AND ((status)::text = 'processing'::text));


--
-- TOC entry 4992 (class 1259 OID 33067)
-- Name: idx_withdraw_sessions_user_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_withdraw_sessions_user_expires ON public.withdraw_sessions USING btree (user_id, expires_at DESC);


--
-- TOC entry 4962 (class 1259 OID 32976)
-- Name: user_fund_positions_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_fund_positions_user_idx ON public.user_fund_positions USING btree (user_id);


--
-- TOC entry 4967 (class 1259 OID 32977)
-- Name: user_portfolio_daily_user_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_portfolio_daily_user_date_idx ON public.user_portfolio_daily USING btree (user_id, date_utc DESC);


--
-- TOC entry 5020 (class 1259 OID 33187)
-- Name: user_totp_recovery_codes_user_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_totp_recovery_codes_user_active_idx ON public.user_totp_recovery_codes USING btree (user_id, is_used);


--
-- TOC entry 4968 (class 1259 OID 32978)
-- Name: user_wallets_blockchain_address_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX user_wallets_blockchain_address_uq ON public.user_wallets USING btree (blockchain, address);


--
-- TOC entry 4969 (class 1259 OID 33069)
-- Name: user_wallets_one_active_bsc; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX user_wallets_one_active_bsc ON public.user_wallets USING btree (user_id) WHERE (((blockchain)::text = 'BSC'::text) AND (is_active = true));


--
-- TOC entry 4972 (class 1259 OID 32980)
-- Name: user_wallets_user_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_wallets_user_id_idx ON public.user_wallets USING btree (user_id);


--
-- TOC entry 4974 (class 1259 OID 32981)
-- Name: users_backup_email_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX users_backup_email_idx ON public.users USING btree (backup_email);


--
-- TOC entry 4985 (class 1259 OID 32982)
-- Name: wallet_transfers_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX wallet_transfers_status_idx ON public.wallet_transfers USING btree (status);


--
-- TOC entry 4988 (class 1259 OID 32983)
-- Name: wallet_transfers_user_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX wallet_transfers_user_time_idx ON public.wallet_transfers USING btree (user_id, tx_time DESC);


--
-- TOC entry 4989 (class 1259 OID 32984)
-- Name: wallet_transfers_wallet_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX wallet_transfers_wallet_time_idx ON public.wallet_transfers USING btree (wallet_id, tx_time DESC);


--
-- TOC entry 5103 (class 2606 OID 33441)
-- Name: fund_allocation_batches fund_allocation_batches_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_batches
    ADD CONSTRAINT fund_allocation_batches_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5104 (class 2606 OID 33436)
-- Name: fund_allocation_batches fund_allocation_batches_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_batches
    ADD CONSTRAINT fund_allocation_batches_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5105 (class 2606 OID 33463)
-- Name: fund_allocation_legs fund_allocation_legs_allocation_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_legs
    ADD CONSTRAINT fund_allocation_legs_allocation_batch_id_fkey FOREIGN KEY (allocation_batch_id) REFERENCES public.fund_allocation_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5106 (class 2606 OID 33473)
-- Name: fund_allocation_legs fund_allocation_legs_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_legs
    ADD CONSTRAINT fund_allocation_legs_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5107 (class 2606 OID 33478)
-- Name: fund_allocation_legs fund_allocation_legs_parent_leg_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_legs
    ADD CONSTRAINT fund_allocation_legs_parent_leg_id_fkey FOREIGN KEY (parent_leg_id) REFERENCES public.fund_allocation_legs(id) ON DELETE SET NULL;


--
-- TOC entry 5108 (class 2606 OID 33468)
-- Name: fund_allocation_legs fund_allocation_legs_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_allocation_legs
    ADD CONSTRAINT fund_allocation_legs_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5102 (class 2606 OID 33375)
-- Name: fund_bybit_accounts fund_bybit_accounts_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_bybit_accounts
    ADD CONSTRAINT fund_bybit_accounts_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5089 (class 2606 OID 33127)
-- Name: fund_chart_daily fund_chart_daily_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_daily
    ADD CONSTRAINT fund_chart_daily_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5090 (class 2606 OID 33142)
-- Name: fund_chart_minute fund_chart_minute_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_minute
    ADD CONSTRAINT fund_chart_minute_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5093 (class 2606 OID 33231)
-- Name: fund_nav_guard_events fund_nav_guard_events_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_events
    ADD CONSTRAINT fund_nav_guard_events_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5092 (class 2606 OID 33215)
-- Name: fund_nav_guard_state fund_nav_guard_state_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_state
    ADD CONSTRAINT fund_nav_guard_state_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5072 (class 2606 OID 32985)
-- Name: fund_nav_minute fund_nav_minute_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute
    ADD CONSTRAINT fund_nav_minute_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5084 (class 2606 OID 33092)
-- Name: fund_orders fund_orders_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders
    ADD CONSTRAINT fund_orders_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5085 (class 2606 OID 33293)
-- Name: fund_orders fund_orders_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders
    ADD CONSTRAINT fund_orders_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE SET NULL;


--
-- TOC entry 5086 (class 2606 OID 33087)
-- Name: fund_orders fund_orders_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders
    ADD CONSTRAINT fund_orders_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5100 (class 2606 OID 33352)
-- Name: fund_runtime_state fund_runtime_state_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_runtime_state
    ADD CONSTRAINT fund_runtime_state_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5101 (class 2606 OID 33357)
-- Name: fund_runtime_state fund_runtime_state_pricing_lock_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_runtime_state
    ADD CONSTRAINT fund_runtime_state_pricing_lock_batch_id_fkey FOREIGN KEY (pricing_lock_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE SET NULL;


--
-- TOC entry 5095 (class 2606 OID 33286)
-- Name: fund_settlement_batches fund_settlement_batches_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_batches
    ADD CONSTRAINT fund_settlement_batches_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5096 (class 2606 OID 33317)
-- Name: fund_settlement_transfers fund_settlement_transfers_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5097 (class 2606 OID 33327)
-- Name: fund_settlement_transfers fund_settlement_transfers_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5098 (class 2606 OID 33322)
-- Name: fund_settlement_transfers fund_settlement_transfers_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.fund_orders(id) ON DELETE SET NULL;


--
-- TOC entry 5099 (class 2606 OID 33332)
-- Name: fund_settlement_transfers fund_settlement_transfers_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- TOC entry 5094 (class 2606 OID 33257)
-- Name: fund_wallets fund_wallets_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_wallets
    ADD CONSTRAINT fund_wallets_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5073 (class 2606 OID 32990)
-- Name: password_reset_sessions password_reset_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_sessions
    ADD CONSTRAINT password_reset_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5074 (class 2606 OID 32995)
-- Name: security_codes security_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_codes
    ADD CONSTRAINT security_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5075 (class 2606 OID 33000)
-- Name: sessions sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5087 (class 2606 OID 33115)
-- Name: user_fund_position_stats user_fund_position_stats_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats
    ADD CONSTRAINT user_fund_position_stats_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5088 (class 2606 OID 33110)
-- Name: user_fund_position_stats user_fund_position_stats_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats
    ADD CONSTRAINT user_fund_position_stats_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5076 (class 2606 OID 33005)
-- Name: user_fund_positions user_fund_positions_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5077 (class 2606 OID 33010)
-- Name: user_fund_positions user_fund_positions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5078 (class 2606 OID 33015)
-- Name: user_portfolio_daily user_portfolio_daily_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily
    ADD CONSTRAINT user_portfolio_daily_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5091 (class 2606 OID 33182)
-- Name: user_totp_recovery_codes user_totp_recovery_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_totp_recovery_codes
    ADD CONSTRAINT user_totp_recovery_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5079 (class 2606 OID 33020)
-- Name: user_wallets user_wallets_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wallets
    ADD CONSTRAINT user_wallets_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5080 (class 2606 OID 33025)
-- Name: wallet_transfers wallet_transfers_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5081 (class 2606 OID 33030)
-- Name: wallet_transfers wallet_transfers_wallet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_wallet_id_fkey FOREIGN KEY (wallet_id) REFERENCES public.user_wallets(id) ON DELETE CASCADE;


--
-- TOC entry 5082 (class 2606 OID 33057)
-- Name: withdraw_sessions withdraw_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5083 (class 2606 OID 33062)
-- Name: withdraw_sessions withdraw_sessions_wallet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_wallet_id_fkey FOREIGN KEY (wallet_id) REFERENCES public.user_wallets(id) ON DELETE CASCADE;


-- Completed on 2026-05-27 16:49:30

--
-- PostgreSQL database dump complete
--

\unrestrict Wg2lNBQIt94OFX8QaVValpWtKnIGJYJTkZvYoM3jZ2TWgTPoO9D84xEvhSCHo1Q

