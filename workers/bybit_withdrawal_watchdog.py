from __future__ import annotations

import argparse
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Sequence

from sqlalchemy.orm import Session

from app.bybit.asset_flows import (
    BybitWithdrawalResult,
    cancel_master_withdrawal,
    list_master_withdrawals,
)
from app.bybit.client import BybitApiError, BybitV5Client
from app.config import settings
from app.db import SessionLocal
from app.emergency_lock import active_platform_emergency_lock, create_platform_emergency_lock
from app.models import (
    ApprovedBybitWithdrawalWindow,
    BybitWithdrawalWatchdogEvent,
)
from app.settlement.statuses import (
    BYBIT_WITHDRAWAL_WATCHDOG_DECISION_ALLOWED,
    BYBIT_WITHDRAWAL_WATCHDOG_DECISION_API_UNAVAILABLE_FAIL_CLOSED,
    BYBIT_WITHDRAWAL_WATCHDOG_DECISION_CANCEL_FAILED,
    BYBIT_WITHDRAWAL_WATCHDOG_DECISION_CANCEL_SUCCESS,
    BYBIT_WITHDRAWAL_WATCHDOG_DECISION_UNEXPECTED,
    PLATFORM_EMERGENCY_LOCK_STATUS_ACTIVE,
)
from app.telegram import send_telegram_message

log = logging.getLogger("workers.bybit_withdrawal_watchdog")

SOURCE_DETECTED = "bybit_master_api"
WATCHDOG_SOURCE = "bybit_withdrawal_watchdog"

CANCELLABLE_STATUSES = {
    "",
    "PENDING",
    "SECURITYCHECK",
    "SECURITY_CHECK",
    "REVIEWING",
    "TOBECONFIRMED",
    "TOBEconfirmed",
    "PROCESSING",
}


@dataclass(frozen=True)
class BybitWithdrawalRecord:
    bybit_withdrawal_id: str
    coin: str | None
    chain: str | None
    address: str | None
    amount: Decimal | None
    bybit_status: str | None
    raw: dict[str, Any]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Decimal(text)


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def upper_text(value: Any) -> str | None:
    text = clean_text(value)
    return text.upper() if text else None


def normalize_address(value: str | None) -> str | None:
    text = clean_text(value)
    return text.lower() if text else None



def withdrawal_record_from_result(result: BybitWithdrawalResult) -> BybitWithdrawalRecord:
    stable_id = (
        str(result.withdrawal_id or "").strip()
        or str(result.request_id or "").strip()
    )

    if not stable_id:
        raise ValueError("Bybit withdrawal result has no withdrawal_id/request_id")

    return BybitWithdrawalRecord(
        bybit_withdrawal_id=stable_id,
        coin=upper_text(result.coin),
        chain=upper_text(result.chain),
        address=clean_text(result.address),
        amount=result.amount_usdt,
        bybit_status=clean_text(result.status),
        raw=result.raw,
    )



def event_exists(db: Session, *, bybit_withdrawal_id: str) -> bool:
    return (
        db.query(BybitWithdrawalWatchdogEvent.id)
        .filter(BybitWithdrawalWatchdogEvent.bybit_withdrawal_id == bybit_withdrawal_id)
        .first()
        is not None
    )


def active_windows(db: Session, *, now: datetime) -> list[ApprovedBybitWithdrawalWindow]:
    return (
        db.query(ApprovedBybitWithdrawalWindow)
        .filter(ApprovedBybitWithdrawalWindow.status == "active")
        .filter(ApprovedBybitWithdrawalWindow.starts_at <= now)
        .filter(ApprovedBybitWithdrawalWindow.expires_at > now)
        .order_by(ApprovedBybitWithdrawalWindow.expires_at.asc(), ApprovedBybitWithdrawalWindow.id.asc())
        .all()
    )


