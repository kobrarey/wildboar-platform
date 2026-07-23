from sqlalchemy import UniqueConstraint

from app.config import Settings
from app.models import (
    FundBscTransactionIntent,
    FundNegativeBybitFlow,
)


def _unique_column_sets(table) -> set[tuple[str, ...]]:
    return {
        tuple(constraint.columns.keys())
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _index_column_sets(table) -> set[tuple[str, ...]]:
    return {
        tuple(index.columns.keys())
        for index in table.indexes
    }


def _assert_fk(
    column,
    *,
    target: str,
    ondelete: str,
) -> None:
    foreign_keys = list(column.foreign_keys)

    assert len(foreign_keys) == 1
    assert foreign_keys[0].target_fullname == target
    assert foreign_keys[0].ondelete == ondelete


def test_fund_bsc_transaction_intent_table_contract():
    table = FundBscTransactionIntent.__table__

    assert table.name == "fund_bsc_transaction_intents"

    required_columns = {
        "id",
        "scope_key",
        "action_type",
        "settlement_batch_id",
        "payout_batch_id",
        "payout_leg_id",
        "fund_id",
        "asset",
        "amount",
        "from_address",
        "to_address",
        "chain_id",
        "source_nonce",
        "prepared_tx_hash",
        "prepared_raw_tx",
        "intent_fingerprint",
        "status",
        "broadcast_attempts",
        "receipt_status",
        "block_number",
        "confirmations",
        "prepared_at",
        "broadcast_started_at",
        "broadcast_at",
        "visible_at",
        "confirmed_at",
        "failed_at",
        "prepared_json",
        "broadcast_json",
        "reconciliation_json",
        "error",
        "created_at",
        "updated_at",
    }

    assert set(table.columns.keys()) == required_columns

    assert table.c.scope_key.type.length == 192
    assert table.c.action_type.type.length == 64
    assert table.c.asset.type.length == 16
    assert table.c.from_address.type.length == 128
    assert table.c.to_address.type.length == 128
    assert table.c.prepared_tx_hash.type.length == 128
    assert table.c.intent_fingerprint.type.length == 64

    assert table.c.amount.type.precision == 38
    assert table.c.amount.type.scale == 18

    assert table.c.payout_leg_id.nullable is True
    assert table.c.prepared_raw_tx.nullable is False
    assert table.c.prepared_at.nullable is False
    assert table.c.prepared_at.type.timezone is True

    assert table.c.status.server_default is not None
    assert table.c.broadcast_attempts.server_default is not None
    assert table.c.created_at.server_default is not None
    assert table.c.updated_at.server_default is not None

    _assert_fk(
        table.c.settlement_batch_id,
        target="fund_settlement_batches.id",
        ondelete="CASCADE",
    )
    _assert_fk(
        table.c.payout_batch_id,
        target="fund_negative_payout_batches.id",
        ondelete="CASCADE",
    )
    _assert_fk(
        table.c.payout_leg_id,
        target="fund_negative_payout_legs.id",
        ondelete="CASCADE",
    )
    _assert_fk(
        table.c.fund_id,
        target="funds.id",
        ondelete="CASCADE",
    )

    unique_sets = _unique_column_sets(table)

    assert ("scope_key",) in unique_sets
    assert (
        "from_address",
        "source_nonce",
    ) in unique_sets
    assert ("payout_leg_id",) in unique_sets

    index_sets = _index_column_sets(table)

    assert ("settlement_batch_id",) in index_sets
    assert ("payout_batch_id",) in index_sets
    assert ("status", "updated_at") in index_sets

    relationships = set(
        FundBscTransactionIntent
        .__mapper__
        .relationships
        .keys()
    )

    assert relationships == {
        "settlement_batch",
        "payout_batch",
        "payout_leg",
        "fund",
    }


def test_negative_bybit_flow_task3_columns():
    table = FundNegativeBybitFlow.__table__

    expected = {
        "withdrawal_policy_version",
        "coin_info_snapshot_json",
        "universal_transfer_intent_json",
        "withdrawal_intent_json",
        "universal_transfer_submitted_at",
        "withdrawal_submitted_at",
        "settlement_wallet_balance_before_usdt",
        "settlement_wallet_balance_after_usdt",
        "settlement_wallet_receipt_confirmations",
        "settlement_wallet_receipt_block_number",
    }

    assert expected.issubset(
        set(table.columns.keys())
    )

    assert (
        table.c.withdrawal_policy_version.type.length
        == 64
    )

    for column_name in (
        "universal_transfer_submitted_at",
        "withdrawal_submitted_at",
    ):
        column = table.c[column_name]

        assert column.nullable is True
        assert column.type.timezone is True

    for column_name in (
        "settlement_wallet_balance_before_usdt",
        "settlement_wallet_balance_after_usdt",
    ):
        column = table.c[column_name]

        assert column.type.precision == 30
        assert column.type.scale == 10
        assert column.nullable is True


def test_negative_cash_delivery_config_defaults():
    config = Settings(
        DATABASE_URL="postgresql://unused",
    )

    assert (
        config.NEGATIVE_NET_WITHDRAWAL_POLICY_VERSION
        == "bsc_exact_received_v1"
    )
    assert (
        config.NEGATIVE_NET_WITHDRAWAL_FEE_MAX_AGE_SEC
        == 300
    )
    assert (
        config.NEGATIVE_NET_BYBIT_RECORD_LOOKBACK_HOURS
        == 24
    )
    assert (
        config.NEGATIVE_NET_BSC_INTENT_CONFIRMATIONS_REQUIRED
        == 12
    )
    assert (
        config.NEGATIVE_NET_BSC_INTENT_MAX_PENDING_SEC
        == 3600
    )

    assert config.NEGATIVE_NET_BYBIT_FLOW_ALLOW_LIVE is False
    assert config.NEGATIVE_NET_PAYOUT_ALLOW_LIVE is False
    assert (
        config.NEGATIVE_NET_BYBIT_FLOW_ALLOW_LIVE_EXECUTION
        is False
    )
    assert (
        config.NEGATIVE_NET_PAYOUT_ALLOW_LIVE_EXECUTION
        is False
    )