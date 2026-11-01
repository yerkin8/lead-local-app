"""Local-only operations exposed through the desktop bridge."""

from __future__ import annotations

import json
import os
import re
import sqlite3
import shutil
import threading
import time
import uuid
from contextlib import closing
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

from . import db
from .driver_registry import quit_driver
from .config import DATA_DIR, DB_PATH
from .domain import COUNTRIES, SITE_TYPES, STATUSES, org_id


class LocalWorker:
    def __init__(self):
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self):
        from .jobs import recover_interrupted
        recover_interrupted()
        self.thread = threading.Thread(target=self.loop, daemon=True, name="LEM jobs")
        self.thread.start()

    def shutdown(self, timeout: float = 50) -> bool:
        self.stop.set()
        from .driver_registry import close_all
        close_all()
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=max(0, timeout))
        return not (self.thread and self.thread.is_alive())

    def loop(self):
        from .jobs import run_job

        while not self.stop.is_set():
            item = db.claim_job()
            if not item:
                self.stop.wait(1)
                continue
            run_job(item)


MAX_MIN_REVIEWS = 10_000_000


def validate_search(payload: dict) -> dict:
    country = str(payload.get("country", "")).strip()
    raw_cities = payload.get("cities", payload.get("city", ""))
    if isinstance(raw_cities, (list, tuple)):
        city_items = [str(value) for value in raw_cities]
    else:
        city_items = re.split(r"[,;\n\r]+", str(raw_cities or ""))
    cities = []
    seen_cities = set()
    for value in city_items:
        city = re.sub(r"\s+", " ", value).strip()
        key = unicodedata.normalize("NFKC", city).casefold().replace("ё", "е")
        if city and key not in seen_cities:
            cities.append(city)
            seen_cities.add(key)
    niche = re.sub(r"\s+", " ", str(payload.get("niche", ""))).strip()
    city = cities[0] if cities else ""
    count = int(payload.get("count", 0))
    types = list(dict.fromkeys(payload.get("types") or []))
    review_limit = int(payload.get("review_limit", 20))
    try:
        min_reviews = int(str(payload.get("min_reviews") or 0).strip() or 0)
    except ValueError:
        raise ValueError("Минимум отзывов: укажите целое число") from None
    if (country not in COUNTRIES or not cities or len(cities) > 20
            or any(not 2 <= len(item) <= 100 for item in cities) or not 2 <= len(niche) <= 100):
        raise ValueError("Проверьте страну, список городов и нишу (от 1 до 20 городов)")
    if not 1 <= count <= 100 or not types or not set(types) <= SITE_TYPES:
        raise ValueError("Укажите цель от 1 до 100 и типы сайтов")
    if not 0 <= review_limit <= 100:
        raise ValueError("Лимит отзывов должен быть от 0 до 100")
    if not 0 <= min_reviews <= MAX_MIN_REVIEWS:
        raise ValueError(f"Минимум отзывов: от 0 до {MAX_MIN_REVIEWS:,}".replace(",", " "))
    return {"country": country, "city": city, "cities": cities, "niche": niche, "count": count,
            "types": types, "review_limit": review_limit, "min_reviews": min_reviews}


def start_desktop_search(payload: dict) -> dict:
    params = validate_search(payload)
    cities = params.pop("cities")
    params.pop("city", None)
    jobs = db.create_desktop_parse_jobs(params, cities)
    return {"jobs": jobs, "count": len(jobs)}


def stop_job(job_id: str) -> dict:
    """A queued job stops at once; a running one is asked to stop and ends as «Остановлен»
    at the next check (between cards, inside pauses and the review pass)."""
    from . import cancel
    item = db.job(job_id)
    if not item or item["kind"] != "search":
        raise ValueError("Задание не найдено")
    if db.stop_queued_job(job_id):
        cancel.forget(job_id)
        return {"id": job_id, "status": "stopped"}
    item = db.job(job_id)
    if item["status"] == "running":
        cancel.request(job_id)
        db.update_job(job_id, progress={**(item.get("progress") or {}), "stopping": True})
        return {"id": job_id, "status": "stopping"}
    raise ValueError("Задание уже завершено")


