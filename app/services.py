"""Transport-free service registry shared by the desktop bridge and the HTTP RPC.

Each service takes plain JSON-like arguments and returns data or raises.
Wrapping into ``{ok, data | error}`` is the job of the transport.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Callable

from . import ai_scripts, db, leads, media_service, outreach
from .desktop_services import (add_lead_by_link, card_job, refill_phones, refresh_business, search_results,
                               start_desktop_search, stop_job)
from .domain import COUNTRIES, SITE_TYPES, STATUSES
from .phone_links import build_telegram_phone_links


def stats():
    return db.stats()


def bootstrap():
    return {"countries": COUNTRIES, "statuses": STATUSES, "site_types": sorted(SITE_TYPES),
            "stats": db.stats()}


def search_start(payload):
    return start_desktop_search(dict(payload or {}))


def search_jobs():
    return db.list_jobs("search")


def search_stop(job_id):
    """«Остановить» for a queued or running search (or media) job; found cards stay."""
    return stop_job(str(job_id))


def parse_niches():
    return db.parse_niches()


def parse_cities(niche_key):
    return db.parse_cities(str(niche_key))


def parse_runs(niche_key, city_key):
    return db.parse_runs_for_city(str(niche_key), str(city_key))


def parse_run_businesses(run_id, filters=None):
    options = dict(filters or {})
    limit = min(200, max(1, int(options.get("limit", 50))))
    offset = max(0, int(options.get("offset", 0)))
    query = str(options.get("q", "")).strip()
    status = str(options.get("status", "")).strip() or None
    site_type = str(options.get("site_type", "")).strip() or None
    return db.parse_run_businesses(str(run_id), query=query, limit=limit, offset=offset,
                                   status=status, site_type=site_type)


def businesses(filters=None):
    options = dict(filters or {})
    limit = min(200, max(1, int(options.pop("limit", 50))))
    offset = max(0, int(options.pop("offset", 0)))
    return db.list_businesses(options, limit=limit, offset=offset)


def business_filter_options():
    return db.filter_options()


def business(identity):
    return db.business_full(str(identity))


def business_save(identity, status=None, *_legacy_note):
    """Save the work status. Notes are no longer edited: an extra argument from an old client is ignored,
    and the `businesses.note` column keeps what it already has."""
    identity = str(identity)
    return db.business(identity) if db.set_business(identity, status, None) else None


def templates():
    return db.templates()


def template_save(slug, body):
    text = str(body or "").strip()
    if not text:
        raise ValueError("Текст скрипта не может быть пустым")
    if len(text) > 20000:
        raise ValueError("Текст скрипта слишком длинный")
    if not db.save_template(str(slug), text):
        raise ValueError("Скрипт не найден")
    return True


def template_delete(slug):
    if not db.delete_template(str(slug)):
        raise ValueError("Удалить можно только ИИ-скрипт")
    return True


def ai_status():
    return ai_scripts.status()


def ai_chat_start(messages, category=None, business_id=None):
    return ai_scripts.start(messages, str(category) if category else None,
                            str(business_id) if business_id else None, ACTOR.get())


def ai_chat_result(task_id):
    return ai_scripts.result(str(task_id or ""), ACTOR.get())


def ai_key_save(key):
    """Save the user's own OpenRouter key (DPAPI, this Windows user). The key is never returned to the page."""
    from . import secret_store
    secret_store.save_key(str(key or ""))
    return ai_status()


def ai_key_clear():
    from . import secret_store
    secret_store.clear_key()
    return ai_status()


def ai_script_save(title, body, category=None):
    name = " ".join(str(title or "").split())
    text = str(body or "").strip()
    if not name:
        raise ValueError("Укажите название скрипта")
    if len(name) > 60:
        raise ValueError("Название длиннее 60 символов")
    if not text:
        raise ValueError("Текст скрипта не может быть пустым")
    if len(text) > 20000:
        raise ValueError("Текст скрипта слишком длинный")
    # Unknown or missing category falls back to «Общие» inside add_ai_template.
    return db.add_ai_template(name, text, category=str(category or "general"))


