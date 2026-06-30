from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.allocation.statuses import (
    DERIVATIVE_SUPPORTED_LEG_GROUPS,
    DERIVATIVE_SUPPORTED_LEG_TYPES,
    LEG_TYPE_BUY_THEN_STAKE,
    LEG_TYPE_RESIDUAL_USDT_EARN,
    LEG_TYPE_SPOT_BUY,
    LEG_TYPE_STABLE_CASH,
    LEG_TYPE_USDT_EARN_STAKE,
)
from app.models import FundAllocationLeg


DERIVATIVE_LIVE_POLICY_FAIL_CLOSED = "fail_closed"
DERIVATIVE_LIVE_POLICY_SKIP_EXISTING_EXPOSURE_SCALING = "skip_existing_exposure_scaling"

BUY_THEN_STAKE_LIVE_POLICY_FAIL_CLOSED = "fail_closed"
BUY_THEN_STAKE_LIVE_POLICY_SPOT_ONLY = "spot_only"

DERIVATIVE_OPTION_SKIP_REASON = "derivative_option_live_policy_skip_existing_exposure_scaling"
BUY_THEN_STAKE_SPOT_ONLY_REASON = "buy_then_stake_live_policy_spot_only"
BUY_THEN_STAKE_FAIL_CLOSED_REASON = "buy_then_stake_live_policy_fail_closed"


ALLOWED_DERIVATIVE_LIVE_POLICIES = {
    DERIVATIVE_LIVE_POLICY_FAIL_CLOSED,
    DERIVATIVE_LIVE_POLICY_SKIP_EXISTING_EXPOSURE_SCALING,
}

ALLOWED_BUY_THEN_STAKE_LIVE_POLICIES = {
    BUY_THEN_STAKE_LIVE_POLICY_FAIL_CLOSED,
    BUY_THEN_STAKE_LIVE_POLICY_SPOT_ONLY,
}


@dataclass(frozen=True)
class LiveLegPolicyDecision:
    allocation_leg_id: int | None
    leg_type: str
    leg_group: str
    action: str
    reason: str | None
    supported_live: bool
    policy_skipped: bool
    fail_closed: bool
    required_guard_actions: tuple[str, ...]
    diagnostics: dict[str, Any]


def normalize_policy(value: Any) -> str:
    return str(value or "").strip().lower()


def derivative_live_policy() -> str:
    policy = normalize_policy(settings.ALLOCATION_DERIVATIVE_LIVE_POLICY)
    if policy not in ALLOWED_DERIVATIVE_LIVE_POLICIES:
        return DERIVATIVE_LIVE_POLICY_FAIL_CLOSED
    return policy


def buy_then_stake_live_policy() -> str:
    policy = normalize_policy(settings.ALLOCATION_BUY_THEN_STAKE_LIVE_POLICY)
    if policy not in ALLOWED_BUY_THEN_STAKE_LIVE_POLICIES:
        return BUY_THEN_STAKE_LIVE_POLICY_FAIL_CLOSED
    return policy


def is_derivative_or_option_leg(leg: FundAllocationLeg) -> bool:
    leg_type = str(leg.leg_type or "")
    leg_group = str(leg.leg_group or "")

    return (
        leg_type in DERIVATIVE_SUPPORTED_LEG_TYPES
        or leg_group in DERIVATIVE_SUPPORTED_LEG_GROUPS
    )


