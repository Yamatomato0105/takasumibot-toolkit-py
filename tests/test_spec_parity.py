"""The expected strings of the helper tests were produced with Node.js 22
(``Intl.NumberFormat`` / ``Intl.RelativeTimeFormat`` / ``toLocaleString``), i.e.
by the NPM package's own implementation.
"""

import asyncio
import pickle
from collections import namedtuple
from datetime import UTC, datetime, timedelta, timezone

import httpx
import pydantic
import pytest

import takasumibot_toolkit as tk
from conftest import STOCK_LIST, json_response, make_kit
from takasumibot_toolkit import (
    DEFAULT_LOCALE,
    DEFAULT_RETRY_CONFIG,
    DEFAULT_STOCK_CACHE_TTL_MS,
    GIFT_STATUSES,
    GIFT_TYPES,
    STOCK_IDS,
    BasicConfig,
    GiftResponse,
    Helpers,
    RetryConfig,
    StockCacheConfig,
    StockEntry,
    TakasumiBotToolkitConfigError,
    TakasumiBotToolkitError,
    TakasumiBotToolkitHttpError,
    TakasumiBotToolkitResponseParseError,
    TakasumiBotToolkitValidationError,
    compute_delay_ms,
    create_kit_client,
    format_number,
    format_timestamp,
    is_known_value,
    omit_fields,
    paginate,
    pick_fields,
    to_markdown_table,
    truncate,
)
from takasumibot_toolkit._args import js_trim
from takasumibot_toolkit.helpers import HELPERS

GIFT = {
    "type": "gift",
    "id": "A1B2C3D4E5",
    "userId": "1",
    "status": "unused",
    "receiverId": None,
    "amount": 1,
    "boughtAt": None,
    "createdAt": "2026-01-01T00:00:00Z",
}
PROFILE = {"id": "123", "money": 1, "assets": 2, "stocks": 3}


def recording_kit(payload, **kwargs):
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        return json_response(payload)

    return make_kit(handler, **kwargs), seen


# ============================================================ 1-1 trimming
@pytest.mark.parametrize(
    ("raw", "path"),
    [
        ("  123  ", "/v3/history/123"),
        ("\t123\n", "/v3/history/123"),
        ("\ufeff123\u3000", "/v3/history/123"),
        ("\u00a0\u2028123\u202f", "/v3/history/123"),
        ("1 2", "/v3/history/1%202"),
    ],
)
async def test_path_parameters_are_trimmed_like_js(raw, path):
    kit, seen = recording_kit([])
    await kit.get_history_by_id(raw)
    assert seen[-1].endswith(path)


async def test_all_string_path_endpoints_trim():
    kit, seen = recording_kit({"id": "42", "name": "foo"})
    with pytest.raises(TakasumiBotToolkitResponseParseError):
        await kit.get_discord_user_by_name("  foo  ")
    assert seen[-1].endswith("/v3/discord/usersearch/foo")
    with pytest.raises(TakasumiBotToolkitResponseParseError):
        await kit.get_profile_by_id(" 123 ")
    assert seen[-1].endswith("/v3/profile/123")
    with pytest.raises(TakasumiBotToolkitResponseParseError):
        await kit.get_company_by_id(" C1 ")
    assert seen[-1].endswith("/v3/company/C1")
    kit, seen = recording_kit([])
    await kit.get_company_history_by_id(" C1 ")
    assert seen[-1].endswith("/v3/companyHistory/C1")


def test_js_trim_uses_the_ecmascript_whitespace_set():
    # str.strip() would remove U+001C and U+0085 but keep U+FEFF; JS does the opposite.
    assert js_trim("\x1cA\x85") == "\x1cA\x85"
    assert js_trim("\ufeffA\ufeff") == "A"
    assert js_trim("\u1680\u2000\u200aA\u205f") == "A"


@pytest.mark.parametrize("blank", ["", " ", "\t\n", "\u3000", "\ufeff"])
async def test_blank_path_parameter_is_empty_argument(blank):
    kit, seen = recording_kit({})
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        await kit.get_discord_user_by_name(blank)
    assert ei.value.code == "EMPTY_ARGUMENT" and ei.value.field == "name"
    assert "non-empty string" in str(ei.value)
    assert seen == []


@pytest.mark.parametrize("bad", [" . ", " .. "])
async def test_trimmed_relative_segments_are_still_rejected(bad):
    kit, seen = recording_kit({})
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        await kit.get_profile_by_id(bad)
    assert ei.value.code == "UNSAFE_PATH_PARAMETER" and seen == []


