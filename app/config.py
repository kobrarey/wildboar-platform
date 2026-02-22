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
    WALLET_ENC_KEY: str = ""

    # auth / session
    SESSION_TTL_DAYS: int = 30
    COOKIE_NAME: str = "session_id"


settings = Settings()
