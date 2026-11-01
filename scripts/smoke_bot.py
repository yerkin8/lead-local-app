"""Offline bot conversation smoke test; does not call Telegram."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import bot

bot.OWNER_TELEGRAM_ID = 42
sent = []
created = []
bot.send = lambda chat, text, markup=None: sent.append((chat, text))
bot.ensure_space = lambda: None
bot.db.create_job = lambda kind, params: created.append((kind, params)) or "a" * 32


def message(text):
    bot.handle_message({"from": {"id": 42}, "chat": {"id": 42}, "text": text})


message("/images")
message("Внутри, Меню")
message("2")
message("3")
message("https://yandex.ru/maps/org/cafe/123/")
assert created[0] == ("images", {"url": "https://yandex.ru/maps/org/cafe/123/",
                                  "sections": {"Внутри": 2, "Меню": 3}})
message("/products")
message("https://yandex.ru/maps/org/cafe/123/prices/")
assert created[1][0] == "products"
message("/images")
message("Внутри")
message("1")
message("https://example.com/evil")
assert len(created) == 2
assert "HTTPS-ссылка" in sent[-1][1]
print("Bot conversation smoke test passed")
