from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data")).resolve()
DB_PATH = DATA_DIR / "app.sqlite3"
MEDIA_DIR = DATA_DIR / "media"
BOT_TOKEN = os.getenv("BOT_TOKEN", "")


def _ids(raw: str) -> list[int]:
    result = []
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if part.isdigit() and int(part) > 0 and int(part) not in result:
            result.append(int(part))
    return result


# ADMIN_IDS="111,222" lists every Telegram account allowed into the Mini App.
# OWNER_TELEGRAM_ID is kept for old deployments and is the bot's notification target.
ADMIN_IDS = tuple(_ids(os.getenv("ADMIN_IDS", "")))
OWNER_TELEGRAM_ID = int(os.getenv("OWNER_TELEGRAM_ID", "0") or "0") or (ADMIN_IDS[0] if ADMIN_IDS else 0)
if OWNER_TELEGRAM_ID and OWNER_TELEGRAM_ID not in ADMIN_IDS:
    ADMIN_IDS = (OWNER_TELEGRAM_ID, *ADMIN_IDS)
APP_ENV = os.getenv("APP_ENV", "production").strip().casefold()
INIT_DATA_MAX_AGE = int(os.getenv("INIT_DATA_MAX_AGE", "3600"))
RPC_RATE_PER_MINUTE = int(os.getenv("RPC_RATE_PER_MINUTE", "240"))
MIN_FREE_BYTES = int(os.getenv("MIN_FREE_GB", "2")) * 1024**3
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
# Dev login works only from loopback and never when APP_ENV=production.
DEV_AUTH = os.getenv("DEV_AUTH", "0") == "1" and APP_ENV != "production"
# Shared secret for the desktop «домашний агент»; the agent API is off when empty.
AGENT_TOKEN = os.getenv("AGENT_TOKEN", "").strip()

# OpenRouter for «ИИ-чат». Developers may set the key here (environment); users save their own key in «Настройки»
# (DPAPI file, app/secret_store.py). Never in SQLite, logs or backups.
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "").strip()
# Free models tried in this order: the next one is used on an upstream limit, overload, timeout or empty answer.
# Chosen by a live test on 29.10.2026, see docs/rewrite/MODELS.md.
DEFAULT_OPENROUTER_MODELS = ("nvidia/nemotron-3.5-super-vl:free,nvidia/nemotron-3-ultra-550b-a55b:free,"
                             "google/gemma-4-31b-it:free,openrouter/free")
OPENROUTER_MODELS = tuple(dict.fromkeys(
    part.strip() for part in (os.getenv("OPENROUTER_MODELS", "").strip() or DEFAULT_OPENROUTER_MODELS).split(",")
    if part.strip()))
