from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.operation_guard.service import OperationGuardDecision, require_operation_allowed
from app.operation_guard.statuses import (
    OP_GUARD_ACTION_BSC_BUY_COLLECTION_GAS_TOPUP,
    OP_GUARD_ACTION_BSC_BUY_COLLECTION_USDT_TO_SETTLEMENT,
    OP_GUARD_ACTION_BSC_POSITIVE_NET_TO_BYBIT,
    OP_GUARD_ACTION_BSC_REDEEM_PAYOUT,
    OP_GUARD_ACTION_BSC_SETTLEMENT_GAS_TOPUP,
    OP_GUARD_ACTION_BYBIT_ALLOCATION_EARN_ORDER,
    OP_GUARD_ACTION_BYBIT_ALLOCATION_STRATEGY_ORDER,
    OP_GUARD_ACTION_BYBIT_ALLOCATION_TRADE_ORDER,
    OP_GUARD_ACTION_BYBIT_ALLOCATION_TRANSFER,
    OP_GUARD_ACTION_BYBIT_MASTER_WITHDRAWAL,
    OP_GUARD_ACTION_BYBIT_NEGATIVE_SALE_ORDER,
    OP_GUARD_ACTION_BYBIT_UNIVERSAL_TRANSFER,
)


def require_bybit_universal_transfer_guard(
    db: Session,
    *,
    fund_id: int,
    settlement_batch_id: int | None,
    amount_usdt: Decimal,
    request_id: str,
    metadata: dict[str, Any] | None = None,
) -> OperationGuardDecision:
    return require_operation_allowed(
        db,
        action_type=OP_GUARD_ACTION_BYBIT_UNIVERSAL_TRANSFER,
        fund_id=int(fund_id),
        settlement_batch_id=settlement_batch_id,
        amount_usdt=amount_usdt,
        request_id=request_id,
        metadata={
            "stage24_hook": "bybit_universal_transfer",
            "live_external_action": True,
            "whitelist_alone_is_insufficient": True,
            **(metadata or {}),
        },
    )


def require_bybit_master_withdrawal_guard(
    db: Session,
    *,
    fund_id: int,
    settlement_batch_id: int | None,
    amount_usdt: Decimal,
    request_id: str,
    metadata: dict[str, Any] | None = None,
) -> OperationGuardDecision:
    return require_operation_allowed(
        db,
        action_type=OP_GUARD_ACTION_BYBIT_MASTER_WITHDRAWAL,
        fund_id=int(fund_id),
        settlement_batch_id=settlement_batch_id,
        amount_usdt=amount_usdt,
        request_id=request_id,
        metadata={
            "stage24_hook": "bybit_master_withdrawal",
            "live_external_action": True,
            "whitelist_alone_is_insufficient": True,
            **(metadata or {}),
        },
    )


def require_bsc_settlement_gas_topup_guard(
    db: Session,
    *,
    fund_id: int,
    settlement_batch_id: int | None,
    request_id: str,
    amount_usdt: Decimal | None = None,
    metadata: dict[str, Any] | None = None,
) -> OperationGuardDecision:
    return require_operation_allowed(
        db,
        action_type=OP_GUARD_ACTION_BSC_SETTLEMENT_GAS_TOPUP,
        fund_id=int(fund_id),
        settlement_batch_id=settlement_batch_id,
        amount_usdt=amount_usdt,
        request_id=request_id,
        metadata={
            "stage24_hook": "bsc_settlement_gas_topup",
            "live_external_action": True,
            "whitelist_alone_is_insufficient": True,
            **(metadata or {}),
        },
    )


def require_bsc_redeem_payout_guard(
    db: Session,
    *,
    fund_id: int,
    settlement_batch_id: int | None,
    amount_usdt: Decimal,
    request_id: str,
    metadata: dict[str, Any] | None = None,
) -> OperationGuardDecision:
    return require_operation_allowed(
        db,
        action_type=OP_GUARD_ACTION_BSC_REDEEM_PAYOUT,
        fund_id=int(fund_id),
        settlement_batch_id=settlement_batch_id,
        amount_usdt=amount_usdt,
        request_id=request_id,
        metadata={
            "stage24_hook": "bsc_redeem_payout",
            "live_external_action": True,
            "whitelist_alone_is_insufficient": True,
            **(metadata or {}),
        },
    )


def require_bsc_positive_net_to_bybit_guard(
    db: Session,
    *,
    fund_id: int,
    settlement_batch_id: int | None,
    amount_usdt: Decimal,
    request_id: str,
    metadata: dict[str, Any] | None = None,
) -> OperationGuardDecision:
    return require_operation_allowed(
        db,
        action_type=OP_GUARD_ACTION_BSC_POSITIVE_NET_TO_BYBIT,
        fund_id=int(fund_id),
        settlement_batch_id=settlement_batch_id,
        amount_usdt=amount_usdt,
        request_id=request_id,
        metadata={
            "stage24_hook": "bsc_positive_net_to_bybit",
            "live_external_action": True,
            "whitelist_alone_is_insufficient": True,
            **(metadata or {}),
        },
    )


