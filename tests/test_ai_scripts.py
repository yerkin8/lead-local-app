import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from app import ai_scripts, config, db, services
from app.script_templates import TEMPLATES

KEY = "test-key-123"
MODELS = ("free/one:free", "free/two:free", "openrouter/free")

LEAD = {
    "id": "1", "name": "Студия Мята", "category": "Салон красоты", "city": "Казань", "niche": "салон красоты",
    "rating": 4.9, "reviews": 214, "site_type": "no_site", "site_engine": None,
    "phones": ["+7 900 000-00-00"], "socials": [{"url": "https://t.me/myata"}],
    "address": "ул. Баумана, 1", "note": "секретная заметка",
    "details": {"description": "Уютная студия маникюра", "categories": ["Салон красоты", "Ногтевая студия"]},
    "features": [{"name": "Онлайн-запись", "value": ""}],
    "review_items": [{"text": "Атмосфера супер, мастера внимательные"}],
    "links": [{"url": "https://vk.com/myata"}],
}
ASK = [{"role": "user", "content": "Первое сообщение салону"}]


def answer(text="Здравствуйте! Посмотрел вашу студию.", finish="stop"):
    return {"id": "gen-1", "choices": [{"finish_reason": finish, "message": {
        "role": "assistant", "content": text, "reasoning": "секретное рассуждение"}}]}


def response(status=200, data=None):
    item = MagicMock()
    item.status_code = status
    item.json.return_value = data if data is not None else {}
    item.text = json.dumps(data or {}, ensure_ascii=False)
    return item


def error(status, message="x"):
    return response(status, {"error": {"code": status, "message": message}})


