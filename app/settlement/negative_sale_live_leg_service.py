from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.bybit.client import BybitV5Client
from app.models import (
    FundNegativeSaleBatch,
    FundNegativeSaleLeg,
    FundSettlementBatch,
)
from app.operation_guard.hooks import (
    require_bybit_negative_sale_order_guard,
)
from app.settlement.negative_sale_execution_types import (
    ZERO,
    utcnow,
)
from app.settlement.negative_sale_live_persistence import (
    persist_runtime_intent_state,
)
from app.settlement.negative_sale_order_intent import (
    NegativeSaleOrderIntentError,
    validate_negative_sale_order_intent,
)
from app.settlement.negative_sale_order_runtime import (
    confirm_prepared_suborder,
    prepared_intent_runtime_summary,
    submit_prepared_suborder,
)
from app.settlement.negative_sale_order_state import (
    persist_prepared_intent_before_submit,
)


class NegativeSaleLiveLegServiceError(
    RuntimeError
):
    pass


ACTIVE_RUNTIME_STATUSES = {
    "submitted",
    "acknowledged",
    "pending_confirmation",
    "partially_filled_pending_confirmation",
}


TERMINAL_RUNTIME_STATUSES = {
    "filled",
    "terminal_partial",
    "failed",
}


@dataclass(frozen=True)
class NegativeSaleLiveLegStepResult:
    leg_id: int
    action: str
    posted: bool
    confirmed_suborders: int
    reason: str
    intent: dict[str, Any]
    summary: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _intent_from_leg(
    leg: FundNegativeSaleLeg,
) -> dict[str, Any]:
    raw = leg.suborders_json

    if not isinstance(raw, dict):
        raise NegativeSaleLiveLegServiceError(
            "Durable prepared intent is missing: "
            f"leg_id={leg.id}"
        )

    intent = deepcopy(raw)

    try:
        validate_negative_sale_order_intent(
            intent
        )
    except NegativeSaleOrderIntentError as exc:
        raise NegativeSaleLiveLegServiceError(
            "Durable prepared intent is "
            f"invalid: leg_id={leg.id}, "
            f"error={exc}"
        ) from exc

    return intent


def _rows(
    intent: dict[str, Any],
) -> list[dict[str, Any]]:
    raw = intent.get("suborders")

    if not isinstance(raw, list):
        raise NegativeSaleLiveLegServiceError(
            "Intent suborders must be a list"
        )

    result: list[dict[str, Any]] = []

    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise NegativeSaleLiveLegServiceError(
                f"suborders[{index}] "
                "must be a dict"
            )

        result.append(item)

    return result


def _status(
    item: dict[str, Any],
) -> str:
    return str(
        item.get("status")
        or "prepared"
    ).strip()


def _target_cash_usdt(
    intent: dict[str, Any],
) -> Decimal:
    value = intent.get(
        "target_cash_usdt"
    )

    if value is None or value == "":
        return ZERO

    result = Decimal(str(value))

    if not result.is_finite():
        raise NegativeSaleLiveLegServiceError(
            "target_cash_usdt must be finite"
        )

    if result < ZERO:
        raise NegativeSaleLiveLegServiceError(
            "target_cash_usdt must be "
            "non-negative"
        )

    return result


