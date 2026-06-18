from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LiveGateDecision:
    allowed: bool
    feature: str
    env_enabled: bool
    cli_enabled: bool
    reason: str

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "feature": self.feature,
            "env_enabled": self.env_enabled,
            "cli_enabled": self.cli_enabled,
            "reason": self.reason,
        }


def evaluate_live_gate(
    *,
    feature: str,
    env_enabled: bool,
    cli_enabled: bool,
) -> LiveGateDecision:
    """
    Generic production-live gate.

    A real external action may be attempted only when both:
    - environment live flag is enabled;
    - explicit CLI live flag is provided.

    Operation Guard, idempotency and preflight checks must still be applied
    at the exact external-action boundary. This helper only handles the
    env+CLI part of the live contract.
    """
    if not bool(env_enabled):
        return LiveGateDecision(
            allowed=False,
            feature=feature,
            env_enabled=False,
            cli_enabled=bool(cli_enabled),
            reason=f"{feature}: environment live flag is disabled",
        )

    if not bool(cli_enabled):
        return LiveGateDecision(
            allowed=False,
            feature=feature,
            env_enabled=True,
            cli_enabled=False,
            reason=f"{feature}: CLI live flag is missing",
        )

    return LiveGateDecision(
        allowed=True,
        feature=feature,
        env_enabled=True,
        cli_enabled=True,
        reason=f"{feature}: env and CLI live gates passed",
    )


def require_live_gate(
    *,
    feature: str,
    env_enabled: bool,
    cli_enabled: bool,
) -> LiveGateDecision:
    decision = evaluate_live_gate(
        feature=feature,
        env_enabled=env_enabled,
        cli_enabled=cli_enabled,
    )

    if not decision.allowed:
        raise RuntimeError(decision.reason)

    return decision
