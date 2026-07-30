from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(PROJECT_ROOT),
    )

from app.db import SessionLocal
from app.models import (
    Fund,
    FundOrder,
    FundRuntimeState,
    FundSettlementBatch,
    User,
    UserFundPosition,
    UserWallet,
)
from app.settlement.negative_external_state import (
    inspect_negative_external_state,
)
from app.settlement.share_quantity import (
    RedeemSharePrecisionError,
    ShareQuantityError,
    validate_redeem_share_input_precision,
)
from app.settlement.statuses import (
    BATCH_STATUS_FAILED_REQUIRES_REVIEW,
    ORDER_SIDE_REDEEM,
    ORDER_STATUS_FAILED_REQUIRES_REVIEW,
)
from app.trading.order_service import (
    ACTIVE_REDEEM_ORDER_STATUSES,
    TradingOrderError,
    create_redeem_order,
    validate_redeem_shares_limits,
)


ZERO = Decimal("0")

RECOVERY_POLICY_VERSION = (
    "small_redeem_withdraw_min_recovery_v1"
)

RECOVERY_MARKER_PREFIX = (
    "[audit]"
    "small_redeem_withdraw_min_recovery_v1"
)


class RecoveryError(RuntimeError):
    def __init__(
        self,
        code: str,
    ) -> None:
        self.code = str(code)
        super().__init__(self.code)


@dataclass(frozen=True)
class RecoveryContext:
    source_order: FundOrder
    batch: FundSettlementBatch
    user: User
    fund: Fund
    position: UserFundPosition
    active_wallet: UserWallet
    runtime_state: FundRuntimeState | None


def _dec(
    value: Any,
) -> Decimal:
    if value is None:
        return ZERO

    if isinstance(value, Decimal):
        return value

    return Decimal(str(value))


def replacement_marker(
    source_order_id: int,
) -> str:
    return (
        f"{RECOVERY_MARKER_PREFIX}:"
        f"source_order_id="
        f"{int(source_order_id)}"
    )


def _row_snapshot(
    row: Any,
) -> dict[str, Any]:
    return {
        key: value
        for key, value in vars(row).items()
        if not key.startswith("_")
    }


def _lock_recovery_context(
    db,
    *,
    failed_order_id: int,
    expected_batch_id: int,
    expected_user_id: int,
    expected_fund_id: int,
) -> RecoveryContext:
    source_order = (
        db.query(FundOrder)
        .filter(
            FundOrder.id
            == int(failed_order_id)
        )
        .with_for_update()
        .first()
    )

    if source_order is None:
        raise RecoveryError(
            "source_order_not_found"
        )

    batch = (
        db.query(FundSettlementBatch)
        .filter(
            FundSettlementBatch.id
            == int(expected_batch_id)
        )
        .with_for_update()
        .first()
    )

    if batch is None:
        raise RecoveryError(
            "source_batch_not_found"
        )

    position = (
        db.query(UserFundPosition)
        .filter(
            UserFundPosition.user_id
            == int(expected_user_id),
            UserFundPosition.fund_id
            == int(expected_fund_id),
        )
        .with_for_update()
        .first()
    )

    if position is None:
        raise RecoveryError(
            "user_fund_position_not_found"
        )

    active_wallets = (
        db.query(UserWallet)
        .filter(
            UserWallet.user_id
            == int(expected_user_id),
            UserWallet.blockchain == "BSC",
            UserWallet.is_active == True,
        )
        .with_for_update()
        .all()
    )

    if len(active_wallets) != 1:
        raise RecoveryError(
            "active_bsc_wallet_count_invalid"
        )

    user = (
        db.query(User)
        .filter(
            User.id == int(expected_user_id)
        )
        .with_for_update()
        .first()
    )

    if user is None:
        raise RecoveryError(
            "source_user_not_found"
        )

    fund = (
        db.query(Fund)
        .filter(
            Fund.id == int(expected_fund_id)
        )
        .with_for_update()
        .first()
    )

    if fund is None:
        raise RecoveryError(
            "source_fund_not_found"
        )

    runtime_state = (
        db.query(FundRuntimeState)
        .filter(
            FundRuntimeState.fund_id
            == int(expected_fund_id)
        )
        .with_for_update()
        .first()
    )

    return RecoveryContext(
        source_order=source_order,
        batch=batch,
        user=user,
        fund=fund,
        position=position,
        active_wallet=active_wallets[0],
        runtime_state=runtime_state,
    )


