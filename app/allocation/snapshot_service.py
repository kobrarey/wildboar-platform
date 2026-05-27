from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


STABLECOINS = {
    "USDT",
    "USDC",
    "DAI",
    "BUSD",
    "TUSD",
    "USDE",
    "FDUSD",
    "PYUSD",
    "USD",
    "USDP",
}

_DELIVERY_INVERSE_RE = re.compile(r"USD[FGHJKMNQUVXZ]\d{2}$")
_DELIVERY_LINEAR_RE = re.compile(r"-\d{2}[A-Z]{3}\d{2}$")


class AllocationSnapshotError(RuntimeError):
    pass


@dataclass(frozen=True)
class AllocationAccountRisk:
    total_equity_usdt: Decimal
    total_wallet_balance_usdt: Decimal
    total_available_usdt: Decimal
    total_initial_margin_usdt: Decimal
    total_maintenance_margin_usdt: Decimal
    account_im_rate: Decimal
    account_mm_rate: Decimal

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_equity_usdt": decimal_to_str(self.total_equity_usdt),
            "total_wallet_balance_usdt": decimal_to_str(self.total_wallet_balance_usdt),
            "total_available_usdt": decimal_to_str(self.total_available_usdt),
            "total_initial_margin_usdt": decimal_to_str(self.total_initial_margin_usdt),
            "total_maintenance_margin_usdt": decimal_to_str(self.total_maintenance_margin_usdt),
            "account_im_rate": decimal_to_str(self.account_im_rate),
            "account_mm_rate": decimal_to_str(self.account_mm_rate),
        }


@dataclass(frozen=True)
class AllocationSnapshotHolding:
    leg_group: str
    leg_type: str
    coin: str | None = None
    symbol: str | None = None
    category: str | None = None
    side: str | None = None
    location: str | None = None
    size: Decimal | None = None
    usd_value: Decimal | None = None
    notional_usd: Decimal | None = None
    avg_price: Decimal | None = None
    mark_price: Decimal | None = None
    leverage: Decimal | None = None
    product: str | None = None
    product_category: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "leg_group": self.leg_group,
            "leg_type": self.leg_type,
            "coin": self.coin,
            "symbol": self.symbol,
            "category": self.category,
            "side": self.side,
            "location": self.location,
            "size": decimal_to_str(self.size),
            "usd_value": decimal_to_str(self.usd_value),
            "notional_usd": decimal_to_str(self.notional_usd),
            "avg_price": decimal_to_str(self.avg_price),
            "mark_price": decimal_to_str(self.mark_price),
            "leverage": decimal_to_str(self.leverage),
            "product": self.product,
            "product_category": self.product_category,
            "extra": json_safe(self.extra),
        }


@dataclass(frozen=True)
class AllocationSnapshot:
    fund_id: int
    fund_code: str
    snapshot_ts: datetime
    account_type: str
    risk: AllocationAccountRisk
    holdings: list[AllocationSnapshotHolding]
    raw_summary_json: dict[str, Any]
    snapshot_source: str = "mock_fixture"

    @property
    def total_equity_usdt(self) -> Decimal:
        return self.risk.total_equity_usdt

    @property
    def total_wallet_balance_usdt(self) -> Decimal:
        return self.risk.total_wallet_balance_usdt

    @property
    def total_available_usdt(self) -> Decimal:
        return self.risk.total_available_usdt

    @property
    def total_initial_margin_usdt(self) -> Decimal:
        return self.risk.total_initial_margin_usdt

    @property
    def total_maintenance_margin_usdt(self) -> Decimal:
        return self.risk.total_maintenance_margin_usdt

    def raw_cash_usdt(self) -> Decimal:
        total = Decimal("0")
        for holding in self.holdings:
            if holding.leg_group == "cash" and (holding.coin or "").upper() in STABLECOINS:
                total += dec(holding.usd_value)
        return total

    def holdings_by_group(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for holding in self.holdings:
            out[holding.leg_group] = out.get(holding.leg_group, 0) + 1
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "fund_id": self.fund_id,
            "fund_code": self.fund_code,
            "snapshot_ts": self.snapshot_ts.isoformat(),
            "account_type": self.account_type,
            "snapshot_source": self.snapshot_source,
            "risk": self.risk.to_dict(),
            "holdings": [holding.to_dict() for holding in self.holdings],
            "raw_summary_json": json_safe(self.raw_summary_json),
            "derived": {
                "raw_cash_usdt": decimal_to_str(self.raw_cash_usdt()),
                "holdings_by_group": self.holdings_by_group(),
            },
        }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def dec(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)

    if isinstance(value, Decimal):
        return value

    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal(default)


