from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from pathlib import Path

import requests

from . import db
from .config import BOT_TOKEN, OWNER_TELEGRAM_ID, WEBAPP_URL
from .media import MediaError, delete_media_files, ensure_space, job_folder, validate_org_url

log = logging.getLogger(__name__)
_sessions: dict[int, dict] = {}
_poll_thread: threading.Thread | None = None
BUTTON_COMMANDS = {
    "📷 Изображения": "/images",
    "🛍 Товары": "/products",
    "🗂 История фото": "/history_images",
    "🗂 История товаров": "/history_products",
    "❌ Отмена": "/cancel",
}


def menu_markup() -> dict:
    keyboard = [
        [{"text": "📷 Изображения"}, {"text": "🛍 Товары"}],
        [{"text": "🗂 История фото"}, {"text": "🗂 История товаров"}],
        [{"text": "❌ Отмена"}],
    ]
    if WEBAPP_URL:
        keyboard.insert(0, [{"text": "Открыть Mini App", "web_app": {"url": WEBAPP_URL}}])
    return {"keyboard": keyboard, "resize_keyboard": True,
            "is_persistent": True, "one_time_keyboard": False}


def call(method: str, payload: dict | None = None, *, file: Path | None = None) -> dict:
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN не задан")
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    try:
        if file:
            with file.open("rb") as handle:
                response = requests.post(url, data=payload, files={"document": (file.name, handle, "application/zip")}, timeout=180)
        else:
            response = requests.post(url, json=payload or {}, timeout=30)
    except requests.RequestException as exc:
        raise RuntimeError(f"Telegram API unavailable ({type(exc).__name__})") from None
    if response.status_code >= 400:
        raise RuntimeError(f"Telegram API returned HTTP {response.status_code}")
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", "Ошибка Telegram Bot API"))
    return data["result"]


def remember_sent(chat_id: int, flow_id: str | None, message: dict) -> None:
    if not flow_id:
        return
    message_id = message["message_id"]
    if not db.record_bot_flow_message(chat_id, flow_id, message_id):
        # A different workflow started while this message was in transit.
        try:
            call("deleteMessage", {"chat_id": chat_id, "message_id": message_id})
        except Exception:
            log.exception("Could not remove stale workflow message %s", message_id)


def send(chat_id: int, text: str, markup: dict | None = None,
         *, flow_id: str | None = None) -> dict:
    payload = {"chat_id": chat_id, "text": text[:4096]}
    if markup:
        payload["reply_markup"] = markup
    result = call("sendMessage", payload)
    remember_sent(chat_id, flow_id, result)
    return result


def send_archive(chat_id: int, path: Path, caption: str = "",
                 *, flow_id: str | None = None) -> None:
    result = call("sendDocument", {"chat_id": chat_id, "caption": caption}, file=path)
    remember_sent(chat_id, flow_id, result)


def delete_workflow_messages(chat_id: int, message_ids: list[int]) -> None:
    for start in range(0, len(message_ids), 100):
        chunk = message_ids[start:start + 100]
        try:
            call("deleteMessages", {"chat_id": chat_id, "message_ids": chunk})
        except Exception:
            log.exception("Could not clear workflow messages in chat %s", chat_id)


def start_workflow(sender: int, chat_id: int, message_id: int, kind: str) -> None:
    flow_id = uuid.uuid4().hex
    old_messages = db.rotate_bot_flow(chat_id, kind, flow_id)
    delete_workflow_messages(chat_id, old_messages)
    db.record_bot_flow_message(chat_id, flow_id, message_id)
    if kind == "images":
        _sessions[sender] = {"kind": kind, "step": "sections", "sections": {},
                             "chat_id": chat_id, "flow_id": flow_id}
        send(chat_id, "Какие секции скачать? Напишите через запятую: Внутри, Еда, Меню",
             menu_markup(), flow_id=flow_id)
    else:
        _sessions[sender] = {"kind": kind, "step": "url", "chat_id": chat_id,
                             "flow_id": flow_id}
        send(chat_id, "Пришлите HTTPS-ссылку на карточку Яндекс.Карт с товарами.",
             menu_markup(), flow_id=flow_id)


