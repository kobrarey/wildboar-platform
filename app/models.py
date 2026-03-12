from datetime import datetime, timezone
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
    account_type: Mapped[str] = mapped_column(String(16), nullable=False, default="basic")

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


class WithdrawSession(Base):
    __tablename__ = "withdraw_sessions"

    token: Mapped[str] = mapped_column(String(80), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    wallet_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("user_wallets.id", ondelete="CASCADE"), nullable=False)

    to_address: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_gross: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    fee_usdt: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False, server_default=sa_text("1"))
    email_slot: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
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
    category = Column(String(16), nullable=False)  # 'active', 'index', 'test'
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)


class FundNavMinute(Base):
    __tablename__ = "fund_nav_minute"

    id = Column(BigInteger, primary_key=True)
    fund_id = Column(Integer, ForeignKey("funds.id", ondelete="CASCADE"), nullable=False)
    ts_utc = Column(DateTime(timezone=True), nullable=False)

    # было: price_usdt
    nav_usdt = Column(Numeric(30, 10), nullable=False)
    shares_outstanding = Column(Numeric(30, 10), nullable=False)


class UserFundPosition(Base):
    __tablename__ = "user_fund_positions"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    fund_id = Column(Integer, ForeignKey("funds.id", ondelete="CASCADE"), nullable=False)
    shares = Column(Numeric(30, 10), nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("user_id", "fund_id", name="user_fund_positions_unique"),
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
