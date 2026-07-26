from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.bybit.asset_flows import (
    BybitAssetFlowError,
    create_master_withdrawal,
    create_universal_transfer,
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
    BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING,
    BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_PENDING,
    BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_RECONCILING,
    BYBIT_FLOW_STATUS_CREATED,
    BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW,
    BYBIT_FLOW_STATUS_MASTER_BALANCE_CONFIRMED,
    BYBIT_FLOW_STATUS_PREFLIGHT_PASSED,
    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_INTENT_PREPARED,
    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILING,
    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED,
    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_SUBMITTING,
    BYBIT_FLOW_STATUS_WITHDRAWAL_INTENT_PREPARED,
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
    payload_template = {
        "requestId": request_id,
        "coin": coin,
        "chain": chain,
        "address": address,
        "amount": amount,
        "forceChain": 1,
        "feeType": int(
            settings
            .NEGATIVE_NET_WITHDRAWAL_FEE_TYPE
        ),
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
        "fee_type": int(
            settings
            .NEGATIVE_NET_WITHDRAWAL_FEE_TYPE
        ),
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
                        _validate_withdrawal_intent(
                            flow=flow,
                            intent=withdrawal_intent,
                            allowed_states={
                                "submitting",
                                "reconciling",
                            },
                        )

                        expected_status = (
                            BYBIT_FLOW_STATUS_WITHDRAWAL_SUBMITTING
                            if withdrawal_state
                            == "submitting"
                            else BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILING
                        )

                        if str(flow.status) != (
                            expected_status
                        ):
                            raise NegativeBybitFlowError(
                                "Withdrawal intent and "
                                "flow status mismatch: "
                                f"intent={withdrawal_state}, "
                                f"flow={flow.status}"
                            )

                        result = _step_result(
                            ok=False,
                            transition=(
                                "withdrawal_"
                                "reconciliation_pending"
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
                                "pending": (
                                    "withdrawal_"
                                    "reconciliation"
                                ),
                                "did_bybit_post": False,
                                "bybit_post_count": 0,
                                "bybit_get_count": 0,
                                "no_automatic_resend": True,
                                "next_transition": (
                                    "reconcile_withdrawal"
                                ),
                            },
                        )

                        db.commit()

                        return result

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