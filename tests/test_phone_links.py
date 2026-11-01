from __future__ import annotations

import unittest

from hypothesis import given, settings, strategies as st

from app.phone_links import build_telegram_phone_links


class TelegramPhoneLinksTests(unittest.TestCase):
    def test_equivalent_russian_mobile_forms_are_normalized_and_deduplicated(self):
        result = build_telegram_phone_links([
            "+7 (999) 123-45-67",
            "8 999 123-45-67",
            "9991234567",
        ])

        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["ok"])
        self.assertEqual(result[0]["e164"], "79991234567")
        self.assertEqual(result[0]["url"], "https://t.me/+79991234567")

    def test_international_mobile_gets_a_manual_link(self):
        result = build_telegram_phone_links(["+44 7911 123456"])

        self.assertEqual(result[0]["url"], "https://t.me/+447911123456")

    def test_landline_extension_and_garbage_are_visible_without_links(self):
        result = build_telegram_phone_links([
            "+7 495 123-45-67",
            "+7 999 123-45-67 ext. 123",
            "не номер",
        ])

        self.assertEqual(len(result), 3)
        self.assertTrue(all(not row["ok"] and not row["url"] for row in result))
        self.assertIn("Городской", result[0]["reason"])
        self.assertIn("Добавочный", result[1]["reason"])
        self.assertTrue(result[2]["reason"])

    @settings(max_examples=150, deadline=None)
    @given(st.text(max_size=128))
    def test_arbitrary_phone_text_never_raises(self, value: str):
        result = build_telegram_phone_links([value])
        self.assertIsInstance(result, list)
        for row in result:
            self.assertEqual(bool(row["ok"]), bool(row["url"]))
            if row["url"]:
                self.assertRegex(row["url"], r"^https://t\.me/\+\d+$")

    @settings(max_examples=100, deadline=None)
    @given(st.lists(st.sampled_from(["+7 (999) 123-45-67", "8 999 123-45-67", "9991234567"]),
                    min_size=1, max_size=20))
    def test_equivalent_number_list_is_idempotently_reduced(self, values: list[str]):
        result = build_telegram_phone_links(values)
        self.assertEqual(len(result), 1)
        canonical = build_telegram_phone_links(["+" + str(result[0]["e164"])])
        self.assertEqual(canonical[0]["e164"], result[0]["e164"])
        self.assertEqual(canonical[0]["url"], result[0]["url"])


if __name__ == "__main__":
    unittest.main()
