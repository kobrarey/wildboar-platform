from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

import app.settlement.negative_bybit_flow_live_service as service
from app.bybit.asset_flows import (
    BybitAccountCoinBalance,
    BybitCoinChainInfo,
    BybitUniversalTransferResult,
    BybitWithdrawalResult,
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
    BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT,
    BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING,
    BATCH_STATUS_NEGATIVE_NET_SALE_EXECUTED,
    BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_PENDING,
    BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_RECONCILING,
    BYBIT_FLOW_STATUS_COMPLETED,
    BYBIT_FLOW_STATUS_CREATED,
    BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW,
    BYBIT_FLOW_STATUS_MASTER_BALANCE_CONFIRMED,
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
        settlement_wallet_id=None,
        settlement_wallet_address=None,
        settlement_wallet_balance_before_usdt=None,
        settlement_wallet_balance_after_usdt=None,
        settlement_wallet_receipt_confirmations=None,
        settlement_wallet_receipt_block_number=None,
        settlement_wallet_receipt_status=None,
        settlement_wallet_received_usdt=None,
        settlement_wallet_receipt_tx_hash=None,
        settlement_wallet_receipt_confirmed_at=None,
        settlement_wallet_receipt_json=None,
        withdrawal_policy_version=None,
        coin_info_snapshot_json=None,
        withdrawal_request_id=None,
        withdrawal_id=None,
        withdrawal_status=None,
        withdrawal_amount_usdt=None,
        withdrawal_fee_usdt=None,
        withdrawal_coin=None,
        withdrawal_chain=None,
        withdrawal_address=None,
        withdrawal_tx_hash=None,
        withdrawal_created_at=None,
        withdrawal_confirmed_at=None,
        withdrawal_record_json=None,
        withdrawal_reconciliation_json=None,
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


def make_master_balance(
    *,
    account_type: str = "FUND",
    coin: str = "USDT",
    member_id: str = "90001",
    wallet_balance: Decimal = Decimal("101"),
    transfer_balance: Decimal = Decimal("101"),
    transfer_safe_amount: Decimal | None = (
        Decimal("101")
    ),
    ltv_transfer_safe_amount: Decimal | None = (
        Decimal("101")
    ),
) -> BybitAccountCoinBalance:
    return BybitAccountCoinBalance(
        account_type=account_type,
        coin=coin,
        member_id=member_id,
        wallet_balance=wallet_balance,
        transfer_balance=transfer_balance,
        transfer_safe_amount=(
            transfer_safe_amount
        ),
        ltv_transfer_safe_amount=(
            ltv_transfer_safe_amount
        ),
        raw={
            "accountType": account_type,
            "coin": coin,
            "memberId": member_id,
            "walletBalance": format(
                wallet_balance,
                "f",
            ),
            "transferBalance": format(
                transfer_balance,
                "f",
            ),
            "transferSafeAmount": (
                format(
                    transfer_safe_amount,
                    "f",
                )
                if transfer_safe_amount
                is not None
                else None
            ),
            "ltvTransferSafeAmount": (
                format(
                    ltv_transfer_safe_amount,
                    "f",
                )
                if ltv_transfer_safe_amount
                is not None
                else None
            ),
        },
    )


def advance_to_reconciled_transfer(
    env: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    resume_once(env)
    resume_once(env)
    resume_once(env)

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
    flow = env.state["flow"]

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
    assert flow.universal_transfer_intent_json[
        "state"
    ] == "confirmed"

    return flow


def install_master_balance_query(
    monkeypatch: pytest.MonkeyPatch,
    env: SimpleNamespace,
    *,
    balance: BybitAccountCoinBalance | None = None,
    error: BaseException | None = None,
) -> None:
    def query_balance(
        bybit_client,
        *,
        account_type,
        coin,
        member_id,
        with_transfer_safe_amount,
        with_ltv_transfer_safe_amount,
    ):
        assert env.db.lock_active is False

        assert account_type == "FUND"
        assert coin == "USDT"
        assert member_id == "90001"

        assert (
            with_transfer_safe_amount
            is True
        )
        assert (
            with_ltv_transfer_safe_amount
            is True
        )

        env.db.events.append(
            "query_master_transferable_balance"
        )

        bybit_client.get_calls.append(
            {
                "path": (
                    "/v5/asset/transfer/"
                    "query-account-coin-balance"
                ),
                "params": {
                    "accountType": account_type,
                    "coin": coin,
                    "memberId": member_id,
                    "withTransferSafeAmount": 1,
                    "withLtvTransferSafeAmount": 1,
                },
            }
        )

        if error is not None:
            raise error

        assert balance is not None
        return balance

    monkeypatch.setattr(
        service,
        "query_account_coin_balance",
        query_balance,
    )


def advance_to_master_balance_confirmed(
    env: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    flow = advance_to_reconciled_transfer(
        env,
        monkeypatch,
    )

    install_master_balance_query(
        monkeypatch,
        env,
        balance=make_master_balance(
            transfer_balance=Decimal("101"),
        ),
    )

    result = resume_once(env)

    assert result.ok is True
    assert result.diagnostics[
        "transition"
    ] == (
        "master_transferable_balance_confirmed"
    )
    assert flow.status == (
        BYBIT_FLOW_STATUS_MASTER_BALANCE_CONFIRMED
    )

    return flow


def make_coin_info(
    *,
    withdraw_fee: Decimal = Decimal("1"),
    withdraw_min: Decimal = Decimal("10"),
    min_accuracy: int = 6,
    chain_withdraw: str | None = "1",
    withdraw_percentage_fee: (
        Decimal | None
    ) = Decimal("0"),
    withdraw_max: Decimal | None = (
        Decimal("1000000")
    ),
) -> BybitCoinChainInfo:
    return BybitCoinChainInfo(
        coin="USDT",
        chain="BSC",
        withdraw_fee=withdraw_fee,
        withdraw_min=withdraw_min,
        min_accuracy=min_accuracy,
        chain_withdraw=chain_withdraw,
        withdraw_percentage_fee=(
            withdraw_percentage_fee
        ),
        withdraw_max=withdraw_max,
        raw={
            "coin": "USDT",
            "chain": "BSC",
            "withdrawFee": format(
                withdraw_fee,
                "f",
            ),
            "withdrawMin": format(
                withdraw_min,
                "f",
            ),
            "minAccuracy": str(
                min_accuracy
            ),
            "chainWithdraw": chain_withdraw,
            "withdrawPercentageFee": (
                format(
                    withdraw_percentage_fee,
                    "f",
                )
                if (
                    withdraw_percentage_fee
                    is not None
                )
                else None
            ),
            "withdrawMax": (
                format(
                    withdraw_max,
                    "f",
                )
                if withdraw_max is not None
                else None
            ),
        },
    )


def install_withdrawal_prepare_reads(
    monkeypatch: pytest.MonkeyPatch,
    env: SimpleNamespace,
    *,
    coin_info: BybitCoinChainInfo | None = None,
    baseline_balance: Decimal = Decimal("7.25"),
    baseline_block: int = 55500000,
) -> SimpleNamespace:
    wallet = SimpleNamespace(
        id=404,
        fund_id=7,
        blockchain="BSC",
        wallet_type="settlement",
        is_active=True,
        address=(
            "0x1111111111111111111111111111111111111111"
        ),
    )

    resolved_coin_info = (
        coin_info
        if coin_info is not None
        else make_coin_info()
    )

    raw_balance = int(
        baseline_balance
        * (
            Decimal("10")
            ** 18
        )
    )

    baseline = {
        "address": wallet.address,
        "contract": (
            service.settings.BSC_USDT_CONTRACT
        ),
        "block_number": baseline_block,
        "decimals": 18,
        "raw_balance": str(raw_balance),
        "balance_usdt": format(
            baseline_balance,
            "f",
        ),
    }

    wallet_calls = {
        "count": 0,
    }

    def active_wallet(
        db,
        *,
        fund_id,
    ):
        assert db is env.db
        assert fund_id == 7
        assert env.db.lock_active is True

        wallet_calls["count"] += 1
        env.db.events.append(
            "lock:settlement_wallet"
        )

        return wallet

    def query_coin(
        bybit_client,
        *,
        coin,
        chain,
    ):
        assert env.db.lock_active is False
        assert coin == "USDT"
        assert chain == "BSC"

        env.db.events.append(
            "query_coin_info"
        )

        bybit_client.get_calls.append(
            {
                "path": (
                    "/v5/asset/coin/query-info"
                ),
                "params": {
                    "coin": coin,
                    "chain": chain,
                },
            }
        )

        return resolved_coin_info

    def query_baseline(
        address,
    ):
        assert env.db.lock_active is False
        assert address == wallet.address

        env.db.events.append(
            "query_settlement_wallet_baseline"
        )

        return deepcopy(baseline)

    monkeypatch.setattr(
        service,
        "_get_active_settlement_wallet",
        active_wallet,
    )
    monkeypatch.setattr(
        service,
        "query_coin_info",
        query_coin,
    )
    monkeypatch.setattr(
        service,
        "_query_settlement_wallet_usdt_baseline",
        query_baseline,
    )

    return SimpleNamespace(
        wallet=wallet,
        coin_info=resolved_coin_info,
        baseline=baseline,
        wallet_calls=wallet_calls,
    )


def advance_to_withdrawal_intent_prepared(
    env: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    flow = advance_to_master_balance_confirmed(
        env,
        monkeypatch,
    )

    reads = install_withdrawal_prepare_reads(
        monkeypatch,
        env,
    )

    result = resume_once(env)

    assert result.ok is True
    assert result.diagnostics[
        "transition"
    ] == "prepare_withdrawal_intent"

    assert flow.status == (
        BYBIT_FLOW_STATUS_WITHDRAWAL_INTENT_PREPARED
    )

    assert isinstance(
        flow.withdrawal_intent_json,
        dict,
    )

    return SimpleNamespace(
        flow=flow,
        reads=reads,
    )


def install_withdrawal_submit_fakes(
    monkeypatch: pytest.MonkeyPatch,
    env: SimpleNamespace,
    *,
    guard_error: BaseException | None = None,
    post_error: BaseException | None = None,
    acknowledgement_amount: Decimal = (
        Decimal("100")
    ),
) -> SimpleNamespace:
    guard_calls: list[dict[str, Any]] = []

    def require_guard(
        db_arg,
        **kwargs,
    ):
        assert db_arg is env.db
        assert env.db.lock_active is False
        assert env.db.events[-1] == "commit"

        flow = env.state["flow"]
        intent = flow.withdrawal_intent_json

        assert flow.status == (
            BYBIT_FLOW_STATUS_WITHDRAWAL_SUBMITTING
        )
        assert intent["state"] == "submitting"

        claim = intent["submit_claim"]

        assert isinstance(claim, dict)
        assert claim[
            "submit_attempt_number"
        ] == 1
        assert claim[
            "no_automatic_resend"
        ] is True

        guard_calls.append(
            deepcopy(kwargs)
        )

        env.db.events.append(
            "withdrawal_operation_guard"
        )

        if guard_error is not None:
            raise guard_error

        return SimpleNamespace(
            allowed=True,
            event_id=929,
        )

    def create_withdrawal(
        bybit_client,
        **kwargs,
    ):
        assert env.db.lock_active is False
        assert env.db.events[-1] == "commit"

        env.db.events.append(
            "withdrawal_post"
        )

        bybit_client.post_calls.append(
            deepcopy(kwargs)
        )

        if post_error is not None:
            raise post_error

        return BybitWithdrawalResult(
            request_id=kwargs[
                "request_id"
            ],
            withdrawal_id="withdrawal-123",
            coin=kwargs["coin"],
            chain=kwargs["chain"],
            address=kwargs["address"],
            amount_usdt=(
                acknowledgement_amount
            ),
            fee_type=kwargs["fee_type"],
            status="PENDING",
            tx_hash=None,
            raw={
                "retCode": 0,
                "result": {
                    "requestId": kwargs[
                        "request_id"
                    ],
                    "withdrawalId": (
                        "withdrawal-123"
                    ),
                    "coin": kwargs["coin"],
                    "chain": kwargs["chain"],
                    "address": kwargs[
                        "address"
                    ],
                    "amount": format(
                        acknowledgement_amount,
                        "f",
                    ),
                    "feeType": kwargs[
                        "fee_type"
                    ],
                    "status": "PENDING",
                },
            },
        )

    monkeypatch.setattr(
        service,
        "require_bybit_master_withdrawal_guard",
        require_guard,
    )

    monkeypatch.setattr(
        service,
        "create_master_withdrawal",
        create_withdrawal,
    )

    return SimpleNamespace(
        guard_calls=guard_calls,
    )


def make_withdrawal_record(
    *,
    request_id: str,
    status: str,
    withdrawal_id: str | None = (
        "withdrawal-123"
    ),
    coin: str = "USDT",
    chain: str = "BSC",
    address: str = (
        "0x1111111111111111111111111111111111111111"
    ),
    amount_usdt: Decimal = Decimal("100"),
    fee_type: int = 0,
    tx_hash: str | None = None,
) -> BybitWithdrawalResult:
    return BybitWithdrawalResult(
        request_id=request_id,
        withdrawal_id=withdrawal_id,
        coin=coin,
        chain=chain,
        address=address,
        amount_usdt=amount_usdt,
        fee_type=fee_type,
        status=status,
        tx_hash=tx_hash,
        raw={
            "requestId": request_id,
            "withdrawalId": withdrawal_id,
            "coin": coin,
            "chain": chain,
            "address": address,
            "amount": format(
                amount_usdt,
                "f",
            ),
            "feeType": fee_type,
            "status": status,
            "txID": tx_hash,
        },
    )


def install_withdrawal_reconciliation_reads(
    monkeypatch: pytest.MonkeyPatch,
    env: SimpleNamespace,
    *,
    exact_record: (
        BybitWithdrawalResult | None
    ) = None,
    bounded_records: (
        list[BybitWithdrawalResult] | None
    ) = None,
    exact_error: BaseException | None = None,
    bounded_error: BaseException | None = None,
) -> SimpleNamespace:
    exact_calls: list[str] = []
    bounded_calls: list[dict[str, Any]] = []

    resolved_bounded_records = list(
        bounded_records or []
    )

    def query_exact(
        bybit_client,
        *,
        request_id,
    ):
        assert env.db.lock_active is False

        flow = env.state["flow"]

        assert request_id == (
            flow.withdrawal_request_id
        )

        exact_calls.append(request_id)

        env.db.events.append(
            "query_master_withdrawal"
        )

        bybit_client.get_calls.append(
            {
                "path": (
                    "/v5/asset/withdraw/"
                    "query-record"
                ),
                "params": {
                    "requestId": request_id,
                },
            }
        )

        if exact_error is not None:
            raise exact_error

        return exact_record

    def query_bounded(
        bybit_client,
        *,
        coin,
        start_time_ms,
        end_time_ms,
        limit,
    ):
        assert env.db.lock_active is False

        flow = env.state["flow"]

        assert coin == "USDT"
        assert limit == (
            service
            .WITHDRAWAL_RECORD_LOOKUP_LIMIT
        )

        lookback_hours = int(
            service.settings
            .NEGATIVE_NET_BYBIT_RECORD_LOOKBACK_HOURS
        )

        expected_start_ms = int(
            (
                flow.withdrawal_submitted_at
                - timedelta(
                    hours=lookback_hours
                )
            ).timestamp()
            * 1000
        )

        assert start_time_ms == (
            expected_start_ms
        )
        assert end_time_ms == int(
            NOW.timestamp() * 1000
        )

        call = {
            "coin": coin,
            "start_time_ms": start_time_ms,
            "end_time_ms": end_time_ms,
            "limit": limit,
        }

        bounded_calls.append(
            deepcopy(call)
        )

        env.db.events.append(
            "list_master_withdrawals"
        )

        bybit_client.get_calls.append(
            {
                "path": (
                    "/v5/asset/withdraw/"
                    "query-record"
                ),
                "params": deepcopy(call),
            }
        )

        if bounded_error is not None:
            raise bounded_error

        return list(
            resolved_bounded_records
        )

    monkeypatch.setattr(
        service,
        "query_master_withdrawal",
        query_exact,
    )

    monkeypatch.setattr(
        service,
        "list_master_withdrawals",
        query_bounded,
    )

    return SimpleNamespace(
        exact_calls=exact_calls,
        bounded_calls=bounded_calls,
    )


def advance_to_withdrawal_reconciling(
    env: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    prepared = (
        advance_to_withdrawal_intent_prepared(
            env,
            monkeypatch,
        )
    )

    install_withdrawal_submit_fakes(
        monkeypatch,
        env,
    )

    result = resume_once(env)

    assert result.ok is True
    assert result.diagnostics[
        "transition"
    ] == "submit_withdrawal"

    assert prepared.flow.status == (
        BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILING
    )

    assert prepared.flow.withdrawal_intent_json[
        "state"
    ] == "reconciling"

    return SimpleNamespace(
        flow=prepared.flow,
        reads=prepared.reads,
    )


def advance_to_withdrawal_reconciled(
    env: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    advanced = (
        advance_to_withdrawal_reconciling(
            env,
            monkeypatch,
        )
    )

    flow = advanced.flow

    record = make_withdrawal_record(
        request_id=flow.withdrawal_request_id,
        status="SUCCESS",
        tx_hash="0xabc123",
    )

    install_withdrawal_reconciliation_reads(
        monkeypatch,
        env,
        exact_record=record,
        bounded_records=[record],
    )

    result = resume_once(env)

    assert result.ok is True
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_withdrawal_confirmed"
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILED
    )

    assert flow.withdrawal_intent_json[
        "state"
    ] == "confirmed"

    assert flow.withdrawal_tx_hash == (
        "0xabc123"
    )

    return SimpleNamespace(
        flow=flow,
        reads=advanced.reads,
        withdrawal_record=record,
    )


def install_bsc_receipt_web3(
    monkeypatch: pytest.MonkeyPatch,
    env: SimpleNamespace,
    *,
    get_web3_error: (
        BaseException | None
    ) = None,
    receipt_error: (
        BaseException | None
    ) = None,
    receipt_present: bool = True,
    receipt_status: int = 1,
    receipt_tx_hash: str = "0xabc123",
    receipt_block_number: int = 55500010,
    current_block_number: int = 55500021,
    current_block_error: (
        BaseException | None
    ) = None,
    transfer_amounts_usdt: tuple[
        Decimal,
        ...,
    ] = (
        Decimal("100"),
    ),
    transfer_destination: (
        str | None
    ) = None,
    balance_after_usdt: Decimal = (
        Decimal("107.25")
    ),
) -> SimpleNamespace:
    flow = env.state["flow"]

    wallet_address = str(
        flow.settlement_wallet_address
    )

    contract_address = str(
        service.settings.BSC_USDT_CONTRACT
    )

    decimals = int(
        service.settings.BSC_USDT_DECIMALS
    )

    transfer_topic = (
        "0x"
        + ("ab" * 32)
    )

    source_topic = (
        "0x"
        + ("0" * 24)
        + ("2" * 40)
    )

    resolved_destination = (
        transfer_destination
        if transfer_destination is not None
        else wallet_address
    )

    destination_topic = (
        "0x"
        + ("0" * 24)
        + resolved_destination[
            2:
        ].lower()
    )

    logs: list[dict[str, Any]] = []

    for log_index, amount_usdt in enumerate(
        transfer_amounts_usdt
    ):
        amount_raw_decimal = (
            Decimal(amount_usdt)
            * (
                Decimal("10")
                ** decimals
            )
        )

        amount_raw = int(
            amount_raw_decimal
        )

        assert Decimal(amount_raw) == (
            amount_raw_decimal
        )

        logs.append(
            {
                "address": contract_address,
                "topics": [
                    transfer_topic,
                    source_topic,
                    destination_topic,
                ],
                "data": amount_raw,
                "logIndex": log_index,
            }
        )

    receipt = (
        {
            "transactionHash": (
                receipt_tx_hash
            ),
            "status": receipt_status,
            "blockNumber": (
                receipt_block_number
            ),
            "logs": logs,
        }
        if receipt_present
        else None
    )

    balance_after_raw_decimal = (
        Decimal(balance_after_usdt)
        * (
            Decimal("10")
            ** decimals
        )
    )

    balance_after_raw = int(
        balance_after_raw_decimal
    )

    assert Decimal(balance_after_raw) == (
        balance_after_raw_decimal
    )

    receipt_calls: list[str] = []
    balance_calls: list[
        dict[str, Any]
    ] = []
    contract_calls: list[
        dict[str, Any]
    ] = []

    class FakeBalanceCall:
        def __init__(
            self,
            address: str,
        ) -> None:
            self.address = address

        def call(
            self,
            *,
            block_identifier,
        ) -> int:
            balance_calls.append(
                {
                    "address": self.address,
                    "block_identifier": int(
                        block_identifier
                    ),
                }
            )

            return balance_after_raw

    class FakeContractFunctions:
        def balanceOf(
            self,
            address,
        ):
            return FakeBalanceCall(
                str(address)
            )

    class FakeContract:
        def __init__(self) -> None:
            self.functions = (
                FakeContractFunctions()
            )

    class FakeEth:
        def get_transaction_receipt(
            self,
            tx_hash,
        ):
            receipt_calls.append(
                str(tx_hash)
            )

            if receipt_error is not None:
                raise receipt_error

            return deepcopy(receipt)

        @property
        def block_number(self):
            if current_block_error is not None:
                raise current_block_error

            return current_block_number

        def contract(
            self,
            *,
            address,
            abi,
        ):
            contract_calls.append(
                {
                    "address": str(address),
                    "abi": deepcopy(abi),
                }
            )

            return FakeContract()

    class FakeWeb3:
        def __init__(self) -> None:
            self.eth = FakeEth()

        @staticmethod
        def to_checksum_address(
            address,
        ):
            return str(address)

        @staticmethod
        def keccak(
            *,
            text,
        ):
            assert text == (
                service
                .ERC20_TRANSFER_EVENT_SIGNATURE
            )

            return bytes.fromhex(
                "ab" * 32
            )

    fake_web3 = FakeWeb3()

    def get_web3():
        assert env.db.lock_active is False

        env.db.events.append(
            "bsc_receipt_rpc"
        )

        if get_web3_error is not None:
            raise get_web3_error

        return fake_web3

    monkeypatch.setattr(
        service,
        "get_web3",
        get_web3,
    )

    return SimpleNamespace(
        web3=fake_web3,
        receipt_calls=receipt_calls,
        balance_calls=balance_calls,
        contract_calls=contract_calls,
        receipt=receipt,
        logs=logs,
    )


def advance_to_settlement_wallet_receipt_confirmed(
    env: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> SimpleNamespace:
    advanced = (
        advance_to_withdrawal_reconciled(
            env,
            monkeypatch,
        )
    )

    calls = install_bsc_receipt_web3(
        monkeypatch,
        env,
        receipt_status=1,
        receipt_tx_hash="0xabc123",
        receipt_block_number=55500010,
        current_block_number=55500021,
        transfer_amounts_usdt=(
            Decimal("100"),
        ),
        balance_after_usdt=Decimal(
            "107.25"
        ),
    )

    result = resume_once(env)

    assert result.ok is True
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_settlement_wallet_"
        "receipt_confirmed"
    )

    assert advanced.flow.status == (
        BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_CONFIRMED
    )

    assert env.batch.status == (
        BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_RECONCILING
    )

    return SimpleNamespace(
        flow=advanced.flow,
        calls=calls,
        withdrawal_record=(
            advanced.withdrawal_record
        ),
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


def test_master_balance_insufficient_stays_pending_without_withdrawal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    flow = advance_to_reconciled_transfer(
        env,
        monkeypatch,
    )

    post_count_before = len(
        env.client.post_calls
    )
    get_count_before = len(
        env.client.get_calls
    )

    install_master_balance_query(
        monkeypatch,
        env,
        balance=make_master_balance(
            wallet_balance=Decimal("101"),
            transfer_balance=Decimal("100"),
        ),
    )

    result = resume_once(env)

    barrier = flow.reconciliation_json[
        "master_transferable_balance_barrier"
    ]

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "master_transferable_balance_pending"
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED
    )
    assert env.batch.status == (
        BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
    )

    assert barrier["state"] == "pending"
    assert barrier[
        "required_master_usdt"
    ] == "101"
    assert barrier[
        "balance"
    ]["transfer_balance"] == "100"
    assert barrier["sufficient"] is False
    assert barrier[
        "withdrawal_allowed"
    ] is False

    assert result.diagnostics[
        "withdrawal_allowed"
    ] is False

    assert flow.withdrawal_intent_json is None
    assert flow.withdrawal_submitted_at is None

    assert len(
        env.client.get_calls
    ) == get_count_before + 1

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_master_balance_sufficient_confirms_and_next_cycle_skips_balance_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    flow = advance_to_reconciled_transfer(
        env,
        monkeypatch,
    )

    post_count_before = len(
        env.client.post_calls
    )
    get_count_before = len(
        env.client.get_calls
    )

    install_master_balance_query(
        monkeypatch,
        env,
        balance=make_master_balance(
            transfer_balance=Decimal("101"),
        ),
    )

    result = resume_once(env)

    barrier = flow.reconciliation_json[
        "master_transferable_balance_barrier"
    ]

    assert result.ok is True
    assert result.diagnostics[
        "transition"
    ] == (
        "master_transferable_balance_confirmed"
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_MASTER_BALANCE_CONFIRMED
    )

    assert barrier["state"] == "confirmed"
    assert barrier["sufficient"] is True
    assert barrier[
        "withdrawal_allowed"
    ] is True

    assert result.diagnostics[
        "next_transition"
    ] == "prepare_withdrawal_intent"

    assert flow.withdrawal_intent_json is None
    assert flow.withdrawal_submitted_at is None

    assert len(
        env.client.get_calls
    ) == get_count_before + 1

    assert len(
        env.client.post_calls
    ) == post_count_before

    def unexpected_balance_query(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "Confirmed master balance must not "
            "repeat balance GET"
        )

    monkeypatch.setattr(
        service,
        "query_account_coin_balance",
        unexpected_balance_query,
    )

    install_withdrawal_prepare_reads(
        monkeypatch,
        env,
    )

    get_count_after_confirm = len(
        env.client.get_calls
    )
    post_count_after_confirm = len(
        env.client.post_calls
    )

    next_step = resume_once(env)

    assert next_step.ok is True
    assert next_step.idempotent is False

    assert next_step.diagnostics[
        "transition"
    ] == "prepare_withdrawal_intent"

    assert next_step.diagnostics[
        "bybit_get_count"
    ] == 1
    assert next_step.diagnostics[
        "bsc_rpc_read_count"
    ] == 1
    assert next_step.diagnostics[
        "bybit_post_count"
    ] == 0

    assert flow.status == (
        BYBIT_FLOW_STATUS_WITHDRAWAL_INTENT_PREPARED
    )
    assert isinstance(
        flow.withdrawal_intent_json,
        dict,
    )

    # Only coin-info GET was added. The master
    # transferable-balance GET was not repeated.
    assert len(
        env.client.get_calls
    ) == get_count_after_confirm + 1

    assert len(
        env.client.post_calls
    ) == post_count_after_confirm


@pytest.mark.parametrize(
    (
        "field_name",
        "field_value",
        "expected_error",
    ),
    [
        (
            "account_type",
            "UNIFIED",
            "account_type mismatch",
        ),
        (
            "coin",
            "USDC",
            "coin mismatch",
        ),
        (
            "member_id",
            "99999",
            "member_id mismatch",
        ),
    ],
)
def test_master_balance_identity_mismatch_requires_review(
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
    field_value: str,
    expected_error: str,
) -> None:
    env = install_service_fakes(monkeypatch)

    flow = advance_to_reconciled_transfer(
        env,
        monkeypatch,
    )

    post_count_before = len(
        env.client.post_calls
    )

    balance_kwargs = {
        field_name: field_value,
    }

    install_master_balance_query(
        monkeypatch,
        env,
        balance=make_master_balance(
            **balance_kwargs,
        ),
    )

    result = resume_once(env)

    barrier = flow.reconciliation_json[
        "master_transferable_balance_barrier"
    ]

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "master_transferable_balance_mismatch"
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert expected_error in str(
        result.error
    )

    assert barrier["state"] == (
        "failed_requires_review"
    )
    assert barrier[
        "withdrawal_allowed"
    ] is False

    assert result.diagnostics[
        "reserve_release_allowed"
    ] is False
    assert result.diagnostics[
        "pricing_unlock_allowed"
    ] is False

    assert flow.withdrawal_intent_json is None
    assert flow.withdrawal_submitted_at is None

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_master_balance_query_error_blocks_withdrawal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    flow = advance_to_reconciled_transfer(
        env,
        monkeypatch,
    )

    post_count_before = len(
        env.client.post_calls
    )
    get_count_before = len(
        env.client.get_calls
    )

    install_master_balance_query(
        monkeypatch,
        env,
        error=BybitApiError(
            "simulated master balance GET failure"
        ),
    )

    result = resume_once(env)

    barrier = flow.reconciliation_json[
        "master_transferable_balance_barrier"
    ]

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "master_transferable_balance_"
        "query_pending"
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_UNIVERSAL_TRANSFER_RECONCILED
    )
    assert env.batch.status == (
        BATCH_STATUS_NEGATIVE_NET_MASTER_FLOW_PROCESSING
    )

    assert barrier["state"] == "pending"
    assert barrier[
        "query_succeeded"
    ] is False
    assert barrier[
        "withdrawal_allowed"
    ] is False

    assert (
        "simulated master balance GET failure"
        in barrier["query_error"]
    )

    assert result.diagnostics[
        "withdrawal_allowed"
    ] is False

    assert flow.withdrawal_intent_json is None
    assert flow.withdrawal_submitted_at is None

    assert len(
        env.client.get_calls
    ) == get_count_before + 1

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_post_acknowledgement_mismatch_records_post_and_requires_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    resume_once(env)
    resume_once(env)

    def mismatched_acknowledgement(
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

        return BybitUniversalTransferResult(
            transfer_id=kwargs[
                "transfer_id"
            ],
            coin=kwargs["coin"],
            amount_usdt=Decimal("102"),
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
                    "coin": kwargs["coin"],
                    "amount": "102",
                    "status": "PENDING",
                },
            },
        )

    monkeypatch.setattr(
        service,
        "create_universal_transfer",
        mismatched_acknowledgement,
    )

    result = resume_once(env)

    flow = env.state["flow"]
    intent = (
        flow.universal_transfer_intent_json
    )
    acknowledgement = intent[
        "acknowledgement"
    ]

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "submit_universal_transfer_"
        "ack_mismatch"
    )

    assert result.diagnostics[
        "did_bybit_post"
    ] is True
    assert result.diagnostics[
        "bybit_post_count"
    ] == 1
    assert result.diagnostics[
        "no_automatic_resend"
    ] is True
    assert result.diagnostics[
        "reserve_release_allowed"
    ] is False
    assert result.diagnostics[
        "pricing_unlock_allowed"
    ] is False

    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert intent["state"] == (
        "failed_requires_review"
    )
    assert acknowledgement[
        "outcome"
    ] == "mismatch"
    assert acknowledgement[
        "bybit_post_performed"
    ] is True
    assert acknowledgement[
        "no_automatic_resend"
    ] is True

    assert acknowledgement[
        "expected"
    ]["amount_usdt"] == "101"
    assert acknowledgement[
        "observed"
    ]["amount_usdt"] == "102"
    assert acknowledgement[
        "response"
    ]["result"]["amount"] == "102"

    assert len(
        env.client.post_calls
    ) == 1

    get_count_before_rerun = len(
        env.client.get_calls
    )
    post_count_before_rerun = len(
        env.client.post_calls
    )

    rerun = resume_once(env)

    assert rerun.ok is False
    assert rerun.idempotent is True

    assert rerun.diagnostics[
        "transition"
    ] == (
        "failed_requires_review_"
        "already_recorded"
    )

    assert rerun.diagnostics[
        "did_bybit_post"
    ] is False
    assert rerun.diagnostics[
        "bybit_post_count"
    ] == 0
    assert rerun.diagnostics[
        "bybit_get_count"
    ] == 0
    assert rerun.diagnostics[
        "no_automatic_resend"
    ] is True

    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert (
        flow
        .universal_transfer_intent_json[
            "acknowledgement"
        ]["outcome"]
        == "mismatch"
    )

    assert len(
        env.client.get_calls
    ) == get_count_before_rerun

    assert len(
        env.client.post_calls
    ) == post_count_before_rerun


def test_prepare_withdrawal_intent_persists_fee_and_baseline_without_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    flow = advance_to_master_balance_confirmed(
        env,
        monkeypatch,
    )

    reads = install_withdrawal_prepare_reads(
        monkeypatch,
        env,
    )

    get_count_before = len(
        env.client.get_calls
    )
    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    intent = flow.withdrawal_intent_json
    assert isinstance(intent, dict)

    payload = intent["payload_template"]
    fee_snapshot = intent["fee_snapshot"]
    baseline = intent[
        "settlement_wallet_balance_baseline"
    ]

    assert result.ok is True
    assert result.diagnostics[
        "transition"
    ] == "prepare_withdrawal_intent"

    assert flow.status == (
        BYBIT_FLOW_STATUS_WITHDRAWAL_INTENT_PREPARED
    )

    assert intent["schema"] == (
        service.WITHDRAWAL_INTENT_SCHEMA
    )
    assert intent["state"] == "prepared"
    assert intent["policy_version"] == (
        "bsc_exact_received_v1"
    )

    assert intent["request_id"] == (
        flow.withdrawal_request_id
    )
    assert len(
        flow.withdrawal_request_id
    ) == 32
    assert flow.withdrawal_request_id.isalnum()
    assert flow.withdrawal_request_id.startswith(
        "wbng"
    )

    assert payload == {
        "requestId": (
            flow.withdrawal_request_id
        ),
        "coin": "USDT",
        "chain": "BSC",
        "address": reads.wallet.address,
        "amount": "100",
        "forceChain": 1,
        "feeType": 0,
        "accountType": "FUND",
    }

    assert intent[
        "payload_fingerprint"
    ] == service._payload_fingerprint(
        payload
    )

    assert intent["amount"] == "100"
    assert intent["fee_usdt"] == "1"
    assert intent["amount_precision"] == 6
    assert intent["timestamp_policy"] == (
        "submit_time_utc_ms"
    )

    assert intent["submit_claim"] is None
    assert intent["acknowledgement"] is None
    assert intent["reconciliation"] is None

    assert fee_snapshot["schema"] == (
        "negative_withdrawal_fee_snapshot_v1"
    )
    assert fee_snapshot[
        "withdraw_fee_usdt"
    ] == "1"
    assert fee_snapshot[
        "withdraw_min_usdt"
    ] == "10"
    assert fee_snapshot[
        "withdraw_max_usdt"
    ] == "1000000"
    assert fee_snapshot[
        "withdraw_percentage_fee"
    ] == "0"
    assert fee_snapshot["min_accuracy"] == 6
    assert fee_snapshot["chain_withdraw"] == "1"
    assert fee_snapshot["max_age_sec"] == (
        service.settings
        .NEGATIVE_NET_WITHDRAWAL_FEE_MAX_AGE_SEC
    )

    assert baseline == reads.baseline
    assert baseline["block_number"] == 55500000
    assert baseline["balance_usdt"] == "7.25"
    assert baseline["raw_balance"] == (
        "7250000000000000000"
    )

    assert flow.settlement_wallet_id == 404
    assert flow.settlement_wallet_address == (
        reads.wallet.address
    )
    assert (
        flow.settlement_wallet_balance_before_usdt
        == Decimal("7.25")
    )

    assert flow.withdrawal_policy_version == (
        "bsc_exact_received_v1"
    )
    assert flow.coin_info_snapshot_json == (
        fee_snapshot
    )

    assert flow.withdrawal_amount_usdt == (
        Decimal("100")
    )
    assert flow.withdrawal_fee_usdt == (
        Decimal("1")
    )
    assert flow.withdrawal_coin == "USDT"
    assert flow.withdrawal_chain == "BSC"
    assert flow.withdrawal_address == (
        reads.wallet.address
    )

    assert flow.withdrawal_submitted_at is None
    assert flow.withdrawal_created_at is None
    assert flow.withdrawal_id is None
    assert flow.withdrawal_tx_hash is None

    assert reads.wallet_calls["count"] == 2

    assert len(
        env.client.get_calls
    ) == get_count_before + 1

    assert len(
        env.client.post_calls
    ) == post_count_before

    assert result.diagnostics[
        "did_bybit_post"
    ] is False
    assert result.diagnostics[
        "bybit_post_count"
    ] == 0
    assert result.diagnostics[
        "bybit_get_count"
    ] == 1
    assert result.diagnostics[
        "bsc_rpc_read_count"
    ] == 1
    assert result.diagnostics[
        "next_transition"
    ] == "submit_withdrawal"


def test_withdrawal_fee_mismatch_fails_closed_and_preserves_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    flow = advance_to_master_balance_confirmed(
        env,
        monkeypatch,
    )

    install_withdrawal_prepare_reads(
        monkeypatch,
        env,
        coin_info=make_coin_info(
            withdraw_fee=Decimal("2"),
        ),
    )

    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == "failed_requires_review"

    assert (
        "withdrawal fee snapshot does not match"
        in str(result.error).lower()
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert flow.withdrawal_intent_json is None
    assert flow.withdrawal_submitted_at is None
    assert flow.withdrawal_created_at is None
    assert flow.withdrawal_id is None

    barrier = flow.reconciliation_json[
        "master_transferable_balance_barrier"
    ]

    assert barrier["state"] == "confirmed"
    assert barrier[
        "withdrawal_allowed"
    ] is True

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_withdrawal_intent_payload_tamper_requires_review_without_external_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    flow = advance_to_master_balance_confirmed(
        env,
        monkeypatch,
    )

    install_withdrawal_prepare_reads(
        monkeypatch,
        env,
    )

    prepared = resume_once(env)

    assert prepared.ok is True
    assert flow.status == (
        BYBIT_FLOW_STATUS_WITHDRAWAL_INTENT_PREPARED
    )

    flow.withdrawal_intent_json[
        "payload_template"
    ]["amount"] = "99"

    get_count_before = len(
        env.client.get_calls
    )
    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == "failed_requires_review"

    assert (
        "payload fingerprint mismatch"
        in str(result.error).lower()
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert flow.withdrawal_submitted_at is None
    assert flow.withdrawal_created_at is None
    assert flow.withdrawal_id is None
    assert flow.withdrawal_tx_hash is None

    barrier = flow.reconciliation_json[
        "master_transferable_balance_barrier"
    ]

    assert barrier["state"] == "confirmed"
    assert barrier[
        "withdrawal_allowed"
    ] is True

    assert len(
        env.client.get_calls
    ) == get_count_before

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_withdrawal_submit_revalidates_fee_claims_and_posts_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    prepared = (
        advance_to_withdrawal_intent_prepared(
            env,
            monkeypatch,
        )
    )

    flow = prepared.flow

    submit_fakes = (
        install_withdrawal_submit_fakes(
            monkeypatch,
            env,
        )
    )

    get_count_before = len(
        env.client.get_calls
    )
    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    intent = flow.withdrawal_intent_json
    claim = intent["submit_claim"]
    acknowledgement = intent[
        "acknowledgement"
    ]

    assert result.ok is True
    assert result.diagnostics[
        "transition"
    ] == "submit_withdrawal"

    assert result.diagnostics[
        "did_bybit_post"
    ] is True
    assert result.diagnostics[
        "bybit_post_count"
    ] == 1
    assert result.diagnostics[
        "bybit_get_count"
    ] == 1
    assert result.diagnostics[
        "no_automatic_resend"
    ] is True

    assert flow.status == (
        BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILING
    )
    assert env.batch.status == (
        BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_RECONCILING
    )

    assert intent["state"] == "reconciling"
    assert isinstance(claim, dict)
    assert claim[
        "submit_attempt_number"
    ] == 1
    assert claim[
        "timestamp_ms"
    ] == int(
        NOW.timestamp() * 1000
    )
    assert claim[
        "no_automatic_resend"
    ] is True

    fee_revalidation = claim[
        "fee_revalidation"
    ]

    assert fee_revalidation["schema"] == (
        "negative_withdrawal_fee_"
        "revalidation_v1"
    )
    assert fee_revalidation[
        "matches_prepared_snapshot"
    ] is True
    assert fee_revalidation[
        "withdraw_fee_usdt"
    ] == "1"
    assert fee_revalidation[
        "min_accuracy"
    ] == 6

    assert acknowledgement[
        "outcome"
    ] == "accepted"
    assert acknowledgement[
        "bybit_post_performed"
    ] is True
    assert acknowledgement[
        "no_automatic_resend"
    ] is True
    assert acknowledgement[
        "guard_event_id"
    ] == 929

    assert flow.withdrawal_id == (
        "withdrawal-123"
    )
    assert flow.withdrawal_status == "PENDING"
    assert flow.withdrawal_amount_usdt == (
        Decimal("100")
    )
    assert flow.withdrawal_fee_usdt == (
        Decimal("1")
    )
    assert flow.withdrawal_created_at == NOW

    assert len(
        submit_fakes.guard_calls
    ) == 1

    assert len(
        env.client.get_calls
    ) == get_count_before + 1

    assert len(
        env.client.post_calls
    ) == post_count_before + 1

    post_call = env.client.post_calls[-1]

    assert post_call[
        "request_id"
    ] == flow.withdrawal_request_id
    assert post_call["coin"] == "USDT"
    assert post_call["chain"] == "BSC"
    assert post_call["amount_str"] == "100"
    assert post_call[
        "amount_usdt"
    ] == Decimal("100")
    assert post_call["fee_type"] == 0
    assert post_call[
        "account_type"
    ] == "FUND"
    assert post_call["force_chain"] == 1
    assert post_call[
        "timestamp_ms"
    ] == int(
        NOW.timestamp() * 1000
    )

    install_withdrawal_reconciliation_reads(
        monkeypatch,
        env,
        exact_record=None,
        bounded_records=[],
    )

    get_count_before_rerun = len(
        env.client.get_calls
    )
    post_count_before_rerun = len(
        env.client.post_calls
    )

    rerun = resume_once(env)

    assert rerun.ok is False
    assert rerun.diagnostics[
        "transition"
    ] == (
        "reconcile_withdrawal_"
        "record_not_found"
    )

    assert rerun.diagnostics[
        "bybit_get_count"
    ] == 2
    assert rerun.diagnostics[
        "bybit_post_count"
    ] == 0
    assert rerun.diagnostics[
        "no_automatic_resend"
    ] is True

    assert len(
        env.client.get_calls
    ) == get_count_before_rerun + 2

    assert len(
        env.client.post_calls
    ) == post_count_before_rerun


def test_withdrawal_fee_change_before_claim_fails_without_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    prepared = (
        advance_to_withdrawal_intent_prepared(
            env,
            monkeypatch,
        )
    )

    flow = prepared.flow

    def changed_fee_query(
        bybit_client,
        *,
        coin,
        chain,
    ):
        assert env.db.lock_active is False
        assert coin == "USDT"
        assert chain == "BSC"

        bybit_client.get_calls.append(
            {
                "path": (
                    "/v5/asset/coin/query-info"
                ),
                "params": {
                    "coin": coin,
                    "chain": chain,
                },
            }
        )

        return make_coin_info(
            withdraw_fee=Decimal("2"),
        )

    def unexpected_guard(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "Guard must not run after fee mismatch"
        )

    def unexpected_post(
        *args,
        **kwargs,
    ):
        raise AssertionError(
            "Withdrawal POST must not run after "
            "fee mismatch"
        )

    monkeypatch.setattr(
        service,
        "query_coin_info",
        changed_fee_query,
    )
    monkeypatch.setattr(
        service,
        "require_bybit_master_withdrawal_guard",
        unexpected_guard,
    )
    monkeypatch.setattr(
        service,
        "create_master_withdrawal",
        unexpected_post,
    )

    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == "failed_requires_review"

    assert (
        "withdrawal fee changed before submit"
        in str(result.error).lower()
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert flow.withdrawal_intent_json[
        "state"
    ] == "prepared"
    assert flow.withdrawal_intent_json[
        "submit_claim"
    ] is None
    assert flow.withdrawal_submitted_at is None
    assert flow.withdrawal_created_at is None
    assert flow.withdrawal_id is None

    barrier = flow.reconciliation_json[
        "master_transferable_balance_barrier"
    ]

    assert barrier["state"] == "confirmed"
    assert barrier[
        "withdrawal_allowed"
    ] is True

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_withdrawal_guard_blocked_after_claim_never_posts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    prepared = (
        advance_to_withdrawal_intent_prepared(
            env,
            monkeypatch,
        )
    )

    flow = prepared.flow

    submit_fakes = (
        install_withdrawal_submit_fakes(
            monkeypatch,
            env,
            guard_error=(
                OperationGuardBlockedError(
                    "simulated withdrawal block"
                )
            ),
        )
    )

    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    intent = flow.withdrawal_intent_json
    acknowledgement = intent[
        "acknowledgement"
    ]

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == "submit_withdrawal_guard_blocked"

    assert result.diagnostics[
        "did_bybit_post"
    ] is False
    assert result.diagnostics[
        "bybit_post_count"
    ] == 0
    assert result.diagnostics[
        "no_automatic_resend"
    ] is True

    assert len(
        submit_fakes.guard_calls
    ) == 1

    assert len(
        env.client.post_calls
    ) == post_count_before

    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert intent["state"] == (
        "failed_requires_review"
    )
    assert isinstance(
        intent["submit_claim"],
        dict,
    )

    assert acknowledgement[
        "outcome"
    ] == "guard_blocked"
    assert acknowledgement[
        "bybit_post_performed"
    ] is False
    assert acknowledgement[
        "no_automatic_resend"
    ] is True

    assert flow.withdrawal_status == (
        "GUARD_BLOCKED"
    )
    assert flow.withdrawal_created_at is None
    assert flow.withdrawal_id is None

    get_count_before_rerun = len(
        env.client.get_calls
    )
    post_count_before_rerun = len(
        env.client.post_calls
    )

    rerun = resume_once(env)

    assert rerun.ok is False
    assert rerun.idempotent is True
    assert rerun.diagnostics[
        "transition"
    ] == (
        "failed_requires_review_"
        "already_recorded"
    )

    assert len(
        env.client.get_calls
    ) == get_count_before_rerun
    assert len(
        env.client.post_calls
    ) == post_count_before_rerun


def test_withdrawal_process_crash_after_claim_never_resends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    prepared = (
        advance_to_withdrawal_intent_prepared(
            env,
            monkeypatch,
        )
    )

    flow = prepared.flow

    install_withdrawal_submit_fakes(
        monkeypatch,
        env,
        post_error=RuntimeError(
            "simulated process crash"
        ),
    )

    post_count_before = len(
        env.client.post_calls
    )

    with pytest.raises(
        RuntimeError,
        match="simulated process crash",
    ):
        resume_once(env)

    intent = flow.withdrawal_intent_json

    assert flow.status == (
        BYBIT_FLOW_STATUS_WITHDRAWAL_SUBMITTING
    )
    assert env.batch.status == (
        BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_PENDING
    )

    assert intent["state"] == "submitting"
    assert isinstance(
        intent["submit_claim"],
        dict,
    )
    assert intent["acknowledgement"] is None
    assert flow.withdrawal_submitted_at == NOW
    assert flow.withdrawal_created_at is None

    assert len(
        env.client.post_calls
    ) == post_count_before + 1

    install_withdrawal_reconciliation_reads(
        monkeypatch,
        env,
        exact_record=None,
        bounded_records=[],
    )

    get_count_before_rerun = len(
        env.client.get_calls
    )
    post_count_before_rerun = len(
        env.client.post_calls
    )

    rerun = resume_once(env)

    assert rerun.ok is False
    assert rerun.diagnostics[
        "transition"
    ] == (
        "reconcile_withdrawal_"
        "record_not_found"
    )

    assert rerun.diagnostics[
        "bybit_get_count"
    ] == 2
    assert rerun.diagnostics[
        "bybit_post_count"
    ] == 0
    assert rerun.diagnostics[
        "no_automatic_resend"
    ] is True

    assert flow.status == (
        BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILING
    )
    assert flow.withdrawal_intent_json[
        "state"
    ] == "reconciling"

    assert len(
        env.client.get_calls
    ) == get_count_before_rerun + 2

    assert len(
        env.client.post_calls
    ) == post_count_before_rerun


def test_withdrawal_post_unknown_moves_to_reconciliation_without_resend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    prepared = (
        advance_to_withdrawal_intent_prepared(
            env,
            monkeypatch,
        )
    )

    flow = prepared.flow

    install_withdrawal_submit_fakes(
        monkeypatch,
        env,
        post_error=BybitApiError(
            "simulated withdrawal timeout"
        ),
    )

    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    intent = flow.withdrawal_intent_json
    acknowledgement = intent[
        "acknowledgement"
    ]

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == "submit_withdrawal_unknown"

    assert result.diagnostics[
        "did_bybit_post"
    ] is True
    assert result.diagnostics[
        "bybit_post_count"
    ] == 1
    assert result.diagnostics[
        "no_automatic_resend"
    ] is True

    assert len(
        env.client.post_calls
    ) == post_count_before + 1

    assert flow.status == (
        BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILING
    )
    assert env.batch.status == (
        BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_RECONCILING
    )
    assert flow.withdrawal_status == "UNKNOWN"

    assert intent["state"] == "reconciling"
    assert acknowledgement[
        "outcome"
    ] == "unknown"
    assert acknowledgement[
        "bybit_post_performed"
    ] is True
    assert acknowledgement[
        "no_automatic_resend"
    ] is True

    install_withdrawal_reconciliation_reads(
        monkeypatch,
        env,
        exact_record=None,
        bounded_records=[],
    )

    get_count_before_rerun = len(
        env.client.get_calls
    )
    post_count_before_rerun = len(
        env.client.post_calls
    )

    rerun = resume_once(env)

    assert rerun.ok is False
    assert rerun.diagnostics[
        "transition"
    ] == (
        "reconcile_withdrawal_"
        "record_not_found"
    )

    assert rerun.diagnostics[
        "bybit_get_count"
    ] == 2
    assert rerun.diagnostics[
        "bybit_post_count"
    ] == 0
    assert rerun.diagnostics[
        "no_automatic_resend"
    ] is True

    assert len(
        env.client.get_calls
    ) == get_count_before_rerun + 2

    assert len(
        env.client.post_calls
    ) == post_count_before_rerun


def test_withdrawal_ack_mismatch_records_post_evidence_and_stops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    prepared = (
        advance_to_withdrawal_intent_prepared(
            env,
            monkeypatch,
        )
    )

    flow = prepared.flow

    install_withdrawal_submit_fakes(
        monkeypatch,
        env,
        acknowledgement_amount=Decimal("99"),
    )

    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    intent = flow.withdrawal_intent_json
    acknowledgement = intent[
        "acknowledgement"
    ]

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == "submit_withdrawal_ack_mismatch"

    assert result.diagnostics[
        "did_bybit_post"
    ] is True
    assert result.diagnostics[
        "bybit_post_count"
    ] == 1
    assert result.diagnostics[
        "no_automatic_resend"
    ] is True
    assert result.diagnostics[
        "acknowledgement_mismatch"
    ] is True
    assert result.diagnostics[
        "reserve_release_allowed"
    ] is False
    assert result.diagnostics[
        "pricing_unlock_allowed"
    ] is False

    assert len(
        env.client.post_calls
    ) == post_count_before + 1

    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert intent["state"] == (
        "failed_requires_review"
    )

    assert acknowledgement[
        "outcome"
    ] == "mismatch"
    assert acknowledgement[
        "bybit_post_performed"
    ] is True
    assert acknowledgement[
        "no_automatic_resend"
    ] is True

    assert acknowledgement[
        "expected"
    ]["amount_usdt"] == "100"
    assert acknowledgement[
        "observed"
    ]["amount_usdt"] == "99"

    assert flow.withdrawal_id == (
        "withdrawal-123"
    )
    assert flow.withdrawal_status == "PENDING"
    assert flow.withdrawal_created_at == NOW

    barrier = flow.reconciliation_json[
        "master_transferable_balance_barrier"
    ]

    assert barrier["state"] == "confirmed"
    assert barrier[
        "withdrawal_allowed"
    ] is True

    get_count_before_rerun = len(
        env.client.get_calls
    )
    post_count_before_rerun = len(
        env.client.post_calls
    )

    rerun = resume_once(env)

    assert rerun.ok is False
    assert rerun.idempotent is True
    assert rerun.diagnostics[
        "transition"
    ] == (
        "failed_requires_review_"
        "already_recorded"
    )

    assert len(
        env.client.get_calls
    ) == get_count_before_rerun
    assert len(
        env.client.post_calls
    ) == post_count_before_rerun


def test_withdrawal_reconciliation_query_error_stays_pending_without_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    advanced = (
        advance_to_withdrawal_reconciling(
            env,
            monkeypatch,
        )
    )

    flow = advanced.flow

    install_withdrawal_reconciliation_reads(
        monkeypatch,
        env,
        exact_error=BybitApiError(
            "simulated withdrawal query failure"
        ),
    )

    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    reconciliation = (
        flow.withdrawal_intent_json[
            "reconciliation"
        ]
    )

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_withdrawal_query_pending"
    )

    assert result.diagnostics[
        "bybit_get_count"
    ] == 1
    assert result.diagnostics[
        "bybit_post_count"
    ] == 0
    assert result.diagnostics[
        "no_automatic_resend"
    ] is True

    assert flow.status == (
        BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILING
    )
    assert flow.withdrawal_intent_json[
        "state"
    ] == "reconciling"

    assert reconciliation["state"] == (
        "query_pending"
    )
    assert (
        "simulated withdrawal query failure"
        in reconciliation["query_error"]
    )

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_withdrawal_reconciliation_record_not_found_remains_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    advanced = (
        advance_to_withdrawal_reconciling(
            env,
            monkeypatch,
        )
    )

    flow = advanced.flow

    reads = (
        install_withdrawal_reconciliation_reads(
            monkeypatch,
            env,
            exact_record=None,
            bounded_records=[],
        )
    )

    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    reconciliation = (
        flow.withdrawal_reconciliation_json
    )

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_withdrawal_"
        "record_not_found"
    )

    assert result.diagnostics[
        "bybit_get_count"
    ] == 2
    assert result.diagnostics[
        "bybit_post_count"
    ] == 0

    assert reconciliation["state"] == (
        "record_not_found"
    )
    assert reconciliation[
        "exact_query_found"
    ] is False
    assert reconciliation["lookup"][
        "matching_request_id_count"
    ] == 0

    assert len(reads.exact_calls) == 1
    assert len(reads.bounded_calls) == 1

    assert flow.status == (
        BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILING
    )

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_withdrawal_reconciliation_pending_record_persists_redacted_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    advanced = (
        advance_to_withdrawal_reconciling(
            env,
            monkeypatch,
        )
    )

    flow = advanced.flow

    record = make_withdrawal_record(
        request_id=flow.withdrawal_request_id,
        status="PROCESSING",
        tx_hash=None,
    )

    install_withdrawal_reconciliation_reads(
        monkeypatch,
        env,
        exact_record=record,
        bounded_records=[record],
    )

    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    reconciliation = (
        flow.withdrawal_reconciliation_json
    )
    record_evidence = (
        flow.withdrawal_record_json
    )

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == "reconcile_withdrawal_pending"

    assert result.diagnostics[
        "bybit_get_count"
    ] == 2
    assert result.diagnostics[
        "bybit_post_count"
    ] == 0

    assert flow.status == (
        BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILING
    )
    assert flow.withdrawal_status == (
        "PROCESSING"
    )
    assert flow.withdrawal_id == (
        "withdrawal-123"
    )
    assert flow.withdrawal_tx_hash is None

    assert reconciliation["state"] == (
        "pending"
    )
    assert reconciliation[
        "pending_reason"
    ] == "bybit_status_pending"

    assert record_evidence[
        "raw_omitted"
    ] is True
    assert "raw_sha256" in record_evidence
    assert len(
        record_evidence["raw_sha256"]
    ) == 64
    assert "raw" not in record_evidence

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_withdrawal_reconciliation_exact_success_confirms_without_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    advanced = (
        advance_to_withdrawal_reconciling(
            env,
            monkeypatch,
        )
    )

    flow = advanced.flow

    record = make_withdrawal_record(
        request_id=flow.withdrawal_request_id,
        status="SUCCESS",
        tx_hash="0xabc123",
    )

    install_withdrawal_reconciliation_reads(
        monkeypatch,
        env,
        exact_record=record,
        bounded_records=[record],
    )

    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    intent = flow.withdrawal_intent_json
    reconciliation = (
        flow.withdrawal_reconciliation_json
    )

    assert result.ok is True
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_withdrawal_confirmed"
    )

    assert result.diagnostics[
        "bybit_get_count"
    ] == 2
    assert result.diagnostics[
        "bybit_post_count"
    ] == 0
    assert result.diagnostics[
        "no_automatic_resend"
    ] is True

    assert flow.status == (
        BYBIT_FLOW_STATUS_WITHDRAWAL_RECONCILED
    )
    assert env.batch.status == (
        BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_RECONCILING
    )

    assert intent["state"] == "confirmed"
    assert reconciliation["state"] == (
        "confirmed"
    )
    assert reconciliation[
        "selected_source"
    ] == "exact_request_id_query"

    assert flow.withdrawal_id == (
        "withdrawal-123"
    )
    assert flow.withdrawal_status == "SUCCESS"
    assert flow.withdrawal_tx_hash == (
        "0xabc123"
    )
    assert flow.withdrawal_confirmed_at == NOW

    assert flow.withdrawal_record_json[
        "raw_omitted"
    ] is True
    assert "raw" not in (
        flow.withdrawal_record_json
    )

    assert len(
        env.client.post_calls
    ) == post_count_before

    install_bsc_receipt_web3(
        monkeypatch,
        env,
        receipt_present=False,
    )

    get_count_before_rerun = len(
        env.client.get_calls
    )
    post_count_before_rerun = len(
        env.client.post_calls
    )

    rerun = resume_once(env)

    assert rerun.ok is False
    assert rerun.diagnostics[
        "transition"
    ] == (
        "reconcile_settlement_wallet_"
        "receipt_pending"
    )

    assert rerun.diagnostics[
        "bybit_get_count"
    ] == 0
    assert rerun.diagnostics[
        "bybit_post_count"
    ] == 0
    assert rerun.diagnostics[
        "bsc_rpc_read_count"
    ] == 1

    assert flow.status == (
        BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_PENDING
    )

    assert len(
        env.client.get_calls
    ) == get_count_before_rerun

    assert len(
        env.client.post_calls
    ) == post_count_before_rerun


def test_withdrawal_reconciliation_mismatch_requires_review_without_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    advanced = (
        advance_to_withdrawal_reconciling(
            env,
            monkeypatch,
        )
    )

    flow = advanced.flow

    record = make_withdrawal_record(
        request_id=flow.withdrawal_request_id,
        status="SUCCESS",
        amount_usdt=Decimal("99"),
        tx_hash="0xmismatch",
    )

    install_withdrawal_reconciliation_reads(
        monkeypatch,
        env,
        exact_record=record,
        bounded_records=[record],
    )

    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == "reconcile_withdrawal_mismatch"

    assert (
        "amount mismatch"
        in str(result.error).lower()
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert flow.withdrawal_intent_json[
        "state"
    ] == "failed_requires_review"

    barrier = flow.reconciliation_json[
        "master_transferable_balance_barrier"
    ]

    assert barrier["state"] == "confirmed"
    assert barrier[
        "withdrawal_allowed"
    ] is True

    assert result.diagnostics[
        "bybit_post_count"
    ] == 0
    assert result.diagnostics[
        "reserve_release_allowed"
    ] is False
    assert result.diagnostics[
        "pricing_unlock_allowed"
    ] is False

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_withdrawal_reconciliation_duplicate_request_id_requires_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    advanced = (
        advance_to_withdrawal_reconciling(
            env,
            monkeypatch,
        )
    )

    flow = advanced.flow

    first = make_withdrawal_record(
        request_id=flow.withdrawal_request_id,
        withdrawal_id="withdrawal-123",
        status="PROCESSING",
    )

    second = make_withdrawal_record(
        request_id=flow.withdrawal_request_id,
        withdrawal_id="withdrawal-456",
        status="PROCESSING",
    )

    install_withdrawal_reconciliation_reads(
        monkeypatch,
        env,
        exact_record=first,
        bounded_records=[
            first,
            second,
        ],
    )

    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_withdrawal_"
        "duplicate_request_id"
    )

    assert (
        "multiple bounded bybit withdrawal records"
        in str(result.error).lower()
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert flow.withdrawal_intent_json[
        "state"
    ] == "failed_requires_review"

    assert result.diagnostics[
        "bybit_post_count"
    ] == 0

    assert len(
        env.client.post_calls
    ) == post_count_before


@pytest.mark.parametrize(
    (
        "status",
        "expected_transition",
        "expected_error",
    ),
    [
        (
            "FAILED",
            (
                "reconcile_withdrawal_"
                "failed_status"
            ),
            "failed status",
        ),
        (
            "MYSTERY",
            (
                "reconcile_withdrawal_"
                "unsupported_status"
            ),
            "unsupported status",
        ),
    ],
)
def test_withdrawal_reconciliation_terminal_status_requires_review(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    expected_transition: str,
    expected_error: str,
) -> None:
    env = install_service_fakes(monkeypatch)

    advanced = (
        advance_to_withdrawal_reconciling(
            env,
            monkeypatch,
        )
    )

    flow = advanced.flow

    record = make_withdrawal_record(
        request_id=flow.withdrawal_request_id,
        status=status,
        tx_hash=None,
    )

    install_withdrawal_reconciliation_reads(
        monkeypatch,
        env,
        exact_record=record,
        bounded_records=[record],
    )

    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == expected_transition

    assert expected_error in str(
        result.error
    ).lower()

    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert flow.withdrawal_intent_json[
        "state"
    ] == "failed_requires_review"

    assert result.diagnostics[
        "bybit_post_count"
    ] == 0
    assert result.diagnostics[
        "no_automatic_resend"
    ] is True

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_settlement_wallet_receipt_rpc_unavailable_stays_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    advanced = (
        advance_to_withdrawal_reconciled(
            env,
            monkeypatch,
        )
    )

    flow = advanced.flow

    install_bsc_receipt_web3(
        monkeypatch,
        env,
        get_web3_error=RuntimeError(
            "simulated BSC RPC unavailable"
        ),
    )

    get_count_before = len(
        env.client.get_calls
    )
    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    evidence = (
        flow.settlement_wallet_receipt_json
    )

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_settlement_wallet_"
        "receipt_pending"
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_PENDING
    )
    assert flow.settlement_wallet_receipt_status == (
        "PENDING"
    )

    assert evidence["state"] == "pending"
    assert (
        "simulated BSC RPC unavailable"
        in evidence["pending_error"]
    )
    assert evidence[
        "raw_receipt_omitted"
    ] is True

    assert len(
        env.client.get_calls
    ) == get_count_before

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_settlement_wallet_receipt_missing_stays_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    advanced = (
        advance_to_withdrawal_reconciled(
            env,
            monkeypatch,
        )
    )

    flow = advanced.flow

    calls = install_bsc_receipt_web3(
        monkeypatch,
        env,
        receipt_present=False,
    )

    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    evidence = (
        flow.settlement_wallet_receipt_json
    )

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_settlement_wallet_"
        "receipt_pending"
    )

    assert evidence["state"] == "pending"
    assert evidence["receipt_status"] is None
    assert evidence["confirmations"] == 0

    assert flow.status == (
        BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_PENDING
    )
    assert flow.settlement_wallet_receipt_confirmations == 0

    assert calls.receipt_calls == [
        "0xabc123",
    ]
    assert calls.balance_calls == []

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_settlement_wallet_receipt_insufficient_confirmations_stays_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    advanced = (
        advance_to_withdrawal_reconciled(
            env,
            monkeypatch,
        )
    )

    flow = advanced.flow

    receipt_block = 55500010

    calls = install_bsc_receipt_web3(
        monkeypatch,
        env,
        receipt_block_number=receipt_block,
        current_block_number=(
            receipt_block + 4
        ),
    )

    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    evidence = (
        flow.settlement_wallet_receipt_json
    )

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_settlement_wallet_"
        "receipt_pending"
    )

    assert evidence["state"] == "pending"
    assert evidence["receipt_status"] == 1
    assert evidence["confirmations"] == 5

    assert evidence[
        "required_confirmations"
    ] == (
        service.settings
        .NEGATIVE_NET_BSC_INTENT_CONFIRMATIONS_REQUIRED
    )

    assert flow.settlement_wallet_receipt_confirmations == 5
    assert flow.status == (
        BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_PENDING
    )

    # Logs and balance are not accepted before
    # the required confirmations.
    assert calls.balance_calls == []

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_settlement_wallet_receipt_timeout_requires_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    advanced = (
        advance_to_withdrawal_reconciled(
            env,
            monkeypatch,
        )
    )

    flow = advanced.flow

    install_bsc_receipt_web3(
        monkeypatch,
        env,
        receipt_present=False,
    )

    max_pending_sec = int(
        service.settings
        .NEGATIVE_NET_BSC_INTENT_MAX_PENDING_SEC
    )

    later = (
        flow.withdrawal_confirmed_at
        + timedelta(
            seconds=max_pending_sec + 1
        )
    )

    post_count_before = len(
        env.client.post_calls
    )

    result = (
        service.resume_negative_bybit_flow_once(
            env.db,
            settlement_batch_id=101,
            bybit_client=env.client,
            fund_sub_uid="70001",
            master_uid="90001",
            now=later,
        )
    )

    evidence = (
        flow.settlement_wallet_receipt_json
    )

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_settlement_wallet_"
        "receipt_failed"
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert flow.settlement_wallet_receipt_status == (
        "FAILED_REQUIRES_REVIEW"
    )

    assert evidence["state"] == (
        "failed_requires_review"
    )
    assert evidence["pending_age_sec"] == (
        max_pending_sec + 1
    )
    assert (
        "exceeded maximum pending time"
        in evidence["error"].lower()
    )

    assert result.diagnostics[
        "reserve_release_allowed"
    ] is False
    assert result.diagnostics[
        "pricing_unlock_allowed"
    ] is False

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_settlement_wallet_receipt_status_zero_requires_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    advanced = (
        advance_to_withdrawal_reconciled(
            env,
            monkeypatch,
        )
    )

    flow = advanced.flow

    calls = install_bsc_receipt_web3(
        monkeypatch,
        env,
        receipt_status=0,
    )

    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    evidence = (
        flow.settlement_wallet_receipt_json
    )

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_settlement_wallet_"
        "receipt_failed"
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert evidence["receipt_status"] == 0

    assert (
        "receipt status 0"
        in evidence["error"].lower()
    )

    assert calls.balance_calls == []

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_settlement_wallet_receipt_tx_hash_mismatch_requires_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    advanced = (
        advance_to_withdrawal_reconciled(
            env,
            monkeypatch,
        )
    )

    flow = advanced.flow

    install_bsc_receipt_web3(
        monkeypatch,
        env,
        receipt_tx_hash="0xdeadbeef",
    )

    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    evidence = (
        flow.settlement_wallet_receipt_json
    )

    assert result.ok is False
    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert (
        "transaction hash mismatch"
        in evidence["error"].lower()
    )

    assert evidence[
        "observed_tx_hash"
    ] == "0xdeadbeef"

    assert len(
        env.client.post_calls
    ) == post_count_before


