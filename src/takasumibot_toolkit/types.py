"""Shared public types for takasumibot-toolkit."""

from __future__ import annotations

from typing import Protocol, TypeAlias, runtime_checkable

JSONValue: TypeAlias = "str | int | float | bool | list[JSONValue] | dict[str, JSONValue] | None"


@runtime_checkable
class LoggerLike(Protocol):
    """Structural logger protocol (stdlib ``logging`` compatible method names).

    Args:
        msg: Log message.
        **context: Structured context values (JSON-compatible scalars).
    """

    def debug(self, msg: str, /, **context: object) -> None:
        """Log at debug level."""
        ...

    def info(self, msg: str, /, **context: object) -> None:
        """Log at info level."""
        ...

    def warning(self, msg: str, /, **context: object) -> None:
        """Log at warning level."""
        ...

    def error(self, msg: str, /, **context: object) -> None:
        """Log at error level."""
        ...


class NullLogger:
    """Logger that discards all messages (default when no logger is given)."""

    def debug(self, msg: str, /, **context: object) -> None:
        """Discard a debug message."""

    def info(self, msg: str, /, **context: object) -> None:
        """Discard an info message."""

    def warning(self, msg: str, /, **context: object) -> None:
        """Discard a warning message."""

    def error(self, msg: str, /, **context: object) -> None:
        """Discard an error message."""
