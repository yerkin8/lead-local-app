import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db, media_service


class LocalDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.tmp.name) / "local.sqlite3"
        db.init_db(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_media_reuses_running_and_completed_jobs_but_repeat_is_explicit(self):
        params = {"url": "https://yandex.ru/maps/org/x/123/", "sections": {"Внутри": 5}}
        first, created = db.create_media_job("images", params, "123", None, path=self.path)
        self.assertTrue(created)
        again, created = db.create_media_job("images", params, "123", None, path=self.path)
        self.assertFalse(created)
        self.assertEqual(again["id"], first["id"])
        db.update_job(first["id"], status="done", result={"archives": ["archive-01.zip"]}, path=self.path)
        ready, created = db.create_media_job("images", params, "123", None, path=self.path)
        self.assertFalse(created)
        self.assertEqual(ready["id"], first["id"])
        repeated, created = db.create_media_job("images", params, "123", None, repeat=True, path=self.path)
        self.assertTrue(created)
        self.assertNotEqual(repeated["id"], first["id"])
        products, created = db.create_media_job("products", {"url": params["url"]}, "123", None, path=self.path)
        self.assertTrue(created)
        self.assertNotEqual(products["id"], repeated["id"])

    def test_media_from_card_and_manual_url(self):
        # Use a local DB for every operation of the media service.
        data = {"id": "123", "name": "Кафе", "site_type": "no_site", "map_url": "https://yandex.ru/maps/org/cafe/123/"}
        db.upsert_business(data, ("кафе", "Россия", "Казань"), db.create_job("search", {}, self.path), self.path)
        original_business, original_create = db.business, db.create_media_job
        with patch.object(media_service.db, "business", side_effect=lambda identity: original_business(identity, self.path)), \
             patch.object(media_service.db, "create_media_job", side_effect=lambda kind, params, identity, business_id, repeat=False: original_create(kind, params, identity, business_id, repeat=repeat, path=self.path)):
            card = media_service.start("images", business_id="123", sections={"Внутри": 10})
            self.assertEqual(card["business_id"], "123")
            self.assertTrue(card["created"])
            manual = media_service.start("products", url="https://yandex.ru/maps/org/cafe/123/")
            self.assertEqual(manual["business_id"], "123")
            unknown = media_service.start("products", url="https://yandex.ru/maps/org/new/999/")
            self.assertIsNone(unknown["business_id"])
            with self.assertRaises(Exception):
                media_service.start("images", url="https://evil.example/maps/org/x/10/", sections={"Внутри": 10})

    def test_archive_is_limited_to_finished_job_and_can_be_copied(self):
        item, _ = db.create_media_job("products", {"url": "https://yandex.ru/maps/org/x/999/"},
                                      "999", None, path=self.path)
        folder = Path(self.tmp.name) / item["id"]
        folder.mkdir()
        (folder / "archive-01.zip").write_bytes(b"test archive")
        get_job = db.job
        with patch.object(media_service.db, "job", side_effect=lambda identity: get_job(identity, self.path)), \
             patch.object(media_service, "job_folder", return_value=folder):
            with self.assertRaises(Exception):
                media_service.archive_path(item["id"], "archive-01.zip")
            db.update_job(item["id"], status="done", result={"archives": ["archive-01.zip"]}, path=self.path)
            self.assertEqual(media_service.archive_path(item["id"], "archive-01.zip").read_bytes(), b"test archive")
            with self.assertRaises(Exception):
                media_service.archive_path(item["id"], "../archive-01.zip")
            target = Path(self.tmp.name) / "saved.zip"
            self.assertEqual(media_service.copy_archive(item["id"], "archive-01.zip", str(target)), str(target))
            self.assertEqual(target.read_bytes(), b"test archive")

    def test_search_key_ignores_count_and_case(self):
        a = {"country": "РОССИЯ", "city": "Казань", "niche": "Кафе", "types": ["weak_site", "no_site"], "count": 20}
        b = {"country": "Россия", "city": "казань", "niche": "кафе", "types": ["no_site", "weak_site"], "count": 7}
        self.assertEqual(db.search_key(a), db.search_key(b))
        db.mark_checked(db.search_key(a), "111", self.path)
        self.assertEqual(db.checked_ids(db.search_key(b), self.path), {"111"})


class RepeatSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.tmp.name) / "search.sqlite3"
        db.init_db(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_runs_get_twenty_disjoint_without_reopening_old_cards(self):
        from app import lead_parser

        params = {"country": "Россия", "city": "Казань", "niche": "кафе", "count": 20, "types": ["no_site"]}
        listing = "".join(f"<div class='search-business-snippet-view__content'><a href='/maps/org/firm/{i}/'>X</a></div>" for i in range(1, 42))
        opened = []
        old_job = db.create_job("search", {"niche": "другая"}, self.path)
        db.upsert_business({"id": "1", "name": "Старая фирма", "site_type": "no_site",
                            "address": "Казань", "map_url": "https://yandex.com/maps/org/firm/1/"},
                           ("другая", "Россия", "Казань"), old_job, self.path)
        db.set_business("1", "waiting", "Важная заметка", self.path)

        def page(_session, url):
            if "/maps/org/" in url:
                opened.append(url)
                return url, "card"
            return url, listing

        def parse(url, _html, *_args, **_kwargs):
            identity = url.rstrip("/").split("/")[-1]
            return {"id": identity, "name": "Фирма " + identity, "site_type": "no_site",
                    "address": "Казань", "map_url": url}

        known_ids, checked_ids, mark_checked = db.known_ids, db.checked_ids, db.mark_checked
        upsert_business, update_job = db.upsert_business, db.update_job
        with patch.object(lead_parser, "request_page", side_effect=page), \
             patch.object(lead_parser, "parse_org_html", side_effect=parse), \
             patch.object(lead_parser.db, "known_ids", side_effect=lambda: known_ids(self.path)), \
             patch.object(lead_parser.db, "checked_ids", side_effect=lambda key: checked_ids(key, self.path)), \
             patch.object(lead_parser.db, "mark_checked", side_effect=lambda key, identity: mark_checked(key, identity, self.path)), \
             patch.object(lead_parser.db, "upsert_business", side_effect=lambda data, tag, job_id, **kw: upsert_business(data, tag, job_id, self.path, **kw)), \
             patch.object(lead_parser.db, "update_job", side_effect=lambda job_id, **kwargs: update_job(job_id, path=self.path, **kwargs)), \
             patch.object(lead_parser.time, "sleep"):
            first_id = db.create_job("search", params, self.path)
            first = lead_parser.run_search_http({"id": first_id, "params": params})
            first_urls = list(opened)
            second_id = db.create_job("search", params, self.path)
            second = lead_parser.run_search_http({"id": second_id, "params": params})
        self.assertEqual(first["matched"], 20)
        self.assertEqual(second["matched"], 20)
        self.assertEqual(second["skipped_existing"], 21)
        self.assertTrue(set(first_urls).isdisjoint(opened[len(first_urls):]))
        with db.connection(self.path) as conn:
            a = {r[0] for r in conn.execute("SELECT business_id FROM job_results WHERE job_id=?", (first_id,))}
            b = {r[0] for r in conn.execute("SELECT business_id FROM job_results WHERE job_id=?", (second_id,))}
        self.assertEqual(len(a), 20)
        self.assertEqual(len(b), 20)
        self.assertTrue(a.isdisjoint(b))
        old = db.business("1", self.path)
        self.assertEqual(old["note"], "Важная заметка")
        self.assertEqual(old["status"], "waiting")

    def test_shortage_and_checked_rejections(self):
        from app import lead_parser

        params = {"country": "Россия", "city": "Казань", "niche": "кофейня", "count": 20,
                  "types": ["no_site"]}
        old_job = db.create_job("search", {}, self.path)
        for identity in range(1, 4):
            db.upsert_business({"id": str(identity), "name": "Старая", "site_type": "no_site",
                                "map_url": f"https://yandex.com/maps/org/x/{identity}/"},
                               ("другая", "Россия", "Казань"), old_job, self.path)
        listing = "".join(f"<div class='search-business-snippet-view__content'><a href='/maps/org/x/{i}/'>X</a></div>" for i in range(1, 12))
        opened = []

        def page(_session, url):
            if "/maps/org/" in url:
                opened.append(url)
                return url, "card"
            return url, listing

        def parse(url, _html, *_args, **_kwargs):
            identity = url.rstrip("/").split("/")[-1]
            return {"id": identity, "name": "Фирма " + identity,
                    "site_type": "weak_site" if identity == "11" else "no_site",
                    "address": "Казань", "map_url": url}

        known_ids, checked_ids, mark_checked = db.known_ids, db.checked_ids, db.mark_checked
        upsert_business, update_job = db.upsert_business, db.update_job
        with patch.object(lead_parser, "request_page", side_effect=page), \
             patch.object(lead_parser, "parse_org_html", side_effect=parse), \
             patch.object(lead_parser.db, "known_ids", side_effect=lambda: known_ids(self.path)), \
             patch.object(lead_parser.db, "checked_ids", side_effect=lambda key: checked_ids(key, self.path)), \
             patch.object(lead_parser.db, "mark_checked", side_effect=lambda key, identity: mark_checked(key, identity, self.path)), \
             patch.object(lead_parser.db, "upsert_business", side_effect=lambda data, tag, job_id, **kw: upsert_business(data, tag, job_id, self.path, **kw)), \
             patch.object(lead_parser.db, "update_job", side_effect=lambda job_id, **kwargs: update_job(job_id, path=self.path, **kwargs)), \
             patch.object(lead_parser.time, "sleep"):
            first_id = db.create_job("search", params, self.path)
            first = lead_parser.run_search_http({"id": first_id, "params": params})
            first_opened = len(opened)
            second_id = db.create_job("search", params, self.path)
            second = lead_parser.run_search_http({"id": second_id, "params": params})
        self.assertEqual(first["matched"], 7)
        self.assertIn("больше не показал", first["reason"])
        self.assertEqual(first_opened, 8)
        self.assertEqual(second["matched"], 0)
        self.assertEqual(second["skipped_existing"], 10)
        self.assertEqual(second["skipped_checked"], 1)
        self.assertEqual(len(opened), first_opened)


if __name__ == "__main__":
    unittest.main()