@pytest.mark.parametrize(
    (
        "transfer_amounts",
        "expected_error",
    ),
    [
        (
            (),
            "exactly one usdt transfer",
        ),
        (
            (
                Decimal("100"),
                Decimal("100"),
            ),
            "exactly one usdt transfer",
        ),
        (
            (
                Decimal("99"),
            ),
            "transfer amount does not match",
        ),
    ],
)
def test_settlement_wallet_receipt_transfer_log_mismatch_requires_review(
    monkeypatch: pytest.MonkeyPatch,
    transfer_amounts: tuple[
        Decimal,
        ...,
    ],
    expected_error: str,
) -> None:
    env = install_service_fakes(monkeypatch)

    advanced = (
        advance_to_withdrawal_reconciled(
            env,
            monkeypatch,
        )
    )

    flow = advanced.flow

    install_bsc_receipt_web3(
        monkeypatch,
        env,
        transfer_amounts_usdt=(
            transfer_amounts
        ),
    )

    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    evidence = (
        flow.settlement_wallet_receipt_json
    )

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_settlement_wallet_"
        "receipt_failed"
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert expected_error in (
        evidence["error"].lower()
    )

    assert evidence[
        "matched_transfer_log_count"
    ] == len(transfer_amounts)

    assert evidence[
        "exact_transfer_log_match"
    ] is False

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_settlement_wallet_receipt_balance_delta_mismatch_requires_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    advanced = (
        advance_to_withdrawal_reconciled(
            env,
            monkeypatch,
        )
    )

    flow = advanced.flow

    install_bsc_receipt_web3(
        monkeypatch,
        env,
        transfer_amounts_usdt=(
            Decimal("100"),
        ),
        # Baseline is 7.25, so this produces
        # delta=99 instead of the required 100.
        balance_after_usdt=Decimal(
            "106.25"
        ),
    )

    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    evidence = (
        flow.settlement_wallet_receipt_json
    )

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_settlement_wallet_"
        "receipt_failed"
    )

    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert evidence[
        "exact_transfer_log_match"
    ] is True
    assert evidence[
        "exact_balance_delta_match"
    ] is False

    assert evidence[
        "balance_delta_usdt"
    ] == "99"

    assert (
        "balance delta"
        in evidence["error"].lower()
    )

    assert len(
        env.client.post_calls
    ) == post_count_before