def search_results(job_id: str) -> list[dict]:
    item = db.job(job_id)
    if not item or item["kind"] != "search":
        raise ValueError("Задание поиска не найдено")
    with db.connection() as conn:
        ids = [row[0] for row in conn.execute(
            "SELECT business_id FROM job_results WHERE job_id=? ORDER BY rowid", (job_id,))]
    return [db.business(identity) for identity in ids]


def refresh_business(business_id: str) -> dict:
    from .lead_parser import make_driver, parse_org, collect_gallery_photo_count, collect_reviews_browser

    existing = db.business(business_id)
    if not existing:
        raise ValueError("Организация не найдена")
    driver = make_driver()
    try:
        data = parse_org(driver, existing["map_url"])
        if data["id"] != business_id:
            raise ValueError("Яндекс вернул другую организацию")
        db.refresh_business(data, store_details=True, review_limit=100)
        photo_count = collect_gallery_photo_count(driver, data["gallery_url"], existing["map_url"])
        if photo_count is not None:
            data["details"]["photo_count"] = photo_count
            db.refresh_business(data, store_details=True, review_limit=100)
        collect_reviews_browser(driver, data["review_url"], business_id, 100)
    finally:
        quit_driver(driver)
    return db.business_full(business_id)


EXPORT_COLUMNS = ["id", "name", "category", "phones", "socials", "website", "site_type",
                  "site_engine", "rating", "reviews", "address", "map_url", "status", "note",
                  "niche", "country", "city"]


def _safe_xlsx_value(value):
    return "'" + value if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"} else value


def export_xlsx(target: str, scope: dict | None = None, *, db_path: Path = DB_PATH) -> dict:
    from openpyxl import Workbook

    path = Path(target).expanduser().resolve()
    if path.suffix.lower() != ".xlsx":
        raise ValueError("Выберите файл XLSX")
    book = Workbook()
    sheet = book.active
    sheet.title = "Организации"
    sheet.append(EXPORT_COLUMNS)
    with db.connection(db_path) as conn:
        rows = conn.execute("""SELECT b.*,t.niche,t.country,t.city FROM businesses b
            LEFT JOIN business_tags t ON t.business_id=b.id
            WHERE t.rowid=(SELECT MIN(rowid) FROM business_tags WHERE business_id=b.id)
               OR t.business_id IS NULL ORDER BY b.created_at""").fetchall()
        details_rows = conn.execute("""SELECT d.business_id,d.description,d.latitude,d.longitude,d.photo_count,
            d.categories_json FROM business_details d ORDER BY d.business_id""").fetchall()
        review_rows = conn.execute("""SELECT b.id AS business_id,r.text,r.rating,r.review_date AS date,r.company_response
            FROM reviews r JOIN businesses b ON b.id=r.business_id ORDER BY b.id,r.review_date,r.id""").fetchall()
        link_rows = conn.execute("""SELECT b.id AS business_id,l.type,l.url,l.label FROM links l JOIN businesses b
            ON b.id=l.business_id ORDER BY b.id,l.type,l.url""").fetchall()
        hour_rows = conn.execute("""SELECT b.id AS business_id,h.weekday,h.opens,h.closes,h.is_closed,h.raw FROM hours h
            JOIN businesses b ON b.id=h.business_id ORDER BY b.id,h.weekday,h.opens""").fetchall()
        feature_rows = conn.execute("""SELECT b.id AS business_id,f.name,f.value FROM features f JOIN businesses b
            ON b.id=f.business_id ORDER BY b.id,f.name,f.value""").fetchall()
    if scope is not None:
        allowed = db.business_ids_for_scope(scope, db_path)
        rows = [row for row in rows if row["id"] in allowed]
        details_rows = [row for row in details_rows if row["business_id"] in allowed]
        review_rows = [row for row in review_rows if row["business_id"] in allowed]
        link_rows = [row for row in link_rows if row["business_id"] in allowed]
        hour_rows = [row for row in hour_rows if row["business_id"] in allowed]
        feature_rows = [row for row in feature_rows if row["business_id"] in allowed]
    for row in rows:
        # Excel must treat imported user text as text, never as a formula.
        sheet.append([_safe_xlsx_value(row[key]) for key in EXPORT_COLUMNS])
    detail_sheets = (
        ("Подробности", ["business_id", "description", "latitude", "longitude", "photo_count", "categories_json"], details_rows),
        ("Отзывы", ["business_id", "text", "rating", "date", "company_response"], review_rows),
        ("Ссылки", ["business_id", "type", "url", "label"], link_rows),
        ("График", ["business_id", "weekday", "opens", "closes", "is_closed", "raw"], hour_rows),
        ("Особенности", ["business_id", "name", "value"], feature_rows),
    )
    counts = {"organizations": len(rows)}
    for title, columns, items in detail_sheets:
        target_sheet = book.create_sheet(title)
        target_sheet.append(columns)
        for item in items:
            target_sheet.append([_safe_xlsx_value(item[column]) for column in columns])
        counts[title] = len(items)
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)
    return {"path": str(path), "count": len(rows), "details": counts}


