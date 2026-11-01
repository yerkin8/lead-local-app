from __future__ import annotations

import re
import shutil
import zipfile
import json
import os
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from .config import MEDIA_DIR, MIN_FREE_BYTES
from .driver_registry import quit_driver
from .errors import YandexBlocked
from .domain import org_id
from .lead_parser import SearchStopped, localized_url, request_page
from .yandex_urls import normalize_yandex_url

MAX_ZIP_BYTES = 45 * 1024 * 1024


class MediaError(RuntimeError):
    pass


class MediaBlocked(MediaError, YandexBlocked):
    """Captcha or rate limit while collecting media."""


def validate_org_url(url: str) -> str:
    value = normalize_yandex_url(url)
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname != "yandex.ru":
        raise MediaError("Нужна HTTPS-ссылка на карточку Яндекс.Карт")
    if re.fullmatch(r"/maps/org/[^/]+/\d+(?:/|/(?:gallery|prices|menu)/?)?", parsed.path):
        return value
    if re.fullmatch(r"/maps/(?:\d+/[^/]+/)?", parsed.path):
        query = parse_qs(parsed.query)
        if query.get("mode") == ["poi"]:
            poi_url = urlparse(query.get("poi[uri]", [""])[0])
            oid = parse_qs(poi_url.query).get("oid", [""])[0]
            if poi_url.scheme == "ymapsbm1" and poi_url.netloc == "org" and re.fullmatch(r"\d+", oid):
                return f"https://yandex.ru/maps/org/organization/{oid}/"
    raise MediaError("Ссылка должна вести на карточку организации Яндекс.Карт")


def ensure_space(path: Path = MEDIA_DIR) -> None:
    path.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(path).free
    if free < MIN_FREE_BYTES:
        raise MediaError(f"На диске осталось {free / 1024**3:.1f} ГБ. Удалите старые архивы в разделе «Медиа».")