def business_scripts(identity):
    """All scripts rendered for this business plus the four offers for its card (TAXONOMY.md §5)."""
    from .domain import render_template
    from .script_templates import offers
    item = db.business_full(str(identity)) or db.business(str(identity))
    if not item:
        raise ValueError("Организация не найдена")
    rows = db.templates()
    items = [{**row, "text": render_template(row["body"], item)} for row in rows]
    cards = offers(item, rows)
    preferred = next((offer["slug"] for offer in cards if offer["slug"]), items[0]["slug"] if items else "")
    return {"preferred": preferred, "offers": cards, "items": items}


def script_taxonomy():
    """Category names, definitions and stage names for grouping the «Скрипты» screen."""
    from .script_templates import CATEGORIES, GENERAL_STAGES, STAGES, SUBTYPES
    return {"categories": [{"key": key, "title": title, "definition": definition, "subtypes": SUBTYPES[key]}
                           for key, (title, definition) in CATEGORIES.items()],
            "stages": STAGES, "general_stages": GENERAL_STAGES}


def frontend_error(message, source="", line=0, column=0):
    logging.getLogger("LeadEqualMoney.frontend").error(
        "JS: %s (%s:%s:%s)", str(message)[:2000], str(source)[:500], line, column)
    return True


def business_refresh(identity):
    return refresh_business(str(identity))


def lead_add(url, niche="", city=""):
    return add_lead_by_link(str(url or ""), str(niche or ""), str(city or ""))


def phones_refill(ids=None):
    return refill_phones(list(ids) if ids else None)


def cards_job(job_id):
    return card_job(str(job_id))


def cards_stop(job_id):
    return stop_job(str(job_id))


def lead_contact_add(business_id, kind, value, label=""):
    return db.add_manual_contact(str(business_id), str(kind), str(value or ""), str(label or ""))


def lead_contact_delete(contact_id):
    if not db.delete_manual_contact(int(contact_id)):
        raise ValueError("Контакт не найден")
    return {"deleted": True}


def debug_html_enabled():
    return db.desktop_diagnostics_enabled()


def set_debug_html_enabled(enabled):
    return db.set_desktop_diagnostics_enabled(bool(enabled))


THEMES = ("light", "dark", "system")


def ui_settings():
    """Manual preferences from «Настройки»: theme and the default reviews per organization for search."""
    theme = db.desktop_setting("ui_theme", "light")
    try:
        reviews = int(db.desktop_setting("search_review_limit", "20"))
    except ValueError:
        reviews = 20
    return {"theme": theme if theme in THEMES else "light", "review_limit": min(max(reviews, 0), 100)}


def ui_settings_save(values):
    values = dict(values or {})
    if "theme" in values:
        if values["theme"] not in THEMES:
            raise ValueError("Неизвестная тема оформления")
        db.set_desktop_setting("ui_theme", values["theme"])
    if "review_limit" in values:
        try:
            reviews = int(values["review_limit"])
        except (TypeError, ValueError) as exc:
            raise ValueError("Число отзывов — целое от 0 до 100") from exc
        if not 0 <= reviews <= 100:
            raise ValueError("Число отзывов — целое от 0 до 100")
        db.set_desktop_setting("search_review_limit", str(reviews))
    return ui_settings()


def media_start(kind, payload):
    return media_service.start(kind, **dict(payload or {}))


def media_discover_sections(payload):
    return media_service.discover_sections(**dict(payload or {}))


def media_resume(job_id):
    return media_service.resume_images(str(job_id))


def media_jobs(business_id=None):
    return media_service.list_jobs(business_id)


def media_delete(job_id):
    return media_service.delete(str(job_id))


def activity_snapshot(known=None):
    request = dict(known) if isinstance(known, dict) else {}
    topics = request.get("topics", ["search", "media"])
    request["topics"] = ([topic for topic in topics if topic in {"search", "media"}]
                         if isinstance(topics, (list, tuple, set)) else ["search", "media"])
    return db.activity_snapshot(request)


def telegram_phone_links(business_id):
    item = db.business(str(business_id))
    if not item:
        raise ValueError("Организация не найдена")
    return build_telegram_phone_links(item.get("phones", []))


# Who is sending: "tg:<telegram id>" in the Mini App (set by the RPC endpoint), "desktop" otherwise.
ACTOR: ContextVar[str] = ContextVar("actor", default="desktop")


def outreach_today():
    return outreach.today(ACTOR.get())


def outreach_add(business_id):
    return outreach.add(str(business_id))


