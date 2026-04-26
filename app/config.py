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

    # --- runtime / proxy ---
    APP_ENV: str = "development"
    APP_HOST: str = "127.0.0.1"
    APP_PORT: int = 8000
    UVICORN_PROXY_HEADERS: bool = True
    UVICORN_FORWARDED_ALLOW_IPS: str = "127.0.0.1"

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

    # --- pancake quote ---
    PANCAKE_ROUTER_V2: str = ""
    WBNB_ADDRESS: str = ""
    USDT_BSC_ADDRESS: str = ""

    # --- stage 18.1: navcalc local config ---
    BYBIT_NAV_HTTP_TIMEOUT_SEC: int = 30
    BYBIT_NAV_RETRIES: int = 4
    BYBIT_NAV_BACKOFF_SEC: Decimal = Decimal("0.8")
    BYBIT_NAV_RECV_WINDOW_MS: int = 5000
    BYBIT_NAV_EQUITY_TOL_PCT: Decimal = Decimal("0.5")
    NAV_POLL_INTERVAL_SEC: int = 10


settings = Settings()
