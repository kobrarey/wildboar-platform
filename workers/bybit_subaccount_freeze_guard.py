from __future__ import annotations

import argparse
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy.orm import Session

from app.bybit.asset_flows import freeze_sub_uid
from app.bybit.client import BybitApiError, BybitV5Client
from app.config import settings
from app.db import SessionLocal
from app.emergency_lock import active_platform_emergency_lock, create_platform_emergency_lock
from app.models import (
    ApprovedBybitSubaccountUnfreezeWindow,
    BybitSubaccountFreezeGuardEvent,
    Fund,
    FundBybitAccount,
)
from app.settlement.statuses import (
    APPROVED_BYBIT_SUBACCOUNT_UNFREEZE_WINDOW_STATUS_ACTIVE,
    BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_DRY_RUN_FREEZE,
    BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_DRY_RUN_UNFREEZE,
    BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_FREEZE_FAILED,
    BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_FREEZE_SUCCESS,
    BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_UNFREEZE_FAILED,
    BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_UNFREEZE_SUCCESS,
    BYBIT_SUBACCOUNT_FREEZE_GUARD_DECISION_API_ERROR_FAIL_CLOSED,
    BYBIT_SUBACCOUNT_FREEZE_GUARD_DECISION_DRY_RUN,
    BYBIT_SUBACCOUNT_FREEZE_GUARD_DECISION_FREEZE_REQUIRED,
    BYBIT_SUBACCOUNT_FREEZE_GUARD_DECISION_UNFREEZE_WINDOW_ACTIVE,
    PLATFORM_EMERGENCY_LOCK_STATUS_ACTIVE,
)
from app.telegram import send_telegram_message

log = logging.getLogger("workers.bybit_subaccount_freeze_guard")

WATCHDOG_SOURCE = "bybit_subaccount_freeze_guard"
LAST_ALERT_AT: dict[str, datetime] = {}


@dataclass(frozen=True)
class ProtectedSubaccount:
    account_id: int
    fund_id: int
    fund_code: str
    bybit_sub_uid: str
    bybit_subaccount_name: str | None


@dataclass
class FreezeGuardCounters:
    protected_seen: int = 0
    freeze_required: int = 0
    unfreeze_window_active: int = 0
    dry_run_actions: int = 0
    api_success: int = 0
    api_errors: int = 0
    emergency_locks_created_or_present: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "protected_seen": self.protected_seen,
            "freeze_required": self.freeze_required,
            "unfreeze_window_active": self.unfreeze_window_active,
            "dry_run_actions": self.dry_run_actions,
            "api_success": self.api_success,
            "api_errors": self.api_errors,
            "emergency_locks_created_or_present": self.emergency_locks_created_or_present,
        }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_allowed_fund_codes() -> set[str]:
    raw = str(settings.BYBIT_SUBACCOUNT_FREEZE_GUARD_ALLOWED_FUND_CODES or "")
    return {x.strip() for x in raw.split(",") if x.strip()}


