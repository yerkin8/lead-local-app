"""
Скачивание фотографий организации из галереи Яндекс.Карт.

Скрипт открывает карточку в Chrome, переключает заданные разделы
галереи и сохраняет только реальные растровые изображения. URL Яндекса
часто не имеют расширения, поэтому формат определяется по Content-Type и
сигнатуре файла, а не по адресу.

Установка:
    pip install -r requirements.txt

Запуск:
    python yandex_images_parser.py
"""

from __future__ import annotations

import hashlib
import os
import random
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import requests
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


# ============================= НАСТРОЙКИ =============================

GALLERY_URL = (
    "https://yandex.ru/maps/org/paradnaya/58911308235/gallery/"
    "?from=tableau_yabro&ll=37.645880%2C55.737203&tab=gallery&z=13.84"
)

# Лимит применяется к каждому разделу отдельно.
# Если указаны "Все" и другие разделы, "Все" будет обработан последним
# и доберёт только ещё не сохранённые фотографии.
SECTIONS = ["Внутри", "Еда", "Меню"]
IMAGES_PER_SECTION = 50

OUTPUT_DIR = "output"
HEADLESS = False
MANUAL_CAPTCHA_WAIT = False

PAGE_TIMEOUT = 20
DOWNLOAD_TIMEOUT = 30
MIN_SCROLL_DELAY = 0.8
MAX_SCROLL_DELAY = 1.4
MAX_STALE_SCROLLS = 8
MAX_IMAGE_BYTES = 30 * 1024 * 1024
CATEGORY_DISCOVERY_TIMEOUT = 15

# =====================================================================


WINDOWS_FORBIDDEN_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
SPACE_RE = re.compile(r"\s+")
PHOTO_SELECTOR = "img.media-wrapper__media"
SCROLL_CONTAINER_SELECTOR = ".scroll__container"
KNOWN_SECTIONS = {
    "все",
    "снаружи",
    "видео",
    "внутри",
    "еда",
    "вход",
    "доступность",
    "меню",
}
SECTION_ALIASES = {
    "all": "все", "interior": "внутри", "inside": "внутри",
    "food": "еда", "menu": "меню", "exterior": "снаружи",
    "video": "видео", "entrance": "вход", "accessibility": "доступность",
}
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".avif", ".gif"}


class ParserError(RuntimeError):
    """Понятная пользователю ошибка парсера."""


class CaptchaDetected(ParserError):
    """Яндекс показал капчу."""


@dataclass
class SectionStats:
    section: str
    saved: int = 0
    existing: int = 0
    duplicates: int = 0
    rejected: int = 0
    seen_urls: int = 0
    missing: int = 0

    @property
    def completed(self) -> int:
        return self.saved + self.existing


def safe_name(text: str, fallback: str = "без_названия") -> str:
    """Делает безопасное для Windows имя файла или папки."""
    cleaned = WINDOWS_FORBIDDEN_RE.sub("_", text)
    cleaned = SPACE_RE.sub("_", cleaned).strip(" ._")
    # Windows не даёт заканчивать имена точкой или пробелом.
    return cleaned[:120].rstrip(" .") or fallback


def make_driver() -> webdriver.Chrome:
    """Создаёт Chrome. Изображения не отключаем: они нужны галерее."""
    options = Options()
    if HEADLESS:
        options.add_argument("--headless=new")
    if HEADLESS or os.getenv("DISPLAY"):
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1400,1000")
    options.add_argument("--disable-notifications")
    options.add_argument("--lang=ru-RU")
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(45)
    driver.set_script_timeout(20)
    from app.driver_registry import register
    return register(driver)


