"""Local media workflow shared by the desktop UI and its job runner."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import db
from .domain import org_id
from .config import MEDIA_DIR
from .media import MediaError, delete_media_files, job_folder, validate_org_url


def _resolve_url(business_id: str | None, url: str | None) -> tuple[str, str, str | None]:
    if (business_id is None) == (url is None):
        raise MediaError("Выберите организацию или вставьте ссылку")
    if business_id is not None:
        business = db.business(business_id)
        if not business:
            raise MediaError("Организация не найдена")
        url = business["map_url"]
    validated = validate_org_url(url or "")
    identity = org_id(validated)
    if not identity:
        raise MediaError("В ссылке нет ID организации")
    if business_id is None and db.business(identity):
        business_id = identity
    return validated, identity, business_id


def _gallery_request(selection: object, sections: object, per_section_limit: object,
                     organization_limit: object) -> dict:
    """Normalize new dynamic selections and old saved section-count maps."""
    from .desktop_gallery import normalize_section_name, normalize_selection

    per_section_limits = None
    if selection is None and isinstance(sections, dict):
        names = []
        per_section_limits = {}
        for raw_name, raw_count in sections.items():
            name = str(raw_name).strip()
            if (not name or len(name) > 100 or isinstance(raw_count, bool)
                    or not isinstance(raw_count, int) or not 1 <= raw_count <= 50):
                raise MediaError("Сохранённый список разделов содержит неверные значения")
            names.append(name)
            per_section_limits[normalize_section_name(name).casefold()] = raw_count
        selection = {"mode": "selected", "names": names}
        per_section_limit = max(per_section_limits.values(), default=10)
    try:
        if selection is None:
            raise ValueError("Сначала найдите разделы галереи")
        if isinstance(per_section_limit, bool) or isinstance(organization_limit, bool):
            raise ValueError("Лимиты должны быть целыми числами")
        normalized = normalize_selection(selection)
        if not isinstance(per_section_limit, int) and not (
                isinstance(per_section_limit, str) and per_section_limit.strip().isdecimal()):
            raise ValueError("Лимит фотографий на раздел должен быть целым числом")
        if not isinstance(organization_limit, int) and not (
                isinstance(organization_limit, str) and organization_limit.strip().isdecimal()):
            raise ValueError("Общий лимит фотографий должен быть целым числом")
        section_limit = int(per_section_limit)
        total_limit = int(organization_limit)
    except (TypeError, ValueError) as error:
        raise MediaError(str(error) or "Проверьте выбор разделов галереи") from error
    if not 1 <= section_limit <= 50:
        raise MediaError("Лимит фотографий на раздел должен быть от 1 до 50")
    if not 1 <= total_limit <= 200:
        raise MediaError("Общий лимит фотографий должен быть от 1 до 200")
    return {"selection": normalized, "per_section_limit": section_limit,
            "organization_limit": total_limit, "per_section_limits": per_section_limits or {}}


def start(kind: str, *, business_id: str | None = None, url: str | None = None,
          sections: dict[str, int] | None = None, selection: object = None,
          per_section_limit: object = 10, organization_limit: object = 50,
          repeat: bool = False) -> dict:
    validated, identity, business_id = _resolve_url(business_id, url)
    if kind == "images":
        request = _gallery_request(selection, sections, per_section_limit, organization_limit)
        params = {"url": validated, **request}
    elif kind == "products":
        params = {"url": validated}
    else:
        raise MediaError("Неизвестный тип медиа")
    item, created = db.create_media_job(kind, params, identity, business_id, repeat=repeat)
    item["business_id"] = business_id
    item["organization_id"] = identity
    item["created"] = created
    return item


def discover_sections(*, business_id: str | None = None, url: str | None = None) -> dict:
    """Desktop preflight: find the current gallery labels before an image job is queued."""
    validated, _, _ = _resolve_url(business_id, url)
    from .desktop_gallery import discover_sections as discover
    return discover(validated)


def resume_images(job_id: str) -> dict:
    """Requeue a failed or blocked desktop gallery task while keeping its partial image files."""
    with db.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT kind,status FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not row or row["kind"] != "images":
            raise MediaError("Задание галереи не найдено")
        if row["status"] in {"queued", "running"}:
            pass
        elif row["status"] not in {"failed", "blocked"}:
            raise MediaError("Продолжить можно только прерванное задание галереи")
        else:
            conn.execute("UPDATE jobs SET status='queued',error=NULL,result='{}',updated_at=? WHERE id=?",
                         (db.now(), job_id))
    return db.job(job_id)


def list_jobs(business_id: str | None = None) -> list[dict]:
    return db.list_media_jobs(business_id)


def archive_path(job_id: str, name: str) -> Path:
    item = db.job(job_id)
    if not item or item["kind"] not in {"images", "products"} or item["status"] != "done":
        raise MediaError("Архив ещё не готов")
    if name not in item["result"].get("archives", []) or Path(name).name != name:
        raise MediaError("Архив не найден в задании")
    path = job_folder(job_id) / name
    if not path.is_file():
        raise MediaError("Файл архива отсутствует")
    return path


def result_folder(job_id: str) -> Path:
    item = db.job(job_id)
    if not item or item["kind"] not in {"images", "products"} or item["status"] != "done":
        raise MediaError("Папка медиа ещё не готова")
    relative = item["result"].get("folder")
    if not relative:
        raise MediaError("У этого старого задания есть только ZIP-архив")
    root = MEDIA_DIR.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_dir():
        raise MediaError("Папка медиа не найдена")
    return path


def open_archive(job_id: str, name: str = "") -> str:
    path = archive_path(job_id, name) if name else result_folder(job_id)
    os.startfile(path)  # type: ignore[attr-defined]
    return str(path)


def copy_archive(job_id: str, name: str, target: str) -> str:
    source = archive_path(job_id, name) if name else result_folder(job_id)
    destination = Path(target).expanduser().resolve()
    if source.is_dir():
        destination = destination / source.name
        if destination.exists():
            raise MediaError("Папка с таким именем уже существует. Выберите другое место.")
        if destination.is_relative_to(source):
            raise MediaError("Нельзя копировать папку внутрь самой себя")
        shutil.copytree(source, destination)
        return str(destination)
    if destination.suffix.lower() != ".zip":
        raise MediaError("Сохраните архив как ZIP")
    if source.resolve() == destination:
        return str(source)
    shutil.copy2(source, destination)
    return str(destination)


def delete(job_id: str) -> None:
    item = db.job(job_id)
    if not item or item["kind"] not in {"images", "products"}:
        raise MediaError("Медиа-задание не найдено")
    completed_folder = None
    if item["status"] == "done" and item["result"].get("folder"):
        root = MEDIA_DIR.resolve()
        candidate = (root / item["result"]["folder"]).resolve()
        if not candidate.is_relative_to(root):
            raise MediaError("Некорректный путь к папке медиа")
        completed_folder = candidate if candidate.is_dir() else None
    if not db.prepare_media_delete(job_id):
        raise MediaError("Задание выполняется")
    if completed_folder:
        shutil.rmtree(completed_folder)
    delete_media_files(job_id)
    db.delete_job(job_id)
