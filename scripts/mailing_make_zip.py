"""Pack dist\\LeadEqualMoney into a portable zip for other people (no keys, no sessions, no secrets).

Usage: python scripts/mailing_make_zip.py [OUT_DIR]
Adds «Создать ярлык.cmd» (double click) and «ПРОЧТИ.txt». Fails if the folder holds anything secret.
"""
from __future__ import annotations

import sys
import zipfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist" / "LeadEqualMoney"
FORBIDDEN_PARTS = {"secrets", ".env", "mailing.sqlite3", "keys.bin", "test_recipients.bin"}
README = """LeadEqualMoney — портативная версия

1. Распакуйте папку куда угодно и запустите LeadEqualMoney.exe.
   Ярлыки на рабочем столе и в «Пуске»: двойной щелчок по «Создать ярлык.cmd».
2. Нужен Microsoft Edge WebView2 Runtime (в Windows 10/11 обычно уже есть).
3. Данные хранятся в %APPDATA%\\LeadEqualMoney, а не в этой папке.

Рассылка в Telegram
- Откройте раздел «Рассылка». Мастер первого запуска попросит ВАШИ api_id и api_hash
  (my.telegram.org → API development tools). В программе чужих ключей нет: Telegram
  запрещает передавать их другим людям.
- Затем добавьте свой аккаунт и войдите: телефон → код из Telegram → пароль 2FA, если есть.
  Ключи и сессия шифруются вашей учётной записью Windows и не покидают компьютер.
- Каждая кампания сначала проходит «сухой запуск» без отправки. Реальная отправка
  включается отдельно, вводом слова ОТПРАВИТЬ. Кнопка «Аварийный стоп» есть на каждом экране.
- Новые аккаунты Telegram ограничивает строже: начинайте с малых объёмов.
"""
SHORTCUT_CMD = '@echo off\r\npowershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0create_shortcut.ps1" -PortableDirectory "%~dp0."\r\npause\r\n'


def main(out_dir: Path) -> Path:
    exe = DIST / "LeadEqualMoney.exe"
    if not exe.is_file():
        raise SystemExit("Сначала соберите приложение: build.ps1")
    files = [p for p in DIST.rglob("*") if p.is_file()]
    bad = [p for p in files if FORBIDDEN_PARTS & {part.casefold() for part in p.parts}
           or p.suffix.casefold() in {".session", ".env"}]
    if bad:
        raise SystemExit(f"В сборке лишние файлы: {bad[:5]}")
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"LeadEqualMoney-portable-{datetime.now():%Y%m%d}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            archive.write(path, Path("LeadEqualMoney") / path.relative_to(DIST))
        archive.write(ROOT / "create_shortcut.ps1", "LeadEqualMoney/create_shortcut.ps1")
        archive.writestr("LeadEqualMoney/Создать ярлык.cmd", SHORTCUT_CMD.encode("cp866"))
        archive.writestr("LeadEqualMoney/ПРОЧТИ.txt", README.encode("utf-8-sig"))
    print(target)
    return target


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist")