def require_bsc_buy_collection_gas_topup_guard(
    db: Session,
    *,
    fund_id: int,
    settlement_batch_id: int | None,
    request_id: str,
    amount_bnb: Decimal,
    metadata: dict[str, Any] | None = None,
) -> OperationGuardDecision:
    return require_operation_allowed(
        db,
        action_type=OP_GUARD_ACTION_BSC_BUY_COLLECTION_GAS_TOPUP,
        fund_id=int(fund_id),
        settlement_batch_id=settlement_batch_id,
        amount_usdt=None,
        request_id=request_id,
        metadata={
            "stage25_hook": "bsc_buy_collection_gas_topup",
            "live_external_action": True,
            "asset": "BNB",
            "amount_bnb": str(amount_bnb),
            "whitelist_alone_is_insufficient": True,
            **(metadata or {}),
        },
    )


def require_bsc_buy_collection_usdt_to_settlement_guard(
    db: Session,
    *,
    fund_id: int,
    settlement_batch_id: int | None,
    amount_usdt: Decimal,
    request_id: str,
    metadata: dict[str, Any] | None = None,
) -> OperationGuardDecision:
    return require_operation_allowed(
        db,
        action_type=OP_GUARD_ACTION_BSC_BUY_COLLECTION_USDT_TO_SETTLEMENT,
        fund_id=int(fund_id),
        settlement_batch_id=settlement_batch_id,
        amount_usdt=amount_usdt,
        request_id=request_id,
        metadata={
            "stage25_hook": "bsc_buy_collection_usdt_to_settlement",
            "live_external_action": True,
            "asset": "USDT",
            "whitelist_alone_is_insufficient": True,
            **(metadata or {}),
        },
    )


def require_bybit_negative_sale_order_guard(
    db: Session,
    *,
    fund_id: int,
    settlement_batch_id: int | None,
    amount_usdt: Decimal,
    request_id: str,
    metadata: dict[str, Any] | None = None,
) -> OperationGuardDecision:
    return require_operation_allowed(
        db,
        action_type=OP_GUARD_ACTION_BYBIT_NEGATIVE_SALE_ORDER,
        fund_id=int(fund_id),
        settlement_batch_id=settlement_batch_id,
        amount_usdt=amount_usdt,
        request_id=request_id,
        metadata={
            "stage25_hook": "bybit_negative_sale_order",
            "live_external_action": True,
            "asset": "USDT",
            "whitelist_alone_is_insufficient": True,
            **(metadata or {}),
        },
    )


def require_bybit_allocation_trade_order_guard(
    db: Session,
    *,
    fund_id: int,
    settlement_batch_id: int | None,
    amount_usdt: Decimal,
    request_id: str,
    metadata: dict[str, Any] | None = None,
) -> OperationGuardDecision:
    return require_operation_allowed(
        db,
        action_type=OP_GUARD_ACTION_BYBIT_ALLOCATION_TRADE_ORDER,
        fund_id=int(fund_id),
        settlement_batch_id=settlement_batch_id,
        amount_usdt=amount_usdt,
        request_id=request_id,
        metadata={
            "stage25_2_hook": "bybit_allocation_trade_order",
            "live_external_action": True,
            "asset": "USDT",
            "whitelist_alone_is_insufficient": True,
            **(metadata or {}),
        },
    )


def require_bybit_allocation_strategy_order_guard(
    db: Session,
    *,
    fund_id: int,
    settlement_batch_id: int | None,
    amount_usdt: Decimal,
    request_id: str,
    metadata: dict[str, Any] | None = None,
) -> OperationGuardDecision:
    return require_operation_allowed(
        db,
        action_type=OP_GUARD_ACTION_BYBIT_ALLOCATION_STRATEGY_ORDER,
        fund_id=int(fund_id),
        settlement_batch_id=settlement_batch_id,
        amount_usdt=amount_usdt,
        request_id=request_id,
        metadata={
            "stage25_2_hook": "bybit_allocation_strategy_order",
            "live_external_action": True,
            "asset": "USDT",
            "whitelist_alone_is_insufficient": True,
            **(metadata or {}),
        },
    )


def require_bybit_allocation_earn_order_guard(
    db: Session,
    *,
    fund_id: int,
    settlement_batch_id: int | None,
    amount_usdt: Decimal,
    request_id: str,
    metadata: dict[str, Any] | None = None,
) -> OperationGuardDecision:
    return require_operation_allowed(
        db,
        action_type=OP_GUARD_ACTION_BYBIT_ALLOCATION_EARN_ORDER,
        fund_id=int(fund_id),
        settlement_batch_id=settlement_batch_id,
        amount_usdt=amount_usdt,
        request_id=request_id,
        metadata={
            "stage25_2_hook": "bybit_allocation_earn_order",
            "live_external_action": True,
            "asset": "USDT",
            "whitelist_alone_is_insufficient": True,
            **(metadata or {}),
        },
    )


def require_bybit_allocation_transfer_guard(
    db: Session,
    *,
    fund_id: int,
    settlement_batch_id: int | None,
    amount_usdt: Decimal,
    request_id: str,
    metadata: dict[str, Any] | None = None,
) -> OperationGuardDecision:
    return require_operation_allowed(
        db,
        action_type=OP_GUARD_ACTION_BYBIT_ALLOCATION_TRANSFER,
        fund_id=int(fund_id),
        settlement_batch_id=settlement_batch_id,
        amount_usdt=amount_usdt,
        request_id=request_id,
        metadata={
            "stage25_2_hook": "bybit_allocation_transfer",
            "live_external_action": True,
            "asset": "USDT",
            "whitelist_alone_is_insufficient": True,
            **(metadata or {}),
        },
    )