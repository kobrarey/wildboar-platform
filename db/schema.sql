--
-- PostgreSQL database dump
--

\restrict 1qa9I1U9AEJaiwXinTxgmJtQ3mnIGdQQhu6fX3wjqsikIaDSpwLG3gAoMPfh7ZA

-- Dumped from database version 16.11
-- Dumped by pg_dump version 16.11

-- Started on 2026-05-22 14:51:24

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
-- TOC entry 5234 (class 0 OID 0)
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
-- TOC entry 5235 (class 0 OID 0)
-- Dependencies: 246
-- Name: fee_wallet_swaps_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fee_wallet_swaps_id_seq OWNED BY public.fee_wallet_swaps.id;


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
-- TOC entry 5236 (class 0 OID 0)
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
-- TOC entry 5237 (class 0 OID 0)
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
-- TOC entry 5238 (class 0 OID 0)
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
-- TOC entry 5239 (class 0 OID 0)
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
    status character varying(16) DEFAULT 'pending'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    executed_at timestamp with time zone,
    settlement_batch_id bigint,
    reserved_at timestamp with time zone,
    settlement_locked_at timestamp with time zone,
    collection_confirmed_at timestamp with time zone,
    error text,
    CONSTRAINT fund_orders_side_check CHECK (((side)::text = ANY ((ARRAY['buy'::character varying, 'redeem'::character varying])::text[]))),
    CONSTRAINT fund_orders_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'settling'::character varying, 'buy_collecting'::character varying, 'buy_collected'::character varying, 'awaiting_positive_net_execution'::character varying, 'awaiting_negative_net_execution'::character varying, 'processing'::character varying, 'success'::character varying, 'failed'::character varying, 'cancelled'::character varying])::text[])))
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
-- TOC entry 5240 (class 0 OID 0)
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
    CONSTRAINT fund_settlement_batches_status_check CHECK (((status)::text = ANY ((ARRAY['created'::character varying, 'pricing_locked'::character varying, 'price_fixed'::character varying, 'gas_checking'::character varying, 'gas_ready'::character varying, 'collecting_buy_usdt'::character varying, 'buy_usdt_collected'::character varying, 'awaiting_positive_net_execution'::character varying, 'awaiting_negative_net_execution'::character varying, 'no_orders'::character varying, 'failed'::character varying])::text[])))
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
-- TOC entry 5241 (class 0 OID 0)
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
    status character varying(32) DEFAULT 'pending'::character varying NOT NULL,
    attempts integer DEFAULT 0 NOT NULL,
    error text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    sent_at timestamp with time zone,
    confirmed_at timestamp with time zone,
    CONSTRAINT fund_settlement_transfers_status_check CHECK (((status)::text = ANY ((ARRAY['pending'::character varying, 'processing'::character varying, 'sent'::character varying, 'confirmed'::character varying, 'failed'::character varying, 'skipped'::character varying])::text[]))),
    CONSTRAINT fund_settlement_transfers_transfer_type_check CHECK (((transfer_type)::text = ANY ((ARRAY['settlement_wallet_gas_topup'::character varying, 'user_wallet_gas_topup'::character varying, 'user_buy_usdt_to_settlement'::character varying])::text[])))
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
-- TOC entry 5242 (class 0 OID 0)
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
-- TOC entry 5243 (class 0 OID 0)
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
-- TOC entry 5244 (class 0 OID 0)
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
-- TOC entry 5245 (class 0 OID 0)
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
-- TOC entry 5246 (class 0 OID 0)
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
-- TOC entry 5247 (class 0 OID 0)
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
-- TOC entry 5248 (class 0 OID 0)
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
-- TOC entry 5249 (class 0 OID 0)
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
-- TOC entry 5250 (class 0 OID 0)
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
-- TOC entry 5251 (class 0 OID 0)
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
-- TOC entry 5252 (class 0 OID 0)
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
-- TOC entry 5253 (class 0 OID 0)
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
-- TOC entry 4857 (class 2604 OID 33193)
-- Name: fee_wallet_swaps id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fee_wallet_swaps ALTER COLUMN id SET DEFAULT nextval('public.fee_wallet_swaps_id_seq'::regclass);


