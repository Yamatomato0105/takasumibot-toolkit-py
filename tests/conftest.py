import json

import httpx
import pytest

from takasumibot_toolkit import create_kit_client

STOCK_LIST = [
    {
        "name": "Alpha",
        "id": "AAA",
        "description": "first",
        "dividendAmount": 10,
        "dividendRate": 0.05,
        "prices": [100, 110, 120],
    },
    {
        "name": "Beta",
        "id": "BBB",
        "description": "second",
        "dividendAmount": 0,
        "dividendRate": 0.0,
        "prices": [],
    },
]

TAX = {
    "benefitTax": [{"threshold": 100.0, "rate": 0.1}],
    "companyBenefitTax": [],
    "debtInterestRate": 0.01,
    "depositInterestRate": 0.02,
    "depositTaxRate": 0.03,
    "exchangeTaxRate": 0.04,
    "giftTaxRate": 0.05,
    "idleTax": 100,
    "incomeTaxRate": 0.06,
    "tradeTaxRate": 0.07,
}


def make_kit(handler, **kwargs):
    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://api.takasumibot.com")
    kwargs.setdefault("retry", {"max_retries": 0})
    return create_kit_client(client=client, **kwargs)


def json_response(payload, status=200, headers=None):
    return httpx.Response(status, content=json.dumps(payload).encode(), headers=headers or {})


@pytest.fixture
def stock_handler():
    calls = {"count": 0, "urls": []}

    def handler(request):
        calls["count"] += 1
        calls["urls"].append(str(request.url))
        return json_response(STOCK_LIST)

    handler.calls = calls
    return handler
