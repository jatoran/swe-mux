"""Making outside text safe to store, hash, and send.

Everything here exists because a single un-encodable character used to be fatal
several layers away from where it entered. Text arrives from agent CLIs, hook
payloads, transcripts on disk, and phones; any of those can hand us a *lone
surrogate* (U+D800-U+DFFF outside a valid pair), which is a legal Python `str`
character and not encodable as UTF-8 at all.

The failure is always the same shape and always far from the cause: some later
`.encode()` raises `UnicodeEncodeError`, which is a `ValueError`, so it is caught
by a broad handler and reported as that layer's problem. Measured 2026-07-31: a
phone-pasted prompt containing an emoji left three sessions permanently nameless,
and the error surfaced as an observer failure at a byte offset inside a JSON blob.

Scrubbing at the boundary is what makes the property hold everywhere downstream
instead of being re-argued at each `.encode()` call.
"""

from __future__ import annotations

import re

# Lone surrogates only. A valid astral character is a single code point in Python
# and never matches, so emoji, CJK, and accents pass through untouched.
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")
REPLACEMENT = "�"


def utf8_safe(text: str) -> str:
    """`text` with anything UTF-8 cannot represent replaced by U+FFFD.

    Cheap enough to call on every boundary crossing: the common case is a scan
    that finds nothing and returns the original object unchanged.
    """
    if not text:
        return text
    return _LONE_SURROGATE.sub(REPLACEMENT, text)


def utf8_safe_value(value: object) -> object:
    """`utf8_safe` applied through the strings of a JSON-shaped value.

    Used where a whole payload is about to be serialized and any string in it
    could carry the damage — the caller should not have to know which field.
    """
    if isinstance(value, str):
        return utf8_safe(value)
    if isinstance(value, dict):
        return {utf8_safe(str(key)): utf8_safe_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [utf8_safe_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(utf8_safe_value(item) for item in value)
    return value
