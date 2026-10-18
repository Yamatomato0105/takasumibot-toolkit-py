"""Pure helper functions (no I/O). See docs/README.en.md#helpers.

The helpers follow the NPM package (``takasumibot-toolkit-js``): the same
names in snake_case, the same defaults (``ja-JP`` locale, ``| --- |`` table
separators, ISO ``...000Z`` timestamps) and the same outputs. They are also
available as ``kit.helpers`` (see :class:`Helpers`).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from decimal import ROUND_HALF_UP, Context, Decimal
from typing import Final, Generic, Literal, TypeAlias, TypeVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel

from takasumibot_toolkit.errors import TakasumiBotToolkitValidationError
from takasumibot_toolkit.stock import get_recent_stock_history

T = TypeVar("T")

DEFAULT_LOCALE: Final[str] = "ja-JP"
"""Default locale of the formatting helpers (NPM ``DEFAULT_LOCALE``)."""

_JA: Final[str] = "ja-JP"
_EN: Final[str] = "en-US"
# Locales whose Intl output is reproduced exactly (matched case-insensitively).
_LOCALE_ALIASES: Final[Mapping[str, str]] = {"ja": _JA, "ja-jp": _JA, "en": _EN, "en-us": _EN}
SUPPORTED_LOCALES: Final[tuple[str, ...]] = (_JA, _EN)
"""Locales ``format_number``/``format_timestamp`` support (``ja``/``en`` also work)."""


def _invalid(
    message: str, *, field: str, value: object = None
) -> TakasumiBotToolkitValidationError:
    return TakasumiBotToolkitValidationError(
        message, field=field, value=value, code="INVALID_ARGUMENT"
    )


def _validate_keys(keys: Sequence[str], field: str) -> list[str]:
    """Normalise ``keys`` to a ``list[str]`` (a bare str is a caller mistake).

    Args:
        keys: Requested field names.
        field: Argument name used in the error.

    Returns:
        The keys as a list.

    Raises:
        TakasumiBotToolkitValidationError: If keys is a str/bytes, not iterable, or
            contains a non-str element (``"id"`` would silently be read as the
            two keys ``"i"`` and ``"d"``).
    """
    if isinstance(keys, (str, bytes, bytearray)):
        raise _invalid(
            f"{field} must be a sequence of str, not a single {type(keys).__name__} (got {keys!r})",
            field=field,
            value=keys,
        )
    try:
        values = list(keys)
    except TypeError:
        raise _invalid(
            f"{field} must be an iterable of str (got {type(keys).__name__})",
            field=field,
            value=keys,
        ) from None
    for value in values:
        if not isinstance(value, str):
            raise _invalid(
                f"{field} must contain only str (got {type(value).__name__} for {value!r})",
                field=field,
                value=value,
            )
    return values


# ---------------------------------------------------------------- truncate
def truncate(text: str, max_length: int, *, ellipsis: str = "...") -> str:
    """Truncate text to at most ``max_length`` code points.

    Args:
        text: Input text.
        max_length: Maximum output length including the ellipsis.
        ellipsis: Marker appended only when truncation happens.

    Returns:
        The original text or a truncated version.

    Raises:
        TakasumiBotToolkitValidationError: On invalid argument types/values.

    Examples:
        >>> truncate("abcdef", 5)
        'ab...'
        >>> truncate("abcdef", 6)
        'abcdef'
        >>> truncate("abc", 0)
        ''
    """
    if not isinstance(text, str):
        raise _invalid(f"text must be a str (got {type(text).__name__})", field="text", value=text)
    if isinstance(max_length, bool) or not isinstance(max_length, int):
        raise _invalid("max_length must be an int", field="max_length", value=max_length)
    if max_length < 0:
        raise _invalid(
            f"max_length must be a non-negative int (got {max_length})",
            field="max_length",
            value=max_length,
        )
    if not isinstance(ellipsis, str):
        raise _invalid("ellipsis must be a str", field="ellipsis", value=ellipsis)
    if len(text) <= max_length:
        return text
    if max_length == 0:
        return ""
    if max_length <= len(ellipsis):
        return text[:max_length]
    return text[: max_length - len(ellipsis)] + ellipsis


# ---------------------------------------------------------------- pick/omit
def _is_record(value: object) -> bool:
    """Whether ``value`` is one record (the NPM "non-null, non-array object").

    Models, mappings, dataclass instances, namedtuples and objects with fields
    are records; scalars, ``None`` and plain lists/tuples are not.
    """
    return (
        isinstance(value, (Mapping, BaseModel))
        or (is_dataclass(value) and not isinstance(value, type))
        or hasattr(value, "_asdict")
        or hasattr(value, "__dict__")
    )


def _to_plain(obj: object) -> dict[str, object]:
    """Convert a record (see :func:`_is_record`) to a plain dict."""
    if isinstance(obj, BaseModel):
        out: dict[str, object] = {}
        for name in type(obj).model_fields:
            out[name] = getattr(obj, name)
        for key, value in (obj.model_extra or {}).items():
            out[key] = value
        return out
    if isinstance(obj, Mapping):
        return {str(k): v for k, v in obj.items()}
    if is_dataclass(obj) and not isinstance(obj, type):
        return {field.name: getattr(obj, field.name) for field in fields(obj)}
    if hasattr(obj, "_asdict"):
        result = obj._asdict()
        if isinstance(result, Mapping):
            return dict(result)
    if hasattr(obj, "__dict__"):
        return dict(vars(obj))
    raise _invalid(
        f"obj must be a model, mapping or object with fields (got {type(obj).__name__})",
        field="obj",
        value=obj,
    )


def _single_object(obj: object) -> dict[str, object]:
    """``obj`` as a plain dict; like the NPM package, sequences are rejected."""
    if not _is_record(obj):
        hint = (
            "; pick_fields/omit_fields take a single object (an array is rejected in "
            "the NPM package too), so apply them to each item"
            if isinstance(obj, Iterable) and not isinstance(obj, (str, bytes, bytearray))
            else ""
        )
        raise _invalid(
            "obj must be a model, mapping, dataclass or object with fields "
            f"(got {type(obj).__name__}){hint}",
            field="obj",
            value=obj,
        )
    return _to_plain(obj)


def pick_fields(obj: object, keys: Sequence[str]) -> dict[str, object]:
    """Keep only the given canonical (snake_case) fields (NPM ``pickFields``).

    Keys that ``obj`` does not have are ignored, and the result follows the
    order of ``keys``, as in the NPM package.

    Args:
        obj: One model, mapping, dataclass, namedtuple or object with fields.
            A list or other sequence is rejected (NPM rejects arrays).
        keys: Canonical field names to keep.

    Returns:
        A new dict with the requested keys that exist on ``obj``.

    Raises:
        TakasumiBotToolkitValidationError: If obj is not a single object, or keys
            is a bare str/bytes or contains a non-str.

    Examples:
        >>> pick_fields({"id": "A", "amount": 1000, "x": 1}, ("id", "amount"))
        {'id': 'A', 'amount': 1000}
        >>> pick_fields({"id": "A", "amount": 1000}, ("amount", "id", "missing"))
        {'amount': 1000, 'id': 'A'}
    """
    plain = _single_object(obj)
    wanted = _validate_keys(keys, "keys")
    return {key: plain[key] for key in wanted if key in plain}


def omit_fields(obj: object, keys: Sequence[str]) -> dict[str, object]:
    """Drop the given canonical (snake_case) fields (NPM ``omitFields``).

    The remaining keys (extras included) keep the order of ``obj``.

    Args:
        obj: One model, mapping, dataclass, namedtuple or object with fields.
            A list or other sequence is rejected (NPM rejects arrays).
        keys: Canonical field names to drop.

    Returns:
        A new dict without the given keys.

    Raises:
        TakasumiBotToolkitValidationError: If obj is not a single object, or keys
            is a bare str/bytes or contains a non-str.

    Examples:
        >>> omit_fields({"id": "A", "amount": 1000}, ("amount",))
        {'id': 'A'}
    """
    plain = _single_object(obj)
    drop = set(_validate_keys(keys, "keys"))
    return {key: value for key, value in plain.items() if key not in drop}


# ---------------------------------------------------------------- locale
def _resolve_locale(locale: object) -> str:
    """Map a BCP 47 tag to a supported locale (``ja``/``ja-JP``/``en``/``en-US``).

    Args:
        locale: Caller-supplied locale, or None for :data:`DEFAULT_LOCALE`.

    Returns:
        The canonical tag (``"ja-JP"`` or ``"en-US"``).

    Raises:
        TakasumiBotToolkitValidationError: If locale is not a str or not supported.
    """
    if locale is None:
        return DEFAULT_LOCALE
    if not isinstance(locale, str):
        raise _invalid(
            f"locale must be a str (got {type(locale).__name__})", field="locale", value=locale
        )
    resolved = _LOCALE_ALIASES.get(locale.lower())
    if resolved is None:
        raise _invalid(
            f"locale {locale!r} is not supported; the Intl-compatible output is "
            f"reproduced for {', '.join(repr(t) for t in SUPPORTED_LOCALES)} "
            "(and 'ja'/'en')",
            field="locale",
            value=locale,
        )
    return resolved


def _group(digits: str, sep: str) -> str:
    chunks: list[str] = []
    while len(digits) > 3:
        chunks.append(digits[-3:])
        digits = digits[:-3]
    chunks.append(digits)
    return sep.join(reversed(chunks))


# ---------------------------------------------------------------- markdown
MarkdownTableAlign: TypeAlias = Literal["left", "center", "right"]
_ALIGN_SEPARATORS: Final[Mapping[str, str]] = {"left": ":---", "center": ":---:", "right": "---:"}


def _escape_cell(text: str) -> str:
    """Escape one Markdown table cell (newlines become ``<br>``, ``|`` is quoted)."""
    return (
        text.replace("\r\n", "<br>").replace("\n", "<br>").replace("\r", "<br>").replace("|", "\\|")
    )


def _cell(value: object) -> str:
    """Render one cell: None is empty, floats use repr, datetimes ISO (``...000Z``)."""
    if value is None:
        text = ""
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, datetime):
        text = format_timestamp(value)
    elif isinstance(value, float):
        text = repr(value)
    else:
        text = str(value)
    return _escape_cell(text)


def _validate_align(align: object) -> list[str] | None:
    if align is None:
        return None
    message = "align must be a sequence of 'left' | 'center' | 'right'"
    if isinstance(align, (str, bytes, bytearray)) or not isinstance(align, Iterable):
        raise _invalid(f"{message} (got {align!r})", field="align", value=align)
    values = list(align)
    for value in values:
        if not isinstance(value, str) or value not in _ALIGN_SEPARATORS:
            raise _invalid(f"{message} (got {value!r})", field="align", value=value)
    return values


def to_markdown_table(
    rows: Sequence[object],
    *,
    headers: Sequence[str] | None = None,
    align: Sequence[MarkdownTableAlign] | None = None,
) -> str:
    r"""Render rows as a Markdown table (NPM ``toMarkdownTable``).

    Validation follows the NPM order: ``rows`` must be a sequence, an empty
    one gives ``""`` right away, then the first row, ``headers``, ``align`` and
    every row are checked.

    Args:
        rows: Records (models, mappings, dataclasses, namedtuples or objects
            with fields). A scalar or list row is rejected, as in the NPM package.
        headers: Column names/order. None uses the keys of the **first** row,
            like the NPM package. Header names are escaped like cell values.
        align: Per-column alignment (``"left"``/``"center"``/``"right"``). When
            given, the separator row uses ``:---`` / ``:---:`` / ``---:`` and
            columns beyond the list are left-aligned; None keeps ``---``.

    Returns:
        Markdown table text without a trailing newline, or ``""`` for empty
        ``rows`` or zero columns. ``|`` is escaped and newlines become
        ``<br>``; None cells are empty, booleans are ``true``/``false``, floats
        use ``repr`` and datetimes the ISO form of :func:`format_timestamp`.

    Raises:
        TakasumiBotToolkitValidationError: If rows is not a sequence, a row is
            not a record, or headers/align are invalid.

    Examples:
        >>> print(to_markdown_table([{"id": "A", "amount": 1000}]))
        | id | amount |
        | --- | --- |
        | A | 1000 |
        >>> print(to_markdown_table([{"a|b": 1}]))
        | a\|b |
        | --- |
        | 1 |
        >>> print(to_markdown_table([{"name": "a", "n": 1}], align=["left", "right"]))
        | name | n |
        | :--- | ---: |
        | a | 1 |
    """
    if isinstance(rows, (str, bytes, bytearray, Mapping)) or not isinstance(rows, Iterable):
        raise _invalid(
            f"rows must be a sequence of rows (got {type(rows).__name__})",
            field="rows",
            value=type(rows).__name__,
        )
    items = list(rows)
    if not items:
        return ""
    first = items[0]
    if not _is_record(first):
        raise _invalid(
            "rows[0] must be a model, mapping, dataclass or object with fields "
            f"(got {type(first).__name__})",
            field="rows[0]",
            value=first,
        )
    cols = list(_to_plain(first)) if headers is None else _validate_keys(headers, "headers")
    aligns = _validate_align(align)
    if not cols:
        # A table without columns has no valid delimiter row (NPM returns "").
        return ""
    if aligns is None:
        separators = ["---"] * len(cols)
    else:
        separators = [
            _ALIGN_SEPARATORS[aligns[index]] if index < len(aligns) else ":---"
            for index in range(len(cols))
        ]
    lines = [
        "| " + " | ".join(_escape_cell(col) for col in cols) + " |",
        "| " + " | ".join(separators) + " |",
    ]
    for row in items:
        if not _is_record(row):
            raise _invalid(
                "every row must be a model, mapping, dataclass or object with fields "
                f"(got {type(row).__name__})",
                field="rows",
                value=row,
            )
        plain = _to_plain(row)
        lines.append("| " + " | ".join(_cell(plain.get(col)) for col in cols) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------- format_number
NumberNotation: TypeAlias = Literal["standard", "compact"]
CompactDisplay: TypeAlias = Literal["short", "long"]

# CLDR compact patterns: (power of ten, suffix). The largest unit keeps
# growing (``1e20`` is ``10,000京`` / ``1e16`` is ``10,000T``), as in ICU.
_COMPACT_PATTERNS: Final[Mapping[tuple[str, str], tuple[tuple[int, str], ...]]] = {
    (_JA, "short"): ((4, "万"), (8, "億"), (12, "兆"), (16, "京")),
    (_JA, "long"): ((4, "万"), (8, "億"), (12, "兆"), (16, "京")),
    (_EN, "short"): ((3, "K"), (6, "M"), (9, "B"), (12, "T")),
    (_EN, "long"): ((3, " thousand"), (6, " million"), (9, " billion"), (12, " trillion")),
}


@dataclass(frozen=True)
class FormatNumberOptions:
    """Options of format_number (NPM ``FormatNumberOptions``; the function takes kwargs)."""

    locale: str | None = None
    minimum_fraction_digits: int | None = None
    maximum_fraction_digits: int | None = None
    notation: NumberNotation | None = None
    compact_display: CompactDisplay | None = None


def _digit_option(value: object, name: str) -> int | None:
    if value is not None and (
        isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100
    ):
        raise _invalid(
            f"{name} must be an int in [0, 100] (got {value!r})", field=name, value=value
        )
    return value


def _fraction_digits(minimum: object, maximum: object) -> tuple[int, int] | None:
    """Resolve fraction digit bounds like ECMA-402 ``SetNumberFormatDigitOptions``.

    Returns:
        ``(minimum, maximum)``, or None when neither bound is set.
    """
    low = _digit_option(minimum, "minimum_fraction_digits")
    high = _digit_option(maximum, "maximum_fraction_digits")
    if low is None and high is None:
        return None
    if low is None:
        assert high is not None
        return 0, high
    if high is None:
        return low, max(3, low)
    if low > high:
        raise _invalid(
            f"minimum_fraction_digits must be <= maximum_fraction_digits (got {low} > {high})",
            field="minimum_fraction_digits",
            value=low,
        )
    return low, high


def _exact_context(value: Decimal, position: int = 0) -> Context:
    """A context precise enough to keep every digit of ``value`` down to ``10**position``."""
    return Context(prec=max(28, value.adjusted() - position + 3), rounding=ROUND_HALF_UP)


def _round_at(value: Decimal, position: int) -> Decimal:
    """Round a non-negative Decimal at ``10**position`` (half away from zero)."""
    return value.quantize(Decimal(1).scaleb(position), context=_exact_context(value, position))


def _scale(value: Decimal, power: int) -> Decimal:
    """``value * 10**power`` without the default 28-digit context rounding."""
    return value.scaleb(power, context=_exact_context(value, min(0, power)))


def _render_number(value: Decimal, *, minimum_fd: int, min_grouping_digits: int) -> str:
    """Render a rounded non-negative Decimal with ``,`` grouping and min fraction digits."""
    int_part, _, frac = format(value, "f").partition(".")
    frac = frac.rstrip("0")
    if len(frac) < minimum_fd:
        frac += "0" * (minimum_fd - len(frac))
    if len(int_part) >= min_grouping_digits:
        int_part = _group(int_part, ",")
    return int_part + (f".{frac}" if frac else "")


def _compact_exponent(units: tuple[tuple[int, str], ...], magnitude: int) -> tuple[int, str]:
    exponent, suffix = 0, ""
    for power, unit_suffix in units:
        if magnitude >= power:
            exponent, suffix = power, unit_suffix
    return exponent, suffix


def format_number(
    value: int | float,
    *,
    locale: str | None = None,
    minimum_fraction_digits: int | None = None,
    maximum_fraction_digits: int | None = None,
    notation: NumberNotation | None = None,
    compact_display: CompactDisplay | None = None,
) -> str:
    """Format a number like ``Intl.NumberFormat`` (NPM ``formatNumber``).

    The output reproduces ``new Intl.NumberFormat(locale, options).format(value)``
    for the supported locales (``ja-JP`` default, ``en-US``): ``,`` grouping, at
    most 3 fraction digits by default, half-away-from-zero rounding on the
    decimal value, and compact notation with ``万``/``億``/``兆``/``京`` (ja) or
    ``K``/``M``/``B``/``T`` (en). Python ints of any size are exact.

    Args:
        value: int or float (bool/None/NaN/Inf are rejected).
        locale: ``"ja-JP"`` (default), ``"en-US"``, ``"ja"`` or ``"en"``.
        minimum_fraction_digits: Minimum fraction digits (0-100).
        maximum_fraction_digits: Maximum fraction digits (0-100; default 3, or
            the minimum when that is larger).
        notation: ``"standard"`` (default) or ``"compact"``.
        compact_display: ``"short"`` (default) or ``"long"`` (compact only).

    Returns:
        The formatted string. A value that rounds to zero is rendered as ``0``,
        never ``-0`` (Intl would print ``-0``).

    Raises:
        TakasumiBotToolkitValidationError: On an invalid value, an unsupported
            locale or invalid options.

    Examples:
        >>> format_number(1234567)
        '1,234,567'
        >>> format_number(1234.5678)
        '1,234.568'
        >>> format_number(1234.5, maximum_fraction_digits=1)
        '1,234.5'
        >>> format_number(1234.5678, minimum_fraction_digits=2, maximum_fraction_digits=2)
        '1,234.57'
        >>> format_number(12345, notation="compact")
        '1.2万'
        >>> format_number(150_000_000, notation="compact")
        '1.5億'
        >>> format_number(1500, notation="compact", locale="en-US")
        '1.5K'
        >>> format_number(999_950, notation="compact", locale="en-US")
        '1M'
        >>> format_number(-0.4, maximum_fraction_digits=0)
        '0'
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid("value must be an int or float", field="value", value=value)
    if isinstance(value, float) and not math.isfinite(value):
        raise _invalid("value must be finite", field="value", value=value)
    resolved_locale = _resolve_locale(locale)
    if notation is not None and notation not in ("standard", "compact"):
        raise _invalid(
            f"notation must be 'standard' or 'compact' (got {notation!r})",
            field="notation",
            value=notation,
        )
    if compact_display is not None and compact_display not in ("short", "long"):
        raise _invalid(
            f"compact_display must be 'short' or 'long' (got {compact_display!r})",
            field="compact_display",
            value=compact_display,
        )
    digits = _fraction_digits(minimum_fraction_digits, maximum_fraction_digits)
    # ``repr`` is the shortest round-trip form, i.e. the digits Intl formats;
    # ints are exact (like a BigInt). ``copy_abs`` avoids context rounding.
    dec = Decimal(value) if isinstance(value, int) else Decimal(repr(value))
    negative = dec.is_signed()
    magnitude_value = dec.copy_abs()
    if notation != "compact":
        minimum_fd, maximum_fd = digits if digits is not None else (0, 3)
        rounded = _round_at(magnitude_value, -maximum_fd)
        text = _render_number(rounded, minimum_fd=minimum_fd, min_grouping_digits=1)
    else:
        units = _COMPACT_PATTERNS[(resolved_locale, compact_display or "short")]
        rounded, suffix, minimum_fd = _round_compact(magnitude_value, units, digits)
        # Compact notation groups only from 5 integer digits (ICU "min2").
        text = _render_number(rounded, minimum_fd=minimum_fd, min_grouping_digits=5) + suffix
    # A value that rounds to zero is never rendered as "-0" (L-2).
    return ("-" if negative and rounded != 0 else "") + text


