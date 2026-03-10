"""BSC USDT balance updater (Stage 9.1)."""

import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

import aiohttp
from aiohttp import resolver

from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.models import UserWallet

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def rpc_get_current_block(session: aiohttp.ClientSession) -> int:
    payload = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}
    async with session.post(settings.BSC_RPC_URL, json=payload) as resp:
        data = await resp.json(content_type=None)
        return int(data["result"], 16)


async def rpc_usdt_balance_of(session: aiohttp.ClientSession, address: str) -> int:
    selector = "70a08231"
    addr_hex = address[2:].lower()
    data_field = "0x" + selector + ("0" * 24) + addr_hex
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_call",
        "params": [{"to": settings.BSC_USDT_CONTRACT, "data": data_field}, "latest"],
        "id": 1,
    }
    async with session.post(settings.BSC_RPC_URL, json=payload) as resp:
        data = await resp.json(content_type=None)
        return int(data["result"], 16)


def _load_bsc_wallets_to_sync(limit: int = 200) -> list[tuple[int, str]]:
    """
    Select only wallets that have a confirmed (success) deposit newer than last balance update.
    This avoids polling all wallets.
    """
    db = SessionLocal()
    try:
        rows = db.execute(
            text("""
            SELECT w.id, w.address
            FROM user_wallets w
            WHERE w.blockchain = 'BSC'
              AND w.address IS NOT NULL
              AND EXISTS (
                SELECT 1
                FROM wallet_transfers t
                WHERE t.wallet_id = w.id
                  AND t.type = 'deposit'
                  AND t.status = 'success'
                  AND t.confirmed_at IS NOT NULL
                  AND t.confirmed_at > COALESCE(w.usdt_balance_updated_at, TIMESTAMPTZ '1970-01-01')
              )
            ORDER BY w.id
            LIMIT :limit
            """),
            {"limit": limit},
        ).fetchall()

        return [(int(r[0]), str(r[1])) for r in rows]
    finally:
        db.close()


def _update_wallet_balance(wallet_id: int, balance: Decimal, current_block: int):
    db = SessionLocal()
    try:
        w = db.query(UserWallet).filter(UserWallet.id == wallet_id).first()
        if not w:
            return
        w.usdt_balance = balance
        w.usdt_balance_updated_at = utcnow()
        w.usdt_balance_block = current_block
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
                wallets = await asyncio.to_thread(_load_bsc_wallets_to_sync, 200)

                if not wallets:
                    logging.info("No wallets to sync right now (block=%d)", current_block)
                    await asyncio.sleep(int(settings.BSC_BALANCE_POLL_SEC))
                    continue

                for wallet_id, address in wallets:
                    try:
                        bal_int = await rpc_usdt_balance_of(session, address)
                        bal = Decimal(bal_int) / (Decimal(10) ** Decimal(settings.BSC_USDT_DECIMALS))
                        await asyncio.to_thread(_update_wallet_balance, wallet_id, bal, current_block)
                    except Exception as e:
                        logging.warning("Balance update failed for %s: %s", address, e)

                if wallets:
                    logging.info("Updated balances for %d wallets (block=%d)", len(wallets), current_block)

            except Exception as e:
                logging.error("Balance updater loop error: %s", e)

            await asyncio.sleep(int(settings.BSC_BALANCE_POLL_SEC))


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