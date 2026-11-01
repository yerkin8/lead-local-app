"""
Сбор лидов с Яндекс.Карт — исправленная версия ноутбука Yandex_parser_leads.ipynb

КУДА ПИШЕТСЯ БАЗА:
    база/<ниша>/il-<город>-<запрос>-<дата>.xlsx   — горячие лиды (is_lead)
    база/<ниша>/nl-<город>-<запрос>-<дата>.xlsx   — остальные (not lead)

Ниша определяется по запросу автоматически (см. NICHE_KEYWORDS), так что
файлы по разным городам и датам сами копятся в одной папке ниши. Пустой файл
не создаётся: если все собранные организации оказались лидами, nl- не появится.

Оба файла перезаписываются после КАЖДОЙ карточки — это страховка от капчи и
падений: прогон, прерванный на 900-й организации, ничего не теряет. Раньше
эту роль играл промежуточный CSV, теперь он не нужен.

ЛОГИКА ФИЛЬТРАЦИИ (важно):
Парсер сохраняет ВСЕ карточки, которые открыл, и помечает каждую типом сайта.
Признака сайта нет в выдаче (проверено — в сниппете нет внешних ссылок),
карточку всё равно приходится открывать. Раз открыли — данные сохраняем:
иначе перефильтровать по другому критерию можно только повторным прогоном,
а это лишний риск капчи.

ТИПЫ САЙТА (колонка site_type). Лид — тип, заданный режимом, при рейтинге
не ниже 4.0 и не менее 80 отзывов:
    none        — сайта нет вообще                     ← самый горячий лид
    builder     — визитка на конструкторе (Tilda, Creatium, Framer, Taplink,
                  clients.site от Яндекс.Бизнеса)      ← горячий: платит за заглушку
    dead        — сайт не открывается или пустая страница  ← горячий, отличный повод
    real        — полноценный сайт на CMS              ← не наш клиент

КАК ОПРЕДЕЛЯЕТСЯ КОНСТРУКТОР (важно, здесь была главная ошибка):
Сначала смотрим домен (BUILDER_DOMAINS), затем — если CHECK_SITE включён —
открываем сам сайт и ищем следы движка в HTML (колонка site_engine).
Проверка по одному домену не работала: конструктор у малого бизнеса стоит
на своём домене, и sil-beauty.ru (Framer), ambeauty-moscow.ru (Tilda),
sk-beauty.ru (Creatium) считались полноценными сайтами. В прогоне
03.09.2026 из-за этого 16 из 22 организаций попали в "real", а лидов
нашлось только 3. После проверки движка на тех же данных лидов 16 из 22.

ЧТО ИСПРАВЛЕНО ОТНОСИТЕЛЬНО ОРИГИНАЛА (проверено на живых Картах 02–03.09.2026):

1. БЛОКЕР. Селектор ".search-snippet-view__link-overlay._focusable" мёртв —
   Яндекс переименовал класс, из-за чего оригинал падал с TimeoutException.
   Заменено на поиск по href (a[href*="/maps/org/"]).
2. БЛОКЕР. openpyxl не ставился (pandas его не тянет) — падал df.to_excel().
3. Дедупликация: на одну организацию в выдаче до 6 ссылок (карточка, /gallery/,
   /reviews/, /prices/, /related/, рекламная с ?yclid=).
4. Регион больше не захардкожен (в оригинале всегда Москва, код 213).
5. Пауза между запросами — оригинал бил по Яндексу без задержек.
6. Промежуточное сохранение после каждой карточки.
7. Нормальная обработка ошибок вместо голых except.
8. Убран average_bill (поле про общепит).

ЧТО ИСПРАВЛЕНО 03.09.2026 ПО ИТОГАМ ПЕРВОГО БОЕВОГО ПРОГОНА:

9.  Категория была мусором ("Карты" у всех записей). Причина: селектор
    breadcrumbs-view__breadcrumb возвращает ["Карты", "Калуга", "Ремонт и
    отделка"], а find() брал ПЕРВУЮ крошку. Теперь берётся
    orgpage-categories-info-view__link ("Строительные и отделочные работы"),
    с фолбэком на последнюю хлебную крошку.
10. Адрес слипался ("ул. Гагарина, 13, Калугаэтаж 1") — get_text(strip=True)
    склеивал вложенные блоки без разделителя. Добавлен separator.
11. Собираются ВСЕ телефоны карточки, а не только первый.
12. Рейтинг и число отзывов пишутся числами, чтобы в Excel сортировались.
13. Фильтр по сайту больше не бинарный: визитка на конструкторе — это тоже
    горячий лид, раньше такие отбрасывались наравне с нормальными сайтами.

ЧТО ИСПРАВЛЕНО 04.09.2026:

16. ГЛАВНОЕ. Конструкторы определялись только по домену, поэтому почти все
    визитки считались полноценными сайтами и в лиды не попадали. Добавлена
    проверка самого сайта: скачиваем страницу и ищем движок (CHECK_SITE,
    колонка site_engine). На данных прогона 03.09.2026 лидов стало 16 из 22
    вместо 3.
17. Добавлен тип dead — сайт не открывается или отдаёт пустую страницу
    (пример: beautylime.ru отдаёт 273 байта JS-заглушки хостинга Beget).
    Это тоже лид.
18. Лидом считается подходящий тип сайта: none, builder, dead (или real в
    режиме all), если рейтинг не ниже 4.0 и отзывов не менее 80.
19. Сайт запрашивается с двумя попытками и раздельными таймаутами: часть
    сайтов на дешёвом хостинге отвечает дольше 10 секунд только с первого
    раза (sk-beauty.ru), и они ошибочно попадали в dead.

УСТАНОВКА:
    pip install -r requirements.txt

Нужен установленный Google Chrome. Драйвер Selenium скачает сам (Selenium 4.6+).

ЗАПУСК:
    python yandex_parser_fixed.py
"""

