from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.bybit.asset_flows import BybitAssetFlowError, freeze_sub_uid
from app.settlement import statuses
from workers import bybit_subaccount_freeze_guard as guard


ROOT = Path(__file__).resolve().parents[1]


class FakeBybitClient:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict]] = []

    def post(self, path: str, payload: dict) -> dict:
        self.posts.append((path, payload))
        return {"retCode": 0, "result": {"path": path, "payload": payload}}


class FakeDb:
    pass


@dataclass
class FakeWindow:
    id: int


@dataclass
class FakeEvent:
    id: int


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def sample_account() -> guard.ProtectedSubaccount:
    return guard.ProtectedSubaccount(
        account_id=11,
        fund_id=3,
        fund_code="wb_test",
        bybit_sub_uid="123456",
        bybit_subaccount_name="WB Test",
    )


def test_freeze_sub_uid_helper() -> None:
    client = FakeBybitClient()

    freeze_sub_uid(client, subuid=123456, frozen=1)
    assert_ok(
        "HELPER_FREEZE_CALLS_CORRECT_ENDPOINT",
        client.posts[-1][0] == "/v5/user/frozen-sub-member",
    )
    assert_ok(
        "HELPER_FREEZE_PAYLOAD",
        client.posts[-1][1] == {"subuid": 123456, "frozen": 1},
    )

    freeze_sub_uid(client, subuid=123456, frozen=0)
    assert_ok(
        "HELPER_UNFREEZE_PAYLOAD",
        client.posts[-1][1] == {"subuid": 123456, "frozen": 0},
    )

    try:
        freeze_sub_uid(client, subuid=0, frozen=1)
        raise AssertionError("HELPER_REJECTS_INVALID_SUBUID")
    except BybitAssetFlowError:
        print("HELPER_REJECTS_INVALID_SUBUID: OK")

    try:
        freeze_sub_uid(client, subuid=123456, frozen=2)
        raise AssertionError("HELPER_REJECTS_INVALID_FROZEN")
    except BybitAssetFlowError:
        print("HELPER_REJECTS_INVALID_FROZEN: OK")


def run_process_account_case(
    *,
    active_window: FakeWindow | None,
    dry_run: bool,
    api_error: Exception | None = None,
) -> dict[str, Any]:
    old_active_unfreeze_window = guard.active_unfreeze_window
    old_freeze_sub_uid = guard.freeze_sub_uid
    old_record_guard_event = guard.record_guard_event
    old_create_emergency_lock_if_absent = guard.create_emergency_lock_if_absent
    old_send_freeze_failure_alert = guard.send_freeze_failure_alert

    calls: list[tuple[int, int]] = []
    events: list[dict[str, Any]] = []
    locks: list[dict[str, Any]] = []
    alerts: list[dict[str, Any]] = []

    def fake_active_unfreeze_window(db, *, account, now):
        return active_window

    def fake_freeze_sub_uid(client, *, subuid: int, frozen: int):
        calls.append((subuid, frozen))
        if api_error is not None:
            raise api_error
        return {"retCode": 0, "result": {"subuid": subuid, "frozen": frozen}}

    def fake_record_guard_event(db, **kwargs):
        events.append(kwargs)
        return FakeEvent(id=900 + len(events))

    def fake_create_emergency_lock_if_absent(db, **kwargs):
        locks.append(kwargs)
        return True

    def fake_send_freeze_failure_alert(**kwargs):
        alerts.append(kwargs)

    try:
        guard.active_unfreeze_window = fake_active_unfreeze_window
        guard.freeze_sub_uid = fake_freeze_sub_uid
        guard.record_guard_event = fake_record_guard_event
        guard.create_emergency_lock_if_absent = fake_create_emergency_lock_if_absent
        guard.send_freeze_failure_alert = fake_send_freeze_failure_alert

        action, lock_created = guard.process_account(
            FakeDb(),
            client=object(),
            account=sample_account(),
            now=guard.utcnow(),
            dry_run=dry_run,
        )

        return {
            "action": action,
            "lock_created": lock_created,
            "calls": calls,
            "events": events,
            "locks": locks,
            "alerts": alerts,
        }

    finally:
        guard.active_unfreeze_window = old_active_unfreeze_window
        guard.freeze_sub_uid = old_freeze_sub_uid
        guard.record_guard_event = old_record_guard_event
        guard.create_emergency_lock_if_absent = old_create_emergency_lock_if_absent
        guard.send_freeze_failure_alert = old_send_freeze_failure_alert


