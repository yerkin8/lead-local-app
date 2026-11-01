import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from openpyxl import Workbook

from app import db


class DesktopMigrationTests(unittest.TestCase):
    def test_legacy_xlsx_columns_import_and_deduplicate(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "il-казань-маникюр-2026-09-27_18-06-50.xlsx"
            book = Workbook()
            sheet = book.active
            sheet.append(["name", "phone", "telegram", "site", "site_type", "address", "url"])
            sheet.append(["Салон", "+7 900 000 00 00", '["https://t.me/testsalon123"]',
                          "https://example.com", "builder", "Казань",
                          "https://yandex.ru/maps/org/salon/555/"])
            book.save(path)
            code = """
import json,sys
from app import db
from app.desktop_services import import_xlsx
db.init_desktop_db();a=import_xlsx(sys.argv[1]);db.set_business('555','waiting','Сохранить заметку')
b=import_xlsx(sys.argv[1]);item=db.business('555')
print(json.dumps({'first':a,'second':b,'item':item},ensure_ascii=False))
"""
            output = subprocess.run([sys.executable, "-c", code, str(path)],
                                    env={**os.environ, "DATA_DIR": str(Path(folder) / "data")},
                                    cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                    text=True, check=True)
            result = json.loads(output.stdout)
            self.assertEqual(result["first"]["imported"], 1)
            self.assertEqual(result["second"]["duplicates"], 1)
            self.assertEqual(result["item"]["site_type"], "weak_site")
            self.assertEqual(result["item"]["niche"], "маникюр")
            self.assertEqual(result["item"]["city"], "казань")
            self.assertEqual(result["item"]["note"], "Сохранить заметку")
            self.assertEqual(result["item"]["socials"][0]["url"], "https://t.me/testsalon123")

    def test_old_sqlite_import_keeps_crm_and_templates(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder)
            source = root / "old.sqlite3"
            data_dir = root / "new-data"
            db.init_db(source)
            job_id = db.create_job("search", {}, source)
            db.upsert_business({"id": "456", "name": "Студия", "site_type": "no_site",
                                "map_url": "https://yandex.ru/maps/org/studio/456/"},
                               ("студия", "Россия", "Казань"), job_id, source)
            db.set_business("456", "waiting", "Позвонил вчера", source)
            with db.connection(source) as conn:  # an edited script under its old flat slug (schema v5)
                conn.execute("""INSERT INTO templates(slug,title,body,updated_at,origin)
                    VALUES ('quick_no_site','Быстрое · без сайта','Мой личный текст','x','builtin')""")
            db.set_suppression("456", True, "Попросили не писать", source)
            code = """
import json,sys
from app import db
from app.desktop_services import import_sqlite, export_xlsx, import_xlsx
db.init_desktop_db()
first=import_sqlite(sys.argv[1])
second=import_sqlite(sys.argv[1])
item=db.business('456')
exported=export_xlsx(sys.argv[2])
print(json.dumps({'first':first,'second':second,'item':item,
  'template':next(x['body'] for x in db.templates() if x['slug']=='site.first'),
  'exported':exported},ensure_ascii=False))
"""
            env = {**os.environ, "DATA_DIR": str(data_dir)}
            output = subprocess.run([sys.executable, "-c", code, str(source), str(root / "export.xlsx")],
                                    env=env, cwd=Path(__file__).resolve().parents[1],
                                    capture_output=True, text=True, check=True)
            result = json.loads(output.stdout)
            self.assertEqual(result["first"]["imported"], 1)
            self.assertEqual(result["second"]["duplicates"], 1)
            self.assertEqual(result["item"]["status"], "waiting")
            self.assertEqual(result["item"]["note"], "Позвонил вчера")
            self.assertTrue(result["item"]["do_not_contact"])
            self.assertEqual(result["template"], "Мой личный текст")
            self.assertEqual(result["exported"]["count"], 1)
            self.assertEqual(len(result["item"]["history"]), 1)

    def test_campaign_requires_review_and_respects_do_not_contact(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            code = """
import json
from app import db,telegram_sender as sender
db.init_desktop_db(); job=db.create_job('search',{})
for identity in ('101','102'):
 db.upsert_business({'id':identity,'name':'Кафе '+identity,'site_type':'no_site',
  'map_url':f'https://yandex.ru/maps/org/x/{identity}/',
  'socials':[{'url':'https://t.me/testuser'+identity}]},
  ('кафе','Россия','Казань'),job)
db.set_suppression('102',True,'Отказ')
draft=sender.create_draft('Тест',['101','102'],'short')
try:
 sender.start(draft['id']); blocked='no'
except ValueError:
 blocked='yes'
edited=sender.update_recipient(draft['id'],'101','Здравствуйте, Кафе 101')
sender.configure(12345,'a'*32,'+79990000000')
print(json.dumps({'count':len(draft['recipients']),'blocked':blocked,
 'edited':edited['recipients'][0]['status'],'telegram':sender.state()}))
"""
            output = subprocess.run([sys.executable, "-c", code], env={**os.environ, "DATA_DIR": folder},
                                    cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                    text=True, check=True)
            result = json.loads(output.stdout)
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["blocked"], "yes")
            self.assertEqual(result["edited"], "draft")
            self.assertTrue(result["telegram"]["configured"])
            self.assertNotIn("api_hash", result["telegram"])

    def test_recipient_check_detects_same_telegram_account(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            code = """
import json
from app import db,telegram_sender as sender
from telethon.tl.types import User
db.init_db(); job=db.create_job('search',{})
for identity in ('201','202'):
 db.upsert_business({'id':identity,'name':'Фирма '+identity,'site_type':'no_site',
  'map_url':f'https://yandex.ru/maps/org/x/{identity}/',
  'socials':[{'url':'https://t.me/testuser'+identity}]},
  ('фирма','Россия','Казань'),job)
draft=sender.create_draft('Тест',['201','202'],'short')
for recipient in draft['recipients']:
 sender.update_recipient(draft['id'],recipient['business_id'],'Привет')
 sender.set_contact_permission(draft['id'],recipient['business_id'],'allowed','explicit_consent','Тестовое согласие')
class Client:
 async def is_user_authorized(self): return True
 async def get_entity(self,peer): return User(id=333)
 async def disconnect(self): pass
async def fake_client(): return Client(),{}
sender._client=fake_client
def drive(coro):
 while True:
  try: coro.send(None)
  except StopIteration as finished: return finished.value
sender.asyncio.run=drive
checked=sender.check_draft(draft['id'])
print(json.dumps([(x['status'],x['telegram_id']) for x in checked['recipients']]))
"""
            output = subprocess.run([sys.executable, "-c", code], env={**os.environ, "DATA_DIR": folder},
                                    cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                    text=True, check=True)
            self.assertEqual(json.loads(output.stdout), [["ready", "333"], ["invalid", "333"]])

    def test_phone_lookup_removes_temporary_contact(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            code = """
import json
from types import SimpleNamespace
from telethon.tl.types import User
from telethon.tl.functions.contacts import GetContactsRequest,ImportContactsRequest,DeleteContactsRequest
from app import db,telegram_sender as sender
db.init_db();job=db.create_job('search',{})
db.upsert_business({'id':'301','name':'Фирма','site_type':'no_site',
 'map_url':'https://yandex.ru/maps/org/x/301/','phones':['+7 999 000 00 00']},
 ('фирма','Россия','Казань'),job)
calls=[]
class Client:
 async def is_user_authorized(self): return True
 async def __call__(self,request):
  calls.append(type(request).__name__)
  if isinstance(request,GetContactsRequest): return SimpleNamespace(users=[])
  if isinstance(request,ImportContactsRequest):
   return SimpleNamespace(users=[User(id=777,access_hash=888,username='testuser301')],
    imported=[SimpleNamespace(user_id=777)])
  return None
 async def disconnect(self): pass
async def fake_client(): return Client(),{}
def drive(coro):
 while True:
  try: coro.send(None)
  except StopIteration as finished: return finished.value
sender._client=fake_client;sender.asyncio.run=drive
result=sender.check_phone('301','+7 999 000 00 00')
print(json.dumps({'result':result,'calls':calls,'linked':db.business('301')['telegram']}))
"""
            output = subprocess.run([sys.executable, "-c", code], env={**os.environ, "DATA_DIR": folder},
                                    cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                    text=True, check=True)
            result = json.loads(output.stdout)
            self.assertTrue(result["result"]["found"])
            self.assertEqual(result["linked"]["telegram_id"], "777")
            self.assertIn("DeleteContactsRequest", result["calls"])

    def test_interrupted_send_is_not_replayed(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            code = """
import json
from app import db,telegram_sender as sender
from app.desktop_services import LocalWorker
db.init_desktop_db();j=db.create_job('search',{})
db.upsert_business({'id':'401','name':'Кафе','site_type':'no_site',
 'map_url':'https://yandex.ru/maps/org/x/401/',
 'socials':[{'url':'https://t.me/testuser401'}]},('кафе','Россия','Казань'),j)
draft=sender.create_draft('Тест',['401'],'short')
with db.connection() as conn:
 conn.execute("UPDATE campaigns SET status='running' WHERE id=?",(draft['id'],))
 conn.execute("UPDATE campaign_recipients SET status='sending' WHERE campaign_id=?",(draft['id'],))
worker=LocalWorker();worker.start();worker.stop.set()
saved=sender.campaign(draft['id'])
print(json.dumps({'campaign':saved['status'],'recipient':saved['recipients'][0]['status']}))
"""
            output = subprocess.run([sys.executable, "-c", code], env={**os.environ, "DATA_DIR": folder},
                                    cwd=Path(__file__).resolve().parents[1], capture_output=True,
                                    text=True, check=True)
            self.assertEqual(json.loads(output.stdout), {"campaign": "interrupted", "recipient": "uncertain"})


if __name__ == "__main__":
    unittest.main()
