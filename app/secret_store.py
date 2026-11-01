"""The OpenRouter key of the «ИИ-чат», kept per Windows user (DPAPI) in the data folder.

Same scheme as the mailing secrets (app/mailing/protect.py, not changed): the file can be decrypted only by
the Windows account that saved it, so a copied data folder or a backup does not reveal the key. The key never
goes to SQLite, logs, the page or a build: every user enters their own key in «Настройки».
"""
from __future__ import annotations

import ctypes
import logging
import os
import re
import subprocess
from pathlib import Path

from .config import DATA_DIR

ENTROPY = b"LeadEqualMoney.ai.v1"
FOLDER = DATA_DIR / "ai"
KEY_FILE = FOLDER / "openrouter.bin"
KEY_PATTERN = re.compile(r"sk-or-v1-[0-9a-fA-F]{16,}")
log = logging.getLogger("LeadEqualMoney.ai")


class SecretError(RuntimeError):
    pass


def _dpapi(name: str, data: bytes) -> bytes:
    if os.name != "nt":
        raise SecretError("Хранение ключа доступно только в Windows")
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.WinDLL("crypt32")
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data, len(data))
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    salt = ctypes.create_string_buffer(ENTROPY, len(ENTROPY))
    entropy = Blob(len(ENTROPY), ctypes.cast(salt, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    # 0x1 = CRYPTPROTECT_UI_FORBIDDEN: never show a dialog.
    if not getattr(crypt32, name)(ctypes.byref(source), None, ctypes.byref(entropy), None, None, 0x1,
                                  ctypes.byref(target)):
        raise SecretError("Сохранённый ключ не открывается этим пользователем Windows: введите ключ заново"
                          if name == "CryptUnprotectData" else "Не удалось зашифровать ключ")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel32.LocalFree(target.pbData)


def _restrict(folder: Path) -> None:
    """Leave only the current user and SYSTEM on the folder (best effort, like the mailing folder)."""
    user, domain = os.environ.get("USERNAME", ""), os.environ.get("USERDOMAIN", "")
    if os.name != "nt" or not user:
        return
    principal = f"{domain}\\{user}" if domain else user
    try:
        subprocess.run(["icacls", str(folder), "/inheritance:r", "/grant:r", f"{principal}:(OI)(CI)F",
                        "/grant:r", "*S-1-5-18:(OI)(CI)F"], capture_output=True, timeout=20,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        log.warning("Не удалось ограничить права на папку ключа ИИ")


def mask(text: str) -> str:
    """Hide any OpenRouter key in a message before it is logged or shown."""
    return KEY_PATTERN.sub("sk-or-v1-***", str(text or ""))


def save_key(key: str) -> None:
    key = str(key or "").strip()
    if not KEY_PATTERN.fullmatch(key):
        raise ValueError("Это не похоже на ключ OpenRouter: он начинается с sk-or-v1-")
    FOLDER.mkdir(parents=True, exist_ok=True)
    _restrict(FOLDER)
    temporary = KEY_FILE.with_suffix(".tmp")
    temporary.write_bytes(_dpapi("CryptProtectData", key.encode("utf-8")))
    temporary.replace(KEY_FILE)


def load_key() -> str:
    if not KEY_FILE.is_file():
        return ""
    try:
        return _dpapi("CryptUnprotectData", KEY_FILE.read_bytes()).decode("utf-8").strip()
    except SecretError:
        log.warning("Сохранённый ключ ИИ не расшифровывается этим пользователем")
        return ""


def clear_key() -> bool:
    existed = KEY_FILE.is_file()
    KEY_FILE.unlink(missing_ok=True)
    return existed
