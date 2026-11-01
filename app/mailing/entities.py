"""Links in a mailing text become clickable Telegram entities.

- A bare ``https://…`` (or ``t.me/…``, ``www.…``) gets a ``url`` entity: clickable even inside brackets.
- ``[слова](https://…)`` becomes a hyperlink on the words; only the words stay in the text.

Offsets and lengths are in UTF-16 code units, as Telegram counts them (emoji take two)."""

from __future__ import annotations

import re
from dataclasses import dataclass

MARKDOWN_LINK = re.compile(r"\[([^\[\]\n]{1,200})\]\((https?://[^\s()]+)\)")
BARE_URL = re.compile(r"(?<![\w@/])((?:https?://|www\.|t\.me/)[^\s<>()\[\]«»\"']+)", re.I)
TRAILING = ".,;:!?…"


@dataclass(frozen=True)
class Entity:
    kind: str      # "url" | "text_url"
    offset: int    # UTF-16 units
    length: int    # UTF-16 units
    url: str = ""


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def has_markdown_link(text: str) -> bool:
    return bool(MARKDOWN_LINK.search(text or ""))


def strip_markdown_links(text: str) -> str:
    """The text a placeholder check should see: ``[слова](url)`` is a link, not an unfilled field."""
    return MARKDOWN_LINK.sub(lambda m: m.group(1), text or "")


def parse(text: str) -> tuple[str, list[Entity]]:
    """Plain text to send and the entities for it."""
    text = text or ""
    plain, entities, last = [], [], 0
    for match in MARKDOWN_LINK.finditer(text):
        plain.append(text[last:match.start()])
        offset = utf16_len("".join(plain))
        words = match.group(1)
        plain.append(words)
        entities.append(Entity("text_url", offset, utf16_len(words), match.group(2)))
        last = match.end()
    plain.append(text[last:])
    result = "".join(plain)
    taken = [(e.offset, e.offset + e.length) for e in entities]
    for match in BARE_URL.finditer(result):
        url = match.group(1).rstrip(TRAILING)
        start = utf16_len(result[:match.start(1)])
        length = utf16_len(url)
        if any(a < start + length and start < b for a, b in taken):
            continue
        entities.append(Entity("url", start, length))
    entities.sort(key=lambda e: e.offset)
    return result, entities
