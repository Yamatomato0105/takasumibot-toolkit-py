import json
import logging

import httpx
import pytest

from conftest import TAX, json_response, make_kit
from takasumibot_toolkit import (
    BasicConfig,
    TakasumiBotToolkitConfigError,
    TakasumiBotToolkitHttpError,
    TakasumiBotToolkitResponseParseError,
    TakasumiBotToolkitTimeoutError,
    TakasumiBotToolkitValidationError,
    create_kit_client,
)


async def test_normal_request_and_url_trim():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["ua"] = request.headers.get("user-agent")
        return json_response(TAX)

    kit = make_kit(handler)
    tax = await kit.get_tax_info()
    assert tax.idle_tax == 100
    assert seen["url"] == "https://api.takasumibot.com/v3/tax"
    assert seen["ua"].startswith("takasumibot-toolkit/")


@pytest.mark.parametrize(
    ("header", "value"),
    [("User-Agent", "custom-agent/1.0"), ("Accept", "text/plain"), ("user-agent", "lower/1.0")],
)
async def test_custom_headers_override_defaults_case_insensitively(header, value):
    seen = {}

    def handler(request):
        seen["values"] = request.headers.get_list(header)
        return json_response(TAX)

    kit = make_kit(handler, headers={header: value})
    await kit.get_tax_info()
    assert seen["values"] == [value]


@pytest.mark.parametrize("constant", ["NaN", "Infinity", "-Infinity"])
async def test_nonstandard_json_constants_are_rejected_even_in_extra_fields(constant):
    body = json.dumps(TAX)[:-1] + f',"futureField":{constant}}}'
    kit = make_kit(lambda request: httpx.Response(200, content=body.encode()))
    with pytest.raises(TakasumiBotToolkitResponseParseError) as exc_info:
        await kit.get_tax_info()
    assert exc_info.value.stage == "decode"
    assert exc_info.value.code == "INVALID_JSON"


async def test_stdlib_logger_handles_structured_debug_context(caplog):
    logger = logging.getLogger("takasumibot_toolkit.test.debug")
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: json_response(TAX)))
    kit = create_kit_client(client=client, logger=logger)

    with caplog.at_level(logging.DEBUG, logger=logger.name):
        assert (await kit.get_tax_info()).idle_tax == 100

    await kit.aclose()
    await client.aclose()
    request_record = next(
        record for record in caplog.records if record.message.startswith("http request")
    )
    assert request_record.method == "GET"
    # The URL is part of the message too, so it shows up in plain log formats.
    assert request_record.url == "https://api.takasumibot.com/v3/tax"
    assert request_record.url in request_record.message


async def test_stdlib_logger_handles_retry_context(caplog):
    logger = logging.getLogger("takasumibot_toolkit.test.retry")
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"message": "try again"})
        return json_response(TAX)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    kit = create_kit_client(
        client=client,
        logger=logger,
        retry={"max_retries": 1, "initial_delay_ms": 0, "max_delay_ms": 0, "jitter": False},
    )
    with caplog.at_level(logging.DEBUG, logger=logger.name):
        assert (await kit.get_tax_info()).idle_tax == 100

    await kit.aclose()
    await client.aclose()
    assert calls == 2
    retry_record = next(
        record for record in caplog.records if record.message.startswith("retrying request")
    )
    assert retry_record.attempt == 1
    assert retry_record.url in retry_record.message


async def test_path_encoding():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return json_response(
            {
                "accentColor": 1,
                "avatar": None,
                "banner": None,
                "bot": False,
                "flags": 0,
                "globalName": None,
                "id": "42",
                "username": "foo",
            }
        )

    kit = make_kit(handler)
    user = await kit.get_discord_user_by_name("foo#bar/baz")
    assert user.id == "42"
    assert seen["url"].endswith("/v3/discord/usersearch/foo%23bar%2Fbaz")


@pytest.mark.parametrize(
    "bad,code",
    [
        ("", "EMPTY_ARGUMENT"),
        ("  ", "EMPTY_ARGUMENT"),
        ("a{b", "UNSAFE_PATH_PARAMETER"),
        (123, "INVALID_TYPE"),
    ],
)
async def test_path_validation_no_send(bad, code):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return json_response({})

    kit = make_kit(handler)
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        await kit.get_profile_by_id(bad)
    assert ei.value.code == code
    assert calls["n"] == 0


