from __future__ import annotations

from copy import deepcopy
from datetime import (
    datetime,
    timezone,
)
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.settlement.negative_sale_live_batch_service as service
from app.settlement.negative_sale_live_leg_service import (
    NegativeSaleLiveLegStepResult,
)
from app.settlement.negative_sale_order_intent import (
    build_negative_sale_order_intent,
)


NOW = datetime(
    2026,
    7,
    23,
    0,
    0,
    tzinfo=timezone.utc,
)


class Balance:
    def __init__(
        self,
        amount: str,
    ):
        self.confirmed_transferable_amount = (
            Decimal(amount)
        )

    def to_dict(self) -> dict:
        return {
            "account_type": "UNIFIED",
            "destination_account_type": (
                "FUND"
            ),
            "coin": "USDT",
            "confirmed_transferable_amount": (
                str(
                    self
                    .confirmed_transferable_amount
                )
            ),
        }


def _balance_state(
    amount: str,
) -> tuple[
    Balance,
    Decimal,
    Decimal,
]:
    balance = Balance(amount)
    available = (
        balance
        .confirmed_transferable_amount
    )
    shortage = max(
        Decimal("100") - available,
        Decimal("0"),
    )

    return (
        balance,
        available,
        shortage,
    )


def _intent(
    *,
    leg_id: int = 30,
    symbol: str = "BTCUSDT",
    execution_round: int = 0,
    status: str = "prepared",
) -> dict:
    intent = (
        build_negative_sale_order_intent(
            sale_batch_id=10,
            leg_id=leg_id,
            execution_round=(
                execution_round
            ),
            category="spot",
            symbol=symbol,
            position_side=None,
            close_side="Sell",
            position_idx=None,
            reduce_only=None,
            market_unit="baseCoin",
            requested_qty=(
                Decimal("0.10")
            ),
            normalized_qty=(
                Decimal("0.10")
            ),
            target_cash_usdt=(
                Decimal("10")
            ),
            slices=(
                Decimal("0.10"),
            ),
            prepared_at=NOW,
        )
        .to_dict()
    )

    suborder = intent[
        "suborders"
    ][0]
    suborder["status"] = status

    if status in {
        "submitted",
        "acknowledged",
        "pending_confirmation",
    }:
        suborder["order_id"] = (
            f"OID-{leg_id}-"
            f"{execution_round}"
        )
        suborder["submitted_at"] = (
            NOW.isoformat()
        )

    if status in {
        "acknowledged",
        "pending_confirmation",
    }:
        suborder["acknowledged_at"] = (
            NOW.isoformat()
        )

    if status in {
        "filled",
        "terminal_partial",
        "failed",
    }:
        suborder["order_id"] = (
            f"OID-{leg_id}-"
            f"{execution_round}"
        )
        suborder["submitted_at"] = (
            NOW.isoformat()
        )
        suborder["acknowledged_at"] = (
            NOW.isoformat()
        )
        suborder["terminal_at"] = (
            NOW.isoformat()
        )
        suborder["reconciliation"] = {
            "aggregate_exec_qty": (
                "0.10"
                if status != "failed"
                else "0"
            ),
            "aggregate_exec_value": (
                "10"
                if status != "failed"
                else "0"
            ),
            "fees_by_currency": {},
        }

    return intent


def _leg(
    *,
    leg_id: int = 30,
    leg_index: int = 1,
    symbol: str = "BTCUSDT",
    execution_round: int = 0,
    status: str = "prepared",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=leg_id,
        sale_batch_id=10,
        leg_index=leg_index,
        leg_group="spot",
        leg_type="spot_sell",
        coin=symbol.replace(
            "USDT",
            "",
        ),
        symbol=symbol,
        category="spot",
        side="Sell",
        location="UNIFIED",
        execution_round=str(
            execution_round
        ),
        suborders_json=_intent(
            leg_id=leg_id,
            symbol=symbol,
            execution_round=(
                execution_round
            ),
            status=status,
        ),
        mock_execution_json=None,
    )


def _sale_batch() -> SimpleNamespace:
    return SimpleNamespace(
        id=10,
        required_master_usdt=(
            Decimal("100")
        ),
        plan_json={
            "legs": [],
        },
    )


def _settlement_batch() -> (
    SimpleNamespace
):
    return SimpleNamespace(
        id=20,
    )


def _forbidden_prepare(
    *args,
    **kwargs,
):
    raise AssertionError(
        "A new correction round must "
        "not be prepared"
    )


def _forbidden_leg_resume(
    *args,
    **kwargs,
):
    raise AssertionError(
        "Terminal intent must not be "
        "resumed"
    )