def _round_compact(
    value: Decimal, units: tuple[tuple[int, str], ...], digits: tuple[int, int] | None
) -> tuple[Decimal, str, int]:
    """Scale and round for compact notation like ICU.

    Without explicit fraction digits the "morePrecision" rule applies (2
    significant digits, but never fewer integer digits); explicit digits round
    at ``10**-maximum``. When rounding moves the value into the next unit
    (``999_950`` -> ``1000K``) it is rescaled once (``1M``).

    Returns:
        ``(rounded scaled value, unit suffix, minimum fraction digits)``.
    """

    def round_scaled(scaled: Decimal) -> Decimal:
        if digits is not None:
            return _round_at(scaled, -digits[1])
        magnitude = scaled.adjusted() if scaled else 0
        return _round_at(scaled, min(0, magnitude - 1))

    minimum_fd = digits[0] if digits is not None else 0
    if not value:
        return _round_at(value, -minimum_fd), "", minimum_fd
    exponent, suffix = _compact_exponent(units, value.adjusted())
    rounded = round_scaled(_scale(value, -exponent))
    if rounded:
        new_exponent, new_suffix = _compact_exponent(units, rounded.adjusted() + exponent)
        if new_exponent != exponent:
            rounded = round_scaled(_scale(rounded, exponent - new_exponent))
            suffix = new_suffix
    return rounded, suffix, minimum_fd


