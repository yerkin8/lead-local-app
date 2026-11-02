"""Mailing core on FakeGateway: idempotency, Telegram errors, limits, stop-list, emergency stop, secrets."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.domain import render_template
from app.mailing import gateway as gw
from app.mailing.engine import Engine
from app.mailing.fake_gateway import FakeGateway
from app.mailing.gateway import GatewayError
from app.mailing.logsafe import scrub
from app.mailing.policy import Policy, recipient_tz
from app.mailing.service import MailingService, normalize_target, username_from_business
from app.mailing.store import Store
from app.script_templates import FIRST_BY_SITE_TYPE

API_ID = 31415926
API_HASH = "0123456789abcdef0123456789abcdef"
PHONE_A = "+79991112233"
PHONE_B = "+79994445566"
TEMPLATE = "Здравствуйте! Пишу {название} из {город}: у вас {тип_сайта}."


class FakeClock:
    """Monday 2026-11-02 08:00 Moscow (05:00 UTC). sleep() moves time instantly."""

    def __init__(self, start=datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)):
        self.now = start

    def __call__(self):
        return self.now

    async def sleep(self, seconds):
        self.now += timedelta(seconds=max(seconds, 0.001))
        await asyncio.sleep(0)


def business(index, *, city="Москва", site_type="no_site", username=None, phone=None):
    username = username if username is not None else f"org_user_{index}"
    socials = [{"url": f"https://t.me/{username}", "label": "Telegram"}] if username else []
    return {"id": f"b{index}", "name": f"Организация {index}", "city": city, "country": "Россия",
            "niche": "кафе", "site_type": site_type, "status": "not_started", "socials": socials,
            "links": [], "phones": [phone or f"+7 900 000-00-{index:02d}"]}


class MailingCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.clock = FakeClock()
        self.fake = FakeGateway(clock=self.clock)
        self.orgs = [business(i) for i in range(1, 9)]
        for org in self.orgs:
            self.fake.add_user(org["socials"][0]["url"].rsplit("/", 1)[1])
        self.legacy_blocked: set[str] = set()
        self.service = self.make_service()

    def make_service(self, gateway=None):
        return MailingService(
            self.data, gateway or self.fake,
            businesses=lambda query, limit: [o for o in self.orgs if all(o.get(k) == v for k, v in query.items())][:limit],
            business=lambda identity: next((o for o in self.orgs if o["id"] == identity), None),
            templates=lambda: [{"slug": "first", "title": "Первое", "body": "Текст {название}"}],
            render=render_template, first_by_site_type=FIRST_BY_SITE_TYPE,
            external_blocked=lambda: self.legacy_blocked,
            engine_options={"clock": self.clock, "sleep": self.clock.sleep, "tick": 3600})

    def tearDown(self):
        self.tmp.cleanup()

    # helpers
    def account(self, label="main-1", phone=PHONE_A, is_test=False, service=None):
        service = service or self.service
        service.save_keys(API_ID, API_HASH)
        account_id = service.add_account(label, phone, is_test)["id"]
        service._save_session(account_id, "fake-session")
        with service.store.tx() as conn:
            conn.execute("UPDATE accounts SET status='active' WHERE id=?", (account_id,))
        return account_id

    def live(self, campaign_id, service=None):
        service = service or self.service
        service.enable_live(campaign_id, "ОТПРАВИТЬ")
        service.mark_running(campaign_id)
        asyncio.run(service.engine.run_campaign(campaign_id))
        return service.campaign(campaign_id)

    def deliveries(self):
        return [(item.user_id, item.text) for item in self.fake.delivered]


class DryRunTests(MailingCase):
    def test_dry_run_on_five_records_sends_nothing(self):
        account = self.account()
        campaign = self.service.create_campaign("Тест", {}, TEMPLATE, [account], limit=5)
        self.assertEqual(campaign["mode"], "dry")
        result = self.service.dry_run(campaign["id"])
        self.assertEqual(result["counts"]["dry_sent"], 5)
        self.assertEqual(result["counts"]["sent"], 0)
        self.assertEqual(self.fake.delivered, [])
        self.assertEqual([c for c in self.fake.calls if c[0] in ("send", "resolve")], [])
        self.assertTrue(all("Уйдёт" in r["reason"] for r in result["recipients"]))
        self.assertIn("Организация 1", result["recipients"][0]["message"])
        self.assertNotIn("СТОП", result["recipients"][0]["message"])   # no opt-out line (N30)

    def test_live_requires_confirm_word(self):
        account = self.account()
        campaign = self.service.create_campaign("Тест", {}, TEMPLATE, [account], limit=2)
        with self.assertRaises(ValueError):
            self.service.enable_live(campaign["id"], "да")
        with self.assertRaises(ValueError):
            self.service.mark_running(campaign["id"])

    def test_unfilled_brackets_block_campaign(self):
        account = self.account()
        with self.assertRaises(ValueError):
            self.service.create_campaign("Тест", {}, "Здравствуйте, [имя]!", [account])

    def test_preview_uses_three_real_records(self):
        preview = self.service.preview(TEMPLATE, {"site_type": "no_site"})
        self.assertEqual(len(preview["items"]), 3)
        self.assertEqual(preview["items"][0]["text"].split("\n")[0],
                         "Здравствуйте! Пишу Организация 1 из Москва: у вас нет сайта.")


class IdempotencyTests(MailingCase):
    def test_crash_after_delivery_then_restart_has_no_duplicates(self):
        account = self.account()
        campaign = self.service.create_campaign("Тест", {}, TEMPLATE, [account], limit=4)
        self.service.enable_live(campaign["id"], "ОТПРАВИТЬ")
        self.service.mark_running(campaign["id"])
        self.fake.crash_after_delivery = 2
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(self.service.engine.run_campaign(campaign["id"]))
        state = self.service.campaign(campaign["id"])
        self.assertEqual(state["status"], "running")
        self.assertEqual(state["counts"]["sending"], 1)
        # Restart: a new engine (new process) over the same database and Telegram.
        restarted = self.make_service()
        resumed = asyncio.run(restarted.engine.resume_running())
        self.assertEqual(resumed, [campaign["id"]])
        asyncio.run(restarted.engine.run_campaign(campaign["id"]))
        users = [user for user, _ in self.deliveries()]
        self.assertEqual(len(users), 4)
        self.assertEqual(len(set(users)), 4, "each recipient exactly once")
        final = restarted.campaign(campaign["id"])
        self.assertEqual(final["status"], "done")
        self.assertEqual(final["counts"]["sent"], 4)
        self.assertTrue(any(e["kind"] == "reconciled" for e in final["events"]))

    def test_lost_answer_reconciled_by_history(self):
        account = self.account()
        campaign = self.service.create_campaign("Тест", {}, TEMPLATE, [account], limit=2)
        self.fake.lose_reply_once = True
        result = self.live(campaign["id"])
        self.assertEqual(result["counts"]["sent"], 2)
        self.assertEqual(len(self.fake.delivered), 2)

    def test_random_id_duplicate_when_history_is_empty(self):
        account = self.account()
        campaign = self.service.create_campaign("Тест", {}, TEMPLATE, [account], limit=2)
        self.fake.lose_reply_once = True
        self.fake.history_broken = True
        result = self.live(campaign["id"])
        self.assertEqual(result["counts"]["sent"], 2)
        self.assertEqual(len(self.fake.delivered), 2)
        attempts = self.service.store.rows("SELECT server_response FROM attempts WHERE mode='live'")
        self.assertTrue(any("RANDOM_ID_DUPLICATE" in a["server_response"] for a in attempts))

    def test_rerun_of_finished_campaign_sends_nothing(self):
        account = self.account()
        campaign = self.service.create_campaign("Тест", {}, TEMPLATE, [account], limit=2)
        self.live(campaign["id"])
        asyncio.run(self.service.engine.run_campaign(campaign["id"]))
        self.assertEqual(len(self.fake.delivered), 2)


class TelegramErrorTests(MailingCase):
    def test_short_flood_wait_is_respected_then_continues(self):
        account = self.account()
        campaign = self.service.create_campaign("Тест", {}, TEMPLATE, [account], limit=2)
        first = self.fake.users["org_user_1"]
        self.fake.fail("send", first.user_id, GatewayError(gw.FLOOD_WAIT, "FLOOD_WAIT", 120))
        started = self.clock.now
        result = self.live(campaign["id"])
        self.assertEqual(result["counts"]["sent"], 2)
        flood = self.service.store.row("SELECT * FROM attempts WHERE error_kind='flood_wait'")
        self.assertEqual(flood["wait_seconds"], 120)
        retry = self.service.store.row("SELECT * FROM attempts WHERE status='sent' ORDER BY id LIMIT 1")
        self.assertGreaterEqual(datetime.fromisoformat(retry["started_at"]) - started, timedelta(seconds=120))
        self.assertEqual(len(self.fake.sent_to(first.user_id)), 1)

    def test_long_flood_wait_pauses_account_until_end_of_day(self):
        account = self.account()
        campaign = self.service.create_campaign("Тест", {}, TEMPLATE, [account], limit=1)
        self.fake.fail("send", "*", GatewayError(gw.FLOOD_WAIT, "FLOOD_WAIT", 5000))
        self.service.enable_live(campaign["id"], "ОТПРАВИТЬ")
        self.service.mark_running(campaign["id"])
        engine = self.service.engine

        async def run_until_paused():
            task = asyncio.ensure_future(engine.run_campaign(campaign["id"]))
            for _ in range(50):
                await asyncio.sleep(0)
                row = self.service.store.row("SELECT * FROM accounts WHERE id=?", (account,))
                if row["status"] == "paused":
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    return row
            task.cancel()
        row = asyncio.run(run_until_paused())
        self.assertEqual(row["status"], "paused")
        end_of_day = Policy().end_of_day(self.clock.now)
        self.assertGreaterEqual(datetime.fromisoformat(row["paused_until"]), end_of_day - timedelta(seconds=1))
        self.assertEqual(self.fake.delivered, [])

    def _quarantine_case(self, kind, where="send"):
        account_a = self.account("main-1", PHONE_A)
        account_b = self.account("main-2", PHONE_B)
        campaign_a = self.service.create_campaign("A", {}, TEMPLATE, [account_a], limit=3)
        self.orgs += [business(i) for i in range(20, 23)]
        for org in self.orgs[-3:]:
            self.fake.add_user(org["socials"][0]["url"].rsplit("/", 1)[1])
        self.fake.fail(where, "*" if where == "send" else str(account_a), GatewayError(kind, kind.upper()))
        result_a = self.live(campaign_a["id"])
        account_row = self.service.store.row("SELECT * FROM accounts WHERE id=?", (account_a,))
        self.assertEqual(account_row["status"], "quarantine")
        self.assertEqual(result_a["status"], "stopped")
        self.assertEqual(result_a["counts"]["sent"], 0)
        # Recipients stay on the quarantined account: traffic is not moved to another account.
        owners = {r["account_id"] for r in result_a["recipients"]}
        self.assertEqual(owners, {account_a})
        with self.assertRaises(ValueError):
            self.service.mark_running(campaign_a["id"])
        # Another account and its campaign are not affected.
        campaign_b = self.service.create_campaign("B", {"city": "Москва"}, TEMPLATE, [account_b], limit=8)
        result_b = self.live(campaign_b["id"])
        self.assertEqual(self.service.store.value("SELECT status FROM accounts WHERE id=?", (account_b,)), "active")
        self.assertGreater(result_b["counts"]["sent"], 0)
        self.assertTrue(all(item.account_id == account_b for item in self.fake.delivered))
        events = [e["kind"] for e in self.service.overview()["events"]]
        self.assertIn("quarantine", events)
        self.assertIn("campaign_stopped", events)

    def test_peer_flood_quarantines_account_only(self):
        self._quarantine_case(gw.PEER_FLOOD)

    def test_frozen_account_quarantined(self):
        self._quarantine_case(gw.ACCOUNT_FROZEN)

    def test_banned_on_connect_quarantined(self):
        self._quarantine_case(gw.ACCOUNT_BANNED, where="connect")

    def test_privacy_marks_recipient_and_never_retries(self):
        account = self.account()
        first = self.fake.users["org_user_1"]
        self.fake.fail("send", first.user_id, GatewayError(gw.PRIVACY, "USER_PRIVACY_RESTRICTED"))
        campaign = self.service.create_campaign("Тест", {}, TEMPLATE, [account], limit=2)
        result = self.live(campaign["id"])
        self.assertEqual(result["counts"]["unreachable"], 1)
        self.assertEqual(result["counts"]["sent"], 1)
        again = self.service.create_campaign("Снова", {}, TEMPLATE, [account], limit=3)
        result2 = self.live(again["id"])
        sends_to_first = [c for c in self.fake.calls if c[0] == "send" and c[2] == first.user_id]
        self.assertEqual(len(sends_to_first), 1)
        reasons = {r["business_id"]: r["reason"] for r in result2["recipients"]}
        self.assertIn("Ранее недоступен", reasons["b1"])
        self.assertIn("уже писали", reasons["b2"])

    def test_channel_username_is_unreachable(self):
        account = self.account()
        self.fake.users["org_user_1"] = gw.Peer(1, 1, "channel")
        campaign = self.service.create_campaign("Тест", {}, TEMPLATE, [account], limit=1)
        result = self.live(campaign["id"])
        self.assertEqual(result["counts"]["unreachable"], 1)
        self.assertEqual(self.fake.delivered, [])


class LimitTests(MailingCase):
    def test_new_account_daily_limit_pauses_and_window(self):
        account = self.account()
        campaign = self.service.create_campaign("Тест", {}, TEMPLATE, [account], limit=8)
        result = self.live(campaign["id"])
        self.assertEqual(result["counts"]["sent"], 8)
        times = [datetime.fromisoformat(r["started_at"]) for r in self.service.store.rows(
            "SELECT started_at FROM attempts WHERE mode='live' AND status='sent' ORDER BY id")]
        msk = timezone(timedelta(hours=3))
        by_day: dict = {}
        for moment in times:
            local = moment.astimezone(msk)
            by_day.setdefault(local.date(), 0)
            by_day[local.date()] += 1
            self.assertTrue(10 <= local.hour < 19, f"outside window: {local}")
            self.assertLess(local.weekday(), 5)
        days = sorted(by_day)
        self.assertEqual(by_day[days[0]], 5, "first day of a new account: 5")
        self.assertEqual(by_day[days[1]], 3)
        gaps = [(b - a).total_seconds() for a, b in zip(times, times[1:]) if a.date() == b.date()]
        self.assertTrue(all(gap >= 90 for gap in gaps), gaps)

    def test_hourly_cap(self):
        self.service.save_policy({"first_day_limit": 20, "daily_cap": 20, "hourly_cap": 2,
                                  "delay_min": 45, "delay_max": 45})
        account = self.account()
        campaign = self.service.create_campaign("Тест", {}, TEMPLATE, [account], limit=5)
        self.live(campaign["id"])
        times = [datetime.fromisoformat(r["started_at"]) for r in self.service.store.rows(
            "SELECT started_at FROM attempts WHERE mode='live' AND status='sent' ORDER BY id")]
        for index in range(len(times) - 2):
            self.assertGreaterEqual(times[index + 2] - times[index], timedelta(hours=1))

    def test_recipient_timezone_from_city(self):
        self.assertEqual(recipient_tz("Новосибирск", "Россия"), "Asia/Novosibirsk")
        self.assertEqual(recipient_tz("Неизвестный", "Казахстан"), "Asia/Almaty")
        self.assertEqual(recipient_tz(None, None), "Europe/Moscow")
        policy = Policy()
        # 05:00 UTC Monday = 12:00 in Novosibirsk (inside) and 08:00 in Moscow (outside).
        moment = datetime(2026, 11, 2, 5, 0, tzinfo=timezone.utc)
        self.assertEqual(policy.next_window_start(moment, "Asia/Novosibirsk"), moment)
        self.assertEqual(policy.next_window_start(moment, "Europe/Moscow"),
                         datetime(2026, 11, 2, 7, 0, tzinfo=timezone.utc))

    def test_policy_bounds(self):
        with self.assertRaises(ValueError):
            Policy.from_dict({"delay_min": 5})
        self.assertEqual(Policy().daily_limit(None, datetime(2026, 1, 1).date()), 5)
        start = datetime(2026, 1, 1).date()
        self.assertEqual(Policy().daily_limit(start, start + timedelta(days=2)), 11)
        self.assertEqual(Policy().daily_limit(start, start + timedelta(days=60)), 25)


class StopListTests(MailingCase):
    def test_stoplist_and_dedupe_at_creation(self):
        account = self.account()
        self.service.stop_add("business", "b1")
        self.service.stop_add("username", "@ORG_USER_2")
        self.service.stop_add("phone", "8 900 000-00-03")
        self.legacy_blocked.add("b4")  # «Не писать» from the old CRM table
        self.orgs[5]["socials"] = [{"url": "https://t.me/org_user_5"}]   # same username as b5
        self.orgs[6]["phones"] = ["+7 900 000-00-05"]                     # same phone as b5
        campaign = self.service.create_campaign("Тест", {}, TEMPLATE, [account], limit=8)
        reasons = {r["business_id"]: r["reason"] for r in campaign["recipients"]}
        self.assertEqual(reasons["b1"], "В стоп-листе")
        self.assertEqual(reasons["b2"], "В стоп-листе")
        self.assertEqual(reasons["b3"], "В стоп-листе")
        self.assertIn("контакт", reasons["b6"])
        self.assertIn("телефон", reasons["b7"])
        result = self.live(campaign["id"])
        sent = {r["business_id"] for r in result["recipients"] if r["status"] == "sent"}
        self.assertEqual(sent, {"b5", "b8"})
        self.assertEqual(result["counts"]["stoplist"], 4)
        self.assertEqual(len(self.fake.delivered), 2)

    def test_stop_added_after_creation_withdraws_queued(self):
        account = self.account()
        campaign = self.service.create_campaign("Тест", {}, TEMPLATE, [account], limit=3)
        self.service.stop_add("username", "org_user_3")
        result = self.live(campaign["id"])
        self.assertEqual(result["counts"]["sent"], 2)
        self.assertNotIn(self.fake.users["org_user_3"].user_id, [u for u, _ in self.deliveries()])

    def test_reply_stop_goes_to_stoplist(self):
        account = self.account()
        campaign = self.service.create_campaign("Тест", {}, TEMPLATE, [account], limit=1)
        self.live(campaign["id"])
        user = self.fake.users["org_user_1"].user_id
        asyncio.run(self.fake.reply(account, user, "Стоп, не пишите нам"))
        stop = self.service.store.stop_set()
        self.assertIn("b1", stop["business"])
        self.assertIn(str(user), stop["telegram_user"])
        self.assertEqual(self.service.campaign(campaign["id"])["counts"]["replied"], 1)
        again = self.service.create_campaign("Снова", {}, TEMPLATE, [account], limit=1)
        self.assertEqual(again["recipients"][0]["reason"], "В стоп-листе")


class TestContourTests(MailingCase):
    def test_test_account_never_writes_to_real_organizations(self):
        test_account = self.account("test", PHONE_A, is_test=True)
        campaign = self.service.create_campaign("Обычная", {}, TEMPLATE, [test_account], limit=3)
        result = self.live(campaign["id"])
        self.assertEqual(self.fake.delivered, [])
        self.assertTrue(all(r["reason"].startswith("Тестовый аккаунт") for r in result["recipients"]))

    def test_test_campaign_goes_only_to_test_recipients(self):
        owner = self.fake.add_user("owner_second")
        self.service.set_test_recipients(["@Owner_Second"])
        test_account = self.account("test", PHONE_A, is_test=True)
        main_account = self.account("main", PHONE_B)
        with self.assertRaises(ValueError):
            self.service.create_campaign("Т", {}, TEMPLATE, [main_account], kind="test")
        campaign = self.service.create_campaign("Т", {}, TEMPLATE, [test_account], kind="test", limit=3)
        result = self.live(campaign["id"])
        self.assertEqual(result["counts"]["sent"], 3)
        self.assertEqual({u for u, _ in self.deliveries()}, {owner.user_id})
        self.assertTrue(all(r["msg_id"] for r in result["recipients"]))


class EmergencyStopTests(MailingCase):
    def test_emergency_stop_within_five_seconds(self):
        fake = FakeGateway(send_delay=30)  # real clock: a send is "in flight" for 30 s
        for org in self.orgs:
            fake.add_user(org["socials"][0]["url"].rsplit("/", 1)[1])
        service = MailingService(
            self.data / "real", fake,
            businesses=lambda query, limit: self.orgs[:limit], business=lambda i: None,
            templates=lambda: [], render=render_template, first_by_site_type={},
            engine_options={"tick": 0.2})
        service.save_policy({"window_start": 0, "window_end": 24, "weekdays_only": False})
        service.start()
        try:
            account = self.account(service=service)
            campaign = service.create_campaign("Тест", {}, TEMPLATE, [account], limit=3)
            service.enable_live(campaign["id"], "ОТПРАВИТЬ")
            service.run(campaign["id"])
            deadline = time.monotonic() + 10
            while not any(c[0] == "send" for c in fake.calls) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertTrue(any(c[0] == "send" for c in fake.calls), "send must be in flight")
            started = time.monotonic()
            service.emergency_stop()
            task = service.engine._tasks[campaign["id"]]
            while not task.done() and time.monotonic() - started < 5:
                time.sleep(0.05)
            elapsed = time.monotonic() - started
            self.assertTrue(task.done())
            self.assertLess(elapsed, 5)
            state = service.campaign(campaign["id"])
            self.assertEqual(state["status"], "paused")
            self.assertEqual(fake.delivered, [])
            with self.assertRaises(ValueError):
                service.run(campaign["id"])
            sends = len([c for c in fake.calls if c[0] == "send"])
            time.sleep(1)
            self.assertEqual(sends, len([c for c in fake.calls if c[0] == "send"]))
        finally:
            service.shutdown(10)


class SecretsTests(MailingCase):
    def test_no_secrets_in_logs_events_ui_or_plain_files(self):
        log_path = self.data / "app.log"
        handler = logging.FileHandler(log_path, encoding="utf-8")
        root = logging.getLogger()
        root.addHandler(handler)
        root.setLevel(logging.INFO)
        try:
            owner = self.data / "accounts.json"
            owner.write_text(json.dumps({"api_id": API_ID, "api_hash": API_HASH, "accounts": [
                {"label": "test", "8935000000": 0, "phone": PHONE_A, "is_test": True}]}), encoding="utf-8")
            service = self.make_service()
            service.start()
            try:
                result = service.import_owner_file(str(owner))
                self.assertTrue(result["keys"])
                self.assertEqual(result["accounts_added"], 1)
                account_id = service.accounts()[0]["id"]
                self.assertEqual(service.login_start(account_id)["status"], "code_sent")
                with self.assertRaises(ValueError):
                    service.login_code(account_id, "00000")
                # Expected id from the owner file does not match the fake user -> rejected, not saved.
                with self.assertRaises(ValueError):
                    service.login_code(account_id, self.fake.login_code)
                logging.getLogger("telethon.network").warning("Connecting %s %s", PHONE_A, API_HASH)
                logging.getLogger("LeadEqualMoney.mailing").info("debug %s %s", API_ID, API_HASH)
                overview = json.dumps(service.overview(), ensure_ascii=False)
            finally:
                service.shutdown(10)
            handler.flush()
            haystacks = {
                "log": log_path.read_text(encoding="utf-8"),
                "overview": overview,
                "events": json.dumps(service.store.rows("SELECT * FROM events"), ensure_ascii=False),
            }
            for folder in (self.data / "mailing", self.data / "mailing" / "accounts"):
                for file in folder.glob("*.bin"):
                    haystacks[file.name] = file.read_bytes().decode("latin-1")
            secrets = [str(API_ID), API_HASH, PHONE_A.lstrip("+"), "fake-session", "8935000000"]
            for name, text in haystacks.items():
                for secret in secrets:
                    self.assertNotIn(secret, text, f"{secret[:2]}•• leaked into {name}")
            self.assertIn("31••••", overview)
        finally:
            root.removeHandler(handler)
            handler.close()

    def test_scrub_masks(self):
        text = scrub(f"phone {PHONE_A} hash {API_HASH} id {API_ID} session 1{'A' * 300}")
        for secret in (PHONE_A[1:], API_HASH, str(API_ID), "A" * 100):
            self.assertNotIn(secret, text)


class ConnectionTests(MailingCase):
    """Review defects: a campaign must never stay 'running' without a task; logout is not quarantine."""

    def test_network_on_connect_pauses_campaign_not_stuck(self):
        account = self.account()
        campaign = self.service.create_campaign("Тест", {}, TEMPLATE, [account], limit=2)
        self.fake.fail("connect", str(account), GatewayError(gw.NETWORK, "timeout"), times=3)
        result = self.live(campaign["id"])
        self.assertEqual(result["status"], "paused")
        self.assertEqual(self.service.store.value("SELECT status FROM accounts WHERE id=?", (account,)), "active")
        self.service.mark_running(campaign["id"])  # can be continued
        asyncio.run(self.service.engine.run_campaign(campaign["id"]))
        self.assertEqual(self.service.campaign(campaign["id"])["counts"]["sent"], 2)

    def test_flood_wait_on_connect_waits_then_sends(self):
        account = self.account()
        campaign = self.service.create_campaign("Тест", {}, TEMPLATE, [account], limit=1)
        self.fake.fail("connect", str(account), GatewayError(gw.FLOOD_WAIT, "FLOOD_WAIT", 60))
        result = self.live(campaign["id"])
        self.assertEqual(result["counts"]["sent"], 1)
        self.assertEqual(result["status"], "done")

    def test_missing_session_means_login_not_quarantine(self):
        account = self.account()
        self.service._account_path(account).unlink()
        campaign = self.service.create_campaign("Тест", {}, TEMPLATE, [account], limit=1)
        result = self.live(campaign["id"])
        row = self.service.store.row("SELECT * FROM accounts WHERE id=?", (account,))
        self.assertEqual(row["status"], "needs_login")
        self.assertEqual(result["status"], "paused")
        self.assertEqual(self.fake.delivered, [])

    def test_logout_then_login_reconnects_without_quarantine(self):
        account = self.account()
        first = self.service.create_campaign("1", {}, TEMPLATE, [account], limit=1)
        self.live(first["id"])
        self.service.start()
        try:
            self.service.remove_account(account)
            self.assertNotIn(account, self.service.engine._connected)
            self.service.login_start(account)
            self.assertEqual(self.service.login_code(account, self.fake.login_code)["status"], "authorized")
        finally:
            self.service.shutdown(10)
        second = self.service.create_campaign("2", {}, TEMPLATE, [account], limit=2)
        result = self.live(second["id"])
        self.assertEqual(self.service.store.value("SELECT status FROM accounts WHERE id=?", (account,)), "active")
        self.assertEqual(result["counts"]["sent"], 1)  # b1 already written to in campaign 1

    def test_phone_key_is_salted(self):
        import hashlib
        account = self.account()
        key = self.service.store.value("SELECT phone_key FROM accounts WHERE id=?", (account,))
        self.assertNotEqual(key, hashlib.sha256(PHONE_A[1:].encode()).hexdigest())


class ParsingTests(unittest.TestCase):
    def test_username_extraction(self):
        self.assertEqual(username_from_business({"socials": [{"url": "https://t.me/joinchat/abc"},
                                                             {"url": "https://t.me/Cafe_Moon"}]}), "cafe_moon")
        self.assertEqual(username_from_business({"socials": [{"url": "https://t.me/+79991234567"}]}), "")
        self.assertEqual(username_from_business({"socials": [{"url": "https://vk.com/cafe"}]}), "")
        self.assertEqual(normalize_target("@Owner_2"), "owner_2")
        self.assertEqual(normalize_target("+7 (999) 111-22-33"), "+79991112233")

    def test_telethon_errors_mapping(self):
        from telethon import errors
        from app.mailing.telethon_gateway import classify
        cases = {
            errors.FloodWaitError(request=None, capture=42): gw.FLOOD_WAIT,
            errors.PeerFloodError(request=None): gw.PEER_FLOOD,
            errors.UserPrivacyRestrictedError(request=None): gw.PRIVACY,
            errors.FrozenMethodInvalidError(request=None): gw.ACCOUNT_FROZEN,
            errors.UserDeactivatedBanError(request=None): gw.ACCOUNT_BANNED,
            errors.AuthKeyUnregisteredError(request=None): gw.SESSION_INVALID,
            errors.UserRestrictedError(request=None): gw.ACCOUNT_LIMITED,
            errors.UsernameNotOccupiedError(request=None): gw.NOT_FOUND,
            errors.RandomIdDuplicateError(request=None): gw.DUPLICATE,
            errors.RPCError(request=None, message="ALLOW_PAYMENT_REQUIRED", code=400): gw.PRIVACY,
            ConnectionError(): gw.NETWORK,
        }
        for error, kind in cases.items():
            self.assertEqual(classify(error).kind, kind, type(error).__name__)
        self.assertEqual(classify(errors.FloodWaitError(request=None, capture=42)).seconds, 42)


class FromLeadsTests(MailingCase):
    """Redesign 02: campaign from leads selected in «Лиды» and per-recipient texts."""

    def test_campaign_from_selected_leads_keeps_order_and_safety(self):
        account = self.account()
        self.service.stop_add("business", "b3")
        self.orgs[4]["socials"] = []  # b5 has no Telegram username
        campaign = self.service.create_campaign("Из лидов", {"ids": ["b6", "b3", "b5", "b2", "b6", "nope"]},
                                                TEMPLATE, [account])
        rows = [(r["business_id"], r["status"], r["reason"]) for r in campaign["recipients"]]
        self.assertEqual([r[0] for r in rows], ["b6", "b3", "b5", "b2"])
        self.assertEqual(rows[1][1:], ("skipped", "В стоп-листе"))
        self.assertEqual(rows[2][1:], ("queued", ""))   # no username, but a mobile: written to by number (N33)
        self.assertEqual(json.loads(campaign["filters_json"])["ids"][0], "b6")
        self.assertEqual(campaign["mode"], "dry")
        self.assertEqual(self.fake.delivered, [])

    def test_no_selected_leads_with_contact(self):
        account = self.account()
        with self.assertRaises(ValueError):
            self.service.create_campaign("Пусто", {"ids": ["nope"]}, TEMPLATE, [account])

    def test_edited_text_is_sent_and_reset_restores_template(self):
        account = self.account()
        campaign = self.service.create_campaign("Из лидов", {"ids": ["b1", "b2"]}, TEMPLATE, [account])
        first, second = campaign["recipients"]
        with self.assertRaises(ValueError):
            self.service.set_message(campaign["id"], first["id"], "Привет, [имя]")
        with self.assertRaises(ValueError):
            self.service.set_message(campaign["id"], first["id"], "   ")
        edited = self.service.set_message(campaign["id"], first["id"], "Личный текст для первой")
        self.assertNotIn("СТОП", edited["message"])
        self.service.set_message(campaign["id"], second["id"], "Временный текст")
        reset = self.service.reset_message(campaign["id"], second["id"])
        self.assertEqual(reset["message"], second["message"])
        detail = self.service.campaign(campaign["id"])
        self.assertEqual([r["edited"] for r in detail["recipients"]], [1, 0])
        self.service.dry_run(campaign["id"])  # edits survive the dry run
        self.service.set_message(campaign["id"], first["id"], "Личный текст после сухого запуска")
        self.live(campaign["id"])
        texts = dict(self.deliveries())
        self.assertTrue(any(t.startswith("Личный текст после сухого запуска") for t in texts.values()))
        self.assertTrue(any(t.startswith("Здравствуйте! Пишу Организация 2") for t in texts.values()))
        with self.assertRaises(ValueError):  # already sent: text is history
            self.service.set_message(campaign["id"], first["id"], "Поздно")

    def test_no_edit_while_running(self):
        account = self.account()
        campaign = self.service.create_campaign("Из лидов", {"ids": ["b1"]}, TEMPLATE, [account])
        self.service.enable_live(campaign["id"], "ОТПРАВИТЬ")
        self.service.mark_running(campaign["id"])
        with self.assertRaises(ValueError):
            self.service.set_message(campaign["id"], campaign["recipients"][0]["id"], "Новый")

    def test_auto_script_fits_each_business_site_type(self):
        account = self.account()
        self.service._templates = lambda: [{"slug": "site.first", "title": "Сайт", "body": "Сайт для {название}"},
                                           {"slug": "site.first.redesign", "title": "Редизайн", "body": "Редизайн {название}"}]
        self.orgs[0]["site_type"], self.orgs[1]["site_type"] = "no_site", "weak_site"
        campaign = self.service.create_campaign("Авто", {"ids": ["b1", "b2"]}, "auto", [account])
        texts = [r["message"].split(chr(10))[0] for r in campaign["recipients"]]
        self.assertEqual(texts, ["Сайт для Организация 1", "Редизайн Организация 2"])
        first = campaign["recipients"][1]
        self.service.set_message(campaign["id"], first["id"], "Своё")
        self.assertTrue(self.service.reset_message(campaign["id"], first["id"])["message"].startswith("Редизайн"))

    def test_one_button_launch_needs_confirm_word_and_batch_limit(self):
        account = self.account()
        with self.assertRaises(ValueError):
            self.service.launch("Пачка", ["b1", "b2"], TEMPLATE, [account], "да")
        self.assertEqual(self.service.campaigns(), [])  # nothing created without the word
        self.service.save_policy({"batch_limit": 3})
        with self.assertRaises(ValueError):
            self.service.save_policy({"batch_limit": 501})
        self.service.start()
        try:
            launched = self.service.launch("Пачка", [f"b{i}" for i in range(1, 9)], TEMPLATE, [account], "ОТПРАВИТЬ")
        finally:
            self.service.shutdown(10)
        self.assertEqual(launched["mode"], "live")
        self.assertEqual(launched["counts"]["total"], 3)
        self.assertIn(launched["status"], ("running", "done"))

    def test_store_adds_edited_column_to_old_database(self):
        import sqlite3
        path = self.data / "old.sqlite3"
        Store(path)
        conn = sqlite3.connect(path)
        conn.execute("ALTER TABLE recipients DROP COLUMN edited")  # database of the previous build
        conn.commit(); conn.close()
        Store(path)
        conn = sqlite3.connect(path)
        self.assertIn("edited", {row[1] for row in conn.execute("PRAGMA table_info(recipients)")})
        conn.close()


if __name__ == "__main__":
    unittest.main()
