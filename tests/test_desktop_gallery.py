import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from app import db, media_service
from app.media import MediaError
from app.desktop_gallery import (
    _selection_plan,
    collect_section,
    discover_sections,
    discover_sections_from_html,
    normalize_selection,
    safe_section_name,
    save_photo,
    unique_section_directory_name,
)


class DesktopGalleryTests(unittest.TestCase):
    def test_discovers_unlisted_dynamic_sections_and_skips_video(self):
        fragment = """
        <div class="media-gallery__filters">
          <button class="media-gallery__filter-button _checked">Все</button>
          <button class="media-gallery__filter-button">Услуги</button>
          <button class="media-gallery__filter-button">Примеры работ</button>
          <button class="media-gallery__filter-button">До/после</button>
          <button class="media-gallery__filter-button">Видео</button>
          <button>Закрыть</button>
        </div>
        """
        self.assertEqual(discover_sections_from_html(fragment),
                         ["Все", "Услуги", "Примеры работ", "До/после"])

    def test_only_photo_filter_chips_are_sections(self):
        fragment = """
        <nav role="tablist"><button role="tab">Обзор</button><button role="tab">Отзывы 19</button></nav>
        <div class="media-gallery__filters"><button>Все</button><button>Вход</button>
          <button>Внутри</button><button>Услуги</button><button>Доступность</button>
          <button>Снаружи</button><button>Товары</button><button>Видео</button></div>
        <div class="map-controls"><button>Слои</button><button>Особенности</button></div>
        """
        self.assertEqual(discover_sections_from_html(fragment),
                         ["Все", "Вход", "Внутри", "Услуги", "Доступность", "Снаружи", "Товары"])

    def test_all_and_missing_selection_use_dynamic_or_general_gallery(self):
        available = ["Все", "Услуги", "Примеры работ", "Видео"]
        all_plan = _selection_plan({"mode": "all", "names": []}, available)
        self.assertEqual([item["label"] for item in all_plan], ["Услуги", "Примеры работ"])
        missing = _selection_plan({"mode": "selected", "names": ["Работы"]}, ["Услуги"])
        self.assertEqual(missing, [{"requested": "Работы", "label": "Все", "button": None}])
        no_sections = _selection_plan({"mode": "all", "names": []}, [])
        self.assertEqual(no_sections, [{"requested": "Все разделы", "label": "Все", "button": None}])

    def test_preflight_returns_categories_without_downloading_images(self):
        driver = object()
        with patch("app.desktop_gallery._make_driver", return_value=driver), \
                patch("app.desktop_gallery._open_gallery", return_value=("Синтетический салон", "https://yandex.ru/maps/org/test/123/")), \
                patch("app.desktop_gallery.wait_for_gallery_sections", return_value=["Все", "Услуги", "Видео"]), \
                patch("app.desktop_gallery.quit_driver") as quit_driver:
            result = discover_sections("https://yandex.ru/maps/org/test/123/")
        self.assertEqual(result["sections"], ["Все", "Услуги"])
        self.assertFalse(result["fallback"])
        quit_driver.assert_called_once_with(driver)

    def test_selection_normalization_accepts_arbitrary_labels_and_rejects_video(self):
        self.assertEqual(normalize_selection([" Услуги  ", "услуги", "До/после"]),
                         {"mode": "selected", "names": ["Услуги", "До/после"]})
        with self.assertRaises(ValueError):
            normalize_selection(["Видео"])

    def test_safe_names_preserve_cyrillic_and_avoid_windows_reserved_names(self):
        self.assertEqual(safe_section_name("До/после: этап 1"), "До_после__этап_1")
        self.assertEqual(safe_section_name("CON"), "_CON")
        used = {}
        first = unique_section_directory_name("Фото/работы", used)
        second = unique_section_directory_name("Фото\\работы", used)
        self.assertEqual(first, "Фото_работы")
        self.assertNotEqual(first.casefold(), second.casefold())

    def test_images_are_content_deduplicated_and_have_sidecar_metadata(self):
        payload = b"synthetic-image-bytes"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            root = Path(folder)
            known = {}
            first, duplicate = save_photo(root / "Услуги", "Услуги",
                "https://yandex.ru/maps/org/test/123/", "https://cdn.example/1",
                payload, ".jpg", known)
            repeated, is_duplicate = save_photo(root / "Работы", "Работы",
                "https://yandex.ru/maps/org/test/123/", "https://cdn.example/2",
                payload, ".jpg", known)
            self.assertFalse(duplicate)
            self.assertTrue(is_duplicate)
            self.assertIsNotNone(first)
            self.assertIsNone(repeated)
            self.assertEqual(first.parent.name, "Услуги")
            metadata = json.loads(first.with_name(first.name + ".json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["section"], "Услуги")
            self.assertEqual(metadata["source"], "Яндекс.Карты")
            self.assertEqual(metadata["size_bytes"], len(payload))
            self.assertEqual(metadata["sha256"], digest)
            images = [path for path in root.rglob("*") if path.suffix == ".jpg"]
            self.assertEqual(len(images), 1)

    def test_per_section_and_organization_limits_are_applied_before_scrolling(self):
        urls = ["https://cdn.example/a", "https://cdn.example/b"]
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            with patch("app.desktop_gallery.ensure_space"):
                result = collect_section(
                    "Услуги", Path(folder) / "Услуги",
                    "https://yandex.ru/maps/org/test/123/", {}, 5, 1,
                    lambda: urls, lambda: (0, 0, 0), lambda url: (url.encode(), ".jpg"),
                )
            images = [path for path in Path(folder).rglob("*.jpg")]
            self.assertEqual(len(images), 1)
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["missing"], 4)

    def test_gallery_request_validates_dynamic_names_and_limits(self):
        accepted = media_service._gallery_request(
            {"mode": "selected", "names": ["Услуги"]}, None, 7, 30)
        self.assertEqual(accepted["selection"]["names"], ["Услуги"])
        self.assertEqual(accepted["per_section_limit"], 7)
        self.assertEqual(accepted["organization_limit"], 30)
        with self.assertRaises(MediaError):
            media_service._gallery_request("__all__", None, 7, 201)
        with self.assertRaises(MediaError):
            media_service._gallery_request(None, None, 7, 30)
        with self.assertRaises(MediaError):
            media_service._gallery_request("__all__", None, 1.5, 30)

    def test_resume_requeues_failed_gallery_without_new_job_or_data_loss(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "app.sqlite3"
            db.init_db(path)
            job, _ = db.create_media_job("images", {"url": "https://yandex.ru/maps/org/test/123/"},
                                         "123", None, path=path)
            db.update_job(job["id"], status="failed", error="synthetic stop", path=path)
            original_connection = db.connection
            @contextmanager
            def temporary_connection(_path=db.DB_PATH):
                with original_connection(path) as conn:
                    yield conn
            with patch.object(db, "connection", temporary_connection):
                resumed = media_service.resume_images(job["id"])
                self.assertEqual(resumed["status"], "queued")
                self.assertEqual(resumed["error"], None)
            conn = sqlite3.connect(path)
            try:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 1)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
