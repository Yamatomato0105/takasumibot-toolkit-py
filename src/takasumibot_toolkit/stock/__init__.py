"""Stock derivation from ``GET /v3/stock`` (no extra HTTP paths).

See docs/README.en.md#stock-client-side-derivation. ``prices`` has no
timestamps, so history values carry no point-in-time information; the last
element is the newest price.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import TYPE_CHECKING, Final, Literal, Protocol

from takasumibot_toolkit._args import js_trim
from takasumibot_toolkit.errors import TakasumiBotToolkitValidationError
from takasumibot_toolkit.models import StockEntry

if TYPE_CHECKING:
    from takasumibot_toolkit.client import KitClient

PRICES_ORDER: Final[Literal["last_is_newest"]] = "last_is_newest"


def validate_history_limit(value: object) -> int | None:
    """Validate a stock history limit.

    Args:
        value: None (all), 0 (empty) or a positive int.

    Returns:
        The validated limit or None.

    Raises:
        TakasumiBotToolkitValidationError: For bools, non-ints or negatives.

    Examples:
        >>> validate_history_limit(3)
        3
        >>> validate_history_limit(None) is None
        True
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TakasumiBotToolkitValidationError(
            f"limit must be a non-negative int or None (got {value!r})",
            field="limit",
            value=value,
            code="INVALID_LIMIT",
        )
    if value < 0:
        raise TakasumiBotToolkitValidationError(
            f"limit must be a non-negative int (got {value})",
            field="limit",
            value=value,
            code="INVALID_LIMIT",
        )
    return value


def slice_history(prices: Sequence[int], limit: int | None = None) -> list[int]:
    """Return prices newest-first, optionally limited to the newest ``limit``.

    Args:
        prices: Prices with the newest last (PRICES_ORDER).
        limit: None for all, 0 for [], positive int for the newest n.

    Returns:
        New list, newest first. The input is never mutated.

    Examples:
        >>> slice_history([100, 110, 120])
        [120, 110, 100]
        >>> slice_history([100, 110, 120], 2)
        [120, 110]
        >>> slice_history([100, 110, 120], 0)
        []
    """
    n = validate_history_limit(limit)
    if n == 0:
        return []
    selected = list(prices) if n is None else list(prices[-n:])
    return list(reversed(selected))


def price_of(entry: StockEntry) -> int | None:
    """Latest price of an entry, or None when prices is empty.

    Args:
        entry: Stock entry.

    Returns:
        The newest price or None.

    Examples:
        >>> from takasumibot_toolkit.models import StockEntry
        >>> e = StockEntry(
        ...     name="n", id="X", description="d", dividendAmount=1,
        ...     dividendRate=0.1, prices=[10, 12])
        >>> price_of(e)
        12
    """
    return entry.prices[-1] if entry.prices else None


def history_of(entry: StockEntry, limit: int | None = None) -> list[int]:
    """Newest-first price history of an entry (no timestamps exist).

    Args:
        entry: Stock entry.
        limit: None for all, 0 for [], positive int for the newest n.

    Returns:
        Newest-first list of prices.
    """
    return slice_history(entry.prices, limit)


def dividend_of(entry: StockEntry, price: int | None = None) -> float | None:
    """Dividend per price unit: ``dividend_amount / price`` (unitless).

    Args:
        entry: Stock entry.
        price: Price to divide by; defaults to the latest price.

    Returns:
        The ratio as a float, or None when the price is None or 0.
    """
    p = price_of(entry) if price is None else price
    if p is None or p == 0:
        return None
    return entry.dividend_amount / p


def change_rate_of(entry: StockEntry) -> float | None:
    """Relative change between the newest two prices, if computable.

    Args:
        entry: Stock entry.

    Returns:
        (newest - previous) / previous, or None with <2 prices or a 0 base.
    """
    if len(entry.prices) < 2:
        return None
    prev = entry.prices[-2]
    if prev == 0:
        return None
    return (entry.prices[-1] - prev) / prev


