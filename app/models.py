from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Column, Integer, BigInteger, SmallInteger, String, Text, Boolean,
    DateTime, Date, ForeignKey, Numeric,
    UniqueConstraint,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from .db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)

    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    two_factor_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # basic | vip | manager | employee | employee2 | ai_agent | tester
    account_type: Mapped[str] = mapped_column(String(16), nullable=False, default="basic")

    non_us_citizen_confirmed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("FALSE"),
    )
    non_us_citizen_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    totp_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("FALSE"),
    )
    totp_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    totp_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    totp_last_used_step: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    cookie_notice_acknowledged: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("FALSE"),
    )
    cookie_notice_acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    backup_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_backup_email_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # stage 9.2: compliance gate
    compliance_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=sa_text("'ok'"),
    )
    compliance_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    compliance_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserTotpRecoveryCode(Base):
    __tablename__ = "user_totp_recovery_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_used: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("FALSE"),
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class SecurityCode(Base):
    __tablename__ = "security_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)


class SessionModel(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)  # session_id token
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserWallet(Base):
    __tablename__ = "user_wallets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    blockchain: Mapped[str] = mapped_column(String(32), nullable=False, default="BSC")
    address: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_private_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # stage 9.1: USDT balance tracking (updated by workers)
    usdt_balance: Mapped[Decimal] = mapped_column(
        Numeric(38, 18),
        nullable=False,
        server_default=sa_text("0"),
    )
    usdt_balance_updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    usdt_balance_block: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    usdt_reserved: Mapped[Decimal] = mapped_column(
        Numeric(38, 18),
        nullable=False,
        server_default=sa_text("0"),
    )

    # stage 9.2: wallet compliance
    compliance_status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=sa_text("'ok'"),
    )
    freeze_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    compliance_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=sa_text("TRUE"))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WalletTransfer(Base):
    __tablename__ = "wallet_transfers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    wallet_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_wallets.id", ondelete="CASCADE"),
        nullable=False,
    )

    coin: Mapped[str] = mapped_column(String(16), nullable=False, default="USDT")
    network: Mapped[str] = mapped_column(String(32), nullable=False, default="BSC (BEP20)")
    type: Mapped[str] = mapped_column(String(16), nullable=False)  # deposit | withdraw

    from_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_address: Mapped[str | None] = mapped_column(String(64), nullable=True)

    tx_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    log_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)

    amount_gross: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    fee_usdt: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False, server_default=sa_text("1"))
    gas_tx_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    fee_tx_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    email_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_gas_alert_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending|processing|waiting_for_gas|success|failed
    compliance_status: Mapped[str | None] = mapped_column(String(32), nullable=True)

    block_number: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tx_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # stage 9.2: transfer compliance result
    compliance_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    compliance_details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint("tx_hash", "log_index", name="wallet_transfers_tx_hash_log_index_uq"),
    )


class FeeWalletSwap(Base):
    __tablename__ = "fee_wallet_swaps"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    wallet_type: Mapped[str] = mapped_column(String(16), nullable=False)  # ok | blocked
    wallet_address: Mapped[str] = mapped_column(String(64), nullable=False)

    token_in: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=sa_text("'USDT'"),
    )
    token_out: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=sa_text("'BNB'"),
    )

    amount_in_usdt: Mapped[Decimal] = mapped_column(
        Numeric(38, 18),
        nullable=False,
        server_default=sa_text("0"),
    )
    amount_out_bnb: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)

    tx_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=sa_text("'pending'"),
    )

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_gas_alert_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WithdrawSession(Base):
    __tablename__ = "withdraw_sessions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    wallet_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("user_wallets.id", ondelete="CASCADE"),
        nullable=False,
    )

    to_address: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_gross: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    fee_usdt: Mapped[Decimal] = mapped_column(
        Numeric(38, 18),
        nullable=False,
        server_default=sa_text("1"),
    )
    email_slot: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PasswordResetSession(Base):
    __tablename__ = "password_reset_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    is_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class Fund(Base):
    __tablename__ = "funds"

    id = Column(Integer, primary_key=True)
    code = Column(String(32), unique=True, nullable=False)

    name_ru = Column(String(100), nullable=False)
    name_en = Column(String(100), nullable=False)

    short_name_ru = Column(String(100), nullable=True)
    short_name_en = Column(String(100), nullable=True)

    full_name_ru = Column(String(150), nullable=True)
    full_name_en = Column(String(150), nullable=True)

    benchmark_name_ru = Column(String(150), nullable=True)
    benchmark_name_en = Column(String(150), nullable=True)

    management_fee_pct = Column(Numeric(10, 4), nullable=True)
    performance_fee_pct = Column(Numeric(10, 4), nullable=True)

    icon_name = Column(String(120), nullable=True)
    launch_date = Column(Date, nullable=True)
    shares_outstanding_current = Column(
        Numeric(30, 10),
        nullable=False,
        server_default=sa_text("0"),
    )

    category = Column(String(16), nullable=False)  # 'active', 'index', 'test'
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)


