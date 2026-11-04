"""«Сегодня»: a semi-manual outreach queue.

The app never sends anything itself. It picks who to write to, prepares the text and a
t.me link; the admin sends from their own Telegram and reports the outcome.
Limits are per sender ("actor"), because Telegram restricts each account separately.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

from . import db
from .config import DB_PATH
from .domain import render_template
from .phone_links import build_telegram_phone_links
from .script_templates import pick_slug

OPT_OUT_FOOTER = "Если сообщение неактуально, ответьте «СТОП» — больше писать не буду."
MSK = timezone(timedelta(hours=3))  # Moscow has no DST; avoids needing tzdata in the image
FOLLOWUP_AFTER = timedelta(days=3)
DEFAULTS = {"daily_limit": 100, "delay_seconds": 60, "mode": "cold"}
LIMITS = {"daily_limit": (1, 100), "delay_seconds": (30, 3600)}
RESULTS = {"sent", "not_sent", "no_account", "stop", "replied"}
SITE_RANK = {"no_site": 0, "weak_site": 1, "unavailable": 2}
BRACKETS = re.compile(r"\[[^\]]+\]")
USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
NOT_USERNAMES = {"joinchat", "addstickers", "share", "proxy", "socks", "s", "c", "iv", "boost"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS outreach (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    stage TEXT NOT NULL CHECK(stage IN ('first','followup')),
    actor TEXT NOT NULL, channel TEXT NOT NULL CHECK(channel IN ('username','phone')),
    contact TEXT NOT NULL, message TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('opened','sent','not_sent','no_account','stop','replied')),
    opened_at TEXT NOT NULL, resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_outreach_actor_time ON outreach(actor, opened_at);
CREATE INDEX IF NOT EXISTS idx_outreach_business ON outreach(business_id, stage);
CREATE TABLE IF NOT EXISTS outreach_dead_contacts (
    business_id TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    contact TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY(business_id, contact)
);
CREATE TABLE IF NOT EXISTS outreach_pins (
    business_id TEXT PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);
"""


def ensure_schema(path: Path = DB_PATH) -> None:
    with db.connection(path) as conn:
        conn.executescript(SCHEMA)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(timezone.utc)


def _day_start(moment: datetime) -> datetime:
    local = moment.astimezone(MSK)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)


# --- settings -----------------------------------------------------------------------------

def _get(conn, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM desktop_settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def _put(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO desktop_settings VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))


def settings(path: Path = DB_PATH) -> dict:
    with db.connection(path) as conn:
        result = dict(DEFAULTS)
        for key, (low, high) in LIMITS.items():
            try:
                value = int(_get(conn, f"outreach_{key}", str(DEFAULTS[key])))
                result[key] = value if low <= value <= high else DEFAULTS[key]
            except ValueError:
                pass
        mode = _get(conn, "outreach_mode", DEFAULTS["mode"])
        result["mode"] = mode if mode in {"cold", "consent"} else DEFAULTS["mode"]
    return result


def save_settings(values: dict, path: Path = DB_PATH) -> dict:
    values = dict(values or {})
    clean = {}
    for key, (low, high) in LIMITS.items():
        if key in values:
            try:
                number = int(values[key])
            except (TypeError, ValueError):
                raise ValueError("Укажите целые числа") from None
            if not low <= number <= high:
                raise ValueError({"daily_limit": f"Дневной лимит: от {low} до {high}",
                                  "delay_seconds": f"Пауза: от {low} до {high} секунд"}[key])
            clean[key] = str(number)
    if "mode" in values:
        if values["mode"] not in {"cold", "consent"}:
            raise ValueError("Режим: cold или consent")
        clean["mode"] = values["mode"]
    with db.connection(path) as conn:
        for key, value in clean.items():
            _put(conn, f"outreach_{key}", value)
    return settings(path)


def acknowledge(actor: str, path: Path = DB_PATH) -> bool:
    with db.connection(path) as conn:
        _put(conn, f"outreach_ack:{actor}", db.now())
    return True