def get_recent_stock_history(
    entry_or_prices: StockEntry | Sequence[int], limit: int | None = None
) -> list[int]:
    """Return recent (newest-first) stock history. Public helper.

    Args:
        entry_or_prices: A StockEntry or a raw prices sequence.
        limit: None for all, 0 for [], positive int for the newest n.

    Returns:
        Newest-first prices (timestamps are not available).

    Raises:
        TakasumiBotToolkitValidationError: On an invalid limit.

    Examples:
        >>> get_recent_stock_history([100, 110, 120], 2)
        [120, 110]
    """
    prices = entry_or_prices.prices if isinstance(entry_or_prices, StockEntry) else entry_or_prices
    return slice_history(prices, limit)


def summarize_stocks(entries: Sequence[StockEntry]) -> list[dict[str, object]]:
    """Summarise stock entries into plain dicts.

    Args:
        entries: Stock entries.

    Returns:
        Dicts with id, name, price, change_rate, dividend_rate and points
        (newest-first history).
    """
    out: list[dict[str, object]] = []
    for e in entries:
        out.append(
            {
                "id": e.id,
                "name": e.name,
                "price": price_of(e),
                "change_rate": change_rate_of(e),
                "dividend_rate": e.dividend_rate,
                "points": history_of(e),
            }
        )
    return out


async def list_stocks(kit: KitClient) -> list[StockEntry]:
    """Fetch the stock list through a client (``kit.get_stock_list()``).

    Args:
        kit: Kit client.

    Returns:
        The stock list as returned by the API (served from the client's stock
        cache when ``stock_cache`` is enabled).
    """
    return await kit.get_stock_list()


# ---- derivation equivalents (also exposed as KitClient methods) ----


async def get_stock_info_by_id(kit: KitClient, id: str) -> StockEntry:
    """Derive one stock entry from the list endpoint.

    Args:
        kit: Kit client.
        id: Stock id.

    Returns:
        The matching entry.

    Raises:
        TakasumiBotToolkitValidationError: If the id is invalid or not found.
    """
    return await kit.get_stock_info_by_id(id)


async def get_stock_price_by_id(kit: KitClient, id: str) -> int | None:
    """Derive the latest price for a stock id from the list endpoint.

    Args:
        kit: Kit client.
        id: Stock id.

    Returns:
        Latest price or None when prices is empty.

    Raises:
        TakasumiBotToolkitValidationError: If the id is invalid or not found.
    """
    return await kit.get_stock_price_by_id(id)


async def get_stock_history_by_id(
    kit: KitClient, id: str, *, limit: int | None = None
) -> list[int]:
    """Derive newest-first history for a stock id from the list endpoint.

    Args:
        kit: Kit client.
        id: Stock id.
        limit: None for all, 0 for [], positive int for the newest n.

    Returns:
        Newest-first prices (no timestamps are available).

    Raises:
        TakasumiBotToolkitValidationError: On an invalid limit or unknown id.
    """
    return await kit.get_stock_history_by_id(id, limit=limit)


class _StockClient(Protocol):
    async def _load_stock_entries(self) -> list[StockEntry]: ...


def validate_stock_id(value: object) -> str:
    """Validate a stock id argument and return it trimmed.

    Surrounding whitespace is removed exactly like the NPM package does
    (``String.prototype.trim()``), so ``"  JTTI "`` resolves the ``JTTI`` entry.

    Args:
        value: Candidate stock id.

    Returns:
        The trimmed id.

    Raises:
        TakasumiBotToolkitValidationError: ``INVALID_STOCK_ID`` for a non-str, an
            empty/blank id or one containing braces.

    Examples:
        >>> validate_stock_id("  JTTI ")
        'JTTI'
    """
    if not isinstance(value, str):
        raise TakasumiBotToolkitValidationError(
            f"id must be a str (got {type(value).__name__})",
            field="id",
            value=value,
            code="INVALID_STOCK_ID",
        )
    trimmed = js_trim(value)
    if not trimmed:
        raise TakasumiBotToolkitValidationError(
            "id must be a non-empty string", field="id", value=value, code="INVALID_STOCK_ID"
        )
    if "{" in trimmed or "}" in trimmed:
        raise TakasumiBotToolkitValidationError(
            "id must not contain braces", field="id", value=value, code="INVALID_STOCK_ID"
        )
    return trimmed