class FundWallet(Base):
    __tablename__ = "fund_wallets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    fund_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )

    blockchain: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=sa_text("'BSC'"),
    )

    wallet_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=sa_text("'settlement'"),
    )

    address: Mapped[str] = mapped_column(String(64), nullable=False)
    encrypted_private_key: Mapped[str] = mapped_column(Text, nullable=False)

    derivation_path: Mapped[str | None] = mapped_column(String(128), nullable=True)
    derivation_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("TRUE"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FundBybitAccount(Base):
    __tablename__ = "fund_bybit_accounts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    fund_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )

    bybit_sub_uid: Mapped[str] = mapped_column(String(64), nullable=False)
    bybit_subaccount_name: Mapped[str | None] = mapped_column(String(128), nullable=True)

    coin: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=sa_text("'USDT'"),
    )

    chain: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chain_type: Mapped[str] = mapped_column(String(64), nullable=False)

    deposit_address: Mapped[str] = mapped_column(String(128), nullable=False)
    deposit_tag: Mapped[str | None] = mapped_column(String(128), nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("TRUE"),
    )

    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_permissions: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_ip_whitelist: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    api_key_added_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    api_key_last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    api_key_is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("FALSE"),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class FundNavMinute(Base):
    __tablename__ = "fund_nav_minute"

    id = Column(BigInteger, primary_key=True)
    fund_id = Column(Integer, ForeignKey("funds.id", ondelete="CASCADE"), nullable=False)
    ts_utc = Column(DateTime(timezone=True), nullable=False)

    # было: price_usdt
    nav_usdt = Column(Numeric(30, 10), nullable=False)
    shares_outstanding = Column(Numeric(30, 10), nullable=False)


class FundNavGuardState(Base):
    __tablename__ = "fund_nav_guard_state"

    fund_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )

    last_snapshot_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    nav_usd: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    uta_equity_usd: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    funding_wallet_usd: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    earn_usd: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)

    source: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=sa_text("'bybit_v5'"),
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class FundNavGuardEvent(Base):
    __tablename__ = "fund_nav_guard_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    fund_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )

    snapshot_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    decision: Mapped[str] = mapped_column(String(16), nullable=False)  # accepted | warning | rejected
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    old_nav_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    old_uta_equity_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    old_funding_wallet_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    old_earn_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    new_nav_usd: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    new_uta_equity_usd: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    new_funding_wallet_usd: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    new_earn_usd: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)

    nav_drop_pct: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    earn_drop_pct: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)
    compensation_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 8), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class FundChartDaily(Base):
    __tablename__ = "fund_chart_daily"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fund_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    open: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    volume: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    __table_args__ = (
        UniqueConstraint("fund_id", "ts_utc", name="fund_chart_daily_fund_ts_uq"),
    )


class FundChartMinute(Base):
    __tablename__ = "fund_chart_minute"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fund_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )
    ts_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    open: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    high: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    low: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    close: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    volume: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    __table_args__ = (
        UniqueConstraint("fund_id", "ts_utc", name="fund_chart_minute_fund_ts_uq"),
    )


