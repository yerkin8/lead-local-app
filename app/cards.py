"""Card jobs (kind ``cards``): «Добавить лида» by a Yandex Maps link and «Дособрать номера» for leads
that were saved with one phone or without a mobile. Same HTTP requests, pauses and «Остановить» as the
search; a captcha ends the job as «Яндекс ограничил»."""

from __future__ import annotations

import logging
import random
import re
from urllib.parse import parse_qsl, urlparse

import requests

from . import cancel, db
from .domain import org_id
from .lead_parser import (CARD_PAUSE, PageFailed, SearchBlocked, SearchStopped, apply_site_check,
                          collect_reviews_http, fetch_site, parse_org_html, request_page, site_needs_check)
from .phone_links import build_telegram_phone_links, merge_phones

log = logging.getLogger("LeadEqualMoney.parser")
YANDEX_HOSTS = ("yandex.ru", "yandex.com", "yandex.kz", "yandex.by", "yandex.uz", "ya.ru")
ID_PATTERNS = (re.compile(r"/maps/org/(?:[^/]+/)?(\d{5,})(?:/|$)"), re.compile(r"/profile/(\d{5,})(?:/|$)"))


def _yandex(host: str) -> bool:
    host = (host or "").casefold().rstrip(".")
    return any(host == item or host.endswith("." + item) for item in YANDEX_HOSTS)


def org_id_from_url(url: str) -> str | None:
    """Organization id from any Yandex Maps link form: /maps/org/<name>/<id>/, /maps/org/<id>/,
    /profile/<id>, ?oid=<id> (links with ?ll=… and yandex.com included)."""
    parsed = urlparse(url if "://" in url else "https://" + url)
    if not _yandex(parsed.hostname or ""):
        return None
    for pattern in ID_PATTERNS:
        match = pattern.search(parsed.path)
        if match:
            return match.group(1)
    query = dict(parse_qsl(parsed.query))
    oid = query.get("oid") or query.get("orgId") or ""
    return oid if oid.isdigit() else None


def is_short_link(url: str) -> bool:
    parsed = urlparse(url if "://" in url else "https://" + url)
    return _yandex(parsed.hostname or "") and bool(re.match(r"^/maps/-/", parsed.path))


def resolve_link(session: requests.Session, url: str) -> str | None:
    """Organization id of a link; a short ``yandex.ru/maps/-/…`` link is opened to follow its redirect."""
    url = str(url or "").strip()
    found = org_id_from_url(url)
    if found or not is_short_link(url):
        return found
    try:
        response = session.get(url if "://" in url else "https://" + url, timeout=(5, 20), allow_redirects=True)
    except requests.RequestException as exc:
        raise PageFailed(f"Не удалось открыть короткую ссылку: {type(exc).__name__}") from None
    for step in [*response.history, response]:
        found = org_id_from_url(step.headers.get("Location") or "") or org_id_from_url(step.url)
        if found:
            return found
    return None


def card_url(identity: str) -> str:
    return f"https://yandex.ru/maps/org/{identity}/"


def fetch_card(session: requests.Session, identity: str, *, check_site: bool = True) -> dict:
    actual, page = request_page(session, card_url(identity))
    row = parse_org_html(actual if org_id(actual) == identity else card_url(identity), page, 0, check_site=False)
    if not row.get("id"):
        row["id"] = identity
    if row["id"] != identity:
        raise PageFailed("Яндекс вернул другую организацию")
    if check_site and site_needs_check(row):
        try:
            html, failed = fetch_site(row["website"])
        except Exception:
            html, failed = None, True
        apply_site_check(row, html, failed)
    return row


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0", "Accept-Language": "ru-RU,ru;q=0.9"})
    return session


