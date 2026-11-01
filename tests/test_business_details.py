import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db


class BusinessDetailsTests(unittest.TestCase):
    def test_base_initializer_does_not_install_desktop_detail_schema(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "app.sqlite3"
            db.init_db(path)
            conn = sqlite3.connect(path)
            try:
                tables = {row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                conn.close()
            self.assertNotIn("business_details", tables)
            self.assertNotIn("parse_runs", tables)
            self.assertNotIn("schema_migrations", tables)

            db.init_desktop_db(path)
            conn = sqlite3.connect(path)
            try:
                tables = {row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                conn.close()
            self.assertTrue({"business_details", "reviews", "links", "hours", "features",
                             "desktop_settings", "schema_migrations", "parse_runs",
                             "parse_run_businesses"} <= tables)

    def test_synthetic_html_extracts_details_without_review_authors(self):
        from app.lead_parser import parse_org_html

        structured = {
            "@context": "https://schema.org",
            "@type": "LocalBusiness",
            "description": "Синтетическое описание",
            "geo": {"latitude": 55.75, "longitude": 37.61},
            "image": ["https://example.test/1.jpg", "https://example.test/2.jpg"],
            "openingHoursSpecification": [{"dayOfWeek": "https://schema.org/Monday",
                                            "opens": "09:00", "closes": "18:00"}],
            "amenityFeature": [{"name": "Wi-Fi", "value": True}],
            "sameAs": ["https://t.me/synthetic_place"],
            "review": [{"author": {"name": "Не сохранять автора"}, "reviewBody": "Синтетический отзыв",
                        "reviewRating": {"ratingValue": 5}, "datePublished": "2026-01-02",
                        "companyResponse": "Синтетический ответ"}],
        }
        page = f"""
        <h1 class="orgpage-header-view__header">Синтетическая кофейня</h1>
        <a class="orgpage-categories-info-view__link">Кофейня</a>
        <div class="orgpage-phones-view__phone-number">+7 900 111-22-33</div>
        <span class="business-urls-view__text">https://example.test</span>
        <span class="business-rating-badge-view__rating-text">4,9</span>
        <div class="business-header-rating-view__text">17 отзывов</div>
        <a class="orgpage-header-view__address">Казань, тестовая улица</a>
        <a href="/maps/org/cafe/123/reviews/">Отзывы</a>
        <a href="/maps/org/cafe/123/gallery/">Фото</a>
        <script type="application/ld+json">{json.dumps(structured, ensure_ascii=False)}</script>
        <article class="business-reviews-card-view">
          <span class="review-author">Не сохранять имя</span>
          <span itemprop="reviewBody">Текст из синтетической карточки отзыва</span>
          <meta itemprop="ratingValue" content="4">
          <time datetime="2026-03-04">4 марта</time>
          <div class="business-review-view__company-response-text">Ответ компании</div>
        </article>
        """
        with patch("app.lead_parser.fetch_site", return_value=(None, True)):
            row = parse_org_html("https://yandex.ru/maps/org/cafe/123/", page, 20)

        self.assertEqual(row["rating"], 4.9)
        self.assertEqual(row["reviews"], 17)
        self.assertEqual(row["details"]["description"], "Синтетическое описание")
        self.assertEqual((row["details"]["latitude"], row["details"]["longitude"]), (55.75, 37.61))
        self.assertEqual(row["details"]["photo_count"], 2)
        self.assertEqual(row["hours"][0]["weekday"], "Monday")
        self.assertEqual(row["features"][0]["name"], "Wi-Fi")
        self.assertIn({"type": "messenger", "url": "https://t.me/synthetic_place",
                       "label": "t.me"}, row["links"])
        self.assertEqual(row["review_items"][0]["text"], "Синтетический отзыв")
        self.assertNotIn("author", row["review_items"][0])

        from app.lead_parser import extract_review_items
        extracted = extract_review_items(page, 20)
        self.assertEqual(len(extracted), 2)
        self.assertNotIn("Не сохранять имя", json.dumps(extracted, ensure_ascii=False))

    def test_review_extraction_limit_caps_at_100(self):
        from app.lead_parser import extract_review_items

        entries = [{"reviewBody": f"Синтетический отзыв {i}", "datePublished": f"2026-01-{i % 28 + 1:02d}"}
                   for i in range(120)]
        page = '<script type="application/ld+json">' + json.dumps({"review": entries}) + "</script>"
        self.assertEqual(len(extract_review_items(page, 1000)), 100)
        self.assertEqual(len(extract_review_items(page, 7)), 7)
        self.assertEqual(len(extract_review_items(page, 0)), 0)

    def test_migration_backs_up_existing_database_and_preserves_rows(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "legacy.sqlite3"
            db.init_desktop_db(path)
            job_id = db.create_job("search", {}, path)
            db.upsert_business({"id": "123", "name": "Синтетическая запись", "site_type": "no_site",
                                "map_url": "https://yandex.ru/maps/org/cafe/123/"},
                               ("кафе", "Россия", "Казань"), job_id, path)
            conn = sqlite3.connect(path)
            try:
                for table in ("parse_run_businesses", "parse_runs", "business_details", "reviews", "links",
                              "hours", "features", "desktop_settings", "schema_migrations"):
                    conn.execute(f"DROP TABLE {table}")
                conn.commit()
            finally:
                conn.close()
            db.init_desktop_db(path)
            backups = list((Path(folder) / "backups").glob(
                f"legacy-pre-schema-v{db.DESKTOP_SCHEMA_VERSION}-*.sqlite3"))
            self.assertEqual(len(backups), 1)
            conn = sqlite3.connect(backups[0])
            try:
                self.assertEqual(conn.execute("SELECT name FROM businesses WHERE id='123'").fetchone()[0],
                                 "Синтетическая запись")
                self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name='business_details'").fetchone())
            finally:
                conn.close()
            self.assertEqual(db.business("123", path)["name"], "Синтетическая запись")
            self.assertEqual(db.business_review_count("123", path), 0)

    def test_details_reviews_are_deduplicated_capped_and_desktop_only(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "app.sqlite3"
            db.init_desktop_db(path)
            job_id = db.create_job("search", {}, path)
            reviews = [{"text": f"Синтетический отзыв {i}", "rating": 5, "date": "2026-01-01",
                        "company_response": "Ответ"} for i in range(100)]
            db.upsert_business({"id": "456", "name": "Тест", "site_type": "no_site",
                "map_url": "https://yandex.ru/maps/org/cafe/456/", "details": {
                    "description": "Описание", "categories": ["Кафе"], "details_complete": True},
                "review_items": reviews}, ("кафе", "Россия", "Казань"), job_id, path,
                store_details=True, review_limit=100)
            self.assertEqual(db.append_business_reviews("456", reviews, 100, path), 0)
            self.assertEqual(db.business_review_count("456", path), 100)
            full = db.business_full("456", path)
            self.assertEqual(len(full["review_items"]), 100)
            self.assertNotIn("details", db.business("456", path))
            self.assertEqual(full["details"]["description"], "Описание")
            self.assertEqual(db.set_desktop_diagnostics_enabled(True, path), True)
            self.assertTrue(db.desktop_diagnostics_enabled(path))

    def test_interrupted_review_collection_can_resume_for_same_job(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "app.sqlite3"
            db.init_desktop_db(path)
            job_id = db.create_job("search", {}, path)
            reviews = [{"text": f"Resume test review {i}", "date": f"2026-02-{i + 1:02d}"}
                       for i in range(10)]
            db.upsert_business({"id": "654", "name": "Resume test", "site_type": "no_site",
                "reviews": 40, "map_url": "https://yandex.ru/maps/org/cafe/654/",
                "details": {"categories": ["Cafe"]}, "review_items": reviews},
                ("cafe", "RU", "Kazan"), job_id, path, store_details=True, review_limit=20)
            self.assertTrue(db.business_needs_review_resume(job_id, "654", 20, path))
            self.assertEqual(db.job_result_count(job_id, path), 1)
            self.assertEqual(db.incomplete_job_review_items(job_id, 20, path), [{
                "id": "654", "map_url": "https://yandex.ru/maps/org/cafe/654/"}])
            more = [{"text": f"Resume test review {i}", "date": f"2026-02-{i + 1:02d}"}
                    for i in range(10, 20)]
            self.assertEqual(db.append_business_reviews("654", more, 20, path), 10)
            db.set_review_collection_state("654", 20, True, path)
            self.assertFalse(db.business_needs_review_resume(job_id, "654", 20, path))
            self.assertEqual(db.incomplete_job_review_items(job_id, 20, path), [])

    def test_export_adds_detail_sheets_and_sanitizes_formula_text(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            export_path = Path(folder) / "details.xlsx"
            script = """
import sys
from app import db
from app.desktop_services import export_xlsx
db.init_desktop_db(); job=db.create_job('search',{})
db.upsert_business({'id':'789','name':'=SUM(1,1)','site_type':'no_site',
 'map_url':'https://yandex.ru/maps/org/cafe/789/',
 'details':{'description':'Description','categories':['Cafe'],'latitude':55.7,'longitude':37.6,'photo_count':3},
 'review_items':[{'text':'Review','rating':5,'date':'2026-01-01','company_response':'Response'}],
 'links':[{'type':'website','url':'https://example.test','label':'example.test'}],
 'hours':[{'weekday':'Monday','opens':'09:00','closes':'18:00'}],
 'features':[{'name':'Wi-Fi','value':'Yes'}]}, ('cafe','RU','Kazan'),job,store_details=True)
print(export_xlsx(sys.argv[1])['count'])
"""
            env = {**os.environ, "DATA_DIR": str(Path(folder) / "data")}
            try:
                subprocess.run([sys.executable, "-c", script, str(export_path)], cwd=Path(__file__).resolve().parents[1],
                               env=env, capture_output=True, text=True, check=True)
            except subprocess.CalledProcessError as exc:
                self.fail(f"Export subprocess failed: {exc.stderr}")
            from openpyxl import load_workbook
            book = load_workbook(export_path, read_only=True, data_only=True)
            self.assertEqual(set(book.sheetnames), {"Организации", "Подробности", "Отзывы", "Ссылки", "График", "Особенности"})
            self.assertEqual(book["Организации"]["B2"].value, "'=SUM(1,1)")
            self.assertEqual(book["Отзывы"]["B2"].value, "Review")
            self.assertEqual(book["Подробности"]["B2"].value, "Description")
            book.close()

    def test_diagnostic_html_is_off_by_default(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            from app import lead_parser
            with patch.object(lead_parser.db, "desktop_diagnostics_enabled", return_value=False), \
                 patch.object(lead_parser, "DATA_DIR", Path(folder)):
                self.assertIsNone(lead_parser.save_failed_card_html(
                    "https://yandex.ru/maps/org/cafe/111/", "<html>private</html>", RuntimeError("test")))
            self.assertEqual(list(Path(folder).rglob("*.html")), [])

    def test_enabled_diagnostic_html_is_bounded(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            from app import lead_parser
            with patch.object(lead_parser.db, "desktop_diagnostics_enabled", return_value=True), \
                 patch.object(lead_parser, "DATA_DIR", Path(folder)):
                saved = lead_parser.save_failed_card_html(
                    "https://yandex.ru/maps/org/cafe/111/", "x" * 1_100_000, RuntimeError("test"))
            self.assertTrue(saved)
            self.assertEqual(Path(saved).stat().st_size, 1_000_000)


if __name__ == "__main__":
    unittest.main()
