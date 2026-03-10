from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # читаем .env из корня проекта
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str

    # сейчас может напрямую не использоваться, но нужен по архитектуре (и позже пригодится)
    SECRET_KEY: str = "CHANGE_ME"

    # сейчас по факту gmail api
    EMAIL_PROVIDER: str = "gmail_api"
    EMAIL_FROM_NAME: str = "Wild Boar"
    EMAIL_FROM_EMAIL: str = ""  # если пусто — возьмем email из Gmail профиля

    # security codes
    SECURITY_CODE_LENGTH: int = 6
    SECURITY_CODE_TTL_MINUTES: int = 15
    SECURITY_CODE_MAX_ATTEMPTS: int = 5
    SECURITY_CODE_RESEND_COOLDOWN_SECONDS: int = 60

    # wallets / bsc
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
    COMPLIANCE_FAIL_CLOSED: bool = True  # всегда включено, но оставляем как настройку
    COMPLIANCE_HTTP_TIMEOUT_SEC: int = 10
    COMPLIANCE_POLL_SEC: int = 10
    COMPLIANCE_PENDING_RETRY_SEC: int = 60
    COMPLIANCE_ORACLE_CONTRACT: str = "0x40C57923924B5c5c5455c48D93317139ADDaC8fb"
    COMPLIANCE_OFAC_FILE: str = "data/ofac_addresses.json"

    # auth / session
    SESSION_TTL_DAYS: int = 30
    COOKIE_NAME: str = "session_id"

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


settings = Settings()