def test_settlement_wallet_receipt_then_db_only_completion_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = install_service_fakes(monkeypatch)

    advanced = (
        advance_to_withdrawal_reconciled(
            env,
            monkeypatch,
        )
    )

    flow = advanced.flow

    calls = install_bsc_receipt_web3(
        monkeypatch,
        env,
        receipt_status=1,
        receipt_tx_hash="0xabc123",
        receipt_block_number=55500010,
        current_block_number=55500021,
        transfer_amounts_usdt=(
            Decimal("100"),
        ),
        balance_after_usdt=Decimal(
            "107.25"
        ),
    )

    get_count_before = len(
        env.client.get_calls
    )
    post_count_before = len(
        env.client.post_calls
    )

    result = resume_once(env)

    evidence = (
        flow.settlement_wallet_receipt_json
    )

    assert result.ok is True
    assert result.diagnostics[
        "transition"
    ] == (
        "reconcile_settlement_wallet_"
        "receipt_confirmed"
    )

    assert result.diagnostics[
        "bybit_get_count"
    ] == 0
    assert result.diagnostics[
        "bybit_post_count"
    ] == 0
    assert result.diagnostics[
        "bsc_rpc_read_count"
    ] == 1

    assert flow.status == (
        BYBIT_FLOW_STATUS_SETTLEMENT_WALLET_RECEIPT_CONFIRMED
    )
    assert env.batch.status == (
        BATCH_STATUS_NEGATIVE_NET_WITHDRAWAL_RECONCILING
    )

    assert flow.settlement_wallet_receipt_status == (
        "CONFIRMED"
    )
    assert flow.settlement_wallet_received_usdt == (
        Decimal("100")
    )
    assert flow.settlement_wallet_balance_before_usdt == (
        Decimal("7.25")
    )
    assert flow.settlement_wallet_balance_after_usdt == (
        Decimal("107.25")
    )

    assert flow.settlement_wallet_receipt_tx_hash == (
        "0xabc123"
    )
    assert flow.settlement_wallet_receipt_confirmations == 12
    assert flow.settlement_wallet_receipt_block_number == (
        55500010
    )
    assert flow.settlement_wallet_receipt_confirmed_at == NOW

    assert evidence["state"] == "confirmed"
    assert evidence[
        "exact_transfer_log_match"
    ] is True
    assert evidence[
        "exact_balance_delta_match"
    ] is True
    assert evidence[
        "balance_delta_usdt"
    ] == "100"
    assert evidence[
        "raw_receipt_omitted"
    ] is True
    assert "raw_receipt" not in evidence

    assert calls.receipt_calls == [
        "0xabc123",
    ]
    assert len(calls.balance_calls) == 1
    assert calls.balance_calls[0][
        "block_identifier"
    ] == 55500021

    assert len(
        env.client.get_calls
    ) == get_count_before

    assert len(
        env.client.post_calls
    ) == post_count_before

    receipt_call_count_before = len(
        calls.receipt_calls
    )
    balance_call_count_before = len(
        calls.balance_calls
    )
    get_count_before_completion = len(
        env.client.get_calls
    )
    post_count_before_completion = len(
        env.client.post_calls
    )

    completion = resume_once(env)

    assert completion.ok is True
    assert completion.idempotent is False
    assert completion.diagnostics[
        "transition"
    ] == (
        "complete_negative_cash_delivery"
    )

    assert completion.diagnostics[
        "db_only_transition"
    ] is True
    assert completion.diagnostics[
        "cash_ready_for_payout"
    ] is True

    assert completion.diagnostics[
        "bybit_get_count"
    ] == 0
    assert completion.diagnostics[
        "bybit_post_count"
    ] == 0
    assert completion.diagnostics[
        "bsc_rpc_read_count"
    ] == 0

    assert completion.diagnostics[
        "seller_payouts_started"
    ] is False
    assert completion.diagnostics[
        "accounting_finalized"
    ] is False
    assert completion.diagnostics[
        "reserve_release_allowed"
    ] is False
    assert completion.diagnostics[
        "pricing_unlock_allowed"
    ] is False

    assert flow.status == (
        BYBIT_FLOW_STATUS_COMPLETED
    )
    assert env.batch.status == (
        BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT
    )

    assert flow.error is None
    assert env.batch.error is None

    completion_evidence = (
        flow.reconciliation_json[
            "cash_delivery_completion"
        ]
    )

    report = flow.report_json

    assert completion_evidence[
        "schema"
    ] == (
        service
        .CASH_DELIVERY_COMPLETION_SCHEMA
    )
    assert completion_evidence[
        "state"
    ] == "completed"
    assert completion_evidence[
        "db_only_transition"
    ] is True

    assert completion_evidence[
        "seller_payouts_started"
    ] is False
    assert completion_evidence[
        "accounting_finalized"
    ] is False
    assert completion_evidence[
        "reserve_release_allowed"
    ] is False
    assert completion_evidence[
        "pricing_unlock_allowed"
    ] is False

    assert completion_evidence[
        "next_stage"
    ] == "negative_payout_pipeline"

    assert (
        "settlement_wallet_address"
        not in completion_evidence
    )
    assert len(
        completion_evidence[
            "settlement_wallet_address_sha256"
        ]
    ) == 64

    assert completion_evidence[
        "withdrawal_tx_hash"
    ] == "0xabc123"
    assert completion_evidence[
        "withdrawal_amount_usdt"
    ] == "100"
    assert completion_evidence[
        "required_master_usdt"
    ] == "101"
    assert completion_evidence[
        "withdrawal_fee_usdt"
    ] == "1"
    assert completion_evidence[
        "balance_before_usdt"
    ] == "7.25"
    assert completion_evidence[
        "balance_after_usdt"
    ] == "107.25"
    assert completion_evidence[
        "confirmations"
    ] == 12
    assert completion_evidence[
        "receipt_block_number"
    ] == 55500010

    fingerprints = completion_evidence[
        "evidence_fingerprints"
    ]

    assert set(fingerprints) == {
        "universal_reconciliation",
        "master_balance_barrier",
        "withdrawal_reconciliation",
        "settlement_wallet_receipt",
    }

    assert all(
        len(value) == 64
        for value in fingerprints.values()
    )

    assert report["schema"] == (
        service
        .CASH_DELIVERY_REPORT_SCHEMA
    )
    assert report["state"] == "completed"
    assert report[
        "cash_ready_for_payout"
    ] is True
    assert report[
        "seller_payouts_started"
    ] is False
    assert report[
        "accounting_finalized"
    ] is False
    assert report[
        "reserve_release_allowed"
    ] is False
    assert report[
        "pricing_unlock_allowed"
    ] is False
    assert report[
        "next_stage"
    ] == "negative_payout_pipeline"

    assert len(
        calls.receipt_calls
    ) == receipt_call_count_before
    assert len(
        calls.balance_calls
    ) == balance_call_count_before
    assert len(
        env.client.get_calls
    ) == get_count_before_completion
    assert len(
        env.client.post_calls
    ) == post_count_before_completion

    reconciliation_before_rerun = (
        deepcopy(
            flow.reconciliation_json
        )
    )
    report_before_rerun = deepcopy(
        flow.report_json
    )

    get_count_before_rerun = len(
        env.client.get_calls
    )
    post_count_before_rerun = len(
        env.client.post_calls
    )
    receipt_count_before_rerun = len(
        calls.receipt_calls
    )
    balance_count_before_rerun = len(
        calls.balance_calls
    )

    rerun = resume_once(env)

    assert rerun.ok is True
    assert rerun.idempotent is True
    assert rerun.diagnostics[
        "transition"
    ] == (
        "negative_cash_delivery_"
        "already_completed"
    )

    assert rerun.diagnostics[
        "db_only_transition"
    ] is True
    assert rerun.diagnostics[
        "bybit_get_count"
    ] == 0
    assert rerun.diagnostics[
        "bybit_post_count"
    ] == 0
    assert rerun.diagnostics[
        "bsc_rpc_read_count"
    ] == 0

    assert flow.status == (
        BYBIT_FLOW_STATUS_COMPLETED
    )
    assert env.batch.status == (
        BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT
    )

    assert flow.reconciliation_json == (
        reconciliation_before_rerun
    )
    assert flow.report_json == (
        report_before_rerun
    )

    assert len(
        env.client.get_calls
    ) == get_count_before_rerun
    assert len(
        env.client.post_calls
    ) == post_count_before_rerun
    assert len(
        calls.receipt_calls
    ) == receipt_count_before_rerun
    assert len(
        calls.balance_calls
    ) == balance_count_before_rerun


