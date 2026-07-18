from __future__ import annotations

import asyncio
import json
import sys
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from sqlalchemy import create_engine, func, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.db import Base
from app.models import (
    Fund,
    FundNegativeBybitFlow,
    FundNegativeFinalizationBatch,
    FundNegativePayoutBatch,
    FundNegativePayoutLeg,
    FundOperatorAction,
    FundNegativeSaleBatch,
    FundNegativeSaleLeg,
    FundOrder,
    FundRuntimeState,
    FundSettlementBatch,
    FundWallet,
    User,
    UserFundPosition,
    UserFundPositionStats,
    UserWallet,
)
from app.settlement.batch_service import _calculate_batch_fields
from app.settlement import accounting_service
from app.settlement import negative_finalization
from app.settlement import negative_bybit_flow
from app.settlement import negative_payout_flow
from app.settlement import negative_sale_execution
from app.settlement import positive_net_service
from app.settlement.accounting_service import (
    SettlementAccountingError,
    SettlementShareQuantityError,
    finalize_positive_net_accounting,
    validate_positive_net_share_preflight,
    validate_settlement_share_state_before_external,
)
from app.settlement.share_quantity import (
    BUY_SHARE_QUANTITY_BELOW_MINIMUM_ERROR,
    SHARE_QUANTUM,
    RedeemSharePrecisionError,
    ShareQuantityError,
    calculate_buy_share_quantity,
    calculate_successful_buy_share_quantity,
    require_share_quantity_4dp_aligned,
    validate_redeem_share_input_precision,
)
from app.trading import order_service
from app.trading import routes as trading_routes
from app.trading.order_service import (
    TradingOrderError,
    create_redeem_order,
)


D = Decimal
NOW = datetime(2043, 1, 1, 12, 0, tzinfo=timezone.utc)
TEST_SCHEMA_PREFIX = "wb_stage26_3_12p_"

TEST_TABLES = [
    User.__table__,
    Fund.__table__,
    FundSettlementBatch.__table__,
    FundRuntimeState.__table__,
    FundWallet.__table__,
    FundNegativeSaleBatch.__table__,
    FundNegativeSaleLeg.__table__,
    FundNegativeBybitFlow.__table__,
    FundOperatorAction.__table__,
    FundNegativePayoutBatch.__table__,
    FundNegativePayoutLeg.__table__,
    FundNegativeFinalizationBatch.__table__,
    FundOrder.__table__,
    UserFundPosition.__table__,
    UserFundPositionStats.__table__,
    UserWallet.__table__,
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


@contextmanager
def patched_attr(
    obj: Any,
    name: str,
    value: Any,
) -> Iterator[None]:
    old_value = getattr(obj, name)
    setattr(obj, name, value)
    try:
        yield
    finally:
        setattr(obj, name, old_value)


def assert_safe_local_postgres_url(database_url: str) -> None:
    url = make_url(database_url)
    backend = url.get_backend_name().lower()
    host = (url.host or "").strip().lower()
    database = (url.database or "").strip().lower()

    require(backend == "postgresql", "Verifier requires PostgreSQL")
    require(
        host in {"localhost", "127.0.0.1", "::1"},
        f"Refusing non-local database host: {host or '<empty>'}",
    )
    require(database != "", "Database name is required")
    require(
        not any(token in database for token in ("prod", "production", "live")),
        f"Refusing production-like database name: {database}",
    )


def create_test_schema() -> tuple[
    Any,
    Any,
    sessionmaker,
    str,
]:
    database_url = str(settings.DATABASE_URL)
    assert_safe_local_postgres_url(database_url)

    schema = TEST_SCHEMA_PREFIX + uuid.uuid4().hex[:12]
    admin_engine = create_engine(
        database_url,
        pool_pre_ping=True,
    )
    test_engine = None

    try:
        with admin_engine.begin() as conn:
            stale_schemas = conn.execute(
                text(
                    """
                    SELECT schema_name
                    FROM information_schema.schemata
                    WHERE schema_name LIKE :pattern
                    """
                ),
                {"pattern": f"{TEST_SCHEMA_PREFIX}%"},
            ).scalars().all()

            for stale_schema in stale_schemas:
                if str(stale_schema).startswith(
                    TEST_SCHEMA_PREFIX
                ):
                    conn.execute(
                        text(
                            f'DROP SCHEMA IF EXISTS '
                            f'"{stale_schema}" CASCADE'
                        )
                    )

            conn.execute(
                text(f'CREATE SCHEMA "{schema}"')
            )

        test_engine = create_engine(
            database_url,
            pool_pre_ping=True,
            connect_args={
                "options": f"-csearch_path={schema}"
            },
        )

        Base.metadata.create_all(
            bind=test_engine,
        )

        with test_engine.begin() as conn:
            actual_schema = conn.execute(
                text("SELECT current_schema()")
            ).scalar_one()
            require(
                actual_schema == schema,
                (
                    "Unexpected search_path schema: "
                    f"{actual_schema}"
                ),
            )

        TestSessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            expire_on_commit=False,
            bind=test_engine,
        )

        return (
            admin_engine,
            test_engine,
            TestSessionLocal,
            schema,
        )
    except Exception:
        if test_engine is not None:
            test_engine.dispose()

        with admin_engine.begin() as conn:
            conn.execute(
                text(
                    f'DROP SCHEMA IF EXISTS '
                    f'"{schema}" CASCADE'
                )
            )

        admin_engine.dispose()
        raise


def drop_test_schema(
    admin_engine: Any,
    test_engine: Any,
    schema: str,
) -> None:
    test_engine.dispose()
    with admin_engine.begin() as conn:
        conn.execute(
            text("SET LOCAL lock_timeout = '5s'")
        )
        conn.execute(
            text(
                f'DROP SCHEMA IF EXISTS '
                f'"{schema}" CASCADE'
            )
        )
    admin_engine.dispose()


def new_user(
    db: Session,
    *,
    suffix: str,
) -> User:
    user = User(
        created_at=NOW,
        email=(
            f"stage26-3-12p-{suffix}-"
            f"{uuid.uuid4().hex[:8]}@example.test"
        ),
        first_name="Stage26P",
        last_name=suffix,
        password_hash="not-a-real-password-hash",
        is_active=True,
        is_email_verified=True,
        two_factor_enabled=True,
        account_type="basic",
        compliance_status="ok",
    )
    db.add(user)
    db.flush()
    return user


def new_fund(
    db: Session,
    *,
    suffix: str,
    shares_outstanding: Decimal = D("1.0000"),
) -> Fund:
    code = (
        f"p_{suffix}_{uuid.uuid4().hex[:6]}"
    ).lower()
    fund = Fund(
        code=code,
        name_ru=code,
        name_en=code,
        category="test",
        sort_order=0,
        is_active=True,
        shares_outstanding_current=shares_outstanding,
    )
    db.add(fund)
    db.flush()
    return fund


def new_position(
    db: Session,
    *,
    user: User,
    fund: Fund,
    shares: Decimal = D("2000.0000"),
    shares_reserved: Decimal = D("0.0000"),
    avg_entry_price_usdt: Decimal | None = None,
) -> UserFundPosition:
    position = UserFundPosition(
        user_id=int(user.id),
        fund_id=int(fund.id),
        shares=shares,
        shares_reserved=shares_reserved,
    )
    db.add(position)
    db.flush()

    resolved_average = (
        D(str(avg_entry_price_usdt))
        if avg_entry_price_usdt is not None
        else (
            D("1")
            if D(str(shares)) > D("0")
            else D("0")
        )
    )

    stats = UserFundPositionStats(
        user_id=int(user.id),
        fund_id=int(fund.id),
        avg_entry_price_usdt=resolved_average,
        updated_at=NOW,
    )
    db.add(stats)
    db.flush()

    return position



def new_user_wallet(
    db: Session,
    *,
    user: User,
    balance: Decimal = D("100"),
    reserved: Decimal = D("0"),
) -> UserWallet:
    wallet = UserWallet(
        user_id=int(user.id),
        blockchain="BSC",
        address="0x" + uuid.uuid4().hex[:40],
        encrypted_private_key="test-only-key",
        usdt_balance=balance,
        usdt_reserved=reserved,
        compliance_status="ok",
        is_active=True,
    )
    db.add(wallet)
    db.flush()
    return wallet


def new_settlement_batch(
    db: Session,
    *,
    fund: Fund,
    suffix: int,
    settlement_price: Decimal,
    shares_before: Decimal,
    planned_issue: Decimal,
    planned_redeem: Decimal = D("0"),
    status: str = "awaiting_positive_net_execution",
) -> FundSettlementBatch:
    ts = NOW + timedelta(days=suffix)
    batch = FundSettlementBatch(
        fund_id=int(fund.id),
        settlement_date=date(2043, 1, 1)
        + timedelta(days=suffix),
        cutoff_ts=ts,
        settlement_ts=ts,
        price_ts=ts,
        settlement_price_usdt=settlement_price,
        nav_usdt=settlement_price * shares_before,
        shares_outstanding_before=shares_before,
        total_buy_usdt=D("0"),
        total_redeem_shares=planned_redeem,
        total_redeem_usdt=D("0"),
        net_cash_usdt=D("0"),
        planned_shares_to_issue=planned_issue,
        planned_shares_to_redeem=planned_redeem,
        planned_net_shares_change=(
            planned_issue - planned_redeem
        ),
        status=status,
        pricing_locked_at=NOW,
        pricing_unlocked_at=None,
        accounting_finalized_at=None,
    )
    db.add(batch)
    db.flush()
    return batch


