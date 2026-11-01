"""Dynamic image gallery collection used only by the Windows desktop workflow."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from .config import DATA_DIR, MEDIA_DIR
from .driver_registry import register, quit_driver
from .media import MediaBlocked, MediaError, _result_directory, ensure_space, gallery_url, job_folder
from yandex_images_parser import (
    CATEGORY_DISCOVERY_TIMEOUT,
    MAX_STALE_SCROLLS,
    PAGE_TIMEOUT,
    SCROLL_CONTAINER_SELECTOR,
    SPACE_RE,
    open_gallery,
    page_has_captcha,
    visible_photo_urls,
    scroll_gallery,
    build_session,
    detect_image_extension,
    next_file_path,
)

MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_SECTION_NAME = 100
ALL_SELECTION = "__all__"
VIDEO_NAMES = {"видео", "video"}
SECTION_CONTEXT_RE = re.compile(
    r"(?:media|photo)[-_]?gallery.*(?:filter|categor|tag)|"
    r"(?:filter|categor|tag).*?(?:media|photo)[-_]?gallery",
    re.IGNORECASE,
)
WINDOWS_FORBIDDEN_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
SPACE_RE = re.compile(r"\s+")
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                    *(f"LPT{i}" for i in range(1, 10))}
NON_SECTION_LABELS = {"закрыть", "назад", "поделиться", "share", "close", "download", "скачать"}


def normalize_section_name(value: object) -> str:
    return SPACE_RE.sub(" ", str(value or "")).strip()


def is_video_section(value: str) -> bool:
    folded = normalize_section_name(value).casefold()
    return folded in VIDEO_NAMES or "видео" in folded or "video" in folded


def discover_sections_from_html(html: str) -> list[str]:
    """Read only chips inside the photo gallery filter strip."""
    soup = BeautifulSoup(html or "", "html.parser")
    result: list[str] = []
    seen: set[str] = set()
    groups = [node for node in soup.select("[class]")
              if SECTION_CONTEXT_RE.search(" ".join(node.get("class") or []))]
    for element in (node for group in groups for node in group.select("button, [role='button']")):
        label = normalize_section_name(element.get_text(" ", strip=True)
                                       or element.get("aria-label"))
        if (not label or len(label) > MAX_SECTION_NAME or is_video_section(label)
                or label.casefold() in NON_SECTION_LABELS):
            continue
        if element.find_parent(attrs={"role": "tablist"}):
            continue
        key = label.casefold()
        if key not in seen:
            seen.add(key)
            result.append(label)
    return result


def safe_section_name(value: str, fallback: str = "Все") -> str:
    """Produce a Windows-safe directory name while preserving Cyrillic labels."""
    cleaned = WINDOWS_FORBIDDEN_RE.sub("_", normalize_section_name(value))
    cleaned = SPACE_RE.sub("_", cleaned).strip(" ._")[:40].rstrip(" .")
    if not cleaned:
        cleaned = fallback
    if cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED:
        cleaned = "_" + cleaned
    return cleaned


def unique_section_directory_name(value: str, used: dict[str, str]) -> str:
    base = safe_section_name(value)
    key = base.casefold()
    if key in used and used[key] != value:
        suffix = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
        base = f"{base[:30]}_{suffix}"
        key = base.casefold()
        serial = 2
        while key in used and used[key] != value:
            base = f"{safe_section_name(value)[:25]}_{suffix}_{serial}"
            key = base.casefold()
            serial += 1
    used[key] = value
    return base


def normalize_selection(selection: object) -> dict:
    """Validate and normalize a persisted desktop gallery selection."""
    if selection in (None, ALL_SELECTION, "all"):
        return {"mode": "all", "names": []}
    if isinstance(selection, dict):
        mode = str(selection.get("mode") or "").casefold()
        if mode == "all":
            return {"mode": "all", "names": []}
        raw_names = selection.get("names")
    else:
        raw_names = selection
    if not isinstance(raw_names, (list, tuple)):
        raise ValueError("Сначала найдите разделы галереи")
    names: list[str] = []
    seen: set[str] = set()
    for raw in raw_names:
        label = normalize_section_name(raw)
        if label == ALL_SELECTION:
            return {"mode": "all", "names": []}
        if (not label or len(label) > MAX_SECTION_NAME or is_video_section(label)
                or label.casefold() in NON_SECTION_LABELS):
            raise ValueError("Название выбранного раздела некорректно")
        if label.casefold() not in seen:
            seen.add(label.casefold())
            names.append(label)
    if not names:
        raise ValueError("Выберите все разделы или хотя бы один найденный раздел")
    return {"mode": "selected", "names": names}


def _visible_section_labels(driver) -> list[str]:
    values = driver.execute_script(r"""
      const isVisible = el => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
      const groups = Array.from(document.querySelectorAll('[class]')).filter(el =>
        /(?:media|photo)[-_]?gallery.*(?:filter|categor|tag)|(?:filter|categor|tag).*?(?:media|photo)[-_]?gallery/i.test(String(el.className || '')));
      const result = [];
      for (const el of groups.flatMap(group => Array.from(group.querySelectorAll('button,[role="button"]')))) {
        if (!isVisible(el)) continue;
        if (el.closest('[role="tablist"]')) continue;
        const label = String(el.innerText || el.getAttribute('aria-label') || '')
          .replace(/\s+/g, ' ').trim();
        if (!label || label.length > 100) continue;
        if (/^(?:закрыть|назад|поделиться|share|close|download|скачать)$/i.test(label)) continue;
        if (/\bvideo\b|видео/i.test(label)) continue;
        result.push(label);
      }
      return [...new Map(result.map(label => [label.toLocaleLowerCase(), label])).values()];
    """) or []
    labels = []
    seen = set()
    for value in values:
        label = normalize_section_name(value)
        if not label or len(label) > MAX_SECTION_NAME or is_video_section(label):
            continue
        if label.casefold() not in seen:
            seen.add(label.casefold())
            labels.append(label)
    return labels


def wait_for_gallery_sections(driver, timeout: float = CATEGORY_DISCOVERY_TIMEOUT) -> list[str]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if page_has_captcha(driver):
            raise MediaBlocked("Яндекс показал проверку или временное ограничение. Задание остановлено; повторите позже.")
        labels = _visible_section_labels(driver)
        if labels:
            return labels
        time.sleep(0.25)
    return []


def _make_driver():
    options = Options()
    if os.name != "nt" and not os.getenv("DISPLAY"):
        options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1400,1000")
    options.add_argument("--disable-notifications")
    options.add_argument("--lang=ru-RU")
    from .driver_registry import profile_argument
    profile = profile_argument()
    if profile:
        options.add_argument(profile)
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(45)
    driver.set_script_timeout(20)
    return register(driver)


def _section_button(driver, label: str):
    wanted = normalize_section_name(label).casefold()
    for group in driver.find_elements(By.CSS_SELECTOR, "[class*='gallery'][class*='filter'], [class*='gallery'][class*='categor'], [class*='gallery'][class*='tag']"):
        for element in group.find_elements(By.CSS_SELECTOR, "button, [role='button']"):
            text = normalize_section_name(element.text or element.get_attribute("aria-label"))
            if text.casefold() == wanted:
                return element
    return None


def _is_selected(element) -> bool:
    classes = (element.get_attribute("class") or "").split()
    return (element.get_attribute("aria-pressed") == "true"
            or element.get_attribute("aria-selected") == "true"
            or element.get_attribute("data-selected") == "true"
            or any(token.casefold() in {"_checked", "checked", "selected", "_active", "active", "is-active"}
                   for token in classes))


def _select_gallery_section(driver, label: str) -> bool:
    element = _section_button(driver, label)
    if element is None:
        return False
    if _is_selected(element):
        return True
    driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", element)
    try:
        WebDriverWait(driver, PAGE_TIMEOUT).until(
            lambda current: (candidate := _section_button(current, label)) is not None
            and _is_selected(candidate))
    except TimeoutException:
        return False
    driver.execute_script(
        "const c=document.querySelector(arguments[0]); if(c){c.scrollTop=0; c.dispatchEvent(new Event('scroll',{bubbles:true}));}",
        SCROLL_CONTAINER_SELECTOR,
    )
    time.sleep(0.8)
    return True


def _open_gallery(driver, url: str) -> tuple[str, str]:
    validated = gallery_url(url)
    name = open_gallery(driver, validated)
    if page_has_captcha(driver):
        raise MediaBlocked("Яндекс показал проверку или временное ограничение. Задание остановлено; повторите позже.")
    if os.getenv("LEM_SAVE_GALLERY_DOM") == "1":
        debug = DATA_DIR / "debug"
        debug.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        (debug / f"gallery-{stamp}.html").write_text(driver.page_source, encoding="utf-8")
        chips = driver.execute_script(r"""
          return Array.from(document.querySelectorAll('[class]'))
            .filter(group => /(?:media|photo)[-_]?gallery.*(?:filter|categor|tag)/i.test(String(group.className || '')))
            .flatMap(group => Array.from(group.querySelectorAll('button,[role="button"]')))
            .map(el => ({text: el.innerText, className: String(el.className || ''),
              role: el.getAttribute('role'), ariaLabel: el.getAttribute('aria-label')}));
        """)
        (debug / f"gallery-{stamp}-chips.json").write_text(
            json.dumps(chips, ensure_ascii=False, indent=2), encoding="utf-8")
    return name, validated


def discover_sections(url: str) -> dict:
    """Open one gallery in Chrome and report section labels; it does not download images."""
    driver = None
    try:
        driver = _make_driver()
        _, validated = _open_gallery(driver, url)
        labels = [label for label in wait_for_gallery_sections(driver)
                  if not is_video_section(label) and label.casefold() not in NON_SECTION_LABELS]
        return {"sections": labels, "fallback": not bool(labels), "url": validated}
    except WebDriverException as error:
        raise MediaError("Не удалось открыть галерею. Проверьте интернет и Chrome/Edge WebDriver.") from error
    finally:
        if driver is not None:
            quit_driver(driver)


def _selection_plan(selection: dict, available: list[str]) -> list[dict]:
    available = [name for name in available if not is_video_section(name)
                 and name.casefold() not in NON_SECTION_LABELS]
    by_key = {name.casefold(): name for name in available}
    all_name = next((name for name in available if name.casefold() in {"все", "all"}), None)
    if selection["mode"] == "all":
        named = [name for name in available if name.casefold() not in {"все", "all"}]
        if named:
            return [{"requested": name, "label": name, "button": name} for name in named]
        return [{"requested": "Все разделы", "label": "Все", "button": all_name}]
    plan = []
    missing = False
    for requested in selection["names"]:
        actual = by_key.get(requested.casefold())
        if actual is None:
            missing = True
            plan.append({"requested": requested, "label": "Все", "button": all_name})
        else:
            plan.append({"requested": requested, "label": actual, "button": actual})
    if missing and all_name is None and not any(item["button"] is None for item in plan):
        plan.append({"requested": "Общая галерея", "label": "Все", "button": None})
    # Missing selected sections share one general gallery pass.
    result = []
    seen = set()
    for item in plan:
        key = (item["button"] or "__general__").casefold()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _image_files(folder: Path) -> list[Path]:
    from yandex_images_parser import SUPPORTED_EXTENSIONS
    return [path for path in folder.rglob("*")
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS]


def _existing_hashes(folder: Path) -> dict[str, list[Path]]:
    result: dict[str, list[Path]] = {}
    for path in _image_files(folder):
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        result.setdefault(digest, []).append(path)
    return result


def _download_image(session: requests.Session, url: str) -> tuple[bytes, str] | None:
    try:
        with session.get(url, timeout=30, stream=True) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if not content_type.startswith("image/") or content_type == "image/svg+xml":
                return None
            chunks = []
            size = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_IMAGE_BYTES:
                    return None
                chunks.append(chunk)
            data = b"".join(chunks)
    except requests.RequestException:
        return None
    extension = detect_image_extension(data)
    return (data, extension) if extension else None


def save_photo(folder: Path, section: str, source_url: str, image_url: str,
               data: bytes, extension: str, known_hashes: dict[str, list[Path]]) -> tuple[Path | None, bool]:
    """Save a unique image with a small JSON sidecar describing its origin and size."""
    digest = hashlib.sha256(data).hexdigest()
    if digest in known_hashes:
        return None, True
    folder.mkdir(parents=True, exist_ok=True)
    ordinal = 1 + sum(1 for path in folder.iterdir()
                      if path.is_file() and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"})
    prefix = safe_section_name(section)
    target = next_file_path(folder, prefix, ordinal, digest, extension)
    sidecar = target.with_name(target.name + ".json")
    payload = {"section": section, "source": "Яндекс.Карты",
               "source_url": source_url, "image_url": image_url,
               "size_bytes": len(data), "sha256": digest,
               "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        target.write_bytes(data)
        temporary = sidecar.with_name(sidecar.name + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(sidecar)
    except OSError as error:
        target.unlink(missing_ok=True)
        sidecar.unlink(missing_ok=True)
        sidecar.with_name(sidecar.name + ".tmp").unlink(missing_ok=True)
        raise MediaError("Не удалось сохранить фотографию и её метаданные") from error
    known_hashes.setdefault(digest, []).append(target)
    return target, False


def collect_section(section: str, folder: Path, source_url: str,
                    known_hashes: dict[str, list[Path]], per_section_limit: int,
                    total_remaining: int, get_urls: Callable[[], list[str]],
                    scroll: Callable[[], tuple[int, int, int]],
                    download: Callable[[str], tuple[bytes, str] | None]) -> dict:
    """Collect one visible section through injectable browser/network callbacks."""
    existing = len(_image_files(folder))
    saved = duplicates = rejected = 0
    processed_urls: set[str] = set()
    stale_rounds = 0
    last_scroll_state = None
    capacity = max(0, min(per_section_limit - existing, total_remaining))
    while saved < capacity and stale_rounds < MAX_STALE_SCROLLS:
        urls = [url for url in get_urls() if url not in processed_urls]
        processed_urls.update(urls)
        stale_rounds = 0 if urls else stale_rounds + 1
        for url in urls:
            result = download(url)
            if result is None:
                rejected += 1
                continue
            data, extension = result
            ensure_space(MEDIA_DIR)
            path, duplicate = save_photo(folder, section, source_url, url,
                                         data, extension, known_hashes)
            if duplicate:
                duplicates += 1
                continue
            if path:
                saved += 1
            if saved >= capacity:
                break
        if saved >= capacity:
            break
        state = scroll()
        top, height, client_height = state
        at_bottom = top + client_height >= height - 5
        if at_bottom and state == last_scroll_state and not urls:
            stale_rounds += 1
        last_scroll_state = state
    completed = existing + saved
    return {"section": section, "saved": saved, "existing": existing,
            "duplicates": duplicates, "rejected": rejected,
            "missing": max(0, per_section_limit - completed)}


def run_gallery_images(job: dict) -> dict:
    params = job["params"]
    url = gallery_url(params["url"])
    old_sections = params.get("sections")
    raw_selection = params.get("selection")
    if raw_selection is None and isinstance(old_sections, dict):
        raw_selection = {"mode": "selected", "names": list(old_sections)}
    selection = normalize_selection(raw_selection)
    per_section_limit = int(params.get("per_section_limit", 10))
    old_limits = params.get("per_section_limits")
    if not isinstance(old_limits, dict) and isinstance(old_sections, dict):
        old_limits = {normalize_section_name(name).casefold(): count
                      for name, count in old_sections.items()
                      if isinstance(count, int) and not isinstance(count, bool)}
    organization_limit = int(params.get("organization_limit",
                                       min(200, sum(old_limits.values())) if old_limits else 50))
    if not 1 <= per_section_limit <= 50 or not 1 <= organization_limit <= 200:
        raise MediaError("Проверьте лимит фотографий на раздел и на организацию")
    ensure_space()
    folder = job_folder(job["id"])
    work = folder / "work"
    work.mkdir(parents=True, exist_ok=True)
    driver = None
    session = None
    stats = []
    try:
        driver = _make_driver()
        name, source_url = _open_gallery(driver, url)
        session = build_session(driver)
        available = wait_for_gallery_sections(driver)
        plan = _selection_plan(selection, available)
        known_hashes = _existing_hashes(work)
        total_existing = sum(len(items) for items in known_hashes.values())
        total_saved = 0
        used_directories: dict[str, str] = {}
        filtered_section_active = False
        for item in plan:
            section_limit = per_section_limit
            if isinstance(old_limits, dict):
                section_limit = int(old_limits.get(item["requested"].casefold(),
                                                    old_limits.get(item["label"].casefold(), per_section_limit)))
            if not 1 <= section_limit <= 50:
                raise MediaError("Лимит фотографий на раздел должен быть от 1 до 50")
            if total_existing + total_saved >= organization_limit:
                stats.append({"section": item["label"], "saved": 0, "existing": 0,
                              "duplicates": 0, "rejected": 0,
                              "missing": section_limit, "reason": "Достигнут общий лимит"})
                continue
            actual_button = item["button"]
            if actual_button and not _select_gallery_section(driver, actual_button):
                # A selected section can disappear while the gallery is loading.
                all_button = next((label for label in available
                                   if label.casefold() in {"все", "all"}), None)
                if all_button and all_button.casefold() != actual_button.casefold():
                    if not _select_gallery_section(driver, all_button):
                        stats.append({"section": item["requested"], "saved": 0, "existing": 0,
                                      "duplicates": 0, "rejected": 0,
                                      "missing": section_limit,
                                      "reason": "Не удалось открыть раздел или общую галерею"})
                        continue
                    item = {**item, "label": "Все", "button": all_button,
                            "requested": item["requested"]}
                    filtered_section_active = False
                elif actual_button.casefold() in {"все", "all"}:
                    name, _ = _open_gallery(driver, source_url)
                    session.headers["Referer"] = driver.current_url
                    item = {**item, "label": "Все", "button": None,
                            "requested": item["requested"]}
                    filtered_section_active = False
                else:
                    # Reload the profile to clear a selected tab when no all tab exists.
                    name, _ = _open_gallery(driver, source_url)
                    session.headers["Referer"] = driver.current_url
                    item = {**item, "label": "Все", "button": None,
                            "requested": item["requested"]}
                    filtered_section_active = False
            elif actual_button is None and filtered_section_active:
                name, _ = _open_gallery(driver, source_url)
                session.headers["Referer"] = driver.current_url
                filtered_section_active = False
            elif actual_button:
                filtered_section_active = actual_button.casefold() not in {"все", "all"}
            section_directory = unique_section_directory_name(item["label"], used_directories)
            section_folder = work / section_directory
            urls = lambda: visible_photo_urls(driver)
            scroll = lambda: scroll_gallery(driver)
            download = lambda image_url: _download_image(session, image_url)
            result = collect_section(item["label"], section_folder, source_url,
                                     known_hashes, section_limit,
                                     max(0, organization_limit - total_existing - total_saved),
                                     urls, scroll, download)
            if item["requested"] != item["label"]:
                result["requested_section"] = item["requested"]
                if item["button"] is None or item["label"] == "Все":
                    result["fallback"] = "Использована общая галерея: выбранный раздел не найден"
            stats.append(result)
            total_saved += result["saved"]
        files = _image_files(work)
        if not files:
            raise MediaError("В галерее не удалось найти и сохранить фотографии")
        destination, file_count, size = _result_directory(job, name, work)
        return {"business_name": name, "sections": stats,
                "folder": str(destination.relative_to(MEDIA_DIR)),
                "files": file_count, "photos": len(files), "bytes": size,
                "organization_limit": organization_limit}
    except WebDriverException as error:
        raise MediaError("Галерея прервана. Уже сохранённые фотографии оставлены; проверьте интернет и продолжите задание.") from error
    finally:
        if session is not None:
            session.close()
        if driver is not None:
            quit_driver(driver)
