from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

import app.settlement.negative_bybit_flow_live_service as service
from app.settlement.negative_bybit_flow_types import (
    NegativeBybitFlowError,
)
from app.settlement.statuses import (
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING,
    BATCH_STATUS_NEGATIVE_NET_SALE_EXECUTED,
    BYBIT_FLOW_STATUS_CREATED,
    BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW,
    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_INTENT_PREPARED,
)


NOW = datetime(
    2026,
    7,
    25,
    12,
    0,
    tzinfo=timezone.utc,
)


class FakeDb:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.flush_count = 0
        self.commit_count = 0
        self.rollback_count = 0

    def add(
        self,
        value: Any,
    ) -> None:
        self.added.append(value)

    def flush(self) -> None:
        self.flush_count += 1

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        self.rollback_count += 1


class FakeBybitClient:
    def __init__(self) -> None:
        self.get_calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []

    def get(
        self,
        path: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self.get_calls.append(
            {
                "path": path,
                "params": deepcopy(params),
            }
        )

        return {
            "retCode": 0,
            "result": {},
        }

    def post(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self.post_calls.append(
            {
                "path": path,
                "payload": deepcopy(payload),
            }
        )

        raise AssertionError(
            "Foundation prepare stages must not "
            "perform Bybit POST"
        )


def make_flow() -> SimpleNamespace:
    return SimpleNamespace(
        id=303,
        settlement_batch_id=101,
        sale_batch_id=202,
        fund_id=7,
        status=BYBIT_FLOW_STATUS_CREATED,
        coin="USDT",
        chain="BSC",
        required_master_usdt=Decimal("101"),
        withdrawal_request_amount_usdt=(
            Decimal("100")
        ),
        bybit_withdrawal_fee_usdt=Decimal("1"),
        retained_fees_usdt=Decimal("0"),
        settlement_wallet_address=None,
        withdrawal_request_id=None,
        universal_transfer_id=None,
        universal_transfer_status=None,
        universal_transfer_amount_usdt=None,
        universal_transfer_coin=None,
        universal_transfer_created_at=None,
        universal_transfer_confirmed_at=None,
        universal_transfer_submitted_at=None,
        universal_transfer_intent_json=None,
        withdrawal_intent_json=None,
        withdrawal_submitted_at=None,
        from_sub_uid=None,
        to_master_uid=None,
        from_account_type=None,
        to_account_type=None,
        preflight_passed=None,
        preflight_error=None,
        preflight_json=None,
        reconciliation_json=None,
        report_json=None,
        error=None,
        updated_at=None,
    )


def install_service_fakes(
    monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    db = FakeDb()
    client = FakeBybitClient()

    settlement_batch = SimpleNamespace(
        id=101,
        fund_id=7,
        status=(
            BATCH_STATUS_NEGATIVE_NET_SALE_EXECUTED
        ),
        error=None,
        updated_at=None,
    )

    sale_batch = SimpleNamespace(
        id=202,
        settlement_batch_id=101,
        fund_id=7,
        status="sale_execution_completed",
        required_master_usdt=Decimal("101"),
        withdrawal_request_amount_usdt=(
            Decimal("100")
        ),
        bybit_withdrawal_fee_usdt=Decimal("1"),
        total_net_user_payout_usdt=(
            Decimal("100")
        ),
        total_partial_month_fee_usdt=(
            Decimal("0")
        ),
        final_shortage_usdt=Decimal("0"),
        final_available_usdt=Decimal("101"),
    )

    fund = SimpleNamespace(
        id=7,
        code="wb_test",
    )

    amounts = {
        "required_master_usdt": Decimal("101"),
        "withdrawal_request_amount_usdt": (
            Decimal("100")
        ),
        "bybit_withdrawal_fee_usdt": (
            Decimal("1")
        ),
        "total_net_user_payout_usdt": (
            Decimal("100")
        ),
        "total_partial_month_fee_usdt": (
            Decimal("0")
        ),
    }

    state: dict[str, Any] = {
        "flow": None,
    }

    monkeypatch.setattr(
        service,
        "_lock_settlement_batch",
        lambda db, settlement_batch_id: (
            settlement_batch
        ),
    )

    monkeypatch.setattr(
        service,
        "_lock_sale_batch_for_settlement",
        lambda db, settlement_batch_id: sale_batch,
    )

    monkeypatch.setattr(
        service,
        "_lock_existing_flow",
        lambda db, settlement_batch_id: (
            state["flow"]
        ),
    )

    monkeypatch.setattr(
        service,
        "_validate_sale_batch_input",
        lambda **kwargs: None,
    )

    monkeypatch.setattr(
        service,
        "_validate_target_fields",
        lambda **kwargs: dict(amounts),
    )

    monkeypatch.setattr(
        service,
        "_get_fund",
        lambda db, fund_id: fund,
    )

    def new_or_existing_flow(
        db,
        *,
        existing,
        settlement_batch,
        sale_batch,
        amounts,
    ):
        assert existing is None
        assert state["flow"] is None

        flow = make_flow()
        state["flow"] = flow

        db.add(flow)
        db.flush()

        return flow

    monkeypatch.setattr(
        service,
        "_new_or_existing_flow",
        new_or_existing_flow,
    )

    def choose_route(
        bybit_client,
        *,
        coin,
        amount_usdt,
        from_member_id,
        to_member_id,
    ):
        bybit_client.get_calls.append(
            {
                "path": (
                    "/v5/asset/transfer/"
                    "query-account-coin-balance"
                ),
                "params": {
                    "coin": coin,
                    "amount": str(amount_usdt),
                    "fromMemberId": (
                        from_member_id
                    ),
                    "toMemberId": to_member_id,
                },
            }
        )

        return {
            "from_account_type": "FUND",
            "to_account_type": "FUND",
            "selected_transfer_balance": (
                Decimal("1000")
            ),
            "checked": [
                {
                    "from_account_type": "FUND",
                    "to_account_type": "FUND",
                    "transferBalance": "1000",
                }
            ],
        }

    monkeypatch.setattr(
        service,
        "choose_universal_transfer_account_route",
        choose_route,
    )

    monkeypatch.setattr(
        service,
        "deterministic_universal_transfer_id",
        lambda **kwargs: (
            "11111111-1111-5111-8111-"
            "111111111111"
        ),
    )

    return SimpleNamespace(
        db=db,
        client=client,
        batch=settlement_batch,
        sale_batch=sale_batch,
        fund=fund,
        amounts=amounts,
        state=state,
    )


def resume_once(
    env: SimpleNamespace,
):
    return service.resume_negative_bybit_flow_once(
        env.db,
        settlement_batch_id=101,
        bybit_client=env.client,
        fund_sub_uid="70001",
        master_uid="90001",
        now=NOW,
    )


def test_create_flow_is_one_transition_and_zero_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    result = resume_once(env)

    flow = env.state["flow"]

    assert result.ok is True
    assert result.diagnostics["transition"] == (
        "create_or_load_flow"
    )
    assert result.diagnostics["did_bybit_post"] is False
    assert result.diagnostics["bybit_post_count"] == 0
    assert result.diagnostics["bybit_get_count"] == 0

    assert flow is not None
    assert flow.status == BYBIT_FLOW_STATUS_CREATED
    assert flow.universal_transfer_intent_json is None

    assert env.batch.status == (
        BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
    )

    assert env.client.get_calls == []
    assert env.client.post_calls == []
    assert env.db.commit_count == 1


def test_prepare_transfer_intent_persists_exact_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    create_result = resume_once(env)
    prepare_result = resume_once(env)

    assert create_result.ok is True
    assert prepare_result.ok is True
    assert prepare_result.diagnostics[
        "transition"
    ] == "prepare_universal_transfer_intent"

    flow = env.state["flow"]
    intent = flow.universal_transfer_intent_json

    assert isinstance(intent, dict)

    assert intent["schema"] == (
        "negative_universal_transfer_intent_v2"
    )
    assert intent["state"] == "prepared"
    assert intent["policy_version"] == (
        "negative_cash_delivery_v1"
    )
    assert intent["settlement_batch_id"] == "101"
    assert intent["fund_id"] == "7"

    assert intent["transfer_id"] == (
        "11111111-1111-5111-8111-"
        "111111111111"
    )
    assert intent["coin"] == "USDT"
    assert intent["amount"] == "101"

    assert intent["from_member_id"] == "70001"
    assert intent["to_member_id"] == "90001"
    assert intent["from_account_type"] == "FUND"
    assert intent["to_account_type"] == "FUND"

    assert intent["payload"] == {
        "transferId": (
            "11111111-1111-5111-8111-"
            "111111111111"
        ),
        "coin": "USDT",
        "amount": "101",
        "fromMemberId": "70001",
        "toMemberId": "90001",
        "fromAccountType": "FUND",
        "toAccountType": "FUND",
    }

    assert intent["payload_fingerprint"] == (
        service._payload_fingerprint(
            intent["payload"]
        )
    )

    assert len(intent["payload_fingerprint"]) == 64
    int(intent["payload_fingerprint"], 16)

    assert intent["submit_claim"] is None
    assert intent["acknowledgement"] is None
    assert intent["reconciliation"] is None

    assert flow.status == (
        BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_INTENT_PREPARED
    )
    assert flow.withdrawal_intent_json is None
    assert flow.withdrawal_submitted_at is None

    assert len(env.client.get_calls) == 1
    assert env.client.post_calls == []
    assert env.db.commit_count == 2


def test_prepare_cycle_never_prepares_withdrawal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    resume_once(env)
    resume_once(env)

    flow = env.state["flow"]

    assert flow.universal_transfer_intent_json is not None
    assert flow.withdrawal_intent_json is None
    assert flow.withdrawal_request_id is None
    assert flow.withdrawal_submitted_at is None

    assert env.client.post_calls == []


def test_prepared_intent_rerun_is_idempotent_without_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    resume_once(env)
    resume_once(env)

    flow = env.state["flow"]
    intent_before = deepcopy(
        flow.universal_transfer_intent_json
    )

    get_count_before = len(env.client.get_calls)
    post_count_before = len(env.client.post_calls)
    commit_count_before = env.db.commit_count

    result = resume_once(env)

    assert result.ok is True
    assert result.idempotent is True
    assert result.diagnostics["transition"] == (
        "prepare_universal_transfer_intent"
    )

    assert flow.universal_transfer_intent_json == (
        intent_before
    )

    assert len(env.client.get_calls) == (
        get_count_before
    )
    assert len(env.client.post_calls) == (
        post_count_before
    )
    assert env.db.commit_count == commit_count_before


def test_payload_fingerprint_is_deterministic() -> None:
    first = {
        "transferId": "abc",
        "coin": "USDT",
        "amount": "101",
        "fromMemberId": "70001",
        "toMemberId": "90001",
    }

    second = {
        "toMemberId": "90001",
        "fromMemberId": "70001",
        "amount": "101",
        "coin": "USDT",
        "transferId": "abc",
    }

    assert service._payload_fingerprint(
        first
    ) == service._payload_fingerprint(second)


def test_payload_fingerprint_rejects_float() -> None:
    with pytest.raises(
        NegativeBybitFlowError,
        match="float is forbidden",
    ):
        service._payload_fingerprint(
            {
                "amount": 101.0,
            }
        )


def test_mutated_intent_fails_requires_review_without_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    resume_once(env)
    resume_once(env)

    flow = env.state["flow"]

    mutated = deepcopy(
        flow.universal_transfer_intent_json
    )
    mutated["payload"]["amount"] = "102"

    flow.universal_transfer_intent_json = mutated

    result = resume_once(env)

    assert result.ok is False
    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert "fingerprint mismatch" in str(
        result.error
    )

    assert env.client.post_calls == []


def test_legacy_transfer_evidence_without_v2_intent_blocks_resend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    resume_once(env)

    flow = env.state["flow"]
    flow.universal_transfer_id = (
        "22222222-2222-5222-8222-"
        "222222222222"
    )

    result = resume_once(env)

    assert result.ok is False
    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert (
        "evidence exists without durable v2 intent"
        in str(result.error)
    )

    assert env.client.get_calls == []
    assert env.client.post_calls == []