from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session
from web3 import Web3

from app.config import settings
from app.models import Fund, FundSettlementBatch, FundSettlementTransfer, FundWallet
from app.settlement.batch_service import get_cutoff_ts, get_default_settlement_date
from app.settlement.statuses import (
    BATCH_STATUS_CREATED,
    TRANSFER_STATUS_FAILED,
    TRANSFER_STATUS_PENDING,
    TRANSFER_STATUS_SENT,
    TRANSFER_TYPE_SETTLEMENT_WALLET_GAS_TOPUP,
)
from app.telegram import send_telegram_message


log = logging.getLogger("settlement.gas_service")

ZERO = Decimal("0")
WEI_PER_BNB = Decimal("1000000000000000000")

PANCAKE_ROUTER_MIN_ABI = [
    {
        "inputs": [
            {"internalType": "uint256", "name": "amountIn", "type": "uint256"},
            {"internalType": "address[]", "name": "path", "type": "address[]"},
        ],
        "name": "getAmountsOut",
        "outputs": [
            {"internalType": "uint256[]", "name": "amounts", "type": "uint256[]"}
        ],
        "stateMutability": "view",
        "type": "function",
    }
]


class SettlementGasError(RuntimeError):
    pass


@dataclass(frozen=True)
class SettlementWalletGasResult:
    fund_code: str
    fund_id: int
    wallet_address: str
    batch_id: int
    bnb_balance: Decimal
    target_bnb: Decimal
    min_operational_bnb: Decimal
    amount_sent_bnb: Decimal
    status: str
    tx_hash: str | None
    message: str


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dec(value: Any) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _normalize_private_key(private_key: str) -> str:
    value = (private_key or "").strip()
    if not value:
        raise SettlementGasError("FEE_WALLET_OK_PRIVATE_KEY is empty")
    if not value.startswith("0x"):
        value = "0x" + value
    return value


def get_web3() -> Web3:
    if not settings.BSC_RPC_URL:
        raise SettlementGasError("BSC_RPC_URL is not configured")

    w3 = Web3(Web3.HTTPProvider(settings.BSC_RPC_URL))
    if not w3.is_connected():
        raise SettlementGasError("Cannot connect to BSC RPC")

    return w3


def _checksum(w3: Web3, address: str) -> str:
    if not address:
        raise SettlementGasError("Address is empty")
    return w3.to_checksum_address(address)


def get_bnb_balance(w3: Web3, address: str) -> Decimal:
    checksum = _checksum(w3, address)
    wei = w3.eth.get_balance(checksum)
    return Decimal(int(wei)) / WEI_PER_BNB


def get_bnb_usd_price(w3: Web3) -> Decimal:
    """
    Quote 1 BNB -> USDT through Pancake router.

    Returns USDT per 1 BNB.
    """
    if not settings.PANCAKE_ROUTER_V2:
        raise SettlementGasError("PANCAKE_ROUTER_V2 is not configured")
    if not settings.WBNB_ADDRESS:
        raise SettlementGasError("WBNB_ADDRESS is not configured")
    if not settings.USDT_BSC_ADDRESS:
        raise SettlementGasError("USDT_BSC_ADDRESS is not configured")

    router = w3.eth.contract(
        address=_checksum(w3, settings.PANCAKE_ROUTER_V2),
        abi=PANCAKE_ROUTER_MIN_ABI,
    )

    one_bnb_wei = int(WEI_PER_BNB)
    amounts = router.functions.getAmountsOut(
        one_bnb_wei,
        [
            _checksum(w3, settings.WBNB_ADDRESS),
            _checksum(w3, settings.USDT_BSC_ADDRESS),
        ],
    ).call()

    usdt_raw = Decimal(int(amounts[-1]))
    usdt_decimals = Decimal(10) ** int(settings.BSC_USDT_DECIMALS)
    price = usdt_raw / usdt_decimals

    if price <= 0:
        raise SettlementGasError("Invalid BNB/USDT quote")

    return price


