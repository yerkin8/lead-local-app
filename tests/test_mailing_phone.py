"""N33: a business without @username is written to by its mobile number («Telegram по номеру»), FakeGateway only."""

from __future__ import annotations

from tests.test_mailing_core import TEMPLATE, MailingCase, business


class PhoneTargetTests(MailingCase):
    def setUp(self):
        super().setUp()
        # b11: only a mobile; b12: landline + two mobiles, the first is hidden; b13: only a landline
        self.orgs += [business(11, username="", phone="+7 900 111-11-11"),
                      {**business(12, username=""), "phones": ["+7 (3452) 00-00-01", "+7 900 222-22-21", "+7 900 222-22-22"]},
                      business(13, username="", phone="+7 (3452) 00-00-03")]
        self.p11 = self.fake.add_phone("79001111111")
        self.p12 = self.fake.add_phone("79002222222")    # the second mobile of b12 resolves
        self.account_id = self.account()

    def campaign(self, ids):
        campaign = self.service.create_campaign("Т", {"ids": ids}, TEMPLATE, [self.account_id])
        return self.live(campaign["id"])

    def test_mobile_without_username_gets_the_message(self):
        detail = self.campaign(["b11"])
        self.assertEqual(detail["counts"]["sent"], 1)
        self.assertEqual(len(self.fake.sent_to(self.p11.user_id)), 1)
        self.assertTrue(detail["recipients"][0]["username"].startswith("по номеру +7 9••"))

    def test_hidden_first_mobile_falls_back_to_the_next(self):
        detail = self.campaign(["b12"])
        self.assertEqual(detail["counts"]["sent"], 1)
        self.assertEqual(len(self.fake.sent_to(self.p12.user_id)), 1)

    def test_landline_only_is_skipped_with_a_clear_reason(self):
        detail = self.campaign(["b13"])
        self.assertEqual(detail["recipients"][0]["reason"], "Нет username Telegram и мобильного номера")

    def test_hidden_number_is_not_delivered_and_not_asked_again(self):
        self.orgs.append(business(14, username="", phone="+7 900 444-44-44"))   # not resolvable
        detail = self.campaign(["b14"])
        row = detail["recipients"][0]
        self.assertEqual((row["status"], row["reason"]), ("unreachable", "Номер скрыт или без Telegram"))
        again = self.service.create_campaign("Т2", {"ids": ["b14"]}, TEMPLATE, [self.account_id])
        calls = len([c for c in self.fake.calls if c[0] == "resolve_phone"])
        self.live(again["id"])
        self.assertEqual(len([c for c in self.fake.calls if c[0] == "resolve_phone"]), calls)

    def test_stop_list_number_and_one_message_per_number(self):
        self.service.stop_add("phone", "+7 900 111-11-11")
        self.assertEqual(self.campaign(["b11"])["counts"]["sent"], 0)
        self.orgs.append(business(15, username="", phone="+7 900 222-22-22"))   # same number as b12's mobile
        detail = self.campaign(["b12", "b15"])
        self.assertEqual(detail["counts"]["sent"], 1)
        self.assertEqual(len(self.fake.sent_to(self.p12.user_id)), 1)
