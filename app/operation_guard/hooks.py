from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.operation_guard.service import OperationGuardDecision, require_operation_allowed
from app.operation_guard.statuses import (
    OP_GUARD_ACTION_BSC_POSITIVE_NET_TO_BYBIT,
    OP_GUARD_ACTION_BSC_REDEEM_PAYOUT,
    OP_GUARD_ACTION_BSC_SETTLEMENT_GAS_TOPUP,
    OP_GUARD_ACTION_BYBIT_MASTER_WITHDRAWAL,
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