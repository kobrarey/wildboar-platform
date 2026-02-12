--
-- PostgreSQL database dump
--

\restrict 9txXmBNepqCo4YW6XNKZZZbbxoCB8XWdoeS9NK9zkXkoeoFin015nnqEE52yA1U

-- Dumped from database version 16.11
-- Dumped by pg_dump version 16.11

-- Started on 2026-02-11 16:58:25

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

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- TOC entry 230 (class 1259 OID 16607)
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
-- TOC entry 229 (class 1259 OID 16606)
-- Name: fund_nav_minute_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.fund_nav_minute_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 4961 (class 0 OID 0)
-- Dependencies: 229
-- Name: fund_nav_minute_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.fund_nav_minute_id_seq OWNED BY public.fund_nav_minute.id;


--
-- TOC entry 224 (class 1259 OID 16544)
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
-- TOC entry 223 (class 1259 OID 16543)
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
-- TOC entry 4962 (class 0 OID 0)
-- Dependencies: 223
-- Name: funds_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.funds_id_seq OWNED BY public.funds.id;


--
-- TOC entry 220 (class 1259 OID 16470)
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
-- TOC entry 219 (class 1259 OID 16455)
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
    CONSTRAINT security_codes_purpose_check CHECK (((purpose)::text = ANY ((ARRAY['registration'::character varying, 'reset'::character varying, 'login_2fa'::character varying, 'password_change'::character varying])::text[])))
);


--
-- TOC entry 218 (class 1259 OID 16454)
-- Name: security_codes_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.security_codes_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 4963 (class 0 OID 0)
-- Dependencies: 218
-- Name: security_codes_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.security_codes_id_seq OWNED BY public.security_codes.id;


--
-- TOC entry 217 (class 1259 OID 16429)
-- Name: sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sessions (
    id character varying(255) NOT NULL,
    user_id bigint NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone NOT NULL
);


--
-- TOC entry 226 (class 1259 OID 16570)
-- Name: user_fund_positions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_fund_positions (
    id bigint NOT NULL,
    user_id integer NOT NULL,
    fund_id integer NOT NULL,
    shares numeric(30,10) DEFAULT 0 NOT NULL
);


--
-- TOC entry 225 (class 1259 OID 16569)
-- Name: user_fund_positions_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_fund_positions_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 4964 (class 0 OID 0)
-- Dependencies: 225
-- Name: user_fund_positions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_fund_positions_id_seq OWNED BY public.user_fund_positions.id;


--
-- TOC entry 228 (class 1259 OID 16591)
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
-- TOC entry 227 (class 1259 OID 16590)
-- Name: user_portfolio_daily_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_portfolio_daily_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 4965 (class 0 OID 0)
-- Dependencies: 227
-- Name: user_portfolio_daily_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_portfolio_daily_id_seq OWNED BY public.user_portfolio_daily.id;


--
-- TOC entry 222 (class 1259 OID 16498)
-- Name: user_wallets; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_wallets (
    id bigint NOT NULL,
    user_id bigint NOT NULL,
    blockchain character varying(32) DEFAULT 'BSC'::character varying NOT NULL,
    address character varying(64) NOT NULL,
    encrypted_private_key text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- TOC entry 221 (class 1259 OID 16497)
-- Name: user_wallets_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.user_wallets_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 4966 (class 0 OID 0)
-- Dependencies: 221
-- Name: user_wallets_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.user_wallets_id_seq OWNED BY public.user_wallets.id;


--
-- TOC entry 216 (class 1259 OID 16415)
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
    CONSTRAINT users_account_type_check CHECK (((account_type)::text = ANY ((ARRAY['basic'::character varying, 'vip'::character varying, 'employee'::character varying, 'manager'::character varying])::text[])))
);


--
-- TOC entry 215 (class 1259 OID 16414)
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.users_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- TOC entry 4967 (class 0 OID 0)
-- Dependencies: 215
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- TOC entry 4750 (class 2604 OID 16610)
-- Name: fund_nav_minute id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute ALTER COLUMN id SET DEFAULT nextval('public.fund_nav_minute_id_seq'::regclass);


--
-- TOC entry 4743 (class 2604 OID 16547)
-- Name: funds id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funds ALTER COLUMN id SET DEFAULT nextval('public.funds_id_seq'::regclass);


--
-- TOC entry 4734 (class 2604 OID 16458)
-- Name: security_codes id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_codes ALTER COLUMN id SET DEFAULT nextval('public.security_codes_id_seq'::regclass);


--
-- TOC entry 4746 (class 2604 OID 16573)
-- Name: user_fund_positions id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions ALTER COLUMN id SET DEFAULT nextval('public.user_fund_positions_id_seq'::regclass);


--
-- TOC entry 4748 (class 2604 OID 16594)
-- Name: user_portfolio_daily id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily ALTER COLUMN id SET DEFAULT nextval('public.user_portfolio_daily_id_seq'::regclass);


--
-- TOC entry 4740 (class 2604 OID 16501)
-- Name: user_wallets id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wallets ALTER COLUMN id SET DEFAULT nextval('public.user_wallets_id_seq'::regclass);


--
-- TOC entry 4726 (class 2604 OID 16418)
-- Name: users id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- TOC entry 4786 (class 2606 OID 16614)
-- Name: fund_nav_minute fund_nav_minute_fund_ts_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute
    ADD CONSTRAINT fund_nav_minute_fund_ts_unique UNIQUE (fund_id, ts_utc);


