from __future__ import annotations

from decimal import Decimal
from typing import Any
import uuid

from sqlalchemy.orm import Session

from app.bybit.asset_flows import (
    BybitAssetFlowError,
    BybitWithdrawalResult,
    create_master_withdrawal,
    create_universal_transfer,
    format_bybit_asset_amount,
    query_account_coin_balance,
    query_coin_info,
    query_master_withdrawal,
    query_universal_transfer,
)
from app.bybit.client import BybitV5Client
from app.config import settings
from app.models import (
    Fund,
    FundNegativeBybitFlow,
    FundNegativeSaleBatch,
    FundSettlementBatch,
    FundWallet,
)
from app.operation_guard.hooks import (
    require_bybit_master_withdrawal_guard,
    require_bybit_universal_transfer_guard,
)
from app.operation_guard.service import OperationGuardBlockedError
from app.settlement.gas_service import get_web3
from app.settlement.transfer_service import _check_tx_confirmed
from app.settlement.negative_bybit_flow_mock import load_negative_bybit_flow_mock_file
from app.settlement.negative_bybit_flow_types import (
    NegativeBybitFlowError,
    NegativeBybitFlowMock,
    NegativeBybitFlowResult,
    _json_dict,
    utcnow,
)
from app.settlement.negative_sale_snapshot import dec
from app.settlement.statuses import (
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
    BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING,
    BATCH_STATUS_NEGATIVE_NET_SALE_EXECUTED,
    BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_PENDING,
    BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_RECONCILING,
    BYBIT_FLOW_STATUS_COMPLETED,
    BYBIT_FLOW_STATUS_CREATED,
    BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW,
    BYBIT_FLOW_STATUS_PREFLIGHT_FAILED_REQUIRES_REVIEW,
    BYBIT_FLOW_STATUS_PREFLIGHT_PASSED,
    BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_CONFIRMED,
    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_MOCKED,
    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED,
    BYBIT_FLOW_STATUS_WITHDRAWAL_MOCKED,
    BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILED,
    SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED,
    SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED_WITH_EXTRA_SALE,
)


ZERO = Decimal("0")

NEGATIVE_NET_UNIVERSAL_TRANSFER_NAMESPACE = uuid.UUID(
    "8f7f7418-7ef0-4efb-9ccb-2b779d3d0f61"
)

NEGATIVE_NET_WITHDRAWAL_REQUEST_NAMESPACE = uuid.UUID(
    "75bce24d-3880-4d30-b7f7-ccf7cc8e0a19"
)

NEGATIVE_NET_UNIVERSAL_TRANSFER_AMOUNT_PRECISION = 6

BYBIT_SUCCESS_STATUSES = {"SUCCESS", "COMPLETED", "COMPLETE"}
BYBIT_PENDING_STATUSES = {
    "PENDING",
    "PROCESSING",
    "REVIEWING",
    "TOBEconfirmed",
    "TOBECONFIRMED",
}


def _bybit_status(value: str | None) -> str:
    return str(value or "").strip().upper()


def _is_bybit_success(value: str | None) -> bool:
    return _bybit_status(value) in BYBIT_SUCCESS_STATUSES


def _is_bybit_pending(value: str | None) -> bool:
    return _bybit_status(value) in BYBIT_PENDING_STATUSES


WITHDRAWAL_SUCCESS_STATUSES = {
    "SUCCESS",
    "BLOCKCHAINCONFIRMED",
    "COMPLETED",
    "COMPLETE",
}

WITHDRAWAL_PENDING_STATUSES = {
    "",
    "SECURITYCHECK",
    "PENDING",
    "MOREINFORMATIONREQUIRED",
    "PROCESSING",
    "REVIEWING",
}

WITHDRAWAL_FAILED_STATUSES = {
    "REJECT",
    "FAIL",
    "CANCELBYUSER",
    "FAILED",
}


def _is_withdrawal_success_like(value: str | None) -> bool:
    return _bybit_status(value) in WITHDRAWAL_SUCCESS_STATUSES


def _is_withdrawal_pending_like(value: str | None) -> bool:
    return _bybit_status(value) in WITHDRAWAL_PENDING_STATUSES


def _is_withdrawal_failed_like(value: str | None) -> bool:
    return _bybit_status(value) in WITHDRAWAL_FAILED_STATUSES


def _q10(value: Decimal) -> Decimal:
    return dec(value).quantize(Decimal("0.0000000001"))


def _same_decimal(left: Any, right: Any) -> bool:
    return _q10(dec(left)) == _q10(dec(right))


def _positive(value: Any, *, field_name: str) -> Decimal:
    amount = dec(value)
    if amount <= ZERO:
        raise NegativeBybitFlowError(f"{field_name} must be positive")

    return amount


def deterministic_universal_transfer_id(
    *,
    settlement_batch_id: int,
    fund_id: int,
    universal_transfer_amount_usdt: Decimal,
    from_member_id: str,
    to_member_id: str,
    from_account_type: str,
    to_account_type: str,
) -> str:
    seed = (
        "wildboar:negative-net-universal-transfer:"
        f"settlement_batch_id={int(settlement_batch_id)}:"
        f"fund_id={int(fund_id)}:"
        f"universal_transfer_amount_usdt={_q10(universal_transfer_amount_usdt)}:"
        f"from_member_id={str(from_member_id).strip()}:"
        f"to_member_id={str(to_member_id).strip()}:"
        f"from_account_type={str(from_account_type).strip().upper()}:"
        f"to_account_type={str(to_account_type).strip().upper()}"
    )
    return str(uuid.uuid5(NEGATIVE_NET_UNIVERSAL_TRANSFER_NAMESPACE, seed))


def deterministic_withdrawal_request_id(
    *,
    settlement_batch_id: int,
    fund_id: int,
    settlement_wallet_address: str,
    withdrawal_request_amount_usdt: Decimal,
    coin: str,
    chain: str,
) -> str:
    seed = (
        "wildboar:negative-net-withdrawal:"
        f"settlement_batch_id={int(settlement_batch_id)}:"
        f"fund_id={int(fund_id)}:"
        f"settlement_wallet_address={str(settlement_wallet_address).strip().lower()}:"
        f"withdrawal_request_amount_usdt={_q10(withdrawal_request_amount_usdt)}:"
        f"coin={str(coin).strip().upper()}:"
        f"chain={str(chain).strip().upper()}"
    )
    return "wbng" + uuid.uuid5(
        NEGATIVE_NET_WITHDRAWAL_REQUEST_NAMESPACE,
        seed,
    ).hex[:28]


def universal_transfer_actual_amount(
    *,
    required_master_usdt: Decimal,
    precision: int = NEGATIVE_NET_UNIVERSAL_TRANSFER_AMOUNT_PRECISION,
) -> tuple[str, Decimal]:
    raw_required = _positive(
        required_master_usdt,
        field_name="required_master_usdt",
    )
    amount_str = format_bybit_asset_amount(
        raw_required,
        precision=int(precision),
        rounding="up",
    )
    actual = dec(amount_str)
    quantum = Decimal("1").scaleb(-int(precision))

    if actual < raw_required:
        raise NegativeBybitFlowError(
            "Universal Transfer actual amount must cover required_master_usdt"
        )

    if actual - raw_required > quantum:
        raise NegativeBybitFlowError(
            "Universal Transfer rounded surplus exceeds one precision quantum"
        )

    return amount_str, actual


def withdrawal_actual_amount(
    *,
    withdrawal_request_amount_usdt: Decimal,
    precision: int,
) -> tuple[str, Decimal]:
    raw_amount = _positive(
        withdrawal_request_amount_usdt,
        field_name="withdrawal_request_amount_usdt",
    )
    amount_str = format_bybit_asset_amount(
        raw_amount,
        precision=int(precision),
        rounding="down",
    )
    actual = dec(amount_str)

    if actual <= ZERO:
        raise NegativeBybitFlowError("Withdrawal actual amount must be positive")

    if actual > raw_amount:
        raise NegativeBybitFlowError("Withdrawal actual amount must not overpay user")

    return amount_str, actual


def choose_universal_transfer_account_route(
    client: BybitV5Client,
    *,
    coin: str,
    amount_usdt: Decimal,
    from_member_id: str,
    to_member_id: str,
) -> dict[str, Any]:
    candidates = [
        ("FUND", "FUND"),
        ("UNIFIED", "UNIFIED"),
    ]
    checked: list[dict[str, Any]] = []

    for from_account_type, to_account_type in candidates:
        balance = query_account_coin_balance(
            client,
            account_type=from_account_type,
            coin=coin,
            member_id=from_member_id,
            to_member_id=to_member_id,
            to_account_type=to_account_type,
            with_transfer_safe_amount=True,
            with_ltv_transfer_safe_amount=True,
        )

        item = {
            "from_account_type": from_account_type,
            "to_account_type": to_account_type,
            "walletBalance": str(balance.wallet_balance),
            "transferBalance": str(balance.transfer_balance),
            "transferSafeAmount": (
                str(balance.transfer_safe_amount)
                if balance.transfer_safe_amount is not None
                else None
            ),
            "ltvTransferSafeAmount": (
                str(balance.ltv_transfer_safe_amount)
                if balance.ltv_transfer_safe_amount is not None
                else None
            ),
        }
        checked.append(item)

        if balance.transfer_balance >= amount_usdt:
            return {
                "from_account_type": from_account_type,
                "to_account_type": to_account_type,
                "selected_transfer_balance": balance.transfer_balance,
                "checked": checked,
            }

    raise NegativeBybitFlowError(
        "No Universal Transfer account route has sufficient transferBalance: "
        f"required={amount_usdt}, checked={checked}"
    )