# --- contacts -----------------------------------------------------------------------------

def _usernames(socials) -> list[str]:
    found = []
    for item in socials or []:
        url = item.get("url") if isinstance(item, dict) else item
        parsed = urlparse(str(url or "").strip())
        host = (parsed.hostname or "").casefold()
        if host not in {"t.me", "telegram.me", "www.t.me"}:
            continue
        name = parsed.path.strip("/").split("/")[0]
        if USERNAME.match(name) and name.casefold() not in NOT_USERNAMES and name not in found:
            found.append(name)
    return found


def contacts(business: dict, dead: set[str] | None = None) -> list[dict]:
    """Telegram usernames first, then mobile numbers; contacts marked «нет аккаунта» are dropped."""
    dead = dead or set()
    result = [{"channel": "username", "contact": "@" + name, "url": f"https://t.me/{name}"}
              for name in _usernames(business.get("socials"))]
    for link in build_telegram_phone_links(business.get("phones") or []):
        if link["ok"]:
            result.append({"channel": "phone", "contact": "+" + link["e164"], "url": link["url"]})
    return [item for item in result if item["contact"].casefold() not in dead]


def _dead(conn, business_id: str) -> set[str]:
    return {row[0].casefold() for row in conn.execute(
        "SELECT contact FROM outreach_dead_contacts WHERE business_id=?", (business_id,))}


def _with_footer(message: str) -> str:
    from .domain import strip_links
    message = strip_links(message)   # no links in outgoing messages (docs/next/DECISIONS.md N29)
    if OPT_OUT_FOOTER.casefold() in message.casefold():
        return message
    return f"{message}\n\n{OPT_OUT_FOOTER}"


def chat_url(base_url: str, message: str) -> str:
    return f"{base_url}?text={quote(message, safe='')}"


# --- queue --------------------------------------------------------------------------------

def _budget(conn, actor: str, policy: dict) -> dict:
    now = _now()
    start = _iso(_day_start(now))
    used = conn.execute("""SELECT COUNT(*) FROM outreach WHERE actor=? AND opened_at>=?
        AND status NOT IN ('not_sent','no_account')""", (actor, start)).fetchone()[0]
    last = conn.execute("SELECT MAX(opened_at) FROM outreach WHERE actor=?", (actor,)).fetchone()[0]
    wait = 0
    if last:
        wait = max(0, int(policy["delay_seconds"] - (now - _parse(last)).total_seconds() + 0.999))
    return {"used": used, "limit": policy["daily_limit"], "left": max(0, policy["daily_limit"] - used),
            "wait_seconds": wait}


def script_for(business: dict, stage: str, path: Path = DB_PATH, category: str = "site") -> tuple[str, str]:
    """(slug, rendered text) of the message for an outreach stage: `first` picks the subtype by
    site type and niche, `followup` uses the category's `reminder`. Empty when no script fits."""
    rows = {row["slug"]: row for row in db.templates(path) if row.get("origin", "builtin") == "builtin"}
    slug = pick_slug(business, category, "reminder" if stage == "followup" else "first", rows)
    return (slug, render_template(rows[slug]["body"], business)) if slug else ("", "")


def _item(business: dict, stage: str, dead: set[str], extra: dict | None = None,
          path: Path = DB_PATH) -> dict | None:
    options = contacts(business, dead)
    if not options:
        return None
    slug, text = script_for(business, stage, path)
    return {"business_id": business["id"], "name": business["name"], "city": business.get("city", ""),
            "niche": business.get("niche", ""), "site_type": business.get("site_type"),
            "rating": business.get("rating"), "reviews": business.get("reviews"),
            "stage": stage, "template": slug, "text": text, "footer": OPT_OUT_FOOTER,
            "contacts": options, **(extra or {})}