--
-- TOC entry 4852 (class 2604 OID 33124)
-- Name: fund_chart_daily id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_daily ALTER COLUMN id SET DEFAULT nextval('public.fund_chart_daily_id_seq'::regclass);


--
-- TOC entry 4853 (class 2604 OID 33139)
-- Name: fund_chart_minute id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_minute ALTER COLUMN id SET DEFAULT nextval('public.fund_chart_minute_id_seq'::regclass);


--
-- TOC entry 4865 (class 2604 OID 33224)
-- Name: fund_nav_guard_events id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_events ALTER COLUMN id SET DEFAULT nextval('public.fund_nav_guard_events_id_seq'::regclass);


--
-- TOC entry 4798 (class 2604 OID 32931)
-- Name: fund_nav_minute id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute ALTER COLUMN id SET DEFAULT nextval('public.fund_nav_minute_id_seq'::regclass);


--
-- TOC entry 4846 (class 2604 OID 33081)
-- Name: fund_orders id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders ALTER COLUMN id SET DEFAULT nextval('public.fund_orders_id_seq'::regclass);


--
-- TOC entry 4872 (class 2604 OID 33271)
-- Name: fund_settlement_batches id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_batches ALTER COLUMN id SET DEFAULT nextval('public.fund_settlement_batches_id_seq'::regclass);


--
-- TOC entry 4883 (class 2604 OID 33308)
-- Name: fund_settlement_transfers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers ALTER COLUMN id SET DEFAULT nextval('public.fund_settlement_transfers_id_seq'::regclass);


--
-- TOC entry 4867 (class 2604 OID 33248)
-- Name: fund_wallets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_wallets ALTER COLUMN id SET DEFAULT nextval('public.fund_wallets_id_seq'::regclass);


--
-- TOC entry 4799 (class 2604 OID 32932)
-- Name: funds id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funds ALTER COLUMN id SET DEFAULT nextval('public.funds_id_seq'::regclass);


--
-- TOC entry 4805 (class 2604 OID 32933)
-- Name: security_codes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_codes ALTER COLUMN id SET DEFAULT nextval('public.security_codes_id_seq'::regclass);


--
-- TOC entry 4849 (class 2604 OID 33103)
-- Name: user_fund_position_stats id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats ALTER COLUMN id SET DEFAULT nextval('public.user_fund_position_stats_id_seq'::regclass);


--
-- TOC entry 4810 (class 2604 OID 32934)
-- Name: user_fund_positions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions ALTER COLUMN id SET DEFAULT nextval('public.user_fund_positions_id_seq'::regclass);


--
-- TOC entry 4813 (class 2604 OID 32935)
-- Name: user_portfolio_daily id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily ALTER COLUMN id SET DEFAULT nextval('public.user_portfolio_daily_id_seq'::regclass);


--
-- TOC entry 4854 (class 2604 OID 33177)
-- Name: user_totp_recovery_codes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_totp_recovery_codes ALTER COLUMN id SET DEFAULT nextval('public.user_totp_recovery_codes_id_seq'::regclass);


--
-- TOC entry 4815 (class 2604 OID 32936)
-- Name: user_wallets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wallets ALTER COLUMN id SET DEFAULT nextval('public.user_wallets_id_seq'::regclass);


--
-- TOC entry 4822 (class 2604 OID 32937)
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- TOC entry 4833 (class 2604 OID 32938)
-- Name: wallet_transfers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers ALTER COLUMN id SET DEFAULT nextval('public.wallet_transfers_id_seq'::regclass);


--
-- TOC entry 4843 (class 2604 OID 33050)
-- Name: withdraw_sessions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions ALTER COLUMN id SET DEFAULT nextval('public.withdraw_sessions_id_seq'::regclass);


--
-- TOC entry 4988 (class 2606 OID 33204)
-- Name: fee_wallet_swaps fee_wallet_swaps_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fee_wallet_swaps
    ADD CONSTRAINT fee_wallet_swaps_pkey PRIMARY KEY (id);