import re
import time
import random
import unicodedata
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

# ============ НАСТРОЙКИ ============

CITY = "казань"              # город
QUERY = "маникюр"               # ниша / что ищем
COUNT = 100                       # сколько организаций собрать (начни с 20!)
SKIP_EXISTING = True              # не открывать организации из прошлых выгрузок

NICHE = ""                         # папка ниши; пусто = определить по QUERY
OUTPUT_DIR = "база"                # корневая папка базы рядом со скриптом

# Кого считать горячим лидом и класть в файл il-:
#   "no_site"   — нет сайта совсем ("none") и визитки-конструкторы ("builder";
#                 они всё равно не сайт, а заглушка от Яндекс.Бизнеса — не
#                 конкурент нашей услуге, поэтому считаются лидом всегда)
#   "weak_site" — сохранено для совместимости с прежними настройками;
#                 сейчас эквивалентно no_site, потому что соцсети не парсятся
#   "all"       — не фильтровать, всё считается лидом
# Рекомендуется no_site. Режим all всё равно применяет минимум по рейтингу
# и количеству отзывов, чтобы в il- не попадали слабые карточки.
LEAD_MODE = "weak_site"

# Минимальные показатели для файла il-. Карточки с отсутствующим рейтингом
# или числом отзывов тоже остаются в nl-, чтобы не выдавать сомнительный лид.
MIN_LEAD_RATING = 4.0
MIN_LEAD_REVIEWS = 80

HEADLESS = False                 # True = без окна браузера (для длинных прогонов)

CHECK_SITE = True                  # открывать сайт и смотреть, на чём он сделан
SITE_TIMEOUT = 20                  # сколько ждать ответа сайта, секунд

MIN_DELAY = 1.5                    # пауза между карточками, секунд (не ставь 0!)
MAX_DELAY = 3.5                    # верхняя граница паузы — рандом усложняет детект

# ===================================

SEARCH_URL = "https://yandex.ru/maps/?text={query}"
ORG_URL_RE = re.compile(r"(https://yandex\.ru/maps/org/[^/]+/\d+/)")
ORG_ID_RE = re.compile(r"/maps/org/[^/]+/(\d+)(?:/|$)")

