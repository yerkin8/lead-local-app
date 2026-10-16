"""Personal Telegram client for reviewed, local campaign drafts."""

from __future__ import annotations

import asyncio
import json
import random
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

from . import db
from .config import DATA_DIR
from .domain import render_template

SETTINGS = DATA_DIR / "telegram.json"
POLICY_SETTINGS = DATA_DIR / "send_policy.json"
SESSION = DATA_DIR / "personal_telegram"
_send_lock = threading.Lock()
_send_threads_lock = threading.Lock()
_send_threads: set[threading.Thread] = set()
DAILY_LIMIT_DEFAULT = 5
SEND_DELAY_DEFAULT = 60
OPT_OUT_FOOTER = "Если сообщение неактуально, ответьте «СТОП» — больше писать не буду."


class SendPolicyError(ValueError):
    """A local daily-limit or pacing guard prevented a send attempt."""


def _telethon():
    try:
        from telethon import TelegramClient
        from telethon.errors import FloodWaitError, PeerFloodError
    except ImportError as exc:
        raise RuntimeError("Установите зависимости настольной версии: pip install -r requirements-desktop.txt") from exc
    return TelegramClient, FloodWaitError, PeerFloodError


def send_policy() -> dict:
    defaults = {"daily_limit": DAILY_LIMIT_DEFAULT, "delay_seconds": SEND_DELAY_DEFAULT}
    try:
        saved = json.loads(POLICY_SETTINGS.read_text(encoding="utf-8"))
        daily_limit = int(saved.get("daily_limit", defaults["daily_limit"]))
        delay_seconds = int(saved.get("delay_seconds", defaults["delay_seconds"]))
        if not 1 <= daily_limit <= 20 or not 30 <= delay_seconds <= 3600:
            return defaults
        return {"daily_limit": daily_limit, "delay_seconds": delay_seconds}
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return defaults


def configure_send_policy(daily_limit: int, delay_seconds: int) -> dict:
    try:
        daily_limit, delay_seconds = int(daily_limit), int(delay_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("Укажите целые числа для дневного лимита и паузы") from exc
    if not 1 <= daily_limit <= 20:
        raise ValueError("Дневной лимит должен быть от 1 до 20 сообщений")
    if not 30 <= delay_seconds <= 3600:
        raise ValueError("Пауза между сообщениями должна быть от 30 до 3600 секунд")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    POLICY_SETTINGS.write_text(json.dumps({"daily_limit": daily_limit,
                                          "delay_seconds": delay_seconds}), encoding="utf-8")
    return send_policy()


def _normalized_peer(peer: str) -> str:
    value = str(peer or "").strip()
    if value.startswith("phone:"):
        return value.casefold()
    return value.lstrip("@").casefold()


def _with_opt_out(message: str) -> str:
    message = str(message or "").strip()
    if not message:
        return ""
    if OPT_OUT_FOOTER.casefold() in message.casefold():
        return message
    return f"{message}\n\n{OPT_OUT_FOOTER}"


def _protect(value: str) -> str:
    if not value:
        return ""
    if __import__("os").name != "nt":
        raise RuntimeError("Хранение Telegram-ключа доступно только в Windows")
    import ctypes
    import ctypes.wintypes
    import base64

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    raw = value.encode()
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), None, None, None, None, 0,
                                                   ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        return base64.b64encode(ctypes.string_at(target.pbData, target.cbData)).decode()
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def _unprotect(value: str) -> str:
    if not value:
        return ""
    import ctypes
    import ctypes.wintypes
    import base64

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    raw = base64.b64decode(value)
    buffer = ctypes.create_string_buffer(raw)
    source = Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    ctypes.windll.kernel32.LocalFree.restype = ctypes.c_void_p
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0,
                                                     ctypes.byref(target)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(target.pbData, target.cbData).decode()
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def configure(api_id: int, api_hash: str, phone: str) -> None:
    if int(api_id) <= 0 or not re.fullmatch(r"[a-fA-F0-9]{32}", api_hash.strip()):
        raise ValueError("Нужны ваш Telegram api_id и api_hash")
    if not re.fullmatch(r"\+\d{10,15}", phone.strip()):
        raise ValueError("Номер укажите в международном формате")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps({"api_id": int(api_id), "api_hash": _protect(api_hash.strip()),
                                    "phone": phone.strip()}), encoding="utf-8")


def _settings() -> dict:
    if not SETTINGS.is_file():
        raise ValueError("API ID не указан. Откройте Настройки и введите свои Telegram api_id и api_hash.")
    try:
        item = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Не удалось прочитать настройки Telegram. Проверьте их в разделе Настройки.") from exc
    try:
        item["api_id"] = int(item.get("api_id", 0))
    except (TypeError, ValueError) as exc:
        raise ValueError("API ID не указан. Введите свой api_id в разделе Настройки.") from exc
    if item["api_id"] <= 0:
        raise ValueError("API ID не указан. Введите свой api_id в разделе Настройки.")
    protected_hash = item.get("api_hash")
    if not protected_hash:
        raise ValueError("API hash не указан. Введите свой api_hash в разделе Настройки.")
    try:
        item["api_hash"] = _unprotect(protected_hash)
    except Exception as exc:
        raise ValueError("Не удалось прочитать сохранённый api_hash. Введите его заново в разделе Настройки.") from exc
    if not re.fullmatch(r"[a-fA-F0-9]{32}", item["api_hash"]):
        raise ValueError("API hash не распознан. Проверьте его в разделе Настройки.")
    return item


