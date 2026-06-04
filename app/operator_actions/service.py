from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.models import FundOperatorAction, FundSettlementBatch
from app.settlement.statuses import BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED


ACTION_TYPE_RETRY_SETTLEMENT_GAS_TOPUP = "retry_settlement_gas_topup"
ACTION_REASON_INSUFFICIENT_OK_GAS = "insufficient_ok_gas"

OPERATOR_ACTION_STATUS_PENDING = "pending"
OPERATOR_ACTION_STATUS_PROCESSING = "processing"
OPERATOR_ACTION_STATUS_SUCCESS = "success"
OPERATOR_ACTION_STATUS_FAILED = "failed"
OPERATOR_ACTION_STATUS_EXPIRED = "expired"
OPERATOR_ACTION_STATUS_CANCELLED = "cancelled"
OPERATOR_ACTION_STATUS_IGNORED = "ignored"

ACTIVE_OPERATOR_ACTION_STATUSES = {
    OPERATOR_ACTION_STATUS_PENDING,
    OPERATOR_ACTION_STATUS_PROCESSING,
}

FINAL_OPERATOR_ACTION_STATUSES = {
    OPERATOR_ACTION_STATUS_SUCCESS,
    OPERATOR_ACTION_STATUS_FAILED,
    OPERATOR_ACTION_STATUS_EXPIRED,
    OPERATOR_ACTION_STATUS_CANCELLED,
    OPERATOR_ACTION_STATUS_IGNORED,
}

CALLBACK_PREFIX = "sg"  # settlement gas
CALLBACK_SIGNATURE_LEN = 16


class OperatorActionError(RuntimeError):
    pass


class OperatorActionDisabledError(OperatorActionError):
    pass


class OperatorActionUnauthorizedError(OperatorActionError):
    pass


class OperatorActionInvalidCallbackError(OperatorActionError):
    pass


class OperatorActionExpiredError(OperatorActionError):
    pass


class OperatorActionInvalidStateError(OperatorActionError):
    pass


@dataclass(frozen=True)
class OperatorActionCallbackPayload:
    callback_data: str
    fund_id: int
    settlement_batch_id: int | None
    action_type: str
    reason: str
    expires_at: datetime
    token_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "callback_data": self.callback_data,
            "fund_id": self.fund_id,
            "settlement_batch_id": self.settlement_batch_id,
            "action_type": self.action_type,
            "reason": self.reason,
            "expires_at": self.expires_at.isoformat(),
            "token_hash": self.token_hash,
        }


@dataclass(frozen=True)
class OperatorActionConfirmationResult:
    ok: bool
    action_id: int | None
    status: str | None
    action_type: str
    reason: str
    created: bool
    duplicate: bool
    message: str
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["diagnostics"] = _json_dict(raw["diagnostics"])
        return raw


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def dec(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)

    if isinstance(value, Decimal):
        return value

    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]

    return value


def _json_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_value(value) for key, value in data.items()}


def _normalize_id(value: Any) -> str:
    return str(value or "").strip()


def _split_allowed_ids(raw: str) -> set[str]:
    return {
        item.strip()
        for item in str(raw or "").split(",")
        if item.strip()
    }


def _resolve_callback_secret(secret: str | None = None) -> str:
    resolved = str(secret or settings.TELEGRAM_CALLBACK_SECRET or "").strip()

    if not resolved:
        raise OperatorActionInvalidCallbackError(
            "TELEGRAM_CALLBACK_SECRET is required for operator callback signing"
        )

    return resolved