# Домены, на которых стоят сайты-заглушки. Формально сайт есть, фактически это
# визитка в три экрана — такой бизнес наш клиент, а не отказ.
# clients.site — бесплатный сайт, который Яндекс.Бизнес генерит сам.
BUILDER_DOMAINS = (
    "clients.site", "business.site", "tilda.ws", "wixsite.com", "taplink.cc",
    "nethouse.ru", "a5.ru", "ucoz.ru", "narod.ru", "wordpress.com",
    "blogspot.com", "flexbe.ru", "readymag.com", "notion.site", "carrd.co",
    "vk.com", "vk.ru", "instagram.com", "t.me", "yandex.ru",
)

HOT_TYPES = {
    # Лид — все, у кого нет полноценного своего сайта. Визитка на
    # конструкторе, заглушка от Яндекс.Бизнеса и неработающий сайт
    # сайтом не считаются.
    #
    # Соцсети намеренно не собираются: для работы с лидами используются
    # телефон и автоматически построенная Telegram-ссылка.
    "no_site": {"none", "builder", "dead"},
    "weak_site": {"none", "builder", "dead"},
    "all": {"none", "builder", "dead", "real"},
}

# Движки-конструкторы: если сайт собран на таком, это визитка, а не разработка.
# Ловим по содержимому страницы, а не по домену: домен у таких сайтов свой
# (sil-beauty.ru — Framer, ambeauty-moscow.ru — Tilda), и проверка по
# BUILDER_DOMAINS их не видела — из-за этого 6 из 16 сайтов в прогоне
# 03.09.2026 ошибочно считались полноценными.
BUILDER_ENGINES = {
    "Tilda": ("tildacdn", "tilda.ws", "tilda.cc"),
    "Creatium": ("creatium",),
    "Framer": ("framerusercontent", "framer.com"),
    "Readymag": ("readymag.com",),
    "Flexbe": ("flexbe.ru", "flexbe.com"),
    "Craftum": ("craftum",),
    "Nethouse": ("nethouse.ru",),
    "Wix": ("wixstatic", "wix.com"),
    "Taplink": ("taplink.cc",),
    "mottor/LPmotor": ("lpmotor", "mottor"),
    "Webflow": ("website-files.com", "webflow.io"),
    "uKit": ("ukit.com",),
    "Carrd": ("carrd.co",),
    "Яндекс.Бизнес": ("clients.site",),
}

# Полноценные CMS — это уже сайт, такой бизнес нам не клиент.
# Внимание: bitrix24.ru маркером не годится — это виджет CRM-формы,
# его вешают на любой чужой движок (пример: petrovka-beauty.ru).
CMS_ENGINES = {
    "WordPress": ("wp-content", "wp-includes"),
    "Joomla": ("joomla",),
    "1С-Битрикс": ("/bitrix/",),
    "Shopify": ("cdn.shopify.com",),
    "InSales": ("insales.ru",),
    "OpenCart": ("catalog/view/theme",),
}

