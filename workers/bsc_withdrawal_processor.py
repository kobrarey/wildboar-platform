import asyncio
import logging
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

from web3 import Web3
from sqlalchemy import text as sa_text

from app.config import settings
from app.db import SessionLocal
from app.models import WalletTransfer, UserWallet, User
from app.wallets import decrypt_private_key, create_bsc_wallet_for_user

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
log = logging.getLogger("bsc_withdrawal_processor")

CHAIN_ID_BSC = 56
TRANSFER_SELECTOR = "a9059cbb"     # transfer(address,uint256)
BALANCEOF_SELECTOR = "70a08231"    # balanceOf(address)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _pad32(hex_no_0x: str) -> str:
    return hex_no_0x.rjust(64, "0")


def erc20_transfer_data(to_addr: str, amount_wei: int) -> str:
    to_hex = to_addr.lower().replace("0x", "")
    amt_hex = hex(int(amount_wei))[2:]
    return "0x" + TRANSFER_SELECTOR + _pad32(to_hex) + _pad32(amt_hex)


def erc20_balance_of_data(addr: str) -> str:
    a = addr.lower().replace("0x", "")
    return "0x" + BALANCEOF_SELECTOR + _pad32(a)


def get_w3() -> Web3:
    if not settings.BSC_RPC_URL:
        raise RuntimeError("BSC_RPC_URL is not set")
    return Web3(Web3.HTTPProvider(settings.BSC_RPC_URL, request_kwargs={"timeout": 20}))


def pick_fee_wallet(compliance_status: str) -> tuple[str, str]:
    # per spec: if transfer.compliance_status == 'ok' -> OK fee wallet, else blocked fee wallet
    if (compliance_status or "ok") == "ok":
        return settings.FEE_WALLET_OK_ADDRESS, settings.FEE_WALLET_OK_PRIVATE_KEY
    return settings.FEE_WALLET_BLOCKED_ADDRESS, settings.FEE_WALLET_BLOCKED_PRIVATE_KEY


def tx_receipt_status(w3: Web3, tx_hash: str) -> int | None:
    """Return 1/0 if receipt exists, else None."""
    try:
        r = w3.eth.get_transaction_receipt(tx_hash)
        if r is None:
            return None
        return int(r.get("status", 0))
    except Exception:
        return None


def get_block_time(w3: Web3, block_number: int) -> datetime | None:
    try:
        b = w3.eth.get_block(block_number)
        return datetime.fromtimestamp(int(b["timestamp"]), tz=timezone.utc)
    except Exception:
        return None


def sign_and_send_raw(w3: Web3, priv_key_hex: str, tx: dict) -> str:
    signed = w3.eth.account.sign_transaction(tx, private_key=priv_key_hex)
    txh = w3.eth.send_raw_transaction(signed.raw_transaction)
    return txh.hex()


def usdt_contract_address() -> str:
    # Prefer explicit USDT_BSC_ADDRESS if set, fallback to BSC_USDT_CONTRACT from earlier stages
    addr = settings.USDT_BSC_ADDRESS or settings.BSC_USDT_CONTRACT
    if not addr:
        raise RuntimeError("USDT contract address is not set (USDT_BSC_ADDRESS/BSC_USDT_CONTRACT)")
    return Web3.to_checksum_address(addr)


def usdt_balance_onchain(w3: Web3, addr: str) -> Decimal:
    usdt = usdt_contract_address()
    data = erc20_balance_of_data(addr)
    res = w3.eth.call({"to": usdt, "data": data}, "latest")
    bal_int = int(res.hex(), 16)
    return Decimal(bal_int) / (Decimal(10) ** Decimal(settings.BSC_USDT_DECIMALS))


def calc_gas_need_wei(
    w3: Web3,
    user_addr: str,
    to_addr: str,
    fee_addr: str,
    amount_net: Decimal,
    fee_usdt: Decimal,
) -> int:
    gas_price = int(w3.eth.gas_price)
    fallback = int(settings.ERC20_TRANSFER_GAS_FALLBACK)
    buffer = Decimal(str(settings.WITHDRAW_GAS_BUFFER_MULT))

    usdt = usdt_contract_address()

    # amounts -> wei
    dec = Decimal(settings.BSC_USDT_DECIMALS)
    net_wei = int(amount_net * (Decimal(10) ** dec))
    fee_wei = int(fee_usdt * (Decimal(10) ** dec))

    # estimateGas for both ERC20 transfers; fallback if estimate fails
    total_units = 0
    for (dst, amt_wei) in [(to_addr, net_wei), (fee_addr, fee_wei)]:
        try:
            data = erc20_transfer_data(dst, amt_wei)
            est = w3.eth.estimate_gas(
                {
                    "from": user_addr,
                    "to": usdt,
                    "value": 0,
                    "data": data,
                }
            )
            total_units += int(est)
        except Exception:
            total_units += fallback

    need_wei = int(Decimal(total_units) * Decimal(gas_price) * buffer)
    return need_wei


