from copy import deepcopy

import pytest

from app.bybit.asset_flows import (
    BybitAssetFlowError,
    list_master_withdrawals_page,
    list_master_withdrawals_paginated,
)


class FakeBybitClient:
    def __init__(
        self,
        responses: list[dict],
    ) -> None:
        self.responses = [
            deepcopy(response)
            for response in responses
        ]
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
                deepcopy(params),
            )
        )

        if not self.responses:
            raise AssertionError(
                "Unexpected extra Bybit GET"
            )

        return self.responses.pop(0)


def withdrawal_row(
    *,
    withdrawal_id: str,
    tx_hash: str,
    created_time_ms: int,
    request_id: str | None = None,
) -> dict:
    row = {
        "withdrawalId": withdrawal_id,
        "coin": "USDT",
        "chain": "BSC",
        "address": (
            "0x1111111111111111111111111111111111111111"
        ),
        "amount": "100",
        "withdrawFee": "1",
        "feeType": 0,
        "status": "SUCCESS",
        "txID": tx_hash,
        "createdTime": str(
            created_time_ms
        ),
    }

    if request_id is not None:
        row["requestId"] = request_id

    return row


def withdrawal_page(
    rows: list[dict],
    *,
    next_cursor: str = "",
) -> dict:
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


def test_paginated_withdrawals_pass_cursor_to_next_page(
) -> None:
    client = FakeBybitClient(
        [
            withdrawal_page(
                [
                    withdrawal_row(
                        withdrawal_id="wd-1",
                        request_id="req-1",
                        tx_hash="0xaaa",
                        created_time_ms=1000,
                    )
                ],
                next_cursor="cursor-2",
            ),
            withdrawal_page(
                [
                    withdrawal_row(
                        withdrawal_id="wd-2",
                        request_id="req-2",
                        tx_hash="0xbbb",
                        created_time_ms=2000,
                    )
                ],
            ),
        ]
    )

    result = (
        list_master_withdrawals_paginated(
            client,
            coin="USDT",
            start_time_ms=100,
            end_time_ms=3000,
            limit=50,
            max_pages=5,
        )
    )

    assert result.exhausted is True
    assert result.stop_reason == (
        "end_of_pages"
    )

    assert len(result.records) == 2
    assert len(result.pages) == 2

    assert client.get_calls == [
        (
            "/v5/asset/withdraw/query-record",
            {
                "limit": 50,
                "coin": "USDT",
                "startTime": 100,
                "endTime": 3000,
            },
        ),
        (
            "/v5/asset/withdraw/query-record",
            {
                "limit": 50,
                "coin": "USDT",
                "startTime": 100,
                "endTime": 3000,
                "cursor": "cursor-2",
            },
        ),
    ]

    assert result.pages[0].page_number == 1
    assert result.pages[0].request_cursor is None
    assert result.pages[0].next_cursor == (
        "cursor-2"
    )

    assert result.pages[1].page_number == 2
    assert result.pages[1].request_cursor == (
        "cursor-2"
    )
    assert result.pages[1].next_cursor is None

    assert len(
        result.pages[0].page_fingerprint
    ) == 64

    assert len(
        result.pages[1].page_fingerprint
    ) == 64


def test_paginated_withdrawals_support_multiple_pages(
) -> None:
    client = FakeBybitClient(
        [
            withdrawal_page(
                [],
                next_cursor="cursor-2",
            ),
            withdrawal_page(
                [],
                next_cursor="cursor-3",
            ),
            withdrawal_page(
                [
                    withdrawal_row(
                        withdrawal_id="wd-3",
                        request_id=None,
                        tx_hash="0xccc",
                        created_time_ms=3000,
                    )
                ],
            ),
        ]
    )

    result = (
        list_master_withdrawals_paginated(
            client,
            coin="USDT",
            max_pages=5,
        )
    )

    assert result.exhausted is True
    assert len(result.pages) == 3
    assert len(result.records) == 1

    record = result.records[0]

    assert record.withdrawal_id == "wd-3"
    assert record.request_id is None
    assert record.tx_hash == "0xccc"
    assert record.fee_usdt is not None
    assert str(record.fee_usdt) == "1"
    assert record.created_time_ms == 3000


