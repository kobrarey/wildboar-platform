"""Daily fee-wallet USDT -> BNB swap worker.

Purpose:
- Fee wallets receive 1 USDT withdrawal fees.
- Once per UTC day, swap accumulated USDT into native BNB for future gas usage.
- Supports two fee wallets:
  - wallet_type="ok"
  - wallet_type="blocked"

Run:
    python -m workers.fee_wallet_swap_worker

Important:
- This worker sends real on-chain transactions if env is configured and enabled.
- Do not run on production without verifying env values and using a small first real test.
"""

import asyncio
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN
from typing import Any

import requests
from sqlalchemy.exc import IntegrityError
from web3 import Web3

from app.config import settings
from app.db import SessionLocal
from app.models import FeeWalletSwap

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("fee_wallet_swap_worker")


ERC20_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [
            {"name": "owner", "type": "address"},
            {"name": "spender", "type": "address"},
        ],
        "name": "allowance",
        "outputs": [{"name": "", "type": "uint256"}],
        "type": "function",
    },
    {
        "constant": False,
        "inputs": [
            {"name": "spender", "type": "address"},
            {"name": "amount", "type": "uint256"},
        ],
        "name": "approve",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
]

PANCAKE_ROUTER_V2_ABI = [
    {
        "name": "getAmountsOut",
        "type": "function",
        "stateMutability": "view",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "path", "type": "address[]"},
        ],
        "outputs": [{"name": "amounts", "type": "uint256[]"}],
    },
    {
        "name": "swapExactTokensForETHSupportingFeeOnTransferTokens",
        "type": "function",
        "stateMutability": "nonpayable",
        "inputs": [
            {"name": "amountIn", "type": "uint256"},
            {"name": "amountOutMin", "type": "uint256"},
            {"name": "path", "type": "address[]"},
            {"name": "to", "type": "address"},
            {"name": "deadline", "type": "uint256"},
        ],
        "outputs": [],
    },
]

MIN_BNB_FOR_GAS = Decimal("0.0003")
APPROVE_GAS_FALLBACK = 70000
SWAP_GAS_FALLBACK = 350000


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utc_day_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or utcnow()
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    return start, start + timedelta(days=1)


def dec_to_int_units(value: Decimal, decimals: int) -> int:
    scale = Decimal(10) ** Decimal(decimals)
    return int((value * scale).to_integral_value(rounding=ROUND_DOWN))


def int_units_to_dec(value: int, decimals: int) -> Decimal:
    return Decimal(int(value)) / (Decimal(10) ** Decimal(decimals))


def wei_to_bnb(value: int) -> Decimal:
    return Decimal(int(value)) / (Decimal(10) ** Decimal(18))


def bnb_to_wei(value: Decimal) -> int:
    return dec_to_int_units(value, 18)


def short_error(e: Exception | str, limit: int = 500) -> str:
    msg = str(e)
    return msg[:limit]


def get_w3() -> Web3:
    if not settings.BSC_RPC_URL:
        raise RuntimeError("BSC_RPC_URL is not set")

    w3 = Web3(Web3.HTTPProvider(settings.BSC_RPC_URL, request_kwargs={"timeout": 30}))
    if not w3.is_connected():
        raise RuntimeError("Web3 provider is not connected")

    return w3


def checksum(addr: str) -> str:
    return Web3.to_checksum_address((addr or "").strip())


def send_telegram(text: str) -> None:
    if not settings.TELEGRAM_BOT_TOKEN or not settings.TELEGRAM_CHAT_ID:
        log.info("Telegram not configured. Skip message: %s", text)
        return

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            json={"chat_id": settings.TELEGRAM_CHAT_ID, "text": text},
            timeout=10,
        )
    except Exception as e:
        log.warning("Telegram alert failed: %s", e)


