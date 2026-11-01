from __future__ import annotations

import logging
import time

from . import db
from .bot import notify_finished
from .lead_parser import run_search
from .media import delete_media_files, run_images, run_products

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def process_one(item: dict) -> None:
    job_id = item["id"]
    try:
        if item["kind"] == "search":
            result = run_search(item)
        elif item["kind"] == "images":
            result = run_images(item)
        elif item["kind"] == "products":
            result = run_products(item)
        else:
            raise ValueError("Неизвестный тип задания")
        db.update_job(job_id, status="done", result=result)
    except Exception as exc:
        log.exception("Job %s failed", job_id)
        if item["kind"] in {"images", "products"}:
            delete_media_files(job_id)
        db.update_job(job_id, status="failed", error=str(exc)[:1000])
    if item["kind"] in {"images", "products"}:
        completed = db.job(job_id)
        if completed:
            notify_finished(completed)


def main() -> None:
    db.init_db()
    # A single worker is required. Jobs interrupted by a restart can run again.
    with db.connection() as conn:
        conn.execute("UPDATE jobs SET status='queued',updated_at=? WHERE status='running'", (db.now(),))
    while True:
        item = db.claim_job()
        if item:
            process_one(item)
        else:
            time.sleep(2)


if __name__ == "__main__":
    main()