@pytest.mark.parametrize(
    (
        "tamper_case",
        "expected_error",
    ),
    [
        (
            "universal_reconciliation",
            (
                "universal transfer reconciliation "
                "is not confirmed"
            ),
        ),
        (
            "withdrawal_reconciliation",
            (
                "withdrawal was not confirmed by "
                "exact requestid query"
            ),
        ),
        (
            "receipt_balance_delta",
            (
                "settlement wallet receipt balance "
                "delta mismatch"
            ),
        ),
        (
            "receipt_confirmations",
            (
                "settlement wallet receipt "
                "confirmations mismatch"
            ),
        ),
    ],
)
def test_cash_delivery_completion_detects_tampered_external_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tamper_case: str,
    expected_error: str,
) -> None:
    env = install_service_fakes(
        monkeypatch
    )

    advanced = (
        advance_to_settlement_wallet_receipt_confirmed(
            env,
            monkeypatch,
        )
    )

    flow = advanced.flow

    if tamper_case == (
        "universal_reconciliation"
    ):
        flow.universal_transfer_intent_json[
            "reconciliation"
        ]["exact_match"] = False

    elif tamper_case == (
        "withdrawal_reconciliation"
    ):
        flow.withdrawal_intent_json[
            "reconciliation"
        ]["selected_source"] = (
            "bounded_record_lookup"
        )

    elif tamper_case == (
        "receipt_balance_delta"
    ):
        flow.settlement_wallet_receipt_json[
            "balance_delta_usdt"
        ] = "99"

    elif tamper_case == (
        "receipt_confirmations"
    ):
        flow.settlement_wallet_receipt_confirmations = (
            13
        )

    else:
        raise AssertionError(
            f"Unsupported tamper case: {tamper_case}"
        )

    get_count_before = len(
        env.client.get_calls
    )
    post_count_before = len(
        env.client.post_calls
    )
    receipt_count_before = len(
        advanced.calls.receipt_calls
    )
    balance_count_before = len(
        advanced.calls.balance_calls
    )

    result = resume_once(env)

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == "failed_requires_review"

    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert expected_error in str(
        result.error
    ).lower()

    assert result.diagnostics[
        "did_bybit_post"
    ] is False
    assert result.diagnostics[
        "bybit_post_count"
    ] == 0
    assert result.diagnostics[
        "reserve_release_allowed"
    ] is False
    assert result.diagnostics[
        "pricing_unlock_allowed"
    ] is False

    assert len(
        env.client.get_calls
    ) == get_count_before
    assert len(
        env.client.post_calls
    ) == post_count_before
    assert len(
        advanced.calls.receipt_calls
    ) == receipt_count_before
    assert len(
        advanced.calls.balance_calls
    ) == balance_count_before


