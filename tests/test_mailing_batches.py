"""Batches after a mailing: result per business, «Повторить пачку» (reminder), «Убрать из пачки»,
default exclusions and the link to lead statuses in «Лиды». FakeGateway only, nothing is sent."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.mailing.service import AUTO_REMINDER
from tests.test_mailing_core import TEMPLATE, MailingCase

REMINDER = "Напоминаю о себе, {название}"


class BatchCase(MailingCase):
    def setUp(self):
        super().setUp()
        self.events: list[tuple[str, dict]] = []
        self.service = self.make_service()
        self.service._lead_events = lambda kind, data: self.events.append((kind, dict(data)))
        self.service._templates = lambda: [{"slug": "site.first", "title": "Первое", "body": "Текст {название}"},
                                           {"slug": "site.reminder", "title": "Напоминание", "body": REMINDER}]
        self.account_id = self.account()
        self.service.run = self.run_live   # the engine thread is not started in unit tests

    def first_batch(self, ids=("b1", "b2", "b3", "b4", "b5", "b6")):
        campaign = self.service.create_campaign("Кафе · Москва", {"ids": list(ids)}, TEMPLATE, [self.account_id])
        return self.live(campaign["id"])

    def run_live(self, campaign_id):
        self.service.mark_running(campaign_id)
        asyncio.run(self.service.engine.run_campaign(campaign_id))
        return self.service.campaign(campaign_id)

    def user_id(self, business_id):
        index = int(business_id[1:])
        return self.fake.users[f"org_user_{index}"].user_id


class RepeatTests(BatchCase):
    def test_preview_excludes_replied_talking_stopped_failed_and_removed(self):
        first = self.first_batch()
        self.assertEqual(first["counts"]["sent"], 6)
        asyncio.run(self.fake.reply(self.account_id, self.user_id("b1"), "Здравствуйте, интересно"))
        self.orgs[2]["status"] = "working"                      # b3 «В работе» in «Лиды»
        self.service.stop_add("username", "org_user_4")           # b4 in the stop-list
        self.legacy_blocked.add("b5")                             # b5 «Не писать»
        self.service.exclude(first["id"], "b6")                   # b6 removed by the owner
        plan = self.service.repeat_preview(first["id"])
        self.assertEqual([x["business_id"] for x in plan["include"]], ["b2"])
        reasons = {x["business_id"]: x["reason"] for x in plan["skip"]}
        self.assertEqual(reasons, {"b1": "Ответил в Telegram", "b3": "В «Лидах»: В работе",
                                   "b4": "В стоп-листе или «Не писать»", "b5": "В стоп-листе или «Не писать»",
                                   "b6": "Убран из пачки"})

    def test_dry_repeat_uses_reminder_text_and_stays_in_batch(self):
        first = self.first_batch(("b1", "b2"))
        repeat = self.service.repeat(first["id"])                # default: reminder, dry run
        self.assertEqual(repeat["mode"], "dry")
        self.assertEqual(repeat["counts"]["dry_sent"], 2)
        self.assertEqual((repeat["batch_id"], repeat["parent_id"], repeat["stage"]), (first["id"], first["id"], "reminder"))
        self.assertTrue(all(r["message"].startswith("Напоминаю о себе, Организация") for r in repeat["recipients"]))
        self.assertEqual([c["id"] for c in self.service.campaign(first["id"])["batch"]], [first["id"], repeat["id"]])
        self.assertEqual(len(self.fake.delivered), 2)            # only the first messages

    def test_live_repeat_sends_reminder_to_those_who_got_the_first_message(self):
        first = self.first_batch(("b1", "b2", "b3"))
        self.service.exclude(first["id"], "b3")
        with self.assertRaises(ValueError):
            self.service.repeat(first["id"], AUTO_REMINDER, live=True, confirm="да")
        detail = self.service.repeat(first["id"], AUTO_REMINDER, live=True, confirm="ОТПРАВИТЬ")
        self.assertEqual(detail["counts"]["sent"], 2)
        reminders = [text for _, text in self.deliveries() if text.startswith("Напоминаю")]
        self.assertEqual(len(reminders), 2)
        self.assertNotIn(self.user_id("b3"), [user for user, text in self.deliveries() if text.startswith("Напоминаю")])

    def test_first_message_protection_stays_for_ordinary_campaigns(self):
        self.first_batch(("b1",))
        again = self.service.create_campaign("Ещё раз первое", {"ids": ["b1"]}, TEMPLATE, [self.account_id])
        detail = self.live(again["id"])
        self.assertEqual(detail["counts"]["sent"], 0)
        self.assertEqual(detail["recipients"][0]["reason"], "Этой организации уже писали")

    def test_live_repeat_of_a_dry_run_is_refused(self):
        dry = self.service.create_campaign("Сухая", {"ids": ["b1"]}, TEMPLATE, [self.account_id])
        self.service.dry_run(dry["id"])
        with self.assertRaises(ValueError):
            self.service.repeat(dry["id"], AUTO_REMINDER, live=True, confirm="ОТПРАВИТЬ")
        self.assertEqual(self.service.repeat(dry["id"])["counts"]["dry_sent"], 1)

    def test_emergency_stop_and_nobody_left(self):
        first = self.first_batch(("b1",))
        self.service.emergency_stop()
        with self.assertRaises(ValueError):
            self.service.repeat(first["id"], live=True, confirm="ОТПРАВИТЬ")
        self.service.clear_emergency()
        self.service.exclude(first["id"], "b1")
        with self.assertRaises(ValueError):
            self.service.repeat(first["id"])

    def test_exclude_withdraws_a_queued_message_and_include_undoes_it_for_later(self):
        first = self.first_batch(("b1", "b2"))
        repeat = self.service.repeat(first["id"])                # dry: done
        self.service.enable_live(repeat["id"], "ОТПРАВИТЬ")      # queued again, not started
        result = self.service.exclude(repeat["id"], "b2")
        self.assertEqual(result["withdrawn"], 1)
        rows = {r["business_id"]: r for r in self.service.campaign(repeat["id"])["recipients"]}
        self.assertEqual((rows["b2"]["status"], rows["b2"]["reason"]), ("skipped", "Убран из пачки"))
        self.assertTrue(rows["b2"]["excluded"])
        self.service.include(first["id"], "b2")
        self.assertIn("b2", [x["business_id"] for x in self.service.repeat_preview(first["id"])["include"]])

    def test_repeat_still_respects_stop_list_at_send_time(self):
        first = self.first_batch(("b1", "b2"))
        repeat = self.service.repeat(first["id"], live=False)
        self.service.enable_live(repeat["id"], "ОТПРАВИТЬ")
        self.service.stop_add("business", "b2")
        detail = self.run_live(repeat["id"])
        self.assertEqual(detail["counts"]["sent"], 1)


class LeadEventTests(BatchCase):
    def test_live_send_and_replies_reach_leads(self):
        first = self.first_batch(("b1", "b2"))
        sent = [data for kind, data in self.events if kind == "sent"]
        self.assertEqual([(x["business_id"], x["stage"]) for x in sent], [("b1", "first"), ("b2", "first")])
        self.assertTrue(sent[0]["message"].startswith("Здравствуйте"))
        self.service.repeat(first["id"], live=True, confirm="ОТПРАВИТЬ")
        self.assertEqual([x["stage"] for kind, x in self.events if kind == "sent"][2:], ["reminder", "reminder"])
        asyncio.run(self.fake.reply(self.account_id, self.user_id("b1"), "Расскажите подробнее"))
        asyncio.run(self.fake.reply(self.account_id, self.user_id("b2"), "Стоп"))
        self.assertIn(("replied", {"business_id": "b1"}), self.events)
        self.assertIn(("stop", {"business_id": "b2"}), self.events)

    def test_new_campaign_takes_its_leads_and_delete_gives_them_back(self):
        # N34: creating a first-message campaign marks its «Новые» leads at once (dry runs too); deleting it
        # before anything went out gives them back. Repeats do not take leads again.
        dry = self.service.create_campaign("Сухая", {"ids": ["b1", "b2"]}, TEMPLATE, [self.account_id])
        self.assertEqual(self.events, [("taken", {"ids": ["b1", "b2"]})])
        self.service.dry_run(dry["id"])
        self.service.delete_campaign(dry["id"])
        self.assertEqual(self.events[-1], ("released", {"ids": ["b1", "b2"]}))

    def test_only_new_leads_go_into_a_first_message(self):
        self.orgs[1]["status"] = "waiting"                           # b2 already «Написал»
        campaign = self.service.create_campaign("Т", {"ids": ["b1", "b2"]}, TEMPLATE, [self.account_id])
        rows = {r["business_id"]: r for r in campaign["recipients"]}
        self.assertEqual(rows["b2"]["reason"], "Уже не «Новый» в «Лидах»")
        self.assertEqual(rows["b1"]["status"], "queued")
        self.assertEqual(self.events, [("taken", {"ids": ["b1"]})])

    def test_repeat_and_test_campaigns_take_nothing(self):
        first = self.first_batch(("b1",))
        self.events.clear()
        self.service.repeat(first["id"])
        self.assertFalse([e for e in self.events if e[0] == "taken"])

    def test_campaigns_made_before_the_change_are_marked_once(self):
        self.service.create_campaign("Старая", {"ids": ["b3"]}, TEMPLATE, [self.account_id])
        self.events.clear()
        self.assertEqual(self.service.mark_taken_before_n34(), 1)
        self.assertEqual(self.events, [("taken", {"ids": ["b3"]})])
        self.assertEqual(self.service.mark_taken_before_n34(), 0)    # only once


class RecordMailingTests(unittest.TestCase):
    """outreach.record_mailing on a temporary app database: «Написал», reminder in «Сегодня»."""

    def setUp(self):
        from app import db
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "app.sqlite3"
        db.init_desktop_db(self.path)
        job = db.create_job("search", {}, self.path)
        for identity, status in (("11", "not_started"), ("12", "working"), ("13", "call")):
            db.upsert_business({"id": identity, "name": f"Кафе {identity}", "site_type": "no_site",
                                "address": "Москва", "map_url": f"https://yandex.ru/maps/org/x/{identity}/",
                                "socials": [{"url": f"https://t.me/cafe_{identity}", "label": "t.me"}]},
                               ("кафе", "Россия", "Москва"), job, self.path)
            if status != "not_started":
                db.set_business(identity, status, None, self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_sent_marks_written_and_reminder_appears_after_three_days(self):
        from app import db, outreach
        for identity in ("11", "12", "13"):
            outreach.record_mailing("sent", {"business_id": identity, "username": f"cafe_{identity}",
                                             "message": "Текст", "stage": "first"}, self.path)
        self.assertEqual([db.business(x, self.path)["status"] for x in ("11", "12", "13")],
                         ["waiting", "working", "waiting"])
        with db.connection(self.path) as conn:
            old = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat(timespec="seconds")
            conn.execute("UPDATE outreach SET resolved_at=?,opened_at=?", (old, old))
        reminders = [x["business_id"] for x in outreach.today("owner", path=self.path)["items"] if x["stage"] == "followup"]
        self.assertEqual(sorted(reminders), ["11", "13"])
        outreach.record_mailing("sent", {"business_id": "11", "username": "cafe_11", "message": "Напоминание",
                                         "stage": "reminder"}, self.path)
        reminders = [x["business_id"] for x in outreach.today("owner", path=self.path)["items"] if x["stage"] == "followup"]
        self.assertEqual(reminders, ["13"])       # a mailing reminder is not offered again by hand

    def test_reply_and_stop(self):
        from app import db, outreach
        outreach.record_mailing("sent", {"business_id": "11", "username": "cafe_11", "stage": "first"}, self.path)
        outreach.record_mailing("replied", {"business_id": "11"}, self.path)
        self.assertEqual(db.business("11", self.path)["status"], "replied")
        outreach.record_mailing("replied", {"business_id": "12"}, self.path)   # «В работе» is not downgraded
        self.assertEqual(db.business("12", self.path)["status"], "working")
        outreach.record_mailing("stop", {"business_id": "13"}, self.path)
        self.assertEqual(db.business("13", self.path)["status"], "unsuitable")
        self.assertIn("13", db.suppressed_ids(self.path))
        outreach.record_mailing("sent", {"business_id": "nope"}, self.path)    # unknown: ignored

    def test_taken_marks_only_new_and_released_gives_back_unless_written(self):
        from app import db, outreach
        outreach.record_mailing("taken", {"ids": ["11", "12", "13"]}, self.path)
        self.assertEqual([db.business(x, self.path)["status"] for x in ("11", "12", "13")],
                         ["waiting", "working", "call"])               # only «Новый» changes
        outreach.record_mailing("released", {"ids": ["11"]}, self.path)
        self.assertEqual(db.business("11", self.path)["status"], "not_started")
        outreach.record_mailing("taken", {"ids": ["11"]}, self.path)
        outreach.record_mailing("sent", {"business_id": "11", "username": "cafe_11", "stage": "first"}, self.path)
        outreach.record_mailing("released", {"ids": ["11"]}, self.path)   # really written: stays «Написал»
        self.assertEqual(db.business("11", self.path)["status"], "waiting")


if __name__ == "__main__":
    unittest.main()
