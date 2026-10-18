"""Common base model for all API response models."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Annotated, TypeGuard, TypeVar

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, StringConstraints

_S = TypeVar("_S", bound=str)


def _parse_datetime(value: object) -> object:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("datetime must be timezone-aware")
    return value


AwareDatetime = Annotated[
    datetime, BeforeValidator(_parse_datetime), AfterValidator(_require_aware)
]
"""Timezone-aware datetime: ISO strings are parsed, naive values are rejected."""

LooseEnum = Annotated[str, StringConstraints(min_length=1)]
"""A permissive stand-in for an OpenAPI ``enum`` (NPM ``looseEnum``).

Any non-empty str is accepted, so a value the API adds after this release does
not invalidate the whole response (one new stock id would otherwise break
``get_stock_list()`` for everyone). The documented values are exported as
tuples (``GIFT_TYPES``, ``GIFT_STATUSES``, ``STOCK_IDS``); narrow with
:func:`is_known_value` where the exact set matters.
"""


def is_known_value(value: object, known: Collection[_S]) -> TypeGuard[_S]:
    """Return True when ``value`` is one of the documented enum values (NPM ``isKnownValue``).

    Args:
        value: A value read from a ``LooseEnum`` field (or anything else).
        known: The documented values, e.g. ``GIFT_STATUSES``.

    Returns:
        Whether value is a str contained in ``known``; type checkers narrow
        value to the corresponding ``Literal`` type.

    Examples:
        >>> is_known_value("received", ("received", "unused"))
        True
        >>> is_known_value("expired", ("received", "unused"))
        False
    """
    return isinstance(value, str) and value in known


class TakasumiBotModel(BaseModel):
    """Base for response models: frozen, extra allowed, strict, aware datetimes.

    Documented OpenAPI properties are required fields in the generated
    subclasses; unknown properties are kept in ``model_extra``.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="allow",
        populate_by_name=True,
        strict=True,
        allow_inf_nan=False,
        revalidate_instances="never",
    )
