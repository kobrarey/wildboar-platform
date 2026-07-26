from decimal import Decimal

from app.bybit.asset_flows import (
    BybitWithdrawalPageEvidence,
    BybitWithdrawalPaginationResult,
    BybitWithdrawalResult,
)
from app.settlement.negative_bybit_flow_live_service import (
    _deduplicate_withdrawal_records,
    _withdrawal_pagination_evidence,
    _withdrawal_record_fingerprint,
    _withdrawal_record_intent_match,
    _withdrawal_recovery_lookup,
)


ADDRESS = (
    "0x1111111111111111111111111111111111111111"
)


class FakeBybitClient:
    def __init__(
        self,
        responses: list[dict],
    ) -> None:
        self.responses = list(
            responses
        )
        self.get_calls: list[
            tuple[str, dict]
        ] = []

    def get(
        self,
        path: str,
        params: dict,
    ) -> dict:
        self.get_calls.append(
            (
                path,
                dict(params),
            )
        )

        if not self.responses:
            raise AssertionError(
                "Unexpected extra Bybit GET"
            )

        return self.responses.pop(0)


def response_page(
    records: list[
        BybitWithdrawalResult
    ],
    *,
    next_cursor: str = "",
) -> dict:
    rows = []

    for record in records:
        row = {
            "withdrawalId": (
                record.withdrawal_id
            ),
            "coin": record.coin,
            "chain": record.chain,
            "address": record.address,
            "amount": format(
                record.amount_usdt,
                "f",
            ),
            "withdrawFee": (
                format(
                    record.fee_usdt,
                    "f",
                )
                if record.fee_usdt
                is not None
                else None
            ),
            "feeType": (
                record.fee_type
            ),
            "status": record.status,
            "txID": record.tx_hash,
            "createdTime": (
                str(
                    record.created_time_ms
                )
                if record.created_time_ms
                is not None
                else None
            ),
        }

        if record.request_id is not None:
            row["requestId"] = (
                record.request_id
            )

        rows.append(row)

    return {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": rows,
            "nextPageCursor": (
                next_cursor
            ),
        },
    }


def make_record(
    *,
    request_id: str | None = "req123",
    withdrawal_id: str = "wd-1",
    tx_hash: str | None = "0xabc",
    fee_usdt: Decimal | None = Decimal("1"),
    fee_type: int = 0,
    created_time_ms: int | None = 2000,
) -> BybitWithdrawalResult:
    return BybitWithdrawalResult(
        request_id=request_id,
        withdrawal_id=withdrawal_id,
        coin="USDT",
        chain="BSC",
        address=ADDRESS,
        amount_usdt=Decimal("100"),
        fee_type=fee_type,
        status="SUCCESS",
        tx_hash=tx_hash,
        raw={},
        fee_usdt=fee_usdt,
        created_time_ms=created_time_ms,
    )


def snapshot() -> dict:
    return {
        "request_id": "req123",
        "coin": "USDT",
        "chain": "BSC",
        "address": ADDRESS,
        "amount_usdt": Decimal("100"),
        "fee_usdt": Decimal("1"),
        "fee_type": 0,
    }


def test_withdrawal_record_without_request_id_matches(
) -> None:
    matched, error = (
        _withdrawal_record_intent_match(
            record=make_record(
                request_id=None
            ),
            snapshot=snapshot(),
            lookup_start_ms=1000,
            lookup_end_ms=3000,
        )
    )

    assert matched is True
    assert error is None


def test_withdrawal_record_returned_request_id_must_match(
) -> None:
    matched, error = (
        _withdrawal_record_intent_match(
            record=make_record(
                request_id="other"
            ),
            snapshot=snapshot(),
            lookup_start_ms=1000,
            lookup_end_ms=3000,
        )
    )

    assert matched is False
    assert error == (
        "Withdrawal record requestId mismatch"
    )


def test_withdrawal_record_fixed_fee_must_match(
) -> None:
    matched, error = (
        _withdrawal_record_intent_match(
            record=make_record(
                fee_usdt=Decimal("2")
            ),
            snapshot=snapshot(),
            lookup_start_ms=1000,
            lookup_end_ms=3000,
        )
    )

    assert matched is False
    assert error == (
        "Withdrawal record fixed fee mismatch"
    )


