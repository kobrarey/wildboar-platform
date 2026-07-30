from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.bybit.asset_flows import (
    BybitAssetFlowError,
    BybitWithdrawalPaginationResult,
    create_master_withdrawal,
    create_universal_transfer,
    list_master_withdrawals_paginated,
    query_account_coin_balance,
    query_coin_info,
    query_universal_transfer,
)
from app.bybit.client import (
    BybitApiError,
    BybitV5Client,
)
from app.config import settings
from app.models import FundNegativeBybitFlow
from app.operation_guard.hooks import (
    require_bybit_master_withdrawal_guard,
    require_bybit_universal_transfer_guard,
)
from app.operation_guard.service import (
    OperationGuardBlockedError,
)
from app.settlement.negative_bybit_flow import (
    _get_active_settlement_wallet,
    _get_fund,
    _is_bybit_pending,
    _is_bybit_success,
    _is_withdrawal_failed_like,
    _is_withdrawal_pending_like,
    _is_withdrawal_success_like,
    _lock_existing_flow,
    _lock_sale_batch_for_settlement,
    _lock_settlement_batch,
    _new_or_existing_flow,
    _same_decimal,
    _set_failed,
    _validate_sale_batch_input,
    _validate_target_fields,
    choose_universal_transfer_account_route,
    deterministic_universal_transfer_id,
    deterministic_withdrawal_request_id,
    universal_transfer_actual_amount,
    withdrawal_actual_amount,
)
from app.settlement.negative_bybit_flow_types import (
    NegativeBybitFlowError,
    NegativeBybitFlowResult,
    _json_dict,
)
from app.settlement.gas_service import get_web3
from app.settlement.statuses import (
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
    BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING,
    BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_PENDING,
    BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_RECONCILING,
    BYBIT_FLOW_STATUS_COMPLETED,
    BYBIT_FLOW_STATUS_CREATED,
    BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW,
    BYBIT_FLOW_STATUS_MASTER_BALANCE_CONFIRMED,
    BYBIT_FLOW_STATUS_PREFLIGHT_PASSED,
    BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_CONFIRMED,
    BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_PENDING,
    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_INTENT_PREPARED,
    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILING,
    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED,
    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_SUBMITTING,
    BYBIT_FLOW_STATUS_WITHDRAWAL_INTENT_PREPARED,
    BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILED,
    BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILING,
    BYBIT_FLOW_STATUS_WITHDRAWAL_SUBMITTING,
)


POLICY_VERSION = "negative_cash_delivery_v1"

UNIVERSAL_TRANSFER_INTENT_SCHEMA = (
    "negative_universal_transfer_intent_v2"
)

UNIVERSAL_TRANSFER_RECONCILIATION_SCHEMA = (
    "negative_universal_transfer_reconciliation_v2"
)

MASTER_TRANSFERABLE_BALANCE_SCHEMA = (
    "negative_master_transferable_balance_barrier_v1"
)

WITHDRAWAL_INTENT_SCHEMA = (
    "negative_withdrawal_intent_v2"
)

WITHDRAWAL_RECONCILIATION_SCHEMA = (
    "negative_withdrawal_reconciliation_v1"
)

WITHDRAWAL_RECORD_LOOKUP_LIMIT = 50

SETTLEMENT_WALLET_RECEIPT_SCHEMA = (
    "negative_settlement_wallet_receipt_v1"
)

CASH_DELIVERY_COMPLETION_SCHEMA = (
    "negative_cash_delivery_completion_v1"
)

CASH_DELIVERY_REPORT_SCHEMA = (
    "negative_cash_delivery_report_v1"
)

ERC20_TRANSFER_EVENT_SIGNATURE = (
    "Transfer(address,address,uint256)"
)

ERC20_BALANCE_OF_ABI = [
    {
        "constant": True,
        "inputs": [
            {
                "name": "account",
                "type": "address",
            },
        ],
        "name": "balanceOf",
        "outputs": [
            {
                "name": "",
                "type": "uint256",
            },
        ],
        "stateMutability": "view",
        "type": "function",
    },
]


def _now(
    value: datetime | None,
) -> datetime:
    result = value or datetime.now(timezone.utc)

    if (
        result.tzinfo is None
        or result.utcoffset() is None
    ):
        raise NegativeBybitFlowError(
            "now must be timezone-aware"
        )

    return result.astimezone(timezone.utc)


def _required_text(
    value: Any,
    *,
    field_name: str,
) -> str:
    clean = str(value or "").strip()

    if not clean:
        raise NegativeBybitFlowError(
            f"{field_name} is required"
        )

    return clean


def _reject_float(
    value: Any,
    *,
    path: str = "root",
) -> None:
    if isinstance(value, float):
        raise NegativeBybitFlowError(
            "float is forbidden in durable intent: "
            f"{path}"
        )

    if isinstance(value, dict):
        for key, item in value.items():
            _reject_float(
                item,
                path=f"{path}.{key}",
            )

        return

    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_float(
                item,
                path=f"{path}[{index}]",
            )


def _payload_fingerprint(
    payload: dict[str, Any],
) -> str:
    _reject_float(payload)

    canonical = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )

    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def _decimal_text(
    value: Decimal,
) -> str:
    if not isinstance(value, Decimal):
        raise NegativeBybitFlowError(
            "money values must use Decimal"
        )

    text = format(value, "f")

    if "e" in text.lower():
        raise NegativeBybitFlowError(
            "scientific notation is forbidden"
        )

    if "." in text:
        text = text.rstrip("0").rstrip(".")

    return text or "0"


