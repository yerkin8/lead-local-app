"""Desktop bridge for «Рассылка»: what the page can call and what it never sees."""

from __future__ import annotations

import inspect
import os
import tempfile
import unittest
from pathlib import Path

from app.domain import render_template
from app.mailing.desktop_bridge import MailingBridgeMixin
from app.mailing.fake_gateway import FakeGateway
from app.mailing.service import MailingService


class Page(MailingBridgeMixin):
    _window = None


def exposed(obj) -> set[str]:
    """Same rule as pywebview.util.get_functions: public methods and public objects (recursively)."""
    names = set()
    for name in dir(obj):
        if name.startswith("_"):
            continue
        attr = getattr(obj, name)
        if inspect.ismethod(attr) or inspect.isfunction(attr):
            names.add(name)
        elif not callable(attr) and hasattr(attr, "__module__"):
            names.add(name + ".*")
    return names


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.page = Page()
        self.page._mailing = MailingService(
            Path(self.tmp.name), FakeGateway(), businesses=lambda q, n: [], business=lambda i: None,
            templates=lambda: [{"slug": "first", "title": "Первое", "body": "Текст"}], render=render_template,
            first_by_site_type={"no_site": "first"})

    def tearDown(self):
        self.tmp.cleanup()

    def test_service_is_not_exposed_to_the_page(self):
        names = exposed(self.page)
        self.assertFalse(any(name.endswith(".*") for name in names), names)
        self.assertTrue({"mailing_overview", "mailing_emergency", "mailing_new", "mailing_run"} <= names)
        self.assertTrue(all(name.startswith("mailing_") for name in names), names)

    def test_keys_can_be_cleared_from_settings(self):
        self.assertTrue(self.page.mailing_save_keys("31415926", "a" * 32)["ok"])
        self.assertTrue(self.page.mailing_overview()["data"]["keys_configured"])
        self.assertTrue(self.page.mailing_clear_keys()["ok"])
        self.assertFalse(self.page.mailing_overview()["data"]["keys_configured"])
        self.assertTrue(self.page.mailing_clear_keys()["ok"])  # nothing saved: still fine

    def test_errors_are_masked(self):
        result = self.page.mailing_save_keys("31415926", "not-a-hash")
        self.assertFalse(result["ok"])
        result = self.page.mailing_account_add("x", "+7 999 111 22 33", False)
        self.assertTrue(result["ok"])
        overview = self.page.mailing_overview()["data"]
        self.assertEqual(overview["accounts"][0]["phone_mask"], "+7••••33")
        self.assertNotIn("9991112233", str(overview))

    def test_overview_without_keys_starts_wizard(self):
        data = self.page.mailing_overview()["data"]
        self.assertFalse(data["keys_configured"])
        self.assertFalse(data["emergency_stop"])

    def test_missing_service_reports_error(self):
        page = Page()
        result = page.mailing_overview()
        self.assertFalse(result["ok"])

    def test_desktop_bridge_inherits_mailing_methods(self):
        source = (Path(__file__).resolve().parent.parent / "app" / "desktop.py").read_text(encoding="utf-8")
        self.assertIn("class Bridge(MailingBridgeMixin)", source)
        self.assertNotIn("telegram_sender", source)


if __name__ == "__main__":
    unittest.main()
