"""One job runner for the desktop thread and the server worker process."""

from __future__ import annotations

import logging
from pathlib import Path

from . import db
from .config import DB_PATH, MEDIA_DIR
from .errors import YandexBlocked

log = logging.getLogger(__name__)
KINDS = ("search", "images", "products", "cards")


def recover_interrupted(path: Path = DB_PATH) -> None:
    """Requeue work cut off by a restart. An interrupted send is never repeated automatically."""
    with db.connection(path) as conn:
        conn.execute("UPDATE jobs SET status='queued',updated_at=? WHERE status='running' "
                     "AND kind IN ('search','images','products','cards')", (db.now(),))
        conn.execute("UPDATE parse_runs SET status='queued',ended_at=NULL WHERE status='running'")
        conn.execute("""UPDATE campaigns SET status='interrupted',
            error='Приложение закрылось во время рассылки; проверьте результат вручную',updated_at=?
            WHERE status IN ('queued','running')""", (db.now(),))
        conn.execute("""UPDATE campaign_recipients SET status='uncertain',
            error='Приложение закрылось во время отправки; проверьте чат вручную'
            WHERE status='sending'""")


def _runners() -> dict:
    from .desktop_gallery import run_gallery_images
    from .lead_parser import run_search
    from .media import run_products
    from .cards import run_cards
    return {"search": run_search, "images": run_gallery_images, "products": run_products, "cards": run_cards}


def package_gallery(job_id: str, result: dict) -> dict:
    """Zip a finished gallery folder next to the job so the bot can send it (≤45 MB parts)."""
    from .media import job_folder, split_archives
    source = (MEDIA_DIR / str(result.get("folder", ""))).resolve()
    if not result.get("folder") or MEDIA_DIR.resolve() not in source.parents or not source.is_dir():
        return result
    files = sorted(item for item in source.rglob("*") if item.is_file())
    target = job_folder(job_id)
    target.mkdir(parents=True, exist_ok=True)
    archives = split_archives(target, files, base=source)
    return {**result, "archives": [item.name for item in archives]}


def run_job(item: dict, *, package_archives: bool = False, runners: dict | None = None) -> str:
    """Run one claimed job with details mode on and return its final status."""
    from . import cancel
    runners = runners or _runners()
    kind, job_id = item["kind"], item["id"]
    run_id = item["params"].get("_desktop_parse_run_id") if kind == "search" else None
    cancel.bind(job_id)
    try:
        status = _run_bound(item, kind, job_id, run_id, runners, package_archives)
    finally:
        cancel.unbind()
    if run_id:
        db.finish_parse_run(run_id, {"done": "done", "blocked": "stopped", "stopped": "stopped", "interrupted": "stopped"}.get(status, "failed"))
    return status


def _run_bound(item: dict, kind: str, job_id: str, run_id: str | None, runners: dict,
               package_archives: bool) -> str:
    from . import cancel
    try:
        if kind not in runners:
            raise ValueError("Неизвестный тип задания")
        if kind == "search":
            item["params"]["_desktop_details"] = True
            if run_id:
                db.start_parse_run(run_id)
        result = runners[kind](item)
        if kind == "images" and package_archives:
            result = package_gallery(job_id, result)
        status = ("blocked" if isinstance(result, dict) and result.get("blocked")
                  else "interrupted" if isinstance(result, dict) and result.get("interrupted") else "done")
        db.update_job(job_id, status=status, result=result)
    except cancel.Cancelled as exc:
        # Found cards stay in «Лиды»; the job shows how many were saved before the stop.
        status = "stopped"
        saved = {"matched": db.job_result_count(job_id)} if kind == "search" else {}
        if kind == "products":
            from .media import delete_media_files
            delete_media_files(job_id)
        db.update_job(job_id, status=status, error=str(exc), result={"partial": True, **saved,
                                                                     "reason": str(exc)})
    except Exception as exc:
        status = "blocked" if isinstance(exc, YandexBlocked) else "failed"
        if status == "failed":
            log.exception("Job %s failed", job_id)
        if kind == "products":
            from .media import delete_media_files
            delete_media_files(job_id)
        db.update_job(job_id, status=status, error=str(exc)[:1000])
    return status
