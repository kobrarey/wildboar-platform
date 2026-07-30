from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import scripts.recover_failed_redeem_order as recovery


NOW = datetime(
    2026,
    7,
    30,
    12,
    0,
    tzinfo=timezone.utc,
)


class FakeDb:
    def __init__(self) -> None:
        self.commit_calls = 0
        self.rollback_calls = 0
        self.added = []

    def add(self, value) -> None:
        self.added.append(value)

    def commit(self) -> None:
        self.commit_calls += 1

    def rollback(self) -> None:
        self.rollback_calls += 1


def make_context():
    source = SimpleNamespace(
        id=136,
        user_id=1,
        fund_id=9,
        side="redeem",
        shares=Decimal("0.0158"),
        amount_usdt=None,
        status="failed_requires_review",
        settlement_batch_id=182,
        redeem_reserve_released_shares=(
            Decimal("0.0158")
        ),
        redeem_reserve_released_at=NOW,
        redeem_reserve_release_reason=(
            "failed_before_external_state"
        ),
        executed_at=None,
        error="original_source_error",
    )

    batch = SimpleNamespace(
        id=182,
        fund_id=9,
        status="failed_requires_review",
        accounting_finalized_at=None,
        seller_payouts_completed_at=None,
        pricing_locked_at=NOW,
        pricing_unlocked_at=NOW,
        error="original_batch_error",
    )

    position = SimpleNamespace(
        user_id=1,
        fund_id=9,
        shares=Decimal("0.0158"),
        shares_reserved=Decimal("0"),
    )

    return recovery.RecoveryContext(
        source_order=source,
        batch=batch,
        user=SimpleNamespace(
            id=1,
            is_active=True,
        ),
        fund=SimpleNamespace(
            id=9,
            code="wb_test",
            is_active=True,
        ),
        position=position,
        active_wallet=SimpleNamespace(
            id=77,
            user_id=1,
            blockchain="BSC",
            is_active=True,
        ),
        runtime_state=SimpleNamespace(
            fund_id=9,
            pricing_locked=False,
            pricing_lock_batch_id=None,
        ),
    )


def safe_external_state():
    return {
        "settlement_batch_id": 182,
        "external_state_absent": True,
        "safe_to_release_reserves": True,
        "safe_to_unlock_pricing": True,
        "accounting_finalized": False,
        "action_flags": {
            "sale": False,
            "earn": False,
            "universal_transfer": False,
            "withdrawal": False,
            "payout": False,
            "gas_topup": False,
            "bsc_intent": False,
            "other": False,
        },
        "reason_count": 0,
        "evidence_count": 0,
    }


def patch_common(
    monkeypatch,
    *,
    context,
    replacements=None,
    external_state=None,
    active_redeems=None,
) -> None:
    monkeypatch.setattr(
        recovery,
        "_lock_recovery_context",
        lambda *args, **kwargs: context,
    )
    monkeypatch.setattr(
        recovery,
        "_external_state_summary",
        lambda *args, **kwargs: (
            external_state
            if external_state is not None
            else safe_external_state()
        ),
    )
    monkeypatch.setattr(
        recovery,
        "_find_replacement_orders",
        lambda *args, **kwargs: list(
            replacements or []
        ),
    )
    monkeypatch.setattr(
        recovery,
        "_find_active_redeem_orders",
        lambda *args, **kwargs: list(
            active_redeems or []
        ),
    )


def run_recovery(
    db,
    *,
    apply: bool,
):
    return recovery.recover_failed_redeem_order(
        db,
        failed_order_id=136,
        expected_batch_id=182,
        expected_user_id=1,
        expected_fund_id=9,
        apply=apply,
    )


def test_recovery_dry_run_does_not_create_or_reserve(
    monkeypatch,
) -> None:
    context = make_context()
    db = FakeDb()

    patch_common(
        monkeypatch,
        context=context,
    )

    monkeypatch.setattr(
        recovery,
        "create_redeem_order",
        lambda *args, **kwargs: (
            pytest.fail(
                "dry-run created an order"
            )
        ),
    )

    result = run_recovery(
        db,
        apply=False,
    )

    assert result["ok"] is True
    assert result["mode"] == "dry_run"
    assert result["replacement_order_id"] is None
    assert (
        context.position.shares_reserved
        == Decimal("0")
    )
    assert db.commit_calls == 0
    assert db.rollback_calls == 1


