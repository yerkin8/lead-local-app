from __future__ import annotations

import ipaddress
import random
import re
import socket
import time
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from . import db
from .driver_registry import register as register_driver, quit_driver
from .domain import classify_site, host_of, is_social, normalized, org_id


class SearchStopped(RuntimeError):
    pass


def make_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1400,900")
    options.add_argument("--lang=ru-RU")
    options.add_experimental_option("prefs", {"profile.managed_default_content_settings.images": 2})
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(45)
    driver.set_script_timeout(20)
    return register_driver(driver)


def captcha(driver) -> bool:
    if "showcaptcha" in driver.current_url.lower() or "captcha" in driver.title.lower():
        return True
    return bool(driver.execute_script("""
      const t=(document.body?.innerText||'').toLowerCase();
      return !!document.querySelector('form[action*="checkcaptcha"],input[name*="captcha"],.SmartCaptcha,.CheckboxCaptcha')
        || t.includes('я не робот') || t.includes('подтвердите, что вы не робот')
        || t.trim()==='limited' || t.includes('too many requests');
    """))


def block_message(driver) -> str:
    body = driver.execute_script("return (document.body?.innerText||'').trim().toLowerCase().slice(0,200)")
    return "Яндекс временно ограничил запросы" if body == "limited" or "too many requests" in body else "Яндекс показал капчу"


def text_of(soup, tag: str, class_name: str) -> str | None:
    node = soup.find(tag, class_=class_name)
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() if node else None


def all_texts(soup, tag: str, class_name: str) -> list[str]:
    return list(dict.fromkeys(x.get_text(" ", strip=True) for x in soup.find_all(tag, class_=class_name) if x.get_text(" ", strip=True)))


