"""Configuration models. ``BasicConfig`` deliberately has no ``base_url``."""

from __future__ import annotations

import math
import os
import string
from collections.abc import Mapping
from typing import Final
from urllib.parse import urlsplit, urlunsplit

import httpx
from pydantic import BaseModel, ConfigDict, model_validator
from pydantic import ValidationError as PydanticValidationError

from takasumibot_toolkit.errors import ErrorCode, TakasumiBotToolkitConfigError
from takasumibot_toolkit.types import LoggerLike, NullLogger

BASE_URL_ENV_VAR: Final[str] = "TAKASUMIBOT_BASE_URL"
DEFAULT_BASE_URL: Final[str] = "https://api.takasumibot.com"
DEFAULT_STOCK_CACHE_TTL_MS: Final[int] = 60_000

# RFC 7230 token characters: the only ones allowed in a header field name.
_HEADER_TOKEN_CHARS: Final[frozenset[str]] = frozenset(
    "!#$%&'*+-.^_`|~" + string.digits + string.ascii_letters
)


def _to_config_error(
    exc: PydanticValidationError, *, model_name: str, field_prefix: str = ""
) -> TakasumiBotToolkitConfigError:
    errors = exc.errors()
    first: dict[str, object] = dict(errors[0]) if errors else {}
    loc_obj = first.get("loc", ())
    loc = loc_obj if isinstance(loc_obj, (list, tuple)) else ()
    field = ".".join(str(p) for p in loc) or None
    msg = str(first.get("msg", "invalid configuration"))
    err_type = str(first.get("type", ""))
    code: ErrorCode = "INVALID_CONFIG"
    if err_type == "extra_forbidden":
        code = "UNKNOWN_CONFIG_KEY"
    if field and field.startswith("retry."):
        field = field[len("retry.") :]
    if field and field_prefix:
        field = field_prefix + field
    return TakasumiBotToolkitConfigError(
        f"{model_name}: {field + ': ' if field else ''}{msg}",
        field=field,
        code=code,
        cause=exc,
    )


def _validate_headers(headers: Mapping[str, str] | None) -> None:
    if headers is None:
        return
    if not isinstance(headers, Mapping):
        raise TakasumiBotToolkitConfigError(
            f"headers must be a mapping (got {type(headers).__name__})",
            field="headers",
            value=headers,
        )
    for key, value in headers.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise TakasumiBotToolkitConfigError(
                "headers keys and values must be str", field="headers", value=key
            )
        if not key or any(c not in _HEADER_TOKEN_CHARS for c in key):
            # Rejecting these here keeps httpx from failing later, at send time.
            raise TakasumiBotToolkitConfigError(
                f"headers key must be a non-empty RFC 7230 token (got {key!r})",
                field="headers",
                value=key,
            )
        invalid = next((c for c in value if c != "\t" and not 0x20 <= ord(c) <= 0x7E), None)
        if invalid is not None:
            raise TakasumiBotToolkitConfigError(
                f"headers value for {key!r} contains {invalid!r}; only printable ASCII, "
                "SP and HTAB are allowed",
                field="headers",
                value=value,
            )


def _validate_logger(logger: object) -> None:
    if logger is None:
        return
    for name in ("debug", "info", "warning", "error"):
        if not callable(getattr(logger, name, None)):
            raise TakasumiBotToolkitConfigError(
                f"logger must provide a callable {name}() method", field="logger", value=logger
            )


