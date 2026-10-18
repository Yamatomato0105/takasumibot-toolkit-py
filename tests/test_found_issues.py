"""Regression tests for API and SDK edge cases.

Each test maps to one report item (H-1 ... I-7) and fails on the code as it was
before the fixes. The live-API shapes used here include ``accentColor: null``
and a company payload without ``employees``.
"""

import asyncio
import copy
import logging
import pickle
import typing
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from conftest import STOCK_LIST, TAX, json_response, make_kit
from takasumibot_toolkit import (
    BasicConfig,
    Issue,
    RetryConfig,
    TakasumiBotToolkitConfigError,
    TakasumiBotToolkitError,
    TakasumiBotToolkitHttpError,
    TakasumiBotToolkitNetworkError,
    TakasumiBotToolkitResponseParseError,
    TakasumiBotToolkitRetryLimitError,
    TakasumiBotToolkitTimeoutError,
    TakasumiBotToolkitValidationError,
    compute_delay_ms,
    create_kit_client,
    format_number,
    format_timestamp,
    omit_fields,
    paginate,
    pick_fields,
    to_markdown_table,
)
from takasumibot_toolkit import errors as errors_module
from takasumibot_toolkit.client import _type_adapter
from takasumibot_toolkit.models import (
    CompanyDetailResponse,
    DiscordUserSearchResponse,
    TaxResponse,
)

NOW = "2026-01-01T00:00:00Z"
REDIRECT_TARGET = "https://api.takasumibot.com/v3/tax?r=1"

# Live payload shape: accentColor is null for users who did
# not pick an accent colour on Discord (~70% of the ranking top 30).
DISCORD_USER_NULL_ACCENT = {
    "id": "559948383079497729",
    "username": "namekuji._.",
    "globalName": "ナメクジ",
    "avatar": "dd53",
    "banner": None,
    "accentColor": None,
    "bot": False,
    "flags": 0,
}

COMPANY_FIELDS = {
    "id": "C1",
    "name": "n",
    "description": "d",
    "assets": 1,
    "salary": 2,
    "jobType": "dev",
    "ownerId": "559948383079497729",
    "updatedAt": NOW,
    "createdAt": NOW,
    "statistics": {
        "companyId": "C1",
        "totalEarn": 1,
        "totalUse": 2,
        "totalTax": 3,
        "updatedAt": NOW,
    },
}

EMPLOYEE = {"userId": "559948383079497729", "companyId": "C1", "updatedAt": NOW, "joinedAt": NOW}


# --------------------------------------------------------------------- H-1
async def test_h1_discord_user_accent_color_may_be_null():
    kit = make_kit(lambda request: json_response(DISCORD_USER_NULL_ACCENT))
    user = await kit.get_discord_user_by_name("namekuji._.")
    assert user.accent_color is None
    assert user.global_name == "ナメクジ"
    assert user.id == "559948383079497729"


def test_h1_accent_color_still_accepts_an_int():
    payload = dict(DISCORD_USER_NULL_ACCENT, accentColor=16711680)
    assert DiscordUserSearchResponse.model_validate(payload).accent_color == 16711680
    annotation = DiscordUserSearchResponse.model_fields["accent_color"].annotation
    assert type(None) in typing.get_args(annotation)


# --------------------------------------------------------------------- H-2
async def test_h2_company_lookup_by_owner_id_omits_employees():
    # The live API drops the whole `employees` key when {id} is an owner's
    # Discord user id; it is present for a company id.
    kit = make_kit(lambda request: json_response(dict(COMPANY_FIELDS)))
    detail = await kit.get_company_by_id("559948383079497729")
    assert detail.employees is None
    assert detail.statistics.total_earn == 1


async def test_h2_company_employees_still_parsed_when_present():
    payload = dict(COMPANY_FIELDS, employees=[EMPLOYEE])
    kit = make_kit(lambda request: json_response(payload))
    detail = await kit.get_company_by_id("C1")
    assert detail.employees is not None and detail.employees[0].user_id == "559948383079497729"


def test_h2_empty_employees_is_not_confused_with_missing():
    detail = CompanyDetailResponse.model_validate(dict(COMPANY_FIELDS, employees=[]))
    assert detail.employees == []
    assert CompanyDetailResponse.model_validate(dict(COMPANY_FIELDS)).employees is None