def record_swap(
    *,
    wallet_type: str,
    wallet_address: str,
    amount_in_usdt: Decimal,
    status: str,
    amount_out_bnb: Decimal | None = None,
    tx_hash: str | None = None,
    error: str | None = None,
) -> None:
    db = SessionLocal()
    try:
        row = FeeWalletSwap(
            wallet_type=wallet_type,
            wallet_address=wallet_address or "",
            token_in="USDT",
            token_out="BNB",
            amount_in_usdt=amount_in_usdt,
            amount_out_bnb=amount_out_bnb,
            tx_hash=tx_hash,
            status=status,
            error=error,
            executed_at=utcnow() if status in {"success", "failed", "skipped"} else None,
        )
        db.add(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        log.warning("Daily success guard blocked duplicate success for wallet_type=%s", wallet_type)
    finally:
        db.close()


def has_success_today(wallet_type: str) -> bool:
    start, end = utc_day_bounds()

    db = SessionLocal()
    try:
        row = (
            db.query(FeeWalletSwap.id)
            .filter(
                FeeWalletSwap.wallet_type == wallet_type,
                FeeWalletSwap.status == "success",
                FeeWalletSwap.created_at >= start,
                FeeWalletSwap.created_at < end,
            )
            .first()
        )
        return row is not None
    finally:
        db.close()


def should_run_now() -> bool:
    hour = int(settings.FEE_WALLET_SWAP_DAILY_HOUR_UTC)
    hour = max(0, min(23, hour))
    return utcnow().hour >= hour


def validate_common_env() -> None:
    missing = []
    if not settings.PANCAKE_ROUTER_V2:
        missing.append("PANCAKE_ROUTER_V2")
    if not settings.WBNB_ADDRESS:
        missing.append("WBNB_ADDRESS")
    if not settings.USDT_BSC_ADDRESS:
        missing.append("USDT_BSC_ADDRESS")
    if not settings.BSC_RPC_URL:
        missing.append("BSC_RPC_URL")

    if missing:
        raise RuntimeError("Missing env vars: " + ", ".join(missing))


def get_wallet_config(wallet_type: str) -> tuple[str, str]:
    if wallet_type == "ok":
        return settings.FEE_WALLET_OK_ADDRESS, settings.FEE_WALLET_OK_PRIVATE_KEY

    if wallet_type == "blocked":
        return settings.FEE_WALLET_BLOCKED_ADDRESS, settings.FEE_WALLET_BLOCKED_PRIVATE_KEY

    raise RuntimeError(f"Unsupported wallet_type: {wallet_type}")


def build_tx_params(w3: Web3, from_address: str, nonce: int) -> dict[str, Any]:
    return {
        "from": from_address,
        "nonce": nonce,
        "chainId": int(w3.eth.chain_id),
        "gasPrice": int(w3.eth.gas_price),
    }


def gas_cost_bnb(receipt: Any) -> Decimal:
    gas_used = int(receipt.get("gasUsed", 0))
    effective_gas_price = receipt.get("effectiveGasPrice", None)

    if effective_gas_price is None:
        effective_gas_price = receipt.get("gasPrice", 0)

    return wei_to_bnb(gas_used * int(effective_gas_price or 0))


def sign_send_wait(
    *,
    w3: Web3,
    private_key: str,
    tx: dict[str, Any],
    label: str,
    timeout: int = 300,
) -> Any:
    signed = w3.eth.account.sign_transaction(tx, private_key=private_key)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    tx_hash = w3.eth.send_raw_transaction(raw)
    log.info("%s tx sent: %s", label, tx_hash.hex())

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)
    if int(receipt.get("status", 0)) != 1:
        raise RuntimeError(f"{label} tx failed: {tx_hash.hex()}")

    return receipt


def build_contract_tx_with_gas(
    *,
    w3: Web3,
    fn: Any,
    from_address: str,
    nonce: int,
    fallback_gas: int,
) -> dict[str, Any]:
    base = build_tx_params(w3, from_address, nonce)

    try:
        gas_estimate = fn.estimate_gas({"from": from_address})
        gas = int(Decimal(gas_estimate) * Decimal("1.2"))
    except Exception as e:
        log.warning("Gas estimate failed, using fallback gas=%s: %s", fallback_gas, e)
        gas = fallback_gas

    base["gas"] = gas
    return fn.build_transaction(base)