# Ниша для папки. Ключи ищутся как подстроки в запросе, поэтому пишем корни
# ("тренировк" ловит и "тренировки", и "тренировка"). Порядок важен: первое
# совпадение выигрывает, поэтому специфичное идёт раньше общего.
NICHE_KEYWORDS = {
    "недвижимость": ("риэлтор", "риелтор", "недвижимост", "аренда квартир",
                     "агентство недвижимости"),
    "стройка": ("ремонт квартир", "отделочные", "отделка", "строительств",
                "строительная", "натяжные потолки", "прораб", "сантехник",
                "электрик", "кровля", "остекление", "мебель на заказ"),
    "архитектура": ("архитектор", "дизайн интерьера", "дизайнер интерьера",
                    "проектирование", "ландшафтн"),
    "инфобиз": ("коуч", "наставник", "бизнес-консультант", "консалтинг",
                "продюсер", "эксперт"),
    "репетиторы": ("репетитор", "подготовка к егэ", "подготовка к огэ",
                   "языковая школа", "английск", "логопед"),
    "фитнес": ("фитнес", "тренировк", "тренажёрн", "тренажерн", "спортзал",
               "спортивный клуб", "йога", "пилатес", "единоборств", "бассейн",
               "персональный тренер"),
    "психологи": ("психолог", "психотерапевт", "расстановщик"),
    "бьюти": ("барбер", "парикмахер", "салон красоты", "маникюр", "ногт",
              "косметолог", "брови", "ресниц", "эпиляц", "депиляц", "тату",
              "визажист", "студия красоты", "спа"),
    "медицина": ("клиника", "стоматолог", "медцентр", "медицинский центр",
                 "массаж", "узи", "ветеринар"),
    "курсы": ("курсы", "учебный центр", "автошкола", "школа программирования",
              "детский центр", "танцевальн"),
    "маркетплейсы": ("маркетплейс", "поставщик", "оптов", "фулфилмент", "склад"),
    "трейдинг": ("трейдинг", "инвестиц", "брокер", "финансовый советник"),
    "рестораны": ("ресторан", "кафе", "кофейн", "пиццери", "суши", "бар",
                  "доставка еды", "кондитерск", "пекарн"),
}

WINDOWS_FORBIDDEN_RE = re.compile(r'[<>:"/\\|?*]+')


def detect_niche(query):
    """Ниша по тексту запроса. Не угадали — папкой станет сам запрос."""
    text = query.lower()
    for niche, keywords in NICHE_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            return niche
    return query.strip().lower() or "прочее"


def safe_name(text):
    """Имя, пригодное для файла и папки в Windows."""
    cleaned = WINDOWS_FORBIDDEN_RE.sub("_", text).strip(" .")
    return re.sub(r"\s+", "_", cleaned) or "без_названия"


def make_driver():
    options = Options()
    # отключаем картинки — заметно быстрее
    options.add_experimental_option(
        "prefs", {"profile.managed_default_content_settings.images": 2}
    )
    if HEADLESS:
        options.add_argument("--headless=new")
    options.add_argument("--window-size=1400,900")
    return webdriver.Chrome(options=options)


def normalize_match_text(text):
    """Нормализует город для сравнения с именами файлов базы."""
    text = unicodedata.normalize("NFKC", str(text)).casefold().replace("ё", "е")
    return re.sub(r"[\s_-]+", " ", text).strip()


def extract_org_id(url):
    """ID организации не меняется при переименовании URL или добавлении меток."""
    match = ORG_ID_RE.search(str(url or ""))
    return match.group(1) if match else None


def load_existing_org_ids(folder, city):
    """Читает ID сохранённых карточек города из файлов il- и nl-."""
    folder = Path(folder)
    if not folder.exists():
        return set()

    city_key = normalize_match_text(safe_name(city))
    existing_ids = set()
    for path in sorted(folder.glob("*.xlsx")):
        name = path.stem
        if not name.casefold().startswith(("il-", "nl-")):
            continue
        file_city = name[3:].split("-", 1)[0]
        if normalize_match_text(file_city) != city_key:
            continue

        try:
            frame = pd.read_excel(path, usecols=["url"])
        except Exception as error:
            raise RuntimeError(
                f"Не удалось прочитать колонку url из файла базы {path}: {error}. "
                "Исправьте или переместите этот файл, затем запустите парсер снова."
            ) from error

        invalid_count = 0
        for value in frame["url"]:
            if pd.isna(value) or not str(value).strip():
                invalid_count += 1
                continue
            org_id = extract_org_id(value)
            if org_id:
                existing_ids.add(org_id)
            else:
                invalid_count += 1
        if invalid_count:
            print(f"Предупреждение: в {path.name} пропущено некорректных/пустых "
                  f"URL: {invalid_count}.")

    return existing_ids


def _read_result_links(driver):
    """Одним вызовом читает href карточек в текущем контейнере выдачи."""
    return driver.execute_script(
        """
        const snippets = Array.from(document.querySelectorAll(
            '.search-business-snippet-view__content'));
        if (!snippets.length) return {hrefs: [], count: 0};
        const first = snippets[0];
        const list = first.closest('.search-list-view__list') ||
                     first.closest('.search-list-view') ||
                     first.closest('.scroll') || first.parentElement;
        const hrefs = Array.from(list.querySelectorAll('a[href*="/maps/org/"]'))
            .map(a => a.href);
        return {hrefs, count: snippets.length};
        """
    )