def _external_state_summary(
    db,
    *,
    batch_id: int,
) -> dict[str, Any]:
    state = inspect_negative_external_state(
        db,
        settlement_batch_id=int(batch_id),
    )

    action_flags = {
        "sale": bool(
            state.sale_action_detected
        ),
        "earn": bool(
            state.earn_action_detected
        ),
        "universal_transfer": bool(
            state
            .universal_transfer_action_detected
        ),
        "withdrawal": bool(
            state.withdrawal_action_detected
        ),
        "payout": bool(
            state.payout_action_detected
        ),
        "gas_topup": bool(
            state.gas_topup_action_detected
        ),
        "bsc_intent": bool(
            state.bsc_intent_action_detected
        ),
        "other": bool(
            state.other_external_action_detected
        ),
    }

    external_state_absent = (
        bool(state.safe_to_release_reserves)
        and bool(state.safe_to_unlock_pricing)
        and not bool(
            state.accounting_finalized
        )
        and not any(
            action_flags.values()
        )
        and not state.reasons
        and not state.evidence
    )

    return {
        "settlement_batch_id": int(
            state.settlement_batch_id
        ),
        "external_state_absent": bool(
            external_state_absent
        ),
        "safe_to_release_reserves": bool(
            state.safe_to_release_reserves
        ),
        "safe_to_unlock_pricing": bool(
            state.safe_to_unlock_pricing
        ),
        "accounting_finalized": bool(
            state.accounting_finalized
        ),
        "action_flags": action_flags,
        "reason_count": len(
            state.reasons
        ),
        "evidence_count": len(
            state.evidence
        ),
    }


def _find_replacement_orders(
    db,
    *,
    marker: str,
) -> list[FundOrder]:
    return list(
        db.query(FundOrder)
        .filter(
            FundOrder.error == marker
        )
        .with_for_update()
        .all()
    )


def _find_active_redeem_orders(
    db,
    *,
    user_id: int,
    fund_id: int,
) -> list[FundOrder]:
    return list(
        db.query(FundOrder)
        .filter(
            FundOrder.user_id
            == int(user_id),
            FundOrder.fund_id
            == int(fund_id),
            FundOrder.side
            == ORDER_SIDE_REDEEM,
            FundOrder.status.in_(
                sorted(
                    ACTIVE_REDEEM_ORDER_STATUSES
                )
            ),
        )
        .with_for_update()
        .all()
    )


def _lock_order_by_id(
    db,
    *,
    order_id: int,
) -> FundOrder:
    order = (
        db.query(FundOrder)
        .filter(
            FundOrder.id == int(order_id)
        )
        .with_for_update()
        .first()
    )

    if order is None:
        raise RecoveryError(
            "replacement_order_not_found"
        )

    return order