def usd_to_bnb(usd_amount: Decimal, bnb_usd_price: Decimal) -> Decimal:
    if bnb_usd_price <= 0:
        raise SettlementGasError("Invalid BNB USD price")
    return usd_amount / bnb_usd_price


def estimate_min_operational_bnb(w3: Web3) -> Decimal:
    """
    Conservative minimum BNB for future settlement operations.

    Stage 21 does not execute final settlement, so this is a practical placeholder:
    2 ERC20 transfers * fallback gas * current gas price * buffer.
    """
    gas_price_wei = Decimal(int(w3.eth.gas_price))
    fallback_gas = Decimal(int(settings.ERC20_TRANSFER_GAS_FALLBACK))
    buffer_mult = Decimal(settings.SETTLEMENT_WALLET_MIN_GAS_BUFFER_MULT)

    estimated_wei = Decimal("2") * fallback_gas * gas_price_wei * buffer_mult
    return estimated_wei / WEI_PER_BNB


def send_native_bnb(
    w3: Web3,
    *,
    from_private_key: str,
    from_address: str,
    to_address: str,
    amount_bnb: Decimal,
) -> str:
    if amount_bnb <= 0:
        raise SettlementGasError(f"Invalid BNB amount: {amount_bnb}")

    private_key = _normalize_private_key(from_private_key)
    from_checksum = _checksum(w3, from_address)
    to_checksum = _checksum(w3, to_address)

    value_wei = int(amount_bnb * WEI_PER_BNB)
    gas_price = int(w3.eth.gas_price)
    nonce = w3.eth.get_transaction_count(from_checksum)
    chain_id = int(w3.eth.chain_id)

    tx = {
        "to": to_checksum,
        "value": value_wei,
        "gas": 21000,
        "gasPrice": gas_price,
        "nonce": nonce,
        "chainId": chain_id,
    }

    signed = w3.eth.account.sign_transaction(tx, private_key)
    raw_tx = getattr(signed, "rawTransaction", None) or getattr(signed, "raw_transaction")
    tx_hash = w3.eth.send_raw_transaction(raw_tx)

    return w3.to_hex(tx_hash)


def _get_active_settlement_wallets(db: Session) -> list[tuple[Fund, FundWallet]]:
    return (
        db.query(Fund, FundWallet)
        .join(FundWallet, FundWallet.fund_id == Fund.id)
        .filter(
            Fund.is_active == True,
            FundWallet.blockchain == "BSC",
            FundWallet.wallet_type == "settlement",
            FundWallet.is_active == True,
        )
        .order_by(Fund.sort_order.asc(), Fund.id.asc())
        .all()
    )


def _get_or_create_shell_batch(
    db: Session,
    *,
    fund_id: int,
    settlement_date: date,
) -> FundSettlementBatch:
    batch = (
        db.query(FundSettlementBatch)
        .filter(
            FundSettlementBatch.fund_id == fund_id,
            FundSettlementBatch.settlement_date == settlement_date,
        )
        .with_for_update()
        .first()
    )

    if batch is not None:
        return batch

    now = utcnow()
    cutoff_ts = get_cutoff_ts(settlement_date)

    batch = FundSettlementBatch(
        fund_id=fund_id,
        settlement_date=settlement_date,
        cutoff_ts=cutoff_ts,
        settlement_ts=cutoff_ts,
        status=BATCH_STATUS_CREATED,
        total_buy_usdt=ZERO,
        total_redeem_shares=ZERO,
        total_redeem_usdt=ZERO,
        net_cash_usdt=ZERO,
        planned_shares_to_issue=ZERO,
        planned_shares_to_redeem=ZERO,
        planned_net_shares_change=ZERO,
        created_at=now,
        updated_at=now,
    )
    db.add(batch)
    db.flush()
    return batch


