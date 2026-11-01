"""The user's OpenRouter key (app/secret_store.py): DPAPI file, never returned to the page or logged."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FAKE = "sk-or-v1-" + "0123456789abcdef" * 4   # format only, not a real key


def run(code: str, data_dir: str, env_key: str = "") -> dict:
    env = {**os.environ, "DATA_DIR": data_dir, "PYTHONIOENCODING": "utf-8", "OPENROUTER_API_KEY": env_key}
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                         encoding="utf-8", timeout=120)
    if out.returncode:
        raise AssertionError(out.stderr[-3000:])
    return json.loads(out.stdout.strip().splitlines()[-1])


@unittest.skipUnless(os.name == "nt", "DPAPI is Windows-only")
class AiKeyTests(unittest.TestCase):
    def test_saved_key_is_encrypted_used_without_restart_and_never_returned(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run(f"""
import json
from app import services, ai_scripts, secret_store
before = services.ai_status()
status = services.ai_key_save({FAKE!r})
raw = secret_store.KEY_FILE.read_bytes()
print(json.dumps({{
    'before': before['configured'], 'after': status['configured'], 'source': status['key_source'],
    'returned': {FAKE!r} in json.dumps(status), 'plain_on_disk': {FAKE!r}.encode() in raw,
    'used': ai_scripts.api_key() == {FAKE!r},
    'cleared': services.ai_key_clear()['configured']}}))
""", tmp)
        self.assertEqual(result, {"before": False, "after": True, "source": "saved", "returned": False,
                                  "plain_on_disk": False, "used": True, "cleared": False})

    def test_wrong_format_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run("""
import json
from app import services
try:
    services.ai_key_save('not-a-key'); print(json.dumps('saved'))
except ValueError as error:
    print(json.dumps(str(error)))
""", tmp)
        self.assertIn("sk-or-v1-", result)

    def test_environment_key_wins_and_is_reported_as_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run("""
import json
from app import ai_scripts
print(json.dumps([ai_scripts.api_key() != '', ai_scripts.status()['key_source']]))
""", tmp, env_key=FAKE)
        self.assertEqual(result, [True, "env"])

    def test_no_key_message_points_to_settings(self):
        from app import ai_scripts
        self.assertIn("Настройках", ai_scripts.NO_KEY)
        self.assertNotIn("переменн", ai_scripts.NO_KEY)


class MaskTests(unittest.TestCase):
    def test_key_is_masked(self):
        from app.secret_store import mask
        self.assertEqual(mask(f"401 for {FAKE}"), "401 for sk-or-v1-***")


if __name__ == "__main__":
    unittest.main()