async def test_stock_ids_are_trimmed():
    kit, _ = recording_kit(STOCK_LIST)
    assert await kit.get_stock_price_by_id("  AAA ") == 120
    assert kit.get_stock().id("\tAAA\n").stock_id == "AAA"
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        kit.get_stock().id("   ")
    assert ei.value.code == "INVALID_STOCK_ID"


# ============================================================ 1-2 gift id
async def test_valid_gift_id_is_sent():
    kit, seen = recording_kit(GIFT)
    gift = await kit.get_gift_info("A1B2C3D4E5")
    assert gift.id == "A1B2C3D4E5"
    assert seen[-1].endswith("/v3/gift/A1B2C3D4E5")


@pytest.mark.parametrize(
    "bad",
    [
        " A1B2C3D4E5",  # NPM validates the raw value: padding is not trimmed
        "A1B2C3D4E5 ",
        "A1B2C3D4E5\n",  # ``$`` would accept this; fullmatch does not
        "A1B2C3D4E",
        "A1B2C3D4E5F",
        "A1B2-3D4E5",
        "\uff21\uff22\uff23\uff24\uff25\uff26\uff27\uff28\uff29\uff2a",  # full-width letters
        "\uff11\uff12\uff13\uff14\uff15\uff16\uff17\uff18\uff19\uff10",  # full-width digits
        "",
        None,
        1234567890,
    ],
)
async def test_invalid_gift_id_is_rejected_before_any_request(bad):
    kit, seen = recording_kit(GIFT)
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        await kit.get_gift_info(bad)
    assert ei.value.code == "INVALID_GIFT_ID" and ei.value.field == "id"
    assert seen == []


# ============================================================ 1-3 stock cache
async def test_get_stock_list_without_cache_always_requests():
    kit, seen = recording_kit(STOCK_LIST)
    await kit.get_stock_list()
    await kit.get_stock_list(force_refresh=True)
    await kit.get_stock_price_by_id("AAA")
    assert len(seen) == 3


async def test_get_stock_list_shares_the_single_flight_with_helpers():
    calls = {"n": 0}

    async def slow(request):
        calls["n"] += 1
        await asyncio.sleep(0.01)
        return json_response(STOCK_LIST)

    transport = httpx.MockTransport(slow)
    client = httpx.AsyncClient(transport=transport)
    kit = create_kit_client(client=client, stock_cache={}, retry={"max_retries": 0})
    listed, price, _ = await asyncio.gather(
        kit.get_stock_list(), kit.get_stock_price_by_id("AAA"), kit.get_stock_list()
    )
    assert [e.id for e in listed] == ["AAA", "BBB"] and price == 120
    assert calls["n"] == 1
    await client.aclose()


async def test_cached_stock_list_cannot_be_corrupted_and_expires():
    kit, seen = recording_kit(STOCK_LIST, stock_cache={"ttl_ms": 50})
    first = await kit.get_stock_list()
    first.clear()
    assert len(await kit.get_stock_list()) == 2
    assert len(seen) == 1
    await asyncio.sleep(0.08)
    await kit.get_stock_list()
    assert len(seen) == 2


async def test_failed_stock_list_is_not_cached():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, content=b"busy")
        return json_response(STOCK_LIST)

    kit = make_kit(handler, stock_cache={})
    with pytest.raises(TakasumiBotToolkitHttpError):
        await kit.get_stock_list()
    assert len(await kit.get_stock_list()) == 2
    assert calls["n"] == 2


async def test_closed_client_rejects_get_stock_list():
    kit, _ = recording_kit(STOCK_LIST, stock_cache={})
    await kit.aclose()
    for call in (kit.get_stock_list(), kit.get_stock_list(force_refresh=True)):
        with pytest.raises(TakasumiBotToolkitConfigError) as ei:
            await call
        assert ei.value.code == "CLIENT_CLOSED"


async def test_force_refresh_waits_for_an_in_flight_cache_miss():
    # A refresh that starts while a cache miss is loading must not be
    # overwritten by the (older) result of that miss.
    first_started, release_first = asyncio.Event(), asyncio.Event()
    calls = {"n": 0}

    async def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            first_started.set()
            await release_first.wait()
            return json_response([dict(STOCK_LIST[0], id="OLD")])
        return json_response([dict(STOCK_LIST[0], id="NEW")])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    kit = create_kit_client(client=client, stock_cache={}, retry={"max_retries": 0})
    miss = asyncio.create_task(kit.get_stock_list())
    await first_started.wait()
    refresh = asyncio.create_task(kit.get_stock_list(force_refresh=True))
    await asyncio.sleep(0.01)
    release_first.set()
    assert [e.id for e in await miss] == ["OLD"]
    assert [e.id for e in await refresh] == ["NEW"]
    assert [e.id for e in await kit.get_stock_list()] == ["NEW"]  # still cached
    assert calls["n"] == 2
    await client.aclose()