def test_recovery_apply_creates_one_replacement_and_preserves_source(
    monkeypatch,
) -> None:
    context = make_context()
    db = FakeDb()

    source_before = dict(
        vars(context.source_order)
    )
    batch_before = dict(
        vars(context.batch)
    )

    patch_common(
        monkeypatch,
        context=context,
    )

    replacement = SimpleNamespace(
        id=200,
        user_id=1,
        fund_id=9,
        side="redeem",
        shares=Decimal("0.0158"),
        amount_usdt=None,
        status="pending",
        settlement_batch_id=None,
        error=None,
    )

    create_calls = []

    def create_order(
        db_arg,
        user,
        fund_code,
        shares,
        *,
        lang,
        commit,
    ):
        create_calls.append(
            (
                fund_code,
                shares,
                commit,
            )
        )

        context.position.shares_reserved = (
            Decimal("0.0158")
        )

        return {
            "order": {
                "id": 200,
            }
        }

    monkeypatch.setattr(
        recovery,
        "create_redeem_order",
        create_order,
    )
    monkeypatch.setattr(
        recovery,
        "_lock_order_by_id",
        lambda *args, **kwargs: replacement,
    )

    result = run_recovery(
        db,
        apply=True,
    )

    assert create_calls == [
        (
            "wb_test",
            Decimal("0.0158"),
            False,
        )
    ]
    assert result["replacement_order_id"] == 200
    assert result["idempotent"] is False
    assert (
        replacement.error
        == recovery.replacement_marker(136)
    )
    assert (
        context.position.shares_reserved
        == Decimal("0.0158")
    )
    assert vars(context.source_order) == (
        source_before
    )
    assert vars(context.batch) == batch_before
    assert db.commit_calls == 1
    assert db.rollback_calls == 0


def test_recovery_repeat_apply_returns_existing_replacement(
    monkeypatch,
) -> None:
    context = make_context()
    context.position.shares_reserved = (
        Decimal("0.0158")
    )

    replacement = SimpleNamespace(
        id=200,
        user_id=1,
        fund_id=9,
        side="redeem",
        shares=Decimal("0.0158"),
        status="pending",
        error=recovery.replacement_marker(
            136
        ),
    )

    db = FakeDb()

    patch_common(
        monkeypatch,
        context=context,
        replacements=[replacement],
    )

    monkeypatch.setattr(
        recovery,
        "create_redeem_order",
        lambda *args, **kwargs: (
            pytest.fail(
                "repeat apply created "
                "a second replacement"
            )
        ),
    )

    result = run_recovery(
        db,
        apply=True,
    )

    assert result["idempotent"] is True
    assert result["replacement_order_id"] == 200
    assert (
        context.position.shares_reserved
        == Decimal("0.0158")
    )
    assert db.commit_calls == 0
    assert db.rollback_calls == 1


def test_recovery_blocks_any_external_state(
    monkeypatch,
) -> None:
    context = make_context()
    db = FakeDb()

    external_state = (
        safe_external_state()
    )
    external_state[
        "external_state_absent"
    ] = False
    external_state[
        "safe_to_release_reserves"
    ] = False
    external_state[
        "safe_to_unlock_pricing"
    ] = False
    external_state[
        "action_flags"
    ]["withdrawal"] = True
    external_state["reason_count"] = 1
    external_state["evidence_count"] = 1

    patch_common(
        monkeypatch,
        context=context,
        external_state=external_state,
    )

    with pytest.raises(
        recovery.RecoveryError,
        match=(
            "source_batch_external_state_exists"
        ),
    ):
        run_recovery(
            db,
            apply=True,
        )

    assert db.commit_calls == 0
    assert db.rollback_calls == 1


def test_recovery_blocks_incomplete_reserve_release(
    monkeypatch,
) -> None:
    context = make_context()

    context.source_order.redeem_reserve_released_shares = (
        Decimal("0")
    )

    db = FakeDb()

    patch_common(
        monkeypatch,
        context=context,
    )

    with pytest.raises(
        recovery.RecoveryError,
        match=(
            "source_redeem_reserve_"
            "not_fully_released"
        ),
    ):
        run_recovery(
            db,
            apply=True,
        )

    assert db.commit_calls == 0
    assert db.rollback_calls == 1


