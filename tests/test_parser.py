import importlib.util
import json
import unittest
from unittest.mock import patch
from urllib.parse import unquote


@unittest.skipUnless(importlib.util.find_spec("bs4") and importlib.util.find_spec("selenium"),
                     "Parser dependencies are not installed")
class ParserExtractionTests(unittest.TestCase):
    def test_gallery_section_aliases(self):
        from yandex_images_parser import section_buttons

        class Button:
            def __init__(self, label):
                self.text = label

        class Driver:
            def find_elements(self, *_args):
                return [Button("All"), Button("Interior"), Button("Food"), Button("Menu")]

        self.assertEqual(section_buttons(Driver()), {
            "все": "All", "внутри": "Interior", "еда": "Food", "меню": "Menu",
        })

    def test_search_tries_second_query_when_first_page_ends(self):
        from app import lead_parser

        def fake_page(_session, url):
            if "/maps/org/" in url:
                return url, "card"
            identity = "1" if "Россия" in unquote(url) else "2"
            return url, ("<div class='search-business-snippet-view__content'>"
                         f"<a href='/maps/org/business/{identity}/'>Card</a></div>")

        def fake_org(url, _page, _review_limit=100, **_kwargs):
            return {"id": url.rstrip("/").split("/")[-1], "site_type": "no_site", "address": "Тверь"}

        job = {"id": "test-job", "params": {"country": "Россия", "city": "Тверь",
               "niche": "стоматология", "count": 2, "types": ["no_site"]}}
        with patch.object(lead_parser, "request_page", side_effect=fake_page), \
             patch.object(lead_parser, "parse_org_html", side_effect=fake_org), \
             patch.object(lead_parser.db, "known_ids", return_value=set()), \
             patch.object(lead_parser.db, "checked_ids", return_value=set()), \
             patch.object(lead_parser.db, "mark_checked"), \
             patch.object(lead_parser.db, "upsert_business", return_value=True) as save, \
             patch.object(lead_parser.db, "update_job"), \
             patch.object(lead_parser.time, "sleep"):
            result = lead_parser.run_search_http(job)
        self.assertEqual(result["matched"], 2)
        self.assertEqual(result["reason"], "Цель достигнута")
        self.assertEqual(save.call_count, 2)

    def test_empty_search_does_not_open_blocked_browser(self):
        from app import lead_parser

        job = {"id": "test-job", "params": {"country": "Россия", "city": "волгорад",
               "niche": "салон красоты", "count": 20, "types": ["no_site"]}}
        with patch.object(lead_parser, "request_page", return_value=(
                "https://yandex.com/maps/", "<div>Ничего не найдено. Попробуйте изменить запрос или масштаб.</div>")) as fetch, \
             patch.object(lead_parser, "run_search_browser") as browser, \
             patch.object(lead_parser.db, "known_ids", return_value=set()), \
             patch.object(lead_parser.db, "checked_ids", return_value=set()):
            result = lead_parser.run_search_http(job)
        self.assertEqual(fetch.call_count, 2)
        browser.assert_not_called()
        self.assertEqual(result["scanned"], 0)
        self.assertIn("Проверьте написание города", result["reason"])

    def test_products_from_embedded_state(self):
        from app.media import products_from_state

        payload = {"stack": [{"results": {"items": [{
            "id": "123", "title": "Кафе", "fullObjects": {"categories": [{
                "categoryName": "Меню", "categoryItems": [{
                    "title": "Суп", "price": "300", "currency": "₽",
                    "photoLink": "https://avatars.mds.yandex.net/get-sprav-products/1/2/{size}",
                }],
            }]},
        }]}}]}
        html = '<script type="application/json">' + json.dumps(payload) + '</script>'
        name, products = products_from_state(html, "123")
        self.assertEqual(name, "Кафе")
        self.assertEqual(products[0].price, "300 ₽")
        self.assertEqual(products[0].image_urls, ["https://avatars.mds.yandex.net/get-sprav-products/1/2/orig"])

    def test_http_search_links_and_locale(self):
        from bs4 import BeautifulSoup
        from app.lead_parser import http_search_links, localized_url

        soup = BeautifulSoup("""
          <div class='search-business-snippet-view__content'>
            <a href='/maps/org/coffee/123/reviews/'>Отзывы</a>
            <a href='/maps/org/coffee/123/gallery/'>Фото</a>
          </div>
          <a href='/maps/1/search/coffee/?page=2'>2</a>
        """, "html.parser")
        businesses, pages = http_search_links("https://yandex.ru/maps/?text=coffee", soup)
        self.assertEqual(businesses, ["https://yandex.ru/maps/org/coffee/123/"])
        self.assertEqual(pages, ["https://yandex.ru/maps/1/search/coffee/?page=2"])
        self.assertIn("lang=ru_RU", localized_url(pages[0]))

    def test_http_search_links_after_redirect_to_com(self):
        from bs4 import BeautifulSoup
        from app.lead_parser import http_search_links, localized_url

        soup = BeautifulSoup("""
          <div class='search-business-snippet-view__content'>
            <a href='/maps/org/coffee/123/reviews/'>Отзывы</a>
            <a href='/maps/org/coffee/123/gallery/'>Фото</a>
          </div>
          <a href='/maps/1/search/coffee/?page=2'>2</a>
        """, "html.parser")
        businesses, pages = http_search_links("https://yandex.com/maps/?text=coffee", soup)
        self.assertEqual(businesses, ["https://yandex.ru/maps/org/coffee/123/"])
        self.assertEqual(pages, ["https://yandex.ru/maps/1/search/coffee/?page=2"])
        self.assertIn("lang=ru_RU", localized_url(pages[0]))

    def test_tracking_parameters_do_not_duplicate_social_profile(self):
        from bs4 import BeautifulSoup
        from app.lead_parser import extract_socials

        soup = BeautifulSoup("""
          <a href='https://t.me/example?erid=one'>Telegram</a>
          <a href='https://t.me/example?erid=two'>Telegram</a>
          <a href='https://vk.com/example'>VK</a>
          <a href='https://vk.com.evil.example/example'>Not VK</a>
          <footer><a href='https://vk.com/yandex.maps'>Yandex footer</a></footer>
        """, "html.parser")
        self.assertEqual(extract_socials(soup), [
            {"label": "t.me", "url": "https://t.me/example"},
            {"label": "vk.com", "url": "https://vk.com/example"},
        ])