# ============================================================ 1-4 error codes
async def test_stock_not_found_code_and_message():
    kit, _ = recording_kit(STOCK_LIST)
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        await kit.get_stock_info_by_id("NOPE")
    assert ei.value.code == "STOCK_NOT_FOUND" and ei.value.field == "id"
    assert "Unknown stock id 'NOPE'" in str(ei.value)


def test_error_code_literal_matches_npm_names():
    codes = set(tk.ErrorCode.__args__)
    assert {"EMPTY_ARGUMENT", "STOCK_NOT_FOUND", "INVALID_GIFT_ID"} <= codes
    assert not {"EMPTY_PATH_PARAMETER", "STOCK_ID_NOT_FOUND"} & codes


# ============================================================ 1-5 looseEnum
def test_unknown_enum_values_are_accepted():
    gift = GiftResponse.model_validate({**GIFT, "status": "expired", "type": "coupon"})
    assert gift.status == "expired" and gift.type == "coupon"
    entry = StockEntry.model_validate({**STOCK_LIST[0], "id": "NEWCO"})
    assert entry.id == "NEWCO"
    assert not is_known_value(entry.id, STOCK_IDS)
    assert is_known_value(GiftResponse.model_validate(GIFT).status, GIFT_STATUSES)


@pytest.mark.parametrize("bad", ["", 42, None])
def test_loose_enum_still_requires_a_non_empty_string(bad):
    with pytest.raises(pydantic.ValidationError, match="status"):
        GiftResponse.model_validate({**GIFT, "status": bad})


async def test_unknown_stock_id_does_not_break_the_list():
    stocks = [*STOCK_LIST, {**STOCK_LIST[0], "id": "BRAND_NEW"}]
    kit, _ = recording_kit(stocks)
    assert [e.id for e in await kit.get_stock_list()] == ["AAA", "BBB", "BRAND_NEW"]


# ============================================================ 2-1 retry
def test_retry_defaults_match_npm():
    assert RetryConfig() == DEFAULT_RETRY_CONFIG
    assert (
        DEFAULT_RETRY_CONFIG.max_retries,
        DEFAULT_RETRY_CONFIG.initial_delay_ms,
        DEFAULT_RETRY_CONFIG.max_delay_ms,
        DEFAULT_RETRY_CONFIG.backoff_factor,
        DEFAULT_RETRY_CONFIG.jitter,
    ) == (3, 300, 5000, 2.0, True)
    assert BasicConfig().effective_retry is DEFAULT_RETRY_CONFIG
    no_jitter = RetryConfig(jitter=False)
    assert [compute_delay_ms(no_jitter, attempt=a) for a in range(6)] == [
        300,
        600,
        1200,
        2400,
        4800,
        5000,
    ]
    # Retry-After is clipped to max_delay_ms * 3 = 15 s.
    assert compute_delay_ms(no_jitter, attempt=0, retry_after_s=120) == 15_000


# ============================================================ 2-2 stockCache
def test_stock_cache_configuration_forms():
    assert DEFAULT_STOCK_CACHE_TTL_MS == 60_000
    assert BasicConfig().stock_cache is None
    assert BasicConfig(stock_cache=False).stock_cache is None
    assert BasicConfig(stock_cache={}).stock_cache == StockCacheConfig(ttl_ms=60_000)
    assert BasicConfig(stock_cache={"ttl_ms": 1500}).stock_cache_ttl_ms == 1500
    assert BasicConfig(stock_cache=StockCacheConfig(ttl_ms=5)).stock_cache_ttl_ms == 5
    assert BasicConfig().stock_cache_ttl_ms is None