def classify_live_leg_policy(leg: FundAllocationLeg) -> LiveLegPolicyDecision:
    leg_type = str(leg.leg_type or "")
    leg_group = str(leg.leg_group or "")
    allocation_leg_id = int(leg.id) if getattr(leg, "id", None) is not None else None

    if leg_type == LEG_TYPE_STABLE_CASH:
        return LiveLegPolicyDecision(
            allocation_leg_id=allocation_leg_id,
            leg_type=leg_type,
            leg_group=leg_group,
            action="supported_live_no_external_call",
            reason=None,
            supported_live=True,
            policy_skipped=False,
            fail_closed=False,
            required_guard_actions=(),
            diagnostics={"policy": "stable_cash_live_noop"},
        )

    if leg_type == LEG_TYPE_SPOT_BUY:
        return LiveLegPolicyDecision(
            allocation_leg_id=allocation_leg_id,
            leg_type=leg_type,
            leg_group=leg_group,
            action="supported_live_spot_order",
            reason=None,
            supported_live=True,
            policy_skipped=False,
            fail_closed=False,
            required_guard_actions=("bybit_allocation_trade_order",),
            diagnostics={"policy": "spot_buy_live_order"},
        )

    if leg_type in {LEG_TYPE_USDT_EARN_STAKE, LEG_TYPE_RESIDUAL_USDT_EARN}:
        return LiveLegPolicyDecision(
            allocation_leg_id=allocation_leg_id,
            leg_type=leg_type,
            leg_group=leg_group,
            action="supported_live_earn_order",
            reason=None,
            supported_live=True,
            policy_skipped=False,
            fail_closed=False,
            required_guard_actions=("bybit_allocation_earn_order",),
            diagnostics={"policy": "earn_live_order"},
        )

    if leg_type == LEG_TYPE_BUY_THEN_STAKE:
        policy = buy_then_stake_live_policy()

        if policy == BUY_THEN_STAKE_LIVE_POLICY_SPOT_ONLY:
            return LiveLegPolicyDecision(
                allocation_leg_id=allocation_leg_id,
                leg_type=leg_type,
                leg_group=leg_group,
                action="policy_spot_only",
                reason=BUY_THEN_STAKE_SPOT_ONLY_REASON,
                supported_live=True,
                policy_skipped=False,
                fail_closed=False,
                required_guard_actions=("bybit_allocation_trade_order",),
                diagnostics={
                    "policy": policy,
                    "earn_stake": "explicitly_skipped",
                    "spot_order": "enabled",
                },
            )

        return LiveLegPolicyDecision(
            allocation_leg_id=allocation_leg_id,
            leg_type=leg_type,
            leg_group=leg_group,
            action="fail_closed",
            reason=BUY_THEN_STAKE_FAIL_CLOSED_REASON,
            supported_live=False,
            policy_skipped=False,
            fail_closed=True,
            required_guard_actions=(),
            diagnostics={"policy": policy},
        )

    if is_derivative_or_option_leg(leg):
        policy = derivative_live_policy()

        if policy == DERIVATIVE_LIVE_POLICY_SKIP_EXISTING_EXPOSURE_SCALING:
            return LiveLegPolicyDecision(
                allocation_leg_id=allocation_leg_id,
                leg_type=leg_type,
                leg_group=leg_group,
                action="policy_skip_existing_exposure_scaling",
                reason=DERIVATIVE_OPTION_SKIP_REASON,
                supported_live=False,
                policy_skipped=True,
                fail_closed=False,
                required_guard_actions=(),
                diagnostics={
                    "policy": policy,
                    "external_order": "not_placed",
                    "mock_derivative_handler": "not_used_in_live",
                },
            )

        return LiveLegPolicyDecision(
            allocation_leg_id=allocation_leg_id,
            leg_type=leg_type,
            leg_group=leg_group,
            action="fail_closed",
            reason="derivative_option_live_policy_fail_closed",
            supported_live=False,
            policy_skipped=False,
            fail_closed=True,
            required_guard_actions=(),
            diagnostics={"policy": policy},
        )

    return LiveLegPolicyDecision(
        allocation_leg_id=allocation_leg_id,
        leg_type=leg_type,
        leg_group=leg_group,
        action="fail_closed",
        reason=f"unsupported_live_allocation_leg_type: {leg_type}",
        supported_live=False,
        policy_skipped=False,
        fail_closed=True,
        required_guard_actions=(),
        diagnostics={},
    )