# --------------------------------------------------------------------- M-1
@pytest.mark.parametrize("value", [".", ".."])
async def test_m1_bare_dot_path_params_are_rejected_before_sending(value):
    calls = {"n": 0, "urls": []}

    def handler(request):
        calls["n"] += 1
        calls["urls"].append(str(request.url))
        return json_response(TAX)

    kit = make_kit(handler)
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        await kit.get_profile_by_id(value)
    assert ei.value.code == "UNSAFE_PATH_PARAMETER"
    assert calls["n"] == 0 and calls["urls"] == []

    with pytest.raises(TakasumiBotToolkitValidationError):
        await kit.get_discord_user_by_name(value)
    assert calls["n"] == 0


async def test_m1_traversal_values_stay_inside_the_documented_path():
    urls = []

    def handler(request):
        urls.append(str(request.url))
        return json_response({"assets": 1, "chips": 2, "jobType": "dev"})

    kit = make_kit(handler)
    for value in ("a/../../b", "%2e%2e", "..%2f", "./x"):
        await kit.get_profile_by_id(value)
    assert urls == [
        "https://api.takasumibot.com/v3/profile/a%2F..%2F..%2Fb",
        "https://api.takasumibot.com/v3/profile/%252e%252e",
        "https://api.takasumibot.com/v3/profile/..%252f",
        "https://api.takasumibot.com/v3/profile/.%2Fx",
    ]


# --------------------------------------------------------------------- M-2
async def test_m2_logging_logger_adapter_survives_retries(caplog):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503) if calls["n"] == 1 else json_response(TAX)

    logger = logging.getLogger("takasumibot_toolkit.test.adapter")
    adapter = logging.LoggerAdapter(logger, {"component": "test"})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    kit = create_kit_client(
        client=client,
        logger=adapter,
        retry={"max_retries": 1, "initial_delay_ms": 0, "max_delay_ms": 0, "jitter": False},
    )
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        assert (await kit.get_tax_info()).idle_tax == 100
    assert calls["n"] == 2
    retry_record = next(r for r in caplog.records if r.message.startswith("retrying request"))
    assert retry_record.attempt == 1
    # A stdlib LoggerAdapter keeps its own extra (its process() replaces it).
    assert retry_record.component == "test"


class _MergingAdapter(logging.LoggerAdapter):
    """A LoggerAdapter with a custom ``process()``: it must stay in charge."""

    def process(self, msg, kwargs):
        extra = dict(self.extra or {})
        extra.update(kwargs.get("extra") or {})
        kwargs["extra"] = extra
        return f"[{extra.get('component', '?')}] {msg}", kwargs


async def test_m2_custom_logger_adapter_process_is_honoured(caplog):
    logger = logging.getLogger("takasumibot_toolkit.test.merging_adapter")
    kit = make_kit(
        lambda request: json_response(TAX),
        logger=_MergingAdapter(logger, {"component": "bot"}),
    )
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        assert (await kit.get_tax_info()).idle_tax == 100
    record = next(r for r in caplog.records if "http request" in r.message)
    assert record.message.startswith("[bot] ")
    # The toolkit context survives next to the adapter's own extra.
    assert record.url == "https://api.takasumibot.com/v3/tax"
    assert record.component == "bot"


class _ExplodingLogger:
    """A LoggerLike whose every call fails (M-2: must not break a request)."""

    def _boom(self, msg, /, **context):
        raise RuntimeError(f"logger is broken: {msg}")

    debug = info = warning = error = _boom


async def test_m2_broken_logger_does_not_break_the_request():
    kit = make_kit(lambda request: json_response(TAX), logger=_ExplodingLogger())
    assert (await kit.get_tax_info()).idle_tax == 100


async def test_m2_log_messages_include_the_url(caplog):
    logger = logging.getLogger("takasumibot_toolkit.test.url")
    kit = make_kit(lambda request: json_response(TAX), logger=logger)
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        await kit.get_tax_info()
    request_record = next(r for r in caplog.records if r.message.startswith("http request"))
    response_record = next(r for r in caplog.records if r.message.startswith("http response"))
    assert "https://api.takasumibot.com/v3/tax" in request_record.message
    assert "https://api.takasumibot.com/v3/tax" in response_record.message
    assert request_record.url == response_record.url == "https://api.takasumibot.com/v3/tax"
    assert response_record.status == 200


