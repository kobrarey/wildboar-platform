--
-- PostgreSQL database dump
--

\restrict IapnWbUp39vJ6IYvfTdNPwNDhmHnbG2MJudyEjSNs7H1DRrmuv6FwP6IbNWd8Bk

-- Dumped from database version 16.11
-- Dumped by pg_dump version 16.11

-- Started on 2026-03-10 13:11:00

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
-- TOC entry 5025 (class 0 OID 0)
-- Dependencies: 5
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS '';


SET default_tablespace = '';

SET default_table_access_method = heap;

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
-- TOC entry 5026 (class 0 OID 0)
-- Dependencies: 216
-- Name: fund_nav_minute_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_nav_minute_id_seq OWNED BY public.fund_nav_minute.id;


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
    is_active boolean DEFAULT true NOT NULL
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
-- TOC entry 5027 (class 0 OID 0)
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
-- TOC entry 5028 (class 0 OID 0)
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
-- TOC entry 223 (class 1259 OID 32882)
-- Name: user_fund_positions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_fund_positions (
    id bigint NOT NULL,
    user_id integer NOT NULL,
    fund_id integer NOT NULL,
    shares numeric(30,10) DEFAULT 0 NOT NULL
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
-- TOC entry 5029 (class 0 OID 0)
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
-- TOC entry 5030 (class 0 OID 0)
-- Dependencies: 226
-- Name: user_portfolio_daily_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_portfolio_daily_id_seq OWNED BY public.user_portfolio_daily.id;


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
-- TOC entry 5031 (class 0 OID 0)
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
    CONSTRAINT users_account_type_check CHECK (((account_type)::text = ANY (ARRAY[('basic'::character varying)::text, ('vip'::character varying)::text, ('employee'::character varying)::text, ('manager'::character varying)::text]))),
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
-- TOC entry 5032 (class 0 OID 0)
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
-- TOC entry 5033 (class 0 OID 0)
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
-- TOC entry 5034 (class 0 OID 0)
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
-- TOC entry 4740 (class 2604 OID 32931)
-- Name: fund_nav_minute id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute ALTER COLUMN id SET DEFAULT nextval('public.fund_nav_minute_id_seq'::regclass);


--
-- TOC entry 4741 (class 2604 OID 32932)
-- Name: funds id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funds ALTER COLUMN id SET DEFAULT nextval('public.funds_id_seq'::regclass);


--
-- TOC entry 4746 (class 2604 OID 32933)
-- Name: security_codes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_codes ALTER COLUMN id SET DEFAULT nextval('public.security_codes_id_seq'::regclass);


--
-- TOC entry 4751 (class 2604 OID 32934)
-- Name: user_fund_positions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions ALTER COLUMN id SET DEFAULT nextval('public.user_fund_positions_id_seq'::regclass);


--
-- TOC entry 4753 (class 2604 OID 32935)
-- Name: user_portfolio_daily id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily ALTER COLUMN id SET DEFAULT nextval('public.user_portfolio_daily_id_seq'::regclass);


--
-- TOC entry 4755 (class 2604 OID 32936)
-- Name: user_wallets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wallets ALTER COLUMN id SET DEFAULT nextval('public.user_wallets_id_seq'::regclass);


--
-- TOC entry 4762 (class 2604 OID 32937)
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- TOC entry 4770 (class 2604 OID 32938)
-- Name: wallet_transfers id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers ALTER COLUMN id SET DEFAULT nextval('public.wallet_transfers_id_seq'::regclass);


--
-- TOC entry 4780 (class 2604 OID 33050)
-- Name: withdraw_sessions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions ALTER COLUMN id SET DEFAULT nextval('public.withdraw_sessions_id_seq'::regclass);


--
-- TOC entry 4999 (class 0 OID 32855)
-- Dependencies: 215
-- Data for Name: fund_nav_minute; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fund_nav_minute (id, fund_id, ts_utc, nav_usdt, shares_outstanding) FROM stdin;
\.


--
-- TOC entry 5001 (class 0 OID 32859)
-- Dependencies: 217
-- Data for Name: funds; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.funds (id, code, name_ru, name_en, category, sort_order, is_active) FROM stdin;
4	defi_sniper	DeFi Sniper	DeFi Sniper	active	10	t
5	btc_fund	Bitcoin fund	Bitcoin fund	active	20	t
6	wb10	WB 10	WB 10	index	10	t
7	wb_defi	WB DeFi	WB DeFi	index	20	t
8	wb_web3	WB Web 3.0	WB Web 3.0	index	30	t
9	wb_test	WB test fund	WB test fund	test	10	t
\.


--
-- TOC entry 5003 (class 0 OID 32865)
-- Dependencies: 219
-- Data for Name: password_reset_sessions; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.password_reset_sessions (id, user_id, created_at, expires_at, is_used) FROM stdin;
\.


--
-- TOC entry 5004 (class 0 OID 32870)
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
15	1	withdraw	644281	2026-03-10 11:37:11.095547+03	2026-03-10 11:52:11.097154+03	f	0
\.


--
-- TOC entry 5006 (class 0 OID 32878)
-- Dependencies: 222
-- Data for Name: sessions; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.sessions (id, user_id, created_at, expires_at) FROM stdin;
25cf5e16ff414a9c9fd5eb5e6a0f9d2c	1	2026-03-04 18:24:04.416585+03	2026-04-03 18:24:04.416585+03
d53719af993e405ca894bb8cbfd646d3	1	2026-03-05 12:26:55.080395+03	2026-04-04 12:26:55.080395+03
\.


--
-- TOC entry 5007 (class 0 OID 32882)
-- Dependencies: 223
-- Data for Name: user_fund_positions; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.user_fund_positions (id, user_id, fund_id, shares) FROM stdin;
\.


--
-- TOC entry 5009 (class 0 OID 32887)
-- Dependencies: 225
-- Data for Name: user_portfolio_daily; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.user_portfolio_daily (id, user_id, date_utc, balance_usdt, created_at) FROM stdin;
\.


--
-- TOC entry 5011 (class 0 OID 32892)
-- Dependencies: 227
-- Data for Name: user_wallets; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.user_wallets (id, user_id, blockchain, address, encrypted_private_key, created_at, usdt_balance, usdt_balance_updated_at, usdt_balance_block, usdt_reserved, compliance_status, freeze_reason, compliance_checked_at, is_active, archived_at) FROM stdin;
1	1	BSC	0xcb97Ea3C8Ff8187026901F6Db5977E1b1815D1d8	gAAAAABpqE6UH_WoP4t0f3JvcXKWh-XwZsJPANgq9gWgjCFGBjCPZJEMgg56xeUhxfUAXeO8nsDaP4YI9Wg09u4sOIHb4Horl6Rjb6KrcSOGRCY_d-c34Dd4m4RxqQRBocdnPpcQEokUpbBhsNFVMqDkTXlSMtGwbZtDfXvzDTl_7WTKO76E9_M=	2026-03-04 18:24:04.389618+03	3.200000000000000000	2026-03-10 11:33:02.384649+03	85745362	0.000000000000000000	ok	\N	2026-03-10 11:33:50.820757+03	t	\N
\.


--
-- TOC entry 5013 (class 0 OID 32904)
-- Dependencies: 229
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.users (id, created_at, email, first_name, last_name, phone, password_hash, is_active, is_email_verified, two_factor_enabled, account_type, backup_email, is_backup_email_verified, compliance_status, compliance_reason, compliance_updated_at) FROM stdin;
1	2026-03-04 18:23:31.870297+03	kobra_rey99@mail.ru	Kirill	Vokulov	034 466 90 92	$2b$12$.k0Sk4ek.S7ZawBOXuZnH.sfhjSht25uQwpUPF6/bbNzOCNljgdIO	t	t	t	basic	volchypastyh@gmail.com	t	ok	\N	2026-03-10 11:33:50.821766+03
\.


--
-- TOC entry 5015 (class 0 OID 32919)
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
-- TOC entry 5019 (class 0 OID 33047)
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
\.


--
-- TOC entry 5017 (class 0 OID 33036)
-- Dependencies: 233
-- Data for Name: worker_cursors; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.worker_cursors (name, last_block, last_log_index, updated_at) FROM stdin;
bsc_usdt_listener	85745297	129	2026-03-10 11:32:33.300548+03
\.


--
-- TOC entry 5035 (class 0 OID 0)
-- Dependencies: 216
-- Name: fund_nav_minute_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.fund_nav_minute_id_seq', 3, true);


--
-- TOC entry 5036 (class 0 OID 0)
-- Dependencies: 218
-- Name: funds_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.funds_id_seq', 9, true);


--
-- TOC entry 5037 (class 0 OID 0)
-- Dependencies: 221
-- Name: security_codes_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.security_codes_id_seq', 15, true);


--
-- TOC entry 5038 (class 0 OID 0)
-- Dependencies: 224
-- Name: user_fund_positions_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.user_fund_positions_id_seq', 1, false);


--
-- TOC entry 5039 (class 0 OID 0)
-- Dependencies: 226
-- Name: user_portfolio_daily_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.user_portfolio_daily_id_seq', 1, false);


--
-- TOC entry 5040 (class 0 OID 0)
-- Dependencies: 228
-- Name: user_wallets_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.user_wallets_id_seq', 1, true);


--
-- TOC entry 5041 (class 0 OID 0)
-- Dependencies: 230
-- Name: users_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.users_id_seq', 1, true);


--
-- TOC entry 5042 (class 0 OID 0)
-- Dependencies: 232
-- Name: wallet_transfers_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.wallet_transfers_id_seq', 6, true);


--
-- TOC entry 5043 (class 0 OID 0)
-- Dependencies: 234
-- Name: withdraw_sessions_id_seq; Type: SEQUENCE SET; Schema: public; Owner: -
--

SELECT pg_catalog.setval('public.withdraw_sessions_id_seq', 12, true);


--
-- TOC entry 4790 (class 2606 OID 32940)
-- Name: fund_nav_minute fund_nav_minute_fund_ts_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute
    ADD CONSTRAINT fund_nav_minute_fund_ts_unique UNIQUE (fund_id, ts_utc);


--
-- TOC entry 4792 (class 2606 OID 32942)
-- Name: fund_nav_minute fund_nav_minute_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute
    ADD CONSTRAINT fund_nav_minute_pkey PRIMARY KEY (id);


--
-- TOC entry 4794 (class 2606 OID 32944)
-- Name: funds funds_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funds
    ADD CONSTRAINT funds_code_key UNIQUE (code);


--
-- TOC entry 4796 (class 2606 OID 32946)
-- Name: funds funds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funds
    ADD CONSTRAINT funds_pkey PRIMARY KEY (id);


--
-- TOC entry 4798 (class 2606 OID 32948)
-- Name: password_reset_sessions password_reset_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_sessions
    ADD CONSTRAINT password_reset_sessions_pkey PRIMARY KEY (id);


--
-- TOC entry 4800 (class 2606 OID 32950)
-- Name: security_codes security_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_codes
    ADD CONSTRAINT security_codes_pkey PRIMARY KEY (id);


--
-- TOC entry 4803 (class 2606 OID 32952)
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);


