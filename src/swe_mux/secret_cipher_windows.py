"""Windows DPAPI protect/unprotect, isolated so `wintypes` is never imported elsewhere.

``from ctypes import wintypes`` raises on a non-Windows host, and the structure
below is built from it at class-body evaluation - so merely importing this module
on Linux fails. That is fine and intended: nothing imports it except the DPAPI
backend, which is only constructed on Windows.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

from .secret_backends import SecretStoreError


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    return (
        _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))),
        buffer,
    )


def dpapi_protect(value: bytes) -> bytes:
    if os.name != "nt":
        raise SecretStoreError("persistent secret storage is unavailable on this platform")
    source, keepalive = _blob(value)
    result = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptProtectData(
        ctypes.byref(source), "swe-mux", None, None, None, 0, ctypes.byref(result)
    ):
        raise SecretStoreError(f"DPAPI protection failed: {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)
        del keepalive


def dpapi_unprotect(value: bytes) -> bytes:
    if os.name != "nt":
        raise SecretStoreError("persistent secret storage is unavailable on this platform")
    source, keepalive = _blob(value)
    result = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(result)
    ):
        raise SecretStoreError(f"DPAPI decryption failed: {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(result.pbData, result.cbData)
    finally:
        kernel32.LocalFree(result.pbData)
        del keepalive


__all__ = ["dpapi_protect", "dpapi_unprotect"]