def new_buy_order(
    db: Session,
    *,
    user: User,
    fund: Fund,
    batch: FundSettlementBatch | None,
    amount: Decimal,
    shares: Decimal | None,
    status: str = "settling",
) -> FundOrder:
    order = FundOrder(
        user_id=int(user.id),
        fund_id=int(fund.id),
        side="buy",
        amount_usdt=amount,
        shares=shares,
        price_usdt=None,
        status=status,
        settlement_batch_id=(
            int(batch.id)
            if batch is not None
            else None
        ),
        created_at=NOW,
    )
    db.add(order)
    db.flush()
    return order

def order_count(
    db: Session,
    *,
    user_id: int,
    fund_id: int,
) -> int:
    return int(
        db.query(func.count(FundOrder.id))
        .filter(
            FundOrder.user_id == int(user_id),
            FundOrder.fund_id == int(fund_id),
        )
        .scalar()
        or 0
    )


def get_position(
    db: Session,
    *,
    user_id: int,
    fund_id: int,
) -> UserFundPosition:
    position = (
        db.query(UserFundPosition)
        .filter(
            UserFundPosition.user_id
            == int(user_id),
            UserFundPosition.fund_id
            == int(fund_id),
        )
        .one()
    )
    return position


def test_share_floor_helper() -> dict[str, Any]:
    require(
        SHARE_QUANTUM == D("0.0001"),
        f"Unexpected SHARE_QUANTUM={SHARE_QUANTUM}",
    )

    result = calculate_buy_share_quantity(
        amount_usdt=D("10"),
        settlement_price_usdt=D("634.25"),
    )

    require(
        result.full_investment_usdt == D("10"),
        "Full investment amount changed",
    )
    require(
        result.issued_shares == D("0.0157"),
        (
            "Unexpected issued shares: "
            f"{result.issued_shares}"
        ),
    )
    require(
        result.theoretical_shares
        > result.issued_shares,
        (
            "Theoretical shares must exceed "
            "floored shares"
        ),
    )
    require(
        D("0")
        <= result.rounding_effect_shares
        < SHARE_QUANTUM,
        (
            "Rounding effect must be inside "
            "one share quantum"
        ),
    )
    require(
        (
            result
            .rounding_effect_usdt_at_settlement_price
        )
        == D("0.042275"),
        "Unexpected rounding effect in USDT",
    )

    rejected_values = [
        (D("-1"), D("1")),
        (D("1"), D("0")),
        (D("NaN"), D("1")),
        (D("Infinity"), D("1")),
    ]
    for amount, price in rejected_values:
        try:
            calculate_buy_share_quantity(
                amount_usdt=amount,
                settlement_price_usdt=price,
            )
        except ShareQuantityError:
            continue
        raise AssertionError(
            "Invalid helper input accepted: "
            f"amount={amount}, price={price}"
        )

    print(
        "STAGE26_3_12P_SHARE_FLOOR_4DP_HELPER_OK"
    )
    return result.audit_dict()


def test_full_buy_amount_invested() -> dict[str, Any]:
    first = calculate_successful_buy_share_quantity(
        amount_usdt=D("10"),
        settlement_price_usdt=D("634.25"),
    )
    second = calculate_successful_buy_share_quantity(
        amount_usdt=D("10"),
        settlement_price_usdt=D("634.25"),
    )

    audit = first.audit_dict()

    require(
        (
            first.full_investment_usdt
            + second.full_investment_usdt
        )
        == D("20"),
        "Full buy cash was not preserved",
    )
    require(
        audit[
            "rounding_effect_is_informational_only"
        ]
        is True,
        "Rounding effect must be informational only",
    )
    require(
        audit[
            "rounding_effect_retained_in_fund_nav"
        ]
        is True,
        "Rounding effect must remain in fund NAV",
    )
    require(
        audit["rounding_effect_refundable"] is False,
        "Rounding effect must not be refundable",
    )
    require(
        audit["rounding_effect_is_fee"] is False,
        "Rounding effect must not be a fee",
    )

    print(
        "STAGE26_3_12P_FULL_BUY_AMOUNT_INVESTED_OK"
    )
    return {
        "total_full_investment_usdt": (
            first.full_investment_usdt
            + second.full_investment_usdt
        ),
        "total_issued_shares": (
            first.issued_shares
            + second.issued_shares
        ),
        "first_order_audit": audit,
    }


def test_per_order_buy_share_floor() -> dict[str, Any]:
    orders = [
        SimpleNamespace(
            id=1,
            side="buy",
            amount_usdt=D("10"),
            shares=None,
        ),
        SimpleNamespace(
            id=2,
            side="buy",
            amount_usdt=D("10"),
            shares=None,
        ),
    ]

    fields = _calculate_batch_fields(
        orders=orders,
        settlement_price_usdt=D("634.25"),
    )

    aggregate_floor = (
        D("20") / D("634.25")
    ).quantize(SHARE_QUANTUM)

    require(
        fields["total_buy_usdt"] == D("20"),
        "Batch total_buy_usdt changed",
    )
    require(
        fields["planned_shares_to_issue"]
        == D("0.0314"),
        "Per-order share floor total mismatch",
    )
    require(
        aggregate_floor == D("0.0315"),
        "Canonical contrast example changed",
    )
    require(
        fields["planned_shares_to_issue"]
        != aggregate_floor,
        (
            "Batch incorrectly floored "
            "aggregate buy amount"
        ),
    )
    require(
        set(
            fields[
                "buy_share_quantities_by_order_id"
            ]
        )
        == {1, 2},
        "Per-order audit mapping is incomplete",
    )

    print(
        "STAGE26_3_12P_PER_ORDER_BUY_SHARE_FLOOR_OK"
    )
    return fields


def test_exact_division_no_reduction() -> dict[str, Any]:
    result = calculate_successful_buy_share_quantity(
        amount_usdt=D("12.34"),
        settlement_price_usdt=D("100"),
    )

    require(
        result.theoretical_shares == D("0.1234"),
        (
            "Unexpected exact theoretical shares: "
            f"{result.theoretical_shares}"
        ),
    )
    require(
        result.issued_shares == D("0.1234"),
        (
            "Exact 4dp quantity was unnecessarily "
            f"reduced: {result.issued_shares}"
        ),
    )
    require(
        result.rounding_effect_shares == D("0"),
        (
            "Exact division produced share "
            f"rounding effect: "
            f"{result.rounding_effect_shares}"
        ),
    )
    require(
        (
            result
            .rounding_effect_usdt_at_settlement_price
        )
        == D("0"),
        (
            "Exact division produced USDT "
            "rounding effect"
        ),
    )
    require(
        result.full_investment_usdt == D("12.34"),
        "Exact division changed investment amount",
    )

    print(
        "STAGE26_3_12P_EXACT_DIVISION_NO_REDUCTION_OK"
    )
    return result.audit_dict()


def test_zero_share_planning_rejected() -> str:
    order = SimpleNamespace(
        id=91,
        side="buy",
        amount_usdt=D("0.01"),
        shares=None,
    )

    try:
        _calculate_batch_fields(
            orders=[order],
            settlement_price_usdt=D("634.25"),
        )
    except ShareQuantityError as exc:
        error = str(exc)
        require(
            (
                BUY_SHARE_QUANTITY_BELOW_MINIMUM_ERROR
                in error
            ),
            f"Unexpected zero-share error: {error}",
        )
        print("ZERO_SHARE_PLANNING_REJECTED: OK")
        return error

    raise AssertionError(
        "Zero-share issuance was accepted"
    )


def test_redeem_precision_helper_cases() -> dict[str, Any]:
    accepted = {
        "1": D("1"),
        "1.2": D("1.2"),
        "1.2300": D("1.2300"),
        "0.0001": D("0.0001"),
        "999.9999": D("999.9999"),
    }
    for raw, expected in accepted.items():
        actual = validate_redeem_share_input_precision(
            raw
        )
        require(
            actual == expected,
            (
                "Accepted redeem value changed: "
                f"{raw} -> {actual}"
            ),
        )

    rejected = [
        "0.00001",
        "1.23456",
        "1.00000",
        "999.99999",
    ]
    for raw in rejected:
        try:
            validate_redeem_share_input_precision(
                raw
            )
        except RedeemSharePrecisionError:
            continue
        raise AssertionError(
            "Redeem value with more than 4 "
            f"fractional digits accepted: {raw}"
        )

    require(
        require_share_quantity_4dp_aligned(
            D("12.3400"),
            field_name="stored_shares",
        )
        == D("12.3400"),
        "Aligned stored share quantity rejected",
    )

    try:
        require_share_quantity_4dp_aligned(
            D("12.34001"),
            field_name="stored_shares",
        )
    except ShareQuantityError:
        pass
    else:
        raise AssertionError(
            "Historical stored share tail was "
            "silently accepted"
        )

    print("REDEEM_PRECISION_HELPER_CASES: OK")
    return {
        "accepted": list(accepted),
        "rejected": rejected,
    }


