"""Clickable links in mailings: entities with UTF-16 offsets, [слова](url) hyperlinks, FakeGateway keeps them."""

from __future__ import annotations

import unittest

from app.mailing.entities import Entity, parse, strip_markdown_links, utf16_len
from tests.test_mailing_core import MailingCase


def cut(text, entity):
    """The piece of text an entity covers, counted in UTF-16 like Telegram."""
    raw = text.encode("utf-16-le")
    return raw[entity.offset * 2:(entity.offset + entity.length) * 2].decode("utf-16-le")


class EntityTests(unittest.TestCase):
    def test_bare_url_in_brackets_with_cyrillic_and_emoji(self):
        text = "Привет 👋! Пример сайта (https://example.ru/кафе), напишите."
        plain, entities = parse(text)
        self.assertEqual(plain, text)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0].kind, "url")
        self.assertEqual(cut(plain, entities[0]), "https://example.ru/кафе")

    def test_words_link(self):
        text = "🔥 Посмотрите [наш пример](https://example.ru/demo) и t.me/name."
        plain, entities = parse(text)
        self.assertEqual(plain, "🔥 Посмотрите наш пример и t.me/name.")
        self.assertEqual([e.kind for e in entities], ["text_url", "url"])
        self.assertEqual(cut(plain, entities[0]), "наш пример")
        self.assertEqual(entities[0].url, "https://example.ru/demo")
        self.assertEqual(cut(plain, entities[1]), "t.me/name")
        self.assertEqual(utf16_len("🔥"), 2)

    def test_placeholder_is_not_a_link(self):
        self.assertEqual(strip_markdown_links("[имя], смотрите [тут](https://a.ru)"), "[имя], смотрите тут")


class SendWithLinksTests(MailingCase):
    def test_outgoing_messages_carry_no_links(self):
        # Telegram shows links from non-contacts as plain text (recipient side): messages go without links.
        account = self.account()
        with self.assertRaises(ValueError):
            self.service.create_campaign("Т", {"ids": ["b1"]}, "Здравствуйте, [имя]!", [account])
        campaign = self.service.create_campaign(
            "Т", {"ids": ["b1"]}, "Здравствуйте, {название}! Пример (https://example.ru), [наш сайт](https://x.ru) 👍", [account])
        self.live(campaign["id"])
        item = self.fake.delivered[0]
        self.assertTrue(item.text.startswith("Здравствуйте, Организация 1! Пример, наш сайт 👍"), item.text)
        self.assertNotIn("http", item.text)
        self.assertEqual(item.entities, ())

if __name__ == "__main__":
    unittest.main()