# --------------------------------------------------------------------- M-3
def _sample_errors():
    cause = TakasumiBotToolkitError("inner")
    return [
        TakasumiBotToolkitError("plain", code="HTTP_ERROR", context={"a": 1}),
        TakasumiBotToolkitConfigError("config", field="timeout_ms", value=0),
        TakasumiBotToolkitValidationError("validation", field="id", value="x"),
        TakasumiBotToolkitHttpError(
            "GET https://x/v3/tax failed with HTTP 503",
            status=503,
            method="GET",
            url="https://x/v3/tax",
            status_text="Service Unavailable",
            request_id="r-1",
            api_code="RATE_LIMITED",
            api_message="slow down",
            headers={"retry-after": "2"},
            json_body={"code": "RATE_LIMITED"},
            raw_body='{"code": "RATE_LIMITED"}',
            retry_after="2",
        ),
        TakasumiBotToolkitNetworkError(
            "network", method="GET", url="https://x", reason="ConnectError"
        ),
        TakasumiBotToolkitTimeoutError(
            "timeout", timeout_ms=500, phase="read", method="GET", url="https://x", cause=cause
        ),
        TakasumiBotToolkitRetryLimitError(
            "exhausted",
            attempts=4,
            max_retries=3,
            last_error=cause,
            delays_ms=(100, 200, 400),
            method="GET",
            url="https://x",
        ),
        TakasumiBotToolkitResponseParseError(
            "parse",
            method="GET",
            url="https://x",
            status=200,
            stage="validate",
            raw_body="{}",
            issues=(Issue(field="idle_tax", message="Field required", type="missing"),),
        ),
    ]


@pytest.mark.parametrize("error", _sample_errors(), ids=lambda e: type(e).__name__)
def test_m3_errors_survive_pickle_copy_and_deepcopy(error):
    restored = pickle.loads(pickle.dumps(error))
    assert type(restored) is type(error)
    assert restored.message == error.message
    assert restored.code == error.code
    assert restored.retryable == error.retryable
    assert restored.to_dict() == error.to_dict()
    assert str(restored) == str(error)
    assert dict(restored.context) == dict(error.context)
    for copied in (copy.copy(error), copy.deepcopy(error)):
        assert type(copied) is type(error) and copied.message == error.message


def test_m3_http_error_details_survive_pickle():
    error = next(e for e in _sample_errors() if isinstance(e, TakasumiBotToolkitHttpError))
    restored = pickle.loads(pickle.dumps(error))
    assert restored.status == 503 and restored.status_code == 503
    assert restored.api_message == "slow down" and restored.request_id == "r-1"
    assert dict(restored.headers) == {"retry-after": "2"}
    assert restored.retry_after == "2" and restored.raw_body == '{"code": "RATE_LIMITED"}'
    with pytest.raises(AttributeError):
        restored.status = 500


def test_m3_retry_limit_error_keeps_last_error_and_cause():
    error = next(e for e in _sample_errors() if isinstance(e, TakasumiBotToolkitRetryLimitError))
    restored = pickle.loads(pickle.dumps(error))
    assert restored.attempts == 4 and restored.delays_ms == (100, 200, 400)
    assert isinstance(restored.last_error, TakasumiBotToolkitError)
    assert restored.last_error.message == "inner"
    assert restored.cause is restored.last_error


def test_m3_parse_error_issues_survive_pickle():
    error = next(e for e in _sample_errors() if isinstance(e, TakasumiBotToolkitResponseParseError))
    restored = pickle.loads(pickle.dumps(error))
    assert restored.stage == "validate" and restored.status == 200
    assert restored.issues == error.issues


# --------------------------------------------------------------------- M-4
async def test_m4_caller_cannot_corrupt_the_stock_cache():
    kit = make_kit(lambda request: json_response(STOCK_LIST), stock_cache={"ttl_ms": 60_000})
    entries = await kit._load_stock_entries()
    entries.clear()
    assert await kit.get_stock_price_by_id("AAA") == 120
    assert len(await kit._load_stock_entries()) == len(STOCK_LIST)