def withdrawal_matches_window(
    record: BybitWithdrawalRecord,
    window: ApprovedBybitWithdrawalWindow,
) -> bool:
    if upper_text(window.coin) != upper_text(record.coin):
        return False

    if window.chain is not None and upper_text(window.chain) != upper_text(record.chain):
        return False

    if window.address is not None and normalize_address(window.address) != normalize_address(record.address):
        return False

    if record.amount is None:
        return False

    if window.amount_min is not None and record.amount < Decimal(str(window.amount_min)):
        return False

    if window.amount_max is not None and record.amount > Decimal(str(window.amount_max)):
        return False

    return True


def find_approved_window(
    db: Session,
    *,
    record: BybitWithdrawalRecord,
    now: datetime,
) -> ApprovedBybitWithdrawalWindow | None:
    for window in active_windows(db, now=now):
        if withdrawal_matches_window(record, window):
            return window
    return None


def record_watchdog_event(
    db: Session,
    *,
    record: BybitWithdrawalRecord,
    decision: str,
    approved_window_id: int | None = None,
    cancel_attempted: bool = False,
    cancel_success: bool | None = None,
    cancel_error: str | None = None,
) -> BybitWithdrawalWatchdogEvent:
    event = BybitWithdrawalWatchdogEvent(
        bybit_withdrawal_id=record.bybit_withdrawal_id,
        coin=record.coin,
        chain=record.chain,
        address=record.address,
        amount=record.amount,
        bybit_status=record.bybit_status,
        source_detected=SOURCE_DETECTED,
        approved_window_id=approved_window_id,
        decision=decision,
        cancel_attempted=bool(cancel_attempted),
        cancel_success=cancel_success,
        cancel_error=cancel_error,
        raw_json=record.raw,
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
) -> None:
    existing = active_platform_emergency_lock(db)
    if existing is not None and existing.status == PLATFORM_EMERGENCY_LOCK_STATUS_ACTIVE:
        return

    create_platform_emergency_lock(
        db,
        reason=reason,
        source=WATCHDOG_SOURCE,
        source_event_id=source_event_id,
        metadata=metadata or {},
        commit=False,
    )


def send_critical_alert(record: BybitWithdrawalRecord, *, decision: str, detail: str) -> None:
    send_telegram_message(
        "🚨 CRITICAL: unexpected Bybit withdrawal detected\n"
        f"decision={decision}\n"
        f"withdrawal_id={record.bybit_withdrawal_id}\n"
        f"coin={record.coin}\n"
        f"chain={record.chain}\n"
        f"amount={record.amount}\n"
        f"address={record.address}\n"
        f"status={record.bybit_status}\n"
        f"detail={detail}"
    )


def send_api_unavailable_alert(*, event_id: str, error: str) -> None:
    send_telegram_message(
        "🚨 CRITICAL: Bybit withdrawal watchdog API unavailable\n"
        f"event_id={event_id}\n"
        f"fail_closed={settings.BYBIT_WITHDRAWAL_WATCHDOG_FAIL_CLOSED}\n"
        f"error={error[:500]}"
    )


def is_cancellable_status(status: str | None) -> bool:
    return str(status or "").strip().upper() in {x.upper() for x in CANCELLABLE_STATUSES}



def fetch_bybit_withdrawal_records(client: BybitV5Client, *, now: datetime) -> list[BybitWithdrawalRecord]:
    lookback_min = max(1, int(settings.BYBIT_WITHDRAWAL_WATCHDOG_LOOKBACK_MIN))
    start_ms = int((now - timedelta(minutes=lookback_min)).timestamp() * 1000)
    end_ms = int(now.timestamp() * 1000)

    results = list_master_withdrawals(
        client,
        start_time_ms=start_ms,
        end_time_ms=end_ms,
        limit=50,
    )

    records: list[BybitWithdrawalRecord] = []
    for result in results:
        try:
            records.append(withdrawal_record_from_result(result))
        except ValueError as exc:
            log.warning("Skip Bybit withdrawal row without stable id: %s", exc)

    return records