--
-- TOC entry 4788 (class 2606 OID 16612)
-- Name: fund_nav_minute fund_nav_minute_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute
    ADD CONSTRAINT fund_nav_minute_pkey PRIMARY KEY (id);


--
-- TOC entry 4771 (class 2606 OID 16553)
-- Name: funds funds_code_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funds
    ADD CONSTRAINT funds_code_key UNIQUE (code);


--
-- TOC entry 4773 (class 2606 OID 16551)
-- Name: funds funds_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.funds
    ADD CONSTRAINT funds_pkey PRIMARY KEY (id);


--
-- TOC entry 4764 (class 2606 OID 16476)
-- Name: password_reset_sessions password_reset_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_sessions
    ADD CONSTRAINT password_reset_sessions_pkey PRIMARY KEY (id);


--
-- TOC entry 4762 (class 2606 OID 16464)
-- Name: security_codes security_codes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_codes
    ADD CONSTRAINT security_codes_pkey PRIMARY KEY (id);


--
-- TOC entry 4760 (class 2606 OID 16434)
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (id);


--
-- TOC entry 4775 (class 2606 OID 16576)
-- Name: user_fund_positions user_fund_positions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_pkey PRIMARY KEY (id);


--
-- TOC entry 4777 (class 2606 OID 16578)
-- Name: user_fund_positions user_fund_positions_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_unique UNIQUE (user_id, fund_id);


--
-- TOC entry 4780 (class 2606 OID 16597)
-- Name: user_portfolio_daily user_portfolio_daily_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily
    ADD CONSTRAINT user_portfolio_daily_pkey PRIMARY KEY (id);


--
-- TOC entry 4782 (class 2606 OID 16599)
-- Name: user_portfolio_daily user_portfolio_daily_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily
    ADD CONSTRAINT user_portfolio_daily_unique UNIQUE (user_id, date_utc);


--
-- TOC entry 4767 (class 2606 OID 16507)
-- Name: user_wallets user_wallets_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wallets
    ADD CONSTRAINT user_wallets_pkey PRIMARY KEY (id);


--
-- TOC entry 4755 (class 2606 OID 16426)
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- TOC entry 4757 (class 2606 OID 16424)
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- TOC entry 4784 (class 1259 OID 16620)
-- Name: fund_nav_minute_fund_ts_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX fund_nav_minute_fund_ts_idx ON public.fund_nav_minute USING btree (fund_id, ts_utc DESC);


--
-- TOC entry 4758 (class 1259 OID 16440)
-- Name: idx_sessions_expires_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_sessions_expires_at ON public.sessions USING btree (expires_at);


--
-- TOC entry 4778 (class 1259 OID 16589)
-- Name: user_fund_positions_user_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_fund_positions_user_idx ON public.user_fund_positions USING btree (user_id);


--
-- TOC entry 4783 (class 1259 OID 16605)
-- Name: user_portfolio_daily_user_date_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_portfolio_daily_user_date_idx ON public.user_portfolio_daily USING btree (user_id, date_utc DESC);


--
-- TOC entry 4765 (class 1259 OID 16514)
-- Name: user_wallets_blockchain_address_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX user_wallets_blockchain_address_uq ON public.user_wallets USING btree (blockchain, address);


--
-- TOC entry 4768 (class 1259 OID 16513)
-- Name: user_wallets_user_blockchain_uq; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX user_wallets_user_blockchain_uq ON public.user_wallets USING btree (user_id, blockchain);


--
-- TOC entry 4769 (class 1259 OID 16515)
-- Name: user_wallets_user_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX user_wallets_user_id_idx ON public.user_wallets USING btree (user_id);


--
-- TOC entry 4753 (class 1259 OID 16542)
-- Name: users_backup_email_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX users_backup_email_idx ON public.users USING btree (backup_email);


--
-- TOC entry 4796 (class 2606 OID 16615)
-- Name: fund_nav_minute fund_nav_minute_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fund_nav_minute
    ADD CONSTRAINT fund_nav_minute_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 4791 (class 2606 OID 16477)
-- Name: password_reset_sessions password_reset_sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.password_reset_sessions
    ADD CONSTRAINT password_reset_sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 4790 (class 2606 OID 16465)
-- Name: security_codes security_codes_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.security_codes
    ADD CONSTRAINT security_codes_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 4789 (class 2606 OID 16435)
-- Name: sessions sessions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 4793 (class 2606 OID 16584)
-- Name: user_fund_positions user_fund_positions_fund_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_fund_id_fkey FOREIGN KEY (fund_id) REFERENCES public.funds(id) ON DELETE CASCADE;


--
-- TOC entry 4794 (class 2606 OID 16579)
-- Name: user_fund_positions user_fund_positions_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_fund_positions
    ADD CONSTRAINT user_fund_positions_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 4795 (class 2606 OID 16600)
-- Name: user_portfolio_daily user_portfolio_daily_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_portfolio_daily
    ADD CONSTRAINT user_portfolio_daily_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- TOC entry 4792 (class 2606 OID 16508)
-- Name: user_wallets user_wallets_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_wallets
    ADD CONSTRAINT user_wallets_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


-- Completed on 2026-02-11 16:58:25

--
-- PostgreSQL database dump complete
--

\unrestrict 9txXmBNepqCo4YW6XNKZZZbbxoCB8XWdoeS9NK9zkXkoeoFin015nnqEE52yA1U