def require_stage24_bybit_universal_transfer_guard(
    db: Session,
    *,
    fund_id: int,
    settlement_batch_id: int,
    required_master_usdt: Decimal,
    universal_transfer_id: str,
) -> None:
    """
    Stage 24 integration hook.

    This must be called immediately before any future real Bybit Universal Transfer.
    Stage 24 does not execute the real transfer; Stage 25 live path must call this
    before the external action is attempted.
    """
    require_bybit_universal_transfer_guard(
        db,
        fund_id=int(fund_id),
        settlement_batch_id=int(settlement_batch_id),
        amount_usdt=required_master_usdt,
        request_id=universal_transfer_id,
        metadata={
            "source": "negative_bybit_flow",
            "boundary": "future_real_universal_transfer",
            "no_real_bybit_call_in_stage24": True,
        },
    )


def require_stage24_bybit_master_withdrawal_guard(
    db: Session,
    *,
    fund_id: int,
    settlement_batch_id: int,
    withdrawal_request_amount_usdt: Decimal,
    withdrawal_request_id: str,
) -> None:
    """
    Stage 24 integration hook.

    This must be called immediately before any future real Bybit master withdrawal.
    Stage 24 does not execute the real withdrawal; Stage 25 live path must call this
    before the external action is attempted.
    """
    require_bybit_master_withdrawal_guard(
        db,
        fund_id=int(fund_id),
        settlement_batch_id=int(settlement_batch_id),
        amount_usdt=withdrawal_request_amount_usdt,
        request_id=withdrawal_request_id,
        metadata={
            "source": "negative_bybit_flow",
            "boundary": "future_real_master_withdrawal",
            "no_real_bybit_call_in_stage24": True,
        },
    )


def _validate_stage23_4_safety(mock_flow: NegativeBybitFlowMock) -> None:
    if settings.NEGATIVE_NET_BYBIT_FLOW_ALLOW_LIVE:
        raise NegativeBybitFlowError(
            "Live negative-net Bybit flow is blocked in Stage 23.4"
        )

    if not settings.NEGATIVE_NET_BYBIT_FLOW_MOCK_ONLY:
        raise NegativeBybitFlowError(
            "Stage 23.4 requires NEGATIVE_NET_BYBIT_FLOW_MOCK_ONLY=true"
        )

    if not mock_flow.mock_only:
        raise NegativeBybitFlowError("Stage 23.4 Bybit flow requires mock_only=true")


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
        raise NegativeBybitFlowError(f"Settlement batch not found: {settlement_batch_id}")

    return settlement_batch


