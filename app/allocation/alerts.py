from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy.orm import Session

from app.allocation.statuses import (
    ALLOCATION_BATCH_STATUS_ALLOCATION_FAILED_REQUIRES_REVIEW,
)
from app.config import settings


class AllocationAlertError(RuntimeError):
    pass


@dataclass(frozen=True)
class AllocationAlert:
    event_type: str
    severity: str
    allocation_batch_id: int | None
    fund_code: str | None
    message: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        raw = asdict(self)
        raw["metadata"] = _json_dict(raw["metadata"])
        return raw


@dataclass(frozen=True)
class AllocationAlertDelivery:
    event_type: str
    severity: str
    attempted: bool
    sent: bool
    skipped: bool
    skipped_reason: str | None
    error: str | None
    message: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AllocationAlertResult:
    allocation_batch_id: int | None
    alerts_enabled: bool
    mock_only: bool
    alerts: list[AllocationAlert]
    deliveries: list[AllocationAlertDelivery]
    sent_count: int
    skipped_count: int
    error_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "allocation_batch_id": self.allocation_batch_id,
            "alerts_enabled": self.alerts_enabled,
            "mock_only": self.mock_only,
            "alerts": [alert.to_dict() for alert in self.alerts],
            "deliveries": [delivery.to_dict() for delivery in self.deliveries],
            "sent_count": self.sent_count,
            "skipped_count": self.skipped_count,
            "error_count": self.error_count,
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
    except Exception:
        return Decimal(default)


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


def _report_int(report: dict[str, Any], key: str) -> int:
    value = report.get(key)

    if value is None or value == "":
        return 0

    try:
        return int(value)
    except Exception:
        return 0


def _report_decimal(report: dict[str, Any], key: str) -> Decimal:
    return dec(report.get(key))


def _report_list(report: dict[str, Any], key: str) -> list[str]:
    value = report.get(key)

    if value is None:
        return []

    if isinstance(value, list):
        return [str(item) for item in value]

    return [str(value)]


def _allocation_batch_id(report: dict[str, Any]) -> int | None:
    value = report.get("allocation_batch_id")

    if value is None or value == "":
        return None

    try:
        return int(value)
    except Exception:
        return None


def _fund_code(report: dict[str, Any]) -> str | None:
    value = report.get("fund_code")
    if value is None:
        return None

    raw = str(value).strip()
    return raw or None


def _alert(
    *,
    event_type: str,
    severity: str,
    report: dict[str, Any],
    message: str,
    metadata: dict[str, Any] | None = None,
) -> AllocationAlert:
    return AllocationAlert(
        event_type=event_type,
        severity=severity,
        allocation_batch_id=_allocation_batch_id(report),
        fund_code=_fund_code(report),
        message=message,
        metadata=metadata or {},
    )


def _is_failed_review_report(report: dict[str, Any]) -> bool:
    return (
        str(report.get("status") or "")
        == ALLOCATION_BATCH_STATUS_ALLOCATION_FAILED_REQUIRES_REVIEW
        or _report_int(report, "failed_requires_review_count") > 0
        or _report_int(report, "failed_legs") > 0
    )


def _has_unknown_state(report: dict[str, Any]) -> bool:
    critical_errors = _report_list(report, "critical_errors")

    if _report_int(report, "active_legs") > 0:
        return True

    keywords = (
        "unknown",
        "not-final",
        "active",
        "processing",
        "reconciliation",
        "inconsistent",
    )

    return any(
        any(keyword in error.lower() for keyword in keywords)
        for error in critical_errors
    )


def _has_reconciliation_inconsistency(report: dict[str, Any]) -> bool:
    critical_errors = _report_list(report, "critical_errors")

    keywords = (
        "reconciliation",
        "inconsistent",
        "unknown execution state",
        "unknown state",
        "unhandled residual",
    )

    return any(
        any(keyword in error.lower() for keyword in keywords)
        for error in critical_errors
    )


def _has_margin_breach_requiring_attention(report: dict[str, Any]) -> bool:
    critical_errors = _report_list(report, "critical_errors")
    warnings = _report_list(report, "warnings")

    critical_margin_keywords = (
        "margin breach",
        "margin_guard_status=uncertain",
        "margin guard uncertain",
        "account risk",
    )

    combined = critical_errors + warnings

    return any(
        any(keyword in item.lower() for keyword in critical_margin_keywords)
        for item in combined
    )


