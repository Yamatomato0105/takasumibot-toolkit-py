import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from zoneinfo import ZoneInfo

import pytest

from takasumibot_toolkit import (
    BasicConfig,
    RetryConfig,
    TakasumiBotToolkitConfigError,
    TakasumiBotToolkitError,
    TakasumiBotToolkitHttpError,
    TakasumiBotToolkitValidationError,
    format_number,
    format_timestamp,
    is_retryable,
    omit_fields,
    paginate,
    pick_fields,
    resolve_base_url,
    to_markdown_table,
    truncate,
)
from takasumibot_toolkit.models import GiftResponse, StockEntry


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout_ms": 0},
        {"timeout_ms": True},
        {"timeout_ms": "1000"},
        {"retry": {"unknown": 1}},
        {"retry": {"max_retries": -1}},
        {"retry": {"initial_delay_ms": 10, "max_delay_ms": 5}},
        {"retry": {"backoff_factor": 0.5}},
        {"retry": {"jitter": "yes"}},
        {"headers": {"Bad Key": "x"}},
        {"headers": {"X-Test": "a\nb"}},
        {"stock_cache_ttl_s": 60},  # removed: stock_cache={"ttl_ms": ...} only
        {"stock_cache": True},
        {"stock_cache": 60_000},
        {"stock_cache": {"ttl_ms": 0}},
        {"stock_cache": {"ttl_ms": 1.5}},
        {"stock_cache": {"ttl": 1}},
        {"follow_redirects": "yes"},
        {"logger": object()},
    ],
)
def test_config_errors(kwargs):
    with pytest.raises(TakasumiBotToolkitConfigError):
        BasicConfig(**kwargs)


def test_config_retry_dict_normalised():
    cfg = BasicConfig(retry={"max_retries": 1})
    assert isinstance(cfg.retry, RetryConfig) and cfg.retry.max_retries == 1
    assert BasicConfig().effective_retry.max_retries == 3
    assert RetryConfig().initial_delay_ms == 300 and RetryConfig().max_delay_ms == 5000


def test_base_url_resolution():
    assert resolve_base_url({}) == "https://api.takasumibot.com"
    assert resolve_base_url({"TAKASUMIBOT_BASE_URL": ""}) == "https://api.takasumibot.com"
    assert resolve_base_url({"TAKASUMIBOT_BASE_URL": "http://localhost:9/"}) == "http://localhost:9"
    for bad in [
        "ftp://x",
        "https://x/path",
        "https://x?q=1",
        "https://x#f",
        "https://",
        "https://[broken",
        "https://example.com:abc",
        "https://example.com:65536",
        "https://user:secret@example.com",
    ]:
        with pytest.raises(TakasumiBotToolkitConfigError):
            resolve_base_url({"TAKASUMIBOT_BASE_URL": bad})

    with pytest.raises(TakasumiBotToolkitConfigError) as exc_info:
        resolve_base_url({"TAKASUMIBOT_BASE_URL": "https://user:secret@example.com"})
    assert "secret" not in str(exc_info.value)


def test_model_alias_roundtrip_and_extra():
    payload = {
        "type": "gift",
        "id": "A1",
        "userId": "42",
        "status": "unused",
        "receiverId": None,
        "amount": 10,
        "boughtAt": None,
        "createdAt": "2026-01-01T00:00:00Z",
        "futureField": 7,
    }
    gift = GiftResponse.model_validate(payload)
    assert gift.user_id == "42" and gift.model_extra == {"futureField": 7}
    dumped = gift.model_dump(by_alias=True)
    assert dumped["userId"] == "42" and dumped["createdAt"].tzinfo is not None
    from pydantic import ValidationError as PydanticValidationError

    with pytest.raises(PydanticValidationError):
        gift.amount = 5  # frozen


def test_errors_hierarchy_and_dict():
    for cls_name in [
        "TakasumiBotToolkitConfigError",
        "TakasumiBotToolkitValidationError",
        "TakasumiBotToolkitHttpError",
        "TakasumiBotToolkitNetworkError",
        "TakasumiBotToolkitTimeoutError",
        "TakasumiBotToolkitRetryLimitError",
        "TakasumiBotToolkitResponseParseError",
    ]:
        import takasumibot_toolkit

        assert issubclass(getattr(takasumibot_toolkit, cls_name), TakasumiBotToolkitError)
    err = TakasumiBotToolkitHttpError(
        "GET u failed with HTTP 500", status=500, method="GET", url="u"
    )
    assert is_retryable(err) is True
    json.dumps(err.to_dict())
    with pytest.raises(AttributeError):
        err.status = 1


