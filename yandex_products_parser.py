"""Сохраняет товары и фотографии из карточки Яндекс.Карт в menu.md.

Установка: pip install -r requirements.txt
Запуск: python yandex_products_parser.py

Укажите PRODUCTS_URL ниже. Результат появится в output/<название бизнеса>/.
"""

from __future__ import annotations

import hashlib
import os
import random
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver import ActionChains, Keys
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


# ============================= НАСТРОЙКИ =============================

PRODUCTS_URL = (
    "https://yandex.ru/maps/org/s_parfum_cosmetic/93907228443/prices/"
    "?from=tableau_yabro&ll=37.601712%2C55.738992&mode=search"
    "&sll=37.601712%2C55.738992&sspn=0.176365%2C0.206723"
    "&tab=prices&text=sparfume&z=11.77"
)
OUTPUT_DIR = "output"
HEADLESS = False
MANUAL_CAPTCHA_WAIT = False

PAGE_TIMEOUT = 20
DOWNLOAD_TIMEOUT = 30
MIN_SCROLL_DELAY = 0.7
MAX_SCROLL_DELAY = 1.2
MAX_STALE_SCROLLS = 5
MAX_PHOTO_STEPS = 500  # Защита от сломанной бесконечной галереи.
MAX_IMAGE_BYTES = 30 * 1024 * 1024

# =====================================================================


WINDOWS_FORBIDDEN_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
SPACE_RE = re.compile(r"\s+")
WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
SUPPORTED_EXTENSIONS = {".jpg", ".png", ".webp", ".avif", ".gif"}
GROUP_SELECTOR = ".business-full-items-grouped-view__category"
ITEM_SELECTOR = ".business-full-items-grouped-view__item"
SCROLL_SELECTOR = ".scroll__container"
MODAL_SELECTOR = ".photos-player-view"


class ParserError(RuntimeError):
    """Ошибка с сообщением для пользователя."""


@dataclass
class Product:
    category: str
    title: str
    description: str = ""
    price: str = ""
    detail: str = ""
    image_urls: list[str] = field(default_factory=list)
    image_paths: list[Path] = field(default_factory=list)

    def identity(self) -> tuple[str, str, str, str, str]:
        return (self.category, self.title, self.description, self.price, self.detail)


def safe_name(value: str, fallback: str = "без_названия", limit: int = 100) -> str:
    cleaned = WINDOWS_FORBIDDEN_RE.sub("_", value)
    cleaned = SPACE_RE.sub("_", cleaned).strip(" ._")[:limit].rstrip(" ._")
    if not cleaned or cleaned.split(".", 1)[0].upper() in WINDOWS_RESERVED:
        return fallback
    return cleaned


def clean_text(value: str | None) -> str:
    return SPACE_RE.sub(" ", value or "").strip()


def markdown_text(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]<>])", r"\\\1", value).replace("\n", " ")


def validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"yandex.ru", "www.yandex.ru"}:
        raise ParserError("PRODUCTS_URL должен быть ссылкой на yandex.ru/maps/org/.")
    if not re.search(r"^/maps/org/[^/]+/\d+(/|$)", parsed.path):
        raise ParserError("PRODUCTS_URL должен вести на карточку организации Яндекс.Карт.")
    if not 0 <= MIN_SCROLL_DELAY <= MAX_SCROLL_DELAY or MAX_STALE_SCROLLS < 1:
        raise ParserError("Проверьте настройки задержки и прокрутки.")


def make_driver() -> webdriver.Chrome:
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
    if "showcaptcha" in driver.current_url.lower() or "captcha" in driver.title.lower():
        return True
    elements = driver.find_elements(
        By.CSS_SELECTOR,
        'form[action*="checkcaptcha"], input[name*="captcha"], .CheckboxCaptcha, .SmartCaptcha',
    )
    if any(element.is_displayed() for element in elements):
        return True
    try:
        body = driver.find_element(By.TAG_NAME, "body").text[:5000].casefold()
    except WebDriverException:
        return False
    return body.strip() == "limited" or "я не робот" in body or "подтвердите, что вы не робот" in body


def wait_for_manual_captcha(driver: webdriver.Chrome) -> None:
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
        raise ParserError("Проверка Яндекса не была пройдена за 2 минуты; задание остановлено.")
    if HEADLESS or not sys.stdin.isatty():
        raise ParserError("Яндекс показал капчу. Повторите задание позже или откройте карточку вручную.")
    print("Яндекс показал капчу. Пройдите проверку в открытом Chrome.")
    input("После проверки нажмите Enter... ")
    if page_has_captcha(driver):
        raise ParserError("Капча всё ещё открыта; запуск остановлен.")