def process_record(
    db: Session,
    *,
    client: BybitV5Client,
    record: BybitWithdrawalRecord,
    now: datetime,
) -> str:
    if event_exists(db, bybit_withdrawal_id=record.bybit_withdrawal_id):
        return "duplicate_skipped"

    approved = find_approved_window(db, record=record, now=now)
    if approved is not None:
        record_watchdog_event(
            db,
            record=record,
            decision=BYBIT_WITHDRAWAL_WATCHDOG_DECISION_ALLOWED,
            approved_window_id=int(approved.id),
        )
        db.commit()
        return "allowed"

    event = record_watchdog_event(
        db,
        record=record,
        decision=BYBIT_WITHDRAWAL_WATCHDOG_DECISION_UNEXPECTED,
    )

    reason = (
        "unexpected Bybit withdrawal outside approved platform window: "
        f"withdrawal_id={record.bybit_withdrawal_id}; "
        f"coin={record.coin}; chain={record.chain}; amount={record.amount}; "
        f"address={record.address}; status={record.bybit_status}"
    )

    create_emergency_lock_if_absent(
        db,
        reason=reason,
        source_event_id=int(event.id),
        metadata={
            "bybit_withdrawal_id": record.bybit_withdrawal_id,
            "coin": record.coin,
            "chain": record.chain,
            "amount": str(record.amount) if record.amount is not None else None,
            "address": record.address,
            "bybit_status": record.bybit_status,
        },
    )

    decision = BYBIT_WITHDRAWAL_WATCHDOG_DECISION_UNEXPECTED
    cancel_attempted = False
    cancel_success = None
    cancel_error = None

    if bool(settings.BYBIT_WITHDRAWAL_WATCHDOG_CANCEL_UNEXPECTED) and is_cancellable_status(record.bybit_status):
        cancel_attempted = True
        try:
            cancel_master_withdrawal(client, withdrawal_id=record.bybit_withdrawal_id)
            cancel_success = True
            decision = BYBIT_WITHDRAWAL_WATCHDOG_DECISION_CANCEL_SUCCESS
        except Exception as exc:
            cancel_success = False
            cancel_error = str(exc)[:1000]
            decision = BYBIT_WITHDRAWAL_WATCHDOG_DECISION_CANCEL_FAILED

    event.decision = decision
    event.cancel_attempted = cancel_attempted
    event.cancel_success = cancel_success
    event.cancel_error = cancel_error
    db.add(event)

    try:
        send_critical_alert(
            record,
            decision=decision,
            detail=cancel_error or "emergency lock activated",
        )
    except Exception as exc:
        log.warning("Telegram critical alert failed: %s", exc)

    db.commit()
    return decision


def api_unavailable_event_id(now: datetime) -> str:
    bucket_sec = max(60, int(settings.BYBIT_WITHDRAWAL_WATCHDOG_API_ERROR_BUCKET_SEC))
    bucket = int(now.timestamp()) // bucket_sec
    return f"api_unavailable:{bucket}"


def handle_api_unavailable(db: Session, *, error: Exception, now: datetime) -> str:
    event_id = api_unavailable_event_id(now)

    if event_exists(db, bybit_withdrawal_id=event_id):
        return "api_unavailable_duplicate_skipped"

    record = BybitWithdrawalRecord(
        bybit_withdrawal_id=event_id,
        coin=None,
        chain=None,
        address=None,
        amount=None,
        bybit_status="api_unavailable",
        raw={
            "error": str(error)[:1000],
            "fail_closed": bool(settings.BYBIT_WITHDRAWAL_WATCHDOG_FAIL_CLOSED),
            "source": WATCHDOG_SOURCE,
        },
    )

    event = record_watchdog_event(
        db,
        record=record,
        decision=BYBIT_WITHDRAWAL_WATCHDOG_DECISION_API_UNAVAILABLE_FAIL_CLOSED,
    )

    create_emergency_lock_if_absent(
        db,
        reason=f"Bybit withdrawal watchdog API unavailable: {str(error)[:500]}",
        source_event_id=int(event.id),
        metadata={
            "event_id": event_id,
            "error": str(error)[:1000],
            "fail_closed": bool(settings.BYBIT_WITHDRAWAL_WATCHDOG_FAIL_CLOSED),
        },
    )

    try:
        send_api_unavailable_alert(event_id=event_id, error=str(error))
    except Exception as exc:
        log.warning("Telegram API unavailable alert failed: %s", exc)

    db.commit()
    return BYBIT_WITHDRAWAL_WATCHDOG_DECISION_API_UNAVAILABLE_FAIL_CLOSED