def import_xlsx(source: str) -> dict:
    from openpyxl import load_workbook
    from urllib.parse import urlparse
    from .media import validate_org_url

    path = Path(source).expanduser().resolve()
    if path.suffix.lower() != ".xlsx" or not path.is_file():
        raise ValueError("Выберите существующий файл XLSX")
    book = load_workbook(path, read_only=True, data_only=True)
    sheet = book.active
    rows = sheet.iter_rows(values_only=True)
    header = [str(value or "").strip() for value in next(rows, [])]
    if "map_url" not in header and "url" not in header:
        raise ValueError("В XLSX нужен столбец map_url или url с адресом Яндекс.Карт")
    imported = duplicates = invalid = 0
    job_id = db.create_job("import", {"file": path.name})
    legacy_stem = re.sub(r"-\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$", "", path.stem)
    parts = legacy_stem.split("-")
    old_style = parts[0] in {"il", "nl"} and len(parts) >= 3
    fallback_city = parts[1].replace("_", " ") if old_style else ""
    fallback_niche = "-".join(parts[2:]).replace("_", " ") if old_style else "Импорт"

    def values_list(value):
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError:
                value = [value] if value else []
        if isinstance(value, dict):
            value = [value]
        return value if isinstance(value, list) else []

    for values in rows:
        row = dict(zip(header, values))
        try:
            map_url = validate_org_url(str(row.get("map_url") or row.get("url") or ""))
        except Exception:
            invalid += 1
            continue
        identity = str(row.get("id") or "").strip() or org_id(map_url)
        if not identity or not identity.isdigit() or org_id(map_url) != identity:
            invalid += 1
            continue
        if db.business(identity):
            duplicates += 1
            continue
        phones = values_list(row.get("phones")) or values_list(row.get("phone"))
        phones = [str(value).strip() for value in phones if str(value).strip()]
        socials = []
        for key in ("socials", "telegram", "whatsapp"):
            for value in values_list(row.get(key)):
                url = _plain_url(value.get("url") if isinstance(value, dict) else str(value))
                parsed = urlparse(url)
                if parsed.scheme == "https" and parsed.hostname:
                    socials.append({"label": parsed.hostname, "url": str(url)})
        socials = list({item["url"]: item for item in socials}.values())
        site_type = {"builder": "weak_site", "dead": "unavailable", "none": "no_site",
                     "social_only": "no_site", "real": "other"}.get(
                         str(row.get("site_type") or ""), row.get("site_type") or "no_site")
        data = {key: row.get(key) for key in EXPORT_COLUMNS}
        data.update({"id": identity, "name": str(row.get("name") or "Без названия"),
                     "phones": phones, "socials": socials,
                     "website": row.get("website") or row.get("site"),
                     "site_type": site_type,
                     "map_url": map_url})
        tag = (str(row.get("niche") or fallback_niche), str(row.get("country") or "Россия" if old_style else row.get("country") or ""),
               str(row.get("city") or fallback_city))
        db.upsert_business(data, tag, job_id)
        if row.get("status") in STATUSES or row.get("note"):
            db.set_business(identity, row.get("status") if row.get("status") in STATUSES else None,
                            str(row.get("note") or ""))
        imported += 1
    book.close()
    db.update_job(job_id, status="done", result={"imported": imported, "duplicates": duplicates, "invalid": invalid})
    return {"imported": imported, "duplicates": duplicates, "invalid": invalid}


