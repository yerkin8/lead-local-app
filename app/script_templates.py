"""Script taxonomy: category → stage → subtype (docs/rewrite/TAXONOMY.md is the contract).

A slug is `category.stage` or `category.first.subtype`, e.g. `site.first.booking`,
`bot.objection.not_needed`, `general.meeting`. The built-in texts live in
`script_texts.py`, which `python -m app.import_scripts` regenerates.
Square-bracket fields require a manual edit before sending; `first` and `reminder`
scripts must have none so they go out in one tap.
"""

from __future__ import annotations

import hashlib
import json
import re

from .domain import normalized
from .script_texts import TEXTS

# Order here is the order on the «Скрипты» screen.
CATEGORIES = {
    "site": ("Сайт", "Несколько страниц: разделы, каталог, ответы на вопросы. Растёт, получает трафик из поиска, "
                     "подключает запись, бронь или оплату."),
    "landing": ("Лендинг", "Одна страница под одну задачу и одно действие. Делается за несколько дней, дешевле сайта; "
                           "хороший первый шаг, если сайта нет совсем."),
    "bot": ("Бот", "Бот в Telegram, VK или MAX: клиент делает нужное действие прямо в мессенджере, "
                   "не открывая сайт и не дожидаясь менеджера."),
    "automation": ("Автоматизация", "Связывает то, что у бизнеса уже есть, и убирает ручную работу: "
                                    "заявки не теряются, ответа не ждут полдня."),
    "ai_agent": ("ИИ-агенты", "ИИ, который сам отвечает и действует круглосуточно, когда вопросов много, "
                              "а отвечать некому или некогда."),
    "promotion": ("Продвижение на Яндекс Картах", "Карточка на картах — первое, что видит клиент. Задача — чтобы её "
                                                  "чаще находили и чаще из неё обращались."),
    "general": ("Общие", "Сообщения, которые не зависят от продукта: секретарь, созвон, оплата, сделка."),
}

SUBTYPES = {
    "landing": {"menu": "меню и прайс", "service": "одна услуга", "event": "мероприятие",
                "brand": "визитка", "leads": "сбор заявок"},
    "site": {"services": "услуги и запись", "booking": "бронь", "shop": "магазин", "orders": "заказы и доставка",
             "portfolio": "портфолио", "redesign": "переделка сайта"},
    "bot": {"booking": "запись", "orders": "заказы", "faq": "частые вопросы", "reminders": "напоминания",
            "loyalty": "лояльность"},
    "automation": {"leads": "заявки в одно место", "speed": "быстрый ответ", "payments": "оплата",
                   "calendar": "календарь", "reviews": "отзывы"},
    "ai_agent": {"admin": "администратор", "consultant": "консультант", "reviews": "ответы на отзывы",
                 "qualify": "разбор заявок"},
    "promotion": {"card": "карточка", "reviews": "отзывы и рейтинг", "ranking": "позиции", "content": "публикации"},
    "general": {},
}

STAGES = {
    "first": "Первое сообщение",
    "reply": "Ответ на интерес",
    "price": "Сколько стоит",
    "reminder": "Напоминание",
    "call": "Звонок",
    "objection.expensive": "Возражение: дорого",
    "objection.have_vendor": "Возражение: есть подрядчик",
    "objection.not_needed": "Возражение: не нужно",
    "objection.think": "Возражение: надо подумать",
    "objection.own": "Главное возражение",
}
GENERAL_STAGES = {
    "secretary_message": "Сообщение секретарю",
    "secretary_call": "Звонок секретарю",
    "meeting": "Созвон",
    "payment": "Оплата",
    "prepayment": "Предоплата",
    "deal": "Сделка",
    "upsell": "Допродажа",
    "not_now": "Закрыть тему",
}
# Stage of AI or user-copied scripts that were saved without one.
FREE_STAGE = ""

# Four offers on the organization card, in this order.
OFFER_CATEGORIES = ("site", "landing", "bot", "promotion")

