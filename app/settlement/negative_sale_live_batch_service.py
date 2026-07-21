from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.orm import Session

from app.bybit.client import BybitV5Client
from app.bybit.instruments import (
    normalize_order_quantity,
    query_instrument_info,
)
from app.bybit.transferable_balance import (
    query_unified_transferable_balance,
)
from app.config import settings
from app.models import (
    FundNegativeSaleBatch,
    FundNegativeSaleLeg,
    FundSettlementBatch,
)
from app.settlement.negative_sale_correction_policy import (
    CorrectionRoundDecision,
    compute_spot_correction_target_usdt,
    confirmed_shortage_usdt,
    evaluate_next_correction_round,
    select_largest_eligible_spot_source,
)
from app.settlement.negative_sale_execution_types import (
    ZERO,
    utcnow,
)
from app.settlement.negative_sale_live_leg_service import (
    NegativeSaleLiveLegStepResult,
    resume_live_leg_once,
)
from app.settlement.negative_sale_live_persistence import (
    archive_terminal_intent_and_activate_next_round,
    persist_new_correction_intent_without_previous,
    validated_terminal_intent_history,
)
from app.settlement.negative_sale_order_intent import (
    NegativeSaleOrderIntentError,
    build_negative_sale_order_intent,
    validate_negative_sale_order_intent,
)
from app.settlement.negative_sale_order_runtime import (
    prepared_intent_runtime_summary,
)
from app.settlement.negative_sale_order_state import (
    NegativeSaleOrderStateError,
    sale_leg_plan_snapshot,
)


class NegativeSaleLiveBatchServiceError(
    RuntimeError
):
    pass


ORDER_CATEGORIES = {
    "spot",
    "linear",
    "inverse",
    "option",
}

DERIVATIVE_ORDER_CATEGORIES = {
    "linear",
    "inverse",
    "option",
}

PENDING_EXTERNAL_ORDER_STATUSES = {
    "submitted",
    "acknowledged",
    "pending_confirmation",
    "partially_filled_pending_confirmation",
}


@dataclass(frozen=True)
class NegativeSaleLiveBatchStepResult:
    sale_batch_id: int
    settlement_batch_id: int
    action: str
    reason: str

    candidate_leg_count: int
    active_leg_id: int | None
    posted: bool

    all_order_legs_terminal: bool
    has_pending_action: bool
    requires_review: bool

    confirmed_available_usdt: (
        Decimal | None
    )
    shortage_usdt: Decimal | None

    correction_decision: (
        dict[str, Any] | None
    )
    transferable_balance: (
        dict[str, Any] | None
    )
    leg_step: dict[str, Any] | None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)

        for key in (
            "confirmed_available_usdt",
            "shortage_usdt",
        ):
            value = result[key]
            result[key] = (
                str(value)
                if value is not None
                else None
            )

        return result