def test_redeem_backend_service_precision(
    db: Session,
) -> dict[str, Any]:
    fund = new_fund(
        db,
        suffix="redeem-service",
    )

    rejected_user = new_user(
        db,
        suffix="redeem-rejected",
    )
    rejected_position = new_position(
        db,
        user=rejected_user,
        fund=fund,
    )
    db.commit()

    rejected_cases = [
        "0.00001",
        "1.23456",
        "1.00000",
        "999.99999",
    ]
    rejected_results: list[dict[str, Any]] = []

    with patched_attr(
        order_service,
        "is_order_entry_enabled_for_fund_code",
        lambda _code: True,
    ):
        for raw in rejected_cases:
            before_count = order_count(
                db,
                user_id=int(rejected_user.id),
                fund_id=int(fund.id),
            )
            before_shares = D(
                str(rejected_position.shares)
            )
            before_reserved = D(
                str(rejected_position.shares_reserved)
            )

            try:
                create_redeem_order(
                    db=db,
                    user=rejected_user,
                    fund_code=str(fund.code),
                    shares=raw,
                    lang="en",
                    commit=False,
                )
            except TradingOrderError as exc:
                require(
                    exc.error_key
                    == "redeem_shares_precision_exceeded",
                    (
                        "Unexpected backend error key for "
                        f"{raw}: {exc.error_key}"
                    ),
                )
            else:
                raise AssertionError(
                    "Backend accepted redeem precision "
                    f"violation: {raw}"
                )

            db.expire_all()

            after_count = order_count(
                db,
                user_id=int(rejected_user.id),
                fund_id=int(fund.id),
            )
            stored_position = get_position(
                db,
                user_id=int(rejected_user.id),
                fund_id=int(fund.id),
            )

            require(
                after_count == before_count,
                (
                    "Rejected redeem created FundOrder: "
                    f"{raw}"
                ),
            )
            require(
                D(str(stored_position.shares))
                == before_shares,
                (
                    "Rejected redeem changed position "
                    f"shares: {raw}"
                ),
            )
            require(
                D(str(stored_position.shares_reserved))
                == before_reserved,
                (
                    "Rejected redeem changed "
                    f"shares_reserved: {raw}"
                ),
            )

            rejected_results.append(
                {
                    "raw": raw,
                    "error_key": (
                        "redeem_shares_precision_exceeded"
                    ),
                    "fund_order_created": False,
                    "shares_changed": False,
                    "shares_reserved_changed": False,
                }
            )

    accepted_user = new_user(
        db,
        suffix="redeem-accepted",
    )
    new_position(
        db,
        user=accepted_user,
        fund=fund,
    )
    db.commit()

    accepted_cases = {
        "1": D("1"),
        "1.2": D("1.2"),
        "1.2300": D("1.2300"),
        "0.0001": D("0.0001"),
        "999.9999": D("999.9999"),
    }
    accepted_results: list[dict[str, Any]] = []

    with patched_attr(
        order_service,
        "is_order_entry_enabled_for_fund_code",
        lambda _code: True,
    ):
        for raw, expected in accepted_cases.items():
            before_count = order_count(
                db,
                user_id=int(accepted_user.id),
                fund_id=int(fund.id),
            )

            result = create_redeem_order(
                db=db,
                user=accepted_user,
                fund_code=str(fund.code),
                shares=raw,
                lang="en",
                commit=False,
            )

            after_count = order_count(
                db,
                user_id=int(accepted_user.id),
                fund_id=int(fund.id),
            )
            stored_position = get_position(
                db,
                user_id=int(accepted_user.id),
                fund_id=int(fund.id),
            )
            order = (
                db.query(FundOrder)
                .filter(
                    FundOrder.user_id
                    == int(accepted_user.id),
                    FundOrder.fund_id
                    == int(fund.id),
                )
                .order_by(FundOrder.id.desc())
                .first()
            )

            require(
                after_count == before_count + 1,
                (
                    "Valid redeem did not create exactly "
                    f"one FundOrder: {raw}"
                ),
            )
            require(
                order is not None,
                f"Valid redeem order missing: {raw}",
            )
            require(
                D(str(order.shares)) == expected,
                (
                    "Valid redeem stored unexpected "
                    f"shares: {raw} -> {order.shares}"
                ),
            )
            require(
                D(str(stored_position.shares))
                == D("2000.0000"),
                (
                    "Valid redeem changed owned shares "
                    f"before settlement: {raw}"
                ),
            )
            require(
                D(str(stored_position.shares_reserved))
                == expected,
                (
                    "Valid redeem reserve mismatch: "
                    f"{raw} -> "
                    f"{stored_position.shares_reserved}"
                ),
            )
            require(
                result["status"] == "ok",
                (
                    "Valid redeem returned non-ok "
                    f"result: {raw}"
                ),
            )

            accepted_results.append(
                {
                    "raw": raw,
                    "stored_shares": str(order.shares),
                    "shares_reserved": str(
                        stored_position.shares_reserved
                    ),
                }
            )

            db.rollback()
            db.expire_all()

    print(
        "STAGE26_3_12P_REDEEM_BACKEND_MAX_4DP_OK"
    )
    print("STAGE26_3_12P_EXISTING_4DP_REDEEM_OK")

    return {
        "accepted": accepted_results,
        "rejected": rejected_results,
    }


async def asgi_json_post(
    app: FastAPI,
    *,
    path: str,
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode("utf-8")
    request_delivered = False
    sent_messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal request_delivered

        if not request_delivered:
            request_delivered = True
            return {
                "type": "http.request",
                "body": body,
                "more_body": False,
            }

        return {
            "type": "http.disconnect",
        }

    async def send(
        message: dict[str, Any],
    ) -> None:
        sent_messages.append(message)

    scope: dict[str, Any] = {
        "type": "http",
        "asgi": {
            "version": "3.0",
            "spec_version": "2.3",
        },
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (
                b"content-length",
                str(len(body)).encode("ascii"),
            ),
        ],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }

    await app(scope, receive, send)

    response_start = next(
        (
            message
            for message in sent_messages
            if message.get("type")
            == "http.response.start"
        ),
        None,
    )
    require(
        response_start is not None,
        (
            "ASGI POST did not emit "
            "http.response.start"
        ),
    )

    response_body = b"".join(
        message.get("body", b"")
        for message in sent_messages
        if message.get("type")
        == "http.response.body"
    )

    status_code = int(response_start["status"])

    try:
        response_payload = json.loads(
            response_body.decode("utf-8")
        )
    except Exception as exc:
        raise AssertionError(
            "ASGI POST returned invalid JSON: "
            f"{response_body!r}"
        ) from exc

    require(
        isinstance(response_payload, dict),
        (
            "ASGI POST response JSON must be "
            f"an object: {response_payload!r}"
        ),
    )

    return status_code, response_payload


def test_direct_api_redeem_precision(
    TestSessionLocal: sessionmaker,
) -> dict[str, Any]:
    setup_db = TestSessionLocal()
    try:
        fund = new_fund(
            setup_db,
            suffix="direct-api",
        )
        user = new_user(
            setup_db,
            suffix="direct-api",
        )
        position = new_position(
            setup_db,
            user=user,
            fund=fund,
        )
        setup_db.commit()

        fund_id = int(fund.id)
        user_id = int(user.id)
        fund_code = str(fund.code)
        shares_before = D(str(position.shares))
        reserved_before = D(
            str(position.shares_reserved)
        )
        orders_before = order_count(
            setup_db,
            user_id=user_id,
            fund_id=fund_id,
        )
    finally:
        setup_db.close()

    user_proxy = SimpleNamespace(
        id=user_id,
        is_active=True,
        compliance_status="ok",
        account_type="basic",
    )

    app = FastAPI()
    app.include_router(trading_routes.router)

    def override_get_db():
        request_db = TestSessionLocal()
        try:
            yield request_db
        finally:
            request_db.close()

    def override_get_user():
        return user_proxy

    app.dependency_overrides[
        trading_routes.get_db
    ] = override_get_db
    app.dependency_overrides[
        trading_routes.get_optional_user
    ] = override_get_user

    with patched_attr(
        order_service,
        "is_order_entry_enabled_for_fund_code",
        lambda _code: True,
    ):
        status_code, payload = asyncio.run(
            asgi_json_post(
                app,
                path="/api/trading/orders/redeem",
                payload={
                    "fund_code": fund_code,
                    "shares": "1.23456",
                },
            )
        )

    require(
        status_code == 400,
        (
            "Direct API precision violation returned "
            f"status {status_code}: {payload}"
        ),
    )
    require(
        payload.get("status") == "error",
        (
            "Direct API response is not error: "
            f"{payload}"
        ),
    )
    require(
        payload.get("error")
        == "redeem_shares_precision_exceeded",
        (
            "Direct API returned unexpected "
            f"error key: {payload}"
        ),
    )

    verify_db = TestSessionLocal()
    try:
        orders_after = order_count(
            verify_db,
            user_id=user_id,
            fund_id=fund_id,
        )
        stored_position = get_position(
            verify_db,
            user_id=user_id,
            fund_id=fund_id,
        )

        require(
            orders_after == orders_before,
            (
                "Direct API precision rejection "
                "created FundOrder"
            ),
        )
        require(
            D(str(stored_position.shares))
            == shares_before,
            (
                "Direct API precision rejection "
                "changed position shares"
            ),
        )
        require(
            D(str(stored_position.shares_reserved))
            == reserved_before,
            (
                "Direct API precision rejection "
                "changed shares_reserved"
            ),
        )
    finally:
        verify_db.close()

    print(
        "STAGE26_3_12P_REDEEM_DIRECT_API_5DP_REJECTED_OK"
    )
    return {
        "http_status": status_code,
        "error_key": payload.get("error"),
        "fund_order_created": False,
        "shares_changed": False,
        "shares_reserved_changed": False,
        "transport": "direct_asgi_post",
    }