def state() -> dict:
    session_exists = SESSION.with_suffix(".session").exists()
    try:
        item = _settings()
    except ValueError as exc:
        message = str(exc)
        return {"configured": False, "phone": "", "session_exists": session_exists,
                "api_id_missing": "API ID не указан" in message,
                "configuration_error": message, **send_policy()}
    return {"configured": True, "api_id_missing": False, "configuration_error": "",
            "phone": item.get("phone", ""), "session_exists": session_exists, **send_policy()}


async def _client():
    TelegramClient, _, _ = _telethon()
    settings = _settings()
    client = TelegramClient(str(SESSION), settings["api_id"], settings["api_hash"],
                            timeout=30, connection_retries=1, request_retries=0,
                            retry_delay=2, auto_reconnect=False, flood_sleep_threshold=0)
    try:
        await _telegram_call(client.connect)
    except Exception:
        await client.disconnect()
        raise
    return client, settings


def _campaign_status(campaign_id: str) -> str | None:
    with db.connection() as conn:
        row = conn.execute("SELECT status FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    return row[0] if row else None


async def _cooperative_sleep(seconds: int, campaign_id: str | None = None) -> bool:
    deadline = time.monotonic() + max(0, seconds)
    while time.monotonic() < deadline:
        if campaign_id and _campaign_status(campaign_id) != "running":
            return False
        await asyncio.sleep(min(1, max(0, deadline - time.monotonic())))
    return not campaign_id or _campaign_status(campaign_id) == "running"


async def _telegram_call(operation, *, campaign_id: str | None = None,
                         business_id: str | None = None):
    """Retry only after Telegram's explicit wait has fully elapsed."""
    _TelegramClient, FloodWaitError, _PeerFloodError = _telethon()
    while True:
        try:
            return await operation()
        except FloodWaitError as exc:
            seconds = max(1, int(getattr(exc, "seconds", 1)))
            if campaign_id and business_id:
                with db.connection() as conn:
                    conn.execute("""UPDATE campaign_recipients SET status='ready',error=?
                        WHERE campaign_id=? AND business_id=?""",
                        (f"Telegram просит подождать {seconds} секунд; ожидание соблюдается", campaign_id, business_id))
            __import__("logging").getLogger(__name__).warning(
                "Telegram FloodWait received; waiting %s seconds before retry", seconds)
            if not await _cooperative_sleep(seconds, campaign_id):
                return None


def request_code() -> dict:
    async def run():
        client, settings = await _client()
        try:
            if await client.is_user_authorized():
                return {"authorized": True}
            sent = await _telegram_call(lambda: client.send_code_request(settings["phone"]))
            # Hash is a short-lived Telegram login token, never sent to the UI.
            (DATA_DIR / "login_state.json").write_text(json.dumps({"phone_code_hash": _protect(sent.phone_code_hash)}), encoding="utf-8")
            return {"authorized": False, "code_sent": True}
        finally:
            await client.disconnect()
    return asyncio.run(run())


def sign_in(code: str, password: str = "") -> dict:
    async def run():
        from telethon.errors import SessionPasswordNeededError
        client, settings = await _client()
        try:
            state_file = DATA_DIR / "login_state.json"
            if not state_file.is_file():
                raise ValueError("Сначала запросите код")
            login_state = json.loads(state_file.read_text(encoding="utf-8"))
            if login_state.get("password_required"):
                if not password:
                    return {"password_required": True}
                await _telegram_call(lambda: client.sign_in(password=password))
            else:
                code_hash = _unprotect(login_state["phone_code_hash"])
                try:
                    await _telegram_call(lambda: client.sign_in(settings["phone"], code.strip(),
                                                                 phone_code_hash=code_hash))
                except SessionPasswordNeededError:
                    login_state["password_required"] = True
                    state_file.write_text(json.dumps(login_state), encoding="utf-8")
                    if not password:
                        return {"password_required": True}
                    await _telegram_call(lambda: client.sign_in(password=password))
            state_file.unlink(missing_ok=True)
            return {"authorized": await client.is_user_authorized()}
        finally:
            await client.disconnect()
    return asyncio.run(run())


def recipient_peer(business: dict) -> str | None:
    for entry in business.get("socials", []):
        value = entry.get("url", "") if isinstance(entry, dict) else ""
        parsed = urlparse(value)
        if parsed.hostname in {"t.me", "telegram.me"}:
            handle = parsed.path.strip("/").split("/")[0]
            if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{4,31}", handle):
                return handle
    if business.get("telegram"):
        return "phone:" + business["id"]
    return None


def check_phone(business_id: str, phone: str) -> dict:
    """Explicit lookup: temporarily imports a number only when it is not already a contact."""
    business = db.business(business_id)
    if not business:
        raise ValueError("Организация не найдена")
    allowed = {re.sub(r"\D", "", str(value)) for value in business.get("phones", [])}
    digits = re.sub(r"\D", "", phone)
    if digits not in allowed or not 10 <= len(digits) <= 15:
        raise ValueError("Выберите номер из карточки организации")
    number = "+" + digits

    async def run():
        from telethon.tl.functions.contacts import DeleteContactsRequest, GetContactsRequest, ImportContactsRequest
        from telethon.tl.types import InputPhoneContact, InputUser
        client, _settings_data = await _client()
        imported_user = None
        result = None
        cleanup_error = None
        try:
            if not await client.is_user_authorized():
                raise ValueError("Сначала войдите в Telegram")
            contacts = await _telegram_call(lambda: client(GetContactsRequest(hash=0)))
            user = next((candidate for candidate in contacts.users
                         if re.sub(r"\D", "", getattr(candidate, "phone", "") or "") == digits), None)
            if not user:
                request = ImportContactsRequest([InputPhoneContact(
                    client_id=random.getrandbits(63), phone=number,
                    first_name="LEM", last_name="Check")])
                response = await _telegram_call(lambda: client(request))
                user = response.users[0] if response.users else None
                imported_ids = {entry.user_id for entry in response.imported}
                imported_user = user if user and user.id in imported_ids else None
            if not user or not user.access_hash:
                result = {"found": False, "phone": number}
            else:
                with db.connection() as conn:
                    conn.execute("""INSERT INTO business_telegram
                        (business_id,telegram_id,access_hash,username,phone,method,checked_at)
                        VALUES (?,?,?,?,?,?,?) ON CONFLICT(business_id) DO UPDATE SET
                        telegram_id=excluded.telegram_id,access_hash=excluded.access_hash,
                        username=excluded.username,phone=excluded.phone,method=excluded.method,
                        checked_at=excluded.checked_at""",
                        (business_id, str(user.id), _protect(str(user.access_hash)),
                         user.username, number, "phone", db.now()))
                result = {"found": True, "telegram_id": str(user.id), "username": user.username or "",
                          "phone": number}
        finally:
            if imported_user and imported_user.access_hash:
                try:
                    await _telegram_call(lambda: client(DeleteContactsRequest([
                        InputUser(imported_user.id, imported_user.access_hash)])))
                except Exception as exc:
                    cleanup_error = exc
                    __import__("logging").getLogger(__name__).exception("Could not remove temporary Telegram contact")
            await client.disconnect()
        if cleanup_error:
            raise RuntimeError("Номер проверен, но Telegram не подтвердил удаление временного контакта. "
                               "Удалите его вручную в контактах Telegram.") from cleanup_error
        return result

    return asyncio.run(run())


async def _resolve_entity(client, peer: str, *, campaign_id: str | None = None,
                          business_id: str | None = None):
    if peer.startswith("phone:"):
        from telethon.tl.types import InputPeerUser
        business_id = peer.split(":", 1)[1]
        with db.connection() as conn:
            row = conn.execute("SELECT telegram_id,access_hash FROM business_telegram WHERE business_id=?",
                               (business_id,)).fetchone()
        if not row:
            raise ValueError("Проверка телефона устарела; проверьте номер заново")
        return await _telegram_call(lambda: client.get_entity(
            InputPeerUser(int(row["telegram_id"]), int(_unprotect(row["access_hash"])))),
            campaign_id=campaign_id, business_id=business_id)
    return await _telegram_call(lambda: client.get_entity(peer),
                                campaign_id=campaign_id, business_id=business_id)


def create_draft(title: str, business_ids: list[str], template_slug: str,
                 overrides: dict[str, str] | None = None) -> dict:
    if not business_ids:
        raise ValueError("Сначала выберите хотя бы одну организацию")
    template = next((item for item in db.templates() if item["slug"] == template_slug), None)
    if not template:
        raise ValueError("Скрипт не найден")
    entries = []
    suppressed = db.suppressed_ids()
    for identity in dict.fromkeys(business_ids):
        if identity in suppressed:
            continue
        business = db.business(identity)
        if not business:
            continue
        peer = recipient_peer(business)
        if not peer:
            continue
        saved_telegram = business.get("telegram") or {}
        permission = db.contact_permission(_normalized_peer(peer), saved_telegram.get("telegram_id"),
                                           saved_telegram.get("phone"))
        if permission and permission.get("status") == "blocked":
            continue
        message = (overrides or {}).get(identity) or render_template(template["body"], business)
        entries.append((identity, peer, _with_opt_out(message)))
    if not entries:
        raise ValueError("У выбранных организаций нет публичной ссылки Telegram")
    campaign_id = uuid.uuid4().hex
    with db.connection() as conn:
        conn.execute("""INSERT INTO campaigns
            (id,title,status,template_slug,created_at,updated_at) VALUES (?,?,?,?,?,?)""",
                     (campaign_id, title.strip() or "Новая рассылка", "draft", template_slug,
                      db.now(), db.now()))
        conn.executemany("INSERT INTO campaign_recipients (campaign_id,business_id,peer,message) VALUES (?,?,?,?)",
                         [(campaign_id, *entry) for entry in entries])
    return campaign(campaign_id)


def campaign(campaign_id: str) -> dict:
    with db.connection() as conn:
        row = conn.execute("SELECT id,title,status,template_slug,created_at,updated_at,error "
                            "FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if not row:
            raise ValueError("Рассылка не найдена")
        recipients = [dict(item) for item in conn.execute(
            """SELECT r.*,b.name AS business_name,bt.phone AS phone FROM campaign_recipients r
               JOIN businesses b ON b.id=r.business_id
               LEFT JOIN business_telegram bt ON bt.business_id=r.business_id
               WHERE r.campaign_id=? ORDER BY r.rowid""", (campaign_id,))]
    suppressed = db.suppressed_ids()
    for recipient in recipients:
        permission = db.contact_permission(_normalized_peer(recipient["peer"]), recipient.get("telegram_id"),
                                           recipient.get("phone"))
        recipient["permission_status"] = "blocked" if recipient["business_id"] in suppressed else (
            permission["status"] if permission else "missing")
        recipient["permission_source"] = permission.get("source", "") if permission else ""
        recipient["permission_note"] = permission.get("note", "") if permission else ""
    return {**dict(row), "recipients": recipients}


def campaigns() -> list[dict]:
    with db.connection() as conn:
        rows = conn.execute("""SELECT c.id,c.title,c.status,c.template_slug,c.created_at,c.updated_at,c.error,
            COUNT(r.business_id) AS recipients,
            SUM(CASE WHEN r.status='sent' THEN 1 ELSE 0 END) AS sent FROM campaigns c
            LEFT JOIN campaign_recipients r ON r.campaign_id=c.id GROUP BY c.id
            ORDER BY c.created_at DESC""").fetchall()
    return [dict(row) for row in rows]


def update_recipient(campaign_id: str, business_id: str, message: str) -> dict:
    if not message.strip() or re.search(r"\[[^\]]+\]", message):
        raise ValueError("Заполните поля в квадратных скобках")
    with db.connection() as conn:
        row = conn.execute("SELECT status FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        if not row or row[0] != "draft":
            raise ValueError("Редактировать можно только черновик")
        recipient = conn.execute("SELECT status FROM campaign_recipients WHERE campaign_id=? AND business_id=?",
                                 (campaign_id, business_id)).fetchone()
        if not recipient:
            raise ValueError("Адресат не найден")
        if recipient["status"] == "sent":
            raise ValueError("Уже отправленное сообщение нельзя изменить или отправить повторно")
        conn.execute("UPDATE campaign_recipients SET message=?,status='draft',error=NULL WHERE campaign_id=? AND business_id=?",
                     (_with_opt_out(message), campaign_id, business_id))
    return campaign(campaign_id)


def set_contact_permission(campaign_id: str, business_id: str, status: str,
                           source: str = "", note: str = "") -> dict:
    item = campaign(campaign_id)
    recipient = next((row for row in item["recipients"] if row["business_id"] == business_id), None)
    if not recipient:
        raise ValueError("Адресат не найден")
    if status == "allowed" and recipient["status"] == "sent":
        raise ValueError("Этому адресату уже отправлено сообщение в этой кампании")
    if status == "allowed" and item["status"] != "draft":
        raise ValueError("Согласие можно добавить только к черновику")
    peer = _normalized_peer(recipient["peer"])
    if status == "blocked":
        db.set_contact_permission(peer, recipient.get("telegram_id"), business_id,
                                  "blocked", "opt_out", note or "Попросили больше не писать",
                                  recipient.get("phone"))
        db.set_suppression(business_id, True, "Отказ от сообщений в Telegram")
    else:
        if business_id in db.suppressed_ids():
            raise ValueError("Для организации установлено правило «Не писать»")
        db.set_contact_permission(peer, recipient.get("telegram_id"), business_id,
                                  "allowed", source, note, recipient.get("phone"))
    with db.connection() as conn:
        conn.execute("""UPDATE campaign_recipients SET status=CASE WHEN status NOT IN ('sent','sending') THEN 'draft' ELSE status END,
            error=CASE WHEN ?='blocked' THEN 'Контакт отказался от сообщений' ELSE NULL END
            WHERE campaign_id=? AND business_id=? AND status NOT IN ('sent','sending')""",
            (status, campaign_id, business_id))
    return campaign(campaign_id)


def _permission_status(recipient: dict) -> str:
    if recipient["business_id"] in db.suppressed_ids():
        return "blocked"
    permission = db.contact_permission(_normalized_peer(recipient["peer"]), recipient.get("telegram_id"),
                                       recipient.get("phone"))
    return permission["status"] if permission else "missing"


def _require_permission(recipient: dict) -> None:
    if _permission_status(recipient) != "allowed":
        raise ValueError("Сначала подтвердите согласие адресата или то, что он написал вам первым")
    if OPT_OUT_FOOTER.casefold() not in recipient["message"].casefold():
        raise ValueError("В сообщении обязательна инструкция для отказа: ответьте «СТОП»")


def manual_prepare(campaign_id: str, business_id: str, message: str) -> dict:
    item = campaign(campaign_id)
    if item["status"] != "draft":
        raise ValueError("Открыть чат можно только из черновика")
    recipient = next((row for row in item["recipients"] if row["business_id"] == business_id), None)
    if not recipient:
        raise ValueError("Адресат не найден")
    if recipient["status"] == "sent":
        raise ValueError("Этому адресату уже отправлено сообщение в этой кампании")
    if recipient["peer"].startswith("phone:"):
        raise ValueError("Для ручного открытия нужна публичная ссылка Telegram")
    if recipient["status"] == "prepared":
        raise ValueError("Сообщение уже подготовлено. Отметьте отправку или подтвердите, что не отправляли")
    _require_permission(recipient)
    if not message.strip() or re.search(r"\[[^\]]+\]", message):
        raise ValueError("Заполните поля в квадратных скобках")
    safe_message = _with_opt_out(message)
    update_recipient(campaign_id, business_id, safe_message)
    attempt_id = _reserve_attempt(campaign_id, business_id, recipient["peer"], "manual", "prepared")
    with db.connection() as conn:
        conn.execute("UPDATE campaign_recipients SET status='prepared',error=NULL WHERE campaign_id=? AND business_id=?",
                     (campaign_id, business_id))
    return {"message": safe_message, "url": f"https://t.me/{recipient['peer']}", "attempt_id": attempt_id}


def mark_manual_sent(campaign_id: str, business_id: str) -> dict:
    """Record a send that the user has confirmed in the official Telegram client."""
    item = campaign(campaign_id)
    if item["status"] != "draft":
        raise ValueError("Ручную отправку можно отметить только в черновике")
    recipient = next((row for row in item["recipients"] if row["business_id"] == business_id), None)
    if not recipient:
        raise ValueError("Адресат не найден")
    if recipient["status"] == "sent":
        return item
    if recipient["status"] != "prepared":
        raise ValueError("Сначала подготовьте текст и откройте Telegram из приложения")
    if recipient["peer"].startswith("phone:"):
        raise ValueError("Для ручной отправки нужна публичная ссылка Telegram")
    _require_permission(recipient)
    if re.search(r"\[[^\]]+\]", recipient["message"]):
        raise ValueError("Сначала заполните поля в квадратных скобках")
    with db.connection() as conn:
        attempt = conn.execute("""SELECT id FROM telegram_send_attempts
            WHERE campaign_id=? AND business_id=? AND method='manual' AND status='prepared'
            ORDER BY id DESC LIMIT 1""", (campaign_id, business_id)).fetchone()
        if not attempt:
            raise ValueError("Подготовка не найдена. Откройте Telegram через кнопку приложения")
        sent_at = db.now()
        conn.execute("UPDATE telegram_send_attempts SET status='sent',sent_at=?,error=NULL WHERE id=?",
                     (sent_at, attempt["id"]))
        conn.execute("UPDATE campaign_recipients SET status='sent',sent_at=?,error=NULL WHERE campaign_id=? AND business_id=?",
                     (sent_at, campaign_id, business_id))
    db.set_business(business_id, "waiting", None)
    return campaign(campaign_id)


def mark_manual_not_sent(campaign_id: str, business_id: str) -> dict:
    item = campaign(campaign_id)
    if item["status"] != "draft":
        raise ValueError("Отменить ручную отправку можно только в черновике")
    recipient = next((row for row in item["recipients"] if row["business_id"] == business_id), None)
    if not recipient or recipient["status"] != "prepared":
        raise ValueError("Подготовленное сообщение не найдено")
    with db.connection() as conn:
        attempt = conn.execute("""SELECT id FROM telegram_send_attempts
            WHERE campaign_id=? AND business_id=? AND method='manual' AND status='prepared'
            ORDER BY id DESC LIMIT 1""", (campaign_id, business_id)).fetchone()
        if not attempt:
            raise ValueError("Журнал попытки не найден")
        conn.execute("UPDATE telegram_send_attempts SET status='not_sent',error=? WHERE id=?",
                     ("Пользователь подтвердил, что сообщение не отправлено", attempt["id"]))
        conn.execute("UPDATE campaign_recipients SET status='draft',error=NULL WHERE campaign_id=? AND business_id=?",
                     (campaign_id, business_id))
    return campaign(campaign_id)


def check_draft(campaign_id: str) -> dict:
    """Resolve public Telegram usernames before the user authorizes sending."""
    item = campaign(campaign_id)
    if item["status"] != "draft":
        raise ValueError("Проверить можно только черновик")

    async def run():
        client, _settings_data = await _client()
        try:
            if not await client.is_user_authorized():
                raise ValueError("Сначала войдите в Telegram")
            from telethon.tl.types import User
            seen_peers, seen_ids = set(), set()
            for recipient in item["recipients"]:
                if recipient["status"] in {"sent", "prepared"}:
                    seen_peers.add(recipient["peer"].casefold())
                    if recipient.get("telegram_id"):
                        seen_ids.add(recipient["telegram_id"])
                    continue
                status, error = "ready", None
                telegram_id = None
                if re.search(r"\[[^\]]+\]", recipient["message"]):
                    status, error = "invalid", "Заполните поля в квадратных скобках"
                elif recipient["peer"].casefold() in seen_peers:
                    status, error = "invalid", "Адресат повторяется в этом черновике"
                elif _already_sent(recipient["peer"], recipient["message"], campaign_id):
                    status, error = "invalid", "Такой текст уже отправлен этому адресату"
                else:
                    try:
                        entity = await _resolve_entity(client, recipient["peer"])
                        if not isinstance(entity, User):
                            raise ValueError("Ссылка ведёт не на личный аккаунт")
                        telegram_id = str(entity.id)
                        phone = getattr(entity, "phone", None)
                        if phone:
                            phone = "+" + re.sub(r"\D", "", phone)
                        permission = db.contact_permission(_normalized_peer(recipient["peer"]), telegram_id, phone)
                        if permission and permission["status"] == "blocked":
                            status, error = "invalid", "Контакт отказался от сообщений"
                        elif not permission or permission["status"] != "allowed":
                            status, error = "invalid", "Согласие адресата не подтверждено"
                        elif telegram_id in seen_ids:
                            status, error = "invalid", "Этот Telegram-аккаунт уже есть в черновике"
                        elif _already_sent(recipient["peer"], recipient["message"], campaign_id,
                                           telegram_id=telegram_id):
                            status, error = "invalid", "Такой текст уже отправлен этому Telegram-аккаунту"
                    except Exception as exc:
                        status, error = "invalid", f"Адресат не найден: {type(exc).__name__}"
                seen_peers.add(recipient["peer"].casefold())
                if telegram_id:
                    seen_ids.add(telegram_id)
                with db.connection() as conn:
                    conn.execute("""UPDATE campaign_recipients SET status=?,error=?,telegram_id=?
                        WHERE campaign_id=? AND business_id=?""",
                        (status, error, telegram_id, campaign_id, recipient["business_id"]))
        finally:
            await client.disconnect()

    asyncio.run(run())
    return campaign(campaign_id)


def _already_sent(peer: str, message: str, campaign_id: str, *, telegram_id: str | None = None) -> bool:
    with db.connection() as conn:
        return conn.execute("""SELECT 1 FROM campaign_recipients
            WHERE (peer=? OR telegram_id=?) AND message=? AND status='sent' AND campaign_id<>? LIMIT 1""",
            (peer, telegram_id, message, campaign_id)).fetchone() is not None


def start(campaign_id: str) -> None:
    item = campaign(campaign_id)
    if item["status"] != "draft":
        raise ValueError("Рассылка уже запущена")
    if not item["recipients"] or any(entry["status"] != "ready" or not entry["telegram_id"]
                                     for entry in item["recipients"]):
        raise ValueError("Сначала проверьте всех адресатов и исправьте ошибки")
    for entry in item["recipients"]:
        _require_permission(entry)
        if re.search(r"\[[^\]]+\]", entry["message"]):
            raise ValueError("Сначала заполните поля в квадратных скобках у каждого адресата")
    if len(item["recipients"]) > _remaining_today():
        raise ValueError(f"Превышен дневной лимит {send_policy()['daily_limit']} сообщений. Уменьшите список или повторите завтра")
    if _seconds_until_next_send() > 0:
        raise ValueError(f"Соблюдайте паузу: подождите ещё {_seconds_until_next_send()} секунд")
    with db.connection() as conn:
        changed = conn.execute("UPDATE campaigns SET status='queued',error=NULL,updated_at=? WHERE id=? AND status='draft'",
                               (db.now(), campaign_id)).rowcount
    if not changed:
        raise ValueError("Рассылка уже запущена")
    thread = threading.Thread(target=_send, args=(campaign_id,), daemon=True,
                              name=f"Telegram send {campaign_id[:8]}")
    with _send_threads_lock:
        _send_threads.add(thread)
    try:
        thread.start()
    except Exception as exc:
        with _send_threads_lock:
            _send_threads.discard(thread)
        with db.connection() as conn:
            conn.execute("UPDATE campaigns SET status='failed',error=?,updated_at=? WHERE id=? AND status='queued'",
                         (f"Не удалось запустить поток отправки: {exc}"[:500], db.now(), campaign_id))
        raise


def stop(campaign_id: str) -> None:
    with db.connection() as conn:
        conn.execute("UPDATE campaigns SET status='stopped',updated_at=? WHERE id=? AND status IN ('queued','running')",
                     (db.now(), campaign_id))


def request_shutdown() -> None:
    with db.connection() as conn:
        conn.execute("UPDATE campaigns SET status='stopped',error='Приложение закрывается',updated_at=? WHERE status IN ('queued','running')",
                     (db.now(),))


def shutdown(timeout: float = 40) -> bool:
    request_shutdown()
    deadline = time.monotonic() + max(0, timeout)
    while True:
        with _send_threads_lock:
            threads = list(_send_threads)
        if not threads:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            __import__("logging").getLogger(__name__).error(
                "Telegram send threads did not stop before the shutdown timeout")
            return False
        for thread in threads:
            thread.join(timeout=min(remaining, 1))


def _send(campaign_id: str) -> None:
    try:
        with _send_lock:
            try:
                asyncio.run(_send_async(campaign_id))
            except Exception as exc:
                with db.connection() as conn:
                    conn.execute("""UPDATE campaigns SET status='failed',error=?,updated_at=?
                        WHERE id=? AND status IN ('queued','running')""",
                        (str(exc)[:500], db.now(), campaign_id))
                __import__("logging").getLogger(__name__).exception("Campaign %s failed: %s", campaign_id, exc)
    finally:
        with _send_threads_lock:
            _send_threads.discard(threading.current_thread())


def _remaining_today() -> int:
    with db.connection() as conn:
        return max(0, send_policy()["daily_limit"] - _daily_usage(conn))


def _seconds_until_next_send() -> int:
    with db.connection() as conn:
        latest = _latest_attempt_time(conn)
    if not latest:
        return 0
    elapsed = (datetime.now().astimezone() - datetime.fromisoformat(latest).astimezone()).total_seconds()
    return max(0, int(send_policy()["delay_seconds"] - elapsed + 0.999))


def _daily_usage(conn) -> int:
    today = datetime.now().astimezone().date()
    local = datetime.now().astimezone()
    start = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()
    rows = conn.execute("""SELECT id,attempted_at,sent_at FROM telegram_send_attempts
        WHERE attempted_at>=? OR sent_at>=?""", (start, start)).fetchall()
    count = sum(any(value and datetime.fromisoformat(value).astimezone().date() == today
                    for value in (row["attempted_at"], row["sent_at"])) for row in rows)
    legacy = conn.execute("""SELECT r.campaign_id,r.business_id,r.sent_at FROM campaign_recipients r
        WHERE r.status='sent' AND r.sent_at IS NOT NULL AND r.sent_at>=?
          AND NOT EXISTS (SELECT 1 FROM telegram_send_attempts a
            WHERE a.campaign_id=r.campaign_id AND a.business_id=r.business_id)""", (start,)).fetchall()
    count += sum(datetime.fromisoformat(row["sent_at"]).astimezone().date() == today for row in legacy)
    return count


def _latest_attempt_time(conn) -> str | None:
    row = conn.execute("""SELECT MAX(moment) FROM (
        SELECT attempted_at AS moment FROM telegram_send_attempts
        UNION ALL SELECT sent_at AS moment FROM telegram_send_attempts WHERE sent_at IS NOT NULL
        UNION ALL SELECT sent_at AS moment FROM campaign_recipients WHERE status='sent' AND sent_at IS NOT NULL
    )""").fetchone()
    return row[0] if row else None


def _reserve_attempt(campaign_id: str, business_id: str, peer: str,
                     method: str, status: str) -> int:
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if method == "manual" and conn.execute("""SELECT 1 FROM telegram_send_attempts
            WHERE campaign_id=? AND business_id=? AND method='manual' AND status='prepared' LIMIT 1""",
            (campaign_id, business_id)).fetchone():
            raise ValueError("Сообщение уже подготовлено; подтвердите отправку или отмените её")
        remaining = send_policy()["daily_limit"] - _daily_usage(conn)
        if remaining <= 0:
            raise SendPolicyError("Достигнут дневной лимит. Следующая отправка будет доступна завтра")
        latest = _latest_attempt_time(conn)
        if latest:
            elapsed = (datetime.now().astimezone() - datetime.fromisoformat(latest).astimezone()).total_seconds()
            wait = max(0, int(send_policy()["delay_seconds"] - elapsed + 0.999))
            if wait:
                raise SendPolicyError(f"Соблюдайте паузу: подождите ещё {wait} секунд")
        cursor = conn.execute("""INSERT INTO telegram_send_attempts
            (campaign_id,business_id,peer,method,status,attempted_at)
            VALUES (?,?,?,?,?,?)""", (campaign_id, business_id, peer, method, status, db.now()))
        return cursor.lastrowid


def _finish_attempt(attempt_id: int | None, status: str, error: str | None = None) -> None:
    if not attempt_id:
        return
    with db.connection() as conn:
        conn.execute("UPDATE telegram_send_attempts SET status=?,sent_at=?,error=? WHERE id=?",
                     (status, db.now() if status == "sent" else None, error, attempt_id))


async def _send_async(campaign_id: str) -> None:
    _client_type, _FloodWaitError, PeerFloodError = _telethon()
    item = campaign(campaign_id)
    client, _settings_data = await _client()
    try:
        if not await client.is_user_authorized():
            raise RuntimeError("Сначала войдите в Telegram")
        with db.connection() as conn:
            changed = conn.execute("UPDATE campaigns SET status='running',updated_at=? WHERE id=? AND status='queued'",
                                   (db.now(), campaign_id)).rowcount
        if not changed:
            return
        for index, recipient in enumerate(item["recipients"]):
            if _campaign_status(campaign_id) != "running":
                break
            if _remaining_today() <= 0:
                with db.connection() as conn:
                    conn.execute("UPDATE campaigns SET status='stopped',error='Достигнут дневной лимит',updated_at=? WHERE id=?",
                                 (db.now(), campaign_id))
                break
            if recipient["status"] != "ready":
                continue
            identity = recipient["business_id"]
            attempt_id = None
            current = next(row for row in campaign(campaign_id)["recipients"] if row["business_id"] == identity)
            try:
                _require_permission(current)
            except ValueError as exc:
                with db.connection() as conn:
                    conn.execute("UPDATE campaign_recipients SET status='skipped',error=? WHERE campaign_id=? AND business_id=?",
                                 (str(exc), campaign_id, identity))
                continue
            try:
                # Resolve the public username before sending. Failed resolution is recorded per recipient.
                entity = await _resolve_entity(client, recipient["peer"], campaign_id=campaign_id,
                                               business_id=identity)
                if _campaign_status(campaign_id) != "running":
                    break
                if entity is None:
                    continue
                if str(entity.id) != recipient["telegram_id"]:
                    raise ValueError("Адресат изменился после проверки")
                _require_permission(current)
                attempt_id = _reserve_attempt(campaign_id, identity, recipient["peer"],
                                              "automatic", "sending")
                with db.connection() as conn:
                    conn.execute("UPDATE campaign_recipients SET status='sending',error=NULL WHERE campaign_id=? AND business_id=?",
                                 (campaign_id, identity))
                sent = await _telegram_call(
                    lambda: client.send_message(entity, recipient["message"]),
                    campaign_id=campaign_id, business_id=identity)
                if sent is None:
                    _finish_attempt(attempt_id, "not_sent", "Кампания остановлена во время FloodWait")
                    break
                _finish_attempt(attempt_id, "sent")
                with db.connection() as conn:
                    conn.execute("UPDATE campaign_recipients SET status='sent',sent_at=?,error=NULL WHERE campaign_id=? AND business_id=?",
                                 (db.now(), campaign_id, identity))
                db.set_business(identity, "waiting", None)
                if _campaign_status(campaign_id) != "running":
                    break
            except PeerFloodError as exc:
                _finish_attempt(attempt_id, "failed", str(exc)[:300])
                with db.connection() as conn:
                    conn.execute("UPDATE campaign_recipients SET status='failed',error=? WHERE campaign_id=? AND business_id=?",
                                 (str(exc)[:300], campaign_id, identity))
                    conn.execute("UPDATE campaigns SET status='stopped',error=?,updated_at=? WHERE id=? AND status='running'",
                                 ("Telegram ограничил отправку с этого аккаунта. Кампания остановлена; не запускайте её повторно без проверки аккаунта. "
                                  + str(exc)[:300], db.now(), campaign_id))
                break
            except SendPolicyError as exc:
                with db.connection() as conn:
                    conn.execute("UPDATE campaign_recipients SET status='ready',error=? WHERE campaign_id=? AND business_id=?",
                                 (str(exc), campaign_id, identity))
                    conn.execute("UPDATE campaigns SET status='stopped',error=?,updated_at=? WHERE id=? AND status='running'",
                                 (str(exc), db.now(), campaign_id))
                break
            except Exception as exc:
                _finish_attempt(attempt_id, "failed" if isinstance(exc, ValueError) else "uncertain",
                                str(exc)[:300])
                with db.connection() as conn:
                    conn.execute("UPDATE campaign_recipients SET status=?,error=? WHERE campaign_id=? AND business_id=?",
                                 ("invalid" if isinstance(exc, ValueError) else "uncertain",
                                  str(exc)[:300], campaign_id, identity))
                    if not isinstance(exc, ValueError):
                        conn.execute("UPDATE campaigns SET status='stopped',error=?,updated_at=? WHERE id=? AND status='running'",
                                     ("Отправка остановлена из-за неопределённого результата. Проверьте чат вручную перед повтором. "
                                      + str(exc)[:300], db.now(), campaign_id))
                __import__("logging").getLogger(__name__).exception("Recipient send failed for campaign %s", campaign_id)
                if not isinstance(exc, ValueError):
                    break
                continue
            pending = any(row["status"] == "ready" for row in item["recipients"][index + 1:])
            if pending and _campaign_status(campaign_id) == "running":
                if not await _cooperative_sleep(send_policy()["delay_seconds"], campaign_id):
                    break
        with db.connection() as conn:
            conn.execute("UPDATE campaigns SET status='done',updated_at=? WHERE id=? AND status='running'",
                         (db.now(), campaign_id))
    except Exception as exc:
        with db.connection() as conn:
            conn.execute("UPDATE campaigns SET status='failed',error=?,updated_at=? WHERE id=?",
                         (str(exc)[:500], db.now(), campaign_id))
        raise exc
    finally:
        await client.disconnect()
