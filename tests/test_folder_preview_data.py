import tempfile
import unittest
from pathlib import Path

from app import db


class FolderPreviewDataTests(unittest.TestCase):
    def test_niche_and_city_previews_are_bounded_and_report_remaining_items(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "folder-preview.sqlite3"
            db.init_desktop_db(path)
            params = {"country": "Россия", "niche": "Салоны красоты", "count": 20,
                      "types": ["no_site"], "review_limit": 10}
            for city in ("Казань", "Самара", "Тула", "Омск", "Уфа"):
                db.create_desktop_parse_jobs(params, [city], path)

            niche = db.parse_niches(path)[0]
            self.assertEqual(niche["city_count"], 5)
            self.assertEqual(len(niche["recent_cities"]), 4)
            self.assertEqual(niche["more_city_count"], 1)

            for _ in range(5):
                db.create_desktop_parse_jobs(params, ["Казань"], path)
            kazan = next(city for city in db.parse_cities(niche["key"], path) if city["key"] == "казань")
            self.assertEqual(kazan["run_count"], 6)
            self.assertEqual(len(kazan["recent_runs"]), 4)
            self.assertEqual(kazan["more_run_count"], 2)


if __name__ == "__main__":
    unittest.main()