def process_fee_wallet(wallet_type: str) -> None:
    wallet_address_raw = ""
    amount_to_swap_dec = Decimal("0")

    try:
        validate_common_env()

        wallet_address_raw, private_key = get_wallet_config(wallet_type)
        if not wallet_address_raw or not private_key:
            raise RuntimeError(f"Missing fee wallet env for wallet_type={wallet_type}")

        wallet_address = checksum(wallet_address_raw)
        router_address = checksum(settings.PANCAKE_ROUTER_V2)
        usdt_address = checksum(settings.USDT_BSC_ADDRESS)
        wbnb_address = checksum(settings.WBNB_ADDRESS)

        w3 = get_w3()
        account = w3.eth.account.from_key(private_key)

        if checksum(account.address) != wallet_address:
            raise RuntimeError(
                f"Private key address mismatch for wallet_type={wallet_type}: "
                f"env_address={wallet_address}, key_address={checksum(account.address)}"
            )

        if has_success_today(wallet_type):
            log.info("wallet_type=%s already has successful swap today. Skip.", wallet_type)
            record_swap(
                wallet_type=wallet_type,
                wallet_address=wallet_address,
                amount_in_usdt=Decimal("0"),
                status="skipped",
                error="successful swap already exists for current UTC day",
            )
            return

        usdt = w3.eth.contract(address=usdt_address, abi=ERC20_ABI)
        router = w3.eth.contract(address=router_address, abi=PANCAKE_ROUTER_V2_ABI)

        bnb_before_wei = int(w3.eth.get_balance(wallet_address))
        bnb_before = wei_to_bnb(bnb_before_wei)

        if bnb_before < MIN_BNB_FOR_GAS:
            raise RuntimeError(
                f"Insufficient BNB for gas: balance={bnb_before}, required_min={MIN_BNB_FOR_GAS}"
            )

        usdt_balance_units = int(usdt.functions.balanceOf(wallet_address).call())
        usdt_balance_dec = int_units_to_dec(usdt_balance_units, int(settings.BSC_USDT_DECIMALS))

        min_usdt = Decimal(settings.FEE_WALLET_SWAP_MIN_USDT)
        if usdt_balance_dec < min_usdt:
            log.info(
                "wallet_type=%s low USDT balance. balance=%s min=%s. Skip.",
                wallet_type,
                usdt_balance_dec,
                min_usdt,
            )
            record_swap(
                wallet_type=wallet_type,
                wallet_address=wallet_address,
                amount_in_usdt=usdt_balance_dec,
                status="skipped",
                error=f"USDT balance below threshold: balance={usdt_balance_dec}, min={min_usdt}",
            )
            return

        # Fee wallet USDT is accumulated fee inventory. Native gas reserve is BNB,
        # so USDT can be swapped when it reaches the configured threshold.
        amount_to_swap_units = usdt_balance_units
        amount_to_swap_dec = usdt_balance_dec

        path = [usdt_address, wbnb_address]
        quoted_amounts = router.functions.getAmountsOut(amount_to_swap_units, path).call()
        quoted_out_wei = int(quoted_amounts[-1])

        slippage_bps = int(settings.FEE_WALLET_SWAP_SLIPPAGE_BPS)
        slippage_bps = max(0, min(5000, slippage_bps))
        amount_out_min_wei = int(Decimal(quoted_out_wei) * Decimal(10000 - slippage_bps) / Decimal(10000))

        if amount_out_min_wei <= 0:
            raise RuntimeError("Quote returned zero amountOutMin")

        allowance = int(usdt.functions.allowance(wallet_address, router_address).call())

        total_gas_spent = Decimal("0")
        nonce = int(w3.eth.get_transaction_count(wallet_address))

        if allowance < amount_to_swap_units:
            approve_fn = usdt.functions.approve(router_address, amount_to_swap_units)
            approve_tx = build_contract_tx_with_gas(
                w3=w3,
                fn=approve_fn,
                from_address=wallet_address,
                nonce=nonce,
                fallback_gas=APPROVE_GAS_FALLBACK,
            )

            approve_receipt = sign_send_wait(
                w3=w3,
                private_key=private_key,
                tx=approve_tx,
                label=f"approve {wallet_type}",
            )
            total_gas_spent += gas_cost_bnb(approve_receipt)
            nonce += 1

        deadline = int(time.time()) + 20 * 60

        swap_fn = router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
            amount_to_swap_units,
            amount_out_min_wei,
            path,
            wallet_address,
            deadline,
        )

        swap_tx = build_contract_tx_with_gas(
            w3=w3,
            fn=swap_fn,
            from_address=wallet_address,
            nonce=nonce,
            fallback_gas=SWAP_GAS_FALLBACK,
        )

        swap_receipt = sign_send_wait(
            w3=w3,
            private_key=private_key,
            tx=swap_tx,
            label=f"swap {wallet_type}",
        )
        total_gas_spent += gas_cost_bnb(swap_receipt)

        bnb_after_wei = int(w3.eth.get_balance(wallet_address))
        bnb_after = wei_to_bnb(bnb_after_wei)

        # Native BNB received = final balance - initial balance + gas spent.
        amount_out_bnb = (bnb_after - bnb_before + total_gas_spent).quantize(
            Decimal("0.000000000000000001"),
            rounding=ROUND_DOWN,
        )

        tx_hash = swap_receipt.get("transactionHash").hex()

        record_swap(
            wallet_type=wallet_type,
            wallet_address=wallet_address,
            amount_in_usdt=amount_to_swap_dec,
            amount_out_bnb=amount_out_bnb,
            tx_hash=tx_hash,
            status="success",
            error=None,
        )

        msg = (
            f"✅ Fee wallet swap success ({wallet_type})\n"
            f"USDT in: {amount_to_swap_dec}\n"
            f"BNB out≈ {amount_out_bnb}\n"
            f"Tx: {tx_hash}"
        )
        log.info(msg)
        send_telegram(msg)

    except Exception as e:
        err = short_error(e)
        wallet_address = wallet_address_raw or ""

        log.error("Swap failed for wallet_type=%s: %s", wallet_type, err)

        record_swap(
            wallet_type=wallet_type,
            wallet_address=wallet_address,
            amount_in_usdt=amount_to_swap_dec,
            status="failed",
            error=err,
        )

        send_telegram(
            f"❌ Fee wallet swap failed ({wallet_type})\n"
            f"Address: {wallet_address or 'missing'}\n"
            f"Error: {err}"
        )


def run_once() -> None:
    if not bool(settings.FEE_WALLET_SWAP_ENABLED):
        log.info("FEE_WALLET_SWAP_ENABLED=False. Worker is disabled.")
        return

    if not should_run_now():
        log.info(
            "Not swap hour yet. Current UTC hour=%s, daily_hour=%s",
            utcnow().hour,
            settings.FEE_WALLET_SWAP_DAILY_HOUR_UTC,
        )
        return

    for wallet_type in ("ok", "blocked"):
        process_fee_wallet(wallet_type)


async def main_loop() -> None:
    interval = int(settings.FEE_WALLET_SWAP_INTERVAL_SEC)

    while True:
        try:
            await asyncio.to_thread(run_once)
        except Exception as e:
            log.exception("Worker loop error: %s", e)

        await asyncio.sleep(interval)


if __name__ == "__main__":
    delay = 5
    max_delay = 60

    while True:
        try:
            asyncio.run(main_loop())
            delay = 5
        except KeyboardInterrupt:
            log.info("Stopped by user.")
            break
        except Exception as e:
            log.exception("Worker crashed: %s", e)

        log.info("Restarting in %d seconds...", delay)
        time.sleep(delay)
        delay = min(max_delay, delay * 2)