def _decimal(
    value: Any,
    *,
    field_name: str,
) -> Decimal:
    if isinstance(value, bool):
        raise NegativeSaleLiveBatchServiceError(
            f"{field_name} must not be bool"
        )

    if isinstance(value, float):
        raise NegativeSaleLiveBatchServiceError(
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
        raise NegativeSaleLiveBatchServiceError(
            f"{field_name} is not Decimal"
        ) from exc

    if not result.is_finite():
        raise NegativeSaleLiveBatchServiceError(
            f"{field_name} must be finite"
        )

    return result


def _validated_intent(
    leg: FundNegativeSaleLeg,
) -> dict[str, Any] | None:
    raw = leg.suborders_json

    if raw is None:
        return None

    if not isinstance(raw, dict):
        raise NegativeSaleLiveBatchServiceError(
            "Sale leg suborders_json must "
            f"be a dict: leg_id={leg.id}"
        )

    intent = deepcopy(raw)

    try:
        validate_negative_sale_order_intent(
            intent
        )
    except NegativeSaleOrderIntentError as exc:
        raise NegativeSaleLiveBatchServiceError(
            "Sale leg durable intent is "
            f"invalid: leg_id={leg.id}, "
            f"error={exc}"
        ) from exc

    return intent


def _plan_snapshot(
    *,
    sale_batch: FundNegativeSaleBatch,
    leg: FundNegativeSaleLeg,
) -> dict[str, Any]:
    try:
        return sale_leg_plan_snapshot(
            sale_batch=sale_batch,
            leg=leg,
        )
    except NegativeSaleOrderStateError as exc:
        raise NegativeSaleLiveBatchServiceError(
            "Cannot recover immutable plan "
            f"leg: leg_id={leg.id}, "
            f"error={exc}"
        ) from exc


def _preflight_normalized_qty(
    plan_leg: dict[str, Any],
) -> Decimal:
    preflight = plan_leg.get(
        "order_quantity_preflight"
    )

    if not isinstance(preflight, dict):
        return ZERO

    value = preflight.get(
        "normalized_qty"
    )

    if value is None or value == "":
        return ZERO

    result = _decimal(
        value,
        field_name=(
            "order_quantity_preflight."
            "normalized_qty"
        ),
    )

    return max(result, ZERO)


def _is_order_candidate(
    *,
    sale_batch: FundNegativeSaleBatch,
    leg: FundNegativeSaleLeg,
) -> bool:
    intent = _validated_intent(leg)

    if intent is not None:
        return True

    plan_leg = _plan_snapshot(
        sale_batch=sale_batch,
        leg=leg,
    )

    category = str(
        plan_leg.get("category")
        or ""
    ).strip().lower()

    if category not in ORDER_CATEGORIES:
        return False

    if (
        plan_leg.get(
            "requires_fund_to_unified_transfer"
        )
        is True
    ):
        return False

    raw = plan_leg.get("raw")

    if isinstance(raw, dict):
        if (
            raw.get(
                "requires_fund_to_unified_transfer"
            )
            is True
        ):
            return False

    return (
        _preflight_normalized_qty(
            plan_leg
        )
        > ZERO
    )


def _candidate_category(
    *,
    sale_batch: FundNegativeSaleBatch,
    leg: FundNegativeSaleLeg,
) -> str:
    intent = _validated_intent(leg)

    if intent is not None:
        return str(
            intent.get("category")
            or ""
        ).strip().lower()

    plan_leg = _plan_snapshot(
        sale_batch=sale_batch,
        leg=leg,
    )

    return str(
        plan_leg.get("category")
        or ""
    ).strip().lower()


def _candidate_priority(
    *,
    sale_batch: FundNegativeSaleBatch,
    leg: FundNegativeSaleLeg,
) -> int:
    category = _candidate_category(
        sale_batch=sale_batch,
        leg=leg,
    )

    if (
        category
        in DERIVATIVE_ORDER_CATEGORIES
    ):
        return 0

    if category == "spot":
        return 1

    return 2


def _execution_round_for_leg(
    leg: FundNegativeSaleLeg,
) -> int:
    intent = _validated_intent(leg)

    if intent is None:
        return 0

    raw_round = intent.get(
        "execution_round"
    )

    if raw_round is None:
        raise NegativeSaleLiveBatchServiceError(
            "Durable intent has no "
            "execution_round: "
            f"leg_id={leg.id}"
        )

    if isinstance(raw_round, bool):
        raise NegativeSaleLiveBatchServiceError(
            "execution_round must not "
            f"be bool: leg_id={leg.id}"
        )

    try:
        execution_round = int(raw_round)
    except (
        TypeError,
        ValueError,
    ) as exc:
        raise NegativeSaleLiveBatchServiceError(
            "execution_round must be int: "
            f"leg_id={leg.id}"
        ) from exc

    if execution_round < 0:
        raise NegativeSaleLiveBatchServiceError(
            "execution_round must be "
            f"non-negative: leg_id={leg.id}"
        )

    return execution_round


def order_candidate_legs(
    *,
    sale_batch: FundNegativeSaleBatch,
    legs: list[FundNegativeSaleLeg],
) -> list[FundNegativeSaleLeg]:
    candidates = [
        leg
        for leg in legs
        if _is_order_candidate(
            sale_batch=sale_batch,
            leg=leg,
        )
    ]

    candidates.sort(
        key=lambda item: (
            _candidate_priority(
                sale_batch=sale_batch,
                leg=item,
            ),
            int(item.leg_index),
            int(item.id),
        )
    )

    return candidates


def _intent_summary(
    leg: FundNegativeSaleLeg,
) -> dict[str, Any] | None:
    intent = _validated_intent(leg)

    if intent is None:
        return None

    return prepared_intent_runtime_summary(
        intent
    )


def _intent_has_pending_external_action(
    intent: dict[str, Any],
) -> bool:
    raw_suborders = intent.get(
        "suborders"
    )

    if not isinstance(
        raw_suborders,
        list,
    ):
        raise NegativeSaleLiveBatchServiceError(
            "Intent suborders must be "
            "a list"
        )

    for index, item in enumerate(
        raw_suborders
    ):
        if not isinstance(item, dict):
            raise (
                NegativeSaleLiveBatchServiceError(
                    f"suborders[{index}] "
                    "must be a dict"
                )
            )

        status = str(
            item.get("status")
            or "prepared"
        ).strip()

        if (
            status
            in PENDING_EXTERNAL_ORDER_STATUSES
        ):
            return True

    return False


def _completed_correction_rounds(
    candidates: list[
        FundNegativeSaleLeg
    ],
) -> int:
    rounds: dict[
        int,
        list[bool],
    ] = {}

    for leg in candidates:
        try:
            history = (
                validated_terminal_intent_history(
                    getattr(
                        leg,
                        "mock_execution_json",
                        None,
                    )
                )
            )
        except Exception as exc:
            raise (
                NegativeSaleLiveBatchServiceError(
                    "Invalid durable intent "
                    "history: "
                    f"leg_id={leg.id}, "
                    f"error={exc}"
                )
            ) from exc

        for entry in history:
            round_index = int(
                entry["execution_round"]
            )

            if round_index <= 0:
                continue

            rounds.setdefault(
                round_index,
                [],
            ).append(True)

        intent = _validated_intent(
            leg
        )

        if intent is None:
            continue

        round_raw = intent.get(
            "execution_round"
        )

        if round_raw is None:
            raise (
                NegativeSaleLiveBatchServiceError(
                    "Durable intent has no "
                    "execution_round: "
                    f"leg_id={leg.id}"
                )
            )

        round_index = int(
            round_raw
        )

        if round_index <= 0:
            continue

        summary = (
            prepared_intent_runtime_summary(
                intent
            )
        )

        rounds.setdefault(
            round_index,
            [],
        ).append(
            bool(
                summary["all_terminal"]
            )
        )

    return sum(
        1
        for states in rounds.values()
        if states and all(states)
    )


def _correction_decision(
    *,
    sale_batch: FundNegativeSaleBatch,
    confirmed_available_usdt: Decimal,
    completed_rounds: int,
) -> CorrectionRoundDecision:
    return evaluate_next_correction_round(
        required_master_usdt=_decimal(
            sale_batch.required_master_usdt,
            field_name=(
                "required_master_usdt"
            ),
        ),
        confirmed_available_usdt=(
            confirmed_available_usdt
        ),
        completed_rounds=completed_rounds,
        max_rounds=(
            settings
            .NEGATIVE_NET_LIVE_CORRECTION_MAX_ROUNDS
        ),
        has_pending_action=False,
    )


def _confirmed_balance_state(
    *,
    client: BybitV5Client,
    sale_batch: FundNegativeSaleBatch,
) -> tuple[
    Any,
    Decimal,
    Decimal,
]:
    balance = (
        query_unified_transferable_balance(
            client,
            coin="USDT",
            destination_account_type="FUND",
        )
    )

    confirmed_available = (
        balance
        .confirmed_transferable_amount
    )

    required = _decimal(
        sale_batch.required_master_usdt,
        field_name=(
            "required_master_usdt"
        ),
    )

    shortage = confirmed_shortage_usdt(
        required_master_usdt=required,
        confirmed_available_usdt=(
            confirmed_available
        ),
    )

    return (
        balance,
        confirmed_available,
        shortage,
    )


def _resume_candidate_phase_once(
    db: Session,
    *,
    client: BybitV5Client,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    candidates: list[
        FundNegativeSaleLeg
    ],
    total_candidate_count: int,
    now: datetime,
    confirmed_available_usdt: (
        Decimal | None
    ) = None,
    shortage_usdt: (
        Decimal | None
    ) = None,
) -> (
    NegativeSaleLiveBatchStepResult
    | None
):
    for leg in candidates:
        summary = _intent_summary(leg)

        if (
            summary is not None
            and summary["has_failure"]
        ):
            return (
                NegativeSaleLiveBatchStepResult(
                    sale_batch_id=int(
                        sale_batch.id
                    ),
                    settlement_batch_id=int(
                        settlement_batch.id
                    ),
                    action=(
                        "review_required"
                    ),
                    reason=(
                        "terminal_order_failure"
                    ),
                    candidate_leg_count=(
                        total_candidate_count
                    ),
                    active_leg_id=int(
                        leg.id
                    ),
                    posted=False,
                    all_order_legs_terminal=(
                        False
                    ),
                    has_pending_action=False,
                    requires_review=True,
                    confirmed_available_usdt=(
                        confirmed_available_usdt
                    ),
                    shortage_usdt=(
                        shortage_usdt
                    ),
                    correction_decision=None,
                    transferable_balance=None,
                    leg_step=None,
                )
            )

        if (
            summary is not None
            and summary["all_terminal"]
        ):
            continue

        execution_round = (
            _execution_round_for_leg(
                leg
            )
        )

        leg_step = resume_live_leg_once(
            db,
            client=client,
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            leg=leg,
            execution_round=(
                execution_round
            ),
            now=now,
        )

        has_pending = (
            _intent_has_pending_external_action(
                leg_step.intent
            )
        )

        return (
            NegativeSaleLiveBatchStepResult(
                sale_batch_id=int(
                    sale_batch.id
                ),
                settlement_batch_id=int(
                    settlement_batch.id
                ),
                action="order_step",
                reason=leg_step.reason,
                candidate_leg_count=(
                    total_candidate_count
                ),
                active_leg_id=int(
                    leg.id
                ),
                posted=bool(
                    leg_step.posted
                ),
                all_order_legs_terminal=(
                    False
                ),
                has_pending_action=(
                    has_pending
                ),
                requires_review=False,
                confirmed_available_usdt=(
                    confirmed_available_usdt
                ),
                shortage_usdt=(
                    shortage_usdt
                ),
                correction_decision=None,
                transferable_balance=None,
                leg_step=(
                    leg_step.to_dict()
                ),
            )
        )

    return None


def resume_negative_sale_order_batch_once(
    db: Session,
    *,
    client: BybitV5Client,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    legs: list[FundNegativeSaleLeg],
    now: datetime | None = None,
) -> NegativeSaleLiveBatchStepResult:
    effective_now = now or utcnow()

    candidates = order_candidate_legs(
        sale_batch=sale_batch,
        legs=legs,
    )

    derivative_candidates = [
        leg
        for leg in candidates
        if _candidate_category(
            sale_batch=sale_batch,
            leg=leg,
        )
        in DERIVATIVE_ORDER_CATEGORIES
    ]

    spot_candidates = [
        leg
        for leg in candidates
        if _candidate_category(
            sale_batch=sale_batch,
            leg=leg,
        )
        == "spot"
    ]

    derivative_step = (
        _resume_candidate_phase_once(
            db,
            client=client,
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            candidates=(
                derivative_candidates
            ),
            total_candidate_count=len(
                candidates
            ),
            now=effective_now,
        )
    )

    if derivative_step is not None:
        return derivative_step

    (
        pre_spot_balance,
        pre_spot_available,
        pre_spot_shortage,
    ) = _confirmed_balance_state(
        client=client,
        sale_batch=sale_batch,
    )

    if pre_spot_shortage <= ZERO:
        completed_rounds = (
            _completed_correction_rounds(
                candidates
            )
        )

        decision = _correction_decision(
            sale_batch=sale_batch,
            confirmed_available_usdt=(
                pre_spot_available
            ),
            completed_rounds=(
                completed_rounds
            ),
        )

        return (
            NegativeSaleLiveBatchStepResult(
                sale_batch_id=int(
                    sale_batch.id
                ),
                settlement_batch_id=int(
                    settlement_batch.id
                ),
                action="balance_check",
                reason=(
                    "confirmed_balance_"
                    "covers_requirement_"
                    "before_spot"
                ),
                candidate_leg_count=len(
                    candidates
                ),
                active_leg_id=None,
                posted=False,
                all_order_legs_terminal=True,
                has_pending_action=False,
                requires_review=False,
                confirmed_available_usdt=(
                    pre_spot_available
                ),
                shortage_usdt=(
                    pre_spot_shortage
                ),
                correction_decision=(
                    decision.to_dict()
                ),
                transferable_balance=(
                    pre_spot_balance.to_dict()
                ),
                leg_step=None,
            )
        )

    spot_step = (
        _resume_candidate_phase_once(
            db,
            client=client,
            sale_batch=sale_batch,
            settlement_batch=(
                settlement_batch
            ),
            candidates=spot_candidates,
            total_candidate_count=len(
                candidates
            ),
            now=effective_now,
            confirmed_available_usdt=(
                pre_spot_available
            ),
            shortage_usdt=(
                pre_spot_shortage
            ),
        )
    )

    if spot_step is not None:
        return spot_step

    (
        final_balance,
        final_available,
        final_shortage,
    ) = _confirmed_balance_state(
        client=client,
        sale_batch=sale_batch,
    )

    completed_rounds = (
        _completed_correction_rounds(
            candidates
        )
    )

    decision = _correction_decision(
        sale_batch=sale_batch,
        confirmed_available_usdt=(
            final_available
        ),
        completed_rounds=(
            completed_rounds
        ),
    )

    return NegativeSaleLiveBatchStepResult(
        sale_batch_id=int(
            sale_batch.id
        ),
        settlement_batch_id=int(
            settlement_batch.id
        ),
        action="balance_check",
        reason=decision.reason,
        candidate_leg_count=len(
            candidates
        ),
        active_leg_id=None,
        posted=False,
        all_order_legs_terminal=True,
        has_pending_action=False,
        requires_review=False,
        confirmed_available_usdt=(
            final_available
        ),
        shortage_usdt=(
            final_shortage
        ),
        correction_decision=(
            decision.to_dict()
        ),
        transferable_balance=(
            final_balance.to_dict()
        ),
        leg_step=None,
    )