def clean_sub_uid(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        raise ValueError("bybit_sub_uid is empty")
    return int(text)


def alert_cooldown() -> timedelta:
    seconds = max(60, int(settings.BYBIT_SUBACCOUNT_FREEZE_GUARD_ALERT_COOLDOWN_SEC))
    return timedelta(seconds=seconds)


def should_send_alert(key: str, *, now: datetime) -> bool:
    last = LAST_ALERT_AT.get(key)
    if last is None:
        return True
    return now - last >= alert_cooldown()


def mark_alert_sent(key: str, *, now: datetime) -> None:
    LAST_ALERT_AT[key] = now


def build_master_bybit_client() -> BybitV5Client:
    api_key = (os.getenv("BYBIT_MASTER_API_KEY") or "").strip()
    api_secret = (os.getenv("BYBIT_MASTER_API_SECRET") or "").strip()

    if not api_key or not api_secret:
        raise BybitApiError(
            "BYBIT_MASTER_API_KEY / BYBIT_MASTER_API_SECRET are required for subaccount freeze guard"
        )

    return BybitV5Client(
        api_key=api_key,
        api_secret=api_secret,
        recv_window_ms=settings.BYBIT_MASTER_RECV_WINDOW_MS,
    )


def fetch_protected_subaccounts(db: Session) -> list[ProtectedSubaccount]:
    allowed_codes = parse_allowed_fund_codes()
    if not allowed_codes:
        log.warning("Bybit subaccount freeze guard has no allowed fund codes configured")
        return []

    rows = (
        db.query(FundBybitAccount, Fund)
        .join(Fund, Fund.id == FundBybitAccount.fund_id)
        .filter(FundBybitAccount.is_active.is_(True))
        .filter(FundBybitAccount.api_key_is_active.is_(True))
        .filter(FundBybitAccount.bybit_sub_uid.isnot(None))
        .filter(Fund.code.in_(allowed_codes))
        .order_by(Fund.code.asc(), FundBybitAccount.id.asc())
        .all()
    )

    protected: list[ProtectedSubaccount] = []
    for account, fund in rows:
        try:
            clean_sub_uid(account.bybit_sub_uid)
        except Exception as exc:
            log.warning(
                "Skipping fund Bybit account with invalid sub UID account_id=%s fund_code=%s: %s",
                account.id,
                getattr(fund, "code", None),
                exc,
            )
            continue

        protected.append(
            ProtectedSubaccount(
                account_id=int(account.id),
                fund_id=int(account.fund_id),
                fund_code=str(fund.code),
                bybit_sub_uid=str(account.bybit_sub_uid).strip(),
                bybit_subaccount_name=account.bybit_subaccount_name,
            )
        )

    return protected


def active_unfreeze_window(
    db: Session,
    *,
    account: ProtectedSubaccount,
    now: datetime,
) -> ApprovedBybitSubaccountUnfreezeWindow | None:
    return (
        db.query(ApprovedBybitSubaccountUnfreezeWindow)
        .filter(ApprovedBybitSubaccountUnfreezeWindow.status == APPROVED_BYBIT_SUBACCOUNT_UNFREEZE_WINDOW_STATUS_ACTIVE)
        .filter(ApprovedBybitSubaccountUnfreezeWindow.bybit_sub_uid == account.bybit_sub_uid)
        .filter(ApprovedBybitSubaccountUnfreezeWindow.starts_at <= now)
        .filter(ApprovedBybitSubaccountUnfreezeWindow.expires_at > now)
        .order_by(ApprovedBybitSubaccountUnfreezeWindow.expires_at.asc(), ApprovedBybitSubaccountUnfreezeWindow.id.asc())
        .first()
    )


def record_guard_event(
    db: Session,
    *,
    account: ProtectedSubaccount,
    desired_frozen: int,
    actual_action: str,
    decision: str,
    approved_window_id: int | None = None,
    error: str | None = None,
    raw_json: dict[str, Any] | None = None,
) -> BybitSubaccountFreezeGuardEvent:
    event = BybitSubaccountFreezeGuardEvent(
        fund_id=account.fund_id,
        bybit_sub_uid=account.bybit_sub_uid,
        desired_frozen=int(desired_frozen),
        actual_action=actual_action,
        approved_window_id=approved_window_id,
        decision=decision,
        error=error,
        raw_json=raw_json,
        created_at=utcnow(),
    )
    db.add(event)
    db.flush()
    return event


def create_emergency_lock_if_absent(
    db: Session,
    *,
    reason: str,
    source_event_id: int | None,
    metadata: dict[str, Any] | None = None,
) -> bool:
    existing = active_platform_emergency_lock(db)
    if existing is not None and existing.status == PLATFORM_EMERGENCY_LOCK_STATUS_ACTIVE:
        return False

    create_platform_emergency_lock(
        db,
        reason=reason,
        source=WATCHDOG_SOURCE,
        source_event_id=source_event_id,
        metadata=metadata or {},
        commit=False,
    )
    return True


def send_freeze_failure_alert(
    *,
    account: ProtectedSubaccount,
    desired_frozen: int,
    error: str,
    now: datetime,
) -> None:
    key = f"{account.bybit_sub_uid}:{desired_frozen}:{error[:120]}"
    if not should_send_alert(key, now=now):
        return

    send_telegram_message(
        "🚨 CRITICAL: Bybit subaccount freeze guard API error\n"
        f"fund_code={account.fund_code}\n"
        f"subuid={account.bybit_sub_uid}\n"
        f"desired_frozen={desired_frozen}\n"
        f"subaccount_name={account.bybit_subaccount_name}\n"
        f"error={error[:500]}"
    )
    mark_alert_sent(key, now=now)


def process_account(
    db: Session,
    *,
    client: BybitV5Client | None,
    account: ProtectedSubaccount,
    now: datetime,
    dry_run: bool,
) -> tuple[str, bool]:
    window = active_unfreeze_window(db, account=account, now=now)

    desired_frozen = 0 if window is not None else 1
    approved_window_id = int(window.id) if window is not None else None

    base_decision = (
        BYBIT_SUBACCOUNT_FREEZE_GUARD_DECISION_UNFREEZE_WINDOW_ACTIVE
        if window is not None
        else BYBIT_SUBACCOUNT_FREEZE_GUARD_DECISION_FREEZE_REQUIRED
    )

    if dry_run:
        actual_action = (
            BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_DRY_RUN_UNFREEZE
            if desired_frozen == 0
            else BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_DRY_RUN_FREEZE
        )
        record_guard_event(
            db,
            account=account,
            desired_frozen=desired_frozen,
            actual_action=actual_action,
            decision=BYBIT_SUBACCOUNT_FREEZE_GUARD_DECISION_DRY_RUN,
            approved_window_id=approved_window_id,
            raw_json={
                "dry_run": True,
                "base_decision": base_decision,
                "fund_code": account.fund_code,
                "account_id": account.account_id,
            },
        )
        return actual_action, False

    if client is None:
        raise BybitApiError("Bybit client is required when dry_run=false")

    try:
        raw = freeze_sub_uid(client, subuid=clean_sub_uid(account.bybit_sub_uid), frozen=desired_frozen)
        actual_action = (
            BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_UNFREEZE_SUCCESS
            if desired_frozen == 0
            else BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_FREEZE_SUCCESS
        )
        record_guard_event(
            db,
            account=account,
            desired_frozen=desired_frozen,
            actual_action=actual_action,
            decision=base_decision,
            approved_window_id=approved_window_id,
            raw_json=raw,
        )
        return actual_action, False

    except Exception as exc:
        error = str(exc)[:1000]
        actual_action = (
            BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_UNFREEZE_FAILED
            if desired_frozen == 0
            else BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_FREEZE_FAILED
        )
        event = record_guard_event(
            db,
            account=account,
            desired_frozen=desired_frozen,
            actual_action=actual_action,
            decision=BYBIT_SUBACCOUNT_FREEZE_GUARD_DECISION_API_ERROR_FAIL_CLOSED,
            approved_window_id=approved_window_id,
            error=error,
            raw_json={
                "fund_code": account.fund_code,
                "account_id": account.account_id,
                "fail_closed": bool(settings.BYBIT_SUBACCOUNT_FREEZE_GUARD_FAIL_CLOSED),
            },
        )

        try:
            send_freeze_failure_alert(
                account=account,
                desired_frozen=desired_frozen,
                error=error,
                now=now,
            )
        except Exception as alert_exc:
            log.warning("Bybit subaccount freeze guard Telegram alert failed: %s", alert_exc)

        lock_created_or_present = False
        if bool(settings.BYBIT_SUBACCOUNT_FREEZE_GUARD_FAIL_CLOSED):
            reason = (
                "Bybit subaccount freeze guard API error: "
                f"fund_code={account.fund_code}; subuid={account.bybit_sub_uid}; "
                f"desired_frozen={desired_frozen}; error={error[:500]}"
            )
            lock_created_or_present = create_emergency_lock_if_absent(
                db,
                reason=reason,
                source_event_id=int(event.id),
                metadata={
                    "fund_id": account.fund_id,
                    "fund_code": account.fund_code,
                    "account_id": account.account_id,
                    "bybit_sub_uid": account.bybit_sub_uid,
                    "desired_frozen": desired_frozen,
                    "error": error,
                },
            )

            if not lock_created_or_present:
                lock_created_or_present = active_platform_emergency_lock(db) is not None

        return actual_action, lock_created_or_present


def process_once(*, client: BybitV5Client | None = None, dry_run: bool | None = None) -> FreezeGuardCounters:
    now = utcnow()
    effective_dry_run = bool(settings.BYBIT_SUBACCOUNT_FREEZE_GUARD_DRY_RUN) if dry_run is None else bool(dry_run)

    counters = FreezeGuardCounters()

    db = SessionLocal()
    try:
        protected = fetch_protected_subaccounts(db)
        counters.protected_seen = len(protected)

        bybit_client = None
        if not effective_dry_run:
            bybit_client = client or build_master_bybit_client()

        for account in protected:
            window = active_unfreeze_window(db, account=account, now=now)
            if window is None:
                counters.freeze_required += 1
            else:
                counters.unfreeze_window_active += 1

            action, lock_created_or_present = process_account(
                db,
                client=bybit_client,
                account=account,
                now=now,
                dry_run=effective_dry_run,
            )

            if action in (
                BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_DRY_RUN_FREEZE,
                BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_DRY_RUN_UNFREEZE,
            ):
                counters.dry_run_actions += 1
            elif action in (
                BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_FREEZE_SUCCESS,
                BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_UNFREEZE_SUCCESS,
            ):
                counters.api_success += 1
            elif action in (
                BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_FREEZE_FAILED,
                BYBIT_SUBACCOUNT_FREEZE_GUARD_ACTION_UNFREEZE_FAILED,
            ):
                counters.api_errors += 1

            if lock_created_or_present:
                counters.emergency_locks_created_or_present += 1

        db.commit()
        return counters

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m workers.bybit_subaccount_freeze_guard",
        description=(
            "Stage 26.1 Bybit Subaccount Freeze Guard. "
            "Continuously enforces frozen=1 for protected fund subaccounts unless approved unfreeze window is active."
        ),
    )
    parser.add_argument("--run-once", action="store_true", help="Run one enforcement cycle and exit.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Force dry-run even if BYBIT_SUBACCOUNT_FREEZE_GUARD_DRY_RUN=false.",
    )
    parser.add_argument(
        "--sleep-sec",
        type=int,
        default=int(settings.BYBIT_SUBACCOUNT_FREEZE_GUARD_POLL_SEC),
        help="Sleep interval in loop mode.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = build_arg_parser().parse_args(argv)

    if args.sleep_sec < 1:
        raise SystemExit("--sleep-sec must be >= 1")

    if not bool(settings.BYBIT_SUBACCOUNT_FREEZE_GUARD_ENABLED):
        log.info("Bybit subaccount freeze guard disabled by BYBIT_SUBACCOUNT_FREEZE_GUARD_ENABLED=false")
        return

    while True:
        counters = process_once(dry_run=True if args.dry_run else None)
        log.info("Bybit subaccount freeze guard cycle complete: %s", counters.to_dict())

        if args.run_once:
            return

        time.sleep(int(args.sleep_sec))


if __name__ == "__main__":
    main()