--
-- TOC entry 4975 (class 2606 OID 33133)
-- Name: fund_chart_daily fund_chart_daily_fund_ts_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_daily
    ADD CONSTRAINT fund_chart_daily_fund_ts_unique UNIQUE (fund_id, ts_utc);


--
-- TOC entry 4977 (class 2606 OID 33126)
-- Name: fund_chart_daily fund_chart_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_daily
    ADD CONSTRAINT fund_chart_daily_pkey PRIMARY KEY (id);


--
-- TOC entry 4980 (class 2606 OID 33148)
-- Name: fund_chart_minute fund_chart_minute_fund_ts_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_minute
    ADD CONSTRAINT fund_chart_minute_fund_ts_unique UNIQUE (fund_id, ts_utc);


--
-- TOC entry 4982 (class 2606 OID 33141)
-- Name: fund_chart_minute fund_chart_minute_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_minute
    ADD CONSTRAINT fund_chart_minute_pkey PRIMARY KEY (id);


--
-- TOC entry 4995 (class 2606 OID 33230)
-- Name: fund_nav_guard_events fund_nav_guard_events_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_events
    ADD CONSTRAINT fund_nav_guard_events_pkey PRIMARY KEY (id);


--
-- TOC entry 4991 (class 2606 OID 33214)
-- Name: fund_nav_guard_state fund_nav_guard_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_state
    ADD CONSTRAINT fund_nav_guard_state_pkey PRIMARY KEY (fund_id);


--
-- TOC entry 4910 (class 2606 OID 32940)
-- Name: fund_nav_minute fund_nav_minute_fund_ts_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute
    ADD CONSTRAINT fund_nav_minute_fund_ts_unique UNIQUE (fund_id, ts_utc);


--
-- TOC entry 4912 (class 2606 OID 32942)
-- Name: fund_nav_minute fund_nav_minute_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute
    ADD CONSTRAINT fund_nav_minute_pkey PRIMARY KEY (id);


--
-- TOC entry 4967 (class 2606 OID 33086)
-- Name: fund_orders fund_orders_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders
    ADD CONSTRAINT fund_orders_pkey PRIMARY KEY (id);


--
-- TOC entry 5012 (class 2606 OID 33351)
-- Name: fund_runtime_state fund_runtime_state_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_runtime_state
    ADD CONSTRAINT fund_runtime_state_pkey PRIMARY KEY (fund_id);


--
-- TOC entry 5003 (class 2606 OID 33285)
-- Name: fund_settlement_batches fund_settlement_batches_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_batches
    ADD CONSTRAINT fund_settlement_batches_pkey PRIMARY KEY (id);


--
-- TOC entry 5008 (class 2606 OID 33316)
-- Name: fund_settlement_transfers fund_settlement_transfers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_pkey PRIMARY KEY (id);


--
-- TOC entry 5000 (class 2606 OID 33256)
-- Name: fund_wallets fund_wallets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_wallets
    ADD CONSTRAINT fund_wallets_pkey PRIMARY KEY (id);


--
-- TOC entry 4914 (class 2606 OID 32944)
-- Name: funds funds_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funds
    ADD CONSTRAINT funds_code_key UNIQUE (code);


--
-- TOC entry 4916 (class 2606 OID 32946)
-- Name: funds funds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funds
    ADD CONSTRAINT funds_pkey PRIMARY KEY (id);


--
-- TOC entry 4918 (class 2606 OID 32948)
-- Name: password_reset_sessions password_reset_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_sessions
    ADD CONSTRAINT password_reset_sessions_pkey PRIMARY KEY (id);


--
-- TOC entry 4920 (class 2606 OID 32950)
-- Name: security_codes security_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_codes
    ADD CONSTRAINT security_codes_pkey PRIMARY KEY (id);


--
-- TOC entry 4923 (class 2606 OID 32952)
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);


--
-- TOC entry 4970 (class 2606 OID 33107)
-- Name: user_fund_position_stats user_fund_position_stats_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats
    ADD CONSTRAINT user_fund_position_stats_pkey PRIMARY KEY (id);


