from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.config import settings


@dataclass(frozen=True)
class AllocationExecutionConfig:
    execution_enabled: bool

    liquidity_corridor_pct: Decimal
    market_slippage_pct: Decimal
    min_fill_ratio: Decimal

    native_iceberg_order_count: int
    max_active_strategy_orders: int

    sliced_ioc_slices: int
    sliced_ioc_chase_bps: int

    short_option_liquidity_mult: Decimal

    max_im_rate: Decimal
    max_mm_rate: Decimal

    @property
    def liquidity_corridor_fraction(self) -> Decimal:
        return self.liquidity_corridor_pct / Decimal("100")

    @property
    def market_slippage_fraction(self) -> Decimal:
        return self.market_slippage_pct / Decimal("100")

    @property
    def sliced_ioc_chase_fraction(self) -> Decimal:
        return self.sliced_ioc_chase_bps / Decimal("10000")


def get_allocation_execution_config() -> AllocationExecutionConfig:
    return AllocationExecutionConfig(
        execution_enabled=bool(settings.ALLOCATION_EXECUTION_ENABLED),
        liquidity_corridor_pct=Decimal(str(settings.ALLOCATION_LIQUIDITY_CORRIDOR_PCT)),
        market_slippage_pct=Decimal(str(settings.ALLOCATION_MARKET_SLIPPAGE_PCT)),
        min_fill_ratio=Decimal(str(settings.ALLOCATION_MIN_FILL_RATIO)),
        native_iceberg_order_count=int(settings.ALLOCATION_NATIVE_ICEBERG_ORDER_COUNT),
        max_active_strategy_orders=int(settings.ALLOCATION_MAX_ACTIVE_STRATEGY_ORDERS),
        sliced_ioc_slices=int(settings.ALLOCATION_SLICED_IOC_SLICES),
        sliced_ioc_chase_bps=int(settings.ALLOCATION_SLICED_IOC_CHASE_BPS),
        short_option_liquidity_mult=Decimal(str(settings.ALLOCATION_SHORT_OPTION_LIQUIDITY_MULT)),
        max_im_rate=Decimal(str(settings.ALLOCATION_MAX_IM_RATE)),
        max_mm_rate=Decimal(str(settings.ALLOCATION_MAX_MM_RATE)),
    )