def _lock_sale_batch_for_settlement(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundNegativeSaleBatch:
    sale_batch = (
        db.query(FundNegativeSaleBatch)
        .filter(FundNegativeSaleBatch.settlement_batch_id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )
    if sale_batch is None:
        raise NegativeBybitFlowError(
            f"Negative sale batch not found for settlement_batch_id={settlement_batch_id}"
        )

    return sale_batch


def _lock_existing_flow(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundNegativeBybitFlow | None:
    return (
        db.query(FundNegativeBybitFlow)
        .filter(FundNegativeBybitFlow.settlement_batch_id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )


def _get_fund(db: Session, *, fund_id: int) -> Fund:
    fund = db.query(Fund).filter(Fund.id == int(fund_id)).first()
    if fund is None:
        raise NegativeBybitFlowError(f"Fund not found: {fund_id}")

    return fund


def _get_active_settlement_wallet(
    db: Session,
    *,
    fund_id: int,
) -> FundWallet:
    wallet = (
        db.query(FundWallet)
        .filter(FundWallet.fund_id == int(fund_id))
        .filter(FundWallet.is_active.is_(True))
        .filter(FundWallet.blockchain == "BSC")
        .filter(FundWallet.wallet_type == "settlement")
        .order_by(FundWallet.id.asc())
        .with_for_update()
        .first()
    )

    if wallet is None:
        raise NegativeBybitFlowError("Active BSC settlement wallet not found")

    if not wallet.address:
        raise NegativeBybitFlowError("Active settlement wallet address is empty")

    return wallet


def _validate_sale_batch_input(
    *,
    settlement_batch: FundSettlementBatch,
    sale_batch: FundNegativeSaleBatch,
) -> None:
    allowed_settlement_statuses = {
        BATCH_STATUS_NEGATIVE_NET_SALE_EXECUTED,
        BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
    }
    if settlement_batch.status not in allowed_settlement_statuses:
        raise NegativeBybitFlowError(
            "Settlement batch status must be "
            f"{BATCH_STATUS_NEGATIVE_NET_SALE_EXECUTED} or "
            f"{BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT}, "
            f"got {settlement_batch.status}"
        )

    if sale_batch.status not in {
        SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED,
        SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED_WITH_EXTRA_SALE,
    }:
        raise NegativeBybitFlowError(
            "Sale batch status must be "
            f"{SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED} or "
            f"{SALE_BATCH_STATUS_SALE_EXECUTION_COMPLETED_WITH_EXTRA_SALE}, "
            f"got {sale_batch.status}"
        )

    if sale_batch.settlement_batch_id != settlement_batch.id:
        raise NegativeBybitFlowError("Sale batch settlement_batch_id mismatch")

    if sale_batch.fund_id != settlement_batch.fund_id:
        raise NegativeBybitFlowError("Sale batch fund_id mismatch")

    if not _same_decimal(sale_batch.final_shortage_usdt, ZERO):
        raise NegativeBybitFlowError("Sale batch final_shortage_usdt must be 0")

    required_master = _positive(
        sale_batch.required_master_usdt,
        field_name="sale_batch.required_master_usdt",
    )
    final_available = dec(sale_batch.final_available_usdt)
    if final_available < required_master:
        raise NegativeBybitFlowError(
            "Sale batch final_available_usdt must be >= required_master_usdt"
        )


def _validate_target_fields(
    *,
    settlement_batch: FundSettlementBatch,
    sale_batch: FundNegativeSaleBatch,
) -> dict[str, Decimal]:
    required_master_usdt = _positive(
        settlement_batch.required_master_usdt,
        field_name="settlement_batch.required_master_usdt",
    )
    withdrawal_request_amount_usdt = _positive(
        settlement_batch.withdrawal_request_amount_usdt,
        field_name="settlement_batch.withdrawal_request_amount_usdt",
    )
    bybit_withdrawal_fee_usdt = _positive(
        settlement_batch.bybit_withdrawal_fee_usdt,
        field_name="settlement_batch.bybit_withdrawal_fee_usdt",
    )
    total_net_user_payout_usdt = _positive(
        settlement_batch.total_net_user_payout_usdt,
        field_name="settlement_batch.total_net_user_payout_usdt",
    )
    total_partial_month_fee_usdt = dec(settlement_batch.total_partial_month_fee_usdt)

    if total_partial_month_fee_usdt < ZERO:
        raise NegativeBybitFlowError(
            "settlement_batch.total_partial_month_fee_usdt must be >= 0"
        )

    if not _same_decimal(sale_batch.required_master_usdt, required_master_usdt):
        raise NegativeBybitFlowError("Sale batch required_master_usdt mismatch")

    if not _same_decimal(
        sale_batch.withdrawal_request_amount_usdt,
        withdrawal_request_amount_usdt,
    ):
        raise NegativeBybitFlowError("Sale batch withdrawal_request_amount_usdt mismatch")

    if not _same_decimal(
        sale_batch.total_net_user_payout_usdt,
        total_net_user_payout_usdt,
    ):
        raise NegativeBybitFlowError("Sale batch total_net_user_payout_usdt mismatch")

    if not _same_decimal(
        sale_batch.total_partial_month_fee_usdt,
        total_partial_month_fee_usdt,
    ):
        raise NegativeBybitFlowError("Sale batch total_partial_month_fee_usdt mismatch")

    if not _same_decimal(
        sale_batch.bybit_withdrawal_fee_usdt,
        bybit_withdrawal_fee_usdt,
    ):
        raise NegativeBybitFlowError("Sale batch bybit_withdrawal_fee_usdt mismatch")

    expected_required_master = (
        total_net_user_payout_usdt
        + bybit_withdrawal_fee_usdt
        + total_partial_month_fee_usdt
    )
    if not _same_decimal(required_master_usdt, expected_required_master):
        raise NegativeBybitFlowError(
            "required_master_usdt formula mismatch: "
            "expected total_net_user_payout_usdt + bybit_withdrawal_fee_usdt + "
            "total_partial_month_fee_usdt"
        )

    if not _same_decimal(withdrawal_request_amount_usdt, total_net_user_payout_usdt):
        raise NegativeBybitFlowError(
            "withdrawal_request_amount_usdt must equal total_net_user_payout_usdt"
        )

    return {
        "required_master_usdt": required_master_usdt,
        "withdrawal_request_amount_usdt": withdrawal_request_amount_usdt,
        "bybit_withdrawal_fee_usdt": bybit_withdrawal_fee_usdt,
        "total_net_user_payout_usdt": total_net_user_payout_usdt,
        "total_partial_month_fee_usdt": total_partial_month_fee_usdt,
    }


def _raw_target_amounts_from_settlement(
    *,
    settlement_batch: FundSettlementBatch,
) -> dict[str, Decimal]:
    return {
        "required_master_usdt": dec(settlement_batch.required_master_usdt),
        "withdrawal_request_amount_usdt": dec(
            settlement_batch.withdrawal_request_amount_usdt
        ),
        "bybit_withdrawal_fee_usdt": dec(settlement_batch.bybit_withdrawal_fee_usdt),
        "total_net_user_payout_usdt": dec(settlement_batch.total_net_user_payout_usdt),
        "total_partial_month_fee_usdt": dec(
            settlement_batch.total_partial_month_fee_usdt
        ),
    }


def _new_or_existing_flow(
    db: Session,
    *,
    existing: FundNegativeBybitFlow | None,
    settlement_batch: FundSettlementBatch,
    sale_batch: FundNegativeSaleBatch,
    amounts: dict[str, Decimal],
) -> FundNegativeBybitFlow:
    if existing is not None:
        return existing

    flow = FundNegativeBybitFlow(
        settlement_batch_id=int(settlement_batch.id),
        sale_batch_id=int(sale_batch.id),
        fund_id=int(settlement_batch.fund_id),
        status=BYBIT_FLOW_STATUS_CREATED,
        coin=settings.NEGATIVE_NET_BYBIT_FLOW_COIN,
        chain=settings.NEGATIVE_NET_BYBIT_FLOW_CHAIN,
        required_master_usdt=amounts["required_master_usdt"],
        withdrawal_request_amount_usdt=amounts["withdrawal_request_amount_usdt"],
        bybit_withdrawal_fee_usdt=amounts["bybit_withdrawal_fee_usdt"],
        retained_fees_usdt=amounts["total_partial_month_fee_usdt"],
    )
    db.add(flow)
    db.flush()
    return flow


def _set_failed(
    *,
    flow: FundNegativeBybitFlow,
    settlement_batch: FundSettlementBatch,
    fund: Fund | None,
    status_before: str | None,
    settlement_status_before: str | None,
    error: str,
    now,
    diagnostics: dict[str, Any] | None = None,
) -> NegativeBybitFlowResult:
    flow.status = BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    flow.error = error
    flow.updated_at = now
    flow.reconciliation_json = _json_dict(
        {
            "ok": False,
            "error": error,
            "diagnostics": diagnostics or {},
        }
    )
    flow.report_json = _json_dict(
        {
            "stage": "23.4",
            "ok": False,
            "error": error,
            "final_state": BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW,
        }
    )

    settlement_batch.status = BATCH_STATUS_FAILED_REQUIRES_REVIEW
    settlement_batch.error = error
    settlement_batch.updated_at = now

    return NegativeBybitFlowResult(
        ok=False,
        flow_id=int(flow.id) if flow.id is not None else None,
        settlement_batch_id=int(settlement_batch.id),
        sale_batch_id=int(flow.sale_batch_id) if flow.sale_batch_id is not None else None,
        fund_id=int(flow.fund_id) if flow.fund_id is not None else None,
        fund_code=str(fund.code) if fund is not None else None,
        status_before=status_before,
        status_after=flow.status,
        settlement_status_before=settlement_status_before,
        settlement_status_after=settlement_batch.status,
        universal_transfer_id=flow.universal_transfer_id,
        withdrawal_request_id=flow.withdrawal_request_id,
        settlement_wallet_address=flow.settlement_wallet_address,
        error=error,
        diagnostics=diagnostics or {},
    )


def _completed_flow_matches(
    *,
    flow: FundNegativeBybitFlow,
    expected_transfer_id: str,
    expected_request_id: str,
    expected_required_master: Decimal,
    expected_withdrawal_amount: Decimal,
    expected_withdrawal_fee: Decimal,
    expected_address: str,
) -> bool:
    return (
        flow.status == BYBIT_FLOW_STATUS_COMPLETED
        and flow.universal_transfer_id == expected_transfer_id
        and flow.withdrawal_request_id == expected_request_id
        and _same_decimal(flow.required_master_usdt, expected_required_master)
        and _same_decimal(flow.withdrawal_request_amount_usdt, expected_withdrawal_amount)
        and _same_decimal(flow.bybit_withdrawal_fee_usdt, expected_withdrawal_fee)
        and str(flow.settlement_wallet_address) == str(expected_address)
        and str(flow.withdrawal_address) == str(expected_address)
    )


def _mock_success(value: str | None) -> bool:
    return str(value or "").strip().upper() == "SUCCESS"


def _receipt_confirmed(value: str | None) -> bool:
    return str(value or "").strip().upper() == "CONFIRMED"


def _required_record(
    raw: dict[str, Any],
    *,
    record_name: str,
) -> dict[str, Any]:
    record = raw.get("record")
    if not isinstance(record, dict):
        raise NegativeBybitFlowError(f"{record_name} record is required")

    return record


def _required_record_field(
    record: dict[str, Any],
    *,
    record_name: str,
    field_name: str,
) -> Any:
    if field_name not in record:
        raise NegativeBybitFlowError(
            f"{record_name} record missing required field: {field_name}"
        )

    value = record.get(field_name)
    if value is None or str(value).strip() == "":
        raise NegativeBybitFlowError(
            f"{record_name} record missing required field: {field_name}"
        )

    return value


def _resolve_auto(value: Any, *, expected: Any) -> Any:
    if isinstance(value, str) and value.strip().upper() == "AUTO":
        return expected

    return value


def _required_record_str(
    record: dict[str, Any],
    *,
    record_name: str,
    field_name: str,
    expected: str | None = None,
) -> str:
    value = _required_record_field(
        record,
        record_name=record_name,
        field_name=field_name,
    )
    if expected is not None:
        value = _resolve_auto(value, expected=expected)

    return str(value).strip()


def _required_record_decimal(
    record: dict[str, Any],
    *,
    record_name: str,
    field_name: str,
    expected: Decimal | None = None,
) -> Decimal:
    value = _required_record_field(
        record,
        record_name=record_name,
        field_name=field_name,
    )
    if expected is not None:
        value = _resolve_auto(value, expected=expected)

    return dec(value)


def _required_receipt_field(
    raw: dict[str, Any],
    *,
    field_name: str,
) -> Any:
    if field_name not in raw:
        raise NegativeBybitFlowError(
            f"Settlement wallet receipt missing required field: {field_name}"
        )

    value = raw.get(field_name)
    if value is None or str(value).strip() == "":
        raise NegativeBybitFlowError(
            f"Settlement wallet receipt missing required field: {field_name}"
        )

    return value


def _required_receipt_str(
    raw: dict[str, Any],
    *,
    field_name: str,
    expected: str | None = None,
) -> str:
    value = _required_receipt_field(raw, field_name=field_name)
    if expected is not None:
        value = _resolve_auto(value, expected=expected)

    return str(value).strip()


def _required_receipt_decimal(
    raw: dict[str, Any],
    *,
    field_name: str,
    expected: Decimal | None = None,
) -> Decimal:
    value = _required_receipt_field(raw, field_name=field_name)
    if expected is not None:
        value = _resolve_auto(value, expected=expected)

    return dec(value)


def _validate_universal_transfer_record(
    *,
    raw: dict[str, Any],
    expected_transfer_id: str,
    expected_amount_usdt: Decimal,
    expected_coin: str,
    expected_from_sub_uid: str,
    expected_to_master_uid: str,
) -> dict[str, Any]:
    record_name = "Universal Transfer"
    record = _required_record(raw, record_name=record_name)

    transfer_id = _required_record_str(
        record,
        record_name=record_name,
        field_name="transferId",
        expected=expected_transfer_id,
    )
    if transfer_id != expected_transfer_id:
        raise NegativeBybitFlowError("Universal Transfer transferId mismatch")

    amount_usdt = _required_record_decimal(
        record,
        record_name=record_name,
        field_name="amount_usdt",
        expected=expected_amount_usdt,
    )
    if not _same_decimal(amount_usdt, expected_amount_usdt):
        raise NegativeBybitFlowError("Universal Transfer amount mismatch")

    coin = _required_record_str(
        record,
        record_name=record_name,
        field_name="coin",
        expected=expected_coin,
    )
    if coin != expected_coin:
        raise NegativeBybitFlowError("Universal Transfer coin mismatch")

    from_sub_uid = _required_record_str(
        record,
        record_name=record_name,
        field_name="from_sub_uid",
        expected=expected_from_sub_uid,
    )
    if from_sub_uid != expected_from_sub_uid:
        raise NegativeBybitFlowError("Universal Transfer from_sub_uid mismatch")

    to_master_uid = _required_record_str(
        record,
        record_name=record_name,
        field_name="to_master_uid",
        expected=expected_to_master_uid,
    )
    if to_master_uid != expected_to_master_uid:
        raise NegativeBybitFlowError("Universal Transfer to_master_uid mismatch")

    status = _required_record_str(
        record,
        record_name=record_name,
        field_name="status",
        expected="SUCCESS",
    )
    if not _mock_success(status):
        raise NegativeBybitFlowError("Universal Transfer status mismatch")

    return _json_dict(
        {
            "transferId": transfer_id,
            "amount_usdt": amount_usdt,
            "coin": coin,
            "from_sub_uid": from_sub_uid,
            "to_master_uid": to_master_uid,
            "status": status,
            "raw": record,
        }
    )


def _validate_withdrawal_record(
    *,
    raw: dict[str, Any],
    expected_request_id: str,
    expected_withdrawal_id: str,
    expected_amount_usdt: Decimal,
    expected_fee_usdt: Decimal,
    expected_coin: str,
    expected_chain: str,
    expected_address: str,
    expected_tx_hash: str,
) -> dict[str, Any]:
    record_name = "Withdrawal"
    record = _required_record(raw, record_name=record_name)

    request_id = _required_record_str(
        record,
        record_name=record_name,
        field_name="requestId",
        expected=expected_request_id,
    )
    if request_id != expected_request_id:
        raise NegativeBybitFlowError("Withdrawal requestId mismatch")

    withdrawal_id = _required_record_str(
        record,
        record_name=record_name,
        field_name="withdrawal_id",
        expected=expected_withdrawal_id,
    )
    if withdrawal_id != expected_withdrawal_id:
        raise NegativeBybitFlowError("Withdrawal ID mismatch")

    amount_usdt = _required_record_decimal(
        record,
        record_name=record_name,
        field_name="amount_usdt",
        expected=expected_amount_usdt,
    )
    if not _same_decimal(amount_usdt, expected_amount_usdt):
        raise NegativeBybitFlowError("Withdrawal amount mismatch")

    fee_usdt = _required_record_decimal(
        record,
        record_name=record_name,
        field_name="fee_usdt",
        expected=expected_fee_usdt,
    )
    if not _same_decimal(fee_usdt, expected_fee_usdt):
        raise NegativeBybitFlowError("Withdrawal fee mismatch")

    coin = _required_record_str(
        record,
        record_name=record_name,
        field_name="coin",
        expected=expected_coin,
    )
    if coin != expected_coin:
        raise NegativeBybitFlowError("Withdrawal coin mismatch")

    chain = _required_record_str(
        record,
        record_name=record_name,
        field_name="chain",
        expected=expected_chain,
    )
    if chain != expected_chain:
        raise NegativeBybitFlowError("Withdrawal chain mismatch")

    address = _required_record_str(
        record,
        record_name=record_name,
        field_name="address",
        expected=expected_address,
    )
    if address != expected_address:
        raise NegativeBybitFlowError("Withdrawal address mismatch")

    tx_hash = _required_record_str(
        record,
        record_name=record_name,
        field_name="tx_hash",
        expected=expected_tx_hash,
    )
    if tx_hash != expected_tx_hash:
        raise NegativeBybitFlowError("Withdrawal tx_hash mismatch")

    status = _required_record_str(
        record,
        record_name=record_name,
        field_name="status",
        expected="SUCCESS",
    )
    if not _mock_success(status):
        raise NegativeBybitFlowError("Withdrawal status mismatch")

    return _json_dict(
        {
            "requestId": request_id,
            "withdrawal_id": withdrawal_id,
            "amount_usdt": amount_usdt,
            "fee_usdt": fee_usdt,
            "coin": coin,
            "chain": chain,
            "address": address,
            "tx_hash": tx_hash,
            "status": status,
            "raw": record,
        }
    )


def _validate_settlement_wallet_receipt(
    *,
    raw: dict[str, Any],
    expected_address: str,
    expected_received_amount_usdt: Decimal,
    expected_tx_hash: str,
) -> dict[str, Any]:
    status = _required_receipt_str(
        raw,
        field_name="status",
        expected="CONFIRMED",
    )
    if not _receipt_confirmed(status):
        raise NegativeBybitFlowError("Settlement wallet receipt status mismatch")

    address = _required_receipt_str(
        raw,
        field_name="address",
        expected=expected_address,
    )
    if address != expected_address:
        raise NegativeBybitFlowError("Settlement wallet receipt address mismatch")

    received_amount_usdt = _required_receipt_decimal(
        raw,
        field_name="received_amount_usdt",
        expected=expected_received_amount_usdt,
    )
    if not _same_decimal(received_amount_usdt, expected_received_amount_usdt):
        raise NegativeBybitFlowError("Settlement wallet received amount mismatch")

    tx_hash = _required_receipt_str(
        raw,
        field_name="tx_hash",
        expected=expected_tx_hash,
    )
    if tx_hash != expected_tx_hash:
        raise NegativeBybitFlowError("Settlement wallet receipt tx_hash mismatch")

    return _json_dict(
        {
            "status": status,
            "address": address,
            "received_usdt": received_amount_usdt,
            "tx_hash": tx_hash,
            "raw": raw,
        }
    )



def execute_negative_bybit_flow_live(
    db: Session,
    *,
    settlement_batch_id: int,
    bybit_client: BybitV5Client,
    fund_sub_uid: str,
    master_uid: str,
    now=None,
) -> NegativeBybitFlowResult:
    """
    Real production-live negative-net Bybit flow:

    1. fund subaccount -> master Universal Transfer;
    2. master -> settlement wallet withdrawal;
    3. withdrawal tx confirmation on BSC;
    4. settlement batch becomes cash-ready for BSC payout only after reconciliation.

    Does not commit. Caller controls transaction boundary.
    """
    if bybit_client is None:
        raise NegativeBybitFlowError("bybit_client is required for live negative-net Bybit flow")

    now = now or utcnow()

    settlement_batch = _lock_settlement_batch(
        db,
        settlement_batch_id=int(settlement_batch_id),
    )
    settlement_status_before = str(settlement_batch.status)

    sale_batch = _lock_sale_batch_for_settlement(
        db,
        settlement_batch_id=int(settlement_batch.id),
    )

    fund = _get_fund(db, fund_id=int(settlement_batch.fund_id))
    amounts = _raw_target_amounts_from_settlement(
        settlement_batch=settlement_batch,
    )

    existing_flow = _lock_existing_flow(
        db,
        settlement_batch_id=int(settlement_batch.id),
    )

    flow = _new_or_existing_flow(
        db,
        existing=existing_flow,
        settlement_batch=settlement_batch,
        sale_batch=sale_batch,
        amounts=amounts,
    )
    status_before = str(flow.status)

    try:
        _validate_sale_batch_input(
            settlement_batch=settlement_batch,
            sale_batch=sale_batch,
        )
        amounts = _validate_target_fields(
            settlement_batch=settlement_batch,
            sale_batch=sale_batch,
        )

        wallet = _get_active_settlement_wallet(db, fund_id=int(fund.id))

        coin = settings.NEGATIVE_NET_BYBIT_FLOW_COIN
        chain = settings.NEGATIVE_NET_BYBIT_FLOW_CHAIN

        if coin != "USDT":
            raise NegativeBybitFlowError("Live negative-net Bybit flow coin must be USDT")

        if chain != "BSC":
            raise NegativeBybitFlowError("Live negative-net Bybit flow chain must be BSC")

        if wallet.blockchain != chain:
            raise NegativeBybitFlowError("Settlement wallet blockchain mismatch")

        if wallet.wallet_type != "settlement":
            raise NegativeBybitFlowError("Settlement wallet type must be settlement")

        settlement_wallet_address = str(wallet.address)

        from_sub_uid = str(fund_sub_uid).strip()
        to_master_uid = str(master_uid).strip()

        if not from_sub_uid:
            raise NegativeBybitFlowError("fund_sub_uid is required for live Universal Transfer")

        if not to_master_uid:
            raise NegativeBybitFlowError("master_uid is required for live Universal Transfer")

        universal_transfer_amount_str, universal_transfer_amount_actual = (
            universal_transfer_actual_amount(
                required_master_usdt=amounts["required_master_usdt"],
            )
        )

        route = choose_universal_transfer_account_route(
            bybit_client,
            coin=coin,
            amount_usdt=universal_transfer_amount_actual,
            from_member_id=from_sub_uid,
            to_member_id=to_master_uid,
        )

        transfer_id = deterministic_universal_transfer_id(
            settlement_batch_id=int(settlement_batch.id),
            fund_id=int(fund.id),
            universal_transfer_amount_usdt=universal_transfer_amount_actual,
            from_member_id=from_sub_uid,
            to_member_id=to_master_uid,
            from_account_type=route["from_account_type"],
            to_account_type=route["to_account_type"],
        )

        coin_info = query_coin_info(
            bybit_client,
            coin=coin,
            chain=chain,
        )

        if str(coin_info.chain_withdraw or "").strip() != "1":
            raise NegativeBybitFlowError("BSC USDT withdrawal is disabled by Bybit coin info")

        if coin_info.withdraw_min > amounts["withdrawal_request_amount_usdt"]:
            raise NegativeBybitFlowError(
                "Withdrawal amount is below Bybit withdrawMin: "
                f"withdrawMin={coin_info.withdraw_min}, "
                f"amount={amounts['withdrawal_request_amount_usdt']}"
            )

        withdrawal_amount_str, withdrawal_amount_actual = withdrawal_actual_amount(
            withdrawal_request_amount_usdt=amounts["withdrawal_request_amount_usdt"],
            precision=int(coin_info.min_accuracy),
        )

        request_id = deterministic_withdrawal_request_id(
            settlement_batch_id=int(settlement_batch.id),
            fund_id=int(fund.id),
            settlement_wallet_address=settlement_wallet_address,
            withdrawal_request_amount_usdt=withdrawal_amount_actual,
            coin=coin,
            chain=chain,
        )

        if flow.status == BYBIT_FLOW_STATUS_COMPLETED:
            if _completed_flow_matches(
                flow=flow,
                expected_transfer_id=transfer_id,
                expected_request_id=request_id,
                expected_required_master=amounts["required_master_usdt"],
                expected_withdrawal_amount=amounts["withdrawal_request_amount_usdt"],
                expected_withdrawal_fee=amounts["bybit_withdrawal_fee_usdt"],
                expected_address=settlement_wallet_address,
            ):
                return NegativeBybitFlowResult(
                    ok=True,
                    flow_id=int(flow.id),
                    settlement_batch_id=int(settlement_batch.id),
                    sale_batch_id=int(sale_batch.id),
                    fund_id=int(fund.id),
                    fund_code=str(fund.code),
                    status_before=status_before,
                    status_after=flow.status,
                    settlement_status_before=settlement_status_before,
                    settlement_status_after=settlement_batch.status,
                    universal_transfer_id=flow.universal_transfer_id,
                    withdrawal_request_id=flow.withdrawal_request_id,
                    settlement_wallet_address=flow.settlement_wallet_address,
                    idempotent=True,
                    diagnostics={"idempotent": True, "live": True},
                )

            return _set_failed(
                flow=flow,
                settlement_batch=settlement_batch,
                fund=fund,
                status_before=status_before,
                settlement_status_before=settlement_status_before,
                error=(
                    "Existing completed live Bybit flow does not match expected "
                    "transferId/requestId/amount/address"
                ),
                now=now,
                diagnostics={
                    "expected_transfer_id": transfer_id,
                    "actual_transfer_id": flow.universal_transfer_id,
                    "expected_request_id": request_id,
                    "actual_request_id": flow.withdrawal_request_id,
                    "expected_address": settlement_wallet_address,
                    "actual_address": flow.settlement_wallet_address,
                },
            )

        flow.coin = coin
        flow.chain = chain
        flow.required_master_usdt = amounts["required_master_usdt"]
        flow.withdrawal_request_amount_usdt = amounts["withdrawal_request_amount_usdt"]
        flow.bybit_withdrawal_fee_usdt = amounts["bybit_withdrawal_fee_usdt"]
        flow.retained_fees_usdt = amounts["total_partial_month_fee_usdt"]
        flow.settlement_wallet_id = int(wallet.id)
        flow.settlement_wallet_address = settlement_wallet_address
        flow.from_sub_uid = from_sub_uid
        flow.to_master_uid = to_master_uid
        flow.from_account_type = route["from_account_type"]
        flow.to_account_type = route["to_account_type"]
        flow.universal_transfer_id = transfer_id
        flow.withdrawal_request_id = request_id

        flow.preflight_passed = True
        flow.preflight_error = None
        flow.preflight_json = _json_dict(
            {
                "live": True,
                "coin": coin,
                "chain": chain,
                "settlement_wallet_id": int(wallet.id),
                "settlement_wallet_address": settlement_wallet_address,
                "from_sub_uid": flow.from_sub_uid,
                "to_master_uid": flow.to_master_uid,
                "fee_type": settings.NEGATIVE_NET_WITHDRAWAL_FEE_TYPE,
                "universal_transfer_required_raw": str(amounts["required_master_usdt"]),
                "universal_transfer_amount_str": universal_transfer_amount_str,
                "universal_transfer_amount_actual": str(universal_transfer_amount_actual),
                "universal_transfer_amount_precision": NEGATIVE_NET_UNIVERSAL_TRANSFER_AMOUNT_PRECISION,
                "universal_transfer_route": route,
                "withdrawal_request_amount_raw": str(amounts["withdrawal_request_amount_usdt"]),
                "withdrawal_amount_str": withdrawal_amount_str,
                "withdrawal_amount_actual": str(withdrawal_amount_actual),
                "withdrawal_amount_precision": int(coin_info.min_accuracy),
                "withdrawal_account_type": "FUND",
                "withdrawal_force_chain": 1,
                "withdrawal_chain_withdraw": coin_info.chain_withdraw,
                "withdrawal_min": str(coin_info.withdraw_min),
            }
        )
        flow.status = BYBIT_FLOW_STATUS_PREFLIGHT_PASSED
        flow.updated_at = now

        settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
        settlement_batch.updated_at = now

        db.add(flow)
        db.add(settlement_batch)
        db.flush()

        # 1) Universal Transfer reconciliation before create:
        # deterministic transferId prevents duplicate create on rerun.
        transfer_record = query_universal_transfer(
            bybit_client,
            transfer_id=transfer_id,
        )

        prior_transfer_attempt = (
            flow.universal_transfer_created_at is not None
            or bool(flow.universal_transfer_status)
        )

        if transfer_record is None and prior_transfer_attempt:
            return _set_failed(
                flow=flow,
                settlement_batch=settlement_batch,
                fund=fund,
                status_before=status_before,
                settlement_status_before=settlement_status_before,
                error="Universal Transfer status unknown after prior attempt; no resend",
                now=now,
                diagnostics={"transfer_id": transfer_id, "no_duplicate_resend": True},
            )

        if transfer_record is None:
            try:
                guard_decision = require_bybit_universal_transfer_guard(
                    db,
                    fund_id=int(fund.id),
                    settlement_batch_id=int(settlement_batch.id),
                    amount_usdt=universal_transfer_amount_actual,
                    request_id=transfer_id,
                    metadata={
                        "source": "negative_bybit_flow_live",
                        "boundary": "fund_subaccount_to_master_universal_transfer",
                        "from_sub_uid": flow.from_sub_uid,
                        "to_master_uid": flow.to_master_uid,
                        "coin": coin,
                        "from_account_type": flow.from_account_type,
                        "to_account_type": flow.to_account_type,
                        "required_master_usdt_raw": str(amounts["required_master_usdt"]),
                        "universal_transfer_amount_str": universal_transfer_amount_str,
                        "universal_transfer_amount_actual": str(universal_transfer_amount_actual),
                        "universal_transfer_amount_precision": NEGATIVE_NET_UNIVERSAL_TRANSFER_AMOUNT_PRECISION,
                    },
                )
            except OperationGuardBlockedError as exc:
                return _set_failed(
                    flow=flow,
                    settlement_batch=settlement_batch,
                    fund=fund,
                    status_before=status_before,
                    settlement_status_before=settlement_status_before,
                    error=f"Operation Guard blocked Bybit Universal Transfer: {exc}",
                    now=now,
                    diagnostics={"transfer_id": transfer_id},
                )

            created_transfer = create_universal_transfer(
                bybit_client,
                transfer_id=transfer_id,
                coin=coin,
                amount_usdt=universal_transfer_amount_actual,
                amount_str=universal_transfer_amount_str,
                from_member_id=flow.from_sub_uid,
                to_member_id=flow.to_master_uid,
                from_account_type=flow.from_account_type,
                to_account_type=flow.to_account_type,
            )

            flow.universal_transfer_status = created_transfer.status
            flow.universal_transfer_amount_usdt = created_transfer.amount_usdt
            flow.universal_transfer_coin = coin
            flow.universal_transfer_created_at = now
            flow.universal_transfer_mock_json = _json_dict(
                {
                    "live": True,
                    "transferId": transfer_id,
                    "guard_event_id": guard_decision.event_id,
                    "raw": created_transfer.raw,
                }
            )
            flow.updated_at = now
            db.add(flow)
            db.flush()

            transfer_record = query_universal_transfer(
                bybit_client,
                transfer_id=transfer_id,
            )

            if transfer_record is None:
                settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
                settlement_batch.updated_at = now
                db.add(settlement_batch)
                db.flush()

                return NegativeBybitFlowResult(
                    ok=False,
                    flow_id=int(flow.id),
                    settlement_batch_id=int(settlement_batch.id),
                    sale_batch_id=int(sale_batch.id),
                    fund_id=int(fund.id),
                    fund_code=str(fund.code),
                    status_before=status_before,
                    status_after=flow.status,
                    settlement_status_before=settlement_status_before,
                    settlement_status_after=settlement_batch.status,
                    universal_transfer_id=transfer_id,
                    withdrawal_request_id=request_id,
                    settlement_wallet_address=settlement_wallet_address,
                    diagnostics={
                        "live": True,
                        "pending": "universal_transfer_record_not_found_yet",
                        "no_duplicate_resend": True,
                    },
                )

        if not _is_bybit_success(transfer_record.status):
            if _is_bybit_pending(transfer_record.status):
                flow.universal_transfer_status = transfer_record.status
                flow.updated_at = now
                settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
                settlement_batch.updated_at = now
                db.add(flow)
                db.add(settlement_batch)
                db.flush()

                return NegativeBybitFlowResult(
                    ok=False,
                    flow_id=int(flow.id),
                    settlement_batch_id=int(settlement_batch.id),
                    sale_batch_id=int(sale_batch.id),
                    fund_id=int(fund.id),
                    fund_code=str(fund.code),
                    status_before=status_before,
                    status_after=flow.status,
                    settlement_status_before=settlement_status_before,
                    settlement_status_after=settlement_batch.status,
                    universal_transfer_id=transfer_id,
                    withdrawal_request_id=request_id,
                    settlement_wallet_address=settlement_wallet_address,
                    diagnostics={
                        "live": True,
                        "pending": "universal_transfer_pending",
                        "status": transfer_record.status,
                    },
                )

            return _set_failed(
                flow=flow,
                settlement_batch=settlement_batch,
                fund=fund,
                status_before=status_before,
                settlement_status_before=settlement_status_before,
                error=f"Universal Transfer unexpected status: {transfer_record.status}",
                now=now,
                diagnostics={"transfer_id": transfer_id, "status": transfer_record.status},
            )

        if transfer_record.coin != coin:
            raise NegativeBybitFlowError("Universal Transfer coin mismatch")

        if not _same_decimal(transfer_record.amount_usdt, universal_transfer_amount_actual):
            raise NegativeBybitFlowError("Universal Transfer amount mismatch")

        if transfer_record.amount_usdt < amounts["required_master_usdt"]:
            raise NegativeBybitFlowError(
                "Universal Transfer amount does not cover required_master_usdt"
            )

        flow.universal_transfer_status = transfer_record.status
        flow.universal_transfer_amount_usdt = universal_transfer_amount_actual
        flow.universal_transfer_coin = coin
        flow.universal_transfer_confirmed_at = now
        flow.universal_transfer_reconciliation_json = _json_dict(
            {
                "live": True,
                "ok": True,
                "transferId": transfer_id,
                "record": transfer_record.raw,
                "required_master_usdt_raw": str(amounts["required_master_usdt"]),
                "universal_transfer_amount_actual": str(universal_transfer_amount_actual),
                "universal_transfer_amount_str": universal_transfer_amount_str,
                "from_account_type": flow.from_account_type,
                "to_account_type": flow.to_account_type,
            }
        )
        flow.status = BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED
        flow.updated_at = now
        db.add(flow)
        db.flush()

        # 2) Master withdrawal reconciliation before create:
        # deterministic requestId prevents duplicate withdrawal on rerun.
        withdrawal_record = query_master_withdrawal(
            bybit_client,
            request_id=request_id,
        )

        prior_withdrawal_attempt = (
            flow.withdrawal_created_at is not None
            or bool(flow.withdrawal_status)
            or bool(flow.withdrawal_id)
        )

        if withdrawal_record is None and prior_withdrawal_attempt:
            return _set_failed(
                flow=flow,
                settlement_batch=settlement_batch,
                fund=fund,
                status_before=status_before,
                settlement_status_before=settlement_status_before,
                error="Bybit withdrawal status unknown after prior attempt; no resend",
                now=now,
                diagnostics={"request_id": request_id, "no_duplicate_resend": True},
            )

        if withdrawal_record is None:
            try:
                guard_decision = require_bybit_master_withdrawal_guard(
                    db,
                    fund_id=int(fund.id),
                    settlement_batch_id=int(settlement_batch.id),
                    amount_usdt=withdrawal_amount_actual,
                    request_id=request_id,
                    metadata={
                        "source": "negative_bybit_flow_live",
                        "boundary": "master_to_settlement_wallet_withdrawal",
                        "coin": coin,
                        "chain": chain,
                        "address": settlement_wallet_address,
                        "amount_str": withdrawal_amount_str,
                        "amount_actual": str(withdrawal_amount_actual),
                        "amount_raw": str(amounts["withdrawal_request_amount_usdt"]),
                        "amount_precision": int(coin_info.min_accuracy),
                        "account_type": "FUND",
                        "forceChain": 1,
                    },
                )
            except OperationGuardBlockedError as exc:
                return _set_failed(
                    flow=flow,
                    settlement_batch=settlement_batch,
                    fund=fund,
                    status_before=status_before,
                    settlement_status_before=settlement_status_before,
                    error=f"Operation Guard blocked Bybit master withdrawal: {exc}",
                    now=now,
                    diagnostics={"request_id": request_id},
                )

            created_withdrawal = create_master_withdrawal(
                bybit_client,
                request_id=request_id,
                coin=coin,
                chain=chain,
                address=settlement_wallet_address,
                amount_usdt=withdrawal_amount_actual,
                amount_str=withdrawal_amount_str,
                amount_precision=int(coin_info.min_accuracy),
                fee_type=int(settings.NEGATIVE_NET_WITHDRAWAL_FEE_TYPE),
                account_type="FUND",
                timestamp_ms=int(now.timestamp() * 1000),
                force_chain=1,
            )

            flow.withdrawal_id = created_withdrawal.withdrawal_id
            flow.withdrawal_status = created_withdrawal.status
            flow.withdrawal_amount_usdt = created_withdrawal.amount_usdt
            flow.withdrawal_fee_usdt = amounts["bybit_withdrawal_fee_usdt"]
            flow.withdrawal_coin = coin
            flow.withdrawal_chain = chain
            flow.withdrawal_address = settlement_wallet_address
            flow.withdrawal_created_at = now
            flow.withdrawal_mock_json = _json_dict(
                {
                    "live": True,
                    "requestId": request_id,
                    "guard_event_id": guard_decision.event_id,
                    "raw": created_withdrawal.raw,
                }
            )
            flow.status = BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED
            flow.updated_at = now

            settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_PENDING
            settlement_batch.updated_at = now

            db.add(flow)
            db.add(settlement_batch)
            db.flush()

            withdrawal_record = query_master_withdrawal(
                bybit_client,
                request_id=request_id,
            )

            if withdrawal_record is None:
                return NegativeBybitFlowResult(
                    ok=False,
                    flow_id=int(flow.id),
                    settlement_batch_id=int(settlement_batch.id),
                    sale_batch_id=int(sale_batch.id),
                    fund_id=int(fund.id),
                    fund_code=str(fund.code),
                    status_before=status_before,
                    status_after=flow.status,
                    settlement_status_before=settlement_status_before,
                    settlement_status_after=settlement_batch.status,
                    universal_transfer_id=transfer_id,
                    withdrawal_request_id=request_id,
                    settlement_wallet_address=settlement_wallet_address,
                    diagnostics={
                        "live": True,
                        "pending": "withdrawal_record_not_found_yet",
                        "no_duplicate_resend": True,
                    },
                )

        if _is_withdrawal_failed_like(withdrawal_record.status):
            return _set_failed(
                flow=flow,
                settlement_batch=settlement_batch,
                fund=fund,
                status_before=status_before,
                settlement_status_before=settlement_status_before,
                error=f"Withdrawal failed status: {withdrawal_record.status}",
                now=now,
                diagnostics={"request_id": request_id, "status": withdrawal_record.status},
            )

        if not _is_withdrawal_success_like(withdrawal_record.status):
            if _is_withdrawal_pending_like(withdrawal_record.status) or not withdrawal_record.tx_hash:
                flow.withdrawal_id = withdrawal_record.withdrawal_id or flow.withdrawal_id
                flow.withdrawal_status = withdrawal_record.status
                flow.withdrawal_amount_usdt = withdrawal_amount_actual
                flow.withdrawal_fee_usdt = amounts["bybit_withdrawal_fee_usdt"]
                flow.withdrawal_coin = coin
                flow.withdrawal_chain = chain
                flow.withdrawal_address = settlement_wallet_address
                flow.withdrawal_record_json = _json_dict(
                    {
                        "live": True,
                        "record": withdrawal_record.raw,
                        "pending": "withdrawal_pending_or_unknown_no_tx_hash",
                        "no_duplicate_resend": True,
                    }
                )
                flow.updated_at = now

                settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_PENDING
                settlement_batch.updated_at = now

                db.add(flow)
                db.add(settlement_batch)
                db.flush()

                return NegativeBybitFlowResult(
                    ok=False,
                    flow_id=int(flow.id),
                    settlement_batch_id=int(settlement_batch.id),
                    sale_batch_id=int(sale_batch.id),
                    fund_id=int(fund.id),
                    fund_code=str(fund.code),
                    status_before=status_before,
                    status_after=flow.status,
                    settlement_status_before=settlement_status_before,
                    settlement_status_after=settlement_batch.status,
                    universal_transfer_id=transfer_id,
                    withdrawal_request_id=request_id,
                    settlement_wallet_address=settlement_wallet_address,
                    diagnostics={
                        "live": True,
                        "pending": "withdrawal_pending_or_unknown_no_tx_hash",
                        "status": withdrawal_record.status,
                        "no_duplicate_resend": True,
                    },
                )

            return _set_failed(
                flow=flow,
                settlement_batch=settlement_batch,
                fund=fund,
                status_before=status_before,
                settlement_status_before=settlement_status_before,
                error=f"Withdrawal unexpected status: {withdrawal_record.status}",
                now=now,
                diagnostics={"request_id": request_id, "status": withdrawal_record.status},
            )

        if withdrawal_record.coin != coin:
            raise NegativeBybitFlowError("Withdrawal coin mismatch")

        if withdrawal_record.chain != chain:
            raise NegativeBybitFlowError("Withdrawal chain mismatch")

        if withdrawal_record.address != settlement_wallet_address:
            raise NegativeBybitFlowError("Withdrawal address mismatch")

        if not _same_decimal(
            withdrawal_record.amount_usdt,
            withdrawal_amount_actual,
        ):
            raise NegativeBybitFlowError("Withdrawal amount mismatch")

        if not withdrawal_record.tx_hash:
            flow.withdrawal_id = withdrawal_record.withdrawal_id or flow.withdrawal_id
            flow.withdrawal_status = withdrawal_record.status
            flow.withdrawal_amount_usdt = withdrawal_amount_actual
            flow.withdrawal_fee_usdt = amounts["bybit_withdrawal_fee_usdt"]
            flow.withdrawal_coin = coin
            flow.withdrawal_chain = chain
            flow.withdrawal_address = settlement_wallet_address
            flow.withdrawal_record_json = _json_dict(
                {
                    "live": True,
                    "record": withdrawal_record.raw,
                    "pending": "withdrawal_success_like_missing_tx_hash",
                    "no_duplicate_resend": True,
                }
            )
            flow.updated_at = now

            settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_RECONCILING
            settlement_batch.updated_at = now

            db.add(flow)
            db.add(settlement_batch)
            db.flush()

            return NegativeBybitFlowResult(
                ok=False,
                flow_id=int(flow.id),
                settlement_batch_id=int(settlement_batch.id),
                sale_batch_id=int(sale_batch.id),
                fund_id=int(fund.id),
                fund_code=str(fund.code),
                status_before=status_before,
                status_after=flow.status,
                settlement_status_before=settlement_status_before,
                settlement_status_after=settlement_batch.status,
                universal_transfer_id=transfer_id,
                withdrawal_request_id=request_id,
                settlement_wallet_address=settlement_wallet_address,
                diagnostics={
                    "live": True,
                    "pending": "withdrawal_success_like_missing_tx_hash",
                    "status": withdrawal_record.status,
                    "no_duplicate_resend": True,
                    "no_bsc_payout": True,
                    "no_accounting_finalization": True,
                },
            )

        flow.withdrawal_id = withdrawal_record.withdrawal_id or flow.withdrawal_id
        flow.withdrawal_status = withdrawal_record.status
        flow.withdrawal_amount_usdt = withdrawal_amount_actual
        flow.withdrawal_fee_usdt = amounts["bybit_withdrawal_fee_usdt"]
        flow.withdrawal_coin = coin
        flow.withdrawal_chain = chain
        flow.withdrawal_address = settlement_wallet_address
        flow.withdrawal_tx_hash = withdrawal_record.tx_hash
        flow.withdrawal_confirmed_at = now
        flow.withdrawal_record_json = _json_dict(
            {
                "live": True,
                "record": withdrawal_record.raw,
            }
        )
        flow.withdrawal_reconciliation_json = _json_dict(
            {
                "live": True,
                "ok": True,
                "requestId": request_id,
                "tx_hash": withdrawal_record.tx_hash,
                "matched_coin": True,
                "matched_chain": True,
                "matched_amount": True,
                "matched_address": True,
                "withdrawal_amount_actual": str(withdrawal_amount_actual),
                "withdrawal_amount_raw": str(amounts["withdrawal_request_amount_usdt"]),
            }
        )
        flow.status = BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILED
        flow.updated_at = now

        settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_RECONCILING
        settlement_batch.updated_at = now

        db.add(flow)
        db.add(settlement_batch)
        db.flush()

        w3 = get_web3()
        if not _check_tx_confirmed(w3, withdrawal_record.tx_hash):
            return NegativeBybitFlowResult(
                ok=False,
                flow_id=int(flow.id),
                settlement_batch_id=int(settlement_batch.id),
                sale_batch_id=int(sale_batch.id),
                fund_id=int(fund.id),
                fund_code=str(fund.code),
                status_before=status_before,
                status_after=flow.status,
                settlement_status_before=settlement_status_before,
                settlement_status_after=settlement_batch.status,
                universal_transfer_id=transfer_id,
                withdrawal_request_id=request_id,
                settlement_wallet_address=settlement_wallet_address,
                diagnostics={
                    "live": True,
                    "pending": "withdrawal_tx_not_confirmed_on_bsc",
                    "tx_hash": withdrawal_record.tx_hash,
                    "no_accounting_finalization": True,
                    "no_payout_execution": True,
                },
            )

        flow.settlement_wallet_receipt_status = "CONFIRMED"
        flow.settlement_wallet_received_usdt = withdrawal_amount_actual
        flow.settlement_wallet_receipt_tx_hash = withdrawal_record.tx_hash
        flow.settlement_wallet_receipt_confirmed_at = now
        flow.settlement_wallet_receipt_json = _json_dict(
            {
                "live": True,
                "status": "CONFIRMED",
                "address": settlement_wallet_address,
                "received_usdt": withdrawal_amount_actual,
                "tx_hash": withdrawal_record.tx_hash,
                "bsc_tx_confirmed": True,
            }
        )

        flow.status = BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_CONFIRMED
        flow.status = BYBIT_FLOW_STATUS_COMPLETED
        flow.reconciliation_json = _json_dict(
            {
                "live": True,
                "ok": True,
                "universal_transfer_reconciled": True,
                "withdrawal_reconciled": True,
                "settlement_wallet_receipt_confirmed": True,
                "no_seller_payouts": True,
                "no_accounting_finalization": True,
            }
        )
        flow.report_json = _json_dict(
            {
                "stage": "25",
                "live": True,
                "ok": True,
                "required_master_usdt": amounts["required_master_usdt"],
                "withdrawal_request_amount_usdt": amounts["withdrawal_request_amount_usdt"],
                "withdrawal_amount_actual": withdrawal_amount_actual,
                "bybit_withdrawal_fee_usdt": amounts["bybit_withdrawal_fee_usdt"],
                "retained_fees_usdt": amounts["total_partial_month_fee_usdt"],
                "settlement_wallet_address": settlement_wallet_address,
                "status": BYBIT_FLOW_STATUS_COMPLETED,
            }
        )
        flow.updated_at = now

        settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT
        settlement_batch.updated_at = now

        db.add(flow)
        db.add(settlement_batch)
        db.flush()

        return NegativeBybitFlowResult(
            ok=True,
            flow_id=int(flow.id),
            settlement_batch_id=int(settlement_batch.id),
            sale_batch_id=int(sale_batch.id),
            fund_id=int(fund.id),
            fund_code=str(fund.code),
            status_before=status_before,
            status_after=flow.status,
            settlement_status_before=settlement_status_before,
            settlement_status_after=settlement_batch.status,
            universal_transfer_id=flow.universal_transfer_id,
            withdrawal_request_id=flow.withdrawal_request_id,
            settlement_wallet_address=flow.settlement_wallet_address,
            diagnostics={
                "live": True,
                "withdrawal_tx_hash": withdrawal_record.tx_hash,
            },
        )

    except (
        NegativeBybitFlowError,
        BybitAssetFlowError,
    ) as exc:
        return _set_failed(
            flow=flow,
            settlement_batch=settlement_batch,
            fund=fund,
            status_before=status_before,
            settlement_status_before=settlement_status_before,
            error=str(exc),
            now=now,
        )


def execute_negative_bybit_flow_mock(
    db: Session,
    *,
    settlement_batch_id: int,
    mock_flow: NegativeBybitFlowMock,
    now=None,
) -> NegativeBybitFlowResult:
    _validate_stage23_4_safety(mock_flow)

    now = now or utcnow()

    settlement_batch = _lock_settlement_batch(
        db,
        settlement_batch_id=int(settlement_batch_id),
    )
    settlement_status_before = str(settlement_batch.status)

    sale_batch = _lock_sale_batch_for_settlement(
        db,
        settlement_batch_id=int(settlement_batch.id),
    )
    status_before = None

    fund = _get_fund(db, fund_id=int(settlement_batch.fund_id))

    amounts = _raw_target_amounts_from_settlement(
        settlement_batch=settlement_batch,
    )

    existing_flow = _lock_existing_flow(
        db,
        settlement_batch_id=int(settlement_batch.id),
    )

    if (
        settlement_batch.status == BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT
        and existing_flow is None
    ):
        raise NegativeBybitFlowError(
            "Settlement batch is cash-ready but has no existing Bybit flow"
        )

    flow = _new_or_existing_flow(
        db,
        existing=existing_flow,
        settlement_batch=settlement_batch,
        sale_batch=sale_batch,
        amounts=amounts,
    )
    status_before = str(flow.status)

    try:
        _validate_sale_batch_input(
            settlement_batch=settlement_batch,
            sale_batch=sale_batch,
        )
        amounts = _validate_target_fields(
            settlement_batch=settlement_batch,
            sale_batch=sale_batch,
        )

        wallet = _get_active_settlement_wallet(db, fund_id=int(fund.id))

        coin = settings.NEGATIVE_NET_BYBIT_FLOW_COIN
        chain = settings.NEGATIVE_NET_BYBIT_FLOW_CHAIN

        if coin != "USDT":
            raise NegativeBybitFlowError("Stage 23.4 coin must be USDT")

        if chain != "BSC":
            raise NegativeBybitFlowError("Stage 23.4 chain must be BSC")

        if settings.NEGATIVE_NET_REQUIRE_INTERNAL_SETTLEMENT_WALLET_WHITELIST:
            if not mock_flow.whitelist.internal_db_whitelist_passed:
                raise NegativeBybitFlowError(
                    "Internal DB settlement wallet whitelist mock failed"
                )

        if wallet.blockchain != settings.NEGATIVE_NET_BYBIT_FLOW_CHAIN:
            raise NegativeBybitFlowError("Settlement wallet blockchain mismatch")

        if wallet.wallet_type != "settlement":
            raise NegativeBybitFlowError("Settlement wallet type must be settlement")

        settlement_wallet_address = str(wallet.address)

        transfer_id = deterministic_universal_transfer_id(
            settlement_batch_id=int(settlement_batch.id),
            fund_id=int(fund.id),
            required_master_usdt=amounts["required_master_usdt"],
        )
        request_id = deterministic_withdrawal_request_id(
            settlement_batch_id=int(settlement_batch.id),
            fund_id=int(fund.id),
            settlement_wallet_address=settlement_wallet_address,
            withdrawal_request_amount_usdt=amounts["withdrawal_request_amount_usdt"],
        )

        if flow.status == BYBIT_FLOW_STATUS_COMPLETED:
            if _completed_flow_matches(
                flow=flow,
                expected_transfer_id=transfer_id,
                expected_request_id=request_id,
                expected_required_master=amounts["required_master_usdt"],
                expected_withdrawal_amount=amounts["withdrawal_request_amount_usdt"],
                expected_withdrawal_fee=amounts["bybit_withdrawal_fee_usdt"],
                expected_address=settlement_wallet_address,
            ):
                return NegativeBybitFlowResult(
                    ok=True,
                    flow_id=int(flow.id),
                    settlement_batch_id=int(settlement_batch.id),
                    sale_batch_id=int(sale_batch.id),
                    fund_id=int(fund.id),
                    fund_code=str(fund.code),
                    status_before=status_before,
                    status_after=flow.status,
                    settlement_status_before=settlement_status_before,
                    settlement_status_after=settlement_batch.status,
                    universal_transfer_id=flow.universal_transfer_id,
                    withdrawal_request_id=flow.withdrawal_request_id,
                    settlement_wallet_address=flow.settlement_wallet_address,
                    idempotent=True,
                    diagnostics={"idempotent": True},
                )

            return _set_failed(
                flow=flow,
                settlement_batch=settlement_batch,
                fund=fund,
                status_before=status_before,
                settlement_status_before=settlement_status_before,
                error=(
                    "Existing completed Bybit flow does not match expected "
                    "transferId/requestId/amount/address"
                ),
                now=now,
                diagnostics={
                    "expected_transfer_id": transfer_id,
                    "actual_transfer_id": flow.universal_transfer_id,
                    "expected_request_id": request_id,
                    "actual_request_id": flow.withdrawal_request_id,
                    "expected_address": settlement_wallet_address,
                    "actual_address": flow.settlement_wallet_address,
                },
            )

        flow.coin = coin
        flow.chain = chain
        flow.required_master_usdt = amounts["required_master_usdt"]
        flow.withdrawal_request_amount_usdt = amounts["withdrawal_request_amount_usdt"]
        flow.bybit_withdrawal_fee_usdt = amounts["bybit_withdrawal_fee_usdt"]
        flow.retained_fees_usdt = amounts["total_partial_month_fee_usdt"]
        flow.settlement_wallet_id = int(wallet.id)
        flow.settlement_wallet_address = settlement_wallet_address
        flow.from_sub_uid = mock_flow.fund_sub_uid
        flow.to_master_uid = mock_flow.master_uid
        flow.from_account_type = "UNIFIED"
        flow.to_account_type = "UNIFIED"
        flow.universal_transfer_id = transfer_id
        flow.withdrawal_request_id = request_id

        flow.preflight_passed = True
        flow.preflight_error = None
        flow.preflight_json = _json_dict(
            {
                "mock_id": mock_flow.mock_id,
                "coin": coin,
                "chain": chain,
                "settlement_wallet_id": int(wallet.id),
                "settlement_wallet_address": settlement_wallet_address,
                "internal_db_whitelist_passed": (
                    mock_flow.whitelist.internal_db_whitelist_passed
                ),
                "bybit_address_whitelist_mock_passed": (
                    mock_flow.whitelist.bybit_address_whitelist_mock_passed
                ),
                "fee_type": settings.NEGATIVE_NET_WITHDRAWAL_FEE_TYPE,
            }
        )
        flow.status = BYBIT_FLOW_STATUS_PREFLIGHT_PASSED
        flow.updated_at = now

        settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
        settlement_batch.updated_at = now

        # Stage 24 Operation Guard live-boundary hook.
        # Future real Bybit Universal Transfer must call:
        # require_stage24_bybit_universal_transfer_guard(
        #     db,
        #     fund_id=int(fund.id),
        #     settlement_batch_id=int(settlement_batch.id),
        #     required_master_usdt=amounts["required_master_usdt"],
        #     universal_transfer_id=transfer_id,
        # )

        # 1) Mock Universal Transfer fund subaccount -> master.
        if not _mock_success(mock_flow.universal_transfer.status):
            raise NegativeBybitFlowError("Universal Transfer mock status is not SUCCESS")

        flow.universal_transfer_status = mock_flow.universal_transfer.status
        flow.universal_transfer_amount_usdt = amounts["required_master_usdt"]
        flow.universal_transfer_coin = coin
        flow.universal_transfer_created_at = now
        flow.universal_transfer_mock_json = _json_dict(
            {
                "transferId": transfer_id,
                "from_sub_uid": mock_flow.fund_sub_uid,
                "to_master_uid": mock_flow.master_uid,
                "from_account_type": flow.from_account_type,
                "to_account_type": flow.to_account_type,
                "amount_usdt": amounts["required_master_usdt"],
                "coin": coin,
                "status": mock_flow.universal_transfer.status,
                "raw": mock_flow.universal_transfer.raw,
            }
        )
        flow.status = BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_MOCKED

        # 2) Reconcile Universal Transfer.
        if not _mock_success(mock_flow.universal_transfer.reconcile_status):
            raise NegativeBybitFlowError(
                "Universal Transfer reconcile_status is not SUCCESS"
            )

        universal_transfer_record = _validate_universal_transfer_record(
            raw=mock_flow.universal_transfer.raw,
            expected_transfer_id=transfer_id,
            expected_amount_usdt=amounts["required_master_usdt"],
            expected_coin=coin,
            expected_from_sub_uid=mock_flow.fund_sub_uid,
            expected_to_master_uid=mock_flow.master_uid,
        )

        flow.universal_transfer_confirmed_at = now
        flow.universal_transfer_reconciliation_json = _json_dict(
            {
                "ok": True,
                "reconcile_status": mock_flow.universal_transfer.reconcile_status,
                "record": universal_transfer_record,
            }
        )
        flow.status = BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED

        # Stage 24 Operation Guard live-boundary hook.
        # Future real Bybit master withdrawal must call:
        # require_stage24_bybit_master_withdrawal_guard(
        #     db,
        #     fund_id=int(fund.id),
        #     settlement_batch_id=int(settlement_batch.id),
        #     withdrawal_request_amount_usdt=amounts["withdrawal_request_amount_usdt"],
        #     withdrawal_request_id=request_id,
        # )

        # 3) Mock master withdrawal to settlement wallet.
        if not _mock_success(mock_flow.withdrawal.status):
            raise NegativeBybitFlowError("Withdrawal mock status is not SUCCESS")

        if mock_flow.withdrawal.withdrawal_id is None:
            raise NegativeBybitFlowError("Withdrawal mock withdrawal_id is required")

        if mock_flow.withdrawal.tx_hash is None:
            raise NegativeBybitFlowError("Withdrawal mock tx_hash is required")

        flow.withdrawal_id = mock_flow.withdrawal.withdrawal_id
        flow.withdrawal_status = mock_flow.withdrawal.status
        flow.withdrawal_amount_usdt = amounts["withdrawal_request_amount_usdt"]
        flow.withdrawal_fee_usdt = amounts["bybit_withdrawal_fee_usdt"]
        flow.withdrawal_coin = coin
        flow.withdrawal_chain = chain
        flow.withdrawal_address = settlement_wallet_address
        flow.withdrawal_tx_hash = mock_flow.withdrawal.tx_hash
        flow.withdrawal_created_at = now
        flow.withdrawal_mock_json = _json_dict(
            {
                "requestId": request_id,
                "withdrawal_id": mock_flow.withdrawal.withdrawal_id,
                "amount_usdt": amounts["withdrawal_request_amount_usdt"],
                "fee_usdt": amounts["bybit_withdrawal_fee_usdt"],
                "coin": coin,
                "chain": chain,
                "address": settlement_wallet_address,
                "tx_hash": mock_flow.withdrawal.tx_hash,
                "fee_type": mock_flow.withdrawal.fee_type,
                "status": mock_flow.withdrawal.status,
                "raw": mock_flow.withdrawal.raw,
            }
        )
        flow.status = BYBIT_FLOW_STATUS_WITHDRAWAL_MOCKED

        # 4) Reconcile withdrawal records.
        if not _mock_success(mock_flow.withdrawal.reconcile_status):
            raise NegativeBybitFlowError("Withdrawal record not found or not SUCCESS")

        withdrawal_record = _validate_withdrawal_record(
            raw=mock_flow.withdrawal.raw,
            expected_request_id=request_id,
            expected_withdrawal_id=mock_flow.withdrawal.withdrawal_id,
            expected_amount_usdt=amounts["withdrawal_request_amount_usdt"],
            expected_fee_usdt=amounts["bybit_withdrawal_fee_usdt"],
            expected_coin=coin,
            expected_chain=chain,
            expected_address=settlement_wallet_address,
            expected_tx_hash=mock_flow.withdrawal.tx_hash,
        )

        flow.withdrawal_confirmed_at = now
        flow.withdrawal_record_json = withdrawal_record
        flow.withdrawal_reconciliation_json = _json_dict(
            {
                "ok": True,
                "matched_request_id": True,
                "matched_withdrawal_id": True,
                "matched_coin": True,
                "matched_chain": True,
                "matched_amount": True,
                "matched_fee": True,
                "matched_address": True,
                "matched_tx_hash": True,
                "reconcile_status": mock_flow.withdrawal.reconcile_status,
            }
        )
        flow.status = BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILED

        # 5) Mock settlement wallet receipt. No BSC call here.
        receipt_record = _validate_settlement_wallet_receipt(
            raw=mock_flow.settlement_wallet_receipt.raw,
            expected_address=settlement_wallet_address,
            expected_received_amount_usdt=amounts["withdrawal_request_amount_usdt"],
            expected_tx_hash=mock_flow.withdrawal.tx_hash,
        )

        flow.settlement_wallet_receipt_status = receipt_record["status"]
        flow.settlement_wallet_received_usdt = dec(receipt_record["received_usdt"])
        flow.settlement_wallet_receipt_tx_hash = receipt_record["tx_hash"]
        flow.settlement_wallet_receipt_confirmed_at = now
        flow.settlement_wallet_receipt_json = receipt_record
        flow.status = BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_CONFIRMED

        # Final Stage 23.4 success.
        flow.status = BYBIT_FLOW_STATUS_COMPLETED
        flow.reconciliation_json = _json_dict(
            {
                "ok": True,
                "universal_transfer_reconciled": True,
                "withdrawal_reconciled": True,
                "settlement_wallet_receipt_confirmed": True,
                "no_real_bybit_calls": True,
                "no_real_universal_transfer": True,
                "no_real_withdrawal": True,
                "no_bsc_calls": True,
                "no_seller_payouts": True,
                "no_balance_refresh": True,
                "no_accounting_finalization": True,
                "no_pricing_unlock": True,
            }
        )
        flow.report_json = _json_dict(
            {
                "stage": "23.4",
                "ok": True,
                "mock_id": mock_flow.mock_id,
                "required_master_usdt": amounts["required_master_usdt"],
                "withdrawal_request_amount_usdt": (
                    amounts["withdrawal_request_amount_usdt"]
                ),
                "bybit_withdrawal_fee_usdt": amounts["bybit_withdrawal_fee_usdt"],
                "retained_fees_usdt": amounts["total_partial_month_fee_usdt"],
                "settlement_wallet_address": settlement_wallet_address,
                "status": BYBIT_FLOW_STATUS_COMPLETED,
            }
        )
        flow.updated_at = now

        settlement_batch.status = BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT
        settlement_batch.updated_at = now

        return NegativeBybitFlowResult(
            ok=True,
            flow_id=int(flow.id),
            settlement_batch_id=int(settlement_batch.id),
            sale_batch_id=int(sale_batch.id),
            fund_id=int(fund.id),
            fund_code=str(fund.code),
            status_before=status_before,
            status_after=flow.status,
            settlement_status_before=settlement_status_before,
            settlement_status_after=settlement_batch.status,
            universal_transfer_id=flow.universal_transfer_id,
            withdrawal_request_id=flow.withdrawal_request_id,
            settlement_wallet_address=flow.settlement_wallet_address,
            diagnostics={
                "mock_id": mock_flow.mock_id,
                "required_master_usdt": str(amounts["required_master_usdt"]),
                "withdrawal_request_amount_usdt": str(
                    amounts["withdrawal_request_amount_usdt"]
                ),
            },
        )

    except NegativeBybitFlowError as exc:
        flow.preflight_passed = False if flow.preflight_passed is not True else flow.preflight_passed
        flow.preflight_error = str(exc) if flow.preflight_passed is not True else flow.preflight_error
        flow.preflight_json = flow.preflight_json or _json_dict(
            {
                "mock_id": mock_flow.mock_id,
                "error": str(exc),
                "coin": settings.NEGATIVE_NET_BYBIT_FLOW_COIN,
                "chain": settings.NEGATIVE_NET_BYBIT_FLOW_CHAIN,
            }
        )

        if flow.status == BYBIT_FLOW_STATUS_CREATED:
            flow.status = BYBIT_FLOW_STATUS_PREFLIGHT_FAILED_REQUIRES_REVIEW

        return _set_failed(
            flow=flow,
            settlement_batch=settlement_batch,
            fund=fund,
            status_before=status_before,
            settlement_status_before=settlement_status_before,
            error=str(exc),
            now=now,
        )