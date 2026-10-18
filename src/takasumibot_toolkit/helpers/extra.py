"""Extra non-public helpers (implementation detail; not exported).

``format_percent`` is intentionally not provided: the toolkit does not guess
units, so callers write ``format_number(x * 100) + "%"`` themselves.
"""

from __future__ import annotations

from takasumibot_toolkit.helpers import _to_plain, truncate

__all__ = ["as_dict", "ellipsis"]


def ellipsis(text: str, max_length: int) -> str:
    """Truncate with the default ellipsis (non-public convenience).

    Args:
        text: Input text.
        max_length: Maximum output length.

    Returns:
        Truncated text.
    """
    return truncate(text, max_length)


def as_dict(obj: object) -> dict[str, object]:
    """Convert an object to a dict of all its fields (non-public).

    Args:
        obj: Model, mapping or object with fields.

    Returns:
        Dict of all fields.
    """
    return _to_plain(obj)