def _has_material_residual_cash(report: dict[str, Any]) -> bool:
    residual_cash = _report_decimal(report, "residual_cash_usdt")
    threshold = dec(settings.ALLOCATION_RESIDUAL_CASH_ALERT_THRESHOLD_USDT)

    if threshold < Decimal("0"):
        threshold = Decimal("0")

    return residual_cash >= threshold and residual_cash > Decimal("0")


def build_allocation_alerts(
    report: dict[str, Any],
) -> list[AllocationAlert]:
    alerts: list[AllocationAlert] = []

    allocation_batch_id = _allocation_batch_id(report)
    fund_code = _fund_code(report)
    status = str(report.get("status") or "")

    if settings.ALLOCATION_FAILED_REVIEW_ALERTS and _is_failed_review_report(report):
        alerts.append(
            _alert(
                event_type="allocation_failed_requires_review",
                severity="critical",
                report=report,
                message=(
                    f"Allocation batch requires review: "
                    f"batch_id={allocation_batch_id}, fund={fund_code}, status={status}"
                ),
                metadata={
                    "status": status,
                    "failed_legs": _report_int(report, "failed_legs"),
                    "failed_requires_review_count": _report_int(
                        report,
                        "failed_requires_review_count",
                    ),
                    "critical_errors": _report_list(report, "critical_errors"),
                },
            )
        )

    if settings.ALLOCATION_UNKNOWN_STATE_ALERTS and _has_unknown_state(report):
        alerts.append(
            _alert(
                event_type="allocation_unknown_state",
                severity="critical",
                report=report,
                message=(
                    f"Allocation batch has unknown/active state: "
                    f"batch_id={allocation_batch_id}, fund={fund_code}, active_legs="
                    f"{_report_int(report, 'active_legs')}"
                ),
                metadata={
                    "active_legs": _report_int(report, "active_legs"),
                    "critical_errors": _report_list(report, "critical_errors"),
                },
            )
        )

    if settings.ALLOCATION_UNKNOWN_STATE_ALERTS and _has_reconciliation_inconsistency(report):
        alerts.append(
            _alert(
                event_type="allocation_reconciliation_inconsistency",
                severity="critical",
                report=report,
                message=(
                    f"Allocation reconciliation inconsistency: "
                    f"batch_id={allocation_batch_id}, fund={fund_code}"
                ),
                metadata={
                    "critical_errors": _report_list(report, "critical_errors"),
                    "warnings": _report_list(report, "warnings"),
                },
            )
        )

    if settings.ALLOCATION_MARGIN_BREACH_ALERTS and _has_margin_breach_requiring_attention(report):
        alerts.append(
            _alert(
                event_type="allocation_margin_breach_attention",
                severity="critical",
                report=report,
                message=(
                    f"Allocation margin guard requires attention: "
                    f"batch_id={allocation_batch_id}, fund={fund_code}"
                ),
                metadata={
                    "margin_guard_skip_count": _report_int(
                        report,
                        "margin_guard_skip_count",
                    ),
                    "critical_errors": _report_list(report, "critical_errors"),
                    "warnings": _report_list(report, "warnings"),
                },
            )
        )

    if _has_material_residual_cash(report):
        alerts.append(
            _alert(
                event_type="allocation_material_residual_cash",
                severity="warning",
                report=report,
                message=(
                    f"Material allocation residual cash remains: "
                    f"batch_id={allocation_batch_id}, fund={fund_code}, "
                    f"residual_cash_usdt={_report_decimal(report, 'residual_cash_usdt')}"
                ),
                metadata={
                    "residual_cash_usdt": _report_decimal(report, "residual_cash_usdt"),
                    "threshold_usdt": dec(
                        settings.ALLOCATION_RESIDUAL_CASH_ALERT_THRESHOLD_USDT
                    ),
                },
            )
        )

    return _dedupe_alerts(alerts)


