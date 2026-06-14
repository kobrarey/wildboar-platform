from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.config import settings
from app.settlement.negative_sale_snapshot import dec
from app.settlement.negative_bybit_flow_types import (
    NegativeBybitFlowError,
    NegativeBybitFlowMock,
    SettlementWalletReceiptMock,
    UniversalTransferMock,
    WhitelistMock,
    WithdrawalMock,
)


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True

    if text in {"0", "false", "no", "n", "off"}:
        return False

    return default


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    return text or None


def _required_str(raw: dict[str, Any], key: str) -> str:
    value = _optional_str(raw.get(key))
    if value is None:
        raise NegativeBybitFlowError(f"Mock field is required: {key}")

    return value


def _nested_dict(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise NegativeBybitFlowError(f"Mock section must be a dict: {key}")

    return value


def _receipt_amount(raw: dict[str, Any]):
    if raw.get("received_amount_usdt") is None:
        return None

    return dec(raw.get("received_amount_usdt"))


def normalize_negative_bybit_flow_mock(raw: dict[str, Any]) -> NegativeBybitFlowMock:
    if not isinstance(raw, dict):
        raise NegativeBybitFlowError("Bybit flow mock must be a dict")

    if not _bool(raw.get("mock_only")):
        raise NegativeBybitFlowError("Stage 23.4 Bybit flow mock must have mock_only=true")

    universal_transfer_raw = _nested_dict(raw, "universal_transfer")
    withdrawal_raw = _nested_dict(raw, "withdrawal")
    receipt_raw = _nested_dict(raw, "settlement_wallet_receipt")
    whitelist_raw = _nested_dict(raw, "whitelist")

    fee_type = int(withdrawal_raw.get("fee_type", settings.NEGATIVE_NET_WITHDRAWAL_FEE_TYPE))
    if fee_type != int(settings.NEGATIVE_NET_WITHDRAWAL_FEE_TYPE):
        raise NegativeBybitFlowError(
            "Stage 23.4 mock withdrawal fee_type must match "
            "NEGATIVE_NET_WITHDRAWAL_FEE_TYPE"
        )

    return NegativeBybitFlowMock(
        mock_id=str(raw.get("mock_id") or "stage23_4_bybit_flow_mock"),
        mock_only=True,
        master_uid=_required_str(raw, "master_uid"),
        fund_sub_uid=_required_str(raw, "fund_sub_uid"),
        universal_transfer=UniversalTransferMock(
            status=str(universal_transfer_raw.get("status") or ""),
            reconcile_status=str(universal_transfer_raw.get("reconcile_status") or ""),
            raw=dict(universal_transfer_raw),
        ),
        withdrawal=WithdrawalMock(
            status=str(withdrawal_raw.get("status") or ""),
            reconcile_status=str(withdrawal_raw.get("reconcile_status") or ""),
            withdrawal_id=_optional_str(withdrawal_raw.get("withdrawal_id")),
            tx_hash=_optional_str(withdrawal_raw.get("tx_hash")),
            fee_type=fee_type,
            raw=dict(withdrawal_raw),
        ),
        settlement_wallet_receipt=SettlementWalletReceiptMock(
            status=str(receipt_raw.get("status") or ""),
            received_amount_matches=_bool(receipt_raw.get("received_amount_matches")),
            received_amount_usdt=_receipt_amount(receipt_raw),
            tx_hash=_optional_str(receipt_raw.get("tx_hash")),
            raw=dict(receipt_raw),
        ),
        whitelist=WhitelistMock(
            internal_db_whitelist_passed=_bool(
                whitelist_raw.get("internal_db_whitelist_passed")
            ),
            bybit_address_whitelist_mock_passed=_bool(
                whitelist_raw.get("bybit_address_whitelist_mock_passed")
            ),
            raw=dict(whitelist_raw),
        ),
        raw=dict(raw),
    )


def load_negative_bybit_flow_mock_file(path: str | Path) -> NegativeBybitFlowMock:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return normalize_negative_bybit_flow_mock(raw)