from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


ZERO = Decimal("0")


class NegativeSaleSnapshotError(RuntimeError):
    pass


@dataclass(frozen=True)
class NegativeSaleAsset:
    asset_type: str
    coin: str | None
    symbol: str | None
    category: str | None
    location: str | None
    side: str | None
    qty: Decimal | None
    size: Decimal | None
    usd_value: Decimal
    notional_usd: Decimal | None
    redeemable_usdt: Decimal | None
    instrument_status: str | None
    raw: dict[str, Any] = field(
        default_factory=dict
    )

    position_side: str | None = None
    position_idx: int | None = None
    close_side: str | None = None

    exposure_notional_usdt: (
        Decimal | None
    ) = None
    expected_cash_delta_usdt: (
        Decimal | None
    ) = None
    confirmed_cash_delta_usdt: (
        Decimal | None
    ) = None

    instrument_info: dict[
        str,
        Any,
    ] = field(default_factory=dict)

    requires_fund_to_unified_transfer: (
        bool
    ) = False
    use_for_deficit_cover: bool | None = None
    eligibility_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)

        for key, value in list(raw.items()):
            if isinstance(value, Decimal):
                raw[key] = str(value)

        raw["raw"] = _json_dict(
            raw["raw"]
        )
        raw["instrument_info"] = _json_dict(
            raw["instrument_info"]
        )

        return raw


@dataclass(frozen=True)
class NegativeSaleSnapshot:
    unified_usdt_available: Decimal
    fund_wallet_usdt_available: Decimal
    usdt_earn_available: Decimal
    usdt_earn_redeemable: Decimal

    spot_holdings: list[NegativeSaleAsset]
    non_stable_earn_holdings: list[
        NegativeSaleAsset
    ]
    perp_future_positions: list[
        NegativeSaleAsset
    ]
    long_options: list[NegativeSaleAsset]
    short_options: list[NegativeSaleAsset]

    total_portfolio_value_usdt: (
        Decimal | None
    )
    snapshot_ts: datetime
    raw_snapshot_json: dict[str, Any]

    funding_wallet_non_stable_assets: list[
        NegativeSaleAsset
    ] = field(default_factory=list)

    snapshot_complete: bool = True
    completeness_reasons: tuple[
        str,
        ...,
    ] = ()
    required_endpoints: tuple[
        str,
        ...,
    ] = ()
    successful_endpoints: tuple[
        str,
        ...,
    ] = ()
    failed_endpoints: tuple[
        str,
        ...,
    ] = ()
    suppressed_errors: tuple[
        dict[str, Any],
        ...,
    ] = ()

    captured_at: datetime | None = None
    source_account: str | None = None
    fund_id: int | None = None
    fund_code: str | None = None

    usdt_earn_redeemable_known: (
        bool
    ) = True

    def usdt_earn_used_as_buffer(
        self,
    ) -> Decimal:
        if not self.usdt_earn_redeemable_known:
            return ZERO

        return max(
            min(
                self.usdt_earn_available,
                self.usdt_earn_redeemable,
            ),
            ZERO,
        )

    def total_cash_like_available_usdt(
        self,
    ) -> Decimal:
        """
        Cash usable by the task-2 sale flow.

        FUND account USDT remains visible in
        diagnostics, but cannot reduce the
        required UNIFIED balance before the
        task-3 internal transfer state machine.
        """

        return (
            self.unified_usdt_available
            + self.usdt_earn_used_as_buffer()
        )

    def all_assets(
        self,
    ) -> list[NegativeSaleAsset]:
        return [
            *self.spot_holdings,
            *self.funding_wallet_non_stable_assets,
            *self.non_stable_earn_holdings,
            *self.perp_future_positions,
            *self.long_options,
            *self.short_options,
        ]

    def to_dict(self) -> dict[str, Any]:
        effective_captured_at = (
            self.captured_at
            or self.snapshot_ts
        )

        return {
            "snapshot_complete": (
                self.snapshot_complete
            ),
            "completeness_reasons": list(
                self.completeness_reasons
            ),
            "required_endpoints": list(
                self.required_endpoints
            ),
            "successful_endpoints": list(
                self.successful_endpoints
            ),
            "failed_endpoints": list(
                self.failed_endpoints
            ),
            "suppressed_errors": [
                _json_dict(row)
                for row
                in self.suppressed_errors
            ],
            "captured_at": (
                effective_captured_at.isoformat()
            ),
            "source_account": (
                self.source_account
            ),
            "fund_id": self.fund_id,
            "fund_code": self.fund_code,
            "unified_usdt_available": str(
                self.unified_usdt_available
            ),
            "fund_wallet_usdt_available": str(
                self.fund_wallet_usdt_available
            ),
            "usdt_earn_available": str(
                self.usdt_earn_available
            ),
            "usdt_earn_redeemable": str(
                self.usdt_earn_redeemable
            ),
            "usdt_earn_redeemable_known": (
                self.usdt_earn_redeemable_known
            ),
            "usdt_earn_used_as_buffer": str(
                self.usdt_earn_used_as_buffer()
            ),
            "cash_like_available_for_plan": str(
                self.total_cash_like_available_usdt()
            ),
            "total_cash_like_available_usdt": str(
                self.total_cash_like_available_usdt()
            ),
            "spot_holdings": [
                item.to_dict()
                for item in self.spot_holdings
            ],
            "funding_wallet_non_stable_assets": [
                item.to_dict()
                for item
                in self.funding_wallet_non_stable_assets
            ],
            "non_stable_earn_holdings": [
                item.to_dict()
                for item
                in self.non_stable_earn_holdings
            ],
            "perp_future_positions": [
                item.to_dict()
                for item
                in self.perp_future_positions
            ],
            "long_options": [
                item.to_dict()
                for item in self.long_options
            ],
            "short_options": [
                item.to_dict()
                for item in self.short_options
            ],
            "total_portfolio_value_usdt": (
                str(
                    self.total_portfolio_value_usdt
                )
                if self.total_portfolio_value_usdt
                is not None
                else None
            ),
            "snapshot_ts": (
                self.snapshot_ts.isoformat()
            ),
            "raw_snapshot_json": _json_dict(
                self.raw_snapshot_json
            ),
        }


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def dec(value: Any, default: str = "0") -> Decimal:
    if value is None or value == "":
        return Decimal(default)

    if isinstance(value, Decimal):
        return value

    return Decimal(str(value))