class RetryConfig(BaseModel):
    """Retry configuration (defaults match the NPM ``DEFAULT_RETRY_CONFIG``).

    Args:
        max_retries: Number of retries (total attempts = max_retries + 1).
        initial_delay_ms: Initial backoff delay in milliseconds (>= 0, default 300).
        max_delay_ms: Maximum backoff delay before jitter (>= initial, default 5000).
        backoff_factor: Exponential factor (>= 1.0).
        jitter: Whether to apply 50-100% jitter.

    Examples:
        >>> RetryConfig().max_retries
        3
        >>> RetryConfig().initial_delay_ms, RetryConfig().max_delay_ms
        (300, 5000)
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    max_retries: int = 3
    initial_delay_ms: int = 300
    max_delay_ms: int = 5_000
    backoff_factor: float = 2.0
    jitter: bool = True

    def __init__(self, **data: object) -> None:
        """Initialise the object."""
        try:
            super().__init__(**data)
        except PydanticValidationError as exc:
            raise _to_config_error(exc, model_name="RetryConfig") from exc

    @model_validator(mode="after")
    def _check_range(self) -> RetryConfig:
        if self.max_retries < 0:
            raise TakasumiBotToolkitConfigError(
                f"max_retries must be >= 0 (got {self.max_retries})",
                field="max_retries",
                value=self.max_retries,
            )
        if self.initial_delay_ms < 0:
            raise TakasumiBotToolkitConfigError(
                f"initial_delay_ms must be >= 0 (got {self.initial_delay_ms})",
                field="initial_delay_ms",
                value=self.initial_delay_ms,
            )
        if self.max_delay_ms < 0:
            raise TakasumiBotToolkitConfigError(
                f"max_delay_ms must be >= 0 (got {self.max_delay_ms})",
                field="max_delay_ms",
                value=self.max_delay_ms,
            )
        if not math.isfinite(self.backoff_factor):
            # NaN would pass every comparison below and blow up later, when the
            # first delay is computed.
            raise TakasumiBotToolkitConfigError(
                f"backoff_factor must be a finite number (got {self.backoff_factor})",
                field="backoff_factor",
                value=self.backoff_factor,
            )
        if self.backoff_factor < 1.0:
            raise TakasumiBotToolkitConfigError(
                f"backoff_factor must be >= 1.0 (got {self.backoff_factor})",
                field="backoff_factor",
                value=self.backoff_factor,
            )
        if self.initial_delay_ms > self.max_delay_ms:
            raise TakasumiBotToolkitConfigError(
                "initial_delay_ms must be <= max_delay_ms "
                f"(got initial_delay_ms={self.initial_delay_ms}, max_delay_ms={self.max_delay_ms})",
                field="initial_delay_ms",
                value=self.initial_delay_ms,
            )
        return self


DEFAULT_RETRY_CONFIG: Final[RetryConfig] = RetryConfig()
"""The retry defaults (``RetryConfig()``), exported like the NPM ``DEFAULT_RETRY_CONFIG``."""


class StockCacheConfig(BaseModel):
    """Client-wide stock list cache (NPM ``stockCache: { ttlMs }``).

    Pass it (or a mapping such as ``{}`` / ``{"ttl_ms": 30_000}``) as
    ``stock_cache``; ``None``/``False`` keeps the cache disabled (the default).

    Args:
        ttl_ms: Time to live in milliseconds (an int > 0, default 60 000).

    Examples:
        >>> StockCacheConfig().ttl_ms
        60000
        >>> StockCacheConfig(ttl_ms=30_000).ttl_ms
        30000
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    ttl_ms: int = DEFAULT_STOCK_CACHE_TTL_MS

    def __init__(self, **data: object) -> None:
        """Initialise the object."""
        try:
            super().__init__(**data)
        except PydanticValidationError as exc:
            raise _to_config_error(
                exc, model_name="StockCacheConfig", field_prefix="stock_cache."
            ) from exc

    @model_validator(mode="after")
    def _check(self) -> StockCacheConfig:
        if self.ttl_ms <= 0:
            raise TakasumiBotToolkitConfigError(
                f"stock_cache.ttl_ms must be an int > 0 (got {self.ttl_ms})",
                field="stock_cache.ttl_ms",
                value=self.ttl_ms,
            )
        return self