def _hmac_signature(message: str, *, secret: str | None = None) -> str:
    key = _resolve_callback_secret(secret).encode("utf-8")
    digest = hmac.new(
        key,
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    return digest[:CALLBACK_SIGNATURE_LEN]


def hash_callback_token(callback_data: str) -> str:
    return hashlib.sha256(
        str(callback_data or "").encode("utf-8")
    ).hexdigest()


def _constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(str(left or ""), str(right or ""))


def _make_idempotency_key(
    *,
    callback_token_hash: str,
) -> str:
    return f"opact:{ACTION_TYPE_RETRY_SETTLEMENT_GAS_TOPUP}:{callback_token_hash[:32]}"


def _make_active_action_lookup_key(
    *,
    fund_id: int | None,
    settlement_batch_id: int | None,
    action_type: str,
    reason: str | None,
) -> tuple[int | None, int | None, str, str | None]:
    return (
        fund_id,
        settlement_batch_id,
        action_type,
        reason,
    )


def build_retry_settlement_gas_topup_callback_payload(
    *,
    fund_id: int,
    settlement_batch_id: int | None,
    ttl_minutes: int | None = None,
    secret: str | None = None,
    now: datetime | None = None,
) -> OperatorActionCallbackPayload:
    """
    Builds compact signed Telegram callback data.

    callback_data format:
        sg:<fund_id>:<settlement_batch_id_or_0>:<expires_ts>:<nonce>:<sig>

    It contains no private key/material and no wallet private data.
    """
    now = now or utcnow()
    ttl = int(ttl_minutes or settings.TELEGRAM_OPERATOR_ACTION_TTL_MINUTES)

    if ttl <= 0:
        raise OperatorActionInvalidCallbackError(
            "TELEGRAM_OPERATOR_ACTION_TTL_MINUTES must be positive"
        )

    expires_at = now + timedelta(minutes=ttl)
    expires_ts = int(expires_at.timestamp())

    settlement_value = int(settlement_batch_id or 0)
    nonce = secrets.token_hex(4)

    body = f"{CALLBACK_PREFIX}:{int(fund_id)}:{settlement_value}:{expires_ts}:{nonce}"
    sig = _hmac_signature(body, secret=secret)

    callback_data = f"{body}:{sig}"

    return OperatorActionCallbackPayload(
        callback_data=callback_data,
        fund_id=int(fund_id),
        settlement_batch_id=int(settlement_batch_id) if settlement_batch_id else None,
        action_type=ACTION_TYPE_RETRY_SETTLEMENT_GAS_TOPUP,
        reason=ACTION_REASON_INSUFFICIENT_OK_GAS,
        expires_at=expires_at,
        token_hash=hash_callback_token(callback_data),
    )


def parse_retry_settlement_gas_topup_callback_data(
    callback_data: str,
    *,
    secret: str | None = None,
    now: datetime | None = None,
) -> OperatorActionCallbackPayload:
    now = now or utcnow()
    raw = str(callback_data or "").strip()
    parts = raw.split(":")

    if len(parts) != 6:
        raise OperatorActionInvalidCallbackError("Invalid callback_data format")

    prefix, fund_raw, settlement_raw, expires_raw, nonce, sig = parts

    if prefix != CALLBACK_PREFIX:
        raise OperatorActionInvalidCallbackError("Invalid callback_data prefix")

    if not nonce:
        raise OperatorActionInvalidCallbackError("Invalid callback nonce")

    try:
        fund_id = int(fund_raw)
        settlement_value = int(settlement_raw)
        expires_ts = int(expires_raw)
    except Exception as exc:
        raise OperatorActionInvalidCallbackError(
            f"Invalid callback numeric fields: {exc}"
        ) from exc

    if fund_id <= 0:
        raise OperatorActionInvalidCallbackError("Invalid fund_id in callback")

    if settlement_value < 0:
        raise OperatorActionInvalidCallbackError(
            "Invalid settlement_batch_id in callback"
        )

    body = f"{prefix}:{fund_id}:{settlement_value}:{expires_ts}:{nonce}"
    expected_sig = _hmac_signature(body, secret=secret)

    if not _constant_time_equal(sig, expected_sig):
        raise OperatorActionInvalidCallbackError("Invalid callback signature")

    expires_at = datetime.fromtimestamp(expires_ts, tz=timezone.utc)

    if expires_at <= now:
        raise OperatorActionExpiredError("Telegram operator callback expired")

    return OperatorActionCallbackPayload(
        callback_data=raw,
        fund_id=fund_id,
        settlement_batch_id=settlement_value if settlement_value else None,
        action_type=ACTION_TYPE_RETRY_SETTLEMENT_GAS_TOPUP,
        reason=ACTION_REASON_INSUFFICIENT_OK_GAS,
        expires_at=expires_at,
        token_hash=hash_callback_token(raw),
    )


def _validate_operator_actions_enabled(*, require_enabled: bool) -> None:
    if require_enabled and not bool(settings.TELEGRAM_OPERATOR_ACTIONS_ENABLED):
        raise OperatorActionDisabledError(
            "TELEGRAM_OPERATOR_ACTIONS_ENABLED=false"
        )


def _validate_allowed_telegram_actor(
    *,
    telegram_chat_id: str | int | None,
    telegram_user_id: str | int | None,
    allowed_chat_ids: set[str] | None = None,
    allowed_user_ids: set[str] | None = None,
) -> None:
    chat_id = _normalize_id(telegram_chat_id)
    user_id = _normalize_id(telegram_user_id)

    allowed_chats = (
        allowed_chat_ids
        if allowed_chat_ids is not None
        else _split_allowed_ids(settings.TELEGRAM_OPERATOR_ALLOWED_CHAT_IDS)
    )
    allowed_users = (
        allowed_user_ids
        if allowed_user_ids is not None
        else _split_allowed_ids(settings.TELEGRAM_OPERATOR_ALLOWED_USER_IDS)
    )

    if allowed_chats and chat_id not in allowed_chats:
        raise OperatorActionUnauthorizedError(
            f"Telegram chat_id is not allowed: {chat_id}"
        )

    if allowed_users and user_id not in allowed_users:
        raise OperatorActionUnauthorizedError(
            f"Telegram user_id is not allowed: {user_id}"
        )

    if not allowed_chats and not allowed_users:
        raise OperatorActionUnauthorizedError(
            "No allowed Telegram operator chat/user ids configured"
        )


def _get_settlement_batch_for_update(
    db: Session,
    *,
    settlement_batch_id: int,
) -> FundSettlementBatch:
    batch = (
        db.query(FundSettlementBatch)
        .filter(FundSettlementBatch.id == int(settlement_batch_id))
        .with_for_update()
        .first()
    )

    if batch is None:
        raise OperatorActionInvalidStateError(
            f"Settlement batch not found: {settlement_batch_id}"
        )

    return batch


def _validate_settlement_batch_paused_for_insufficient_ok_gas(
    db: Session,
    *,
    fund_id: int,
    settlement_batch_id: int | None,
) -> FundSettlementBatch:
    if settlement_batch_id is None:
        raise OperatorActionInvalidStateError(
            "settlement_batch_id is required for retry_settlement_gas_topup callback"
        )

    batch = _get_settlement_batch_for_update(
        db,
        settlement_batch_id=settlement_batch_id,
    )

    if int(batch.fund_id) != int(fund_id):
        raise OperatorActionInvalidStateError(
            (
                "Callback fund_id does not match settlement batch fund_id: "
                f"callback={fund_id}, batch={batch.fund_id}"
            )
        )

    if batch.status != BATCH_STATUS_PAUSED_OPERATOR_ACTION_REQUIRED:
        raise OperatorActionInvalidStateError(
            (
                "Settlement batch is not paused for operator action: "
                f"batch_id={batch.id}, status={batch.status}"
            )
        )

    error_text = str(batch.error or "")
    if ACTION_REASON_INSUFFICIENT_OK_GAS not in error_text:
        raise OperatorActionInvalidStateError(
            (
                "Settlement batch is not paused for insufficient_ok_gas: "
                f"batch_id={batch.id}, error={batch.error!r}"
            )
        )

    return batch


def _find_active_action(
    db: Session,
    *,
    fund_id: int | None,
    settlement_batch_id: int | None,
    action_type: str,
    reason: str | None,
) -> FundOperatorAction | None:
    q = (
        db.query(FundOperatorAction)
        .filter(
            FundOperatorAction.fund_id == fund_id,
            FundOperatorAction.settlement_batch_id == settlement_batch_id,
            FundOperatorAction.action_type == action_type,
            FundOperatorAction.reason == reason,
            FundOperatorAction.status.in_(list(ACTIVE_OPERATOR_ACTION_STATUSES)),
        )
        .order_by(FundOperatorAction.requested_at.desc(), FundOperatorAction.id.desc())
        .with_for_update()
    )

    return q.first()


def _find_action_by_idempotency_key(
    db: Session,
    *,
    idempotency_key: str,
) -> FundOperatorAction | None:
    return (
        db.query(FundOperatorAction)
        .filter(FundOperatorAction.idempotency_key == idempotency_key)
        .with_for_update()
        .first()
    )


def create_operator_action(
    db: Session,
    *,
    fund_id: int | None,
    settlement_batch_id: int | None,
    allocation_batch_id: int | None = None,
    action_type: str,
    reason: str | None,
    idempotency_key: str,
    callback_token_hash: str | None = None,
    telegram_chat_id: str | int | None = None,
    telegram_user_id: str | int | None = None,
    telegram_message_id: str | int | None = None,
    telegram_callback_query_id: str | int | None = None,
    requested_by: str | None = None,
    expires_at: datetime | None = None,
    payload_json: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> tuple[FundOperatorAction, bool]:
    now = now or utcnow()

    existing_by_key = _find_action_by_idempotency_key(
        db,
        idempotency_key=idempotency_key,
    )
    if existing_by_key is not None:
        return existing_by_key, False

    active_existing = _find_active_action(
        db,
        fund_id=fund_id,
        settlement_batch_id=settlement_batch_id,
        action_type=action_type,
        reason=reason,
    )
    if active_existing is not None:
        return active_existing, False

    action = FundOperatorAction(
        fund_id=fund_id,
        settlement_batch_id=settlement_batch_id,
        allocation_batch_id=allocation_batch_id,
        action_type=action_type,
        reason=reason,
        status=OPERATOR_ACTION_STATUS_PENDING,
        idempotency_key=idempotency_key,
        callback_token_hash=callback_token_hash,
        telegram_chat_id=_normalize_id(telegram_chat_id) or None,
        telegram_user_id=_normalize_id(telegram_user_id) or None,
        telegram_message_id=_normalize_id(telegram_message_id) or None,
        telegram_callback_query_id=_normalize_id(telegram_callback_query_id) or None,
        requested_by=requested_by,
        requested_at=now,
        expires_at=expires_at,
        payload_json=_json_dict(payload_json or {}),
        result_json=None,
        error=None,
        created_at=now,
        updated_at=now,
    )

    db.add(action)

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing_after_race = _find_action_by_idempotency_key(
            db,
            idempotency_key=idempotency_key,
        )
        if existing_after_race is None:
            raise

        return existing_after_race, False

    return action, True


def confirm_operator_action_from_telegram_callback(
    db: Session,
    *,
    callback_data: str,
    telegram_chat_id: str | int | None,
    telegram_user_id: str | int | None,
    telegram_message_id: str | int | None = None,
    telegram_callback_query_id: str | int | None = None,
    requested_by: str | None = None,
    require_enabled: bool = True,
    allowed_chat_ids: set[str] | None = None,
    allowed_user_ids: set[str] | None = None,
    secret: str | None = None,
    now: datetime | None = None,
) -> OperatorActionConfirmationResult:
    now = now or utcnow()

    _validate_operator_actions_enabled(require_enabled=require_enabled)

    _validate_allowed_telegram_actor(
        telegram_chat_id=telegram_chat_id,
        telegram_user_id=telegram_user_id,
        allowed_chat_ids=allowed_chat_ids,
        allowed_user_ids=allowed_user_ids,
    )

    payload = parse_retry_settlement_gas_topup_callback_data(
        callback_data,
        secret=secret,
        now=now,
    )

    _validate_settlement_batch_paused_for_insufficient_ok_gas(
        db,
        fund_id=payload.fund_id,
        settlement_batch_id=payload.settlement_batch_id,
    )

    idempotency_key = _make_idempotency_key(
        callback_token_hash=payload.token_hash,
    )

    action, created = create_operator_action(
        db,
        fund_id=payload.fund_id,
        settlement_batch_id=payload.settlement_batch_id,
        allocation_batch_id=None,
        action_type=payload.action_type,
        reason=payload.reason,
        idempotency_key=idempotency_key,
        callback_token_hash=payload.token_hash,
        telegram_chat_id=telegram_chat_id,
        telegram_user_id=telegram_user_id,
        telegram_message_id=telegram_message_id,
        telegram_callback_query_id=telegram_callback_query_id,
        requested_by=requested_by or "telegram_callback",
        expires_at=payload.expires_at,
        payload_json={
            "source": "telegram_callback",
            "callback_prefix": CALLBACK_PREFIX,
            "fund_id": payload.fund_id,
            "settlement_batch_id": payload.settlement_batch_id,
            "action_type": payload.action_type,
            "reason": payload.reason,
        },
        now=now,
    )

    return OperatorActionConfirmationResult(
        ok=True,
        action_id=action.id,
        status=action.status,
        action_type=payload.action_type,
        reason=payload.reason,
        created=created,
        duplicate=not created,
        message=(
            "Operator action created"
            if created
            else "Duplicate callback ignored; existing operator action reused"
        ),
        diagnostics={
            "fund_id": payload.fund_id,
            "settlement_batch_id": payload.settlement_batch_id,
            "telegram_chat_id": _normalize_id(telegram_chat_id),
            "telegram_user_id": _normalize_id(telegram_user_id),
            "expires_at": payload.expires_at,
        },
    )


def get_pending_operator_actions_for_worker(
    db: Session,
    *,
    action_type: str = ACTION_TYPE_RETRY_SETTLEMENT_GAS_TOPUP,
    reason: str | None = ACTION_REASON_INSUFFICIENT_OK_GAS,
    limit: int = 20,
    now: datetime | None = None,
) -> list[FundOperatorAction]:
    now = now or utcnow()

    q = (
        db.query(FundOperatorAction)
        .filter(
            FundOperatorAction.action_type == action_type,
            FundOperatorAction.status == OPERATOR_ACTION_STATUS_PENDING,
        )
    )

    if reason is None:
        q = q.filter(FundOperatorAction.reason.is_(None))
    else:
        q = q.filter(FundOperatorAction.reason == reason)

    q = q.filter(
        (FundOperatorAction.expires_at.is_(None))
        | (FundOperatorAction.expires_at > now)
    )

    return (
        q.order_by(FundOperatorAction.requested_at.asc(), FundOperatorAction.id.asc())
        .limit(int(limit))
        .all()
    )


def _get_operator_action_for_update(
    db: Session,
    *,
    action_id: int,
) -> FundOperatorAction:
    action = (
        db.query(FundOperatorAction)
        .filter(FundOperatorAction.id == int(action_id))
        .with_for_update()
        .first()
    )

    if action is None:
        raise OperatorActionInvalidStateError(
            f"Operator action not found: {action_id}"
        )

    return action


def mark_operator_action_processing(
    db: Session,
    *,
    action_id: int,
    now: datetime | None = None,
) -> FundOperatorAction:
    now = now or utcnow()
    action = _get_operator_action_for_update(db, action_id=action_id)

    if action.status != OPERATOR_ACTION_STATUS_PENDING:
        raise OperatorActionInvalidStateError(
            f"Operator action is not pending: id={action.id}, status={action.status}"
        )

    if action.expires_at is not None and action.expires_at <= now:
        action.status = OPERATOR_ACTION_STATUS_EXPIRED
        action.processed_at = now
        action.error = "Operator action expired before processing"
        action.updated_at = now
        db.add(action)
        db.flush()
        return action

    action.status = OPERATOR_ACTION_STATUS_PROCESSING
    action.processing_started_at = now
    action.attempts = int(action.attempts or 0) + 1
    action.updated_at = now

    db.add(action)
    db.flush()

    return action


def mark_operator_action_success(
    db: Session,
    *,
    action_id: int,
    result_json: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> FundOperatorAction:
    now = now or utcnow()
    action = _get_operator_action_for_update(db, action_id=action_id)

    action.status = OPERATOR_ACTION_STATUS_SUCCESS
    action.processed_at = now
    action.result_json = _json_dict(result_json or {})
    action.error = None
    action.updated_at = now

    db.add(action)
    db.flush()

    return action


def mark_operator_action_failed(
    db: Session,
    *,
    action_id: int,
    error: str,
    result_json: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> FundOperatorAction:
    now = now or utcnow()
    action = _get_operator_action_for_update(db, action_id=action_id)

    action.status = OPERATOR_ACTION_STATUS_FAILED
    action.processed_at = now
    action.result_json = _json_dict(result_json or {})
    action.error = str(error)
    action.updated_at = now

    db.add(action)
    db.flush()

    return action


def mark_operator_action_cancelled(
    db: Session,
    *,
    action_id: int,
    reason: str,
    now: datetime | None = None,
) -> FundOperatorAction:
    now = now or utcnow()
    action = _get_operator_action_for_update(db, action_id=action_id)

    action.status = OPERATOR_ACTION_STATUS_CANCELLED
    action.processed_at = now
    action.error = str(reason)
    action.updated_at = now

    db.add(action)
    db.flush()

    return action