def _dedupe_alerts(alerts: list[AllocationAlert]) -> list[AllocationAlert]:
    seen: set[tuple[str, int | None, str | None]] = set()
    deduped: list[AllocationAlert] = []

    for alert in alerts:
        key = (
            alert.event_type,
            alert.allocation_batch_id,
            alert.fund_code,
        )

        if key in seen:
            continue

        seen.add(key)
        deduped.append(alert)

    return deduped


def format_allocation_alert_message(alert: AllocationAlert) -> str:
    lines = [
        f"[Wild Boar allocation] {alert.severity.upper()}",
        f"event: {alert.event_type}",
        f"batch_id: {alert.allocation_batch_id}",
        f"fund: {alert.fund_code}",
        f"message: {alert.message}",
    ]

    if alert.metadata:
        compact_metadata = _json_dict(alert.metadata)
        lines.append(f"metadata: {compact_metadata}")

    return "\n".join(lines)


def _delivery_skipped(
    alert: AllocationAlert,
    *,
    reason: str,
) -> AllocationAlertDelivery:
    return AllocationAlertDelivery(
        event_type=alert.event_type,
        severity=alert.severity,
        attempted=False,
        sent=False,
        skipped=True,
        skipped_reason=reason,
        error=None,
        message=format_allocation_alert_message(alert),
    )


def _delivery_error(
    alert: AllocationAlert,
    *,
    error: str,
) -> AllocationAlertDelivery:
    return AllocationAlertDelivery(
        event_type=alert.event_type,
        severity=alert.severity,
        attempted=True,
        sent=False,
        skipped=False,
        skipped_reason=None,
        error=error,
        message=format_allocation_alert_message(alert),
    )


def _delivery_sent(alert: AllocationAlert) -> AllocationAlertDelivery:
    return AllocationAlertDelivery(
        event_type=alert.event_type,
        severity=alert.severity,
        attempted=True,
        sent=True,
        skipped=False,
        skipped_reason=None,
        error=None,
        message=format_allocation_alert_message(alert),
    )


def send_allocation_alerts_mock(
    *,
    report: dict[str, Any],
    sender: Callable[[str], Any] | None = None,
    mock_only: bool = True,
) -> AllocationAlertResult:
    """
    Stage 22.6 alert delivery.

    Default behavior is mock/suppressed:
    - no Telegram network call;
    - no crash if TELEGRAM_* settings are missing;
    - no success spam;
    - only critical/material residual alerts are built.
    """
    alerts = build_allocation_alerts(report)

    deliveries: list[AllocationAlertDelivery] = []
    alerts_enabled = bool(settings.ALLOCATION_ALERTS_ENABLED)

    for alert in alerts:
        if not alerts_enabled:
            deliveries.append(
                _delivery_skipped(
                    alert,
                    reason="ALLOCATION_ALERTS_ENABLED=false",
                )
            )
            continue

        if mock_only:
            deliveries.append(
                _delivery_skipped(
                    alert,
                    reason="Stage 22.6 mock_only alert delivery",
                )
            )
            continue

        if sender is None:
            deliveries.append(
                _delivery_skipped(
                    alert,
                    reason="No alert sender configured",
                )
            )
            continue

        try:
            sender(format_allocation_alert_message(alert))
            deliveries.append(_delivery_sent(alert))
        except Exception as exc:
            deliveries.append(
                _delivery_error(
                    alert,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    sent_count = sum(1 for delivery in deliveries if delivery.sent)
    skipped_count = sum(1 for delivery in deliveries if delivery.skipped)
    error_count = sum(1 for delivery in deliveries if delivery.error)

    return AllocationAlertResult(
        allocation_batch_id=_allocation_batch_id(report),
        alerts_enabled=alerts_enabled,
        mock_only=mock_only,
        alerts=alerts,
        deliveries=deliveries,
        sent_count=sent_count,
        skipped_count=skipped_count,
        error_count=error_count,
    )


def send_critical_allocation_alerts_mock(
    db: Session | None = None,
    *,
    report: dict[str, Any],
    sender: Callable[[str], Any] | None = None,
    mock_only: bool = True,
) -> AllocationAlertResult:
    # db is accepted for orchestrator compatibility and future alert audit storage.
    _ = db

    return send_allocation_alerts_mock(
        report=report,
        sender=sender,
        mock_only=mock_only,
    )