async def test_m4_mutating_returned_entries_cannot_change_the_cache():
    # StockEntry is frozen, but its ``prices`` list is not: cached entries must
    # never be shared with callers (on a miss, a hit or a forced refresh).
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return json_response(STOCK_LIST)

    kit = make_kit(handler, stock_cache={"ttl_ms": 60_000})
    original = list(STOCK_LIST[0]["prices"])
    (await kit.get_stock_list())[0].prices.append(999)  # freshly fetched (miss)
    hit = await kit.get_stock_list()
    assert hit[0].prices == original
    hit[0].prices.clear()  # served from the cache (hit)
    (await kit.get_stock_list(force_refresh=True))[0].prices.append(1)
    (await kit.get_stock_info_by_id("AAA")).prices.append(2)
    assert (await kit.get_stock_list())[0].prices == original
    assert await kit.get_stock_price_by_id("AAA") == original[-1]
    assert calls["n"] == 2  # one miss and one forced refresh; the rest are hits


async def test_m4_stock_cache_still_single_flight():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return json_response(STOCK_LIST)

    kit = make_kit(handler, stock_cache={"ttl_ms": 60_000})
    results = await asyncio.gather(*(kit.get_stock_price_by_id("AAA") for _ in range(20)))
    assert results == [120] * 20
    assert calls["n"] == 1


# --------------------------------------------------------------------- M-5
async def test_m5_remote_protocol_error_is_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.RemoteProtocolError(
                "Server disconnected without sending a response.", request=request
            )
        return json_response(TAX)

    kit = make_kit(
        handler, retry={"max_retries": 2, "initial_delay_ms": 0, "max_delay_ms": 0, "jitter": False}
    )
    assert (await kit.get_tax_info()).idle_tax == 100
    assert calls["n"] == 2


async def test_m5_local_protocol_error_is_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.LocalProtocolError("illegal request", request=request)

    kit = make_kit(
        handler, retry={"max_retries": 2, "initial_delay_ms": 0, "max_delay_ms": 0, "jitter": False}
    )
    with pytest.raises(TakasumiBotToolkitNetworkError) as ei:
        await kit.get_tax_info()
    assert ei.value.retryable is False and ei.value.reason == "LocalProtocolError"
    assert calls["n"] == 1


# --------------------------------------------------------------------- M-6
SHADOWING_NAMES = (
    "ConfigError",
    "ValidationError",
    "HttpError",
    "NetworkError",
    "TimeoutError",
    "RetryLimitError",
    "ResponseParseError",
)


def test_m6_no_short_aliases_shadow_builtins_or_pydantic():
    import takasumibot_toolkit as package

    for name in SHADOWING_NAMES:
        assert name not in package.__all__
        assert not hasattr(package, name)
        assert name not in errors_module.__all__
        assert not hasattr(errors_module, name)


async def test_m6_builtin_timeout_error_still_catchable():
    async def slow_handler(request):
        await asyncio.sleep(5)
        return json_response(TAX)

    kit = make_kit(slow_handler)
    with pytest.raises(TimeoutError):  # the builtin, raised by asyncio.timeout
        async with asyncio.timeout(0.05):
            await kit.get_tax_info()


# --------------------------------------------------------------------- L-1
# Compact notation now follows Intl.NumberFormat (NPM parity): the unit follows
# the locale (ja-JP by default) and rounding keeps 2 significant digits.
@pytest.mark.parametrize(
    ("value", "locale", "expected"),
    [
        (999_950, "en-US", "1M"),
        (999_999_999, "en-US", "1B"),
        (999_999_999_999, "en-US", "1T"),
        (-999_950, "en-US", "-1M"),
        (1500, "en-US", "1.5K"),
        (2_500_000, "en-US", "2.5M"),
        (999, "en-US", "999"),
        (1000, "en-US", "1K"),
        (999_949, "en-US", "1M"),
        (9_999.5, "ja-JP", "1万"),
        (99_999_999, "ja-JP", "1億"),
        (999_950, "ja-JP", "100万"),
    ],
)
def test_l1_compact_rounding_carries_into_the_next_unit(value, locale, expected):
    assert format_number(value, notation="compact", locale=locale) == expected


# --------------------------------------------------------------------- L-2
@pytest.mark.parametrize(
    ("value", "decimals", "expected"),
    [(-0.4, 0, "0"), (-0.0, 0, "0"), (-0.001, 2, "0.00"), (-2.5, 0, "-3"), (2.5, 0, "3")],
)
def test_l2_no_negative_zero(value, decimals, expected):
    assert (
        format_number(value, minimum_fraction_digits=decimals, maximum_fraction_digits=decimals)
        == expected
    )


