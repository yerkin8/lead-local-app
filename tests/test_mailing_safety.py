"""Mailing defects found by the independent review (REVIEW.md, evidence/mailing.md), each reproduced on
FakeGateway: repeats twice, replies after a repeat was made, live repeat of a dry run, two campaigns at
once, a stuck «sending» after a stop-list entry, replies that came while the app was closed, the window
for test campaigns and the backup of mailing.sqlite3. Nothing is sent to Telegram."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.mailing import gateway as gw
from app.mailing.policy import when_text
from app.mailing.service import AUTO_REMINDER
from app.mailing.store import SCHEMA_VERSION, SchemaTooNew, Store, iso
from tests.test_mailing_batches import BatchCase
from tests.test_mailing_core import PHONE_B, TEMPLATE, FakeClock, MailingCase

GO = "ОТПРАВИТЬ"


def reminders_to(fake, user_id):
    return [item for item in fake.sent_to(user_id) if item.text.startswith("Напоминаю")]


class RepeatTwiceTests(BatchCase):
    """E-M-10: a stage reaches an organization at most once per batch."""

    def test_second_live_repeat_has_nobody_to_include(self):
        first = self.first_batch(("b1", "b2"))
        self.service.repeat(first["id"], AUTO_REMINDER, live=True, confirm=GO)
        with self.assertRaises(ValueError):
            self.service.repeat(first["id"], AUTO_REMINDER, live=True, confirm=GO)
        reasons = {x["business_id"]: x["reason"] for x in self.service.repeat_preview(first["id"])["skip"]}
        self.assertEqual(reasons, {"b1": "Напоминание уже получал", "b2": "Напоминание уже получал"})
        for business_id in ("b1", "b2"):
            self.assertEqual(len(self.fake.sent_to(self.user_id(business_id))), 2)  # first + one reminder

    def test_two_repeats_prepared_together_send_one_reminder(self):
        first = self.first_batch(("b1", "b2"))
        one = self.service.repeat(first["id"])            # dry
        two = self.service.repeat(first["id"])            # dry, prepared before any went live
        self.service.enable_live(one["id"], GO)
        self.service.enable_live(two["id"], GO)
        self.run_live(one["id"])
        detail = self.run_live(two["id"])
        self.assertEqual(detail["counts"]["sent"], 0)
        self.assertTrue(all(r["reason"] == "Напоминание этой организации уже отправлено" for r in detail["recipients"]))
        for business_id in ("b1", "b2"):
            self.assertEqual(len(reminders_to(self.fake, self.user_id(business_id))), 1)

    def test_reminder_of_a_reminder_is_not_sent(self):
        first = self.first_batch(("b1",))
        reminder = self.service.repeat(first["id"], AUTO_REMINDER, live=True, confirm=GO)
        with self.assertRaises(ValueError):
            self.service.repeat(reminder["id"], AUTO_REMINDER, live=True, confirm=GO)
        self.assertEqual(len(self.fake.sent_to(self.user_id("b1"))), 2)


class ReplyAfterRepeatTests(BatchCase):
    """E-M-11: replies and lead statuses are read before every repeat message, not only at creation."""

    def test_reply_after_the_repeat_was_made_stops_the_reminder(self):
        first = self.first_batch(("b1", "b2", "b3"))
        repeat = self.service.repeat(first["id"])
        self.service.enable_live(repeat["id"], GO)
        asyncio.run(self.fake.reply(self.account_id, self.user_id("b1"), "Интересно, позвоните"))
        self.orgs[1]["status"] = "working"                  # b2 «В работе» set by the owner meanwhile
        detail = self.run_live(repeat["id"])
        self.assertEqual(detail["counts"]["sent"], 1)
        rows = {r["business_id"]: r["reason"] for r in detail["recipients"]}
        self.assertEqual(rows["b1"], "Ответил в Telegram")
        self.assertEqual(rows["b2"], "В «Лидах»: В работе")
        self.assertEqual(len(self.fake.sent_to(self.user_id("b1"))), 1)


class DryRepeatGoLiveTests(BatchCase):
    """E-M-13: a dry repeat of a dry run can never become live."""

    def test_enable_live_on_a_dry_repeat_of_a_dry_run_is_refused(self):
        dry = self.service.create_campaign("Сухая", {"ids": ["b1", "b2"]}, TEMPLATE, [self.account_id])
        self.service.dry_run(dry["id"])
        repeat = self.service.repeat(dry["id"])
        with self.assertRaises(ValueError):
            self.service.enable_live(repeat["id"], GO)
        self.assertEqual(self.fake.delivered, [])

    def test_engine_refuses_a_repeat_to_someone_who_did_not_get_the_first(self):
        first = self.first_batch(("b1",))
        repeat = self.service.repeat(first["id"])
        self.service.enable_live(repeat["id"], GO)
        with self.service.store.tx() as conn:   # forged row: b2 never got the first message
            conn.execute("INSERT INTO recipients (campaign_id,business_id,business_name,account_id,username,phones,"
                         "dedupe_key,message,tz,status,position) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (repeat["id"], "b2", "Организация 2", self.account_id, "org_user_2", "", "x", "Напоминаю",
                          "Europe/Moscow", "queued", 9))
        detail = self.run_live(repeat["id"])
        rows = {r["business_id"]: r for r in detail["recipients"]}
        self.assertEqual(rows["b2"]["reason"], "Предыдущее сообщение пачки не получал")
        self.assertEqual(self.fake.sent_to(self.user_id("b2")), [])


class ParallelCampaignTests(MailingCase):
    """E-M-08/E-M-09: two campaigns at once never write twice and never skip the pause."""

    def setUp(self):
        super().setUp()
        self.fake.send_delay = 0.05

    def run_together(self, *ids):
        for campaign_id in ids:
            self.service.enable_live(campaign_id, GO)
            self.service.mark_running(campaign_id)

        async def both():
            await asyncio.gather(*(self.service.engine.run_campaign(cid) for cid in ids))
        asyncio.run(both())

    def test_same_leads_in_two_campaigns_get_one_message(self):
        account = self.account()
        ids = [f"b{i}" for i in range(1, 7)]
        one = self.service.create_campaign("Один", {"ids": ids}, TEMPLATE, [account])
        two = self.service.create_campaign("Два", {"ids": ids}, TEMPLATE, [account])
        self.run_together(one["id"], two["id"])
        users = [item.user_id for item in self.fake.delivered]
        self.assertEqual(len(users), 6)
        self.assertEqual(len(set(users)), 6)

    def test_same_leads_on_two_accounts_get_one_message(self):
        first = self.account("main-1")
        second = self.account("main-2", PHONE_B)
        ids = [f"b{i}" for i in range(1, 5)]
        one = self.service.create_campaign("Один", {"ids": ids}, TEMPLATE, [first])
        two = self.service.create_campaign("Два", {"ids": ids}, TEMPLATE, [second])
        self.run_together(one["id"], two["id"])
        users = [item.user_id for item in self.fake.delivered]
        self.assertEqual(sorted(users), sorted(set(users)))
        self.assertEqual(len(users), 4)

    def test_pause_between_messages_holds_for_two_campaigns(self):
        account = self.account()
        one = self.service.create_campaign("Один", {"ids": ["b1", "b2", "b3", "b4"]}, TEMPLATE, [account])
        two = self.service.create_campaign("Два", {"ids": ["b5", "b6", "b7", "b8"]}, TEMPLATE, [account])
        self.run_together(one["id"], two["id"])
        dates = sorted(item.date for item in self.fake.delivered)
        self.assertEqual(len(dates), 8)
        gaps = [(b - a).total_seconds() for a, b in zip(dates, dates[1:])]
        self.assertGreaterEqual(min(gaps), self.service.policy().delay_min)


class StuckSendingTests(MailingCase):
    """E-M-07: a message stuck in «sending» is checked against the stop-list before a new try."""

    def test_stuck_sending_is_not_sent_after_the_business_went_to_the_stop_list(self):
        account = self.account()
        campaign = self.service.create_campaign("Т", {"ids": ["b1", "b2"]}, TEMPLATE, [account])
        self.service.enable_live(campaign["id"], GO)
        rows = {r["business_id"]: r for r in self.service.campaign(campaign["id"])["recipients"]}
        with self.service.store.tx() as conn:   # the app died during the send of b1, nothing was delivered
            conn.execute("INSERT INTO attempts (recipient_id,account_id,random_id,mode,status,started_at) "
                         "VALUES (?,?,?,?,?,?)", (rows["b1"]["id"], account, 123, "live", "sending", iso(self.clock())))
            conn.execute("UPDATE recipients SET status='sending' WHERE id=?", (rows["b1"]["id"],))
        self.service.stop_add("business", "b1")
        self.service.mark_running(campaign["id"])
        asyncio.run(self.service.engine.run_campaign(campaign["id"]))
        detail = {r["business_id"]: r for r in self.service.campaign(campaign["id"])["recipients"]}
        self.assertEqual(detail["b1"]["status"], "skipped")
        self.assertEqual(detail["b2"]["status"], "sent")
        self.assertEqual(self.fake.sent_to(self.fake.users["org_user_1"].user_id), [])


class RepliesWhileClosedTests(BatchCase):
    """E-M-14: a reply or «СТОП» that came while the app was closed is read on the next connection."""

    def restart(self):
        service = self.make_service()
        service._lead_events = lambda kind, data: self.events.append((kind, dict(data)))
        service._templates = self.service._templates
        return service

    def test_stop_received_while_closed_reaches_the_stop_list_and_leads(self):
        first = self.first_batch(("b1", "b2"))
        self.fake.reply_while_closed(self.account_id, self.user_id("b1"), "СТОП")
        self.fake.reply_while_closed(self.account_id, self.user_id("b2"), "Здравствуйте, сколько стоит?")
        service = self.restart()
        self.assertEqual(asyncio.run(service.engine.sync_replies()), 1)
        self.assertIn("b1", service.store.stop_set()["business"])
        self.assertIn(("stop", {"business_id": "b1"}), self.events)
        self.assertIn(("replied", {"business_id": "b2"}), self.events)
        plan = service.repeat_preview(first["id"])
        self.assertEqual(plan["include"], [])
        # The same messages are not taken again on the next start.
        self.events.clear()
        asyncio.run(self.restart().engine.sync_replies())
        self.assertEqual(self.events, [])

    def test_catch_up_runs_when_a_campaign_connects(self):
        self.first_batch(("b1",))
        self.fake.reply_while_closed(self.account_id, self.user_id("b1"), "не пишите больше")
        service = self.restart()
        campaign = service.create_campaign("Другая", {"ids": ["b3"]}, TEMPLATE, [self.account_id])
        service.enable_live(campaign["id"], GO)
        service.mark_running(campaign["id"])
        asyncio.run(service.engine.run_campaign(campaign["id"]))
        self.assertIn("b1", service.store.stop_set()["business"])


class TestCampaignWindowTests(MailingCase):
    """E-M-04: test campaigns follow the 10–19 window in Moscow time and weekdays; «Без ожидания» skips it."""

    def setUp(self):
        super().setUp()
        self.clock.now = datetime(2026, 11, 7, 20, 30, tzinfo=timezone.utc)   # Saturday 23:30 Moscow
        self.owner = self.fake.add_user("owner_second")
        self.service.set_test_recipients(["@owner_second"])
        self.test_account = self.account("test", is_test=True)

    def test_saturday_night_test_waits_for_monday_ten(self):
        campaign = self.service.create_campaign("Т", {}, TEMPLATE, [self.test_account], kind="test", limit=2)
        self.live(campaign["id"])
        first = min(item.date for item in self.fake.delivered)
        self.assertEqual(first, datetime(2026, 11, 9, 7, 0, tzinfo=timezone.utc))   # Monday 10:00 Moscow

    def test_dry_run_shows_moscow_time(self):
        campaign = self.service.create_campaign("Т", {}, TEMPLATE, [self.test_account], kind="test", limit=1)
        detail = self.service.dry_run(campaign["id"])
        self.assertRegex(detail["recipients"][0]["reason"], r"^Уйдёт \d\d\.\d\d \d\d:\d\d МСК$")

    def test_no_wait_sends_now_but_keeps_limits(self):
        campaign = self.service.create_campaign("Т", {}, TEMPLATE, [self.test_account], kind="test", limit=2,
                                                no_wait=True)
        self.live(campaign["id"])
        self.assertEqual(len(self.fake.delivered), 2)
        self.assertLess(max(item.date for item in self.fake.delivered) - self.clock.now, timedelta(minutes=1))
        with self.assertRaises(ValueError):
            self.service.create_campaign("Т", {}, TEMPLATE, [self.test_account], no_wait=True)


class WhenTextTests(MailingCase):
    def test_two_times(self):
        moment = datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)
        self.assertEqual(when_text(moment, "Asia/Yekaterinburg", "Тюмень"), "02.11 10:00 по Тюмени (08:00 МСК)")
        self.assertEqual(when_text(moment, "Asia/Novosibirsk", "Новосибирск"),
                         "02.11 12:00 по Новосибирску (08:00 МСК)")
        self.assertEqual(when_text(moment, "Europe/Moscow", "Москва"), "02.11 08:00 МСК")
        self.assertEqual(when_text(moment, "Asia/Omsk", "Нижний Тагил"),
                         "02.11 11:00 по времени получателя (08:00 МСК)")

    def test_dry_run_reason_names_the_city(self):
        self.orgs[0]["city"] = "Тюмень"
        account = self.account()
        campaign = self.service.create_campaign("Т", {"ids": ["b1"]}, TEMPLATE, [account])
        detail = self.service.dry_run(campaign["id"])
        # The dry run counts from the real clock: only the shape is fixed here.
        self.assertRegex(detail["recipients"][0]["reason"], r"^Уйдёт \d\d\.\d\d \d\d:\d\d по Тюмени \(\d\d:\d\d МСК\)$")


class MailingBaseBackupTests(MailingCase):
    """F-O-07/08: the stop-list base is copied before a schema change and refused when it is newer."""

    def old_base(self, version: int) -> Path:
        path = self.data / "old" / "mailing" / "mailing.sqlite3"
        path.parent.mkdir(parents=True)
        with sqlite3.connect(path) as conn:
            conn.executescript("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
                               "CREATE TABLE campaigns (id INTEGER PRIMARY KEY, title TEXT NOT NULL, kind TEXT, "
                               "mode TEXT, status TEXT, template TEXT, filters_json TEXT, stop_reason TEXT, "
                               "created_at TEXT, updated_at TEXT, started_at TEXT, finished_at TEXT);"
                               "CREATE TABLE stoplist (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, value TEXT, "
                               "label TEXT, source TEXT, created_at TEXT, UNIQUE(kind,value));")
            conn.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(version),))
            conn.execute("INSERT INTO stoplist (kind,value,label,source,created_at) VALUES "
                         "('business','b1','','manual','2026-10-01')")
        conn.close()
        return path

    def test_backup_before_migration(self):
        path = self.old_base(1)
        store = Store(path)
        backups = list((path.parent.parent / "backups").glob("mailing-pre-schema-v*.sqlite3"))
        self.assertEqual(len(backups), 1)
        with sqlite3.connect(backups[0]) as copy:
            self.assertEqual(copy.execute("SELECT value FROM stoplist").fetchone()[0], "b1")
        copy.close()
        self.assertEqual(store.value("SELECT value FROM meta WHERE key='schema_version'"), str(SCHEMA_VERSION))
        Store(path)   # second open: nothing to migrate, no new copy
        self.assertEqual(len(list((path.parent.parent / "backups").glob("mailing-*.sqlite3"))), 1)

    def test_newer_base_is_refused_untouched(self):
        path = self.old_base(SCHEMA_VERSION + 1)
        before = path.read_bytes()
        with self.assertRaises(SchemaTooNew):
            Store(path)
        self.assertEqual(path.read_bytes(), before)

    def test_manual_backup_includes_the_mailing_base(self):
        from app import desktop_services
        data = self.data / "manual"
        (data / "mailing").mkdir(parents=True)
        with sqlite3.connect(data / "app.sqlite3") as conn:
            conn.execute("CREATE TABLE t (x)")
        conn.close()
        Store(data / "mailing" / "mailing.sqlite3").stop_add("business", "b9")
        old = desktop_services.DATA_DIR, desktop_services.DB_PATH
        desktop_services.DATA_DIR, desktop_services.DB_PATH = data, data / "app.sqlite3"
        try:
            target = Path(desktop_services.backup_database())
        finally:
            desktop_services.DATA_DIR, desktop_services.DB_PATH = old
        copy = target.with_name(target.stem + "-mailing.sqlite3")
        with sqlite3.connect(copy) as conn:
            self.assertEqual(conn.execute("SELECT value FROM stoplist").fetchone()[0], "b9")
        conn.close()


if __name__ == "__main__":
    import unittest
    unittest.main()
