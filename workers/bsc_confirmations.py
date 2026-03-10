"""BSC confirmations worker (Stage 9.1)."""

import asyncio
import logging
import sys
import time
from datetime import datetime, timezone

import aiohttp
from aiohttp import resolver

from app.config import settings
from app.db import SessionLocal
from app.models import WalletTransfer

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def rpc_get_current_block(session: aiohttp.ClientSession) -> int:
    if not settings.BSC_RPC_URL:
        raise RuntimeError("BSC_RPC_URL is not set")
    payload = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}
    async with session.post(settings.BSC_RPC_URL, json=payload) as resp:
        if resp.status != 200:
            txt = await resp.text()
            raise RuntimeError(f"eth_blockNumber HTTP {resp.status}: {txt[:200]}")
        data = await resp.json(content_type=None)
        return int(data["result"], 16)


def _load_pending_deposits() -> list[WalletTransfer]:
    db = SessionLocal()
    try:
        return (
            db.query(WalletTransfer)
            .filter(WalletTransfer.status == "pending", WalletTransfer.type == "deposit")
            .all()
        )
    finally:
        db.close()


def _mark_success(transfer_id: int):
    db = SessionLocal()
    try:
        tr = db.query(WalletTransfer).filter(WalletTransfer.id == transfer_id).first()
        if not tr or tr.status != "pending":
            return
        tr.status = "success"
        tr.confirmed_at = utcnow()
        db.commit()
    finally:
        db.close()


async def main_loop():
    connector = aiohttp.TCPConnector(
        resolver=resolver.ThreadedResolver(),
        ttl_dns_cache=300,
    )
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        while True:
            try:
                current_block = await rpc_get_current_block(session)
                pending = await asyncio.to_thread(_load_pending_deposits)

                updated = 0
                for tr in pending:
                    if tr.block_number is None:
                        continue
                    if current_block - int(tr.block_number) >= int(settings.BSC_CONFIRMATIONS):
                        await asyncio.to_thread(_mark_success, int(tr.id))
                        updated += 1

                if updated:
                    logging.info("Marked success: %d transfers (current_block=%d)", updated, current_block)

            except Exception as e:
                logging.error("Confirmations loop error: %s", e)

            await asyncio.sleep(int(settings.BSC_CONFIRM_POLL_SEC))


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