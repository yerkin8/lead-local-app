import tempfile
import unittest
from pathlib import Path

from app import db


class ActivitySnapshotTests(unittest.TestCase):
    def test_returns_active_tasks_and_only_previously_tracked_finished_ids(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "app.sqlite3"
            db.init_db(path)
            stamp = db.now()
            with db.connection(path) as conn:
                for identity, kind, status in (
                    ("search-active", "search", "running"),
                    ("search-finished", "search", "done"),
                    ("media-active", "images", "queued"),
                ):
                    conn.execute(
                        "INSERT INTO jobs (id,kind,status,params,progress,result,created_at,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?)",
                        (identity, kind, status, "{}", "{}", "{}", stamp, stamp),
                    )
                conn.execute("INSERT INTO media_links (job_id,organization_id) VALUES (?,?)",
                             ("media-active", "org-1"))
                conn.execute(
                    "INSERT INTO campaigns (id,title,status,created_at,updated_at) VALUES (?,?,?,?,?)",
                    ("campaign-active", "Test", "running", stamp, stamp),
                )

            initial = db.activity_snapshot(path=path)
            self.assertEqual(initial["active_count"], 3)
            self.assertEqual({item["id"] for item in initial["search"]}, {"search-active"})
            self.assertEqual({item["id"] for item in initial["media"]}, {"media-active"})
            self.assertEqual({item["id"] for item in initial["campaigns"]}, {"campaign-active"})

            with db.connection(path) as conn:
                conn.execute("UPDATE jobs SET status='done',updated_at=? WHERE id='search-active'", (db.now(),))
            finished = db.activity_snapshot({"search": ["search-active"]}, path)
            self.assertEqual(finished["active_count"], 2)
            self.assertEqual(finished["search"][0]["status"], "done")

    def test_reads_only_selected_topics_and_returns_only_changed_rows(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            path = Path(folder) / "app.sqlite3"
            db.init_db(path)
            stamp = db.now()
            with db.connection(path) as conn:
                conn.execute(
                    "INSERT INTO jobs (id,kind,status,params,progress,result,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    ("search-active", "search", "running", "{}", "{}", "{}", stamp, stamp),
                )
                conn.execute(
                    "INSERT INTO jobs (id,kind,status,params,progress,result,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    ("media-active", "images", "running", "{}", "{}", "{}", stamp, stamp),
                )
                conn.execute("INSERT INTO media_links (job_id,organization_id) VALUES (?,?)",
                             ("media-active", "org-1"))

            first = db.activity_snapshot({"topics": ["search"]}, path)
            self.assertEqual(first["active_count"], 1)
            self.assertEqual([item["id"] for item in first["search"]], ["search-active"])
            self.assertEqual(first["media"], [])
            self.assertEqual(first["campaigns"], [])

            known = {"topics": ["search"], "search": {"search-active": first["search"][0]["revision"]}}
            unchanged = db.activity_snapshot(known, path)
            self.assertEqual(unchanged["active_count"], 1)
            self.assertEqual(unchanged["search"], [])

            with db.connection(path) as conn:
                conn.execute("UPDATE jobs SET progress=?,updated_at=? WHERE id='search-active'",
                             ('{"scanned":1}', db.now()))
            changed = db.activity_snapshot(known, path)
            self.assertEqual(changed["active_count"], 1)
            self.assertEqual([item["id"] for item in changed["search"]], ["search-active"])
            self.assertNotEqual(changed["search"][0]["revision"], first["search"][0]["revision"])


if __name__ == "__main__":
    unittest.main()
