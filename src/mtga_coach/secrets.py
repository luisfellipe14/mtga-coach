"""Store one API key on this machine, encrypted for this Windows account.

The key is protected with DPAPI (`CryptProtectData`), so the file on disk is useless
on another account or another machine. It is never written to the database, never logged
and never leaves the process except in the Authorization header of the provider call.
On a platform without DPAPI the key is refused rather than written in the clear.
"""

import ctypes
import ctypes.wintypes
import sys
from pathlib import Path

KEY_FILE = "anthropic.key"
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_char))]


def available():
    return sys.platform == "win32"


def _blob(data):
    buffer = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))


def _read_blob(blob):
    try:
        return ctypes.string_at(blob.pbData, blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob.pbData)


def protect(value):
    if not available():
        raise RuntimeError("the local vault only exists on Windows")
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(_blob(value.encode("utf-8"))), "mtga-coach", None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise RuntimeError("the key could not be encrypted for this Windows account")
    return _read_blob(out)


def unprotect(data):
    if not available():
        raise RuntimeError("the local vault only exists on Windows")
    out = _Blob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(_blob(data)), None, None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out))
    if not ok:
        raise RuntimeError("the stored key does not open on this Windows account")
    return _read_blob(out).decode("utf-8")


class KeyStore:
    def __init__(self, data_dir):
        self.path = Path(data_dir) / KEY_FILE

    def has_key(self):
        return self.path.is_file() and self.path.stat().st_size > 0

    def save(self, value):
        value = (value or "").strip()
        if not value:
            self.forget()
            return False
        self.path.write_bytes(protect(value))
        return True

    def load(self):
        if not self.has_key():
            return None
        try:
            return unprotect(self.path.read_bytes())
        except (RuntimeError, OSError):
            return None

    def forget(self):
        self.path.unlink(missing_ok=True)

    def fingerprint(self):
        """Last four characters, so the interface can confirm which key is stored."""
        key = self.load()
        return f"…{key[-4:]}" if key and len(key) >= 4 else ""