def test_process_account_freeze_without_window() -> None:
    result = run_process_account_case(active_window=None, dry_run=False)

    assert_ok(
        "PROCESS_NO_WINDOW_CALLS_FREEZE_1",
        result["calls"] == [(123456, 1)],
    )
    assert_ok(
        "PROCESS_NO_WINDOW_ACTION_FREEZE_SUCCESS",
        result["action"] == statuses.BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_FREEZE_SUCCESS,
    )
    assert_ok(
        "PROCESS_NO_WINDOW_EVENT_DESIRED_FROZEN_1",
        result["events"][-1]["desired_frozen"] == 1,
    )
    assert_ok(
        "PROCESS_NO_WINDOW_EVENT_DECISION_FREEZE_REQUIRED",
        result["events"][-1]["decision"] == statuses.BYBIT_SUBACCOUNT_FREEZE_GUARD_DECISION_FREEZE_REQUIRED,
    )


def test_process_account_unfreeze_with_active_window() -> None:
    result = run_process_account_case(active_window=FakeWindow(id=77), dry_run=False)

    assert_ok(
        "PROCESS_ACTIVE_WINDOW_CALLS_UNFREEZE_0",
        result["calls"] == [(123456, 0)],
    )
    assert_ok(
        "PROCESS_ACTIVE_WINDOW_ACTION_UNFREEZE_SUCCESS",
        result["action"] == statuses.BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_UNFREEZE_SUCCESS,
    )
    assert_ok(
        "PROCESS_ACTIVE_WINDOW_EVENT_DESIRED_FROZEN_0",
        result["events"][-1]["desired_frozen"] == 0,
    )
    assert_ok(
        "PROCESS_ACTIVE_WINDOW_EVENT_APPROVED_WINDOW_ID",
        result["events"][-1]["approved_window_id"] == 77,
    )
    assert_ok(
        "PROCESS_ACTIVE_WINDOW_DECISION_UNFREEZE",
        result["events"][-1]["decision"] == statuses.BYBIT_SUBACCOUNT_FREEZE_GUARD_DECISION_UNFREEZE_WINDOW_ACTIVE,
    )


def test_process_account_expired_window_treated_as_no_window() -> None:
    worker = read("workers/bybit_subaccount_freeze_guard.py")

    assert_ok(
        "ACTIVE_WINDOW_QUERY_REQUIRES_EXPIRES_GT_NOW",
        ".filter(ApprovedBybitSubaccountUnfreezeWindow.expires_at > now)" in worker,
    )

    result = run_process_account_case(active_window=None, dry_run=False)

    assert_ok(
        "PROCESS_EXPIRED_WINDOW_FALLS_BACK_TO_FREEZE_1",
        result["calls"] == [(123456, 1)],
    )


def test_process_account_dry_run_no_bybit_call() -> None:
    result = run_process_account_case(active_window=None, dry_run=True)

    assert_ok(
        "PROCESS_DRY_RUN_NO_BYBIT_CALL",
        result["calls"] == [],
    )
    assert_ok(
        "PROCESS_DRY_RUN_ACTION",
        result["action"] == statuses.BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_DRY_RUN_FREEZE,
    )
    assert_ok(
        "PROCESS_DRY_RUN_EVENT_DECISION",
        result["events"][-1]["decision"] == statuses.BYBIT_SUBACCOUNT_FREEZE_GUARD_DECISION_DRY_RUN,
    )
    assert_ok(
        "PROCESS_DRY_RUN_NO_LOCK",
        result["locks"] == [] and result["lock_created"] is False,
    )


