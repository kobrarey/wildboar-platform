from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any

from sqlalchemy import MetaData, Table, inspect
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    Fund,
    FundNegativeBybitFlow,
    FundNegativePayoutBatch,
    FundNegativePayoutLeg,
    FundOrder,
    FundSettlementBatch,
    FundWallet,
    UserFundPosition,
    UserWallet,
)
from app.operation_guard.hooks import (
    require_bsc_redeem_payout_guard,
    require_bsc_settlement_gas_topup_guard,
)
from app.operation_guard.service import OperationGuardBlockedError
from app.settlement.gas_service import (
    WEI_PER_BNB,
    get_bnb_balance,
    get_web3,
    send_native_bnb,
)
from app.settlement.transfer_service import _check_tx_confirmed, _send_usdt_transfer
from app.wallets import decrypt_private_key
from app.settlement.negative_payout_flow_types import (
    NegativePayoutFlowError,
    NegativePayoutMock,
    NegativePayoutResult,
    _json_dict,
    utcnow,
)
from app.settlement.negative_sale_snapshot import dec
from app.settlement.accounting_service import (
    SettlementShareQuantityError,
    validate_settlement_share_state_before_external,
)
from app.settlement.statuses import (
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
    BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING,
    BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED,
    BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED,
    BYBIT_FLOW_STATUS_COMPLETED,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_CANCELLED,
    ORDER_STATUS_FAILED,
    ORDER_STATUS_FAILED_REQUIRES_REVIEW,
    ORDER_STATUS_SUCCESS,
    PAYOUT_BALANCE_REFRESH_STATUS_CONFIRMED,
    PAYOUT_BALANCE_REFRESH_STATUS_FAILED_REQUIRES_REVIEW,
    PAYOUT_BALANCE_REFRESH_STATUS_MOCKED,
    PAYOUT_BALANCE_REFRESH_STATUS_NOT_STARTED,
    PAYOUT_BATCH_STATUS_BALANCE_REFRESH_MOCKED,
    PAYOUT_BATCH_STATUS_COMPLETED,
    PAYOUT_BATCH_STATUS_CREATED,
    PAYOUT_BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    PAYOUT_BATCH_STATUS_GAS_CHECK_PASSED,
    PAYOUT_BATCH_STATUS_GAS_READY,
    PAYOUT_BATCH_STATUS_GAS_TOPUP_MOCKED,
    PAYOUT_BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED,
    PAYOUT_BATCH_STATUS_PAYOUTS_CONFIRMED,
    PAYOUT_BATCH_STATUS_PAYOUTS_MOCKED,
    PAYOUT_BATCH_STATUS_PAYOUTS_PLANNED,
    PAYOUT_GAS_STATUS_FAILED_REQUIRES_REVIEW,
    PAYOUT_GAS_STATUS_INSUFFICIENT_OK_GAS,
    PAYOUT_GAS_STATUS_NOT_CHECKED,
    PAYOUT_GAS_STATUS_READY,
    PAYOUT_GAS_STATUS_SUFFICIENT,
    PAYOUT_GAS_STATUS_TOPUP_MOCKED,
    PAYOUT_GAS_STATUS_TOPUP_REQUIRED,
    PAYOUT_LEG_STATUS_BALANCE_REFRESHED,
    PAYOUT_LEG_STATUS_FAILED_REQUIRES_REVIEW,
    PAYOUT_LEG_STATUS_PAYOUT_CONFIRMED,
    PAYOUT_LEG_STATUS_PAYOUT_MOCKED,
    PAYOUT_LEG_STATUS_PLANNED,
)


ZERO = Decimal("0")


LIVE_RESUMABLE_PAYOUT_BATCH_STATUSES = {
    PAYOUT_BATCH_STATUS_CREATED,
    PAYOUT_BATCH_STATUS_GAS_CHECK_PASSED,
    PAYOUT_BATCH_STATUS_GAS_READY,
    PAYOUT_BATCH_STATUS_PAYOUTS_PLANNED,
    PAYOUT_BATCH_STATUS_GAS_TOPUP_MOCKED,
    PAYOUT_BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED,
}


def _q10(value: Any) -> Decimal:
    return dec(value).quantize(Decimal("0.0000000001"))


def _q18(value: Any) -> Decimal:
    return dec(value).quantize(Decimal("0.000000000000000001"))


def _same_decimal(left: Any, right: Any) -> bool:
    return _q10(left) == _q10(right)


def _mock_confirmed(value: str | None) -> bool:
    return str(value or "").strip().upper() == "CONFIRMED"


def _is_auto(value: Any) -> bool:
    return isinstance(value, str) and value.strip().upper() == "AUTO"


def deterministic_payout_key(
    *,
    settlement_batch_id: int,
    user_wallet_id: int,
    amount_usdt: Decimal,
) -> str:
    return (
        f"neg-net-payout:"
        f"{int(settlement_batch_id)}:"
        f"{int(user_wallet_id)}:"
        f"{_q10(amount_usdt)}"
    )


def deterministic_payout_tx_hash(
    *,
    prefix: str,
    payout_batch_id: int,
    payout_leg_id: int,
) -> str:
    cleaned_prefix = str(prefix or "0xmockpayout").strip() or "0xmockpayout"
    return f"{cleaned_prefix}{int(payout_batch_id)}_{int(payout_leg_id)}"


def deterministic_settlement_gas_topup_request_id(
    *,
    settlement_batch_id: int,
    payout_batch_id: int,
    amount_bnb: Decimal,
    to_address: str,
) -> str:
    return (
        f"neg-net-settlement-gas-topup:"
        f"{int(settlement_batch_id)}:"
        f"{int(payout_batch_id)}:"
        f"{_q18(amount_bnb)}:"
        f"{str(to_address).strip()}"
    )


def deterministic_redeem_payout_request_id(
    *,
    settlement_batch_id: int,
    payout_batch_id: int,
    payout_leg_id: int,
    user_wallet_id: int,
    amount_usdt: Decimal,
    to_address: str,
) -> str:
    return (
        f"neg-net-redeem-payout:"
        f"{int(settlement_batch_id)}:"
        f"{int(payout_batch_id)}:"
        f"{int(payout_leg_id)}:"
        f"{int(user_wallet_id)}:"
        f"{_q10(amount_usdt)}:"
        f"{str(to_address).strip()}"
    )


def _required_bnb_for_payout_legs(
    w3,
    *,
    leg_count: int,
) -> Decimal:
    gas_price_wei = Decimal(int(w3.eth.gas_price))
    fallback_gas = Decimal(int(settings.ERC20_TRANSFER_GAS_FALLBACK))
    buffer_mult = Decimal(settings.WITHDRAW_GAS_BUFFER_MULT)
    count = Decimal(max(1, int(leg_count)))

    return _q18((fallback_gas * gas_price_wei * buffer_mult * count) / WEI_PER_BNB)


