"""All phones of a card: the HTML shows one, the page state JSON has both, WhatsApp/Viber links add the
mobile again (01.11, the «Гранат» card had exactly this shape). Synthetic numbers only."""

from __future__ import annotations

import json
import unittest

from app.lead_parser import parse_org_html
from app.phone_links import build_telegram_phone_links, merge_phones, messenger_phones

URL = "https://yandex.ru/maps/org/test_cafe/1000000001/"
LANDLINE = "+7 (3452) 00-00-01"
MOBILE = "+7 (900) 000-00-02"


def card(state_phones, html_phones=(LANDLINE,), social_links=(), extra_html=""):
    state = {"stack": [{"results": {"items": [{
        "id": "1000000001", "phones": state_phones, "socialLinks": list(social_links),
        "ratingData": {"ratingCount": 140, "ratingValue": 4.3, "reviewCount": 53}, "photos": {"count": 23}}]}}]}
    shown = "".join(f"<div class='orgpage-phones-view__phone-number'>{p}</div>" for p in html_phones)
    return ("<h1 class='orgpage-header-view__header'>Тестовое кафе</h1>"
            "<a class='orgpage-header-view__address'>Тюмень, ул. Первая, 1</a>"
            "<div class='business-header-rating-view__text'>140 оценок</div>" + shown + extra_html +
            f"<script type='application/json' class='state-view'>{json.dumps(state, ensure_ascii=False)}</script>")


class CardPhonesTests(unittest.TestCase):
    def test_two_numbers_in_json_one_in_html(self):
        page = card([{"type": "phone", "number": LANDLINE, "value": "+73452000001"},
                     {"type": "phone", "number": MOBILE, "value": "+79000000002"}])
        row = parse_org_html(URL, page, 0, check_site=False)
        self.assertEqual(row["phones"], [LANDLINE, MOBILE])          # order: as on the card
        links = build_telegram_phone_links(row["phones"])
        self.assertEqual([item["ok"] for item in links], [False, True])  # landline gets no Telegram link
        self.assertEqual(row["reviews"], 53)                            # reviewCount, not 140 ratings
        self.assertEqual(row["details"]["photo_count"], 23)

    def test_whatsapp_link_adds_the_mobile_once(self):
        wa = "https://wa.me/79000000002?text=%D0%9E%D0%B1%D1%80%D0%B0%D1%89%D0%B5%D0%BD%D0%B8%D0%B5"
        page = card([{"type": "phone", "number": LANDLINE, "value": "+73452000001"}],
                    social_links=[{"type": "whatsapp", "href": wa}],
                    extra_html=f"<a href='{wa}'>WhatsApp</a><a href='viber://chat?number=%2B79000000003'>Viber</a>")
        row = parse_org_html(URL, page, 0, check_site=False)
        self.assertEqual(len(row["phones"]), 3)
        self.assertEqual(row["phones"][0], LANDLINE)
        self.assertIn("+7 900 000-00-02", row["phones"])
        self.assertIn("+7 900 000-00-03", row["phones"])

    def test_html_only_fallback(self):
        page = ("<h1 class='orgpage-header-view__header'>Кафе</h1>"
                f"<div class='orgpage-phones-view__phone-number'>{MOBILE}</div>")
        row = parse_org_html(URL, page, 0, check_site=False)
        self.assertEqual(row["phones"], [MOBILE])

    def test_merge_dedupes_forms_of_one_number(self):
        self.assertEqual(merge_phones([MOBILE], ["+79000000002", "8 900 000 00 02"], ["79000000002"]), [MOBILE])

    def test_messenger_phones(self):
        self.assertEqual(messenger_phones(["https://wa.me/79000000002?text=x",
                                           "https://api.whatsapp.com/send?phone=79000000004",
                                           "viber://add?number=79000000005", "https://t.me/name"]),
                         ["79000000002", "79000000004", "79000000005"])


if __name__ == "__main__":
    unittest.main()