def db_release_reserve(db, wallet_id: int, amount_gross: Decimal):
    db.execute(
        sa_text(
            """
            UPDATE user_wallets
            SET usdt_reserved = GREATEST(0, usdt_reserved - :amt)
            WHERE id = :wid
            """
        ),
        {"amt": str(amount_gross), "wid": int(wallet_id)},
    )


def db_mark_failed(db, tr: WalletTransfer, msg: str):
    tr.status = "failed"
    tr.error = (msg or "")[:800]


def db_set_processing_error(db, tr: WalletTransfer, msg: str):
    tr.error = (msg or "")[:800]


def db_mark_success(db, tr: WalletTransfer, tx_time: datetime | None, payout_block: int | None):
    tr.status = "success"
    tr.confirmed_at = utcnow()
    if tx_time:
        tr.tx_time = tx_time
    if payout_block is not None:
        tr.block_number = int(payout_block)


def rotate_blocked_wallet_if_empty(db, tr: WalletTransfer, w3: Web3):
    # only for blocked/pending_check case: compliance_status != 'ok'
    if (tr.compliance_status or "ok") == "ok":
        return

    wallet = db.query(UserWallet).filter(UserWallet.id == tr.wallet_id).first()
    user = db.query(User).filter(User.id == tr.user_id).first()
    if not wallet or not user:
        return

    # on-chain USDT balance <= 0.01 -> rotate
    bal = usdt_balance_onchain(w3, wallet.address)
    if bal > Decimal("0.01"):
        return

    wallet.is_active = False
    wallet.archived_at = utcnow()

    # create new active wallet
    create_bsc_wallet_for_user(db, user, commit=False, force_new=True)

    # reset user compliance (per acceptance)
    user.compliance_status = "ok"
    user.compliance_reason = None
    user.compliance_updated_at = utcnow()