MARKDOWN_LINK = re.compile(r"^\[([^\]]*)\]\(([^)\s]+)\)$")


def _plain_url(value) -> str:
    """Old parser XLSX files hold Telegram as a markdown link «[https://t.me/+7…](https://t.me/+7…)»."""
    text = str(value or "").strip()
    match = MARKDOWN_LINK.match(text)
    if match:
        text = match.group(2) if urlparse(match.group(2)).scheme else match.group(1)
    return text.strip()


def backup_database() -> str:
    folder = DATA_DIR / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (time.strftime("lem-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8] + ".sqlite3")
    # closing(): `with sqlite3.connect()` only commits; an open handle keeps the file locked on Windows.
    with closing(sqlite3.connect(DB_PATH)) as source, closing(sqlite3.connect(target)) as dest:
        source.backup(dest)
    return str(target)


# Per-organisation data that used to be lost on import (card details, reviews, links, base runs,
# hidden leads, «Сегодня»). Rows go only to organisations this import added: an organisation already
# in the local base keeps its own data. AUTOINCREMENT ids are dropped so they never clash.
BUSINESS_TABLES = ("business_details", "reviews", "links", "hours", "features", "hidden_businesses",
                   "outreach", "outreach_dead_contacts", "outreach_pins")
AUTO_ID_TABLES = {"reviews", "outreach"}


def _insert_rows(target, table: str, rows) -> None:
    target_columns = {column[1] for column in target.execute(f"PRAGMA table_info({table})")}
    if not target_columns:
        return
    for record in rows:
        columns = [column for column in record if column in target_columns]
        target.execute(f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                       [record[column] for column in columns])


def _copy_business_tables(source_db, target, tables: set[str], new_ids: set[str]) -> None:
    for table in BUSINESS_TABLES:
        if table not in tables or not new_ids:
            continue
        rows = []
        for row in source_db.execute(f"SELECT * FROM {table}"):
            record = dict(row)
            if record.get("business_id") not in new_ids:
                continue
            if table in AUTO_ID_TABLES:
                record.pop("id", None)
            rows.append(record)
        _insert_rows(target, table, rows)
    # Base hierarchy: runs whose job came over, then links of runs to organisations that exist here.
    if "parse_runs" in tables:
        jobs = {row[0] for row in target.execute("SELECT id FROM jobs")}
        _insert_rows(target, "parse_runs", [dict(row) for row in source_db.execute("SELECT * FROM parse_runs")
                                            if row["job_id"] in jobs])
    if "parse_run_businesses" in tables:
        runs = {row[0] for row in target.execute("SELECT id FROM parse_runs")}
        known = {row[0] for row in target.execute("SELECT id FROM businesses")}
        _insert_rows(target, "parse_run_businesses", [
            dict(row) for row in source_db.execute("SELECT * FROM parse_run_businesses")
            if row["parse_run_id"] in runs and row["business_id"] in known])


def _check_source_schema(path: Path) -> None:
    exists, version = db._schema_state(path)
    if exists and version > db.DESKTOP_SCHEMA_VERSION:
        raise ValueError(f"Эта база создана более новой версией приложения (схема {version}). "
                         "Обновите LeadEqualMoney и повторите.")


def replace_database(source: str) -> dict:
    """Make another app base the working one: backup → copy → migrate. Nothing is merged."""
    path = Path(source).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in {".sqlite3", ".db", ".sqlite"}:
        raise ValueError("Выберите файл базы SQLite")
    if path == DB_PATH.resolve():
        raise ValueError("Это текущая база приложения")
    _check_source_schema(path)
    check = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"businesses", "templates"} <= tables:
            raise ValueError("Файл не похож на базу приложения")
        if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Файл базы повреждён")
    finally:
        check.close()
    with db.connection() as current:
        running = current.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0]
    if running:
        raise ValueError("Сначала дождитесь окончания или остановите задания поиска и медиа")
    backup = backup_database()
    staging = DB_PATH.with_name(DB_PATH.stem + f".replace-{uuid.uuid4().hex[:8]}.sqlite3")
    try:
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as source_db,                 closing(sqlite3.connect(staging)) as dest:
            source_db.backup(dest)
        with closing(sqlite3.connect(DB_PATH)) as current_db:  # fold the WAL into the old file (already backed up)
            current_db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        for suffix in ("-wal", "-shm"):
            Path(str(DB_PATH) + suffix).unlink(missing_ok=True)
        os.replace(staging, DB_PATH)
    finally:
        staging.unlink(missing_ok=True)
    db.init_desktop_db()
    with db.connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0]
    return {"backup": backup, "businesses": count}


