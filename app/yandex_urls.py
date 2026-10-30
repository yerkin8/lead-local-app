from __future__ import annotations

from urllib.parse import urlparse, urlunparse


YANDEX_MAP_HOSTS = {"yandex.ru", "www.yandex.ru", "yandex.com", "www.yandex.com", "maps.yandex.com"}


def normalize_yandex_url(value: str) -> str:
    """Return a Yandex Maps URL on yandex.ru while leaving CDN hosts untouched."""
    raw = str(value or "").strip()
    if not raw:
        return raw
    parsed = urlparse(raw)
    host = (parsed.hostname or "").casefold().rstrip(".")
    if host not in YANDEX_MAP_HOSTS:
        return raw
    path = parsed.path or "/"
    if host == "maps.yandex.com" and not path.startswith("/maps/"):
        path = "/maps" + (path if path.startswith("/") else "/" + path)
    return urlunparse((parsed.scheme or "https", "yandex.ru", path, parsed.params, parsed.query, parsed.fragment))