--
-- TOC entry 4805 (class 2606 OID 32954)
-- Name: user_fund_positions user_fund_positions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_pkey PRIMARY KEY (id);


--
-- TOC entry 4807 (class 2606 OID 32956)
-- Name: user_fund_positions user_fund_positions_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_unique UNIQUE (user_id, fund_id);


--
-- TOC entry 4810 (class 2606 OID 32958)
-- Name: user_portfolio_daily user_portfolio_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily
    ADD CONSTRAINT user_portfolio_daily_pkey PRIMARY KEY (id);


--
-- TOC entry 4812 (class 2606 OID 32960)
-- Name: user_portfolio_daily user_portfolio_daily_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily
    ADD CONSTRAINT user_portfolio_daily_unique UNIQUE (user_id, date_utc);


--
-- TOC entry 4817 (class 2606 OID 32962)
-- Name: user_wallets user_wallets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wallets
    ADD CONSTRAINT user_wallets_pkey PRIMARY KEY (id);


--
-- TOC entry 4823 (class 2606 OID 32964)
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- TOC entry 4825 (class 2606 OID 32966)
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- TOC entry 4831 (class 2606 OID 32968)
-- Name: wallet_transfers wallet_transfers_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_pkey PRIMARY KEY (id);


--
-- TOC entry 4834 (class 2606 OID 32970)
-- Name: wallet_transfers wallet_transfers_tx_hash_log_index_uq; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_tx_hash_log_index_uq UNIQUE (tx_hash, log_index);