def _page_looks_like_captcha(driver):
    try:
        return bool(driver.execute_script(
            """const t = (document.body?.innerText || '').toLowerCase();
            return !!document.querySelector('iframe[src*="captcha"], '
                + '[class*="captcha"], input[name*="captcha"]') ||
                t.includes('введите символы с картинки') ||
                t.includes('подтвердите, что вы не робот');"""
        ))
    except Exception:
        return False


def collect_org_links(driver, target_count, known_org_ids=None):
    """Собирает новые уникальные ссылки, проходя виртуализированную выдачу."""
    known_org_ids = set(known_org_ids or ())
    search_text = f"{CITY} {QUERY}"
    driver.get(SEARCH_URL.format(query=search_text))

    scan_started = time.perf_counter()
    link_read_seconds = 0.0
    wait_seconds = 0.0
    seen_ids = set()
    new_links = []
    skipped_existing_ids = set()
    stale_rounds = 0
    stop_reason = "достигнут лимит новых ссылок"
    stale_limit = 18

    def consume_result_links():
        nonlocal link_read_seconds
        started = time.perf_counter()
        result = _read_result_links(driver)
        link_read_seconds += time.perf_counter() - started
        added_ids = set()
        if not result or not result.get("count"):
            return added_ids, None
        for href in result.get("hrefs", []):
            match = ORG_URL_RE.match(href or "")
            if not match:
                continue
            org_id = extract_org_id(match.group(1))
            if not org_id or org_id in seen_ids:
                continue
            seen_ids.add(org_id)
            added_ids.add(org_id)
            if org_id in known_org_ids:
                skipped_existing_ids.add(org_id)
            elif len(new_links) < target_count:
                new_links.append(match.group(1))
        return added_ids, result

    # Ждём появления карточек результата, не ссылок в произвольной части страницы.
    initial_wait_started = time.perf_counter()
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_all_elements_located(
                (By.CLASS_NAME, "search-business-snippet-view__content")
            )
        )
    except Exception:
        wait_seconds += time.perf_counter() - initial_wait_started
        stop_reason = "обнаружена капча" if _page_looks_like_captcha(driver) \
            else "тайм-аут ожидания карточек выдачи"
        print(f"Сбор остановлен: {stop_reason}.")
        print(f"Время чтения ссылок: {link_read_seconds:.2f} с; "
              f"ожидание подгрузки: {wait_seconds:.2f} с; "
              f"всего: {time.perf_counter() - scan_started:.2f} с.")
        return []
    wait_seconds += time.perf_counter() - initial_wait_started

    added, result = consume_result_links()
    if result is None:
        stop_reason = "обнаружена капча" if _page_looks_like_captcha(driver) \
            else "карточки выдачи исчезли"
    while len(new_links) < target_count and result and result.get("count"):
        # Прокрутка контейнера выдачи, который может переиспользовать DOM-карточки.
        driver.execute_script(
            """const snippets = Array.from(document.querySelectorAll(
                '.search-business-snippet-view__content'));
            if (!snippets.length) return false;
            const s = snippets[snippets.length - 1];
            const l = s.closest('.search-list-view__list') ||
                      s.closest('.search-list-view') || s.closest('.scroll') ||
                      s.parentElement;
            const c = s.closest('.scroll') || l;
            c.scrollTop = c.scrollHeight;
            c.dispatchEvent(new Event('scroll', {bubbles: true}));
            s.scrollIntoView({block: 'end'});
            return true;"""
        )

        # Ждём продвижения, проверяя DOM раз в полсекунды, максимум 5 секунд.
        wait_started = time.perf_counter()
        deadline = wait_started + 5.0
        round_added_ids = set()
        result = None
        while time.perf_counter() < deadline:
            time.sleep(0.5)
            added, result = consume_result_links()
            round_added_ids.update(added)
            if not result or not result.get("count"):
                stop_reason = "обнаружена капча" if _page_looks_like_captcha(driver) \
                    else "карточки выдачи исчезли"
                break
            if added:
                break
        wait_seconds += time.perf_counter() - wait_started
        if not result or not result.get("count"):
            break

        if round_added_ids:
            stale_rounds = 0
        else:
            stale_rounds += 1
            if stale_rounds >= stale_limit:
                stop_reason = "тайм-аут: новых ID нет после 18 попыток"
                if _page_looks_like_captcha(driver):
                    stop_reason = "обнаружена капча"
                break
        print(f"Новых ссылок: {len(new_links)}/{target_count}; "
              f"известных пропущено: {len(skipped_existing_ids)}", end="\r")

    if len(new_links) >= target_count:
        stop_reason = "достигнут лимит новых ссылок"
    elapsed = time.perf_counter() - scan_started
    print(f"\nСбор ссылок завершён: {stop_reason}. Новых ссылок: "
          f"{len(new_links)}; известных организаций пропущено: "
          f"{len(skipped_existing_ids)}; всего просмотрено ID: {len(seen_ids)}.")
    print(f"Время чтения ссылок: {link_read_seconds:.2f} с; "
          f"ожидание подгрузки: {wait_seconds:.2f} с; всего: {elapsed:.2f} с.")
    return new_links