class ChatTests(unittest.TestCase):
    def setUp(self):
        patch.object(config, "OPENROUTER_API_KEY", KEY).start()
        patch.object(config, "OPENROUTER_MODELS", MODELS).start()
        patch.object(config, "WEBAPP_URL", "https://app.example/").start()
        self.post = patch.object(ai_scripts.requests, "post").start()
        self.lead = patch.object(ai_scripts.db, "business_full",
                                 side_effect=lambda identity: LEAD if identity == "1" else None).start()
        self.addCleanup(patch.stopall)

    def body(self, call=-1):
        return self.post.call_args_list[call].kwargs["json"]

    def system(self, call=-1):
        first = self.body(call)["messages"][0]
        self.assertEqual(first["role"], "system")
        return first["content"]

    def test_without_key_nothing_is_sent(self):
        with patch.object(config, "OPENROUTER_API_KEY", ""):
            # Desktop: the message sends the user to «Настройках» (the key is saved there, not in the environment).
            with self.assertRaisesRegex(ValueError, "Ключ OpenRouter не задан"):
                ai_scripts.chat(ASK)
            with self.assertRaisesRegex(ValueError, "Ключ OpenRouter не задан"):
                ai_scripts.start(ASK)
        self.post.assert_not_called()

    def test_request_shape_and_result(self):
        self.post.return_value = response(200, answer())
        result = ai_scripts.chat(ASK, "site")
        self.assertEqual(self.post.call_args.args[0], "https://openrouter.ai/api/v1/chat/completions")
        headers = self.post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], f"Bearer {KEY}")
        self.assertEqual(headers["HTTP-Referer"], "https://app.example/")
        self.assertEqual(headers["X-OpenRouter-Title"], "LeadEqualMoney")
        self.assertEqual(headers["X-Title"], "LeadEqualMoney")
        self.assertEqual(self.post.call_args.kwargs["timeout"], (ai_scripts.CONNECT_TIMEOUT, ai_scripts.TIMEOUT))
        body = self.body()
        self.assertEqual(body["model"], "free/one:free")
        self.assertEqual(body["reasoning"]["effort"], "medium")
        self.assertGreaterEqual(body["max_tokens"], 8000)
        self.assertNotIn("response_format", body)
        self.assertEqual(body["messages"][1:], ASK)
        self.assertEqual(result, {"text": "Здравствуйте! Посмотрел вашу студию.", "model": "free/one:free"})

    def test_referer_falls_back_to_repository(self):
        self.post.return_value = response(200, answer())
        with patch.object(config, "WEBAPP_URL", ""):
            ai_scripts.chat(ASK)
        self.assertEqual(self.post.call_args.kwargs["headers"]["HTTP-Referer"], ai_scripts.REPO_URL)

    def test_context_is_facts_only(self):
        self.post.return_value = response(200, answer())
        ai_scripts.chat(ASK, "site")
        text = self.system()
        self.assertIn(ai_scripts.ABOUT, text)
        self.assertIn("Категория сообщения: Сайт", text)
        self.assertIn("бронь столов", text)
        self.assertNotIn("Бизнес (", text)
        for rule in ("JSON", "абзац", "эмодзи", "{название}", "Как писать"):
            self.assertNotIn(rule, text)
        self.assertFalse(hasattr(ai_scripts, "SYSTEM_PROMPT"))
        self.assertFalse(hasattr(ai_scripts, "RESPONSE_SCHEMA"))

    def test_every_category_builds_a_context(self):
        self.assertEqual(list(ai_scripts.CATEGORIES),
                         ["landing", "site", "bot", "automation", "ai_agent", "promotion", "general"])
        for key, (label, _, _) in ai_scripts.CATEGORIES.items():
            with self.subTest(key=key):
                self.assertIn(label, ai_scripts.context(key))

    def test_default_category_is_general_and_unknown_is_rejected(self):
        self.post.return_value = response(200, answer())
        ai_scripts.chat(ASK)
        self.assertIn("Категория сообщения: Общие", self.system())
        with self.assertRaisesRegex(ValueError, "категория"):
            ai_scripts.chat(ASK, "spam")

    def test_lead_context_has_facts_but_no_contacts(self):
        self.post.return_value = response(200, answer())
        ai_scripts.chat(ASK, "landing", "1")
        text = self.system()
        for fact in ("Студия Мята", "Ногтевая студия", "Казань", "4.9", "214", "нет сайта",
                     "Уютная студия маникюра", "Онлайн-запись", "Атмосфера супер", ai_scripts.INJECTION_GUARD):
            self.assertIn(fact, text)
        payload = json.dumps(self.body(), ensure_ascii=False)
        for secret in ("+7 900", "t.me/myata", "Баумана", "секретная заметка", "vk.com"):
            self.assertNotIn(secret, payload)

    def test_unknown_lead(self):
        with self.assertRaisesRegex(ValueError, "Организация не найдена"):
            ai_scripts.chat(ASK, None, "nope")
        self.post.assert_not_called()

    def test_history_is_validated(self):
        for bad in ([], None, "текст", [{"role": "system", "content": "x"}], [{"role": "assistant", "content": "x"}],
                    [{"role": "user", "content": "   "}], [{"role": "user", "content": "x" * 2001}]):
            with self.subTest(bad=str(bad)[:40]), self.assertRaises(ValueError):
                ai_scripts.chat(bad)
        self.post.assert_not_called()

    def test_refinement_sends_the_whole_conversation(self):
        self.post.return_value = response(200, answer("Короткий вариант"))
        history = [{"role": "user", "content": "Первое сообщение"}, {"role": "assistant", "content": "Длинный текст"},
                   {"role": "user", "content": "короче"}]
        ai_scripts.chat(history, "bot")
        self.assertEqual(self.body()["messages"][1:], history)

    def test_two_user_turns_in_a_row_are_merged(self):
        self.post.return_value = response(200, answer())
        ai_scripts.chat([{"role": "user", "content": "раз"}, {"role": "user", "content": "два"}])
        self.assertEqual(self.body()["messages"][1:], [{"role": "user", "content": "раз\n\nдва"}])

    def test_long_history_drops_oldest_turns_and_starts_with_user(self):
        history = []
        for n in range(10):
            history += [{"role": "user", "content": f"вопрос {n} " + "у" * 1000},
                        {"role": "assistant", "content": f"ответ {n} " + "о" * 2000}]
        history.append({"role": "user", "content": "последний"})
        kept = ai_scripts.clean_history(history)
        self.assertLessEqual(sum(len(t["content"]) for t in kept), ai_scripts.MAX_HISTORY)
        self.assertEqual(kept[0]["role"], "user")
        self.assertEqual(kept[-1], {"role": "user", "content": "последний"})
        self.assertLess(len(kept), len(history))

    def test_reasoning_and_think_tags_are_not_returned(self):
        for raw in ("<think>план</think>\nЗдравствуйте!", "план без начала</think>Здравствуйте!",
                    "Здравствуйте!<think>обрывок мысли"):
            self.post.side_effect = None
            self.post.return_value = response(200, answer(raw))
            with self.subTest(raw=raw):
                self.assertEqual(ai_scripts.chat(ASK)["text"], "Здравствуйте!")

    def test_only_a_cut_thought_counts_as_empty(self):
        self.post.side_effect = [response(200, answer("<think>думаю, думаю")), response(200, answer())]
        self.assertEqual(ai_scripts.chat(ASK)["model"], "free/two:free")

    def test_later_models_get_what_is_left_of_the_deadline(self):
        self.post.side_effect = [error(503), response(200, answer())]
        clock = iter([0, 0, ai_scripts.DEADLINE - 100])
        with patch.object(ai_scripts.time, "monotonic", side_effect=lambda: next(clock)):
            ai_scripts.chat(ASK)
        self.assertEqual([c.kwargs["timeout"][1] for c in self.post.call_args_list], [ai_scripts.TIMEOUT, 100])

    def test_429_and_5xx_move_to_the_next_model(self):
        for status in (429, 500, 502, 503, 504):
            self.post.reset_mock()
            self.post.side_effect = [error(status), response(200, answer())]
            with self.subTest(status=status):
                self.assertEqual(ai_scripts.chat(ASK)["model"], "free/two:free")
                self.assertEqual(self.body(1)["model"], "free/two:free")

    def test_timeout_missing_model_and_empty_reply_move_on(self):
        self.post.side_effect = [requests.Timeout("slow"), error(404, "No endpoints found"),
                                 response(200, answer("   ")), response(200, answer())]
        with patch.object(config, "OPENROUTER_MODELS", MODELS + ("last:free",)):
            self.assertEqual(ai_scripts.chat(ASK)["model"], "last:free")

    def test_error_inside_a_200_body_moves_on(self):
        self.post.side_effect = [response(200, {"error": {"code": 502, "message": "Provider returned error"}}),
                                 response(200, answer())]
        self.assertEqual(ai_scripts.chat(ASK)["model"], "free/two:free")

    def test_account_limit_stops_at_once_and_explains(self):
        self.post.side_effect = [error(429, "Rate limit exceeded: free-models-per-day. Add 10 credits")] * 3
        with self.assertRaises(ValueError) as caught:
            ai_scripts.chat(ASK)
        for part in ("20 запросов в минуту", "50 в день", "$10", "1000"):
            self.assertIn(part, str(caught.exception))
        self.assertEqual(self.post.call_count, 1)

    def test_upstream_429_moves_on_and_is_not_called_our_limit(self):
        upstream = response(429, {"error": {"code": 429, "message": "Provider returned error", "metadata": {
            "raw": "google/gemma-4-31b-it:free is temporarily rate-limited upstream", "provider_name": "Google"}}})
        self.post.side_effect = [upstream] * 3
        with self.assertRaises(ValueError) as caught:
            ai_scripts.chat(ASK)
        self.assertEqual(str(caught.exception), ai_scripts.BUSY)
        self.assertEqual(self.post.call_count, 3)

    def test_limit_wins_over_busy(self):
        self.post.side_effect = [error(503), error(429, "Rate limit exceeded: free-models-per-min")]
        with self.assertRaisesRegex(ValueError, "Лимит бесплатных моделей"):
            ai_scripts.chat(ASK)
        self.assertEqual(self.post.call_count, 2)

    def test_data_policy_stops_and_points_to_privacy_settings(self):
        self.post.side_effect = [error(404, "No endpoints found matching your data policy (Free model publication)")] * 3
        with self.assertRaisesRegex(ValueError, "settings/privacy"):
            ai_scripts.chat(ASK)
        self.assertEqual(self.post.call_count, 1)

    def test_bad_model_id_moves_on(self):
        self.post.side_effect = [error(400, "free/one:free is not a valid model ID"), response(200, answer())]
        self.assertEqual(ai_scripts.chat(ASK)["model"], "free/two:free")

    def test_key_is_masked_in_a_non_json_error(self):
        broken = response(400)
        broken.json.side_effect = ValueError("not json")
        broken.text = f"<html>bad gateway for {KEY}</html>"
        self.post.return_value = broken
        with self.assertRaises(ValueError) as caught:
            ai_scripts.chat(ASK)
        self.assertNotIn(KEY, str(caught.exception))
        self.assertIn("***", str(caught.exception))

    def test_model_closed_to_this_app_moves_on(self):
        self.post.side_effect = [error(403, "thinkingmachines/inkling:free is only available on agentic harnesses"),
                                 response(200, answer())]
        self.assertEqual(ai_scripts.chat(ASK)["model"], "free/two:free")

    def test_everything_busy_or_empty(self):
        self.post.side_effect = [error(503), requests.Timeout("x"), error(502)]
        with self.assertRaises(ValueError) as caught:
            ai_scripts.chat(ASK)
        self.assertEqual(str(caught.exception), ai_scripts.BUSY)
        self.post.reset_mock()
        self.post.side_effect = [response(200, answer(""))] * 3
        with self.assertRaises(ValueError) as caught:
            ai_scripts.chat(ASK)
        self.assertEqual(str(caught.exception), ai_scripts.EMPTY)

    def test_all_models_missing(self):
        self.post.side_effect = [error(404, "No endpoints found")] * 3
        with self.assertRaisesRegex(ValueError, "OPENROUTER_MODELS"):
            ai_scripts.chat(ASK)

    def test_finish_length_is_explained(self):
        self.post.side_effect = [response(200, answer("Здравс", finish="length"))] * 3
        with self.assertRaises(ValueError) as caught:
            ai_scripts.chat(ASK)
        self.assertEqual(str(caught.exception), ai_scripts.TRUNCATED)

    def test_account_errors_stop_at_once(self):
        for status, expected in ((401, "Ключ OpenRouter не подходит"), (402, "балансе"), (403, "отказалась"),
                                 (400, "Ошибка OpenRouter \\(400\\)")):
            self.post.reset_mock()
            self.post.side_effect = None
            self.post.return_value = error(status, f"bad thing for {KEY}")
            if status == 403:
                self.post.return_value = error(403, f"Input was flagged by moderation, key {KEY}")
            with self.subTest(status=status):
                with self.assertRaisesRegex(ValueError, expected) as caught:
                    ai_scripts.chat(ASK)
                self.assertNotIn(KEY, str(caught.exception))
                self.assertEqual(self.post.call_count, 1)

    def test_deadline_stops_the_chain(self):
        self.post.side_effect = [error(503), response(200, answer())]
        clock = iter([0, 0, ai_scripts.DEADLINE + 1])
        with patch.object(ai_scripts.time, "monotonic", side_effect=lambda: next(clock)):
            with self.assertRaisesRegex(ValueError, "перегружены"):
                ai_scripts.chat(ASK)
        self.assertEqual(self.post.call_count, 1)

    def test_status_never_returns_key(self):
        status = ai_scripts.status()
        self.assertTrue(status["configured"])
        self.assertEqual(status["models"], list(MODELS))
        self.assertEqual(len(status["categories"]), 7)
        self.assertNotIn(KEY, json.dumps(status))
        with patch.object(config, "OPENROUTER_API_KEY", ""):
            self.assertFalse(ai_scripts.status()["configured"])

    def test_models_setting_is_parsed(self):
        import importlib
        with patch.dict("os.environ", {"OPENROUTER_MODELS": " a:free , b:free,,a:free "}):
            fresh = importlib.reload(config)
            self.assertEqual(fresh.OPENROUTER_MODELS, ("a:free", "b:free"))
        with patch.dict("os.environ", {"OPENROUTER_MODELS": ""}):
            fresh = importlib.reload(config)
            self.assertEqual(fresh.OPENROUTER_MODELS[-1], "openrouter/free")
        importlib.reload(config)


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        patch.object(config, "OPENROUTER_API_KEY", KEY).start()
        for _ in range(300):  # threads of an earlier test may still be finishing
            if not ai_scripts._running:
                break
            time.sleep(0.01)
        ai_scripts._tasks.clear()
        self.addCleanup(patch.stopall)
        self.addCleanup(ai_scripts._tasks.clear)

    def wait(self, task_id, owner="me"):
        for _ in range(300):
            reply = ai_scripts.result(task_id, owner)
            if reply["status"] != "running":
                return reply
            time.sleep(0.01)
        self.fail("the reply never finished")

    def test_start_returns_at_once_and_result_survives_a_lost_poll(self):
        gate = threading.Event()

        def slow(*args):
            gate.wait(2)
            return {"text": "Готово", "model": "m"}

        with patch.object(ai_scripts, "chat", side_effect=slow):
            task = ai_scripts.start(ASK, "site", None, "me")
            self.assertEqual(ai_scripts.result(task["id"], "me")["status"], "running")
            gate.set()
            reply = self.wait(task["id"])
        self.assertEqual((reply["status"], reply["text"], reply["model"]), ("done", "Готово", "m"))
        self.assertEqual(ai_scripts.result(task["id"], "me")["text"], "Готово")  # asked again: still there

    def test_a_new_turn_replaces_the_owners_old_one(self):
        gate = threading.Event()
        self.addCleanup(gate.set)

        def slow(history, *args):
            text = history[-1]["content"]
            if text != "новый":
                gate.wait(2)
            return {"text": text, "model": "m"}

        with patch.object(ai_scripts, "chat", side_effect=slow):
            old = ai_scripts.start([{"role": "user", "content": "старый"}], None, None, "me")
            other = ai_scripts.start([{"role": "user", "content": "чужой"}], None, None, "someone else")
            new = ai_scripts.start([{"role": "user", "content": "новый"}], None, None, "me")
            with self.assertRaisesRegex(ValueError, "потерялся"):
                ai_scripts.result(old["id"], "me")
            self.assertEqual(self.wait(new["id"])["text"], "новый")
            gate.set()
            self.assertEqual(self.wait(other["id"], "someone else")["status"], "done")
        with self.assertRaisesRegex(ValueError, "потерялся"):
            ai_scripts.result(old["id"], "me")

    def test_too_many_replies_at_once(self):
        gate = threading.Event()
        with patch.object(ai_scripts, "chat", side_effect=lambda *a: gate.wait(2) and {"text": "x", "model": "m"}):
            ids = [ai_scripts.start(ASK, None, None, f"owner{n}")["id"] for n in range(ai_scripts.MAX_RUNNING)]
            with self.assertRaisesRegex(ValueError, "слишком много"):
                ai_scripts.start(ASK, None, None, "late")
            gate.set()
            for n, task_id in enumerate(ids):
                self.wait(task_id, f"owner{n}")
        self.assertEqual(ai_scripts._running, 0)

    def test_english_errors_are_not_shown(self):
        with patch.object(ai_scripts, "chat", side_effect=ValueError("latin-1 codec can't encode")):
            reply = self.wait(ai_scripts.start(ASK, None, None, "me")["id"])
        self.assertEqual(reply["status"], "error")
        self.assertNotIn("codec", reply["error"])

    def test_errors_come_back_as_text(self):
        with patch.object(ai_scripts, "chat", side_effect=ValueError(ai_scripts.BUSY)):
            reply = self.wait(ai_scripts.start(ASK, None, None, "me")["id"])
        self.assertEqual((reply["status"], reply["error"]), ("error", ai_scripts.BUSY))
        with patch.object(ai_scripts, "chat", side_effect=RuntimeError("boom")), self.assertLogs(ai_scripts.log, "ERROR"):
            reply = self.wait(ai_scripts.start(ASK, None, None, "me")["id"])
        self.assertEqual(reply["status"], "error")
        self.assertNotIn("boom", reply["error"])

    def test_bad_input_fails_before_any_thread(self):
        with patch.object(ai_scripts, "chat") as chat:
            for args in (([],), (ASK, "spam")):
                with self.assertRaises(ValueError):
                    ai_scripts.start(*args)
        chat.assert_not_called()
        self.assertEqual(ai_scripts._tasks, {})

    def test_another_owner_cannot_read_the_reply(self):
        with patch.object(ai_scripts, "chat", return_value={"text": "x", "model": "m"}):
            task = ai_scripts.start(ASK, None, None, "tg:1")
            with self.assertRaisesRegex(ValueError, "потерялся"):
                ai_scripts.result(task["id"], "tg:2")
            self.assertEqual(self.wait(task["id"], "tg:1")["status"], "done")


class TemplateStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.tmp.name) / "t.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def test_old_table_gets_origin_and_keeps_user_edits(self):
        conn = sqlite3.connect(self.path)
        conn.execute("CREATE TABLE templates (slug TEXT PRIMARY KEY, title TEXT NOT NULL, "
                     "body TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.execute("INSERT INTO templates VALUES ('quick_no_site','Быстрое','Мой текст','2026-01-01')")
        conn.commit()
        conn.close()
        db.init_db(self.path)
        rows = {row["slug"]: row for row in db.templates(self.path)}
        self.assertEqual(rows["site.first"]["body"], "Мой текст")  # old slug renamed, edit kept
        self.assertEqual({row["origin"] for row in rows.values()}, {"builtin"})
        self.assertEqual(len(rows), len(TEMPLATES))

    def test_ai_rows_follow_builtins_newest_first(self):
        db.init_db(self.path)
        with db.connection(self.path) as conn:
            for slug in ("ai_20260101000000_aaaa", "ai_20260102000000_bbbb"):
                conn.execute("INSERT INTO templates(slug,title,body,updated_at,origin) VALUES (?,?,?,?,'ai')",
                             (slug, slug, "Текст", db.now()))
        slugs = [row["slug"] for row in db.templates(self.path)]
        self.assertEqual(slugs[:len(TEMPLATES)], list(TEMPLATES))
        self.assertEqual(slugs[len(TEMPLATES):], ["ai_20260102000000_bbbb", "ai_20260101000000_aaaa"])

    def test_add_delete_and_reinit(self):
        db.init_db(self.path)
        slug = db.add_ai_template("Без сайта", "Здравствуйте!", self.path)
        self.assertRegex(slug, r"^ai_\d{14}_[0-9a-f]{4}$")
        db.init_db(self.path)
        saved = [row for row in db.templates(self.path) if row["slug"] == slug]
        self.assertEqual([(row["title"], row["body"], row["origin"]) for row in saved],
                         [("Без сайта", "Здравствуйте!", "ai")])
        self.assertFalse(db.delete_template("site.first", self.path))
        self.assertTrue(any(row["slug"] == "site.first" for row in db.templates(self.path)))
        self.assertTrue(db.delete_template(slug, self.path))
        self.assertFalse(any(row["slug"] == slug for row in db.templates(self.path)))

    def test_v5_desktop_database_is_backed_up_before_category_columns(self):
        self.assertGreaterEqual(db.DESKTOP_SCHEMA_VERSION, 6)  # Desktop: 7
        db.init_desktop_db(self.path)
        conn = sqlite3.connect(self.path)
        try:  # turn the fresh DB into what version 5 left on disk
            for column in db.TEMPLATE_COLUMNS:
                conn.execute(f"ALTER TABLE templates DROP COLUMN {column}")
            conn.execute("DELETE FROM schema_migrations WHERE version>5")
            conn.commit()
        finally:
            conn.close()
        db.init_desktop_db(self.path)
        backups = list((self.path.parent / "backups").glob(f"t-pre-schema-v{db.DESKTOP_SCHEMA_VERSION}-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        old = sqlite3.connect(backups[0])
        try:
            columns = {row[1] for row in old.execute("PRAGMA table_info(templates)")}
        finally:
            old.close()
        self.assertNotIn("category", columns)
        with db.connection(self.path) as live:
            self.assertEqual(live.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0], db.DESKTOP_SCHEMA_VERSION)
            self.assertIn("category", {row[1] for row in live.execute("PRAGMA table_info(templates)")})
        db.init_desktop_db(self.path)  # already v6: no second backup
        self.assertEqual(len(list((self.path.parent / "backups").glob(f"t-pre-schema-v{db.DESKTOP_SCHEMA_VERSION}-*.sqlite3"))), 1)


class AiServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.path = Path(self.tmp.name) / "s.sqlite3"
        db.init_db(self.path)
        for name in ("templates", "add_ai_template", "delete_template"):
            original = getattr(db, name)
            patch.object(db, name, side_effect=lambda *a, _o=original, **k: _o(*a, path=self.path, **k)).start()
        self.addCleanup(patch.stopall)
        self.addCleanup(self.tmp.cleanup)

    def test_methods_are_registered_for_both_transports(self):
        for name in ("ai_status", "ai_chat_start", "ai_chat_result", "ai_script_save", "template_delete"):
            self.assertIn(name, services.WEB_METHODS)

    def test_save_validates_and_normalises(self):
        slug = services.ai_script_save("  Без   сайта ", "  Здравствуйте!  ")
        row = next(row for row in services.templates() if row["slug"] == slug)
        self.assertEqual((row["title"], row["body"], row["origin"]), ("Без сайта", "Здравствуйте!", "ai"))
        for title, body in (("", "текст"), ("x" * 61, "текст"), ("Название", "   "), ("Название", "x" * 20001)):
            with self.subTest(title=title[:5], body=body[:5]), self.assertRaises(ValueError):
                services.ai_script_save(title, body)

    def test_save_puts_the_script_into_the_chat_category(self):
        for category, expected in (("bot", "bot"), ("ai_agent", "ai_agent"), (None, "general"), ("nope", "general")):
            with self.subTest(category=category):
                slug = services.ai_script_save("Название", "Текст", category)
                row = next(row for row in services.templates() if row["slug"] == slug)
                self.assertEqual((row["category"], row["origin"]), (expected, "ai"))

    def test_delete_only_ai(self):
        slug = services.ai_script_save("Название", "Текст")
        with self.assertRaisesRegex(ValueError, "только ИИ-скрипт"):
            services.template_delete("first")
        self.assertTrue(services.template_delete(slug))

    def test_chat_start_passes_empty_choices_as_none_and_the_actor(self):
        with patch.object(services.ai_scripts, "start", return_value={"id": "a"}) as start:
            services.ai_chat_start(ASK, "", "")
        start.assert_called_once_with(ASK, None, None, "desktop")
        token = services.ACTOR.set("tg:5")
        try:
            with patch.object(services.ai_scripts, "result", return_value={"status": "running"}) as result:
                services.ai_chat_result("a")
            result.assert_called_once_with("a", "tg:5")
        finally:
            services.ACTOR.reset(token)

if __name__ == "__main__":
    unittest.main()