class FundOrder(Base):
    __tablename__ = "fund_orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    fund_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )

    side: Mapped[str] = mapped_column(String(16), nullable=False)  # buy | redeem

    amount_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    shares: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    price_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    # stage 23.1: negative-net redeem fee / payout audit
    gross_redeem_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    success_fee_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    management_fee_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    partial_month_fee_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    net_user_payout_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    net_price_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    fee_calc_month_open_price_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    fee_calc_days_in_month_period: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success_fee_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    management_fee_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)

    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=sa_text("'pending'"),
    )

    settlement_batch_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("fund_settlement_batches.id", ondelete="SET NULL"),
        nullable=True,
    )

    reserved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settlement_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collection_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class FundSettlementBatch(Base):
    __tablename__ = "fund_settlement_batches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    fund_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )

    settlement_date: Mapped[date] = mapped_column(Date, nullable=False)
    cutoff_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    settlement_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    price_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settlement_price_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    nav_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    shares_outstanding_before: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    total_buy_usdt: Mapped[Decimal] = mapped_column(
        Numeric(30, 10),
        nullable=False,
        server_default=sa_text("0"),
    )
    total_redeem_shares: Mapped[Decimal] = mapped_column(
        Numeric(30, 10),
        nullable=False,
        server_default=sa_text("0"),
    )
    total_redeem_usdt: Mapped[Decimal] = mapped_column(
        Numeric(30, 10),
        nullable=False,
        server_default=sa_text("0"),
    )
    net_cash_usdt: Mapped[Decimal] = mapped_column(
        Numeric(30, 10),
        nullable=False,
        server_default=sa_text("0"),
    )

    # stage 23.1: negative-net fee totals / withdrawal targets
    total_gross_redeem_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    total_net_user_payout_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    total_success_fee_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    total_management_fee_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    total_partial_month_fee_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    bybit_withdrawal_fee_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    required_master_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    withdrawal_request_amount_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    negative_net_target_calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fee_calc_month_open_price_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    fee_calc_month_open_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fee_calc_days_in_month_period: Mapped[int | None] = mapped_column(Integer, nullable=True)

    planned_shares_to_issue: Mapped[Decimal] = mapped_column(
        Numeric(30, 10),
        nullable=False,
        server_default=sa_text("0"),
    )
    planned_shares_to_redeem: Mapped[Decimal] = mapped_column(
        Numeric(30, 10),
        nullable=False,
        server_default=sa_text("0"),
    )
    planned_net_shares_change: Mapped[Decimal] = mapped_column(
        Numeric(30, 10),
        nullable=False,
        server_default=sa_text("0"),
    )

    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=sa_text("'created'"),
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    positive_net_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    seller_payouts_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    bybit_deposit_tx_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    bybit_deposit_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    bybit_deposit_account_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    bybit_internal_transfer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bybit_internal_transfer_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    accounting_finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    pricing_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pricing_unlocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("fund_id", "settlement_date", name="fund_settlement_batches_fund_date_uq"),
    )


class FundNegativeSaleBatch(Base):
    __tablename__ = "fund_negative_sale_batches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    settlement_batch_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fund_settlement_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    fund_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=sa_text("'snapshot_created'"),
    )

    required_master_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    withdrawal_request_amount_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    total_net_user_payout_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    total_partial_month_fee_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    bybit_withdrawal_fee_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    unified_usdt_available: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    fund_wallet_usdt_available: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    usdt_earn_available: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    total_cash_like_available_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    sale_target_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    planned_sale_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    expected_shortage_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    expected_surplus_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    largest_extra_sale_buffer_pct: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)

    snapshot_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    plan_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    report_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    snapshot_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    plan_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # stage 23.3: mock sale execution / reconciliation
    execution_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    available_usdt_before_execution: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    initial_cash_like_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    usdt_earn_redeemed_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    initial_sale_executed_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    available_usdt_after_initial_sales: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    shortage_after_initial_sales_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    extra_sale_required_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    extra_sale_target_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    extra_sale_executed_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    final_available_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    final_shortage_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    final_surplus_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    execution_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reconciliation_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    settlement_batch: Mapped["FundSettlementBatch"] = relationship(
        "FundSettlementBatch",
        foreign_keys=[settlement_batch_id],
    )
    fund: Mapped["Fund"] = relationship(
        "Fund",
        foreign_keys=[fund_id],
    )
    sale_legs: Mapped[list["FundNegativeSaleLeg"]] = relationship(
        "FundNegativeSaleLeg",
        back_populates="sale_batch",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "settlement_batch_id",
            name="fund_negative_sale_batches_settlement_uq",
        ),
    )


