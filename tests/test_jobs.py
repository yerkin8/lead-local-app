import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


class SharedWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory(dir=HERE) as data_dir:
            env = {**os.environ, "DATA_DIR": data_dir, "PYTHONIOENCODING": "utf-8"}
            done = subprocess.run([sys.executable, str(HERE / "jobs_case.py")], env=env,
                                  capture_output=True, text=True, encoding="utf-8", timeout=120)
        if done.returncode:
            raise AssertionError(done.stderr[-3000:])
        cls.r = json.loads(done.stdout.strip().splitlines()[-1])

    def test_server_database_migrates_with_backup(self):
        self.assertEqual(self.r["backups"], 1)
        self.assertEqual(self.r["backups_after_second_start"], 1)
        self.assertEqual(self.r["kept"], ["waiting", "заметка"])

    def test_search_runs_in_details_mode_and_reports_block(self):
        self.assertTrue(self.r["details"])
        self.assertEqual(self.r["search_status"], "blocked")
        self.assertEqual(self.r["run_status"], "stopped")

    def test_gallery_block_is_resumable(self):
        self.assertEqual(self.r["gallery_blocked"], "blocked")
        self.assertEqual(self.r["resume"], "queued")

    def test_server_gallery_is_zipped_for_bot(self):
        self.assertEqual(self.r["gallery_done"], "done")
        self.assertEqual(self.r["archives"], ["archive-01.zip"])
        self.assertEqual(self.r["zip_names"], ["Внутри/2.jpg", "Еда/1.jpg"])
        self.assertTrue(self.r["archive_path_ok"])

    def test_unknown_kind_and_restart(self):
        self.assertEqual(self.r["weird"], "failed")
        self.assertEqual(self.r["requeued"], "queued")


if __name__ == "__main__":
    unittest.main()