@pytest.mark.parametrize(
    ("value", "field", "code"),
    [
        (True, "stock_cache", "INVALID_CONFIG"),
        (60_000, "stock_cache", "INVALID_CONFIG"),
        ({"ttl_ms": 0}, "stock_cache.ttl_ms", "INVALID_CONFIG"),
        ({"ttl_ms": -1}, "stock_cache.ttl_ms", "INVALID_CONFIG"),
        ({"ttl_ms": "60000"}, "stock_cache.ttl_ms", "INVALID_CONFIG"),
        ({"ttlMs": 1}, "stock_cache.ttlMs", "UNKNOWN_CONFIG_KEY"),
    ],
)
def test_invalid_stock_cache_configuration(value, field, code):
    with pytest.raises(TakasumiBotToolkitConfigError) as ei:
        BasicConfig(stock_cache=value)
    assert (ei.value.field, ei.value.code) == (field, code)


def test_seconds_based_stock_cache_option_is_gone():
    with pytest.raises(TakasumiBotToolkitConfigError) as ei:
        BasicConfig(stock_cache_ttl_s=60)
    assert (ei.value.field, ei.value.code) == ("stock_cache_ttl_s", "UNKNOWN_CONFIG_KEY")
    with pytest.raises(TypeError):
        create_kit_client(stock_cache_ttl_s=60)
    assert not hasattr(BasicConfig(stock_cache={}), "stock_cache_ttl_s")


def test_create_kit_client_stock_cache_override():
    base = BasicConfig(stock_cache={"ttl_ms": 10})
    assert create_kit_client(base).config.stock_cache_ttl_ms == 10
    assert create_kit_client(base, stock_cache={}).config.stock_cache_ttl_ms == 60_000
    assert create_kit_client(base, stock_cache=False).config.stock_cache is None
    assert create_kit_client(base, timeout_ms=5).config.stock_cache_ttl_ms == 10


# ============================================================ 2-3 exports
def test_npm_constants_are_exported():
    assert GIFT_TYPES == ("gift",)
    assert GIFT_STATUSES == ("received", "unused")
    assert STOCK_IDS == (
        "JTTI",
        "TENOMU",
        "KAKAPO",
        "TOMOTA",
        "NITIMOTO",
        "DEEDLE",
        "RIPPLE",
        "NIMURA",
        "TAKASUMI",
        "KENTAI",
    )
    for name in (
        "STOCK_IDS",
        "GIFT_TYPES",
        "GIFT_STATUSES",
        "is_known_value",
        "DEFAULT_RETRY_CONFIG",
        "DEFAULT_LOCALE",
        "StockCacheConfig",
        "Helpers",
        "LooseEnum",
    ):
        assert name in tk.__all__ and hasattr(tk, name)


# ============================================================ 2-4 HttpError
async def test_http_error_keeps_npm_headers_full_body_and_raw_retry_after():
    body = b'{"message": "slow down", "pad": "' + b"x" * 2000 + b'"}'

    def handler(request):
        return httpx.Response(
            429,
            content=body,
            headers={
                "retry-after": "120",
                "content-type": "application/json",
                "x-takasumibot-request-id": "tb-1",
                "x-ratelimit-remaining": "0",
                "request-id": "rid",
                "x-requestid": "xr",
                "set-cookie": "secret=1",
                "server": "nginx",
            },
        )

    kit = make_kit(handler)
    with pytest.raises(TakasumiBotToolkitHttpError) as ei:
        await kit.get_tax_info()
    err = ei.value
    assert set(err.headers) == {
        "retry-after",
        "content-type",
        "x-takasumibot-request-id",
        "x-ratelimit-remaining",
        "request-id",
        "x-requestid",
    }  # exactly the NPM snapshot (content-length and set-cookie are dropped)
    assert err.request_id == "xr"  # x-request-id > x-requestid > request-id > x-takasumibot-...
    assert err.raw_body == body.decode() and len(err.raw_body) > 2000
    assert err.retry_after == "120"
    assert not hasattr(err, "raw_text") and not hasattr(err, "retry_after_s")
    restored = pickle.loads(pickle.dumps(err))
    assert restored.raw_body == err.raw_body and restored.retry_after == "120"


async def test_http_error_request_id_from_takasumibot_header_and_date_retry_after():
    date = "Wed, 21 Oct 2099 07:28:00 GMT"

    def handler(request):
        return httpx.Response(
            503, content=b"", headers={"x-takasumibot-request-id": "tb-2", "retry-after": date}
        )

    kit = make_kit(handler)
    with pytest.raises(TakasumiBotToolkitHttpError) as ei:
        await kit.get_tax_info()
    assert ei.value.request_id == "tb-2"
    assert ei.value.retry_after == date
    assert ei.value.raw_body == ""  # NPM: rawBody === ""