class FundNegativeBybitFlow(Base):
    __tablename__ = "fund_negative_bybit_flows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    settlement_batch_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fund_settlement_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    sale_batch_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fund_negative_sale_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    fund_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=sa_text("'created'"),
    )

    coin: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=sa_text("'USDT'"),
    )
    chain: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=sa_text("'BSC'"),
    )

    required_master_usdt: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    withdrawal_request_amount_usdt: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    bybit_withdrawal_fee_usdt: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    retained_fees_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    settlement_wallet_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("fund_wallets.id", ondelete="SET NULL"),
        nullable=True,
    )
    settlement_wallet_address: Mapped[str | None] = mapped_column(String(128), nullable=True)

    preflight_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    preflight_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    preflight_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    from_sub_uid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_master_uid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    from_account_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_account_type: Mapped[str | None] = mapped_column(String(32), nullable=True)

    universal_transfer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    universal_transfer_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    universal_transfer_amount_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    universal_transfer_coin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    universal_transfer_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    universal_transfer_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    universal_transfer_mock_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    universal_transfer_reconciliation_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    withdrawal_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    withdrawal_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    withdrawal_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    withdrawal_amount_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    withdrawal_fee_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    withdrawal_coin: Mapped[str | None] = mapped_column(String(16), nullable=True)
    withdrawal_chain: Mapped[str | None] = mapped_column(String(32), nullable=True)
    withdrawal_address: Mapped[str | None] = mapped_column(String(128), nullable=True)
    withdrawal_tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    withdrawal_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawal_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawal_mock_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    withdrawal_record_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    withdrawal_reconciliation_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    settlement_wallet_receipt_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    settlement_wallet_received_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    settlement_wallet_receipt_tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    settlement_wallet_receipt_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    settlement_wallet_receipt_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    reconciliation_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    report_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    settlement_batch: Mapped["FundSettlementBatch"] = relationship(
        "FundSettlementBatch",
        foreign_keys=[settlement_batch_id],
    )
    sale_batch: Mapped["FundNegativeSaleBatch"] = relationship(
        "FundNegativeSaleBatch",
        foreign_keys=[sale_batch_id],
    )
    fund: Mapped["Fund"] = relationship(
        "Fund",
        foreign_keys=[fund_id],
    )
    settlement_wallet: Mapped["FundWallet | None"] = relationship(
        "FundWallet",
        foreign_keys=[settlement_wallet_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "settlement_batch_id",
            name="fund_negative_bybit_flows_settlement_uq",
        ),
        UniqueConstraint(
            "sale_batch_id",
            name="fund_negative_bybit_flows_sale_batch_uq",
        ),
    )


class FundNegativePayoutBatch(Base):
    __tablename__ = "fund_negative_payout_batches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    settlement_batch_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fund_settlement_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    bybit_flow_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fund_negative_bybit_flows.id", ondelete="CASCADE"),
        nullable=False,
    )
    fund_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=sa_text("'created'"),
    )
    coin: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=sa_text("'USDT'"),
    )
    chain: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=sa_text("'BSC'"),
    )

    settlement_wallet_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("fund_wallets.id", ondelete="SET NULL"),
        nullable=True,
    )
    settlement_wallet_address: Mapped[str | None] = mapped_column(String(128), nullable=True)

    expected_total_payout_usdt: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)
    planned_total_payout_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    confirmed_total_payout_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    payout_leg_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confirmed_payout_leg_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    gas_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    settlement_wallet_bnb_before: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    settlement_wallet_bnb_required: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    settlement_wallet_bnb_after: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    ok_gas_wallet_bnb_available: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    gas_topup_required_bnb: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    gas_topup_amount_bnb: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    gas_topup_tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    gas_topup_mock_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    gas_reconciliation_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    operator_action_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("fund_operator_actions.id", ondelete="SET NULL"),
        nullable=True,
    )
    pause_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)

    payout_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payout_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    settlement_wallet_usdt_before: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    settlement_wallet_usdt_after: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    balance_refresh_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    balance_refresh_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    balance_refresh_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    balance_refresh_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    payout_plan_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    payout_execution_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reconciliation_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    report_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    settlement_batch: Mapped["FundSettlementBatch"] = relationship(
        "FundSettlementBatch",
        foreign_keys=[settlement_batch_id],
    )
    bybit_flow: Mapped["FundNegativeBybitFlow"] = relationship(
        "FundNegativeBybitFlow",
        foreign_keys=[bybit_flow_id],
    )
    fund: Mapped["Fund"] = relationship(
        "Fund",
        foreign_keys=[fund_id],
    )
    settlement_wallet: Mapped["FundWallet | None"] = relationship(
        "FundWallet",
        foreign_keys=[settlement_wallet_id],
    )
    payout_legs: Mapped[list["FundNegativePayoutLeg"]] = relationship(
        "FundNegativePayoutLeg",
        back_populates="payout_batch",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "settlement_batch_id",
            name="fund_negative_payout_batches_settlement_uq",
        ),
        UniqueConstraint(
            "bybit_flow_id",
            name="fund_negative_payout_batches_bybit_flow_uq",
        ),
    )


