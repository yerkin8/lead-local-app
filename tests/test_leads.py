import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app import db, leads, outreach

A = "tg:1"


class LeadsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.tmp.name) / "l.sqlite3"
        db.init_desktop_db(self.path)
        jobs = db.create_desktop_parse_jobs({"country": "Россия", "niche": "кафе", "count": 9, "types": ["no_site"],
                                             "review_limit": 0}, ["Казань"], self.path)
        self.run = jobs[0]["parse_run_id"] if "parse_run_id" in jobs[0] else db.claim_job(self.path)["params"]["_desktop_parse_run_id"]
        self.job = db.create_job("search", {"niche": "кафе"}, self.path)
        outreach.save_settings({"delay_seconds": 30}, self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def biz(self, ident, site_type="no_site", phones=None, socials=(), rating=4.5, run=True):
        phones = ["+7 900 123-45-6" + ident] if phones is None else phones
        db.upsert_business({"id": ident, "name": "Кафе " + ident, "site_type": site_type, "address": "Казань",
                            "phones": phones, "socials": list(socials), "rating": rating, "reviews": 10,
                            "map_url": f"https://yandex.ru/maps/org/x/{ident}/"},
                           ("кафе", "Россия", "Казань"), self.job, self.path,
                           parse_run_id=self.run if run else None)

    def test_feed_segments_order_and_contact(self):
        self.biz("1", "unavailable"), self.biz("2", "no_site", rating=4.0), self.biz("3", "no_site", rating=4.9)
        self.biz("4", phones=[], socials=[{"url": "https://t.me/cafe_four"}])
        db.set_business("2", "waiting", None, self.path)
        feed = leads.list_leads({"segment": "new"}, self.path)
        self.assertEqual([i["business_id"] if "business_id" in i else i["id"] for i in feed["items"]], ["3", "4", "1"])
        self.assertEqual(next(i for i in feed["items"] if i["id"] == "4")["contact"], "@cafe_four")
        self.assertEqual(feed["counts"]["new"], 3)
        self.assertEqual(feed["counts"]["written"], 1)
        self.assertEqual(leads.list_leads({"segment": "new", "q": "Кафе 1"}, self.path)["items"][0]["id"], "1")
        self.assertEqual(len(leads.list_leads({"segment": "all", "run": self.run}, self.path)["items"]), 4)

    def test_filters_list_niches_cities_and_searches(self):
        self.biz("1")
        f = leads.filters(self.path)
        self.assertEqual((f["niches"], f["cities"]), (["кафе"], ["Казань"]))
        self.assertEqual([(r["id"], r["niche"], r["city"]) for r in f["runs"]], [(self.run, "кафе", "Казань")])

    def test_card_has_everything_to_decide_and_a_ready_text(self):
        self.biz("1", "weak_site")
        card = leads.card(A, "1", self.path)
        self.assertEqual(card["map_url"], "https://yandex.ru/maps/org/x/1/")
        self.assertEqual(card["contacts"][0]["contact"], "+79001234561")
        self.assertEqual(card["template"], "site.first.redesign")
        self.assertNotRegex(card["text"], r"\[")
        self.assertIn("Кафе 1", card["text"])
        self.assertTrue(card["can_write"])

    def test_one_tap_write_then_auto_sent_and_correction(self):
        self.biz("1"), self.biz("2")
        first = leads.write(A, "1", path=self.path)
        url = urlparse(first["url"])
        self.assertEqual(url.netloc + url.path, "t.me/+79001234561")
        self.assertIn("СТОП", parse_qs(url.query)["text"][0])
        leads.mark(A, first["outreach_id"], "sent", self.path)
        self.assertEqual(db.business("1", self.path)["status"], "waiting")
        leads.correct(A, first["outreach_id"], "not_sent", self.path)
        self.assertEqual(db.business("1", self.path)["status"], "not_started")
        self.assertEqual(leads.list_leads({"segment": "new"}, self.path)["counts"]["new"], 2)

    def test_forgotten_open_chat_is_closed_when_writing_next(self):
        self.biz("1"), self.biz("2")
        first = leads.write(A, "1", path=self.path)
        with db.connection(self.path) as conn:
            conn.execute("UPDATE outreach SET opened_at=?", (outreach._iso(outreach._now() - timedelta(minutes=5)),))
        leads.write(A, "2", path=self.path)
        with db.connection(self.path) as conn:
            self.assertEqual(conn.execute("SELECT status FROM outreach WHERE id=?", (first["outreach_id"],)).fetchone()[0], "sent")

    def test_no_contact_lead_cannot_write(self):
        self.biz("1", phones=["+7 843 200-10-20"])
        card = leads.card(A, "1", self.path)
        self.assertFalse(card["can_write"])
        with self.assertRaisesRegex(ValueError, "нет телефона"):
            leads.write(A, "1", path=self.path)

    def test_soft_delete_and_undo_everywhere(self):
        self.biz("1"), self.biz("2")
        leads.hide(A, ["1"], self.path)
        self.assertEqual([i["id"] for i in leads.list_leads({"segment": "all"}, self.path)["items"]], ["2"])
        self.assertNotIn("1", [b["id"] for b in db.list_businesses({}, self.path)])
        self.assertNotIn("1", [i["business_id"] for i in outreach.today(A, path=self.path)["items"]])
        self.assertNotIn("1", [b["id"] for b in db.parse_run_businesses(self.run, self.path)])
        self.assertIsNotNone(db.business("1", self.path))  # data is kept
        leads.restore(["1"], self.path)
        self.assertEqual(len(leads.list_leads({"segment": "all"}, self.path)["items"]), 2)

    def test_remind_segment_after_three_days(self):
        self.biz("1")
        opened = leads.write(A, "1", path=self.path)
        leads.mark(A, opened["outreach_id"], "sent", self.path)
        self.assertEqual(leads.counts({}, self.path)["remind"], 0)
        with db.connection(self.path) as conn:
            conn.execute("UPDATE outreach SET resolved_at=?, opened_at=?",
                         (outreach._iso(outreach._now() - timedelta(days=4)),) * 2)
        self.assertEqual(leads.counts({}, self.path)["remind"], 1)
        card = leads.card(A, "1", self.path)
        self.assertEqual((card["stage"], card["template"]), ("followup", "site.reminder"))

    def test_remind_after_manual_written_status(self):
        self.biz("1"), self.biz("2")
        leads.set_status("1", "waiting", self.path)          # «Написал» set by hand, no «Написать» button
        leads.set_status("2", "waiting", self.path)
        self.assertEqual(leads.counts({}, self.path)["remind"], 0)
        with db.connection(self.path) as conn:
            conn.execute("UPDATE status_events SET changed_at=? WHERE business_id='1'",
                         (outreach._iso(outreach._now() - timedelta(days=4)),))
        self.assertEqual([i["id"] for i in leads.list_leads({"segment": "remind"}, self.path)["items"]], ["1"])
        card = leads.card(A, "1", self.path)
        self.assertEqual((card["stage"], card["template"]), ("followup", "site.reminder"))
        reminder = leads.write(A, "1", path=self.path)
        leads.mark(A, reminder["outreach_id"], "sent", self.path)
        self.assertEqual(leads.counts({}, self.path)["remind"], 0)  # reminded once, not again

    def test_search_by_phone_digits_in_any_format(self):
        self.biz("1", phones=["+7 (900) 123-45-67"]), self.biz("2", phones=["+7 900 765-43-21"])
        for q in ("9001234567", "89001234567", "+7 900 123 45 67", "123-45-67"):
            self.assertEqual([i["id"] for i in leads.list_leads({"segment": "all", "q": q}, self.path)["items"]], ["1"], q)

    def test_summary_keeps_written_today_apart_from_link_budget(self):
        self.biz("1"), self.biz("2")
        leads.set_status("1", "waiting", self.path)
        opened = leads.write(A, "2", path=self.path)
        leads.mark(A, opened["outreach_id"], "sent", self.path)
        s = leads.summary(A, self.path)
        self.assertEqual((s["written_today"], s["budget"]["used"]), (2, 1))

    def test_bulk_status_and_summary(self):
        self.biz("1"), self.biz("2")
        self.assertEqual(leads.bulk_status(["1", "2"], "unsuitable", self.path), 2)
        self.assertEqual(leads.summary(A, self.path)["new"], 0)

    def test_written_today_counts_manual_status_changes_by_any_admin(self):
        for ident in "12345":
            self.biz(ident)
        leads.set_status("1", "waiting", self.path)          # marked by hand, no «Написать» button
        leads.set_status("2", "waiting", self.path)
        opened = leads.write("tg:2", "3", path=self.path)     # another admin used the button
        leads.mark("tg:2", opened["outreach_id"], "sent", self.path)  # the app confirms on return
        leads.set_status("4", "waiting", self.path)
        leads.set_status("4", "not_started", self.path)      # undone: does not count
        leads.set_status("5", "waiting", self.path)
        leads.hide(A, ["5"], self.path)                      # deleted: does not count
        self.assertEqual(leads.summary(A, self.path)["written_today"], 3)

    def test_stats_leave_out_deleted_leads(self):
        for ident in "123":
            self.biz(ident)
        leads.set_status("1", "waiting", self.path)
        leads.hide(A, ["2", "3"], self.path)
        stats = db.stats(self.path)
        self.assertEqual((stats["total"], stats["contacted"]), (1, 1))
        self.assertEqual(stats["statuses"]["not_started"], 0)


if __name__ == "__main__":
    unittest.main()