# Niche → subtype per category. Matched against the niche tag, the main category and
# the Yandex rubrics. A word matches at the start of a word («бар» finds «Бар у дома», not
# «Барбершоп»); the first rule with a match wins, so the more specific rules go first.
# A missing subtype or a subtype without its own script falls back to `category.first`.
NICHE_SUBTYPES: tuple[tuple[tuple[str, ...], dict[str, str]], ...] = (
    (("салон", "парикмахер", "барбер", "маникюр", "ногт", "косметолог", "массаж", "спа", "spa", "бров",
      "ресниц", "тату", "эпиляц", "визаж", "бьюти", "beauty"),
     {"site": "services", "landing": "service", "bot": "booking", "promotion": "reviews"}),
    (("клиник", "стоматолог", "медицин", "ветеринар", "врач", "диагностик", "психолог"),
     {"site": "services", "landing": "service", "bot": "reminders", "promotion": "reviews"}),
    (("автосервис", "шиномонтаж", "автомойк", "детейлинг", "кузов", "автоэлектрик", "сто"),
     {"site": "services", "landing": "service", "bot": "booking", "promotion": "reviews"}),
    (("фотограф", "фотостуди", "дизайн", "видеосъ"),
     {"site": "portfolio", "landing": "service", "bot": "booking", "promotion": "content"}),
    (("фитнес", "спорт", "танц", "йог", "школ", "курс", "репетитор", "студия"),
     {"site": "services", "landing": "service", "bot": "booking", "promotion": "card"}),
    (("доставк", "пекарн", "кондитер", "торт", "кейтеринг", "пицц", "суши", "роллы"),
     {"site": "orders", "landing": "menu", "bot": "orders", "promotion": "card"}),
    (("ресторан", "кафе", "бар", "паб", "кофейн", "столов", "бургер", "чайхан", "бистро", "гастро"),
     {"site": "booking", "landing": "menu", "bot": "booking", "promotion": "card"}),
    (("банкет", "караоке", "клуб", "площадк", "event", "ивент", "лофт"),
     {"site": "booking", "landing": "event", "bot": "booking", "promotion": "content"}),
    (("гостиниц", "отел", "хостел", "баня", "бани", "саун", "база отдыха", "глэмпинг", "апартамент"),
     {"site": "booking", "landing": "brand", "bot": "booking", "promotion": "card"}),
    (("ремонт", "мебел", "окн", "двер", "потолк", "строит", "отделк", "кухни", "сантехник"),
     {"site": "portfolio", "landing": "leads", "bot": "faq", "promotion": "reviews"}),
    (("магазин", "цвет", "одежд", "обув", "стройматериал", "зоотовар", "подарк", "бутик"),
     {"site": "shop", "landing": "brand", "bot": "orders", "promotion": "card"}),
)
# Exceptions where a short word would match a longer, unrelated one.
_WHOLE_WORD = {"бар", "паб", "сто", "спа", "бани"}

# Old flat slugs (schema ≤ 5) → new slugs. The first old slug that maps to a new one
# is renamed in place (user edits survive); later ones merge into it. Campaigns and
# saved links keep working through resolve_slug().
LEGACY_SLUGS = {
    "quick_no_site": "site.first",
    "quick_weak_site": "site.first.redesign",
    "quick_followup": "site.reminder",
    "detailed": "site.reply",
    "packages": "site.price",
    "call": "site.call",
    "expensive": "site.objection.expensive",
    "existing_vendor": "site.objection.have_vendor",
    "not_needed": "site.objection.not_needed",
    "thinking": "site.objection.think",
    "secretary_message": "general.secretary_message",
    "secretary_call": "general.secretary_call",
    "meeting": "general.meeting",
    "payment": "general.payment",
    "prepayment": "general.prepayment",
    "deal": "general.deal",
    "upsell": "general.upsell",
    "nudge": "general.not_now",
    # Merged: the new structure has one slot for these.
    "quick_unavailable": "site.first.redesign",
    "first": "site.first",
    "first_weak_site": "site.first.redesign",
    "first_unavailable": "site.first.redesign",
    "short": "site.first",
    "followup": "site.reminder",
}


def is_primary_legacy(old: str) -> bool:
    """True for the old slug that owns its new place (the first one mapped there). Texts of the
    others never land in that place: e.g. the bracketed `first` must not become the one-tap `site.first`."""
    new = LEGACY_SLUGS.get(old)
    return new is not None and next(slug for slug, target in LEGACY_SLUGS.items() if target == new) == old


def parse_slug(slug: str) -> tuple[str, str, str] | None:
    """(category, stage, subtype) for a valid built-in slug, else None."""
    parts = str(slug or "").split(".")
    category = parts[0]
    if category not in CATEGORIES:
        return None
    if category == "general":
        return (category, parts[1], "") if len(parts) == 2 and parts[1] in GENERAL_STAGES else None
    if len(parts) >= 3 and parts[1] == "objection":
        stage = ".".join(parts[1:])
        return (category, stage, "") if len(parts) == 3 and stage in STAGES else None
    if len(parts) == 2 and parts[1] in STAGES:
        return category, parts[1], ""
    if len(parts) == 3 and parts[1] == "first" and parts[2] in SUBTYPES[category]:
        return category, "first", parts[2]
    return None