def _validate_base_context(
    *,
    context: RecoveryContext,
    failed_order_id: int,
    expected_batch_id: int,
    expected_user_id: int,
    expected_fund_id: int,
    external_state_summary: dict[str, Any],
) -> Decimal:
    source = context.source_order
    batch = context.batch

    if int(source.id) != int(
        failed_order_id
    ):
        raise RecoveryError(
            "source_order_id_mismatch"
        )

    if int(source.user_id) != int(
        expected_user_id
    ):
        raise RecoveryError(
            "source_user_id_mismatch"
        )

    if int(source.fund_id) != int(
        expected_fund_id
    ):
        raise RecoveryError(
            "source_fund_id_mismatch"
        )

    if int(batch.id) != int(
        expected_batch_id
    ):
        raise RecoveryError(
            "source_batch_id_mismatch"
        )

    if int(batch.fund_id) != int(
        expected_fund_id
    ):
        raise RecoveryError(
            "batch_fund_id_mismatch"
        )

    if source.settlement_batch_id is None:
        raise RecoveryError(
            "source_order_batch_link_missing"
        )

    if int(
        source.settlement_batch_id
    ) != int(expected_batch_id):
        raise RecoveryError(
            "source_order_batch_link_mismatch"
        )

    if str(source.side) != ORDER_SIDE_REDEEM:
        raise RecoveryError(
            "source_order_not_redeem"
        )

    if (
        str(source.status)
        != ORDER_STATUS_FAILED_REQUIRES_REVIEW
    ):
        raise RecoveryError(
            "source_order_status_invalid"
        )

    if (
        str(batch.status)
        != BATCH_STATUS_FAILED_REQUIRES_REVIEW
    ):
        raise RecoveryError(
            "source_batch_status_invalid"
        )

    try:
        shares = (
            validate_redeem_share_input_precision(
                source.shares
            )
        )
    except (
        RedeemSharePrecisionError,
        ShareQuantityError,
    ) as exc:
        raise RecoveryError(
            "source_order_shares_invalid"
        ) from exc

    try:
        validate_redeem_shares_limits(
            shares
        )
    except TradingOrderError as exc:
        raise RecoveryError(
            "source_order_shares_rejected:"
            f"{exc.error_key}"
        ) from exc

    if source.executed_at is not None:
        raise RecoveryError(
            "source_order_already_executed"
        )

    released_shares = _dec(
        source
        .redeem_reserve_released_shares
    )

    if released_shares != shares:
        raise RecoveryError(
            "source_redeem_reserve_not_fully_released"
        )

    if (
        source.redeem_reserve_released_at
        is None
    ):
        raise RecoveryError(
            "source_redeem_release_timestamp_missing"
        )

    if batch.accounting_finalized_at is not None:
        raise RecoveryError(
            "source_batch_accounting_exists"
        )

    if (
        batch.seller_payouts_completed_at
        is not None
    ):
        raise RecoveryError(
            "source_batch_payout_accounting_exists"
        )

    batch_pricing_locked_at = getattr(
        batch,
        "pricing_locked_at",
        None,
    )
    batch_pricing_unlocked_at = getattr(
        batch,
        "pricing_unlocked_at",
        None,
    )

    if (
        batch_pricing_locked_at is not None
        and batch_pricing_unlocked_at is None
    ):
        raise RecoveryError(
            "source_batch_pricing_lock_still_active"
        )

    runtime_state = context.runtime_state

    if (
        runtime_state is not None
        and (
            bool(runtime_state.pricing_locked)
            or runtime_state
            .pricing_lock_batch_id
            is not None
        )
    ):
        raise RecoveryError(
            "fund_pricing_lock_still_active"
        )

    if not bool(
        getattr(
            context.user,
            "is_active",
            False,
        )
    ):
        raise RecoveryError(
            "source_user_inactive"
        )

    if not bool(
        getattr(
            context.fund,
            "is_active",
            False,
        )
    ):
        raise RecoveryError(
            "source_fund_inactive"
        )

    if (
        external_state_summary.get(
            "external_state_absent"
        )
        is not True
    ):
        raise RecoveryError(
            "source_batch_external_state_exists"
        )

    return shares


def _validate_existing_replacement(
    *,
    replacement: FundOrder,
    source: FundOrder,
    marker: str,
    source_shares: Decimal,
) -> None:
    if int(replacement.id) == int(source.id):
        raise RecoveryError(
            "replacement_points_to_source_order"
        )

    if replacement.error != marker:
        raise RecoveryError(
            "replacement_marker_mismatch"
        )

    if int(replacement.user_id) != int(
        source.user_id
    ):
        raise RecoveryError(
            "replacement_user_id_mismatch"
        )

    if int(replacement.fund_id) != int(
        source.fund_id
    ):
        raise RecoveryError(
            "replacement_fund_id_mismatch"
        )

    if str(
        replacement.side
    ) != ORDER_SIDE_REDEEM:
        raise RecoveryError(
            "replacement_side_mismatch"
        )

    if _dec(
        replacement.shares
    ) != source_shares:
        raise RecoveryError(
            "replacement_shares_mismatch"
        )