def test_completed_rounds_are_counted_globally_once(
    monkeypatch,
):
    first = _leg(
        leg_id=30,
        leg_index=1,
        symbol="BTCUSDT",
        execution_round=2,
        status="filled",
    )
    second = _leg(
        leg_id=31,
        leg_index=2,
        symbol="ETHUSDT",
        execution_round=2,
        status="filled",
    )

    first.mock_execution_json = [
        {
            "execution_round": 1,
        }
    ]
    second.mock_execution_json = [
        {
            "execution_round": 1,
        }
    ]

    monkeypatch.setattr(
        service,
        "validated_terminal_intent_history",
        lambda raw: list(
            raw or []
        ),
    )

    completed = (
        service
        ._completed_correction_rounds(
            [
                first,
                second,
            ]
        )
    )

    # Round 1 and round 2 are counted
    # once each, not once per leg.
    assert completed == 2


def test_exhausted_rounds_stop_without_new_intent(
    monkeypatch,
):
    leg = _leg(
        execution_round=2,
        status="filled",
    )

    monkeypatch.setattr(
        service.settings,
        "NEGATIVE_NET_LIVE_CORRECTION_MAX_ROUNDS",
        2,
    )
    monkeypatch.setattr(
        service,
        "_completed_correction_rounds",
        lambda candidates: 2,
    )
    monkeypatch.setattr(
        service,
        "_confirmed_balance_state",
        lambda **kwargs: (
            _balance_state("80")
        ),
    )
    monkeypatch.setattr(
        service,
        "_prepare_spot_correction_round",
        _forbidden_prepare,
    )
    monkeypatch.setattr(
        service,
        "resume_live_leg_once",
        _forbidden_leg_resume,
    )

    result = (
        service
        .resume_negative_sale_order_batch_once(
            object(),
            client=object(),
            sale_batch=_sale_batch(),
            settlement_batch=(
                _settlement_batch()
            ),
            legs=[leg],
            now=NOW,
        )
    )

    assert result.action == (
        "balance_check"
    )
    assert result.reason == (
        "correction_rounds_exhausted"
    )
    assert result.posted is False
    assert (
        result.has_pending_action
        is False
    )
    assert result.requires_review is False
    assert result.shortage_usdt == (
        Decimal("20")
    )
    assert result.correction_decision[
        "allowed"
    ] is False
    assert result.correction_decision[
        "next_round"
    ] is None


def test_resolved_shortage_does_not_start_another_round(
    monkeypatch,
):
    leg = _leg(
        execution_round=1,
        status="filled",
    )

    monkeypatch.setattr(
        service.settings,
        "NEGATIVE_NET_LIVE_CORRECTION_MAX_ROUNDS",
        2,
    )
    monkeypatch.setattr(
        service,
        "_confirmed_balance_state",
        lambda **kwargs: (
            _balance_state("105")
        ),
    )
    monkeypatch.setattr(
        service,
        "_prepare_spot_correction_round",
        _forbidden_prepare,
    )
    monkeypatch.setattr(
        service,
        "resume_live_leg_once",
        _forbidden_leg_resume,
    )

    result = (
        service
        .resume_negative_sale_order_batch_once(
            object(),
            client=object(),
            sale_batch=_sale_batch(),
            settlement_batch=(
                _settlement_batch()
            ),
            legs=[leg],
            now=NOW,
        )
    )

    assert result.action == (
        "balance_check"
    )
    assert result.reason == (
        "confirmed_balance_covers_"
        "requirement_before_spot"
    )
    assert (
        result.confirmed_available_usdt
        == Decimal("105")
    )
    assert result.shortage_usdt == (
        Decimal("0")
    )
    assert result.posted is False
    assert result.correction_decision[
        "reason"
    ] == "shortage_resolved"
    assert result.correction_decision[
        "allowed"
    ] is False


def test_no_eligible_source_requires_review(
    monkeypatch,
):
    leg = _leg(
        execution_round=0,
        status="filled",
    )

    monkeypatch.setattr(
        service.settings,
        "NEGATIVE_NET_LIVE_CORRECTION_MAX_ROUNDS",
        2,
    )
    monkeypatch.setattr(
        service,
        "_confirmed_balance_state",
        lambda **kwargs: (
            _balance_state("80")
        ),
    )
    monkeypatch.setattr(
        service,
        "_prepare_spot_correction_round",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        service,
        "resume_live_leg_once",
        _forbidden_leg_resume,
    )

    result = (
        service
        .resume_negative_sale_order_batch_once(
            object(),
            client=object(),
            sale_batch=_sale_batch(),
            settlement_batch=(
                _settlement_batch()
            ),
            legs=[leg],
            now=NOW,
        )
    )

    assert result.action == (
        "review_required"
    )
    assert result.reason == (
        "no_eligible_spot_"
        "correction_source"
    )
    assert result.posted is False
    assert result.requires_review is True
    assert result.shortage_usdt == (
        Decimal("20")
    )
    assert result.correction_decision[
        "allowed"
    ] is True
    assert result.correction_decision[
        "next_round"
    ] == 1