def text_of(soup, tag, class_name):
    """Достаёт текст элемента, возвращает None если элемента нет.

    separator=" " обязателен: без него адрес склеивался в "Калугаэтаж 1".
    """
    element = soup.find(tag, class_=class_name)
    if element is None:
        return None
    return re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip() or None


def all_texts(soup, tag, class_name):
    """Все совпадения (телефонов на карточке может быть несколько)."""
    values = []
    for element in soup.find_all(tag, class_=class_name):
        text = re.sub(r"\s+", " ", element.get_text(" ", strip=True)).strip()
        if text and text not in values:
            values.append(text)
    return values


def get_category(soup):
    """Категория организации.

    Основной источник — блок категорий на карточке. Хлебные крошки только
    фолбэком и обязательно последняя: первая крошка — это всегда "Карты",
    именно из-за неё в базе первого прогона у всех записей стояло "Карты".
    """
    categories = all_texts(soup, "a", "orgpage-categories-info-view__link")
    if categories:
        return ", ".join(categories[:3])

    crumbs = all_texts(soup, "a", "breadcrumbs-view__breadcrumb")
    # выкидываем служебные крошки "Карты" и город
    crumbs = [c for c in crumbs if c.lower() not in ("карты", CITY.lower())]
    return crumbs[-1] if crumbs else None


def telegram_links_from_phone(phone):
    """Финальный преобразователь колонки phone в Markdown-ссылки Telegram.

    У бизнеса может быть несколько номеров, поэтому возвращается ссылка для
    каждого корректно записанного российского номера. Неполные и зарубежные
    номера пропускаются.
    """
    if not phone or pd.isna(phone):
        return None

    links = []
    for raw_phone in str(phone).split(";"):
        digits = re.sub(r"\D", "", raw_phone)
        # Российский номер из Карты может начинаться с 8; Telegram нужен +7.
        if len(digits) == 11 and digits[0] == "8":
            digits = "7" + digits[1:]
        if len(digits) != 11 or not digits.startswith("7"):
            continue
        link = f"https://t.me/+{digits}"
        markdown_link = f"[{link}]({link})"
        if markdown_link not in links:
            links.append(markdown_link)
    return "; ".join(links) or None


def detect_engine(html):
    """Движок сайта по его HTML. Возвращает (название, тип сайта).

    Чистая функция без сети — сюда приходит уже скачанная страница.
    """
    if html is None:
        return "недоступен", "dead"

    low = html.lower()
    # почти пустой ответ — это заглушка хостинга или неподнятый сайт.
    # Пример из прогона: beautylime.ru отдаёт 273 байта JS-редиректа Beget.
    if len(low.strip()) < 1000:
        return "пустая страница", "dead"

    for engine, markers in BUILDER_ENGINES.items():
        if any(marker in low for marker in markers):
            return engine, "builder"
    for engine, markers in CMS_ENGINES.items():
        if any(marker in low for marker in markers):
            return engine, "real"
    return "неизвестно", "real"