def _find_existing_topup_transfer(
    db: Session,
    *,
    batch_id: int,
    fund_id: int,
    to_address: str,
) -> FundSettlementTransfer | None:
    return (
        db.query(FundSettlementTransfer)
        .filter(
            FundSettlementTransfer.batch_id == batch_id,
            FundSettlementTransfer.fund_id == fund_id,
            FundSettlementTransfer.transfer_type == TRANSFER_TYPE_SETTLEMENT_WALLET_GAS_TOPUP,
            FundSettlementTransfer.to_address == to_address,
            FundSettlementTransfer.status.in_(
                [
                    TRANSFER_STATUS_PENDING,
                    "processing",
                    TRANSFER_STATUS_SENT,
                    "confirmed",
                ]
            ),
        )
        .order_by(FundSettlementTransfer.id.desc())
        .first()
    )


def _create_topup_transfer_row(
    db: Session,
    *,
    batch_id: int,
    fund_id: int,
    from_address: str,
    to_address: str,
    amount_bnb: Decimal,
    status: str,
    tx_hash: str | None = None,
    error: str | None = None,
) -> FundSettlementTransfer:
    now = utcnow()

    row = FundSettlementTransfer(
        batch_id=batch_id,
        order_id=None,
        fund_id=fund_id,
        user_id=None,
        transfer_type=TRANSFER_TYPE_SETTLEMENT_WALLET_GAS_TOPUP,
        from_address=from_address,
        to_address=to_address,
        amount_usdt=None,
        amount_bnb=amount_bnb,
        gas_tx_hash=None,
        tx_hash=tx_hash,
        status=status,
        attempts=1 if tx_hash or error else 0,
        error=error,
        created_at=now,
        updated_at=now,
        sent_at=now if tx_hash else None,
        confirmed_at=None,
    )
    db.add(row)
    db.flush()
    return row


def _send_alert(text: str) -> None:
    try:
        send_telegram_message(text)
    except Exception as exc:
        log.warning("Settlement gas Telegram alert failed: %s", exc)