def test_withdrawal_record_created_time_must_be_in_window(
) -> None:
    matched, error = (
        _withdrawal_record_intent_match(
            record=make_record(
                created_time_ms=4000
            ),
            snapshot=snapshot(),
            lookup_start_ms=1000,
            lookup_end_ms=3000,
        )
    )

    assert matched is False
    assert error == (
        "Withdrawal record createdTime is "
        "outside lookup window"
    )


def test_withdrawal_record_fingerprint_is_deterministic(
) -> None:
    left = make_record()
    right = make_record()

    assert (
        _withdrawal_record_fingerprint(
            left
        )
        == _withdrawal_record_fingerprint(
            right
        )
    )

    assert len(
        _withdrawal_record_fingerprint(
            left
        )
    ) == 64


def test_duplicate_records_are_deduplicated_by_fingerprint(
) -> None:
    records = (
        make_record(),
        make_record(),
        make_record(
            withdrawal_id="wd-2",
            tx_hash="0xdef",
        ),
    )

    unique = (
        _deduplicate_withdrawal_records(
            records
        )
    )

    assert len(unique) == 2


def test_pagination_evidence_preserves_all_page_fingerprints(
) -> None:
    result = (
        BybitWithdrawalPaginationResult(
            records=(
                make_record(),
            ),
            pages=(
                BybitWithdrawalPageEvidence(
                    page_number=1,
                    request_cursor=None,
                    next_cursor="cursor-2",
                    record_count=0,
                    page_fingerprint="a" * 64,
                ),
                BybitWithdrawalPageEvidence(
                    page_number=2,
                    request_cursor="cursor-2",
                    next_cursor=None,
                    record_count=1,
                    page_fingerprint="b" * 64,
                ),
            ),
            exhausted=True,
            stop_reason="end_of_pages",
        )
    )

    evidence = (
        _withdrawal_pagination_evidence(
            result
        )
    )

    assert evidence["exhausted"] is True
    assert evidence[
        "stop_reason"
    ] == "end_of_pages"

    assert evidence[
        "page_count"
    ] == 2

    assert evidence[
        "returned_record_count"
    ] == 1

    assert [
        page["page_fingerprint"]
        for page in evidence["pages"]
    ] == [
        "a" * 64,
        "b" * 64,
    ]


def test_recovery_prefers_saved_withdrawal_id(
) -> None:
    record = make_record()

    client = FakeBybitClient(
        [
            response_page(
                [record]
            )
        ]
    )

    result = _withdrawal_recovery_lookup(
        bybit_client=client,
        snapshot=snapshot(),
        saved_withdrawal_id="wd-1",
        saved_tx_hash=None,
        lookup_start_ms=1000,
        lookup_end_ms=3000,
        max_pages=5,
    )

    assert result["state"] == (
        "unique_match"
    )
    assert result[
        "selected_source"
    ] == "withdrawal_id_query"
    assert result[
        "unique_match"
    ] is True
    assert result[
        "exact_fingerprint_match"
    ] is True
    assert len(client.get_calls) == 1
    assert client.get_calls[0][1][
        "withdrawID"
    ] == "wd-1"


def test_recovery_falls_back_to_saved_tx_hash(
) -> None:
    record = make_record()

    client = FakeBybitClient(
        [
            response_page([]),
            response_page([record]),
        ]
    )

    result = _withdrawal_recovery_lookup(
        bybit_client=client,
        snapshot=snapshot(),
        saved_withdrawal_id="unknown-id",
        saved_tx_hash="0xabc",
        lookup_start_ms=1000,
        lookup_end_ms=3000,
        max_pages=5,
    )

    assert result["state"] == (
        "unique_match"
    )
    assert result[
        "selected_source"
    ] == "tx_hash_query"
    assert len(client.get_calls) == 2
    assert client.get_calls[1][1][
        "txID"
    ] == "0xabc"