def _validate_new_recovery_capacity(
    *,
    context: RecoveryContext,
    source_shares: Decimal,
) -> None:
    position_shares = _dec(
        context.position.shares
    )

    position_reserved = _dec(
        context.position.shares_reserved
    )

    if position_reserved != ZERO:
        raise RecoveryError(
            "position_shares_reserved_not_zero"
        )

    if position_shares < source_shares:
        raise RecoveryError(
            "position_shares_below_source_order"
        )


def _safe_report(
    *,
    mode: str,
    context: RecoveryContext,
    source_shares: Decimal,
    external_state_summary: dict[str, Any],
    replacement: FundOrder | None,
    idempotent: bool,
) -> dict[str, Any]:
    return {
        "ok": True,
        "mode": mode,
        "policy_version": (
            RECOVERY_POLICY_VERSION
        ),
        "idempotent": bool(idempotent),
        "source_order_id": int(
            context.source_order.id
        ),
        "source_batch_id": int(
            context.batch.id
        ),
        "user_id": int(
            context.source_order.user_id
        ),
        "fund_id": int(
            context.source_order.fund_id
        ),
        "source_shares": str(
            source_shares
        ),
        "source_order_status": str(
            context.source_order.status
        ),
        "source_batch_status": str(
            context.batch.status
        ),
        "source_reserve_fully_released": (
            True
        ),
        "pricing_locked": False,
        "accounting_absent": True,
        "external_state_absent": bool(
            external_state_summary[
                "external_state_absent"
            ]
        ),
        "external_state": dict(
            external_state_summary
        ),
        "active_wallet_id": int(
            context.active_wallet.id
        ),
        "position_shares": str(
            _dec(context.position.shares)
        ),
        "position_shares_reserved": str(
            _dec(
                context
                .position
                .shares_reserved
            )
        ),
        "replacement_order_id": (
            int(replacement.id)
            if replacement is not None
            else None
        ),
        "replacement_status": (
            str(replacement.status)
            if replacement is not None
            else None
        ),
    }


