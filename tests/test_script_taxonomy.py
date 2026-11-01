import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from app import db, import_scripts, services
from app.script_legacy import V5_TEMPLATES
from app.script_templates import (LEGACY_SLUGS, OFFER_CATEGORIES, TEMPLATES, body_hash, offers, parse_slug,
                                  pick_slug, sort_key, subtype_for)

# A fixed stand-in for the shipped texts, so these tests do not depend on app/script_texts.py.
FIXED = {
    "site.first": ("Первое · сайт", "Здравствуйте! Нашёл {название} в Яндекс Картах."),
    "site.first.redesign": ("Первое · переделка", "Здравствуйте! Сайт {название}: {тип_сайта}."),
    "site.reminder": ("Напоминание · сайт", "Здравствуйте! Писал про {название}."),
    "general.meeting": ("Созвон", "Созвонимся [время]?"),
}
FIXED_ROWS = [{"slug": slug, "title": title, "body": body, "origin": "builtin"} for slug, (title, body) in FIXED.items()]

SAMPLE = """# Новые скрипты — черновик

Комментарий вне блоков игнорируется.

## site — Сайт

### `site.first` — Первое · сайт
```text
Здравствуйте! Новый текст для {название}.
```

### `site.first.booking` — Первое · сайт с бронью
```text
Здравствуйте! Бронь столов для {название} прямо с сайта.
```

### `site.reminder` — Напоминание · сайт
```text
Здравствуйте, [имя]! Напоминаю.
```

### `site.second` — Неизвестный этап
```text
Что-то.
```

### `bot.first` — Первое · бот
```text
Бот для {название}: запись без звонков.
```
"""


