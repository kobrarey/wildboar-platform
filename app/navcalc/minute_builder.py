from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.navcalc.schemas import MinuteState


ZERO = Decimal("0")


def minute_floor(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(
        second=0,
        microsecond=0,
    )


def rebase_nav_for_shares(
    nav_usdt: Decimal,
    *,
    from_shares_outstanding: Decimal,
    to_shares_outstanding: Decimal,
) -> Decimal:
    from_shares = Decimal(
        str(from_shares_outstanding)
    )
    to_shares = Decimal(
        str(to_shares_outstanding)
    )
    nav = Decimal(str(nav_usdt))

    if from_shares <= ZERO:
        raise ValueError(
            "from_shares_outstanding must be positive"
        )

    if to_shares <= ZERO:
        raise ValueError(
            "to_shares_outstanding must be positive"
        )

    if from_shares == to_shares:
        return nav

    previous_share_price = nav / from_shares

    return previous_share_price * to_shares


def rebase_minute_state_for_shares(
    state: MinuteState,
    *,
    shares_outstanding: Decimal,
) -> MinuteState:
    new_shares = Decimal(str(shares_outstanding))
    old_shares = Decimal(
        str(state.shares_outstanding)
    )

    if new_shares <= ZERO:
        raise ValueError(
            "shares_outstanding must be positive"
        )

    if old_shares <= ZERO:
        raise ValueError(
            "state.shares_outstanding must be positive"
        )

    if old_shares == new_shares:
        return state

    state.open_nav = rebase_nav_for_shares(
        state.open_nav,
        from_shares_outstanding=old_shares,
        to_shares_outstanding=new_shares,
    )
    state.high_nav = rebase_nav_for_shares(
        state.high_nav,
        from_shares_outstanding=old_shares,
        to_shares_outstanding=new_shares,
    )
    state.low_nav = rebase_nav_for_shares(
        state.low_nav,
        from_shares_outstanding=old_shares,
        to_shares_outstanding=new_shares,
    )
    state.close_nav = rebase_nav_for_shares(
        state.close_nav,
        from_shares_outstanding=old_shares,
        to_shares_outstanding=new_shares,
    )
    state.shares_outstanding = new_shares

    return state


def open_new_minute_state(
    *,
    fund_code: str,
    minute_ts: datetime,
    current_sample_nav: Decimal,
    sample_ts: datetime,
    shares_outstanding: Decimal,
    prev_close_nav: Decimal | None,
    prev_close_shares_outstanding: Decimal | None = None,
) -> MinuteState:
    minute_ts = minute_floor(minute_ts)
    current_shares = Decimal(
        str(shares_outstanding)
    )

    if current_shares <= ZERO:
        raise ValueError(
            "shares_outstanding must be positive"
        )

    if prev_close_nav is None:
        open_nav = current_sample_nav
        high_nav = current_sample_nav
        low_nav = current_sample_nav
        close_nav = current_sample_nav
    else:
        previous_shares = (
            Decimal(
                str(
                    prev_close_shares_outstanding
                )
            )
            if prev_close_shares_outstanding
            is not None
            else current_shares
        )

        rebased_previous_close = (
            rebase_nav_for_shares(
                prev_close_nav,
                from_shares_outstanding=(
                    previous_shares
                ),
                to_shares_outstanding=(
                    current_shares
                ),
            )
        )

        open_nav = rebased_previous_close
        high_nav = max(
            rebased_previous_close,
            current_sample_nav,
        )
        low_nav = min(
            rebased_previous_close,
            current_sample_nav,
        )
        close_nav = current_sample_nav

    return MinuteState(
        fund_code=fund_code,
        minute_ts=minute_ts,
        open_nav=open_nav,
        high_nav=high_nav,
        low_nav=low_nav,
        close_nav=close_nav,
        last_sample_ts=sample_ts.astimezone(
            timezone.utc
        ),
        sample_count=1,
        shares_outstanding=current_shares,
    )


def update_minute_state(
    state: MinuteState,
    *,
    current_sample_nav: Decimal,
    sample_ts: datetime,
) -> MinuteState:
    state.high_nav = max(
        state.high_nav,
        current_sample_nav,
    )
    state.low_nav = min(
        state.low_nav,
        current_sample_nav,
    )
    state.close_nav = current_sample_nav
    state.last_sample_ts = sample_ts.astimezone(
        timezone.utc
    )
    state.sample_count += 1

    return state