def test_truncate():
    assert truncate("abcdef", 5) == "ab..."
    assert truncate("abcdef", 6) == "abcdef"
    assert truncate("abcdef", 2) == "ab"
    assert truncate("", 5) == ""
    assert truncate("abc", 0) == ""
    with pytest.raises(TakasumiBotToolkitValidationError):
        truncate("abc", -1)
    with pytest.raises(TakasumiBotToolkitValidationError):
        truncate("abc", True)


def test_pick_omit():
    entry = StockEntry(
        name="n", id="X", description="d", dividendAmount=1, dividendRate=0.5, prices=[1]
    )
    assert pick_fields(entry, ("id", "nope")) == {"id": "X"}
    omitted = omit_fields(entry, ("prices",))
    assert "prices" not in omitted and omitted["id"] == "X"
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        pick_fields([{"a": 1, "b": 2}], ("a",))  # NPM rejects arrays too
    assert ei.value.field == "obj"


def test_markdown_table():
    assert to_markdown_table([]) == ""
    table = to_markdown_table([{"id": "a|b", "note": "x\ny", "ok": True, "v": None}])
    assert "a\\|b" in table and "x<br>y" in table and "true" in table
    assert table.splitlines()[1] == "| --- | --- | --- | --- |"


def test_helpers_accept_slotted_dataclasses_and_namedtuples():
    @dataclass(slots=True)
    class SlottedRow:
        id: str
        amount: int

    class NamedRow(NamedTuple):
        id: str
        amount: int

    slotted = SlottedRow("slot", 10)
    assert pick_fields(slotted, ("id",)) == {"id": "slot"}
    assert omit_fields(slotted, ("amount",)) == {"id": "slot"}
    assert pick_fields(NamedRow("named", 20), ("amount",)) == {"amount": 20}
    assert omit_fields(NamedRow("named", 20), ("amount",)) == {"id": "named"}
    assert to_markdown_table([slotted]) == "| id | amount |\n| --- | --- |\n| slot | 10 |"
    assert to_markdown_table([NamedRow("named", 20)]) == (
        "| id | amount |\n| --- | --- |\n| named | 20 |"
    )


def test_format_number():
    assert format_number(1_000_000) == "1,000,000"
    assert format_number(1234.5678) == "1,234.568"
    assert (
        format_number(1234.5678, minimum_fraction_digits=2, maximum_fraction_digits=2) == "1,234.57"
    )
    assert format_number(1500, notation="compact") == "1500"
    assert format_number(15_000, notation="compact") == "1.5万"
    assert format_number(1500, notation="compact", locale="en-US") == "1.5K"
    assert format_number(-1500, notation="compact", locale="en-US") == "-1.5K"
    assert format_number(0.5) == "0.5"
    assert format_number(2.5, maximum_fraction_digits=0) == "3"
    for bad in [float("nan"), float("inf"), None, True]:
        with pytest.raises(TakasumiBotToolkitValidationError):
            format_number(bad)


def test_format_timestamp():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert format_timestamp(now) == "2026-01-01T00:00:00.000Z"
    assert format_timestamp(now, format="locale", time_zone="UTC") == "2026/1/1 0:00:00"
    ago = now - timedelta(seconds=30)
    assert format_timestamp(ago, format="relative", reference=now) == "30 秒前"
    ago = now - timedelta(days=2)
    assert format_timestamp(ago, format="relative", reference=now) == "2 日前"
    later = now + timedelta(minutes=3)
    assert format_timestamp(later, format="relative", reference=now) == "3 分後"
    with pytest.raises(TakasumiBotToolkitValidationError):
        format_timestamp(datetime(2026, 1, 1))
    with pytest.raises(TakasumiBotToolkitValidationError):
        format_timestamp(now, format="locale", time_zone="Not/A_Timezone")


@pytest.mark.parametrize(
    ("reference", "value", "expected"),
    [
        (
            datetime(2024, 3, 10, 1, 30, tzinfo=ZoneInfo("America/New_York")),
            datetime(2024, 3, 10, 3, 30, tzinfo=ZoneInfo("America/New_York")),
            "in 1 hour",
        ),
        (
            datetime(2024, 11, 3, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=0),
            datetime(2024, 11, 3, 3, 30, tzinfo=ZoneInfo("America/New_York")),
            "in 3 hours",
        ),
    ],
)
def test_format_timestamp_relative_across_dst(reference, value, expected):
    assert (
        format_timestamp(value, format="relative", locale="en-US", reference=reference) == expected
    )


def test_paginate():
    page = paginate([1, 2, 3, 4, 5], page=2, page_size=2)
    assert page.items == [3, 4] and page.has_next is True and page.total_pages == 3
    assert paginate([1], page=9, page_size=2).items == []
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        paginate([], page=0)
    assert ei.value.code == "INVALID_PAGE"
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        paginate([], page_size=0)
    assert ei.value.code == "INVALID_PAGE_SIZE"
