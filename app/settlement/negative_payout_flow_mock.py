from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.settlement.negative_payout_flow_types import (
    NegativePayoutBalanceRefreshMock,
    NegativePayoutFlowError,
    NegativePayoutGasMock,
    NegativePayoutMock,
    NegativePayoutsMock,
)
from app.settlement.negative_sale_snapshot import dec


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
        raise NegativePayoutFlowError(f"Mock field is required: {key}")

    return value


def _nested_dict(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise NegativePayoutFlowError(f"Mock section must be a dict: {key}")

    return value


def _decimal_or_none_auto(value: Any):
    if value is None:
        return None

    if isinstance(value, str) and value.strip().upper() == "AUTO":
        return None

    return dec(value)


def normalize_negative_payout_mock(raw: dict[str, Any]) -> NegativePayoutMock:
    if not isinstance(raw, dict):
        raise NegativePayoutFlowError("Negative payout mock must be a dict")

    if not _bool(raw.get("mock_only")):
        raise NegativePayoutFlowError("Stage 23.5 payout mock must have mock_only=true")

    gas_raw = _nested_dict(raw, "gas")
    payouts_raw = _nested_dict(raw, "payouts")
    balance_refresh_raw = _nested_dict(raw, "balance_refresh")

    return NegativePayoutMock(
        mock_id=str(raw.get("mock_id") or "stage23_5_negative_payout_mock"),
        mock_only=True,
        coin=_required_str(raw, "coin"),
        chain=_required_str(raw, "chain"),
        gas=NegativePayoutGasMock(
            settlement_wallet_bnb_before=dec(
                gas_raw.get("settlement_wallet_bnb_before")
            ),
            required_bnb=dec(gas_raw.get("required_bnb")),
            ok_gas_wallet_bnb_available=dec(
                gas_raw.get("ok_gas_wallet_bnb_available")
            ),
            topup_amount_bnb=dec(gas_raw.get("topup_amount_bnb")),
            topup_tx_hash=_optional_str(gas_raw.get("topup_tx_hash")),
            topup_status=str(gas_raw.get("topup_status") or ""),
            raw=dict(gas_raw),
        ),
        payouts=NegativePayoutsMock(
            default_confirmations=int(payouts_raw.get("default_confirmations") or 0),
            tx_hash_prefix=str(payouts_raw.get("tx_hash_prefix") or "0xmockpayout"),
            all_confirmed=_bool(payouts_raw.get("all_confirmed")),
            raw=dict(payouts_raw),
        ),
        balance_refresh=NegativePayoutBalanceRefreshMock(
            settlement_wallet_usdt_before=_decimal_or_none_auto(
                balance_refresh_raw.get("settlement_wallet_usdt_before")
            ),
            settlement_wallet_usdt_after=_decimal_or_none_auto(
                balance_refresh_raw.get("settlement_wallet_usdt_after")
            ),
            user_wallet_balances=balance_refresh_raw.get("user_wallet_balances"),
            raw=dict(balance_refresh_raw),
        ),
        raw=dict(raw),
    )


def load_negative_payout_mock_file(path: str | Path) -> NegativePayoutMock:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return normalize_negative_payout_mock(raw)