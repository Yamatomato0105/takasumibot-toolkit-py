[English](./README.en.md) | [日本語](./README.ja.md)

# takasumibot-toolkit

Async Python client SDK for the **[TakasumiBOT public API](https://api.takasumibot.com/docs)** (`TakasumiBOT API 3.0.0-beta`).

- Distribution name: `takasumibot-toolkit`
- Import name: `takasumibot_toolkit`
- Python: `>=3.11` (async API only, `py.typed` included)
- Runtime dependencies: `httpx` and `pydantic` only
- Exponential backoff retries, timeouts, structured errors, opt-in stock caching
- Arbitrary-precision `int` for every `format: int64` field (no precision loss) and timezone-aware `datetime` for `date-time` fields
- **No authentication** is required (the API is public), and the WebSocket endpoint (`/v3/realtime/`) is **not** implemented.

The OpenAPI document shipped in this repository (`TakasumiBOT-OpenAPI-Document.json`) is the single source of truth: every method maps to one declared HTTP path, and nothing else is called.

> The WebSocket endpoint `/v3/realtime/` is intentionally **not** implemented.

## Installation

```bash
pip install --pre takasumibot-toolkit
# for development of this repository:
pip install -e ".[dev]"
```

This package is an beta release; backward-incompatible changes may occur.

## Quick start (async)

```python
import asyncio

from takasumibot_toolkit import create_kit_client


async def main() -> None:
    async with create_kit_client() as kit:
        tax = await kit.get_tax_info()
        ranking = await kit.get_ranking()

        print(tax.income_tax_rate)  # float
        if ranking:
            profile = await kit.get_profile_by_id(ranking[0].id)
            print(profile.assets)  # int


asyncio.run(main())
```

Only an async API is provided (no sync facade). Wrap calls with `asyncio.run(...)` from synchronous code, as above. Everything is available from a single client instance; there is no global state.

## Configuration (`BasicConfig`)

`create_kit_client()` accepts either a `BasicConfig` or keyword overrides (keywords win). `BasicConfig` deliberately has **no `base_url` field**.

```python
from takasumibot_toolkit import BasicConfig, create_kit_client

kit = create_kit_client(
    BasicConfig(
        timeout_ms=10_000,
        headers={"user-agent": "my-bot/1.0.0"},
        retry={
            "max_retries": 3,
            "initial_delay_ms": 300,
            "max_delay_ms": 5_000,
            "backoff_factor": 2.0,
            "jitter": True,
        },
        stock_cache={"ttl_ms": 60_000},  # disabled unless passed
    )
)

# Keyword overrides:
kit = create_kit_client(timeout_ms=3_000)
# Stock-list cache with the default 60 s TTL, or an explicit TTL in ms:
kit = create_kit_client(stock_cache={})
kit = create_kit_client(stock_cache={"ttl_ms": 30_000})
```

| Field | Default | Notes |
|---|---|---|
| `timeout_ms` | `10000` | Per-attempt timeout (each retry gets a fresh timeout; there is no overall deadline). Must be an `int > 0`. |
| `headers` | `None` | Extra headers; user headers override the defaults (`accept`, `user-agent`). Names must be RFC 7230 tokens and values may only contain printable ASCII, SP and HTAB — anything else raises `TakasumiBotToolkitConfigError` at construction time, not at send time. |
| `retry` | see [Retries](#retries) | A `RetryConfig` or a plain `dict`; unknown keys raise `TakasumiBotToolkitConfigError(code="UNKNOWN_CONFIG_KEY")`. `backoff_factor` must be finite and `>= 1.0`. |
| `client` | `None` | Caller-owned `httpx.AsyncClient` to inject. The kit never closes it (`kit.owns_http_client is False`). `timeout_ms` and `follow_redirects` are still applied per request, so this configuration wins over the injected client's own settings. |
| `logger` | `None` | Object with `debug`/`info`/`warning`/`error` methods (e.g. `logging.getLogger(...)` or a `logging.LoggerAdapter`). |
| `follow_redirects` | `False` | When `False`, a 3xx response raises `TakasumiBotToolkitHttpError`. When enabled, custom headers can be forwarded to the redirect target; do not use sensitive custom headers with redirect following, especially across hosts or from HTTPS to HTTP. |
| `stock_cache` | `None` | Client-wide stock-list cache (NPM `stockCache`). `None`/`False` disables it; `{}` enables it with the default TTL of 60 000 ms; `{"ttl_ms": n}` (or `StockCacheConfig(ttl_ms=n)`) sets the TTL in **milliseconds** (an `int > 0`). `True`, other types, a non-positive TTL or an unknown key raise `TakasumiBotToolkitConfigError`. |

`DEFAULT_RETRY_CONFIG` and `DEFAULT_STOCK_CACHE_TTL_MS` expose the defaults.

### Base URL (`base_url`)

The base URL defaults to the OpenAPI `servers[0].url` (`https://api.takasumibot.com`) and can only be changed with the `TAKASUMIBOT_BASE_URL` environment variable (for example, `TAKASUMIBOT_BASE_URL=http://localhost:8000` for a local server).
Use HTTPS when configured headers contain sensitive data. Restrict HTTP base URLs to local testing because headers are sent without an HTTPS upgrade.

Resolution order:

1. `os.environ["TAKASUMIBOT_BASE_URL"]` (ignored when empty/blank)
2. `servers[0].url` of the OpenAPI document (`https://api.takasumibot.com/`)

Non-`http(s)` URLs and URLs with a path/query/fragment raise `TakasumiBotToolkitConfigError`. Trailing slashes are removed from both the base URL and the path, so requests go to `https://api.takasumibot.com/v3/tax`. The resolved value is read-only:

```python
kit.base_url  # e.g. "https://api.takasumibot.com"
```

### Timeout

The timeout applies **per attempt**, not to the whole (retried) call. To bound the total time, wrap the call yourself:

```python
import asyncio

from takasumibot_toolkit import TakasumiBotToolkitTimeoutError

try:
    async with asyncio.timeout(5):
        tax = await kit.get_tax_info()
except (TimeoutError, TakasumiBotToolkitTimeoutError):
    ...  # asyncio.timeout raises the builtin; a per-attempt timeout raises ours
```

`asyncio.timeout()` raises the **builtin** `TimeoutError`; a per-attempt httpx timeout becomes `TakasumiBotToolkitTimeoutError` (retryable). They are unrelated classes, so catch both when you want either.

### Logging

Pass any `LoggerLike` (a stdlib `logging.Logger` or `logging.LoggerAdapter` works). Send/receive events are logged at `debug`, each retry at `warning`, and retry exhaustion at `error`. Messages are in English and contain the request URL, so they stay useful with a plain log format:

```text
DEBUG    http request GET https://api.takasumibot.com/v3/tax
DEBUG    http response GET https://api.takasumibot.com/v3/tax status=200
WARNING  retrying request GET https://api.takasumibot.com/v3/tax
ERROR    retry limit exceeded for GET https://api.takasumibot.com/v3/tax
```

Structured context (`method`, `url`, `status`, `attempt`, `delay_ms`, `reason`, `attempts`) is passed as `extra=` to stdlib loggers, so it is available on the `LogRecord`. Notes:

- For a `logging.LoggerAdapter`, the adapter's own `extra` is merged with the toolkit context. If the adapter uses the stock `process()` (which replaces `extra`), the toolkit logs through the wrapped logger so nothing is dropped; a custom `process()` stays in charge.
- Logging is best-effort: an exception raised by your logger is swallowed and never turns into a failed request.

## Endpoints

All 13 HTTP paths from the OpenAPI document, and no others:

| Method | Path | HTTP | Returns |
|---|---|---|---|
| `get_gift_info(id)` | `/v3/gift/{id}` | GET | `GiftResponse` |
| `get_tax_info()` | `/v3/tax/` | GET | `TaxResponse` |
| `get_shard_info()` | `/v3/shard/` | GET | `ShardResponse` |
| `get_statistics_info()` | `/v3/statistics/` | GET | `StatisticsResponse` |
| `get_history_by_id(id)` | `/v3/history/{id}` | GET | `list[HistoryEntry]` |
| `get_profile_by_id(id)` | `/v3/profile/{id}` | GET | `ProfileResponse` |
| `get_ranking()` | `/v3/ranking/` | GET | `list[RankingEntry]` |
| `get_company_list()` | `/v3/companylist/` | GET | `list[CompanyListEntry]` |
| `get_company_by_id(id)` | `/v3/company/{id}` | GET | `CompanyDetailResponse` |
| `get_stock_list()` | `/v3/stock/` | GET | `list[StockEntry]` |
| `get_discord_user_by_name(name)` | `/v3/discord/usersearch/{name}` | GET | `DiscordUserSearchResponse` |
| `get_company_history_by_id(id)` | `/v3/companyHistory/{id}` | GET | `list[CompanyHistoryEntry]` |
| `get_status()` | `/v3/status/` | GET | `list[StatusEntry]` |

```python
# Gift codes are validated client-side with /^[A-Za-z0-9]{10}$/ before any request.
gift = await kit.get_gift_info("Abc123Xyz0")
```

Notes:

- Path arguments are trimmed first, with the same whitespace set as JavaScript's `String.prototype.trim()` (so `" 123 "` requests `/v3/profile/123`, exactly like the NPM package), then percent-encoded with `quote(value, safe="")`; trailing slashes of paths are removed. A blank value raises `TakasumiBotToolkitValidationError(code="EMPTY_ARGUMENT")`. Path arguments are validated for type, emptiness, braces and bare `.`/`..` segments — their format is **not** checked (the OpenAPI document declares no patterns), except for gift codes (next item). Rejecting `.`/`..` matters because `quote()` does not escape dots: without it, `get_profile_by_id("..")` would be normalised to a request against `/v3` and leave the documented path. Anything else stays inside its path (`a/../../b` → `/v3/profile/a%2F..%2F..%2Fb`), so only the 13 documented paths are ever requested.
- `get_gift_info(id)` requires exactly 10 ASCII letters/digits (`[A-Za-z0-9]{10}`, the NPM `requireGiftId` rule). Anything else — including a code with surrounding spaces, which the NPM package does not trim either — raises `TakasumiBotToolkitValidationError(code="INVALID_GIFT_ID", field="id")` **without sending a request**.
- Array responses keep the server's order and return `list[T]`.
- The API may represent "no data" as `404` **or** possibly as `200` with an empty/null body; a `404` is always raised as `TakasumiBotToolkitHttpError`, while `200 + null` raises `TakasumiBotToolkitResponseParseError` (it is not normalised to `[]`).
- `get_company_by_id()` accepts a company id **or** the owner's Discord user id. The live API omits the whole `employees` key for owner-id lookups, so `CompanyDetailResponse.employees` is `list[CompanyEmployeeEntry] | None` (`None` = not returned, `[]` = returned and empty).

> `/v3/realtime/` is a WebSocket endpoint and is out of scope for this package.

## Stock (client-side derivation)

There is **no** `/v3/stock/{id}` endpoint in the OpenAPI document, so single stocks (`info`, `price`, `history`) are derived client-side from `GET /v3/stock/`. Each `StockResource` loads the list once and shares an in-progress load among concurrent calls; without the client-wide cache, the first use of each resource makes a request:

```python
stocks = await kit.get_stock_list()

# Builder (lazy: nothing happens until you await a terminal method)
stock = kit.get_stock().id("JTTI")
entry = await stock.info()          # StockEntry
price = await stock.price()         # latest price = prices[-1]; None if prices is empty
history = await stock.history()     # newest-first list[int]
recent = await stock.history(limit=3)

# Equivalent client methods:
entry = await kit.get_stock_info_by_id("JTTI")
price = await kit.get_stock_price_by_id("JTTI")
recent = await kit.get_stock_history_by_id("JTTI", limit=3)
```

Rules:

- Stock ids are trimmed like other path arguments (`" JTTI "` → `"JTTI"`).
- `PRICES_ORDER = "last_is_newest"`: `prices` is ascending in time (its last element is the newest price), and `history()` is always returned **newest-first**.
- `price()` returns the newest price (`prices[-1]`), or `None` when `prices` is empty (not an error).
- `limit`: omitted → all; `0` → `[]`; a positive `int` → the newest *n*; negative, boolean or non-int values raise `TakasumiBotToolkitValidationError`.
- An unknown stock id raises `TakasumiBotToolkitValidationError(code="STOCK_NOT_FOUND")` (never retried; `resolve()` returns `None` instead).

### Forward compatibility

`StockEntry.id` is declared as a 10-value enum in the OpenAPI document, but a response is validated as a whole. With a strict `Literal`, the day the API adds an eleventh company the entire payload would fail validation and `get_stock_list()` — and every Stock helper built on it — would break for every caller at once.

So OpenAPI `enum` properties (`GiftResponse.type`/`status`, `StockEntry.id`) are `LooseEnum` fields, like the NPM `looseEnum`: any non-empty string is accepted. The documented values are exported as `GIFT_TYPES`, `GIFT_STATUSES` and `STOCK_IDS` (with the `GiftType`/`GiftStatus`/`StockId` `Literal` aliases), and `is_known_value()` narrows a value to the documented set:

```python
from takasumibot_toolkit import STOCK_IDS, is_known_value

stocks = await kit.get_stock_list()
documented = [s for s in stocks if is_known_value(s.id, STOCK_IDS)]
```

### Stock cache

Each `StockResource` loads the list at most once, including when multiple methods are called concurrently. The client-wide cache is **disabled by default** (without `stock_cache`, the first call on each resource goes to the network). Enable it per client:

```python
kit = create_kit_client(stock_cache={})                 # enabled, TTL 60 s (DEFAULT_STOCK_CACHE_TTL_MS)
other = create_kit_client(stock_cache={"ttl_ms": 30_000})  # enabled, TTL 30 s
uncached = create_kit_client(stock_cache=False)         # disabled (default)
```

- Scope: one client instance (two clients never share a cache).
- When `stock_cache` is enabled, **`get_stock_list()` is served from the same cache** as the stock helpers, as in the NPM package (single-flight: concurrent loads share one request).
- `get_stock_list(force_refresh=True)` always fetches and refreshes the cache (after any load already in flight, so that older result cannot overwrite the refreshed list).
- Failures are never cached. No retries are added beyond the client's retry policy.
- The cache keeps its own deep copies of the entries and returns new ones on every call, so mutating a list you received — or an entry's `prices` — cannot corrupt the cache or later results. (`StockEntry.prices` itself is still a mutable `list`, like every list field of a frozen pydantic model.)

## Errors

All errors derive from `TakasumiBotToolkitError` (8 public classes, named like the NPM package). Only these full names are exported: short aliases such as `TimeoutError` and `ValidationError` were dropped because they shadow the builtin `TimeoutError` (and `pydantic.ValidationError`), which silently breaks `except TimeoutError:` around `asyncio.timeout(...)`. `TakasumiBotToolkitTimeoutError` is deliberately **not** a subclass of the builtin, so catch both explicitly if you want them together: `except (TimeoutError, TakasumiBotToolkitTimeoutError):`.

| Class | Trigger | `retryable` |
|---|---|---|
| `TakasumiBotToolkitError` | Base class for all toolkit errors | `False` (default) |
| `TakasumiBotToolkitConfigError` | Invalid config, invalid base URL, closed client | `False` |
| `TakasumiBotToolkitValidationError` | Invalid arguments (path params, stock limit, unknown stock id, bad gift id, helpers) | `False` |
| `TakasumiBotToolkitHttpError` | Non-2xx status (incl. 404 and unfollowed 3xx) | `True` for status in `{429, 500, 502, 503, 504}` |
| `TakasumiBotToolkitNetworkError` | Connection/transport failure | usually `True` |
| `TakasumiBotToolkitTimeoutError` | Per-attempt timeout | `True` |
| `TakasumiBotToolkitRetryLimitError` | Retries exhausted (`attempts`, `delays_ms`, `last_error`) | `False` |
| `TakasumiBotToolkitResponseParseError` | Empty/invalid JSON body or schema mismatch (`issues`, `raw_body`) | `False` |

```python
from takasumibot_toolkit import (
    TakasumiBotToolkitError,
    TakasumiBotToolkitHttpError,
    TakasumiBotToolkitRetryLimitError,
    TakasumiBotToolkitValidationError,
)

try:
    await kit.get_gift_info("nope")
except TakasumiBotToolkitValidationError as exc:
    print(exc.field, exc.code)  # "id" "INVALID_GIFT_ID"
except TakasumiBotToolkitHttpError as exc:
    print(exc.status, exc.request_id)
except TakasumiBotToolkitRetryLimitError as exc:
    print(exc.attempts, exc.last_error)  # exc.last_error holds the final Http/Network/Timeout error
except TakasumiBotToolkitError as exc:
    if exc.retryable:
        ...  # an outer retry may help
```

`TakasumiBotToolkitHttpError` carries `status` (also aliased as `status_code`), `method`, `url`, and best-effort `api_code`/`api_message`/`request_id` extracted from the response (the OpenAPI document defines no error schema, so these may be `None`). Like the NPM error it also keeps the whole body as `raw_body` (`""` for an empty body), the raw `Retry-After` header as `retry_after` and the same allowlist of response `headers` (`content-type`, `retry-after`, `x-request-id`, `x-requestid`, `request-id`, `x-takasumibot-request-id`, `x-ratelimit-remaining`). A non-2xx response whose body is not JSON stays a `TakasumiBotToolkitHttpError`; only **2xx** responses can raise `TakasumiBotToolkitResponseParseError` (which keeps `raw_body` too).

Validation error codes follow the NPM package: `EMPTY_ARGUMENT` (blank argument), `INVALID_GIFT_ID`, `INVALID_STOCK_ID`, `STOCK_NOT_FOUND`, `UNSAFE_PATH_PARAMETER`, `INVALID_TYPE`, `INVALID_LIMIT`, `INVALID_ARGUMENT`, `INVALID_PAGE`, `INVALID_PAGE_SIZE`.

Every error class can be pickled, copied and deep-copied, so errors survive `multiprocessing`, Celery, pytest-xdist and `ProcessPoolExecutor` boundaries (`__cause__`/`__context__` are not carried over, as with stdlib exceptions; `RetryLimitError.last_error` is, and is restored as `__cause__`).

## Retries

Defaults (the NPM `DEFAULT_RETRY_CONFIG`, exported as `DEFAULT_RETRY_CONFIG`): `max_retries=3` (retries, so up to 4 total attempts), `initial_delay_ms=300`, `max_delay_ms=5000`, `backoff_factor=2.0`, `jitter=True`.

```python
kit = create_kit_client(retry={"max_retries": 0})  # disable retries
```

- Retried: network errors, timeouts, and HTTP `429/500/502/503/504`. This includes `httpx.RemoteProtocolError` ("Server disconnected without sending a response"), a transient keep-alive failure — safe to retry because every request is an idempotent GET.
- Not retried: other 4xx, validation errors, config errors, response parse errors, `httpx.LocalProtocolError`, too many redirects and proxy errors. Cancellation (`asyncio.CancelledError`) is never caught or retried.
- Delay = `min(initial * factor**attempt, max_delay)` with 50–100% jitter, rounded up with `ceil` (at least 1 ms when the base is `> 0`). Large `attempt` numbers are clipped instead of overflowing.
- A `Retry-After` header (seconds or HTTP-date) takes precedence and is clipped to `max_delay_ms * 3` — with the defaults that is **15 seconds**, so a server asking for `Retry-After: 60` is retried after 15 s. Raise `max_delay_ms` if you need to honour long server-imposed waits.
- With `max_retries=0` the original error is raised as-is; otherwise exhaustion raises `TakasumiBotToolkitRetryLimitError` chained from the last error (with `attempts`, `delays_ms` and `last_error`).

## Helpers

Pure, side-effect-free offline helpers exported from the top level and, like the NPM `kit.helpers`, as a read-only namespace on every client (`kit.helpers.format_number` is `format_number`). Their output matches the NPM package (`Intl.NumberFormat`/`Intl.RelativeTimeFormat` behaviour is reproduced for `ja-JP` — the default, `DEFAULT_LOCALE` — and `en-US`):

| Helper | Description |
|---|---|
| `truncate(text, max_length, *, ellipsis="...")` | Truncate by code points, ellipsis included in the limit. |
| `pick_fields(obj, keys)` / `omit_fields(obj, keys)` | Keep/drop canonical (`snake_case`) fields of **one** model, mapping, dataclass or namedtuple. Like the NPM package, a list is rejected (apply it per item) and `pick_fields` returns the keys in the order of `keys`. `keys` must be a sequence of `str`: a bare `"id"` raises `TakasumiBotToolkitValidationError` instead of being read as `"i"`, `"d"`. |
| `to_markdown_table(rows, *, headers=None, align=None)` | Markdown table (`\| a \| b \|` / `\| --- \| --- \|`). Every row must be a record (model, mapping, dataclass, …). Columns default to the keys of the **first** row; `align` (`"left"`/`"center"`/`"right"` per column) produces `:---`/`:---:`/`---:`. `\|` is escaped and newlines become `<br>` in cells **and** headers; `None` is an empty cell, floats use `repr`, datetimes the ISO form of `format_timestamp`; empty `rows` or zero columns give `""`. |
| `format_number(value, *, locale=None, minimum_fraction_digits=None, maximum_fraction_digits=None, notation=None, compact_display=None)` | `Intl.NumberFormat` output: `1234.5678` → `1,234.568`, `notation="compact"` → `1.2万`/`1.5億` (`ja-JP`) or `1.2K` (`en-US`). Python ints of any size are exact; `NaN`/`Inf`/`None`/`bool` are rejected and `-0` is never rendered. |
| `format_timestamp(value, *, format="iso", locale=None, time_zone=None, reference=None)` | `value` is a tz-aware `datetime`, an ISO string or **epoch milliseconds** (`int`/`float`). `iso` → `2024-01-01T00:00:00.000Z`, `locale` with `time_zone="Asia/Tokyo"` → `2024/1/1 9:00:00`, `relative` → `5 分前` / `5 minutes ago`. `reference` (Python only) fixes "now" for `relative`. |
| `paginate(items, page=1, page_size=25)` | 1-based paging returning `Page[T]` (`items`, `page`, `page_size`, `total_items`, `total_pages`, `has_next`, `has_prev`). Out-of-range pages are empty; `items` must be a real sequence (mappings and `str`/`bytes` are rejected). |
| `get_recent_stock_history(entry_or_prices, limit=None)` | Newest-first stock history with the stock limit rules. |

All helpers are pure and offline, and every invalid argument raises `TakasumiBotToolkitValidationError(code="INVALID_ARGUMENT", field=...)` (or `INVALID_PAGE` / `INVALID_PAGE_SIZE` for `paginate` bounds).

```python
from takasumibot_toolkit import (
    format_number,
    format_timestamp,
    get_recent_stock_history,
    omit_fields,
    paginate,
    pick_fields,
    to_markdown_table,
    truncate,
)

truncate("hello world", 8)  # "hello..." (the ellipsis counts towards max_length)
pick_fields({"a": 1, "b": 2, "c": 3}, ["a", "c"])  # {"a": 1, "c": 3}
omit_fields({"a": 1, "b": 2, "c": 3}, ["b"])  # {"a": 1, "c": 3}

to_markdown_table([
    {"name": "JTTI", "price": 103},
    {"name": "KENTAI", "price": None},
])
# | name | price |
# | --- | --- |
# | JTTI | 103 |
# | KENTAI |  |

to_markdown_table([{}])  # "" — no columns means no renderable table

format_number(1234567)  # "1,234,567" (ja-JP by default)
format_timestamp("2024-01-01T00:00:00Z")  # "2024-01-01T00:00:00.000Z"

page = paginate([1, 2, 3, 4, 5], 1, 2)
# Page(items=[1, 2], page=1, page_size=2, total_items=5, total_pages=3, has_next=True, has_prev=False)
paginate([1, 2, 3], 10, 2).items  # [] — out-of-range pages are empty, not errors

get_recent_stock_history([100, 101, 102, 103], 2)  # [103, 102]
```

## Testing your app

Inject an `httpx.AsyncClient` backed by `httpx.MockTransport` — no real network access and no extra mocking library is needed:

```python
import asyncio
import httpx

from takasumibot_toolkit import create_kit_client


def handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=[])

async def main():
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        kit = create_kit_client(client=client, retry={"max_retries": 0})
        ranking = await kit.get_ranking()

asyncio.run(main())
```

## Data model notes

- Models use `snake_case` field names with the OpenAPI name as the alias (`populate_by_name=True`); `model_dump(by_alias=True)` restores the raw JSON keys. All documented properties are required — a missing key or a type mismatch raises `TakasumiBotToolkitResponseParseError`.
- Two documented properties are exceptions, because the live API disagrees with the OpenAPI document (both are declared as overrides in `scripts/generate_models.py`, not hand-edited into the generated module): `DiscordUserSearchResponse.accent_color` is `int | None` (Discord returns `null` when no accent colour is set) and `CompanyDetailResponse.employees` is `list[CompanyEmployeeEntry] | None = None` (the key is absent when you look a company up by its owner's Discord id).
- Unknown fields are kept (`extra="allow"`, visible in `model_extra`).
- OpenAPI `enum` properties (`GiftResponse.type`/`status`, `StockEntry.id`) are `LooseEnum` fields, like the NPM `looseEnum`: any non-empty string is accepted, so a value the API adds later does not break the whole response. The documented values are exported as `GIFT_TYPES`, `GIFT_STATUSES` and `STOCK_IDS` (with the `GiftType`/`GiftStatus`/`StockId` `Literal` aliases); `is_known_value(entry.id, STOCK_IDS)` narrows a value to the documented set.
- `int64`/`int32` map to Python `int` (arbitrary precision, no precision loss and no range check); `number` maps to `float` (finite only); `date-time` maps to a timezone-aware `datetime` (naive values are rejected).
- `ShardDataEntry` counts are strings because the OpenAPI document declares them as `string`; convert with `int(...)` if you need numbers.

## Known limitations / unknowns

- `prices` carries **no timestamps**, so `history()` cannot say *when* a price was recorded; the "last is newest" order is a confirmed convention (`PRICES_ORDER`), not an OpenAPI declaration.
- Error response bodies are not defined in the OpenAPI document; error details are extracted best-effort and may be `None`.
- Units are not converted (tax rates, `ping`, CPU/memory usage, dividend units are returned exactly as the API sends them).
- No WebSocket client (`/v3/realtime/`), no authentication, no sync API, no client-side rate limiting.
- Models are frozen (`frozen=True`) but **not hashable** when they contain a list field (e.g. `StockEntry.prices`, `CompanyDetailResponse.employees`), so they cannot be used as `dict` keys or in a `set`.
- A client is bound to the event loop that created it. Reusing one across two `asyncio.run()` calls raises `RuntimeError: Event loop is closed` from httpx — create a client per loop (`async with create_kit_client() as kit: ...`).
- `format_number` and `format_timestamp(format="locale"/"relative")` reproduce Intl output for `ja`/`ja-JP` and `en`/`en-US` only; other locales raise `TakasumiBotToolkitValidationError(field="locale")` instead of guessing.
- `format_timestamp(..., time_zone="Asia/Tokyo")` needs an IANA time-zone database. Linux and macOS ship one; on Windows install the `tzdata` package yourself (the runtime dependencies are `httpx` and `pydantic` only).
- `format_timestamp(format="locale")` without `time_zone` uses the host's local zone. On Windows the C runtime cannot convert instants before 1970 or after the year 3000 to local time, so those raise `TakasumiBotToolkitValidationError(field="value")`; pass `time_zone=` to format them.

## Development

```bash
pip install -e ".[dev]"
make check    # ruff + format check + mypy + pytest + doctests + gen-check + openapi-check
make gen      # regenerate src/takasumibot_toolkit/models/generated/models.py
make live     # smoke tests against the real API (network required)
make sweep    # sample the real API and report schema drift (network required)
make build    # python -m build + twine check
```

Generated models are committed; CI (`.github/workflows/ci.yml`) verifies them with `scripts/generate_models.py --check`, and `scripts/check_openapi_consistency.py` verifies that no HTTP path beyond the OpenAPI document is used. CI runs lint, type checking, the test matrix (Python 3.11–3.14 on Ubuntu/macOS/Windows with a 90% coverage gate), the doctests and a distribution build on every push and pull request.

> **Note:** pushing `.github/workflows/ci.yml` needs a token with the `workflows` permission. A GitHub App installation token without it is rejected with *"refusing to allow a GitHub App to create or update workflow"*; commit the file with your own credentials (`git add .github/workflows/ci.yml && git commit && git push`) or grant the app `Workflows: read and write`.

`tests/test_live.py` hits the real API and is deselected by default via the `live` pytest marker; run it with `make live` (or `pytest -m live`). It skips itself when the API cannot be reached.

`scripts/live_sweep.py` (also `make sweep`) samples the real API and reports schema drift: parse failures, unknown response fields, and how often each field comes back `null` or absent. It is how `accentColor: null` (H-1) and the missing `employees` (H-2) were found. Findings go into the `NULLABLE_OVERRIDES` / `OPTIONAL_OVERRIDES` tables in `scripts/generate_models.py` — never into the generated file by hand. Exit codes: `0` no drift, `1` drift found, `2` API unreachable (no verdict either way). Set `TAKASUMIBOT_BASE_URL` to point it at a staging host and `TAKASUMIBOT_LIVE_GIFT_CODE` (comma separated) to exercise `/v3/gift/{id}` with real codes.

## About TakasumiBOT-OpenAPI-Document.json

This SDK defines its HTTP endpoints based on the bundled `TakasumiBOT-OpenAPI-Document.json`.
This OpenAPI document is obtained from the [TakasumiBOT API](https://api.takasumibot.com/docs) with additional metadata and organization for the SDK. The bundled OpenAPI document may be updated along with SDK updates.
The original OpenAPI document is available from the TakasumiBOT API.

## License

[MIT](../LICENSE) — Copyright (c) 2026 Yamatomato0105.
