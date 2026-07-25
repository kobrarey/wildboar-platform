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
    create_universal_transfer,
    query_universal_transfer,
)
from app.bybit.client import (
    BybitApiError,
    BybitV5Client,
)
from app.config import settings
from app.models import FundNegativeBybitFlow
from app.operation_guard.hooks import (
    require_bybit_universal_transfer_guard,
)
from app.operation_guard.service import (
    OperationGuardBlockedError,
)
from app.settlement.negative_bybit_flow import (
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
    universal_transfer_actual_amount,
)
from app.settlement.negative_bybit_flow_types import (
    NegativeBybitFlowError,
    NegativeBybitFlowResult,
    _json_dict,
)
from app.settlement.statuses import (
    BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING,
    BYBIT_FLOW_STATUS_CREATED,
    BYBIT_FLOW_STATUS_PREFLIGHT_PASSED,
    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_INTENT_PREPARED,
    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILING,
    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED,
    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_SUBMITTING,
)


POLICY_VERSION = "negative_cash_delivery_v1"

UNIVERSAL_TRANSFER_INTENT_SCHEMA = (
    "negative_universal_transfer_intent_v2"
)

UNIVERSAL_TRANSFER_RECONCILIATION_SCHEMA = (
    "negative_universal_transfer_reconciliation_v2"
)


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

    if (
        created_transfer.transfer_id
        != snapshot["transfer_id"]
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer acknowledgement "
            "transfer_id mismatch"
        )

    if (
        created_transfer.coin
        != snapshot["coin"]
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer acknowledgement "
            "coin mismatch"
        )

    if not _same_decimal(
        created_transfer.amount_usdt,
        snapshot["amount_usdt"],
    ):
        raise NegativeBybitFlowError(
            "Universal Transfer acknowledgement "
            "amount mismatch"
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

                result = _step_result(
                    ok=True,
                    transition=(
                        "universal_transfer_"
                        "already_confirmed"
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
                        "did_bybit_post": False,
                        "bybit_post_count": 0,
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

        db.flush()
        db.commit()

        return result