--
-- TOC entry 4972 (class 2606 OID 33109)
-- Name: user_fund_position_stats user_fund_position_stats_user_fund_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats
    ADD CONSTRAINT user_fund_position_stats_user_fund_uq UNIQUE (user_id, fund_id);


--
-- TOC entry 4925 (class 2606 OID 32954)
-- Name: user_fund_positions user_fund_positions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_pkey PRIMARY KEY (id);


--
-- TOC entry 4927 (class 2606 OID 32956)
-- Name: user_fund_positions user_fund_positions_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_unique UNIQUE (user_id, fund_id);


--
-- TOC entry 4930 (class 2606 OID 32958)
-- Name: user_portfolio_daily user_portfolio_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily
    ADD CONSTRAINT user_portfolio_daily_pkey PRIMARY KEY (id);


--
-- TOC entry 4932 (class 2606 OID 32960)
-- Name: user_portfolio_daily user_portfolio_daily_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily
    ADD CONSTRAINT user_portfolio_daily_unique UNIQUE (user_id, date_utc);


--
-- TOC entry 4984 (class 2606 OID 33181)
-- Name: user_totp_recovery_codes user_totp_recovery_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_totp_recovery_codes
    ADD CONSTRAINT user_totp_recovery_codes_pkey PRIMARY KEY (id);


--
-- TOC entry 4937 (class 2606 OID 32962)
-- Name: user_wallets user_wallets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wallets
    ADD CONSTRAINT user_wallets_pkey PRIMARY KEY (id);


--
-- TOC entry 4942 (class 2606 OID 32964)
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- TOC entry 4944 (class 2606 OID 32966)
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- TOC entry 4950 (class 2606 OID 32968)
-- Name: wallet_transfers wallet_transfers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_pkey PRIMARY KEY (id);


--
-- TOC entry 4953 (class 2606 OID 32970)
-- Name: wallet_transfers wallet_transfers_tx_hash_log_index_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_tx_hash_log_index_uq UNIQUE (tx_hash, log_index);


--
-- TOC entry 4960 (class 2606 OID 33054)
-- Name: withdraw_sessions withdraw_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_pkey PRIMARY KEY (id);


--
-- TOC entry 4962 (class 2606 OID 33056)
-- Name: withdraw_sessions withdraw_sessions_token_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_token_key UNIQUE (token);


--
-- TOC entry 4957 (class 2606 OID 33044)
-- Name: worker_cursors worker_cursors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.worker_cursors
    ADD CONSTRAINT worker_cursors_pkey PRIMARY KEY (name);


--
-- TOC entry 4986 (class 1259 OID 33206)
-- Name: fee_wallet_swaps_one_success_per_day_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fee_wallet_swaps_one_success_per_day_idx ON public.fee_wallet_swaps USING btree (wallet_type, (((created_at AT TIME ZONE 'UTC'::text))::date)) WHERE ((status)::text = 'success'::text);


--
-- TOC entry 4989 (class 1259 OID 33205)
-- Name: fee_wallet_swaps_wallet_type_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fee_wallet_swaps_wallet_type_created_idx ON public.fee_wallet_swaps USING btree (wallet_type, created_at DESC);


--
-- TOC entry 4973 (class 1259 OID 33134)
-- Name: fund_chart_daily_fund_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_chart_daily_fund_ts_idx ON public.fund_chart_daily USING btree (fund_id, ts_utc DESC);


--
-- TOC entry 4978 (class 1259 OID 33149)
-- Name: fund_chart_minute_fund_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_chart_minute_fund_ts_idx ON public.fund_chart_minute USING btree (fund_id, ts_utc DESC);


--
-- TOC entry 4992 (class 1259 OID 33237)
-- Name: fund_nav_guard_events_decision_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_nav_guard_events_decision_created_idx ON public.fund_nav_guard_events USING btree (decision, created_at DESC);


--
-- TOC entry 4993 (class 1259 OID 33236)
-- Name: fund_nav_guard_events_fund_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_nav_guard_events_fund_created_idx ON public.fund_nav_guard_events USING btree (fund_id, created_at DESC);


