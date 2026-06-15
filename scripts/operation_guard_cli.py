from __future__ import annotations

import argparse
from decimal import Decimal

from app.db import SessionLocal
from app.models import (
    Fund,
    FundOperationGuardOverride,
    FundOperationGuardState,
)
from app.operation_guard.service import (
    OperationGuardError,
    create_operation_guard_override,
    revoke_operation_guard_override,
    set_operation_guard_state,
)
from app.operation_guard.statuses import (
    OP_GUARD_ACTION_TYPES,
    OP_GUARD_MODE_BLOCKED,
    OP_GUARD_MODE_LIVE_ALLOWED,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stage 24 Operation Guard / Withdrawal Kill Switch CLI"
    )

    command = parser.add_mutually_exclusive_group(required=True)
    command.add_argument("--list", action="store_true")
    command.add_argument("--set-state", action="store_true")
    command.add_argument("--create-override", action="store_true")
    command.add_argument("--revoke-override", action="store_true")

    parser.add_argument("--scope", choices=["global", "fund"], default=None)
    parser.add_argument("--fund-code", default=None)

    parser.add_argument("--action", choices=sorted(OP_GUARD_ACTION_TYPES), default=None)
    parser.add_argument(
        "--mode",
        choices=[OP_GUARD_MODE_BLOCKED, OP_GUARD_MODE_LIVE_ALLOWED],
        default=None,
    )

    parser.add_argument("--manager-user-id", type=int, default=None)
    parser.add_argument("--reason", default=None)

    parser.add_argument("--ttl-minutes", type=int, default=None)
    parser.add_argument("--max-amount-usdt", default=None)
    parser.add_argument("--request-id", default=None)
    parser.add_argument("--settlement-batch-id", type=int, default=None)
    parser.add_argument("--idempotency-key", default=None)

    parser.add_argument("--override-id", type=int, default=None)

    return parser.parse_args()


def _get_fund_id_by_code(db, *, fund_code: str | None) -> int | None:
    if not fund_code:
        return None

    fund = db.query(Fund).filter(Fund.code == str(fund_code)).first()
    if fund is None:
        raise OperationGuardError(f"Fund not found by code: {fund_code}")

    return int(fund.id)


def _resolve_scope(args: argparse.Namespace, *, fund_id: int | None) -> None:
    if args.scope == "global" and fund_id is not None:
        raise OperationGuardError("--scope global cannot be combined with --fund-code")

    if args.scope == "fund" and fund_id is None:
        raise OperationGuardError("--scope fund requires --fund-code")

    if args.scope is None and fund_id is None:
        args.scope = "global"

    if args.scope is None and fund_id is not None:
        args.scope = "fund"


def _require_manager(args: argparse.Namespace) -> int:
    if args.manager_user_id is None:
        raise OperationGuardError("--manager-user-id is required")

    return int(args.manager_user_id)


def _require_action(args: argparse.Namespace) -> str:
    if not args.action:
        raise OperationGuardError("--action is required")

    return str(args.action)


def _require_mode(args: argparse.Namespace) -> str:
    if not args.mode:
        raise OperationGuardError("--mode is required")

    return str(args.mode)


def _list_rows(db) -> None:
    states = (
        db.query(FundOperationGuardState)
        .order_by(
            FundOperationGuardState.action_type.asc(),
            FundOperationGuardState.scope_key.asc(),
        )
        .all()
    )

    overrides = (
        db.query(FundOperationGuardOverride)
        .order_by(
            FundOperationGuardOverride.status.asc(),
            FundOperationGuardOverride.expires_at.asc(),
            FundOperationGuardOverride.id.asc(),
        )
        .all()
    )

    print("OPERATION GUARD STATES")
    if not states:
        print("  <empty>")
    for state in states:
        print(
            {
                "id": int(state.id),
                "scope_key": state.scope_key,
                "scope_type": state.scope_type,
                "fund_id": state.fund_id,
                "action_type": state.action_type,
                "mode": state.mode,
                "reason": state.reason,
                "updated_by_user_id": state.updated_by_user_id,
                "updated_at": (
                    state.updated_at.isoformat()
                    if state.updated_at is not None
                    else None
                ),
            }
        )

    print("OPERATION GUARD OVERRIDES")
    if not overrides:
        print("  <empty>")
    for override in overrides:
        print(
            {
                "id": int(override.id),
                "scope_key": override.scope_key,
                "scope_type": override.scope_type,
                "fund_id": override.fund_id,
                "action_type": override.action_type,
                "status": override.status,
                "manager_user_id": int(override.manager_user_id),
                "settlement_batch_id": override.settlement_batch_id,
                "request_id": override.request_id,
                "max_amount_usdt": (
                    str(override.max_amount_usdt)
                    if override.max_amount_usdt is not None
                    else None
                ),
                "starts_at": override.starts_at.isoformat(),
                "expires_at": override.expires_at.isoformat(),
                "used_at": (
                    override.used_at.isoformat()
                    if override.used_at is not None
                    else None
                ),
                "revoked_at": (
                    override.revoked_at.isoformat()
                    if override.revoked_at is not None
                    else None
                ),
                "reason": override.reason,
            }
        )


