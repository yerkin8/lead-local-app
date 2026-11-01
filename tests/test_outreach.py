import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from app import db, outreach

A, B = "tg:111", "tg:222"


class OutreachTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.tmp.name) / "o.sqlite3"
        db.init_desktop_db(self.path)
        self.job = db.create_job("search", {"niche": "кафе"}, self.path)
        outreach.save_settings({"delay_seconds": 30}, self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def biz(self, ident, site_type="no_site", phones=None, socials=(), rating=4.5, status=None):
        phones = phones or ("+7 900 123-45-6" + ident,)
        db.upsert_business({"id": ident, "name": "Кафе " + ident, "site_type": site_type, "address": "Казань",
                            "phones": list(phones), "socials": list(socials), "rating": rating, "reviews": 10,
                            "map_url": f"https://yandex.ru/maps/org/x/{ident}/"},
                           ("кафе", "Россия", "Казань"), self.job, self.path)
        if status:
            db.set_business(ident, status, None, self.path)

    def today(self, actor=A):
        return outreach.today(actor, path=self.path)

    def open_chat(self, ident, actor=A, stage="first", contact=None, text="Здравствуйте!"):
        item = next(i for i in self.today(actor)["items"] if i["business_id"] == ident)
        return outreach.prepare(actor, ident, stage, contact or item["contacts"][0]["contact"], text, self.path)

    def skip_pause(self):
        with db.connection(self.path) as conn:
            conn.execute("UPDATE outreach SET opened_at=datetime(opened_at, '-10 minutes')")
        # opened_at is ISO with offset; rewrite in the same format
        with db.connection(self.path) as conn:
            for row in conn.execute("SELECT id FROM outreach").fetchall():
                conn.execute("UPDATE outreach SET opened_at=? WHERE id=?",
                             (outreach._iso(outreach._now() - timedelta(minutes=10)), row[0]))

    def test_defaults_and_ranges(self):
        fresh = Path(self.tmp.name) / "fresh.sqlite3"
        db.init_desktop_db(fresh)
        self.assertEqual(outreach.settings(fresh), {"daily_limit": 100, "delay_seconds": 60, "mode": "cold"})
        with self.assertRaises(ValueError):
            outreach.save_settings({"daily_limit": 101}, self.path)
        with self.assertRaises(ValueError):
            outreach.save_settings({"delay_seconds": 10}, self.path)
        self.assertEqual(outreach.save_settings({"daily_limit": 1}, self.path)["daily_limit"], 1)

    def test_queue_order_and_exclusions(self):
        self.biz("1", "unavailable")
        self.biz("2", "no_site", rating=4.0)
        self.biz("3", "no_site", rating=4.9)
        self.biz("4", "weak_site")
        self.biz("5", "no_site", phones=("+7 843 200-00-00",))  # landline only: no Telegram link
        self.biz("6", "no_site", status="working")
        self.biz("7", "no_site")
        db.set_suppression("7", True, "нет", self.path)
        self.assertEqual([i["business_id"] for i in self.today()["items"]], ["3", "2", "4", "1"])

    def test_username_contacts_come_first(self):
        self.biz("1", socials=[{"type": "telegram", "url": "https://t.me/cafe_one"},
                               {"type": "telegram", "url": "https://t.me/joinchat/abc"}])
        contacts = self.today()["items"][0]["contacts"]
        self.assertEqual([c["contact"] for c in contacts], ["@cafe_one", "+79001234561"])

    def test_prepare_builds_link_with_footer_and_checks_brackets(self):
        self.biz("1")
        with self.assertRaises(ValueError):
            self.open_chat("1", text="Здравствуйте, [имя]!")
        result = self.open_chat("1", text="Привет")
        url = urlparse(result["url"])
        self.assertEqual(url.netloc + url.path, "t.me/+79001234561")
        self.assertEqual(parse_qs(url.query)["text"][0], "Привет\n\n" + outreach.OPT_OUT_FOOTER)
        self.assertTrue(result["message"].endswith(outreach.OPT_OUT_FOOTER))
        self.assertEqual(result["message"].count("СТОП"), 1)

    def test_one_open_chat_at_a_time_pause_and_limit(self):
        self.biz("1"), self.biz("2"), self.biz("3")
        first = self.open_chat("1")
        with self.assertRaisesRegex(ValueError, "предыдущего"):
            self.open_chat("2")
        outreach.mark(A, first["outreach_id"], "sent", self.path)
        with self.assertRaisesRegex(ValueError, "Пауза"):
            self.open_chat("2")
        self.skip_pause()
        outreach.save_settings({"daily_limit": 1}, self.path)
        with self.assertRaisesRegex(ValueError, "лимит"):
            self.open_chat("2")
        # Limits are per sender: the second admin still can write.
        second = self.open_chat("2", actor=B)
        self.assertTrue(second["url"])
        self.assertEqual(self.today(B)["budget"]["used"], 1)

    def test_not_sent_frees_the_slot(self):
        self.biz("1")
        outreach.save_settings({"daily_limit": 1}, self.path)
        opened = self.open_chat("1")
        self.assertEqual(self.today()["budget"]["left"], 0)
        outreach.mark(A, opened["outreach_id"], "not_sent", self.path)
        self.assertEqual(self.today()["budget"]["left"], 1)
        self.assertEqual(self.today()["items"][0]["business_id"], "1")

    def test_results(self):
        for ident in "12345":
            self.biz(ident, phones=("+7 900 123-45-6" + ident, "+7 900 765-43-2" + ident))
        outcomes = {"1": "sent", "2": "replied", "3": "stop", "4": "no_account"}
        for ident, result in outcomes.items():
            opened = self.open_chat(ident)
            outreach.mark(A, opened["outreach_id"], result, self.path)
            self.skip_pause()
        self.assertEqual(db.business("1", self.path)["status"], "waiting")
        self.assertEqual(db.business("2", self.path)["status"], "replied")
        self.assertEqual(db.business("3", self.path)["status"], "unsuitable")
        self.assertTrue(db.business("3", self.path)["do_not_contact"])
        item4 = next(i for i in self.today()["items"] if i["business_id"] == "4")
        self.assertEqual([c["contact"] for c in item4["contacts"]], ["+79007654324"])
        ids = [i["business_id"] for i in self.today()["items"]]
        self.assertNotIn("1", ids)
        self.assertNotIn("3", ids)
        with self.assertRaises(ValueError):
            outreach.mark(A, 1, "sent", self.path)

    def test_followup_after_three_days(self):
        self.biz("1")
        opened = self.open_chat("1")
        outreach.mark(A, opened["outreach_id"], "sent", self.path)
        self.assertNotIn("1", [i["business_id"] for i in self.today()["items"]])
        with db.connection(self.path) as conn:
            conn.execute("UPDATE outreach SET resolved_at=?, opened_at=?",
                         (outreach._iso(outreach._now() - timedelta(days=3, minutes=1)),) * 2)
        item = self.today()["items"][0]
        self.assertEqual((item["business_id"], item["stage"], item["template"]), ("1", "followup", "site.reminder"))
        follow = outreach.prepare(A, "1", "followup", item["contacts"][0]["contact"], "Напоминаю", self.path)
        outreach.mark(A, follow["outreach_id"], "sent", self.path)
        self.skip_pause()
        self.assertEqual(self.today()["items"], [])

    def test_same_text_to_same_contact_is_refused(self):
        self.biz("1"), self.biz("2", phones=("+7 900 123-45-61",))
        opened = self.open_chat("1", text="Один текст")
        outreach.mark(A, opened["outreach_id"], "sent", self.path)
        self.skip_pause()
        with self.assertRaisesRegex(ValueError, "уже отправляли"):
            self.open_chat("2", text="Один текст")

    def test_consent_mode_requires_permission(self):
        self.biz("1")
        outreach.save_settings({"mode": "consent"}, self.path)
        with self.assertRaisesRegex(ValueError, "согласием"):
            self.open_chat("1")

    def test_pin_adds_business_first_and_ack(self):
        self.biz("1", rating=5), self.biz("2", "unavailable", rating=1)
        outreach.add("2", self.path)
        self.assertEqual(self.today()["items"][0]["business_id"], "2")
        self.assertTrue(self.today()["items"][0]["pinned"])
        self.assertFalse(self.today()["acknowledged"])
        outreach.acknowledge(A, self.path)
        self.assertTrue(self.today()["acknowledged"])
        self.assertFalse(self.today(B)["acknowledged"])

    def test_contacted_list_for_status_changes(self):
        self.biz("1"), self.biz("2"), self.biz("3")
        opened = self.open_chat("1")
        outreach.mark(A, opened["outreach_id"], "sent", self.path)
        db.set_business("2", "working", None, self.path)
        rows = outreach.contacted(path=self.path)
        self.assertEqual({r["id"] for r in rows}, {"1", "2"})
        first = next(r for r in rows if r["id"] == "1")
        self.assertEqual(first["chat_url"], "https://t.me/+79001234561")
        self.assertTrue(first["written_at"])

    def test_day_boundary_is_moscow(self):
        with patch.object(outreach, "_now", return_value=outreach._parse("2026-10-21T20:30:00+00:00")):
            self.assertEqual(outreach._iso(outreach._day_start(outreach._now())), "2026-10-20T21:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
