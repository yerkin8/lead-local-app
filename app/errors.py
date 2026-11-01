"""Shared error markers."""


class YandexBlocked(Exception):
    """Yandex answered with a captcha or "limited": retry later, not a bug."""
