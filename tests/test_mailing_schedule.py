"""Greeting by the recipient's local time (утро/день/вечер) and the planned send time on every waiting card."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.mailing.policy import apply_greeting
from tests.test_mailing_core import MailingCase

FIRST = "Добрый день!) Нашёл {название} на картах"


class GreetingTests(unittest.TestCase):
    def at(self, hour_utc, tz="Europe/Moscow"):
        return apply_greeting(FIRST, datetime(2026, 11, 2, hour_utc, 0, tzinfo=timezone.utc), tz)

    def test_by_local_hour(self):
        self.assertTrue(self.at(4).startswith("Доброго утра!)"))             # 07:00 Moscow
        self.assertTrue(self.at(8).startswith("Добрый день!)"))              # 11:00 Moscow
        self.assertTrue(self.at(14).startswith("Добрый вечер!)"))            # 17:00 Moscow
        self.assertTrue(self.at(2, "Asia/Yekaterinburg").startswith("Доброго утра!)"))   # 07:00 Tyumen
        self.assertTrue(self.at(12, "Asia/Yekaterinburg").startswith("Добрый вечер!)"))  # 17:00 Tyumen

    def test_other_openings_untouched(self):
        moment = datetime(2026, 11, 2, 4, 0, tzinfo=timezone.utc)
        self.assertEqual(apply_greeting("Здравствуйте!) Текст", moment, "Europe/Moscow"), "Здравствуйте!) Текст")
        self.assertEqual(apply_greeting("Текст: добрый день", moment, "Europe/Moscow"), "Текст: добрый день")


class ScheduleTests(MailingCase):
    def test_sent_greeting_fits_the_send_time(self):
        account = self.account()
        self.service.save_policy({"window_start": 6, "window_end": 21, "weekdays_only": False})
        self.clock.now = datetime(2026, 11, 2, 4, 0, tzinfo=timezone.utc)        # 07:00 Moscow
        campaign = self.service.create_campaign("Т", {"ids": ["b1"]}, FIRST, [account])
        self.live(campaign["id"])
        self.assertTrue(self.fake.delivered[0].text.startswith("Доброго утра!)"), self.fake.delivered[0].text)
        sent = self.service.campaign(campaign["id"])["recipients"][0]
        self.assertTrue(sent["message"].startswith("Доброго утра!)"))      # the saved text is what went out

    def test_every_waiting_card_has_a_planned_time(self):
        account = self.account()
        campaign = self.service.create_campaign("Т", {"ids": [f"b{i}" for i in range(1, 9)]}, FIRST, [account])
        self.service.enable_live(campaign["id"], "ОТПРАВИТЬ")
        rows = self.service.campaign(campaign["id"])["recipients"]
        due = [r.get("due_at") for r in rows]
        self.assertTrue(all(due), due)
        self.assertEqual(due, sorted(due))
        self.assertTrue(all(r["due_when"] for r in rows))
        # New account: 5 on the first day, the rest continue next working day — not one per day.
        days = [d[:10] for d in due]
        self.assertEqual(days.count(days[0]), 5)
        self.assertEqual(len(set(days[5:])), 1)


if __name__ == "__main__":
    unittest.main()
