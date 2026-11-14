"""OKX ve Telegram alanları. Yalnızca bu Windows kullanıcısı çözer."""

from __future__ import annotations

import ctypes
import json
import os
from ctypes import wintypes
from pathlib import Path

_UI_FORBIDDEN = 0x1


class _Blob(ctypes.Structure):
    _fields_ = (("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char)))


def _crypt32():
    lib = ctypes.WinDLL("crypt32", use_last_error=True)
    lib.CryptProtectData.argtypes = [
        ctypes.POINTER(_Blob),
        wintypes.LPCWSTR,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_Blob),
    ]
    lib.CryptProtectData.restype = wintypes.BOOL
    lib.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_Blob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_Blob),
    ]
    lib.CryptUnprotectData.restype = wintypes.BOOL
    return lib


def _kernel32():
    lib = ctypes.WinDLL("kernel32", use_last_error=True)
    lib.LocalFree.argtypes = [ctypes.c_void_p]
    lib.LocalFree.restype = ctypes.c_void_p
    return lib


def _protect(plain: bytes) -> bytes:
    buffer = ctypes.create_string_buffer(plain)
    incoming = _Blob(len(plain), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    outgoing = _Blob()
    if not _crypt32().CryptProtectData(
        ctypes.byref(incoming), "CryptoHub", None, None, None, _UI_FORBIDDEN, ctypes.byref(outgoing)
    ):
        raise OSError("anahtarlar şifrelenemedi")
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        _kernel32().LocalFree(outgoing.pbData)


def _unprotect(blob: bytes) -> bytes:
    buffer = ctypes.create_string_buffer(blob)
    incoming = _Blob(len(blob), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    outgoing = _Blob()
    if not _crypt32().CryptUnprotectData(
        ctypes.byref(incoming), None, None, None, None, _UI_FORBIDDEN, ctypes.byref(outgoing)
    ):
        raise OSError("anahtarlar çözülemedi")
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        _kernel32().LocalFree(outgoing.pbData)


def load_secrets(path: Path) -> dict:
    try:
        blob = path.read_bytes()
    except OSError:
        return {}
    if not blob:
        return {}
    try:
        data = json.loads(_unprotect(blob).decode("utf-8"))
    except (OSError, ValueError, UnicodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_secrets(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _protect(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
