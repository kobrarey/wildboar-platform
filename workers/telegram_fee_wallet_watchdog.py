import asyncio
import logging
import sys
from decimal import Decimal

import requests
from web3 import Web3

from app.config import settings

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("telegram_fee_wallet_watchdog")

ABI_ROUTER = [
    {
        "name": "getAmountsOut",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "path", "type": "address[]"},
        ],
        "outputs": [{"name": "", "type": "uint256[]"}],
    }
]


def get_w3() -> Web3:
    if not settings.BSC_RPC_URL:
        raise RuntimeError("BSC_RPC_URL is not set")
    return Web3(Web3.HTTPProvider(settings.BSC_RPC_URL, request_kwargs={"timeout": 20}))


def send_telegram(text: str):
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        log.info("Telegram not configured. Skip message: %s", text)
        return

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    requests.post(
        url,
        json={"chat_id": settings.TELEGRAM_CHAT_ID, "text": text},
        timeout=10,
    )


def bnb_balance(w3: Web3, addr: str) -> Decimal:
    wei = w3.eth.get_balance(Web3.to_checksum_address(addr))
    return Decimal(wei) / (Decimal(10) ** 18)


def bnb_price_usdt(w3: Web3) -> Decimal:
    if not settings.PANCAKE_ROUTER_V2 or not settings.WBNB_ADDRESS or not settings.USDT_BSC_ADDRESS:
        raise RuntimeError("Pancake quote config missing")

    router = w3.eth.contract(
        address=Web3.to_checksum_address(settings.PANCAKE_ROUTER_V2),
        abi=ABI_ROUTER,
    )

    one_bnb = int(Decimal(10) ** 18)
    path = [
        Web3.to_checksum_address(settings.WBNB_ADDRESS),
        Web3.to_checksum_address(settings.USDT_BSC_ADDRESS),
    ]
    amounts = router.functions.getAmountsOut(one_bnb, path).call()
    out = int(amounts[-1])
    return Decimal(out) / (Decimal(10) ** Decimal(settings.BSC_USDT_DECIMALS))


def run_check_once():
    w3 = get_w3()
    price = bnb_price_usdt(w3)
    thr = Decimal(str(settings.BNB_ALERT_THRESHOLD_USD))

    wallets = [
        ("ok", settings.FEE_WALLET_OK_ADDRESS),
        ("blocked", settings.FEE_WALLET_BLOCKED_ADDRESS),
    ]

    for label, addr in wallets:
        if not addr:
            continue

        bal_bnb = bnb_balance(w3, addr)
        usd_val = bal_bnb * price
        log.info("Fee wallet %s: BNB=%s USD≈%s", label, bal_bnb, usd_val)

        if usd_val < thr:
            send_telegram(
                f"⚠️ Fee wallet ({label}) BNB balance below ${thr} (≈ ${usd_val:.2f}).\n"
                f"Address: {addr}"
            )


async def main_loop():
    normal_sleep = int(settings.TELEGRAM_CHECK_SEC)
    retry_sleep = 15
    retry_sleep_max = 60

    while True:
        try:
            await asyncio.to_thread(run_check_once)
            retry_sleep = 15
            await asyncio.sleep(normal_sleep)

        except Exception as e:
            log.error("Watchdog error: %s", e)
            log.info("Retrying in %s seconds...", retry_sleep)
            await asyncio.sleep(retry_sleep)
            retry_sleep = min(retry_sleep_max, retry_sleep * 2)


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        log.info("Stopped by user.")