def page_has_captcha(driver: webdriver.Chrome) -> bool:
    current_url = driver.current_url.lower()
    title = driver.title.lower()
    if "showcaptcha" in current_url or "captcha" in title:
        return True
    captcha_elements = driver.find_elements(
        By.CSS_SELECTOR,
        'form[action*="checkcaptcha"], input[name*="captcha"], '
        ".CheckboxCaptcha, .SmartCaptcha",
    )
    if any(element.is_displayed() for element in captcha_elements):
        return True
    try:
        visible_text = driver.find_element(By.TAG_NAME, "body").text[:5_000].casefold()
    except WebDriverException:
        return False
    return visible_text.strip() == "limited" or any(
        marker in visible_text
        for marker in ("я не робот", "подтвердите, что вы не робот")
    )


def wait_for_manual_captcha(driver: webdriver.Chrome) -> None:
    """Не обходит капчу, а оставляет видимый Chrome пользователю."""
    if not page_has_captcha(driver):
        return
    if driver.find_element(By.TAG_NAME, "body").text.strip().casefold() == "limited":
        raise ParserError("Яндекс временно ограничил запросы. Повторите задание позже.")
    if MANUAL_CAPTCHA_WAIT and not HEADLESS:
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            time.sleep(1)
            if not page_has_captcha(driver):
                return
        raise CaptchaDetected("Проверка Яндекса не была пройдена за 2 минуты; задание остановлено.")
    if HEADLESS or not sys.stdin.isatty():
        raise CaptchaDetected(
            "Яндекс показал капчу. Повторите задание позже или откройте карточку вручную."
        )
    print("\nЯндекс показал капчу. Скрипт не будет её обходить.")
    input("Пройдите проверку в открытом Chrome и нажмите Enter... ")
    if page_has_captcha(driver):
        raise CaptchaDetected("Капча всё ещё открыта; запуск остановлен.")


def wait_for_heading(driver: webdriver.Chrome) -> str:
    wait_for_manual_captcha(driver)
    try:
        heading = WebDriverWait(driver, PAGE_TIMEOUT).until(
            EC.visibility_of_element_located((By.CSS_SELECTOR, "h1"))
        )
    except TimeoutException as error:
        if page_has_captcha(driver):
            wait_for_manual_captcha(driver)
            try:
                heading = WebDriverWait(driver, PAGE_TIMEOUT).until(
                    EC.visibility_of_element_located((By.CSS_SELECTOR, "h1"))
                )
            except TimeoutException:
                pass
            else:
                name = heading.text.strip()
                if name:
                    return name
        raise ParserError(
            "Не удалось открыть карточку: не найдено название бизнеса. "
            "Проверьте ссылку, интернет и окно Chrome."
        ) from error
    name = heading.text.strip()
    if not name:
        raise ParserError("В карточке не найдено название бизнеса.")
    return name


def open_gallery(driver: webdriver.Chrome, url: str) -> str:
    if not url.lower().startswith(("https://yandex.ru/maps/", "http://yandex.ru/maps/")):
        raise ParserError("Нужна ссылка на карточку yandex.ru/maps.")

    driver.get(url)
    business_name = wait_for_heading(driver)

    if "/gallery" not in driver.current_url.lower():
        try:
            photo_link = WebDriverWait(driver, PAGE_TIMEOUT).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, 'a[href*="/gallery/"]'))
            )
            driver.execute_script("arguments[0].click();", photo_link)
        except TimeoutException as error:
            raise ParserError("В карточке не найдена вкладка «Фото».") from error

    try:
        WebDriverWait(driver, PAGE_TIMEOUT).until(
            lambda current: "/gallery" in current.current_url.lower()
        )
        WebDriverWait(driver, PAGE_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, SCROLL_CONTAINER_SELECTOR))
        )
    except TimeoutException as error:
        wait_for_manual_captcha(driver)
        raise ParserError("Галерея не загрузилась за отведённое время.") from error

    return business_name


def section_buttons(driver: webdriver.Chrome) -> dict[str, str]:
    """Возвращает {casefold-имя: видимое имя} кнопок категорий."""
    result: dict[str, str] = {}
    for button in driver.find_elements(By.TAG_NAME, "button"):
        text = SPACE_RE.sub(" ", button.text).strip()
        key = SECTION_ALIASES.get(text.casefold(), text.casefold())
        if key in KNOWN_SECTIONS:
            result[key] = text
    return result


