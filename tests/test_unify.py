"""Merge of the Mini App mechanics into Desktop: guards that the plain module tests do not cover.

- «Рассылка» (app/mailing, unchanged) still finds its preferred script and skips hidden / suppressed leads;
- a base written by a newer app is refused before anything touches it;
- import keeps card details, reviews, base runs and «Сегодня» of the organisations it adds;
- Telegram from old parser XLSX files (markdown links) is not lost.
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(code: str, data_dir: str) -> dict:
    env = {**os.environ, "DATA_DIR": data_dir, "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True,
                         text=True, encoding="utf-8", timeout=120)
    if out.returncode:
        raise AssertionError(out.stderr[-3000:])
    return json.loads(out.stdout.strip().splitlines()[-1])


SEED = """
from app import db
db.init_desktop_db(); job = db.create_job('search', {})
for identity, site_type in (('1', ''), ('2', 'no_site'), ('3', 'weak_site'), ('4', 'unavailable'), ('5', 'other'),
                            ('6', 'no_site'), ('7', 'no_site')):
    db.upsert_business({'id': identity, 'name': 'Орг ' + identity, 'site_type': site_type,
                        'map_url': f'https://yandex.ru/maps/org/x/{identity}/',
                        'socials': [{'url': 'https://t.me/user' + identity}]}, ('кафе', 'Россия', 'Тула'), job)
"""


class MailingIntegrationTests(unittest.TestCase):
    def test_mailing_preferred_script_exists_for_every_site_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run(SEED + """
import json
from pathlib import Path
from app.mailing.desktop_bridge import create_service
service = create_service(Path(r'%s') / 'mailing')
out = {}
for site_type in ('', 'no_site', 'weak_site', 'unavailable', 'other'):
    s = service.script_suggestions(site_type)
    out[site_type] = s['preferred'] in {item['slug'] for item in s['items']}
print(json.dumps(out))
""" % tmp, tmp)
        self.assertEqual(result, {key: True for key in ('', 'no_site', 'weak_site', 'unavailable', 'other')})

    def test_mailing_selection_skips_hidden_and_suppressed(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run(SEED + """
import json
from pathlib import Path
from app import leads
from app.mailing.desktop_bridge import create_service
leads.hide('test', ['6'])
db.set_suppression('7', True, 'просил не писать')
service = create_service(Path(r'%s') / 'mailing')
picked = sorted(item['id'] for item in service._select({'contact': 'username'}))
print(json.dumps({'picked': picked, 'blocked': sorted(db.suppressed_ids())}))
""" % tmp, tmp)
        self.assertNotIn("6", result["picked"])   # hidden lead is not offered
        self.assertIn("2", result["picked"])
        self.assertIn("7", result["blocked"])     # suppressed lead is blocked before sending


class SchemaGuardTests(unittest.TestCase):
    def test_newer_base_is_refused_and_left_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "app.sqlite3"
            conn = sqlite3.connect(base)
            conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            conn.execute("INSERT INTO schema_migrations VALUES (99, 'x')")
            conn.commit(); conn.close()
            before = hashlib.sha256(base.read_bytes()).hexdigest()
            result = run("""
import json
from app import db
try:
    db.init_desktop_db(); print(json.dumps('opened'))
except db.SchemaTooNew as error:
    print(json.dumps(str(error)))
""", tmp)
            self.assertIn("более новой версией", result)
            self.assertEqual(hashlib.sha256(base.read_bytes()).hexdigest(), before)
            self.assertFalse((Path(tmp) / "backups").exists())

    def test_current_base_opens_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run("""
import json, sqlite3
from app import db, config
db.init_desktop_db(); db.init_desktop_db()
with sqlite3.connect(config.DB_PATH) as conn:
    print(json.dumps(conn.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0]))
""", tmp)
            self.assertEqual(result, 7)


class ImportTests(unittest.TestCase):
    def test_import_brings_details_runs_hidden_and_today(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            run(SEED + """
import json
from app import leads, outreach
with db.connection() as conn:
    conn.execute("INSERT INTO business_details(business_id,description,updated_at) VALUES ('2','Описание','t')")
    conn.execute("INSERT INTO reviews(business_id,review_key,text,inserted_at) VALUES ('2','k1','Хорошо','t')")
    conn.execute("INSERT INTO links(business_id,type,url) VALUES ('2','vk','https://vk.com/x')")
leads.hide('test', ['3'])
print(json.dumps('ok'))
""", source_dir)
            result = run("""
import json
from app import db, desktop_services
db.init_desktop_db()
summary = desktop_services.import_sqlite(r'%s')
with db.connection() as conn:
    counts = {table: conn.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
              for table in ('businesses', 'business_details', 'reviews', 'links', 'hidden_businesses',
                            'parse_runs', 'parse_run_businesses')}
print(json.dumps(counts))
""" % (Path(source_dir) / "app.sqlite3"), target_dir)
        self.assertEqual(result["businesses"], 7)
        self.assertEqual((result["business_details"], result["reviews"], result["links"]), (1, 1, 1))
        self.assertEqual(result["hidden_businesses"], 1)

    def test_import_refuses_newer_source(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            source = Path(source_dir) / "new.sqlite3"
            conn = sqlite3.connect(source)
            conn.executescript("""CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);
                    INSERT INTO schema_migrations VALUES (99,'x');
                    CREATE TABLE businesses(id TEXT); CREATE TABLE business_tags(x); CREATE TABLE templates(x);""")
            conn.close()
            result = run("""
import json
from app import db, desktop_services
db.init_desktop_db()
for name in ('import_sqlite', 'replace_database'):
    try:
        getattr(desktop_services, name)(r'%s'); print('no')
    except ValueError as error:
        last = str(error)
print(json.dumps(last))
""" % source, target_dir)
        self.assertIn("более новой версией", result)

    def test_replace_database_makes_source_current_with_backup(self):
        with tempfile.TemporaryDirectory() as source_dir, tempfile.TemporaryDirectory() as target_dir:
            run(SEED + "\nimport json\nprint(json.dumps('ok'))", source_dir)
            result = run("""
import json
from pathlib import Path
from app import db, desktop_services
db.init_desktop_db()
summary = desktop_services.replace_database(r'%s')
print(json.dumps({'businesses': summary['businesses'], 'backup': Path(summary['backup']).is_file()}))
""" % (Path(source_dir) / "app.sqlite3"), target_dir)
        self.assertEqual(result, {"businesses": 7, "backup": True})


class MarkdownTelegramTests(unittest.TestCase):
    def test_old_xlsx_markdown_link_is_read(self):
        from app.desktop_services import _plain_url
        self.assertEqual(_plain_url("[https://t.me/+79990001122](https://t.me/+79990001122)"),
                         "https://t.me/+79990001122")
        self.assertEqual(_plain_url("https://t.me/user"), "https://t.me/user")
        self.assertEqual(_plain_url(""), "")


if __name__ == "__main__":
    unittest.main()
