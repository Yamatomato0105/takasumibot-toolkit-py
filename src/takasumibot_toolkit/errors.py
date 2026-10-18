"""Exception hierarchy (8 public classes). See docs/README.en.md#errors.

The classes use the ``TakasumiBotToolkit*`` names of the NPM package
(``takasumibot-toolkit-js``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Final, Literal, TypeVar

ErrorCode = Literal[
    "INVALID_CONFIG",
    "UNKNOWN_CONFIG_KEY",
    "CLIENT_CLOSED",
    "CLIENT_ALREADY_CLOSED",
    "INVALID_TYPE",
    "EMPTY_ARGUMENT",
    "UNSAFE_PATH_PARAMETER",
    "INVALID_ARGUMENT",
    "INVALID_GIFT_ID",
    "INVALID_LIMIT",
    "INVALID_PAGE",
    "INVALID_PAGE_SIZE",
    "STOCK_NOT_FOUND",
    "INVALID_STOCK_ID",
    "PRICES_EMPTY",
    "HTTP_ERROR",
    "NETWORK_ERROR",
    "TIMEOUT",
    "RETRY_LIMIT_EXCEEDED",
    "EMPTY_BODY",
    "INVALID_JSON",
    "SCHEMA_MISMATCH",
    "DECODE_ERROR",
]

RETRYABLE_STATUS_CODES: Final[frozenset[int]] = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class Issue:
    """One normalised validation issue (no pydantic types leak)."""

    field: str
    message: str
    type: str


def _freeze_context(context: Mapping[str, object] | None) -> Mapping[str, object]:
    return MappingProxyType(dict(context) if context else {})


_E = TypeVar("_E", bound="TakasumiBotToolkitError")

_PICKLE_MAPPING_TAG: Final[str] = "__mappingproxy__"


def _state_for_pickle(state: Mapping[str, object]) -> dict[str, object]:
    """Replace ``MappingProxyType`` values (unpicklable) with a tagged tuple."""
    out: dict[str, object] = {}
    for key, value in state.items():
        out[key] = (
            (_PICKLE_MAPPING_TAG, dict(value)) if isinstance(value, MappingProxyType) else value
        )
    return out


def _state_from_pickle(state: Mapping[str, object]) -> dict[str, object]:
    """Inverse of :func:`_state_for_pickle`."""
    out: dict[str, object] = {}
    for key, value in state.items():
        if isinstance(value, tuple) and len(value) == 2 and value[0] == _PICKLE_MAPPING_TAG:
            inner = value[1]
            out[key] = MappingProxyType(dict(inner)) if isinstance(inner, Mapping) else inner
        else:
            out[key] = value
    return out


def _restore_error(cls: type[_E], message: str, state: dict[str, object]) -> _E:
    """Rebuild an error produced by :meth:`TakasumiBotToolkitError.__reduce__`.

    Args:
        cls: Concrete error class to rebuild.
        message: Original message (restored as ``args[0]``).
        state: Picklable instance ``__dict__``.

    Returns:
        An equivalent error instance (``__cause__``/``__context__`` are not
        carried over, matching stdlib exception pickling; ``last_error`` is).
    """
    obj = cls.__new__(cls)
    Exception.__init__(obj, message)
    obj.__dict__.update(_state_from_pickle(state))
    last_error = obj.__dict__.get("_last_error")
    if isinstance(last_error, BaseException) and obj.__cause__ is None:
        obj.__cause__ = last_error
    return obj


class TakasumiBotToolkitError(Exception):
    """Base class for all takasumibot-toolkit errors.

    Args:
        message: One-line English message including context values.
        code: Machine-readable error code.
        retryable: Whether a retry could succeed.
        cause: Original exception (also chained via ``raise ... from``).
        context: JSON-compatible structured context.

    Examples:
        >>> err = TakasumiBotToolkitError("something failed", code="HTTP_ERROR")
        >>> err.retryable
        False
    """

    def __init__(
        self,
        message: str,
        *,
        code: ErrorCode | None = None,
        retryable: bool = False,
        cause: BaseException | None = None,
        context: Mapping[str, object] | None = None,
    ) -> None:
        """Initialise the object."""
        super().__init__(message)
        self._message = message
        self._code = code
        self._retryable = retryable
        self._context = _freeze_context(context)
        self._occurred_at = datetime.now(UTC)
        if cause is not None:
            self.__cause__ = cause

    @property
    def message(self) -> str:
        """Human-readable message."""
        return self._message

    @property
    def code(self) -> ErrorCode | None:
        """Machine-readable error code, if any."""
        return self._code

    @property
    def retryable(self) -> bool:
        """Whether retrying could succeed."""
        return self._retryable

    @property
    def context(self) -> Mapping[str, object]:
        """Immutable structured context."""
        return self._context

    @property
    def cause(self) -> BaseException | None:
        """Original exception, if any (alias of ``__cause__``)."""
        return self.__cause__

    @property
    def occurred_at(self) -> datetime:
        """When the error was created (tz-aware UTC)."""
        return self._occurred_at

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable representation.

        Returns:
            Dict with type, message, code, retryable, occurred_at and context.
        """
        ctx: dict[str, object] = {}
        for key, value in self._context.items():
            if isinstance(value, (str, int, float, bool)) or value is None:
                ctx[key] = value
            else:
                ctx[key] = str(value)
        return {
            "type": type(self).__name__,
            "message": self._message,
            "code": self._code,
            "retryable": self._retryable,
            "occurred_at": self._occurred_at.isoformat(),
            "context": ctx,
        }

    def __repr__(self) -> str:
        """Return a debug representation listing key attributes."""
        return (
            f"{type(self).__name__}(message={self._message!r}, code={self._code!r}, "
            f"retryable={self._retryable!r})"
        )

    def __reduce__(self) -> tuple[object, ...]:
        """Support ``pickle``/``copy`` (``MappingProxyType`` state is re-frozen).

        Returns:
            ``(callable, args)`` rebuilding an equivalent error. ``__cause__``
            and ``__context__`` are not transferred (as with stdlib exceptions),
            except for ``RetryLimitError.last_error``, which is also restored as
            ``__cause__``.

        Examples:
            >>> import pickle
            >>> err = TakasumiBotToolkitError("boom", code="HTTP_ERROR")
            >>> pickle.loads(pickle.dumps(err)).message
            'boom'
        """
        return (
            _restore_error,
            (type(self), self._message, _state_for_pickle(self.__dict__)),
        )


