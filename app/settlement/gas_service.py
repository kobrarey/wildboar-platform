from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session
from web3 import Web3

from app.config import settings
from app.models import Fund, FundSettlementBatch, FundSettlementTransfer, FundWallet
from app.settlement.batch_service import get_cutoff_ts, get_default_settlement_date
from app.settlement.statuses import (
    BATCH_STATUS_CREATED,
    TRANSFER_STATUS_FAILED,
    TRANSFER_STATUS_PENDING,
    TRANSFER_STATUS_PROCESSING,
    TRANSFER_STATUS_SENT,
    TRANSFER_STATUS_SKIPPED,
    TRANSFER_STATUS_WAITING_FOR_GAS,
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


TOPUP_MODE_TARGET_RESERVE = "target_reserve"
TOPUP_MODE_MINIMUM_OPERATIONAL_FALLBACK = "minimum_operational_fallback"
TOPUP_MODE_MINIMUM_ALREADY_PRESENT = "minimum_already_present"
TOPUP_MODE_TARGET_ALREADY_PRESENT = "target_already_present"
TOPUP_MODE_WAITING_FOR_GAS = "waiting_for_gas"
TOPUP_MODE_RETRY_WAIT = "retry_wait"
TOPUP_MODE_EXISTING_TRANSFER = "existing_transfer"


@dataclass(frozen=True)
class SettlementWalletGasResult:
    fund_code: str
    fund_id: int
    wallet_address: str
    batch_id: int
    bnb_balance: Decimal
    target_bnb: Decimal
    min_operational_bnb: Decimal
    target_deficit_bnb: Decimal
    operational_deficit_bnb: Decimal
    topup_mode: str
    ok_balance_before: Decimal
    ok_balance_after_estimated: Decimal
    amount_sent_bnb: Decimal
    status: str
    tx_hash: str | None
    message: str


@dataclass(frozen=True)
class SettlementGasTopupDecision:
    action: str
    topup_mode: str
    amount_to_send_bnb: Decimal
    target_deficit_bnb: Decimal
    operational_deficit_bnb: Decimal
    message: str


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def settlement_gas_retry_delay() -> timedelta:
    seconds = max(30, int(settings.SETTLEMENT_GAS_WAIT_RETRY_SEC))
    return timedelta(seconds=seconds)


def settlement_gas_alert_cooldown() -> timedelta:
    seconds = max(60, int(settings.SETTLEMENT_GAS_ALERT_COOLDOWN_SEC))
    return timedelta(seconds=seconds)


def should_send_settlement_gas_alert(row: FundSettlementTransfer, now: datetime) -> bool:
    last = getattr(row, "last_gas_alert_at", None)
    if last is None:
        return True
    return now - last >= settlement_gas_alert_cooldown()


def mark_topup_waiting_for_gas(
    db: Session,
    *,
    row: FundSettlementTransfer | None,
    batch_id: int,
    fund_id: int,
    from_address: str,
    to_address: str,
    amount_bnb: Decimal,
    error: str,
) -> FundSettlementTransfer:
    now = utcnow()

    if row is None:
        row = _create_topup_transfer_row(
            db,
            batch_id=batch_id,
            fund_id=fund_id,
            from_address=from_address,
            to_address=to_address,
            amount_bnb=amount_bnb,
            status=TRANSFER_STATUS_WAITING_FOR_GAS,
            tx_hash=None,
            error=error,
        )
    else:
        row.from_address = from_address
        row.to_address = to_address
        row.amount_bnb = amount_bnb
        row.status = TRANSFER_STATUS_WAITING_FOR_GAS
        row.tx_hash = None
        row.error = error
        row.updated_at = now

    row.next_retry_at = now + settlement_gas_retry_delay()

    if should_send_settlement_gas_alert(row, now):
        _send_alert(
            "⚠️ Settlement wallet gas top-up waiting for BNB\n"
            f"fund_id={fund_id}\n"
            f"batch_id={batch_id}\n"
            f"to_wallet={to_address}\n"
            f"amount_bnb={amount_bnb}\n"
            f"reason={error}\n"
            f"next_retry_at={row.next_retry_at}"
        )
        row.last_gas_alert_at = now

    db.add(row)
    db.flush()
    return row


def mark_topup_sent(
    row: FundSettlementTransfer,
    *,
    amount_bnb: Decimal,
    tx_hash: str | None,
) -> None:
    now = utcnow()
    row.amount_bnb = amount_bnb
    row.status = TRANSFER_STATUS_SENT
    row.tx_hash = tx_hash
    row.error = None
    row.next_retry_at = None
    row.updated_at = now
    row.sent_at = now if tx_hash else None


def _dec(value: Any) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _max_dec(left: Decimal, right: Decimal) -> Decimal:
    return left if left >= right else right


def _normalize_fund_codes(value: set[str] | list[str] | tuple[str, ...] | str | None) -> set[str]:
    if value is None:
        return set()

    if isinstance(value, str):
        parts = value.split(",")
    else:
        parts = list(value)

    return {str(item).strip().lower() for item in parts if str(item).strip()}


def configured_settlement_gas_topup_fund_codes() -> set[str]:
    return _normalize_fund_codes(settings.SETTLEMENT_GAS_TOPUP_FUND_CODES)


def _effective_fund_codes(fund_codes: set[str] | None) -> set[str]:
    explicit = _normalize_fund_codes(fund_codes)
    if explicit:
        return explicit
    return configured_settlement_gas_topup_fund_codes()


def choose_settlement_gas_topup_amount(
    *,
    wallet_balance_bnb: Decimal,
    target_bnb: Decimal,
    min_operational_bnb: Decimal,
    ok_balance_bnb: Decimal,
    allow_min_operational_fallback: bool,
) -> SettlementGasTopupDecision:
    balance = _dec(wallet_balance_bnb)
    target = _dec(target_bnb)
    minimum = _dec(min_operational_bnb)
    ok_balance = _dec(ok_balance_bnb)

    target_deficit = _max_dec(target - balance, ZERO)
    operational_deficit = _max_dec(minimum - balance, ZERO)

    if target_deficit <= ZERO:
        return SettlementGasTopupDecision(
            action="skip",
            topup_mode=TOPUP_MODE_TARGET_ALREADY_PRESENT,
            amount_to_send_bnb=ZERO,
            target_deficit_bnb=target_deficit,
            operational_deficit_bnb=operational_deficit,
            message="Wallet already has target BNB.",
        )

    if ok_balance >= target_deficit:
        return SettlementGasTopupDecision(
            action="send",
            topup_mode=TOPUP_MODE_TARGET_RESERVE,
            amount_to_send_bnb=target_deficit,
            target_deficit_bnb=target_deficit,
            operational_deficit_bnb=operational_deficit,
            message="Target reserve BNB top-up can be funded.",
        )

    if allow_min_operational_fallback and operational_deficit <= ZERO:
        return SettlementGasTopupDecision(
            action="skip",
            topup_mode=TOPUP_MODE_MINIMUM_ALREADY_PRESENT,
            amount_to_send_bnb=ZERO,
            target_deficit_bnb=target_deficit,
            operational_deficit_bnb=operational_deficit,
            message="Target not met, but minimum operational BNB already present.",
        )

    if (
        allow_min_operational_fallback
        and operational_deficit > ZERO
        and ok_balance >= operational_deficit
    ):
        return SettlementGasTopupDecision(
            action="send",
            topup_mode=TOPUP_MODE_MINIMUM_OPERATIONAL_FALLBACK,
            amount_to_send_bnb=operational_deficit,
            target_deficit_bnb=target_deficit,
            operational_deficit_bnb=operational_deficit,
            message="Minimum operational BNB fallback can be funded.",
        )

    if not allow_min_operational_fallback:
        reason = (
            "insufficient_ok_gas: target reserve cannot be funded and "
            "minimum operational fallback is disabled"
        )
        amount_required = target_deficit
    else:
        reason = "insufficient_ok_gas: minimum operational BNB cannot be funded"
        amount_required = operational_deficit if operational_deficit > ZERO else target_deficit

    return SettlementGasTopupDecision(
        action="waiting_for_gas",
        topup_mode=TOPUP_MODE_WAITING_FOR_GAS,
        amount_to_send_bnb=amount_required,
        target_deficit_bnb=target_deficit,
        operational_deficit_bnb=operational_deficit,
        message=(
            f"{reason}; target_deficit_bnb={target_deficit}; "
            f"operational_deficit_bnb={operational_deficit}; "
            f"available_bnb={ok_balance}"
        ),
    )


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


def _get_active_settlement_wallets(
    db: Session,
    *,
    fund_codes: set[str] | None = None,
) -> list[tuple[Fund, FundWallet]]:
    normalized_codes = _normalize_fund_codes(fund_codes)

    query = (
        db.query(Fund, FundWallet)
        .join(FundWallet, FundWallet.fund_id == Fund.id)
        .filter(
            Fund.is_active == True,
            FundWallet.blockchain == "BSC",
            FundWallet.wallet_type == "settlement",
            FundWallet.is_active == True,
        )
    )

    if normalized_codes:
        query = query.filter(func.lower(Fund.code).in_(sorted(normalized_codes)))

    rows = query.order_by(Fund.sort_order.asc(), Fund.id.asc()).all()

    if normalized_codes:
        found_codes = {str(fund.code).strip().lower() for fund, _wallet in rows}
        missing_codes = normalized_codes - found_codes
        if missing_codes:
            raise SettlementGasError(
                "Active settlement wallet not found for fund_codes="
                f"{sorted(missing_codes)}"
            )

    return rows


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
                    TRANSFER_STATUS_WAITING_FOR_GAS,
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
    fund_codes: set[str] | None = None,
) -> list[SettlementWalletGasResult]:
    """
    Check/top-up active fund settlement wallets.

    Stage 26.2.6 safety:
    - target reserve remains preferred when OK gas wallet can fund it;
    - minimum operational fallback can proceed when target reserve cannot be funded;
    - insufficient OK gas is waiting_for_gas, not final failed;
    - if dry_run=True, no BNB transaction is sent and caller may rollback.
    """
    actual_settlement_date = settlement_date or get_default_settlement_date()
    effective_fund_codes = _effective_fund_codes(fund_codes)
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
    allow_min_fallback = bool(settings.SETTLEMENT_GAS_ALLOW_MIN_OPERATIONAL_FALLBACK)

    ok_balance = get_bnb_balance(w3, ok_wallet_address)

    results: list[SettlementWalletGasResult] = []
    insufficient_messages: list[str] = []

    def build_result(
        *,
        fund: Fund,
        wallet_address: str,
        batch_id: int,
        balance: Decimal,
        decision: SettlementGasTopupDecision,
        status: str,
        tx_hash: str | None,
        message: str | None = None,
        amount_sent_bnb: Decimal | None = None,
        ok_balance_before: Decimal | None = None,
        ok_balance_after_estimated: Decimal | None = None,
    ) -> SettlementWalletGasResult:
        before = ok_balance if ok_balance_before is None else ok_balance_before
        amount_sent = decision.amount_to_send_bnb if amount_sent_bnb is None else amount_sent_bnb
        after = (
            before - amount_sent
            if ok_balance_after_estimated is None and amount_sent > ZERO
            else before if ok_balance_after_estimated is None else ok_balance_after_estimated
        )

        return SettlementWalletGasResult(
            fund_code=str(fund.code),
            fund_id=int(fund.id),
            wallet_address=wallet_address,
            batch_id=int(batch_id),
            bnb_balance=balance,
            target_bnb=target_bnb,
            min_operational_bnb=min_operational_bnb,
            target_deficit_bnb=decision.target_deficit_bnb,
            operational_deficit_bnb=decision.operational_deficit_bnb,
            topup_mode=decision.topup_mode,
            ok_balance_before=before,
            ok_balance_after_estimated=after,
            amount_sent_bnb=amount_sent,
            status=status,
            tx_hash=tx_hash,
            message=message or decision.message,
        )

    for fund, wallet in _get_active_settlement_wallets(db, fund_codes=effective_fund_codes):
        wallet_address = _checksum(w3, wallet.address)
        batch = _get_or_create_shell_batch(
            db,
            fund_id=int(fund.id),
            settlement_date=actual_settlement_date,
        )

        existing = _find_existing_topup_transfer(
            db,
            batch_id=int(batch.id),
            fund_id=int(fund.id),
            to_address=wallet_address,
        )

        balance = get_bnb_balance(w3, wallet_address)
        decision = choose_settlement_gas_topup_amount(
            wallet_balance_bnb=balance,
            target_bnb=target_bnb,
            min_operational_bnb=min_operational_bnb,
            ok_balance_bnb=ok_balance,
            allow_min_operational_fallback=allow_min_fallback,
        )

        if existing is not None:
            if existing.status == TRANSFER_STATUS_WAITING_FOR_GAS:
                if existing.next_retry_at is not None and existing.next_retry_at > utcnow():
                    retry_decision = SettlementGasTopupDecision(
                        action="skip",
                        topup_mode=TOPUP_MODE_RETRY_WAIT,
                        amount_to_send_bnb=ZERO,
                        target_deficit_bnb=decision.target_deficit_bnb,
                        operational_deficit_bnb=decision.operational_deficit_bnb,
                        message=(
                            f"Existing waiting_for_gas top-up transfer id={existing.id}; "
                            f"next_retry_at={existing.next_retry_at}; skipped until retry time."
                        ),
                    )
                    results.append(
                        build_result(
                            fund=fund,
                            wallet_address=wallet_address,
                            batch_id=int(batch.id),
                            balance=balance,
                            decision=retry_decision,
                            status=TRANSFER_STATUS_WAITING_FOR_GAS,
                            tx_hash=existing.tx_hash,
                            amount_sent_bnb=ZERO,
                        )
                    )
                    continue

                # Retry window is open. Reuse the existing row instead of creating
                # a duplicate top-up transfer.
                existing.status = TRANSFER_STATUS_PROCESSING
                existing.updated_at = utcnow()
                db.add(existing)
                db.flush()

            if existing.status != TRANSFER_STATUS_PROCESSING:
                existing_decision = SettlementGasTopupDecision(
                    action="skip",
                    topup_mode=TOPUP_MODE_EXISTING_TRANSFER,
                    amount_to_send_bnb=ZERO,
                    target_deficit_bnb=decision.target_deficit_bnb,
                    operational_deficit_bnb=decision.operational_deficit_bnb,
                    message=f"Existing top-up transfer found id={existing.id}; skipped.",
                )
                results.append(
                    build_result(
                        fund=fund,
                        wallet_address=wallet_address,
                        batch_id=int(batch.id),
                        balance=balance,
                        decision=existing_decision,
                        status=TRANSFER_STATUS_SKIPPED,
                        tx_hash=existing.tx_hash,
                        amount_sent_bnb=ZERO,
                    )
                )
                continue

        if decision.action == "skip":
            if existing is not None and existing.status == TRANSFER_STATUS_PROCESSING:
                existing.amount_bnb = ZERO
                existing.status = TRANSFER_STATUS_SKIPPED
                existing.error = None
                existing.next_retry_at = None
                existing.updated_at = utcnow()
                db.add(existing)
                db.flush()

            results.append(
                build_result(
                    fund=fund,
                    wallet_address=wallet_address,
                    batch_id=int(batch.id),
                    balance=balance,
                    decision=decision,
                    status=TRANSFER_STATUS_SKIPPED,
                    tx_hash=existing.tx_hash if existing is not None else None,
                    amount_sent_bnb=ZERO,
                    ok_balance_after_estimated=ok_balance,
                )
            )

            if decision.topup_mode == TOPUP_MODE_MINIMUM_ALREADY_PRESENT:
                insufficient_messages.append(
                    f"{fund.code}: target reserve shortage but minimum operational gas already present; "
                    f"wallet={wallet_address}; target_deficit_bnb={decision.target_deficit_bnb}; "
                    f"operational_deficit_bnb={decision.operational_deficit_bnb}; "
                    f"ok_wallet_available_bnb={ok_balance}"
                )

            continue

        if decision.action == "waiting_for_gas":
            error = (
                f"{decision.message}; fund={fund.code}; batch_id={batch.id}; "
                f"wallet={wallet_address}; ok_wallet={ok_wallet_address}"
            )

            waiting_row = mark_topup_waiting_for_gas(
                db,
                row=existing if existing is not None else None,
                batch_id=int(batch.id),
                fund_id=int(fund.id),
                from_address=ok_wallet_address,
                to_address=wallet_address,
                amount_bnb=decision.amount_to_send_bnb,
                error=error,
            )
            insufficient_messages.append(error)

            results.append(
                build_result(
                    fund=fund,
                    wallet_address=wallet_address,
                    batch_id=int(batch.id),
                    balance=balance,
                    decision=decision,
                    status=TRANSFER_STATUS_WAITING_FOR_GAS,
                    tx_hash=waiting_row.tx_hash,
                    amount_sent_bnb=ZERO,
                    ok_balance_after_estimated=ok_balance,
                )
            )
            continue

        amount_to_send = decision.amount_to_send_bnb
        ok_before = ok_balance

        if amount_to_send <= ZERO:
            raise SettlementGasError(
                f"Internal gas top-up decision error: non-positive amount for fund={fund.code}"
            )

        if dry_run:
            tx_hash = None
            status = "dry_run"
            message = (
                f"Dry-run: would send {amount_to_send} BNB; "
                f"topup_mode={decision.topup_mode}."
            )
        else:
            tx_hash = send_native_bnb(
                w3,
                from_private_key=ok_wallet_private_key,
                from_address=ok_wallet_address,
                to_address=wallet_address,
                amount_bnb=amount_to_send,
            )
            status = TRANSFER_STATUS_SENT
            message = (
                f"BNB top-up transaction sent; topup_mode={decision.topup_mode}."
            )
            ok_balance -= amount_to_send

        if existing is not None and existing.status == TRANSFER_STATUS_PROCESSING:
            mark_topup_sent(
                existing,
                amount_bnb=amount_to_send,
                tx_hash=tx_hash,
            )
        else:
            _create_topup_transfer_row(
                db,
                batch_id=int(batch.id),
                fund_id=int(fund.id),
                from_address=ok_wallet_address,
                to_address=wallet_address,
                amount_bnb=amount_to_send,
                status=TRANSFER_STATUS_SENT if not dry_run else TRANSFER_STATUS_SKIPPED,
                tx_hash=tx_hash,
                error=None,
            )

        results.append(
            build_result(
                fund=fund,
                wallet_address=wallet_address,
                batch_id=int(batch.id),
                balance=balance,
                decision=decision,
                status=status,
                tx_hash=tx_hash,
                message=message,
                amount_sent_bnb=amount_to_send,
                ok_balance_before=ok_before,
                ok_balance_after_estimated=ok_before - amount_to_send,
            )
        )

    if insufficient_messages:
        _send_alert(
            " Settlement wallet gas top-up issue\n"
            f"OK wallet: {ok_wallet_address}\n"
            f"OK wallet available BNB: {ok_balance}\n"
            + "\n".join(insufficient_messages)
        )

    return results