def fetch_site(site):
    """Скачивает начало страницы сайта. None, если сайт не открылся.

    Запрос идёт к сайту бизнеса, а не к Яндексу, — капчу это не провоцирует.
    Просроченный сертификат или таймаут тоже считаем «сайта нет»: для оффера
    это такой же повод зайти, как и полное отсутствие сайта.
    """
    url = site if site.startswith("http") else f"https://{site}"
    # Две попытки: часть сайтов на дешёвом хостинге «просыпается» дольше 10 с,
    # а со второго раза отвечает за пару секунд (пример: sk-beauty.ru).
    # Ложный «dead» дороже лишнего запроса: из-за него в лиды попадёт бизнес,
    # у которого сайт на самом деле есть.
    for attempt in range(2):
        try:
            response = requests.get(
                url,
                timeout=(5, SITE_TIMEOUT),   # (соединение, чтение)
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                stream=True,
            )
            if response.status_code >= 400:
                return None
            chunks, total = [], 0
            for chunk in response.iter_content(8192):
                chunks.append(chunk)
                total += len(chunk)
                if total >= 300_000:   # больше для определения движка не нужно
                    break
            return b"".join(chunks).decode(response.encoding or "utf-8", "ignore")
        except Exception:
            if attempt == 0:
                continue
            return None
    return None


def classify_site(site, engine=None):
    """Тип сайта — на нём построена вся фильтрация лидов.

    none / builder / dead / real
    """
    if not site:
        return "none"
    low = site.lower()
    if any(domain in low for domain in BUILDER_DOMAINS):
        return "builder"
    if engine is not None:
        return engine          # результат проверки самого сайта
    return "real"


def to_number(text, pattern=r"[\d]+(?:[.,]\d+)?"):
    """'5,0' -> 5.0, '717 оценок' -> 717.0. Нужно, чтобы Excel сортировал."""
    if not text:
        return None
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def parse_org(driver, url):
    """Открывает карточку организации и достаёт нужные поля."""
    driver.get(url)
    time.sleep(random.uniform(MIN_DELAY, MAX_DELAY))
    soup = BeautifulSoup(driver.page_source, "html.parser")

    # селекторы проверены на живых Яндекс.Картах 02–03.09.2026
    site = text_of(soup, "span", "business-urls-view__text")
    phones = all_texts(soup, "div", "orgpage-phones-view__phone-number")
    reviews = to_number(text_of(soup, "div", "business-header-rating-view__text"))

    # открываем сам сайт и смотрим, на чём он сделан: по домену конструктор
    # не виден, потому что домен у бизнеса свой
    engine_name, engine_type = None, None
    if site and CHECK_SITE:
        engine_name, engine_type = detect_engine(fetch_site(site))

    return {
        "name": text_of(soup, "h1", "orgpage-header-view__header"),
        "category": get_category(soup),
        "phone": "; ".join(phones) or None,
        "site": site,
        "site_engine": engine_name,
        "site_type": classify_site(site, engine_type),
        "rating": to_number(text_of(soup, "span", "business-rating-badge-view__rating-text")),
        "reviews": int(reviews) if reviews is not None else None,
        "address": text_of(soup, "a", "orgpage-header-view__address"),
        "url": url,
    }


def build_paths():
    """база/<ниша>/il-<город>-<запрос>-<дата>.xlsx и nl-…"""
    niche = safe_name(NICHE.strip() or detect_niche(QUERY))
    folder = Path(__file__).resolve().parent / OUTPUT_DIR / niche
    folder.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    base_name = safe_name(f"{CITY}-{QUERY}-{stamp}")
    return niche, folder / f"il-{base_name}.xlsx", folder / f"nl-{base_name}.xlsx"