def sort_key(category: str, stage: str = "", subtype: str = "") -> int:
    """Stable position: category block of 1000, stage block of 20, subtype inside; free scripts at 900+."""
    categories = list(CATEGORIES)
    base = (categories.index(category) if category in categories else len(categories) - 1) * 1000
    stages = list(GENERAL_STAGES if category == "general" else STAGES)
    if stage not in stages:
        return base + 900
    subtypes = list(SUBTYPES.get(category, {}))
    return base + stages.index(stage) * 20 + (subtypes.index(subtype) + 1 if subtype in subtypes else 0)


def body_hash(body: str) -> str:
    return hashlib.sha1(str(body).encode("utf-8")).hexdigest()[:16]


def resolve_slug(slug: str) -> str:
    return LEGACY_SLUGS.get(slug, slug)


def default_title(category: str, stage: str, subtype: str = "") -> str:
    """Fallback title when a script file gives none."""
    name = CATEGORIES[category][0]
    if category == "general":
        return GENERAL_STAGES[stage]
    title = f"{STAGES[stage]} · {name.lower() if category != 'promotion' else 'продвижение'}"
    return f"{title} · {SUBTYPES[category][subtype]}" if subtype else title


def _signals(business: dict) -> str:
    details = business.get("details") or {}
    rubrics = details.get("categories") or []
    if isinstance(rubrics, str):
        try:
            rubrics = json.loads(rubrics)
        except ValueError:
            rubrics = [rubrics]
    parts = [business.get("niche"), business.get("category"), *rubrics,
             *(tag.get("niche") for tag in business.get("tags") or [] if isinstance(tag, dict))]
    return " " + normalized(" ".join(str(part) for part in parts if part)) + " "


def subtype_for(business: dict, category: str) -> str:
    """Subtype suggested by niche and rubrics; weak or broken sites always get `site.redesign`."""
    if category == "site" and business.get("site_type") in {"weak_site", "unavailable"}:
        return "redesign"
    text = _signals(business)
    for words, mapping in NICHE_SUBTYPES:
        if category in mapping and any(_has_word(text, word) for word in words):
            return mapping[category]
    return ""


def _has_word(text: str, word: str) -> bool:
    """`word` starts a word in `text`; short ambiguous words must be the whole word."""
    tail = r"(?![a-zа-я0-9])" if word in _WHOLE_WORD else ""
    return re.search(r"(?<![a-zа-я0-9])" + re.escape(word) + tail, text) is not None


def pick_slug(business: dict, category: str, stage: str, available) -> str | None:
    """The script to use for this business: `category.first.subtype` when that script exists,
    then the plain `category.stage`. Only `first` has subtypes."""
    available = set(available)
    if stage == "first":
        subtype = subtype_for(business, category)
        if subtype and f"{category}.first.{subtype}" in available:
            return f"{category}.first.{subtype}"
    slug = f"{category}.{stage}"
    return slug if slug in available else None


def offers(business: dict, rows) -> list[dict]:
    """Four first messages for the organization card (site, landing, bot, promotion), rendered.
    `rows` are template rows; only shipped scripts are offered. An empty `text` means the
    category has no first message yet (import SCRIPTS_NEW.md)."""
    from .domain import render_template
    builtin = {row["slug"]: row for row in rows if row.get("origin", "builtin") == "builtin"}
    result = []
    for category in OFFER_CATEGORIES:
        slug = pick_slug(business, category, "first", builtin)
        suggested = subtype_for(business, category)
        # The subtype actually used: empty when the text fell back to the plain `category.first`.
        subtype = (parse_slug(slug) or ("", "", ""))[2] if slug else ""
        result.append({"category": category, "label": CATEGORIES[category][0],
                       "definition": CATEGORIES[category][1], "suggested_subtype": suggested,
                       "subtype": subtype, "subtype_label": SUBTYPES[category].get(subtype, ""),
                       "slug": slug or "", "title": builtin[slug]["title"] if slug else "",
                       "text": render_template(builtin[slug]["body"], business) if slug else ""})
    return result


def _ordered(texts: dict[str, tuple[str, str]]) -> dict[str, tuple[str, str]]:
    def position(slug):
        meta = parse_slug(slug) or ("general", "", "")
        return sort_key(*meta), slug
    return {slug: texts[slug] for slug in sorted(texts, key=position)}


# slug → (title, body), in screen order.
TEMPLATES = _ordered(TEXTS)


# Desktop «Рассылка» (app/mailing, not changed by the merge) still asks for the first message by site type.
# Old flat slugs are gone after schema 6; this keeps the mailing preselect pointing at the new texts.
# `""` and `other` cover organisations without a site type and with a working site.
FIRST_BY_SITE_TYPE = {
    "": "site.first",
    "no_site": "site.first",
    "weak_site": "site.first.redesign",
    "unavailable": "site.first.redesign",
    "other": "site.first",
}