def _step_result(
    *,
    ok: bool,
    transition: str,
    settlement_batch,
    flow,
    status_before: str | None,
    settlement_status_before: str | None,
    idempotent: bool = False,
    error: str | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> NegativeBybitFlowResult:
    return NegativeBybitFlowResult(
        ok=ok,
        flow_id=(
            int(flow.id)
            if (
                flow is not None
                and flow.id is not None
            )
            else None
        ),
        settlement_batch_id=int(
            settlement_batch.id
        ),
        sale_batch_id=(
            int(flow.sale_batch_id)
            if (
                flow is not None
                and flow.sale_batch_id is not None
            )
            else None
        ),
        fund_id=(
            int(flow.fund_id)
            if (
                flow is not None
                and flow.fund_id is not None
            )
            else int(settlement_batch.fund_id)
        ),
        fund_code=None,
        status_before=status_before,
        status_after=(
            str(flow.status)
            if flow is not None
            else None
        ),
        settlement_status_before=(
            settlement_status_before
        ),
        settlement_status_after=str(
            settlement_batch.status
        ),
        universal_transfer_id=(
            str(flow.universal_transfer_id)
            if (
                flow is not None
                and flow.universal_transfer_id
            )
            else None
        ),
        withdrawal_request_id=(
            str(flow.withdrawal_request_id)
            if (
                flow is not None
                and flow.withdrawal_request_id
            )
            else None
        ),
        settlement_wallet_address=(
            str(flow.settlement_wallet_address)
            if (
                flow is not None
                and flow.settlement_wallet_address
            )
            else None
        ),
        idempotent=idempotent,
        error=error,
        diagnostics={
            "transition": transition,
            "did_bybit_post": False,
            "bybit_post_count": 0,
            **(diagnostics or {}),
        },
    )


def _validate_existing_flow(
    *,
    flow: FundNegativeBybitFlow,
    settlement_batch,
    sale_batch,
    amounts: dict[str, Decimal],
) -> None:
    if (
        int(flow.settlement_batch_id)
        != int(settlement_batch.id)
    ):
        raise NegativeBybitFlowError(
            "Bybit flow settlement_batch_id mismatch"
        )

    if (
        int(flow.sale_batch_id)
        != int(sale_batch.id)
    ):
        raise NegativeBybitFlowError(
            "Bybit flow sale_batch_id mismatch"
        )

    if (
        int(flow.fund_id)
        != int(settlement_batch.fund_id)
    ):
        raise NegativeBybitFlowError(
            "Bybit flow fund_id mismatch"
        )

    checks = (
        (
            "required_master_usdt",
            flow.required_master_usdt,
            amounts["required_master_usdt"],
        ),
        (
            "withdrawal_request_amount_usdt",
            flow.withdrawal_request_amount_usdt,
            amounts[
                "withdrawal_request_amount_usdt"
            ],
        ),
        (
            "bybit_withdrawal_fee_usdt",
            flow.bybit_withdrawal_fee_usdt,
            amounts["bybit_withdrawal_fee_usdt"],
        ),
        (
            "retained_fees_usdt",
            flow.retained_fees_usdt,
            amounts[
                "total_partial_month_fee_usdt"
            ],
        ),
    )

    for field_name, actual, expected in checks:
        if not _same_decimal(
            actual,
            expected,
        ):
            raise NegativeBybitFlowError(
                "Bybit flow immutable amount "
                f"mismatch: {field_name}"
            )


def _has_transfer_evidence(
    flow: FundNegativeBybitFlow,
) -> bool:
    return any(
        (
            bool(flow.universal_transfer_id),
            bool(flow.universal_transfer_status),
            (
                flow.universal_transfer_created_at
                is not None
            ),
            (
                flow.universal_transfer_confirmed_at
                is not None
            ),
            (
                flow.universal_transfer_submitted_at
                is not None
            ),
        )
    )


def _build_intent(
    *,
    settlement_batch_id: int,
    fund_id: int,
    transfer_id: str,
    coin: str,
    amount: str,
    from_member_id: str,
    to_member_id: str,
    from_account_type: str,
    to_account_type: str,
    prepared_at: datetime,
) -> dict[str, Any]:
    payload = {
        "transferId": transfer_id,
        "coin": coin,
        "amount": amount,
        "fromMemberId": from_member_id,
        "toMemberId": to_member_id,
        "fromAccountType": from_account_type,
        "toAccountType": to_account_type,
    }

    intent = {
        "schema": (
            UNIVERSAL_TRANSFER_INTENT_SCHEMA
        ),
        "state": "prepared",
        "policy_version": POLICY_VERSION,
        "settlement_batch_id": str(
            int(settlement_batch_id)
        ),
        "fund_id": str(int(fund_id)),
        "transfer_id": transfer_id,
        "coin": coin,
        "amount": amount,
        "from_member_id": from_member_id,
        "to_member_id": to_member_id,
        "from_account_type": from_account_type,
        "to_account_type": to_account_type,
        "payload": payload,
        "payload_fingerprint": (
            _payload_fingerprint(payload)
        ),
        "prepared_at": prepared_at.isoformat(),
        "submit_claim": None,
        "acknowledgement": None,
        "reconciliation": None,
    }

    _reject_float(intent)

    return intent


def _validate_prepared_intent(
    *,
    flow: FundNegativeBybitFlow,
    intent: dict[str, Any],
    allowed_states: set[str] | frozenset[str] | None = None,
) -> None:
    if (
        intent.get("schema")
        != UNIVERSAL_TRANSFER_INTENT_SCHEMA
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer intent schema "
            "mismatch"
        )

    if (
        intent.get("policy_version")
        != POLICY_VERSION
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer intent policy "
            "mismatch"
        )

    allowed = frozenset(
        allowed_states
        if allowed_states is not None
        else {"prepared"}
    )

    intent_state = str(
        intent.get("state") or ""
    ).strip()

    if intent_state not in allowed:
        raise NegativeBybitFlowError(
            "Universal Transfer intent state "
            f"mismatch: state={intent_state or 'empty'}, "
            f"allowed={sorted(allowed)}"
        )

    payload = intent.get("payload")

    if not isinstance(payload, dict):
        raise NegativeBybitFlowError(
            "Universal Transfer intent payload "
            "is missing"
        )

    if (
        intent.get("payload_fingerprint")
        != _payload_fingerprint(payload)
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer payload "
            "fingerprint mismatch"
        )

    expected_payload = {
        "transferId": _required_text(
            flow.universal_transfer_id,
            field_name=(
                "flow.universal_transfer_id"
            ),
        ),
        "coin": _required_text(
            (
                flow.universal_transfer_coin
                or flow.coin
            ),
            field_name=(
                "flow.universal_transfer_coin"
            ),
        ).upper(),
        "amount": _decimal_text(
            Decimal(
                flow.universal_transfer_amount_usdt
            )
        ),
        "fromMemberId": _required_text(
            flow.from_sub_uid,
            field_name="flow.from_sub_uid",
        ),
        "toMemberId": _required_text(
            flow.to_master_uid,
            field_name="flow.to_master_uid",
        ),
        "fromAccountType": _required_text(
            flow.from_account_type,
            field_name=(
                "flow.from_account_type"
            ),
        ).upper(),
        "toAccountType": _required_text(
            flow.to_account_type,
            field_name="flow.to_account_type",
        ).upper(),
    }

    if payload != expected_payload:
        raise NegativeBybitFlowError(
            "Universal Transfer immutable "
            "payload mismatch"
        )


def _bounded_external_error(
    exc: BaseException,
) -> str:
    text = (
        f"{type(exc).__name__}: {str(exc)}"
    )

    return text[:500]


def _require_single_post_client(
    bybit_client: BybitV5Client,
) -> None:
    retries = getattr(
        bybit_client,
        "retries",
        0,
    )

    try:
        retry_count = int(retries or 0)
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise NegativeBybitFlowError(
            "Bybit client retries value is invalid"
        ) from exc

    if retry_count != 0:
        raise NegativeBybitFlowError(
            "Financial Bybit POST client must use "
            "retries=0"
        )


def _locked_flow_for_submit(
    db: Session,
    *,
    settlement_batch_id: int,
):
    settlement_batch = _lock_settlement_batch(
        db,
        settlement_batch_id=int(
            settlement_batch_id
        ),
    )

    flow = _lock_existing_flow(
        db,
        settlement_batch_id=int(
            settlement_batch_id
        ),
    )

    if flow is None:
        raise NegativeBybitFlowError(
            "Negative Bybit flow disappeared "
            "during Universal Transfer submit"
        )

    return settlement_batch, flow


def _intent_snapshot(
    *,
    flow: FundNegativeBybitFlow,
    intent: dict[str, Any],
    allowed_states: (
        set[str] | frozenset[str] | None
    ) = None,
) -> dict[str, Any]:
    _validate_prepared_intent(
        flow=flow,
        intent=intent,
        allowed_states=(
            allowed_states
            if allowed_states is not None
            else {"prepared"}
        ),
    )

    payload = intent.get("payload")

    if not isinstance(payload, dict):
        raise NegativeBybitFlowError(
            "Universal Transfer payload is missing"
        )

    return {
        "settlement_batch_id": int(
            flow.settlement_batch_id
        ),
        "fund_id": int(flow.fund_id),
        "transfer_id": _required_text(
            intent.get("transfer_id"),
            field_name="intent.transfer_id",
        ),
        "coin": _required_text(
            intent.get("coin"),
            field_name="intent.coin",
        ).upper(),
        "amount_text": _required_text(
            intent.get("amount"),
            field_name="intent.amount",
        ),
        "amount_usdt": Decimal(
            _required_text(
                intent.get("amount"),
                field_name="intent.amount",
            )
        ),
        "from_member_id": _required_text(
            intent.get("from_member_id"),
            field_name=(
                "intent.from_member_id"
            ),
        ),
        "to_member_id": _required_text(
            intent.get("to_member_id"),
            field_name="intent.to_member_id",
        ),
        "from_account_type": _required_text(
            intent.get("from_account_type"),
            field_name=(
                "intent.from_account_type"
            ),
        ).upper(),
        "to_account_type": _required_text(
            intent.get("to_account_type"),
            field_name=(
                "intent.to_account_type"
            ),
        ).upper(),
        "payload": deepcopy(payload),
        "payload_fingerprint": _required_text(
            intent.get("payload_fingerprint"),
            field_name=(
                "intent.payload_fingerprint"
            ),
        ),
    }


def _validate_snapshot_unchanged(
    *,
    flow: FundNegativeBybitFlow,
    intent: dict[str, Any],
    snapshot: dict[str, Any],
    allowed_states: set[str],
) -> None:
    _validate_prepared_intent(
        flow=flow,
        intent=intent,
        allowed_states=allowed_states,
    )

    if (
        intent.get("payload_fingerprint")
        != snapshot["payload_fingerprint"]
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer intent fingerprint "
            "changed during submit"
        )

    if (
        intent.get("payload")
        != snapshot["payload"]
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer payload changed "
            "during submit"
        )

    if (
        str(intent.get("transfer_id") or "")
        != snapshot["transfer_id"]
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer transfer_id changed "
            "during submit"
        )


def _submit_claim_matches(
    *,
    intent: dict[str, Any],
    claim_token: str,
) -> bool:
    claim = intent.get("submit_claim")

    return (
        isinstance(claim, dict)
        and str(
            claim.get("claim_token") or ""
        ) == claim_token
    )


def _mark_submit_unknown(
    db: Session,
    *,
    settlement_batch_id: int,
    snapshot: dict[str, Any],
    claim_token: str,
    error: BaseException,
    now: datetime,
) -> NegativeBybitFlowResult:
    settlement_batch, flow = (
        _locked_flow_for_submit(
            db,
            settlement_batch_id=(
                settlement_batch_id
            ),
        )
    )

    intent = deepcopy(
        flow.universal_transfer_intent_json
    )

    if not isinstance(intent, dict):
        raise NegativeBybitFlowError(
            "Universal Transfer intent missing "
            "after POST attempt"
        )

    _validate_snapshot_unchanged(
        flow=flow,
        intent=intent,
        snapshot=snapshot,
        allowed_states={"submitting"},
    )

    if not _submit_claim_matches(
        intent=intent,
        claim_token=claim_token,
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer submit claim "
            "ownership mismatch"
        )

    intent["state"] = "reconciling"
    intent["acknowledgement"] = {
        "outcome": "unknown",
        "claim_token": claim_token,
        "acknowledged_at": now.isoformat(),
        "error": _bounded_external_error(
            error
        ),
        "no_automatic_resend": True,
    }

    flow.universal_transfer_intent_json = (
        intent
    )
    flow.universal_transfer_status = (
        "UNKNOWN"
    )
    flow.status = (
        BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILING
    )
    flow.error = None
    flow.updated_at = now

    settlement_batch.status = (
        BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
    )
    settlement_batch.error = None
    settlement_batch.updated_at = now

    db.add(flow)
    db.add(settlement_batch)
    db.flush()

    result = _step_result(
        ok=False,
        transition=(
            "submit_universal_transfer_unknown"
        ),
        settlement_batch=settlement_batch,
        flow=flow,
        status_before=(
            BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_SUBMITTING
        ),
        settlement_status_before=(
            str(settlement_batch.status)
        ),
        diagnostics={
            "pending": (
                "universal_transfer_reconciliation"
            ),
            "did_bybit_post": True,
            "bybit_post_count": 1,
            "no_automatic_resend": True,
            "payload_fingerprint": snapshot[
                "payload_fingerprint"
            ],
        },
    )

    db.commit()

    return result


def _mark_guard_blocked(
    db: Session,
    *,
    settlement_batch_id: int,
    snapshot: dict[str, Any],
    claim_token: str,
    error: BaseException,
    now: datetime,
) -> NegativeBybitFlowResult:
    settlement_batch, flow = (
        _locked_flow_for_submit(
            db,
            settlement_batch_id=(
                settlement_batch_id
            ),
        )
    )

    intent = deepcopy(
        flow.universal_transfer_intent_json
    )

    if not isinstance(intent, dict):
        raise NegativeBybitFlowError(
            "Universal Transfer intent missing "
            "after Operation Guard"
        )

    _validate_snapshot_unchanged(
        flow=flow,
        intent=intent,
        snapshot=snapshot,
        allowed_states={"submitting"},
    )

    if not _submit_claim_matches(
        intent=intent,
        claim_token=claim_token,
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer submit claim "
            "ownership mismatch"
        )

    intent["state"] = (
        "failed_requires_review"
    )
    intent["acknowledgement"] = {
        "outcome": "guard_blocked",
        "claim_token": claim_token,
        "acknowledged_at": now.isoformat(),
        "error": _bounded_external_error(
            error
        ),
        "bybit_post_performed": False,
    }

    flow.universal_transfer_intent_json = (
        intent
    )

    result = _set_failed(
        flow=flow,
        settlement_batch=settlement_batch,
        fund=None,
        status_before=(
            BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_SUBMITTING
        ),
        settlement_status_before=(
            str(settlement_batch.status)
        ),
        error=(
            "Operation Guard blocked Bybit "
            f"Universal Transfer: {error}"
        ),
        now=now,
        diagnostics={
            "transition": (
                "submit_universal_transfer_guard_blocked"
            ),
            "did_bybit_post": False,
            "bybit_post_count": 0,
            "payload_fingerprint": snapshot[
                "payload_fingerprint"
            ],
            "reserve_release_allowed": False,
            "pricing_unlock_allowed": False,
        },
    )

    db.add(flow)
    db.add(settlement_batch)
    db.flush()
    db.commit()

    return result


def _mark_submit_ack_mismatch(
    db: Session,
    *,
    settlement_batch,
    flow: FundNegativeBybitFlow,
    current_intent: dict[str, Any],
    snapshot: dict[str, Any],
    claim_token: str,
    guard_event_id: int | None,
    created_transfer,
    error: BaseException,
    now: datetime,
) -> NegativeBybitFlowResult:
    current_intent["state"] = (
        "failed_requires_review"
    )
    current_intent["acknowledgement"] = {
        "outcome": "mismatch",
        "claim_token": claim_token,
        "guard_event_id": guard_event_id,
        "acknowledged_at": now.isoformat(),
        "expected": {
            "transfer_id": snapshot[
                "transfer_id"
            ],
            "coin": snapshot["coin"],
            "amount_usdt": _decimal_text(
                snapshot["amount_usdt"]
            ),
            "from_member_id": snapshot[
                "from_member_id"
            ],
            "to_member_id": snapshot[
                "to_member_id"
            ],
            "from_account_type": snapshot[
                "from_account_type"
            ],
            "to_account_type": snapshot[
                "to_account_type"
            ],
        },
        "observed": (
            _transfer_record_snapshot(
                created_transfer
            )
        ),
        "response": _json_dict(
            created_transfer.raw
        ),
        "error": _bounded_external_error(
            error
        ),
        "bybit_post_performed": True,
        "no_automatic_resend": True,
    }

    flow.universal_transfer_intent_json = (
        current_intent
    )
    flow.universal_transfer_status = (
        created_transfer.status
    )
    flow.universal_transfer_created_at = now

    result = _set_failed(
        flow=flow,
        settlement_batch=settlement_batch,
        fund=None,
        status_before=(
            BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_SUBMITTING
        ),
        settlement_status_before=str(
            settlement_batch.status
        ),
        error=(
            "Universal Transfer acknowledgement "
            f"mismatch after POST: {error}"
        ),
        now=now,
        diagnostics={
            "transition": (
                "submit_universal_transfer_"
                "ack_mismatch"
            ),
            "did_bybit_post": True,
            "bybit_post_count": 1,
            "no_automatic_resend": True,
            "payload_fingerprint": snapshot[
                "payload_fingerprint"
            ],
            "guard_event_id": guard_event_id,
            "reserve_release_allowed": False,
            "pricing_unlock_allowed": False,
            "acknowledgement_mismatch": True,
        },
    )

    db.add(flow)
    db.add(settlement_batch)
    db.flush()
    db.commit()

    return result


def _submit_universal_transfer_once(
    db: Session,
    *,
    settlement_batch,
    flow: FundNegativeBybitFlow,
    fund,
    bybit_client: BybitV5Client,
    resolved_now: datetime,
    status_before: str | None,
    settlement_status_before: str | None,
) -> NegativeBybitFlowResult:
    intent = deepcopy(
        flow.universal_transfer_intent_json
    )

    if not isinstance(intent, dict):
        raise NegativeBybitFlowError(
            "Universal Transfer intent is missing"
        )

    _require_single_post_client(
        bybit_client
    )

    snapshot = _intent_snapshot(
        flow=flow,
        intent=intent,
    )

    settlement_batch_id = int(
        settlement_batch.id
    )

    # Release all FOR UPDATE locks before
    # read-only Bybit reconciliation.
    db.commit()

    existing_record = query_universal_transfer(
        bybit_client,
        transfer_id=snapshot["transfer_id"],
    )

    if existing_record is not None:
        settlement_batch, flow = (
            _locked_flow_for_submit(
                db,
                settlement_batch_id=(
                    settlement_batch_id
                ),
            )
        )

        current_intent = deepcopy(
            flow.universal_transfer_intent_json
        )

        if not isinstance(
            current_intent,
            dict,
        ):
            raise NegativeBybitFlowError(
                "Universal Transfer intent "
                "disappeared before submit"
            )

        _validate_snapshot_unchanged(
            flow=flow,
            intent=current_intent,
            snapshot=snapshot,
            allowed_states={"prepared"},
        )

        current_intent["state"] = (
            "reconciling"
        )
        current_intent["reconciliation"] = {
            "phase": "pre_submit_query",
            "record_found": True,
            "observed_status": (
                existing_record.status
            ),
            "observed_at": (
                resolved_now.isoformat()
            ),
            "no_post_performed": True,
        }

        flow.universal_transfer_intent_json = (
            current_intent
        )
        flow.universal_transfer_status = (
            existing_record.status
        )
        flow.status = (
            BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILING
        )
        flow.updated_at = resolved_now

        settlement_batch.status = (
            BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
        )
        settlement_batch.error = None
        settlement_batch.updated_at = (
            resolved_now
        )

        db.add(flow)
        db.add(settlement_batch)
        db.flush()

        result = _step_result(
            ok=False,
            transition=(
                "submit_universal_transfer_"
                "preexisting_record"
            ),
            settlement_batch=settlement_batch,
            flow=flow,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            diagnostics={
                "pending": (
                    "universal_transfer_reconciliation"
                ),
                "did_bybit_post": False,
                "bybit_post_count": 0,
                "record_found": True,
                "payload_fingerprint": snapshot[
                    "payload_fingerprint"
                ],
            },
        )

        db.commit()

        return result

    # Re-lock and claim the only permitted POST.
    settlement_batch, flow = (
        _locked_flow_for_submit(
            db,
            settlement_batch_id=(
                settlement_batch_id
            ),
        )
    )

    current_intent = deepcopy(
        flow.universal_transfer_intent_json
    )

    if not isinstance(current_intent, dict):
        raise NegativeBybitFlowError(
            "Universal Transfer intent "
            "disappeared before claim"
        )

    _validate_snapshot_unchanged(
        flow=flow,
        intent=current_intent,
        snapshot=snapshot,
        allowed_states={"prepared"},
    )

    if (
        current_intent.get("submit_claim")
        is not None
    ):
        db.commit()

        return _step_result(
            ok=False,
            transition=(
                "submit_universal_transfer_"
                "claim_already_exists"
            ),
            settlement_batch=settlement_batch,
            flow=flow,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            idempotent=True,
            diagnostics={
                "pending": (
                    "universal_transfer_reconciliation"
                ),
                "did_bybit_post": False,
                "bybit_post_count": 0,
                "no_automatic_resend": True,
            },
        )

    claim_token = str(uuid4())

    current_intent["state"] = "submitting"
    current_intent["submit_claim"] = {
        "claim_token": claim_token,
        "claimed_at": resolved_now.isoformat(),
        "submit_attempt_number": 1,
    }

    flow.universal_transfer_intent_json = (
        current_intent
    )
    flow.universal_transfer_submitted_at = (
        resolved_now
    )
    flow.status = (
        BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_SUBMITTING
    )
    flow.error = None
    flow.updated_at = resolved_now

    settlement_batch.status = (
        BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
    )
    settlement_batch.error = None
    settlement_batch.updated_at = (
        resolved_now
    )

    db.add(flow)
    db.add(settlement_batch)
    db.flush()

    # Durable claim boundary.
    # After this commit no automatic resend is allowed.
    db.commit()

    try:
        guard_decision = (
            require_bybit_universal_transfer_guard(
                db,
                fund_id=int(snapshot["fund_id"]),
                settlement_batch_id=(
                    settlement_batch_id
                ),
                amount_usdt=snapshot[
                    "amount_usdt"
                ],
                request_id=snapshot[
                    "transfer_id"
                ],
                metadata={
                    "source": (
                        "negative_bybit_flow_"
                        "live_service"
                    ),
                    "intent_schema": (
                        UNIVERSAL_TRANSFER_INTENT_SCHEMA
                    ),
                    "intent_state": "submitting",
                    "claim_token": claim_token,
                    "payload_fingerprint": snapshot[
                        "payload_fingerprint"
                    ],
                    "from_member_id": snapshot[
                        "from_member_id"
                    ],
                    "to_member_id": snapshot[
                        "to_member_id"
                    ],
                    "from_account_type": snapshot[
                        "from_account_type"
                    ],
                    "to_account_type": snapshot[
                        "to_account_type"
                    ],
                },
            )
        )

        # Persist the Guard audit and release its
        # transaction before the HTTP POST.
        db.commit()

    except OperationGuardBlockedError as exc:
        # Preserve a possible blocked Guard audit event.
        db.commit()

        return _mark_guard_blocked(
            db,
            settlement_batch_id=(
                settlement_batch_id
            ),
            snapshot=snapshot,
            claim_token=claim_token,
            error=exc,
            now=resolved_now,
        )

    try:
        created_transfer = (
            create_universal_transfer(
                bybit_client,
                transfer_id=snapshot[
                    "transfer_id"
                ],
                coin=snapshot["coin"],
                amount_usdt=snapshot[
                    "amount_usdt"
                ],
                amount_str=snapshot[
                    "amount_text"
                ],
                amount_precision=int(
                    settings
                    .NEGATIVE_NET_UNIVERSAL_TRANSFER_AMOUNT_PRECISION
                ),
                from_member_id=snapshot[
                    "from_member_id"
                ],
                to_member_id=snapshot[
                    "to_member_id"
                ],
                from_account_type=snapshot[
                    "from_account_type"
                ],
                to_account_type=snapshot[
                    "to_account_type"
                ],
            )
        )

    except (
        BybitApiError,
        BybitAssetFlowError,
    ) as exc:
        return _mark_submit_unknown(
            db,
            settlement_batch_id=(
                settlement_batch_id
            ),
            snapshot=snapshot,
            claim_token=claim_token,
            error=exc,
            now=resolved_now,
        )

    settlement_batch, flow = (
        _locked_flow_for_submit(
            db,
            settlement_batch_id=(
                settlement_batch_id
            ),
        )
    )

    current_intent = deepcopy(
        flow.universal_transfer_intent_json
    )

    if not isinstance(current_intent, dict):
        raise NegativeBybitFlowError(
            "Universal Transfer intent missing "
            "after POST acknowledgement"
        )

    _validate_snapshot_unchanged(
        flow=flow,
        intent=current_intent,
        snapshot=snapshot,
        allowed_states={"submitting"},
    )

    if not _submit_claim_matches(
        intent=current_intent,
        claim_token=claim_token,
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer submit claim "
            "ownership mismatch"
        )

    try:
        _validate_exact_transfer_record(
            record=created_transfer,
            snapshot=snapshot,
        )
    except NegativeBybitFlowError as exc:
        return _mark_submit_ack_mismatch(
            db,
            settlement_batch=settlement_batch,
            flow=flow,
            current_intent=current_intent,
            snapshot=snapshot,
            claim_token=claim_token,
            guard_event_id=(
                guard_decision.event_id
            ),
            created_transfer=(
                created_transfer
            ),
            error=exc,
            now=resolved_now,
        )

    current_intent["state"] = "reconciling"
    current_intent["acknowledgement"] = {
        "outcome": "accepted",
        "claim_token": claim_token,
        "guard_event_id": (
            guard_decision.event_id
        ),
        "acknowledged_at": (
            resolved_now.isoformat()
        ),
        "transfer_id": (
            created_transfer.transfer_id
        ),
        "status": created_transfer.status,
        "response": _json_dict(
            created_transfer.raw
        ),
        "no_automatic_resend": True,
    }

    flow.universal_transfer_intent_json = (
        current_intent
    )
    flow.universal_transfer_status = (
        created_transfer.status
    )
    flow.universal_transfer_created_at = (
        resolved_now
    )
    flow.status = (
        BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILING
    )
    flow.error = None
    flow.updated_at = resolved_now

    settlement_batch.status = (
        BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
    )
    settlement_batch.error = None
    settlement_batch.updated_at = (
        resolved_now
    )

    db.add(flow)
    db.add(settlement_batch)
    db.flush()

    result = _step_result(
        ok=True,
        transition="submit_universal_transfer",
        settlement_batch=settlement_batch,
        flow=flow,
        status_before=status_before,
        settlement_status_before=(
            settlement_status_before
        ),
        diagnostics={
            "did_bybit_post": True,
            "bybit_post_count": 1,
            "guard_event_id": (
                guard_decision.event_id
            ),
            "claim_token": claim_token,
            "payload_fingerprint": snapshot[
                "payload_fingerprint"
            ],
            "next_transition": (
                "reconcile_universal_transfer"
            ),
        },
    )

    db.commit()

    return result


def _transfer_record_snapshot(
    record,
) -> dict[str, Any]:
    return {
        "transfer_id": str(
            record.transfer_id or ""
        ).strip(),
        "coin": str(
            record.coin or ""
        ).strip().upper(),
        "amount_usdt": _decimal_text(
            Decimal(record.amount_usdt)
        ),
        "from_member_id": str(
            record.from_member_id or ""
        ).strip(),
        "to_member_id": str(
            record.to_member_id or ""
        ).strip(),
        "from_account_type": str(
            record.from_account_type or ""
        ).strip().upper(),
        "to_account_type": str(
            record.to_account_type or ""
        ).strip().upper(),
        "status": (
            str(record.status).strip()
            if record.status is not None
            else None
        ),
        "raw": _json_dict(record.raw),
    }


def _validate_exact_transfer_record(
    *,
    record,
    snapshot: dict[str, Any],
) -> None:
    if (
        str(record.transfer_id or "").strip()
        != snapshot["transfer_id"]
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer reconciliation "
            "transfer_id mismatch"
        )

    if (
        str(record.coin or "").strip().upper()
        != snapshot["coin"]
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer reconciliation "
            "coin mismatch"
        )

    if not _same_decimal(
        record.amount_usdt,
        snapshot["amount_usdt"],
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer reconciliation "
            "amount mismatch"
        )

    if (
        str(
            record.from_member_id or ""
        ).strip()
        != snapshot["from_member_id"]
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer reconciliation "
            "from_member_id mismatch"
        )

    if (
        str(
            record.to_member_id or ""
        ).strip()
        != snapshot["to_member_id"]
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer reconciliation "
            "to_member_id mismatch"
        )

    if (
        str(
            record.from_account_type or ""
        ).strip().upper()
        != snapshot["from_account_type"]
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer reconciliation "
            "from_account_type mismatch"
        )

    if (
        str(
            record.to_account_type or ""
        ).strip().upper()
        != snapshot["to_account_type"]
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer reconciliation "
            "to_account_type mismatch"
        )


def _fail_universal_transfer_reconciliation(
    db: Session,
    *,
    settlement_batch,
    flow: FundNegativeBybitFlow,
    intent: dict[str, Any],
    reconciliation: dict[str, Any],
    error: str,
    now: datetime,
    status_before: str | None,
    settlement_status_before: str | None,
    transition: str,
) -> NegativeBybitFlowResult:
    intent["state"] = (
        "failed_requires_review"
    )
    intent["reconciliation"] = (
        reconciliation
    )

    flow.universal_transfer_intent_json = (
        intent
    )
    flow.universal_transfer_reconciliation_json = (
        _json_dict(reconciliation)
    )

    result = _set_failed(
        flow=flow,
        settlement_batch=settlement_batch,
        fund=None,
        status_before=status_before,
        settlement_status_before=(
            settlement_status_before
        ),
        error=error,
        now=now,
        diagnostics={
            "transition": transition,
            "did_bybit_post": False,
            "bybit_post_count": 0,
            "no_automatic_resend": True,
            "reserve_release_allowed": False,
            "pricing_unlock_allowed": False,
            "reconciliation": reconciliation,
        },
    )

    db.add(flow)
    db.add(settlement_batch)
    db.flush()
    db.commit()

    return result


def _reconcile_universal_transfer_once(
    db: Session,
    *,
    settlement_batch,
    flow: FundNegativeBybitFlow,
    bybit_client: BybitV5Client,
    resolved_now: datetime,
    status_before: str | None,
    settlement_status_before: str | None,
) -> NegativeBybitFlowResult:
    intent = deepcopy(
        flow.universal_transfer_intent_json
    )

    if not isinstance(intent, dict):
        raise NegativeBybitFlowError(
            "Universal Transfer intent is missing "
            "during reconciliation"
        )

    snapshot = _intent_snapshot(
        flow=flow,
        intent=intent,
        allowed_states={
            "submitting",
            "reconciling",
        },
    )

    settlement_batch_id = int(
        settlement_batch.id
    )

    # Release settlement, sale and flow locks
    # before the read-only Bybit GET.
    db.commit()

    record = None
    query_error: BaseException | None = None

    try:
        record = query_universal_transfer(
            bybit_client,
            transfer_id=snapshot[
                "transfer_id"
            ],
        )
    except (
        BybitApiError,
        BybitAssetFlowError,
    ) as exc:
        query_error = exc

    settlement_batch, flow = (
        _locked_flow_for_submit(
            db,
            settlement_batch_id=(
                settlement_batch_id
            ),
        )
    )

    current_intent = deepcopy(
        flow.universal_transfer_intent_json
    )

    if not isinstance(
        current_intent,
        dict,
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer intent "
            "disappeared during reconciliation"
        )

    _validate_snapshot_unchanged(
        flow=flow,
        intent=current_intent,
        snapshot=snapshot,
        allowed_states={
            "submitting",
            "reconciling",
        },
    )

    if query_error is not None:
        reconciliation = {
            "schema": (
                UNIVERSAL_TRANSFER_RECONCILIATION_SCHEMA
            ),
            "phase": "exact_transfer_id_query",
            "transfer_id": snapshot[
                "transfer_id"
            ],
            "record_found": False,
            "query_succeeded": False,
            "query_error": (
                _bounded_external_error(
                    query_error
                )
            ),
            "observed_at": (
                resolved_now.isoformat()
            ),
            "no_automatic_resend": True,
        }

        current_intent["state"] = (
            "reconciling"
        )
        current_intent["reconciliation"] = (
            reconciliation
        )

        flow.universal_transfer_intent_json = (
            current_intent
        )
        flow.universal_transfer_reconciliation_json = (
            _json_dict(reconciliation)
        )
        flow.status = (
            BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILING
        )
        flow.error = None
        flow.updated_at = resolved_now

        settlement_batch.status = (
            BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
        )
        settlement_batch.error = None
        settlement_batch.updated_at = (
            resolved_now
        )

        db.add(flow)
        db.add(settlement_batch)
        db.flush()

        result = _step_result(
            ok=False,
            transition=(
                "reconcile_universal_transfer_"
                "query_pending"
            ),
            settlement_batch=(
                settlement_batch
            ),
            flow=flow,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            diagnostics={
                "pending": (
                    "universal_transfer_"
                    "reconciliation"
                ),
                "did_bybit_post": False,
                "bybit_post_count": 0,
                "record_found": False,
                "query_succeeded": False,
                "no_automatic_resend": True,
            },
        )

        db.commit()

        return result

    if record is None:
        reconciliation = {
            "schema": (
                UNIVERSAL_TRANSFER_RECONCILIATION_SCHEMA
            ),
            "phase": "exact_transfer_id_query",
            "transfer_id": snapshot[
                "transfer_id"
            ],
            "record_found": False,
            "query_succeeded": True,
            "observed_at": (
                resolved_now.isoformat()
            ),
            "no_automatic_resend": True,
        }

        current_intent["state"] = (
            "reconciling"
        )
        current_intent["reconciliation"] = (
            reconciliation
        )

        flow.universal_transfer_intent_json = (
            current_intent
        )
        flow.universal_transfer_reconciliation_json = (
            _json_dict(reconciliation)
        )
        flow.status = (
            BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILING
        )
        flow.error = None
        flow.updated_at = resolved_now

        settlement_batch.status = (
            BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
        )
        settlement_batch.error = None
        settlement_batch.updated_at = (
            resolved_now
        )

        db.add(flow)
        db.add(settlement_batch)
        db.flush()

        result = _step_result(
            ok=False,
            transition=(
                "reconcile_universal_transfer_"
                "missing"
            ),
            settlement_batch=(
                settlement_batch
            ),
            flow=flow,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            diagnostics={
                "pending": (
                    "universal_transfer_"
                    "reconciliation"
                ),
                "did_bybit_post": False,
                "bybit_post_count": 0,
                "record_found": False,
                "query_succeeded": True,
                "no_automatic_resend": True,
            },
        )

        db.commit()

        return result

    record_snapshot = (
        _transfer_record_snapshot(record)
    )

    try:
        _validate_exact_transfer_record(
            record=record,
            snapshot=snapshot,
        )
    except NegativeBybitFlowError as exc:
        reconciliation = {
            "schema": (
                UNIVERSAL_TRANSFER_RECONCILIATION_SCHEMA
            ),
            "phase": "exact_transfer_id_query",
            "transfer_id": snapshot[
                "transfer_id"
            ],
            "record_found": True,
            "query_succeeded": True,
            "exact_match": False,
            "record": record_snapshot,
            "error": str(exc),
            "observed_at": (
                resolved_now.isoformat()
            ),
            "no_automatic_resend": True,
        }

        return (
            _fail_universal_transfer_reconciliation(
                db,
                settlement_batch=(
                    settlement_batch
                ),
                flow=flow,
                intent=current_intent,
                reconciliation=(
                    reconciliation
                ),
                error=str(exc),
                now=resolved_now,
                status_before=status_before,
                settlement_status_before=(
                    settlement_status_before
                ),
                transition=(
                    "reconcile_universal_transfer_"
                    "mismatch"
                ),
            )
        )

    reconciliation = {
        "schema": (
            UNIVERSAL_TRANSFER_RECONCILIATION_SCHEMA
        ),
        "phase": "exact_transfer_id_query",
        "transfer_id": snapshot[
            "transfer_id"
        ],
        "record_found": True,
        "query_succeeded": True,
        "exact_match": True,
        "record": record_snapshot,
        "observed_status": (
            record.status
        ),
        "observed_at": (
            resolved_now.isoformat()
        ),
        "no_automatic_resend": True,
    }

    if _is_bybit_success(record.status):
        current_intent["state"] = (
            "confirmed"
        )
        current_intent["reconciliation"] = (
            reconciliation
        )

        flow.universal_transfer_intent_json = (
            current_intent
        )
        flow.universal_transfer_reconciliation_json = (
            _json_dict(reconciliation)
        )
        flow.universal_transfer_status = (
            record.status
        )
        flow.universal_transfer_confirmed_at = (
            resolved_now
        )
        flow.status = (
            BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED
        )
        flow.error = None
        flow.updated_at = resolved_now

        settlement_batch.status = (
            BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
        )
        settlement_batch.error = None
        settlement_batch.updated_at = (
            resolved_now
        )

        db.add(flow)
        db.add(settlement_batch)
        db.flush()

        result = _step_result(
            ok=True,
            transition=(
                "reconcile_universal_transfer_"
                "confirmed"
            ),
            settlement_batch=(
                settlement_batch
            ),
            flow=flow,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            diagnostics={
                "did_bybit_post": False,
                "bybit_post_count": 0,
                "record_found": True,
                "exact_match": True,
                "observed_status": (
                    record.status
                ),
                "next_transition": (
                    "master_transferable_"
                    "balance_barrier"
                ),
            },
        )

        db.commit()

        return result

    if _is_bybit_pending(record.status):
        current_intent["state"] = (
            "reconciling"
        )
        current_intent["reconciliation"] = (
            reconciliation
        )

        flow.universal_transfer_intent_json = (
            current_intent
        )
        flow.universal_transfer_reconciliation_json = (
            _json_dict(reconciliation)
        )
        flow.universal_transfer_status = (
            record.status
        )
        flow.status = (
            BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILING
        )
        flow.error = None
        flow.updated_at = resolved_now

        settlement_batch.status = (
            BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
        )
        settlement_batch.error = None
        settlement_batch.updated_at = (
            resolved_now
        )

        db.add(flow)
        db.add(settlement_batch)
        db.flush()

        result = _step_result(
            ok=False,
            transition=(
                "reconcile_universal_transfer_"
                "pending"
            ),
            settlement_batch=(
                settlement_batch
            ),
            flow=flow,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            diagnostics={
                "pending": (
                    "universal_transfer_"
                    "reconciliation"
                ),
                "did_bybit_post": False,
                "bybit_post_count": 0,
                "record_found": True,
                "exact_match": True,
                "observed_status": (
                    record.status
                ),
                "no_automatic_resend": True,
            },
        )

        db.commit()

        return result

    reconciliation[
        "terminal_status_requires_review"
    ] = True

    return (
        _fail_universal_transfer_reconciliation(
            db,
            settlement_batch=(
                settlement_batch
            ),
            flow=flow,
            intent=current_intent,
            reconciliation=reconciliation,
            error=(
                "Universal Transfer has "
                "unsupported terminal status: "
                f"{record.status or 'empty'}"
            ),
            now=resolved_now,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            transition=(
                "reconcile_universal_transfer_"
                "terminal_status_review"
            ),
        )
    )


def _store_master_balance_barrier(
    *,
    flow: FundNegativeBybitFlow,
    barrier: dict[str, Any],
) -> None:
    current = flow.reconciliation_json

    if current is None:
        reconciliation: dict[str, Any] = {}
    elif isinstance(current, dict):
        reconciliation = deepcopy(current)
    else:
        raise NegativeBybitFlowError(
            "Flow reconciliation_json must be "
            "a JSON object"
        )

    reconciliation[
        "master_transferable_balance_barrier"
    ] = barrier

    flow.reconciliation_json = _json_dict(
        reconciliation
    )


def _fail_master_balance_barrier(
    db: Session,
    *,
    settlement_batch,
    flow: FundNegativeBybitFlow,
    barrier: dict[str, Any],
    error: str,
    resolved_now: datetime,
    status_before: str | None,
    settlement_status_before: str | None,
    transition: str,
) -> NegativeBybitFlowResult:
    result = _set_failed(
        flow=flow,
        settlement_batch=settlement_batch,
        fund=None,
        status_before=status_before,
        settlement_status_before=(
            settlement_status_before
        ),
        error=error,
        now=resolved_now,
        diagnostics={
            "transition": transition,
            "did_bybit_post": False,
            "bybit_post_count": 0,
            "bybit_get_count": 1,
            "reserve_release_allowed": False,
            "pricing_unlock_allowed": False,
            "master_balance_barrier": (
                barrier
            ),
        },
    )

    # _set_failed() creates the generic failure
    # reconciliation envelope. Add the durable
    # barrier evidence only after that assignment,
    # otherwise it would be overwritten.
    _store_master_balance_barrier(
        flow=flow,
        barrier=barrier,
    )

    db.add(flow)
    db.add(settlement_batch)
    db.flush()
    db.commit()

    return result


def _master_transferable_balance_barrier_once(
    db: Session,
    *,
    settlement_batch,
    flow: FundNegativeBybitFlow,
    bybit_client: BybitV5Client,
    master_uid: str,
    resolved_now: datetime,
    status_before: str | None,
    settlement_status_before: str | None,
) -> NegativeBybitFlowResult:
    if str(flow.status) != (
        BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED
    ):
        raise NegativeBybitFlowError(
            "Master transferable balance barrier "
            "requires reconciled Universal Transfer"
        )

    intent = deepcopy(
        flow.universal_transfer_intent_json
    )

    if not isinstance(intent, dict):
        raise NegativeBybitFlowError(
            "Confirmed Universal Transfer intent "
            "is missing"
        )

    snapshot = _intent_snapshot(
        flow=flow,
        intent=intent,
        allowed_states={"confirmed"},
    )

    clean_master_uid = _required_text(
        master_uid,
        field_name="master_uid",
    )

    if (
        clean_master_uid
        != snapshot["to_member_id"]
    ):
        raise NegativeBybitFlowError(
            "master_uid does not match confirmed "
            "Universal Transfer destination"
        )

    account_type = snapshot[
        "to_account_type"
    ]
    coin = snapshot["coin"]

    if account_type != "FUND":
        raise NegativeBybitFlowError(
            "Master transferable balance barrier "
            "requires FUND destination account"
        )

    required_master_usdt = Decimal(
        flow.required_master_usdt
    )

    if required_master_usdt <= Decimal("0"):
        raise NegativeBybitFlowError(
            "required_master_usdt must be positive"
        )

    if (
        snapshot["amount_usdt"]
        < required_master_usdt
    ):
        raise NegativeBybitFlowError(
            "Confirmed Universal Transfer amount "
            "does not cover required_master_usdt"
        )

    settlement_batch_id = int(
        settlement_batch.id
    )
    expected_flow_id = int(flow.id)

    # Release all service row locks before
    # the read-only Bybit balance GET.
    db.commit()

    balance = None
    query_error: BaseException | None = None

    try:
        balance = query_account_coin_balance(
            bybit_client,
            account_type=account_type,
            coin=coin,
            member_id=clean_master_uid,
            with_transfer_safe_amount=True,
            with_ltv_transfer_safe_amount=True,
        )
    except (
        BybitApiError,
        BybitAssetFlowError,
    ) as exc:
        query_error = exc

    settlement_batch, flow = (
        _locked_flow_for_submit(
            db,
            settlement_batch_id=(
                settlement_batch_id
            ),
        )
    )

    if int(flow.id) != expected_flow_id:
        raise NegativeBybitFlowError(
            "Negative Bybit flow identity changed "
            "during master balance barrier"
        )

    current_intent = deepcopy(
        flow.universal_transfer_intent_json
    )

    if not isinstance(current_intent, dict):
        raise NegativeBybitFlowError(
            "Universal Transfer intent disappeared "
            "during master balance barrier"
        )

    _validate_snapshot_unchanged(
        flow=flow,
        intent=current_intent,
        snapshot=snapshot,
        allowed_states={"confirmed"},
    )

    # Another worker may have confirmed the
    # same barrier while this GET was running.
    if str(flow.status) == (
        BYBIT_FLOW_STATUS_MASTER_BALANCE_CONFIRMED
    ):
        result = _step_result(
            ok=True,
            transition=(
                "master_transferable_balance_"
                "already_confirmed"
            ),
            settlement_batch=settlement_batch,
            flow=flow,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            idempotent=True,
            diagnostics={
                "did_bybit_post": False,
                "bybit_post_count": 0,
                "bybit_get_count": 1,
            },
        )

        db.commit()

        return result

    if str(flow.status) != (
        BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED
    ):
        raise NegativeBybitFlowError(
            "Flow status changed during master "
            "transferable balance query"
        )

    if query_error is not None:
        barrier = {
            "schema": (
                MASTER_TRANSFERABLE_BALANCE_SCHEMA
            ),
            "state": "pending",
            "account_type": account_type,
            "coin": coin,
            "member_id": clean_master_uid,
            "required_master_usdt": (
                _decimal_text(
                    required_master_usdt
                )
            ),
            "query_succeeded": False,
            "query_error": (
                _bounded_external_error(
                    query_error
                )
            ),
            "observed_at": (
                resolved_now.isoformat()
            ),
            "withdrawal_allowed": False,
        }

        _store_master_balance_barrier(
            flow=flow,
            barrier=barrier,
        )

        flow.status = (
            BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED
        )
        flow.error = None
        flow.updated_at = resolved_now

        settlement_batch.status = (
            BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
        )
        settlement_batch.error = None
        settlement_batch.updated_at = (
            resolved_now
        )

        db.add(flow)
        db.add(settlement_batch)
        db.flush()

        result = _step_result(
            ok=False,
            transition=(
                "master_transferable_balance_"
                "query_pending"
            ),
            settlement_batch=settlement_batch,
            flow=flow,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            diagnostics={
                "pending": (
                    "master_transferable_balance"
                ),
                "did_bybit_post": False,
                "bybit_post_count": 0,
                "bybit_get_count": 1,
                "query_succeeded": False,
                "withdrawal_allowed": False,
            },
        )

        db.commit()

        return result

    if balance is None:
        raise NegativeBybitFlowError(
            "Master balance query returned no result"
        )

    observed_account_type = str(
        balance.account_type or ""
    ).strip().upper()

    observed_coin = str(
        balance.coin or ""
    ).strip().upper()

    observed_member_id = str(
        balance.member_id or ""
    ).strip()

    balance_snapshot = {
        "account_type": observed_account_type,
        "coin": observed_coin,
        "member_id": observed_member_id,
        "wallet_balance": _decimal_text(
            Decimal(balance.wallet_balance)
        ),
        "transfer_balance": _decimal_text(
            Decimal(balance.transfer_balance)
        ),
        "transfer_safe_amount": (
            _decimal_text(
                Decimal(
                    balance.transfer_safe_amount
                )
            )
            if (
                balance.transfer_safe_amount
                is not None
            )
            else None
        ),
        "ltv_transfer_safe_amount": (
            _decimal_text(
                Decimal(
                    balance
                    .ltv_transfer_safe_amount
                )
            )
            if (
                balance
                .ltv_transfer_safe_amount
                is not None
            )
            else None
        ),
        "raw": _json_dict(balance.raw),
    }

    mismatch_error: str | None = None

    if observed_account_type != account_type:
        mismatch_error = (
            "Master balance account_type mismatch"
        )
    elif observed_coin != coin:
        mismatch_error = (
            "Master balance coin mismatch"
        )
    elif (
        observed_member_id
        != clean_master_uid
    ):
        mismatch_error = (
            "Master balance member_id mismatch"
        )

    if mismatch_error is not None:
        barrier = {
            "schema": (
                MASTER_TRANSFERABLE_BALANCE_SCHEMA
            ),
            "state": "failed_requires_review",
            "required_master_usdt": (
                _decimal_text(
                    required_master_usdt
                )
            ),
            "query_succeeded": True,
            "balance": balance_snapshot,
            "error": mismatch_error,
            "observed_at": (
                resolved_now.isoformat()
            ),
            "withdrawal_allowed": False,
        }

        return _fail_master_balance_barrier(
            db,
            settlement_batch=settlement_batch,
            flow=flow,
            barrier=barrier,
            error=mismatch_error,
            resolved_now=resolved_now,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            transition=(
                "master_transferable_balance_"
                "mismatch"
            ),
        )

    transfer_balance = Decimal(
        balance.transfer_balance
    )

    sufficient = (
        transfer_balance
        >= required_master_usdt
    )

    barrier = {
        "schema": (
            MASTER_TRANSFERABLE_BALANCE_SCHEMA
        ),
        "state": (
            "confirmed"
            if sufficient
            else "pending"
        ),
        "account_type": account_type,
        "coin": coin,
        "member_id": clean_master_uid,
        "required_master_usdt": (
            _decimal_text(
                required_master_usdt
            )
        ),
        "balance": balance_snapshot,
        "query_succeeded": True,
        "sufficient": sufficient,
        "observed_at": (
            resolved_now.isoformat()
        ),
        "withdrawal_allowed": sufficient,
    }

    _store_master_balance_barrier(
        flow=flow,
        barrier=barrier,
    )

    if not sufficient:
        flow.status = (
            BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED
        )
        flow.error = None
        flow.updated_at = resolved_now

        settlement_batch.status = (
            BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
        )
        settlement_batch.error = None
        settlement_batch.updated_at = (
            resolved_now
        )

        db.add(flow)
        db.add(settlement_batch)
        db.flush()

        result = _step_result(
            ok=False,
            transition=(
                "master_transferable_balance_"
                "pending"
            ),
            settlement_batch=settlement_batch,
            flow=flow,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            diagnostics={
                "pending": (
                    "master_transferable_balance"
                ),
                "did_bybit_post": False,
                "bybit_post_count": 0,
                "bybit_get_count": 1,
                "required_master_usdt": (
                    _decimal_text(
                        required_master_usdt
                    )
                ),
                "observed_transfer_balance": (
                    _decimal_text(
                        transfer_balance
                    )
                ),
                "withdrawal_allowed": False,
            },
        )

        db.commit()

        return result

    flow.status = (
        BYBIT_FLOW_STATUS_MASTER_BALANCE_CONFIRMED
    )
    flow.error = None
    flow.updated_at = resolved_now

    settlement_batch.status = (
        BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
    )
    settlement_batch.error = None
    settlement_batch.updated_at = (
        resolved_now
    )

    db.add(flow)
    db.add(settlement_batch)
    db.flush()

    result = _step_result(
        ok=True,
        transition=(
            "master_transferable_balance_"
            "confirmed"
        ),
        settlement_batch=settlement_batch,
        flow=flow,
        status_before=status_before,
        settlement_status_before=(
            settlement_status_before
        ),
        diagnostics={
            "did_bybit_post": False,
            "bybit_post_count": 0,
            "bybit_get_count": 1,
            "required_master_usdt": (
                _decimal_text(
                    required_master_usdt
                )
            ),
            "observed_transfer_balance": (
                _decimal_text(
                    transfer_balance
                )
            ),
            "withdrawal_allowed": True,
            "next_transition": (
                "prepare_withdrawal_intent"
            ),
        },
    )

    db.commit()

    return result


def _confirmed_master_balance_barrier(
    flow: FundNegativeBybitFlow,
) -> dict[str, Any]:
    reconciliation = flow.reconciliation_json

    if not isinstance(reconciliation, dict):
        raise NegativeBybitFlowError(
            "Master balance reconciliation "
            "evidence is missing"
        )

    barrier = reconciliation.get(
        "master_transferable_balance_barrier"
    )

    if not isinstance(barrier, dict):
        raise NegativeBybitFlowError(
            "Master transferable balance barrier "
            "is missing"
        )

    if (
        barrier.get("schema")
        != MASTER_TRANSFERABLE_BALANCE_SCHEMA
    ):
        raise NegativeBybitFlowError(
            "Master balance barrier schema mismatch"
        )

    if barrier.get("state") != "confirmed":
        raise NegativeBybitFlowError(
            "Master transferable balance is not "
            "confirmed"
        )

    if barrier.get("withdrawal_allowed") is not True:
        raise NegativeBybitFlowError(
            "Master balance barrier does not allow "
            "withdrawal"
        )

    if (
        str(
            barrier.get("account_type")
            or ""
        ).strip().upper()
        != "FUND"
    ):
        raise NegativeBybitFlowError(
            "Master balance barrier account type "
            "must be FUND"
        )

    if (
        str(barrier.get("coin") or "")
        .strip()
        .upper()
        != str(flow.coin or "").strip().upper()
    ):
        raise NegativeBybitFlowError(
            "Master balance barrier coin mismatch"
        )

    if (
        str(barrier.get("member_id") or "").strip()
        != str(flow.to_master_uid or "").strip()
    ):
        raise NegativeBybitFlowError(
            "Master balance barrier member ID "
            "mismatch"
        )

    required_master = Decimal(
        _required_text(
            barrier.get("required_master_usdt"),
            field_name=(
                "master_balance_barrier."
                "required_master_usdt"
            ),
        )
    )

    if not _same_decimal(
        required_master,
        flow.required_master_usdt,
    ):
        raise NegativeBybitFlowError(
            "Master balance barrier required amount "
            "mismatch"
        )

    return deepcopy(barrier)


def _has_withdrawal_evidence(
    flow: FundNegativeBybitFlow,
) -> bool:
    return any(
        (
            bool(flow.withdrawal_request_id),
            bool(flow.withdrawal_id),
            bool(flow.withdrawal_status),
            (
                flow.withdrawal_amount_usdt
                is not None
            ),
            (
                flow.withdrawal_fee_usdt
                is not None
            ),
            bool(flow.withdrawal_coin),
            bool(flow.withdrawal_chain),
            bool(flow.withdrawal_address),
            bool(flow.withdrawal_tx_hash),
            (
                flow.withdrawal_submitted_at
                is not None
            ),
            (
                flow.withdrawal_created_at
                is not None
            ),
        )
    )


def _query_settlement_wallet_usdt_baseline(
    address: str,
) -> dict[str, Any]:
    clean_address = _required_text(
        address,
        field_name="settlement_wallet_address",
    )

    contract_address = _required_text(
        settings.BSC_USDT_CONTRACT,
        field_name="BSC_USDT_CONTRACT",
    )

    decimals = int(
        settings.BSC_USDT_DECIMALS
    )

    if decimals < 0:
        raise NegativeBybitFlowError(
            "BSC_USDT_DECIMALS is invalid"
        )

    try:
        w3 = get_web3()

        wallet_checksum = (
            w3.to_checksum_address(
                clean_address
            )
        )
        contract_checksum = (
            w3.to_checksum_address(
                contract_address
            )
        )

        block_number = int(
            w3.eth.block_number
        )

        contract = w3.eth.contract(
            address=contract_checksum,
            abi=ERC20_BALANCE_OF_ABI,
        )

        raw_balance = int(
            contract.functions.balanceOf(
                wallet_checksum
            ).call(
                block_identifier=block_number
            )
        )

    except Exception as exc:
        raise NegativeBybitFlowError(
            "Settlement wallet USDT baseline "
            f"query failed: {exc}"
        ) from exc

    if raw_balance < 0:
        raise NegativeBybitFlowError(
            "Settlement wallet USDT raw balance "
            "cannot be negative"
        )

    balance_usdt = (
        Decimal(raw_balance)
        / (
            Decimal("10")
            ** decimals
        )
    )

    return {
        "address": clean_address,
        "contract": contract_address,
        "block_number": block_number,
        "decimals": decimals,
        "raw_balance": str(raw_balance),
        "balance_usdt": _decimal_text(
            balance_usdt
        ),
    }


def _validate_withdrawal_intent(
    *,
    flow: FundNegativeBybitFlow,
    intent: dict[str, Any],
    allowed_states: set[str],
) -> None:
    if (
        intent.get("schema")
        != WITHDRAWAL_INTENT_SCHEMA
    ):
        raise NegativeBybitFlowError(
            "Withdrawal intent schema mismatch"
        )

    if (
        intent.get("policy_version")
        != settings
        .NEGATIVE_NET_WITHDRAWAL_POLICY_VERSION
    ):
        raise NegativeBybitFlowError(
            "Withdrawal intent policy mismatch"
        )

    state = str(
        intent.get("state") or ""
    ).strip()

    if state not in allowed_states:
        raise NegativeBybitFlowError(
            "Withdrawal intent state mismatch: "
            f"state={state or 'empty'}, "
            f"allowed={sorted(allowed_states)}"
        )

    payload = intent.get(
        "payload_template"
    )

    if not isinstance(payload, dict):
        raise NegativeBybitFlowError(
            "Withdrawal payload template missing"
        )

    if (
        intent.get("payload_fingerprint")
        != _payload_fingerprint(payload)
    ):
        raise NegativeBybitFlowError(
            "Withdrawal payload fingerprint "
            "mismatch"
        )

    expected_payload = {
        "requestId": _required_text(
            flow.withdrawal_request_id,
            field_name=(
                "flow.withdrawal_request_id"
            ),
        ),
        "coin": _required_text(
            flow.withdrawal_coin,
            field_name="flow.withdrawal_coin",
        ).upper(),
        "chain": _required_text(
            flow.withdrawal_chain,
            field_name="flow.withdrawal_chain",
        ).upper(),
        "address": _required_text(
            flow.withdrawal_address,
            field_name=(
                "flow.withdrawal_address"
            ),
        ),
        "amount": _decimal_text(
            Decimal(
                flow.withdrawal_amount_usdt
            )
        ),
        "forceChain": 1,
        "feeType": int(
            settings
            .NEGATIVE_NET_WITHDRAWAL_FEE_TYPE
        ),
        "accountType": "FUND",
    }

    if payload != expected_payload:
        raise NegativeBybitFlowError(
            "Withdrawal immutable payload "
            "mismatch"
        )

    if not _same_decimal(
        Decimal(
            _required_text(
                intent.get("fee_usdt"),
                field_name=(
                    "withdrawal_intent.fee_usdt"
                ),
            )
        ),
        flow.withdrawal_fee_usdt,
    ):
        raise NegativeBybitFlowError(
            "Withdrawal intent fee mismatch"
        )


def _build_withdrawal_intent(
    *,
    settlement_batch_id: int,
    fund_id: int,
    request_id: str,
    coin: str,
    chain: str,
    address: str,
    amount: str,
    fee_usdt: Decimal,
    amount_precision: int,
    fee_snapshot: dict[str, Any],
    balance_baseline: dict[str, Any],
    prepared_at: datetime,
) -> dict[str, Any]:
    fee_type = int(
        settings.NEGATIVE_NET_WITHDRAWAL_FEE_TYPE
    )

    if fee_type != 0:
        raise NegativeBybitFlowError(
            "Negative-net withdrawal intent "
            "requires feeType=0"
        )

    payload_template = {
        "requestId": request_id,
        "coin": coin,
        "chain": chain,
        "address": address,
        "amount": amount,
        "forceChain": 1,
        "feeType": fee_type,
        "accountType": "FUND",
    }

    intent = {
        "schema": WITHDRAWAL_INTENT_SCHEMA,
        "state": "prepared",
        "policy_version": (
            settings
            .NEGATIVE_NET_WITHDRAWAL_POLICY_VERSION
        ),
        "settlement_batch_id": str(
            int(settlement_batch_id)
        ),
        "fund_id": str(int(fund_id)),
        "request_id": request_id,
        "coin": coin,
        "chain": chain,
        "address": address,
        "amount": amount,
        "fee_usdt": _decimal_text(
            fee_usdt
        ),
        "fee_type": fee_type,
        "account_type": "FUND",
        "force_chain": 1,
        "amount_precision": int(
            amount_precision
        ),
        "timestamp_policy": (
            "submit_time_utc_ms"
        ),
        "payload_template": (
            payload_template
        ),
        "payload_fingerprint": (
            _payload_fingerprint(
                payload_template
            )
        ),
        "fee_snapshot": fee_snapshot,
        "settlement_wallet_balance_baseline": (
            balance_baseline
        ),
        "prepared_at": (
            prepared_at.isoformat()
        ),
        "submit_claim": None,
        "acknowledgement": None,
        "reconciliation": None,
    }

    _reject_float(intent)

    return intent


def _prepare_withdrawal_intent_once(
    db: Session,
    *,
    settlement_batch,
    flow: FundNegativeBybitFlow,
    bybit_client: BybitV5Client,
    resolved_now: datetime,
    status_before: str | None,
    settlement_status_before: str | None,
) -> NegativeBybitFlowResult:
    if str(flow.status) != (
        BYBIT_FLOW_STATUS_MASTER_BALANCE_CONFIRMED
    ):
        raise NegativeBybitFlowError(
            "Withdrawal intent requires confirmed "
            "master transferable balance"
        )

    barrier_snapshot = (
        _confirmed_master_balance_barrier(
            flow
        )
    )

    if flow.withdrawal_intent_json is not None:
        raise NegativeBybitFlowError(
            "Withdrawal intent already exists"
        )

    if _has_withdrawal_evidence(flow):
        raise NegativeBybitFlowError(
            "Withdrawal evidence exists without "
            "durable v2 intent"
        )

    if (
        settings
        .NEGATIVE_NET_WITHDRAWAL_POLICY_VERSION
        != "bsc_exact_received_v1"
    ):
        raise NegativeBybitFlowError(
            "Unsupported negative withdrawal policy"
        )

    fee_type = int(
        settings
        .NEGATIVE_NET_WITHDRAWAL_FEE_TYPE
    )

    if fee_type != 0:
        raise NegativeBybitFlowError(
            "bsc_exact_received_v1 requires "
            "feeType=0"
        )

    if not (
        settings
        .NEGATIVE_NET_REQUIRE_ACTIVE_SETTLEMENT_WALLET
    ):
        raise NegativeBybitFlowError(
            "Active settlement wallet requirement "
            "must be enabled"
        )

    if not (
        settings
        .NEGATIVE_NET_REQUIRE_INTERNAL_SETTLEMENT_WALLET_WHITELIST
    ):
        raise NegativeBybitFlowError(
            "Internal settlement wallet whitelist "
            "requirement must be enabled"
        )

    wallet = _get_active_settlement_wallet(
        db,
        fund_id=int(flow.fund_id),
    )

    coin = _required_text(
        flow.coin,
        field_name="flow.coin",
    ).upper()

    chain = _required_text(
        flow.chain,
        field_name="flow.chain",
    ).upper()

    if coin != "USDT":
        raise NegativeBybitFlowError(
            "Withdrawal coin must be USDT"
        )

    if chain != "BSC":
        raise NegativeBybitFlowError(
            "Withdrawal chain must be BSC"
        )

    if str(wallet.blockchain).upper() != chain:
        raise NegativeBybitFlowError(
            "Settlement wallet blockchain mismatch"
        )

    if str(wallet.wallet_type) != "settlement":
        raise NegativeBybitFlowError(
            "Settlement wallet type mismatch"
        )

    wallet_id = int(wallet.id)
    wallet_address = _required_text(
        wallet.address,
        field_name="settlement_wallet_address",
    )

    settlement_batch_id = int(
        settlement_batch.id
    )
    expected_flow_id = int(flow.id)

    target_amount = Decimal(
        flow.withdrawal_request_amount_usdt
    )
    expected_fee = Decimal(
        flow.bybit_withdrawal_fee_usdt
    )

    # Release settlement, sale, flow and wallet
    # locks before Bybit GET and BSC RPC reads.
    db.commit()

    coin_info = query_coin_info(
        bybit_client,
        coin=coin,
        chain=chain,
    )

    balance_baseline = (
        _query_settlement_wallet_usdt_baseline(
            wallet_address
        )
    )

    settlement_batch = _lock_settlement_batch(
        db,
        settlement_batch_id=(
            settlement_batch_id
        ),
    )
    sale_batch = (
        _lock_sale_batch_for_settlement(
            db,
            settlement_batch_id=(
                settlement_batch_id
            ),
        )
    )
    flow = _lock_existing_flow(
        db,
        settlement_batch_id=(
            settlement_batch_id
        ),
    )

    if flow is None:
        raise NegativeBybitFlowError(
            "Negative Bybit flow disappeared "
            "during withdrawal prepare"
        )

    if int(flow.id) != expected_flow_id:
        raise NegativeBybitFlowError(
            "Negative Bybit flow identity changed "
            "during withdrawal prepare"
        )

    current_wallet = (
        _get_active_settlement_wallet(
            db,
            fund_id=int(flow.fund_id),
        )
    )

    if int(current_wallet.id) != wallet_id:
        raise NegativeBybitFlowError(
            "Active settlement wallet changed "
            "during withdrawal prepare"
        )

    if (
        str(current_wallet.address).strip()
        != wallet_address
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet address changed "
            "during withdrawal prepare"
        )

    _validate_sale_batch_input(
        settlement_batch=settlement_batch,
        sale_batch=sale_batch,
    )

    current_amounts = _validate_target_fields(
        settlement_batch=settlement_batch,
        sale_batch=sale_batch,
    )

    _validate_existing_flow(
        flow=flow,
        settlement_batch=settlement_batch,
        sale_batch=sale_batch,
        amounts=current_amounts,
    )

    if (
        _confirmed_master_balance_barrier(flow)
        != barrier_snapshot
    ):
        raise NegativeBybitFlowError(
            "Master balance barrier changed during "
            "withdrawal prepare"
        )

    concurrent_intent = (
        flow.withdrawal_intent_json
    )

    if isinstance(concurrent_intent, dict):
        _validate_withdrawal_intent(
            flow=flow,
            intent=concurrent_intent,
            allowed_states={"prepared"},
        )

        result = _step_result(
            ok=True,
            transition=(
                "prepare_withdrawal_intent_"
                "concurrent_state_detected"
            ),
            settlement_batch=settlement_batch,
            flow=flow,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            idempotent=True,
            diagnostics={
                "did_bybit_post": False,
                "bybit_post_count": 0,
                "bybit_get_count": 1,
                "bsc_rpc_read_count": 1,
            },
        )

        db.commit()
        return result

    if concurrent_intent is not None:
        raise NegativeBybitFlowError(
            "Withdrawal intent must be a JSON object"
        )

    if str(flow.status) != (
        BYBIT_FLOW_STATUS_MASTER_BALANCE_CONFIRMED
    ):
        raise NegativeBybitFlowError(
            "Flow status changed during withdrawal "
            "prepare"
        )

    if str(coin_info.coin).upper() != coin:
        raise NegativeBybitFlowError(
            "Coin info coin mismatch"
        )

    if str(coin_info.chain).upper() != chain:
        raise NegativeBybitFlowError(
            "Coin info chain mismatch"
        )

    if str(
        coin_info.chain_withdraw or ""
    ).strip() != "1":
        raise NegativeBybitFlowError(
            "BSC USDT withdrawal is disabled"
        )

    percentage_fee = (
        coin_info.withdraw_percentage_fee
    )

    if (
        percentage_fee is not None
        and Decimal(percentage_fee)
        != Decimal("0")
    ):
        raise NegativeBybitFlowError(
            "Percentage withdrawal fee is not "
            "supported by exact-received policy"
        )

    dynamic_fee = Decimal(
        coin_info.withdraw_fee
    )

    if not _same_decimal(
        dynamic_fee,
        expected_fee,
    ):
        raise NegativeBybitFlowError(
            "Bybit withdrawal fee snapshot does not "
            "match settlement fee"
        )

    if target_amount < Decimal(
        coin_info.withdraw_min
    ):
        raise NegativeBybitFlowError(
            "Withdrawal amount is below withdrawMin"
        )

    if (
        coin_info.withdraw_max is not None
        and Decimal(coin_info.withdraw_max)
        > Decimal("0")
        and target_amount
        > Decimal(coin_info.withdraw_max)
    ):
        raise NegativeBybitFlowError(
            "Withdrawal amount exceeds withdrawMax"
        )

    amount_text, amount_actual = (
        withdrawal_actual_amount(
            withdrawal_request_amount_usdt=(
                target_amount
            ),
            precision=int(
                coin_info.min_accuracy
            ),
        )
    )

    if not _same_decimal(
        amount_actual,
        target_amount,
    ):
        raise NegativeBybitFlowError(
            "Withdrawal amount changed during "
            "formatting"
        )

    request_id = (
        deterministic_withdrawal_request_id(
            settlement_batch_id=(
                settlement_batch_id
            ),
            fund_id=int(flow.fund_id),
            settlement_wallet_address=(
                wallet_address
            ),
            withdrawal_request_amount_usdt=(
                amount_actual
            ),
            coin=coin,
            chain=chain,
        )
    )

    fee_snapshot = {
        "schema": (
            "negative_withdrawal_fee_snapshot_v1"
        ),
        "policy_version": (
            settings
            .NEGATIVE_NET_WITHDRAWAL_POLICY_VERSION
        ),
        "queried_at": (
            resolved_now.isoformat()
        ),
        "max_age_sec": int(
            settings
            .NEGATIVE_NET_WITHDRAWAL_FEE_MAX_AGE_SEC
        ),
        "coin": coin,
        "chain": chain,
        "withdraw_fee_usdt": (
            _decimal_text(dynamic_fee)
        ),
        "withdraw_min_usdt": (
            _decimal_text(
                Decimal(
                    coin_info.withdraw_min
                )
            )
        ),
        "withdraw_max_usdt": (
            _decimal_text(
                Decimal(
                    coin_info.withdraw_max
                )
            )
            if coin_info.withdraw_max is not None
            else None
        ),
        "withdraw_percentage_fee": (
            _decimal_text(
                Decimal(percentage_fee)
            )
            if percentage_fee is not None
            else None
        ),
        "min_accuracy": int(
            coin_info.min_accuracy
        ),
        "chain_withdraw": (
            coin_info.chain_withdraw
        ),
        "raw": _json_dict(
            coin_info.raw
        ),
    }

    intent = _build_withdrawal_intent(
        settlement_batch_id=(
            settlement_batch_id
        ),
        fund_id=int(flow.fund_id),
        request_id=request_id,
        coin=coin,
        chain=chain,
        address=wallet_address,
        amount=amount_text,
        fee_usdt=dynamic_fee,
        amount_precision=int(
            coin_info.min_accuracy
        ),
        fee_snapshot=fee_snapshot,
        balance_baseline=balance_baseline,
        prepared_at=resolved_now,
    )

    flow.settlement_wallet_id = wallet_id
    flow.settlement_wallet_address = (
        wallet_address
    )

    flow.withdrawal_policy_version = (
        settings
        .NEGATIVE_NET_WITHDRAWAL_POLICY_VERSION
    )
    flow.coin_info_snapshot_json = (
        _json_dict(fee_snapshot)
    )
    flow.settlement_wallet_balance_before_usdt = (
        Decimal(
            balance_baseline["balance_usdt"]
        )
    )

    flow.withdrawal_request_id = request_id
    flow.withdrawal_amount_usdt = (
        amount_actual
    )
    flow.withdrawal_fee_usdt = dynamic_fee
    flow.withdrawal_coin = coin
    flow.withdrawal_chain = chain
    flow.withdrawal_address = (
        wallet_address
    )
    flow.withdrawal_intent_json = intent

    flow.status = (
        BYBIT_FLOW_STATUS_WITHDRAWAL_INTENT_PREPARED
    )
    flow.error = None
    flow.updated_at = resolved_now

    settlement_batch.status = (
        BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
    )
    settlement_batch.error = None
    settlement_batch.updated_at = (
        resolved_now
    )

    db.add(flow)
    db.add(settlement_batch)
    db.flush()

    result = _step_result(
        ok=True,
        transition=(
            "prepare_withdrawal_intent"
        ),
        settlement_batch=settlement_batch,
        flow=flow,
        status_before=status_before,
        settlement_status_before=(
            settlement_status_before
        ),
        diagnostics={
            "did_bybit_post": False,
            "bybit_post_count": 0,
            "bybit_get_count": 1,
            "bsc_rpc_read_count": 1,
            "request_id": request_id,
            "payload_fingerprint": intent[
                "payload_fingerprint"
            ],
            "withdrawal_fee_usdt": (
                _decimal_text(dynamic_fee)
            ),
            "settlement_wallet_balance_before_usdt": (
                balance_baseline[
                    "balance_usdt"
                ]
            ),
            "next_transition": (
                "submit_withdrawal"
            ),
        },
    )

    db.commit()

    return result


def _aware_utc_datetime(
    value: Any,
    *,
    field_name: str,
) -> datetime:
    text = _required_text(
        value,
        field_name=field_name,
    )

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise NegativeBybitFlowError(
            f"{field_name} must be ISO datetime"
        ) from exc

    if (
        parsed.tzinfo is None
        or parsed.utcoffset() is None
    ):
        raise NegativeBybitFlowError(
            f"{field_name} must be timezone-aware"
        )

    return parsed.astimezone(timezone.utc)


def _withdrawal_intent_snapshot(
    *,
    flow: FundNegativeBybitFlow,
    intent: dict[str, Any],
    allowed_states: set[str],
) -> dict[str, Any]:
    _validate_withdrawal_intent(
        flow=flow,
        intent=intent,
        allowed_states=allowed_states,
    )

    payload = intent.get(
        "payload_template"
    )
    fee_snapshot = intent.get(
        "fee_snapshot"
    )
    balance_baseline = intent.get(
        "settlement_wallet_balance_baseline"
    )

    if not isinstance(payload, dict):
        raise NegativeBybitFlowError(
            "Withdrawal payload template missing"
        )

    if not isinstance(fee_snapshot, dict):
        raise NegativeBybitFlowError(
            "Withdrawal fee snapshot missing"
        )

    if not isinstance(
        balance_baseline,
        dict,
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet balance baseline "
            "missing"
        )

    request_id = _required_text(
        intent.get("request_id"),
        field_name=(
            "withdrawal_intent.request_id"
        ),
    )

    coin = _required_text(
        intent.get("coin"),
        field_name="withdrawal_intent.coin",
    ).upper()

    chain = _required_text(
        intent.get("chain"),
        field_name="withdrawal_intent.chain",
    ).upper()

    address = _required_text(
        intent.get("address"),
        field_name="withdrawal_intent.address",
    )

    amount_text = _required_text(
        intent.get("amount"),
        field_name="withdrawal_intent.amount",
    )

    amount_usdt = Decimal(amount_text)

    if amount_usdt <= Decimal("0"):
        raise NegativeBybitFlowError(
            "Withdrawal intent amount must be "
            "positive"
        )

    fee_usdt = Decimal(
        _required_text(
            intent.get("fee_usdt"),
            field_name=(
                "withdrawal_intent.fee_usdt"
            ),
        )
    )

    if fee_usdt <= Decimal("0"):
        raise NegativeBybitFlowError(
            "Withdrawal intent fee must be positive"
        )

    fee_type = int(
        intent.get("fee_type")
    )
    force_chain = int(
        intent.get("force_chain")
    )
    amount_precision = int(
        intent.get("amount_precision")
    )

    account_type = _required_text(
        intent.get("account_type"),
        field_name=(
            "withdrawal_intent.account_type"
        ),
    ).upper()

    if coin != "USDT":
        raise NegativeBybitFlowError(
            "Withdrawal intent coin must be USDT"
        )

    if chain != "BSC":
        raise NegativeBybitFlowError(
            "Withdrawal intent chain must be BSC"
        )

    if fee_type != 0:
        raise NegativeBybitFlowError(
            "Withdrawal intent feeType must be 0"
        )

    if force_chain != 1:
        raise NegativeBybitFlowError(
            "Withdrawal intent forceChain must be 1"
        )

    if account_type != "FUND":
        raise NegativeBybitFlowError(
            "Withdrawal intent accountType must "
            "be FUND"
        )

    if amount_precision < 0:
        raise NegativeBybitFlowError(
            "Withdrawal intent precision is invalid"
        )

    if (
        str(payload.get("requestId") or "")
        != request_id
    ):
        raise NegativeBybitFlowError(
            "Withdrawal requestId mismatch"
        )

    if (
        str(payload.get("coin") or "")
        .strip()
        .upper()
        != coin
    ):
        raise NegativeBybitFlowError(
            "Withdrawal payload coin mismatch"
        )

    if (
        str(payload.get("chain") or "")
        .strip()
        .upper()
        != chain
    ):
        raise NegativeBybitFlowError(
            "Withdrawal payload chain mismatch"
        )

    if (
        str(payload.get("address") or "").strip()
        != address
    ):
        raise NegativeBybitFlowError(
            "Withdrawal payload address mismatch"
        )

    if (
        str(payload.get("amount") or "").strip()
        != amount_text
    ):
        raise NegativeBybitFlowError(
            "Withdrawal payload amount mismatch"
        )

    if int(payload.get("feeType")) != fee_type:
        raise NegativeBybitFlowError(
            "Withdrawal payload feeType mismatch"
        )

    if (
        int(payload.get("forceChain"))
        != force_chain
    ):
        raise NegativeBybitFlowError(
            "Withdrawal payload forceChain mismatch"
        )

    if (
        str(payload.get("accountType") or "")
        .strip()
        .upper()
        != account_type
    ):
        raise NegativeBybitFlowError(
            "Withdrawal payload accountType mismatch"
        )

    if (
        fee_snapshot.get("schema")
        != "negative_withdrawal_fee_snapshot_v1"
    ):
        raise NegativeBybitFlowError(
            "Withdrawal fee snapshot schema mismatch"
        )

    if (
        fee_snapshot.get("policy_version")
        != settings
        .NEGATIVE_NET_WITHDRAWAL_POLICY_VERSION
    ):
        raise NegativeBybitFlowError(
            "Withdrawal fee snapshot policy mismatch"
        )

    if (
        str(fee_snapshot.get("coin") or "")
        .strip()
        .upper()
        != coin
    ):
        raise NegativeBybitFlowError(
            "Withdrawal fee snapshot coin mismatch"
        )

    if (
        str(fee_snapshot.get("chain") or "")
        .strip()
        .upper()
        != chain
    ):
        raise NegativeBybitFlowError(
            "Withdrawal fee snapshot chain mismatch"
        )

    snapshot_fee = Decimal(
        _required_text(
            fee_snapshot.get(
                "withdraw_fee_usdt"
            ),
            field_name=(
                "fee_snapshot.withdraw_fee_usdt"
            ),
        )
    )

    if not _same_decimal(
        snapshot_fee,
        fee_usdt,
    ):
        raise NegativeBybitFlowError(
            "Withdrawal fee snapshot amount mismatch"
        )

    snapshot_precision = int(
        fee_snapshot.get("min_accuracy")
    )

    if snapshot_precision != amount_precision:
        raise NegativeBybitFlowError(
            "Withdrawal fee snapshot precision "
            "mismatch"
        )

    if (
        str(
            fee_snapshot.get("chain_withdraw")
            or ""
        ).strip()
        != "1"
    ):
        raise NegativeBybitFlowError(
            "Withdrawal fee snapshot does not allow "
            "BSC withdrawal"
        )

    percentage_fee_text = (
        fee_snapshot.get(
            "withdraw_percentage_fee"
        )
    )

    if (
        percentage_fee_text is not None
        and Decimal(
            str(percentage_fee_text)
        ) != Decimal("0")
    ):
        raise NegativeBybitFlowError(
            "Percentage withdrawal fee is not "
            "supported"
        )

    max_age_sec = int(
        fee_snapshot.get("max_age_sec")
    )

    configured_max_age_sec = int(
        settings
        .NEGATIVE_NET_WITHDRAWAL_FEE_MAX_AGE_SEC
    )

    if configured_max_age_sec <= 0:
        raise NegativeBybitFlowError(
            "Withdrawal fee max age is invalid"
        )

    if max_age_sec != configured_max_age_sec:
        raise NegativeBybitFlowError(
            "Withdrawal fee max age mismatch"
        )

    fee_queried_at = _aware_utc_datetime(
        fee_snapshot.get("queried_at"),
        field_name=(
            "fee_snapshot.queried_at"
        ),
    )

    prepared_at = _aware_utc_datetime(
        intent.get("prepared_at"),
        field_name=(
            "withdrawal_intent.prepared_at"
        ),
    )

    baseline_address = _required_text(
        balance_baseline.get("address"),
        field_name=(
            "balance_baseline.address"
        ),
    )

    if (
        baseline_address.lower()
        != address.lower()
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet baseline address "
            "mismatch"
        )

    baseline_contract = _required_text(
        balance_baseline.get("contract"),
        field_name=(
            "balance_baseline.contract"
        ),
    )

    if (
        baseline_contract.lower()
        != str(
            settings.BSC_USDT_CONTRACT
        ).strip().lower()
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet baseline contract "
            "mismatch"
        )

    baseline_decimals = int(
        balance_baseline.get("decimals")
    )

    if baseline_decimals != int(
        settings.BSC_USDT_DECIMALS
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet baseline decimals "
            "mismatch"
        )

    baseline_block_number = int(
        balance_baseline.get("block_number")
    )

    if baseline_block_number < 0:
        raise NegativeBybitFlowError(
            "Settlement wallet baseline block is "
            "invalid"
        )

    baseline_raw_balance = int(
        _required_text(
            balance_baseline.get(
                "raw_balance"
            ),
            field_name=(
                "balance_baseline.raw_balance"
            ),
        )
    )

    if baseline_raw_balance < 0:
        raise NegativeBybitFlowError(
            "Settlement wallet baseline raw balance "
            "is invalid"
        )

    baseline_balance_usdt = Decimal(
        _required_text(
            balance_baseline.get(
                "balance_usdt"
            ),
            field_name=(
                "balance_baseline.balance_usdt"
            ),
        )
    )

    if flow.settlement_wallet_id is None:
        raise NegativeBybitFlowError(
            "Settlement wallet ID is missing"
        )

    if (
        flow
        .settlement_wallet_balance_before_usdt
        is None
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet baseline amount is "
            "missing"
        )

    if not _same_decimal(
        baseline_balance_usdt,
        flow
        .settlement_wallet_balance_before_usdt,
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet baseline amount "
            "mismatch"
        )

    return {
        "settlement_batch_id": int(
            flow.settlement_batch_id
        ),
        "fund_id": int(flow.fund_id),
        "flow_id": int(flow.id),
        "settlement_wallet_id": int(
            flow.settlement_wallet_id
        ),
        "request_id": request_id,
        "coin": coin,
        "chain": chain,
        "address": address,
        "amount_text": amount_text,
        "amount_usdt": amount_usdt,
        "fee_usdt": fee_usdt,
        "fee_type": fee_type,
        "force_chain": force_chain,
        "account_type": account_type,
        "amount_precision": (
            amount_precision
        ),
        "payload": deepcopy(payload),
        "payload_fingerprint": (
            _required_text(
                intent.get(
                    "payload_fingerprint"
                ),
                field_name=(
                    "withdrawal_intent."
                    "payload_fingerprint"
                ),
            )
        ),
        "fee_snapshot": deepcopy(
            fee_snapshot
        ),
        "balance_baseline": deepcopy(
            balance_baseline
        ),
        "fee_queried_at": fee_queried_at,
        "prepared_at": prepared_at,
        "max_age_sec": max_age_sec,
    }


def _validate_withdrawal_snapshot_unchanged(
    *,
    flow: FundNegativeBybitFlow,
    intent: dict[str, Any],
    snapshot: dict[str, Any],
    allowed_states: set[str],
) -> None:
    current_snapshot = (
        _withdrawal_intent_snapshot(
            flow=flow,
            intent=intent,
            allowed_states=allowed_states,
        )
    )

    if current_snapshot != snapshot:
        raise NegativeBybitFlowError(
            "Withdrawal immutable intent changed "
            "during submit"
        )


def _withdrawal_claim_matches(
    *,
    intent: dict[str, Any],
    claim_token: str,
) -> bool:
    claim = intent.get("submit_claim")

    return (
        isinstance(claim, dict)
        and str(
            claim.get("claim_token") or ""
        ) == claim_token
    )


def _withdrawal_ack_snapshot(
    record,
) -> dict[str, Any]:
    return {
        "request_id": str(
            record.request_id or ""
        ).strip(),
        "withdrawal_id": (
            str(record.withdrawal_id).strip()
            if record.withdrawal_id
            else None
        ),
        "coin": str(
            record.coin or ""
        ).strip().upper(),
        "chain": str(
            record.chain or ""
        ).strip().upper(),
        "address": str(
            record.address or ""
        ).strip(),
        "amount_usdt": _decimal_text(
            Decimal(record.amount_usdt)
        ),
        "fee_type": int(record.fee_type),
        "status": record.status,
        "tx_hash": (
            str(record.tx_hash).strip()
            if record.tx_hash
            else None
        ),
    }


def _validate_exact_withdrawal_ack(
    *,
    record,
    snapshot: dict[str, Any],
) -> None:
    if (
        str(record.request_id or "").strip()
        != snapshot["request_id"]
    ):
        raise NegativeBybitFlowError(
            "Withdrawal acknowledgement requestId "
            "mismatch"
        )

    if (
        str(record.coin or "").strip().upper()
        != snapshot["coin"]
    ):
        raise NegativeBybitFlowError(
            "Withdrawal acknowledgement coin "
            "mismatch"
        )

    if (
        str(record.chain or "").strip().upper()
        != snapshot["chain"]
    ):
        raise NegativeBybitFlowError(
            "Withdrawal acknowledgement chain "
            "mismatch"
        )

    if (
        str(record.address or "").strip().lower()
        != snapshot["address"].lower()
    ):
        raise NegativeBybitFlowError(
            "Withdrawal acknowledgement address "
            "mismatch"
        )

    if not _same_decimal(
        record.amount_usdt,
        snapshot["amount_usdt"],
    ):
        raise NegativeBybitFlowError(
            "Withdrawal acknowledgement amount "
            "mismatch"
        )

    if int(record.fee_type) != int(
        snapshot["fee_type"]
    ):
        raise NegativeBybitFlowError(
            "Withdrawal acknowledgement feeType "
            "mismatch"
        )


def _withdrawal_record_fingerprint(
    record,
) -> str:
    payload = {
        "request_id": (
            str(record.request_id).strip()
            if record.request_id
            else None
        ),
        "withdrawal_id": (
            str(record.withdrawal_id).strip()
            if record.withdrawal_id
            else None
        ),
        "coin": str(
            record.coin or ""
        ).strip().upper(),
        "chain": str(
            record.chain or ""
        ).strip().upper(),
        "address": str(
            record.address or ""
        ).strip().lower(),
        "amount_usdt": _decimal_text(
            Decimal(record.amount_usdt)
        ),
        "fee_usdt": (
            _decimal_text(
                Decimal(record.fee_usdt)
            )
            if record.fee_usdt
            is not None
            else None
        ),
        "fee_type": int(
            record.fee_type
        ),
        "withdrawal_status": str(
            record.status or ""
        ).strip(),
        "tx_hash": (
            str(record.tx_hash)
            .strip()
            .lower()
            if record.tx_hash
            else None
        ),
        "created_time_ms": (
            int(record.created_time_ms)
            if record.created_time_ms
            is not None
            else None
        ),
    }

    return _payload_fingerprint(
        payload
    )


def _withdrawal_pagination_evidence(
    result: BybitWithdrawalPaginationResult,
) -> dict[str, Any]:
    return {
        "exhausted": bool(
            result.exhausted
        ),
        "stop_reason": str(
            result.stop_reason
        ),
        "page_count": len(
            result.pages
        ),
        "returned_record_count": len(
            result.records
        ),
        "pages": [
            {
                "page_number": int(
                    page.page_number
                ),
                "request_cursor": (
                    page.request_cursor
                ),
                "next_cursor": (
                    page.next_cursor
                ),
                "record_count": int(
                    page.record_count
                ),
                "page_fingerprint": (
                    page.page_fingerprint
                ),
            }
            for page in result.pages
        ],
    }


def _withdrawal_record_intent_match(
    *,
    record,
    snapshot: dict[str, Any],
    lookup_start_ms: int,
    lookup_end_ms: int,
) -> tuple[bool, str | None]:
    record_coin = str(
        record.coin or ""
    ).strip().upper()

    record_chain = str(
        record.chain or ""
    ).strip().upper()

    record_address = str(
        record.address or ""
    ).strip().lower()

    record_amount = Decimal(
        record.amount_usdt
    )

    core_candidate = (
        record_coin
        == snapshot["coin"]
        and record_chain
        == snapshot["chain"]
        and record_address
        == snapshot["address"].lower()
        and _same_decimal(
            record_amount,
            snapshot["amount_usdt"],
        )
    )

    if not core_candidate:
        return False, None

    if (
        int(record.fee_type)
        != int(snapshot["fee_type"])
    ):
        return (
            False,
            "Withdrawal record feeType mismatch",
        )

    if record.fee_usdt is None:
        return (
            False,
            "Withdrawal record fixed fee is missing",
        )

    if not _same_decimal(
        Decimal(record.fee_usdt),
        snapshot["fee_usdt"],
    ):
        return (
            False,
            "Withdrawal record fixed fee mismatch",
        )

    if record.created_time_ms is None:
        return (
            False,
            "Withdrawal record createdTime is "
            "missing",
        )

    created_time_ms = int(
        record.created_time_ms
    )

    if not (
        int(lookup_start_ms)
        <= created_time_ms
        <= int(lookup_end_ms)
    ):
        return (
            False,
            "Withdrawal record createdTime is "
            "outside lookup window",
        )

    record_request_id = str(
        record.request_id or ""
    ).strip()

    if (
        record_request_id
        and record_request_id
        != snapshot["request_id"]
    ):
        return (
            False,
            "Withdrawal record requestId mismatch",
        )

    return True, None


def _withdrawal_record_core_mismatch(
    *,
    record,
    snapshot: dict[str, Any],
) -> str | None:
    if (
        str(record.coin or "")
        .strip()
        .upper()
        != snapshot["coin"]
    ):
        return (
            "Withdrawal record coin mismatch"
        )

    if (
        str(record.chain or "")
        .strip()
        .upper()
        != snapshot["chain"]
    ):
        return (
            "Withdrawal record chain mismatch"
        )

    if (
        str(record.address or "")
        .strip()
        .lower()
        != snapshot["address"].lower()
    ):
        return (
            "Withdrawal record address mismatch"
        )

    if not _same_decimal(
        Decimal(record.amount_usdt),
        snapshot["amount_usdt"],
    ):
        return (
            "Withdrawal record amount mismatch"
        )

    return None


def _deduplicate_withdrawal_records(
    records,
) -> list[Any]:
    unique: dict[str, Any] = {}

    for record in records:
        fingerprint = (
            _withdrawal_record_fingerprint(
                record
            )
        )

        if fingerprint not in unique:
            unique[fingerprint] = record

    return list(unique.values())


class _BybitReadCounter:
    def __init__(
        self,
        client: BybitV5Client,
    ) -> None:
        self.client = client
        self.get_count = 0

    def get(
        self,
        path: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self.get_count += 1

        return self.client.get(
            path,
            params,
        )


def _withdrawal_source_records(
    *,
    source: str,
    records: list[Any],
    withdrawal_id: str | None,
    tx_hash: str | None,
) -> list[Any]:
    if source == "withdrawal_id_query":
        expected_id = str(
            withdrawal_id or ""
        ).strip()

        return [
            record
            for record in records
            if str(
                record.withdrawal_id or ""
            ).strip()
            == expected_id
        ]

    if source == "tx_hash_query":
        expected_hash = _normalized_hex(
            tx_hash
        )

        return [
            record
            for record in records
            if _normalized_hex(
                record.tx_hash
            )
            == expected_hash
        ]

    return records


def _withdrawal_recovery_lookup(
    *,
    bybit_client: BybitV5Client,
    snapshot: dict[str, Any],
    saved_withdrawal_id: str | None,
    saved_tx_hash: str | None,
    lookup_start_ms: int,
    lookup_end_ms: int,
    max_pages: int,
) -> dict[str, Any]:
    counter = _BybitReadCounter(
        bybit_client
    )

    query_evidence: dict[
        str,
        Any,
    ] = {}

    query_specs: list[
        tuple[
            str,
            str | None,
            str | None,
        ]
    ] = []

    clean_withdrawal_id = str(
        saved_withdrawal_id or ""
    ).strip() or None

    clean_tx_hash = str(
        saved_tx_hash or ""
    ).strip() or None

    if clean_withdrawal_id is not None:
        query_specs.append(
            (
                "withdrawal_id_query",
                clean_withdrawal_id,
                None,
            )
        )

    if clean_tx_hash is not None:
        query_specs.append(
            (
                "tx_hash_query",
                None,
                clean_tx_hash,
            )
        )

    query_specs.append(
        (
            "bounded_record_lookup",
            None,
            None,
        )
    )

    for (
        source,
        withdrawal_id_filter,
        tx_hash_filter,
    ) in query_specs:
        try:
            result = (
                list_master_withdrawals_paginated(
                    counter,
                    coin=snapshot["coin"],
                    start_time_ms=(
                        lookup_start_ms
                    ),
                    end_time_ms=(
                        lookup_end_ms
                    ),
                    limit=(
                        WITHDRAWAL_RECORD_LOOKUP_LIMIT
                    ),
                    max_pages=max_pages,
                    withdrawal_id=(
                        withdrawal_id_filter
                    ),
                    tx_hash=tx_hash_filter,
                )
            )

        except BybitApiError as exc:
            query_evidence[source] = {
                "state": "query_error",
                "error": (
                    _bounded_external_error(exc)
                ),
            }

            return {
                "state": "query_error",
                "selected_record": None,
                "selected_source": source,
                "unique_match": False,
                "ambiguous": False,
                "exact_fingerprint_match": False,
                "error": (
                    _bounded_external_error(exc)
                ),
                "queries": query_evidence,
                "bybit_get_count": (
                    counter.get_count
                ),
            }

        except BybitAssetFlowError as exc:
            query_evidence[source] = {
                "state": "lookup_incomplete",
                "error": (
                    _bounded_external_error(exc)
                ),
            }

            return {
                "state": "lookup_incomplete",
                "selected_record": None,
                "selected_source": source,
                "unique_match": False,
                "ambiguous": False,
                "exact_fingerprint_match": False,
                "error": (
                    _bounded_external_error(exc)
                ),
                "queries": query_evidence,
                "bybit_get_count": (
                    counter.get_count
                ),
            }

        source_evidence = (
            _withdrawal_pagination_evidence(
                result
            )
        )

        query_evidence[source] = (
            source_evidence
        )

        if not result.exhausted:
            return {
                "state": "lookup_incomplete",
                "selected_record": None,
                "selected_source": None,
                "unique_match": False,
                "ambiguous": False,
                "exact_fingerprint_match": False,
                "error": (
                    "Withdrawal lookup reached "
                    "max_pages before exhausting "
                    f"{source}"
                ),
                "queries": query_evidence,
                "bybit_get_count": (
                    counter.get_count
                ),
            }

        source_records = (
            _withdrawal_source_records(
                source=source,
                records=(
                    _deduplicate_withdrawal_records(
                        result.records
                    )
                ),
                withdrawal_id=(
                    clean_withdrawal_id
                ),
                tx_hash=clean_tx_hash,
            )
        )

        exact_matches: list[Any] = []
        mismatch_evidence: list[
            dict[str, Any]
        ] = []

        for record in source_records:
            matched, mismatch = (
                _withdrawal_record_intent_match(
                    record=record,
                    snapshot=snapshot,
                    lookup_start_ms=(
                        lookup_start_ms
                    ),
                    lookup_end_ms=(
                        lookup_end_ms
                    ),
                )
            )

            if matched:
                exact_matches.append(
                    record
                )
                continue

            if (
                mismatch is not None
                or source
                in {
                    "withdrawal_id_query",
                    "tx_hash_query",
                }
            ):
                resolved_mismatch = (
                    mismatch
                )

                if (
                    resolved_mismatch is None
                    and source
                    in {
                        "withdrawal_id_query",
                        "tx_hash_query",
                    }
                ):
                    resolved_mismatch = (
                        _withdrawal_record_core_mismatch(
                            record=record,
                            snapshot=snapshot,
                        )
                    )

                mismatch_evidence.append(
                    {
                        "record_fingerprint": (
                            _withdrawal_record_fingerprint(
                                record
                            )
                        ),
                        "error": (
                            resolved_mismatch
                            or (
                                "Withdrawal record "
                                "immutable fingerprint "
                                "mismatch"
                            )
                        ),
                    }
                )

        exact_matches = (
            _deduplicate_withdrawal_records(
                exact_matches
            )
        )

        source_evidence[
            "source_record_count"
        ] = len(source_records)

        source_evidence[
            "exact_match_count"
        ] = len(exact_matches)

        source_evidence[
            "mismatch_count"
        ] = len(mismatch_evidence)

        source_evidence[
            "mismatches"
        ] = mismatch_evidence

        if mismatch_evidence:
            return {
                "state": "record_mismatch",
                "selected_record": None,
                "selected_source": source,
                "unique_match": False,
                "ambiguous": False,
                "exact_fingerprint_match": False,
                "error": mismatch_evidence[
                    0
                ]["error"],
                "queries": query_evidence,
                "bybit_get_count": (
                    counter.get_count
                ),
            }

        if len(exact_matches) > 1:
            return {
                "state": "ambiguous",
                "selected_record": None,
                "selected_source": source,
                "unique_match": False,
                "ambiguous": True,
                "exact_fingerprint_match": False,
                "error": (
                    "Multiple Bybit withdrawal "
                    "records match immutable intent"
                ),
                "matching_record_fingerprints": [
                    _withdrawal_record_fingerprint(
                        record
                    )
                    for record in exact_matches
                ],
                "queries": query_evidence,
                "bybit_get_count": (
                    counter.get_count
                ),
            }

        if len(exact_matches) == 1:
            selected_record = (
                exact_matches[0]
            )

            return {
                "state": "unique_match",
                "selected_record": (
                    selected_record
                ),
                "selected_source": source,
                "unique_match": True,
                "ambiguous": False,
                "exact_fingerprint_match": True,
                "record_fingerprint": (
                    _withdrawal_record_fingerprint(
                        selected_record
                    )
                ),
                "queries": query_evidence,
                "bybit_get_count": (
                    counter.get_count
                ),
            }

    return {
        "state": "record_not_found",
        "selected_record": None,
        "selected_source": None,
        "unique_match": False,
        "ambiguous": False,
        "exact_fingerprint_match": False,
        "queries": query_evidence,
        "bybit_get_count": (
            counter.get_count
        ),
    }


def _validate_fresh_withdrawal_fee(
    *,
    snapshot: dict[str, Any],
    coin_info,
    checked_at: datetime,
) -> dict[str, Any]:
    if (
        str(coin_info.coin or "")
        .strip()
        .upper()
        != snapshot["coin"]
    ):
        raise NegativeBybitFlowError(
            "Withdrawal fee revalidation coin "
            "mismatch"
        )

    if (
        str(coin_info.chain or "")
        .strip()
        .upper()
        != snapshot["chain"]
    ):
        raise NegativeBybitFlowError(
            "Withdrawal fee revalidation chain "
            "mismatch"
        )

    if (
        str(
            coin_info.chain_withdraw or ""
        ).strip()
        != "1"
    ):
        raise NegativeBybitFlowError(
            "BSC withdrawal became unavailable"
        )

    percentage_fee = (
        coin_info.withdraw_percentage_fee
    )

    if (
        percentage_fee is not None
        and Decimal(percentage_fee)
        != Decimal("0")
    ):
        raise NegativeBybitFlowError(
            "Percentage withdrawal fee appeared "
            "during revalidation"
        )

    observed_fee = Decimal(
        coin_info.withdraw_fee
    )

    if not _same_decimal(
        observed_fee,
        snapshot["fee_usdt"],
    ):
        raise NegativeBybitFlowError(
            "Bybit withdrawal fee changed before "
            "submit"
        )

    observed_precision = int(
        coin_info.min_accuracy
    )

    if (
        observed_precision
        != snapshot["amount_precision"]
    ):
        raise NegativeBybitFlowError(
            "Bybit withdrawal precision changed "
            "before submit"
        )

    observed_min = Decimal(
        coin_info.withdraw_min
    )

    if snapshot["amount_usdt"] < observed_min:
        raise NegativeBybitFlowError(
            "Withdrawal amount became lower than "
            "withdrawMin"
        )

    observed_max = (
        Decimal(coin_info.withdraw_max)
        if coin_info.withdraw_max is not None
        else None
    )

    if (
        observed_max is not None
        and observed_max > Decimal("0")
        and snapshot["amount_usdt"]
        > observed_max
    ):
        raise NegativeBybitFlowError(
            "Withdrawal amount became higher than "
            "withdrawMax"
        )

    max_age_sec = int(
        settings
        .NEGATIVE_NET_WITHDRAWAL_FEE_MAX_AGE_SEC
    )

    checked_at_ms = int(
        checked_at.timestamp() * 1000
    )

    return {
        "schema": (
            "negative_withdrawal_fee_"
            "revalidation_v1"
        ),
        "checked_at": (
            checked_at.isoformat()
        ),
        "checked_at_ms": checked_at_ms,
        "valid_for_sec": max_age_sec,
        "valid_until_ms": (
            checked_at_ms
            + max_age_sec * 1000
        ),
        "coin": snapshot["coin"],
        "chain": snapshot["chain"],
        "withdraw_fee_usdt": (
            _decimal_text(observed_fee)
        ),
        "withdraw_min_usdt": (
            _decimal_text(observed_min)
        ),
        "withdraw_max_usdt": (
            _decimal_text(observed_max)
            if observed_max is not None
            else None
        ),
        "withdraw_percentage_fee": (
            _decimal_text(
                Decimal(percentage_fee)
            )
            if percentage_fee is not None
            else None
        ),
        "min_accuracy": (
            observed_precision
        ),
        "chain_withdraw": (
            coin_info.chain_withdraw
        ),
        "matches_prepared_snapshot": True,
    }


def _locked_withdrawal_context(
    db: Session,
    *,
    settlement_batch_id: int,
):
    settlement_batch = _lock_settlement_batch(
        db,
        settlement_batch_id=int(
            settlement_batch_id
        ),
    )

    sale_batch = (
        _lock_sale_batch_for_settlement(
            db,
            settlement_batch_id=int(
                settlement_batch_id
            ),
        )
    )

    flow = _lock_existing_flow(
        db,
        settlement_batch_id=int(
            settlement_batch_id
        ),
    )

    if flow is None:
        raise NegativeBybitFlowError(
            "Negative Bybit flow disappeared "
            "during withdrawal submit"
        )

    _validate_sale_batch_input(
        settlement_batch=settlement_batch,
        sale_batch=sale_batch,
    )

    amounts = _validate_target_fields(
        settlement_batch=settlement_batch,
        sale_batch=sale_batch,
    )

    _validate_existing_flow(
        flow=flow,
        settlement_batch=settlement_batch,
        sale_batch=sale_batch,
        amounts=amounts,
    )

    return (
        settlement_batch,
        sale_batch,
        flow,
    )


def _merge_failure_reconciliation(
    *,
    flow: FundNegativeBybitFlow,
    prior_reconciliation: (
        dict[str, Any] | None
    ),
) -> None:
    if prior_reconciliation is None:
        return

    failure_reconciliation = (
        deepcopy(flow.reconciliation_json)
        if isinstance(
            flow.reconciliation_json,
            dict,
        )
        else {}
    )

    merged = deepcopy(
        prior_reconciliation
    )
    merged.update(
        failure_reconciliation
    )

    flow.reconciliation_json = _json_dict(
        merged
    )


def _mark_withdrawal_guard_blocked(
    db: Session,
    *,
    settlement_batch_id: int,
    snapshot: dict[str, Any],
    claim_token: str,
    error: BaseException,
    now: datetime,
) -> NegativeBybitFlowResult:
    settlement_batch, _, flow = (
        _locked_withdrawal_context(
            db,
            settlement_batch_id=(
                settlement_batch_id
            ),
        )
    )

    current_intent = deepcopy(
        flow.withdrawal_intent_json
    )

    if not isinstance(current_intent, dict):
        raise NegativeBybitFlowError(
            "Withdrawal intent missing after "
            "Operation Guard"
        )

    _validate_withdrawal_snapshot_unchanged(
        flow=flow,
        intent=current_intent,
        snapshot=snapshot,
        allowed_states={"submitting"},
    )

    if not _withdrawal_claim_matches(
        intent=current_intent,
        claim_token=claim_token,
    ):
        raise NegativeBybitFlowError(
            "Withdrawal submit claim ownership "
            "mismatch"
        )

    prior_reconciliation = (
        deepcopy(flow.reconciliation_json)
        if isinstance(
            flow.reconciliation_json,
            dict,
        )
        else None
    )

    current_intent["state"] = (
        "failed_requires_review"
    )
    current_intent["acknowledgement"] = {
        "outcome": "guard_blocked",
        "claim_token": claim_token,
        "acknowledged_at": now.isoformat(),
        "error": _bounded_external_error(
            error
        ),
        "bybit_post_performed": False,
        "no_automatic_resend": True,
    }

    flow.withdrawal_intent_json = (
        current_intent
    )
    flow.withdrawal_status = (
        "GUARD_BLOCKED"
    )

    result = _set_failed(
        flow=flow,
        settlement_batch=settlement_batch,
        fund=None,
        status_before=(
            BYBIT_FLOW_STATUS_WITHDRAWAL_SUBMITTING
        ),
        settlement_status_before=str(
            settlement_batch.status
        ),
        error=(
            "Operation Guard blocked Bybit "
            f"master withdrawal: {error}"
        ),
        now=now,
        diagnostics={
            "transition": (
                "submit_withdrawal_guard_blocked"
            ),
            "did_bybit_post": False,
            "bybit_post_count": 0,
            "no_automatic_resend": True,
            "payload_fingerprint": snapshot[
                "payload_fingerprint"
            ],
            "reserve_release_allowed": False,
            "pricing_unlock_allowed": False,
        },
    )

    _merge_failure_reconciliation(
        flow=flow,
        prior_reconciliation=(
            prior_reconciliation
        ),
    )

    db.add(flow)
    db.add(settlement_batch)
    db.flush()
    db.commit()

    return result


def _mark_withdrawal_submit_unknown(
    db: Session,
    *,
    settlement_batch_id: int,
    snapshot: dict[str, Any],
    claim_token: str,
    error: BaseException,
    now: datetime,
) -> NegativeBybitFlowResult:
    settlement_batch, _, flow = (
        _locked_withdrawal_context(
            db,
            settlement_batch_id=(
                settlement_batch_id
            ),
        )
    )

    current_intent = deepcopy(
        flow.withdrawal_intent_json
    )

    if not isinstance(current_intent, dict):
        raise NegativeBybitFlowError(
            "Withdrawal intent missing after "
            "POST attempt"
        )

    _validate_withdrawal_snapshot_unchanged(
        flow=flow,
        intent=current_intent,
        snapshot=snapshot,
        allowed_states={"submitting"},
    )

    if not _withdrawal_claim_matches(
        intent=current_intent,
        claim_token=claim_token,
    ):
        raise NegativeBybitFlowError(
            "Withdrawal submit claim ownership "
            "mismatch"
        )

    current_intent["state"] = "reconciling"
    current_intent["acknowledgement"] = {
        "outcome": "unknown",
        "claim_token": claim_token,
        "acknowledged_at": now.isoformat(),
        "error": _bounded_external_error(
            error
        ),
        "bybit_post_performed": True,
        "no_automatic_resend": True,
    }

    flow.withdrawal_intent_json = (
        current_intent
    )
    flow.withdrawal_status = "UNKNOWN"
    flow.status = (
        BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILING
    )
    flow.error = None
    flow.updated_at = now

    settlement_batch.status = (
        BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_RECONCILING
    )
    settlement_batch.error = None
    settlement_batch.updated_at = now

    db.add(flow)
    db.add(settlement_batch)
    db.flush()

    result = _step_result(
        ok=False,
        transition=(
            "submit_withdrawal_unknown"
        ),
        settlement_batch=settlement_batch,
        flow=flow,
        status_before=(
            BYBIT_FLOW_STATUS_WITHDRAWAL_SUBMITTING
        ),
        settlement_status_before=str(
            settlement_batch.status
        ),
        diagnostics={
            "pending": (
                "withdrawal_reconciliation"
            ),
            "did_bybit_post": True,
            "bybit_post_count": 1,
            "no_automatic_resend": True,
            "payload_fingerprint": snapshot[
                "payload_fingerprint"
            ],
            "next_transition": (
                "reconcile_withdrawal"
            ),
        },
    )

    db.commit()

    return result


def _mark_withdrawal_ack_mismatch(
    db: Session,
    *,
    settlement_batch,
    flow: FundNegativeBybitFlow,
    current_intent: dict[str, Any],
    snapshot: dict[str, Any],
    claim_token: str,
    guard_event_id: int | None,
    created_withdrawal,
    error: BaseException,
    now: datetime,
) -> NegativeBybitFlowResult:
    prior_reconciliation = (
        deepcopy(flow.reconciliation_json)
        if isinstance(
            flow.reconciliation_json,
            dict,
        )
        else None
    )

    current_intent["state"] = (
        "failed_requires_review"
    )
    current_intent["acknowledgement"] = {
        "outcome": "mismatch",
        "claim_token": claim_token,
        "guard_event_id": guard_event_id,
        "acknowledged_at": now.isoformat(),
        "expected": {
            "request_id": snapshot[
                "request_id"
            ],
            "coin": snapshot["coin"],
            "chain": snapshot["chain"],
            "address": snapshot["address"],
            "amount_usdt": _decimal_text(
                snapshot["amount_usdt"]
            ),
            "fee_type": snapshot["fee_type"],
        },
        "observed": (
            _withdrawal_ack_snapshot(
                created_withdrawal
            )
        ),
        "response": _json_dict(
            created_withdrawal.raw
        ),
        "error": _bounded_external_error(
            error
        ),
        "bybit_post_performed": True,
        "no_automatic_resend": True,
    }

    flow.withdrawal_intent_json = (
        current_intent
    )
    flow.withdrawal_id = (
        created_withdrawal.withdrawal_id
    )
    flow.withdrawal_status = (
        created_withdrawal.status
    )
    flow.withdrawal_tx_hash = (
        created_withdrawal.tx_hash
    )
    flow.withdrawal_created_at = now

    result = _set_failed(
        flow=flow,
        settlement_batch=settlement_batch,
        fund=None,
        status_before=(
            BYBIT_FLOW_STATUS_WITHDRAWAL_SUBMITTING
        ),
        settlement_status_before=str(
            settlement_batch.status
        ),
        error=(
            "Withdrawal acknowledgement mismatch "
            f"after POST: {error}"
        ),
        now=now,
        diagnostics={
            "transition": (
                "submit_withdrawal_ack_mismatch"
            ),
            "did_bybit_post": True,
            "bybit_post_count": 1,
            "no_automatic_resend": True,
            "payload_fingerprint": snapshot[
                "payload_fingerprint"
            ],
            "guard_event_id": guard_event_id,
            "acknowledgement_mismatch": True,
            "reserve_release_allowed": False,
            "pricing_unlock_allowed": False,
        },
    )

    _merge_failure_reconciliation(
        flow=flow,
        prior_reconciliation=(
            prior_reconciliation
        ),
    )

    db.add(flow)
    db.add(settlement_batch)
    db.flush()
    db.commit()

    return result


def _submit_withdrawal_once(
    db: Session,
    *,
    settlement_batch,
    flow: FundNegativeBybitFlow,
    bybit_client: BybitV5Client,
    resolved_now: datetime,
    status_before: str | None,
    settlement_status_before: str | None,
) -> NegativeBybitFlowResult:
    intent = deepcopy(
        flow.withdrawal_intent_json
    )

    if not isinstance(intent, dict):
        raise NegativeBybitFlowError(
            "Withdrawal intent is missing"
        )

    if str(flow.status) != (
        BYBIT_FLOW_STATUS_WITHDRAWAL_INTENT_PREPARED
    ):
        raise NegativeBybitFlowError(
            "Withdrawal submit requires prepared "
            "intent status"
        )

    _require_single_post_client(
        bybit_client
    )

    snapshot = _withdrawal_intent_snapshot(
        flow=flow,
        intent=intent,
        allowed_states={"prepared"},
    )

    barrier_snapshot = (
        _confirmed_master_balance_barrier(
            flow
        )
    )

    wallet = _get_active_settlement_wallet(
        db,
        fund_id=int(flow.fund_id),
    )

    if int(wallet.id) != snapshot[
        "settlement_wallet_id"
    ]:
        raise NegativeBybitFlowError(
            "Active settlement wallet ID mismatch "
            "before withdrawal submit"
        )

    if (
        str(wallet.address).strip().lower()
        != snapshot["address"].lower()
    ):
        raise NegativeBybitFlowError(
            "Active settlement wallet address "
            "mismatch before withdrawal submit"
        )

    settlement_batch_id = int(
        settlement_batch.id
    )

    expected_flow_id = int(flow.id)

    # Release all FOR UPDATE locks before the
    # fresh Bybit fee GET.
    db.commit()

    coin_info = query_coin_info(
        bybit_client,
        coin=snapshot["coin"],
        chain=snapshot["chain"],
    )

    fee_revalidation = (
        _validate_fresh_withdrawal_fee(
            snapshot=snapshot,
            coin_info=coin_info,
            checked_at=resolved_now,
        )
    )

    (
        settlement_batch,
        _,
        flow,
    ) = _locked_withdrawal_context(
        db,
        settlement_batch_id=(
            settlement_batch_id
        ),
    )

    if int(flow.id) != expected_flow_id:
        raise NegativeBybitFlowError(
            "Negative Bybit flow identity changed "
            "during withdrawal fee revalidation"
        )

    current_intent = deepcopy(
        flow.withdrawal_intent_json
    )

    if not isinstance(current_intent, dict):
        raise NegativeBybitFlowError(
            "Withdrawal intent disappeared before "
            "claim"
        )

    _validate_withdrawal_snapshot_unchanged(
        flow=flow,
        intent=current_intent,
        snapshot=snapshot,
        allowed_states={"prepared"},
    )

    if (
        _confirmed_master_balance_barrier(flow)
        != barrier_snapshot
    ):
        raise NegativeBybitFlowError(
            "Master balance barrier changed during "
            "withdrawal submit"
        )

    current_wallet = (
        _get_active_settlement_wallet(
            db,
            fund_id=int(flow.fund_id),
        )
    )

    if int(current_wallet.id) != snapshot[
        "settlement_wallet_id"
    ]:
        raise NegativeBybitFlowError(
            "Active settlement wallet changed "
            "during withdrawal submit"
        )

    if (
        str(current_wallet.address)
        .strip()
        .lower()
        != snapshot["address"].lower()
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet address changed "
            "during withdrawal submit"
        )

    if (
        current_intent.get("submit_claim")
        is not None
    ):
        db.commit()

        return _step_result(
            ok=False,
            transition=(
                "submit_withdrawal_claim_"
                "already_exists"
            ),
            settlement_batch=settlement_batch,
            flow=flow,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            idempotent=True,
            diagnostics={
                "pending": (
                    "withdrawal_reconciliation"
                ),
                "did_bybit_post": False,
                "bybit_post_count": 0,
                "bybit_get_count": 1,
                "no_automatic_resend": True,
                "next_transition": (
                    "reconcile_withdrawal"
                ),
            },
        )

    claim_token = str(uuid4())
    timestamp_ms = int(
        resolved_now.timestamp() * 1000
    )

    current_intent["state"] = "submitting"
    current_intent["submit_claim"] = {
        "claim_token": claim_token,
        "claimed_at": (
            resolved_now.isoformat()
        ),
        "submit_attempt_number": 1,
        "timestamp_ms": timestamp_ms,
        "fee_revalidation": (
            fee_revalidation
        ),
        "no_automatic_resend": True,
    }

    flow.withdrawal_intent_json = (
        current_intent
    )
    flow.withdrawal_submitted_at = (
        resolved_now
    )
    flow.status = (
        BYBIT_FLOW_STATUS_WITHDRAWAL_SUBMITTING
    )
    flow.error = None
    flow.updated_at = resolved_now

    settlement_batch.status = (
        BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_PENDING
    )
    settlement_batch.error = None
    settlement_batch.updated_at = (
        resolved_now
    )

    db.add(flow)
    db.add(settlement_batch)
    db.flush()

    # Durable claim boundary.
    # After this commit no automatic resend is
    # permitted, including after process crash.
    db.commit()

    try:
        guard_decision = (
            require_bybit_master_withdrawal_guard(
                db,
                fund_id=snapshot["fund_id"],
                settlement_batch_id=(
                    settlement_batch_id
                ),
                amount_usdt=snapshot[
                    "amount_usdt"
                ],
                request_id=snapshot[
                    "request_id"
                ],
                metadata={
                    "source": (
                        "negative_bybit_flow_"
                        "live_service"
                    ),
                    "intent_schema": (
                        WITHDRAWAL_INTENT_SCHEMA
                    ),
                    "intent_state": "submitting",
                    "claim_token": claim_token,
                    "payload_fingerprint": snapshot[
                        "payload_fingerprint"
                    ],
                    "coin": snapshot["coin"],
                    "chain": snapshot["chain"],
                    "address": snapshot[
                        "address"
                    ],
                    "account_type": snapshot[
                        "account_type"
                    ],
                    "fee_type": snapshot[
                        "fee_type"
                    ],
                    "force_chain": snapshot[
                        "force_chain"
                    ],
                    "fee_revalidated": True,
                },
            )
        )

        # Persist Guard audit and release its
        # transaction before the HTTP POST.
        db.commit()

    except OperationGuardBlockedError as exc:
        db.commit()

        return _mark_withdrawal_guard_blocked(
            db,
            settlement_batch_id=(
                settlement_batch_id
            ),
            snapshot=snapshot,
            claim_token=claim_token,
            error=exc,
            now=resolved_now,
        )

    try:
        created_withdrawal = (
            create_master_withdrawal(
                bybit_client,
                request_id=snapshot[
                    "request_id"
                ],
                coin=snapshot["coin"],
                chain=snapshot["chain"],
                address=snapshot["address"],
                amount_usdt=snapshot[
                    "amount_usdt"
                ],
                amount_str=snapshot[
                    "amount_text"
                ],
                amount_precision=snapshot[
                    "amount_precision"
                ],
                fee_type=snapshot[
                    "fee_type"
                ],
                account_type=snapshot[
                    "account_type"
                ],
                timestamp_ms=timestamp_ms,
                force_chain=snapshot[
                    "force_chain"
                ],
            )
        )

    except (
        BybitApiError,
        BybitAssetFlowError,
    ) as exc:
        return _mark_withdrawal_submit_unknown(
            db,
            settlement_batch_id=(
                settlement_batch_id
            ),
            snapshot=snapshot,
            claim_token=claim_token,
            error=exc,
            now=resolved_now,
        )

    (
        settlement_batch,
        _,
        flow,
    ) = _locked_withdrawal_context(
        db,
        settlement_batch_id=(
            settlement_batch_id
        ),
    )

    current_intent = deepcopy(
        flow.withdrawal_intent_json
    )

    if not isinstance(current_intent, dict):
        raise NegativeBybitFlowError(
            "Withdrawal intent missing after POST "
            "acknowledgement"
        )

    _validate_withdrawal_snapshot_unchanged(
        flow=flow,
        intent=current_intent,
        snapshot=snapshot,
        allowed_states={"submitting"},
    )

    if not _withdrawal_claim_matches(
        intent=current_intent,
        claim_token=claim_token,
    ):
        raise NegativeBybitFlowError(
            "Withdrawal submit claim ownership "
            "mismatch"
        )

    try:
        _validate_exact_withdrawal_ack(
            record=created_withdrawal,
            snapshot=snapshot,
        )
    except NegativeBybitFlowError as exc:
        return _mark_withdrawal_ack_mismatch(
            db,
            settlement_batch=(
                settlement_batch
            ),
            flow=flow,
            current_intent=current_intent,
            snapshot=snapshot,
            claim_token=claim_token,
            guard_event_id=(
                guard_decision.event_id
            ),
            created_withdrawal=(
                created_withdrawal
            ),
            error=exc,
            now=resolved_now,
        )

    current_intent["state"] = "reconciling"
    current_intent["acknowledgement"] = {
        "outcome": "accepted",
        "claim_token": claim_token,
        "guard_event_id": (
            guard_decision.event_id
        ),
        "acknowledged_at": (
            resolved_now.isoformat()
        ),
        "request_id": (
            created_withdrawal.request_id
        ),
        "withdrawal_id": (
            created_withdrawal.withdrawal_id
        ),
        "status": (
            created_withdrawal.status
        ),
        "tx_hash": (
            created_withdrawal.tx_hash
        ),
        "response": _json_dict(
            created_withdrawal.raw
        ),
        "bybit_post_performed": True,
        "no_automatic_resend": True,
    }

    flow.withdrawal_intent_json = (
        current_intent
    )
    flow.withdrawal_id = (
        created_withdrawal.withdrawal_id
    )
    flow.withdrawal_status = (
        created_withdrawal.status
    )
    flow.withdrawal_amount_usdt = (
        snapshot["amount_usdt"]
    )
    flow.withdrawal_fee_usdt = (
        snapshot["fee_usdt"]
    )
    flow.withdrawal_coin = snapshot["coin"]
    flow.withdrawal_chain = snapshot["chain"]
    flow.withdrawal_address = (
        snapshot["address"]
    )
    flow.withdrawal_tx_hash = (
        created_withdrawal.tx_hash
    )
    flow.withdrawal_created_at = (
        resolved_now
    )
    flow.status = (
        BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILING
    )
    flow.error = None
    flow.updated_at = resolved_now

    settlement_batch.status = (
        BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_RECONCILING
    )
    settlement_batch.error = None
    settlement_batch.updated_at = (
        resolved_now
    )

    db.add(flow)
    db.add(settlement_batch)
    db.flush()

    result = _step_result(
        ok=True,
        transition="submit_withdrawal",
        settlement_batch=settlement_batch,
        flow=flow,
        status_before=status_before,
        settlement_status_before=(
            settlement_status_before
        ),
        diagnostics={
            "did_bybit_post": True,
            "bybit_post_count": 1,
            "bybit_get_count": 1,
            "guard_event_id": (
                guard_decision.event_id
            ),
            "claim_token": claim_token,
            "payload_fingerprint": snapshot[
                "payload_fingerprint"
            ],
            "no_automatic_resend": True,
            "next_transition": (
                "reconcile_withdrawal"
            ),
        },
    )

    db.commit()

    return result


def _withdrawal_record_evidence(
    record,
) -> dict[str, Any]:
    raw = (
        record.raw
        if isinstance(record.raw, dict)
        else {
            "value": str(record.raw),
        }
    )

    canonical_raw = json.dumps(
        raw,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
        allow_nan=False,
    )

    return {
        "request_id": (
            str(
                record.request_id
            ).strip()
            if record.request_id
            else None
        ),
        "withdrawal_id": (
            str(record.withdrawal_id).strip()
            if record.withdrawal_id
            else None
        ),
        "coin": str(
            record.coin or ""
        ).strip().upper(),
        "chain": str(
            record.chain or ""
        ).strip().upper(),
        "address": str(
            record.address or ""
        ).strip(),
        "amount_usdt": _decimal_text(
            Decimal(record.amount_usdt)
        ),
        "fee_usdt": (
            _decimal_text(
                Decimal(
                    record.fee_usdt
                )
            )
            if record.fee_usdt
            is not None
            else None
        ),
        "fee_type": int(record.fee_type),
        "status": str(
            record.status or ""
        ).strip(),
        "tx_hash": (
            str(record.tx_hash).strip()
            if record.tx_hash
            else None
        ),
        "created_time_ms": (
            int(
                record.created_time_ms
            )
            if record.created_time_ms
            is not None
            else None
        ),
        "record_fingerprint": (
            _withdrawal_record_fingerprint(
                record
            )
        ),
        "raw_sha256": hashlib.sha256(
            canonical_raw.encode("utf-8")
        ).hexdigest(),
        "raw_keys": sorted(
            str(key)
            for key in raw.keys()
        )[:50],
        "raw_omitted": True,
    }


def _validate_same_withdrawal_identity(
    *,
    left,
    right,
    snapshot: dict[str, Any],
) -> None:
    _validate_exact_withdrawal_ack(
        record=left,
        snapshot=snapshot,
    )

    _validate_exact_withdrawal_ack(
        record=right,
        snapshot=snapshot,
    )

    left_id = str(
        left.withdrawal_id or ""
    ).strip()

    right_id = str(
        right.withdrawal_id or ""
    ).strip()

    if (
        left_id
        and right_id
        and left_id != right_id
    ):
        raise NegativeBybitFlowError(
            "Exact and bounded withdrawal records "
            "have different withdrawal IDs"
        )


def _apply_withdrawal_record(
    *,
    flow: FundNegativeBybitFlow,
    record,
    snapshot: dict[str, Any],
    observed_at: datetime,
) -> None:
    flow.withdrawal_id = (
        record.withdrawal_id
        or flow.withdrawal_id
    )

    flow.withdrawal_status = str(
        record.status or ""
    ).strip()

    flow.withdrawal_amount_usdt = (
        snapshot["amount_usdt"]
    )
    flow.withdrawal_fee_usdt = (
        snapshot["fee_usdt"]
    )
    flow.withdrawal_coin = snapshot["coin"]
    flow.withdrawal_chain = snapshot["chain"]
    flow.withdrawal_address = (
        snapshot["address"]
    )

    if record.tx_hash:
        flow.withdrawal_tx_hash = str(
            record.tx_hash
        ).strip()

    if flow.withdrawal_created_at is None:
        flow.withdrawal_created_at = observed_at


def _concurrent_withdrawal_reconciliation_result(
    *,
    settlement_batch,
    flow: FundNegativeBybitFlow,
    status_before: str | None,
    settlement_status_before: str | None,
) -> NegativeBybitFlowResult | None:
    intent = flow.withdrawal_intent_json

    if not isinstance(intent, dict):
        return None

    state = str(
        intent.get("state") or ""
    ).strip()

    if state == "confirmed":
        _validate_withdrawal_intent(
            flow=flow,
            intent=intent,
            allowed_states={"confirmed"},
        )

        if str(flow.status) != (
            BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILED
        ):
            raise NegativeBybitFlowError(
                "Confirmed withdrawal intent has "
                "incompatible flow status"
            )

        return _step_result(
            ok=True,
            transition=(
                "withdrawal_reconciliation_"
                "concurrent_confirmed"
            ),
            settlement_batch=settlement_batch,
            flow=flow,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            idempotent=True,
            diagnostics={
                "did_bybit_post": False,
                "bybit_post_count": 0,
                "bybit_get_count": 0,
                "no_automatic_resend": True,
                "next_transition": (
                    "reconcile_settlement_wallet_"
                    "receipt"
                ),
            },
        )

    if state == "failed_requires_review":
        if str(flow.status) != (
            BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
        ):
            raise NegativeBybitFlowError(
                "Failed withdrawal intent has "
                "incompatible flow status"
            )

        return _step_result(
            ok=False,
            transition=(
                "failed_requires_review_"
                "already_recorded"
            ),
            settlement_batch=settlement_batch,
            flow=flow,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            idempotent=True,
            error=flow.error,
            diagnostics={
                "did_bybit_post": False,
                "bybit_post_count": 0,
                "bybit_get_count": 0,
                "no_automatic_resend": True,
                "reserve_release_allowed": False,
                "pricing_unlock_allowed": False,
            },
        )

    return None


def _persist_withdrawal_reconciliation_pending(
    db: Session,
    *,
    settlement_batch,
    flow: FundNegativeBybitFlow,
    current_intent: dict[str, Any],
    evidence: dict[str, Any],
    transition: str,
    resolved_now: datetime,
    status_before: str | None,
    settlement_status_before: str | None,
    bybit_get_count: int,
    record=None,
) -> NegativeBybitFlowResult:
    current_intent["state"] = "reconciling"
    current_intent["reconciliation"] = evidence

    flow.withdrawal_intent_json = (
        current_intent
    )

    if record is not None:
        _apply_withdrawal_record(
            flow=flow,
            record=record,
            snapshot=(
                _withdrawal_intent_snapshot(
                    flow=flow,
                    intent=current_intent,
                    allowed_states={
                        "reconciling"
                    },
                )
            ),
            observed_at=resolved_now,
        )

        flow.withdrawal_record_json = (
            _json_dict(
                _withdrawal_record_evidence(
                    record
                )
            )
        )

    flow.withdrawal_reconciliation_json = (
        _json_dict(evidence)
    )

    flow.status = (
        BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILING
    )
    flow.error = None
    flow.updated_at = resolved_now

    settlement_batch.status = (
        BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_RECONCILING
    )
    settlement_batch.error = None
    settlement_batch.updated_at = resolved_now

    db.add(flow)
    db.add(settlement_batch)
    db.flush()

    result = _step_result(
        ok=False,
        transition=transition,
        settlement_batch=settlement_batch,
        flow=flow,
        status_before=status_before,
        settlement_status_before=(
            settlement_status_before
        ),
        diagnostics={
            "pending": (
                "withdrawal_reconciliation"
            ),
            "did_bybit_post": False,
            "bybit_post_count": 0,
            "bybit_get_count": bybit_get_count,
            "no_automatic_resend": True,
            "reserve_release_allowed": False,
            "pricing_unlock_allowed": False,
            "next_transition": (
                "reconcile_withdrawal"
            ),
        },
    )

    db.commit()

    return result


def _fail_withdrawal_reconciliation(
    db: Session,
    *,
    settlement_batch,
    flow: FundNegativeBybitFlow,
    current_intent: dict[str, Any],
    evidence: dict[str, Any],
    error: str,
    transition: str,
    resolved_now: datetime,
    status_before: str | None,
    settlement_status_before: str | None,
    bybit_get_count: int,
    record=None,
) -> NegativeBybitFlowResult:
    prior_reconciliation = (
        deepcopy(flow.reconciliation_json)
        if isinstance(
            flow.reconciliation_json,
            dict,
        )
        else None
    )

    failed_evidence = deepcopy(evidence)
    failed_evidence["state"] = (
        "failed_requires_review"
    )
    failed_evidence["error"] = str(error)[:500]

    current_intent["state"] = (
        "failed_requires_review"
    )
    current_intent["reconciliation"] = (
        failed_evidence
    )

    flow.withdrawal_intent_json = (
        current_intent
    )

    if record is not None:
        _apply_withdrawal_record(
            flow=flow,
            record=record,
            snapshot=(
                _withdrawal_intent_snapshot(
                    flow=flow,
                    intent=current_intent,
                    allowed_states={
                        "failed_requires_review"
                    },
                )
            ),
            observed_at=resolved_now,
        )

        flow.withdrawal_record_json = (
            _json_dict(
                _withdrawal_record_evidence(
                    record
                )
            )
        )

    flow.withdrawal_reconciliation_json = (
        _json_dict(failed_evidence)
    )

    result = _set_failed(
        flow=flow,
        settlement_batch=settlement_batch,
        fund=None,
        status_before=status_before,
        settlement_status_before=(
            settlement_status_before
        ),
        error=error,
        now=resolved_now,
        diagnostics={
            "transition": transition,
            "did_bybit_post": False,
            "bybit_post_count": 0,
            "bybit_get_count": bybit_get_count,
            "no_automatic_resend": True,
            "reserve_release_allowed": False,
            "pricing_unlock_allowed": False,
        },
    )

    _merge_failure_reconciliation(
        flow=flow,
        prior_reconciliation=(
            prior_reconciliation
        ),
    )

    db.add(flow)
    db.add(settlement_batch)
    db.flush()
    db.commit()

    return result


def _reconcile_withdrawal_once(
    db: Session,
    *,
    settlement_batch,
    flow: FundNegativeBybitFlow,
    bybit_client: BybitV5Client,
    resolved_now: datetime,
    status_before: str | None,
    settlement_status_before: str | None,
) -> NegativeBybitFlowResult:
    intent = deepcopy(
        flow.withdrawal_intent_json
    )

    if not isinstance(intent, dict):
        raise NegativeBybitFlowError(
            "Withdrawal intent missing during "
            "reconciliation"
        )

    if str(flow.status) not in {
        BYBIT_FLOW_STATUS_WITHDRAWAL_SUBMITTING,
        BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILING,
    }:
        raise NegativeBybitFlowError(
            "Withdrawal reconciliation has "
            "incompatible flow status"
        )

    snapshot = _withdrawal_intent_snapshot(
        flow=flow,
        intent=intent,
        allowed_states={
            "submitting",
            "reconciling",
        },
    )

    barrier_snapshot = (
        _confirmed_master_balance_barrier(
            flow
        )
    )

    submitted_at = _aware_utc_datetime(
        flow.withdrawal_submitted_at,
        field_name=(
            "flow.withdrawal_submitted_at"
        ),
    )

    if resolved_now < submitted_at:
        raise NegativeBybitFlowError(
            "Withdrawal reconciliation time is "
            "before submit time"
        )

    lookback_hours = int(
        settings
        .NEGATIVE_NET_BYBIT_RECORD_LOOKBACK_HOURS
    )

    if lookback_hours <= 0:
        raise NegativeBybitFlowError(
            "NEGATIVE_NET_BYBIT_RECORD_LOOKBACK_HOURS "
            "must be positive"
        )

    max_pages = int(
        settings
        .NEGATIVE_NET_BYBIT_WITHDRAWAL_LOOKUP_MAX_PAGES
    )

    if max_pages <= 0:
        raise NegativeBybitFlowError(
            "NEGATIVE_NET_BYBIT_WITHDRAWAL_"
            "LOOKUP_MAX_PAGES must be positive"
        )

    max_pending_sec = int(
        settings
        .NEGATIVE_NET_BYBIT_WITHDRAWAL_RECONCILIATION_MAX_PENDING_SEC
    )

    if max_pending_sec <= 0:
        raise NegativeBybitFlowError(
            "NEGATIVE_NET_BYBIT_WITHDRAWAL_"
            "RECONCILIATION_MAX_PENDING_SEC must "
            "be positive"
        )

    lookup_start = (
        submitted_at
        - timedelta(hours=lookback_hours)
    )

    lookup_start_ms = int(
        lookup_start.timestamp() * 1000
    )

    lookup_end_ms = int(
        resolved_now.timestamp() * 1000
    )

    pending_age_sec = int(
        (
            resolved_now
            - submitted_at
        ).total_seconds()
    )

    settlement_batch_id = int(
        settlement_batch.id
    )

    expected_flow_id = int(
        flow.id
    )

    saved_withdrawal_id = str(
        flow.withdrawal_id or ""
    ).strip() or None

    saved_tx_hash = str(
        flow.withdrawal_tx_hash or ""
    ).strip() or None

    # Release all FOR UPDATE locks before
    # read-only Bybit requests.
    db.commit()

    recovery = _withdrawal_recovery_lookup(
        bybit_client=bybit_client,
        snapshot=snapshot,
        saved_withdrawal_id=(
            saved_withdrawal_id
        ),
        saved_tx_hash=saved_tx_hash,
        lookup_start_ms=lookup_start_ms,
        lookup_end_ms=lookup_end_ms,
        max_pages=max_pages,
    )

    bybit_get_count = int(
        recovery.get(
            "bybit_get_count"
        )
        or 0
    )

    (
        settlement_batch,
        _,
        flow,
    ) = _locked_withdrawal_context(
        db,
        settlement_batch_id=(
            settlement_batch_id
        ),
    )

    if int(flow.id) != expected_flow_id:
        raise NegativeBybitFlowError(
            "Negative Bybit flow identity changed "
            "during withdrawal reconciliation"
        )

    concurrent_result = (
        _concurrent_withdrawal_reconciliation_result(
            settlement_batch=(
                settlement_batch
            ),
            flow=flow,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
        )
    )

    if concurrent_result is not None:
        db.commit()
        return concurrent_result

    current_intent = deepcopy(
        flow.withdrawal_intent_json
    )

    if not isinstance(
        current_intent,
        dict,
    ):
        raise NegativeBybitFlowError(
            "Withdrawal intent disappeared during "
            "reconciliation"
        )

    _validate_withdrawal_snapshot_unchanged(
        flow=flow,
        intent=current_intent,
        snapshot=snapshot,
        allowed_states={
            "submitting",
            "reconciling",
        },
    )

    if (
        _confirmed_master_balance_barrier(
            flow
        )
        != barrier_snapshot
    ):
        raise NegativeBybitFlowError(
            "Master balance barrier changed during "
            "withdrawal reconciliation"
        )

    recovery_state = str(
        recovery.get("state") or ""
    ).strip()

    selected_source = (
        str(
            recovery.get(
                "selected_source"
            )
            or ""
        ).strip()
        or None
    )

    evidence: dict[str, Any] = {
        "schema": (
            WITHDRAWAL_RECONCILIATION_SCHEMA
        ),
        "state": "checking",
        "checked_at": (
            resolved_now.isoformat()
        ),
        "request_id": snapshot[
            "request_id"
        ],
        "lookup": {
            "start_time_ms": (
                lookup_start_ms
            ),
            "end_time_ms": (
                lookup_end_ms
            ),
            "limit": (
                WITHDRAWAL_RECORD_LOOKUP_LIMIT
            ),
            "max_pages": max_pages,
        },
        "recovery_state": (
            recovery_state
        ),
        "selected_source": (
            selected_source
        ),
        "unique_match": bool(
            recovery.get(
                "unique_match"
            )
        ),
        "ambiguous": bool(
            recovery.get("ambiguous")
        ),
        "exact_fingerprint_match": bool(
            recovery.get(
                "exact_fingerprint_match"
            )
        ),
        "record_fingerprint": (
            recovery.get(
                "record_fingerprint"
            )
        ),
        "matching_record_fingerprints": (
            deepcopy(
                recovery.get(
                    "matching_record_fingerprints"
                )
                or []
            )
        ),
        "queries": deepcopy(
            recovery.get("queries")
            or {}
        ),
        "pending_age_sec": (
            pending_age_sec
        ),
        "max_pending_sec": (
            max_pending_sec
        ),
        "bybit_get_count": (
            bybit_get_count
        ),
        "no_automatic_resend": True,
    }

    if recovery_state == "query_error":
        evidence["state"] = (
            "query_pending"
        )

        evidence["query_error"] = str(
            recovery.get("error")
            or "Bybit withdrawal query failed"
        )[:500]

        if pending_age_sec >= max_pending_sec:
            return _fail_withdrawal_reconciliation(
                db,
                settlement_batch=(
                    settlement_batch
                ),
                flow=flow,
                current_intent=(
                    current_intent
                ),
                evidence=evidence,
                error=(
                    "Withdrawal reconciliation "
                    "exceeded maximum pending time "
                    "after Bybit query errors"
                ),
                transition=(
                    "reconcile_withdrawal_"
                    "query_timeout"
                ),
                resolved_now=resolved_now,
                status_before=status_before,
                settlement_status_before=(
                    settlement_status_before
                ),
                bybit_get_count=(
                    bybit_get_count
                ),
            )

        return (
            _persist_withdrawal_reconciliation_pending(
                db,
                settlement_batch=(
                    settlement_batch
                ),
                flow=flow,
                current_intent=(
                    current_intent
                ),
                evidence=evidence,
                transition=(
                    "reconcile_withdrawal_"
                    "query_pending"
                ),
                resolved_now=resolved_now,
                status_before=status_before,
                settlement_status_before=(
                    settlement_status_before
                ),
                bybit_get_count=(
                    bybit_get_count
                ),
            )
        )

    if recovery_state == "lookup_incomplete":
        evidence["state"] = (
            "lookup_incomplete"
        )

        return _fail_withdrawal_reconciliation(
            db,
            settlement_batch=settlement_batch,
            flow=flow,
            current_intent=current_intent,
            evidence=evidence,
            error=str(
                recovery.get("error")
                or (
                    "Withdrawal lookup did not "
                    "exhaust all pages"
                )
            ),
            transition=(
                "reconcile_withdrawal_"
                "lookup_incomplete"
            ),
            resolved_now=resolved_now,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            bybit_get_count=(
                bybit_get_count
            ),
        )

    if recovery_state == "ambiguous":
        evidence["state"] = "ambiguous"

        return _fail_withdrawal_reconciliation(
            db,
            settlement_batch=settlement_batch,
            flow=flow,
            current_intent=current_intent,
            evidence=evidence,
            error=str(
                recovery.get("error")
                or (
                    "Multiple Bybit withdrawal "
                    "records match immutable intent"
                )
            ),
            transition=(
                "reconcile_withdrawal_ambiguous"
            ),
            resolved_now=resolved_now,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            bybit_get_count=(
                bybit_get_count
            ),
        )

    if recovery_state == "record_mismatch":
        evidence["state"] = (
            "record_mismatch"
        )

        return _fail_withdrawal_reconciliation(
            db,
            settlement_batch=settlement_batch,
            flow=flow,
            current_intent=current_intent,
            evidence=evidence,
            error=str(
                recovery.get("error")
                or (
                    "Bybit withdrawal record "
                    "immutable fingerprint mismatch"
                )
            ),
            transition=(
                "reconcile_withdrawal_mismatch"
            ),
            resolved_now=resolved_now,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            bybit_get_count=(
                bybit_get_count
            ),
        )

    if recovery_state == "record_not_found":
        evidence["state"] = (
            "record_not_found"
        )

        if pending_age_sec >= max_pending_sec:
            return _fail_withdrawal_reconciliation(
                db,
                settlement_batch=(
                    settlement_batch
                ),
                flow=flow,
                current_intent=(
                    current_intent
                ),
                evidence=evidence,
                error=(
                    "Bybit withdrawal record was "
                    "not found before reconciliation "
                    "timeout"
                ),
                transition=(
                    "reconcile_withdrawal_"
                    "record_not_found_timeout"
                ),
                resolved_now=resolved_now,
                status_before=status_before,
                settlement_status_before=(
                    settlement_status_before
                ),
                bybit_get_count=(
                    bybit_get_count
                ),
            )

        return (
            _persist_withdrawal_reconciliation_pending(
                db,
                settlement_batch=(
                    settlement_batch
                ),
                flow=flow,
                current_intent=(
                    current_intent
                ),
                evidence=evidence,
                transition=(
                    "reconcile_withdrawal_"
                    "record_not_found"
                ),
                resolved_now=resolved_now,
                status_before=status_before,
                settlement_status_before=(
                    settlement_status_before
                ),
                bybit_get_count=(
                    bybit_get_count
                ),
            )
        )

    if recovery_state != "unique_match":
        evidence["state"] = (
            "unsupported_recovery_state"
        )

        return _fail_withdrawal_reconciliation(
            db,
            settlement_batch=settlement_batch,
            flow=flow,
            current_intent=current_intent,
            evidence=evidence,
            error=(
                "Unsupported withdrawal recovery "
                f"state: {recovery_state or 'empty'}"
            ),
            transition=(
                "reconcile_withdrawal_"
                "unsupported_recovery_state"
            ),
            resolved_now=resolved_now,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            bybit_get_count=(
                bybit_get_count
            ),
        )

    if selected_source not in {
        "withdrawal_id_query",
        "tx_hash_query",
        "bounded_record_lookup",
        "exact_request_id_query",
    }:
        evidence["state"] = (
            "invalid_selected_source"
        )

        return _fail_withdrawal_reconciliation(
            db,
            settlement_batch=settlement_batch,
            flow=flow,
            current_intent=current_intent,
            evidence=evidence,
            error=(
                "Withdrawal reconciliation selected "
                "source is invalid"
            ),
            transition=(
                "reconcile_withdrawal_"
                "invalid_selected_source"
            ),
            resolved_now=resolved_now,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            bybit_get_count=(
                bybit_get_count
            ),
        )

    if (
        not evidence["unique_match"]
        or evidence["ambiguous"]
        or not evidence[
            "exact_fingerprint_match"
        ]
    ):
        evidence["state"] = (
            "invalid_unique_match_evidence"
        )

        return _fail_withdrawal_reconciliation(
            db,
            settlement_batch=settlement_batch,
            flow=flow,
            current_intent=current_intent,
            evidence=evidence,
            error=(
                "Withdrawal recovery unique match "
                "evidence is invalid"
            ),
            transition=(
                "reconcile_withdrawal_"
                "invalid_unique_match"
            ),
            resolved_now=resolved_now,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            bybit_get_count=(
                bybit_get_count
            ),
        )

    selected_record = recovery.get(
        "selected_record"
    )

    if selected_record is None:
        evidence["state"] = (
            "selected_record_missing"
        )

        return _fail_withdrawal_reconciliation(
            db,
            settlement_batch=settlement_batch,
            flow=flow,
            current_intent=current_intent,
            evidence=evidence,
            error=(
                "Withdrawal recovery selected record "
                "is missing"
            ),
            transition=(
                "reconcile_withdrawal_"
                "selected_record_missing"
            ),
            resolved_now=resolved_now,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            bybit_get_count=(
                bybit_get_count
            ),
        )

    matched, mismatch_error = (
        _withdrawal_record_intent_match(
            record=selected_record,
            snapshot=snapshot,
            lookup_start_ms=(
                lookup_start_ms
            ),
            lookup_end_ms=(
                lookup_end_ms
            ),
        )
    )

    if not matched:
        evidence["state"] = (
            "record_mismatch"
        )

        return _fail_withdrawal_reconciliation(
            db,
            settlement_batch=settlement_batch,
            flow=flow,
            current_intent=current_intent,
            evidence=evidence,
            error=(
                mismatch_error
                or (
                    "Selected Bybit withdrawal "
                    "record does not match immutable "
                    "intent"
                )
            ),
            transition=(
                "reconcile_withdrawal_mismatch"
            ),
            resolved_now=resolved_now,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            bybit_get_count=(
                bybit_get_count
            ),
            record=selected_record,
        )

    record_evidence = (
        _withdrawal_record_evidence(
            selected_record
        )
    )

    evidence["record_fingerprint"] = (
        _withdrawal_record_fingerprint(
            selected_record
        )
    )

    evidence["record"] = (
        record_evidence
    )

    _apply_withdrawal_record(
        flow=flow,
        record=selected_record,
        snapshot=snapshot,
        observed_at=resolved_now,
    )

    flow.withdrawal_record_json = (
        _json_dict(record_evidence)
    )

    status = str(
        selected_record.status or ""
    ).strip()

    if _is_withdrawal_failed_like(
        status
    ):
        return _fail_withdrawal_reconciliation(
            db,
            settlement_batch=settlement_batch,
            flow=flow,
            current_intent=current_intent,
            evidence=evidence,
            error=(
                "Bybit withdrawal has failed "
                f"status: {status}"
            ),
            transition=(
                "reconcile_withdrawal_failed_status"
            ),
            resolved_now=resolved_now,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            bybit_get_count=(
                bybit_get_count
            ),
            record=selected_record,
        )

    if (
        _is_withdrawal_pending_like(
            status
        )
        or (
            _is_withdrawal_success_like(
                status
            )
            and not selected_record.tx_hash
        )
    ):
        evidence["state"] = "pending"

        evidence["pending_reason"] = (
            "success_like_missing_tx_hash"
            if _is_withdrawal_success_like(
                status
            )
            else "bybit_status_pending"
        )

        return (
            _persist_withdrawal_reconciliation_pending(
                db,
                settlement_batch=(
                    settlement_batch
                ),
                flow=flow,
                current_intent=(
                    current_intent
                ),
                evidence=evidence,
                transition=(
                    "reconcile_withdrawal_pending"
                ),
                resolved_now=resolved_now,
                status_before=status_before,
                settlement_status_before=(
                    settlement_status_before
                ),
                bybit_get_count=(
                    bybit_get_count
                ),
                record=selected_record,
            )
        )

    if not _is_withdrawal_success_like(
        status
    ):
        return _fail_withdrawal_reconciliation(
            db,
            settlement_batch=settlement_batch,
            flow=flow,
            current_intent=current_intent,
            evidence=evidence,
            error=(
                "Bybit withdrawal has unsupported "
                f"status: {status or 'empty'}"
            ),
            transition=(
                "reconcile_withdrawal_"
                "unsupported_status"
            ),
            resolved_now=resolved_now,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            bybit_get_count=(
                bybit_get_count
            ),
            record=selected_record,
        )

    if not selected_record.withdrawal_id:
        return _fail_withdrawal_reconciliation(
            db,
            settlement_batch=settlement_batch,
            flow=flow,
            current_intent=current_intent,
            evidence=evidence,
            error=(
                "Successful Bybit withdrawal record "
                "is missing withdrawal_id"
            ),
            transition=(
                "reconcile_withdrawal_"
                "missing_withdrawal_id"
            ),
            resolved_now=resolved_now,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            bybit_get_count=(
                bybit_get_count
            ),
            record=selected_record,
        )

    tx_hash = _required_text(
        selected_record.tx_hash,
        field_name=(
            "withdrawal_record.tx_hash"
        ),
    )

    evidence["state"] = "confirmed"
    evidence["tx_hash"] = tx_hash
    evidence["withdrawal_id"] = str(
        selected_record.withdrawal_id
    ).strip()
    evidence["next_transition"] = (
        "reconcile_settlement_wallet_receipt"
    )

    current_intent["state"] = "confirmed"
    current_intent["reconciliation"] = (
        evidence
    )

    flow.withdrawal_intent_json = (
        current_intent
    )

    flow.withdrawal_id = str(
        selected_record.withdrawal_id
    ).strip()

    flow.withdrawal_status = status
    flow.withdrawal_tx_hash = tx_hash

    flow.withdrawal_confirmed_at = (
        resolved_now
    )

    flow.withdrawal_reconciliation_json = (
        _json_dict(evidence)
    )

    flow.status = (
        BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILED
    )

    flow.error = None
    flow.updated_at = resolved_now

    settlement_batch.status = (
        BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_RECONCILING
    )

    settlement_batch.error = None
    settlement_batch.updated_at = (
        resolved_now
    )

    db.add(flow)
    db.add(settlement_batch)
    db.flush()

    result = _step_result(
        ok=True,
        transition=(
            "reconcile_withdrawal_confirmed"
        ),
        settlement_batch=settlement_batch,
        flow=flow,
        status_before=status_before,
        settlement_status_before=(
            settlement_status_before
        ),
        diagnostics={
            "did_bybit_post": False,
            "bybit_post_count": 0,
            "bybit_get_count": (
                bybit_get_count
            ),
            "no_automatic_resend": True,
            "selected_source": (
                selected_source
            ),
            "unique_match": True,
            "exact_fingerprint_match": True,
            "withdrawal_id": (
                flow.withdrawal_id
            ),
            "tx_hash": tx_hash,
            "reserve_release_allowed": False,
            "pricing_unlock_allowed": False,
            "next_transition": (
                "reconcile_settlement_wallet_"
                "receipt"
            ),
        },
    )

    db.commit()

    return result


def _rpc_value(
    value: Any,
    key: str,
    default: Any = None,
) -> Any:
    if value is None:
        return default

    if hasattr(value, "get"):
        return value.get(key, default)

    return getattr(value, key, default)


def _normalized_hex(
    value: Any,
) -> str | None:
    if value is None:
        return None

    if isinstance(value, int):
        text = hex(value)
    elif isinstance(
        value,
        (bytes, bytearray),
    ):
        text = value.hex()
    elif hasattr(value, "hex"):
        text = value.hex()
    else:
        text = str(value).strip()

    text = str(text).strip().lower()

    if not text:
        return None

    if not text.startswith("0x"):
        text = "0x" + text

    return text


def _rpc_integer(
    value: Any,
    *,
    field_name: str,
) -> int:
    if isinstance(value, int):
        return value

    if isinstance(
        value,
        (bytes, bytearray),
    ):
        return int.from_bytes(
            value,
            byteorder="big",
        )

    if hasattr(value, "hex"):
        text = str(value.hex()).strip()

        if not text.startswith("0x"):
            text = "0x" + text

        return int(text, 16)

    text = _required_text(
        value,
        field_name=field_name,
    )

    return int(
        text,
        16,
    ) if text.lower().startswith("0x") else int(
        text
    )


def _settlement_wallet_balance_snapshot(
    *,
    w3,
    address: str,
    block_number: int,
) -> dict[str, Any]:
    clean_address = _required_text(
        address,
        field_name="settlement_wallet_address",
    )

    contract_address = _required_text(
        settings.BSC_USDT_CONTRACT,
        field_name="BSC_USDT_CONTRACT",
    )

    decimals = int(
        settings.BSC_USDT_DECIMALS
    )

    if decimals < 0:
        raise NegativeBybitFlowError(
            "BSC_USDT_DECIMALS is invalid"
        )

    wallet_checksum = (
        w3.to_checksum_address(
            clean_address
        )
    )

    contract_checksum = (
        w3.to_checksum_address(
            contract_address
        )
    )

    contract = w3.eth.contract(
        address=contract_checksum,
        abi=ERC20_BALANCE_OF_ABI,
    )

    raw_balance = int(
        contract.functions.balanceOf(
            wallet_checksum
        ).call(
            block_identifier=int(
                block_number
            )
        )
    )

    if raw_balance < 0:
        raise NegativeBybitFlowError(
            "Settlement wallet USDT balance "
            "cannot be negative"
        )

    balance_usdt = (
        Decimal(raw_balance)
        / (
            Decimal("10")
            ** decimals
        )
    )

    return {
        "address": clean_address,
        "contract": contract_address,
        "block_number": int(
            block_number
        ),
        "decimals": decimals,
        "raw_balance": str(raw_balance),
        "balance_usdt": _decimal_text(
            balance_usdt
        ),
    }


def _receipt_pending_or_expired(
    *,
    evidence: dict[str, Any],
    error: str | None = None,
) -> dict[str, Any]:
    result = deepcopy(evidence)

    if error:
        result["pending_error"] = str(
            error
        )[:500]

    pending_age_sec = int(
        result["pending_age_sec"]
    )

    max_pending_sec = int(
        result["max_pending_sec"]
    )

    if pending_age_sec > max_pending_sec:
        result["state"] = (
            "failed_requires_review"
        )
        result["error"] = (
            "Settlement wallet receipt exceeded "
            "maximum pending time"
        )
    else:
        result["state"] = "pending"

    return result


def _query_settlement_wallet_receipt_observation(
    *,
    tx_hash: str,
    address: str,
    expected_amount_usdt: Decimal,
    balance_baseline: dict[str, Any],
    pending_started_at: datetime,
    checked_at: datetime,
) -> dict[str, Any]:
    clean_tx_hash = _required_text(
        tx_hash,
        field_name="withdrawal_tx_hash",
    )

    clean_address = _required_text(
        address,
        field_name="settlement_wallet_address",
    )

    contract_address = _required_text(
        settings.BSC_USDT_CONTRACT,
        field_name="BSC_USDT_CONTRACT",
    )

    decimals = int(
        settings.BSC_USDT_DECIMALS
    )

    required_confirmations = int(
        settings
        .NEGATIVE_NET_BSC_INTENT_CONFIRMATIONS_REQUIRED
    )

    max_pending_sec = int(
        settings
        .NEGATIVE_NET_BSC_INTENT_MAX_PENDING_SEC
    )

    if decimals < 0:
        raise NegativeBybitFlowError(
            "BSC_USDT_DECIMALS is invalid"
        )

    if required_confirmations <= 0:
        raise NegativeBybitFlowError(
            "NEGATIVE_NET_BSC_INTENT_CONFIRMATIONS_"
            "REQUIRED must be positive"
        )

    if max_pending_sec <= 0:
        raise NegativeBybitFlowError(
            "NEGATIVE_NET_BSC_INTENT_MAX_PENDING_SEC "
            "must be positive"
        )

    resolved_started_at = (
        pending_started_at.astimezone(
            timezone.utc
        )
    )

    resolved_checked_at = (
        checked_at.astimezone(
            timezone.utc
        )
    )

    if resolved_checked_at < resolved_started_at:
        raise NegativeBybitFlowError(
            "BSC receipt check time is before "
            "withdrawal confirmation time"
        )

    pending_age_sec = int(
        (
            resolved_checked_at
            - resolved_started_at
        ).total_seconds()
    )

    expected_amount = Decimal(
        expected_amount_usdt
    )

    if expected_amount <= Decimal("0"):
        raise NegativeBybitFlowError(
            "Expected settlement wallet receipt "
            "amount must be positive"
        )

    expected_raw_decimal = (
        expected_amount
        * (
            Decimal("10")
            ** decimals
        )
    )

    expected_raw = int(
        expected_raw_decimal
    )

    if (
        Decimal(expected_raw)
        != expected_raw_decimal
    ):
        raise NegativeBybitFlowError(
            "Expected withdrawal amount cannot be "
            "represented with configured USDT "
            "decimals"
        )

    baseline_address = _required_text(
        balance_baseline.get("address"),
        field_name=(
            "balance_baseline.address"
        ),
    )

    if (
        baseline_address.lower()
        != clean_address.lower()
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet baseline address "
            "mismatch"
        )

    baseline_contract = _required_text(
        balance_baseline.get("contract"),
        field_name=(
            "balance_baseline.contract"
        ),
    )

    if (
        baseline_contract.lower()
        != contract_address.lower()
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet baseline contract "
            "mismatch"
        )

    baseline_decimals = int(
        balance_baseline.get("decimals")
    )

    if baseline_decimals != decimals:
        raise NegativeBybitFlowError(
            "Settlement wallet baseline decimals "
            "mismatch"
        )

    baseline_block_number = int(
        balance_baseline.get(
            "block_number"
        )
    )

    baseline_raw_balance = int(
        _required_text(
            balance_baseline.get(
                "raw_balance"
            ),
            field_name=(
                "balance_baseline.raw_balance"
            ),
        )
    )

    evidence: dict[str, Any] = {
        "schema": (
            SETTLEMENT_WALLET_RECEIPT_SCHEMA
        ),
        "policy_version": (
            settings
            .NEGATIVE_NET_WITHDRAWAL_POLICY_VERSION
        ),
        "state": "checking",
        "checked_at": (
            resolved_checked_at.isoformat()
        ),
        "tx_hash": clean_tx_hash,
        "address": clean_address,
        "contract": contract_address,
        "expected_amount_usdt": (
            _decimal_text(expected_amount)
        ),
        "expected_amount_raw": str(
            expected_raw
        ),
        "required_confirmations": (
            required_confirmations
        ),
        "pending_age_sec": (
            pending_age_sec
        ),
        "max_pending_sec": (
            max_pending_sec
        ),
        "balance_before": deepcopy(
            balance_baseline
        ),
        "receipt_status": None,
        "receipt_block_number": None,
        "current_block_number": None,
        "confirmations": 0,
        "matched_transfer_log_count": 0,
        "matched_transfer_logs": [],
        "matched_transfer_log_indexes": [],
        "matched_transfer_amounts_raw": [],
        "matched_transfer_total_raw": None,
        "matched_transfer_logs_fingerprint": None,
        "malformed_matching_log_count": 0,
        "malformed_matching_logs": [],
        "balance_before_raw": str(
            baseline_raw_balance
        ),
        "balance_after": None,
        "balance_after_raw": None,
        "balance_delta_raw": None,
        "balance_delta_usdt": None,
        "expected_raw": str(
            expected_raw
        ),
        "unrelated_additional_incoming_raw": (
            None
        ),
        "balance_delta_covers_expected": False,
        "exact_transfer_log_match": False,
        "exact_balance_delta_match": False,
        "raw_receipt_omitted": True,
    }

    try:
        w3 = get_web3()
    except Exception as exc:
        return _receipt_pending_or_expired(
            evidence=evidence,
            error=(
                "BSC client unavailable: "
                f"{exc}"
            ),
        )

    try:
        receipt = (
            w3.eth.get_transaction_receipt(
                clean_tx_hash
            )
        )
    except Exception as exc:
        return _receipt_pending_or_expired(
            evidence=evidence,
            error=(
                "BSC receipt unavailable: "
                f"{exc}"
            ),
        )

    if receipt is None:
        return _receipt_pending_or_expired(
            evidence=evidence,
        )

    receipt_tx_hash = _normalized_hex(
        _rpc_value(
            receipt,
            "transactionHash",
            None,
        )
    )

    expected_tx_hash = _normalized_hex(
        clean_tx_hash
    )

    if (
        receipt_tx_hash is not None
        and receipt_tx_hash
        != expected_tx_hash
    ):
        evidence["state"] = (
            "failed_requires_review"
        )
        evidence["error"] = (
            "BSC receipt transaction hash mismatch"
        )
        evidence["observed_tx_hash"] = (
            receipt_tx_hash
        )

        return evidence

    try:
        receipt_status = _rpc_integer(
            _rpc_value(
                receipt,
                "status",
                None,
            ),
            field_name=(
                "receipt.status"
            ),
        )

        receipt_block_number = (
            _rpc_integer(
                _rpc_value(
                    receipt,
                    "blockNumber",
                    None,
                ),
                field_name=(
                    "receipt.blockNumber"
                ),
            )
        )

    except Exception as exc:
        evidence["state"] = (
            "failed_requires_review"
        )
        evidence["error"] = (
            "BSC receipt has invalid status or "
            f"block number: {exc}"
        )

        return evidence

    evidence["receipt_status"] = (
        receipt_status
    )
    evidence["receipt_block_number"] = (
        receipt_block_number
    )

    try:
        current_block_number = int(
            w3.eth.block_number
        )
    except Exception as exc:
        return _receipt_pending_or_expired(
            evidence=evidence,
            error=(
                "BSC current block unavailable: "
                f"{exc}"
            ),
        )

    confirmations = max(
        current_block_number
        - receipt_block_number
        + 1,
        0,
    )

    evidence["current_block_number"] = (
        current_block_number
    )
    evidence["confirmations"] = (
        confirmations
    )

    if (
        baseline_block_number
        >= receipt_block_number
    ):
        evidence["state"] = (
            "failed_requires_review"
        )
        evidence["error"] = (
            "Settlement wallet baseline block is "
            "not before withdrawal receipt block"
        )

        return evidence

    if confirmations < required_confirmations:
        return _receipt_pending_or_expired(
            evidence=evidence,
        )

    if receipt_status == 0:
        evidence["state"] = (
            "failed_requires_review"
        )
        evidence["error"] = (
            "BSC withdrawal transaction failed "
            "with receipt status 0"
        )

        return evidence

    if receipt_status != 1:
        evidence["state"] = (
            "failed_requires_review"
        )
        evidence["error"] = (
            "BSC withdrawal receipt has "
            f"unsupported status: {receipt_status}"
        )

        return evidence

    try:
        contract_checksum = (
            w3.to_checksum_address(
                contract_address
            )
        )

        wallet_checksum = (
            w3.to_checksum_address(
                clean_address
            )
        )

        transfer_topic = _normalized_hex(
            w3.keccak(
                text=(
                    ERC20_TRANSFER_EVENT_SIGNATURE
                )
            )
        )

        destination_topic = (
            "0x"
            + ("0" * 24)
            + wallet_checksum[
                2:
            ].lower()
        )

        logs_value = _rpc_value(
            receipt,
            "logs",
            [],
        )

        if isinstance(
            logs_value,
            (
                str,
                bytes,
                bytearray,
            ),
        ):
            raise NegativeBybitFlowError(
                "BSC receipt logs container is invalid"
            )

        logs = list(
            logs_value or []
        )

        matched_logs: list[
            dict[str, Any]
        ] = []

        malformed_matching_logs: list[
            dict[str, Any]
        ] = []

        for receipt_position, log in enumerate(
            logs
        ):
            log_address = str(
                _rpc_value(
                    log,
                    "address",
                    "",
                )
            ).strip()

            if (
                log_address.lower()
                != contract_checksum.lower()
            ):
                continue

            topics_value = _rpc_value(
                log,
                "topics",
                [],
            )

            if isinstance(
                topics_value,
                (
                    str,
                    bytes,
                    bytearray,
                ),
            ):
                malformed_matching_logs.append(
                    {
                        "receipt_position": (
                            receipt_position
                        ),
                        "reason": (
                            "invalid_topics_container"
                        ),
                    }
                )
                continue

            topics = list(
                topics_value or []
            )

            normalized_topics = [
                _normalized_hex(topic)
                for topic in topics
            ]

            transfer_like = (
                len(normalized_topics) >= 1
                and normalized_topics[0]
                == transfer_topic
            )

            destination_like = (
                len(normalized_topics) >= 3
                and normalized_topics[2]
                == destination_topic
            )

            if (
                transfer_like
                and len(normalized_topics) < 3
            ):
                malformed_matching_logs.append(
                    {
                        "receipt_position": (
                            receipt_position
                        ),
                        "reason": (
                            "transfer_topics_missing"
                        ),
                    }
                )
                continue

            if not destination_like:
                # Valid USDT Transfer to another
                # destination or unrelated USDT log.
                continue

            if (
                not transfer_like
                or len(normalized_topics) != 3
                or any(
                    topic is None
                    or len(topic) != 66
                    for topic
                    in normalized_topics
                )
            ):
                malformed_matching_logs.append(
                    {
                        "receipt_position": (
                            receipt_position
                        ),
                        "reason": (
                            "invalid_transfer_topics"
                        ),
                    }
                )
                continue

            try:
                log_index = _rpc_integer(
                    _rpc_value(
                        log,
                        "logIndex",
                        receipt_position,
                    ),
                    field_name=(
                        "receipt.logs.logIndex"
                    ),
                )

                amount_raw = _rpc_integer(
                    _rpc_value(
                        log,
                        "data",
                        None,
                    ),
                    field_name=(
                        "receipt.logs.data"
                    ),
                )

            except Exception as exc:
                malformed_matching_logs.append(
                    {
                        "receipt_position": (
                            receipt_position
                        ),
                        "reason": (
                            "invalid_log_index_or_amount"
                        ),
                        "error": str(exc)[:300],
                    }
                )
                continue

            if (
                log_index < 0
                or amount_raw < 0
            ):
                malformed_matching_logs.append(
                    {
                        "receipt_position": (
                            receipt_position
                        ),
                        "reason": (
                            "negative_log_index_or_amount"
                        ),
                    }
                )
                continue

            matched_logs.append(
                {
                    "receipt_position": (
                        receipt_position
                    ),
                    "log_index": log_index,
                    "amount_raw": str(
                        amount_raw
                    ),
                }
            )

    except Exception as exc:
        evidence["state"] = (
            "failed_requires_review"
        )
        evidence["error"] = (
            "BSC USDT Transfer log parsing "
            f"failed: {exc}"
        )

        return evidence

    matched_logs.sort(
        key=lambda item: (
            int(item["log_index"]),
            int(item["receipt_position"]),
        )
    )

    log_indexes = [
        int(item["log_index"])
        for item in matched_logs
    ]

    matched_amounts_raw = [
        str(item["amount_raw"])
        for item in matched_logs
    ]

    matched_total_raw = sum(
        int(item["amount_raw"])
        for item in matched_logs
    )

    logs_fingerprint = (
        _payload_fingerprint(
            {
                "schema": (
                    "negative_settlement_wallet_"
                    "matched_logs_v1"
                ),
                "tx_hash": (
                    expected_tx_hash
                ),
                "contract": (
                    contract_checksum.lower()
                ),
                "destination_topic": (
                    destination_topic
                ),
                "logs": [
                    {
                        "log_index": int(
                            item["log_index"]
                        ),
                        "amount_raw": str(
                            item["amount_raw"]
                        ),
                    }
                    for item in matched_logs
                ],
            }
        )
    )

    evidence[
        "matched_transfer_log_count"
    ] = len(matched_logs)

    evidence["matched_transfer_logs"] = (
        matched_logs
    )

    evidence[
        "matched_transfer_log_indexes"
    ] = log_indexes

    evidence[
        "matched_transfer_amounts_raw"
    ] = matched_amounts_raw

    evidence[
        "matched_transfer_total_raw"
    ] = str(matched_total_raw)

    evidence[
        "matched_transfer_logs_fingerprint"
    ] = logs_fingerprint

    evidence[
        "malformed_matching_log_count"
    ] = len(
        malformed_matching_logs
    )

    evidence[
        "malformed_matching_logs"
    ] = malformed_matching_logs

    if malformed_matching_logs:
        evidence["state"] = (
            "failed_requires_review"
        )
        evidence["error"] = (
            "BSC receipt contains malformed "
            "USDT Transfer-like log for settlement "
            "wallet"
        )

        return evidence

    if not matched_logs:
        evidence["state"] = (
            "failed_requires_review"
        )
        evidence["error"] = (
            "BSC receipt must contain one or more "
            "valid USDT Transfers to settlement wallet"
        )

        return evidence

    if len(set(log_indexes)) != len(
        log_indexes
    ):
        evidence["state"] = (
            "failed_requires_review"
        )
        evidence["error"] = (
            "BSC receipt contains duplicate matching "
            "Transfer log indexes"
        )

        return evidence

    if matched_total_raw != expected_raw:
        evidence["state"] = (
            "failed_requires_review"
        )
        evidence["error"] = (
            "BSC USDT Transfer aggregate amount does "
            "not match expected withdrawal amount"
        )

        return evidence

    evidence["exact_transfer_log_match"] = (
        True
    )

    try:
        balance_after = (
            _settlement_wallet_balance_snapshot(
                w3=w3,
                address=clean_address,
                block_number=(
                    current_block_number
                ),
            )
        )

    except Exception as exc:
        return _receipt_pending_or_expired(
            evidence=evidence,
            error=(
                "Settlement wallet balance-after "
                f"query failed: {exc}"
            ),
        )

    balance_after_raw = int(
        balance_after["raw_balance"]
    )

    balance_delta_raw = (
        balance_after_raw
        - baseline_raw_balance
    )

    balance_delta_usdt = (
        Decimal(balance_delta_raw)
        / (
            Decimal("10")
            ** decimals
        )
    )

    unrelated_additional_incoming_raw = (
        balance_delta_raw
        - expected_raw
    )

    balance_delta_covers_expected = (
        balance_delta_raw
        >= expected_raw
    )

    evidence["balance_after"] = (
        balance_after
    )

    evidence["balance_after_raw"] = str(
        balance_after_raw
    )

    evidence["balance_delta_raw"] = str(
        balance_delta_raw
    )

    evidence["balance_delta_usdt"] = (
        _decimal_text(
            balance_delta_usdt
        )
    )

    evidence[
        "unrelated_additional_incoming_raw"
    ] = str(
        unrelated_additional_incoming_raw
    )

    evidence[
        "balance_delta_covers_expected"
    ] = balance_delta_covers_expected

    evidence[
        "exact_balance_delta_match"
    ] = (
        balance_delta_raw
        == expected_raw
    )

    if not balance_delta_covers_expected:
        evidence["state"] = (
            "failed_requires_review"
        )
        evidence["error"] = (
            "Settlement wallet USDT balance delta "
            "does not cover expected withdrawal amount"
        )

        return evidence

    evidence["state"] = "confirmed"

    return evidence


def _persist_settlement_wallet_receipt_pending(
    db: Session,
    *,
    settlement_batch,
    flow: FundNegativeBybitFlow,
    observation: dict[str, Any],
    resolved_now: datetime,
    status_before: str | None,
    settlement_status_before: str | None,
) -> NegativeBybitFlowResult:
    flow.settlement_wallet_receipt_status = (
        "PENDING"
    )
    flow.settlement_wallet_receipt_tx_hash = (
        flow.withdrawal_tx_hash
    )
    flow.settlement_wallet_receipt_confirmations = (
        int(
            observation.get(
                "confirmations"
            )
            or 0
        )
    )
    flow.settlement_wallet_receipt_block_number = (
        observation.get(
            "receipt_block_number"
        )
    )

    balance_after = observation.get(
        "balance_after"
    )

    if isinstance(balance_after, dict):
        flow.settlement_wallet_balance_after_usdt = (
            Decimal(
                _required_text(
                    balance_after.get(
                        "balance_usdt"
                    ),
                    field_name=(
                        "balance_after.balance_usdt"
                    ),
                )
            )
        )

    flow.settlement_wallet_receipt_json = (
        _json_dict(observation)
    )
    flow.status = (
        BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_PENDING
    )
    flow.error = None
    flow.updated_at = resolved_now

    settlement_batch.status = (
        BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_RECONCILING
    )
    settlement_batch.error = None
    settlement_batch.updated_at = resolved_now

    db.add(flow)
    db.add(settlement_batch)
    db.flush()

    result = _step_result(
        ok=False,
        transition=(
            "reconcile_settlement_wallet_"
            "receipt_pending"
        ),
        settlement_batch=settlement_batch,
        flow=flow,
        status_before=status_before,
        settlement_status_before=(
            settlement_status_before
        ),
        diagnostics={
            "did_bybit_post": False,
            "bybit_post_count": 0,
            "bybit_get_count": 0,
            "bsc_rpc_read_count": 1,
            "confirmations": (
                flow
                .settlement_wallet_receipt_confirmations
            ),
            "required_confirmations": (
                observation[
                    "required_confirmations"
                ]
            ),
            "reserve_release_allowed": False,
            "pricing_unlock_allowed": False,
            "next_transition": (
                "reconcile_settlement_wallet_"
                "receipt"
            ),
        },
    )

    db.commit()

    return result


def _fail_settlement_wallet_receipt(
    db: Session,
    *,
    settlement_batch,
    flow: FundNegativeBybitFlow,
    observation: dict[str, Any],
    resolved_now: datetime,
    status_before: str | None,
    settlement_status_before: str | None,
) -> NegativeBybitFlowResult:
    prior_reconciliation = (
        deepcopy(flow.reconciliation_json)
        if isinstance(
            flow.reconciliation_json,
            dict,
        )
        else None
    )

    error = str(
        observation.get("error")
        or (
            "Settlement wallet receipt "
            "reconciliation failed"
        )
    )

    flow.settlement_wallet_receipt_status = (
        "FAILED_REQUIRES_REVIEW"
    )
    flow.settlement_wallet_receipt_tx_hash = (
        flow.withdrawal_tx_hash
    )
    flow.settlement_wallet_receipt_confirmations = (
        int(
            observation.get(
                "confirmations"
            )
            or 0
        )
    )
    flow.settlement_wallet_receipt_block_number = (
        observation.get(
            "receipt_block_number"
        )
    )
    flow.settlement_wallet_receipt_json = (
        _json_dict(observation)
    )

    balance_after = observation.get(
        "balance_after"
    )

    if isinstance(balance_after, dict):
        flow.settlement_wallet_balance_after_usdt = (
            Decimal(
                _required_text(
                    balance_after.get(
                        "balance_usdt"
                    ),
                    field_name=(
                        "balance_after.balance_usdt"
                    ),
                )
            )
        )

    result = _set_failed(
        flow=flow,
        settlement_batch=settlement_batch,
        fund=None,
        status_before=status_before,
        settlement_status_before=(
            settlement_status_before
        ),
        error=error,
        now=resolved_now,
        diagnostics={
            "transition": (
                "reconcile_settlement_wallet_"
                "receipt_failed"
            ),
            "did_bybit_post": False,
            "bybit_post_count": 0,
            "bybit_get_count": 0,
            "bsc_rpc_read_count": 1,
            "reserve_release_allowed": False,
            "pricing_unlock_allowed": False,
        },
    )

    _merge_failure_reconciliation(
        flow=flow,
        prior_reconciliation=(
            prior_reconciliation
        ),
    )

    db.add(flow)
    db.add(settlement_batch)
    db.flush()
    db.commit()

    return result


def _reconcile_settlement_wallet_receipt_once(
    db: Session,
    *,
    settlement_batch,
    flow: FundNegativeBybitFlow,
    resolved_now: datetime,
    status_before: str | None,
    settlement_status_before: str | None,
) -> NegativeBybitFlowResult:
    if str(flow.status) not in {
        BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILED,
        BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_PENDING,
    }:
        raise NegativeBybitFlowError(
            "Settlement wallet receipt "
            "reconciliation has incompatible "
            f"flow status: {flow.status}"
        )

    intent = deepcopy(
        flow.withdrawal_intent_json
    )

    if not isinstance(intent, dict):
        raise NegativeBybitFlowError(
            "Withdrawal intent missing during "
            "settlement wallet receipt "
            "reconciliation"
        )

    snapshot = _withdrawal_intent_snapshot(
        flow=flow,
        intent=intent,
        allowed_states={"confirmed"},
    )

    withdrawal_reconciliation = (
        intent.get("reconciliation")
    )

    if not isinstance(
        withdrawal_reconciliation,
        dict,
    ):
        raise NegativeBybitFlowError(
            "Confirmed withdrawal reconciliation "
            "evidence is missing"
        )

    if (
        withdrawal_reconciliation.get(
            "schema"
        )
        != WITHDRAWAL_RECONCILIATION_SCHEMA
    ):
        raise NegativeBybitFlowError(
            "Confirmed withdrawal reconciliation "
            "schema mismatch"
        )

    if withdrawal_reconciliation.get(
        "state"
    ) != "confirmed":
        raise NegativeBybitFlowError(
            "Withdrawal reconciliation is not "
            "confirmed"
        )

    tx_hash = _required_text(
        flow.withdrawal_tx_hash,
        field_name=(
            "flow.withdrawal_tx_hash"
        ),
    )

    if (
        _normalized_hex(
            withdrawal_reconciliation.get(
                "tx_hash"
            )
        )
        != _normalized_hex(tx_hash)
    ):
        raise NegativeBybitFlowError(
            "Withdrawal reconciliation tx hash "
            "mismatch"
        )

    if not _is_withdrawal_success_like(
        flow.withdrawal_status
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt requires "
            "successful Bybit withdrawal status"
        )

    if not flow.withdrawal_id:
        raise NegativeBybitFlowError(
            "Settlement wallet receipt requires "
            "withdrawal_id"
        )

    pending_started_at = (
        _aware_utc_datetime(
            flow.withdrawal_confirmed_at,
            field_name=(
                "flow.withdrawal_confirmed_at"
            ),
        )
    )

    barrier_snapshot = (
        _confirmed_master_balance_barrier(
            flow
        )
    )

    wallet = _get_active_settlement_wallet(
        db,
        fund_id=int(flow.fund_id),
    )

    if int(wallet.id) != snapshot[
        "settlement_wallet_id"
    ]:
        raise NegativeBybitFlowError(
            "Active settlement wallet ID mismatch "
            "during BSC receipt reconciliation"
        )

    if (
        str(wallet.address).strip().lower()
        != snapshot["address"].lower()
    ):
        raise NegativeBybitFlowError(
            "Active settlement wallet address "
            "mismatch during BSC receipt "
            "reconciliation"
        )

    balance_baseline = deepcopy(
        snapshot["balance_baseline"]
    )

    settlement_batch_id = int(
        settlement_batch.id
    )
    expected_flow_id = int(flow.id)

    # Release all row locks before BSC RPC.
    db.commit()

    observation = (
        _query_settlement_wallet_receipt_observation(
            tx_hash=tx_hash,
            address=snapshot["address"],
            expected_amount_usdt=(
                snapshot["amount_usdt"]
            ),
            balance_baseline=(
                balance_baseline
            ),
            pending_started_at=(
                pending_started_at
            ),
            checked_at=resolved_now,
        )
    )

    (
        settlement_batch,
        _,
        flow,
    ) = _locked_withdrawal_context(
        db,
        settlement_batch_id=(
            settlement_batch_id
        ),
    )

    if int(flow.id) != expected_flow_id:
        raise NegativeBybitFlowError(
            "Negative Bybit flow identity changed "
            "during BSC receipt reconciliation"
        )

    if str(flow.status) == (
        BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_CONFIRMED
    ):
        result = _step_result(
            ok=True,
            transition=(
                "settlement_wallet_receipt_"
                "concurrent_confirmed"
            ),
            settlement_batch=settlement_batch,
            flow=flow,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            idempotent=True,
            diagnostics={
                "did_bybit_post": False,
                "bybit_post_count": 0,
                "bybit_get_count": 0,
                "bsc_rpc_read_count": 1,
                "reserve_release_allowed": False,
                "pricing_unlock_allowed": False,
                "next_transition": (
                    "complete_negative_cash_"
                    "delivery"
                ),
            },
        )

        db.commit()

        return result

    if str(flow.status) == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    ):
        result = _step_result(
            ok=False,
            transition=(
                "failed_requires_review_"
                "already_recorded"
            ),
            settlement_batch=settlement_batch,
            flow=flow,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            idempotent=True,
            error=flow.error,
            diagnostics={
                "did_bybit_post": False,
                "bybit_post_count": 0,
                "bybit_get_count": 0,
                "bsc_rpc_read_count": 1,
                "reserve_release_allowed": False,
                "pricing_unlock_allowed": False,
            },
        )

        db.commit()

        return result

    if str(flow.status) not in {
        BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILED,
        BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_PENDING,
    }:
        raise NegativeBybitFlowError(
            "Flow status changed during BSC "
            "receipt reconciliation"
        )

    current_intent = deepcopy(
        flow.withdrawal_intent_json
    )

    if not isinstance(
        current_intent,
        dict,
    ):
        raise NegativeBybitFlowError(
            "Withdrawal intent disappeared during "
            "BSC receipt reconciliation"
        )

    _validate_withdrawal_snapshot_unchanged(
        flow=flow,
        intent=current_intent,
        snapshot=snapshot,
        allowed_states={"confirmed"},
    )

    if (
        _confirmed_master_balance_barrier(flow)
        != barrier_snapshot
    ):
        raise NegativeBybitFlowError(
            "Master balance barrier changed during "
            "BSC receipt reconciliation"
        )

    if (
        _normalized_hex(
            flow.withdrawal_tx_hash
        )
        != _normalized_hex(tx_hash)
    ):
        raise NegativeBybitFlowError(
            "Withdrawal tx hash changed during "
            "BSC receipt reconciliation"
        )

    current_wallet = (
        _get_active_settlement_wallet(
            db,
            fund_id=int(flow.fund_id),
        )
    )

    if int(current_wallet.id) != snapshot[
        "settlement_wallet_id"
    ]:
        raise NegativeBybitFlowError(
            "Active settlement wallet changed "
            "during BSC receipt reconciliation"
        )

    if (
        str(current_wallet.address)
        .strip()
        .lower()
        != snapshot["address"].lower()
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet address changed "
            "during BSC receipt reconciliation"
        )

    observation_state = str(
        observation.get("state") or ""
    ).strip()

    if observation_state == "pending":
        return (
            _persist_settlement_wallet_receipt_pending(
                db,
                settlement_batch=(
                    settlement_batch
                ),
                flow=flow,
                observation=observation,
                resolved_now=resolved_now,
                status_before=status_before,
                settlement_status_before=(
                    settlement_status_before
                ),
            )
        )

    if observation_state == (
        "failed_requires_review"
    ):
        return _fail_settlement_wallet_receipt(
            db,
            settlement_batch=(
                settlement_batch
            ),
            flow=flow,
            observation=observation,
            resolved_now=resolved_now,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
        )

    if observation_state != "confirmed":
        raise NegativeBybitFlowError(
            "Unsupported settlement wallet receipt "
            f"state: {observation_state or 'empty'}"
        )

    if observation.get(
        "exact_transfer_log_match"
    ) is not True:
        raise NegativeBybitFlowError(
            "Confirmed receipt lacks exact USDT "
            "Transfer log match"
        )

    if observation.get(
        "balance_delta_covers_expected"
    ) is not True:
        raise NegativeBybitFlowError(
            "Confirmed receipt balance delta does "
            "not cover expected withdrawal amount"
        )

    balance_after = observation.get(
        "balance_after"
    )

    if not isinstance(balance_after, dict):
        raise NegativeBybitFlowError(
            "Confirmed settlement wallet receipt "
            "lacks balance-after snapshot"
        )

    balance_after_usdt = Decimal(
        _required_text(
            balance_after.get(
                "balance_usdt"
            ),
            field_name=(
                "balance_after.balance_usdt"
            ),
        )
    )

    balance_delta_usdt = Decimal(
        _required_text(
            observation.get(
                "balance_delta_usdt"
            ),
            field_name=(
                "receipt.balance_delta_usdt"
            ),
        )
    )

    if (
        balance_delta_usdt
        < snapshot["amount_usdt"]
    ):
        raise NegativeBybitFlowError(
            "Confirmed settlement wallet balance "
            "delta is below expected amount"
        )

    expected_raw = int(
        _required_text(
            observation.get(
                "expected_raw"
            ),
            field_name=(
                "receipt.expected_raw"
            ),
        )
    )

    balance_before_raw = int(
        _required_text(
            observation.get(
                "balance_before_raw"
            ),
            field_name=(
                "receipt.balance_before_raw"
            ),
        )
    )

    balance_after_raw = int(
        _required_text(
            observation.get(
                "balance_after_raw"
            ),
            field_name=(
                "receipt.balance_after_raw"
            ),
        )
    )

    balance_delta_raw = int(
        _required_text(
            observation.get(
                "balance_delta_raw"
            ),
            field_name=(
                "receipt.balance_delta_raw"
            ),
        )
    )

    unrelated_additional_raw = int(
        _required_text(
            observation.get(
                "unrelated_additional_incoming_raw"
            ),
            field_name=(
                "receipt.unrelated_additional_"
                "incoming_raw"
            ),
        )
    )

    if (
        balance_after_raw
        - balance_before_raw
        != balance_delta_raw
    ):
        raise NegativeBybitFlowError(
            "Confirmed settlement wallet raw "
            "balance arithmetic mismatch"
        )

    if balance_delta_raw < expected_raw:
        raise NegativeBybitFlowError(
            "Confirmed settlement wallet raw "
            "balance delta is below expected amount"
        )

    if (
        unrelated_additional_raw
        != balance_delta_raw
        - expected_raw
    ):
        raise NegativeBybitFlowError(
            "Confirmed settlement wallet unrelated "
            "incoming amount mismatch"
        )

    flow.settlement_wallet_balance_after_usdt = (
        balance_after_usdt
    )
    flow.settlement_wallet_receipt_status = (
        "CONFIRMED"
    )
    flow.settlement_wallet_received_usdt = (
        snapshot["amount_usdt"]
    )
    flow.settlement_wallet_receipt_tx_hash = (
        tx_hash
    )
    flow.settlement_wallet_receipt_confirmations = (
        int(observation["confirmations"])
    )
    flow.settlement_wallet_receipt_block_number = (
        int(
            observation[
                "receipt_block_number"
            ]
        )
    )
    flow.settlement_wallet_receipt_confirmed_at = (
        resolved_now
    )
    flow.settlement_wallet_receipt_json = (
        _json_dict(observation)
    )
    flow.status = (
        BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_CONFIRMED
    )
    flow.error = None
    flow.updated_at = resolved_now

    settlement_batch.status = (
        BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_RECONCILING
    )
    settlement_batch.error = None
    settlement_batch.updated_at = resolved_now

    db.add(flow)
    db.add(settlement_batch)
    db.flush()

    result = _step_result(
        ok=True,
        transition=(
            "reconcile_settlement_wallet_"
            "receipt_confirmed"
        ),
        settlement_batch=settlement_batch,
        flow=flow,
        status_before=status_before,
        settlement_status_before=(
            settlement_status_before
        ),
        diagnostics={
            "did_bybit_post": False,
            "bybit_post_count": 0,
            "bybit_get_count": 0,
            "bsc_rpc_read_count": 1,
            "confirmations": (
                flow
                .settlement_wallet_receipt_confirmations
            ),
            "received_usdt": (
                _decimal_text(
                    flow
                    .settlement_wallet_received_usdt
                )
            ),
            "exact_transfer_log_match": True,
            "balance_delta_covers_expected": True,
            "exact_balance_delta_match": (
                observation.get(
                    "exact_balance_delta_match"
                )
                is True
            ),
            "reserve_release_allowed": False,
            "pricing_unlock_allowed": False,
            "next_transition": (
                "complete_negative_cash_delivery"
            ),
        },
    )

    db.commit()

    return result


def _completion_decimal(
    value: Any,
    *,
    field_name: str,
) -> Decimal:
    try:
        return Decimal(
            _required_text(
                value,
                field_name=field_name,
            )
        )
    except (
        ArithmeticError,
        TypeError,
        ValueError,
    ) as exc:
        raise NegativeBybitFlowError(
            f"{field_name} must be a valid decimal"
        ) from exc


def _completion_integer(
    value: Any,
    *,
    field_name: str,
) -> int:
    if (
        value is None
        or isinstance(value, bool)
    ):
        raise NegativeBybitFlowError(
            f"{field_name} must be a valid integer"
        )

    clean_value = str(value).strip()

    if not clean_value:
        raise NegativeBybitFlowError(
            f"{field_name} must be a valid integer"
        )

    try:
        return int(clean_value)

    except (
        TypeError,
        ValueError,
    ) as exc:
        raise NegativeBybitFlowError(
            f"{field_name} must be a valid integer"
        ) from exc


def _cash_delivery_snapshot(
    *,
    flow: FundNegativeBybitFlow,
    settlement_batch,
    amounts: dict[str, Decimal],
    allowed_flow_statuses: set[str],
) -> dict[str, Any]:
    if str(flow.status) not in (
        allowed_flow_statuses
    ):
        raise NegativeBybitFlowError(
            "Cash-delivery completion has "
            "incompatible flow status: "
            f"{flow.status}"
        )

    universal_intent = (
        flow.universal_transfer_intent_json
    )

    if not isinstance(
        universal_intent,
        dict,
    ):
        raise NegativeBybitFlowError(
            "Confirmed Universal Transfer intent "
            "is missing"
        )

    _validate_prepared_intent(
        flow=flow,
        intent=universal_intent,
        allowed_states={"confirmed"},
    )

    universal_reconciliation = (
        universal_intent.get(
            "reconciliation"
        )
    )

    if not isinstance(
        universal_reconciliation,
        dict,
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer reconciliation "
            "evidence is missing"
        )

    if (
        universal_reconciliation.get(
            "schema"
        )
        != UNIVERSAL_TRANSFER_RECONCILIATION_SCHEMA
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer reconciliation "
            "schema mismatch"
        )

    if (
        universal_reconciliation.get(
            "phase"
        )
        != "exact_transfer_id_query"
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer reconciliation "
            "phase mismatch"
        )

    if (
        str(
            universal_reconciliation.get(
                "transfer_id"
            )
            or ""
        ).strip()
        != str(
            flow.universal_transfer_id
            or ""
        ).strip()
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer reconciliation "
            "transfer_id mismatch"
        )

    if (
        universal_reconciliation.get(
            "record_found"
        )
        is not True
        or universal_reconciliation.get(
            "query_succeeded"
        )
        is not True
        or universal_reconciliation.get(
            "exact_match"
        )
        is not True
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer reconciliation "
            "is not confirmed"
        )

    if not _is_bybit_success(
        universal_reconciliation.get(
            "observed_status"
        )
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer reconciliation "
            "is not confirmed"
        )

    if (
        universal_reconciliation.get(
            "no_automatic_resend"
        )
        is not True
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer reconciliation "
            "no-resend marker is missing"
        )

    if not isinstance(
        universal_reconciliation.get(
            "record"
        ),
        dict,
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer reconciliation "
            "record evidence is missing"
        )

    durable_universal_reconciliation = (
        flow
        .universal_transfer_reconciliation_json
    )

    if not isinstance(
        durable_universal_reconciliation,
        dict,
    ):
        raise NegativeBybitFlowError(
            "Durable Universal Transfer "
            "reconciliation evidence is missing"
        )

    if (
        durable_universal_reconciliation
        != universal_reconciliation
    ):
        raise NegativeBybitFlowError(
            "Durable Universal Transfer "
            "reconciliation evidence mismatch"
        )

    if not _is_bybit_success(
        flow.universal_transfer_status
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer status is not "
            "successful"
        )

    if (
        flow.universal_transfer_confirmed_at
        is None
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer confirmed_at "
            "is missing"
        )

    if not _same_decimal(
        flow.universal_transfer_amount_usdt,
        amounts["required_master_usdt"],
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer amount does not "
            "match required master amount"
        )

    master_barrier = (
        _confirmed_master_balance_barrier(
            flow
        )
    )

    withdrawal_intent = (
        flow.withdrawal_intent_json
    )

    if not isinstance(
        withdrawal_intent,
        dict,
    ):
        raise NegativeBybitFlowError(
            "Confirmed withdrawal intent is missing"
        )

    withdrawal_snapshot = (
        _withdrawal_intent_snapshot(
            flow=flow,
            intent=withdrawal_intent,
            allowed_states={"confirmed"},
        )
    )

    if not _same_decimal(
        withdrawal_snapshot[
            "amount_usdt"
        ],
        amounts[
            "withdrawal_request_amount_usdt"
        ],
    ):
        raise NegativeBybitFlowError(
            "Confirmed withdrawal amount does not "
            "match settlement target"
        )

    if not _same_decimal(
        withdrawal_snapshot["fee_usdt"],
        amounts["bybit_withdrawal_fee_usdt"],
    ):
        raise NegativeBybitFlowError(
            "Confirmed withdrawal fee does not "
            "match settlement target"
        )

    withdrawal_reconciliation = (
        withdrawal_intent.get(
            "reconciliation"
        )
    )

    if not isinstance(
        withdrawal_reconciliation,
        dict,
    ):
        raise NegativeBybitFlowError(
            "Withdrawal reconciliation evidence "
            "is missing"
        )

    if (
        withdrawal_reconciliation.get(
            "schema"
        )
        != WITHDRAWAL_RECONCILIATION_SCHEMA
    ):
        raise NegativeBybitFlowError(
            "Withdrawal reconciliation schema "
            "mismatch"
        )

    if withdrawal_reconciliation.get(
        "state"
    ) != "confirmed":
        raise NegativeBybitFlowError(
            "Withdrawal reconciliation is not "
            "confirmed"
        )

    selected_source = str(
        withdrawal_reconciliation.get(
            "selected_source"
        )
        or ""
    ).strip()

    allowed_selected_sources = {
        "withdrawal_id_query",
        "tx_hash_query",
        "bounded_record_lookup",
    }

    if (
        selected_source
        not in allowed_selected_sources
    ):
        raise NegativeBybitFlowError(
            "Withdrawal reconciliation selected "
            "source is invalid"
        )

    if (
        withdrawal_reconciliation.get(
            "unique_match"
        )
        is not True
        or withdrawal_reconciliation.get(
            "ambiguous"
        )
        is not False
        or withdrawal_reconciliation.get(
            "exact_fingerprint_match"
        )
        is not True
    ):
        raise NegativeBybitFlowError(
            "Withdrawal reconciliation unique-match "
            "evidence is invalid"
        )

    if (
        withdrawal_reconciliation.get(
            "no_automatic_resend"
        )
        is not True
    ):
        raise NegativeBybitFlowError(
            "Withdrawal reconciliation no-resend "
            "marker is missing"
        )

    if (
        str(
            withdrawal_reconciliation.get(
                "request_id"
            )
            or ""
        ).strip()
        != withdrawal_snapshot[
            "request_id"
        ]
    ):
        raise NegativeBybitFlowError(
            "Withdrawal reconciliation request_id "
            "mismatch"
        )

    record_fingerprint = _required_text(
        withdrawal_reconciliation.get(
            "record_fingerprint"
        ),
        field_name=(
            "withdrawal_reconciliation."
            "record_fingerprint"
        ),
    ).lower()

    if (
        len(record_fingerprint) != 64
        or any(
            character
            not in "0123456789abcdef"
            for character
            in record_fingerprint
        )
    ):
        raise NegativeBybitFlowError(
            "Withdrawal reconciliation record "
            "fingerprint is invalid"
        )

    queries = (
        withdrawal_reconciliation.get(
            "queries"
        )
    )

    if not isinstance(queries, dict):
        raise NegativeBybitFlowError(
            "Withdrawal reconciliation query "
            "evidence is missing"
        )

    selected_query = queries.get(
        selected_source
    )

    if not isinstance(
        selected_query,
        dict,
    ):
        raise NegativeBybitFlowError(
            "Withdrawal reconciliation selected "
            "query evidence is missing"
        )

    if (
        selected_query.get("exhausted")
        is not True
        or selected_query.get(
            "stop_reason"
        )
        != "end_of_pages"
    ):
        raise NegativeBybitFlowError(
            "Withdrawal reconciliation selected "
            "query was not exhausted"
        )

    durable_withdrawal_reconciliation = (
        flow.withdrawal_reconciliation_json
    )

    if not isinstance(
        durable_withdrawal_reconciliation,
        dict,
    ):
        raise NegativeBybitFlowError(
            "Durable withdrawal reconciliation "
            "evidence is missing"
        )

    if (
        durable_withdrawal_reconciliation
        != withdrawal_reconciliation
    ):
        raise NegativeBybitFlowError(
            "Durable withdrawal reconciliation "
            "evidence mismatch"
        )

    withdrawal_record_evidence = (
        flow.withdrawal_record_json
    )

    if not isinstance(
        withdrawal_record_evidence,
        dict,
    ):
        raise NegativeBybitFlowError(
            "Durable withdrawal record evidence "
            "is missing"
        )

    if (
        str(
            withdrawal_record_evidence.get(
                "record_fingerprint"
            )
            or ""
        ).strip().lower()
        != record_fingerprint
    ):
        raise NegativeBybitFlowError(
            "Withdrawal reconciliation record "
            "fingerprint mismatch"
        )

    if not _is_withdrawal_success_like(
        flow.withdrawal_status
    ):
        raise NegativeBybitFlowError(
            "Withdrawal status is not successful"
        )

    _required_text(
        flow.withdrawal_id,
        field_name="flow.withdrawal_id",
    )

    withdrawal_tx_hash = _required_text(
        flow.withdrawal_tx_hash,
        field_name=(
            "flow.withdrawal_tx_hash"
        ),
    )

    if (
        _normalized_hex(
            withdrawal_reconciliation.get(
                "tx_hash"
            )
        )
        != _normalized_hex(
            withdrawal_tx_hash
        )
    ):
        raise NegativeBybitFlowError(
            "Withdrawal reconciliation tx hash "
            "mismatch"
        )

    if flow.withdrawal_confirmed_at is None:
        raise NegativeBybitFlowError(
            "Withdrawal confirmed_at is missing"
        )

    receipt = (
        flow.settlement_wallet_receipt_json
    )

    if not isinstance(receipt, dict):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt evidence "
            "is missing"
        )

    if (
        receipt.get("schema")
        != SETTLEMENT_WALLET_RECEIPT_SCHEMA
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt schema "
            "mismatch"
        )

    if (
        receipt.get("policy_version")
        != settings
        .NEGATIVE_NET_WITHDRAWAL_POLICY_VERSION
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt policy "
            "mismatch"
        )

    if receipt.get("state") != "confirmed":
        raise NegativeBybitFlowError(
            "Settlement wallet receipt is not "
            "confirmed"
        )

    if (
        receipt.get(
            "exact_transfer_log_match"
        )
        is not True
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt lacks exact "
            "Transfer log match"
        )

    if (
        receipt.get(
            "balance_delta_covers_expected"
        )
        is not True
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt balance "
            "delta does not cover expected amount"
        )

    if (
        receipt.get("raw_receipt_omitted")
        is not True
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt raw-state "
            "redaction marker is missing"
        )

    if (
        _normalized_hex(
            receipt.get("tx_hash")
        )
        != _normalized_hex(
            withdrawal_tx_hash
        )
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt tx hash "
            "mismatch"
        )

    if (
        _normalized_hex(
            flow
            .settlement_wallet_receipt_tx_hash
        )
        != _normalized_hex(
            withdrawal_tx_hash
        )
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt flow tx hash "
            "mismatch"
        )

    if (
        str(
            flow
            .settlement_wallet_receipt_status
            or ""
        ).strip().upper()
        != "CONFIRMED"
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt flow status "
            "is not CONFIRMED"
        )

    expected_amount = Decimal(
        withdrawal_snapshot[
            "amount_usdt"
        ]
    )

    if not _same_decimal(
        flow.settlement_wallet_received_usdt,
        expected_amount,
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet received amount "
            "mismatch"
        )

    if not _same_decimal(
        _completion_decimal(
            receipt.get(
                "expected_amount_usdt"
            ),
            field_name=(
                "receipt.expected_amount_usdt"
            ),
        ),
        expected_amount,
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt expected "
            "amount mismatch"
        )

    balance_delta_usdt = (
        _completion_decimal(
            receipt.get(
                "balance_delta_usdt"
            ),
            field_name=(
                "receipt.balance_delta_usdt"
            ),
        )
    )

    if balance_delta_usdt < expected_amount:
        raise NegativeBybitFlowError(
            "Settlement wallet receipt balance "
            "delta is below expected amount"
        )

    required_confirmations = int(
        settings
        .NEGATIVE_NET_BSC_INTENT_CONFIRMATIONS_REQUIRED
    )

    observed_confirmations = (
        _completion_integer(
            flow
            .settlement_wallet_receipt_confirmations,
            field_name=(
                "flow.settlement_wallet_receipt_"
                "confirmations"
            ),
        )
    )

    evidence_confirmations = (
        _completion_integer(
            receipt.get("confirmations"),
            field_name=(
                "receipt.confirmations"
            ),
        )
    )

    if (
        observed_confirmations
        != evidence_confirmations
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt "
            "confirmations mismatch"
        )

    if (
        observed_confirmations
        < required_confirmations
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt has "
            "insufficient confirmations"
        )

    receipt_block_number = (
        _completion_integer(
            receipt.get(
                "receipt_block_number"
            ),
            field_name=(
                "receipt.receipt_block_number"
            ),
        )
    )

    flow_receipt_block_number = (
        _completion_integer(
            flow
            .settlement_wallet_receipt_block_number,
            field_name=(
                "flow.settlement_wallet_receipt_"
                "block_number"
            ),
        )
    )

    if (
        receipt_block_number
        != flow_receipt_block_number
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt block "
            "number mismatch"
        )

    if receipt_block_number < 0:
        raise NegativeBybitFlowError(
            "Settlement wallet receipt block "
            "number is invalid"
        )

    if (
        flow
        .settlement_wallet_receipt_confirmed_at
        is None
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt "
            "confirmed_at is missing"
        )

    balance_before = receipt.get(
        "balance_before"
    )

    if (
        balance_before
        != withdrawal_snapshot[
            "balance_baseline"
        ]
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt baseline "
            "changed"
        )

    balance_after = receipt.get(
        "balance_after"
    )

    if not isinstance(
        balance_after,
        dict,
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet balance-after "
            "snapshot is missing"
        )

    if (
        str(
            balance_after.get("address")
            or ""
        ).strip().lower()
        != withdrawal_snapshot[
            "address"
        ].lower()
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet balance-after "
            "address mismatch"
        )

    if (
        str(
            balance_after.get("contract")
            or ""
        ).strip().lower()
        != str(
            settings.BSC_USDT_CONTRACT
        ).strip().lower()
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet balance-after "
            "contract mismatch"
        )

    decimals = _completion_integer(
        balance_after.get("decimals"),
        field_name=(
            "balance_after.decimals"
        ),
    )

    if decimals != int(
        settings.BSC_USDT_DECIMALS
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet balance-after "
            "decimals mismatch"
        )

    before_usdt = _completion_decimal(
        withdrawal_snapshot[
            "balance_baseline"
        ].get("balance_usdt"),
        field_name=(
            "balance_baseline.balance_usdt"
        ),
    )

    after_usdt = _completion_decimal(
        balance_after.get(
            "balance_usdt"
        ),
        field_name=(
            "balance_after.balance_usdt"
        ),
    )

    if not _same_decimal(
        flow
        .settlement_wallet_balance_before_usdt,
        before_usdt,
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet balance-before "
            "field mismatch"
        )

    if not _same_decimal(
        flow
        .settlement_wallet_balance_after_usdt,
        after_usdt,
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet balance-after "
            "field mismatch"
        )

    if (
        after_usdt - before_usdt
        < expected_amount
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet final balance delta "
            "is below received amount"
        )

    expected_raw_decimal = (
        expected_amount
        * (
            Decimal("10")
            ** decimals
        )
    )

    expected_raw = int(
        expected_raw_decimal
    )

    if (
        Decimal(expected_raw)
        != expected_raw_decimal
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet received amount "
            "cannot be represented exactly"
        )

    if (
        _completion_integer(
            receipt.get(
                "expected_amount_raw"
            ),
            field_name=(
                "receipt.expected_amount_raw"
            ),
        )
        != expected_raw
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt expected "
            "raw amount mismatch"
        )

    receipt_expected_raw = (
        _completion_integer(
            receipt.get(
                "expected_raw"
            ),
            field_name=(
                "receipt.expected_raw"
            ),
        )
    )

    if receipt_expected_raw != expected_raw:
        raise NegativeBybitFlowError(
            "Settlement wallet receipt expected "
            "raw value mismatch"
        )

    balance_before_raw = (
        _completion_integer(
            receipt.get(
                "balance_before_raw"
            ),
            field_name=(
                "receipt.balance_before_raw"
            ),
        )
    )

    balance_after_raw = (
        _completion_integer(
            receipt.get(
                "balance_after_raw"
            ),
            field_name=(
                "receipt.balance_after_raw"
            ),
        )
    )

    balance_delta_raw = (
        _completion_integer(
            receipt.get(
                "balance_delta_raw"
            ),
            field_name=(
                "receipt.balance_delta_raw"
            ),
        )
    )

    baseline_raw = (
        _completion_integer(
            withdrawal_snapshot[
                "balance_baseline"
            ].get(
                "raw_balance"
            ),
            field_name=(
                "balance_baseline.raw_balance"
            ),
        )
    )

    balance_after_snapshot_raw = (
        _completion_integer(
            balance_after.get(
                "raw_balance"
            ),
            field_name=(
                "balance_after.raw_balance"
            ),
        )
    )

    if balance_before_raw != baseline_raw:
        raise NegativeBybitFlowError(
            "Settlement wallet receipt raw "
            "balance-before mismatch"
        )

    if (
        balance_after_raw
        != balance_after_snapshot_raw
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt raw "
            "balance-after mismatch"
        )

    if (
        balance_after_raw
        - balance_before_raw
        != balance_delta_raw
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt raw "
            "balance arithmetic mismatch"
        )

    if balance_delta_raw < expected_raw:
        raise NegativeBybitFlowError(
            "Settlement wallet receipt raw balance "
            "delta is below expected amount"
        )

    unrelated_additional_raw = (
        _completion_integer(
            receipt.get(
                "unrelated_additional_incoming_raw"
            ),
            field_name=(
                "receipt.unrelated_additional_"
                "incoming_raw"
            ),
        )
    )

    if unrelated_additional_raw < 0:
        raise NegativeBybitFlowError(
            "Settlement wallet unrelated incoming "
            "amount cannot be negative"
        )

    if (
        unrelated_additional_raw
        != balance_delta_raw
        - expected_raw
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt unrelated "
            "incoming amount mismatch"
        )

    malformed_count = (
        _completion_integer(
            receipt.get(
                "malformed_matching_log_count"
            ),
            field_name=(
                "receipt.malformed_matching_"
                "log_count"
            ),
        )
    )

    malformed_logs = receipt.get(
        "malformed_matching_logs"
    )

    if (
        malformed_count != 0
        or malformed_logs != []
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt contains "
            "malformed matching Transfer evidence"
        )

    matched_log_count = (
        _completion_integer(
            receipt.get(
                "matched_transfer_log_count"
            ),
            field_name=(
                "receipt.matched_transfer_"
                "log_count"
            ),
        )
    )

    if matched_log_count <= 0:
        raise NegativeBybitFlowError(
            "Settlement wallet receipt must contain "
            "one or more matching Transfer logs"
        )

    matched_logs = receipt.get(
        "matched_transfer_logs"
    )

    if (
        not isinstance(matched_logs, list)
        or len(matched_logs)
        != matched_log_count
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt matching "
            "Transfer log evidence is invalid"
        )

    normalized_logs: list[
        dict[str, Any]
    ] = []

    for position, matched_log in enumerate(
        matched_logs
    ):
        if not isinstance(
            matched_log,
            dict,
        ):
            raise NegativeBybitFlowError(
                "Settlement wallet receipt matching "
                "Transfer log entry is invalid"
            )

        log_index = _completion_integer(
            matched_log.get(
                "log_index"
            ),
            field_name=(
                "receipt.matched_transfer_logs."
                f"{position}.log_index"
            ),
        )

        amount_raw = _completion_integer(
            matched_log.get(
                "amount_raw"
            ),
            field_name=(
                "receipt.matched_transfer_logs."
                f"{position}.amount_raw"
            ),
        )

        if (
            log_index < 0
            or amount_raw < 0
        ):
            raise NegativeBybitFlowError(
                "Settlement wallet receipt matching "
                "Transfer log contains negative value"
            )

        normalized_logs.append(
            {
                "log_index": log_index,
                "amount_raw": amount_raw,
            }
        )

    if normalized_logs != sorted(
        normalized_logs,
        key=lambda item: (
            item["log_index"]
        ),
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt matching "
            "Transfer logs are not ordered"
        )

    matched_indexes = [
        item["log_index"]
        for item in normalized_logs
    ]

    if len(set(matched_indexes)) != len(
        matched_indexes
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt contains "
            "duplicate matching Transfer indexes"
        )

    evidence_indexes = receipt.get(
        "matched_transfer_log_indexes"
    )

    if not isinstance(
        evidence_indexes,
        list,
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt matching "
            "Transfer index evidence is missing"
        )

    normalized_evidence_indexes = [
        _completion_integer(
            value,
            field_name=(
                "receipt.matched_transfer_"
                f"log_indexes.{position}"
            ),
        )
        for position, value in enumerate(
            evidence_indexes
        )
    ]

    if normalized_evidence_indexes != (
        matched_indexes
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt matching "
            "Transfer indexes mismatch"
        )

    matched_amounts = [
        item["amount_raw"]
        for item in normalized_logs
    ]

    evidence_amounts = receipt.get(
        "matched_transfer_amounts_raw"
    )

    if not isinstance(
        evidence_amounts,
        list,
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt matching "
            "Transfer amount evidence is missing"
        )

    normalized_evidence_amounts = [
        _completion_integer(
            value,
            field_name=(
                "receipt.matched_transfer_"
                f"amounts_raw.{position}"
            ),
        )
        for position, value in enumerate(
            evidence_amounts
        )
    ]

    if normalized_evidence_amounts != (
        matched_amounts
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt matching "
            "Transfer amounts mismatch"
        )

    matched_total_raw = sum(
        matched_amounts
    )

    if matched_total_raw != expected_raw:
        raise NegativeBybitFlowError(
            "Settlement wallet receipt matching "
            "Transfer aggregate amount mismatch"
        )

    if (
        _completion_integer(
            receipt.get(
                "matched_transfer_total_raw"
            ),
            field_name=(
                "receipt.matched_transfer_"
                "total_raw"
            ),
        )
        != matched_total_raw
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt matching "
            "Transfer total evidence mismatch"
        )

    destination_address = str(
        withdrawal_snapshot["address"]
    ).strip().lower()

    if (
        not destination_address.startswith(
            "0x"
        )
        or len(destination_address) != 42
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt destination "
            "address is invalid"
        )

    destination_topic = (
        "0x"
        + ("0" * 24)
        + destination_address[2:]
    )

    expected_logs_fingerprint = (
        _payload_fingerprint(
            {
                "schema": (
                    "negative_settlement_wallet_"
                    "matched_logs_v1"
                ),
                "tx_hash": (
                    _normalized_hex(
                        withdrawal_tx_hash
                    )
                ),
                "contract": str(
                    settings.BSC_USDT_CONTRACT
                ).strip().lower(),
                "destination_topic": (
                    destination_topic
                ),
                "logs": [
                    {
                        "log_index": (
                            item["log_index"]
                        ),
                        "amount_raw": str(
                            item["amount_raw"]
                        ),
                    }
                    for item
                    in normalized_logs
                ],
            }
        )
    )

    observed_logs_fingerprint = (
        _required_text(
            receipt.get(
                "matched_transfer_logs_fingerprint"
            ),
            field_name=(
                "receipt.matched_transfer_"
                "logs_fingerprint"
            ),
        ).lower()
    )

    if (
        observed_logs_fingerprint
        != expected_logs_fingerprint
    ):
        raise NegativeBybitFlowError(
            "Settlement wallet receipt matching "
            "Transfer log fingerprint mismatch"
        )

    return {
        "settlement_batch_id": int(
            settlement_batch.id
        ),
        "flow_id": int(flow.id),
        "fund_id": int(flow.fund_id),
        "withdrawal_snapshot": (
            withdrawal_snapshot
        ),
        "universal_intent": deepcopy(
            universal_intent
        ),
        "universal_reconciliation": (
            deepcopy(
                universal_reconciliation
            )
        ),
        "master_barrier": deepcopy(
            master_barrier
        ),
        "withdrawal_intent": deepcopy(
            withdrawal_intent
        ),
        "withdrawal_reconciliation": (
            deepcopy(
                withdrawal_reconciliation
            )
        ),
        "receipt": deepcopy(receipt),
        "withdrawal_tx_hash": (
            withdrawal_tx_hash
        ),
        "expected_amount_usdt": (
            expected_amount
        ),
        "confirmations": (
            observed_confirmations
        ),
        "receipt_block_number": (
            receipt_block_number
        ),
        "balance_before_usdt": (
            before_usdt
        ),
        "balance_after_usdt": (
            after_usdt
        ),
    }


