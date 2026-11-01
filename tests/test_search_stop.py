"""Search speed (two passes, parallel site checks, reviews from one page) and «Остановить»."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class SearchStopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as data_dir:
            env = {**os.environ, "DATA_DIR": data_dir, "PYTHONIOENCODING": "utf-8"}
            done = subprocess.run([sys.executable, str(HERE / "search_stop_case.py")], env=env,
                                  capture_output=True, text=True, encoding="utf-8", timeout=180)
        if done.returncode:
            raise AssertionError(done.stderr[-4000:])
        cls.r = json.loads(done.stdout.strip().splitlines()[-1])

    def test_queued_job_stops_and_is_never_started(self):
        self.assertEqual(self.r["queued_stop"], "stopped")
        self.assertEqual(self.r["queued_job_status"], "stopped")
        self.assertEqual(self.r["queued_run_status"], "stopped")
        self.assertFalse(self.r["queued_claimed"])

    def test_running_job_stops_between_cards_and_keeps_found(self):
        self.assertEqual(self.r["running_status"], "stopped")
        self.assertEqual(self.r["running_run_status"], "stopped")
        self.assertEqual(self.r["running_saved"], 4)   # the card that was loading is finished
        self.assertEqual(self.r["running_result_matched"], 4)
        self.assertTrue(self.r["running_leads_exist"])
        self.assertEqual(self.r["running_card_requests"], 4)

    def test_cards_first_then_reviews_from_one_page(self):
        self.assertEqual(self.r["two_pass_status"], "done")
        self.assertTrue(self.r["two_pass_cards_before_details"])
        self.assertEqual(self.r["two_pass_review_pages"], 3)          # one page per card
        self.assertEqual(self.r["two_pass_reviews"], [3, 3, 3])
        self.assertEqual(self.r["two_pass_photo_count"], 7)       # from the card JSON
        self.assertEqual(self.r["two_pass_gallery_pages"], 0)      # no gallery request (E-S-08)
        self.assertEqual(self.r["two_pass_review_field"], 3)       # reviewCount, not 9 ratings (E-S-07)
        self.assertEqual(self.r["two_pass_progress_phase"], "details")
        self.assertIn("card_page", self.r["two_pass_timing_stages"])
        self.assertIn("reviews", self.r["two_pass_timing_stages"])

    def test_stop_during_review_pass_keeps_cards(self):
        self.assertEqual(self.r["review_stop_status"], "stopped")
        self.assertEqual(self.r["review_stop_saved"], 3)

    def test_stop_pressed_before_start(self):
        self.assertEqual(self.r["race_status"], "stopped")
        self.assertEqual(self.r["race_requests"], 0)

    def test_captcha_is_still_blocked(self):
        self.assertEqual(self.r["blocked_status"], "blocked")

    def test_site_checks_run_in_parallel(self):
        self.assertEqual(self.r["parallel_status"], "done")
        self.assertEqual(self.r["parallel_saved"], 6)
        self.assertLess(self.r["parallel_seconds"], 2.5)   # six sites one by one would take 3.6 s
        self.assertEqual(self.r["threads_left"], 0)


class ReviewExtractionTests(unittest.TestCase):
    def test_live_markup_gives_every_review_on_the_page(self):
        from app.lead_parser import extract_review_items
        page = "".join(
            "<div class='business-reviews-card-view'>" +
            "".join("<div class='business-reviews-card-view__review'><div class='business-review-view'>"
                    "<meta itemprop='ratingValue' content='4.0'/>"
                    f"<meta itemprop='datePublished' content='2026-09-{i + 10}T17:13:53.428Z'/>"
                    "<span class='business-review-view__date'>11 сентября</span>"
                    f"<div class='business-review-view__body' itemprop='reviewBody'><span>Отзыв {i}</span></div>"
                    "</div></div>" for i in range(5)) + "</div>")
        items = extract_review_items(page, 100)
        self.assertEqual([item["text"] for item in items], [f"Отзыв {i}" for i in range(5)])
        self.assertEqual(items[0]["rating"], 4.0)
        self.assertTrue(items[0]["date"].startswith("2026-09-10"))

    def test_search_page_state_gives_more_links(self):
        from bs4 import BeautifulSoup
        from app.lead_parser import http_search_links
        state = {"stack": [{"results": {"items": [{"id": str(500 + i), "seoname": f"org_{i}"} for i in range(25)]}}]}
        soup = BeautifulSoup("<div class='search-business-snippet-view__content'>"
                             "<a href='/maps/org/org_0/500/'>A</a></div>"
                             f"<script class='state-view' type='application/json'>{json.dumps(state)}</script>",
                             "html.parser")
        links, _ = http_search_links("https://yandex.ru/maps/?text=x", soup)
        self.assertEqual(len(links), 25)
        self.assertEqual(links[0], "https://yandex.ru/maps/org/org_0/500/")
        self.assertEqual(links[-1], "https://yandex.ru/maps/org/org_24/524/")


if __name__ == "__main__":
    unittest.main()


class SearchRobustnessTests(unittest.TestCase):
    """REVIEW.md E-S-11…E-S-13 on synthetic pages (same child process script as above)."""
    setUpClass = classmethod(SearchStopTests.setUpClass.__func__)

    def test_one_failed_card_is_skipped(self):
        self.assertEqual(self.r["one_bad_status"], "done")
        self.assertEqual(self.r["one_bad_saved"], 3)
        self.assertEqual(self.r["one_bad_errors"], 1)

    def test_no_connection_is_interrupted_not_done(self):
        self.assertEqual(self.r["no_net_status"], "interrupted")
        self.assertEqual(self.r["no_net_run_status"], "stopped")

    def test_reviews_cut_by_captcha_come_on_the_next_run(self):
        self.assertEqual(self.r["captcha_first_status"], "blocked")
        self.assertEqual(self.r["captcha_first_reviews"], [0, 0, 0])
        self.assertEqual(self.r["captcha_second_status"], "done")
        self.assertEqual(self.r["captcha_second_reviews"], [3, 3, 3])

    def test_city_typo_gets_a_hint(self):
        self.assertIn("проверьте написание города", self.r["typo_reason"])