def optional_dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None

    return dec(value)


def optional_int(
    value: Any,
) -> int | None:
    if value is None or value == "":
        return None

    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def bool_value(
    value: Any,
    *,
    default: bool,
) -> bool:
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    text = str(value).strip().lower()

    if text in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return True

    if text in {
        "0",
        "false",
        "no",
        "off",
    }:
        return False

    return default


def string_tuple(
    value: Any,
) -> tuple[str, ...]:
    if value is None:
        return ()

    rows = (
        value
        if isinstance(value, list | tuple)
        else [value]
    )

    result: list[str] = []

    for row in rows:
        text = str(row).strip()

        if text:
            result.append(text)

    return tuple(result)


def dict_tuple(
    value: Any,
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, list | tuple):
        return ()

    return tuple(
        dict(row)
        for row in value
        if isinstance(row, dict)
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}

    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]

    return value


def _json_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {str(key): _json_value(value) for key, value in data.items()}


def _first(data: dict[str, Any], keys: list[str], default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]

    return default


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []

    if isinstance(value, list):
        return value

    return [value]


def _normalize_coin(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    return text.upper()


def _normalize_symbol(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    return text.upper()


def _normalize_side(value: Any) -> str | None:
    if value is None:
        return None

    text = str(value).strip().lower()
    if not text:
        return None

    if text in {"buy", "long"}:
        return "long"

    if text in {"sell", "short"}:
        return "short"

    return text


def _snapshot_ts(raw: dict[str, Any]) -> datetime:
    value = _first(raw, ["snapshot_ts", "ts", "timestamp", "created_at"])

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    if value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            pass

    return utcnow()
def _nested_first(data: dict[str, Any], paths: list[str], default: Any = None) -> Any:
    for path in paths:
        current: Any = data
        ok = True

        for part in path.split("."):
            if not isinstance(current, dict) or part not in current:
                ok = False
                break
            current = current[part]

        if ok and current is not None:
            return current

    return default


def _value_from(
    data: dict[str, Any],
    *,
    flat_keys: list[str],
    nested_paths: list[str] | None = None,
    default: Any = None,
) -> Any:
    flat = _first(data, flat_keys, None)
    if flat is not None:
        return flat

    return _nested_first(data, nested_paths or [], default)


def _asset_from_raw(
    raw_item: dict[str, Any],
    *,
    asset_type: str,
    default_category: str | None = None,
    default_location: str | None = None,
    default_side: str | None = None,
) -> NegativeSaleAsset:
    coin = _normalize_coin(
        _first(raw_item, ["coin", "asset", "baseCoin", "base_coin", "currency"])
    )
    symbol = _normalize_symbol(
        _first(raw_item, ["symbol", "instrument", "instrument_name", "ticker"])
    )
    category = _first(raw_item, ["category", "product_type"], default_category)
    location = _first(raw_item, ["location", "account", "account_type"], default_location)
    position_side = _normalize_side(
        _first(
            raw_item,
            [
                "position_side",
                "side",
            ],
            default_side,
        )
    )
    side = position_side

    close_side = _normalize_side(
        _first(
            raw_item,
            ["close_side"],
        )
    )
    position_idx = optional_int(
        _first(
            raw_item,
            [
                "position_idx",
                "positionIdx",
            ],
        )
    )

    qty = optional_dec(
        _first(raw_item, ["qty", "quantity", "free", "wallet_balance", "walletBalance"])
    )
    size = optional_dec(
        _first(raw_item, ["size", "position_size", "positionSize", "contracts"])
    )

    usd_value = dec(
        _first(
            raw_item,
            [
                "usd_value",
                "current_usd_value",
                "value_usdt",
                "valueUsd",
                "equity_usdt",
                "market_value_usdt",
            ],
            "0",
        )
    )
    notional_usd = optional_dec(
        _first(
            raw_item,
            [
                "notional_usd",
                "current_notional_usd",
                "position_value_usdt",
                "positionValue",
            ],
        )
    )
    redeemable_usdt = optional_dec(
        _first(
            raw_item,
            [
                "redeemable_usdt",
                "redeemable_amount_usdt",
                "redeemableValue",
            ],
        )
    )
    instrument_status = _first(
        raw_item,
        [
            "instrument_status",
            "symbol_status",
        ],
        None,
    )

    exposure_notional_usdt = optional_dec(
        _first(
            raw_item,
            [
                "exposure_notional_usdt",
                "exposure_notional_usd",
            ],
        )
    )

    if exposure_notional_usdt is None:
        exposure_notional_usdt = (
            notional_usd
        )

    expected_cash_delta_usdt = (
        optional_dec(
            _first(
                raw_item,
                [
                    "expected_cash_delta_usdt",
                ],
            )
        )
    )
    confirmed_cash_delta_usdt = (
        optional_dec(
            _first(
                raw_item,
                [
                    "confirmed_cash_delta_usdt",
                ],
            )
        )
    )

    instrument_info_raw = _first(
        raw_item,
        ["instrument_info"],
        {},
    )
    instrument_info = (
        dict(instrument_info_raw)
        if isinstance(
            instrument_info_raw,
            dict,
        )
        else {}
    )

    return NegativeSaleAsset(
        asset_type=asset_type,
        coin=coin,
        symbol=symbol,
        category=str(category) if category is not None else None,
        location=str(location) if location is not None else None,
        side=side,
        qty=qty,
        size=size,
        usd_value=usd_value,
        notional_usd=notional_usd,
        redeemable_usdt=redeemable_usdt,
        instrument_status=str(instrument_status) if instrument_status is not None else None,
        raw=dict(raw_item),
        position_side=position_side,
        position_idx=position_idx,
        close_side=close_side,
        exposure_notional_usdt=(
            exposure_notional_usdt
        ),
        expected_cash_delta_usdt=(
            expected_cash_delta_usdt
        ),
        confirmed_cash_delta_usdt=(
            confirmed_cash_delta_usdt
        ),
        instrument_info=instrument_info,
        requires_fund_to_unified_transfer=(
            bool_value(
                _first(
                    raw_item,
                    [
                        "requires_fund_to_unified_transfer",
                    ],
                ),
                default=False,
            )
        ),
        use_for_deficit_cover=(
            bool_value(
                _first(
                    raw_item,
                    ["use_for_deficit_cover"],
                ),
                default=False,
            )
            if "use_for_deficit_cover"
            in raw_item
            else None
        ),
        eligibility_reason=(
            str(
                _first(
                    raw_item,
                    ["eligibility_reason"],
                )
            )
            if _first(
                raw_item,
                ["eligibility_reason"],
            )
            is not None
            else None
        ),
    )


def _assets_from_raw_list(
    raw_items: Any,
    *,
    asset_type: str,
    default_category: str | None = None,
    default_location: str | None = None,
    default_side: str | None = None,
) -> list[NegativeSaleAsset]:
    result: list[NegativeSaleAsset] = []

    for raw_item in _as_list(raw_items):
        if not isinstance(raw_item, dict):
            continue

        result.append(
            _asset_from_raw(
                raw_item,
                asset_type=asset_type,
                default_category=default_category,
                default_location=default_location,
                default_side=default_side,
            )
        )

    return result


def normalize_negative_sale_snapshot(raw_snapshot: dict[str, Any]) -> NegativeSaleSnapshot:
    if not isinstance(raw_snapshot, dict):
        raise NegativeSaleSnapshotError("Snapshot must be a dict")

    unified_usdt_available = dec(
        _value_from(
            raw_snapshot,
            flat_keys=[
                "unified_usdt_available",
                "unified_usdt",
                "unified_cash_usdt",
                "unified_wallet_usdt",
            ],
            nested_paths=[
                "cash.unified_usdt_available",
                "cash.unified_usdt",
                "unified.usdt_available",
                "unified.usdt",
            ],
            default="0",
        )
    )
    fund_wallet_usdt_available = dec(
        _value_from(
            raw_snapshot,
            flat_keys=[
                "fund_wallet_usdt_available",
                "fund_wallet_usdt",
                "fund_usdt",
            ],
            nested_paths=[
                "cash.fund_wallet_usdt_available",
                "cash.fund_wallet_usdt",
                "fund_wallet.usdt_available",
                "fund_wallet.usdt",
            ],
            default="0",
        )
    )
    usdt_earn_available = dec(
        _value_from(
            raw_snapshot,
            flat_keys=[
                "usdt_earn_available",
                "usdt_earn_usdt",
                "earn_usdt",
            ],
            nested_paths=[
                "cash.usdt_earn_available",
                "cash.usdt_earn_usdt",
                "earn.usdt_available",
                "earn.usdt",
            ],
            default="0",
        )
    )
    usdt_earn_redeemable = dec(
        _value_from(
            raw_snapshot,
            flat_keys=[
                "usdt_earn_redeemable",
                "usdt_earn_redeemable_usdt",
            ],
            nested_paths=[
                "cash.usdt_earn_redeemable",
                "earn.usdt_redeemable",
                "earn.usdt_redeemable_usdt",
            ],
            default="0",
        )
    )

    spot_raw = _value_from(
        raw_snapshot,
        flat_keys=["spot_holdings", "spot", "spot_assets"],
        nested_paths=["assets.spot_holdings", "assets.spot"],
        default=[],
    )
    funding_wallet_non_stable_raw = (
        _value_from(
            raw_snapshot,
            flat_keys=[
                "funding_wallet_non_stable",
                "funding_wallet_non_stable_assets",
            ],
            nested_paths=[
                "assets.funding_wallet_non_stable",
            ],
            default=[],
        )
    )
    non_stable_earn_raw = _value_from(
        raw_snapshot,
        flat_keys=[
            "non_stable_earn_holdings",
            "non_stable_earn",
            "earn_holdings",
        ],
        nested_paths=["assets.non_stable_earn_holdings", "assets.non_stable_earn"],
        default=[],
    )
    perp_future_raw = _value_from(
        raw_snapshot,
        flat_keys=[
            "perp_future_positions",
            "perps",
            "futures",
            "derivatives",
        ],
        nested_paths=["assets.perp_future_positions", "assets.derivatives"],
        default=[],
    )
    long_options_raw = _value_from(
        raw_snapshot,
        flat_keys=["long_options", "options_long"],
        nested_paths=["assets.long_options"],
        default=[],
    )
    short_options_raw = _value_from(
        raw_snapshot,
        flat_keys=["short_options", "options_short"],
        nested_paths=["assets.short_options"],
        default=[],
    )

    total_portfolio_value_usdt = optional_dec(
        _value_from(
            raw_snapshot,
            flat_keys=[
                "total_portfolio_value_usdt",
                "total_portfolio_nav_usdt",
                "total_nav_usdt",
                "portfolio_value_usdt",
            ],
            nested_paths=[
                "summary.total_portfolio_value_usdt",
                "summary.total_nav_usdt",
            ],
            default=None,
        )
    )

    return NegativeSaleSnapshot(
        unified_usdt_available=unified_usdt_available,
        fund_wallet_usdt_available=fund_wallet_usdt_available,
        usdt_earn_available=usdt_earn_available,
        usdt_earn_redeemable=usdt_earn_redeemable,
        spot_holdings=_assets_from_raw_list(
            spot_raw,
            asset_type="spot",
            default_category="spot",
            default_location="UNIFIED",
        ),
        funding_wallet_non_stable_assets=(
            _assets_from_raw_list(
                funding_wallet_non_stable_raw,
                asset_type=(
                    "funding_wallet_asset"
                ),
                default_category="spot",
                default_location="FUND",
            )
        ),
        non_stable_earn_holdings=_assets_from_raw_list(
            non_stable_earn_raw,
            asset_type="non_stable_earn",
            default_category="earn",
            default_location="EARN",
        ),
        perp_future_positions=_assets_from_raw_list(
            perp_future_raw,
            asset_type="perp_future",
            default_category="linear",
            default_location="UNIFIED",
        ),
        long_options=_assets_from_raw_list(
            long_options_raw,
            asset_type="long_option",
            default_category="option",
            default_location="UNIFIED",
            default_side="long",
        ),
        short_options=_assets_from_raw_list(
            short_options_raw,
            asset_type="short_option",
            default_category="option",
            default_location="UNIFIED",
            default_side="short",
        ),
        total_portfolio_value_usdt=total_portfolio_value_usdt,
        snapshot_ts=_snapshot_ts(raw_snapshot),
        raw_snapshot_json=dict(raw_snapshot),
        snapshot_complete=bool_value(
            _first(
                raw_snapshot,
                ["snapshot_complete"],
            ),
            default=True,
        ),
        completeness_reasons=string_tuple(
            _first(
                raw_snapshot,
                ["completeness_reasons"],
                [],
            )
        ),
        required_endpoints=string_tuple(
            _first(
                raw_snapshot,
                ["required_endpoints"],
                [],
            )
        ),
        successful_endpoints=string_tuple(
            _first(
                raw_snapshot,
                ["successful_endpoints"],
                [],
            )
        ),
        failed_endpoints=string_tuple(
            _first(
                raw_snapshot,
                ["failed_endpoints"],
                [],
            )
        ),
        suppressed_errors=dict_tuple(
            _first(
                raw_snapshot,
                ["suppressed_errors"],
                [],
            )
        ),
        captured_at=_snapshot_ts(
            {
                "snapshot_ts": _first(
                    raw_snapshot,
                    [
                        "captured_at",
                        "snapshot_ts",
                    ],
                )
            }
        ),
        source_account=(
            str(
                _first(
                    raw_snapshot,
                    ["source_account"],
                )
            )
            if _first(
                raw_snapshot,
                ["source_account"],
            )
            is not None
            else None
        ),
        fund_id=optional_int(
            _first(
                raw_snapshot,
                ["fund_id"],
            )
        ),
        fund_code=(
            str(
                _first(
                    raw_snapshot,
                    ["fund_code"],
                )
            )
            if _first(
                raw_snapshot,
                ["fund_code"],
            )
            is not None
            else None
        ),
        usdt_earn_redeemable_known=(
            bool_value(
                _value_from(
                    raw_snapshot,
                    flat_keys=[
                        "usdt_earn_redeemable_known",
                    ],
                    nested_paths=[
                        "cash.usdt_earn_redeemable_known",
                    ],
                    default=True,
                ),
                default=True,
            )
        ),
    )


def build_negative_sale_snapshot_mock(
    *,
    snapshot_json: dict[str, Any] | None = None,
    mock_snapshot_file: str | None = None,
) -> NegativeSaleSnapshot:
    """
    Stage 23.2 mock/local snapshot reader.

    Safety:
    - no real Bybit calls;
    - no trades;
    - no transfers/withdrawals;
    - no BSC calls;
    - no accounting finalization.
    """
    if snapshot_json is not None and mock_snapshot_file is not None:
        raise NegativeSaleSnapshotError(
            "Pass either snapshot_json or mock_snapshot_file, not both"
        )

    if mock_snapshot_file:
        import json
        from pathlib import Path

        raw = json.loads(Path(mock_snapshot_file).read_text(encoding="utf-8"))
        return normalize_negative_sale_snapshot(raw)

    if snapshot_json is not None:
        return normalize_negative_sale_snapshot(snapshot_json)

    return normalize_negative_sale_snapshot(
        {
            "snapshot_ts": utcnow().isoformat(),
            "cash": {
                "unified_usdt_available": "0",
                "fund_wallet_usdt_available": "0",
                "usdt_earn_available": "0",
                "usdt_earn_redeemable": "0",
            },
            "assets": {
                "spot": [],
                "non_stable_earn": [],
                "perp_future_positions": [],
                "long_options": [],
                "short_options": [],
            },
            "summary": {
                "total_portfolio_value_usdt": "0",
            },
        }
    )