def job_folder(job_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise MediaError("Некорректный ID задания")
    return MEDIA_DIR / job_id


def gallery_url(url: str) -> str:
    """The gallery browser parser expects the Russian Maps host."""
    parsed = urlparse(validate_org_url(url))
    return localized_url(parsed._replace(netloc="yandex.ru").geturl())


def _result_directory(job: dict, business_name: str, work: Path) -> tuple[Path, int, int]:
    """Move completed files into a browsable business/city directory."""
    from . import db
    from yandex_products_parser import safe_name

    linked = db.business(job.get("business_id") or "") if job.get("business_id") else None
    if not linked:
        identity = org_id(job["params"]["url"])
        linked = db.business(identity) if identity else None
    city = (linked or {}).get("city") or "Город не указан"
    name = (linked or {}).get("name") or business_name or "Организация"
    parent = MEDIA_DIR / f"{safe_name(name)} - {safe_name(city)}" / ("Галерея" if job["kind"] == "images" else "Товары")
    parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    destination = parent / stamp
    serial = 2
    while destination.exists():
        destination = parent / f"{stamp} ({serial})"
        serial += 1
    files = [item for item in work.rglob("*") if item.is_file()]
    size = sum(item.stat().st_size for item in files)
    work.rename(destination)
    return destination, len(files), size


def split_archives(folder: Path, files: list[Path], *, include_menu: bool = False,
                   base: Path | None = None) -> list[Path]:
    """Produce valid, independent ZIP files in ``folder`` with paths relative to ``base``."""
    base = base or folder / "work"
    menu = base / "menu.md"
    payload = [f for f in files if f.is_file() and f != menu]
    if not payload and not include_menu:
        raise MediaError("Не найдено файлов для архива")
    estimated = sum(f.stat().st_size for f in payload)
    if include_menu and menu.exists():
        estimated += menu.stat().st_size
    if shutil.disk_usage(folder).free < estimated + MIN_FREE_BYTES:
        raise MediaError("Недостаточно места для упаковки ZIP. Удалите старые архивы в разделе «Медиа».")
    groups: list[list[Path]] = []
    group: list[Path] = []
    size = menu.stat().st_size if include_menu and menu.exists() else 0
    for file in payload:
        file_size = file.stat().st_size
        if file_size + 1024 * 1024 > MAX_ZIP_BYTES:
            raise MediaError(f"Файл {file.name} слишком велик для отправки через Telegram")
        if group and size + file_size + 1024 * 1024 > MAX_ZIP_BYTES:
            groups.append(group)
            group = []
            size = menu.stat().st_size if include_menu and menu.exists() else 0
        group.append(file)
        size += file_size
    if group or not groups:
        groups.append(group)
    archives = []
    for index, items in enumerate(groups, 1):
        target = folder / f"archive-{index:02d}.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_STORED) as archive:
            if include_menu and menu.exists():
                archive.write(menu, "menu.md")
            for file in items:
                archive.write(file, file.relative_to(base).as_posix())
        if target.stat().st_size > 50 * 1024 * 1024:
            target.unlink(missing_ok=True)
            raise MediaError("Архив превысил лимит Telegram")
        archives.append(target)
    return archives


def guard_download(module):
    if getattr(module, "_lem_guarded", False):
        return
    original = module.download_image

    def checked(*args, **kwargs):
        ensure_space()
        return original(*args, **kwargs)

    module.download_image = checked
    module._lem_guarded = True


def run_images(job: dict) -> dict:
    import yandex_images_parser as parser

    params = job["params"]
    url = gallery_url(params["url"])
    ensure_space()
    parser.HEADLESS = os.name != "nt" and not bool(os.getenv("DISPLAY"))
    parser.MANUAL_CAPTCHA_WAIT = os.name == "nt"
    parser.MAX_IMAGE_BYTES = 10 * 1024 * 1024
    guard_download(parser)
    folder = job_folder(job["id"])
    if folder.exists():
        shutil.rmtree(folder)
    work = folder / "work"
    work.mkdir(parents=True, exist_ok=True)
    driver = parser.make_driver()
    stats = []
    try:
        name = parser.open_gallery(driver, url)
        session = parser.build_session(driver)
        requested = params["sections"]
        available = parser.wait_for_section_buttons(driver, requested)
        for section, count in requested.items():
            actual = available.get(section.casefold())
            if actual is None:
                stats.append({"section": section, "saved": 0, "missing": count, "reason": "Секции нет"})
                continue
            if not parser.select_section(driver, actual):
                stats.append({"section": section, "saved": 0, "missing": count, "reason": "Не удалось открыть секцию"})
                continue
            parser.IMAGES_PER_SECTION = count
            section_folder = work / parser.safe_name(section)
            section_folder.mkdir(parents=True, exist_ok=True)
            section_stats = parser.process_section(driver, session, section_folder, actual, {})
            stats.append({"section": section, "saved": section_stats.saved, "missing": section_stats.missing})
        files = [f for f in work.rglob("*") if f.is_file()]
        if not files:
            raise MediaError("В выбранных секциях не удалось найти и скачать изображения")
        destination, file_count, size = _result_directory(job, name, work)
        return {"business_name": name, "sections": stats,
                "folder": str(destination.relative_to(MEDIA_DIR)), "files": file_count, "bytes": size}
    finally:
        quit_driver(driver)
        shutil.rmtree(work, ignore_errors=True)


def run_products(job: dict) -> dict:
    for attempt in range(3):
        try:
            return run_products_http(job)
        except SearchStopped:
            return run_products_browser(job)
        except MediaError as exc:
            if str(exc) != "HTML карточки не содержит товаров":
                raise
            if attempt < 2:
                time.sleep(attempt + 1)
    return run_products_browser(job)


def products_from_state(page: str, expected_id: str):
    import yandex_products_parser as parser

    soup = BeautifulSoup(page, "html.parser")
    state_node = soup.find("script", type="application/json")
    if not state_node or not state_node.string:
        raise MediaError("HTML карточки не содержит товаров")
    try:
        state = json.loads(state_node.string)
        items = state["stack"][0]["results"]["items"]
        business = next(item for item in items if str(item.get("id")) == expected_id)
        categories = business.get("fullObjects", {}).get("categories", [])
    except (ValueError, KeyError, IndexError, TypeError, StopIteration) as exc:
        raise MediaError("HTML карточки не содержит товаров") from exc
    products = []
    for category in categories:
        name = category.get("categoryName") or "Без категории"
        for item in category.get("categoryItems", []):
            title = (item.get("title") or "").strip()
            if not title:
                continue
            amount = str(item.get("price") or "").strip()
            currency = str(item.get("currency") or "").strip()
            product = parser.Product(
                category=name, title=title,
                description=item.get("description") or "",
                price=f"{amount} {currency}".strip(),
                detail=item.get("volume") or "",
            )
            template = item.get("photoLink") or ""
            image_url = template.replace("{size}", "orig")
            parsed = urlparse(image_url)
            if parsed.scheme == "https" and parsed.hostname == "avatars.mds.yandex.net":
                product.image_urls = [image_url]
            products.append(product)
    return business.get("title") or "Бизнес", products


def run_products_http(job: dict) -> dict:
    import yandex_products_parser as parser

    url = validate_org_url(job["params"]["url"])
    ensure_space()
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "ru-RU,ru;q=0.9"})
    match = re.match(r"(/maps/org/[^/]+/\d+)(?:/|$)", urlparse(url).path)
    if not match:
        raise MediaError("Ссылка должна вести на карточку организации Яндекс.Карт")
    card_path = match.group(1) + "/"
    section = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    sections = ("menu", "prices") if section == "menu" else ("prices", "menu")
    products = []
    for section in sections:
        _, page = request_page(session, f"https://yandex.ru{card_path}{section}/")
        try:
            name, products = products_from_state(page, org_id(url))
        except MediaError as exc:
            if str(exc) != "HTML карточки не содержит товаров":
                raise
            continue
        if products:
            break
    if not products:
        raise MediaError("HTML карточки не содержит товаров")
    parser.MAX_IMAGE_BYTES = 10 * 1024 * 1024
    guard_download(parser)
    folder = job_folder(job["id"])
    if folder.exists():
        shutil.rmtree(folder)
    work = folder / "work"
    work.mkdir(parents=True, exist_ok=True)
    try:
        saved, failed = parser.save_images(products, work / "images", session)
        parser.write_menu(work / "menu.md", parser.render_menu(name, products))
        files = [f for f in work.rglob("*") if f.is_file()]
        destination, file_count, size = _result_directory(job, name, work)
        return {"business_name": name, "products": len(products), "images": saved,
                "failed_images": failed, "folder": str(destination.relative_to(MEDIA_DIR)),
                "files": file_count, "bytes": size}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def run_products_browser(job: dict) -> dict:
    import yandex_products_parser as parser

    url = gallery_url(job["params"]["url"])
    ensure_space()
    parser.HEADLESS = os.name != "nt" and not bool(os.getenv("DISPLAY"))
    parser.MANUAL_CAPTCHA_WAIT = os.name == "nt"
    parser.MAX_IMAGE_BYTES = 10 * 1024 * 1024
    guard_download(parser)
    folder = job_folder(job["id"])
    if folder.exists():
        shutil.rmtree(folder)
    work = folder / "work"
    work.mkdir(parents=True, exist_ok=True)
    driver = parser.make_driver()
    try:
        name = parser.open_products(driver, url)
        products = parser.collect_products(driver)
        if not products:
            raise MediaError("В карточке нет товаров")
        session = parser.build_session(driver)
        saved, failed = parser.save_images(products, work / "images", session)
        parser.write_menu(work / "menu.md", parser.render_menu(name, products))
        files = [f for f in work.rglob("*") if f.is_file()]
        destination, file_count, size = _result_directory(job, name, work)
        return {"business_name": name, "products": len(products), "images": saved,
                "failed_images": failed, "folder": str(destination.relative_to(MEDIA_DIR)),
                "files": file_count, "bytes": size}
    finally:
        quit_driver(driver)
        shutil.rmtree(work, ignore_errors=True)


def delete_media_files(job_id: str) -> None:
    folder = job_folder(job_id)
    if folder.exists():
        shutil.rmtree(folder)