def stop_workflow(chat_id: int) -> None:
    old_messages = db.rotate_bot_flow(chat_id, "idle", uuid.uuid4().hex)
    delete_workflow_messages(chat_id, old_messages)


def notify_finished(job: dict) -> None:
    if not BOT_TOKEN or not OWNER_TELEGRAM_ID:
        return
    flow_id = job["params"].get("flow_id")
    if flow_id and not db.active_bot_flow(OWNER_TELEGRAM_ID, flow_id):
        # The user switched workflows; the archive remains available in history.
        return
    result = job.get("result", {})
    try:
        if job["status"] == "done":
            title = result.get("business_name", "Бизнес")
            send(OWNER_TELEGRAM_ID, f"Готово: {title}. Отправляю {len(result.get('archives', []))} архив(а).",
                 flow_id=flow_id)
            for name in result.get("archives", []):
                path = job_folder(job["id"]) / name
                send_archive(OWNER_TELEGRAM_ID, path, f"{title} · {name}", flow_id=flow_id)
        else:
            send(OWNER_TELEGRAM_ID, f"Задание остановлено: {job.get('error') or 'неизвестная ошибка'}",
                 flow_id=flow_id)
    except Exception as exc:
        log.exception("Telegram delivery failed for %s", job["id"])
        try:
            send(OWNER_TELEGRAM_ID, f"Не удалось отправить архив: {exc}. Он сохранён в истории бота.",
                 flow_id=flow_id)
        except Exception:
            log.exception("Telegram error notification failed")


def history(chat_id: int, kind: str) -> None:
    jobs = [item for item in db.list_jobs(kind, limit=15)]
    if not jobs:
        send(chat_id, "История пока пуста.")
        return
    for item in jobs:
        name = item["result"].get("business_name") or item["params"].get("url", "")
        label = {"queued": "В очереди", "running": "В работе", "done": "Готово", "failed": "Ошибка"}.get(item["status"], item["status"])
        buttons = []
        if item["status"] == "done":
            buttons.append({"text": "Скачать ещё раз", "callback_data": f"get:{item['id']}"})
        if item["status"] != "running":
            buttons.append({"text": "Удалить", "callback_data": f"del:{item['id']}"})
        markup = {"inline_keyboard": [buttons]} if buttons else None
        send(chat_id, f"{label}: {name}\nID: {item['id'][:8]}", markup)