def _validate_completion_wallet(
    db: Session,
    *,
    flow: FundNegativeBybitFlow,
    snapshot: dict[str, Any],
) -> None:
    wallet = _get_active_settlement_wallet(
        db,
        fund_id=int(flow.fund_id),
    )

    withdrawal_snapshot = snapshot[
        "withdrawal_snapshot"
    ]

    if int(wallet.id) != int(
        withdrawal_snapshot[
            "settlement_wallet_id"
        ]
    ):
        raise NegativeBybitFlowError(
            "Active settlement wallet ID changed "
            "before cash-delivery completion"
        )

    if (
        str(wallet.address)
        .strip()
        .lower()
        != withdrawal_snapshot[
            "address"
        ].lower()
    ):
        raise NegativeBybitFlowError(
            "Active settlement wallet address "
            "changed before cash-delivery "
            "completion"
        )


def _completion_fingerprints(
    snapshot: dict[str, Any],
) -> dict[str, str]:
    return {
        "universal_reconciliation": (
            _payload_fingerprint(
                snapshot[
                    "universal_reconciliation"
                ]
            )
        ),
        "master_balance_barrier": (
            _payload_fingerprint(
                snapshot[
                    "master_barrier"
                ]
            )
        ),
        "withdrawal_reconciliation": (
            _payload_fingerprint(
                snapshot[
                    "withdrawal_reconciliation"
                ]
            )
        ),
        "settlement_wallet_receipt": (
            _payload_fingerprint(
                snapshot["receipt"]
            )
        ),
    }