# --------------------------------------------------------------------- L-3
@pytest.mark.parametrize("keys", ["id", b"id", ["i", 1], None, 42])
def test_l3_keys_must_be_a_sequence_of_str(keys):
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        pick_fields({"id": "A"}, keys)
    assert ei.value.field == "keys"
    with pytest.raises(TakasumiBotToolkitValidationError):
        omit_fields({"id": "A"}, keys)


def test_l3_valid_keys_still_work():
    assert pick_fields({"id": "A", "x": 1}, ("id",)) == {"id": "A"}
    assert omit_fields({"id": "A", "x": 1}, ["x"]) == {"id": "A"}
    assert pick_fields({"id": "A"}, iter(("id",))) == {"id": "A"}


# --------------------------------------------------------------------- L-4
def test_l4_markdown_headers_are_escaped():
    assert to_markdown_table([{"a|b": 1}]) == "| a\\|b |\n| --- |\n| 1 |"
    table = to_markdown_table([{"x": 1}], headers=["col|name", "line\nbreak"])
    assert table.splitlines()[0] == "| col\\|name | line<br>break |"
    assert table.splitlines()[1] == "| --- | --- |"


# --------------------------------------------------------------------- L-5
_L5_NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize(
    ("value", "kwargs", "field"),
    [
        (1e20, {}, "value"),
        (float("nan"), {}, "value"),
        (float("inf"), {}, "value"),
        (10**400, {}, "value"),
        (_L5_NOW, {"format": "locale", "time_zone": 123}, "time_zone"),
        (_L5_NOW, {"format": "locale", "time_zone": object()}, "time_zone"),
        (_L5_NOW, {"format": "relative", "reference": "2026-01-01T00:00:00Z"}, "reference"),
        (_L5_NOW, {"format": "relative", "reference": datetime(2026, 1, 1)}, "reference"),
    ],
)
def test_l5_format_timestamp_rejects_bad_arguments(value, kwargs, field):
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        format_timestamp(value, **kwargs)
    assert ei.value.field == field


def test_l5_format_timestamp_valid_values_and_time_zones():
    assert format_timestamp(0) == "1970-01-01T00:00:00.000Z"
    assert format_timestamp(1_000) == "1970-01-01T00:00:01.000Z"
    tokyo = ZoneInfo("Asia/Tokyo")
    assert format_timestamp(_L5_NOW, format="locale", time_zone=tokyo) == "2026/1/1 9:00:00"
    assert format_timestamp(_L5_NOW, format="locale", time_zone=UTC) == "2026/1/1 0:00:00"


# --------------------------------------------------------------------- L-6
@pytest.mark.parametrize("factor", [float("nan"), float("inf"), float("-inf")])
def test_l6_retry_config_rejects_non_finite_backoff(factor):
    with pytest.raises(TakasumiBotToolkitConfigError) as ei:
        RetryConfig(backoff_factor=factor)
    assert ei.value.field == "backoff_factor"


@pytest.mark.parametrize("attempt", [0, 1, 10, 1100, 100_000])
def test_l6_compute_delay_does_not_overflow(attempt):
    cfg = RetryConfig(initial_delay_ms=200, max_delay_ms=2_000, jitter=False)
    assert compute_delay_ms(cfg, attempt=attempt) == min(200 * 2**attempt, 2_000)


def test_l6_compute_delay_with_extreme_configuration():
    cfg = RetryConfig(initial_delay_ms=1, max_delay_ms=10**400, jitter=False)
    assert compute_delay_ms(cfg, attempt=1100) <= 2**53 - 1
    assert compute_delay_ms(cfg, attempt=0) == 1
    assert compute_delay_ms(RetryConfig(backoff_factor=1.0, jitter=False), attempt=50) == 300
    # A non-finite Retry-After is ignored instead of poisoning the delay.
    assert compute_delay_ms(cfg, attempt=0, retry_after_s=float("nan")) == 1


# --------------------------------------------------------------------- L-7
def test_l7_total_pages_is_consistent_for_no_items():
    beyond = paginate([], 5, 2)
    assert (beyond.total_items, beyond.total_pages, beyond.has_next) == (0, 0, False)
    assert beyond.has_prev is True  # page > 1, as in NPM


# --------------------------------------------------------------------- L-8
@pytest.mark.parametrize("items", [{"a": 1}, "abc", b"abc", 42, None, {1, 2}])
def test_l8_paginate_rejects_non_sequence_items(items):
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        paginate(items)
    assert ei.value.field == "items" and ei.value.code == "INVALID_ARGUMENT"