def decimal_to_str(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return str(value)


def json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}

    if isinstance(value, list):
        return [json_safe(v) for v in value]

    if isinstance(value, tuple):
        return [json_safe(v) for v in value]

    return value


def get_any(data: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return default


def parse_ts(value: Any) -> datetime:
    if value is None or value == "":
        return utcnow()

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

    if isinstance(value, int | float):
        raw = float(value)
        if raw > 10_000_000_000:
            raw = raw / 1000
        return datetime.fromtimestamp(raw, tz=timezone.utc)

    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return utcnow()

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)

    return parsed


def normalize_coin(value: Any) -> str | None:
    if value is None:
        return None

    coin = str(value).strip().upper()
    return coin or None


def normalize_symbol(value: Any) -> str | None:
    if value is None:
        return None

    symbol = str(value).strip().upper()
    return symbol or None


def normalize_side(value: Any) -> str | None:
    if value is None:
        return None

    raw = str(value).strip()
    low = raw.lower()

    if low in {"buy", "long"}:
        return "Buy"

    if low in {"sell", "short"}:
        return "Sell"

    return raw or None


def classify_contract(category: str | None, symbol: str | None, explicit_contract_type: str | None = None) -> str:
    if explicit_contract_type:
        return explicit_contract_type

    cat = (category or "").lower()
    sym = symbol or ""

    if cat == "option":
        return "Option"

    if cat == "spot":
        return "Spot"

    if cat == "linear":
        if _DELIVERY_LINEAR_RE.search(sym) or "-" in sym:
            return "LinearFutures"
        return "LinearPerpetual"

    if cat == "inverse":
        if _DELIVERY_INVERSE_RE.search(sym):
            return "InverseFutures"
        return "InversePerpetual"

    return cat.title() if cat else "Unknown"


def infer_spot_symbol(coin: str | None, raw_symbol: Any = None) -> str | None:
    symbol = normalize_symbol(raw_symbol)
    if symbol:
        return symbol

    if not coin:
        return None

    if coin.upper() in STABLECOINS:
        return None

    return f"{coin.upper()}USDT"


def _risk_from_payload(payload: dict[str, Any]) -> AllocationAccountRisk:
    summary = payload.get("summary") or payload.get("risk") or payload

    return AllocationAccountRisk(
        total_equity_usdt=dec(
            get_any(summary, "total_equity_usdt", "total_equity", "totalEquity")
        ),
        total_wallet_balance_usdt=dec(
            get_any(summary, "total_wallet_balance_usdt", "total_wallet_balance", "totalWalletBalance")
        ),
        total_available_usdt=dec(
            get_any(summary, "total_available_usdt", "total_available", "totalAvailableBalance")
        ),
        total_initial_margin_usdt=dec(
            get_any(summary, "total_initial_margin_usdt", "total_initial_margin", "totalInitialMargin")
        ),
        total_maintenance_margin_usdt=dec(
            get_any(summary, "total_maintenance_margin_usdt", "total_maintenance_margin", "totalMaintenanceMargin")
        ),
        account_im_rate=dec(
            get_any(summary, "account_im_rate", "accountIMRate")
        ),
        account_mm_rate=dec(
            get_any(summary, "account_mm_rate", "accountMMRate")
        ),
    )


def _wallet_items(payload: dict[str, Any], *names: str) -> list[dict[str, Any]]:
    for name in names:
        value = payload.get(name)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]

    wallet = payload.get("wallet") or {}
    if isinstance(wallet, dict):
        for name in names:
            value = wallet.get(name)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]

    return []


def _earn_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ["earn", "earn_holdings", "earnHoldings"]:
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]

    return []


def _position_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ["positions", "derivatives"]:
        value = payload.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)]

    return []


