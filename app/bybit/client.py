from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import requests

from app.config import settings


log = logging.getLogger("app.bybit.client")


class BybitApiError(RuntimeError):
    pass


class BybitV5Client:
    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        base_url: str = "https://api.bybit.com",
        recv_window_ms: int | None = None,
        timeout_sec: int | None = None,
        retries: int | None = None,
        backoff_sec: Decimal | None = None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.api_secret = (api_secret or "").strip()
        self.base_url = base_url.rstrip("/")
        self.recv_window_ms = int(recv_window_ms or settings.BYBIT_MASTER_RECV_WINDOW_MS)
        self.timeout_sec = int(timeout_sec or settings.BYBIT_MASTER_HTTP_TIMEOUT_SEC)
        self.retries = int(retries if retries is not None else settings.BYBIT_MASTER_RETRIES)
        self.backoff_sec = Decimal(str(backoff_sec if backoff_sec is not None else settings.BYBIT_MASTER_BACKOFF_SEC))

        if not self.api_key:
            raise BybitApiError("Bybit API key is empty")
        if not self.api_secret:
            raise BybitApiError("Bybit API secret is empty")

    def _timestamp_ms(self) -> str:
        return str(int(time.time() * 1000))

    def _sign_get(self, *, timestamp: str, query_string: str) -> str:
        payload = f"{timestamp}{self.api_key}{self.recv_window_ms}{query_string}"
        return hmac.new(
            self.api_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _sign_post(self, *, timestamp: str, body: str) -> str:
        payload = f"{timestamp}{self.api_key}{self.recv_window_ms}{body}"
        return hmac.new(
            self.api_secret.encode("utf-8"),
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _headers(self, *, timestamp: str, signature: str) -> dict[str, str]:
        return {
            "X-BAPI-API-KEY": self.api_key,
            "X-BAPI-SIGN": signature,
            "X-BAPI-SIGN-TYPE": "2",
            "X-BAPI-TIMESTAMP": timestamp,
            "X-BAPI-RECV-WINDOW": str(self.recv_window_ms),
            "Content-Type": "application/json",
        }

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not path.startswith("/"):
            raise ValueError("Bybit path must start with '/'")

        clean_params: dict[str, Any] = {}
        for key, value in (params or {}).items():
            if value is None:
                continue
            clean_params[key] = value

        query_string = urlencode(clean_params)
        url = f"{self.base_url}{path}"

        last_error: Exception | None = None

        for attempt in range(self.retries + 1):
            try:
                timestamp = self._timestamp_ms()
                signature = self._sign_get(timestamp=timestamp, query_string=query_string)
                headers = self._headers(timestamp=timestamp, signature=signature)

                log.debug(
                    "Bybit GET request path=%s params_keys=%s attempt=%s recv_window_ms=%s",
                    path,
                    sorted(clean_params.keys()),
                    attempt + 1,
                    self.recv_window_ms,
                )

                resp = requests.get(
                    url,
                    params=clean_params,
                    headers=headers,
                    timeout=self.timeout_sec,
                )
                resp.raise_for_status()

                data = resp.json()
                ret_code = data.get("retCode")
                if ret_code != 0:
                    raise BybitApiError(
                        f"Bybit API error path={path} retCode={ret_code} retMsg={data.get('retMsg')}"
                    )

                return data

            except Exception as exc:
                last_error = exc
                if attempt >= self.retries:
                    break

                sleep_sec = float(self.backoff_sec * Decimal(attempt + 1))
                time.sleep(sleep_sec)

        raise BybitApiError(f"Bybit GET failed path={path}: {last_error}")

    def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if not path.startswith("/"):
            raise ValueError("Bybit path must start with '/'")

        clean_payload: dict[str, Any] = {}
        for key, value in (payload or {}).items():
            if value is None:
                continue
            clean_payload[key] = value

        body = json.dumps(
            clean_payload,
            separators=(",", ":"),
            ensure_ascii=False,
        )

        url = f"{self.base_url}{path}"

        last_error: Exception | None = None

        for attempt in range(self.retries + 1):
            try:
                timestamp = self._timestamp_ms()
                signature = self._sign_post(timestamp=timestamp, body=body)
                headers = self._headers(timestamp=timestamp, signature=signature)

                log.debug(
                    "Bybit POST request path=%s payload_keys=%s attempt=%s recv_window_ms=%s",
                    path,
                    sorted(clean_payload.keys()),
                    attempt + 1,
                    self.recv_window_ms,
                )

                resp = requests.post(
                    url,
                    data=body,
                    headers=headers,
                    timeout=self.timeout_sec,
                )
                resp.raise_for_status()

                data = resp.json()
                ret_code = data.get("retCode")
                if ret_code != 0:
                    raise BybitApiError(
                        f"Bybit API error path={path} retCode={ret_code} retMsg={data.get('retMsg')}"
                    )

                return data

            except Exception as exc:
                last_error = exc
                if attempt >= self.retries:
                    break

                sleep_sec = float(self.backoff_sec * Decimal(attempt + 1))
                time.sleep(sleep_sec)

        raise BybitApiError(f"Bybit POST failed path={path}: {last_error}")