"""Live smoke tests against the real API (skipped by default).

``pyproject.toml`` sets ``addopts = "-ra -m 'not live'"``, so these only run
when explicitly requested:

    pytest -m live

They exist because the OpenAPI document and the live API can disagree:
mocked tests alone cannot catch that class of drift.
Requests are kept to a minimum (one per endpoint, a 3-item sample for the two
regressions) and every call is spaced out a little.
"""

import asyncio
import os

import httpx
import pytest

from takasumibot_toolkit import (
    TakasumiBotToolkitHttpError,
    create_kit_client,
)
from takasumibot_toolkit.models import (
    CompanyDetailResponse,
    CompanyHistoryEntry,
    CompanyListEntry,
    DiscordUserSearchResponse,
    GiftResponse,
    HistoryEntry,
    ProfileResponse,
    RankingEntry,
    ShardResponse,
    StatisticsResponse,
    StatusEntry,
    StockEntry,
    TaxResponse,
)

pytestmark = pytest.mark.live

SAMPLE_SIZE = 3
POLITENESS_DELAY_S = 0.5


@pytest.fixture
async def kit():
    client = create_kit_client(timeout_ms=30_000)
    try:
        yield client
    finally:
        await client.aclose()


async def _breath() -> None:
    await asyncio.sleep(POLITENESS_DELAY_S)


def _skip_if_unreachable(exc: BaseException) -> None:
    """Skip instead of failing when the API cannot be reached at all."""
    cause: BaseException | None = exc
    seen: set[int] = set()
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, (httpx.ConnectError, httpx.ConnectTimeout, OSError)):
            pytest.skip(f"live API unreachable: {cause!r}")
        cause = cause.__cause__ or cause.__context__


async def test_live_all_endpoints_return_documented_models(kit):
    checks = [
        (kit.get_tax_info, TaxResponse),
        (kit.get_shard_info, ShardResponse),
        (kit.get_statistics_info, StatisticsResponse),
    ]
    for call, model in checks:
        try:
            result = await call()
        except Exception as exc:  # pragma: no cover - network dependent
            _skip_if_unreachable(exc)
            raise
        assert isinstance(result, model)
        await _breath()

    list_checks = [
        (kit.get_ranking, RankingEntry),
        (kit.get_company_list, CompanyListEntry),
        (kit.get_stock_list, StockEntry),
        (kit.get_status, StatusEntry),
    ]
    for call, model in list_checks:
        try:
            result = await call()
        except Exception as exc:  # pragma: no cover - network dependent
            _skip_if_unreachable(exc)
            raise
        assert isinstance(result, list)
        assert all(isinstance(item, model) for item in result)
        await _breath()


async def test_live_user_endpoints_parse(kit):
    try:
        ranking = await kit.get_ranking()
    except Exception as exc:  # pragma: no cover - network dependent
        _skip_if_unreachable(exc)
        raise
    assert ranking, "expected a non-empty ranking"
    for entry in ranking[:SAMPLE_SIZE]:
        profile = await kit.get_profile_by_id(entry.id)
        assert isinstance(profile, ProfileResponse)
        history = await kit.get_history_by_id(entry.id)
        assert all(isinstance(item, HistoryEntry) for item in history)
        await _breath()


async def test_live_h1_discord_user_search_accepts_null_accent_color(kit):
    """H-1: ``accentColor`` is null for most users on the live API."""
    try:
        ranking = await kit.get_ranking()
    except Exception as exc:  # pragma: no cover - network dependent
        _skip_if_unreachable(exc)
        raise
    parsed = 0
    for entry in ranking[:SAMPLE_SIZE]:
        try:
            user = await kit.get_discord_user_by_name(entry.username)
        except TakasumiBotToolkitHttpError as exc:
            if exc.status == 404:
                continue
            raise
        assert isinstance(user, DiscordUserSearchResponse)
        assert user.accent_color is None or isinstance(user.accent_color, int)
        parsed += 1
        await _breath()
    assert parsed, "no username resolved; cannot verify H-1"


async def test_live_h2_company_lookup_by_owner_id(kit):
    """H-2: searching by the owner's Discord id omits ``employees``."""
    try:
        companies = await kit.get_company_list()
    except Exception as exc:  # pragma: no cover - network dependent
        _skip_if_unreachable(exc)
        raise
    assert companies, "expected a non-empty company list"
    for entry in companies[:SAMPLE_SIZE]:
        by_company_id = await kit.get_company_by_id(entry.id)
        assert isinstance(by_company_id, CompanyDetailResponse)
        assert by_company_id.employees is not None, "company id lookup lost employees"
        await _breath()

        by_owner_id = await kit.get_company_by_id(entry.owner_id)
        assert isinstance(by_owner_id, CompanyDetailResponse)
        assert by_owner_id.employees is None or isinstance(by_owner_id.employees, list)
        await _breath()

        history = await kit.get_company_history_by_id(entry.id)
        assert all(isinstance(item, CompanyHistoryEntry) for item in history)
        await _breath()


async def test_live_stock_derivation_and_gift_404(kit):
    try:
        entries = await kit.get_stock_list()
    except Exception as exc:  # pragma: no cover - network dependent
        _skip_if_unreachable(exc)
        raise
    assert entries, "expected a non-empty stock list"
    stock = kit.get_stock().id(entries[0].id)
    assert (await stock.info()).id == entries[0].id
    assert await stock.price() == (entries[0].prices[-1] if entries[0].prices else None)
    await _breath()

    with pytest.raises(TakasumiBotToolkitHttpError) as ei:
        await kit.get_gift_info("zzzzzzzzzz")
    assert ei.value.status == 404
    assert ei.value.api_message, "expected a best-effort api_message on 404"


async def test_live_unknown_fields_are_only_kept_not_required(kit):
    """The models tolerate unknown fields (extra="allow"); nothing is required."""
    try:
        tax = await kit.get_tax_info()
    except Exception as exc:  # pragma: no cover - network dependent
        _skip_if_unreachable(exc)
        raise
    assert isinstance(tax.model_extra, dict)
    dumped = tax.model_dump(by_alias=True)
    assert TaxResponse.model_validate(dumped) == tax
    await _breath()


async def test_live_gift_response_shape_is_optional(kit):
    """``GiftResponse`` is only exercised when a real gift code is supplied.

    Set ``TAKASUMIBOT_LIVE_GIFT_CODE`` to run this check; it is skipped
    otherwise so the suite never needs a secret.
    """
    code = os.environ.get("TAKASUMIBOT_LIVE_GIFT_CODE")
    if not code:
        pytest.skip("set TAKASUMIBOT_LIVE_GIFT_CODE to exercise GET /v3/gift/{id}")
    gift = await kit.get_gift_info(code)
    assert isinstance(gift, GiftResponse)