def _coerce_stock_cache(value: object) -> StockCacheConfig | None:
    """Normalise the ``stock_cache`` option (NPM ``stockCache`` semantics).

    Args:
        value: ``None``/``False`` (disabled), a ``StockCacheConfig`` or a mapping
            accepted by it (``{}`` enables the cache with the 60 s default).

    Returns:
        The cache configuration, or None when the cache is disabled.

    Raises:
        TakasumiBotToolkitConfigError: For ``True`` or any other value (as in the
            NPM package, enabling needs an explicit ``{}``/``StockCacheConfig()``),
            unknown mapping keys, or an invalid ``ttl_ms``.
    """
    if value is None or value is False:
        return None
    if isinstance(value, StockCacheConfig):
        return value
    if isinstance(value, Mapping):
        unknown = sorted(str(k) for k in value if k != "ttl_ms")
        if unknown:
            raise TakasumiBotToolkitConfigError(
                f"stock_cache: unknown config key(s): {', '.join(unknown)}",
                field=f"stock_cache.{unknown[0]}",
                code="UNKNOWN_CONFIG_KEY",
            )
        return StockCacheConfig(**{str(k): v for k, v in value.items()})
    raise TakasumiBotToolkitConfigError(
        "stock_cache must be None/False or a StockCacheConfig / mapping like "
        f"{{'ttl_ms': {DEFAULT_STOCK_CACHE_TTL_MS}}} (got {value!r})",
        field="stock_cache",
        value=value,
    )


def _coerce_retry(value: object) -> RetryConfig | None:
    if value is None:
        return None
    if isinstance(value, RetryConfig):
        return value
    if isinstance(value, Mapping):
        known = {"max_retries", "initial_delay_ms", "max_delay_ms", "backoff_factor", "jitter"}
        unknown = [str(k) for k in value if k not in known]
        if unknown:
            raise TakasumiBotToolkitConfigError(
                f"retry: unknown config key(s): {', '.join(sorted(unknown))}",
                field=sorted(unknown)[0],
                code="UNKNOWN_CONFIG_KEY",
            )
        return RetryConfig(**dict(value))
    raise TakasumiBotToolkitConfigError(
        f"retry must be a RetryConfig or a mapping (got {type(value).__name__})",
        field="retry",
        value=value,
    )


class BasicConfig(BaseModel):
    """Client configuration. There is intentionally no ``base_url`` field.

    Args:
        timeout_ms: Per-attempt timeout in milliseconds (> 0).
        headers: Extra headers (user headers win over defaults).
        retry: RetryConfig or dict (unknown keys raise ConfigError).
        client: Caller-owned httpx.AsyncClient to inject.
        logger: LoggerLike logger; None uses a null logger.
        follow_redirects: Whether httpx follows redirects (default False).
        stock_cache: Client-wide stock list cache (NPM ``stockCache``):
            ``None``/``False`` disables it (default), ``{}`` or
            ``StockCacheConfig()`` enables it with a 60 000 ms TTL, and
            ``{"ttl_ms": n}`` sets the TTL in milliseconds.

    Examples:
        >>> cfg = BasicConfig(timeout_ms=5000)
        >>> cfg.timeout_ms
        5000
        >>> hasattr(cfg, "base_url")
        False
        >>> BasicConfig(stock_cache={}).stock_cache
        StockCacheConfig(ttl_ms=60000)
    """

    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, arbitrary_types_allowed=True
    )

    timeout_ms: int = 10_000
    headers: Mapping[str, str] | None = None
    retry: RetryConfig | None = None
    client: httpx.AsyncClient | None = None
    logger: LoggerLike | None = None
    follow_redirects: bool = False
    stock_cache: StockCacheConfig | None = None

    def __init__(self, **data: object) -> None:
        """Initialise the object."""
        if "base_url" in data:
            raise TakasumiBotToolkitConfigError(
                "base_url is not a BasicConfig field; use the TAKASUMIBOT_BASE_URL "
                "environment variable instead",
                field="base_url",
                code="UNKNOWN_CONFIG_KEY",
            )
        data = dict(data)
        if "retry" in data:
            data["retry"] = _coerce_retry(data["retry"])
        if "stock_cache" in data:
            data["stock_cache"] = _coerce_stock_cache(data["stock_cache"])
        try:
            super().__init__(**data)
        except TakasumiBotToolkitConfigError:
            raise
        except PydanticValidationError as exc:
            raise _to_config_error(exc, model_name="BasicConfig") from exc

    @model_validator(mode="after")
    def _check(self) -> BasicConfig:
        if self.timeout_ms <= 0:
            raise TakasumiBotToolkitConfigError(
                f"timeout_ms must be an int > 0 (got {self.timeout_ms})",
                field="timeout_ms",
                value=self.timeout_ms,
            )
        _validate_headers(self.headers)
        _validate_logger(self.logger)
        if self.client is not None:
            if not isinstance(self.client, httpx.AsyncClient):
                raise TakasumiBotToolkitConfigError(
                    "client must be an httpx.AsyncClient", field="client", value=self.client
                )
            if self.client.is_closed:
                raise TakasumiBotToolkitConfigError(
                    "client must not be closed", field="client", value=self.client
                )
        return self

    @property
    def effective_retry(self) -> RetryConfig:
        """Retry config with defaults applied.

        Returns:
            The configured RetryConfig or a default instance.
        """
        return self.retry if self.retry is not None else DEFAULT_RETRY_CONFIG

    @property
    def stock_cache_ttl_ms(self) -> int | None:
        """Stock list cache TTL in milliseconds, or None when the cache is off.

        Returns:
            ``stock_cache.ttl_ms`` or None.
        """
        return self.stock_cache.ttl_ms if self.stock_cache is not None else None

    @property
    def effective_logger(self) -> LoggerLike:
        """Logger with the null logger applied.

        Returns:
            The configured logger or a NullLogger.
        """
        return self.logger if self.logger is not None else NullLogger()