def number(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", value.replace("\u00a0", ""))
    return float(match.group().replace(",", ".")) if match else None


def public_host(host: str) -> bool:
    try:
        addresses = socket.getaddrinfo(host, None)
    except OSError:
        return False
    return bool(addresses) and all(ipaddress.ip_address(addr[4][0]).is_global for addr in addresses)


def fetch_site(site: str) -> tuple[str | None, bool]:
    """Read only a small public web page; reject private hosts and redirects."""
    url = site if "://" in site else "https://" + site
    for _ in range(4):
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or not public_host(parsed.hostname):
            return None, True
        try:
            with requests.get(url, timeout=(5, 12), stream=True, allow_redirects=False,
                              headers={"User-Agent": "Mozilla/5.0"}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    url = urljoin(url, response.headers.get("Location", ""))
                    continue
                if response.status_code >= 400:
                    return None, True
                chunks, size = [], 0
                for chunk in response.iter_content(8192):
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= 300_000:
                        break
                return b"".join(chunks).decode(response.encoding or "utf-8", "ignore"), False
        except requests.RequestException:
            return None, True
    return None, True


def clean_social_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def extract_socials(soup) -> list[dict]:
    result, seen = [], set()
    anchors = soup.select('.business-contacts-view__social-button a[href^="http"]')
    if not anchors:
        anchors = soup.select('a[href^="http"]')
    for a in anchors:
        if a.find_parent("footer"):
            continue
        url = a.get("href", "")
        host = host_of(url)
        if not is_social(url):
            continue
        if any(x in url for x in ("/share?", "?share=", "intent/tweet")):
            continue
        clean = clean_social_url(url)
        if clean in seen:
            continue
        seen.add(clean)
        result.append({"label": host, "url": clean})
    return result[:12]


def parse_org(driver, url: str) -> dict:
    driver.get(url)
    WebDriverWait(driver, 20).until(lambda d: d.find_elements(By.CSS_SELECTOR, "h1.orgpage-header-view__header") or captcha(d))
    if captcha(driver):
        raise SearchStopped(block_message(driver))
    return parse_org_html(url, driver.page_source)


def parse_org_html(url: str, page: str) -> dict:
    soup = BeautifulSoup(page, "html.parser")
    socials = extract_socials(soup)
    site = text_of(soup, "span", "business-urls-view__text")
    site_node = soup.find("span", class_="business-urls-view__text")
    anchor = site_node.find_parent("a") if site_node else None
    if anchor and anchor.get("href", "").startswith("http"):
        site = anchor["href"]
    if site and is_social(site):
        clean = clean_social_url(site)
        if clean not in {item["url"] for item in socials}:
            socials.append({"label": host_of(site), "url": clean})
        site = None
    site_type, engine = classify_site(site)
    if site_type != "weak_site" and site:
        html, failed = fetch_site(site)
        site_type, engine = classify_site(site, html, failed)
    categories = all_texts(soup, "a", "orgpage-categories-info-view__link")
    return {
        "id": org_id(url), "name": text_of(soup, "h1", "orgpage-header-view__header") or "Без названия",
        "category": ", ".join(categories[:3]) if categories else None,
        "phones": all_texts(soup, "div", "orgpage-phones-view__phone-number"),
        "socials": socials, "website": site, "site_type": site_type,
        "site_engine": engine, "rating": number(text_of(soup, "span", "business-rating-badge-view__rating-text")),
        "reviews": int(number(text_of(soup, "div", "business-header-rating-view__text")) or 0),
        "address": text_of(soup, "a", "orgpage-header-view__address"), "map_url": url,
    }


def visible_links(driver) -> list[str]:
    return driver.execute_script("""
      const first=document.querySelector('.search-business-snippet-view__content');
      const root=first?.closest('.search-list-view__list,.search-list-view,.scroll')||document;
      return Array.from(root.querySelectorAll('a[href*="/maps/org/"]')).map(a=>a.href);
    """) or []


def scroll_results(driver) -> None:
    driver.execute_script("""
      const nodes=document.querySelectorAll('.search-business-snippet-view__content');
      const last=nodes[nodes.length-1]; if (!last) return;
      const sc=last.closest('.scroll')||last.closest('.search-list-view');
      if(sc){sc.scrollTop=sc.scrollHeight;sc.dispatchEvent(new Event('scroll',{bubbles:true}));}
      last.scrollIntoView({block:'end'});
    """)


def localized_url(url: str) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["lang"] = "ru_RU"
    return urlunparse(parsed._replace(query=urlencode(query)))


def request_page(session: requests.Session, url: str) -> tuple[str, str]:
    try:
        response = session.get(localized_url(url), timeout=(5, 25))
    except requests.RequestException as exc:
        raise SearchStopped(f"Не удалось загрузить Яндекс.Карты: {type(exc).__name__}") from None
    body = response.text
    plain = BeautifulSoup(body[:20000], "html.parser").get_text(" ", strip=True).casefold()
    if response.status_code in {403, 429} or plain == "limited" or "showcaptcha" in response.url.lower():
        raise SearchStopped("Яндекс временно ограничил запросы или показал капчу")
    if response.status_code >= 400:
        raise SearchStopped(f"Яндекс.Карты вернули HTTP {response.status_code}")
    return response.url, body


def http_search_links(page_url: str, soup: BeautifulSoup) -> tuple[list[str], list[str]]:
    businesses, pages = [], []
    for snippet in soup.select(".search-business-snippet-view__content"):
        for anchor in snippet.select('a[href*="/maps/org/"]'):
            url = urljoin(page_url, anchor.get("href", ""))
            match = re.search(r"/maps/org/[^/]+/\d+/", url)
            if match:
                businesses.append("https://yandex.com" + match.group())
                break
    for anchor in soup.select('a[href*="page="]'):
        url = urljoin(page_url, anchor.get("href", ""))
        parsed = urlparse(url)
        if parsed.hostname in {"yandex.ru", "yandex.com"} and parsed.path.startswith("/maps/") and "page" in dict(parse_qsl(parsed.query)):
            pages.append(url)
    return list(dict.fromkeys(businesses)), list(dict.fromkeys(pages))


def explicit_no_results(soup: BeautifulSoup) -> bool:
    """Distinguish an empty Yandex search from an unrendered search page."""
    text = soup.get_text(" ", strip=True).casefold()
    return "ничего не найдено" in text and "попробуйте изменить запрос" in text


def run_search_http(job: dict) -> dict:
    params = job["params"]
    target = params["count"]
    max_scanned = min(1000, max(80, target * 10))
    deadline = time.monotonic() + 3600
    known = db.known_ids()
    search_key = db.search_key(params)
    checked = db.checked_ids(search_key)
    seen_orgs, seen_pages = set(), set()
    matched = scanned = skipped = skipped_checked = errors = 0
    empty_queries = 0
    queries = list(dict.fromkeys((
        f"{params['country']} {params['city']} {params['niche']}",
        f"{params['niche']} {params['city']}",
    )))
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "ru-RU,ru;q=0.9"})
    for query in queries:
        pending = [f"https://yandex.com/maps/?text={quote(query)}"]
        while pending and scanned < max_scanned and matched < target and time.monotonic() < deadline and len(seen_pages) < 200:
            page_url = pending.pop(0)
            if page_url in seen_pages:
                continue
            seen_pages.add(page_url)
            try:
                actual_url, body = request_page(session, page_url)
            except SearchStopped as exc:
                return {"matched": matched, "scanned": scanned, "skipped_existing": skipped,
                        "skipped_checked": skipped_checked, "errors": errors, "reason": str(exc)}
            soup = BeautifulSoup(body, "html.parser")
            links, next_pages = http_search_links(actual_url, soup)
            if not links and explicit_no_results(soup):
                empty_queries += 1
                continue
            if not links and len(seen_pages) == 1:
                return run_search_browser(job)
            for next_page in next_pages:
                if next_page not in seen_pages and next_page not in pending:
                    pending.append(next_page)
            for url in links:
                identity = org_id(url)
                if not identity or identity in seen_orgs:
                    continue
                seen_orgs.add(identity)
                if identity in known:
                    skipped += 1
                    continue
                if identity in checked:
                    skipped_checked += 1
                    continue
                if scanned >= max_scanned or matched >= target or time.monotonic() >= deadline:
                    break
                scanned += 1
                try:
                    _, card_html = request_page(session, url)
                    row = parse_org_html(url, card_html)
                    if row["id"] and row["site_type"] in params["types"]:
                        address = normalized(row.get("address") or "")
                        if not address or normalized(params["city"]) in address:
                            if db.upsert_business(row, (params["niche"], params["country"], params["city"]), job["id"]):
                                matched += 1
                                known.add(row["id"])
                            else:
                                skipped += 1
                    if row["id"]:
                        db.mark_checked(search_key, row["id"])
                        checked.add(row["id"])
                except SearchStopped as exc:
                    return {"matched": matched, "scanned": scanned, "skipped_existing": skipped,
                            "skipped_checked": skipped_checked, "errors": errors, "reason": str(exc)}
                except Exception as exc:
                    errors += 1
                    db.update_job(job["id"], progress={"matched": matched, "scanned": scanned,
                                                        "skipped_existing": skipped, "skipped_checked": skipped_checked,
                                                        "errors": errors, "last_error": type(exc).__name__})
                db.update_job(job["id"], progress={"matched": matched, "scanned": scanned,
                                                    "skipped_existing": skipped, "skipped_checked": skipped_checked,
                                                    "errors": errors, "target": target})
                time.sleep(random.uniform(0.25, 0.5))
        if matched >= target or scanned >= max_scanned or time.monotonic() >= deadline:
            break
    if matched >= target:
        reason = "Цель достигнута"
    elif scanned >= max_scanned:
        reason = "Достигнут лимит просмотренных карточек"
    elif time.monotonic() >= deadline:
        reason = "Достигнут лимит времени"
    elif len(seen_pages) >= 200:
        reason = "Достигнут лимит страниц выдачи"
    elif scanned == 0 and empty_queries >= len(queries):
        reason = "Яндекс не нашёл организаций по запросу. Проверьте написание города и ниши"
    else:
        reason = "По этим запросам Яндекс больше не показал новых карточек"
    return {"matched": matched, "scanned": scanned, "skipped_existing": skipped,
            "skipped_checked": skipped_checked, "errors": errors, "reason": reason}


