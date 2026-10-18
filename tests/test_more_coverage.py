from datetime import UTC, datetime, timedelta

import httpx
import pytest

from conftest import TAX, json_response, make_kit
from takasumibot_toolkit import (
    BasicConfig,
    RetryConfig,
    TakasumiBotToolkitValidationError,
    format_number,
    format_timestamp,
    omit_fields,
    parse_retry_after,
    pick_fields,
    to_markdown_table,
    truncate,
)
from takasumibot_toolkit.helpers.extra import as_dict, ellipsis
from takasumibot_toolkit.models import StockEntry
from takasumibot_toolkit.retry import compute_delay_ms
from takasumibot_toolkit.stock import validate_stock_id

NOW = "2026-01-01T00:00:00Z"

PAYLOADS = {
    "/v3/gift/A1B2C3D4E5": {
        "type": "gift",
        "id": "A1B2C3D4E5",
        "userId": "1",
        "status": "received",
        "receiverId": "2",
        "amount": 3,
        "boughtAt": NOW,
        "createdAt": NOW,
    },
    "/v3/shard": {
        "data": [{"shardId": "0", "guildCount": "3", "userCount": "4", "ping": "5"}],
        "loggedAt": NOW,
    },
    "/v3/statistics": {
        "user": {"totalEarn": 1, "totalUse": 2, "totalTax": 3, "totalWork": 4, "totalCommand": 5},
        "company": {"totalEarn": 1, "totalUse": 2, "totalTax": 3},
        "economy": {"treasury": 10, "debt": 20},
        "event": {
            "total": 1,
            "totalOnlyUser": 2,
            "messageCreate": {
                "oneDay": 1,
                "difference": 2,
                "oneDayOnlyUser": 3,
                "differenceOnlyUser": 4,
            },
            "interactionCreate": {
                "oneDay": 1,
                "difference": 2,
                "oneDayOnlyUser": 3,
                "differenceOnlyUser": 4,
            },
            "guildMemberAdd": {"oneDay": 1, "difference": 2},
            "guildMemberRemove": {"oneDay": 1, "difference": 2},
            "guildCreate": {"oneDay": 1, "difference": 2},
            "guildDelete": {"oneDay": 1, "difference": 2},
        },
    },
    "/v3/history/1": [{"id": 1, "userId": "1", "amount": 5, "reason": "work", "tradedAt": NOW}],
    "/v3/profile/1": {"assets": 1, "chips": 2, "jobType": "dev"},
    "/v3/ranking": [
        {"id": "1", "username": "u", "avatarURL": None, "assets": 1, "chips": 2, "jobType": "dev"}
    ],
    "/v3/companylist": [
        {
            "id": "C1",
            "name": "n",
            "description": "d",
            "assets": 1,
            "salary": 2,
            "jobType": "dev",
            "ownerId": "9",
            "updatedAt": NOW,
            "createdAt": NOW,
        }
    ],
    "/v3/company/C1": {
        "id": "C1",
        "name": "n",
        "description": "d",
        "assets": 1,
        "salary": 2,
        "jobType": "dev",
        "ownerId": "9",
        "updatedAt": NOW,
        "createdAt": NOW,
        "statistics": {
            "companyId": "C1",
            "totalEarn": 1,
            "totalUse": 2,
            "totalTax": 3,
            "updatedAt": NOW,
        },
        "employees": [{"userId": "9", "companyId": "C1", "updatedAt": NOW, "joinedAt": NOW}],
    },
    "/v3/companyHistory/C1": [
        {"id": 1, "companyId": "C1", "userId": "9", "amount": 5, "reason": "r", "tradedAt": NOW}
    ],
    "/v3/status": [
        {
            "id": 1,
            "ping": 10,
            "totalUser": 1,
            "totalGuild": 2,
            "totalCommand": 3,
            "cpuUsage": 4,
            "memoryUsage": 5,
            "loggedAt": NOW,
        }
    ],
    "/v3/tax": TAX,
}


def handler_for(request):
    return json_response(PAYLOADS[str(request.url.path)])