def test_prepared_correction_resumes_without_duplicate_post(
    monkeypatch,
):
    leg = _leg(
        execution_round=1,
        status="prepared",
    )

    monkeypatch.setattr(
        service,
        "_confirmed_balance_state",
        lambda **kwargs: (
            _balance_state("80")
        ),
    )
    monkeypatch.setattr(
        service,
        "_prepare_spot_correction_round",
        _forbidden_prepare,
    )

    resume_calls: list[str] = []
    simulated_posts: list[str] = []

    def fake_resume(
        db,
        *,
        client,
        sale_batch,
        settlement_batch,
        leg,
        execution_round,
        now,
    ):
        current_status = str(
            leg.suborders_json[
                "suborders"
            ][0]["status"]
        )
        resume_calls.append(
            current_status
        )

        updated = deepcopy(
            leg.suborders_json
        )

        if current_status == "prepared":
            simulated_posts.append(
                updated["suborders"][0][
                    "order_link_id"
                ]
            )
            updated["suborders"][0][
                "status"
            ] = "acknowledged"
            updated["suborders"][0][
                "order_id"
            ] = "OID-CORRECTION-1"
            updated["suborders"][0][
                "submitted_at"
            ] = now.isoformat()
            updated["suborders"][0][
                "acknowledged_at"
            ] = now.isoformat()
            posted = True
            reason = (
                "prepared_suborder_submitted"
            )

        elif (
            current_status
            == "acknowledged"
        ):
            updated["suborders"][0][
                "status"
            ] = "filled"
            updated["suborders"][0][
                "terminal_at"
            ] = now.isoformat()
            updated["suborders"][0][
                "reconciliation"
            ] = {
                "aggregate_exec_qty": (
                    "0.10"
                ),
                "aggregate_exec_value": (
                    "10"
                ),
                "fees_by_currency": {},
            }
            posted = False
            reason = (
                "active_suborders_reconciled"
            )

        else:
            pytest.fail(
                "Unexpected resume status: "
                f"{current_status}"
            )

        leg.suborders_json = updated

        return NegativeSaleLiveLegStepResult(
            leg_id=int(leg.id),
            action="resume",
            posted=posted,
            confirmed_suborders=(
                1 if not posted else 0
            ),
            reason=reason,
            intent=deepcopy(updated),
            summary={},
        )

    monkeypatch.setattr(
        service,
        "resume_live_leg_once",
        fake_resume,
    )

    first = (
        service
        .resume_negative_sale_order_batch_once(
            object(),
            client=object(),
            sale_batch=_sale_batch(),
            settlement_batch=(
                _settlement_batch()
            ),
            legs=[leg],
            now=NOW,
        )
    )

    second = (
        service
        .resume_negative_sale_order_batch_once(
            object(),
            client=object(),
            sale_batch=_sale_batch(),
            settlement_batch=(
                _settlement_batch()
            ),
            legs=[leg],
            now=NOW,
        )
    )

    assert first.action == "order_step"
    assert first.posted is True
    assert (
        first.has_pending_action
        is True
    )

    assert second.action == "order_step"
    assert second.posted is False
    assert (
        second.has_pending_action
        is False
    )

    assert resume_calls == [
        "prepared",
        "acknowledged",
    ]
    assert len(simulated_posts) == 1
    assert simulated_posts == [
        "wbns-10-30-r1-s0"
    ]


def test_terminal_correction_failure_requires_review(
    monkeypatch,
):
    leg = _leg(
        execution_round=1,
        status="failed",
    )

    balance_calls = 0

    def fake_balance(
        **kwargs,
    ):
        nonlocal balance_calls
        balance_calls += 1
        return _balance_state("80")

    monkeypatch.setattr(
        service,
        "_confirmed_balance_state",
        fake_balance,
    )
    monkeypatch.setattr(
        service,
        "_prepare_spot_correction_round",
        _forbidden_prepare,
    )
    monkeypatch.setattr(
        service,
        "resume_live_leg_once",
        _forbidden_leg_resume,
    )

    result = (
        service
        .resume_negative_sale_order_batch_once(
            object(),
            client=object(),
            sale_batch=_sale_batch(),
            settlement_batch=(
                _settlement_batch()
            ),
            legs=[leg],
            now=NOW,
        )
    )

    assert result.action == (
        "review_required"
    )
    assert result.reason == (
        "terminal_order_failure"
    )
    assert result.active_leg_id == 30
    assert result.posted is False
    assert result.requires_review is True
    assert (
        result.has_pending_action
        is False
    )
    assert result.shortage_usdt == (
        Decimal("20")
    )
    assert balance_calls == 1