def _persist_runtime(
    db: Session,
    *,
    leg: FundNegativeSaleLeg,
    intent: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    persisted_leg = (
        persist_runtime_intent_state(
            db,
            leg_id=int(leg.id),
            raw_intent=intent,
            now=now,
        )
    )

    persisted = persisted_leg.suborders_json

    if not isinstance(persisted, dict):
        raise NegativeSaleLiveLegServiceError(
            "Persisted runtime intent "
            "is missing"
        )

    return deepcopy(persisted)


def ensure_live_leg_intent_prepared(
    db: Session,
    *,
    sale_batch: FundNegativeSaleBatch,
    leg: FundNegativeSaleLeg,
    execution_round: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    if isinstance(
        leg.suborders_json,
        dict,
    ):
        return _intent_from_leg(leg)

    return (
        persist_prepared_intent_before_submit(
            db,
            sale_batch=sale_batch,
            leg=leg,
            execution_round=execution_round,
            now=now,
        )
    )


def prepare_live_leg_once(
    db: Session,
    *,
    sale_batch: FundNegativeSaleBatch,
    leg: FundNegativeSaleLeg,
    execution_round: int,
    now: datetime | None = None,
) -> NegativeSaleLiveLegStepResult:
    effective_now = now or utcnow()

    if isinstance(
        leg.suborders_json,
        dict,
    ):
        intent = _intent_from_leg(leg)

        return NegativeSaleLiveLegStepResult(
            leg_id=int(leg.id),
            action="prepare",
            posted=False,
            confirmed_suborders=0,
            reason="intent_already_prepared",
            intent=intent,
            summary=(
                prepared_intent_runtime_summary(
                    intent
                )
            ),
        )

    intent = ensure_live_leg_intent_prepared(
        db,
        sale_batch=sale_batch,
        leg=leg,
        execution_round=execution_round,
        now=effective_now,
    )

    return NegativeSaleLiveLegStepResult(
        leg_id=int(leg.id),
        action="prepare",
        posted=False,
        confirmed_suborders=0,
        reason="intent_prepared",
        intent=intent,
        summary=(
            prepared_intent_runtime_summary(
                intent
            )
        ),
    )


def submit_next_live_leg_suborder(
    db: Session,
    *,
    client: BybitV5Client,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    leg: FundNegativeSaleLeg,
    execution_round: int,
    now: datetime | None = None,
) -> NegativeSaleLiveLegStepResult:
    effective_now = now or utcnow()

    if not isinstance(
        leg.suborders_json,
        dict,
    ):
        raise NegativeSaleLiveLegServiceError(
            "Submit requires a durable "
            "prepared intent"
        )

    intent = _intent_from_leg(leg)

    intent_round = intent.get(
        "execution_round"
    )

    if intent_round is None:
        raise NegativeSaleLiveLegServiceError(
            "Prepared intent has no "
            "execution_round"
        )

    if int(intent_round) != int(
        execution_round
    ):
        raise NegativeSaleLiveLegServiceError(
            "Prepared intent execution "
            "round mismatch"
        )

    rows = _rows(intent)

    active_indexes = [
        index
        for index, item in enumerate(rows)
        if _status(item)
        in ACTIVE_RUNTIME_STATUSES
    ]

    if active_indexes:
        return NegativeSaleLiveLegStepResult(
            leg_id=int(leg.id),
            action="submit",
            posted=False,
            confirmed_suborders=0,
            reason=(
                "active_suborder_requires_"
                "confirmation"
            ),
            intent=intent,
            summary=(
                prepared_intent_runtime_summary(
                    intent
                )
            ),
        )

    if any(
        _status(item) == "failed"
        for item in rows
    ):
        return NegativeSaleLiveLegStepResult(
            leg_id=int(leg.id),
            action="submit",
            posted=False,
            confirmed_suborders=0,
            reason=(
                "failed_suborder_requires_"
                "review"
            ),
            intent=intent,
            summary=(
                prepared_intent_runtime_summary(
                    intent
                )
            ),
        )

    prepared_indexes = [
        index
        for index, item in enumerate(rows)
        if _status(item) == "prepared"
    ]

    if not prepared_indexes:
        return NegativeSaleLiveLegStepResult(
            leg_id=int(leg.id),
            action="submit",
            posted=False,
            confirmed_suborders=0,
            reason="no_prepared_suborder",
            intent=intent,
            summary=(
                prepared_intent_runtime_summary(
                    intent
                )
            ),
        )

    suborder_index = prepared_indexes[0]
    durable_holder = {
        "intent": intent,
    }

    def persist_state(
        updated_intent: dict[str, Any],
    ) -> None:
        durable_holder["intent"] = (
            _persist_runtime(
                db,
                leg=leg,
                intent=updated_intent,
                now=effective_now,
            )
        )

    def before_submit(
        payload: dict[str, Any],
    ) -> None:
        order_link_id = str(
            payload.get("orderLinkId")
            or ""
        ).strip()

        if not order_link_id:
            raise (
                NegativeSaleLiveLegServiceError(
                    "Prepared payload has no "
                    "orderLinkId"
                )
            )

        require_bybit_negative_sale_order_guard(
            db,
            fund_id=int(
                sale_batch.fund_id
            ),
            settlement_batch_id=int(
                settlement_batch.id
            ),
            amount_usdt=(
                _target_cash_usdt(intent)
            ),
            request_id=order_link_id,
            metadata={
                "sale_batch_id": int(
                    sale_batch.id
                ),
                "sale_leg_id": int(
                    leg.id
                ),
                "leg_index": int(
                    leg.leg_index
                ),
                "execution_round": int(
                    execution_round
                ),
                "suborder_index": int(
                    suborder_index
                ),
                "intent_fingerprint": (
                    intent.get(
                        "intent_fingerprint"
                    )
                ),
                "category": (
                    payload.get("category")
                ),
                "symbol": (
                    payload.get("symbol")
                ),
                "side": payload.get("side"),
                "qty": payload.get("qty"),
                "reduce_only": (
                    payload.get(
                        "reduceOnly"
                    )
                ),
                "position_idx": (
                    payload.get(
                        "positionIdx"
                    )
                ),
                "market_unit": (
                    payload.get(
                        "marketUnit"
                    )
                ),
                "exact_prepared_payload": (
                    deepcopy(payload)
                ),
                "no_transfer": True,
                "no_withdrawal": True,
                "no_bsc_action": True,
                "no_accounting_finalization": (
                    True
                ),
            },
        )

    updated_intent, posted = (
        submit_prepared_suborder(
            client,
            raw_intent=intent,
            suborder_index=(
                suborder_index
            ),
            before_submit=before_submit,
            persist_state=persist_state,
            now=effective_now,
        )
    )

    if posted:
        durable_intent = deepcopy(
            durable_holder["intent"]
        )
        reason = "suborder_acknowledged"
    else:
        durable_intent = _persist_runtime(
            db,
            leg=leg,
            intent=updated_intent,
            now=effective_now,
        )
        reason = (
            "suborder_not_submitted_"
            "after_reconciliation"
        )

    return NegativeSaleLiveLegStepResult(
        leg_id=int(leg.id),
        action="submit",
        posted=posted,
        confirmed_suborders=0,
        reason=reason,
        intent=durable_intent,
        summary=(
            prepared_intent_runtime_summary(
                durable_intent
            )
        ),
    )


def confirm_live_leg_suborders(
    db: Session,
    *,
    client: BybitV5Client,
    leg: FundNegativeSaleLeg,
    now: datetime | None = None,
) -> NegativeSaleLiveLegStepResult:
    effective_now = now or utcnow()
    intent = _intent_from_leg(leg)

    confirmed_count = 0

    for index, item in enumerate(
        _rows(intent)
    ):
        if (
            _status(item)
            not in ACTIVE_RUNTIME_STATUSES
        ):
            continue

        intent, _ = (
            confirm_prepared_suborder(
                client,
                raw_intent=intent,
                suborder_index=index,
                now=effective_now,
            )
        )

        intent = _persist_runtime(
            db,
            leg=leg,
            intent=intent,
            now=effective_now,
        )
        confirmed_count += 1

    summary = (
        prepared_intent_runtime_summary(
            intent
        )
    )

    return NegativeSaleLiveLegStepResult(
        leg_id=int(leg.id),
        action="confirm",
        posted=False,
        confirmed_suborders=(
            confirmed_count
        ),
        reason=(
            "active_suborders_reconciled"
            if confirmed_count
            else "no_active_suborders"
        ),
        intent=intent,
        summary=summary,
    )


def resume_live_leg_once(
    db: Session,
    *,
    client: BybitV5Client,
    sale_batch: FundNegativeSaleBatch,
    settlement_batch: FundSettlementBatch,
    leg: FundNegativeSaleLeg,
    execution_round: int,
    now: datetime | None = None,
) -> NegativeSaleLiveLegStepResult:
    effective_now = now or utcnow()

    if not isinstance(
        leg.suborders_json,
        dict,
    ):
        # Preparing an immutable durable
        # intent is a separate cycle.
        # No POST is allowed in this cycle.
        return prepare_live_leg_once(
            db,
            sale_batch=sale_batch,
            leg=leg,
            execution_round=(
                execution_round
            ),
            now=effective_now,
        )

    intent = _intent_from_leg(leg)

    if any(
        _status(item)
        in ACTIVE_RUNTIME_STATUSES
        for item in _rows(intent)
    ):
        # Confirmation and a new POST are
        # intentionally not performed in
        # the same state-machine step.
        return confirm_live_leg_suborders(
            db,
            client=client,
            leg=leg,
            now=effective_now,
        )

    return submit_next_live_leg_suborder(
        db,
        client=client,
        sale_batch=sale_batch,
        settlement_batch=(
            settlement_batch
        ),
        leg=leg,
        execution_round=execution_round,
        now=effective_now,
    )