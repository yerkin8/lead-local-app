"""After messaging the bot, print recent sender IDs without printing its token."""
import os

import requests

token = os.environ.get("BOT_TOKEN")
if not token:
    raise SystemExit("Set BOT_TOKEN first")
try:
    response = requests.post(f"https://api.telegram.org/bot{token}/getUpdates", json={"limit": 20}, timeout=30)
    response.raise_for_status()
except requests.RequestException as exc:
    raise SystemExit(f"Telegram request failed ({type(exc).__name__})") from None
for update in response.json().get("result", []):
    sender = update.get("message", {}).get("from")
    if sender:
        print(sender.get("id"), sender.get("first_name", ""))
