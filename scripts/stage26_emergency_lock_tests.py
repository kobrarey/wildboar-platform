from __future__ import annotations

from pathlib import Path

from app.emergency_lock import PlatformEmergencyLockSnapshot
from app.settlement import statuses


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def test_status_constants() -> None:
    assert_ok(
        "PLATFORM_LOCK_ACTIVE_STATUS",
        statuses.PLATFORM_EMERGENCY_LOCK_STATUS_ACTIVE == "active",
    )
    assert_ok(
        "PLATFORM_LOCK_RESOLVED_STATUS",
        statuses.PLATFORM_EMERGENCY_LOCK_STATUS_RESOLVED == "resolved",
    )


def test_snapshot_methods() -> None:
    snapshot = PlatformEmergencyLockSnapshot(
        id=123,
        status="active",
        reason="unexpected bybit withdrawal",
        source="bybit_withdrawal_watchdog",
        source_event_id=777,
        created_at=None,
        metadata_json={"x": "y"},
    )

    reason = snapshot.to_reason()
    payload = snapshot.to_dict()

    assert_ok("SNAPSHOT_REASON_HAS_ID", "id=123" in reason)
    assert_ok("SNAPSHOT_REASON_HAS_SOURCE", "bybit_withdrawal_watchdog" in reason)
    assert_ok("SNAPSHOT_REASON_HAS_REASON", "unexpected bybit withdrawal" in reason)
    assert_ok("SNAPSHOT_TO_DICT_HAS_ID", payload["id"] == 123)
    assert_ok("SNAPSHOT_TO_DICT_HAS_METADATA", payload["metadata_json"] == {"x": "y"})


def test_emergency_lock_service_source() -> None:
    src = Path("app/emergency_lock.py").read_text(encoding="utf-8")

    assert_ok("SOURCE_HAS_ACTIVE_LOOKUP", "def active_platform_emergency_lock" in src)
    assert_ok("SOURCE_FILTERS_ACTIVE_STATUS", "PLATFORM_EMERGENCY_LOCK_STATUS_ACTIVE" in src)
    assert_ok("SOURCE_HAS_REQUIRE_NO_ACTIVE", "def require_no_active_platform_emergency_lock" in src)
    assert_ok("SOURCE_RAISES_LOCKED_ERROR", "PlatformEmergencyLockedError" in src)
    assert_ok("SOURCE_HAS_CREATE_LOCK", "def create_platform_emergency_lock" in src)
    assert_ok("SOURCE_HAS_RESOLVE_LOCK", "def resolve_platform_emergency_lock" in src)
    assert_ok("SOURCE_RESOLVES_TO_RESOLVED", "PLATFORM_EMERGENCY_LOCK_STATUS_RESOLVED" in src)


def test_operation_guard_integration_source() -> None:
    src = Path("app/operation_guard/service.py").read_text(encoding="utf-8")

    assert_ok(
        "OP_GUARD_IMPORTS_EMERGENCY_LOCK",
        "from app.emergency_lock import active_platform_emergency_lock_snapshot" in src,
    )
    assert_ok(
        "OP_GUARD_CHECKS_EMERGENCY_LOCK",
        "emergency_lock = active_platform_emergency_lock_snapshot(db)" in src,
    )
    assert_ok(
        "OP_GUARD_BLOCKS_ON_ACTIVE_LOCK",
        "if emergency_lock is not None:" in src
        and "decision=OP_GUARD_DECISION_BLOCKED" in src
        and "reason=emergency_lock.to_reason()" in src,
    )
    assert_ok(
        "OP_GUARD_EMERGENCY_BEFORE_DISABLED_CHECK",
        src.find("emergency_lock = active_platform_emergency_lock_snapshot(db)")
        < src.find("if not settings.OPERATION_GUARD_ENABLED:"),
    )
    assert_ok(
        "OP_GUARD_LOCK_BLOCKS_OVERRIDES",
        "emergency_lock_blocks_manager_overrides" in src,
    )


def main() -> None:
    test_status_constants()
    test_snapshot_methods()
    test_emergency_lock_service_source()
    test_operation_guard_integration_source()
    print("STAGE26_EMERGENCY_LOCK_TESTS_OK")


if __name__ == "__main__":
    main()