def test_paginated_withdrawals_reject_repeated_cursor(
) -> None:
    client = FakeBybitClient(
        [
            withdrawal_page(
                [],
                next_cursor="cursor-2",
            ),
            withdrawal_page(
                [],
                next_cursor="cursor-2",
            ),
        ]
    )

    with pytest.raises(
        BybitAssetFlowError,
        match=(
            "repeated nextPageCursor"
        ),
    ):
        list_master_withdrawals_paginated(
            client,
            coin="USDT",
            max_pages=5,
        )

    assert len(client.get_calls) == 2


def test_paginated_withdrawals_reject_cursor_cycle(
) -> None:
    client = FakeBybitClient(
        [
            withdrawal_page(
                [],
                next_cursor="cursor-2",
            ),
            withdrawal_page(
                [],
                next_cursor="cursor-3",
            ),
            withdrawal_page(
                [],
                next_cursor="cursor-2",
            ),
        ]
    )

    with pytest.raises(
        BybitAssetFlowError,
        match=(
            "repeated nextPageCursor"
        ),
    ):
        list_master_withdrawals_paginated(
            client,
            coin="USDT",
            max_pages=5,
        )

    assert len(client.get_calls) == 3


def test_paginated_withdrawals_stop_at_max_pages(
) -> None:
    client = FakeBybitClient(
        [
            withdrawal_page(
                [
                    withdrawal_row(
                        withdrawal_id="wd-1",
                        request_id="req-1",
                        tx_hash="0xaaa",
                        created_time_ms=1000,
                    )
                ],
                next_cursor="cursor-2",
            ),
            withdrawal_page(
                [
                    withdrawal_row(
                        withdrawal_id="wd-2",
                        request_id="req-2",
                        tx_hash="0xbbb",
                        created_time_ms=2000,
                    )
                ],
                next_cursor="cursor-3",
            ),
        ]
    )

    result = (
        list_master_withdrawals_paginated(
            client,
            coin="USDT",
            max_pages=2,
        )
    )

    assert result.exhausted is False
    assert result.stop_reason == (
        "max_pages_reached"
    )

    assert len(result.pages) == 2
    assert len(result.records) == 2
    assert len(client.get_calls) == 2


def test_withdrawal_page_supports_withdrawal_id_filter(
) -> None:
    client = FakeBybitClient(
        [
            withdrawal_page(
                [
                    withdrawal_row(
                        withdrawal_id="wd-1",
                        request_id=None,
                        tx_hash="0xaaa",
                        created_time_ms=1000,
                    )
                ]
            )
        ]
    )

    result = list_master_withdrawals_page(
        client,
        coin="USDT",
        withdrawal_id="wd-1",
        limit=10,
    )

    assert len(result.records) == 1

    assert client.get_calls == [
        (
            "/v5/asset/withdraw/query-record",
            {
                "limit": 10,
                "coin": "USDT",
                "withdrawID": "wd-1",
            },
        )
    ]


def test_withdrawal_page_supports_tx_hash_filter(
) -> None:
    client = FakeBybitClient(
        [
            withdrawal_page(
                [
                    withdrawal_row(
                        withdrawal_id="wd-1",
                        request_id=None,
                        tx_hash="0xaaa",
                        created_time_ms=1000,
                    )
                ]
            )
        ]
    )

    result = list_master_withdrawals_page(
        client,
        coin="USDT",
        tx_hash="0xaaa",
        limit=10,
    )

    assert len(result.records) == 1

    assert client.get_calls == [
        (
            "/v5/asset/withdraw/query-record",
            {
                "limit": 10,
                "coin": "USDT",
                "txID": "0xaaa",
            },
        )
    ]


@pytest.mark.parametrize(
    "limit",
    [
        0,
        51,
    ],
)
def test_withdrawal_page_rejects_invalid_limit(
    limit: int,
) -> None:
    client = FakeBybitClient([])

    with pytest.raises(
        BybitAssetFlowError,
        match="between 1 and 50",
    ):
        list_master_withdrawals_page(
            client,
            limit=limit,
        )

    assert client.get_calls == []


def test_paginated_withdrawals_reject_invalid_max_pages(
) -> None:
    client = FakeBybitClient([])

    with pytest.raises(
        BybitAssetFlowError,
        match="max_pages must be positive",
    ):
        list_master_withdrawals_paginated(
            client,
            max_pages=0,
        )

    assert client.get_calls == []