def require_stage24_bsc_settlement_gas_topup_guard(
    db: Session,
    *,
    fund_id: int,
    settlement_batch_id: int,
    request_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Stage 24 integration hook.

    This must be called immediately before any future real settlement-wallet gas top-up.
    Stage 24 does not execute BSC gas top-up; Stage 25 live path must call this before
    the external action is attempted.
    """
    require_bsc_settlement_gas_topup_guard(
        db,
        fund_id=int(fund_id),
        settlement_batch_id=int(settlement_batch_id),
        request_id=request_id,
        amount_usdt=None,
        metadata={
            "source": "negative_payout_flow",
            "boundary": "future_real_settlement_wallet_gas_topup",
            "no_real_bsc_call_in_stage24": True,
            **(metadata or {}),
        },
    )


def require_stage24_bsc_redeem_payout_guard(
    db: Session,
    *,
    fund_id: int,
    settlement_batch_id: int,
    amount_usdt: Decimal,
    request_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    """
    Stage 24 integration hook.

    This must be called immediately before any future real BSC redeem payout.
    Stage 24 does not execute BSC payout; Stage 25 live path must call this before
    the external action is attempted.
    """
    require_bsc_redeem_payout_guard(
        db,
        fund_id=int(fund_id),
        settlement_batch_id=int(settlement_batch_id),
        amount_usdt=amount_usdt,
        request_id=request_id,
        metadata={
            "source": "negative_payout_flow",
            "boundary": "future_real_bsc_redeem_payout",
            "no_real_bsc_call_in_stage24": True,
            **(metadata or {}),
        },
    )


def _validate_stage23_5_safety(mock_payout: NegativePayoutMock) -> None:
    if settings.NEGATIVE_NET_PAYOUT_ALLOW_LIVE:
        raise NegativePayoutFlowError(
            "Live negative-net payout flow is blocked in Stage 23.5"
        )

    if not settings.NEGATIVE_NET_PAYOUT_MOCK_ONLY:
        raise NegativePayoutFlowError(
            "Stage 23.5 requires NEGATIVE_NET_PAYOUT_MOCK_ONLY=true"
        )

    if not mock_payout.mock_only:
        raise NegativePayoutFlowError("Stage 23.5 payout flow requires mock_only=true")

    if mock_payout.coin != settings.NEGATIVE_NET_PAYOUT_COIN:
        raise NegativePayoutFlowError("Stage 23.5 payout mock coin mismatch")

    if mock_payout.chain != settings.NEGATIVE_NET_PAYOUT_CHAIN:
        raise NegativePayoutFlowError("Stage 23.5 payout mock chain mismatch")

    if settings.NEGATIVE_NET_PAYOUT_COIN != "USDT":
        raise NegativePayoutFlowError("Stage 23.5 payout coin must be USDT")

    if settings.NEGATIVE_NET_PAYOUT_CHAIN != "BSC":
        raise NegativePayoutFlowError("Stage 23.5 payout chain must be BSC")


def _lock_settlement_batch(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundSettlementBatch:
    settlement_batch = (
        db.query(FundSettlementBatch)
        .filter(FundSettlementBatch.id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )
    if settlement_batch is None:
        raise NegativePayoutFlowError(f"Settlement batch not found: {settlement_batch_id}")

    return settlement_batch


def _lock_completed_bybit_flow(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundNegativeBybitFlow:
    bybit_flow = (
        db.query(FundNegativeBybitFlow)
        .filter(FundNegativeBybitFlow.settlement_batch_id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )
    if bybit_flow is None:
        raise NegativePayoutFlowError(
            f"Completed negative-net Bybit flow not found for settlement_batch_id={settlement_batch_id}"
        )

    return bybit_flow


def _lock_existing_payout_batch(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundNegativePayoutBatch | None:
    return (
        db.query(FundNegativePayoutBatch)
        .filter(FundNegativePayoutBatch.settlement_batch_id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )


def _get_fund(db: Session, *, fund_id: int) -> Fund:
    fund = db.query(Fund).filter(Fund.id == int(fund_id)).first()
    if fund is None:
        raise NegativePayoutFlowError(f"Fund not found: {fund_id}")

    return fund


def _validate_bybit_flow_input(
    *,
    settlement_batch: FundSettlementBatch,
    bybit_flow: FundNegativeBybitFlow,
    allow_completed_settlement_status: bool = False,
    allow_payout_processing_status: bool = False,
) -> dict[str, Decimal]:
    allowed_settlement_statuses = {BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT}

    if allow_payout_processing_status:
        allowed_settlement_statuses.add(BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING)

    if allow_completed_settlement_status:
        allowed_settlement_statuses.add(BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED)

    if settlement_batch.status not in allowed_settlement_statuses:
        raise NegativePayoutFlowError(
            "Settlement batch status is not allowed for negative-net payout flow: "
            f"got {settlement_batch.status}, allowed={sorted(allowed_settlement_statuses)}"
        )

    if bybit_flow.status != BYBIT_FLOW_STATUS_COMPLETED:
        raise NegativePayoutFlowError("Bybit flow must be completed")

    if str(bybit_flow.settlement_wallet_receipt_status or "").upper() != "CONFIRMED":
        raise NegativePayoutFlowError("Settlement wallet receipt must be CONFIRMED")

    if not bybit_flow.withdrawal_tx_hash:
        raise NegativePayoutFlowError("Bybit flow withdrawal_tx_hash is required")

    expected_total = dec(settlement_batch.total_net_user_payout_usdt)
    withdrawal_request_amount = dec(settlement_batch.withdrawal_request_amount_usdt)
    flow_withdrawal_amount = dec(bybit_flow.withdrawal_request_amount_usdt)
    received_amount = dec(bybit_flow.settlement_wallet_received_usdt)

    if expected_total <= ZERO:
        raise NegativePayoutFlowError("Expected total payout must be positive")

    if not _same_decimal(expected_total, withdrawal_request_amount):
        raise NegativePayoutFlowError(
            "Expected payout total must equal settlement withdrawal_request_amount_usdt"
        )

    if not _same_decimal(expected_total, flow_withdrawal_amount):
        raise NegativePayoutFlowError(
            "Expected payout total must equal Bybit flow withdrawal_request_amount_usdt"
        )

    if not _same_decimal(expected_total, received_amount):
        raise NegativePayoutFlowError(
            "Expected payout total must equal settlement wallet received amount"
        )

    return {
        "expected_total_payout_usdt": expected_total,
        "withdrawal_request_amount_usdt": withdrawal_request_amount,
        "flow_withdrawal_amount_usdt": flow_withdrawal_amount,
        "settlement_wallet_received_usdt": received_amount,
    }


def _validate_settlement_wallet(
    db: Session,
    *,
    fund_id: int,
    bybit_flow: FundNegativeBybitFlow,
) -> FundWallet:
    if bybit_flow.settlement_wallet_id is None:
        raise NegativePayoutFlowError("Bybit flow settlement_wallet_id is required")

    wallet = (
        db.query(FundWallet)
        .filter(FundWallet.id == int(bybit_flow.settlement_wallet_id))
        .with_for_update()
        .first()
    )
    if wallet is None:
        raise NegativePayoutFlowError("Settlement wallet not found")

    if int(wallet.fund_id) != int(fund_id):
        raise NegativePayoutFlowError("Settlement wallet fund_id mismatch")

    if wallet.blockchain != settings.NEGATIVE_NET_PAYOUT_CHAIN:
        raise NegativePayoutFlowError("Settlement wallet blockchain mismatch")

    if wallet.wallet_type != "settlement":
        raise NegativePayoutFlowError("Settlement wallet type must be settlement")

    if not wallet.is_active:
        raise NegativePayoutFlowError("Settlement wallet must be active")

    if not wallet.address:
        raise NegativePayoutFlowError("Settlement wallet address is required")

    if str(wallet.address) != str(bybit_flow.settlement_wallet_address):
        raise NegativePayoutFlowError("Settlement wallet address mismatch")

    return wallet


def _active_bsc_user_wallet_for_user(
    db: Session,
    *,
    user_id: int,
) -> UserWallet:
    wallet = (
        db.query(UserWallet)
        .filter(UserWallet.user_id == int(user_id))
        .filter(UserWallet.blockchain == settings.NEGATIVE_NET_PAYOUT_CHAIN)
        .filter(UserWallet.is_active.is_(True))
        .order_by(UserWallet.id.asc())
        .with_for_update()
        .first()
    )
    if wallet is None:
        raise NegativePayoutFlowError(f"Active BSC user wallet not found for user_id={user_id}")

    if not wallet.address:
        raise NegativePayoutFlowError(f"Active BSC user wallet address missing for user_id={user_id}")

    return wallet


def _load_redeem_orders(
    db: Session,
    *,
    settlement_batch_id: int,
) -> list[FundOrder]:
    excluded_statuses = [
        ORDER_STATUS_SUCCESS,
        ORDER_STATUS_FAILED,
        ORDER_STATUS_FAILED_REQUIRES_REVIEW,
        ORDER_STATUS_CANCELLED,
    ]

    orders = (
        db.query(FundOrder)
        .filter(FundOrder.settlement_batch_id == int(settlement_batch_id))
        .filter(FundOrder.side == ORDER_SIDE_REDEEM)
        .filter(FundOrder.net_user_payout_usdt.isnot(None))
        .filter(FundOrder.net_user_payout_usdt > ZERO)
        .filter(~FundOrder.status.in_(excluded_statuses))
        .order_by(FundOrder.user_id.asc(), FundOrder.id.asc())
        .all()
    )
    if not orders:
        raise NegativePayoutFlowError("No redeem orders with positive net_user_payout_usdt found")

    return orders


def _build_payout_plan(
    db: Session,
    *,
    settlement_batch: FundSettlementBatch,
    expected_total_payout_usdt: Decimal,
) -> list[dict[str, Any]]:
    orders = _load_redeem_orders(db, settlement_batch_id=int(settlement_batch.id))

    grouped: dict[int, dict[str, Any]] = {}
    for order in orders:
        if order.user_id is None:
            raise NegativePayoutFlowError(f"Redeem order {order.id} has no user_id")

        amount = dec(order.net_user_payout_usdt)
        if amount <= ZERO:
            raise NegativePayoutFlowError(f"Redeem order {order.id} has non-positive payout amount")

        wallet = _active_bsc_user_wallet_for_user(db, user_id=int(order.user_id))

        if settings.NEGATIVE_NET_PAYOUT_AGGREGATE_BY_USER_WALLET:
            key = int(wallet.id)
        else:
            key = int(order.id)

        if key not in grouped:
            grouped[key] = {
                "user_id": int(order.user_id),
                "user_wallet_id": int(wallet.id),
                "to_user_wallet_id": int(wallet.id),
                "to_address": str(wallet.address),
                "amount_usdt": ZERO,
                "order_ids": [],
                "order_allocations": [],
            }

        item = grouped[key]
        if int(item["user_wallet_id"]) != int(wallet.id):
            raise NegativePayoutFlowError("Payout aggregation wallet mismatch")

        item["amount_usdt"] = dec(item["amount_usdt"]) + amount
        item["order_ids"].append(int(order.id))
        item["order_allocations"].append(
            {
                "order_id": int(order.id),
                "user_id": int(order.user_id),
                "user_wallet_id": int(wallet.id),
                "amount_usdt": amount,
            }
        )

    plan = list(grouped.values())
    for item in plan:
        item["amount_usdt"] = _q10(item["amount_usdt"])
        if item["amount_usdt"] <= ZERO:
            raise NegativePayoutFlowError("Payout plan contains zero/negative leg")

    planned_total = sum((dec(item["amount_usdt"]) for item in plan), ZERO)
    if not _same_decimal(planned_total, expected_total_payout_usdt):
        raise NegativePayoutFlowError(
            "Expected payout total must equal sum redeem net user payouts"
        )

    return sorted(plan, key=lambda row: (int(row["user_wallet_id"]), str(row["to_address"])))


def _new_or_existing_payout_batch(
    db: Session,
    *,
    existing: FundNegativePayoutBatch | None,
    settlement_batch: FundSettlementBatch,
    bybit_flow: FundNegativeBybitFlow,
    settlement_wallet: FundWallet,
    expected_total_payout_usdt: Decimal,
) -> FundNegativePayoutBatch:
    if existing is not None:
        return existing

    batch = FundNegativePayoutBatch(
        settlement_batch_id=int(settlement_batch.id),
        bybit_flow_id=int(bybit_flow.id),
        fund_id=int(settlement_batch.fund_id),
        status=PAYOUT_BATCH_STATUS_CREATED,
        coin=settings.NEGATIVE_NET_PAYOUT_COIN,
        chain=settings.NEGATIVE_NET_PAYOUT_CHAIN,
        settlement_wallet_id=int(settlement_wallet.id),
        settlement_wallet_address=str(settlement_wallet.address),
        expected_total_payout_usdt=expected_total_payout_usdt,
        gas_status=PAYOUT_GAS_STATUS_NOT_CHECKED,
        balance_refresh_status=PAYOUT_BALANCE_REFRESH_STATUS_NOT_STARTED,
    )
    db.add(batch)
    db.flush()
    return batch


def _set_failed(
    *,
    batch: FundNegativePayoutBatch,
    settlement_batch: FundSettlementBatch,
    fund: Fund | None,
    status_before: str | None,
    settlement_status_before: str | None,
    error: str,
    now,
    diagnostics: dict[str, Any] | None = None,
) -> NegativePayoutResult:
    batch.status = PAYOUT_BATCH_STATUS_FAILED_REQUIRES_REVIEW
    batch.error = error
    batch.updated_at = now
    batch.gas_status = (
        batch.gas_status
        if batch.gas_status not in {None, PAYOUT_GAS_STATUS_NOT_CHECKED}
        else PAYOUT_GAS_STATUS_FAILED_REQUIRES_REVIEW
    )
    batch.balance_refresh_status = (
        batch.balance_refresh_status
        if batch.balance_refresh_status not in {None, PAYOUT_BALANCE_REFRESH_STATUS_NOT_STARTED}
        else PAYOUT_BALANCE_REFRESH_STATUS_FAILED_REQUIRES_REVIEW
    )
    batch.reconciliation_json = _json_dict(
        {
            "ok": False,
            "error": error,
            "diagnostics": diagnostics or {},
            "no_real_bsc_calls": True,
            "no_real_gas_topup": True,
            "no_real_usdt_transfers": True,
            "no_accounting_finalization": True,
            "no_pricing_unlock": True,
        }
    )
    batch.report_json = _json_dict(
        {
            "stage": "23.5",
            "ok": False,
            "error": error,
            "final_state": PAYOUT_BATCH_STATUS_FAILED_REQUIRES_REVIEW,
        }
    )

    settlement_batch.status = BATCH_STATUS_FAILED_REQUIRES_REVIEW
    settlement_batch.error = error
    settlement_batch.updated_at = now

    return NegativePayoutResult(
        ok=False,
        payout_batch_id=int(batch.id) if batch.id is not None else None,
        settlement_batch_id=int(settlement_batch.id),
        bybit_flow_id=int(batch.bybit_flow_id) if batch.bybit_flow_id is not None else None,
        fund_id=int(batch.fund_id) if batch.fund_id is not None else None,
        fund_code=str(fund.code) if fund is not None else None,
        status_before=status_before,
        status_after=batch.status,
        settlement_status_before=settlement_status_before,
        settlement_status_after=settlement_batch.status,
        payout_leg_count=batch.payout_leg_count,
        confirmed_payout_leg_count=batch.confirmed_payout_leg_count,
        expected_total_payout_usdt=str(batch.expected_total_payout_usdt),
        confirmed_total_payout_usdt=(
            str(batch.confirmed_total_payout_usdt)
            if batch.confirmed_total_payout_usdt is not None
            else None
        ),
        error=error,
        diagnostics=diagnostics or {},
    )


def _set_paused_for_operator_action(
    *,
    batch: FundNegativePayoutBatch,
    settlement_batch: FundSettlementBatch,
    fund: Fund | None,
    status_before: str | None,
    settlement_status_before: str | None,
    operator_action_id: int | None,
    now,
    diagnostics: dict[str, Any],
) -> NegativePayoutResult:
    error = "insufficient_ok_gas"

    batch.status = PAYOUT_BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED
    batch.gas_status = PAYOUT_GAS_STATUS_INSUFFICIENT_OK_GAS
    batch.pause_reason = error
    batch.operator_action_id = operator_action_id
    batch.error = error
    batch.updated_at = now
    batch.reconciliation_json = _json_dict(
        {
            "ok": False,
            "paused_operator_action_required": True,
            "pause_reason": error,
            "operator_action_id": operator_action_id,
            "diagnostics": diagnostics,
            "no_real_bsc_calls": True,
            "no_real_gas_topup": True,
            "no_real_usdt_transfers": True,
        }
    )
    batch.report_json = _json_dict(
        {
            "stage": "23.5",
            "ok": False,
            "pause_reason": error,
            "operator_action_id": operator_action_id,
            "final_state": PAYOUT_BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED,
        }
    )

    settlement_batch.status = BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED
    settlement_batch.error = error
    settlement_batch.updated_at = now

    return NegativePayoutResult(
        ok=False,
        payout_batch_id=int(batch.id),
        settlement_batch_id=int(settlement_batch.id),
        bybit_flow_id=int(batch.bybit_flow_id),
        fund_id=int(batch.fund_id),
        fund_code=str(fund.code) if fund is not None else None,
        status_before=status_before,
        status_after=batch.status,
        settlement_status_before=settlement_status_before,
        settlement_status_after=settlement_batch.status,
        payout_leg_count=batch.payout_leg_count,
        confirmed_payout_leg_count=batch.confirmed_payout_leg_count,
        expected_total_payout_usdt=str(batch.expected_total_payout_usdt),
        confirmed_total_payout_usdt=(
            str(batch.confirmed_total_payout_usdt)
            if batch.confirmed_total_payout_usdt is not None
            else None
        ),
        paused_operator_action_required=True,
        operator_action_id=operator_action_id,
        error=error,
        diagnostics=diagnostics,
    )


def _operator_action_values_for_existing_columns(
    *,
    table,
    action_type: str,
    fund_id: int,
    settlement_batch_id: int,
    payout_batch_id: int,
    payload: dict[str, Any],
    now,
) -> dict[str, Any]:
    columns = set(table.c.keys())
    values: dict[str, Any] = {}

    idempotency_key = (
        f"negative-net-retry-gas-topup:"
        f"{int(settlement_batch_id)}:"
        f"{int(payout_batch_id)}"
    )

    candidates = {
        "idempotency_key": idempotency_key,
        "action_type": action_type,
        "status": "pending",
        "fund_id": int(fund_id),
        "settlement_batch_id": int(settlement_batch_id),
        "payout_batch_id": int(payout_batch_id),
        "payload_json": _json_dict(payload),
        "request_json": _json_dict(payload),
        "created_at": now,
        "updated_at": now,
        "requested_at": now,
        "error": None,
    }

    for key, value in candidates.items():
        if key in columns:
            values[key] = value

    return values


def _create_operator_action_if_supported(
    db: Session,
    *,
    fund_id: int,
    settlement_batch_id: int,
    payout_batch_id: int,
    payload: dict[str, Any],
    now,
) -> int | None:
    if not settings.NEGATIVE_NET_PAYOUT_CREATE_OPERATOR_ACTION_ON_GAS_FAIL:
        return None

    if db.bind is None:
        return None

    inspector = inspect(db.bind)
    has_table = inspector.has_table("fund_operator_actions")
    if not has_table:
        has_table = inspector.has_table("fund_operator_actions", schema="public")

    if not has_table:
        return None

    metadata = MetaData()
    table = Table("fund_operator_actions", metadata, autoload_with=db.bind)

    values = _operator_action_values_for_existing_columns(
        table=table,
        action_type="negative_net_retry_gas_topup",
        fund_id=int(fund_id),
        settlement_batch_id=int(settlement_batch_id),
        payout_batch_id=int(payout_batch_id),
        payload=payload,
        now=now,
    )
    if "action_type" not in values:
        return None

    result = db.execute(table.insert().values(**values))
    inserted_id = result.inserted_primary_key[0] if result.inserted_primary_key else None
    db.flush()
    return int(inserted_id) if inserted_id is not None else None


def _reset_not_executed_legs(
    db: Session,
    *,
    batch: FundNegativePayoutBatch,
) -> None:
    if batch.status in {
        PAYOUT_BATCH_STATUS_PAYOUTS_MOCKED,
        PAYOUT_BATCH_STATUS_PAYOUTS_CONFIRMED,
        PAYOUT_BATCH_STATUS_BALANCE_REFRESH_MOCKED,
        PAYOUT_BATCH_STATUS_COMPLETED,
    }:
        raise NegativePayoutFlowError("Existing payout batch already executed")

    (
        db.query(FundNegativePayoutLeg)
        .filter(FundNegativePayoutLeg.payout_batch_id == int(batch.id))
        .delete(synchronize_session=False)
    )
    db.flush()


def _create_planned_legs(
    db: Session,
    *,
    batch: FundNegativePayoutBatch,
    settlement_batch: FundSettlementBatch,
    bybit_flow: FundNegativeBybitFlow,
    settlement_wallet: FundWallet,
    plan: list[dict[str, Any]],
    now,
) -> list[FundNegativePayoutLeg]:
    _reset_not_executed_legs(db, batch=batch)

    legs: list[FundNegativePayoutLeg] = []
    for item in plan:
        deterministic_key = deterministic_payout_key(
            settlement_batch_id=int(settlement_batch.id),
            user_wallet_id=int(item["user_wallet_id"]),
            amount_usdt=dec(item["amount_usdt"]),
        )
        leg = FundNegativePayoutLeg(
            payout_batch_id=int(batch.id),
            settlement_batch_id=int(settlement_batch.id),
            bybit_flow_id=int(bybit_flow.id),
            fund_id=int(settlement_batch.fund_id),
            user_id=int(item["user_id"]),
            user_wallet_id=int(item["user_wallet_id"]),
            status=PAYOUT_LEG_STATUS_PLANNED,
            coin=settings.NEGATIVE_NET_PAYOUT_COIN,
            chain=settings.NEGATIVE_NET_PAYOUT_CHAIN,
            from_settlement_wallet_id=int(settlement_wallet.id),
            from_address=str(settlement_wallet.address),
            to_user_wallet_id=int(item["to_user_wallet_id"]),
            to_address=str(item["to_address"]),
            amount_usdt=dec(item["amount_usdt"]),
            order_ids_json=_json_dict({"order_ids": item["order_ids"]}),
            order_allocations_json=_json_dict(
                {"allocations": item["order_allocations"]}
            ),
            deterministic_key=deterministic_key,
            created_at=now,
            updated_at=now,
        )
        db.add(leg)
        legs.append(leg)

    db.flush()
    return legs


def _plan_to_json(plan: list[dict[str, Any]]) -> dict[str, Any]:
    return _json_dict(
        {
            "aggregate_by_user_wallet": settings.NEGATIVE_NET_PAYOUT_AGGREGATE_BY_USER_WALLET,
            "leg_count": len(plan),
            "legs": plan,
        }
    )


def _completed_batch_matches(
    db: Session,
    *,
    batch: FundNegativePayoutBatch,
    settlement_batch: FundSettlementBatch,
    plan: list[dict[str, Any]],
    expected_total_payout_usdt: Decimal,
    tx_hash_prefix: str,
) -> bool:
    if batch.status != PAYOUT_BATCH_STATUS_COMPLETED:
        return False

    if not _same_decimal(batch.expected_total_payout_usdt, expected_total_payout_usdt):
        return False

    if not _same_decimal(batch.confirmed_total_payout_usdt or ZERO, expected_total_payout_usdt):
        return False

    existing_legs = (
        db.query(FundNegativePayoutLeg)
        .filter(FundNegativePayoutLeg.payout_batch_id == int(batch.id))
        .order_by(FundNegativePayoutLeg.to_user_wallet_id.asc())
        .all()
    )

    if len(existing_legs) != len(plan):
        return False

    plan_by_wallet = {int(item["user_wallet_id"]): item for item in plan}
    for leg in existing_legs:
        plan_item = plan_by_wallet.get(int(leg.to_user_wallet_id or 0))
        if plan_item is None:
            return False

        expected_tx_hash = deterministic_payout_tx_hash(
            prefix=tx_hash_prefix,
            payout_batch_id=int(batch.id),
            payout_leg_id=int(leg.id),
        )

        if leg.status != PAYOUT_LEG_STATUS_BALANCE_REFRESHED:
            return False
        if str(leg.to_address) != str(plan_item["to_address"]):
            return False
        if not _same_decimal(leg.amount_usdt, plan_item["amount_usdt"]):
            return False
        if str(leg.tx_hash) != expected_tx_hash:
            return False

    return True


def _payout_override_for_leg(
    *,
    mock_payout: NegativePayoutMock,
    leg: FundNegativePayoutLeg,
) -> dict[str, Any]:
    raw_legs = mock_payout.payouts.raw.get("legs")
    if raw_legs is None:
        return {}

    if isinstance(raw_legs, dict):
        for key in (
            str(leg.deterministic_key),
            str(leg.to_user_wallet_id),
            str(leg.to_address),
            str(leg.id),
        ):
            value = raw_legs.get(key)
            if isinstance(value, dict):
                return value
        return {}

    if isinstance(raw_legs, list):
        for value in raw_legs:
            if not isinstance(value, dict):
                continue
            if value.get("deterministic_key") == leg.deterministic_key:
                return value
            if str(value.get("to_user_wallet_id")) == str(leg.to_user_wallet_id):
                return value
            if str(value.get("to_address")) == str(leg.to_address):
                return value
        return {}

    return {}


def _value_or_expected(value: Any, expected: Any) -> Any:
    if value is None:
        return expected
    if _is_auto(value):
        return expected
    return value


def _mock_payout_leg(
    *,
    batch: FundNegativePayoutBatch,
    leg: FundNegativePayoutLeg,
    mock_payout: NegativePayoutMock,
    now,
) -> None:
    override = _payout_override_for_leg(mock_payout=mock_payout, leg=leg)

    expected_tx_hash = deterministic_payout_tx_hash(
        prefix=mock_payout.payouts.tx_hash_prefix,
        payout_batch_id=int(batch.id),
        payout_leg_id=int(leg.id),
    )

    if mock_payout.payouts.raw.get("missing_tx_hash") is True:
        tx_hash = None
    else:
        tx_hash = _value_or_expected(override.get("tx_hash"), expected_tx_hash)

    if not tx_hash:
        raise NegativePayoutFlowError("Payout tx_hash is required")

    coin = str(_value_or_expected(override.get("coin"), settings.NEGATIVE_NET_PAYOUT_COIN))
    if coin != settings.NEGATIVE_NET_PAYOUT_COIN:
        raise NegativePayoutFlowError("Payout coin mismatch")

    chain = str(_value_or_expected(override.get("chain"), settings.NEGATIVE_NET_PAYOUT_CHAIN))
    if chain != settings.NEGATIVE_NET_PAYOUT_CHAIN:
        raise NegativePayoutFlowError("Payout chain mismatch")

    from_address = str(_value_or_expected(override.get("from_address"), leg.from_address))
    if from_address != str(leg.from_address):
        raise NegativePayoutFlowError("Payout from address mismatch")

    to_address = str(_value_or_expected(override.get("to_address"), leg.to_address))
    if to_address != str(leg.to_address):
        raise NegativePayoutFlowError("Payout address mismatch")

    amount_usdt = dec(_value_or_expected(override.get("amount_usdt"), leg.amount_usdt))
    if not _same_decimal(amount_usdt, leg.amount_usdt):
        raise NegativePayoutFlowError("Payout amount mismatch")

    if mock_payout.payouts.all_confirmed:
        confirmations = int(
            _value_or_expected(
                override.get("confirmations"),
                mock_payout.payouts.default_confirmations,
            )
        )
    else:
        confirmations = int(_value_or_expected(override.get("confirmations"), 0))

    if confirmations < int(settings.NEGATIVE_NET_PAYOUT_CONFIRMATIONS_REQUIRED):
        raise NegativePayoutFlowError("Payout confirmations below required")

    leg.tx_hash = str(tx_hash)
    leg.confirmations = confirmations
    leg.status = PAYOUT_LEG_STATUS_PAYOUT_MOCKED
    leg.sent_at = now
    leg.updated_at = now
    leg.payout_mock_json = _json_dict(
        {
            "mock_only": True,
            "coin": coin,
            "chain": chain,
            "from_address": from_address,
            "to_address": to_address,
            "amount_usdt": amount_usdt,
            "tx_hash": tx_hash,
            "deterministic_key": leg.deterministic_key,
            "no_real_bsc_calls": True,
            "no_real_usdt_transfers": True,
            "raw_override": override,
        }
    )

    leg.status = PAYOUT_LEG_STATUS_PAYOUT_CONFIRMED
    leg.confirmed_at = now
    leg.confirmation_json = _json_dict(
        {
            "mock_only": True,
            "tx_hash": tx_hash,
            "confirmations": confirmations,
            "required_confirmations": settings.NEGATIVE_NET_PAYOUT_CONFIRMATIONS_REQUIRED,
            "confirmed": True,
        }
    )


def _balance_override_for_wallet(
    *,
    mock_payout: NegativePayoutMock,
    wallet_id: int,
    address: str,
) -> dict[str, Any]:
    balances = mock_payout.balance_refresh.user_wallet_balances
    if balances is None or balances == "AUTO":
        return {}

    if not isinstance(balances, dict):
        raise NegativePayoutFlowError("user_wallet_balances must be AUTO or dict")

    for key in (str(wallet_id), str(address)):
        value = balances.get(key)
        if isinstance(value, dict):
            return value

    return {}


def _mock_balance_refresh(
    db: Session,
    *,
    batch: FundNegativePayoutBatch,
    legs: list[FundNegativePayoutLeg],
    mock_payout: NegativePayoutMock,
    expected_total_payout_usdt: Decimal,
    now,
) -> None:
    batch.balance_refresh_started_at = now

    settlement_before = (
        mock_payout.balance_refresh.settlement_wallet_usdt_before
        if mock_payout.balance_refresh.settlement_wallet_usdt_before is not None
        else expected_total_payout_usdt
    )
    settlement_after = (
        mock_payout.balance_refresh.settlement_wallet_usdt_after
        if mock_payout.balance_refresh.settlement_wallet_usdt_after is not None
        else settlement_before - expected_total_payout_usdt
    )

    if settlement_after < ZERO:
        raise NegativePayoutFlowError("Settlement wallet balance refresh after balance is negative")

    expected_settlement_after = settlement_before - expected_total_payout_usdt
    if not _same_decimal(settlement_after, expected_settlement_after):
        raise NegativePayoutFlowError("Settlement wallet balance refresh mismatch")

    user_refresh_rows = []
    for leg in legs:
        wallet = (
            db.query(UserWallet)
            .filter(UserWallet.id == int(leg.to_user_wallet_id))
            .with_for_update()
            .first()
        )
        if wallet is None:
            raise NegativePayoutFlowError("User wallet not found during balance refresh")

        override = _balance_override_for_wallet(
            mock_payout=mock_payout,
            wallet_id=int(wallet.id),
            address=str(wallet.address),
        )

        before = dec(_value_or_expected(override.get("before_usdt"), wallet.usdt_balance or ZERO))
        after = dec(_value_or_expected(override.get("after_usdt"), before + dec(leg.amount_usdt)))

        expected_after = before + dec(leg.amount_usdt)
        if not _same_decimal(after, expected_after):
            raise NegativePayoutFlowError("User wallet balance refresh mismatch")

        leg.wallet_balance_before_usdt = before
        leg.wallet_balance_after_usdt = after
        leg.status = PAYOUT_LEG_STATUS_BALANCE_REFRESHED
        leg.updated_at = now
        leg.balance_refresh_json = _json_dict(
            {
                "mock_only": True,
                "user_wallet_id": int(wallet.id),
                "address": wallet.address,
                "before_usdt": before,
                "payout_amount_usdt": leg.amount_usdt,
                "after_usdt": after,
                "raw_override": override,
            }
        )

        if settings.NEGATIVE_NET_PAYOUT_UPDATE_USER_WALLET_BALANCES_IN_MOCK:
            wallet.usdt_balance = after
            wallet.usdt_balance_updated_at = now

        user_refresh_rows.append(
            {
                "user_wallet_id": int(wallet.id),
                "address": wallet.address,
                "before_usdt": before,
                "payout_amount_usdt": leg.amount_usdt,
                "after_usdt": after,
            }
        )

    batch.settlement_wallet_usdt_before = settlement_before
    batch.settlement_wallet_usdt_after = settlement_after
    batch.balance_refresh_status = PAYOUT_BALANCE_REFRESH_STATUS_MOCKED
    batch.balance_refresh_completed_at = now
    batch.balance_refresh_status = PAYOUT_BALANCE_REFRESH_STATUS_CONFIRMED
    batch.balance_refresh_json = _json_dict(
        {
            "mock_only": True,
            "settlement_wallet": {
                "before_usdt": settlement_before,
                "confirmed_total_payout_usdt": expected_total_payout_usdt,
                "after_usdt": settlement_after,
            },
            "user_wallets": user_refresh_rows,
        }
    )


def _apply_gas_check_or_pause(
    db: Session,
    *,
    batch: FundNegativePayoutBatch,
    settlement_batch: FundSettlementBatch,
    fund: Fund,
    mock_payout: NegativePayoutMock,
    status_before: str | None,
    settlement_status_before: str | None,
    now,
) -> NegativePayoutResult | None:
    bnb_before = _q18(mock_payout.gas.settlement_wallet_bnb_before)
    required_bnb = _q18(mock_payout.gas.required_bnb)
    ok_available = _q18(mock_payout.gas.ok_gas_wallet_bnb_available)
    topup_amount = _q18(mock_payout.gas.topup_amount_bnb)

    batch.settlement_wallet_bnb_before = bnb_before
    batch.settlement_wallet_bnb_required = required_bnb
    batch.ok_gas_wallet_bnb_available = ok_available
    batch.gas_topup_required_bnb = max(required_bnb - bnb_before, ZERO)

    if bnb_before >= required_bnb:
        batch.gas_status = PAYOUT_GAS_STATUS_SUFFICIENT
        batch.settlement_wallet_bnb_after = bnb_before
        batch.gas_reconciliation_json = _json_dict(
            {
                "mock_only": True,
                "gas_sufficient": True,
                "settlement_wallet_bnb_before": bnb_before,
                "required_bnb": required_bnb,
                "no_real_gas_topup": True,
            }
        )
        batch.status = PAYOUT_BATCH_STATUS_GAS_CHECK_PASSED
        batch.gas_status = PAYOUT_GAS_STATUS_READY
        batch.status = PAYOUT_BATCH_STATUS_GAS_READY
        batch.updated_at = now
        return None

    batch.gas_status = PAYOUT_GAS_STATUS_TOPUP_REQUIRED

    if ok_available < topup_amount:
        payload = {
            "stage": "23.5",
            "reason": "insufficient_ok_gas",
            "fund_id": int(fund.id),
            "settlement_batch_id": int(settlement_batch.id),
            "payout_batch_id": int(batch.id),
            "settlement_wallet_bnb_before": bnb_before,
            "required_bnb": required_bnb,
            "ok_gas_wallet_bnb_available": ok_available,
            "topup_amount_bnb": topup_amount,
        }
        operator_action_id = _create_operator_action_if_supported(
            db,
            fund_id=int(fund.id),
            settlement_batch_id=int(settlement_batch.id),
            payout_batch_id=int(batch.id),
            payload=payload,
            now=now,
        )

        return _set_paused_for_operator_action(
            batch=batch,
            settlement_batch=settlement_batch,
            fund=fund,
            status_before=status_before,
            settlement_status_before=settlement_status_before,
            operator_action_id=operator_action_id,
            now=now,
            diagnostics=payload,
        )

    # Stage 24 Operation Guard live-boundary hook.
    # Future real settlement-wallet gas top-up must call:
    # require_stage24_bsc_settlement_gas_topup_guard(
    #     db,
    #     fund_id=int(fund.id),
    #     settlement_batch_id=int(settlement_batch.id),
    #     request_id=f"neg-net-gas-topup:{int(settlement_batch.id)}:{int(batch.id)}",
    #     metadata={"equivalent_bnb_amount": "..."},
    # )

    if not mock_payout.gas.topup_tx_hash:
        raise NegativePayoutFlowError("Gas top-up tx_hash is required")

    if not _mock_confirmed(mock_payout.gas.topup_status):
        raise NegativePayoutFlowError("Gas top-up status must be CONFIRMED")

    batch.gas_status = PAYOUT_GAS_STATUS_TOPUP_MOCKED
    batch.status = PAYOUT_BATCH_STATUS_GAS_TOPUP_MOCKED
    batch.gas_topup_amount_bnb = topup_amount
    batch.gas_topup_tx_hash = mock_payout.gas.topup_tx_hash
    batch.settlement_wallet_bnb_after = bnb_before + topup_amount
    batch.gas_topup_mock_json = _json_dict(
        {
            "mock_only": True,
            "from": "ok_gas_wallet",
            "to": batch.settlement_wallet_address,
            "amount_bnb": topup_amount,
            "tx_hash": mock_payout.gas.topup_tx_hash,
            "status": mock_payout.gas.topup_status,
            "no_real_bsc_calls": True,
            "no_real_gas_topup": True,
        }
    )
    batch.gas_reconciliation_json = _json_dict(
        {
            "mock_only": True,
            "gas_sufficient_after_topup": batch.settlement_wallet_bnb_after >= required_bnb,
            "settlement_wallet_bnb_before": bnb_before,
            "required_bnb": required_bnb,
            "topup_amount_bnb": topup_amount,
            "settlement_wallet_bnb_after": batch.settlement_wallet_bnb_after,
            "topup_tx_hash": mock_payout.gas.topup_tx_hash,
        }
    )

    if batch.settlement_wallet_bnb_after < required_bnb:
        raise NegativePayoutFlowError("Gas top-up did not make settlement wallet gas-ready")

    batch.gas_status = PAYOUT_GAS_STATUS_READY
    batch.status = PAYOUT_BATCH_STATUS_GAS_READY
    batch.updated_at = now
    return None


def _ensure_live_settlement_wallet_gas(
    db: Session,
    *,
    w3,
    batch: FundNegativePayoutBatch,
    settlement_batch: FundSettlementBatch,
    fund: Fund,
    settlement_wallet: FundWallet,
    leg_count: int,
    now,
) -> bool:
    """
    Real BSC gas top-up path:
    OK gas wallet -> fund settlement wallet.

    Returns True only when the settlement wallet is gas-ready.
    Returns False when a top-up tx has been sent and is pending confirmation.
    Does not commit.
    """
    settlement_address = str(settlement_wallet.address)
    ok_wallet_address = str(settings.FEE_WALLET_OK_ADDRESS or "").strip()
    ok_wallet_private_key = str(settings.FEE_WALLET_OK_PRIVATE_KEY or "").strip()

    if not ok_wallet_address:
        raise NegativePayoutFlowError("FEE_WALLET_OK_ADDRESS is required for live gas top-up")

    if not ok_wallet_private_key:
        raise NegativePayoutFlowError("FEE_WALLET_OK_PRIVATE_KEY is required for live gas top-up")

    required_bnb = _required_bnb_for_payout_legs(w3, leg_count=leg_count)
    bnb_before = _q18(get_bnb_balance(w3, settlement_address))

    batch.settlement_wallet_bnb_before = bnb_before
    batch.settlement_wallet_bnb_required = required_bnb
    batch.gas_topup_required_bnb = max(required_bnb - bnb_before, ZERO)
    batch.updated_at = now

    if batch.gas_topup_tx_hash:
        if _check_tx_confirmed(w3, batch.gas_topup_tx_hash):
            bnb_after = _q18(get_bnb_balance(w3, settlement_address))
            batch.settlement_wallet_bnb_after = bnb_after
            batch.gas_status = PAYOUT_GAS_STATUS_READY
            batch.status = PAYOUT_BATCH_STATUS_GAS_READY
            batch.gas_reconciliation_json = _json_dict(
                {
                    "live": True,
                    "gas_topup_tx_hash": batch.gas_topup_tx_hash,
                    "confirmed": True,
                    "settlement_wallet_bnb_before": bnb_before,
                    "settlement_wallet_bnb_after": bnb_after,
                    "required_bnb": required_bnb,
                    "no_duplicate_topup": True,
                }
            )
            db.add(batch)
            db.flush()
            return True

        batch.gas_status = PAYOUT_GAS_STATUS_TOPUP_REQUIRED
        batch.status = PAYOUT_BATCH_STATUS_GAS_CHECK_PASSED
        batch.gas_reconciliation_json = _json_dict(
            {
                "live": True,
                "gas_topup_tx_hash": batch.gas_topup_tx_hash,
                "pending_confirmation": True,
                "no_duplicate_topup": True,
            }
        )
        db.add(batch)
        db.flush()
        return False

    if bnb_before >= required_bnb:
        batch.settlement_wallet_bnb_after = bnb_before
        batch.gas_status = PAYOUT_GAS_STATUS_READY
        batch.status = PAYOUT_BATCH_STATUS_GAS_READY
        batch.gas_reconciliation_json = _json_dict(
            {
                "live": True,
                "gas_sufficient": True,
                "settlement_wallet_bnb_before": bnb_before,
                "required_bnb": required_bnb,
                "no_real_gas_topup_needed": True,
            }
        )
        db.add(batch)
        db.flush()
        return True

    topup_amount = _q18(required_bnb - bnb_before)
    ok_available = _q18(get_bnb_balance(w3, ok_wallet_address))
    batch.ok_gas_wallet_bnb_available = ok_available
    batch.gas_topup_amount_bnb = topup_amount

    if ok_available < topup_amount:
        raise NegativePayoutFlowError(
            f"Insufficient OK gas wallet BNB: available={ok_available}, required={topup_amount}"
        )

    request_id = deterministic_settlement_gas_topup_request_id(
        settlement_batch_id=int(settlement_batch.id),
        payout_batch_id=int(batch.id),
        amount_bnb=topup_amount,
        to_address=settlement_address,
    )

    try:
        guard_decision = require_bsc_settlement_gas_topup_guard(
            db,
            fund_id=int(fund.id),
            settlement_batch_id=int(settlement_batch.id),
            request_id=request_id,
            amount_usdt=None,
            metadata={
                "source": "negative_payout_flow_live",
                "boundary": "ok_gas_wallet_to_settlement_wallet",
                "asset": "BNB",
                "amount_bnb": str(topup_amount),
                "from_address": ok_wallet_address,
                "to_address": settlement_address,
                "payout_batch_id": int(batch.id),
            },
        )
    except OperationGuardBlockedError as exc:
        batch.gas_status = PAYOUT_GAS_STATUS_FAILED_REQUIRES_REVIEW
        batch.status = PAYOUT_BATCH_STATUS_FAILED_REQUIRES_REVIEW
        batch.error = f"Operation Guard blocked settlement wallet gas top-up: {exc}"
        batch.updated_at = now
        db.add(batch)
        db.flush()
        raise NegativePayoutFlowError(batch.error) from exc

    tx_hash = send_native_bnb(
        w3,
        from_private_key=ok_wallet_private_key,
        from_address=ok_wallet_address,
        to_address=settlement_address,
        amount_bnb=topup_amount,
    )

    batch.gas_topup_tx_hash = tx_hash
    batch.gas_status = PAYOUT_GAS_STATUS_TOPUP_REQUIRED
    batch.status = PAYOUT_BATCH_STATUS_GAS_CHECK_PASSED
    batch.gas_topup_mock_json = _json_dict(
        {
            "live": True,
            "request_id": request_id,
            "guard_event_id": guard_decision.event_id,
            "from_address": ok_wallet_address,
            "to_address": settlement_address,
            "amount_bnb": topup_amount,
            "tx_hash": tx_hash,
            "pending_confirmation": True,
        }
    )
    batch.updated_at = now

    db.add(batch)
    db.flush()
    return False


def _send_or_confirm_live_payout_leg(
    db: Session,
    *,
    w3,
    batch: FundNegativePayoutBatch,
    settlement_batch: FundSettlementBatch,
    fund: Fund,
    settlement_wallet: FundWallet,
    leg: FundNegativePayoutLeg,
    now,
) -> bool:
    """
    Real BSC payout:
    settlement wallet -> user platform wallet USDT.

    Returns True only when the payout tx is confirmed.
    Returns False when tx is sent but still pending confirmation.
    Does not commit.
    """
    if dec(leg.amount_usdt) <= ZERO:
        raise NegativePayoutFlowError("Live payout leg amount must be positive")

    if not leg.to_address:
        raise NegativePayoutFlowError("Live payout leg to_address is required")

    if str(leg.from_address) != str(settlement_wallet.address):
        raise NegativePayoutFlowError("Live payout leg from_address mismatch")

    if str(leg.coin) != settings.NEGATIVE_NET_PAYOUT_COIN:
        raise NegativePayoutFlowError("Live payout leg coin mismatch")

    if str(leg.chain) != settings.NEGATIVE_NET_PAYOUT_CHAIN:
        raise NegativePayoutFlowError("Live payout leg chain mismatch")

    if leg.tx_hash:
        if _check_tx_confirmed(w3, leg.tx_hash):
            leg.status = PAYOUT_LEG_STATUS_PAYOUT_CONFIRMED
            leg.confirmations = int(settings.NEGATIVE_NET_PAYOUT_CONFIRMATIONS_REQUIRED)
            leg.confirmed_at = leg.confirmed_at or now
            leg.updated_at = now
            leg.confirmation_json = _json_dict(
                {
                    "live": True,
                    "tx_hash": leg.tx_hash,
                    "confirmed": True,
                    "required_confirmations": int(settings.NEGATIVE_NET_PAYOUT_CONFIRMATIONS_REQUIRED),
                    "no_duplicate_payout": True,
                }
            )
            db.add(leg)
            db.flush()
            return True

        leg.confirmation_json = _json_dict(
            {
                "live": True,
                "tx_hash": leg.tx_hash,
                "pending_confirmation": True,
                "no_duplicate_payout": True,
            }
        )
        leg.updated_at = now
        db.add(leg)
        db.flush()
        return False

    request_id = deterministic_redeem_payout_request_id(
        settlement_batch_id=int(settlement_batch.id),
        payout_batch_id=int(batch.id),
        payout_leg_id=int(leg.id),
        user_wallet_id=int(leg.to_user_wallet_id or leg.user_wallet_id),
        amount_usdt=dec(leg.amount_usdt),
        to_address=str(leg.to_address),
    )

    try:
        guard_decision = require_bsc_redeem_payout_guard(
            db,
            fund_id=int(fund.id),
            settlement_batch_id=int(settlement_batch.id),
            amount_usdt=dec(leg.amount_usdt),
            request_id=request_id,
            metadata={
                "source": "negative_payout_flow_live",
                "boundary": "settlement_wallet_to_user_wallet",
                "asset": "USDT",
                "payout_batch_id": int(batch.id),
                "payout_leg_id": int(leg.id),
                "user_id": int(leg.user_id),
                "user_wallet_id": int(leg.to_user_wallet_id or leg.user_wallet_id),
                "from_address": str(leg.from_address),
                "to_address": str(leg.to_address),
            },
        )
    except OperationGuardBlockedError as exc:
        leg.status = PAYOUT_LEG_STATUS_FAILED_REQUIRES_REVIEW
        leg.error = f"Operation Guard blocked BSC redeem payout: {exc}"
        leg.failed_at = now
        leg.updated_at = now
        db.add(leg)
        db.flush()
        raise NegativePayoutFlowError(leg.error) from exc

    private_key = decrypt_private_key(settlement_wallet.encrypted_private_key)

    tx_hash = _send_usdt_transfer(
        w3,
        from_private_key=private_key,
        from_address=str(settlement_wallet.address),
        to_address=str(leg.to_address),
        amount_usdt=dec(leg.amount_usdt),
    )

    leg.tx_hash = tx_hash
    leg.sent_at = now
    leg.updated_at = now
    leg.payout_mock_json = _json_dict(
        {
            "live": True,
            "request_id": request_id,
            "guard_event_id": guard_decision.event_id,
            "coin": leg.coin,
            "chain": leg.chain,
            "from_address": str(settlement_wallet.address),
            "to_address": str(leg.to_address),
            "amount_usdt": dec(leg.amount_usdt),
            "tx_hash": tx_hash,
            "pending_confirmation": True,
        }
    )

    db.add(leg)
    db.flush()
    return False


def _refresh_live_balances_after_confirmed_payouts(
    db: Session,
    *,
    batch: FundNegativePayoutBatch,
    legs: list[FundNegativePayoutLeg],
    expected_total_payout_usdt: Decimal,
    now,
) -> None:
    confirmed_total = sum((dec(leg.amount_usdt) for leg in legs), ZERO)

    if not _same_decimal(confirmed_total, expected_total_payout_usdt):
        raise NegativePayoutFlowError("Confirmed payout total mismatch")

    batch.balance_refresh_started_at = batch.balance_refresh_started_at or now

    settlement_before = (
        dec(batch.settlement_wallet_usdt_before)
        if batch.settlement_wallet_usdt_before is not None
        else expected_total_payout_usdt
    )
    settlement_after = settlement_before - expected_total_payout_usdt
    if settlement_after < ZERO:
        raise NegativePayoutFlowError("Settlement wallet balance refresh after balance is negative")

    user_refresh_rows = []
    for leg in legs:
        if leg.status != PAYOUT_LEG_STATUS_PAYOUT_CONFIRMED:
            raise NegativePayoutFlowError("Cannot refresh balance before all payout legs are confirmed")

        wallet = (
            db.query(UserWallet)
            .filter(UserWallet.id == int(leg.to_user_wallet_id or leg.user_wallet_id))
            .with_for_update()
            .first()
        )
        if wallet is None:
            raise NegativePayoutFlowError("User wallet not found during live balance refresh")

        before = dec(wallet.usdt_balance or ZERO)
        after = before + dec(leg.amount_usdt)

        wallet.usdt_balance = after
        wallet.usdt_balance_updated_at = now

        leg.wallet_balance_before_usdt = before
        leg.wallet_balance_after_usdt = after
        leg.status = PAYOUT_LEG_STATUS_BALANCE_REFRESHED
        leg.balance_refresh_json = _json_dict(
            {
                "live": True,
                "user_wallet_id": int(wallet.id),
                "address": wallet.address,
                "before_usdt": before,
                "payout_amount_usdt": dec(leg.amount_usdt),
                "after_usdt": after,
            }
        )
        leg.updated_at = now

        db.add(wallet)
        db.add(leg)

        user_refresh_rows.append(
            {
                "user_wallet_id": int(wallet.id),
                "address": wallet.address,
                "before_usdt": before,
                "payout_amount_usdt": dec(leg.amount_usdt),
                "after_usdt": after,
            }
        )

    batch.settlement_wallet_usdt_before = settlement_before
    batch.settlement_wallet_usdt_after = settlement_after
    batch.confirmed_total_payout_usdt = expected_total_payout_usdt
    batch.confirmed_payout_leg_count = len(legs)
    batch.balance_refresh_status = PAYOUT_BALANCE_REFRESH_STATUS_CONFIRMED
    batch.balance_refresh_completed_at = now
    batch.balance_refresh_json = _json_dict(
        {
            "live": True,
            "settlement_wallet": {
                "before_usdt": settlement_before,
                "confirmed_total_payout_usdt": expected_total_payout_usdt,
                "after_usdt": settlement_after,
            },
            "user_wallets": user_refresh_rows,
        }
    )

    db.add(batch)
    db.flush()


def execute_negative_payout_flow_live(
    db: Session,
    *,
    settlement_batch_id: int,
    now=None,
) -> NegativePayoutResult:
    """
    Real production-live negative-net BSC payout flow:

    1. validate completed Bybit master flow;
    2. build payout legs from redeem orders;
    3. ensure settlement wallet BNB gas;
    4. send/confirm settlement wallet -> user wallet USDT payouts;
    5. refresh internal user wallet balances after all payout txs are confirmed.

    Does not finalize fund shares/accounting. Caller controls transaction boundary.
    """
    if not settings.NEGATIVE_NET_PAYOUT_ALLOW_LIVE_EXECUTION:
        raise NegativePayoutFlowError(
            "Live negative-net payout execution is disabled: "
            "NEGATIVE_NET_PAYOUT_ALLOW_LIVE_EXECUTION=false"
        )

    if settings.NEGATIVE_NET_PAYOUT_MOCK_ONLY:
        raise NegativePayoutFlowError(
            "Live negative-net payout requires NEGATIVE_NET_PAYOUT_MOCK_ONLY=false"
        )

    now = now or utcnow()

    settlement_batch = _lock_settlement_batch(
        db,
        settlement_batch_id=int(settlement_batch_id),
    )
    settlement_status_before = str(settlement_batch.status)

    bybit_flow = _lock_completed_bybit_flow(
        db,
        settlement_batch_id=int(settlement_batch.id),
    )
    fund = _get_fund(db, fund_id=int(settlement_batch.fund_id))

    existing_batch = _lock_existing_payout_batch(
        db,
        settlement_batch_id=int(settlement_batch.id),
    )

    try:
        validate_settlement_share_state_before_external(
            db,
            batch=settlement_batch,
            mark_failed=True,
        )
    except SettlementShareQuantityError as exc:
        error = str(exc)

        if existing_batch is not None:
            existing_batch.status = (
                PAYOUT_BATCH_STATUS_FAILED_REQUIRES_REVIEW
            )
            existing_batch.error = error
            existing_batch.updated_at = now
            db.add(existing_batch)

        settlement_batch.status = (
            BATCH_STATUS_FAILED_REQUIRES_REVIEW
        )
        settlement_batch.error = error
        settlement_batch.updated_at = now

        db.add(settlement_batch)
        db.flush()
        raise

    status_before = (
        str(existing_batch.status)
        if existing_batch is not None
        else None
    )
    can_resume_payout_processing = (
        existing_batch is not None
        and str(existing_batch.status) in LIVE_RESUMABLE_PAYOUT_BATCH_STATUSES
    )

    try:
        amounts = _validate_bybit_flow_input(
            settlement_batch=settlement_batch,
            bybit_flow=bybit_flow,
            allow_payout_processing_status=can_resume_payout_processing,
            allow_completed_settlement_status=(
                existing_batch is not None
                and existing_batch.status == PAYOUT_BATCH_STATUS_COMPLETED
            ),
        )
        settlement_wallet = _validate_settlement_wallet(
            db,
            fund_id=int(fund.id),
            bybit_flow=bybit_flow,
        )
        plan = _build_payout_plan(
            db,
            settlement_batch=settlement_batch,
            expected_total_payout_usdt=amounts["expected_total_payout_usdt"],
        )

        batch = _new_or_existing_payout_batch(
            db,
            existing=existing_batch,
            settlement_batch=settlement_batch,
            bybit_flow=bybit_flow,
            settlement_wallet=settlement_wallet,
            expected_total_payout_usdt=amounts["expected_total_payout_usdt"],
        )
        status_before = str(batch.status)

        if batch.status == PAYOUT_BATCH_STATUS_COMPLETED:
            if _same_decimal(batch.expected_total_payout_usdt, amounts["expected_total_payout_usdt"]) and _same_decimal(
                batch.confirmed_total_payout_usdt or ZERO,
                amounts["expected_total_payout_usdt"],
            ):
                return NegativePayoutResult(
                    ok=True,
                    payout_batch_id=int(batch.id),
                    settlement_batch_id=int(settlement_batch.id),
                    bybit_flow_id=int(bybit_flow.id),
                    fund_id=int(fund.id),
                    fund_code=str(fund.code),
                    status_before=status_before,
                    status_after=batch.status,
                    settlement_status_before=settlement_status_before,
                    settlement_status_after=settlement_batch.status,
                    payout_leg_count=batch.payout_leg_count,
                    confirmed_payout_leg_count=batch.confirmed_payout_leg_count,
                    expected_total_payout_usdt=str(batch.expected_total_payout_usdt),
                    confirmed_total_payout_usdt=str(batch.confirmed_total_payout_usdt),
                    idempotent=True,
                    diagnostics={"idempotent": True, "live": True},
                )

            return _set_failed(
                batch=batch,
                settlement_batch=settlement_batch,
                fund=fund,
                status_before=status_before,
                settlement_status_before=settlement_status_before,
                error="Existing completed live payout batch does not match expected total",
                now=now,
            )

        batch.bybit_flow_id = int(bybit_flow.id)
        batch.fund_id = int(fund.id)
        batch.coin = settings.NEGATIVE_NET_PAYOUT_COIN
        batch.chain = settings.NEGATIVE_NET_PAYOUT_CHAIN
        batch.settlement_wallet_id = int(settlement_wallet.id)
        batch.settlement_wallet_address = str(settlement_wallet.address)
        batch.expected_total_payout_usdt = amounts["expected_total_payout_usdt"]
        batch.planned_total_payout_usdt = amounts["expected_total_payout_usdt"]
        batch.payout_leg_count = len(plan)
        batch.payout_plan_json = _plan_to_json(plan)

        existing_legs = (
            db.query(FundNegativePayoutLeg)
            .filter(FundNegativePayoutLeg.payout_batch_id == int(batch.id))
            .order_by(FundNegativePayoutLeg.to_user_wallet_id.asc(), FundNegativePayoutLeg.id.asc())
            .with_for_update()
            .all()
        )

        if existing_legs:
            legs = existing_legs
            if len(legs) != len(plan):
                raise NegativePayoutFlowError("Existing live payout legs count mismatch")

            plan_by_wallet = {int(item["user_wallet_id"]): item for item in plan}
            for leg in legs:
                plan_item = plan_by_wallet.get(int(leg.to_user_wallet_id or leg.user_wallet_id or 0))
                if plan_item is None:
                    raise NegativePayoutFlowError("Existing live payout leg wallet mismatch")

                if str(leg.to_address) != str(plan_item["to_address"]):
                    raise NegativePayoutFlowError("Existing live payout leg address mismatch")

                if not _same_decimal(leg.amount_usdt, plan_item["amount_usdt"]):
                    raise NegativePayoutFlowError("Existing live payout leg amount mismatch")
        else:
            legs = _create_planned_legs(
                db,
                batch=batch,
                settlement_batch=settlement_batch,
                bybit_flow=bybit_flow,
                settlement_wallet=settlement_wallet,
                plan=plan,
                now=now,
            )

        batch.status = PAYOUT_BATCH_STATUS_PAYOUTS_PLANNED
        batch.updated_at = now
        db.add(batch)
        db.flush()

        w3 = get_web3()

        gas_ready = _ensure_live_settlement_wallet_gas(
            db,
            w3=w3,
            batch=batch,
            settlement_batch=settlement_batch,
            fund=fund,
            settlement_wallet=settlement_wallet,
            leg_count=len(legs),
            now=now,
        )
        if not gas_ready:
            settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING
            settlement_batch.updated_at = now

            batch.updated_at = now
            db.add(settlement_batch)
            db.add(batch)
            db.flush()

            return NegativePayoutResult(
                ok=False,
                payout_batch_id=int(batch.id),
                settlement_batch_id=int(settlement_batch.id),
                bybit_flow_id=int(bybit_flow.id),
                fund_id=int(fund.id),
                fund_code=str(fund.code),
                status_before=status_before,
                status_after=batch.status,
                settlement_status_before=settlement_status_before,
                settlement_status_after=settlement_batch.status,
                payout_leg_count=len(legs),
                confirmed_payout_leg_count=sum(
                    1 for leg in legs if leg.status == PAYOUT_LEG_STATUS_PAYOUT_CONFIRMED
                ),
                expected_total_payout_usdt=str(amounts["expected_total_payout_usdt"]),
                confirmed_total_payout_usdt=(
                    str(batch.confirmed_total_payout_usdt)
                    if batch.confirmed_total_payout_usdt is not None
                    else None
                ),
                diagnostics={
                    "live": True,
                    "pending": "settlement_wallet_gas_topup_or_confirmation",
                    "gas_status": batch.gas_status,
                    "gas_topup_tx_hash": batch.gas_topup_tx_hash,
                    "no_duplicate_topup": True,
                },
            )

        settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING
        settlement_batch.updated_at = now

        batch.payout_started_at = batch.payout_started_at or now
        batch.status = PAYOUT_BATCH_STATUS_GAS_READY
        batch.updated_at = now

        db.add(settlement_batch)
        db.add(batch)
        db.flush()

        all_confirmed = True
        payout_rows = []

        for leg in legs:
            confirmed = _send_or_confirm_live_payout_leg(
                db,
                w3=w3,
                batch=batch,
                settlement_batch=settlement_batch,
                fund=fund,
                settlement_wallet=settlement_wallet,
                leg=leg,
                now=now,
            )

            if not confirmed:
                all_confirmed = False

            payout_rows.append(
                {
                    "payout_leg_id": int(leg.id),
                    "user_id": int(leg.user_id),
                    "to_user_wallet_id": int(leg.to_user_wallet_id or leg.user_wallet_id),
                    "to_address": leg.to_address,
                    "amount_usdt": leg.amount_usdt,
                    "tx_hash": leg.tx_hash,
                    "status": leg.status,
                    "confirmed": confirmed,
                }
            )

        confirmed_count = sum(
            1 for leg in legs if leg.status == PAYOUT_LEG_STATUS_PAYOUT_CONFIRMED
        )
        confirmed_total = sum(
            (
                dec(leg.amount_usdt)
                for leg in legs
                if leg.status == PAYOUT_LEG_STATUS_PAYOUT_CONFIRMED
            ),
            ZERO,
        )

        batch.confirmed_payout_leg_count = confirmed_count
        batch.confirmed_total_payout_usdt = confirmed_total
        batch.payout_execution_json = _json_dict(
            {
                "live": True,
                "all_confirmed": all_confirmed,
                "confirmed_total_payout_usdt": confirmed_total,
                "confirmed_payout_leg_count": confirmed_count,
                "payout_leg_count": len(legs),
                "legs": payout_rows,
            }
        )
        batch.updated_at = now

        if not all_confirmed:
            db.add(batch)
            db.add(settlement_batch)
            db.flush()

            return NegativePayoutResult(
                ok=False,
                payout_batch_id=int(batch.id),
                settlement_batch_id=int(settlement_batch.id),
                bybit_flow_id=int(bybit_flow.id),
                fund_id=int(fund.id),
                fund_code=str(fund.code),
                status_before=status_before,
                status_after=batch.status,
                settlement_status_before=settlement_status_before,
                settlement_status_after=settlement_batch.status,
                payout_leg_count=len(legs),
                confirmed_payout_leg_count=confirmed_count,
                expected_total_payout_usdt=str(amounts["expected_total_payout_usdt"]),
                confirmed_total_payout_usdt=str(confirmed_total),
                diagnostics={
                    "live": True,
                    "pending": "one_or_more_payout_txs_pending_confirmation",
                    "no_duplicate_payout": True,
                    "legs": payout_rows,
                },
            )

        if confirmed_count != len(legs):
            raise NegativePayoutFlowError("Confirmed payout leg count mismatch")

        if not _same_decimal(confirmed_total, amounts["expected_total_payout_usdt"]):
            raise NegativePayoutFlowError("Confirmed payout total mismatch")

        batch.status = PAYOUT_BATCH_STATUS_PAYOUTS_CONFIRMED
        batch.payout_completed_at = now
        db.add(batch)
        db.flush()

        _refresh_live_balances_after_confirmed_payouts(
            db,
            batch=batch,
            legs=legs,
            expected_total_payout_usdt=amounts["expected_total_payout_usdt"],
            now=now,
        )

        batch.status = PAYOUT_BATCH_STATUS_COMPLETED
        batch.reconciliation_json = _json_dict(
            {
                "live": True,
                "ok": True,
                "gas_ready": True,
                "payouts_confirmed": True,
                "balance_refresh_confirmed": True,
                "no_order_success_finalization": True,
                "no_shares_reserved_release": True,
                "no_usdt_reserved_release": True,
                "no_user_position_share_mutation": True,
                "no_shares_outstanding_mutation": True,
                "no_pricing_unlock": True,
                "no_accounting_finalization": True,
            }
        )
        batch.report_json = _json_dict(
            {
                "stage": "25",
                "live": True,
                "ok": True,
                "expected_total_payout_usdt": amounts["expected_total_payout_usdt"],
                "confirmed_total_payout_usdt": batch.confirmed_total_payout_usdt,
                "payout_leg_count": len(legs),
                "confirmed_payout_leg_count": batch.confirmed_payout_leg_count,
                "status": PAYOUT_BATCH_STATUS_COMPLETED,
            }
        )
        batch.updated_at = now

        settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED
        settlement_batch.updated_at = now

        db.add(batch)
        db.add(settlement_batch)
        db.flush()

        return NegativePayoutResult(
            ok=True,
            payout_batch_id=int(batch.id),
            settlement_batch_id=int(settlement_batch.id),
            bybit_flow_id=int(bybit_flow.id),
            fund_id=int(fund.id),
            fund_code=str(fund.code),
            status_before=status_before,
            status_after=batch.status,
            settlement_status_before=settlement_status_before,
            settlement_status_after=settlement_batch.status,
            payout_leg_count=batch.payout_leg_count,
            confirmed_payout_leg_count=batch.confirmed_payout_leg_count,
            expected_total_payout_usdt=str(amounts["expected_total_payout_usdt"]),
            confirmed_total_payout_usdt=str(batch.confirmed_total_payout_usdt),
            diagnostics={
                "live": True,
                "gas_status": batch.gas_status,
                "balance_refresh_status": batch.balance_refresh_status,
            },
        )

    except NegativePayoutFlowError as exc:
        if "batch" not in locals() or batch is None:
            raise

        return _set_failed(
            batch=batch,
            settlement_batch=settlement_batch,
            fund=fund,
            status_before=status_before,
            settlement_status_before=settlement_status_before,
            error=str(exc),
            now=now,
        )


def execute_negative_payout_flow_mock(
    db: Session,
    *,
    settlement_batch_id: int,
    mock_payout: NegativePayoutMock,
    now=None,
) -> NegativePayoutResult:
    _validate_stage23_5_safety(mock_payout)

    now = now or utcnow()

    settlement_batch = _lock_settlement_batch(
        db,
        settlement_batch_id=int(settlement_batch_id),
    )
    settlement_status_before = str(settlement_batch.status)

    bybit_flow = _lock_completed_bybit_flow(
        db,
        settlement_batch_id=int(settlement_batch.id),
    )
    fund = _get_fund(db, fund_id=int(settlement_batch.fund_id))

    existing_batch = _lock_existing_payout_batch(
        db,
        settlement_batch_id=int(settlement_batch.id),
    )

    status_before = str(existing_batch.status) if existing_batch is not None else None

    try:
        amounts = _validate_bybit_flow_input(
            settlement_batch=settlement_batch,
            bybit_flow=bybit_flow,
            allow_completed_settlement_status=(
                existing_batch is not None
                and existing_batch.status == PAYOUT_BATCH_STATUS_COMPLETED
            ),
        )
        settlement_wallet = _validate_settlement_wallet(
            db,
            fund_id=int(fund.id),
            bybit_flow=bybit_flow,
        )
        plan = _build_payout_plan(
            db,
            settlement_batch=settlement_batch,
            expected_total_payout_usdt=amounts["expected_total_payout_usdt"],
        )

        batch = _new_or_existing_payout_batch(
            db,
            existing=existing_batch,
            settlement_batch=settlement_batch,
            bybit_flow=bybit_flow,
            settlement_wallet=settlement_wallet,
            expected_total_payout_usdt=amounts["expected_total_payout_usdt"],
        )
        status_before = str(batch.status)

        if batch.status == PAYOUT_BATCH_STATUS_COMPLETED:
            if _completed_batch_matches(
                db,
                batch=batch,
                settlement_batch=settlement_batch,
                plan=plan,
                expected_total_payout_usdt=amounts["expected_total_payout_usdt"],
                tx_hash_prefix=mock_payout.payouts.tx_hash_prefix,
            ):
                return NegativePayoutResult(
                    ok=True,
                    payout_batch_id=int(batch.id),
                    settlement_batch_id=int(settlement_batch.id),
                    bybit_flow_id=int(bybit_flow.id),
                    fund_id=int(fund.id),
                    fund_code=str(fund.code),
                    status_before=status_before,
                    status_after=batch.status,
                    settlement_status_before=settlement_status_before,
                    settlement_status_after=settlement_batch.status,
                    payout_leg_count=batch.payout_leg_count,
                    confirmed_payout_leg_count=batch.confirmed_payout_leg_count,
                    expected_total_payout_usdt=str(batch.expected_total_payout_usdt),
                    confirmed_total_payout_usdt=str(batch.confirmed_total_payout_usdt),
                    idempotent=True,
                    diagnostics={"idempotent": True},
                )

            return _set_failed(
                batch=batch,
                settlement_batch=settlement_batch,
                fund=fund,
                status_before=status_before,
                settlement_status_before=settlement_status_before,
                error="Existing completed payout batch does not match expected amount/address/tx_hash",
                now=now,
            )

        batch.bybit_flow_id = int(bybit_flow.id)
        batch.fund_id = int(fund.id)
        batch.coin = settings.NEGATIVE_NET_PAYOUT_COIN
        batch.chain = settings.NEGATIVE_NET_PAYOUT_CHAIN
        batch.settlement_wallet_id = int(settlement_wallet.id)
        batch.settlement_wallet_address = str(settlement_wallet.address)
        batch.expected_total_payout_usdt = amounts["expected_total_payout_usdt"]
        batch.planned_total_payout_usdt = amounts["expected_total_payout_usdt"]
        batch.confirmed_total_payout_usdt = None
        batch.payout_leg_count = len(plan)
        batch.confirmed_payout_leg_count = 0
        batch.payout_plan_json = _plan_to_json(plan)
        batch.status = PAYOUT_BATCH_STATUS_PAYOUTS_PLANNED
        batch.updated_at = now

        legs = _create_planned_legs(
            db,
            batch=batch,
            settlement_batch=settlement_batch,
            bybit_flow=bybit_flow,
            settlement_wallet=settlement_wallet,
            plan=plan,
            now=now,
        )

        paused_result = _apply_gas_check_or_pause(
            db,
            batch=batch,
            settlement_batch=settlement_batch,
            fund=fund,
            mock_payout=mock_payout,
            status_before=status_before,
            settlement_status_before=settlement_status_before,
            now=now,
        )
        if paused_result is not None:
            return paused_result

        settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_PAYOUT_PROCESSING
        settlement_batch.updated_at = now

        batch.payout_started_at = now
        batch.status = PAYOUT_BATCH_STATUS_PAYOUTS_MOCKED
        for leg in legs:
            # Stage 24 Operation Guard live-boundary hook.
            # Future real BSC redeem payout must call:
            # require_stage24_bsc_redeem_payout_guard(
            #     db,
            #     fund_id=int(fund.id),
            #     settlement_batch_id=int(settlement_batch.id),
            #     amount_usdt=leg.amount_usdt,
            #     request_id=deterministic_payout_key(
            #         settlement_batch_id=int(settlement_batch.id),
            #         user_wallet_id=int(leg.user_wallet_id),
            #         amount_usdt=leg.amount_usdt,
            #     ),
            # )
            _mock_payout_leg(
                batch=batch,
                leg=leg,
                mock_payout=mock_payout,
                now=now,
            )

        confirmed_total = sum((dec(leg.amount_usdt) for leg in legs), ZERO)
        confirmed_count = sum(
            1 for leg in legs if leg.status == PAYOUT_LEG_STATUS_PAYOUT_CONFIRMED
        )

        if not _same_decimal(confirmed_total, amounts["expected_total_payout_usdt"]):
            raise NegativePayoutFlowError("Confirmed payout total mismatch")

        if confirmed_count != len(legs):
            raise NegativePayoutFlowError("Confirmed payout leg count mismatch")

        batch.confirmed_total_payout_usdt = confirmed_total
        batch.confirmed_payout_leg_count = confirmed_count
        batch.payout_completed_at = now
        batch.status = PAYOUT_BATCH_STATUS_PAYOUTS_CONFIRMED
        batch.payout_execution_json = _json_dict(
            {
                "mock_only": True,
                "confirmed_total_payout_usdt": confirmed_total,
                "confirmed_payout_leg_count": confirmed_count,
                "legs": [
                    {
                        "payout_leg_id": int(leg.id),
                        "user_id": int(leg.user_id),
                        "to_user_wallet_id": int(leg.to_user_wallet_id),
                        "to_address": leg.to_address,
                        "amount_usdt": leg.amount_usdt,
                        "tx_hash": leg.tx_hash,
                        "confirmations": leg.confirmations,
                    }
                    for leg in legs
                ],
                "no_real_bsc_calls": True,
                "no_real_usdt_transfers": True,
            }
        )

        _mock_balance_refresh(
            db,
            batch=batch,
            legs=legs,
            mock_payout=mock_payout,
            expected_total_payout_usdt=amounts["expected_total_payout_usdt"],
            now=now,
        )

        batch.status = PAYOUT_BATCH_STATUS_BALANCE_REFRESH_MOCKED
        batch.status = PAYOUT_BATCH_STATUS_COMPLETED
        batch.reconciliation_json = _json_dict(
            {
                "ok": True,
                "gas_ready": True,
                "payouts_confirmed": True,
                "balance_refresh_confirmed": True,
                "no_order_success_finalization": True,
                "no_shares_reserved_release": True,
                "no_usdt_reserved_release": True,
                "no_user_position_share_mutation": True,
                "no_shares_outstanding_mutation": True,
                "no_pricing_unlock": True,
                "no_real_bsc_calls": True,
                "no_real_gas_topup": True,
                "no_real_usdt_transfers": True,
                "no_accounting_finalization": True,
            }
        )
        batch.report_json = _json_dict(
            {
                "stage": "23.5",
                "ok": True,
                "mock_id": mock_payout.mock_id,
                "expected_total_payout_usdt": amounts["expected_total_payout_usdt"],
                "confirmed_total_payout_usdt": confirmed_total,
                "payout_leg_count": len(legs),
                "confirmed_payout_leg_count": confirmed_count,
                "status": PAYOUT_BATCH_STATUS_COMPLETED,
            }
        )
        batch.updated_at = now

        settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_PAYOUTS_CONFIRMED
        settlement_batch.updated_at = now

        return NegativePayoutResult(
            ok=True,
            payout_batch_id=int(batch.id),
            settlement_batch_id=int(settlement_batch.id),
            bybit_flow_id=int(bybit_flow.id),
            fund_id=int(fund.id),
            fund_code=str(fund.code),
            status_before=status_before,
            status_after=batch.status,
            settlement_status_before=settlement_status_before,
            settlement_status_after=settlement_batch.status,
            payout_leg_count=batch.payout_leg_count,
            confirmed_payout_leg_count=batch.confirmed_payout_leg_count,
            expected_total_payout_usdt=str(amounts["expected_total_payout_usdt"]),
            confirmed_total_payout_usdt=str(confirmed_total),
            diagnostics={
                "mock_id": mock_payout.mock_id,
                "gas_status": batch.gas_status,
                "balance_refresh_status": batch.balance_refresh_status,
            },
        )

    except NegativePayoutFlowError as exc:
        if "batch" not in locals() or batch is None:
            raise

        return _set_failed(
            batch=batch,
            settlement_batch=settlement_batch,
            fund=fund,
            status_before=status_before,
            settlement_status_before=settlement_status_before,
            error=str(exc),
            now=now,
        )