--
-- TOC entry 4908 (class 1259 OID 32971)
-- Name: fund_nav_minute_fund_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_nav_minute_fund_ts_idx ON public.fund_nav_minute USING btree (fund_id, ts_utc DESC);


--
-- TOC entry 4963 (class 1259 OID 33300)
-- Name: fund_orders_batch_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_batch_id_idx ON public.fund_orders USING btree (settlement_batch_id);


--
-- TOC entry 4964 (class 1259 OID 33098)
-- Name: fund_orders_fund_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_fund_created_at_idx ON public.fund_orders USING btree (fund_id, created_at DESC);


--
-- TOC entry 4965 (class 1259 OID 33301)
-- Name: fund_orders_pending_cutoff_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_pending_cutoff_idx ON public.fund_orders USING btree (fund_id, status, created_at);


--
-- TOC entry 4968 (class 1259 OID 33097)
-- Name: fund_orders_user_created_at_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_orders_user_created_at_idx ON public.fund_orders USING btree (user_id, created_at DESC);


--
-- TOC entry 5001 (class 1259 OID 33291)
-- Name: fund_settlement_batches_fund_date_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_settlement_batches_fund_date_uq ON public.fund_settlement_batches USING btree (fund_id, settlement_date);


--
-- TOC entry 5004 (class 1259 OID 33339)
-- Name: fund_settlement_transfers_batch_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_transfers_batch_idx ON public.fund_settlement_transfers USING btree (batch_id);


--
-- TOC entry 5005 (class 1259 OID 33340)
-- Name: fund_settlement_transfers_order_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_transfers_order_idx ON public.fund_settlement_transfers USING btree (order_id);


--
-- TOC entry 5006 (class 1259 OID 33343)
-- Name: fund_settlement_transfers_order_type_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_settlement_transfers_order_type_uq ON public.fund_settlement_transfers USING btree (batch_id, order_id, transfer_type) WHERE (order_id IS NOT NULL);


--
-- TOC entry 5009 (class 1259 OID 33341)
-- Name: fund_settlement_transfers_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_settlement_transfers_status_idx ON public.fund_settlement_transfers USING btree (status);


--
-- TOC entry 5010 (class 1259 OID 33342)
-- Name: fund_settlement_transfers_tx_hash_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_settlement_transfers_tx_hash_uq ON public.fund_settlement_transfers USING btree (tx_hash) WHERE (tx_hash IS NOT NULL);


--
-- TOC entry 4996 (class 1259 OID 33263)
-- Name: fund_wallets_active_settlement_fund_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_wallets_active_settlement_fund_uq ON public.fund_wallets USING btree (fund_id, blockchain, wallet_type) WHERE (is_active = true);


--
-- TOC entry 4997 (class 1259 OID 33262)
-- Name: fund_wallets_blockchain_address_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX fund_wallets_blockchain_address_uq ON public.fund_wallets USING btree (blockchain, address);


--
-- TOC entry 4998 (class 1259 OID 33264)
-- Name: fund_wallets_fund_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_wallets_fund_id_idx ON public.fund_wallets USING btree (fund_id);


--
-- TOC entry 4921 (class 1259 OID 32972)
-- Name: idx_sessions_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_expires_at ON public.sessions USING btree (expires_at);


--
-- TOC entry 4939 (class 1259 OID 32973)
-- Name: idx_users_compliance_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_compliance_status ON public.users USING btree (compliance_status);


--
-- TOC entry 4945 (class 1259 OID 32974)
-- Name: idx_wallet_transfers_compliance_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_compliance_status ON public.wallet_transfers USING btree (compliance_status);


--
-- TOC entry 4946 (class 1259 OID 32975)
-- Name: idx_wallet_transfers_need_compliance; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_need_compliance ON public.wallet_transfers USING btree (status, compliance_status);


--
-- TOC entry 4947 (class 1259 OID 33073)
-- Name: idx_wallet_transfers_user_type_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_user_type_time ON public.wallet_transfers USING btree (user_id, type, detected_at DESC);