def _completion_identity(
    *,
    flow: FundNegativeBybitFlow,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    withdrawal_snapshot = snapshot[
        "withdrawal_snapshot"
    ]

    address_hash = hashlib.sha256(
        withdrawal_snapshot[
            "address"
        ].strip().lower().encode(
            "utf-8"
        )
    ).hexdigest()

    return {
        "settlement_batch_id": str(
            int(flow.settlement_batch_id)
        ),
        "flow_id": str(int(flow.id)),
        "fund_id": str(int(flow.fund_id)),
        "settlement_wallet_id": str(
            int(
                withdrawal_snapshot[
                    "settlement_wallet_id"
                ]
            )
        ),
        "settlement_wallet_address_sha256": (
            address_hash
        ),
        "universal_transfer_id": (
            _required_text(
                flow.universal_transfer_id,
                field_name=(
                    "flow.universal_transfer_id"
                ),
            )
        ),
        "withdrawal_request_id": (
            withdrawal_snapshot[
                "request_id"
            ]
        ),
        "withdrawal_id": _required_text(
            flow.withdrawal_id,
            field_name="flow.withdrawal_id",
        ),
        "withdrawal_tx_hash": (
            snapshot[
                "withdrawal_tx_hash"
            ]
        ),
        "required_master_usdt": (
            _decimal_text(
                Decimal(
                    flow.required_master_usdt
                )
            )
        ),
        "withdrawal_amount_usdt": (
            _decimal_text(
                snapshot[
                    "expected_amount_usdt"
                ]
            )
        ),
        "withdrawal_fee_usdt": (
            _decimal_text(
                Decimal(
                    flow
                    .bybit_withdrawal_fee_usdt
                )
            )
        ),
        "retained_fees_usdt": (
            _decimal_text(
                Decimal(
                    flow.retained_fees_usdt
                )
            )
        ),
        "balance_before_usdt": (
            _decimal_text(
                snapshot[
                    "balance_before_usdt"
                ]
            )
        ),
        "balance_after_usdt": (
            _decimal_text(
                snapshot[
                    "balance_after_usdt"
                ]
            )
        ),
        "confirmations": int(
            snapshot["confirmations"]
        ),
        "receipt_block_number": int(
            snapshot[
                "receipt_block_number"
            ]
        ),
    }


def _complete_negative_cash_delivery_once(
    db: Session,
    *,
    settlement_batch,
    flow: FundNegativeBybitFlow,
    amounts: dict[str, Decimal],
    resolved_now: datetime,
    status_before: str | None,
    settlement_status_before: str | None,
) -> NegativeBybitFlowResult:
    if str(flow.status) != (
        BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_CONFIRMED
    ):
        raise NegativeBybitFlowError(
            "Cash-delivery completion requires "
            "confirmed settlement wallet receipt"
        )

    if str(settlement_batch.status) != (
        BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_RECONCILING
    ):
        raise NegativeBybitFlowError(
            "Cash-delivery completion has "
            "incompatible settlement batch status"
        )

    snapshot = _cash_delivery_snapshot(
        flow=flow,
        settlement_batch=settlement_batch,
        amounts=amounts,
        allowed_flow_statuses={
            BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_CONFIRMED
        },
    )

    _validate_completion_wallet(
        db,
        flow=flow,
        snapshot=snapshot,
    )

    fingerprints = (
        _completion_fingerprints(
            snapshot
        )
    )

    identity = _completion_identity(
        flow=flow,
        snapshot=snapshot,
    )

    completion = {
        "schema": (
            CASH_DELIVERY_COMPLETION_SCHEMA
        ),
        "policy_version": POLICY_VERSION,
        "state": "completed",
        "completed_at": (
            resolved_now.isoformat()
        ),
        **identity,
        "evidence_fingerprints": (
            fingerprints
        ),
        "db_only_transition": True,
        "bybit_get_count": 0,
        "bybit_post_count": 0,
        "bsc_rpc_read_count": 0,
        "seller_payouts_started": False,
        "accounting_finalized": False,
        "reserve_release_allowed": False,
        "pricing_unlock_allowed": False,
        "next_stage": (
            "negative_payout_pipeline"
        ),
    }

    prior_reconciliation = (
        deepcopy(flow.reconciliation_json)
        if isinstance(
            flow.reconciliation_json,
            dict,
        )
        else {}
    )

    prior_reconciliation[
        "cash_delivery_completion"
    ] = completion

    flow.reconciliation_json = (
        _json_dict(
            prior_reconciliation
        )
    )

    flow.report_json = _json_dict(
        {
            "schema": (
                CASH_DELIVERY_REPORT_SCHEMA
            ),
            "policy_version": (
                POLICY_VERSION
            ),
            "state": "completed",
            "completed_at": (
                resolved_now.isoformat()
            ),
            **identity,
            "evidence_fingerprints": (
                fingerprints
            ),
            "cash_ready_for_payout": True,
            "seller_payouts_started": False,
            "accounting_finalized": False,
            "reserve_release_allowed": False,
            "pricing_unlock_allowed": False,
            "next_stage": (
                "negative_payout_pipeline"
            ),
        }
    )

    flow.status = BYBIT_FLOW_STATUS_COMPLETED
    flow.error = None
    flow.updated_at = resolved_now

    settlement_batch.status = (
        BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT
    )
    settlement_batch.error = None
    settlement_batch.updated_at = (
        resolved_now
    )

    db.add(flow)
    db.add(settlement_batch)
    db.flush()

    result = _step_result(
        ok=True,
        transition=(
            "complete_negative_cash_delivery"
        ),
        settlement_batch=settlement_batch,
        flow=flow,
        status_before=status_before,
        settlement_status_before=(
            settlement_status_before
        ),
        diagnostics={
            "did_bybit_post": False,
            "bybit_post_count": 0,
            "bybit_get_count": 0,
            "bsc_rpc_read_count": 0,
            "db_only_transition": True,
            "cash_ready_for_payout": True,
            "seller_payouts_started": False,
            "accounting_finalized": False,
            "reserve_release_allowed": False,
            "pricing_unlock_allowed": False,
            "next_transition": (
                "negative_payout_pipeline"
            ),
        },
    )

    db.commit()

    return result


def _completed_negative_cash_delivery_once(
    db: Session,
    *,
    settlement_batch,
    flow: FundNegativeBybitFlow,
    amounts: dict[str, Decimal],
    status_before: str | None,
    settlement_status_before: str | None,
) -> NegativeBybitFlowResult:
    if str(flow.status) != (
        BYBIT_FLOW_STATUS_COMPLETED
    ):
        raise NegativeBybitFlowError(
            "Completed cash-delivery flow status "
            "mismatch"
        )

    if str(settlement_batch.status) != (
        BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT
    ):
        raise NegativeBybitFlowError(
            "Completed cash-delivery settlement "
            "batch status mismatch"
        )

    snapshot = _cash_delivery_snapshot(
        flow=flow,
        settlement_batch=settlement_batch,
        amounts=amounts,
        allowed_flow_statuses={
            BYBIT_FLOW_STATUS_COMPLETED
        },
    )

    _validate_completion_wallet(
        db,
        flow=flow,
        snapshot=snapshot,
    )

    fingerprints = (
        _completion_fingerprints(
            snapshot
        )
    )

    identity = _completion_identity(
        flow=flow,
        snapshot=snapshot,
    )

    reconciliation = (
        flow.reconciliation_json
    )

    if not isinstance(
        reconciliation,
        dict,
    ):
        raise NegativeBybitFlowError(
            "Completed cash-delivery "
            "reconciliation is missing"
        )

    completion = reconciliation.get(
        "cash_delivery_completion"
    )

    if not isinstance(completion, dict):
        raise NegativeBybitFlowError(
            "Cash-delivery completion evidence "
            "is missing"
        )

    if completion.get("schema") != (
        CASH_DELIVERY_COMPLETION_SCHEMA
    ):
        raise NegativeBybitFlowError(
            "Cash-delivery completion schema "
            "mismatch"
        )

    if (
        completion.get("policy_version")
        != POLICY_VERSION
    ):
        raise NegativeBybitFlowError(
            "Cash-delivery completion policy "
            "mismatch"
        )

    if completion.get("state") != "completed":
        raise NegativeBybitFlowError(
            "Cash-delivery completion state "
            "mismatch"
        )

    _aware_utc_datetime(
        completion.get("completed_at"),
        field_name=(
            "cash_delivery_completion."
            "completed_at"
        ),
    )

    for key, expected in identity.items():
        actual = completion.get(key)

        if actual != expected:
            raise NegativeBybitFlowError(
                "Cash-delivery completion identity "
                f"mismatch: {key}"
            )

    if (
        completion.get(
            "evidence_fingerprints"
        )
        != fingerprints
    ):
        raise NegativeBybitFlowError(
            "Cash-delivery completion evidence "
            "fingerprints mismatch"
        )

    if (
        completion.get(
            "db_only_transition"
        )
        is not True
    ):
        raise NegativeBybitFlowError(
            "Cash-delivery completion DB-only "
            "marker is missing"
        )

    if (
        completion.get(
            "seller_payouts_started"
        )
        is not False
    ):
        raise NegativeBybitFlowError(
            "Cash-delivery completion incorrectly "
            "claims seller payout execution"
        )

    if (
        completion.get(
            "accounting_finalized"
        )
        is not False
    ):
        raise NegativeBybitFlowError(
            "Cash-delivery completion incorrectly "
            "claims accounting finalization"
        )

    report = flow.report_json

    if not isinstance(report, dict):
        raise NegativeBybitFlowError(
            "Completed cash-delivery report "
            "is missing"
        )

    if report.get("schema") != (
        CASH_DELIVERY_REPORT_SCHEMA
    ):
        raise NegativeBybitFlowError(
            "Cash-delivery report schema mismatch"
        )

    if report.get("policy_version") != (
        POLICY_VERSION
    ):
        raise NegativeBybitFlowError(
            "Cash-delivery report policy mismatch"
        )

    if report.get("state") != "completed":
        raise NegativeBybitFlowError(
            "Cash-delivery report state mismatch"
        )

    for key, expected in identity.items():
        if report.get(key) != expected:
            raise NegativeBybitFlowError(
                "Cash-delivery report identity "
                f"mismatch: {key}"
            )

    if (
        report.get(
            "evidence_fingerprints"
        )
        != fingerprints
    ):
        raise NegativeBybitFlowError(
            "Cash-delivery report evidence "
            "fingerprints mismatch"
        )

    if (
        report.get("cash_ready_for_payout")
        is not True
    ):
        raise NegativeBybitFlowError(
            "Cash-delivery report cash-ready "
            "marker is missing"
        )

    if (
        report.get("seller_payouts_started")
        is not False
        or report.get(
            "accounting_finalized"
        )
        is not False
        or report.get(
            "reserve_release_allowed"
        )
        is not False
        or report.get(
            "pricing_unlock_allowed"
        )
        is not False
    ):
        raise NegativeBybitFlowError(
            "Cash-delivery report violates strict "
            "finalization boundary"
        )

    result = _step_result(
        ok=True,
        transition=(
            "negative_cash_delivery_"
            "already_completed"
        ),
        settlement_batch=settlement_batch,
        flow=flow,
        status_before=status_before,
        settlement_status_before=(
            settlement_status_before
        ),
        idempotent=True,
        diagnostics={
            "did_bybit_post": False,
            "bybit_post_count": 0,
            "bybit_get_count": 0,
            "bsc_rpc_read_count": 0,
            "db_only_transition": True,
            "cash_ready_for_payout": True,
            "seller_payouts_started": False,
            "accounting_finalized": False,
            "reserve_release_allowed": False,
            "pricing_unlock_allowed": False,
            "next_transition": (
                "negative_payout_pipeline"
            ),
        },
    )

    db.commit()

    return result


def resume_negative_bybit_flow_once(
    db: Session,
    *,
    settlement_batch_id: int,
    bybit_client: BybitV5Client,
    fund_sub_uid: str,
    master_uid: str,
    now: datetime | None = None,
) -> NegativeBybitFlowResult:
    resolved_now = _now(now)

    settlement_batch = _lock_settlement_batch(
        db,
        settlement_batch_id=int(
            settlement_batch_id
        ),
    )

    sale_batch = _lock_sale_batch_for_settlement(
        db,
        settlement_batch_id=int(
            settlement_batch_id
        ),
    )

    flow = _lock_existing_flow(
        db,
        settlement_batch_id=int(
            settlement_batch_id
        ),
    )

    settlement_status_before = str(
        settlement_batch.status
    )

    status_before = (
        str(flow.status)
        if flow is not None
        else None
    )

    try:
        _validate_sale_batch_input(
            settlement_batch=settlement_batch,
            sale_batch=sale_batch,
        )

        amounts = _validate_target_fields(
            settlement_batch=settlement_batch,
            sale_batch=sale_batch,
        )

        fund = _get_fund(
            db,
            fund_id=int(settlement_batch.fund_id),
        )

        # Transition 1:
        # create the durable flow row only.
        # No Bybit GET and no Bybit POST.
        if flow is None:
            flow = _new_or_existing_flow(
                db,
                existing=None,
                settlement_batch=settlement_batch,
                sale_batch=sale_batch,
                amounts=amounts,
            )

            flow.updated_at = resolved_now

            settlement_batch.status = (
                BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
            )
            settlement_batch.error = None
            settlement_batch.updated_at = (
                resolved_now
            )

            db.add(flow)
            db.add(settlement_batch)
            db.flush()

            result = _step_result(
                ok=True,
                transition="create_or_load_flow",
                settlement_batch=settlement_batch,
                flow=flow,
                status_before=None,
                settlement_status_before=(
                    settlement_status_before
                ),
                diagnostics={
                    "fund_code": str(fund.code),
                    "created": True,
                    "bybit_get_count": 0,
                },
            )

            db.commit()

            return result

        _validate_existing_flow(
            flow=flow,
            settlement_batch=settlement_batch,
            sale_batch=sale_batch,
            amounts=amounts,
        )

        if str(flow.status) == (
            BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
        ):
            if str(settlement_batch.status) != (
                BATCH_STATUS_FAILED_REQUIRES_REVIEW
            ):
                raise NegativeBybitFlowError(
                    "Failed flow has incompatible "
                    "settlement batch status"
                )

            result = _step_result(
                ok=False,
                transition=(
                    "failed_requires_review_"
                    "already_recorded"
                ),
                settlement_batch=settlement_batch,
                flow=flow,
                status_before=status_before,
                settlement_status_before=(
                    settlement_status_before
                ),
                idempotent=True,
                error=flow.error,
                diagnostics={
                    "did_bybit_post": False,
                    "bybit_post_count": 0,
                    "bybit_get_count": 0,
                    "bsc_rpc_read_count": 0,
                    "no_automatic_resend": True,
                    "reserve_release_allowed": False,
                    "pricing_unlock_allowed": False,
                },
            )

            db.commit()

            return result

        if isinstance(
            flow.universal_transfer_intent_json,
            dict,
        ):
            intent_state = str(
                flow
                .universal_transfer_intent_json
                .get("state")
                or ""
            ).strip()

            if intent_state == "prepared":
                return _submit_universal_transfer_once(
                    db,
                    settlement_batch=(
                        settlement_batch
                    ),
                    flow=flow,
                    fund=fund,
                    bybit_client=bybit_client,
                    resolved_now=resolved_now,
                    status_before=status_before,
                    settlement_status_before=(
                        settlement_status_before
                    ),
                )

            if intent_state in {
                "submitting",
                "reconciling",
            }:
                return (
                    _reconcile_universal_transfer_once(
                        db,
                        settlement_batch=(
                            settlement_batch
                        ),
                        flow=flow,
                        bybit_client=(
                            bybit_client
                        ),
                        resolved_now=(
                            resolved_now
                        ),
                        status_before=(
                            status_before
                        ),
                        settlement_status_before=(
                            settlement_status_before
                        ),
                    )
                )

            if intent_state == "confirmed":
                _validate_prepared_intent(
                    flow=flow,
                    intent=(
                        flow
                        .universal_transfer_intent_json
                    ),
                    allowed_states={"confirmed"},
                )

                if isinstance(
                    flow.withdrawal_intent_json,
                    dict,
                ):
                    withdrawal_intent = (
                        flow
                        .withdrawal_intent_json
                    )

                    withdrawal_state = str(
                        withdrawal_intent.get(
                            "state"
                        )
                        or ""
                    ).strip()

                    if withdrawal_state == "prepared":
                        _validate_withdrawal_intent(
                            flow=flow,
                            intent=withdrawal_intent,
                            allowed_states={
                                "prepared"
                            },
                        )

                        if str(flow.status) != (
                            BYBIT_FLOW_STATUS_WITHDRAWAL_INTENT_PREPARED
                        ):
                            raise NegativeBybitFlowError(
                                "Prepared withdrawal "
                                "intent has incompatible "
                                "flow status: "
                                f"{flow.status}"
                            )

                        return _submit_withdrawal_once(
                            db,
                            settlement_batch=(
                                settlement_batch
                            ),
                            flow=flow,
                            bybit_client=(
                                bybit_client
                            ),
                            resolved_now=(
                                resolved_now
                            ),
                            status_before=(
                                status_before
                            ),
                            settlement_status_before=(
                                settlement_status_before
                            ),
                        )

                    if withdrawal_state in {
                        "submitting",
                        "reconciling",
                    }:
                        return (
                            _reconcile_withdrawal_once(
                                db,
                                settlement_batch=(
                                    settlement_batch
                                ),
                                flow=flow,
                                bybit_client=(
                                    bybit_client
                                ),
                                resolved_now=(
                                    resolved_now
                                ),
                                status_before=(
                                    status_before
                                ),
                                settlement_status_before=(
                                    settlement_status_before
                                ),
                            )
                        )

                    if withdrawal_state == "confirmed":
                        _validate_withdrawal_intent(
                            flow=flow,
                            intent=withdrawal_intent,
                            allowed_states={
                                "confirmed"
                            },
                        )

                        if str(flow.status) in {
                            BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILED,
                            BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_PENDING,
                        }:
                            return (
                                _reconcile_settlement_wallet_receipt_once(
                                    db,
                                    settlement_batch=(
                                        settlement_batch
                                    ),
                                    flow=flow,
                                    resolved_now=(
                                        resolved_now
                                    ),
                                    status_before=(
                                        status_before
                                    ),
                                    settlement_status_before=(
                                        settlement_status_before
                                    ),
                                )
                            )

                        if str(flow.status) == (
                            BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_CONFIRMED
                        ):
                            return (
                                _complete_negative_cash_delivery_once(
                                    db,
                                    settlement_batch=(
                                        settlement_batch
                                    ),
                                    flow=flow,
                                    amounts=amounts,
                                    resolved_now=(
                                        resolved_now
                                    ),
                                    status_before=(
                                        status_before
                                    ),
                                    settlement_status_before=(
                                        settlement_status_before
                                    ),
                                )
                            )

                        if str(flow.status) == (
                            BYBIT_FLOW_STATUS_COMPLETED
                        ):
                            return (
                                _completed_negative_cash_delivery_once(
                                    db,
                                    settlement_batch=(
                                        settlement_batch
                                    ),
                                    flow=flow,
                                    amounts=amounts,
                                    status_before=(
                                        status_before
                                    ),
                                    settlement_status_before=(
                                        settlement_status_before
                                    ),
                                )
                            )

                        raise NegativeBybitFlowError(
                            "Confirmed withdrawal intent "
                            "has incompatible flow status: "
                            f"{flow.status}"
                        )

                    if withdrawal_state == (
                        "failed_requires_review"
                    ):
                        if str(flow.status) != (
                            BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
                        ):
                            raise NegativeBybitFlowError(
                                "Failed withdrawal "
                                "intent has incompatible "
                                "flow status"
                            )

                        result = _step_result(
                            ok=False,
                            transition=(
                                "failed_requires_review_"
                                "already_recorded"
                            ),
                            settlement_batch=(
                                settlement_batch
                            ),
                            flow=flow,
                            status_before=status_before,
                            settlement_status_before=(
                                settlement_status_before
                            ),
                            idempotent=True,
                            error=flow.error,
                            diagnostics={
                                "did_bybit_post": False,
                                "bybit_post_count": 0,
                                "bybit_get_count": 0,
                                "no_automatic_resend": True,
                                "reserve_release_allowed": False,
                                "pricing_unlock_allowed": False,
                            },
                        )

                        db.commit()

                        return result

                    raise NegativeBybitFlowError(
                        "Unsupported withdrawal "
                        "intent state: "
                        f"{withdrawal_state or 'empty'}"
                    )

                if (
                    flow.withdrawal_intent_json
                    is not None
                ):
                    raise NegativeBybitFlowError(
                        "Withdrawal intent must be "
                        "a JSON object"
                    )

                if str(flow.status) == (
                    BYBIT_FLOW_STATUS_MASTER_BALANCE_CONFIRMED
                ):
                    return (
                        _prepare_withdrawal_intent_once(
                            db,
                            settlement_batch=(
                                settlement_batch
                            ),
                            flow=flow,
                            bybit_client=(
                                bybit_client
                            ),
                            resolved_now=(
                                resolved_now
                            ),
                            status_before=(
                                status_before
                            ),
                            settlement_status_before=(
                                settlement_status_before
                            ),
                        )
                    )

                if str(flow.status) != (
                    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED
                ):
                    raise NegativeBybitFlowError(
                        "Confirmed Universal Transfer "
                        "has incompatible flow status: "
                        f"{flow.status}"
                    )

                return (
                    _master_transferable_balance_barrier_once(
                        db,
                        settlement_batch=(
                            settlement_batch
                        ),
                        flow=flow,
                        bybit_client=(
                            bybit_client
                        ),
                        master_uid=master_uid,
                        resolved_now=(
                            resolved_now
                        ),
                        status_before=(
                            status_before
                        ),
                        settlement_status_before=(
                            settlement_status_before
                        ),
                    )
                )

            if intent_state == (
                "failed_requires_review"
            ):
                _validate_prepared_intent(
                    flow=flow,
                    intent=(
                        flow
                        .universal_transfer_intent_json
                    ),
                    allowed_states={
                        "failed_requires_review"
                    },
                )

                if str(flow.status) != (
                    BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
                ):
                    raise NegativeBybitFlowError(
                        "Failed Universal Transfer "
                        "intent has incompatible "
                        f"flow status: {flow.status}"
                    )

                result = _step_result(
                    ok=False,
                    transition=(
                        "failed_requires_review_"
                        "already_recorded"
                    ),
                    settlement_batch=(
                        settlement_batch
                    ),
                    flow=flow,
                    status_before=status_before,
                    settlement_status_before=(
                        settlement_status_before
                    ),
                    idempotent=True,
                    error=flow.error,
                    diagnostics={
                        "did_bybit_post": False,
                        "bybit_post_count": 0,
                        "bybit_get_count": 0,
                        "no_automatic_resend": True,
                        "reserve_release_allowed": False,
                        "pricing_unlock_allowed": False,
                    },
                )

                db.commit()

                return result

            raise NegativeBybitFlowError(
                "Unsupported Universal Transfer "
                f"intent state: {intent_state or 'empty'}"
            )

        if (
            flow.universal_transfer_intent_json
            is not None
        ):
            raise NegativeBybitFlowError(
                "Universal Transfer intent must "
                "be a JSON object"
            )

        # A legacy attempt without a v2 intent cannot
        # safely be resumed or resent automatically.
        if _has_transfer_evidence(flow):
            raise NegativeBybitFlowError(
                "Universal Transfer evidence exists "
                "without durable v2 intent"
            )

        if flow.status not in {
            BYBIT_FLOW_STATUS_CREATED,
            BYBIT_FLOW_STATUS_PREFLIGHT_PASSED,
        }:
            raise NegativeBybitFlowError(
                "Flow status cannot prepare "
                "Universal Transfer intent: "
                f"{flow.status}"
            )

        clean_fund_sub_uid = _required_text(
            fund_sub_uid,
            field_name="fund_sub_uid",
        )

        clean_master_uid = _required_text(
            master_uid,
            field_name="master_uid",
        )

        coin = _required_text(
            settings.NEGATIVE_NET_BYBIT_FLOW_COIN,
            field_name=(
                "NEGATIVE_NET_BYBIT_FLOW_COIN"
            ),
        ).upper()

        amount_text, amount_actual = (
            universal_transfer_actual_amount(
                required_master_usdt=amounts[
                    "required_master_usdt"
                ],
            )
        )

        prepare_settlement_batch_id = int(
            settlement_batch.id
        )
        prepare_flow_id = int(flow.id)
        expected_required_master_usdt = Decimal(
            amounts["required_master_usdt"]
        )

        # Release settlement, sale and flow row locks
        # before any Bybit HTTP GET.
        db.commit()

        route = (
            choose_universal_transfer_account_route(
                bybit_client,
                coin=coin,
                amount_usdt=amount_actual,
                from_member_id=(
                    clean_fund_sub_uid
                ),
                to_member_id=clean_master_uid,
            )
        )

        # Re-lock and revalidate all immutable inputs
        # before persisting the prepared intent.
        settlement_batch = _lock_settlement_batch(
            db,
            settlement_batch_id=(
                prepare_settlement_batch_id
            ),
        )

        sale_batch = (
            _lock_sale_batch_for_settlement(
                db,
                settlement_batch_id=(
                    prepare_settlement_batch_id
                ),
            )
        )

        flow = _lock_existing_flow(
            db,
            settlement_batch_id=(
                prepare_settlement_batch_id
            ),
        )

        if flow is None:
            raise NegativeBybitFlowError(
                "Negative Bybit flow disappeared "
                "during Universal Transfer prepare"
            )

        if int(flow.id) != prepare_flow_id:
            raise NegativeBybitFlowError(
                "Negative Bybit flow identity "
                "changed during prepare"
            )

        _validate_sale_batch_input(
            settlement_batch=settlement_batch,
            sale_batch=sale_batch,
        )

        current_amounts = _validate_target_fields(
            settlement_batch=settlement_batch,
            sale_batch=sale_batch,
        )

        _validate_existing_flow(
            flow=flow,
            settlement_batch=settlement_batch,
            sale_batch=sale_batch,
            amounts=current_amounts,
        )

        if not _same_decimal(
            current_amounts[
                "required_master_usdt"
            ],
            expected_required_master_usdt,
        ):
            raise NegativeBybitFlowError(
                "required_master_usdt changed "
                "during Universal Transfer prepare"
            )

        amounts = current_amounts

        concurrent_intent = (
            flow.universal_transfer_intent_json
        )

        if isinstance(
            concurrent_intent,
            dict,
        ):
            concurrent_state = str(
                concurrent_intent.get("state")
                or ""
            ).strip()

            _validate_prepared_intent(
                flow=flow,
                intent=concurrent_intent,
                allowed_states={
                    "prepared",
                    "submitting",
                    "reconciling",
                    "confirmed",
                },
            )

            result = _step_result(
                ok=concurrent_state in {
                    "prepared",
                    "confirmed",
                },
                transition=(
                    "prepare_universal_transfer_"
                    "concurrent_state_detected"
                ),
                settlement_batch=(
                    settlement_batch
                ),
                flow=flow,
                status_before=status_before,
                settlement_status_before=(
                    settlement_status_before
                ),
                idempotent=True,
                diagnostics={
                    "concurrent_intent_state": (
                        concurrent_state
                    ),
                    "did_bybit_post": False,
                    "bybit_post_count": 0,
                    "bybit_get_count": len(
                        route.get("checked") or []
                    ),
                    "no_automatic_resend": (
                        concurrent_state
                        in {
                            "submitting",
                            "reconciling",
                        }
                    ),
                },
            )

            db.commit()

            return result

        if concurrent_intent is not None:
            raise NegativeBybitFlowError(
                "Universal Transfer intent must "
                "be a JSON object"
            )

        if _has_transfer_evidence(flow):
            raise NegativeBybitFlowError(
                "Universal Transfer evidence "
                "appeared during prepare"
            )

        if flow.status not in {
            BYBIT_FLOW_STATUS_CREATED,
            BYBIT_FLOW_STATUS_PREFLIGHT_PASSED,
        }:
            raise NegativeBybitFlowError(
                "Flow status changed during "
                "Universal Transfer prepare: "
                f"{flow.status}"
            )

        from_account_type = _required_text(
            route.get("from_account_type"),
            field_name=(
                "route.from_account_type"
            ),
        ).upper()

        to_account_type = _required_text(
            route.get("to_account_type"),
            field_name="route.to_account_type",
        ).upper()

        transfer_id = (
            deterministic_universal_transfer_id(
                settlement_batch_id=int(
                    settlement_batch.id
                ),
                fund_id=int(
                    settlement_batch.fund_id
                ),
                universal_transfer_amount_usdt=(
                    amount_actual
                ),
                from_member_id=(
                    clean_fund_sub_uid
                ),
                to_member_id=clean_master_uid,
                from_account_type=(
                    from_account_type
                ),
                to_account_type=to_account_type,
            )
        )

        intent = _build_intent(
            settlement_batch_id=int(
                settlement_batch.id
            ),
            fund_id=int(
                settlement_batch.fund_id
            ),
            transfer_id=transfer_id,
            coin=coin,
            amount=amount_text,
            from_member_id=clean_fund_sub_uid,
            to_member_id=clean_master_uid,
            from_account_type=(
                from_account_type
            ),
            to_account_type=to_account_type,
            prepared_at=resolved_now,
        )

        flow.from_sub_uid = clean_fund_sub_uid
        flow.to_master_uid = clean_master_uid
        flow.from_account_type = (
            from_account_type
        )
        flow.to_account_type = to_account_type

        flow.universal_transfer_id = (
            transfer_id
        )
        flow.universal_transfer_amount_usdt = (
            amount_actual
        )
        flow.universal_transfer_coin = coin
        flow.universal_transfer_intent_json = (
            intent
        )

        flow.preflight_passed = True
        flow.preflight_error = None
        flow.preflight_json = _json_dict(
            {
                "schema": (
                    "negative_universal_transfer_"
                    "preflight_v2"
                ),
                "policy_version": POLICY_VERSION,
                "route": route,
                "amount": amount_text,
                "transfer_id": transfer_id,
                "payload_fingerprint": intent[
                    "payload_fingerprint"
                ],
                "external_action": False,
            }
        )

        flow.status = (
            BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_INTENT_PREPARED
        )
        flow.error = None
        flow.updated_at = resolved_now

        settlement_batch.status = (
            BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
        )
        settlement_batch.error = None
        settlement_batch.updated_at = (
            resolved_now
        )

        db.add(flow)
        db.add(settlement_batch)
        db.flush()

        # Durable boundary:
        # immutable intent and deterministic transferId
        # are committed before any future POST.
        result = _step_result(
            ok=True,
            transition=(
                "prepare_universal_transfer_intent"
            ),
            settlement_batch=settlement_batch,
            flow=flow,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            diagnostics={
                "fund_code": str(fund.code),
                "intent_state": "prepared",
                "payload_fingerprint": intent[
                    "payload_fingerprint"
                ],
                "bybit_get_count": 1,
            },
        )

        db.commit()

        return result

    except (
        NegativeBybitFlowError,
        BybitAssetFlowError,
    ) as exc:
        if flow is None:
            db.rollback()
            raise

        prior_reconciliation = (
            deepcopy(
                flow.reconciliation_json
            )
            if isinstance(
                flow.reconciliation_json,
                dict,
            )
            else None
        )

        result = _set_failed(
            flow=flow,
            settlement_batch=settlement_batch,
            fund=None,
            status_before=status_before,
            settlement_status_before=(
                settlement_status_before
            ),
            error=str(exc),
            now=resolved_now,
            diagnostics={
                "transition": (
                    "failed_requires_review"
                ),
                "did_bybit_post": False,
                "bybit_post_count": 0,
                "reserve_release_allowed": False,
                "pricing_unlock_allowed": False,
            },
        )

        if prior_reconciliation is not None:
            failure_reconciliation = (
                deepcopy(
                    flow.reconciliation_json
                )
                if isinstance(
                    flow.reconciliation_json,
                    dict,
                )
                else {}
            )

            merged_reconciliation = (
                prior_reconciliation
            )
            merged_reconciliation.update(
                failure_reconciliation
            )

            flow.reconciliation_json = (
                _json_dict(
                    merged_reconciliation
                )
            )

            db.add(flow)

        db.flush()
        db.commit()

        return result