def test_process_account_api_error_fail_closed() -> None:
    result = run_process_account_case(
        active_window=None,
        dry_run=False,
        api_error=RuntimeError("simulated Bybit freeze failure"),
    )

    assert_ok(
        "PROCESS_API_ERROR_ACTION_FREEZE_FAILED",
        result["action"] == statuses.BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_FREEZE_FAILED,
    )
    assert_ok(
        "PROCESS_API_ERROR_EVENT_RECORDED",
        len(result["events"]) == 1 and "simulated Bybit freeze failure" in result["events"][0]["error"],
    )
    assert_ok(
        "PROCESS_API_ERROR_DECISION_FAIL_CLOSED",
        result["events"][0]["decision"] == statuses.BYBIT_SUBACCOUNT_FREEZE_GUARD_DECISION_API_ERROR_FAIL_CLOSED,
    )
    assert_ok(
        "PROCESS_API_ERROR_ALERT_SENT",
        len(result["alerts"]) == 1,
    )
    assert_ok(
        "PROCESS_API_ERROR_EMERGENCY_LOCK_CREATED",
        len(result["locks"]) == 1 and result["lock_created"] is True,
    )


def test_worker_source_contract() -> None:
    worker = read("workers/bybit_subaccount_freeze_guard.py")

    assert_ok("WORKER_EXISTS", "Bybit Subaccount Freeze Guard" in worker)
    assert_ok("WORKER_SUPPORTS_RUN_ONCE", "--run-once" in worker)
    assert_ok("WORKER_SUPPORTS_DRY_RUN", "--dry-run" in worker)

    assert_ok("WORKER_USES_FUND_BYBIT_ACCOUNTS", "FundBybitAccount" in worker)
    assert_ok("WORKER_FILTERS_IS_ACTIVE", "FundBybitAccount.is_active.is_(True)" in worker)
    assert_ok("WORKER_FILTERS_API_KEY_ACTIVE", "FundBybitAccount.api_key_is_active.is_(True)" in worker)
    assert_ok("WORKER_FILTERS_SUBUID_NOT_NULL", "FundBybitAccount.bybit_sub_uid.isnot(None)" in worker)
    assert_ok("WORKER_RESTRICTS_ALLOWED_FUNDS", "BYBIT_SUBACCOUNT_FREEZE_GUARD_ALLOWED_FUND_CODES" in worker)

    assert_ok("WORKER_USES_UNFREEZE_WINDOWS", "ApprovedBybitSubaccountUnfreezeWindow" in worker)
    assert_ok("WORKER_USES_FREEZE_EVENTS", "BybitSubaccountFreezeGuardEvent" in worker)

    assert_ok("WORKER_DEFAULT_FREEZE_NO_WINDOW", "desired_frozen = 0 if window is not None else 1" in worker)
    assert_ok("WORKER_FREEZES_WITHOUT_WINDOW", "BYBIT_SUBACCOUNT_FREEZE_GUARD_DECISION_FREEZE_REQUIRED" in worker)
    assert_ok("WORKER_UNFREEZES_WITH_ACTIVE_WINDOW", "BYBIT_SUBACCOUNT_FREEZE_GUARD_DECISION_UNFREEZE_WINDOW_ACTIVE" in worker)

    assert_ok("WORKER_USES_FREEZE_HELPER", "freeze_sub_uid(" in worker)
    assert_ok("WORKER_DRY_RUN_RECORDS_ONLY", "BYBIT_SUBACCOUNT_FREEZE_GUARD_DECISION_DRY_RUN" in worker)
    assert_ok("WORKER_FAIL_CLOSED_LOCK", "create_platform_emergency_lock" in worker)
    assert_ok("WORKER_FAIL_CLOSED_CONFIG", "BYBIT_SUBACCOUNT_FREEZE_GUARD_FAIL_CLOSED" in worker)
    assert_ok("WORKER_TELEGRAM_ALERT", "send_telegram_message" in worker)
    assert_ok("WORKER_ALERT_COOLDOWN", "BYBIT_SUBACCOUNT_FREEZE_GUARD_ALERT_COOLDOWN_SEC" in worker)

    assert_ok("WORKER_DISABLED_BEFORE_LOOP", "BYBIT_SUBACCOUNT_FREEZE_GUARD_ENABLED=false" in worker)
    assert_ok("WORKER_NO_SECRET_LOGGING_API_SECRET", "api_secret" not in "\n".join(
        line for line in worker.splitlines()
        if "log." in line.lower() or "send_telegram_message" in line
    ))
    assert_ok("WORKER_NO_WALLET_ENC_KEY_TOUCH", "WALLET_ENC_KEY" not in worker)
    assert_ok("WORKER_NO_BYBIT_API_ENC_KEY_TOUCH", "BYBIT_API_ENC_KEY" not in worker)