class FundNegativePayoutLeg(Base):
    __tablename__ = "fund_negative_payout_legs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    payout_batch_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fund_negative_payout_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    settlement_batch_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fund_settlement_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    bybit_flow_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fund_negative_bybit_flows.id", ondelete="CASCADE"),
        nullable=False,
    )
    fund_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )

    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_wallet_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("user_wallets.id", ondelete="SET NULL"),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=sa_text("'planned'"),
    )
    coin: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default=sa_text("'USDT'"),
    )
    chain: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=sa_text("'BSC'"),
    )

    from_settlement_wallet_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("fund_wallets.id", ondelete="SET NULL"),
        nullable=True,
    )
    from_address: Mapped[str | None] = mapped_column(String(128), nullable=True)

    to_user_wallet_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("user_wallets.id", ondelete="SET NULL"),
        nullable=True,
    )
    to_address: Mapped[str | None] = mapped_column(String(128), nullable=True)

    amount_usdt: Mapped[Decimal] = mapped_column(Numeric(30, 10), nullable=False)

    order_ids_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    order_allocations_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    deterministic_key: Mapped[str | None] = mapped_column(String(192), nullable=True)
    tx_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confirmations: Mapped[int | None] = mapped_column(Integer, nullable=True)

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    wallet_balance_before_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    wallet_balance_after_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    payout_mock_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    confirmation_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    balance_refresh_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    payout_batch: Mapped["FundNegativePayoutBatch"] = relationship(
        "FundNegativePayoutBatch",
        back_populates="payout_legs",
        foreign_keys=[payout_batch_id],
    )
    settlement_batch: Mapped["FundSettlementBatch"] = relationship(
        "FundSettlementBatch",
        foreign_keys=[settlement_batch_id],
    )
    bybit_flow: Mapped["FundNegativeBybitFlow"] = relationship(
        "FundNegativeBybitFlow",
        foreign_keys=[bybit_flow_id],
    )
    fund: Mapped["Fund"] = relationship(
        "Fund",
        foreign_keys=[fund_id],
    )
    user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[user_id],
    )
    user_wallet: Mapped["UserWallet | None"] = relationship(
        "UserWallet",
        foreign_keys=[user_wallet_id],
    )
    to_user_wallet: Mapped["UserWallet | None"] = relationship(
        "UserWallet",
        foreign_keys=[to_user_wallet_id],
    )
    settlement_wallet: Mapped["FundWallet | None"] = relationship(
        "FundWallet",
        foreign_keys=[from_settlement_wallet_id],
    )


class FundNegativeFinalizationBatch(Base):
    __tablename__ = "fund_negative_finalization_batches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    settlement_batch_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fund_settlement_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    payout_batch_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fund_negative_payout_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    bybit_flow_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("fund_negative_bybit_flows.id", ondelete="SET NULL"),
        nullable=True,
    )
    sale_batch_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("fund_negative_sale_batches.id", ondelete="SET NULL"),
        nullable=True,
    )
    fund_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=sa_text("'created'"),
    )

    settlement_price_usdt: Mapped[Decimal] = mapped_column(
        Numeric(30, 10),
        nullable=False,
    )
    shares_outstanding_before: Mapped[Decimal] = mapped_column(
        Numeric(30, 10),
        nullable=False,
    )
    shares_outstanding_after: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10),
        nullable=True,
    )

    buy_order_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    redeem_order_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    success_order_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    total_buy_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    total_buy_shares: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    total_redeem_shares: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    planned_net_shares_change: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    actual_net_shares_change: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    total_net_user_payout_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    total_partial_month_fee_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    positions_before_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    positions_after_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    user_wallet_reserves_before_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    user_wallet_reserves_after_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    order_updates_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    fund_update_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    pricing_lock_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    validation_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    accounting_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    reconciliation_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    report_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    finalization_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    accounting_finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    pricing_unlocked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    settlement_batch: Mapped["FundSettlementBatch"] = relationship(
        "FundSettlementBatch",
        foreign_keys=[settlement_batch_id],
    )
    payout_batch: Mapped["FundNegativePayoutBatch"] = relationship(
        "FundNegativePayoutBatch",
        foreign_keys=[payout_batch_id],
    )
    bybit_flow: Mapped["FundNegativeBybitFlow | None"] = relationship(
        "FundNegativeBybitFlow",
        foreign_keys=[bybit_flow_id],
    )
    sale_batch: Mapped["FundNegativeSaleBatch | None"] = relationship(
        "FundNegativeSaleBatch",
        foreign_keys=[sale_batch_id],
    )
    fund: Mapped["Fund"] = relationship(
        "Fund",
        foreign_keys=[fund_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "settlement_batch_id",
            name="fund_negative_finalization_batches_settlement_uq",
        ),
        UniqueConstraint(
            "payout_batch_id",
            name="fund_negative_finalization_batches_payout_uq",
        ),
    )


