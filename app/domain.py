from __future__ import annotations

import re
from urllib.parse import urlparse

STATUSES = {
    "not_started": "Не начато",
    "waiting": "Написал",
    "replied": "Ответили",
    "working": "В работе",
    "completed": "Договорились",
    "unsuitable": "Отказ",
}
SITE_TYPES = {"no_site", "weak_site", "unavailable"}
COUNTRIES = (
    "Россия", "Беларусь", "Казахстан", "Армения", "Азербайджан",
    "Кыргызстан", "Молдова", "Таджикистан", "Узбекистан",
)
SOCIAL_HOSTS = {
    "vk.com", "vk.ru", "t.me", "telegram.me", "instagram.com",
    "facebook.com", "ok.ru", "youtube.com", "rutube.ru", "wa.me",
    "whatsapp.com", "pinterest.com", "dzen.ru",
}
BUILDER_HOSTS = {
    "clients.site": "Яндекс Бизнес", "business.site": "Яндекс Бизнес",
    "tilda.ws": "Tilda", "wixsite.com": "Wix", "taplink.cc": "Taplink",
    "nethouse.ru": "Nethouse", "a5.ru": "A5", "ucoz.ru": "uCoz",
    "narod.ru": "Народ", "wordpress.com": "WordPress.com",
    "blogspot.com": "Blogger", "flexbe.ru": "Flexbe",
    "readymag.com": "Readymag", "notion.site": "Notion",
    "carrd.co": "Carrd", "webflow.io": "Webflow",
    "ukit.me": "uKit",
}
BUILDER_MARKERS = {
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
    "Яндекс Бизнес": ("clients.site",),
}
ORG_RE = re.compile(r"/maps/org/[^/]+/(\d+)(?:/|$)")


def host_of(url: str) -> str:
    parsed = urlparse(url if "://" in url else "https://" + url)
    return (parsed.hostname or "").lower().rstrip(".")


def host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def is_social(url: str) -> bool:
    host = host_of(url)
    return any(host_matches(host, domain) for domain in SOCIAL_HOSTS)


def classify_site(site: str | None, html: str | None = None, failed: bool = False) -> tuple[str, str | None]:
    if not site or is_social(site):
        return "no_site", None
    host = host_of(site)
    for domain, engine in BUILDER_HOSTS.items():
        if host_matches(host, domain):
            return "weak_site", engine
    if html:
        low = html.lower()
        for engine, markers in BUILDER_MARKERS.items():
            if any(marker in low for marker in markers):
                return "weak_site", engine
    if failed or html is not None and len(html.strip()) < 1000:
        return "unavailable", None
    return "other", None


def org_id(url: str) -> str | None:
    match = ORG_RE.search(urlparse(url).path)
    return match.group(1) if match else None


def normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text.casefold().replace("ё", "е")).strip()


def render_template(body: str, business: dict) -> str:
    values = {
        "название": business.get("name") or "ваша компания",
        "ниша": business.get("niche") or "ваша сфера",
        "город": business.get("city") or "ваш город",
        "тип_сайта": {
            "no_site": "нет сайта", "weak_site": "сайт на конструкторе",
            "unavailable": "сайт сейчас недоступен",
        }.get(business.get("site_type"), "сайт"),
    }
    return re.sub(r"\{(название|ниша|город|тип_сайта)\}", lambda m: str(values[m.group(1)]), body)