def wait_for_section_buttons(driver: webdriver.Chrome, requested: Iterable[str] = ()) -> dict[str, str]:
    """Панель категорий часто появляется позже самой галереи."""
    deadline = time.monotonic() + CATEGORY_DISCOVERY_TIMEOUT
    wanted = {section.casefold() for section in requested}
    available = {}
    while time.monotonic() < deadline:
        wait_for_manual_captcha(driver)
        available = section_buttons(driver)
        if available and (not wanted or wanted.issubset(available)):
            return available
        time.sleep(0.25)
    return available


def find_section_button(driver: webdriver.Chrome, section: str):
    wanted = section.casefold()
    for button in driver.find_elements(By.TAG_NAME, "button"):
        if SPACE_RE.sub(" ", button.text).strip().casefold() == wanted:
            return button
    return None


def select_section(driver: webdriver.Chrome, section: str) -> bool:
    button = find_section_button(driver, section)
    if button is None:
        return False

    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", button)
    driver.execute_script("arguments[0].click();", button)

    try:
        WebDriverWait(driver, PAGE_TIMEOUT).until(
            lambda current: (
                (candidate := find_section_button(current, section)) is not None
                and "_checked" in (candidate.get_attribute("class") or "").split()
            )
        )
    except TimeoutException:
        return False

    # После переключения Яндекс иногда сохраняет старую позицию панели.
    driver.execute_script(
        "const c=document.querySelector(arguments[0]); if(c){c.scrollTop=0; "
        "c.dispatchEvent(new Event('scroll',{bubbles:true}));}",
        SCROLL_CONTAINER_SELECTOR,
    )
    time.sleep(random.uniform(MIN_SCROLL_DELAY, MAX_SCROLL_DELAY))
    return True


def visible_photo_urls(driver: webdriver.Chrome) -> list[str]:
    """Берёт только фото. get-vh и карточки с <video> — видео/превью."""
    urls = driver.execute_script(
        """
        return Array.from(document.querySelectorAll(arguments[0]))
          .filter(img => {
            const src = img.currentSrc || img.src || '';
            const card = img.closest('button, .media-gallery__frame-wrapper');
            const classes = card ? String(card.className || '').toLowerCase() : '';
            return src.startsWith('http')
              && !src.includes('/get-vh/')
              && !classes.includes('video')
              && !(card && card.querySelector('video'));
          })
          .map(img => img.currentSrc || img.src);
        """,
        PHOTO_SELECTOR,
    )
    # dict сохраняет порядок и убирает повторы в текущем DOM.
    return list(dict.fromkeys(urls or []))


def scroll_gallery(driver: webdriver.Chrome) -> tuple[int, int, int]:
    """Скроллит боковую панель; возвращает top, height, clientHeight."""
    values = driver.execute_script(
        """
        const c = document.querySelector(arguments[0]);
        if (!c) return null;
        const step = Math.max(Math.floor(c.clientHeight * 0.85), 600);
        c.scrollTop = Math.min(c.scrollTop + step, c.scrollHeight);
        c.dispatchEvent(new Event('scroll', {bubbles: true}));
        return [Math.floor(c.scrollTop), c.scrollHeight, c.clientHeight];
        """,
        SCROLL_CONTAINER_SELECTOR,
    )
    if not values:
        raise ParserError("Пропал контейнер галереи.")
    time.sleep(random.uniform(MIN_SCROLL_DELAY, MAX_SCROLL_DELAY))
    return tuple(int(value) for value in values)


def build_session(driver: webdriver.Chrome) -> requests.Session:
    session = requests.Session()
    user_agent = driver.execute_script("return navigator.userAgent")
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Referer": driver.current_url,
            "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif,*/*;q=0.5",
        }
    )
    for cookie in driver.get_cookies():
        session.cookies.set(
            cookie["name"],
            cookie["value"],
            domain=cookie.get("domain"),
            path=cookie.get("path", "/"),
        )
    return session


