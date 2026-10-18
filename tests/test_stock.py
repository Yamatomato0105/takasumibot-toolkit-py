import asyncio

import httpx
import pytest

from conftest import STOCK_LIST, json_response, make_kit
from takasumibot_toolkit import (
    TakasumiBotToolkitHttpError,
    TakasumiBotToolkitValidationError,
    get_recent_stock_history,
)
from takasumibot_toolkit.models import StockEntry
from takasumibot_toolkit.stock import PRICES_ORDER, slice_history, summarize_stocks


async def test_stock_builder_derivation(stock_handler):
    kit = make_kit(stock_handler, retry={"max_retries": 0})
    stock = kit.get_stock().id("AAA")
    assert (await stock.info()).name == "Alpha"
    assert await stock.price() == 120
    assert await stock.history() == [120, 110, 100]
    assert await stock.history(limit=2) == [120, 110]
    assert await stock.history(limit=0) == []
    assert await stock.dividend() == pytest.approx(10 / 120)
    # resource-level single load
    assert stock_handler.calls["count"] == 1
    assert PRICES_ORDER == "last_is_newest"


async def test_stock_equivalent_functions(stock_handler):
    kit = make_kit(stock_handler)
    assert (await kit.get_stock_info_by_id("AAA")).id == "AAA"
    assert await kit.get_stock_price_by_id("AAA") == 120
    assert await kit.get_stock_history_by_id("AAA", limit=1) == [120]
    assert await kit.get_stock_price_by_id("BBB") is None
    assert await kit.get_stock_history_by_id("BBB") == []


async def test_stock_not_found(stock_handler):
    kit = make_kit(stock_handler)
    stock = kit.get_stock().id("NOPE")
    assert await stock.resolve() is None
    assert await stock.is_valid() is False
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        await stock.info()
    assert ei.value.code == "STOCK_NOT_FOUND"


@pytest.mark.parametrize("bad", [-1, True, 1.0, "1"])
async def test_stock_limit_validation(stock_handler, bad):
    kit = make_kit(stock_handler)
    with pytest.raises(TakasumiBotToolkitValidationError) as ei:
        await kit.get_stock_history_by_id("AAA", limit=bad)
    assert ei.value.code == "INVALID_LIMIT"
    with pytest.raises(TakasumiBotToolkitValidationError):
        get_recent_stock_history([1, 2], bad)


def test_history_helpers_do_not_mutate():
    prices = [100, 110, 120]
    assert slice_history(prices, 10) == [120, 110, 100]
    assert get_recent_stock_history(prices) == [120, 110, 100]
    assert prices == [100, 110, 120]
    entry = StockEntry.model_validate(STOCK_LIST[0])
    summary = summarize_stocks([entry])
    assert summary[0]["price"] == 120 and summary[0]["points"] == [120, 110, 100]
    assert summary[0]["change_rate"] == pytest.approx(10 / 110)


async def test_get_stock_list_uses_ttl_cache():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return json_response(STOCK_LIST)

    kit = make_kit(handler, stock_cache={"ttl_ms": 60_000})
    assert await kit.get_stock_price_by_id("AAA") == 120
    assert await kit.get_stock_price_by_id("AAA") == 120
    assert calls["n"] == 1  # TTL cache shared
    await kit.get_stock_list()
    await kit.get_stock_list()
    assert calls["n"] == 1  # get_stock_list() is served from the same cache (NPM parity)
    await kit.get_stock_list(force_refresh=True)
    assert calls["n"] == 2  # force_refresh fetches and refreshes the cache
    assert await kit.get_stock_price_by_id("AAA") == 120
    assert calls["n"] == 2


async def test_stock_single_flight():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return json_response(STOCK_LIST)

    kit = make_kit(handler, stock_cache={"ttl_ms": 60_000})
    a, b = await asyncio.gather(kit.get_stock_price_by_id("AAA"), kit.get_stock_price_by_id("BBB"))
    assert (a, b) == (120, None)
    assert calls["n"] == 1


async def test_stock_resource_shares_concurrent_initial_load():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return json_response(STOCK_LIST)

    kit = make_kit(handler)
    stock = kit.get_stock().id("AAA")
    info, price, history = await asyncio.gather(stock.info(), stock.price(), stock.history())
    assert (info.id, price, history) == ("AAA", 120, [120, 110, 100])
    assert calls["n"] == 1


async def test_stock_resource_survives_waiter_cancellation():
    calls = {"n": 0}
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(request):
        calls["n"] += 1
        started.set()
        await release.wait()
        return json_response(STOCK_LIST)

    kit = make_kit(handler)
    stock = kit.get_stock().id("AAA")
    cancelled_waiter = asyncio.create_task(stock.info())
    await started.wait()
    remaining_waiter = asyncio.create_task(stock.price())
    cancelled_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_waiter
    release.set()
    assert await remaining_waiter == 120
    assert calls["n"] == 1


async def test_stock_resource_failed_shared_load_can_be_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, content=b"unavailable")
        return json_response(STOCK_LIST)

    kit = make_kit(handler)
    stock = kit.get_stock().id("AAA")
    results = await asyncio.gather(stock.info(), stock.price(), return_exceptions=True)
    assert all(isinstance(result, TakasumiBotToolkitHttpError) for result in results)
    assert calls["n"] == 1
    assert await stock.price() == 120
    assert calls["n"] == 2


async def test_stock_failure_not_cached():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(404, content=b"no")
        return json_response(STOCK_LIST)

    kit = make_kit(handler, stock_cache={"ttl_ms": 60_000}, retry={"max_retries": 0})
    with pytest.raises(TakasumiBotToolkitHttpError):
        await kit.get_stock_price_by_id("AAA")
    assert await kit.get_stock_price_by_id("AAA") == 120
    assert calls["n"] == 2