class FundOperationGuardState(Base):
    __tablename__ = "fund_operation_guard_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    scope_key: Mapped[str] = mapped_column(String(128), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)

    fund_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=True,
    )

    action_type: Mapped[str] = mapped_column(String(64), nullable=False)

    mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=sa_text("'blocked'"),
    )

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    updated_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    fund: Mapped["Fund | None"] = relationship(
        "Fund",
        foreign_keys=[fund_id],
    )
    updated_by_user: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[updated_by_user_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "scope_key",
            "action_type",
            name="fund_operation_guard_state_scope_action_uq",
        ),
    )


class FundOperationGuardOverride(Base):
    __tablename__ = "fund_operation_guard_overrides"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    scope_key: Mapped[str] = mapped_column(String(128), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)

    fund_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=True,
    )

    action_type: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=sa_text("'active'"),
    )

    manager_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    settlement_batch_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("fund_settlement_batches.id", ondelete="SET NULL"),
        nullable=True,
    )

    request_id: Mapped[str | None] = mapped_column(String(192), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(192), nullable=False)

    max_amount_usdt: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 10),
        nullable=True,
    )

    starts_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    payload_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    fund: Mapped["Fund | None"] = relationship(
        "Fund",
        foreign_keys=[fund_id],
    )
    manager_user: Mapped["User"] = relationship(
        "User",
        foreign_keys=[manager_user_id],
    )
    settlement_batch: Mapped["FundSettlementBatch | None"] = relationship(
        "FundSettlementBatch",
        foreign_keys=[settlement_batch_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="fund_operation_guard_overrides_idempotency_uq",
        ),
    )


class FundOperationGuardEvent(Base):
    __tablename__ = "fund_operation_guard_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)

    fund_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="SET NULL"),
        nullable=True,
    )

    settlement_batch_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("fund_settlement_batches.id", ondelete="SET NULL"),
        nullable=True,
    )

    request_id: Mapped[str | None] = mapped_column(String(192), nullable=True)
    amount_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    guard_state_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("fund_operation_guard_state.id", ondelete="SET NULL"),
        nullable=True,
    )
    override_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("fund_operation_guard_overrides.id", ondelete="SET NULL"),
        nullable=True,
    )

    mode_snapshot: Mapped[str | None] = mapped_column(String(32), nullable=True)
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    fund: Mapped["Fund | None"] = relationship(
        "Fund",
        foreign_keys=[fund_id],
    )
    settlement_batch: Mapped["FundSettlementBatch | None"] = relationship(
        "FundSettlementBatch",
        foreign_keys=[settlement_batch_id],
    )
    guard_state: Mapped["FundOperationGuardState | None"] = relationship(
        "FundOperationGuardState",
        foreign_keys=[guard_state_id],
    )
    override: Mapped["FundOperationGuardOverride | None"] = relationship(
        "FundOperationGuardOverride",
        foreign_keys=[override_id],
    )


class FundNegativeSaleLeg(Base):
    __tablename__ = "fund_negative_sale_legs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    sale_batch_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fund_negative_sale_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    settlement_batch_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fund_settlement_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    fund_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )

    leg_index: Mapped[int] = mapped_column(Integer, nullable=False)
    leg_group: Mapped[str] = mapped_column(String(64), nullable=False)
    leg_type: Mapped[str] = mapped_column(String(64), nullable=False)

    coin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(64), nullable=True)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    side: Mapped[str | None] = mapped_column(String(16), nullable=True)
    location: Mapped[str | None] = mapped_column(String(64), nullable=True)

    current_qty: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    current_size: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    current_usd_value: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    current_notional_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    source_weight: Mapped[Decimal | None] = mapped_column(Numeric(30, 18), nullable=True)

    target_cash_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    target_qty: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    expected_cash_delta_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    eligible: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("TRUE"),
    )
    eligibility_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    use_for_deficit_cover: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("TRUE"),
    )

    instrument_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    min_order_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    liquidity_check_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    margin_guard_required: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    planned_execution_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    order_link_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    strategy_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # stage 23.3: mock sale leg execution / fills
    actual_execution_mode: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_round: Mapped[str | None] = mapped_column(String(64), nullable=True)
    deterministic_key: Mapped[str | None] = mapped_column(String(160), nullable=True)

    bybit_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bybit_strategy_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    planned_suborders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    executed_suborders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    suborders_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    mock_execution_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    last_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    best_bid: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    best_ask: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    corridor_pct: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)

    available_liquidity_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    available_liquidity_qty: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)

    filled_qty: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    filled_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    fill_ratio: Mapped[Decimal | None] = mapped_column(Numeric(18, 10), nullable=True)
    unfilled_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    fee_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    cash_delta_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    execution_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=sa_text("'planned'"),
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    sale_batch: Mapped["FundNegativeSaleBatch"] = relationship(
        "FundNegativeSaleBatch",
        back_populates="sale_legs",
        foreign_keys=[sale_batch_id],
    )
    settlement_batch: Mapped["FundSettlementBatch"] = relationship(
        "FundSettlementBatch",
        foreign_keys=[settlement_batch_id],
    )
    fund: Mapped["Fund"] = relationship(
        "Fund",
        foreign_keys=[fund_id],
    )

    __table_args__ = (
        UniqueConstraint(
            "sale_batch_id",
            "leg_index",
            name="fund_negative_sale_legs_batch_index_uq",
        ),
    )


