from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    Fund,
    FundOperationGuardEvent,
    FundOperationGuardOverride,
    FundOperationGuardState,
    FundSettlementBatch,
    User,
)
from app.emergency_lock import active_platform_emergency_lock_snapshot
from app.operation_guard.statuses import (
    OP_GUARD_ACTION_TYPES,
    OP_GUARD_DECISION_ALLOWED,
    OP_GUARD_DECISION_BLOCKED,
    OP_GUARD_DECISION_ERROR,
    OP_GUARD_MODE_BLOCKED,
    OP_GUARD_MODE_LIVE_ALLOWED,
    OP_GUARD_OVERRIDE_STATUS_ACTIVE,
    OP_GUARD_OVERRIDE_STATUS_EXPIRED,
    OP_GUARD_OVERRIDE_STATUS_REVOKED,
    OP_GUARD_OVERRIDE_STATUS_USED,
    OP_GUARD_SCOPE_FUND,
    OP_GUARD_SCOPE_GLOBAL,
)


class OperationGuardError(RuntimeError):
    pass


class OperationGuardBlockedError(OperationGuardError):
    pass


@dataclass(frozen=True)
class OperationGuardDecision:
    allowed: bool
    decision: str
    reason: str
    action_type: str
    fund_id: int | None
    scope_key: str
    global_mode: str | None
    fund_mode: str | None
    override_id: int | None
    event_id: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dec_or_none(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _scope_for_fund(fund_id: int | None) -> tuple[str, str]:
    if fund_id is None:
        return OP_GUARD_SCOPE_GLOBAL, OP_GUARD_SCOPE_GLOBAL

    return f"fund:{int(fund_id)}", OP_GUARD_SCOPE_FUND


def _validate_action_type(action_type: str) -> None:
    if action_type not in OP_GUARD_ACTION_TYPES:
        raise OperationGuardError(f"Unsupported operation guard action_type: {action_type}")


def _validate_mode(mode: str) -> None:
    if mode not in {OP_GUARD_MODE_BLOCKED, OP_GUARD_MODE_LIVE_ALLOWED}:
        raise OperationGuardError(f"Unsupported operation guard mode: {mode}")


def _validate_manager_user(db: Session, *, manager_user_id: int) -> User:
    manager = (
        db.query(User)
        .filter(User.id == int(manager_user_id))
        .first()
    )
    if manager is None:
        raise OperationGuardError(f"Manager user not found: {manager_user_id}")

    if not bool(manager.is_active):
        raise OperationGuardError(f"Manager user is inactive: {manager_user_id}")

    if settings.OPERATION_GUARD_REQUIRE_MANAGER_ACCOUNT:
        if str(manager.account_type) != "manager":
            raise OperationGuardError(
                f"Operation guard requires manager account_type: {manager_user_id}"
            )

    return manager


def _get_fund(db: Session, *, fund_id: int | None) -> Fund | None:
    if fund_id is None:
        return None

    fund = db.query(Fund).filter(Fund.id == int(fund_id)).first()
    if fund is None:
        raise OperationGuardError(f"Fund not found: {fund_id}")

    return fund


def _get_settlement_batch(
    db: Session,
    *,
    settlement_batch_id: int | None,
) -> FundSettlementBatch | None:
    if settlement_batch_id is None:
        return None

    batch = (
        db.query(FundSettlementBatch)
        .filter(FundSettlementBatch.id == int(settlement_batch_id))
        .first()
    )
    if batch is None:
        raise OperationGuardError(f"Settlement batch not found: {settlement_batch_id}")

    return batch


def _get_guard_state(
    db: Session,
    *,
    action_type: str,
    scope_key: str,
) -> FundOperationGuardState | None:
    return (
        db.query(FundOperationGuardState)
        .filter(FundOperationGuardState.action_type == action_type)
        .filter(FundOperationGuardState.scope_key == scope_key)
        .first()
    )


def _event_should_be_logged(decision: str) -> bool:
    if decision == OP_GUARD_DECISION_ALLOWED:
        return bool(settings.OPERATION_GUARD_LOG_ALLOWED_EVENTS)

    return bool(settings.OPERATION_GUARD_LOG_BLOCKED_EVENTS)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_value(v) for v in value]
    return value


