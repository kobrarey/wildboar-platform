import hashlib
import json
from decimal import Decimal

import pytest

from app.settlement.bsc_intent_service import (
    BscIntentError,
    PreparedBscTransaction,
    build_bsc_intent_fingerprint,
    build_bsc_intent_safe_audit,
)
from app.settlement.negative_external_state import (
    _audit_field_value,
)
from app.settlement.statuses import (
    BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT,
    BSC_INTENT_ACTION_NEGATIVE_SETTLEMENT_GAS_TOPUP,
    BSC_INTENT_ACTION_TYPES,
    BSC_INTENT_STATUS_BROADCAST,
    BSC_INTENT_STATUS_BROADCASTING,
    BSC_INTENT_STATUS_CONFIRMED,
    BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW,
    BSC_INTENT_STATUS_PENDING_CONFIRMATION,
    BSC_INTENT_STATUS_PREPARED,
    BSC_INTENT_STATUS_VISIBLE,
    BSC_INTENT_TERMINAL_STATUSES,
    BSC_INTENT_UNRESOLVED_STATUSES,
)


def _prepared(
    *,
    nonce: int = 7,
    tx_hash: str | None = None,
    raw_tx_hex: str = "0x0102",
) -> PreparedBscTransaction:
    return PreparedBscTransaction(
        chain_id=56,
        source_nonce=nonce,
        tx_hash=(
            tx_hash
            or f"0x{'ab' * 32}"
        ),
        raw_tx_hex=raw_tx_hex,
    )


def _fingerprint(
    **overrides,
) -> str:
    values = {
        "scope_key": "negative-payout:11:22:33",
        "action_type": (
            BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT
        ),
        "settlement_batch_id": 11,
        "payout_batch_id": 22,
        "payout_leg_id": 33,
        "fund_id": 9,
        "asset": "USDT",
        "amount": Decimal("10.500000000000000000"),
        "from_address": "0xABCDEF",
        "to_address": "0x123456",
        "prepared": _prepared(),
    }
    values.update(overrides)

    return build_bsc_intent_fingerprint(**values)


def test_bsc_intent_status_contract_is_exact():
    assert BSC_INTENT_ACTION_TYPES == {
        BSC_INTENT_ACTION_NEGATIVE_SETTLEMENT_GAS_TOPUP,
        BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT,
    }

    assert BSC_INTENT_UNRESOLVED_STATUSES == {
        BSC_INTENT_STATUS_PREPARED,
        BSC_INTENT_STATUS_BROADCASTING,
        BSC_INTENT_STATUS_BROADCAST,
        BSC_INTENT_STATUS_VISIBLE,
        BSC_INTENT_STATUS_PENDING_CONFIRMATION,
    }

    assert BSC_INTENT_TERMINAL_STATUSES == {
        BSC_INTENT_STATUS_CONFIRMED,
        BSC_INTENT_STATUS_FAILED_REQUIRES_REVIEW,
    }


def test_bsc_intent_fingerprint_is_canonical():
    first = _fingerprint()

    second = _fingerprint(
        amount=Decimal("10.5"),
        asset="usdt",
        from_address="0xabcdef",
        to_address="0x123456",
        prepared=_prepared(
            tx_hash=f"0x{'AB' * 32}",
            raw_tx_hex="0x0102",
        ),
    )

    assert first == second
    assert len(first) == 64


def test_bsc_intent_fingerprint_changes_with_contract():
    original = _fingerprint()

    assert _fingerprint(
        amount=Decimal("10.6")
    ) != original

    assert _fingerprint(
        to_address="0x654321"
    ) != original

    assert _fingerprint(
        prepared=_prepared(nonce=8)
    ) != original

    assert _fingerprint(
        prepared=_prepared(raw_tx_hex="0x0103")
    ) != original


def test_bsc_intent_safe_audit_never_contains_raw_tx():
    prepared = _prepared(
        raw_tx_hex="0x0102",
    )

    audit = build_bsc_intent_safe_audit(
        scope_key="negative-payout:11:22:33",
        action_type=(
            BSC_INTENT_ACTION_NEGATIVE_REDEEM_PAYOUT
        ),
        settlement_batch_id=11,
        payout_batch_id=22,
        payout_leg_id=33,
        fund_id=9,
        asset="USDT",
        amount=Decimal("10.5"),
        from_address="0xabcdef",
        to_address="0x123456",
        prepared=prepared,
    )

    serialized = json.dumps(
        audit,
        sort_keys=True,
    )

    assert prepared.raw_tx_hex not in serialized
    assert "prepared_raw_tx" not in audit
    assert "raw_tx_hex" not in audit

    assert audit["raw_transaction_sha256"] == (
        hashlib.sha256(
            bytes.fromhex("0102")
        ).hexdigest()
    )
    assert len(audit["intent_fingerprint"]) == 64


def test_float_amount_and_unknown_action_are_rejected():
    with pytest.raises(
        BscIntentError,
        match="Float BSC intent amount is forbidden",
    ):
        _fingerprint(amount=10.5)

    with pytest.raises(
        BscIntentError,
        match="Unsupported BSC intent action_type",
    ):
        _fingerprint(action_type="unknown_action")


def test_negative_external_state_redacts_prepared_raw_tx():
    raw_transaction = (
        "0x"
        + ("ab" * 512)
    )

    audited = _audit_field_value(
        field_name="prepared_raw_tx",
        value=raw_transaction,
    )

    assert audited == "redacted_present"
    assert raw_transaction not in str(audited)