def recover_failed_redeem_order(
    db,
    *,
    failed_order_id: int,
    expected_batch_id: int,
    expected_user_id: int,
    expected_fund_id: int,
    apply: bool,
) -> dict[str, Any]:
    try:
        context = _lock_recovery_context(
            db,
            failed_order_id=(
                failed_order_id
            ),
            expected_batch_id=(
                expected_batch_id
            ),
            expected_user_id=(
                expected_user_id
            ),
            expected_fund_id=(
                expected_fund_id
            ),
        )

        source_before = _row_snapshot(
            context.source_order
        )
        batch_before = _row_snapshot(
            context.batch
        )

        external_state = (
            _external_state_summary(
                db,
                batch_id=expected_batch_id,
            )
        )

        source_shares = (
            _validate_base_context(
                context=context,
                failed_order_id=(
                    failed_order_id
                ),
                expected_batch_id=(
                    expected_batch_id
                ),
                expected_user_id=(
                    expected_user_id
                ),
                expected_fund_id=(
                    expected_fund_id
                ),
                external_state_summary=(
                    external_state
                ),
            )
        )

        marker = replacement_marker(
            failed_order_id
        )

        replacements = (
            _find_replacement_orders(
                db,
                marker=marker,
            )
        )

        if len(replacements) > 1:
            raise RecoveryError(
                "multiple_replacement_orders_found"
            )

        if replacements:
            replacement = replacements[0]

            _validate_existing_replacement(
                replacement=replacement,
                source=context.source_order,
                marker=marker,
                source_shares=source_shares,
            )

            if _row_snapshot(
                context.source_order
            ) != source_before:
                raise RecoveryError(
                    "source_order_mutated"
                )

            if _row_snapshot(
                context.batch
            ) != batch_before:
                raise RecoveryError(
                    "source_batch_mutated"
                )

            report = _safe_report(
                mode=(
                    "apply"
                    if apply
                    else "dry_run"
                ),
                context=context,
                source_shares=source_shares,
                external_state_summary=(
                    external_state
                ),
                replacement=replacement,
                idempotent=True,
            )

            db.rollback()
            return report

        _validate_new_recovery_capacity(
            context=context,
            source_shares=source_shares,
        )

        active_redeems = (
            _find_active_redeem_orders(
                db,
                user_id=expected_user_id,
                fund_id=expected_fund_id,
            )
        )

        if active_redeems:
            raise RecoveryError(
                "active_redeem_order_exists"
            )

        if not apply:
            report = _safe_report(
                mode="dry_run",
                context=context,
                source_shares=source_shares,
                external_state_summary=(
                    external_state
                ),
                replacement=None,
                idempotent=False,
            )

            db.rollback()
            return report

        create_result = create_redeem_order(
            db,
            context.user,
            str(context.fund.code),
            source_shares,
            lang="en",
            commit=False,
        )

        replacement_id = int(
            create_result["order"]["id"]
        )

        replacement = _lock_order_by_id(
            db,
            order_id=replacement_id,
        )

        replacement.error = marker
        db.add(replacement)

        _validate_existing_replacement(
            replacement=replacement,
            source=context.source_order,
            marker=marker,
            source_shares=source_shares,
        )

        if _dec(
            context.position.shares_reserved
        ) != source_shares:
            raise RecoveryError(
                "replacement_reserve_mismatch"
            )

        if _row_snapshot(
            context.source_order
        ) != source_before:
            raise RecoveryError(
                "source_order_mutated"
            )

        if _row_snapshot(
            context.batch
        ) != batch_before:
            raise RecoveryError(
                "source_batch_mutated"
            )

        db.commit()

        return _safe_report(
            mode="apply",
            context=context,
            source_shares=source_shares,
            external_state_summary=(
                external_state
            ),
            replacement=replacement,
            idempotent=False,
        )

    except Exception:
        db.rollback()
        raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create one safe replacement redeem "
            "order for a failed pre-external "
            "settlement order."
        )
    )

    parser.add_argument(
        "--failed-order-id",
        type=int,
        required=True,
    )
    parser.add_argument(
        "--expected-batch-id",
        type=int,
        required=True,
    )
    parser.add_argument(
        "--expected-user-id",
        type=int,
        required=True,
    )
    parser.add_argument(
        "--expected-fund-id",
        type=int,
        required=True,
    )

    mode = (
        parser
        .add_mutually_exclusive_group()
    )

    mode.add_argument(
        "--dry-run",
        action="store_true",
    )
    mode.add_argument(
        "--apply",
        action="store_true",
    )

    return parser


def main() -> int:
    args = _build_parser().parse_args()

    with SessionLocal() as db:
        try:
            report = (
                recover_failed_redeem_order(
                    db,
                    failed_order_id=(
                        args.failed_order_id
                    ),
                    expected_batch_id=(
                        args.expected_batch_id
                    ),
                    expected_user_id=(
                        args.expected_user_id
                    ),
                    expected_fund_id=(
                        args.expected_fund_id
                    ),
                    apply=bool(args.apply),
                )
            )
        except RecoveryError as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "mode": (
                            "apply"
                            if args.apply
                            else "dry_run"
                        ),
                        "error_code": exc.code,
                    },
                    sort_keys=True,
                )
            )
            return 2
        except TradingOrderError as exc:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "mode": (
                            "apply"
                            if args.apply
                            else "dry_run"
                        ),
                        "error_code": (
                            "trading_order_rejected"
                        ),
                        "trading_error_key": (
                            exc.error_key
                        ),
                    },
                    sort_keys=True,
                )
            )
            return 3
        except Exception:
            print(
                json.dumps(
                    {
                        "ok": False,
                        "mode": (
                            "apply"
                            if args.apply
                            else "dry_run"
                        ),
                        "error_code": (
                            "unexpected_recovery_error"
                        ),
                    },
                    sort_keys=True,
                )
            )
            return 4

    print(
        json.dumps(
            report,
            sort_keys=True,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())