from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import requests

from app.config import settings
from app.navcalc.exceptions import BybitAuthError, BybitNetworkError, NavCalcError


BYBIT_MAINNET = "https://api.bybit.com"
BYBIT_TESTNET = "https://api-testnet.bybit.com"


class BybitApiError(NavCalcError):
    def __init__(self, code: int, msg: str, path: str):
        super().__init__(f"Bybit API error {code} on {path}: {msg}")
        self.code = code
        self.msg = msg
        self.path = path


class BybitClient:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        *,
        testnet: bool = False,
        recv_window: int | None = None,
        timeout: int | None = None,
        retries: int | None = None,
        backoff: float | None = None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.api_secret = (api_secret or "").strip()
        self.base = BYBIT_TESTNET if testnet else BYBIT_MAINNET
        self.recv_window = recv_window or int(settings.BYBIT_NAV_RECV_WINDOW_MS)
        self.timeout = timeout or int(settings.BYBIT_NAV_HTTP_TIMEOUT_SEC)
        self.retries = max(1, int(retries or settings.BYBIT_NAV_RETRIES))
        self.backoff = float(backoff if backoff is not None else settings.BYBIT_NAV_BACKOFF_SEC)
        self.session = requests.Session()

        if not self.api_key or not self.api_secret:
            raise BybitAuthError("Missing Bybit API key/secret")

    def _sign(self, ts: str, payload: str) -> str:
        body = f"{ts}{self.api_key}{self.recv_window}{payload}"
        return hmac.new(
            self.api_secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _request(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        signed: bool = True,
    ) -> dict[str, Any]:
        clean = {k: v for k, v in (params or {}).items() if v not in (None, "")}
        query = "&".join(f"{k}={v}" for k, v in clean.items())
        url = f"{self.base}{path}"
        if query:
            url = f"{url}?{query}"

        last_exc: Exception | None = None

        for attempt in range(self.retries):
            headers: dict[str, str] = {"Accept": "application/json"}

            if signed:
                ts = str(int(time.time() * 1000))
                headers.update(
                    {
                        "X-BAPI-API-KEY": self.api_key,
                        "X-BAPI-TIMESTAMP": ts,
                        "X-BAPI-RECV-WINDOW": str(self.recv_window),
                        "X-BAPI-SIGN": self._sign(ts, query),
                        "X-BAPI-SIGN-TYPE": "2",
                    }
                )

            try:
                resp = self.session.get(url, headers=headers, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout) as exc:
                last_exc = exc
                if attempt + 1 < self.retries:
                    time.sleep(self.backoff * (attempt + 1))
                    continue
                raise BybitNetworkError(f"Network error for {path}: {exc}") from exc

            try:
                resp.raise_for_status()
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else 0
                if status in (401, 403):
                    raise BybitAuthError(f"HTTP {status} for {path}") from exc
                if 500 <= status < 600 and attempt + 1 < self.retries:
                    last_exc = exc
                    time.sleep(self.backoff * (attempt + 1))
                    continue
                raise BybitNetworkError(f"HTTP {status} for {path}") from exc

            try:
                data = resp.json()
            except Exception as exc:
                raise BybitNetworkError(f"Invalid JSON from {path}") from exc

            ret_code = int(data.get("retCode", -1))
            if ret_code != 0:
                ret_msg = str(data.get("retMsg", ""))
                if ret_code in (10003, 10004, 10005, 10007, 10010):
                    raise BybitAuthError(f"{ret_code}: {ret_msg}")
                raise BybitApiError(ret_code, ret_msg, path)

            return data.get("result", {}) or {}

        if last_exc is not None:
            raise BybitNetworkError(f"Request failed for {path}: {last_exc}") from last_exc

        raise BybitNetworkError(f"Request failed for {path}")

    def get(self, path: str, **params: Any) -> dict[str, Any]:
        return self._request(path, params, signed=True)

    def public_get(self, path: str, **params: Any) -> dict[str, Any]:
        return self._request(path, params, signed=False)