def today(actor: str, *, limit: int = 30, path: Path = DB_PATH) -> dict:
    policy = settings(path)
    now = _now()
    due_before = _iso(now - FOLLOWUP_AFTER)
    with db.connection(path) as conn:
        budget = _budget(conn, actor, policy)
        acknowledged = bool(_get(conn, f"outreach_ack:{actor}"))
        suppressed = {row[0] for row in conn.execute("SELECT business_id FROM suppressions")}
        pending = [dict(row) for row in conn.execute("""SELECT id,business_id,stage,contact,opened_at FROM outreach
            WHERE actor=? AND status='opened' ORDER BY opened_at""", (actor,))]
        followups = [row[0] for row in conn.execute("""SELECT o.business_id FROM outreach o
            JOIN businesses b ON b.id=o.business_id
            WHERE o.stage='first' AND o.status='sent' AND o.resolved_at<=? AND b.status='waiting'
              AND NOT EXISTS (SELECT 1 FROM outreach f WHERE f.business_id=o.business_id AND f.stage='followup'
                              AND f.status IN ('opened','sent','stop','replied'))
            GROUP BY o.business_id ORDER BY MIN(o.resolved_at)""", (due_before,))]
        pins = [row[0] for row in conn.execute("SELECT business_id FROM outreach_pins ORDER BY created_at")]
        fresh = [row[0] for row in conn.execute("""SELECT b.id FROM businesses b
            WHERE b.status='not_started'
              AND NOT EXISTS (SELECT 1 FROM hidden_businesses h WHERE h.business_id=b.id)
              AND NOT EXISTS (SELECT 1 FROM outreach o WHERE o.business_id=b.id AND o.stage='first'
                              AND o.status IN ('opened','sent','stop','replied'))
            ORDER BY CASE b.site_type WHEN 'no_site' THEN 0 WHEN 'weak_site' THEN 1 WHEN 'unavailable' THEN 2 ELSE 3 END,
                     COALESCE(b.rating,0) DESC, COALESCE(b.reviews,0) DESC, b.created_at
            LIMIT 400""")]

    waiting = []
    for row in pending:
        business = db.business(row["business_id"], path)
        if business:
            waiting.append({"outreach_id": row["id"], "business_id": business["id"], "name": business["name"],
                            "stage": row["stage"], "contact": row["contact"], "opened_at": row["opened_at"]})
    items, seen = [], {row["business_id"] for row in pending}
    for stage, ids in (("followup", followups), ("first", pins), ("first", fresh)):
        for business_id in ids:
            if len(items) >= limit:
                break
            if business_id in seen or business_id in suppressed:
                continue
            business = db.business(business_id, path)
            if not business or (stage == "first" and business["status"] != "not_started" and business_id not in pins):
                continue
            with db.connection(path) as conn:
                dead = _dead(conn, business_id)
            item = _item(business, stage, dead, {"pinned": business_id in pins}, path)
            if item:
                items.append(item)
                seen.add(business_id)
    return {"settings": policy, "budget": budget, "acknowledged": acknowledged,
            "awaiting": waiting, "items": items}


def add(business_id: str, path: Path = DB_PATH) -> bool:
    if not db.business(str(business_id), path):
        raise ValueError("Организация не найдена")
    with db.connection(path) as conn:
        conn.execute("INSERT OR IGNORE INTO outreach_pins VALUES (?,?)", (str(business_id), db.now()))
    return True


