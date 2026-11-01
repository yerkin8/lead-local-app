import importlib.util
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from app import import_scripts
from app.script_templates import TEMPLATES

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("import_rewritten_scripts", ROOT / "scripts" / "import_rewritten_scripts.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

FIXTURE = {
    "site.first": ("Первое · общее", "Здравствуйте! Нашёл {название} в картах."),
    "general.meeting": ("Созвон", "Созвонимся [время]?"),
}


def md(texts):
    return tool.export(texts)


class ImportRewrittenScriptsTest(unittest.TestCase):
    def check(self, texts):
        parsed = import_scripts.parse(md(texts))
        return tool.validate(parsed, FIXTURE)

    def test_roundtrip_of_fixture_is_clean(self):
        errors, warnings = self.check(FIXTURE)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_full_export_roundtrips_without_changes(self):
        parsed = import_scripts.parse(md(dict(TEMPLATES)))
        self.assertEqual(parsed.scripts, dict(TEMPLATES))

    def test_missing_unknown_and_placeholder_changes_are_errors(self):
        texts = {"site.first": ("Первое · общее", "Добрый день! Пишу вам, {имя}."),
                 "site.reminder": ("Напоминание", "Здравствуйте!")}
        errors, _ = self.check(texts)
        joined = "\n".join(errors)
        self.assertIn("нет скрипта `general.meeting`", joined)
        self.assertIn("неизвестный slug `site.reminder`", joined)
        self.assertIn("подстановки изменились", joined)

    def test_long_text_warns(self):
        long = "Добрый день! " + "Очень живой рассказ про {название}. " + "слово " * 400
        _, warnings = self.check({**FIXTURE, "site.first": ("Первое · общее", long)})
        self.assertTrue(any("> 1800" in w for w in warnings))
        self.assertTrue(any("K4" in w for w in warnings))

    def test_apply_writes_only_given_texts_file(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "in.md"
            target = Path(folder) / "texts.py"
            source.write_text(md(dict(TEMPLATES)), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                code = tool.main([str(source), "--apply", "--texts-file", str(target),
                                  "--db", str(Path(folder) / "none.db")])
            self.assertEqual(code, 0)
            namespace = {}
            exec(target.read_text(encoding="utf-8"), namespace)
            self.assertEqual(namespace["TEXTS"], dict(TEMPLATES))


if __name__ == "__main__":
    unittest.main()