class TaxonomyTests(unittest.TestCase):
    def test_parse_slug(self):
        self.assertEqual(parse_slug("site.first.booking"), ("site", "first", "booking"))
        self.assertEqual(parse_slug("bot.objection.not_needed"), ("bot", "objection.not_needed", ""))
        self.assertEqual(parse_slug("general.meeting"), ("general", "meeting", ""))
        for bad in ("site.first.menu", "site.reply.booking", "general.first", "shop.first", "site.objection",
                    "site.objection.own.x", "quick_no_site", ""):
            self.assertIsNone(parse_slug(bad), bad)

    def test_every_builtin_and_legacy_target_is_valid(self):
        for slug in TEMPLATES:
            self.assertIsNotNone(parse_slug(slug), slug)
        for old, new in LEGACY_SLUGS.items():
            self.assertIn(new, TEMPLATES, old)
        self.assertEqual(set(LEGACY_SLUGS), set(V5_TEMPLATES))  # every old slug has a home
        sorts = [sort_key(*parse_slug(slug)) for slug in TEMPLATES]
        self.assertEqual(sorts, sorted(sorts))

    def test_quick_scripts_have_no_brackets(self):
        for slug, (_, body) in TEMPLATES.items():
            if parse_slug(slug)[1] in {"first", "reminder"}:
                self.assertNotRegex(body, r"\[", slug)

    def test_subtype_by_niche_rubrics_and_site_type(self):
        cafe = {"site_type": "no_site", "niche": "Рестораны"}
        self.assertEqual([subtype_for(cafe, c) for c in OFFER_CATEGORIES], ["booking", "menu", "booking", "card"])
        self.assertEqual(subtype_for({"site_type": "weak_site", "niche": "Ресторан"}, "site"), "redesign")
        self.assertEqual(subtype_for({"site_type": "unavailable"}, "site"), "redesign")
        rubric = {"niche": "", "details": {"categories": ["Барбершоп"]}}
        self.assertEqual(subtype_for(rubric, "bot"), "booking")
        self.assertEqual(subtype_for({"niche": "Нотариус"}, "site"), "")

    def test_pick_falls_back_to_plain_first(self):
        available = {"site.first", "site.first.booking", "bot.first"}
        self.assertEqual(pick_slug({"niche": "кафе"}, "site", "first", available), "site.first.booking")
        self.assertEqual(pick_slug({"niche": "кафе"}, "bot", "first", available), "bot.first")
        self.assertEqual(pick_slug({"niche": "нотариус"}, "site", "first", available), "site.first")
        self.assertEqual(pick_slug({"site_type": "weak_site"}, "site", "first", available), "site.first")
        self.assertIsNone(pick_slug({}, "landing", "first", available))
        self.assertIsNone(pick_slug({}, "site", "reminder", available))

    def test_offers_render_four_categories(self):
        result = offers({"name": "Кафе Ромашка", "site_type": "no_site", "niche": "кафе"}, FIXED_ROWS)
        self.assertEqual([offer["category"] for offer in result], ["site", "landing", "bot", "promotion"])
        self.assertEqual(result[0]["slug"], "site.first")
        self.assertIn("Кафе Ромашка", result[0]["text"])
        self.assertEqual((result[0]["subtype"], result[0]["suggested_subtype"]), ("", "booking"))  # no booking text yet
        self.assertEqual(result[1]["text"], "")  # no landing texts until SCRIPTS_NEW.md is imported


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.tmp.name) / "t.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def v5_database(self, edits=None, ai=True):
        conn = sqlite3.connect(self.path)
        conn.executescript("""CREATE TABLE templates (slug TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
            updated_at TEXT NOT NULL, origin TEXT NOT NULL DEFAULT 'builtin');
            CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
            INSERT INTO schema_migrations VALUES (5,'x');""")
        for slug, (title, body) in V5_TEMPLATES.items():
            conn.execute("INSERT INTO templates VALUES (?,?,?,'x','builtin')", (slug, title, (edits or {}).get(slug, body)))
        if ai:
            conn.execute("INSERT INTO templates VALUES ('ai_20260101000000_aaaa','ИИ','Текст','x','ai')")
        conn.commit()
        conn.close()

    def test_old_slugs_renamed_edits_kept_and_backup_taken(self):
        self.v5_database({"quick_weak_site": "Моя правка сайта", "short": "Моё короткое", "expensive": "Моё дорого"})
        db.init_desktop_db(self.path)
        with db.connection(self.path) as conn:
            conn.execute("""INSERT INTO campaigns(id,title,status,template_slug,created_at,updated_at)
                VALUES ('c1','Тест','draft','quick_no_site','x','x')""")
        db.init_desktop_db(self.path)  # second run: idempotent, fixes the campaign slug
        rows = {row["slug"]: row for row in db.templates(self.path)}
        self.assertEqual(set(TEMPLATES) <= set(rows), True)
        self.assertFalse(set(V5_TEMPLATES) & set(rows))
        self.assertEqual(rows["site.first.redesign"]["body"], "Моя правка сайта")
        self.assertEqual(rows["site.objection.expensive"]["body"], "Моё дорого")
        self.assertEqual(rows["site.first"]["body"], TEMPLATES["site.first"][1])
        copy = rows["custom_short"]  # merged slot was taken: the edit stays as the user's own copy
        self.assertEqual((copy["origin"], copy["body"], copy["category"], copy["stage"]),
                         ("custom", "Моё короткое", "site", "first"))
        self.assertEqual((rows["ai_20260101000000_aaaa"]["category"], rows["ai_20260101000000_aaaa"]["origin"]),
                         ("general", "ai"))
        self.assertEqual(rows["general.meeting"]["category"], "general")
        self.assertEqual(rows["site.objection.think"]["stage"], "objection.think")
        self.assertEqual(len(list((self.path.parent / "backups").glob(f"t-pre-schema-v{db.DESKTOP_SCHEMA_VERSION}-*.sqlite3"))), 1)
        with db.connection(self.path) as conn:
            self.assertEqual(conn.execute("SELECT template_slug FROM campaigns").fetchone()[0], "site.first")
        self.assertTrue(db.delete_template("custom_short", self.path))
        self.assertFalse(db.delete_template("site.first", self.path))

    def test_old_slug_lookup_and_save(self):
        db.init_db(self.path)
        self.assertEqual(db.template("quick_followup", self.path)["slug"], "site.reminder")
        self.assertTrue(db.save_template("meeting", "Созвон?", self.path))
        self.assertEqual(db.template("general.meeting", self.path)["body"], "Созвон?")

    def test_new_shipped_text_replaces_only_untouched(self):
        db.init_db(self.path)
        db.save_template("site.call", "Мой звонок", self.path)
        texts = {**TEMPLATES, "site.reply": ("Ответ", "Новый ответ"), "site.call": ("Звонок", "Новый звонок"),
                 "bot.first": ("Бот", "Бот для {название}")}
        db.sync_builtin_templates(self.path, texts)
        rows = {row["slug"]: row for row in db.templates(self.path)}
        self.assertEqual(rows["site.reply"]["body"], "Новый ответ")
        self.assertEqual(rows["site.call"]["body"], "Мой звонок")
        self.assertEqual((rows["bot.first"]["category"], rows["bot.first"]["stage"]), ("bot", "first"))
        db.sync_builtin_templates(self.path, texts, overwrite_edited=True)
        self.assertEqual(db.template("site.call", self.path)["body"], "Новый звонок")
        self.assertEqual(db.template("site.call", self.path)["default_hash"], body_hash("Новый звонок"))

    def test_ai_script_lands_in_its_category(self):
        db.init_db(self.path)
        slug = db.add_ai_template("Бот для кафе", "Текст", self.path, category="bot")
        rows = db.templates(self.path)
        index = [row["slug"] for row in rows].index(slug)
        self.assertEqual(rows[index]["category"], "bot")
        self.assertTrue(all(row["category"] != "bot" or row["slug"] == slug for row in rows[index + 1:]))


class ImportScriptsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_parse_report(self):
        parsed = import_scripts.parse(SAMPLE)
        self.assertEqual(list(parsed.scripts), ["site.first", "site.first.booking", "site.reminder", "bot.first"])
        self.assertEqual(parsed.unknown, ["site.second"])
        self.assertTrue(any("site.reminder" in warning for warning in parsed.warnings))
        result = import_scripts.plan(parsed, dict(FIXED))
        self.assertEqual(result.added, ["site.first.booking", "bot.first"])
        self.assertEqual(result.changed, ["site.first", "site.reminder"])
        self.assertIn("general.meeting", result.kept)
        self.assertIn("site.first.booking", result.merged)

    def test_parse_errors(self):
        parsed = import_scripts.parse("### `site.first` — Без блока\n\n### `site.call` — " + "Д" * 70
                                      + "\n```text\nТекст\n```\n### `site.call`\n```text\nЕщё\n```\n")
        self.assertTrue(any("site.first" in error for error in parsed.errors))
        self.assertTrue(any("дважды" in error for error in parsed.errors))
        self.assertEqual(len(parsed.scripts["site.call"][0]), 60)

    def test_dry_run_writes_nothing_and_apply_updates_code_and_db(self):
        source = self.root / "SCRIPTS_NEW.md"
        source.write_text(SAMPLE, encoding="utf-8")
        texts_file = self.root / "script_texts.py"
        database = self.root / "app.sqlite3"
        db.init_db(database)
        db.save_template("site.first", "Моя правка", database)
        with patch.object(import_scripts, "TEXTS_FILE", texts_file), \
                patch.object(import_scripts.taxonomy, "TEMPLATES", FIXED):
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(import_scripts.main([str(source), "--db", str(database)]), 0)
            report = out.getvalue()
            self.assertIn("Пробный прогон", report)
            self.assertIn("Добавлено: 2", report)
            self.assertIn("site.second", report)
            self.assertIn("landing — Лендинг: текстов нет", report)
            self.assertIn("· site.first\n", report.split("Правлены")[1])
            self.assertFalse(texts_file.exists())
            with redirect_stdout(io.StringIO()):
                self.assertEqual(import_scripts.main([str(source), "--db", str(database), "--apply"]), 0)
        namespace = {}
        exec(texts_file.read_text(encoding="utf-8"), namespace)
        self.assertEqual(namespace["TEXTS"]["bot.first"], ("Первое · бот", "Бот для {название}: запись без звонков."))
        self.assertIn("general.meeting", namespace["TEXTS"])
        rows = {row["slug"]: row for row in db.templates(database)}
        self.assertEqual(rows["site.first"]["body"], "Моя правка")  # edited in the app: kept
        self.assertEqual(rows["site.first.booking"]["subtype"], "booking")
        self.assertEqual(rows["site.reminder"]["body"], "Здравствуйте, [имя]! Напоминаю.")


class ServiceTests(unittest.TestCase):
    def test_business_save_ignores_note(self):
        with patch.object(services.db, "set_business", return_value=True) as save, \
                patch.object(services.db, "business", return_value={"id": "1"}):
            self.assertEqual(services.business_save("1", "call", "старая заметка"), {"id": "1"})
        save.assert_called_once_with("1", "call", None)

    def test_business_scripts_has_offers(self):
        rows = FIXED_ROWS
        with patch.object(services.db, "business_full", return_value={"id": "1", "name": "Кафе", "niche": "кафе"}), \
                patch.object(services.db, "templates", return_value=rows):
            result = services.business_scripts("1")
        self.assertEqual(len(result["offers"]), 4)
        self.assertEqual(result["preferred"], "site.first")
        taxonomy = services.script_taxonomy()
        self.assertEqual(taxonomy["categories"][0]["key"], "site")
        self.assertIn("objection.own", taxonomy["stages"])


if __name__ == "__main__":
    unittest.main()