class StockBuilder:
    """Builder returned by ``kit.get_stock()``; performs no HTTP itself.

    Examples:
        >>> # stock = kit.get_stock().id("SOME_ID")
        >>> # await stock.info()
    """

    def __init__(self, client: KitClient) -> None:
        """Initialise the object."""
        self._client = client

    def id(self, stock_id: str) -> StockResource:
        """Bind a stock id and return its resource (no HTTP).

        Args:
            stock_id: Stock id (validated immediately).

        Returns:
            A StockResource for the id.

        Raises:
            TakasumiBotToolkitValidationError: If the id is invalid.
        """
        return StockResource(self._client, validate_stock_id(stock_id))


# Backwards-friendly alias name used in the task description.
StockSnapshot = StockEntry


class StockResource:
    """A stock id bound to a client; data is derived from the list endpoint.

    The list is loaded at most once per resource instance (plus the optional
    client-wide TTL cache). Failures are never cached and no extra retries
    are added beyond the client's retry policy.
    """

    def __init__(self, client: KitClient, stock_id: str) -> None:
        """Initialise the object."""
        self._client = client
        self._stock_id = stock_id
        self._entries: list[StockEntry] | None = None
        self._load_task: asyncio.Task[list[StockEntry]] | None = None

    @property
    def stock_id(self) -> str:
        """The bound stock id."""
        return self._stock_id

    async def _load(self) -> list[StockEntry]:
        if self._entries is not None:
            return self._entries

        task = self._load_task
        if task is None:
            task = asyncio.create_task(self._client._load_stock_entries())
            self._load_task = task
        try:
            entries = await asyncio.shield(task)
        except (Exception, asyncio.CancelledError):
            if task.done() and self._load_task is task:
                self._load_task = None
            raise
        self._entries = entries
        if self._load_task is task:
            self._load_task = None
        return entries

    async def resolve(self) -> StockEntry | None:
        """Resolve to the matching entry, or None when absent.

        Returns:
            The matching StockEntry or None.
        """
        entries = await self._load()
        for entry in entries:
            if entry.id == self._stock_id:
                return entry
        return None

    async def is_valid(self) -> bool:
        """Return True when the id exists in the stock list.

        Returns:
            Whether the id was found.
        """
        return await self.resolve() is not None

    async def info(self) -> StockEntry:
        """Return the matching stock entry.

        Returns:
            The matching StockEntry.

        Raises:
            TakasumiBotToolkitValidationError: code STOCK_NOT_FOUND when absent.
        """
        entry = await self.resolve()
        if entry is None:
            entries = await self._load()
            raise TakasumiBotToolkitValidationError(
                f"Unknown stock id {self._stock_id!r}: it is not in the returned list "
                f"({len(entries)} ids available)",
                field="id",
                value=self._stock_id,
                code="STOCK_NOT_FOUND",
            )
        return entry

    async def price(self) -> int | None:
        """Return the latest price (prices[-1]), or None when prices is empty.

        Returns:
            Latest price or None.

        Raises:
            TakasumiBotToolkitValidationError: code STOCK_NOT_FOUND when absent.
        """
        return price_of(await self.info())

    async def history(self, limit: int | None = None) -> list[int]:
        """Return newest-first prices (timestamps are not available).

        Args:
            limit: None for all, 0 for [], positive int for the newest n.

        Returns:
            Newest-first prices.

        Raises:
            TakasumiBotToolkitValidationError: On an invalid limit or unknown id.
        """
        n = validate_history_limit(limit)
        return history_of(await self.info(), n)

    async def dividend(self, price: int | None = None) -> float | None:
        """Return dividend_amount / price, or None if price is None or 0.

        Args:
            price: Price to divide by; defaults to the latest price.

        Returns:
            The unitless ratio or None.
        """
        return dividend_of(await self.info(), price)


__all__ = [
    "PRICES_ORDER",
    "StockBuilder",
    "StockResource",
    "StockSnapshot",
    "change_rate_of",
    "dividend_of",
    "get_recent_stock_history",
    "get_stock_history_by_id",
    "get_stock_info_by_id",
    "get_stock_price_by_id",
    "history_of",
    "list_stocks",
    "price_of",
    "slice_history",
    "summarize_stocks",
    "validate_history_limit",
    "validate_stock_id",
]
