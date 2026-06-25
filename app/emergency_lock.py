from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import PlatformEmergencyLock
from app.settlement.statuses import (
    PLATFORM_EMERGENCY_LOCK_STATUS_ACTIVE,
    PLATFORM_EMERGENCY_LOCK_STATUS_RESOLVED,
)


class PlatformEmergencyLockError(RuntimeError):
    pass


class PlatformEmergencyLockedError(PlatformEmergencyLockError):
    pass


@dataclass(frozen=True)
class PlatformEmergencyLockSnapshot:
    id: int
    status: str
    reason: str
    source: str
    source_event_id: int | None
    created_at: datetime | None
    metadata_json: dict[str, Any] | None

    def to_reason(self) -> str:
        return (
            "platform emergency lock active: "
            f"id={self.id}; source={self.source}; reason={self.reason}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "reason": self.reason,
            "source": self.source,
            "source_event_id": self.source_event_id,
            "created_at": self.created_at.isoformat() if self.created_at is not None else None,
            "metadata_json": self.metadata_json,
        }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def active_platform_emergency_lock(db: Session) -> PlatformEmergencyLock | None:
    return (
        db.query(PlatformEmergencyLock)
        .filter(PlatformEmergencyLock.status == PLATFORM_EMERGENCY_LOCK_STATUS_ACTIVE)
        .order_by(PlatformEmergencyLock.created_at.desc(), PlatformEmergencyLock.id.desc())
        .first()
    )


def snapshot_platform_emergency_lock(
    lock: PlatformEmergencyLock | None,
) -> PlatformEmergencyLockSnapshot | None:
    if lock is None:
        return None

    return PlatformEmergencyLockSnapshot(
        id=int(lock.id),
        status=str(lock.status),
        reason=str(lock.reason),
        source=str(lock.source),
        source_event_id=int(lock.source_event_id) if lock.source_event_id is not None else None,
        created_at=lock.created_at,
        metadata_json=lock.metadata_json,
    )


def active_platform_emergency_lock_snapshot(
    db: Session,
) -> PlatformEmergencyLockSnapshot | None:
    return snapshot_platform_emergency_lock(active_platform_emergency_lock(db))


def require_no_active_platform_emergency_lock(db: Session) -> None:
    snapshot = active_platform_emergency_lock_snapshot(db)
    if snapshot is not None:
        raise PlatformEmergencyLockedError(snapshot.to_reason())


def create_platform_emergency_lock(
    db: Session,
    *,
    reason: str,
    source: str,
    source_event_id: int | None = None,
    metadata: dict[str, Any] | None = None,
    commit: bool = False,
) -> PlatformEmergencyLock:
    cleaned_reason = str(reason or "").strip()
    cleaned_source = str(source or "").strip()

    if not cleaned_reason:
        raise PlatformEmergencyLockError("Emergency lock reason is required")

    if not cleaned_source:
        raise PlatformEmergencyLockError("Emergency lock source is required")

    lock = PlatformEmergencyLock(
        status=PLATFORM_EMERGENCY_LOCK_STATUS_ACTIVE,
        reason=cleaned_reason,
        source=cleaned_source,
        source_event_id=source_event_id,
        metadata_json=metadata or {},
        created_at=utcnow(),
    )
    db.add(lock)
    db.flush()

    if commit:
        db.commit()

    return lock


def resolve_platform_emergency_lock(
    db: Session,
    *,
    lock_id: int,
    resolved_by: str | None = None,
    resolve_reason: str | None = None,
    commit: bool = False,
) -> PlatformEmergencyLock:
    lock = (
        db.query(PlatformEmergencyLock)
        .filter(PlatformEmergencyLock.id == int(lock_id))
        .with_for_update()
        .first()
    )

    if lock is None:
        raise PlatformEmergencyLockError(f"Emergency lock not found: {lock_id}")

    if lock.status != PLATFORM_EMERGENCY_LOCK_STATUS_ACTIVE:
        return lock

    lock.status = PLATFORM_EMERGENCY_LOCK_STATUS_RESOLVED
    lock.resolved_at = utcnow()
    lock.resolved_by = resolved_by
    lock.resolve_reason = resolve_reason
    db.add(lock)
    db.flush()

    if commit:
        db.commit()

    return lock