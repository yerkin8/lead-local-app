"""Export all built-in scripts to one markdown file for rewriting, and import it back strictly.

    python scripts/import_rewritten_scripts.py --export docs/scripts/scripts-for-rewrite.md
    python scripts/import_rewritten_scripts.py docs/scripts/scripts-for-rewrite.md            # dry run
    python scripts/import_rewritten_scripts.py docs/scripts/scripts-for-rewrite.md --apply    # write app/script_texts.py

The file format is the one `app.import_scripts` reads («### `slug` — Название» + ```text block).
Unlike `app.import_scripts`, this import is strict: every current slug must be present, no
unknown slugs, placeholders ({...} and [...]) must stay exactly as in the current text.
Writing goes through `app.import_scripts.apply`, so the local database keeps texts the user
edited in the app (default_hash) and updates only untouched defaults.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import import_scripts  # noqa: E402
from app import script_templates as taxonomy  # noqa: E402
from app.outreach import OPT_OUT_FOOTER, chat_url  # noqa: E402

CURLY = re.compile(r"\{[^{}\n]+\}")
SQUARE = re.compile(r"\[[^\]\n]+\]")
RAW_LIMIT = 1800          # text + signature, characters (task limit)
URL_LIMIT = 2048          # open_external limit for the «Написать» link (KNOWN_ISSUES K4)
SAMPLE_BASE = "https://t.me/+70000000000"
SAMPLE_NAME = "Кофейня у дома"


def placeholders(body: str) -> Counter:
    return Counter(CURLY.findall(body)) + Counter(SQUARE.findall(body))


def with_signature(body: str) -> str:
    return f"{body.strip()}\n\n{OPT_OUT_FOOTER}"


def link_length(body: str) -> int:
    return len(chat_url(SAMPLE_BASE, with_signature(body.replace("{название}", SAMPLE_NAME))))


def _note(category: str, stage: str, subtype: str) -> str:
    if stage == "first":
        what = "ПЕРВОЕ сообщение (главная цель переписывания)"
        if subtype:
            what += f", вариант для ниш: {taxonomy.SUBTYPES.get(category, {}).get(subtype, subtype)}"
        else:
            what += ", общий вариант, если ниша не определена"
        return what
    label = taxonomy.STAGES.get(stage) or taxonomy.GENERAL_STAGES.get(stage) or stage
    return f"{label} — продолжение серии (менять только приветствие и тон)"


def export(texts: dict[str, tuple[str, str]]) -> str:
    out = ["# Скрипты LeadEqualMoney — файл для переписывания", "",
           "Формат обязателен: строка «### `slug` — Название», под ней блок ```text с текстом.",
           "Не меняйте slug, не удаляйте скрипты, сохраняйте {название} и [поля в скобках] как есть.",
           "Строки вне блоков ```text (заметки) при импорте игнорируются.",
           f"Всего скриптов: {len(texts)}, из них первых сообщений: "
           f"{sum(1 for s in texts if taxonomy.parse_slug(s)[1] == 'first')}.", ""]
    current_category = current_stage = None
    for slug, (title, body) in texts.items():
        category, stage, subtype = taxonomy.parse_slug(slug)
        if category != current_category:
            name, about = taxonomy.CATEGORIES[category]
            out += [f"## {category} — {name}", "", f"_{about}_", ""]
            current_category, current_stage = category, None
        if stage != current_stage and category != "general":
            out += [f"#### Этап: {'ПЕРВОЕ СООБЩЕНИЕ' if stage == 'first' else taxonomy.STAGES.get(stage, stage)}", ""]
            current_stage = stage
        out += [f"### `{slug}` — {title}", f"Назначение: {_note(category, stage, subtype)}.",
                "```text", body, "```", ""]
    return "\n".join(out)


def validate(parsed: import_scripts.Parsed, current: dict[str, tuple[str, str]]) -> tuple[list[str], list[str]]:
    errors = list(parsed.errors)
    warnings = list(parsed.warnings)
    errors += [f"неизвестный slug `{slug}`" for slug in parsed.unknown]
    errors += [f"нет скрипта `{slug}`" for slug in current if slug not in parsed.scripts]
    errors += [f"неизвестный slug `{slug}`" for slug in parsed.scripts if slug not in current]
    for slug, (_, body) in parsed.scripts.items():
        if slug not in current:
            continue
        before, after = placeholders(current[slug][1]), placeholders(body)
        if before != after:
            errors.append(f"`{slug}`: подстановки изменились: было {sorted(before.elements())}, "
                          f"стало {sorted(after.elements())}")
        if "{" in CURLY.sub("", body) or "}" in CURLY.sub("", body):
            errors.append(f"`{slug}`: непарная фигурная скобка")
        total = len(with_signature(body))
        if total > RAW_LIMIT:
            warnings.append(f"`{slug}`: с подписью {total} символов > {RAW_LIMIT}")
        url = link_length(body)
        if url > URL_LIMIT:
            warnings.append(f"`{slug}`: ссылка «Написать» {url} > {URL_LIMIT} (K4) — текст откроется "
                            "только через «Копировать»")
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("file", type=Path)
    parser.add_argument("--export", action="store_true", help="выгрузить текущие скрипты в файл")
    parser.add_argument("--apply", action="store_true", help="записать app/script_texts.py и обновить базу")
    parser.add_argument("--db", type=Path, help="путь к базе (по умолчанию база приложения)")
    parser.add_argument("--texts-file", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    current = dict(taxonomy.TEMPLATES)
    if args.export:
        args.file.parent.mkdir(parents=True, exist_ok=True)
        args.file.write_text(export(current), encoding="utf-8", newline="\n")
        print(f"Выгружено скриптов: {len(current)} → {args.file}")
        return 0
    parsed = import_scripts.parse(args.file.read_text(encoding="utf-8-sig"))
    errors, warnings = validate(parsed, current)
    result = import_scripts.plan(parsed, current)
    print(f"Скриптов в файле: {len(parsed.scripts)}; изменено: {len(result.changed)}; "
          f"без изменений: {len(result.unchanged)}")
    for slug in result.changed:
        print(f"  · изменён {slug}")
    for line in warnings:
        print(f"Предупреждение: {line}")
    for line in errors:
        print(f"Ошибка: {line}")
    if errors:
        print("Импорт не применён: исправьте ошибки.")
        return 1
    if not args.apply:
        print("Пробный прогон: ничего не записано. Добавьте --apply.")
        return 0
    import_scripts.apply(result, db_path=args.db, texts_file=args.texts_file)
    edited = import_scripts.edited_in_db(result.changed, args.db)
    print("Записано в app/script_texts.py. Тексты, правленые в приложении, в базе не тронуты: "
          + (", ".join(edited) or "таких нет"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
