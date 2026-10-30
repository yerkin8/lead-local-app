from __future__ import annotations

import ipaddress
import json
import logging
import random
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
import uuid
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from . import db
from .config import DATA_DIR
from .driver_registry import register as register_driver, quit_driver
from .domain import classify_site, host_of, is_social, normalized, org_id
from .yandex_urls import normalize_yandex_url


LOGGER = logging.getLogger("LeadEqualMoney.parser")
SELECTORS_PATH = Path(__file__).with_name("selectors.json")
with SELECTORS_PATH.open("r", encoding="utf-8") as _selectors_file:
    SELECTORS = json.load(_selectors_file)


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
      return arguments[0].some(s=>document.querySelector(s))
        || t.includes('я не робот') || t.includes('подтвердите, что вы не робот')
        || t.trim()==='limited' || t.includes('too many requests');
    """, SELECTORS["search"]["captcha"]))


def block_message(driver) -> str:
    body = driver.execute_script("return (document.body?.innerText||'').trim().toLowerCase().slice(0,200)")
    return "Яндекс временно ограничил запросы" if body == "limited" or "too many requests" in body else "Яндекс показал капчу"


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
    anchors = soup.select(SELECTORS["organization"]["social_links"])
    if not anchors:
        anchors = soup.select(SELECTORS["organization"]["fallback_social_links"])
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


def _jsonld_objects(value):
    if isinstance(value, list):
        for item in value:
            yield from _jsonld_objects(item)
    elif isinstance(value, dict):
        yield value
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in value:
                yield from _jsonld_objects(value[key])


def jsonld_records(soup) -> list[dict]:
    found = []
    for node in soup.select(SELECTORS["structured_data"]["json_ld"]):
        try:
            parsed = json.loads(node.string or node.get_text())
        except (TypeError, ValueError):
            continue
        found.extend(item for item in _jsonld_objects(parsed) if isinstance(item, dict))
    return found


def _first_dict(value):
    if isinstance(value, list):
        return next((item for item in value if isinstance(item, dict)), {})
    return value if isinstance(value, dict) else {}


def _jsonld_details(records: list[dict], soup, categories: list[str]) -> tuple[dict, list, list, list, list]:
    description = None
    latitude = longitude = None
    photos: set[str] = set()
    hours, features, reviews, links = [], [], [], []
    for record in records:
        description = description or record.get("description")
        if not categories and record.get("category"):
            raw_categories = record["category"]
            categories.extend(str(value).strip() for value in (raw_categories if isinstance(raw_categories, list) else [raw_categories])
                             if str(value).strip())
        geo = _first_dict(record.get("geo"))
        latitude = latitude if latitude is not None else number(str(geo.get("latitude") or ""))
        longitude = longitude if longitude is not None else number(str(geo.get("longitude") or ""))
        images = record.get("image") or record.get("photo") or []
        if isinstance(images, (str, dict)):
            images = [images]
        for image in images if isinstance(images, list) else []:
            image_url = image.get("url") if isinstance(image, dict) else image
            if isinstance(image_url, str) and image_url.startswith(("https://", "http://")):
                photos.add(image_url)
        specifications = record.get("openingHoursSpecification") or []
        if isinstance(specifications, dict):
            specifications = [specifications]
        for item in specifications if isinstance(specifications, list) else []:
            if not isinstance(item, dict):
                continue
            days = item.get("dayOfWeek") or []
            if isinstance(days, str):
                days = [days]
            for day in days if isinstance(days, list) else []:
                day_name = str(day).rsplit("/", 1)[-1]
                hours.append({"weekday": day_name, "opens": str(item.get("opens") or ""),
                              "closes": str(item.get("closes") or ""),
                              "closed": bool(item.get("closed")), "raw": ""})
        raw_hours = record.get("openingHours") or []
        if isinstance(raw_hours, str):
            raw_hours = [raw_hours]
        day_names = {"Mo": "Monday", "Tu": "Tuesday", "We": "Wednesday", "Th": "Thursday",
                     "Fr": "Friday", "Sa": "Saturday", "Su": "Sunday"}
        for raw in raw_hours if isinstance(raw_hours, list) else []:
            match = re.fullmatch(r"([A-Za-z]{2,9})(?:-([A-Za-z]{2,9}))?\s+([0-2]?\d:\d{2})-([0-2]?\d:\d{2})", str(raw).strip())
            if not match:
                continue
            aliases = {**day_names, **{value: value for value in day_names.values()}}
            start = aliases.get(match.group(1).title())
            end = aliases.get((match.group(2) or match.group(1)).title())
            if start and end:
                ordered = list(day_names.values())
                first, last = ordered.index(start), ordered.index(end)
                days = ordered[first:last + 1] if first <= last else ordered[first:] + ordered[:last + 1]
                hours.extend({"weekday": day, "opens": match.group(3), "closes": match.group(4),
                              "closed": False, "raw": str(raw)[:200]} for day in days)
        amenities = record.get("amenityFeature") or []
        properties = record.get("additionalProperty") or []
        amenities = amenities if isinstance(amenities, list) else [amenities]
        properties = properties if isinstance(properties, list) else [properties]
        for item in amenities + properties:
            if not isinstance(item, dict):
                continue
            features.append({"name": str(item.get("name") or "").strip(),
                             "value": str(item.get("value") or "").strip()})
        same_as = record.get("sameAs") or []
        if isinstance(same_as, str):
            same_as = [same_as]
        for url in same_as if isinstance(same_as, list) else []:
            if isinstance(url, str):
                links.append({"url": url, "label": host_of(url) or "", "type": "other"})
        items = record.get("review") or []
        if isinstance(items, dict):
            items = [items]
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            rating = _first_dict(item.get("reviewRating")).get("ratingValue")
            reviews.append({"text": str(item.get("reviewBody") or "").strip(),
                            "rating": number(str(rating)) if rating is not None else None,
                            "date": str(item.get("datePublished") or "").strip(),
                            "company_response": str(item.get("companyResponse") or "").strip()})
    if description is None:
        meta = soup.select_one(SELECTORS["structured_data"]["description_meta"])
        description = meta.get("content") if meta else None
    if latitude is None or longitude is None:
        geo_meta = soup.select_one(SELECTORS["structured_data"]["geo_meta"])
        match = re.search(r"(-?\d+(?:\.\d+)?)\s*;\s*(-?\d+(?:\.\d+)?)", geo_meta.get("content", "")) if geo_meta else None
        if match:
            latitude = latitude if latitude is not None else float(match.group(1))
            longitude = longitude if longitude is not None else float(match.group(2))
    return ({"description": str(description).strip() if description else None,
             "latitude": latitude, "longitude": longitude,
             "photo_count": len(photos) if photos else None,
             "categories": categories, "details_complete": bool(description or latitude is not None or
                 longitude is not None or photos or categories or reviews or links or hours or features)},
            reviews, links, hours, features)


def _classify_link(url: str, website: str | None = None) -> str:
    if website and url.rstrip("/") == website.rstrip("/"):
        return "website"
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host in {"t.me", "telegram.me", "wa.me", "whatsapp.com", "api.whatsapp.com", "viber.com"}:
        return "messenger"
    if is_social(url):
        return "social"
    return "other"


def _profile_links(site: str | None, socials: list[dict], structured_links: list[dict]) -> list[dict]:
    items = []
    if site and site.startswith(("https://", "http://")):
        items.append({"url": site, "label": host_of(site) or "Сайт", "type": "website"})
    items.extend({**item, "type": "social"} for item in socials)
    items.extend(structured_links)
    unique = {}
    for item in items:
        url = str(item.get("url") or "").strip()
        if not url.startswith(("https://", "http://")):
            continue
        item_type = _classify_link(url, site)
        unique.setdefault(url, {"url": url, "label": str(item.get("label") or host_of(url) or "")[:200],
                                "type": item_type if item_type in {"website", "social", "messenger", "other"} else "other"})
    return list(unique.values())


def _contact_links(soup) -> list[dict]:
    result = []
    for anchor in soup.select(SELECTORS["organization"]["contact_links"]):
        url = str(anchor.get("href") or "").strip()
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        on_yandex = host == "yandex.ru" or host.endswith(".yandex.ru")
        if parsed.scheme in {"https", "http"} and parsed.hostname and not on_yandex:
            result.append({"url": url, "label": anchor.get_text(" ", strip=True) or host_of(url) or "",
                           "type": _classify_link(url)})
    return result


def extract_review_items(page: str, limit: int = 100) -> list[dict]:
    cap = min(100, max(0, int(limit)))
    if cap == 0:
        return []
    soup = BeautifulSoup(page, "html.parser")
    _, embedded, _, _, _ = _jsonld_details(jsonld_records(soup), soup, [])
    result = list(embedded)
    card_selector = SELECTORS["reviews"]["cards"]
    for card in soup.select(card_selector):
        text_node = card.select_one(SELECTORS["reviews"]["text"])
        text = re.sub(r"\s+", " ", text_node.get_text(" ", strip=True)).strip() if text_node else ""
        if not text:
            continue
        rating_node = card.select_one(SELECTORS["reviews"]["rating"])
        rating_value = (rating_node.get("content") or rating_node.get("aria-label") or rating_node.get_text(" ", strip=True)) if rating_node else ""
        date_node = card.select_one(SELECTORS["reviews"]["date"])
        date_value = (date_node.get("datetime") or date_node.get_text(" ", strip=True)) if date_node else ""
        response_node = card.select_one(SELECTORS["reviews"]["company_response"])
        result.append({"text": text, "rating": number(str(rating_value)),
                       "date": str(date_value).strip(),
                       "company_response": re.sub(r"\s+", " ", response_node.get_text(" ", strip=True)).strip()
                           if response_node else ""})
    unique, signatures = [], set()
    for item in result:
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if not text:
            continue
        signature = (text.casefold(), str(item.get("date") or "").strip(), item.get("rating"))
        if signature in signatures:
            continue
        signatures.add(signature)
        unique.append({"text": text[:10000], "rating": item.get("rating"),
                       "date": str(item.get("date") or "")[:80],
                       "company_response": str(item.get("company_response") or "")[:5000]})
        if len(unique) >= cap:
            break
    return unique


def parse_org(driver, url: str) -> dict:
    driver.get(url)
    WebDriverWait(driver, 20).until(lambda d: d.find_elements(
        By.CSS_SELECTOR, SELECTORS["organization"]["name"]) or captcha(d))
    if captcha(driver):
        raise SearchStopped(block_message(driver))
    return parse_org_html(url, driver.page_source)


def _selector_text(soup, selector: str) -> str | None:
    node = soup.select_one(selector)
    return re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip() if node else None


def _selector_texts(soup, selector: str) -> list[str]:
    return list(dict.fromkeys(re.sub(r"\s+", " ", node.get_text(" ", strip=True)).strip()
                               for node in soup.select(selector) if node.get_text(" ", strip=True)))


def parse_org_html(url: str, page: str, review_limit: int = 100) -> dict:
    soup = BeautifulSoup(page, "html.parser")
    selectors = SELECTORS["organization"]
    socials = extract_socials(soup)
    site = _selector_text(soup, selectors["website_text"])
    site_node = soup.select_one(selectors["website_text"])
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
    categories = _selector_texts(soup, selectors["categories"])
    records = jsonld_records(soup)
    details, embedded_reviews, structured_links, hours, features = _jsonld_details(records, soup, categories)
    review_links = soup.select(selectors["review_link"])
    review_url = urljoin(url, review_links[0].get("href", "")) if review_links else url.rstrip("/") + "/reviews/"
    gallery_links = soup.select(selectors["gallery_link"])
    gallery_url = urljoin(url, gallery_links[0].get("href", "")) if gallery_links else url.rstrip("/") + "/gallery/"
    links = _profile_links(site, socials, structured_links + _contact_links(soup))
    photo_count = max(0, int(details["photo_count"])) if details.get("photo_count") is not None else None
    return {
        "id": org_id(url), "name": _selector_text(soup, selectors["name"]) or "Без названия",
        "category": ", ".join(categories[:3]) if categories else None,
        "phones": _selector_texts(soup, selectors["phones"]),
        "socials": socials, "website": site, "site_type": site_type,
        "site_engine": engine, "rating": number(_selector_text(soup, selectors["rating"])),
        "reviews": int(number(_selector_text(soup, selectors["review_count"])) or 0),
        "address": _selector_text(soup, selectors["address"]), "map_url": normalize_yandex_url(url),
        "details": {**details, "photo_count": photo_count},
        "review_items": extract_review_items(page, review_limit),
        "links": links, "hours": hours, "features": features,
        "review_url": review_url, "gallery_url": gallery_url,
    }


def _log_parser_event(level: int, event: str, **fields) -> None:
    safe_fields = {key: value for key, value in fields.items()
                   if key not in {"phone", "phones", "review", "review_text", "html"}}
    payload = json.dumps({"event": event, **safe_fields}, ensure_ascii=False, sort_keys=True, default=str)
    LOGGER.log(level, payload, extra={"event": event, **safe_fields})


def save_failed_card_html(url: str, html: str | None, error: Exception) -> str | None:
    """Save bounded diagnostic HTML only when explicitly enabled in desktop settings."""
    if not html or not db.desktop_diagnostics_enabled():
        return None
    encoded = html.encode("utf-8", "replace")[:1_000_000]
    folder = DATA_DIR / "debug"
    folder.mkdir(parents=True, exist_ok=True)
    identity = org_id(url) or "unknown"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    target = folder / f"card-{identity}-{stamp}-{uuid.uuid4().hex[:8]}.html"
    target.write_bytes(encoded)
    _log_parser_event(logging.WARNING, "parser_debug_html_saved", business_id=identity,
                      error_type=type(error).__name__, bytes_saved=len(encoded))
    return str(target)


def collect_reviews_http(session: requests.Session, review_url: str, business_id: str,
                         review_limit: int = 20) -> int:
    cap = min(100, max(0, int(review_limit)))
    if not cap or db.business_review_count(business_id) >= cap:
        db.set_review_collection_state(business_id, cap, True)
        return 0
    pending, seen_pages = [review_url], set()
    pages = added = stale = 0
    request_failed = False
    while pending and pages < 40 and db.business_review_count(business_id) < cap:
        current = pending.pop(0)
        parsed = urlparse(current)
        if (parsed.hostname != "yandex.ru" or "/reviews/" not in parsed.path
                or org_id(current) != business_id or current in seen_pages):
            continue
        seen_pages.add(current)
        pages += 1
        try:
            time.sleep(random.uniform(0.5, 1.0))
            actual, page = request_page(session, current)
            items = extract_review_items(page, cap)
            new_count = db.append_business_reviews(business_id, items, cap)
            added += new_count
            stale = 0 if new_count else stale + 1
            if db.business_review_count(business_id) >= cap or stale >= 3:
                break
            soup = BeautifulSoup(page, "html.parser")
            for anchor in soup.select(SELECTORS["reviews"]["pagination_links"]):
                next_url = urljoin(actual, anchor.get("href", ""))
                candidate = urlparse(next_url)
                if (candidate.hostname == "yandex.ru" and "/reviews/" in candidate.path
                        and org_id(next_url) == business_id and next_url not in seen_pages
                        and next_url not in pending):
                    pending.append(next_url)
        except SearchStopped:
            request_failed = True
            raise
        except Exception as exc:
            request_failed = True
            _log_parser_event(logging.WARNING, "review_page_fetch_failed", business_id=business_id,
                              error_type=type(exc).__name__, page_number=pages)
            break
        time.sleep(random.uniform(0.5, 1.0))
    complete = not request_failed and (db.business_review_count(business_id) >= cap or not pending or stale >= 3)
    db.set_review_collection_state(business_id, cap, complete)
    _log_parser_event(logging.INFO, "reviews_collected", business_id=business_id,
                      added=added, total=db.business_review_count(business_id), pages=pages)
    return added


def collect_gallery_photo_count_http(session: requests.Session, gallery_url: str) -> int | None:
    try:
        time.sleep(random.uniform(0.5, 1.0))
        _, page = request_page(session, gallery_url)
        soup = BeautifulSoup(page, "html.parser")
        urls = set()
        for node in soup.select(SELECTORS["gallery"]["photos"]):
            url = node.get("src") or node.get("data-src") or ""
            if url.startswith(("https://", "http://")) and "/get-vh/" not in url:
                urls.add(url)
        return len(urls) if urls else None
    except SearchStopped:
        raise
    except Exception as exc:
        _log_parser_event(logging.INFO, "gallery_photo_count_unavailable", error_type=type(exc).__name__)
        return None


def collect_reviews_browser(driver, review_url: str, business_id: str,
                            review_limit: int = 20) -> int:
    cap = min(100, max(0, int(review_limit)))
    if not cap or db.business_review_count(business_id) >= cap:
        db.set_review_collection_state(business_id, cap, True)
        return 0
    original = driver.current_url
    added = stale = scrolls = 0
    config = SELECTORS["reviews"]
    try:
        driver.get(review_url)
        WebDriverWait(driver, 12).until(lambda d: d.execute_script(
            "return document.readyState === 'complete'"))
        if captcha(driver):
            raise SearchStopped(block_message(driver))
        time.sleep(0.7)
        while scrolls <= int(config["max_scrolls"]) and db.business_review_count(business_id) < cap:
            items = extract_review_items(driver.page_source, cap)
            new_count = db.append_business_reviews(business_id, items, cap)
            added += new_count
            stale = 0 if new_count else stale + 1
            if db.business_review_count(business_id) >= cap or stale >= int(config["stale_scrolls"]):
                break
            movement = driver.execute_script("""
              const selectors=arguments[0].split(',').map(x=>x.trim());
              const c=selectors.map(x=>document.querySelector(x)).find(Boolean);
              if(!c)return false;
              const before=c.scrollTop;
              c.scrollTop=Math.min(c.scrollTop+Math.max(400,Math.floor(c.clientHeight*.8)),c.scrollHeight);
              c.dispatchEvent(new Event('scroll',{bubbles:true}));
              return c.scrollTop!==before;
            """, config["scroll_container"])
            if not movement:
                stale += 1
                if stale >= int(config["stale_scrolls"]):
                    break
            scrolls += 1
            time.sleep(random.uniform(0.6, 1.0))
    except SearchStopped:
        raise
    except Exception as exc:
        _log_parser_event(logging.WARNING, "review_scroll_failed", business_id=business_id,
                          error_type=type(exc).__name__, scrolls=scrolls)
    finally:
        if original and driver.current_url != original:
            try:
                driver.get(original)
                WebDriverWait(driver, 10).until(lambda d: d.execute_script(
                    "return document.readyState === 'complete'"))
            except Exception as exc:
                _log_parser_event(logging.WARNING, "review_return_to_card_failed", business_id=business_id,
                                  error_type=type(exc).__name__)
    complete = db.business_review_count(business_id) >= cap or stale >= int(config["stale_scrolls"])
    db.set_review_collection_state(business_id, cap, complete)
    _log_parser_event(logging.INFO, "reviews_collected", business_id=business_id,
                      added=added, total=db.business_review_count(business_id), scrolls=scrolls)
    return added


def collect_gallery_photo_count(driver, gallery_url: str, profile_url: str) -> int | None:
    config = SELECTORS["gallery"]
    photos: set[str] = set()
    stale = 0
    try:
        driver.get(gallery_url)
        WebDriverWait(driver, 10).until(lambda d: d.execute_script(
            "return document.readyState === 'complete'"))
        if captcha(driver):
            raise SearchStopped(block_message(driver))
        for _ in range(int(config["max_scrolls"])):
            urls = driver.execute_script("""
              return Array.from(document.querySelectorAll(arguments[0]))
                .map(x=>x.currentSrc||x.src||'').filter(x=>x.startsWith('http')&&!x.includes('/get-vh/'));
            """, config["photos"]) or []
            old_count = len(photos)
            photos.update(urls)
            stale = stale + 1 if len(photos) == old_count else 0
            if stale >= int(config["stale_scrolls"]):
                break
            movement = driver.execute_script("""
              const c=document.querySelector(arguments[0]); if(!c)return false;
              const before=c.scrollTop;
              c.scrollTop=Math.min(c.scrollTop+Math.max(400,Math.floor(c.clientHeight*.8)),c.scrollHeight);
              c.dispatchEvent(new Event('scroll',{bubbles:true})); return c.scrollTop!==before;
            """, config["scroll_container"])
            if not movement:
                stale += 1
            if stale >= int(config["stale_scrolls"]):
                break
            time.sleep(0.4)
    except SearchStopped:
        raise
    except Exception as exc:
        _log_parser_event(logging.INFO, "gallery_photo_count_unavailable", error_type=type(exc).__name__)
    finally:
        if profile_url and driver.current_url != profile_url:
            try:
                driver.get(profile_url)
                WebDriverWait(driver, 10).until(lambda d: d.execute_script(
                    "return document.readyState === 'complete'"))
            except Exception as exc:
                _log_parser_event(logging.WARNING, "gallery_return_to_card_failed",
                                  error_type=type(exc).__name__)
    return len(photos) if photos else None


def visible_links(driver) -> list[str]:
    return driver.execute_script("""
      const first=document.querySelector(arguments[0]);
      const root=first?.closest(arguments[1])||document;
      return Array.from(root.querySelectorAll(arguments[2])).map(a=>a.href);
    """, SELECTORS["search"]["snippets"], SELECTORS["search"]["scroll_roots"],
        SELECTORS["search"]["organization_links"]) or []


def scroll_results(driver) -> None:
    driver.execute_script("""
      const nodes=document.querySelectorAll(arguments[0]);
      const last=nodes[nodes.length-1]; if (!last) return;
      const sc=last.closest(arguments[1]);
      if(sc){sc.scrollTop=sc.scrollHeight;sc.dispatchEvent(new Event('scroll',{bubbles:true}));}
      last.scrollIntoView({block:'end'});
    """, SELECTORS["search"]["snippets"], SELECTORS["search"]["scroll_roots"])


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
    for snippet in soup.select(SELECTORS["search"]["snippets"]):
        for anchor in snippet.select(SELECTORS["search"]["organization_links"]):
            url = urljoin(page_url, anchor.get("href", ""))
            match = re.search(r"/maps/org/[^/]+/\d+/", url)
            if match:
                businesses.append("https://yandex.ru" + match.group())
                break
    for anchor in soup.select(SELECTORS["search"]["next_pages"]):
        url = urljoin(page_url, anchor.get("href", ""))
        parsed = urlparse(url)
        if parsed.hostname == "yandex.ru" and parsed.path.startswith("/maps/") and "page" in dict(parse_qsl(parsed.query)):
            pages.append(url)
    return list(dict.fromkeys(businesses)), list(dict.fromkeys(pages))


def explicit_no_results(soup: BeautifulSoup) -> bool:
    """Distinguish an empty Yandex search from an unrendered search page."""
    text = soup.get_text(" ", strip=True).casefold()
    return "ничего не найдено" in text and "попробуйте изменить запрос" in text


def _link_known_desktop_result(identity: str, params: dict, parse_run_id: str | None) -> None:
    """Associate a previously saved card discovered by a desktop parse with its run."""
    if not parse_run_id:
        return
    item = db.business(identity)
    if not item or item.get("site_type") not in params.get("types", []):
        return
    address = normalized(item.get("address") or "")
    city = normalized(params.get("city") or "")
    if not address or city in address:
        db.link_parse_run_business(parse_run_id, identity)


def run_search_http(job: dict) -> dict:
    params = job["params"]
    desktop_details = bool(params.get("_desktop_details"))
    parse_run_id = params.get("_desktop_parse_run_id") if desktop_details else None
    review_limit = min(100, max(0, int(params.get("review_limit", 20)))) if desktop_details else 0
    target = params["count"]
    max_scanned = min(1000, max(80, target * 10))
    deadline = time.monotonic() + 3600
    known = db.known_ids()
    search_key = db.search_key(params)
    checked = db.checked_ids(search_key)
    seen_orgs, seen_pages = set(), set()
    matched = db.job_result_count(job["id"]) if desktop_details else 0
    scanned = skipped = skipped_checked = errors = 0
    empty_queries = 0
    queries = list(dict.fromkeys((
        f"{params['country']} {params['city']} {params['niche']}",
        f"{params['niche']} {params['city']}",
    )))
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "ru-RU,ru;q=0.9"})
    if desktop_details:
        for pending_review in db.incomplete_job_review_items(job["id"], review_limit):
            try:
                _, profile_html = request_page(session, pending_review["map_url"])
                row = parse_org_html(pending_review["map_url"], profile_html, review_limit)
                if row.get("id") != pending_review["id"]:
                    continue
                db.refresh_business(row, store_details=True, review_limit=review_limit)
                if review_limit:
                    collect_reviews_http(session, row["review_url"], row["id"], review_limit)
                _log_parser_event(logging.INFO, "interrupted_review_collection_resumed",
                                  business_id=row["id"], stored=db.business_review_count(row["id"]))
            except SearchStopped as exc:
                return {"matched": matched, "scanned": scanned, "skipped_existing": skipped,
                        "skipped_checked": skipped_checked, "errors": errors, "reason": str(exc)}
            except Exception as exc:
                _log_parser_event(logging.WARNING, "interrupted_review_collection_failed",
                                  business_id=pending_review["id"], error_type=type(exc).__name__)
    for query in queries:
        pending = [f"https://yandex.ru/maps/?text={quote(query)}"]
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
                resume_existing = desktop_details and db.business_needs_review_resume(
                    job["id"], identity, review_limit)
                if identity in known and not resume_existing:
                    _link_known_desktop_result(identity, params, parse_run_id)
                    skipped += 1
                    continue
                if identity in checked and not resume_existing:
                    skipped_checked += 1
                    continue
                if scanned >= max_scanned or matched >= target or time.monotonic() >= deadline:
                    break
                scanned += 1
                card_html = None
                try:
                    _, card_html = request_page(session, url)
                    row = parse_org_html(url, card_html, review_limit)
                    if row["id"] and row["site_type"] in params["types"]:
                        address = normalized(row.get("address") or "")
                        if not address or normalized(params["city"]) in address:
                            resume_reviews = desktop_details and db.business_needs_review_resume(
                                job["id"], row["id"], review_limit)
                            if desktop_details:
                                is_new = db.upsert_business(row, (params["niche"], params["country"], params["city"]),
                                    job["id"], store_details=True, review_limit=review_limit,
                                    parse_run_id=parse_run_id)
                            else:
                                is_new = db.upsert_business(row, (params["niche"], params["country"], params["city"]),
                                    job["id"])
                            if is_new or resume_reviews:
                                if is_new:
                                    matched += 1
                                    known.add(row["id"])
                                if desktop_details and row.get("details", {}).get("photo_count") is None:
                                    photo_count = collect_gallery_photo_count_http(session, row["gallery_url"])
                                    if photo_count is not None:
                                        row["details"]["photo_count"] = photo_count
                                        db.refresh_business(row, store_details=True, review_limit=review_limit)
                                if desktop_details and review_limit:
                                    collect_reviews_http(session, row["review_url"], row["id"], review_limit)
                                _log_parser_event(logging.INFO, "business_details_saved" if desktop_details else "business_saved",
                                    business_id=row["id"], reviews_collected=db.business_review_count(row["id"]) if desktop_details else 0)
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
                    if desktop_details:
                        try:
                            save_failed_card_html(url, card_html, exc)
                        except Exception as debug_error:
                            _log_parser_event(logging.WARNING, "parser_debug_html_failed",
                                business_id=org_id(url), error_type=type(debug_error).__name__)
                    _log_parser_event(logging.WARNING, "organization_parse_failed",
                        business_id=org_id(url), error_type=type(exc).__name__)
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
    desktop_details = bool(params.get("_desktop_details"))
    parse_run_id = params.get("_desktop_parse_run_id") if desktop_details else None
    review_limit = min(100, max(0, int(params.get("review_limit", 20)))) if desktop_details else 0
    target = params["count"]
    max_scanned = min(1000, max(80, target * 10))
    deadline = time.monotonic() + 3600
    known = db.known_ids()
    search_key = db.search_key(params)
    checked = db.checked_ids(search_key)
    seen: set[str] = set()
    matched = db.job_result_count(job["id"]) if desktop_details else 0
    scanned = skipped = skipped_checked = errors = 0
    stale = 0
    scrolls = 0
    reason = "Выдача исчерпана"
    driver = make_driver()
    try:
        query = quote(f"{params['country']} {params['city']} {params['niche']}")
        driver.get(f"https://yandex.ru/maps/?text={query}")
        WebDriverWait(driver, 20).until(lambda d: d.find_elements(
            By.CSS_SELECTOR, SELECTORS["search"]["snippets"]) or captcha(d))
        if captcha(driver):
            raise SearchStopped(block_message(driver))
        search_tab = driver.current_window_handle
        driver.switch_to.new_window("tab")
        card_tab = driver.current_window_handle
        driver.switch_to.window(search_tab)
        if desktop_details:
            for pending_review in db.incomplete_job_review_items(job["id"], review_limit):
                try:
                    row = parse_org(driver, pending_review["map_url"])
                    if row.get("id") != pending_review["id"]:
                        continue
                    photo_count = collect_gallery_photo_count(driver, row["gallery_url"], row["map_url"])
                    if photo_count is not None:
                        row["details"]["photo_count"] = photo_count
                    db.refresh_business(row, store_details=True, review_limit=review_limit)
                    if review_limit:
                        collect_reviews_browser(driver, row["review_url"], row["id"], review_limit)
                    _log_parser_event(logging.INFO, "interrupted_review_collection_resumed",
                                      business_id=row["id"], stored=db.business_review_count(row["id"]))
                except SearchStopped:
                    raise
                except Exception as exc:
                    errors += 1
                    _log_parser_event(logging.WARNING, "interrupted_review_collection_failed",
                                      business_id=pending_review["id"], error_type=type(exc).__name__)
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
                        _link_known_desktop_result(identity, params, parse_run_id)
                        if desktop_details and db.business_needs_review_resume(job["id"], identity, review_limit):
                            new_links.append(canonical)
                            continue
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
                page_html = None
                try:
                    row = parse_org(driver, url)
                    if row["id"] and row["site_type"] in params["types"]:
                        # Search is geographically scoped by Yandex. Reject an explicit different city.
                        address = normalized(row.get("address") or "")
                        if not address or normalized(params["city"]) in address:
                            resume_reviews = desktop_details and db.business_needs_review_resume(
                                job["id"], row["id"], review_limit)
                            if desktop_details:
                                is_new = db.upsert_business(row, (params["niche"], params["country"], params["city"]),
                                    job["id"], store_details=True, review_limit=review_limit,
                                    parse_run_id=parse_run_id)
                            else:
                                is_new = db.upsert_business(row, (params["niche"], params["country"], params["city"]),
                                    job["id"])
                            if is_new or resume_reviews:
                                if is_new:
                                    matched += 1
                                    known.add(row["id"])
                                if desktop_details:
                                    row["review_items"] = row.get("review_items", [])[:review_limit]
                                    if row.get("details", {}).get("photo_count") is None:
                                        photo_count = collect_gallery_photo_count(driver, row["gallery_url"], url)
                                        if photo_count is not None:
                                            row["details"]["photo_count"] = photo_count
                                            db.refresh_business(row, store_details=True, review_limit=review_limit)
                                if desktop_details and review_limit:
                                    collect_reviews_browser(driver, row["review_url"], row["id"], review_limit)
                                _log_parser_event(logging.INFO, "business_details_saved" if desktop_details else "business_saved",
                                    business_id=row["id"], reviews_collected=db.business_review_count(row["id"]) if desktop_details else 0)
                            else:
                                skipped += 1
                    if row["id"]:
                        db.mark_checked(search_key, row["id"])
                        checked.add(row["id"])
                except SearchStopped:
                    raise
                except Exception as exc:
                    errors += 1
                    if desktop_details:
                        try:
                            page_html = driver.page_source
                            save_failed_card_html(url, page_html, exc)
                        except Exception as debug_error:
                            _log_parser_event(logging.WARNING, "parser_debug_html_failed",
                                business_id=org_id(url), error_type=type(debug_error).__name__)
                    _log_parser_event(logging.WARNING, "organization_parse_failed",
                        business_id=org_id(url), error_type=type(exc).__name__)
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
