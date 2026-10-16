from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data")).resolve()
DB_PATH = DATA_DIR / "app.sqlite3"
MEDIA_DIR = DATA_DIR / "media"
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OWNER_TELEGRAM_ID = int(os.getenv("OWNER_TELEGRAM_ID", "0") or "0")
MIN_FREE_BYTES = int(os.getenv("MIN_FREE_GB", "2")) * 1024**3
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
DEV_AUTH = os.getenv("DEV_AUTH", "0") == "1"
