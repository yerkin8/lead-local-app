"""Child process: DATA_DIR applies before app modules are imported. No network, no Chrome."""
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.config import DB_PATH, MEDIA_DIR  # noqa: E402
from app.jobs import recover_interrupted, run_job  # noqa: E402
from app.media import MediaBlocked  # noqa: E402

out = {}

# 1. An old server database (init_db only) with CRM data is migrated additively, with a backup.
db.init_db()
job0 = db.create_job("search", {"niche": "кафе"})
db.upsert_business({"id": "77", "name": "Старое кафе", "site_type": "no_site", "address": "Казань",
                    "map_url": "https://yandex.ru/maps/org/x/77/"}, ("кафе", "Россия", "Казань"), job0)
db.set_business("77", "waiting", "заметка")
db.update_job(job0, status="done", result={})
db.init_desktop_db()
out["backups"] = len(list((DB_PATH.parent / "backups").glob("*.sqlite3")))
kept = db.business("77")
out["kept"] = [kept["status"], kept["note"]]
db.init_desktop_db()
out["backups_after_second_start"] = len(list((DB_PATH.parent / "backups").glob("*.sqlite3")))

# 2. Search: details mode is forced on, a blocked partial result ends as "blocked".
seen = {}


def fake_search(item):
    seen["details"] = item["params"].get("_desktop_details")
    return {"matched": 1, "reason": "Яндекс временно ограничил запросы", "blocked": True}


created = db.create_desktop_parse_jobs({"country": "Россия", "niche": "бар", "count": 5,
                                        "types": ["no_site"], "review_limit": 0}, ["Уфа"])
item = db.claim_job()
out["search_status"] = run_job(item, runners={"search": fake_search})
out["details"] = seen["details"]
with db.connection() as conn:
    out["run_status"] = conn.execute("SELECT status FROM parse_runs").fetchone()[0]

# 3. Gallery blocked by captcha -> "blocked", and it can be resumed.
from app import media_service  # noqa: E402

gallery, _ = db.create_media_job("images", {"url": "https://yandex.ru/maps/org/x/77/"}, "77", "77")


def blocked(_item):
    raise MediaBlocked("Яндекс показал проверку")


out["gallery_blocked"] = run_job(db.claim_job(), runners={"images": blocked})
out["resume"] = media_service.resume_images(gallery["id"])["status"]


# 4. Gallery done on the server is zipped for the bot.
def fake_gallery(_item):
    folder = MEDIA_DIR / "Кафе - Казань" / "Галерея" / "2026"
    (folder / "Еда").mkdir(parents=True)
    (folder / "Еда" / "1.jpg").write_bytes(b"x" * 1000)
    (folder / "Внутри").mkdir()
    (folder / "Внутри" / "2.jpg").write_bytes(b"y" * 1000)
    return {"folder": str(folder.relative_to(MEDIA_DIR)), "photos": 2}


out["gallery_done"] = run_job(db.claim_job(), package_archives=True, runners={"images": fake_gallery})
result = db.job(gallery["id"])["result"]
out["archives"] = result.get("archives")
with zipfile.ZipFile(MEDIA_DIR / gallery["id"] / result["archives"][0]) as archive:
    out["zip_names"] = sorted(archive.namelist())
out["archive_path_ok"] = media_service.archive_path(gallery["id"], result["archives"][0]).is_file()

# 5. Unknown kinds fail cleanly; a restart requeues running jobs.
weird = db.create_job("weird", {})
out["weird"] = run_job(db.claim_job(), runners={})
other = db.create_job("search", {"niche": "x"})
db.claim_job()
recover_interrupted()
out["requeued"] = db.job(other)["status"]
print(json.dumps(out, ensure_ascii=False))