def save_tables(rows, lead_path, rest_path):
    """Пишет обе таблицы. Вызывается после каждой карточки — это страховка.

    Пустой файл не создаём. Ошибку записи не считаем фатальной: если файл
    открыт в Excel, глупо ронять прогон — данные останутся в памяти
    и запишутся на следующей карточке.
    """
    df = pd.DataFrame(rows)
    # Telegram намеренно формируется на финальном этапе из готовой колонки
    # phone, а не парсится на Яндекс.Картах. Колонка идёт сразу после phone.
    df.insert(df.columns.get_loc("phone") + 1, "telegram",
              df["phone"].apply(telegram_links_from_phone))
    # Int64 (а не int) — чтобы пустые отзывы остались пустыми,
    # а не превратились в 717.0 при выгрузке
    df["reviews"] = df["reviews"].astype("Int64")

    parts = ((lead_path, df[df["is_lead"]]), (rest_path, df[~df["is_lead"]]))
    for path, part in parts:
        if part.empty:
            continue
        try:
            part.drop(columns=["is_lead"]).to_excel(path, index=False)
        except PermissionError:
            print(f"  не могу записать {path.name} — файл открыт в Excel? "
                  f"продолжаю, сохраню на следующей карточке")
    return df


def main():
    if LEAD_MODE not in HOT_TYPES:
        raise SystemExit(f"LEAD_MODE должен быть одним из: {', '.join(HOT_TYPES)}")
    hot_types = HOT_TYPES[LEAD_MODE]

    niche, lead_path, rest_path = build_paths()
    print(f"Ниша: {niche} | папка: {lead_path.parent}\n")

    known_org_ids = set()
    if SKIP_EXISTING:
        try:
            known_org_ids = load_existing_org_ids(lead_path.parent, CITY)
        except RuntimeError as error:
            raise SystemExit(str(error)) from error
        print(f"В базе найдено организаций для города {CITY}: "
              f"{len(known_org_ids)}.\n")

    driver = make_driver()
    rows = []

    try:
        links = collect_org_links(driver, COUNT, known_org_ids)
        if not links:
            print("Ссылок не собрано, выхожу.")
            return

        print(f"Нашёл {len(links)} организаций, собираю данные...\n")

        for index, url in enumerate(links, start=1):
            try:
                row = parse_org(driver, url)
            except Exception as error:
                print(f"  [{index}] пропускаю {url}: {type(error).__name__}")
                continue

            # В il- попадают только перспективные карточки с достаточной
            # репутацией: минимум 4.0 рейтинга и 80 отзывов.
            row["is_lead"] = (
                row["site_type"] in hot_types
                and row["rating"] is not None
                and row["rating"] >= MIN_LEAD_RATING
                and row["reviews"] is not None
                and row["reviews"] >= MIN_LEAD_REVIEWS
            )
            rows.append(row)
            save_tables(rows, lead_path, rest_path)

            mark = "ЛИД  " if row["is_lead"] else "     "
            contact = row["phone"] or "без телефона"
            # движок печатаем рядом с типом — сразу видно, почему так решено
            engine = f" ({row['site_engine']})" if row["site_engine"] else ""
            print(f"  [{index}/{len(links)}] {mark} {row['name']} | "
                  f"{contact} | {row['site_type']}{engine}")

    except KeyboardInterrupt:
        print("\nОстановлено вручную — собранное уже сохранено.")
    finally:
        driver.quit()

    if not rows:
        print("Ничего не собрано.")
        return

    df = save_tables(rows, lead_path, rest_path)
    leads = int(df["is_lead"].sum())
    breakdown = df["site_type"].value_counts().to_dict()

    print(f"\nГотово. Открыто карточек: {len(df)} | горячих лидов: {leads}")
    print("Разбивка по сайту: " + ", ".join(
        f"{key}={value}" for key, value in breakdown.items()
    ))
    if leads:
        print(f"Лиды:     {lead_path}")
    if leads < len(df):
        print(f"Остальные: {rest_path}")


if __name__ == "__main__":
    main()