class FundSettlementTransfer(Base):
    __tablename__ = "fund_settlement_transfers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    batch_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fund_settlement_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    order_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("fund_orders.id", ondelete="SET NULL"),
        nullable=True,
    )
    fund_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    transfer_type: Mapped[str] = mapped_column(String(64), nullable=False)

    from_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    to_address: Mapped[str | None] = mapped_column(String(64), nullable=True)

    amount_usdt: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    amount_bnb: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)

    gas_tx_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    tx_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=sa_text("'pending'"),
    )
    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=sa_text("0"),
    )

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_gas_alert_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class PlatformEmergencyLock(Base):
    __tablename__ = "platform_emergency_locks"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=sa_text("'active'"),
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_event_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolve_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class ApprovedBybitWithdrawalWindow(Base):
    __tablename__ = "approved_bybit_withdrawal_windows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    scope: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=sa_text("'global'"),
    )
    fund_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="SET NULL"),
        nullable=True,
    )

    coin: Mapped[str] = mapped_column(String(32), nullable=False)
    chain: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address: Mapped[str | None] = mapped_column(String(256), nullable=True)

    amount_min: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    amount_max: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)

    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=sa_text("'active'"),
    )
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class BybitWithdrawalWatchdogEvent(Base):
    __tablename__ = "bybit_withdrawal_watchdog_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    bybit_withdrawal_id: Mapped[str] = mapped_column(String(128), nullable=False)

    coin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    chain: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address: Mapped[str | None] = mapped_column(String(256), nullable=True)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)

    bybit_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_detected: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=sa_text("'bybit_master_api'"),
    )

    approved_window_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("approved_bybit_withdrawal_windows.id", ondelete="SET NULL"),
        nullable=True,
    )

    decision: Mapped[str] = mapped_column(String(64), nullable=False)

    cancel_attempted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("FALSE"),
    )
    cancel_success: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cancel_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    raw_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class FundOperatorAction(Base):
    __tablename__ = "fund_operator_actions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    fund_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="SET NULL"),
        nullable=True,
    )
    settlement_batch_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("fund_settlement_batches.id", ondelete="SET NULL"),
        nullable=True,
    )
    allocation_batch_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("fund_allocation_batches.id", ondelete="SET NULL"),
        nullable=True,
    )

    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(128), nullable=True)

    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=sa_text("'pending'"),
    )

    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    callback_token_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    telegram_chat_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    telegram_user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    telegram_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    telegram_callback_query_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    requested_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    processing_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default=sa_text("0"),
    )

    payload_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "idempotency_key",
            name="fund_operator_actions_idempotency_uq",
        ),
    )


class FundAllocationBatch(Base):
    __tablename__ = "fund_allocation_batches"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    settlement_batch_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fund_settlement_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    fund_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )

    snapshot_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    positive_net_usdt: Mapped[Decimal] = mapped_column(
        Numeric(30, 10),
        nullable=False,
        server_default=sa_text("0"),
    )
    settlement_nav_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    snapshot_total_equity_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    base_nav_for_scale_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    scale: Mapped[Decimal | None] = mapped_column(Numeric(30, 18), nullable=True)

    snapshot_source: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=sa_text("'bybit_subaccount'"),
    )
    snapshot_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # stage 22.6.1: allocation finalization/reporting/timeline audit
    report_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    allocation_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconciliation_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reconciliation_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    alert_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    total_legs_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    filled_legs_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skipped_legs_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    partial_legs_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    failed_legs_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    active_legs_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    total_target_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    total_filled_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    total_residual_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    residual_earn_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    residual_cash_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=sa_text("'planned'"),
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "settlement_batch_id",
            name="fund_allocation_batches_settlement_batch_uq",
        ),
    )


