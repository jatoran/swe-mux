"""Text from outside must be storable, hashable, and sendable.

Every case here is the same live defect seen from a different layer. On
2026-07-31 sessions started from a phone stopped getting titles: the pasted
prompts carried `⚠️`, the hook shim decoded the UTF-8 bytes `E2 9A A0 EF B8 8F`
with the Windows ANSI code page, and byte 0x8F — one of the five cp1252 leaves
undefined — landed as the lone surrogate `\\udc8f`. Four layers later
`json.dumps(..., ensure_ascii=False).encode()` raised `UnicodeEncodeError`,
which is a `ValueError`, so it was caught as an observer fault and the run lost
its name for good.
"""

from __future__ import annotations

import io
import json
import sys
from types import SimpleNamespace
from typing import Any, cast

from swe_mux.automation import TranscriptSliceService
from swe_mux.hook_client import _read_payload
from swe_mux.text_safety import utf8_safe, utf8_safe_value

# The exact bytes of "⚠️" (U+26A0 U+FE0F), which is what a phone paste carries.
WARNING_SIGN_UTF8 = b"\xe2\x9a\xa0\xef\xb8\x8f"
# What cp1252 + surrogateescape turned those six bytes into.
MOJIBAKE = "âš ï¸\udc8f"


def test_hook_payload_is_decoded_as_the_utf8_that_json_is() -> None:
    """The root cause: `sys.stdin.read()` uses the locale encoding, not UTF-8.

    On Windows that is cp1252 with `errors="surrogateescape"`, so every non-ASCII
    character in every hook payload arrived corrupted — silently for accents and
    curly quotes, fatally for anything whose UTF-8 contains 0x81/0x8D/0x8F/0x90/
    0x9D, because those have no cp1252 mapping and become lone surrogates.
    """
    payload = {"prompt": "⚠️ TWO HARD WARNINGS"}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    stdin = cast(Any, SimpleNamespace(buffer=io.BytesIO(raw)))
    original = sys.stdin
    sys.stdin = stdin
    try:
        decoded = _read_payload()
    finally:
        sys.stdin = original

    assert json.loads(decoded) == payload
    assert not any(0xD800 <= ord(ch) <= 0xDFFF for ch in decoded)


def test_the_locale_decode_this_replaces_really_did_produce_that_surrogate() -> None:
    """Pins the diagnosis, so a future reader does not have to re-derive it."""
    assert WARNING_SIGN_UTF8.decode("cp1252", errors="surrogateescape") == MOJIBAKE
    assert WARNING_SIGN_UTF8.decode("utf-8") == "⚠️"


def test_utf8_safe_removes_only_what_cannot_be_encoded() -> None:
    assert utf8_safe(MOJIBAKE).encode("utf-8")
    assert "\udc8f" not in utf8_safe(MOJIBAKE)
    # Real characters are not collateral: emoji, CJK and accents all survive.
    intact = "⚠️ café 日本語 \U0001f600"
    assert utf8_safe(intact) == intact
    assert utf8_safe("") == ""


def test_utf8_safe_value_reaches_strings_nested_anywhere() -> None:
    value = {"a": [{"text": MOJIBAKE}], "b": ("x", MOJIBAKE), "n": 3, "keep": None}
    safe = cast(dict[str, Any], utf8_safe_value(value))
    assert json.dumps(safe, ensure_ascii=False).encode("utf-8")
    assert safe["n"] == 3
    assert safe["keep"] is None
    assert isinstance(safe["b"], tuple)


def test_a_prompt_slice_survives_a_lone_surrogate() -> None:
    """This is the call that actually raised, at `automation.py` `from_prompt`.

    It hashes and measures the slice by encoding it, so an unencodable prompt took
    the titler down before any provider was ever contacted — which is why the
    failure looked nothing like the rate limits it was mixed in with.
    """
    slice_ = TranscriptSliceService.from_prompt(f"# {MOJIBAKE} TWO HARD WARNINGS")

    assert slice_.bytes > 0
    assert len(slice_.input_hash) == 64
    assert slice_.render().encode("utf-8")
    assert "\udc8f" not in slice_.render()


def test_annotation_and_transcript_slices_are_encodable_too() -> None:
    """The prompt is not the only text read from outside; transcripts are as well."""
    items = [{"created_at": 1.0, "content": MOJIBAKE}]
    slice_ = TranscriptSliceService.from_annotations(items)

    assert slice_.render().encode("utf-8")
    assert "\udc8f" not in slice_.render()