class ReviewFixTests(unittest.TestCase):
    """Cases found in review: backup merge, secondary old slugs, niche words, parser headings."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def v5(self, path, edits):
        conn = sqlite3.connect(path)
        conn.execute("""CREATE TABLE templates (slug TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT NOT NULL,
            updated_at TEXT NOT NULL, origin TEXT NOT NULL DEFAULT 'builtin')""")
        for slug, (title, body) in V5_TEMPLATES.items():
            conn.execute("INSERT INTO templates VALUES (?,?,?,'x','builtin')", (slug, title, edits.get(slug, body)))
        conn.commit()
        return conn

    def merge(self, source_path, target_path):
        from app.desktop_services import _merge_templates
        source = sqlite3.connect(source_path)
        source.row_factory = sqlite3.Row
        try:
            with db.connection(target_path) as target:
                _merge_templates(source, target)
        finally:
            source.close()

    def test_backup_secondary_slug_never_lands_in_one_tap_place(self):
        source, target = self.root / "old.sqlite3", self.root / "new.sqlite3"
        self.v5(source, {"first": "Здравствуйте, [имя]! Моя правка", "quick_unavailable": "Сайт не открывается!"}).close()
        db.init_db(target)
        self.merge(source, target)
        self.merge(source, target)  # twice: no duplicate copies
        rows = {row["slug"]: row for row in db.templates(target)}
        self.assertEqual(rows["site.first"]["body"], TEMPLATES["site.first"][1])
        self.assertEqual(rows["site.first.redesign"]["body"], TEMPLATES["site.first.redesign"][1])
        self.assertEqual(rows["custom_first"]["body"], "Здравствуйте, [имя]! Моя правка")
        self.assertEqual(rows["custom_quick_unavailable"]["origin"], "custom")
        self.assertEqual(sum(slug.startswith("custom_") for slug in rows), 2)

    def test_backup_primary_edit_lands_and_differing_copy_is_not_lost(self):
        source, target = self.root / "old.sqlite3", self.root / "new.sqlite3"
        self.v5(source, {"quick_no_site": "Мой быстрый текст", "short": "Короткое v1"}).close()
        db.init_db(target)
        with db.connection(target) as conn:
            conn.execute("""INSERT INTO templates(slug,title,body,updated_at,origin,category)
                VALUES ('custom_short','Ваша правка','Короткое v0','x','custom','site')""")
        self.merge(source, target)
        rows = {row["slug"]: row for row in db.templates(target)}
        self.assertEqual(rows["site.first"]["body"], "Мой быстрый текст")
        self.assertEqual(rows["custom_short"]["body"], "Короткое v0")
        self.assertEqual(rows["custom_short_2"]["body"], "Короткое v1")

    def test_v6_backup_with_an_older_shipped_text_is_skipped(self):
        source, target = self.root / "old.sqlite3", self.root / "new.sqlite3"
        db.init_db(source)
        with db.connection(source) as conn:  # what an earlier release shipped, untouched by the user
            conn.execute("UPDATE templates SET body='Старый встроенный',default_hash=? WHERE slug='site.call'",
                         (body_hash("Старый встроенный"),))
        db.init_db(target)
        self.merge(source, target)
        db.sync_builtin_templates(target)
        self.assertEqual(db.template("site.call", target)["body"], TEMPLATES["site.call"][1])

    def test_secondary_edit_without_primary_becomes_a_copy_and_campaign_follows(self):
        path = self.root / "t.sqlite3"
        conn = self.v5(path, {"first": "Здравствуйте, [имя]!"})
        conn.execute("DELETE FROM templates WHERE slug='quick_no_site'")
        conn.execute("CREATE TABLE campaigns (id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL, "
                     "template_slug TEXT, video_path TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("INSERT INTO campaigns VALUES ('c1','Т','draft','first',NULL,'x','x')")
        conn.commit()
        conn.close()
        db.init_db(path)
        rows = {row["slug"]: row for row in db.templates(path)}
        self.assertEqual(rows["site.first"]["body"], TEMPLATES["site.first"][1])
        self.assertEqual(rows["custom_first"]["body"], "Здравствуйте, [имя]!")
        with db.connection(path) as conn:
            self.assertEqual(conn.execute("SELECT template_slug FROM campaigns").fetchone()[0], "custom_first")

    def test_niche_words_match_at_word_start(self):
        cases = {"Барбершоп": "services", "Фитнес-клуб": "services", "Фотостудия": "portfolio",
                 "Бар у дома": "booking", "Ночной клуб": "booking", "СТО на Ленина": "services",
                 "Пиццерия": "orders", "Столовая": "booking"}
        for niche, subtype in cases.items():
            self.assertEqual(subtype_for({"niche": niche}, "site"), subtype, niche)
        self.assertEqual(subtype_for({"niche": "Ночной клуб"}, "landing"), "event")

    def test_heading_variants_bom_and_unparsed_headings(self):
        parsed = import_scripts.parse("﻿### `site.reply` Ответ\n```text\nA\n```\n"
                                      "### `site.price`: Цена\n```text\nB\n```\n#### `site.call`\n```text\nC\n```\n")
        self.assertEqual(list(parsed.scripts), ["site.reply", "site.price"])
        self.assertEqual(parsed.scripts["site.price"][0], "Цена")
        self.assertTrue(any("строка 9" in error for error in parsed.errors))

    def test_call_status_stays_in_today_written_list(self):
        from app import outreach
        self.assertIn("call", outreach.ACTIVE_STATUSES)