def run_search(job: dict) -> dict:
    return run_search_http(job)


def run_search_browser(job: dict) -> dict:
    params = job["params"]
    target = params["count"]
    max_scanned = min(1000, max(80, target * 10))
    deadline = time.monotonic() + 3600
    known = db.known_ids()
    search_key = db.search_key(params)
    checked = db.checked_ids(search_key)
    seen: set[str] = set()
    matched = scanned = skipped = skipped_checked = errors = 0
    stale = 0
    scrolls = 0
    reason = "Выдача исчерпана"
    driver = make_driver()
    try:
        query = quote(f"{params['country']} {params['city']} {params['niche']}")
        driver.get(f"https://yandex.ru/maps/?text={query}")
        WebDriverWait(driver, 20).until(lambda d: d.find_elements(By.CSS_SELECTOR, ".search-business-snippet-view__content") or captcha(d))
        if captcha(driver):
            raise SearchStopped(block_message(driver))
        search_tab = driver.current_window_handle
        driver.switch_to.new_window("tab")
        card_tab = driver.current_window_handle
        driver.switch_to.window(search_tab)
        while scanned < max_scanned and matched < target and time.monotonic() < deadline and scrolls < 200:
            links = visible_links(driver)
            new_links = []
            discovered = 0
            for url in links:
                match = re.search(r"https://yandex\.ru/maps/org/[^/]+/\d+/", url)
                canonical = match.group() if match else None
                identity = org_id(canonical or "")
                if identity and identity not in seen:
                    seen.add(identity)
                    discovered += 1
                    if identity in known:
                        skipped += 1
                        continue
                    if identity in checked:
                        skipped_checked += 1
                        continue
                    new_links.append(canonical)
            if discovered:
                stale = 0
            else:
                stale += 1
            for url in new_links:
                if scanned >= max_scanned or matched >= target or time.monotonic() >= deadline:
                    break
                scanned += 1
                driver.switch_to.window(card_tab)
                try:
                    row = parse_org(driver, url)
                    if row["id"] and row["site_type"] in params["types"]:
                        # Search is geographically scoped by Yandex. Reject an explicit different city.
                        address = normalized(row.get("address") or "")
                        if not address or normalized(params["city"]) in address:
                            if db.upsert_business(row, (params["niche"], params["country"], params["city"]), job["id"]):
                                matched += 1
                                known.add(row["id"])
                            else:
                                skipped += 1
                    if row["id"]:
                        db.mark_checked(search_key, row["id"])
                        checked.add(row["id"])
                except SearchStopped:
                    raise
                except Exception as exc:
                    errors += 1
                    db.update_job(job["id"], progress={"matched": matched, "scanned": scanned,
                                                            "skipped_existing": skipped, "skipped_checked": skipped_checked,
                                                            "errors": errors, "last_error": type(exc).__name__})
                finally:
                    driver.switch_to.window(search_tab)
                db.update_job(job["id"], progress={"matched": matched, "scanned": scanned,
                                                        "skipped_existing": skipped, "skipped_checked": skipped_checked,
                                                        "errors": errors, "target": target})
                time.sleep(random.uniform(1.5, 3.0))
            if matched >= target:
                reason = "Цель достигнута"
                break
            if captcha(driver):
                raise SearchStopped(block_message(driver))
            if stale >= 12:
                reason = "Новых карточек больше нет"
                break
            scroll_results(driver)
            scrolls += 1
            time.sleep(1)
        if scanned >= max_scanned:
            reason = "Достигнут лимит просмотренных карточек"
        elif time.monotonic() >= deadline:
            reason = "Достигнут лимит времени"
        elif scrolls >= 200:
            reason = "Достигнут лимит обхода выдачи"
        return {"matched": matched, "scanned": scanned, "skipped_existing": skipped,
                "skipped_checked": skipped_checked, "errors": errors, "reason": reason}
    except SearchStopped as exc:
        return {"matched": matched, "scanned": scanned, "skipped_existing": skipped,
                "skipped_checked": skipped_checked, "errors": errors, "reason": str(exc)}
    finally:
        quit_driver(driver)
