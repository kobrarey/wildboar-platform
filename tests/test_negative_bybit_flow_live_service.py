from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

import app.settlement.negative_bybit_flow_live_service as service
from app.bybit.asset_flows import (
    BybitUniversalTransferResult,
)
from app.bybit.client import BybitApiError
from app.operation_guard.service import (
    OperationGuardBlockedError,
)
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
    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILING,
    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED,
    BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_SUBMITTING,
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
        self.lock_active = False
        self.events: list[str] = []

    def mark_locked(
        self,
        label: str,
    ) -> None:
        self.lock_active = True
        self.events.append(
            f"lock:{label}"
        )

    def add(
        self,
        value: Any,
    ) -> None:
        self.added.append(value)

    def flush(self) -> None:
        self.flush_count += 1

    def commit(self) -> None:
        self.commit_count += 1
        self.lock_active = False
        self.events.append("commit")

    def rollback(self) -> None:
        self.rollback_count += 1
        self.lock_active = False
        self.events.append("rollback")


class FakeBybitClient:
    def __init__(self) -> None:
        self.get_calls: list[dict[str, Any]] = []
        self.post_calls: list[dict[str, Any]] = []
        self.retries = 0

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
        universal_transfer_reconciliation_json=None,
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


def make_transfer_record(
    *,
    status: str,
    transfer_id: str = (
        "11111111-1111-5111-8111-"
        "111111111111"
    ),
    coin: str = "USDT",
    amount_usdt: Decimal = Decimal("101"),
    from_member_id: str = "70001",
    to_member_id: str = "90001",
    from_account_type: str = "FUND",
    to_account_type: str = "FUND",
) -> BybitUniversalTransferResult:
    return BybitUniversalTransferResult(
        transfer_id=transfer_id,
        coin=coin,
        amount_usdt=amount_usdt,
        from_member_id=from_member_id,
        to_member_id=to_member_id,
        from_account_type=from_account_type,
        to_account_type=to_account_type,
        status=status,
        raw={
            "transferId": transfer_id,
            "coin": coin,
            "amount": format(
                amount_usdt,
                "f",
            ),
            "fromMemberId": from_member_id,
            "toMemberId": to_member_id,
            "fromAccountType": (
                from_account_type
            ),
            "toAccountType": (
                to_account_type
            ),
            "status": status,
        },
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

    def lock_settlement_batch(
        db,
        *,
        settlement_batch_id,
    ):
        db.mark_locked("settlement_batch")
        return settlement_batch

    def lock_sale_batch(
        db,
        *,
        settlement_batch_id,
    ):
        db.mark_locked("sale_batch")
        return sale_batch

    def lock_existing_flow(
        db,
        *,
        settlement_batch_id,
    ):
        db.mark_locked("bybit_flow")
        return state["flow"]

    monkeypatch.setattr(
        service,
        "_lock_settlement_batch",
        lock_settlement_batch,
    )

    monkeypatch.setattr(
        service,
        "_lock_sale_batch_for_settlement",
        lock_sale_batch,
    )

    monkeypatch.setattr(
        service,
        "_lock_existing_flow",
        lock_existing_flow,
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
        assert db.lock_active is False
        db.events.append(
            "prepare_route_get"
        )

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

    def query_transfer(
        bybit_client,
        *,
        transfer_id,
    ):
        assert db.lock_active is False

        db.events.append(
            "query_universal_transfer"
        )

        bybit_client.get_calls.append(
            {
                "path": (
                    "/v5/asset/transfer/"
                    "query-universal-transfer-list"
                ),
                "params": {
                    "transferId": transfer_id,
                },
            }
        )

        return None

    def require_guard(
        db_arg,
        **kwargs,
    ):
        assert db_arg is db
        assert db.lock_active is False
        assert db.events[-1] == "commit"

        flow = state["flow"]
        intent = (
            flow.universal_transfer_intent_json
        )

        assert flow.status == (
            BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_SUBMITTING
        )
        assert intent["state"] == "submitting"
        assert isinstance(
            intent["submit_claim"],
            dict,
        )

        db.events.append(
            "operation_guard"
        )

        return SimpleNamespace(
            allowed=True,
            event_id=919,
        )

    def create_transfer(
        bybit_client,
        **kwargs,
    ):
        assert db.lock_active is False
        assert db.events[-1] == "commit"

        db.events.append(
            "universal_transfer_post"
        )

        bybit_client.post_calls.append(
            deepcopy(kwargs)
        )

        return BybitUniversalTransferResult(
            transfer_id=kwargs[
                "transfer_id"
            ],
            coin=kwargs["coin"],
            amount_usdt=kwargs[
                "amount_usdt"
            ],
            from_member_id=kwargs[
                "from_member_id"
            ],
            to_member_id=kwargs[
                "to_member_id"
            ],
            from_account_type=kwargs[
                "from_account_type"
            ],
            to_account_type=kwargs[
                "to_account_type"
            ],
            status="PENDING",
            raw={
                "retCode": 0,
                "result": {
                    "transferId": kwargs[
                        "transfer_id"
                    ],
                    "status": "PENDING",
                },
            },
        )

    monkeypatch.setattr(
        service,
        "query_universal_transfer",
        query_transfer,
    )

    monkeypatch.setattr(
        service,
        "require_bybit_universal_transfer_guard",
        require_guard,
    )

    monkeypatch.setattr(
        service,
        "create_universal_transfer",
        create_transfer,
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
    assert env.db.commit_count == 3
    assert "prepare_route_get" in (
        env.db.events
    )


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


def test_prepared_intent_next_cycle_claims_and_posts_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    resume_once(env)
    resume_once(env)

    flow = env.state["flow"]

    intent_before = deepcopy(
        flow.universal_transfer_intent_json
    )

    commit_count_before = (
        env.db.commit_count
    )

    result = resume_once(env)

    intent = (
        flow.universal_transfer_intent_json
    )

    assert result.ok is True
    assert result.diagnostics[
        "transition"
    ] == "submit_universal_transfer"

    assert result.diagnostics[
        "did_bybit_post"
    ] is True

    assert result.diagnostics[
        "bybit_post_count"
    ] == 1

    assert flow.status == (
        BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILING
    )

    assert flow.universal_transfer_submitted_at == NOW
    assert flow.universal_transfer_created_at == NOW

    assert intent["state"] == "reconciling"

    assert isinstance(
        intent["submit_claim"],
        dict,
    )

    assert intent[
        "submit_claim"
    ]["submit_attempt_number"] == 1

    assert intent[
        "acknowledgement"
    ]["outcome"] == "accepted"

    assert intent[
        "acknowledgement"
    ]["guard_event_id"] == 919

    assert intent[
        "acknowledgement"
    ]["no_automatic_resend"] is True

    assert intent[
        "payload"
    ] == intent_before["payload"]

    assert intent[
        "payload_fingerprint"
    ] == intent_before[
        "payload_fingerprint"
    ]

    assert len(
        env.client.post_calls
    ) == 1

    post = env.client.post_calls[0]

    assert post["transfer_id"] == (
        "11111111-1111-5111-8111-"
        "111111111111"
    )
    assert post["coin"] == "USDT"
    assert post["amount_usdt"] == (
        Decimal("101")
    )
    assert post["amount_str"] == "101"
    assert post["from_member_id"] == "70001"
    assert post["to_member_id"] == "90001"
    assert post["from_account_type"] == "FUND"
    assert post["to_account_type"] == "FUND"

    assert env.db.events.index(
        "query_universal_transfer"
    ) < env.db.events.index(
        "operation_guard"
    )

    assert env.db.events.index(
        "operation_guard"
    ) < env.db.events.index(
        "universal_transfer_post"
    )

    assert env.db.lock_active is False

    # Release before query, claim commit,
    # Guard commit and acknowledgement commit.
    assert env.db.commit_count == (
        commit_count_before + 4
    )


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


def test_submit_rejects_bybit_client_with_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    resume_once(env)
    resume_once(env)

    env.client.retries = 1

    get_count_before = len(
        env.client.get_calls
    )

    result = resume_once(env)

    flow = env.state["flow"]

    assert result.ok is False
    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert "retries=0" in str(
        result.error
    )

    assert len(
        env.client.get_calls
    ) == get_count_before

    assert env.client.post_calls == []


def test_preexisting_transfer_record_blocks_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    resume_once(env)
    resume_once(env)

    def existing_record(
        bybit_client,
        *,
        transfer_id,
    ):
        assert env.db.lock_active is False

        env.db.events.append(
            "query_universal_transfer"
        )

        bybit_client.get_calls.append(
            {
                "path": (
                    "/v5/asset/transfer/"
                    "query-universal-transfer-list"
                ),
                "params": {
                    "transferId": transfer_id,
                },
            }
        )

        return BybitUniversalTransferResult(
            transfer_id=transfer_id,
            coin="USDT",
            amount_usdt=Decimal("101"),
            from_member_id="70001",
            to_member_id="90001",
            from_account_type="FUND",
            to_account_type="FUND",
            status="PENDING",
            raw={
                "transferId": transfer_id,
                "coin": "USDT",
                "amount": "101",
                "status": "PENDING",
            },
        )

    monkeypatch.setattr(
        service,
        "query_universal_transfer",
        existing_record,
    )

    result = resume_once(env)

    flow = env.state["flow"]
    intent = (
        flow.universal_transfer_intent_json
    )

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "submit_universal_transfer_"
        "preexisting_record"
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILING
    )
    assert flow.universal_transfer_status == (
        "PENDING"
    )

    assert intent["state"] == "reconciling"
    assert intent["submit_claim"] is None

    assert intent[
        "reconciliation"
    ]["record_found"] is True

    assert intent[
        "reconciliation"
    ]["no_post_performed"] is True

    assert env.client.post_calls == []
    assert (
        "operation_guard"
        not in env.db.events
    )
    assert (
        "universal_transfer_post"
        not in env.db.events
    )


def test_guard_blocked_after_claim_performs_no_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    resume_once(env)
    resume_once(env)

    def blocked_guard(
        db_arg,
        **kwargs,
    ):
        assert db_arg is env.db
        assert env.db.lock_active is False
        assert env.db.events[-1] == "commit"

        flow = env.state["flow"]
        intent = (
            flow.universal_transfer_intent_json
        )

        assert flow.status == (
            BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_SUBMITTING
        )
        assert intent["state"] == "submitting"
        assert isinstance(
            intent["submit_claim"],
            dict,
        )

        env.db.events.append(
            "operation_guard_blocked"
        )

        raise OperationGuardBlockedError(
            "blocked by test"
        )

    monkeypatch.setattr(
        service,
        "require_bybit_universal_transfer_guard",
        blocked_guard,
    )

    result = resume_once(env)

    flow = env.state["flow"]
    intent = (
        flow.universal_transfer_intent_json
    )

    assert result.ok is False
    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert intent["state"] == (
        "failed_requires_review"
    )
    assert intent[
        "acknowledgement"
    ]["outcome"] == "guard_blocked"

    assert intent[
        "acknowledgement"
    ]["bybit_post_performed"] is False

    assert env.client.post_calls == []
    assert (
        "universal_transfer_post"
        not in env.db.events
    )


def test_crash_after_claim_recovers_by_exact_query_without_resend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    resume_once(env)
    resume_once(env)

    def crash_after_claim(
        db_arg,
        **kwargs,
    ):
        assert db_arg is env.db
        assert env.db.lock_active is False
        assert env.db.events[-1] == "commit"

        flow = env.state["flow"]
        intent = (
            flow.universal_transfer_intent_json
        )

        assert flow.status == (
            BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_SUBMITTING
        )
        assert intent["state"] == "submitting"
        assert isinstance(
            intent["submit_claim"],
            dict,
        )

        raise KeyboardInterrupt(
            "simulated crash after claim commit"
        )

    monkeypatch.setattr(
        service,
        "require_bybit_universal_transfer_guard",
        crash_after_claim,
    )

    with pytest.raises(
        KeyboardInterrupt,
        match="simulated crash",
    ):
        resume_once(env)

    flow = env.state["flow"]
    intent = (
        flow.universal_transfer_intent_json
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_SUBMITTING
    )
    assert intent["state"] == "submitting"
    assert isinstance(
        intent["submit_claim"],
        dict,
    )

    get_count_after_crash = len(
        env.client.get_calls
    )
    post_count_after_crash = len(
        env.client.post_calls
    )

    def confirmed_record(
        bybit_client,
        *,
        transfer_id,
    ):
        assert env.db.lock_active is False

        env.db.events.append(
            "query_universal_transfer"
        )

        bybit_client.get_calls.append(
            {
                "path": (
                    "/v5/asset/transfer/"
                    "query-universal-transfer-list"
                ),
                "params": {
                    "transferId": transfer_id,
                },
            }
        )

        return make_transfer_record(
            status="SUCCESS",
            transfer_id=transfer_id,
        )

    monkeypatch.setattr(
        service,
        "query_universal_transfer",
        confirmed_record,
    )

    result = resume_once(env)

    intent = (
        flow.universal_transfer_intent_json
    )

    assert result.ok is True
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_universal_transfer_"
        "confirmed"
    )

    assert result.diagnostics[
        "did_bybit_post"
    ] is False
    assert result.diagnostics[
        "bybit_post_count"
    ] == 0

    assert flow.status == (
        BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED
    )
    assert flow.universal_transfer_status == (
        "SUCCESS"
    )
    assert flow.universal_transfer_confirmed_at == (
        NOW
    )

    assert intent["state"] == "confirmed"
    assert intent[
        "reconciliation"
    ]["record_found"] is True
    assert intent[
        "reconciliation"
    ]["exact_match"] is True

    assert len(
        env.client.get_calls
    ) == get_count_after_crash + 1

    assert len(
        env.client.post_calls
    ) == post_count_after_crash

    assert env.client.post_calls == []


def test_unknown_post_result_never_resends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    resume_once(env)
    resume_once(env)

    def unknown_post(
        bybit_client,
        **kwargs,
    ):
        assert env.db.lock_active is False
        assert env.db.events[-1] == "commit"

        env.db.events.append(
            "universal_transfer_post"
        )

        bybit_client.post_calls.append(
            deepcopy(kwargs)
        )

        raise BybitApiError(
            "simulated timeout after POST"
        )

    monkeypatch.setattr(
        service,
        "create_universal_transfer",
        unknown_post,
    )

    result = resume_once(env)

    flow = env.state["flow"]
    intent = (
        flow.universal_transfer_intent_json
    )

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "submit_universal_transfer_unknown"
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILING
    )
    assert flow.universal_transfer_status == (
        "UNKNOWN"
    )

    assert intent["state"] == "reconciling"
    assert intent[
        "acknowledgement"
    ]["outcome"] == "unknown"

    assert intent[
        "acknowledgement"
    ]["no_automatic_resend"] is True

    assert len(
        env.client.post_calls
    ) == 1

    get_count_after_unknown = len(
        env.client.get_calls
    )
    post_count_after_unknown = len(
        env.client.post_calls
    )

    rerun = resume_once(env)

    intent = (
        flow.universal_transfer_intent_json
    )

    assert rerun.ok is False
    assert rerun.diagnostics[
        "transition"
    ] == (
        "reconcile_universal_transfer_"
        "missing"
    )

    assert rerun.diagnostics[
        "did_bybit_post"
    ] is False
    assert rerun.diagnostics[
        "bybit_post_count"
    ] == 0
    assert rerun.diagnostics[
        "no_automatic_resend"
    ] is True

    assert flow.status == (
        BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILING
    )

    assert intent["state"] == "reconciling"
    assert intent[
        "reconciliation"
    ]["record_found"] is False
    assert intent[
        "reconciliation"
    ]["query_succeeded"] is True

    assert len(
        env.client.get_calls
    ) == get_count_after_unknown + 1

    assert len(
        env.client.post_calls
    ) == post_count_after_unknown