def handle_message(message: dict) -> None:
    sender = message.get("from", {}).get("id")
    if sender != OWNER_TELEGRAM_ID:
        return
    if message.get("chat", {}).get("type") != "private":
        return
    chat_id = message["chat"]["id"]
    text = (message.get("text") or "").strip()
    command = text.split()[0].split("@")[0] if text.startswith("/") else BUTTON_COMMANDS.get(text)
    if command:
        if command in {"/images", "/products"}:
            _sessions.pop(sender, None)
            start_workflow(sender, chat_id, message["message_id"],
                           "images" if command == "/images" else "products")
            return
        _sessions.pop(sender, None)
        if command == "/start":
            stop_workflow(chat_id)
            send(chat_id, "Выберите функцию кнопкой ниже. Архивы доступны в истории.", menu_markup())
        elif command == "/history_images":
            history(chat_id, "images")
        elif command == "/history_products":
            history(chat_id, "products")
        elif command == "/cancel":
            stop_workflow(chat_id)
            send(chat_id, "Ввод отменён.", menu_markup())
        else:
            stop_workflow(chat_id)
            send(chat_id, "Выберите функцию кнопкой ниже.", menu_markup())
        return
    state = _sessions.get(sender)
    if not state:
        send(chat_id, "Выберите функцию кнопкой ниже.", menu_markup())
        return
    db.record_bot_flow_message(chat_id, state["flow_id"], message["message_id"])
    if state["step"] == "sections":
        allowed = {"внутри": "Внутри", "еда": "Еда", "меню": "Меню"}
        requested = [allowed.get(x.strip().casefold()) for x in text.split(",")]
        requested = list(dict.fromkeys(x for x in requested if x))
        if not requested:
            send(chat_id, "Укажите хотя бы одну секцию: Внутри, Еда или Меню.", flow_id=state["flow_id"])
            return
        state["requested"] = requested
        state["step"] = "count"
        send(chat_id, f"Сколько изображений скачать из секции «{requested[0]}»? Введите число от 1 до 50.",
             flow_id=state["flow_id"])
        return
    if state["step"] == "count":
        if not text.isdigit() or not 1 <= int(text) <= 50:
            send(chat_id, "Введите число от 1 до 50.", flow_id=state["flow_id"])
            return
        section = state["requested"][len(state["sections"])]
        state["sections"][section] = int(text)
        if len(state["sections"]) < len(state["requested"]):
            next_section = state["requested"][len(state["sections"])]
            send(chat_id, f"Сколько изображений скачать из секции «{next_section}»? (1–50)",
                 flow_id=state["flow_id"])
        else:
            state["step"] = "url"
            send(chat_id, "Пришлите HTTPS-ссылку на карточку бизнеса Яндекс.Карт.",
                 flow_id=state["flow_id"])
        return
    if state["step"] == "url":
        try:
            url = validate_org_url(text)
            ensure_space()
            params = {"url": url, "flow_id": state["flow_id"]}
            if state["kind"] == "images":
                params["sections"] = state["sections"]
            job_id = db.create_job(state["kind"], params)
        except MediaError as exc:
            send(chat_id, str(exc), flow_id=state["flow_id"])
            return
        _sessions.pop(sender, None)
        send(chat_id, f"Задание добавлено в очередь. ID: {job_id[:8]}. Результат придёт сюда; архив останется в истории.",
             flow_id=state["flow_id"])


def handle_callback(callback: dict) -> None:
    sender = callback.get("from", {}).get("id")
    if sender != OWNER_TELEGRAM_ID:
        return
    data = callback.get("data", "")
    match = re.fullmatch(r"(get|del|confirm):([a-f0-9]{32})", data)
    call("answerCallbackQuery", {"callback_query_id": callback["id"]})
    if not match:
        return
    action, job_id = match.groups()
    item = db.job(job_id)
    chat_id = callback["message"]["chat"]["id"]
    if not item or item["kind"] not in {"images", "products"}:
        send(chat_id, "Задание не найдено.")
        return
    if action == "del":
        send(chat_id, "Удалить все архивы и запись этого задания?", {"inline_keyboard": [[
            {"text": "Да, удалить", "callback_data": f"confirm:{job_id}"}]]})
    elif action == "confirm":
        if item["status"] == "running":
            send(chat_id, "Дождитесь завершения задания.")
            return
        if not db.prepare_media_delete(job_id):
            send(chat_id, "Задание уже запущено или удалено.")
            return
        delete_media_files(job_id)
        db.delete_job(job_id)
        send(chat_id, "Архивы и запись удалены.")
    elif action == "get":
        if item["status"] != "done":
            send(chat_id, "Архив ещё не готов.")
            return
        for name in item["result"].get("archives", []):
            call("sendDocument", {"chat_id": chat_id}, file=job_folder(job_id) / name)


def bot_loop() -> None:
    offset = None
    while True:
        try:
            payload = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
            if offset is not None:
                payload["offset"] = offset
            updates = call("getUpdates", payload)
            for update in updates:
                offset = update["update_id"] + 1
                try:
                    if "message" in update:
                        handle_message(update["message"])
                    elif "callback_query" in update:
                        handle_callback(update["callback_query"])
                except Exception:
                    log.exception("Bot update failed")
        except Exception:
            log.exception("Bot polling failed")
            time.sleep(5)


def start_bot() -> None:
    global _poll_thread
    if BOT_TOKEN and OWNER_TELEGRAM_ID and not _poll_thread:
        _poll_thread = threading.Thread(target=bot_loop, name="telegram-bot", daemon=True)
        _poll_thread.start()