--
-- TOC entry 4948 (class 1259 OID 33072)
-- Name: idx_wallet_transfers_withdraw_processing; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_withdraw_processing ON public.wallet_transfers USING btree (type, status) WHERE (((type)::text = 'withdraw'::text) AND ((status)::text = 'processing'::text));


--
-- TOC entry 4958 (class 1259 OID 33067)
-- Name: idx_withdraw_sessions_user_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_withdraw_sessions_user_expires ON public.withdraw_sessions USING btree (user_id, expires_at DESC);


--
-- TOC entry 4928 (class 1259 OID 32976)
-- Name: user_fund_positions_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_fund_positions_user_idx ON public.user_fund_positions USING btree (user_id);


--
-- TOC entry 4933 (class 1259 OID 32977)
-- Name: user_portfolio_daily_user_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_portfolio_daily_user_date_idx ON public.user_portfolio_daily USING btree (user_id, date_utc DESC);


--
-- TOC entry 4985 (class 1259 OID 33187)
-- Name: user_totp_recovery_codes_user_active_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_totp_recovery_codes_user_active_idx ON public.user_totp_recovery_codes USING btree (user_id, is_used);


--
-- TOC entry 4934 (class 1259 OID 32978)
-- Name: user_wallets_blockchain_address_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX user_wallets_blockchain_address_uq ON public.user_wallets USING btree (blockchain, address);


--
-- TOC entry 4935 (class 1259 OID 33069)
-- Name: user_wallets_one_active_bsc; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX user_wallets_one_active_bsc ON public.user_wallets USING btree (user_id) WHERE (((blockchain)::text = 'BSC'::text) AND (is_active = true));


--
-- TOC entry 4938 (class 1259 OID 32980)
-- Name: user_wallets_user_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_wallets_user_id_idx ON public.user_wallets USING btree (user_id);


--
-- TOC entry 4940 (class 1259 OID 32981)
-- Name: users_backup_email_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX users_backup_email_idx ON public.users USING btree (backup_email);


--
-- TOC entry 4951 (class 1259 OID 32982)
-- Name: wallet_transfers_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX wallet_transfers_status_idx ON public.wallet_transfers USING btree (status);


--
-- TOC entry 4954 (class 1259 OID 32983)
-- Name: wallet_transfers_user_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX wallet_transfers_user_time_idx ON public.wallet_transfers USING btree (user_id, tx_time DESC);


--
-- TOC entry 4955 (class 1259 OID 32984)
-- Name: wallet_transfers_wallet_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX wallet_transfers_wallet_time_idx ON public.wallet_transfers USING btree (wallet_id, tx_time DESC);


--
-- TOC entry 5030 (class 2606 OID 33127)
-- Name: fund_chart_daily fund_chart_daily_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_daily
    ADD CONSTRAINT fund_chart_daily_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5031 (class 2606 OID 33142)
-- Name: fund_chart_minute fund_chart_minute_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_chart_minute
    ADD CONSTRAINT fund_chart_minute_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5034 (class 2606 OID 33231)
-- Name: fund_nav_guard_events fund_nav_guard_events_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_events
    ADD CONSTRAINT fund_nav_guard_events_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5033 (class 2606 OID 33215)
-- Name: fund_nav_guard_state fund_nav_guard_state_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_guard_state
    ADD CONSTRAINT fund_nav_guard_state_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5013 (class 2606 OID 32985)
-- Name: fund_nav_minute fund_nav_minute_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute
    ADD CONSTRAINT fund_nav_minute_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5025 (class 2606 OID 33092)
-- Name: fund_orders fund_orders_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders
    ADD CONSTRAINT fund_orders_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5026 (class 2606 OID 33293)