def outreach_prepare(business_id, stage, contact, message):
    return outreach.prepare(ACTOR.get(), str(business_id), str(stage), str(contact), str(message or ""))


def outreach_mark(outreach_id, result):
    return outreach.mark(ACTOR.get(), int(outreach_id), str(result))


def outreach_settings():
    return outreach.settings()


def outreach_settings_save(values):
    return outreach.save_settings(dict(values or {}))


def outreach_contacted():
    return outreach.contacted()


def _actor_chat() -> int:
    actor = ACTOR.get()
    if not actor.startswith("tg:"):
        raise ValueError("Отправка файла в чат доступна только из Mini App")
    return int(actor[3:])


TELEGRAM_FILE_LIMIT = 49 * 1024 * 1024


def leads_list(filters=None):
    return leads.list_leads(dict(filters or {}))


def leads_filters():
    return leads.filters()


def lead_card(business_id):
    return leads.card(ACTOR.get(), str(business_id))


def lead_write(business_id, contact="", text=""):
    return leads.write(ACTOR.get(), str(business_id), str(contact or ""), str(text or ""))


def lead_mark(outreach_id, result):
    return leads.mark(ACTOR.get(), int(outreach_id), str(result))


def lead_correct(outreach_id, result):
    return leads.correct(ACTOR.get(), int(outreach_id), str(result))


def lead_status(business_id, status):
    return leads.set_status(str(business_id), str(status))


def leads_hide(ids):
    return leads.hide(ACTOR.get(), ids)


def leads_restore(ids):
    return leads.restore(ids)


def leads_bulk_status(ids, status):
    return leads.bulk_status(list(ids or []), str(status))


def leads_summary():
    return leads.summary(ACTOR.get())


def outreach_ack():
    return outreach.acknowledge(ACTOR.get())


# Desktop LeadEqualMoney carries only the shared mechanics. Server-only methods of the Mini App
# (admins, whoami, sending files through the bot, the home agent) are left out on purpose:
# they need app/admins.py, app/agent.py and the server bot, which the desktop build does not ship.
SERVER_ONLY = frozenset({"whoami", "admins_list", "admin_add", "admin_remove", "export_send", "backup_send",
                         "agent_settings", "agent_settings_save"})

SERVICES: dict[str, Callable] = {fn.__name__: fn for fn in (
    stats, bootstrap, search_start, search_jobs, search_stop, search_results, parse_niches, parse_cities,
    parse_runs, parse_run_businesses, businesses, business_filter_options, business,
    business_save, templates, template_save, template_delete, ai_status, ai_key_save, ai_key_clear, ai_chat_start, ai_chat_result, ai_script_save,
    business_scripts, script_taxonomy, frontend_error,
    business_refresh, debug_html_enabled, set_debug_html_enabled, ui_settings, ui_settings_save, media_start,
    media_discover_sections, media_resume, media_jobs, media_delete, activity_snapshot,
    telegram_phone_links, outreach_today, outreach_add, outreach_prepare, outreach_mark,
    outreach_settings, outreach_settings_save, outreach_ack, outreach_contacted,
    leads_list, leads_filters, lead_card, lead_write, lead_mark,
    lead_add, phones_refill, cards_job, cards_stop, lead_contact_add, lead_contact_delete,
    lead_correct, lead_status, leads_hide, leads_restore, leads_bulk_status, leads_summary,
)}

# Methods the web RPC may call. Excluded ones either need the PC (dialogs, files)
# or start Chrome inside the request; on a 2 GB VPS that must go through the worker (phase 3).
WEB_EXCLUDED = {"business_refresh", "media_discover_sections", "ai_key_save", "ai_key_clear",
                "lead_add", "phones_refill", "cards_job", "cards_stop"}
WEB_METHODS = frozenset(set(SERVICES) - WEB_EXCLUDED)

# Bridge methods with no web counterpart yet; tracked in docs/PARITY.md.
DESKTOP_ONLY = frozenset({"complete_setup", "open_logs", "media_open", "media_save", "import_xlsx",
                          "export_xlsx", "export_scope", "backup", "import_sqlite", "open_external",
                          "replace_database", "open_data_dir"})


def call(name: str, args: list | tuple = (), *, allowed: frozenset | set | None = None):
    if allowed is not None and name not in allowed:
        raise KeyError(name)
    func = SERVICES[name]
    return func(*args)