def open_products(driver: webdriver.Chrome, url: str) -> str:
    heading = None
    for attempt in range(3):
        driver.get(url)
        wait_for_manual_captcha(driver)
        try:
            heading = WebDriverWait(driver, PAGE_TIMEOUT).until(
                EC.visibility_of_element_located((By.CSS_SELECTOR, "h1"))
            )
            break
        except TimeoutException as error:
            wait_for_manual_captcha(driver)
            if attempt == 2:
                body = clean_text(driver.find_element(By.TAG_NAME, "body").text[:250])
                raise ParserError(
                    "Не удалось открыть карточку и прочитать название бизнеса. "
                    f"Ответ страницы: {body or 'пустая страница'}."
                ) from error
            time.sleep(1)
    assert heading is not None
    business_name = clean_text(heading.text)
    if not business_name:
        raise ParserError("В карточке не найдено название бизнеса.")

    if "/prices" not in driver.current_url.lower():
        links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/prices/"]')
        if not links:
            raise ParserError("В карточке нет вкладки «Товары и услуги».")
        driver.execute_script("arguments[0].click();", links[0])

    try:
        WebDriverWait(driver, PAGE_TIMEOUT).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, SCROLL_SELECTOR))
        )
        WebDriverWait(driver, PAGE_TIMEOUT).until(
            lambda current: current.find_elements(By.CSS_SELECTOR, GROUP_SELECTOR)
            or "товаров и услуг пока нет" in current.find_element(By.TAG_NAME, "body").text.casefold()
        )
    except TimeoutException as error:
        wait_for_manual_captcha(driver)
        raise ParserError("Раздел товаров не загрузился или в нём нет позиций.") from error
    return business_name


def visible_products(driver: webdriver.Chrome) -> list[dict]:
    """Снимает данные прямо с карточек, сохраняя порядок категорий и товаров."""
    return driver.execute_script(
        """
        const text = (root, selector) => (root.querySelector(selector)?.textContent || '').trim();
        return Array.from(document.querySelectorAll(arguments[0])).flatMap((group, gi) => {
          const category = text(group, '.business-full-items-grouped-view__title') || 'Без категории';
          return Array.from(group.querySelectorAll(arguments[1])).map((card, ii) => ({
            group_index: gi,
            item_index: ii,
            category,
            title: text(card, '.related-item-photo-view__title'),
            description: text(card, '.related-item-photo-view__description'),
            price: text(card, '.related-product-view__price'),
            detail: text(card, '.related-product-view__volume'),
            preview: card.querySelector('.related-item-photo-view img')?.currentSrc ||
                     card.querySelector('.related-item-photo-view img')?.src || ''
          })).filter(item => item.title);
        });
        """,
        GROUP_SELECTOR,
        ITEM_SELECTOR,
    ) or []


def modal_state(driver: webdriver.Chrome) -> dict:
    return driver.execute_script(
        """
        const modal = document.querySelector(arguments[0]);
        if (!modal) return {urls: [], active: '', single: true};
        const images = Array.from(modal.querySelectorAll('.photos-player-view__tape img.media-wrapper__media'));
        const urls = images.map(img => img.currentSrc || img.src).filter(url => url.startsWith('http'));
        const active = images.find(img => img.closest('.media-wrapper')?.getAttribute('aria-hidden') === 'false');
        return {urls, active: active?.currentSrc || active?.src || '',
                single: !!modal.querySelector('.photos-player-view__frame-wrapper._single')};
        """,
        MODAL_SELECTOR,
    )


def original_url(url: str) -> str:
    """Превью Яндекса использует тот же идентификатор файла, но меньший размер."""
    return re.sub(r"/(?:M_height|M_width|S_height|S_width)(?=$|\?)", "/orig", url)