def prepare(actor: str, business_id: str, stage: str, contact: str, message: str,
            path: Path = DB_PATH) -> dict:
    """Reserve a slot and return the t.me link with the text filled in."""
    business_id, contact = str(business_id), str(contact or "").strip()
    if stage not in {"first", "followup"}:
        raise ValueError("Неизвестный этап")
    business = db.business(business_id, path)
    if not business:
        raise ValueError("Организация не найдена")
    if business.get("do_not_contact"):
        raise ValueError("Организация в блок-листе: ей писать нельзя")
    text = str(message or "").strip()
    if not text:
        raise ValueError("Текст сообщения пустой")
    if BRACKETS.search(text):
        raise ValueError("Заполните поля в квадратных скобках")
    text = _with_footer(text)
    policy = settings(path)
    with db.connection(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        option = next((item for item in contacts(business, _dead(conn, business_id))
                       if item["contact"].casefold() == contact.casefold()), None)
        if not option:
            raise ValueError("Этот контакт недоступен")
        if policy["mode"] == "consent":
            permission = db.contact_permission(option["contact"].lstrip("@").casefold(), None,
                                               option["contact"] if option["channel"] == "phone" else None, path)
            if not permission or permission["status"] != "allowed":
                raise ValueError("Режим «только с согласием»: сначала отметьте согласие адресата")
        open_row = conn.execute("SELECT 1 FROM outreach WHERE actor=? AND status='opened'", (actor,)).fetchone()
        if open_row:
            raise ValueError("Сначала отметьте результат предыдущего сообщения")
        if conn.execute("""SELECT 1 FROM outreach WHERE business_id=? AND stage=?
                AND status IN ('opened','sent','stop','replied')""", (business_id, stage)).fetchone():
            raise ValueError("Этой организации уже писали на этом этапе")
        if conn.execute("""SELECT 1 FROM outreach WHERE lower(contact)=lower(?) AND message=?
                AND status IN ('sent','replied')""", (option["contact"], text)).fetchone():
            raise ValueError("Этот текст уже отправляли этому адресату")
        budget = _budget(conn, actor, policy)
        if budget["left"] <= 0:
            raise ValueError(f"Дневной лимит исчерпан: {budget['used']} из {budget['limit']}")
        if budget["wait_seconds"] > 0:
            raise ValueError(f"Пауза между сообщениями: подождите {budget['wait_seconds']} с")
        cursor = conn.execute("""INSERT INTO outreach (business_id,stage,actor,channel,contact,message,status,opened_at)
            VALUES (?,?,?,?,?,?,'opened',?)""", (business_id, stage, actor, option["channel"], option["contact"],
                                                 text, db.now()))
        conn.execute("DELETE FROM outreach_pins WHERE business_id=?", (business_id,))
    return {"outreach_id": cursor.lastrowid, "url": chat_url(option["url"], text), "message": text}


def mark(actor: str, outreach_id: int, result: str, path: Path = DB_PATH) -> dict:
    """Record what happened after «Открыть чат»."""
    if result not in RESULTS:
        raise ValueError("Неизвестный результат")
    with db.connection(path) as conn:
        row = conn.execute("SELECT * FROM outreach WHERE id=? AND actor=?", (int(outreach_id), actor)).fetchone()
        if not row:
            raise ValueError("Запись не найдена")
        if row["status"] != "opened":
            raise ValueError("Результат уже отмечен")
        status = {"stop": "stop"}.get(result, result)
        conn.execute("UPDATE outreach SET status=?,resolved_at=? WHERE id=?", (status, db.now(), row["id"]))
        if result == "no_account":
            conn.execute("INSERT OR IGNORE INTO outreach_dead_contacts VALUES (?,?,?,?)",
                         (row["business_id"], row["contact"], "Нет аккаунта Telegram", db.now()))
    business_id = row["business_id"]
    if result == "sent" and row["stage"] == "first":
        db.set_business(business_id, "waiting", None, path)
    elif result == "replied":
        db.set_business(business_id, "replied", None, path)
    elif result == "stop":
        db.set_suppression(business_id, True, "Попросили не писать", path)
        db.set_business(business_id, "unsuitable", None, path)
    return {"outreach_id": row["id"], "status": status}


MAILING_ACTOR = "mailing"


def record_mailing(kind: str, data: dict, path: Path = DB_PATH) -> None:
    """«Рассылка» → «Лиды». ``sent``: the lead gets «Написал» (only from «Не начато»/«Позвонить») and an
    outreach entry like a manual send, so «Сегодня» offers a reminder after FOLLOWUP_AFTER and does
    not offer a second one after a mailing reminder. ``replied``: «Ответили». ``stop``: «Не писать»
    and «Отказ», as for a manual «Попросили не писать». The mailing actor has its own daily budget."""
    if kind in ("taken", "released") and data.get("ids") is not None:
        for business_id in data["ids"]:
            record_mailing(kind, {"business_id": business_id}, path)
        return
    business_id = str(data.get("business_id") or "")
    business = db.business(business_id, path) if business_id else None
    if not business:
        return
    ensure_schema(path)
    if kind == "sent":
        stage = "first" if (data.get("stage") or "first") == "first" else "followup"
        username = str(data.get("username") or "").lstrip("@")
        stamp = db.now()
        with db.connection(path) as conn:
            conn.execute("""INSERT INTO outreach (business_id,stage,actor,channel,contact,message,status,
                opened_at,resolved_at) VALUES (?,?,?,?,?,?,?,?,?)""",
                (business_id, stage, MAILING_ACTOR, "username", "@" + username if username else "",
                 str(data.get("message") or "")[:4000], "sent", stamp, stamp))
        if business["status"] in {"not_started", "call"}:
            db.set_business(business_id, "waiting", None, path)
    elif kind == "replied":
        if business["status"] in {"not_started", "call", "waiting"}:
            db.set_business(business_id, "replied", None, path)
    elif kind == "stop":
        db.set_suppression(business_id, True, "Попросили не писать (ответ в Telegram)", path)
        db.set_business(business_id, "unsuitable", None, path)
    elif kind == "taken":
        # Taken into a new mailing campaign (N34): «Новый» → «Написал» at once, so the next batch takes
        # other leads. No outreach entry: «Сегодня» offers a reminder only after a real send («sent»).
        if business["status"] == "not_started":
            db.set_business(business_id, "waiting", None, path)
    elif kind == "released":
        # The campaign was deleted before anything went out: back to «Новый», unless the owner changed the
        # status meanwhile or the lead was written to some other way.
        with db.connection(path) as conn:
            written = conn.execute("SELECT 1 FROM outreach WHERE business_id=? LIMIT 1", (business_id,)).fetchone()
        if business["status"] == "waiting" and not written:
            db.set_business(business_id, "not_started", None, path)


ACTIVE_STATUSES = ("waiting", "call", "replied", "working")


def contacted(limit: int = 100, path: Path = DB_PATH) -> list[dict]:
    """Leads already in conversation, newest activity first, for quick status changes."""
    with db.connection(path) as conn:
        rows = conn.execute(f"""SELECT b.id,b.name,b.status,b.updated_at,
                (SELECT t.city FROM business_tags t WHERE t.business_id=b.id LIMIT 1) AS city,
                (SELECT o.contact FROM outreach o WHERE o.business_id=b.id AND o.status IN ('sent','replied')
                   ORDER BY o.id DESC LIMIT 1) AS contact,
                (SELECT o.channel FROM outreach o WHERE o.business_id=b.id AND o.status IN ('sent','replied')
                   ORDER BY o.id DESC LIMIT 1) AS channel,
                (SELECT MAX(COALESCE(o.resolved_at,o.opened_at)) FROM outreach o WHERE o.business_id=b.id
                   AND o.status IN ('sent','replied')) AS written_at,
                (SELECT MAX(e.changed_at) FROM status_events e WHERE e.business_id=b.id) AS status_at
            FROM businesses b WHERE b.status IN ({",".join("?" * len(ACTIVE_STATUSES))})
            ORDER BY COALESCE((SELECT MAX(e.changed_at) FROM status_events e WHERE e.business_id=b.id), b.updated_at) DESC
            LIMIT ?""", (*ACTIVE_STATUSES, int(limit))).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        contact = item.get("contact") or ""
        item["chat_url"] = (f"https://t.me/{contact.lstrip('@')}" if item.get("channel") == "username"
                            else f"https://t.me/{contact}" if contact else "")
        result.append(item)
    return result