# ---------------------------------------------------------------- format_timestamp
TimestampFormat: TypeAlias = Literal["iso", "locale", "relative"]
_TIMESTAMP_FORMATS: Final[tuple[str, ...]] = ("iso", "locale", "relative")
_UNIX_EPOCH: Final[datetime] = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class FormatTimestampOptions:
    """Options of format_timestamp (NPM ``FormatTimestampOptions``; the function takes kwargs)."""

    format: TimestampFormat | None = None
    locale: str | None = None
    time_zone: str | tzinfo | None = None


@dataclass(frozen=True)
class MarkdownTableOptions:
    """Options of to_markdown_table (NPM ``ToMarkdownTableOptions``; the function takes kwargs)."""

    headers: tuple[str, ...] | None = None
    align: tuple[MarkdownTableAlign, ...] | None = None


# NPM ``formatRelative`` thresholds: (upper bound in seconds, unit, divisor).
_RELATIVE_STEPS: Final[tuple[tuple[int, str, int], ...]] = (
    (60, "second", 1),
    (3_600, "minute", 60),
    (86_400, "hour", 3_600),
    (30 * 86_400, "day", 86_400),
    (365 * 86_400, "month", 30 * 86_400),
)
_YEAR_SECONDS: Final[int] = 365 * 86_400
_RELATIVE_JA_UNITS: Final[Mapping[str, str]] = {
    "second": "秒",
    "minute": "分",
    "hour": "時間",
    "day": "日",
    "month": "か月",
    "year": "年",
}


