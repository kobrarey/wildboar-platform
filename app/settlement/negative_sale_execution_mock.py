from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config import settings
from app.settlement.negative_sale_snapshot import dec
from app.settlement.negative_sale_execution_types import (
    HUNDRED,
    ONE,
    ZERO,
    EarnExecutionMock,
    ExtraSaleExecutionMock,
    NegativeSaleExecutionError,
    NegativeSaleExecutionMock,
    SymbolExecutionMock,
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


def _symbol_key(value: Any) -> str:
    text = str(value or "").strip().upper()
    if not text:
        raise NegativeSaleExecutionError("Mock symbol key is empty")

    return text


def _symbol_mock_from_raw(symbol: str, raw: dict[str, Any]) -> SymbolExecutionMock:
    return SymbolExecutionMock(
        symbol=_symbol_key(symbol),
        category=_optional_str(raw.get("category")),
        last_price=dec(raw.get("last_price")),
        best_bid=dec(raw.get("best_bid")),
        best_ask=dec(raw.get("best_ask")),
        available_liquidity_usdt=dec(raw.get("available_liquidity_usdt")),
        available_liquidity_qty=dec(raw.get("available_liquidity_qty")),
        native_strategy_supported=_bool(raw.get("native_strategy_supported")),
        mock_fill_ratio=dec(raw.get("mock_fill_ratio"), "1"),
        fee_usdt=dec(raw.get("fee_usdt"), "0"),
        raw=dict(raw),
    )


def _earn_mock_from_raw(raw: dict[str, Any]) -> EarnExecutionMock:
    return EarnExecutionMock(
        initial_redeemable_usdt=dec(raw.get("initial_redeemable_usdt"), "0"),
        initial_redeem_fill_usdt=dec(raw.get("initial_redeem_fill_usdt"), "0"),
        additional_redeemable_usdt=dec(raw.get("additional_redeemable_usdt"), "0"),
        additional_redeem_fill_usdt=dec(raw.get("additional_redeem_fill_usdt"), "0"),
    )


def _extra_sale_mock_from_raw(raw: dict[str, Any]) -> ExtraSaleExecutionMock:
    return ExtraSaleExecutionMock(
        enabled=_bool(raw.get("enabled")),
        preferred_symbol=_optional_str(raw.get("preferred_symbol")),
        last_price=dec(raw.get("last_price"), "0"),
        best_bid=dec(raw.get("best_bid"), "0"),
        best_ask=dec(raw.get("best_ask"), "0"),
        available_liquidity_usdt=dec(raw.get("available_liquidity_usdt"), "0"),
        available_liquidity_qty=dec(raw.get("available_liquidity_qty"), "0"),
        mock_fill_ratio=dec(raw.get("mock_fill_ratio"), "1"),
        fee_usdt=dec(raw.get("fee_usdt"), "0"),
        raw=dict(raw),
    )


def normalize_negative_sale_execution_mock(raw: dict[str, Any]) -> NegativeSaleExecutionMock:
    if not isinstance(raw, dict):
        raise NegativeSaleExecutionError("Execution mock must be a dict")

    if not _bool(raw.get("mock_only")):
        raise NegativeSaleExecutionError("Stage 23.3 execution mock must have mock_only=true")

    policy = raw.get("execution_policy") or {}
    if not isinstance(policy, dict):
        raise NegativeSaleExecutionError("execution_policy must be a dict")

    earn_raw = raw.get("usdt_earn") or {}
    if not isinstance(earn_raw, dict):
        raise NegativeSaleExecutionError("usdt_earn must be a dict")

    symbols_raw = raw.get("symbols") or {}
    if not isinstance(symbols_raw, dict):
        raise NegativeSaleExecutionError("symbols must be a dict")

    symbols: dict[str, SymbolExecutionMock] = {}
    for symbol, item in symbols_raw.items():
        if not isinstance(item, dict):
            continue

        parsed = _symbol_mock_from_raw(symbol, item)
        symbols[parsed.symbol] = parsed

    extra_raw = raw.get("extra_sale") or {}
    if not isinstance(extra_raw, dict):
        raise NegativeSaleExecutionError("extra_sale must be a dict")

    return NegativeSaleExecutionMock(
        mock_id=str(raw.get("mock_id") or "stage23_3_mock"),
        mock_only=True,
        sell_corridor_pct=dec(
            policy.get("sell_corridor_pct"),
            str(settings.NEGATIVE_NET_SALE_CORRIDOR_PCT),
        ),
        fill_acceptance_pct=dec(
            policy.get("fill_acceptance_pct"),
            str(settings.NEGATIVE_NET_SALE_FILL_ACCEPTANCE_PCT),
        ),
        slices=int(policy.get("slices") or settings.NEGATIVE_NET_SALE_SLICES),
        max_active_strategy_orders=int(
            policy.get("max_active_strategy_orders")
            or settings.NEGATIVE_NET_SALE_MAX_ACTIVE_STRATEGY_ORDERS
        ),
        extra_largest_asset_buffer_pct=dec(
            policy.get("extra_largest_asset_buffer_pct"),
            str(settings.NEGATIVE_NET_EXTRA_LARGEST_ASSET_BUFFER_PCT * HUNDRED),
        ),
        usdt_earn=_earn_mock_from_raw(earn_raw),
        symbols=symbols,
        extra_sale=_extra_sale_mock_from_raw(extra_raw),
        raw=dict(raw),
    )


def load_negative_sale_execution_mock_file(path: str | Path) -> NegativeSaleExecutionMock:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return normalize_negative_sale_execution_mock(raw)

