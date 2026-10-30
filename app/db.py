from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import DB_PATH
from .domain import STATUSES, normalized, org_id
from .script_templates import TEMPLATES
from .yandex_urls import normalize_yandex_url


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


DETAILS_SCHEMA_VERSION = 1
DESKTOP_SCHEMA_VERSION = 3


def _schema_state(path: Path) -> tuple[bool, int]:
    if not path.is_file() or path.stat().st_size == 0:
        return False, 0
    source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    try:
        tables = {row[0] for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not tables:
            return False, 0
        if "schema_migrations" not in tables:
            return True, 0
        row = source.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        return True, int(row[0] or 0)
    finally:
        source.close()


def _backup_before_migration(path: Path, target_version: int) -> Path | None:
    """Take a consistent SQLite backup before the first additive schema upgrade."""
    exists, current_version = _schema_state(path)
    if not exists or current_version >= target_version:
        return None
    folder = path.parent / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = folder / f"{path.stem}-pre-schema-v{target_version}-{stamp}-{uuid.uuid4().hex[:8]}.sqlite3"
    source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
    destination = sqlite3.connect(target, timeout=30)
    try:
        source.backup(destination)
        check = destination.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise sqlite3.DatabaseError("Pre-migration SQLite backup failed integrity_check")
    except Exception:
        destination.close()
        source.close()
        target.unlink(missing_ok=True)
        raise
    destination.close()
    source.close()
    return target


@contextmanager
def connection(path: Path = DB_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=30000")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


LEGACY_TEMPLATES = {
    "first": ("Первое сообщение", "Здравствуйте! Меня зовут [ваше имя]. Увидел {название} в {город}. Занимаюсь сайтами для бизнеса в сфере «{ниша}». Можно задам пару вопросов о том, как к вам сейчас приходят новые клиенты?"),
    "short": ("Короткое сообщение", "Здравствуйте! Увидел {название} в {город}. Помогаю бизнесам в сфере «{ниша}» с сайтом и заявками. Актуально обсудить?"),
    "detailed": ("Подробное сообщение", "Здравствуйте! Я занимаюсь сайтами для бизнеса. Обратил внимание на {название}: по открытым данным {тип_сайта}. Могу предложить несколько вариантов, как удобнее представить услуги и принимать заявки. Если интересно, пришлю короткие идеи без обязательств."),
    "call": ("Скрипт звонка", "Здравствуйте! Меня зовут [ваше имя]. Подскажите, удобно 30 секунд? Увидел {название} и хотел узнать, кто у вас отвечает за сайт и привлечение заявок. Я могу предложить идеи для вашей сферы."),
    "followup": ("Повторное сообщение", "Здравствуйте! Возвращаюсь к сообщению о сайте для {название}. Если тема сейчас неактуальна, просто дайте знать — не буду отвлекать."),
    "secretary_message": ("Сообщение секретарю", "Здравствуйте! Подскажите, кому в {название} лучше направить короткое предложение по сайту и приёму заявок?"),
    "secretary_call": ("Звонок секретарю", "Здравствуйте! Подскажите, кто у вас принимает решения по сайту и маркетингу? Хотел бы адресовать предложение конкретному человеку."),
    "meeting": ("Предложение на созвон", "Если вам удобно, можем созвониться на 15 минут и обсудить задачи {название}. Какое время вам подходит?"),
    "payment": ("Договориться об оплате", "Чтобы согласовать оплату, я пришлю состав работ, сроки и сумму. Подскажите, какой формат документов вам удобен?"),
    "deal": ("Сделка и следующие шаги", "Зафиксирую наши договорённости: [объём], [сроки], [стоимость]. Следующий шаг — [действие]. Всё верно?"),
    "nudge": ("Мягко подтолкнуть", "Здравствуйте! Хотел уточнить, успели ли вы посмотреть предложение для {название}. Если остались вопросы, с радостью отвечу."),
    "packages": ("Три пакета услуг", "Могу предложить три варианта: базовый — [состав], стандарт — [состав], расширенный — [состав]. Подберём исходя из ваших задач и бюджета."),
    "prepayment": ("Объяснить предоплату", "Предоплата закрепляет время на проект и покрывает стартовые работы. В договоре зафиксируем этапы, сроки и результат каждого этапа."),
    "upsell": ("Новая услуга бывшему клиенту", "Здравствуйте! После нашего проекта у меня появилась идея, которая может быть полезна {название}: [идея]. Если интересно, кратко расскажу."),
    "expensive": ("Возражение: дорого", "Понимаю. Давайте уточним, какие задачи для вас главные, и я предложу объём работ под ваш бюджет без лишних функций."),
    "existing_vendor": ("Возражение: есть исполнитель", "Понимаю. Хорошо, что задача уже в работе. Если понадобится независимый взгляд или помощь с отдельным этапом, буду рад подключиться."),
    "not_needed": ("Возражение: не нужно", "Понял. Спасибо за ответ. Если ситуация изменится, можно будет вернуться к разговору."),
    "thinking": ("Возражение: надо подумать", "Конечно. Подскажите, какая информация поможет принять решение? Могу коротко уточнить сроки, результат и стоимость."),
}


def init_db(path: Path = DB_PATH) -> None:
    with connection(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
        CREATE TABLE IF NOT EXISTS businesses (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT, phones TEXT NOT NULL DEFAULT '[]',
            socials TEXT NOT NULL DEFAULT '[]', website TEXT, site_type TEXT NOT NULL,
            site_engine TEXT, rating REAL, reviews INTEGER, address TEXT, map_url TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'not_started', note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS business_tags (
            business_id TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            niche TEXT NOT NULL, country TEXT NOT NULL, city TEXT NOT NULL,
            PRIMARY KEY(business_id,niche,country,city)
        );
        CREATE TABLE IF NOT EXISTS status_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, business_id TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            old_status TEXT NOT NULL, new_status TEXT NOT NULL, changed_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL, params TEXT NOT NULL,
            progress TEXT NOT NULL DEFAULT '{}', error TEXT, result TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS job_results (
            job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            business_id TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            PRIMARY KEY(job_id,business_id)
        );
        CREATE TABLE IF NOT EXISTS search_seen (
            search_key TEXT NOT NULL, business_id TEXT NOT NULL,
            checked_at TEXT NOT NULL,
            PRIMARY KEY(search_key,business_id)
        );
        CREATE TABLE IF NOT EXISTS media_links (
            job_id TEXT PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
            organization_id TEXT NOT NULL,
            business_id TEXT REFERENCES businesses(id) ON DELETE SET NULL
        );
        CREATE TABLE IF NOT EXISTS campaigns (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL,
            template_slug TEXT, video_path TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            error TEXT
        );
        CREATE TABLE IF NOT EXISTS campaign_recipients (
            campaign_id TEXT NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            business_id TEXT NOT NULL REFERENCES businesses(id),
            peer TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
            error TEXT, sent_at TEXT, telegram_id TEXT, PRIMARY KEY(campaign_id,business_id)
        );
        CREATE TABLE IF NOT EXISTS suppressions (
            business_id TEXT PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
            reason TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS contact_permissions (
            peer TEXT PRIMARY KEY, telegram_id TEXT, phone TEXT, business_id TEXT,
            status TEXT NOT NULL CHECK(status IN ('allowed','blocked')),
            source TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_contact_permissions_telegram_id
            ON contact_permissions(telegram_id);
        CREATE INDEX IF NOT EXISTS idx_contact_permissions_status
            ON contact_permissions(status,updated_at);
        CREATE TABLE IF NOT EXISTS telegram_send_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, campaign_id TEXT NOT NULL,
            business_id TEXT NOT NULL, peer TEXT NOT NULL,
            method TEXT NOT NULL CHECK(method IN ('manual','automatic')),
            status TEXT NOT NULL, attempted_at TEXT NOT NULL, sent_at TEXT, error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_telegram_send_attempts_time
            ON telegram_send_attempts(attempted_at,sent_at);
        CREATE TABLE IF NOT EXISTS business_telegram (
            business_id TEXT PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
            telegram_id TEXT NOT NULL, access_hash TEXT NOT NULL, username TEXT, phone TEXT,
            method TEXT NOT NULL, checked_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS templates (
            slug TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bot_flows (
            chat_id INTEGER PRIMARY KEY, kind TEXT NOT NULL, flow_id TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bot_flow_messages (
            chat_id INTEGER NOT NULL, flow_id TEXT NOT NULL, message_id INTEGER NOT NULL,
            created_at TEXT NOT NULL, PRIMARY KEY(chat_id,message_id)
        );
        CREATE INDEX IF NOT EXISTS idx_business_status ON businesses(status);
        CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status,created_at);
        CREATE INDEX IF NOT EXISTS idx_tags_place ON business_tags(country,city,niche);
        CREATE INDEX IF NOT EXISTS idx_media_org ON media_links(organization_id);
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(campaign_recipients)")}
        if "telegram_id" not in columns:
            db.execute("ALTER TABLE campaign_recipients ADD COLUMN telegram_id TEXT")
        business_telegram_columns = {row[1] for row in db.execute("PRAGMA table_info(business_telegram)")}
        if "phone" not in business_telegram_columns:
            db.execute("ALTER TABLE business_telegram ADD COLUMN phone TEXT")
        permission_columns = {row[1] for row in db.execute("PRAGMA table_info(contact_permissions)")}
        if "phone" not in permission_columns:
            db.execute("ALTER TABLE contact_permissions ADD COLUMN phone TEXT")
        campaign_columns = {row[1] for row in db.execute("PRAGMA table_info(campaigns)")}
        if "error" not in campaign_columns:
            db.execute("ALTER TABLE campaigns ADD COLUMN error TEXT")
        # Link media jobs created by the old bot when their Yandex ID is available.
        for row in db.execute("""SELECT j.id,j.params FROM jobs j LEFT JOIN media_links m ON m.job_id=j.id
                               WHERE j.kind IN ('images','products') AND m.job_id IS NULL"""):
            try:
                identity = org_id(json.loads(row["params"]).get("url", ""))
            except (TypeError, ValueError):
                identity = None
            if identity:
                business_id = db.execute("SELECT id FROM businesses WHERE id=?", (identity,)).fetchone()
                db.execute("INSERT OR IGNORE INTO media_links VALUES (?,?,?)",
                           (row["id"], identity, business_id[0] if business_id else None))
        db.execute("""UPDATE media_links SET business_id=organization_id WHERE business_id IS NULL
            AND organization_id IN (SELECT id FROM businesses)""")
        for slug, (title, body) in TEMPLATES.items():
            db.execute("INSERT OR IGNORE INTO templates VALUES (?,?,?,?)", (slug, title, body, now()))
        # Replace only untouched original defaults. User-edited scripts remain intact.
        for slug, (_, old_body) in LEGACY_TEMPLATES.items():
            if slug in TEMPLATES:
                title, body = TEMPLATES[slug]
                db.execute("""UPDATE templates SET title=?,body=?,updated_at=?
                    WHERE slug=? AND body=?""", (title, body, now(), slug, old_body))


def init_desktop_db(path: Path = DB_PATH) -> None:
    """Initialize desktop-only CRM extensions without changing server-mode startup."""
    _backup_before_migration(path, DESKTOP_SCHEMA_VERSION)
    init_db(path)
    with connection(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS business_details (
            business_id TEXT PRIMARY KEY REFERENCES businesses(id) ON DELETE CASCADE,
            description TEXT, latitude REAL, longitude REAL, photo_count INTEGER,
            categories_json TEXT NOT NULL DEFAULT '[]', details_complete INTEGER NOT NULL DEFAULT 0,
            reviews_complete INTEGER NOT NULL DEFAULT 0, requested_review_limit INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            business_id TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            review_key TEXT NOT NULL, text TEXT NOT NULL, rating REAL, review_date TEXT,
            company_response TEXT, inserted_at TEXT NOT NULL,
            UNIQUE(business_id,review_key)
        );
        CREATE TABLE IF NOT EXISTS links (
            business_id TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            type TEXT NOT NULL, url TEXT NOT NULL, label TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(business_id,type,url)
        );
        CREATE TABLE IF NOT EXISTS hours (
            business_id TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            weekday TEXT NOT NULL, opens TEXT NOT NULL DEFAULT '', closes TEXT NOT NULL DEFAULT '',
            is_closed INTEGER NOT NULL DEFAULT 0, raw TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(business_id,weekday,opens,closes,raw)
        );
        CREATE TABLE IF NOT EXISTS features (
            business_id TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            name TEXT NOT NULL, value TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(business_id,name,value)
        );
        CREATE TABLE IF NOT EXISTS desktop_settings (
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS parse_runs (
            id TEXT PRIMARY KEY,
            niche TEXT NOT NULL,
            niche_key TEXT NOT NULL,
            country TEXT NOT NULL,
            city TEXT NOT NULL,
            city_key TEXT NOT NULL,
            job_id TEXT NOT NULL UNIQUE REFERENCES jobs(id) ON DELETE CASCADE,
            started_at TEXT,
            ended_at TEXT,
            params_json TEXT NOT NULL,
            organization_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'queued',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS parse_run_businesses (
            parse_run_id TEXT NOT NULL REFERENCES parse_runs(id) ON DELETE CASCADE,
            business_id TEXT NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
            linked_at TEXT NOT NULL,
            PRIMARY KEY(parse_run_id,business_id)
        );
        CREATE INDEX IF NOT EXISTS idx_reviews_business_date ON reviews(business_id,review_date);
        CREATE INDEX IF NOT EXISTS idx_links_business_type ON links(business_id,type);
        CREATE INDEX IF NOT EXISTS idx_parse_runs_niche_city ON parse_runs(niche_key,city_key,created_at);
        CREATE INDEX IF NOT EXISTS idx_parse_run_business_business ON parse_run_businesses(business_id);
        """)
        timestamp = now()
        db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (?,?)", (DETAILS_SCHEMA_VERSION, timestamp))
        db.execute("INSERT OR IGNORE INTO schema_migrations VALUES (?,?)", (DESKTOP_SCHEMA_VERSION, timestamp))
        # Idempotent host migration. _backup_before_migration() ran before this transaction.
        for row in db.execute("SELECT id,map_url FROM businesses"):
            normalized_url = normalize_yandex_url(row["map_url"])
            if normalized_url != row["map_url"]:
                db.execute("UPDATE businesses SET map_url=?,updated_at=? WHERE id=?",
                           (normalized_url, now(), row["id"]))


def _label_key(value: str) -> tuple[str, str]:
    display = re.sub(r"\s+", " ", str(value or "")).strip()
    return display, normalized(display)


def create_desktop_parse_jobs(params: dict, cities: list[str], path: Path = DB_PATH) -> list[dict]:
    """Atomically create one desktop parse run and queue job for each requested city."""
    if not cities:
        raise ValueError("Укажите хотя бы один город")
    niche, niche_key = _label_key(params.get("niche", ""))
    if not niche or not niche_key:
        raise ValueError("Укажите нишу")
    country = str(params.get("country", "")).strip()
    base_params = {key: value for key, value in params.items() if key not in {"city", "cities"}}
    created = []
    seen_city_keys: set[str] = set()
    with connection(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        for raw_city in cities:
            city, city_key = _label_key(raw_city)
            if not city or not city_key or city_key in seen_city_keys:
                continue
            seen_city_keys.add(city_key)
            run_id = uuid.uuid4().hex
            job_id = uuid.uuid4().hex
            item_params = {**base_params, "city": city, "_desktop_details": True,
                           "_desktop_parse_run_id": run_id}
            stamp = now()
            conn.execute("INSERT INTO jobs(id,kind,status,params,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                         (job_id, "search", "queued", json.dumps(item_params, ensure_ascii=False), stamp, stamp))
            conn.execute("""INSERT INTO parse_runs
                (id,niche,niche_key,country,city,city_key,job_id,params_json,status,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (run_id, niche, niche_key, country, city, city_key, job_id,
                 json.dumps(base_params | {"city": city}, ensure_ascii=False), "queued", stamp))
            created.append({"id": job_id, "run_id": run_id, "kind": "search", "status": "queued",
                            "params": item_params, "created_at": stamp})
        if not created:
            raise ValueError("Не удалось определить города")
    return created


def start_parse_run(run_id: str, path: Path = DB_PATH) -> None:
    stamp = now()
    with connection(path) as conn:
        conn.execute("""UPDATE parse_runs SET status='running',started_at=COALESCE(started_at,?),ended_at=NULL
            WHERE id=?""", (stamp, run_id))


def finish_parse_run(run_id: str, status: str, path: Path = DB_PATH) -> None:
    if status not in {"done", "failed", "stopped", "interrupted"}:
        raise ValueError("Недопустимый статус запуска парсинга")
    with connection(path) as conn:
        conn.execute("""UPDATE parse_runs SET status=?,ended_at=?,organization_count=(
            SELECT COUNT(*) FROM parse_run_businesses WHERE parse_run_id=?) WHERE id=?""",
            (status, now(), run_id, run_id))


def link_parse_run_business(run_id: str, business_id: str, path: Path = DB_PATH) -> bool:
    with connection(path) as conn:
        if not conn.execute("SELECT 1 FROM parse_runs WHERE id=?", (run_id,)).fetchone():
            return False
        if not conn.execute("SELECT 1 FROM businesses WHERE id=?", (business_id,)).fetchone():
            return False
        conn.execute("INSERT OR IGNORE INTO parse_run_businesses VALUES (?,?,?)", (run_id, business_id, now()))
        conn.execute("UPDATE parse_runs SET organization_count=(SELECT COUNT(*) FROM parse_run_businesses WHERE parse_run_id=?) WHERE id=?",
                     (run_id, run_id))
        return True


def _legacy_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("""SELECT COUNT(*) FROM businesses b WHERE NOT EXISTS(
        SELECT 1 FROM parse_run_businesses rb WHERE rb.business_id=b.id)""").fetchone()[0])


def parse_niches(path: Path = DB_PATH) -> list[dict]:
    with connection(path) as conn:
        rows = [dict(row) for row in conn.execute("""SELECT MIN(niche) AS name,niche_key AS key,
            COUNT(DISTINCT city_key) AS city_count,COUNT(DISTINCT rb.business_id) AS organization_count,
            MAX(COALESCE(started_at,created_at)) AS latest_at
            FROM parse_runs p LEFT JOIN parse_run_businesses rb ON rb.parse_run_id=p.id
            GROUP BY niche_key ORDER BY name COLLATE NOCASE""")]
        recent_cities: dict[str, list[dict]] = {}
        for city in conn.execute("""SELECT p.niche_key,MIN(p.city) AS name,p.city_key AS key,
            COUNT(DISTINCT rb.business_id) AS organization_count,
            MAX(COALESCE(p.started_at,p.created_at)) AS latest_at
            FROM parse_runs p LEFT JOIN parse_run_businesses rb ON rb.parse_run_id=p.id
            GROUP BY p.niche_key,p.city_key ORDER BY latest_at DESC"""):
            items = recent_cities.setdefault(city["niche_key"], [])
            if len(items) < 4:
                items.append({"name": city["name"], "key": city["key"],
                             "organization_count": int(city["organization_count"]),
                             "latest_at": city["latest_at"]})
        legacy_count = _legacy_count(conn)
    for row in rows:
        row["recent_cities"] = recent_cities.get(row["key"], [])
        row["more_city_count"] = max(0, int(row["city_count"]) - len(row["recent_cities"]))
    if legacy_count:
        rows.append({"name": "Без ниши", "key": "__legacy_niche__", "city_count": 1,
                     "organization_count": legacy_count, "latest_at": None, "legacy": True,
                     "recent_cities": [{"name": "Без города", "key": "__legacy_city__",
                                        "organization_count": legacy_count, "latest_at": None}],
                     "more_city_count": 0})
    return rows


def parse_cities(niche_key: str, path: Path = DB_PATH) -> list[dict]:
    if niche_key == "__legacy_niche__":
        with connection(path) as conn:
            count = _legacy_count(conn)
        if not count:
            return []
        runs = parse_runs_for_city(niche_key, "__legacy_city__", path)
        return [{"name": "Без города", "key": "__legacy_city__", "organization_count": count,
                 "latest_at": None, "legacy": True, "recent_runs": runs[:4],
                 "more_run_count": max(0, len(runs) - 4), "run_count": len(runs)}]
    with connection(path) as conn:
        cities = [dict(row) for row in conn.execute("""SELECT MIN(city) AS name,city_key AS key,
            COUNT(DISTINCT rb.business_id) AS organization_count,
            MAX(COALESCE(p.started_at,p.created_at)) AS latest_at
            FROM parse_runs p LEFT JOIN parse_run_businesses rb ON rb.parse_run_id=p.id
            WHERE p.niche_key=? GROUP BY city_key ORDER BY name COLLATE NOCASE""", (niche_key,))]
        recent_runs: dict[str, list[dict]] = {}
        run_counts: dict[str, int] = {}
        for run in conn.execute("""SELECT p.city_key,p.id,COALESCE(p.started_at,p.created_at) AS display_at,
            p.started_at,p.ended_at,p.status,COUNT(rb.business_id) AS organization_count
            FROM parse_runs p LEFT JOIN parse_run_businesses rb ON rb.parse_run_id=p.id
            WHERE p.niche_key=? GROUP BY p.id ORDER BY display_at DESC""", (niche_key,)):
            run_counts[run["city_key"]] = run_counts.get(run["city_key"], 0) + 1
            items = recent_runs.setdefault(run["city_key"], [])
            if len(items) < 4:
                items.append({"id": run["id"], "display_at": run["display_at"],
                              "started_at": run["started_at"], "ended_at": run["ended_at"],
                              "status": run["status"], "organization_count": int(run["organization_count"])})
    for city in cities:
        city["recent_runs"] = recent_runs.get(city["key"], [])
        city["run_count"] = run_counts.get(city["key"], 0)
        city["more_run_count"] = max(0, city["run_count"] - len(city["recent_runs"]))
    return cities


def parse_runs_for_city(niche_key: str, city_key: str, path: Path = DB_PATH) -> list[dict]:
    if niche_key == "__legacy_niche__" and city_key == "__legacy_city__":
        with connection(path) as conn:
            rows = conn.execute("""SELECT substr(b.created_at,1,10) AS day,COUNT(*) AS organization_count
                FROM businesses b WHERE NOT EXISTS(SELECT 1 FROM parse_run_businesses rb WHERE rb.business_id=b.id)
                GROUP BY substr(b.created_at,1,10) ORDER BY day DESC""").fetchall()
        return [{"id": f"legacy:{row['day']}", "display_at": row["day"], "created_at": row["day"],
                 "started_at": None, "ended_at": None, "status": "done",
                 "organization_count": int(row["organization_count"]), "legacy": True}
                for row in rows]
    with connection(path) as conn:
        return [dict(row) for row in conn.execute("""SELECT p.id,p.niche,p.city,p.job_id,p.started_at,p.ended_at,
            p.status,p.created_at,COUNT(rb.business_id) AS organization_count,
            COALESCE(p.started_at,p.created_at) AS display_at
            FROM parse_runs p LEFT JOIN parse_run_businesses rb ON rb.parse_run_id=p.id
            WHERE p.niche_key=? AND p.city_key=? GROUP BY p.id
            ORDER BY display_at DESC,p.created_at DESC""", (niche_key, city_key))]


def parse_run_businesses(run_id: str, path: Path = DB_PATH, *, query: str = "", limit: int = 50,
                         offset: int = 0, status: str | None = None,
                         site_type: str | None = None) -> list[dict]:
    limit = min(200, max(1, int(limit)))
    offset = max(0, int(offset))
    conditions = ["(?='' OR b.name LIKE ? OR b.address LIKE ?)"]
    args: list = [query, f"%{query}%", f"%{query}%"]
    if status:
        conditions.append("b.status=?")
        args.append(status)
    if site_type:
        conditions.append("b.site_type=?")
        args.append(site_type)
    filters = " AND ".join(conditions)
    with connection(path) as conn:
        if run_id.startswith("legacy:"):
            day = run_id.partition(":")[2]
            rows = conn.execute("""SELECT b.id FROM businesses b
                WHERE substr(b.created_at,1,10)=? AND NOT EXISTS(
                    SELECT 1 FROM parse_run_businesses rb WHERE rb.business_id=b.id)
                AND """ + filters + " ORDER BY b.created_at DESC LIMIT ? OFFSET ?",
                (day, *args, limit, offset)).fetchall()
        else:
            rows = conn.execute("""SELECT b.id FROM parse_run_businesses rb
                JOIN businesses b ON b.id=rb.business_id WHERE rb.parse_run_id=?
                AND """ + filters + " ORDER BY b.created_at DESC LIMIT ? OFFSET ?",
                (run_id, *args, limit, offset)).fetchall()
        ids = [row[0] for row in rows]
    return [business(identity, path) for identity in ids]


def business_ids_for_scope(scope: dict, path: Path = DB_PATH) -> set[str]:
    level = scope.get("level")
    niche_key, city_key, run_id = scope.get("niche_key"), scope.get("city_key"), scope.get("run_id")
    with connection(path) as conn:
        if level == "run" and isinstance(run_id, str) and run_id.startswith("legacy:"):
            day = run_id.partition(":")[2]
            rows = conn.execute("""SELECT b.id FROM businesses b WHERE substr(b.created_at,1,10)=?
                AND NOT EXISTS(SELECT 1 FROM parse_run_businesses rb WHERE rb.business_id=b.id)""", (day,))
        elif level == "run" and run_id:
            rows = conn.execute("SELECT business_id FROM parse_run_businesses WHERE parse_run_id=?", (run_id,))
        elif niche_key == "__legacy_niche__":
            rows = conn.execute("""SELECT b.id FROM businesses b WHERE NOT EXISTS(
                SELECT 1 FROM parse_run_businesses rb WHERE rb.business_id=b.id)""")
        elif level == "city":
            rows = conn.execute("""SELECT DISTINCT rb.business_id FROM parse_runs p
                JOIN parse_run_businesses rb ON rb.parse_run_id=p.id WHERE p.niche_key=? AND p.city_key=?""",
                (niche_key, city_key))
        elif level == "niche":
            rows = conn.execute("""SELECT DISTINCT rb.business_id FROM parse_runs p
                JOIN parse_run_businesses rb ON rb.parse_run_id=p.id WHERE p.niche_key=?""", (niche_key,))
        else:
            raise ValueError("Неизвестный уровень экспорта")
        return {row[0] for row in rows}


def create_job(kind: str, params: dict, path: Path = DB_PATH) -> str:
    job_id = uuid.uuid4().hex
    with connection(path) as db:
        db.execute("INSERT INTO jobs (id,kind,status,params,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                   (job_id, kind, "queued", json.dumps(params, ensure_ascii=False), now(), now()))
    return job_id


def job(job_id: str, path: Path = DB_PATH) -> dict | None:
    with connection(path) as db:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return decode_job(row) if row else None


def decode_job(row: sqlite3.Row) -> dict:
    item = dict(row)
    for key in ("params", "progress", "result"):
        item[key] = json.loads(item[key])
    return item


def list_jobs(kind: str | None = None, path: Path = DB_PATH, limit: int = 100) -> list[dict]:
    with connection(path) as db:
        sql = "SELECT * FROM jobs" + (" WHERE kind=?" if kind else "") + " ORDER BY created_at DESC LIMIT ?"
        rows = db.execute(sql, (kind, limit) if kind else (limit,)).fetchall()
        return [decode_job(row) for row in rows]


def claim_job(path: Path = DB_PATH) -> dict | None:
    with connection(path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
        if not row:
            return None
        db.execute("UPDATE jobs SET status='running',updated_at=? WHERE id=?", (now(), row["id"]))
        item = decode_job(row)
        item["status"] = "running"
        return item


def update_job(job_id: str, *, status: str | None = None, progress: dict | None = None,
               error: str | None = None, result: dict | None = None, path: Path = DB_PATH) -> None:
    fields = ["updated_at=?"]
    values: list = [now()]
    for key, value in (("status", status), ("progress", progress), ("error", error), ("result", result)):
        if value is not None:
            fields.append(f"{key}=?")
            values.append(json.dumps(value, ensure_ascii=False) if key in {"progress", "result"} else value)
    values.append(job_id)
    with connection(path) as db:
        db.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE id=?", values)


def _review_key(review: dict) -> str:
    payload = {
        "text": re.sub(r"\s+", " ", str(review.get("text") or "")).strip().casefold(),
        "rating": review.get("rating"),
        "date": str(review.get("date") or "").strip(),
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _store_details(db: sqlite3.Connection, business_id: str, data: dict,
                   review_limit: int = 100) -> None:
    details = data.get("details") or {}
    db.execute("""INSERT INTO business_details
        (business_id,description,latitude,longitude,photo_count,categories_json,details_complete,
         requested_review_limit,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(business_id) DO UPDATE SET
        description=COALESCE(excluded.description,business_details.description),
        latitude=COALESCE(excluded.latitude,business_details.latitude),
        longitude=COALESCE(excluded.longitude,business_details.longitude),
        photo_count=COALESCE(excluded.photo_count,business_details.photo_count),
        categories_json=CASE WHEN excluded.categories_json='[]' THEN business_details.categories_json
                             ELSE excluded.categories_json END,
        details_complete=MAX(business_details.details_complete,excluded.details_complete),
        requested_review_limit=excluded.requested_review_limit,
        updated_at=excluded.updated_at""",
        (business_id, details.get("description"), details.get("latitude"), details.get("longitude"),
         details.get("photo_count"), json.dumps(details.get("categories") or [], ensure_ascii=False),
         int(bool(details.get("details_complete"))), min(100, max(0, int(review_limit))), now()))

    for item in data.get("links") or []:
        url = str(item.get("url") or "").strip()
        link_type = str(item.get("type") or "other")
        if url.startswith(("https://", "http://")) and link_type in {"website", "social", "messenger", "other"}:
            db.execute("INSERT OR IGNORE INTO links VALUES (?,?,?,?)",
                       (business_id, link_type, url, str(item.get("label") or "")[:200]))
    for item in data.get("hours") or []:
        weekday = str(item.get("weekday") or "").strip()[:40]
        if weekday:
            db.execute("INSERT OR IGNORE INTO hours VALUES (?,?,?,?,?,?)",
                       (business_id, weekday, str(item.get("opens") or "")[:20],
                        str(item.get("closes") or "")[:20], int(bool(item.get("closed"))),
                        str(item.get("raw") or "")[:200]))
    for item in data.get("features") or []:
        name = str(item.get("name") or "").strip()[:200]
        if name:
            db.execute("INSERT OR IGNORE INTO features VALUES (?,?,?)",
                       (business_id, name, str(item.get("value") or "")[:500]))
    _store_reviews(db, business_id, data.get("review_items") or [], review_limit)
    count = db.execute("SELECT COUNT(*) FROM reviews WHERE business_id=?", (business_id,)).fetchone()[0]
    if count >= min(100, max(0, int(review_limit))):
        db.execute("UPDATE business_details SET reviews_complete=1 WHERE business_id=?", (business_id,))


def _store_reviews(db: sqlite3.Connection, business_id: str,
                   reviews: list[dict], review_limit: int = 100) -> int:
    cap = min(100, max(0, int(review_limit)))
    stored = db.execute("SELECT COUNT(*) FROM reviews WHERE business_id=?", (business_id,)).fetchone()[0]
    added = 0
    for item in reviews:
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if not text or stored >= cap:
            continue
        key = _review_key(item)
        cursor = db.execute("""INSERT OR IGNORE INTO reviews
            (business_id,review_key,text,rating,review_date,company_response,inserted_at)
            VALUES (?,?,?,?,?,?,?)""",
            (business_id, key, text[:10000], item.get("rating"),
             str(item.get("date") or "")[:80] or None,
             str(item.get("company_response") or "")[:5000] or None, now()))
        if cursor.rowcount:
            added += 1
            stored += 1
    return added


def append_business_reviews(business_id: str, reviews: list[dict],
                            review_limit: int = 100, path: Path = DB_PATH) -> int:
    """Persist each fetched review batch so an interrupted desktop job can resume."""
    with connection(path) as conn:
        if not conn.execute("SELECT 1 FROM businesses WHERE id=?", (business_id,)).fetchone():
            return 0
        return _store_reviews(conn, business_id, reviews, review_limit)


def business_review_count(business_id: str, path: Path = DB_PATH) -> int:
    with connection(path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM reviews WHERE business_id=?", (business_id,)).fetchone()
        return int(row[0])


def set_review_collection_state(business_id: str, review_limit: int, complete: bool,
                                path: Path = DB_PATH) -> None:
    cap = min(100, max(0, int(review_limit)))
    with connection(path) as conn:
        count, expected = conn.execute("""SELECT (SELECT COUNT(*) FROM reviews WHERE business_id=?),
            reviews FROM businesses WHERE id=?""", (business_id, business_id)).fetchone()
        expected_count = cap if expected is None else int(expected)
        enough = count >= min(cap, expected_count)
        conn.execute("""UPDATE business_details SET requested_review_limit=?,reviews_complete=?,updated_at=?
            WHERE business_id=?""", (cap, int(bool(complete and enough)), now(), business_id))


def business_needs_review_resume(job_id: str, business_id: str, review_limit: int,
                                 path: Path = DB_PATH) -> bool:
    cap = min(100, max(0, int(review_limit)))
    if cap == 0:
        return False
    with connection(path) as conn:
        row = conn.execute("""SELECT d.reviews_complete,d.requested_review_limit,b.reviews,
            (SELECT COUNT(*) FROM reviews r WHERE r.business_id=b.id) AS stored,
            EXISTS(SELECT 1 FROM job_results jr WHERE jr.job_id=? AND jr.business_id=b.id) AS belongs
            FROM businesses b LEFT JOIN business_details d ON d.business_id=b.id WHERE b.id=?""",
            (job_id, business_id)).fetchone()
        return bool(row and row["belongs"] and not row["reviews_complete"]
                    and int(row["requested_review_limit"] or 0) > 0
                    and row["stored"] < min(cap, int(row["reviews"] or cap)))


def job_result_count(job_id: str, path: Path = DB_PATH) -> int:
    with connection(path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM job_results WHERE job_id=?", (job_id,)).fetchone()
        return int(row[0])


def incomplete_job_review_items(job_id: str, review_limit: int,
                                path: Path = DB_PATH) -> list[dict]:
    cap = min(100, max(0, int(review_limit)))
    if cap == 0:
        return []
    with connection(path) as conn:
        rows = conn.execute("""SELECT b.id,b.map_url FROM job_results jr
            JOIN businesses b ON b.id=jr.business_id
            JOIN business_details d ON d.business_id=b.id
            WHERE jr.job_id=? AND d.reviews_complete=0 AND d.requested_review_limit>0
              AND (SELECT COUNT(*) FROM reviews r WHERE r.business_id=b.id)
                  < MIN(?,COALESCE(NULLIF(b.reviews,0),?))
            ORDER BY jr.rowid""", (job_id, cap, cap)).fetchall()
        return [dict(row) for row in rows]


def upsert_business(data: dict, tag: tuple[str, str, str], job_id: str, path: Path = DB_PATH,
                    *, store_details: bool = False, review_limit: int = 100,
                    parse_run_id: str | None = None) -> bool:
    """Return True only for a new organization; never overwrite CRM fields."""
    business_id = str(data.get("id") or "").strip()
    if not business_id:
        identity_text = normalized(data.get("name") or "") + "\0" + normalized(data.get("address") or "")
        if identity_text == "\0":
            raise ValueError("Для организации без внешнего ID нужны название и адрес")
        business_id = "local:" + hashlib.sha256(identity_text.encode("utf-8")).hexdigest()[:32]
    with connection(path) as db:
        exists = db.execute("SELECT 1 FROM businesses WHERE id=?", (business_id,)).fetchone() is not None
        db.execute("""INSERT INTO businesses
            (id,name,category,phones,socials,website,site_type,site_engine,rating,reviews,address,map_url,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET
              name=excluded.name,category=excluded.category,phones=excluded.phones,
              socials=excluded.socials,website=excluded.website,site_type=excluded.site_type,
              site_engine=excluded.site_engine,rating=excluded.rating,reviews=excluded.reviews,
              address=excluded.address,map_url=excluded.map_url,updated_at=excluded.updated_at""",
            (business_id, data.get("name") or "Без названия", data.get("category"),
             json.dumps(data.get("phones", []), ensure_ascii=False),
             json.dumps(data.get("socials", []), ensure_ascii=False), data.get("website"),
             data["site_type"], data.get("site_engine"), data.get("rating"), data.get("reviews"),
             data.get("address"), normalize_yandex_url(data["map_url"]), now(), now()))
        db.execute("INSERT OR IGNORE INTO business_tags VALUES (?,?,?,?)", (business_id, *tag))
        db.execute("UPDATE media_links SET business_id=? WHERE organization_id=? AND business_id IS NULL",
                   (business_id, business_id))
        if store_details:
            _store_details(db, business_id, data, review_limit)
        if not exists:
            db.execute("INSERT OR IGNORE INTO job_results VALUES (?,?)", (job_id, business_id))
        if parse_run_id:
            db.execute("INSERT OR IGNORE INTO parse_run_businesses VALUES (?,?,?)",
                       (parse_run_id, business_id, now()))
            db.execute("UPDATE parse_runs SET organization_count=(SELECT COUNT(*) FROM parse_run_businesses WHERE parse_run_id=?) WHERE id=?",
                       (parse_run_id, parse_run_id))
        return not exists


def refresh_business(data: dict, path: Path = DB_PATH, *, store_details: bool = False,
                     review_limit: int = 100) -> bool:
    """Update public fields only; CRM and search results stay untouched."""
    with connection(path) as conn:
        if not conn.execute("SELECT 1 FROM businesses WHERE id=?", (data["id"],)).fetchone():
            return False
        conn.execute("""UPDATE businesses SET name=?,category=?,phones=?,socials=?,website=?,
            site_type=?,site_engine=?,rating=?,reviews=?,address=?,map_url=?,updated_at=? WHERE id=?""",
            (data.get("name") or "Без названия", data.get("category"),
             json.dumps(data.get("phones", []), ensure_ascii=False),
             json.dumps(data.get("socials", []), ensure_ascii=False), data.get("website"),
             data["site_type"], data.get("site_engine"), data.get("rating"), data.get("reviews"),
             data.get("address"), normalize_yandex_url(data["map_url"]), now(), data["id"]))
        if store_details:
            _store_details(conn, data["id"], data, review_limit)
    return True


def known_ids(path: Path = DB_PATH) -> set[str]:
    with connection(path) as db:
        return {row[0] for row in db.execute("SELECT id FROM businesses")}


def search_key(params: dict) -> str:
    """A changed count does not make a previously checked search new."""
    return json.dumps({
        "country": normalized(params["country"]),
        "city": normalized(params["city"]),
        "niche": normalized(params["niche"]),
        "types": sorted(set(params["types"])),
    }, ensure_ascii=False, sort_keys=True)


def checked_ids(key: str, path: Path = DB_PATH) -> set[str]:
    with connection(path) as db:
        return {row[0] for row in db.execute(
            "SELECT business_id FROM search_seen WHERE search_key=?", (key,))}


def mark_checked(key: str, business_id: str, path: Path = DB_PATH) -> None:
    with connection(path) as db:
        db.execute("INSERT OR IGNORE INTO search_seen VALUES (?,?,?)",
                   (key, business_id, now()))


def create_media_job(kind: str, params: dict, organization_id: str,
                     business_id: str | None, *, repeat: bool = False,
                     path: Path = DB_PATH) -> tuple[dict, bool]:
    """Create one job per intent; an active job always wins over a repeated click."""
    if kind not in {"images", "products"}:
        raise ValueError("Неизвестный тип медиа")
    with connection(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute("""SELECT j.* FROM jobs j JOIN media_links m ON m.job_id=j.id
                               WHERE m.organization_id=? AND j.kind=?
                               ORDER BY j.created_at DESC,j.rowid DESC""",
                            (organization_id, kind)).fetchall()
        for row in rows:
            if row["status"] in {"queued", "running"}:
                return decode_job(row), False
        if not repeat:
            for row in rows:
                if row["status"] == "done":
                    return decode_job(row), False
        job_id = uuid.uuid4().hex
        conn.execute("INSERT INTO jobs (id,kind,status,params,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                     (job_id, kind, "queued", json.dumps(params, ensure_ascii=False), now(), now()))
        conn.execute("INSERT INTO media_links VALUES (?,?,?)", (job_id, organization_id, business_id))
        return decode_job(conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()), True


def list_media_jobs(business_id: str | None = None, path: Path = DB_PATH) -> list[dict]:
    with connection(path) as conn:
        query = """SELECT j.*,m.organization_id,m.business_id FROM jobs j
                   JOIN media_links m ON m.job_id=j.id"""
        args: list = []
        if business_id is not None:
            query += " WHERE m.business_id=?"
            args.append(business_id)
        query += " ORDER BY j.created_at DESC,j.rowid DESC"
        rows = conn.execute(query, args).fetchall()
    result = []
    for row in rows:
        item = decode_job(row)
        result.append(item)
    return result


def activity_snapshot(known: dict | None = None, path: Path = DB_PATH) -> dict:
    """Return changed activity for selected topics and the active task count."""
    tracked = known if isinstance(known, dict) else {}

    default_topics = ("search", "media", "campaigns")
    raw_topics = tracked.get("topics", default_topics)
    if not isinstance(raw_topics, (list, tuple, set)):
        raw_topics = default_topics
    topics = set(raw_topics) & set(default_topics)

    def revisions_for(key: str) -> dict[str, str | None]:
        values = tracked.get(key, [])
        if isinstance(values, dict):
            return {str(identity): str(revision) if revision is not None else None
                    for identity, revision in list(values.items())[:100] if identity}
        if isinstance(values, (list, tuple, set)):
            return {str(identity): None for identity in list(values)[:100] if identity}
        return {}

    search_known = revisions_for("search") if "search" in topics else {}
    media_known = revisions_for("media") if "media" in topics else {}
    campaign_known = revisions_for("campaigns") if "campaigns" in topics else {}

    def where_for(revisions: dict[str, str | None], prefix: str = "") -> tuple[str, list[str]]:
        ids = list(revisions)
        column = f"{prefix}id"
        if not ids:
            return f"{prefix}status IN ('queued','running')", []
        placeholders = ",".join("?" for _ in ids)
        return f"({prefix}status IN ('queued','running') OR {column} IN ({placeholders}))", ids

    search_rows = media_rows = campaign_rows = []
    search_active = media_active = campaign_active = 0
    with connection(path) as conn:
        if "search" in topics:
            search_where, search_args = where_for(search_known)
            search_rows = conn.execute(
                f"SELECT * FROM jobs WHERE kind='search' AND {search_where} ORDER BY created_at,rowid",
                search_args,
            ).fetchall()
            search_active = sum(row["status"] in {"queued", "running"} for row in search_rows)

        if "media" in topics:
            media_where, media_args = where_for(media_known, "j.")
            media_rows = conn.execute(
                f"SELECT j.*,m.organization_id,m.business_id FROM jobs j "
                f"JOIN media_links m ON m.job_id=j.id WHERE {media_where} ORDER BY j.created_at,j.rowid",
                media_args,
            ).fetchall()
            media_active = sum(row["status"] in {"queued", "running"} for row in media_rows)

        if "campaigns" in topics:
            campaign_where, campaign_args = where_for(campaign_known, "c.")
            campaign_rows = conn.execute(
                f"SELECT c.id,c.title,c.status,c.error,c.updated_at,COUNT(r.business_id) AS recipients, "
                f"COALESCE(SUM(CASE WHEN r.status='sent' THEN 1 ELSE 0 END),0) AS sent "
                f"FROM campaigns c LEFT JOIN campaign_recipients r ON r.campaign_id=c.id "
                f"WHERE {campaign_where} GROUP BY c.id ORDER BY c.created_at,c.rowid",
                campaign_args,
            ).fetchall()
            campaign_active = sum(row["status"] in {"queued", "running"} for row in campaign_rows)

        campaign_items = []
        for row in campaign_rows:
            item = dict(row)
            item["recipients_state"] = [dict(recipient) for recipient in conn.execute(
                "SELECT business_id,status,error FROM campaign_recipients "
                "WHERE campaign_id=? AND status<>'draft' ORDER BY rowid", (item["id"],)
            ).fetchall()]
            campaign_items.append(item)

    search = [decode_job(row) for row in search_rows]
    media = [decode_job(row) for row in media_rows]
    for item, row in zip(media, media_rows):
        item["organization_id"] = row["organization_id"]
        item["business_id"] = row["business_id"]

    def changed(items: list[dict], previous: dict[str, str | None]) -> list[dict]:
        result = []
        for item in items:
            revision = hashlib.sha256(json.dumps(
                item, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")).hexdigest()[:16]
            if previous.get(str(item["id"])) != revision:
                item["revision"] = revision
                result.append(item)
        return result

    return {
        "search": changed(search, search_known),
        "media": changed(media, media_known),
        "campaigns": changed(campaign_items, campaign_known),
        "active_count": search_active + media_active + campaign_active,
    }


def business(business_id: str, path: Path = DB_PATH) -> dict | None:
    with connection(path) as db:
        row = db.execute("SELECT * FROM businesses WHERE id=?", (business_id,)).fetchone()
        if not row:
            return None
        result = dict(row)
        result["map_url"] = normalize_yandex_url(result["map_url"])
        result["phones"] = json.loads(result["phones"])
        result["socials"] = json.loads(result["socials"])
        suppression = db.execute("SELECT reason FROM suppressions WHERE business_id=?", (business_id,)).fetchone()
        result["do_not_contact"] = bool(suppression)
        result["suppression_reason"] = suppression["reason"] if suppression else ""
        telegram = db.execute("""SELECT telegram_id,username,phone,method,checked_at FROM business_telegram
            WHERE business_id=?""", (business_id,)).fetchone()
        result["telegram"] = dict(telegram) if telegram else None
        tags = db.execute("SELECT niche,country,city FROM business_tags WHERE business_id=?", (business_id,)).fetchall()
        result["tags"] = [dict(tag) for tag in tags]
        if tags:
            result.update(dict(tags[0]))
        result["history"] = [dict(e) for e in db.execute(
            "SELECT old_status,new_status,changed_at FROM status_events WHERE business_id=? ORDER BY id DESC", (business_id,))]
        return result


def business_full(business_id: str, path: Path = DB_PATH) -> dict | None:
    """Desktop-only extended card; the shared business() shape remains unchanged."""
    result = business(business_id, path)
    if result is None:
        return None
    with connection(path) as conn:
        details = conn.execute("SELECT * FROM business_details WHERE business_id=?", (business_id,)).fetchone()
        result["details"] = dict(details) if details else {
            "description": None, "latitude": None, "longitude": None, "photo_count": None,
            "categories_json": "[]", "details_complete": 0, "reviews_complete": 0,
            "requested_review_limit": 0,
        }
        result["details"]["categories"] = json.loads(result["details"].pop("categories_json", "[]"))
        result["review_items"] = [dict(row) for row in conn.execute("""SELECT text,rating,review_date,
            company_response FROM reviews WHERE business_id=? ORDER BY COALESCE(review_date,'') DESC,id DESC LIMIT 100""",
            (business_id,))]
        result["links"] = [dict(row) for row in conn.execute(
            "SELECT type,url,label FROM links WHERE business_id=? ORDER BY type,url", (business_id,))]
        result["hours"] = [dict(row) for row in conn.execute(
            "SELECT weekday,opens,closes,is_closed,raw FROM hours WHERE business_id=? ORDER BY weekday,opens",
            (business_id,))]
        result["features"] = [dict(row) for row in conn.execute(
            "SELECT name,value FROM features WHERE business_id=? ORDER BY name,value", (business_id,))]
    return result


def desktop_diagnostics_enabled(path: Path = DB_PATH) -> bool:
    with connection(path) as conn:
        row = conn.execute("SELECT value FROM desktop_settings WHERE key='debug_save_html'").fetchone()
        return bool(row and row[0] == "1")


def set_desktop_diagnostics_enabled(enabled: bool, path: Path = DB_PATH) -> bool:
    with connection(path) as conn:
        conn.execute("INSERT INTO desktop_settings(key,value) VALUES('debug_save_html',?) "
                     "ON CONFLICT(key) DO UPDATE SET value=excluded.value", ("1" if enabled else "0",))
    return bool(enabled)


def list_businesses(filters: dict, path: Path = DB_PATH, limit: int = 50, offset: int = 0) -> list[dict]:
    conditions, args = [], []
    for key in ("niche", "country", "city"):
        if filters.get(key):
            conditions.append(f"t.{key}=?")
            args.append(filters[key])
    for key in ("site_type", "status"):
        if filters.get(key):
            conditions.append(f"b.{key}=?")
            args.append(filters[key])
    if filters.get("q"):
        conditions.append("(b.name LIKE ? OR b.address LIKE ?)")
        args.extend([f"%{filters['q']}%"] * 2)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    with connection(path) as db:
        ids = [row[0] for row in db.execute(
            "SELECT DISTINCT b.id FROM businesses b JOIN business_tags t ON t.business_id=b.id" +
            where + " ORDER BY b.created_at DESC LIMIT ? OFFSET ?", [*args, limit, offset])]
    return [business(item, path) for item in ids]


def filter_options(path: Path = DB_PATH) -> dict:
    with connection(path) as db:
        options = {key: [row[0] for row in db.execute(f"SELECT DISTINCT {key} FROM business_tags ORDER BY {key}")]
                   for key in ("niche", "country", "city")}
        options["status"] = [row[0] for row in db.execute("SELECT DISTINCT status FROM businesses ORDER BY status")]
        return options


def set_business(business_id: str, status: str | None, note: str | None, path: Path = DB_PATH) -> bool:
    if status is not None and status not in STATUSES:
        raise ValueError("Неизвестный статус")
    with connection(path) as db:
        row = db.execute("SELECT status FROM businesses WHERE id=?", (business_id,)).fetchone()
        if not row:
            return False
        if status is not None and status != row["status"]:
            db.execute("UPDATE businesses SET status=?,updated_at=? WHERE id=?", (status, now(), business_id))
            db.execute("INSERT INTO status_events (business_id,old_status,new_status,changed_at) VALUES (?,?,?,?)",
                       (business_id, row["status"], status, now()))
        if note is not None:
            db.execute("UPDATE businesses SET note=?,updated_at=? WHERE id=?", (note, now(), business_id))
    return True


def set_suppression(business_id: str, blocked: bool, reason: str = "", path: Path = DB_PATH) -> bool:
    with connection(path) as conn:
        if not conn.execute("SELECT 1 FROM businesses WHERE id=?", (business_id,)).fetchone():
            return False
        if blocked:
            conn.execute("INSERT INTO suppressions VALUES (?,?,?) ON CONFLICT(business_id) DO UPDATE SET reason=excluded.reason",
                         (business_id, reason.strip() or "Не писать", now()))
        else:
            conn.execute("DELETE FROM suppressions WHERE business_id=?", (business_id,))
    return True


def suppressed_ids(path: Path = DB_PATH) -> set[str]:
    with connection(path) as conn:
        return {row[0] for row in conn.execute("SELECT business_id FROM suppressions")}


def contact_permission(peer: str, telegram_id: str | None = None, phone: str | None = None,
                       path: Path = DB_PATH) -> dict | None:
    with connection(path) as conn:
        row = conn.execute("""SELECT * FROM contact_permissions
            WHERE peer=? OR (? IS NOT NULL AND telegram_id=?) OR (? IS NOT NULL AND phone=?)
            ORDER BY CASE status WHEN 'blocked' THEN 0 ELSE 1 END, updated_at DESC LIMIT 1""",
            (peer, telegram_id, telegram_id, phone, phone)).fetchone()
    return dict(row) if row else None


def set_contact_permission(peer: str, telegram_id: str | None, business_id: str,
                            status: str, source: str, note: str = "",
                            phone: str | None = None,
                            path: Path = DB_PATH) -> dict:
    if not peer or status not in {"allowed", "blocked"}:
        raise ValueError("Не удалось определить контакт или правило связи")
    if status == "allowed" and source not in {"explicit_consent", "inbound_message"}:
        raise ValueError("Согласие можно отметить только как полученное или как входящее сообщение")
    if status == "blocked" and source != "opt_out":
        raise ValueError("Отказ должен быть отмечен как отписка или просьба больше не писать")
    note = note.strip()
    if status == "allowed" and len(note) < 3:
        raise ValueError("Добавьте короткую заметку, где или когда получено согласие")
    timestamp = now()
    with connection(path) as conn:
        if status == "allowed":
            existing_block = conn.execute("""SELECT 1 FROM contact_permissions
                WHERE status='blocked' AND (peer=? OR (? IS NOT NULL AND telegram_id=?)
                    OR (? IS NOT NULL AND phone=?)) LIMIT 1""",
                (peer, telegram_id, telegram_id, phone, phone)).fetchone()
            if existing_block:
                raise ValueError("Контакт отказался от сообщений; повторно разрешить отправку нельзя")
        row = conn.execute("SELECT status,created_at FROM contact_permissions WHERE peer=?", (peer,)).fetchone()
        if row and row["status"] == "blocked" and status != "blocked":
            raise ValueError("Контакт отказался от сообщений; отказ сохранён")
        created_at = row["created_at"] if row else timestamp
        conn.execute("""INSERT INTO contact_permissions
            (peer,telegram_id,phone,business_id,status,source,note,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(peer) DO UPDATE SET
            telegram_id=COALESCE(excluded.telegram_id,contact_permissions.telegram_id),
            phone=COALESCE(excluded.phone,contact_permissions.phone),
            business_id=excluded.business_id,status=excluded.status,source=excluded.source,
            note=excluded.note,updated_at=excluded.updated_at""",
            (peer, telegram_id, phone, business_id, status, source, note, created_at, timestamp))
        saved = conn.execute("SELECT * FROM contact_permissions WHERE peer=?", (peer,)).fetchone()
    return dict(saved)


def list_contact_permissions(status: str = "blocked", path: Path = DB_PATH) -> list[dict]:
    if status not in {"allowed", "blocked"}:
        raise ValueError("Неизвестный статус контакта")
    with connection(path) as conn:
        rows = conn.execute("""SELECT p.*,b.name AS business_name FROM contact_permissions p
            LEFT JOIN businesses b ON b.id=p.business_id
            WHERE p.status=? ORDER BY p.updated_at DESC""", (status,)).fetchall()
    return [dict(row) for row in rows]


def stats(path: Path = DB_PATH) -> dict:
    with connection(path) as db:
        total = db.execute("SELECT COUNT(*) FROM businesses").fetchone()[0]
        status = {row[0]: row[1] for row in db.execute("SELECT status,COUNT(*) FROM businesses GROUP BY status")}
        types = {row[0]: row[1] for row in db.execute("SELECT site_type,COUNT(*) FROM businesses GROUP BY site_type")}
        grouped = [dict(row) for row in db.execute("""SELECT t.niche,t.country,t.city,b.site_type,
            COUNT(DISTINCT t.business_id) AS count FROM business_tags t
            JOIN businesses b ON b.id=t.business_id
            GROUP BY t.niche,t.country,t.city,b.site_type ORDER BY count DESC""")]
        timeline = [dict(row) for row in db.execute("""SELECT substr(changed_at,1,10) AS day,new_status AS status,COUNT(*) AS count
            FROM status_events GROUP BY day,new_status ORDER BY day DESC LIMIT 90""")]
        contacted = db.execute("""SELECT COUNT(DISTINCT business_id) FROM status_events
            WHERE new_status IN ('waiting','replied','working','completed')""").fetchone()[0]
        replied = db.execute("""SELECT COUNT(DISTINCT business_id) FROM status_events
            WHERE new_status IN ('replied','working','completed')""").fetchone()[0]
    return {"total": total, "statuses": {key: status.get(key, 0) for key in STATUSES},
            "site_types": types, "groups": grouped, "timeline": timeline,
            "contacted": contacted, "replied": replied,
            "completed": status.get("completed", 0)}


def templates(path: Path = DB_PATH) -> list[dict]:
    with connection(path) as db:
        rows = [dict(row) for row in db.execute("SELECT * FROM templates")]
        order = {slug: index for index, slug in enumerate(TEMPLATES)}
        return sorted(rows, key=lambda row: order.get(row["slug"], len(order)))


def save_template(slug: str, body: str, path: Path = DB_PATH) -> bool:
    with connection(path) as db:
        cur = db.execute("UPDATE templates SET body=?,updated_at=? WHERE slug=?", (body, now(), slug))
        return cur.rowcount > 0


def rotate_bot_flow(chat_id: int, kind: str, flow_id: str, path: Path = DB_PATH) -> list[int]:
    """Start a new bot workflow and return recent messages from the previous one."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat(timespec="seconds")
    with connection(path) as db:
        db.execute("BEGIN IMMEDIATE")
        old = db.execute("SELECT flow_id FROM bot_flows WHERE chat_id=?", (chat_id,)).fetchone()
        rows = db.execute("""SELECT message_id FROM bot_flow_messages
            WHERE chat_id=? AND flow_id=? AND created_at>=? ORDER BY message_id""",
            (chat_id, old["flow_id"], cutoff)).fetchall() if old else []
        db.execute("DELETE FROM bot_flow_messages WHERE chat_id=?", (chat_id,))
        db.execute("""INSERT INTO bot_flows(chat_id,kind,flow_id) VALUES (?,?,?)
            ON CONFLICT(chat_id) DO UPDATE SET kind=excluded.kind,flow_id=excluded.flow_id""",
            (chat_id, kind, flow_id))
        return [row["message_id"] for row in rows]


def active_bot_flow(chat_id: int, flow_id: str, path: Path = DB_PATH) -> bool:
    with connection(path) as db:
        return db.execute("SELECT 1 FROM bot_flows WHERE chat_id=? AND flow_id=?",
                          (chat_id, flow_id)).fetchone() is not None


def record_bot_flow_message(chat_id: int, flow_id: str, message_id: int,
                            path: Path = DB_PATH) -> bool:
    with connection(path) as db:
        db.execute("BEGIN IMMEDIATE")
        if not db.execute("SELECT 1 FROM bot_flows WHERE chat_id=? AND flow_id=?",
                          (chat_id, flow_id)).fetchone():
            return False
        db.execute("""INSERT OR IGNORE INTO bot_flow_messages
            (chat_id,flow_id,message_id,created_at) VALUES (?,?,?,?)""",
            (chat_id, flow_id, message_id, now()))
        return True


def prepare_media_delete(job_id: str, path: Path = DB_PATH) -> bool:
    with connection(path) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT kind,status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row or row["kind"] not in {"images", "products"} or row["status"] == "running":
            return False
        db.execute("UPDATE jobs SET status='deleting',updated_at=? WHERE id=?", (now(), job_id))
        return True


def delete_job(job_id: str, path: Path = DB_PATH) -> bool:
    with connection(path) as db:
        row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row or row["status"] != "deleting":
            return False
        db.execute("DELETE FROM jobs WHERE id=?", (job_id,))
        return True
