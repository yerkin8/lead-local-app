import tempfile
import unittest
import zipfile
from pathlib import Path

from app import bot, db
from app.bot import menu_markup
from app.domain import classify_site, is_social, org_id, render_template
from app.script_templates import TEMPLATES, pick_slug
from app.media import MediaError, split_archives, validate_org_url
import app.media as media
from unittest.mock import patch


class ClassificationTests(unittest.TestCase):
    def test_site_types(self):
        self.assertEqual(classify_site(None)[0], "no_site")
        self.assertEqual(classify_site("https://vk.com/cafe")[0], "no_site")
        self.assertEqual(classify_site("https://tilda.ws.fake.example", "<html>" + "x" * 1200)[0], "other")
        self.assertEqual(classify_site("https://mysalon.ru", "<html>tildacdn" + "x" * 1200)[0], "weak_site")
        self.assertEqual(classify_site("https://mysalon.ru", failed=True)[0], "unavailable")
        self.assertEqual(classify_site("https://mysalon.ru", "<html>" + "x" * 1200)[0], "other")

    def test_social_and_identity(self):
        self.assertTrue(is_social("https://m.vk.com/foo"))
        self.assertFalse(is_social("https://vk.com.evil.example/foo"))
        self.assertEqual(org_id("https://yandex.ru/maps/org/cafe/12345/"), "12345")
        self.assertEqual(org_id("https://yandex.com/maps/org/cafe/12345?si=example"), "12345")
        self.assertEqual(org_id("https://yandex.ru/maps/org/cafe/12345/menu/?ll=1"), "12345")

    def test_template(self):
        self.assertEqual(render_template("{название}: {тип_сайта}", {"name": "Кафе", "site_type": "weak_site"}),
                         "Кафе: сайт на конструкторе")
        for site_type in ("weak_site", "unavailable"):
            self.assertEqual(pick_slug({"site_type": site_type}, "site", "first", TEMPLATES), "site.first.redesign")
        self.assertEqual(pick_slug({"site_type": "no_site"}, "site", "first", TEMPLATES), "site.first")

    def test_bot_keyboard_is_persistent(self):
        markup = menu_markup()
        self.assertTrue(markup["is_persistent"])
        labels = [button["text"] for row in markup["keyboard"] for button in row]
        self.assertIn("📷 Изображения", labels)
        self.assertIn("🛍 Товары", labels)


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.path = Path(self.tmp.name) / "test.sqlite3"
        db.init_db(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def sample(self, name="Кафе"):
        return {"id": "123", "name": name, "phones": ["+7 900 000-00-00"], "socials": [],
                "website": None, "site_type": "no_site", "rating": 4.8, "reviews": 42,
                "address": "Казань", "map_url": "https://yandex.ru/maps/org/cafe/123/"}

    def test_script_migration_preserves_user_edits(self):
        with db.connection(self.path) as conn:  # a pre-v5 default and an edit under old slugs
            conn.execute("DELETE FROM templates")
            conn.execute("INSERT INTO templates(slug,title,body,updated_at) VALUES ('call','Звонок',?,'x')",
                         (db.LEGACY_TEMPLATES["call"][1],))
            conn.execute("INSERT INTO templates(slug,title,body,updated_at) VALUES ('expensive','Дорого',?,'x')",
                         ("Мой собственный текст",))
        db.init_db(self.path)
        scripts = {item["slug"]: item for item in db.templates(self.path)}
        self.assertEqual(scripts["site.call"]["body"], TEMPLATES["site.call"][1])
        self.assertEqual(scripts["site.objection.expensive"]["body"], "Мой собственный текст")
        self.assertNotIn("call", scripts)
        self.assertEqual(set(TEMPLATES) - set(scripts), set())

    def test_repeat_scan_preserves_crm(self):
        job_id = db.create_job("search", {"niche": "кафе"}, self.path)
        tag = ("кафе", "Россия", "Казань")
        self.assertTrue(db.upsert_business(self.sample(), tag, job_id, self.path))
        self.assertTrue(db.set_business("123", "waiting", "Написал в VK", self.path))
        self.assertFalse(db.upsert_business(self.sample("Новое имя"), tag, job_id, self.path))
        item = db.business("123", self.path)
        self.assertEqual(item["name"], "Новое имя")
        self.assertEqual(item["status"], "waiting")
        self.assertEqual(item["note"], "Написал в VK")
        self.assertEqual(len(item["history"]), 1)
        self.assertEqual(db.stats(self.path)["contacted"], 1)

    def test_single_worker_claim(self):
        job_id = db.create_job("images", {"url": "https://yandex.ru/maps/org/a/1/"}, self.path)
        self.assertEqual(db.claim_job(self.path)["id"], job_id)
        self.assertIsNone(db.claim_job(self.path))

    def test_bot_flow_rotation_tracks_only_previous_chat_messages(self):
        self.assertEqual(db.rotate_bot_flow(123, "products", "flow-a", self.path), [])
        self.assertTrue(db.record_bot_flow_message(123, "flow-a", 10, self.path))
        self.assertTrue(db.record_bot_flow_message(123, "flow-a", 11, self.path))
        self.assertFalse(db.record_bot_flow_message(123, "wrong-flow", 12, self.path))
        job_id = db.create_job("products", {"url": "https://yandex.com/maps/org/x/1"}, self.path)
        self.assertEqual(db.rotate_bot_flow(123, "images", "flow-b", self.path), [10, 11])
        self.assertFalse(db.active_bot_flow(123, "flow-a", self.path))
        self.assertTrue(db.active_bot_flow(123, "flow-b", self.path))
        self.assertIsNotNone(db.job(job_id, self.path))

    def test_product_link_without_trailing_slash_reaches_card(self):
        url = "https://yandex.com/maps/org/donats_lab/155046956951?si=example"
        with patch.object(media, "ensure_space"), \
             patch.object(media, "request_page", return_value=("", "")) as request, \
             patch.object(media, "products_from_state", side_effect=MediaError("HTML карточки не содержит товаров")):
            with self.assertRaises(MediaError):
                media.run_products_http({"params": {"url": url}})
        self.assertEqual([item.args[1] for item in request.call_args_list], [
            "https://yandex.ru/maps/org/donats_lab/155046956951/prices/",
            "https://yandex.ru/maps/org/donats_lab/155046956951/menu/",
        ])

    def test_direct_menu_link_uses_menu_first(self):
        url = "https://yandex.ru/maps/org/donats_lab/155046956951/menu/?ll=36.267221"
        self.assertEqual(validate_org_url(url), url)
        with patch.object(media, "ensure_space"), \
             patch.object(media, "request_page", return_value=("", "")) as request, \
             patch.object(media, "products_from_state", side_effect=MediaError("HTML карточки не содержит товаров")):
            with self.assertRaises(MediaError):
                media.run_products_http({"params": {"url": url}})
        self.assertEqual(request.call_args_list[0].args[1],
                         "https://yandex.ru/maps/org/donats_lab/155046956951/menu/")

    def test_products_retry_transient_missing_html_state(self):
        expected = {"business_name": "Донатс Лаб", "products": 40}
        with patch.object(media, "run_products_http", side_effect=[
                MediaError("HTML карточки не содержит товаров"), expected]) as http, \
             patch.object(media, "run_products_browser") as browser, \
             patch.object(media.time, "sleep"):
            self.assertEqual(media.run_products({"params": {"url": "example"}}), expected)
        self.assertEqual(http.call_count, 2)
        browser.assert_not_called()

    # Server bot: Desktop keeps its own app/bot.py (no admins module), so this is the Desktop version.
    def test_switching_bot_buttons_deletes_only_previous_flow_messages(self):
        original_rotate = db.rotate_bot_flow
        original_record = db.record_bot_flow_message
        calls = []
        next_message_id = iter(range(1000, 1010))

        def fake_call(method, payload, **_kwargs):
            calls.append((method, payload))
            return {"message_id": next(next_message_id)} if method == "sendMessage" else True

        def incoming(text, message_id):
            return {"from": {"id": 123}, "chat": {"id": 123, "type": "private"},
                    "text": text, "message_id": message_id}

        bot._sessions.clear()
        with patch.object(bot, "OWNER_TELEGRAM_ID", 123), \
             patch.object(bot, "call", side_effect=fake_call), \
             patch.object(bot.db, "rotate_bot_flow", side_effect=lambda c, k, f: original_rotate(c, k, f, self.path)), \
             patch.object(bot.db, "record_bot_flow_message", side_effect=lambda c, f, m: original_record(c, f, m, self.path)):
            bot.handle_message(incoming("🛍 Товары", 100))
            bot.handle_message(incoming("не ссылка", 101))
            bot.handle_message(incoming("📷 Изображения", 102))
        bot._sessions.clear()
        deleted = [payload["message_ids"] for method, payload in calls if method == "deleteMessages"]
        self.assertEqual(deleted, [[100, 101, 1000, 1001]])

    def test_media_deletion_cannot_race_worker(self):
        job_id = db.create_job("images", {"url": "https://yandex.ru/maps/org/a/1/"}, self.path)
        self.assertTrue(db.prepare_media_delete(job_id, self.path))
        self.assertIsNone(db.claim_job(self.path))
        self.assertTrue(db.delete_job(job_id, self.path))
        self.assertIsNone(db.job(job_id, self.path))

    def test_all_crm_statuses_have_history(self):
        job_id = db.create_job("search", {}, self.path)
        db.upsert_business(self.sample(), ("кафе", "Россия", "Казань"), job_id, self.path)
        for status in ("call", "waiting", "replied", "working", "completed", "unsuitable", "not_started"):
            db.set_business("123", status, None, self.path)
        self.assertEqual(len(db.business("123", self.path)["history"]), 7)
        self.assertEqual(db.stats(self.path)["statuses"]["call"], 0)
        self.assertEqual(db.stats(self.path)["statuses"]["not_started"], 1)
        self.assertEqual(db.stats(self.path)["contacted"], 1)


class ArchiveTests(unittest.TestCase):
    def test_url_validation(self):
        self.assertEqual(validate_org_url("https://yandex.ru/maps/org/a/123/"),
                         "https://yandex.ru/maps/org/a/123/")
        self.assertEqual(validate_org_url(
            "https://yandex.ru/maps/213/moscow/?ll=37.576661%2C55.736989&mode=poi&"
            "poi%5Buri%5D=ymapsbm1%3A%2F%2Forg%3Foid%3D78252163025&tab=gallery&z=18.29"),
            "https://yandex.ru/maps/org/organization/78252163025/")
        self.assertEqual(validate_org_url(
            "https://yandex.ru/maps/213/moscow/?mode=poi&"
            "poi%5Buri%5D=ymapsbm1%3A%2F%2Forg%3Foid%3D21117108341"),
            "https://yandex.ru/maps/org/organization/21117108341/")
        for value in ("http://yandex.ru/maps/org/a/123/", "https://evil.com/maps/org/a/123/", "https://yandex.ru/other"):
            with self.assertRaises(Exception):
                validate_org_url(value)
        with self.assertRaises(Exception):
            validate_org_url("https://yandex.ru/maps/213/moscow/?mode=poi&poi%5Buri%5D=https%3A%2F%2Fevil.com%2F%3Foid%3D123")

    def test_zip_has_sections_and_product_paths(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            folder = Path(tmp)
            section = folder / "work" / "Еда"
            section.mkdir(parents=True)
            photo = section / "one.webp"
            photo.write_bytes(b"RIFFtestWEBP")
            files = split_archives(folder, [photo])
            with zipfile.ZipFile(files[0]) as archive:
                self.assertEqual(archive.namelist(), ["Еда/one.webp"])
            images = folder / "work" / "images"
            images.mkdir()
            image = images / "one.webp"
            image.write_bytes(b"RIFFtestWEBP")
            menu = folder / "work" / "menu.md"
            menu.write_text("![Фото](images/one.webp)", encoding="utf-8")
            files = split_archives(folder, [image, menu], include_menu=True)
            with zipfile.ZipFile(files[0]) as archive:
                self.assertIn("menu.md", archive.namelist())
                self.assertIn("images/one.webp", archive.namelist())

    def test_large_payload_is_split_into_valid_zips(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            folder = Path(tmp)
            work = folder / "work"
            work.mkdir()
            files = []
            for name in ("a.jpg", "b.jpg"):
                path = work / name
                path.write_bytes(b"x" * 50)
                files.append(path)
            old_limit = media.MAX_ZIP_BYTES
            try:
                media.MAX_ZIP_BYTES = 1024 * 1024 + 80
                archives = split_archives(folder, files)
            finally:
                media.MAX_ZIP_BYTES = old_limit
            self.assertEqual(len(archives), 2)
            for archive in archives:
                with zipfile.ZipFile(archive) as z:
                    self.assertEqual(len(z.namelist()), 1)

    def test_media_deletion_removes_all_job_files(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as tmp:
            old_dir = media.MEDIA_DIR
            try:
                media.MEDIA_DIR = Path(tmp)
                job_id = "a" * 32
                folder = media.job_folder(job_id)
                folder.mkdir()
                (folder / "archive-01.zip").write_bytes(b"archive")
                media.delete_media_files(job_id)
                self.assertFalse(folder.exists())
            finally:
                media.MEDIA_DIR = old_dir


if __name__ == "__main__":
    unittest.main()