def _other_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    value = payload.get("other")
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]

    return []


def _parse_unified_wallet_holdings(payload: dict[str, Any]) -> list[AllocationSnapshotHolding]:
    out: list[AllocationSnapshotHolding] = []

    for item in _wallet_items(payload, "unified", "coins_uta", "coinsUnified"):
        coin = normalize_coin(get_any(item, "coin", "currency"))
        if not coin:
            continue

        size = dec(get_any(item, "wallet_balance", "walletBalance", "equity", "size", "amount"))
        usd_value = dec(get_any(item, "usd_value", "usdValue", "value_usdt", "value"))
        available = dec(get_any(item, "available", "availableToWithdraw", "free"))
        locked = dec(get_any(item, "locked"))
        mark_price = dec(get_any(item, "mark_price", "markPrice", "lastPrice"), default="0")

        if size == 0 and usd_value == 0:
            continue

        if coin in STABLECOINS:
            out.append(
                AllocationSnapshotHolding(
                    leg_group="cash",
                    leg_type="stable_cash",
                    coin=coin,
                    symbol=None,
                    category="wallet",
                    side=None,
                    location="UNIFIED",
                    size=size,
                    usd_value=usd_value if usd_value != 0 else size,
                    notional_usd=None,
                    avg_price=None,
                    mark_price=mark_price if mark_price != 0 else None,
                    leverage=None,
                    product=None,
                    product_category=None,
                    extra={
                        "available": available,
                        "locked": locked,
                        "unrealised_pnl": dec(get_any(item, "unrealised_pnl", "unrealisedPnl")),
                        "cum_realised_pnl": dec(get_any(item, "cum_realised_pnl", "cumRealisedPnl")),
                        "bonus": dec(get_any(item, "bonus")),
                        "source": "unified_wallet",
                    },
                )
            )
        else:
            out.append(
                AllocationSnapshotHolding(
                    leg_group="spot",
                    leg_type="spot_holding",
                    coin=coin,
                    symbol=infer_spot_symbol(coin, get_any(item, "symbol")),
                    category="spot",
                    side=None,
                    location="UNIFIED",
                    size=size,
                    usd_value=usd_value,
                    notional_usd=None,
                    avg_price=None,
                    mark_price=mark_price if mark_price != 0 else None,
                    leverage=None,
                    product=None,
                    product_category=None,
                    extra={
                        "available": available,
                        "locked": locked,
                        "unrealised_pnl": dec(get_any(item, "unrealised_pnl", "unrealisedPnl")),
                        "cum_realised_pnl": dec(get_any(item, "cum_realised_pnl", "cumRealisedPnl")),
                        "bonus": dec(get_any(item, "bonus")),
                        "source": "unified_wallet",
                    },
                )
            )

    return out


def _parse_funding_wallet_holdings(payload: dict[str, Any]) -> list[AllocationSnapshotHolding]:
    out: list[AllocationSnapshotHolding] = []

    for item in _wallet_items(payload, "funding", "coins_funding", "fundingWallet"):
        coin = normalize_coin(get_any(item, "coin", "currency"))
        if not coin:
            continue

        size = dec(get_any(item, "wallet_balance", "walletBalance", "amount", "size"))
        usd_value = dec(get_any(item, "usd_value", "usdValue", "value_usdt", "value"))
        available = dec(get_any(item, "available", "transferBalance", "free", "transferable"))

        if size == 0 and usd_value == 0:
            continue

        out.append(
            AllocationSnapshotHolding(
                leg_group="funding_wallet",
                leg_type="funding_wallet_cash" if coin in STABLECOINS else "funding_wallet_asset",
                coin=coin,
                symbol=infer_spot_symbol(coin, get_any(item, "symbol")),
                category="funding",
                side=None,
                location="FUND",
                size=size,
                usd_value=usd_value if usd_value != 0 else (size if coin in STABLECOINS else Decimal("0")),
                notional_usd=None,
                avg_price=None,
                mark_price=dec(get_any(item, "mark_price", "markPrice", "lastPrice"), default="0") or None,
                leverage=None,
                product=None,
                product_category=None,
                extra={
                    "available": available,
                    "source": "funding_wallet",
                },
            )
        )

    return out


