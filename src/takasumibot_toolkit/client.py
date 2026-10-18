"""Async client for the TakasumiBOT API (13 GET endpoints, no WebSocket)."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import time
from collections.abc import Iterable, Mapping
from typing import Any, Final, Literal, TypeAlias, TypeVar
from urllib.parse import quote

import httpx
from pydantic import BaseModel, TypeAdapter
from pydantic import ValidationError as PydanticValidationError

from takasumibot_toolkit import __version__
from takasumibot_toolkit._args import js_trim
from takasumibot_toolkit.config import (
    BasicConfig,
    RetryConfig,
    StockCacheConfig,
    resolve_base_url,
)
from takasumibot_toolkit.errors import (
    Issue,
    TakasumiBotToolkitConfigError,
    TakasumiBotToolkitError,
    TakasumiBotToolkitHttpError,
    TakasumiBotToolkitNetworkError,
    TakasumiBotToolkitResponseParseError,
    TakasumiBotToolkitRetryLimitError,
    TakasumiBotToolkitTimeoutError,
    TakasumiBotToolkitValidationError,
    is_retryable,
)
from takasumibot_toolkit.helpers import HELPERS, Helpers
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
from takasumibot_toolkit.retry import compute_delay_ms, parse_retry_after
from takasumibot_toolkit.stock import (
    StockBuilder,
    history_of,
    price_of,
    validate_history_limit,
    validate_stock_id,
)
from takasumibot_toolkit.types import LoggerLike

T = TypeVar("T", bound=BaseModel)

_LogLevel: TypeAlias = Literal["debug", "info", "warning", "error"]

# Response headers kept on ``HttpError.headers``: exactly the NPM package's
# snapshot (retry-after, content-type, the request-id family and
# x-ratelimit-remaining). Anything else is dropped to avoid leaking or hoarding data.
_HEADER_ALLOWLIST: Final[tuple[str, ...]] = (
    "content-type",
    "retry-after",
    "x-request-id",
    "x-requestid",
    "request-id",
    "x-takasumibot-request-id",
    "x-ratelimit-remaining",
)
# Searched in this order (the NPM order).
_REQUEST_ID_HEADERS: Final[tuple[str, ...]] = (
    "x-request-id",
    "x-requestid",
    "request-id",
    "x-takasumibot-request-id",
)
# Gift codes are exactly 10 ASCII letters/digits (OpenAPI: "A-Za-z0-9 10文字").
# ``fullmatch`` is used because ``$`` would also accept a trailing newline.
GIFT_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9]{10}")
# ``httpx.RemoteProtocolError`` ("Server disconnected without sending a
# response") is a transient keep-alive failure, and this client only issues
# idempotent GET requests, so only the *local* protocol error is excluded from
# retries.
_NON_RETRYABLE_TRANSPORT: Final[tuple[type[httpx.HTTPError], ...]] = (
    httpx.LocalProtocolError,
    httpx.TooManyRedirects,
    httpx.ProxyError,
)

# Building a TypeAdapter is comparatively expensive and the response schemas are
# fixed, so adapters are cached per (model, is_list) pair.
_ADAPTER_CACHE: dict[tuple[type[BaseModel], bool], TypeAdapter[Any]] = {}


def _type_adapter(model: type[T], is_list: bool) -> TypeAdapter[Any]:
    """Return a cached ``TypeAdapter`` for ``model`` (or ``list[model]``)."""
    key = (model, is_list)
    adapter = _ADAPTER_CACHE.get(key)
    if adapter is None:
        adapter = TypeAdapter(list[model] if is_list else model)  # type: ignore[valid-type]
        _ADAPTER_CACHE[key] = adapter
    return adapter


class _StructuredLoggerAdapter:
    """Expose a stdlib logger as ``LoggerLike`` (context becomes ``extra``).

    ``logging.LoggerAdapter`` instances are wrapped as well: forwarding
    ``**context`` to one reaches ``Logger._log()`` as unknown keyword arguments
    and raises ``TypeError`` on the first retry.
    """

    def __init__(self, logger: logging.Logger | logging.LoggerAdapter[logging.Logger]) -> None:
        self._logger = logger

    def _emit(self, level: _LogLevel, msg: str, context: Mapping[str, object]) -> None:
        logger = self._logger
        if not isinstance(logger, logging.LoggerAdapter):
            getattr(logger, level)(msg, extra=dict(context))
            return
        adapter_extra = getattr(logger, "extra", None)
        extra = {**(adapter_extra if isinstance(adapter_extra, Mapping) else {}), **context}
        if type(logger).process is logging.LoggerAdapter.process:
            # The stock ``process()`` replaces ``extra`` with the adapter's own,
            # which would silently drop the toolkit context; log through the
            # wrapped logger with the merged mapping instead.
            logger.logger.log(getattr(logging, level.upper()), msg, extra=extra)
        else:
            # A custom ``process()`` is honoured (merging adapters keep both).
            getattr(logger, level)(msg, extra=extra)

    def debug(self, msg: str, /, **context: object) -> None:
        self._emit("debug", msg, context)

    def info(self, msg: str, /, **context: object) -> None:
        self._emit("info", msg, context)

    def warning(self, msg: str, /, **context: object) -> None:
        self._emit("warning", msg, context)

    def error(self, msg: str, /, **context: object) -> None:
        self._emit("error", msg, context)


async def _sleep_ms(ms: int) -> None:
    await asyncio.sleep(ms / 1000)


def _validate_path_param(name: str, value: object) -> str:
    """Validate a path parameter and return it trimmed (NPM ``requireNonEmptyString``).

    Args:
        name: Parameter name used in errors.
        value: Caller-supplied value.

    Returns:
        The value with surrounding whitespace removed (JavaScript ``trim()``
        semantics), ready to be percent-encoded.

    Raises:
        TakasumiBotToolkitValidationError: ``INVALID_TYPE`` for a non-str,
            ``EMPTY_ARGUMENT`` for an empty/blank str, ``UNSAFE_PATH_PARAMETER``
            for braces or a bare ``"."``/``".."`` segment.
    """
    if not isinstance(value, str):
        raise TakasumiBotToolkitValidationError(
            f"{name} must be a str (got {type(value).__name__})",
            field=name,
            value=value,
            code="INVALID_TYPE",
        )
    trimmed = js_trim(value)
    if not trimmed:
        raise TakasumiBotToolkitValidationError(
            f"{name} must be a non-empty string", field=name, value=value, code="EMPTY_ARGUMENT"
        )
    if "{" in trimmed or "}" in trimmed:
        raise TakasumiBotToolkitValidationError(
            f"{name} must not contain braces", field=name, value=value, code="UNSAFE_PATH_PARAMETER"
        )
    if trimmed in (".", ".."):
        # ``quote(value, safe="")`` does not escape dots, so "." / ".." would be
        # normalised away by the URL parser and the request would leave the
        # documented path.
        raise TakasumiBotToolkitValidationError(
            f"{name} must not be a relative path segment (got {value!r})",
            field=name,
            value=value,
            code="UNSAFE_PATH_PARAMETER",
        )
    return trimmed


def _validate_gift_id(value: object) -> str:
    """Validate a gift code before any request (NPM ``requireGiftId``).

    As in the NPM package the value is checked as given (it is **not** trimmed):
    only exactly 10 ASCII letters/digits pass, which also rules out empty,
    blank, padded and URL-unsafe values.

    Args:
        value: Caller-supplied gift code.

    Returns:
        The validated code (unchanged).

    Raises:
        TakasumiBotToolkitValidationError: ``INVALID_GIFT_ID`` (field ``id``) for
            anything that is not a str matching ``[A-Za-z0-9]{10}``.
    """
    if not isinstance(value, str) or GIFT_ID_PATTERN.fullmatch(value) is None:
        raise TakasumiBotToolkitValidationError(
            f"id must be a 10 character gift code matching /^[A-Za-z0-9]{{10}}$/ (got {value!r})",
            field="id",
            value=value,
            code="INVALID_GIFT_ID",
        )
    return value


def _build_url(base_url: str, path_template: str, params: Mapping[str, str]) -> str:
    path = path_template
    for key, value in params.items():
        path = path.replace("{" + key + "}", quote(value, safe=""))
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return f"{base_url}{path}"


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"invalid JSON constant {value}")


def _copy_entries(entries: Iterable[StockEntry]) -> list[StockEntry]:
    """Deep-copy stock entries for or from the stock cache.

    ``StockEntry`` is frozen, but its ``prices`` list is mutable, so the cache
    and its callers must never share entry objects.
    """
    return [entry.model_copy(deep=True) for entry in entries]


def _raw_body(content: bytes) -> str:
    """Decode a whole body for an error (NPM ``rawBody``; ``""`` when empty).

    Only called on failure paths, so successful responses never pay for it.
    """
    return content.decode("utf-8", errors="replace")


def _extract_http_error(response: httpx.Response, url: str) -> TakasumiBotToolkitHttpError:
    content = response.content
    raw_body = _raw_body(content)
    json_body: object = None
    if content:
        try:
            json_body = json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError):
            json_body = None
    headers = {k.lower(): v for k, v in response.headers.items() if k.lower() in _HEADER_ALLOWLIST}
    request_id: str | None = None
    for name in _REQUEST_ID_HEADERS:
        candidate = response.headers.get(name)
        if isinstance(candidate, str) and candidate:
            request_id = candidate
            break
    api_code: str | None = None
    api_message: str | None = None
    if isinstance(json_body, dict):
        if request_id is None:
            for key in ("requestId", "request_id", "id"):
                v = json_body.get(key)
                if isinstance(v, str):
                    request_id = v
                    break
        for key in ("code", "errorCode", "error_code"):
            v = json_body.get(key)
            if isinstance(v, (str, int)):
                api_code = str(v)
                break
        if api_code is None and isinstance(json_body.get("error"), dict):
            v = json_body["error"].get("code")
            if isinstance(v, (str, int)):
                api_code = str(v)
        for key in ("message", "error", "detail"):
            v = json_body.get(key)
            if isinstance(v, str):
                api_message = v
                break
        if api_message is None and isinstance(json_body.get("error"), dict):
            v = json_body["error"].get("message")
            if isinstance(v, str):
                api_message = v
    retry_after = response.headers.get("retry-after")
    status = response.status_code
    return TakasumiBotToolkitHttpError(
        f"GET {url} failed with HTTP {status}" + (f": {api_message}" if api_message else ""),
        status=status,
        method="GET",
        url=url,
        status_text=response.reason_phrase,
        request_id=request_id,
        api_code=api_code,
        api_message=api_message,
        headers=headers,
        json_body=json_body,
        raw_body=raw_body,
        retry_after=retry_after,
    )


def _map_httpx_error(exc: httpx.HTTPError, *, url: str, timeout_ms: int) -> Exception:
    if isinstance(exc, httpx.ConnectTimeout):
        phase: str | None = "connect"
    elif isinstance(exc, httpx.ReadTimeout):
        phase = "read"
    elif isinstance(exc, httpx.WriteTimeout):
        phase = "write"
    elif isinstance(exc, httpx.PoolTimeout):
        phase = "pool"
    else:
        phase = None
    if isinstance(exc, httpx.TimeoutException):
        return TakasumiBotToolkitTimeoutError(
            f"GET {url} timed out after {timeout_ms} ms" + (f" (phase={phase})" if phase else ""),
            timeout_ms=timeout_ms,
            phase=phase,  # type: ignore[arg-type]
            method="GET",
            url=url,
            cause=exc,
        )
    retryable = not isinstance(exc, _NON_RETRYABLE_TRANSPORT) and not isinstance(
        exc, httpx.UnsupportedProtocol
    )
    return TakasumiBotToolkitNetworkError(
        f"GET {url} failed: {type(exc).__name__}: {exc}",
        method="GET",
        url=url,
        reason=type(exc).__name__,
        retryable=retryable,
        cause=exc,
    )


def _issues_from(exc: PydanticValidationError) -> tuple[Issue, ...]:
    issues: list[Issue] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        parts: list[str] = []
        for part in loc:
            if isinstance(part, int):
                if parts:
                    parts[-1] = f"{parts[-1]}[{part}]"
                else:
                    parts.append(f"[{part}]")
            else:
                parts.append(str(part))
        issues.append(
            Issue(
                field=".".join(parts),
                message=str(err.get("msg", "")),
                type=str(err.get("type", "")),
            )
        )
    return tuple(issues)


class KitClient:
    """Async TakasumiBOT API client. Create via :func:`create_kit_client`.

    Examples:
        >>> import asyncio
        >>> async def main() -> None:
        ...     async with create_kit_client() as kit:
        ...         tax = await kit.get_tax_info()
        >>> # asyncio.run(main())
    """

    def __init__(self, config: BasicConfig, *, base_url: str, owns_http_client: bool) -> None:
        """Initialise the object."""
        self._config = config
        self._base_url = base_url
        self._owns_http_client = owns_http_client
        self._closed = False
        logger = config.effective_logger
        self._logger: LoggerLike = (
            _StructuredLoggerAdapter(logger)
            if isinstance(logger, (logging.Logger, logging.LoggerAdapter))
            else logger
        )
        if config.client is not None:
            self._http = config.client
        else:
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(config.timeout_ms / 1000),
                follow_redirects=config.follow_redirects,
            )
        self._stock_cache: tuple[float, tuple[StockEntry, ...]] | None = None
        self._stock_lock = asyncio.Lock()
        self._rng = random.Random()

    # -- properties ------------------------------------------------------
    @property
    def base_url(self) -> str:
        """Resolved base URL (read-only, no trailing slash)."""
        return self._base_url

    @property
    def config(self) -> BasicConfig:
        """Merged immutable configuration."""
        return self._config

    @property
    def logger(self) -> LoggerLike:
        """Effective logger (stdlib loggers are wrapped to carry ``extra``)."""
        return self._logger

    @property
    def closed(self) -> bool:
        """Whether aclose() has been called."""
        return self._closed

    @property
    def owns_http_client(self) -> bool:
        """True when this client created (and will close) its AsyncClient."""
        return self._owns_http_client

    @property
    def helpers(self) -> Helpers:
        """The pure helper functions (NPM ``kit.helpers``), usable without imports.

        Examples:
            >>> kit = create_kit_client()
            >>> kit.helpers.truncate("hello world", 5)
            'he...'
        """
        return HELPERS

    # -- lifecycle -------------------------------------------------------
    async def aclose(self) -> None:
        """Close the internally-created HTTP client (idempotent).

        A caller-injected client is never closed. Examples: ``await kit.aclose()``.
        """
        if self._closed:
            return
        self._closed = True
        if self._owns_http_client:
            await self._http.aclose()
        else:
            self._log("debug", "not closing caller-owned http client", {})

    async def __aenter__(self) -> KitClient:
        """Enter the async context manager.

        Returns:
            This client.
        """
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: object
    ) -> None:
        """Exit the async context manager, closing owned resources."""
        await self.aclose()

    def _ensure_open(self) -> None:
        if self._closed:
            raise TakasumiBotToolkitConfigError(
                "client is closed", code="CLIENT_CLOSED", field="client"
            )

    def _log(self, level: _LogLevel, msg: str, context: Mapping[str, object]) -> None:
        """Log one event; a failing logger never fails the request itself."""
        try:
            method = getattr(self._logger, level, None)
            if callable(method):
                method(msg, **context)
        except Exception:
            # A misbehaving logger must not turn into a failed request (M-2).
            return

    # -- core request ----------------------------------------------------
    async def _request(
        self, path_template: str, params: Mapping[str, str], model: type[T], *, is_list: bool
    ) -> T | list[T]:
        self._ensure_open()
        url = _build_url(self._base_url, path_template, params)
        cfg = self._config
        retry_cfg = cfg.effective_retry
        delays: list[int] = []
        last_error: Exception | None = None
        for attempt in range(retry_cfg.max_retries + 1):
            try:
                return await self._request_once(url, model, is_list=is_list)
            except Exception as exc:
                if not is_retryable(exc):
                    raise
                if attempt == retry_cfg.max_retries:
                    last_error = exc
                    break
                kit_exc = exc  # retryable toolkit error
                retry_after = (
                    parse_retry_after(kit_exc.retry_after)
                    if isinstance(kit_exc, TakasumiBotToolkitHttpError)
                    else None
                )
                delay = compute_delay_ms(
                    retry_cfg, attempt=attempt, retry_after_s=retry_after, rng=self._rng
                )
                delays.append(delay)
                self._log(
                    "warning",
                    f"retrying request GET {url}",
                    {
                        "attempt": attempt + 1,
                        "delay_ms": delay,
                        "reason": type(exc).__name__,
                        "url": url,
                    },
                )
                await _sleep_ms(delay)
        assert isinstance(last_error, TakasumiBotToolkitError)
        if retry_cfg.max_retries == 0:
            raise last_error
        self._log(
            "error",
            f"retry limit exceeded for GET {url}",
            {"attempts": retry_cfg.max_retries + 1, "url": url},
        )
        raise TakasumiBotToolkitRetryLimitError(
            f"GET {url} failed after {retry_cfg.max_retries + 1} attempts: {last_error}",
            attempts=retry_cfg.max_retries + 1,
            max_retries=retry_cfg.max_retries,
            last_error=last_error,
            delays_ms=tuple(delays),
            method="GET",
            url=url,
        ) from last_error

    async def _request_once(self, url: str, model: type[T], *, is_list: bool) -> T | list[T]:
        cfg = self._config
        headers = httpx.Headers(
            {
                "accept": "application/json",
                "user-agent": f"takasumibot-toolkit/{__version__}",
            }
        )
        if cfg.headers:
            headers.update(cfg.headers)
        timeout = httpx.Timeout(cfg.timeout_ms / 1000)
        self._log("debug", f"http request GET {url}", {"method": "GET", "url": url})
        try:
            # ``follow_redirects`` is passed per request so the configured value
            # also applies to a caller-injected AsyncClient.
            response = await self._http.request(
                "GET",
                url,
                headers=headers,
                timeout=timeout,
                follow_redirects=cfg.follow_redirects,
            )
        except httpx.HTTPError as exc:
            raise _map_httpx_error(exc, url=url, timeout_ms=cfg.timeout_ms) from exc
        self._log(
            "debug",
            f"http response GET {url} status={response.status_code}",
            {"method": "GET", "url": url, "status": response.status_code},
        )
        if not 200 <= response.status_code < 300:
            raise _extract_http_error(response, url)
        content = response.content
        if not content:
            raise TakasumiBotToolkitResponseParseError(
                f"response for GET {url} does not match {model.__name__}: empty body",
                method="GET",
                url=url,
                status=response.status_code,
                stage="decode",
                raw_body="",
                code="EMPTY_BODY",
            )
        try:
            data: object = json.loads(content, parse_constant=_reject_json_constant)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as decode_exc:
            raise TakasumiBotToolkitResponseParseError(
                f"response for GET {url} is not valid JSON: {decode_exc}",
                method="GET",
                url=url,
                status=response.status_code,
                stage="decode",
                raw_body=_raw_body(content),
                code="INVALID_JSON",
                cause=decode_exc,
            ) from decode_exc

        adapter = _type_adapter(model, is_list)
        try:
            # ``validate_json`` parses and validates in one pass.
            result: T | list[T] = adapter.validate_json(content)
            return result
        except PydanticValidationError as exc:
            if data is None:
                raise TakasumiBotToolkitResponseParseError(
                    f"response for GET {url} does not match {model.__name__}: null body",
                    method="GET",
                    url=url,
                    status=response.status_code,
                    stage="validate",
                    raw_body=_raw_body(content),
                    code="SCHEMA_MISMATCH",
                    issues=(
                        Issue(field="", message="null is not a valid response", type="null_type"),
                    ),
                ) from exc
            issues = _issues_from(exc)
            first = f"; first: {issues[0].field}: {issues[0].message}" if issues else ""
            raise TakasumiBotToolkitResponseParseError(
                f"response for GET {url} does not match {model.__name__}: "
                f"{len(issues)} issue(s){first}",
                method="GET",
                url=url,
                status=response.status_code,
                stage="validate",
                raw_body=_raw_body(content),
                issues=issues,
                code="SCHEMA_MISMATCH",
                cause=exc,
            ) from exc

    # -- endpoints -------------------------------------------------------
    async def get_gift_info(self, id: str, /) -> GiftResponse:
        """Get gift information by gift code.

        Args:
            id: Gift code: exactly 10 ASCII letters/digits (``[A-Za-z0-9]{10}``),
                validated as given (not trimmed), like the NPM package.

        Returns:
            Gift information.

        Raises:
            TakasumiBotToolkitValidationError: ``INVALID_GIFT_ID`` (field ``id``)
                when id is not a 10 character alphanumeric str; no request is sent.
            TakasumiBotToolkitHttpError: On an HTTP error (including 404).
            TakasumiBotToolkitResponseParseError: If the response mismatches the schema.

        Examples:
            >>> # gift = await kit.get_gift_info("A1B2C3D4E5")
        """
        value = _validate_gift_id(id)
        result = await self._request("/v3/gift/{id}", {"id": value}, GiftResponse, is_list=False)
        assert isinstance(result, GiftResponse)
        return result

    async def get_tax_info(self) -> TaxResponse:
        """Get tax information.

        Returns:
            Tax rates and thresholds (units are not converted).

        Raises:
            TakasumiBotToolkitHttpError: On an HTTP error.
            TakasumiBotToolkitResponseParseError: If the response mismatches the schema.

        Examples:
            >>> # tax = await kit.get_tax_info()
        """
        result = await self._request("/v3/tax/", {}, TaxResponse, is_list=False)
        assert isinstance(result, TaxResponse)
        return result

    async def get_shard_info(self) -> ShardResponse:
        """Get shard information (counts remain strings, as in the API).

        Returns:
            Shard data.

        Raises:
            TakasumiBotToolkitHttpError: On an HTTP error.
            TakasumiBotToolkitResponseParseError: If the response mismatches the schema.

        Examples:
            >>> # shard = await kit.get_shard_info()
        """
        result = await self._request("/v3/shard/", {}, ShardResponse, is_list=False)
        assert isinstance(result, ShardResponse)
        return result

    async def get_statistics_info(self) -> StatisticsResponse:
        """Get aggregate statistics.

        Returns:
            Statistics response.

        Raises:
            TakasumiBotToolkitHttpError: On an HTTP error.
            TakasumiBotToolkitResponseParseError: If the response mismatches the schema.

        Examples:
            >>> # stats = await kit.get_statistics_info()
        """
        result = await self._request("/v3/statistics/", {}, StatisticsResponse, is_list=False)
        assert isinstance(result, StatisticsResponse)
        return result

    async def get_history_by_id(self, id: str, /) -> list[HistoryEntry]:
        """Get transaction history for a user id (server order preserved).

        Args:
            id: Discord user id.

        Returns:
            History entries (possibly empty).

        Raises:
            TakasumiBotToolkitValidationError: If id is invalid.
            TakasumiBotToolkitHttpError: On an HTTP error.
            TakasumiBotToolkitResponseParseError: If the response mismatches the schema.

        Examples:
            >>> # entries = await kit.get_history_by_id("123456789012345678")
        """
        value = _validate_path_param("id", id)
        result = await self._request("/v3/history/{id}", {"id": value}, HistoryEntry, is_list=True)
        assert isinstance(result, list)
        return result

    async def get_profile_by_id(self, id: str, /) -> ProfileResponse:
        """Get a user profile by id.

        Args:
            id: Discord user id.

        Returns:
            Profile response.

        Raises:
            TakasumiBotToolkitValidationError: If id is invalid.
            TakasumiBotToolkitHttpError: On an HTTP error.
            TakasumiBotToolkitResponseParseError: If the response mismatches the schema.

        Examples:
            >>> # profile = await kit.get_profile_by_id("123456789012345678")
        """
        value = _validate_path_param("id", id)
        result = await self._request(
            "/v3/profile/{id}", {"id": value}, ProfileResponse, is_list=False
        )
        assert isinstance(result, ProfileResponse)
        return result

    async def get_ranking(self) -> list[RankingEntry]:
        """Get the ranking list (server order/count preserved).

        Returns:
            Ranking entries.

        Raises:
            TakasumiBotToolkitHttpError: On an HTTP error.
            TakasumiBotToolkitResponseParseError: If the response mismatches the schema.

        Examples:
            >>> # ranking = await kit.get_ranking()
        """
        result = await self._request("/v3/ranking/", {}, RankingEntry, is_list=True)
        assert isinstance(result, list)
        return result

    async def get_company_list(self) -> list[CompanyListEntry]:
        """Get the company list.

        Returns:
            Company list entries.

        Raises:
            TakasumiBotToolkitHttpError: On an HTTP error.
            TakasumiBotToolkitResponseParseError: If the response mismatches the schema.

        Examples:
            >>> # companies = await kit.get_company_list()
        """
        result = await self._request("/v3/companylist/", {}, CompanyListEntry, is_list=True)
        assert isinstance(result, list)
        return result

    async def get_company_by_id(self, id: str, /) -> CompanyDetailResponse:
        """Get company details by company id or owner Discord id.

        Args:
            id: Company id (10 chars) or owner Discord user id.

        Returns:
            Company detail including statistics and employees.

        Raises:
            TakasumiBotToolkitValidationError: If id is invalid.
            TakasumiBotToolkitHttpError: On an HTTP error.
            TakasumiBotToolkitResponseParseError: If the response mismatches the schema.

        Examples:
            >>> # company = await kit.get_company_by_id("COMPANYID1")
        """
        value = _validate_path_param("id", id)
        result = await self._request(
            "/v3/company/{id}", {"id": value}, CompanyDetailResponse, is_list=False
        )
        assert isinstance(result, CompanyDetailResponse)
        return result

    async def get_stock_list(self, *, force_refresh: bool = False) -> list[StockEntry]:
        """Get the full stock list, served from the stock cache when enabled.

        With ``stock_cache`` configured this shares the client-wide TTL cache
        (and its single-flight request) with the stock helpers, exactly like the
        NPM ``getStockList()``; without it every call sends a request.

        Args:
            force_refresh: Bypass a cached list and fetch a fresh one (the cache,
                when enabled, is refreshed with the result).

        Returns:
            Stock entries (new objects on every call; mutating the list or an
            entry's ``prices`` never affects the cache).

        Raises:
            TakasumiBotToolkitHttpError: On an HTTP error.
            TakasumiBotToolkitResponseParseError: If the response mismatches the schema.

        Examples:
            >>> # stocks = await kit.get_stock_list()
            >>> # fresh = await kit.get_stock_list(force_refresh=True)
        """
        if force_refresh:
            if self._config.stock_cache is None:
                return await self._fetch_stock_list()
            # Hold the cache lock so an in-flight cache miss cannot overwrite
            # the refreshed list with its older result afterwards.
            async with self._stock_lock:
                entries = await self._fetch_stock_list()
                self._stock_cache = (time.monotonic(), tuple(_copy_entries(entries)))
                return entries
        return await self._load_stock_entries()

    async def _fetch_stock_list(self) -> list[StockEntry]:
        """Request ``GET /v3/stock/`` (no cache involved)."""
        result = await self._request("/v3/stock/", {}, StockEntry, is_list=True)
        assert isinstance(result, list)
        return result

    async def get_discord_user_by_name(self, name: str, /) -> DiscordUserSearchResponse:
        """Get cached Discord user info by name (trimmed, then percent-encoded).

        Args:
            name: Discord user name. Surrounding whitespace is trimmed (as in the
                NPM package), then the name is percent-encoded; no @/# handling
                is done.

        Returns:
            Discord user search response.

        Raises:
            TakasumiBotToolkitValidationError: If name is invalid.
            TakasumiBotToolkitHttpError: On an HTTP error.
            TakasumiBotToolkitResponseParseError: If the response mismatches the schema.

        Examples:
            >>> # user = await kit.get_discord_user_by_name("some_name")
        """
        value = _validate_path_param("name", name)
        result = await self._request(
            "/v3/discord/usersearch/{name}",
            {"name": value},
            DiscordUserSearchResponse,
            is_list=False,
        )
        assert isinstance(result, DiscordUserSearchResponse)
        return result

    async def get_company_history_by_id(self, id: str, /) -> list[CompanyHistoryEntry]:
        """Get company transaction history by id (server order preserved).

        Args:
            id: Company id.

        Returns:
            Company history entries.

        Raises:
            TakasumiBotToolkitValidationError: If id is invalid.
            TakasumiBotToolkitHttpError: On an HTTP error.
            TakasumiBotToolkitResponseParseError: If the response mismatches the schema.

        Examples:
            >>> # history = await kit.get_company_history_by_id("COMPANYID1")
        """
        value = _validate_path_param("id", id)
        result = await self._request(
            "/v3/companyHistory/{id}", {"id": value}, CompanyHistoryEntry, is_list=True
        )
        assert isinstance(result, list)
        return result

    async def get_status(self) -> list[StatusEntry]:
        """Get system status entries (units are not converted).

        Returns:
            Status entries.

        Raises:
            TakasumiBotToolkitHttpError: On an HTTP error.
            TakasumiBotToolkitResponseParseError: If the response mismatches the schema.

        Examples:
            >>> # status = await kit.get_status()
        """
        result = await self._request("/v3/status/", {}, StatusEntry, is_list=True)
        assert isinstance(result, list)
        return result

    # -- stock derivation --------------------------------------------------
    def get_stock(self) -> StockBuilder:
        """Return a stock builder (no HTTP is performed).

        Returns:
            StockBuilder bound to this client.

        Raises:
            TakasumiBotToolkitConfigError: If the client is closed.

        Examples:
            >>> # stock = kit.get_stock().id("SOME_ID")
            >>> # price = await stock.price()
        """
        self._ensure_open()
        return StockBuilder(self)

    async def _load_stock_entries(self) -> list[StockEntry]:
        """Load the stock list, using the client-wide TTL cache when enabled.

        Concurrent misses share one request (single-flight); failures are never
        cached.

        Returns:
            A fresh ``list`` of entries the cache does not share (entries are
            deep-copied in and out of the cache), so callers cannot corrupt it
            by mutating what they received, ``prices`` included.
        """
        self._ensure_open()
        ttl_ms = self._config.stock_cache_ttl_ms
        if ttl_ms is None:
            return await self._fetch_stock_list()
        ttl = ttl_ms / 1000
        now = time.monotonic()
        cached = self._stock_cache
        if cached is not None and now < cached[0] + ttl:
            return _copy_entries(cached[1])
        async with self._stock_lock:
            now = time.monotonic()
            cached = self._stock_cache
            if cached is not None and now < cached[0] + ttl:
                return _copy_entries(cached[1])
            entries = await self._fetch_stock_list()
            self._stock_cache = (time.monotonic(), tuple(_copy_entries(entries)))
            return entries

    async def get_stock_info_by_id(self, id: str, /) -> StockEntry:
        """Derive one stock entry from the list endpoint.

        Args:
            id: Stock id.

        Returns:
            The matching entry.

        Raises:
            TakasumiBotToolkitValidationError: code STOCK_NOT_FOUND when absent.

        Examples:
            >>> # entry = await kit.get_stock_info_by_id("SOME_ID")
        """
        return await self.get_stock().id(validate_stock_id(id)).info()

    async def get_stock_price_by_id(self, id: str, /) -> int | None:
        """Derive the latest price for a stock id from the list endpoint.

        Args:
            id: Stock id.

        Returns:
            Latest price, or None when prices is empty.

        Raises:
            TakasumiBotToolkitValidationError: code STOCK_NOT_FOUND when absent.

        Examples:
            >>> # price = await kit.get_stock_price_by_id("SOME_ID")
        """
        entry = await self.get_stock_info_by_id(id)
        return price_of(entry)

    async def get_stock_history_by_id(self, id: str, /, *, limit: int | None = None) -> list[int]:
        """Derive newest-first history for a stock id (no timestamps exist).

        Args:
            id: Stock id.
            limit: None for all, 0 for [], positive int for the newest n.

        Returns:
            Newest-first prices.

        Raises:
            TakasumiBotToolkitValidationError: On an invalid limit or unknown id.

        Examples:
            >>> # history = await kit.get_stock_history_by_id("SOME_ID", limit=30)
        """
        n = validate_history_limit(limit)
        entry = await self.get_stock_info_by_id(id)
        return history_of(entry, n)


def create_kit_client(
    config: BasicConfig | None = None,
    *,
    timeout_ms: int | None = None,
    headers: Mapping[str, str] | None = None,
    retry: RetryConfig | Mapping[str, object] | None = None,
    client: httpx.AsyncClient | None = None,
    logger: object | None = None,
    stock_cache: StockCacheConfig | Mapping[str, object] | Literal[False] | None = None,
    follow_redirects: bool | None = None,
) -> KitClient:
    """Create a KitClient (keyword arguments override ``config`` fields).

    Args:
        config: Base configuration, if any.
        timeout_ms: Per-attempt timeout override.
        headers: Extra headers override.
        retry: RetryConfig or dict override.
        client: Caller-owned httpx.AsyncClient (never closed by the kit).
        logger: LoggerLike logger override.
        stock_cache: Stock list cache override (NPM ``stockCache``): ``{}`` /
            ``StockCacheConfig()`` enables a 60 000 ms TTL, ``{"ttl_ms": n}``
            sets it, ``False`` disables it. ``None`` keeps ``config``'s value.
        follow_redirects: Redirect-following override.

    Returns:
        A new KitClient. Use ``async with`` or call ``aclose()``.

    Raises:
        TakasumiBotToolkitConfigError: On invalid configuration or base URL.

    Examples:
        >>> kit = create_kit_client(timeout_ms=5000)
        >>> kit.base_url
        'https://api.takasumibot.com'
        >>> create_kit_client(stock_cache={}).config.stock_cache_ttl_ms
        60000
    """
    base = config if config is not None else BasicConfig()
    updates: dict[str, object] = {}
    if timeout_ms is not None:
        updates["timeout_ms"] = timeout_ms
    if headers is not None:
        updates["headers"] = headers
    if retry is not None:
        updates["retry"] = retry
    if client is not None:
        updates["client"] = client
    if logger is not None:
        updates["logger"] = logger
    if stock_cache is not None:
        updates["stock_cache"] = stock_cache
    if follow_redirects is not None:
        updates["follow_redirects"] = follow_redirects
    if updates:
        data = {name: getattr(base, name) for name in BasicConfig.model_fields}
        data.update(updates)
        merged = BasicConfig(**data)
    else:
        merged = base
    owns = merged.client is None
    return KitClient(merged, base_url=resolve_base_url(), owns_http_client=owns)
