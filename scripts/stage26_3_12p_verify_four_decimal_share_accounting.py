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
    FundOrder,
    FundSettlementBatch,
    User,
    UserFundPosition,
    UserWallet,
)
from app.settlement.batch_service import _calculate_batch_fields
from app.settlement import accounting_service
from app.settlement import negative_finalization
from app.settlement.accounting_service import (
    SettlementAccountingError,
    SettlementShareQuantityError,
    finalize_positive_net_accounting,
    validate_positive_net_share_preflight,
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
    FundOrder.__table__,
    UserFundPosition.__table__,
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
            test_engine,
            tables=TEST_TABLES,
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
) -> UserFundPosition:
    position = UserFundPosition(
        user_id=int(user.id),
        fund_id=int(fund.id),
        shares=shares,
        shares_reserved=shares_reserved,
    )
    db.add(position)
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
    )
    first_order = new_buy_order(
        db,
        user=user,
        fund=fund,
        batch=batch,
        amount=D("10"),
        shares=D("0.0157"),
    )
    second_order = new_buy_order(
        db,
        user=user,
        fund=fund,
        batch=batch,
        amount=D("10"),
        shares=D("0.0157"),
    )
    db.commit()

    result = finalize_positive_net_accounting(
        db,
        batch_id=int(batch.id),
        unlock_pricing=False,
    )
    db.commit()
    db.expire_all()

    stored_batch = db.get(
        FundSettlementBatch,
        int(batch.id),
    )
    stored_fund = db.get(Fund, int(fund.id))
    stored_orders = (
        db.query(FundOrder)
        .filter(
            FundOrder.settlement_batch_id
            == int(batch.id)
        )
        .order_by(FundOrder.id.asc())
        .all()
    )
    stored_position = get_position(
        db,
        user_id=int(user.id),
        fund_id=int(fund.id),
    )

    require(
        result.buyer_shares_issued == D("0.0314"),
        (
            "Positive accounting issued unexpected "
            f"shares: {result.buyer_shares_issued}"
        ),
    )
    require(
        [D(str(order.shares)) for order in stored_orders]
        == [D("0.0157"), D("0.0157")],
        (
            "Positive orders are not individually "
            "floored to 4dp"
        ),
    )
    require(
        D(str(stored_position.shares))
        == D("0.0314"),
        (
            "Positive user position mismatch: "
            f"{stored_position.shares}"
        ),
    )
    require(
        D(str(stored_fund.shares_outstanding_current))
        == D("1.0314"),
        (
            "Positive fund shares outstanding "
            f"mismatch: "
            f"{stored_fund.shares_outstanding_current}"
        ),
    )
    require(
        stored_batch.accounting_finalized_at
        is not None,
        "Positive batch was not finalized",
    )

    snapshot = {
        "fund_shares": D(
            str(stored_fund.shares_outstanding_current)
        ),
        "position_shares": D(
            str(stored_position.shares)
        ),
        "order_shares": [
            D(str(order.shares))
            for order in stored_orders
        ],
        "order_statuses": [
            str(order.status)
            for order in stored_orders
        ],
    }

    try:
        finalize_positive_net_accounting(
            db,
            batch_id=int(batch.id),
            unlock_pricing=False,
        )
    except SettlementAccountingError:
        db.rollback()
    else:
        db.rollback()

    db.expire_all()
    repeated_fund = db.get(Fund, int(fund.id))
    repeated_position = get_position(
        db,
        user_id=int(user.id),
        fund_id=int(fund.id),
    )
    repeated_orders = (
        db.query(FundOrder)
        .filter(
            FundOrder.settlement_batch_id
            == int(batch.id)
        )
        .order_by(FundOrder.id.asc())
        .all()
    )

    require(
        D(str(repeated_fund.shares_outstanding_current))
        == snapshot["fund_shares"],
        "Repeated positive accounting changed fund shares",
    )
    require(
        D(str(repeated_position.shares))
        == snapshot["position_shares"],
        (
            "Repeated positive accounting changed "
            "user position"
        ),
    )
    require(
        [
            D(str(order.shares))
            for order in repeated_orders
        ]
        == snapshot["order_shares"],
        (
            "Repeated positive accounting changed "
            "order shares"
        ),
    )
    require(
        [
            str(order.status)
            for order in repeated_orders
        ]
        == snapshot["order_statuses"],
        (
            "Repeated positive accounting changed "
            "order statuses"
        ),
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
        "order_shares": snapshot["order_shares"],
        "position_shares": snapshot[
            "position_shares"
        ],
        "fund_shares": snapshot["fund_shares"],
        "second_call_changed_state": False,
    }


def test_negative_net_buy_accounting(
    db: Session,
) -> dict[str, Any]:
    fund = new_fund(
        db,
        suffix="negative",
        shares_outstanding=D("1.0000"),
    )
    user = new_user(
        db,
        suffix="negative",
    )
    wallet = new_user_wallet(
        db,
        user=user,
        balance=D("100"),
        reserved=D("10"),
    )
    position = new_position(
        db,
        user=user,
        fund=fund,
        shares=D("0.0000"),
        shares_reserved=D("0.0000"),
    )
    order = new_buy_order(
        db,
        user=user,
        fund=fund,
        batch=None,
        amount=D("10"),
        shares=D("0.0157"),
        status="processing",
    )
    db.flush()

    validation = (
        negative_finalization._validate_buy_orders(
            buy_orders=[order],
            settlement_price_usdt=D("634.25"),
        )
    )

    updates = (
        negative_finalization._apply_buy_accounting(
            db,
            fund_id=int(fund.id),
            buy_orders=[order],
            buy_positions={
                int(order.id): position,
            },
            buy_wallets={
                int(order.id): wallet,
            },
            computed_shares_by_order_id=(
                validation[
                    "computed_shares_by_order_id"
                ]
            ),
            settlement_price_usdt=D("634.25"),
            executed_at=NOW,
        )
    )
    db.flush()

    require(
        validation["total_buy_usdt"]
        == D("10.0000000000"),
        "Negative accounting changed buy cash",
    )
    require(
        validation["total_buy_shares"]
        == D("0.0157"),
        (
            "Negative validation did not calculate "
            "4dp shares"
        ),
    )
    require(
        D(str(order.shares)) == D("0.0157"),
        (
            "Negative order shares mismatch: "
            f"{order.shares}"
        ),
    )
    require(
        D(str(position.shares)) == D("0.0157"),
        (
            "Negative position shares mismatch: "
            f"{position.shares}"
        ),
    )
    require(
        D(str(wallet.usdt_reserved)) == D("0"),
        (
            "Negative wallet reserve was not released: "
            f"{wallet.usdt_reserved}"
        ),
    )
    require(
        len(updates) == 1,
        "Negative accounting update audit missing",
    )

    db.rollback()

    print(
        "STAGE26_3_12P_NEGATIVE_NET_BUY_SHARES_4DP_OK"
    )
    return {
        "order_shares": D("0.0157"),
        "position_shares": D("0.0157"),
        "full_buy_usdt": D("10"),
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


def print_final_markers() -> None:
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
    zero_share_db = None
    historical_tail = None

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
            "zero_share_db": zero_share_db,
            "historical_tail": historical_tail,
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