def build_master_bybit_client() -> BybitV5Client:
    api_key = (os.getenv("BYBIT_MASTER_API_KEY") or "").strip()
    api_secret = (os.getenv("BYBIT_MASTER_API_SECRET") or "").strip()

    if not api_key or not api_secret:
        raise BybitApiError(
            "BYBIT_MASTER_API_KEY / BYBIT_MASTER_API_SECRET are required for withdrawal watchdog"
        )

    return BybitV5Client(
        api_key=api_key,
        api_secret=api_secret,
        recv_window_ms=settings.BYBIT_MASTER_RECV_WINDOW_MS,
    )


def process_once(*, client: BybitV5Client | None = None) -> dict[str, int]:
    now = utcnow()
    counters = {
        "allowed": 0,
        "unexpected": 0,
        "cancel_success": 0,
        "cancel_failed": 0,
        "duplicates": 0,
        "api_unavailable": 0,
    }

    db = SessionLocal()
    try:
        bybit_client = client or build_master_bybit_client()
        records = fetch_bybit_withdrawal_records(bybit_client, now=now)

        for record in records:
            result = process_record(db, client=bybit_client, record=record, now=now)

            if result == "allowed":
                counters["allowed"] += 1
            elif result == "duplicate_skipped":
                counters["duplicates"] += 1
            elif result == BYBIT_WITHDRAWAL_WATCHDOG_DECISION_CANCEL_SUCCESS:
                counters["cancel_success"] += 1
                counters["unexpected"] += 1
            elif result == BYBIT_WITHDRAWAL_WATCHDOG_DECISION_CANCEL_FAILED:
                counters["cancel_failed"] += 1
                counters["unexpected"] += 1
            elif result == BYBIT_WITHDRAWAL_WATCHDOG_DECISION_UNEXPECTED:
                counters["unexpected"] += 1

        return counters

    except Exception as exc:
        db.rollback()
        if bool(settings.BYBIT_WITHDRAWAL_WATCHDOG_FAIL_CLOSED):
            try:
                result = handle_api_unavailable(db, error=exc, now=now)
                if result == BYBIT_WITHDRAWAL_WATCHDOG_DECISION_API_UNAVAILABLE_FAIL_CLOSED:
                    counters["api_unavailable"] += 1
                return counters
            except Exception:
                db.rollback()
                raise

        raise

    finally:
        db.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m workers.bybit_withdrawal_watchdog",
        description=(
            "Stage 26 Bybit withdrawal watchdog. "
            "Detects unexpected Bybit withdrawals, records incidents, and activates platform emergency lock."
        ),
    )
    parser.add_argument("--run-once", action="store_true", help="Run one polling cycle and exit.")
    parser.add_argument(
        "--sleep-sec",
        type=int,
        default=int(settings.BYBIT_WITHDRAWAL_WATCHDOG_POLL_SEC),
        help="Sleep interval in loop mode.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = build_arg_parser().parse_args(argv)

    if args.sleep_sec < 1:
        raise SystemExit("--sleep-sec must be >= 1")

    if not bool(settings.BYBIT_WITHDRAWAL_WATCHDOG_ENABLED):
        log.info("Bybit withdrawal watchdog disabled by BYBIT_WITHDRAWAL_WATCHDOG_ENABLED=false")
        return

    while True:
        counters = process_once()
        log.info("Bybit withdrawal watchdog cycle complete: %s", counters)

        if args.run_once:
            return

        time.sleep(int(args.sleep_sec))


if __name__ == "__main__":
    main()
