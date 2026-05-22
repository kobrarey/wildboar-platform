from __future__ import annotations

# fund_settlement_batches.status
BATCH_STATUS_CREATED = "created"
BATCH_STATUS_PRICING_LOCKED = "pricing_locked"
BATCH_STATUS_PRICE_FIXED = "price_fixed"
BATCH_STATUS_GAS_CHECKING = "gas_checking"
BATCH_STATUS_GAS_READY = "gas_ready"
BATCH_STATUS_COLLECTING_BUY_USDT = "collecting_buy_usdt"
BATCH_STATUS_BUY_USDT_COLLECTED = "buy_usdt_collected"
BATCH_STATUS_AWAITING_POSITIVE_NET_EXECUTION = "awaiting_positive_net_execution"
BATCH_STATUS_AWAITING_NEGATIVE_NET_EXECUTION = "awaiting_negative_net_execution"
BATCH_STATUS_NO_ORDERS = "no_orders"
BATCH_STATUS_FAILED = "failed"

BATCH_TERMINAL_STATUSES = {
    BATCH_STATUS_AWAITING_POSITIVE_NET_EXECUTION,
    BATCH_STATUS_AWAITING_NEGATIVE_NET_EXECUTION,
    BATCH_STATUS_NO_ORDERS,
    BATCH_STATUS_FAILED,
}

# fund_orders.status
ORDER_STATUS_PENDING = "pending"
ORDER_STATUS_SETTLING = "settling"
ORDER_STATUS_BUY_COLLECTING = "buy_collecting"
ORDER_STATUS_BUY_COLLECTED = "buy_collected"
ORDER_STATUS_AWAITING_POSITIVE_NET_EXECUTION = "awaiting_positive_net_execution"
ORDER_STATUS_AWAITING_NEGATIVE_NET_EXECUTION = "awaiting_negative_net_execution"
ORDER_STATUS_PROCESSING = "processing"
ORDER_STATUS_SUCCESS = "success"
ORDER_STATUS_FAILED = "failed"
ORDER_STATUS_CANCELLED = "cancelled"

# fund_orders.side
ORDER_SIDE_BUY = "buy"
ORDER_SIDE_REDEEM = "redeem"

# fund_settlement_transfers.transfer_type
TRANSFER_TYPE_SETTLEMENT_WALLET_GAS_TOPUP = "settlement_wallet_gas_topup"
TRANSFER_TYPE_USER_WALLET_GAS_TOPUP = "user_wallet_gas_topup"
TRANSFER_TYPE_USER_BUY_USDT_TO_SETTLEMENT = "user_buy_usdt_to_settlement"

# fund_settlement_transfers.status
TRANSFER_STATUS_PENDING = "pending"
TRANSFER_STATUS_PROCESSING = "processing"
TRANSFER_STATUS_SENT = "sent"
TRANSFER_STATUS_CONFIRMED = "confirmed"
TRANSFER_STATUS_FAILED = "failed"
TRANSFER_STATUS_SKIPPED = "skipped"

# fund_runtime_state
PRICING_LOCK_REASON_SETTLEMENT = "settlement"