def _parse_earn_holdings(payload: dict[str, Any]) -> list[AllocationSnapshotHolding]:
    out: list[AllocationSnapshotHolding] = []

    for item in _earn_items(payload):
        coin = normalize_coin(get_any(item, "coin", "currency"))
        if not coin:
            continue

        amount = dec(get_any(item, "amount", "orderQty", "size"))
        usd_value = dec(get_any(item, "usd_value", "usdValue", "totalPrincipalAmount", "value_usdt"))
        product = str(get_any(item, "product", "product_name", default="Earn") or "Earn")
        product_category = str(get_any(item, "product_category", "category", default="") or "")

        if amount == 0 and usd_value == 0:
            continue

        out.append(
            AllocationSnapshotHolding(
                leg_group="earn",
                leg_type="earn_holding",
                coin=coin,
                symbol=infer_spot_symbol(coin, get_any(item, "symbol")),
                category="earn",
                side=None,
                location="EARN",
                size=amount,
                usd_value=usd_value,
                notional_usd=None,
                avg_price=None,
                mark_price=dec(get_any(item, "mark_price", "markPrice", "lastPrice"), default="0") or None,
                leverage=None,
                product=product,
                product_category=product_category,
                extra={
                    "apr": dec(get_any(item, "apr", "annualRate")),
                    "status": get_any(item, "status", "extra", default=""),
                    "source": "earn",
                },
            )
        )

    return out


def _parse_position_holdings(payload: dict[str, Any]) -> list[AllocationSnapshotHolding]:
    out: list[AllocationSnapshotHolding] = []

    for item in _position_items(payload):
        category = str(get_any(item, "category", default="") or "").lower()
        symbol = normalize_symbol(get_any(item, "symbol"))
        side = normalize_side(get_any(item, "side"))
        size = dec(get_any(item, "size", "qty", "position_size"))
        avg_price = dec(get_any(item, "avg_price", "avgPrice", "entryPrice"))
        mark_price = dec(get_any(item, "mark_price", "markPrice"))
        notional = dec(get_any(item, "notional_usd", "notionalUsd", "positionValue", "value_usdt"))
        leverage = dec(get_any(item, "leverage"))
        contract_type = classify_contract(
            category,
            symbol,
            explicit_contract_type=get_any(item, "contract_type", "contractType"),
        )

        if size == 0 and notional == 0:
            continue

        if notional == 0 and size != 0 and mark_price != 0:
            notional = abs(size * mark_price)

        if contract_type in {"LinearPerpetual", "InversePerpetual"}:
            leg_group = "perp"
            leg_type = "perp_position"
        elif contract_type in {"LinearFutures", "InverseFutures"}:
            leg_group = "future"
            leg_type = "future_position"
        elif contract_type == "Option" and (side or "").lower() == "sell":
            leg_group = "short_option"
            leg_type = "short_option_position"
        elif contract_type == "Option":
            leg_group = "long_option"
            leg_type = "long_option_position"
        else:
            leg_group = "other"
            leg_type = "unknown_position"

        out.append(
            AllocationSnapshotHolding(
                leg_group=leg_group,
                leg_type=leg_type,
                coin=normalize_coin(get_any(item, "coin", "baseCoin")),
                symbol=symbol,
                category=category or None,
                side=side,
                location="UNIFIED",
                size=size,
                usd_value=None,
                notional_usd=abs(notional),
                avg_price=avg_price if avg_price != 0 else None,
                mark_price=mark_price if mark_price != 0 else None,
                leverage=leverage if leverage != 0 else None,
                product=contract_type,
                product_category=category or None,
                extra={
                    "unrealised_pnl": dec(get_any(item, "unrealised_pnl", "unrealisedPnl")),
                    "cum_realised_pnl": dec(get_any(item, "cum_realised_pnl", "cumRealisedPnl")),
                    "source": "position",
                    "contract_type": contract_type,
                },
            )
        )

    return out


