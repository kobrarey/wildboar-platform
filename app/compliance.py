from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

from app.config import settings

# keccak256("isSanctioned(address)")[:4] = 0xdf592f7d
ORACLE_SELECTOR = "df592f7d"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_address(addr: str) -> str:
    a = (addr or "").strip()
    if not a.startswith("0x") or len(a) != 42:
        return a.lower()
    return a.lower()


def _encode_call_is_sanctioned(address_lower_0x: str) -> str:
    addr = address_lower_0x.lower().replace("0x", "")
    return "0x" + ORACLE_SELECTOR + ("0" * 24) + addr


_ofac_cache: set[str] | None = None
_ofac_cache_mtime: float | None = None


def _load_ofac_set() -> set[str]:
    global _ofac_cache, _ofac_cache_mtime

    root = Path(__file__).resolve().parent.parent
    fp = root / settings.COMPLIANCE_OFAC_FILE

    st = fp.stat()  # may raise
    if _ofac_cache is not None and _ofac_cache_mtime == st.st_mtime:
        return _ofac_cache

    raw = fp.read_text(encoding="utf-8")
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError("OFAC file must be a JSON list of addresses")

    s = set()
    for x in data:
        if isinstance(x, str):
            s.add(x.strip().lower())
    _ofac_cache = s
    _ofac_cache_mtime = st.st_mtime
    return s


async def check_chainalysis_api(address: str, session: aiohttp.ClientSession) -> tuple[str, dict]:
    if not settings.COMPLIANCE_USE_CHAINALYSIS_API:
        return "ok", {"skipped": True}

    if not settings.CHAINALYSIS_SANCTIONS_API_KEY:
        return "error", {"error": "missing_api_key"}

    addr = normalize_address(address)
    url = f"https://public.chainalysis.com/api/v1/address/{addr}"

    try:
        timeout = aiohttp.ClientTimeout(total=int(settings.COMPLIANCE_HTTP_TIMEOUT_SEC))
        async with session.get(url, headers={"X-API-Key": settings.CHAINALYSIS_SANCTIONS_API_KEY}, timeout=timeout) as resp:
            text = await resp.text()
            if resp.status != 200:
                return "error", {"http_status": resp.status, "body": text[:300]}

            data = json.loads(text)
            ident = data.get("identifications") or []
            if ident:
                return "blocked", {"identifications": ident}
            return "ok", {"identifications": []}
    except Exception as e:
        return "error", {"exception": str(e)}


async def _rpc(session: aiohttp.ClientSession, method: str, params: list[Any]) -> Any:
    if not settings.BSC_RPC_URL:
        raise RuntimeError("BSC_RPC_URL is not set")
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    timeout = aiohttp.ClientTimeout(total=int(settings.COMPLIANCE_HTTP_TIMEOUT_SEC))
    async with session.post(settings.BSC_RPC_URL, json=payload, timeout=timeout) as resp:
        data = await resp.json(content_type=None)
        if "error" in data:
            raise RuntimeError(str(data["error"]))
        return data.get("result")


async def check_oracle(address: str, session: aiohttp.ClientSession) -> tuple[str, dict]:
    if not settings.COMPLIANCE_USE_ORACLE:
        return "ok", {"skipped": True}

    addr = normalize_address(address)
    if not re.fullmatch(r"0x[a-f0-9]{40}", addr or ""):
        return "error", {"error": "invalid_address_format"}

    try:
        data_field = _encode_call_is_sanctioned(addr)
        result = await _rpc(
            session,
            "eth_call",
            [{"to": settings.COMPLIANCE_ORACLE_CONTRACT, "data": data_field}, "latest"],
        )
        if not isinstance(result, str) or not result.startswith("0x"):
            return "error", {"error": "bad_rpc_result", "result": result}

        # bool encoded in 32 bytes; any non-zero => True
        is_sanctioned = int(result, 16) != 0
        return ("blocked" if is_sanctioned else "ok"), {"isSanctioned": is_sanctioned}
    except Exception as e:
        return "error", {"exception": str(e)}


async def check_ofac_local(address: str) -> tuple[str, dict]:
    if not settings.COMPLIANCE_USE_OFAC:
        return "ok", {"skipped": True}

    addr = normalize_address(address)
    try:
        s = _load_ofac_set()
        hit = addr in s
        return ("blocked" if hit else "ok"), {"hit": hit}
    except Exception as e:
        return "error", {"exception": str(e)}


async def screen_address(address: str, session: aiohttp.ClientSession) -> tuple[str, dict]:
    """
    final: ok | blocked | pending_check
    """
    details: dict[str, Any] = {"address": normalize_address(address), "ts": utcnow().isoformat()}

    c_status, c_det = await check_chainalysis_api(address, session)
    o_status, o_det = await check_oracle(address, session)
    f_status, f_det = await check_ofac_local(address)

    details["chainalysis_api"] = {"status": c_status, "details": c_det}
    details["oracle"] = {"status": o_status, "details": o_det}
    details["ofac_local"] = {"status": f_status, "details": f_det}

    statuses = [c_status, o_status, f_status]

    if "blocked" in statuses:
        return "blocked", details

    if settings.COMPLIANCE_FAIL_CLOSED and "error" in statuses:
        return "pending_check", details

    return "ok", details


def ensure_user_compliance_ok(user) -> None:
    """
    Raises ValueError with i18n key when user is not allowed to perform operations.
    Intended for future endpoints (buy shares, withdrawals, etc.).
    """
    status = getattr(user, "compliance_status", "ok")
    if status == "ok":
        return
    if status == "blocked":
        raise ValueError("compliance_blocked")
    raise ValueError("compliance_pending_check")