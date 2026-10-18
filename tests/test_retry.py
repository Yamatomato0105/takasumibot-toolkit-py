import httpx
import pytest

import takasumibot_toolkit.client as client_mod
from conftest import TAX, json_response, make_kit
from takasumibot_toolkit import (
    RetryConfig,
    TakasumiBotToolkitHttpError,
    TakasumiBotToolkitRetryLimitError,
    compute_delay_ms,
)


@pytest.fixture(autouse=True)
def fast_sleep(monkeypatch):
    async def fake(ms):
        return None

    monkeypatch.setattr(client_mod, "_sleep_ms", fake)


def test_compute_delay_backoff_and_jitter_floor():
    cfg = RetryConfig(initial_delay_ms=100, max_delay_ms=5000, jitter=False)
    assert compute_delay_ms(cfg, attempt=0) == 100
    assert compute_delay_ms(cfg, attempt=2) == 400
    cfg_j = RetryConfig(initial_delay_ms=1000, max_delay_ms=5000, jitter=True)

    class Rng:
        def random(self):
            return 0.0

    assert compute_delay_ms(cfg_j, attempt=0, rng=Rng()) == 500
    assert compute_delay_ms(cfg, attempt=0, retry_after_s=1.0) == 1000


async def test_retry_then_success():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, content=b"oops")
        return json_response(TAX)

    kit = make_kit(handler, retry={"max_retries": 3, "initial_delay_ms": 0, "jitter": False})
    tax = await kit.get_tax_info()
    assert tax.idle_tax == 100 and calls["n"] == 3


async def test_retry_limit():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, content=b"oops")

    kit = make_kit(
        handler,
        retry={"max_retries": 2, "initial_delay_ms": 100, "max_delay_ms": 5000, "jitter": False},
    )
    with pytest.raises(TakasumiBotToolkitRetryLimitError) as ei:
        await kit.get_tax_info()
    err = ei.value
    assert calls["n"] == 3
    assert err.attempts == 3 and err.delays_ms == (100, 200)
    assert isinstance(err.last_error, TakasumiBotToolkitHttpError)
    assert err.__cause__ is err.last_error


async def test_non_retryable_status_not_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(400, content=b"bad")

    kit = make_kit(handler, retry={"max_retries": 3, "initial_delay_ms": 0})
    with pytest.raises(TakasumiBotToolkitHttpError):
        await kit.get_tax_info()
    assert calls["n"] == 1


async def test_network_error_retried():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("down", request=request)
        return json_response(TAX)

    kit = make_kit(handler, retry={"max_retries": 2, "initial_delay_ms": 0, "jitter": False})
    assert (await kit.get_tax_info()).idle_tax == 100
    assert calls["n"] == 2
