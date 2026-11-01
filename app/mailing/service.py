"""Operations behind the desktop «Рассылка» screens. Bridge methods call only this class."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
import json
import re
import threading
from datetime import timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from . import gateway as gw
from .entities import strip_markdown_links
from .engine import REPEAT_SKIP_STATUSES, Credentials, Engine
from .gateway import GatewayError, TelegramGateway
from .logsafe import get_logger, mask_digits, scrub
from .policy import Policy, recipient_tz, when_text, zone, DEFAULT_TZ
from .protect import read_secret, restrict_folder, write_secret
from .store import TERMINAL, Store, iso, parse, utcnow

log = get_logger()
# Same footer as the existing sender (app/telegram_sender.py OPT_OUT_FOOTER).
OPT_OUT_FOOTER = "Если сообщение неактуально, ответьте «СТОП» — больше писать не буду."
USERNAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
NOT_USERNAMES = {"joinchat", "addstickers", "addemoji", "share", "proxy", "socks", "s", "c", "iv",
                 "boost", "addlist", "login", "setlanguage", "confirmphone", "bg", "invoice"}
BRACKETS = re.compile(r"\[[^\]]+\]")
CONFIRM_WORD = "ОТПРАВИТЬ"
MAX_TEST_RECIPIENTS = 5
AUTO_SCRIPT = "auto"  # each business gets the first-message script for its site type
AUTO_REMINDER = "auto:reminder"  # each business gets the reminder script (follow-up stage)
MAX_SELECTED = 500  # leads picked in «Лиды» for one campaign


def normalize_target(value: str) -> str:
    """'@Name' -> 'name'; '+7 999 ...' -> '+7999...'; used for usernames and test recipients."""
    text = str(value or "").strip()
    if text.startswith("+") or text[:1].isdigit():
        digits = re.sub(r"\D", "", text)
        return "+" + digits if len(digits) >= 10 else ""
    text = text.lstrip("@")
    if text.lower().startswith(("https://t.me/", "http://t.me/", "t.me/")):
        text = text.split("t.me/", 1)[1].split("/")[0].split("?")[0]
    return text.casefold() if USERNAME_RE.fullmatch(text) else ""


def username_from_business(business: dict) -> str:
    urls = [item.get("url", "") if isinstance(item, dict) else str(item)
            for item in (business.get("socials") or []) + (business.get("links") or [])]
    for url in urls:
        parsed = urlparse(url if "://" in url else "https://" + url)
        if (parsed.hostname or "").lower() not in {"t.me", "telegram.me", "www.t.me"}:
            continue
        handle = parsed.path.strip("/").split("/")[0]
        if handle.casefold() in NOT_USERNAMES or not USERNAME_RE.fullmatch(handle):
            continue
        return handle.casefold()
    return ""


def phone_keys(business: dict) -> list[str]:
    result = []
    for phone in business.get("phones") or []:
        digits = re.sub(r"\D", "", str(phone))
        if len(digits) == 11 and digits[0] == "8":
            digits = "7" + digits[1:]
        if len(digits) >= 10:
            result.append(digits)
    return sorted(set(result))


def with_footer(text: str) -> str:
    from ..domain import strip_links
    text = strip_links(text)   # no links in outgoing messages (docs/next/DECISIONS.md N29)
    return text if OPT_OUT_FOOTER.casefold() in text.casefold() else f"{text}\n\n{OPT_OUT_FOOTER}"


class MailingService:
    def __init__(self, data_dir: Path, gateway: TelegramGateway, *,
                 businesses: Callable[[dict, int], list[dict]],
                 business: Callable[[str], dict | None],
                 templates: Callable[[], list[dict]],
                 render: Callable[[str, dict], str],
                 first_by_site_type: dict[str, str],
                 external_blocked: Callable[[], set[str]] = lambda: set(),
                 reminder_slug: str = "site.reminder",
                 lead_events: Callable[[str, dict], None] | None = None,
                 engine_options: dict | None = None):
        get_logger()  # attach the masking filter to the log handlers configured by now
        self.dir = Path(data_dir) / "mailing"
        self.private_ok = restrict_folder(self.dir)
        (self.dir / "accounts").mkdir(parents=True, exist_ok=True)
        self.store = Store(self.dir / "mailing.sqlite3")
        self.gateway = gateway
        self._businesses = businesses
        self._business = business
        self._templates = templates
        self._render = render
        self._first_by_site_type = first_by_site_type
        self._reminder_slug = reminder_slug
        self._lead_events = lead_events or (lambda _kind, _data: None)
        self._external_blocked = external_blocked
        self._logins: dict[int, dict] = {}
        self._lock = threading.RLock()
        self.engine = Engine(self.store, gateway, credentials=self._credentials,
                             save_session=self._save_session, policy=self.policy,
                             test_recipients=self.test_recipients, external_blocked=external_blocked,
                             on_sent=self._after_sent, on_reply=self._after_reply,
                             lead_status=lambda business_id: (self._business(business_id) or {}).get("status", ""),
                             **(engine_options or {}))

    # ------------------------------------------------------------ lifecycle
    def start(self) -> None:
        # Attempts left in 'sending' by a crash are reconciled by the engine on the next run.
        self.engine.start_thread()

    def shutdown(self, timeout: float = 15) -> bool:
        return self.engine.shutdown(timeout)

    # ----------------------------------------------------------------- keys
    @property
    def _keys_path(self) -> Path:
        return self.dir / "keys.bin"

    def _keys(self) -> dict:
        keys = read_secret(self._keys_path)
        if not keys:
            raise ValueError("Сначала укажите свои api_id и api_hash (мастер первого запуска)")
        return keys

    def save_keys(self, api_id, api_hash) -> dict:
        try:
            api_id = int(str(api_id).strip())
        except ValueError as exc:
            raise ValueError("api_id — это число с my.telegram.org") from exc
        api_hash = str(api_hash or "").strip()
        if api_id <= 0 or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash):
            raise ValueError("Проверьте api_id (число) и api_hash (32 символа 0-9, a-f)")
        write_secret(self._keys_path, {"api_id": api_id, "api_hash": api_hash.lower()})
        self.store.event("keys", "Ключи API сохранены (зашифрованы)")
        return {"api_id": mask_digits(api_id)}

    def clear_keys(self) -> dict:
        """Forget api_id/api_hash; signed-in sessions stay but cannot connect until keys are entered again."""
        self._keys_path.unlink(missing_ok=True)
        self.store.event("keys", "Ключи API удалены")
        return {"keys_configured": False}

    def import_owner_file(self, path: str) -> dict:
        """Read the owner's secrets/accounts.json (or accounts.local.json) and encrypt what is inside."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        result = {"keys": False, "accounts_added": 0, "accounts_known": 0, "test_recipients": 0}
        if data.get("api_id") and data.get("api_hash"):
            self.save_keys(data["api_id"], data["api_hash"])
            result["keys"] = True
        for item in data.get("accounts") or []:
            phone = str(item.get("phone") or "")
            if len(re.sub(r"\D", "", phone)) < 10:
                continue
            created = self.add_account(str(item.get("label") or "аккаунт"), phone, bool(item.get("is_test")),
                                       expected_id=_account_id(item))
            result["accounts_added" if created["created"] else "accounts_known"] += 1
        if isinstance(data.get("test_recipients"), list):
            result["test_recipients"] = self.set_test_recipients(data["test_recipients"])
        return result

    # --------------------------------------------------------- test contour
    def set_test_recipients(self, values: list) -> int:
        targets = []
        for value in values:
            raw = value.get("peer") or value.get("username") or value.get("phone") if isinstance(value, dict) else value
            target = normalize_target(str(raw or ""))
            if target and target not in targets:
                targets.append(target)
        write_secret(self.dir / "test_recipients.bin", {"targets": targets[:20]})
        self.store.event("test_recipients", f"Тестовых адресатов: {len(targets[:20])}")
        return len(targets[:20])

    def test_recipients(self) -> set[str]:
        data = read_secret(self.dir / "test_recipients.bin") or {}
        return set(data.get("targets") or [])

    def _test_targets(self) -> list[str]:
        return list((read_secret(self.dir / "test_recipients.bin") or {}).get("targets") or [])

    def test_recipients_list(self) -> list[dict]:
        """«Тесты»: the owner's test recipients, masked on screen (@al***), removable by position."""
        result = []
        for index, target in enumerate(self._test_targets()):
            shown = mask_digits(target) if target.startswith("+") else "@" + target[:2] + "***"
            result.append({"index": index, "shown": shown})
        return result

    def test_recipient_add(self, value: str) -> dict:
        target = normalize_target(str(value or ""))
        if not target:
            raise ValueError("Укажите username Telegram (@name) или номер из ваших контактов (+7…)")
        targets = self._test_targets()
        if target in targets:
            return {"count": len(targets), "added": False}
        if len(targets) >= 20:
            raise ValueError("Не больше 20 тестовых адресатов")
        return {"count": self.set_test_recipients(targets + [target]), "added": True}

    def test_recipient_remove(self, index: int) -> dict:
        targets = self._test_targets()
        index = int(index)
        if not 0 <= index < len(targets):
            raise ValueError("Адресат не найден")
        targets.pop(index)
        return {"count": self.set_test_recipients(targets)}

    # ------------------------------------------------------------- accounts
    def _account_path(self, account_id: int) -> Path:
        return self.dir / "accounts" / f"{int(account_id)}.bin"

    def _credentials(self, account_id: int) -> Credentials:
        keys = self._keys()
        secret = read_secret(self._account_path(account_id)) or {}
        if not secret.get("session"):
            raise GatewayError(gw.SESSION_INVALID, "no session")
        return Credentials(int(keys["api_id"]), keys["api_hash"], secret["session"])

    def _save_session(self, account_id: int, session: str) -> None:
        secret = read_secret(self._account_path(account_id)) or {}
        if secret.get("session") != session:
            secret["session"] = session
            write_secret(self._account_path(account_id), secret)

    def _salt(self) -> bytes:
        """Random per-install secret (DPAPI-encrypted) so phone_key cannot be reversed by brute force."""
        path = self.dir / "salt.bin"
        data = read_secret(path)
        if not data:
            data = {"salt": secrets.token_hex(32)}
            write_secret(path, data)
        return bytes.fromhex(data["salt"])

    def add_account(self, label: str, phone: str, is_test: bool, expected_id: int | None = None) -> dict:
        digits = re.sub(r"\D", "", str(phone))
        if not 10 <= len(digits) <= 15:
            raise ValueError("Телефон укажите в международном формате, например +7…")
        key = hmac.new(self._salt(), digits.encode(), hashlib.sha256).hexdigest()
        existing = self.store.row("SELECT id FROM accounts WHERE phone_key=?", (key,))
        if existing:
            return {"id": existing["id"], "created": False}
        now = iso(utcnow())
        with self.store.tx() as conn:
            account_id = conn.execute(
                "INSERT INTO accounts (label,is_test,phone_mask,phone_key,status,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (label.strip()[:40] or "аккаунт", 1 if is_test else 0, f"+{digits[:1]}••••{digits[-2:]}", key,
                 "needs_login", now, now)).lastrowid
        write_secret(self._account_path(account_id), {"phone": "+" + digits, "expected_id": expected_id})
        self.store.event("account_added", f"Добавлен аккаунт «{label}»", account_id=account_id)
        return {"id": account_id, "created": True}

    def accounts(self) -> list[dict]:
        rows = self.store.rows("SELECT id,label,is_test,phone_mask,status,status_reason,paused_until,"
                               "first_send_day,tg_user_id FROM accounts ORDER BY is_test DESC,id")
        day_start = iso(Policy().end_of_day(utcnow()) - timedelta(days=1))
        policy = self.policy()
        for row in rows:
            row["tg_user_id"] = mask_digits(row["tg_user_id"]) if row["tg_user_id"] else ""
            row["sent_today"] = self.store.value(
                "SELECT COUNT(*) FROM attempts WHERE account_id=? AND mode='live' AND status IN ('sent','sending') "
                "AND started_at>=?", (row["id"], day_start))
            row["sent_total"] = self.store.value(
                "SELECT COUNT(*) FROM attempts WHERE account_id=? AND mode='live' AND status='sent'", (row["id"],))
            from datetime import date
            first = date.fromisoformat(row["first_send_day"]) if row["first_send_day"] else None
            row["limit_today"] = policy.daily_limit(first, utcnow().astimezone(zone(DEFAULT_TZ)).date())
        return rows

    def set_account_status(self, account_id: int, status: str) -> dict:
        account = self.store.row("SELECT * FROM accounts WHERE id=?", (int(account_id),))
        if not account:
            raise ValueError("Аккаунт не найден")
        if status == "paused":
            reason, until = "Пауза включена вручную", iso(utcnow() + timedelta(days=3650))
        elif status == "active":
            if account["status"] == "needs_login":
                raise ValueError("Сначала войдите в аккаунт")
            reason, until = "", None
        else:
            raise ValueError("Неизвестный статус")
        with self.store.tx() as conn:
            conn.execute("UPDATE accounts SET status=?,status_reason=?,paused_until=?,updated_at=? WHERE id=?",
                         (status, reason, until, iso(utcnow()), account["id"]))
            self.store.event("account_status", f"Статус аккаунта: {status}" +
                             (" (карантин снят вручную)" if account["status"] == "quarantine" else ""),
                             account_id=account["id"], level="warning", conn=conn)
        return {"ok": True}

    def remove_account(self, account_id: int) -> None:
        account_id = int(account_id)
        busy = self.store.value("SELECT COUNT(*) FROM recipients r JOIN campaigns c ON c.id=r.campaign_id "
                                "WHERE r.account_id=? AND c.status='running'", (account_id,))
        if busy:
            raise ValueError("Аккаунт участвует в запущенной кампании. Сначала остановите её")
        self.engine.call(self.engine.forget_account(account_id), timeout=30)
        secret = read_secret(self._account_path(account_id)) or {}
        secret.pop("session", None)  # keep the phone so the owner can log in again
        write_secret(self._account_path(account_id), secret)
        with self.store.tx() as conn:
            conn.execute("UPDATE accounts SET status='needs_login',status_reason='Сессия удалена',tg_user_id=NULL "
                         "WHERE id=?", (account_id,))
            self.store.event("account_logout", "Сессия аккаунта удалена с этого компьютера",
                             account_id=account_id, conn=conn)

    # ---------------------------------------------------------------- login
    def login_start(self, account_id: int) -> dict:
        account_id = int(account_id)
        keys = self._keys()
        secret = read_secret(self._account_path(account_id)) or {}
        if not secret.get("phone"):
            raise ValueError("Аккаунт не найден")
        try:
            step = self.engine.call(self.gateway.request_code(int(keys["api_id"]), keys["api_hash"],
                                                              secret["phone"]))
        except GatewayError as error:
            raise ValueError(_login_message(error)) from None
        self._logins[account_id] = {"token": step.token}
        return {"status": step.status}

    def login_code(self, account_id: int, code: str) -> dict:
        login = self._logins.get(int(account_id))
        if not login:
            raise ValueError("Сначала запросите код")
        try:
            step = self.engine.call(self.gateway.submit_code(login["token"], str(code)))
        except GatewayError as error:
            raise ValueError(_login_message(error)) from None
        return self._login_result(int(account_id), step)

    def login_password(self, account_id: int, password: str) -> dict:
        login = self._logins.get(int(account_id))
        if not login:
            raise ValueError("Сначала запросите код")
        try:
            step = self.engine.call(self.gateway.submit_password(login["token"], str(password)))
        except GatewayError as error:
            raise ValueError(_login_message(error)) from None
        return self._login_result(int(account_id), step)

    def login_cancel(self, account_id: int) -> None:
        login = self._logins.pop(int(account_id), None)
        if login:
            self.engine.call(self.gateway.cancel_login(login["token"]), timeout=30)

    def _login_result(self, account_id: int, step) -> dict:
        if step.status != "authorized":
            return {"status": step.status}
        self._logins.pop(account_id, None)
        secret = read_secret(self._account_path(account_id)) or {}
        expected = secret.get("expected_id")
        if expected and int(expected) != int(step.user_id):
            raise ValueError("Вход выполнен в другой аккаунт, чем указан в файле владельца. Сессия не сохранена")
        secret["session"] = step.session
        write_secret(self._account_path(account_id), secret)
        with self.store.tx() as conn:
            conn.execute("UPDATE accounts SET status='active',status_reason='',tg_user_id=?,updated_at=? WHERE id=?",
                         (str(step.user_id), iso(utcnow()), account_id))
            self.store.event("login", "Вход выполнен, сессия зашифрована", account_id=account_id, conn=conn)
        return {"status": "authorized"}

    # --------------------------------------------------------------- policy
    def policy(self) -> Policy:
        return Policy.from_dict(self.store.setting("policy", {}))

    def save_policy(self, values: dict) -> dict:
        policy = Policy.from_dict({**self.policy().to_dict(), **(values or {})})
        self.store.set_setting("policy", policy.to_dict())
        return policy.to_dict()

    # ------------------------------------------------------------ campaigns
    def script_suggestions(self, site_type: str = "") -> dict:
        rows = self._templates()
        preferred = self._first_by_site_type.get(site_type, "first")
        return {"preferred": preferred,
                "items": [{"slug": row["slug"], "title": row["title"], "body": row["body"]} for row in rows]}

    def _select(self, filters: dict) -> list[dict]:
        filters = dict(filters or {})
        contact = filters.pop("contact", "username")
        if filters.get("ids"):
            items, seen = [], set()
            for identity in [str(x) for x in filters["ids"]][:MAX_SELECTED]:
                item = None if identity in seen else self._business(identity)
                seen.add(identity)
                if item:
                    items.append({**item, "_username": username_from_business(item), "_phones": phone_keys(item)})
            return items
        query = {key: filters[key] for key in ("niche", "country", "city", "site_type", "status", "q")
                 if filters.get(key)}
        items = []
        for item in self._businesses(query, 2000):
            username, phones = username_from_business(item), phone_keys(item)
            if contact == "username" and not username:
                continue
            if contact == "phone" and not phones:
                continue
            items.append({**item, "_username": username, "_phones": phones})
        return items

    def _template_for(self, template: str, business: dict) -> str:
        """AUTO_SCRIPT: the first-message script that fits this business's site type;
        AUTO_REMINDER: the reminder script."""
        if template not in (AUTO_SCRIPT, AUTO_REMINDER):
            return template
        bodies = {row["slug"]: row["body"] for row in self._templates()}
        if template == AUTO_REMINDER:
            body = bodies.get(self._reminder_slug, "")
            if not body:
                raise ValueError("Нет скрипта напоминания (site.reminder) в «Скриптах»")
            return body
        slug = self._first_by_site_type.get(business.get("site_type") or "", "")
        body = bodies.get(slug) or bodies.get(self._first_by_site_type.get("", "")) or next(iter(bodies.values()), "")
        if not body:
            raise ValueError("Нет скриптов первого сообщения")
        return body

    def _message(self, template: str, business: dict) -> str:
        return with_footer(self._render(self._template_for(template, business), business))

    def preview(self, template: str, filters: dict, kind: str = "normal") -> dict:
        template = str(template or "").strip()
        if not template:
            raise ValueError("Введите текст сообщения")
        selected = self._select(filters)
        stop = self.store.stop_set()
        rows = []
        for item in selected:
            if item["id"] in stop["business"]:
                continue
            rows.append({"business_id": item["id"], "name": item["name"], "city": item.get("city") or "",
                         "site_type": item.get("site_type"), "username": item["_username"],
                         "text": self._message(template, item)})
            if len(rows) == 3:
                break
        problems = [] if template == AUTO_SCRIPT else sorted(set(BRACKETS.findall(strip_markdown_links(template))))
        return {"total": len(selected), "items": rows, "unfilled": problems,
                "with_username": sum(1 for item in selected if item["_username"])}

    def create_campaign(self, title: str, filters: dict, template: str, account_ids: list,
                        kind: str = "normal", limit: int | None = None, *, batch: dict | None = None,
                        no_wait: bool = False) -> dict:
        template = str(template or "").strip()
        if not template:
            raise ValueError("Введите текст сообщения")
        # [слова](https://…) is a link on the words, not an unfilled field.
        if BRACKETS.search(strip_markdown_links(template)):
            raise ValueError("Замените поля в квадратных скобках: " + ", ".join(sorted(set(BRACKETS.findall(strip_markdown_links(template))))))
        if kind not in ("normal", "test"):
            raise ValueError("Неизвестный тип кампании")
        if no_wait and kind != "test":
            raise ValueError("«Без ожидания» — только для тестов")
        accounts = [self.store.row("SELECT * FROM accounts WHERE id=?", (int(item),)) for item in account_ids or []]
        accounts = [item for item in accounts if item]
        if not accounts:
            raise ValueError("Выберите хотя бы один аккаунт")
        if kind == "test":
            if any(not item["is_test"] for item in accounts):
                raise ValueError("Тестовая кампания отправляется только с тестовых аккаунтов")
            targets = sorted(self.test_recipients())
            if not targets:
                raise ValueError("Нет тестовых адресатов. Загрузите secrets\\accounts.local.json (поле test_recipients)")
            limit = min(int(limit or 3), MAX_TEST_RECIPIENTS)
        selected = self._select(filters if kind == "normal" else {**(filters or {}), "contact": "any"})
        if limit:
            selected = selected[:int(limit)]
        if not selected:
            raise ValueError("Среди выбранных нет организаций с контактом Telegram" if (filters or {}).get("ids")
                             else "По фильтрам не найдено организаций с контактом Telegram")
        stop = self.store.stop_set()
        by_ids = bool((filters or {}).get("ids"))
        now = iso(utcnow())
        with self.store.tx() as conn:
            batch = batch or {}
            campaign_id = conn.execute(
                "INSERT INTO campaigns (title,kind,mode,status,template,filters_json,created_at,updated_at,"
                "batch_id,parent_id,stage,no_wait) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(title or "").strip()[:120] or "Новая рассылка", kind, "dry", "draft", template,
                 json.dumps(filters or {}, ensure_ascii=False), now, now, batch.get("batch_id"),
                 batch.get("parent_id"), batch.get("stage") or "first", 1 if no_wait else 0)).lastrowid
            conn.executemany("INSERT INTO campaign_accounts VALUES (?,?)", [(campaign_id, a["id"]) for a in accounts])
            seen_phones: set[str] = set()
            seen_keys: set[str] = set()
            for position, item in enumerate(selected):
                account = accounts[position % len(accounts)]
                status, reason_kind, reason = "queued", "", ""
                if kind == "test":
                    username = targets[position % len(targets)]
                    # The owner's own accounts: Moscow time, the same window as businesses (E-M-04).
                    key, tz = f"test:{item['id']}", DEFAULT_TZ
                else:
                    username = item["_username"]
                    key = username or f"business:{item['id']}"
                    tz = recipient_tz(item.get("city"), item.get("country"))
                    if item["id"] in stop["business"] or (username and username in stop["username"]) or set(item["_phones"]) & stop["phone"]:
                        status, reason_kind, reason = "skipped", "stoplist", "В стоп-листе"
                    elif by_ids and not username:
                        status, reason_kind, reason = "skipped", "no_contact", "Нет username Telegram"
                    elif key in seen_keys:
                        status, reason_kind, reason = "skipped", "duplicate", "Тот же username у другой организации"
                        key = f"dup:{item['id']}"
                    elif set(item["_phones"]) & seen_phones:
                        status, reason_kind, reason = "skipped", "duplicate", "Тот же телефон у другой организации"
                    seen_phones.update(item["_phones"])
                seen_keys.add(key)
                conn.execute(
                    "INSERT INTO recipients (campaign_id,business_id,business_name,account_id,username,phones,"
                    "dedupe_key,message,tz,status,reason_kind,reason,position) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (campaign_id, item["id"], item["name"], account["id"], username or None,
                     ",".join(item["_phones"]), key, self._message(template, item), tz, status, reason_kind,
                     reason, position))
            self.store.event("campaign_created", f"Кампания создана: {len(selected)} получателей, сухой запуск",
                             campaign_id=campaign_id, conn=conn)
        return self.campaign(campaign_id)

    def launch(self, title: str, ids: list, template: str, account_ids: list, confirm: str) -> dict:
        """One button: a campaign for a batch of leads, live, started. The confirm word stays mandatory;
        limits, windows, stop-list and pauses are the same as for any campaign."""
        if str(confirm or "").strip().upper() != CONFIRM_WORD:
            raise ValueError(f"Чтобы запустить рассылку, введите слово {CONFIRM_WORD}")
        if self.store.setting("emergency_stop", False):
            raise ValueError("Включён аварийный стоп. Снимите его в разделе «Рассылка»")
        ids = [str(x) for x in ids or []][:self.policy().batch_limit]
        campaign = self.create_campaign(title, {"ids": ids}, template, account_ids)
        self.enable_live(campaign["id"], confirm)
        return self.run(campaign["id"])

    def _editable(self, campaign_id: int, recipient_id: int) -> tuple[dict, dict]:
        campaign = self.store.row("SELECT * FROM campaigns WHERE id=?", (int(campaign_id),))
        if not campaign:
            raise ValueError("Кампания не найдена")
        if campaign["status"] == "running":
            raise ValueError("Кампания идёт — поставьте её на паузу, чтобы менять тексты")
        recipient = self.store.row("SELECT * FROM recipients WHERE id=? AND campaign_id=?",
                                   (int(recipient_id), campaign["id"]))
        if not recipient:
            raise ValueError("Получатель не найден")
        if recipient["status"] not in ("queued", "dry_sent"):
            raise ValueError("Этому получателю текст уже не изменить: сообщение отправлено или он пропущен")
        return campaign, recipient

    def set_message(self, campaign_id: int, recipient_id: int, text: str) -> dict:
        """Personal text for one recipient before sending. Opt-out line is always kept."""
        campaign, recipient = self._editable(campaign_id, recipient_id)
        text = str(text or "").strip()
        if not text:
            raise ValueError("Текст сообщения пустой")
        if len(text) > 4000:
            raise ValueError("Сообщение длиннее 4000 символов")
        if BRACKETS.search(strip_markdown_links(text)):
            raise ValueError("Замените поля в квадратных скобках: " + ", ".join(sorted(set(BRACKETS.findall(strip_markdown_links(text))))))
        message = with_footer(text)
        with self.store.tx() as conn:
            conn.execute("UPDATE recipients SET message=?,edited=1 WHERE id=?", (message, recipient["id"]))
            self.store.event("message_edited", f"Текст изменён вручную: {recipient['business_name']}",
                             campaign_id=campaign["id"], conn=conn)
        return {"id": recipient["id"], "message": message, "edited": 1}

    def reset_message(self, campaign_id: int, recipient_id: int) -> dict:
        campaign, recipient = self._editable(campaign_id, recipient_id)
        business = self._business(recipient["business_id"]) or {"name": recipient["business_name"]}
        message = self._message(campaign["template"], business)
        with self.store.tx() as conn:
            conn.execute("UPDATE recipients SET message=?,edited=0 WHERE id=?", (message, recipient["id"]))
        return {"id": recipient["id"], "message": message, "edited": 0}

    # ------------------------------------------------------------ batches and repeats
    def _batch_id(self, campaign: dict) -> int:
        return int(campaign.get("batch_id") or campaign["id"])

    def _batch_row(self, campaign_id: int) -> dict:
        campaign = self.store.row("SELECT * FROM campaigns WHERE id=?", (int(campaign_id),))
        if not campaign:
            raise ValueError("Кампания не найдена")
        return campaign

    def exclude(self, campaign_id: int, business_id: str, reason: str = "") -> dict:
        """«Убрать из пачки»: the business gets no repeat of this batch; a queued message is withdrawn."""
        campaign = self._batch_row(campaign_id)
        batch_id = self._batch_id(campaign)
        business_id = str(business_id)
        if not self.store.value("SELECT 1 FROM recipients r JOIN campaigns c ON c.id=r.campaign_id "
                                "WHERE COALESCE(c.batch_id,c.id)=? AND r.business_id=?", (batch_id, business_id)):
            raise ValueError("Этой организации нет в пачке")
        with self.store.tx() as conn:
            conn.execute("INSERT OR IGNORE INTO batch_exclusions VALUES (?,?,?,?)",
                         (batch_id, business_id, str(reason or "Убран вручную")[:200], iso(utcnow())))
            withdrawn = conn.execute(
                "UPDATE recipients SET status='skipped',reason_kind='excluded',reason='Убран из пачки' "
                "WHERE status='queued' AND business_id=? AND campaign_id IN "
                "(SELECT id FROM campaigns WHERE COALESCE(batch_id,id)=?)", (business_id, batch_id)).rowcount
            name = conn.execute("SELECT business_name FROM recipients WHERE business_id=? LIMIT 1",
                                (business_id,)).fetchone()
            self.store.event("batch_exclude", f"Убран из пачки: {name[0] if name else business_id}",
                             campaign_id=batch_id, conn=conn)
        return {"excluded": True, "withdrawn": withdrawn}

    def include(self, campaign_id: int, business_id: str) -> dict:
        """Undo «Убрать из пачки» for future repeats (already skipped messages stay skipped)."""
        campaign = self._batch_row(campaign_id)
        with self.store.tx() as conn:
            conn.execute("DELETE FROM batch_exclusions WHERE batch_id=? AND business_id=?",
                         (self._batch_id(campaign), str(business_id)))
        return {"excluded": False}

    def repeat_preview(self, campaign_id: int, template: str = AUTO_REMINDER) -> dict:
        """Who gets a repeat of this campaign: those who got (or in a dry run would get) its message,
        minus excluded, replied, stop-list, «Не писать» and leads already talking or refused."""
        source = self._batch_row(campaign_id)
        batch_id = self._batch_id(source)
        excluded = {row["business_id"] for row in self.store.rows(
            "SELECT business_id FROM batch_exclusions WHERE batch_id=?", (batch_id,))}
        replied = {row["business_id"] for row in self.store.rows(
            "SELECT DISTINCT r.business_id FROM recipients r JOIN campaigns c ON c.id=r.campaign_id "
            "WHERE COALESCE(c.batch_id,c.id)=? AND r.replied_at IS NOT NULL", (batch_id,))}
        stage = "reminder" if (str(template or "").strip() or AUTO_REMINDER) == AUTO_REMINDER else "repeat"
        # Each stage reaches an organization at most once per batch, counting live repeats still queued (E-M-10).
        had_stage = {row["business_id"] for row in self.store.rows(
            "SELECT DISTINCT r.business_id FROM recipients r JOIN campaigns c ON c.id=r.campaign_id "
            "WHERE COALESCE(c.batch_id,c.id)=? AND c.stage=? AND (r.status IN ('sent','sending') "
            "OR (r.status='queued' AND c.mode='live'))", (batch_id, stage))}
        stop = self.store.stop_set()
        blocked = set(self._external_blocked() or ())
        include, skip = [], []
        seen = set()
        for row in self.store.rows("SELECT business_id,business_name,username,phones,status FROM recipients "
                                   "WHERE campaign_id=? ORDER BY position", (source["id"],)):
            if row["business_id"] in seen:
                continue
            seen.add(row["business_id"])
            business = self._business(row["business_id"]) or {}
            status = business.get("status") or ""
            phones = {item for item in (row["phones"] or "").split(",") if item}
            username = row["username"] if source["kind"] == "normal" else ""
            reason = ""
            if row["status"] not in ("sent", "dry_sent"):
                reason = ("Первое сообщение не дошло" if row["status"] in ("failed", "unreachable")
                          else "Не получал сообщение")
            elif row["business_id"] in had_stage:
                reason = "Напоминание уже получал" if stage == "reminder" else "Этот повтор уже получал"
            elif row["business_id"] in excluded:
                reason = "Убран из пачки"
            elif row["business_id"] in replied:
                reason = "Ответил в Telegram"
            elif status in REPEAT_SKIP_STATUSES:
                reason = f"В «Лидах»: {REPEAT_SKIP_STATUSES[status]}"
            elif (row["business_id"] in stop["business"] or row["business_id"] in blocked
                  or (username and username in stop["username"]) or phones & stop["phone"]):
                reason = "В стоп-листе или «Не писать»"
            elif not business:
                reason = "Организации больше нет в базе"
            item = {"business_id": row["business_id"], "name": row["business_name"], "reason": reason,
                    "lead_status": status}
            (skip if reason else include).append(item)
        return {"campaign_id": source["id"], "batch_id": batch_id, "kind": source["kind"], "stage": stage,
                "include": include, "skip": skip,
                "accounts": [row["account_id"] for row in self.store.rows(
                    "SELECT account_id FROM campaign_accounts WHERE campaign_id=?", (source["id"],))]}

    def repeat(self, campaign_id: int, template: str = AUTO_REMINDER, title: str = "",
               account_ids: list | None = None, live: bool = False, confirm: str = "") -> dict:
        """«Повторить пачку»: same batch, another text (by default the reminder). A dry run unless
        ``live`` and the confirm word; limits, windows, stop-list and pauses as for any campaign."""
        template = str(template or "").strip() or AUTO_REMINDER
        if live:
            if str(confirm or "").strip().upper() != CONFIRM_WORD:
                raise ValueError(f"Чтобы запустить повтор, введите слово {CONFIRM_WORD}")
            if self.store.setting("emergency_stop", False):
                raise ValueError("Включён аварийный стоп. Снимите его в разделе «Рассылка»")
        source = self._batch_row(campaign_id)
        if source["status"] == "running":
            raise ValueError("Кампания ещё идёт — повторить можно после её окончания или на паузе")
        if live and source["mode"] != "live":
            raise ValueError("Это был сухой запуск: первое сообщение никто не получил. Повтор — тоже только сухой")
        plan = self.repeat_preview(source["id"], template)
        ids = [item["business_id"] for item in plan["include"]]
        if not ids:
            raise ValueError("В повтор некого включить: все ответили, убраны, в стоп-листе или уже получили этот этап")
        stage = "reminder" if template == AUTO_REMINDER else "repeat"
        root = self._batch_row(plan["batch_id"])
        title = str(title or "").strip() or f"{root['title']} · {'напоминание' if stage == 'reminder' else 'повтор'}"
        campaign = self.create_campaign(title, {"ids": ids, "repeat_of": source["id"]}, template,
                                        account_ids or plan["accounts"], kind=source["kind"],
                                        batch={"batch_id": plan["batch_id"], "parent_id": source["id"],
                                               "stage": stage})
        self.store.event("batch_repeat", f"Повтор пачки: {len(ids)} получателей, пропущено {len(plan['skip'])}",
                         campaign_id=campaign["id"])
        if not live:
            return self.dry_run(campaign["id"])
        self.enable_live(campaign["id"], confirm)
        return self.run(campaign["id"])

    def _after_sent(self, recipient: dict) -> None:
        """A live message reached a real business: «Лиды» get «Написал» and a reminder entry."""
        campaign = self.store.row("SELECT kind,mode,stage FROM campaigns WHERE id=?", (recipient["campaign_id"],))
        if not campaign or campaign["kind"] != "normal" or campaign["mode"] != "live":
            return
        row = self.store.row("SELECT business_id,username,message FROM recipients WHERE id=?", (recipient["id"],))
        if row:
            self._lead_events("sent", {**row, "stage": campaign["stage"] or "first"})

    def _after_reply(self, recipient: dict, is_stop: bool) -> None:
        self._lead_events("stop" if is_stop else "replied", {"business_id": recipient["business_id"]})

    def campaigns(self) -> list[dict]:
        rows = self.store.rows("SELECT id,title,kind,mode,status,stop_reason,created_at,updated_at,"
                               "COALESCE(batch_id,id) AS batch_id,parent_id,stage FROM campaigns ORDER BY id DESC")
        for row in rows:
            row["counts"] = self._counts(row["id"])
        return rows

    def campaign(self, campaign_id: int, recipients_limit: int = 300) -> dict:
        row = self.store.row("SELECT * FROM campaigns WHERE id=?", (int(campaign_id),))
        if not row:
            raise ValueError("Кампания не найдена")
        row["counts"] = self._counts(row["id"])
        row["reasons"] = self.store.rows(
            "SELECT status,reason_kind,reason,COUNT(*) AS count FROM recipients WHERE campaign_id=? "
            "AND status IN ('skipped','unreachable','failed') GROUP BY status,reason_kind,reason ORDER BY count DESC",
            (row["id"],))
        row["accounts"] = self.store.rows(
            "SELECT a.id,a.label,a.is_test,a.status,a.status_reason,"
            "SUM(CASE WHEN r.status='sent' THEN 1 ELSE 0 END) AS sent,"
            "SUM(CASE WHEN r.status IN ('queued','sending') THEN 1 ELSE 0 END) AS left,"
            "SUM(CASE WHEN r.status IN ('failed','unreachable') THEN 1 ELSE 0 END) AS errors "
            "FROM campaign_accounts ca JOIN accounts a ON a.id=ca.account_id "
            "LEFT JOIN recipients r ON r.campaign_id=ca.campaign_id AND r.account_id=a.id "
            "WHERE ca.campaign_id=? GROUP BY a.id ORDER BY a.id", (row["id"],))
        recipients = self.store.rows(
            "SELECT id,business_id,business_name,account_id,username,status,reason_kind,reason,msg_id,sent_at,"
            "replied_at,message,edited,tz "
            "FROM recipients WHERE campaign_id=? ORDER BY position LIMIT ?", (row["id"], int(recipients_limit)))
        test_kind = row["kind"] == "test"
        batch_id = self._batch_id(row)
        excluded = {item["business_id"] for item in self.store.rows(
            "SELECT business_id FROM batch_exclusions WHERE batch_id=?", (batch_id,))}
        for item in recipients:
            if test_kind and item["username"] and item["username"].startswith("+"):
                item["username"] = mask_digits(item["username"])
            business = self._business(item["business_id"]) or {}
            item["lead_status"] = business.get("status") or ""
            if item["sent_at"]:
                item["sent_when"] = "Ушло " + when_text(parse(item["sent_at"]), item.pop("tz", ""),
                                                        "" if test_kind else business.get("city", ""))
            item.pop("tz", None)
            item["excluded"] = item["business_id"] in excluded
        row["batch_id"] = batch_id
        row["batch"] = self.store.rows(
            "SELECT id,title,stage,mode,status,created_at FROM campaigns WHERE COALESCE(batch_id,id)=? ORDER BY id",
            (batch_id,))
        for item in row["batch"]:
            item["counts"] = self._counts(item["id"])
        row["recipients"] = recipients
        row["events"] = self.store.rows("SELECT at,level,kind,message FROM events WHERE campaign_id=? "
                                        "ORDER BY id DESC LIMIT 50", (row["id"],))
        return row

    def _counts(self, campaign_id: int) -> dict:
        counts = {key: 0 for key in ("queued", "sending", "sent", "dry_sent", "skipped", "unreachable", "failed")}
        for item in self.store.rows("SELECT status,COUNT(*) AS n FROM recipients WHERE campaign_id=? GROUP BY status",
                                    (campaign_id,)):
            counts[item["status"]] = item["n"]
        counts["stoplist"] = self.store.value("SELECT COUNT(*) FROM recipients WHERE campaign_id=? AND "
                                              "reason_kind='stoplist'", (campaign_id,))
        counts["replied"] = self.store.value("SELECT COUNT(*) FROM recipients WHERE campaign_id=? AND "
                                             "replied_at IS NOT NULL", (campaign_id,))
        counts["total"] = sum(counts[key] for key in ("queued", "sending", "sent", "dry_sent", "skipped",
                                                      "unreachable", "failed"))
        return counts

    def run(self, campaign_id: int) -> dict:
        campaign = self.campaign(campaign_id, 0)
        if self.store.setting("emergency_stop", False):
            raise ValueError("Включён аварийный стоп. Снимите его в разделе «Рассылка»")
        if campaign["mode"] == "dry":
            return self.dry_run(campaign["id"])
        self.mark_running(campaign["id"])
        self.engine.call(_launch(self.engine, campaign["id"]), timeout=30)
        return self.campaign(campaign["id"])

    def mark_running(self, campaign_id: int) -> None:
        """Checks before a live run; the engine then picks the campaign up."""
        campaign = self.campaign(campaign_id, 0)
        if self.store.setting("emergency_stop", False):
            raise ValueError("Включён аварийный стоп. Снимите его в разделе «Рассылка»")
        if campaign["mode"] != "live":
            raise ValueError("Сначала включите реальную отправку")
        if campaign["status"] not in ("draft", "paused", "stopped"):
            raise ValueError("Кампания уже запущена или завершена")
        blocked = self.store.rows("SELECT a.label,a.status FROM campaign_accounts ca JOIN accounts a "
                                  "ON a.id=ca.account_id WHERE ca.campaign_id=? AND a.status IN "
                                  "('quarantine','needs_login')", (campaign["id"],))
        if blocked:
            raise ValueError("Аккаунт не готов: " + ", ".join(f"{item['label']} ({item['status']})" for item in blocked))
        with self.store.tx() as conn:
            conn.execute("UPDATE campaigns SET status='running',stop_reason='',started_at=COALESCE(started_at,?),"
                         "updated_at=? WHERE id=?", (iso(utcnow()), iso(utcnow()), campaign["id"]))
            self.store.event("campaign_started", "Реальная отправка запущена", level="warning",
                             campaign_id=campaign["id"], conn=conn)

    def dry_run(self, campaign_id: int) -> dict:
        """Same checks as a live run, no Telegram calls. Shows when each message would go."""
        campaign = self.store.row("SELECT * FROM campaigns WHERE id=?", (int(campaign_id),))
        if campaign["mode"] != "dry" or campaign["status"] not in ("draft", "done"):
            raise ValueError("Сухой запуск доступен только для черновика")
        engine = self.engine
        policy = self.policy()
        now = utcnow()
        state: dict[int, dict] = {}
        for recipient in self.store.rows("SELECT * FROM recipients WHERE campaign_id=? AND status='queued' "
                                         "ORDER BY position", (campaign["id"],)):
            account = self.store.row("SELECT * FROM accounts WHERE id=?", (recipient["account_id"],))
            verdict = engine.precheck(campaign, account, recipient)
            if verdict:
                engine._close_recipient(recipient["id"], *verdict)
                continue
            sim = state.setdefault(account["id"], {"next": now, "today": 0, "hour": []})
            due = engine.earliest_send(account, recipient, now=sim["next"], sent_today=sim["today"],
                                       last_hour=sim["hour"], last_send=sim["next"])
            sim["today"] += 1
            sim["hour"] = [item for item in sim["hour"] if item > due - timedelta(hours=1)] + [due]
            sim["next"] = due + timedelta(seconds=(policy.delay_min + policy.delay_max) // 2)
            city = "" if campaign["kind"] == "test" else (self._business(recipient["business_id"]) or {}).get("city", "")
            local = when_text(due, recipient["tz"], city)
            with self.store.tx() as conn:
                conn.execute("INSERT INTO attempts (recipient_id,account_id,random_id,mode,status,started_at,"
                             "finished_at,server_response) VALUES (?,?,0,'dry','dry',?,?,?)",
                             (recipient["id"], account["id"], iso(utcnow()), iso(utcnow()),
                              f"сухой запуск: ушло бы {local}"))
                conn.execute("UPDATE recipients SET status='dry_sent',reason_kind='dry',reason=? WHERE id=?",
                             (f"Уйдёт {local}", recipient["id"]))
        with self.store.tx() as conn:
            conn.execute("UPDATE campaigns SET status='done',finished_at=?,updated_at=? WHERE id=?",
                         (iso(utcnow()), iso(utcnow()), campaign["id"]))
            self.store.event("dry_run", "Сухой запуск завершён, ничего не отправлено", campaign_id=campaign["id"],
                             conn=conn)
        return self.campaign(campaign["id"])

    def enable_live(self, campaign_id: int, confirm: str) -> dict:
        if str(confirm or "").strip().upper() != CONFIRM_WORD:
            raise ValueError(f"Чтобы включить реальную отправку, введите слово {CONFIRM_WORD}")
        campaign = self.campaign(campaign_id, 0)
        if campaign["mode"] == "live":
            return campaign
        if campaign.get("parent_id"):
            # A repeat goes live only after a live message it repeats (E-M-13).
            parent = self.store.row("SELECT mode FROM campaigns WHERE id=?", (campaign["parent_id"],))
            if not parent or parent["mode"] != "live":
                raise ValueError("Это повтор сухого запуска: предыдущее сообщение никто не получил. "
                                 "Реальная отправка для него закрыта")
        with self.store.tx() as conn:
            conn.execute("UPDATE recipients SET status='queued',reason_kind='',reason='' WHERE campaign_id=? "
                         "AND status='dry_sent'", (campaign["id"],))
            conn.execute("UPDATE campaigns SET mode='live',status='draft',finished_at=NULL,updated_at=? WHERE id=?",
                         (iso(utcnow()), campaign["id"]))
            self.store.event("live_enabled", "Включена реальная отправка", level="warning",
                             campaign_id=campaign["id"], conn=conn)
        return self.campaign(campaign["id"])

    def pause(self, campaign_id: int) -> dict:
        with self.store.tx() as conn:
            conn.execute("UPDATE campaigns SET status='paused',stop_reason='Пауза вручную',updated_at=? "
                         "WHERE id=? AND status='running'", (iso(utcnow()), int(campaign_id)))
        self.engine.call(self.engine.cancel_campaign(int(campaign_id)), timeout=30)
        return self.campaign(campaign_id)

    def delete_campaign(self, campaign_id: int) -> None:
        campaign = self.campaign(campaign_id, 0)
        if campaign["status"] == "running":
            raise ValueError("Сначала поставьте кампанию на паузу")
        if campaign["counts"]["sent"]:
            raise ValueError("Из кампании уже отправлены сообщения — она остаётся в истории")
        with self.store.tx() as conn:
            conn.execute("DELETE FROM campaigns WHERE id=?", (campaign["id"],))

    def emergency_stop(self) -> dict:
        self.store.set_setting("emergency_stop", True)
        with self.store.tx() as conn:
            conn.execute("UPDATE campaigns SET status='paused',stop_reason='Аварийный стоп',updated_at=? "
                         "WHERE status='running'", (iso(utcnow()),))
            self.store.event("emergency_stop", "Аварийный стоп: все отправки остановлены", level="error", conn=conn)
        try:
            self.engine.call(self.engine.cancel_all(), timeout=5)
        except Exception:
            log.exception("Отмена задач при аварийном стопе")
        return {"emergency_stop": True}

    def clear_emergency(self) -> dict:
        self.store.set_setting("emergency_stop", False)
        self.store.event("emergency_clear", "Аварийный стоп снят. Кампании остаются на паузе", level="warning")
        return {"emergency_stop": False}

    # ------------------------------------------------------------ stoplist
    def stoplist(self) -> list[dict]:
        rows = self.store.rows("SELECT id,kind,value,label,source,created_at FROM stoplist ORDER BY id DESC")
        for row in rows:
            if row["kind"] in ("phone", "telegram_user"):
                row["value"] = mask_digits(row["value"])
        return rows

    def stop_add(self, kind: str, value: str, label: str = "") -> dict:
        if kind not in ("business", "username", "phone"):
            raise ValueError("Неизвестный тип записи")
        if kind == "username":
            value = normalize_target(value)
            if not value or value.startswith("+"):
                raise ValueError("Укажите username Telegram")
        elif kind == "phone":
            value = re.sub(r"\D", "", value)
            if len(value) == 11 and value[0] == "8":
                value = "7" + value[1:]
            if len(value) < 10:
                raise ValueError("Укажите телефон")
        elif kind == "business":
            business = self._business(str(value))
            if not business:
                raise ValueError("Организация не найдена")
            label = label or business["name"]
        self.store.stop_add(kind, value, label)
        self.store.event("stoplist", f"Добавлено в стоп-лист: {label or kind}")
        return {"ok": True}

    def stop_remove(self, entry_id: int) -> None:
        self.store.stop_remove(entry_id)

    # --------------------------------------------------------------- state
    def overview(self) -> dict:
        keys = None
        try:
            keys = read_secret(self._keys_path)
        except Exception:
            keys = None
        accounts = self.accounts()
        return {"keys_configured": bool(keys), "api_id": mask_digits(keys["api_id"]) if keys else "",
                "accounts": accounts, "emergency_stop": bool(self.store.setting("emergency_stop", False)),
                "test_recipients": len(self.test_recipients()), "policy": self.policy().to_dict(),
                "private_folder": self.private_ok, "confirm_word": CONFIRM_WORD,
                "events": self.store.rows("SELECT at,level,kind,message,account_id,campaign_id FROM events "
                                          "ORDER BY id DESC LIMIT 30")}


def _account_id(item: dict) -> int | None:
    value = item.get("account_id")
    if not value:  # the owner's file may hold the id as a key: {"<id>": 0}
        value = next((key for key in item if str(key).isdigit()), None)
    try:
        return int(value) if value else None
    except ValueError:
        return None


def _login_message(error: GatewayError) -> str:
    return {
        gw.AUTH_CODE_INVALID: "Код неверный или устарел. Запросите новый",
        gw.AUTH_PASSWORD_INVALID: "Неверный пароль двухэтапной проверки",
        gw.AUTH_PHONE_INVALID: "Telegram не принял этот номер",
        gw.FLOOD_WAIT: f"Telegram просит подождать {error.seconds or 0} с перед новой попыткой входа",
        gw.NETWORK: "Нет связи с Telegram",
    }.get(error.kind, gw.KIND_LABELS.get(error.kind, "Не удалось войти") + f" ({scrub(error.detail)})")


async def _launch(engine: Engine, campaign_id: int) -> None:
    engine.launch(campaign_id)
    await asyncio.sleep(0)
