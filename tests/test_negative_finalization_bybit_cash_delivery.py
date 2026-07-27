from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

import app.settlement.negative_finalization as service
from app.settlement.statuses import (
    BYBIT_FLOW_STATUS_COMPLETED,
)


NOW = datetime(
    2026,
    7,
    26,
    17,
    0,
    tzinfo=timezone.utc,
)

SETTLEMENT_ADDRESS = (
    "0x1111111111111111111111111111111111111111"
)

TX_HASH = (
    "0x"
    + ("a" * 64)
)

RECORD_FINGERPRINT = (
    "b" * 64
)


def make_context() -> tuple[Any, Any]:
    settlement_batch = SimpleNamespace(
        id=11,
        fund_id=3,
    )

    universal_payload = {
        "transferId": "ut-11",
        "coin": "USDT",
        "amount": "31",
        "fromMemberId": "sub-3",
        "toMemberId": "master-1",
        "fromAccountType": "UNIFIED",
        "toAccountType": "FUND",
    }

    universal_reconciliation = {
        "schema": (
            service
            .BYBIT_UNIVERSAL_RECONCILIATION_SCHEMA
        ),
        "phase": "exact_transfer_id_query",
        "transfer_id": "ut-11",
        "record_found": True,
        "query_succeeded": True,
        "exact_match": True,
        "observed_status": "SUCCESS",
        "no_automatic_resend": True,
        "record": {
            "transferId": "ut-11",
            "status": "SUCCESS",
        },
    }

    universal_intent = {
        "schema": (
            service
            .BYBIT_UNIVERSAL_INTENT_SCHEMA
        ),
        "policy_version": (
            service
            .BYBIT_CASH_DELIVERY_POLICY_VERSION
        ),
        "state": "confirmed",
        "payload": universal_payload,
        "payload_fingerprint": (
            service
            ._bybit_evidence_fingerprint(
                universal_payload
            )
        ),
        "reconciliation": deepcopy(
            universal_reconciliation
        ),
    }

    withdrawal_payload = {
        "requestId": "wr-11",
        "coin": "USDT",
        "chain": "BSC",
        "address": SETTLEMENT_ADDRESS,
        "amount": "30",
        "forceChain": 1,
        "feeType": int(
            service.settings
            .NEGATIVE_NET_WITHDRAWAL_FEE_TYPE
        ),
        "accountType": "FUND",
    }

    withdrawal_reconciliation = {
        "schema": (
            service
            .BYBIT_WITHDRAWAL_RECONCILIATION_SCHEMA
        ),
        "state": "confirmed",
        "request_id": "wr-11",
        "unique_match": True,
        "ambiguous": False,
        "exact_fingerprint_match": True,
        "no_automatic_resend": True,
        "record_fingerprint": (
            RECORD_FINGERPRINT
        ),
        "tx_hash": TX_HASH,
    }

    withdrawal_intent = {
        "schema": (
            service
            .BYBIT_WITHDRAWAL_INTENT_SCHEMA
        ),
        "policy_version": (
            service.settings
            .NEGATIVE_NET_WITHDRAWAL_POLICY_VERSION
        ),
        "state": "confirmed",
        "payload_template": (
            withdrawal_payload
        ),
        "payload_fingerprint": (
            service
            ._bybit_evidence_fingerprint(
                withdrawal_payload
            )
        ),
        "fee_usdt": "1",
        "reconciliation": deepcopy(
            withdrawal_reconciliation
        ),
    }

    expected_raw = (
        30
        * (
            10
            ** int(
                service.settings
                .BSC_USDT_DECIMALS
            )
        )
    )

    receipt = {
        "schema": (
            service
            .BYBIT_SETTLEMENT_RECEIPT_SCHEMA
        ),
        "policy_version": (
            service.settings
            .NEGATIVE_NET_WITHDRAWAL_POLICY_VERSION
        ),
        "state": "confirmed",
        "tx_hash": TX_HASH,
        "expected_amount_usdt": "30",
        "expected_raw": str(
            expected_raw
        ),
        "confirmations": max(
            12,
            int(
                service.settings
                .NEGATIVE_NET_BSC_INTENT_CONFIRMATIONS_REQUIRED
            ),
        ),
        "receipt_block_number": 500,
        "matched_transfer_log_count": 1,
        "matched_transfer_total_raw": str(
            expected_raw
        ),
        "malformed_matching_log_count": 0,
        "malformed_matching_logs": [],
        "balance_delta_raw": str(
            expected_raw
        ),
        "unrelated_additional_incoming_raw": "0",
        "exact_transfer_log_match": True,
        "balance_delta_covers_expected": True,
        "raw_receipt_omitted": True,
    }

    master_barrier = {
        "schema": (
            service
            .BYBIT_MASTER_BALANCE_SCHEMA
        ),
        "state": "confirmed",
        "account_type": "FUND",
        "coin": "USDT",
        "member_id": "master-1",
        "required_master_usdt": "31",
        "query_succeeded": True,
        "sufficient": True,
        "withdrawal_allowed": True,
        "balance": {
            "transfer_balance": "31",
        },
    }

    bybit_flow = SimpleNamespace(
        id=31,
        settlement_batch_id=11,
        fund_id=3,
        status=BYBIT_FLOW_STATUS_COMPLETED,
        coin="USDT",
        chain="BSC",
        required_master_usdt=Decimal(
            "31"
        ),
        withdrawal_request_amount_usdt=Decimal(
            "30"
        ),
        bybit_withdrawal_fee_usdt=Decimal(
            "1"
        ),
        retained_fees_usdt=Decimal(
            "0"
        ),
        settlement_wallet_id=71,
        settlement_wallet_address=(
            SETTLEMENT_ADDRESS
        ),
        universal_transfer_id="ut-11",
        universal_transfer_status="SUCCESS",
        universal_transfer_amount_usdt=Decimal(
            "31"
        ),
        universal_transfer_coin="USDT",
        universal_transfer_confirmed_at=NOW,
        universal_transfer_intent_json=(
            universal_intent
        ),
        universal_transfer_reconciliation_json=(
            universal_reconciliation
        ),
        from_sub_uid="sub-3",
        to_master_uid="master-1",
        from_account_type="UNIFIED",
        to_account_type="FUND",
        withdrawal_request_id="wr-11",
        withdrawal_id="wd-11",
        withdrawal_status="SUCCESS",
        withdrawal_amount_usdt=Decimal(
            "30"
        ),
        withdrawal_fee_usdt=Decimal(
            "1"
        ),
        withdrawal_coin="USDT",
        withdrawal_chain="BSC",
        withdrawal_address=(
            SETTLEMENT_ADDRESS
        ),
        withdrawal_tx_hash=TX_HASH,
        withdrawal_confirmed_at=NOW,
        withdrawal_intent_json=(
            withdrawal_intent
        ),
        withdrawal_reconciliation_json=(
            withdrawal_reconciliation
        ),
        withdrawal_record_json={
            "withdrawal_id": "wd-11",
            "tx_hash": TX_HASH,
            "record_fingerprint": (
                RECORD_FINGERPRINT
            ),
            "raw_omitted": True,
        },
        settlement_wallet_balance_before_usdt=Decimal(
            "5"
        ),
        settlement_wallet_balance_after_usdt=Decimal(
            "35"
        ),
        settlement_wallet_receipt_status=(
            "CONFIRMED"
        ),
        settlement_wallet_received_usdt=Decimal(
            "30"
        ),
        settlement_wallet_receipt_tx_hash=(
            TX_HASH
        ),
        settlement_wallet_receipt_confirmations=(
            receipt["confirmations"]
        ),
        settlement_wallet_receipt_block_number=500,
        settlement_wallet_receipt_confirmed_at=NOW,
        settlement_wallet_receipt_json=(
            receipt
        ),
        reconciliation_json={
            "master_transferable_"
            "balance_barrier": (
                master_barrier
            ),
        },
        report_json=None,
    )

    identity = (
        service
        ._bybit_cash_delivery_identity(
            settlement_batch=(
                settlement_batch
            ),
            bybit_flow=bybit_flow,
        )
    )

    fingerprints = (
        service
        ._bybit_cash_delivery_fingerprints(
            bybit_flow=bybit_flow,
        )
    )

    completion = {
        "schema": (
            service
            .BYBIT_CASH_DELIVERY_COMPLETION_SCHEMA
        ),
        "policy_version": (
            service
            .BYBIT_CASH_DELIVERY_POLICY_VERSION
        ),
        "state": "completed",
        "completed_at": NOW.isoformat(),
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

    report = {
        "schema": (
            service
            .BYBIT_CASH_DELIVERY_REPORT_SCHEMA
        ),
        "policy_version": (
            service
            .BYBIT_CASH_DELIVERY_POLICY_VERSION
        ),
        "state": "completed",
        "completed_at": NOW.isoformat(),
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

    bybit_flow.reconciliation_json[
        "cash_delivery_completion"
    ] = completion

    bybit_flow.report_json = report

    return (
        settlement_batch,
        bybit_flow,
    )


def validate(
    *,
    settlement_batch: Any | None = None,
    bybit_flow: Any | None = None,
) -> dict[str, Any]:
    default_batch, default_flow = (
        make_context()
    )

    return (
        service
        ._validate_bybit_cash_delivery_evidence(
            settlement_batch=(
                settlement_batch
                if settlement_batch is not None
                else default_batch
            ),
            bybit_flow=(
                bybit_flow
                if bybit_flow is not None
                else default_flow
            ),
        )
    )


def test_full_durable_cash_delivery_is_accepted_without_selected_source() -> None:
    settlement_batch, bybit_flow = (
        make_context()
    )

    assert (
        "selected_source"
        not in bybit_flow
        .withdrawal_reconciliation_json
    )

    result = validate(
        settlement_batch=settlement_batch,
        bybit_flow=bybit_flow,
    )

    assert result[
        "durable_evidence_validated"
    ] is True

    assert result[
        "selected_source_required"
    ] is False

    assert result[
        "raw_external_payloads_omitted"
    ] is True


@pytest.mark.parametrize(
    (
        "target",
        "key",
        "value",
        "error_match",
    ),
    [
        (
            "completion",
            "schema",
            "wrong",
            "completion schema",
        ),
        (
            "report",
            "state",
            "pending",
            "report state",
        ),
        (
            "completion",
            "evidence_fingerprints",
            {},
            "completion evidence fingerprints",
        ),
        (
            "report",
            "reserve_release_allowed",
            True,
            "strict finalization boundary",
        ),
    ],
)
def test_invalid_completion_or_report_is_rejected(
    target: str,
    key: str,
    value: Any,
    error_match: str,
) -> None:
    settlement_batch, bybit_flow = (
        make_context()
    )

    if target == "completion":
        bybit_flow.reconciliation_json[
            "cash_delivery_completion"
        ][key] = value
    else:
        bybit_flow.report_json[
            key
        ] = value

    with pytest.raises(
        service.NegativeFinalizationError,
        match=error_match,
    ):
        validate(
            settlement_batch=(
                settlement_batch
            ),
            bybit_flow=bybit_flow,
        )


@pytest.mark.parametrize(
    (
        "target",
        "key",
        "value",
        "error_match",
    ),
    [
        (
            "universal",
            "exact_match",
            False,
            "Universal Transfer reconciliation",
        ),
        (
            "withdrawal",
            "ambiguous",
            True,
            "unique-match",
        ),
        (
            "withdrawal",
            "no_automatic_resend",
            False,
            "no-resend",
        ),
        (
            "receipt",
            "state",
            "pending",
            "receipt state",
        ),
        (
            "receipt",
            "malformed_matching_log_count",
            1,
            "malformed matching logs",
        ),
    ],
)
def test_invalid_durable_external_evidence_is_rejected(
    target: str,
    key: str,
    value: Any,
    error_match: str,
) -> None:
    settlement_batch, bybit_flow = (
        make_context()
    )

    if target == "universal":
        evidence = (
            bybit_flow
            .universal_transfer_reconciliation_json
        )
        bybit_flow.universal_transfer_intent_json[
            "reconciliation"
        ] = evidence

    elif target == "withdrawal":
        evidence = (
            bybit_flow
            .withdrawal_reconciliation_json
        )
        bybit_flow.withdrawal_intent_json[
            "reconciliation"
        ] = evidence

    else:
        evidence = (
            bybit_flow
            .settlement_wallet_receipt_json
        )

    evidence[key] = value

    with pytest.raises(
        service.NegativeFinalizationError,
        match=error_match,
    ):
        validate(
            settlement_batch=(
                settlement_batch
            ),
            bybit_flow=bybit_flow,
        )


def test_withdrawal_record_fingerprint_mismatch_is_rejected() -> None:
    settlement_batch, bybit_flow = (
        make_context()
    )

    bybit_flow.withdrawal_record_json[
        "record_fingerprint"
    ] = "c" * 64

    with pytest.raises(
        service.NegativeFinalizationError,
        match="record fingerprint",
    ):
        validate(
            settlement_batch=(
                settlement_batch
            ),
            bybit_flow=bybit_flow,
        )


def test_flow_identity_mismatch_is_rejected() -> None:
    settlement_batch, bybit_flow = (
        make_context()
    )

    bybit_flow.settlement_batch_id = 99

    with pytest.raises(
        service.NegativeFinalizationError,
        match="settlement batch mismatch",
    ):
        validate(
            settlement_batch=(
                settlement_batch
            ),
            bybit_flow=bybit_flow,
        )