@pytest.mark.parametrize("bad", ["", "  ", "a{b", 123, "A", "ABCDEFGHI", "ABCDEFGHIJK"])
async def test_gift_id_validation_no_send(bad):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return json_response({})

    kit = make_kit(handler)
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        await kit.get_gift_info(bad)
    assert ei.value.code == "INVALID_GIFT_ID" and ei.value.field == "id"
    assert calls["n"] == 0


async def test_http_error_best_effort():
    def handler(request):
        return httpx.Response(
            404,
            content=b'{"code": "NOT_FOUND", "message": "missing", "requestId": "r-1"}',
            headers={"x-request-id": "hdr-1", "content-type": "application/json"},
        )

    kit = make_kit(handler)
    with pytest.raises(TakasumiBotToolkitHttpError) as ei:
        await kit.get_tax_info()
    err = ei.value
    assert err.status == 404 and err.retryable is False
    assert err.api_code == "NOT_FOUND" and err.api_message == "missing"
    assert err.request_id == "hdr-1"
    assert err.json_body["code"] == "NOT_FOUND"


async def test_redirect_not_followed_is_http_error():
    def handler(request):
        return httpx.Response(302, headers={"location": "https://example.com/"})

    kit = make_kit(handler)
    with pytest.raises(TakasumiBotToolkitHttpError) as ei:
        await kit.get_tax_info()
    assert ei.value.status == 302


async def test_timeout_mapped_and_retried_zero():
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    kit = make_kit(handler)
    with pytest.raises(TakasumiBotToolkitTimeoutError) as ei:
        await kit.get_tax_info()
    assert ei.value.retryable is True and ei.value.phase == "read"


async def test_parse_errors():
    kit = make_kit(lambda r: httpx.Response(200, content=b"not json"))
    with pytest.raises(TakasumiBotToolkitResponseParseError) as ei:
        await kit.get_tax_info()
    assert ei.value.stage == "decode" and ei.value.code == "INVALID_JSON"

    kit = make_kit(lambda r: httpx.Response(200, content=b""))
    with pytest.raises(TakasumiBotToolkitResponseParseError) as ei:
        await kit.get_tax_info()
    assert ei.value.code == "EMPTY_BODY"

    kit = make_kit(lambda r: json_response(None))
    with pytest.raises(TakasumiBotToolkitResponseParseError) as ei:
        await kit.get_tax_info()
    assert ei.value.stage == "validate"

    bad = dict(TAX, idleTax="x")
    kit = make_kit(lambda r: json_response(bad))
    with pytest.raises(TakasumiBotToolkitResponseParseError) as ei:
        await kit.get_tax_info()
    assert ei.value.issues and ei.value.code == "SCHEMA_MISMATCH"


async def test_missing_field_and_naive_datetime_are_parse_errors():
    bad = dict(TAX)
    del bad["idleTax"]
    kit = make_kit(lambda r: json_response(bad))
    with pytest.raises(TakasumiBotToolkitResponseParseError):
        await kit.get_tax_info()

    gift = {
        "type": "gift",
        "id": "A",
        "userId": "1",
        "status": "unused",
        "receiverId": None,
        "amount": 1,
        "boughtAt": None,
        "createdAt": "2026-01-01T00:00:00",
    }
    kit = make_kit(lambda r: json_response(gift))
    with pytest.raises(TakasumiBotToolkitResponseParseError):
        await kit.get_gift_info("A1B2C3D4E5")


async def test_injected_client_ownership():
    transport = httpx.MockTransport(lambda r: json_response(TAX))
    http_client = httpx.AsyncClient(transport=transport)
    kit = create_kit_client(client=http_client)
    assert kit.owns_http_client is False
    await kit.aclose()
    assert http_client.is_closed is False
    await http_client.aclose()
    with pytest.raises(TakasumiBotToolkitConfigError) as ei:
        await kit.get_tax_info()
    assert ei.value.code == "CLIENT_CLOSED"


async def test_owned_client_closed():
    kit = create_kit_client()
    assert kit.owns_http_client is True
    await kit.aclose()
    await kit.aclose()  # idempotent
    assert kit.closed is True


def test_config_has_no_base_url():
    assert "base_url" not in BasicConfig.model_fields
    with pytest.raises(TakasumiBotToolkitConfigError):
        BasicConfig(base_url="https://example.com")  # type: ignore[call-arg]


async def test_array_response_returns_list():
    def handler(request):
        return json_response([])

    kit = make_kit(handler)
    assert await kit.get_ranking() == []
