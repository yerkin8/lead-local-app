# LeadEqualMoney Desktop

Local Windows app (Python + pywebview/WebView2, vanilla JS UI in `app/desktop_ui`, PyInstaller build via
`build.ps1`). It finds small businesses on Yandex Maps without a website (or with a weak one) and helps
write to them in Telegram. Everything stays on the owner's PC.

Полное описание на русском — [README_RU.md](README_RU.md): разделы, поиск и кнопка «Остановить», работа
с лидами, рассылка пачками (результат по каждому бизнесу, «Повторить пачку» с напоминанием, «Убрать из
пачки», связь со статусами «Лидов»), где лежат данные и резервные копии, сборка и обновление скриптов.

- Sections: Лиды · Итоги · Сегодня · Поиск · Скрипты · ИИ-чат · Рассылка · Медиа · Настройки.
- Data: `%APPDATA%\LeadEqualMoney` (`app.sqlite3`, `mailing\`, `backups\`). Keys and Telegram sessions are
  encrypted with Windows DPAPI for the current user.
- Check: `powershell -NoProfile -ExecutionPolicy Bypass -File scripts\verify.ps1` (must be PASS).
- Build: `powershell -NoProfile -ExecutionPolicy Bypass -File build.ps1` → `dist\LeadEqualMoney\`.
- Docs: `docs/unify/` (Mini App merge, redesign), `docs/mailing/` (mailing module), `docs/next/` (search speed,
  stop, batches and repeats). The server Mini App lives in a separate repository.