def _log_guard_event(
    db: Session,
    *,
    action_type: str,
    scope_key: str,
    scope_type: str,
    fund_id: int | None,
    settlement_batch_id: int | None,
    request_id: str | None,
    amount_usdt: Decimal | None,
    decision: str,
    reason: str,
    guard_state_id: int | None,
    override_id: int | None,
    mode_snapshot: str | None,
    global_mode: str | None,
    fund_mode: str | None,
    metadata: dict[str, Any] | None,
    now: datetime,
) -> int | None:
    if not _event_should_be_logged(decision):
        return None

    event = FundOperationGuardEvent(
        action_type=action_type,
        scope_key=scope_key,
        scope_type=scope_type,
        fund_id=fund_id,
        settlement_batch_id=settlement_batch_id,
        request_id=request_id,
        amount_usdt=amount_usdt,
        decision=decision,
        reason=reason,
        guard_state_id=guard_state_id,
        override_id=override_id,
        mode_snapshot=mode_snapshot,
        metadata_json={
            "global_mode": global_mode,
            "fund_mode": fund_mode,
            "override_id": override_id,
            "metadata": _json_value(metadata or {}),
            "whitelist_alone_is_insufficient": True,
            "stage": "24",
        },
        created_at=now,
    )
    db.add(event)
    db.flush()
    return int(event.id)


def _build_decision(
    db: Session,
    *,
    allowed: bool,
    decision: str,
    reason: str,
    action_type: str,
    fund_id: int | None,
    settlement_batch_id: int | None,
    request_id: str | None,
    amount_usdt: Decimal | None,
    scope_key: str,
    scope_type: str,
    guard_state_id: int | None,
    override_id: int | None,
    mode_snapshot: str | None,
    global_mode: str | None,
    fund_mode: str | None,
    metadata: dict[str, Any] | None,
    now: datetime,
) -> OperationGuardDecision:
    event_id = _log_guard_event(
        db,
        action_type=action_type,
        scope_key=scope_key,
        scope_type=scope_type,
        fund_id=fund_id,
        settlement_batch_id=settlement_batch_id,
        request_id=request_id,
        amount_usdt=amount_usdt,
        decision=decision,
        reason=reason,
        guard_state_id=guard_state_id,
        override_id=override_id,
        mode_snapshot=mode_snapshot,
        global_mode=global_mode,
        fund_mode=fund_mode,
        metadata=metadata,
        now=now,
    )

    return OperationGuardDecision(
        allowed=allowed,
        decision=decision,
        reason=reason,
        action_type=action_type,
        fund_id=fund_id,
        scope_key=scope_key,
        global_mode=global_mode,
        fund_mode=fund_mode,
        override_id=override_id,
        event_id=event_id,
    )


def _manager_is_valid(
    db: Session,
    *,
    manager_user_id: int,
) -> bool:
    try:
        _validate_manager_user(db, manager_user_id=int(manager_user_id))
        return True
    except OperationGuardError:
        return False


def _override_matches_request(
    db: Session,
    *,
    override: FundOperationGuardOverride,
    action_type: str,
    fund_scope_key: str | None,
    request_id: str | None,
    amount_usdt: Decimal | None,
    now: datetime,
) -> tuple[bool, str]:
    if override.status != OP_GUARD_OVERRIDE_STATUS_ACTIVE:
        return False, f"override status is {override.status}"

    if override.action_type != action_type:
        return False, "override action_type mismatch"

    if not (override.starts_at <= now < override.expires_at):
        return False, "override is outside TTL window"

    allowed_scope_keys = {OP_GUARD_SCOPE_GLOBAL}
    if fund_scope_key is not None:
        allowed_scope_keys.add(fund_scope_key)

    if override.scope_key not in allowed_scope_keys:
        return False, "override scope mismatch"

    if settings.OPERATION_GUARD_REQUIRE_MANAGER_ACCOUNT:
        if not _manager_is_valid(db, manager_user_id=int(override.manager_user_id)):
            return False, "override manager user is invalid"

    if override.request_id is not None and override.request_id != request_id:
        return False, "override request_id mismatch"

    if override.max_amount_usdt is not None:
        if amount_usdt is None:
            return False, "override max_amount_usdt requires amount_usdt"
        if amount_usdt > Decimal(str(override.max_amount_usdt)):
            return False, "override max_amount_usdt exceeded"

    return True, "valid manager override"


