"""Retry policy: delay computation only (no I/O). See docs/README.en.md#retries."""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final

from takasumibot_toolkit.config import RetryConfig

# Upper bound for any computed delay in ms (the largest int a float can carry
# exactly). ``RetryConfig`` places no upper bound on ``max_delay_ms``, so this
# keeps ``compute_delay_ms`` free of OverflowError for extreme configurations.
_MAX_DELAY_MS: Final[int] = 2**53 - 1


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Parse a Retry-After header value into seconds.

    Args:
        value: Header value (delta-seconds or HTTP-date), or None.
        now: Reference time for HTTP-date values.

    Returns:
        Seconds as a float (>= 0), or None if unparseable/absent.

    Examples:
        >>> parse_retry_after("3")
        3.0
        >>> parse_retry_after("garbage") is None
        True
        >>> parse_retry_after(None) is None
        True
    """
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        dt = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    ref = now if now is not None else datetime.now(UTC)
    return max(0.0, (dt - ref).total_seconds())


def _backoff_ms(cfg: RetryConfig, attempt: int) -> float:
    """Return ``min(initial * factor**attempt, max_delay)`` without overflowing.

    ``factor ** attempt`` raises OverflowError for large attempt numbers, so the
    value is multiplied step by step and the loop stops as soon as the cap is
    reached.
    """
    cap = float(min(cfg.max_delay_ms, _MAX_DELAY_MS))
    value = float(cfg.initial_delay_ms)
    if value <= 0.0 or attempt <= 0 or cfg.backoff_factor <= 1.0 or cap <= value:
        return min(value, cap)
    steps = min(attempt, max(0, math.ceil(math.log(cap / value, cfg.backoff_factor))))
    for _ in range(steps):
        value *= cfg.backoff_factor
    return min(value, cap)


def compute_delay_ms(
    cfg: RetryConfig,
    *,
    attempt: int,
    retry_after_s: float | None = None,
    rng: random.Random | None = None,
) -> int:
    """Compute the delay before the next attempt.

    Backoff is clipped to ``max_delay_ms`` before jitter (50-100%).
    A Retry-After value takes precedence and is clipped to
    ``max_delay_ms * 3`` (jitter is not applied on top of it). Delays are
    additionally clipped to ``2**53 - 1`` ms, so extreme ``attempt`` numbers or
    configurations cannot raise OverflowError; a non-finite ``retry_after_s`` is
    ignored.

    Args:
        cfg: Retry configuration.
        attempt: Zero-based attempt index that just failed.
        retry_after_s: Parsed Retry-After seconds, if the response had one.
        rng: Random source (injectable for deterministic tests).

    Returns:
        Delay in milliseconds (ceil; at least 1 ms when the base is > 0).

    Examples:
        >>> cfg = RetryConfig(initial_delay_ms=100, max_delay_ms=5000, jitter=False)
        >>> compute_delay_ms(cfg, attempt=0)
        100
        >>> compute_delay_ms(cfg, attempt=1)
        200
        >>> compute_delay_ms(cfg, attempt=0, retry_after_s=1.0)
        1000
        >>> compute_delay_ms(cfg, attempt=1100)
        5000
    """
    clipped = _backoff_ms(cfg, attempt)
    if retry_after_s is not None and math.isfinite(retry_after_s):
        wanted_ms = max(retry_after_s * 1000.0, clipped)
        capped = min(wanted_ms, float(min(cfg.max_delay_ms * 3, _MAX_DELAY_MS)))
        if capped <= 0:
            return 0
        return max(1, math.ceil(capped))
    delay = clipped
    if cfg.jitter and clipped > 0:
        source = rng if rng is not None else random.Random()
        delay = clipped * (0.5 + 0.5 * source.random())
    if delay <= 0:
        return 0
    return max(1, math.ceil(delay))