async def test_request_id_headers_follow_the_npm_list_only():
    def handler(request):
        return httpx.Response(500, content=b"{}", headers={"x-amz-request-id": "amz-1"})

    kit = make_kit(handler, retry={"max_retries": 0})
    with pytest.raises(TakasumiBotToolkitHttpError) as ei:
        await kit.get_tax_info()
    assert ei.value.request_id is None and "x-amz-request-id" not in ei.value.headers


async def test_parse_error_keeps_the_full_raw_body():
    payload = b"[" + b"1," * 600 + b"1]"
    kit = make_kit(lambda request: httpx.Response(200, content=payload))
    with pytest.raises(TakasumiBotToolkitResponseParseError) as ei:
        await kit.get_tax_info()
    assert ei.value.raw_body == payload.decode()
    assert not hasattr(ei.value, "raw_text")
    kit = make_kit(lambda request: httpx.Response(200, content=b"{not json"))
    with pytest.raises(TakasumiBotToolkitResponseParseError) as ei:
        await kit.get_tax_info()
    assert ei.value.raw_body == "{not json" and ei.value.code == "INVALID_JSON"
    kit = make_kit(lambda request: httpx.Response(200, content=b"null"))
    with pytest.raises(TakasumiBotToolkitResponseParseError) as ei:
        await kit.get_tax_info()
    assert ei.value.raw_body == "null"
    kit = make_kit(lambda request: httpx.Response(200, content=b""))
    with pytest.raises(TakasumiBotToolkitResponseParseError) as ei:
        await kit.get_tax_info()
    assert ei.value.code == "EMPTY_BODY" and ei.value.raw_body == ""


# ============================================================ 2-5 class names
def test_toolkit_class_names_match_npm():
    suffixes = [
        "Error",
        "ConfigError",
        "ValidationError",
        "HttpError",
        "NetworkError",
        "TimeoutError",
        "RetryLimitError",
        "ResponseParseError",
    ]
    for suffix in suffixes:
        new, old = f"TakasumiBotToolkit{suffix}", f"TakasumiBotKit{suffix}"
        assert getattr(tk, new).__name__ == new and new in tk.__all__
        assert not hasattr(tk, old) and old not in tk.__all__  # no legacy aliases
    assert not hasattr(tk, "TimeoutError") and not hasattr(tk, "ValidationError")


# ============================================================ 3-1 paginate
def test_paginate_matches_npm_result():
    result = paginate([1, 2, 3, 4, 5], 2, 2)
    assert result.model_dump() == {
        "items": [3, 4],
        "page": 2,
        "page_size": 2,
        "total_items": 5,
        "total_pages": 3,
        "has_next": True,
        "has_prev": True,
    }
    assert not hasattr(result, "total")  # NPM name only: total_items
    first = paginate([1, 2, 3], 1, 2)
    assert (first.has_prev, first.has_next) == (False, True)
    beyond = paginate([1, 2, 3], 10, 2)
    assert (beyond.items, beyond.has_next, beyond.has_prev) == ([], False, True)
    empty = paginate([], 1, 10)
    assert (empty.total_items, empty.total_pages, empty.has_next) == (0, 0, False)
    assert paginate([1, 2, 3], page=2, page_size=1).items == [2]


# ============================================================ 3-2 markdown
def test_markdown_table_matches_npm():
    rows = [{"name": "a", "age": 1}, {"name": "b", "age": 2, "extra": "ignored"}]
    assert to_markdown_table(rows) == (
        "| name | age |\n| --- | --- |\n| a | 1 |\n| b | 2 |"
    )  # columns come from the first row only
    assert to_markdown_table(rows, headers=["age", "extra"]) == (
        "| age | extra |\n| --- | --- |\n| 1 |  |\n| 2 | ignored |"
    )
    assert to_markdown_table(rows, align=["center", "right"]).splitlines()[1] == (
        "| :---: | ---: |"
    )
    assert to_markdown_table(rows, align=["right"]).splitlines()[1] == "| ---: | :--- |"
    assert to_markdown_table(rows, align=[]).splitlines()[1] == "| :--- | :--- |"
    assert to_markdown_table(rows, headers=[]) == ""
    assert to_markdown_table([{}]) == ""
    assert to_markdown_table([]) == ""
    # Like NPM, an empty rows list returns "" before headers/align are checked.
    assert to_markdown_table([], headers="bad", align="bad") == ""