def _js_round(x: float) -> int:
    """``Math.round``: halves round towards +infinity (``-2.5`` -> ``-2``)."""
    floor = math.floor(x)
    return floor + 1 if x - floor >= 0.5 else floor


def _relative_text(amount: int, unit: str, locale: str) -> str:
    """Render like ``Intl.RelativeTimeFormat(locale, {numeric})`` for one unit."""
    if amount == 0:
        # NPM switches to numeric "auto" for 0; only seconds can round to 0.
        return "今" if locale == _JA else "now"
    count = _group(str(abs(amount)), ",")
    if locale == _JA:
        return f"{count} {_RELATIVE_JA_UNITS[unit]}{'前' if amount < 0 else '後'}"
    word = unit if abs(amount) == 1 else f"{unit}s"
    return f"{count} {word} ago" if amount < 0 else f"in {count} {word}"


def _relative(dt: datetime, reference: datetime, locale: str) -> str:
    diff_seconds = _js_round(
        (dt.astimezone(UTC) - reference.astimezone(UTC)) / timedelta(milliseconds=1) / 1000
    )
    unit, divisor = "year", _YEAR_SECONDS
    for limit, name, size in _RELATIVE_STEPS:
        if abs(diff_seconds) < limit:
            unit, divisor = name, size
            break
    amount = diff_seconds if divisor == 1 else _js_round(diff_seconds / divisor)
    return _relative_text(amount, unit, locale)


