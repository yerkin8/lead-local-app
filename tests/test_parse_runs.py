import hashlib
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook

from app import db
from app.desktop_services import export_xlsx, validate_search


def search_params(niche="Салоны красоты"):
    return {"country": "Россия", "niche": niche, "count": 20,
            "types": ["no_site"], "review_limit": 10}


def business_data(identity, name=None, address="Казань, тестовая улица"):
    return {"id": identity, "name": name or f"Организация {identity}",
            "site_type": "no_site", "map_url": f"https://yandex.ru/maps/org/test/{identity}/",
            "address": address, "phones": [], "socials": []}


class ParseRunTests(unittest.TestCase):
    def test_desktop_migration_on_copy_preserves_legacy_rows_and_source(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder)
            original = root / "legacy.sqlite3"
            copied = root / "copied.sqlite3"
            db.init_db(original)
            job = db.create_job("search", {}, original)
            for identity in ("1001", "1002"):
                db.upsert_business(business_data(identity), ("старый поиск", "Россия", "Казань"), job, original)
            source_hash = hashlib.sha256(original.read_bytes()).hexdigest()
            with closing(sqlite3.connect(original)) as source, closing(sqlite3.connect(copied)) as target:
                source.backup(target)
                target.commit()

            db.init_desktop_db(copied)
            self.assertEqual(hashlib.sha256(original.read_bytes()).hexdigest(), source_hash)
            with closing(sqlite3.connect(copied)) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0], 2)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM business_tags").fetchone()[0], 2)
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertIn("parse_runs", tables)
                self.assertIn("parse_run_businesses", tables)
            backups = list((root / "backups").glob(
                f"copied-pre-schema-v{db.DESKTOP_SCHEMA_VERSION}-*.sqlite3"))
            self.assertEqual(len(backups), 1)
            with closing(sqlite3.connect(backups[0])) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0], 2)

    def test_base_initializer_does_not_add_parse_run_schema(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "base.sqlite3"
            db.init_db(path)
            with closing(sqlite3.connect(path)) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn("parse_runs", tables)
            self.assertNotIn("parse_run_businesses", tables)

    def test_city_input_normalizes_spaces_case_and_deduplicates(self):
        validated = validate_search({"country": "Россия", "city": " Казань ; КАЗАНЬ  ; Самара\nСамара",
            "niche": "  Салоны   красоты ", "count": 5, "types": ["no_site"]})
        self.assertEqual(validated["cities"], ["Казань", "Самара"])
        self.assertEqual(validated["city"], "Казань")
        self.assertEqual(validated["niche"], "Салоны красоты")

    def test_two_cities_and_repeat_first_create_three_runs_without_duplicate_business(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "runs.sqlite3"
            db.init_desktop_db(path)
            first = db.create_desktop_parse_jobs(search_params(), ["Казань"], path)[0]
            second = db.create_desktop_parse_jobs(search_params(" салоны   красоты "), ["Самара"], path)[0]
            repeated = db.create_desktop_parse_jobs(search_params("САЛОНЫ КРАСОТЫ"), ["казань"], path)[0]

            self.assertTrue(db.upsert_business(business_data("2001"), ("Салоны красоты", "Россия", "Казань"),
                first["id"], path, parse_run_id=first["run_id"]))
            self.assertTrue(db.upsert_business(business_data("2002", address="Самара, тестовая улица"),
                ("салоны красоты", "Россия", "Самара"), second["id"], path, parse_run_id=second["run_id"]))
            self.assertFalse(db.upsert_business(business_data("2001"), ("САЛОНЫ КРАСОТЫ", "Россия", "Казань"),
                repeated["id"], path, parse_run_id=repeated["run_id"]))

            niches = db.parse_niches(path)
            self.assertEqual(len(niches), 1)
            self.assertEqual(niches[0]["city_count"], 2)
            cities = db.parse_cities(niches[0]["key"], path)
            self.assertEqual({item["key"] for item in cities}, {"казань", "самара"})
            kazan_runs = db.parse_runs_for_city(niches[0]["key"], "казань", path)
            self.assertEqual(len(kazan_runs), 2)
            self.assertEqual([item["organization_count"] for item in kazan_runs], [1, 1])
            with db.connection(path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0], 2)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM parse_runs").fetchone()[0], 3)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM parse_run_businesses WHERE business_id='2001'").fetchone()[0], 2)
            self.assertEqual(db.business_ids_for_scope({"level": "niche", "niche_key": niches[0]["key"]}, path),
                             {"2001", "2002"})
            self.assertEqual(db.business_ids_for_scope({"level": "city", "niche_key": niches[0]["key"],
                                                        "city_key": "казань"}, path), {"2001"})

    def test_run_status_and_count_follow_worker_lifecycle(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "lifecycle.sqlite3"
            db.init_desktop_db(path)
            run = db.create_desktop_parse_jobs(search_params(), ["Казань"], path)[0]
            db.upsert_business(business_data("2100"), ("Салоны красоты", "Россия", "Казань"),
                run["id"], path, parse_run_id=run["run_id"])
            db.start_parse_run(run["run_id"], path)
            with db.connection(path) as conn:
                started = conn.execute("SELECT status,started_at,ended_at FROM parse_runs WHERE id=?",
                                       (run["run_id"],)).fetchone()
                self.assertEqual(started["status"], "running")
                self.assertIsNotNone(started["started_at"])
                self.assertIsNone(started["ended_at"])
            db.finish_parse_run(run["run_id"], "done", path)
            with db.connection(path) as conn:
                finished = conn.execute("SELECT status,ended_at,organization_count FROM parse_runs WHERE id=?",
                                        (run["run_id"],)).fetchone()
                self.assertEqual(finished["status"], "done")
                self.assertIsNotNone(finished["ended_at"])
                self.assertEqual(finished["organization_count"], 1)

    def test_desktop_parser_links_existing_discovered_cards_to_the_new_run(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "link-existing.sqlite3"
            db.init_desktop_db(path)
            old_job = db.create_job("search", {}, path)
            db.upsert_business(business_data("2200"), ("Ниша", "Россия", "Казань"), old_job, path)
            run = db.create_desktop_parse_jobs(search_params("Ниша"), ["Казань"], path)[0]
            from app import lead_parser
            real_business = db.business
            real_link = db.link_parse_run_business
            with patch.object(lead_parser.db, "business", side_effect=lambda identity: real_business(identity, path)), \
                 patch.object(lead_parser.db, "link_parse_run_business",
                              side_effect=lambda run_id, identity: real_link(run_id, identity, path)):
                lead_parser._link_known_desktop_result("2200", {"types": ["no_site"], "city": "КАЗАНЬ"},
                                                       run["run_id"])
            self.assertEqual(db.business_ids_for_scope({"level": "run", "run_id": run["run_id"]}, path), {"2200"})

    def test_name_address_fallback_deduplicates_when_external_id_is_missing(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "fallback.sqlite3"
            db.init_desktop_db(path)
            run = db.create_desktop_parse_jobs(search_params(), ["Казань"], path)[0]
            item = {"name": "  Тестовый   салон ", "address": "Казань, улица 1",
                    "site_type": "no_site", "map_url": "https://example.test/org/", "phones": [], "socials": []}
            self.assertTrue(db.upsert_business(item, ("Салоны красоты", "Россия", "Казань"),
                run["id"], path, parse_run_id=run["run_id"]))
            self.assertFalse(db.upsert_business({**item, "name": "тестовый салон"},
                ("Салоны красоты", "Россия", "Казань"), run["id"], path, parse_run_id=run["run_id"]))
            with db.connection(path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM parse_run_businesses").fetchone()[0], 1)

    def test_legacy_businesses_are_virtual_under_no_niche_no_city_by_creation_date(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "legacy-virtual.sqlite3"
            db.init_desktop_db(path)
            job = db.create_job("import", {}, path)
            db.upsert_business(business_data("3001"), ("старый тег", "Россия", "Казань"), job, path)
            with db.connection(path) as conn:
                conn.execute("UPDATE businesses SET created_at='2025-06-07T12:00:00+00:00' WHERE id='3001'")
            niche = db.parse_niches(path)[0]
            self.assertEqual((niche["name"], niche["key"]), ("Без ниши", "__legacy_niche__"))
            city = db.parse_cities(niche["key"], path)[0]
            self.assertEqual((city["name"], city["key"]), ("Без города", "__legacy_city__"))
            run = db.parse_runs_for_city(niche["key"], city["key"], path)[0]
            self.assertEqual(run["id"], "legacy:2025-06-07")
            self.assertEqual([row["id"] for row in db.parse_run_businesses(run["id"], path)], ["3001"])
            with db.connection(path) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM parse_run_businesses").fetchone()[0], 0)
                self.assertEqual(tuple(conn.execute("SELECT niche,city FROM business_tags WHERE business_id='3001'").fetchone()),
                                 ("старый тег", "Казань"))

    def test_scope_export_only_contains_selected_run_organizations_and_details(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder)
            path = root / "export.sqlite3"
            db.init_desktop_db(path)
            first = db.create_desktop_parse_jobs(search_params(), ["Казань"], path)[0]
            second = db.create_desktop_parse_jobs(search_params(), ["Самара"], path)[0]
            for identity, run, city in (("4001", first, "Казань"), ("4002", second, "Самара")):
                details = {"description": f"Описание {identity}", "categories": ["Тест"]}
                data = {**business_data(identity, address=f"{city}, улица 1"), "details": details,
                        "review_items": [{"text": f"Отзыв {identity}", "date": "2026-01-01"}]}
                db.upsert_business(data, ("Салоны красоты", "Россия", city), run["id"], path,
                    store_details=True, parse_run_id=run["run_id"])
            result = export_xlsx(str(root / "selected.xlsx"), {"level": "run", "run_id": first["run_id"]},
                                 db_path=path)
            self.assertEqual(result["count"], 1)
            book = load_workbook(root / "selected.xlsx", read_only=True, data_only=True)
            try:
                self.assertEqual(book["Организации"].max_row, 2)
                self.assertEqual(book["Организации"]["A2"].value, "4001")
                self.assertEqual(book["Подробности"].max_row, 2)
                self.assertEqual(book["Подробности"]["A2"].value, "4001")
                self.assertEqual(book["Отзывы"].max_row, 2)
                self.assertEqual(book["Отзывы"]["A2"].value, "4001")
            finally:
                book.close()


if __name__ == "__main__":
    unittest.main()
