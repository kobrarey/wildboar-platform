from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def assert_ok(name: str, condition: bool) -> None:
    if not condition:
        raise AssertionError(name)
    print(f"{name}: OK")


def py_files_under(*roots: str) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        base = ROOT / root
        if not base.exists():
            continue
        for path in base.rglob("*.py"):
            if path.resolve() == SELF:
                continue
            if "__pycache__" in path.parts:
                continue
            files.append(path)
    return sorted(files)


def relative(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def denylist_tokens() -> list[str]:
    return [
        "BYBIT_SUBACCOUNT_" + "FREEZE_GUARD",
        "frozen-" + "sub-member",
        "freeze_" + "sub_uid",
        "unfreeze_" + "sub_uid",
        "Subaccount " + "Freeze Guard",
        "freeze " + "guard",
        "Freeze " + "Guard",
        "subaccount " + "freeze",
        "subaccount_" + "freeze",
        "subaccount_" + "frozen",
        "frozen " + "sub member",
        "ApprovedBybit" + "SubaccountUnfreezeWindow",
        "Bybit" + "SubaccountFreezeGuardEvent",
        "approved_bybit_" + "subaccount_unfreeze_windows",
        "bybit_" + "subaccount_freeze_guard_events",
    ]


def find_token_hits(files: list[Path], tokens: list[str]) -> list[str]:
    hits: list[str] = []

    for path in files:
        text = read(path)
        lower_text = text.lower()

        for token in tokens:
            token_lower = token.lower()
            if token_lower not in lower_text:
                continue

            for line_no, line in enumerate(text.splitlines(), start=1):
                if token_lower in line.lower():
                    hits.append(f"{relative(path)}:{line_no}: {token}: {line.strip()}")

    return hits


def test_settings_removed() -> None:
    config = read(ROOT / "app" / "config.py")
    tokens = [
        "BYBIT_SUBACCOUNT_" + "FREEZE_GUARD_ENABLED",
        "BYBIT_SUBACCOUNT_" + "FREEZE_GUARD_DRY_RUN",
        "BYBIT_SUBACCOUNT_" + "FREEZE_GUARD_POLL_SEC",
        "BYBIT_SUBACCOUNT_" + "FREEZE_GUARD_ALLOWED_FUND_CODES",
        "BYBIT_SUBACCOUNT_" + "FREEZE_GUARD_FAIL_CLOSED",
        "BYBIT_SUBACCOUNT_" + "FREEZE_GUARD_ALERT_COOLDOWN_SEC",
    ]

    hits = [token for token in tokens if token in config]

    assert_ok("CONFIG_FREEZE_GUARD_SETTINGS_ABSENT", not hits)

    print("STAGE26_3_12B_FREEZE_GUARD_SETTINGS_REMOVED_OK")


def test_freeze_endpoint_removed() -> None:
    asset_flows = read(ROOT / "app" / "bybit" / "asset_flows.py")

    assert_ok("ASSET_FLOWS_NO_FROZEN_ENDPOINT", "frozen-" + "sub-member" not in asset_flows)
    assert_ok("ASSET_FLOWS_NO_FREEZE_HELPER", "freeze_" + "sub_uid" not in asset_flows)
    assert_ok("ASSET_FLOWS_NO_UNFREEZE_HELPER", "unfreeze_" + "sub_uid" not in asset_flows)

    print("STAGE26_3_12B_FREEZE_ENDPOINT_REMOVED_OK")


def test_freeze_workers_removed() -> None:
    worker_files = [relative(path) for path in (ROOT / "workers").rglob("*.py")]
    forbidden_worker_files = [
        path
        for path in worker_files
        if any(
            token in path.lower()
            for token in [
                "freeze",
                "frozen",
                "unfreeze",
                "subaccount_freeze",
            ]
        )
    ]

    assert_ok("NO_FREEZE_WORKER_FILENAMES", not forbidden_worker_files)

    print("STAGE26_3_12B_FREEZE_WORKERS_REMOVED_OK")


def test_freeze_references_removed() -> None:
    files = py_files_under("app", "workers", "scripts")
    hits = find_token_hits(files, denylist_tokens())

    assert_ok("NO_FREEZE_REFERENCE_HITS", not hits)

    print("STAGE26_3_12B_FREEZE_REFERENCES_REMOVED_OK")


def test_no_frozen_sub_member_endpoint() -> None:
    files = py_files_under("app", "workers", "scripts")
    endpoint = "frozen-" + "sub-member"
    hits = find_token_hits(files, [endpoint])

    assert_ok("NO_FROZEN_SUB_MEMBER_ENDPOINT", not hits)

    print("STAGE26_3_12B_NO_FROZEN_SUB_MEMBER_ENDPOINT_OK")


def test_no_operation_guard_freeze_action() -> None:
    files = py_files_under("app/operation_guard")
    tokens = [
        "BYBIT_SUBACCOUNT_" + "FREEZE_GUARD",
        "frozen-" + "sub-member",
        "freeze_" + "sub_uid",
        "unfreeze_" + "sub_uid",
        "Subaccount " + "Freeze Guard",
        "frozen " + "sub member",
        "bybit_" + "subaccount_freeze_guard",
        "approved_bybit_" + "subaccount_unfreeze",
    ]
    hits = find_token_hits(files, tokens)

    assert_ok("NO_OPERATION_GUARD_FREEZE_ACTION", not hits)


def main() -> int:
    test_settings_removed()
    test_freeze_endpoint_removed()
    test_freeze_workers_removed()
    test_freeze_references_removed()
    test_no_frozen_sub_member_endpoint()
    test_no_operation_guard_freeze_action()

    print("STAGE26_3_12B_FREEZE_GUARD_FULLY_REMOVED_OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())