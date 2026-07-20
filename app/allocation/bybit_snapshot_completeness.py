from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


@dataclass
class SnapshotEndpointMatrix:
    required: list[str] = field(
        default_factory=list
    )
    successful: list[str] = field(
        default_factory=list
    )
    failed: list[str] = field(
        default_factory=list
    )
    failures: dict[str, str] = field(
        default_factory=dict
    )
    suppressed_errors: list[
        dict[str, Any]
    ] = field(default_factory=list)

    def require(
        self,
        endpoint_key: str,
    ) -> None:
        key = str(endpoint_key).strip()

        if not key:
            raise ValueError(
                "endpoint_key must not be empty"
            )

        if key not in self.required:
            self.required.append(key)

    def mark_success(
        self,
        endpoint_key: str,
    ) -> None:
        key = str(endpoint_key).strip()
        self.require(key)

        if key not in self.successful:
            self.successful.append(key)

        if key in self.failed:
            self.failed.remove(key)

        self.failures.pop(key, None)

    def mark_failure(
        self,
        endpoint_key: str,
        *,
        error: Exception | str,
        suppressed: bool,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        key = str(endpoint_key).strip()
        self.require(key)

        if key not in self.failed:
            self.failed.append(key)

        if key in self.successful:
            self.successful.remove(key)

        error_text = str(error)
        self.failures[key] = error_text

        if suppressed:
            row: dict[str, Any] = {
                "endpoint": key,
                "error": error_text,
                "suppressed": True,
            }

            if metadata:
                row["metadata"] = dict(metadata)

            self.suppressed_errors.append(row)

    @property
    def required_endpoints(
        self,
    ) -> tuple[str, ...]:
        return _unique(self.required)

    @property
    def successful_endpoints(
        self,
    ) -> tuple[str, ...]:
        return _unique(self.successful)

    @property
    def failed_endpoints(
        self,
    ) -> tuple[str, ...]:
        return _unique(self.failed)

    @property
    def snapshot_complete(self) -> bool:
        required = set(
            self.required_endpoints
        )
        successful = set(
            self.successful_endpoints
        )

        return (
            not self.failed_endpoints
            and required.issubset(successful)
        )

    @property
    def completeness_reasons(
        self,
    ) -> tuple[str, ...]:
        reasons: list[str] = []

        for endpoint in self.failed_endpoints:
            error = self.failures.get(
                endpoint,
                "unknown_error",
            )
            reasons.append(
                f"required_endpoint_failed:"
                f"{endpoint}:{error}"
            )

        successful = set(
            self.successful_endpoints
        )

        for endpoint in self.required_endpoints:
            if (
                endpoint not in successful
                and endpoint
                not in self.failed_endpoints
            ):
                reasons.append(
                    "required_endpoint_not_confirmed:"
                    f"{endpoint}"
                )

        return _unique(reasons)

    def to_dict(
        self,
        *,
        captured_at: datetime | None = None,
    ) -> dict[str, Any]:
        timestamp = captured_at or utcnow()

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
            "failures": dict(self.failures),
            "suppressed_errors": [
                dict(row)
                for row in self.suppressed_errors
            ],
            "captured_at": (
                timestamp.isoformat()
            ),
        }