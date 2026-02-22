"""BSC USDT deposit listener (Stage 9.1).

Listens to USDT Transfer logs via WebSocket.
On each Transfer to one of our user wallets -> inserts wallet_transfers row with status='pending'.

Run:
    python -m workers.bsc_usdt_deposit_listener
"""

import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

import websockets
from websockets import exceptions as ws_exceptions

import aiohttp
from aiohttp import resolver
from eth_utils import to_checksum_address
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.db import SessionLocal
from app.models import UserWallet, WalletTransfer

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")


def _encode_topic_address(addr: str) -> str:
    a = addr.lower().replace("0x", "")
    return "0x" + "0" * 24 + a


def _decode_topic_address(topic_hex: str) -> str | None:
    if not isinstance(topic_hex, str) or not topic_hex.startswith("0x"):
        return None
    h = topic_hex[2:].lower()
    if len(h) < 40:
        return None
    return "0x" + h[-40:]


def _hex_to_int(h: str) -> int:
    return int(h, 16) if isinstance(h, str) and h.startswith("0x") else int(h)


def _chunk(lst: list[str], size: int) -> list[list[str]]:
    return [lst[i : i + size] for i in range(0, len(lst), size)]


def load_wallet_map() -> dict[str, tuple[int, int]]:
    """address_lower -> (wallet_id, user_id)."""
    db = SessionLocal()
    try:
        rows = (
            db.query(UserWallet.id, UserWallet.user_id, UserWallet.address)
            .filter(UserWallet.blockchain == "BSC")
            .all()
        )
        m: dict[str, tuple[int, int]] = {}
        for wallet_id, user_id, address in rows:
            if address:
                m[address.lower()] = (int(wallet_id), int(user_id))
        return m
    finally:
        db.close()


async def rpc_get_block_timestamp(session: aiohttp.ClientSession, block_number: int) -> datetime | None:
    if not settings.BSC_RPC_URL:
        return None
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getBlockByNumber",
        "params": [hex(block_number), False],
        "id": 1,
    }
    async with session.post(settings.BSC_RPC_URL, json=payload) as resp:
        if resp.status != 200:
            txt = await resp.text()
            raise RuntimeError(f"eth_getBlockByNumber HTTP {resp.status}: {txt[:200]}")
        data = await resp.json(content_type=None)
        result = data.get("result")
        if not result:
            return None
        ts_hex = result.get("timestamp")
        if not ts_hex:
            return None
        ts = int(ts_hex, 16)
        return datetime.fromtimestamp(ts, tz=timezone.utc)


def db_insert_transfer(
    *,
    user_id: int,
    wallet_id: int,
    tx_hash: str,
    log_index: int,
    block_number: int,
    from_address: str | None,
    to_address: str | None,
    amount: Decimal,
    tx_time: datetime | None,
):
    db = SessionLocal()
    try:
        stmt = (
            insert(WalletTransfer)
            .values(
                user_id=user_id,
                wallet_id=wallet_id,
                coin="USDT",
                network="BSC (BEP20)",
                type="deposit",
                status="pending",
                tx_hash=tx_hash,
                log_index=log_index,
                block_number=block_number,
                from_address=from_address,
                to_address=to_address,
                amount=amount,
                tx_time=tx_time,
            )
            .on_conflict_do_nothing(index_elements=["tx_hash", "log_index"])
        )
        db.execute(stmt)
        db.commit()
    finally:
        db.close()


