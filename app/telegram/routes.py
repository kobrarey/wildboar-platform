from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.operator_actions.service import (
    OperatorActionDisabledError,
    OperatorActionExpiredError,
    OperatorActionInvalidCallbackError,
    OperatorActionInvalidStateError,
    OperatorActionUnauthorizedError,
    confirm_operator_action_from_telegram_callback,
)


router = APIRouter(prefix="/telegram", tags=["telegram"])


def _extract_callback_query(update: dict[str, Any]) -> dict[str, Any] | None:
    callback_query = update.get("callback_query")

    if isinstance(callback_query, dict):
        return callback_query

    return None


def _extract_message(callback_query: dict[str, Any]) -> dict[str, Any]:
    message = callback_query.get("message")

    if isinstance(message, dict):
        return message

    return {}


def _extract_from_user(callback_query: dict[str, Any]) -> dict[str, Any]:
    user = callback_query.get("from")

    if isinstance(user, dict):
        return user

    return {}


def _extract_chat(message: dict[str, Any]) -> dict[str, Any]:
    chat = message.get("chat")

    if isinstance(chat, dict):
        return chat

    return {}


def _safe_str(value: Any) -> str | None:
    if value is None:
        return None

    raw = str(value).strip()
    return raw or None


def _callback_error_response(
    *,
    error_type: str,
    message: str,
    status_code: int,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "handled": False,
            "error_type": error_type,
            "message": message,
        },
    )


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Stage 22.7 Telegram webhook.

    Security policy:
    - accepts only callback_query updates;
    - does not execute shell;
    - does not call Telegram API;
    - does not call BSC;
    - does not perform BNB transfer;
    - only confirms/creates DB operator action.
    """
    try:
        update = await request.json()
    except Exception:
        return _callback_error_response(
            error_type="invalid_json",
            message="Invalid Telegram update JSON",
            status_code=400,
        )

    if not isinstance(update, dict):
        return _callback_error_response(
            error_type="invalid_update",
            message="Telegram update must be a JSON object",
            status_code=400,
        )

    callback_query = _extract_callback_query(update)
    if callback_query is None:
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "handled": False,
                "message": "No callback_query in update",
            },
        )

    callback_data = _safe_str(callback_query.get("data"))
    if not callback_data:
        return _callback_error_response(
            error_type="missing_callback_data",
            message="callback_query.data is required",
            status_code=400,
        )

    message = _extract_message(callback_query)
    from_user = _extract_from_user(callback_query)
    chat = _extract_chat(message)

    telegram_chat_id = _safe_str(chat.get("id"))
    telegram_user_id = _safe_str(from_user.get("id"))
    telegram_message_id = _safe_str(message.get("message_id"))
    telegram_callback_query_id = _safe_str(callback_query.get("id"))

    try:
        result = confirm_operator_action_from_telegram_callback(
            db,
            callback_data=callback_data,
            telegram_chat_id=telegram_chat_id,
            telegram_user_id=telegram_user_id,
            telegram_message_id=telegram_message_id,
            telegram_callback_query_id=telegram_callback_query_id,
            requested_by="telegram_webhook",
            require_enabled=True,
        )
        db.commit()

        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "handled": True,
                "result": result.to_dict(),
            },
        )

    except OperatorActionDisabledError as exc:
        db.rollback()
        return _callback_error_response(
            error_type="operator_actions_disabled",
            message=str(exc),
            status_code=403,
        )

    except OperatorActionUnauthorizedError as exc:
        db.rollback()
        return _callback_error_response(
            error_type="unauthorized_operator",
            message=str(exc),
            status_code=403,
        )

    except OperatorActionExpiredError as exc:
        db.rollback()
        return _callback_error_response(
            error_type="expired_callback",
            message=str(exc),
            status_code=400,
        )

    except OperatorActionInvalidCallbackError as exc:
        db.rollback()
        return _callback_error_response(
            error_type="invalid_callback",
            message=str(exc),
            status_code=400,
        )

    except OperatorActionInvalidStateError as exc:
        db.rollback()
        return _callback_error_response(
            error_type="invalid_state",
            message=str(exc),
            status_code=409,
        )

    except Exception as exc:
        db.rollback()
        return _callback_error_response(
            error_type="unexpected_error",
            message=f"{type(exc).__name__}: {exc}",
            status_code=500,
        )