def _set_state(db, args: argparse.Namespace) -> None:
    fund_id = _get_fund_id_by_code(db, fund_code=args.fund_code)
    _resolve_scope(args, fund_id=fund_id)

    state = set_operation_guard_state(
        db,
        action_type=_require_action(args),
        mode=_require_mode(args),
        fund_id=fund_id,
        manager_user_id=_require_manager(args),
        reason=args.reason,
    )

    db.commit()

    print(
        {
            "operation": "set_state",
            "id": int(state.id),
            "scope_key": state.scope_key,
            "scope_type": state.scope_type,
            "fund_id": state.fund_id,
            "action_type": state.action_type,
            "mode": state.mode,
            "reason": state.reason,
            "committed": True,
            "no_external_action": True,
        }
    )


def _create_override(db, args: argparse.Namespace) -> None:
    fund_id = _get_fund_id_by_code(db, fund_code=args.fund_code)
    _resolve_scope(args, fund_id=fund_id)

    max_amount = (
        Decimal(str(args.max_amount_usdt))
        if args.max_amount_usdt is not None
        else None
    )

    override = create_operation_guard_override(
        db,
        action_type=_require_action(args),
        manager_user_id=_require_manager(args),
        fund_id=fund_id,
        settlement_batch_id=args.settlement_batch_id,
        request_id=args.request_id,
        idempotency_key=args.idempotency_key,
        max_amount_usdt=max_amount,
        ttl_minutes=args.ttl_minutes,
        reason=args.reason,
        payload={
            "source": "operation_guard_cli",
            "no_external_action": True,
        },
    )

    db.commit()

    print(
        {
            "operation": "create_override",
            "id": int(override.id),
            "scope_key": override.scope_key,
            "scope_type": override.scope_type,
            "fund_id": override.fund_id,
            "action_type": override.action_type,
            "status": override.status,
            "manager_user_id": int(override.manager_user_id),
            "settlement_batch_id": override.settlement_batch_id,
            "request_id": override.request_id,
            "max_amount_usdt": (
                str(override.max_amount_usdt)
                if override.max_amount_usdt is not None
                else None
            ),
            "starts_at": override.starts_at.isoformat(),
            "expires_at": override.expires_at.isoformat(),
            "reason": override.reason,
            "committed": True,
            "no_external_action": True,
        }
    )


def _revoke_override(db, args: argparse.Namespace) -> None:
    if args.override_id is None:
        raise OperationGuardError("--override-id is required")

    override = revoke_operation_guard_override(
        db,
        override_id=int(args.override_id),
        manager_user_id=_require_manager(args),
        reason=args.reason,
    )

    db.commit()

    print(
        {
            "operation": "revoke_override",
            "id": int(override.id),
            "status": override.status,
            "revoked_at": (
                override.revoked_at.isoformat()
                if override.revoked_at is not None
                else None
            ),
            "reason": override.reason,
            "committed": True,
            "no_external_action": True,
        }
    )


def main() -> None:
    args = _parse_args()
    db = SessionLocal()

    try:
        if args.list:
            _list_rows(db)
            db.rollback()
            return

        if args.set_state:
            _set_state(db, args)
            return

        if args.create_override:
            _create_override(db, args)
            return

        if args.revoke_override:
            _revoke_override(db, args)
            return

        raise OperationGuardError("Unsupported CLI command")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()