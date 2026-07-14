from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    Fund,
    FundOrder,
    User,
    UserFundPosition,
    UserWallet,
)
from app.trading.order_gate import (
    ORDER_ENTRY_DISABLED_ERROR_KEY,
    is_order_entry_enabled_for_fund_code,
)
from app.settlement.statuses import (
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
    ORDER_STATUS_PENDING,
    ORDER_STATUS_PROCESSING,
    ORDER_STATUS_SETTLING,
)
from app.settlement.share_quantity import (
    RedeemSharePrecisionError,
    ShareQuantityError,
    require_share_quantity_4dp_aligned,
    validate_redeem_share_input_precision,
)


class TradingOrderError(ValueError):
    def __init__(self, error_key: str):
        self.error_key = error_key
        super().__init__(error_key)


ACTIVE_REDEEM_ORDER_STATUSES = {
    ORDER_STATUS_PENDING,
    ORDER_STATUS_SETTLING,
    ORDER_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
    ORDER_STATUS_PROCESSING,
}


def _active_redeem_order_exists(
    db: Session,
    *,
    user_id: int,
    fund_id: int,
) -> bool:
    return (
        db.query(FundOrder.id)
        .filter(
            FundOrder.user_id == int(user_id),
            FundOrder.fund_id == int(fund_id),
            FundOrder.side == ORDER_SIDE_REDEEM,
            FundOrder.status.in_(sorted(ACTIVE_REDEEM_ORDER_STATUSES)),
        )
        .with_for_update()
        .first()
        is not None
    )


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_decimal(value: Any, *, error_key: str) -> Decimal:
    if value is None:
        raise TradingOrderError(error_key)

    try:
        d = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError, AttributeError):
        raise TradingOrderError(error_key)

    if not d.is_finite():
        raise TradingOrderError(error_key)

    if d <= 0:
        raise TradingOrderError(error_key)

    return d


def validate_buy_amount_limits(amount: Decimal) -> None:
    if amount < settings.TRADING_BUY_MIN_USDT:
        raise TradingOrderError("buy_amount_below_min")

    if amount > settings.TRADING_BUY_MAX_USDT:
        raise TradingOrderError("buy_amount_above_max")


def validate_redeem_shares_limits(shares: Decimal) -> None:
    if shares > settings.TRADING_REDEEM_MAX_SHARES:
        raise TradingOrderError("redeem_shares_above_max")


