from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable


ZERO = Decimal("0")
ONE = Decimal("1")

NEGATIVE_SALE_CORRECTION_POLICY_VERSION = (
    "bounded_actual_balance_correction_v1"
)


class NegativeSaleCorrectionPolicyError(
    RuntimeError
):
    pass


def _decimal(
    value: Any,
    *,
    field_name: str,
) -> Decimal:
    if isinstance(value, bool):
        raise NegativeSaleCorrectionPolicyError(
            f"{field_name} must not be bool"
        )

    if isinstance(value, float):
        raise NegativeSaleCorrectionPolicyError(
            f"{field_name} must not be float"
        )

    try:
        result = (
            value
            if isinstance(value, Decimal)
            else Decimal(str(value))
        )
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ) as exc:
        raise NegativeSaleCorrectionPolicyError(
            f"{field_name} is not Decimal"
        ) from exc

    if not result.is_finite():
        raise NegativeSaleCorrectionPolicyError(
            f"{field_name} must be finite"
        )

    return result


def _nonnegative_decimal(
    value: Any,
    *,
    field_name: str,
) -> Decimal:
    result = _decimal(
        value,
        field_name=field_name,
    )

    if result < ZERO:
        raise NegativeSaleCorrectionPolicyError(
            f"{field_name} must be "
            "non-negative"
        )

    return result


def _nonnegative_int(
    value: Any,
    *,
    field_name: str,
) -> int:
    if isinstance(value, bool):
        raise NegativeSaleCorrectionPolicyError(
            f"{field_name} must not be bool"
        )

    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise NegativeSaleCorrectionPolicyError(
            f"{field_name} must be int"
        ) from exc

    if result < 0:
        raise NegativeSaleCorrectionPolicyError(
            f"{field_name} must be "
            "non-negative"
        )

    return result


def confirmed_shortage_usdt(
    *,
    required_master_usdt: Any,
    confirmed_available_usdt: Any,
) -> Decimal:
    required = _nonnegative_decimal(
        required_master_usdt,
        field_name="required_master_usdt",
    )
    available = _nonnegative_decimal(
        confirmed_available_usdt,
        field_name=(
            "confirmed_available_usdt"
        ),
    )

    return max(
        required - available,
        ZERO,
    )


@dataclass(frozen=True)
class CorrectionRoundDecision:
    allowed: bool
    next_round: int | None
    shortage_usdt: Decimal
    reason: str
    policy_version: str = (
        NEGATIVE_SALE_CORRECTION_POLICY_VERSION
    )

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["shortage_usdt"] = str(
            self.shortage_usdt
        )
        return result


def evaluate_next_correction_round(
    *,
    required_master_usdt: Any,
    confirmed_available_usdt: Any,
    completed_rounds: int,
    max_rounds: int,
    has_pending_action: bool,
) -> CorrectionRoundDecision:
    shortage = confirmed_shortage_usdt(
        required_master_usdt=(
            required_master_usdt
        ),
        confirmed_available_usdt=(
            confirmed_available_usdt
        ),
    )
    completed = _nonnegative_int(
        completed_rounds,
        field_name="completed_rounds",
    )
    maximum = _nonnegative_int(
        max_rounds,
        field_name="max_rounds",
    )

    if shortage <= ZERO:
        return CorrectionRoundDecision(
            allowed=False,
            next_round=None,
            shortage_usdt=ZERO,
            reason="shortage_resolved",
        )

    if bool(has_pending_action):
        return CorrectionRoundDecision(
            allowed=False,
            next_round=None,
            shortage_usdt=shortage,
            reason=(
                "pending_action_blocks_"
                "correction"
            ),
        )

    if completed >= maximum:
        return CorrectionRoundDecision(
            allowed=False,
            next_round=None,
            shortage_usdt=shortage,
            reason=(
                "correction_rounds_exhausted"
            ),
        )

    return CorrectionRoundDecision(
        allowed=True,
        next_round=completed + 1,
        shortage_usdt=shortage,
        reason="correction_round_available",
    )