def test_recovery_falls_back_to_bounded_listing(
) -> None:
    record = make_record(
        request_id=None
    )

    client = FakeBybitClient(
        [
            response_page([record])
        ]
    )

    result = _withdrawal_recovery_lookup(
        bybit_client=client,
        snapshot=snapshot(),
        saved_withdrawal_id=None,
        saved_tx_hash=None,
        lookup_start_ms=1000,
        lookup_end_ms=3000,
        max_pages=5,
    )

    assert result["state"] == (
        "unique_match"
    )
    assert result[
        "selected_source"
    ] == "bounded_record_lookup"
    assert result[
        "unique_match"
    ] is True
    assert result[
        "exact_fingerprint_match"
    ] is True


def test_recovery_reads_second_bounded_page(
) -> None:
    record = make_record(
        request_id=None
    )

    client = FakeBybitClient(
        [
            response_page(
                [],
                next_cursor="cursor-2",
            ),
            response_page([record]),
        ]
    )

    result = _withdrawal_recovery_lookup(
        bybit_client=client,
        snapshot=snapshot(),
        saved_withdrawal_id=None,
        saved_tx_hash=None,
        lookup_start_ms=1000,
        lookup_end_ms=3000,
        max_pages=5,
    )

    assert result["state"] == (
        "unique_match"
    )
    assert result[
        "selected_source"
    ] == "bounded_record_lookup"
    assert result[
        "bybit_get_count"
    ] == 2

    pages = result["queries"][
        "bounded_record_lookup"
    ]["pages"]

    assert len(pages) == 2
    assert pages[0][
        "next_cursor"
    ] == "cursor-2"


def test_recovery_reports_ambiguous_matches(
) -> None:
    client = FakeBybitClient(
        [
            response_page(
                [
                    make_record(),
                    make_record(
                        request_id=None,
                        withdrawal_id="wd-2",
                        tx_hash="0xdef",
                    ),
                ]
            )
        ]
    )

    result = _withdrawal_recovery_lookup(
        bybit_client=client,
        snapshot=snapshot(),
        saved_withdrawal_id=None,
        saved_tx_hash=None,
        lookup_start_ms=1000,
        lookup_end_ms=3000,
        max_pages=5,
    )

    assert result["state"] == (
        "ambiguous"
    )
    assert result["ambiguous"] is True
    assert result[
        "unique_match"
    ] is False
    assert len(
        result[
            "matching_record_fingerprints"
        ]
    ) == 2


def test_recovery_reports_request_id_mismatch(
) -> None:
    client = FakeBybitClient(
        [
            response_page(
                [
                    make_record(
                        request_id="other"
                    )
                ]
            )
        ]
    )

    result = _withdrawal_recovery_lookup(
        bybit_client=client,
        snapshot=snapshot(),
        saved_withdrawal_id=None,
        saved_tx_hash=None,
        lookup_start_ms=1000,
        lookup_end_ms=3000,
        max_pages=5,
    )

    assert result["state"] == (
        "record_mismatch"
    )
    assert (
        "requestId mismatch"
        in result["error"]
    )


def test_recovery_reports_incomplete_max_pages(
) -> None:
    client = FakeBybitClient(
        [
            response_page(
                [],
                next_cursor="cursor-2",
            )
        ]
    )

    result = _withdrawal_recovery_lookup(
        bybit_client=client,
        snapshot=snapshot(),
        saved_withdrawal_id=None,
        saved_tx_hash=None,
        lookup_start_ms=1000,
        lookup_end_ms=3000,
        max_pages=1,
    )

    assert result["state"] == (
        "lookup_incomplete"
    )
    assert result[
        "unique_match"
    ] is False
    assert result[
        "queries"
    ][
        "bounded_record_lookup"
    ]["stop_reason"] == (
        "max_pages_reached"
    )


def test_recovery_returns_record_not_found(
) -> None:
    client = FakeBybitClient(
        [
            response_page([])
        ]
    )

    result = _withdrawal_recovery_lookup(
        bybit_client=client,
        snapshot=snapshot(),
        saved_withdrawal_id=None,
        saved_tx_hash=None,
        lookup_start_ms=1000,
        lookup_end_ms=3000,
        max_pages=5,
    )

    assert result["state"] == (
        "record_not_found"
    )
    assert result[
        "selected_record"
    ] is None
    assert result[
        "bybit_get_count"
    ] == 1
