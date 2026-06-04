from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from app.operator_actions.service import (
    ACTION_REASON_INSUFFICIENT_OK_GAS,
    ACTION_TYPE_RETRY_SETTLEMENT_GAS_TOPUP,
    OperatorActionCallbackPayload,
    build_retry_settlement_gas_topup_callback_payload,
)


@dataclass(frozen=True)
class TelegramInlineButton:
    text: str
    callback_data: str

    def to_dict(self) -> dict[str, str]:
        return {
            "text": self.text,
            "callback_data": self.callback_data,
        }


@dataclass(frozen=True)
class TelegramAlertPayload:
    text: str
    reply_markup: dict[str, Any]
    callback_payload: OperatorActionCallbackPayload
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["callback_payload"] = self.callback_payload.to_dict()
        return raw


def _dec_str(value: Decimal | str | int | float | None) -> str:
    if value is None:
        return "0"

    if isinstance(value, Decimal):
        return str(value.normalize())

    return str(value)


def build_retry_gas_topup_inline_keyboard(
    *,
    callback_data: str,
) -> dict[str, Any]:
    button = TelegramInlineButton(
        text="Retry gas top-up",
        callback_data=callback_data,
    )

    return {
        "inline_keyboard": [
            [
                button.to_dict(),
            ]
        ]
    }


def build_insufficient_ok_gas_alert_payload(
    *,
    fund_id: int,
    fund_code: str,
    settlement_batch_id: int | None,
    required_bnb: Decimal | str,
    required_usdt: Decimal | str,
    ok_gas_wallet_address: str,
    ttl_minutes: int | None = None,
    secret: str | None = None,
) -> TelegramAlertPayload:
    callback_payload = build_retry_settlement_gas_topup_callback_payload(
        fund_id=fund_id,
        settlement_batch_id=settlement_batch_id,
        ttl_minutes=ttl_minutes,
        secret=secret,
    )

    required_bnb_text = _dec_str(required_bnb)
    required_usdt_text = _dec_str(required_usdt)

    text = "\n".join(
        [
            "Critical: insufficient BNB on OK gas wallet.",
            "",
            f"Fund: {fund_code}",
            f"Reason: {ACTION_REASON_INSUFFICIENT_OK_GAS}",
            f"Required top-up: {required_bnb_text} BNB / approx {required_usdt_text} USDT",
            f"OK gas wallet: {ok_gas_wallet_address}",
            "",
            "After funding the OK gas wallet, press:",
            "[Retry gas top-up]",
        ]
    )

    reply_markup = build_retry_gas_topup_inline_keyboard(
        callback_data=callback_payload.callback_data,
    )

    return TelegramAlertPayload(
        text=text,
        reply_markup=reply_markup,
        callback_payload=callback_payload,
        metadata={
            "fund_id": fund_id,
            "fund_code": fund_code,
            "settlement_batch_id": settlement_batch_id,
            "reason": ACTION_REASON_INSUFFICIENT_OK_GAS,
            "action_type": ACTION_TYPE_RETRY_SETTLEMENT_GAS_TOPUP,
            "ok_gas_wallet_address": ok_gas_wallet_address,
            "required_bnb": required_bnb_text,
            "required_usdt": required_usdt_text,
        },
    )


def build_mock_telegram_send_payload(
    *,
    alert: TelegramAlertPayload,
    chat_id: str | int | None = None,
) -> dict[str, Any]:
    """
    Stage 22.7 mock/suppressed Telegram delivery payload.
    This function only builds the sendMessage payload. It does not call Telegram.
    """
    payload = {
        "chat_id": str(chat_id or ""),
        "text": alert.text,
        "reply_markup": alert.reply_markup,
        "disable_web_page_preview": True,
    }

    return payload