def _check_reference(reference: object) -> datetime:
    if reference is None:
        return datetime.now(UTC)
    if not isinstance(reference, datetime):
        raise _invalid(
            f"reference must be a datetime (got {type(reference).__name__})",
            field="reference",
            value=reference,
        )
    if reference.tzinfo is None or reference.tzinfo.utcoffset(reference) is None:
        raise _invalid("reference must be timezone-aware", field="reference", value=reference)
    return reference


def _resolve_zone(value: object, *, field: str) -> tzinfo | None:
    if value is None:
        return None
    if isinstance(value, tzinfo):
        return value
    if isinstance(value, str):
        try:
            return ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise _invalid(
                f"{field} is not a valid IANA timezone (got {value!r})", field=field, value=value
            ) from exc
    raise _invalid(
        f"{field} must be a tzinfo, an IANA timezone name or None (got {type(value).__name__})",
        field=field,
        value=value,
    )


def _to_datetime(value: object) -> datetime:
    """Convert a timestamp argument (aware datetime, ISO str, epoch ms) to datetime."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError as exc:
            raise _invalid(
                f"value is not a valid ISO 8601 timestamp (got {value!r})",
                field="value",
                value=value,
            ) from exc
    elif isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(
            "value must be a datetime, an ISO 8601 str or epoch milliseconds (int/float)",
            field="value",
            value=value,
        )
    else:
        if isinstance(value, float) and not math.isfinite(value):
            raise _invalid(
                "value must be a finite number of milliseconds", field="value", value=value
            )
        try:
            # ``new Date(ms)`` truncates fractional milliseconds towards zero.
            return _UNIX_EPOCH + timedelta(milliseconds=math.trunc(value))
        except OverflowError as exc:
            raise _invalid(
                f"value is out of the supported date range (got {value!r})",
                field="value",
                value=value,
            ) from exc
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise _invalid("value must be timezone-aware", field="value", value=value)
    return dt


def _convert(dt: datetime, zone: tzinfo | None, value: object) -> datetime:
    try:
        return dt.astimezone(zone)
    except (OverflowError, OSError, ValueError) as exc:
        # ``zone is None`` means the host's local zone, which CPython resolves
        # through the C ``localtime``; on Windows that raises ``OSError`` for
        # instants before 1970 or after the year 3000.
        hint = "; pass time_zone= to format it in an explicit zone" if zone is None else ""
        raise _invalid(
            f"value is out of the supported date range (got {value!r}){hint}",
            field="value",
            value=value,
        ) from exc


def _locale_string(local: datetime, locale: str) -> str:
    """``Date.prototype.toLocaleString(locale)`` for ja-JP / en-US."""
    if locale == _JA:
        return (
            f"{local.year}/{local.month}/{local.day} "
            f"{local.hour}:{local.minute:02d}:{local.second:02d}"
        )
    hour = local.hour % 12 or 12
    meridiem = "AM" if local.hour < 12 else "PM"
    return (
        f"{local.month}/{local.day}/{local.year}, "
        f"{hour}:{local.minute:02d}:{local.second:02d} {meridiem}"
    )


def format_timestamp(
    value: datetime | str | int | float,
    *,
    format: TimestampFormat | None = None,  # the NPM option name (shadows the builtin)
    locale: str | None = None,
    time_zone: str | tzinfo | None = None,
    reference: datetime | None = None,
) -> str:
    """Format a timestamp (NPM ``formatTimestamp``).

    ``value`` may be a tz-aware datetime, an ISO 8601 string with an offset, or
    a number of **epoch milliseconds** (int or float), exactly like the NPM
    package. Naive datetimes/strings are rejected.

    * ``format="iso"`` (default): ``YYYY-MM-DDTHH:MM:SS.sssZ`` in UTC
      (``Date.prototype.toISOString``).
    * ``format="locale"``: ``toLocaleString`` output, e.g. ``2024/1/1 9:00:00``
      (ja-JP) or ``1/1/2024, 9:00:00 AM`` (en-US), in ``time_zone`` (an IANA
      name or ``tzinfo``; default: the local time zone, as in Node).
    * ``format="relative"``: ``Intl.RelativeTimeFormat`` output such as
      ``今``, ``5 分前``, ``3 日後`` (ja-JP) or ``now``, ``5 minutes ago``
      (en-US), relative to ``reference`` (default: now, like ``Date.now()``).

    Args:
        value: Tz-aware datetime, ISO 8601 string or epoch milliseconds.
        format: ``"iso"`` (default), ``"locale"`` or ``"relative"``.
        locale: ``"ja-JP"`` (default), ``"en-US"``, ``"ja"`` or ``"en"``.
        time_zone: Time zone of ``format="locale"`` (IANA name or tzinfo).
        reference: Reference time of ``format="relative"`` (default: now). The
            NPM package always uses ``Date.now()``; this makes output repeatable.

    Returns:
        Formatted timestamp string.

    Raises:
        TakasumiBotToolkitValidationError: On naive/invalid input, an invalid
            format, locale, time zone or reference, or a value outside the
            supported date range (no raw OverflowError/TypeError escapes).

    Examples:
        >>> format_timestamp("2024-01-01T00:00:00Z")
        '2024-01-01T00:00:00.000Z'
        >>> format_timestamp(1704067200000)
        '2024-01-01T00:00:00.000Z'
        >>> format_timestamp(1704067200000, format="locale", time_zone="Asia/Tokyo")
        '2024/1/1 9:00:00'
        >>> from datetime import datetime, timedelta, timezone
        >>> now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        >>> format_timestamp(now - timedelta(minutes=5), format="relative", reference=now)
        '5 分前'
        >>> format_timestamp(now, format="relative", reference=now, locale="en-US")
        'now'
    """
    if locale is not None and not isinstance(locale, str):
        raise _invalid(
            f"locale must be a str (got {type(locale).__name__})", field="locale", value=locale
        )
    dt = _to_datetime(value)
    output = "iso" if format is None else format
    if output == "iso":
        utc = _convert(dt, UTC, value)
        return (
            f"{utc.year:04d}-{utc.month:02d}-{utc.day:02d}T"
            f"{utc.hour:02d}:{utc.minute:02d}:{utc.second:02d}.{utc.microsecond // 1000:03d}Z"
        )
    if output == "locale":
        resolved = _resolve_locale(locale)
        zone = _resolve_zone(time_zone, field="time_zone")
        return _locale_string(_convert(dt, zone, value), resolved)
    if output == "relative":
        resolved = _resolve_locale(locale)
        return _relative(dt, _check_reference(reference), resolved)
    raise _invalid(
        f"format must be one of {' | '.join(repr(f) for f in _TIMESTAMP_FORMATS)} (got {output!r})",
        field="format",
        value=output,
    )


# ---------------------------------------------------------------- paginate
class Page(BaseModel, Generic[T]):
    """One page of items (1-based), mirroring the NPM ``PaginateResult``.

    Attributes:
        items: The items of this page (empty when out of range).
        page: Requested page number (1-based, echoed back).
        page_size: Page size (echoed back).
        total_items: Total number of items (NPM ``totalItems``).
        total_pages: Total number of pages (0 when there is no item).
        has_next: Whether a next page exists (``page < total_pages``).
        has_prev: Whether a previous page exists (``page > 1``).
    """

    model_config = {"frozen": True}

    items: list[T]
    page: int
    page_size: int
    total_items: int
    total_pages: int
    has_next: bool
    has_prev: bool


def _validate_page(page: object, page_size: object) -> tuple[int, int]:
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise TakasumiBotToolkitValidationError(
            f"page must be an int >= 1 (got {page!r})",
            field="page",
            value=page,
            code="INVALID_PAGE",
        )
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size <= 0:
        raise TakasumiBotToolkitValidationError(
            f"page_size must be an int > 0 (got {page_size!r})",
            field="page_size",
            value=page_size,
            code="INVALID_PAGE_SIZE",
        )
    return page, page_size


def paginate(items: Sequence[T], page: int = 1, page_size: int = 25) -> Page[T]:
    """Return a 1-based page slice of ``items`` (NPM ``paginate(items, page, pageSize)``).

    Args:
        items: Items to page (a real sequence, not mutated). Mappings and
            str/bytes are rejected instead of being paged key-wise/char-wise.
        page: 1-based page number (positional or keyword).
        page_size: Items per page (positional or keyword).

    Returns:
        Page with items, total_items, total_pages, has_next and has_prev.
        Out-of-range pages are empty.

    Raises:
        TakasumiBotToolkitValidationError: If items is not a sequence, page < 1 or
            page_size <= 0.

    Examples:
        >>> result = paginate([1, 2, 3, 4, 5], 2, 2)
        >>> result.items, result.total_items, result.total_pages
        ([3, 4], 5, 3)
        >>> result.has_next, result.has_prev
        (True, True)
    """
    if isinstance(items, (str, bytes, bytearray, Mapping)) or not isinstance(items, Sequence):
        raise TakasumiBotToolkitValidationError(
            f"items must be a sequence (got {type(items).__name__})",
            field="items",
            value=type(items).__name__,
            code="INVALID_ARGUMENT",
        )
    page, page_size = _validate_page(page, page_size)
    total = len(items)
    total_pages = math.ceil(total / page_size) if total else 0
    start = (page - 1) * page_size
    sliced = list(items[start : start + page_size])
    return Page[T](
        items=sliced,
        page=page,
        page_size=page_size,
        total_items=total,
        total_pages=total_pages,
        has_next=page < total_pages,
        has_prev=page > 1,
    )


# ---------------------------------------------------------------- kit.helpers
_truncate = truncate
_pick_fields_fn = pick_fields
_omit_fields_fn = omit_fields
_to_markdown_table = to_markdown_table
_format_number = format_number
_format_timestamp = format_timestamp
_paginate = paginate
_get_recent_stock_history = get_recent_stock_history


class Helpers:
    """All pure helpers as one read-only namespace (NPM ``Helpers``).

    An instance is available as ``kit.helpers`` (and as :data:`HELPERS`), so
    helpers can be called without extra imports. The attributes are the very
    same functions as the module-level ones.

    Examples:
        >>> HELPERS.truncate("hello world", 5)
        'he...'
        >>> HELPERS.paginate([1, 2, 3], 1, 2).has_next
        True
    """

    __slots__ = ()

    truncate = staticmethod(_truncate)
    pick_fields = staticmethod(_pick_fields_fn)
    omit_fields = staticmethod(_omit_fields_fn)
    to_markdown_table = staticmethod(_to_markdown_table)
    format_number = staticmethod(_format_number)
    format_timestamp = staticmethod(_format_timestamp)
    paginate = staticmethod(_paginate)
    get_recent_stock_history = staticmethod(_get_recent_stock_history)


HELPERS: Final[Helpers] = Helpers()
"""The shared :class:`Helpers` instance returned by ``kit.helpers``."""


__all__ = [
    "DEFAULT_LOCALE",
    "HELPERS",
    "SUPPORTED_LOCALES",
    "CompactDisplay",
    "FormatNumberOptions",
    "FormatTimestampOptions",
    "Helpers",
    "MarkdownTableAlign",
    "MarkdownTableOptions",
    "NumberNotation",
    "Page",
    "TimestampFormat",
    "format_number",
    "format_timestamp",
    "get_recent_stock_history",
    "omit_fields",
    "paginate",
    "pick_fields",
    "to_markdown_table",
    "truncate",
]