def collect_item_images(driver: webdriver.Chrome, row: dict) -> list[str]:
    preview = row.get("preview") or ""
    fallback = [original_url(preview)] if preview.startswith("http") else []
    try:
        group = driver.find_elements(By.CSS_SELECTOR, GROUP_SELECTOR)[row["group_index"]]
        card = group.find_elements(By.CSS_SELECTOR, ITEM_SELECTOR)[row["item_index"]]
        trigger = card.find_element(By.CSS_SELECTOR, ".related-item-photo-view")
        driver.execute_script("arguments[0].click();", trigger)
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, MODAL_SELECTOR)))
    except (IndexError, TimeoutException, WebDriverException):
        return fallback

    urls: list[str] = []
    seen: set[str] = set()
    try:
        first_active = ""
        stale = 0
        for step in range(MAX_PHOTO_STEPS):
            state = modal_state(driver)
            active = state["active"]
            if not first_active:
                first_active = active
            old_count = len(seen)
            for url in state["urls"]:
                if url not in seen:
                    seen.add(url)
                    urls.append(url)
            if state["single"]:
                break

            container = driver.find_element(By.CSS_SELECTOR, ".photos-player-view__container")
            container.send_keys(Keys.ARROW_RIGHT)
            try:
                WebDriverWait(driver, 2).until(
                    lambda current: (new := modal_state(current))["active"] != active
                    or len(new["urls"]) > len(state["urls"])
                )
            except TimeoutException:
                pass
            next_active = modal_state(driver)["active"]
            if next_active == first_active and step > 0:
                break
            stale = stale + 1 if next_active == active and len(seen) == old_count else 0
            if stale >= 2:
                break
        else:
            print(f"  предупреждение: достигнут предел просмотра фото товара «{row['title']}»")
    finally:
        try:
            close = driver.find_element(By.CSS_SELECTOR, ".photos-player-view .media-header__button._type_close")
            driver.execute_script("arguments[0].click();", close)
            WebDriverWait(driver, 5).until(EC.invisibility_of_element_located((By.CSS_SELECTOR, MODAL_SELECTOR)))
        except (TimeoutException, WebDriverException):
            ActionChains(driver).send_keys(Keys.ESCAPE).perform()

    return urls or fallback


def expand_more(driver: webdriver.Chrome) -> bool:
    button = driver.execute_script(
        """
        const panel = document.querySelector(arguments[0]);
        if (!panel) return null;
        return Array.from(panel.querySelectorAll('button, [role="button"]')).find(el =>
          /^(показать ещё|ещё товары|показать все)$/i.test((el.textContent || '').trim())
          && el.getClientRects().length);
        """,
        SCROLL_SELECTOR,
    )
    if button is None:
        return False
    driver.execute_script("arguments[0].click();", button)
    return True


def scroll_products(driver: webdriver.Chrome) -> tuple[int, int, int]:
    values = driver.execute_script(
        """
        const panel = document.querySelector(arguments[0]);
        if (!panel) return null;
        const step = Math.max(Math.floor(panel.clientHeight * 0.8), 500);
        panel.scrollTop = Math.min(panel.scrollTop + step, panel.scrollHeight);
        panel.dispatchEvent(new Event('scroll', {bubbles: true}));
        return [Math.floor(panel.scrollTop), panel.scrollHeight, panel.clientHeight];
        """,
        SCROLL_SELECTOR,
    )
    if values is None:
        raise ParserError("Пропал список товаров.")
    time.sleep(random.uniform(MIN_SCROLL_DELAY, MAX_SCROLL_DELAY))
    return tuple(int(value) for value in values)


def collect_products(driver: webdriver.Chrome) -> list[Product]:
    products: list[Product] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    stale = 0
    previous_scroll: tuple[int, int, int] | None = None

    while stale < MAX_STALE_SCROLLS:
        wait_for_manual_captcha(driver)
        added = 0
        for row in visible_products(driver):
            product = Product(
                category=clean_text(row["category"]),
                title=clean_text(row["title"]),
                description=clean_text(row["description"]),
                price=clean_text(row["price"]),
                detail=clean_text(row["detail"]),
            )
            if product.identity() in seen:
                continue
            product.image_urls = collect_item_images(driver, row)
            seen.add(product.identity())
            products.append(product)
            added += 1
            print(f"  {product.category}: {product.title}")

        if expand_more(driver):
            stale = 0
            time.sleep(random.uniform(MIN_SCROLL_DELAY, MAX_SCROLL_DELAY))
            continue

        position = scroll_products(driver)
        at_bottom = position[0] + position[2] >= position[1] - 5
        if added or not at_bottom or position != previous_scroll:
            stale = 0
        else:
            stale += 1
        previous_scroll = position

    return products


def build_session(driver: webdriver.Chrome) -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": driver.execute_script("return navigator.userAgent"),
            "Referer": driver.current_url,
            "Accept": "image/avif,image/webp,image/png,image/jpeg,image/gif,*/*;q=0.5",
        }
    )
    for cookie in driver.get_cookies():
        session.cookies.set(
            cookie["name"], cookie["value"],
            domain=cookie.get("domain"), path=cookie.get("path", "/"),
        )
    return session


