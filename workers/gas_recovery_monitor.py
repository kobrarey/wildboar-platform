from __future__ import annotations

import argparse
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from sqlalchemy.orm import Session

from app.config import settings
from app.db import SessionLocal
from app.models import (
    FeeWalletSwap,
    FundOperatorAction,
    FundSettlementTransfer,
    UserWallet,
    WalletTransfer,
)
from app.settlement.gas_service import SettlementGasError, get_bnb_balance, get_web3
from app.settlement.statuses import (
    FEE_WALLET_SWAP_STATUS_WAITING_FOR_GAS,
    TRANSFER_STATUS_WAITING_FOR_GAS,
    WALLET_TRANSFER_STATUS_WAITING_FOR_GAS,
)
from app.telegram import send_telegram_message

log = logging.getLogger("workers.gas_recovery_monitor")

ZERO = Decimal("0")
MIN_NATIVE_BNB_FOR_RETRY = Decimal("0.0003")


@dataclass
class GasRecoveryCounters:
    rpc_unavailable: int = 0
    fee_wallet_ok_ready: int = 0
    fee_wallet_blocked_ready: int = 0
    withdrawal_rows_seen: int = 0
    withdrawal_rows_requeued: int = 0
    settlement_rows_seen: int = 0
    settlement_rows_requeued: int = 0
    fee_swap_rows_seen: int = 0
    fee_swap_rows_requeued: int = 0
    operator_gas_actions_seen: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "rpc_unavailable": self.rpc_unavailable,
            "fee_wallet_ok_ready": self.fee_wallet_ok_ready,
            "fee_wallet_blocked_ready": self.fee_wallet_blocked_ready,
            "withdrawal_rows_seen": self.withdrawal_rows_seen,
            "withdrawal_rows_requeued": self.withdrawal_rows_requeued,
            "settlement_rows_seen": self.settlement_rows_seen,
            "settlement_rows_requeued": self.settlement_rows_requeued,
            "fee_swap_rows_seen": self.fee_swap_rows_seen,
            "fee_swap_rows_requeued": self.fee_swap_rows_requeued,
            "operator_gas_actions_seen": self.operator_gas_actions_seen,
        }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def dec(value) -> Decimal:
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def normalize_text(value: str | None) -> str:
    return str(value or "").strip().lower()


def is_ready_balance(balance: Decimal | None, minimum: Decimal = MIN_NATIVE_BNB_FOR_RETRY) -> bool:
    if balance is None:
        return False
    return dec(balance) >= minimum


def safe_bnb_balance(w3, *, address: str | None, label: str) -> Decimal | None:
    clean_address = str(address or "").strip()
    if not clean_address:
        log.info("Gas recovery monitor: %s address is empty", label)
        return None

    try:
        return get_bnb_balance(w3, clean_address)
    except Exception as exc:
        log.warning("Gas recovery monitor: cannot read BNB balance label=%s: %s", label, exc)
        return None


def requeue_row(row, *, now: datetime, dry_run: bool) -> bool:
    current_next_retry_at = getattr(row, "next_retry_at", None)

    if current_next_retry_at is None:
        return False

    if current_next_retry_at <= now:
        return False

    if not dry_run:
        row.next_retry_at = now

    return True


def waiting_reason(value: str | None) -> str:
    return normalize_text(value)


def withdrawal_needs_ok_fee_wallet(row: WalletTransfer) -> bool:
    reason = waiting_reason(row.error)
    return (
        "insufficient_ok_gas" in reason
        or "insufficient_fee_wallet_bnb" in reason
        or "fee_wallet=ok" in reason
        or "wallet_type=ok" in reason
    )


def withdrawal_needs_blocked_fee_wallet(row: WalletTransfer) -> bool:
    reason = waiting_reason(row.error)
    return (
        "insufficient_blocked_gas" in reason
        or "fee_wallet=blocked" in reason
        or "wallet_type=blocked" in reason
    )


def withdrawal_needs_user_wallet(row: WalletTransfer) -> bool:
    return "insufficient_user_wallet_gas" in waiting_reason(row.error)


def requeue_waiting_withdrawals(
    db: Session,
    *,
    w3,
    ok_ready: bool,
    blocked_ready: bool,
    now: datetime,
    dry_run: bool,
) -> tuple[int, int]:
    rows = (
        db.query(WalletTransfer, UserWallet)
        .join(UserWallet, UserWallet.id == WalletTransfer.wallet_id)
        .filter(WalletTransfer.type == "withdraw")
        .filter(WalletTransfer.status == WALLET_TRANSFER_STATUS_WAITING_FOR_GAS)
        .order_by(WalletTransfer.id.asc())
        .all()
    )

    seen = 0
    requeued = 0

    for transfer, wallet in rows:
        seen += 1
        ready = False

        if withdrawal_needs_ok_fee_wallet(transfer):
            ready = ok_ready
        elif withdrawal_needs_blocked_fee_wallet(transfer):
            ready = blocked_ready
        elif withdrawal_needs_user_wallet(transfer):
            balance = safe_bnb_balance(
                w3,
                address=wallet.address,
                label=f"user_wallet:{wallet.id}",
            )
            ready = is_ready_balance(balance)
        else:
            log.info(
                "Gas recovery monitor: withdrawal waiting reason is not classified "
                "transfer_id=%s error=%s",
                transfer.id,
                transfer.error,
            )

        if ready and requeue_row(transfer, now=now, dry_run=dry_run):
            requeued += 1
            db.add(transfer)

    return seen, requeued