def test_exact_pending_transfer_stays_reconciling_without_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    resume_once(env)
    resume_once(env)
    resume_once(env)

    post_count_before = len(
        env.client.post_calls
    )

    def pending_record(
        bybit_client,
        *,
        transfer_id,
    ):
        assert env.db.lock_active is False

        env.db.events.append(
            "query_universal_transfer"
        )

        bybit_client.get_calls.append(
            {
                "path": (
                    "/v5/asset/transfer/"
                    "query-universal-transfer-list"
                ),
                "params": {
                    "transferId": transfer_id,
                },
            }
        )

        return make_transfer_record(
            status="PROCESSING",
            transfer_id=transfer_id,
        )

    monkeypatch.setattr(
        service,
        "query_universal_transfer",
        pending_record,
    )

    result = resume_once(env)

    flow = env.state["flow"]
    intent = (
        flow.universal_transfer_intent_json
    )

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_universal_transfer_"
        "pending"
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILING
    )
    assert flow.universal_transfer_status == (
        "PROCESSING"
    )

    assert intent["state"] == "reconciling"
    assert intent[
        "reconciliation"
    ]["exact_match"] is True
    assert intent[
        "reconciliation"
    ]["observed_status"] == (
        "PROCESSING"
    )

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_exact_success_transfer_is_confirmed_without_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    resume_once(env)
    resume_once(env)
    resume_once(env)

    post_count_before = len(
        env.client.post_calls
    )

    def success_record(
        bybit_client,
        *,
        transfer_id,
    ):
        assert env.db.lock_active is False

        env.db.events.append(
            "query_universal_transfer"
        )

        bybit_client.get_calls.append(
            {
                "path": (
                    "/v5/asset/transfer/"
                    "query-universal-transfer-list"
                ),
                "params": {
                    "transferId": transfer_id,
                },
            }
        )

        return make_transfer_record(
            status="COMPLETED",
            transfer_id=transfer_id,
        )

    monkeypatch.setattr(
        service,
        "query_universal_transfer",
        success_record,
    )

    result = resume_once(env)

    flow = env.state["flow"]
    intent = (
        flow.universal_transfer_intent_json
    )

    assert result.ok is True
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_universal_transfer_"
        "confirmed"
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED
    )
    assert flow.universal_transfer_status == (
        "COMPLETED"
    )
    assert flow.universal_transfer_confirmed_at == (
        NOW
    )

    assert intent["state"] == "confirmed"
    assert intent[
        "reconciliation"
    ]["exact_match"] is True

    assert result.diagnostics[
        "next_transition"
    ] == (
        "master_transferable_balance_barrier"
    )

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_transfer_record_mismatch_fails_requires_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    resume_once(env)
    resume_once(env)
    resume_once(env)

    post_count_before = len(
        env.client.post_calls
    )

    def mismatched_record(
        bybit_client,
        *,
        transfer_id,
    ):
        assert env.db.lock_active is False

        env.db.events.append(
            "query_universal_transfer"
        )

        bybit_client.get_calls.append(
            {
                "path": (
                    "/v5/asset/transfer/"
                    "query-universal-transfer-list"
                ),
                "params": {
                    "transferId": transfer_id,
                },
            }
        )

        return make_transfer_record(
            status="SUCCESS",
            transfer_id=transfer_id,
            amount_usdt=Decimal("102"),
        )

    monkeypatch.setattr(
        service,
        "query_universal_transfer",
        mismatched_record,
    )

    result = resume_once(env)

    flow = env.state["flow"]
    intent = (
        flow.universal_transfer_intent_json
    )

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_universal_transfer_"
        "mismatch"
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert "amount mismatch" in str(
        result.error
    )

    assert intent["state"] == (
        "failed_requires_review"
    )
    assert intent[
        "reconciliation"
    ]["exact_match"] is False

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_unknown_terminal_transfer_status_requires_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    resume_once(env)
    resume_once(env)
    resume_once(env)

    post_count_before = len(
        env.client.post_calls
    )

    def failed_record(
        bybit_client,
        *,
        transfer_id,
    ):
        assert env.db.lock_active is False

        env.db.events.append(
            "query_universal_transfer"
        )

        bybit_client.get_calls.append(
            {
                "path": (
                    "/v5/asset/transfer/"
                    "query-universal-transfer-list"
                ),
                "params": {
                    "transferId": transfer_id,
                },
            }
        )

        return make_transfer_record(
            status="FAILED",
            transfer_id=transfer_id,
        )

    monkeypatch.setattr(
        service,
        "query_universal_transfer",
        failed_record,
    )

    result = resume_once(env)

    flow = env.state["flow"]
    intent = (
        flow.universal_transfer_intent_json
    )

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_universal_transfer_"
        "terminal_status_review"
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert "unsupported terminal status" in str(
        result.error
    )

    assert intent["state"] == (
        "failed_requires_review"
    )
    assert intent[
        "reconciliation"
    ][
        "terminal_status_requires_review"
    ] is True

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_reconciliation_query_error_stays_pending_without_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    resume_once(env)
    resume_once(env)
    resume_once(env)

    post_count_before = len(
        env.client.post_calls
    )

    def query_error(
        bybit_client,
        *,
        transfer_id,
    ):
        assert env.db.lock_active is False

        env.db.events.append(
            "query_universal_transfer_error"
        )

        bybit_client.get_calls.append(
            {
                "path": (
                    "/v5/asset/transfer/"
                    "query-universal-transfer-list"
                ),
                "params": {
                    "transferId": transfer_id,
                },
            }
        )

        raise BybitApiError(
            "simulated reconciliation GET failure"
        )

    monkeypatch.setattr(
        service,
        "query_universal_transfer",
        query_error,
    )

    result = resume_once(env)

    flow = env.state["flow"]
    intent = (
        flow.universal_transfer_intent_json
    )
    reconciliation = intent[
        "reconciliation"
    ]

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_universal_transfer_"
        "query_pending"
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILING
    )
    assert intent["state"] == "reconciling"

    assert reconciliation[
        "record_found"
    ] is False
    assert reconciliation[
        "query_succeeded"
    ] is False
    assert "simulated reconciliation GET failure" in (
        reconciliation["query_error"]
    )

    assert result.diagnostics[
        "no_automatic_resend"
    ] is True

    assert len(
        env.client.post_calls
    ) == post_count_before