@pytest.mark.parametrize(
    ("rows", "field"),
    [
        ([1, 2], "rows[0]"),
        ([["a", 1]], "rows[0]"),
        ([None], "rows[0]"),
        ([{"a": 1}, "scalar"], "rows"),
        ([{"a": 1}, [1]], "rows"),
    ],
)
def test_markdown_table_rows_must_be_records(rows, field):
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        to_markdown_table(rows)
    assert (ei.value.field, ei.value.code) == (field, "INVALID_ARGUMENT")


def test_markdown_table_cells():
    row = {
        "none": None,
        "flag": True,
        "n": 1.0,
        "at": datetime(2026, 1, 1, tzinfo=UTC),
        "pipe": "a|b\nc",
    }
    assert to_markdown_table([row]).splitlines()[2] == (
        "|  | true | 1.0 | 2026-01-01T00:00:00.000Z | a\\|b<br>c |"
    )


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"headers": "name"}, "headers"),
        ({"headers": ["a", 1]}, "headers"),
        ({"align": "left"}, "align"),
        ({"align": ["middle"]}, "align"),
        ({"align": [["left"]]}, "align"),
        ({"align": 3}, "align"),
    ],
)
def test_markdown_table_option_validation(kwargs, field):
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        to_markdown_table([{"a": 1}], **kwargs)
    assert ei.value.field == field and ei.value.code == "INVALID_ARGUMENT"


@pytest.mark.parametrize("rows", ["abc", b"abc", {"a": 1}, 42, None])
def test_markdown_table_rows_must_be_a_sequence(rows):
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        to_markdown_table(rows)
    assert ei.value.field == "rows"


# ============================================================ 3-3 format_number
@pytest.mark.parametrize(
    ("value", "kwargs", "expected"),
    [
        (1234567, {}, "1,234,567"),
        (1234.5678, {}, "1,234.568"),
        (0.1 + 0.2, {}, "0.3"),
        (1.005, {"maximum_fraction_digits": 2}, "1.01"),
        (1234.5, {"maximum_fraction_digits": 1}, "1,234.5"),
        (1, {"minimum_fraction_digits": 2}, "1.00"),
        (1.23456, {"minimum_fraction_digits": 4}, "1.2346"),
        (2**70, {}, "1,180,591,620,717,411,303,424"),
        (10**30 + 1, {}, "1,000,000,000,000,000,000,000,000,000,001"),
        (1e21, {}, "1,000,000,000,000,000,000,000"),
        (1000, {"notation": "compact"}, "1000"),
        (12345, {"notation": "compact"}, "1.2万"),
        (123456, {"notation": "compact"}, "12万"),
        (12345678, {"notation": "compact"}, "1235万"),
        (150_000_000, {"notation": "compact"}, "1.5億"),
        (1.5e12, {"notation": "compact"}, "1.5兆"),
        (1e20, {"notation": "compact"}, "10,000京"),
        (12345, {"notation": "compact", "compact_display": "long"}, "1.2万"),
        (12345, {"notation": "compact", "maximum_fraction_digits": 2}, "1.23万"),
        (0, {"notation": "compact", "minimum_fraction_digits": 2}, "0.00"),
        (0.001234, {"notation": "compact"}, "0.0012"),
        (1234, {"notation": "compact", "locale": "en-US"}, "1.2K"),
        (9999, {"notation": "compact", "locale": "en"}, "10K"),
        (1e16, {"notation": "compact", "locale": "en-US"}, "10,000T"),
        (
            1234,
            {"notation": "compact", "locale": "en-US", "compact_display": "long"},
            "1.2 thousand",
        ),
        (
            2_000_000,
            {"notation": "compact", "locale": "en-US", "compact_display": "long"},
            "2 million",
        ),
        (
            999_950,
            {"notation": "compact", "locale": "en-US", "maximum_fraction_digits": 2},
            "999.95K",
        ),
        (-1234.5, {"locale": "EN-us"}, "-1,234.5"),
        (1234567, {"notation": "compact", "locale": "en-US"}, "1.2M"),
    ],
)
def test_format_number_matches_intl(value, kwargs, expected):
    assert format_number(value, **kwargs) == expected


@pytest.mark.parametrize(
    ("kwargs", "field"),
    [
        ({"locale": "fr-FR"}, "locale"),
        ({"locale": "ja_JP"}, "locale"),
        ({"locale": 1}, "locale"),
        ({"minimum_fraction_digits": 3, "maximum_fraction_digits": 2}, "minimum_fraction_digits"),
        ({"maximum_fraction_digits": 101}, "maximum_fraction_digits"),
        ({"minimum_fraction_digits": -1}, "minimum_fraction_digits"),
        ({"minimum_fraction_digits": True}, "minimum_fraction_digits"),
        ({"notation": "scientific"}, "notation"),
        ({"compact_display": "tiny"}, "compact_display"),
    ],
)
def test_format_number_option_errors(kwargs, field):
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        format_number(1, **kwargs)
    assert ei.value.field == field