class TakasumiBotToolkitConfigError(TakasumiBotToolkitError):
    """Configuration or client-lifecycle error (never retryable).

    Args:
        message: Description including the offending value.
        field: Config field name, if applicable.
        value: Offending value.
        code: Error code (default ``INVALID_CONFIG``).

    Examples:
        >>> err = TakasumiBotToolkitConfigError(
        ...     "timeout_ms must be an int > 0 (got 0)", field="timeout_ms", value=0)
        >>> err.field
        'timeout_ms'
    """

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        value: object = None,
        code: ErrorCode | None = "INVALID_CONFIG",
        cause: BaseException | None = None,
    ) -> None:
        """Initialise the object."""
        context: dict[str, object] = {}
        if field is not None:
            context["field"] = field
        if value is not None:
            context["value"] = (
                str(value) if not isinstance(value, (str, int, float, bool)) else value
            )
        super().__init__(message, code=code, retryable=False, cause=cause, context=context)
        self._field = field
        self._value = value

    @property
    def field(self) -> str | None:
        """Config field that failed validation."""
        return self._field

    @property
    def value(self) -> object:
        """Offending value."""
        return self._value


class TakasumiBotToolkitValidationError(TakasumiBotToolkitError):
    """Caller-argument validation error (never retryable).

    Args:
        message: Description including the offending value.
        field: Argument name, if applicable.
        value: Offending value.
        code: Error code (default ``INVALID_ARGUMENT``).

    Examples:
        >>> err = TakasumiBotToolkitValidationError(
        ...     "limit must be a non-negative int (got -1)",
        ...     field="limit", value=-1, code="INVALID_LIMIT")
        >>> err.code
        'INVALID_LIMIT'
    """

    def __init__(
        self,
        message: str,
        *,
        field: str | None = None,
        value: object = None,
        code: ErrorCode | None = "INVALID_ARGUMENT",
        cause: BaseException | None = None,
    ) -> None:
        """Initialise the object."""
        context: dict[str, object] = {}
        if field is not None:
            context["field"] = field
        if value is not None:
            context["value"] = (
                str(value) if not isinstance(value, (str, int, float, bool)) else value
            )
        super().__init__(message, code=code, retryable=False, cause=cause, context=context)
        self._field = field
        self._value = value

    @property
    def field(self) -> str | None:
        """Argument that failed validation."""
        return self._field

    @property
    def value(self) -> object:
        """Offending value."""
        return self._value