def _merge_templates(source_db, target) -> None:
    """Bring over the user's own scripts from an old database; local edits win.

    AI scripts and user copies are added as they are. A shipped script counts only when it was
    edited there: its text lands on the new slug (old flat slugs are mapped) unless the local
    one is edited too. Shipped defaults from any version are skipped.
    """
    from .script_legacy import LEGACY_TEMPLATES, V5_TEMPLATES
    from .script_templates import LEGACY_SLUGS, TEMPLATES, body_hash, is_primary_legacy, parse_slug, resolve_slug

    def keep_copy(row, category, stage, subtype, origin="custom", slug=None):
        """Add the text under a free slug unless the very same text is already there."""
        base = slug or f"custom_{row['slug']}"
        same = target.execute("SELECT 1 FROM templates WHERE (slug=? OR slug GLOB ?) AND body=?",
                              (base, base + "_[0-9]*", row["body"])).fetchone()
        if same:
            return
        title = row["title"] if origin != "custom" else f"Ваша правка · {row['title']}"[:60]
        target.execute("""INSERT INTO templates(slug,title,body,updated_at,origin,category,stage,subtype)
            VALUES (?,?,?,?,?,?,?,?)""", (db._free_slug(target, base), title, row["body"], row["updated_at"],
                                          origin, category, stage, subtype))

    source_columns = {column[1] for column in source_db.execute("PRAGMA table_info(templates)")}
    extra = [column for column in ("origin", "category", "stage", "subtype", "default_hash") if column in source_columns]
    for record in source_db.execute(f"SELECT slug,title,body,updated_at{''.join(',' + c for c in extra)} FROM templates"):
        row = dict(record)
        origin = row.get("origin") or "builtin"
        if origin != "builtin":
            keep_copy(row, row.get("category") or "", row.get("stage") or "", row.get("subtype") or "",
                      origin=origin, slug=row["slug"])
            continue
        shipped = {texts[row["slug"]][1] for texts in (V5_TEMPLATES, LEGACY_TEMPLATES) if row["slug"] in texts}
        slug = resolve_slug(row["slug"])
        if slug in TEMPLATES:
            shipped.add(TEMPLATES[slug][1])
        # A shipped text of any version (v6+ rows carry the hash of what they shipped with).
        if row["body"] in shipped or (row.get("default_hash") and body_hash(row["body"]) == row["default_hash"]):
            continue
        category, stage, subtype = parse_slug(slug) or ("general", "", "")
        local = target.execute("SELECT body,default_hash FROM templates WHERE slug=?", (slug,)).fetchone()
        owns_place = row["slug"] not in LEGACY_SLUGS or is_primary_legacy(row["slug"])
        if owns_place and local and body_hash(local["body"]) == local["default_hash"]:
            target.execute("UPDATE templates SET body=?,updated_at=? WHERE slug=?", (row["body"], row["updated_at"], slug))
        elif not local or local["body"] != row["body"]:
            # The local script is edited too, the old slug does not own this place, or the slug is
            # unknown here: keep the imported text as the user's own copy so nothing is lost.
            keep_copy(row, category, stage, subtype)