def requeue_waiting_settlement_gas(
    db: Session,
    *,
    ok_ready: bool,
    now: datetime,
    dry_run: bool,
) -> tuple[int, int]:
    rows = (
        db.query(FundSettlementTransfer)
        .filter(FundSettlementTransfer.status == TRANSFER_STATUS_WAITING_FOR_GAS)
        .order_by(FundSettlementTransfer.id.asc())
        .all()
    )

    seen = 0
    requeued = 0

    for row in rows:
        seen += 1

        if not ok_ready:
            continue

        if requeue_row(row, now=now, dry_run=dry_run):
            requeued += 1
            db.add(row)

    return seen, requeued


def requeue_waiting_fee_wallet_swaps(
    db: Session,
    *,
    ok_ready: bool,
    blocked_ready: bool,
    now: datetime,
    dry_run: bool,
) -> tuple[int, int]:
    rows = (
        db.query(FeeWalletSwap)
        .filter(FeeWalletSwap.status == FEE_WALLET_SWAP_STATUS_WAITING_FOR_GAS)
        .order_by(FeeWalletSwap.id.asc())
        .all()
    )

    seen = 0
    requeued = 0

    for row in rows:
        seen += 1
        wallet_type = normalize_text(row.wallet_type)

        ready = (
            wallet_type == "ok"
            and ok_ready
        ) or (
            wallet_type == "blocked"
            and blocked_ready
        )

        if ready and requeue_row(row, now=now, dry_run=dry_run):
            requeued += 1
            db.add(row)

    return seen, requeued


def count_pending_operator_gas_actions(db: Session) -> int:
    return (
        db.query(FundOperatorAction)
        .filter(FundOperatorAction.status == "pending")
        .filter(FundOperatorAction.reason.ilike("%gas%"))
        .count()
    )


def send_recovery_alert(counters: GasRecoveryCounters) -> None:
    total_requeued = (
        counters.withdrawal_rows_requeued
        + counters.settlement_rows_requeued
        + counters.fee_swap_rows_requeued
    )

    if total_requeued <= 0:
        return

    send_telegram_message(
        "✅ Gas recovery monitor requeued waiting operations\n"
        f"withdrawals={counters.withdrawal_rows_requeued}\n"
        f"settlement_gas={counters.settlement_rows_requeued}\n"
        f"fee_wallet_swaps={counters.fee_swap_rows_requeued}\n"
        f"operator_gas_actions_seen={counters.operator_gas_actions_seen}"
    )


def process_once(*, dry_run: bool = False, send_alerts: bool = False) -> GasRecoveryCounters:
    counters = GasRecoveryCounters()
    now = utcnow()

    try:
        w3 = get_web3()
    except SettlementGasError as exc:
        counters.rpc_unavailable = 1
        log.warning("Gas recovery monitor: BSC RPC unavailable: %s", exc)
        return counters

    ok_balance = safe_bnb_balance(
        w3,
        address=settings.FEE_WALLET_OK_ADDRESS,
        label="fee_wallet_ok",
    )
    blocked_balance = safe_bnb_balance(
        w3,
        address=settings.FEE_WALLET_BLOCKED_ADDRESS,
        label="fee_wallet_blocked",
    )

    ok_ready = is_ready_balance(ok_balance)
    blocked_ready = is_ready_balance(blocked_balance)

    counters.fee_wallet_ok_ready = 1 if ok_ready else 0
    counters.fee_wallet_blocked_ready = 1 if blocked_ready else 0

    db = SessionLocal()
    try:
        seen, requeued = requeue_waiting_withdrawals(
            db,
            w3=w3,
            ok_ready=ok_ready,
            blocked_ready=blocked_ready,
            now=now,
            dry_run=dry_run,
        )
        counters.withdrawal_rows_seen = seen
        counters.withdrawal_rows_requeued = requeued

        seen, requeued = requeue_waiting_settlement_gas(
            db,
            ok_ready=ok_ready,
            now=now,
            dry_run=dry_run,
        )
        counters.settlement_rows_seen = seen
        counters.settlement_rows_requeued = requeued

        seen, requeued = requeue_waiting_fee_wallet_swaps(
            db,
            ok_ready=ok_ready,
            blocked_ready=blocked_ready,
            now=now,
            dry_run=dry_run,
        )
        counters.fee_swap_rows_seen = seen
        counters.fee_swap_rows_requeued = requeued

        counters.operator_gas_actions_seen = count_pending_operator_gas_actions(db)

        if dry_run:
            db.rollback()
        else:
            db.commit()

        if send_alerts and not dry_run:
            try:
                send_recovery_alert(counters)
            except Exception as exc:
                log.warning("Gas recovery monitor: Telegram recovery alert failed: %s", exc)

        return counters

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m workers.gas_recovery_monitor",
        description=(
            "Stage 26 gas recovery monitor. "
            "Read-only BNB balance monitor plus DB retry requeue for waiting_for_gas rows. "
            "Does not send BSC transactions."
        ),
    )
    parser.add_argument("--run-once", action="store_true", help="Run one cycle and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Rollback DB retry updates.")
    parser.add_argument(
        "--send-alerts",
        action="store_true",
        help="Send Telegram recovery alert when rows are requeued.",
    )
    parser.add_argument(
        "--sleep-sec",
        type=int,
        default=60,
        help="Sleep interval in loop mode.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = build_arg_parser().parse_args(argv)

    if args.sleep_sec < 1:
        raise SystemExit("--sleep-sec must be >= 1")

    while True:
        counters = process_once(dry_run=bool(args.dry_run), send_alerts=bool(args.send_alerts))
        log.info("Gas recovery monitor cycle complete: %s", counters.to_dict())

        if args.run_once:
            return

        time.sleep(int(args.sleep_sec))


if __name__ == "__main__":
    main()