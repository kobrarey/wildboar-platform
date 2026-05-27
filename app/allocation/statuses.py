from __future__ import annotations


# Allocation batch statuses
ALLOCATION_BATCH_STATUS_PLANNED = "planned"
ALLOCATION_BATCH_STATUS_SNAPSHOT_CREATED = "snapshot_created"
ALLOCATION_BATCH_STATUS_PLAN_CREATED = "plan_created"
ALLOCATION_BATCH_STATUS_FAILED_REQUIRES_REVIEW = "failed_requires_review"

# Future statuses are present in DB, but Stage 22.2 does not use them.
ALLOCATION_BATCH_STATUS_ALLOCATION_PROCESSING = "allocation_processing"
ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED = "allocation_completed"
ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED_WITH_RESIDUAL_EARN = (
    "allocation_completed_with_residual_earn"
)
ALLOCATION_BATCH_STATUS_ALLOCATION_COMPLETED_WITH_RESIDUAL_CASH = (
    "allocation_completed_with_residual_cash"
)


# Allocation leg statuses
ALLOCATION_LEG_STATUS_PLANNED = "planned"
ALLOCATION_LEG_STATUS_SKIPPED_ZERO_VALUE = "skipped_zero_value"
ALLOCATION_LEG_STATUS_FAILED_REQUIRES_REVIEW = "failed_requires_review"

# Future statuses are present in DB, but Stage 22.2 does not use them.
ALLOCATION_LEG_STATUS_SKIPPED_MIN_ORDER = "skipped_min_order"
ALLOCATION_LEG_STATUS_SKIPPED_SYMBOL_NOT_TRADING = "skipped_symbol_not_trading"
ALLOCATION_LEG_STATUS_SKIPPED_EARN_UNAVAILABLE = "skipped_earn_unavailable"
ALLOCATION_LEG_STATUS_SKIPPED_MARGIN_GUARD = "skipped_margin_guard"
ALLOCATION_LEG_STATUS_MARKET_ORDER_SENT = "market_order_sent"
ALLOCATION_LEG_STATUS_NATIVE_ICEBERG_PROCESSING = "native_iceberg_processing"
ALLOCATION_LEG_STATUS_SLICED_IOC_PROCESSING = "sliced_ioc_processing"
ALLOCATION_LEG_STATUS_FILLED = "filled"
ALLOCATION_LEG_STATUS_PARTIAL_FILLED_RESIDUALIZED = "partial_filled_residualized"
ALLOCATION_LEG_STATUS_RESIDUAL_EARN_COMPLETED = "residual_earn_completed"
ALLOCATION_LEG_STATUS_RESIDUAL_CASH = "residual_cash"


# Snapshot holding groups
HOLDING_GROUP_CASH = "cash"
HOLDING_GROUP_SPOT = "spot"
HOLDING_GROUP_FUNDING_WALLET = "funding_wallet"
HOLDING_GROUP_EARN = "earn"
HOLDING_GROUP_PERP = "perp"
HOLDING_GROUP_FUTURE = "future"
HOLDING_GROUP_LONG_OPTION = "long_option"
HOLDING_GROUP_SHORT_OPTION = "short_option"
HOLDING_GROUP_OTHER = "other"


# Planned leg groups
LEG_GROUP_CASH = "cash"
LEG_GROUP_SPOT = "spot"
LEG_GROUP_EARN = "earn"
LEG_GROUP_DERIVATIVE = "derivative"
LEG_GROUP_OPTION = "option"
LEG_GROUP_OTHER = "other"


# Planned leg types
LEG_TYPE_STABLE_CASH = "stable_cash"
LEG_TYPE_SPOT_BUY = "spot_buy"
LEG_TYPE_USDT_EARN_STAKE = "usdt_earn_stake"
LEG_TYPE_BUY_THEN_STAKE = "buy_then_stake"
LEG_TYPE_PERP_INCREASE = "perp_increase"
LEG_TYPE_FUTURE_INCREASE = "future_increase"
LEG_TYPE_LONG_OPTION_INCREASE = "long_option_increase"
LEG_TYPE_SHORT_OPTION_INCREASE = "short_option_increase"
LEG_TYPE_OTHER = "other"


EXECUTION_MODE_PLANNED = "planned"