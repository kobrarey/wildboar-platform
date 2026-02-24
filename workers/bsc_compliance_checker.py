"""BSC compliance checker (Stage 9.2)

Checks success deposits that have no compliance_status yet.
Fail-closed policy: provider errors -> pending_check (operations blocked).
Run:
    python -m workers.bsc_compliance_checker
"""

import asyncio
import logging
import sys
import time
from datetime import datetime, timezone

import aiohttp
from aiohttp import resolver

from app.config import settings
from app.db import SessionLocal
from app.models import WalletTransfer, UserWallet, User
from app.compliance import screen_address

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def rpc_get_tx_from(session: aiohttp.ClientSession, tx_hash: str) -> str | None:
    if not settings.BSC_RPC_URL:
        return None
    payload = {"jsonrpc": "2.0", "method": "eth_getTransactionByHash", "params": [tx_hash], "id": 1}
    timeout = aiohttp.ClientTimeout(total=int(settings.COMPLIANCE_HTTP_TIMEOUT_SEC))
    async with session.post(settings.BSC_RPC_URL, json=payload, timeout=timeout) as resp:
        data = await resp.json(content_type=None)
        result = data.get("result")
        if not result:
            return None
        tx_from = result.get("from")
        return str(tx_from) if tx_from else None


def _load_targets(limit: int = 100) -> list[WalletTransfer]:
    db = SessionLocal()
    try:
        return (
            db.query(WalletTransfer)
            .filter(
                WalletTransfer.type == "deposit",
                WalletTransfer.status == "success",
                WalletTransfer.compliance_status.is_(None),
            )
            .order_by(WalletTransfer.confirmed_at.desc().nullslast(), WalletTransfer.detected_at.desc())
            .limit(limit)
            .all()
        )
    finally:
        db.close()


def _apply_result(
    transfer_id: int,
    final_status: str,
    details: dict,
    freeze_reason: str | None,
):
    db = SessionLocal()
    try:
        tr = db.query(WalletTransfer).filter(WalletTransfer.id == transfer_id).first()
        if not tr or tr.compliance_status is not None:
            return

        tr.compliance_status = final_status  # ok | blocked | pending_check
        tr.compliance_checked_at = utcnow()
        tr.compliance_details = details

        if final_status in ("blocked", "pending_check"):
            wallet = db.query(UserWallet).filter(UserWallet.id == tr.wallet_id).first()
            user = db.query(User).filter(User.id == tr.user_id).first()

            # эскалация статусов: ok -> pending_check -> blocked; не понижаем
            if wallet:
                if wallet.compliance_status == "ok":
                    wallet.compliance_status = final_status
                    wallet.freeze_reason = freeze_reason
                    wallet.compliance_checked_at = utcnow()
                elif wallet.compliance_status == "pending_check" and final_status == "blocked":
                    wallet.compliance_status = "blocked"
                    wallet.freeze_reason = "sanctions_match"
                    wallet.compliance_checked_at = utcnow()

            if user:
                if user.compliance_status == "ok":
                    user.compliance_status = final_status
                    user.compliance_reason = freeze_reason
                    user.compliance_updated_at = utcnow()
                elif user.compliance_status == "pending_check" and final_status == "blocked":
                    user.compliance_status = "blocked"
                    user.compliance_reason = "sanctions_match"
                    user.compliance_updated_at = utcnow()

        db.commit()
    finally:
        db.close()


async def process_one(tr: WalletTransfer, session: aiohttp.ClientSession):
    addresses: list[str] = []
    if tr.from_address:
        addresses.append(tr.from_address)

    tx_from = None
    try:
        tx_from = await rpc_get_tx_from(session, tr.tx_hash)
    except Exception as e:
        # fail-closed: ошибка получения tx_from трактуем как pending_check
        tx_from = None

    if tx_from:
        addresses.append(tx_from)

    # убираем дубли, пустые
    uniq = []
    seen = set()
    for a in addresses:
        if not a:
            continue
        al = a.lower()
        if al in seen:
            continue
        seen.add(al)
        uniq.append(a)

    if not uniq:
        details = {
            "transfer_id": int(tr.id),
            "tx_hash": tr.tx_hash,
            "checked_at": utcnow().isoformat(),
            "addresses": {},
            "final": "pending_check",
            "note": "no_addresses_to_check",
        }
        await asyncio.to_thread(_apply_result, int(tr.id), "pending_check", details, "screening_error")
        logging.info("Compliance checked: transfer_id=%s final=pending_check (no addresses)", tr.id)
        return

    per_address = {}
    finals = []

    for a in uniq:
        final, det = await screen_address(a, session)
        per_address[a.lower()] = {"final": final, "details": det}
        finals.append(final)

    if "blocked" in finals:
        final_status = "blocked"
        freeze_reason = "sanctions_match"
    elif "pending_check" in finals:
        final_status = "pending_check"
        freeze_reason = "screening_error"
    else:
        final_status = "ok"
        freeze_reason = None

    details = {
        "transfer_id": int(tr.id),
        "tx_hash": tr.tx_hash,
        "checked_at": utcnow().isoformat(),
        "addresses": per_address,
        "final": final_status,
    }

    await asyncio.to_thread(_apply_result, int(tr.id), final_status, details, freeze_reason)
    logging.info("Compliance checked: transfer_id=%s final=%s", tr.id, final_status)


async def main_loop():
    connector = aiohttp.TCPConnector(
        resolver=resolver.ThreadedResolver(),
        ttl_dns_cache=300,
    )
    async with aiohttp.ClientSession(connector=connector) as session:
        while True:
            try:
                targets = await asyncio.to_thread(_load_targets, 100)
                if targets:
                    for tr in targets:
                        await process_one(tr, session)
                else:
                    logging.info("No transfers to check.")
            except Exception as e:
                logging.error("Compliance loop error: %s", e)

            await asyncio.sleep(int(settings.COMPLIANCE_POLL_SEC))


if __name__ == "__main__":
    delay = 5
    max_delay = 60
    while True:
        try:
            asyncio.run(main_loop())
            delay = 5
        except KeyboardInterrupt:
            logging.info("Stopped by user.")
            break
        except Exception as e:
            logging.error("Worker crashed: %s", e)

        logging.info("Restarting in %d seconds...", delay)
        time.sleep(delay)
        delay = min(max_delay, delay * 2)