def test_foundation_source_contract() -> None:
    config = read("app/config.py")
    env = read(".env.example")
    models = read("app/models.py")
    statuses_source = read("app/settlement/statuses.py")
    asset = read("app/bybit/asset_flows.py")
    withdrawal_watchdog = read("workers/bybit_withdrawal_watchdog.py")

    assert_ok("CONFIG_ENABLED_DEFAULT_FALSE", "BYBIT_SUBACCOUNT_FREEZE_GUARD_ENABLED: bool = False" in config)
    assert_ok("CONFIG_DRY_RUN_DEFAULT_TRUE", "BYBIT_SUBACCOUNT_FREEZE_GUARD_DRY_RUN: bool = True" in config)
    assert_ok("ENV_ENABLED_DEFAULT_FALSE", "BYBIT_SUBACCOUNT_FREEZE_GUARD_ENABLED=false" in env)
    assert_ok("ENV_DRY_RUN_DEFAULT_TRUE", "BYBIT_SUBACCOUNT_FREEZE_GUARD_DRY_RUN=true" in env)

    assert_ok("MODEL_UNFREEZE_WINDOW_PRESENT", "class ApprovedBybitSubaccountUnfreezeWindow" in models)
    assert_ok("MODEL_FREEZE_EVENT_PRESENT", "class BybitSubaccountFreezeGuardEvent" in models)

    assert_ok("STATUS_DRY_RUN_FREEZE", "BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_DRY_RUN_FREEZE" in statuses_source)
    assert_ok("STATUS_FREEZE_SUCCESS", "BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_FREEZE_SUCCESS" in statuses_source)
    assert_ok("STATUS_API_ERROR_FAIL_CLOSED", "BYBIT_SUBACCOUNT_FREEZE_GUARD_DECISION_API_ERROR_FAIL_CLOSED" in statuses_source)

    assert_ok("ASSET_HELPER_ENDPOINT", "/v5/user/frozen-sub-member" in asset)
    assert_ok("WITHDRAWAL_WATCHDOG_STILL_PRESENT", "unexpected Bybit withdrawal detected" in withdrawal_watchdog)
    assert_ok("WITHDRAWAL_WATCHDOG_CANCEL_STILL_CONFIG_GATED", "BYBIT_WITHDRAWAL_WATCHDOG_CANCEL_UNEXPECTED" in withdrawal_watchdog)


def main() -> None:
    test_freeze_sub_uid_helper()
    test_process_account_freeze_without_window()
    test_process_account_unfreeze_with_active_window()
    test_process_account_expired_window_treated_as_no_window()
    test_process_account_dry_run_no_bybit_call()
    test_process_account_api_error_fail_closed()
    test_worker_source_contract()
    test_foundation_source_contract()
    print("STAGE26_1_BYBIT_SUBACCOUNT_FREEZE_GUARD_TESTS_OK")


if __name__ == "__main__":
    main()