class TakasumiBotToolkitHttpError(TakasumiBotToolkitError):
    """Non-2xx HTTP response.

    Args:
        message: Description including status, method and url.
        status: HTTP status code.
        method: HTTP method (always ``GET`` in this toolkit).
        url: Final request URL.
        status_text: Reason phrase, if known.
        request_id: Best-effort request id (header/body), else None.
        api_code: Best-effort API code from body, else None.
        api_message: Best-effort API message from body, else None.
        headers: Allow-listed response headers only (the NPM snapshot:
            content-type, retry-after, the request-id family and
            x-ratelimit-remaining).
        json_body: Parsed JSON body, if it parsed.
        raw_body: Full response body text (NPM ``rawBody``; ``""`` for an
            empty body). None when the error was not built from a response.
        retry_after: Raw ``Retry-After`` header value (NPM ``retryAfter``).

    Examples:
        >>> err = TakasumiBotToolkitHttpError(
        ...     "GET https://x/v3/tax failed with HTTP 404", status=404, method="GET", url="https://x/v3/tax")
        >>> err.retryable
        False
    """

    def __init__(
        self,
        message: str,
        *,
        status: int,
        method: str = "GET",
        url: str = "",
        status_text: str | None = None,
        request_id: str | None = None,
        api_code: str | None = None,
        api_message: str | None = None,
        headers: Mapping[str, str] | None = None,
        json_body: object = None,
        raw_body: str | None = None,
        retry_after: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        """Initialise the object."""
        super().__init__(
            message,
            code="HTTP_ERROR",
            retryable=status in RETRYABLE_STATUS_CODES,
            cause=cause,
            context={"status": status, "method": method, "url": url},
        )
        self._status = status
        self._status_text = status_text
        self._method = method
        self._url = url
        self._request_id = request_id
        self._api_code = api_code
        self._api_message = api_message
        self._headers: Mapping[str, str] = MappingProxyType(dict(headers) if headers else {})
        self._json_body = json_body
        self._raw_body = raw_body
        self._retry_after = retry_after

    @property
    def status(self) -> int:
        """HTTP status code."""
        return self._status

    @property
    def status_code(self) -> int:
        """Alias of :attr:`status`."""
        return self._status

    @property
    def status_text(self) -> str | None:
        """HTTP reason phrase, if known."""
        return self._status_text

    @property
    def method(self) -> str:
        """HTTP method."""
        return self._method

    @property
    def url(self) -> str:
        """Final request URL."""
        return self._url

    @property
    def request_id(self) -> str | None:
        """Best-effort request id, if extracted."""
        return self._request_id

    @property
    def api_code(self) -> str | None:
        """Best-effort API error code from the body, if extracted."""
        return self._api_code

    @property
    def api_message(self) -> str | None:
        """Best-effort API error message from the body, if extracted."""
        return self._api_message

    @property
    def headers(self) -> Mapping[str, str]:
        """Allow-listed response headers."""
        return self._headers

    @property
    def raw_body(self) -> str | None:
        """Full response body text (NPM ``rawBody``; never truncated, ``""`` if empty)."""
        return self._raw_body

    @property
    def json_body(self) -> object:
        """Parsed JSON body, if it parsed."""
        return self._json_body

    @property
    def retry_after(self) -> str | None:
        """Raw ``Retry-After`` header value, if present (NPM ``retryAfter``)."""
        return self._retry_after


class TakasumiBotToolkitNetworkError(TakasumiBotToolkitError):
    """Transport-level failure (connection, protocol, redirects).

    Args:
        message: Description including method and url.
        method: HTTP method.
        url: Request URL.
        reason: Original exception class name, if any.
        retryable: Whether retrying could succeed.

    Examples:
        >>> TakasumiBotToolkitNetworkError("GET https://x failed: ConnectError", method="GET", url="https://x").retryable
        True
    """

    def __init__(
        self,
        message: str,
        *,
        method: str = "GET",
        url: str = "",
        reason: str | None = None,
        retryable: bool = True,
        cause: BaseException | None = None,
    ) -> None:
        """Initialise the object."""
        super().__init__(
            message,
            code="NETWORK_ERROR",
            retryable=retryable,
            cause=cause,
            context={"method": method, "url": url, "reason": reason or ""},
        )
        self._method = method
        self._url = url
        self._reason = reason

    @property
    def method(self) -> str:
        """HTTP method."""
        return self._method

    @property
    def url(self) -> str:
        """Request URL."""
        return self._url

    @property
    def reason(self) -> str | None:
        """Original exception class name, if any."""
        return self._reason


class TakasumiBotToolkitTimeoutError(TakasumiBotToolkitError):
    """Per-attempt timeout (never a builtin ``TimeoutError`` subclass).

    Args:
        message: Description including method, url and timeout.
        timeout_ms: Configured per-attempt timeout in milliseconds.
        phase: httpx timeout phase, if known.
        method: HTTP method.
        url: Request URL.

    Examples:
        >>> TakasumiBotToolkitTimeoutError(
        ...     "timed out", timeout_ms=10000, phase="read", method="GET", url="https://x"
        ... ).retryable
        True
    """

    def __init__(
        self,
        message: str,
        *,
        timeout_ms: int = 0,
        phase: Literal["connect", "read", "write", "pool"] | None = None,
        method: str = "GET",
        url: str = "",
        cause: BaseException | None = None,
    ) -> None:
        """Initialise the object."""
        super().__init__(
            message,
            code="TIMEOUT",
            retryable=True,
            cause=cause,
            context={"method": method, "url": url, "timeout_ms": timeout_ms},
        )
        self._timeout_ms = timeout_ms
        self._phase = phase
        self._method = method
        self._url = url

    @property
    def timeout_ms(self) -> int:
        """Configured per-attempt timeout in milliseconds."""
        return self._timeout_ms

    @property
    def phase(self) -> Literal["connect", "read", "write", "pool"] | None:
        """Timeout phase, if known."""
        return self._phase

    @property
    def method(self) -> str:
        """HTTP method."""
        return self._method

    @property
    def url(self) -> str:
        """Request URL."""
        return self._url


class TakasumiBotToolkitRetryLimitError(TakasumiBotToolkitError):
    """All retry attempts were exhausted.

    Args:
        message: Description including method and url.
        attempts: Total attempts made (``max_retries + 1``).
        max_retries: Configured retry count.
        last_error: Final underlying error (also ``__cause__``).
        delays_ms: Actual delays waited between attempts.
        method: HTTP method.
        url: Request URL.

    Examples:
        >>> cause = TakasumiBotToolkitError("boom")
        >>> err = TakasumiBotToolkitRetryLimitError(
        ...     "exhausted", attempts=4, max_retries=3, last_error=cause,
        ...     delays_ms=(100, 200, 400), method="GET", url="https://x")
        >>> err.attempts
        4
    """

    def __init__(
        self,
        message: str,
        *,
        attempts: int,
        max_retries: int,
        last_error: TakasumiBotToolkitError,
        delays_ms: tuple[int, ...] = (),
        method: str = "GET",
        url: str = "",
    ) -> None:
        """Initialise the object."""
        super().__init__(
            message,
            code="RETRY_LIMIT_EXCEEDED",
            retryable=False,
            cause=last_error,
            context={"method": method, "url": url, "attempts": attempts},
        )
        self._attempts = attempts
        self._max_retries = max_retries
        self._last_error = last_error
        self._delays_ms = tuple(delays_ms)
        self._method = method
        self._url = url

    @property
    def attempts(self) -> int:
        """Total attempts made."""
        return self._attempts

    @property
    def max_retries(self) -> int:
        """Configured retry count."""
        return self._max_retries

    @property
    def last_error(self) -> TakasumiBotToolkitError:
        """Final underlying error."""
        return self._last_error

    @property
    def delays_ms(self) -> tuple[int, ...]:
        """Actual delays waited between attempts."""
        return self._delays_ms

    @property
    def method(self) -> str:
        """HTTP method."""
        return self._method

    @property
    def url(self) -> str:
        """Request URL."""
        return self._url


class TakasumiBotToolkitResponseParseError(TakasumiBotToolkitError):
    """A 2xx response could not be decoded or validated.

    Args:
        message: Description including method, url and first issue.
        method: HTTP method.
        url: Request URL.
        status: HTTP status, if known.
        stage: ``decode`` (JSON/body) or ``validate`` (schema).
        issues: Normalised validation issues.
        code: Error code (default ``SCHEMA_MISMATCH``).
        raw_body: Full response body text (NPM ``rawBody``; ``""`` for an
            empty body). None when the error was not built from a response.

    Examples:
        >>> err = TakasumiBotToolkitResponseParseError(
        ...     "bad response", method="GET", url="https://x", status=200,
        ...     stage="decode", code="EMPTY_BODY")
        >>> err.stage
        'decode'
    """

    def __init__(
        self,
        message: str,
        *,
        method: str = "GET",
        url: str = "",
        status: int | None = None,
        stage: Literal["decode", "validate"] = "validate",
        issues: tuple[Issue, ...] = (),
        code: ErrorCode | None = "SCHEMA_MISMATCH",
        raw_body: str | None = None,
        cause: BaseException | None = None,
    ) -> None:
        """Initialise the object."""
        super().__init__(
            message,
            code=code,
            retryable=False,
            cause=cause,
            context={"method": method, "url": url, "stage": stage},
        )
        self._method = method
        self._url = url
        self._status = status
        self._stage = stage
        self._raw_body = raw_body
        self._issues = tuple(issues)

    @property
    def method(self) -> str:
        """HTTP method."""
        return self._method

    @property
    def url(self) -> str:
        """Request URL."""
        return self._url

    @property
    def status(self) -> int | None:
        """HTTP status, if known."""
        return self._status

    @property
    def stage(self) -> Literal["decode", "validate"]:
        """Failure stage: decode or validate."""
        return self._stage

    @property
    def raw_body(self) -> str | None:
        """Full response body text (NPM ``rawBody``; never truncated, ``""`` if empty)."""
        return self._raw_body

    @property
    def issues(self) -> tuple[Issue, ...]:
        """Normalised validation issues."""
        return self._issues


def is_retryable(exc: BaseException) -> bool:
    """Return whether ``exc`` is a retryable toolkit error.

    Args:
        exc: Exception to inspect.

    Returns:
        True only for toolkit errors whose ``retryable`` flag is set.

    Examples:
        >>> is_retryable(TakasumiBotToolkitError("x", retryable=True))
        True
        >>> is_retryable(ValueError("x"))
        False
    """
    return isinstance(exc, TakasumiBotToolkitError) and exc.retryable


# No short aliases (``TimeoutError``, ``ValidationError``, ...): they shadow the
# builtin ``TimeoutError`` and ``pydantic.ValidationError`` in ``import *`` and
# make ``except TimeoutError`` miss ``asyncio.timeout``.

__all__ = [
    "RETRYABLE_STATUS_CODES",
    "ErrorCode",
    "Issue",
    "TakasumiBotToolkitConfigError",
    "TakasumiBotToolkitError",
    "TakasumiBotToolkitHttpError",
    "TakasumiBotToolkitNetworkError",
    "TakasumiBotToolkitResponseParseError",
    "TakasumiBotToolkitRetryLimitError",
    "TakasumiBotToolkitTimeoutError",
    "TakasumiBotToolkitValidationError",
    "is_retryable",
]