def _parse_other_holdings(payload: dict[str, Any]) -> list[AllocationSnapshotHolding]:
    out: list[AllocationSnapshotHolding] = []

    for index, item in enumerate(_other_items(payload), start=1):
        coin = normalize_coin(get_any(item, "coin", "currency"))
        symbol = normalize_symbol(get_any(item, "symbol"))
        size = dec(get_any(item, "size", "amount"))
        usd_value = dec(get_any(item, "usd_value", "usdValue", "value_usdt", "value"))

        if size == 0 and usd_value == 0:
            continue

        out.append(
            AllocationSnapshotHolding(
                leg_group="other",
                leg_type=str(get_any(item, "leg_type", "type", default="other") or "other"),
                coin=coin,
                symbol=symbol,
                category=str(get_any(item, "category", default="other") or "other"),
                side=normalize_side(get_any(item, "side")),
                location=str(get_any(item, "location", default="OTHER") or "OTHER"),
                size=size,
                usd_value=usd_value,
                notional_usd=dec(get_any(item, "notional_usd", "notionalUsd"), default="0") or None,
                avg_price=dec(get_any(item, "avg_price", "avgPrice"), default="0") or None,
                mark_price=dec(get_any(item, "mark_price", "markPrice"), default="0") or None,
                leverage=dec(get_any(item, "leverage"), default="0") or None,
                product=str(get_any(item, "product", default="other") or "other"),
                product_category=str(get_any(item, "product_category", default="other") or "other"),
                extra={
                    "source": "other",
                    "source_index": index,
                    "raw": json_safe(item),
                },
            )
        )

    return out


def build_allocation_snapshot_from_payload(
    *,
    fund_id: int,
    fund_code: str,
    payload: dict[str, Any],
    snapshot_source: str = "mock_fixture",
) -> AllocationSnapshot:
    if not isinstance(payload, dict):
        raise AllocationSnapshotError("Allocation snapshot payload must be a dict")

    risk = _risk_from_payload(payload)
    snapshot_ts = parse_ts(get_any(payload, "snapshot_ts", "snapshotTs", "ts"))

    account_type = str(
        get_any(payload, "account_type", "accountType", default="UNIFIED") or "UNIFIED"
    )

    holdings: list[AllocationSnapshotHolding] = []
    holdings.extend(_parse_unified_wallet_holdings(payload))
    holdings.extend(_parse_funding_wallet_holdings(payload))
    holdings.extend(_parse_earn_holdings(payload))
    holdings.extend(_parse_position_holdings(payload))
    holdings.extend(_parse_other_holdings(payload))

    return AllocationSnapshot(
        fund_id=fund_id,
        fund_code=fund_code,
        snapshot_ts=snapshot_ts,
        account_type=account_type,
        risk=risk,
        holdings=holdings,
        raw_summary_json=json_safe(payload),
        snapshot_source=snapshot_source,
    )


def build_allocation_snapshot_from_fixture_file(
    *,
    fund_id: int,
    fund_code: str,
    path: str | Path,
) -> AllocationSnapshot:
    fixture_path = Path(path)

    if not fixture_path.exists():
        raise AllocationSnapshotError(f"Snapshot fixture file not found: {fixture_path}")

    with fixture_path.open("r", encoding="utf-8-sig") as f:
        payload = json.load(f)

    return build_allocation_snapshot_from_payload(
        fund_id=fund_id,
        fund_code=fund_code,
        payload=payload,
        snapshot_source=f"fixture:{fixture_path.name}",
    )


def build_empty_allocation_snapshot(
    *,
    fund_id: int,
    fund_code: str,
    snapshot_ts: datetime | None = None,
) -> AllocationSnapshot:
    risk = AllocationAccountRisk(
        total_equity_usdt=Decimal("0"),
        total_wallet_balance_usdt=Decimal("0"),
        total_available_usdt=Decimal("0"),
        total_initial_margin_usdt=Decimal("0"),
        total_maintenance_margin_usdt=Decimal("0"),
        account_im_rate=Decimal("0"),
        account_mm_rate=Decimal("0"),
    )

    return AllocationSnapshot(
        fund_id=fund_id,
        fund_code=fund_code,
        snapshot_ts=snapshot_ts or utcnow(),
        account_type="UNIFIED",
        risk=risk,
        holdings=[],
        raw_summary_json={},
        snapshot_source="empty",
    )