-- Name: fund_orders fund_orders_settlement_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders
    ADD CONSTRAINT fund_orders_settlement_batch_id_fkey FOREIGN KEY (settlement_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE SET NULL;


--
-- TOC entry 5027 (class 2606 OID 33087)
-- Name: fund_orders fund_orders_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_orders
    ADD CONSTRAINT fund_orders_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5041 (class 2606 OID 33352)
-- Name: fund_runtime_state fund_runtime_state_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_runtime_state
    ADD CONSTRAINT fund_runtime_state_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5042 (class 2606 OID 33357)
-- Name: fund_runtime_state fund_runtime_state_pricing_lock_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_runtime_state
    ADD CONSTRAINT fund_runtime_state_pricing_lock_batch_id_fkey FOREIGN KEY (pricing_lock_batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE SET NULL;


--
-- TOC entry 5036 (class 2606 OID 33286)
-- Name: fund_settlement_batches fund_settlement_batches_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_batches
    ADD CONSTRAINT fund_settlement_batches_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5037 (class 2606 OID 33317)
-- Name: fund_settlement_transfers fund_settlement_transfers_batch_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_batch_id_fkey FOREIGN KEY (batch_id) REFERENCES public.fund_settlement_batches(id) ON DELETE CASCADE;


--
-- TOC entry 5038 (class 2606 OID 33327)
-- Name: fund_settlement_transfers fund_settlement_transfers_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5039 (class 2606 OID 33322)
-- Name: fund_settlement_transfers fund_settlement_transfers_order_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_order_id_fkey FOREIGN KEY (order_id) REFERENCES public.fund_orders(id) ON DELETE SET NULL;


--
-- TOC entry 5040 (class 2606 OID 33332)
-- Name: fund_settlement_transfers fund_settlement_transfers_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_settlement_transfers
    ADD CONSTRAINT fund_settlement_transfers_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- TOC entry 5035 (class 2606 OID 33257)
-- Name: fund_wallets fund_wallets_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_wallets
    ADD CONSTRAINT fund_wallets_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5014 (class 2606 OID 32990)
-- Name: password_reset_sessions password_reset_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_sessions
    ADD CONSTRAINT password_reset_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5015 (class 2606 OID 32995)
-- Name: security_codes security_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_codes
    ADD CONSTRAINT security_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5016 (class 2606 OID 33000)
-- Name: sessions sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5028 (class 2606 OID 33115)
-- Name: user_fund_position_stats user_fund_position_stats_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats
    ADD CONSTRAINT user_fund_position_stats_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5029 (class 2606 OID 33110)
-- Name: user_fund_position_stats user_fund_position_stats_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_position_stats
    ADD CONSTRAINT user_fund_position_stats_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5017 (class 2606 OID 33005)
-- Name: user_fund_positions user_fund_positions_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 5018 (class 2606 OID 33010)
-- Name: user_fund_positions user_fund_positions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5019 (class 2606 OID 33015)
-- Name: user_portfolio_daily user_portfolio_daily_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily
    ADD CONSTRAINT user_portfolio_daily_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5032 (class 2606 OID 33182)
-- Name: user_totp_recovery_codes user_totp_recovery_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_totp_recovery_codes
    ADD CONSTRAINT user_totp_recovery_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5020 (class 2606 OID 33020)
-- Name: user_wallets user_wallets_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wallets
    ADD CONSTRAINT user_wallets_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5021 (class 2606 OID 33025)
-- Name: wallet_transfers wallet_transfers_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5022 (class 2606 OID 33030)
-- Name: wallet_transfers wallet_transfers_wallet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_wallet_id_fkey FOREIGN KEY (wallet_id) REFERENCES public.user_wallets(id) ON DELETE CASCADE;


--
-- TOC entry 5023 (class 2606 OID 33057)
-- Name: withdraw_sessions withdraw_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 5024 (class 2606 OID 33062)
-- Name: withdraw_sessions withdraw_sessions_wallet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_wallet_id_fkey FOREIGN KEY (wallet_id) REFERENCES public.user_wallets(id) ON DELETE CASCADE;


-- Completed on 2026-05-22 14:51:24

--
-- PostgreSQL database dump complete
--

\unrestrict 1qa9I1U9AEJaiwXinTxgmJtQ3mnIGdQQhu6fX3wjqsikIaDSpwLG3gAoMPfh7ZA

