"""Internal argument normalisation shared by the client and the stock helpers."""

from __future__ import annotations

from typing import Final

# Characters removed by ECMAScript ``String.prototype.trim()`` (WhiteSpace and
# LineTerminator: TAB, LF, VT, FF, CR, SP, NBSP, every ``Zs`` code point, LS, PS
# and the BOM). Using this set instead of ``str.strip()`` trims arguments exactly
# like the NPM package: ``str.strip()`` would also drop U+001C..U+001F and U+0085
# but keep U+FEFF.
JS_WHITESPACE: Final[str] = (
    "\t\n\v\f\r \u00a0\u1680"
    "\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)


def js_trim(value: str) -> str:
    r"""Trim ``value`` like JavaScript's ``String.prototype.trim()``.

    Args:
        value: Text to trim.

    Returns:
        ``value`` without leading/trailing ECMAScript whitespace.

    Examples:
        >>> js_trim("  123456  ")
        '123456'
        >>> js_trim("\ufeffJTTI\u3000")
        'JTTI'
        >>> js_trim("\x1cA")
        '\x1cA'
    """
    return value.strip(JS_WHITESPACE)


__all__ = ["JS_WHITESPACE", "js_trim"]
