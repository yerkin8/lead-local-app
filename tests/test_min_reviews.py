"""«Отзывов от N» in the search and review counts read correctly from the card."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Desktop: the server agent API is not part of the app, so its threshold case is not here.
from app import db
from app.desktop_services import validate_search

PARSER = importlib.util.find_spec("bs4") and importlib.util.find_spec("selenium")
SEARCH = {"country": "Россия", "cities": "Казань", "niche": "кафе", "count": 5, "types": ["no_site"]}


class ValidateTests(unittest.TestCase):
    def test_min_reviews_is_optional_and_checked(self):
        self.assertEqual(validate_search(SEARCH)["min_reviews"], 0)
        self.assertEqual(validate_search({**SEARCH, "min_reviews": ""})["min_reviews"], 0)
        self.assertEqual(validate_search({**SEARCH, "min_reviews": "500"})["min_reviews"], 500)
        for bad in (-1, "много", 10_000_001):
            with self.assertRaises(ValueError):
                validate_search({**SEARCH, "min_reviews": bad})

    def test_threshold_is_part_of_the_checked_cards_key(self):
        base = {"country": "Россия", "city": "Казань", "niche": "кафе", "types": ["no_site"]}
        self.assertEqual(db.search_key(base), db.search_key({**base, "min_reviews": 0}))  # old history stays valid
        self.assertNotEqual(db.search_key(base), db.search_key({**base, "min_reviews": 500}))
        self.assertNotEqual(db.search_key({**base, "min_reviews": 100}), db.search_key({**base, "min_reviews": 500}))


@unittest.skipUnless(PARSER, "Parser dependencies are not installed")
class ParserTests(unittest.TestCase):
    def test_review_count_with_thousands(self):
        from app.lead_parser import review_count
        for text, expected in [("161 отзыв", 161), ("1 234 отзыва", 1234), ("1 234 отзыва", 1234),
                               ("12 345 отзывов", 12345), ("1,2 тыс. отзывов", 1200), ("2 отзыва", 2),
                               ("", 0), (None, 0), ("Нет отзывов", 0)]:
            self.assertEqual(review_count(text), expected, text)

    def test_card_with_thousands_of_reviews(self):
        from app.lead_parser import parse_org_html
        page = ("<h1 class='orgpage-header-view__header'>Кафе</h1>"
                "<div class='business-header-rating-view__text'>1 234 отзыва</div>")
        with patch("app.lead_parser.fetch_site", return_value=(None, True)):
            self.assertEqual(parse_org_html("https://yandex.ru/maps/org/cafe/1/", page)["reviews"], 1234)

    def test_search_skips_cards_below_threshold(self):
        from app import lead_parser
        reviews = {"1": 40, "2": 700, "3": 500, "4": 1200}

        def fake_page(_session, url):
            if "/maps/org/" in url:
                return url, "card"
            return url, "".join(f"<div class='search-business-snippet-view__content'>"
                                f"<a href='/maps/org/b/{i}/'>Card</a></div>" for i in reviews)

        def fake_org(url, _page, _review_limit=100, **_kwargs):
            ident = url.rstrip("/").split("/")[-1]
            return {"id": ident, "site_type": "no_site", "address": "Казань", "reviews": reviews[ident]}

        job = {"id": "j", "params": {"country": "Россия", "city": "Казань", "niche": "кафе", "count": 5,
                                     "types": ["no_site"], "min_reviews": 500}}
        with patch.object(lead_parser, "request_page", side_effect=fake_page), \
             patch.object(lead_parser, "parse_org_html", side_effect=fake_org), \
             patch.object(lead_parser.db, "known_ids", return_value=set()), \
             patch.object(lead_parser.db, "checked_ids", return_value=set()), \
             patch.object(lead_parser.db, "mark_checked"), \
             patch.object(lead_parser.db, "upsert_business", return_value=True) as save, \
             patch.object(lead_parser.db, "update_job"), \
             patch.object(lead_parser.time, "sleep"):
            result = lead_parser.run_search_http(job)
        self.assertEqual(sorted(call.args[0]["id"] for call in save.call_args_list), ["2", "3", "4"])
        self.assertEqual(result["matched"], 3)
        self.assertIn("отзывов от 500", result["reason"])