@pytest.mark.parametrize(
    (
        "tamper_case",
        "expected_error",
    ),
    [
        (
            "completion_fingerprint",
            (
                "cash-delivery completion evidence "
                "fingerprints mismatch"
            ),
        ),
        (
            "report_finalization_boundary",
            (
                "cash-delivery report violates "
                "strict finalization boundary"
            ),
        ),
    ],
)
def test_completed_cash_delivery_detects_tampered_completion_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tamper_case: str,
    expected_error: str,
) -> None:
    env = install_service_fakes(
        monkeypatch
    )

    advanced = (
        advance_to_settlement_wallet_receipt_confirmed(
            env,
            monkeypatch,
        )
    )

    flow = advanced.flow

    completion = resume_once(env)

    assert completion.ok is True
    assert completion.diagnostics[
        "transition"
    ] == (
        "complete_negative_cash_delivery"
    )
    assert flow.status == (
        BYBIT_FLOW_STATUS_COMPLETED
    )
    assert env.batch.status == (
        BATCH_STATUS_NEGATIVE_NET_CASH_READY_FOR_PAYOUT
    )

    if tamper_case == (
        "completion_fingerprint"
    ):
        flow.reconciliation_json[
            "cash_delivery_completion"
        ][
            "evidence_fingerprints"
        ][
            "settlement_wallet_receipt"
        ] = "0" * 64

    elif tamper_case == (
        "report_finalization_boundary"
    ):
        flow.report_json[
            "seller_payouts_started"
        ] = True

    else:
        raise AssertionError(
            f"Unsupported tamper case: {tamper_case}"
        )

    get_count_before = len(
        env.client.get_calls
    )
    post_count_before = len(
        env.client.post_calls
    )
    receipt_count_before = len(
        advanced.calls.receipt_calls
    )
    balance_count_before = len(
        advanced.calls.balance_calls
    )

    result = resume_once(env)

    assert result.ok is False
    assert result.diagnostics[
        "transition"
    ] == "failed_requires_review"

    assert flow.status == (
        BYBIT_FLOW_STATUS_FAILED_REQUIRES_REVIEW
    )
    assert env.batch.status == (
        BATCH_STATUS_FAILED_REQUIRES_REVIEW
    )

    assert expected_error in str(
        result.error
    ).lower()

    assert result.diagnostics[
        "did_bybit_post"
    ] is False
    assert result.diagnostics[
        "bybit_post_count"
    ] == 0
    assert result.diagnostics[
        "reserve_release_allowed"
    ] is False
    assert result.diagnostics[
        "pricing_unlock_allowed"
    ] is False

    assert len(
        env.client.get_calls
    ) == get_count_before
    assert len(
        env.client.post_calls
    ) == post_count_before
    assert len(
        advanced.calls.receipt_calls
    ) == receipt_count_before
    assert len(
        advanced.calls.balance_calls
    ) == balance_count_before