def top_up_settlement_wallets_once(
    db: Session,
    *,
    settlement_date: date | None = None,
    retry_mode: bool = False,
    dry_run: bool = False,
) -> list[SettlementWalletGasResult]:
    """
    Check/top-up active fund settlement wallets.

    Stage 21 safety:
    - if dry_run=True, no BNB transaction is sent and no transfer rows are intended to persist;
    - caller may rollback after dry-run.
    """
    actual_settlement_date = settlement_date or get_default_settlement_date()
    w3 = get_web3()

    ok_wallet_address = settings.FEE_WALLET_OK_ADDRESS
    ok_wallet_private_key = settings.FEE_WALLET_OK_PRIVATE_KEY

    if not ok_wallet_address:
        raise SettlementGasError("FEE_WALLET_OK_ADDRESS is not configured")
    if not ok_wallet_private_key:
        raise SettlementGasError("FEE_WALLET_OK_PRIVATE_KEY is not configured")

    ok_wallet_address = _checksum(w3, ok_wallet_address)

    bnb_usd_price = get_bnb_usd_price(w3)
    target_bnb = usd_to_bnb(
        Decimal(settings.SETTLEMENT_WALLET_TARGET_BNB_USD),
        bnb_usd_price,
    )
    min_operational_bnb = estimate_min_operational_bnb(w3)

    ok_balance = get_bnb_balance(w3, ok_wallet_address)

    results: list[SettlementWalletGasResult] = []
    insufficient_messages: list[str] = []

    for fund, wallet in _get_active_settlement_wallets(db):
        wallet_address = _checksum(w3, wallet.address)
        batch = _get_or_create_shell_batch(
            db,
            fund_id=fund.id,
            settlement_date=actual_settlement_date,
        )

        existing = _find_existing_topup_transfer(
            db,
            batch_id=batch.id,
            fund_id=fund.id,
            to_address=wallet_address,
        )
        if existing is not None:
            results.append(
                SettlementWalletGasResult(
                    fund_code=fund.code,
                    fund_id=fund.id,
                    wallet_address=wallet_address,
                    batch_id=batch.id,
                    bnb_balance=get_bnb_balance(w3, wallet_address),
                    target_bnb=target_bnb,
                    min_operational_bnb=min_operational_bnb,
                    amount_sent_bnb=ZERO,
                    status="skipped",
                    tx_hash=existing.tx_hash,
                    message=f"Existing top-up transfer found id={existing.id}; skipped.",
                )
            )
            continue

        balance = get_bnb_balance(w3, wallet_address)

        if balance >= target_bnb:
            results.append(
                SettlementWalletGasResult(
                    fund_code=fund.code,
                    fund_id=fund.id,
                    wallet_address=wallet_address,
                    batch_id=batch.id,
                    bnb_balance=balance,
                    target_bnb=target_bnb,
                    min_operational_bnb=min_operational_bnb,
                    amount_sent_bnb=ZERO,
                    status="skipped",
                    tx_hash=None,
                    message="Wallet already has target BNB.",
                )
            )
            continue

        desired_amount = target_bnb - balance

        if retry_mode and ok_balance < desired_amount:
            # Retry mode fallback: try to secure minimum operational gas.
            if balance >= min_operational_bnb:
                results.append(
                    SettlementWalletGasResult(
                        fund_code=fund.code,
                        fund_id=fund.id,
                        wallet_address=wallet_address,
                        batch_id=batch.id,
                        bnb_balance=balance,
                        target_bnb=target_bnb,
                        min_operational_bnb=min_operational_bnb,
                        amount_sent_bnb=ZERO,
                        status="skipped",
                        tx_hash=None,
                        message="Target not met, but minimum operational BNB already present.",
                    )
                )
                insufficient_messages.append(
                    f"{fund.code}: target not met, minimum already present. "
                    f"wallet={wallet_address}"
                )
                continue

            desired_amount = max(min_operational_bnb - balance, ZERO)

        if desired_amount <= 0:
            continue

        if ok_balance < desired_amount:
            error = (
                f"Insufficient OK gas wallet BNB. fund={fund.code} "
                f"ok_wallet={ok_wallet_address} needed_bnb={desired_amount} "
                f"available_bnb={ok_balance}"
            )
            _create_topup_transfer_row(
                db,
                batch_id=batch.id,
                fund_id=fund.id,
                from_address=ok_wallet_address,
                to_address=wallet_address,
                amount_bnb=desired_amount,
                status=TRANSFER_STATUS_FAILED,
                error=error,
            )
            insufficient_messages.append(error)

            results.append(
                SettlementWalletGasResult(
                    fund_code=fund.code,
                    fund_id=fund.id,
                    wallet_address=wallet_address,
                    batch_id=batch.id,
                    bnb_balance=balance,
                    target_bnb=target_bnb,
                    min_operational_bnb=min_operational_bnb,
                    amount_sent_bnb=ZERO,
                    status=TRANSFER_STATUS_FAILED,
                    tx_hash=None,
                    message=error,
                )
            )
            continue

        if dry_run:
            tx_hash = None
            status = "dry_run"
            message = f"Dry-run: would send {desired_amount} BNB."
        else:
            tx_hash = send_native_bnb(
                w3,
                from_private_key=ok_wallet_private_key,
                from_address=ok_wallet_address,
                to_address=wallet_address,
                amount_bnb=desired_amount,
            )
            status = TRANSFER_STATUS_SENT
            message = "BNB top-up transaction sent."
            ok_balance -= desired_amount

        _create_topup_transfer_row(
            db,
            batch_id=batch.id,
            fund_id=fund.id,
            from_address=ok_wallet_address,
            to_address=wallet_address,
            amount_bnb=desired_amount,
            status=TRANSFER_STATUS_SENT if not dry_run else "skipped",
            tx_hash=tx_hash,
            error=None,
        )

        results.append(
            SettlementWalletGasResult(
                fund_code=fund.code,
                fund_id=fund.id,
                wallet_address=wallet_address,
                batch_id=batch.id,
                bnb_balance=balance,
                target_bnb=target_bnb,
                min_operational_bnb=min_operational_bnb,
                amount_sent_bnb=desired_amount,
                status=status,
                tx_hash=tx_hash,
                message=message,
            )
        )

    if insufficient_messages:
        _send_alert(
            "⚠️ Settlement wallet gas top-up issue\n"
            f"OK wallet: {ok_wallet_address}\n"
            + "\n".join(insufficient_messages)
        )

    return results