def test_format_number_defaults_to_ja_jp():
    assert DEFAULT_LOCALE == "ja-JP"
    assert format_number(150_000_000, notation="compact") == "1.5億"


# ============================================================ 3-4 format_timestamp
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_format_timestamp_accepts_epoch_milliseconds():
    assert format_timestamp(0) == "1970-01-01T00:00:00.000Z"
    assert format_timestamp(1704067200000) == "2024-01-01T00:00:00.000Z"
    assert format_timestamp(1704067200123.9) == "2024-01-01T00:00:00.123Z"
    assert format_timestamp(-1.5) == "1969-12-31T23:59:59.999Z"
    assert format_timestamp("2024-01-01T09:00:00.123456+09:00") == "2024-01-01T00:00:00.123Z"
    assert format_timestamp(NOW, format="iso", time_zone="Asia/Tokyo") == (
        "2026-01-01T00:00:00.000Z"
    )


def test_format_timestamp_locale_format():
    assert format_timestamp(1704067200000, format="locale", time_zone="Asia/Tokyo") == (
        "2024/1/1 9:00:00"
    )
    assert format_timestamp(1704067200000, format="locale", locale="en-US", time_zone="UTC") == (
        "1/1/2024, 12:00:00 AM"
    )
    assert format_timestamp(
        "2024-07-04T18:05:09Z", format="locale", locale="en", time_zone=UTC
    ) == ("7/4/2024, 6:05:09 PM")
    local = datetime(2024, 1, 1, tzinfo=UTC).astimezone()
    expected = (
        f"{local.year}/{local.month}/{local.day} {local.hour}:{local.minute:02d}:{local.second:02d}"
    )
    assert format_timestamp(1704067200000, format="locale") == expected


class _WindowsLocalTimeDatetime(datetime):
    """Mimics Windows, whose C ``localtime`` rejects pre-1970 instants."""

    def astimezone(self, tz=None):
        if tz is None:
            raise OSError(22, "Invalid argument")
        return super().astimezone(tz)


def test_format_timestamp_host_zone_failure_is_validation_error():
    value = _WindowsLocalTimeDatetime(1960, 1, 1, tzinfo=UTC)
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        format_timestamp(value, format="locale")
    assert ei.value.field == "value"
    assert "time_zone=" in str(ei.value)
    assert isinstance(ei.value.__cause__, OSError)
    # An explicit zone does not touch the host's localtime at all.
    assert format_timestamp(value, format="locale", time_zone="UTC") == "1960/1/1 0:00:00"


@pytest.mark.parametrize(
    ("delta", "locale", "expected"),
    [
        (timedelta(0), "ja-JP", "今"),
        (timedelta(milliseconds=499), "ja-JP", "今"),
        (timedelta(seconds=-30), "ja-JP", "30 秒前"),
        (timedelta(minutes=-5), "ja-JP", "5 分前"),
        (timedelta(minutes=90), "ja-JP", "2 時間後"),
        (timedelta(days=3), "ja-JP", "3 日後"),
        (timedelta(days=-45), "ja-JP", "1 か月前"),  # Math.round(-1.5) == -1
        (timedelta(days=45), "ja-JP", "2 か月後"),  # Math.round(1.5) == 2
        (timedelta(days=-400), "ja-JP", "1 年前"),
        (timedelta(days=-365 * 1234), "ja-JP", "1,234 年前"),
        (timedelta(0), "en-US", "now"),
        (timedelta(seconds=-1), "en-US", "1 second ago"),
        (timedelta(seconds=5), "en-US", "in 5 seconds"),
        (timedelta(hours=-1), "en", "1 hour ago"),
        (timedelta(days=2), "en-US", "in 2 days"),
    ],
)
def test_format_timestamp_relative_matches_intl(delta, locale, expected):
    assert format_timestamp(NOW + delta, format="relative", reference=NOW, locale=locale) == (
        expected
    )


def test_format_timestamp_relative_defaults_to_now():
    assert format_timestamp(datetime.now(UTC), format="relative") == "今"


