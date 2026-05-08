from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import settings
from app.models import FundNavGuardEvent, FundNavGuardState
from app.navcalc.schemas import NavResult
from app.telegram import send_telegram_message

log = logging.getLogger("navcalc.nav_guard")

GuardDecision = Literal["accepted", "warning", "rejected"]


@dataclass(frozen=True)
class NavGuardDecision:
    decision: GuardDecision
    reason: str
    nav_drop_pct: Decimal | None
    earn_drop_pct: Decimal | None
    compensation_ratio: Decimal | None
    has_previous: bool


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dec(value) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _pct(drop_abs: Decimal, base: Decimal) -> Decimal | None:
    if base <= 0:
        return None
    return (drop_abs / base) * Decimal("100")


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def get_guard_state(db: Session, *, fund_id: int) -> FundNavGuardState | None:
    return (
        db.query(FundNavGuardState)
        .filter(FundNavGuardState.fund_id == fund_id)
        .first()
    )


def evaluate_nav_guard(
    *,
    previous: FundNavGuardState | None,
    current: NavResult,
) -> NavGuardDecision:
    if not bool(settings.NAV_GUARD_ENABLED):
        return NavGuardDecision(
            decision="accepted",
            reason="NAV Guard disabled",
            nav_drop_pct=None,
            earn_drop_pct=None,
            compensation_ratio=None,
            has_previous=previous is not None,
        )

    if previous is None:
        return NavGuardDecision(
            decision="accepted",
            reason="No previous accepted snapshot",
            nav_drop_pct=None,
            earn_drop_pct=None,
            compensation_ratio=None,
            has_previous=False,
        )

    old_nav = _dec(previous.nav_usd)
    new_nav = _dec(current.nav_usd)

    old_earn = _dec(previous.earn_usd)
    new_earn = _dec(current.earn_usd)

    old_non_earn = _dec(previous.uta_equity_usd) + _dec(previous.funding_wallet_usd)
    new_non_earn = _dec(current.uta_equity_usd) + _dec(current.funding_wallet_usd)

    earn_drop_abs = max(old_earn - new_earn, Decimal("0"))
    earn_drop_pct = _pct(earn_drop_abs, old_earn)

    nav_drop_abs = max(old_nav - new_nav, Decimal("0"))
    nav_drop_pct = _pct(nav_drop_abs, old_nav)

    compensation_abs = max(new_non_earn - old_non_earn, Decimal("0"))
    compensation_ratio = _ratio(compensation_abs, earn_drop_abs)

    max_nav_drop_pct = Decimal(settings.NAV_GUARD_MAX_NAV_DROP_PCT)
    earn_drop_threshold_pct = Decimal(settings.NAV_GUARD_EARN_DROP_PCT)
    compensation_threshold = Decimal(settings.NAV_GUARD_COMPENSATION_RATIO)
    min_earn_drop_usd = Decimal(settings.NAV_GUARD_MIN_EARN_DROP_USD)

    earn_drop_material = earn_drop_abs >= min_earn_drop_usd
    earn_drop_large = (earn_drop_pct is not None) and (earn_drop_pct >= earn_drop_threshold_pct)
    nav_drop_large = (nav_drop_pct is not None) and (nav_drop_pct >= max_nav_drop_pct)

    # If there is no Earn drop, there is nothing for Earn guard to block.
    compensation_low = (
        compensation_ratio is not None
        and compensation_ratio < compensation_threshold
    )

    if (
        earn_drop_material
        and earn_drop_large
        and nav_drop_large
        and compensation_low
    ):
        return NavGuardDecision(
            decision="rejected",
            reason="possible missing Bybit Earn/Savings data",
            nav_drop_pct=nav_drop_pct,
            earn_drop_pct=earn_drop_pct,
            compensation_ratio=compensation_ratio,
            has_previous=True,
        )

    if (
        earn_drop_material
        and earn_drop_large
        and compensation_low
        and (nav_drop_pct is not None)
        and nav_drop_pct < max_nav_drop_pct
    ):
        return NavGuardDecision(
            decision="warning",
            reason="Earn dropped without compensation, but NAV drop is below reject threshold",
            nav_drop_pct=nav_drop_pct,
            earn_drop_pct=earn_drop_pct,
            compensation_ratio=compensation_ratio,
            has_previous=True,
        )

    return NavGuardDecision(
        decision="accepted",
        reason="Snapshot accepted by NAV Guard",
        nav_drop_pct=nav_drop_pct,
        earn_drop_pct=earn_drop_pct,
        compensation_ratio=compensation_ratio,
        has_previous=True,
    )