def test_positive_net_accounting_and_idempotency(
    db: Session,
    *,
    suffix: int,
) -> dict[str, Any]:
    fund = new_fund(
        db,
        suffix="positive",
        shares_outstanding=D("1.0000"),
    )
    user = new_user(
        db,
        suffix="positive",
    )

    batch = new_settlement_batch(
        db,
        fund=fund,
        suffix=suffix,
        settlement_price=D("634.25"),
        shares_before=D("1.0000"),
        planned_issue=D("0.0314"),
        status="awaiting_positive_net_execution",
    )
    batch.total_buy_usdt = D("20")
    batch.total_redeem_shares = D("0")
    batch.total_redeem_usdt = D("0")
    batch.net_cash_usdt = D("20")
    batch.planned_shares_to_issue = D("0.0314")
    batch.planned_shares_to_redeem = D("0")
    batch.planned_net_shares_change = D("0.0314")

    runtime_state = FundRuntimeState(
        fund_id=int(fund.id),
        pricing_locked=True,
        pricing_lock_reason="settlement",
        pricing_lock_batch_id=int(batch.id),
        pricing_locked_at=NOW,
        pricing_unlocked_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(runtime_state)

    first_order = new_buy_order(
        db,
        user=user,
        fund=fund,
        batch=batch,
        amount=D("10"),
        shares=D("0.0157"),
        status="buy_collected",
    )
    second_order = new_buy_order(
        db,
        user=user,
        fund=fund,
        batch=batch,
        amount=D("10"),
        shares=D("0.0157"),
        status="buy_collected",
    )
    db.commit()

    counters = {
        "seller_payout": 0,
        "positive_bsc_transfer": 0,
        "bybit_deposit": 0,
        "internal_transfer": 0,
    }

    def fake_seller_payouts(
        fake_db: Session,
        *,
        batch_id: int,
        dry_run: bool,
        mock_confirm: bool,
    ) -> Any:
        counters["seller_payout"] += 1

        stored_batch = fake_db.get(
            FundSettlementBatch,
            int(batch_id),
        )
        stored_batch.seller_payouts_completed_at = NOW
        fake_db.add(stored_batch)
        fake_db.flush()

        return SimpleNamespace(
            seller_payouts_completed=True,
        )

    def fake_positive_transfer(
        fake_db: Session,
        *,
        batch_id: int,
        dry_run: bool,
        mock_confirm: bool,
    ) -> Any:
        counters["positive_bsc_transfer"] += 1

        stored_batch = fake_db.get(
            FundSettlementBatch,
            int(batch_id),
        )
        stored_batch.bybit_deposit_tx_hash = (
            "0x" + "11" * 32
        )
        fake_db.add(stored_batch)
        fake_db.flush()

        return SimpleNamespace(
            transfer_status="confirmed",
        )

    def fake_bybit_deposit(
        fake_db: Session,
        *,
        batch_id: int,
        master_client: Any,
        mock_confirm: bool,
    ) -> bool:
        counters["bybit_deposit"] += 1

        stored_batch = fake_db.get(
            FundSettlementBatch,
            int(batch_id),
        )
        stored_batch.bybit_deposit_confirmed_at = NOW
        fake_db.add(stored_batch)
        fake_db.flush()
        return True

    def fake_internal_transfer(
        fake_db: Session,
        *,
        batch: FundSettlementBatch,
        fund_client_factory: Any,
        dry_run: bool,
        mock_bybit: bool,
    ) -> bool:
        counters["internal_transfer"] += 1

        batch.bybit_internal_transfer_completed_at = NOW
        batch.bybit_internal_transfer_status = (
            "SUCCESS"
        )
        batch.bybit_internal_transfer_error = None
        fake_db.add(batch)
        fake_db.flush()
        return True

    with patched_attr(
        positive_net_service,
        "process_seller_payouts_for_batch",
        fake_seller_payouts,
    ), patched_attr(
        positive_net_service,
        "send_or_confirm_positive_net_transfer",
        fake_positive_transfer,
    ), patched_attr(
        positive_net_service,
        "confirm_bybit_deposit_for_batch",
        fake_bybit_deposit,
    ), patched_attr(
        positive_net_service,
        "_internal_transfer_ready_or_skipped",
        fake_internal_transfer,
    ), patched_attr(
        positive_net_service,
        "_send_alert",
        lambda _text: None,
    ):
        result = (
            positive_net_service.process_positive_net_batch(
                db,
                batch_id=int(batch.id),
                master_client=None,
                fund_client_factory=None,
                dry_run=False,
                mock_chain=False,
                mock_bybit=False,
                finalize_accounting=True,
            )
        )
        db.commit()

        counters_after_first_run = dict(counters)

        repeated = (
            positive_net_service.process_positive_net_batch(
                db,
                batch_id=int(batch.id),
                master_client=None,
                fund_client_factory=None,
                dry_run=False,
                mock_chain=False,
                mock_bybit=False,
                finalize_accounting=True,
            )
        )
        db.commit()

    db.expire_all()

    stored_batch = db.get(
        FundSettlementBatch,
        int(batch.id),
    )
    stored_fund = db.get(
        Fund,
        int(fund.id),
    )
    stored_position = get_position(
        db,
        user_id=int(user.id),
        fund_id=int(fund.id),
    )
    stored_orders = (
        db.query(FundOrder)
        .filter(
            FundOrder.settlement_batch_id
            == int(batch.id)
        )
        .order_by(FundOrder.id.asc())
        .all()
    )

    require(
        result.accounting_finalized is True,
        "Positive full flow did not finalize accounting",
    )
    require(
        result.accounting_result is not None,
        "Positive full flow has no accounting result",
    )
    require(
        result.accounting_result.buyer_shares_issued
        == D("0.0314"),
        (
            "Positive full flow issued unexpected "
            f"shares: "
            f"{result.accounting_result.buyer_shares_issued}"
        ),
    )
    require(
        [
            D(str(order.shares))
            for order in stored_orders
        ]
        == [D("0.0157"), D("0.0157")],
        "Positive buy orders were not floored per order",
    )
    require(
        D(str(stored_position.shares))
        == D("0.0314"),
        (
            "Positive position mismatch: "
            f"{stored_position.shares}"
        ),
    )
    require(
        D(str(stored_fund.shares_outstanding_current))
        == D("1.0314"),
        (
            "Positive shares outstanding mismatch: "
            f"{stored_fund.shares_outstanding_current}"
        ),
    )
    require(
        D(str(stored_batch.total_buy_usdt))
        == D("20"),
        (
            "Complete positive buy cash was not retained: "
            f"{stored_batch.total_buy_usdt}"
        ),
    )
    require(
        D(str(stored_batch.net_cash_usdt))
        == D("20"),
        (
            "Positive net cash mismatch: "
            f"{stored_batch.net_cash_usdt}"
        ),
    )
    require(
        counters_after_first_run
        == {
            "seller_payout": 1,
            "positive_bsc_transfer": 1,
            "bybit_deposit": 1,
            "internal_transfer": 1,
        },
        (
            "Unexpected positive external boundary calls: "
            f"{counters_after_first_run}"
        ),
    )
    require(
        counters == counters_after_first_run,
        (
            "Idempotent positive rerun repeated external "
            f"calls: {counters}"
        ),
    )
    require(
        repeated.accounting_finalized is True,
        "Positive rerun did not report finalized state",
    )
    require(
        repeated.accounting_result is None,
        (
            "Positive idempotent rerun unexpectedly "
            "repeated accounting"
        ),
    )
    require(
        D(str(stored_position.shares))
        == D("0.0314"),
        "Positive rerun changed position shares",
    )
    require(
        D(str(stored_fund.shares_outstanding_current))
        == D("1.0314"),
        "Positive rerun changed fund shares",
    )

    print(
        "STAGE26_3_12P_R1_FULL_BUY_CASH_RECONCILIATION_OK"
    )
    print(
        "STAGE26_3_12P_R1_POSITIVE_FULL_FLOW_4DP_OK"
    )
    print(
        "STAGE26_3_12P_POSITIVE_NET_BUY_SHARES_4DP_OK"
    )
    print(
        "STAGE26_3_12P_SHARES_OUTSTANDING_4DP_OK"
    )
    print(
        "STAGE26_3_12P_USER_POSITION_4DP_OK"
    )
    print(
        "STAGE26_3_12P_IDEMPOTENT_ACCOUNTING_OK"
    )

    return {
        "batch_id": int(batch.id),
        "order_shares": [
            D(str(order.shares))
            for order in stored_orders
        ],
        "position_shares": D(
            str(stored_position.shares)
        ),
        "fund_shares": D(
            str(stored_fund.shares_outstanding_current)
        ),
        "total_buy_usdt": D(
            str(stored_batch.total_buy_usdt)
        ),
        "net_cash_usdt": D(
            str(stored_batch.net_cash_usdt)
        ),
        "external_calls_first_run": (
            counters_after_first_run
        ),
        "external_calls_after_rerun": dict(counters),
        "second_call_changed_state": False,
    }


def test_negative_net_buy_accounting(
    db: Session,
) -> dict[str, Any]:
    fund = new_fund(
        db,
        suffix="negative-mixed",
        shares_outstanding=D("1.0000"),
    )

    buy_user = new_user(
        db,
        suffix="negative-buy",
    )
    redeem_user = new_user(
        db,
        suffix="negative-redeem",
    )

    buy_wallet = new_user_wallet(
        db,
        user=buy_user,
        balance=D("100"),
        reserved=D("10"),
    )
    redeem_wallet = new_user_wallet(
        db,
        user=redeem_user,
        balance=D("0"),
        reserved=D("0"),
    )

    redeem_position = new_position(
        db,
        user=redeem_user,
        fund=fund,
        shares=D("0.1000"),
        shares_reserved=D("0.0200"),
    )

    batch = new_settlement_batch(
        db,
        fund=fund,
        suffix=40,
        settlement_price=D("634.25"),
        shares_before=D("1.0000"),
        planned_issue=D("0.0157"),
        planned_redeem=D("0.0200"),
        status="negative_net_payouts_confirmed",
    )
    batch.total_buy_usdt = D("10")
    batch.total_redeem_shares = D("0.0200")
    batch.total_redeem_usdt = D("12.6850")
    batch.net_cash_usdt = D("-2.6850")
    batch.planned_shares_to_issue = D("0.0157")
    batch.planned_shares_to_redeem = D("0.0200")
    batch.planned_net_shares_change = D("-0.0043")
    batch.total_net_user_payout_usdt = D("12.5000")
    batch.total_partial_month_fee_usdt = D("0.1850")
    batch.pricing_locked_at = NOW
    batch.pricing_unlocked_at = None

    runtime_state = FundRuntimeState(
        fund_id=int(fund.id),
        pricing_locked=True,
        pricing_lock_reason="settlement",
        pricing_lock_batch_id=int(batch.id),
        pricing_locked_at=NOW,
        pricing_unlocked_at=None,
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(runtime_state)

    buy_order = new_buy_order(
        db,
        user=buy_user,
        fund=fund,
        batch=batch,
        amount=D("10"),
        shares=D("0.0157"),
        status="processing",
    )

    redeem_order = FundOrder(
        user_id=int(redeem_user.id),
        fund_id=int(fund.id),
        side="redeem",
        amount_usdt=None,
        shares=D("0.0200"),
        price_usdt=None,
        gross_redeem_usdt=D("12.6850"),
        success_fee_usdt=D("0"),
        management_fee_usdt=D("0"),
        partial_month_fee_usdt=D("0.1850"),
        net_user_payout_usdt=D("12.5000"),
        net_price_usdt=D("625.0000"),
        status="processing",
        settlement_batch_id=int(batch.id),
        created_at=NOW,
    )
    db.add(redeem_order)
    db.flush()

    sale_batch = FundNegativeSaleBatch(
        settlement_batch_id=int(batch.id),
        fund_id=int(fund.id),
        status="sale_execution_completed",
        required_master_usdt=D("12.5000"),
        withdrawal_request_amount_usdt=D("12.5000"),
        total_net_user_payout_usdt=D("12.5000"),
        total_partial_month_fee_usdt=D("0.1850"),
        bybit_withdrawal_fee_usdt=D("0"),
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(sale_batch)
    db.flush()

    bybit_flow = FundNegativeBybitFlow(
        settlement_batch_id=int(batch.id),
        sale_batch_id=int(sale_batch.id),
        fund_id=int(fund.id),
        status="completed",
        coin="USDT",
        chain="BSC",
        required_master_usdt=D("12.5000"),
        withdrawal_request_amount_usdt=D("12.5000"),
        bybit_withdrawal_fee_usdt=D("0"),
        retained_fees_usdt=D("0.1850"),
        settlement_wallet_received_usdt=D("12.5000"),
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(bybit_flow)
    db.flush()

    payout_batch = FundNegativePayoutBatch(
        settlement_batch_id=int(batch.id),
        bybit_flow_id=int(bybit_flow.id),
        fund_id=int(fund.id),
        status="completed",
        coin="USDT",
        chain="BSC",
        expected_total_payout_usdt=D("12.5000"),
        planned_total_payout_usdt=D("12.5000"),
        confirmed_total_payout_usdt=D("12.5000"),
        payout_leg_count=1,
        confirmed_payout_leg_count=1,
        balance_refresh_status="confirmed",
        balance_refresh_json={
            "confirmed": True,
        },
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(payout_batch)
    db.flush()

    payout_leg = FundNegativePayoutLeg(
        payout_batch_id=int(payout_batch.id),
        settlement_batch_id=int(batch.id),
        bybit_flow_id=int(bybit_flow.id),
        fund_id=int(fund.id),
        user_id=int(redeem_user.id),
        user_wallet_id=int(redeem_wallet.id),
        to_user_wallet_id=int(redeem_wallet.id),
        status="balance_refreshed",
        coin="USDT",
        chain="BSC",
        to_address=str(redeem_wallet.address),
        amount_usdt=D("12.5000"),
        order_ids_json={
            "order_ids": [int(redeem_order.id)],
        },
        balance_refresh_json={
            "confirmed": True,
        },
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(payout_leg)
    db.commit()

    old_enabled = (
        settings.NEGATIVE_NET_FINALIZATION_ENABLED
    )
    old_unlock = (
        settings.NEGATIVE_NET_FINALIZATION_UNLOCK_PRICING
    )
    old_require_payout = (
        settings
        .NEGATIVE_NET_FINALIZATION_REQUIRE_PAYOUTS_CONFIRMED
    )

    settings.NEGATIVE_NET_FINALIZATION_ENABLED = True
    settings.NEGATIVE_NET_FINALIZATION_UNLOCK_PRICING = True
    settings.NEGATIVE_NET_FINALIZATION_REQUIRE_PAYOUTS_CONFIRMED = True

    try:
        result = (
            negative_finalization
            .finalize_negative_net_settlement(
                db,
                settlement_batch_id=int(batch.id),
                now=NOW,
            )
        )
        db.commit()

        repeated = (
            negative_finalization
            .finalize_negative_net_settlement(
                db,
                settlement_batch_id=int(batch.id),
                now=NOW,
            )
        )
        db.commit()

    finally:
        settings.NEGATIVE_NET_FINALIZATION_ENABLED = (
            old_enabled
        )
        settings.NEGATIVE_NET_FINALIZATION_UNLOCK_PRICING = (
            old_unlock
        )
        settings.NEGATIVE_NET_FINALIZATION_REQUIRE_PAYOUTS_CONFIRMED = (
            old_require_payout
        )

    db.expire_all()

    stored_batch = db.get(
        FundSettlementBatch,
        int(batch.id),
    )
    stored_fund = db.get(
        Fund,
        int(fund.id),
    )
    stored_buy_order = db.get(
        FundOrder,
        int(buy_order.id),
    )
    stored_redeem_order = db.get(
        FundOrder,
        int(redeem_order.id),
    )
    stored_buy_wallet = db.get(
        UserWallet,
        int(buy_wallet.id),
    )
    stored_buy_position = get_position(
        db,
        user_id=int(buy_user.id),
        fund_id=int(fund.id),
    )
    stored_redeem_position = get_position(
        db,
        user_id=int(redeem_user.id),
        fund_id=int(fund.id),
    )

    require(
        result.ok is True,
        "Negative mixed full flow did not complete",
    )
    require(
        result.buy_order_count == 1,
        "Negative mixed flow buy count mismatch",
    )
    require(
        result.redeem_order_count == 1,
        "Negative mixed flow redeem count mismatch",
    )
    require(
        D(str(result.total_buy_usdt))
        == D("10"),
        (
            "Negative mixed flow lost full buy cash: "
            f"{result.total_buy_usdt}"
        ),
    )
    require(
        D(str(result.total_buy_shares))
        == D("0.0157"),
        (
            "Negative mixed flow buy shares mismatch: "
            f"{result.total_buy_shares}"
        ),
    )
    require(
        D(str(result.total_redeem_shares))
        == D("0.0200"),
        (
            "Negative mixed flow redeem shares mismatch: "
            f"{result.total_redeem_shares}"
        ),
    )
    require(
        D(str(stored_batch.total_buy_usdt))
        == D("10"),
        "Negative batch full buy cash changed",
    )
    require(
        D(str(stored_batch.total_redeem_usdt))
        == D("12.6850"),
        "Negative batch redeem cash changed",
    )
    require(
        D(str(stored_batch.net_cash_usdt))
        == D("-2.6850"),
        "Negative batch net cash changed",
    )
    require(
        D(str(stored_buy_order.shares))
        == D("0.0157"),
        "Negative buy order not floored to 4dp",
    )
    require(
        D(str(stored_redeem_order.shares))
        == D("0.0200"),
        "Negative redeem shares changed",
    )
    require(
        D(str(stored_buy_position.shares))
        == D("0.0157"),
        (
            "Negative buy position mismatch: "
            f"{stored_buy_position.shares}"
        ),
    )
    require(
        D(str(stored_redeem_position.shares))
        == D("0.0800"),
        (
            "Negative redeem position mismatch: "
            f"{stored_redeem_position.shares}"
        ),
    )
    require(
        D(str(stored_redeem_position.shares_reserved))
        == D("0"),
        (
            "Negative redeem reserve mismatch: "
            f"{stored_redeem_position.shares_reserved}"
        ),
    )
    require(
        D(str(stored_buy_wallet.usdt_reserved))
        == D("0"),
        (
            "Negative buy wallet reserve mismatch: "
            f"{stored_buy_wallet.usdt_reserved}"
        ),
    )
    require(
        D(str(stored_fund.shares_outstanding_current))
        == D("0.9957"),
        (
            "Negative fund shares mismatch: "
            f"{stored_fund.shares_outstanding_current}"
        ),
    )
    require(
        repeated.idempotent is True,
        "Negative rerun was not idempotent",
    )
    require(
        D(str(stored_buy_position.shares))
        == D("0.0157"),
        "Negative rerun changed buy position",
    )
    require(
        D(str(stored_redeem_position.shares))
        == D("0.0800"),
        "Negative rerun changed redeem position",
    )
    require(
        D(str(stored_fund.shares_outstanding_current))
        == D("0.9957"),
        "Negative rerun changed fund shares",
    )

    print(
        "STAGE26_3_12P_NEGATIVE_NET_BUY_SHARES_4DP_OK"
    )
    print(
        "STAGE26_3_12P_R1_NEGATIVE_MIXED_FULL_FLOW_4DP_OK"
    )

    return {
        "total_buy_usdt": D(
            str(stored_batch.total_buy_usdt)
        ),
        "total_redeem_usdt": D(
            str(stored_batch.total_redeem_usdt)
        ),
        "net_cash_usdt": D(
            str(stored_batch.net_cash_usdt)
        ),
        "buy_order_shares": D(
            str(stored_buy_order.shares)
        ),
        "redeem_order_shares": D(
            str(stored_redeem_order.shares)
        ),
        "buy_position_shares": D(
            str(stored_buy_position.shares)
        ),
        "redeem_position_shares": D(
            str(stored_redeem_position.shares)
        ),
        "fund_shares": D(
            str(stored_fund.shares_outstanding_current)
        ),
        "idempotent": bool(repeated.idempotent),
    }


def test_zero_share_db_fail_closed(
    db: Session,
    *,
    suffix: int,
) -> dict[str, Any]:
    fund = new_fund(
        db,
        suffix="zero-share",
        shares_outstanding=D("1.0000"),
    )
    user = new_user(
        db,
        suffix="zero-share",
    )
    batch = new_settlement_batch(
        db,
        fund=fund,
        suffix=suffix,
        settlement_price=D("634.25"),
        shares_before=D("1.0000"),
        planned_issue=D("0.0000"),
    )
    order = new_buy_order(
        db,
        user=user,
        fund=fund,
        batch=batch,
        amount=D("0.01"),
        shares=None,
    )
    db.commit()

    try:
        validate_positive_net_share_preflight(
            db,
            batch=batch,
        )
    except SettlementShareQuantityError as exc:
        error = str(exc)
        require(
            BUY_SHARE_QUANTITY_BELOW_MINIMUM_ERROR
            in error,
            (
                "Unexpected zero-share preflight "
                f"error: {error}"
            ),
        )
        db.commit()
    else:
        raise AssertionError(
            "Zero-share DB preflight was accepted"
        )

    db.expire_all()
    stored_batch = db.get(
        FundSettlementBatch,
        int(batch.id),
    )
    stored_order = db.get(
        FundOrder,
        int(order.id),
    )
    stored_fund = db.get(Fund, int(fund.id))
    position_count = int(
        db.query(func.count(UserFundPosition.id))
        .filter(
            UserFundPosition.user_id
            == int(user.id),
            UserFundPosition.fund_id
            == int(fund.id),
        )
        .scalar()
        or 0
    )

    require(
        stored_batch.status
        == "failed_requires_review",
        (
            "Zero-share batch was not marked "
            "failed_requires_review"
        ),
    )
    require(
        stored_order.status
        == "failed_requires_review",
        (
            "Zero-share order was not marked "
            "failed_requires_review"
        ),
    )
    require(
        D(str(stored_fund.shares_outstanding_current))
        == D("1.0000"),
        "Zero-share failure changed fund shares",
    )
    require(
        stored_order.shares is None,
        "Zero-share failure wrote order shares",
    )
    require(
        position_count == 0,
        "Zero-share failure created user position",
    )

    print(
        "STAGE26_3_12P_ZERO_SHARE_ISSUANCE_FAIL_CLOSED_OK"
    )
    return {
        "batch_status": stored_batch.status,
        "order_status": stored_order.status,
        "fund_shares_unchanged": True,
        "position_created": False,
    }


def test_historical_tail_fail_closed(
    db: Session,
    *,
    suffix: int,
) -> dict[str, Any]:
    fund = new_fund(
        db,
        suffix="tail",
        shares_outstanding=D("1.0000"),
    )
    user = new_user(
        db,
        suffix="tail",
    )
    position = new_position(
        db,
        user=user,
        fund=fund,
        shares=D("2.0000000001"),
        shares_reserved=D("0.0000"),
    )
    batch = new_settlement_batch(
        db,
        fund=fund,
        suffix=suffix,
        settlement_price=D("634.25"),
        shares_before=D("1.0000"),
        planned_issue=D("0.0157"),
    )
    order = new_buy_order(
        db,
        user=user,
        fund=fund,
        batch=batch,
        amount=D("10"),
        shares=D("0.0157"),
    )
    db.commit()

    tail_before = D(str(position.shares))

    try:
        finalize_positive_net_accounting(
            db,
            batch_id=int(batch.id),
            unlock_pricing=False,
        )
    except SettlementShareQuantityError:
        db.commit()
    else:
        raise AssertionError(
            "Historical position tail was accepted"
        )

    db.expire_all()
    stored_position = get_position(
        db,
        user_id=int(user.id),
        fund_id=int(fund.id),
    )
    stored_fund = db.get(Fund, int(fund.id))
    stored_batch = db.get(
        FundSettlementBatch,
        int(batch.id),
    )
    stored_order = db.get(
        FundOrder,
        int(order.id),
    )

    require(
        D(str(stored_position.shares))
        == tail_before,
        (
            "Historical tail was silently "
            f"normalized: {stored_position.shares}"
        ),
    )
    require(
        D(str(stored_fund.shares_outstanding_current))
        == D("1.0000"),
        (
            "Historical-tail failure changed "
            "fund shares"
        ),
    )
    require(
        stored_batch.status
        == "failed_requires_review",
        (
            "Historical-tail batch was not marked "
            "failed_requires_review"
        ),
    )
    require(
        stored_order.status
        == "failed_requires_review",
        (
            "Historical-tail order was not marked "
            "failed_requires_review"
        ),
    )

    print(
        "STAGE26_3_12P_HISTORICAL_TAIL_NOT_SILENTLY_TRUNCATED_OK"
    )
    return {
        "tail_before": tail_before,
        "tail_after": D(str(stored_position.shares)),
        "batch_status": stored_batch.status,
        "order_status": stored_order.status,
    }



def test_r1_pre_external_fail_closed(
    db: Session,
) -> dict[str, Any]:
    # -------------------------------------------------
    # Positive-net: historical position tail
    # -------------------------------------------------
    positive_fund = new_fund(
        db,
        suffix="r1-positive-tail",
        shares_outstanding=D("1.0000"),
    )
    positive_user = new_user(
        db,
        suffix="r1-positive-tail",
    )
    positive_position = new_position(
        db,
        user=positive_user,
        fund=positive_fund,
        shares=D("2.0000000001"),
        shares_reserved=D("0"),
    )
    positive_batch = new_settlement_batch(
        db,
        fund=positive_fund,
        suffix=50,
        settlement_price=D("634.25"),
        shares_before=D("1.0000"),
        planned_issue=D("0.0157"),
        status="awaiting_positive_net_execution",
    )
    positive_batch.total_buy_usdt = D("10")
    positive_batch.total_redeem_shares = D("0")
    positive_batch.total_redeem_usdt = D("0")
    positive_batch.net_cash_usdt = D("10")
    positive_batch.planned_shares_to_issue = D("0.0157")
    positive_batch.planned_shares_to_redeem = D("0")
    positive_batch.planned_net_shares_change = D("0.0157")

    db.add(
        FundRuntimeState(
            fund_id=int(positive_fund.id),
            pricing_locked=True,
            pricing_lock_reason="settlement",
            pricing_lock_batch_id=int(positive_batch.id),
            pricing_locked_at=NOW,
            pricing_unlocked_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
    )

    positive_order = new_buy_order(
        db,
        user=positive_user,
        fund=positive_fund,
        batch=positive_batch,
        amount=D("10"),
        shares=D("0.0157"),
        status="buy_collected",
    )
    db.commit()

    positive_tail_before = D(
        str(positive_position.shares)
    )
    positive_calls = {
        "seller_payout": 0,
        "positive_bsc_transfer": 0,
        "bybit_deposit": 0,
        "internal_transfer": 0,
    }

    def positive_seller(*args: Any, **kwargs: Any) -> Any:
        positive_calls["seller_payout"] += 1
        return SimpleNamespace(
            seller_payouts_completed=True,
        )

    def positive_transfer(*args: Any, **kwargs: Any) -> Any:
        positive_calls["positive_bsc_transfer"] += 1
        return SimpleNamespace(
            transfer_status="confirmed",
        )

    def positive_deposit(*args: Any, **kwargs: Any) -> bool:
        positive_calls["bybit_deposit"] += 1
        return True

    def positive_internal(*args: Any, **kwargs: Any) -> bool:
        positive_calls["internal_transfer"] += 1
        return True

    with patched_attr(
        positive_net_service,
        "process_seller_payouts_for_batch",
        positive_seller,
    ), patched_attr(
        positive_net_service,
        "send_or_confirm_positive_net_transfer",
        positive_transfer,
    ), patched_attr(
        positive_net_service,
        "confirm_bybit_deposit_for_batch",
        positive_deposit,
    ), patched_attr(
        positive_net_service,
        "_internal_transfer_ready_or_skipped",
        positive_internal,
    ), patched_attr(
        positive_net_service,
        "_send_alert",
        lambda _text: None,
    ):
        positive_result = (
            positive_net_service.process_positive_net_batch(
                db,
                batch_id=int(positive_batch.id),
                master_client=None,
                fund_client_factory=None,
                dry_run=False,
                mock_chain=False,
                mock_bybit=False,
                finalize_accounting=True,
            )
        )
        db.commit()

    db.expire_all()

    stored_positive_batch = db.get(
        FundSettlementBatch,
        int(positive_batch.id),
    )
    stored_positive_position = get_position(
        db,
        user_id=int(positive_user.id),
        fund_id=int(positive_fund.id),
    )

    require(
        positive_result.status
        == "failed_requires_review",
        "Positive tail did not fail closed",
    )
    require(
        stored_positive_batch.status
        == "failed_requires_review",
        "Positive tail batch status mismatch",
    )
    require(
        positive_calls
        == {
            "seller_payout": 0,
            "positive_bsc_transfer": 0,
            "bybit_deposit": 0,
            "internal_transfer": 0,
        },
        (
            "Positive external calls occurred before "
            f"tail validation: {positive_calls}"
        ),
    )
    require(
        D(str(stored_positive_position.shares))
        == positive_tail_before,
        "Positive historical tail was mutated",
    )

    # -------------------------------------------------
    # Positive-net: cash mismatch
    # -------------------------------------------------
    mismatch_fund = new_fund(
        db,
        suffix="r1-cash-mismatch",
        shares_outstanding=D("1.0000"),
    )
    mismatch_user = new_user(
        db,
        suffix="r1-cash-mismatch",
    )
    mismatch_batch = new_settlement_batch(
        db,
        fund=mismatch_fund,
        suffix=51,
        settlement_price=D("634.25"),
        shares_before=D("1.0000"),
        planned_issue=D("0.0314"),
        status="awaiting_positive_net_execution",
    )
    mismatch_batch.total_buy_usdt = D("19")
    mismatch_batch.total_redeem_shares = D("0")
    mismatch_batch.total_redeem_usdt = D("0")
    mismatch_batch.net_cash_usdt = D("19")
    mismatch_batch.planned_shares_to_issue = D("0.0314")
    mismatch_batch.planned_shares_to_redeem = D("0")
    mismatch_batch.planned_net_shares_change = D("0.0314")

    db.add(
        FundRuntimeState(
            fund_id=int(mismatch_fund.id),
            pricing_locked=True,
            pricing_lock_reason="settlement",
            pricing_lock_batch_id=int(mismatch_batch.id),
            pricing_locked_at=NOW,
            pricing_unlocked_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
    )

    new_buy_order(
        db,
        user=mismatch_user,
        fund=mismatch_fund,
        batch=mismatch_batch,
        amount=D("10"),
        shares=D("0.0157"),
        status="buy_collected",
    )
    new_buy_order(
        db,
        user=mismatch_user,
        fund=mismatch_fund,
        batch=mismatch_batch,
        amount=D("10"),
        shares=D("0.0157"),
        status="buy_collected",
    )
    db.commit()

    mismatch_calls = {
        "seller_payout": 0,
        "positive_bsc_transfer": 0,
        "bybit_deposit": 0,
        "internal_transfer": 0,
    }

    def mismatch_seller(*args: Any, **kwargs: Any) -> Any:
        mismatch_calls["seller_payout"] += 1
        return SimpleNamespace(
            seller_payouts_completed=True,
        )

    def mismatch_transfer(*args: Any, **kwargs: Any) -> Any:
        mismatch_calls["positive_bsc_transfer"] += 1
        return SimpleNamespace(
            transfer_status="confirmed",
        )

    def mismatch_deposit(*args: Any, **kwargs: Any) -> bool:
        mismatch_calls["bybit_deposit"] += 1
        return True

    def mismatch_internal(*args: Any, **kwargs: Any) -> bool:
        mismatch_calls["internal_transfer"] += 1
        return True

    with patched_attr(
        positive_net_service,
        "process_seller_payouts_for_batch",
        mismatch_seller,
    ), patched_attr(
        positive_net_service,
        "send_or_confirm_positive_net_transfer",
        mismatch_transfer,
    ), patched_attr(
        positive_net_service,
        "confirm_bybit_deposit_for_batch",
        mismatch_deposit,
    ), patched_attr(
        positive_net_service,
        "_internal_transfer_ready_or_skipped",
        mismatch_internal,
    ), patched_attr(
        positive_net_service,
        "_send_alert",
        lambda _text: None,
    ):
        mismatch_result = (
            positive_net_service.process_positive_net_batch(
                db,
                batch_id=int(mismatch_batch.id),
                master_client=None,
                fund_client_factory=None,
                dry_run=False,
                mock_chain=False,
                mock_bybit=False,
                finalize_accounting=True,
            )
        )
        db.commit()

    require(
        mismatch_result.status
        == "failed_requires_review",
        "Cash mismatch did not fail closed",
    )
    require(
        mismatch_calls
        == {
            "seller_payout": 0,
            "positive_bsc_transfer": 0,
            "bybit_deposit": 0,
            "internal_transfer": 0,
        },
        (
            "Positive external calls occurred before "
            f"cash reconciliation: {mismatch_calls}"
        ),
    )

    # -------------------------------------------------
    # Negative-net: historical position tail
    # -------------------------------------------------
    negative_fund = new_fund(
        db,
        suffix="r1-negative-tail",
        shares_outstanding=D("1.0000"),
    )
    negative_user = new_user(
        db,
        suffix="r1-negative-tail",
    )
    negative_position = new_position(
        db,
        user=negative_user,
        fund=negative_fund,
        shares=D("2.0000000001"),
        shares_reserved=D("0"),
    )
    negative_batch = new_settlement_batch(
        db,
        fund=negative_fund,
        suffix=52,
        settlement_price=D("634.25"),
        shares_before=D("1.0000"),
        planned_issue=D("0.0157"),
        status="negative_net_sale_planned",
    )
    negative_batch.total_buy_usdt = D("10")
    negative_batch.total_redeem_shares = D("0")
    negative_batch.total_redeem_usdt = D("0")
    negative_batch.net_cash_usdt = D("10")
    negative_batch.planned_shares_to_issue = D("0.0157")
    negative_batch.planned_shares_to_redeem = D("0")
    negative_batch.planned_net_shares_change = D("0.0157")

    new_buy_order(
        db,
        user=negative_user,
        fund=negative_fund,
        batch=negative_batch,
        amount=D("10"),
        shares=D("0.0157"),
        status="settling",
    )

    negative_sale_batch = FundNegativeSaleBatch(
        settlement_batch_id=int(negative_batch.id),
        fund_id=int(negative_fund.id),
        status="sale_plan_created",
        required_master_usdt=D("1"),
        withdrawal_request_amount_usdt=D("1"),
        total_net_user_payout_usdt=D("1"),
        total_partial_month_fee_usdt=D("0"),
        bybit_withdrawal_fee_usdt=D("0"),
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(negative_sale_batch)
    db.commit()

    negative_tail_before = D(
        str(negative_position.shares)
    )
    negative_calls = {
        "sale_order": 0,
        "earn_redeem": 0,
        "universal_transfer": 0,
        "bybit_withdrawal": 0,
        "bsc_payout": 0,
    }

    def negative_earn(*args: Any, **kwargs: Any) -> Any:
        negative_calls["earn_redeem"] += 1
        return D("0"), []

    def negative_sale(*args: Any, **kwargs: Any) -> list[Any]:
        negative_calls["sale_order"] += 1
        return []

    with patched_attr(
        negative_sale_execution,
        "execute_initial_usdt_earn_redeem_live",
        negative_earn,
    ), patched_attr(
        negative_sale_execution,
        "execute_initial_sale_legs_live",
        negative_sale,
    ):
        try:
            negative_sale_execution.execute_negative_sale_plan_live(
                db,
                sale_batch_id=int(negative_sale_batch.id),
                client=object(),
                now=NOW,
            )
        except SettlementShareQuantityError:
            db.commit()
        else:
            raise AssertionError(
                "Negative historical tail was accepted"
            )

    db.expire_all()

    stored_negative_batch = db.get(
        FundSettlementBatch,
        int(negative_batch.id),
    )
    stored_negative_position = get_position(
        db,
        user_id=int(negative_user.id),
        fund_id=int(negative_fund.id),
    )

    require(
        stored_negative_batch.status
        == "failed_requires_review",
        "Negative tail batch did not fail closed",
    )
    require(
        negative_calls["sale_order"] == 0,
        "Negative sale order executed before validation",
    )
    require(
        negative_calls["earn_redeem"] == 0,
        "Negative Earn redeem executed before validation",
    )
    require(
        D(str(stored_negative_position.shares))
        == negative_tail_before,
        "Negative historical tail was mutated",
    )

    # Проверяем расположение общего validator перед
    # Universal Transfer, withdrawal и BSC payout.
    bybit_source = Path(
        "app/settlement/negative_bybit_flow.py"
    ).read_text(encoding="utf-8")
    bybit_start = bybit_source.index(
        "def execute_negative_bybit_flow_live"
    )
    bybit_block = bybit_source[bybit_start:]
    bybit_validate = bybit_block.index(
        "validate_settlement_share_state_before_external("
    )
    universal_transfer = bybit_block.index(
        "create_universal_transfer("
    )
    withdrawal = bybit_block.index(
        "create_master_withdrawal("
    )

    require(
        bybit_validate < universal_transfer,
        "Negative validator is after Universal Transfer",
    )
    require(
        bybit_validate < withdrawal,
        "Negative validator is after Bybit withdrawal",
    )

    payout_source = Path(
        "app/settlement/negative_payout_flow.py"
    ).read_text(encoding="utf-8")
    payout_start = payout_source.index(
        "def execute_negative_payout_flow_live"
    )
    payout_end = payout_source.index(
        "def execute_negative_payout_flow_mock",
        payout_start,
    )
    payout_block = payout_source[
        payout_start:payout_end
    ]
    payout_validate = payout_block.index(
        "validate_settlement_share_state_before_external("
    )
    gas_boundary = payout_block.index(
        "_ensure_live_settlement_wallet_gas("
    )
    payout_boundary = payout_block.index(
        "_send_or_confirm_live_payout_leg("
    )

    require(
        payout_validate < gas_boundary,
        "Negative validator is after gas boundary",
    )
    require(
        payout_validate < payout_boundary,
        "Negative validator is after BSC payout boundary",
    )

    print(
        "STAGE26_3_12P_R1_POSITIVE_PREFLIGHT_VALIDATES_POSITIONS_OK"
    )
    print(
        "STAGE26_3_12P_R1_NEGATIVE_PREFLIGHT_BEFORE_EXTERNAL_ACTIONS_OK"
    )
    print(
        "STAGE26_3_12P_R1_POSITIVE_EXTERNAL_CALLS_ZERO_ON_TAIL_OK"
    )
    print(
        "STAGE26_3_12P_R1_NEGATIVE_EXTERNAL_CALLS_ZERO_ON_TAIL_OK"
    )
    print(
        "STAGE26_3_12P_R1_HISTORICAL_TAIL_FAILS_BEFORE_EXTERNAL_OK"
    )

    return {
        "positive_tail_calls": positive_calls,
        "positive_cash_mismatch_calls": mismatch_calls,
        "negative_tail_calls": negative_calls,
        "positive_tail_unchanged": True,
        "negative_tail_unchanged": True,
        "cash_mismatch_failed_before_external": True,
        "negative_bybit_boundaries_verified": True,
        "negative_payout_boundaries_verified": True,
    }



def test_redeem_only_pre_external(
    db: Session,
) -> dict[str, Any]:
    fund = new_fund(
        db,
        suffix="redeem-only",
        shares_outstanding=D("1.0000"),
    )
    user = new_user(
        db,
        suffix="redeem-only",
    )
    new_position(
        db,
        user=user,
        fund=fund,
        shares=D("1.0000"),
        shares_reserved=D("0.0160"),
    )

    batch = new_settlement_batch(
        db,
        fund=fund,
        suffix=90,
        settlement_price=D("634.25"),
        shares_before=D("1.0000"),
        planned_issue=D("0"),
        planned_redeem=D("0.0160"),
        status="negative_net_sale_planned",
    )
    batch.total_buy_usdt = D("0")
    batch.total_redeem_shares = D("0.0160")
    batch.total_redeem_usdt = D("10.1480")
    batch.net_cash_usdt = D("-10.1480")
    batch.planned_shares_to_issue = D("0")
    batch.planned_shares_to_redeem = D("0.0160")
    batch.planned_net_shares_change = D("-0.0160")

    order = FundOrder(
        user_id=int(user.id),
        fund_id=int(fund.id),
        side="redeem",
        amount_usdt=None,
        shares=D("0.0160"),
        price_usdt=None,
        status="settling",
        settlement_batch_id=int(batch.id),
        created_at=NOW,
    )
    db.add(order)
    db.commit()

    plan = validate_settlement_share_state_before_external(
        db,
        batch=batch,
        mark_failed=True,
    )

    require(
        plan.total_buy_usdt == D("0"),
        f"Redeem-only buy total mismatch: {plan.total_buy_usdt}",
    )
    require(
        plan.total_redeem_shares == D("0.0160"),
        (
            "Redeem-only shares mismatch: "
            f"{plan.total_redeem_shares}"
        ),
    )
    require(
        plan.total_redeem_usdt == D("10.1480"),
        (
            "Redeem-only USDT mismatch: "
            f"{plan.total_redeem_usdt}"
        ),
    )
    require(
        plan.net_cash_usdt == D("-10.1480"),
        (
            "Redeem-only net cash mismatch: "
            f"{plan.net_cash_usdt}"
        ),
    )

    print("STAGE26_3_12P_REDEEM_ONLY_PREFLIGHT_OK")

    return {
        "total_buy_usdt": plan.total_buy_usdt,
        "total_redeem_shares": plan.total_redeem_shares,
        "total_redeem_usdt": plan.total_redeem_usdt,
        "net_cash_usdt": plan.net_cash_usdt,
    }


def print_final_markers() -> None:
    print(
        "STAGE26_3_12P_R1_FOUR_DECIMAL_SHARE_ACCOUNTING_OK"
    )
    print(
        "STAGE26_3_12P_FOUR_DECIMAL_SHARE_ACCOUNTING_OK"
    )

def main() -> int:
    admin_engine = None
    test_engine = None
    schema = None

    helper_audit = None
    full_buy_audit = None
    batch_fields = None
    exact_division_audit = None
    zero_share_error = None
    redeem_precision_helper = None
    redeem_backend = None
    direct_api = None
    positive_accounting = None
    negative_accounting = None
    redeem_only = None
    zero_share_db = None
    historical_tail = None
    r1_pre_external = None

    try:
        (
            admin_engine,
            test_engine,
            TestSessionLocal,
            schema,
        ) = create_test_schema()

        helper_audit = test_share_floor_helper()
        full_buy_audit = test_full_buy_amount_invested()
        batch_fields = test_per_order_buy_share_floor()
        exact_division_audit = (
            test_exact_division_no_reduction()
        )
        zero_share_error = (
            test_zero_share_planning_rejected()
        )
        redeem_precision_helper = (
            test_redeem_precision_helper_cases()
        )

        db = TestSessionLocal()
        try:
            redeem_backend = (
                test_redeem_backend_service_precision(db)
            )
            positive_accounting = (
                test_positive_net_accounting_and_idempotency(
                    db,
                    suffix=30,
                )
            )
            negative_accounting = (
                test_negative_net_buy_accounting(db)
            )
            redeem_only = (
                test_redeem_only_pre_external(db)
            )
            zero_share_db = (
                test_zero_share_db_fail_closed(
                    db,
                    suffix=31,
                )
            )
            historical_tail = (
                test_historical_tail_fail_closed(
                    db,
                    suffix=32,
                )
            )
            r1_pre_external = (
                test_r1_pre_external_fail_closed(db)
            )
        finally:
            db.rollback()
            db.close()

        direct_api = test_direct_api_redeem_precision(
            TestSessionLocal
        )
    finally:
        if (
            admin_engine is not None
            and test_engine is not None
            and schema is not None
        ):
            drop_test_schema(
                admin_engine,
                test_engine,
                schema,
            )

    print(
        {
            "helper_audit": helper_audit,
            "full_buy_audit": full_buy_audit,
            "batch_planned_shares_to_issue": (
                batch_fields[
                    "planned_shares_to_issue"
                ]
                if batch_fields is not None
                else None
            ),
            "exact_division_audit": (
                exact_division_audit
            ),
            "zero_share_planning_error": (
                zero_share_error
            ),
            "redeem_precision_helper": (
                redeem_precision_helper
            ),
            "redeem_backend": redeem_backend,
            "direct_api": direct_api,
            "positive_accounting": positive_accounting,
            "negative_accounting": negative_accounting,
            "redeem_only": redeem_only,
            "zero_share_db": zero_share_db,
            "historical_tail": historical_tail,
            "r1_pre_external": r1_pre_external,
            "isolated_test_schema": schema,
            "isolated_test_schema_dropped": True,
            "production_rows_touched": False,
            "external_actions_called": False,
        }
    )
    print_final_markers()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())