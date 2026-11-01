import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app import db, media, media_service, telegram_sender


class MediaDirectoryTests(unittest.TestCase):
    def test_gallery_uses_russian_maps_host_for_saved_card(self):
        source = "https://yandex.com/maps/org/deteyl_23/220067206323/"
        result = media.gallery_url(source)
        self.assertTrue(result.startswith("https://yandex.ru/maps/org/deteyl_23/220067206323/"))

    def test_new_media_is_plain_named_folder_without_zip(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
            base = Path(root) / "media"
            work = base / "temporary" / "work"
            work.mkdir(parents=True)
            (work / "Внутри").mkdir()
            (work / "Внутри" / "photo.jpg").write_bytes(b"photo")
            business = {"name": "Детей 23", "city": "Воронеж"}
            job = {"kind": "images", "params": {"url": "https://yandex.ru/maps/org/deteyl_23/220067206323/"}}
            with patch.object(media, "MEDIA_DIR", base), patch.object(db, "business", return_value=business):
                folder, count, size = media._result_directory(job, "Детей 23", work)
            self.assertIn("Детей_23 - Воронеж", str(folder))
            self.assertEqual(folder.parent.name, "Галерея")
            self.assertEqual((folder / "Внутри" / "photo.jpg").read_bytes(), b"photo")
            self.assertEqual((count, size), (1, 5))
            self.assertFalse(list(folder.rglob("*.zip")))

    def test_gallery_job_from_database_uses_visible_chrome_and_russian_host(self):
        import yandex_images_parser as parser

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
            base = Path(root) / "media"
            driver = Mock()
            seen_urls = []
            job = {"id": "a" * 32, "kind": "images", "params": {
                "url": "https://yandex.com/maps/org/deteyl_23/220067206323/",
                "sections": {"Внутри": 1}}}

            def process(_driver, _session, target, _section, _seen):
                (target / "photo.jpg").write_bytes(b"image")
                return SimpleNamespace(saved=1, missing=0)

            def open_gallery(_driver, url):
                seen_urls.append(url)
                return "Детей 23"

            with patch.object(media, "MEDIA_DIR", base), \
                 patch.object(media, "ensure_space"), \
                 patch.object(media, "job_folder", return_value=base / job["id"]), \
                 patch.object(media, "guard_download"), \
                 patch.object(db, "business", return_value={"name": "Детей 23", "city": "Воронеж"}), \
                 patch.object(parser, "HEADLESS", parser.HEADLESS), \
                 patch.object(parser, "MANUAL_CAPTCHA_WAIT", parser.MANUAL_CAPTCHA_WAIT), \
                 patch.object(parser, "make_driver", return_value=driver), \
                 patch.object(parser, "open_gallery", side_effect=open_gallery), \
                 patch.object(parser, "build_session", return_value=Mock()), \
                 patch.object(parser, "wait_for_section_buttons", return_value={"внутри": "Внутри"}), \
                 patch.object(parser, "select_section", return_value=True), \
                 patch.object(parser, "process_section", side_effect=process):
                result = media.run_images(job)
                self.assertFalse(parser.HEADLESS)
            self.assertTrue(seen_urls[0].startswith("https://yandex.ru/maps/org/"))
            self.assertEqual(result["files"], 1)
            self.assertTrue((base / result["folder"] / "Внутри" / "photo.jpg").is_file())
            self.assertNotIn("archives", result)

    def test_new_folder_can_be_opened_and_copied_but_not_escape_media_root(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
            base = Path(root) / "media"
            folder = base / "Кафе - Казань" / "Товары" / "Запуск 1"
            folder.mkdir(parents=True)
            (folder / "menu.md").write_text("menu", encoding="utf-8")
            item = {"kind": "products", "status": "done", "result": {"folder": str(folder.relative_to(base))}}
            with patch.object(media_service, "MEDIA_DIR", base), patch.object(db, "job", return_value=item):
                self.assertEqual(media_service.result_folder("job"), folder)
                copied = Path(media_service.copy_archive("job", "", str(Path(root) / "copies")))
                self.assertEqual((copied / "menu.md").read_text(encoding="utf-8"), "menu")
                item["result"]["folder"] = "../outside"
                with self.assertRaises(media.MediaError):
                    media_service.result_folder("job")


class ManualTelegramTests(unittest.TestCase):
    def test_confirmed_manual_send_updates_campaign_and_crm(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as root:
            path = Path(root) / "app.sqlite3"
            db.init_db(path)
            search_id = db.create_job("search", {}, path)
            db.upsert_business({"id": "123", "name": "Кафе", "site_type": "no_site",
                                "map_url": "https://yandex.ru/maps/org/cafe/123/"},
                               ("Кафе", "Россия", "Казань"), search_id, path)
            with db.connection(path) as conn:
                conn.execute("INSERT INTO campaigns (id,title,status,created_at,updated_at) VALUES (?,?,?,?,?)",
                             ("campaign", "Тест", "draft", db.now(), db.now()))
                conn.execute("INSERT INTO campaign_recipients (campaign_id,business_id,peer,message) VALUES (?,?,?,?)",
                             ("campaign", "123", "testcafe",
                              "Здравствуйте!\n\n" + telegram_sender.OPT_OUT_FOOTER))
            original_connection = db.connection
            original_suppressed = db.suppressed_ids
            original_set = db.set_business
            with patch.object(db, "connection", side_effect=lambda *args, **kwargs: original_connection(path)), \
                 patch.object(db, "suppressed_ids", side_effect=lambda: original_suppressed(path)), \
                 patch.object(db, "set_business", side_effect=lambda identity, status, note: original_set(identity, status, note, path)):
                telegram_sender.set_contact_permission("campaign", "123", "allowed",
                                                        "explicit_consent", "Согласие в тесте")
                telegram_sender.manual_prepare(
                    "campaign", "123",
                    "Здравствуйте, тестовое сообщение\n\n" + telegram_sender.OPT_OUT_FOOTER)
                result = telegram_sender.mark_manual_sent("campaign", "123")
                self.assertEqual(result["recipients"][0]["status"], "sent")
            self.assertEqual(db.business("123", path)["status"], "waiting")
