"""Create manual Telegram phone links without contacting Telegram."""

from __future__ import annotations

import re

import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat, PhoneNumberType


def _dedupe_key(value: str, parsed: phonenumbers.PhoneNumber | None, region: str) -> str:
    if parsed is not None:
        try:
            return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
        except Exception:
            pass

    digits = re.sub(r"\D", "", value)
    if region == "RU":
        if len(digits) == 11 and digits[0] in "78":
            digits = "7" + digits[1:]
        elif len(digits) == 10:
            digits = "7" + digits
    return f"{region}:{digits}" if digits else f"{region}:{value.casefold().strip()}"


def _invalid_reason(phone: phonenumbers.PhoneNumber) -> str:
    if phone.extension:
        return "Добавочный номер нельзя включить в ссылку Telegram."

    possible = phonenumbers.is_possible_number_with_reason(phone)
    if possible == phonenumbers.ValidationResult.TOO_SHORT:
        return "Номер слишком короткий."
    if possible == phonenumbers.ValidationResult.TOO_LONG:
        return "Номер слишком длинный."
    if possible != phonenumbers.ValidationResult.IS_POSSIBLE:
        return "Номер не соответствует возможной длине для этого региона."
    if not phonenumbers.is_valid_number(phone):
        return "Номер не соответствует актуальному плану нумерации."

    number_type = phonenumbers.number_type(phone)
    if number_type == PhoneNumberType.FIXED_LINE:
        return "Городской или стационарный номер: мобильная ссылка не сформирована."
    if number_type != PhoneNumberType.MOBILE:
        return "Тип номера не подтверждён как мобильный."
    return "Не удалось сформировать ссылку для этого номера."


def build_telegram_phone_links(phones: object, region: str = "RU") -> list[dict[str, str | bool]]:
    """Normalize phones and make official t.me/+<E.164 digits> links locally.

    The input is never changed and this function has no Telegram network client.
    Duplicate normalized numbers are emitted once. Landlines and invalid values
    remain visible with a reason but do not receive a link.
    """
    if not isinstance(phones, (list, tuple)):
        return []

    result: list[dict[str, str | bool]] = []
    seen: set[str] = set()
    for value in phones:
        original = str(value or "").strip()
        if not original:
            continue

        parsed = None
        try:
            parsed = phonenumbers.parse(original, region)
        except NumberParseException:
            key = _dedupe_key(original, None, region)
            if key in seen:
                continue
            seen.add(key)
            result.append({
                "number": original,
                "e164": "",
                "url": "",
                "ok": False,
                "reason": "Не удалось разобрать номер телефона.",
            })
            continue

        key = _dedupe_key(original, parsed, region)
        if key in seen:
            continue
        seen.add(key)

        e164 = ""
        if phonenumbers.is_possible_number(parsed):
            e164 = phonenumbers.format_number(parsed, PhoneNumberFormat.E164).lstrip("+")

        reason = _invalid_reason(parsed)
        ok = False
        if phonenumbers.is_valid_number(parsed) and not parsed.extension \
                and phonenumbers.number_type(parsed) == PhoneNumberType.MOBILE:
            ok = True
            reason = ""

        result.append({
            "number": original,
            "e164": e164,
            "url": f"https://t.me/+{e164}" if ok else "",
            "ok": ok,
            "reason": reason,
        })

    return result


def merge_phones(*groups: object, region: str = "RU") -> list[str]:
    """All numbers of a card in order of appearance, each once (compared as E.164).

    Groups go in priority order: what the card shows, then the page state JSON, then numbers taken from
    WhatsApp/Viber links. Bare digits (``79991234567``) are shown in the international format."""
    result: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group if isinstance(group, (list, tuple)) else []:
            text = str(value or "").strip()
            if not text:
                continue
            candidate = text
            if re.fullmatch(r"\d{10,15}", text):
                candidate = "+" + text if not (len(text) == 11 and text[0] == "8") else text
            try:
                parsed = phonenumbers.parse(candidate, region)
            except NumberParseException:
                parsed = None
            key = _dedupe_key(candidate, parsed, region)
            if key in seen:
                continue
            seen.add(key)
            if parsed is not None and candidate is not text:
                text = phonenumbers.format_number(parsed, PhoneNumberFormat.INTERNATIONAL)
            result.append(text)
    return result


MESSENGER_PHONE = re.compile(
    r"(?:wa\.me/|api\.whatsapp\.com/send/?\?phone=|whatsapp://send\?phone=|viber://(?:chat|add)\?number=)"
    r"(?:%2B|\+)?(\d{10,15})", re.I)


def messenger_phones(urls: object) -> list[str]:
    """Digits of numbers in WhatsApp and Viber links (``wa.me/79991234567?text=…``)."""
    found = []
    for url in urls if isinstance(urls, (list, tuple, set)) else []:
        for match in MESSENGER_PHONE.finditer(str(url or "")):
            found.append(match.group(1))
    return found
