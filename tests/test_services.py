import ast
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db, services

ROOT = Path(__file__).resolve().parent.parent


def bridge_methods() -> set[str]:
    """Public Bridge methods, read from source (desktop.py needs Windows-only modules)."""
    tree = ast.parse((ROOT / "app" / "desktop.py").read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Bridge")
    return {n.name for n in cls.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")}


def ui_methods() -> set[str]:
    text = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "app" / "desktop_ui").glob("*.js"))
    return set(re.findall(r"\bcall\('([a-z_]+)'", text))


class RegistryTests(unittest.TestCase):
    def test_every_ui_method_is_served(self):
        # Desktop: «Рассылка» methods come from MailingBridgeMixin (app/mailing, not part of the registry).
        from app.mailing.desktop_bridge import MailingBridgeMixin
        mailing = {name for name in dir(MailingBridgeMixin) if name.startswith("mailing_")}
        served = set(services.SERVICES) | bridge_methods() | mailing
        self.assertEqual(ui_methods() - served, set())

    def test_server_only_methods_stay_out_of_the_desktop(self):
        self.assertFalse(services.SERVER_ONLY & set(services.SERVICES))

    def test_groups_do_not_overlap(self):
        self.assertFalse(services.DESKTOP_ONLY & set(services.SERVICES))
        self.assertTrue(services.WEB_METHODS <= set(services.SERVICES))
        self.assertTrue(services.DESKTOP_ONLY <= bridge_methods() | {"backup"})

    def test_bridge_keeps_only_desktop_methods(self):
        self.assertEqual(bridge_methods() - {"bootstrap"}, set(services.DESKTOP_ONLY))

    def test_call_respects_allow_list(self):
        with self.assertRaises(KeyError):
            services.call("business_refresh", [], allowed=services.WEB_METHODS)
        with self.assertRaises(KeyError):
            services.call("open_logs", [])


class ServiceBehaviourTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.tmp.name) / "s.sqlite3"
        db.init_desktop_db(self.path)
        self.patches = [patch.object(db, "DB_PATH", self.path)]
        for name in ("templates", "save_template", "business", "set_business", "stats"):
            original = getattr(db, name)
            self.patches.append(patch.object(db, name, side_effect=lambda *a, _o=original, **k: _o(*a, path=self.path, **k)))
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_template_save_validates(self):
        with self.assertRaises(ValueError):
            services.template_save("first", "   ")
        with self.assertRaises(ValueError):
            services.template_save("no-such-template", "text")
        slug = services.templates()[0]["slug"]
        self.assertTrue(services.template_save(slug, "Новый текст"))

    def test_ui_settings_default_save_and_validate(self):
        self.assertEqual(services.ui_settings(), {"theme": "light", "review_limit": 20})
        self.assertEqual(services.ui_settings_save({"theme": "dark", "review_limit": 5}),
                         {"theme": "dark", "review_limit": 5})
        self.assertEqual(services.ui_settings()["theme"], "dark")
        with self.assertRaises(ValueError):
            services.ui_settings_save({"theme": "pink"})
        with self.assertRaises(ValueError):
            services.ui_settings_save({"review_limit": 101})

    def test_search_start_rejects_over_100(self):
        with self.assertRaises(ValueError):
            services.search_start({"country": "Россия", "cities": ["Казань"], "niche": "кафе",
                                   "count": 101, "types": ["no_site"]})

    def test_missing_business(self):
        self.assertIsNone(services.business_save("nope", None, "заметка"))
        with self.assertRaises(ValueError):
            services.telegram_phone_links("nope")


if __name__ == "__main__":
    unittest.main()