def write_guard_event(
    db: Session,
    *,
    fund_id: int,
    previous: FundNavGuardState | None,
    current: NavResult,
    decision: NavGuardDecision,
) -> None:
    event = FundNavGuardEvent(
        fund_id=fund_id,
        snapshot_ts=current.snapshot_ts,
        decision=decision.decision,
        reason=decision.reason,
        old_nav_usd=previous.nav_usd if previous else None,
        old_uta_equity_usd=previous.uta_equity_usd if previous else None,
        old_funding_wallet_usd=previous.funding_wallet_usd if previous else None,
        old_earn_usd=previous.earn_usd if previous else None,
        new_nav_usd=current.nav_usd,
        new_uta_equity_usd=current.uta_equity_usd,
        new_funding_wallet_usd=current.funding_wallet_usd,
        new_earn_usd=current.earn_usd,
        nav_drop_pct=decision.nav_drop_pct,
        earn_drop_pct=decision.earn_drop_pct,
        compensation_ratio=decision.compensation_ratio,
    )
    db.add(event)
    db.commit()


def update_guard_state(
    db: Session,
    *,
    fund_id: int,
    current: NavResult,
) -> None:
    stmt = (
        pg_insert(FundNavGuardState.__table__)
        .values(
            fund_id=fund_id,
            last_snapshot_ts=current.snapshot_ts,
            nav_usd=current.nav_usd,
            uta_equity_usd=current.uta_equity_usd,
            funding_wallet_usd=current.funding_wallet_usd,
            earn_usd=current.earn_usd,
            source=current.source or "bybit_v5",
            updated_at=_utcnow(),
        )
        .on_conflict_do_update(
            index_elements=["fund_id"],
            set_={
                "last_snapshot_ts": current.snapshot_ts,
                "nav_usd": current.nav_usd,
                "uta_equity_usd": current.uta_equity_usd,
                "funding_wallet_usd": current.funding_wallet_usd,
                "earn_usd": current.earn_usd,
                "source": current.source or "bybit_v5",
                "updated_at": _utcnow(),
            },
        )
    )
    db.execute(stmt)
    db.commit()


def _fmt_pct(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def _fmt_ratio(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.4f}"


def send_guard_alert(
    *,
    fund_code: str,
    previous: FundNavGuardState | None,
    current: NavResult,
    decision: NavGuardDecision,
) -> None:
    if not bool(settings.NAV_GUARD_TELEGRAM_ALERTS):
        return

    if decision.decision == "rejected":
        text = (
            "❌ NAV Guard rejected snapshot\n"
            f"Fund: {fund_code}\n"
            f"Old NAV: {previous.nav_usd if previous else 'n/a'}\n"
            f"New NAV: {current.nav_usd}\n"
            f"NAV drop: {_fmt_pct(decision.nav_drop_pct)}%\n"
            f"Old Earn: {previous.earn_usd if previous else 'n/a'}\n"
            f"New Earn: {current.earn_usd}\n"
            f"Earn drop: {_fmt_pct(decision.earn_drop_pct)}%\n"
            f"Compensation ratio: {_fmt_ratio(decision.compensation_ratio)}\n"
            f"Reason: {decision.reason}"
        )
        send_telegram_message(text)
        return

    if decision.decision == "warning":
        text = (
            "⚠️ NAV Guard warning\n"
            f"Fund: {fund_code}\n"
            "Earn dropped without compensation, but NAV drop is below reject threshold\n"
            f"Old NAV: {previous.nav_usd if previous else 'n/a'}\n"
            f"New NAV: {current.nav_usd}\n"
            f"NAV drop: {_fmt_pct(decision.nav_drop_pct)}%\n"
            f"Old Earn: {previous.earn_usd if previous else 'n/a'}\n"
            f"New Earn: {current.earn_usd}\n"
            f"Earn drop: {_fmt_pct(decision.earn_drop_pct)}%\n"
            f"Compensation ratio: {_fmt_ratio(decision.compensation_ratio)}"
        )
        send_telegram_message(text)


def evaluate_and_record_nav_guard(
    db: Session,
    *,
    fund_id: int,
    fund_code: str,
    current: NavResult,
) -> NavGuardDecision:
    previous = get_guard_state(db, fund_id=fund_id)
    decision = evaluate_nav_guard(previous=previous, current=current)

    if decision.decision in {"warning", "rejected"}:
        write_guard_event(
            db,
            fund_id=fund_id,
            previous=previous,
            current=current,
            decision=decision,
        )
        send_guard_alert(
            fund_code=fund_code,
            previous=previous,
            current=current,
            decision=decision,
        )

    return decision


def mark_nav_guard_accepted(
    db: Session,
    *,
    fund_id: int,
    current: NavResult,
) -> None:
    update_guard_state(db, fund_id=fund_id, current=current)