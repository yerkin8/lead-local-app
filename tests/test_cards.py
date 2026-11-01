"""«Добавить лида» by link, «Дособрать номера» and the owner's own numbers and links (manual_contacts).
No network: card pages are synthetic, numbers are not real."""

from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import cards, db, leads, lead_parser, outreach

ID = "1000000001"


def card_page(identity=ID, phones=("+7 (3452) 00-00-01", "+7 (900) 000-00-02"), city="Тюмень"):
    state = {"stack": [{"results": {"items": [{
        "id": identity, "phones": [{"type": "phone", "number": p} for p in phones],
        "ratingData": {"ratingCount": 20, "reviewCount": 0}, "photos": {"count": 4},
        "compositeAddress": {"country": "Россия", "locality": city}, "categories": [{"name": "Кафе"}]}]}}]}
    return (f"<h1 class='orgpage-header-view__header'>Кафе {identity}</h1>"
            f"<a class='orgpage-header-view__address'>{city}, ул. Первая, 1</a>"
            "<a class='business-categories-view__category'>Кафе</a>"
            f"<div class='orgpage-phones-view__phone-number'>{phones[0]}</div>"
            f"<script type='application/json' class='state-view'>{json.dumps(state, ensure_ascii=False)}</script>")


class LinkTests(unittest.TestCase):
    def test_link_forms(self):
        cases = {
            "https://yandex.ru/maps/org/granat/1011865976/": "1011865976",
            "https://yandex.ru/maps/org/granat/1011865976/reviews/?ll=49.6%2C58.6&z=16": "1011865976",
            "yandex.ru/maps/org/granat/1011865976": "1011865976",
            "https://yandex.com/maps/org/granat/1011865976/": "1011865976",
            "https://yandex.ru/maps/46/kirov/?ll=49.6%2C58.6&mode=search&oid=1011865976&ol=biz&z=16": "1011865976",
            "https://yandex.ru/profile/1011865976": "1011865976",
            "https://yandex.ru/maps/org/1011865976/": "1011865976",
            "https://google.com/maps/org/x/1011865976/": None,
            "https://yandex.ru/maps/-/CHuBy2~M": None,
        }
        for url, expected in cases.items():
            self.assertEqual(cards.org_id_from_url(url), expected, url)
        self.assertTrue(cards.is_short_link("https://yandex.ru/maps/-/CHuBy2~M"))

    def test_short_link_follows_redirect(self):
        class Response:
            history = []
            headers = {}
            url = "https://yandex.ru/maps/org/granat/1011865976/?ll=1"

        session = type("S", (), {"get": lambda self, url, **kw: Response()})()
        self.assertEqual(cards.resolve_link(session, "https://yandex.ru/maps/-/CHuBy2~M"), "1011865976")


class CardJobsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.tmp.name) / "c.sqlite3"
        db.init_desktop_db(self.path)
        self.patches = [patch.object(db, "DB_PATH", self.path)]
        for name in ("connection", "business", "business_full", "upsert_business", "create_job", "job",
                     "update_job", "set_business_phones", "business_phones_raw", "add_manual_contact",
                     "delete_manual_contact", "refresh_business", "set_review_collection_state",
                     "business_review_count", "append_business_reviews", "list_jobs_of_kind"):
            self.patches.append(patch.object(db, name, self.with_path(getattr(db, name))))
        for item in self.patches:
            item.start()
        self.job = db.create_job("search", {})

    def with_path(self, func):
        """Run db functions on the test base unless the caller passed a path."""
        signature = inspect.signature(func)

        def wrapper(*args, **kwargs):
            bound = signature.bind_partial(*args, **kwargs)
            if "path" not in bound.arguments:
                kwargs["path"] = self.path
            return func(*args, **kwargs)
        return wrapper

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def biz(self, identity, phones):
        db.upsert_business({"id": identity, "name": "Кафе", "site_type": "no_site", "address": "Тюмень",
                            "phones": phones, "socials": [], "reviews": 3,
                            "map_url": f"https://yandex.ru/maps/org/x/{identity}/"}, ("кафе", "Россия", "Тюмень"),
                           self.job)

    def run_job(self, job_id, pages):
        def fake(_session, url):
            identity = cards.org_id_from_url(url)
            return url, pages[identity]
        item = db.job(job_id)
        with patch.object(lead_parser, "request_page", side_effect=fake), patch.object(cards, "request_page", side_effect=fake), \
                patch.object(cards.cancel, "sleep"), patch.object(cards, "fetch_site", return_value=(None, True)):
            return cards.run_cards(item)

    def test_refill_adds_the_hidden_mobile_and_keeps_order(self):
        self.biz(ID, ["+7 (3452) 00-00-01"])
        self.biz("1000000002", ["+7 (900) 000-00-05", "+7 (3452) 00-00-06"])   # already has a mobile and two numbers
        self.assertEqual(cards.leads_missing_phones(self.path), [ID])
        job_id = db.create_job("cards", {"mode": "phones", "ids": [ID]})
        result = self.run_job(job_id, {ID: card_page()})
        self.assertEqual((result["added"], result["leads"]), (1, 1))
        self.assertEqual(db.business(ID)["phones"], ["+7 (3452) 00-00-01", "+7 (900) 000-00-02"])

    def test_refill_captcha_is_a_status(self):
        self.biz(ID, ["+7 (3452) 00-00-01"])
        job_id = db.create_job("cards", {"mode": "phones", "ids": [ID]})
        with patch.object(cards, "request_page", side_effect=lead_parser.SearchBlocked("капча")):
            result = cards.run_cards(db.job(job_id))
        self.assertTrue(result["blocked"])

    def test_add_lead_by_link_parses_the_card_and_a_duplicate_opens_it(self):
        job_id = db.create_job("cards", {"mode": "add", "url": f"https://yandex.ru/maps/org/cafe/{ID}/?ll=1,2",
                                         "review_limit": 0})
        result = self.run_job(job_id, {ID: card_page()})
        self.assertEqual((result["business_id"], result["existed"]), (ID, False))
        lead = db.business(ID)
        self.assertEqual(len(lead["phones"]), 2)
        self.assertEqual((lead["city"], lead["niche"], lead["country"]), ("Тюмень", "кафе", "Россия"))
        again = db.create_job("cards", {"mode": "add", "url": f"https://yandex.com/maps/org/cafe/{ID}/"})
        self.assertEqual(self.run_job(again, {})["existed"], True)

    def test_manual_contacts_survive_a_card_refresh_and_reach_contacts(self):
        self.biz(ID, ["+7 (3452) 00-00-01"])
        db.add_manual_contact(ID, "phone", "8 900 000 00 09")
        db.add_manual_contact(ID, "link", "@cafe_owner")
        with self.assertRaises(ValueError):
            db.add_manual_contact(ID, "phone", "12")
        with self.assertRaises(ValueError):
            db.add_manual_contact(ID, "link", "просто текст")
        row = lead_parser.parse_org_html(f"https://yandex.ru/maps/org/x/{ID}/", card_page(phones=("+7 (3452) 00-00-01",)),
                                         0, check_site=False)
        db.refresh_business(row)
        lead = db.business(ID)
        self.assertEqual(lead["phones"], ["+7 (3452) 00-00-01", "+7 900 000-00-09"])
        self.assertIn("https://t.me/cafe_owner", [item["url"] for item in lead["socials"]])
        self.assertEqual({item["kind"] for item in lead["manual_contacts"]}, {"phone", "link"})
        contacts = [item["contact"] for item in outreach.contacts(lead)]
        self.assertIn("@cafe_owner", contacts)
        listed = leads.list_leads({"segment": "all"}, self.path)["items"]
        self.assertEqual(next(i for i in listed if i["id"] == ID)["contact"], "@cafe_owner")
        manual_id = lead["manual_contacts"][0]["id"]
        self.assertTrue(db.delete_manual_contact(manual_id))
        self.assertEqual(db.business(ID)["phones"], ["+7 (3452) 00-00-01"])


if __name__ == "__main__":
    unittest.main()
