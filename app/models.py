from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Column, Integer, BigInteger, SmallInteger, String, Text, Boolean,
    DateTime, Date, ForeignKey, Numeric,
    UniqueConstraint,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
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

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending|processing|success|failed
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