def detect_image_extension(data: bytes) -> str | None:
    """Определяет растровый формат по magic bytes."""
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 16 and data[4:8] == b"ftyp":
        brands = data[8:32]
        if b"avif" in brands or b"avis" in brands:
            return ".avif"
    return None


def download_image(session: requests.Session, url: str) -> tuple[bytes, str] | None:
    try:
        with session.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True) as response:
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
            if not content_type.startswith("image/") or content_type == "image/svg+xml":
                return None

            chunks: list[bytes] = []
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
    if extension is None:
        return None
    return data, extension


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def hashes_in_folder(folder: Path) -> dict[str, list[Path]]:
    """Индекс уже сохранённых файлов для идемпотентных повторных запусков."""
    index: dict[str, list[Path]] = {}
    if not folder.exists():
        return index
    for path in folder.iterdir():
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        try:
            digest = content_hash(path.read_bytes())
        except OSError:
            continue
        index.setdefault(digest, []).append(path)
    return index


def is_existing_for_section(paths: Iterable[Path], prefix: str) -> bool:
    expected = f"{prefix}_"
    return any(path.name.casefold().startswith(expected.casefold()) for path in paths)


def next_file_path(
    folder: Path,
    section_prefix: str,
    ordinal: int,
    digest: str,
    extension: str,
) -> Path:
    base = f"{section_prefix}_{ordinal:03d}_{digest[:12]}"
    path = folder / f"{base}{extension}"
    counter = 2
    while path.exists():
        path = folder / f"{base}_{counter}{extension}"
        counter += 1
    return path


def process_section(
    driver: webdriver.Chrome,
    session: requests.Session,
    folder: Path,
    section: str,
    known_hashes: dict[str, list[Path]],
) -> SectionStats:
    stats = SectionStats(section=section)
    section_prefix = safe_name(section, "все")
    processed_urls: set[str] = set()
    counted_hashes: set[str] = set()
    stale_rounds = 0
    last_scroll_state: tuple[int, int, int] | None = None

    while stats.completed < IMAGES_PER_SECTION and stale_rounds < MAX_STALE_SCROLLS:
        wait_for_manual_captcha(driver)
        new_urls = [url for url in visible_photo_urls(driver) if url not in processed_urls]

        if new_urls:
            stale_rounds = 0
        else:
            stale_rounds += 1

        for url in new_urls:
            processed_urls.add(url)
            stats.seen_urls += 1

            result = download_image(session, url)
            if result is None:
                stats.rejected += 1
                continue

            data, extension = result
            digest = content_hash(data)
            if digest in counted_hashes:
                stats.duplicates += 1
                continue

            existing_paths = known_hashes.get(digest, [])
            if existing_paths:
                counted_hashes.add(digest)
                if is_existing_for_section(existing_paths, section_prefix):
                    stats.existing += 1
                    if stats.completed >= IMAGES_PER_SECTION:
                        break
                else:
                    # Фото уже сохранено другим разделом. Оно не занимает квоту
                    # текущего: скрипт продолжит искать новые уникальные кадры.
                    stats.duplicates += 1
                continue

            ordinal = stats.saved + stats.existing + 1
            path = next_file_path(folder, section_prefix, ordinal, digest, extension)
            try:
                path.write_bytes(data)
            except OSError as error:
                print(f"  не удалось записать {path.name}: {error}")
                stats.rejected += 1
                continue

            known_hashes.setdefault(digest, []).append(path)
            counted_hashes.add(digest)
            stats.saved += 1
            print(
                f"  [{section}] {stats.completed}/{IMAGES_PER_SECTION}: "
                f"{path.name}"
            )
            if stats.completed >= IMAGES_PER_SECTION:
                break

        if stats.completed >= IMAGES_PER_SECTION:
            break

        scroll_state = scroll_gallery(driver)
        top, height, client_height = scroll_state
        at_bottom = top + client_height >= height - 5
        if at_bottom and scroll_state == last_scroll_state and not new_urls:
            stale_rounds += 1
        last_scroll_state = scroll_state

    stats.missing = max(0, IMAGES_PER_SECTION - stats.completed)
    return stats