--
-- TOC entry 4841 (class 2606 OID 33054)
-- Name: withdraw_sessions withdraw_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_pkey PRIMARY KEY (id);


--
-- TOC entry 4843 (class 2606 OID 33056)
-- Name: withdraw_sessions withdraw_sessions_token_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_token_key UNIQUE (token);


--
-- TOC entry 4838 (class 2606 OID 33044)
-- Name: worker_cursors worker_cursors_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.worker_cursors
    ADD CONSTRAINT worker_cursors_pkey PRIMARY KEY (name);


--
-- TOC entry 4788 (class 1259 OID 32971)
-- Name: fund_nav_minute_fund_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_nav_minute_fund_ts_idx ON public.fund_nav_minute USING btree (fund_id, ts_utc DESC);


--
-- TOC entry 4801 (class 1259 OID 32972)
-- Name: idx_sessions_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_expires_at ON public.sessions USING btree (expires_at);


--
-- TOC entry 4820 (class 1259 OID 32973)
-- Name: idx_users_compliance_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_users_compliance_status ON public.users USING btree (compliance_status);


--
-- TOC entry 4826 (class 1259 OID 32974)
-- Name: idx_wallet_transfers_compliance_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_compliance_status ON public.wallet_transfers USING btree (compliance_status);


--
-- TOC entry 4827 (class 1259 OID 32975)
-- Name: idx_wallet_transfers_need_compliance; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_need_compliance ON public.wallet_transfers USING btree (status, compliance_status);


--
-- TOC entry 4828 (class 1259 OID 33073)
-- Name: idx_wallet_transfers_user_type_time; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_user_type_time ON public.wallet_transfers USING btree (user_id, type, detected_at DESC);