async def test_all_endpoints():
    kit = make_kit(handler_for)
    assert (await kit.get_gift_info("A1B2C3D4E5")).receiver_id == "2"
    assert (await kit.get_shard_info()).data[0].ping == "5"
    stats = await kit.get_statistics_info()
    assert stats.event.message_create.one_day_only_user == 3
    assert stats.company.total_tax == 3
    assert (await kit.get_history_by_id("1"))[0].amount == 5
    assert (await kit.get_profile_by_id("1")).job_type == "dev"
    assert (await kit.get_ranking())[0].avatar_url is None
    assert (await kit.get_company_list())[0].owner_id == "9"
    detail = await kit.get_company_by_id("C1")
    assert detail.statistics.total_earn == 1 and detail.employees[0].user_id == "9"
    assert (await kit.get_company_history_by_id("C1"))[0].company_id == "C1"
    assert (await kit.get_status())[0].cpu_usage == 4


def test_retry_after_parsing():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert parse_retry_after(" 2 ") == 2.0
    assert parse_retry_after("-5") == 0.0
    assert parse_retry_after("") is None
    future = (now + timedelta(seconds=90)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(future, now=now) == pytest.approx(90.0)
    past = (now - timedelta(seconds=90)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert parse_retry_after(past, now=now) == 0.0


def test_compute_delay_retry_after_clip_and_zero():
    cfg = RetryConfig(initial_delay_ms=100, max_delay_ms=2000, jitter=False)
    assert compute_delay_ms(cfg, attempt=0, retry_after_s=100000) == 6000
    cfg0 = RetryConfig(initial_delay_ms=0, max_delay_ms=0, jitter=False)
    assert compute_delay_ms(cfg0, attempt=0) == 0


def test_helper_edges():
    assert truncate("abcdef", 5, ellipsis="…") == "abcd…"
    assert ellipsis("abcdef", 4) == "a..."
    assert as_dict({"a": 1}) == {"a": 1}
    entry = StockEntry.model_validate(
        {
            "name": "n",
            "id": "X",
            "description": "d",
            "dividendAmount": 1,
            "dividendRate": 0.5,
            "prices": [1],
        }
    )
    assert as_dict(entry)["id"] == "X"
    with pytest.raises(TakasumiBotToolkitValidationError):
        omit_fields([{"a": 1, "b": 2}], ("b",))  # one object only, as in NPM
    assert (
        pick_fields(
            {"a": 1},
            (),
        )
        == {}
    )
    table = to_markdown_table([{"when": datetime(2026, 1, 1, tzinfo=UTC), "v": 1.234}])
    assert table.splitlines()[2] == "| 2026-01-01T00:00:00.000Z | 1.234 |"
    assert format_number(999, notation="compact", locale="en-US") == "999"
    assert format_number(2_500_000, notation="compact", locale="en-US") == "2.5M"
    assert format_number(2_500_000, notation="compact") == "250万"
    assert format_number(1234.5, minimum_fraction_digits=2) == "1,234.50"
    assert (
        format_timestamp("2026-01-01T00:00:00Z", format="locale", time_zone="Asia/Tokyo")
        == "2026/1/1 9:00:00"
    )
    with pytest.raises(TakasumiBotToolkitValidationError):
        format_timestamp("not-a-date")
    with pytest.raises(TakasumiBotToolkitValidationError):
        format_timestamp(datetime(2026, 1, 1, tzinfo=UTC), format="bogus")
    with pytest.raises(TakasumiBotToolkitValidationError):
        format_number(1.0, maximum_fraction_digits=101)
    with pytest.raises(TakasumiBotToolkitValidationError):
        pick_fields(123, ("a",))


def test_stock_id_validation():
    assert validate_stock_id("AAA") == "AAA"
    for bad in ["", " ", "a{b", 123]:
        with pytest.raises(TakasumiBotToolkitValidationError):
            validate_stock_id(bad)


def test_config_effective_and_closed_client_rejected():
    import asyncio as _asyncio

    async def main():
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: json_response(TAX)))
        await client.aclose()
        with pytest.raises(Exception) as ei:
            BasicConfig(client=client)
        assert "closed" in str(ei.value)

    _asyncio.run(main())
    cfg = BasicConfig(timeout_ms=1234)
    assert cfg.effective_retry.jitter is True
    assert cfg.effective_logger is not None


async def test_list_stocks_helper():
    from conftest import STOCK_LIST
    from takasumibot_toolkit.stock import list_stocks

    kit = make_kit(lambda r: json_response(STOCK_LIST))
    entries = await list_stocks(kit)
    assert [e.id for e in entries] == ["AAA", "BBB"]