class FundAllocationLeg(Base):
    __tablename__ = "fund_allocation_legs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    allocation_batch_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fund_allocation_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    settlement_batch_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("fund_settlement_batches.id", ondelete="CASCADE"),
        nullable=False,
    )
    fund_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_leg_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("fund_allocation_legs.id", ondelete="SET NULL"),
        nullable=True,
    )

    leg_index: Mapped[int] = mapped_column(Integer, nullable=False)
    leg_key: Mapped[str] = mapped_column(String(160), nullable=False)

    leg_group: Mapped[str] = mapped_column(String(64), nullable=False)
    leg_type: Mapped[str] = mapped_column(String(64), nullable=False)

    coin: Mapped[str | None] = mapped_column(String(32), nullable=True)
    symbol: Mapped[str | None] = mapped_column(String(80), nullable=True)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    side: Mapped[str | None] = mapped_column(String(16), nullable=True)
    location: Mapped[str | None] = mapped_column(String(64), nullable=True)

    current_size: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    current_usd_value: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    current_notional_usd: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    source_weight: Mapped[Decimal | None] = mapped_column(Numeric(30, 18), nullable=True)

    target_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    target_qty: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)

    execution_mode: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=sa_text("'planned'"),
    )

    planned_suborders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    executed_suborders: Mapped[int | None] = mapped_column(Integer, nullable=True)

    order_link_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    bybit_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    strategy_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    earn_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    transfer_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    last_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    best_bid: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    best_ask: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    corridor_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 6), nullable=True)

    available_liquidity_qty: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    available_liquidity_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    required_qty: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    required_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    filled_qty: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    filled_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    avg_fill_price: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    fill_ratio: Mapped[Decimal | None] = mapped_column(Numeric(30, 18), nullable=True)

    fee_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    actual_cash_used_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    actual_margin_change_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    residual_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    # stage 22.5.1: margin/risk audit for derivative/options allocation legs
    account_im_rate_before: Mapped[Decimal | None] = mapped_column(Numeric(30, 18), nullable=True)
    account_mm_rate_before: Mapped[Decimal | None] = mapped_column(Numeric(30, 18), nullable=True)
    account_im_rate_after_est: Mapped[Decimal | None] = mapped_column(Numeric(30, 18), nullable=True)
    account_mm_rate_after_est: Mapped[Decimal | None] = mapped_column(Numeric(30, 18), nullable=True)

    total_equity_usdt_before: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    total_initial_margin_usdt_before: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    total_maintenance_margin_usdt_before: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    estimated_initial_margin_change_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)
    estimated_maintenance_margin_change_usdt: Mapped[Decimal | None] = mapped_column(Numeric(30, 10), nullable=True)

    margin_guard_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    margin_guard_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default=sa_text("'planned'"),
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "allocation_batch_id",
            "leg_index",
            name="fund_allocation_legs_batch_leg_index_uq",
        ),
        UniqueConstraint(
            "allocation_batch_id",
            "leg_key",
            name="fund_allocation_legs_batch_leg_key_uq",
        ),
    )


class FundRuntimeState(Base):
    __tablename__ = "fund_runtime_state"

    fund_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    )

    pricing_locked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=sa_text("FALSE"),
    )
    pricing_lock_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    pricing_lock_batch_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("fund_settlement_batches.id", ondelete="SET NULL"),
        nullable=True,
    )
    pricing_locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pricing_unlocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class UserFundPosition(Base):
    __tablename__ = "user_fund_positions"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    fund_id = Column(Integer, ForeignKey("funds.id", ondelete="CASCADE"), nullable=False)
    shares = Column(Numeric(30, 10), nullable=False, default=0)
    shares_reserved = Column(
        Numeric(30, 10),
        nullable=False,
        default=0,
        server_default=sa_text("0"),
    )

    __table_args__ = (
        UniqueConstraint("user_id", "fund_id", name="user_fund_positions_unique"),
    )


class UserFundPositionStats(Base):
    __tablename__ = "user_fund_position_stats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)

    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    fund_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("funds.id", ondelete="CASCADE"),
        nullable=False,
    )

    avg_entry_price_usdt: Mapped[Decimal] = mapped_column(
        Numeric(30, 10),
        nullable=False,
        server_default=sa_text("0"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("user_id", "fund_id", name="user_fund_position_stats_user_fund_uq"),
    )


class UserPortfolioDaily(Base):
    __tablename__ = "user_portfolio_daily"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    date_utc = Column(Date, nullable=False)
    balance_usdt = Column(Numeric(30, 10), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("user_id", "date_utc", name="user_portfolio_daily_unique"),
    )