def detect_image_extension(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if len(data) >= 16 and data[4:8] == b"ftyp" and any(
        brand in data[8:32] for brand in (b"avif", b"avis")
    ):
        return ".avif"
    return None


def download_image(session: requests.Session, url: str) -> tuple[bytes, str] | None:
    try:
        with session.get(url, timeout=DOWNLOAD_TIMEOUT, stream=True) as response:
            response.raise_for_status()
            if not response.headers.get("Content-Type", "").lower().startswith("image/"):
                return None
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    size += len(chunk)
                    if size > MAX_IMAGE_BYTES:
                        return None
                    chunks.append(chunk)
            data = b"".join(chunks)
    except requests.RequestException:
        return None
    extension = detect_image_extension(data)
    return (data, extension) if extension else None


def existing_hashes(folder: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    if folder.exists():
        for path in folder.iterdir():
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
                try:
                    result.setdefault(hashlib.sha256(path.read_bytes()).hexdigest(), path)
                except OSError:
                    pass
    return result


def save_images(products: list[Product], folder: Path, session: requests.Session) -> tuple[int, int]:
    folder.mkdir(parents=True, exist_ok=True)
    hashes = existing_hashes(folder)
    by_url: dict[str, Path] = {}
    saved = 0
    failed = 0
    for product_index, product in enumerate(products, start=1):
        for photo_index, url in enumerate(dict.fromkeys(product.image_urls), start=1):
            if url in by_url:
                path = by_url[url]
            else:
                downloaded = download_image(session, url)
                if downloaded is None:
                    failed += 1
                    print(f"  не удалось скачать фото: {product.title} ({url})")
                    continue
                data, extension = downloaded
                digest = hashlib.sha256(data).hexdigest()
                path = hashes.get(digest)
                if path is None:
                    stem = safe_name(product.title, "товар", 70)
                    path = folder / f"{product_index:04d}_{stem}_{photo_index:02d}_{digest[:12]}{extension}"
                    path.write_bytes(data)
                    hashes[digest] = path
                    saved += 1
                by_url[url] = path
            if path not in product.image_paths:
                product.image_paths.append(path)
    return saved, failed


def render_menu(business_name: str, products: list[Product]) -> str:
    lines = [f"# {markdown_text(business_name)}", ""]
    category = None
    ordinal = 0
    for product in products:
        if product.category != category:
            category = product.category
            ordinal = 0
            lines.extend((f"## {markdown_text(category)}", ""))
        ordinal += 1
        images = []
        for path in product.image_paths:
            relative = f"images/{quote(path.name)}"
            images.append(f"![{markdown_text(product.title)}]({relative})")
        if images:
            lines.append(f"{ordinal}. {images[0]}")
            lines.extend(f"   {image}" for image in images[1:])
            lines.append("")
            indent = "   "
        else:
            lines.append(f"{ordinal}. {markdown_text(product.title)}")
            indent = "   "

        fields = [product.title, product.description, product.price, product.detail]
        if not images:
            fields = fields[1:]
        fields = [markdown_text(value) for value in fields if value]
        lines.extend(f"{indent}{value}  " for value in fields)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_menu(menu_path: Path, content: str) -> None:
    temporary = menu_path.with_name(menu_path.name + ".tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, menu_path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    validate_url(PRODUCTS_URL.strip())
    driver: webdriver.Chrome | None = None
    try:
        driver = make_driver()
        business_name = open_products(driver, PRODUCTS_URL.strip())
        print(f"Бизнес: {business_name}")
        products = collect_products(driver)
        if not products:
            raise ParserError("В разделе не найдено ни одного товара. menu.md не изменён.")

        business_folder = Path(__file__).resolve().parent / OUTPUT_DIR / safe_name(business_name)
        image_folder = business_folder / "images"
        session = build_session(driver)
        saved, failed = save_images(products, image_folder, session)
        menu_path = business_folder / "menu.md"
        write_menu(menu_path, render_menu(business_name, products))
        print(f"\nГотово: {len(products)} товаров, {saved} новых фото, {failed} ошибок скачивания")
        print(f"Меню: {menu_path.resolve()}")
    except KeyboardInterrupt:
        print("\nОстановлено вручную. menu.md не изменён, если сбор не был завершён.")
    except (ParserError, requests.RequestException, WebDriverException, OSError) as error:
        print(f"\nОшибка: {error}")
    finally:
        if driver is not None:
            from app.driver_registry import quit_driver
            quit_driver(driver)


if __name__ == "__main__":
    main()
