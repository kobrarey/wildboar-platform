from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str

    SECRET_KEY: str = "CHANGE_ME"

    # --- email ---
    EMAIL_PROVIDER: str = "smtp_relay"   # smtp_relay | gmail_api
    EMAIL_FROM_NAME: str = "Wild Boar"
    EMAIL_FROM_EMAIL: str = ""

    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_STARTTLS: bool = True
    SMTP_SSL: bool = False
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_TIMEOUT_SEC: int = 20

    # --- security codes ---
    SECURITY_CODE_LENGTH: int = 6
    SECURITY_CODE_TTL_MINUTES: int = 15
    SECURITY_CODE_MAX_ATTEMPTS: int = 5
    SECURITY_CODE_RESEND_COOLDOWN_SECONDS: int = 60

    # --- auth / session / cookies ---
    SESSION_TTL_DAYS: int = 30
    COOKIE_NAME: str = "session_id"
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"   # lax | strict | none
    COOKIE_DOMAIN: str = ""
    COOKIE_PATH: str = "/"

    # --- TOTP / Google Authenticator ---
    TOTP_ENC_KEY: str = ""

    # --- Bybit API credentials encryption ---
    BYBIT_API_ENC_KEY: str = ""

    # --- runtime / proxy ---
    APP_ENV: str = "development"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    UVICORN_PROXY_HEADERS: bool = True
    UVICORN_FORWARDED_ALLOW_IPS: str = "127.0.0.1"

    # --- stage 25: production readiness / order entry gates ---
    ORDER_ENTRY_ENABLED_FUND_CODES: str = "wb_test"
    ORDER_ENTRY_DISABLED_MODE: str = "reject"
    TRADING_BUY_MIN_USDT: Decimal = Decimal("10")
    TRADING_BUY_MAX_USDT: Decimal = Decimal("10000000")
    TRADING_REDEEM_MAX_SHARES: Decimal = Decimal("1000")
    WITHDRAW_GAS_WAIT_RETRY_SEC: int = 300
    WITHDRAW_GAS_ALERT_COOLDOWN_SEC: int = 3600
    SETTLEMENT_GAS_WAIT_RETRY_SEC: int = 300
    SETTLEMENT_GAS_ALERT_COOLDOWN_SEC: int = 3600
    BYBIT_WITHDRAWALS_ENABLED: bool = False
    POSITIVE_NET_LIVE_TRANSFER_ENABLED: bool = False
    LIFECYCLE_WORKERS_PRODUCTION_LIVE_ENABLED: bool = False
    NEGATIVE_NET_TARGETS_ALLOW_LIVE_FEE: bool = False
    NEGATIVE_NET_SALE_PLAN_ALLOW_LIVE_READONLY: bool = False
    NEGATIVE_NET_SALE_EXECUTION_ALLOW_LIVE: bool = False
    NEGATIVE_NET_BYBIT_FLOW_ALLOW_LIVE_EXECUTION: bool = False
    NEGATIVE_NET_PAYOUT_ALLOW_LIVE_EXECUTION: bool = False
    NEGATIVE_NET_FINALIZATION_ALLOW_LIVE_EXECUTION: bool = False
    SETTLEMENT_OPERATOR_ACTION_ALLOW_LIVE_BSC: bool = False
    SETTLEMENT_BUY_COLLECTION_ALLOW_LIVE_BSC: bool = False
    ALLOCATION_PLAN_ALLOW_LIVE_READONLY: bool = False
    ALLOCATION_EXECUTION_ALLOW_LIVE: bool = False

    # --- wallets / bsc ---
    BSC_RPC_URL: str = ""
    BSC_WS_URL: str = ""
    BSC_USDT_CONTRACT: str = "0x55d398326f99059fF775485246999027B3197955"
    BSC_USDT_DECIMALS: int = 18
    BSC_CONFIRMATIONS: int = 20
    BSC_CONFIRM_POLL_SEC: int = 15
    BSC_BALANCE_POLL_SEC: int = 15
    BSC_WALLET_MAP_RELOAD_SEC: int = 60
    BSC_REORG_BUFFER_BLOCKS: int = 20
    BSC_BACKFILL_CHUNK_BLOCKS: int = 2000
    BSC_START_LOOKBACK_BLOCKS: int = 50000
    BSC_BACKFILL_ON_START: bool = True
    WALLET_ENC_KEY: str = ""

    # --- compliance / sanctions screening ---
    CHAINALYSIS_SANCTIONS_API_KEY: str = ""
    COMPLIANCE_USE_CHAINALYSIS_API: bool = True
    COMPLIANCE_USE_ORACLE: bool = True
    COMPLIANCE_USE_OFAC: bool = True
    COMPLIANCE_FAIL_CLOSED: bool = True
    COMPLIANCE_HTTP_TIMEOUT_SEC: int = 10
    COMPLIANCE_POLL_SEC: int = 10
    COMPLIANCE_PENDING_RETRY_SEC: int = 60
    COMPLIANCE_ORACLE_CONTRACT: str = "0x40C57923924B5c5c5455c48D93317139ADDaC8fb"
    COMPLIANCE_OFAC_FILE: str = "data/ofac_addresses.json"

    # --- stage 10: withdrawals ---
    FEE_WALLET_OK_ADDRESS: str = ""
    FEE_WALLET_OK_PRIVATE_KEY: str = ""
    FEE_WALLET_BLOCKED_ADDRESS: str = ""
    FEE_WALLET_BLOCKED_PRIVATE_KEY: str = ""

    WITHDRAW_FEE_USDT: Decimal = Decimal("1")
    WITHDRAW_SESSION_TTL_MIN: int = 15
    WITHDRAW_GAS_BUFFER_MULT: Decimal = Decimal("1.2")
    WITHDRAW_GAS_MAX_BNB: Decimal = Decimal("0.01")
    ERC20_TRANSFER_GAS_FALLBACK: int = 70000

    # --- telegram watchdog ---
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_CHAT_ID: str = ""
    BNB_ALERT_THRESHOLD_USD: Decimal = Decimal("100")
    TELEGRAM_CHECK_SEC: int = 3600

    # --- stage 22.7: telegram operator actions ---
    TELEGRAM_OPERATOR_ACTIONS_ENABLED: bool = False
    TELEGRAM_OPERATOR_ALLOWED_CHAT_IDS: str = ""
    TELEGRAM_OPERATOR_ALLOWED_USER_IDS: str = ""
    TELEGRAM_CALLBACK_SECRET: str = ""
    TELEGRAM_OPERATOR_ACTION_TTL_MINUTES: int = 60
    TELEGRAM_OPERATOR_ACTION_COOLDOWN_SEC: int = 30

    # --- stage 23.1: negative-net target calculation ---
    NEGATIVE_NET_MOCK_BYBIT_WITHDRAWAL_FEE_USDT: Decimal = Decimal("1")

    # --- stage 23.2: negative-net sale plan ---
    NEGATIVE_NET_SALE_PLAN_MOCK_ONLY: bool = True
    NEGATIVE_NET_EXTRA_LARGEST_ASSET_BUFFER_PCT: Decimal = Decimal("0.10")
    NEGATIVE_NET_SALE_PLAN_USE_READONLY_BYBIT: bool = False

    # --- stage 23.3: negative-net sale execution ---
    NEGATIVE_NET_SALE_EXECUTION_MOCK_ONLY: bool = True
    NEGATIVE_NET_SALE_FILL_ACCEPTANCE_PCT: Decimal = Decimal("98")
    NEGATIVE_NET_SALE_CORRIDOR_PCT: Decimal = Decimal("1")
    NEGATIVE_NET_SALE_SLICES: int = 10
    NEGATIVE_NET_SALE_MAX_ACTIVE_STRATEGY_ORDERS: int = 5
    NEGATIVE_NET_SALE_ALLOW_LIVE_EXECUTION: bool = False

    # --- stage 23.4: negative-net Bybit master flow mock/preflight ---
    NEGATIVE_NET_BYBIT_FLOW_MOCK_ONLY: bool = True
    NEGATIVE_NET_BYBIT_FLOW_ALLOW_LIVE: bool = False
    NEGATIVE_NET_BYBIT_FLOW_COIN: str = "USDT"
    NEGATIVE_NET_BYBIT_FLOW_CHAIN: str = "BSC"
    NEGATIVE_NET_WITHDRAWAL_FEE_TYPE: int = 0
    NEGATIVE_NET_REQUIRE_ACTIVE_SETTLEMENT_WALLET: bool = True
    NEGATIVE_NET_REQUIRE_INTERNAL_SETTLEMENT_WALLET_WHITELIST: bool = True
    BYBIT_MASTER_RECV_WINDOW_MS: int = 5000

    # --- stage 23.5: negative-net settlement payout mock flow ---
    NEGATIVE_NET_PAYOUT_MOCK_ONLY: bool = True
    NEGATIVE_NET_PAYOUT_ALLOW_LIVE: bool = False
    NEGATIVE_NET_PAYOUT_COIN: str = "USDT"
    NEGATIVE_NET_PAYOUT_CHAIN: str = "BSC"
    NEGATIVE_NET_PAYOUT_CONFIRMATIONS_REQUIRED: int = 12
    NEGATIVE_NET_PAYOUT_AGGREGATE_BY_USER_WALLET: bool = True
    NEGATIVE_NET_PAYOUT_UPDATE_USER_WALLET_BALANCES_IN_MOCK: bool = True
    NEGATIVE_NET_PAYOUT_CREATE_OPERATOR_ACTION_ON_GAS_FAIL: bool = True

    # --- stage 23.6: negative-net final accounting / pricing unlock ---
    NEGATIVE_NET_FINALIZATION_ENABLED: bool = True
    NEGATIVE_NET_FINALIZATION_DRY_RUN_DEFAULT: bool = True
    NEGATIVE_NET_FINALIZATION_REQUIRE_PAYOUTS_CONFIRMED: bool = True
    NEGATIVE_NET_FINALIZATION_UNLOCK_PRICING: bool = True
    NEGATIVE_NET_FINALIZATION_SEND_TELEGRAM_ALERTS: bool = True

    # --- stage 24: operation guard / withdrawal kill switch ---
    OPERATION_GUARD_ENABLED: bool = True
    OPERATION_GUARD_FAIL_CLOSED: bool = True
    OPERATION_GUARD_DEFAULT_MODE: str = "blocked"
    OPERATION_GUARD_OVERRIDE_DEFAULT_TTL_MINUTES: int = 15
    OPERATION_GUARD_OVERRIDE_MAX_TTL_MINUTES: int = 60
    OPERATION_GUARD_REQUIRE_MANAGER_ACCOUNT: bool = True
    OPERATION_GUARD_REQUIRE_FUND_STATE_FOR_FUND_ACTIONS: bool = True
    OPERATION_GUARD_LOG_ALLOWED_EVENTS: bool = True
    OPERATION_GUARD_LOG_BLOCKED_EVENTS: bool = True

    # --- fee wallet daily USDT -> BNB swap ---
    FEE_WALLET_SWAP_ENABLED: bool = True
    FEE_WALLET_SWAP_INTERVAL_SEC: int = 3600
    FEE_WALLET_SWAP_MIN_USDT: Decimal = Decimal("10")
    FEE_WALLET_SWAP_SLIPPAGE_BPS: int = 100
    FEE_WALLET_SWAP_DAILY_HOUR_UTC: int = 0

    # --- pancake quote ---
    PANCAKE_ROUTER_V2: str = ""
    WBNB_ADDRESS: str = ""
    USDT_BSC_ADDRESS: str = ""

    # --- stage 18.3: NAV Guard ---
    NAV_GUARD_ENABLED: bool = True
    NAV_GUARD_MAX_NAV_DROP_PCT: Decimal = Decimal("15")
    NAV_GUARD_EARN_DROP_PCT: Decimal = Decimal("50")
    NAV_GUARD_COMPENSATION_RATIO: Decimal = Decimal("0.70")
    NAV_GUARD_MIN_EARN_DROP_USD: Decimal = Decimal("10")
    NAV_GUARD_TELEGRAM_ALERTS: bool = True

    # --- stage 18.1: navcalc local config ---
    BYBIT_NAV_HTTP_TIMEOUT_SEC: int = 30
    BYBIT_NAV_RETRIES: int = 4
    BYBIT_NAV_BACKOFF_SEC: Decimal = Decimal("0.8")
    BYBIT_NAV_RECV_WINDOW_MS: int = 5000
    BYBIT_NAV_EQUITY_TOL_PCT: Decimal = Decimal("0.5")
    NAV_POLL_INTERVAL_SEC: int = 10

    # --- stage 21: settlement ---
    SETTLEMENT_ENABLED: bool = False
    SETTLEMENT_CUTOFF_HOUR_UTC: int = 23
    SETTLEMENT_CUTOFF_MINUTE_UTC: int = 59
    SETTLEMENT_RUN_HOUR_UTC: int = 0
    SETTLEMENT_RUN_MINUTE_UTC: int = 0
    SETTLEMENT_PRICE_MAX_AGE_SEC: int = 300

    SETTLEMENT_WALLET_TARGET_BNB_USD: Decimal = Decimal("100")
    SETTLEMENT_WALLET_MIN_GAS_BUFFER_MULT: Decimal = Decimal("1.20")
    SETTLEMENT_GAS_TOPUP_RETRY_HOUR_UTC: int = 23
    SETTLEMENT_GAS_TOPUP_RETRY_MINUTE_UTC: int = 50

    # --- stage 22.1: positive net settlement ---
    POSITIVE_NET_SETTLEMENT_ENABLED: bool = False
    POSITIVE_NET_DEPOSIT_CONFIRM_TIMEOUT_SEC: int = 3600
    POSITIVE_NET_DEPOSIT_POLL_INTERVAL_SEC: int = 30
    POSITIVE_NET_DUST_TOLERANCE_USDT: Decimal = Decimal("0.01")

    # --- stage 22.3: allocation execution engine ---
    ALLOCATION_EXECUTION_ENABLED: bool = False

    ALLOCATION_LIQUIDITY_CORRIDOR_PCT: Decimal = Decimal("1")
    ALLOCATION_MARKET_SLIPPAGE_PCT: Decimal = Decimal("1")
    ALLOCATION_MIN_FILL_RATIO: Decimal = Decimal("0.90")

    ALLOCATION_NATIVE_ICEBERG_ORDER_COUNT: int = 10
    ALLOCATION_MAX_ACTIVE_STRATEGY_ORDERS: int = 5

    ALLOCATION_SLICED_IOC_SLICES: int = 10
    ALLOCATION_SLICED_IOC_CHASE_BPS: int = 10

    ALLOCATION_SHORT_OPTION_LIQUIDITY_MULT: Decimal = Decimal("1.20")

    ALLOCATION_MAX_IM_RATE: Decimal = Decimal("0.70")
    ALLOCATION_MAX_MM_RATE: Decimal = Decimal("0.50")

    # --- stage 22.4: spot / earn / residual mock handlers ---
    ALLOCATION_SPOT_EARN_ENABLED: bool = False
    ALLOCATION_EARN_ENABLED: bool = False
    ALLOCATION_USDT_EARN_CATEGORY: str = "FlexibleSaving"

    # --- stage 25.3: guarded live Earn allocation execution ---
    ALLOCATION_EARN_ALLOW_LIVE: bool = False
    ALLOCATION_EARN_ALLOWED_FUND_CODES: str = "wb_test"
    ALLOCATION_EARN_ALLOWED_COINS: str = "USDT"
    ALLOCATION_EARN_ALLOWED_PRODUCT_IDS: str = ""
    ALLOCATION_EARN_ALLOWED_CATEGORIES: str = "FlexibleSaving"
    ALLOCATION_EARN_RESIDUAL_TO_CASH_WHEN_DISABLED: bool = True

    ALLOCATION_RESIDUAL_MIN_MATERIALITY_USDT: Decimal = Decimal("0.01")
    ALLOCATION_RESIDUAL_ALERT_THRESHOLD_USDT: Decimal = Decimal("100")

    # --- stage 22.5: derivatives / options mock handlers ---
    ALLOCATION_DERIVATIVES_ENABLED: bool = False
    ALLOCATION_OPTIONS_ENABLED: bool = False
    ALLOCATION_DERIVATIVE_RESIDUAL_ON_GUARD_FAIL: bool = True

    # --- stage 22.6: positive-net allocation integration / reconciliation ---
    POSITIVE_NET_ALLOCATION_ENABLED: bool = False
    POSITIVE_NET_ALLOCATION_MOCK_ONLY: bool = True

    ALLOCATION_ALERTS_ENABLED: bool = True
    ALLOCATION_RESIDUAL_CASH_ALERT_THRESHOLD_USDT: Decimal = Decimal("100")
    ALLOCATION_FAILED_REVIEW_ALERTS: bool = True
    ALLOCATION_UNKNOWN_STATE_ALERTS: bool = True
    ALLOCATION_MARGIN_BREACH_ALERTS: bool = True

    ALLOCATION_FINALIZATION_REQUIRE_NO_ACTIVE_LEGS: bool = True

    # --- Bybit master API runtime settings ---
    BYBIT_MASTER_HTTP_TIMEOUT_SEC: int = 20
    BYBIT_MASTER_RETRIES: int = 3
    BYBIT_MASTER_BACKOFF_SEC: Decimal = Decimal("0.8")


settings = Settings()
