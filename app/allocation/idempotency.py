from __future__ import annotations

import hashlib


MAX_BYBIT_CLIENT_ID_LEN = 36


def _compact_hash(value: str, *, size: int = 8) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:size]


def _safe_id(value: str, *, max_len: int = MAX_BYBIT_CLIENT_ID_LEN) -> str:
    raw = value.strip().lower()

    allowed = []
    for ch in raw:
        if ch.isalnum() or ch in {"-", "_", ":"}:
            allowed.append(ch)
        else:
            allowed.append("-")

    out = "".join(allowed)
    if len(out) <= max_len:
        return out

    digest = _compact_hash(out)
    keep = max_len - len(digest) - 1
    return f"{out[:keep]}-{digest}"


def make_market_order_link_id(allocation_batch_id: int, leg_id: int) -> str:
    return _safe_id(f"alloc:{allocation_batch_id}:leg:{leg_id}:mkt")


def make_strategy_client_ref(allocation_batch_id: int, leg_id: int) -> str:
    return _safe_id(f"alloc:{allocation_batch_id}:leg:{leg_id}:str")


def make_slice_order_link_id(
    allocation_batch_id: int,
    leg_id: int,
    slice_no: int,
) -> str:
    return _safe_id(f"alloc:{allocation_batch_id}:leg:{leg_id}:s:{slice_no}")


def make_mock_bybit_order_id(order_link_id: str) -> str:
    return _safe_id(f"mock-order:{order_link_id}")


def make_mock_strategy_id(strategy_ref: str) -> str:
    return _safe_id(f"mock-strategy:{strategy_ref}")