def test_recovery_blocks_expected_id_mismatch(
    monkeypatch,
) -> None:
    context = make_context()
    context.source_order.user_id = 2

    db = FakeDb()

    patch_common(
        monkeypatch,
        context=context,
    )

    with pytest.raises(
        recovery.RecoveryError,
        match="source_user_id_mismatch",
    ):
        run_recovery(
            db,
            apply=True,
        )

    assert db.commit_calls == 0
    assert db.rollback_calls == 1


def test_recovery_cli_defaults_to_dry_run(
) -> None:
    args = recovery._build_parser().parse_args(
        [
            "--failed-order-id",
            "136",
            "--expected-batch-id",
            "182",
            "--expected-user-id",
            "1",
            "--expected-fund-id",
            "9",
        ]
    )

    assert args.apply is False
    assert args.dry_run is False


def test_recovery_allows_batch_never_pricing_locked(
    monkeypatch,
) -> None:
    context = make_context()

    context.batch.pricing_locked_at = None
    context.batch.pricing_unlocked_at = None

    db = FakeDb()

    patch_common(
        monkeypatch,
        context=context,
    )

    result = run_recovery(
        db,
        apply=False,
    )

    assert result["ok"] is True
    assert result["mode"] == "dry_run"
    assert db.commit_calls == 0
    assert db.rollback_calls == 1


def test_recovery_blocks_active_batch_pricing_lock(
    monkeypatch,
) -> None:
    context = make_context()

    context.batch.pricing_locked_at = NOW
    context.batch.pricing_unlocked_at = None

    db = FakeDb()

    patch_common(
        monkeypatch,
        context=context,
    )

    with pytest.raises(
        recovery.RecoveryError,
        match=(
            "source_batch_pricing_lock_"
            "still_active"
        ),
    ):
        run_recovery(
            db,
            apply=False,
        )

    assert db.commit_calls == 0
    assert db.rollback_calls == 1


def test_external_state_summary_accepts_authoritative_safe_result(
    monkeypatch,
) -> None:
    state = SimpleNamespace(
        settlement_batch_id=182,
        safe_to_release_reserves=True,
        safe_to_unlock_pricing=True,
        accounting_finalized=False,
        sale_action_detected=False,
        earn_action_detected=False,
        universal_transfer_action_detected=False,
        withdrawal_action_detected=False,
        payout_action_detected=False,
        gas_topup_action_detected=False,
        bsc_intent_action_detected=False,
        other_external_action_detected=False,
        reasons=(),
        evidence=(),
    )

    monkeypatch.setattr(
        recovery,
        "inspect_negative_external_state",
        lambda *args, **kwargs: state,
    )

    summary = (
        recovery._external_state_summary(
            object(),
            batch_id=182,
        )
    )

    assert (
        summary["external_state_absent"]
        is True
    )
    assert summary["reason_count"] == 0
    assert summary["evidence_count"] == 0


def test_external_state_summary_blocks_durable_withdrawal(
    monkeypatch,
) -> None:
    state = SimpleNamespace(
        settlement_batch_id=182,
        safe_to_release_reserves=False,
        safe_to_unlock_pricing=False,
        accounting_finalized=False,
        sale_action_detected=False,
        earn_action_detected=False,
        universal_transfer_action_detected=False,
        withdrawal_action_detected=True,
        payout_action_detected=False,
        gas_topup_action_detected=False,
        bsc_intent_action_detected=False,
        other_external_action_detected=False,
        reasons=(
            "withdrawal_durable_intent",
        ),
        evidence=(
            {
                "action": "withdrawal",
            },
        ),
    )

    monkeypatch.setattr(
        recovery,
        "inspect_negative_external_state",
        lambda *args, **kwargs: state,
    )

    summary = (
        recovery._external_state_summary(
            object(),
            batch_id=182,
        )
    )

    assert (
        summary["external_state_absent"]
        is False
    )
    assert (
        summary["action_flags"][
            "withdrawal"
        ]
        is True
    )
    assert summary["reason_count"] == 1
    assert summary["evidence_count"] == 1


def test_recovery_dry_run_validates_source_share_precision(
    monkeypatch,
) -> None:
    context = make_context()

    context.source_order.shares = Decimal(
        "0.00001"
    )
    context.source_order.redeem_reserve_released_shares = (
        Decimal("0.00001")
    )

    db = FakeDb()

    patch_common(
        monkeypatch,
        context=context,
    )

    with pytest.raises(
        recovery.RecoveryError,
        match="source_order_shares_invalid",
    ):
        run_recovery(
            db,
            apply=False,
        )

    assert db.commit_calls == 0
    assert db.rollback_calls == 1