def normalized_sections(sections: Iterable[str]) -> list[str]:
    """Убирает дубли/видео и переносит «Все» в конец."""
    result: list[str] = []
    seen: set[str] = set()
    all_section: str | None = None
    for raw_section in sections:
        section = SPACE_RE.sub(" ", str(raw_section)).strip()
        if not section:
            continue
        key = section.casefold()
        if key == "видео":
            print("Раздел «Видео» пропущен: скрипт скачивает только фотографии.")
            continue
        if key in seen:
            continue
        seen.add(key)
        if key == "все":
            all_section = section
        else:
            result.append(section)
    if all_section:
        result.append(all_section)
    return result


def print_summary(business_name: str, folder: Path, stats: Iterable[SectionStats]) -> None:
    print(f"\nГотово: {business_name}")
    print(f"Папка: {folder.resolve()}")
    print("\nИтог по разделам:")
    for item in stats:
        print(
            f"  {item.section}: новых {item.saved}, "
            f"уже было {item.existing}, дублей {item.duplicates}, "
            f"отбраковано {item.rejected}, не хватило {item.missing}"
        )


def validate_settings() -> None:
    if not GALLERY_URL.strip():
        raise ParserError("Заполните GALLERY_URL в начале файла.")
    if not isinstance(IMAGES_PER_SECTION, int) or IMAGES_PER_SECTION <= 0:
        raise ParserError("IMAGES_PER_SECTION должен быть целым числом больше нуля.")
    if MIN_SCROLL_DELAY < 0 or MAX_SCROLL_DELAY < MIN_SCROLL_DELAY:
        raise ParserError("Проверьте MIN_SCROLL_DELAY и MAX_SCROLL_DELAY.")
    if MAX_STALE_SCROLLS <= 0:
        raise ParserError("MAX_STALE_SCROLLS должен быть больше нуля.")


def main() -> None:
    validate_settings()
    driver: webdriver.Chrome | None = None
    all_stats: list[SectionStats] = []

    try:
        driver = make_driver()
        business_name = open_gallery(driver, GALLERY_URL.strip())
        folder = Path(__file__).resolve().parent / OUTPUT_DIR / safe_name(business_name)
        folder.mkdir(parents=True, exist_ok=True)
        known_hashes = hashes_in_folder(folder)
        session = build_session(driver)

        available = wait_for_section_buttons(driver)
        requested = normalized_sections(SECTIONS)

        print(f"Бизнес: {business_name}")
        print(f"Папка: {folder.resolve()}")

        if not available:
            print("Разделов галереи нет — SECTIONS игнорируется, обрабатываю все фото.")
            all_stats.append(
                process_section(driver, session, folder, "Все", known_hashes)
            )
        else:
            if not requested:
                requested = [available.get("все", "Все")]
            print("Доступные разделы: " + ", ".join(available.values()))

            for section in requested:
                actual_name = available.get(section.casefold())
                if actual_name is None:
                    print(f"Раздел «{section}» в этой карточке отсутствует — пропускаю.")
                    continue
                print(f"\nРаздел: {actual_name}")
                if not select_section(driver, actual_name):
                    print(f"Не удалось переключиться на «{actual_name}» — пропускаю.")
                    continue
                session.headers["Referer"] = driver.current_url
                all_stats.append(
                    process_section(driver, session, folder, actual_name, known_hashes)
                )

        print_summary(business_name, folder, all_stats)
    except KeyboardInterrupt:
        print("\nОстановлено вручную. Уже скачанные фотографии сохранены.")
    except (ParserError, requests.RequestException, WebDriverException) as error:
        print(f"\nОшибка: {error}")
    finally:
        if driver is not None:
            from app.driver_registry import quit_driver
            quit_driver(driver)


if __name__ == "__main__":
    main()