def _find_valid_override(
    db: Session,
    *,
    action_type: str,
    fund_id: int | None,
    request_id: str | None,
    amount_usdt: Decimal | None,
    now: datetime,
) -> FundOperationGuardOverride | None:
    fund_scope_key = f"fund:{int(fund_id)}" if fund_id is not None else None
    scope_keys = [OP_GUARD_SCOPE_GLOBAL]
    if fund_scope_key is not None:
        scope_keys.insert(0, fund_scope_key)

    candidates = (
        db.query(FundOperationGuardOverride)
        .filter(FundOperationGuardOverride.action_type == action_type)
        .filter(FundOperationGuardOverride.status == OP_GUARD_OVERRIDE_STATUS_ACTIVE)
        .filter(FundOperationGuardOverride.scope_key.in_(scope_keys))
        .order_by(
            FundOperationGuardOverride.scope_type.desc(),
            FundOperationGuardOverride.expires_at.asc(),
            FundOperationGuardOverride.id.asc(),
        )
        .all()
    )

    for override in candidates:
        ok, _reason = _override_matches_request(
            db,
            override=override,
            action_type=action_type,
            fund_scope_key=fund_scope_key,
            request_id=request_id,
            amount_usdt=amount_usdt,
            now=now,
        )
        if ok:
            return override

    return None