def _dec(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _get_active_fund(db: Session, fund_code: str) -> Fund:
    code = (fund_code or "").strip().lower()
    if not code:
        raise TradingOrderError("fund_not_found")

    fund = (
        db.query(Fund)
        .filter(
            Fund.is_active == True,
            func.lower(Fund.code) == code,
        )
        .first()
    )

    if fund is None:
        raise TradingOrderError("fund_not_found")

    return fund


def _enforce_order_entry_enabled(fund: Fund) -> None:
    """
    Stage 25 backend enforcement.

    Buy/Redeem order creation is temporarily allowed only for funds listed in
    ORDER_ENTRY_ENABLED_FUND_CODES. This is backend protection against direct
    POST bypass; it runs before reserves, fund_orders or settlement state changes.
    """
    if not is_order_entry_enabled_for_fund_code(fund.code):
        raise TradingOrderError(ORDER_ENTRY_DISABLED_ERROR_KEY)


def _validate_user_for_buy(user: User) -> None:
    if user is None:
        raise TradingOrderError("not_authenticated")

    if not bool(getattr(user, "is_active", False)):
        raise TradingOrderError("trading_unavailable")

    if getattr(user, "compliance_status", "ok") != "ok":
        raise TradingOrderError("compliance_blocked")


def _validate_user_for_redeem(user: User) -> None:
    if user is None:
        raise TradingOrderError("not_authenticated")

    if not bool(getattr(user, "is_active", False)):
        raise TradingOrderError("trading_unavailable")


def _lock_active_user_wallet(db: Session, user_id: int) -> UserWallet:
    wallet = (
        db.query(UserWallet)
        .filter(
            UserWallet.user_id == user_id,
            UserWallet.blockchain == "BSC",
            UserWallet.is_active == True,
        )
        .with_for_update()
        .first()
    )

    if wallet is None:
        raise TradingOrderError("wallet_not_found")

    if getattr(wallet, "compliance_status", "ok") != "ok":
        raise TradingOrderError("compliance_blocked")

    return wallet


def _lock_user_position(db: Session, *, user_id: int, fund_id: int) -> UserFundPosition:
    position = (
        db.query(UserFundPosition)
        .filter(
            UserFundPosition.user_id == user_id,
            UserFundPosition.fund_id == fund_id,
        )
        .with_for_update()
        .first()
    )

    if position is None:
        raise TradingOrderError("insufficient_shares")

    return position


def get_available_usdt(wallet: UserWallet) -> Decimal:
    total = _dec(wallet.usdt_balance)
    reserved = _dec(wallet.usdt_reserved)
    available = total - reserved
    return max(available, Decimal("0"))


def get_available_shares(position: UserFundPosition) -> Decimal:
    total = _dec(position.shares)
    reserved = _dec(getattr(position, "shares_reserved", 0))
    available = total - reserved
    return max(available, Decimal("0"))


def _fund_name(fund: Fund, lang: str = "en") -> str:
    if lang == "ru":
        return str(fund.name_ru or fund.name_en or fund.code or "")
    return str(fund.name_en or fund.name_ru or fund.code or "")


def format_order_response(
    *,
    order: FundOrder,
    fund: Fund,
    lang: str = "en",
    usdt_available: Decimal | None = None,
    usdt_reserved: Decimal | None = None,
    shares_available: Decimal | None = None,
    shares_reserved: Decimal | None = None,
) -> dict:
    return {
        "status": "ok",
        "order": {
            "id": order.id,
            "fund_name": _fund_name(fund, lang),
            "fund_code": fund.code,
            "side": order.side,
            "amount_usdt": str(order.amount_usdt) if order.amount_usdt is not None else None,
            "shares": str(order.shares) if order.shares is not None else None,
            "price_usdt": str(order.price_usdt) if order.price_usdt is not None else None,
            "status": order.status,
            "created_at": (
                order.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                if order.created_at
                else None
            ),
            "executed_at": (
                order.executed_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                if order.executed_at
                else None
            ),
        },
        "balances": {
            "usdt_available": str(usdt_available) if usdt_available is not None else None,
            "usdt_reserved": str(usdt_reserved) if usdt_reserved is not None else None,
            "shares_available": str(shares_available) if shares_available is not None else None,
            "shares_reserved": str(shares_reserved) if shares_reserved is not None else None,
        },
    }


def create_buy_order(
    db: Session,
    user: User,
    fund_code: str,
    amount_usdt: Any,
    *,
    lang: str = "en",
) -> dict:
    """
    Create pending buy order and reserve user's USDT.

    Stage 20 restrictions:
    - no on-chain transfer;
    - no Bybit call;
    - no settlement;
    - no change to funds.shares_outstanding_current;
    - no user fund position update yet.
    """
    _validate_user_for_buy(user)

    amount = _to_decimal(amount_usdt, error_key="invalid_amount")
    validate_buy_amount_limits(amount)

    fund = _get_active_fund(db, fund_code)
    _enforce_order_entry_enabled(fund)

    try:
        wallet = _lock_active_user_wallet(db, user.id)

        available = get_available_usdt(wallet)
        if amount > available:
            raise TradingOrderError("insufficient_funds")

        wallet.usdt_reserved = _dec(wallet.usdt_reserved) + amount

        order = FundOrder(
            user_id=user.id,
            fund_id=fund.id,
            side="buy",
            amount_usdt=amount,
            shares=None,
            price_usdt=None,
            status="pending",
            created_at=utcnow(),
            executed_at=None,
        )

        db.add(wallet)
        db.add(order)
        db.commit()
        db.refresh(order)
        db.refresh(wallet)

        return format_order_response(
            order=order,
            fund=fund,
            lang=lang,
            usdt_available=get_available_usdt(wallet),
            usdt_reserved=_dec(wallet.usdt_reserved),
            shares_available=None,
            shares_reserved=None,
        )

    except TradingOrderError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise


def create_redeem_order(
    db: Session,
    user: User,
    fund_code: str,
    shares: Any,
    *,
    lang: str = "en",
    commit: bool = True,
) -> dict:
    """
    Create pending redeem order and reserve user's fund shares.

    Stage 20 restrictions:
    - do not reduce position.shares yet;
    - do not credit USDT yet;
    - no on-chain transfer;
    - no Bybit call;
    - no settlement;
    - no change to funds.shares_outstanding_current.
    """
    _validate_user_for_redeem(user)

    try:
        shares_dec = validate_redeem_share_input_precision(shares)
    except RedeemSharePrecisionError:
        raise TradingOrderError(
            "redeem_shares_precision_exceeded"
        )
    except ShareQuantityError:
        raise TradingOrderError("invalid_shares")

    validate_redeem_shares_limits(shares_dec)

    fund = _get_active_fund(db, fund_code)
    _enforce_order_entry_enabled(fund)

    try:
        position = _lock_user_position(
            db,
            user_id=user.id,
            fund_id=fund.id,
        )

        try:
            require_share_quantity_4dp_aligned(
                position.shares,
                field_name="position_shares",
            )
            require_share_quantity_4dp_aligned(
                getattr(position, "shares_reserved", 0),
                field_name="position_shares_reserved",
            )
        except ShareQuantityError:
            raise TradingOrderError(
                "share_quantity_not_4dp_aligned"
            )

        if _active_redeem_order_exists(
            db,
            user_id=int(user.id),
            fund_id=int(fund.id),
        ):
            raise TradingOrderError("active_redeem_order_exists")

        available = get_available_shares(position)
        if shares_dec > available:
            raise TradingOrderError("insufficient_shares")

        current_reserved = _dec(getattr(position, "shares_reserved", 0))

        new_reserved = current_reserved + shares_dec

        try:
            require_share_quantity_4dp_aligned(
                new_reserved,
                field_name="position_shares_reserved_after",
            )
        except ShareQuantityError:
            raise TradingOrderError(
                "share_quantity_not_4dp_aligned"
            )

        position.shares_reserved = new_reserved

        order = FundOrder(
            user_id=user.id,
            fund_id=fund.id,
            side="redeem",
            amount_usdt=None,
            shares=shares_dec,
            price_usdt=None,
            status="pending",
            created_at=utcnow(),
            executed_at=None,
        )

        db.add(position)
        db.add(order)

        if commit:
            db.commit()
        else:
            db.flush()

        db.refresh(order)
        db.refresh(position)

        return format_order_response(
            order=order,
            fund=fund,
            lang=lang,
            usdt_available=None,
            usdt_reserved=None,
            shares_available=get_available_shares(position),
            shares_reserved=_dec(getattr(position, "shares_reserved", 0)),
        )

    except TradingOrderError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise