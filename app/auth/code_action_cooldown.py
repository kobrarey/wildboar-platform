from __future__ import annotations

import threading
import time

from app.config import settings


_LOCK = threading.Lock()
_LAST_ACTION_AT: dict[str, float] = {}


def enforce_code_action_cooldown(key: str, seconds: int | None = None) -> None:
    """
    In-memory per-action cooldown for code-sending endpoints.

    Important:
    - key must include scenario/action, e.g. register_initial vs register_resend
    - key must include stable identity, e.g. normalized email or user_id
    - this intentionally does not replace security_codes TTL/verification logic
    - isolated helper so it can later be replaced by DB/Redis-backed cooldown
    """
    normalized_key = (key or "").strip().lower()
    if not normalized_key:
        raise ValueError("Invalid cooldown key")

    cooldown = int(seconds if seconds is not None else settings.SECURITY_CODE_RESEND_COOLDOWN_SECONDS)
    if cooldown <= 0:
        return

    now = time.monotonic()

    with _LOCK:
        last = _LAST_ACTION_AT.get(normalized_key)
        if last is not None and (now - last) < cooldown:
            raise ValueError("code_cooldown")

        _LAST_ACTION_AT[normalized_key] = now