def compute_spot_correction_target_usdt(
    *,
    shortage_usdt: Any,
    remaining_sellable_usdt: Any,
    oversell_cap_usdt: Any,
    buffer_pct: Any,
) -> Decimal:
    shortage = _nonnegative_decimal(
        shortage_usdt,
        field_name="shortage_usdt",
    )
    remaining = _nonnegative_decimal(
        remaining_sellable_usdt,
        field_name=(
            "remaining_sellable_usdt"
        ),
    )
    oversell_cap = _nonnegative_decimal(
        oversell_cap_usdt,
        field_name="oversell_cap_usdt",
    )
    buffer_ratio = _nonnegative_decimal(
        buffer_pct,
        field_name="buffer_pct",
    )

    if buffer_ratio > ONE:
        raise NegativeSaleCorrectionPolicyError(
            "buffer_pct must be a ratio "
            "between 0 and 1"
        )

    if (
        shortage <= ZERO
        or remaining <= ZERO
        or oversell_cap <= ZERO
    ):
        return ZERO

    buffered_shortage = shortage * (
        ONE + buffer_ratio
    )

    return min(
        buffered_shortage,
        remaining,
        oversell_cap,
    )


@dataclass(frozen=True)
class CorrectionSpotSource:
    source_key: str
    symbol: str
    category: str
    asset_type: str
    location: str | None
    remaining_sellable_usdt: Decimal
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "symbol": self.symbol,
            "category": self.category,
            "asset_type": self.asset_type,
            "location": self.location,
            "remaining_sellable_usdt": str(
                self.remaining_sellable_usdt
            ),
            "raw": dict(self.raw),
        }


def select_largest_eligible_spot_source(
    sources: Iterable[dict[str, Any]],
) -> CorrectionSpotSource | None:
    candidates: list[
        CorrectionSpotSource
    ] = []

    for index, raw_source in enumerate(
        sources
    ):
        if not isinstance(
            raw_source,
            dict,
        ):
            raise NegativeSaleCorrectionPolicyError(
                f"sources[{index}] "
                "must be a dict"
            )

        source = dict(raw_source)

        category = str(
            source.get("category")
            or ""
        ).strip().lower()
        asset_type = str(
            source.get("asset_type")
            or ""
        ).strip().lower()
        location = str(
            source.get("location")
            or ""
        ).strip().upper() or None

        is_spot = (
            category == "spot"
            or asset_type == "spot"
        )

        if not is_spot:
            continue

        if source.get("eligible") is not True:
            continue

        if (
            source.get(
                "use_for_deficit_cover"
            )
            is not True
        ):
            continue

        if source.get(
            "requires_fund_to_unified_transfer"
        ) is True:
            continue

        if location in {
            "FUND",
            "FUND_WALLET",
            "FUNDING",
            "FUNDING_WALLET",
        }:
            continue

        remaining_raw = source.get(
            "remaining_sellable_usdt"
        )

        if remaining_raw is None:
            raise NegativeSaleCorrectionPolicyError(
                "Eligible spot correction "
                "source has no "
                "remaining_sellable_usdt"
            )

        remaining = _nonnegative_decimal(
            remaining_raw,
            field_name=(
                "remaining_sellable_usdt"
            ),
        )

        if remaining <= ZERO:
            continue

        symbol = str(
            source.get("symbol")
            or ""
        ).strip().upper()

        if not symbol:
            raise NegativeSaleCorrectionPolicyError(
                "Eligible spot correction "
                "source has no symbol"
            )

        source_key = str(
            source.get("source_key")
            or source.get(
                "deterministic_key"
            )
            or symbol
        ).strip()

        candidates.append(
            CorrectionSpotSource(
                source_key=source_key,
                symbol=symbol,
                category=category or "spot",
                asset_type=(
                    asset_type or "spot"
                ),
                location=location,
                remaining_sellable_usdt=(
                    remaining
                ),
                raw=source,
            )
        )

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            -item.remaining_sellable_usdt,
            item.source_key,
            item.symbol,
        )
    )

    return candidates[0]