def check_operation_allowed(
    db: Session,
    *,
    action_type: str,
    fund_id: int | None = None,
    amount_usdt: Decimal | None = None,
    request_id: str | None = None,
    settlement_batch_id: int | None = None,
    metadata: dict | None = None,
    now: datetime | None = None,
) -> OperationGuardDecision:
    now = now or _utcnow()
    amount = _dec_or_none(amount_usdt)
    scope_key, scope_type = _scope_for_fund(fund_id)

    try:
        _validate_action_type(action_type)

        emergency_lock = active_platform_emergency_lock_snapshot(db)
        if emergency_lock is not None:
            return _build_decision(
                db,
                allowed=False,
                decision=OP_GUARD_DECISION_BLOCKED,
                reason=emergency_lock.to_reason(),
                action_type=action_type,
                fund_id=fund_id,
                settlement_batch_id=settlement_batch_id,
                request_id=request_id,
                amount_usdt=amount,
                scope_key=scope_key,
                scope_type=scope_type,
                guard_state_id=None,
                override_id=None,
                mode_snapshot=None,
                global_mode=None,
                fund_mode=None,
                metadata={
                    **(metadata or {}),
                    "platform_emergency_lock": emergency_lock.to_dict()
                    if hasattr(emergency_lock, "to_dict")
                    else {
                        "id": emergency_lock.id,
                        "status": emergency_lock.status,
                        "reason": emergency_lock.reason,
                        "source": emergency_lock.source,
                        "source_event_id": emergency_lock.source_event_id,
                        "created_at": emergency_lock.created_at.isoformat()
                        if emergency_lock.created_at is not None
                        else None,
                        "metadata_json": emergency_lock.metadata_json,
                    },
                    "emergency_lock_blocks_even_if_operation_guard_disabled": True,
                    "emergency_lock_blocks_manager_overrides": True,
                },
                now=now,
            )

        if not settings.OPERATION_GUARD_ENABLED:
            return _build_decision(
                db,
                allowed=True,
                decision=OP_GUARD_DECISION_ALLOWED,
                reason="operation guard disabled",
                action_type=action_type,
                fund_id=fund_id,
                settlement_batch_id=settlement_batch_id,
                request_id=request_id,
                amount_usdt=amount,
                scope_key=scope_key,
                scope_type=scope_type,
                guard_state_id=None,
                override_id=None,
                mode_snapshot=None,
                global_mode=None,
                fund_mode=None,
                metadata=metadata,
                now=now,
            )

        global_state = _get_guard_state(
            db,
            action_type=action_type,
            scope_key=OP_GUARD_SCOPE_GLOBAL,
        )
        global_mode = global_state.mode if global_state is not None else None

        fund_state = None
        fund_mode = None
        if fund_id is not None:
            fund_state = _get_guard_state(
                db,
                action_type=action_type,
                scope_key=scope_key,
            )
            fund_mode = fund_state.mode if fund_state is not None else None

        if global_state is None:
            return _build_decision(
                db,
                allowed=False,
                decision=OP_GUARD_DECISION_BLOCKED,
                reason="missing global guard state: fail-closed",
                action_type=action_type,
                fund_id=fund_id,
                settlement_batch_id=settlement_batch_id,
                request_id=request_id,
                amount_usdt=amount,
                scope_key=scope_key,
                scope_type=scope_type,
                guard_state_id=None,
                override_id=None,
                mode_snapshot=None,
                global_mode=global_mode,
                fund_mode=fund_mode,
                metadata=metadata,
                now=now,
            )

        if global_state.mode == OP_GUARD_MODE_BLOCKED:
            override = _find_valid_override(
                db,
                action_type=action_type,
                fund_id=fund_id,
                request_id=request_id,
                amount_usdt=amount,
                now=now,
            )
            if override is not None:
                return _build_decision(
                    db,
                    allowed=True,
                    decision=OP_GUARD_DECISION_ALLOWED,
                    reason="global blocked but valid manager override exists",
                    action_type=action_type,
                    fund_id=fund_id,
                    settlement_batch_id=settlement_batch_id,
                    request_id=request_id,
                    amount_usdt=amount,
                    scope_key=scope_key,
                    scope_type=scope_type,
                    guard_state_id=int(global_state.id),
                    override_id=int(override.id),
                    mode_snapshot=global_state.mode,
                    global_mode=global_mode,
                    fund_mode=fund_mode,
                    metadata=metadata,
                    now=now,
                )

            return _build_decision(
                db,
                allowed=False,
                decision=OP_GUARD_DECISION_BLOCKED,
                reason="global guard mode is blocked",
                action_type=action_type,
                fund_id=fund_id,
                settlement_batch_id=settlement_batch_id,
                request_id=request_id,
                amount_usdt=amount,
                scope_key=scope_key,
                scope_type=scope_type,
                guard_state_id=int(global_state.id),
                override_id=None,
                mode_snapshot=global_state.mode,
                global_mode=global_mode,
                fund_mode=fund_mode,
                metadata=metadata,
                now=now,
            )

        if global_state.mode != OP_GUARD_MODE_LIVE_ALLOWED:
            return _build_decision(
                db,
                allowed=False,
                decision=OP_GUARD_DECISION_BLOCKED,
                reason=f"unsupported global guard mode: {global_state.mode}",
                action_type=action_type,
                fund_id=fund_id,
                settlement_batch_id=settlement_batch_id,
                request_id=request_id,
                amount_usdt=amount,
                scope_key=scope_key,
                scope_type=scope_type,
                guard_state_id=int(global_state.id),
                override_id=None,
                mode_snapshot=global_state.mode,
                global_mode=global_mode,
                fund_mode=fund_mode,
                metadata=metadata,
                now=now,
            )

        if (
            fund_id is not None
            and settings.OPERATION_GUARD_REQUIRE_FUND_STATE_FOR_FUND_ACTIONS
            and fund_state is None
        ):
            override = _find_valid_override(
                db,
                action_type=action_type,
                fund_id=fund_id,
                request_id=request_id,
                amount_usdt=amount,
                now=now,
            )
            if override is not None:
                return _build_decision(
                    db,
                    allowed=True,
                    decision=OP_GUARD_DECISION_ALLOWED,
                    reason="missing fund guard state but valid manager override exists",
                    action_type=action_type,
                    fund_id=fund_id,
                    settlement_batch_id=settlement_batch_id,
                    request_id=request_id,
                    amount_usdt=amount,
                    scope_key=scope_key,
                    scope_type=scope_type,
                    guard_state_id=None,
                    override_id=int(override.id),
                    mode_snapshot=None,
                    global_mode=global_mode,
                    fund_mode=fund_mode,
                    metadata=metadata,
                    now=now,
                )

            return _build_decision(
                db,
                allowed=False,
                decision=OP_GUARD_DECISION_BLOCKED,
                reason="missing fund guard state: fail-closed",
                action_type=action_type,
                fund_id=fund_id,
                settlement_batch_id=settlement_batch_id,
                request_id=request_id,
                amount_usdt=amount,
                scope_key=scope_key,
                scope_type=scope_type,
                guard_state_id=None,
                override_id=None,
                mode_snapshot=None,
                global_mode=global_mode,
                fund_mode=fund_mode,
                metadata=metadata,
                now=now,
            )

        if fund_state is not None and fund_state.mode == OP_GUARD_MODE_BLOCKED:
            override = _find_valid_override(
                db,
                action_type=action_type,
                fund_id=fund_id,
                request_id=request_id,
                amount_usdt=amount,
                now=now,
            )
            if override is not None:
                return _build_decision(
                    db,
                    allowed=True,
                    decision=OP_GUARD_DECISION_ALLOWED,
                    reason="fund blocked but valid manager override exists",
                    action_type=action_type,
                    fund_id=fund_id,
                    settlement_batch_id=settlement_batch_id,
                    request_id=request_id,
                    amount_usdt=amount,
                    scope_key=scope_key,
                    scope_type=scope_type,
                    guard_state_id=int(fund_state.id),
                    override_id=int(override.id),
                    mode_snapshot=fund_state.mode,
                    global_mode=global_mode,
                    fund_mode=fund_mode,
                    metadata=metadata,
                    now=now,
                )

            return _build_decision(
                db,
                allowed=False,
                decision=OP_GUARD_DECISION_BLOCKED,
                reason="fund guard mode is blocked",
                action_type=action_type,
                fund_id=fund_id,
                settlement_batch_id=settlement_batch_id,
                request_id=request_id,
                amount_usdt=amount,
                scope_key=scope_key,
                scope_type=scope_type,
                guard_state_id=int(fund_state.id),
                override_id=None,
                mode_snapshot=fund_state.mode,
                global_mode=global_mode,
                fund_mode=fund_mode,
                metadata=metadata,
                now=now,
            )

        if fund_state is not None and fund_state.mode != OP_GUARD_MODE_LIVE_ALLOWED:
            return _build_decision(
                db,
                allowed=False,
                decision=OP_GUARD_DECISION_BLOCKED,
                reason=f"unsupported fund guard mode: {fund_state.mode}",
                action_type=action_type,
                fund_id=fund_id,
                settlement_batch_id=settlement_batch_id,
                request_id=request_id,
                amount_usdt=amount,
                scope_key=scope_key,
                scope_type=scope_type,
                guard_state_id=int(fund_state.id),
                override_id=None,
                mode_snapshot=fund_state.mode,
                global_mode=global_mode,
                fund_mode=fund_mode,
                metadata=metadata,
                now=now,
            )

        return _build_decision(
            db,
            allowed=True,
            decision=OP_GUARD_DECISION_ALLOWED,
            reason="global live_allowed and no fund block",
            action_type=action_type,
            fund_id=fund_id,
            settlement_batch_id=settlement_batch_id,
            request_id=request_id,
            amount_usdt=amount,
            scope_key=scope_key,
            scope_type=scope_type,
            guard_state_id=int(global_state.id),
            override_id=None,
            mode_snapshot=global_state.mode,
            global_mode=global_mode,
            fund_mode=fund_mode,
            metadata=metadata,
            now=now,
        )

    except Exception as exc:
        if settings.OPERATION_GUARD_FAIL_CLOSED:
            try:
                event_id = _log_guard_event(
                    db,
                    action_type=action_type,
                    scope_key=scope_key,
                    scope_type=scope_type,
                    fund_id=fund_id,
                    settlement_batch_id=settlement_batch_id,
                    request_id=request_id,
                    amount_usdt=amount,
                    decision=OP_GUARD_DECISION_ERROR,
                    reason=f"operation guard error: {exc}",
                    guard_state_id=None,
                    override_id=None,
                    mode_snapshot=None,
                    global_mode=None,
                    fund_mode=None,
                    metadata=metadata,
                    now=now,
                )
            except Exception:
                event_id = None

            return OperationGuardDecision(
                allowed=False,
                decision=OP_GUARD_DECISION_ERROR,
                reason=f"operation guard error: {exc}",
                action_type=action_type,
                fund_id=fund_id,
                scope_key=scope_key,
                global_mode=None,
                fund_mode=None,
                override_id=None,
                event_id=event_id,
            )

        raise