def leads_missing_phones(path=None) -> list[str]:
    """Leads with at most one phone or without a mobile (those can hide a second number)."""
    kwargs = {"path": path} if path else {}
    result = []
    with db.connection(**kwargs) as conn:
        rows = conn.execute("SELECT id,phones,map_url FROM businesses WHERE map_url LIKE '%/maps/org/%' "
                            "ORDER BY created_at").fetchall()
    for row in rows:
        phones = _json_list(row["phones"])
        mobile = any(item["ok"] for item in build_telegram_phone_links(phones))
        if len(phones) <= 1 or not mobile:
            result.append(row["id"])
    return result


def _json_list(raw) -> list:
    import json
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def run_cards(job: dict) -> dict:
    params = job["params"]
    mode = params.get("mode")
    if mode == "phones":
        return _refill_phones(job)
    if mode == "add":
        return _add_lead(job)
    raise ValueError("Неизвестное задание карточек")


def _progress(job: dict, **values) -> None:
    db.update_job(job["id"], progress={"stopping": cancel.requested(), **values})


def _refill_phones(job: dict) -> dict:
    ids = [str(item) for item in job["params"].get("ids") or []]
    session = _session()
    done = added = leads_changed = errors = 0
    try:
        for index, identity in enumerate(ids):
            cancel.check()
            _progress(job, phase="phones", done=index, total=len(ids), added=added, errors=errors)
            existing = db.business(identity)
            if not existing:
                continue
            try:
                row = fetch_card(session, identity, check_site=False)
            except SearchStopped:
                raise
            except Exception as exc:   # one card that does not load is skipped
                errors += 1
                log.warning('{"event": "phones_refill_failed", "business_id": "%s", "error_type": "%s"}',
                            identity, type(exc).__name__)
            else:
                before = db.business_phones_raw(identity)   # parser numbers only, not manual ones
                merged = merge_phones(before, row.get("phones") or [])
                if len(merged) > len(before):
                    db.set_business_phones(identity, merged)
                    added += len(merged) - len(before)
                    leads_changed += 1
            done += 1
            if index + 1 < len(ids):
                cancel.sleep(random.uniform(*CARD_PAUSE) + 0.3)
    except SearchBlocked as exc:
        return {"blocked": True, "reason": str(exc), "done": done, "total": len(ids), "added": added,
                "leads": leads_changed, "errors": errors}
    finally:
        session.close()
    _progress(job, phase="phones", done=done, total=len(ids), added=added, errors=errors)
    reason = (f"Проверено карточек: {done} из {len(ids)}. Новых номеров: {added} у {leads_changed} лидов"
              + (f". Не загрузилось: {errors}" if errors else ""))
    return {"done": done, "total": len(ids), "added": added, "leads": leads_changed, "errors": errors,
            "reason": reason}


def _add_lead(job: dict) -> dict:
    params = job["params"]
    session = _session()
    try:
        _progress(job, phase="link")
        identity = resolve_link(session, params.get("url") or "")
        if not identity:
            raise ValueError("В ссылке нет организации Яндекс.Карт. Откройте карточку организации и скопируйте ссылку")
        if db.business(identity):
            return {"business_id": identity, "existed": True, "reason": "Этот лид уже есть"}
        cancel.check()
        _progress(job, phase="card")
        row = fetch_card(session, identity)
        city = str(params.get("city") or row.get("city") or "").strip() or "Без города"
        niche = str(params.get("niche") or row.get("niche") or "").strip() or "Без ниши"
        country = str(row.get("country") or "Россия").strip()
        review_limit = min(100, max(0, int(params.get("review_limit") or 0)))
        db.upsert_business(row, (niche, country, city), job["id"], store_details=True, review_limit=review_limit)
        if review_limit and int(row.get("reviews") or 0):
            cancel.check()
            _progress(job, phase="reviews", business_id=identity)
            cancel.sleep(random.uniform(0.4, 0.8))
            collect_reviews_http(session, row["review_url"], identity, review_limit)
    except SearchBlocked as exc:
        return {"blocked": True, "reason": str(exc)}
    finally:
        session.close()
    return {"business_id": identity, "existed": False, "reason": f"Добавлен лид «{row.get('name')}»"}