def resolve_base_url(environ: Mapping[str, str] | None = None) -> str:
    """Resolve the base URL (env var wins; empty means default).

    Args:
        environ: Environment mapping to read (defaults to ``os.environ``).

    Returns:
        Normalised base URL without a trailing slash.

    Raises:
        TakasumiBotToolkitConfigError: If the URL is invalid, has a non-http(s)
            scheme, or carries a path/query/fragment.

    Examples:
        >>> resolve_base_url({})
        'https://api.takasumibot.com'
        >>> resolve_base_url({"TAKASUMIBOT_BASE_URL": "http://localhost:8000/"})
        'http://localhost:8000'
    """
    env = environ if environ is not None else os.environ
    raw = env.get(BASE_URL_ENV_VAR)
    candidate = DEFAULT_BASE_URL if (raw is None or not raw.strip()) else raw.strip()
    try:
        parts = urlsplit(candidate)
        hostname = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise TakasumiBotToolkitConfigError(
            "base_url must be a valid HTTP(S) URL",
            field="base_url",
            cause=exc,
        ) from exc
    if parts.scheme not in ("http", "https"):
        raise TakasumiBotToolkitConfigError(
            f"base_url scheme must be http or https (got {parts.scheme!r})",
            field="base_url",
            value=candidate,
        )
    if parts.username is not None or parts.password is not None:
        raise TakasumiBotToolkitConfigError(
            "base_url must not contain user information",
            field="base_url",
        )
    if not parts.netloc or hostname is None:
        raise TakasumiBotToolkitConfigError(
            f"base_url must include a host (got {candidate!r})", field="base_url", value=candidate
        )
    if port is not None and not 0 <= port <= 65_535:
        raise TakasumiBotToolkitConfigError(
            "base_url port must be between 0 and 65535",
            field="base_url",
        )
    if parts.query or parts.fragment:
        raise TakasumiBotToolkitConfigError(
            "base_url must not contain a query or fragment", field="base_url", value=candidate
        )
    if parts.path not in ("", "/"):
        raise TakasumiBotToolkitConfigError(
            f"base_url must not contain a path (got {parts.path!r})",
            field="base_url",
            value=candidate,
        )
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))
