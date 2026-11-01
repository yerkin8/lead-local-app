"""Encrypt secrets with Windows DPAPI (current user) and keep the folder private."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

ENTROPY = b"LeadEqualMoney.mailing.v1"
_log = logging.getLogger("LeadEqualMoney.mailing")


class ProtectError(RuntimeError):
    pass


def _blob_api():
    import ctypes
    import ctypes.wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return ctypes, Blob, crypt32, kernel32


def _call(name: str, data: bytes) -> bytes:
    if os.name != "nt":
        raise ProtectError("Шифрование секретов доступно только в Windows")
    ctypes, Blob, crypt32, kernel32 = _blob_api()
    buffer = ctypes.create_string_buffer(data, len(data))
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    entropy_buffer = ctypes.create_string_buffer(ENTROPY, len(ENTROPY))
    entropy = Blob(len(ENTROPY), ctypes.cast(entropy_buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    function = getattr(crypt32, name)
    # Flag 0x1 = CRYPTPROTECT_UI_FORBIDDEN: never show a dialog.
    if not function(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0x1,
                    ctypes.byref(target)):
        raise ProtectError("Не удалось расшифровать данные этим пользователем Windows"
                           if name == "CryptUnprotectData" else "Не удалось зашифровать данные")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


def protect(data: bytes) -> bytes:
    return _call("CryptProtectData", data)


def unprotect(data: bytes) -> bytes:
    return _call("CryptUnprotectData", data)


def write_secret(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_bytes(protect(json.dumps(payload).encode("utf-8")))
    temporary.replace(path)


def read_secret(path: Path) -> dict | None:
    if not path.is_file():
        return None
    return json.loads(unprotect(path.read_bytes()).decode("utf-8"))


def restrict_folder(folder: Path) -> bool:
    """Leave only the current user (and SYSTEM) on the folder ACL. Returns True on success."""
    folder.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        os.chmod(folder, 0o700)
        return True
    user = os.environ.get("USERNAME", "")
    domain = os.environ.get("USERDOMAIN", "")
    if not user:
        return False
    principal = f"{domain}\\{user}" if domain else user
    try:
        result = subprocess.run(
            ["icacls", str(folder), "/inheritance:r", "/grant:r", f"{principal}:(OI)(CI)F",
             "/grant:r", "*S-1-5-18:(OI)(CI)F"],
            capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        _log.warning("Не удалось ограничить права на папку рассылки")
        return False
    if result.returncode != 0:
        _log.warning("icacls завершился с кодом %s", result.returncode)
    return result.returncode == 0