def process_one(tr_id: int):
    w3 = get_w3()

    db = SessionLocal()
    try:
        tr = (
            db.query(WalletTransfer)
            .filter(WalletTransfer.id == tr_id)
            .with_for_update()
            .first()
        )
        if not tr or tr.type != "withdraw" or tr.status != "processing":
            return

        wallet = (
            db.query(UserWallet)
            .filter(UserWallet.id == tr.wallet_id)
            .with_for_update()
            .first()
        )
        if not wallet:
            db_mark_failed(db, tr, "wallet_not_found")
            db.commit()
            return

        # fee wallet selection
        fee_addr, fee_priv = pick_fee_wallet(tr.compliance_status or "ok")
        if not fee_addr or not fee_priv:
            db_mark_failed(db, tr, "fee_wallet_not_configured")
            # release reserve because we cannot proceed
            if tr.amount_gross:
                db_release_reserve(db, wallet.id, Decimal(tr.amount_gross))
            db.commit()
            return

        user_addr = Web3.to_checksum_address(wallet.address)
        to_addr = Web3.to_checksum_address(tr.to_address)
        fee_addr = Web3.to_checksum_address(fee_addr)

        # amounts
        amount_gross = Decimal(tr.amount_gross)
        fee_usdt = Decimal(tr.fee_usdt or settings.WITHDRAW_FEE_USDT)
        amount_net = Decimal(tr.amount)

        # ---------- Step 1: gas topup if needed ----------
        need_wei = calc_gas_need_wei(w3, user_addr, to_addr, fee_addr, amount_net, fee_usdt)

        max_bnb = Decimal(str(settings.WITHDRAW_GAS_MAX_BNB))
        max_wei = int(max_bnb * (Decimal(10) ** 18))
        if need_wei > max_wei:
            db_mark_failed(db, tr, "gas_need_exceeds_withdraw_gas_max_bnb")
            db_release_reserve(db, wallet.id, amount_gross)
            db.commit()
            return

        # if gas tx already exists -> check it
        if tr.gas_tx_hash:
            st = tx_receipt_status(w3, tr.gas_tx_hash)
            if st is None:
                # pending, wait next cycle
                db.commit()
                return
            if st != 1:
                db_mark_failed(db, tr, "gas_topup_failed")
                db_release_reserve(db, wallet.id, amount_gross)
                db.commit()
                return
        else:
            # check user BNB
            bal_wei = int(w3.eth.get_balance(user_addr))
            if bal_wei < need_wei:
                topup_wei = need_wei - bal_wei
                # (need_wei already <= max_wei)
                if topup_wei <= 0:
                    topup_wei = 0

                gas_price = int(w3.eth.gas_price)
                nonce_fee = w3.eth.get_transaction_count(fee_addr, "pending")
                tx = {
                    "chainId": CHAIN_ID_BSC,
                    "to": user_addr,
                    "value": int(topup_wei),
                    "gas": 21000,
                    "gasPrice": gas_price,
                    "nonce": nonce_fee,
                }
                try:
                    txh = sign_and_send_raw(w3, fee_priv, tx)
                    tr.gas_tx_hash = txh
                    tr.error = None
                    db.commit()
                    return
                except Exception as e:
                    db_set_processing_error(db, tr, f"gas_tx_send_error: {e}")
                    db.commit()
                    return

        # ---------- Step 2: payout (net) ----------
        if tr.tx_hash:
            st = tx_receipt_status(w3, tr.tx_hash)
            if st is None:
                db.commit()
                return
            if st != 1:
                db_mark_failed(db, tr, "payout_tx_failed")
                db_release_reserve(db, wallet.id, amount_gross)
                db.commit()
                return
        else:
            user_priv = decrypt_private_key(wallet.encrypted_private_key)

            usdt = usdt_contract_address()
            dec = Decimal(settings.BSC_USDT_DECIMALS)
            net_wei = int(amount_net * (Decimal(10) ** dec))

            gas_price = int(w3.eth.gas_price)
            nonce_user = w3.eth.get_transaction_count(user_addr, "pending")
            data = erc20_transfer_data(to_addr, net_wei)

            # gas limit (estimate or fallback)
            try:
                gas_limit = int(
                    w3.eth.estimate_gas({"from": user_addr, "to": usdt, "value": 0, "data": data})
                )
            except Exception:
                gas_limit = int(settings.ERC20_TRANSFER_GAS_FALLBACK)

            tx = {
                "chainId": CHAIN_ID_BSC,
                "to": usdt,
                "value": 0,
                "gas": gas_limit,
                "gasPrice": gas_price,
                "nonce": nonce_user,
                "data": data,
            }
            try:
                txh = sign_and_send_raw(w3, user_priv, tx)
                tr.tx_hash = txh
                tr.error = None
                db.commit()
                return
            except Exception as e:
                db_set_processing_error(db, tr, f"payout_tx_send_error: {e}")
                db.commit()
                return

        # ---------- Step 3: fee tx (1 USDT) ----------
        if tr.fee_tx_hash:
            st = tx_receipt_status(w3, tr.fee_tx_hash)
            if st is None:
                db.commit()
                return
            if st != 1:
                db_mark_failed(db, tr, "fee_tx_failed")
                db_release_reserve(db, wallet.id, amount_gross)
                db.commit()
                return
        else:
            user_priv = decrypt_private_key(wallet.encrypted_private_key)

            usdt = usdt_contract_address()
            dec = Decimal(settings.BSC_USDT_DECIMALS)
            fee_wei = int(fee_usdt * (Decimal(10) ** dec))

            gas_price = int(w3.eth.gas_price)
            nonce_user = w3.eth.get_transaction_count(user_addr, "pending")
            data = erc20_transfer_data(fee_addr, fee_wei)

            try:
                gas_limit = int(
                    w3.eth.estimate_gas({"from": user_addr, "to": usdt, "value": 0, "data": data})
                )
            except Exception:
                gas_limit = int(settings.ERC20_TRANSFER_GAS_FALLBACK)

            tx = {
                "chainId": CHAIN_ID_BSC,
                "to": usdt,
                "value": 0,
                "gas": gas_limit,
                "gasPrice": gas_price,
                "nonce": nonce_user,
                "data": data,
            }
            try:
                txh = sign_and_send_raw(w3, user_priv, tx)
                tr.fee_tx_hash = txh
                tr.error = None
                db.commit()
                return
            except Exception as e:
                db_set_processing_error(db, tr, f"fee_tx_send_error: {e}")
                db.commit()
                return

        # ---------- Step 4: finalize ----------
        # payout receipt -> tx_time
        payout_receipt = w3.eth.get_transaction_receipt(tr.tx_hash)
        payout_block = int(payout_receipt["blockNumber"])
        tx_time = get_block_time(w3, payout_block)

        db_mark_success(db, tr, tx_time, payout_block)
        db_release_reserve(db, wallet.id, amount_gross)

        # optional: update wallet usdt_balance fast
        try:
            wallet.usdt_balance = usdt_balance_onchain(w3, wallet.address)
            wallet.usdt_balance_updated_at = utcnow()
            wallet.usdt_balance_block = payout_block
        except Exception:
            pass

        db.commit()

        # ---------- Step 5: rotate blocked wallet after success ----------
        rotate_blocked_wallet_if_empty(db, tr, w3)
        db.commit()

        log.info("Withdraw success: transfer_id=%s", tr.id)

    except Exception as e:
        log.exception("Withdraw worker crashed on transfer %s: %s", tr_id, e)
    finally:
        db.close()


def load_processing_ids(limit: int = 50) -> list[int]:
    db = SessionLocal()
    try:
        rows = (
            db.query(WalletTransfer.id)
            .filter(WalletTransfer.type == "withdraw", WalletTransfer.status == "processing")
            .order_by(WalletTransfer.detected_at.asc())
            .limit(limit)
            .all()
        )
        return [int(r[0]) for r in rows]
    finally:
        db.close()


async def main_loop():
    while True:
        try:
            ids = await asyncio.to_thread(load_processing_ids, 50)
            for tr_id in ids:
                await asyncio.to_thread(process_one, tr_id)
        except Exception as e:
            log.error("Withdrawal main loop error: %s", e)

        await asyncio.sleep(5)


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
            log.error("Worker crashed: %s", e)

        log.info("Restarting in %d seconds...", delay)
        time.sleep(delay)
        delay = min(max_delay, delay * 2)