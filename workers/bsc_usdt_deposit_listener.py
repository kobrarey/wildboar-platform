"""BSC USDT deposit listener (Stage 9.2.1 + backfill).

- Backfill on startup via eth_getLogs (missed deposits while worker was down)
- Persists cursor (block/log_index) in Postgres table worker_cursors
- Continues realtime tracking via WS subscription

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

import aiohttp
from aiohttp import resolver
import websockets
from websockets import exceptions as ws_exceptions
from eth_utils import to_checksum_address
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.db import SessionLocal
from app.models import UserWallet, WalletTransfer

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
CURSOR_NAME = "bsc_usdt_listener"

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


async def rpc_with_retries(coro_factory, label: str, attempts: int = 5, base_delay: int = 3):
    """
    Runs async RPC call with retries/backoff.
    If all retries fail -> raises the last exception.
    """
    delay = base_delay
    last_exc = None

    for attempt in range(1, attempts + 1):
        try:
            return await coro_factory()
        except Exception as e:
            last_exc = e
            logging.warning("%s failed (attempt %d/%d): %s", label, attempt, attempts, e)
            if attempt < attempts:
                await asyncio.sleep(delay)
                delay = min(30, delay * 2)

    raise last_exc


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


def get_cursor(db, name: str) -> tuple[int, int] | None:
    row = db.execute(
        sa_text("SELECT last_block, last_log_index FROM worker_cursors WHERE name=:name"),
        {"name": name},
    ).first()
    if not row:
        return None
    return int(row[0]), int(row[1])


def upsert_cursor(db, name: str, last_block: int, last_log_index: int) -> None:
    # monotonic upsert: never move cursor backwards
    db.execute(
        sa_text(
            """
            INSERT INTO worker_cursors (name, last_block, last_log_index)
            VALUES (:name, :b, :i)
            ON CONFLICT (name) DO UPDATE SET
              last_block = CASE
                WHEN EXCLUDED.last_block > worker_cursors.last_block THEN EXCLUDED.last_block
                WHEN EXCLUDED.last_block = worker_cursors.last_block
                     AND EXCLUDED.last_log_index > worker_cursors.last_log_index THEN EXCLUDED.last_block
                ELSE worker_cursors.last_block
              END,
              last_log_index = CASE
                WHEN EXCLUDED.last_block > worker_cursors.last_block THEN EXCLUDED.last_log_index
                WHEN EXCLUDED.last_block = worker_cursors.last_block
                     AND EXCLUDED.last_log_index > worker_cursors.last_log_index THEN EXCLUDED.last_log_index
                ELSE worker_cursors.last_log_index
              END,
              updated_at = now()
            """
        ),
        {"name": name, "b": int(last_block), "i": int(last_log_index)},
    )
    db.commit()


def db_upsert_cursor(name: str, last_block: int, last_log_index: int) -> None:
    db = SessionLocal()
    try:
        upsert_cursor(db, name, last_block, last_log_index)
    finally:
        db.close()


async def rpc_get_latest_block(session: aiohttp.ClientSession) -> int:
    if not settings.BSC_RPC_URL:
        raise RuntimeError("BSC_RPC_URL is not set")
    payload = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}
    async with session.post(settings.BSC_RPC_URL, json=payload) as resp:
        if resp.status != 200:
            txt = await resp.text()
            raise RuntimeError(f"eth_blockNumber HTTP {resp.status}: {txt[:200]}")
        data = await resp.json(content_type=None)
        result = data.get("result")
        if not result:
            raise RuntimeError(f"eth_blockNumber bad result: {data}")
        return int(result, 16)


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


async def rpc_get_logs(session: aiohttp.ClientSession, from_block: int, to_block: int, to_topics: list[str]) -> list[dict]:
    """eth_getLogs for USDT Transfer where topic[2] in to_topics."""
    if not settings.BSC_RPC_URL:
        raise RuntimeError("BSC_RPC_URL is not set")

    params = {
        "fromBlock": hex(int(from_block)),
        "toBlock": hex(int(to_block)),
        "address": settings.BSC_USDT_CONTRACT,
        "topics": [TRANSFER_TOPIC, None, to_topics],
    }
    payload = {"jsonrpc": "2.0", "method": "eth_getLogs", "params": [params], "id": 1}
    async with session.post(settings.BSC_RPC_URL, json=payload) as resp:
        if resp.status != 200:
            txt = await resp.text()
            raise RuntimeError(f"eth_getLogs HTTP {resp.status}: {txt[:200]}")
        data = await resp.json(content_type=None)
        if "error" in data and data["error"]:
            raise RuntimeError(f"eth_getLogs error: {data['error']}")
        result = data.get("result") or []
        if not isinstance(result, list):
            raise RuntimeError(f"eth_getLogs bad result: {data}")
        return result


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


async def handle_log(
    log: dict,
    wallet_map: dict[str, tuple[int, int]],
    rpc_session: aiohttp.ClientSession,
    block_time_cache: dict[int, datetime],
    *,
    update_cursor_flag: bool,
):
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

    if update_cursor_flag:
        await asyncio.to_thread(db_upsert_cursor, CURSOR_NAME, block_number, log_index)

    logging.info("Deposit detected: user_id=%s wallet_id=%s tx=%s li=%s amount=%s", user_id, wallet_id, tx_hash, log_index, str(amount))


async def subscribe_chunk(addresses: list[str], wallet_map: dict[str, tuple[int, int]]):
    if not settings.BSC_WS_URL:
        raise RuntimeError("BSC_WS_URL is not set")

    connector = aiohttp.TCPConnector(resolver=resolver.ThreadedResolver(), ttl_dns_cache=300)
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
                lg = (event.get("params") or {}).get("result")
                if isinstance(lg, dict):
                    await handle_log(lg, wallet_map, rpc_session, block_time_cache, update_cursor_flag=True)


async def backfill_on_start(wallet_map: dict[str, tuple[int, int]]):
    if not getattr(settings, "BSC_BACKFILL_ON_START", True):
        logging.info("Backfill on start disabled.")
        return

    if not settings.BSC_RPC_URL:
        logging.warning("BSC_RPC_URL is not set; cannot run backfill.")
        return

    addresses = list(wallet_map.keys())
    if not addresses:
        logging.info("No wallets in DB; skipping backfill.")
        return

    connector = aiohttp.TCPConnector(resolver=resolver.ThreadedResolver(), ttl_dns_cache=300)
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as rpc_session:
        latest_block = await rpc_with_retries(
            lambda: rpc_get_latest_block(rpc_session),
            "eth_blockNumber (backfill start)",
        )
        buffer_blocks = int(getattr(settings, "BSC_REORG_BUFFER_BLOCKS", 20))
        chunk_blocks = int(getattr(settings, "BSC_BACKFILL_CHUNK_BLOCKS", 2000))
        lookback_blocks = int(getattr(settings, "BSC_START_LOOKBACK_BLOCKS", 50000))

        to_block = max(0, int(latest_block) - buffer_blocks)

        db = SessionLocal()
        try:
            cur = get_cursor(db, CURSOR_NAME)
        finally:
            db.close()

        if cur:
            from_block = int(cur[0])
            logging.info("Backfill cursor found: from_block=%d", from_block)
        else:
            from_block = max(0, to_block - lookback_blocks)
            logging.info("Backfill cursor missing: from_block=%d (lookback=%d)", from_block, lookback_blocks)

        if from_block > to_block:
            logging.info("Backfill: nothing to do (from_block=%d > to_block=%d)", from_block, to_block)
            await asyncio.to_thread(db_upsert_cursor, CURSOR_NAME, to_block, 0)
            return

        block_time_cache: dict[int, datetime] = {}
        addr_chunks = _chunk(addresses, 1000)

        logging.info("Backfill start: [%d..%d] (latest=%d, buffer=%d)", from_block, to_block, latest_block, buffer_blocks)

        start = from_block
        while start <= to_block:
            end = min(to_block, start + chunk_blocks - 1)

            logs_all: list[dict] = []
            for a_chunk in addr_chunks:
                to_topics = [_encode_topic_address(a) for a in a_chunk]
                part = await rpc_with_retries(
                    lambda: rpc_get_logs(rpc_session, start, end, to_topics),
                    f"eth_getLogs {start}-{end}",
                )
                logs_all.extend(part)

            logs_all.sort(key=lambda x: (_hex_to_int(x.get("blockNumber", "0x0")), _hex_to_int(x.get("logIndex", "0x0"))))

            for lg in logs_all:
                await handle_log(lg, wallet_map, rpc_session, block_time_cache, update_cursor_flag=False)

            await asyncio.to_thread(db_upsert_cursor, CURSOR_NAME, end, 0)
            logging.info("Backfill chunk done: %d..%d (logs=%d)", start, end, len(logs_all))

            start = end + 1


async def main():
    reload_sec = int(getattr(settings, "BSC_WALLET_MAP_RELOAD_SEC", 60))

    wallet_map = load_wallet_map()
    last_addrs = set(wallet_map.keys())

    # one-time backfill on startup
    await backfill_on_start(wallet_map)

    while True:
        if not last_addrs:
            logging.warning("No BSC wallets found in DB. Waiting for wallets...")
            await asyncio.sleep(reload_sec)
            wallet_map = load_wallet_map()
            last_addrs = set(wallet_map.keys())
            continue

        addresses = list(last_addrs)
        chunks = _chunk(addresses, 1000)

        tasks = [asyncio.create_task(subscribe_chunk(ch, wallet_map)) for ch in chunks if ch]
        if not tasks:
            await asyncio.sleep(reload_sec)
            wallet_map = load_wallet_map()
            last_addrs = set(wallet_map.keys())
            continue

        try:
            while True:
                done, _pending = await asyncio.wait(tasks, timeout=reload_sec, return_when=asyncio.FIRST_EXCEPTION)

                for t in done:
                    exc = t.exception()
                    if exc:
                        raise exc

                new_map = load_wallet_map()
                new_addrs = set(new_map.keys())

                if new_addrs != last_addrs:
                    logging.info("Wallet list changed: %d -> %d addresses. Reloading subscriptions...", len(last_addrs), len(new_addrs))
                    for t in tasks:
                        t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)

                    wallet_map = new_map
                    last_addrs = new_addrs
                    break
        finally:
            pass


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