def require_operation_allowed(
    db: Session,
    *,
    action_type: str,
    fund_id: int | None = None,
    amount_usdt: Decimal | None = None,
    request_id: str | None = None,
    settlement_batch_id: int | None = None,
    metadata: dict | None = None,
    now: datetime | None = None,
) -> OperationGuardDecision:
    decision = check_operation_allowed(
        db,
        action_type=action_type,
        fund_id=fund_id,
        amount_usdt=amount_usdt,
        request_id=request_id,
        settlement_batch_id=settlement_batch_id,
        metadata=metadata,
        now=now,
    )
    if not decision.allowed:
        raise OperationGuardBlockedError(decision.reason)

    return decision


def set_operation_guard_state(
    db: Session,
    *,
    action_type: str,
    mode: str,
    fund_id: int | None = None,
    manager_user_id: int | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> FundOperationGuardState:
    now = now or _utcnow()
    _validate_action_type(action_type)
    _validate_mode(mode)
    _get_fund(db, fund_id=fund_id)

    if manager_user_id is not None:
        _validate_manager_user(db, manager_user_id=int(manager_user_id))

    scope_key, scope_type = _scope_for_fund(fund_id)

    state = (
        db.query(FundOperationGuardState)
        .filter(FundOperationGuardState.scope_key == scope_key)
        .filter(FundOperationGuardState.action_type == action_type)
        .with_for_update()
        .first()
    )

    if state is None:
        state = FundOperationGuardState(
            scope_key=scope_key,
            scope_type=scope_type,
            fund_id=fund_id,
            action_type=action_type,
            mode=mode,
            reason=reason,
            updated_by_user_id=manager_user_id,
            created_at=now,
            updated_at=now,
        )
        db.add(state)
    else:
        state.scope_type = scope_type
        state.fund_id = fund_id
        state.mode = mode
        state.reason = reason
        state.updated_by_user_id = manager_user_id
        state.updated_at = now

    db.flush()
    return state


def create_operation_guard_override(
    db: Session,
    *,
    action_type: str,
    manager_user_id: int,
    fund_id: int | None = None,
    settlement_batch_id: int | None = None,
    request_id: str | None = None,
    idempotency_key: str | None = None,
    max_amount_usdt: Decimal | None = None,
    ttl_minutes: int | None = None,
    reason: str | None = None,
    payload: dict | None = None,
    now: datetime | None = None,
) -> FundOperationGuardOverride:
    now = now or _utcnow()
    _validate_action_type(action_type)
    _validate_manager_user(db, manager_user_id=int(manager_user_id))
    _get_fund(db, fund_id=fund_id)
    _get_settlement_batch(db, settlement_batch_id=settlement_batch_id)

    ttl = int(ttl_minutes or settings.OPERATION_GUARD_OVERRIDE_DEFAULT_TTL_MINUTES)
    if ttl <= 0:
        raise OperationGuardError("Override TTL must be positive")

    if ttl > int(settings.OPERATION_GUARD_OVERRIDE_MAX_TTL_MINUTES):
        raise OperationGuardError("Override TTL exceeds max allowed TTL")

    scope_key, scope_type = _scope_for_fund(fund_id)
    idem = idempotency_key or (
        f"op_guard_override:{action_type}:{scope_key}:{manager_user_id}:{uuid4().hex}"
    )

    existing = (
        db.query(FundOperationGuardOverride)
        .filter(FundOperationGuardOverride.idempotency_key == idem)
        .first()
    )
    if existing is not None:
        return existing

    override = FundOperationGuardOverride(
        scope_key=scope_key,
        scope_type=scope_type,
        fund_id=fund_id,
        action_type=action_type,
        status=OP_GUARD_OVERRIDE_STATUS_ACTIVE,
        manager_user_id=int(manager_user_id),
        settlement_batch_id=settlement_batch_id,
        request_id=request_id,
        idempotency_key=idem,
        max_amount_usdt=_dec_or_none(max_amount_usdt),
        starts_at=now,
        expires_at=now + timedelta(minutes=ttl),
        reason=reason,
        payload_json=payload or {},
        result_json=None,
        created_at=now,
        updated_at=now,
    )
    db.add(override)
    db.flush()
    return override


def revoke_operation_guard_override(
    db: Session,
    *,
    override_id: int,
    manager_user_id: int,
    reason: str | None = None,
    now: datetime | None = None,
) -> FundOperationGuardOverride:
    now = now or _utcnow()
    _validate_manager_user(db, manager_user_id=int(manager_user_id))

    override = (
        db.query(FundOperationGuardOverride)
        .filter(FundOperationGuardOverride.id == int(override_id))
        .with_for_update()
        .first()
    )
    if override is None:
        raise OperationGuardError(f"Operation guard override not found: {override_id}")

    override.status = OP_GUARD_OVERRIDE_STATUS_REVOKED
    override.revoked_at = now
    override.reason = reason or override.reason
    override.result_json = {
        "revoked_by_manager_user_id": int(manager_user_id),
        "revoked_at": now.isoformat(),
        "reason": reason,
    }
    override.updated_at = now
    db.flush()
    return override


def mark_operation_guard_override_used(
    db: Session,
    *,
    override_id: int,
    result: dict | None = None,
    now: datetime | None = None,
) -> FundOperationGuardOverride:
    now = now or _utcnow()

    override = (
        db.query(FundOperationGuardOverride)
        .filter(FundOperationGuardOverride.id == int(override_id))
        .with_for_update()
        .first()
    )
    if override is None:
        raise OperationGuardError(f"Operation guard override not found: {override_id}")

    if override.status != OP_GUARD_OVERRIDE_STATUS_ACTIVE:
        raise OperationGuardError(f"Override is not active: {override.status}")

    override.status = OP_GUARD_OVERRIDE_STATUS_USED
    override.used_at = now
    override.result_json = result or {"used_at": now.isoformat()}
    override.updated_at = now
    db.flush()
    return override


def expire_operation_guard_override(
    db: Session,
    *,
    override_id: int,
    now: datetime | None = None,
) -> FundOperationGuardOverride:
    now = now or _utcnow()

    override = (
        db.query(FundOperationGuardOverride)
        .filter(FundOperationGuardOverride.id == int(override_id))
        .with_for_update()
        .first()
    )
    if override is None:
        raise OperationGuardError(f"Operation guard override not found: {override_id}")

    override.status = OP_GUARD_OVERRIDE_STATUS_EXPIRED
    override.updated_at = now
    db.flush()
    return override