def import_sqlite(source: str) -> dict:
    """Merge a former Mini App database; keep existing local CRM data."""
    path = Path(source).expanduser().resolve()
    if not path.is_file() or path.suffix.lower() not in {".sqlite3", ".db", ".sqlite"}:
        raise ValueError("Выберите старую SQLite-базу")
    if path == DB_PATH.resolve():
        raise ValueError("Это текущая база приложения")
    _check_source_schema(path)
    backup_database()
    source_db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    source_db.row_factory = sqlite3.Row
    imported = duplicate = archives = 0
    new_ids: set[str] = set()
    try:
        tables = {row[0] for row in source_db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"businesses", "business_tags", "templates"} <= tables:
            raise ValueError("Файл не похож на базу приложения")
        with db.connection() as target:
            target.execute("BEGIN IMMEDIATE")
            for row in source_db.execute("SELECT * FROM businesses"):
                record = dict(row)
                if target.execute("SELECT 1 FROM businesses WHERE id=?", (record["id"],)).fetchone():
                    duplicate += 1
                    continue
                columns = [column for column in record if column in {
                    "id", "name", "category", "phones", "socials", "website", "site_type",
                    "site_engine", "rating", "reviews", "address", "map_url", "status",
                    "note", "created_at", "updated_at"}]
                target.execute(f"INSERT INTO businesses ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                               [record[column] for column in columns])
                imported += 1
                new_ids.add(record["id"])
            for table in ("business_tags", "jobs", "job_results", "media_links", "search_seen",
                          "suppressions", "business_telegram", "contact_permissions", "campaigns", "campaign_recipients",
                          "telegram_send_attempts",
                          "status_events"):
                if table not in tables:
                    continue
                for row in source_db.execute(f"SELECT * FROM {table}"):
                    record = dict(row)
                    if table == "jobs" and record.get("status") in {"queued", "running"}:
                        record["status"] = "failed"
                        record["error"] = "Импортировано незавершённое задание; запустите его заново"
                    if table == "status_events":
                        if record["business_id"] not in new_ids:
                            continue
                        record.pop("id", None)
                    target_columns = {column[1] for column in target.execute(f"PRAGMA table_info({table})")}
                    columns = [column for column in record if column in target_columns]
                    target.execute(f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                                   [record[column] for column in columns])
            _copy_business_tables(source_db, target, tables, new_ids)
            _merge_templates(source_db, target)
        db.init_desktop_db()
        old_media = path.parent / "media"
        if old_media.is_dir():
            from .config import MEDIA_DIR
            MEDIA_DIR.mkdir(parents=True, exist_ok=True)
            for job_dir in old_media.iterdir():
                if (job_dir.is_dir() and not job_dir.is_symlink() and len(job_dir.name) == 32
                        and all(c in "0123456789abcdef" for c in job_dir.name)
                        and not (MEDIA_DIR / job_dir.name).exists()):
                    shutil.copytree(job_dir, MEDIA_DIR / job_dir.name)
                    archives += 1
    finally:
        source_db.close()
    return {"imported": imported, "duplicates": duplicate, "archive_folders": archives}