--
-- TOC entry 4829 (class 1259 OID 33072)
-- Name: idx_wallet_transfers_withdraw_processing; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_wallet_transfers_withdraw_processing ON public.wallet_transfers USING btree (type, status) WHERE (((type)::text = 'withdraw'::text) AND ((status)::text = 'processing'::text));


--
-- TOC entry 4839 (class 1259 OID 33067)
-- Name: idx_withdraw_sessions_user_expires; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_withdraw_sessions_user_expires ON public.withdraw_sessions USING btree (user_id, expires_at DESC);


--
-- TOC entry 4808 (class 1259 OID 32976)
-- Name: user_fund_positions_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_fund_positions_user_idx ON public.user_fund_positions USING btree (user_id);


--
-- TOC entry 4813 (class 1259 OID 32977)
-- Name: user_portfolio_daily_user_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_portfolio_daily_user_date_idx ON public.user_portfolio_daily USING btree (user_id, date_utc DESC);


--
-- TOC entry 4814 (class 1259 OID 32978)
-- Name: user_wallets_blockchain_address_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX user_wallets_blockchain_address_uq ON public.user_wallets USING btree (blockchain, address);


--
-- TOC entry 4815 (class 1259 OID 33069)
-- Name: user_wallets_one_active_bsc; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX user_wallets_one_active_bsc ON public.user_wallets USING btree (user_id) WHERE (((blockchain)::text = 'BSC'::text) AND (is_active = true));


--
-- TOC entry 4818 (class 1259 OID 32979)
-- Name: user_wallets_user_blockchain_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX user_wallets_user_blockchain_uq ON public.user_wallets USING btree (user_id, blockchain);


--
-- TOC entry 4819 (class 1259 OID 32980)
-- Name: user_wallets_user_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_wallets_user_id_idx ON public.user_wallets USING btree (user_id);


--
-- TOC entry 4821 (class 1259 OID 32981)
-- Name: users_backup_email_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX users_backup_email_idx ON public.users USING btree (backup_email);


--
-- TOC entry 4832 (class 1259 OID 32982)
-- Name: wallet_transfers_status_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX wallet_transfers_status_idx ON public.wallet_transfers USING btree (status);


--
-- TOC entry 4835 (class 1259 OID 32983)
-- Name: wallet_transfers_user_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX wallet_transfers_user_time_idx ON public.wallet_transfers USING btree (user_id, tx_time DESC);


--
-- TOC entry 4836 (class 1259 OID 32984)
-- Name: wallet_transfers_wallet_time_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX wallet_transfers_wallet_time_idx ON public.wallet_transfers USING btree (wallet_id, tx_time DESC);


--
-- TOC entry 4844 (class 2606 OID 32985)
-- Name: fund_nav_minute fund_nav_minute_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute
    ADD CONSTRAINT fund_nav_minute_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 4845 (class 2606 OID 32990)
-- Name: password_reset_sessions password_reset_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_sessions
    ADD CONSTRAINT password_reset_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 4846 (class 2606 OID 32995)
-- Name: security_codes security_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_codes
    ADD CONSTRAINT security_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 4847 (class 2606 OID 33000)
-- Name: sessions sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 4848 (class 2606 OID 33005)
-- Name: user_fund_positions user_fund_positions_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 4849 (class 2606 OID 33010)
-- Name: user_fund_positions user_fund_positions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 4850 (class 2606 OID 33015)
-- Name: user_portfolio_daily user_portfolio_daily_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily
    ADD CONSTRAINT user_portfolio_daily_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 4851 (class 2606 OID 33020)
-- Name: user_wallets user_wallets_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wallets
    ADD CONSTRAINT user_wallets_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 4852 (class 2606 OID 33025)
-- Name: wallet_transfers wallet_transfers_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 4853 (class 2606 OID 33030)
-- Name: wallet_transfers wallet_transfers_wallet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.wallet_transfers
    ADD CONSTRAINT wallet_transfers_wallet_id_fkey FOREIGN KEY (wallet_id) REFERENCES public.user_wallets(id) ON DELETE CASCADE;


--
-- TOC entry 4854 (class 2606 OID 33057)
-- Name: withdraw_sessions withdraw_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 4855 (class 2606 OID 33062)
-- Name: withdraw_sessions withdraw_sessions_wallet_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.withdraw_sessions
    ADD CONSTRAINT withdraw_sessions_wallet_id_fkey FOREIGN KEY (wallet_id) REFERENCES public.user_wallets(id) ON DELETE CASCADE;


-- Completed on 2026-03-10 13:11:00

--
-- PostgreSQL database dump complete
--

\unrestrict IapnWbUp39vJ6IYvfTdNPwNDhmHnbG2MJudyEjSNs7H1DRrmuv6FwP6IbNWd8Bk