async def handle_log(log: dict, wallet_map: dict[str, tuple[int, int]], rpc_session: aiohttp.ClientSession, block_time_cache: dict[int, datetime]):
    tx_hash = log.get("transactionHash")
    if not tx_hash:
        return

    topics = log.get("topics", []) or []
    if len(topics) < 3:
        return

    from_addr_raw = _decode_topic_address(topics[1])
    to_addr_raw = _decode_topic_address(topics[2])
    if not to_addr_raw:
        return

    to_lower = to_addr_raw.lower()
    if to_lower not in wallet_map:
        return

    wallet_id, user_id = wallet_map[to_lower]

    try:
        to_addr = to_checksum_address(to_addr_raw)
    except Exception:
        to_addr = to_addr_raw

    try:
        from_addr = to_checksum_address(from_addr_raw) if from_addr_raw else None
    except Exception:
        from_addr = from_addr_raw

    block_number = _hex_to_int(log.get("blockNumber", "0x0"))
    log_index = _hex_to_int(log.get("logIndex", "0x0"))

    amount_int = _hex_to_int(log.get("data") or "0x0")
    amount = Decimal(amount_int) / (Decimal(10) ** Decimal(settings.BSC_USDT_DECIMALS))

    tx_time = block_time_cache.get(block_number)
    if tx_time is None:
        try:
            tx_time = await rpc_get_block_timestamp(rpc_session, block_number)
        except Exception as e:
            logging.warning("Failed to fetch block time for %s: %s", block_number, e)
            tx_time = None
        if tx_time is not None:
            block_time_cache[block_number] = tx_time

    await asyncio.to_thread(
        db_insert_transfer,
        user_id=user_id,
        wallet_id=wallet_id,
        tx_hash=tx_hash,
        log_index=log_index,
        block_number=block_number,
        from_address=from_addr,
        to_address=to_addr,
        amount=amount,
        tx_time=tx_time,
    )

    logging.info("Deposit detected: user_id=%s wallet_id=%s tx=%s li=%s amount=%s", user_id, wallet_id, tx_hash, log_index, str(amount))


async def subscribe_chunk(addresses: list[str], wallet_map: dict[str, tuple[int, int]]):
    if not settings.BSC_WS_URL:
        raise RuntimeError("BSC_WS_URL is not set")

    connector = aiohttp.TCPConnector(resolver=resolver.ThreadedResolver())
    async with aiohttp.ClientSession(connector=connector) as rpc_session:
        block_time_cache: dict[int, datetime] = {}

        async with websockets.connect(settings.BSC_WS_URL, ping_interval=20, ping_timeout=20) as ws:
            req = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "eth_subscribe",
                "params": [
                    "logs",
                    {
                        "address": settings.BSC_USDT_CONTRACT,
                        "topics": [TRANSFER_TOPIC, None, [_encode_topic_address(a) for a in addresses]],
                    },
                ],
            }
            await ws.send(json.dumps(req))

            while True:
                raw = await ws.recv()
                msg = json.loads(raw)
                if msg.get("id") == 1:
                    if "result" not in msg:
                        raise RuntimeError(f"Subscribe failed: {msg}")
                    logging.info("Subscribed (%d addresses), sub_id=%s", len(addresses), msg.get("result"))
                    break

            while True:
                raw = await ws.recv()
                event = json.loads(raw)
                if event.get("method") != "eth_subscription":
                    continue
                log = (event.get("params") or {}).get("result")
                if isinstance(log, dict):
                    await handle_log(log, wallet_map, rpc_session, block_time_cache)


async def main():
    wallet_map = load_wallet_map()
    if not wallet_map:
        logging.warning("No BSC wallets found in DB. Sleeping...")

    addresses = list(wallet_map.keys())
    chunks = _chunk(addresses, 1000) if addresses else [[]]

    tasks = [asyncio.create_task(subscribe_chunk(ch, wallet_map)) for ch in chunks if ch]
    if not tasks:
        while True:
            await asyncio.sleep(30)

    await asyncio.gather(*tasks)


if __name__ == "__main__":
    delay = 5
    max_delay = 60
    while True:
        try:
            asyncio.run(main())
            delay = 5
        except KeyboardInterrupt:
            logging.info("Stopped by user.")
            break
        except (ws_exceptions.ConnectionClosedError, ws_exceptions.ConnectionClosedOK) as e:
            logging.warning("WebSocket closed: %s", e)
        except Exception as e:
            logging.exception("Worker crashed: %s", e)

        logging.info("Restarting in %d seconds...", delay)
        time.sleep(delay)
        delay = min(max_delay, delay * 2)