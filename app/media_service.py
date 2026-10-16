"""Local media workflow shared by the desktop UI and its job runner."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from . import db
from .domain import org_id
from .config import MEDIA_DIR
from .media import MediaError, delete_media_files, job_folder, validate_org_url

SECTIONS = {"Внутри", "Еда", "Меню"}


def start(kind: str, *, business_id: str | None = None, url: str | None = None,
          sections: dict[str, int] | None = None, repeat: bool = False) -> dict:
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
    if kind == "images":
        if not sections or any(key not in SECTIONS or not isinstance(value, int)
                               or isinstance(value, bool) or not 1 <= value <= 50
                               for key, value in sections.items()):
            raise MediaError("Для галереи выберите секции и количество от 1 до 50")
        params = {"url": validated, "sections": sections}
    elif kind == "products":
        params = {"url": validated}
    else:
        raise MediaError("Неизвестный тип медиа")
    item, created = db.create_media_job(kind, params, identity, business_id, repeat=repeat)
    item["business_id"] = business_id
    item["organization_id"] = identity
    item["created"] = created
    return item


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