def test_l8_paginate_accepts_sequences():
    assert paginate((1, 2, 3), page=2, page_size=2).items == [3]
    assert paginate([1, 2, 3]).total_items == 3


# --------------------------------------------------------------------- L-9
@pytest.mark.parametrize(
    "headers",
    [
        {"X\tA": "v"},
        {"X(A)": "v"},
        {"X\x00A": "v"},
        {"X\x7f": "v"},
        {"X A": "v"},
        {"X-A": "v\x00"},
        {"X-A": "v\x7f"},
        {"X-A": "v\x1b"},
        {"X-A": "値"},
    ],
)
def test_l9_header_validation_is_complete(headers):
    with pytest.raises(TakasumiBotToolkitConfigError):
        BasicConfig(headers=headers)


@pytest.mark.parametrize("headers", [{"X-A": "v"}, {"x-request-id": "1"}, {"X-A": "a\tb"}])
def test_l9_valid_headers_are_accepted(headers):
    assert BasicConfig(headers=headers).headers == headers


# -------------------------------------------------------------------- L-10
def test_l10_fixed_decimals_come_from_format_number():
    # Table cells render floats with repr (Q-23); format_number pads decimals.
    assert to_markdown_table([{"x": 1.5}]).splitlines()[2] == "| 1.5 |"
    assert format_number(1.5, minimum_fraction_digits=2) == "1.50"


# --------------------------------------------------------------------- I-1
async def test_i1_follow_redirects_applies_to_injected_clients():
    def handler(request):
        if request.url.path == "/v3/tax" and "r" not in dict(request.url.params):
            return httpx.Response(302, headers={"location": REDIRECT_TARGET})
        return json_response(TAX)

    following = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    kit = create_kit_client(client=following, follow_redirects=True)
    assert (await kit.get_tax_info()).idle_tax == 100

    strict = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    kit = create_kit_client(client=strict)  # config default (False) wins
    with pytest.raises(TakasumiBotToolkitHttpError) as ei:
        await kit.get_tax_info()
    assert ei.value.status == 302


# --------------------------------------------------------------------- I-4
def test_i4_pick_and_omit_return_one_dict():
    # NPM parity: one object in, one dict out (the list overloads are gone).
    assert typing.get_overloads(pick_fields) == [] and typing.get_overloads(omit_fields) == []
    assert pick_fields.__annotations__["return"] == "dict[str, object]"
    assert omit_fields.__annotations__["return"] == "dict[str, object]"


# --------------------------------------------------------------------- I-6
def test_i6_type_adapters_are_cached_per_model_and_shape():
    single = _type_adapter(TaxResponse, False)
    assert single is _type_adapter(TaxResponse, False)
    assert _type_adapter(TaxResponse, True) is not single


async def test_i6_error_codes_are_unchanged_by_the_fast_json_path():
    kit = make_kit(lambda request: httpx.Response(200, content=b"not json"))
    with pytest.raises(TakasumiBotToolkitResponseParseError) as ei:
        await kit.get_tax_info()
    assert ei.value.code == "INVALID_JSON" and ei.value.stage == "decode"

    kit = make_kit(lambda request: httpx.Response(200, content=b""))
    with pytest.raises(TakasumiBotToolkitResponseParseError) as ei:
        await kit.get_tax_info()
    assert ei.value.code == "EMPTY_BODY"

    kit = make_kit(lambda request: json_response(None))
    with pytest.raises(TakasumiBotToolkitResponseParseError) as ei:
        await kit.get_tax_info()
    assert ei.value.stage == "validate" and ei.value.issues[0].type == "null_type"

    kit = make_kit(lambda request: httpx.Response(200, content=b'{"idleTax": NaN}'))
    with pytest.raises(TakasumiBotToolkitResponseParseError) as ei:
        await kit.get_tax_info()
    assert ei.value.code == "INVALID_JSON"

    kit = make_kit(lambda request: json_response(dict(TAX, idleTax="x")))
    with pytest.raises(TakasumiBotToolkitResponseParseError) as ei:
        await kit.get_tax_info()
    assert ei.value.code == "SCHEMA_MISMATCH" and ei.value.issues


# --------------------------------------------------------------------- I-7
def test_i7_unused_json_check_helper_is_gone():
    assert not hasattr(errors_module, "_json_check")
    assert not hasattr(errors_module, "json")