@pytest.mark.parametrize(
    ("value", "kwargs", "field"),
    [
        (NOW, {"format": "date"}, "format"),
        (NOW, {"format": "locale", "locale": "de-DE"}, "locale"),
        (NOW, {"format": "relative", "locale": "xx"}, "locale"),
        (NOW, {"locale": 5}, "locale"),
        (NOW, {"format": "locale", "time_zone": "Mars/Base"}, "time_zone"),
        (NOW, {"format": "locale", "time_zone": 9}, "time_zone"),
        (NOW, {"format": "relative", "reference": datetime(2026, 1, 1)}, "reference"),
        (float("nan"), {}, "value"),
        (float("inf"), {}, "value"),
        (10**20, {}, "value"),
        (True, {}, "value"),
        (None, {}, "value"),
        ("2026-01-01T00:00:00", {}, "value"),
        ("yesterday", {}, "value"),
        (datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=9))), {}, "value"),
    ],
)
def test_format_timestamp_errors(value, kwargs, field):
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        format_timestamp(value, **kwargs)
    assert ei.value.field == field


@pytest.mark.parametrize(
    ("func", "kwargs"),
    [
        (format_number, {"decimals": 2}),
        (format_number, {"group": " "}),
        (format_number, {"compact": True}),
        (format_timestamp, {"style": "iso"}),
        (format_timestamp, {"tz": "UTC"}),
        (format_timestamp, {"epoch": 0}),
        (format_timestamp, {"unit": "ms"}),
        (to_markdown_table, {"columns": ["a"]}),
        (to_markdown_table, {"none_text": "-"}),
        (to_markdown_table, {"float_digits": 2}),
    ],
)
def test_pre_npm_options_were_removed(func, kwargs):
    first = [{"a": 1}] if func is to_markdown_table else 0
    with pytest.raises(TypeError):
        func(first, **kwargs)


# ============================================================ 3-5 pick/omit
def test_pick_and_omit_match_npm():
    picked = pick_fields({"a": 1, "b": 2, "c": 3}, ["c", "a", "missing"])
    assert list(picked.items()) == [("c", 3), ("a", 1)]  # the order of keys, as in NPM
    assert list(omit_fields({"a": 1, "b": 2, "c": 3}, ["b"])) == ["a", "c"]
    point = namedtuple("point", "x y")(1, 2)
    assert pick_fields(point, ["y"]) == {"y": 2}  # a namedtuple is one record
    with pytest.raises(TypeError):
        pick_fields({"a": 1}, ["a"], as_dict=True)


@pytest.mark.parametrize("func", [pick_fields, omit_fields])
@pytest.mark.parametrize("obj", [[{"a": 1}], ({"a": 1},), [], "abc", 42, None])
def test_pick_and_omit_reject_non_objects_like_npm(func, obj):
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        func(obj, ["a"])
    assert (ei.value.field, ei.value.code) == ("obj", "INVALID_ARGUMENT")


def test_pick_and_omit_check_the_object_before_the_keys():
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        pick_fields([{"a": 1}], "a")
    assert ei.value.field == "obj" and "each item" in str(ei.value)


# ============================================================ 3-6 kit.helpers
async def test_kit_helpers_namespace():
    kit, _ = recording_kit(STOCK_LIST)
    helpers = kit.helpers
    assert isinstance(helpers, Helpers) and helpers is HELPERS
    assert helpers.truncate is truncate and helpers.format_number is format_number
    assert helpers.truncate("hello world", 5) == "he..."
    assert helpers.pick_fields({"a": 1, "b": 2}, ["a"]) == {"a": 1}
    assert helpers.omit_fields({"a": 1, "b": 2}, ["b"]) == {"a": 1}
    assert helpers.to_markdown_table([{"a": 1}]) == "| a |\n| --- |\n| 1 |"
    assert helpers.format_number(1234567) == "1,234,567"
    assert helpers.format_timestamp(0) == "1970-01-01T00:00:00.000Z"
    assert helpers.paginate([1, 2, 3], 1, 2).has_next is True
    assert helpers.get_recent_stock_history([100, 101], 1) == [101]
    assert not hasattr(helpers, "paginate_async") and not hasattr(tk, "paginate_async")
    with pytest.raises(AttributeError):
        helpers.truncate = len
    with pytest.raises(AttributeError):
        helpers.extra = 1
    other, _ = recording_kit(STOCK_LIST)
    assert other.helpers is helpers


def test_toolkit_error_base_catches_everything():
    assert issubclass(TakasumiBotToolkitHttpError, TakasumiBotToolkitError)
